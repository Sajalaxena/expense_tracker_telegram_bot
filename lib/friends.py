"""Money lent to and borrowed from friends: a per-person ledger.

Each friend_txns row is one of:
    lent      you gave them money         -> they owe you more
    borrowed  they gave you money         -> you owe them more
    received  they paid you back          -> they owe you less
    repaid    you paid them back          -> you owe them less
A person's balance is lent - received - borrowed + repaid; positive means
they owe you, negative means you owe them.

Telegram messages:
    lent rahul 500 · lent 500 to rahul · rahul owes me 500 for dinner
    borrowed 1000 from amit · i owe amit 1000 · amit lent me 1k
    rahul paid back 200 · rahul paid back      (no amount = everything owed)
    paid back amit 500 · repaid amit
    settle rahul · settled with amit
Verbs that also describe ordinary spending ("gave", "took", "got",
"received", "returned", "paid me", "settled") only count for people who
already have entries, so "gave 500 to maid" is still logged as an expense.
"""

import math
import re
from dataclasses import dataclass
from datetime import date

from lib.dates import extract_date, short_date
from lib.utils import indian_format

KINDS = ("lent", "borrowed", "received", "repaid")
SIGN = {"lent": 1, "borrowed": -1, "received": -1, "repaid": 1}

MAX_AMOUNT = 1_000_000_000
MAX_PERSON = 40
MAX_NOTE = 200

# Words that can't be a person's name in "<name> owes me 500"-style phrases.
_NOT_NAMES = {"i", "me", "you", "my", "we", "he", "she", "they", "him", "her", "them",
              "the", "a", "an", "back", "to", "from", "for", "with", "up"}

_NAME = (
    r"(?P<name>[a-z][a-z0-9_.'-]*"
    r"(?:\s(?!(?:for|me|back|to|from|on|rs|of|with)\b)[a-z][a-z0-9_.'-]*)?)"
)
_AMT = (
    r"(?:rs\.?\s*|₹\s*)?(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<suffix>[kl])?"
    r"(?:\s*(?:rs|rupees|/-))?"
)
_NOTE = r"(?:\s+(?:for\s+)?(?P<note>.+?))?"
_I = r"(?:i\s+)?"

# (kind, only for people already in the ledger, pattern) — first match wins.
_PATTERNS = [(kind, known, re.compile(p.format(N=_NAME, A=_AMT, T=_NOTE, I=_I))) for kind, known, p in [
    ("lent", False, r"{I}(?:lent|lend)\s+{N}\s+{A}{T}"),
    ("lent", False, r"{I}(?:lent|lend)\s+{A}\s+to\s+{N}{T}"),
    ("lent", False, r"{N}\s+owes\s+me\s+{A}{T}"),
    ("lent", True, r"{I}(?:gave|given)\s+{N}\s+{A}{T}"),
    ("lent", True, r"{I}(?:gave|given)\s+{A}\s+to\s+{N}{T}"),
    ("lent", False, r"{N}\s+borrowed\s+{A}(?:\s+from\s+me)?{T}"),
    ("lent", True, r"{N}\s+took\s+{A}(?:\s+from\s+me)?{T}"),
    ("borrowed", False, r"{I}borrowed\s+{A}\s+from\s+{N}{T}"),
    ("borrowed", False, r"{I}borrowed\s+from\s+{N}\s+{A}{T}"),
    ("borrowed", False, r"{I}owe\s+{N}\s+{A}{T}"),
    ("borrowed", False, r"{N}\s+lent\s+me\s+{A}{T}"),
    ("borrowed", True, r"{N}\s+gave\s+me\s+{A}{T}"),
    ("borrowed", True, r"{I}took\s+{A}\s+from\s+{N}{T}"),
    ("received", False, r"{N}\s+(?:paid\s+(?:me\s+)?back|repaid|gave\s+(?:me\s+)?back)(?:\s+{A})?{T}"),
    ("received", True, r"{N}\s+(?:returned|paid\s+me)(?:\s+{A})?{T}"),
    ("received", True, r"(?:got|received)\s+(?:back\s+)?{A}\s+(?:back\s+)?from\s+{N}{T}"),
    ("repaid", False, r"{I}(?:paid\s+back|repaid|gave\s+back)\s+{N}(?:\s+{A})?{T}"),
    ("repaid", False, r"{I}(?:paid\s+back|repaid|gave\s+back|returned)\s+{A}\s+to\s+{N}{T}"),
    ("repaid", False, r"{I}paid\s+{N}\s+back(?:\s+{A})?{T}"),
    ("repaid", True, r"{I}returned\s+{N}\s+{A}{T}"),
    ("settle", True, r"settled?(?:\s+up)?\s+(?:with\s+)?{N}"),
    ("settle", True, r"{N}\s+settled(?:\s+up)?"),
]]


@dataclass
class FriendIntent:
    kind: str                 # one of KINDS, or "settle"
    person: str               # lowercase
    amount: float | None      # None = the whole balance (paid back / settle)
    note: str
    date: date
    needs_known: bool         # only valid if `person` already has entries


def display_name(person: str) -> str:
    return " ".join(word.capitalize() for word in person.split())


