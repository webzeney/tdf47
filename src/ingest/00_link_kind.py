#!/usr/bin/env python3
"""00_link_kind.py — link_kind マスタの投入（参照データ・冪等）。

設計書 5章の link_kind と サイトマップ /item/ の分類に基づく。
phase='v2.0' のものは画面で「準備中」と表示される（link ではなく link_kind で駆動）。
ラベル・並び・アイコンはモック入手後に調整しうるが、節構成の骨格として先に確定させる。
"""

from __future__ import annotations

import os
import sys

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)
import db  # noqa: E402

# (code, label, category, phase, sort, icon)
LINK_KINDS = [
    ("official", "公式サイト", "admin", "v1.0", 10, "home"),
    ("reiki", "例規集", "admin", "v1.0", 20, "book"),
    ("gikai", "議会会議録", "admin", "v1.0", 30, "gavel"),
    ("kouhou", "広報誌", "admin", "v1.0", 40, "news"),
    ("sns_x", "X（旧Twitter）", "sns", "v1.0", 50, "x"),
    ("sns_line", "LINE", "sns", "v1.0", 60, "line"),
    ("sns_facebook", "Facebook", "sns", "v1.0", 70, "facebook"),
    ("sns_instagram", "Instagram", "sns", "v1.0", 80, "instagram"),
    ("sns_youtube", "YouTube", "sns", "v1.0", 90, "youtube"),
    # v2.0（CMS判別エンジン実装後）。v1.0では「準備中」表示。
    ("pubcomment", "パブリックコメント", "admin", "v1.0", 100, "comment"),
    ("nyusatsu", "入札・調達", "admin", "v1.0", 110, "doc"),
    ("saiyo", "職員採用", "admin", "v1.0", 120, "person"),
    ("toukei", "統計", "admin", "v1.0", 130, "chart"),
    # 2026-10-22 追加（職員の「計画をつくる」作業向け。全国一覧がないため v2.0 の探索で取得）
    ("sougou_keikaku", "総合計画", "admin", "v1.0", 140, "doc"),
    ("dx_keikaku", "DX推進計画", "admin", "v1.0", 150, "doc"),
    ("dx_torikumi", "自治体DXの取組状況", "admin", "v2.0", 160, "chart"),
    # ご当地カード
    ("card_manhole", "マンホールカード", "card", "v1.0", 200, "manhole"),
    ("card_dam", "ダムカード", "card", "v1.0", 210, "dam"),
]


def main():
    conn = db.connect()
    db.init_schema(conn)
    cur = conn.cursor()
    existing = {r["code"]: dict(r) for r in cur.execute(
        "SELECT code,label,category,phase,sort,icon FROM link_kind")}
    ins = upd = same = 0
    for code, label, category, phase, sort, icon in LINK_KINDS:
        rec = dict(code=code, label=label, category=category, phase=phase, sort=sort, icon=icon)
        old = existing.get(code)
        if old is None:
            cur.execute("INSERT INTO link_kind (code,label,category,phase,sort,icon) VALUES (?,?,?,?,?,?)",
                        (code, label, category, phase, sort, icon))
            ins += 1
        elif any(old[k] != rec[k] for k in rec):
            cur.execute("UPDATE link_kind SET label=?,category=?,phase=?,sort=?,icon=? WHERE code=?",
                        (label, category, phase, sort, icon, code))
            upd += 1
        else:
            same += 1
    conn.commit()
    conn.close()
    print(f"link_kind: 新規 {ins} / 更新 {upd} / 変化なし {same}  合計 {len(LINK_KINDS)}")


if __name__ == "__main__":
    main()
