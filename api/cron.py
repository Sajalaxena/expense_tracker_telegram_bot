"""Daily scheduled job — Vercel Cron (see "crons" in vercel.json).

Runs once a day at 22:00 IST and, for every chat that uses the bot:
  1. Reminds you if nothing was logged today.
  2. Auto-logs subscription renewals that fell due this period (once each,
     tracked by subscriptions.last_billed), with buttons to undo.
  3. On Sundays, sends a weekly spending summary.

Vercel calls it with "Authorization: Bearer $CRON_SECRET"; without a
configured CRON_SECRET every request is refused.
"""

import hmac
import json
import traceback
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler

from lib.config import apply_budget_overrides, load_config_from_env
from lib.dates import local_today, local_tz, short_date
from lib.db import SupabaseDB
from lib.entries import log_transactions
from lib.parser import Transaction
from lib.telegram import send_telegram_message
from lib.utils import indian_format


# ---------------------------------------------------------------------------
# Subscription renewals
# ---------------------------------------------------------------------------


def _created_local_date(created_at: str) -> date:
    """created_at is a UTC timestamp; renewal days follow the local calendar."""
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if created.tzinfo is None:
        return created.date()
    return created.astimezone(local_tz()).date()


def due_date(created: date, cycle: str, today: date) -> date:
    """This period's renewal date: same day-of-month (monthly) or same day-of-year
    (yearly) as the subscription was added, clamped to short months (31st -> 30th)."""
    month = created.month if cycle == "yearly" else today.month
    day = min(created.day, monthrange(today.year, month)[1])
    return date(today.year, month, day)


def renewals_due(subs: list[dict], today: date) -> list[tuple[dict, date]]:
    """Subscriptions whose renewal for the current period is due and not yet logged."""
    due = []
    for sub in subs:
        if not sub.get("created_at"):
            continue
        created = _created_local_date(sub["created_at"])
        renewal = due_date(created, sub.get("cycle", "monthly"), today)
        if renewal > today or renewal < created:
            continue
        last = sub.get("last_billed")
        if last and date.fromisoformat(last) >= renewal:
            continue
        due.append((sub, renewal))
    return due


# ---------------------------------------------------------------------------
# Weekly summary
# ---------------------------------------------------------------------------


def weekly_summary(rows: list[dict], today: date, config, month_total: float) -> str | None:
    """Summary of the 7 days ending today vs the 7 days before, or None if both are empty."""
    currency = config.currency
    week_start = today - timedelta(days=6)
    prev_start = week_start - timedelta(days=7)

    this_week, last_week_total = [], 0.0
    for row in rows:
        if row.get("type") != "expense" or row.get("category") == "fav_p" or not row.get("date"):
            continue
        d = date.fromisoformat(row["date"])
        if week_start <= d <= today:
            this_week.append(row)
        elif prev_start <= d < week_start:
            last_week_total += float(row["amount"])

    if not this_week and not last_week_total:
        return None

    total = sum(float(r["amount"]) for r in this_week)
    by_category: dict = defaultdict(float)
    for r in this_week:
        by_category[r["category"]] += float(r["amount"])

    lines = [f"📅 Your week ({short_date(week_start)} – {short_date(today)})", ""]
    spent = f"Spent: {indian_format(total, currency)}"
    if last_week_total > 0:
        change = (total - last_week_total) / last_week_total * 100
        arrow = "↑" if change > 0 else "↓"
        spent += (
            f" ({arrow}{abs(change):.0f}% vs last week's "
            f"{indian_format(last_week_total, currency)})"
        )
    lines.append(spent)

    if by_category:
        top = sorted(by_category.items(), key=lambda kv: -kv[1])[:3]
        lines.append("Top: " + ", ".join(f"{c} {indian_format(a, currency)}" for c, a in top))
    if this_week:
        biggest = max(this_week, key=lambda r: float(r["amount"]))
        lines.append(
            f"Biggest: {indian_format(float(biggest['amount']), currency)} — "
            f"{biggest.get('note') or biggest['category']}"
        )

    budget = config.monthly_budget
    if budget:
        days_left = monthrange(today.year, today.month)[1] - today.day
        lines.append(
            f"\nMonth so far: {indian_format(month_total, currency)} of "
            f"{indian_format(budget, currency)} ({month_total / budget * 100:.0f}%) · "
            f"{days_left} day{'s' if days_left != 1 else ''} left"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def run_daily(db, config, today: date) -> dict:
    """Run all daily tasks; one chat failing doesn't stop the others."""
    token = config.telegram_token
    stats = {"chats": 0, "reminders": 0, "renewals": 0, "summaries": 0}

    try:
        subs_by_chat = defaultdict(list)
        for sub in db.subscriptions_for_billing():
            subs_by_chat[sub["chat_id"]].append(sub)
    except Exception as exc:
        # Most likely the last_billed column is missing (migration not run yet).
        print(f"Skipping subscription renewals: {exc}")
        subs_by_chat = defaultdict(list)

    month_total = None
    for chat_id in db.chat_ids():
        stats["chats"] += 1
        try:
            rows = db.rows_since(chat_id, today - timedelta(days=13))

            # 1. Reminder — checked before renewals so auto-logged entries don't count.
            if not any(r.get("date") == today.isoformat() for r in rows):
                send_telegram_message(
                    chat_id,
                    "📝 Nothing logged today. Spent anything? Just send it here, "
                    "e.g. \"chai 30, auto 80\".",
                    token,
                )
                stats["reminders"] += 1

            # 2. Subscription renewals
            due = renewals_due(subs_by_chat.get(chat_id, []), today)
            if due:
                txns = [
                    Transaction(float(sub["amount"]), "subscriptions", sub["name"], "expense", renewal)
                    for sub, renewal in due
                ]
                reply = log_transactions(txns, chat_id, db, config, header="🔁 Subscription renewals logged:")
                for sub, renewal in due:
                    db.mark_subscription_billed(sub["id"], renewal)
                send_telegram_message(chat_id, reply.text, token, reply.markup)
                stats["renewals"] += len(due)

            # 3. Weekly summary on Sundays
            if today.weekday() == 6:
                if month_total is None:
                    month_total = db.month_total(today.strftime("%Y-%m"))
                summary = weekly_summary(rows, today, config, month_total)
                if summary:
                    send_telegram_message(chat_id, summary, token)
                    stats["summaries"] += 1
        except Exception:
            print(f"Cron error for chat {chat_id}: {traceback.format_exc()}")

    return stats


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the daily cron job."""

    def do_GET(self):
        try:
            config = load_config_from_env()

            expected = f"Bearer {config.cron_secret}"
            given = self.headers.get("Authorization", "")
            if not config.cron_secret or not hmac.compare_digest(given.encode(), expected.encode()):
                self._send_response(401, {"error": "Unauthorized"})
                return

            db = SupabaseDB(config.supabase_url, config.supabase_key)
            apply_budget_overrides(config, db)
            stats = run_daily(db, config, local_today())
            print(f"Cron finished: {stats}")
            self._send_response(200, stats)
        except Exception:
            print(f"Cron error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Internal server error"})

    def _send_response(self, status_code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
