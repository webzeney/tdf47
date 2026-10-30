#!/usr/bin/env python3
"""probe.py — 死活監視・差分検知（設計書 10章）。VPS上の日次cronで実行する。

見るのは3つだけ（本文ハッシュは使わない。新着欄で毎日変わりノイズになるため）:
  1. HTTPステータス（200以外）
  2. 最終リダイレクト先URL（前回との差）
  3. <title> の変化
変化のあった行（changed=1）を reports/monthly-review-YYYYMM.csv に出し、
月1回の目視確認 → 承認したものをDBへ反映する運用。

マナー（設計書 13章）: robots.txt尊重 / 1リクエスト1秒以上 / User-Agentに連絡先。
★このスクリプトは手元のPCで走らせない。VPSから実行すること。
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

UA = "tdf47.com-bot/1.0 (+https://tdf47.com/about/; link checker; contact: https://tdf47.com/contact/)"
DELAY_SEC = 1.2       # 1リクエスト1秒以上
TIMEOUT = 20
NOW = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
MONTH = datetime.date.today().strftime("%Y%m")
REVIEW_CSV = os.path.join(ROOT, "reports", f"monthly-review-{MONTH}.csv")

REVIEW_FIELDS = ["checked_at", "entity_code", "entity_name", "kind", "prev_status", "new_status",
                 "link_id", "url", "http_status", "final_url", "title", "error", "changed"]
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_robots_cache: dict[str, RobotFileParser] = {}

# 大手の例規集サービス。人がブラウザで開くと正常に表示されるが、プログラムからの確認では
#   - ぎょうせい（d1-law.com）… 403 を返す（ボット拒否）
#   - 第一法規（legal-square.com）… 古い暗号方式のため Python の既定設定では SSL ハンドシェイクに失敗
# となり「リンク切れ」と誤判定されていた（2026-10-25 確認：北海道例規類集・Reiki-Base とも実在）。
# これらのサービスで「拒否・通信方式の不一致」だった場合は、生死を判定できない（inconclusive）として
# リンク切れにしない。404・ドメイン消滅など、明確に存在しない応答はこれまでどおりリンク切れにする。
ROBOTS_ERR = "robots.txt で不許可"  # robots.txt で確認を断られたとき probe.error に入れる値
INCONCLUSIVE_HOSTS = ("d1-law.com", "legal-square.com")
INCONCLUSIVE_HTTP = {401, 403, 429}
INCONCLUSIVE_ERR = ("SSL", "RemoteDisconnected", "timed out", "Connection reset")

# 公式SNS（Facebook・Instagram・X）は robots.txt で自動確認を断られるため、直接は確認できない。
# 代わりに、その自治体の公式サイト（トップページ）を確認したときに、そのアカウントへのリンクが
# 載っているかを照合して probe に記録する（2026-10-25）。画面には出さない（内部記録のみ）。
# 「掲載あり」→「掲載なし」に変わったものだけを月次レビュー CSV に載せる（見直しの材料）。
# ※トップに載っていないだけでアカウントが消えたとは限らないので、link.status は変えない。
OFFICIAL_LISTED = "公式サイトに掲載あり"
OFFICIAL_MISSING = "公式サイトに掲載なし"
OFFICIAL_MARKERS = (OFFICIAL_LISTED, OFFICIAL_MISSING)  # この2つは「確認の試み」に数えない
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_SNS_HOST_ALIAS = {"twitter.com": "x.com", "fb.com": "facebook.com", "instagr.am": "instagram.com",
                   "page.line.me": "line", "line.me": "line"}
_SNS_HOSTS = {"x.com", "facebook.com", "instagram.com", "youtube.com", "line"}
_NOT_ACCOUNT = {"intent", "share", "sharer", "sharer.php", "home", "p", "explore", "reel", "watch",
                "hashtag", "search", "dialog", "plugins", "tr", "results", "embed", "playlist",
                "login", "signup", "i", "r", "ti", "stories"}


def host_matches(url: str, suffixes=INCONCLUSIVE_HOSTS) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return any(h == s or h.endswith("." + s) for s in suffixes)


# サーバーが中間証明書を送っていない（証明書チェーン不完全）。ブラウザは自動で補って普通に開けるが、
# Python では検証に失敗する（2026-10-25 直方市の公式サイトで確認）。サーバー自体は応答しているので、
# どのホストでもリンク切れにはしない。
INCOMPLETE_CHAIN_ERR = "unable to get local issuer certificate"


def is_inconclusive(url: str, status, err) -> bool:
    """生死を判定できない応答か（証明書チェーン不完全・200以外の成功応答はすべてのホスト、
    それ以外は INCONCLUSIVE_HOSTS のみ）。"""
    if err and status is None and INCOMPLETE_CHAIN_ERR in err:
        return True
    # 200以外の成功応答（202 など）。ボット対策の確認画面などで返ることがあり、ページの有無は分からない
    # （2026-10-27 確認：茨城県後期高齢者医療広域連合・益子町・河内町で毎回 202）。
    if status is not None and 200 < status < 300:
        return True
    if not host_matches(url):
        return False
    if status in INCONCLUSIVE_HTTP:
        return True
    return bool(err) and status is None and any(k in err for k in INCONCLUSIVE_ERR)


# 一時的な不調の可能性がある失敗（時間切れ・接続の切断・サーバー側の一時エラー）。
# 1回だけではリンク切れにせず、前回の確認でも失敗していたとき（2回続けて失敗）にリンク切れにする。
# （2026-10-27：一度の時間切れでリンク切れになり、ブラウザでは見られる例規集があったため）
TRANSIENT_HTTP = {502, 503, 504}
TRANSIENT_ERR = ("timed out", "RemoteDisconnected", "Connection reset")


def is_transient(status, err) -> bool:
    """一時的な不調の可能性がある失敗か。"""
    if status in TRANSIENT_HTTP:
        return True
    return bool(err) and status is None and any(k in err for k in TRANSIENT_ERR)


def is_alive(status, err) -> bool:
    return status == 200 and err is None


def _legacy_ssl_context():
    """古い暗号方式のサーバー向け。証明書の検証は通常どおり行う。"""
    import ssl
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    return ctx


def sns_key(url: str):
    """SNSアカウントURLを照合用のキー (サービス, アカウント) にする。アカウントと判別できなければ None。
    例: https://twitter.com/Warabi_City/ と https://x.com/warabi_city は同じキーになる。"""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    h = (p.hostname or "").lower()
    for pre in ("www.", "m.", "mobile.", "web."):
        if h.startswith(pre):
            h = h[len(pre):]
    h = _SNS_HOST_ALIAS.get(h, h)
    if h not in _SNS_HOSTS:
        return None
    segs = [x for x in unquote(p.path).lower().split("/") if x]
    if not segs:
        return None
    if h == "facebook.com":
        if segs[0] == "profile.php":
            ids = parse_qs(p.query).get("id")
            return (h, "id:" + ids[0]) if ids else None
        if segs[0] == "pages" and segs[-1].isdigit():
            return (h, "id:" + segs[-1])
        if segs[0] == "people" and segs[-1].isdigit():
            return (h, "id:" + segs[-1])
    if h == "youtube.com":
        if segs[0] in ("channel", "c", "user") and len(segs) > 1:
            return (h, segs[0] + "/" + segs[1])
        return (h, segs[0]) if segs[0].startswith("@") else None
    if h == "line":
        # page.line.me/<id> と line.me/R/ti/p/@<id> を同じキーに
        acc = segs[-1] if segs[0] == "r" else segs[0]
        acc = acc.lstrip("@")
        return (h, acc) if acc and acc not in _NOT_ACCOUNT else None
    if segs[0] in _NOT_ACCOUNT:
        return None
    return (h, segs[0].lstrip("@"))


