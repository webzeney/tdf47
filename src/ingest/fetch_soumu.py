#!/usr/bin/env python3
"""fetch_soumu.py — 総務省の財政系資料の最新年度を自動で探し、data/soumu/ に保存する。

対象（追補 #31〜#34）:
  - 市町村決算カード（都道府県別 PDF・Excel）      … 類似団体区分（市町村類型）と決算カードPDFのページ
  - 類似団体比較カード（都道府県別 PDF）
  - 地方公共団体の主要財政指標一覧（全市町村・全都道府県 Excel）

総務省のファイルURL（main_content/<連番>）は年度ごとに変わるため固定せず、
毎回「一覧ページの先頭（＝最新年度）のリンク」をたどって解決する。
結果は data/soumu/manifest.json に書き出し、取り込み側（13_fiscal.py / 14_ruiji.py）はそれを読む。

総務省のページは Shift_JIS。クロールのマナー（設計書 13章）に従い、
1リクエスト1秒以上あけ、User-Agent に連絡先を入れる。
★手元のPCでは実行しない。GitHub Actions（soumu.yml）から実行する。

実行: python3 src/ingest/fetch_soumu.py [--no-download]
"""

from __future__ import annotations

import argparse
import html.parser
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(SRC)
OUT = os.path.join(ROOT, "data", "soumu")
CARD_DIR = os.path.join(OUT, "kessan-card")
MANIFEST = os.path.join(OUT, "manifest.json")

BASE = "https://www.soumu.go.jp"
CARD_INDEX = BASE + "/iken/zaisei/card.html"
SHIHYO_INDEX = BASE + "/iken/shihyo_ichiran.html"
UA = "TDF47-bot/1.0 (+https://tdf47.com/about/; open data fetcher; contact: https://tdf47.com/contact/)"
WAIT = 1.2

PREF_NAMES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
PREF2_BY_NAME = {n: f"{i + 1:02d}" for i, n in enumerate(PREF_NAMES)}


# ── 取得 ──
_last = [0.0]


def _get(url: str) -> bytes:
    wait = WAIT - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    _last[0] = time.time()
    return data


def decode_html(raw: bytes) -> str:
    """meta charset を見て復号する（総務省は Shift_JIS）。"""
    head = raw[:3000].decode("ascii", "ignore")
    m = re.search(r"charset=[\"']?([\w-]+)", head, re.I)
    cs = (m.group(1) if m else "utf-8").lower()
    if cs in ("shift_jis", "shift-jis", "sjis", "x-sjis"):
        cs = "cp932"
    return raw.decode(cs, "replace")


