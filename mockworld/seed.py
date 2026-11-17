"""Deterministic seed for the mock lending world. The company name is set in config.BRAND.

All people, loans, addresses and loan officers are fictional. Each customer is hand-written so scenario files can
point at a known situation: a payment going up because of escrow, two missed payments, a stalled home equity
application, an unrecognized home equity draw. Loan figures come from the standard amortization formula, so the
payment, balance, and interest and principal split the agent quotes are internally consistent.

    python -m mockworld.seed          # rebuild data/lender.db
"""
import hashlib
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from config import AS_OF, DB_PATH

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()
TODAY = date.fromisoformat(AS_OF)
GRACE_DAYS = 15                 # a payment counts as on time until the end of the 15th day after the due date
LATE_FEE_PCT = 4.0              # of the overdue principal and interest
MANUAL_PAY_DELAY_DAYS = 3       # customers without autopay pay a few days after the due date

# customer_id, first, last, email, phone_last4, date_of_birth, ssn_last4, property_zip, customer_since
CUSTOMERS = [
    (1, "Priya", "Raman", "priya.raman@example.com", "0417", "1984-03-12", "3816", "80302", "2021-05-14"),
    (2, "Marcus", "Oyelaran", "marcus.oyelaran@example.com", "2290", "1979-08-23", "5527", "97214", "2020-02-03"),
    (3, "Elena", "Vasquez-Torres", "elena.vt@example.com", "7718", "1991-11-02", "2068", "78704", "2022-09-19"),
    (4, "Daniel", "Whitcombe", "dan.whitcombe@example.com", "5056", "1988-06-30", "7392", "85016", "2023-01-11"),
    (5, "Aisha", "Bello", "aisha.bello@example.com", "3384", "1986-01-17", "4458", "28205", "2019-10-08"),
    (6, "Tomasz", "Nowicki", "t.nowicki@example.com", "9142", "1972-04-05", "6203", "60618", "2018-07-22"),
    (7, "Hannah", "Lindqvist", "hannah.lindqvist@example.com", "6673", "1990-09-14", "8814", "55408", "2020-11-30"),
    (8, "Kenji", "Watanabe", "kenji.watanabe@example.com", "4425", "1983-12-01", "1290", "98103", "2016-12-15"),
    (9, "Grace", "Achterberg", "grace.achterberg@example.com", "8809", "1954-02-27", "3047", "04101", "2015-06-05"),
    (10, "Samuel", "Okafor", "samuel.okafor@example.com", "1137", "1975-07-19", "5961", "30307", "2019-04-16"),
    (11, "Leila", "Haddad", "leila.haddad@example.com", "2951", "1993-05-08", "7735", "92103", "2024-02-20"),
    (12, "Victor", "Castellanos", "victor.castellanos@example.com", "6698", "1968-10-11", "2281", "33133", "2016-12-01"),
    (13, "Mei-Ling", "Chao", "meiling.chao@example.com", "7704", "1989-03-29", "9408", "94110", "2021-08-12"),
    (14, "Robert", "Kaminski", "robert.kaminski@example.com", "3310", "1981-01-25", "4672", "43215", "2022-04-27"),
    (15, "Fatima", "Al-Sayed", "fatima.alsayed@example.com", "4476", "1977-09-03", "6619", "19103", "2019-01-14"),
    (16, "Oliver", "Brennan", "oliver.brennan@example.com", "5583", "1995-12-19", "1842", "80206", "2025-06-02"),
    (17, "Sofia", "Marchetti", "sofia.marchetti@example.com", "9920", "1987-07-07", "3355", "02139", "2023-10-05"),
    (18, "Jamal", "Thompson", "jamal.thompson@example.com", "6061", "1992-02-14", "8126", "37203", "2022-11-08"),
    (19, "Ingrid", "Sorensen", "ingrid.sorensen@example.com", "2287", "1965-06-21", "5049", "53703", "2015-09-30"),
    (20, "Noah", "Feldman", "noah.feldman@example.com", "8175", "1998-04-09", "9520", "97035", "2026-10-09"),
]

