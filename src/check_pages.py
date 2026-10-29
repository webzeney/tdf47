#!/usr/bin/env python3
"""check_pages.py — 人手で探したリンクの候補ページを開いて、表示できるか・何のページかを確かめる。

入力 CSV（entity_code,kind_code,url[,memo]）の各 URL を開き、
  - HTTP の結果・最終 URL・ページの題名（title / 最初の h1）
  - ページ内のリンクのうち、言葉（--words）を含むもの（最大 --max-links 件）
を出力 CSV に書く。DB は変えない（結果を見て manual_links.csv に入れる）。

マナー: robots.txt を守る／同じサイトへは 1.2 秒以上あける／User-Agent に連絡先（crawl_links.py と同じ）。
★手元のPCでは実行しない。GitHub Actions（check-pages.yml）から実行する。

実行: python3 src/check_pages.py reports/check-input.csv --out reports/check-result.csv
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

SRC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)
from crawl_links import DELAY_SEC, MAX_BYTES, TIMEOUT, _decode, anchors  # noqa: E402
from probe import UA, allowed_by_robots  # noqa: E402

DEFAULT_WORDS = "広報,区版,だより,例規,バックナンバー"
_last = {}


def fetch(url: str):
    """(status, final_url, html_text or '', error)"""
    if not allowed_by_robots(url):
        return None, url, "", "robots.txt で禁止"
    host = urlparse(url).hostname or ""
    wait = DELAY_SEC - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read(MAX_BYTES)
            text = _decode(raw, r.headers.get_content_charset()) if "html" in ctype.lower() else ""
            return r.status, r.geturl(), text, "" if text else f"HTMLではない（{ctype}）"
    except urllib.error.HTTPError as e:
        return e.code, url, "", f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, url, "", type(e).__name__ + ": " + str(e)[:80]


def title_of(text: str) -> str:
    out = []
    for pat in (r"<title[^>]*>(.*?)</title>", r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pat, text, re.I | re.S)
        if m:
            out.append(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()[:80])
    return " ／ ".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True)
    ap.add_argument("--words", default=DEFAULT_WORDS)
    ap.add_argument("--max-links", type=int, default=30)
    args = ap.parse_args()
    words = [w for w in args.words.split(",") if w]
    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    print(f"対象 {len(rows)} 件")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entity_code", "kind_code", "url", "memo", "status", "final_url", "title", "error", "links"])
        for i, r in enumerate(rows, 1):
            url = r["url"].strip()
            st, final, text, err = fetch(url)
            links = []
            if text:
                seen = set()
                for t, u in anchors(final, text):
                    if any(x in t for x in words) and u not in seen and len(t) <= 60:
                        seen.add(u)
                        links.append(f"{t} => {u}")
                        if len(links) >= args.max_links:
                            break
            w.writerow([r.get("entity_code", ""), r.get("kind_code", ""), url, r.get("memo", ""),
                        st or "", final, title_of(text) if text else "", err, " | ".join(links)])
            f.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(rows)}")
    print(f"完了 → {args.out}")


if __name__ == "__main__":
    main()