# ── HTML解析 ──
class _Links(html.parser.HTMLParser):
    """<a> を (テキスト, href, 所属<td>の連番) で集める。<td> 単位のまとまりが要るため td 番号も持つ。"""

    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str, int]] = []
        self._a: list | None = None
        self._td = -1
        self._td_text: dict[int, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            self._td += 1
            self._td_text[self._td] = []
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._a = [href, []]
        elif tag == "img" and self._a is not None:
            alt = dict(attrs).get("alt")
            if alt:
                self._a[1].append(alt)

    def handle_data(self, data):
        if self._a is not None:
            self._a[1].append(data)
        if self._td >= 0:
            self._td_text[self._td].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            text = re.sub(r"\s+", "", "".join(self._a[1]))
            self.links.append((text, self._a[0], self._td))
            self._a = None

    def td_text(self, i: int) -> str:
        return re.sub(r"\s+", "", "".join(self._td_text.get(i, [])))


def parse(html_text: str) -> _Links:
    p = _Links()
    p.feed(html_text)
    return p


def absurl(href: str, base: str) -> str:
    return urllib.parse.urljoin(base, href)


def first_link(p: _Links, pattern: str, base: str) -> tuple[str, str]:
    """リンク文字列（中の画像の alt を含む。例「全市町村の主要財政指標EXCEL」）が pattern に合う最初のリンク。"""
    for text, href, _ in p.links:
        if re.search(pattern, text):
            return text, absurl(href, base)
    raise RuntimeError(f"リンクが見つかりません: {pattern}")


def fiscal_year_in(text: str) -> str:
    m = re.search(r"令和([0-9０-９]+|元)年度", text)
    if not m:
        raise RuntimeError(f"年度が読み取れません: {text}")
    n = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return f"令和{n}年度"


def pref_of(td_text: str) -> str | None:
    """「■埼玉県」「■北海道1212」などのセル文字列から都道府県2桁コード。"""
    for name, p2 in PREF2_BY_NAME.items():
        if name in td_text:
            return p2
    return None


def parse_card_page(html_text: str, base: str) -> list[dict]:
    """市町村決算カードの年度ページ → [{pref, pdf:[..], xlsx:[..]}]（北海道は2分割）。"""
    p = parse(html_text)
    cells: dict[int, dict] = {}
    for _text, href, td in p.links:
        if td < 0:
            continue
        low = href.lower()
        if not (low.endswith(".pdf") or low.endswith(".xlsx")):
            continue
        c = cells.setdefault(td, {"pdf": [], "xlsx": []})
        c["pdf" if low.endswith(".pdf") else "xlsx"].append(absurl(href, base))
    out = []
    for td, c in sorted(cells.items()):
        p2 = pref_of(p.td_text(td))
        if p2 and c["xlsx"]:
            out.append({"pref": p2, "pdf": c["pdf"], "xlsx": c["xlsx"]})
    return out


def parse_similarity_page(html_text: str, base: str) -> dict[str, str]:
    """類似団体比較カードの年度ページ → {pref2: pdf}。"""
    p = parse(html_text)
    out = {}
    for text, href, _td in p.links:
        if href.lower().endswith(".pdf"):
            p2 = pref_of(text)
            if p2 and p2 not in out:
                out[p2] = absurl(href, base)
    return out


def resolve() -> dict:
    """一覧ページから最新年度のURL一式を解決する（ダウンロードはしない）。"""
    card_idx = parse(decode_html(_get(CARD_INDEX)))
    card_text, card_page = first_link(card_idx, r"年度市町村決算カード", CARD_INDEX)
    sim_text, sim_page = first_link(card_idx, r"年度類似団体比較カード", CARD_INDEX)
    fy = fiscal_year_in(card_text)
    if fiscal_year_in(sim_text) != fy:
        print(f"注意: 類似団体比較カードの最新年度({sim_text})が決算カード({card_text})と異なります", file=sys.stderr)

    card = parse_card_page(decode_html(_get(card_page)), card_page)
    sim = parse_similarity_page(decode_html(_get(sim_page)), sim_page)

    shi_idx = parse(decode_html(_get(SHIHYO_INDEX)))
    shi_text, shi_page = first_link(shi_idx, r"年度主要財政指標一覧", SHIHYO_INDEX)
    shi = parse(decode_html(_get(shi_page)))
    _, shichoson = first_link(shi, r"^全市町村の主要財政指標(?:EXCEL|Excel|エクセル)?$", shi_page)
    _, todofuken = first_link(shi, r"^全都道府県の主要財政指標(?:EXCEL|Excel|エクセル)?$", shi_page)

    if len(card) != 47:
        raise RuntimeError(f"決算カードの都道府県数が47ではありません: {len(card)}")
    if len(sim) != 47:
        raise RuntimeError(f"類似団体比較カードの都道府県数が47ではありません: {len(sim)}")
    return {
        "kessan_card": {"fiscal_year": fy, "page": card_page, "prefs": card},
        "similarity_card": {"fiscal_year": fiscal_year_in(sim_text), "page": sim_page, "prefs": sim},
        "shihyo": {"fiscal_year": fiscal_year_in(shi_text), "page": shi_page,
                   "shichoson": shichoson, "todofuken": todofuken},
    }


def download(man: dict) -> None:
    os.makedirs(CARD_DIR, exist_ok=True)
    for c in man["kessan_card"]["prefs"]:
        c["xlsx_local"] = []
        for u in c["xlsx"]:
            name = os.path.basename(urllib.parse.urlparse(u).path)
            dest = os.path.join(CARD_DIR, name)
            if not os.path.exists(dest):
                with open(dest, "wb") as f:
                    f.write(_get(u))
            c["xlsx_local"].append(os.path.relpath(dest, ROOT))
    for key, fname in (("shichoson", "shihyo-shichoson.xlsx"), ("todofuken", "shihyo-todofuken.xlsx")):
        dest = os.path.join(OUT, fname)
        with open(dest, "wb") as f:
            f.write(_get(man["shihyo"][key]))
        man["shihyo"][key + "_local"] = os.path.relpath(dest, ROOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true", help="URLの解決だけ行い、ファイルは取得しない")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    man = resolve()
    if not args.no_download:
        download(man)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    k = man["kessan_card"]
    print(f"決算カード {k['fiscal_year']}: {len(k['prefs'])}都道府県 / "
          f"類似団体比較カード {man['similarity_card']['fiscal_year']} / "
          f"主要財政指標 {man['shihyo']['fiscal_year']} → {os.path.relpath(MANIFEST, ROOT)}")


if __name__ == "__main__":
    main()
