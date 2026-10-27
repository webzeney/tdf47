#!/usr/bin/env python3
"""15_manual_links.py — 人手で確認したリンクを link テーブルに投入（source='manual'）。

全国リンク集（RILG 等）に載っていない・移転していた URL を、各自治体の公式サイト等で
確認したうえで src/ingest/manual_links.csv に1行ずつ記録し、このスクリプトで反映する。

manual_links.csv の列:
  entity_code … 団体コード6桁
  kind_code   … reiki など link_kind.code
  url         … 確認した URL
  evidence    … その URL を確認したページ（公式サイトの該当リンクなど）
  checked_on  … 確認日（YYYY-MM-DD）
  note        … 補足（任意）

冪等（UNIQUE(entity_code,kind_code,url) に INSERT OR IGNORE）。既存の種まき（06_reiki 等）も
INSERT OR IGNORE なので、再取り込みで手動分が消えることはない。
投入時は status='unchecked'（日次の probe.py が確認して ok にする）。

実行: python3 src/ingest/15_manual_links.py
"""

from __future__ import annotations

import csv
import datetime
import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import db  # noqa: E402

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual_links.csv")
TODAY = datetime.date.today().isoformat()


def main():
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    codes = {r["code"] for r in cur.execute("SELECT code FROM entity")}
    kinds = {r["code"] for r in cur.execute("SELECT code FROM link_kind")}
    ins = same = bad = 0
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code, kind, url = row["entity_code"].strip(), row["kind_code"].strip(), row["url"].strip()
            if code not in codes or kind not in kinds or not url.startswith(("http://", "https://")):
                print(f"  スキップ（不正な行）: {row}", file=sys.stderr)
                bad += 1
                continue
            cur.execute(
                "INSERT OR IGNORE INTO link (entity_code,kind_code,url,title,note,status,source,first_seen) "
                "VALUES (?,?,?,NULL,?,'unchecked','manual',?)",
                (code, kind, url, (row.get("note") or "").strip() or None, row.get("checked_on") or TODAY))
            if cur.rowcount:
                ins += 1
            else:
                # 同じURLが既にある。リンク切れ判定のままなら、人手で確認できたので未確認に戻す。
                cur.execute("UPDATE link SET status='unchecked' WHERE entity_code=? AND kind_code=? AND url=? "
                            "AND status='dead'", (code, kind, url))
                same += 1
    conn.commit()
    conn.close()
    print(f"手動リンク: 新規 {ins} / 既存 {same} / 不正 {bad}")


if __name__ == "__main__":
    main()
