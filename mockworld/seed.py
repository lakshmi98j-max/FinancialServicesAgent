"""Deterministic seed for the Larkmoor Invest mock world.

All people, accounts, tickers and institutions are fictional. The data is hand-written so that
every scenario in evals/scenarios can point at a specific, known situation (a withdrawal on hold,
a portfolio down 12%, an unrecognized transfer). Rebuilding always produces byte-identical tables.

    python -m mockworld.seed          # rebuild data/larkmoor.db
"""
import hashlib
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from config import AS_OF, DB_PATH

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

BENCHMARKS = [
    ("us_equity", "Broad US Equity Index (fictional)",
     "Capitalization-weighted index of large and mid-cap US stocks. Fictional, for illustration."),
    ("us_bond", "US Aggregate Bond Index (fictional)",
     "Investment-grade US bond market index. Fictional, for illustration."),
    ("blend_60_40", "60/40 Blend (fictional)",
     "60% Broad US Equity Index, 40% US Aggregate Bond Index. Fictional, for illustration."),
]
BENCHMARK_RETURNS = {
    "us_equity":   {"1M": -1.5, "3M": -3.0, "YTD": 8.6, "1Y": 12.4},
    "us_bond":     {"1M": 0.5, "3M": 1.1, "YTD": 3.7, "1Y": 4.4},
    "blend_60_40": {"1M": -0.7, "3M": -1.4, "YTD": 6.6, "1Y": 9.2},
}

# symbol, name, asset_class, sector, price on AS_OF
SECURITIES = [
    ("HLVD", "Halverd Semiconductors", "equity", "Technology", 142.30),
    ("VLTR", "Veltro Cloud Software", "equity", "Technology", 88.75),
    ("QLBT", "Quillon Biotherapeutics", "equity", "Health Care", 31.40),
    ("TSCG", "Tessaro Consumer Goods", "equity", "Consumer Staples", 64.10),
    ("NGLF", "Northgale Financial Group", "equity", "Financials", 52.85),
    ("ORNE", "Orrin Energy Partners", "equity", "Energy", 47.20),
    ("BRMU", "Brightmoor Utilities", "equity", "Utilities", 71.05),
    ("CDRL", "Cedarline Property Trust", "reit", "Real Estate", 23.60),
    ("LMTM", "Larkmoor Total Market Index Fund", "equity_fund", "Diversified", 118.42),
    ("LMCB", "Larkmoor Core Bond Fund", "bond_fund", "Fixed Income", 96.15),
]
SECURITY_RETURNS = {
    "HLVD": {"1M": -9.8, "3M": -18.4, "YTD": 6.2, "1Y": 11.5},
    "VLTR": {"1M": -7.1, "3M": -14.2, "YTD": -3.8, "1Y": 2.4},
    "QLBT": {"1M": -12.5, "3M": -22.6, "YTD": -31.0, "1Y": -28.3},
    "TSCG": {"1M": 1.2, "3M": 3.1, "YTD": 7.9, "1Y": 9.4},
    "NGLF": {"1M": 0.8, "3M": 5.3, "YTD": 12.1, "1Y": 15.7},
    "ORNE": {"1M": 3.4, "3M": 8.2, "YTD": 4.6, "1Y": 1.9},
    "BRMU": {"1M": 1.9, "3M": 4.4, "YTD": 10.3, "1Y": 12.8},
    "CDRL": {"1M": -2.2, "3M": -6.1, "YTD": -4.5, "1Y": -1.7},
    "LMTM": {"1M": -1.6, "3M": -3.0, "YTD": 8.4, "1Y": 12.2},
    "LMCB": {"1M": 0.4, "3M": 1.2, "YTD": 3.9, "1Y": 4.6},
}
QUARTERLY_DIVIDEND_PER_SHARE = {"TSCG": 0.45, "NGLF": 0.38, "BRMU": 0.62, "CDRL": 0.31, "LMCB": 0.70, "LMTM": 0.42}
DIVIDEND_DATES = ("2026-03-31", "2026-06-30", "2026-09-30")

