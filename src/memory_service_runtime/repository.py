"""PostgreSQL CAS operations for the infrastructure runtime queue."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


TERMINAL_STATES = frozenset(("succeeded", "failed"))


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different operational request."""


@dataclass(frozen=True)
class RuntimeTask:
    task_id: str
    tenant_id: str
    organization_id: str
    task_type: str
    idempotency_key: str
    request_sha256: str
    payload: dict[str, Any]
    state: str
    attempt: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: datetime | None
    result: dict[str, Any] | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "RuntimeTask":
        values = dict(row)
        for key in ("task_id", "lease_token"):
            if values.get(key) is not None:
                values[key] = str(values[key])
        return cls(**values)


@dataclass(frozen=True)
class EnqueueResult:
    task: RuntimeTask
    created: bool


@dataclass(frozen=True)
class RecoveryResult:
    retryable: int
    failed: int


def request_sha256(
    tenant_id: str,
    organization_id: str,
    task_type: str,
    payload: dict[str, Any],
    max_attempts: int,
) -> str:
    """Hash one operational request using a stable, versioned local encoding."""
    envelope = {
        "hashVersion": "runtime-json-v1",
        "maxAttempts": max_attempts,
        "organizationId": organization_id,
        "payload": payload,
        "taskType": task_type,
        "tenantId": tenant_id,
    }
    try:
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("runtime task payload 必须是可序列化的 JSON 对象") from exc
    return hashlib.sha256(encoded).hexdigest()


def enqueue_task(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    organization_id: str,
    task_type: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 5,
) -> EnqueueResult:
    """Create one task, or return the identical reservation for a repeated key."""
    tenant_id = tenant_id.strip()
    organization_id = organization_id.strip()
    task_type = task_type.strip()
    idempotency_key = idempotency_key.strip()
    body = {} if payload is None else payload
    if not isinstance(body, dict):
        raise ValueError("runtime task payload 必须是 JSON 对象")
    if not 1 <= len(tenant_id) <= 200 or not 1 <= len(organization_id) <= 200:
        raise ValueError("tenant_id 和 organization_id 长度必须在 1..200 范围内")
    if not 1 <= len(task_type) <= 128:
        raise ValueError("task_type 长度必须在 1..128 范围内")
    if not 1 <= len(idempotency_key) <= 200:
        raise ValueError("idempotency_key 长度必须在 1..200 范围内")
    if not 1 <= max_attempts <= 100:
        raise ValueError("max_attempts 必须在 1..100 范围内")
    digest = request_sha256(tenant_id, organization_id, task_type, body, max_attempts)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """INSERT INTO runtime_tasks
                 (tenant_id, organization_id, task_type, idempotency_key,
                  request_sha256, payload, max_attempts)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, organization_id, idempotency_key) DO NOTHING
               RETURNING *""",
            (
                tenant_id,
                organization_id,
                task_type,
                idempotency_key,
                digest,
                Jsonb(body),
                max_attempts,
            ),
        )
        row = cur.fetchone()
        if row is not None:
            return EnqueueResult(RuntimeTask.from_row(row), created=True)
        cur.execute(
            """SELECT * FROM runtime_tasks
                 WHERE tenant_id=%s AND organization_id=%s AND idempotency_key=%s""",
            (tenant_id, organization_id, idempotency_key),
        )
        existing = cur.fetchone()
    if existing is None:  # defensive: the unique row cannot disappear in normal operation
        raise RuntimeError("idempotency reservation disappeared")
    task = RuntimeTask.from_row(existing)
    if task.request_sha256 != digest:
        raise IdempotencyConflict("idempotency_key 已用于不同的 runtime task 请求")
    return EnqueueResult(task, created=False)


