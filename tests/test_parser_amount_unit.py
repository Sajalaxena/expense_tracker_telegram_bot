"""Unit tests for parser amount extraction logic."""

import pytest
from src.parser import parse, ParseError, Transaction


class TestPlainNumbers:
    """Test extraction of plain numbers without prefixes/suffixes."""

    def test_integer(self):
        result = parse("swiggy 450")
        assert result.amount == 450.0

    def test_decimal(self):
        result = parse("chai 49.50")
        assert result.amount == 49.50

    def test_single_digit(self):
        result = parse("tip 5")
        assert result.amount == 5.0

    def test_large_number(self):
        result = parse("rent 25000")
        assert result.amount == 25000.0

    def test_min_amount(self):
        result = parse("tiny 0.01")
        assert result.amount == 0.01

    def test_max_amount(self):
        result = parse("big 99999999.99")
        assert result.amount == 99999999.99


class TestCurrencyPrefix:
    """Test extraction with currency prefixes."""

    def test_rs_prefix(self):
        result = parse("rs 500 for lunch")
        assert result.amount == 500.0

    def test_Rs_prefix(self):
        result = parse("Rs 1000")
        assert result.amount == 1000.0

    def test_RS_prefix(self):
        result = parse("RS 250")
        assert result.amount == 250.0

    def test_rupee_symbol_prefix(self):
        result = parse("₹500")
        assert result.amount == 500.0

    def test_rupee_symbol_with_space(self):
        result = parse("₹ 750")
        assert result.amount == 750.0

    def test_rs_no_space(self):
        result = parse("rs500 uber")
        assert result.amount == 500.0


class TestCurrencySuffix:
    """Test extraction with currency suffixes."""

    def test_rs_suffix(self):
        result = parse("500rs for food")
        assert result.amount == 500.0

    def test_Rs_suffix(self):
        result = parse("1000Rs taxi")
        assert result.amount == 1000.0

    def test_RS_suffix(self):
        result = parse("250RS snack")
        assert result.amount == 250.0


class TestMultipliers:
    """Test k/K and l/L multiplier suffixes."""

    def test_k_lowercase(self):
        result = parse("rent 1.5k")
        assert result.amount == 1500.0

    def test_k_uppercase(self):
        result = parse("rent 2K")
        assert result.amount == 2000.0

    def test_l_lowercase(self):
        result = parse("car 2l")
        assert result.amount == 200000.0

    def test_l_uppercase(self):
        result = parse("flat 1.5L")
        assert result.amount == 150000.0

    def test_k_with_integer(self):
        result = parse("5k shopping")
        assert result.amount == 5000.0

    def test_l_with_decimal(self):
        result = parse("0.5l investment")
        assert result.amount == 50000.0


class TestCommaNumbers:
    """Test comma-separated numbers (Indian and international grouping)."""

    def test_international_grouping(self):
        result = parse("paid 1,250")
        assert result.amount == 1250.0

    def test_indian_grouping_lakh(self):
        result = parse("1,25,000 rent")
        assert result.amount == 125000.0

    def test_indian_grouping_thousand(self):
        result = parse("paid 12,500")
        assert result.amount == 12500.0

    def test_indian_grouping_crore(self):
        result = parse("property 1,00,00,000")
        assert result.amount == 10000000.0

    def test_comma_with_decimal(self):
        result = parse("1,250.50 groceries")
        assert result.amount == 1250.50


class TestMultipleNumbers:
    """Test that first recognizable amount expression wins."""

    def test_first_number_wins(self):
        result = parse("500 for 2 coffees")
        assert result.amount == 500.0

    def test_first_prefixed_wins(self):
        result = parse("₹300 for group of 4")
        assert result.amount == 300.0


class TestParseError:
    """Test error cases."""

    def test_no_number(self):
        with pytest.raises(ParseError, match="Could not find an amount"):
            parse("hello world")

    def test_empty_message(self):
        with pytest.raises(ParseError, match="Could not find an amount"):
            parse("")

    def test_whitespace_only(self):
        with pytest.raises(ParseError, match="Could not find an amount"):
            parse("   ")

    def test_amount_exceeds_max(self):
        with pytest.raises(ParseError, match="Amount must be between"):
            parse("100000000")  # 10 crore, exceeds max

    def test_amount_too_large_with_multiplier(self):
        with pytest.raises(ParseError, match="Amount must be between"):
            parse("100000k")  # 100,000 × 1000 = 100,000,000 > max


class TestTransactionFields:
    """Test that placeholder fields are set correctly."""

    def test_category_is_matched(self):
        result = parse("500 lunch")
        assert result.category == "food"

    def test_type_is_expense(self):
        result = parse("500 lunch")
        assert result.type == "expense"

    def test_note_is_cleaned_message(self):
        result = parse("swiggy 450")
        assert result.note == "swiggy"

    def test_note_strips_prefix_amount(self):
        result = parse("₹500 uber ride")
        assert result.note == "uber ride"

    def test_note_defaults_to_other_when_empty(self):
        result = parse("500")
        assert result.note == "other"
