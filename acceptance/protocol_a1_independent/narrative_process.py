"""Selected-source Narrative process; optional one-time post-render file barrier.

The sole permitted patch calls the real render_facts first, then pauses, then
returns its exact original return object. Inputs, return values, authentication,
SQL access, protocol authorization and HTTP handlers are never replaced.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
import uuid


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _private(path: Path, value) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
    temp.replace(path)


def post_render_barrier(original, *, paused: Path, release: Path, token: str, timeout: float):
    """Only instrumentation point approved for this acceptance variant."""
    lock = threading.Lock()
    consumed = False

    @functools.wraps(original)
    def wrapped(facts):
        nonlocal consumed
        before = _digest(facts)
        rendered = original(facts)
        after = _digest(facts)
        assert before == after, "real render function modified captured facts"
        with lock:
            first = not consumed
            consumed = True
        if first:
            assert isinstance(rendered, str), "real Narrative render return must be text"
            _private(paused, {"phase": "after_real_render_before_final_database_transaction",
                "barrier_token": token, "pid": os.getpid(), "facts": facts,
                "facts_sha256": before, "original_result_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "original_result": rendered, "original_result_chars": len(rendered),
                "input_modified": False, "return_value_modified": False})
            deadline = time.monotonic() + timeout
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Narrative acceptance barrier was not released")
                time.sleep(0.05)
            permit = json.loads(release.read_text())
            assert permit == {"barrier_token": token}, "unexpected Narrative barrier release marker"
        return rendered
    return wrapped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--paused", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--barrier-token")
    parser.add_argument("--barrier-timeout", type=float, default=60)
    args = parser.parse_args()
    if bool(args.paused) != bool(args.release) or bool(args.paused) != bool(args.barrier_token):
        parser.error("a barrier requires paused, release and barrier-token together")
    if not 1 <= args.barrier_timeout <= 60:
        parser.error("barrier timeout must be bounded to at most 60 seconds")
    if any(path and path.exists() for path in (args.ready_file, args.paused, args.release)):
        parser.error("refusing stale process/barrier files")
    expected_env = {"TKOS_NARRATIVE_ENABLED": "1", "TKOS_NARRATIVE_LEGACY_ENABLED": "0",
                    "TKOS_NARRATIVE_COMPRESSION": "none"}
    if any(os.environ.get(name) != value for name, value in expected_env.items()):
        parser.error("Narrative acceptance must use governed-only deterministic configuration")
    source = args.source.resolve()
    if not (source / "memory_service_app/main.py").is_file():
        parser.error("selected application source does not exist")
    sys.path.insert(0, str(source))
    from psycopg.conninfo import conninfo_to_dict
    connection = conninfo_to_dict(os.environ.get("DATABASE_URL", ""))
    if connection.get("host") not in {"127.0.0.1", "localhost", "::1"} \
            or not connection.get("dbname", "").startswith("tkos_a1_"):
        parser.error("Narrative acceptance requires the one-off loopback database")
    provenance = {}

    def load(name):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        assert path.is_relative_to(source), "Narrative process imported a different source"
        provenance[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return module

    facts = load("memory_service_runtime.governed.narrative_facts")
    patches = []
    if args.paused:
        assert facts.render_facts.__module__ == "memory_service_runtime.governed.narrative_facts"
        facts.render_facts = post_render_barrier(facts.render_facts, paused=args.paused,
            release=args.release, token=args.barrier_token, timeout=args.barrier_timeout)
        patches = ["memory_service_runtime.governed.narrative_facts.render_facts:post_return_pause_only"]
    load("memory_service_app.narrative")
    load("memory_service_runtime.governed.db")
    load("memory_service_runtime.governed.protocol")
    load("memory_service_runtime.governed.readers")
    app = load("memory_service_app.main").app
    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                _private(args.ready_file, {"pid": os.getpid(), "url": f"http://127.0.0.1:{port}",
                    "source_root": str(source), "provenance": provenance, "configuration": expected_env,
                    "patches": patches, "http_management_endpoint_added": False})

    try:
        asyncio.run(Server(config).serve(sockets=[sock]))
    finally:
        sock.close()


if __name__ == "__main__":
    main()
