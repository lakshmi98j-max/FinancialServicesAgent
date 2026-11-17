"""Every tool, every code-enforced rule."""
import inspect
import json

import pytest

from agent import tools
from agent.session import Session
from agent.tools import run_tool
from tests.conftest import verify

PRIYA = dict(locator="priya.raman@example.com", date_of_birth="1984-03-12", property_zip="80302")


# ------------------------------------------------------------------ verify_identity (R1, R2)

def test_verify_with_loan_last4_and_flexible_formats(conn, session):
    r = tools.verify_identity(session, conn, "****1827", date_of_birth="March 12, 1984", ssn_last4="xxx-xx-3816")
    assert r["ok"] and r["status"] == "verified" and r["customer_first_name"] == "Priya"
    assert r["loans"] == [{"loan_id": 101, "loan_number_masked": "••••1827", "loan_type": "mortgage",
                           "property_address": "1418 Alder Crest Lane, Ashgrove, CO 80302"}]


def test_verify_by_full_loan_number_and_zip_plus_four(conn, session):
    r = tools.verify_identity(session, conn, "7100022915", date_of_birth="08/23/1979", property_zip="97214-3301")
    assert r["ok"] and session.verified_customer_id == 2


def test_one_factor_is_not_enough_and_does_not_burn_an_attempt(conn, session):
    r = tools.verify_identity(session, conn, "priya.raman@example.com", date_of_birth="1984-03-12")
    assert r["error"] == "INSUFFICIENT_FACTORS" and session.verification_attempts == 0


def test_unrecognised_locator_does_not_burn_an_attempt(conn, session):
    r = tools.verify_identity(session, conn, "Priya Raman", date_of_birth="1984-03-12", property_zip="80302")
    assert r["error"] == "INVALID_LOCATOR" and session.verification_attempts == 0


def test_wrong_factor_fails_without_saying_which(conn, session):
    r = tools.verify_identity(session, conn, "priya.raman@example.com", date_of_birth="1984-03-12", property_zip="10001")
    assert r["error"] == "VERIFICATION_FAILED" and r["attempts_remaining"] == 2
    text = json.dumps(r).lower()
    assert "could not be verified" in text and "which detail was wrong" in text
    assert "80302" not in text and "3816" not in text and "1984" not in text


def test_three_failures_lock_the_session(conn, session):
    for _ in range(3):
        r = tools.verify_identity(session, conn, "priya.raman@example.com", date_of_birth="2000-01-01", ssn_last4="0000")
    assert r["error"] == "SESSION_LOCKED" and session.locked
    assert tools.verify_identity(session, conn, **PRIYA)["error"] == "SESSION_LOCKED" and not session.verified


def test_unknown_customer_fails_like_a_wrong_factor(conn, session):
    r = tools.verify_identity(session, conn, "nobody@example.com", date_of_birth="1984-03-12", property_zip="80302")
    assert r["error"] == "VERIFICATION_FAILED" and session.verification_attempts == 1


def test_already_verified(conn, priya):
    assert tools.verify_identity(priya, conn, **PRIYA)["status"] == "already_verified"


def test_factor_values_never_appear_in_tool_output(conn, priya):
    blob = json.dumps([tools.get_loan_summary(priya, conn), tools.get_payment_history(priya, conn, 101),
                       tools.get_escrow_analysis(priya, conn, 101), tools.get_payoff_quote(priya, conn, 101),
                       tools.get_fee_details(priya, conn), tools.get_application_status(priya, conn)])
    assert "3816" not in blob and "1984-03-12" not in blob


# ------------------------------------------------------------------ the gate and ownership (R1, R11)

GATED_CALLS = [
    ("get_loan_summary", {}),
    ("get_payment_history", {"loan_id": 101}),
    ("get_escrow_analysis", {"loan_id": 101}),
    ("get_payoff_quote", {"loan_id": 101}),
    ("get_application_status", {}),
    ("make_payment", {"loan_id": 101, "amount": 100, "bank_last4": "4471", "confirmed": True}),
    ("set_autopay", {"loan_id": 101, "enabled": False, "confirmed": True}),
    ("create_handoff", {"team": "loan_officer", "reason_category": "refinance_inquiry", "summary": "x" * 50,
                        "customer_goal": "wants to refinance"}),
]


