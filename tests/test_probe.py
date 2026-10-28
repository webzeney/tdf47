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


def test_transient_and_2xx_classification():
    t, inc = probe.is_transient, probe.is_inconclusive
    assert t(None, "URLError: <urlopen error timed out>")
    assert t(None, "TimeoutError: The read operation timed out")
    assert t(None, "RemoteDisconnected: Remote end closed connection without response")
    assert t(None, "ConnectionResetError: [Errno 104] Connection reset by peer")
    assert t(503, "HTTPError 503") and t(502, "HTTPError 502") and t(504, "HTTPError 504")
    assert not t(404, "HTTPError 404")
    assert not t(500, "HTTPError 500")
    assert not t(None, "URLError: <urlopen error [Errno -2] Name or service not known>")
    assert not t(200, None)
    # 200以外の成功応答はどのホストでも「判定できない」
    assert inc("https://www.town.example.lg.jp/", 202, None)
    assert inc("https://www.town.example.lg.jp/", 204, None)
    assert not inc("https://www.town.example.lg.jp/", 200, None)
    assert not inc("https://www.town.example.lg.jp/", 404, "HTTPError 404")
    assert not inc("https://www.town.example.lg.jp/", 301, None)


def _run_once(p, d, url_result, now):
    """対象リンクだけを1件確認する（--limit 1。対象を先頭にするため呼び出し側で履歴を調整）。"""
    saved = (db.DB_PATH, probe.fetch_page, probe.allowed_by_robots, probe.DELAY_SEC,
             probe.REVIEW_CSV, probe.NOW, sys.argv)
    orig_connect = db.connect
    try:
        db.DB_PATH = p
        probe.fetch_page = lambda url, max_bytes=200_000: url_result(url)
        probe.allowed_by_robots = lambda u: True
        probe.DELAY_SEC = 0
        probe.REVIEW_CSV = os.path.join(d, "review.csv")
        probe.NOW = now
        sys.argv = ["probe.py", "--limit", "1"]
        db.connect = lambda path=p: orig_connect(path)
        probe.main()
    finally:
        db.connect = orig_connect
        (db.DB_PATH, probe.fetch_page, probe.allowed_by_robots, probe.DELAY_SEC,
         probe.REVIEW_CSV, probe.NOW, sys.argv) = saved


def _prepare_target(p, status):
    """ok の通常リンクを1件選び、状態を設定して履歴を消す。
    確認の対象がこの1件だけになるよう、DBのコピー上でほかのリンクは対象外（none）にする。"""
    conn = db.connect(p)
    r = conn.execute("SELECT id, url FROM link WHERE status='ok' AND kind_code='reiki' "
                     "AND url LIKE 'https://%' AND url NOT LIKE '%d1-law.com%' "
                     "AND url NOT LIKE '%legal-square.com%' LIMIT 1").fetchone()
    conn.execute("UPDATE link SET status='none' WHERE id<>?", (r["id"],))
    conn.execute("DELETE FROM probe WHERE link_id=?", (r["id"],))
    conn.execute("UPDATE link SET status=? WHERE id=?", (status, r["id"]))
    conn.commit()
    conn.close()
    return r["id"], r["url"]


def _status(p, lid):
    conn = db.connect(p)
    s = conn.execute("SELECT status FROM link WHERE id=?", (lid,)).fetchone()[0]
    conn.close()
    return s


TIMEOUT_RES = lambda url: (None, url, None, "URLError: <urlopen error timed out>", "")  # noqa: E731
OK_RES = lambda url: (200, url, "テスト", None, "")  # noqa: E731
NOTFOUND_RES = lambda url: (404, url, None, "HTTPError 404", "")  # noqa: E731
ACCEPTED_RES = lambda url: (202, url, None, None, "")  # noqa: E731


def test_timeout_needs_two_consecutive_failures():
    d, p = _copy_db()
    try:
        lid, _ = _prepare_target(p, "ok")
        _run_once(p, d, TIMEOUT_RES, "2099-01-01T00:00:00")
        assert _status(p, lid) == "ok"          # 1回目の時間切れ：そのまま
        _run_once(p, d, TIMEOUT_RES, "2099-01-02T00:00:00")
        assert _status(p, lid) == "dead"        # 2回続けて失敗：リンク切れ
        _run_once(p, d, OK_RES, "2099-01-03T00:00:00")
        assert _status(p, lid) == "ok"          # 復活
        _run_once(p, d, TIMEOUT_RES, "2099-01-04T00:00:00")
        assert _status(p, lid) == "ok"          # 成功のあとの1回目：そのまま
    finally:
        shutil.rmtree(d)