def balances(entries: list[dict]) -> dict[str, float]:
    """{person: balance}; positive = they owe you, negative = you owe them."""
    totals: dict[str, float] = {}
    for entry in entries:
        person = entry["person"]
        totals[person] = round(totals.get(person, 0.0) + SIGN[entry["kind"]] * float(entry["amount"]), 2)
    return totals


def balance_line(person: str, balance: float, currency: str) -> str:
    name = display_name(person)
    if balance > 0:
        return f"{name} owes you {indian_format(balance, currency)}"
    if balance < 0:
        return f"You owe {name} {indian_format(-balance, currency)}"
    return f"All settled with {name} ✅"


def settlement(balance: float) -> tuple[str, float] | None:
    """The (kind, amount) entry that brings `balance` to zero, or None if already settled."""
    if balance > 0:
        return "received", balance
    if balance < 0:
        return "repaid", -balance
    return None


# ---------------------------------------------------------------------------
# Telegram parsing and replies
# ---------------------------------------------------------------------------


def parse_friend_message(text: str, today: date) -> FriendIntent | None:
    """Match one lent/borrowed/paid-back/settle phrase, or return None."""
    lowered = re.sub(r"\s+", " ", text.lower()).strip()
    when, rest = extract_date(lowered, today)
    for kind, needs_known, pattern in _PATTERNS:
        match = pattern.fullmatch(rest)
        if not match:
            continue
        groups = match.groupdict()
        person = groups["name"].strip()
        if person in _NOT_NAMES or person.split()[0] in _NOT_NAMES:
            continue
        note = (groups.get("note") or "").strip()
        amount = None
        if groups.get("amount"):
            amount = float(groups["amount"].replace(",", ""))
            amount *= {"k": 1000, "l": 100000}.get(groups.get("suffix") or "", 1)
            if not 0 < amount <= MAX_AMOUNT:
                return None
        elif kind in ("lent", "borrowed"):
            continue
        elif note:
            # "rahul returned the book" is not a repayment of everything owed.
            return None
        return FriendIntent(kind, person[:MAX_PERSON], amount, note[:MAX_NOTE],
                            when or today, needs_known)
    return None


def entry_reply(entry: dict, balance: float, currency: str, today: date) -> str:
    """Confirmation for a ledger entry just added; `balance` is the person's new balance."""
    name = display_name(entry["person"])
    amount = indian_format(float(entry["amount"]), currency)
    headline = {
        "lent": f"🤝 Lent {amount} to {name}",
        "borrowed": f"🤝 Borrowed {amount} from {name}",
        "received": f"💸 {name} paid you back {amount}",
        "repaid": f"💸 Paid back {amount} to {name}",
    }[entry["kind"]]
    if entry.get("note"):
        headline += f" — {entry['note']}"
    if entry["date"] != today.isoformat():
        headline += f" · 📅 {short_date(date.fromisoformat(entry['date']))}"
    return f"{headline}\n{balance_line(entry['person'], balance, currency)}"


def friends_overview(entries: list[dict], currency: str) -> str:
    """/friends — everyone with an open balance, then the net."""
    open_balances = {p: b for p, b in balances(entries).items() if b}
    if not open_balances:
        return ("🤝 Nobody owes anybody. Log with \"lent rahul 500\" or "
                "\"borrowed 1000 from amit\".")
    lines = ["🤝 Friends"]
    for person, balance in sorted(open_balances.items(), key=lambda kv: -kv[1]):
        lines.append(("🟢 " if balance > 0 else "🔴 ") + balance_line(person, balance, currency))
    owed_to_you = sum(b for b in open_balances.values() if b > 0)
    you_owe = -sum(b for b in open_balances.values() if b < 0)
    lines.append("")
    lines.append(f"Owed to you: {indian_format(owed_to_you, currency)}")
    lines.append(f"You owe: {indian_format(you_owe, currency)}")
    lines.append("\nSettle up with \"rahul paid back\" or \"settle rahul\".")
    return "\n".join(lines)


def undo_markup(entry_id: int) -> dict:
    return {"inline_keyboard": [[{"text": "↩️ Undo", "callback_data": f"fu:{entry_id}"}]]}


# ---------------------------------------------------------------------------
# Validation (dashboard add / edit)
# ---------------------------------------------------------------------------


def validate_entry(payload, today: date) -> tuple[dict | None, str | None]:
    """Validate {person, kind, amount, note?, date?}. Returns (row fields, None) or (None, error)."""
    if not isinstance(payload, dict):
        return None, "Expected a JSON object."

    person = re.sub(r"\s+", " ", str(payload.get("person") or "")).strip().lower()
    if not person:
        return None, "Name is required."
    if len(person) > MAX_PERSON:
        return None, f"Name must be {MAX_PERSON} characters or fewer."

    kind = payload.get("kind")
    if kind not in KINDS:
        return None, f"Type must be one of: {', '.join(KINDS)}."

    amount = payload.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or math.isnan(amount) \
            or not 0 < amount <= MAX_AMOUNT:
        return None, "Amount must be a positive number."

    when = payload.get("date") or today.isoformat()
    try:
        when = date.fromisoformat(str(when)).isoformat()
    except ValueError:
        return None, "Date must be a date (YYYY-MM-DD)."

    return {
        "person": person,
        "kind": kind,
        "amount": round(float(amount), 2),
        "note": str(payload.get("note") or "").strip()[:MAX_NOTE],
        "date": when,
    }, None
