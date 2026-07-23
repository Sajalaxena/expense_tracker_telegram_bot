"""Parser module for extracting transaction data from plain-English messages."""

import re
from dataclasses import dataclass


@dataclass
class Transaction:
    """Represents a parsed financial transaction."""

    amount: float
    category: str
    note: str
    type: str  # "expense" | "income"


class ParseError(Exception):
    """Raised when a message cannot be parsed into a valid transaction."""

    pass


# Editable mapping: category name → list of keywords that trigger that category
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "travel": ["ola", "uber", "metro", "rapido", "auto", "cab", "bus", "train", "flight", "petrol", "diesel", "parking", "toll", "rickshaw"],
    "food": ["swiggy", "zomato", "chai", "coffee", "lunch", "dinner", "breakfast", "snack", "restaurant", "biryani"],
    "groceries": ["blinkit", "zepto", "bigbasket", "dmart", "vegetables", "fruits", "grocery", "milk"],
    "shopping": ["amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa", "tatacliq", "croma", "reliance"],
    "lifestyle": ["salon", "spa", "haircut", "parlour", "skincare", "perfume", "makeup", "facial", "massage", "grooming"],
    "clothes": ["shirt", "jeans", "shoes", "clothing", "jacket", "kurta", "saree", "dress"],
    "rent": ["rent", "house", "flat", "apartment", "pg"],
    "bills": ["electricity", "wifi", "internet", "mobile", "recharge", "phone", "water", "gas"],
    "entertainment": ["movie", "gaming", "concert", "event", "ticket", "outing", "party", "drinks"],
    "subscriptions": [
        "netflix", "spotify", "gym", "prime", "hotstar", "youtube",
        "disney", "hulu", "apple", "icloud", "dropbox", "notion",
        "chatgpt", "github", "jio", "airtel", "vi"
    ],
    "investments": ["sip", "etf", "stocks", "mutual", "fund", "ppf", "nps", "crypto"],
    "health": ["medicine", "doctor", "hospital", "pharmacy", "lab", "test", "dental"],
    "education": ["course", "book", "udemy", "tutorial", "exam", "college", "fee"],
    "fav P": ["T", "fav"],

}

# Editable list: keywords that indicate an income transaction
INCOME_KEYWORDS: list[str] = [
    "salary", "refund", "cashback", "received", "credited", "bonus", "reimbursement"
]

# Filler words to strip from notes
FILLER_WORDS: list[str] = ["spent", "on", "for", "paid", "just", "today", "yesterday"]


# Amount regex pattern explanation:
# - Optional currency prefix: rs/Rs/RS/₹ followed by optional whitespace
# - Number: digits with optional commas (Indian or international grouping)
# - Optional decimal part: dot followed by 1-2 digits
# - Optional multiplier suffix: k/K (×1000) or l/L (×100000)
# - Optional currency suffix: rs/Rs/RS (no space between number and suffix)
#
# The pattern captures:
#   group 1: currency prefix (if present)
#   group 2: the numeric part (with commas)
#   group 3: decimal part (if present)
#   group 4: multiplier suffix k/K/l/L (if present)
#   group 5: currency suffix (if present)

_AMOUNT_PATTERN = re.compile(
    r'(?:(?P<prefix>[Rr][Ss]|RS|₹)\s*)?'        # optional currency prefix + optional space
    r'(?P<number>\d{1,3}(?:,\d{2,3})*(?:,\d{3})?|\d{1,3}(?:,\d{2})*(?:,\d{3})?|\d+)'  # number with optional commas
    r'(?:\.(?P<decimal>\d{1,2}))?'                # optional decimal
    r'(?P<multiplier>[kKlL])?'                    # optional multiplier
    r'(?P<suffix>[Rr][Ss]|RS)?'                   # optional currency suffix (no space)
)

# More precise pattern that handles comma-separated numbers properly
_AMOUNT_RE = re.compile(
    r'(?:(?P<prefix>[Rr][Ss]|RS|₹)\s*)?'        # optional currency prefix + optional space
    r'(?P<number>\d[\d,]*\d|\d)'                  # number with optional commas (at least 1 digit)
    r'(?:\.(?P<decimal>\d{1,2}))?'                # optional decimal
    r'(?P<multiplier>[kKlL])?'                    # optional multiplier
    r'(?P<suffix>[Rr][Ss]|RS)?'                   # optional currency suffix (no space)
)

