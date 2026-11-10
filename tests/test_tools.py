"""Every tool, every code-enforced rule."""
import json

import pytest

from agent import tools
from agent.session import Session
from agent.tools import run_tool

PRIYA = dict(locator="priya.raman@example.com", date_of_birth="1984-03-12", security_word="saffron")


# ------------------------------------------------------------------ verify_identity (R1, R2)

def test_verify_with_two_factors_and_flexible_date(conn, session):
    r = tools.verify_identity(session, conn, "****1827", date_of_birth="March 12, 1984", bank_last4="4471")
    assert r["ok"] and r["status"] == "verified" and r["customer_first_name"] == "Priya"
    assert session.verified_customer_id == 1
    assert r["accounts"][0]["account_number_masked"] == "****1827"


def test_verify_by_full_account_number(conn, session):
    r = tools.verify_identity(session, conn, "40022915", date_of_birth="08/23/1979", security_word="HARBOR ")
    assert r["ok"] and session.verified_customer_id == 2


def test_one_factor_is_not_enough_and_does_not_burn_an_attempt(conn, session):
    r = tools.verify_identity(session, conn, "priya.raman@example.com", date_of_birth="1984-03-12")
    assert not r["ok"] and r["error"] == "INSUFFICIENT_FACTORS"
    assert session.verification_attempts == 0 and not session.verified


def test_unrecognised_locator_does_not_burn_an_attempt(conn, session):
    r = tools.verify_identity(session, conn, "Priya Raman", date_of_birth="1984-03-12", security_word="saffron")
    assert r["error"] == "INVALID_LOCATOR" and session.verification_attempts == 0


def test_wrong_factor_fails_generically(conn, session):
    r = tools.verify_identity(session, conn, "priya.raman@example.com", date_of_birth="1984-03-12", security_word="wrong")
    assert not r["ok"] and r["error"] == "VERIFICATION_FAILED" and r["attempts_remaining"] == 2
    text = json.dumps(r).lower()
    assert "could not be verified" in text and "which detail was wrong" in text
    assert "saffron" not in text and "4471" not in text and "1984" not in text


def test_three_failures_lock_the_session(conn, session):
    for _ in range(3):
        r = tools.verify_identity(session, conn, "priya.raman@example.com", date_of_birth="2000-01-01", security_word="nope")
    assert r["error"] == "SESSION_LOCKED" and session.locked
    again = tools.verify_identity(session, conn, **PRIYA)
    assert again["error"] == "SESSION_LOCKED" and not session.verified, "correct factors must not unlock a locked session"


def test_unknown_customer_fails_like_a_wrong_factor(conn, session):
    r = tools.verify_identity(session, conn, "nobody@example.com", date_of_birth="1984-03-12", security_word="saffron")
    assert r["error"] == "VERIFICATION_FAILED" and session.verification_attempts == 1


def test_verify_twice_reports_already_verified(conn, verified):
    r = tools.verify_identity(verified, conn, "priya.raman@example.com", date_of_birth="1984-03-12", security_word="saffron")
    assert r["ok"] and r["status"] == "already_verified"


def test_factor_values_never_appear_in_any_tool_output(conn, verified):
    outputs = [
        tools.get_account_summary(verified, conn),
        tools.get_withdrawal_status(verified, conn),
        tools.get_transfer_status(verified, conn),
        tools.get_fee_details(verified, conn),
        tools.get_performance_breakdown(verified, conn, 101, "1Y"),
    ]
    blob = json.dumps(outputs)
    assert "saffron" not in blob and "4471" not in blob and "1984-03-12" not in blob


# ------------------------------------------------------------------ the gate (R1)

@pytest.mark.parametrize("name,args", [
    ("get_account_summary", {}),
    ("get_withdrawal_status", {}),
    ("get_transfer_status", {}),
    ("get_performance_breakdown", {"account_id": 101}),
    ("create_advisor_handoff", {"reason_category": "advice_request", "summary": "x" * 50, "customer_goal": "wants help"}),
])
def test_gated_tools_refuse_before_verification(conn, session, name, args):
    r = run_tool(session, conn, name, args)
    assert not r["ok"] and r["error"] == "SESSION_NOT_VERIFIED"
    assert conn.execute("SELECT COUNT(*) FROM advisor_handoffs").fetchone()[0] == 0


