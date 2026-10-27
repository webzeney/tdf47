#!/usr/bin/env python3
"""08_uub.py — 公式サイト・広報誌・公式SNSの種まき（設計書 データ取得順9 / uub.jp）。

uub.jp「公式メディア一覧」の都道府県別ページ(data/uub/<pref>.html)を取り込む。
各ページは1行=1自治体で、公式HP/広報紙/X/Facebook/Instagram/YouTube/LINE の列を持つ。

★出典の明記（設計書 12章）: uub.jp は個人が長年かけて集めた成果。公開ページ(/about/sources/)
  で目立つ形でクレジットし、相互リンクを申し出る方針。この取り込みは初回の種まきのみ。

status='unchecked' で投入 → VPSの probe.py が検証して ok/dead と最終確認日を立てる。冪等。
実行: python3 src/ingest/08_uub.py
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

UUB_DIR = os.path.join(ROOT, "data", "uub")
UNMATCHED_CSV = os.path.join(ROOT, "reports", "uub-unmatched.csv")
TODAY = datetime.date.today().isoformat()
SOURCE = "uub"

LABEL_TO_KIND = {
    "公式HP": "official",
    "広報紙": "kouhou",
    "X": "sns_x",
    "Facebook": "sns_facebook",
    "Instagram": "sns_instagram",
    "YouTube": "sns_youtube",
    "LINE": "sns_line",
}

_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_NAME_RE = re.compile(r'<th scope="row">(.*?)</th>', re.S)
_CELL_RE = re.compile(r'<td data-label="([^"]+)"[^>]*>(.*?)</td>', re.S)
_HREF_RE = re.compile(r'href="([^"]+)"')
_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s):
    return _TAG_RE.sub("", s).strip()


def norm_name(s):
    return s.replace("ヶ", "ケ").replace("ヵ", "カ").replace("　", "").strip()


def parse_page(html):
    """[(name, kind_code, url), ...] を返す。"""
    out = []
    for row in _ROW_RE.findall(html):
        nm = _NAME_RE.search(row)
        if not nm:
            continue
        name = strip_tags(nm.group(1))
        if not name:
            continue
        for label, cell in _CELL_RE.findall(row):
            kind = LABEL_TO_KIND.get(label)
            if not kind:
                continue
            href = _HREF_RE.search(cell)
            if href:
                out.append((name, kind, href.group(1).strip()))
    return out


def main():
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute("SELECT code,name,slug,kind FROM entity")]
    pref_slug_to2 = {e["slug"]: e["code"][:2] for e in ents if e["kind"] == "pref"}
    by_pref_name = {}
    for e in ents:
        by_pref_name.setdefault(e["code"][:2], {}).setdefault(norm_name(e["name"]), []).append(e)

    matched = 0
    per_kind = {}
    unmatched = []
    files = sorted(f for f in os.listdir(UUB_DIR) if f.endswith(".html") and not f.startswith("_"))
    for fn in files:
        slug = fn[:-5]
        pref2 = pref_slug_to2.get(slug)
        if pref2 is None:
            continue
        html = open(os.path.join(UUB_DIR, fn), encoding="utf-8").read()
        used = set()  # (code, kind) を一度だけ
        for name, kind, url in parse_page(html):
            key = norm_name(name)
            cands = [e for e in by_pref_name.get(pref2, {}).get(key, [])
                     if (e["code"], kind) not in used]
            if not cands:
                if key not in by_pref_name.get(pref2, {}):
                    unmatched.append({"pref": slug, "name": name, "kind": kind, "url": url})
                continue
            e = cands[0]
            used.add((e["code"], kind))
            cur.execute(
                "INSERT OR IGNORE INTO link "
                "(entity_code,kind_code,url,title,status,source,first_seen,last_checked) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (e["code"], kind, url, None, "unchecked", SOURCE, TODAY, None))
            if cur.rowcount:
                matched += 1
                per_kind[kind] = per_kind.get(kind, 0) + 1
    conn.commit()
    conn.close()

    os.makedirs(os.path.dirname(UNMATCHED_CSV), exist_ok=True)
    with open(UNMATCHED_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pref", "name", "kind", "url"])
        w.writeheader()
        w.writerows(unmatched)
    print(f"uub公式メディア: 投入 {matched} 件 / 未マッチ {len(unmatched)}")
    for k in sorted(per_kind):
        print(f"  {k}: {per_kind[k]}")
    print(f"未マッチ一覧: {UNMATCHED_CSV}")


if __name__ == "__main__":
    main()
