#!/usr/bin/env python3
"""build.py — SQLite から静的HTMLを生成（設計書 9章「静的生成」）。

entity.phase='v1.0' のページだけを生成。出力は dist/ 配下、末尾スラッシュURLは
<path>/index.html に書き出す。static/ は dist/assets/ にコピー。
実行時にDB/Pythonは本番で動かさない前提（生成の素材としてのみ使用）。

実行: python3 src/build.py
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sys

from jinja2 import Environment, FileSystemLoader, select_autoescape

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
sys.path.insert(0, SRC)
import db  # noqa: E402
from probe import ROBOTS_ERR, OFFICIAL_MARKERS  # noqa: E402

DIST = os.path.join(ROOT, "dist")
TPL = os.path.join(SRC, "templates")
STATIC = os.path.join(ROOT, "static")

SITE_NAME = "全国の地方自治体の色々なリンクを集めてみた件"
SITE_SHORT = "全国の地方自治体の色々なリンクを集めてみた件TOP"  # パンくずの先頭など、短く出す場所の名称
# 空文字で渡ってきても既定を使う（GitHub Variables未設定でもOK）
SITE_URL = os.environ.get("SITE_URL") or "https://tdf47.com"
SITE_DESC = ("全国1,741市区町村を網羅した自治体リンク集。例規集・議会会議録・広報誌・公式サイト・"
             "公式SNSへのリンクを、隣接自治体ごとにまとめて掲載。自治体のリンクを探すなら全国の地方自治体の色々なリンクを集めてみた件。")
# 公開前に設定（環境変数で注入）。空ならタグ/リンクは出力しない。
GA4_ID = os.environ.get("GA4_ID") or "G-834NJY78FW"  # tdf47.com の GA4 測定ID
CONTACT_URL = (os.environ.get("CONTACT_URL")
               or "https://docs.google.com/forms/d/e/1FAIpQLSflWRIOMfP_UGe-C958V-UTIQCJbbCYqa0nspRQiIxlvZNPpA/viewform")  # tdf47.com お問い合わせフォーム
TODAY = datetime.date.today().isoformat()
YEAR = datetime.date.today().year
# CSS/JS のキャッシュバスティング用。static/ の中身のハッシュ（中身が変わったときだけ変わる）。
# ※以前はビルド日時だったため、毎回 全HTMLが変化 → FTPで全ファイル再送（約40分）になっていた（2026-10-25修正）。
def _asset_ver() -> str:
    import hashlib
    h = hashlib.sha1()
    for d, dirs, files in os.walk(STATIC):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(d, fn)
            h.update(os.path.relpath(p, STATIC).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:10]


ASSET_VER = _asset_ver()

# 都道府県2桁コード → 地方ブロック
BLOCKS = [
    ("hokkaido-tohoku", "北海道・東北", {"01", "02", "03", "04", "05", "06", "07"}),
    ("kanto", "関東", {"08", "09", "10", "11", "12", "13", "14"}),
    ("chubu", "中部", {"15", "16", "17", "18", "19", "20", "21", "22", "23"}),
    ("kinki", "近畿", {"24", "25", "26", "27", "28", "29", "30"}),
    ("chugoku", "中国", {"31", "32", "33", "34", "35"}),
    ("shikoku", "四国", {"36", "37", "38", "39"}),
    ("kyushu-okinawa", "九州・沖縄", {"40", "41", "42", "43", "44", "45", "46", "47"}),
]

ITEM_META = {
    "official": ("公式サイト", "各市区町村の公式ウェブサイトのトップページ。"),
    "reiki": ("例規集", "条例・規則などの例規集。"),
    "gikai": ("議会会議録", "議会の会議録検索システム。"),
    "kouhou": ("広報誌", "広報誌のバックナンバー・電子版。"),
    "sns": ("公式SNS", "X・LINE・Facebook・Instagram・YouTube などの公式アカウント。"),
    "neighbors": ("隣接自治体", "陸続きで隣接する自治体の一覧。隣のまちの取り組みを調べられます。"),
}
TYPE_META = {
    "seirei": ("政令指定都市", "政令で指定された人口50万以上の市。行政区を持ちます。"),
    "chukaku": ("中核市", "地方自治法で政令指定された人口20万以上の都市（令和5年時点で62市）。政令指定都市に次ぐ事務権限を持ちます。"),
    "tokyo23": ("東京23区", "東京都の特別区。"),
    "shi": ("市", "市（政令指定都市を含む）。"),
    "cho": ("町", "町。"),
    "son": ("村", "村。"),
    "union": ("広域連合", "後期高齢者医療広域連合（v1.0）。"),
}

# ── 類似団体区分（追補 #31） ──
# 区分の定義は総務省「類似団体別職員数の状況」の区分表による。値は決算カードの「市町村類型」欄（例 Ⅱ－３）。
# 一般市と町村は同じローマ数字を使うが別の区分なので、URLキーは shi-2-3 / choson-2-1 のように分ける。
# 北方領土の6村。総務省コードには現行団体として載るが、公式の市区町村数（1,741）には含めない。
# ページは生成し、件数の表記では「ほか北方領土の6村」と注記する。
HOPPO = {"016951", "016969", "016977", "016985", "016993", "017001"}

RUIJI_SRC = "https://www.soumu.go.jp/main_sosiki/jichi_gyousei/c-gyousei/ruiji-dantai/index.html"
ROMAN = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5}
RUIJI_GROUP = {"shi": "一般市", "choson": "町村"}
RUIJI_POP = {
    "shi": {1: "人口5万人未満", 2: "人口5万人以上10万人未満", 3: "人口10万人以上15万人未満",
            4: "人口15万人以上"},
    "choson": {1: "人口5千人未満", 2: "人口5千人以上1万人未満", 3: "人口1万人以上1万5千人未満",
               4: "人口1万5千人以上2万人未満", 5: "人口2万人以上"},
}
RUIJI_IND = {
    "shi": {3: "第2次・第3次産業が90%以上、かつ第3次産業が65%以上",
            2: "第2次・第3次産業が90%以上、かつ第3次産業が65%未満",
            1: "第2次・第3次産業が90%未満、かつ第3次産業が55%以上",
            0: "第2次・第3次産業が90%未満、かつ第3次産業が55%未満"},
    "choson": {2: "第2次・第3次産業が80%以上、かつ第3次産業が60%以上",
               1: "第2次・第3次産業が80%以上、かつ第3次産業が60%未満",
               0: "第2次・第3次産業が80%未満"},
}
# 一般市・町村以外は区分そのものが1グループ（既存の区分ページがあるものはそこへ）
RUIJI_SPECIAL = {
    "政令指定都市": ("seirei", "/type/seirei/"),
    "中核市": ("chukaku", "/type/chukaku/"),
    "施行時特例市": ("tokurei", "/type/ruiji/tokurei/"),
    "特別区": ("tokyo23", "/type/tokyo23/"),
}
RUIJI_METRICS = ("ruiji_type", "kessan_card", "similarity_card")


def fy_num(fiscal_year: str) -> int:
    """「令和6年度」→ 6（「令和元年度」→ 1）。年度の新旧比較用。"""
    m = re.search(r"令和(\d+|元)年度", fiscal_year or "")
    if not m:
        return 0
    return 1 if m.group(1) == "元" else int(m.group(1))


def ruiji_info(raw: str, type_label: str):
    """決算カードの類型表記 → {key, label, path, desc}。読めなければ None。"""
    raw = (raw or "").strip()
    if raw in RUIJI_SPECIAL:
        key, path = RUIJI_SPECIAL[raw]
        return {"key": key, "label": raw, "path": path, "desc": "", "group": key}
    m = re.match(r"([ⅠⅡⅢⅣⅤ])\s*[－\-‐ー]\s*([0-9０-９])$", raw)
    if not m:
        return None
    p = ROMAN[m.group(1)]
    i = int(m.group(2).translate(str.maketrans("０１２３４５６７８９", "0123456789")))
    group = "choson" if type_label in ("町", "村") else "shi"
    pop, ind = RUIJI_POP[group].get(p), RUIJI_IND[group].get(i)
    if not (pop and ind):
        return None
    key = f"{group}-{p}-{i}"
    return {"key": key, "label": f"{RUIJI_GROUP[group]} {raw}", "path": f"/type/ruiji/{key}/",
            "desc": f"{pop}／産業構造：{ind}", "group": group}


def url_disp(url, limit=52):
    """リンクカードに出す短い表示用URL（スキームと末尾スラッシュを落として省略）。"""
    if not url:
        return ""
    d = re.sub(r"^https?://", "", url).rstrip("/")
    return d if len(d) <= limit else d[:limit - 1] + "…"


def esc_env():
    env = Environment(
        loader=FileSystemLoader(TPL),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["jp_date"] = lambda d: _jp_date(d)
    env.filters["format_num"] = lambda n: f"{n:,}" if isinstance(n, (int, float)) else n
    return env


class Builder:
    def __init__(self):
        self.env = esc_env()
        self.conn = db.connect()
        self.n = 0
        self._load()

    def _load(self):
        cur = self.conn.cursor()
        self.entities = [dict(r) for r in cur.execute("SELECT * FROM entity")]
        self.by_code = {e["code"]: e for e in self.entities}
        self.prefs = sorted((e for e in self.entities if e["kind"] == "pref"),
                            key=lambda e: e["code"])
        self.pref_by2 = {p["code"][:2]: p for p in self.prefs}
        self.cities = [e for e in self.entities if e["kind"] == "city"]
        self.wards = [e for e in self.entities if e["kind"] == "ward"]
        self.unions = [e for e in self.entities if e["kind"] == "union"]
        self.link_kinds = [dict(r) for r in cur.execute(
            "SELECT * FROM link_kind ORDER BY sort")]
        # links: entity_code -> kind_code -> [rows]
        # unchecked（種まき済み・未検証）も載せ、UIで「収集済み・未確認」として出す。
        # probe.py が検証すると ok/dead に変わり、ok は最終確認日つきで表示される。
        self.links = {}
        for r in cur.execute("SELECT * FROM link WHERE status IN ('ok','none','unchecked')"):
            self.links.setdefault(r["entity_code"], {}).setdefault(r["kind_code"], []).append(dict(r))
        # 直近の確認が「robots.txt で不許可」だったリンク（＝自動では生存確認できない）。
        # 公式サイト掲載の照合（OFFICIAL_MARKERS）の記録は「確認の試み」に数えない。
        self.robots_blocked = {r[0] for r in cur.execute(
            "SELECT p.link_id, p.error FROM probe p WHERE p.id = (SELECT q.id FROM probe q "
            "WHERE q.link_id = p.link_id AND (q.error IS NULL OR q.error NOT IN (?,?)) "
            "ORDER BY q.checked_at DESC, q.id DESC LIMIT 1)", OFFICIAL_MARKERS)
            if r[1] == ROBOTS_ERR}
        # adjacency: code -> [codes]
        self.adj = {}
        for r in cur.execute("SELECT a_code,b_code FROM adjacency"):
            self.adj.setdefault(r["a_code"], []).append(r["b_code"])
        # cards: entity_code -> [rows]
        self.cards = {}
        for r in cur.execute("SELECT * FROM card"):
            self.cards.setdefault(r["entity_code"], []).append(dict(r))
        # union members: union_code -> [member_code]
        self.umembers = {}
        for r in cur.execute("SELECT union_code,member_code FROM union_member"):
            self.umembers.setdefault(r["union_code"], []).append(r["member_code"])
        # fiscal: entity_code -> {year, source, vals:{metric:value}}（財政指標。最新年度のみ）
        # ruiji:  entity_code -> {year, raw, info, card, sim}（類似団体区分・決算カード。最新年度のみ）
        # 年度が複数入っていても、指標系・区分系それぞれ最新年度だけを使う（年度の混在を防ぐ）。
        frows = [dict(r) for r in cur.execute(
            "SELECT entity_code,fiscal_year,metric,value,value_text,source_url FROM fiscal")]
        newest = {}
        for r in frows:
            g = (r["entity_code"], r["metric"] in RUIJI_METRICS)
            newest[g] = max(newest.get(g, 0), fy_num(r["fiscal_year"]))
        self.fiscal = {}
        self.ruiji = {}
        # 自治体DXの取組状況（デジタル庁）。テーブルが無い・空のときは表示しない。
        self.dx = {}
        if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dx_status'").fetchone():
            for r in cur.execute("SELECT entity_code,metric,value_text FROM dx_status"):
                self.dx.setdefault(r["entity_code"], {})[r["metric"]] = r["value_text"]
        for r in frows:
            is_r = r["metric"] in RUIJI_METRICS
            if fy_num(r["fiscal_year"]) != newest[(r["entity_code"], is_r)]:
                continue
            if is_r:
                d = self.ruiji.setdefault(r["entity_code"], {"year": r["fiscal_year"]})
                if r["metric"] == "ruiji_type":
                    d["raw"] = r["value_text"]
                elif r["metric"] == "kessan_card":
                    d["card"] = r["value_text"]
                else:
                    d["sim"] = r["value_text"]
            else:
                d = self.fiscal.setdefault(r["entity_code"],
                                           {"year": r["fiscal_year"], "source": r["source_url"], "vals": {}})
                d["vals"][r["metric"]] = r["value"]
        for code, d in self.ruiji.items():
            e = self.by_code.get(code)
            d["info"] = ruiji_info(d.get("raw"), e["type_label"] if e else "")
        self.wards_by_city = {}
        for w in self.wards:
            parent = self.by_code.get(w["parent_code"])
            w["short_name"] = w["name"][len(parent["name"]):] if parent else w["name"]
            self.wards_by_city.setdefault(w["parent_code"], []).append(w)
        for lst in self.wards_by_city.values():
            lst.sort(key=lambda w: w["kana"])

        self.block_of = {}  # 都道府県2桁 → 地方ブロック {label, slug}
        for slug, label, codes in BLOCKS:
            for co in codes:
                self.block_of[co] = {"label": label, "slug": slug}

        self.muni_count = sum(1 for c in self.cities if c["code"] not in HOPPO)
        self.n_links = cur.execute("SELECT count(*) FROM link").fetchone()[0]
        lc = cur.execute("SELECT max(last_checked) FROM link WHERE last_checked IS NOT NULL").fetchone()[0]
        self.last_checked = lc
        self.nav = self._nav_context()

    def _nav_context(self):
        """スライドナビ・統計に渡す集計データ。"""
        def cnt(pred):
            return sum(1 for c in self.cities if pred(c))
        blocks = []
        for slug, label, codes in BLOCKS:
            prefs = [{"name": p["name"], "path": p["path"], "slug": p["slug"],
                      "n": cnt(lambda c, co=codes: c["code"][:2] in co and c["code"][:2] == p["code"][:2])}
                     for p in self.prefs if p["code"][:2] in codes]
            blocks.append({"slug": slug, "label": label, "prefs": prefs, "n": len(prefs)})
        seirei = sorted((c for c in self.cities if c["type_label"] == "政令指定都市"),
                        key=lambda c: c["code"])
        tokyo23 = sorted((c for c in self.cities if c["code"][:2] == "13" and c["type_label"] == "区"),
                         key=lambda c: c["code"])
        counts = {
            "seirei": len(seirei),
            "tokyo23": len(tokyo23),
            "shi": cnt(lambda c: c["type_label"] in ("市", "政令指定都市", "中核市")),
            "chukaku": cnt(lambda c: c["type_label"] == "中核市"),
            "cho": cnt(lambda c: c["type_label"] == "町"),
            "son": cnt(lambda c: c["type_label"] == "村"),
            "union": len(self.unions),
            "pref": len(self.prefs),
        }
        teams = len(self.prefs) + self.muni_count + len(self.unions)
        return {
            "nav_blocks": blocks,
            "nav_seirei": [{"name": c["name"], "path": c["path"]} for c in seirei],
            "nav_tokyo23": [{"name": c["name"], "path": c["path"]} for c in tokyo23],
            "nav_counts": counts,
            "stat_teams": teams,
            "stat_links": self.n_links,
            "stat_last_checked": self.last_checked,
        }

    # ── 共通コンテキスト ──
    def nav_blocks(self, current_path=None):
        return self.nav["nav_blocks"]

    def base_ctx(self, path, page_title, **extra):
        ctx = dict(
            site_name=SITE_NAME, site_short=SITE_SHORT, site_url=SITE_URL, site_desc=SITE_DESC, year=YEAR,
            path=path, current_path=path, page_title=page_title,
            crumbs=extra.pop("crumbs", None), muni_count=self.muni_count, hoppo_count=len(HOPPO),
            ga4_id=GA4_ID, contact_url=CONTACT_URL, asset_ver=ASSET_VER,
        )
        ctx.update(self.nav)
        ctx.update(extra)
        return ctx

    def write(self, path, html):
        # path は "/a/b/" 形式。dist/a/b/index.html に書く。"/" はトップ。
        rel = path.strip("/")
        out_dir = os.path.join(DIST, rel)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        self.n += 1

    def write_file(self, rel, content):
        dest = os.path.join(DIST, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(content)

    def render(self, template, path, page_title, **extra):
        html = self.env.get_template(template).render(**self.base_ctx(path, page_title, **extra))
        self.write(path, html)
        return html

    # ── リンク節の状態計算 ──
    def sections_for(self, code, category):
        out = []
        ent_links = self.links.get(code, {})
        for k in self.link_kinds:
            if k["category"] != category:
                continue
            rows = ent_links.get(k["code"], [])
            ok = [r for r in rows if r["status"] == "ok"]
            seeded = [r for r in rows if r["status"] == "unchecked" and r["url"]]
            if ok:
                state, links = "ready", ok
            elif seeded:
                state, links = "seeded", seeded
            elif any(r["status"] == "none" for r in rows):
                state, links = "none", []
            elif k["phase"] == "v2.0":
                state, links = "coming", []
            else:
                state, links = "collecting", []
            out.append({"label": k["label"], "code": k["code"], "category": category,
                        "phase": k["phase"], "state": state, "links": links})
        return out

    def link_for(self, code, kind):
        """(code, kind) の代表リンク1本の表示情報。無ければ None。"""
        rows = self.links.get(code, {}).get(kind, [])
        ok = [r for r in rows if r["status"] == "ok"]
        seeded = [r for r in rows if r["status"] == "unchecked" and r["url"]]
        if ok:
            r = ok[0]
            return {"url": r["url"], "disp": url_disp(r["url"]), "title": r["title"],
                    "state": "ready", "last_checked": r["last_checked"],
                    "auto_na": not r["last_checked"] and r["id"] in self.robots_blocked}
        if seeded:
            r = seeded[0]
            return {"url": r["url"], "disp": url_disp(r["url"]), "title": r["title"],
                    "state": "seeded", "last_checked": None,
                    "auto_na": r["id"] in self.robots_blocked}
        if any(r["status"] == "none" for r in rows):
            return {"state": "none"}
        return None

    def neighbors_of(self, code):
        out = []
        for bc in self.adj.get(code, []):
            n = self.by_code.get(bc)
            if n:
                out.append({"name": n["name"], "path": n["path"],
                            "pref_name": self.pref_by2[n["code"][:2]]["name"],
                            "type_label": n["type_label"],
                            "pop": n.get("population"), "area": n.get("area_km2"),
                            "pop_disp": f"{n['population']:,}人" if n.get("population") else "",
                            "area_disp": f"{n['area_km2']:g}km²" if n.get("area_km2") else ""})
        out.sort(key=lambda x: (-(x["pop"] or 0), x["name"]))
        return out

    def ruiji_key(self, code):
        d = self.ruiji.get(code)
        return d["info"]["key"] if d and d.get("info") else None

    def ruiji_members(self, key):
        return [c for c in self.cities if self.ruiji_key(c["code"]) == key]

    def similar_meta(self, code):
        """同規模タブの説明用。類似団体区分があればその区分、無ければ人口±15%。"""
        d = self.ruiji.get(code)
        if d and d.get("info"):
            info = d["info"]
            return {"mode": "ruiji", "label": info["label"], "path": info["path"], "year": d["year"],
                    "total": len(self.ruiji_members(info["key"])) - 1}
        return {"mode": "pop"}

    def similar_of(self, code, band=0.15, limit=12):
        """同規模の市区町村。類似団体区分（#31）が同じ団体のうち人口が近い順。
        区分が無い団体（北方領土の6村など）は従来どおり人口±band%で代替。"""
        self_e = self.by_code.get(code)
        if not self_e:
            return []
        p = self_e.get("population")
        if not p:
            return []
        key = self.ruiji_key(code)
        lo, hi = p * (1 - band), p * (1 + band)
        cand = []
        for e in self.cities:
            if e["code"] == code:
                continue
            ep = e.get("population")
            if ep is None:
                continue
            if key:
                if self.ruiji_key(e["code"]) != key:
                    continue
            elif not (lo <= ep <= hi):
                continue
            cand.append((abs(ep - p), e))
        cand.sort(key=lambda t: (t[0], t[1]["name"]))
        out = []
        for _, n in cand[:limit]:
            out.append({"name": n["name"], "path": n["path"],
                        "pref_name": self.pref_by2[n["code"][:2]]["name"],
                        "type_label": n["type_label"],
                        "pop": n.get("population"), "area": n.get("area_km2"),
                        "pop_disp": f"{n['population']:,}人" if n.get("population") else "",
                        "area_disp": f"{n['area_km2']:g}km²" if n.get("area_km2") else ""})
        return out

    # ── 各ページ ──
    def _pref_short_paths(self):
        """地図ブロック用: 県/府/都を外した短縮名 → path（北海道はそのまま）。"""
        pp = {}
        for p in self.prefs:
            name = p["name"]
            short = name if name.endswith("道") else name[:-1]
            pp[short] = p["path"]
            pp[name] = p["path"]
        return pp

    def build_top(self):
        self.render("top.html", "/", "全国1,741市区町村の自治体リンク（例規集・議会会議録・広報誌）",
                    og_title=SITE_NAME, news=NEWS[:3],
                    page_desc=SITE_DESC, pp=self._pref_short_paths())

    def build_areas(self):
        for b in self.nav_blocks():
            crumbs = [{"label": SITE_SHORT, "path": "/"}, {"label": b["label"] + "地方"}]
            self.render("area.html", f"/area/{b['slug']}/", f"{b['label']}地方",
                        block=b, crumbs=crumbs)

    def _tile(self, c):
        k = c["gun"] if (c["type_label"] in ("町", "村") and c["gun"]) else c["kana"]
        pop = c.get("population")
        z = c.get("rep_zip")
        return {"name": c["name"], "path": c["path"], "kana": c["kana"], "k": k,
                "pop": pop, "pop_disp": f"{pop:,}人" if pop else "",
                "zip": f"〒{z[:3]}-{z[3:]}" if z else ""}

    def build_prefs(self):
        for p in self.prefs:
            code2 = p["code"][:2]
            cities = sorted((c for c in self.cities if c["code"][:2] == code2),
                            key=lambda c: c["code"])
            block = self.block_of.get(code2)
            seirei = [c for c in cities if c["type_label"] == "政令指定都市"]
            groups = []
            if seirei:
                groups.append({"head": "政令指定都市", "sub": f"{len(seirei)}市",
                               "tiles": [self._tile(c) for c in seirei], "ward": False})
                for c in seirei:
                    ws = self.wards_by_city.get(c["code"], [])
                    if ws:
                        ck = c["kana"]
                        wtiles = []
                        for w in ws:
                            wk = w["kana"][len(ck):] if w["kana"].startswith(ck) else w["kana"]
                            wz = w.get("rep_zip")
                            wtiles.append({"name": w["short_name"],
                                           "path": c["path"] + "#ku-" + w["slug"],
                                           "kana": wk, "k": wk, "pop": None, "pop_disp": "",
                                           "zip": f"〒{wz[:3]}-{wz[3:]}" if wz else ""})
                        groups.append({"head": f"{c['name']}の行政区", "sub": f"{len(ws)}区",
                                       "tiles": wtiles, "ward": True})
            chukaku = [c for c in cities if c["type_label"] == "中核市"]
            shi = [c for c in cities if c["type_label"] == "市"]
            cho = [c for c in cities if c["type_label"] == "町"]
            son = [c for c in cities if c["type_label"] == "村"]
            ku = [c for c in cities if c["type_label"] == "区"]  # 特別区(東京)
            for label, lst, suf in [("特別区", ku, "区"), ("中核市", chukaku, "市"),
                                    ("市", shi, "市"),
                                    ("町", cho, "町"), ("村", son, "村")]:
                if lst:
                    groups.append({"head": label, "sub": f"{len(lst)}{suf}",
                                   "tiles": [self._tile(c) for c in lst], "ward": False})
            us = [u for u in self.unions if u["code"][:2] == code2]
            self.render("pref.html", p["path"], p["name"], pref=p, block=block,
                        code5=code2 + "000",
                        official=self.link_for(p["code"], "official"),
                        office=self._office(p),
                        n_muni=len(cities), n_seirei=len(seirei),
                        groups=groups, unions=us,
                        admin=[{"kind": k, "label": lab, "link": self.link_for(p["code"], k)}
                               for k, lab in self.ADMIN_KINDS],
                        fiscal=self._fiscal(p), dx=self._dx(p),
                        entity_code=p["code"], pref_code=p["code"],
                        og_title=f"{p['name']} ｜ {SITE_NAME}",
                        page_desc=f"{p['name']}の市区町村一覧と、各自治体の公式サイト・例規集・広報誌・SNSへのリンク。")

    ADMIN_KINDS = [("reiki", "例規集"), ("gikai", "議会会議録"), ("kouhou", "広報誌"),
                   ("pubcomment", "パブリックコメント"), ("nyusatsu", "入札・契約"), ("saiyo", "職員採用"),
                   ("toukei", "統計"), ("sougou_keikaku", "総合計画"), ("dx_keikaku", "DX推進計画")]
    V2_KINDS = []
    SNS_KINDS = [("sns_x", "X", "x"), ("sns_line", "LINE", "line"),
                 ("sns_youtube", "YouTube", "yt"), ("sns_facebook", "Facebook", "fb"),
                 ("sns_instagram", "Instagram", "ig")]

    def _facts(self, e, n_adj):
        """phead-data 用の数値（データのあるものだけ）。"""
        out = []
        if e.get("population") is not None:
            out.append(("人口", f"{e['population']:,}", "人"))
        if e.get("households") is not None:
            out.append(("世帯数", f"{e['households']:,}", "世帯"))
        if e.get("area_km2") is not None:
            out.append(("面積", f"{e['area_km2']:g}", "km²"))
        if n_adj:
            out.append(("隣接", str(n_adj), "自治体"))
        return out

    # 主要財政指標の表示順・ラベル・単位（総務省「主要財政指標一覧」）
    FISCAL_META = [
        ("zaisei_ryoku", "財政力指数", ""),
        ("keijo_shushi", "経常収支比率", "%"),
        ("kosaihi", "実質公債費比率", "%"),
        ("shorai_futan", "将来負担比率", "%"),
        ("laspeyres", "ラスパイレス指数", ""),
    ]

    def _fiscal(self, e):
        """財政指標セクション用（データのある指標だけ。出典・基準年度つき）。"""
        f = self.fiscal.get(e["code"])
        items = []
        for key, label, unit in self.FISCAL_META:
            v = f["vals"].get(key) if f else None
            if v is not None:
                disp = f"{v:.2f}".rstrip("0").rstrip(".")  # 2桁に丸め＋末尾0除去で表示統一
                items.append({"label": label, "value": disp, "unit": unit})
        r = self.ruiji.get(e["code"]) or {}
        pref = self.pref_by2.get(e["code"][:2])
        extra = {}
        if r.get("info"):
            extra["ruiji"] = dict(r["info"], year=r["year"])
        if r.get("card"):
            m = re.search(r"#page=(\d+)$", r["card"])
            extra["card"] = {"url": r["card"], "page": int(m.group(1)) if m else None,
                             "year": r["year"], "pref": pref["name"] if pref else ""}
        if r.get("sim"):
            extra["sim"] = {"url": r["sim"], "year": r["year"], "pref": pref["name"] if pref else ""}
        if not items and not extra:
            return None
        return dict({"year": f["year"] if f else "", "source": f["source"] if f else "", "rows": items},
                    **extra)

    # 自治体DXの取組状況の表示順・ラベル（デジタル庁「自治体DXの取組に関するダッシュボード」）
    DX_META = [
        ("cio", "CIOの任命"), ("cio_hosa", "CIO補佐官等の任命"), ("houshin", "全体方針の策定"),
        ("taisei", "全庁的な体制構築"), ("gaibu", "外部人材の活用"), ("ikusei", "職員育成の取組"),
        ("kenshu", "全職員対象の研修"), ("ai", "AIの導入"), ("rpa", "RPAの導入"),
        ("telework", "テレワークの導入"), ("mynumber", "マイナンバーカード保有率"),
        ("online26", "子育て・介護26手続のオンライン化"), ("online32", "よく使う32手続のオンライン化"),
    ]

    def _dx(self, e):
        """自治体DXの取組状況セクション用（データのある指標だけ）。"""
        d = self.dx.get(e["code"])
        if not d:
            return None
        rows = []
        for key, label in self.DX_META:
            v = d.get(key)
            if not v:
                continue
            if v.endswith("%"):
                rows.append({"label": label, "value": v[:-1], "unit": "%"})
            else:
                rows.append({"label": label, "value": "調査なし" if v == "調査無し" else v, "unit": ""})
        return rows or None

    def _office(self, e):
        if e.get("office_addr") or e.get("office_tel") or e.get("office_zip"):
            return {"zip": e.get("office_zip"), "addr": e.get("office_addr"),
                    "tel": e.get("office_tel")}
        return None

    def build_munis(self):
        for c in self.cities:
            pref = self.pref_by2[c["code"][:2]]
            block = self.block_of.get(c["code"][:2])
            neighbors = self.neighbors_of(c["code"])
            similar = self.similar_of(c["code"])
            admin = [{"kind": k, "label": lab, "link": self.link_for(c["code"], k),
                      "chip": "外部システム" if k == "gikai" else None}
                     for k, lab in self.ADMIN_KINDS]
            sns = []
            for skind, lab, key in self.SNS_KINDS:
                lk = self.link_for(c["code"], skind)
                sns.append({"key": key, "label": lab,
                            "url": lk["url"] if lk and lk.get("url") else None})
            wards = []
            for w in self.wards_by_city.get(c["code"], []):
                wards.append({"name": w["name"], "short_name": w["short_name"],
                              "slug": w["slug"],
                              "kouhou": self.link_for(w["code"], "kouhou")})
            ctx = dict(
                e=c, pref=pref, block=block, code5=c["code"][:5],
                official=self.link_for(c["code"], "official"),
                office=self._office(c), facts=self._facts(c, len(neighbors)),
                fiscal=self._fiscal(c), dx=self._dx(c),
                admin=admin, v2=self.V2_KINDS + ([] if self._dx(c) else [("dx_torikumi", "自治体DXの取組状況")]), sns=sns, wards=wards,
                neighbors=neighbors, similar=similar, similar_meta=self.similar_meta(c["code"]),
                cards=self.cards.get(c["code"]),
                base_pop=f"{c['population']:,}人" if c.get("population") else "",
                base_area=f"{c['area_km2']:g}km²" if c.get("area_km2") else "",
                entity_code=c["code"], pref_code=pref["code"],
                og_title=f"{c['name']} ｜ {SITE_NAME}",
                page_desc=f"{pref['name']}{c['name']}の自治体リンク集。公式サイト・例規集・議会会議録・広報誌・公式SNSへのリンクと、隣接自治体・人口・面積などの基本データ。",
            )
            self.render("muni.html", c["path"], f"{c['name']}の自治体リンク（例規集・議会会議録・広報誌）",
                        **ctx)
            if neighbors:
                self.render("neighbors.html", c["path"] + "neighbors/",
                            f"{c['name']} の隣接自治体", e=c, pref=pref, block=block,
                            neighbors=neighbors,
                            base_pop=f"{c['population']:,}人" if c.get("population") else "",
                            base_area=f"{c['area_km2']:g}km²" if c.get("area_km2") else "")

    def build_unions(self):
        for p in self.prefs:
            code2 = p["code"][:2]
            block = self.block_of.get(code2)
            us = [u for u in self.unions if u["code"][:2] == code2]
            self.render("union_index.html", f"/{p['slug']}/union/",
                        f"{p['name']}の広域連合", pref=p, block=block, unions=us)
            for u in us:
                members = [self.by_code[m] for m in self.umembers.get(u["code"], [])
                           if m in self.by_code]
                self.render("union.html", u["path"], u["name"], e=u, pref=p, block=block,
                            code5=u["code"][:5],
                            official=self.link_for(u["code"], "official"),
                            admin=[{"kind": k, "label": lab, "link": self.link_for(u["code"], k)}
                                   for k, lab in [("reiki", "例規集")]],
                            members=members, n_members=len(members),
                            entity_code=u["code"], pref_code=p["code"],
                            og_title=f"{u['name']} ｜ {SITE_NAME}")

    def build_types(self):
        # index
        links = "".join(f'<li><a href="/type/{k}/">{v[0]}</a></li>' for k, v in TYPE_META.items())
        if self.ruiji:
            links += '<li><a href="/type/ruiji/">類似団体区分（人口と産業構造によるグループ）</a></li>'
        self.render("static.html", "/type/", "区分から探す",
                    body=f"<p>市区町村を区分から探せます。</p><ul class='plain-links'>{links}</ul>",
                    crumbs=[{"label": SITE_SHORT, "path": "/"}, {"label": "区分から探す"}])
        for key, (label, desc) in TYPE_META.items():
            groups = self._type_groups(key)
            total = sum(len(g["items"]) for g in groups)
            self.render("type.html", f"/type/{key}/", label, type_label=label,
                        type_desc=desc, groups=groups, total=total,
                        crumbs=[{"label": SITE_SHORT, "path": "/"},
                                {"label": "区分", "path": "/type/"}, {"label": label}])
        self.build_ruiji()

    def ruiji_pages(self):
        """類似団体区分ページの一覧 [(key, label, desc, members)]。データのある区分だけ。"""
        out = []
        for group in ("shi", "choson"):
            for p in sorted(RUIJI_POP[group]):
                for i in sorted(RUIJI_IND[group], reverse=True):
                    key = f"{group}-{p}-{i}"
                    mem = self.ruiji_members(key)
                    if mem:
                        roman = [k for k, v in ROMAN.items() if v == p][0]
                        label = f"{RUIJI_GROUP[group]} {roman}－{chr(0xFF10 + i)}"
                        desc = f"{RUIJI_POP[group][p]}／産業構造：{RUIJI_IND[group][i]}"
                        out.append((key, label, desc, mem))
        tok = self.ruiji_members("tokurei")
        if tok:
            out.append(("tokurei", "施行時特例市", "旧特例市のうち、中核市に移行していない市。", tok))
        return out

    def _ruiji_year(self):
        years = sorted({d["year"] for d in self.ruiji.values()}, key=fy_num)
        return years[-1] if years else ""

    def build_ruiji(self):
        """#31 類似団体区分: /type/ruiji/（一覧）と区分ごとのページ。"""
        pages = self.ruiji_pages()
        if not pages:
            return
        fy = self._ruiji_year()
        crumbs_base = [{"label": SITE_SHORT, "path": "/"}, {"label": "区分", "path": "/type/"}]
        src_note = (f"{fy}の市町村決算カード（総務省）の「市町村類型」による。"
                    f"区分の定義は総務省「類似団体別職員数の状況」による。")

        def table(group):
            rows = [(k, lab, desc, mem) for k, lab, desc, mem in pages if k.startswith(group + "-")]
            if not rows:
                return ""
            h = [f"<h2>{RUIJI_GROUP[group]}（{len(rows)}区分）</h2><ul class='plain-links'>"]
            for k, lab, desc, mem in rows:
                h.append(f"<li><a href='/type/ruiji/{k}/'>{lab}</a>（{len(mem)}団体）"
                         f"<br><small>{desc}</small></li>")
            h.append("</ul>")
            return "".join(h)

        special = []
        for raw, (k, path) in RUIJI_SPECIAL.items():
            n = len(self.ruiji_members(k))
            if n:
                special.append(f"<li><a href='{path}'>{raw}</a>（{n}団体）</li>")
        body = ("<p>総務省は、財政や職員数を比べやすいように、全国の市区町村を人口と産業構造（産業別就業人口の構成比）で"
                "グループに分けています（類似団体区分）。議会や財政課への説明で「同じくらいの規模の自治体」と比べるときの"
                "ものさしとして使われています。</p>"
                f"<p>政令指定都市・中核市・施行時特例市・特別区はそれぞれ1つの区分です。</p><ul class='plain-links'>{''.join(special)}</ul>"
                f"{table('shi')}{table('choson')}"
                f"<p class='muted'>{src_note}<a href='{RUIJI_SRC}' target='_blank' rel='noopener'>区分の定義（総務省）</a></p>")
        self.render("static.html", "/type/ruiji/", "類似団体区分から探す", body=body,
                    page_desc="総務省の類似団体区分（人口と産業構造による市区町村のグループ）ごとに、同じ区分の自治体を一覧できます。",
                    crumbs=crumbs_base + [{"label": "類似団体区分"}])
        for key, label, desc, mem in pages:
            groups = {}
            order = []
            for e in sorted(mem, key=lambda e: (e["code"][:2], e["kana"])):
                p2 = e["code"][:2]
                if p2 not in groups:
                    groups[p2] = {"name": self.pref_by2[p2]["name"], "slug": self.pref_by2[p2]["slug"],
                                  "items": []}
                    order.append(p2)
                groups[p2]["items"].append({"name": e["name"], "path": e["path"], "kana": e["kana"]})
            self.render("type.html", f"/type/ruiji/{key}/", f"類似団体区分 {label}",
                        type_label=f"類似団体区分 {label}",
                        type_desc=f"{desc}。{src_note}",
                        groups=[groups[p2] for p2 in order], total=len(mem),
                        page_desc=f"総務省の類似団体区分「{label}」（{desc}）に属する{len(mem)}団体の一覧。",
                        crumbs=crumbs_base + [{"label": "類似団体区分", "path": "/type/ruiji/"},
                                              {"label": label}])

    def _type_groups(self, key):
        """区分メンバーを都道府県ごとにまとめる（県別表示用）。"""
        groups = {}
        order = []
        for m in self._type_members(key):
            p2 = m["pref_order"]
            if p2 not in groups:
                groups[p2] = {"name": m["pref_name"],
                              "slug": self.pref_by2[p2]["slug"], "items": []}
                order.append(p2)
            groups[p2]["items"].append(m)
        return [groups[p2] for p2 in order]

    def _type_members(self, key):
        def deco(lst):
            out = []
            for e in sorted(lst, key=lambda e: (e["code"][:2], e["kana"])):
                pref = self.pref_by2[e["code"][:2]]
                out.append({"name": e["name"], "path": e["path"], "kana": e["kana"],
                            "pref_name": pref["name"], "pref_order": e["code"][:2],
                            "type_label": e["type_label"]})
            return out
        if key == "seirei":
            return deco([c for c in self.cities if c["type_label"] == "政令指定都市"])
        if key == "tokyo23":
            return deco([c for c in self.cities if c["code"][:2] == "13" and c["type_label"] == "区"])
        if key == "chukaku":
            return deco([c for c in self.cities if c["type_label"] == "中核市"])
        if key == "shi":
            return deco([c for c in self.cities
                         if c["type_label"] in ("市", "政令指定都市", "中核市")])
        if key == "cho":
            return deco([c for c in self.cities if c["type_label"] == "町"])
        if key == "son":
            return deco([c for c in self.cities if c["type_label"] == "村"])
        if key == "union":
            return deco(self.unions)
        return []

    ITEM_COL = {"official": "公式サイト", "reiki": "例規集", "gikai": "議会会議録",
                "kouhou": "広報誌", "sns": "公式SNS", "neighbors": "隣接自治体"}

    def _item_rows(self, key):
        """情報種別ごとの全国横断一覧用の行データと集計を作る。"""
        rows = []
        have = seeded = none = missing = 0
        for c in sorted(self.cities, key=lambda e: (e["code"][:2], e["kana"])):
            pref = self.pref_by2[c["code"][:2]]
            r = {"name": c["name"], "kana": c["kana"], "slug": c["slug"],
                 "path": c["path"], "pref_name": pref["name"], "pref_slug": pref["slug"]}
            if key == "neighbors":
                adj = self.adj.get(c["code"], [])
                r.update(kind="neighbors", count=len(adj),
                         url=(c["path"] + "neighbors/") if adj else None,
                         state="ready" if adj else "none")
                if adj:
                    have += 1
                else:
                    none += 1
            elif key == "sns":
                present = []
                for skind, lab, _k in self.SNS_KINDS:
                    lk = self.link_for(c["code"], skind)
                    if lk and lk.get("url"):
                        present.append({"label": lab, "url": lk["url"]})
                r.update(kind="sns", sns=present, state="ready" if present else "missing")
                if present:
                    have += 1
                else:
                    missing += 1
            else:
                lk = self.link_for(c["code"], key)
                if lk and lk.get("url"):
                    r.update(kind="link", url=lk["url"], disp=lk["disp"],
                             state=lk["state"], last_checked=lk.get("last_checked"))
                    if lk["state"] == "ready":
                        have += 1
                    else:
                        seeded += 1
                elif lk and lk.get("state") == "none":
                    r.update(kind="link", url=None, state="none")
                    none += 1
                else:
                    r.update(kind="link", url=None, state="missing")
                    missing += 1
            rows.append(r)
        stats = {"have": have, "seeded": seeded, "none": none,
                 "missing": missing, "total": len(rows)}
        return rows, stats

    def build_items(self):
        links = "".join(f'<li><a href="/item/{k}/">{v[0]}</a></li>' for k, v in ITEM_META.items())
        if self.ruiji:
            links += '<li><a href="/item/similar/">同規模の自治体で比べる</a></li>'
            self.build_item_similar()
        self.render("static.html", "/item/", "収録している情報",
                    body=self._items_body(),
                    crumbs=[{"label": SITE_SHORT, "path": "/"}, {"label": "収録している情報"}])
        pref_opts = [{"name": p["name"], "slug": p["slug"]} for p in self.prefs]
        for key, (label, desc) in ITEM_META.items():
            rows, stats = self._item_rows(key)
            self.render("item.html", f"/item/{key}/", f"全国の{label}一覧",
                        item_label=label, item_desc=desc, item_key=key,
                        col_label=self.ITEM_COL.get(key, label),
                        rows=rows, stats=stats, pref_opts=pref_opts,
                        page_desc=f"全国{len(rows)}市区町村の{label}へのリンクを一覧できる自治体リンク集。{desc}",
                        crumbs=[{"label": SITE_SHORT, "path": "/"},
                                {"label": "収録情報", "path": "/item/"}, {"label": label}])

    def _count_kind(self, *kinds):
        """その種別のリンク（表示中＝ok/unchecked）がある団体の数。"""
        n = 0
        for code, by_kind in self.links.items():
            if any(r["status"] in ("ok", "unchecked") and r["url"]
                   for k in kinds for r in by_kind.get(k, [])):
                n += 1
        return n

    def _items_body(self):
        """「収録している情報」ページの本文。掲載数はデータから数える。"""
        c = self._count_kind
        fmt = lambda n: f"{n:,}団体"  # noqa: E731
        n_cards = {}
        for k in ("manhole", "dam"):
            n_cards[k] = sum(1 for rs in self.cards.values() for r in rs if r["kind"] == k)
        items = [
            ("公式サイト", "各自治体の公式ウェブサイトのトップページ。", fmt(c("official")), "/item/official/"),
            ("例規集", "条例・規則などを調べられる例規集。", fmt(c("reiki")), "/item/reiki/"),
            ("議会会議録", "議会の会議録検索システム。システムが無い自治体は、その旨を表示しています。",
             fmt(c("gikai")), "/item/gikai/"),
            ("広報誌", "広報誌のバックナンバー・電子版。", fmt(c("kouhou")), "/item/kouhou/"),
            ("パブリックコメント", "計画や条例案などへの意見募集（パブリックコメント）のページ。", fmt(c("pubcomment")), None),
            ("入札・契約", "入札・契約の情報のページ。", fmt(c("nyusatsu")), None),
            ("職員採用", "職員の採用情報のページ。", fmt(c("saiyo")), None),
            ("統計", "人口・統計書など、自治体の統計のページ。", fmt(c("toukei")), None),
            ("総合計画", "自治体の最上位の計画である総合計画のページ。", fmt(c("sougou_keikaku")), None),
            ("DX推進計画", "DX推進計画（デジタル化の計画）のページ。", fmt(c("dx_keikaku")), None),
            ("公式SNS", "X・LINE・Facebook・Instagram・YouTube の公式アカウント。",
             f"X {c('sns_x'):,}／LINE {c('sns_line'):,}／Facebook {c('sns_facebook'):,}／"
             f"Instagram {c('sns_instagram'):,}／YouTube {c('sns_youtube'):,}団体", "/item/sns/"),
            ("人口・世帯数・面積", "住民基本台帳による人口・世帯数と、全国都道府県市区町村別面積調による面積。", "全団体", None),
            ("隣接自治体", "陸続きで接する自治体と、人口・面積・各種リンクを並べた比較表。", "全市区町村", "/item/neighbors/"),
            ("類似団体区分・決算カード・財政指標", "総務省の類似団体区分、決算カード・類似団体比較カード、主要財政指標。",
             "全市区町村・都道府県", "/item/similar/"),
            ("自治体DXの取組状況", "デジタル庁「自治体DXの取組に関するダッシュボード」の13項目。", "全市区町村・都道府県", None),
            ("ご当地カード", "マンホールカード・ダムカードの配布場所・配布時間・在庫状況。",
             f"マンホールカード {n_cards['manhole']:,}種／ダムカード {n_cards['dam']:,}種", "/card/"),
        ]
        out = ["<p>1団体ごとに、次の情報を掲載しています。掲載数は、いま表示しているリンク・データのある団体の数です。</p>"]
        for label, desc, num, link in items:
            more = f'　<a href="{link}">一覧を見る</a>' if link else ""
            out.append(f"<h2>{label}</h2><p>{desc}掲載数：{num}{more}</p>")
        out.append("<p>リンク先は毎日確認し、各リンクに最終確認日を表示しています。"
                   "掲載のない項目は「掲載準備中です」と表示し、順次追加しています。</p>")
        return "\n".join(out)

    def build_item_similar(self):
        """#8 /item/similar/: 同規模の自治体と比べる方法の案内と、類似団体区分の一覧。"""
        fy = self._ruiji_year()
        items = []
        for key, label, desc, mem in self.ruiji_pages():
            items.append(f"<li><a href='/type/ruiji/{key}/'>{label}</a>（{len(mem)}団体）</li>")
        for raw, (k, path) in RUIJI_SPECIAL.items():
            if raw != "施行時特例市":
                n = len(self.ruiji_members(k))
                if n:
                    items.insert(0, f"<li><a href='{path}'>{raw}</a>（{n}団体）</li>")
        body = ("<p>議会の答弁や財政課への説明では、近隣の自治体だけでなく<strong>人口や産業構造が同じくらいの自治体</strong>"
                "と比べる場面が多くあります。全国の地方自治体の色々なリンクを集めてみた件では、総務省の<strong>類似団体区分</strong>を使って同規模の自治体を探せます。</p>"
                "<h2>比べ方</h2><ul>"
                "<li><strong>各自治体のページ</strong>の「となり・同規模と比べる」で「同規模」タブを開くと、同じ類似団体区分のうち"
                "人口が近い自治体を、人口・面積の表で比べられます（表はコピーしてExcelに貼り付けできます）。</li>"
                "<li><strong>類似団体区分ごとの一覧</strong>から、同じ区分のすべての自治体を都道府県別に見られます。</li>"
                "<li><a href='/compare/'>自治体をえらんで比べる</a>では、最大5団体の人口・面積・財政指標・類似団体区分を並べて比べられます。</li>"
                "<li>各自治体ページの「財政指標」には、総務省の<strong>決算カード</strong>と<strong>類似団体比較カード</strong>"
                "（都道府県ごとのPDF）へのリンクがあります。</li>"
                "</ul>"
                f"<h2>類似団体区分の一覧</h2><ul class='plain-links'>{''.join(items)}</ul>"
                f"<p class='muted'>{fy}の市町村決算カード（総務省）の「市町村類型」による。"
                f"<a href='/type/ruiji/'>区分の説明</a></p>")
        self.render("static.html", "/item/similar/", "同規模の自治体で比べる", body=body,
                    page_desc="総務省の類似団体区分を使って、人口や産業構造が同じくらいの自治体を探し、比べられます。",
                    crumbs=[{"label": SITE_SHORT, "path": "/"},
                            {"label": "収録情報", "path": "/item/"}, {"label": "同規模の自治体で比べる"}])

    def build_cards(self):
        self.render("static.html", "/card/", "ご当地カード",
                    body="<p>ご当地カードを収録しています。</p><ul class='plain-links'>"
                         "<li><a href='/card/manhole/'>マンホールカード</a></li>"
                         "<li><a href='/card/dam/'>ダムカード</a></li></ul>",
                    crumbs=[{"label": SITE_SHORT, "path": "/"}, {"label": "ご当地カード"}])
        meta = {"manhole": ("マンホールカード", "全国のマンホールカードの配布場所・在庫状況を都道府県別に掲載しています。画像はGKP（下水道広報プラットホーム）への利用照会が済むまで掲載しません。"),
                "dam": ("ダムカード", "全国のダムカードの配布場所・配布時間を都道府県別に掲載しています。")}
        for kind, (label, desc) in meta.items():
            # 都道府県2桁 → そのkindのカードを持つ entity と枚数
            by_pref = {}
            for code, cards in self.cards.items():
                ks = [c for c in cards if c["kind"] == kind]
                if not ks:
                    continue
                e = self.by_code.get(code)
                if not e:
                    continue
                by_pref.setdefault(e["code"][:2], []).append((e, ks))
            pref_list = []
            for p in self.prefs:
                items = by_pref.get(p["code"][:2])
                if items:
                    n = sum(len(ks) for _e, ks in items)
                    pref_list.append({"name": p["name"], "path": f"/card/{kind}/{p['slug']}/",
                                      "count": n})
            crumbs = [{"label": SITE_SHORT, "path": "/"},
                      {"label": "ご当地カード", "path": "/card/"}, {"label": label}]
            self.render("card_index.html", f"/card/{kind}/", label,
                        card_label=label, kind=kind, by_pref=pref_list, card_desc=desc,
                        crumbs=crumbs)
            # 都道府県別ページ
            for p in self.prefs:
                items = by_pref.get(p["code"][:2])
                if not items:
                    continue
                munis = []
                for e, ks in sorted(items, key=lambda x: x[0]["code"]):
                    munis.append({"name": e["name"], "path": e["path"],
                                  "cards": sorted(ks, key=lambda c: c["card_no"] or "")})
                self.render("card_pref.html", f"/card/{kind}/{p['slug']}/",
                            f"{p['name']}の{label}", card_label=label, kind=kind,
                            pref=p, munis=munis,
                            crumbs=crumbs + [{"label": p["name"]}])

    def build_all(self):
        items = []
        for c in sorted(self.cities, key=lambda e: (e["code"][:2], e["kana"])):
            pref = self.pref_by2[c["code"][:2]]
            items.append({"name": c["name"], "path": c["path"], "kana": c["kana"],
                          "slug": c["slug"], "pref_name": pref["name"],
                          "type_label": c["type_label"]})
        self.render("all.html", "/all/", "全国の市区町村", items=items,
                    crumbs=[{"label": SITE_SHORT, "path": "/"}, {"label": "全国の市区町村"}])

    def build_static(self):
        pages = {
            "/about/": ("このサイトについて", ABOUT_BODY),
            "/about/sources/": ("出典", SOURCES_BODY),
            "/about/updates/": ("更新履歴", self._news_body()),
            "/about/news/": ("お知らせ", self._news_body()),
            "/privacy/": ("プライバシーポリシー", PRIVACY_BODY),
            "/contact/": ("お問い合わせ", self._contact_body()),
        }
        for path, (title, body) in pages.items():
            self.render("static.html", path, title, body=body,
                        crumbs=[{"label": SITE_SHORT, "path": "/"}, {"label": title}])
        # お知らせの詳細ページ（/about/news/年月日-時分/）
        for n in NEWS:
            text = n["text"].replace("{muni_count}", f"{self.muni_count:,}")
            body = (f'<p class="muted">{_jp_date(n["date"])} {n["time"]}　{n["cat"]}</p>'
                    + n["body"].replace("{muni_count}", f"{self.muni_count:,}")
                    + f'<p class="more"><a class="pill" style="display:inline-flex" href="{n["link"][0]}">'
                      f'{n["link"][1]}<span class="go">›</span></a></p>'
                    + '<p><a href="/about/news/">お知らせの一覧へ</a></p>')
            title = text.split("。")[0]
            self.render("static.html", n["path"], title, body=body,
                        crumbs=[{"label": SITE_SHORT, "path": "/"},
                                {"label": "お知らせ", "path": "/about/news/"}, {"label": title}])

    def _news_body(self):
        """お知らせ（NEWS）の一覧。新しい順。"""
        import html as _h
        rows = []
        for n in NEWS:
            text = n["text"].replace("{muni_count}", f"{self.muni_count:,}")
            rows.append(f'<li>{_jp_date(n["date"])}　{_h.escape(n["cat"])}：'
                        f'<a href="{n["path"]}">{_h.escape(text)}</a></li>')  # 詳細ページへ
        return "<ul>\n" + "\n".join(rows) + "\n</ul>\n"

    def _contact_body(self):
        if CONTACT_URL:
            return ('<p>リンク切れのご報告・掲載内容に関するお問い合わせは、下記のフォームよりお願いします。</p>'
                    f'<p class="more"><a class="pill" style="display:inline-flex" href="{CONTACT_URL}" '
                    'target="_blank" rel="noopener">お問い合わせフォームを開く<span class="go">›</span></a></p>'
                    '<p class="muted">リンク切れの報告も同じフォームで受け付けています。</p>')
        return CONTACT_BODY

    def build_404(self):
        html = self.env.get_template("404.html").render(
            **self.base_ctx("/404.html", "ページが見つかりません"))
        self.write_file("404.html", html)

    def build_search_index(self):
        data = []
        for c in self.cities:
            pref = self.pref_by2[c["code"][:2]]
            data.append({"n": c["name"], "k": c["kana"], "s": c["slug"],
                         "p": pref["name"], "u": c["path"]})
        for u in self.unions:
            pref = self.pref_by2[u["code"][:2]]
            data.append({"n": u["name"], "k": "", "s": "koukikourei",
                         "p": pref["name"], "u": u["path"]})
        self.write_file(os.path.join("data", "search-index.json"),
                        json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def build_compare(self):
        """#4 任意比較: 検索インデックス（軽量）＋県別データ（遅延フェッチ用）＋ /compare/ ページ。"""
        idx = []
        bypref = {}
        for e in self.entities:
            if e["kind"] not in ("city", "pref"):
                continue
            pref = self.pref_by2[e["code"][:2]]
            idx.append({"c": e["code"], "n": e["name"], "k": e.get("kana", ""),
                        "p": pref["name"], "u": e["path"]})
            f = self.fiscal.get(e["code"])
            r = self.ruiji.get(e["code"]) or {}
            bypref.setdefault(e["code"][:2], {})[e["code"]] = {
                "n": e["name"], "p": pref["name"], "t": e.get("type_label") or "",
                "u": e["path"], "pop": e.get("population"), "area": e.get("area_km2"),
                "f": (f["vals"] if f else {}),
                "r": (r["info"]["label"] if r.get("info") else ""),
            }
        self.write_file(os.path.join("data", "compare-index.json"),
                        json.dumps(idx, ensure_ascii=False, separators=(",", ":")))
        for p2, recs in bypref.items():
            self.write_file(os.path.join("data", "compare", p2 + ".json"),
                            json.dumps(recs, ensure_ascii=False, separators=(",", ":")))
        fys = sorted({d["year"] for d in self.fiscal.values()}, key=fy_num)
        self.render("compare.html", "/compare/", "自治体をえらんで比べる",
                    fiscal_year=fys[-1] if fys else "", ruiji_year=self._ruiji_year(),
                    og_title=f"自治体をえらんで比べる ｜ {SITE_NAME}",
                    page_desc="全国の市区町村・都道府県を最大5つえらんで、人口・面積・財政指標を一覧で比較できます。")

    def build_sitemap(self):
        urls = []
        for e in self.entities:
            if e["phase"] != "v1.0":
                continue
            urls.append((SITE_URL + e["path"], e.get("updated_at", "")[:10] or TODAY))
        for extra in ["/", "/all/", "/type/", "/item/", "/card/", "/card/manhole/",
                      "/card/dam/", "/about/", "/about/sources/", "/about/updates/", "/about/news/",
                      "/privacy/", "/contact/"]:
            urls.append((SITE_URL + extra, TODAY))
        for n in NEWS:
            urls.append((SITE_URL + n["path"], n["date"]))
        for b in self.nav_blocks():
            urls.append((SITE_URL + f"/area/{b['slug']}/", TODAY))
        for k in ITEM_META:
            urls.append((SITE_URL + f"/item/{k}/", TODAY))
        for k in TYPE_META:
            urls.append((SITE_URL + f"/type/{k}/", TODAY))
        if self.ruiji:
            urls.append((SITE_URL + "/type/ruiji/", TODAY))
            urls.append((SITE_URL + "/item/similar/", TODAY))
            for key, _l, _d, _m in self.ruiji_pages():
                urls.append((SITE_URL + f"/type/ruiji/{key}/", TODAY))
        body = ['<?xml version="1.0" encoding="UTF-8"?>',
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for loc, lastmod in urls:
            body.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod></url>")
        body.append("</urlset>")
        self.write_file("sitemap.xml", "\n".join(body))
        return len(urls)

    def build_robots(self):
        txt = (f"User-agent: *\nDisallow: /_recheck/\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n")
        self.write_file("robots.txt", txt)

    def build_htaccess(self):
        """Apache（共有ホスティング）向け。GitHub Actions→FTPでdistごと配信する前提。
        www→apex / http→https / 末尾スラッシュ付与 / 404 / 最小セキュリティヘッダ。"""
        host = SITE_URL.replace("https://", "").replace("http://", "").strip("/")
        txt = f"""Options -Indexes
DirectoryIndex index.html
AddDefaultCharset UTF-8

<IfModule mod_rewrite.c>
  RewriteEngine On

  # http → https（プロキシ環境も考慮）
  RewriteCond %{{HTTPS}} !=on [OR]
  RewriteCond %{{HTTP:X-Forwarded-Proto}} =http
  RewriteRule ^ https://%{{HTTP_HOST}}%{{REQUEST_URI}} [L,R=301]

  # www. → apex（{host}）
  RewriteCond %{{HTTP_HOST}} ^www\\.(.+)$ [NC]
  RewriteRule ^ https://%1%{{REQUEST_URI}} [L,R=301]

  # 実在ディレクトリは末尾スラッシュを付与（/a/b → /a/b/）
  RewriteCond %{{REQUEST_FILENAME}} -d
  RewriteCond %{{REQUEST_URI}} !/$
  RewriteRule ^ %{{REQUEST_URI}}/ [R=301,L]
</IfModule>

ErrorDocument 404 /404.html

<IfModule mod_headers.c>
  Header set X-Content-Type-Options "nosniff"
  Header set Referrer-Policy "strict-origin-when-cross-origin"
</IfModule>

<IfModule mod_expires.c>
  ExpiresActive On
  ExpiresByType text/css "access plus 7 days"
  ExpiresByType application/javascript "access plus 7 days"
  ExpiresByType image/svg+xml "access plus 7 days"
</IfModule>
"""
        self.write_file(".htaccess", txt)

    def build_recheck(self):
        """国内からの再確認窓口（src/server/recheck.php）を /_recheck/ に置く。
        合言葉は GitHub の秘密情報 RECHECK_TOKEN（環境変数 RECHECK_TOKEN）から、ハッシュだけを config.php に書く。
        合言葉が無いとき（手元での生成など）は config.php を作らない＝窓口は「未設定」で応答して何もしない。"""
        import hashlib
        d = os.path.join(DIST, "_recheck")
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(os.path.join(SRC, "server", "recheck.php"), os.path.join(d, "index.php"))
        token = os.environ.get("RECHECK_TOKEN", "")
        if token:
            h = hashlib.sha256(token.encode("utf-8")).hexdigest()
            with open(os.path.join(d, "config.php"), "w", encoding="utf-8") as f:
                f.write(f"<?php\nreturn '{h}';\n")
        with open(os.path.join(d, ".htaccess"), "w", encoding="utf-8") as f:
            f.write("DirectoryIndex index.php\n"
                    "<Files \"config.php\">\n"
                    "  <IfModule mod_authz_core.c>\n    Require all denied\n  </IfModule>\n"
                    "  <IfModule !mod_authz_core.c>\n    Order allow,deny\n    Deny from all\n  </IfModule>\n"
                    "</Files>\n")

    def copy_static(self):
        dest = os.path.join(DIST, "assets")
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(STATIC, dest)

    def run(self):
        if os.path.exists(DIST):
            shutil.rmtree(DIST)
        os.makedirs(DIST, exist_ok=True)
        self.build_top()
        self.build_areas()
        self.build_prefs()
        self.build_munis()
        self.build_unions()
        self.build_types()
        self.build_items()
        self.build_cards()
        self.build_all()
        self.build_static()
        self.build_404()
        self.build_search_index()
        self.build_compare()
        n_sitemap = self.build_sitemap()
        self.build_robots()
        self.build_htaccess()
        self.build_recheck()
        self.copy_static()
        self.conn.close()
        print(f"生成完了: {self.n} ページ ＋ sitemap({n_sitemap} URL)・robots・search-index・404")
        print(f"出力先: {DIST}")


# ── 固定ページ本文 ──
ABOUT_BODY = """
<p>全国の地方自治体の色々なリンクを集めてみた件は、全国1,741市区町村を網羅した<strong>自治体リンク集</strong>です。各自治体の例規集・議会会議録・広報誌・公式サイト・公式SNSへのリンクを、虫食いなく一か所に集めています。「自治体のリンクを探すならここ」を目指しています。</p>

<h2>このサイトでできること</h2>
<ul>
<li>市区町村ごとに、<strong>公式サイト・例規集・議会会議録・広報誌・公式SNS</strong>へのリンクをまとめて確認できます。</li>
<li>都道府県・地方・区分（政令指定都市／中核市／市／町／村／東京23区／広域連合）から、目的の自治体リンクをたどれます。</li>
<li>各自治体ページから<strong>隣接する自治体</strong>へワンタップで移動できます。</li>
<li>人口・世帯数・面積・役場所在地などの基本データも併記しています。</li>
</ul>

<h2>想定している使い方</h2>
<p>主な利用シーンは、<strong>自分のまちではなく、隣のまちが何をしているかを調べる</strong>ことです。例規や広報の横並び比較、近隣自治体の取り組み調査など、自治体職員の実務での利用を想定しています。もちろん、どなたでも全国の自治体情報の入口としてお使いいただけます。</p>

<h2>他の自治体リンク集との違い</h2>
<ul>
<li><strong>隣接自治体への導線</strong>を中核に置いています。地図とデータで近隣の自治体へすぐ辿れます。</li>
<li>リンクが最後に生きていた<strong>「最終確認日」</strong>を団体ごとに表示します。リンク先は定期的に確認しています。</li>
<li>スマートフォンでも使える、軽くて速い静的サイトです。</li>
</ul>

<h2>収録している情報</h2>
<p>1団体につき、公式サイト・例規集・議会会議録・広報誌・パブリックコメント・公式SNS・人口・隣接自治体を収録しています。入札・契約、職員採用、統計、総合計画、DX推進計画のページや、自治体DXの取組状況（デジタル庁のデータ）も掲載しています。マンホールカード・ダムカードなどのご当地カードも都道府県別に一覧できます。</p>

<h2>データの出典</h2>
<p>総務省の全国地方公共団体コードをはじめとする一次データと、全国の自治体リンク集を出典としています。くわしくは<a href="/about/sources/">データの出典</a>をご覧ください。</p>
"""
SOURCES_BODY = """
<p>掲載情報は、以下の一次データ・リンク集をもとにしています。</p>
<ul>
<li>総務省 全国地方公共団体コード／一部事務組合等コード</li>
<li>e-Stat（人口・世帯）／国土地理院 全国都道府県市区町村別面積調</li>
<li>国土数値情報 市区町村役場(P34)・行政区域(N03)</li>
<li>日本郵便 郵便番号データ（郡名の補完に使用）</li>
<li>RILG 全国自治体例規集／uub.jp 公式メディア／app-mints 会議録検索MAP</li>
<li>GKP マンホールカード／国土交通省 ダムカード</li>
<li>総務省 地方公共団体の主要財政指標一覧（財政指標）</li>
<li>総務省 市町村決算カード（類似団体区分・決算カードへのリンク）／類似団体比較カード</li>
<li>総務省 類似団体別職員数の状況（類似団体区分の定義）</li>
</ul>
<p>特に uub.jp は個人が長年かけて収集された成果です。敬意を表し、相互リンクを申し出る方針です。</p>
"""
# トップページ「更新のお知らせ」と更新履歴ページの中身（新しい順）。
# 新機能・新しい情報を追加したときだけ書く（毎日のリンク確認などの定常作業は書かない）。
# date は実装を公開した日。text の {muni_count} は市区町村数に置き換わる。
NEWS = [
    {"slug": "20261029-1530", "cat": "お知らせ",
     "text": "政令指定都市の行政区ごとの広報誌へのリンクを掲載しました",
     "body": "<p>政令指定都市のページの「行政区」に、区ごとの広報誌（区版・区役所だよりなど）へのリンクを掲載しました。"
             "全国20市171区のうち、142区（公開時点）に掲載しています。</p>"
             "<p>区独自の広報誌がなく、市の広報誌の中に区のページがある区は「掲載準備中です」と表示しています。</p>"
             "<p>あわせて、行政区の「例規集」の欄をなくしました。条例や規則は市が定めるため、例規集は市のページに掲載しています。</p>",
     "link": ("/item/", "収録している情報")},
    {"slug": "20261028-1834", "cat": "新機能",
     "text": "入札・契約、職員採用、統計、総合計画、DX推進計画のページへのリンクを掲載しました",
     "body": "<p>各自治体のページの「行政・法務の情報」に、入札・契約、職員採用、統計、総合計画、DX推進計画のページへのリンクを加えました。"
             "例規集や広報誌と同じカードで表示し、リンク先は毎日確認して最終確認日を表示します。</p>"
             "<h2>掲載した数（公開時点）</h2><ul><li>入札・契約：1,100団体</li><li>職員採用：1,022団体</li>"
             "<li>統計：727団体</li><li>総合計画：569団体</li><li>DX推進計画：35団体</li></ul>"
             "<p>掲載のない自治体は「掲載準備中です」と表示しています。各自治体のページを確認しながら、順次追加していきます。</p>",
     "link": ("/item/", "収録している情報")},
    {"slug": "20261028-1518", "cat": "新機能",
     "text": "自治体DXの取組状況を掲載しました",
     "body": "<p>全国の市区町村と都道府県のページに「自治体DXの取組状況」を加えました。"
             "デジタル庁「自治体DXの取組に関するダッシュボード」のデータをもとに、次の13項目を表示します。</p>"
             "<ul><li>DXの推進体制：CIOの任命、CIO補佐官等の任命、全体方針の策定、全庁的な体制構築、外部人材の活用、職員育成の取組、全職員対象の研修</li>"
             "<li>業務のDX：AIの導入、RPAの導入、テレワークの導入</li>"
             "<li>住民サービスのDX：マイナンバーカード保有率、子育て・介護26手続のオンライン化、よく使う32手続のオンライン化</li></ul>"
             "<p>調査時点は項目により異なります。くわしくはデジタル庁のデータの定義をご覧ください。</p>",
     "link": ("/item/", "収録している情報")},
    {"slug": "20261028-1419", "cat": "新機能",
     "text": "パブリックコメント（意見募集）のページへのリンクを掲載しました",
     "body": "<p>1,179団体（公開時点）のパブリックコメント（意見募集）のページへのリンクを、各自治体のページの「行政・法務の情報」に加えました。"
             "例規集や広報誌と同じカードで表示し、リンク先は毎日確認して最終確認日を表示します。</p>"
             "<p>掲載のない自治体は、各自治体のページを確認しながら順次追加していきます。</p>",
     "link": ("/item/", "収録している情報")},
    {"slug": "20261024-2321", "cat": "新機能",
     "text": "類似団体区分と決算カードを掲載しました。人口規模などが近い自治体を探して比べられます",
     "body": "<p>総務省の類似団体区分（人口規模と産業構造による区分）を各自治体のページに掲載し、同じ区分の自治体を一覧できるようにしました。"
             "議会での説明や資料づくりで、規模の近い自治体と比べるときに使えます。</p>"
             "<p>あわせて、各自治体のページから総務省の決算カード・類似団体比較カード（PDF）を開けるようにしました。</p>",
     "link": ("/item/similar/", "同規模の自治体で比べる")},
    {"slug": "20261023-1558", "cat": "新機能",
     "text": "自治体をえらんで比べる機能と、財政指標の掲載を始めました",
     "body": "<p>好きな自治体を選んで、人口・面積・財政指標を並べて比べられるページを加えました。</p>"
             "<p>あわせて、各自治体のページに総務省「主要財政指標一覧」の財政指標（財政力指数・経常収支比率・実質公債費比率・将来負担比率・ラスパイレス指数）を掲載しました。</p>",
     "link": ("/compare/", "自治体をえらんで比べる")},
    {"slug": "20261022-2008", "cat": "新機能",
     "text": "全国の市区町村の一覧と、隣接自治体の比較表を追加しました",
     "body": "<p>全国の市区町村を一覧できるページを加えました。都道府県や五十音で探せます。</p>"
             "<p>あわせて、各自治体の「隣接自治体」のページで、隣のまちの人口・面積と各種リンクを表で比べられるようにしました。</p>",
     "link": ("/all/", "全国の市区町村")},
    {"slug": "20261021-1851", "cat": "お知らせ",
     "text": "サイトを公開しました。全国{muni_count}市区町村の公式サイト・例規集・議会会議録・広報誌・公式SNSへのリンクを掲載しています",
     "body": "<p>全国の地方自治体の色々なリンクを集めてみた件を公開しました。全国{muni_count}市区町村と47都道府県・47広域連合について、"
             "公式サイト・例規集・議会会議録・広報誌・公式SNSへのリンクを、人口・面積・隣接自治体とあわせて掲載しています。</p>"
             "<p>マンホールカード・ダムカードも都道府県別に一覧できます。</p>",
     "link": ("/about/", "このサイトについて")},
]
for _n in NEWS:
    _n["date"] = f"{_n['slug'][:4]}-{_n['slug'][4:6]}-{_n['slug'][6:8]}"
    _n["time"] = f"{int(_n['slug'][9:11])}時{_n['slug'][11:13]}分"
    _n["path"] = f"/about/news/{_n['slug']}/"


def _jp_date(d: str) -> str:
    y, m, dd = d.split("-")
    return f"{int(y)}年{int(m)}月{int(dd)}日"

PRIVACY_BODY = """
<p>本サイトはアクセス解析に Google アナリティクス（GA4）を利用します。GA4 は Cookie を使用し、個人を特定しない形で利用状況を収集します。</p>
<p>収集した情報は、サイトの改善と利用状況の把握のために使用します。ブラウザの設定で Cookie を無効にすることで収集を拒否できます。</p>
<p>外部サイトへのリンクをクリックした回数を、改善のために計測する場合があります。計測のためのリダイレクトは行わず、公式サイトへ直接リンクしています。</p>
"""
CONTACT_BODY = """
<p>リンク切れのご報告・掲載内容に関するお問い合わせは、外部フォームよりお願いします。</p>
<p class="muted">※ お問い合わせフォーム（Google フォーム等）のURLは公開時に設定します。</p>
"""


if __name__ == "__main__":
    Builder().run()