# customer_id, bank_last4, account_type, linked_on
BANK_ACCOUNTS = [
    (1, "4471", "checking", "2021-05-14"), (2, "3302", "checking", "2020-02-03"), (3, "8821", "checking", "2022-09-19"),
    (4, "6610", "checking", "2023-01-11"), (5, "1947", "checking", "2019-10-08"), (6, "5518", "checking", "2018-07-22"),
    (7, "7730", "checking", "2020-11-30"), (7, "1185", "savings", "2023-05-02"), (8, "2064", "checking", "2016-12-15"),
    (9, "9152", "checking", "2015-06-05"), (10, "4408", "checking", "2019-04-16"), (11, "3376", "checking", "2024-02-20"),
    (12, "7045", "checking", "2016-12-01"), (13, "8863", "checking", "2021-08-12"), (14, "5529", "checking", "2022-04-27"),
    (15, "2210", "checking", "2019-01-14"),
    (15, "9930", "checking", "2026-11-02"),   # linked online the day before an unrecognized draw (fraud scenario)
    (16, "6691", "checking", "2025-06-02"), (17, "1138", "checking", "2023-10-05"), (18, "4452", "checking", "2022-11-08"),
    (19, "7719", "checking", "2015-09-30"), (20, "3094", "checking", "2026-10-09"),
]

# loan_id, customer_id, loan_number, property_address, amount, rate, term_months, origination, first_payment,
# annual_tax, annual_insurance, autopay_bank_last4 (None = pays manually)
MORTGAGES = [
    (101, 1, "7100011827", "1418 Alder Crest Lane, Ashgrove, CO 80302", 380000, 3.125, 360, "2021-05-14", "2021-07-01", 4800, 1620, "4471"),
    (102, 2, "7100022915", "77 Larchmont Terrace, Harwick, OR 97214", 310000, 3.5, 360, "2020-02-03", "2020-04-01", 3900, 1380, None),
    (103, 3, "7100033406", "2209 Pecan Hollow Drive, Bexley, TX 78704", 295000, 5.75, 360, "2022-09-19", "2022-11-01", 6900, 2400, "8821"),
    (104, 4, "7100044198", "3150 Saguaro Bend, Mesa Verde Hills, AZ 85016", 265000, 6.5, 360, "2023-01-11", "2023-03-01", 2100, 1500, None),
    (105, 5, "7100055731", "915 Dogwood Row, Kestrel Park, NC 28205", 240000, 3.75, 360, "2019-10-08", "2019-12-01", 2700, 1350, "1947"),
    (106, 6, "7100066342", "4426 Juniper Court, Northbrook Glen, IL 60618", 210000, 4.25, 360, "2018-07-22", "2018-09-01", 5200, 1450, None),
    (107, 7, "7100077563", "3801 Linden Hills Way, Lakeshore Crossing, MN 55408", 330000, 2.875, 360, "2020-11-30", "2021-01-01", 4300, 1600, None),
    (109, 9, "7100090618", "26 Harborview Lane, Port Ellery, ME 04101", 180000, 3.25, 180, "2015-06-05", "2015-08-01", 3100, 1250, "9152"),
    (110, 10, "7100101329", "1840 Magnolia Bend, Druid Crossing, GA 30307", 250000, 4.0, 360, "2019-04-16", "2019-06-01", 3300, 1700, None),
    (111, 11, "7100112750", "4110 Mesa Vista Street, Coronado Heights, CA 92103", 345000, 6.875, 360, "2024-02-20", "2024-04-01", 4300, 1300, "3376"),
    (112, 12, "7100123461", "3277 Banyan Grove Road, Coral Vista, FL 33133", 275000, 3.625, 360, "2016-12-01", "2017-02-01", 5100, 4200, None),
    (113, 13, "7100134172", "2150 Valencia Hill Road, Mission Bluff, CA 94110", 360000, 2.99, 360, "2021-08-12", "2021-10-01", 4900, 1500, "8863"),
    (115, 14, "7100145694", "68 Scioto Park Lane, Brindle Falls, OH 43215", 300000, 5.25, 360, "2022-04-27", "2022-06-01", 4100, 1400, None),
    (118, 17, "7100178527", "39 Brattle Green, Cambria Square, MA 02139", 320000, 7.125, 360, "2023-10-05", "2023-12-01", 5600, 1450, "1138"),
    (119, 18, "7100189238", "1206 Cumberland Rise, Belmont Ridge, TN 37203", 280000, 6.25, 360, "2022-11-08", "2023-01-01", 2600, 1700, "4452"),
    (120, 19, "7100190949", "818 Lakeside Prairie Drive, Monona Point, WI 53703", 200000, 3.875, 360, "2015-09-30", "2015-11-01", 4400, 1100, "7719"),
    (121, 20, "7100201560", "4521 Sunfield Loop, Lake Oswin, OR 97035", 410000, 6.375, 360, "2026-10-09", "2026-12-01", 4800, 1500, None),
]

