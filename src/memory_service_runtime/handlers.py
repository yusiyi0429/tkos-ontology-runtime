"""Allowlisted, infrastructure-only runtime task handlers."""
from __future__ import annotations

import os
from typing import Any, Callable, Mapping

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


def _boolean(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise RuntimeConfigError(f"{name} 必须是 true/false")


def object_store_preflight(
    task: RuntimeTask,
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Read only MinIO/S3 immutability settings; never create or mutate a bucket."""
    source = os.environ if environ is None else environ
    try:
        endpoint = env_value(
            "TKOS_OBJECT_STORE_ENDPOINT", environ=source, required=True
        )
        bucket = env_value("TKOS_OBJECT_STORE_BUCKET", environ=source, required=True)
        access_key = env_value(
            "TKOS_OBJECT_STORE_ACCESS_KEY", environ=source, required=True
        )
        secret_key = env_value(
            "TKOS_OBJECT_STORE_SECRET_KEY", environ=source, required=True
        )
        region = env_value(
            "TKOS_OBJECT_STORE_REGION", environ=source, default="us-east-1"
        )
        verify_tls = _boolean(
            source.get("TKOS_OBJECT_STORE_VERIFY_TLS", "true"),
            name="TKOS_OBJECT_STORE_VERIFY_TLS",
        )
    except RuntimeConfigError as exc:
        raise TaskExecutionError("object_store_config_invalid", retryable=False) from exc

    try:
        if client_factory is None:
            from botocore.config import Config
            from botocore.session import get_session

            config = Config(
                signature_version="s3v4",
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 2, "mode": "standard"},
                s3={"addressing_style": "path"},
            )
            client = get_session().create_client(
                "s3",
                endpoint_url=endpoint,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                verify=verify_tls,
                config=config,
            )
        else:
            client = client_factory(
                endpoint=endpoint,
                region=region,
                access_key=access_key,
                secret_key=secret_key,
                verify_tls=verify_tls,
            )

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
