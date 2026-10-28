<?php
// TDF47 国内からの再確認窓口（/_recheck/index.php として配信）。
// 毎日のリンク確認（GitHub＝海外）で「時間切れ・接続拒否」になったリンクを、国内のこのサーバーから確認し直す。
// 海外からの接続を断っている自治体サイトを、リンク切れと誤判定しないため（2026-10-28）。
//
// 使い方: POST（本文 {"url": "https://..."}）、ヘッダー X-TDF47-Token に合言葉。1回1件。
// 応答: {"status": 200|null, "final_url": "...", "title": "...", "error": null|"...", "robots_blocked": false}
// 合言葉そのものはサーバーに置かない。配信時に GitHub の秘密情報から作ったハッシュを config.php に書き込む。
// 踏み台にされないよう、IPアドレス直指定・内部アドレス・http/https 以外には接続しない。リダイレクト先も毎回確かめる。

declare(strict_types=1);

const UA = 'TDF47-bot/1.0 (+https://tdf47.com/about/; link checker; contact: https://tdf47.com/contact/)';
const TIMEOUT = 15;
const ROBOTS_TIMEOUT = 10;
const MAX_BYTES = 200000;
const MAX_REDIRECTS = 10;
const DELAY_US = 1200000;

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Robots-Tag: noindex');

function reply(int $code, array $data): void
{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function allow_local(): bool
{
    // テスト専用（手元で 127.0.0.1 のテスト用サーバーに接続するため）。本番のサーバーでは設定しない。
    return getenv('TDF47_RECHECK_ALLOW_LOCAL') === '1';
}

// 接続先の名前を引き、すべて外部のアドレスなら最初のアドレスを返す（接続はこのアドレスに固定する）。
// 名前が引けないときは ''（接続時に「名前解決できない」になる）。内部のアドレスを含むときは null。
function resolve_public(string $host): ?string
{
    if ($host === '' || filter_var($host, FILTER_VALIDATE_IP) !== false || strpos($host, ':') !== false) {
        return null;  // IPアドレスの直指定は受け付けない
    }
    if (allow_local() && $host === 'localhost') {
        return '127.0.0.1';
    }
    $ips = gethostbynamel($host);
    if ($ips === false || !$ips) {
        return '';
    }
    foreach ($ips as $ip) {
        if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE) === false) {
            return null;
        }
    }
    return $ips[0];
}

// 確認してよいURLか。よければ [null, 接続先アドレス]、だめなら [理由, null]。
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
    $ip = resolve_public(strtolower($p['host']));
    if ($ip === null) {
        return ['内部アドレスには接続しない', null];
    }
    return [null, $ip];
}

// 接続先をあらかじめ確かめたアドレスに固定する（確かめた後に名前の向き先を変えられても内部へ届かない）。
function pin_for(string $url, string $ip): array
{
    if ($ip === '') {
        return [];
    }
    $p = parse_url($url);
    $port = isset($p['port']) ? (int)$p['port'] : (strtolower($p['scheme']) === 'https' ? 443 : 80);
    return [$p['host'] . ':' . $port . ':' . $ip];
}

// curl のエラーを、確認プログラム（probe.py）と同じ判定ができる文言にそろえる。
function curl_error_text(int $no, string $msg): string
{
    switch ($no) {
        case CURLE_OPERATION_TIMEDOUT:
            return "国内から: timed out ($msg)";
        case CURLE_COULDNT_CONNECT:
            return stripos($msg, 'refused') !== false
                ? "国内から: Connection refused ($msg)" : "国内から: could not connect ($msg)";
        case CURLE_COULDNT_RESOLVE_HOST:
            return "国内から: Name or service not known ($msg)";
        case CURLE_RECV_ERROR:
        case CURLE_SEND_ERROR:
            return "国内から: Connection reset ($msg)";
        case CURLE_GOT_NOTHING:
            return "国内から: RemoteDisconnected ($msg)";
        default:
            if (stripos($msg, 'SSL') !== false || stripos($msg, 'certificate') !== false) {
                return "国内から: SSL ($msg)";
            }
            return "国内から: curl $no ($msg)";
    }
}