def test_gate_marker_matches_registry():
    gated = {n for n, f in tools.TOOLS.items() if getattr(f, "requires_verification", False)}
    assert gated == {"get_account_summary", "get_withdrawal_status", "get_transfer_status",
                     "get_performance_breakdown", "create_advisor_handoff"}


def test_verified_customer_cannot_read_another_customers_account(conn, verified):
    for name, args in [("get_account_summary", {"account_id": 102}),
                       ("get_withdrawal_status", {"account_id": 102}),
                       ("get_transfer_status", {"account_id": 104}),
                       ("get_performance_breakdown", {"account_id": 102}),
                       ("get_withdrawal_status", {"withdrawal_id": "WD-24117"}),
                       ("get_transfer_status", {"transfer_id": "TR-30552"})]:
        r = run_tool(verified, conn, name, args)
        assert not r["ok"], name
        assert r["error"] in ("ACCOUNT_NOT_FOUND", "WITHDRAWAL_NOT_FOUND", "TRANSFER_NOT_FOUND")


# ------------------------------------------------------------------ account servicing tools

def test_account_summary_totals_add_up(conn, verified):
    r = tools.get_account_summary(verified, conn)
    assert r["ok"] and r["as_of"] == "2026-11-06"
    acct = r["accounts"][0]
    assert acct["account_id"] == 101
    assert acct["total_value"] == pytest.approx(acct["cash_balance"] + sum(h["market_value"] for h in acct["holdings"]))
    assert all(h["market_value"] == pytest.approx(h["quantity"] * h["price"], abs=0.01) for h in acct["holdings"])


def test_multi_account_customer_can_narrow(conn):
    s = Session()
    tools.verify_identity(s, conn, "hannah.lindqvist@example.com", date_of_birth="1990-09-14", bank_last4="7730")
    assert len(tools.get_account_summary(s, conn)["accounts"]) == 2
    only = tools.get_account_summary(s, conn, account_id=107)["accounts"]
    assert len(only) == 1 and only[0]["managed"] is True


def test_withdrawal_status_by_id_and_list(conn):
    s = Session()
    tools.verify_identity(s, conn, "marcus.oyelaran@example.com", date_of_birth="1979-08-23", security_word="harbor")
    r = tools.get_withdrawal_status(s, conn, withdrawal_id="wd-24117")
    assert r["ok"] and r["withdrawals"][0]["status"] == "processing"
    assert "1 to 3 business days" in r["withdrawals"][0]["explanation"]
    assert r["withdrawals"][0]["expected_arrival_on"] == "2026-11-10"
    assert tools.get_withdrawal_status(s, conn)["withdrawals"][0]["withdrawal_id"] == "WD-24117"
    assert tools.get_withdrawal_status(s, conn, withdrawal_id="WD-00000")["error"] == "WITHDRAWAL_NOT_FOUND"


def test_withdrawal_on_hold_carries_reason(conn):
    s = Session()
    tools.verify_identity(s, conn, "elena.vt@example.com", date_of_birth="1991-11-02", bank_last4="8821")
    w = tools.get_withdrawal_status(s, conn)["withdrawals"][0]
    assert w["status"] == "on_hold" and "5 business days" in w["status_reason"]


def test_customer_with_no_withdrawals_gets_a_note(conn, verified):
    r = tools.get_withdrawal_status(verified, conn)
    assert r["ok"] and r["withdrawals"] == [] and "No withdrawals" in r["note"]


def test_transfer_status(conn):
    s = Session()
    tools.verify_identity(s, conn, "dan.whitcombe@example.com", date_of_birth="1988-06-30", security_word="compass")
    r = tools.get_transfer_status(s, conn, transfer_id="TR-30552")
    assert r["ok"] and r["transfers"][0]["status"] == "in_transit" and r["transfers"][0]["counterparty"] == "Pembrook Brokerage"
    assert tools.get_transfer_status(s, conn, transfer_id="TR-99999")["error"] == "TRANSFER_NOT_FOUND"


