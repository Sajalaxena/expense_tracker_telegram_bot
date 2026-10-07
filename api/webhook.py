"""Telegram webhook handler — Vercel serverless function.

Receives POST requests from Telegram, validates the secret token, and routes:
  * slash commands            -> _handle_command
  * plain text                -> _handle_message (one or more entries)
  * photos / voice notes      -> _handle_media (read by Gemini)
  * inline button taps        -> _handle_callback
Always returns HTTP 200 to prevent Telegram retry storms.
"""

import hashlib
import json
import traceback
import urllib.error
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler

from lib.budget import parse_simple_amount
from lib.config import apply_budget_overrides, load_config_from_env
from lib.dates import local_now, local_today, short_date
from lib.db import MONTHLY_BUDGET_KEY, SupabaseDB
from lib.entries import (
    CATEGORY_CHOICES,
    Reply,
    category_keyboard,
    ids_in,
    log_transactions,
    render_entries,
)
from lib.media import extract_transactions
from lib.parser import CATEGORY_KEYWORDS, ParseError, parse_entries
from lib.ratelimit import check_rate_limit
from lib.telegram import (
    answer_callback,
    download_file,
    edit_message,
    edit_reply_markup,
    send_chat_action,
    send_telegram_message,
)
from lib.utils import indian_format


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for Telegram webhook."""

    def do_POST(self):
        """Handle incoming Telegram webhook POST requests."""
        try:
            # Load config early for secret validation
            config = load_config_from_env()

            # Step 1: Validate webhook secret token
            secret_header = self.headers.get(
                "X-Telegram-Bot-Api-Secret-Token", ""
            )
            if secret_header != config.webhook_secret:
                self._send_response(401, {"error": "Unauthorized"})
                return

            # Step 2: Deserialize request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body)

            callback = data.get("callback_query")
            message = data.get("message")
            if not callback and not message:
                self._send_response(200, {"ok": True})
                return

            # Step 3: Initialize database and layer saved budgets over env defaults
            db = SupabaseDB(config.supabase_url, config.supabase_key)
            apply_budget_overrides(config, db)

            # Step 4: Route
            if callback:
                _handle_callback(callback, db, config)
            else:
                chat_id = message["chat"]["id"]
                text = message.get("text")
                if text is not None:
                    if text.startswith("/"):
                        reply = _handle_command(text, chat_id, db, config)
                    else:
                        reply = _handle_message(text, chat_id, db, config)
                elif _media_of(message):
                    reply = _handle_media(message, chat_id, db, config)
                else:
                    reply = None

                # Step 5: Send reply via Telegram
                if reply is not None:
                    if isinstance(reply, str):
                        reply = Reply(reply)
                    send_telegram_message(chat_id, reply.text, config.telegram_token, reply.markup)

        except Exception:
            # Log error for Vercel function logs, but never fail
            print(f"Webhook error: {traceback.format_exc()}")

        # Always return 200 to Telegram
        self._send_response(200, {"ok": True})

    def _send_response(self, status_code: int, body: dict):
        """Send an HTTP response with JSON body."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


# ---------------------------------------------------------------------------
# Command Handlers (Task 5.3)
# ---------------------------------------------------------------------------


def _handle_command(text: str, chat_id: int, db: SupabaseDB, config) -> str:
    """Route slash commands to their handler functions.

    Args:
        text: The full message text (e.g. "/start" or "/addsub netflix 199 monthly").
        chat_id: Telegram chat ID.
        db: SupabaseDB instance.
        config: AppConfig instance.

    Returns:
        Reply text string to send back to the user.
    """
    # Split command and arguments
    parts = text.strip().split()
    command = parts[0].lower().split("@")[0]  # Handle "/start@botname"
    args = parts[1:]

    try:
        if command == "/start":
            return _cmd_start()
        elif command == "/help":
            return _cmd_help()
        elif command == "/total":
            return _cmd_total(db, config)
        elif command == "/undo":
            return _cmd_undo(chat_id, db, config)
        elif command == "/delete":
            return _cmd_delete(args, chat_id, db, config)
        elif command == "/budget":
            return _cmd_budget(db, config)
        elif command == "/setbudget":
            return _cmd_setbudget(args, db, config)
        elif command == "/addsub":
            return _cmd_addsub(args, chat_id, db, config)
        elif command == "/removesub":
            return _cmd_removesub(args, chat_id, db)
        elif command == "/sub":
            return _cmd_sub(chat_id, db, config)
        else:
            return (
                "Unknown command. Use /help to see available commands."
            )
    except Exception:
        print(f"Command error: {traceback.format_exc()}")
        return "⚠️ Something went wrong. Please try again."


