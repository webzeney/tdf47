<?php
// TDF47 国内からの再確認窓口（/_recheck/index.php として配信）。
// 毎日のリンク確認（GitHub＝海外）で「時間切れ・接続拒否」になったリンクを、国内のこのサーバーから確認し直す。
// 海外からの接続を断っている自治体サイトを、リンク切れと誤判定しないため（2026-10-28）。
//
// 使い方: POST（本文 {"url": "https://..."}）、ヘッダー X-TDF47-Token に合言葉。1回1件。
// 応答: {"status": 200|null, "final_url": "...", "title": "...", "error": null|"...", "robots_blocked": false}
// 判定は確認プログラム（src/probe.py の _fetch と urllib.robotparser、Python 3.12）に合わせている。
// 合言葉そのものはサーバーに置かない。配信時に GitHub の秘密情報から作ったハッシュを config.php に書き込む。
// 踏み台にされないよう、接続先は毎回（リダイレクト先も）名前を引いて外部のアドレスか確かめ、そのアドレスに固定して接続する。

declare(strict_types=1);

ini_set('display_errors', '0');
error_reporting(0);

const UA = 'TDF47-bot/1.0 (+https://tdf47.com/about/; link checker; contact: https://tdf47.com/contact/)';
const TIMEOUT = 15;
const MAX_BYTES = 200000;
const MAX_REDIRECTS = 10;
const DELAY_US = 1200000;
const DEADLINE_SEC = 70;  // 依頼側（probe.py）は90秒で待つのをやめる。その前に必ず応答する
const TOO_MANY_REDIRECTS = '国内から: HTTPError（リダイレクトが多すぎる）';
const REFUSED = '窓口で確認を断った: ';  // この文言で始まるエラーは「確認できなかった」として 422 で返す（判定に使わない）

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

$START = microtime(true);

function reply(int $code, array $data): void
{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}

function remaining(): int
{
    global $START;
    return (int)floor(DEADLINE_SEC - (microtime(true) - $START));
}

function allow_local(): bool
{
    // テスト専用（手元で localhost のテスト用サーバーに接続するため）。本番のサーバーでは設定しない。
    return getenv('TDF47_RECHECK_ALLOW_LOCAL') === '1';
}

// ---- 接続先の確認（踏み台対策） ----

// 外部に公開されているアドレスか（内部・予約・共有用のアドレスは不可）。
function ip_is_public(string $ip): bool
{
    $bin = @inet_pton($ip);
    if ($bin === false) {
        return false;
    }
    if (strlen($bin) === 16 && substr($bin, 0, 12) === str_repeat("\0", 10) . "\xff\xff") {
        $bin = substr($bin, 12);  // IPv4 射影アドレス（::ffff:a.b.c.d）は IPv4 として調べる
    }
    if (strlen($bin) === 16 && (ord($bin[0]) & 0xe0) !== 0x20) {
        return false;  // IPv6 はインターネットで使われる範囲（2000::/3）だけ
    }
    $deny = strlen($bin) === 4
        ? ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12',
           '192.0.0.0/24', '192.0.2.0/24', '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15',
           '198.51.100.0/24', '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4']
        : ['::/127', '::ffff:0:0/96', '64:ff9b::/96', '64:ff9b:1::/48', '100::/64', '2001::/23',
           '2001:db8::/32', '2002::/16', 'fc00::/7', 'fe80::/10', 'fec0::/10', 'ff00::/8'];
    foreach ($deny as $cidr) {
        [$net, $bits] = explode('/', $cidr);
        $nb = inet_pton($net);
        if (strlen($nb) !== strlen($bin)) {
            continue;
        }
        $bits = (int)$bits;
        $bytes = intdiv($bits, 8);
        $rest = $bits % 8;
        if (substr($bin, 0, $bytes) !== substr($nb, 0, $bytes)) {
            continue;
        }
        if ($rest === 0) {
            return false;
        }
        $mask = (0xff << (8 - $rest)) & 0xff;
        if ((ord($bin[$bytes]) & $mask) === (ord($nb[$bytes]) & $mask)) {
            return false;
        }
    }
    return true;
}

