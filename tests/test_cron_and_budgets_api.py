"""Tests for the daily cron job (api/cron.py) and POST /api/budgets."""

import http.client
import json
import os
import threading
from datetime import date
from http.server import HTTPServer

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "t")
os.environ.setdefault("SUPABASE_URL", "https://x.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "k")
os.environ.setdefault("WEBHOOK_SECRET", "w")
os.environ["SESSION_SECRET"] = "test-session-secret"

import api.budgets as budgets_mod
import api.cron as cron
from lib.auth import create_session_token
from lib.config import AppConfig
from tests.fakes import FakeDB

CHAT = 42
SUNDAY = date(2026, 10, 11)
WEDNESDAY = date(2026, 10, 7)


def make_config(**kw):
    defaults = dict(telegram_token="t", supabase_url="u", supabase_key="k", webhook_secret="w",
                    monthly_budget=50000, budgets={})
    defaults.update(kw)
    return AppConfig(**defaults)


def txn(id, d, amount, category="food", note="x", type="expense"):
    return {"id": id, "date": d, "category": category, "amount": float(amount),
            "note": note, "type": type, "chat_id": CHAT}


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(cron, "send_telegram_message",
                        lambda chat_id, text, token, markup=None: messages.append((chat_id, text, markup)))
    return messages


# ---------------------------------------------------------------------------
# Renewal dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("created, cycle, today, expected", [
    (date(2026, 1, 15), "monthly", WEDNESDAY, date(2026, 10, 15)),
    (date(2026, 1, 31), "monthly", date(2026, 2, 10), date(2026, 2, 28)),   # clamped
    (date(2025, 3, 20), "yearly", WEDNESDAY, date(2026, 3, 20)),
    (date(2024, 2, 29), "yearly", date(2025, 3, 1), date(2025, 2, 28)),    # leap day
])
def test_due_date(created, cycle, today, expected):
    assert cron.due_date(created, cycle, today) == expected


def sub(id, created_at, cycle="monthly", last_billed=None, amount=199, name="netflix"):
    return {"id": id, "name": name, "amount": amount, "cycle": cycle, "chat_id": CHAT,
            "created_at": created_at, "last_billed": last_billed, "active": True}


def test_renewals_due():
    subs = [
        sub(1, "2026-01-05T10:00:00+00:00"),                              # due 5 Oct -> yes
        sub(2, "2026-01-20T10:00:00+00:00"),                              # due 20 Oct -> not yet
        sub(3, "2026-01-05T10:00:00+00:00", last_billed="2026-10-05"),    # already logged
        sub(4, "2026-01-05T10:00:00+00:00", last_billed="2026-09-05"),    # last month -> yes
        sub(5, "2026-10-06T20:00:00+00:00"),                              # 7 Oct IST -> due today
        sub(6, "2026-03-01T00:00:00+00:00", cycle="yearly"),              # due 1 Mar -> logged? no last_billed -> yes
        sub(7, "2026-11-01T00:00:00+00:00", cycle="yearly"),              # created after due date
    ]
    due = {s["id"]: d for s, d in cron.renewals_due(subs, WEDNESDAY)}
    assert due == {1: date(2026, 10, 5), 4: date(2026, 10, 5), 5: WEDNESDAY, 6: date(2026, 3, 1)}


# ---------------------------------------------------------------------------
# run_daily
# ---------------------------------------------------------------------------


def test_reminder_only_when_nothing_logged_today(sent):
    db = FakeDB(txns=[txn(1, "2026-10-06", 100)])
    stats = cron.run_daily(db, make_config(), WEDNESDAY)
    assert stats["reminders"] == 1
    assert sent[0][1].startswith("📝 Nothing logged today")

    sent.clear()
    db.txns[2] = txn(2, WEDNESDAY.isoformat(), 50)
    assert cron.run_daily(db, make_config(), WEDNESDAY)["reminders"] == 0
    assert sent == []


def test_renewals_logged_once_with_buttons(sent):
    db = FakeDB(txns=[txn(1, WEDNESDAY.isoformat(), 50)],
                subs=[sub(10, "2026-01-05T10:00:00+00:00", amount=649)])

    cron.run_daily(db, make_config(), WEDNESDAY)
    renewal = [m for m in sent if "renewals" in m[1]]
    assert len(renewal) == 1
    assert "₹649 • subscriptions — netflix (#2)" in renewal[0][1]
    assert renewal[0][2]["inline_keyboard"][0][0]["callback_data"] == "d:2"
    assert db.txns[2]["date"] == "2026-10-05"
    assert db.subs[10]["last_billed"] == "2026-10-05"

    # Deleting the auto-logged entry must not make it come back tomorrow.
    del db.txns[2]
    sent.clear()
    cron.run_daily(db, make_config(), date(2026, 10, 8))
    assert not [m for m in sent if "renewals" in m[1]]


