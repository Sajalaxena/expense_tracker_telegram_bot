"""Tests for the Telegram bot flows: logging with buttons, button taps,
budget alerts, /setbudget, and photo/voice logging."""

import json
from datetime import timedelta

import pytest

import api.webhook as webhook
from lib.budget import budget_alerts
from lib.config import AppConfig, apply_budget_overrides
from lib.dates import local_today, short_date
from lib.db import MONTHLY_BUDGET_KEY
from lib.entries import Reply, ids_in
from lib.media import _to_transactions
from tests.fakes import FakeDB

CHAT = 42


def make_config(**kw):
    defaults = dict(telegram_token="t", supabase_url="u", supabase_key="k", webhook_secret="w",
                    monthly_budget=10000, budgets={"food": 1000})
    defaults.update(kw)
    return AppConfig(**defaults)


@pytest.fixture
def tg(monkeypatch):
    """Capture Telegram API calls made by the webhook."""
    calls = []
    for name in ("edit_message", "edit_reply_markup", "answer_callback", "send_chat_action"):
        monkeypatch.setattr(webhook, name, lambda *a, _n=name, **k: calls.append((_n, a, k)))
    return calls


def button_data(reply: Reply) -> list[str]:
    return [b["callback_data"] for row in reply.markup["inline_keyboard"] for b in row]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def test_logging_one_entry_replies_with_buttons():
    db = FakeDB()
    reply = webhook._handle_message("swiggy 450", CHAT, db, make_config())
    assert reply.text.startswith("✅ ₹450 • food — swiggy (#1)")
    assert "Month: ₹450 / ₹10,000" in reply.text
    assert button_data(reply) == ["d:1", "c:1", "y:1"]


def test_logging_several_entries():
    db = FakeDB()
    reply = webhook._handle_message("swiggy 450, uber 200 yesterday", CHAT, db, make_config())
    assert ids_in(reply.text) == [1, 2]
    assert db.txns[2]["date"] == (local_today() - timedelta(days=1)).isoformat()
    assert f"📅 {short_date(local_today() - timedelta(days=1))}" in reply.text
    assert len(reply.markup["inline_keyboard"]) == 2


def test_fav_p_entry_not_counted():
    reply = webhook._handle_message("fav 500 headphones", CHAT, FakeDB(), make_config())
    assert "Personal Favorites" in reply.text
    assert "(Not counted in monthly budget)" in reply.text


def test_parse_error_is_plain_text():
    reply = webhook._handle_message("hello", CHAT, FakeDB(), make_config())
    assert isinstance(reply, str) and reply.startswith("❌")


# ---------------------------------------------------------------------------
# Button taps
# ---------------------------------------------------------------------------


def tap(db, reply, data, tg_calls, chat_id=CHAT):
    callback = {"id": "cb1", "data": data,
                "message": {"message_id": 99, "chat": {"id": chat_id}, "text": reply.text}}
    webhook._handle_callback(callback, db, make_config())
    edits = [c for c in tg_calls if c[0] == "edit_message"]
    return edits[-1][1] if edits else None  # (chat_id, message_id, text, token, markup)


def test_delete_button(tg):
    db = FakeDB()
    reply = webhook._handle_message("swiggy 450, uber 200", CHAT, db, make_config())
    _, _, text, _, markup = tap(db, reply, "d:1", tg)
    assert 1 not in db.txns
    assert "🗑️ #1 deleted" in text and "(#2)" in text
    assert [b["callback_data"] for row in markup["inline_keyboard"] for b in row] == ["d:2", "c:2", "y:2"]
    # Deleted line survives a second tap on the re-rendered message
    _, _, text2, _, _ = tap(db, Reply(text), "y:2", tg)
    assert "🗑️ #1 deleted" in text2


def test_minus_one_day_button(tg):
    db = FakeDB()
    reply = webhook._handle_message("swiggy 450", CHAT, db, make_config())
    tap(db, reply, "y:1", tg)
    tap(db, reply, "y:1", tg)
    assert db.txns[1]["date"] == (local_today() - timedelta(days=2)).isoformat()