// ホスト名の形（英数字とハイフンの区切り、最後はアルファベット）。数字だけ・16進・%エンコードのアドレス表記を受け付けない。
function host_name_ok(string $host): bool
{
    if (allow_local() && $host === 'localhost') {
        return true;
    }
    if (strlen($host) > 253 || preg_match('/(^|\.)localhost\.?$/', $host)) {
        return false;
    }
    // 末尾のドット（example.jp.）も受け付けない（接続先の固定が効かなくなるため）
    return (bool)preg_match('/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$/', $host);
}

// 名前を引き、すべて外部のアドレスなら接続に使うアドレスを返す。[エラー文, アドレス]
function resolve_public(string $host): array
{
    if (allow_local() && $host === 'localhost') {
        return [null, '127.0.0.1'];
    }
    $v4 = gethostbynamel($host) ?: [];
    $v6 = [];
    $recs = @dns_get_record($host, DNS_AAAA);
    if (is_array($recs)) {
        foreach ($recs as $r) {
            if (($r['type'] ?? '') === 'AAAA' && isset($r['ipv6'])) {
                $v6[] = $r['ipv6'];
            }
        }
    }
    if (!$v4 && !$v6) {
        return ['国内から: Name or service not known（名前を引けない）', null];
    }
    foreach (array_merge($v4, $v6) as $ip) {
        if (!ip_is_public($ip)) {
            return ['内部アドレスには接続しない', null];
        }
    }
    return [null, $v4 ? $v4[0] : $v6[0]];
}

// 確認してよいURLか。[エラー文, 接続先アドレス]。エラー文が「国内から:」で始まるものは確認の結果として返す。
function check_url(string $url): array
{
    $p = parse_url($url);
    if ($p === false || !isset($p['scheme'], $p['host'])) {
        return ['URLの形式が不正', null];
    }
    $scheme = strtolower($p['scheme']);
    if ($scheme !== 'http' && $scheme !== 'https') {
        return ['http/https 以外は確認しない', null];
    }
    if (isset($p['user']) || isset($p['pass'])) {
        return ['URLの形式が不正', null];
    }
    if (isset($p['port']) && !in_array((int)$p['port'], [80, 443], true) && !allow_local()) {
        return ['通常以外のポートは確認しない', null];
    }
    $host = strtolower($p['host']);
    if (!host_name_ok($host)) {
        return ['ホスト名の形が不正（アドレスの直指定は不可）', null];
    }
    return resolve_public($host);
}

function pin_for(string $url, string $ip): array
{
    $p = parse_url($url);
    $port = isset($p['port']) ? (int)$p['port'] : (strtolower($p['scheme']) === 'https' ? 443 : 80);
    $addr = strpos($ip, ':') !== false ? '[' . $ip . ']' : $ip;
    return [strtolower($p['host']) . ':' . $port . ':' . $addr];
}

// ---- 取得 ----

// curl のエラーを、確認プログラム（probe.py）と同じ判定ができる文言にそろえる。
function curl_error_text(int $no, string $msg): string
{
    switch ($no) {
        case CURLE_OPERATION_TIMEDOUT:
            return "国内から: timed out ($msg)";
        case CURLE_COULDNT_CONNECT:
            return "国内から: Connection refused or unreachable ($msg)";
        case CURLE_COULDNT_RESOLVE_HOST:
            return "国内から: Name or service not known ($msg)";
        case CURLE_RECV_ERROR:
        case CURLE_SEND_ERROR:
            return "国内から: Connection reset ($msg)";
        case CURLE_GOT_NOTHING:
            return "国内から: RemoteDisconnected ($msg)";
        case CURLE_URL_MALFORMAT:
            return REFUSED . "URLの形式が不正 ($msg)";
        default:
            if (stripos($msg, 'SSL') !== false || stripos($msg, 'certificate') !== false) {
                return "国内から: SSL ($msg)";
            }
            return "国内から: curl $no ($msg)";
    }
}