# Home equity lines of credit, interest-only during the draw period.
# loan_id, customer_id, loan_number, property_address, credit_limit, balance, variable rate, origination,
# draw_period_ends_on, autopay_bank_last4
HELOCS = [
    (108, 8, "7100088284", "512 Fremont Ridge Avenue, Cedar Hollow, WA 98103", 100000, 42500, 8.25, "2016-12-15", "2026-12-31", "2064"),
    (114, 13, "7100134983", "2150 Valencia Hill Road, Mission Bluff, CA 94110", 50000, 18000, 8.5, "2023-03-01", "2033-03-01", "8863"),
    (116, 15, "7100156205", "1902 Spruce Commons, Rittenhouse Mews, PA 19103", 75000, 9500, 8.0, "2024-05-20", "2034-05-20", "2210"),
    (117, 16, "7100167816", "745 Marigold Mews, Highline Park, CO 80206", 40000, 12000, 8.75, "2025-06-02", "2035-06-02", "6691"),
]

# Situations that scenarios rely on.
MISSED_INSTALLMENTS = {104: 2}                          # Daniel: October and November unpaid
FORBEARANCE = {115: ("2026-08-01", "2026-10-31", 3)}    # Robert: 3 installments deferred; November not yet paid
EXTRA_PRINCIPAL = {120: (200.0, "2024-01-01")}          # Ingrid pays $200 extra principal each month since 2024
SPECIAL_HANDLING = {112: ("bankruptcy", "A Chapter 13 bankruptcy case filed on 2026-09-15 is on file. Do not request "
                          "payment, discuss collection, or schedule payments in chat. Answer factual questions and offer "
                          "a transfer to the bankruptcy team.")}
ESCROW_BALANCE_OVERRIDE = {101: 610.00}                 # Priya's escrow account ran short after tax and insurance rose

# loan_id, analysis_date, effective_date, prior_tax, new_tax, prior_insurance, new_insurance, shortage, surplus, spread_months
ESCROW_ANALYSES = [
    (101, "2026-10-15", "2026-12-01", 4800, 5520, 1620, 2184, 1128.00, 0.0, 12),
    (113, "2026-10-15", "2026-12-01", 4900, 4660, 1500, 1500, 0.0, 214.00, 0),
]

# draw_id, loan_id, drawn_on, amount, to_bank_last4, channel
HELOC_DRAWS = [
    ("DR-7311", 108, "2026-03-10", 12000.00, "2064", "online"),
    ("DR-6802", 114, "2025-04-12", 18000.00, "8863", "online"),
    ("DR-7590", 116, "2026-11-03", 9500.00, "9930", "online"),   # the customer will not recognize this one
    ("DR-6955", 117, "2025-07-08", 12000.00, "6691", "online"),
]

