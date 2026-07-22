"""Database module for storing transactions in Supabase PostgreSQL."""

from datetime import datetime
from typing import Optional, Protocol


class TransactionLike(Protocol):
    """Protocol for transaction objects — matches src.parser.Transaction fields."""

    amount: float
    category: str
    note: str
    type: str


class SupabaseDB:
    """Thin wrapper around the Supabase client providing transaction storage operations."""

    def __init__(self, url: str, key: str):
        """
        Initialize the Supabase client connection.

        Args:
            url: The Supabase project URL.
            key: The Supabase service role key.
        """
        from supabase import create_client

        self.client = create_client(url, key)

    def add(self, txn: TransactionLike, chat_id: int) -> int:
        """
        Insert a transaction into the txns table.

        Args:
            txn: A transaction object with amount, category, note, and type fields.
            chat_id: The Telegram chat ID associated with the transaction.

        Returns:
            The row id of the inserted transaction.
        """
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")

        row = {
            "date": current_date,
            "category": txn.category,
            "amount": float(txn.amount),
            "note": txn.note,
            "type": txn.type,
            "chat_id": chat_id,
        }

        result = self.client.table("txns").insert(row).execute()
        return result.data[0]["id"]

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
        # Find the most recent transaction for this chat_id
        result = (
            self.client.table("txns")
            .select("*")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            return None

        row = result.data[0]
        row_id = row["id"]

        # Delete the row
        self.client.table("txns").delete().eq("id", row_id).execute()

        return row

    def month_total(self, month: str) -> float:
        """
        Sum expense amounts for a given month.

        Args:
            month: A string in the format "YYYY-MM".

        Returns:
            The sum of amounts where type is 'expense' and the date starts with
            the given month string. Returns 0.0 if no matching expenses exist.
        """
        # Filter expenses whose date starts with "YYYY-MM-"
        date_prefix = month + "-"

        result = (
            self.client.table("txns")
            .select("amount")
            .eq("type", "expense")
            .like("date", f"{date_prefix}%")
            .execute()
        )

        if not result.data:
            return 0.0

        return sum(float(row["amount"]) for row in result.data)

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
        date_prefix = month + "-"

        result = (
            self.client.table("txns")
            .select("amount")
            .eq("type", "expense")
            .eq("category", category)
            .like("date", f"{date_prefix}%")
            .execute()
        )

        if not result.data:
            return 0.0

        return sum(float(row["amount"]) for row in result.data)

    def all_rows(self) -> list[dict]:
        """
        Return all transactions ordered by created_at descending.

        Returns:
            A list of dicts, each containing all fields of a transaction row.
        """
        result = (
            self.client.table("txns")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )

        return result.data if result.data else []

    def add_subscription(self, name: str, amount: float, cycle: str, chat_id: int) -> int:
        """
        Add a recurring subscription.

        Args:
            name: Subscription name (non-empty).
            amount: Positive subscription amount.
            cycle: Either "monthly" or "yearly".
            chat_id: The Telegram chat ID associated with the subscription.

        Returns:
            The row id of the inserted subscription.
        """
        row = {
            "name": name,
            "amount": float(amount),
            "cycle": cycle,
            "chat_id": chat_id,
            "active": True,
        }

        result = self.client.table("subscriptions").insert(row).execute()
        return result.data[0]["id"]

    def get_active_subscriptions(self, chat_id: int) -> list[dict]:
        """
        Get all active subscriptions for a chat_id.

        Args:
            chat_id: The Telegram chat ID to query subscriptions for.

        Returns:
            List of dicts with fields: id, name, amount, cycle, created_at.
            Ordered by name ascending.
        """
        result = (
            self.client.table("subscriptions")
            .select("id, name, amount, cycle, created_at")
            .eq("chat_id", chat_id)
            .eq("active", True)
            .order("name", desc=False)
            .execute()
        )

        return result.data if result.data else []

    def get_all_active_subscriptions(self) -> list[dict]:
        """
        Get all active subscriptions across all chat_ids.

        Used by the data API to include subscription data in the dashboard.

        Returns:
            List of dicts with fields: id, name, amount, cycle.
            Ordered by name ascending. Does NOT include chat_id.
        """
        result = (
            self.client.table("subscriptions")
            .select("id, name, amount, cycle")
            .eq("active", True)
            .order("name", desc=False)
            .execute()
        )

        return result.data if result.data else []

    def deactivate_subscription(self, sub_id: int, chat_id: int) -> bool:
        """
        Mark a subscription as inactive.

        Sets active=FALSE for the subscription matching the given id and chat_id.

        Args:
            sub_id: The subscription row id.
            chat_id: The Telegram chat ID (ensures ownership).

        Returns:
            True if a matching active subscription was found and deactivated,
            False otherwise.
        """
        result = (
            self.client.table("subscriptions")
            .update({"active": False})
            .eq("id", sub_id)
            .eq("chat_id", chat_id)
            .eq("active", True)
            .execute()
        )

        return len(result.data) > 0
