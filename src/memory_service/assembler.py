"""Assemble only the memory slices explicitly requested by a run.

连接边界（host capability boundary）：本模块不自开连接。``memory_context_get`` 显式接收
``connect_factory``（宿主提供，如 aw.db.connect），按需开短事务；模型 gateway
也由宿主显式传入。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable

from memory_service import context_profile as cp
from memory_service import episodic, user_memory
from memory_service.common import sha256_hex

SUPPORTED_SLOTS = (
    "conversation_summary",
    "user_context",
    "episodic_recall",
    "context_graph",
    "issue_chain",
)

_SLOT_HEADINGS = {
    "conversation_summary": "对话摘要",
    "episodic_recall": "相关历史记录",
    "context_graph": "公司经营战略背景",
    "user_context": "用户长期记忆",
    "issue_chain": "当前议题链经营背景",
}


def render_injected_context(slices: dict[str, object]) -> str:
    """Render memory slices in the Memory Service's canonical order."""
    sections: list[str] = []
    ordered = [slot for slot in SUPPORTED_SLOTS if slot in slices]
    ordered.extend(slot for slot in slices if slot not in SUPPORTED_SLOTS)
    for slot in ordered:
        value = slices[slot]
        if isinstance(value, str):
            text = value
        elif value is None:
            text = "(无内容)"
        else:
            text = json.dumps(value, ensure_ascii=False, default=str)
        sections.append(f"## {_SLOT_HEADINGS.get(slot, slot)}\n\n{text}")
    return "\n\n".join(sections)

_DEFAULT_TOP_N_USER_MEMORIES = 5
_DEFAULT_TOP_K_EPISODES = 3
_EPISODIC_MIN_SCORE = 0.52


def _augment_issue_chain_query(
    conn,
    *,
    base_query: str,
    chain_id: str,
    tenant_id: str,
    organization_id: str,
    referenced_types,
) -> tuple[str, tuple[str, ...]]:
    """Add explicitly referenced Signal/Issue content to the retrieval query."""
    cfg = cp.load_working_memory_profile()
    result = cp.augment_effective_query(
        conn,
        base_query=base_query,
        chain_id=chain_id,
        tenant_id=tenant_id,
        organization_id=organization_id,
        requested_object_types=list(referenced_types),
        profile=cfg,
    )
    return result.augmented_query, result.used_record_ids


def build_issue_chain_slot(
    conn,
    *,
    chain_id: str,
    tenant_id: str,
    organization_id: str,
    referenced_types,
) -> tuple[str, dict]:
    """Build the structured Working Memory chain slice for explicit references."""
    cfg = cp.load_working_memory_profile()
    request = cp.ContextProfileRequest(
        chain_id=chain_id,
        tenant_id=tenant_id,
        organization_id=organization_id,
        requested_object_types=tuple(referenced_types),
    )
    pack = cp.build_context_profile_pack(conn, request, profile=cfg)
    rendered = cp.render_model_context(pack, character_budget=cfg.character_budget)
    return rendered.text, {
        "chain_id": str(chain_id),
        "tenant_id": tenant_id,
        "organization_id": organization_id,
        "referenced_object_types": list(referenced_types),
        "requested_object_types": list(pack.requested_object_types),
        "missing_object_types": list(pack.missing_object_types),
        "character_budget": cfg.character_budget,
        "narrative_used_chars": rendered.budget.used,
        "narrative_truncated": rendered.budget.truncated,
        "source_refs_checked": pack.source_resolution_audit.refs_checked,
        "source_refs_mismatched": pack.source_resolution_audit.mismatched,
        "source_refs_missing": pack.source_resolution_audit.missing,
    }


def build_context_graph_slot(
    effective_query: str,
    *,
    tenant_id: str,
    organization_id: str,
    gateway,
    embedding_dim: int,
    _connect: Callable[..., Any],
) -> tuple[str, dict]:
    """Build the strategic Context Graph narrative and its retrieval audit.

    gateway（满足 Embedder + Compressor 协议）、embedding_dim、_connect 均由宿主显式传入。
    叙事在读时用 gateway.chat 压缩一次（fail-closed：压缩失败即本次检索失败，不回退未压缩全文）。
    """
    from memory_service.context_graph.context_pack import strategic_context

    if not hasattr(gateway, "embed"):
        raise TypeError("context_graph slot requires a gateway with embed()")
    if not hasattr(gateway, "chat"):
        raise TypeError("context_graph slot requires a gateway with chat()")
    result = strategic_context(
        tenant_id, organization_id, effective_query,
        embedder=gateway, compressor=gateway, embedding_dim=embedding_dim, _connect=_connect,
    )
    pack = result["pack"]
    audit = pack.to_json().get("audit", {})
    return result["narrative"], {
        "tenant_id": tenant_id,
        "organization_id": organization_id,
        "effective_query": effective_query,
        "retrieval_mode": "strategic_path_v1",
        "generation_id": pack.generation.generation_id,
        "hit_candidates": audit.get("hit_candidates", 0),
        "paths_built": len(pack.main_paths),
        "lateral_selected": audit.get("lateral_selected", 0),
        "narrative_raw_chars": result["narrative_raw_chars"],
        "narrative_chars": result["narrative_chars"],
    }


