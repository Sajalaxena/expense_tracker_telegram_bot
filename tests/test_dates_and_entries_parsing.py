"""Tests for lib/dates.py and multi-entry parsing (lib.parser.parse_entries)."""

from datetime import date, datetime, timezone

import pytest

from lib import dates
from lib.dates import extract_date, local_today
from lib.parser import ParseError, parse_entries

TODAY = date(2026, 10, 7)  # a Wednesday


@pytest.mark.parametrize("text, expected_date, expected_rest", [
    ("dinner 800 yesterday", date(2026, 10, 6), "dinner 800"),
    ("yday chai 30", date(2026, 10, 6), "chai 30"),
    ("cab 300 day before yesterday", date(2026, 10, 5), "cab 300"),
    ("cab 300 2 days ago", date(2026, 10, 5), "cab 300"),
    ("petrol 500 today", TODAY, "petrol 500"),
    ("chai 30 last monday", date(2026, 10, 5), "chai 30"),
    ("chai 30 on wed", TODAY, "chai 30"),           # "on <today's weekday>" = today
    ("chai 30 last wednesday", date(2026, 9, 30), "chai 30"),
    ("rent 15k on 1/10", date(2026, 10, 1), "rent 15k"),
    ("gift 500 on 28-12", date(2025, 12, 28), "gift 500"),  # future day/month -> last year
    ("gift 500 on 28/12/2025", date(2025, 12, 28), "gift 500"),
    ("yesterday: swiggy 450", date(2026, 10, 6), "swiggy 450"),
])
def test_extract_date(text, expected_date, expected_rest):
    assert extract_date(text, TODAY) == (expected_date, expected_rest)


@pytest.mark.parametrize("text", [
    "swiggy 450",
    "monday 200",            # bare weekday is a note, not a date
    "laptop on 31/02",       # invalid date
    "ticket on 1/1/2030",    # future
    "watch on 1/1/2020",     # more than a year back
])
def test_extract_date_leaves_text_alone(text):
    assert extract_date(text, TODAY) == (None, text)


def test_local_today_uses_configured_timezone(monkeypatch):
    # 20:00 UTC on 6 Oct is already 7 Oct (01:30) in India.
    fixed = datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz)

    monkeypatch.setattr(dates, "datetime", FakeDatetime)
    monkeypatch.setenv("TIMEZONE", "Asia/Kolkata")
    assert local_today() == date(2026, 10, 7)
    monkeypatch.setenv("TIMEZONE", "UTC")
    assert local_today() == date(2026, 10, 6)
    monkeypatch.setenv("TIMEZONE", "Not/A_Zone")
    assert local_today() == date(2026, 10, 7)  # falls back to the default


def _summary(txns):
    return [(t.amount, t.category, t.note, t.date) for t in txns]


def test_single_entry_has_no_date():
    assert _summary(parse_entries("swiggy 450", TODAY)) == [(450.0, "food", "swiggy", None)]


def test_comma_separated_entries():
    assert _summary(parse_entries("swiggy 450, uber 200, chai 30", TODAY)) == [
        (450.0, "food", "swiggy", None),
        (200.0, "travel", "uber", None),
        (30.0, "food", "chai", None),
    ]


def test_newline_and_semicolon_separators():
    txns = parse_entries("salary 80k\nrent 15k; milk 60", TODAY)
    assert [(t.amount, t.type) for t in txns] == [(80000.0, "income"), (15000.0, "expense"), (60.0, "expense")]


def test_indian_comma_number_is_not_split():
    assert _summary(parse_entries("rs 1,25,000 rent", TODAY)) == [(125000.0, "rent", "rent", None)]


def test_falls_back_to_one_entry_when_a_piece_has_no_amount():
    assert _summary(parse_entries("bread, butter 50", TODAY)) == [(50.0, "other", "bread, butter", None)]


def test_date_carries_forward_to_later_pieces():
    txns = parse_entries("yesterday swiggy 450, uber 200, chai 30 today", TODAY)
    assert [t.date for t in txns] == [date(2026, 10, 6), date(2026, 10, 6), TODAY]


def test_date_does_not_carry_backwards():
    txns = parse_entries("swiggy 450, uber 200 yesterday", TODAY)
    assert [t.date for t in txns] == [None, date(2026, 10, 6)]


def test_no_amount_raises():
    with pytest.raises(ParseError):
        parse_entries("hello there", TODAY)