# customer_id, first, last, email, phone_last4, date_of_birth, bank_last4, security_word, since, e_delivery, risk
CUSTOMERS = [
    (1, "Priya", "Raman", "priya.raman@example.com", "0417", "1984-03-12", "4471", "saffron", "2021-05-14", 1, "growth"),
    (2, "Marcus", "Oyelaran", "marcus.oyelaran@example.com", "2290", "1979-08-23", "3302", "harbor", "2020-02-03", 1, "balanced"),
    (3, "Elena", "Vasquez-Torres", "elena.vt@example.com", "7718", "1991-11-02", "8821", "marigold", "2022-09-19", 1, "balanced"),
    (4, "Daniel", "Whitcombe", "dan.whitcombe@example.com", "5056", "1988-06-30", "6610", "compass", "2023-01-11", 1, "growth"),
    (5, "Aisha", "Bello", "aisha.bello@example.com", "3384", "1986-01-17", "1947", "indigo", "2019-10-08", 1, "balanced"),
    (6, "Tomasz", "Nowicki", "t.nowicki@example.com", "9142", "1972-04-05", "5518", "granite", "2018-07-22", 1, "conservative"),
    (7, "Hannah", "Lindqvist", "hannah.lindqvist@example.com", "6673", "1990-09-14", "7730", "juniper", "2020-11-30", 1, "balanced"),
    (8, "Kenji", "Watanabe", "kenji.watanabe@example.com", "4425", "1983-12-01", "2064", "lantern", "2021-03-09", 1, "growth"),
    (9, "Grace", "Achterberg", "grace.achterberg@example.com", "8809", "1954-02-27", "9152", "willow", "2017-06-05", 1, "conservative"),
    (10, "Samuel", "Okafor", "samuel.okafor@example.com", "1137", "1975-07-19", "4408", "cobalt", "2019-04-16", 0, "balanced"),
    (11, "Leila", "Haddad", "leila.haddad@example.com", "2951", "1993-05-08", "3376", "cedar", "2024-02-20", 1, "growth"),
    (12, "Victor", "Castellanos", "victor.castellanos@example.com", "6698", "1968-10-11", "7045", "meadow", "2016-12-01", 1, "balanced"),
    (13, "Mei-Ling", "Chao", "meiling.chao@example.com", "7704", "1989-03-29", "8863", "orchid", "2021-08-12", 1, "balanced"),
    (14, "Robert", "Kaminski", "robert.kaminski@example.com", "3310", "1981-01-25", "5529", "anvil", "2022-04-27", 1, "growth"),
    (15, "Fatima", "Al-Sayed", "fatima.alsayed@example.com", "4476", "1977-09-03", "2210", "pearl", "2019-01-14", 1, "conservative"),
    (16, "Oliver", "Brennan", "oliver.brennan@example.com", "5583", "1995-12-19", "6691", "falcon", "2023-06-02", 1, "growth"),
    (17, "Sofia", "Marchetti", "sofia.marchetti@example.com", "9920", "1987-07-07", "1138", "violet", "2020-05-21", 1, "balanced"),
    (18, "Jamal", "Thompson", "jamal.thompson@example.com", "6061", "1992-02-14", "4452", "summit", "2022-11-08", 1, "growth"),
    (19, "Ingrid", "Sorensen", "ingrid.sorensen@example.com", "2287", "1965-06-21", "7719", "birch", "2015-09-30", 1, "conservative"),
    (20, "Noah", "Feldman", "noah.feldman@example.com", "8175", "1998-04-09", "3094", "maple", "2026-09-15", 1, "balanced"),
]

