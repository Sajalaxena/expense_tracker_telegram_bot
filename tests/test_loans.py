"""Tests for loans: EMI maths, form validation, status, Telegram parsing,
and the bot flows (EMI done, prepayment, undo, /loans)."""

from datetime import date, timedelta

import pytest

import api.webhook as webhook
from lib import loans
from lib.config import AppConfig
from lib.dates import local_today
from tests.fakes import FakeDB

CHAT = 42
TODAY = date(2026, 10, 25)


def make_config(**kw):
    defaults = dict(telegram_token="t", supabase_url="u", supabase_key="k", webhook_secret="w",
                    monthly_budget=50000)
    defaults.update(kw)
    return AppConfig(**defaults)


def home_loan(**kw):
    loan = {"id": 1, "name": "Home Loan", "lender": "HDFC", "loan_type": "home",
            "principal": 2500000.0, "interest_rate": 8.5, "emi_amount": 25000.0,
            "tenure_months": 240, "emis_paid": 30, "outstanding": 1800000.0,
            "start_date": None, "emi_day": 5, "notes": "", "chat_id": None}
    loan.update(kw)
    return loan


def flat_loan(**kw):
    """A loan with no interest rate: outstanding is the total still to pay."""
    loan = {"id": 2, "name": "loan1", "lender": "", "loan_type": "personal",
            "principal": 100000.0, "interest_rate": None, "emi_amount": 5000.0,
            "tenure_months": 24, "emis_paid": 16, "outstanding": 40000.0,
            "start_date": None, "emi_day": None, "notes": "", "chat_id": None}
    loan.update(kw)
    return loan


@pytest.fixture
def tg(monkeypatch):
    calls = []
    for name in ("edit_message", "answer_callback"):
        monkeypatch.setattr(webhook, name, lambda *a, _n=name, **k: calls.append((_n, a, k)))
    return calls


# ---------------------------------------------------------------------------
# Maths
# ---------------------------------------------------------------------------


def test_compute_emi_matches_standard_formula():
    assert loans.compute_emi(800000, 9.5, 60) == pytest.approx(16801.49, abs=0.01)
    assert loans.compute_emi(120000, 0, 12) == 10000


def test_balance_reaches_zero_after_full_tenure():
    emi = loans.compute_emi(500000, 10, 36)
    assert loans.balance_after(500000, 10, emi, 36) == pytest.approx(0, abs=1)
    assert loans.balance_after(500000, 10, emi, 0) == 500000


def test_emis_to_clear():
    emi = loans.compute_emi(500000, 10, 36)
    assert loans.emis_to_clear(500000, 10, emi) == 36
    assert loans.emis_to_clear(40000, None, 5000) == 8
    assert loans.emis_to_clear(0, 10, emi) == 0
    assert loans.emis_to_clear(1_000_000, 12, 5000) is None  # EMI below the monthly interest


def test_split_emi_pays_interest_first():
    principal, interest = loans.split_emi(home_loan(), 25000)
    assert interest == pytest.approx(1800000 * 8.5 / 1200, abs=0.01)
    assert principal == pytest.approx(25000 - interest, abs=0.01)
    assert loans.split_emi(flat_loan(), 5000) == (5000, 0)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_computes_emi_and_outstanding_from_rate():
    fields, error = loans.validate_loan({"name": " Car ", "principal": 800000, "tenure_months": 60,
                                         "interest_rate": 9.5, "emis_paid": 12})
    assert error is None
    assert fields["name"] == "Car"
    assert fields["emi_amount"] == pytest.approx(16801.49, abs=0.01)
    assert fields["outstanding"] == pytest.approx(
        loans.balance_after(800000, 9.5, fields["emi_amount"], 12), abs=0.01)
    assert fields["loan_type"] == "other"


def test_validate_without_rate_uses_emis_left_times_emi():
    fields, error = loans.validate_loan({"name": "loan1", "principal": 100000, "tenure_months": 24,
                                         "emi_amount": 5000, "emis_paid": 16})
    assert error is None
    assert fields["outstanding"] == 40000
    assert fields["interest_rate"] is None


