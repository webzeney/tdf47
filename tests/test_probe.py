"""probe.py のテスト（通信なし。fetch_page / allowed_by_robots を差し替えて DB のコピーで動かす）。

実行: python3 tests/test_probe.py  （pytest でも可）
"""

import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
sys.path.insert(0, SRC)

import db  # noqa: E402
import probe  # noqa: E402


def test_sns_key_same_account():
    k = probe.sns_key
    assert k("https://twitter.com/Warabi_City/") == k("https://x.com/warabi_city") == ("x.com", "warabi_city")
    assert k("https://www.instagram.com/kani_city.kouhou/?hl=ja") == ("instagram.com", "kani_city.kouhou")
    assert k("https://www.facebook.com/profile.php?id=10006") == ("facebook.com", "id:10006")
    assert k("https://m.facebook.com/pages/Foo/12345") == ("facebook.com", "id:12345")
    assert k("https://www.facebook.com/warabicity") == ("facebook.com", "warabicity")
    assert k("https://www.youtube.com/channel/UCabc") == ("youtube.com", "channel/ucabc")
    assert k("https://www.youtube.com/@warabi") == ("youtube.com", "@warabi")
    assert k("https://page.line.me/abc123") == k("https://line.me/R/ti/p/@abc123") == ("line", "abc123")


def test_sns_key_not_account():
    k = probe.sns_key
    assert k("https://twitter.com/intent/tweet?text=x") is None
    assert k("https://www.facebook.com/sharer/sharer.php?u=x") is None
    assert k("https://www.youtube.com/watch?v=xyz") is None
    assert k("https://www.city.warabi.saitama.jp/") is None
    assert k("https://x.com/") is None


def test_sns_keys_in_html():
    html = ('<a href="https://twitter.com/Warabi_City">X</a>'
            '<a href=\'https://www.instagram.com/warabi/\'>IG</a>'
            '<a href="/kurashi/">暮らし</a><a href="https://twitter.com/share?url=a&amp;b=c">共有</a>')
    keys = probe.sns_keys_in(html, "https://www.city.warabi.saitama.jp/")
    assert keys == {("x.com", "warabi_city"), ("instagram.com", "warabi")}


def _copy_db():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.sqlite")
    shutil.copy(db.DB_PATH, p)
    return d, p


def _pick_entity(conn):
    """公式サイト(ok/unchecked)と SNS(ok/unchecked) を両方持つ団体を1つ。"""
    return conn.execute(
        "SELECT o.entity_code AS code, o.id AS oid, o.url AS ourl FROM link o "
        "WHERE o.kind_code='official' AND o.status IN ('ok','unchecked') AND o.url IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM link s WHERE s.entity_code=o.entity_code AND s.kind_code='sns_x' "
        "AND s.status IN ('ok','unchecked') AND s.url IS NOT NULL) LIMIT 1").fetchone()


def test_official_listing_and_review_transition():
    d, p = _copy_db()
    try:
        conn = db.connect(p)
        e = _pick_entity(conn)
        sx = conn.execute("SELECT id,url FROM link WHERE entity_code=? AND kind_code='sns_x' "
                          "AND status IN ('ok','unchecked')", (e["code"],)).fetchone()
        before = conn.execute("SELECT status,last_checked FROM link WHERE id=?", (sx["id"],)).fetchone()
        lp_before = probe.last_probe(conn, sx["id"])
        # 1回目：掲載あり → 記録のみ（レビュー行なし）
        rv = probe.check_official_listing(conn, e["code"], "X", e["ourl"], f'<a href="{sx["url"]}">X</a>')
        assert rv == []
        last = conn.execute("SELECT error FROM probe WHERE link_id=? ORDER BY id DESC LIMIT 1", (sx["id"],)).fetchone()
        assert last["error"] == probe.OFFICIAL_LISTED
        # 2回目：掲載なし → 「掲載あり→なし」なのでレビュー行が出る
        rv = probe.check_official_listing(conn, e["code"], "X", e["ourl"], "<p>no sns</p>")
        assert [r["link_id"] for r in rv if r["kind"] == "sns_x"] == [sx["id"]]
        # 3回目：掲載なしのまま → 新たなレビュー行は出ない
        rv = probe.check_official_listing(conn, e["code"], "X", e["ourl"], "<p>no sns</p>")
        assert not [r for r in rv if r["link_id"] == sx["id"]]
        # link の状態・最終確認日は変えない
        after = conn.execute("SELECT status,last_checked FROM link WHERE id=?", (sx["id"],)).fetchone()
        assert tuple(before) == tuple(after)
        # 照合の記録は「前回の確認結果」に使わない
        assert probe.last_probe(conn, sx["id"]) == lp_before
        conn.close()
    finally:
        shutil.rmtree(d)