def test_fee_schedule_is_public_but_charges_are_gated(conn, session):
    r = tools.get_fee_details(session, conn)
    assert r["ok"] and len(r["fee_schedule"]) >= 8 and r["recent_charges"] is None
    assert "verification" in r["note"]
    one_fee = tools.get_fee_details(session, conn, fee_type="wire_outgoing")
    assert one_fee["fee_schedule"][0]["amount"] == 25.0
    assert tools.get_fee_details(session, conn, fee_type="made_up")["error"] == "UNKNOWN_FEE_TYPE"


def test_fee_charges_after_verification(conn):
    s = Session()
    tools.verify_identity(s, conn, "t.nowicki@example.com", date_of_birth="1972-04-05", bank_last4="5518")
    r = tools.get_fee_details(s, conn, fee_type="wire_outgoing")
    assert r["recent_charges"][0]["amount"] == -25.0 and r["recent_charges"][0]["account_number_masked"] == "****6342"


# ------------------------------------------------------------------ performance (R6)

def test_performance_math_and_disclosure(conn, verified):
    r = tools.get_performance_breakdown(verified, conn, 101, "3M")
    assert r["ok"] and r["period_start"] == "2026-08-06"
    assert r["portfolio_return_pct"] == pytest.approx(sum(c["contribution_pct"] for c in r["contributions"]), abs=0.05)
    assert sum(c["weight_pct"] for c in r["contributions"]) == pytest.approx(100, abs=0.3)
    assert r["end_value"] == pytest.approx(r["start_value"] + r["change_value"], abs=0.01)
    assert r["contributions"][0]["contribution_pct"] <= r["contributions"][-1]["contribution_pct"]
    assert r["difference_vs_benchmark_pct"] == pytest.approx(r["portfolio_return_pct"] - r["benchmark"]["return_pct"], abs=0.01)
    assert "Past performance" in r["required_disclosure"] and "not as a recommendation" in r["required_disclosure"]


@pytest.mark.parametrize("period,start", [("1M", "2026-10-06"), ("YTD", "2026-01-01"), ("1Y", "2025-11-06")])
def test_performance_period_starts(conn, verified, period, start):
    assert tools.get_performance_breakdown(verified, conn, 101, period)["period_start"] == start


def test_performance_rejects_bad_period_and_default_is_3m(conn, verified):
    assert tools.get_performance_breakdown(verified, conn, 101, "2W")["error"] == "INVALID_PERIOD"
    assert tools.get_performance_breakdown(verified, conn, 101)["period"] == "3M"


def test_performance_breakdown_never_recommends(conn, verified):
    blob = json.dumps(tools.get_performance_breakdown(verified, conn, 101, "3M")).lower()
    for word in ("should buy", "should sell", "recommend buying", "recommend selling", "will recover"):
        assert word not in blob


# ------------------------------------------------------------------ handoff (R7)

GOOD_HANDOFF = dict(reason_category="performance_concern",
                    summary="Customer is worried about a 12% three-month decline driven by two technology holdings and asked whether to sell.",
                    customer_goal="Decide whether to reduce technology exposure before year end.",
                    open_questions=["Should the HLVD position be trimmed?", "Is the portfolio too concentrated?"],
                    urgency="normal", account_id=101)


def test_handoff_is_recorded_with_structured_summary(conn, verified):
    r = tools.create_advisor_handoff(verified, conn, **GOOD_HANDOFF)
    assert r["ok"] and r["handoff_id"] == "HO-0001"
    row = conn.execute("SELECT * FROM advisor_handoffs").fetchone()
    assert row["customer_id"] == 1 and row["account_id"] == 101 and row["reason_category"] == "performance_concern"
    assert json.loads(row["open_questions"]) == GOOD_HANDOFF["open_questions"]
    second = tools.create_advisor_handoff(verified, conn, **GOOD_HANDOFF)
    assert second["handoff_id"] == "HO-0002"


