"""The eight tools the agent can call, and the dispatcher that runs them.

Everything that can be enforced in code is enforced here, so the model never has to be trusted with it:
  * account tools refuse to return anything until the session is verified          (policy R1)
  * verification needs two matching factors, never says which factor failed,
    and locks the session after three misses                                       (policy R2)
  * a verified customer can only reach their own accounts                           (policy R1)
  * the performance tool attaches the disclosure the agent must relay              (policy R6)
  * a handoff is refused unless its structured summary is complete                 (policy R7)
  * an escalation is recorded even when the session was never verified            (policy R8)
What stays with the model: deciding when a handoff or escalation is warranted, declining advice
gracefully, and tone. Those live in policy.md and the version prompts.

Every tool takes (session, conn, **args) and returns a JSON-serialisable dict with an "ok" flag.
"""
import json
from datetime import date, datetime

from config import AS_OF
from mockworld.db import one, rows
from agent.session import Session, ToolCall

MAX_VERIFICATION_ATTEMPTS = 3
MIN_FACTORS = 2
PERIODS = ("1M", "3M", "YTD", "1Y")
HANDOFF_CATEGORIES = ("performance_concern", "advice_request", "retirement_planning", "tax_question",
                      "portfolio_review", "other")
ESCALATION_CATEGORIES = ("complaint", "suspected_fraud", "verification_failed", "account_security",
                         "distressed_customer", "out_of_scope", "other")
URGENCY_LEVELS = ("low", "normal", "high")
HIGH_URGENCY_CATEGORIES = ("suspected_fraud", "account_security")

PERFORMANCE_DISCLOSURE = (
    "Past performance does not guarantee future results. Investing involves risk, including possible loss of "
    f"principal. Figures are as of {AS_OF} and are provided for information only, not as a recommendation."
)
WITHDRAWAL_STATUS_TEXT = {
    "pending": "The request has been received and will start processing on the next business day.",
    "processing": "The withdrawal is being sent to the destination bank. Funds typically arrive 1 to 3 business days "
                  "after processing starts.",
    "completed": "Funds were sent to the destination bank. Some banks take one more business day to post them.",
    "on_hold": "The withdrawal is paused. See status_reason for why and when it will resume.",
    "rejected": "The withdrawal was not processed. See status_reason for why and what the customer can do.",
}
TRANSFER_STATUS_TEXT = {
    "initiated": "The request has been submitted and is waiting for the other firm to respond.",
    "validating": "Account details are being matched with the other firm's records.",
    "in_transit": "The other firm has accepted the transfer and assets are moving.",
    "completed": "The transfer has finished.",
    "rejected": "The transfer was returned. See status_reason for why and how to resubmit.",
}
ESCALATION_MESSAGES = {
    "suspected_fraud": "A fraud specialist has been assigned with high priority and will call the phone number on file "
                       "within 2 hours during business hours. Advise the customer not to share account details in chat.",
    "account_security": "A security specialist has been assigned with high priority and will call the phone number on file.",
    "verification_failed": "A specialist will contact the customer at the phone number on file to complete verification another way.",
    "complaint": "The complaint has been logged. A supervisor will respond within 2 business days.",
    "distressed_customer": "A senior representative will call the customer within 1 business day.",
}
DEFAULT_ESCALATION_MESSAGE = "A human specialist has been assigned and will follow up within 1 business day."

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%B %d, %Y", "%B %d %Y", "%d %B %Y",
                 "%b %d, %Y", "%b %d %Y", "%d %b %Y")


# ----------------------------------------------------------------------------- helpers

def _ok(**data) -> dict:
    return {"ok": True, **data}


def _err(code: str, message: str, **extra) -> dict:
    return {"ok": False, "error": code, "message": message, **extra}


def _mask(account_number: str) -> str:
    return "****" + account_number[-4:]


def _parse_date(text) -> date | None:
    if isinstance(text, date):
        return text
    text = str(text).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _digits(text) -> str:
    return "".join(ch for ch in str(text) if ch.isdigit())


def _period_start(period: str) -> str:
    as_of = date.fromisoformat(AS_OF)
    if period == "YTD":
        return as_of.replace(month=1, day=1).isoformat()
    months = {"1M": 1, "3M": 3, "1Y": 12}[period]
    month = as_of.month - months
    year = as_of.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return as_of.replace(year=year, month=month).isoformat()


