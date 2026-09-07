"""Real PostgreSQL acceptance for the isolated infrastructure worker queue."""
from __future__ import annotations

from types import SimpleNamespace
import uuid

import psycopg
import pytest

from memory_service_runtime.config import RuntimeConfig
from memory_service_runtime.handlers import (
    TaskExecutionError,
    object_store_preflight,
)
from memory_service_runtime.repository import (
    IdempotencyConflict,
    claim_task,
    enqueue_task,
    get_task,
    record_worker_heartbeat,
    recover_expired_tasks,
    succeed_task,
    worker_is_healthy,
)
from memory_service_runtime.worker import RuntimeWorker
from tests.conftest import DATABASE_URL, connect


@pytest.fixture
def runtime_scope():
    tenant = f"runtime-test-{uuid.uuid4().hex[:10]}"
    organization = f"runtime-org-{uuid.uuid4().hex[:10]}"
    try:
        yield tenant, organization
    finally:
        with connect() as conn:
            conn.execute(
                """DELETE FROM runtime_worker_heartbeats
                     WHERE tenant_id=%s AND organization_id=%s""",
                (tenant, organization),
            )
            conn.execute(
                """DELETE FROM runtime_tasks
                     WHERE tenant_id=%s AND organization_id=%s""",
                (tenant, organization),
            )


def _enqueue(
    tenant: str,
    organization: str,
    *,
    key: str,
    payload: dict | None = None,
    task_type: str = "system.noop",
    max_attempts: int = 5,
):
    with connect() as conn:
        return enqueue_task(
            conn,
            tenant_id=tenant,
            organization_id=organization,
            task_type=task_type,
            idempotency_key=key,
            payload=payload,
            max_attempts=max_attempts,
        )


def _config(tenant: str, organization: str, worker_id: str = "worker-a") -> RuntimeConfig:
    return RuntimeConfig(
        database_url=DATABASE_URL,
        tenant_id=tenant,
        organization_id=organization,
        worker_id=worker_id,
        retry_base_seconds=0,
        retry_max_seconds=0,
    )


def test_enqueue_is_idempotent_within_scope_and_isolated_between_scopes(runtime_scope) -> None:
    tenant, organization = runtime_scope
    key = f"idempotent-{uuid.uuid4()}"
    first = _enqueue(tenant, organization, key=key, payload={"value": 1})
    repeated = _enqueue(tenant, organization, key=key, payload={"value": 1})
    assert first.created is True
    assert repeated.created is False
    assert repeated.task.task_id == first.task.task_id

    with pytest.raises(IdempotencyConflict):
        _enqueue(tenant, organization, key=key, payload={"value": 2})

    foreign_org = f"{organization}-foreign"
    try:
        foreign = _enqueue(tenant, foreign_org, key=key, payload={"value": 2})
        assert foreign.created is True
        assert foreign.task.task_id != first.task.task_id
    finally:
        with connect() as conn:
            conn.execute(
                "DELETE FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s",
                (tenant, foreign_org),
            )


def test_claim_uses_skip_locked_and_never_crosses_scope(runtime_scope) -> None:
    tenant, organization = runtime_scope
    first = _enqueue(tenant, organization, key=f"claim-a-{uuid.uuid4()}").task
    second = _enqueue(tenant, organization, key=f"claim-b-{uuid.uuid4()}").task
    foreign_org = f"{organization}-foreign"
    try:
        foreign = _enqueue(tenant, foreign_org, key=f"claim-c-{uuid.uuid4()}").task
        conn_a = connect()
        conn_b = connect()
        try:
            claimed_a = claim_task(
                conn_a,
                tenant_id=tenant,
                organization_id=organization,
                worker_id="worker-a",
                lease_seconds=30,
            )
            claimed_b = claim_task(
                conn_b,
                tenant_id=tenant,
                organization_id=organization,
                worker_id="worker-b",
                lease_seconds=30,
            )
            assert claimed_a is not None and claimed_b is not None
            assert {claimed_a.task_id, claimed_b.task_id} == {first.task_id, second.task_id}
            assert foreign.task_id not in {claimed_a.task_id, claimed_b.task_id}
        finally:
            conn_a.rollback()
            conn_b.rollback()
            conn_a.close()
            conn_b.close()
    finally:
        with connect() as conn:
            conn.execute(
                "DELETE FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s",
                (tenant, foreign_org),
            )


def test_worker_completes_noop_and_publishes_scoped_health(runtime_scope) -> None:
    tenant, organization = runtime_scope
    task = _enqueue(tenant, organization, key=f"noop-{uuid.uuid4()}").task
    worker = RuntimeWorker(_config(tenant, organization))

    assert worker.run_once() is True
    with connect() as conn:
        completed = get_task(
            conn,
            task.task_id,
            tenant_id=tenant,
            organization_id=organization,
        )
        healthy, report = worker_is_healthy(
            conn,
            tenant_id=tenant,
            organization_id=organization,
            worker_id="worker-a",
            max_age_seconds=30,
        )
    assert completed is not None
    assert completed.state == "succeeded"
    assert completed.attempt == 1
    assert completed.result == {
        "ok": True,
        "taskType": "system.noop",
        "requestSha256": completed.request_sha256,
    }
    assert healthy is True
    assert report["currentTaskId"] is None
    assert report["tenantId"] == tenant
    assert report["organizationId"] == organization


