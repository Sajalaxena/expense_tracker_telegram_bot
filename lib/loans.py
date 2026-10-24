"""Loans & EMIs: maths, validation, Telegram parsing and replies.

A loan stores its current `outstanding` balance and `emis_paid`; each EMI or
prepayment is a loan_payments row recording how much of it went to principal,
so any payment can be undone exactly (outstanding += principal).

Balances use reducing-balance interest when the loan has an interest rate:
an EMI first pays the month's interest (outstanding × rate / 12) and the rest
reduces the principal. Without a rate, `outstanding` is simply the total still
to pay and each EMI reduces it by its full amount.

Telegram messages (matched against the loan names you've added):
    home loan emi done              one EMI of the loan's usual amount
    home loan emi paid 26500        an EMI of a different amount
    home loan 2 emis done           two EMIs
    car loan lumpsum 50000          a prepayment, all of it off the principal
    car loan foreclosed             pay off whatever is outstanding
Date phrases work too: "home loan emi done yesterday".
"""

import math
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from lib.dates import extract_date, short_date
from lib.utils import indian_format

LOAN_TYPES = ("home", "car", "bike", "personal", "education", "gold", "credit_card", "consumer", "other")

MAX_AMOUNT = 1_000_000_000      # ₹100 crore
MAX_RATE = 60                   # % per year
MAX_TENURE = 600                # months
MAX_NAME = 50
MAX_TEXT = 200
MAX_EMIS_AT_ONCE = 12           # keeps the Undo button's callback data under 64 bytes

# A balance at or below this counts as fully repaid (absorbs rounding).
PAID_OFF = 1.0


# ---------------------------------------------------------------------------
# Maths
# ---------------------------------------------------------------------------


def monthly_rate(rate) -> float:
    return float(rate or 0) / 1200


def compute_emi(principal: float, rate, tenure: int) -> float:
    """Standard reducing-balance EMI."""
    r = monthly_rate(rate)
    if r == 0:
        return round(principal / tenure, 2)
    growth = (1 + r) ** tenure
    return round(principal * r * growth / (growth - 1), 2)


def balance_after(principal: float, rate, emi: float, emis_paid: int) -> float:
    """Principal left after `emis_paid` regular EMIs."""
    r = monthly_rate(rate)
    if r == 0:
        return max(0.0, round(principal - emi * emis_paid, 2))
    growth = (1 + r) ** emis_paid
    return max(0.0, round(principal * growth - emi * (growth - 1) / r, 2))


def emis_to_clear(outstanding: float, rate, emi: float) -> int | None:
    """EMIs needed to clear `outstanding` at `emi` a month (None if it never clears).

    A final leftover under 1% of an EMI (from EMIs rounded to the paisa) isn't
    counted as another EMI.
    """
    if outstanding <= PAID_OFF:
        return 0
    r = monthly_rate(rate)
    if r == 0:
        return math.ceil(outstanding / emi - 0.01)
    if emi <= outstanding * r:
        return None
    return math.ceil(-math.log(1 - r * outstanding / emi) / math.log(1 + r) - 0.01)


def split_emi(loan: dict, amount: float) -> tuple[float, float]:
    """(principal, interest) parts of an EMI paid on `loan` today."""
    outstanding = float(loan["outstanding"])
    interest = 0.0
    if loan.get("interest_rate") is not None:
        interest = round(min(outstanding * monthly_rate(loan["interest_rate"]), amount), 2)
    principal = round(min(amount - interest, outstanding), 2)
    return max(principal, 0.0), interest


def _add_months(d: date, months: int, day: int | None = None) -> date:
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day or d.day, monthrange(year, month)[1]))


