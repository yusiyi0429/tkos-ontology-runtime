"""Allowlisted, infrastructure-only runtime task handlers."""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping

from memory_service_runtime import object_store
from memory_service_runtime.config import RuntimeConfigError, env_value
from memory_service_runtime.repository import RuntimeTask


class TaskExecutionError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


TaskHandler = Callable[[RuntimeTask], dict[str, Any]]


def system_noop(task: RuntimeTask) -> dict[str, Any]:
    """Prove queue claim/finalize semantics without touching business data."""
    return {
        "ok": True,
        "taskType": task.task_type,
        "requestSha256": task.request_sha256,
    }


def object_store_preflight(
    task: RuntimeTask,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read only MinIO/S3 immutability settings; never create or mutate a bucket."""
    source = os.environ if environ is None else environ
    try:
        bucket = env_value("TKOS_OBJECT_STORE_BUCKET", environ=source, required=True)
        connection = object_store.settings(source)
    except RuntimeConfigError as exc:
        raise TaskExecutionError("object_store_config_invalid", retryable=False) from exc

    try:
        client = (client_factory or object_store.create_client)(**connection)

        from memory_service.context_graph.snapshot_storage import probe_immutability

        probe = probe_immutability(bucket, client)
    except ModuleNotFoundError as exc:
        raise TaskExecutionError("object_store_dependency_missing", retryable=False) from exc
    except Exception as exc:
        raise TaskExecutionError("object_store_unavailable", retryable=True) from exc
    finally:
        if "client" in locals() and hasattr(client, "close"):
            client.close()

    if probe.api_errors:
        raise TaskExecutionError("object_store_unavailable", retryable=True)
    if not probe.passed:
        raise TaskExecutionError("object_store_immutability_gate_failed", retryable=False)
    return {
        "ok": True,
        "taskType": task.task_type,
        "bucket": probe.bucket,
        "versioningEnabled": probe.versioning_enabled,
        "objectLockEnabled": probe.object_lock_enabled,
        "retentionMode": probe.retention_mode,
        "retentionDays": probe.retention_days,
        "retentionYears": probe.retention_years,
    }


def default_handlers() -> dict[str, TaskHandler]:
    from memory_service_runtime.governed.effects import governance_dispatch

    return {
        "system.noop": system_noop,
        "object_store.preflight": object_store_preflight,
        "governance.dispatch": governance_dispatch,
    }
