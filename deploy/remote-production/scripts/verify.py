#!/usr/bin/env python3
"""Sanitized target verification for the loopback production candidate."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def dotenv() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) < 2 or value[0] != "'" or value[-1] != "'":
            raise RuntimeError("ENV_FORMAT_INVALID")
        result[key] = value[1:-1]
    return result


def command(argv: list[str], *, timeout: int = 120) -> str:
    proc = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True,
                          timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError("TARGET_COMMAND_FAILED")
    return proc.stdout


def compose(*argv: str, timeout: int = 120) -> str:
    return command(["docker", "compose", "--project-directory", str(ROOT),
                    "--env-file", str(ROOT / ".env"), "-f", str(ROOT / "compose.yaml"),
                    *argv], timeout=timeout)


def last_json(raw: str) -> dict:
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("JSON_RESULT_MISSING")


def http_bytes(url: str, *, authorization: str | None = None,
               body: dict | None = None, expected: int = 200,
               timeout: int = 70) -> bytes:
    headers = {"Accept": "application/json"}
    data = None
    if authorization is not None:
        headers["Authorization"] = authorization
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=timeout) as response:
            status = response.status
            raw = response.read(2_000_001)
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(2_000_001)
    except URLError as exc:
        raise RuntimeError("HTTP_UNAVAILABLE") from exc
    if status != expected or len(raw) > 2_000_000:
        raise RuntimeError(f"HTTP_STATUS_UNEXPECTED:{status}")
    return raw


def request(url: str, *, token: str | None = None, authorization: str | None = None,
            body: dict | None = None, expected: int = 200, timeout: int = 70) -> dict:
    if token is not None:
        if authorization is not None:
            raise RuntimeError("HTTP_AUTH_AMBIGUOUS")
        authorization = "Bearer " + token
    raw = http_bytes(url, authorization=authorization, body=body,
                     expected=expected, timeout=timeout)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("HTTP_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise RuntimeError("HTTP_JSON_INVALID")
    return value


def task(task_type: str, key: str) -> dict:
    raw = compose("exec", "-T", "runtime-worker", "tkos-memory-task-enqueue",
                  "--type", task_type, "--idempotency-key", key,
                  "--payload-json", "{}", "--wait", "30", timeout=60)
    result = last_json(raw)
    if result.get("state") != "succeeded":
        raise RuntimeError("WORKER_TASK_FAILED")
    return {"type": task_type, "state": "succeeded", "attempt": result.get("attempt")}


def verify(restart: bool) -> dict:
    values = dotenv()
    port = values["RUNTIME_HTTP_PORT"]
    base = f"http://127.0.0.1:{port}"
    release = json.loads((ROOT / "deployment-state.json").read_text())["release_id"]
    if restart:
        compose("restart", "runtime-api", "runtime-worker", timeout=120)
        time.sleep(4)
    health = request(base + "/healthz")
    if health.get("ok") is not True or health.get("narrative") is not True:
        raise RuntimeError("HEALTH_INVALID")
    openapi = request(base + "/openapi.json")
    schemas = openapi.get("components", {}).get("schemas", {})
    if "NarrativeResponse" not in schemas:
        raise RuntimeError("OPENAPI_NARRATIVE_MISSING")
    basic = "Basic " + base64.b64encode(values["ADAPTER_AUTH"].encode("utf-8")).decode("ascii")
    adapter_health = request(base + "/healthz", authorization=basic)
    if (adapter_health.get("ok") is not True or adapter_health.get("db") is not True
            or adapter_health.get("embedding") is not True):
        raise RuntimeError("LEGACY_ADAPTER_HEALTH_INVALID")
    static = http_bytes(base + "/api/v1/context/static?viewer=cutover",
                        authorization=basic, timeout=30)
    if not static.strip() or values["ADAPTER_AUTH"].encode("utf-8") in static:
        raise RuntimeError("LEGACY_STATIC_CONTEXT_INVALID")
    request(base + "/v1/context-graph/narrative", body={"query": "TKOS 是什么"},
            expected=401)

    tasks = [
        task("system.noop", "production-verify-noop-" + release),
        task("object_store.preflight", "production-verify-object-store-" + release),
    ]
    before = last_json(compose("--profile", "ops", "run", "--rm", "--no-deps",
                               "db-admin", "fingerprint"))
    token = (ROOT / "private/runtime-service-token").read_text(encoding="utf-8").strip()
    narrative = request(
        base + "/v1/context-graph/narrative",
        token=token,
        body={"query": "TKOS 是什么", "include_raw": True,
              "tenant": values["MEMORY_TENANT"], "org": values["MEMORY_ORG"]},
    )
    after = last_json(compose("--profile", "ops", "run", "--rm", "--no-deps",
                              "db-admin", "fingerprint"))
    if before["sha256"] != after["sha256"]:
        raise RuntimeError("NARRATIVE_MUTATED_AUTHORITATIVE_DATA")
    provenance = narrative.get("provenance", {})
    if (not narrative.get("narrative") or narrative.get("tenant") != values["MEMORY_TENANT"]
            or narrative.get("org") != values["MEMORY_ORG"]
            or provenance.get("scope_id") != values["TKOS_NARRATIVE_SCOPE_ID"]
            or provenance.get("domain_id") != values["TKOS_NARRATIVE_DOMAIN_ID"]
            or provenance.get("compression_mode") != "none"
            or provenance.get("compression_applied") is not False
            or not provenance.get("legacy", {}).get("generation_id")):
        raise RuntimeError("NARRATIVE_CONTRACT_INVALID")
    database = last_json(compose("--profile", "ops", "run", "--rm", "--no-deps",
                                 "db-admin", "verify"))
    if database.get("fresh_worker_heartbeats", 0) < 1:
        raise RuntimeError("WORKER_HEARTBEAT_MISSING")
    containers = {}
    for service in ("postgres", "minio", "runtime-api", "runtime-worker"):
        container_id = compose("ps", "-q", service).strip()
        if not container_id:
            raise RuntimeError("CONTAINER_MISSING:" + service)
        info = json.loads(command(["docker", "inspect", container_id]))[0]
        health_state = info["State"].get("Health", {}).get("Status")
        if not info["State"]["Running"] or health_state not in (None, "healthy"):
            raise RuntimeError("CONTAINER_UNHEALTHY:" + service)
        containers[service] = health_state or "running"
    return {
        "ok": True,
        "release_id": release,
        "restart_verified": restart,
        "containers": containers,
        "unauthorized_narrative": 401,
        "authorized_narrative": 200,
        "legacy_adapter_health": 200,
        "legacy_static_context": 200,
        "narrative_chars": narrative["narrative_chars"],
        "legacy_generation_present": True,
        "compression": "none",
        "read_only_fingerprint_equal": True,
        "worker_tasks": tasks,
        "database": database,
        "secrets_printed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.restart), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