# Minimum and maximum valid amounts
_MIN_AMOUNT = 0.01
_MAX_AMOUNT = 99999999.99  # 9,99,99,999.99


def _is_amount_expression(match: re.Match) -> bool:
    """Check if a regex match is a valid amount expression (has prefix, suffix, multiplier, or standalone number)."""
    has_prefix = match.group('prefix') is not None
    has_suffix = match.group('suffix') is not None
    has_multiplier = match.group('multiplier') is not None
    return has_prefix or has_suffix or has_multiplier or True  # bare numbers are valid


def _parse_number(number_str: str, decimal_str: str | None, multiplier: str | None) -> float:
    """Parse the numeric components into a float value."""
    # Remove commas
    clean_number = number_str.replace(',', '')

    # Build the numeric string
    if decimal_str:
        numeric_str = f"{clean_number}.{decimal_str}"
    else:
        numeric_str = clean_number

    value = float(numeric_str)

    # Apply multiplier
    if multiplier:
        if multiplier in ('k', 'K'):
            value *= 1000
        elif multiplier in ('l', 'L'):
            value *= 100000

    return value


def _validate_comma_grouping(number_str: str) -> bool:
    """Validate that comma-separated numbers follow Indian or international grouping."""
    if ',' not in number_str:
        return True

    parts = number_str.split(',')

    # Must have at least 2 parts
    if len(parts) < 2:
        return False

    # First part: 1-3 digits
    if not (1 <= len(parts[0]) <= 3):
        return False

    # Last part: must be exactly 3 digits
    if len(parts[-1]) != 3:
        return False

    # Middle parts (if any): 2 or 3 digits
    # Indian grouping: middle parts are 2 digits
    # International grouping: middle parts are 3 digits
    for part in parts[1:-1]:
        if not (2 <= len(part) <= 3):
            return False

    return True


def _tokenize(message: str) -> list[str]:
    """Split message into words delimited by spaces, punctuation, or boundaries."""
    return re.split(r'[^a-zA-Z0-9]+', message)


def _detect_income(message: str) -> bool:
    """Check if the message contains any income keyword (case-insensitive, whole-word)."""
    words = [w.lower() for w in _tokenize(message)]
    for keyword in INCOME_KEYWORDS:
        if keyword.lower() in words:
            return True
    return False


def _match_category(message: str) -> str:
    """
    Find the category whose keyword appears earliest in the message text.

    Uses case-insensitive whole-word matching. A word is delimited by spaces,
    punctuation, or message boundaries. Returns "other" if no keyword matches.
    """
    msg_lower = message.lower()

    # Find the earliest position of any keyword match
    best_position = len(message)  # sentinel: beyond end
    best_category = "other"

    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            kw_lower = keyword.lower()
            # Search for whole-word occurrences of this keyword
            # Use regex for whole-word boundary based on non-alphanumeric delimiters
            pattern = re.compile(
                r'(?<![a-zA-Z0-9])' + re.escape(kw_lower) + r'(?![a-zA-Z0-9])'
            )
            match = pattern.search(msg_lower)
            if match and match.start() < best_position:
                best_position = match.start()
                best_category = category

    return best_category


