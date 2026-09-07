from __future__ import annotations

from memory_service.context_graph import context_pack as cp
from memory_service.context_graph.query_contracts import (
    EntityPayload,
    GenerationRef,
    HitCandidate,
    OwnerResolution,
    PackAudit,
    RelationPayload,
    RetrievalAudit,
    RetrievalBudgets,
    SourcesAudit,
)

from adapter.pack_projection import project_dynamic


def _audit() -> PackAudit:
    generation = GenerationRef("generation", "测试代", "current")
    return PackAudit(
        retrieval=RetrievalAudit(
            budgets=RetrievalBudgets(),
            generation=generation,
            hit_candidates=0,
            seeds_dropped=(),
            paths_dropped=(),
            lateral_candidate_nodes=0,
            lateral_selected=0,
            lateral_pruned=(),
            intra_path_business_edges_skipped=0,
        ),
        sources=SourcesAudit(
            owners_checked=0,
            owners_resolvable=0,
            owners_with_zero_refs=(),
            refs_total=0,
            refs_resolved=0,
            refs_unresolved=(),
        ),
    )


def _entity(entity_id: str, type_key: str) -> EntityPayload:
    return EntityPayload(
        entity_id=entity_id,
        type_key=type_key,
        name=f"节点-{entity_id}",
        content={},
        rationale=None,
        revision=1,
        status="confirmed",
        confirmed_by=None,
        confirmed_at=None,
    )


def _lateral(entity_id: str, type_key: str, *, rank: int, similarity: float) -> cp.PackLateral:
    relation = RelationPayload(
        relation_id=f"relation-{entity_id}",
        relation_type="supports",
        source_id="anchor",
        target_id=entity_id,
        content={},
        rationale=None,
        revision=1,
        status="confirmed",
    )
    return cp.PackLateral(
        entity=_entity(entity_id, type_key),
        relation=relation,
        entity_resolution=OwnerResolution("entity", entity_id, ()),
        relation_resolution=OwnerResolution("relation", relation.relation_id, ()),
        attached_to_entity_id="anchor",
        similarity=similarity,
        rank=rank,
    )


def _pack(
    hits: tuple[HitCandidate, ...] = (),
    laterals: tuple[LateralNode, ...] = (),
) -> cp.NarrativeContextPack:
    generation = GenerationRef("generation", "测试代", "current")
    return cp.NarrativeContextPack(
        pack_version=cp.PACK_VERSION,
        retrieval_mode="strategic_path_v1",
        tenant_id="tenant",
        organization_id="organization",
        generation=generation,
        effective_query="原始问题",
        hit_nodes=hits,
        main_paths=(),
        lateral_nodes=laterals,
        source_resolutions=(),
        audit=_audit(),
    )


def test_project_orders_hits_then_laterals_deduplicates_and_caps() -> None:
    pack = _pack(
        hits=(
            HitCandidate("hit-2", "Issue", "二", 0.2, 0.82, 2),
            HitCandidate("hit-1", "Outcome", "一", 0.1, 0.91, 1),
        ),
        laterals=(
            _lateral("hit-2", "Issue", rank=1, similarity=0.99),
            _lateral("lateral-1", "Domain", rank=2, similarity=0.71),
        ),
    )
    result = project_dynamic(pack, query="q", k=3, budget=500, narrative="叙事")

    assert [seed.model_dump() for seed in result.seeds or []] == [
        {"code": "CG-hit-1", "type": "Outcome", "score": 0.91},
        {"code": "CG-hit-2", "type": "Issue", "score": 0.82},
        {"code": "CG-lateral-1", "type": "Domain", "score": 0.71},
    ]
    assert result.text == "（strategic_path_v1 路径召回：已含愿景→命中节点主路径叙事）\n叙事"
    assert result.warning is None


def test_project_shape_and_repeatability() -> None:
    pack = _pack(
        hits=(HitCandidate("550e8400-e29b-41d4-a716-446655440000", "Issue", "问题", 0.1, 0.5, 1),),
    )
    first = project_dynamic(pack, query="q", k=5, budget=500, narrative="正文")
    second = project_dynamic(pack, query="q", k=5, budget=500, narrative="正文")

    assert first.model_dump() == second.model_dump()
    assert first.seeds and first.seeds[0].code == "CG-550e8400-e29b-41d4-a716-446655440000"
    assert first.seeds[0].type == "Issue"
    assert first.seeds[0].score == 0.5


def test_budget_exact_and_truncation_keep_seeds() -> None:
    pack = _pack(
        hits=(HitCandidate("hit-1", "Issue", "一", 0.1, 0.5, 1),),
        laterals=(_lateral("lateral-1", "Domain", rank=1, similarity=0.4),),
    )
    marker = "…（按语义召回预算截断）"
    full = "（strategic_path_v1 路径召回：已含愿景→命中节点主路径叙事）\n完整叙事"

    exact = project_dynamic(pack, query="q", k=5, budget=len(full), narrative="完整叙事")
    assert exact.text == full
    assert exact.warning is None

    truncated = project_dynamic(pack, query="q", k=1, budget=len(marker) + 4, narrative="完整叙事")
    assert truncated.text == full[:4] + marker
    assert len(truncated.text or "") == len(marker) + 4
    assert truncated.warning == "narrative_truncated_to_budget"
    assert len(truncated.seeds or []) == 1  # text 截断不影响结构化 seeds


def test_budget_smaller_than_marker_returns_marker_prefix() -> None:
    pack = _pack(
        hits=(HitCandidate("hit-1", "Issue", "一", 0.1, 0.5, 1),),
    )
    marker = "…（按语义召回预算截断）"
    result = project_dynamic(pack, query="q", k=1, budget=len(marker) - 1, narrative="很长的正文")
    assert result.text == marker[: len(marker) - 1]
    assert len(result.text or "") == len(marker) - 1
    assert result.warning == "narrative_truncated_to_budget"


def test_empty_seeds_have_explicit_warning_even_when_text_is_tiny() -> None:
    result = project_dynamic(_pack(), query="q", k=2, budget=1, narrative="无匹配")
    assert result.seeds == []
    assert len(result.text or "") <= 1
    assert result.warning == "no_semantic_matches"
