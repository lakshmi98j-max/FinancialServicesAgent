"""The eleven tools the agent can call, and the dispatcher that runs them.

Everything that can be enforced in code is enforced here, so the model never has to be trusted with it:
  * loan tools refuse to return anything until the session is verified              (policy R1)
  * verification needs two matching factors, never says which one failed,
    and locks the session after three misses                                       (policy R2)
  * a verified customer can only reach their own loans and applications           (policy R1, R11)
  * no tool returns rates or terms for new credit, so the agent has none to quote  (policy R4)
  * payoff and escrow tools attach the disclosure the agent must relay             (policy R6)
  * a handoff is refused unless its structured summary is complete                 (policy R7)
  * an escalation is recorded even when the session was never verified            (policy R8)
  * payments and autopay need confirmed=true, a bank account already on file,
    and are blocked on loans under bankruptcy handling                             (policy R9, R11)
What stays with the model: when to hand off or escalate, declining advice and rate requests gracefully, asking for
confirmation, and tone. policy.md lists the split rule by rule.

Every tool takes (session, conn, **args) and returns a JSON-serialisable dict with an "ok" flag.
"""
import functools
import json
from datetime import date, datetime, timedelta

from agent.session import Session, ToolCall
from config import AS_OF, BRAND
from mockworld.db import one, rows
from mockworld.seed import FEE_SCHEDULE, GRACE_DAYS, add_months

TODAY = date.fromisoformat(AS_OF)
MAX_VERIFICATION_ATTEMPTS = 3
MIN_FACTORS = 2
DATE_WINDOW_DAYS = 30            # payoff quotes and scheduled payments, from today

HANDOFF_TEAMS = {
    "loan_officer": ("refinance_inquiry", "new_credit_inquiry", "application_help", "rates_and_terms", "other"),
    "hardship_specialist": ("payment_affordability", "income_loss", "medical_or_disaster", "post_forbearance", "other"),
}
ESCALATION_CATEGORIES = ("suspected_fraud", "account_security", "verification_failed", "complaint", "dispute",
                         "bankruptcy", "servicemember", "deceased_borrower", "distressed_customer", "out_of_scope",
                         "other")
HIGH_URGENCY_CATEGORIES = ("suspected_fraud", "account_security")
URGENCY_LEVELS = ("low", "normal", "high")

PAYOFF_DISCLOSURE = ("This quote is an estimate good through {good_through}. Interest accrues at ${per_diem} per day "
                     "after that date. The official written payoff statement governs, and figures can change if a "
                     "payment is returned or a fee posts. Any escrow balance is refunded separately after payoff.")
PAYMENT_CHANGE_DISCLOSURE = ("Escrow payments change when property taxes or homeowners insurance premiums change. "
                             "These figures come from the escrow analysis dated {analysis_date}. The new payment starts "
                             "with the installment due {effective_date}. Principal and interest do not change.")
CREDIT_REPORTING_NOTE = (f"{BRAND} reports a loan to the credit bureaus as late when a payment is 30 or more days "
                         "past due. This chat cannot change or promise anything about credit reporting.")
FORBEARANCE_NOTE = ("Installments skipped during forbearance were moved to a deferred balance. How it is repaid is "
                    "decided with a hardship specialist. This chat cannot set or promise a repayment option.")
