"""Unit tests for subscription CRUD methods and category_total in db.py."""

import pytest
from datetime import datetime

from src.db import Database
from src.parser import Transaction


@pytest.fixture
def db():
    """Return a fresh in-memory database."""
    return Database(":memory:")


class TestSubscriptionsCRUD:
    """Tests for add_subscription, get_active_subscriptions, deactivate_subscription."""

    def test_add_subscription_returns_id(self, db):
        sub_id = db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        assert isinstance(sub_id, int)
        assert sub_id > 0

    def test_add_subscription_increments_id(self, db):
        id1 = db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        id2 = db.add_subscription("spotify", 119.0, "monthly", chat_id=123)
        assert id2 > id1

    def test_get_active_subscriptions_returns_added(self, db):
        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        db.add_subscription("gym", 2500.0, "monthly", chat_id=123)

        subs = db.get_active_subscriptions(chat_id=123)
        assert len(subs) == 2
        names = [s["name"] for s in subs]
        assert "gym" in names
        assert "netflix" in names

    def test_get_active_subscriptions_ordered_by_name(self, db):
        db.add_subscription("zoom", 500.0, "monthly", chat_id=123)
        db.add_subscription("apple", 79.0, "monthly", chat_id=123)
        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)

        subs = db.get_active_subscriptions(chat_id=123)
        names = [s["name"] for s in subs]
        assert names == ["apple", "netflix", "zoom"]

    def test_get_active_subscriptions_returns_correct_fields(self, db):
        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)

        subs = db.get_active_subscriptions(chat_id=123)
        assert len(subs) == 1
        sub = subs[0]
        assert set(sub.keys()) == {"id", "name", "amount", "cycle", "created_at"}
        assert sub["name"] == "netflix"
        assert sub["amount"] == 199.0
        assert sub["cycle"] == "monthly"

    def test_get_active_subscriptions_filters_by_chat_id(self, db):
        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        db.add_subscription("spotify", 119.0, "monthly", chat_id=456)

        subs_123 = db.get_active_subscriptions(chat_id=123)
        subs_456 = db.get_active_subscriptions(chat_id=456)
        assert len(subs_123) == 1
        assert subs_123[0]["name"] == "netflix"
        assert len(subs_456) == 1
        assert subs_456[0]["name"] == "spotify"

    def test_get_active_subscriptions_empty(self, db):
        subs = db.get_active_subscriptions(chat_id=999)
        assert subs == []

    def test_deactivate_subscription_returns_true(self, db):
        sub_id = db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        result = db.deactivate_subscription(sub_id, chat_id=123)
        assert result is True

    def test_deactivate_subscription_removes_from_active(self, db):
        sub_id = db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        db.deactivate_subscription(sub_id, chat_id=123)

        subs = db.get_active_subscriptions(chat_id=123)
        assert len(subs) == 0

    def test_deactivate_subscription_wrong_chat_id_returns_false(self, db):
        sub_id = db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        result = db.deactivate_subscription(sub_id, chat_id=999)
        assert result is False

    def test_deactivate_subscription_nonexistent_returns_false(self, db):
        result = db.deactivate_subscription(9999, chat_id=123)
        assert result is False

    def test_deactivate_already_inactive_returns_false(self, db):
        sub_id = db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        db.deactivate_subscription(sub_id, chat_id=123)
        # Second deactivation should return False
        result = db.deactivate_subscription(sub_id, chat_id=123)
        assert result is False

    def test_add_subscription_yearly_cycle(self, db):
        sub_id = db.add_subscription("youtube", 1290.0, "yearly", chat_id=123)
        subs = db.get_active_subscriptions(chat_id=123)
        assert subs[0]["cycle"] == "yearly"

    def test_add_subscription_rejects_invalid_cycle(self, db):
        with pytest.raises(Exception):
            db.add_subscription("test", 100.0, "weekly", chat_id=123)

    def test_add_subscription_rejects_zero_amount(self, db):
        with pytest.raises(Exception):
            db.add_subscription("test", 0.0, "monthly", chat_id=123)

    def test_add_subscription_rejects_negative_amount(self, db):
        with pytest.raises(Exception):
            db.add_subscription("test", -50.0, "monthly", chat_id=123)


class TestCategoryTotal:
    """Tests for category_total method."""

    def test_category_total_single_expense(self, db):
        txn = Transaction(amount=500.0, category="food", note="lunch", type="expense")
        db.add(txn, chat_id=123)
        month = datetime.now().strftime("%Y-%m")
        total = db.category_total(month, "food")
        assert total == 500.0

    def test_category_total_multiple_expenses_same_category(self, db):
        db.add(Transaction(amount=200.0, category="food", note="lunch", type="expense"), chat_id=123)
        db.add(Transaction(amount=300.0, category="food", note="dinner", type="expense"), chat_id=123)
        month = datetime.now().strftime("%Y-%m")
        total = db.category_total(month, "food")
        assert total == 500.0

    def test_category_total_ignores_other_categories(self, db):
        db.add(Transaction(amount=200.0, category="food", note="lunch", type="expense"), chat_id=123)
        db.add(Transaction(amount=1500.0, category="travel", note="uber", type="expense"), chat_id=123)
        month = datetime.now().strftime("%Y-%m")
        total = db.category_total(month, "food")
        assert total == 200.0

    def test_category_total_ignores_income(self, db):
        db.add(Transaction(amount=50000.0, category="food", note="refund", type="income"), chat_id=123)
        db.add(Transaction(amount=200.0, category="food", note="lunch", type="expense"), chat_id=123)
        month = datetime.now().strftime("%Y-%m")
        total = db.category_total(month, "food")
        assert total == 200.0

    def test_category_total_returns_zero_for_no_matches(self, db):
        total = db.category_total("2025-01", "food")
        assert total == 0.0

    def test_category_total_returns_zero_for_nonexistent_category(self, db):
        db.add(Transaction(amount=200.0, category="food", note="lunch", type="expense"), chat_id=123)
        month = datetime.now().strftime("%Y-%m")
        total = db.category_total(month, "nonexistent")
        assert total == 0.0

    def test_category_total_different_month_not_counted(self, db):
        # Manually insert a transaction with a different month
        db._conn.execute(
            """
            INSERT INTO txns (date, category, amount, note, type, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2024-06-15", "food", 999.0, "old", "expense", 123, "2024-06-15T12:00:00"),
        )
        db._conn.commit()

        total = db.category_total("2025-01", "food")
        assert total == 0.0

        total_june = db.category_total("2024-06", "food")
        assert total_june == 999.0
