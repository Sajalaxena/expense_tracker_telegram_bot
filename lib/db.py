"""Database module for storing transactions in Supabase PostgreSQL."""

from datetime import date
from typing import Optional, Protocol

from lib.dates import local_today

# Row in the `budgets` table that holds the overall monthly budget.
MONTHLY_BUDGET_KEY = "_monthly"


class TransactionLike(Protocol):
    """Protocol for transaction objects — matches src.parser.Transaction fields."""

    amount: float
    category: str
    note: str
    type: str
    date: Optional[date]


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
            txn: A transaction object with amount, category, note, type and
                date fields (date None or missing = today, local time).
            chat_id: The Telegram chat ID associated with the transaction.

        Returns:
            The row id of the inserted transaction.
        """
        txn_date = getattr(txn, "date", None) or local_today()

        row = {
            "date": txn_date.isoformat(),
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

    def delete_transaction(self, txn_id: int, chat_id: Optional[int] = None) -> Optional[dict]:
        """
        Delete a single transaction by id.

        Args:
            txn_id: The transaction row id.
            chat_id: If given, only delete when the row belongs to this chat
                (used by the Telegram bot). The web dashboard passes None.

        Returns:
            A dict with all fields of the deleted row, or None if no matching
            transaction exists.
        """
        query = self.client.table("txns").delete().eq("id", txn_id)
        if chat_id is not None:
            query = query.eq("chat_id", chat_id)
        result = query.execute()

        return result.data[0] if result.data else None

    def get_transactions(self, ids: list[int], chat_id: int) -> list[dict]:
        """
        Fetch the given transactions that belong to chat_id (missing ids are skipped).
        """
        if not ids:
            return []
        result = (
            self.client.table("txns")
            .select("*")
            .in_("id", ids)
            .eq("chat_id", chat_id)
            .execute()
        )
        return result.data if result.data else []

    def update_transaction(self, txn_id: int, chat_id: int, fields: dict) -> Optional[dict]:
        """
        Update fields (e.g. category, date) of a transaction owned by chat_id.

        Returns:
            The updated row, or None if no matching transaction exists.
        """
        result = (
            self.client.table("txns")
            .update(fields)
            .eq("id", txn_id)
            .eq("chat_id", chat_id)
            .execute()
        )
        return result.data[0] if result.data else None

    def rows_since(self, chat_id: int, start: date) -> list[dict]:
        """
        Return a chat's transactions dated on or after `start`.
        """
        result = (
            self.client.table("txns")
            .select("*")
            .eq("chat_id", chat_id)
            .gte("date", start.isoformat())
            .execute()
        )
        return result.data if result.data else []

    def chat_ids(self) -> set[int]:
        """
        Return every chat_id that has logged a transaction or subscription.
        """
        ids = set()
        for table in ("txns", "subscriptions"):
            result = self.client.table(table).select("chat_id").execute()
            ids.update(row["chat_id"] for row in (result.data or []) if row.get("chat_id"))
        return ids

    def recent(self, chat_id: int, limit: int = 10) -> list[dict]:
        """
        Return the most recent transactions for a chat_id, newest first.

        Args:
            chat_id: The Telegram chat ID.
            limit: Maximum number of rows to return.

        Returns:
            A list of dicts, each containing all fields of a transaction row.
        """
        result = (
            self.client.table("txns")
            .select("*")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        return result.data if result.data else []

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
            .neq("category", "fav_p")
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

    def deactivate_subscription(self, sub_id: int, chat_id: Optional[int] = None) -> bool:
        """
        Mark a subscription as inactive.

        Sets active=FALSE for the subscription matching the given id (and
        chat_id, when given).

        Args:
            sub_id: The subscription row id.
            chat_id: The Telegram chat ID (ensures ownership). The web
                dashboard passes None.

        Returns:
            True if a matching active subscription was found and deactivated,
            False otherwise.
        """
        query = (
            self.client.table("subscriptions")
            .update({"active": False})
            .eq("id", sub_id)
            .eq("active", True)
        )
        if chat_id is not None:
            query = query.eq("chat_id", chat_id)
        result = query.execute()

        return len(result.data) > 0

    def subscriptions_for_billing(self) -> list[dict]:
        """
        Get all active subscriptions with the fields needed to auto-log renewals.

        Requires the `last_billed` column (see supabase/migrations.sql).

        Returns:
            List of dicts with id, name, amount, cycle, chat_id, created_at,
            last_billed.
        """
        result = (
            self.client.table("subscriptions")
            .select("id, name, amount, cycle, chat_id, created_at, last_billed")
            .eq("active", True)
            .execute()
        )
        return result.data if result.data else []

    def mark_subscription_billed(self, sub_id: int, billed_on: date) -> None:
        """
        Record the date a subscription renewal was last auto-logged.
        """
        (
            self.client.table("subscriptions")
            .update({"last_billed": billed_on.isoformat()})
            .eq("id", sub_id)
            .execute()
        )

    def get_budget_overrides(self) -> dict[str, float]:
        """
        Read budgets set from Telegram or the dashboard (the `budgets` table).

        Returns:
            {category: amount}. MONTHLY_BUDGET_KEY holds the overall monthly
            budget; an amount of 0 means "no cap" for that category.
        """
        result = self.client.table("budgets").select("category, amount").execute()
        return {row["category"]: float(row["amount"]) for row in (result.data or [])}

    def set_budget(self, category: str, amount: float) -> None:
        """
        Create or replace a budget cap. Use amount 0 to remove a category's cap.
        """
        (
            self.client.table("budgets")
            .upsert({"category": category, "amount": float(amount)}, on_conflict="category")
            .execute()
        )
