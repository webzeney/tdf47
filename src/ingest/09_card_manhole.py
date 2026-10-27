#!/usr/bin/env python3
"""09_card_manhole.py — マンホールカードを card テーブルへ投入（設計書 データ取得順10 / GKP）。

GKP「マンホールカード検索」の都道府県別ページ(data/gkp/prefNN.html, NN=JIS都道府県コード)を取り込む。
各ページの <table class="table1 cr"> が 市町村/カード画像/弾数/発行年月日/配布場所/配布時間/在庫 を持つ。

★画像は設計書12章により image_url=NULL のまま（GKPへの利用照会が済むまで掲載しない）。
配布自治体名を entity に突き合わせ、市町村カードのみ格納（事務組合等は対象外→reportへ）。冪等。
実行: python3 src/ingest/09_card_manhole.py
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

GKP_DIR = os.path.join(ROOT, "data", "gkp")
UNMATCHED_CSV = os.path.join(ROOT, "reports", "card-manhole-unmatched.csv")
TODAY = datetime.date.today().isoformat()

_TABLE_RE = re.compile(r'<table class="table1 cr">(.*?)</table>', re.S)
_TR_RE = re.compile(r'<tr>(.*?)</tr>', re.S)
_TD_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)
_IMG_RE = re.compile(r'/mhc/([^"/]+)\.(?:jpg|jpeg|png)', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)


def strip_tags(s):
    return re.sub(r"\s+", " ", _TAG_RE.sub("", s)).strip()


def norm_name(s):
    return s.replace("ヶ", "ケ").replace("ヵ", "カ").replace("　", "").strip()


def to_date(s):
    m = re.search(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def stock_of(s):
    s = strip_tags(s)
    if any(k in s for k in ("終了", "配布中止", "休止", "なし")):
        return "ended"
    if any(k in s for k in ("わずか", "少", "残り")):
        return "low"
    return "ok"


def parse_page(html):
    """[(muni_name, card_no, series, issued, place_name, place_addr, hours, stock), ...]"""
    out = []
    tm = _TABLE_RE.search(html)
    if not tm:
        return out
    for tr in _TR_RE.findall(tm.group(1)):
        tds = _TD_RE.findall(tr)
        if len(tds) != 7:
            continue  # ヘッダ行や変則行はスキップ
        raw = strip_tags(tds[0])
        # 「札幌市（A001）」「東京都 千代田区 (F001)」等 → 基礎名とカード記号を分離
        suffix_m = re.search(r"[（(]\s*([A-Za-z]\d{3})\s*[）)]", raw)
        suffix = suffix_m.group(1) if suffix_m else ""
        base = re.split(r"[（(]", raw, 1)[0].strip()
        if " " in base or "　" in base:
            base = re.split(r"[ 　]+", base)[-1]  # 「東京都 千代田区」→「千代田区」
        muni = base
        card_no = (base + suffix) if suffix else base
        series = strip_tags(tds[2])
        issued = to_date(tds[3])
        place_cell = _BR_RE.split(tds[4], 1)
        place_name = strip_tags(place_cell[0])
        place_addr = strip_tags(place_cell[1]) if len(place_cell) > 1 else None
        hours = strip_tags(tds[5])
        stock = stock_of(tds[6])
        if muni:
            out.append((muni, card_no, series, issued, place_name, place_addr, hours, stock))
    return out


def main():
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute("SELECT code,name,kind FROM entity")]
    by_pref_name = {}
    for e in ents:
        by_pref_name.setdefault(e["code"][:2], {}).setdefault(norm_name(e["name"]), []).append(e)

    cur.execute("DELETE FROM card WHERE kind='manhole'")
    matched = 0
    unmatched = []
    files = sorted(f for f in os.listdir(GKP_DIR) if re.match(r"pref\d{2}\.html$", f))
    for fn in files:
        pref2 = fn[4:6]
        html = open(os.path.join(GKP_DIR, fn), encoding="utf-8").read()
        for muni, card_no, series, issued, pname, paddr, hours, stock in parse_page(html):
            cands = by_pref_name.get(pref2, {}).get(norm_name(muni), [])
            ent = next((e for e in cands if e["kind"] in ("city", "ward")), None)
            if not ent:
                unmatched.append({"pref2": pref2, "muni": muni, "card_no": card_no or ""})
                continue
            cur.execute(
                "INSERT INTO card (kind,card_no,entity_code,series,issued_on,place_name,"
                "place_addr,place_hours,stock,image_url,source_url,last_checked) "
                "VALUES ('manhole',?,?,?,?,?,?,?,?,NULL,?,?)",
                (card_no, ent["code"], series, issued, pname, paddr, hours, stock,
                 "https://www.gk-p.jp/mhcard/", TODAY))
            matched += 1
    conn.commit()
    total = cur.execute("SELECT count(*) FROM card WHERE kind='manhole'").fetchone()[0]
    ent_cnt = cur.execute("SELECT count(DISTINCT entity_code) FROM card WHERE kind='manhole'").fetchone()[0]
    conn.close()

    os.makedirs(os.path.dirname(UNMATCHED_CSV), exist_ok=True)
    with open(UNMATCHED_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pref2", "muni", "card_no"])
        w.writeheader()
        w.writerows(unmatched)
    print(f"マンホールカード: 投入 {total} 枚 / {ent_cnt} 自治体 / 未マッチ {len(unmatched)}（組合等）")
    print(f"画像は image_url=NULL（GKP利用照会前）。未マッチ: {UNMATCHED_CSV}")


if __name__ == "__main__":
    main()
