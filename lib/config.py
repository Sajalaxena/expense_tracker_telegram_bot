"""Application configuration loader for Vercel environment variables."""

import json
import os
from dataclasses import dataclass, field


@dataclass
class AppConfig:
    """Application configuration loaded from environment variables."""

    telegram_token: str
    supabase_url: str
    supabase_key: str
    webhook_secret: str
    currency: str = "₹"
    monthly_budget: int = 50000
    budgets: dict = field(default_factory=dict)
    gemini_api_key: str = ""
    auth_username: str = ""
    auth_password: str = ""
    session_secret: str = ""
    cron_secret: str = ""


def load_config_from_env() -> AppConfig:
    """Load application configuration from Vercel environment variables.

    Required env vars: TELEGRAM_TOKEN, SUPABASE_URL, SUPABASE_KEY, WEBHOOK_SECRET
    Optional env vars: CURRENCY (default "₹"), MONTHLY_BUDGET (default 50000),
                       BUDGETS (JSON string, default empty dict)

    Raises:
        ValueError: If any required environment variable is missing.

    Returns:
        AppConfig with all fields populated.
    """
    required_vars = ["TELEGRAM_TOKEN", "SUPABASE_URL", "SUPABASE_KEY", "WEBHOOK_SECRET"]
    missing = [var for var in required_vars if not os.environ.get(var)]

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    # Parse BUDGETS as JSON with safe fallback
    budgets_raw = os.environ.get("BUDGETS", "{}")
    try:
        budgets = json.loads(budgets_raw)
        if not isinstance(budgets, dict):
            budgets = {}
    except (json.JSONDecodeError, TypeError):
        budgets = {}

    # Parse MONTHLY_BUDGET with safe fallback
    try:
        monthly_budget = int(os.environ.get("MONTHLY_BUDGET", "50000"))
    except (ValueError, TypeError):
        monthly_budget = 50000

    return AppConfig(
        telegram_token=os.environ["TELEGRAM_TOKEN"],
        supabase_url=os.environ["SUPABASE_URL"],
        supabase_key=os.environ["SUPABASE_KEY"],
        webhook_secret=os.environ["WEBHOOK_SECRET"],
        currency=os.environ.get("CURRENCY", "₹"),
        monthly_budget=monthly_budget,
        budgets=budgets,
        gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
        auth_username=os.environ.get("AUTH_USERNAME", ""),
        auth_password=os.environ.get("AUTH_PASSWORD", ""),
        session_secret=os.environ.get("SESSION_SECRET", ""),
        cron_secret=os.environ.get("CRON_SECRET", ""),
    )


def apply_budget_overrides(config: AppConfig, db) -> AppConfig:
    """Layer budgets saved in Supabase (via /setbudget or the dashboard) over
    the env-var defaults. An override of 0 removes that category's cap.

    Fails soft: if the `budgets` table doesn't exist yet (migration not run),
    the env-var budgets are used unchanged.
    """
    from lib.db import MONTHLY_BUDGET_KEY

    try:
        overrides = db.get_budget_overrides()
    except Exception as exc:
        print(f"Budget overrides unavailable, using env budgets: {exc}")
        return config

    budgets = dict(config.budgets)
    for category, amount in overrides.items():
        if category == MONTHLY_BUDGET_KEY:
            if amount > 0:
                config.monthly_budget = int(amount)
        elif amount > 0:
            budgets[category] = amount
        else:
            budgets.pop(category, None)
    config.budgets = budgets
    return config
