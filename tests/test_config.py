"""Unit tests for lib/config.py configuration loader."""

import json
import os
import pytest

from lib.config import load_config_from_env, AppConfig


@pytest.fixture
def required_env(monkeypatch):
    """Set all required environment variables."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token-123")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "test-service-key")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")


class TestLoadConfigFromEnv:
    """Tests for load_config_from_env function."""

    def test_loads_required_vars(self, required_env):
        config = load_config_from_env()
        assert config.telegram_token == "test-token-123"
        assert config.supabase_url == "https://test.supabase.co"
        assert config.supabase_key == "test-service-key"
        assert config.webhook_secret == "test-secret"

    def test_defaults_currency(self, required_env):
        config = load_config_from_env()
        assert config.currency == "₹"

    def test_defaults_monthly_budget(self, required_env):
        config = load_config_from_env()
        assert config.monthly_budget == 50000

    def test_defaults_budgets_empty_dict(self, required_env):
        config = load_config_from_env()
        assert config.budgets == {}

    def test_custom_currency(self, required_env, monkeypatch):
        monkeypatch.setenv("CURRENCY", "$")
        config = load_config_from_env()
        assert config.currency == "$"

    def test_custom_monthly_budget(self, required_env, monkeypatch):
        monkeypatch.setenv("MONTHLY_BUDGET", "75000")
        config = load_config_from_env()
        assert config.monthly_budget == 75000

    def test_invalid_monthly_budget_falls_back(self, required_env, monkeypatch):
        monkeypatch.setenv("MONTHLY_BUDGET", "not-a-number")
        config = load_config_from_env()
        assert config.monthly_budget == 50000

    def test_parses_valid_budgets_json(self, required_env, monkeypatch):
        budgets = {"food": 8000, "travel": 5000}
        monkeypatch.setenv("BUDGETS", json.dumps(budgets))
        config = load_config_from_env()
        assert config.budgets == budgets

    def test_invalid_budgets_json_falls_back_to_empty_dict(self, required_env, monkeypatch):
        monkeypatch.setenv("BUDGETS", "not valid json {{{")
        config = load_config_from_env()
        assert config.budgets == {}

    def test_budgets_non_dict_json_falls_back_to_empty_dict(self, required_env, monkeypatch):
        monkeypatch.setenv("BUDGETS", "[1, 2, 3]")
        config = load_config_from_env()
        assert config.budgets == {}

    def test_raises_when_telegram_token_missing(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")
        monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
            load_config_from_env()

    def test_raises_when_supabase_url_missing(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")
        monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        with pytest.raises(ValueError, match="SUPABASE_URL"):
            load_config_from_env()

    def test_raises_when_supabase_key_missing(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        with pytest.raises(ValueError, match="SUPABASE_KEY"):
            load_config_from_env()

    def test_raises_when_webhook_secret_missing(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_KEY", "test-key")
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
            load_config_from_env()

    def test_raises_with_all_missing_vars_listed(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_KEY", raising=False)
        monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
        with pytest.raises(ValueError) as exc_info:
            load_config_from_env()
        error_msg = str(exc_info.value)
        assert "TELEGRAM_TOKEN" in error_msg
        assert "SUPABASE_URL" in error_msg
        assert "SUPABASE_KEY" in error_msg
        assert "WEBHOOK_SECRET" in error_msg

    def test_returns_appconfig_instance(self, required_env):
        config = load_config_from_env()
        assert isinstance(config, AppConfig)
