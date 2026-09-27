"""End-to-end tests for api/login.py and api/logout.py over real HTTP."""

import http.client
import json
import threading

import pytest
from http.server import HTTPServer

import os

os.environ.setdefault("TELEGRAM_TOKEN", "t")
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ.setdefault("WEBHOOK_SECRET", "w")
os.environ["AUTH_USERNAME"] = "sajal"
os.environ["AUTH_PASSWORD"] = "correct-horse"
os.environ["SESSION_SECRET"] = "test-session-secret"

import api.login as login_mod
import api.logout as logout_mod
from lib import ratelimit
from lib.auth import verify_session_token


class BrokenClient:
    """Simulates the api_requests table not existing, forcing the memory fallback."""

    def table(self, name):
        raise RuntimeError("no table in tests")


class FakeDB:
    client = BrokenClient()

    def __init__(self, *a, **k):
        pass


@pytest.fixture(autouse=True)
def reset_rate_limit_state(monkeypatch):
    ratelimit._memory.clear()
    ratelimit._memory_global.clear()
    monkeypatch.setattr(login_mod, "SupabaseDB", FakeDB)


@pytest.fixture
def login_server():
    srv = HTTPServer(("127.0.0.1", 0), login_mod.handler)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()
    srv.server_close()


def _post(port, body):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request(
        "POST", "/", body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    raw = resp.read()
    return resp.status, json.loads(raw), dict(resp.getheaders())


def test_correct_credentials_sets_valid_cookie(login_server):
    status, body, headers = _post(login_server, {"username": "sajal", "password": "correct-horse"})
    assert status == 200
    assert body["ok"] is True
    set_cookie = headers["Set-Cookie"]
    assert set_cookie.startswith("tracksy_session=")
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=Lax" in set_cookie
    token = set_cookie.split("=", 1)[1].split(";")[0]
    assert verify_session_token(token, "test-session-secret")


def test_wrong_password_rejected(login_server):
    status, body, _ = _post(login_server, {"username": "sajal", "password": "wrong"})
    assert status == 401
    assert "Invalid" in body["error"]


def test_wrong_username_rejected(login_server):
    status, body, _ = _post(login_server, {"username": "notme", "password": "correct-horse"})
    assert status == 401


def test_missing_fields_rejected(login_server):
    status, body, _ = _post(login_server, {})
    assert status == 401


def test_repeated_failures_are_rate_limited(login_server):
    for _ in range(5):
        _post(login_server, {"username": "sajal", "password": "wrong"})
    status, body, headers = _post(login_server, {"username": "sajal", "password": "wrong"})
    assert status == 429
    assert "Retry-After" in headers
    assert "wait" in body["error"].lower()


def test_correct_password_still_blocked_once_rate_limited(login_server):
    for _ in range(5):
        _post(login_server, {"username": "sajal", "password": "wrong"})
    status, _, _ = _post(login_server, {"username": "sajal", "password": "correct-horse"})
    assert status == 429


def test_logout_clears_cookie():
    srv = HTTPServer(("127.0.0.1", 0), logout_mod.handler)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("POST", "/")
        resp = conn.getresponse()
        headers = dict(resp.getheaders())
        assert resp.status == 200
        assert "Max-Age=0" in headers["Set-Cookie"]
        assert headers["Set-Cookie"].startswith("tracksy_session=;")
    finally:
        srv.shutdown()
        srv.server_close()
