"""Gated static page server — Vercel serverless function.

vercel.json rewrites the app's real pages (/, /dashboard.html, /daily-details.html,
/insights.html, /fav-p.html) to this function with a `page` query param, so the
HTML is only ever sent to a browser holding a valid session cookie. Anyone else
is redirected to /login.html, which is served directly (not rewritten) and
therefore always reachable.

The gated pages live in /templates, NOT /public. Vercel checks the filesystem
for a literal static-file match before evaluating rewrites, so a file sitting
in /public at the same path as a rewrite source (e.g. public/dashboard.html
vs. a rewrite for /dashboard.html) would be served directly and silently skip
this gate entirely. Keeping them out of /public is what makes the rewrite
(and therefore the auth check below) actually run.
"""

import os
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from lib.auth import is_authenticated
from lib.config import load_config_from_env

PAGES = {
    "index": "index.html",
    "dashboard": "dashboard.html",
    "daily-details": "daily-details.html",
    "insights": "insights.html",
    "fav-p": "fav-p.html",
}

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for gated pages."""

    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            page = (query.get("page") or [""])[0]
            filename = PAGES.get(page)

            if not filename:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            try:
                config = load_config_from_env()
                authed = bool(config.session_secret) and is_authenticated(
                    self.headers, config.session_secret
                )
            except ValueError:
                authed = False

            if not authed:
                self.send_response(302)
                self.send_header("Location", "/login.html")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            with open(os.path.join(TEMPLATES_DIR, filename), "rb") as f:
                body = f.read()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        except Exception:
            print(f"Page gate error: {traceback.format_exc()}")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Internal server error")
