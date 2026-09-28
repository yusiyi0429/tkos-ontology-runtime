"""FastAPI dependency for per-request DB connections."""
from __future__ import annotations

from collections.abc import Iterator
import logging
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException
from pgvector.psycopg import register_vector

from adapter.settings import Settings, get_settings

LOGGER = logging.getLogger("tkos.adapter")


def get_conn(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[psycopg.Connection]:
    """Yield one connection per request and always close it afterwards.

    A refused/unreachable database is explicitly 503.  It must never become a
    404, because clark treats 404 as the valid external-only branch.

    ``connect_timeout`` 不能省。**「被拒绝」和「连不上」不是同一件事**：前者立刻抛
    ``OperationalError``，下面那一句把它翻成 503；而后者（地址写错、防火墙静默丢包、
    库过载）在 libpq 默认配置下会一直等下去，异常永远不来 —— 这个请求于是挂着，
    占住的线程池线程也不还。实测：连一个没人监听的端口，不给期限时超过 60 秒不返回，
    给 3 秒时 3.01 秒抛 ``ConnectionTimeout``（它是 ``OperationalError`` 的子类，
    所以下面那一行原样接得住，503 的语义一个字没变）。
    """
    try:
        conn = psycopg.connect(
            settings.database_url, connect_timeout=settings.db_connect_timeout
        )
    except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
        # 原文带地址、端口、库名与用户名，只进服务端日志。
        LOGGER.warning("memory_service database unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="memory_service 数据库不可达") from exc
    try:
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            # 与 workspsce aw.db 保持一致：首次迁移前 vector 扩展可能尚未创建。
            pass
        yield conn
    finally:
        conn.close()
