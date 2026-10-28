#!/usr/bin/env python3
"""16_pubcomment.py — パブリックコメントのリンクを link テーブルに投入（source='pubcomment'）。

src/ingest/pubcomment_links.csv（列: entity_code, url）を取り込む。
冪等（UNIQUE(entity_code,kind_code,url) に INSERT OR IGNORE）。投入時は status='unchecked'。
以後は日次の probe.py が確認し、つながったものは ok（最終確認日つき）になる。
リンク切れになったものは、公式サイトで移転先を確認して manual_links.csv に記録する。

実行: python3 src/ingest/16_pubcomment.py
"""

from __future__ import annotations

import csv
import datetime
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import db  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pubcomment_links.csv")
TODAY = datetime.date.today().isoformat()


def main():
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    known = {r[0] for r in cur.execute("SELECT code FROM entity")}
    ins = same = bad = 0
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code, url = row["entity_code"].strip(), row["url"].strip()
            if code not in known or not url.startswith(("http://", "https://")):
                print(f"  スキップ（団体コードかURLが不正）: {code} {url}")
                bad += 1
                continue
            cur.execute(
                "INSERT OR IGNORE INTO link (entity_code,kind_code,url,title,note,status,source,first_seen) "
                "VALUES (?,?,?,NULL,NULL,'unchecked','pubcomment',?)",
                (code, "pubcomment", url, TODAY))
            if cur.rowcount:
                ins += 1
            else:
                same += 1
    conn.commit()
    conn.close()
    print(f"パブリックコメント: 新規 {ins} / 既存 {same} / 不正 {bad}")


if __name__ == "__main__":
    main()
