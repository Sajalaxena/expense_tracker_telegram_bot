"""Budget editor API — Vercel serverless function.

Accepts POST {"monthlyBudget": number?, "budgets": {category: number}} from
the dashboard. Each value is saved to the Supabase `budgets` table (0 removes
a category's cap) and overrides the env-var defaults everywhere — Telegram,
dashboard and AI features. Returns the effective
{"monthlyBudget": n, "budgets": {...}} after saving.
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler

from lib.auth import is_authenticated
from lib.config import apply_budget_overrides, load_config_from_env
from lib.db import MONTHLY_BUDGET_KEY, SupabaseDB
from lib.parser import CATEGORY_KEYWORDS

MAX_BUDGET = 10_000_000


def _valid_amount(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= MAX_BUDGET
    )


def validate(payload, known_categories: set) -> tuple[dict | None, str | None]:
    """Return ({db_key: amount}, None) or (None, error message)."""
    if not isinstance(payload, dict):
        return None, "Expected a JSON object."

    updates = {}
    monthly = payload.get("monthlyBudget")
    if monthly is not None:
        if not _valid_amount(monthly) or monthly == 0:
            return None, "Monthly budget must be a positive number."
        updates[MONTHLY_BUDGET_KEY] = float(monthly)

    budgets = payload.get("budgets") or {}
    if not isinstance(budgets, dict):
        return None, "budgets must be an object."
    for category, amount in budgets.items():
        if category not in known_categories:
            return None, f"Unknown category '{category}'."
        if not _valid_amount(amount):
            return None, f"Budget for {category} must be between 0 and {MAX_BUDGET}."
        updates[category] = float(amount)

    if not updates:
        return None, "Nothing to update."
    return updates, None


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the budget editor API."""

    def do_POST(self):
        try:
            config = load_config_from_env()

            if not is_authenticated(self.headers, config.session_secret):
                self._send_response(401, {"error": "Unauthorized"})
                return

            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_response(400, {"error": "Invalid JSON body."})
                return

            known = {c for c in CATEGORY_KEYWORDS if c != "fav_p"} | {"other"} | set(config.budgets)
            updates, error = validate(payload, known)
            if error:
                self._send_response(400, {"error": error})
                return

            db = SupabaseDB(config.supabase_url, config.supabase_key)
            try:
                for key, amount in updates.items():
                    db.set_budget(key, amount)
            except Exception as exc:
                print(f"set_budget failed: {exc}")
                self._send_response(503, {
                    "error": "Couldn't save budgets. Run supabase/migrations.sql in the "
                             "Supabase SQL editor to create the budgets table."
                })
                return

            apply_budget_overrides(config, db)
            self._send_response(200, {
                "monthlyBudget": config.monthly_budget,
                "budgets": config.budgets,
            })

        except Exception:
            print(f"Budgets API error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Something went wrong. Please try again."})

    def _send_response(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
