"""End-to-end tests for api/page.py, the gate that serves the real HTML pages
only to browsers holding a valid session cookie."""

import http.client
import os
import threading

import pytest
from http.server import HTTPServer

os.environ.setdefault("TELEGRAM_TOKEN", "t")
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ.setdefault("WEBHOOK_SECRET", "w")
os.environ["SESSION_SECRET"] = "test-session-secret"

import api.page as page_mod
from lib.auth import create_session_token

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), page_mod.handler)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()
    srv.server_close()


def _get(port, path, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Cookie": f"tracksy_session={cookie}"} if cookie else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    return resp.status, resp.read(), dict(resp.getheaders())


def test_gated_pages_are_not_also_in_public():
    """Vercel checks the filesystem for a literal static-file match BEFORE
    evaluating vercel.json rewrites. If a gated page also exists under
    public/, Vercel serves that static file directly and the rewrite to
    this auth gate never fires -- silently defeating the login requirement.
    """
    for fname in page_mod.PAGES.values():
        assert not os.path.exists(os.path.join(REPO_ROOT, "public", fname)), (
            f"{fname} exists in public/ and would bypass the auth gate; "
            "it must live only in templates/"
        )


def test_unknown_page_is_404(server):
    status, _, _ = _get(server, "/?page=nope")
    assert status == 404


def test_missing_page_param_is_404(server):
    status, _, _ = _get(server, "/")
    assert status == 404


def test_no_cookie_redirects_to_login(server):
    status, _, headers = _get(server, "/?page=dashboard")
    assert status == 302
    assert headers["Location"] == "/login.html"


def test_garbage_cookie_redirects_to_login(server):
    status, _, headers = _get(server, "/?page=dashboard", cookie="not-a-real-token")
    assert status == 302
    assert headers["Location"] == "/login.html"


def test_expired_cookie_redirects_to_login(server):
    token = create_session_token("test-session-secret", lifetime=-10)
    status, _, headers = _get(server, "/?page=dashboard", cookie=token)
    assert status == 302
    assert headers["Location"] == "/login.html"


def test_cookie_signed_with_wrong_secret_redirects_to_login(server):
    token = create_session_token("some-other-secret")
    status, _, _ = _get(server, "/?page=dashboard", cookie=token)
    assert status == 302


@pytest.mark.parametrize("page,fname", [
    ("dashboard", "dashboard.html"),
    ("daily-details", "daily-details.html"),
    ("insights", "insights.html"),
    ("fav-p", "fav-p.html"),
    ("index", "index.html"),
])
def test_valid_cookie_serves_the_real_file_bytes(server, page, fname):
    token = create_session_token("test-session-secret")
    status, body, headers = _get(server, f"/?page={page}", cookie=token)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Cache-Control"] == "no-store"
    with open(os.path.join(REPO_ROOT, "templates", fname), "rb") as f:
        assert body == f.read()