@pytest.mark.parametrize("bad,expected", [
    (dict(reason_category="stock_tip"), "INVALID_CATEGORY"),
    (dict(summary="too short"), "INCOMPLETE_SUMMARY"),
    (dict(customer_goal=""), "INCOMPLETE_SUMMARY"),
    (dict(open_questions=[1, 2]), "INCOMPLETE_SUMMARY"),
    (dict(urgency="asap"), "INVALID_URGENCY"),
    (dict(account_id=102), "ACCOUNT_NOT_FOUND"),
])
def test_incomplete_handoff_is_refused(conn, verified, bad, expected):
    r = tools.create_advisor_handoff(verified, conn, **{**GOOD_HANDOFF, **bad})
    assert not r["ok"] and r["error"] == expected
    assert conn.execute("SELECT COUNT(*) FROM advisor_handoffs").fetchone()[0] == 0


def test_handoff_accepts_single_question_string_and_no_account(conn, verified):
    r = tools.create_advisor_handoff(verified, conn, **{**GOOD_HANDOFF, "open_questions": "Just one?", "account_id": None})
    assert r["ok"] and r["recorded"]["open_questions"] == ["Just one?"]


# ------------------------------------------------------------------ escalation (R8)

def test_escalation_works_before_verification(conn, session):
    r = tools.escalate_to_human(session, conn, "verification_failed", "Customer failed verification three times and is locked out.")
    assert r["ok"] and r["escalation_id"] == "ESC-0001" and r["customer_verified"] is False
    row = conn.execute("SELECT * FROM escalations").fetchone()
    assert row["customer_id"] is None and session.escalated


def test_fraud_escalation_is_forced_high_urgency(conn, verified):
    r = tools.escalate_to_human(verified, conn, "suspected_fraud", "Customer does not recognise a $3,000 withdrawal.", urgency="low")
    assert r["ok"] and r["urgency"] == "high" and "fraud specialist" in r["message"].lower()
    assert tuple(conn.execute("SELECT customer_id, urgency FROM escalations").fetchone()) == (1, "high")


@pytest.mark.parametrize("bad,expected", [
    (dict(reason_category="angry"), "INVALID_CATEGORY"),
    (dict(details="short"), "INCOMPLETE_DETAILS"),
    (dict(urgency="now"), "INVALID_URGENCY"),
])
def test_bad_escalation_is_refused(conn, session, bad, expected):
    args = {"reason_category": "complaint", "details": "Customer is unhappy with how losses were explained.", **bad}
    r = tools.escalate_to_human(session, conn, **args)
    assert r["error"] == expected and not session.escalated


# ------------------------------------------------------------------ dispatcher and trace

def test_run_tool_records_trace_with_rules(conn, session):
    run_tool(session, conn, "get_fee_details", {"fee_type": "paper_statement"})
    run_tool(session, conn, "verify_identity", PRIYA)
    run_tool(session, conn, "get_account_summary", {})
    names = [c.name for c in session.trace]
    assert names == ["get_fee_details", "verify_identity", "get_account_summary"]
    assert all(c.ok for c in session.trace)
    assert session.trace[1].rules == ["R1", "R2"] and session.trace[2].rules == ["R1"]


def test_run_tool_handles_unknown_tool_and_bad_arguments(conn, verified):
    assert run_tool(verified, conn, "transfer_money", {})["error"] == "UNKNOWN_TOOL"
    assert run_tool(verified, conn, "get_account_summary", {"nonsense": 1})["error"] == "BAD_ARGUMENTS"
    assert run_tool(verified, conn, "get_performance_breakdown", {})["error"] == "BAD_ARGUMENTS"
    assert len(verified.trace) == 3 and not any(c.ok for c in verified.trace)


def test_every_tool_has_a_spec_and_rule_mapping():
    spec_names = {s["name"] for s in tools.TOOL_SPECS}
    assert spec_names == set(tools.TOOLS) == set(tools.TOOL_RULES)
