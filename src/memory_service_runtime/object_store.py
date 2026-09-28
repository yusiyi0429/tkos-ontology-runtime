"""The one S3/MinIO client configuration, shared by evidence storage and the worker preflight."""
from __future__ import annotations

import os
from typing import Any, Mapping

from memory_service_runtime.config import RuntimeConfigError, env_value


def _boolean(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise RuntimeConfigError(f"{name} 必须是 true/false")


def settings(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Connection settings from the environment; raises RuntimeConfigError, never echoing values."""
    source = os.environ if environ is None else environ
    return {
        "endpoint": env_value("TKOS_OBJECT_STORE_ENDPOINT", environ=source, required=True),
        "region": env_value("TKOS_OBJECT_STORE_REGION", environ=source, default="us-east-1"),
        "access_key": env_value("TKOS_OBJECT_STORE_ACCESS_KEY", environ=source, required=True),
        "secret_key": env_value("TKOS_OBJECT_STORE_SECRET_KEY", environ=source, required=True),
        "verify_tls": _boolean(source.get("TKOS_OBJECT_STORE_VERIFY_TLS", "true"),
                               name="TKOS_OBJECT_STORE_VERIFY_TLS"),
    }


def create_client(*, endpoint: str, region: str, access_key: str, secret_key: str, verify_tls: bool):
    # Imported here so a missing optional extra surfaces as ModuleNotFoundError at use.
    from botocore.config import Config
    from botocore.session import get_session

    return get_session().create_client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        verify=verify_tls,
        # Two attempts in total: the first call and one retry.
        config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5,
                      retries={"total_max_attempts": 2, "mode": "standard"},
                      s3={"addressing_style": "path"}),
    )