def test_retry_then_success_and_stale_lease_is_fenced(runtime_scope) -> None:
    tenant, organization = runtime_scope
    calls = 0

    def flaky(task):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TaskExecutionError("temporary_failure", retryable=True)
        return {"ok": True, "taskType": task.task_type}

    task = _enqueue(
        tenant,
        organization,
        key=f"retry-{uuid.uuid4()}",
        max_attempts=2,
    ).task
    worker = RuntimeWorker(
        _config(tenant, organization),
        handlers={"system.noop": flaky},
    )
    assert worker.run_once() is True
    assert worker.run_once() is True
    with connect() as conn:
        current = get_task(
            conn,
            task.task_id,
            tenant_id=tenant,
            organization_id=organization,
        )
    assert current is not None
    assert current.state == "succeeded"
    assert current.attempt == 2

    leased = _enqueue(
        tenant,
        organization,
        key=f"lease-{uuid.uuid4()}",
        max_attempts=2,
    ).task
    with connect() as conn:
        old_claim = claim_task(
            conn,
            tenant_id=tenant,
            organization_id=organization,
            worker_id="old-worker",
            lease_seconds=30,
        )
    assert old_claim is not None and old_claim.task_id == leased.task_id
    with connect() as conn:
        conn.execute(
            """UPDATE runtime_tasks
                  SET lease_expires_at=clock_timestamp() - interval '1 second'
                WHERE task_id=%s""",
            (leased.task_id,),
        )
        recovered = recover_expired_tasks(
            conn,
            tenant_id=tenant,
            organization_id=organization,
        )
    assert recovered.retryable == 1
    with connect() as conn:
        new_claim = claim_task(
            conn,
            tenant_id=tenant,
            organization_id=organization,
            worker_id="new-worker",
            lease_seconds=30,
        )
    assert new_claim is not None and new_claim.lease_token != old_claim.lease_token
    with connect() as conn:
        assert succeed_task(conn, old_claim, result={"ok": True}) is False
        assert succeed_task(conn, new_claim, result={"ok": True}) is True


def test_worker_health_is_scoped_and_detects_stale_heartbeat(runtime_scope) -> None:
    tenant, organization = runtime_scope
    with connect() as conn:
        record_worker_heartbeat(
            conn,
            tenant_id=tenant,
            organization_id=organization,
            worker_id="health-worker",
            status="running",
        )
    with connect() as conn:
        missing, _ = worker_is_healthy(
            conn,
            tenant_id=tenant,
            organization_id=f"{organization}-foreign",
            worker_id="health-worker",
            max_age_seconds=30,
        )
        conn.execute(
            """UPDATE runtime_worker_heartbeats
                  SET heartbeat_at=clock_timestamp() - interval '2 minutes'
                WHERE tenant_id=%s AND organization_id=%s AND worker_id=%s""",
            (tenant, organization, "health-worker"),
        )
    with connect() as conn:
        stale, report = worker_is_healthy(
            conn,
            tenant_id=tenant,
            organization_id=organization,
            worker_id="health-worker",
            max_age_seconds=30,
        )
    assert missing is False
    assert stale is False
    assert report["fresh"] is False


def test_object_store_preflight_is_read_only_and_requires_immutability() -> None:
    pytest.importorskip("botocore")

    class FakeClient:
        closed = False

        def get_bucket_versioning(self, *, Bucket):
            assert Bucket == "snapshot-bucket"
            return {"Status": "Enabled"}

        def get_object_lock_configuration(self, *, Bucket):
            assert Bucket == "snapshot-bucket"
            return {
                "ObjectLockConfiguration": {
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {
                        "DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}
                    },
                }
            }

        def close(self):
            self.closed = True

    client = FakeClient()
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return client

    task = SimpleNamespace(task_type="object_store.preflight")
    report = object_store_preflight(
        task,
        environ={
            "TKOS_OBJECT_STORE_ENDPOINT": "http://minio:9000",
            "TKOS_OBJECT_STORE_BUCKET": "snapshot-bucket",
            "TKOS_OBJECT_STORE_ACCESS_KEY": "app-user",
            "TKOS_OBJECT_STORE_SECRET_KEY": "test-only-secret",
            "TKOS_OBJECT_STORE_REGION": "us-east-1",
            "TKOS_OBJECT_STORE_VERIFY_TLS": "false",
        },
        client_factory=factory,
    )
    assert report["ok"] is True
    assert report["objectLockEnabled"] is True
    assert report["retentionDays"] == 30
    assert captured["verify_tls"] is False
    assert client.closed is True