STATUS_TEXT = {
    "current": "The loan is up to date. The next installment is not yet due.",
    "due_within_grace_period": "An installment is due. No late fee applies if it is received by the end of the grace period.",
    "past_due": "At least one installment was not received by the end of its grace period.",
}
PAYMENT_STATUS_TEXT = {
    "posted": "Received and applied to the loan.",
    "returned": "Returned unpaid by the customer's bank. Nothing was applied.",
    "pending": "Received today and being processed. It is credited as of today.",
    "scheduled": "Scheduled for a future date. It has not been drafted yet.",
}
STAGE_TEXT = {
    "submitted": "The application was received and is waiting for review.",
    "conditions_outstanding": "The application is waiting on items from the applicant, listed in outstanding_items.",
    "underwriting": "An underwriter is reviewing the application. No action is needed from the applicant right now.",
    "approved_closing_scheduled": "The application is approved and a closing is scheduled, subject to the note.",
    "withdrawn": "The application was withdrawn.",
}
HANDOFF_MESSAGES = {
    "loan_officer": f"A licensed {BRAND} loan officer will contact the customer within 1 business day using the phone "
                    "number and email on file. Only the loan officer can discuss rates, terms, approval, or whether an "
                    "option fits the customer.",
    "hardship_specialist": "A hardship specialist will contact the customer within 2 business days to review assistance "
                           "options and the documents needed. Submitting a request does not guarantee approval of any "
                           "option.",
}
ESCALATION_MESSAGES = {
    "suspected_fraud": "A fraud specialist has been assigned with high priority and will call the phone number on file "
                       "within 2 hours during business hours. Advise the customer not to share codes or passwords with "
                       "anyone.",
    "account_security": "A security specialist has been assigned with high priority and will call the phone number on file.",
    "verification_failed": "A specialist will call the phone number on file to verify the customer another way.",
    "complaint": "The complaint has been logged. A supervisor will respond within 2 business days.",
    "dispute": "The dispute has been logged for research and a specialist will respond in writing. The customer can "
               "also send a written notice of error to the address on their statement.",
    "bankruptcy": "The bankruptcy team has been notified and will contact the customer or their attorney.",
    "servicemember": "The military benefits team will contact the customer about protections for servicemembers.",
    "deceased_borrower": "The estate team will contact the caller about the documents needed. Do not share any loan "
                         "details with the caller in chat.",
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


def _mask(number: str) -> str:
    """Last four digits only. Bullets, not asterisks, so the mask cannot collide with Markdown bold in replies."""
    return "••••" + str(number)[-4:]


def _parse_date(text) -> date | None:
    if isinstance(text, date):
        return text
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(str(text).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _digits(text) -> str:
    return "".join(ch for ch in str(text) if ch.isdigit())


def _customer(conn, customer_id: int) -> dict:
    return one(conn, "SELECT * FROM customers WHERE customer_id = ?", (customer_id,))


def _loan_list(conn, customer_id: int) -> list[dict]:
    return [{"loan_id": l["loan_id"], "loan_number_masked": _mask(l["loan_number"]), "loan_type": l["loan_type"],
             "property_address": l["property_address"]}
            for l in rows(conn, "SELECT * FROM loans WHERE customer_id = ? ORDER BY loan_id", (customer_id,))]


def _own_loan(session: Session, conn, loan_id) -> dict | None:
    """The loan, if it belongs to the verified customer."""
    try:
        return one(conn, "SELECT * FROM loans WHERE loan_id = ? AND customer_id = ?",
                   (int(loan_id), session.verified_customer_id))
    except (TypeError, ValueError):
        return None


def _bank_on_file(session: Session, conn, bank_last4) -> dict | None:
    return one(conn, "SELECT * FROM bank_accounts WHERE customer_id = ? AND bank_last4 = ?",
               (session.verified_customer_id, _digits(bank_last4)[-4:]))


def _unpaid_fees(conn, loan_id: int) -> float:
    return round(one(conn, "SELECT COALESCE(SUM(amount), 0) AS t FROM fees_charged WHERE loan_id = ? AND status = 'unpaid'",
                     (loan_id,))["t"], 2)


def _monthly_payment(loan: dict) -> float:
    return round(loan["monthly_principal_interest"] + loan["monthly_escrow"], 2)


def _status(loan: dict) -> dict:
    """Where the loan stands today: current, inside the grace period, or past due."""
    due = date.fromisoformat(loan["next_due_date"])
    grace_ends = due + timedelta(days=GRACE_DAYS)
    installments_due = 0
    d = due
    while d <= TODAY:
        installments_due += 1
        d = add_months(d, 1)
    if due > TODAY:
        status = "current"
    elif TODAY <= grace_ends:
        status = "due_within_grace_period"
    else:
        status = "past_due"
    return {"status": status, "status_explanation": STATUS_TEXT[status], "next_due_date": loan["next_due_date"],
            "grace_period_ends_on": grace_ends.isoformat(), "days_past_due": max(0, (TODAY - due).days),
            "installments_due_now": installments_due}


def _payoff(conn, loan: dict, payoff_date: date) -> dict:
    accrued_from = add_months(date.fromisoformat(loan["next_due_date"]), -1)   # mortgage interest is paid in arrears
    days = max(0, (payoff_date - accrued_from).days)
    daily = loan["principal_balance"] * loan["interest_rate"] / 100 / 365
    interest = round(daily * days, 2)
    deferred = loan["deferred_amount"] or 0.0
    fees = _unpaid_fees(conn, loan["loan_id"])
    return {"principal_balance": loan["principal_balance"], "interest_from": accrued_from.isoformat(),
            "interest_days": days, "accrued_interest": interest, "per_diem_interest": round(daily, 2),
            "deferred_balance": deferred, "unpaid_fees": fees,
            "total_payoff": round(loan["principal_balance"] + interest + deferred + fees, 2)}


def requires_verification(fn):
    """Decorator: the tool returns SESSION_NOT_VERIFIED instead of running until verify_identity has succeeded."""
    @functools.wraps(fn)
    def wrapper(session: Session, conn, *args, **kwargs):
        if not session.verified:
            return _err("SESSION_NOT_VERIFIED",
                        "Identity must be verified before any loan information can be shared. Call verify_identity first.")
        return fn(session, conn, *args, **kwargs)
    wrapper.requires_verification = True
    return wrapper


# ----------------------------------------------------------------------------- identity

_INVALID_LOCATOR = object()


def verify_identity(session: Session, conn, locator, date_of_birth=None, ssn_last4=None, property_zip=None) -> dict:
    """Verify the customer with two of three factors. Never reveals which factor failed."""
    if session.verified:
        cust = _customer(conn, session.verified_customer_id)
        return _ok(status="already_verified", customer_first_name=cust["first_name"], loans=_loan_list(conn, cust["customer_id"]))
    if session.locked:
        return _err("SESSION_LOCKED", "Verification is locked for this session after too many failed attempts. "
                    "Do not try again; offer to escalate to a human specialist.", status="locked")

    provided = {k: v for k, v in {"date_of_birth": date_of_birth, "ssn_last4": ssn_last4,
                                  "property_zip": property_zip}.items() if v not in (None, "")}
    if len(provided) < MIN_FACTORS:
        return _err("INSUFFICIENT_FACTORS", f"Verification needs at least {MIN_FACTORS} of: date of birth, last four "
                    "digits of the Social Security number, ZIP code of the property. Ask for what is missing.",
                    status="need_more_factors", factors_received=sorted(provided))

    customer = _locate_customer(conn, locator)
    if customer is _INVALID_LOCATOR:
        return _err("INVALID_LOCATOR", "The locator must be the email address on file, the 10-digit loan number, "
                    "or its last 4 digits.", status="need_locator")

    if customer is not None and all(_factor_matches(customer, k, v) for k, v in provided.items()):
        session.verified_customer_id = customer["customer_id"]
        return _ok(status="verified", customer_first_name=customer["first_name"],
                   loans=_loan_list(conn, customer["customer_id"]))

    session.verification_attempts += 1
    remaining = MAX_VERIFICATION_ATTEMPTS - session.verification_attempts
    if remaining <= 0:
        session.locked = True
        return _err("SESSION_LOCKED", "Verification failed and the session is now locked. Do not say which detail was "
                    "wrong. Offer to escalate to a human specialist who can verify the customer another way.",
                    status="locked", attempts_remaining=0)
    return _err("VERIFICATION_FAILED", "The details provided could not be verified. Do not say which detail was "
                f"wrong. The customer may try again ({remaining} attempt(s) remaining).",
                status="failed", attempts_remaining=remaining)


def _locate_customer(conn, locator):
    text = str(locator or "").strip().lower()
    if "@" in text:
        return one(conn, "SELECT * FROM customers WHERE lower(email) = ?", (text,))
    digits = _digits(text)
    if len(digits) == 10:
        loan = one(conn, "SELECT customer_id FROM loans WHERE loan_number = ?", (digits,))
    elif len(digits) == 4:
        loan = one(conn, "SELECT customer_id FROM loans WHERE substr(loan_number, -4) = ?", (digits,))
    else:
        return _INVALID_LOCATOR
    return _customer(conn, loan["customer_id"]) if loan else None


def _factor_matches(customer: dict, factor: str, value) -> bool:
    if factor == "date_of_birth":
        given = _parse_date(value)
        return given is not None and given.isoformat() == customer["date_of_birth"]
    if factor == "ssn_last4":
        digits = _digits(value)
        return len(digits) >= 4 and digits[-4:] == customer["ssn_last4"]
    if factor == "property_zip":
        return _digits(value)[:5] == customer["property_zip"]
    return False


# ----------------------------------------------------------------------------- read tools

@requires_verification
def get_loan_summary(session: Session, conn, loan_id=None) -> dict:
    """Balance, payment, due date, status and settings for the verified customer's loans (or one of them)."""
    if loan_id is not None:
        loan = _own_loan(session, conn, loan_id)
        loans = [loan] if loan else []
    else:
        loans = rows(conn, "SELECT * FROM loans WHERE customer_id = ? ORDER BY loan_id", (session.verified_customer_id,))
    if not loans:
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    out = []
    for l in loans:
        status = _status(l)
        fees = _unpaid_fees(conn, l["loan_id"])
        item = {
            "loan_id": l["loan_id"], "loan_number_masked": _mask(l["loan_number"]), "loan_type": l["loan_type"],
            "property_address": l["property_address"], "interest_rate": l["interest_rate"], "rate_type": l["rate_type"],
            "origination_date": l["origination_date"], "principal_balance": l["principal_balance"],
            "monthly_principal_interest": l["monthly_principal_interest"], "monthly_escrow": l["monthly_escrow"],
            "monthly_payment": _monthly_payment(l), **status, "unpaid_fees": fees,
            "amount_due_now": round(status["installments_due_now"] * _monthly_payment(l) + fees, 2),
            "escrow_balance": l["escrow_balance"],
            "autopay": {"enabled": bool(l["autopay_enabled"]),
                        "bank_account": _mask(l["autopay_bank_last4"]) if l["autopay_bank_last4"] else None},
        }
        if l["loan_type"] == "mortgage":
            item.update(original_amount=l["original_amount"], term_months=l["term_months"])
        else:
            draws = rows(conn, "SELECT drawn_on, amount, to_bank_last4, channel FROM heloc_draws WHERE loan_id = ? "
                               "ORDER BY drawn_on DESC LIMIT 3", (l["loan_id"],))
            for d in draws:
                d["to_bank_account"] = _mask(d.pop("to_bank_last4"))
            item.update(credit_limit=l["credit_limit"],
                        available_credit=round(l["credit_limit"] - l["principal_balance"], 2),
                        draw_period_ends_on=l["draw_period_ends_on"], payment_type="interest_only_during_draw_period",
                        recent_draws=draws)
        if status["status"] != "current":
            item["credit_reporting_note"] = CREDIT_REPORTING_NOTE
        if l["forbearance_start"]:
            item["forbearance"] = {"start": l["forbearance_start"], "end": l["forbearance_end"],
                                   "deferred_balance": l["deferred_amount"], "note": FORBEARANCE_NOTE}
        if l["special_handling"]:
            item["special_handling"] = {"type": l["special_handling"], "instruction": l["special_handling_note"]}
        change = one(conn, "SELECT * FROM escrow_analyses WHERE loan_id = ? AND effective_date > ? "
                           "ORDER BY analysis_date DESC LIMIT 1", (l["loan_id"], AS_OF))
        if change:
            new_payment = _escrow_figures(l, change)["new_monthly_payment"]
            item["upcoming_payment_change"] = {"effective_date": change["effective_date"], "new_monthly_payment": new_payment,
                                               "detail": "Call get_escrow_analysis for the reasons and options."}
        out.append(item)
    banks = rows(conn, "SELECT bank_last4, account_type, linked_on FROM bank_accounts WHERE customer_id = ? "
                       "ORDER BY linked_on", (session.verified_customer_id,))
    return _ok(as_of=AS_OF, customer_first_name=_customer(conn, session.verified_customer_id)["first_name"], loans=out,
               bank_accounts_on_file=[{"bank_account": _mask(b["bank_last4"]), "account_type": b["account_type"],
                                       "linked_on": b["linked_on"]} for b in banks])


@requires_verification
def get_payment_history(session: Session, conn, loan_id, limit=6) -> dict:
    """Recent payments on one loan, including returned and scheduled ones, with how each was applied."""
    loan = _own_loan(session, conn, loan_id)
    if not loan:
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    try:
        limit = max(1, min(int(limit), 24))
    except (TypeError, ValueError):
        return _err("BAD_ARGUMENTS", "limit must be a whole number from 1 to 24.")
    payments = rows(conn, "SELECT * FROM payments WHERE loan_id = ? ORDER BY received_on DESC, payment_id DESC LIMIT ?",
                    (loan["loan_id"], limit))
    for p in payments:
        p["status_explanation"] = PAYMENT_STATUS_TEXT[p["status"]]
        p["bank_account"] = _mask(p.pop("bank_last4")) if p["bank_last4"] else None
        p.pop("loan_id")
    return _ok(as_of=AS_OF, loan_id=loan["loan_id"], loan_number_masked=_mask(loan["loan_number"]), payments=payments,
               note=None if payments else "No payments on this loan yet.")


def _escrow_figures(loan: dict, a: dict) -> dict:
    prior_escrow = round((a["prior_annual_tax"] + a["prior_annual_insurance"]) / 12, 2)
    base_escrow = round((a["new_annual_tax"] + a["new_annual_insurance"]) / 12, 2)
    shortage_monthly = round(a["shortage_amount"] / a["spread_months"], 2) if a["spread_months"] else 0.0
    new_escrow = round(base_escrow + shortage_monthly, 2)
    pi = loan["monthly_principal_interest"]
    return {"prior_monthly_escrow": prior_escrow, "new_monthly_escrow_before_shortage": base_escrow,
            "shortage_monthly": shortage_monthly, "new_monthly_escrow": new_escrow,
            "prior_monthly_payment": round(pi + prior_escrow, 2), "new_monthly_payment": round(pi + new_escrow, 2),
            "monthly_change": round(new_escrow - prior_escrow, 2)}


@requires_verification
def get_escrow_analysis(session: Session, conn, loan_id) -> dict:
    """Why the monthly payment changed: property tax and insurance before and after, shortage or surplus, options."""
    loan = _own_loan(session, conn, loan_id)
    if not loan:
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    if loan["loan_type"] != "mortgage":
        return _err("NO_ESCROW", "This loan has no escrow account. Home equity lines do not collect taxes or insurance.")
    a = one(conn, "SELECT * FROM escrow_analyses WHERE loan_id = ? AND analysis_date >= ? ORDER BY analysis_date DESC "
                  "LIMIT 1", (loan["loan_id"], add_months(TODAY, -12).isoformat()))
    if not a:
        return _ok(as_of=AS_OF, loan_id=loan["loan_id"], analysis=None,
                   note=f"No escrow analysis in the last 12 months. The current monthly escrow of "
                        f"${loan['monthly_escrow']:,.2f} covers property tax and homeowners insurance.")
    figures = _escrow_figures(loan, a)
    options = []
    if a["shortage_amount"]:
        options.append(f"The customer may pay the ${a['shortage_amount']:,.2f} shortage in one payment before "
                       f"{a['effective_date']}. The new monthly payment would then be "
                       f"${figures['new_monthly_payment'] - figures['shortage_monthly']:,.2f}.")
        options.append(f"Otherwise the shortage is spread over {a['spread_months']} months at "
                       f"${figures['shortage_monthly']:,.2f} per month, as shown.")
    if a["surplus_amount"]:
        options.append(f"The ${a['surplus_amount']:,.2f} surplus is refunded by check within 30 days of the analysis date.")
    return _ok(
        as_of=AS_OF, loan_id=loan["loan_id"], loan_number_masked=_mask(loan["loan_number"]),
        analysis_date=a["analysis_date"], effective_date=a["effective_date"],
        property_tax={"prior_annual": a["prior_annual_tax"], "new_annual": a["new_annual_tax"],
                      "change_annual": round(a["new_annual_tax"] - a["prior_annual_tax"], 2)},
        homeowners_insurance={"prior_annual": a["prior_annual_insurance"], "new_annual": a["new_annual_insurance"],
                              "change_annual": round(a["new_annual_insurance"] - a["prior_annual_insurance"], 2)},
        shortage_amount=a["shortage_amount"], surplus_amount=a["surplus_amount"], spread_months=a["spread_months"],
        monthly_principal_interest=loan["monthly_principal_interest"], **figures, options=options,
        required_disclosure=PAYMENT_CHANGE_DISCLOSURE.format(analysis_date=a["analysis_date"],
                                                             effective_date=a["effective_date"]),
    )


@requires_verification
def get_payoff_quote(session: Session, conn, loan_id, payoff_date=None) -> dict:
    """Estimated amount to pay the loan off on a date within the next 30 days, with the required disclosure."""
    loan = _own_loan(session, conn, loan_id)
    if not loan:
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    when = TODAY if payoff_date in (None, "") else _parse_date(payoff_date)
    if when is None or not TODAY <= when <= TODAY + timedelta(days=DATE_WINDOW_DAYS):
        return _err("INVALID_DATE", f"payoff_date must be a date from {AS_OF} to "
                    f"{(TODAY + timedelta(days=DATE_WINDOW_DAYS)).isoformat()}.")
    figures = _payoff(conn, loan, when)
    result = dict(as_of=AS_OF, loan_id=loan["loan_id"], loan_number_masked=_mask(loan["loan_number"]),
                  good_through=when.isoformat(), **figures, escrow_balance_refunded_after_payoff=loan["escrow_balance"],
                  required_disclosure=PAYOFF_DISCLOSURE.format(good_through=when.isoformat(),
                                                               per_diem=f"{figures['per_diem_interest']:,.2f}"))
    if loan["loan_type"] == "heloc":
        result["note"] = "Paying the balance to zero does not close the line. Closing it is a separate written request."
    return _ok(**result)


@requires_verification
def get_application_status(session: Session, conn, application_id=None) -> dict:
    """Home equity applications for the verified customer: stage, what is still needed, and the loan officer."""
    sql, params = "SELECT * FROM applications WHERE customer_id = ?", (session.verified_customer_id,)
    if application_id:
        sql, params = sql + " AND application_id = ?", params + (str(application_id).strip().upper(),)
    apps = rows(conn, sql + " ORDER BY submitted_on DESC", params)
    if application_id and not apps:
        return _err("APPLICATION_NOT_FOUND", "No application with that id belongs to the verified customer.")
    for a in apps:
        a["outstanding_items"] = json.loads(a["outstanding_items"])
        a["stage_explanation"] = STAGE_TEXT[a["stage"]]
        a["days_since_last_activity"] = (TODAY - date.fromisoformat(a["last_activity_on"])).days
        a.pop("customer_id")
    return _ok(as_of=AS_OF, applications=apps,
               note=("Only the licensed loan officer can discuss approval, rates, or terms." if apps
                     else "The customer has no applications on file."))


def get_fee_details(session: Session, conn, fee_type=None) -> dict:
    """The fee schedule (public, no verification needed) plus, once verified, fees charged in the last 12 months."""
    if fee_type:
        schedule = rows(conn, "SELECT * FROM fee_schedule WHERE fee_type = ?", (str(fee_type).strip().lower(),))
        if not schedule:
            return _err("UNKNOWN_FEE_TYPE", f"Unknown fee type. Known types: {', '.join(f[0] for f in FEE_SCHEDULE)}.")
    else:
        schedule = rows(conn, "SELECT * FROM fee_schedule ORDER BY fee_type")
    result = {"as_of": AS_OF, "fee_schedule": schedule}
    if session.verified:
        charges = rows(conn, """SELECT f.charged_on, f.fee_type, f.amount, f.status, f.note, l.loan_number
                                FROM fees_charged f JOIN loans l ON l.loan_id = f.loan_id
                                WHERE l.customer_id = ? AND f.charged_on >= ? ORDER BY f.charged_on DESC""",
                       (session.verified_customer_id, add_months(TODAY, -12).isoformat()))
        for c in charges:
            c["loan_number_masked"] = _mask(c.pop("loan_number"))
        result["charged_last_12_months"] = charges
        result["note"] = None if charges else "No fees were charged on the customer's loans in the last 12 months."
    else:
        result["charged_last_12_months"] = None
        result["note"] = "The fee schedule is public. Fees charged on the customer's loans require verification first."
    return _ok(**result)


# ----------------------------------------------------------------------------- write tools

@requires_verification
def make_payment(session: Session, conn, loan_id, amount, bank_last4, payment_date=None, confirmed=False) -> dict:
    """Pay toward a loan from a bank account already on file, today or on a date in the next 30 days."""
    loan = _own_loan(session, conn, loan_id)
    if not loan:
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    if loan["special_handling"] == "bankruptcy":
        return _err("BANKRUPTCY_ON_FILE", "Payments on this loan are handled by the bankruptcy team, not in chat. "
                    "Do not request payment. Offer a transfer to the bankruptcy team.")
    bank = _bank_on_file(session, conn, bank_last4)
    if not bank:
        return _err("BANK_ACCOUNT_NOT_ON_FILE", "Payments can only come from a bank account already on file. New bank "
                    "accounts cannot be added in chat; never ask for a full account number.")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return _err("INVALID_AMOUNT", "amount must be a number of dollars.")
    when = TODAY if payment_date in (None, "") else _parse_date(payment_date)
    if when is None or not TODAY <= when <= TODAY + timedelta(days=DATE_WINDOW_DAYS):
        return _err("INVALID_DATE", f"payment_date must be a date from {AS_OF} to "
                    f"{(TODAY + timedelta(days=DATE_WINDOW_DAYS)).isoformat()}.")
    payoff = _payoff(conn, loan, when)["total_payoff"]
    if not 0 < amount <= payoff:
        return _err("INVALID_AMOUNT", f"amount must be more than $0 and no more than the payoff amount of ${payoff:,.2f}.")
    summary = (f"Pay ${amount:,.2f} toward loan {_mask(loan['loan_number'])} from {bank['account_type']} account "
               f"{_mask(bank['bank_last4'])} on {when.isoformat()}")
    if confirmed is not True:
        return _err("CONFIRMATION_REQUIRED", "Read this back to the customer and ask them to confirm. Call again with "
                    "confirmed=true only after they clearly say yes.", summary_to_confirm=summary)
    count = one(conn, "SELECT COUNT(*) AS n FROM payments WHERE method = 'chat'")["n"]
    payment_id = f"PMT-C{count + 1:04d}"
    status = "pending" if when == TODAY else "scheduled"
    conn.execute("INSERT INTO payments (payment_id, loan_id, for_due_date, received_on, amount, method, bank_last4, "
                 "status, status_note) VALUES (?,?,?,?,?,?,?,?,?)",
                 (payment_id, loan["loan_id"], loan["next_due_date"], when.isoformat(), amount, "chat",
                  bank["bank_last4"], status, "Made in chat."))
    conn.commit()
    return _ok(payment_id=payment_id, status=status, confirmed_details=summary,
               applies_to_installment_due=loan["next_due_date"], message=PAYMENT_STATUS_TEXT[status])


@requires_verification
def set_autopay(session: Session, conn, loan_id, enabled, bank_last4=None, confirmed=False) -> dict:
    """Turn automatic monthly payments on (from a bank account on file) or off."""
    loan = _own_loan(session, conn, loan_id)
    if not loan:
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    if loan["special_handling"] == "bankruptcy":
        return _err("BANKRUPTCY_ON_FILE", "Autopay on this loan is handled by the bankruptcy team, not in chat.")
    if not isinstance(enabled, bool):
        return _err("BAD_ARGUMENTS", "enabled must be true or false.")
    bank = None
    if enabled:
        bank = _bank_on_file(session, conn, bank_last4 or "")
        if not bank:
            return _err("BANK_ACCOUNT_NOT_ON_FILE", "Autopay can only draft a bank account already on file. New bank "
                        "accounts cannot be added in chat.")
    new_bank = bank["bank_last4"] if bank else None
    if bool(loan["autopay_enabled"]) == enabled and loan["autopay_bank_last4"] == new_bank:
        return _ok(changed=False, message="No change was needed; that is already the setting.")
    summary = (f"Turn on autopay for loan {_mask(loan['loan_number'])} from {bank['account_type']} account "
               f"{_mask(new_bank)}, drafting ${_monthly_payment(loan):,.2f} on each due date" if enabled
               else f"Turn off autopay for loan {_mask(loan['loan_number'])}")
    if confirmed is not True:
        return _err("CONFIRMATION_REQUIRED", "Read this back to the customer and ask them to confirm. Call again with "
                    "confirmed=true only after they clearly say yes.", summary_to_confirm=summary)
    conn.execute("UPDATE loans SET autopay_enabled = ?, autopay_bank_last4 = ?, autopay_updated_on = ? WHERE loan_id = ?",
                 (int(enabled), new_bank, AS_OF, loan["loan_id"]))
    conn.commit()
    note = None
    if enabled and _status(loan)["installments_due_now"]:
        note = "Autopay covers future installments only. Amounts already due are not drafted automatically."
    return _ok(changed=True, confirmed_details=summary,
               first_draft_on=loan["next_due_date"] if enabled and loan["next_due_date"] > AS_OF else None, note=note)


@requires_verification
def create_handoff(session: Session, conn, team, reason_category, summary, customer_goal, open_questions=None,
                   urgency="normal", loan_id=None) -> dict:
    """Hand the customer to a licensed loan officer or a hardship specialist, with a complete structured summary."""
    if team not in HANDOFF_TEAMS:
        return _err("INVALID_TEAM", f"team must be one of {', '.join(HANDOFF_TEAMS)}.")
    if reason_category not in HANDOFF_TEAMS[team]:
        return _err("INVALID_CATEGORY", f"For {team}, reason_category must be one of {', '.join(HANDOFF_TEAMS[team])}.")
    if urgency not in URGENCY_LEVELS:
        return _err("INVALID_URGENCY", f"urgency must be one of {', '.join(URGENCY_LEVELS)}.")
    if not isinstance(summary, str) or len(summary.strip()) < 40:
        return _err("INCOMPLETE_SUMMARY", "summary must describe the situation and what was discussed, in at least "
                    "40 characters.")
    if not isinstance(customer_goal, str) or len(customer_goal.strip()) < 10:
        return _err("INCOMPLETE_SUMMARY", "customer_goal must state what the customer wants, in at least 10 characters.")
    if isinstance(open_questions, str):
        open_questions = [open_questions]
    if open_questions is None:
        open_questions = []
    if not isinstance(open_questions, list) or not all(isinstance(q, str) for q in open_questions):
        return _err("INCOMPLETE_SUMMARY", "open_questions must be a list of strings (it may be empty).")
    if loan_id is not None and not _own_loan(session, conn, loan_id):
        return _err("LOAN_NOT_FOUND", "No loan with that id belongs to the verified customer.")
    count = one(conn, "SELECT COUNT(*) AS n FROM handoffs")["n"]
    handoff_id = f"HO-{count + 1:04d}"
    conn.execute("""INSERT INTO handoffs (handoff_id, customer_id, loan_id, created_on, team, reason_category, summary,
                    customer_goal, open_questions, urgency) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (handoff_id, session.verified_customer_id, loan_id, AS_OF, team, reason_category, summary.strip(),
                  customer_goal.strip(), json.dumps(open_questions), urgency))
    conn.commit()
    return _ok(handoff_id=handoff_id, team=team, status="open", message=HANDOFF_MESSAGES[team],
               recorded={"reason_category": reason_category, "summary": summary.strip(),
                         "customer_goal": customer_goal.strip(), "open_questions": open_questions, "urgency": urgency})


def escalate_to_human(session: Session, conn, reason_category, details, urgency="normal") -> dict:
    """Hand the conversation to a human specialist. Allowed before verification (fraud, lockouts, a deceased borrower)."""
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
    "get_loan_summary": get_loan_summary,
    "get_payment_history": get_payment_history,
    "get_escrow_analysis": get_escrow_analysis,
    "get_payoff_quote": get_payoff_quote,
    "get_application_status": get_application_status,
    "get_fee_details": get_fee_details,
    "make_payment": make_payment,
    "set_autopay": set_autopay,
    "create_handoff": create_handoff,
    "escalate_to_human": escalate_to_human,
}

# Policy rule IDs (policy.md) that each tool enforces in code. Shown in the demo UI side panel.
TOOL_RULES = {
    "verify_identity": ["R1", "R2"],
    "get_loan_summary": ["R1", "R11"],
    "get_payment_history": ["R1", "R11"],
    "get_escrow_analysis": ["R1", "R6"],
    "get_payoff_quote": ["R1", "R6"],
    "get_application_status": ["R1", "R4"],
    "get_fee_details": ["R1"],
    "make_payment": ["R1", "R9", "R11"],
    "set_autopay": ["R1", "R9", "R11"],
    "create_handoff": ["R7"],
    "escalate_to_human": ["R8"],
}


def _schema(properties: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_LOAN_ID = {"type": "integer", "description": "loan_id as returned by verify_identity or get_loan_summary."}
_CONFIRMED = {"type": "boolean", "description": "True only after the customer clearly said yes to the read-back."}

# What the model sees: name, description, JSON Schema. Descriptions carry the rules the model needs at call time.
TOOL_SPECS = [
    {"name": "verify_identity",
     "description": "Verify the customer before sharing any loan information. Needs the locator (email on file, "
                    "10-digit loan number, or its last 4 digits) plus at least two of: date of birth, last 4 digits of "
                    "the Social Security number, ZIP code of the property. Never ask for a full Social Security number. "
                    "Never say which detail failed. After 3 failures the session locks; then offer escalation.",
     "input_schema": _schema({
         "locator": {"type": "string", "description": "Email on file, loan number, or its last 4 digits."},
         "date_of_birth": {"type": "string", "description": "Any common date format."},
         "ssn_last4": {"type": "string", "description": "Last 4 digits of the Social Security number only."},
         "property_zip": {"type": "string", "description": "ZIP code of the property."},
     }, ("locator",))},
    {"name": "get_loan_summary",
     "description": "Balance, monthly payment, next due date, status (current, within grace period, past due), amount "
                    "due now, autopay, escrow balance, bank accounts on file, and for home equity lines the credit "
                    "limit, available credit and recent draws. Pass loan_id to narrow to one loan.",
     "input_schema": _schema({"loan_id": _LOAN_ID})},
    {"name": "get_payment_history",
     "description": "Recent payments on one loan, newest first, including returned and scheduled payments and how "
                    "each was applied to principal, interest and escrow.",
     "input_schema": _schema({"loan_id": _LOAN_ID, "limit": {"type": "integer", "description": "1 to 24, default 6."}},
                             ("loan_id",))},
    {"name": "get_escrow_analysis",
     "description": "Why a mortgage payment changed: property tax and insurance before and after, any shortage or "
                    "surplus, the old and new payment, the customer's options, and the disclosure to include.",
     "input_schema": _schema({"loan_id": _LOAN_ID}, ("loan_id",))},
    {"name": "get_payoff_quote",
     "description": "Estimated payoff amount for a date from today up to 30 days ahead: principal, accrued interest, "
                    "per-day interest, deferred balance, unpaid fees, total, and the disclosure to include.",
     "input_schema": _schema({"loan_id": _LOAN_ID,
                              "payoff_date": {"type": "string", "description": "Date to pay off. Defaults to today."}},
                             ("loan_id",))},
    {"name": "get_application_status",
     "description": "The verified customer's home equity line applications: stage, what the applicant still needs to "
                    "provide, the assigned loan officer, and any closing date. Never predict approval.",
     "input_schema": _schema({"application_id": {"type": "string", "description": "For example APP-58210."}})},
    {"name": "get_fee_details",
     "description": "The fee schedule (public, no verification needed) and, once verified, fees charged on the "
                    "customer's loans in the last 12 months with their status.",
     "input_schema": _schema({"fee_type": {"type": "string", "enum": [f[0] for f in FEE_SCHEDULE]}})},
    {"name": "make_payment",
     "description": "Pay toward a loan from a bank account already on file, today or on a date up to 30 days ahead. "
                    "First call without confirmed to get the exact read-back, read it to the customer, and call again "
                    "with confirmed=true only after they say yes. Blocked on loans with bankruptcy handling.",
     "input_schema": _schema({"loan_id": _LOAN_ID, "amount": {"type": "number"},
                              "bank_last4": {"type": "string", "description": "Last 4 digits of a bank account on file."},
                              "payment_date": {"type": "string", "description": "Defaults to today."},
                              "confirmed": _CONFIRMED}, ("loan_id", "amount", "bank_last4"))},
    {"name": "set_autopay",
     "description": "Turn automatic monthly payments on (enabled=true, from a bank account on file) or off. First call "
                    "without confirmed to get the read-back; call again with confirmed=true only after the customer "
                    "says yes.",
     "input_schema": _schema({"loan_id": _LOAN_ID, "enabled": {"type": "boolean"},
                              "bank_last4": {"type": "string", "description": "Required when enabling."},
                              "confirmed": _CONFIRMED}, ("loan_id", "enabled"))},
    {"name": "create_handoff",
     "description": "Hand the customer to a person who can help: team=loan_officer for refinancing, borrowing more, "
                    "rates, terms or application help (licensed, can discuss options); team=hardship_specialist when "
                    "the customer cannot afford payments. Create it only after the customer agrees. Requires a "
                    "complete, factual summary with no advice in it.",
     "input_schema": _schema({
         "team": {"type": "string", "enum": list(HANDOFF_TEAMS)},
         "reason_category": {"type": "string",
                             "enum": sorted({c for cats in HANDOFF_TEAMS.values() for c in cats})},
         "summary": {"type": "string", "description": "Factual summary: the situation, figures discussed, what was asked."},
         "customer_goal": {"type": "string", "description": "What the customer wants to achieve, in their terms."},
         "open_questions": {"type": "array", "items": {"type": "string"}},
         "urgency": {"type": "string", "enum": list(URGENCY_LEVELS)},
         "loan_id": _LOAN_ID,
     }, ("team", "reason_category", "summary", "customer_goal"))},
    {"name": "escalate_to_human",
     "description": "Transfer to a human specialist for suspected fraud, account security, failed verification, a "
                    "complaint, a disputed charge or payment, bankruptcy, a servicemember asking about military "
                    "protections, a deceased borrower, a distressed customer, a request for a human, or anything out "
                    "of scope. Works before verification.",
     "input_schema": _schema({
         "reason_category": {"type": "string", "enum": list(ESCALATION_CATEGORIES)},
         "details": {"type": "string", "description": "What happened and what the customer needs."},
         "urgency": {"type": "string", "enum": list(URGENCY_LEVELS)},
     }, ("reason_category", "details"))},
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