def loan_status(loan: dict, payments: list[dict], today: date) -> dict:
    """Everything the Loans page and Telegram replies show about one loan.

    `payments` are this loan's loan_payments rows (any order).
    """
    tenure = int(loan["tenure_months"])
    emis_paid = int(loan["emis_paid"])
    emi = float(loan["emi_amount"])
    outstanding = float(loan["outstanding"])
    principal = float(loan["principal"])
    rate = loan.get("interest_rate")
    has_rate = rate is not None

    emis_left = max(tenure - emis_paid, 0)
    closed = emis_left == 0 or outstanding <= PAID_OFF

    # After prepayments the balance may clear sooner than the schedule says.
    at_balance = emis_to_clear(outstanding, rate, emi) if has_rate else None
    effective_left = 0 if closed else emis_left
    if not closed and at_balance is not None:
        effective_left = min(emis_left, at_balance)

    if has_rate:
        repaid_fraction = 1 - outstanding / principal
        left_to_pay = 0.0 if closed else round(effective_left * emi, 2)
    else:
        total = max(emi * tenure, principal)
        repaid_fraction = 1 - outstanding / total
        left_to_pay = 0.0 if closed else outstanding

    month = today.strftime("%Y-%m")
    paid_this_month = any(
        p["kind"] == "emi" and str(p["paid_on"]).startswith(month) for p in payments
    )

    emi_day = loan.get("emi_day")
    if not emi_day and loan.get("start_date"):
        emi_day = date.fromisoformat(str(loan["start_date"])).day

    next_due = None
    if not closed and emi_day:
        next_due = _add_months(today.replace(day=1), 1 if paid_this_month else 0, int(emi_day))

    ends = None
    if not closed and effective_left:
        ends = _add_months(today.replace(day=1), effective_left - (0 if paid_this_month else 1))

    return {
        "emis_left": emis_left,
        "emis_left_at_balance": at_balance if has_rate and not closed else None,
        "emi_progress": round(min(emis_paid / tenure, 1.0), 4),
        "repaid_progress": round(min(max(repaid_fraction, 0.0), 1.0), 4),
        "left_to_pay": left_to_pay,
        "interest_paid": round(sum(float(p.get("interest") or 0) for p in payments), 2),
        "total_paid": round(sum(float(p["amount"]) for p in payments), 2),
        "prepaid": round(sum(float(p["amount"]) for p in payments if p["kind"] == "prepayment"), 2),
        "paid_this_month": paid_this_month,
        "next_due": next_due.isoformat() if next_due else None,
        "due_in_days": (next_due - today).days if next_due else None,
        "ends": ends.strftime("%Y-%m") if ends else None,
        "closed": closed,
    }


# ---------------------------------------------------------------------------
# Validation (dashboard add / edit)
# ---------------------------------------------------------------------------


