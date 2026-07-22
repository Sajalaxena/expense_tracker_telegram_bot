"""Budget management helpers for Tracksy (serverless version).

This module provides budget-checking logic without any file-persistence.
Budget configuration lives in environment variables (parsed via AppConfig).
"""

from __future__ import annotations

from lib.utils import indian_format


def parse_simple_amount(text: str) -> float:
    """
    Parse a simple amount string with optional k/K or l/L suffix.

    Supports:
    - Plain numbers: "199" → 199.0
    - k/K suffix (×1000): "2.5k" → 2500.0
    - l/L suffix (×100000): "1.5l" → 150000.0

    Args:
        text: A string containing a number with an optional suffix.

    Returns:
        The parsed float value after applying the multiplier.

    Raises:
        ValueError: If the text cannot be parsed as a valid number.
    """
    text = text.strip().lower()
    multiplier = 1

    if text.endswith("k"):
        multiplier = 1000
        text = text[:-1]
    elif text.endswith("l"):
        multiplier = 100000
        text = text[:-1]

    return float(text) * multiplier


def check_overspend(db, config, category: str, month: str) -> str | None:
    """
    Check if a category has exceeded its budget cap for the given month.

    Looks up the budget cap from config, then queries the database for the
    category's total spend in the given month. If spend exceeds the cap,
    returns a formatted warning message.

    Args:
        db: A SupabaseDB instance with a category_total(month, category) method.
        config: An AppConfig instance or dict with 'budgets' and 'currency' fields.
        category: The expense category to check.
        month: The month in "YYYY-MM" format.

    Returns:
        A warning string if spend exceeds the cap, or None if within budget
        or no cap is configured for the category.
    """
    # Support both AppConfig (attribute access) and plain dict
    if hasattr(config, "budgets"):
        budgets = config.budgets
        currency = config.currency
    else:
        budgets = config.get("budgets", {})
        currency = config.get("currency", "₹")

    cap = budgets.get(category)

    if cap is None:
        return None

    category_spend = db.category_total(month, category)

    if category_spend > cap:
        overspend = category_spend - cap
        overspend_str = indian_format(overspend, currency)
        cat_display = category.capitalize()
        return f"⚠️ {cat_display} is now {overspend_str} over budget!"

    return None
