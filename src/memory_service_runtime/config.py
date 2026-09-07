"""Environment configuration shared by the worker and its health CLI."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
from typing import Mapping


class RuntimeConfigError(ValueError):
    """The worker cannot start safely with its current configuration."""


def env_value(
    name: str,
    *,
    environ: Mapping[str, str] | None = None,
    required: bool = False,
    default: str = "",
) -> str:
    """Read ``NAME`` or ``NAME_FILE`` without ever including the value in errors."""
    source = os.environ if environ is None else environ
    inline = source.get(name, "").strip()
    file_name = source.get(f"{name}_FILE", "").strip()
    if inline and file_name:
        raise RuntimeConfigError(f"{name} 与 {name}_FILE 不能同时配置")
    if file_name:
        try:
            inline = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeConfigError(f"无法读取 {name}_FILE") from exc
    value = inline or default
    if required and not value:
        raise RuntimeConfigError(f"{name} 或 {name}_FILE 未配置")
    return value


def _integer(
    name: str,
    *,
    environ: Mapping[str, str],
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeConfigError(f"{name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise RuntimeConfigError(f"{name} 必须在 {minimum}..{maximum} 范围内")
    return value


def _float(
    name: str,
    *,
    environ: Mapping[str, str],
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeConfigError(f"{name} 必须是数字") from exc
    if not minimum <= value <= maximum:
        raise RuntimeConfigError(f"{name} 必须在 {minimum}..{maximum} 范围内")
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    tenant_id: str
    organization_id: str
    worker_id: str
    db_connect_timeout: int = 5
    poll_seconds: float = 1.0
    lease_seconds: int = 30
    retry_base_seconds: int = 2
    retry_max_seconds: int = 60
    default_max_attempts: int = 5
    health_max_age_seconds: int = 30

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "RuntimeConfig":
        source = dict(os.environ if environ is None else environ)
        worker_id = source.get("RUNTIME_WORKER_ID", "").strip() or socket.gethostname()
        if not 1 <= len(worker_id) <= 200:
            raise RuntimeConfigError("RUNTIME_WORKER_ID 长度必须在 1..200 范围内")
        tenant_id = source.get("MEMORY_TENANT", "").strip()
        organization_id = source.get("MEMORY_ORG", "").strip()
        if not 1 <= len(tenant_id) <= 200 or not 1 <= len(organization_id) <= 200:
            raise RuntimeConfigError("MEMORY_TENANT 和 MEMORY_ORG 长度必须在 1..200 范围内")
        return cls(
            database_url=env_value("DATABASE_URL", environ=source, required=True),
            tenant_id=tenant_id,
            organization_id=organization_id,
            worker_id=worker_id,
            db_connect_timeout=_integer(
                "DB_CONNECT_TIMEOUT", environ=source, default=5, minimum=1, maximum=60
            ),
            poll_seconds=_float(
                "RUNTIME_WORKER_POLL_SECONDS",
                environ=source,
                default=1.0,
                minimum=0.1,
                maximum=60.0,
            ),
            lease_seconds=_integer(
                "RUNTIME_TASK_LEASE_SECONDS",
                environ=source,
                default=30,
                minimum=5,
                maximum=3600,
            ),
            retry_base_seconds=_integer(
                "RUNTIME_TASK_RETRY_BASE_SECONDS",
                environ=source,
                default=2,
                minimum=1,
                maximum=3600,
            ),
            retry_max_seconds=_integer(
                "RUNTIME_TASK_RETRY_MAX_SECONDS",
                environ=source,
                default=60,
                minimum=1,
                maximum=86400,
            ),
            default_max_attempts=_integer(
                "RUNTIME_TASK_MAX_ATTEMPTS",
                environ=source,
                default=5,
                minimum=1,
                maximum=100,
            ),
            health_max_age_seconds=_integer(
                "RUNTIME_WORKER_HEALTH_MAX_AGE_SECONDS",
                environ=source,
                default=30,
                minimum=5,
                maximum=3600,
            ),
        )
