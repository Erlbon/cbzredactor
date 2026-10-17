"""Batch 4 (2026-10-17): the Grand Comics Database login (HTTP Basic, the user's comics.org password) is never sent to
the host a redirect points at. Real sockets: two local http.server instances on 127.0.0.1."""

import http.server
import threading

import pytest

from core.gcd_lookup import make_gcd_fetch


class _Server:
    def __init__(self):
        self.routes = {}
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.seen.append({k.lower(): v for k, v in self.headers.items()})
                status, headers, body = outer.routes.get(self.path, (404, {}, b"{}"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def two_servers():
    first, second = _Server(), _Server()
    yield first, second
    first.close()
    second.close()


def test_the_gcd_login_is_not_sent_to_the_host_a_redirect_points_at(two_servers):
    first, second = two_servers
    first.routes["/api/series/"] = (302, {"Location": f"{second.base}/landing"}, b"")
    second.routes["/landing"] = (200, {}, b'{"results": []}')
    fetch = make_gcd_fetch("someone", "hunter2")
    assert fetch(f"{first.base}/api/series/") == b'{"results": []}'
    assert first.seen[0]["authorization"].startswith("Basic ")
    assert second.seen and "authorization" not in second.seen[0]
