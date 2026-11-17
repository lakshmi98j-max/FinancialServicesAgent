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


def verify(conn, locator, **factors) -> Session:
    s = Session()
    r = tools.verify_identity(s, conn, locator, **factors)
    assert r["ok"], r
    return s


@pytest.fixture
def priya(conn):
    """Customer 1: mortgage 101, payment rising in December after an escrow analysis."""
    return verify(conn, "priya.raman@example.com", date_of_birth="1984-03-12", property_zip="80302")
