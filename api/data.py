"""Dashboard data API — Vercel serverless function.

Serves all transactions, configuration, and active subscriptions as JSON
for the dashboard frontend. Includes CORS headers for browser access.
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler

from lib.config import load_config_from_env
from lib.db import SupabaseDB


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for dashboard data API."""

    def do_GET(self):
        """Handle GET requests — return dashboard data as JSON."""
        try:
            config = load_config_from_env()
            db = SupabaseDB(config.supabase_url, config.supabase_key)

            # Query all transactions ordered by created_at DESC
            rows = db.all_rows()

            # Strip sensitive fields (chat_id) from each transaction row
            expenses = []
            for row in rows:
                sanitized = {k: v for k, v in row.items() if k != "chat_id"}
                expenses.append(sanitized)

            # Get only active subscriptions (already excludes chat_id)
            subscriptions = db.get_all_active_subscriptions()

            # Build response payload
            response_data = {
                "expenses": expenses,
                "config": {
                    "currency": config.currency,
                    "monthlyBudget": config.monthly_budget,
                    "budgets": config.budgets,
                },
                "subscriptions": subscriptions,
            }

            body = json.dumps(response_data, ensure_ascii=False)
            self._send_response(200, body)

        except Exception:
            print(f"Data API error: {traceback.format_exc()}")
            error_body = json.dumps({"error": "Internal server error"})
            self._send_response(500, error_body)

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_response(self, status_code: int, body: str):
        """Send an HTTP response with JSON body and CORS/cache headers."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "public, max-age=30")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