def _customer(conn, customer_id: int) -> dict:
    return one(conn, "SELECT * FROM customers WHERE customer_id = ?", (customer_id,))


def _account_list(conn, customer_id: int) -> list[dict]:
    return [
        {"account_id": a["account_id"], "account_number_masked": _mask(a["account_number"]),
         "account_type": a["account_type"], "managed": bool(a["managed"])}
        for a in rows(conn, "SELECT * FROM accounts WHERE customer_id = ? ORDER BY account_id", (customer_id,))
    ]


def _own_accounts(session: Session, conn, account_id=None) -> list[dict]:
    """Accounts of the verified customer, optionally narrowed to one. Empty if account_id is not theirs."""
    sql = "SELECT * FROM accounts WHERE customer_id = ?"
    params: tuple = (session.verified_customer_id,)
    if account_id is not None:
        sql += " AND account_id = ?"
        params += (int(account_id),)
    return rows(conn, sql + " ORDER BY account_id", params)


def requires_verification(fn):
    """Decorator: the tool returns SESSION_NOT_VERIFIED instead of running until verify_identity has succeeded."""
    def wrapper(session: Session, conn, *args, **kwargs):
        if not session.verified:
            return _err("SESSION_NOT_VERIFIED",
                        "Identity must be verified before any account information can be shared. "
                        "Call verify_identity first.")
        return fn(session, conn, *args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.requires_verification = True
    return wrapper


# ----------------------------------------------------------------------------- tools

def verify_identity(session: Session, conn, locator, date_of_birth=None, bank_last4=None, security_word=None) -> dict:
    """Verify the customer with two of three factors. Never reveals which factor failed."""
    if session.verified:
        cust = _customer(conn, session.verified_customer_id)
        return _ok(status="already_verified", customer_first_name=cust["first_name"],
                   accounts=_account_list(conn, cust["customer_id"]))
    if session.locked:
        return _err("SESSION_LOCKED", "Verification is locked for this session after too many failed attempts. "
                    "Do not try again; offer to escalate to a human specialist.", status="locked")

    provided = {k: v for k, v in {"date_of_birth": date_of_birth, "bank_last4": bank_last4,
                                  "security_word": security_word}.items() if v not in (None, "")}
    if len(provided) < MIN_FACTORS:
        return _err("INSUFFICIENT_FACTORS",
                    f"Verification needs at least {MIN_FACTORS} of: date of birth, last four digits of the linked "
                    "bank account, security word. Ask for what is missing.",
                    status="need_more_factors", factors_received=sorted(provided))

    customer = _locate_customer(conn, locator)
    if customer is _INVALID_LOCATOR:
        return _err("INVALID_LOCATOR", "The locator must be the email address on file, or the 8-digit account "
                    "number, or its last 4 digits.", status="need_locator")

    matched = customer is not None and all(_factor_matches(customer, k, v) for k, v in provided.items())
    if matched:
        session.verified_customer_id = customer["customer_id"]
        return _ok(status="verified", customer_first_name=customer["first_name"],
                   accounts=_account_list(conn, customer["customer_id"]))

    session.verification_attempts += 1
    remaining = MAX_VERIFICATION_ATTEMPTS - session.verification_attempts
    if remaining <= 0:
        session.locked = True
        return _err("SESSION_LOCKED", "Verification failed and the session is now locked. Do not say which detail "
                    "was wrong. Offer to escalate to a human specialist who can verify the customer another way.",
                    status="locked", attempts_remaining=0)
    return _err("VERIFICATION_FAILED", "The details provided could not be verified. Do not say which detail was "
                f"wrong. The customer may try again ({remaining} attempt(s) remaining).",
                status="failed", attempts_remaining=remaining)


_INVALID_LOCATOR = object()


def _locate_customer(conn, locator):
    text = str(locator or "").strip().lower()
    if "@" in text:
        return one(conn, "SELECT * FROM customers WHERE lower(email) = ?", (text,))
    digits = _digits(text)
    if len(digits) == 8:
        acct = one(conn, "SELECT customer_id FROM accounts WHERE account_number = ?", (digits,))
    elif len(digits) == 4:
        acct = one(conn, "SELECT customer_id FROM accounts WHERE substr(account_number, -4) = ?", (digits,))
    else:
        return _INVALID_LOCATOR
    return _customer(conn, acct["customer_id"]) if acct else None


def _factor_matches(customer: dict, factor: str, value) -> bool:
    if factor == "date_of_birth":
        given = _parse_date(value)
        return given is not None and given.isoformat() == customer["date_of_birth"]
    if factor == "bank_last4":
        return _digits(value)[-4:] == customer["bank_last4"] and len(_digits(value)) >= 4
    if factor == "security_word":
        return str(value).strip().casefold() == customer["security_word"].casefold()
    return False


@requires_verification
def get_account_summary(session: Session, conn, account_id=None) -> dict:
    """Balances and holdings for the verified customer's accounts (or one of them)."""
    accounts = _own_accounts(session, conn, account_id)
    if not accounts:
        return _err("ACCOUNT_NOT_FOUND", "No account with that id belongs to the verified customer.")
    out = []
    for a in accounts:
        holdings = rows(conn, """SELECT h.symbol, s.name, s.asset_class, s.sector, h.quantity, h.cost_basis, s.price
                                 FROM holdings h JOIN securities s ON s.symbol = h.symbol
                                 WHERE h.account_id = ? ORDER BY h.quantity * s.price DESC""", (a["account_id"],))
        for h in holdings:
            h["market_value"] = round(h["quantity"] * h["price"], 2)
            h["unrealized_gain"] = round(h["market_value"] - h["cost_basis"], 2)
        holdings_value = round(sum(h["market_value"] for h in holdings), 2)
        out.append({
            "account_id": a["account_id"], "account_number_masked": _mask(a["account_number"]),
            "account_type": a["account_type"], "managed": bool(a["managed"]), "opened_on": a["opened_on"],
            "cash_balance": a["cash_balance"], "settled_cash": a["settled_cash"],
            "holdings_value": holdings_value, "total_value": round(holdings_value + a["cash_balance"], 2),
            "holdings": holdings,
        })
    cust = _customer(conn, session.verified_customer_id)
    return _ok(as_of=AS_OF, customer_first_name=cust["first_name"], accounts=out)


@requires_verification
def get_withdrawal_status(session: Session, conn, account_id=None, withdrawal_id=None) -> dict:
    """Recent withdrawals for the customer's accounts, or one withdrawal by id, with plain-language status."""
    accounts = _own_accounts(session, conn, account_id)
    if not accounts:
        return _err("ACCOUNT_NOT_FOUND", "No account with that id belongs to the verified customer.")
    ids = tuple(a["account_id"] for a in accounts)
    marks = ",".join("?" * len(ids))
    if withdrawal_id:
        found = rows(conn, f"SELECT * FROM withdrawals WHERE withdrawal_id = ? AND account_id IN ({marks})",
                     (str(withdrawal_id).strip().upper(), *ids))
        if not found:
            return _err("WITHDRAWAL_NOT_FOUND", "No withdrawal with that id exists on the customer's accounts.")
    else:
        found = rows(conn, f"SELECT * FROM withdrawals WHERE account_id IN ({marks}) "
                           "ORDER BY requested_on DESC, withdrawal_id DESC LIMIT 10", ids)
    masked = {a["account_id"]: _mask(a["account_number"]) for a in accounts}
    for w in found:
        w["account_number_masked"] = masked[w["account_id"]]
        w["explanation"] = WITHDRAWAL_STATUS_TEXT[w["status"]]
    return _ok(as_of=AS_OF, withdrawals=found,
               note=None if found else "No withdrawals found on the customer's accounts.")


@requires_verification
def get_transfer_status(session: Session, conn, account_id=None, transfer_id=None) -> dict:
    """Account transfers (incoming, outgoing, internal) for the customer's accounts, or one by id."""
    accounts = _own_accounts(session, conn, account_id)
    if not accounts:
        return _err("ACCOUNT_NOT_FOUND", "No account with that id belongs to the verified customer.")
    ids = tuple(a["account_id"] for a in accounts)
    marks = ",".join("?" * len(ids))
    if transfer_id:
        found = rows(conn, f"SELECT * FROM transfers WHERE transfer_id = ? AND account_id IN ({marks})",
                     (str(transfer_id).strip().upper(), *ids))
        if not found:
            return _err("TRANSFER_NOT_FOUND", "No transfer with that id exists on the customer's accounts.")
    else:
        found = rows(conn, f"SELECT * FROM transfers WHERE account_id IN ({marks}) "
                           "ORDER BY initiated_on DESC, transfer_id DESC LIMIT 10", ids)
    masked = {a["account_id"]: _mask(a["account_number"]) for a in accounts}
    for t in found:
        t["account_number_masked"] = masked[t["account_id"]]
        t["explanation"] = TRANSFER_STATUS_TEXT[t["status"]]
    return _ok(as_of=AS_OF, transfers=found,
               note=None if found else "No transfers found on the customer's accounts.")


def get_fee_details(session: Session, conn, fee_type=None) -> dict:
    """The public fee schedule (no verification needed) plus, if verified, fees charged on the customer's accounts."""
    if fee_type:
        schedule = rows(conn, "SELECT * FROM fee_schedule WHERE fee_type = ?", (str(fee_type).strip().lower(),))
        if not schedule:
            known = [r["fee_type"] for r in rows(conn, "SELECT fee_type FROM fee_schedule ORDER BY fee_type")]
            return _err("UNKNOWN_FEE_TYPE", f"Unknown fee type. Known types: {', '.join(known)}.")
    else:
        schedule = rows(conn, "SELECT * FROM fee_schedule ORDER BY fee_type")
    result = {"as_of": AS_OF, "fee_schedule": schedule}
    if session.verified:
        charges = rows(conn, """SELECT t.posted_on, t.amount, t.description, a.account_number
                                FROM transactions t JOIN accounts a ON a.account_id = t.account_id
                                WHERE a.customer_id = ? AND t.type = 'fee' AND t.posted_on >= ?
                                ORDER BY t.posted_on DESC""",
                       (session.verified_customer_id, _period_start("1Y")))
        for c in charges:
            c["account_number_masked"] = _mask(c.pop("account_number"))
        result["recent_charges"] = charges
        result["note"] = None if charges else "No fees were charged on the customer's accounts in the last 12 months."
    else:
        result["recent_charges"] = None
        result["note"] = ("The fee schedule is public information. Fees charged on the customer's own accounts "
                          "require identity verification first.")
    return _ok(**result)


@requires_verification
def get_performance_breakdown(session: Session, conn, account_id, period="3M") -> dict:
    """Period return for one account, contribution by holding, and the benchmark, with the required disclosure."""
    period = str(period).strip().upper()
    if period not in PERIODS:
        return _err("INVALID_PERIOD", f"period must be one of {', '.join(PERIODS)}.")
    accounts = _own_accounts(session, conn, account_id)
    if not accounts:
        return _err("ACCOUNT_NOT_FOUND", "No account with that id belongs to the verified customer.")
    acct = accounts[0]
    holdings = rows(conn, """SELECT h.symbol, s.name, s.sector, h.quantity, s.price, r.return_pct
                             FROM holdings h JOIN securities s ON s.symbol = h.symbol
                             JOIN security_returns r ON r.symbol = h.symbol AND r.period = ?
                             WHERE h.account_id = ?""", (period, acct["account_id"]))
    end_total = sum(h["quantity"] * h["price"] for h in holdings)
    start_total = sum(h["quantity"] * h["price"] / (1 + h["return_pct"] / 100) for h in holdings)
    contributions = []
    for h in holdings:
        end_value = h["quantity"] * h["price"]
        start_value = end_value / (1 + h["return_pct"] / 100)
        weight = start_value / start_total if start_total else 0.0
        contributions.append({
            "symbol": h["symbol"], "name": h["name"], "sector": h["sector"],
            "market_value": round(end_value, 2), "weight_pct": round(weight * 100, 1),
            "return_pct": h["return_pct"], "contribution_pct": round(weight * h["return_pct"], 2),
        })
    contributions.sort(key=lambda c: c["contribution_pct"])
    portfolio_return = (end_total - start_total) / start_total * 100 if start_total else 0.0
    bench = one(conn, """SELECT b.benchmark_id, b.name, r.return_pct FROM benchmarks b
                         JOIN benchmark_returns r ON r.benchmark_id = b.benchmark_id AND r.period = ?
                         WHERE b.benchmark_id = ?""", (period, acct["benchmark_id"]))
    period_start = _period_start(period)
    flows = one(conn, """SELECT COALESCE(SUM(amount), 0) AS net FROM transactions
                         WHERE account_id = ? AND posted_on >= ?
                         AND type IN ('deposit', 'withdrawal', 'transfer_in', 'transfer_out')""",
                (acct["account_id"], period_start))
    return _ok(
        as_of=AS_OF, account_id=acct["account_id"], account_number_masked=_mask(acct["account_number"]),
        period=period, period_start=period_start,
        start_value=round(start_total, 2), end_value=round(end_total, 2),
        change_value=round(end_total - start_total, 2), portfolio_return_pct=round(portfolio_return, 2),
        benchmark={"name": bench["name"], "return_pct": bench["return_pct"]},
        difference_vs_benchmark_pct=round(portfolio_return - bench["return_pct"], 2),
        contributions=contributions, cash_balance=acct["cash_balance"],
        net_cash_flows_in_period=round(flows["net"], 2),
        method_note="Return is computed on invested holdings only and assumes they were held for the whole period. "
                    "Cash and deposits or withdrawals during the period are excluded from the return figure.",
        required_disclosure=PERFORMANCE_DISCLOSURE,
    )


@requires_verification
def create_advisor_handoff(session: Session, conn, reason_category, summary, customer_goal,
                           open_questions=None, urgency="normal", account_id=None) -> dict:
    """Record a handoff to a licensed advisor. Refused unless the structured summary is complete."""
    if reason_category not in HANDOFF_CATEGORIES:
        return _err("INVALID_CATEGORY", f"reason_category must be one of {', '.join(HANDOFF_CATEGORIES)}.")
    if urgency not in URGENCY_LEVELS:
        return _err("INVALID_URGENCY", f"urgency must be one of {', '.join(URGENCY_LEVELS)}.")
    if not isinstance(summary, str) or len(summary.strip()) < 40:
        return _err("INCOMPLETE_SUMMARY", "summary must describe the customer's situation and what was discussed, "
                    "in at least 40 characters.")
    if not isinstance(customer_goal, str) or len(customer_goal.strip()) < 10:
        return _err("INCOMPLETE_SUMMARY", "customer_goal must state what the customer wants, in at least 10 characters.")
    if isinstance(open_questions, str):
        open_questions = [open_questions]
    if open_questions is None:
        open_questions = []
    if not isinstance(open_questions, list) or not all(isinstance(q, str) for q in open_questions):
        return _err("INCOMPLETE_SUMMARY", "open_questions must be a list of strings (it may be empty).")
    if account_id is not None and not _own_accounts(session, conn, account_id):
        return _err("ACCOUNT_NOT_FOUND", "No account with that id belongs to the verified customer.")
    count = one(conn, "SELECT COUNT(*) AS n FROM advisor_handoffs")["n"]
    handoff_id = f"HO-{count + 1:04d}"
    conn.execute("""INSERT INTO advisor_handoffs (handoff_id, customer_id, account_id, created_on, reason_category,
                    summary, customer_goal, open_questions, urgency) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (handoff_id, session.verified_customer_id, account_id, AS_OF, reason_category, summary.strip(),
                  customer_goal.strip(), json.dumps(open_questions), urgency))
    conn.commit()
    return _ok(handoff_id=handoff_id, status="open",
               message="A licensed Larkmoor advisor will contact the customer within 1 business day using the phone "
                       "number and email on file. The advisor can discuss recommendations; this chat cannot.",
               recorded={"reason_category": reason_category, "summary": summary.strip(),
                         "customer_goal": customer_goal.strip(), "open_questions": open_questions, "urgency": urgency})


def escalate_to_human(session: Session, conn, reason_category, details, urgency="normal") -> dict:
    """Hand the conversation to a human specialist. Allowed before verification (fraud, lockouts, complaints)."""
    if reason_category not in ESCALATION_CATEGORIES:
        return _err("INVALID_CATEGORY", f"reason_category must be one of {', '.join(ESCALATION_CATEGORIES)}.")
    if urgency not in URGENCY_LEVELS:
        return _err("INVALID_URGENCY", f"urgency must be one of {', '.join(URGENCY_LEVELS)}.")
    if not isinstance(details, str) or len(details.strip()) < 20:
        return _err("INCOMPLETE_DETAILS", "details must explain the situation in at least 20 characters.")
    if reason_category in HIGH_URGENCY_CATEGORIES:
        urgency = "high"
    count = one(conn, "SELECT COUNT(*) AS n FROM escalations")["n"]
    escalation_id = f"ESC-{count + 1:04d}"
    conn.execute("""INSERT INTO escalations (escalation_id, customer_id, created_on, reason_category, details, urgency)
                    VALUES (?,?,?,?,?,?)""",
                 (escalation_id, session.verified_customer_id, AS_OF, reason_category, details.strip(), urgency))
    conn.commit()
    session.escalated = True
    return _ok(escalation_id=escalation_id, status="open", urgency=urgency, customer_verified=session.verified,
               message=ESCALATION_MESSAGES.get(reason_category, DEFAULT_ESCALATION_MESSAGE))


# ----------------------------------------------------------------------------- registry

TOOLS = {
    "verify_identity": verify_identity,
    "get_account_summary": get_account_summary,
    "get_withdrawal_status": get_withdrawal_status,
    "get_transfer_status": get_transfer_status,
    "get_fee_details": get_fee_details,
    "get_performance_breakdown": get_performance_breakdown,
    "create_advisor_handoff": create_advisor_handoff,
    "escalate_to_human": escalate_to_human,
}

# Policy rule IDs (policy.md) that each tool enforces or informs. Shown in the demo UI side panel.
TOOL_RULES = {
    "verify_identity": ["R1", "R2"],
    "get_account_summary": ["R1"],
    "get_withdrawal_status": ["R1"],
    "get_transfer_status": ["R1"],
    "get_fee_details": ["R1", "R3"],
    "get_performance_breakdown": ["R1", "R6"],
    "create_advisor_handoff": ["R4", "R7"],
    "escalate_to_human": ["R8"],
}

# What the model sees. Descriptions carry the operational rules the model needs at call time.
TOOL_SPECS = [
    {"name": "verify_identity",
     "description": "Verify the customer's identity before sharing any account information. Needs the locator "
                    "(email on file, or 8-digit account number, or its last 4 digits) plus at least two of: date of "
                    "birth, last 4 digits of the linked bank account, security word. Never tell the customer which "
                    "factor failed. After 3 failures the session locks; then offer escalation.",
     "parameters": {"locator": "string", "date_of_birth": "string?", "bank_last4": "string?", "security_word": "string?"}},
    {"name": "get_account_summary",
     "description": "Balances, cash and holdings for the verified customer. Pass account_id to narrow to one account.",
     "parameters": {"account_id": "integer?"}},
    {"name": "get_withdrawal_status",
     "description": "Status and plain-language explanation of withdrawals on the verified customer's accounts. "
                    "Pass withdrawal_id (e.g. WD-24117) for one, or account_id to narrow.",
     "parameters": {"account_id": "integer?", "withdrawal_id": "string?"}},
    {"name": "get_transfer_status",
     "description": "Status of account transfers (incoming from another firm, outgoing, or between Larkmoor accounts) "
                    "for the verified customer. Pass transfer_id (e.g. TR-30552) for one, or account_id to narrow.",
     "parameters": {"account_id": "integer?", "transfer_id": "string?"}},
    {"name": "get_fee_details",
     "description": "The fee schedule (public, no verification needed) and, once verified, fees actually charged on "
                    "the customer's accounts in the last 12 months. Optional fee_type filter.",
     "parameters": {"fee_type": "string?"}},
    {"name": "get_performance_breakdown",
     "description": "Period return for one verified account, contribution by holding, benchmark comparison, and the "
                    "required disclosure, which must be included when discussing performance. period: 1M, 3M, YTD or 1Y.",
     "parameters": {"account_id": "integer", "period": "string?"}},
    {"name": "create_advisor_handoff",
     "description": "Hand the customer to a licensed advisor when they want a recommendation, planning help, or a "
                    "portfolio review. Requires a complete structured summary of what was discussed.",
     "parameters": {"reason_category": "string", "summary": "string", "customer_goal": "string",
                    "open_questions": "string[]?", "urgency": "string?", "account_id": "integer?"}},
    {"name": "escalate_to_human",
     "description": "Transfer to a human specialist for complaints, suspected fraud, failed verification or lockout, "
                    "account security concerns, a distressed customer, or anything out of scope. Works before verification.",
     "parameters": {"reason_category": "string", "details": "string", "urgency": "string?"}},
]


def run_tool(session: Session, conn, name: str, args: dict | None = None) -> dict:
    """Run one tool by name, record it in the session trace, and return its result. Never raises on bad input."""
    args = dict(args or {})
    fn = TOOLS.get(name)
    if fn is None:
        result = _err("UNKNOWN_TOOL", f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}.")
    else:
        try:
            result = fn(session, conn, **args)
        except (TypeError, ValueError) as exc:
            result = _err("BAD_ARGUMENTS", f"{name} could not run with those arguments: {exc}")
    session.trace.append(ToolCall(name=name, args=args, result=result, rules=TOOL_RULES.get(name, [])))
    return result