// 1回分の取得（リダイレクトはたどらない）。[status, headers, body, error]
function fetch_once(string $url, int $timeout, int $max_bytes, array $pin): array
{
    $body = '';
    $truncated = false;
    $headers = [];
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
        CURLOPT_CONNECTTIMEOUT => $timeout,
        CURLOPT_TIMEOUT => $timeout,
        CURLOPT_USERAGENT => UA,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_ENCODING => '',
        CURLOPT_RESOLVE => $pin,
        CURLOPT_HEADERFUNCTION => function ($ch, $line) use (&$headers) {
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
    curl_close($ch);
    if ($ok === false && !($truncated && $no === CURLE_WRITE_ERROR)) {
        return [null, $headers, '', curl_error_text($no, $msg)];
    }
    return [$status ?: null, $headers, substr($body, 0, $max_bytes), null];
}

// robots.txt（Python の urllib.robotparser と同じく、該当グループの規則を上から見て最初に当てはまったもの）。
function robots_allows(string $url, string $ip): bool
{
    $p = parse_url($url);
    $base = strtolower($p['scheme']) . '://' . $p['host'] . (isset($p['port']) ? ':' . $p['port'] : '');
    [$status, , $body, $err] = fetch_once($base . '/robots.txt', ROBOTS_TIMEOUT, 500000, pin_for($base . '/', $ip));
    if ($err !== null || $status === null) {
        return true;  // 取得できないときは許可扱い（probe.py と同じ）
    }
    if ($status === 401 || $status === 403) {
        return false;
    }
    if ($status >= 400) {
        return true;
    }
    $path = ($p['path'] ?? '/') . (isset($p['query']) ? '?' . $p['query'] : '');
    $groups = [];
    $cur = null;
    $last_was_agent = false;
    foreach (preg_split('/\r\n|\r|\n/', $body) as $line) {
        $line = trim(preg_replace('/#.*/', '', $line));
        if ($line === '' || strpos($line, ':') === false) {
            continue;
        }
        [$k, $v] = array_map('trim', explode(':', $line, 2));
        $k = strtolower($k);
        if ($k === 'user-agent') {
            if (!$last_was_agent) {
                $groups[] = ['agents' => [], 'rules' => []];
                $cur = count($groups) - 1;
            }
            $groups[$cur]['agents'][] = strtolower($v);
            $last_was_agent = true;
        } elseif (($k === 'allow' || $k === 'disallow') && $cur !== null) {
            $groups[$cur]['rules'][] = [$k === 'allow', $v];
            $last_was_agent = false;
        } else {
            $last_was_agent = false;
        }
    }
    $pick = null;
    foreach ($groups as $g) {
        foreach ($g['agents'] as $a) {
            if ($a !== '*' && strpos('tdf47-bot', $a) !== false) {
                $pick = $g;
                break 2;
            }
        }
    }
    if ($pick === null) {
        foreach ($groups as $g) {
            if (in_array('*', $g['agents'], true)) {
                $pick = $g;
                break;
            }
        }
    }
    if ($pick === null) {
        return true;
    }
    foreach ($pick['rules'] as [$allow, $rule]) {
        if ($rule === '') {
            if (!$allow) {
                return true;  // 空の Disallow は「すべて許可」（urllib.robotparser と同じ）
            }
            continue;
        }
        if ($rule === '*' || strpos(rawurldecode($path), rawurldecode($rule)) === 0) {
            return $allow;
        }
    }
    return true;
}

function title_of(string $html, array $headers): ?string
{
    if (!preg_match('/<title[^>]*>(.*?)<\/title>/is', $html, $m)) {
        return null;
    }
    $t = $m[1];
    $cs = null;
    if (isset($headers['content-type']) && preg_match('/charset=([\w\-]+)/i', $headers['content-type'], $c)) {
        $cs = $c[1];
    } elseif (preg_match('/<meta[^>]+charset=["\']?([\w\-]+)/i', $html, $c)) {
        $cs = $c[1];
    }
    if ($cs && strtolower($cs) !== 'utf-8' && function_exists('mb_convert_encoding')) {
        $conv = @mb_convert_encoding($t, 'UTF-8', $cs);
        if ($conv !== false) {
            $t = $conv;
        }
    }
    if (function_exists('mb_scrub')) {
        $t = mb_scrub($t, 'UTF-8');
    }
    return trim(preg_replace('/\s+/u', ' ', $t) ?? $t);
}

// ---- 受付 ----
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
@set_time_limit(60);

[$bad, $ip] = check_url($url);
if ($bad !== null) {
    reply(400, ['error' => $bad]);
}
if (!robots_allows($url, $ip)) {
    reply(200, ['status' => null, 'final_url' => $url, 'title' => null, 'error' => null, 'robots_blocked' => true]);
}
usleep(DELAY_US);

$cur = $url;
for ($i = 0; $i <= MAX_REDIRECTS; $i++) {
    [$status, $headers, $body, $err] = fetch_once($cur, TIMEOUT, MAX_BYTES, pin_for($cur, $ip));
    if ($err !== null) {
        reply(200, ['status' => null, 'final_url' => $cur, 'title' => null, 'error' => $err, 'robots_blocked' => false]);
    }
    if (in_array($status, [301, 302, 303, 307, 308], true) && isset($headers['location'])) {
        $next = $headers['location'];
        if (!preg_match('#^https?://#i', $next)) {
            $p = parse_url($cur);
            $origin = $p['scheme'] . '://' . $p['host'] . (isset($p['port']) ? ':' . $p['port'] : '');
            if (strpos($next, '//') === 0) {
                $next = $p['scheme'] . ':' . $next;
            } elseif (strpos($next, '/') === 0) {
                $next = $origin . $next;
            } else {
                $dir = preg_replace('#/[^/]*$#', '/', $p['path'] ?? '/');
                $next = $origin . $dir . $next;
            }
        }
        [$bad, $ip] = check_url($next);
        if ($bad !== null) {
            reply(200, ['status' => null, 'final_url' => $next, 'title' => null,
                        'error' => "国内から: リダイレクト先を確認しない（$bad）", 'robots_blocked' => false]);
        }
        $cur = $next;
        continue;
    }
    $error = ($status === null || $status >= 400) ? 'HTTPError ' . (string)$status : null;
    reply(200, ['status' => $status, 'final_url' => $cur, 'title' => title_of($body, $headers),
                'error' => $error, 'robots_blocked' => false]);
}
reply(200, ['status' => null, 'final_url' => $cur, 'title' => null,
            'error' => '国内から: リダイレクトが多すぎる', 'robots_blocked' => false]);
