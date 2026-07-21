"""Telegram bot module — orchestrates parser, database, exporter, and config."""

import json
import sys
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.budget import check_overspend, parse_simple_amount, update_budget
from src.db import Database
from src.export import regenerate
from src.parser import CATEGORY_KEYWORDS, ParseError, parse
from src.utils import indian_format


def load_config(config_path: str = "config.json") -> dict:
    """
    Load configuration from JSON file or environment variables.

    On Render/cloud: reads TELEGRAM_TOKEN and MONTHLY_BUDGET from env vars.
    Locally: reads from config.json as before.
    """
    import os

    # If env var is set (cloud deployment), build config from env
    token_from_env = os.environ.get("TELEGRAM_TOKEN")
    if token_from_env:
        config = {
            "telegram_token": token_from_env,
            "currency": os.environ.get("CURRENCY", "₹"),
            "monthlyBudget": int(os.environ.get("MONTHLY_BUDGET", "50000")),
            "budgets": {},
        }
        # Try loading budgets from config.json if it exists
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.loads(f.read())
            config["budgets"] = file_config.get("budgets", {})
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return config

    # Otherwise, load from config.json (local dev)
    # Load the file
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        sys.exit("config.json not found. See README for setup instructions.")
    except OSError as e:
        sys.exit(f"Failed to read config.json: {e}")

    # Parse JSON
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.exit(f"config.json contains invalid JSON: {e}")

    # Validate telegram_token
    token = config.get("telegram_token")
    if not token or not isinstance(token, str) or not token.strip():
        sys.exit("telegram_token is required in config.json")

    # Validate monthlyBudget
    budget = config.get("monthlyBudget")
    if budget is None:
        sys.exit("monthlyBudget must be a positive number in config.json")
    if not isinstance(budget, (int, float)) or budget <= 0:
        sys.exit("monthlyBudget must be a positive number in config.json")

    # Apply defaults
    if "currency" not in config:
        config["currency"] = "₹"
    if "budgets" not in config:
        config["budgets"] = {}

    return config


