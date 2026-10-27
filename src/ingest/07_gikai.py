#!/usr/bin/env python3
"""07_gikai.py — 議会会議録リンクの種まき（設計書 データ取得順8 / app-mints）。

app-mints「全国自治体の会議録検索MAP」の都道府県別ページ(data/mints/<pref>.html)を取り込む。
各ページは市区町村ごとに <div class="lg-list-item"> ... <h2>○○議会</h2> ... 会議録URL（PC版/スマホ版）。
北海道は hokkaido1〜4（地域別）に分かれているので4ファイルとも 北海道(01) に突き合わせる。

status='unchecked' で投入 → VPSの probe.py が検証。PC版(MinuteSearch)を優先採用する。冪等。
実行: python3 src/ingest/07_gikai.py
"""

from __future__ import annotations

import csv
import datetime
import os
import re
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

MINTS_DIR = os.path.join(ROOT, "data", "mints")
UNMATCHED_CSV = os.path.join(ROOT, "reports", "gikai-unmatched.csv")
TODAY = datetime.date.today().isoformat()
KIND = "gikai"
SOURCE = "app-mints"

_TITLE_RE = re.compile(r'lg-list-item-title[^>]*>(.*?)</h2>', re.S)
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s):
    return _TAG_RE.sub("", s).strip()


def norm_name(s):
    return s.replace("ヶ", "ケ").replace("ヵ", "カ").replace("　", "").strip()


def best_url(urls):
    """PC版(MinuteSearch, Spを含まない)を優先。無ければ最初の外部URL。"""
    ext = [u for u in urls if "app-mints.com" not in u]
    if not ext:
        return None
    pc = [u for u in ext if "MinuteSearch" in u and "SpMinute" not in u]
    return pc[0] if pc else ext[0]


def parse_page(html):
    """[(name, url, state), ...] を返す。state は 'ok_seed'（URLあり）/ 'none'（提供なし）。

    見出し(議会名)の位置で区切り、次の見出しまでをブロックとする。
    app-mints は会議録検索が無い自治体を「提供なし」と明記しており、これを none として扱う。
    """
    titles = list(_TITLE_RE.finditer(html))
    out = []
    for i, tm in enumerate(titles):
        title = strip_tags(tm.group(1))
        name = re.sub(r"議会$", "", title).strip()
        if not name:
            continue
        start = tm.end()
        end = titles[i + 1].start() if i + 1 < len(titles) else len(html)
        block = html[start:end]
        url = best_url(_HREF_RE.findall(block))
        if url:
            out.append((name, url, "ok_seed"))
        elif "提供なし" in block:
            out.append((name, None, "none"))
    return out


def main():
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute("SELECT code,name,slug,kind FROM entity")]
    slug_to2 = {e["slug"]: e["code"][:2] for e in ents if e["kind"] == "pref"}
    by_pref_name = {}
    for e in ents:
        by_pref_name.setdefault(e["code"][:2], {}).setdefault(norm_name(e["name"]), []).append(e)

    matched = 0
    none_cnt = 0
    unmatched = []
    files = sorted(f for f in os.listdir(MINTS_DIR) if f.endswith(".html"))
    for fn in files:
        stem = fn[:-5]
        if stem.startswith("hokkaido"):
            pref2 = "01"
        else:
            pref2 = slug_to2.get(stem)
        if pref2 is None:
            continue
        html = open(os.path.join(MINTS_DIR, fn), encoding="utf-8").read()
        used = set()
        for name, url, state in parse_page(html):
            key = norm_name(name)
            cands = [e for e in by_pref_name.get(pref2, {}).get(key, []) if e["code"] not in used]
            if not cands:
                if key not in by_pref_name.get(pref2, {}):
                    unmatched.append({"pref": stem, "name": name, "url": url or ""})
                continue
            e = cands[0]
            used.add(e["code"])
            status = "unchecked" if state == "ok_seed" else "none"
            cur.execute(
                "INSERT OR IGNORE INTO link "
                "(entity_code,kind_code,url,title,status,source,first_seen,last_checked) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (e["code"], KIND, url, None, status, SOURCE, TODAY, None))
            if cur.rowcount:
                if state == "ok_seed":
                    matched += 1
                else:
                    none_cnt += 1
    conn.commit()
    conn.close()

    os.makedirs(os.path.dirname(UNMATCHED_CSV), exist_ok=True)
    with open(UNMATCHED_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pref", "name", "url"])
        w.writeheader()
        w.writerows(unmatched)
    print(f"app-mints議会: リンクあり {matched} 件 / 提供なし(none) {none_cnt} 件 / 未マッチ {len(unmatched)}")
    print(f"未マッチ一覧: {UNMATCHED_CSV}")


if __name__ == "__main__":
    main()
