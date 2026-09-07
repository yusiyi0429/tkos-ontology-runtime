"""Test-only API launcher; production main never imports this module.

Headers: X-Acceptance-Key, X-Acceptance-Checkpoint, X-Acceptance-Mode,
X-Acceptance-Token. A barrier writes TOKEN.reached.json and blocks inside the
checkpoint until TOKEN.release exists. A fail raises inside the real transaction.
"""
from __future__ import annotations

import argparse
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import importlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
CHECKPOINTS = {"auth_fence_acquired", "before_business_commit", "after_first_bundle_update"}
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
SAFE_CONTEXT_KEYS = {"action_type", "object_id", "revision_id", "receipt_id", "auth_epoch",
                     "expected_version", "object_version", "processing_cycle_id", "task_id"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        json.dump(value, handle, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


@dataclass
class Injection:
    checkpoint: str
    mode: str
    token: str
    triggered: bool = False


CURRENT: ContextVar[Injection | None] = ContextVar("acceptance_injection", default=None)


class CheckpointController:
    def __init__(self, directory: Path, timeout: float) -> None:
        self.directory = directory
        self.timeout = timeout
        self.lock = threading.Lock()
        directory.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, injection: Injection, context: Any = None) -> dict[str, Any]:
        safe = {}
        if isinstance(context, dict):
            for name in SAFE_CONTEXT_KEYS:
                value = context.get(name)
                if isinstance(value, (str, int, bool)) and len(str(value)) <= 200:
                    safe[name] = value
        record = {"event": event, "checkpoint": injection.checkpoint, "mode": injection.mode,
                  "token": injection.token, "pid": os.getpid(), "at": utc_now(),
                  "monotonic_ns": time.monotonic_ns(), "context": safe}
        with self.lock:
            fd = os.open(self.directory / "events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def checkpoint(self, name: str, context: Any = None, **kwargs: Any) -> None:
        injection = CURRENT.get()
        if injection is None or injection.checkpoint != name or injection.triggered:
            return
        injection.triggered = True
        if context is None and kwargs:
            context = kwargs
        record = self.event("checkpoint_reached", injection, context)
        write_json(self.directory / f"{injection.token}.reached.json", record)
        if injection.mode == "fail":
            self.event("checkpoint_failure_injected", injection)
            raise RuntimeError(f"ACCEPTANCE_INJECTED_FAILURE:{name}")
        release = self.directory / f"{injection.token}.release"
        deadline = time.monotonic() + self.timeout
        while not release.exists():
            if time.monotonic() >= deadline:
                self.event("checkpoint_timeout", injection)
                raise RuntimeError(f"ACCEPTANCE_BARRIER_TIMEOUT:{name}")
            time.sleep(0.01)
        self.event("checkpoint_released", injection)


class InjectionMiddleware:
    def __init__(self, app: Any, secret: str, controller: CheckpointController) -> None:
        self.app = app
        self.secret = secret
        self.controller = controller

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        selected = any(key.startswith(b"x-acceptance-") for key in headers)
        injection = None
        if selected:
            supplied = headers.get(b"x-acceptance-key", b"")
            authorized = hmac.compare_digest(supplied, self.secret.encode("ascii"))
            name = headers.get(b"x-acceptance-checkpoint", b"").decode("ascii", "ignore")
            mode = headers.get(b"x-acceptance-mode", b"").decode("ascii", "ignore")
            token = headers.get(b"x-acceptance-token", b"").decode("ascii", "ignore")
            if not authorized or name not in CHECKPOINTS or mode not in {"barrier", "fail"} or not TOKEN_PATTERN.fullmatch(token):
                await self.reject(send, 403, "INVALID_ACCEPTANCE_CONTROL")
                return
            if (self.controller.directory / f"{token}.release").exists():
                await self.reject(send, 409, "ACCEPTANCE_TOKEN_ALREADY_RELEASED")
                return
            try:
                fd = os.open(self.controller.directory / f"{token}.claimed", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except FileExistsError:
                await self.reject(send, 409, "ACCEPTANCE_TOKEN_ALREADY_USED")
                return
            injection = Injection(name, mode, token)
        current_token = CURRENT.set(injection)
        try:
            await self.app(scope, receive, send)
        finally:
            if injection is not None and not injection.triggered:
                self.controller.event("checkpoint_not_reached", injection)
            CURRENT.reset(current_token)

    @staticmethod
    async def reject(send: Any, status: int, code: str) -> None:
        body = json.dumps({"error": {"code": code}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--control-dir", required=True, type=Path)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--checkpoint-timeout", type=float, default=60.0)
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    if not 1 <= args.checkpoint_timeout <= 300:
        parser.error("checkpoint timeout must be between 1 and 300 seconds")
    secret = args.key_file.read_text(encoding="ascii").strip()
    if not 32 <= len(secret) <= 512:
        parser.error("test key must contain 32..512 ASCII characters")
    controller = CheckpointController(args.control_dir, args.checkpoint_timeout)
    checkpoints = importlib.import_module("memory_service_runtime.governed.checkpoints")
    original = checkpoints.checkpoint
    replacement = controller.checkpoint
    checkpoints.checkpoint = replacement
    module = importlib.import_module("memory_service_app.main")
    # Also adapt an eagerly imported `from checkpoints import checkpoint` alias.
    for module_name, imported in list(sys.modules.items()):
        if module_name.startswith("memory_service_runtime.governed.") and imported is not None:
            for key, value in list(vars(imported).items()):
                if value is original:
                    setattr(imported, key, replacement)
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    port = sock.getsockname()[1]
    app = InjectionMiddleware(module.app, secret, controller)
    config = uvicorn.Config(app, host=args.host, port=port, access_log=False, log_level="warning")

    class AcceptanceServer(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:
                ready = {"kind": "api", "pid": os.getpid(), "host": args.host, "port": port,
                         "url": f"http://{args.host}:{port}", "control_dir": str(args.control_dir.resolve())}
                if args.ready_file:
                    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
                    write_json(args.ready_file, ready)
                print(json.dumps(ready, sort_keys=True), flush=True)

    try:
        asyncio.run(AcceptanceServer(config).serve(sockets=[sock]))
    finally:
        sock.close()


if __name__ == "__main__":
    main()
