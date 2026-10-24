"""Subscription editor API — Vercel serverless function.

Accepts POST from the dashboard:
    {"action": "create", "name": str, "amount": number, "cycle": "monthly"|"yearly"}
    {"action": "update", "id": int, "name": str, "amount": number, "cycle": ...}
Returns {"subscription": {id, name, amount, cycle}} or {"error": str}.
Removing a subscription stays on /api/delete.
"""

import json
import math
import traceback
from http.server import BaseHTTPRequestHandler

from lib.auth import is_authenticated
from lib.config import load_config_from_env
from lib.db import SupabaseDB

MAX_NAME = 50
MAX_AMOUNT = 10_000_000  # same limits as /addsub


def validate(payload) -> tuple[dict | None, str | None]:
    """Return ({name, amount, cycle}, None) or (None, error message)."""
    if not isinstance(payload, dict):
        return None, "Expected a JSON object."
    name = str(payload.get("name") or "").strip()
    if not name:
        return None, "Name is required."
    if len(name) > MAX_NAME:
        return None, f"Name must be {MAX_NAME} characters or fewer."
    amount = payload.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or math.isnan(amount) \
            or not 1 <= amount <= MAX_AMOUNT:
        return None, f"Amount must be between 1 and {MAX_AMOUNT:,}."
    cycle = payload.get("cycle")
    if cycle not in ("monthly", "yearly"):
        return None, "Cycle must be 'monthly' or 'yearly'."
    return {"name": name, "amount": round(float(amount), 2), "cycle": cycle}, None


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the subscription editor API."""

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

            action = payload.get("action") if isinstance(payload, dict) else None
            sub_id = payload.get("id") if isinstance(payload, dict) else None
            if action not in ("create", "update") or (
                action == "update" and (isinstance(sub_id, bool) or not isinstance(sub_id, int))
            ):
                self._send_response(400, {"error": "Expected {action: 'create'|'update', id?: int, ...}."})
                return

            fields, error = validate(payload)
            if error:
                self._send_response(400, {"error": error})
                return

            db = SupabaseDB(config.supabase_url, config.supabase_key)

            if action == "update":
                row = db.update_subscription(sub_id, fields)
                if row is None:
                    self._send_response(404, {"error": f"No subscription found with id {sub_id}."})
                    return
            else:
                # Renewals are announced in Telegram, so the subscription needs a chat.
                chat_id = min(config.owner_chat_ids) if config.owner_chat_ids else db.latest_chat_id()
                if chat_id is None:
                    self._send_response(400, {
                        "error": "Send the Telegram bot a message first, so it knows where "
                                 "to post renewals."
                    })
                    return
                new_id = db.add_subscription(fields["name"], fields["amount"], fields["cycle"], chat_id)
                row = {"id": new_id, **fields}

            self._send_response(200, {"subscription": {
                "id": row["id"], "name": row["name"],
                "amount": float(row["amount"]), "cycle": row["cycle"],
            }})

        except Exception:
            print(f"Subscriptions API error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Something went wrong. Please try again."})

    def _send_response(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
