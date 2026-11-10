import pytest

from agent import tools
from agent.session import Session
from mockworld.seed import memory_db


@pytest.fixture
def conn():
    c = memory_db()
    yield c
    c.close()


@pytest.fixture
def session():
    return Session()


@pytest.fixture
def verified(conn):
    """A session verified as customer 1 (Priya Raman, account 101)."""
    s = Session()
    r = tools.verify_identity(s, conn, "priya.raman@example.com", date_of_birth="1984-03-12", security_word="saffron")
    assert r["ok"] and s.verified_customer_id == 1
    return s
