"""Independent HTTP acceptance client and sanitized evidence recorder.

This module does not import the implementation under test. Successful reports
are derived from assertions in the runner, never from an implementation flag.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any
import uuid

import httpx


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitized(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key.lower() in {
                "authorization", "token", "bearer_token", "credential", "password",
                "secret_key", "access_key", "credential_hash", "token_hash", "token_digest",
            }:
                out[key] = "[redacted]"
            elif key == "content_base64" and isinstance(item, str):
                raw = base64.b64decode(item)
                out[key] = {"sha256": sha256(raw), "bytes": len(raw)}
            else:
                out[key] = sanitized(item)
        return out
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    return value


class EvidenceLog:
    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "http-transcript.jsonl"
        self.lock = threading.Lock()
        self.counter = 0

    def record(self, record: dict) -> None:
        with self.lock:
            self.counter += 1
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sanitized({"sequence": self.counter, "at": utc_now(), **record}), ensure_ascii=False) + "\n")


class Client:
    def __init__(self, base_url: str, token: str, name: str, log: EvidenceLog):
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.log = log
        self.http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=35,
            trust_env=False,
        )

    def request(self, method: str, path: str, body=None, *, expected=200, headers=None):
        record = {"actor": self.name, "method": method, "path": path, "request": body}
        try:
            response = self.http.request(method, path, json=body, headers=headers)
        except httpx.TransportError as exc:
            self.log.record({**record, "transport_error": type(exc).__name__})
            raise
        if "application/json" in response.headers.get("content-type", ""):
            data = response.json()
        else:
            data = {"body_sha256": sha256(response.content), "body_bytes": len(response.content)}
        self.log.record({**record, "status": response.status_code, "response": data})
        codes = {expected} if isinstance(expected, int) else set(expected)
        assert response.status_code in codes, (
            f"{self.name}: {method} {path}: expected {sorted(codes)}, "
            f"got {response.status_code}: {str(data)[:1000]}"
        )
        return response

    def json(self, method: str, path: str, body=None, **kwargs):
        return self.request(method, path, body, **kwargs).json()

    def object(self, object_id: str, **kwargs):
        return self.json("GET", f"/v1/objects/{object_id}", **kwargs)

    def revision(self, object_id: str, revision_id: str, **kwargs):
        return self.json("GET", f"/v1/objects/{object_id}/revisions/{revision_id}", **kwargs)

    @staticmethod
    def command(action_type: str, params: dict, *, target=None, expected_versions=None, key=None):
        return {
            "action_type": action_type,
            "target": target,
            "expected_versions": expected_versions or [],
            "idempotency_key": key or f"acceptance-{uuid.uuid4()}",
            "reason": "Independent runtime acceptance with synthetic business data",
            "params": params,
        }

    def action(self, action_type: str, params: dict, *, target=None, expected_versions=None, key=None, **kwargs):
        body = self.command(action_type, params, target=target, expected_versions=expected_versions, key=key)
        self.last_action_command = body
        return self.json("POST", "/v1/actions", body, **kwargs)

    def close(self):
        self.http.close()


def error_code(response: dict) -> str:
    error = response.get("error", {})
    return error.get("code", "")


def assert_error(response: dict, code: str) -> None:
    assert error_code(response) == code, f"expected {code}, got {response}"