@pytest.mark.parametrize("name,args", GATED_CALLS, ids=[c[0] for c in GATED_CALLS])
def test_gated_tools_refuse_before_verification(conn, session, name, args):
    r = run_tool(session, conn, name, args)
    assert r["error"] == "SESSION_NOT_VERIFIED"
    assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM payments WHERE method = 'chat'").fetchone()[0] == 0


def test_gate_marker_matches_registry():
    gated = {n for n, f in tools.TOOLS.items() if getattr(f, "requires_verification", False)}
    assert gated == set(tools.TOOLS) - {"verify_identity", "get_fee_details", "escalate_to_human"}


@pytest.mark.parametrize("name,args", [
    ("get_loan_summary", {"loan_id": 102}), ("get_payment_history", {"loan_id": 102}),
    ("get_escrow_analysis", {"loan_id": 113}), ("get_payoff_quote", {"loan_id": 102}),
    ("get_application_status", {"application_id": "APP-58210"}),
    ("make_payment", {"loan_id": 102, "amount": 100, "bank_last4": "4471", "confirmed": True}),
    ("set_autopay", {"loan_id": 102, "enabled": True, "bank_last4": "4471", "confirmed": True}),
])
def test_verified_customer_cannot_reach_another_customers_records(conn, priya, name, args):
    r = run_tool(priya, conn, name, args)
    assert r["error"] in ("LOAN_NOT_FOUND", "APPLICATION_NOT_FOUND")


# ------------------------------------------------------------------ loan summary and status

def test_current_loan_summary(conn, priya):
    r = tools.get_loan_summary(priya, conn)
    loan = r["loans"][0]
    assert loan["status"] == "current" and loan["amount_due_now"] == 0 and loan["next_due_date"] == "2026-12-01"
    assert loan["monthly_payment"] == round(loan["monthly_principal_interest"] + loan["monthly_escrow"], 2) == 2162.83
    assert loan["upcoming_payment_change"]["new_monthly_payment"] == 2363.83
    assert "credit_reporting_note" not in loan
    assert r["bank_accounts_on_file"] == [{"bank_account": "••••4471", "account_type": "checking", "linked_on": "2021-05-14"}]


def test_past_due_loan_shows_amount_due_and_credit_reporting_fact(conn):
    s = verify(conn, "4198", ssn_last4="7392", date_of_birth="06/30/1988")
    loan = tools.get_loan_summary(s, conn)["loans"][0]
    assert (loan["status"], loan["days_past_due"], loan["installments_due_now"]) == ("past_due", 36, 2)
    assert loan["unpaid_fees"] == 67.0 and loan["amount_due_now"] == round(2 * 1974.98 + 67, 2)
    assert "30 or more days past due" in loan["credit_reporting_note"]


def test_forbearance_loan_inside_grace_period(conn):
    s = verify(conn, "robert.kaminski@example.com", ssn_last4="4672", property_zip="43215")
    loan = tools.get_loan_summary(s, conn)["loans"][0]
    assert loan["status"] == "due_within_grace_period" and loan["grace_period_ends_on"] == "2026-11-16"
    assert loan["forbearance"]["deferred_balance"] == 6344.82 and "cannot set or promise" in loan["forbearance"]["note"]


def test_heloc_summary_shows_available_credit_and_recent_draws(conn):
    s = verify(conn, "fatima.alsayed@example.com", date_of_birth="1977-09-03", ssn_last4="6619")
    loan = tools.get_loan_summary(s, conn)["loans"][0]
    assert loan["loan_type"] == "heloc" and loan["available_credit"] == 75000 - 9500
    assert loan["recent_draws"][0] == {"drawn_on": "2026-11-03", "amount": 9500.0, "channel": "online",
                                       "to_bank_account": "••••9930"}


