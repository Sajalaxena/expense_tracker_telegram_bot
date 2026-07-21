"""Database module for storing transactions in a local SQLite database."""

import sqlite3
from datetime import datetime, date
from typing import Optional

from src.parser import Transaction


class Database:
    """Thin wrapper around sqlite3 providing transaction storage operations."""

    def __init__(self, db_path: str = "tracksy.db"):
        """
        Open or create the SQLite database and ensure the schema exists.

        Args:
            db_path: Path to the SQLite database file. Created if it doesn't exist.
        """
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        """Create tables if they do not already exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS txns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                note TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('expense', 'income')),
                chat_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                cycle TEXT NOT NULL CHECK(cycle IN ('monthly', 'yearly')),
                chat_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def add(self, txn: Transaction, chat_id: int) -> int:
        """
        Insert a transaction into the database.

        Uses the current date (YYYY-MM-DD) and current timestamp (YYYY-MM-DDTHH:MM:SS).

        Args:
            txn: A Transaction dataclass with amount, category, note, and type.
            chat_id: The Telegram chat ID associated with the transaction.

        Returns:
            The row id of the inserted transaction.
        """
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        created_at = now.strftime("%Y-%m-%dT%H:%M:%S")

        cursor = self._conn.execute(
            """
            INSERT INTO txns (date, category, amount, note, type, chat_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (current_date, txn.category, txn.amount, txn.note, txn.type, chat_id, created_at),
        )
        self._conn.commit()
        return cursor.lastrowid

    def undo_last(self, chat_id: int) -> Optional[dict]:
        """
        Delete the most recent transaction for a given chat_id.

        Finds the row with the highest created_at for the chat_id, deletes it,
        and returns the deleted entry as a dictionary.

        Args:
            chat_id: The Telegram chat ID to undo the last transaction for.

        Returns:
            A dict with all fields of the deleted row, or None if no transactions
            exist for the given chat_id.
        """
        row = self._conn.execute(
            """
            SELECT id, date, category, amount, note, type, chat_id, created_at
            FROM txns
            WHERE chat_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()

        if row is None:
            return None

        entry = dict(row)
        self._conn.execute("DELETE FROM txns WHERE id = ?", (entry["id"],))
        self._conn.commit()
        return entry

    def all_rows(self) -> list[dict]:
        """
        Return all transactions ordered by created_at descending.

        Returns:
            A list of dicts, each containing all fields of a transaction row.
        """
        rows = self._conn.execute(
            """
            SELECT id, date, category, amount, note, type, chat_id, created_at
            FROM txns
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def month_total(self, month: str) -> float:
        """
        Sum expense amounts for a given month.

        Args:
            month: A string in the format "YYYY-MM".

        Returns:
            The sum of amounts where type is 'expense' and the date starts with
            the given month string. Returns 0.0 if no matching expenses exist.
        """
        result = self._conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0.0) as total
            FROM txns
            WHERE type = 'expense' AND date LIKE ? || '%'
            """,
            (month + "-",),
        ).fetchone()
        return result["total"]

    def add_subscription(self, name: str, amount: float, cycle: str, chat_id: int) -> int:
        """
        Add a recurring subscription.

        Inserts a new row into the subscriptions table with active=1 and the
        current timestamp in ISO 8601 format.

        Args:
            name: Subscription name (non-empty).
            amount: Positive subscription amount.
            cycle: Either "monthly" or "yearly".
            chat_id: The Telegram chat ID associated with the subscription.

        Returns:
            The row id of the inserted subscription.
        """
        created_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        cursor = self._conn.execute(
            """
            INSERT INTO subscriptions (name, amount, cycle, chat_id, active, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (name, amount, cycle, chat_id, created_at),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_active_subscriptions(self, chat_id: int) -> list[dict]:
        """
        Get all active subscriptions for a chat_id.

        Args:
            chat_id: The Telegram chat ID to query subscriptions for.

        Returns:
            List of dicts with fields: id, name, amount, cycle, created_at.
            Ordered by name ascending.
        """
        rows = self._conn.execute(
            """
            SELECT id, name, amount, cycle, created_at
            FROM subscriptions
            WHERE chat_id = ? AND active = 1
            ORDER BY name ASC
            """,
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_all_active_subscriptions(self) -> list[dict]:
        """
        Get all active subscriptions across all chat_ids.

        Used by the exporter to include subscription data in data.js.

        Returns:
            List of dicts with fields: id, name, amount, cycle.
            Ordered by name ascending. Does NOT include chat_id.
        """
        rows = self._conn.execute(
            """
            SELECT id, name, amount, cycle
            FROM subscriptions
            WHERE active = 1
            ORDER BY name ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def deactivate_subscription(self, sub_id: int, chat_id: int) -> bool:
        """
        Mark a subscription as inactive.

        Sets active=0 for the subscription matching the given id and chat_id.

        Args:
            sub_id: The subscription row id.
            chat_id: The Telegram chat ID (ensures ownership).

        Returns:
            True if a matching active subscription was found and deactivated,
            False otherwise.
        """
        cursor = self._conn.execute(
            """
            UPDATE subscriptions
            SET active = 0
            WHERE id = ? AND chat_id = ? AND active = 1
            """,
            (sub_id, chat_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def category_total(self, month: str, category: str) -> float:
        """
        Sum expense amounts for a given month and category.

        Args:
            month: A string in the format "YYYY-MM".
            category: The category name to filter by.

        Returns:
            The sum of amounts for expenses matching the month and category.
            Returns 0.0 if no matching rows exist.
        """
        result = self._conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0.0) as total
            FROM txns
            WHERE type = 'expense' AND date LIKE ? || '%' AND category = ?
            """,
            (month + "-", category),
        ).fetchone()
        return result["total"]