def _format_user_context(items: list[dict]) -> str:
    if not items:
        return "(暂无用户长期记忆)"
    return "\n".join(f"- [{item['kind']}] {item['content']}" for item in items)


def _format_episodic_recall(items: list[dict]) -> str:
    if not items:
        return "(未召回到相关历史 Episode)"
    return "\n".join(f"- {item['content']}" for item in items)


def _record_slice(context: dict[str, str], records: dict[str, dict], name: str, text: str, retrieval: dict) -> None:
    context[name] = text
    records[name] = {"retrieval": retrieval, "content_sha256": sha256_hex(text)}


def memory_context_get(
    user_id: str,
    conversation_id: str | None,
    org_id: str,
    query: str,
    slots: list[str] | None,
    *,
    gateway,
    connect_factory: Callable[..., Any],
    embedding_dim: int,
    issue_chain_id: str | None = None,
    issue_object_types: list[str] | None = None,
    tenant_id: str = "local",
) -> tuple[dict[str, str], dict]:
    """Return requested memory slices and an auditable assembly record.

    ``gateway``/``connect_factory``/``embedding_dim`` 由宿主显式传入；未请求的 slot 零查询。
    """
    slots = slots or []
    unknown = [slot for slot in slots if slot not in SUPPORTED_SLOTS]
    if unknown:
        raise ValueError(f"不支持的 slot：{unknown}，支持的 slot：{SUPPORTED_SLOTS}")

    context: dict[str, str] = {}
    slice_records: dict[str, dict] = {}
    effective_query = query
    issue_text: str | None = None
    issue_retrieval: dict = {}
    issue_record_ids: tuple[str, ...] = ()

    if issue_chain_id and issue_object_types:
        with connect_factory() as conn:
            effective_query, issue_record_ids = _augment_issue_chain_query(
                conn,
                base_query=effective_query,
                chain_id=issue_chain_id,
                tenant_id=tenant_id,
                organization_id=org_id,
                referenced_types=tuple(issue_object_types),
            )
            issue_text, issue_retrieval = build_issue_chain_slot(
                conn,
                chain_id=issue_chain_id,
                tenant_id=tenant_id,
                organization_id=org_id,
                referenced_types=tuple(issue_object_types),
            )

    if "conversation_summary" in slots:
        retrieval = {"conversation_id": conversation_id}
        if conversation_id:
            with connect_factory() as conn:
                summary = episodic.get_or_create_conversation_summary(
                    conn, conversation_id, gateway=gateway
                )
            text = summary.get("content", "")
            retrieval["summary_id"] = summary.get("summary_id")
        else:
            text = "(未提供 conversation_id，无法生成摘要)"
        _record_slice(context, slice_records, "conversation_summary", text, retrieval)

    if "episodic_recall" in slots:
        retrieval = {
            "user_id": user_id,
            "query": query,
            "effective_query": effective_query,
            "top_k": _DEFAULT_TOP_K_EPISODES,
            "min_score": _EPISODIC_MIN_SCORE,
        }
        episodes = (
            episodic.recall_episodes(
                connect_factory, user_id, effective_query, top_k=_DEFAULT_TOP_K_EPISODES, gateway=gateway
            )
            if effective_query
            else []
        )
        episodes = [episode for episode in episodes if episode["score"] >= _EPISODIC_MIN_SCORE]
        retrieval["hit_summary_ids"] = [episode["summary_id"] for episode in episodes]
        _record_slice(
            context, slice_records, "episodic_recall", _format_episodic_recall(episodes), retrieval
        )

    if "user_context" in slots:
        with connect_factory() as conn:
            memories = user_memory.list_user_memories(conn, user_id)[:_DEFAULT_TOP_N_USER_MEMORIES]
        retrieval = {
            "user_id": user_id,
            "top_n": _DEFAULT_TOP_N_USER_MEMORIES,
            "hit_memory_ids": [memory["memory_id"] for memory in memories],
        }
        _record_slice(context, slice_records, "user_context", _format_user_context(memories), retrieval)

    if "issue_chain" in slots:
        retrieval = {
            "chain_id": issue_chain_id,
            "referenced_object_types": list(issue_object_types or []),
        }
        if issue_text is None:
            text = "(未构建议题链上下文：本消息无显式引用)"
        else:
            text = issue_text
            retrieval.update(issue_retrieval)
            retrieval["used_record_ids"] = list(issue_record_ids)
        _record_slice(context, slice_records, "issue_chain", text, retrieval)

    if "context_graph" in slots:
        text, retrieval = build_context_graph_slot(
            effective_query,
            tenant_id=tenant_id,
            organization_id=org_id,
            gateway=gateway,
            embedding_dim=embedding_dim,
            _connect=connect_factory,
        )
        _record_slice(context, slice_records, "context_graph", text, retrieval)

    return context, {
        "user_id": str(user_id),
        "conversation_id": str(conversation_id) if conversation_id else None,
        "organization_id": org_id,
        "query": query,
        "slots_requested": list(slots),
        "slots_assembled": list(context),
        "slices": slice_records,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
