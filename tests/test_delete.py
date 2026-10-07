"""Tests for POST /api/delete and the Telegram /delete command."""

import http.client
import json
import os
import threading
from http.server import HTTPServer
from types import SimpleNamespace

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "t")
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ.setdefault("WEBHOOK_SECRET", "w")
os.environ["SESSION_SECRET"] = "test-session-secret"

import api.delete as delete_mod
from api.webhook import _handle_command
from lib.auth import create_session_token

VALID_TOKEN = create_session_token("test-session-secret")

TXN = {"id": 7, "date": "2026-10-01", "amount": 450.0, "category": "food",
       "note": "swiggy", "type": "expense", "chat_id": 42}


class FakeDB:
    """In-memory stand-in for SupabaseDB covering the delete paths."""

    def __init__(self, *a, **k):
        self.txns = {7: dict(TXN), 8: dict(TXN, id=8, chat_id=99)}
        self.subs = {3: {"id": 3, "name": "netflix", "chat_id": 42, "active": True}}

    def delete_transaction(self, txn_id, chat_id=None):
        row = self.txns.get(txn_id)
        if row is None or (chat_id is not None and row["chat_id"] != chat_id):
            return None
        return self.txns.pop(txn_id)

    def deactivate_subscription(self, sub_id, chat_id=None):
        sub = self.subs.get(sub_id)
        if not sub or not sub["active"] or (chat_id is not None and sub["chat_id"] != chat_id):
            return False
        sub["active"] = False
        return True

    def recent(self, chat_id, limit=10):
        rows = [r for r in self.txns.values() if r["chat_id"] == chat_id]
        return sorted(rows, key=lambda r: -r["id"])[:limit]


# ---------------------------------------------------------------------------
# /api/delete
# ---------------------------------------------------------------------------


@pytest.fixture
def server(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(delete_mod, "SupabaseDB", lambda *a, **k: db)
    srv = HTTPServer(("127.0.0.1", 0), delete_mod.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port, db
    srv.shutdown()
    srv.server_close()


def _post(port, body, cookie=VALID_TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"tracksy_session={cookie}"
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    conn.request("POST", "/", body=raw, headers=headers)
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


def test_requires_auth(server):
    port, db = server
    status, _ = _post(port, {"kind": "expense", "id": 7}, cookie=None)
    assert status == 401
    assert 7 in db.txns


def test_deletes_expense(server):
    port, db = server
    status, body = _post(port, {"kind": "expense", "id": 7})
    assert (status, body) == (200, {"ok": True})
    assert 7 not in db.txns


def test_deactivates_subscription(server):
    port, db = server
    status, _ = _post(port, {"kind": "subscription", "id": 3})
    assert status == 200
    assert db.subs[3]["active"] is False


def test_missing_item_is_404(server):
    port, _ = server
    assert _post(port, {"kind": "expense", "id": 999})[0] == 404
    assert _post(port, {"kind": "subscription", "id": 999})[0] == 404


@pytest.mark.parametrize("body", [
    {"kind": "expense"},
    {"kind": "expense", "id": "7"},
    {"kind": "expense", "id": True},
    {"kind": "bogus", "id": 7},
    b"not json",
])
def test_bad_request_is_400(server, body):
    port, db = server
    assert _post(port, body)[0] == 400
    assert 7 in db.txns


# ---------------------------------------------------------------------------
# Telegram /delete
# ---------------------------------------------------------------------------

CONFIG = SimpleNamespace(currency="₹")


def test_delete_without_args_lists_recent_with_ids():
    reply = _handle_command("/delete", 42, FakeDB(), CONFIG)
    assert "#7" in reply and "swiggy" in reply
    assert "#8" not in reply  # other chat's entry is not listed


def test_delete_by_id():
    db = FakeDB()
    reply = _handle_command("/delete 7", 42, db, CONFIG)
    assert reply.startswith("🗑️ Deleted #7")
    assert 7 not in db.txns


def test_delete_accepts_hash_prefix():
    db = FakeDB()
    _handle_command("/delete #7", 42, db, CONFIG)
    assert 7 not in db.txns


def test_delete_refuses_other_chats_entry():
    db = FakeDB()
    reply = _handle_command("/delete 8", 42, db, CONFIG)
    assert reply == "No entry found with id #8."
    assert 8 in db.txns


def test_delete_invalid_id_shows_usage():
    reply = _handle_command("/delete abc", 42, FakeDB(), CONFIG)
    assert reply.startswith("Usage: /delete <id>")
