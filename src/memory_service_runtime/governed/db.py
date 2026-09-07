"""Authentication, scope fencing and current policy checks for the pilot.

The per-scope FOR UPDATE fence deliberately serializes authorized transactions.
Callers must not commit midway through an authenticated operation or retain an
AuthContext for a later transaction. Historical reads still use current rights.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import os
from typing import Any, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from . import checkpoints
from .errors import GovernedError


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


def authenticate(conn: psycopg.Connection, token: str) -> AuthContext:
    """Resolve digest, acquire the auth fence, then recheck all current authority."""
    conn.row_factory = dict_row
    if not isinstance(token, str) or not 32 <= len(token) <= 1024 or token != token.strip():
        raise GovernedError("UNAUTHENTICATED")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    _set_scope(conn, "")
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
    scope = conn.execute("SELECT * FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (scope_id,)).fetchone()
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


@contextmanager
def transaction(token: str) -> Iterator[tuple[psycopg.Connection, AuthContext]]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise GovernedError("EVIDENCE_UNAVAILABLE", "The governed database is unavailable.")
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        with conn.transaction():
            ctx = authenticate(conn, token)
            yield conn, ctx
