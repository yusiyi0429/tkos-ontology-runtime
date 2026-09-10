"""Execute real selected-source worker code, without injecting protocol support.

The held-claim mode commits a real old claim, then pauses with the actual task
in process memory BEFORE entering governance_dispatch. It holds no database
transaction while root performs the isolated migration. This does not model
revoking an HTTP request that already passed its admission fence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit


def write_private(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True, default=str)
        stream.write("\n")
    temporary.replace(path)


def selected_module(source: Path, name: str, provenance: dict):
    module = importlib.import_module(name)
    actual = Path(module.__file__).resolve()
    if not actual.is_relative_to(source):
        raise RuntimeError("selected worker source was not imported")
    provenance[name] = {"path": str(actual), "sha256": hashlib.sha256(actual.read_bytes()).hexdigest()}
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("held-claim", "dispatch", "worker-once"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--wait-seconds", type=int, default=7200)
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / "memory_service_runtime/worker.py").is_file():
        parser.error("worker source is unavailable")
    if args.operation == "held-claim" and not all((args.task_file, args.ready, args.release)):
        parser.error("held-claim requires task-file, ready and release")
    if args.operation == "dispatch" and not args.task_file:
        parser.error("dispatch requires task-file")
    if not 1 <= args.wait_seconds <= 7200:
        parser.error("wait-seconds must be bounded")
    if args.ready and args.ready.exists() or args.release and args.release.exists():
        parser.error("refusing stale readiness or release marker")
    sys.path.insert(0, str(source))
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    connection = conninfo_to_dict(os.environ.get("DATABASE_URL", ""))
    if connection.get("host") not in {"127.0.0.1", "localhost", "::1"} or not connection.get("dbname", "").startswith("tkos_a1_"):
        raise ValueError("worker probe requires the one-off loopback database")
    if args.legacy and any("runtime_write_capability" in value for value in (
            connection.get("options", ""), os.environ.get("PGOPTIONS", ""))):
        raise ValueError("a real old worker must not receive the new capability")
    endpoint = urlsplit(os.environ.get("GOVERNED_EFFECT_URL", ""))
    if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path != "/effects"):
        raise ValueError("worker probes only call their loopback receiver")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        role = conn.execute("""SELECT r.rolsuper,r.rolbypassrls,
            r.oid=(SELECT relowner FROM pg_class WHERE oid='runtime_tasks'::regclass)
            FROM pg_roles r WHERE r.rolname=current_user""").fetchone()
        if role is None or any(role):
            raise ValueError("worker process must use the ordinary application role")
        capability = conn.execute("SELECT current_setting('app.runtime_write_capability',true)").fetchone()[0]
        if args.legacy and capability:
            raise ValueError("old worker connection was contaminated with a new capability")
    provenance = {}
    repository = selected_module(source, "memory_service_runtime.repository", provenance)
    effects = selected_module(source, "memory_service_runtime.governed.effects", provenance)
    selected_module(source, "memory_service_runtime.governed.db", provenance)
    handlers = selected_module(source, "memory_service_runtime.handlers", provenance)
    result = {"operation": args.operation, "source": str(source), "legacy": args.legacy,
              "provenance": provenance, "database_role_is_app": True,
              "capability_injected_by_probe": False, "successful_dispatch": False,
              "contract_a1_accepted": False}
    try:
        if args.operation == "worker-once":
            worker = selected_module(source, "memory_service_runtime.worker", provenance)
            config = selected_module(source, "memory_service_runtime.config", provenance)
            result["did_work"] = worker.RuntimeWorker(config.RuntimeConfig.from_env()).run_once()
            result["completed"] = True
        else:
            if args.operation == "held-claim":
                with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
                    task = repository.claim_task(conn,
                        tenant_id=os.environ["MEMORY_TENANT"], organization_id=os.environ["MEMORY_ORG"],
                        worker_id="independent-a1-old-held", lease_seconds=7200)
                if task is None or task.task_type != "governance.dispatch":
                    raise RuntimeError("a real governed effect task was not claimed")
                write_private(args.task_file, asdict(task))
                write_private(args.ready, {**result, "phase": "claimed_before_dispatch",
                    "task_id": task.task_id, "database_transaction_open": False})
                deadline = time.monotonic() + args.wait_seconds
                while not args.release.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("held worker was not released")
                    time.sleep(0.1)
            else:
                task = repository.RuntimeTask.from_row(json.loads(args.task_file.read_text()))
            result["task_id"] = task.task_id
            result["dispatch_result"] = effects.governance_dispatch(task)
            result["successful_dispatch"] = True
            result["completed"] = True
    except handlers.TaskExecutionError as exc:
        result.update(completed=False, error_kind="TaskExecutionError", error_code=exc.code,
                      retryable=exc.retryable)
    except psycopg.Error as exc:
        result.update(completed=False, error_kind=type(exc).__name__, sqlstate=exc.sqlstate)
    except Exception as exc:
        # Exception messages can contain DSNs, so only expose their class.
        result.update(completed=False, error_kind=type(exc).__name__, unexpected_error=True)
    write_private(args.result, result)
    print(json.dumps({"result_file": str(args.result), "completed": result.get("completed", False)}), flush=True)


if __name__ == "__main__":
    main()
