"""Unit tests for Indian number formatting utility."""

import pytest
from src.utils import indian_format


class TestIndianFormat:
    """Tests for indian_format function."""

    def test_simple_number(self):
        assert indian_format(500) == "₹500"

    def test_thousands(self):
        assert indian_format(1000) == "₹1,000"

    def test_ten_thousands(self):
        assert indian_format(10000) == "₹10,000"

    def test_lakh(self):
        assert indian_format(100000) == "₹1,00,000"

    def test_ten_lakhs(self):
        assert indian_format(1000000) == "₹10,00,000"

    def test_crore(self):
        assert indian_format(10000000) == "₹1,00,00,000"

    def test_mixed_large_number(self):
        assert indian_format(1234567) == "₹12,34,567"

    def test_decimal_with_trailing_zeros_stripped(self):
        assert indian_format(500.00) == "₹500"

    def test_decimal_kept_when_meaningful(self):
        assert indian_format(500.50) == "₹500.50"

    def test_large_number_with_decimal(self):
        assert indian_format(1234567.50) == "₹12,34,567.50"

    def test_single_digit(self):
        assert indian_format(5) == "₹5"

    def test_two_digits(self):
        assert indian_format(99) == "₹99"

    def test_three_digits(self):
        assert indian_format(999) == "₹999"

    def test_custom_currency(self):
        assert indian_format(1000, currency="$") == "$1,000"

    def test_empty_currency(self):
        assert indian_format(1000, currency="") == "1,000"

    def test_small_decimal(self):
        assert indian_format(0.50) == "₹0.50"

    def test_decimal_single_digit_after_point(self):
        assert indian_format(123.10) == "₹123.10"

    def test_max_range_value(self):
        assert indian_format(99999999.99) == "₹9,99,99,999.99"

    def test_min_value(self):
        assert indian_format(0.01) == "₹0.01"