# Module-level references set during main() startup
_db: Database | None = None
_config: dict | None = None
_export_path: str | None = None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages: parse → store → export → reply."""
    message_text = update.message.text
    chat_id = update.message.chat_id
    currency = _config.get("currency", "₹")
    budget = _config.get("monthlyBudget", 0)

    try:
        txn = parse(message_text)
    except ParseError as e:
        await update.message.reply_text(
            f"❌ {e}\n\nExample: \"swiggy 450\" or \"1.5k uber\""
        )
        return

    # Store transaction
    _db.add(txn, chat_id)

    # Regenerate export
    regenerate(_db, _config, _export_path)

    # Get month total
    current_month = datetime.now().strftime("%Y-%m")
    month_total = _db.month_total(current_month)

    # Build confirmation reply
    amount_str = indian_format(txn.amount, currency)
    total_str = indian_format(month_total, currency)
    budget_str = indian_format(budget, currency)

    reply = f"✅ {amount_str} • {txn.category}\nMonth: {total_str} / {budget_str}"

    # Check overspend (only for expenses, not income)
    if txn.type == "expense":
        try:
            warning = check_overspend(_db, _config, txn.category, current_month)
            if warning:
                reply += f"\n{warning}"
        except Exception:
            pass  # Silently skip overspend check on failure

    await update.message.reply_text(reply)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — welcome message with usage instructions."""
    await update.message.reply_text(
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


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command — usage instructions and examples."""
    await update.message.reply_text(
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
        "/budget — per-category budget status"
    )


async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /total command — current month total, budget, and progress bar."""
    currency = _config.get("currency", "₹")
    budget = _config.get("monthlyBudget", 0)

    current_month = datetime.now().strftime("%Y-%m")
    month_total = _db.month_total(current_month)

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

    await update.message.reply_text(
        f"📊 {month_name}\n\n"
        f"Spent: {total_str}\n"
        f"Budget: {budget_str}\n\n"
        f"[{bar}] {pct_display}%"
    )


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /undo command — delete last transaction, regenerate export, confirm."""
    chat_id = update.message.chat_id
    currency = _config.get("currency", "₹")

    entry = _db.undo_last(chat_id)

    if entry is None:
        await update.message.reply_text("Nothing to undo.")
        return

    # Regenerate export after deletion
    regenerate(_db, _config, _export_path)

    amount_str = indian_format(entry["amount"], currency)
    await update.message.reply_text(
        f"🗑️ Deleted: {amount_str} • {entry['category']} — {entry['note']}"
    )


async def cmd_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sub command — list active subscriptions with monthly costs."""
    chat_id = update.message.chat_id
    currency = _config.get("currency", "₹")

    subs = _db.get_active_subscriptions(chat_id)

    if not subs:
        await update.message.reply_text(
            "No active subscriptions. Use /addsub to add one."
        )
        return

    lines = ["📋 Active Subscriptions:\n"]
    total_monthly = 0.0

    for sub in subs:
        name = sub["name"]
        amount = sub["amount"]
        cycle = sub["cycle"]

        if cycle == "yearly":
            monthly_cost = round(amount / 12, 2)
            amount_str = indian_format(monthly_cost, currency)
            cycle_label = "month"
        else:
            monthly_cost = amount
            amount_str = indian_format(amount, currency)
            cycle_label = "month"

        total_monthly += monthly_cost
        lines.append(f"• {name} — {amount_str}/{cycle_label}")

    total_str = indian_format(total_monthly, currency)
    lines.append(f"\nTotal: {total_str}/month")

    await update.message.reply_text("\n".join(lines))


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /budget command — per-category budget caps and current spend."""
    currency = _config.get("currency", "₹")
    budgets = _config.get("budgets", {})
    current_month = datetime.now().strftime("%Y-%m")

    if not budgets:
        await update.message.reply_text(
            "No per-category budgets configured.\n"
            "Add a \"budgets\" section to config.json."
        )
        return

    # Get all rows for current month to compute per-category spend
    all_rows = _db.all_rows()
    category_spend: dict[str, float] = {}
    for row in all_rows:
        if row["type"] == "expense" and row["date"].startswith(current_month + "-"):
            cat = row["category"]
            category_spend[cat] = category_spend.get(cat, 0) + row["amount"]

    lines = ["💰 Category Budgets\n"]
    for category, cap in sorted(budgets.items()):
        spent = category_spend.get(category, 0)
        spent_str = indian_format(spent, currency)
        cap_str = indian_format(cap, currency)
        indicator = "🔴" if spent > cap else "🟢"
        lines.append(f"{indicator} {category}: {spent_str} / {cap_str}")

    await update.message.reply_text("\n".join(lines))


async def cmd_setbudget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /setbudget command — update a per-category budget cap.

    Usage: /setbudget <category> <amount>
    Example: /setbudget food 10000

    Validates category against config budgets keys and CATEGORY_KEYWORDS.
    Supports k/K (×1000) and l/L (×100000) amount suffixes.
    Persists to config.json atomically and regenerates data.js.
    """
    args = context.args

    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: /setbudget <category> <amount>\n"
            "Example: /setbudget food 10000"
        )
        return

    category = args[0].lower()
    amount_str = args[1]

    # Validate category: must be in existing budgets or CATEGORY_KEYWORDS or special categories
    valid_categories = set(_config.get("budgets", {}).keys())
    valid_categories.update(CATEGORY_KEYWORDS.keys())
    valid_categories.add("subscriptions")
    valid_categories.add("other")

    if category not in valid_categories:
        await update.message.reply_text(
            f"Unknown category '{category}'.\n"
            f"Valid: {', '.join(sorted(valid_categories))}"
        )
        return

    # Parse amount with k/l suffix support
    try:
        amount = parse_simple_amount(amount_str)
    except ValueError:
        await update.message.reply_text(
            "Invalid amount. Use a number like 10000 or 10k"
        )
        return

    if amount <= 0:
        await update.message.reply_text("Budget amount must be positive")
        return

    # Update config and persist to disk
    try:
        update_budget(_config, "config.json", category, amount)
    except OSError:
        await update.message.reply_text(
            "❌ Failed to save budget. Please try again."
        )
        return

    # Regenerate export so dashboard sees updated budgets
    regenerate(_db, _config, _export_path)

    # Confirm
    currency = _config.get("currency", "₹")
    amount_formatted = indian_format(amount, currency)
    await update.message.reply_text(
        f"✅ Budget for {category} updated to {amount_formatted}"
    )


