"""Persistence primitives and read projections for Working Memory."""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

_OBJECT_UUID_KEYS = ("object_id", "chain_id", "issue_id", "created_by")
_VERSION_UUID_KEYS = (
    "record_id", "object_id", "supersedes", "confirmed_judgment_record_id",
    "agreement_record_id", "created_by", "confirmed_by",
)
_CHAIN_UUID_KEYS = ("chain_id", "derived_from_chain", "created_by")


def _dictcur(conn):
    return conn.cursor(row_factory=dict_row)


def _stringify(row: dict | None, keys: tuple[str, ...]) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in keys:
        if out.get(key) is not None:
            out[key] = str(out[key])
    return out


def get_object(conn, object_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute("SELECT * FROM wm_objects WHERE object_id=%s", (object_id,))
        row = cur.fetchone()
    return _stringify(row, _OBJECT_UUID_KEYS)


def get_chain(conn, chain_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute("SELECT * FROM wm_issue_chains WHERE chain_id=%s", (chain_id,))
        row = cur.fetchone()
    return _stringify(row, _CHAIN_UUID_KEYS)


def get_chain_scoped(conn, *, chain_id, tenant_id: str, organization_id: str) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute(
            """SELECT * FROM wm_issue_chains
               WHERE chain_id=%s AND tenant_id=%s AND organization_id=%s""",
            (chain_id, tenant_id, organization_id),
        )
        row = cur.fetchone()
    return _stringify(row, _CHAIN_UUID_KEYS)


def get_version(conn, record_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute("SELECT * FROM wm_object_versions WHERE record_id=%s", (record_id,))
        row = cur.fetchone()
    return _stringify(row, _VERSION_UUID_KEYS)


def lock_version(conn, record_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute("SELECT * FROM wm_object_versions WHERE record_id=%s FOR UPDATE", (record_id,))
        row = cur.fetchone()
    return _stringify(row, _VERSION_UUID_KEYS)


def lock_current_version(conn, object_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT * FROM wm_object_versions WHERE object_id=%s ORDER BY version DESC LIMIT 1 FOR UPDATE",
            (object_id,),
        )
        row = cur.fetchone()
    return _stringify(row, _VERSION_UUID_KEYS)


def get_current_version(conn, object_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT * FROM wm_object_versions WHERE object_id=%s ORDER BY version DESC LIMIT 1",
            (object_id,),
        )
        row = cur.fetchone()
    return _stringify(row, _VERSION_UUID_KEYS)


def get_latest_confirmed_version(conn, object_id) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT * FROM wm_object_versions WHERE object_id=%s AND confirmation_status='confirmed' "
            "ORDER BY version DESC LIMIT 1",
            (object_id,),
        )
        row = cur.fetchone()
    return _stringify(row, _VERSION_UUID_KEYS)


def get_version_chain(conn, object_id) -> list[dict]:
    with _dictcur(conn) as cur:
        cur.execute("SELECT * FROM wm_object_versions WHERE object_id=%s ORDER BY version ASC", (object_id,))
        rows = cur.fetchall()
    return [_stringify(row, _VERSION_UUID_KEYS) for row in rows]


def list_chain_current_versions_by_type(conn, chain_id, object_type: str) -> list[dict]:
    with _dictcur(conn) as cur:
        cur.execute(
            """SELECT DISTINCT ON (v.object_id) v.*
               FROM wm_object_versions v JOIN wm_objects o ON o.object_id=v.object_id
               WHERE o.chain_id=%s AND o.object_type=%s
               ORDER BY v.object_id, v.version DESC""",
            (chain_id, object_type),
        )
        rows = cur.fetchall()
    return [_stringify(row, _VERSION_UUID_KEYS) for row in rows]


def resolve_chain_by_title(conn, *, tenant_id: str, organization_id: str, title: str) -> dict | None:
    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT * FROM wm_issue_chains WHERE tenant_id=%s AND organization_id=%s AND lower(title)=lower(%s)",
            (tenant_id, organization_id, title),
        )
        row = cur.fetchone()
    return _stringify(row, _CHAIN_UUID_KEYS)


def list_chains(conn, *, tenant_id: str, organization_id: str, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM wm_issue_chains WHERE tenant_id=%s AND organization_id=%s"
    params: list[Any] = [tenant_id, organization_id]
    if status:
        sql += " AND status=%s"
        params.append(status)
    sql += " ORDER BY created_at"
    with _dictcur(conn) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_stringify(row, _CHAIN_UUID_KEYS) for row in rows]
