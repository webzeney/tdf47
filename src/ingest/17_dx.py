#!/usr/bin/env python3
"""17_dx.py — 自治体DXの取組状況を取り込む（デジタル庁「自治体DXの取組に関するダッシュボード」）。

ダッシュボードのページにある「データテーブル（ZIP）」を取得し、
  - 市区町村毎のDX進捗状況_市区町村比較.csv
  - 都道府県のDX進捗状況_都道府県比較.csv
を dx_status テーブルに入れる。

CSV は「行＝指標、列＝団体」の横持ち。列の団体は団体名だけで団体コードが無いため、
団体コード順に並んでいることを利用して、こちらの団体一覧（コード順）と1列ずつ突き合わせる。
名前が1つでも合わなければ取り込まずに終了する（取り違えを防ぐため）。

ZIP のファイル名は更新のたびに変わるため、ページ内のリンクから解決する。
★手元のPCでは実行しない。GitHub Actions（soumu.yml）から実行する。

実行: python3 src/ingest/17_dx.py            （ページから最新の ZIP を取得）
      python3 src/ingest/17_dx.py --zip FILE （手元の ZIP を使う。テスト用）
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import db  # noqa: E402

PAGE = "https://www.digital.go.jp/resources/govdashboard/local-government-dx"
UA = "tdf47.com-bot/1.0 (+https://tdf47.com/about/; open data fetcher; contact: https://tdf47.com/contact/)"
NOW = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

# CSV の指標名 → metric キー（表示順もこの順）
METRICS = [
    ("CIOの任命", "cio"),
    ("CIO補佐官等の任命", "cio_hosa"),
    ("全体方針策定", "houshin"),
    ("全庁的な体制構築", "taisei"),
    ("外部人材活用", "gaibu"),
    ("職員育成の取組", "ikusei"),
    ("全職員対象研修の実施", "kenshu"),
    ("AIの導入状況", "ai"),
    ("RPAの導入状況", "rpa"),
    ("テレワークの導入状況", "telework"),
    ("マイナンバーカードの保有状況", "mynumber"),
    ("子育て・介護26手続のオンライン化状況", "online26"),
    ("よく使う32手続のオンライン化状況", "online32"),
]
VALID = re.compile(r"^(実施|未実施|調査無し|\d{1,3}%)$")
HOPPO = {"016951", "016969", "016977", "016985", "016993", "017001"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def find_zip_url() -> str:
    html = _get(PAGE).decode("utf-8", errors="replace")
    m = re.findall(r'href="([^"]*local_governmentdx_table[^"]*\.zip)"', html)
    if not m:
        sys.exit("データテーブル（ZIP）のリンクがページに見つからない。ページの構成が変わった可能性がある")
    time.sleep(1.2)
    return urllib.parse.urljoin(PAGE, m[0])


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    sys.exit("CSV の文字コードが判別できない")


def read_csv(zf: zipfile.ZipFile, suffix: str):
    names = [n for n in zf.namelist() if n.endswith(suffix)]
    if len(names) != 1:
        # ZIP 内のファイル名が cp437 として読まれている場合に備えて読み直す
        fixed = {}
        for n in zf.namelist():
            try:
                fixed[n.encode("cp437").decode("cp932")] = n
            except (UnicodeEncodeError, UnicodeDecodeError):
                fixed[n] = n
        names = [orig for good, orig in fixed.items() if good.endswith(suffix)]
    if len(names) != 1:
        sys.exit(f"ZIP に {suffix} が見つからない: {zf.namelist()}")
    rows = list(csv.reader(io.StringIO(_decode(zf.read(names[0])))))
    return [r for r in rows if any(c.strip() for c in r)]


def norm(name: str) -> str:
    """表記ゆれ（ケ/ヶ、旧字体など）を吸収して比べる。"""
    s = name.strip()
    for a, b in (("ヶ", "ケ"), ("ヵ", "カ"), ("﨑", "崎"), ("髙", "高"), ("檮", "梼"), ("　", ""), (" ", "")):
        s = s.replace(a, b)
    return s


def parse(rows, entities, label):
    """横持ちの CSV を {(entity_code, metric): value} にする。列の団体名をコード順の団体一覧と突き合わせる。"""
    head = rows[0]
    cols = [c for c in head[2:]]
    while cols and not cols[-1].strip():
        cols.pop()
    if len(cols) != len(entities):
        sys.exit(f"{label}: 団体の数が合わない（CSV {len(cols)} / こちら {len(entities)}）")
    for i, (col, ent) in enumerate(zip(cols, entities)):
        if norm(col) != norm(ent["name"]):
            sys.exit(f"{label}: {i + 1}列目の団体名が合わない（CSV「{col}」/ こちら「{ent['name']}」{ent['code']}）")
    by_label = {r[1].strip(): r for r in rows[1:] if len(r) > 1}
    out = {}
    for csv_label, key in METRICS:
        r = by_label.get(csv_label)
        if r is None:
            continue  # 都道府県の CSV には無い指標がある
        for i, ent in enumerate(entities):
            v = r[i + 2].strip() if i + 2 < len(r) else ""
            if not v or v in ("%", "-", "－"):
                continue  # 値なし（「%」だけのセルは回答なし）
            if not VALID.match(v):
                sys.exit(f"{label}: 想定外の値「{v}」（{ent['name']}・{csv_label}）")
            out[(ent["code"], key)] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", help="手元の ZIP を使う（テスト用）")
    args = ap.parse_args()
    if args.zip:
        data, src = open(args.zip, "rb").read(), PAGE
    else:
        url = find_zip_url()
        data, src = _get(url), PAGE
        print(f"取得: {url}")
    zf = zipfile.ZipFile(io.BytesIO(data))

    conn = db.connect()
    db.init_schema(conn)
    munis = [dict(r) for r in conn.execute(
        "SELECT code,name FROM entity WHERE kind='city' ORDER BY code") if r["code"] not in HOPPO]
    prefs = [dict(r) for r in conn.execute("SELECT code,name FROM entity WHERE kind='pref' ORDER BY code")]

    vals = parse(read_csv(zf, "_市区町村比較.csv"), munis, "市区町村")
    vals.update(parse(read_csv(zf, "_都道府県比較.csv"), prefs, "都道府県"))
    if len(vals) < 1000:
        sys.exit(f"取り込める値が少なすぎる（{len(vals)}件）")

    conn.execute("DELETE FROM dx_status")
    conn.executemany(
        "INSERT INTO dx_status (entity_code,metric,value_text,source_url,updated_at) VALUES (?,?,?,?,?)",
        [(c, k, v, src, NOW) for (c, k), v in sorted(vals.items())])
    conn.commit()
    n_ent = len({c for c, _ in vals})
    conn.close()
    print(f"自治体DXの取組状況: {len(vals)} 件（{n_ent} 団体）")


if __name__ == "__main__":
    main()
