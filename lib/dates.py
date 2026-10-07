"""Local-time helpers and natural-language date extraction.

Vercel runs in UTC, so plain datetime.now() puts anything logged between
00:00 and 05:30 IST on the previous day. Everything date-related goes through
here instead, using the TIMEZONE env var (default Asia/Kolkata).
"""

import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Kolkata"

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

# Oldest date a phrase may resolve to — guards against typos like "on 31/12/1999".
MAX_DAYS_BACK = 366

_B = r"(?<![a-zA-Z0-9])"  # word start (same delimiter rule as lib.parser)
_E = r"(?![a-zA-Z0-9])"   # word end

_PATTERNS = [
    ("dby", re.compile(_B + r"(?:day before yesterday|dby)" + _E, re.I)),
    ("yesterday", re.compile(_B + r"(?:yesterday|yday|ystd)" + _E, re.I)),
    ("today", re.compile(_B + r"today" + _E, re.I)),
    ("ago", re.compile(_B + r"(\d{1,3}) days? ago" + _E, re.I)),
    ("weekday", re.compile(_B + r"(last|on) (" + "|".join(WEEKDAYS) + r")" + _E, re.I)),
    ("dmy", re.compile(_B + r"on (\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?" + _E, re.I)),
]


def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.environ.get("TIMEZONE") or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_now() -> datetime:
    return datetime.now(local_tz())


def local_today() -> date:
    return local_now().date()


def _resolve(kind: str, match: re.Match, today: date) -> date | None:
    if kind == "dby":
        return today - timedelta(days=2)
    if kind == "yesterday":
        return today - timedelta(days=1)
    if kind == "today":
        return today
    if kind == "ago":
        return today - timedelta(days=int(match.group(1)))
    if kind == "weekday":
        back = (today.weekday() - WEEKDAYS[match.group(2).lower()]) % 7
        if back == 0 and match.group(1).lower() == "last":
            back = 7
        return today - timedelta(days=back)
    if kind == "dmy":
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        try:
            if year:
                y = int(year)
                return date(y + 2000 if y < 100 else y, month, day)
            candidate = date(today.year, month, day)
            # "on 28/12" typed in January means last December.
            return candidate if candidate <= today else date(today.year - 1, month, day)
        except ValueError:
            return None
    return None


def extract_date(text: str, today: date) -> tuple[date | None, str]:
    """Find a date phrase in `text`, returning (date, text without the phrase).

    Recognises "today", "yesterday"/"yday", "day before yesterday"/"dby",
    "N days ago", "last monday"/"on fri", and "on DD/MM[/YYYY]". Future dates
    and dates more than a year back are ignored. Returns (None, text) when
    nothing matches.
    """
    for kind, pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        resolved = _resolve(kind, match, today)
        if resolved is None or resolved > today or (today - resolved).days > MAX_DAYS_BACK:
            continue
        remaining = text[:match.start()] + " " + text[match.end():]
        return resolved, re.sub(r"\s+", " ", remaining).strip(" :,-")
    return None, text


def short_date(d: date) -> str:
    """5 Oct"""
    return f"{d.day} {d.strftime('%b')}"
