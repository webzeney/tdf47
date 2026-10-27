#!/usr/bin/env python3
"""03_area.py — 面積を entity に投入（設計書 データ取得順4 / 国土地理院 面積調）。

国土地理院 全国都道府県市区町村別面積調 data/gsi/mencho.csv を取り込む。
標準地域コード（5桁）が entity.code の先頭5桁と一致する。最新列（令和8年7月1日）の面積を使う。

列: 0=標準地域コード / 1=都道府県 / 2=郡等 / 3=市区町村 / 4=最新面積(k㎡) / 5=備考 ...
実行: python3 src/ingest/03_area.py
"""

from __future__ import annotations

import csv
import datetime
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

CSVPATH = os.path.join(ROOT, "data", "gsi", "mencho.csv")
AS_OF = "2026-07-01"  # 令和8年7月1日
NOW = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def main():
    with open(CSVPATH, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    # ヘッダは4行目(index4)、データはそれ以降
    area_by5 = {}
    for r in rows[5:]:
        if len(r) < 5:
            continue
        code = (r[0] or "").strip()
        if not code.isdigit() or len(code) > 5:
            continue
        code = code.zfill(5)  # 北海道等は先頭0が落ちて4桁になっている
        val = (r[4] or "").replace(",", "").strip()
        try:
            area = float(val)
        except ValueError:
            continue
        area_by5[code] = area

    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute("SELECT code,area_km2,area_as_of FROM entity")]
    upd = same = miss = 0
    matched_codes = set()
    for e in ents:
        a = area_by5.get(e["code"][:5])
        if a is None:
            miss += 1
            continue
        matched_codes.add(e["code"][:5])
        if e["area_km2"] == a and e["area_as_of"] == AS_OF:
            same += 1
            continue
        cur.execute("UPDATE entity SET area_km2=?, area_as_of=?, updated_at=? WHERE code=?",
                    (a, AS_OF, NOW, e["code"]))
        upd += 1
    conn.commit()
    filled = cur.execute("SELECT count(*) FROM entity WHERE area_km2 IS NOT NULL").fetchone()[0]
    conn.close()
    print(f"面積: 更新 {upd} / 変化なし {same} / 面積データに無い entity {miss}")
    print(f"面積が入っている entity: {filled}（CSV側で未使用コード {len(area_by5)-len(matched_codes)}）")


if __name__ == "__main__":
    main()
