"""Telegram replies for logged entries, with inline action buttons.

A reply lists each entry as "(#id)". Button taps are stateless: the callback
handler re-reads those ids from the message text, applies the action, and
re-renders the whole message from the database, so entries deleted earlier
show as "🗑️ #id deleted" and everything else reflects current values.

Callback data (max 64 bytes):
    d:<id>        delete
    y:<id>        move date one day back
    c:<id>        show category picker
    s:<id>:<cat>  set category
    b:<id>        back from picker (just re-render)
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from lib.budget import budget_alerts
from lib.dates import local_today, short_date
from lib.parser import CATEGORY_KEYWORDS
from lib.utils import indian_format

CATEGORY_CHOICES = sorted(c for c in CATEGORY_KEYWORDS if c != "fav_p") + ["other", "fav_p"]

_ID_RE = re.compile(r"\(#(\d+)\)|🗑️ #(\d+) deleted")


@dataclass
class Reply:
    text: str
    markup: dict | None = None


def ids_in(text: str) -> list[int]:
    """Entry ids in a rendered entries message, in order, without duplicates."""
    seen = []
    for match in _ID_RE.finditer(text or ""):
        txn_id = int(match.group(1) or match.group(2))
        if txn_id not in seen:
            seen.append(txn_id)
    return seen


def _category_label(category: str) -> str:
    return "Personal Favorites" if category == "fav_p" else category


def _entry_line(row: dict, currency: str, today: date) -> str:
    amount = indian_format(float(row["amount"]), currency)
    if row.get("type") == "income":
        amount = "+" + amount
    line = f"✅ {amount} • {_category_label(row['category'])}"
    note = (row.get("note") or "").strip()
    if note and note.lower() != row["category"]:
        line += f" — {note}"
    line += f" (#{row['id']})"
    if row.get("date") and row["date"] != today.isoformat():
        line += f" · 📅 {short_date(date.fromisoformat(row['date']))}"
    return line


def entry_keyboard(rows: list[dict]) -> dict | None:
    """One row of buttons per entry (labelled with the note when there are several)."""
    if not rows:
        return None
    keyboard = []
    for row in rows:
        txn_id = row["id"]
        if len(rows) == 1:
            labels = ("🗑 Delete", "🏷 Category", "📅 −1 day")
        else:
            labels = (f"🗑 {(row.get('note') or row['category'])[:14]}", "🏷", "📅 −1d")
        keyboard.append([
            {"text": labels[0], "callback_data": f"d:{txn_id}"},
            {"text": labels[1], "callback_data": f"c:{txn_id}"},
            {"text": labels[2], "callback_data": f"y:{txn_id}"},
        ])
    return {"inline_keyboard": keyboard}


def category_keyboard(txn_id: int) -> dict:
    """Category picker for one entry, three per row, plus a back button."""
    buttons = [
        {"text": "⭐ fav" if c == "fav_p" else c, "callback_data": f"s:{txn_id}:{c}"}
        for c in CATEGORY_CHOICES
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([{"text": "↩ Back", "callback_data": f"b:{txn_id}"}])
    return {"inline_keyboard": rows}


def render_entries(ids: list[int], rows: list[dict], db, config,
                   header: str = "", alerts: list[str] | None = None) -> Reply:
    """Build the entries message: one line per id, month footer, alerts, buttons."""
    today = local_today()
    by_id = {row["id"]: row for row in rows}
    live = [by_id[i] for i in ids if i in by_id]

    lines = [header] if header else []
    for txn_id in ids:
        row = by_id.get(txn_id)
        lines.append(_entry_line(row, config.currency, today) if row else f"🗑️ #{txn_id} deleted")

    if live and all(row["category"] == "fav_p" for row in live):
        lines.append("(Not counted in monthly budget)")
    else:
        try:
            total = db.month_total(today.strftime("%Y-%m"))
            lines.append(
                f"Month: {indian_format(total, config.currency)} / "
                f"{indian_format(config.monthly_budget, config.currency)}"
            )
        except Exception as exc:
            print(f"Month total unavailable: {exc}")

    lines.extend(alerts or [])
    return Reply("\n".join(lines), entry_keyboard(live))


def log_transactions(txns: list, chat_id: int, db, config, header: str = "") -> Reply:
    """Insert parsed transactions and build the confirmation reply with buttons."""
    today = local_today()
    rows = []
    try:
        for txn in txns:
            txn_id = db.add(txn, chat_id)
            rows.append({
                "id": txn_id,
                "amount": txn.amount,
                "category": txn.category,
                "note": txn.note,
                "type": txn.type,
                "date": (txn.date or today).isoformat(),
            })
    except Exception as exc:
        print(f"DB error storing transaction: {exc}")
        if not rows:
            return Reply("⚠️ Service temporarily unavailable, please try again.")
        header = (header + "\n" if header else "") + "⚠️ Some entries could not be saved."

    # Budget alerts, grouped by the month each entry landed in.
    added_by_month: dict = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if row["type"] == "expense" and row["category"] != "fav_p":
            added_by_month[row["date"][:7]][row["category"]] += float(row["amount"])
    alerts = []
    for month, added in added_by_month.items():
        try:
            alerts.extend(budget_alerts(db, config, month, dict(added)))
        except Exception as exc:
            print(f"Budget alert check failed: {exc}")

    return render_entries([r["id"] for r in rows], rows, db, config, header, alerts)
