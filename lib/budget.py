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


# Fraction of a budget at which an early warning is sent.
WARN_FRACTION = 0.8


def budget_alerts(db, config, month: str, added: dict[str, float]) -> list[str]:
    """
    Alerts triggered by entries just logged in `month`.

    `added` maps category -> amount just added (expenses only, fav_p excluded).
    For each category with a cap, and for the overall monthly budget:
      * over the cap -> overspend warning (every time, like check_overspend)
      * crossed WARN_FRACTION with these entries -> a one-time heads-up

    Args:
        db: SupabaseDB-like object with category_total and month_total.
        config: AppConfig with budgets, monthly_budget and currency.
        month: "YYYY-MM" the entries were logged in.
        added: {category: amount added by this message}.

    Returns:
        A list of alert lines (possibly empty).
    """
    currency = config.currency
    alerts = []

    for category, amount in added.items():
        cap = config.budgets.get(category)
        if not cap:
            continue
        after = db.category_total(month, category)
        before = after - amount
        name = category.capitalize()
        if after > cap:
            alerts.append(f"⚠️ {name} is now {indian_format(after - cap, currency)} over budget!")
        elif before < cap * WARN_FRACTION <= after:
            alerts.append(
                f"🟡 {name} has used {int(after / cap * 100)}% of its "
                f"{indian_format(cap, currency)} budget"
            )

    total_added = sum(added.values())
    budget = config.monthly_budget
    if total_added and budget:
        after = db.month_total(month)
        before = after - total_added
        if before <= budget < after:
            alerts.append(f"🔴 You've crossed this month's {indian_format(budget, currency)} budget")
        elif before < budget * WARN_FRACTION <= after <= budget:
            alerts.append(
                f"🟡 You've used {int(after / budget * 100)}% of this month's "
                f"{indian_format(budget, currency)} budget"
            )

    return alerts