def get_task(
    conn: psycopg.Connection,
    task_id: str,
    *,
    tenant_id: str,
    organization_id: str,
) -> RuntimeTask | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT * FROM runtime_tasks
                 WHERE task_id=%s AND tenant_id=%s AND organization_id=%s""",
            (task_id, tenant_id, organization_id),
        )
        row = cur.fetchone()
    return None if row is None else RuntimeTask.from_row(row)


def recover_expired_tasks(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    organization_id: str,
) -> RecoveryResult:
    """Fence expired owners; only non-terminal operational work may be retried."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """UPDATE runtime_tasks
                  SET state = CASE WHEN attempt >= max_attempts THEN 'failed' ELSE 'retryable' END,
                      available_at = clock_timestamp(),
                      lease_owner = NULL,
                      lease_token = NULL,
                      lease_expires_at = NULL,
                      error_code = CASE
                          WHEN attempt >= max_attempts THEN 'lease_expired_max_attempts'
                          ELSE 'lease_expired'
                      END,
                      finished_at = CASE
                          WHEN attempt >= max_attempts THEN clock_timestamp() ELSE NULL
                      END,
                      updated_at = clock_timestamp()
                WHERE tenant_id=%s AND organization_id=%s
                  AND state='in_progress' AND lease_expires_at <= clock_timestamp()
                RETURNING state"""
            ,
            (tenant_id, organization_id),
        )
        rows = cur.fetchall()
    return RecoveryResult(
        retryable=sum(row["state"] == "retryable" for row in rows),
        failed=sum(row["state"] == "failed" for row in rows),
    )


def claim_task(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    organization_id: str,
    worker_id: str,
    lease_seconds: int,
) -> RuntimeTask | None:
    """Claim the next available task with ``FOR UPDATE SKIP LOCKED``."""
    token = uuid.uuid4()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """WITH candidate AS (
                   SELECT task_id
                     FROM runtime_tasks
                    WHERE tenant_id=%s AND organization_id=%s
                      AND state IN ('queued', 'retryable')
                      AND available_at <= clock_timestamp()
                    ORDER BY available_at, created_at, task_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
               )
               UPDATE runtime_tasks AS task
                  SET state='in_progress',
                      attempt=task.attempt + 1,
                      lease_owner=%s,
                      lease_token=%s,
                      lease_expires_at=clock_timestamp() + make_interval(secs => %s),
                      started_at=coalesce(task.started_at, clock_timestamp()),
                      error_code=NULL,
                      updated_at=clock_timestamp()
                 FROM candidate
                WHERE task.task_id=candidate.task_id
               RETURNING task.*""",
            (tenant_id, organization_id, worker_id, token, lease_seconds),
        )
        row = cur.fetchone()
    return None if row is None else RuntimeTask.from_row(row)


def renew_task_lease(
    conn: psycopg.Connection,
    task: RuntimeTask,
    *,
    lease_seconds: int,
) -> bool:
    """Extend only the currently fenced lease; stale owners cannot revive it."""
    if task.lease_owner is None or task.lease_token is None:
        return False
    updated = conn.execute(
        """UPDATE runtime_tasks
              SET lease_expires_at=clock_timestamp() + make_interval(secs => %s),
                  updated_at=clock_timestamp()
            WHERE task_id=%s AND tenant_id=%s AND organization_id=%s
              AND state='in_progress'
              AND lease_owner=%s AND lease_token=%s
              AND lease_expires_at > clock_timestamp()""",
        (
            lease_seconds,
            task.task_id,
            task.tenant_id,
            task.organization_id,
            task.lease_owner,
            task.lease_token,
        ),
    ).rowcount
    return updated == 1


def succeed_task(
    conn: psycopg.Connection,
    task: RuntimeTask,
    *,
    result: dict[str, Any],
) -> bool:
    """Commit a terminal result only while the caller still owns the lease."""
    if task.lease_owner is None or task.lease_token is None:
        return False
    updated = conn.execute(
        """UPDATE runtime_tasks
              SET state='succeeded', result=%s,
                  lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                  finished_at=clock_timestamp(), updated_at=clock_timestamp()
            WHERE task_id=%s AND tenant_id=%s AND organization_id=%s
              AND state='in_progress'
              AND lease_owner=%s AND lease_token=%s
              AND lease_expires_at > clock_timestamp()""",
        (
            Jsonb(result),
            task.task_id,
            task.tenant_id,
            task.organization_id,
            task.lease_owner,
            task.lease_token,
        ),
    ).rowcount
    return updated == 1


def fail_task(
    conn: psycopg.Connection,
    task: RuntimeTask,
    *,
    error_code: str,
    retryable: bool,
    retry_delay_seconds: int,
) -> str | None:
    """CAS one failure to ``retryable`` or the immutable ``failed`` state."""
    if task.lease_owner is None or task.lease_token is None:
        return None
    code = error_code.strip()[:128] or "runtime_task_failed"
    should_retry = retryable and task.attempt < task.max_attempts
    target = "retryable" if should_retry else "failed"
    updated = conn.execute(
        """UPDATE runtime_tasks
              SET state=%s,
                  available_at=CASE WHEN %s THEN
                      clock_timestamp() + make_interval(secs => %s)
                    ELSE available_at END,
                  lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                  error_code=%s,
                  finished_at=CASE WHEN %s THEN NULL ELSE clock_timestamp() END,
                  updated_at=clock_timestamp()
            WHERE task_id=%s AND tenant_id=%s AND organization_id=%s
              AND state='in_progress'
              AND lease_owner=%s AND lease_token=%s
              AND lease_expires_at > clock_timestamp()""",
        (
            target,
            should_retry,
            retry_delay_seconds,
            code,
            should_retry,
            task.task_id,
            task.tenant_id,
            task.organization_id,
            task.lease_owner,
            task.lease_token,
        ),
    ).rowcount
    return target if updated == 1 else None


def record_worker_heartbeat(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    organization_id: str,
    worker_id: str,
    status: str,
    current_task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Upsert the current worker liveness projection."""
    conn.execute(
        """INSERT INTO runtime_worker_heartbeats
             (tenant_id, organization_id, worker_id, status, current_task_id, metadata)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, organization_id, worker_id) DO UPDATE
             SET status=EXCLUDED.status,
                 current_task_id=EXCLUDED.current_task_id,
                 heartbeat_at=clock_timestamp(),
                 started_at=CASE WHEN EXCLUDED.status='starting'
                                 THEN clock_timestamp()
                                 ELSE runtime_worker_heartbeats.started_at END,
                 metadata=EXCLUDED.metadata""",
        (
            tenant_id,
            organization_id,
            worker_id,
            status,
            current_task_id,
            Jsonb(metadata or {}),
        ),
    )