def test_bankruptcy_handling_is_surfaced(conn):
    s = verify(conn, "victor.castellanos@example.com", date_of_birth="1968-10-11", property_zip="33133")
    loan = tools.get_loan_summary(s, conn)["loans"][0]
    assert loan["special_handling"]["type"] == "bankruptcy"
    assert "Do not request payment" in loan["special_handling"]["instruction"]


def test_multi_loan_customer_can_narrow(conn):
    s = verify(conn, "meiling.chao@example.com", date_of_birth="1989-03-29", ssn_last4="9408")
    assert [l["loan_type"] for l in tools.get_loan_summary(s, conn)["loans"]] == ["mortgage", "heloc"]
    assert len(tools.get_loan_summary(s, conn, loan_id=114)["loans"]) == 1


# ------------------------------------------------------------------ payment history

def test_payment_history_newest_first_with_split(conn, priya):
    r = tools.get_payment_history(priya, conn, 101)
    assert [p["received_on"] for p in r["payments"]] == sorted((p["received_on"] for p in r["payments"]), reverse=True)
    latest = r["payments"][0]
    assert latest["for_due_date"] == "2026-11-01" and latest["bank_account"] == "••••4471"
    assert latest["applied_principal"] + latest["applied_interest"] + latest["applied_escrow"] == pytest.approx(latest["amount"])


def test_returned_payment_is_explained(conn):
    s = verify(conn, "samuel.okafor@example.com", date_of_birth="1975-07-19", ssn_last4="5961")
    payments = tools.get_payment_history(s, conn, 110, limit=4)["payments"]
    returned = next(p for p in payments if p["status"] == "returned")
    assert "insufficient funds" in returned["status_note"] and "Nothing was applied" in returned["status_explanation"]
    assert tools.get_payment_history(s, conn, 110, limit="lots")["error"] == "BAD_ARGUMENTS"


# ------------------------------------------------------------------ escrow and payoff (R6)

def test_escrow_shortage_explains_the_increase(conn, priya):
    r = tools.get_escrow_analysis(priya, conn, 101)
    assert (r["prior_monthly_payment"], r["new_monthly_payment"], r["monthly_change"]) == (2162.83, 2363.83, 201.0)
    assert r["property_tax"]["change_annual"] == 720 and r["homeowners_insurance"]["change_annual"] == 564
    assert r["shortage_monthly"] == 94.0 and "$2,269.83" in r["options"][0]
    assert "Principal and interest do not change" in r["required_disclosure"]


def test_escrow_surplus_and_no_analysis_cases(conn):
    s = verify(conn, "meiling.chao@example.com", date_of_birth="1989-03-29", ssn_last4="9408")
    r = tools.get_escrow_analysis(s, conn, 113)
    assert r["monthly_change"] < 0 and "refunded by check" in r["options"][0]
    assert tools.get_escrow_analysis(s, conn, 114)["error"] == "NO_ESCROW"
    s2 = verify(conn, "aisha.bello@example.com", date_of_birth="1986-01-17", ssn_last4="4458")
    assert tools.get_escrow_analysis(s2, conn, 105)["analysis"] is None


def test_payoff_quote_adds_up_and_carries_disclosure(conn, priya):
    r = tools.get_payoff_quote(priya, conn, 101, "2026-11-20")
    assert r["interest_from"] == "2026-11-01" and r["interest_days"] == 19
    assert r["total_payoff"] == round(r["principal_balance"] + r["accrued_interest"] + r["unpaid_fees"], 2)
    assert "good through 2026-11-20" in r["required_disclosure"] and "official written payoff statement" in r["required_disclosure"]


def test_payoff_includes_fees_and_deferred_balance(conn):
    daniel = verify(conn, "dan.whitcombe@example.com", ssn_last4="7392", property_zip="85016")
    r = tools.get_payoff_quote(daniel, conn, 104)
    assert r["interest_from"] == "2026-09-01" and r["unpaid_fees"] == 67.0
    robert = verify(conn, "robert.kaminski@example.com", ssn_last4="4672", property_zip="43215")
    assert tools.get_payoff_quote(robert, conn, 115)["deferred_balance"] == 6344.82


