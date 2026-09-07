"""Canonical advisory-lock operations for a Context Graph scope."""
from __future__ import annotations

from psycopg import IsolationLevel
from psycopg.pq import TransactionStatus


class ContextGraphLockError(RuntimeError):
    pass


def scope_lock_key(tenant_id: str, organization_id: str) -> str:
    return f"context-graph:{tenant_id}:{organization_id}"


def lock_shared_for_transaction(executor, tenant_id: str, organization_id: str) -> None:
    executor.execute(
        "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))",
        (scope_lock_key(tenant_id, organization_id),),
    )


def acquire_exclusive_session_lock(conn, tenant_id: str, organization_id: str) -> None:
    """Acquire outside a transaction so waiting does not freeze an MVCC snapshot."""
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise ContextGraphLockError("advisory-lock connection must be idle")
    conn.isolation_level = IsolationLevel.READ_COMMITTED
    conn.execute(
        "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
        (scope_lock_key(tenant_id, organization_id),),
    )
    conn.commit()


def release_exclusive_session_lock(conn, tenant_id: str, organization_id: str) -> None:
    if conn.info.transaction_status != TransactionStatus.IDLE:
        conn.rollback()
    conn.isolation_level = IsolationLevel.READ_COMMITTED
    unlocked = conn.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
        (scope_lock_key(tenant_id, organization_id),),
    ).fetchone()[0]
    conn.commit()
    if not unlocked:
        raise ContextGraphLockError("context graph advisory lock was not held")