def sns_keys_in(html: str, base_url: str) -> set:
    """ページ内のリンク先からSNSアカウントのキーを集める。"""
    keys = set()
    for href in _HREF_RE.findall(html or ""):
        k = sns_key(urljoin(base_url, href.replace("&amp;", "&")))
        if k:
            keys.add(k)
    return keys


def check_official_listing(conn, entity_code, entity_name, official_final_url, html):
    """公式サイトのページに、その団体の公式SNSアカウントが載っているかを照合して記録する。
    月次レビューに載せる行（掲載あり→掲載なし に変わったもの）を返す。"""
    found = sns_keys_in(html, official_final_url)
    review = []
    rows = conn.execute(
        "SELECT id, url, status, kind_code FROM link WHERE entity_code=? AND substr(kind_code,1,4)='sns_' "
        "AND url IS NOT NULL AND status IN ('ok','unchecked')", (entity_code,)).fetchall()
    for r in rows:
        k = sns_key(r["url"])
        if not k:
            continue
        marker = OFFICIAL_LISTED if k in found else OFFICIAL_MISSING
        prev = conn.execute(
            "SELECT error FROM probe WHERE link_id=? AND error IN (?,?) ORDER BY checked_at DESC, id DESC LIMIT 1",
            (r["id"],) + OFFICIAL_MARKERS).fetchone()
        conn.execute(
            "INSERT INTO probe (link_id,checked_at,http_status,final_url,page_title,changed,error) "
            "VALUES (?,?,NULL,?,NULL,?,?)",
            (r["id"], NOW, official_final_url, 0, marker))
        if prev and prev["error"] == OFFICIAL_LISTED and marker == OFFICIAL_MISSING:
            conn.execute("UPDATE probe SET changed=1 WHERE id=last_insert_rowid()")
            review.append({"checked_at": NOW, "entity_code": entity_code, "entity_name": entity_name,
                           "kind": r["kind_code"], "prev_status": r["status"], "new_status": r["status"],
                           "link_id": r["id"], "url": r["url"], "http_status": "",
                           "final_url": official_final_url, "title": "",
                           "error": "公式サイトのトップページから、このアカウントへのリンクがなくなった",
                           "changed": 1})
    return review