# account_id, customer_id, account_number, type, managed, benchmark_id, cash_balance, settled_cash, opened_on
ACCOUNTS = [
    (101, 1, "40011827", "individual", 0, "us_equity", 1250.00, 1250.00, "2021-05-14"),
    (102, 2, "40022915", "individual", 0, "blend_60_40", 3200.00, 3200.00, "2020-02-03"),
    (103, 3, "40033406", "individual", 0, "blend_60_40", 2600.00, 2600.00, "2022-09-19"),
    (104, 4, "40044198", "individual", 0, "us_equity", 900.00, 900.00, "2023-01-11"),
    (105, 5, "40055731", "individual", 0, "blend_60_40", 1500.00, 1500.00, "2019-10-08"),
    (106, 6, "40066342", "individual", 0, "blend_60_40", 4100.00, 4100.00, "2018-07-22"),
    (107, 7, "40077563", "roth_ira", 1, "blend_60_40", 850.00, 850.00, "2020-11-30"),
    (108, 7, "40078284", "individual", 0, "us_equity", 700.00, 700.00, "2021-02-15"),
    (109, 8, "40089075", "individual", 0, "us_equity", 2200.00, 2200.00, "2021-03-09"),
    (110, 9, "40090618", "traditional_ira", 0, "blend_60_40", 15000.00, 15000.00, "2017-06-05"),
    (111, 10, "40101329", "individual", 0, "blend_60_40", 1100.00, 1100.00, "2019-04-16"),
    (112, 11, "40112750", "individual", 0, "us_equity", 4800.00, 3230.00, "2024-02-20"),
    (113, 12, "40123461", "individual", 0, "blend_60_40", 2900.00, 2900.00, "2016-12-01"),
    (114, 13, "40134172", "individual", 0, "blend_60_40", 1200.00, 1200.00, "2021-08-12"),
    (115, 13, "40134983", "roth_ira", 0, "blend_60_40", 5000.00, 5000.00, "2023-03-01"),
    (116, 14, "40145694", "individual", 0, "us_equity", 600.00, 600.00, "2022-04-27"),
    (117, 15, "40156205", "individual", 0, "blend_60_40", 2100.00, 2100.00, "2019-01-14"),
    (118, 16, "40167816", "individual", 0, "us_equity", 7500.00, 7500.00, "2023-06-02"),
    (119, 17, "40178527", "individual", 0, "blend_60_40", 1900.00, 1900.00, "2020-05-21"),
    (120, 18, "40189238", "roth_ira", 0, "us_equity", 1300.00, 1300.00, "2022-11-08"),
    (121, 19, "40190949", "individual", 0, "blend_60_40", 6200.00, 6200.00, "2015-09-30"),
    (122, 20, "40201560", "individual", 0, "blend_60_40", 450.00, 450.00, "2026-09-15"),
]

# account_id, symbol, quantity, total cost basis
HOLDINGS = [
    (101, "HLVD", 40, 6000), (101, "VLTR", 60, 5700), (101, "LMTM", 30, 3150), (101, "TSCG", 20, 1160),
    (102, "LMTM", 120, 12000), (102, "LMCB", 80, 7760), (102, "NGLF", 40, 1760),
    (103, "LMTM", 55, 5940), (103, "BRMU", 30, 1950), (103, "LMCB", 40, 3880),
    (104, "HLVD", 25, 3250), (104, "ORNE", 60, 2520), (104, "LMTM", 40, 4200),
    (105, "LMTM", 70, 6300), (105, "NGLF", 50, 2000), (105, "TSCG", 35, 1890),
    (106, "LMCB", 150, 14250), (106, "LMTM", 35, 3150), (106, "BRMU", 25, 1500),
    (107, "LMTM", 95, 9025), (107, "LMCB", 60, 5820),
    (108, "NGLF", 30, 1320), (108, "TSCG", 25, 1400), (108, "CDRL", 100, 2600),
    (109, "LMTM", 60, 5700), (109, "ORNE", 40, 1800), (109, "NGLF", 35, 1540), (109, "HLVD", 10, 1300),
    (110, "LMCB", 1500, 141000), (110, "LMTM", 1200, 108000), (110, "CDRL", 800, 20800), (110, "BRMU", 300, 18000),
    (111, "LMTM", 45, 4050), (111, "TSCG", 30, 1650), (111, "LMCB", 20, 1900),
    (112, "QLBT", 120, 5400), (112, "VLTR", 40, 3800), (112, "HLVD", 15, 2100),
    (113, "LMTM", 80, 7200), (113, "NGLF", 45, 1800), (113, "LMCB", 30, 2850),
    (114, "LMTM", 50, 5000), (114, "TSCG", 40, 2240), (114, "VLTR", 20, 1900),
    (115, "LMTM", 42, 4410), (115, "LMCB", 25, 2400),
    (116, "QLBT", 300, 14100), (116, "VLTR", 90, 9000), (116, "HLVD", 35, 5250),
    (117, "LMCB", 200, 19200), (117, "LMTM", 60, 5700), (117, "BRMU", 40, 2560),
    (118, "HLVD", 30, 3900), (118, "NGLF", 60, 2520), (118, "LMTM", 25, 2625),
    (119, "LMTM", 85, 8075), (119, "TSCG", 50, 2850), (119, "LMCB", 45, 4320),
    (120, "LMTM", 65, 6500), (120, "VLTR", 30, 2850), (120, "ORNE", 25, 1050),
    (121, "LMCB", 400, 38400), (121, "BRMU", 80, 5200), (121, "LMTM", 40, 3800),
    (122, "LMTM", 40, 4760), (122, "LMCB", 10, 965),
]

