"""Allowlisted effect dispatch with current rights and receiver idempotency."""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg.rows import dict_row

from memory_service_runtime.config import env_value
from memory_service_runtime.governed import db, protocol
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.handlers import TaskExecutionError


def governance_dispatch(task) -> dict:
    endpoint = os.environ.get("GOVERNED_EFFECT_URL", "").strip()
    parsed = urlsplit(endpoint)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.fragment
            or parsed.scheme not in {"http", "https"}
            or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})):
        raise TaskExecutionError("governance_effect_destination_invalid", retryable=False)
    payload = task.payload
    try:
        scope_id, receipt_id = str(payload["scope_id"]), str(payload["receipt_id"])
        with psycopg.connect(env_value("DATABASE_URL", required=True), row_factory=dict_row, connect_timeout=5) as conn:
            # Independent dispatch connection: declare the runtime capability
            # before the first gov_scopes read (0018 restrictive policies hide
            # identity/scope rows from pre-A1 binaries, stopping them here).
            db.set_write_capability(conn)
            conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope_id,))
            scope = conn.execute(
                "SELECT * FROM gov_scopes WHERE scope_id=%s AND tenant_id=%s AND company_id=%s FOR UPDATE",
                (scope_id, task.tenant_id, task.organization_id),
            ).fetchone()
            if scope is None:
                raise TaskExecutionError("governance_effect_scope_invalid", retryable=False)
            receipt = conn.execute("SELECT * FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s",
                                   (scope_id, receipt_id)).fetchone()
            if receipt is None or task.task_id not in receipt["effect_task_ids"]:
                raise TaskExecutionError("governance_effect_receipt_invalid", retryable=False)
            principal_id = str(receipt["principal_id"])
            principal = conn.execute("SELECT principal_type,active FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
                                     (scope_id, principal_id)).fetchone()
            if principal is None or not principal["active"]:
                raise TaskExecutionError("governance_effect_permission_revoked", retryable=False)
            assignments = conn.execute(
                "SELECT * FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND active AND valid_from<=clock_timestamp() AND (valid_to IS NULL OR valid_to>clock_timestamp())",
                (scope_id, principal_id),
            ).fetchall()
            ctx = db.AuthContext(scope_id, scope["tenant_id"], scope["company_id"], principal_id,
                                 principal["principal_type"], int(scope["auth_epoch"]), db.jsonable(assignments))
            required = receipt["result"].get("required_assignment_ids", [])
            if not required or sorted(required) != sorted(payload.get("required_assignment_ids", [])):
                raise TaskExecutionError("governance_effect_authority_invalid", retryable=False)
            valid = conn.execute(
                """SELECT a.assignment_id FROM gov_role_assignments a
                   JOIN gov_principals p ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
                   WHERE a.scope_id=%s AND a.assignment_id=ANY(%s::uuid[]) AND a.active AND p.active
                     AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())""",
                (scope_id, required),
            ).fetchall()
            if {str(item["assignment_id"]) for item in valid} != set(required):
                raise TaskExecutionError("governance_effect_permission_revoked", retryable=False)
            domains = set()
            for item in receipt["object_versions"]:
                obj = db.object_row(conn, ctx, item["object_id"])
                domains.add(obj["domain_id"])
                # Re-resolve every receipt object against its current binding
                # before any external call; the queue payload is not trusted.
                protocol.gate_effect_dispatch(conn, scope_id, item["object_id"], receipt["action_type"])
            if not domains:
                raise TaskExecutionError("governance_effect_scope_invalid", retryable=False)
            for domain in domains:
                db.authorize_domain(conn, ctx, domain, receipt["action_type"])
            # The key and body come from immutable authority, not mutable queue JSON.
            effect_key = f"{receipt_id}:{task.task_id}"
            body = {"effect_key": effect_key, "scope_id": scope_id, "receipt_id": receipt_id,
                    "action_type": receipt["action_type"], "object_versions": receipt["object_versions"]}
            with httpx.Client(trust_env=False, timeout=httpx.Timeout(5, connect=3), follow_redirects=False) as client:
                response = client.post(endpoint, json=body, headers={"Idempotency-Key": effect_key})
            if response.status_code == 409:
                raise TaskExecutionError("governance_effect_idempotency_conflict", retryable=False)
            if response.status_code < 200 or response.status_code >= 300:
                raise TaskExecutionError("governance_effect_unavailable", retryable=response.status_code >= 500)
            if len(response.content) > 65536:
                raise TaskExecutionError("governance_effect_response_invalid", retryable=False)
            acknowledgement = response.json()
            if not isinstance(acknowledgement, dict) or acknowledgement.get("effect_key") != effect_key:
                raise TaskExecutionError("governance_effect_response_invalid", retryable=False)
            return {"ok": True, "effect_key": effect_key, "receipt_id": receipt_id,
                    "external_receipt": acknowledgement}
    except TaskExecutionError:
        raise
    except GovernedError as exc:
        raise TaskExecutionError("governance_effect_permission_revoked", retryable=False) from exc
    except (httpx.TransportError, psycopg.OperationalError) as exc:
        raise TaskExecutionError("governance_effect_unavailable", retryable=True) from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise TaskExecutionError("governance_effect_payload_invalid", retryable=False) from exc