@pytest.mark.parametrize("bad_date", ["2026-11-05", "2026-12-07", "someday"])
def test_payoff_date_window(conn, priya, bad_date):
    assert tools.get_payoff_quote(priya, conn, 101, bad_date)["error"] == "INVALID_DATE"


# ------------------------------------------------------------------ applications and fees

def test_stalled_application_lists_what_is_missing(conn):
    s = verify(conn, "elena.vt@example.com", date_of_birth="1991-11-02", property_zip="78704")
    app = tools.get_application_status(s, conn)["applications"][0]
    assert app["stage"] == "conditions_outstanding" and len(app["outstanding_items"]) == 3
    assert app["days_since_last_activity"] == 14 and app["loan_officer"] == "Dana Whitfield"


def test_application_with_tentative_closing_and_customer_with_none(conn, priya):
    s = verify(conn, "jamal.thompson@example.com", date_of_birth="1992-02-14", ssn_last4="8126")
    app = tools.get_application_status(s, conn, "app-58233")["applications"][0]
    assert app["estimated_closing_on"] == "2026-11-13" and "tentative" in app["note"]
    assert tools.get_application_status(priya, conn)["applications"] == []


def test_fee_schedule_is_public_but_charges_are_gated(conn, session):
    r = tools.get_fee_details(session, conn)
    assert len(r["fee_schedule"]) == 5 and r["charged_last_12_months"] is None
    assert tools.get_fee_details(session, conn, "late_fee")["fee_schedule"][0]["amount"] == 4.0
    assert tools.get_fee_details(session, conn, "made_up")["error"] == "UNKNOWN_FEE_TYPE"


def test_fees_charged_after_verification(conn):
    s = verify(conn, "t.nowicki@example.com", date_of_birth="1972-04-05", ssn_last4="6203")
    charge = tools.get_fee_details(s, conn)["charged_last_12_months"][0]
    assert (charge["fee_type"], charge["amount"], charge["status"]) == ("late_fee", 41.32, "unpaid")


# ------------------------------------------------------------------ payments and autopay (R9)

def _chat_payments(conn):
    return conn.execute("SELECT COUNT(*) FROM payments WHERE method = 'chat'").fetchone()[0]


def test_payment_needs_a_read_back_and_explicit_confirmation(conn, priya):
    for confirmed in (False, "yes", 1, None):
        r = tools.make_payment(priya, conn, 101, 500, "4471", confirmed=confirmed)
        assert r["error"] == "CONFIRMATION_REQUIRED"
    assert r["summary_to_confirm"] == "Pay $500.00 toward loan ••••1827 from checking account ••••4471 on 2026-11-06"
    assert _chat_payments(conn) == 0


def test_payment_today_and_scheduled(conn, priya):
    today = tools.make_payment(priya, conn, 101, "500", "4471", confirmed=True)
    assert today["ok"] and today["status"] == "pending" and today["payment_id"] == "PMT-C0001"
    later = tools.make_payment(priya, conn, 101, 250.5, "4471", "November 13, 2026", confirmed=True)
    assert later["status"] == "scheduled" and later["payment_id"] == "PMT-C0002"
    row = conn.execute("SELECT * FROM payments WHERE payment_id = 'PMT-C0002'").fetchone()
    assert (row["amount"], row["received_on"], row["bank_last4"], row["for_due_date"]) == (250.5, "2026-11-13", "4471", "2026-12-01")