# fee_type, display_name, amount, unit, description, waiver
FEE_SCHEDULE = [
    ("account_maintenance", "Account maintenance fee", 0.0, "per_month",
     "No monthly or annual maintenance fee on Larkmoor brokerage or IRA accounts.", None),
    ("stock_commission", "Online stock and ETF trades", 0.0, "per_trade",
     "No commission on online US stock and ETF trades.", None),
    ("advisory_fee", "Advisory fee (managed accounts only)", 0.25, "pct_annual",
     "Charged only on accounts enrolled in Larkmoor Managed Portfolios. Billed quarterly in arrears on the "
     "average daily balance and shown as a 'Quarterly advisory fee' transaction.",
     "Not charged on self-directed accounts."),
    ("ach_withdrawal", "Electronic (ACH) withdrawal", 0.0, "per_item",
     "Standard transfers to a linked bank account are free. Funds typically arrive 1 to 3 business days after processing.", None),
    ("wire_outgoing", "Outgoing domestic wire", 25.0, "per_item",
     "Charged per outgoing domestic wire. Wires requested before 2 pm Eastern on a business day usually arrive the same day.",
     "Waived for households with combined Larkmoor balances above $250,000."),
    ("paper_statement", "Paper statement fee", 5.0, "per_statement",
     "Charged for each mailed statement or trade confirmation.",
     "Waived when electronic delivery is turned on in settings."),
    ("acats_outgoing", "Full account transfer out", 75.0, "per_item",
     "Charged when a full account is transferred to another brokerage. Partial transfers out are free.", None),
    ("options_contract", "Options contract fee", 0.65, "per_contract",
     "Per-contract fee on options trades. No base commission.", None),
    ("margin_interest", "Margin interest", 9.5, "pct_annual",
     "Annual rate on borrowed balances, accrued daily and charged monthly. Illustrative rate.", None),
]