def _cmd_start() -> str:
    """Handle /start — welcome message with usage instructions."""
    return (
        "👋 Welcome to Tracksy!\n\n"
        "I help you track expenses right from Telegram.\n\n"
        "Just send me a message like:\n"
        "• \"swiggy 450\" — logs ₹450 under food\n"
        "• \"swiggy 450, uber 200\" — logs both\n"
        "• \"dinner 800 yesterday\" — logs it on yesterday's date\n"
        "• \"salary 50k\" — logs ₹50,000 as income\n"
        "• 📷 a receipt / UPI screenshot, or 🎙️ a voice note\n\n"
        "Tap the buttons under each entry to delete it, change its category, "
        "or move it a day back.\n\n"
        "Commands:\n"
        "/help — usage instructions\n"
        "/total — this month's spending\n"
        "/undo — delete last entry\n"
        "/delete — delete a specific entry\n"
        "/budget — per-category budgets"
    )


def _cmd_help() -> str:
    """Handle /help — formatting examples and command list."""
    return (
        "📖 How to use Tracksy:\n\n"
        "Send a message with an amount and optional description:\n\n"
        "Examples:\n"
        "• \"500 chai\" — ₹500, food\n"
        "• \"rs 1,250 groceries\" — ₹1,250, groceries\n"
        "• \"2.5k rent\" — ₹2,500, rent\n"
        "• \"1.5l apartment\" — ₹1,50,000, rent\n"
        "• \"salary 80k\" — ₹80,000, income\n\n"
        "Several at once (comma, ; or new line):\n"
        "• \"swiggy 450, uber 200, chai 30\"\n\n"
        "Past dates:\n"
        "• \"dinner 800 yesterday\" · \"cab 300 2 days ago\"\n"
        "• \"chai 30 last monday\" · \"rent 15k on 1/10\"\n\n"
        "Photos & voice:\n"
        "• Send a receipt or UPI screenshot 📷\n"
        "• Send a voice note 🎙️ — \"four fifty swiggy and two hundred uber\"\n\n"
        "Commands:\n"
        "/total — current month total vs budget\n"
        "/undo — remove last transaction\n"
        "/delete — list recent entries / delete one by #id\n"
        "/budget — per-category budget status\n"
        "/setbudget — set a budget, e.g. /setbudget food 8000\n"
        "/addsub — add a subscription\n"
        "/removesub — remove a subscription\n"
        "/sub — list subscriptions"
    )


def _cmd_total(db: SupabaseDB, config) -> str:
    """Handle /total — current month expense total with budget progress bar."""
    currency = config.currency
    budget = config.monthly_budget

    now = local_now()
    current_month = now.strftime("%Y-%m")
    month_total = db.month_total(current_month)

    total_str = indian_format(month_total, currency)
    budget_str = indian_format(budget, currency)

    # Text progress bar
    if budget > 0:
        pct = min(month_total / budget, 1.0)
        filled = int(pct * 20)
        bar = "█" * filled + "░" * (20 - filled)
        pct_display = int(pct * 100)
    else:
        bar = "░" * 20
        pct_display = 0

    month_name = now.strftime("%B %Y")

    return (
        f"📊 {month_name}\n\n"
        f"Spent: {total_str}\n"
        f"Budget: {budget_str}\n\n"
        f"[{bar}] {pct_display}%"
    )