def _number(value, label: str, low: float, high: float, integer: bool = False):
    """Parse an optional number. Returns (value or None, error or None)."""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, f"{label} must be a number."
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{label} must be a number."
    if math.isnan(number) or not low <= number <= high:
        return None, f"{label} must be between {low:g} and {high:g}."
    if integer:
        if number != int(number):
            return None, f"{label} must be a whole number."
        return int(number), None
    return round(number, 2), None


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def validate_loan(payload) -> tuple[dict | None, str | None]:
    """Validate a loan from the dashboard form and fill in what can be derived.

    Required: name, principal, tenure_months, and emi_amount unless
    interest_rate is given (then the EMI is calculated). Optional: lender,
    loan_type, interest_rate, emis_paid (default 0), outstanding (calculated
    when blank), start_date, emi_day, notes.

    Returns ({column: value}, None) or (None, error message).
    """
    if not isinstance(payload, dict):
        return None, "Expected a JSON object."

    name = _text(payload.get("name"), MAX_NAME + 1)
    if not name:
        return None, "Loan name is required."
    if len(name) > MAX_NAME:
        return None, f"Loan name must be {MAX_NAME} characters or fewer."

    principal, err = _number(payload.get("principal"), "Loan amount", 1, MAX_AMOUNT)
    if err or principal is None:
        return None, err or "Loan amount is required."
    tenure, err = _number(payload.get("tenure_months"), "Total EMIs", 1, MAX_TENURE, integer=True)
    if err or tenure is None:
        return None, err or "Total EMIs (tenure in months) is required."
    rate, err = _number(payload.get("interest_rate"), "Interest rate", 0, MAX_RATE)
    if err:
        return None, err
    emi, err = _number(payload.get("emi_amount"), "EMI amount", 1, MAX_AMOUNT)
    if err:
        return None, err
    if emi is None:
        if rate is None:
            return None, "Enter the EMI amount, or the interest rate to calculate it."
        emi = compute_emi(principal, rate, tenure)

    emis_paid, err = _number(payload.get("emis_paid"), "EMIs paid", 0, MAX_TENURE, integer=True)
    if err:
        return None, err
    emis_paid = emis_paid or 0
    if emis_paid > tenure:
        return None, "EMIs paid can't be more than the total EMIs."

    outstanding, err = _number(payload.get("outstanding"), "Outstanding", 0, MAX_AMOUNT)
    if err:
        return None, err
    if outstanding is None:
        if rate is not None:
            outstanding = balance_after(principal, rate, emi, emis_paid)
        else:
            outstanding = round((tenure - emis_paid) * emi, 2)

    emi_day, err = _number(payload.get("emi_day"), "EMI day", 1, 31, integer=True)
    if err:
        return None, err

    start_date = payload.get("start_date") or None
    if start_date is not None:
        try:
            start_date = date.fromisoformat(str(start_date)).isoformat()
        except ValueError:
            return None, "Start date must be a date (YYYY-MM-DD)."

    loan_type = payload.get("loan_type") or "other"
    if loan_type not in LOAN_TYPES:
        return None, f"Loan type must be one of: {', '.join(LOAN_TYPES)}."

    return {
        "name": name,
        "lender": _text(payload.get("lender"), MAX_NAME),
        "loan_type": loan_type,
        "principal": principal,
        "interest_rate": rate,
        "emi_amount": emi,
        "tenure_months": tenure,
        "emis_paid": emis_paid,
        "outstanding": outstanding,
        "start_date": start_date,
        "emi_day": emi_day,
        "notes": _text(payload.get("notes"), MAX_TEXT),
    }, None


# ---------------------------------------------------------------------------
# Recording and undoing payments
# ---------------------------------------------------------------------------


def record_payment(db, loan: dict, kind: str, amount: float, paid_on: date,
                   note: str = "", chat_id: int | None = None) -> tuple[dict, dict]:
    """Store an EMI or prepayment and move the loan's balance.

    Returns (payment row, updated loan row).
    """
    if kind == "emi":
        principal, interest = split_emi(loan, amount)
        emis_paid = int(loan["emis_paid"]) + 1
    else:
        principal, interest = round(min(amount, float(loan["outstanding"])), 2), 0.0
        emis_paid = int(loan["emis_paid"])

    payment = db.add_loan_payment({
        "loan_id": loan["id"],
        "kind": kind,
        "amount": round(float(amount), 2),
        "principal": principal,
        "interest": interest,
        "paid_on": paid_on.isoformat(),
        "note": note,
        "chat_id": chat_id,
    })
    fields = {
        "outstanding": max(0.0, round(float(loan["outstanding"]) - principal, 2)),
        "emis_paid": emis_paid,
    }
    try:
        updated = db.update_loan(loan["id"], fields)
    except Exception:
        db.delete_loan_payment(payment["id"])
        raise
    return payment, updated or {**loan, **fields}


def reverse_payment(db, payment: dict) -> dict | None:
    """Undo a payment: give its principal back to the loan and delete it.

    Returns the updated loan, or None if the loan no longer exists.
    """
    loan = db.get_loan(payment["loan_id"])
    updated = None
    if loan:
        fields = {"outstanding": round(float(loan["outstanding"]) + float(payment.get("principal") or 0), 2)}
        if payment["kind"] == "emi":
            fields["emis_paid"] = max(int(loan["emis_paid"]) - 1, 0)
        updated = db.update_loan(loan["id"], fields)
    db.delete_loan_payment(payment["id"])
    return updated


