#!/usr/bin/env python3
"""10_card_dam.py — ダムカードを card テーブルへ投入（設計書 データ取得順11 / 国土交通省）。

国土交通省「ダムカード配布場所一覧」data/mlit/damcard.xlsx を取り込む。
各行＝1ダム。配布場所の住所から所在市町村を割り出し、entity に紐づける。

列(0始まり): 0番号 1水系 2河川 3ダム名 4ver 5配布場所 6配布日時 7県名 8住所 9URL
画像は設計書どおり image_url=NULL（統一デザイン以外は収録しない方針でもあり、まずは情報のみ）。冪等。
実行: python3 src/ingest/10_card_dam.py
"""

from __future__ import annotations

import csv
import datetime
import os
import re
import sys

import openpyxl

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

XLSX = os.path.join(ROOT, "data", "mlit", "damcard.xlsx")
UNMATCHED_CSV = os.path.join(ROOT, "reports", "card-dam-unmatched.csv")
TODAY = datetime.date.today().isoformat()


def first_line(s):
    for line in str(s or "").split("\n"):
        line = line.strip().lstrip("①②③④⑤⑥⑦⑧⑨⑩ 　")
        if line:
            return line
    return ""


def first_addr_line(s):
    for line in str(s or "").split("\n"):
        line = line.strip().lstrip("①②③④⑤⑥⑦⑧⑨⑩ 　")
        if re.search(r"[市区町村]", line):
            return line
    return first_line(s)


def main():
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [r for r in ws.iter_rows(min_row=4, values_only=True)]
    wb.close()

    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute("SELECT code,name,kind FROM entity")]
    pref_by_name = {e["name"]: e["code"][:2] for e in ents if e["kind"] == "pref"}
    # 都道府県2桁 → [(name, entity)] 名前長い順（最長一致用）
    names_by_pref = {}
    for e in ents:
        if e["kind"] in ("city", "ward"):
            names_by_pref.setdefault(e["code"][:2], []).append(e)
    for lst in names_by_pref.values():
        lst.sort(key=lambda e: len(e["name"]), reverse=True)

    cur.execute("DELETE FROM card WHERE kind='dam'")
    matched = 0
    unmatched = []
    for r in rows:
        if not r or not r[3]:  # ダム名なし
            continue
        dam = str(r[3]).strip()
        place = first_line(r[5])
        hours = first_line(r[6])
        pref_name = str(r[7] or "").strip()
        addr_line = first_addr_line(r[8])
        url = first_line(r[9])
        pref2 = pref_by_name.get(pref_name)
        if not pref2:
            unmatched.append({"pref": pref_name, "dam": dam, "addr": addr_line})
            continue
        stripped = addr_line
        if stripped.startswith(pref_name):          # 都道府県名を除去
            stripped = stripped[len(pref_name):]
        stripped = re.sub(r"^\s*.{1,8}?郡", "", stripped)  # 郡を除去
        ent = next((e for e in names_by_pref.get(pref2, []) if stripped.startswith(e["name"])), None)
        if not ent:
            unmatched.append({"pref": pref_name, "dam": dam, "addr": addr_line})
            continue
        cur.execute(
            "INSERT INTO card (kind,card_no,entity_code,series,issued_on,place_name,"
            "place_addr,place_hours,stock,image_url,source_url,last_checked) "
            "VALUES ('dam',?,?,NULL,NULL,?,?,?,'ok',NULL,?,?)",
            (dam, ent["code"], place, addr_line, hours, url or
             "https://www.mlit.go.jp/river/kankyo/campaign/shunnkan/damcard.html", TODAY))
        matched += 1
    conn.commit()
    total = cur.execute("SELECT count(*) FROM card WHERE kind='dam'").fetchone()[0]
    ent_cnt = cur.execute("SELECT count(DISTINCT entity_code) FROM card WHERE kind='dam'").fetchone()[0]
    conn.close()

    os.makedirs(os.path.dirname(UNMATCHED_CSV), exist_ok=True)
    with open(UNMATCHED_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pref", "dam", "addr"])
        w.writeheader()
        w.writerows(unmatched)
    print(f"ダムカード: 投入 {total} 枚 / {ent_cnt} 自治体 / 未マッチ {len(unmatched)}")
    print(f"未マッチ一覧: {UNMATCHED_CSV}")


if __name__ == "__main__":
    main()
