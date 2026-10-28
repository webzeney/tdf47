#!/usr/bin/env python3
"""18_crawl_import.py — 公式サイトから見つけた項目ページの候補（reports/link-candidates-*.csv）を link に入れる。

対象: 入札・契約／職員採用／統計／総合計画／DX推進計画（crawl_links.py の結果）。
確度（score）が MIN_SCORE 以上の候補だけを status='unchecked'・source='crawl' で入れる。
同じ団体・同じ項目に、すでに表示中のリンク（ok / unchecked）があるときは入れない（人手で入れたものを優先）。
同じ団体・項目の候補が複数の CSV にあるときは、新しい CSV のものを使う。
以後は日次の probe.py が確認する。

実行: python3 src/ingest/18_crawl_import.py [--min-score 110] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
import os
import re
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

KINDS = ("nyusatsu", "saiyo", "toukei", "sougou_keikaku", "dx_keikaku")
TODAY = datetime.date.today().isoformat()
# 候補の文字にこれが入っていたら使わない（意見募集中の案など、計画そのものではない）
SKIP_TEXT = re.compile(r"パブリックコメント|意見募集|（案）|\(案\)|素案")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=110)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    best = {}
    for f in sorted(glob.glob(os.path.join(ROOT, "reports", "link-candidates-*.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["kind_code"] not in KINDS or not r["url"]:
                    continue
                best[(r["entity_code"], r["kind_code"])] = r  # 新しい CSV で上書き

    conn = db.connect()
    db.init_schema(conn)
    has = {(r[0], r[1]) for r in conn.execute(
        "SELECT entity_code, kind_code FROM link WHERE status IN ('ok','unchecked') AND url IS NOT NULL")}
    ins = skip_low = skip_has = skip_text = 0
    for (code, kind), r in sorted(best.items()):
        if int(r["score"]) < args.min_score:
            skip_low += 1
            continue
        if kind in ("sougou_keikaku", "dx_keikaku") and SKIP_TEXT.search(r["link_text"]):
            skip_text += 1
            continue
        if (code, kind) in has:
            skip_has += 1
            continue
        if not args.dry_run:
            cur = conn.execute(
                "INSERT OR IGNORE INTO link (entity_code,kind_code,url,title,note,status,source,first_seen) "
                "VALUES (?,?,?,NULL,?,'unchecked','crawl',?)",
                (code, kind, r["url"], f"見つけたページ: {r['found_on']}", TODAY))
            ins += cur.rowcount
        else:
            ins += 1
    if not args.dry_run:
        conn.commit()
    conn.close()
    print(f"候補の取り込み: 新規 {ins} / 確度不足 {skip_low} / 案・お知らせ {skip_text} / 既にあり {skip_has}"
          + ("（試行のみ）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