# withdrawal_id, account_id, requested_on, amount, destination_bank_last4, method, status, status_reason, expected_arrival_on, updated_on
WITHDRAWALS = [
    ("WD-24101", 106, "2026-10-28", 7500.00, "5518", "wire", "completed",
     "Wire sent 2026-10-28. A $25.00 outgoing wire fee was charged as a separate transaction.", "2026-10-28", "2026-10-28"),
    ("WD-24109", 113, "2026-10-29", 1200.00, "7045", "ach", "completed",
     "Funds were sent to the destination bank on 2026-10-30.", "2026-11-02", "2026-10-30"),
    ("WD-24115", 117, "2026-11-04", 3000.00, "9930", "ach", "completed",
     "Funds were sent to the destination bank on 2026-11-05.", "2026-11-06", "2026-11-05"),
    ("WD-24117", 102, "2026-11-05", 2500.00, "3302", "ach", "processing",
     None, "2026-11-10", "2026-11-06"),
    ("WD-24120", 103, "2026-11-04", 1800.00, "8821", "ach", "on_hold",
     "Destination bank account ending 8821 was linked on 2026-11-03. New bank links are held for 5 business days "
     "before the first withdrawal as a fraud-protection measure. The hold lifts on 2026-11-10 and funds are released "
     "the next business day.", "2026-11-12", "2026-11-04"),
    ("WD-24122", 112, "2026-11-06", 4000.00, "3376", "ach", "rejected",
     "Requested amount exceeded settled cash. Proceeds of $1,570.00 from the sale of 50 QLBT on 2026-11-06 settle on "
     "2026-11-07, one business day after the trade. Settled cash available on 2026-11-06: $3,230.00.", None, "2026-11-06"),
]

# transfer_id, account_id, direction, counterparty, transfer_type, amount, status, status_reason, initiated_on, estimated_completion_on, updated_on
TRANSFERS = [
    ("TR-30549", 105, "outgoing", "Harrowgate Investments", "partial", 10000.00, "rejected",
     "The receiving firm reported that the account owner name on the receiving account does not match the Larkmoor "
     "account. The request can be resubmitted once the receiving account title matches exactly.",
     "2026-10-27", None, "2026-11-04"),
    ("TR-30552", 104, "incoming", "Pembrook Brokerage", "full_account", None, "in_transit",
     "The delivering firm accepted the request on 2026-11-03. Assets typically arrive 3 to 6 business days after "
     "acceptance and may appear one position at a time.", "2026-10-30", "2026-11-09", "2026-11-03"),
    ("TR-30555", 114, "internal", "Larkmoor Roth IRA ending 4983", "cash", 5000.00, "completed",
     "Cash moved between the customer's Larkmoor accounts on 2026-11-03.", "2026-11-03", "2026-11-03", "2026-11-03"),
    ("TR-30561", 109, "incoming", "Vantrell Securities", "partial", None, "validating",
     "Transfer details are being matched against the delivering firm's records. This step usually completes within "
     "2 business days, after which the transfer moves to in transit.", "2026-11-05", "2026-11-13", "2026-11-05"),
]

# Hand-placed transactions that scenarios rely on: account_id, posted_on, type, symbol, quantity, amount, description
SPECIAL_TRANSACTIONS = [
    (106, "2026-10-28", "withdrawal", None, None, -7500.00, "Wire to bank ending 5518 (WD-24101)"),
    (106, "2026-10-28", "fee", None, None, -25.00, "Outgoing domestic wire fee"),
    (113, "2026-10-29", "withdrawal", None, None, -1200.00, "Withdrawal to bank ending 7045 (WD-24109)"),
    (117, "2026-11-04", "withdrawal", None, None, -3000.00, "Withdrawal to bank ending 9930 (WD-24115)"),
    (102, "2026-11-05", "withdrawal", None, None, -2500.00, "Withdrawal to bank ending 3302 (WD-24117, processing)"),
    (111, "2026-04-01", "fee", None, None, -5.00, "Paper statement fee"),
    (111, "2026-07-01", "fee", None, None, -5.00, "Paper statement fee"),
    (111, "2026-10-01", "fee", None, None, -5.00, "Paper statement fee"),
    (107, "2026-04-06", "fee", None, None, -11.17, "Quarterly advisory fee (0.25% annual rate)"),
    (107, "2026-07-06", "fee", None, None, -11.42, "Quarterly advisory fee (0.25% annual rate)"),
    (107, "2026-10-05", "fee", None, None, -11.09, "Quarterly advisory fee (0.25% annual rate)"),
    (112, "2026-11-06", "sell", "QLBT", 50, 1570.00, "Sold 50 QLBT at $31.40 (settles 2026-11-07)"),
    (114, "2026-11-03", "transfer_out", None, None, -5000.00, "Transfer to Larkmoor Roth IRA ending 4983 (TR-30555)"),
    (115, "2026-11-03", "transfer_in", None, None, 5000.00, "Transfer from Larkmoor account ending 4172 (TR-30555)"),
]


