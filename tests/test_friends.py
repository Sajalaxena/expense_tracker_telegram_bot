"""Tests for the friends ledger: balances, Telegram parsing and bot flows."""

from datetime import date, timedelta

import pytest

import api.webhook as webhook
from lib import friends
from lib.config import AppConfig
from lib.dates import local_today
from tests.fakes import FakeDB

CHAT = 42
TODAY = date(2026, 10, 25)


def make_config(**kw):
    defaults = dict(telegram_token="t", supabase_url="u", supabase_key="k", webhook_secret="w")
    defaults.update(kw)
    return AppConfig(**defaults)


def entry(id, person, kind, amount, day="2026-10-01", note=""):
    return {"id": id, "person": person, "kind": kind, "amount": float(amount), "note": note,
            "date": day, "chat_id": CHAT}


@pytest.fixture
def tg(monkeypatch):
    calls = []
    for name in ("edit_message", "answer_callback"):
        monkeypatch.setattr(webhook, name, lambda *a, _n=name, **k: calls.append((_n, a, k)))
    return calls


def test_balances():
    entries = [entry(1, "rahul", "lent", 1000), entry(2, "rahul", "received", 300),
               entry(3, "amit", "borrowed", 500), entry(4, "amit", "repaid", 200)]
    assert friends.balances(entries) == {"rahul": 700, "amit": -300}


def test_balance_line():
    assert friends.balance_line("rahul sharma", 700, "₹") == "Rahul Sharma owes you ₹700"
    assert friends.balance_line("amit", -300, "₹") == "You owe Amit ₹300"
    assert friends.balance_line("amit", 0, "₹") == "All settled with Amit ✅"


@pytest.mark.parametrize("text, kind, person, amount, note, needs_known", [
    ("lent rahul 500", "lent", "rahul", 500, "", False),
    ("Lent 500 to Rahul Sharma", "lent", "rahul sharma", 500, "", False),
    ("rahul owes me 1.5k for dinner", "lent", "rahul", 1500, "dinner", False),
    ("rahul borrowed 300", "lent", "rahul", 300, "", False),
    ("gave rahul 500", "lent", "rahul", 500, "", True),
    ("borrowed 1000 from amit", "borrowed", "amit", 1000, "", False),
    ("i borrowed 1,000 from amit for rent", "borrowed", "amit", 1000, "rent", False),
    ("i owe amit 1000", "borrowed", "amit", 1000, "", False),
    ("amit lent me 2k", "borrowed", "amit", 2000, "", False),
    ("rahul paid back 200", "received", "rahul", 200, "", False),
    ("rahul paid back", "received", "rahul", None, "", False),
    ("got 200 from rahul", "received", "rahul", 200, "", True),
    ("paid back amit 500", "repaid", "amit", 500, "", False),
    ("repaid amit", "repaid", "amit", None, "", False),
    ("returned 500 to amit", "repaid", "amit", 500, "", False),
    ("settle rahul", "settle", "rahul", None, "", True),
    ("settled up with amit", "settle", "amit", None, "", True),
])
def test_parse(text, kind, person, amount, note, needs_known):
    intent = friends.parse_friend_message(text, TODAY)
    assert (intent.kind, intent.person, intent.amount, intent.note, intent.needs_known) == \
        (kind, person, amount, note, needs_known)
    assert intent.date == TODAY


def test_parse_date():
    assert friends.parse_friend_message("lent rahul 500 yesterday", TODAY).date == TODAY - timedelta(days=1)


@pytest.mark.parametrize("text", [
    "swiggy 450", "paid 500 for lunch", "lent rahul", "rahul returned the book",
    "i borrowed", "salary 80k", "swiggy 450, lent rahul 500",
])
def test_parse_ignores(text):
    assert friends.parse_friend_message(text, TODAY) is None


def test_validate_entry():
    fields, error = friends.validate_entry({"person": "  Rahul   Sharma ", "kind": "lent", "amount": 500}, TODAY)
    assert error is None
    assert fields == {"person": "rahul sharma", "kind": "lent", "amount": 500.0, "note": "",
                      "date": "2026-10-25"}
    for bad in ({"kind": "lent", "amount": 1}, {"person": "a", "kind": "gift", "amount": 1},
                {"person": "a", "kind": "lent", "amount": 0}, {"person": "a", "kind": "lent", "amount": "5"},
                {"person": "a", "kind": "lent", "amount": 5, "date": "tomorrow"}):
        assert friends.validate_entry(bad, TODAY)[0] is None


