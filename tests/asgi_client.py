"""Minimal synchronous ASGI test client without an external client dependency."""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.body)


def get(
    application,
    target: str,
    *,
    headers: dict[str, str] | None = None,
) -> AsgiResponse:
    parsed = urlsplit(target)
    sent: list[dict] = []
    received = False

    async def receive() -> dict:
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    if headers is None:
        token = base64.b64encode(b"clark:test").decode("ascii")
        headers = {"authorization": f"Basic {token}"}
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in headers.items()
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": parsed.path,
        "raw_path": parsed.path.encode(),
        "query_string": parsed.query.encode(),
        "headers": raw_headers,
        "client": ("test", 1),
        "server": ("test", 80),
        "root_path": "",
    }
    asyncio.run(application(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    return AsgiResponse(status_code=start["status"], headers=headers, body=body)


def get_json(
    application,
    target: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    response = get(application, target, headers=headers)
    return response.status_code, response.json()
