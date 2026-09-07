"""Project a typed Context Pack into the M7-compatible dynamic response.

Context Graph entities deliberately retain their own read-only identity at the
Clark boundary: ``CG-{entity_uuid}``.  They are not interchangeable with WM
ISS/JDG/SGN codes, and this module performs no database lookup or allocation.
"""
from __future__ import annotations

from memory_service.context_graph.context_pack import NarrativeContextPack, PackLateral
from memory_service.context_graph.query_contracts import HitCandidate

from adapter.contracts import GkDynamic, GkSeed


_PATH_DESCRIPTION = "（strategic_path_v1 路径召回：已含愿景→命中节点主路径叙事）"
_TRUNCATION_MARKER = "…（按语义召回预算截断）"


def _seed_from_hit(hit: HitCandidate) -> GkSeed:
    return GkSeed(
        code=f"CG-{hit.entity_id}",
        type=hit.type_key,
        score=hit.similarity,
    )


def _seed_from_lateral(node: PackLateral) -> GkSeed:
    entity = node.entity
    return GkSeed(
        code=f"CG-{entity.entity_id}",
        type=entity.type_key,
        score=node.similarity,
    )


def _truncate(text: str, budget: int) -> tuple[str, bool]:
    """Apply the fixed character budget without splitting structured seeds."""
    if len(text) <= budget:
        return text, False
    if budget < len(_TRUNCATION_MARKER):
        return _TRUNCATION_MARKER[:budget], True
    return text[: budget - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER, True


def project_dynamic(
    pack: NarrativeContextPack,
    *,
    query: str,
    k: int,
    budget: int,
    narrative: str,
) -> GkDynamic:
    """Project one typed pack into deterministic ``GkDynamic`` data.

    Hits are ordered by their rank, followed by lateral nodes ordered by rank;
    the first occurrence of an entity wins.  The caller supplies the narrative
    rendered by memory_service's canonical Context Pack renderer.
    """
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    if type(budget) is not int or budget < 1:
        raise ValueError("budget must be a positive integer")
    if not isinstance(query, str):
        raise TypeError("query must be a string")

    seeds: list[GkSeed] = []
    seen_entity_ids: set[str] = set()
    ranked_hits = sorted(pack.hit_nodes, key=lambda item: item.rank)
    ranked_laterals = sorted(pack.lateral_nodes, key=lambda item: item.rank)
    for hit in ranked_hits:
        entity_id = str(hit.entity_id)
        if entity_id in seen_entity_ids:
            continue
        seen_entity_ids.add(entity_id)
        seeds.append(_seed_from_hit(hit))
        if len(seeds) >= k:
            break
    if len(seeds) < k:
        for node in ranked_laterals:
            entity_id = str(node.entity.entity_id)
            if entity_id in seen_entity_ids:
                continue
            seen_entity_ids.add(entity_id)
            seeds.append(_seed_from_lateral(node))
            if len(seeds) >= k:
                break

    if not isinstance(narrative, str):
        raise TypeError("narrative must be a string")
    full_text = f"{_PATH_DESCRIPTION}\n{narrative}"
    text, truncated = _truncate(full_text, budget)
    if not seeds:
        response_warning = "no_semantic_matches"
    elif truncated:
        response_warning = "narrative_truncated_to_budget"
    else:
        response_warning = None
    return GkDynamic(query=query, seeds=seeds, text=text, warning=response_warning)


__all__ = ("project_dynamic",)