def test_category_picker_then_set(tg):
    db = FakeDB()
    reply = webhook._handle_message("misc 300", CHAT, db, make_config())
    tap(db, reply, "c:1", tg)
    name, args, _ = [c for c in tg if c[0] == "edit_reply_markup"][-1]
    picker = [b["callback_data"] for row in args[2]["inline_keyboard"] for b in row]
    assert "s:1:food" in picker and picker[-1] == "b:1"

    _, _, text, _, _ = tap(db, reply, "s:1:food", tg)
    assert db.txns[1]["category"] == "food"
    assert "• food — misc (#1)" in text


def test_invalid_category_is_rejected(tg):
    db = FakeDB()
    reply = webhook._handle_message("misc 300", CHAT, db, make_config())
    assert tap(db, reply, "s:1:hacked", tg) is None
    assert db.txns[1]["category"] == "other"


def test_cannot_touch_another_chats_entry(tg):
    db = FakeDB()
    reply = webhook._handle_message("swiggy 450", CHAT, db, make_config())
    tap(db, reply, "d:1", tg, chat_id=999)
    assert 1 in db.txns


# ---------------------------------------------------------------------------
# Budget alerts
# ---------------------------------------------------------------------------


def test_category_alert_fires_once_when_crossing_80_percent():
    db = FakeDB()
    config = make_config()
    first = webhook._handle_message("swiggy 700", CHAT, db, config)
    assert "🟡" not in first.text
    crossing = webhook._handle_message("zomato 150", CHAT, db, config)
    assert "🟡 Food has used 85% of its ₹1,000 budget" in crossing.text
    again = webhook._handle_message("chai 20", CHAT, db, config)
    assert "🟡 Food" not in again.text


def test_overspend_alert_every_time():
    db = FakeDB()
    config = make_config()
    webhook._handle_message("swiggy 1100", CHAT, db, config)
    reply = webhook._handle_message("chai 20", CHAT, db, config)
    assert "⚠️ Food is now ₹120 over budget!" in reply.text


def test_monthly_budget_alerts():
    db = FakeDB()
    config = make_config(budgets={})
    month = local_today().strftime("%Y-%m")
    db.txns[1] = {"id": 1, "date": f"{month}-01", "category": "rent", "amount": 7500.0,
                  "note": "rent", "type": "expense", "chat_id": CHAT}
    db._next_id = 2
    db.txns[2] = {**db.txns[1], "id": 2, "amount": 600.0}
    assert budget_alerts(db, config, month, {"rent": 600}) == [
        "🟡 You've used 81% of this month's ₹10,000 budget"
    ]
    db.txns[3] = {**db.txns[1], "id": 3, "amount": 2000.0}
    assert budget_alerts(db, config, month, {"rent": 2000}) == [
        "🔴 You've crossed this month's ₹10,000 budget"
    ]


# ---------------------------------------------------------------------------
# /setbudget + overrides
# ---------------------------------------------------------------------------


def test_setbudget_saves_and_overrides():
    db = FakeDB()
    config = make_config()
    assert webhook._cmd_setbudget(["food", "8k"], db, config) == "✅ Food budget set to ₹8,000/month"
    assert webhook._cmd_setbudget(["total", "60000"], db, config) == "✅ Monthly budget set to ₹60,000"
    assert webhook._cmd_setbudget(["travel", "0"], db, config) == "✅ Removed the travel budget cap"

    fresh = apply_budget_overrides(make_config(budgets={"food": 1000, "travel": 500}), db)
    assert fresh.monthly_budget == 60000
    assert fresh.budgets == {"food": 8000.0}


def test_setbudget_validation():
    db = FakeDB()
    config = make_config()
    assert webhook._cmd_setbudget([], db, config).startswith("Usage:")
    assert webhook._cmd_setbudget(["pizza", "100"], db, config).startswith("Unknown category")
    assert webhook._cmd_setbudget(["food", "abc"], db, config).startswith("Invalid amount")
    assert webhook._cmd_setbudget(["total", "0"], db, config) == "Budget amount must be positive"
    assert db.budgets == {}