async def cmd_addsub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /addsub command — add a recurring subscription."""
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /addsub <name> <amount> <monthly|yearly>\n"
            "Example: /addsub netflix 199 monthly"
        )
        return

    name = args[0].lower()
    amount_str = args[1]
    cycle = args[2].lower()

    # Validate name length
    if len(name) > 50:
        await update.message.reply_text(
            "❌ Name must be 50 characters or fewer."
        )
        return

    # Validate cycle
    if cycle not in ("monthly", "yearly"):
        await update.message.reply_text(
            "❌ Cycle must be 'monthly' or 'yearly'."
        )
        return

    # Parse amount (supports k/l suffixes)
    try:
        amount = parse_simple_amount(amount_str)
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Use a number like 199 or 2.5k"
        )
        return

    # Validate amount range (1 to 10,000,000)
    if amount < 1 or amount > 10_000_000:
        await update.message.reply_text(
            "❌ Amount must be between 1 and 10,000,000."
        )
        return

    # Store subscription
    chat_id = update.message.chat_id
    _db.add_subscription(name, amount, cycle, chat_id)

    # Regenerate export
    regenerate(_db, _config, _export_path)

    # Confirm
    currency = _config.get("currency", "₹")
    formatted_amount = indian_format(amount, currency)
    cycle_label = "month" if cycle == "monthly" else "year"
    await update.message.reply_text(
        f"✅ Subscription added: {name} — {formatted_amount}/{cycle_label}"
    )


async def cmd_removesub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /removesub command — deactivate a subscription by name.

    Usage: /removesub <name>
    Example: /removesub netflix

    Performs case-insensitive exact match against active subscription names.
    Marks the matching subscription as inactive and confirms removal.
    """
    args = context.args

    if not args:
        await update.message.reply_text(
            "Usage: /removesub <name>\n"
            "Example: /removesub netflix"
        )
        return

    name = args[0].lower()
    chat_id = update.message.chat_id

    # Get all active subscriptions for this chat
    active_subs = _db.get_active_subscriptions(chat_id)

    # Find subscription with case-insensitive exact match
    match = None
    for sub in active_subs:
        if sub["name"].lower() == name:
            match = sub
            break

    if match is None:
        await update.message.reply_text(
            f"No active subscription found matching '{name}'"
        )
        return

    # Deactivate the subscription
    _db.deactivate_subscription(match["id"], chat_id)

    # Regenerate export
    regenerate(_db, _config, _export_path)

    await update.message.reply_text(f"✅ Removed subscription: {name}")


def main() -> None:
    """Load config, validate, build Application, register handlers, and run polling."""
    global _db, _config, _export_path

    _config = load_config("config.json")

    # Initialize database
    _db = Database("tracksy.db")

    # Export path: data.js in the same directory as the dashboard
    _export_path = "data.js"

    # Build the python-telegram-bot Application with the validated token
    app = Application.builder().token(_config["telegram_token"]).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("total", cmd_total))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("addsub", cmd_addsub))
    app.add_handler(CommandHandler("removesub", cmd_removesub))
    app.add_handler(CommandHandler("setbudget", cmd_setbudget))

    # Register message handler for plain text (non-command messages)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start polling
    app.run_polling()


if __name__ == "__main__":
    main()
