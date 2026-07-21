"""Tests for src/budget.py — parse_simple_amount and update_budget."""

import json
import os
import tempfile

import pytest

from src.budget import parse_simple_amount, update_budget


class TestParseSimpleAmount:
    """Tests for parse_simple_amount()."""

    def test_plain_integer(self):
        assert parse_simple_amount("199") == 199.0

    def test_plain_float(self):
        assert parse_simple_amount("2.5") == 2.5

    def test_k_suffix_lowercase(self):
        assert parse_simple_amount("2.5k") == 2500.0

    def test_k_suffix_uppercase(self):
        assert parse_simple_amount("10K") == 10000.0

    def test_l_suffix_lowercase(self):
        assert parse_simple_amount("1.5l") == 150000.0

    def test_l_suffix_uppercase(self):
        assert parse_simple_amount("2L") == 200000.0

    def test_whitespace_stripped(self):
        assert parse_simple_amount("  5k  ") == 5000.0

    def test_integer_k(self):
        assert parse_simple_amount("10k") == 10000.0

    def test_integer_l(self):
        assert parse_simple_amount("1l") == 100000.0

    def test_invalid_text_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_simple_amount("abc")

    def test_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_simple_amount("")

    def test_only_suffix_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_simple_amount("k")

    def test_negative_value(self):
        assert parse_simple_amount("-5k") == -5000.0

    def test_zero(self):
        assert parse_simple_amount("0") == 0.0


class TestUpdateBudget:
    """Tests for update_budget()."""

    def _make_config_file(self, config: dict) -> str:
        """Create a temporary config file and return its path."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return path

    def test_updates_in_memory_config(self):
        config = {"budgets": {"food": 5000}}
        path = self._make_config_file(config)
        try:
            update_budget(config, path, "food", 10000)
            assert config["budgets"]["food"] == 10000
        finally:
            os.unlink(path)

    def test_persists_to_disk(self):
        config = {"budgets": {"food": 5000, "travel": 3000}}
        path = self._make_config_file(config)
        try:
            update_budget(config, path, "food", 8000)
            with open(path, "r", encoding="utf-8") as f:
                disk_config = json.load(f)
            assert disk_config["budgets"]["food"] == 8000
        finally:
            os.unlink(path)

    def test_preserves_other_fields(self):
        config = {
            "telegram_token": "test-token",
            "currency": "₹",
            "monthlyBudget": 50000,
            "budgets": {"food": 5000, "travel": 3000},
        }
        path = self._make_config_file(config)
        try:
            update_budget(config, path, "food", 10000)
            with open(path, "r", encoding="utf-8") as f:
                disk_config = json.load(f)
            assert disk_config["telegram_token"] == "test-token"
            assert disk_config["currency"] == "₹"
            assert disk_config["monthlyBudget"] == 50000
            assert disk_config["budgets"]["travel"] == 3000
        finally:
            os.unlink(path)

    def test_normalizes_category_to_lowercase(self):
        config = {"budgets": {"food": 5000}}
        path = self._make_config_file(config)
        try:
            update_budget(config, path, "Food", 9000)
            assert config["budgets"]["food"] == 9000
        finally:
            os.unlink(path)

    def test_adds_new_category(self):
        config = {"budgets": {"food": 5000}}
        path = self._make_config_file(config)
        try:
            update_budget(config, path, "entertainment", 2000)
            assert config["budgets"]["entertainment"] == 2000
            with open(path, "r", encoding="utf-8") as f:
                disk_config = json.load(f)
            assert disk_config["budgets"]["entertainment"] == 2000
        finally:
            os.unlink(path)

    def test_reverts_on_write_failure(self, monkeypatch):
        config = {"budgets": {"food": 5000}}
        path = self._make_config_file(config)
        try:
            # Simulate os.replace failing
            monkeypatch.setattr(
                "os.replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full"))
            )
            with pytest.raises(OSError):
                update_budget(config, path, "food", 9999)
            # In-memory config should be reverted
            assert config["budgets"]["food"] == 5000
        finally:
            os.unlink(path)

    def test_disk_file_is_valid_json(self):
        config = {"budgets": {"food": 5000}, "currency": "₹"}
        path = self._make_config_file(config)
        try:
            update_budget(config, path, "food", 7500)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            # Should be valid JSON
            parsed = json.loads(content)
            assert parsed["budgets"]["food"] == 7500
        finally:
            os.unlink(path)
