"""Utility functions for the Tracksy expense tracker."""


def indian_format(amount: float, currency: str = "₹") -> str:
    """
    Format a number in the Indian numbering system.

    The Indian system groups the last 3 digits together, then groups of 2
    for the remaining digits.

    Examples:
        500       → "₹500"
        1000      → "₹1,000"
        100000    → "₹1,00,000"
        1234567   → "₹12,34,567"
        1234567.5 → "₹12,34,567.50"
        500.00    → "₹500"
        500.50    → "₹500.50"

    Args:
        amount: The numeric amount to format.
        currency: The currency symbol to prepend. Defaults to "₹".

    Returns:
        A string with the currency symbol and Indian-grouped number.
    """
    # Round to 2 decimal places
    amount = round(amount, 2)

    # Split into integer and decimal parts
    if amount == int(amount):
        int_part = int(amount)
        decimal_str = ""
    else:
        int_part = int(amount)
        # Format to 2 decimal places and keep as-is (don't strip trailing zero
        # from a single non-zero decimal digit, e.g. 500.50 stays "500.50")
        decimal_raw = f"{amount:.2f}".split(".")[1]
        # Only strip if entirely zeros (handled above), otherwise keep 2 places
        decimal_str = "." + decimal_raw

    # Format the integer part with Indian grouping
    int_str = str(int_part)

    if len(int_str) <= 3:
        formatted_int = int_str
    else:
        # Last 3 digits
        last_three = int_str[-3:]
        remaining = int_str[:-3]

        # Group remaining digits in pairs from right to left
        groups = []
        while len(remaining) > 2:
            groups.append(remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.append(remaining)

        groups.reverse()
        formatted_int = ",".join(groups) + "," + last_three

    return f"{currency}{formatted_int}{decimal_str}"