def _cmd_undo(chat_id: int, db: SupabaseDB, config) -> str:
    """Handle /undo — delete most recent transaction, reply with details."""
    currency = config.currency

    entry = db.undo_last(chat_id)

    if entry is None:
        return "Nothing to undo."

    amount_str = indian_format(entry["amount"], currency)
    return f"🗑️ Deleted: {amount_str} • {entry['category']} — {entry['note']}"


def _cmd_delete(args: list, chat_id: int, db: SupabaseDB, config) -> str:
    """Handle /delete — list recent entries, or delete one by its #id."""
    currency = config.currency

    if not args:
        rows = db.recent(chat_id, limit=10)
        if not rows:
            return "No entries to delete."

        lines = ["🗂️ Recent entries:\n"]
        for row in rows:
            amount_str = indian_format(row["amount"], currency)
            lines.append(
                f"#{row['id']} • {row['date']} • {amount_str} • "
                f"{row['category']} — {row['note']}"
            )
        lines.append(f"\nDelete one with /delete <id>, e.g. /delete {rows[0]['id']}")
        return "\n".join(lines)

    try:
        txn_id = int(args[0].lstrip("#"))
    except ValueError:
        return "Usage: /delete <id>\nSend /delete with no id to see recent entries."

    entry = db.delete_transaction(txn_id, chat_id)
    if entry is None:
        return f"No entry found with id #{txn_id}."

    amount_str = indian_format(entry["amount"], currency)
    return f"🗑️ Deleted #{txn_id}: {amount_str} • {entry['category']} — {entry['note']}"


def _cmd_budget(db: SupabaseDB, config) -> str:
    """Handle /budget — per-category budget caps and current spend."""
    currency = config.currency
    budgets = config.budgets
    current_month = local_now().strftime("%Y-%m")

    if not budgets:
        return (
            "No per-category budgets configured.\n"
            "Set one with /setbudget <category> <amount>, e.g. /setbudget food 8000"
        )

    lines = ["💰 Category Budgets\n"]
    for category, cap in sorted(budgets.items()):
        spent = db.category_total(current_month, category)
        spent_str = indian_format(spent, currency)
        cap_str = indian_format(cap, currency)
        indicator = "🔴" if spent > cap else "🟢"
        lines.append(f"{indicator} {category}: {spent_str} / {cap_str}")

    return "\n".join(lines)


def _cmd_setbudget(args: list, db: SupabaseDB, config) -> str:
    """Handle /setbudget — save a category cap or the overall monthly budget.

    /setbudget food 8000     -> food capped at ₹8,000
    /setbudget total 60000   -> overall monthly budget (alias: monthly)
    /setbudget food 0        -> remove the food cap
    """
    valid_categories = set(config.budgets.keys())
    valid_categories.update(c for c in CATEGORY_KEYWORDS if c != "fav_p")
    valid_categories.add("other")

    if len(args) < 2:
        return (
            "Usage: /setbudget <category> <amount>\n"
            "Examples:\n"
            "  /setbudget food 8000\n"
            "  /setbudget total 60000 — overall monthly budget\n"
            "  /setbudget food 0 — remove the food cap\n\n"
            f"Categories: {', '.join(sorted(valid_categories))}"
        )

    category = args[0].lower()
    is_total = category in ("total", "monthly", "month")

    if not is_total and category not in valid_categories:
        return (
            f"Unknown category '{category}'.\n"
            f"Valid: total, {', '.join(sorted(valid_categories))}"
        )

    try:
        amount = parse_simple_amount(args[1])
    except ValueError:
        return "Invalid amount. Use a number like 10000 or 10k"

    if amount < 0 or (is_total and amount == 0):
        return "Budget amount must be positive"
    if amount > 10_000_000:
        return "Budget amount must be at most 1,00,00,000"

    try:
        db.set_budget(MONTHLY_BUDGET_KEY if is_total else category, amount)
    except Exception as exc:
        print(f"set_budget failed: {exc}")
        return (
            "⚠️ Couldn't save the budget. If this is a new setup, run "
            "supabase/migrations.sql in the Supabase SQL editor first."
        )

    currency = config.currency
    if is_total:
        return f"✅ Monthly budget set to {indian_format(amount, currency)}"
    if amount == 0:
        return f"✅ Removed the {category} budget cap"
    return f"✅ {category.capitalize()} budget set to {indian_format(amount, currency)}/month"