def worker_is_healthy(
    conn: psycopg.Connection,
    *,
    tenant_id: str,
    organization_id: str,
    worker_id: str,
    max_age_seconds: int,
) -> tuple[bool, dict[str, Any]]:
    """Check the exact worker row without exposing database connection details."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT worker_id, status, current_task_id, heartbeat_at,
                      heartbeat_at > clock_timestamp() - make_interval(secs => %s) AS fresh
                 FROM runtime_worker_heartbeats
                WHERE tenant_id=%s AND organization_id=%s AND worker_id=%s""",
            (max_age_seconds, tenant_id, organization_id, worker_id),
        )
        row = cur.fetchone()
    if row is None:
        return False, {
            "ok": False,
            "tenantId": tenant_id,
            "organizationId": organization_id,
            "workerId": worker_id,
            "code": "worker_not_registered",
        }
    healthy = row["status"] == "running" and bool(row["fresh"])
    return healthy, {
        "ok": healthy,
        "tenantId": tenant_id,
        "organizationId": organization_id,
        "workerId": worker_id,
        "status": row["status"],
        "fresh": bool(row["fresh"]),
        "currentTaskId": str(row["current_task_id"]) if row["current_task_id"] else None,
        "heartbeatAt": row["heartbeat_at"].isoformat(),
    }