def test_unchecked_first_timeout_stays_unchecked_and_404_is_dead():
    d, p = _copy_db()
    try:
        lid, _ = _prepare_target(p, "unchecked")
        _run_once(p, d, TIMEOUT_RES, "2099-01-01T00:00:00")
        assert _status(p, lid) == "unchecked"
        _run_once(p, d, NOTFOUND_RES, "2099-01-02T00:00:00")
        assert _status(p, lid) == "dead"        # 404 は1回でリンク切れ（従来どおり）
        _run_once(p, d, TIMEOUT_RES, "2099-01-03T00:00:00")
        assert _status(p, lid) == "dead"        # 前回も失敗：リンク切れのまま
    finally:
        shutil.rmtree(d)


def test_202_is_inconclusive():
    d, p = _copy_db()
    try:
        lid, _ = _prepare_target(p, "ok")
        for i in range(3):
            _run_once(p, d, ACCEPTED_RES, f"2099-01-0{i+1}T00:00:00")
            assert _status(p, lid) == "ok"
        conn = db.connect(p)
        lc = conn.execute("SELECT last_checked FROM link WHERE id=?", (lid,)).fetchone()[0]
        conn.close()
        assert lc != "2099-01-03"               # 最終確認日は更新しない
    finally:
        shutil.rmtree(d)


def test_reclassify_single_transient_and_202():
    d, p = _copy_db()
    try:
        conn = db.connect(p)
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM link WHERE status='ok' AND url IS NOT NULL AND url NOT LIKE '%d1-law.com%' "
            "AND url NOT LIKE '%legal-square.com%' LIMIT 4")]
        a, b, c, e = ids
        rows = {
            a: [("2099-01-01", None, "URLError: <urlopen error timed out>")],          # 1回だけ → 戻す
            b: [("2099-01-01", 200, None), ("2099-01-02", 503, "HTTPError 503")],      # 成功→一時エラー → 戻す
            c: [("2099-01-01", None, "URLError: <urlopen error timed out>"),
                ("2099-01-02", None, "URLError: <urlopen error timed out>")],          # 2回連続 → dead のまま
            e: [("2099-01-01", 202, None)],                                             # 202 → 戻す
        }
        for lid, ps in rows.items():
            conn.execute("DELETE FROM probe WHERE link_id=?", (lid,))
            for at, st, er in ps:
                conn.execute("INSERT INTO probe (link_id,checked_at,http_status,changed,error) "
                             "VALUES (?,?,?,0,?)", (lid, at + "T00:00:00", st, er))
            # robots 不許可の記録は「確認の試み」に数えない
            conn.execute("INSERT INTO probe (link_id,checked_at,changed,error) VALUES (?,?,0,?)",
                         (lid, "2099-02-01T00:00:00", probe.ROBOTS_ERR))
            conn.execute("UPDATE link SET status='dead' WHERE id=?", (lid,))
        conn.commit()
        probe.reclassify(conn)
        st = {lid: conn.execute("SELECT status FROM link WHERE id=?", (lid,)).fetchone()[0] for lid in ids}
        assert st == {a: "unchecked", b: "unchecked", c: "dead", e: "unchecked"}
        assert probe.reclassify(conn) == 0      # 冪等
        conn.close()
    finally:
        shutil.rmtree(d)


def test_503_and_202_then_timeout():
    d, p = _copy_db()
    try:
        lid, _ = _prepare_target(p, "ok")
        r503 = lambda url: (503, url, None, "HTTPError 503", "")  # noqa: E731
        _run_once(p, d, r503, "2099-01-01T00:00:00")
        assert _status(p, lid) == "ok"
        _run_once(p, d, r503, "2099-01-02T00:00:00")
        assert _status(p, lid) == "dead"
        _run_once(p, d, ACCEPTED_RES, "2099-01-03T00:00:00")
        assert _status(p, lid) == "unchecked"   # 202：判定できない（dead から戻す）
        _run_once(p, d, TIMEOUT_RES, "2099-01-04T00:00:00")
        assert _status(p, lid) == "unchecked"   # 前回が「判定できない」なので失敗に数えない
    finally:
        shutil.rmtree(d)


