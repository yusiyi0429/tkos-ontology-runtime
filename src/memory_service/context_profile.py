"""Read-only facade for Working Memory Context Profiles and effective queries.

Contracts define immutable values and strict configuration.  The repository owns SQL and tier
assembly, while the renderer owns narrative budgets.  This module only orchestrates those parts.
Public functions borrow a caller connection, issue SELECT statements, and never manage transaction
boundaries or depend on a UI.
"""
from __future__ import annotations

from typing import Sequence

from memory_service import working
from memory_service.context_profile_contracts import (
    OBJECT_TYPE_ORDER,  # re-export the canonical working_contracts order
    ChainScopeError,
    ContextProfileError,
    ContextProfilePack,
    ContextProfileRequest,
    EffectiveQueryResult,
    InvalidRequestedTypesError,
    ProfileConfigError,
    RenderedNarrative,
    WorkingMemoryProfileConfig,
    load_working_memory_profile,
    normalize_requested_types,
)
from memory_service.context_profile_repository import (
    ContextProfileRepository,
    build_source_resolution_audit,
    load_chain_scoped,
)
from memory_service.context_profile_renderer import build_effective_query_suffix, render_model_context

__all__ = [
    "OBJECT_TYPE_ORDER",
    "ChainScopeError",
    "ContextProfileError",
    "ContextProfilePack",
    "ContextProfileRequest",
    "ContextProfileRepository",
    "EffectiveQueryResult",
    "InvalidRequestedTypesError",
    "ProfileConfigError",
    "RenderedNarrative",
    "WorkingMemoryProfileConfig",
    "augment_effective_query",
    "build_context_profile_pack",
    "load_working_memory_profile",
    "normalize_requested_types",
    "render_model_context",
]


def build_context_profile_pack(
    conn,
    request: ContextProfileRequest,
    *,
    profile: WorkingMemoryProfileConfig | None = None,
) -> ContextProfilePack:
    """构建结构化 Context Profile Pack（六档语义见 repository/render 模块 docstring）。"""
    cfg = profile or load_working_memory_profile()
    requested = normalize_requested_types(request.requested_object_types, allow_empty=False)

    # Missing and out-of-scope chains share one error so ownership is not disclosed.
    chain = load_chain_scoped(conn, request.chain_id, request.tenant_id, request.organization_id)

    repo = ContextProfileRepository(conn, request.chain_id)
    sections: dict[str, object] = {}
    present: dict[str, bool] = {}
    for t in requested:  # canonical order; repository snapshots are shared.
        sections[t], present[t] = repo.build_tier(t)

    audit = build_source_resolution_audit(
        [sections.get(t) for t in requested]
    )
    missing_types = tuple(t for t in requested if not present.get(t, False))
    return ContextProfilePack(
        profile_id=cfg.profile_id,
        profile_name=cfg.name,
        chain_id=str(request.chain_id),
        tenant_id=request.tenant_id,
        organization_id=request.organization_id,
        chain_title=chain["title"],
        chain_status=chain["status"],
        requested_object_types=requested,
        missing_object_types=missing_types,
        character_budget_default=cfg.character_budget,
        signal_section=sections.get("Signal"),
        issue_section=sections.get("Issue"),
        judgment_section=sections.get("Judgment"),
        agreement_section=sections.get("Agreement"),
        mission_section=sections.get("Strategic Mission"),
        close_section=sections.get("Close"),
        source_resolution_audit=audit,
    )


def augment_effective_query(
    conn,
    *,
    base_query: str,
    chain_id: str,
    tenant_id: str,
    organization_id: str,
    requested_object_types: Sequence[str],
    profile: WorkingMemoryProfileConfig | None = None,
) -> EffectiveQueryResult:
    """Append current Signal and Issue fields explicitly referenced by this message.

    - 只有显式类型中的 Signal / Issue 参与扩充（且须在配置 effective_query.
      object_types 启用集合内），按 canonical 顺序拼接；
    - 只有绑定、没有显式引用，或显式类型不在配置启用集合内时原样返回
      base_query 且零数据库访问（只读缓存配置）；
    - 新增段（换行 + 前缀 + 条目 + 结尾句号）受配置化 max_added_chars 独立
      预算约束，恒有 len(新增段) <= max_added_chars；used_record_ids
      只含实际进入 augmented_query 的版本行，截断情况在 truncated 中如实上报。
    """
    explicit = normalize_requested_types(requested_object_types, allow_empty=True)
    if not explicit:
        return EffectiveQueryResult(
            base_query=base_query, augmented_query=base_query,
            used_object_types=(), used_record_ids=(),
        )

    cfg = profile or load_working_memory_profile()
    enabled = set(cfg.effective_query.object_types)
    eligible = tuple(t for t in explicit if t in enabled)
    if not eligible:
        # 显式类型存在但均未被配置启用：不扩 query，零 DB
        return EffectiveQueryResult(
            base_query=base_query, augmented_query=base_query,
            used_object_types=(), used_record_ids=(),
        )

    load_chain_scoped(conn, chain_id, tenant_id, organization_id)

    repo = ContextProfileRepository(conn, chain_id)
    entries: list[tuple[str, str, str, str]] = []  # (record_id, object_type, label, text)
    for t in eligible:
        rows = repo.build_tier(t)[0]
        fields = cfg.effective_query.signal_fields if t == "Signal" else cfg.effective_query.issue_fields
        label = "信号" if t == "Signal" else "议题"
        views = rows.signals if t == "Signal" else (
            [v for v in rows.versions if v.record_id == rows.current_record_id] if rows.versions else []
        )
        for v in views:
            pieces = [
                v.content[f].strip()
                for f in fields
                if isinstance(v.content.get(f), str) and v.content[f].strip()
            ]
            if not pieces:
                continue
            entries.append((v.record_id, t, label, "；".join(pieces)))

    if not entries:
        return EffectiveQueryResult(
            base_query=base_query, augmented_query=base_query,
            used_object_types=(), used_record_ids=(),
        )
    suffix, included_ids, truncated = build_effective_query_suffix(
        [(rid, label, text) for rid, _t, label, text in entries],
        max_added_chars=cfg.effective_query.max_added_chars,
        needs_newline=bool(base_query.strip()),
    )
    if suffix is None or not included_ids:
        # 有条目但因预算一条都放不下：新增段为空，如实上报截断
        return EffectiveQueryResult(
            base_query=base_query, augmented_query=base_query,
            used_object_types=(), used_record_ids=(), truncated=truncated,
        )
    included = set(included_ids)
    used_types = tuple(t for t in eligible if any(rid in included for rid, ot, _l, _x in entries if ot == t))
    augmented = f"{base_query}\n{suffix}" if base_query.strip() else suffix
    return EffectiveQueryResult(
        base_query=base_query,
        augmented_query=augmented,
        used_object_types=used_types,
        used_record_ids=tuple(included_ids),
        truncated=truncated,
    )