// 1回分の取得（リダイレクトはたどらない。接続先はアドレスに固定）。[status, headers, body, error]
function fetch_once(string $url, string $ip, int $max_bytes): array
{
    $t = min(TIMEOUT, remaining());
    if ($t < 2) {
        return [null, [], '', '国内から: timed out（再確認の持ち時間切れ）'];
    }
    $body = '';
    $truncated = false;
    $headers = [];
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
        CURLOPT_CONNECTTIMEOUT => $t,
        CURLOPT_TIMEOUT => $t,
        CURLOPT_USERAGENT => UA,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_ENCODING => '',
        CURLOPT_RESOLVE => pin_for($url, $ip),
        CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$headers) {
            if (preg_match('#^HTTP/#i', $line)) {
                $headers = [];
            }
            $kv = explode(':', $line, 2);
            if (count($kv) === 2) {
                $headers[strtolower(trim($kv[0]))] = trim($kv[1]);
            }
            return strlen($line);
        },
        CURLOPT_WRITEFUNCTION => function ($ch, $chunk) use (&$body, &$truncated, $max_bytes) {
            $body .= $chunk;
            if (strlen($body) >= $max_bytes) {
                $truncated = true;
                return -1;  // 先頭だけ読めば足りる（title は頭にある）
            }
            return strlen($chunk);
        },
    ]);
    $ok = curl_exec($ch);
    $no = curl_errno($ch);
    $msg = curl_error($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    $primary = (string)curl_getinfo($ch, CURLINFO_PRIMARY_IP);
    curl_close($ch);
    if ($ok === false && !($truncated && $no === CURLE_WRITE_ERROR)) {
        return [null, $headers, '', curl_error_text($no, $msg)];
    }
    if ($primary !== '' && @inet_pton($primary) !== @inet_pton($ip)) {
        return [null, [], '', REFUSED . '接続先が確認したアドレスと違う'];  // 念のための二重確認
    }
    return [$status ?: null, $headers, substr($body, 0, $max_bytes), null];
}

// 相対URLの解決（RFC 3986 の 5.2）。
function resolve_url(string $base, string $ref): string
{
    if (preg_match('#^[a-z][a-z0-9+.\-]*:#i', $ref)) {
        return $ref;
    }
    $b = parse_url($base);
    $origin = $b['scheme'] . '://' . $b['host'] . (isset($b['port']) ? ':' . $b['port'] : '');
    $bpath = $b['path'] ?? '';
    $bquery = isset($b['query']) ? '?' . $b['query'] : '';
    $ref = preg_replace('/#.*$/s', '', $ref);
    if (strpos($ref, '//') === 0) {
        return $b['scheme'] . ':' . $ref;
    }
    if ($ref === '') {
        return $origin . ($bpath === '' ? '/' : $bpath) . $bquery;
    }
    if ($ref[0] === '?') {
        return $origin . ($bpath === '' ? '/' : $bpath) . $ref;
    }
    $q = '';
    if (($i = strpos($ref, '?')) !== false) {
        $q = substr($ref, $i);
        $ref = substr($ref, 0, $i);
    }
    if ($ref[0] === '/') {
        $path = $ref;
    } else {
        $dir = $bpath === '' ? '/' : preg_replace('#[^/]*$#', '', $bpath);
        $path = $dir . $ref;
    }
    $out = [];
    $segs = explode('/', $path);
    $n = count($segs);
    foreach ($segs as $k => $s) {
        if ($s === '.') {
            if ($k === $n - 1) {
                $out[] = '';
            }
            continue;
        }
        if ($s === '..') {
            if (count($out) > 1) {
                array_pop($out);
            }
            if ($k === $n - 1) {
                $out[] = '';
            }
            continue;
        }
        $out[] = $s;
    }
    $path = implode('/', $out);
    if ($path === '' || $path[0] !== '/') {
        $path = '/' . ltrim($path, '/');
    }
    return $origin . $path . $q;
}

