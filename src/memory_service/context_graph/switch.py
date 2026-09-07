"""Controlled shadow-to-current graph switch.

The operation snapshots the current graph to immutable storage, takes the scope advisory lock, then
retires the old generation and promotes the shadow generation in one transaction.  The switch is
non-destructive: the old generation's entities, relations, source refs, proposals, and audit rows
are all retained, and the outcome reports explicit ``retained`` counts plus zero ``deleted`` counts.
Destructive cleanup (``clear_graph_data``) is a separate, explicitly authorized recovery/maintenance
capability and is never invoked by the switch.  A non-current source generation is rejected, and a
scope-local human actor is required.

The MVP does not fabricate replay or reconciliation reports, so ``context_graph_switch_log`` stays
unused while its report-key columns are mandatory.  Production scope always fails closed until the
P8 cutover gate is complete; ``allow_production`` no longer lifts that gate.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable, ContextManager

from psycopg import IsolationLevel
from psycopg.pq import TransactionStatus

from memory_service.actors import ActorRequiredError, require_human
from memory_service.context_graph.concurrency import (
    acquire_exclusive_session_lock,
    release_exclusive_session_lock,
)
from memory_service.context_graph.snapshot import SnapshotError, create_snapshot

_PROD_SCOPE = ("local", "local-org")


class SwitchError(RuntimeError):
    """切换编排错误基类。"""


class SwitchGateError(SwitchError):
    """切换门禁失败（目标代状态/存在性等，拒绝继续）。"""


class SwitchAborted(SwitchError):
    """切换被中止。"""


def _one(cur, sql, params=()):
    """兼容传入 Cursor 或 Connection：Connection.execute 返回一个 Cursor。"""
    if hasattr(cur, "fetchone"):
        cur.execute(sql, params)
        return cur.fetchone()
    return cur.execute(sql, params).fetchone()


def acquire_exclusive_lock(conn, tenant_id: str, organization_id: str) -> None:
    acquire_exclusive_session_lock(conn, tenant_id, organization_id)


def release_exclusive_lock(conn, tenant_id: str, organization_id: str) -> None:
    release_exclusive_session_lock(conn, tenant_id, organization_id)


# ---------------------------------------------------------------------------
# 最小前置检查（MVP：只留一两条真正必要的）
# ---------------------------------------------------------------------------


def minimal_preflight(
    db_connect,
    *,
    tenant_id: str,
    organization_id: str,
    from_generation: str,
    to_generation: str,
) -> list[str]:
    """MVP 最小检查：目标代存在且为 shadow、目标代非空（有 confirmed 实体）。

    旧代是否为 current 不在这一层判（由切换事务里 UPDATE ... WHERE status='current'
    的 rowcount==1 兜底）；其余 11 项前置/重放/核对在 MVP 明确不做。
    返回失败原因列表（空 = 通过）。
    """
    problems: list[str] = []
    with db_connect() as conn:
        to_row = _one(
            conn,
            "SELECT status FROM context_graph_versions "
            "WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s",
            (to_generation, tenant_id, organization_id),
        )
        if to_row is None:
            problems.append(f"目标代 {to_generation} 不存在")
        elif to_row[0] != "shadow":
            problems.append(f"目标代 {to_generation} status={to_row[0]}，不是 shadow")
        else:
            cnt = _one(
                conn,
                "SELECT count(*) FROM semantic_entities "
                "WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s "
                "AND status='confirmed'",
                (to_generation, tenant_id, organization_id),
            )
            if int(cnt[0]) == 0:
                problems.append(f"目标代 {to_generation} 无 confirmed 实体（空图）")
    return problems


# ---------------------------------------------------------------------------
# Immutable snapshot and audit ledger
# ---------------------------------------------------------------------------


def create_switch_snapshot(
    db_connect, object_store, *, tenant_id, organization_id, from_generation, created_by,
) -> dict[str, Any]:
    """Create the old-generation snapshot only on storage proven immutable by a read-only probe."""
    try:
        object_store.preflight_immutability()
        return create_snapshot(
            db_connect, object_store, tenant_id=tenant_id, organization_id=organization_id,
            generation_id=from_generation, created_by=created_by,
        )
    except SnapshotError as exc:
        raise SwitchAborted(f"旧图快照失败（未切换）：{exc}") from exc


# ---------------------------------------------------------------------------
# 切换事务（核心链路）
# ---------------------------------------------------------------------------


def _count_retained_rows(cur, generation_id: str, tenant_id: str, organization_id: str) -> dict[str, int]:
    """统计旧 generation 在切换后必须全部保留的行数（实体/关系/来源引用/提案/审计）。"""
    scope = (generation_id, tenant_id, organization_id)
    return {
        "entities": _one(
            cur,
            "SELECT count(*) FROM semantic_entities"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s",
            scope,
        )[0],
        "relations": _one(
            cur,
            "SELECT count(*) FROM semantic_relations"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s",
            scope,
        )[0],
        "entity_source_refs": _one(
            cur,
            "SELECT count(*) FROM semantic_entity_source_refs"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s",
            scope,
        )[0],
        "relation_source_refs": _one(
            cur,
            "SELECT count(*) FROM semantic_relation_source_refs"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s",
            scope,
        )[0],
        "proposals": _one(
            cur,
            "SELECT count(*) FROM memory_proposals"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s",
            scope,
        )[0],
        "audits": _one(
            cur,
            "SELECT count(*) FROM memory_audit a JOIN memory_proposals p USING (proposal_id)"
            " WHERE p.graph_generation_id=%s AND p.tenant_id=%s AND p.organization_id=%s",
            scope,
        )[0],
    }


def run_switch_transaction(
    conn,
    *,
    tenant_id: str,
    organization_id: str,
    from_generation: str,
    to_generation: str,
    switched_by: str,
) -> dict[str, dict[str, int]]:
    """单事务：old→retired → shadow→current，全程零删除。

    非破坏式门禁：旧代仅被标记为 retired，其实体、关系、来源引用、提案与审计全部保留；
    返回值显式给出 ``retained`` 计数与恒为零的 ``deleted`` 计数，防止调用方把
    “未返回 deleted” 误解为清理成功。旧代数据的清理由独立、显式授权的维护动作负责
    （``restore.clear_graph_data``，不属于切换路径）。

    事务自包含：本函数自带 ``with conn.transaction()``，任何异常在返回前 rollback，
    调用方即使 catch 异常后 commit 连接，也不会留下 old=retired/new 未 current 的
    半切换状态。``switched_by`` 在同一事务内 FOR SHARE 锁定并复核为本 scope human，
    与编排层的预校验共同消除“校验后、切换前”的身份 TOCTOU 窗口。
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise SwitchError("switch transaction connection must be idle")
    conn.isolation_level = IsolationLevel.READ_COMMITTED
    with conn.transaction():
        # 同事务内锁定并复核切换人：缺失/跨 scope/非 human 一律 SwitchGateError，
        # 泛化文案不泄露目标是否存在于其它 scope。
        try:
            require_human(
                conn,
                switched_by,
                tenant_id=tenant_id,
                organization_id=organization_id,
                field_name="switched_by",
                lock=True,
            )
        except ActorRequiredError as exc:
            raise SwitchGateError(f"切换人校验失败：{exc}") from exc
        with conn.cursor() as cur:
            # 目标代存在且为 shadow（MVP 最小检查，事务内再验一次）
            to_row = _one(
                cur,
                "SELECT status FROM context_graph_versions "
                "WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s",
                (to_generation, tenant_id, organization_id),
            )
            if to_row is None or to_row[0] != "shadow":
                raise SwitchGateError(f"目标代 {to_generation} 不存在或非 shadow")

            # 1) old→retired 先落地（让出 uq_cgv_current 唯一槽；旧代必须是 current 才匹配）
            cur.execute(
                "UPDATE context_graph_versions SET status='retired', retired_at=now() "
                "WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s AND status='current'",
                (from_generation, tenant_id, organization_id),
            )
            if cur.rowcount != 1:
                raise SwitchGateError(
                    f"旧代 {from_generation} 不是 current，无法置 retired（rowcount={cur.rowcount}）；未做任何变更"
                )

            # 2) shadow→current（取回唯一槽）
            cur.execute(
                "UPDATE context_graph_versions SET status='current' "
                "WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s AND status='shadow'",
                (to_generation, tenant_id, organization_id),
            )
            if cur.rowcount != 1:
                raise SwitchGateError(f"目标代 {to_generation} 置 current 失败（rowcount={cur.rowcount}）")

            # 3) 非破坏式断言：旧代行全部保留，本事务删除计数恒为零。
            retained = _count_retained_rows(cur, from_generation, tenant_id, organization_id)
    return {
        "retained": retained,
        "deleted": {"deleted_entity_count": 0, "deleted_relation_count": 0},
    }


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SwitchOutcome:
    status: str                      # ok / aborted
    tenant_id: str
    organization_id: str
    from_generation: str
    to_generation: str
    snapshot: dict[str, Any] | None = None
    deleted: dict[str, int] | None = None
    aborted_reason: str | None = None
    preflight_problems: tuple[str, ...] = ()
    # 追加在末尾以保持位置兼容；切换成功时为旧代保留行数，aborted 时为 None。
    retained: dict[str, int] | None = None


