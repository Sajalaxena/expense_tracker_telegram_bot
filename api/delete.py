"""Delete API — Vercel serverless function.

Accepts POST {"kind": "expense" | "subscription", "id": int} from the
dashboard. Expenses (any txns row, including fav_p) are deleted outright;
subscriptions are deactivated, matching /removesub in the Telegram bot.
Returns {"ok": true} on success or {"error": str}.
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler

from lib.auth import is_authenticated
from lib.config import load_config_from_env
from lib.db import SupabaseDB

KINDS = ("expense", "subscription")


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the delete API."""

    def do_POST(self):
        try:
            config = load_config_from_env()

            if not is_authenticated(self.headers, config.session_secret):
                self._send_response(401, {"error": "Unauthorized"})
                return

            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            kind = payload.get("kind")
            item_id = payload.get("id")

            if kind not in KINDS or isinstance(item_id, bool) or not isinstance(item_id, int):
                self._send_response(400, {"error": "Expected {kind: 'expense'|'subscription', id: int}."})
                return

            db = SupabaseDB(config.supabase_url, config.supabase_key)

            if kind == "expense":
                found = db.delete_transaction(item_id) is not None
            else:
                found = db.deactivate_subscription(item_id)

            if not found:
                self._send_response(404, {"error": f"No {kind} found with id {item_id}."})
                return

            self._send_response(200, {"ok": True})

        except json.JSONDecodeError:
            self._send_response(400, {"error": "Invalid JSON body."})
        except Exception:
            print(f"Delete API error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Something went wrong. Please try again."})

    def _send_response(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