// リダイレクトを（毎回接続先を確かめながら）たどって取得する。Python の urllib と同じく 301/302/303/307/308 だけたどる。
// [status, final_url, headers, body, error, ip]
function fetch_follow(string $url, string $ip, int $max_bytes): array
{
    $cur = $url;
    for ($i = 0; $i <= MAX_REDIRECTS; $i++) {
        [$status, $headers, $body, $err] = fetch_once($cur, $ip, $max_bytes);
        if ($err !== null) {
            return [null, $cur, $headers, '', $err, $ip];
        }
        if (in_array($status, [301, 302, 303, 307, 308], true) && isset($headers['location'])
            && $headers['location'] !== '') {
            $next = resolve_url($cur, $headers['location']);
            [$bad, $nip] = check_url($next);
            if ($bad !== null) {
                // 名前を引けない以外（内部アドレス・アドレス直指定など）は、窓口として確認を断る（判定には使わない）
                $e = strpos($bad, '国内から:') === 0 ? $bad : REFUSED . "リダイレクト先を確認しない（{$bad}）";
                return [null, $next, [], '', $e, $ip];
            }
            $cur = $next;
            $ip = $nip;
            continue;
        }
        return [$status, $cur, $headers, $body, null, $ip];
    }
    return [null, $cur, [], '', TOO_MANY_REDIRECTS, $ip];
}

// robots.txt（Python 3.12 の urllib.robotparser と同じ判定）。
// 取得できない（時間切れ・接続できない）ときは許可扱い（probe.py と同じ）。401/403 は不許可、その他の 4xx は許可、5xx は不許可。
function robots_allows(string $url, string $ip): bool
{
    $p = parse_url($url);
    $base = strtolower($p['scheme']) . '://' . $p['host'] . (isset($p['port']) ? ':' . $p['port'] : '');
    [$status, , , $body, $err] = fetch_follow($base . '/robots.txt', $ip, 500000);
    if ($err === TOO_MANY_REDIRECTS) {
        return false;  // Python では HTTPError（3xx）になり、どれにも当たらないので不許可
    }
    if ($err !== null || $status === null) {
        return true;
    }
    if ($status === 401 || $status === 403) {
        return false;
    }
    if ($status >= 400 && $status < 500) {
        return true;
    }
    if ($status < 200 || $status >= 300) {
        return false;
    }
    if (function_exists('mb_check_encoding') && !mb_check_encoding($body, 'UTF-8')) {
        return true;  // Python では UTF-8 として読めず例外になり、probe.py は許可扱いにする
    }
    // 解析（robotparser.parse と同じ状態遷移。空行でグループが終わる）
    $entries = [];
    $default = null;
    $entry = ['agents' => [], 'rules' => []];
    $state = 0;
    $add = function (array $e) use (&$entries, &$default) {
        if (in_array('*', $e['agents'], true)) {
            if ($default === null) {
                $default = $e;
            }
        } else {
            $entries[] = $e;
        }
    };
    foreach (preg_split('/\r\n|\r|\n/', $body) as $line) {
        if ($line === '') {
            if ($state === 1) {
                $entry = ['agents' => [], 'rules' => []];
                $state = 0;
            } elseif ($state === 2) {
                $add($entry);
                $entry = ['agents' => [], 'rules' => []];
                $state = 0;
            }
        }
        if (($i = strpos($line, '#')) !== false) {
            $line = substr($line, 0, $i);
        }
        $line = trim($line);
        if ($line === '' || strpos($line, ':') === false) {
            continue;
        }
        [$k, $v] = explode(':', $line, 2);
        $k = strtolower(trim($k));
        $v = rawurldecode(trim($v));
        if ($k === 'user-agent') {
            if ($state === 2) {
                $add($entry);
                $entry = ['agents' => [], 'rules' => []];
            }
            $entry['agents'][] = $v;
            $state = 1;
        } elseif ($k === 'disallow' || $k === 'allow') {
            if ($state !== 0) {
                $entry['rules'][] = [$k === 'allow' || $v === '', $v];
                $state = 2;
            }
        } elseif ($k === 'crawl-delay' || $k === 'request-rate') {
            if ($state !== 0) {
                $state = 2;
            }
        }
    }
    if ($state === 2) {
        $add($entry);
    }
    $path = rawurldecode(($p['path'] ?? '') . (isset($p['query']) ? '?' . $p['query'] : ''));
    if ($path === '') {
        $path = '/';
    }
    $allowance = function (array $e) use ($path): bool {
        foreach ($e['rules'] as [$allow, $rule]) {
            if ($rule === '' || strpos($path, $rule) === 0) {  // Python では「*」は %2A になり何にも当たらない
                return $allow;
            }
        }
        return true;
    };
    foreach ($entries as $e) {
        foreach ($e['agents'] as $a) {
            $a = strtolower($a);
            if ($a === '*' || $a === '' || strpos('tdf47-bot', $a) !== false) {
                return $allowance($e);
            }
        }
    }
    return $default !== null ? $allowance($default) : true;
}