def allowed_by_robots(url: str) -> bool:
    try:
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rp = _robots_cache.get(base)
        if rp is None:
            rp = RobotFileParser()
            rp.set_url(base + "/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None  # robots取得失敗時は許可扱い
            _robots_cache[base] = rp
        return True if rp is None else rp.can_fetch(UA, url)
    except Exception:
        return True


def fetch(url: str):
    """(http_status, final_url, title, error) を返す。"""
    return fetch_page(url)[:4]


def fetch_page(url: str, max_bytes: int = 200_000):
    """(http_status, final_url, title, error, html) を返す。
    例規集サービスで SSL ハンドシェイクに失敗したときは、古い暗号方式も許す設定で1回だけ再試行する。"""
    res = _fetch(url, max_bytes=max_bytes)
    if res[3] and res[0] is None and "SSL" in res[3] and host_matches(url):
        time.sleep(DELAY_SEC)
        res = _fetch(url, _legacy_ssl_context(), max_bytes=max_bytes)
    return res


def _fetch(url: str, context=None, max_bytes: int = 200_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=context) as resp:
            final_url = resp.geturl()
            raw = resp.read(max_bytes)  # 先頭だけ（titleは頭にある。公式サイトはSNS照合のため多めに読む）
            enc = resp.headers.get_content_charset() or "utf-8"
            try:
                html = raw.decode(enc, errors="replace")
            except LookupError:
                html = raw.decode("utf-8", errors="replace")
            m = _TITLE_RE.search(html)
            title = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
            return resp.status, final_url, title, None, html
    except urllib.error.HTTPError as e:
        return e.code, url, None, f"HTTPError {e.code}", ""
    except Exception as e:
        return None, url, None, f"{type(e).__name__}: {e}", ""


# 国内からの再確認窓口（tdf47.com のサーバー上の src/server/recheck.php）。
# 毎日の確認は GitHub（海外）で動くため、海外からの接続を断っている自治体サイトは毎回「時間切れ」になる。
# 時間切れ・接続拒否などになったリンクだけを、この窓口に国内から確認し直してもらう（2026-10-28）。
RECHECK_URL = os.environ.get("RECHECK_URL") or "https://tdf47.com/_recheck/index.php"
RECHECK_TIMEOUT = 90
RECHECK_MAX_PER_RUN = 60  # 1回の確認で国内から確認し直す上限（1件あたり最大90秒待つため、全体の時間を抑える）
RECHECK_GIVE_UP = 3       # 窓口が続けてこの回数使えなかったら、その回は国内からの確認をやめる
# 国内から確認し直す失敗（海外からの接続を断っているサーバーで起きるもの）。
RECHECK_ERR = ("timed out", "Connection refused", "Connection reset", "RemoteDisconnected",
               "No route to host", "Network is unreachable")


def needs_recheck(status, err) -> bool:
    """国内から確認し直す対象か（応答が無い失敗のみ。404 などサーバーが答えたものは対象外）。"""
    return status is None and bool(err) and any(k in err for k in RECHECK_ERR)


def recheck_domestic(url: str):
    """国内から確認し直した結果 (http_status, final_url, title, error, robots_blocked)。
    窓口が使えない（合言葉が無い・応答が無い・形式が違う）ときは None（呼び出し側は今までどおりに判定する）。"""
    import json
    token = os.environ.get("RECHECK_TOKEN", "")
    if not token:
        return None
    req = urllib.request.Request(
        RECHECK_URL, data=json.dumps({"url": url}).encode("utf-8"), method="POST",
        headers={"User-Agent": UA, "Content-Type": "application/json", "X-Recheck-Token": token})
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None  # 合言葉を別の場所へ送らないよう、リダイレクトはたどらない

    try:
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=RECHECK_TIMEOUT) as resp:
            d = json.loads(resp.read(100_000).decode("utf-8"))
    except Exception as e:
        print(f"  国内からの再確認に失敗（今までどおり判定）: {type(e).__name__}: {e}")
        return None
    if not isinstance(d, dict) or "robots_blocked" not in d:
        return None
    st = d.get("status")
    return (st if isinstance(st, int) else None, d.get("final_url") or url, d.get("title"),
            d.get("error"), bool(d.get("robots_blocked")))


