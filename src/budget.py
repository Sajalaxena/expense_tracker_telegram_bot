"""Budget management helpers for Tracksy."""

from __future__ import annotations

import json
import os
import tempfile

from src.utils import indian_format


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


def update_budget(config: dict, config_path: str, category: str, amount: float) -> None:
    """
    Update a category budget in config and persist to disk atomically.

    Uses a temp file + os.replace strategy to prevent corruption on failure.

    Args:
        config: The in-memory config dictionary to update.
        config_path: Path to the config.json file on disk.
        category: The budget category name (will be normalized to lowercase).
        amount: The new budget amount (must be positive).

    Raises:
        OSError: If the file write fails (in-memory config is NOT modified on failure).
    """
    category = category.lower().strip()

    # Save old value for rollback
    old_value = config["budgets"].get(category)

    # Update in-memory config first (needed for json.dump to include the change)
    config["budgets"][category] = amount

    # Persist to disk atomically using temp file + os.replace
    abs_config_path = os.path.abspath(config_path)
    dir_name = os.path.dirname(abs_config_path)
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, abs_config_path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        # Revert in-memory config change
        if old_value is None:
            del config["budgets"][category]
        else:
            config["budgets"][category] = old_value
        raise


def check_overspend(db, config: dict, category: str, month: str) -> str | None:
    """
    Check if a category has exceeded its budget cap for the given month.

    Looks up the budget cap from config, then queries the database for the
    category's total spend in the given month. If spend exceeds the cap,
    returns a formatted warning message.

    Args:
        db: A Database instance with a category_total(month, category) method.
        config: The application config dict containing "budgets" and "currency".
        category: The expense category to check.
        month: The month in "YYYY-MM" format.

    Returns:
        A warning string if spend exceeds the cap, or None if within budget
        or no cap is configured for the category.
    """
    cap = config.get("budgets", {}).get(category)

    if cap is None:
        return None

    category_spend = db.category_total(month, category)

    if category_spend > cap:
        overspend = category_spend - cap
        currency = config.get("currency", "₹")
        overspend_str = indian_format(overspend, currency)
        cat_display = category.capitalize()
        return f"⚠️ {cat_display} is now {overspend_str} over budget!"

    return None
