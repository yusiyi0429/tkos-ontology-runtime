"""Loopback-only external system with a durable, idempotent SQLite effect ledger.

Run separately from the API/worker. A committed row is the simulated external
business effect; calls are recorded independently so retries cannot masquerade
as multiple effects. No successful rows are seeded by this helper.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any
from urllib.parse import urlsplit
import uuid


MAX_BODY = 2 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        handle.write(canonical_json(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


class Ledger:
    def __init__(self, path: Path, events: Path | None = None) -> None:
        self.path = path
        self.events = events
        self.event_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS effects (
                    effect_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    committed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calls (
                    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    effect_key TEXT,
                    payload_hash TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    called_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS calls_effect_key ON calls(effect_key);
            """)
        os.chmod(path, 0o600)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def event(self, value: dict[str, Any]) -> None:
        if self.events is None:
            return
        self.events.parent.mkdir(parents=True, exist_ok=True)
        with self.event_lock:
            fd = os.open(self.events, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(canonical_json(value) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def apply(self, key: str | None, payload: Any, raw_hash: str) -> tuple[int, dict[str, Any]]:
        valid = isinstance(payload, dict) and isinstance(key, str) and 1 <= len(key) <= 512
        if valid:
            valid = all(32 <= ord(char) < 127 for char in key)
        payload_json = canonical_json(payload) if valid else ""
        digest = hashlib.sha256(payload_json.encode()).hexdigest() if valid else raw_hash
        now = utc_now()
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not valid:
                outcome, status = "invalid", 400
                result = {"error": {"code": "INVALID_EFFECT_REQUEST"}}
                key = None
            else:
                existing = conn.execute("SELECT * FROM effects WHERE effect_key = ?", (key,)).fetchone()
                if existing is not None and existing["payload_hash"] != digest:
                    outcome, status = "conflict", 409
                    result = {"error": {"code": "EFFECT_IDEMPOTENCY_CONFLICT"}}
                elif existing is not None:
                    outcome, status = "replayed", 200
                    result = json.loads(existing["receipt_json"])
                else:
                    outcome, status = "applied", 200
                    result = {
                        "ok": True,
                        "status": "applied",
                        "effect_key": key,
                        "payload_hash": digest,
                        "receiver_receipt_id": str(uuid.uuid4()),
                        "committed_at": now,
                    }
                    conn.execute(
                        "INSERT INTO effects VALUES (?, ?, ?, ?, ?)",
                        (key, digest, payload_json, canonical_json(result), now),
                    )
            cursor = conn.execute(
                "INSERT INTO calls(effect_key, payload_hash, outcome, called_at) VALUES (?, ?, ?, ?)",
                (key, digest, outcome, now),
            )
            call_id = cursor.lastrowid
            conn.commit()
        self.event({"event": "receiver_call_committed", "called_at": now, "call_id": call_id,
                    "effect_key": key, "payload_hash": digest, "outcome": outcome, "pid": os.getpid()})
        return status, result

    def summary(self) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN")
            rows = conn.execute("""
                SELECT e.effect_key, e.payload_hash, e.receipt_json, e.committed_at,
                       COUNT(c.call_id) AS attempts
                FROM effects e LEFT JOIN calls c ON c.effect_key = e.effect_key
                GROUP BY e.effect_key ORDER BY e.committed_at, e.effect_key
            """).fetchall()
            counts = {row["outcome"]: row["n"] for row in conn.execute(
                "SELECT outcome, COUNT(*) AS n FROM calls GROUP BY outcome"
            )}
            calls = [dict(row) for row in conn.execute("SELECT * FROM calls ORDER BY call_id")]
        effects = []
        for row in rows:
            effect = dict(row)
            effect["receipt"] = json.loads(effect.pop("receipt_json"))
            effects.append(effect)
        return {"total_calls": len(calls), "unique_effects": len(effects),
                "replayed_calls": counts.get("replayed", 0), "conflict_calls": counts.get("conflict", 0),
                "invalid_calls": counts.get("invalid", 0), "effects": effects, "calls": calls}


class ReceiverHandler(BaseHTTPRequestHandler):
    server: Any
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        pass

    def send_json(self, status: int, value: dict[str, Any]) -> None:
        body = canonical_json(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send_json(200, {"ok": True, "kind": "acceptance-effect-receiver"})
        elif path == "/ledger":
            self.send_json(200, self.server.ledger.summary())
        else:
            self.send_json(404, {"error": {"code": "NOT_FOUND"}})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/effects":
            self.send_json(404, {"error": {"code": "NOT_FOUND"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
            self.send_json(400, {"error": {"code": "INVALID_BODY_LENGTH"}})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            canonical_json(payload)
        except (ValueError, UnicodeDecodeError, RecursionError):
            payload = None
        header_key = self.headers.get("Idempotency-Key") or self.headers.get("X-Effect-Key")
        body_key = payload.get("effect_key") if isinstance(payload, dict) else None
        key = header_key or body_key
        if header_key and body_key and header_key != body_key:
            payload = None
        status, result = self.server.ledger.apply(key, payload, hashlib.sha256(raw).hexdigest())
        self.send_json(status, result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ReceiverHandler)
    server.daemon_threads = True
    server.ledger = Ledger(args.ledger, args.events)
    ready = {"kind": "receiver", "pid": os.getpid(), "host": args.host,
             "port": server.server_port, "url": f"http://{args.host}:{server.server_port}",
             "ledger": str(args.ledger.resolve())}
    if args.ready_file:
        write_json(args.ready_file, ready)
    print(canonical_json(ready), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
