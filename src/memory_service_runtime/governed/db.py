"""Authentication, scope fencing and current policy checks for the pilot.

The per-scope FOR UPDATE fence deliberately serializes authorized transactions.
Callers must not commit midway through an authenticated operation or retain an
AuthContext for a later transaction. Historical reads still use current rights.
The one sanctioned split is evidence bytes (ADR-0006): upload and download are
two separately authenticated transactions with object-store I/O between them,
and only the second transaction's commit counts.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import atexit
import hashlib
import os
import threading
import time
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout

from memory_service_runtime import observability
from . import checkpoints
from .errors import GovernedError

# 本事务等 scope 栅栏的耗时（毫秒）：authenticate 拿到锁后写入，事务结束记运行记录时读出。
LOCK_WAIT_MS: ContextVar[float | None] = ContextVar("governed_lock_wait_ms", default=None)
# 本事务提交的动作（动作类型、回执、效果任务）：只在事务提交后随运行记录写出。
ACTION: ContextVar[dict[str, Any] | None] = ContextVar("governed_action", default=None)
_BUSY = "The governed database is busy; retry later."


@dataclass(frozen=True)
class AuthContext:
    scope_id: str
    tenant_id: str
    company_id: str
    principal_id: str
    principal_type: str
    auth_epoch: int
    assignments: list[dict[str, Any]]


def jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


def _uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise GovernedError("NOT_FOUND") from exc


def _set_scope(conn: psycopg.Connection, scope_id: str) -> None:
    conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope_id,))


def _assignments(conn: psycopg.Connection, ctx: AuthContext) -> list[dict[str, Any]]:
    principal = conn.execute(
        "SELECT active FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
        (ctx.scope_id, ctx.principal_id),
    ).fetchone()
    if principal is None or not principal["active"]:
        raise GovernedError("FORBIDDEN")
    rows = conn.execute(
        """SELECT assignment_id, scope_id, principal_id, domain_id, role, active,
                  valid_from, valid_to
             FROM gov_role_assignments
            WHERE scope_id=%s AND principal_id=%s AND active
              AND valid_from <= clock_timestamp()
              AND (valid_to IS NULL OR valid_to > clock_timestamp())
            ORDER BY domain_id, role, assignment_id""",
        (ctx.scope_id, ctx.principal_id),
    ).fetchall()
    if not rows:
        raise GovernedError("FORBIDDEN")
    return jsonable(rows)


def set_write_capability(conn: psycopg.Connection, *, local: bool = True) -> None:
    """Declare the A1 write capability for this transaction/session.

    Migration 0018 rejects governed business writes from connections that do
    not carry this declaration, which stops pre-A1 writer/worker binaries.  It
    is a stale-implementation fence, not a security boundary against SQL.
    """
    from .profile import WRITE_CAPABILITY

    conn.execute("SELECT set_config('app.runtime_write_capability', %s, %s)",
                 (WRITE_CAPABILITY, local))


def authenticate(conn: psycopg.Connection, token: str) -> AuthContext:
    """Resolve digest, acquire the auth fence, then recheck all current authority."""
    conn.row_factory = dict_row
    if not isinstance(token, str) or not 32 <= len(token) <= 1024 or token != token.strip():
        raise GovernedError("UNAUTHENTICATED")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    _set_scope(conn, "")
    # Declare the A1 runtime capability before the first protected read: the
    # 0018 restrictive policies hide gov_credentials/gov_scopes/... rows from
    # connections without it, which is exactly what stops pre-A1 binaries here.
    # This declares code currency only; identity is still fully verified below.
    set_write_capability(conn)
    conn.execute("SELECT set_config('app.governed_credential_digest', %s, true)", (digest,))
    credential = conn.execute(
        """SELECT credential_id, scope_id, principal_id FROM gov_credentials
            WHERE credential_digest=%s AND revoked_at IS NULL""", (digest,),
    ).fetchone()
    if credential is None:
        raise GovernedError("UNAUTHENTICATED")
    scope_id = str(credential["scope_id"])
    principal_id = str(credential["principal_id"])
    _set_scope(conn, scope_id)
    scope = acquire_fence(conn, "SELECT * FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (scope_id,))
    if scope is None:
        raise GovernedError("UNAUTHENTICATED")
    checkpoints.checkpoint("auth_fence_acquired", {
        "scope_id": scope_id, "principal_id": principal_id, "auth_epoch": scope["auth_epoch"],
    })
    current = conn.execute(
        """SELECT credential_id FROM gov_credentials
            WHERE credential_digest=%s AND scope_id=%s AND principal_id=%s
              AND revoked_at IS NULL""", (digest, scope_id, principal_id),
    ).fetchone()
    if current is None:
        raise GovernedError("UNAUTHENTICATED")
    principal = conn.execute(
        "SELECT principal_type, active FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
        (scope_id, principal_id),
    ).fetchone()
    if principal is None or not principal["active"]:
        raise GovernedError("FORBIDDEN")
    base = AuthContext(scope_id, scope["tenant_id"], scope["company_id"], principal_id,
                       principal["principal_type"], int(scope["auth_epoch"]), [])
    return AuthContext(base.scope_id, base.tenant_id, base.company_id, base.principal_id,
                       base.principal_type, base.auth_epoch, _assignments(conn, base))


def authorize_domain(conn: psycopg.Connection, ctx: AuthContext, domain_id: str,
                     action_type: str = "read") -> list[dict[str, Any]]:
    """Return current assignments explicitly allowed by the current policy version."""
    conn.row_factory = dict_row
    domain_id = _uuid(domain_id)
    current = _assignments(conn, ctx)
    policy = conn.execute(
        """SELECT policy_revision_id, content FROM gov_activation_policies
            WHERE scope_id=%s AND domain_id=%s
            ORDER BY policy_seq DESC LIMIT 1""", (ctx.scope_id, domain_id),
    ).fetchone()
    if policy is None or not isinstance(policy["content"], dict):
        raise GovernedError("FORBIDDEN")
    action_roles = policy["content"].get("action_roles", {})
    allowed = action_roles.get(action_type) if isinstance(action_roles, dict) else None
    if not isinstance(allowed, list) or not allowed or any(not isinstance(role, str) for role in allowed):
        raise GovernedError("FORBIDDEN")
    matches = [item for item in current if item["domain_id"] == domain_id and item["role"] in allowed]
    if not matches:
        raise GovernedError("FORBIDDEN")
    return matches


def object_row(conn: psycopg.Connection, ctx: AuthContext, object_id: str,
               lock: bool = False) -> dict[str, Any]:
    conn.row_factory = dict_row
    _assignments(conn, ctx)  # an actor with no current assignment remains a 403
    object_id = _uuid(object_id)
    suffix = " FOR UPDATE" if lock else ""
    row = conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s" + suffix,
                       (ctx.scope_id, object_id)).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND")
    try:
        authorize_domain(conn, ctx, str(row["domain_id"]), "read")
    except GovernedError as exc:
        if exc.code == "FORBIDDEN":
            raise GovernedError("NOT_FOUND") from exc
        raise
    return jsonable(row)


def revision_row(conn: psycopg.Connection, ctx: AuthContext, object_id: str,
                 revision_id: str) -> dict[str, Any]:
    head = object_row(conn, ctx, object_id)
    revision_id = _uuid(revision_id)
    row = conn.execute(
        "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
        (ctx.scope_id, head["object_id"], revision_id),
    ).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND")
    return jsonable(row)


# Session GUCs the governed path declares.  Every governed call sets these
# transaction-locally, so COMMIT/ROLLBACK already clears them; the pool clears
# them again on return so a reused connection cannot carry an identity or a
# scope into the next caller even if some future path forgets the `local` flag.
_SESSION_GUCS = ("app.governed_scope_id", "app.runtime_write_capability",
                 "app.governed_credential_digest")

_POOL_LOCK = threading.Lock()
_POOL: ConnectionPool | None = None
_POOL_KEY: tuple[str, int] | None = None


def _reset(conn: psycopg.Connection) -> None:
    """Clear governance session state before the connection is reused."""
    conn.execute("SELECT " + ", ".join(f"set_config('{name}', '', false)"
                                       for name in _SESSION_GUCS))
    conn.commit()


def _pool_config() -> tuple[str, int, float]:
    """Pool sizing from the environment.  max_size 0 disables pooling.

    DATABASE_URL is read through env_value so DATABASE_URL_FILE works here too:
    the DSN carries the application password, and a secret file keeps it out of
    the container environment where `docker inspect` would expose it.
    """
    from memory_service_runtime.config import RuntimeConfigError, env_value

    try:
        url = env_value("DATABASE_URL").strip()
    except RuntimeConfigError:
        url = ""
    try:
        max_size = int(os.environ.get("GOVERNED_POOL_MAX_SIZE", "10"))
        timeout = float(os.environ.get("GOVERNED_POOL_TIMEOUT_SECONDS", "10"))
    except ValueError:
        max_size, timeout = 10, 10.0
    return url, max(max_size, 0), max(timeout, 0.1)


def _pool(url: str, max_size: int) -> ConnectionPool:
    """One bounded pool per process, rebuilt when the DSN or bound changes."""
    global _POOL, _POOL_KEY
    with _POOL_LOCK:
        if _POOL is not None and _POOL_KEY == (url, max_size):
            return _POOL
        if _POOL is not None:
            _POOL.close()
            _POOL, _POOL_KEY = None, None
        pool = ConnectionPool(url, min_size=0, max_size=max_size, open=True,
                              reset=_reset, kwargs={"row_factory": dict_row,
                                                    "connect_timeout": 5})
        _POOL, _POOL_KEY = pool, (url, max_size)
        return pool


@atexit.register
def _close_pool() -> None:
    """Release pooled backends on SIGTERM so shutdown stays inside the grace period."""
    global _POOL, _POOL_KEY
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.close()
            _POOL, _POOL_KEY = None, None


def _timeouts() -> tuple[int, int]:
    """等锁与单条语句的上限（毫秒），可由环境变量调整；0 表示不设上限。"""
    try:
        lock = int(os.environ.get("GOVERNED_LOCK_TIMEOUT_MS", "5000"))
        statement = int(os.environ.get("GOVERNED_STATEMENT_TIMEOUT_MS", "30000"))
    except ValueError:
        lock, statement = 5000, 30000
    return max(lock, 0), max(statement, 0)


def acquire_fence(conn: psycopg.Connection, query: str, params: tuple) -> dict[str, Any] | None:
    """取 scope 栅栏（FOR UPDATE 那一条语句），整体受 GOVERNED_LOCK_TIMEOUT_MS 约束。

    lock_timeout 按每次取锁计：排在别人后面时要先等元组锁、再等持锁事务，最多两倍。
    这条语句另用 statement_timeout 限在同一上限内，之后恢复平常的语句上限；等锁耗时记入运行记录。
    """
    lock, statement = _timeouts()
    if lock:
        conn.execute("SELECT set_config('statement_timeout', %s, true)", (f"{lock}ms",))
    waited = time.perf_counter()
    row = conn.execute(query, params).fetchone()
    LOCK_WAIT_MS.set(round((time.perf_counter() - waited) * 1000, 1))
    if lock:
        conn.execute("SELECT set_config('statement_timeout', %s, true)", (f"{statement}ms",))
    return row


def record_action(receipt: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    """记下本事务提交的动作（类型、回执、效果任务），事务提交后随运行记录写出；不含请求内容。"""
    ACTION.set({"action_type": receipt["action_type"], "receipt_id": receipt["receipt_id"],
                "effect_task_ids": receipt.get("effect_task_ids") or [], "replayed": replayed})
    return receipt


def set_timeouts(conn: psycopg.Connection) -> None:
    """事务内生效：等 scope 栅栏（或任何锁）、执行单条语句超过上限，都由数据库中止。"""
    lock, statement = _timeouts()
    conn.execute("SELECT set_config('lock_timeout', %s, true), set_config('statement_timeout', %s, true)",
                 (f"{lock}ms", f"{statement}ms"))


@contextmanager
def _governed(conn: psycopg.Connection, token: str,
              pool_wait_ms: float) -> Iterator[tuple[psycopg.Connection, AuthContext]]:
    """一个治理事务：设超时、认证并拿 scope 栅栏；超时是可重试的 503；结束后记一行运行记录。"""
    LOCK_WAIT_MS.set(None)
    ACTION.set(None)
    started, scope_id, outcome = time.perf_counter(), None, "error"
    try:
        with conn.transaction():
            set_timeouts(conn)
            try:
                ctx = authenticate(conn, token)
                scope_id = ctx.scope_id
                yield conn, ctx
            except (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled) as exc:
                # Same public code as an exhausted pool: the error set has no overload signal yet.
                raise GovernedError("EVIDENCE_UNAVAILABLE", _BUSY) from exc
        outcome = "committed"
    except GovernedError as exc:
        outcome = exc.code
        raise
    finally:
        action = ACTION.get() if outcome == "committed" else None
        observability.log("governed_transaction", scope_id=scope_id, pool_wait_ms=round(pool_wait_ms, 1),
                          lock_wait_ms=LOCK_WAIT_MS.get(),
                          held_ms=round((time.perf_counter() - started) * 1000, 1),
                          outcome=outcome, **(action or {}))


@contextmanager
def transaction(token: str) -> Iterator[tuple[psycopg.Connection, AuthContext]]:
    url, max_size, timeout = _pool_config()
    if not url:
        raise GovernedError("EVIDENCE_UNAVAILABLE", "The governed database is unavailable.")
    if max_size == 0:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
            with _governed(conn, token, 0.0) as bound:
                yield bound
        return
    pool = _pool(url, max_size)
    with ExitStack() as stack:
        started = time.perf_counter()
        try:
            conn = stack.enter_context(pool.connection(timeout=timeout))
        except (PoolTimeout, psycopg.OperationalError) as exc:
            # Exhausted pool or an unreachable server: a 503 the caller can
            # retry, not an unhandled 500.  The code is shared with evidence
            # storage because the public error set has no overload signal yet.
            raise GovernedError("EVIDENCE_UNAVAILABLE",
                                "The governed database is unavailable.") from exc
        with _governed(conn, token, (time.perf_counter() - started) * 1000) as bound:
            yield bound