@pytest.mark.parametrize("args,error", [
    (dict(bank_last4="9999"), "BANK_ACCOUNT_NOT_ON_FILE"),
    (dict(amount=0), "INVALID_AMOUNT"),
    (dict(amount=-5), "INVALID_AMOUNT"),
    (dict(amount="a lot"), "INVALID_AMOUNT"),
    (dict(amount=10_000_000), "INVALID_AMOUNT"),
    (dict(payment_date="2027-01-15"), "INVALID_DATE"),
])
def test_invalid_payments_are_refused_before_confirmation(conn, priya, args, error):
    r = tools.make_payment(priya, conn, **{"loan_id": 101, "amount": 500, "bank_last4": "4471", **args})
    assert r["error"] == error and _chat_payments(conn) == 0


def test_no_payments_or_autopay_changes_under_bankruptcy(conn):
    s = verify(conn, "victor.castellanos@example.com", date_of_birth="1968-10-11", property_zip="33133")
    assert tools.make_payment(s, conn, 112, 100, "7045", confirmed=True)["error"] == "BANKRUPTCY_ON_FILE"
    assert tools.set_autopay(s, conn, 112, True, "7045", confirmed=True)["error"] == "BANKRUPTCY_ON_FILE"


def test_enable_autopay_from_a_chosen_account(conn):
    s = verify(conn, "hannah.lindqvist@example.com", date_of_birth="1990-09-14", property_zip="55408")
    pending = tools.set_autopay(s, conn, 107, True, "1185")
    assert pending["error"] == "CONFIRMATION_REQUIRED" and "savings account ••••1185" in pending["summary_to_confirm"]
    r = tools.set_autopay(s, conn, 107, True, "1185", confirmed=True)
    assert r["ok"] and r["changed"] and r["first_draft_on"] == "2026-12-01"
    assert tuple(conn.execute("SELECT autopay_enabled, autopay_bank_last4 FROM loans WHERE loan_id = 107").fetchone()) == (1, "1185")
    assert tools.set_autopay(s, conn, 107, True, "1185", confirmed=True)["changed"] is False
    assert tools.set_autopay(s, conn, 107, "yes", "1185", confirmed=True)["error"] == "BAD_ARGUMENTS"
    assert tools.set_autopay(s, conn, 107, True, "4471", confirmed=True)["error"] == "BANK_ACCOUNT_NOT_ON_FILE"


def test_autopay_on_a_past_due_loan_explains_it_covers_future_installments(conn):
    s = verify(conn, "dan.whitcombe@example.com", ssn_last4="7392", property_zip="85016")
    r = tools.set_autopay(s, conn, 104, True, "6610", confirmed=True)
    assert r["ok"] and r["first_draft_on"] is None and "future installments only" in r["note"]


# ------------------------------------------------------------------ handoffs (R7)

GOOD_HANDOFF = dict(team="hardship_specialist", reason_category="payment_affordability",
                    summary="Customer's mortgage payment rises from $2,162.83 to $2,363.83 on 2026-12-01 after an escrow "
                            "shortage. Customer says the new amount is not affordable.",
                    customer_goal="Find a way to keep the monthly payment manageable.",
                    open_questions=["What assistance options exist?"], urgency="normal", loan_id=101)


def test_handoff_is_recorded_with_structured_summary(conn, priya):
    r = tools.create_handoff(priya, conn, **GOOD_HANDOFF)
    assert r["ok"] and r["handoff_id"] == "HO-0001" and "does not guarantee approval" in r["message"]
    row = conn.execute("SELECT * FROM handoffs").fetchone()
    assert (row["team"], row["customer_id"], row["loan_id"]) == ("hardship_specialist", 1, 101)
    assert json.loads(row["open_questions"]) == GOOD_HANDOFF["open_questions"]
    officer = tools.create_handoff(priya, conn, **{**GOOD_HANDOFF, "team": "loan_officer",
                                                    "reason_category": "refinance_inquiry"})
    assert officer["handoff_id"] == "HO-0002" and "Only the loan officer can discuss rates" in officer["message"]