def test_validate_keeps_given_outstanding():
    fields, _ = loans.validate_loan({"name": "x", "principal": 100000, "tenure_months": 24,
                                     "emi_amount": 5000, "outstanding": "12345.5", "emi_day": 7,
                                     "start_date": "2025-01-05", "loan_type": "car"})
    assert fields["outstanding"] == 12345.5
    assert fields["emi_day"] == 7
    assert fields["start_date"] == "2025-01-05"


@pytest.mark.parametrize("payload, message", [
    ({}, "Loan name is required."),
    ({"name": "x"}, "Loan amount is required."),
    ({"name": "x", "principal": 1000}, "Total EMIs (tenure in months) is required."),
    ({"name": "x", "principal": 1000, "tenure_months": 12}, "Enter the EMI amount, or the interest rate"),
    ({"name": "x", "principal": 1000, "tenure_months": 12, "emi_amount": 100, "emis_paid": 13},
     "EMIs paid can't be more"),
    ({"name": "x", "principal": 1000, "tenure_months": 1.5, "emi_amount": 100}, "whole number"),
    ({"name": "x", "principal": True, "tenure_months": 12, "emi_amount": 100}, "must be a number"),
    ({"name": "x", "principal": 1000, "tenure_months": 12, "emi_amount": 100, "interest_rate": 99},
     "between 0 and 60"),
    ({"name": "x", "principal": 1000, "tenure_months": 12, "emi_amount": 100, "start_date": "soon"},
     "Start date"),
    ({"name": "x", "principal": 1000, "tenure_months": 12, "emi_amount": 100, "loan_type": "boat"},
     "Loan type"),
    ({"name": "x" * 51, "principal": 1000, "tenure_months": 12, "emi_amount": 100}, "50 characters"),
])
def test_validate_rejects(payload, message):
    fields, error = loans.validate_loan(payload)
    assert fields is None and message in error


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_of_unpaid_month():
    status = loans.loan_status(home_loan(), [], TODAY)
    assert status["emis_left"] == 210
    assert status["emi_progress"] == pytest.approx(30 / 240, abs=1e-4)
    assert status["repaid_progress"] == pytest.approx(0.28, abs=1e-4)
    assert not status["paid_this_month"]
    assert status["next_due"] == "2026-10-05" and status["due_in_days"] == -20
    assert not status["closed"]


def test_status_after_paying_this_month():
    payments = [{"kind": "emi", "amount": 25000, "interest": 12750, "paid_on": "2026-10-05"}]
    status = loans.loan_status(home_loan(), payments, TODAY)
    assert status["paid_this_month"]
    assert status["next_due"] == "2026-11-05"
    assert status["interest_paid"] == 12750


def test_status_shows_prepayment_shortening_the_loan():
    status = loans.loan_status(home_loan(outstanding=1000000), [], TODAY)
    assert status["emis_left_at_balance"] < status["emis_left"]
    assert status["left_to_pay"] == status["emis_left_at_balance"] * 25000


def test_status_without_rate():
    status = loans.loan_status(flat_loan(), [], TODAY)
    assert status["left_to_pay"] == 40000
    assert status["emis_left_at_balance"] is None
    assert status["ends"] == "2027-05"  # 8 EMIs: Oct (unpaid) .. May
    assert status["next_due"] is None


def test_status_closed():
    assert loans.loan_status(flat_loan(emis_paid=24, outstanding=0), [], TODAY)["closed"]
    assert loans.loan_status(home_loan(outstanding=0.5), [], TODAY)["closed"]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

LOANS = [home_loan(), flat_loan()]


