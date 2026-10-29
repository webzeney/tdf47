#!/usr/bin/env python3
"""crawl_links.py — 各団体の公式サイトから「入札・契約／職員採用／統計／総合計画／DX推進計画」のページを探す。

全国の一覧が無い項目は、公式サイトをたどって候補を見つける（設計：roadmap v2.0）。
たどる順番（1団体あたり最大 MAX_PAGES ページ）:
  1. トップページ
  2. サイトマップ（トップにあるリンク、無ければ /sitemap.html などの定番の場所）
  3. 行政情報の入口（「市政情報」「町政情報」「行政情報」などのリンク先）
見つけ方: リンクの文字（画像の alt を含む）を項目ごとの言葉で照合する。URL だけでは判定しない
（URL が番号だけのサイトが多いため）。個別記事より一覧ページ・同じサイト内のページを優先する。

結果は reports/link-candidates.csv（団体コード・項目・URL・リンク文字・見つけたページ・確度）に書く。
DB は変えない（取り込みは import_candidates で、確度の高いものだけを別途行う）。

マナー（設計書 13章）: robots.txt を守る／同じサイトへは 1.2 秒以上あける／User-Agent に連絡先。
★手元のPCでは実行しない。GitHub Actions（crawl.yml）から実行する。

実行: python3 src/crawl_links.py --codes 112232,011002   （指定した団体だけ）
      python3 src/crawl_links.py --sample 50              （規模を混ぜて50団体）
      python3 src/crawl_links.py --all [--offset N --limit M]
"""

from __future__ import annotations

import argparse
import csv
import html.parser
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urldefrag, urljoin, urlparse

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402
from probe import UA, allowed_by_robots  # noqa: E402

DELAY_SEC = 1.2
TIMEOUT = 20
MAX_BYTES = 800_000
MAX_PAGES = 6
OUT_CSV = os.path.join(ROOT, "reports", "link-candidates.csv")
HOPPO = {"016951", "016969", "016977", "016985", "016993", "017001"}

# 項目ごとの言葉（リンク文字に含まれるか）。先に書いたものほど強い。除外語を含むリンクは使わない。
KINDS = {
    "nyusatsu": {"label": "入札・契約",
                 "words": ["入札・契約", "入札契約", "入札・調達", "入札情報", "契約情報", "入札", "調達情報"],
                 "exclude": ["結果のお知らせ", "公告", "令和", "平成", "年度", "委員会", "議事", "監視"]},
    "saiyo": {"label": "職員採用",
              "words": ["職員採用", "職員募集", "採用情報", "職員の採用", "採用試験"],
              "exclude": ["令和", "年度", "会計年度", "任期付", "保育士", "看護", "組合", "協力隊", "教員"]},
    "toukei": {"label": "統計",
               "words": ["統計情報", "統計データ", "統計書", "統計", "数字で見る", "データで見る"],
               "exclude": ["人口動態統計月報", "令和", "平成"]},
    "sougou_keikaku": {"label": "総合計画",
                       "words": ["総合計画", "総合振興計画", "総合発展計画", "まちづくり計画"],
                       "exclude": ["実施計画の", "審議会", "パブリックコメント", "素案", "（案）", "意見", "福祉", "下水道",
                                   "交通", "防災", "環境", "教育", "観光", "都市計画", "緑", "住宅", "保健", "医療",
                                   "森林", "農業", "水道", "子ども", "子育て", "障害", "介護", "健康"]},
    "dx_keikaku": {"label": "DX推進計画",
                   "words": ["DX推進計画", "ＤＸ推進計画", "デジタル化推進計画", "DX推進方針", "ＤＸ推進方針",
                             "デジタル・トランスフォーメーション推進計画", "デジタル変革", "情報化推進計画",
                             "DX推進", "ＤＸ推進"],
                   "exclude": ["採用", "募集", "人材", "研修", "セミナー", "補助", "支援"]},
}
# --kinds で指定したときだけ探す項目（全国の一覧から入れた URL が切れていた・無かった団体の再調査用）
EXTRA_KINDS = {
    "kouhou": {"label": "広報誌",
               "words": ["広報誌", "広報紙", "広報誌・広報紙", "市報", "町報", "村報", "県報", "広報"],
               "exclude": ["令和", "平成", "年度", "月号", "号", "広聴", "報道", "記者", "課", "係", "室",
                           "お問い合わせ", "広告", "ラジオ", "テレビ", "動画", "SNS", "メール", "アプリ", "議会"]},
    "reiki": {"label": "例規集",
              "words": ["例規集", "例規類集", "例規", "条例・規則", "条例集", "条例・規則等"],
              "exclude": ["改正", "制定", "パブリック", "意見", "公布", "議会"]},
}
SITEMAP_WORDS = ["サイトマップ", "サイト・マップ", "site map", "sitemap"]
SITEMAP_PATHS = ["/sitemap.html", "/sitemap/", "/site_map.html", "/sitemap/index.html", "/site/sitemap.html"]
HUB_WORDS = ["市政情報", "町政情報", "村政情報", "区政情報", "県政情報", "道政情報", "府政情報", "都政情報",
             "行政情報", "市政", "町政", "村政", "区政", "行政・まちづくり", "市の情報", "町の情報", "村の情報",
             "事業者向け", "事業者の方", "ビジネス・産業", "しごと・産業"]
