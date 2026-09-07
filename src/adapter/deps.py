"""FastAPI dependencies for per-request DB connections and viewer identity."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException
from pgvector.psycopg import register_vector

from memory_service import actors

from adapter.settings import Settings, get_settings


@dataclass(frozen=True)
class Viewer:
    """The validated human viewer exposed to rendering/projection code."""

    user_id: str
    display_name: str


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
        raise HTTPException(status_code=503, detail=f"memory_service 数据库不可达：{exc}") from exc
    try:
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            # 与 workspsce aw.db 保持一致：首次迁移前 vector 扩展可能尚未创建。
            pass
        yield conn
    finally:
        conn.close()


def resolve_viewer(conn: psycopg.Connection, settings: Settings) -> Viewer:
    """Resolve and fail closed unless the configured viewer is an in-scope human."""
    user_id = settings.viewer_user_id
    if not user_id:
        raise HTTPException(status_code=500, detail="VIEWER_USER_ID 未配置，无法解析 viewer")

    try:
        # Keep the memory_service actor check as the single authority for the
        # human + tenant/org invariant; this is not a duplicate local policy.
        actors.require_human(
            conn,
            user_id,
            tenant_id=settings.memory_tenant,
            organization_id=settings.memory_org,
            field_name="viewer",
        )
    except actors.ActorRequiredError as exc:
        # Viewer is process configuration, not a user-provided anchor.  A bad
        # configured viewer therefore fails loudly as 500, never as 404.
        raise HTTPException(status_code=500, detail=f"viewer 配置无效：{exc}") from exc

    row = conn.execute(
        "SELECT display_name FROM users WHERE user_id=%s",
        (user_id,),
    ).fetchone()
    if row is None:
        # Defensive: require_human already checked this row.  Keep the failure
        # explicit if the row disappears between the two reads.
        raise HTTPException(status_code=500, detail=f"viewer 用户不存在：{user_id}")
    return Viewer(user_id=user_id, display_name=str(row[0] or user_id))


def get_viewer(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Viewer:
    """FastAPI dependency wrapper around :func:`resolve_viewer`."""
    return resolve_viewer(conn, settings)