@pytest.mark.parametrize("text, kind, name, amount, count", [
    ("home loan emi done", "emi", "Home Loan", 25000, 1),
    ("Home Loan EMI paid 26,500", "emi", "Home Loan", 26500, 1),
    ("emi done home loan", "emi", "Home Loan", 25000, 1),
    ("loan1 emi done", "emi", "loan1", 5000, 1),
    ("loan1 2 emis done", "emi", "loan1", 5000, 2),
    ("home loan lumpsum 1l", "prepayment", "Home Loan", 100000, 1),
    ("home loan prepaid rs 50k", "prepayment", "Home Loan", 50000, 1),
    ("loan1 part payment 10000", "prepayment", "loan1", 10000, 1),
    ("loan1 foreclosed", "prepayment", "loan1", 40000, 1),
])
def test_parse_loan_message(text, kind, name, amount, count):
    intent = loans.parse_loan_message(text, LOANS, TODAY)
    assert (intent.kind, intent.loan["name"], intent.amount, intent.count) == (kind, name, amount, count)
    assert intent.paid_on == TODAY


def test_parse_date_phrase():
    intent = loans.parse_loan_message("loan1 emi done yesterday", LOANS, TODAY)
    assert intent.paid_on == TODAY - timedelta(days=1)


def test_longest_loan_name_wins():
    both = [home_loan(), home_loan(id=3, name="Home Loan 2")]
    assert loans.parse_loan_message("home loan 2 emi done", both, TODAY).loan["id"] == 3


def test_parse_non_loan_messages():
    assert loans.parse_loan_message("swiggy 450", LOANS, TODAY) is None
    assert loans.parse_loan_message("phone emi 2000", LOANS, TODAY) is None


def test_parse_unknown_loan_and_missing_amount():
    assert loans.parse_loan_message("car loan emi done", LOANS, TODAY).kind == "unknown_loan"
    assert loans.parse_loan_message("emi done", LOANS, TODAY).kind == "unknown_loan"
    assert loans.parse_loan_message("loan1 prepay", LOANS, TODAY).kind == "missing_amount"


def test_bare_emi_done_with_a_single_loan():
    assert loans.parse_loan_message("emi done", [flat_loan()], TODAY).loan["name"] == "loan1"


# ---------------------------------------------------------------------------
# Bot flows
# ---------------------------------------------------------------------------


def test_emi_done_records_payment_and_replies_with_progress():
    db = FakeDB(loans=[home_loan()])
    reply = webhook._handle_message("home loan emi done", CHAT, db, make_config())

    assert db.loans[1]["emis_paid"] == 31
    payment = next(iter(db.payments.values()))
    assert payment["kind"] == "emi" and payment["chat_id"] == CHAT
    assert payment["principal"] + payment["interest"] == pytest.approx(25000)
    assert db.loans[1]["outstanding"] == pytest.approx(1800000 - payment["principal"])
    assert reply.text.startswith("✅ EMI paid · Home Loan")
    assert "31/240 EMIs" in reply.text and "interest" in reply.text
    assert reply.markup["inline_keyboard"][0][0]["callback_data"] == f"lu:{payment['id']}"
    assert not db.txns  # not logged as an expense


def test_second_emi_in_a_month_warns():
    db = FakeDB(loans=[flat_loan()])
    webhook._handle_message("loan1 emi done", CHAT, db, make_config())
    reply = webhook._handle_message("loan1 emi done", CHAT, db, make_config())
    assert "already logged this month" in reply.text
    assert db.loans[2]["emis_paid"] == 18


def test_several_emis_stop_when_the_loan_closes():
    db = FakeDB(loans=[flat_loan(emis_paid=23, outstanding=5000)])
    reply = webhook._handle_message("loan1 3 emis done", CHAT, db, make_config())
    assert len(db.payments) == 1
    assert "🎉 Loan closed!" in reply.text


def test_closed_loan_refuses_more_emis():
    db = FakeDB(loans=[flat_loan(emis_paid=24, outstanding=0)])
    reply = webhook._handle_message("loan1 emi done", CHAT, db, make_config())
    assert "already closed" in reply
    assert not db.payments


def test_prepayment_reduces_principal_only():
    db = FakeDB(loans=[home_loan()])
    reply = webhook._handle_message("home loan lumpsum 2l", CHAT, db, make_config())
    assert db.loans[1]["outstanding"] == 1600000
    assert db.loans[1]["emis_paid"] == 30
    assert reply.text.startswith("💰 Prepayment · Home Loan")
    assert "fewer" in reply.text