_DOC_LIKE = re.compile(r"/(docs?|news|topics|info|kiji|article)/[^/]*\d{6,}|/\d{8,}\.html?$|\.pdf$", re.I)


class _Anchors(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._a = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._a = [a["href"], []]
        elif tag == "img" and self._a is not None and a.get("alt"):
            self._a[1].append(a["alt"])

    def handle_data(self, data):
        if self._a is not None:
            self._a[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.links.append((re.sub(r"\s+", " ", "".join(self._a[1])).strip(), self._a[0]))
            self._a = None


def _decode(raw: bytes, header_cs: str | None) -> str:
    cands = []
    if header_cs:
        cands.append(header_cs)
    m = re.search(rb'<meta[^>]+charset=["\']?([\w\-]+)', raw[:4000], re.I)
    if m:
        cands.append(m.group(1).decode("ascii", "ignore"))
    cands += ["utf-8", "cp932", "euc_jp"]
    for cs in cands:
        cs = {"shift_jis": "cp932", "x-sjis": "cp932", "sjis": "cp932"}.get(cs.lower(), cs)
        try:
            return raw.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


_last_req = {}


def get(url: str):
    """(final_url, html) か None。robots.txt で断られたら None。同じホストへは間隔をあける。"""
    if not url.startswith(("http://", "https://")) or not allowed_by_robots(url):
        return None
    host = urlparse(url).hostname or ""
    wait = DELAY_SEC - (time.time() - _last_req.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_req[host] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype.lower():
                return None
            raw = r.read(MAX_BYTES)
            return r.geturl(), _decode(raw, r.headers.get_content_charset())
    except Exception:
        return None


def base_domain(host: str) -> str:
    h = (host or "").lower()
    for p in ("www.", "www1.", "www2.", "www3.", "www4.", "web."):
        if h.startswith(p):
            h = h[len(p):]
    return h


def same_site(u: str, site: str) -> bool:
    h = base_domain(urlparse(u).hostname or "")
    return h == site or h.endswith("." + site) or site.endswith("." + h)


def anchors(page_url: str, text: str):
    p = _Anchors()
    try:
        p.feed(text)
    except Exception:
        pass
    out = []
    for t, href in p.links:
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        u = urldefrag(urljoin(page_url, href.strip()))[0]
        u = re.sub(r"[?&]utm_[a-z]+=[^&]*", "", u).rstrip("?&")
        if u.startswith(("http://", "https://")):
            out.append((t, u))
    return out


def score(kind: str, text: str, url: str, site: str, depth: int) -> int:
    """リンクの確度（0 は対象外）。言葉の強さ＋同じサイト内＋一覧ページらしさ。"""
    k = KINDS[kind]
    if kind in EXTRA_KINDS and not same_site(url, site) and kind == "kouhou":
        return 0  # 広報誌は公式サイト内のページだけ（電子書籍サービスなどは除く）
    if not text or len(text) > 40:
        return 0
    if any(x in text for x in k["exclude"]):
        return 0
    if kind in ("dx_keikaku", "sougou_keikaku") and re.search(r"(課|室|係|部|センター)$", text):
        return 0  # 担当課のページは計画そのものではない
    if re.search(r"[?&](keyword|q|search)=|/search[/?]", url, re.I):
        return 0  # サイト内検索の結果ページは使わない
    s = 0
    for i, w in enumerate(k["words"]):
        if w.lower() in text.lower():
            s = 100 - i * 5
            break
    if not s:
        return 0
    if same_site(url, site):
        s += 20
    if _DOC_LIKE.search(url):
        s -= 30
    s -= depth * 5
    if len(text) <= len(k["words"][0]) + 4:
        s += 5  # 短い＝見出し・メニューらしい
    return s


def crawl(code: str, name: str, official: str):
    """1団体を調べて {kind: (score, url, text, found_on)}。"""
    best = {}
    pages_done = 0
    site = base_domain(urlparse(official).hostname or "")

    def scan(page_url, html_text, depth):
        links = anchors(page_url, html_text)
        for t, u in links:
            for kind in KINDS:
                s = score(kind, t, u, site, depth)
                if s and (kind not in best or s > best[kind][0]):
                    best[kind] = (s, u, t, page_url)
        return links

    top = get(official)
    if not top:
        return best, "公式サイトを取得できない"
    pages_done += 1
    top_url, top_html = top
    site = base_domain(urlparse(top_url).hostname or "") or site
    links = scan(top_url, top_html, 0)

    # 2. サイトマップ
    sm = [u for t, u in links if any(w in t.lower() for w in SITEMAP_WORDS) and same_site(u, site)]
    if not sm:
        root = f"{urlparse(top_url).scheme}://{urlparse(top_url).netloc}"
        sm = [root + p for p in SITEMAP_PATHS[:2]]
    for u in sm[:2]:
        if len(best) == len(KINDS) or pages_done >= MAX_PAGES:
            break
        r = get(u)
        pages_done += 1
        if r:
            scan(r[0], r[1], 1)
            break

    # 3. 行政情報の入口
    hubs, seen = [], set()
    for t, u in links:
        if any(w == t or (w in t and len(t) <= len(w) + 4) for w in HUB_WORDS) and same_site(u, site) and u not in seen:
            hubs.append(u)
            seen.add(u)
    for u in hubs:
        if len(best) == len(KINDS) or pages_done >= MAX_PAGES:
            break
        r = get(u)
        pages_done += 1
        if r:
            scan(r[0], r[1], 1)
    return best, ""


def targets(conn, args):
    rows = [dict(r) for r in conn.execute(
        "SELECT e.code, e.name, e.type_label, l.url FROM entity e JOIN link l ON l.entity_code=e.code "
        "AND l.kind_code='official' AND l.status IN ('ok','unchecked') AND l.url IS NOT NULL "
        "WHERE e.kind IN ('city','pref') ORDER BY e.code")]
    seen, uniq = set(), []
    for r in rows:
        if r["code"] in seen or r["code"] in HOPPO:
            continue
        seen.add(r["code"])
        uniq.append(r)
    if args.codes:
        want = set(args.codes.split(","))
        return [r for r in uniq if r["code"] in want]
    if args.sample:
        rnd = random.Random(20261028)
        groups = {}
        for r in uniq:
            groups.setdefault(r["type_label"] or "", []).append(r)
        out = []
        per = max(1, args.sample // max(1, len(groups)))
        for g in sorted(groups):
            out += rnd.sample(groups[g], min(per, len(groups[g])))
        return sorted(out[: args.sample], key=lambda r: r["code"])
    return uniq[args.offset: args.offset + args.limit if args.limit else None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT_CSV)
    ap.add_argument("--kinds", help="探す項目（カンマ区切り）。既定は入札・契約〜DX推進計画の5項目。kouhou / reiki も指定できる")
    args = ap.parse_args()
    if args.kinds:
        allk = {**KINDS, **EXTRA_KINDS}
        want = [k for k in args.kinds.split(",") if k]
        bad = [k for k in want if k not in allk]
        if bad:
            ap.error(f"不明な項目: {bad}")
        KINDS.clear()
        KINDS.update({k: allk[k] for k in want})
    if not (args.codes or args.sample or args.all):
        ap.error("--codes / --sample / --all のどれかを指定")
    conn = db.connect()
    ts = targets(conn, args)
    conn.close()
    print(f"対象 {len(ts)} 団体")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    new = not os.path.exists(args.out)
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["entity_code", "entity_name", "kind_code", "score", "url", "link_text", "found_on", "note"])
        for i, t in enumerate(ts, 1):
            best, note = crawl(t["code"], t["name"], t["url"])
            if note:
                w.writerow([t["code"], t["name"], "", 0, "", "", t["url"], note])
            for kind in KINDS:
                if kind in best:
                    s, u, txt, on = best[kind]
                    w.writerow([t["code"], t["name"], kind, s, u, txt, on, ""])
            f.flush()
            if i % 10 == 0:
                print(f"  {i}/{len(ts)}")
    print(f"完了 → {args.out}")


if __name__ == "__main__":
    main()