def test_prune_keeps_real_probes_behind_robots_rows():
    """robots 不許可の記録が続いても、通常の確認の履歴は押し出されない。"""
    d, p = _copy_db()
    try:
        conn = db.connect(p)
        lid = conn.execute("SELECT id FROM link WHERE url IS NOT NULL AND url NOT LIKE '%d1-law.com%' "
                           "AND url NOT LIKE '%legal-square.com%' LIMIT 1").fetchone()["id"]
        conn.execute("DELETE FROM probe WHERE link_id=?", (lid,))
        to = "URLError: <urlopen error timed out>"
        for at, er in [("2099-01-01", to), ("2099-01-02", probe.ROBOTS_ERR),
                       ("2099-01-03", probe.ROBOTS_ERR), ("2099-01-04", to)]:
            conn.execute("INSERT INTO probe (link_id,checked_at,changed,error) VALUES (?,?,0,?)",
                         (lid, at + "T00:00:00", er))
        conn.commit()
        probe.prune_history(conn)
        assert len(probe.recent_probes(conn, lid)) == 2
        conn.execute("UPDATE link SET status='dead' WHERE id=?", (lid,))
        conn.commit()
        probe.reclassify(conn)
        assert conn.execute("SELECT status FROM link WHERE id=?", (lid,)).fetchone()[0] == "dead"
        conn.close()
    finally:
        shutil.rmtree(d)


def test_needs_recheck():
    n = probe.needs_recheck
    assert n(None, "URLError: <urlopen error timed out>")
    assert n(None, "URLError: <urlopen error [Errno 111] Connection refused>")
    assert n(None, "RemoteDisconnected: Remote end closed connection without response")
    assert not n(404, "HTTPError 404")
    assert not n(503, "HTTPError 503")
    assert not n(None, "URLError: <urlopen error [Errno -2] Name or service not known>")
    assert not n(200, None)


def _run_with_recheck(p, d, url_result, recheck_result, now):
    saved = probe.recheck_domestic
    calls = []

    def fake(url):
        calls.append(url)
        return recheck_result(url) if callable(recheck_result) else recheck_result
    probe.recheck_domestic = fake
    try:
        _run_once(p, d, url_result, now)
    finally:
        probe.recheck_domestic = saved
    return calls


def test_domestic_recheck_alive_and_fallback():
    d, p = _copy_db()
    try:
        lid, url = _prepare_target(p, "unchecked")
        alive = lambda u: (200, u, "国内", None, False)  # noqa: E731
        calls = _run_with_recheck(p, d, TIMEOUT_RES, alive, "2099-01-01T00:00:00")
        assert calls == [url]
        assert _status(p, lid) == "ok"           # 国内から見られた → 確認済み
        conn = db.connect(p)
        assert conn.execute("SELECT last_checked FROM link WHERE id=?", (lid,)).fetchone()[0] == "2099-01-01"
        conn.close()
        # 窓口が使えない（None）→ 今までどおり（成功のあとの1回目の時間切れなのでそのまま）
        _run_with_recheck(p, d, TIMEOUT_RES, None, "2099-01-02T00:00:00")
        assert _status(p, lid) == "ok"
        # 404 はサーバーが答えているので国内からは確認し直さない
        calls = _run_with_recheck(p, d, NOTFOUND_RES, alive, "2099-01-03T00:00:00")
        assert calls == [] and _status(p, lid) == "dead"
    finally:
        shutil.rmtree(d)


def test_domestic_recheck_timeout_twice_and_robots():
    d, p = _copy_db()
    try:
        lid, url = _prepare_target(p, "ok")
        dto = lambda u: (None, u, None, "国内から: timed out (x)", False)  # noqa: E731
        _run_with_recheck(p, d, TIMEOUT_RES, dto, "2099-01-01T00:00:00")
        assert _status(p, lid) == "ok"
        _run_with_recheck(p, d, TIMEOUT_RES, dto, "2099-01-02T00:00:00")
        assert _status(p, lid) == "dead"         # 国内からも2回続けて時間切れ
        rb = lambda u: (None, u, None, None, True)  # noqa: E731
        _run_with_recheck(p, d, TIMEOUT_RES, rb, "2099-01-03T00:00:00")
        conn = db.connect(p)
        last = conn.execute("SELECT error FROM probe WHERE link_id=? ORDER BY checked_at DESC LIMIT 1",
                            (lid,)).fetchone()[0]
        conn.close()
        assert last == probe.ROBOTS_ERR and _status(p, lid) == "dead"   # 状態は変えない
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    n = 0
    for k, v in list(globals().items()):
        if k.startswith("test_") and callable(v):
            v()
            n += 1
    print(f"OK {n} tests")
