"""AI Money Coach — Vercel serverless function.

Summarizes the signed-in user's spending for a given month and asks Google
Gemini for short, actionable money-saving, budgeting and investment tips.
No raw transaction notes are sent — only category totals and subscription
data — to keep the prompt small and avoid leaking unnecessary detail.
"""

import json
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from lib.config import load_config_from_env
from lib.db import SupabaseDB

GEMINI_MODEL = "gemini-flash-latest"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)


def _month_str(date_str: str) -> str:
    return date_str[:7]


def _build_prompt(month: str, config, category_totals: dict, month_total: float,
                   prev_total: float, subscriptions: list) -> str:
    currency = config.currency

    lines = [
        "You are a friendly personal finance coach for someone tracking expenses in India.",
        f"Currency is {currency}. Analyze this month's spending and reply ONLY with 4-6 short, "
        "specific, actionable bullet points (one sentence each, no headers, no markdown bold/asterisks).",
        "Cover: which categories to cut back on, whether subscriptions are worth trimming, "
        "at least one concrete investment or savings tip (e.g. SIP, emergency fund, PPF), "
        "and one general money habit tip.",
        "",
        f"Month: {month}",
        f"Total spent: {currency}{month_total:.0f}",
    ]

    if prev_total:
        lines.append(f"Previous month spent: {currency}{prev_total:.0f}")

    if config.monthly_budget:
        lines.append(f"Monthly budget: {currency}{config.monthly_budget}")

    if category_totals:
        lines.append("Category breakdown:")
        for cat, amount in sorted(category_totals.items(), key=lambda kv: -kv[1]):
            cap = config.budgets.get(cat)
            cap_str = f" (budget cap {currency}{cap})" if cap else ""
            lines.append(f"- {cat}: {currency}{amount:.0f}{cap_str}")

    if subscriptions:
        lines.append("Active subscriptions:")
        for sub in subscriptions:
            lines.append(f"- {sub['name']}: {currency}{sub['amount']:.0f}/{sub['cycle']}")

    return "\n".join(lines)


def _call_gemini(api_key: str, prompt: str) -> str:
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 500},
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))

    candidates = body.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts).strip()

    if not text:
        raise ValueError("Gemini returned an empty response")

    return text


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the AI Money Coach API."""

    def do_GET(self):
        """Handle GET requests — return AI-generated money tips as JSON."""
        try:
            config = load_config_from_env()

            if not config.gemini_api_key:
                self._send_response(200, json.dumps({
                    "error": "AI Money Coach is not configured. Set GEMINI_API_KEY to enable it."
                }))
                return

            query = parse_qs(urlparse(self.path).query)
            month = (query.get("month") or [None])[0]

            db = SupabaseDB(config.supabase_url, config.supabase_key)
            rows = db.all_rows()

            expenses = [
                r for r in rows
                if r.get("type") == "expense" and r.get("category") != "fav_p"
            ]

            if not month:
                dates = [r["date"] for r in expenses if r.get("date")]
                month = max(dates)[:7] if dates else ""

            month_rows = [r for r in expenses if _month_str(r.get("date", "")) == month]

            category_totals: dict = {}
            month_total = 0.0
            for r in month_rows:
                amount = float(r["amount"])
                category_totals[r["category"]] = category_totals.get(r["category"], 0.0) + amount
                month_total += amount

            prev_total = 0.0
            if month:
                year, mon = int(month[:4]), int(month[5:7])
                prev_year, prev_mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
                prev_month = f"{prev_year:04d}-{prev_mon:02d}"
                prev_total = sum(
                    float(r["amount"]) for r in expenses
                    if _month_str(r.get("date", "")) == prev_month
                )

            subscriptions = db.get_all_active_subscriptions()

            if month_total == 0 and not subscriptions:
                self._send_response(200, json.dumps({
                    "insights": "Not enough spending data yet this month — add a few "
                                "transactions and check back for personalized tips."
                }))
                return

            prompt = _build_prompt(
                month, config, category_totals, month_total, prev_total, subscriptions
            )
            insights = _call_gemini(config.gemini_api_key, prompt)

            self._send_response(200, json.dumps({"insights": insights}, ensure_ascii=False))

        except urllib.error.HTTPError as exc:
            print(f"Gemini API HTTP error: {exc.read().decode('utf-8', 'ignore')}")
            self._send_response(200, json.dumps({
                "error": "AI Money Coach couldn't reach Gemini right now. Please try again later."
            }))
        except Exception:
            print(f"Insights API error: {traceback.format_exc()}")
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
        """Send an HTTP response with JSON body and CORS headers."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
