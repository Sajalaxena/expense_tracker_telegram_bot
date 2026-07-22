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
    )
