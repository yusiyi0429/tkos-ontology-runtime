"""请求体上限：超出的请求在读入应用之前以 413 拒绝，不让过大的 payload 落进只增不删的表。

默认 1 MiB（`GOVERNED_MAX_BODY_BYTES` 可调）；证据上传单独放宽到能装下一个 2 MiB 文件的 base64。
带 Content-Length 的请求直接比长度，分块传输的边读边计。
"""
from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_LIMIT = 1024 * 1024
EVIDENCE_LIMIT = 3 * 1024 * 1024  # 2 MiB 证据的 base64 约 2.67 MiB，另留 JSON 字段余量
_EVIDENCE_PATH = "/v1/evidence-assets"
_BODY = json.dumps({"error": {"code": "REQUEST_TOO_LARGE", "message": "The request body is too large."}}).encode()


class _TooLarge(Exception):
    pass


def _limit(path: str) -> int:
    if path == _EVIDENCE_PATH:
        return EVIDENCE_LIMIT
    try:
        return max(int(os.environ.get("GOVERNED_MAX_BODY_BYTES", DEFAULT_LIMIT)), 1)
    except ValueError:
        return DEFAULT_LIMIT


class BodyLimitMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit(scope["path"])
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(send)
            return
        received, started = 0, False

        async def counted_receive() -> dict:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        async def tracked_send(message: dict) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Any) -> None:
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"),
                                (b"content-length", str(len(_BODY)).encode())]})
        await send({"type": "http.response.body", "body": _BODY})