def last_probe(conn, link_id):
    row = conn.execute(
        "SELECT http_status,final_url,page_title,error FROM probe "
        "WHERE link_id=? AND (error IS NULL OR error NOT IN (?,?,?)) ORDER BY checked_at DESC LIMIT 1",
        (link_id, ROBOTS_ERR) + OFFICIAL_MARKERS).fetchone()
    return dict(row) if row else None


def recent_probes(conn, link_id, n=2):
    """「確認の試み」の直近 n 回（新しい順）。robots.txt 不許可・公式サイト掲載の照合は除く。"""
    return [dict(r) for r in conn.execute(
        "SELECT http_status,error FROM probe WHERE link_id=? AND (error IS NULL OR error NOT IN (?,?,?)) "
        "ORDER BY checked_at DESC, id DESC LIMIT ?",
        (link_id, ROBOTS_ERR) + OFFICIAL_MARKERS + (n,)).fetchall()]


def failed_before(prev, url) -> bool:
    """前回の確認でも失敗していたか（prev は前回の確認結果。無ければ None）。
    一時的な失敗が2回続いたらリンク切れにするために使う。前回が「判定できない」応答なら失敗に数えない。"""
    if prev is None or is_alive(prev["http_status"], prev["error"]):
        return False
    return not is_inconclusive(url, prev["http_status"], prev["error"])


def reclassify(conn) -> int:
    """dead のうち、直近の確認結果が「判定できない」か「一時的な失敗が1回だけ」だったものを
    unchecked に戻す（通信なし・冪等）。"""
    rows = conn.execute("SELECT id, url FROM link WHERE status='dead'").fetchall()
    n = 0
    for r in rows:
        ps = recent_probes(conn, r["id"])
        if not ps:
            continue
        last, prev = ps[0], (ps[1] if len(ps) > 1 else None)
        back = is_inconclusive(r["url"], last["http_status"], last["error"]) or (
            is_transient(last["http_status"], last["error"])
            and not failed_before(prev, r["url"]))
        if back:
            conn.execute("UPDATE link SET status='unchecked' WHERE id=?", (r["id"],))
            n += 1
    conn.commit()
    return n


KEEP_HISTORY = 3  # probe テーブルに残す1リンクあたりの履歴数


