"""Process and evidence utilities for a real, isolated acceptance run."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import uuid

import httpx

from acceptance.runtime.client import EvidenceLog, utc_now


ROOT = Path(__file__).resolve().parents[2]


def port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def private_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    path.chmod(0o600)


def wait_until(predicate, *, timeout=20, message="condition not reached"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (httpx.TransportError, FileNotFoundError) as exc:
            last = type(exc).__name__
        time.sleep(0.05)
    raise AssertionError(f"{message}; last transient error={last}")


class Harness:
    def __init__(self, *, run_id=None):
        self.run_id = run_id or f"runtime-{uuid.uuid4().hex[:12]}"
        self.private = ROOT / ".runtime-acceptance" / self.run_id
        self.private.mkdir(parents=True, exist_ok=True)
        self.private.chmod(0o700)
        self.output = ROOT / "artifacts" / "runtime-acceptance" / self.run_id
        self.output.mkdir(parents=True, exist_ok=True)
        self.control = self.private / "control"
        self.control.mkdir(exist_ok=True)
        self.key_file = self.private / "control-key"
        self.key_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        self.key_file.chmod(0o600)
        self.env = json.loads((ROOT / ".runtime-acceptance" / "env.json").read_text())
        from acceptance.runtime.infra import python_path
        self.python = python_path()
        self.processes = []
        self.log = EvidenceLog(self.output)
        self.groups = []
        self.started_at = utc_now()
        self.complete = False

    def process_environment(self, updates=None):
        env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "HOME") if key in os.environ}
        env.update({
            key: str(value)
            for key, value in self.env.items()
            if isinstance(value, (str, int, float)) and (
                key in {"DATABASE_URL", "MEMORY_TENANT", "MEMORY_ORG", "DB_CONNECT_TIMEOUT"}
                or key.startswith(("TKOS_OBJECT_STORE_", "RUNTIME_", "GOVERNED_"))
            )
        })
        env.update({"PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT), "PYTHONUNBUFFERED": "1"})
        if updates:
            env.update({key: str(value) for key, value in updates.items()})
        return env

    def spawn(self, name: str, script: str, *args, updates=None):
        log_path = self.private / f"{name}.log"
        with log_path.open("ab") as output:
            log_path.chmod(0o600)
            process = subprocess.Popen(
                [self.python, str(ROOT / "acceptance/runtime" / script), *map(str, args)],
                cwd=ROOT,
                env=self.process_environment(updates),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        self.processes.append((name, process))
        return process

    def wait_http(self, url: str):
        def healthy():
            with httpx.Client(trust_env=False, timeout=1) as client:
                return client.get(url).status_code == 200
        wait_until(healthy, timeout=25, message=f"local helper did not become ready: {url}")

    @staticmethod
    def stop(process, *, kill=False):
        if process.poll() is not None:
            return
        if kill:
            process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=6)

    def stop_all(self):
        for _, process in reversed(self.processes):
            self.stop(process)

    def headers(self, checkpoint: str, mode: str, token: str):
        return {
            "X-Acceptance-Key": self.key_file.read_text(),
            "X-Acceptance-Checkpoint": checkpoint,
            "X-Acceptance-Mode": mode,
            "X-Acceptance-Token": token,
        }

    def reached(self, token: str, suffix="reached"):
        path = self.control / f"{token}.{suffix}.json"
        return wait_until(lambda: json.loads(path.read_text()) if path.exists() else None,
                          timeout=25, message=f"fault barrier {token} was not reached")

    def release(self, token: str):
        (self.control / f"{token}.release").touch()

    @contextmanager
    def group(self, name: str):
        result = {"name": name, "started_at": utc_now(), "status": "running"}
        self.groups.append(result)
        print(f"RUN {name}", flush=True)
        try:
            yield result
        except BaseException as exc:
            result.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:2000])
            print(f"FAIL {name}: {type(exc).__name__}: {str(exc)[:800]}", flush=True)
            raise
        else:
            result["status"] = "passed"
            print(f"PASS {name}", flush=True)
        finally:
            result["finished_at"] = utc_now()
            self.save_report()

    def save_report(self, **extra):
        report = {
            "test_run_id": self.run_id,
            "started_at": self.started_at,
            "updated_at": utc_now(),
            "environment": "isolated local HTTP API/Worker + PostgreSQL + versioned MinIO",
            "groups": self.groups,
            "status": "passed" if self.complete and self.groups and all(g["status"] == "passed" for g in self.groups) else "incomplete_or_failed",
            "runtime_accepted": bool(self.complete and self.groups and all(g["status"] == "passed" for g in self.groups)),
            "clark_integration_accepted": False,
            "released": False,
            "production_deployed": False,
            **extra,
        }
        path = self.output / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
