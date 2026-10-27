#!/usr/bin/env python3
"""06_reiki.py — 例規集リンクの種まき（設計書 データ取得順7 / RILG）。

RILG「全国自治体例規集」(data/rilg/zenkoku_link.html) は1ページに全自治体の
例規集URLを直リンクで持つ。これを初回の種まきとして取り込む。

重要: 取り込み時は status='unchecked'（まだ生死を検証していない）。
VPS上の probe.py が検証して status='ok'/'dead' と last_checked を立てる。
→ 「最終確認日」は自前の死活監視が根拠。RILGに載っていた事実と混同しない。

名称は都道府県セクション(<a name="NN">)ごとに区切ってマッチし、県をまたぐ同名衝突を防ぐ。
冪等（UNIQUE(entity_code,kind_code,url) に INSERT OR IGNORE、first_seen は初回のみ）。

実行: python3 src/ingest/06_reiki.py
※ ファイル取得（ダウンロード）は集約サイトの索引1枚のみ。自治体サイトのクロールではない。
"""

from __future__ import annotations

import csv
import datetime
import html
import os
import re
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

RILG_HTML = os.path.join(ROOT, "data", "rilg", "zenkoku_link.html")
UNMATCHED_CSV = os.path.join(ROOT, "reports", "reiki-unmatched.csv")
TODAY = datetime.date.today().isoformat()
KIND = "reiki"
SOURCE = "rilg"

_SECTION_RE = re.compile(r'<a name="(\d{2})">')
# protocol-relative(//...) も拾う。RILGは我孫子市などで //host/ 形式を使う。
_LINK_RE = re.compile(r'<a href="((?:https?:)?//[^"]+)"[^>]*>([^<]+)</a>')


def norm_name(s: str) -> str:
    """名称マッチ用の正規化。ヶ/ケ・ヵ/カ の表記ゆれと空白を吸収する。"""
    return s.replace("ヶ", "ケ").replace("ヵ", "カ").replace("　", "").strip()


def norm_url(url: str) -> str:
    url = html.unescape(url)  # href内の &amp; 等のHTMLエンティティを素のURLに戻す
    return "https:" + url if url.startswith("//") else url


def parse_rilg(html):
    """都道府県2桁コード → [(name, url), ...] を返す。"""
    marks = [(m.group(1), m.start()) for m in _SECTION_RE.finditer(html)]
    out = {}
    for i, (pref2, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(html)
        chunk = html[start:end]
        links = []
        for url, text in _LINK_RE.findall(chunk):
            name = text.strip()
            if name:
                links.append((name, norm_url(url)))
        out[pref2] = links
    return out


def main():
    if not os.path.exists(RILG_HTML):
        sys.exit(f"RILGデータが無い: {RILG_HTML}（先にダウンロード）")
    html = open(RILG_HTML, encoding="utf-8").read()
    sections = parse_rilg(html)

    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute(
        "SELECT code,name,kind,pref_code FROM entity")]
    # 都道府県2桁 → {正規化名: [entity,...]}
    by_pref_name = {}
    for e in ents:
        by_pref_name.setdefault(e["code"][:2], {}).setdefault(norm_name(e["name"]), []).append(e)

    matched = 0
    unmatched = []
    used = set()  # 同名重複対策（entity.code を一度だけ使う）
    for pref2, links in sections.items():
        name_map = by_pref_name.get(pref2, {})
        for name, url in links:
            key = norm_name(name)
            cands = [e for e in name_map.get(key, []) if e["code"] not in used]
            if not cands:
                # 既出(同名で使い切った)か、未マッチ
                if key not in name_map:
                    unmatched.append({"pref2": pref2, "name": name, "url": url})
                continue
            e = cands[0]
            used.add(e["code"])
            cur.execute(
                "INSERT OR IGNORE INTO link "
                "(entity_code,kind_code,url,title,status,source,first_seen,last_checked) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (e["code"], KIND, url, None, "unchecked", SOURCE, TODAY, None))
            matched += 1
    conn.commit()
    total_links = sum(len(v) for v in sections.values())
    conn.close()

    os.makedirs(os.path.dirname(UNMATCHED_CSV), exist_ok=True)
    with open(UNMATCHED_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pref2", "name", "url"])
        w.writeheader()
        w.writerows(unmatched)
    print(f"RILG例規集: 全{total_links}リンク中 マッチ {matched} / 未マッチ {len(unmatched)}")
    print(f"未マッチ一覧: {UNMATCHED_CSV}")
    print("status=unchecked で投入。VPSの probe.py が検証して ok/dead と最終確認日を立てる。")


if __name__ == "__main__":
    main()