def _cmd_addsub(args: list, chat_id: int, db: SupabaseDB, config) -> str:
    """Handle /addsub — add new subscription with name, amount, cycle."""
    if len(args) < 3:
        return (
            "Usage: /addsub <name> <amount> <monthly|yearly>\n"
            "Example: /addsub netflix 199 monthly"
        )

    name = args[0].lower()
    amount_str = args[1]
    cycle = args[2].lower()

    # Validate name length
    if len(name) > 50:
        return "❌ Name must be 50 characters or fewer."

    # Validate cycle
    if cycle not in ("monthly", "yearly"):
        return "❌ Cycle must be 'monthly' or 'yearly'."

    # Parse amount
    try:
        amount = parse_simple_amount(amount_str)
    except ValueError:
        return "❌ Invalid amount. Use a number like 199 or 2.5k"

    # Validate amount range
    if amount < 1 or amount > 10_000_000:
        return "❌ Amount must be between 1 and 10,000,000."

    # Store subscription
    db.add_subscription(name, amount, cycle, chat_id)

    # Confirm
    currency = config.currency
    formatted_amount = indian_format(amount, currency)
    cycle_label = "month" if cycle == "monthly" else "year"
    return f"✅ Subscription added: {name} — {formatted_amount}/{cycle_label}"


def _cmd_removesub(args: list, chat_id: int, db: SupabaseDB) -> str:
    """Handle /removesub — deactivate subscription by name."""
    if not args:
        return (
            "Usage: /removesub <name>\n"
            "Example: /removesub netflix"
        )

    name = args[0].lower()

    # Get active subscriptions for this chat
    active_subs = db.get_active_subscriptions(chat_id)

    # Find match (case-insensitive)
    match = None
    for sub in active_subs:
        if sub["name"].lower() == name:
            match = sub
            break

    if match is None:
        return f"No active subscription found matching '{name}'"

    # Deactivate
    db.deactivate_subscription(match["id"], chat_id)
    return f"✅ Removed subscription: {name}"


def _cmd_sub(chat_id: int, db: SupabaseDB, config) -> str:
    """Handle /sub — list active subscriptions with monthly cost."""
    currency = config.currency
    subs = db.get_active_subscriptions(chat_id)

    if not subs:
        return "No active subscriptions. Use /addsub to add one."

    lines = ["📋 Active Subscriptions:\n"]
    total_monthly = 0.0

    for sub in subs:
        name = sub["name"]
        amount = sub["amount"]
        cycle = sub["cycle"]

        if cycle == "yearly":
            monthly_cost = round(amount / 12, 2)
            amount_str = indian_format(monthly_cost, currency)
        else:
            monthly_cost = amount
            amount_str = indian_format(amount, currency)

        total_monthly += monthly_cost
        lines.append(f"• {name} — {amount_str}/month")

    total_str = indian_format(total_monthly, currency)
    lines.append(f"\nTotal: {total_str}/month")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plain Message Handler
# ---------------------------------------------------------------------------


def _handle_message(text: str, chat_id: int, db: SupabaseDB, config) -> Reply | str:
    """Handle plain text messages — parse one or more entries and log them.

    Args:
        text: The plain-text message from the user.
        chat_id: Telegram chat ID.
        db: SupabaseDB instance.
        config: AppConfig instance.

    Returns:
        A Reply with the confirmation and action buttons, or an error string.
    """
    try:
        txns = parse_entries(text, local_today())
    except ParseError as e:
        return f"❌ {e}\n\nExample: \"swiggy 450\" or \"1.5k uber\""

    return log_transactions(txns, chat_id, db, config)


# ---------------------------------------------------------------------------
# Photos & Voice Notes
# ---------------------------------------------------------------------------