# ---------------------------------------------------------------------------
# Telegram parsing
# ---------------------------------------------------------------------------

_B = r"(?<![a-z0-9])"
_E = r"(?![a-z0-9])"

_EMI_RE = re.compile(_B + r"emis?" + _E)
_PREPAY_RE = re.compile(
    _B + r"(?:lump\s?sum|prepay(?:ment|ed)?|prepaid|part[\s-]?pay(?:ment)?|foreclos(?:e|ed|ure))" + _E
)
_FORECLOSE_RE = re.compile(_B + r"foreclos(?:e|ed|ure)" + _E)
_COUNT_RE = re.compile(_B + r"(\d{1,2})\s*emis" + _E)
_AMOUNT_RE = re.compile(_B + r"(?:rs\.?\s*|₹\s*)?(\d[\d,]*(?:\.\d{1,2})?)\s*([kl])?" + _E)
_BARE_EMI_RE = re.compile(r"(?:my\s+)?(?:loan\s+)?emi\s+(?:done|paid|deducted|cleared)")
_EMI_DONE_RE = re.compile(_B + r"emi\s+(?:done|paid|deducted|cleared)" + _E)


@dataclass
class LoanIntent:
    kind: str                 # "emi" | "prepayment" | "unknown_loan" | "missing_amount"
    loan: dict | None = None
    amount: float | None = None
    count: int = 1
    paid_on: date | None = None


def mentions_loan(text: str) -> bool:
    """Cheap check before loading loans: does the message talk about EMIs/prepaying?"""
    lowered = text.lower()
    return bool(_EMI_RE.search(lowered) or _PREPAY_RE.search(lowered))


def _parse_amount(number: str, suffix: str | None) -> float:
    value = float(number.replace(",", ""))
    return value * {"k": 1000, "l": 100000}.get(suffix or "", 1)


def find_loan(text: str, loans: list[dict]) -> tuple[dict | None, str]:
    """The loan whose name appears in `text` (longest name wins), and the text without it."""
    best, best_match = None, None
    for loan in loans:
        name = loan["name"].strip().lower()
        if not name:
            continue
        pattern = _B + r"\s+".join(re.escape(word) for word in name.split()) + _E
        match = re.search(pattern, text)
        if match and (best_match is None or len(name) > len(best["name"].strip())):
            best, best_match = loan, match
    if best is None:
        return None, text
    return best, (text[:best_match.start()] + " " + text[best_match.end():]).strip()


def parse_loan_message(text: str, loans: list[dict], today: date) -> LoanIntent | None:
    """Turn "home loan emi done" / "car loan lumpsum 50k" into a LoanIntent.

    Returns None when the message isn't about a loan, so it can be logged as
    an expense instead (e.g. "phone emi 2000").
    """
    lowered = re.sub(r"\s+", " ", text.lower()).strip()
    is_prepay = bool(_PREPAY_RE.search(lowered))
    if not is_prepay and not _EMI_RE.search(lowered):
        return None

    paid_on, rest = extract_date(lowered, today)
    loan, rest = find_loan(rest, loans)
    if loan is None:
        open_loans = [l for l in loans if not loan_status(l, [], today)["closed"]]
        if len(open_loans) == 1 and not is_prepay and _BARE_EMI_RE.fullmatch(rest):
            loan = open_loans[0]
        elif is_prepay or _EMI_DONE_RE.search(rest):
            return LoanIntent("unknown_loan")
        else:
            return None

    count = 1
    count_match = _COUNT_RE.search(rest)
    if count_match:
        count = min(max(1, int(count_match.group(1))), MAX_EMIS_AT_ONCE)
        rest = rest[:count_match.start()] + " " + rest[count_match.end():]

    amount_match = _AMOUNT_RE.search(rest)
    amount = _parse_amount(*amount_match.groups()) if amount_match else None
    if amount is not None and not 0 < amount <= MAX_AMOUNT:
        amount = None

    paid_on = paid_on or today
    if is_prepay:
        if amount is None:
            if not _FORECLOSE_RE.search(rest):
                return LoanIntent("missing_amount", loan)
            amount = float(loan["outstanding"])
        return LoanIntent("prepayment", loan, amount, 1, paid_on)
    return LoanIntent("emi", loan, amount or float(loan["emi_amount"]), count, paid_on)