def switch_graph(
    *,
    tenant_id: str,
    organization_id: str,
    from_generation: str,
    to_generation: str,
    switched_by: str,
    db_connect: Callable[[], ContextManager[Any]],
    object_store: Any,
    allow_production: bool = False,
) -> SwitchOutcome:
    """受控非破坏式切换（MVP 核心链路）。生产 scope 在 P8 切换门禁完成前始终 fail closed。

    ``allow_production`` 仅保留签名兼容，不再豁免生产 scope：传 True 也同样拒绝。
    ``db_connect`` 与 ``object_store`` 由宿主显式装配（host capability boundary：Memory 不自开连接、
    不自取 S3）。
    """
    if (tenant_id, organization_id) == _PROD_SCOPE:
        return SwitchOutcome(
            status="aborted", tenant_id=tenant_id, organization_id=organization_id,
            from_generation=from_generation, to_generation=to_generation,
            aborted_reason="生产 scope 切换在 P8 正式切换门禁完成前始终拒绝（allow_production 不再豁免）",
        )

    # from/to generation 在任何 DB/preflight/snapshot/lock 前 Python 侧 canonical：
    # 非法 UUID fail-closed 为 aborted，不触发 PG 类型错误（generation_id 列为 uuid）。
    try:
        canonical_from = str(uuid.UUID(str(from_generation)))
        canonical_to = str(uuid.UUID(str(to_generation)))
    except (ValueError, AttributeError, TypeError):
        return SwitchOutcome(
            status="aborted", tenant_id=tenant_id, organization_id=organization_id,
            from_generation=from_generation, to_generation=to_generation,
            aborted_reason="generation_id 不是合法 UUID（未校验身份、未快照、未切换）",
        )
    from_generation, to_generation = canonical_from, canonical_to

    store = object_store

    # 同 scope human 身份校验必须先于任何 preflight/snapshot/object-store/lock：
    # 未授权 actor 对 DB 仅发生这一次只读身份校验，不产生任何存储副作用。
    with db_connect() as conn:
        try:
            require_human(
                conn,
                switched_by,
                tenant_id=tenant_id,
                organization_id=organization_id,
                field_name="switched_by",
            )
        except ActorRequiredError as exc:
            return SwitchOutcome(
                status="aborted", tenant_id=tenant_id, organization_id=organization_id,
                from_generation=from_generation, to_generation=to_generation,
                aborted_reason=f"切换人不是本 scope 的 human（未快照、未切换）：{exc}",
            )
    # 身份校验通过即说明 switched_by 是合法 UUID：snapshot 账本与切换事务绑定 canonical 值
    #（PG 不接受 urn:uuid: 前缀，必须在编排层规范化后再下传）。
    switched_by = str(uuid.UUID(switched_by))

    problems = minimal_preflight(
        db_connect, tenant_id=tenant_id, organization_id=organization_id,
        from_generation=from_generation, to_generation=to_generation,
    )
    if problems:
        return SwitchOutcome(
            status="aborted", tenant_id=tenant_id, organization_id=organization_id,
            from_generation=from_generation, to_generation=to_generation,
            preflight_problems=tuple(problems),
            aborted_reason="最小前置检查未通过（未快照、未切换）",
        )

    # Snapshot the old graph before promotion; MVP accepts the small pre-lock window.
    snapshot = create_switch_snapshot(
        db_connect, store, tenant_id=tenant_id, organization_id=organization_id,
        from_generation=from_generation, created_by=switched_by,
    )

    # 简单排他锁 → 单事务切换 → 释放锁（finally）
    with db_connect() as lock_conn:
        acquire_exclusive_lock(lock_conn, tenant_id, organization_id)
        try:
            result = run_switch_transaction(
                lock_conn, tenant_id=tenant_id, organization_id=organization_id,
                from_generation=from_generation, to_generation=to_generation,
                switched_by=switched_by,
            )
        finally:
            try:
                release_exclusive_lock(lock_conn, tenant_id, organization_id)
            except Exception:
                pass  # 解锁失败由连接关闭兜底，不掩盖切换结果

    return SwitchOutcome(
        status="ok", tenant_id=tenant_id, organization_id=organization_id,
        from_generation=from_generation, to_generation=to_generation,
        snapshot=snapshot, retained=result["retained"], deleted=result["deleted"],
    )


__all__ = [
    "SwitchError", "SwitchGateError", "SwitchAborted", "SwitchOutcome",
    "minimal_preflight", "switch_graph",
    "acquire_exclusive_lock", "release_exclusive_lock", "create_switch_snapshot",
]