# application_id, customer_id, product, property_address, requested_amount, submitted_on, stage, outstanding_items,
# loan_officer, last_activity_on, estimated_closing_on, note
APPLICATIONS = [
    ("APP-58210", 3, "heloc", "2209 Pecan Hollow Drive, Bexley, TX 78704", 60000, "2026-10-20", "conditions_outstanding",
     ["Upload your two most recent pay stubs", "Link the bank account you want draws sent to",
      "Share your homeowners insurance agent's contact details"],
     "Dana Whitfield", "2026-10-23", None, "No activity from the applicant since 2026-10-23."),
    ("APP-58233", 18, "heloc", "1206 Cumberland Rise, Belmont Ridge, TN 37203", 35000, "2026-10-02",
     "approved_closing_scheduled", [], "Rosa Delgado", "2026-11-04", "2026-11-13",
     "Closing date is tentative until final verification of employment is complete."),
]

# fee_type, display_name, amount, unit, description, waiver
FEE_SCHEDULE = [
    ("late_fee", "Late fee", LATE_FEE_PCT, "pct_of_principal_and_interest",
     f"Charged when an installment is not received by the end of the {GRACE_DAYS}-day grace period after the due date. "
     f"{LATE_FEE_PCT:g}% of the overdue principal and interest.", None),
    ("returned_payment_fee", "Returned payment fee", 25.0, "per_item",
     "Charged when a payment is returned unpaid by the customer's bank, for example for insufficient funds.", None),
    ("payment_processing_fee", "Paying online, by phone, in chat, or by autopay", 0.0, "per_item",
     "There is no fee for any way of making a payment.", None),
    ("payoff_statement_fee", "Payoff statement", 0.0, "per_item", "Payoff statements are free.", None),
    ("heloc_annual_fee", "Home equity line annual fee", 50.0, "per_year",
     "Charged on each anniversary of a home equity line of credit during the draw period.",
     "Waived for the first anniversary."),
]

# loan_id, charged_on, fee_type, amount, status, note
FEES_CHARGED = [
    (104, "2026-10-16", "late_fee", None, "unpaid", "The October installment was not received by the end of the grace period."),
    (106, "2026-10-16", "late_fee", None, "unpaid",
     "The October installment was not received by the end of the grace period. It was received on 2026-10-19."),
    (110, "2026-10-06", "returned_payment_fee", 25.0, "unpaid",
     "The payment received on 2026-10-01 was returned unpaid by the bank: insufficient funds."),
    (108, "2025-12-15", "heloc_annual_fee", 50.0, "paid", "Annual fee on the line's anniversary."),
    (114, "2026-03-01", "heloc_annual_fee", 50.0, "paid", "Annual fee on the line's anniversary."),
    (116, "2026-05-20", "heloc_annual_fee", 50.0, "paid", "Annual fee on the line's anniversary."),
]


# ----------------------------------------------------------------------------- loan math