def prune_history(conn) -> int:
    """probe の履歴を1リンクにつき直近 KEEP_HISTORY 回分に間引く。
    DBは毎日 git にコミットされるため、履歴を無制限に残すとDB・リポジトリが膨らみ続ける。
    使っているのは「直近の結果」（前回との比較・誤判定の見直し・巡回の順番）だけ。
    公式サイト掲載の照合（OFFICIAL_MARKERS）と robots.txt 不許可の記録は通常の確認とは別枠で、
    それぞれ直近3回分を残す（不許可の記録が通常の確認の履歴を押し出さないように。2回続けての失敗の判定に使う）。
    変化のあった確認は reports/monthly-review-*.csv にも残る。"""
    cur = conn.execute(
        "DELETE FROM probe WHERE id IN (SELECT id FROM (SELECT id, ROW_NUMBER() OVER "
        "(PARTITION BY link_id, CASE WHEN error IN (?,?) THEN 1 WHEN error = ? THEN 2 ELSE 0 END "
        "ORDER BY checked_at DESC, id DESC) AS rn FROM probe) WHERE rn > ?)",
        OFFICIAL_MARKERS + (ROBOTS_ERR, KEEP_HISTORY))
    n = cur.rowcount
    conn.commit()
    if n:
        conn.execute("VACUUM")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="1回に検証する最大リンク数（0=全件）。日次ローリング用。")
    ap.add_argument("--reclassify-only", action="store_true",
                    help="通信せず、既存の確認結果からリンク切れの誤判定だけを戻して終了する。")
    args = ap.parse_args()

    conn = db.connect()
    db.init_schema(conn)
    n_back = reclassify(conn)
    print(f"誤判定の見直し: リンク切れ → 未確認 に戻したリンク {n_back} 件")
    if args.reclassify_only:
        conn.close()
        return
    # 種まき(unchecked)・既存ok・dead(復活確認) を対象。未確認(last_checked NULL)を最優先、
    # 以降は確認が古い順。--limit で1回分を区切るとローリング検証になる。
    # 並び順は「最後に確認を試みた日時」（probe の最新 checked_at）が古い順。一度も試していないものが最優先。
    # ※以前は link.last_checked（生存を確認できた日）で並べていたため、robots.txt で不許可のリンクや
    #   リンク切れのリンクは日付が入らず毎日先頭に居座り、1日の枠を食いつぶしていた（2026-10-25修正）。
    sql = ("SELECT l.id, l.url, l.status, l.kind_code, e.code AS entity_code, e.name AS entity_name "
           "FROM link l JOIN entity e ON e.code = l.entity_code "
           "LEFT JOIN (SELECT link_id, MAX(checked_at) AS last_try FROM probe "
           "WHERE error IS NULL OR error NOT IN (?,?) GROUP BY link_id) t "
           "ON t.link_id = l.id "
           "WHERE l.url IS NOT NULL AND l.status IN ('ok','unchecked','dead') "
           "ORDER BY (t.last_try IS NOT NULL), t.last_try ASC, l.id ASC")
    if args.limit and args.limit > 0:
        sql += f" LIMIT {int(args.limit)}"
    links = [dict(r) for r in conn.execute(sql, OFFICIAL_MARKERS)]
    print(f"probe対象: {len(links)} リンク（limit={args.limit or '全件'}）")
    changed_rows = []
    n_robots = 0
    n_sns = 0  # SNSの掲載照合をした公式サイトの数
    n_recheck = n_recheck_ok = 0  # 国内から確認し直した数・そのうち生存を確認できた数
    n_recheck_try = n_recheck_fail_streak = 0  # 窓口に頼んだ数（上限の計算用）・窓口が続けて使えなかった数
    for i, l in enumerate(links, 1):
        url = l["url"]
        if not allowed_by_robots(url):
            # 確認しない（マナー）。試みた記録だけ残し、順番を後ろに回す。status は変えない。
            conn.execute(
                "INSERT INTO probe (link_id,checked_at,http_status,final_url,page_title,changed,error) "
                "VALUES (?,?,NULL,NULL,NULL,0,?)", (l["id"], NOW, ROBOTS_ERR))
            n_robots += 1
            continue
        if l["kind_code"] == "official":
            status, final_url, title, err, html = fetch_page(url, max_bytes=600_000)
        else:
            status, final_url, title, err, html = fetch_page(url)
        if (needs_recheck(status, err) and n_recheck_try < RECHECK_MAX_PER_RUN
                and n_recheck_fail_streak < RECHECK_GIVE_UP):
            time.sleep(DELAY_SEC)
            n_recheck_try += 1
            rc = recheck_domestic(url)
            n_recheck_fail_streak = 0 if rc is not None else n_recheck_fail_streak + 1
            if rc is not None and rc[0] is None and rc[3] and not rc[4] and not is_transient(None, rc[3]):
                # 国内からは「名前を引けない・SSL・その他の通信エラー」：窓口側の事情のこともあるので判定に使わず、
                # 海外からの結果（時間切れなど＝1回目は猶予）で判定する
                rc = None
            if rc is not None:
                n_recheck += 1
                if rc[4]:
                    # 国内から読んだ robots.txt では確認を断られた：確認しない（上の robots と同じ扱い）
                    conn.execute(
                        "INSERT INTO probe (link_id,checked_at,http_status,final_url,page_title,changed,error) "
                        "VALUES (?,?,NULL,NULL,NULL,0,?)", (l["id"], NOW, ROBOTS_ERR))
                    n_robots += 1
                    time.sleep(DELAY_SEC)
                    continue
                # 国内からの結果で判定する（SNSの掲載照合は本文を受け取らないので行わない）
                status, final_url, title, err = rc[:4]
                html = ""
                if is_alive(status, err):
                    n_recheck_ok += 1
        prev = last_probe(conn, l["id"])
        changed = 0
        if prev:
            if (prev["http_status"] != status or prev["final_url"] != final_url
                    or (title and prev["page_title"] and prev["page_title"] != title)):
                changed = 1
        conn.execute(
            "INSERT INTO probe (link_id,checked_at,http_status,final_url,page_title,changed,error) "
            "VALUES (?,?,?,?,?,?,?)",
            (l["id"], NOW, status, final_url, title, changed, err))
        # 生死を link.status に確定。生存なら last_checked を更新（中核機能）。
        alive = is_alive(status, err)
        if alive:
            new_status = "ok"
        elif is_inconclusive(url, status, err) or (
                is_transient(status, err) and not failed_before(prev, url)):
            # 判定できない／一時的な失敗が1回目：リンク切れにはしない。最終確認日は更新しない。
            new_status = "ok" if l["status"] == "ok" else "unchecked"
        else:
            new_status = "dead"
        if alive:
            conn.execute("UPDATE link SET status=?, last_checked=? WHERE id=?",
                         (new_status, NOW[:10], l["id"]))
            if l["kind_code"] == "official" and html:
                sns_review = check_official_listing(conn, l["entity_code"], l["entity_name"], final_url, html)
                changed_rows.extend(sns_review)
                n_sns += 1
        else:
            conn.execute("UPDATE link SET status=? WHERE id=?", (new_status, l["id"]))
        if l["status"] == "unchecked":
            changed = 1  # 初回検証は必ずレビュー対象に載せる
        if changed or (status != 200):
            changed_rows.append({"checked_at": NOW, "entity_code": l["entity_code"],
                                 "entity_name": l["entity_name"], "kind": l["kind_code"],
                                 "prev_status": l["status"], "new_status": new_status,
                                 "link_id": l["id"], "url": url, "http_status": status,
                                 "final_url": final_url, "title": title or "",
                                 "error": err or "", "changed": changed})
        if i % 200 == 0:
            conn.commit()
            print(f"  {i}/{len(links)} …")
        time.sleep(DELAY_SEC)
    conn.commit()
    n_pruned = prune_history(conn)
    print(f"古い確認履歴を削除: {n_pruned} 行（1リンクにつき直近 {KEEP_HISTORY} 回分を保持）")

    # 月ごとのファイルに日々追記する（以前は毎回上書きで、その日の分しか残らなかった）。
    # reports/monthly-review-*.csv は git 管理（probe.yml がコミット）。月1回の目視確認に使う。
    os.makedirs(os.path.dirname(REVIEW_CSV), exist_ok=True)
    new_file = not os.path.exists(REVIEW_CSV)
    with open(REVIEW_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(changed_rows)
    conn.close()
    print(f"完了。確認 {len(links) - n_robots} 件 / robots.txt で不許可 {n_robots} 件 / "
          f"SNSの掲載照合 {n_sns} 団体 / 国内から再確認 {n_recheck} 件（うち生存 {n_recheck_ok} 件）。"
          f"要レビュー {len(changed_rows)} 行 → {REVIEW_CSV}")


if __name__ == "__main__":
    main()
