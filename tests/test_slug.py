"""slug.py のテスト。設計書 6章「検証済み」のケースを網羅する。

実行: python3 tests/test_slug.py  （pytest でも可）
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import slug  # noqa: E402


# (漢字名, かな読み, 期待スラッグ)
MUNI = [
    ("蕨市", "わらびし", "warabi-shi"),
    ("新宿区", "しんじゅくく", "shinjuku-ku"),
    ("小川町", "おがわまち", "ogawa-machi"),
    ("江差町", "えさしちょう", "esashi-cho"),
    ("東秩父村", "ひがしちちぶむら", "higashichichibu-mura"),
    ("三郷市", "みさとし", "misato-shi"),
    ("美里町", "みさとまち", "misato-machi"),
    ("札幌市", "さっぽろし", "sapporo-shi"),
    ("別府市", "べっぷし", "beppu-shi"),
    ("郡山市", "こおりやまし", "koriyama-shi"),
    ("十日町市", "とおかまちし", "tokamachi-shi"),
    ("四日市市", "よっかいちし", "yokkaichi-shi"),
    ("野々市市", "ののいちし", "nonoichi-shi"),
    ("廿日市市", "はつかいちし", "hatsukaichi-shi"),
    ("鶴ヶ島市", "つるがしまし", "tsurugashima-shi"),
    ("八王子市", "はちおうじし", "hachioji-shi"),
    ("神戸市", "こうべし", "kobe-shi"),
    ("大阪市", "おおさかし", "osaka-shi"),
    ("京都市", "きょうとし", "kyoto-shi"),
    ("新潟市", "にいがたし", "niigata-shi"),
    ("宇都宮市", "うつのみやし", "utsunomiya-shi"),
    ("町田市", "まちだし", "machida-shi"),
    ("ふじみ野市", "ふじみのし", "fujimino-shi"),
    ("日光市", "にっこうし", "nikko-shi"),
    ("中央区", "ちゅうおうく", "chuo-ku"),
]

PREF = [
    ("北海道", "ほっかいどう", "hokkaido"),
    ("東京都", "とうきょうと", "tokyo"),
    ("京都府", "きょうとふ", "kyoto"),
    ("大阪府", "おおさかふ", "osaka"),
    ("埼玉県", "さいたまけん", "saitama"),
    ("青森県", "あおもりけん", "aomori"),
    ("沖縄県", "おきなわけん", "okinawa"),
]

GUN = [
    ("檜山郡", "ひやまぐん", "hiyama"),
    ("枝幸郡", "えさしぐん", "esashi"),
]

# romanize 単体（促音・撥音・長音の規則）
ROMAN = [
    ("さっぽろ", "sapporo"),
    ("べっぷ", "beppu"),
    ("ろっぽんぎ", "roppongi"),
    ("にっこう", "nikko"),
    ("はっちょう", "hatcho"),
    ("なんぶ", "nanbu"),
    ("しんじゅく", "shinjuku"),
    ("こうべ", "kobe"),
    ("こおりやま", "koriyama"),
    ("とおかまち", "tokamachi"),
    ("ちゅうおう", "chuo"),
    ("よっかいち", "yokkaichi"),
    # 半角カタカナ入力も通ること（総務省データ形式）
]


def run():
    fails = []

    for name, kana, want in MUNI:
        got, _suffix, _tl = slug.muni_slug(name, kana)
        if got != want:
            fails.append(f"muni_slug({name},{kana}) = {got!r}, want {want!r}")

    for name, kana, want in PREF:
        got = slug.pref_slug(name, kana)
        if got != want:
            fails.append(f"pref_slug({name},{kana}) = {got!r}, want {want!r}")

    for name, kana, want in GUN:
        got = slug.gun_slug(name, kana)
        if got != want:
            fails.append(f"gun_slug({name},{kana}) = {got!r}, want {want!r}")

    for kana, want in ROMAN:
        got = slug.romanize(kana)
        if got != want:
            fails.append(f"romanize({kana}) = {got!r}, want {want!r}")

    # 半角カタカナ（総務省データ形式）でも同じ結果になること
    if slug.romanize("ｻｯﾎﾟﾛ") != "sapporo":
        fails.append(f"romanize(半角ｻｯﾎﾟﾛ) = {slug.romanize('ｻｯﾎﾟﾛ')!r}")

    # 全スラッグが規則に合致すること
    for name, kana, _want in MUNI:
        s, _, _ = slug.muni_slug(name, kana)
        if not slug.is_valid_slug(s):
            fails.append(f"invalid slug form: {s}")

    total = len(MUNI) + len(PREF) + len(GUN) + len(ROMAN) + 1
    if fails:
        print(f"FAIL {len(fails)}/{total}")
        for f in fails:
            print("  -", f)
        return 1
    print(f"OK  all {total} slug cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
