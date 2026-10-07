"""Tests for the /removesub command handler in api/webhook.py."""

from unittest.mock import MagicMock

from api.webhook import _cmd_removesub


def _db(subs):
    db = MagicMock()
    db.get_active_subscriptions.return_value = subs
    db.deactivate_subscription.return_value = True
    return db


def test_no_args_shows_usage():
    assert _cmd_removesub([], 12345, _db([])) == "Usage: /removesub <name>\nExample: /removesub netflix"


def test_removes_matching_subscription():
    db = _db([
        {"id": 1, "name": "netflix", "amount": 199, "cycle": "monthly"},
        {"id": 2, "name": "spotify", "amount": 119, "cycle": "monthly"},
    ])
    assert _cmd_removesub(["netflix"], 12345, db) == "✅ Removed subscription: netflix"
    db.deactivate_subscription.assert_called_once_with(1, 12345)


def test_case_insensitive_match():
    db = _db([{"id": 3, "name": "netflix", "amount": 199, "cycle": "monthly"}])
    assert _cmd_removesub(["Netflix"], 12345, db) == "✅ Removed subscription: netflix"
    db.deactivate_subscription.assert_called_once_with(3, 12345)


def test_no_matching_subscription():
    db = _db([{"id": 1, "name": "netflix", "amount": 199, "cycle": "monthly"}])
    assert _cmd_removesub(["hulu"], 12345, db) == "No active subscription found matching 'hulu'"
    db.deactivate_subscription.assert_not_called()
