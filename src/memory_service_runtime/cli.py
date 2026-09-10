"""Console entry points for the worker, its probe, and local operational smoke."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time

import psycopg

from memory_service_runtime.config import RuntimeConfig, RuntimeConfigError
from memory_service_runtime.governed import db
from memory_service_runtime.handlers import default_handlers
from memory_service_runtime.repository import (
    IdempotencyConflict,
    TERMINAL_STATES,
    enqueue_task,
    get_task,
    worker_is_healthy,
)
from memory_service_runtime.worker import RuntimeWorker, install_signal_handlers


def _connect(config: RuntimeConfig) -> psycopg.Connection:
    return psycopg.connect(
        config.database_url,
        connect_timeout=config.db_connect_timeout,
        application_name="tkos-memory-runtime-cli",
    )


def worker_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        config = RuntimeConfig.from_env()
        stop = threading.Event()
        install_signal_handlers(stop)
        RuntimeWorker(config).run_forever(stop)
    except RuntimeConfigError as exc:
        print(f"worker configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def health_main() -> None:
    try:
        config = RuntimeConfig.from_env()
        with _connect(config) as conn:
            healthy, report = worker_is_healthy(
                conn,
                tenant_id=config.tenant_id,
                organization_id=config.organization_id,
                worker_id=config.worker_id,
                max_age_seconds=config.health_max_age_seconds,
            )
    except (RuntimeConfigError, psycopg.Error, OSError) as exc:
        report = {"ok": False, "code": exc.__class__.__name__}
        healthy = False
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(0 if healthy else 1)


def enqueue_main() -> None:
    parser = argparse.ArgumentParser(
        description="Enqueue an allowlisted infrastructure task (not a business Action)."
    )
    parser.add_argument("--type", required=True, choices=sorted(default_handlers()))
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--payload-json", default="{}")
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument(
        "--wait-seconds",
        "--wait",
        nargs="?",
        const=30.0,
        type=float,
        default=0.0,
        help="Wait for a terminal result; --wait without a value waits up to 30 seconds.",
    )
    args = parser.parse_args()

    try:
        config = RuntimeConfig.from_env()
        payload = json.loads(args.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("--payload-json 必须是 JSON 对象")
        max_attempts = args.max_attempts or config.default_max_attempts
        with _connect(config) as conn:
            # The 0018 dispatch fence requires the runtime capability on any
            # governance.dispatch insert; the CLI declares it like the worker,
            # but this is not governance authority — dispatch still re-checks
            # the immutable receipt, membership and current bindings.
            db.set_write_capability(conn)
            enqueued = enqueue_task(
                conn,
                tenant_id=config.tenant_id,
                organization_id=config.organization_id,
                task_type=args.type,
                idempotency_key=args.idempotency_key,
                payload=payload,
                max_attempts=max_attempts,
            )
        task = enqueued.task
        deadline = time.monotonic() + max(args.wait_seconds, 0.0)
        while task.state not in TERMINAL_STATES and time.monotonic() < deadline:
            time.sleep(0.2)
            with _connect(config) as conn:
                current = get_task(
                    conn,
                    task.task_id,
                    tenant_id=config.tenant_id,
                    organization_id=config.organization_id,
                )
            if current is None:
                raise RuntimeError("runtime task disappeared")
            task = current
        report = {
            "taskId": task.task_id,
            "taskType": task.task_type,
            "state": task.state,
            "attempt": task.attempt,
            "created": enqueued.created,
            "result": task.result,
            "errorCode": task.error_code,
        }
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
        if task.state == "failed":
            raise SystemExit(1)
        if args.wait_seconds > 0 and task.state != "succeeded":
            raise SystemExit(3)
    except (RuntimeConfigError, IdempotencyConflict, ValueError, psycopg.Error) as exc:
        print(f"enqueue failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


__all__ = ("enqueue_main", "health_main", "worker_main")
