"""Export module for regenerating the data.js file consumed by the dashboard."""

import json

from src.db import Database


def regenerate(db: Database, config: dict, output_path: str) -> None:
    """
    Read all transactions from db, format as JavaScript, write to output_path.

    Only exports: currency, monthlyBudget, budgets from config (never token).
    Includes active subscriptions with id, name, amount, cycle (no chat_id).
    Raises OSError with descriptive message on write failure.

    Args:
        db: A Database instance with an all_rows() method.
        config: A dict loaded from config.json.
        output_path: Path where the data.js file will be written.
    """
    rows = db.all_rows()

    # Build EXPENSE_DATA array — only include public fields
    expense_data = [
        {
            "id": row["id"],
            "date": row["date"],
            "category": row["category"],
            "amount": row["amount"],
            "note": row["note"],
            "type": row["type"],
        }
        for row in rows
    ]

    # Build EXPENSE_CONFIG object — never include telegram_token or other secrets
    expense_config = {
        "currency": config.get("currency", "₹"),
        "monthlyBudget": config.get("monthlyBudget", 0),
        "budgets": config.get("budgets", {}),
    }

    # Build EXPENSE_SUBSCRIPTIONS array — only active, no chat_id
    expense_subscriptions = _get_subscriptions(db)

    data_js = (
        f"window.EXPENSE_DATA = {json.dumps(expense_data, ensure_ascii=False)};\n"
        f"window.EXPENSE_CONFIG = {json.dumps(expense_config, ensure_ascii=False)};\n"
        f"window.EXPENSE_SUBSCRIPTIONS = {json.dumps(expense_subscriptions, ensure_ascii=False)};\n"
    )

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(data_js)
    except OSError as e:
        raise OSError(
            f"Failed to write data file to '{output_path}': {e.strerror}"
        ) from e


def _get_subscriptions(db: Database) -> list[dict]:
    """
    Retrieve all active subscriptions for export.

    Returns a list of dicts with fields: id, name, amount, cycle.
    Gracefully returns an empty list if the subscriptions table doesn't
    exist or the method is unavailable.
    """
    try:
        return db.get_all_active_subscriptions()
    except Exception:
        return []
