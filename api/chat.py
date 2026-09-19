"""AI chat about the user's expenses — Vercel serverless function.

Accepts POST {"question": str, "history": [{"role": "user"|"model", "text": str}]},
gives Gemini a compact snapshot of the user's transactions and subscriptions
as grounding context, and returns {"reply": str}.
"""

import json
import traceback
import urllib.error
from datetime import date
from http.server import BaseHTTPRequestHandler

from lib.config import load_config_from_env
from lib.db import SupabaseDB
from lib.gemini import call_gemini

MAX_QUESTION_CHARS = 500
MAX_HISTORY_TURNS = 10
MAX_TRANSACTIONS_IN_CONTEXT = 500
MONTHS_OF_SUMMARY = 6

SYSTEM_PROMPT = (
    "You are Tracksy's friendly personal-finance assistant for a user in India. "
    "Answer questions using ONLY the expense data provided below; if the data does not "
    "contain the answer, say so plainly instead of guessing. "
    "Do the arithmetic carefully and quote exact amounts with the currency symbol. "
    "Keep replies short and conversational (under ~120 words unless the user asks for detail). "
    "Use simple formatting only: short paragraphs, '- ' bullets, and **bold** for key numbers. "
    "You can also give practical saving, budgeting and investment suggestions when asked, "
    "but you are not a licensed financial advisor — do not promise returns."
)


def _fmt(amount: float) -> str:
    return f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"


def _build_context(rows: list, subscriptions: list, config, today: date) -> str:
    currency = config.currency
    rows = [r for r in rows if r.get("category") != "fav_p" and r.get("date")]

    monthly: dict = {}
    for r in rows:
        if r.get("type") != "expense":
            continue
        month = r["date"][:7]
        cats = monthly.setdefault(month, {})
        cats[r["category"]] = cats.get(r["category"], 0.0) + float(r["amount"])

    lines = [
        f"Today's date: {today.isoformat()}",
        f"Currency: {currency}",
    ]
    if config.monthly_budget:
        lines.append(f"Monthly budget: {currency}{_fmt(config.monthly_budget)}")
    if config.budgets:
        caps = ", ".join(f"{c}: {currency}{_fmt(v)}" for c, v in config.budgets.items())
        lines.append(f"Category budget caps: {caps}")

    lines.append("")
    lines.append("Expense totals by month (most recent first):")
    for month in sorted(monthly, reverse=True)[:MONTHS_OF_SUMMARY]:
        cats = monthly[month]
        total = sum(cats.values())
        breakdown = ", ".join(
            f"{c} {_fmt(a)}" for c, a in sorted(cats.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"- {month}: total {currency}{_fmt(total)} ({breakdown})")
    if not monthly:
        lines.append("- (no expenses logged yet)")

    if subscriptions:
        lines.append("")
        lines.append("Active subscriptions:")
        for s in subscriptions:
            lines.append(f"- {s['name']}: {currency}{_fmt(float(s['amount']))}/{s['cycle']}")

    recent = sorted(rows, key=lambda r: (r["date"], r.get("id") or 0), reverse=True)
    recent = recent[:MAX_TRANSACTIONS_IN_CONTEXT]
    lines.append("")
    lines.append(
        f"Most recent {len(recent)} transactions (date | type | category | amount | note):"
    )
    for r in recent:
        note = (r.get("note") or "").replace("\n", " ").strip()
        lines.append(
            f"{r['date']} | {r.get('type', 'expense')} | {r['category']} | "
            f"{_fmt(float(r['amount']))} | {note}"
        )

    return "\n".join(lines)


def _build_contents(question: str, history: list) -> list:
    contents = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = "model" if turn.get("role") == "model" else "user"
        text = str(turn.get("text", "")).strip()[:2000]
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})

    while contents and contents[0]["role"] != "user":
        contents.pop(0)

    contents.append({"role": "user", "parts": [{"text": question}]})
    return contents


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the expense chat API."""

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = str(payload.get("question", "")).strip()
            history = payload.get("history") or []

            if not question:
                self._send_response(400, {"error": "Please type a question."})
                return
            if len(question) > MAX_QUESTION_CHARS:
                self._send_response(400, {
                    "error": f"Question is too long (max {MAX_QUESTION_CHARS} characters)."
                })
                return

            config = load_config_from_env()
            if not config.gemini_api_key:
                self._send_response(200, {
                    "error": "AI chat is not configured. Set GEMINI_API_KEY to enable it."
                })
                return

            db = SupabaseDB(config.supabase_url, config.supabase_key)
            context = _build_context(
                db.all_rows(), db.get_all_active_subscriptions(), config, date.today()
            )

            reply = call_gemini(
                config.gemini_api_key,
                _build_contents(question, history if isinstance(history, list) else []),
                system_instruction=f"{SYSTEM_PROMPT}\n\n=== USER'S EXPENSE DATA ===\n{context}",
                max_output_tokens=1000,
                temperature=0.4,
            )
            self._send_response(200, {"reply": reply})

        except urllib.error.HTTPError as exc:
            print(f"Gemini API HTTP error {exc.code}: {exc.read().decode('utf-8', 'ignore')}")
            message = "I couldn't reach Gemini right now. Please try again."
            if exc.code == 503:
                message = "Gemini is busy right now. Please try again in a moment."
            self._send_response(200, {"error": message})
        except Exception:
            print(f"Chat API error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Something went wrong. Please try again."})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_response(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