@pytest.mark.parametrize("bad,expected", [
    (dict(team="sales"), "INVALID_TEAM"),
    (dict(reason_category="refinance_inquiry"), "INVALID_CATEGORY"),   # a loan officer category on the hardship team
    (dict(summary="too short"), "INCOMPLETE_SUMMARY"),
    (dict(customer_goal=""), "INCOMPLETE_SUMMARY"),
    (dict(open_questions=[1, 2]), "INCOMPLETE_SUMMARY"),
    (dict(urgency="asap"), "INVALID_URGENCY"),
    (dict(loan_id=102), "LOAN_NOT_FOUND"),
])
def test_incomplete_handoff_is_refused(conn, priya, bad, expected):
    assert tools.create_handoff(priya, conn, **{**GOOD_HANDOFF, **bad})["error"] == expected
    assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0


# ------------------------------------------------------------------ escalation (R8)

def test_escalation_works_before_verification(conn, session):
    r = tools.escalate_to_human(session, conn, "deceased_borrower", "Caller says the borrower passed away last month.")
    assert r["ok"] and r["customer_verified"] is False and "Do not share any loan details" in r["message"]
    assert conn.execute("SELECT customer_id FROM escalations").fetchone()[0] is None and session.escalated


def test_fraud_escalation_is_forced_high_urgency(conn, priya):
    r = tools.escalate_to_human(priya, conn, "suspected_fraud", "Customer does not recognize a $9,500 draw.", urgency="low")
    assert r["urgency"] == "high"
    assert tuple(conn.execute("SELECT customer_id, urgency FROM escalations").fetchone()) == (1, "high")


@pytest.mark.parametrize("bad,expected", [(dict(reason_category="angry"), "INVALID_CATEGORY"),
                                          (dict(details="short"), "INCOMPLETE_DETAILS"),
                                          (dict(urgency="now"), "INVALID_URGENCY")])
def test_bad_escalation_is_refused(conn, session, bad, expected):
    args = {"reason_category": "dispute", "details": "Customer disputes the October late fee.", **bad}
    assert tools.escalate_to_human(session, conn, **args)["error"] == expected and not session.escalated


# ------------------------------------------------------------------ dispatcher, trace, schemas

def test_run_tool_records_trace_with_rules(conn, session):
    run_tool(session, conn, "get_fee_details", {"fee_type": "late_fee"})
    run_tool(session, conn, "verify_identity", PRIYA)
    run_tool(session, conn, "get_loan_summary", {})
    assert [c.name for c in session.trace] == ["get_fee_details", "verify_identity", "get_loan_summary"]
    assert all(c.ok for c in session.trace) and session.trace[1].rules == ["R1", "R2"]


def test_run_tool_handles_unknown_tool_and_bad_arguments(conn, priya):
    assert run_tool(priya, conn, "approve_loan", {})["error"] == "UNKNOWN_TOOL"
    assert run_tool(priya, conn, "get_loan_summary", {"nonsense": 1})["error"] == "BAD_ARGUMENTS"
    assert run_tool(priya, conn, "get_payoff_quote", {})["error"] == "BAD_ARGUMENTS"


def test_no_tool_can_quote_rates_for_new_credit():
    """R4 is partly structural: nothing the agent can call returns pricing for a new loan or refinance."""
    names = " ".join(tools.TOOLS) + json.dumps(tools.TOOL_SPECS)
    assert not any(word in names for word in ("rate_quote", "get_rates", "pricing", "prequalif", "approve"))


@pytest.mark.parametrize("spec", tools.TOOL_SPECS, ids=lambda s: s["name"])
def test_schema_matches_function_signature(spec):
    schema = spec["input_schema"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    params = inspect.signature(tools.TOOLS[spec["name"]]).parameters
    py_args = [n for n in params if n not in ("session", "conn")]
    assert set(schema["properties"]) == set(py_args), spec["name"]
    py_required = {n for n in py_args if params[n].default is inspect.Parameter.empty}
    assert py_required <= set(schema["required"]) <= set(py_args), spec["name"]


def test_every_tool_has_a_spec_and_rule_mapping():
    assert {s["name"] for s in tools.TOOL_SPECS} == set(tools.TOOLS) == set(tools.TOOL_RULES)
