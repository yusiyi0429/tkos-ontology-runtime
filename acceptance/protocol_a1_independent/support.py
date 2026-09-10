"""Private process control and public, independent evidence for A1.

No infra lifecycle or migration is started on import. Commands use argument
arrays, application traffic receives no owner/admin DSN, logs are private until
redacted. The caller/root coordinates database lifecycle.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.conninfo import conninfo_to_dict

from acceptance.runtime.client import Client, EvidenceLog, sanitized
from acceptance.runtime.sql_oracle import snapshot_scope, assert_application_role

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def public_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitized(value), ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def wait(predicate, *, timeout: float = 25) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("independent acceptance condition timed out")


def source_manifest(source: Path) -> dict[str, str]:
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.rglob("*")) if path.is_file()
            and path.suffix not in {".pyc", ".pyo"} and "__pycache__" not in path.parts}


def safe_traceback_frames(error: BaseException) -> list[dict]:
    """Code coordinates only: never format exception values, source or locals."""
    import traceback
    return [{"path": frame.f_code.co_filename, "line": line, "function": frame.f_code.co_name}
            for frame, line in traceback.walk_tb(error.__traceback__)]


class CaseBook:
    """All required subchecks must be recorded; missing work is never a PASS."""

    def __init__(self, output: Path, matrix: dict[str, list[str]]):
        self.output = output
        self.matrix = matrix
        self.cases = {key: {"id": key, "status": "not_run", "checks": {}, "evidence": []}
                      for key in matrix}
        self.started_at = now()

    def check(self, case: str, name: str, passed: bool, *, evidence: Any = None) -> None:
        if case not in self.matrix or name not in self.matrix[case]:
            raise ValueError("independent check is not in the frozen required matrix")
        if name in self.cases[case]["checks"]:
            raise ValueError("independent check cannot be silently replaced")
        self.cases[case]["checks"][name] = {"passed": bool(passed), "evidence": evidence}
        self._status(case)
        self.save()
        if not passed:
            raise AssertionError(f"{case}/{name} failed")

    def failure(self, case: str, error: str) -> None:
        self.cases[case]["error"] = error
        self.cases[case]["status"] = "failed"
        self.save()

    def _status(self, case: str) -> None:
        if self.cases[case]["status"] == "failed" or "error" in self.cases[case]:
            self.cases[case]["status"] = "failed"
            return
        checks = self.cases[case]["checks"]
        if any(not item["passed"] for item in checks.values()):
            self.cases[case]["status"] = "failed"
        elif set(checks) == set(self.matrix[case]):
            self.cases[case]["status"] = "passed"
        elif checks:
            self.cases[case]["status"] = "incomplete"

    def save(self, **extra) -> dict:
        passed = sum(c["status"] == "passed" for c in self.cases.values())
        failed = sum(c["status"] == "failed" for c in self.cases.values())
        result = {**extra, "scope": "runtime-a1", "started_at": self.started_at, "updated_at": now(),
                  "contract_a1_accepted": passed == 14 and len(self.cases) == 14,
                  "runtime_accepted": False, "a2_status": "not_implemented",
                  "a3_status": "not_implemented", "released": False, "deployed": False,
                  "mandatory_cases": 14, "passed": passed, "failed": failed,
                  "not_complete": len(self.cases) - passed - failed,
                  "required_checks": self.matrix, "cases": list(self.cases.values())}
        public_json(self.output / "report.json", result)
        return result


class Environment:
    def __init__(self, path: Path):
        self.path = path
        self.values = json.loads(path.read_text())
        for name in ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL"):
            if name not in self.values:
                raise ValueError(f"private environment lacks {name}")
            info = conninfo_to_dict(self.values[name])
            if info.get("host") not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("independent environment must use loopback PostgreSQL")
            database_name = info.get("dbname", "")
            if database_name != "tkos_runtime_acceptance" and not database_name.startswith("tkos_a1_"):
                raise ValueError("independent environment must use an explicit acceptance database")
        app = conninfo_to_dict(self.values["APP_DATABASE_URL"])
        owner = conninfo_to_dict(self.values["MIGRATION_DATABASE_URL"])
        if (app.get("host"), app.get("port", "5432"), app.get("dbname")) != (
                owner.get("host"), owner.get("port", "5432"), owner.get("dbname")):
            raise ValueError("application and migration database endpoints differ")
        if app.get("user") == owner.get("user"):
            raise ValueError("application and migration identities must be separate")
        endpoint = urlsplit(self.values.get("TKOS_OBJECT_STORE_ENDPOINT", ""))
        if (endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
                or endpoint.scheme not in {"http", "https"} or endpoint.username is not None
                or endpoint.password is not None or endpoint.query or endpoint.fragment):
            raise ValueError("independent object storage must use a loopback URL without userinfo")
        self.values["DATABASE_URL"] = self.values["APP_DATABASE_URL"]

    def child(self, *, owner: bool = False, updates: dict | None = None) -> dict[str, str]:
        result = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "HOME") if k in os.environ}
        for key, value in self.values.items():
            if isinstance(value, (str, int, float, bool)) and (
                key in {"DATABASE_URL", "MEMORY_TENANT", "MEMORY_ORG", "DB_CONNECT_TIMEOUT"}
                or key.startswith(("TKOS_OBJECT_STORE_", "RUNTIME_", "GOVERNED_"))
            ):
                result[key] = str(value)
        result["DATABASE_URL"] = self.values["MIGRATION_DATABASE_URL" if owner else "APP_DATABASE_URL"]
        result["PYTHONUNBUFFERED"] = "1"
        result["PYTHONDONTWRITEBYTECODE"] = "1"
        if updates:
            result.update({key: str(value) for key, value in updates.items()})
        return result

    def redact(self, text: str) -> str:
        for key, value in self.values.items():
            if isinstance(value, str) and any(marker in key.upper() for marker in
                    ("TOKEN", "SECRET", "PASSWORD", "KEY", "DATABASE_URL")):
                text = text.replace(value, "[REDACTED]")
        return text


class Harness:
    def __init__(self, env_file: Path, output: Path, private: Path):
        self.env = Environment(env_file)
        self.output, self.private = output, private
        output.mkdir(parents=True, exist_ok=True)
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        private.chmod(0o700)
        self.processes: list[tuple[subprocess.Popen, Path, str]] = []
        self.log = EvidenceLog(output)
        self.control = private / "control"
        self.control.mkdir(mode=0o700, exist_ok=True)
        self.control_key = secrets.token_hex(32)
        self.key_file = private / "control.key"
        self.key_file.write_text(self.control_key)
        self.key_file.chmod(0o600)
        self.fixture_tokens: list[str] = []

    def spawn(self, name: str, args: list[str], *, owner: bool = False,
              updates: dict | None = None) -> subprocess.Popen:
        logfile = self.private / f"{name}-{uuid.uuid4().hex[:8]}.log"
        with logfile.open("wb") as stream:
            logfile.chmod(0o600)
            process = subprocess.Popen(args, cwd=ROOT, env=self.env.child(owner=owner, updates=updates),
                                       stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT)
        self.processes.append((process, logfile, name))
        return process

    def start_api(self, source: Path, *, old: bool = False, updates: dict | None = None) -> tuple[subprocess.Popen, str, Path]:
        if old:
            options = conninfo_to_dict(self.env.values["APP_DATABASE_URL"]).get("options", "")
            if "runtime_write_capability" in options or (updates and "PGOPTIONS" in updates):
                raise ValueError("old-source probe must not inherit a new protocol capability")
        ready = self.private / f"api-ready-{uuid.uuid4().hex}.json"
        args = [sys.executable, "-I", str(HERE / "api_process.py"), "--source", str(source),
                "--ready-file", str(ready)]
        if not old:
            args += ["--control-dir", str(self.control), "--key-file", str(self.key_file)]
        process = self.spawn("old-api" if old else "api", args, updates=updates)
        def started():
            if ready.exists():
                return json.loads(ready.read_text())
            if process.poll() is not None:
                raise AssertionError("API process exited before readiness; inspect redacted process log")
            return None
        info = wait(started)
        if not Path(info["application_source"]).resolve().is_relative_to(source.resolve()):
            raise AssertionError("API imported the wrong source checkout")
        return process, info["url"], ready

    def clients(self, url: str, fixture: dict) -> dict[str, Client]:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"}
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("acceptance bearer clients must use loopback URLs without userinfo")
        self.fixture_tokens.extend(actor["token"] for actor in fixture["actors"].values())
        return {alias: Client(url, actor["token"], alias, self.log)
                for alias, actor in fixture["actors"].items()}

    def headers(self, checkpoint: str, *, mode: str = "fail", token: str | None = None) -> dict:
        return {"X-Acceptance-Key": self.control_key, "X-Acceptance-Checkpoint": checkpoint,
                "X-Acceptance-Mode": mode, "X-Acceptance-Token": token or uuid.uuid4().hex}

    @staticmethod
    def stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def close(self) -> None:
        for process, logfile, name in reversed(self.processes):
            self.stop(process)
            text = self.env.redact(logfile.read_text(errors="replace"))
            text = text.replace(self.control_key, "[REDACTED]")
            for token in self.fixture_tokens:
                text = text.replace(token, "[REDACTED]")
            (self.output / logfile.name).write_text(text)

    @contextmanager
    def app_connection(self):
        # The independent observer is an A1-aware app-role reader. Declare the
        # reviewed connection capability explicitly without importing any guard.
        # This option is never put in the environment/DSN passed to old code.
        with psycopg.connect(self.env.values["APP_DATABASE_URL"], row_factory=dict_row,
                             options="-c app.runtime_write_capability=tkos-runtime-a1") as conn:
            yield conn

    def snapshot(self, fixture: dict) -> dict:
        with self.app_connection() as conn:
            return snapshot_scope(conn, fixture["scope_id"], fixture["tenant_id"], fixture["company_id"])

    def permissions(self) -> dict:
        with self.app_connection() as conn:
            return assert_application_role(conn)

    def sql(self, fixture: dict, statement: str, params: tuple = ()) -> list[dict]:
        with self.app_connection() as conn, conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (fixture["scope_id"],))
            return conn.execute(statement, params).fetchall()

    def storage_snapshot(self, scope_id: str) -> dict:
        """Independent S3 read: includes every version and delete marker in scope."""
        from botocore.config import Config
        from botocore.session import get_session
        env = self.env.values
        endpoint = env["TKOS_OBJECT_STORE_ENDPOINT"]
        if urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("acceptance object storage must use loopback")
        client = get_session().create_client("s3", endpoint_url=endpoint,
            region_name=env.get("TKOS_OBJECT_STORE_REGION", "us-east-1"),
            aws_access_key_id=env["TKOS_OBJECT_STORE_ACCESS_KEY"],
            aws_secret_access_key=env["TKOS_OBJECT_STORE_SECRET_KEY"],
            verify=str(env.get("TKOS_OBJECT_STORE_VERIFY_TLS", "true")).lower() not in {"false", "0", "no"},
            config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5,
                          retries={"total_max_attempts": 2}, s3={"addressing_style": "path"}))
        rows = []
        try:
            for page in client.get_paginator("list_object_versions").paginate(
                    Bucket=env["TKOS_OBJECT_STORE_BUCKET"], Prefix=f"gov/{scope_id}/"):
                for key, deleted in (("Versions", False), ("DeleteMarkers", True)):
                    for entry in page.get(key, []):
                        rows.append({"key": entry["Key"], "version_id": entry["VersionId"],
                                     "deleted": deleted, "etag": entry.get("ETag"),
                                     "bytes": entry.get("Size"), "last_modified": str(entry["LastModified"])})
        finally:
            client.close()
        rows.sort(key=lambda row: (row["key"], row["version_id"]))
        return {"versions": rows, "sha256": digest(rows), "count": len(rows)}

    def rejection(self, fixture: dict, client: Client, path: str, body: dict,
                  status: int, code: str, *, check_storage: bool = False, headers: dict | None = None) -> dict:
        before = self.snapshot(fixture)
        storage_before = self.storage_snapshot(fixture["scope_id"]) if check_storage else None
        response = client.json("POST", path, body, expected=status, headers=headers)
        assert response.get("error", {}).get("code") == code, "unexpected rejection code"
        after = self.snapshot(fixture)
        assert before == after, "rejected command changed durable governed state"
        if check_storage:
            assert storage_before == self.storage_snapshot(fixture["scope_id"]), "rejected upload wrote S3"
        return {"status": status, "code": code, "snapshot_sha256": digest(after),
                "storage_unchanged": True if check_storage else None}


def receiver_snapshot(path: Path) -> dict:
    """A missing ledger is a failure, not a zero-effects proof."""
    if not path.is_file():
        raise AssertionError("receiver ledger is missing")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return {"calls": conn.execute("SELECT count(*) FROM calls").fetchone()[0],
                "effects": conn.execute("SELECT count(*) FROM effects").fetchone()[0]}
