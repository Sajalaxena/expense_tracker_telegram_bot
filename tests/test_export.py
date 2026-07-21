"""Unit tests for the export module."""

import json
import os
import tempfile

import pytest

from src.db import Database
from src.export import regenerate
from src.parser import Transaction


class TestRegenerate:
    """Tests for the regenerate function."""

    def _make_db(self):
        """Create an in-memory database for testing."""
        return Database(":memory:")

    def _sample_config(self):
        return {
            "telegram_token": "SECRET_TOKEN_12345",
            "currency": "₹",
            "monthlyBudget": 50000,
            "budgets": {"food": 8000, "travel": 5000},
        }

    def test_empty_database_produces_empty_array(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        assert "window.EXPENSE_DATA = [];" in content

    def test_output_contains_expense_config(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        assert "window.EXPENSE_CONFIG = " in content
        # Extract config JSON — split on ";\n" to isolate the config line
        config_line = content.split("window.EXPENSE_CONFIG = ")[1].split(";\n")[0]
        parsed_config = json.loads(config_line)
        assert parsed_config["currency"] == "₹"
        assert parsed_config["monthlyBudget"] == 50000
        assert parsed_config["budgets"] == {"food": 8000, "travel": 5000}

    def test_token_never_in_output(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        assert "SECRET_TOKEN_12345" not in content
        assert "telegram_token" not in content

    def test_transaction_fields_in_output(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        txn = Transaction(amount=450.0, category="food", note="swiggy", type="expense")
        db.add(txn, chat_id=123)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        data_json = content.split("window.EXPENSE_DATA = ")[1].split(";\n")[0]
        data = json.loads(data_json)

        assert len(data) == 1
        entry = data[0]
        assert entry["id"] == 1
        assert entry["category"] == "food"
        assert entry["amount"] == 450.0
        assert entry["note"] == "swiggy"
        assert entry["type"] == "expense"
        assert "date" in entry
        # Ensure chat_id and created_at are NOT in the output
        assert "chat_id" not in entry
        assert "created_at" not in entry

    def test_multiple_transactions(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        txn1 = Transaction(amount=100.0, category="travel", note="metro", type="expense")
        txn2 = Transaction(amount=5000.0, category="income", note="salary", type="income")
        db.add(txn1, chat_id=1)
        db.add(txn2, chat_id=1)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        data_json = content.split("window.EXPENSE_DATA = ")[1].split(";\n")[0]
        data = json.loads(data_json)
        assert len(data) == 2

    def test_output_is_valid_javascript(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        txn = Transaction(amount=1500.0, category="bills", note="wifi bill", type="expense")
        db.add(txn, chat_id=42)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        lines = content.strip().split("\n")
        assert len(lines) == 3
        assert lines[0].startswith("window.EXPENSE_DATA = ")
        assert lines[0].endswith(";")
        assert lines[1].startswith("window.EXPENSE_CONFIG = ")
        assert lines[1].endswith(";")
        assert lines[2].startswith("window.EXPENSE_SUBSCRIPTIONS = ")
        assert lines[2].endswith(";")

    def test_write_failure_raises_oserror(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        # Use a path in a non-existent directory to trigger OSError
        bad_path = str(tmp_path / "nonexistent_dir" / "subdir" / "data.js")

        with pytest.raises(OSError) as exc_info:
            regenerate(db, config, bad_path)

        assert bad_path in str(exc_info.value)

    def test_config_defaults_when_fields_missing(self, tmp_path):
        db = self._make_db()
        config = {"telegram_token": "tok"}  # minimal config, missing optional fields
        output = str(tmp_path / "data.js")

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        config_line = content.split("window.EXPENSE_CONFIG = ")[1].split(";\n")[0]
        parsed_config = json.loads(config_line)
        assert parsed_config["currency"] == "₹"
        assert parsed_config["monthlyBudget"] == 0
        assert parsed_config["budgets"] == {}


class TestSubscriptionExport:
    """Tests for subscription data export in regenerate."""

    def _make_db(self):
        """Create an in-memory database for testing."""
        return Database(":memory:")

    def _sample_config(self):
        return {
            "telegram_token": "SECRET_TOKEN_12345",
            "currency": "₹",
            "monthlyBudget": 50000,
            "budgets": {"food": 8000, "travel": 5000},
        }

    def test_empty_subscriptions_produces_empty_array(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        assert "window.EXPENSE_SUBSCRIPTIONS = [];" in content

    def test_active_subscriptions_included(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        db.add_subscription("gym", 2500.0, "monthly", chat_id=456)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        subs_json = content.split("window.EXPENSE_SUBSCRIPTIONS = ")[1].rstrip(";\n")
        subs = json.loads(subs_json)

        assert len(subs) == 2
        # Should be ordered by name
        assert subs[0]["name"] == "gym"
        assert subs[1]["name"] == "netflix"

    def test_subscription_fields_correct(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        db.add_subscription("spotify", 119.0, "monthly", chat_id=123)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        subs_json = content.split("window.EXPENSE_SUBSCRIPTIONS = ")[1].rstrip(";\n")
        subs = json.loads(subs_json)

        assert len(subs) == 1
        sub = subs[0]
        assert sub["id"] == 1
        assert sub["name"] == "spotify"
        assert sub["amount"] == 119.0
        assert sub["cycle"] == "monthly"

    def test_chat_id_excluded_from_export(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        subs_json = content.split("window.EXPENSE_SUBSCRIPTIONS = ")[1].rstrip(";\n")
        subs = json.loads(subs_json)

        sub = subs[0]
        assert "chat_id" not in sub
        assert "created_at" not in sub

    def test_inactive_subscriptions_excluded(self, tmp_path):
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        db.add_subscription("netflix", 199.0, "monthly", chat_id=123)
        db.add_subscription("hulu", 149.0, "monthly", chat_id=123)
        # Deactivate hulu
        db.deactivate_subscription(2, chat_id=123)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        subs_json = content.split("window.EXPENSE_SUBSCRIPTIONS = ")[1].rstrip(";\n")
        subs = json.loads(subs_json)

        assert len(subs) == 1
        assert subs[0]["name"] == "netflix"

    def test_graceful_fallback_when_method_raises(self, tmp_path):
        """If the db method raises an exception, output empty array."""
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        # Monkey-patch to simulate an error (e.g., table doesn't exist)
        def raise_error():
            raise Exception("no such table: subscriptions")

        db.get_all_active_subscriptions = raise_error

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        assert "window.EXPENSE_SUBSCRIPTIONS = [];" in content

    def test_subscriptions_across_multiple_chat_ids(self, tmp_path):
        """All active subscriptions from all users are exported."""
        db = self._make_db()
        config = self._sample_config()
        output = str(tmp_path / "data.js")

        db.add_subscription("netflix", 199.0, "monthly", chat_id=111)
        db.add_subscription("spotify", 119.0, "monthly", chat_id=222)
        db.add_subscription("gym", 2500.0, "yearly", chat_id=333)

        regenerate(db, config, output)

        content = open(output, encoding="utf-8").read()
        subs_json = content.split("window.EXPENSE_SUBSCRIPTIONS = ")[1].rstrip(";\n")
        subs = json.loads(subs_json)

        assert len(subs) == 3
        names = [s["name"] for s in subs]
        assert "netflix" in names
        assert "spotify" in names
        assert "gym" in names
