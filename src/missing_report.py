#!/usr/bin/env python3
"""missing_report.py — 掲載できていない項目（リンクが無い・切れたまま）の一覧と、毎月の探し直しの結果。

  --codes          掲載できていない項目がある団体のコードをカンマ区切りで出す（月1回の探し直しの対象）
  --report CSV...  探し直しの結果（crawl_links.py の出力）と突き合わせ、
                   reports/missing-candidates-YYYYMM.csv に「掲載できていない項目と、見つかった候補」を書く

候補は自動では載せない。月1回の目視確認で確かめてから manual_links.csv に入れる。
行政区は公式サイトの URL を持たないため、ここでは対象外（区の広報誌は個別に確認する）。
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import sys

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

KINDS = ("reiki", "kouhou", "pubcomment", "nyusatsu", "saiyo", "toukei", "sougou_keikaku", "dx_keikaku")
HOPPO = {"016951", "016969", "016977", "016985", "016993", "017001"}


def missing(conn):
    """[(code, name, kind)]：表示中のリンク（ok / unchecked）が無い項目。"""
    has = {(r[0], r[1]) for r in conn.execute(
        "SELECT entity_code, kind_code FROM link WHERE status IN ('ok','unchecked') AND url IS NOT NULL")}
    out = []
    for code, name in conn.execute("SELECT code, name FROM entity WHERE kind IN ('city','pref') ORDER BY code"):
        if code in HOPPO:
            continue
        for k in KINDS:
            if (code, k) not in has:
                out.append((code, name, k))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", action="store_true")
    ap.add_argument("--report", nargs="+")
    args = ap.parse_args()
    conn = db.connect()
    miss = missing(conn)
    conn.close()
    if args.codes:
        print(",".join(sorted({c for c, _, _ in miss})))
        return
    if not args.report:
        ap.error("--codes か --report を指定")
    cand = {}
    for f in args.report:
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["kind_code"] and r["url"]:
                    cand[(r["entity_code"], r["kind_code"])] = r
    month = datetime.date.today().strftime("%Y%m")
    out = os.path.join(ROOT, "reports", f"missing-candidates-{month}.csv")
    found = 0
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["entity_code", "entity_name", "kind_code", "candidate_url", "link_text", "found_on", "score"])
        for code, name, k in miss:
            c = cand.get((code, k))
            if c:
                found += 1
            w.writerow([code, name, k, c["url"] if c else "", c["link_text"] if c else "",
                        c["found_on"] if c else "", c["score"] if c else ""])
    print(f"掲載できていない項目 {len(miss)} 件のうち、候補が見つかったもの {found} 件 → {out}")


if __name__ == "__main__":
    main()