def test_undo_button_reverses_the_payment(tg):
    db = FakeDB(loans=[home_loan()])
    reply = webhook._handle_message("home loan emi done", CHAT, db, make_config())
    data = reply.markup["inline_keyboard"][0][0]["callback_data"]
    callback = {"id": "cb", "data": data,
                "message": {"chat": {"id": CHAT}, "message_id": 9, "text": reply.text}}

    webhook._handle_callback(callback, db, make_config())
    assert db.loans[1]["emis_paid"] == 30
    assert db.loans[1]["outstanding"] == 1800000
    assert not db.payments
    edit = next(c for c in tg if c[0] == "edit_message")
    assert edit[1][2].endswith("↩️ Undone")
    assert ("answer_callback", ("cb", "t", "Undone"), {}) in tg

    tg.clear()
    webhook._handle_callback(callback, db, make_config())
    assert ("answer_callback", ("cb", "t", "Already undone"), {}) in tg


def test_undo_several_emis(tg):
    db = FakeDB(loans=[flat_loan()])
    reply = webhook._handle_message("loan1 2 emis done", CHAT, db, make_config())
    callback = {"id": "cb", "data": reply.markup["inline_keyboard"][0][0]["callback_data"],
                "message": {"chat": {"id": CHAT}, "message_id": 9, "text": reply.text}}
    webhook._handle_callback(callback, db, make_config())
    assert db.loans[2]["emis_paid"] == 16 and db.loans[2]["outstanding"] == 40000


def test_unknown_loan_lists_loans():
    reply = webhook._handle_message("car loan emi done", CHAT, FakeDB(loans=LOANS), make_config())
    assert "Which loan?" in reply and "Home Loan, loan1" in reply


def test_expense_with_emi_word_is_still_an_expense():
    db = FakeDB(loans=LOANS)
    reply = webhook._handle_message("phone emi 2000", CHAT, db, make_config())
    assert reply.text.startswith("✅ ₹2,000")
    assert not db.payments


def test_missing_loans_table_falls_back_to_expense():
    class NoLoansDB(FakeDB):
        def get_loans(self):
            raise RuntimeError('relation "loans" does not exist')

    reply = webhook._handle_message("phone emi 2000", CHAT, NoLoansDB(), make_config())
    assert reply.text.startswith("✅ ₹2,000")


def test_owner_lock():
    config = make_config(owner_chat_ids=frozenset({7}))
    db = FakeDB(loans=[home_loan()])
    webhook._handle_message("home loan emi done", CHAT, db, config)
    assert not db.payments
    assert "only shown in the owner's chat" in webhook._handle_command("/loans", CHAT, db, config)
    webhook._handle_message("home loan emi done", 7, db, config)
    assert len(db.payments) == 1


def test_loans_command_overview():
    db = FakeDB(loans=LOANS)
    text = webhook._handle_command("/loans", CHAT, db, make_config())
    assert text.startswith("🏦 Your loans")
    assert "Home Loan — HDFC" in text and "loan1" in text
    assert "Total outstanding: ₹18,40,000" in text
    assert "EMIs per month: ₹30,000" in text
    assert "EMI ₹25,000 due 5 Oct" in text or "This month's EMI paid" in text


def test_loans_command_with_no_loans():
    assert "No loans yet" in webhook._handle_command("/loans", CHAT, FakeDB(), make_config())


def test_ai_context_lines():
    db = FakeDB(loans=LOANS, friends=[
        {"id": 50, "person": "rahul", "kind": "lent", "amount": 500.0, "note": "", "date": "2026-10-01"},
    ])
    lines = loans.ai_context_lines(db, "₹", local_today())
    text = "\n".join(lines)
    assert "Home Loan (HDFC): home loan; outstanding ₹18,00,000" in text
    assert "8.5% interest per year" in text
    assert "Rahul owes the user ₹500" in text
