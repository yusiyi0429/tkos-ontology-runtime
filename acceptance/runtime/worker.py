"""Launch the real RuntimeWorker with an optional post-dispatch crash barrier.

The wrapper pauses only after the real governance.dispatch handler has returned,
before RuntimeWorker can acknowledge the task. The acceptance driver must also
check the independent receiver ledger before SIGKILL. Restart without the pause
option to exercise the real lease-expiry/retry path.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--pause-after-effect", metavar="TOKEN")
    parser.add_argument("--task-id", type=lambda value: str(uuid.UUID(value)))
    parser.add_argument("--pause-timeout", type=float, default=120.0)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.pause_after_effect and not TOKEN_PATTERN.fullmatch(args.pause_after_effect):
        parser.error("pause token must be a safe identifier of at most 80 characters")
    if args.task_id and not args.pause_after_effect:
        parser.error("--task-id requires --pause-after-effect")
    if not 1 <= args.pause_timeout <= 600:
        parser.error("pause timeout must be between 1 and 600 seconds")
    args.control_dir.mkdir(parents=True, exist_ok=True)

    from memory_service_runtime.config import RuntimeConfig
    from memory_service_runtime.handlers import TaskExecutionError, default_handlers
    from memory_service_runtime.worker import RuntimeWorker, install_signal_handlers

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = RuntimeConfig.from_env()
    handlers = default_handlers()
    original = handlers.get("governance.dispatch")
    if original is None:
        parser.error("production governance.dispatch handler is not registered")
    stop = threading.Event()
    install_signal_handlers(stop)

    def record(event: str, task: Any = None) -> dict[str, Any]:
        value = {"event": event, "pid": os.getpid(), "worker_id": config.worker_id,
                 "at": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns(),
                 "token": args.pause_after_effect}
        if task is not None:
            value.update(task_id=task.task_id, task_type=task.task_type, attempt=task.attempt,
                         request_sha256=task.request_sha256)
        fd = os.open(args.control_dir / "events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return value

    def dispatch(task: Any) -> dict[str, Any]:
        result = original(task)
        record("dispatch_handler_returned", task)
        matches = args.pause_after_effect and (args.task_id is None or task.task_id == args.task_id)
        if matches and isinstance(result, dict):
            claimed = args.control_dir / f"{args.pause_after_effect}.claimed"
            try:
                fd = os.open(claimed, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except FileExistsError:
                return result
            event = record("after_effect_before_worker_ack", task)
            write_json(args.control_dir / f"{args.pause_after_effect}.reached.json", event)
            release = args.control_dir / f"{args.pause_after_effect}.release"
            deadline = time.monotonic() + args.pause_timeout
            while not release.exists():
                if stop.is_set() or time.monotonic() >= deadline:
                    record("worker_barrier_interrupted", task)
                    raise TaskExecutionError("acceptance_barrier_interrupted", retryable=True)
                time.sleep(0.01)
            record("worker_barrier_released", task)
        return result

    handlers["governance.dispatch"] = dispatch
    worker = RuntimeWorker(config, handlers=handlers)
    ready = {"kind": "worker", "pid": os.getpid(), "worker_id": config.worker_id,
             "control_dir": str(args.control_dir.resolve()), "pause_token": args.pause_after_effect,
             "task_filter": args.task_id, "ready_semantics": "configured_before_first_database_cycle"}
    if args.ready_file:
        write_json(args.ready_file, ready)
    print(json.dumps(ready, sort_keys=True), flush=True)
    if args.once:
        worker.run_once()
    else:
        worker.run_forever(stop)


if __name__ == "__main__":
    main()
