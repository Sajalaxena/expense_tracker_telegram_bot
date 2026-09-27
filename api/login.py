"""Login endpoint — Vercel serverless function.

Checks username/password against AUTH_USERNAME/AUTH_PASSWORD and, on success,
sets a signed session cookie (see lib/auth.py). Rate-limited per client to
slow down brute-force attempts.
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler

from lib.auth import build_session_cookie_header, check_credentials
from lib.config import load_config_from_env
from lib.db import SupabaseDB
from lib.ratelimit import check_rate_limit, get_client_id


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for login."""

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))

            config = load_config_from_env()

            db = SupabaseDB(config.supabase_url, config.supabase_key)
            decision = check_rate_limit(db.client, "login", get_client_id(self.headers))
            if not decision.allowed:
                self._send_response(
                    429,
                    {"error": decision.message, "retryAfter": decision.retry_after},
                    {"Retry-After": str(decision.retry_after)},
                )
                return

            if not config.session_secret:
                self._send_response(500, {
                    "error": "Login is not configured. Set AUTH_USERNAME, AUTH_PASSWORD "
                             "and SESSION_SECRET."
                })
                return

            if not check_credentials(username, password, config.auth_username, config.auth_password):
                self._send_response(401, {"error": "Invalid username or password."})
                return

            cookie = build_session_cookie_header(self.headers, config.session_secret)
            self._send_response(200, {"ok": True}, {"Set-Cookie": cookie})

        except Exception:
            print(f"Login API error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Something went wrong. Please try again."})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Allow", "POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_response(self, status_code: int, data: dict, extra_headers: dict | None = None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
