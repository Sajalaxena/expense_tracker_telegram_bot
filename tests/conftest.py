"""Shared pytest fixtures for the Tracksy test suite."""

import pytest

from src.db import Database
from src.parser import Transaction


@pytest.fixture
def sample_config():
    """Return a complete config dict with all fields populated."""
    return {
        "telegram_token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        "currency": "₹",
        "monthlyBudget": 50000,
        "budgets": {
            "food": 8000,
            "travel": 5000,
            "groceries": 6000,
            "clothes": 3000,
            "rent": 15000,
            "bills": 4000,
            "luxuries": 3000,
            "investments": 10000,
            "health": 3000,
            "education": 2000,
            "other": 2000,
        },
    }


@pytest.fixture
def in_memory_db():
    """Return a Database instance backed by an in-memory SQLite database."""
    return Database(":memory:")


@pytest.fixture
def sample_transaction():
    """Return a single Transaction with typical expense values."""
    return Transaction(
        amount=450.0,
        category="food",
        note="swiggy",
        type="expense",
    )


@pytest.fixture
def sample_transactions():
    """Return a list of varied Transaction objects covering different categories and types."""
    return [
        Transaction(amount=450.0, category="food", note="swiggy", type="expense"),
        Transaction(amount=1500.0, category="travel", note="uber ride", type="expense"),
        Transaction(amount=250.0, category="groceries", note="blinkit order", type="expense"),
        Transaction(amount=2000.0, category="bills", note="wifi bill", type="expense"),
        Transaction(amount=50000.0, category="income", note="salary", type="income"),
        Transaction(amount=800.0, category="health", note="pharmacy", type="expense"),
        Transaction(amount=5000.0, category="luxuries", note="amazon", type="expense"),
        Transaction(amount=10000.0, category="investments", note="sip", type="expense"),
    ]


@pytest.fixture
def populated_db(in_memory_db, sample_transactions):
    """Return an in-memory database pre-populated with sample transactions."""
    for txn in sample_transactions:
        in_memory_db.add(txn, chat_id=12345)
    return in_memory_db
