"""Unit tests for check_overspend function."""

import pytest
from unittest.mock import MagicMock

from src.budget import check_overspend


class TestCheckOverspend:
    """Tests for check_overspend()."""

    def _make_db(self, category_total_value: float) -> MagicMock:
        """Create a mock db with a given category_total return value."""
        db = MagicMock()
        db.category_total.return_value = category_total_value
        return db

    def test_returns_none_when_no_budget_cap(self):
        """No warning if category has no cap configured."""
        db = self._make_db(5000.0)
        config = {"budgets": {}, "currency": "₹"}
        result = check_overspend(db, config, "food", "2025-01")
        assert result is None

    def test_returns_none_when_spend_equals_cap(self):
        """No warning when spend is exactly equal to the cap."""
        db = self._make_db(8000.0)
        config = {"budgets": {"food": 8000}, "currency": "₹"}
        result = check_overspend(db, config, "food", "2025-01")
        assert result is None

    def test_returns_none_when_spend_below_cap(self):
        """No warning when spend is under the cap."""
        db = self._make_db(5000.0)
        config = {"budgets": {"food": 8000}, "currency": "₹"}
        result = check_overspend(db, config, "food", "2025-01")
        assert result is None

    def test_returns_warning_when_spend_exceeds_cap(self):
        """Returns a warning when spend exceeds the budget cap."""
        db = self._make_db(9200.0)
        config = {"budgets": {"food": 8000}, "currency": "₹"}
        result = check_overspend(db, config, "food", "2025-01")
        assert result == "⚠️ Food is now ₹1,200 over budget!"

    def test_warning_uses_indian_format(self):
        """Warning amount is formatted with Indian number grouping."""
        db = self._make_db(250000.0)
        config = {"budgets": {"travel": 100000}, "currency": "₹"}
        result = check_overspend(db, config, "travel", "2025-03")
        assert result == "⚠️ Travel is now ₹1,50,000 over budget!"

    def test_uses_configured_currency(self):
        """Warning uses the currency from config."""
        db = self._make_db(12000.0)
        config = {"budgets": {"food": 8000}, "currency": "$"}
        result = check_overspend(db, config, "food", "2025-01")
        assert result == "⚠️ Food is now $4,000 over budget!"

    def test_defaults_currency_to_rupee(self):
        """Defaults to ₹ if no currency in config."""
        db = self._make_db(10000.0)
        config = {"budgets": {"food": 8000}}
        result = check_overspend(db, config, "food", "2025-01")
        assert result == "⚠️ Food is now ₹2,000 over budget!"

    def test_category_display_capitalized(self):
        """Category name is capitalized in the warning."""
        db = self._make_db(7000.0)
        config = {"budgets": {"groceries": 6000}, "currency": "₹"}
        result = check_overspend(db, config, "groceries", "2025-01")
        assert result == "⚠️ Groceries is now ₹1,000 over budget!"

    def test_calls_category_total_with_correct_args(self):
        """Verifies that db.category_total is called with the right args."""
        db = self._make_db(5000.0)
        config = {"budgets": {"food": 8000}, "currency": "₹"}
        check_overspend(db, config, "food", "2025-06")
        db.category_total.assert_called_once_with("2025-06", "food")

    def test_returns_none_when_budgets_key_missing(self):
        """No warning if config has no 'budgets' key at all."""
        db = self._make_db(5000.0)
        config = {"currency": "₹"}
        result = check_overspend(db, config, "food", "2025-01")
        assert result is None
