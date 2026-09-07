"""Actor checks shared by Memory governance write paths."""
from __future__ import annotations

import uuid
from collections.abc import Mapping

import psycopg


class ActorRequiredError(Exception):
    """The referenced user is absent or outside the requested tenant/organization scope."""


class HumanActorRequiredError(ActorRequiredError):
    """The referenced user is absent, out of scope, or is not a human actor."""


def _value(row, key: str, index: int):
    return row[key] if isinstance(row, Mapping) else row[index]


def _canonical_uuid(value, *, field_name: str) -> uuid.UUID:
    """非法 UUID 在 Python 侧转稳定领域错误：既不泄露 psycopg InvalidTextRepresentation，
    也避免服务端类型错误毒化调用方事务（InFailedSqlTransaction）。合法输入返回
    canonical ``uuid.UUID``，SQL 一律绑定该值而非原始替代表示（如大写/花括号形式）。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ActorRequiredError(f"{field_name} 不是合法的用户标识") from exc


def require_actor(
    conn,
    user_id: str,
    *,
    tenant_id: str | None = None,
    organization_id: str | None = None,
    field_name: str = "actor",
    lock: bool = False,
) -> str:
    """Return the actor kind, failing closed on absence or a scope mismatch.

    tenant/org scope 在 SQL 层约束（不存在与跨 scope 同为零行，泛化错误不泄露目标
    是否存在）；``lock=True`` 时对 actor 行 FOR SHARE，锁在事务内持有至 commit，
    防止校验后、写入前的并发身份变更（TOCTOU）。非法 UUID 等输入统一转换为稳定
    领域错误，不外泄 psycopg 的类型错误细节。
    """
    if (tenant_id is None) != (organization_id is None):
        raise ValueError("tenant_id and organization_id must be supplied together")
    canonical_user_id = _canonical_uuid(user_id, field_name=field_name)
    sql = "SELECT kind FROM users WHERE user_id=%s"
    params: list = [canonical_user_id]
    if tenant_id is not None:
        sql += " AND tenant_id=%s AND organization_id=%s"
        params += [tenant_id, organization_id]
    if lock:
        sql += " FOR SHARE"
    try:
        row = conn.execute(sql, params).fetchone()
    except psycopg.errors.InvalidTextRepresentation as exc:
        raise ActorRequiredError(f"{field_name} 不是合法的用户标识") from exc
    if row is None:
        raise ActorRequiredError(f"{field_name} 不存在或不属于目标 tenant/organization scope")
    return str(_value(row, "kind", 0))


def require_human(
    conn,
    user_id: str,
    *,
    tenant_id: str | None = None,
    organization_id: str | None = None,
    field_name: str = "decided_by",
    lock: bool = False,
) -> None:
    """Fail closed unless ``user_id`` references an in-scope ``users.kind='human'`` row."""
    try:
        kind = require_actor(
            conn,
            user_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            field_name=field_name,
            lock=lock,
        )
    except ActorRequiredError as exc:
        raise HumanActorRequiredError(str(exc)) from exc
    if kind != "human":
        raise HumanActorRequiredError(
            f"{field_name} 必须是真实用户（kind='human'），当前 kind={kind!r}"
        )