# ---------------------------------------------------------------------------
# Bot flows
# ---------------------------------------------------------------------------


def test_lent_creates_entry_with_undo():
    db = FakeDB()
    reply = webhook._handle_message("lent rahul 500 for dinner", CHAT, db, make_config())
    saved = next(iter(db.friends.values()))
    assert (saved["person"], saved["kind"], saved["amount"], saved["note"]) == ("rahul", "lent", 500, "dinner")
    assert reply.text == "🤝 Lent ₹500 to Rahul — dinner\nRahul owes you ₹500"
    assert reply.markup["inline_keyboard"][0][0]["callback_data"] == f"fu:{saved['id']}"
    assert not db.txns


def test_paid_back_without_amount_clears_balance():
    db = FakeDB(friends=[entry(1, "rahul", "lent", 800), entry(2, "rahul", "received", 300)])
    reply = webhook._handle_message("rahul paid back", CHAT, db, make_config())
    assert friends.balances(db.get_friend_txns())["rahul"] == 0
    assert reply.text.startswith("💸 Rahul paid you back ₹500")
    assert "All settled with Rahul" in reply.text


def test_settle_when_you_owe():
    db = FakeDB(friends=[entry(1, "amit", "borrowed", 1000)])
    reply = webhook._handle_message("settle amit", CHAT, db, make_config())
    last = max(db.friends.values(), key=lambda e: e["id"])
    assert (last["kind"], last["amount"], last["note"]) == ("repaid", 1000, "settled up")
    assert "All settled with Amit" in reply.text


def test_nothing_to_settle():
    db = FakeDB(friends=[entry(1, "amit", "borrowed", 1000)])
    reply = webhook._handle_message("amit paid back", CHAT, db, make_config())
    assert reply == "🤔 Nothing to settle. You owe Amit ₹1,000"
    assert len(db.friends) == 1


def test_ambiguous_verbs_need_a_known_person():
    db = FakeDB(friends=[entry(1, "rahul", "lent", 100)])
    reply = webhook._handle_message("gave 500 to maid", CHAT, db, make_config())
    assert reply.text.startswith("✅ ₹500")  # logged as an expense
    assert len(db.friends) == 1

    webhook._handle_message("gave rahul 500", CHAT, db, make_config())
    assert friends.balances(db.get_friend_txns())["rahul"] == 600


def test_settle_unknown_person_falls_through():
    reply = webhook._handle_message("settle bill", CHAT, FakeDB(), make_config())
    assert reply.startswith("❌")  # parse error from the expense parser, nothing recorded


def test_undo_friend_entry(tg):
    db = FakeDB()
    reply = webhook._handle_message("lent rahul 500", CHAT, db, make_config())
    callback = {"id": "cb", "data": reply.markup["inline_keyboard"][0][0]["callback_data"],
                "message": {"chat": {"id": CHAT}, "message_id": 3, "text": reply.text}}
    webhook._handle_callback(callback, db, make_config())
    assert not db.friends
    assert ("answer_callback", ("cb", "t", "Undone"), {}) in tg


def test_friends_command():
    db = FakeDB(friends=[entry(1, "rahul", "lent", 700), entry(2, "amit", "borrowed", 300),
                         entry(3, "neha", "lent", 50), entry(4, "neha", "received", 50)])
    text = webhook._handle_command("/friends", CHAT, db, make_config())
    assert "🟢 Rahul owes you ₹700" in text
    assert "🔴 You owe Amit ₹300" in text
    assert "Neha" not in text
    assert "Owed to you: ₹700" in text and "You owe: ₹300" in text


def test_friends_command_empty():
    assert "Nobody owes anybody" in webhook._handle_command("/friends", CHAT, FakeDB(), make_config())


def test_entry_reply_shows_past_date():
    db = FakeDB()
    reply = webhook._handle_message("lent rahul 500 yesterday", CHAT, db, make_config())
    assert "📅" in reply.text.splitlines()[0]
    assert next(iter(db.friends.values()))["date"] == (local_today() - timedelta(days=1)).isoformat()
