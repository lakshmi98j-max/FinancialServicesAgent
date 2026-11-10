"""The mock world must be deterministic and internally consistent."""
import sqlite3

from mockworld import seed
from mockworld.db import connect, rows
from mockworld.seed import fingerprint, memory_db, reset_db


def test_rebuild_is_byte_identical():
    a, b = memory_db(), memory_db()
    assert fingerprint(a) == fingerprint(b)


def test_reset_db_recreates_file(tmp_path):
    path = tmp_path / "nested" / "world.db"
    reset_db(path)
    conn = connect(path)
    conn.execute("INSERT INTO escalations VALUES ('ESC-9999', NULL, '2026-11-06', 'other', 'scratch', 'low', 'open')")
    conn.commit()
    conn.close()
    reset_db(path)
    fresh = connect(path)
    assert fresh.execute("SELECT COUNT(*) FROM escalations").fetchone()[0] == 0
    assert fingerprint(fresh) == fingerprint(memory_db())


def test_world_shape(conn):
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] >= 20
    orphan = conn.execute("""SELECT COUNT(*) FROM customers c
                             WHERE NOT EXISTS (SELECT 1 FROM accounts a WHERE a.customer_id = c.customer_id)""").fetchone()[0]
    assert orphan == 0
    assert conn.execute("SELECT COUNT(*) FROM advisor_handoffs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM escalations").fetchone()[0] == 0


def test_account_last4_unique(conn):
    last4 = [r["l4"] for r in rows(conn, "SELECT substr(account_number, -4) AS l4 FROM accounts")]
    assert len(last4) == len(set(last4)), "verify_identity locates by last 4, so they must be unique"


def test_every_holding_and_benchmark_has_all_periods(conn):
    for table, key in (("security_returns", "symbol"), ("benchmark_returns", "benchmark_id")):
        counts = rows(conn, f"SELECT {key} AS k, COUNT(*) AS n FROM {table} GROUP BY {key}")
        assert all(c["n"] == 4 for c in counts), table
    missing = conn.execute("""SELECT COUNT(*) FROM holdings h
                              WHERE NOT EXISTS (SELECT 1 FROM securities s WHERE s.symbol = h.symbol)""").fetchone()[0]
    assert missing == 0


def test_scenario_anchors_exist(conn):
    """Situations the scenario files point at must be present."""
    statuses = {r["status"] for r in rows(conn, "SELECT status FROM withdrawals")}
    assert {"processing", "on_hold", "rejected", "completed"} <= statuses
    tstatuses = {r["status"] for r in rows(conn, "SELECT status FROM transfers")}
    assert {"in_transit", "rejected", "completed", "validating"} <= tstatuses
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE type = 'fee'").fetchone()[0] >= 3


def test_foreign_keys_hold():
    conn = sqlite3.connect(":memory:")
    seed.build(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