def _generated_transactions() -> list[tuple]:
    """Opening deposit, one buy per holding, quarterly dividends. Dates derive from opened_on, so the result is fixed."""
    out = []
    holdings_by_account: dict[int, list[tuple]] = {}
    for acct_id, symbol, qty, cost in HOLDINGS:
        holdings_by_account.setdefault(acct_id, []).append((symbol, qty, cost))
    for acct_id, _cust, _num, _type, _managed, _bench, cash, _settled, opened_on in ACCOUNTS:
        opened = date.fromisoformat(opened_on)
        invested = sum(cost for _s, _q, cost in holdings_by_account.get(acct_id, []))
        out.append((acct_id, opened_on, "deposit", None, None, round(invested + cash, 2), "Initial deposit from linked bank"))
        for i, (symbol, qty, cost) in enumerate(holdings_by_account.get(acct_id, [])):
            buy_on = (opened + timedelta(days=3 + 9 * i)).isoformat()
            out.append((acct_id, buy_on, "buy", symbol, qty, -float(cost), f"Bought {qty:g} {symbol} at ${cost / qty:.2f}"))
            per_share = QUARTERLY_DIVIDEND_PER_SHARE.get(symbol)
            if per_share:
                for d in DIVIDEND_DATES:
                    if d > buy_on:
                        out.append((acct_id, d, "dividend", symbol, None, round(qty * per_share, 2), f"Dividend {symbol}"))
    return out


def build(conn: sqlite3.Connection) -> None:
    """Create every table and load the seed into an open connection (file or in-memory)."""
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO benchmarks VALUES (?,?,?)", BENCHMARKS)
    conn.executemany("INSERT INTO benchmark_returns VALUES (?,?,?)",
                     [(b, p, r) for b, periods in BENCHMARK_RETURNS.items() for p, r in periods.items()])
    conn.executemany("INSERT INTO securities VALUES (?,?,?,?,?)", SECURITIES)
    conn.executemany("INSERT INTO security_returns VALUES (?,?,?)",
                     [(s, p, r) for s, periods in SECURITY_RETURNS.items() for p, r in periods.items()])
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)", CUSTOMERS)
    conn.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)", ACCOUNTS)
    conn.executemany("INSERT INTO holdings VALUES (?,?,?,?)", HOLDINGS)
    conn.executemany("INSERT INTO fee_schedule VALUES (?,?,?,?,?,?)", FEE_SCHEDULE)
    conn.executemany("INSERT INTO withdrawals VALUES (?,?,?,?,?,?,?,?,?,?)", WITHDRAWALS)
    conn.executemany("INSERT INTO transfers VALUES (?,?,?,?,?,?,?,?,?,?,?)", TRANSFERS)
    txs = sorted(_generated_transactions() + SPECIAL_TRANSACTIONS, key=lambda t: (t[1], t[0], t[6]))
    conn.executemany(
        "INSERT INTO transactions (account_id, posted_on, type, symbol, quantity, amount, description) VALUES (?,?,?,?,?,?,?)", txs)
    conn.commit()


def memory_db() -> sqlite3.Connection:
    """A fresh, fully seeded in-memory database. Evals give every conversation one of these."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
        order = ", ".join(cols)
        for row in conn.execute(f"SELECT * FROM {t} ORDER BY {order}"):
            digest.update(json.dumps([t, list(row)], sort_keys=True, default=str).encode())
    return digest.hexdigest()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    reset_db(target)
    print(f"Rebuilt {target} as of {AS_OF}: {len(CUSTOMERS)} customers, {len(ACCOUNTS)} accounts.")
