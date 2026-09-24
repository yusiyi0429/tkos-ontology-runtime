"""请求 ID 与结构化运行记录。

- 每个 HTTP 请求一行 ``http_request``：方法、路由模板、状态与耗时，响应头带回 ``X-Request-ID``。
- 每个治理事务一行 ``governed_transaction``：取连接与等 scope 锁的耗时、持有时长、结果；
  经 /v1/actions 或工作台提交的动作、证据上传，另带回执 id、动作类型与效果任务 id。
  Worker 的任务日志按 task_id 对上。

日志只写标识符、路由模板与耗时，不写请求体、查询串、凭证、SQL 或业务内容。
"""
from __future__ import annotations

from contextvars import ContextVar
import json
import logging
import re
import sys
import time
from typing import Any
import uuid

LOGGER = logging.getLogger("tkos.runtime")
REQUEST_ID: ContextVar[str | None] = ContextVar("tkos_request_id", default=None)
_ACCEPTED = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")


def log(event: str, **fields: Any) -> None:
    LOGGER.info(json.dumps({"event": event, "request_id": REQUEST_ID.get(), **fields},
                           ensure_ascii=False, separators=(",", ":"), default=str))


def ensure_handler() -> None:
    """进程没给 tkos.runtime 配日志时，挂一个写 stderr 的处理器，每行就是一条 JSON。"""
    if not LOGGER.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        LOGGER.propagate = False


class RequestIdMiddleware:
    """沿用格式合法的 X-Request-ID，否则新生成；写回响应头，请求结束记一行 http_request。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _ACCEPTED.match(incoming) else uuid.uuid4().hex
        token = REQUEST_ID.set(request_id)
        started, status = time.perf_counter(), 500

        async def send_with_id(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []),
                                                  (b"x-request-id", request_id.encode("latin-1"))]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            log("http_request", method=scope["method"], path=getattr(route, "path", None) or scope["path"],
                status=status, duration_ms=round((time.perf_counter() - started) * 1000, 1))
            REQUEST_ID.reset(token)
