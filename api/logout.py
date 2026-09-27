"""Logout endpoint — Vercel serverless function. Clears the session cookie."""

import json
from http.server import BaseHTTPRequestHandler

from lib.auth import build_session_cookie_header


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for logout."""

    def do_POST(self):
        cookie = build_session_cookie_header(self.headers, "", clear=True)
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Allow", "POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()