def add_months(d: date, n: int) -> date:
    month = d.month - 1 + n
    return date(d.year + month // 12, month % 12 + 1, 1)


def next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    r = annual_rate / 100 / 12
    return round(principal * r / (1 - (1 + r) ** -months), 2)


def due_dates(first_payment: str) -> list[date]:
    """Every installment due date from the first payment through today."""
    out, d = [], date.fromisoformat(first_payment)
    while d <= TODAY:
        out.append(d)
        d = add_months(d, 1)
    return out


def amortize(principal: float, annual_rate: float, payment: float, dues: list[date], extra=None):
    """Apply each paid installment in order. Returns one (due_date, interest, principal_paid, balance_after) per due."""
    r, balance, rows = annual_rate / 100 / 12, principal, []
    for d in dues:
        interest = round(balance * r, 2)
        principal_paid = round(payment - interest, 2)
        if extra and d >= date.fromisoformat(extra[1]):
            principal_paid = round(principal_paid + extra[0], 2)
        balance = round(balance - principal_paid, 2)
        rows.append((d, interest, principal_paid, balance))
    return rows


# ----------------------------------------------------------------------------- build rows

def _mortgage_rows(m) -> tuple[tuple, list[tuple]]:
    loan_id, cust, number, address, amount, rate, term, orig, first, tax, ins, autopay = m
    pi = monthly_payment(amount, rate, term)
    escrow = round((tax + ins) / 12, 2)
    dues = due_dates(first)
    unpaid = MISSED_INSTALLMENTS.get(loan_id, 0)
    deferred = 0
    if loan_id in FORBEARANCE:
        deferred = FORBEARANCE[loan_id][2]
        unpaid = 1                                   # the installment after forbearance is still inside its grace period
    paid_dues = dues[: len(dues) - unpaid - deferred]
    schedule = amortize(amount, rate, pi, paid_dues, EXTRA_PRINCIPAL.get(loan_id))
    balance = schedule[-1][3] if schedule else float(amount)
    first_unpaid = len(paid_dues) + deferred           # deferred installments move to the deferred balance
    next_due = dues[first_unpaid] if first_unpaid < len(dues) else add_months(date.fromisoformat(first), len(dues))
    special = SPECIAL_HANDLING.get(loan_id, (None, None))
    forbearance = FORBEARANCE.get(loan_id)
    loan = (loan_id, cust, number, "mortgage", address, rate, "fixed", amount, orig, term, first, balance, pi, escrow,
            ESCROW_BALANCE_OVERRIDE.get(loan_id, round(escrow * 3, 2)), next_due.isoformat(), int(autopay is not None),
            autopay, None, None, None,
            forbearance[0] if forbearance else None, forbearance[1] if forbearance else None,
            round(deferred * (pi + escrow), 2) if deferred else None, special[0], special[1])
    payments = []
    for d, interest, principal_paid, _ in schedule[-6:]:
        received = next_business_day(d if autopay else d + timedelta(days=MANUAL_PAY_DELAY_DAYS))
        payments.append((f"PMT-{loan_id}-{d:%y%m}", loan_id, d.isoformat(), received.isoformat(),
                         round(interest + principal_paid + escrow, 2), principal_paid, interest, escrow,
                         "autopay" if autopay else "online", autopay or _first_bank(cust), "posted", None))
    return loan, payments


def _heloc_rows(h) -> tuple[tuple, list[tuple]]:
    loan_id, cust, number, address, limit, balance, rate, orig, draw_end, autopay = h
    interest_only = round(balance * rate / 100 / 12, 2)
    first = add_months(date.fromisoformat(orig), 2).isoformat()
    loan = (loan_id, cust, number, "heloc", address, rate, "variable", limit, orig, 360, first, float(balance),
            interest_only, 0.0, 0.0, add_months(TODAY, 1).isoformat(), int(autopay is not None), autopay, None,
            float(limit), draw_end, None, None, None, None, None)
    last_draw = max(dr[2] for dr in HELOC_DRAWS if dr[1] == loan_id)
    payments = []
    for d in due_dates(first)[-6:]:
        if d.isoformat() <= last_draw:
            continue                                  # nothing was owed before the most recent draw
        payments.append((f"PMT-{loan_id}-{d:%y%m}", loan_id, d.isoformat(), next_business_day(d).isoformat(),
                         interest_only, 0.0, interest_only, 0.0, "autopay", autopay, "posted", None))
    return loan, payments


def _first_bank(customer_id: int) -> str:
    return next(b[1] for b in BANK_ACCOUNTS if b[0] == customer_id)


# Hand-built changes to the generated payment history: payment_id -> fields to override.
PAYMENT_OVERRIDES = {
    # Tomasz: October installment arrived by bank bill pay after the grace period ended.
    "PMT-106-2610": {"received_on": "2026-10-19", "method": "bank_bill_pay",
                     "status_note": "Sent through the customer's bank bill pay service. Received after the grace period "
                                    "ended on 2026-10-16."},
    # Samuel: the October payment was returned (row added below), then paid again on 2026-10-09.
    "PMT-110-2610": {"received_on": "2026-10-09"},
}


def _special_payments(payments: list[tuple]) -> list[tuple]:
    """Apply PAYMENT_OVERRIDES and add the payments that are not part of a regular schedule."""
    cols = ["payment_id", "loan_id", "for_due_date", "received_on", "amount", "applied_principal", "applied_interest",
            "applied_escrow", "method", "bank_last4", "status", "status_note"]
    out = []
    for p in payments:
        row = dict(zip(cols, p))
        row.update(PAYMENT_OVERRIDES.get(row["payment_id"], {}))
        out.append(tuple(row[c] for c in cols))
    samuel = next(p for p in out if p[0] == "PMT-110-2610")
    out.append(("PMT-110-2610R", 110, "2026-10-01", "2026-10-01", samuel[4], None, None, None, "online", "4408",
                "returned", "Returned unpaid by the bank on 2026-10-06: insufficient funds. Nothing was applied."))
    # Oliver: a one-time extra payment he scheduled for next week.
    out.append(("PMT-117-SCHED", 117, "2026-12-01", "2026-11-13", 500.00, None, None, None, "online", "6691",
                "scheduled", "One-time extra payment toward principal, scheduled by the customer."))
    return out


def build(conn: sqlite3.Connection) -> None:
    """Create every table and load the seed into an open connection (file or in-memory)."""
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?)", CUSTOMERS)
    conn.executemany("INSERT INTO bank_accounts VALUES (?,?,?,?)", BANK_ACCOUNTS)
    conn.executemany("INSERT INTO fee_schedule VALUES (?,?,?,?,?,?)", FEE_SCHEDULE)
    loans, payments = [], []
    for m in MORTGAGES:
        loan, pays = _mortgage_rows(m)
        loans.append(loan)
        payments += pays
    for h in HELOCS:
        loan, pays = _heloc_rows(h)
        loans.append(loan)
        payments += pays
    conn.executemany("INSERT INTO loans VALUES (" + ",".join("?" * 26) + ")", sorted(loans))
    by_id = {row[0]: {"pi": row[12], "escrow": row[13]} for row in loans}
    conn.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", sorted(_special_payments(payments)))
    conn.executemany("INSERT INTO heloc_draws VALUES (?,?,?,?,?,?)", HELOC_DRAWS)
    fees = [(i, loan_id, on, kind, amount if amount is not None else round(by_id[loan_id]["pi"] * LATE_FEE_PCT / 100, 2),
             status, note) for i, (loan_id, on, kind, amount, status, note) in enumerate(FEES_CHARGED, start=1)]
    conn.executemany("INSERT INTO fees_charged VALUES (?,?,?,?,?,?,?)", fees)
    conn.executemany("INSERT INTO escrow_analyses VALUES (?,?,?,?,?,?,?,?,?,?)", ESCROW_ANALYSES)
    conn.executemany("INSERT INTO applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     [(*a[:7], json.dumps(a[7]), *a[8:]) for a in APPLICATIONS])
    conn.commit()


def memory_db() -> sqlite3.Connection:
    """A fresh, fully seeded in-memory database. Evals give every conversation one of these."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    build(conn)
    return conn


def reset_db(path: str | Path = DB_PATH) -> Path:
    """Delete and rebuild the database file. Used by the demo UI reset button and the CLI."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        build(conn)
    finally:
        conn.close()
    return path


def fingerprint(conn: sqlite3.Connection) -> str:
    """Stable hash of every row in every table. Equal fingerprints mean identical database state."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    digest = hashlib.sha256()
    for t in tables:
        cols = ", ".join(r[1] for r in conn.execute(f"PRAGMA table_info({t})"))
        for row in conn.execute(f"SELECT * FROM {t} ORDER BY {cols}"):
            digest.update(json.dumps([t, list(row)], sort_keys=True, default=str).encode())
    return digest.hexdigest()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    reset_db(target)
    print(f"Rebuilt {target} as of {AS_OF}: {len(CUSTOMERS)} customers, {len(MORTGAGES) + len(HELOCS)} loans.")
