"""Tests for lib/budget.py — parse_simple_amount."""

import pytest

from lib.budget import parse_simple_amount


class TestParseSimpleAmount:
    """Tests for parse_simple_amount()."""

    def test_plain_integer(self):
        assert parse_simple_amount("199") == 199.0

    def test_plain_float(self):
        assert parse_simple_amount("2.5") == 2.5

    def test_k_suffix_lowercase(self):
        assert parse_simple_amount("2.5k") == 2500.0

    def test_k_suffix_uppercase(self):
        assert parse_simple_amount("10K") == 10000.0

    def test_l_suffix_lowercase(self):
        assert parse_simple_amount("1.5l") == 150000.0

    def test_l_suffix_uppercase(self):
        assert parse_simple_amount("2L") == 200000.0

    def test_whitespace_stripped(self):
        assert parse_simple_amount("  5k  ") == 5000.0

    def test_integer_k(self):
        assert parse_simple_amount("10k") == 10000.0

    def test_integer_l(self):
        assert parse_simple_amount("1l") == 100000.0

    def test_invalid_text_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_simple_amount("abc")

    def test_empty_string_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_simple_amount("")

    def test_only_suffix_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_simple_amount("k")

    def test_negative_value(self):
        assert parse_simple_amount("-5k") == -5000.0

    def test_zero(self):
        assert parse_simple_amount("0") == 0.0