// <title>（probe.py と同じく、文字コードはヘッダーの charset だけを見る。無ければ UTF-8）。
function title_of(string $html, array $headers): ?string
{
    if (!preg_match('/<title[^>]*>(.*?)<\/title>/is', $html, $m)) {
        return null;
    }
    $t = $m[1];
    try {
        $cs = 'UTF-8';
        if (isset($headers['content-type']) && preg_match('/charset=["\']?([\w\-]+)/i', $headers['content-type'], $c)) {
            $cs = $c[1];
        }
        if (function_exists('mb_convert_encoding') && strtolower($cs) !== 'utf-8') {
            $conv = mb_convert_encoding($t, 'UTF-8', $cs);
            if (is_string($conv)) {
                $t = $conv;
            }
        }
        if (function_exists('mb_scrub')) {
            mb_substitute_character(0xFFFD);
            $t = mb_scrub($t, 'UTF-8');
        }
    } catch (\Throwable $e) {
        if (function_exists('mb_scrub')) {
            $t = mb_scrub($m[1], 'UTF-8');
        }
    }
    $t = preg_replace('/\s+/u', ' ', $t);
    return $t === null ? null : trim($t);
}

// ---- 受付 ----
try {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        reply(405, ['error' => 'POST のみ']);
    }
    $cfg = __DIR__ . '/config.php';
    $hash = is_file($cfg) ? (string)(include $cfg) : '';
    if ($hash === '') {
        reply(503, ['error' => '合言葉が設定されていない']);
    }
    $token = (string)($_SERVER['HTTP_X_TDF47_TOKEN'] ?? '');
    if ($token === '' || !hash_equals($hash, hash('sha256', $token))) {
        reply(403, ['error' => '合言葉が違う']);
    }
    $req = json_decode((string)file_get_contents('php://input', false, null, 0, 10000), true);
    $url = is_array($req) ? (string)($req['url'] ?? '') : '';
    if ($url === '' || strlen($url) > 2000) {
        reply(400, ['error' => 'url がない']);
    }
    if (function_exists('set_time_limit')) {
        @set_time_limit(DEADLINE_SEC + 20);
    }

    [$bad, $ip] = check_url($url);
    if ($bad !== null) {
        if (strpos($bad, '国内から:') === 0) {
            reply(200, ['status' => null, 'final_url' => $url, 'title' => null, 'error' => $bad, 'robots_blocked' => false]);
        }
        reply(400, ['error' => $bad]);
    }
    if (!robots_allows($url, $ip)) {
        reply(200, ['status' => null, 'final_url' => $url, 'title' => null, 'error' => null, 'robots_blocked' => true]);
    }
    usleep(DELAY_US);

    [$status, $final, $headers, $body, $err] = fetch_follow($url, $ip, MAX_BYTES);
    if ($err !== null && strpos($err, REFUSED) === 0) {
        reply(422, ['error' => $err]);
    }
    if ($err !== null) {
        reply(200, ['status' => null, 'final_url' => $url, 'title' => null, 'error' => $err, 'robots_blocked' => false]);
    }
    if ($status === null || $status >= 300) {
        // probe.py と同じく、エラーのときの final_url は元のURL
        reply(200, ['status' => $status, 'final_url' => $url, 'title' => null,
                    'error' => 'HTTPError ' . (string)$status, 'robots_blocked' => false]);
    }
    reply(200, ['status' => $status, 'final_url' => $final, 'title' => title_of($body, $headers),
                'error' => null, 'robots_blocked' => false]);
} catch (\Throwable $e) {
    reply(500, ['error' => '窓口の内部エラー']);
}
