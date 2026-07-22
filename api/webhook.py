"""Telegram webhook handler — Vercel serverless function.

Receives POST requests from Telegram, validates the secret token,
routes commands and plain messages to their respective handlers,
and always returns HTTP 200 to prevent Telegram retry storms.
"""

import json
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler

from lib.budget import check_overspend, parse_simple_amount
from lib.config import load_config_from_env
from lib.db import SupabaseDB
from lib.parser import CATEGORY_KEYWORDS, ParseError, parse
from lib.telegram import send_telegram_message
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

            # Step 3: Extract message — return 200 with no side effects if missing
            message = data.get("message")
            if not message or "text" not in message:
                self._send_response(200, {"ok": True})
                return

            text = message["text"]
            chat_id = message["chat"]["id"]

            # Step 4: Initialize database
            db = SupabaseDB(config.supabase_url, config.supabase_key)

            # Step 5: Route to appropriate handler
            if text.startswith("/"):
                reply = _handle_command(text, chat_id, db, config)
            else:
                reply = _handle_message(text, chat_id, db, config)

            # Step 6: Send reply via Telegram
            send_telegram_message(chat_id, reply, config.telegram_token)

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
        elif command == "/budget":
            return _cmd_budget(db, config)
        elif command == "/setbudget":
            return _cmd_setbudget(args, config)
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
        "• \"1.5k uber\" — logs ₹1,500 under travel\n"
        "• \"salary 50k\" — logs ₹50,000 as income\n\n"
        "Commands:\n"
        "/help — usage instructions\n"
        "/total — this month's spending\n"
        "/undo — delete last entry\n"
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
        "• \"₹450 swiggy\" — ₹450, food\n"
        "• \"salary 80k\" — ₹80,000, income\n\n"
        "Commands:\n"
        "/total — current month total vs budget\n"
        "/undo — remove last transaction\n"
        "/budget — per-category budget status\n"
        "/addsub — add a subscription\n"
        "/removesub — remove a subscription\n"
        "/sub — list subscriptions"
    )


def _cmd_total(db: SupabaseDB, config) -> str:
    """Handle /total — current month expense total with budget progress bar."""
    currency = config.currency
    budget = config.monthly_budget

    current_month = datetime.now().strftime("%Y-%m")
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

    month_name = datetime.now().strftime("%B %Y")

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


def _cmd_budget(db: SupabaseDB, config) -> str:
    """Handle /budget — per-category budget caps and current spend."""
    currency = config.currency
    budgets = config.budgets
    current_month = datetime.now().strftime("%Y-%m")

    if not budgets:
        return (
            "No per-category budgets configured.\n"
            "Set budgets via the BUDGETS environment variable."
        )

    lines = ["💰 Category Budgets\n"]
    for category, cap in sorted(budgets.items()):
        spent = db.category_total(current_month, category)
        spent_str = indian_format(spent, currency)
        cap_str = indian_format(cap, currency)
        indicator = "🔴" if spent > cap else "🟢"
        lines.append(f"{indicator} {category}: {spent_str} / {cap_str}")

    return "\n".join(lines)


def _cmd_setbudget(args: list, config) -> str:
    """Handle /setbudget — acknowledge that budgets are managed via env vars.

    In serverless mode, budget caps live in the BUDGETS environment variable.
    This command can only inform the user how to update them.
    """
    if not args or len(args) < 2:
        return (
            "Usage: /setbudget <category> <amount>\n"
            "Example: /setbudget food 10000\n\n"
            "Note: In serverless mode, budgets are managed via the "
            "BUDGETS environment variable in your Vercel project settings."
        )

    category = args[0].lower()
    amount_str = args[1]

    # Validate category
    valid_categories = set(config.budgets.keys())
    valid_categories.update(CATEGORY_KEYWORDS.keys())
    valid_categories.add("subscriptions")
    valid_categories.add("other")

    if category not in valid_categories:
        return (
            f"Unknown category '{category}'.\n"
            f"Valid: {', '.join(sorted(valid_categories))}"
        )

    # Parse amount
    try:
        amount = parse_simple_amount(amount_str)
    except ValueError:
        return "Invalid amount. Use a number like 10000 or 10k"

    if amount <= 0:
        return "Budget amount must be positive"

    currency = config.currency
    amount_formatted = indian_format(amount, currency)
    return (
        f"To set {category} budget to {amount_formatted}, update the BUDGETS "
        f"environment variable in your Vercel project settings.\n\n"
        f"Current BUDGETS value should include:\n"
        f'  "{category}": {int(amount)}'
    )


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
# Plain Message Handler (Task 5.4)
# ---------------------------------------------------------------------------


def _handle_message(text: str, chat_id: int, db: SupabaseDB, config) -> str:
    """Handle plain text messages — parse as expense/income and log.

    Args:
        text: The plain-text message from the user.
        chat_id: Telegram chat ID.
        db: SupabaseDB instance.
        config: AppConfig instance.

    Returns:
        Reply text string (confirmation or error message).
    """
    currency = config.currency
    budget = config.monthly_budget

    # Step 1: Parse the message
    try:
        txn = parse(text)
    except ParseError as e:
        return f"❌ {e}\n\nExample: \"swiggy 450\" or \"1.5k uber\""

    # Step 2: Store transaction (wrapped for Supabase failure handling)
    try:
        db.add(txn, chat_id)
    except Exception:
        print(f"DB error storing transaction: {traceback.format_exc()}")
        return "⚠️ Service temporarily unavailable, please try again."

    # Step 3: Get month total for confirmation
    try:
        current_month = datetime.now().strftime("%Y-%m")
        month_total = db.month_total(current_month)
    except Exception:
        print(f"DB error fetching month total: {traceback.format_exc()}")
        # Transaction was stored successfully, just can't get totals
        amount_str = indian_format(txn.amount, currency)
        return f"✅ {amount_str} • {txn.category}\n(Could not fetch monthly total)"

    # Step 4: Build confirmation reply
    amount_str = indian_format(txn.amount, currency)
    total_str = indian_format(month_total, currency)
    budget_str = indian_format(budget, currency)

    reply = f"✅ {amount_str} • {txn.category}\nMonth: {total_str} / {budget_str}"

    # Step 5: Check overspend (expenses only)
    if txn.type == "expense":
        try:
            warning = check_overspend(db, config, txn.category, current_month)
            if warning:
                reply += f"\n{warning}"
        except Exception:
            pass  # Silently skip overspend check on failure

    return reply
