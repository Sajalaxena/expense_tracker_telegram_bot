"""Turn a photo (receipt / UPI screenshot) or voice note into transactions via Gemini."""

import base64
import json
from datetime import date

from lib.dates import MAX_DAYS_BACK
from lib.gemini import call_gemini
from lib.parser import CATEGORY_KEYWORDS, Transaction

EXPENSE_CATEGORIES = sorted(CATEGORY_KEYWORDS) + ["other"]
MAX_AMOUNT = 99999999.99
MAX_ENTRIES = 20

_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "amount": {"type": "NUMBER"},
            "note": {"type": "STRING"},
            "category": {"type": "STRING", "enum": EXPENSE_CATEGORIES + ["income"]},
            "type": {"type": "STRING", "enum": ["expense", "income"]},
            "date": {"type": "STRING", "nullable": True},
        },
        "required": ["amount", "note", "category", "type"],
    },
}


def _prompt(kind: str, caption: str, today: date) -> str:
    source = {
        "photo": "This image is a receipt, bill, or payment screenshot (UPI/GPay/PhonePe/Paytm/bank).",
        "voice": "This audio is a voice note in English, Hindi or Hinglish describing money spent or received.",
    }[kind]
    lines = [
        "You extract expenses for an Indian expense-tracking app.",
        source,
        f"Today's date is {today.isoformat()}.",
        "Return one item per separate payment. For a receipt or payment screenshot, "
        "return ONE item with the final total paid (not each line item).",
        "amount: rupees as a number. Spoken numbers count: 'four fifty' = 450, "
        "'dedh hazaar' = 1500, '2k' = 2000.",
        "note: 1-4 lowercase words naming the merchant or item, e.g. 'swiggy', 'uber to office'.",
        f"category: one of {', '.join(EXPENSE_CATEGORIES)}; use 'income' with type 'income' "
        "for salary, refunds, cashback or money received.",
        "date: YYYY-MM-DD if the image/audio states a date (e.g. 'yesterday', a receipt date), else null.",
        "If there is no payment at all, return [].",
    ]
    if caption:
        lines.append(f"The user added this caption (treat as a hint): {caption}")
    return "\n".join(lines)


def _clean_date(value, today: date) -> date | None:
    try:
        d = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if d > today or (today - d).days > MAX_DAYS_BACK:
        return None
    return d


def _to_transactions(items, today: date) -> list[Transaction]:
    """Validate Gemini's items; drop anything malformed rather than trusting it."""
    if not isinstance(items, list):
        return []
    results = []
    for item in items[:MAX_ENTRIES]:
        if not isinstance(item, dict):
            continue
        try:
            amount = round(float(item.get("amount")), 2)
        except (TypeError, ValueError):
            continue
        if not 0.01 <= amount <= MAX_AMOUNT:
            continue

        is_income = item.get("type") == "income" or item.get("category") == "income"
        category = "income" if is_income else str(item.get("category", "")).lower()
        if not is_income and category not in EXPENSE_CATEGORIES:
            category = "other"
        note = " ".join(str(item.get("note") or "").split())[:60].lower() or category

        results.append(Transaction(
            amount=amount,
            category=category,
            note=note,
            type="income" if is_income else "expense",
            date=_clean_date(item.get("date"), today),
        ))
    return results


def extract_transactions(api_key: str, data: bytes, mime_type: str, kind: str,
                         caption: str, today: date) -> list[Transaction]:
    """Ask Gemini to read a photo or voice note and return validated transactions.

    Args:
        kind: "photo" or "voice".
        caption: Optional text the user sent with the media.

    Raises:
        urllib.error.HTTPError / ValueError from call_gemini on API failure.
    """
    contents = [{
        "role": "user",
        "parts": [
            {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}},
            {"text": _prompt(kind, caption, today)},
        ],
    }]
    text = call_gemini(
        api_key,
        contents,
        response_schema=_SCHEMA,
        max_output_tokens=1000,
        temperature=0.1,
        timeout=40,
    )
    return _to_transactions(json.loads(text), today)
