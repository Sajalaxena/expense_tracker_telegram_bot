"""Confirms /api/data, /api/insights and /api/chat refuse unauthenticated
requests, and accept a valid session cookie."""

import http.client
import json
import os
import threading

import pytest
from http.server import HTTPServer

os.environ.setdefault("TELEGRAM_TOKEN", "t")
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ.setdefault("WEBHOOK_SECRET", "w")
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["GEMINI_API_KEY"] = "fake-key"

import api.chat as chat_mod
import api.data as data_mod
import api.insights as insights_mod
from lib import ratelimit
from lib.auth import create_session_token

VALID_TOKEN = create_session_token("test-session-secret")


class FakeDB:
    client = object()  # any attribute access raises -> rate limiter falls back to memory

    def __init__(self, *a, **k):
        pass

    def all_rows(self):
        return []

    def get_all_active_subscriptions(self):
        return []


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    ratelimit._memory.clear()
    ratelimit._memory_global.clear()
    monkeypatch.setattr(data_mod, "SupabaseDB", FakeDB)
    monkeypatch.setattr(insights_mod, "SupabaseDB", FakeDB)
    monkeypatch.setattr(chat_mod, "SupabaseDB", FakeDB)
    monkeypatch.setattr(insights_mod, "_call_gemini", lambda *a, **k: {
        "summary": "s", "expense_tips": [], "subscription_tips": [],
        "loan_tips": [], "investment_tips": [], "general_tips": [],
    })
    monkeypatch.setattr(chat_mod, "call_gemini", lambda *a, **k: "ok")


def _run(mod):
    srv = HTTPServer(("127.0.0.1", 0), mod.handler)
    port = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _get(port, path, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Cookie": f"tracksy_session={cookie}"} if cookie else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    return resp.status, resp.read()


def _post(port, body, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"tracksy_session={cookie}"
    conn.request("POST", "/", body=json.dumps(body).encode("utf-8"), headers=headers)
    resp = conn.getresponse()
    return resp.status, resp.read()


def test_data_endpoint_rejects_then_accepts():
    srv, port = _run(data_mod)
    try:
        status, _ = _get(port, "/")
        assert status == 401
        status, body = _get(port, "/", cookie=VALID_TOKEN)
        assert status == 200
        assert json.loads(body)["expenses"] == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_insights_endpoint_rejects_then_accepts():
    srv, port = _run(insights_mod)
    try:
        status, _ = _get(port, "/?month=2026-09")
        assert status == 401
        status, body = _get(port, "/?month=2026-09", cookie=VALID_TOKEN)
        assert status == 200
        assert json.loads(body)["summary"] == "s"
    finally:
        srv.shutdown()
        srv.server_close()


def test_chat_endpoint_rejects_then_accepts():
    srv, port = _run(chat_mod)
    try:
        status, _ = _post(port, {"question": "hi"})
        assert status == 401
        status, body = _post(port, {"question": "hi"}, cookie=VALID_TOKEN)
        assert status == 200
        assert json.loads(body)["reply"] == "ok"
    finally:
        srv.shutdown()
        srv.server_close()


def test_wrong_secret_cookie_is_rejected():
    srv, port = _run(data_mod)
    try:
        forged = create_session_token("wrong-secret")
        status, _ = _get(port, "/", cookie=forged)
        assert status == 401
    finally:
        srv.shutdown()
        srv.server_close()