# ---------------------------------------------------------------------------
# Telegram replies
# ---------------------------------------------------------------------------


def progress_bar(fraction: float, width: int = 12) -> str:
    filled = round(max(0.0, min(fraction, 1.0)) * width)
    return "█" * filled + "░" * (width - filled)


def _month_label(ym: str) -> str:
    return date.fromisoformat(ym + "-01").strftime("%b %Y")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def status_lines(loan: dict, status: dict, currency: str) -> list[str]:
    """Progress bar, balance and what's left — shared by every loan reply."""
    tenure = int(loan["tenure_months"])
    lines = [
        f"{progress_bar(status['emi_progress'])} {loan['emis_paid']}/{tenure} EMIs "
        f"({round(status['emi_progress'] * 100)}%)",
    ]
    if status["closed"]:
        lines.append("🎉 Loan closed!")
        return lines
    lines.append(f"Outstanding: {indian_format(float(loan['outstanding']), currency)}")
    left = [_plural(status["emis_left"], "EMI") + " left"]
    if status["left_to_pay"]:
        left.append(f"≈{indian_format(status['left_to_pay'], currency)} to pay")
    if status["ends"]:
        left.append(f"ends {_month_label(status['ends'])}")
    lines.append(" · ".join(left))
    at_balance = status["emis_left_at_balance"]
    if at_balance is not None and at_balance < status["emis_left"]:
        lines.append(
            f"💡 At the current balance it clears in ≈{_plural(at_balance, 'EMI')} "
            f"({status['emis_left'] - at_balance} fewer)"
        )
    return lines


def payment_reply(loan: dict, payments: list[dict], all_payments: list[dict],
                  currency: str, today: date) -> str:
    """Confirmation for EMIs/prepayment just recorded on `loan` (its updated row)."""
    status = loan_status(loan, all_payments, today)
    first = payments[0]
    name = loan["name"]
    if first["kind"] == "prepayment":
        lines = [
            f"💰 Prepayment · {name}",
            f"{indian_format(float(first['amount']), currency)} off the principal",
        ]
    else:
        total = sum(float(p["amount"]) for p in payments)
        count = f"{len(payments)} EMIs" if len(payments) > 1 else "EMI"
        lines = [f"✅ {count} paid · {name}", indian_format(total, currency)]
        if loan.get("interest_rate") is not None:
            principal = sum(float(p["principal"]) for p in payments)
            interest = sum(float(p["interest"]) for p in payments)
            lines[1] += (
                f" → {indian_format(principal, currency)} principal + "
                f"{indian_format(interest, currency)} interest"
            )
    if first["paid_on"] != today.isoformat():
        lines[0] += f" · 📅 {short_date(date.fromisoformat(first['paid_on']))}"
    return "\n".join(lines + [""] + status_lines(loan, status, currency))


