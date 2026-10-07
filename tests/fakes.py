"""In-memory stand-in for lib.db.SupabaseDB, covering the methods the bot,
cron job and APIs use. Behaviour mirrors the real queries (chat_id
ownership checks, local-date defaults) closely enough for handler tests."""

from datetime import date

from lib.dates import local_today


class FakeDB:
    def __init__(self, txns=None, subs=None, budgets=None):
        self.txns = {t["id"]: dict(t) for t in (txns or [])}
        self.subs = {s["id"]: dict(s) for s in (subs or [])}
        self.budgets = dict(budgets or {})
        self.client = object()  # rate limiter falls back to in-memory
        self._next_id = max(self.txns, default=0) + 1

    # --- transactions ---------------------------------------------------
    def add(self, txn, chat_id):
        txn_id = self._next_id
        self._next_id += 1
        self.txns[txn_id] = {
            "id": txn_id,
            "date": (getattr(txn, "date", None) or local_today()).isoformat(),
            "category": txn.category,
            "amount": float(txn.amount),
            "note": txn.note,
            "type": txn.type,
            "chat_id": chat_id,
        }
        return txn_id

    def _owned(self, txn_id, chat_id):
        row = self.txns.get(txn_id)
        if row is None or (chat_id is not None and row["chat_id"] != chat_id):
            return None
        return row

    def delete_transaction(self, txn_id, chat_id=None):
        return self.txns.pop(txn_id) if self._owned(txn_id, chat_id) else None

    def get_transactions(self, ids, chat_id):
        return [dict(self.txns[i]) for i in ids if self._owned(i, chat_id)]

    def update_transaction(self, txn_id, chat_id, fields):
        row = self._owned(txn_id, chat_id)
        if row is None:
            return None
        row.update(fields)
        return dict(row)

    def rows_since(self, chat_id, start: date):
        return [dict(r) for r in self.txns.values()
                if r["chat_id"] == chat_id and r["date"] >= start.isoformat()]

    def recent(self, chat_id, limit=10):
        rows = [r for r in self.txns.values() if r["chat_id"] == chat_id]
        return sorted(rows, key=lambda r: -r["id"])[:limit]

    def chat_ids(self):
        return {r["chat_id"] for r in self.txns.values()} | {s["chat_id"] for s in self.subs.values()}

    def _expenses(self, month):
        return [r for r in self.txns.values()
                if r["type"] == "expense" and r["date"].startswith(month + "-")]

    def month_total(self, month):
        return sum(r["amount"] for r in self._expenses(month) if r["category"] != "fav_p")

    def category_total(self, month, category):
        return sum(r["amount"] for r in self._expenses(month) if r["category"] == category)

    # --- subscriptions --------------------------------------------------
    def deactivate_subscription(self, sub_id, chat_id=None):
        sub = self.subs.get(sub_id)
        if not sub or not sub.get("active", True) or (chat_id is not None and sub["chat_id"] != chat_id):
            return False
        sub["active"] = False
        return True

    def subscriptions_for_billing(self):
        return [dict(s) for s in self.subs.values() if s.get("active", True)]

    def mark_subscription_billed(self, sub_id, billed_on):
        self.subs[sub_id]["last_billed"] = billed_on.isoformat()

    # --- budgets --------------------------------------------------------
    def get_budget_overrides(self):
        return dict(self.budgets)

    def set_budget(self, category, amount):
        self.budgets[category] = float(amount)
