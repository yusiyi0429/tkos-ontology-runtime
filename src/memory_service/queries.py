"""Governed proposal reads and pending-content edits for host interfaces.

Edits remain pending and never write semantic snapshots directly.  Every function borrows the
caller's connection; transaction ownership stays with the caller.
"""
from __future__ import annotations

import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory_service.actors import ActorRequiredError, require_actor
from memory_service.context_graph.service import GraphInvariantError, validate_proposed_content
from memory_service.governance import (
    InvalidProposalContentError,
    ProposalAlreadyDecidedError,
    ProposalNotFoundError,
)

_PROPOSAL_COLS = (
    "proposal_id", "tenant_id", "organization_id", "target_kind", "target_id",
    "base_revision", "action", "proposed_content", "change_reason", "evidence",
    "source", "import_session_id", "confidence", "proposed_by", "status", "created_at",
)


def _row_to_proposal(row: dict) -> dict:
    out = dict(row)
    for k in ("proposal_id", "target_id", "proposed_by", "import_session_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    return out


def list_proposals(
    conn,
    *,
    tenant_id: str,
    organization_id: str,
    status: str = "pending",
    import_session_id: str | None = None,
) -> list[dict]:
    """列出提案（默认只列 pending），显式 tenant/org scope 必填，可按 Import Session 过滤。"""
    sql = (
        f"SELECT {', '.join(_PROPOSAL_COLS)} FROM memory_proposals"
        " WHERE tenant_id = %(tenant_id)s AND organization_id = %(organization_id)s"
    )
    params: dict[str, Any] = {"tenant_id": tenant_id, "organization_id": organization_id}
    if status:
        sql += " AND status = %(status)s"
        params["status"] = status
    if import_session_id:
        sql += " AND import_session_id = %(import_session_id)s"
        params["import_session_id"] = import_session_id
    sql += " ORDER BY import_session_id NULLS LAST, (target_kind = 'relation') ASC, created_at ASC"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_proposal(r) for r in rows]


def get_proposal(conn, proposal_id: str, *, tenant_id: str, organization_id: str) -> dict | None:
    """跨 scope 与不存在同样返回 None，不形成对象枚举侧信道。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {', '.join(_PROPOSAL_COLS)} FROM memory_proposals"
            " WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s",
            (proposal_id, tenant_id, organization_id),
        )
        row = cur.fetchone()
    return _row_to_proposal(row) if row else None


def get_current_snapshot(
    conn, target_kind: str, target_id: str | None, *, tenant_id: str, organization_id: str,
) -> dict | None:
    """提案 target_id 对应的当前快照（用于渲染 diff 的 before 侧），仅限显式 scope 内。"""
    if not target_id:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        if target_kind == "entity":
            cur.execute(
                "SELECT * FROM semantic_entities"
                " WHERE entity_id=%s AND tenant_id=%s AND organization_id=%s",
                (target_id, tenant_id, organization_id),
            )
        elif target_kind == "relation":
            cur.execute(
                "SELECT * FROM semantic_relations"
                " WHERE relation_id=%s AND tenant_id=%s AND organization_id=%s",
                (target_id, tenant_id, organization_id),
            )
        else:
            raise ValueError(f"非法 target_kind：{target_kind}")
        row = cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    for k in ("entity_id", "relation_id", "source_id", "target_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    out.pop("embedding", None)
    return out


def update_proposal_content(
    conn,
    proposal_id: str,
    new_content: dict,
    *,
    tenant_id: str,
    organization_id: str,
    actor: str,
) -> dict:
    """Edit a pending proposal without bypassing confirmation.

    ``tenant_id`` / ``organization_id`` / ``actor`` are mandatory and keyword-only; the actor
    must belong to the proposal's scope.  An absent proposal, a cross-scope proposal, and an
    out-of-scope actor all raise the same generalized ``ProposalNotFoundError`` so callers
    cannot enumerate foreign proposals.  The UPDATE itself carries the scope plus a
    ``status='pending'`` CAS and its rowcount is checked, so a concurrent decision cannot be
    overwritten even if the pre-check raced it.  Current Context Graph validation runs before
    persistence.  The first submitted content is retained in
    ``source.original_proposed_content`` for evaluation exports and is not replaced by
    later edits.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        # actor 授权（scoped SQL + FOR SHARE）必须先于任何 memory_proposals 读取：
        # invalid / out-of-scope actor 对 existing / missing proposal 的可观察 SQL
        # 顺序完全一致，ActorRequiredError 一律映射泛化 ProposalNotFoundError。
        try:
            require_actor(
                cur,
                actor,
                tenant_id=tenant_id,
                organization_id=organization_id,
                field_name="actor",
                lock=True,
            )
        except ActorRequiredError as exc:
            raise ProposalNotFoundError("proposal 不存在或不在调用者 scope") from exc
        # proposal_id 在 Python 侧规范化并绑定 canonical 值：非法 UUID 与
        # valid-but-missing 同类同文，不送 PG、不毒化调用方事务。
        try:
            canonical_proposal_id = uuid.UUID(str(proposal_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ProposalNotFoundError("proposal 不存在或不在调用者 scope") from exc
        cur.execute(
            "SELECT target_kind, action, status, proposed_content, source FROM memory_proposals"
            " WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s",
            (canonical_proposal_id, tenant_id, organization_id),
        )
        row = cur.fetchone()
        if row is None:
            # 泛化错误文案不含 proposal_id：随机 ID 与跨 scope ID 不可区分。
            raise ProposalNotFoundError("proposal 不存在或不在调用者 scope")
        if row["status"] != "pending":
            raise ProposalAlreadyDecidedError(f"proposal {proposal_id} 已处于终态：status={row['status']}")
        try:
            validate_proposed_content(row["target_kind"], new_content)
        except GraphInvariantError as exc:
            raise InvalidProposalContentError(str(exc)) from exc

        source = dict(row["source"] or {})
        if "original_proposed_content" not in source:
            source["original_proposed_content"] = row["proposed_content"]

        cur.execute(
            "UPDATE memory_proposals SET proposed_content=%s, source=%s"
            " WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s AND status='pending'",
            (Jsonb(new_content), Jsonb(source), canonical_proposal_id, tenant_id, organization_id),
        )
        if cur.rowcount != 1:
            raise ProposalAlreadyDecidedError(
                f"proposal {proposal_id} 已处于终态或发生并发修改（CAS 失败）"
            )
    # 同一借用 conn 上重读（调用方提交前可见本事务未提交的写入），保持原返回契约。
    return get_proposal(
        conn, str(canonical_proposal_id), tenant_id=tenant_id, organization_id=organization_id
    )


def diff_fields(before: dict | None, after: dict) -> list[tuple[str, Any, Any]]:
    """Return a simple field diff without introducing a general version-comparison engine.

    只比对 proposed_content 里实际出现的字段（after 的 key）：current snapshot 里的
    revision/status/tenant_id/created_at 等台账字段不属于提案内容，不进 diff（避免噪音与
    datetime 无法 JSON 序列化）。
    """
    before = before or {}
    out = []
    for k in after.keys():
        b, a = before.get(k), after.get(k)
        if b != a:
            out.append((k, b, a))
    return out


__all__ = [
    "list_proposals",
    "get_proposal",
    "get_current_snapshot",
    "update_proposal_content",
    "diff_fields",
    "InvalidProposalContentError",
]