def loans_overview(loans: list[dict], payments: list[dict], currency: str, today: date) -> str:
    """/loans — every loan with its progress, then totals."""
    if not loans:
        return "🏦 No loans yet. Add them on the Loans page of the dashboard."

    by_loan: dict = {}
    for p in payments:
        by_loan.setdefault(p["loan_id"], []).append(p)

    lines = ["🏦 Your loans"]
    total_outstanding = total_emi = 0.0
    for loan in loans:
        status = loan_status(loan, by_loan.get(loan["id"], []), today)
        title = loan["name"] + (f" — {loan['lender']}" if loan.get("lender") else "")
        lines.append("")
        lines.append(("✅ " if status["closed"] else "") + title)
        lines.extend(status_lines(loan, status, currency))
        if not status["closed"]:
            total_outstanding += float(loan["outstanding"])
            total_emi += float(loan["emi_amount"])
            if status["paid_this_month"]:
                lines.append("☑️ This month's EMI paid")
            elif status["next_due"]:
                due = date.fromisoformat(status["next_due"])
                days = status["due_in_days"]
                when = "today" if days == 0 else (f"{-days}d overdue" if days < 0 else f"in {days}d")
                lines.append(f"⏳ EMI {indian_format(float(loan['emi_amount']), currency)} due "
                             f"{short_date(due)} ({when})")

    lines.append("")
    lines.append(f"Total outstanding: {indian_format(total_outstanding, currency)}")
    lines.append(f"EMIs per month: {indian_format(total_emi, currency)}")
    lines.append(f"\nLog: \"{loans[0]['name']} emi done\" · \"{loans[0]['name']} lumpsum 50000\"")
    return "\n".join(lines)


def undo_markup(payment_ids: list[int]) -> dict:
    return {"inline_keyboard": [[{
        "text": "↩️ Undo",
        "callback_data": "lu:" + ",".join(str(i) for i in payment_ids),
    }]]}


# ---------------------------------------------------------------------------
# Gemini context (AI chat and Money Coach)
# ---------------------------------------------------------------------------


def ai_context_lines(db, currency: str, today: date) -> list[str]:
    """Loans and friend balances as plain lines for a Gemini prompt.

    Returns [] if there's nothing to say or the tables don't exist yet.
    """
    from lib.friends import balances, display_name

    try:
        all_loans = db.get_loans()
        payments = db.get_loan_payments()
        entries = db.get_friend_txns()
    except Exception as exc:
        print(f"Loans/friends unavailable for AI context: {exc}")
        return []

    lines = []
    if all_loans:
        lines.append("Loans (EMIs the user records):")
        for loan in all_loans:
            mine = [p for p in payments if p["loan_id"] == loan["id"]]
            status = loan_status(loan, mine, today)
            name = loan["name"] + (f" ({loan['lender']})" if loan.get("lender") else "")
            if status["closed"]:
                lines.append(f"- {name}: closed, fully repaid")
                continue
            parts = [
                f"{loan.get('loan_type') or 'other'} loan",
                f"outstanding {indian_format(float(loan['outstanding']), currency)} "
                f"of {indian_format(float(loan['principal']), currency)} borrowed",
                f"EMI {indian_format(float(loan['emi_amount']), currency)}/month",
                f"{loan['emis_paid']}/{loan['tenure_months']} EMIs paid, {status['emis_left']} left",
            ]
            if loan.get("interest_rate") is not None:
                parts.append(f"{float(loan['interest_rate']):g}% interest per year")
            if status["left_to_pay"]:
                parts.append(f"about {indian_format(status['left_to_pay'], currency)} still to pay")
            if status["ends"]:
                parts.append(f"ends {status['ends']}")
            parts.append("this month's EMI " + ("paid" if status["paid_this_month"] else "not yet paid"))
            if status["prepaid"]:
                parts.append(f"prepaid {indian_format(status['prepaid'], currency)} so far")
            if status["interest_paid"]:
                parts.append(f"interest paid so far {indian_format(status['interest_paid'], currency)}")
            lines.append(f"- {name}: " + "; ".join(parts))

    open_balances = {p: b for p, b in balances(entries).items() if b}
    if open_balances:
        if lines:
            lines.append("")
        lines.append("Money with friends:")
        for person, balance in sorted(open_balances.items(), key=lambda kv: -kv[1]):
            amount = indian_format(abs(balance), currency)
            if balance > 0:
                lines.append(f"- {display_name(person)} owes the user {amount}")
            else:
                lines.append(f"- The user owes {display_name(person)} {amount}")
    return lines
