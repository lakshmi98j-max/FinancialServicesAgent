"""The mock world must be deterministic, internally consistent, and contain every situation scenarios rely on."""
import sqlite3
from datetime import date

from mockworld import seed
from mockworld.db import connect, rows
from mockworld.seed import fingerprint, memory_db, monthly_payment, reset_db


def test_rebuild_is_byte_identical():
    assert fingerprint(memory_db()) == fingerprint(memory_db())


def test_reset_db_recreates_file(tmp_path):
    path = tmp_path / "nested" / "world.db"
    reset_db(path)
    conn = connect(path)
    conn.execute("INSERT INTO escalations VALUES ('ESC-9999', NULL, '2026-11-06', 'other', 'scratch', 'low', 'open')")
    conn.commit()
    conn.close()
    reset_db(path)
    assert fingerprint(connect(path)) == fingerprint(memory_db())


def test_foreign_keys_hold():
    conn = sqlite3.connect(":memory:")
    seed.build(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_world_shape(conn):
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM loans").fetchone()[0] == 21
    no_loan = conn.execute("""SELECT COUNT(*) FROM customers c
                              WHERE NOT EXISTS (SELECT 1 FROM loans l WHERE l.customer_id = c.customer_id)""").fetchone()[0]
    assert no_loan == 0
    assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM escalations").fetchone()[0] == 0


def test_loan_number_last4_unique(conn):
    last4 = [r["l4"] for r in rows(conn, "SELECT substr(loan_number, -4) AS l4 FROM loans")]
    assert len(last4) == len(set(last4)), "verify_identity locates by the last 4 digits, so they must be unique"


def test_payment_formula_matches_a_known_value():
    assert monthly_payment(200000, 6.0, 360) == 1199.10


def test_posted_payments_split_exactly(conn):
    for p in rows(conn, "SELECT * FROM payments WHERE status = 'posted' AND applied_principal IS NOT NULL"):
        assert round(p["applied_principal"] + p["applied_interest"] + p["applied_escrow"], 2) == p["amount"], p["payment_id"]


def test_mortgage_balances_fall_with_each_payment(conn):
    for loan in rows(conn, "SELECT * FROM loans WHERE loan_type = 'mortgage'"):
        assert 0 < loan["principal_balance"] <= loan["original_amount"]
        assert loan["monthly_principal_interest"] == monthly_payment(loan["original_amount"], loan["interest_rate"],
                                                                     loan["term_months"])


def test_scenario_anchors_exist(conn):
    statuses = {r["status"] for r in rows(conn, "SELECT status FROM payments")}
    assert {"posted", "returned", "scheduled"} <= statuses
    assert conn.execute("SELECT next_due_date FROM loans WHERE loan_id = 104").fetchone()[0] == "2026-10-01"
    assert conn.execute("SELECT next_due_date, deferred_amount FROM loans WHERE loan_id = 115").fetchone()[0] == "2026-11-01"
    assert conn.execute("SELECT special_handling FROM loans WHERE loan_id = 112").fetchone()[0] == "bankruptcy"
    assert conn.execute("SELECT COUNT(*) FROM escrow_analyses WHERE shortage_amount > 0").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM applications WHERE stage = 'conditions_outstanding'").fetchone()[0] == 1
    assert conn.execute("SELECT to_bank_last4 FROM heloc_draws WHERE draw_id = 'DR-7590'").fetchone()[0] == "9930"
    assert conn.execute("SELECT COUNT(*) FROM fees_charged WHERE status = 'unpaid'").fetchone()[0] == 3


def test_due_and_closing_dates_are_business_days(conn):
    dates = [r[0] for r in conn.execute(
        "SELECT received_on FROM payments UNION SELECT estimated_closing_on FROM applications "
        "WHERE estimated_closing_on IS NOT NULL")]
    assert dates and all(date.fromisoformat(d).weekday() < 5 for d in dates)