def test_setbudget_without_table_explains_migration():
    class NoTableDB(FakeDB):
        def set_budget(self, *a):
            raise RuntimeError('relation "budgets" does not exist')

    reply = webhook._cmd_setbudget(["food", "100"], NoTableDB(), make_config())
    assert "supabase/migrations.sql" in reply


def test_overrides_fail_soft_without_table():
    class NoTableDB(FakeDB):
        def get_budget_overrides(self):
            raise RuntimeError("missing")

    config = apply_budget_overrides(make_config(), NoTableDB())
    assert config.budgets == {"food": 1000} and config.monthly_budget == 10000


def test_month_key_constant():
    assert MONTHLY_BUDGET_KEY == "_monthly"


# ---------------------------------------------------------------------------
# Photos & voice
# ---------------------------------------------------------------------------


def test_media_of():
    assert webhook._media_of({"photo": [{"file_id": "small"}, {"file_id": "big"}]}) == ("big", "image/jpeg", "photo")
    assert webhook._media_of({"voice": {"file_id": "v", "mime_type": "audio/ogg"}}) == ("v", "audio/ogg", "voice")
    assert webhook._media_of({"document": {"file_id": "d", "mime_type": "application/pdf"}}) == ("d", "application/pdf", "photo")
    assert webhook._media_of({"document": {"file_id": "z", "mime_type": "application/zip"}}) is None
    assert webhook._media_of({"sticker": {}}) is None


def test_gemini_items_are_validated():
    today = local_today()
    txns = _to_transactions([
        {"amount": 450, "note": "Swiggy", "category": "food", "type": "expense",
         "date": (today - timedelta(days=1)).isoformat()},
        {"amount": 80000, "note": "salary", "category": "income", "type": "income"},
        {"amount": 99, "note": "thing", "category": "made-up", "type": "expense", "date": "2999-01-01"},
        {"amount": -5, "note": "bad", "category": "food", "type": "expense"},
        {"amount": "lots", "note": "bad", "category": "food", "type": "expense"},
        "not a dict",
    ], today)
    assert [(t.amount, t.category, t.note, t.type, t.date) for t in txns] == [
        (450.0, "food", "swiggy", "expense", today - timedelta(days=1)),
        (80000.0, "income", "salary", "income", None),
        (99.0, "other", "thing", "expense", None),
    ]
    assert _to_transactions({"not": "a list"}, today) == []


def test_photo_is_logged(monkeypatch, tg):
    monkeypatch.setattr(webhook, "download_file", lambda file_id, token: b"jpeg-bytes")
    seen = {}

    def fake_extract(api_key, data, mime, kind, caption, today):
        seen.update(data=data, mime=mime, kind=kind, caption=caption)
        return _to_transactions([{"amount": 349, "note": "zomato", "category": "food", "type": "expense"}], today)

    monkeypatch.setattr(webhook, "extract_transactions", fake_extract)
    db = FakeDB()
    message = {"photo": [{"file_id": "p1"}], "caption": "lunch", "chat": {"id": CHAT}}
    reply = webhook._handle_media(message, CHAT, db, make_config(gemini_api_key="g"))

    assert seen == {"data": b"jpeg-bytes", "mime": "image/jpeg", "kind": "photo", "caption": "lunch"}
    assert reply.text.startswith("📷 From your photo:\n✅ ₹349 • food — zomato (#1)")
    assert db.txns[1]["amount"] == 349.0


def test_media_with_nothing_found(monkeypatch, tg):
    monkeypatch.setattr(webhook, "download_file", lambda *a: b"ogg")
    monkeypatch.setattr(webhook, "extract_transactions", lambda *a: [])
    message = {"voice": {"file_id": "v"}, "chat": {"id": CHAT}}
    reply = webhook._handle_media(message, CHAT, FakeDB(), make_config(gemini_api_key="g"))
    assert reply.startswith("🤔 I couldn't find an amount in that voice note")


def test_media_needs_gemini_key():
    message = {"voice": {"file_id": "v"}, "chat": {"id": CHAT}}
    assert "GEMINI_API_KEY" in webhook._handle_media(message, CHAT, FakeDB(), make_config())
