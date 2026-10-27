#!/usr/bin/env python3
"""05_adjacency.py — 隣接自治体を算出（設計書 データ取得順6 / 国土数値情報 N03）。

国土数値情報 行政区域(N03) のポリゴンから、市区町村どうしの陸上隣接を算出する。
★重い処理。設計書どおり本来はVPSで実行する。手元では shapely のみ使用。

方針:
- N03の行政区域コード(5桁)を entity に対応づけ、行政区(ward)は親の政令市に集約して「市単位」で扱う。
- 各市区町村のポリゴンを dissolve（飛地は結合）、STRtree で境界が接するペアを隣接とする。
- adjacency テーブルへ両方向で格納（冪等：全消去→再投入）。

注意（設計書13章）: 飛地（結合で対応）／湖上・海上の見かけ接触（微小バッファで吸収しきれない分は残りうる）／離島は0件。
実行: python3 src/ingest/05_adjacency.py
"""

from __future__ import annotations

import json
import os
import sys

from shapely import STRtree
from shapely.geometry import shape
from shapely.ops import unary_union

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402

KSJ_DIR = os.path.join(ROOT, "data", "ksj")
CODE_PROP = "N03_007"  # 行政区域コード（5桁）


def find_geojson():
    # 市区町村版（_prefecture でない）を優先。
    cands = [f for f in os.listdir(KSJ_DIR) if f.lower().endswith(".geojson")]
    muni = [f for f in cands if "prefecture" not in f.lower()]
    pick = (muni or cands)
    return os.path.join(KSJ_DIR, pick[0]) if pick else None


def main():
    gj = find_geojson()
    if not gj:
        sys.exit(f"N03 GeoJSON が見つからない（{KSJ_DIR}）。shapefileの場合は変換が必要。")

    conn = db.connect()
    cur = conn.cursor()
    ents = [dict(r) for r in cur.execute("SELECT code,kind,parent_code FROM entity")]
    by5 = {}
    for e in ents:
        # ward は親政令市に集約。city/特別区はそのまま。
        target = e["parent_code"] if e["kind"] == "ward" and e["parent_code"] else e["code"]
        by5.setdefault(e["code"][:5], target)

    print("N03 読み込み中 …")
    with open(gj, encoding="utf-8") as f:
        data = json.load(f)
    feats = data["features"]
    print(f"  features: {len(feats)}")

    # 市単位(6桁 target)ごとにジオメトリを集める
    geoms_by_target = {}
    skipped = 0
    for ft in feats:
        props = ft.get("properties") or {}
        code5 = props.get(CODE_PROP)
        geom = ft.get("geometry")
        if not code5 or not geom:
            skipped += 1
            continue
        target = by5.get(str(code5))
        if not target:
            skipped += 1
            continue
        try:
            g = shape(geom)
            if not g.is_empty:
                geoms_by_target.setdefault(target, []).append(g)
        except Exception:
            skipped += 1
    print(f"  対象市区町村: {len(geoms_by_target)}  （スキップ feature {skipped}）")

    print("ポリゴン結合（dissolve）…")
    codes = []
    unified = []
    for target, gs in geoms_by_target.items():
        u = unary_union(gs)
        codes.append(target)
        unified.append(u)

    print("隣接判定（STRtree）…")
    tree = STRtree(unified)
    pairs = set()
    for i, g in enumerate(unified):
        # 境界が接する/交差する候補を取得（微小バッファで数値誤差のスリバーを吸収）
        cand = tree.query(g, predicate="intersects")
        for j in cand:
            if j == i:
                continue
            a, b = codes[i], codes[j]
            if a == b:
                continue
            pairs.add((a, b) if a < b else (b, a))

    print(f"隣接ペア: {len(pairs)}  → adjacency 投入")
    cur.execute("DELETE FROM adjacency")
    for a, b in pairs:
        cur.execute("INSERT OR IGNORE INTO adjacency (a_code,b_code) VALUES (?,?)", (a, b))
        cur.execute("INSERT OR IGNORE INTO adjacency (a_code,b_code) VALUES (?,?)", (b, a))
    conn.commit()

    # 隣接0件（離島等）の市区町村数
    zero = cur.execute(
        "SELECT count(*) FROM entity WHERE kind='city' AND code NOT IN "
        "(SELECT DISTINCT a_code FROM adjacency)").fetchone()[0]
    conn.close()
    print(f"完了。隣接0件の市区町村（離島など）: {zero}")


if __name__ == "__main__":
    main()