def test_renewals_skipped_without_migration(sent):
    class NoColumnDB(FakeDB):
        def subscriptions_for_billing(self):
            raise RuntimeError("column subscriptions.last_billed does not exist")

    db = NoColumnDB(txns=[txn(1, WEDNESDAY.isoformat(), 50)], subs=[sub(10, "2026-01-05T10:00:00+00:00")])
    assert cron.run_daily(db, make_config(), WEDNESDAY)["renewals"] == 0


def test_weekly_summary_on_sunday(sent):
    db = FakeDB(txns=[
        txn(1, "2026-10-11", 1200, "food", "swiggy"),
        txn(2, "2026-10-06", 300, "travel", "uber"),
        txn(3, "2026-10-04", 500, "food", "last week"),
        txn(4, "2026-10-10", 9999, "fav_p", "excluded"),
        txn(5, "2026-10-09", 50000, "income", "salary", type="income"),
    ])
    stats = cron.run_daily(db, make_config(), SUNDAY)
    assert stats["summaries"] == 1
    summary = [m[1] for m in sent if m[1].startswith("📅")][0]
    assert summary.splitlines()[0] == "📅 Your week (5 Oct – 11 Oct)"
    assert "Spent: ₹1,500 (↑200% vs last week's ₹500)" in summary
    assert "Top: food ₹1,200, travel ₹300" in summary
    assert "Biggest: ₹1,200 — swiggy" in summary
    assert "20 days left" in summary


def test_no_weekly_summary_on_other_days(sent):
    db = FakeDB(txns=[txn(1, WEDNESDAY.isoformat(), 100)])
    assert cron.run_daily(db, make_config(), WEDNESDAY)["summaries"] == 0


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


def _serve(mod):
    srv = HTTPServer(("127.0.0.1", 0), mod.handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _request(port, method, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request(method, "/", body=body, headers=headers or {})
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


def test_cron_requires_secret(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(cron, "SupabaseDB", lambda *a, **k: db)
    monkeypatch.setattr(cron, "send_telegram_message", lambda *a, **k: None)
    srv = _serve(cron)
    port = srv.server_port
    try:
        monkeypatch.delenv("CRON_SECRET", raising=False)
        assert _request(port, "GET", {"Authorization": "Bearer "})[0] == 401

        monkeypatch.setenv("CRON_SECRET", "s3cret")
        assert _request(port, "GET")[0] == 401
        assert _request(port, "GET", {"Authorization": "Bearer wrong"})[0] == 401
        status, body = _request(port, "GET", {"Authorization": "Bearer s3cret"})
        assert status == 200 and body["chats"] == 0
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def budgets_server(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(budgets_mod, "SupabaseDB", lambda *a, **k: db)
    monkeypatch.setenv("BUDGETS", '{"food": 8000}')
    monkeypatch.setenv("MONTHLY_BUDGET", "50000")
    srv = _serve(budgets_mod)
    yield srv.server_port, db
    srv.shutdown()
    srv.server_close()


def _post_budgets(port, payload, auth=True):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Cookie"] = f"tracksy_session={create_session_token('test-session-secret')}"
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return _request(port, "POST", headers, body)


def test_budgets_api_requires_auth(budgets_server):
    port, db = budgets_server
    assert _post_budgets(port, {"budgets": {"food": 1}}, auth=False)[0] == 401
    assert db.budgets == {}


def test_budgets_api_saves_and_returns_effective_budgets(budgets_server):
    port, db = budgets_server
    status, body = _post_budgets(port, {"monthlyBudget": 60000, "budgets": {"travel": 5000, "food": 0}})
    assert status == 200
    assert body == {"monthlyBudget": 60000, "budgets": {"travel": 5000.0}}
    assert db.budgets == {"_monthly": 60000.0, "travel": 5000.0, "food": 0.0}


@pytest.mark.parametrize("payload", [
    {},
    {"budgets": {"pizza": 100}},
    {"budgets": {"food": -1}},
    {"budgets": {"food": "100"}},
    {"budgets": {"food": True}},
    {"monthlyBudget": 0},
    {"budgets": ["food"]},
    b"not json",
])
def test_budgets_api_rejects_bad_input(budgets_server, payload):
    port, db = budgets_server
    assert _post_budgets(port, payload)[0] == 400
    assert db.budgets == {}
