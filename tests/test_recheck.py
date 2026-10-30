"""国内からの再確認窓口（src/server/recheck.php）のテスト。手元の PHP と テスト用サーバーで動かす（外部には接続しない）。

実行: python3 tests/test_recheck.py  （php が無ければ何もしない）
"""

import hashlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PHP_SRC = os.path.join(HERE, "..", "src", "server", "recheck.php")
TOKEN = "test-token-0123456789"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Site(http.server.BaseHTTPRequestHandler):
    """テスト用の確認先。"""
    routes = {
        "/robots.txt": (200, {"Content-Type": "text/plain"},
                        b"User-agent: *\nDisallow: /private/\n\nUser-agent: Other\nDisallow: /\n"),
        "/page.html": (200, {"Content-Type": "text/html; charset=shift_jis"},
                       "<html><title>例規集</title></html>".encode("cp932")),
        "/private/a.html": (200, {}, b"<title>x</title>"),
        "/dir": (301, {"Location": "/dir/"}, b""),
        "/dir/": (200, {"Content-Type": "text/html"}, b"<title>d</title>"),
        "/q/page": (302, {"Location": "?q=1"}, b""),
        "/q/page?q=1": (200, {"Content-Type": "text/html"}, b"<title>q</title>"),
        "/to-internal": (302, {"Location": "http://foo.localhost/"}, b""),
        "/to-ip": (302, {"Location": "http://127.0.0.1/"}, b""),
        "/accepted": (202, {"Content-Type": "text/html"}, b"<title>wait</title>"),
        "/bogus-charset": (200, {"Content-Type": "text/html; charset=x-bogus"}, b"<title>b</title>"),
        "/redirect-nolocation": (301, {}, b""),
        "/slow": None,
    }

    def do_GET(self):
        r = self.routes.get(self.path)
        if self.path == "/slow":
            time.sleep(30)
            r = (200, {}, b"")
        if r is None:
            r = (404, {}, b"not found")
        code, headers, body = r
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _post(port, payload, token=TOKEN):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/index.php",
                                 data=json.dumps(payload).encode(), method="POST",
                                 headers={"X-Recheck-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    if not shutil.which("php"):
        print("SKIP php が無い")
        return
    d = tempfile.mkdtemp()
    procs = []
    try:
        shutil.copy(PHP_SRC, os.path.join(d, "index.php"))
        with open(os.path.join(d, "config.php"), "w") as f:
            f.write("<?php\nreturn '%s';\n" % hashlib.sha256(TOKEN.encode()).hexdigest())
        site_port = _free_port()
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", site_port), Site)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        p_local, p_prod = _free_port(), _free_port()
        env = dict(os.environ, RECHECK_ALLOW_LOCAL="1")
        procs.append(subprocess.Popen(["php", "-S", f"127.0.0.1:{p_local}", "-t", d], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        env2 = {k: v for k, v in os.environ.items() if k != "RECHECK_ALLOW_LOCAL"}
        procs.append(subprocess.Popen(["php", "-S", f"127.0.0.1:{p_prod}", "-t", d], env=env2,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1)
        base = f"http://localhost:{site_port}"
        n = 0

        def ok(cond, label):
            nonlocal n
            assert cond, label
            n += 1

        # 正常系（手元のテスト用サーバーに接続できる設定）
        c, r = _post(p_local, {"url": base + "/page.html"})
        ok(c == 200 and r["status"] == 200 and r["error"] is None and r["title"] == "例規集", f"page {r}")
        c, r = _post(p_local, {"url": base + "/private/a.html"})
        ok(r["robots_blocked"] is True, f"robots {r}")
        c, r = _post(p_local, {"url": base + "/nope"})
        ok(r["status"] == 404 and r["error"] == "HTTPError 404" and r["final_url"] == base + "/nope", f"404 {r}")
        c, r = _post(p_local, {"url": base + "/dir"})
        ok(r["status"] == 200 and r["final_url"] == base + "/dir/", f"redirect {r}")
        c, r = _post(p_local, {"url": base + "/q/page"})
        ok(r["status"] == 200 and r["final_url"] == base + "/q/page?q=1", f"query redirect {r}")
        c, r = _post(p_local, {"url": base + "/accepted"})
        ok(r["status"] == 202 and r["error"] is None, f"202 {r}")
        c, r = _post(p_local, {"url": base + "/bogus-charset"})
        ok(c == 200 and r["status"] == 200, f"bogus charset {r}")
        c, r = _post(p_local, {"url": base + "/redirect-nolocation"})
        ok(r["status"] == 301 and r["error"] == "HTTPError 301", f"3xx no location {r}")
        c, r = _post(p_local, {"url": base + "/to-internal"})
        ok(c == 422 and "リダイレクト先" in (r["error"] or ""), f"redirect to internal {c} {r}")
        c, r = _post(p_local, {"url": base + "/to-ip"})
        ok(c == 422 and "リダイレクト先" in (r["error"] or ""), f"redirect to ip {c} {r}")
        c, r = _post(p_local, {"url": f"http://localhost:{_free_port()}/x"})
        ok(r["status"] is None and "国内から:" in (r["error"] or ""), f"refused {r}")

        # 受付の確認
        ok(_post(p_local, {"url": base + "/page.html"}, token="wrong")[0] == 403, "wrong token")
        ok(_post(p_local, {"url": base + "/page.html"}, token="")[0] == 403, "no token")
        ok(_post(p_local, {})[0] == 400, "no url")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{p_local}/index.php")
            ok(False, "GET")
        except urllib.error.HTTPError as e:
            ok(e.code == 405, "GET は 405")
        # 本番と同じ設定（手元のアドレスへは一切接続しない）
        for u in ["http://127.0.0.1/", "http://foo.localhost/", "http://localhost/", "http://0x7f000001/",
                  "http://%31%32%37.0.0.1/", "http://[::1]/", "http://2130706433/", "http://127.1/",
                  "file:///etc/passwd", "gopher://example.com/", "http://user:pw@example.com/",
                  "http://example.com:8080/", f"http://localhost:{site_port}/page.html",
                  "http://example.jp./", "http://foo.localhost./", "http://[::ffff:127.0.0.1]/"]:
            c, r = _post(p_prod, {"url": u})
            ok(c == 400 or (r.get("status") is None and r.get("error", "").startswith("国内から:")), (u, c, r))
        print(f"OK {n} checks")
    finally:
        for p in procs:
            p.terminate()
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