def test_markers_do_not_affect_queue_or_last_probe():
    d, p = _copy_db()
    try:
        conn = db.connect(p)
        e = _pick_entity(conn)
        sx = conn.execute("SELECT id FROM link WHERE entity_code=? AND kind_code='sns_x' "
                          "AND status IN ('ok','unchecked')", (e["code"],)).fetchone()["id"]
        conn.execute("DELETE FROM probe WHERE link_id=?", (sx,))
        conn.execute("INSERT INTO probe (link_id,checked_at,changed,error) VALUES (?,?,0,?)",
                     (sx, "2099-01-01T00:00:00", probe.OFFICIAL_LISTED))
        conn.commit()
        assert probe.last_probe(conn, sx) is None
        # 照合の記録しかないリンクは「一度も試していない」扱いで先頭側に来る
        sql = ("SELECT l.id FROM link l LEFT JOIN (SELECT link_id, MAX(checked_at) AS last_try FROM probe "
               "WHERE error IS NULL OR error NOT IN (?,?) GROUP BY link_id) t ON t.link_id = l.id "
               "WHERE l.id=? AND t.last_try IS NULL")
        assert conn.execute(sql, probe.OFFICIAL_MARKERS + (sx,)).fetchone() is not None
        conn.close()
    finally:
        shutil.rmtree(d)


def test_prune_keeps_both_kinds():
    d, p = _copy_db()
    try:
        conn = db.connect(p)
        lid = conn.execute("SELECT id FROM link WHERE url IS NOT NULL LIMIT 1").fetchone()["id"]
        conn.execute("DELETE FROM probe WHERE link_id=?", (lid,))
        for i in range(5):
            conn.execute("INSERT INTO probe (link_id,checked_at,changed,error) VALUES (?,?,0,?)",
                         (lid, f"2099-01-0{i+1}T00:00:00", probe.ROBOTS_ERR))
            conn.execute("INSERT INTO probe (link_id,checked_at,changed,error) VALUES (?,?,0,?)",
                         (lid, f"2099-01-0{i+1}T00:00:01", probe.OFFICIAL_MISSING))
        conn.commit()
        probe.prune_history(conn)
        rows = conn.execute("SELECT error FROM probe WHERE link_id=?", (lid,)).fetchall()
        assert sum(r[0] == probe.ROBOTS_ERR for r in rows) == 3
        assert sum(r[0] == probe.OFFICIAL_MISSING for r in rows) == 3
        conn.close()
    finally:
        shutil.rmtree(d)


def test_main_run_with_fake_network():
    """main() を通しで動かす：公式サイトの確認で SNS 照合が走り、robots 不許可は記録だけ残る。"""
    d, p = _copy_db()
    saved = (db.DB_PATH, probe.fetch_page, probe.allowed_by_robots, probe.DELAY_SEC,
             probe.REVIEW_CSV, sys.argv)
    try:
        conn = db.connect(p)
        e = _pick_entity(conn)
        sx_url = conn.execute("SELECT url FROM link WHERE entity_code=? AND kind_code='sns_x' "
                              "AND status IN ('ok','unchecked')", (e["code"],)).fetchone()["url"]
        # この団体の公式サイトが最初に確認されるよう、履歴を消す
        conn.execute("DELETE FROM probe WHERE link_id=?", (e["oid"],))
        conn.commit()
        conn.close()

        def fake_fetch_page(url, max_bytes=200_000):
            return 200, url, "テスト", None, f'<a href="{sx_url}">X</a>'

        db.DB_PATH = p
        probe.fetch_page = fake_fetch_page
        probe.allowed_by_robots = lambda u: "x.com" not in u and "facebook.com" not in u
        probe.DELAY_SEC = 0
        probe.REVIEW_CSV = os.path.join(d, "review.csv")
        sys.argv = ["probe.py", "--limit", "0"]
        orig_connect = db.connect
        db.connect = lambda path=p: orig_connect(path)
        try:
            probe.main()
        finally:
            db.connect = orig_connect
        conn = db.connect(p)
        n = conn.execute("SELECT count(*) FROM probe WHERE error=? AND checked_at=?",
                         (probe.OFFICIAL_LISTED, probe.NOW)).fetchone()[0]
        assert n >= 1
        assert os.path.exists(probe.REVIEW_CSV)
        conn.close()
    finally:
        (db.DB_PATH, probe.fetch_page, probe.allowed_by_robots, probe.DELAY_SEC,
         probe.REVIEW_CSV, sys.argv) = saved
        shutil.rmtree(d)


if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            n += 1
    print(f"OK {n} tests")
