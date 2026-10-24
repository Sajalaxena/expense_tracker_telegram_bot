"""In-memory stand-in for lib.db.SupabaseDB, covering the methods the bot,
cron job and APIs use. Behaviour mirrors the real queries (chat_id
ownership checks, local-date defaults) closely enough for handler tests."""

from datetime import date

from lib.dates import local_today


class FakeDB:
    def __init__(self, txns=None, subs=None, budgets=None, loans=None, payments=None, friends=None):
        self.txns = {t["id"]: dict(t) for t in (txns or [])}
        self.subs = {s["id"]: dict(s) for s in (subs or [])}
        self.budgets = dict(budgets or {})
        self.loans = {l["id"]: dict(l) for l in (loans or [])}
        self.payments = {p["id"]: dict(p) for p in (payments or [])}
        self.friends = {f["id"]: dict(f) for f in (friends or [])}
        self.client = object()  # rate limiter falls back to in-memory
        self._next_id = max(self.txns, default=0) + 1
        self._next_other_id = max([0, *self.subs, *self.loans, *self.payments, *self.friends]) + 1

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

    def _new_id(self):
        """Ids for subscriptions, loans, payments and friend entries."""
        new_id = self._next_other_id
        self._next_other_id += 1
        return new_id

    # --- subscriptions --------------------------------------------------
    def add_subscription(self, name, amount, cycle, chat_id):
        sub_id = self._new_id()
        self.subs[sub_id] = {"id": sub_id, "name": name, "amount": float(amount), "cycle": cycle,
                             "chat_id": chat_id, "active": True}
        return sub_id

    def update_subscription(self, sub_id, fields):
        sub = self.subs.get(sub_id)
        if not sub or not sub.get("active", True):
            return None
        sub.update(fields)
        return dict(sub)

    def latest_chat_id(self):
        rows = list(self.txns.values()) or list(self.subs.values())
        return max(rows, key=lambda r: r["id"])["chat_id"] if rows else None

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

    # --- loans ----------------------------------------------------------
    def get_loans(self):
        return [dict(l) for _, l in sorted(self.loans.items())]

    def get_loan(self, loan_id):
        loan = self.loans.get(loan_id)
        return dict(loan) if loan else None

    def add_loan(self, fields):
        loan_id = self._new_id()
        self.loans[loan_id] = {"id": loan_id, "chat_id": None, **fields}
        return dict(self.loans[loan_id])

    def update_loan(self, loan_id, fields):
        loan = self.loans.get(loan_id)
        if loan is None:
            return None
        loan.update(fields)
        return dict(loan)

    def delete_loan(self, loan_id):
        if self.loans.pop(loan_id, None) is None:
            return False
        self.payments = {k: p for k, p in self.payments.items() if p["loan_id"] != loan_id}
        return True

    def get_loan_payments(self):
        rows = sorted(self.payments.values(), key=lambda p: (p["paid_on"], p["id"]), reverse=True)
        return [dict(p) for p in rows]

    def get_loan_payment(self, payment_id):
        payment = self.payments.get(payment_id)
        return dict(payment) if payment else None

    def add_loan_payment(self, fields):
        payment_id = self._new_id()
        self.payments[payment_id] = {"id": payment_id, **fields}
        return dict(self.payments[payment_id])

    def delete_loan_payment(self, payment_id):
        return self.payments.pop(payment_id, None)

    # --- friends ledger -------------------------------------------------
    def get_friend_txns(self):
        rows = sorted(self.friends.values(), key=lambda f: (f["date"], f["id"]), reverse=True)
        return [dict(f) for f in rows]

    def add_friend_txn(self, fields):
        entry_id = self._new_id()
        self.friends[entry_id] = {"id": entry_id, "chat_id": None, **fields}
        return dict(self.friends[entry_id])

    def update_friend_txn(self, entry_id, fields):
        entry = self.friends.get(entry_id)
        if entry is None:
            return None
        entry.update(fields)
        return dict(entry)

    def delete_friend_txn(self, entry_id):
        return self.friends.pop(entry_id, None)

    def rename_friend(self, person, new_person):
        changed = [f for f in self.friends.values() if f["person"] == person]
        for f in changed:
            f["person"] = new_person
        return len(changed)

    def delete_friend(self, person):
        doomed = [k for k, f in self.friends.items() if f["person"] == person]
        for k in doomed:
            del self.friends[k]
        return len(doomed)
