#!/usr/bin/env python3
"""Create the target-only production configuration from the current Memory scope.

Run this as root on the target. Existing configuration or Compose volumes cause a
fail-closed result; the script never prints or copies a secret off the target.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
PRIVATE = ROOT / "private"
STATE = ROOT / "deployment-state.json"
REQUIRED_SOURCE = (
    "MEMORY_TENANT",
    "MEMORY_ORG",
    "ADAPTER_AUTH",
    "VIEWER_USER_ID",
    "MEMORY_EMBEDDING_API_KEY",
    "MEMORY_EMBEDDING_BASE_URL",
    "MEMORY_EMBEDDING_MODEL",
)


def run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError("TARGET_COMMAND_FAILED")
    return result.stdout


def inspect_environment(container: str) -> dict[str, str]:
    raw = run(["docker", "inspect", container, "--format", "{{json .Config.Env}}"])
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("SOURCE_ENVIRONMENT_INVALID") from exc
    result: dict[str, str] = {}
    for item in values:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key] = value
    for key in REQUIRED_SOURCE:
        value = result.get(key, "")
        if not value or value != value.strip() or "\n" in value or "\r" in value:
            raise RuntimeError("SOURCE_ENVIRONMENT_INCOMPLETE:" + key)
    return result


def private_write(path: Path, value: str, *, uid: int = 0, gid: int = 0) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    os.fchmod(fd, 0o400)
    os.fchown(fd, uid, gid)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)


def dotenv(value: str) -> str:
    if not value or "'" in value or "\n" in value or "\r" in value:
        raise RuntimeError("DOTENV_VALUE_INVALID")
    return "'" + value + "'"


def assert_new_target(port: int) -> None:
    if ENV_FILE.exists() or PRIVATE.exists() or STATE.exists():
        raise RuntimeError("TARGET_CONFIGURATION_ALREADY_EXISTS")
    volumes = run([
        "docker", "volume", "ls", "-q", "--filter",
        "label=com.docker.compose.project=tkos-runtime-prod",
    ])
    if volumes.strip():
        raise RuntimeError("TARGET_VOLUMES_EXIST_WITHOUT_CONFIGURATION")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError("RUNTIME_PORT_IN_USE") from exc


def create(args: argparse.Namespace) -> dict:
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    assert_new_target(args.port)
    source = inspect_environment(args.source_container)
    PRIVATE.mkdir(mode=0o700)
    os.chown(PRIVATE, 10001, 10001)
    service_token = secrets.token_urlsafe(48)
    minio_access = "runtime-" + secrets.token_hex(8)
    minio_secret = secrets.token_hex(32)
    private_write(PRIVATE / "runtime-service-token", service_token, uid=10001, gid=10001)
    private_write(PRIVATE / "minio-app-access-key", minio_access, uid=10001, gid=10001)
    private_write(PRIVATE / "minio-app-secret-key", minio_secret, uid=10001, gid=10001)
    app_password = secrets.token_hex(32)
    private_write(PRIVATE / "database-url",
                  f"postgresql://tkos_runtime_app:{app_password}@postgres:5432/tkos_runtime",
                  uid=10001, gid=10001)
    private_write(PRIVATE / "embedding-api-key", source["MEMORY_EMBEDDING_API_KEY"],
                  uid=10001, gid=10001)

    values = {
        "COMPOSE_PROJECT_NAME": "tkos-runtime-prod",
        "RUNTIME_HTTP_PORT": str(args.port),
        "RUNTIME_API_IMAGE": args.runtime_api_image,
        "RUNTIME_WORKER_IMAGE": args.runtime_worker_image,
        "POSTGRES_IMAGE": args.postgres_image,
        "MINIO_IMAGE": args.minio_image,
        "MINIO_MC_IMAGE": args.minio_mc_image,
        "POSTGRES_ADMIN_USER": "tkos_runtime_admin",
        "POSTGRES_ADMIN_PASSWORD": secrets.token_hex(32),
        "POSTGRES_OWNER_USER": "tkos_runtime_owner",
        "POSTGRES_OWNER_PASSWORD": secrets.token_hex(32),
        "POSTGRES_APP_USER": "tkos_runtime_app",
        "POSTGRES_APP_PASSWORD": app_password,
        "POSTGRES_DB": "tkos_runtime",
        "MEMORY_TENANT": source["MEMORY_TENANT"],
        "MEMORY_ORG": source["MEMORY_ORG"],
        "ADAPTER_AUTH": source["ADAPTER_AUTH"],
        "VIEWER_USER_ID": source["VIEWER_USER_ID"],
        "MEMORY_EMBEDDING_API_KEY": source["MEMORY_EMBEDDING_API_KEY"],
        "MEMORY_EMBEDDING_BASE_URL": source["MEMORY_EMBEDDING_BASE_URL"],
        "MEMORY_EMBEDDING_MODEL": source["MEMORY_EMBEDDING_MODEL"],
        "MEMORY_EMBEDDING_DIM": source.get("MEMORY_EMBEDDING_DIM", "2048"),
        "TKOS_NARRATIVE_COMPRESSION": "none",
        "TKOS_NARRATIVE_TIMEOUT_SECONDS": "20",
        "TKOS_NARRATIVE_SCOPE_ID": str(uuid4()),
        "TKOS_NARRATIVE_DOMAIN_ID": str(uuid4()),
        "TKOS_NARRATIVE_PRINCIPAL_ID": str(uuid4()),
        "TKOS_NARRATIVE_ASSIGNMENT_ID": str(uuid4()),
        "TKOS_NARRATIVE_CREDENTIAL_ID": str(uuid4()),
        "TKOS_NARRATIVE_POLICY_ID": str(uuid4()),
        "TKOS_NARRATIVE_POLICY_REVISION_ID": str(uuid4()),
        "MINIO_ROOT_USER": "runtime-root-" + secrets.token_hex(6),
        "MINIO_ROOT_PASSWORD": secrets.token_hex(32),
        "MINIO_APP_ACCESS_KEY": minio_access,
        "MINIO_APP_SECRET_KEY": minio_secret,
        "MINIO_APP_POLICY": "tkos-runtime-prod-v1",
        "MINIO_SNAPSHOT_BUCKET": "tkos-runtime-snapshots",
        "MINIO_SNAPSHOT_RETENTION": "30d",
        "DB_CONNECT_TIMEOUT": "5",
        "RUNTIME_WORKER_ID": "tkos-runtime-prod-worker-1",
        "RUNTIME_WORKER_POLL_SECONDS": "1",
    }
    private_write(ENV_FILE, "".join(f"{key}={dotenv(value)}\n" for key, value in values.items()))
    state = {
        "release_id": args.release_id,
        "runtime_port": args.port,
        "source_container": args.source_container,
        "scope_source": "existing-production-memory-container",
        "compression": "none",
        "business_facts_provisioned": False,
        "service_identity": "AGENT read/read_legacy_context only",
    }
    private_write(STATE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "configured": True, "release_id": args.release_id,
            "runtime_port": args.port, "secrets_printed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--runtime-api-image", required=True)
    parser.add_argument("--runtime-worker-image", required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--minio-image", required=True)
    parser.add_argument("--minio-mc-image", required=True)
    parser.add_argument("--source-container", default="tkos-memory-memory-service-1")
    parser.add_argument("--port", type=int, default=8030)
    args = parser.parse_args()
    print(json.dumps(create(args), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