def _media_of(message: dict) -> tuple[str, str, str] | None:
    """Return (file_id, mime_type, kind) for a loggable photo/voice message."""
    if message.get("photo"):
        return message["photo"][-1]["file_id"], "image/jpeg", "photo"  # largest size
    for key in ("voice", "audio"):
        media = message.get(key)
        if media:
            return media["file_id"], media.get("mime_type") or "audio/ogg", "voice"
    doc = message.get("document")
    if doc:
        mime = doc.get("mime_type") or ""
        if mime.startswith("image/") or mime == "application/pdf":
            return doc["file_id"], mime, "photo"
    return None


def _handle_media(message: dict, chat_id: int, db: SupabaseDB, config) -> Reply | str:
    """Read a receipt/UPI screenshot or voice note with Gemini and log what it finds."""
    if not config.gemini_api_key:
        return "📷 Photo and voice logging need GEMINI_API_KEY to be set."

    client_id = hashlib.sha256(f"tg:{chat_id}".encode()).hexdigest()[:16]
    decision = check_rate_limit(db.client, "media", client_id)
    if not decision.allowed:
        return f"⏳ {decision.message}"

    file_id, mime_type, kind = _media_of(message)
    send_chat_action(chat_id, "typing", config.telegram_token)

    try:
        data = download_file(file_id, config.telegram_token)
        txns = extract_transactions(
            config.gemini_api_key, data, mime_type, kind,
            message.get("caption") or "", local_today(),
        )
    except urllib.error.HTTPError as exc:
        print(f"Gemini media error {exc.code}: {exc.read().decode('utf-8', 'ignore')[:500]}")
        return "⚠️ Couldn't reach Gemini right now. Please try again, or type the expense."
    except Exception:
        print(f"Media logging error: {traceback.format_exc()}")
        return "⚠️ Couldn't read that file. Please try again, or type the expense."

    if not txns:
        what = "photo" if kind == "photo" else "voice note"
        return f"🤔 I couldn't find an amount in that {what}. Try typing it, e.g. \"swiggy 450\"."

    header = "📷 From your photo:" if kind == "photo" else "🎙️ From your voice note:"
    return log_transactions(txns, chat_id, db, config, header=header)


# ---------------------------------------------------------------------------
# Inline Button Taps
# ---------------------------------------------------------------------------


def _handle_callback(callback: dict, db: SupabaseDB, config) -> None:
    """Apply a button tap (see lib/entries.py for the callback_data format)."""
    token = config.telegram_token
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    parts = (callback.get("data") or "").split(":")

    if not chat_id or not message_id or len(parts) < 2 or not parts[1].isdigit():
        answer_callback(callback["id"], token, "This button has expired.")
        return

    action, txn_id = parts[0], int(parts[1])

    if action == "c":
        edit_reply_markup(chat_id, message_id, category_keyboard(txn_id), token)
        answer_callback(callback["id"], token)
        return

    toast = None
    if action == "d":
        deleted = db.delete_transaction(txn_id, chat_id)
        toast = "Deleted" if deleted else "Already deleted"
    elif action == "y":
        row = next(iter(db.get_transactions([txn_id], chat_id)), None)
        if row:
            new_date = date.fromisoformat(row["date"]) - timedelta(days=1)
            db.update_transaction(txn_id, chat_id, {"date": new_date.isoformat()})
            toast = f"Date → {short_date(new_date)}"
    elif action == "s" and len(parts) == 3 and parts[2] in CATEGORY_CHOICES:
        db.update_transaction(txn_id, chat_id, {"category": parts[2], "type": "expense"})
        toast = f"Category → {parts[2]}"
    elif action != "b":
        answer_callback(callback["id"], token, "Unknown action.")
        return

    ids = ids_in(message.get("text", "")) or [txn_id]
    reply = render_entries(ids, db.get_transactions(ids, chat_id), db, config)
    edit_message(chat_id, message_id, reply.text, token, reply.markup)
    answer_callback(callback["id"], token, toast)
