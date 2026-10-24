"""Tests for /api/debts (Loans and Friends pages) and /api/subscriptions."""

import http.client
import json
import os
import threading
from http.server import HTTPServer

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "t")
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ.setdefault("WEBHOOK_SECRET", "w")
os.environ["SESSION_SECRET"] = "test-session-secret"

import api.debts as debts_mod
import api.subscriptions as subs_mod
from lib.auth import create_session_token
from tests.fakes import FakeDB

VALID_TOKEN = create_session_token("test-session-secret")

LOAN_FORM = {"name": "Car Loan", "principal": 800000, "tenure_months": 60, "interest_rate": 9.5,
             "lender": "SBI", "loan_type": "car"}


def _serve(monkeypatch, mod, db):
    monkeypatch.setattr(mod, "SupabaseDB", lambda *a, **k: db)
    srv = HTTPServer(("127.0.0.1", 0), mod.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def debts(monkeypatch):
    db = FakeDB(txns=[{"id": 1, "date": "2026-10-01", "category": "food", "amount": 10.0,
                       "note": "", "type": "expense", "chat_id": 42}])
    srv = _serve(monkeypatch, debts_mod, db)
    yield srv.server_port, db
    srv.shutdown()
    srv.server_close()


def _request(port, method, body=None, cookie=VALID_TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"tracksy_session={cookie}"
    raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, "/", body=raw, headers=headers)
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


def post(port, body, **kw):
    return _request(port, "POST", body, **kw)


# ---------------------------------------------------------------------------
# /api/debts — loans
# ---------------------------------------------------------------------------


def test_requires_auth(debts):
    port, db = debts
    assert _request(port, "GET", cookie=None)[0] == 401
    assert post(port, {"kind": "loan", "action": "create", "data": LOAN_FORM}, cookie=None)[0] == 401
    assert not db.loans


def test_get_empty_state(debts):
    port, _ = debts
    status, body = _request(port, "GET")
    assert status == 200
    assert body["loans"] == [] and body["friends"] == []
    assert "home" in body["loanTypes"] and body["currency"] == "₹"


def test_create_loan_returns_computed_status(debts):
    port, db = debts
    status, body = post(port, {"kind": "loan", "action": "create", "data": LOAN_FORM})
    assert status == 200
    loan = body["loans"][0]
    assert loan["name"] == "Car Loan" and loan["emi_amount"] == pytest.approx(16801.49, abs=0.01)
    assert loan["outstanding"] == 800000
    assert loan["status"]["emis_left"] == 60
    assert loan["payments"] == []
    assert "chat_id" not in loan


def test_duplicate_loan_name_rejected(debts):
    port, _ = debts
    post(port, {"kind": "loan", "action": "create", "data": LOAN_FORM})
    status, body = post(port, {"kind": "loan", "action": "create", "data": dict(LOAN_FORM, name="car loan")})
    assert status == 400 and "already have a loan" in body["error"]


def test_update_and_delete_loan(debts):
    port, db = debts
    loan_id = post(port, {"kind": "loan", "action": "create", "data": LOAN_FORM})[1]["loans"][0]["id"]

    status, body = post(port, {"kind": "loan", "action": "update", "id": loan_id,
                               "data": dict(LOAN_FORM, emis_paid=12, outstanding=650000, emi_day=5)})
    assert status == 200
    assert body["loans"][0]["emis_paid"] == 12 and body["loans"][0]["outstanding"] == 650000
    # Renaming to its own name (different case) is fine.
    assert post(port, {"kind": "loan", "action": "update", "id": loan_id,
                       "data": dict(LOAN_FORM, name="CAR LOAN")})[0] == 200

    assert post(port, {"kind": "loan", "action": "delete", "id": loan_id})[1]["loans"] == []
    assert post(port, {"kind": "loan", "action": "delete", "id": loan_id})[0] == 404


def test_delete_payment_reverses_it(debts):
    port, db = debts
    db.loans[5] = {"id": 5, "name": "loan1", "lender": "", "loan_type": "other", "principal": 100000,
                   "interest_rate": None, "emi_amount": 5000, "tenure_months": 24, "emis_paid": 17,
                   "outstanding": 35000, "start_date": None, "emi_day": None, "notes": "", "chat_id": None}
    db.payments[6] = {"id": 6, "loan_id": 5, "kind": "emi", "amount": 5000, "principal": 5000,
                      "interest": 0, "paid_on": "2026-10-05", "note": "", "chat_id": 42}

    status, body = _request(port, "GET")
    assert body["loans"][0]["payments"][0]["id"] == 6

    status, body = post(port, {"kind": "payment", "action": "delete", "id": 6})
    assert status == 200
    assert body["loans"][0]["emis_paid"] == 16 and body["loans"][0]["outstanding"] == 40000
    assert body["loans"][0]["payments"] == []
    assert post(port, {"kind": "payment", "action": "delete", "id": 6})[0] == 404


@pytest.mark.parametrize("body", [
    {"kind": "loan", "action": "create", "data": {"name": "x"}},
    {"kind": "loan", "action": "update", "id": "1", "data": LOAN_FORM},
    {"kind": "loan", "action": "explode"},
    {"kind": "rocket", "action": "create"},
    b"not json",
    [1, 2],
])
def test_bad_requests(debts, body):
    port, db = debts
    assert post(port, body)[0] == 400
    assert not db.loans


def test_missing_tables_hint(monkeypatch):
    class NoTables(FakeDB):
        def get_loan_payments(self):
            raise RuntimeError('relation "public.loan_payments" does not exist')

    srv = _serve(monkeypatch, debts_mod, NoTables())
    try:
        status, body = _request(srv.server_port, "GET")
        assert status == 503 and "migrations.sql" in body["error"]
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------
# /api/debts — friends
# ---------------------------------------------------------------------------


def test_friend_entries_and_balances(debts):
    port, db = debts
    post(port, {"kind": "friend", "action": "create",
                "data": {"person": "Rahul", "kind": "lent", "amount": 1000, "note": "trip"}})
    status, body = post(port, {"kind": "friend", "action": "create",
                               "data": {"person": "rahul", "kind": "received", "amount": 400}})
    assert status == 200
    rahul = body["friends"][0]
    assert rahul["person"] == "rahul" and rahul["balance"] == 600 and len(rahul["entries"]) == 2

    entry_id = rahul["entries"][0]["id"]
    status, body = post(port, {"kind": "friend", "action": "update", "id": entry_id,
                               "data": {"person": "rahul", "kind": "received", "amount": 500}})
    assert body["friends"][0]["balance"] == 500

    status, body = post(port, {"kind": "friend", "action": "delete", "id": entry_id})
    assert body["friends"][0]["balance"] == 1000
    assert post(port, {"kind": "friend", "action": "delete", "id": entry_id})[0] == 404


def test_settle_rename_delete_person(debts):
    port, db = debts
    post(port, {"kind": "friend", "action": "create", "data": {"person": "amit", "kind": "borrowed", "amount": 300}})

    status, body = post(port, {"kind": "person", "action": "settle", "data": {"person": "amit"}})
    assert body["friends"][0]["balance"] == 0
    assert body["friends"][0]["entries"][0]["kind"] == "repaid"
    assert post(port, {"kind": "person", "action": "settle", "data": {"person": "amit"}})[0] == 400

    status, body = post(port, {"kind": "person", "action": "rename", "data": {"person": "amit", "name": "Amit  K"}})
    assert body["friends"][0]["person"] == "amit k"

    status, body = post(port, {"kind": "person", "action": "delete", "data": {"person": "amit k"}})
    assert body["friends"] == [] and not db.friends
    assert post(port, {"kind": "person", "action": "delete", "data": {"person": "nobody"}})[0] == 404


def test_friends_sorted_open_balances_first(debts):
    port, _ = debts
    for person, kind, amount in [("a", "lent", 10), ("a", "received", 10), ("b", "lent", 50), ("c", "borrowed", 500)]:
        post(port, {"kind": "friend", "action": "create", "data": {"person": person, "kind": kind, "amount": amount}})
    body = _request(port, "GET")[1]
    assert [f["person"] for f in body["friends"]] == ["c", "b", "a"]


# ---------------------------------------------------------------------------
# /api/subscriptions
# ---------------------------------------------------------------------------


@pytest.fixture
def subs(monkeypatch):
    db = FakeDB(txns=[{"id": 1, "date": "2026-10-01", "category": "food", "amount": 10.0,
                       "note": "", "type": "expense", "chat_id": 42}],
                subs=[{"id": 3, "name": "netflix", "amount": 649.0, "cycle": "monthly",
                       "chat_id": 42, "active": True}])
    srv = _serve(monkeypatch, subs_mod, db)
    yield srv.server_port, db
    srv.shutdown()
    srv.server_close()


def test_subscriptions_requires_auth(subs):
    port, _ = subs
    assert post(port, {"action": "create", "name": "x", "amount": 1, "cycle": "monthly"}, cookie=None)[0] == 401


def test_create_subscription_uses_latest_chat(subs):
    port, db = subs
    status, body = post(port, {"action": "create", "name": "Spotify", "amount": 119, "cycle": "monthly"})
    assert status == 200
    sub = body["subscription"]
    assert (sub["name"], sub["amount"], sub["cycle"]) == ("Spotify", 119, "monthly")
    assert db.subs[sub["id"]]["chat_id"] == 42


def test_update_subscription(subs):
    port, db = subs
    status, body = post(port, {"action": "update", "id": 3, "name": "netflix", "amount": 6490, "cycle": "yearly"})
    assert status == 200 and body["subscription"]["cycle"] == "yearly"
    assert db.subs[3]["amount"] == 6490
    assert post(port, {"action": "update", "id": 99, "name": "x", "amount": 1, "cycle": "monthly"})[0] == 404


@pytest.mark.parametrize("body", [
    {"action": "create", "name": "", "amount": 1, "cycle": "monthly"},
    {"action": "create", "name": "x", "amount": 0, "cycle": "monthly"},
    {"action": "create", "name": "x", "amount": True, "cycle": "monthly"},
    {"action": "create", "name": "x", "amount": 5, "cycle": "weekly"},
    {"action": "update", "name": "x", "amount": 5, "cycle": "monthly"},
    {"action": "remove", "id": 3},
    b"{bad",
])
def test_subscription_bad_requests(subs, body):
    port, db = subs
    assert post(port, body)[0] == 400
    assert db.subs[3]["amount"] == 649


def test_create_subscription_needs_a_chat(monkeypatch):
    srv = _serve(monkeypatch, subs_mod, FakeDB())
    try:
        status, body = post(srv.server_port, {"action": "create", "name": "x", "amount": 5, "cycle": "monthly"})
        assert status == 400 and "message first" in body["error"]
    finally:
        srv.shutdown()
        srv.server_close()
