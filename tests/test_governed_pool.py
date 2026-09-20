"""Real PostgreSQL regression for the governed connection pool.

The pool reuses physical backends across requests, so these tests assert the
two properties that reuse could otherwise break: a returned connection carries
no identity or scope state into the next caller, and an exhausted pool or an
unreachable server surfaces as a retryable 503 rather than an unhandled 500.
"""
from __future__ import annotations

import os

import psycopg
import pytest

from memory_service_runtime.governed import db
from memory_service_runtime.governed.errors import GovernedError


@pytest.fixture(autouse=True)
def isolated_pool(monkeypatch):
    """Each test builds and disposes its own pool; none is left for the next."""
    db._close_pool()
    yield
    db._close_pool()


def _pid(conn) -> int:
    return conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]


def test_returned_connection_carries_no_governance_state():
    """Session-level identity written by one caller must not reach the next.

    The governed path sets these transaction-locally, so COMMIT already clears
    them.  This writes them at session level (local=false) on purpose: it is the
    worst case a future code path could introduce, and the pool must still
    hand the next caller a clean connection.
    """
    pool = db._pool(os.environ["DATABASE_URL"], 1)
    with pool.connection() as conn:
        first = _pid(conn)
        conn.execute(
            "SELECT " + ", ".join(f"set_config('{name}', 'leaked', false)"
                                  for name in db._SESSION_GUCS)
        )
        conn.commit()
    with pool.connection() as conn:
        assert _pid(conn) == first, "pool did not reuse the backend; assertion is vacuous"
        residue = conn.execute(
            "SELECT " + ", ".join(f"current_setting('{name}', true) AS {name.replace('.', '_')}"
                                  for name in db._SESSION_GUCS)
        ).fetchone()
    assert not any(residue.values()), f"governance state survived checkout: {residue}"


def test_exhausted_pool_is_a_retryable_503(monkeypatch):
    monkeypatch.setenv("GOVERNED_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("GOVERNED_POOL_TIMEOUT_SECONDS", "1")
    pool = db._pool(os.environ["DATABASE_URL"], 1)
    with pool.connection():
        with pytest.raises(GovernedError) as caught:
            with db.transaction("x" * 40):
                pass
    assert caught.value.code == "EVIDENCE_UNAVAILABLE"
    assert caught.value.status == 503


def test_unreachable_server_is_a_retryable_503(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/absent")
    monkeypatch.setenv("GOVERNED_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("GOVERNED_POOL_TIMEOUT_SECONDS", "1")
    with pytest.raises(GovernedError) as caught:
        with db.transaction("x" * 40):
            pass
    assert caught.value.code == "EVIDENCE_UNAVAILABLE"
    assert caught.value.status == 503


def test_zero_max_size_opens_a_direct_connection(monkeypatch):
    """The documented rollback switch: no pool is built for the process."""
    monkeypatch.setenv("GOVERNED_POOL_MAX_SIZE", "0")
    with pytest.raises((GovernedError, psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege)):
        with db.transaction("x" * 40):
            pass
    assert db._POOL is None


def test_missing_database_url_is_reported_not_pooled(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "  ")
    with pytest.raises(GovernedError) as caught:
        with db.transaction("x" * 40):
            pass
    assert caught.value.code == "EVIDENCE_UNAVAILABLE"
    assert db._POOL is None
