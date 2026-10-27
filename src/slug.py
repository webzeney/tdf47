"""かな読み → URLスラッグ 生成（設計書 6章「スラッグ生成規則」の正本を実装）。

入力は団体名の「かな読み」。漢字からは生成しない。
- 長音は表記しない（ou→o / oo→o / uu→u、長音記号ーは削除）
- 促音は次の子音を重ねる（さっぽろ→sapporo）。促音＋ち は tch（はっちょう→hatcho）
- 撥音「ん」は常に n（修正ヘボン式の m は使わない。読みから一意に決めるため）
- し=shi / ち=chi / つ=tsu / ふ=fu / じ=ji / づ=zu / ぢ=ji / を=o
- 接尾辞は「読み」で決める（市=し→-shi、町=まち→-machi/ちょう→-cho 等）

このモジュールは純粋関数のみ。DB もファイルも触らない（テスト可能性のため）。
"""

from __future__ import annotations

import re
import unicodedata

# ── かな → ローマ字（拗音を優先的に見るため2文字表を先に引く）──────────────

# 拗音・特殊2文字（子音＋小さいゃゅょ、一部の外来音）
_DIGRAPH = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    # 外来音（地名でまれに出る）
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔ": "vu", "ゔぇ": "ve", "ゔぉ": "vo",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "ちぇ": "che", "じぇ": "je", "しぇ": "she",
}

# 清音・濁音・半濁音（1文字）
_MONO = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o",
    # 単独の小書き（拗音を構成しなかった場合のフォールバック）
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo",
}

_KATA_START = 0x30A1  # ァ
_KATA_END = 0x30F6    # ヶ


def to_hiragana(text: str) -> str:
    """半角/全角カタカナ混在の読みを、ひらがなに正規化する。

    総務省コード表のカナは半角カタカナ（ｻｯﾎﾟﾛｼ）なので、
    まず NFKC で全角化し、カタカナをひらがなへ写す。
    """
    text = unicodedata.normalize("NFKC", text or "")
    out = []
    for ch in text:
        cp = ord(ch)
        if _KATA_START <= cp <= _KATA_END:
            out.append(chr(cp - 0x60))  # カタカナ → ひらがな
        else:
            out.append(ch)
    return "".join(out)


def _collapse_long(roman: str) -> str:
    """長音の除去。ou→o / oo→o / uu→u（設計書の規則）。"""
    roman = roman.replace("ou", "o").replace("oo", "o").replace("uu", "u")
    return roman


def romanize(kana: str) -> str:
    """ひらがな/カタカナの読みを、設計書の規則でローマ字化する。"""
    s = to_hiragana(kana)
    out: list[str] = []
    i = 0
    n = len(s)
    sokuon = False
    while i < n:
        ch = s[i]
        if ch == "っ":
            sokuon = True
            i += 1
            continue
        if ch == "ん":
            out.append("n")
            i += 1
            continue
        if ch == "ー":  # 長音記号は削除
            i += 1
            continue
        two = s[i:i + 2]
        if two in _DIGRAPH:
            r = _DIGRAPH[two]
            i += 2
        elif ch in _MONO:
            r = _MONO[ch]
            i += 1
        else:
            # 読みに想定外の文字（空白・中黒・漢字など）→ 無視
            i += 1
            continue
        if sokuon:
            if r.startswith("ch"):
                r = "t" + r          # 促音＋ち系 → tch
            elif r[0] not in "aiueo":
                r = r[0] + r         # 子音を重ねる
            # 母音始まりの促音は表記しようがないので落とす
            sokuon = False
        out.append(r)
    return _collapse_long("".join(out))


# ── 接尾辞（読みで決める）──────────────────────────────────────────────

# (漢字末尾, 読み末尾) → (剥がす読み長, 接尾辞スラッグ, type_label)
_MUNI_SUFFIX = [
    ("市", "し", 1, "shi", "市"),
    ("区", "く", 1, "ku", "区"),
    ("町", "ちょう", 3, "cho", "町"),
    ("町", "まち", 2, "machi", "町"),
    ("村", "むら", 2, "mura", "村"),
    ("村", "そん", 2, "son", "村"),
]


class SlugError(ValueError):
    """スラッグ生成に失敗（想定外の接尾辞・読み不一致など）。"""


def muni_slug(name_kanji: str, kana: str) -> tuple[str, str, str]:
    """市区町村・行政区のスラッグを返す。

    returns: (slug, suffix, type_label)  例: ("warabi-shi", "shi", "市")
    """
    kana_h = to_hiragana(kana)
    last = name_kanji[-1]
    for k_last, y_last, cut, suffix, type_label in _MUNI_SUFFIX:
        if last == k_last and kana_h.endswith(y_last):
            base = kana_h[:-cut]
            stem = romanize(base)
            if not stem:
                raise SlugError(f"stem空: {name_kanji}/{kana}")
            return f"{stem}-{suffix}", suffix, type_label
    raise SlugError(f"接尾辞を判定できない: {name_kanji!r} / {kana!r}")


# 都道府県：接尾辞なし。読み末尾の都/道/府/県分を剥がす（北海道は剥がさない）。
_PREF_STRIP = {"都": "と", "府": "ふ", "県": "けん"}


def pref_slug(name_kanji: str, kana: str) -> str:
    """都道府県のスラッグ。例: 東京都→tokyo / 京都府→kyoto / 北海道→hokkaido。"""
    kana_h = to_hiragana(kana)
    last = name_kanji[-1]
    strip = _PREF_STRIP.get(last)  # 北海道(道) は None → 剥がさない
    if strip and kana_h.endswith(strip):
        kana_h = kana_h[:-len(strip)]
    return romanize(kana_h)


def gun_slug(gun_kanji: str, gun_kana: str) -> str:
    """郡名のスラッグ（衝突回避の前置に使う）。末尾「ぐん（郡）」を剥がす。"""
    kana_h = to_hiragana(gun_kana)
    if kana_h.endswith("ぐん"):
        kana_h = kana_h[:-2]
    return romanize(kana_h)


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_valid_slug(slug: str) -> bool:
    """小文字ASCII・ハイフン区切りのみを許可（設計書 URL 基本ルール）。"""
    return bool(_SLUG_RE.match(slug))