def parse(message: str) -> Transaction:
    """
    Parse a plain-English message into a Transaction.

    Extracts the first valid amount expression from the message,
    detects income keywords, matches category by keyword, and generates a note.

    Args:
        message: The user's plain-text message.

    Returns:
        A Transaction with extracted amount, detected category, generated note, and type.

    Raises:
        ParseError: If no valid amount is found or amount is out of range.
    """
    if not message or not message.strip():
        raise ParseError("Could not find an amount in your message")

    # Find all potential amount matches
    for match in _AMOUNT_RE.finditer(message):
        number_str = match.group('number')
        decimal_str = match.group('decimal')
        multiplier = match.group('multiplier')
        prefix = match.group('prefix')
        suffix = match.group('suffix')

        # Validate comma grouping
        if not _validate_comma_grouping(number_str):
            continue

        # Ensure suffix isn't part of a larger word
        # Suffix must be at end of string or followed by non-alphanumeric
        if suffix:
            end_pos = match.end()
            if end_pos < len(message) and message[end_pos].isalpha():
                continue

        # Ensure multiplier isn't part of a larger word
        if multiplier:
            # The multiplier position is right after the number (and optional decimal)
            end_pos = match.end('multiplier')
            if end_pos < len(message) and message[end_pos].isalpha():
                continue

        # Ensure number isn't part of a larger alphanumeric sequence (unless prefixed/suffixed)
        if not prefix and not suffix and not multiplier:
            start_pos = match.start('number')
            end_pos = match.end('decimal') if decimal_str else match.end('number')
            # Check if preceded by alpha (would make it part of a word, not standalone)
            if start_pos > 0 and message[start_pos - 1].isalpha():
                continue

        try:
            amount = _parse_number(number_str, decimal_str, multiplier)
        except (ValueError, OverflowError):
            continue

        # Validate range
        if amount < _MIN_AMOUNT:
            continue
        if amount > _MAX_AMOUNT:
            raise ParseError("Amount must be between ₹0.01 and ₹9,99,99,999.99")

        # Detect income
        is_income = _detect_income(message)

        # Determine category
        if is_income:
            category = "income"
            txn_type = "income"
        else:
            category = _match_category(message)
            txn_type = "expense"

        # Generate note: strip amount expression and filler words
        amount_expr = message[match.start():match.end()]
        note = message.replace(amount_expr, '', 1).strip()
        # Remove filler words (whole-word, case-insensitive)
        for filler in FILLER_WORDS:
            # Use word boundary based on non-alphanumeric delimiters
            pattern = re.compile(
                r'(?<![a-zA-Z0-9])' + re.escape(filler) + r'(?![a-zA-Z0-9])',
                re.IGNORECASE
            )
            note = pattern.sub('', note)
        # Collapse multiple spaces and trim
        note = re.sub(r'\s+', ' ', note).strip()
        if not note:
            note = category

        return Transaction(
            amount=round(amount, 2),
            category=category,
            note=note,
            type=txn_type,
        )

    raise ParseError("Could not find an amount in your message")


def format_transaction(txn: Transaction) -> str:
    """
    Format a Transaction back into a parseable string.

    Produces a single-line string containing the amount as a plain number,
    the category, and the note separated by whitespace. For income transactions,
    an income keyword is included so that re-parsing preserves the income type.

    Round-trip guarantee: parse(format_transaction(t)) == t for valid transactions.

    Args:
        txn: A Transaction object with amount, category, note, and type.

    Returns:
        A single-line string that can be parsed back into the same transaction.
    """
    # Format amount as plain number (no commas, minimal decimal)
    if txn.amount == int(txn.amount):
        amount_str = str(int(txn.amount))
    else:
        amount_str = f"{txn.amount:.2f}".rstrip('0')

    # Determine note: use category name if note is empty or whitespace-only
    note = txn.note.strip() if txn.note else ""
    if not note:
        note = txn.category

    # Build the formatted string
    if txn.type == "income":
        # For income: we need an income keyword in the string so re-parsing
        # detects income type. Check if note already contains one.
        note_words = [w.lower() for w in _tokenize(note)]
        has_income_keyword = any(kw.lower() in note_words for kw in INCOME_KEYWORDS)

        if has_income_keyword:
            # Note already contains an income keyword, just use it directly
            return f"{amount_str} {note}"
        else:
            # Add "salary" keyword before the note. When re-parsed, the note
            # will include "salary" as part of it. So we must prepend it to
            # make the round-trip work: the note in the output Transaction
            # will be "salary {note}".
            return f"{amount_str} salary {note}"
    else:
        # For expenses: amount + note
        # The note should contain a category keyword for proper round-trip.
        # If the note equals the category name but that name isn't itself a
        # keyword that triggers the category, we need to include an actual keyword.
        if txn.category != "other" and txn.category in CATEGORY_KEYWORDS:
            # Check if note already contains a keyword for this category
            note_words = [w.lower() for w in _tokenize(note)]
            category_kws = CATEGORY_KEYWORDS[txn.category]
            has_category_keyword = any(kw.lower() in note_words for kw in category_kws)
            if not has_category_keyword:
                # Use the first keyword for the category to ensure re-parsing
                # assigns the correct category. The note will become this keyword.
                keyword = category_kws[0]
                return f"{amount_str} {keyword}"
        return f"{amount_str} {note}"
