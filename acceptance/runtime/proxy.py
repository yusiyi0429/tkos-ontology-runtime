"""Loopback-only HTTP proxy that drops one successful upstream response per token.

The upstream request and response complete first. Only then is the client socket
closed without an HTTP response. Durable token markers make the retry reachable,
including after this proxy restarts. Control headers never reach production API.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit
import uuid


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
               "trailer", "transfer-encoding", "upgrade", "host", "content-length"}
MAX_BODY = 4 * 1024 * 1024
MAX_RESPONSE = 8 * 1024 * 1024


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


class ProxyState:
    def __init__(self, directory: Path, secret: str, upstream_port: int, timeout: float) -> None:
        self.directory = directory
        self.secret = secret
        self.upstream_port = upstream_port
        self.timeout = timeout
        self.lock = threading.Lock()
        directory.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, token: str | None, **fields: Any) -> dict[str, Any]:
        record = {"event": event, "token": token, "pid": os.getpid(),
                  "at": datetime.now(timezone.utc).isoformat(), "monotonic_ns": time.monotonic_ns(), **fields}
        with self.lock:
            fd = os.open(self.directory / "events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def claim_drop(self, token: str) -> bool:
        try:
            fd = os.open(self.directory / f"{token}.claimed", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            return True
        except FileExistsError:
            return False


class ProxyHandler(BaseHTTPRequestHandler):
    server: Any
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        pass

    def send_json(self, status: int, code: str) -> None:
        body = json.dumps({"error": {"code": code}}).encode()
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

    def forward(self) -> None:
        state: ProxyState = self.server.state
        parsed = urlsplit(self.path)
        if not self.path.startswith("/") or self.path.startswith("//") or parsed.scheme or parsed.netloc:
            self.send_json(400, "INVALID_PROXY_TARGET")
            return
        token = self.headers.get("X-Acceptance-Drop-Response")
        has_controls = any(name.lower().startswith("x-acceptance-") for name in self.headers)
        if has_controls:
            supplied = self.headers.get("X-Acceptance-Key", "").encode("ascii", "ignore")
            if not hmac.compare_digest(supplied, state.secret.encode("ascii")) or not token or not TOKEN_PATTERN.fullmatch(token):
                self.send_json(403, "INVALID_ACCEPTANCE_CONTROL")
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
            self.send_json(400, "INVALID_BODY_LENGTH")
            return
        body = self.rfile.read(length) if length else None
        connection_tokens = {part.strip().lower() for part in self.headers.get("Connection", "").split(",")}
        headers = {name: value for name, value in self.headers.items()
                   if name.lower() not in HOP_HEADERS | connection_tokens
                   and not name.lower().startswith("x-acceptance-")}
        headers["Connection"] = "close"
        upstream = http.client.HTTPConnection("127.0.0.1", state.upstream_port, timeout=state.timeout)
        try:
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                self.send_json(502, "UPSTREAM_RESPONSE_TOO_LARGE")
                return
            status = response.status
            response_headers = response.getheaders()
        except (OSError, http.client.HTTPException, TimeoutError):
            state.event("upstream_failure", token, method=self.command, path=parsed.path)
            self.send_json(502, "UPSTREAM_UNAVAILABLE")
            return
        finally:
            upstream.close()
        digest = hashlib.sha256(data).hexdigest()
        if token and 200 <= status < 300 and state.claim_drop(token):
            fields = {"method": self.command, "path": parsed.path, "upstream_status": status,
                      "response_sha256": digest, "response_length": len(data)}
            # These are only observed upstream identifiers, never manufactured receipts.
            try:
                decoded = json.loads(data)
                receipt_id = decoded.get("receipt_id") if isinstance(decoded, dict) else None
                if isinstance(receipt_id, str):
                    fields["receipt_id"] = str(uuid.UUID(receipt_id))
            except (ValueError, UnicodeDecodeError, TypeError):
                pass
            event = state.event("successful_response_dropped", token, **fields)
            write_json(state.directory / f"{token}.dropped.json", event)
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        state.event("response_forwarded", token, method=self.command, path=parsed.path,
                    upstream_status=status, response_sha256=digest)
        self.send_response(status)
        connection_tokens = set()
        for name, value in response_headers:
            if name.lower() == "connection":
                connection_tokens.update(part.strip().lower() for part in value.split(","))
        for name, value in response_headers:
            if name.lower() not in HOP_HEADERS | connection_tokens:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    do_GET = forward
    do_POST = forward
    do_PUT = forward
    do_PATCH = forward
    do_DELETE = forward
    do_HEAD = forward
    do_OPTIONS = forward


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--control-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--upstream-timeout", type=float, default=60.0)
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    parsed = urlsplit(args.upstream)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        parser.error("upstream must be http://127.0.0.1:PORT with no credentials, path or query")
    try:
        port = parsed.port or 80
    except ValueError:
        parser.error("invalid upstream port")
    if not 1 <= args.upstream_timeout <= 300:
        parser.error("upstream timeout must be between 1 and 300 seconds")
    secret = args.key_file.read_text(encoding="ascii").strip()
    if not 32 <= len(secret) <= 512:
        parser.error("test key must contain 32..512 ASCII characters")
    server = ThreadingHTTPServer((args.host, args.port), ProxyHandler)
    server.daemon_threads = True
    server.state = ProxyState(args.control_dir, secret, port, args.upstream_timeout)
    ready = {"kind": "proxy", "pid": os.getpid(), "host": args.host, "port": server.server_port,
             "url": f"http://{args.host}:{server.server_port}", "upstream": f"http://127.0.0.1:{port}",
             "control_dir": str(args.control_dir.resolve())}
    if args.ready_file:
        write_json(args.ready_file, ready)
    print(json.dumps(ready, sort_keys=True), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
