"""Loans and friends-ledger API — Vercel serverless function.

GET returns everything the Loans and Friends pages show:
    {"currency", "loanTypes",
     "loans": [loan row + "status" (see lib.loans.loan_status) + "payments"],
     "friends": [{"person", "balance", "entries": [...]}]}

POST {"kind", "action", "id"?, "data"?} changes one thing and returns the
same payload as GET, so the page simply re-renders:
    loan     create | update | delete      data = loan form (lib.loans.validate_loan)
    payment  delete                        undoes an EMI/prepayment
    friend   create | update | delete      data = {person, kind, amount, note?, date?}
    person   settle | rename | delete      data = {person, name?}

EMIs and prepayments are recorded from Telegram (see lib/loans.py).
"""

import json
import traceback
from http.server import BaseHTTPRequestHandler

from lib import friends, loans
from lib.auth import is_authenticated
from lib.config import load_config_from_env
from lib.dates import local_today
from lib.db import SupabaseDB

ACTIONS = {
    "loan": ("create", "update", "delete"),
    "payment": ("delete",),
    "friend": ("create", "update", "delete"),
    "person": ("settle", "rename", "delete"),
}


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "chat_id"}


def build_state(db, config, today) -> dict:
    payments_by_loan: dict = {}
    for payment in db.get_loan_payments():
        payments_by_loan.setdefault(payment["loan_id"], []).append(_public(payment))

    loan_rows = []
    for loan in db.get_loans():
        payments = payments_by_loan.get(loan["id"], [])
        loan_rows.append({
            **_public(loan),
            "status": loans.loan_status(loan, payments, today),
            "payments": payments,
        })

    entries = db.get_friend_txns()
    by_person: dict = {}
    for entry in entries:
        by_person.setdefault(entry["person"], []).append(_public(entry))
    people = [
        {"person": person, "balance": balance, "entries": by_person[person]}
        for person, balance in friends.balances(entries).items()
    ]
    # Open balances first (largest first), settled people last.
    people.sort(key=lambda p: (p["balance"] == 0, -abs(p["balance"]), p["person"]))

    return {
        "currency": config.currency,
        "loanTypes": list(loans.LOAN_TYPES),
        "loans": loan_rows,
        "friends": people,
    }


def _require_id(payload) -> int:
    item_id = payload.get("id")
    if isinstance(item_id, bool) or not isinstance(item_id, int):
        raise RequestError(400, "Expected an integer id.")
    return item_id


def _check(fields_error):
    fields, error = fields_error
    if error:
        raise RequestError(400, error)
    return fields


def _unique_loan_name(db, name: str, loan_id: int | None = None) -> None:
    """Telegram finds loans by name, so two loans can't share one."""
    for loan in db.get_loans():
        if loan["id"] != loan_id and loan["name"].strip().lower() == name.lower():
            raise RequestError(400, f"You already have a loan called \"{loan['name']}\".")


def apply_change(db, payload, today) -> None:
    kind, action = payload.get("kind"), payload.get("action")
    if action not in ACTIONS.get(kind, ()):
        raise RequestError(400, "Unknown kind/action.")
    data = payload.get("data") or {}

    if kind == "loan":
        if action == "create":
            fields = _check(loans.validate_loan(data))
            _unique_loan_name(db, fields["name"])
            db.add_loan(fields)
            return
        loan_id = _require_id(payload)
        if action == "delete":
            if not db.delete_loan(loan_id):
                raise RequestError(404, f"No loan found with id {loan_id}.")
            return
        fields = _check(loans.validate_loan(data))
        _unique_loan_name(db, fields["name"], loan_id)
        if db.update_loan(loan_id, fields) is None:
            raise RequestError(404, f"No loan found with id {loan_id}.")

    elif kind == "payment":
        payment_id = _require_id(payload)
        payment = db.get_loan_payment(payment_id)
        if payment is None:
            raise RequestError(404, f"No payment found with id {payment_id}.")
        loans.reverse_payment(db, payment)

    elif kind == "friend":
        if action == "create":
            db.add_friend_txn(_check(friends.validate_entry(data, today)))
            return
        entry_id = _require_id(payload)
        if action == "delete":
            found = db.delete_friend_txn(entry_id)
        else:
            found = db.update_friend_txn(entry_id, _check(friends.validate_entry(data, today)))
        if found is None:
            raise RequestError(404, f"No entry found with id {entry_id}.")

    else:  # person
        person = str(data.get("person") or "").strip().lower()
        balance = friends.balances(db.get_friend_txns()).get(person)
        if balance is None:
            raise RequestError(404, f"No entries for {person or 'that person'}.")
        if action == "settle":
            settle = friends.settlement(balance)
            if settle is None:
                raise RequestError(400, "Already settled.")
            db.add_friend_txn({"person": person, "kind": settle[0], "amount": settle[1],
                               "note": "settled up", "date": today.isoformat()})
        elif action == "rename":
            new_name = " ".join(str(data.get("name") or "").split()).lower()
            if not new_name or len(new_name) > friends.MAX_PERSON:
                raise RequestError(400, f"Name must be 1–{friends.MAX_PERSON} characters.")
            db.rename_friend(person, new_name)
        else:
            db.delete_friend(person)


class handler(BaseHTTPRequestHandler):
    """Vercel serverless function entry point for the loans/friends API."""

    def do_GET(self):
        self._handle(None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = None
        self._handle(payload if isinstance(payload, dict) else "invalid")

    def _handle(self, payload):
        try:
            config = load_config_from_env()

            if not is_authenticated(self.headers, config.session_secret):
                self._send_response(401, {"error": "Unauthorized"})
                return
            if payload == "invalid":
                self._send_response(400, {"error": "Invalid JSON body."})
                return

            db = SupabaseDB(config.supabase_url, config.supabase_key)
            today = local_today()
            try:
                if payload is not None:
                    apply_change(db, payload, today)
                state = build_state(db, config, today)
            except RequestError:
                raise
            except Exception as exc:
                print(f"Debts API DB error: {traceback.format_exc()}")
                if "loans" in str(exc) or "friend_txns" in str(exc) or "loan_payments" in str(exc):
                    raise RequestError(503, "Run supabase/migrations.sql in the Supabase SQL "
                                            "editor to create the loans and friends tables.")
                raise

            self._send_response(200, state)

        except RequestError as exc:
            self._send_response(exc.status, {"error": str(exc)})
        except Exception:
            print(f"Debts API error: {traceback.format_exc()}")
            self._send_response(500, {"error": "Something went wrong. Please try again."})

    def _send_response(self, status_code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(body)
