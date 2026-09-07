"""Stable validation contracts and typed filing plans for Memory Service clients.

``ShadowFilingPlan`` 是 distill→Memory 影子归档的唯一入参形态：distill 负责 manifest
解析、document_fragments 查询与冻结来源（source_refs 快照）构造；Memory 在
caller-owned conn 的同一事务内消费 plan。字段只保留 filing 真正消费的最小集，
集合字段一律 tuple（冻结的不只是顶层）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memory_service.context_graph.types import (
    ContextGraphValidationError,
    RELATION_TYPES,
    TYPE_KEYS,
    required_relation_types,
    validate_entity_content,
    validate_relation_matrix,
)

__all__ = [
    "ContextGraphValidationError",
    "EntityFilingOutcome",
    "EntityFilingPlan",
    "FilingPlanResult",
    "FilingSourceRef",
    "RELATION_TYPES",
    "RequiredRelationPlan",
    "ShadowFilingPlan",
    "TYPE_KEYS",
    "required_relation_types",
    "validate_entity_content",
    "validate_relation_matrix",
]


@dataclass(frozen=True)
class FilingSourceRef:
    """冻结的单条来源引用：fragment 身份 + 逐字快照 + locator 原子字段。

    source refs 是完整性/控制数据，不适用「业务 content 可变 dict」例外。
    to_proposal_dict() 是到 memory_proposals.proposed_content 的唯一转换点
    （由 ingest 在 SQL 边调用），字典键集与既有提案存储形态逐字一致。
    """

    fragment_id: str
    ordinal: int
    excerpt_snapshot: str
    content_hash_snapshot: str
    document_id: str
    filename: str | None
    heading_path: str | None
    page_no: int | None
    paragraph_pos: int | None
    chunk_index: int
    fragment_ordinal: int

    def to_proposal_dict(self) -> dict[str, Any]:
        locator = {
            "document_id": self.document_id,
            "filename": self.filename,
            "heading_path": self.heading_path,
            "page_no": self.page_no,
            "paragraph_pos": self.paragraph_pos,
            "chunk_index": self.chunk_index,
            "fragment_ordinal": self.fragment_ordinal,
        }
        return {
            "fragment_id": self.fragment_id,
            "ordinal": self.ordinal,
            "excerpt_snapshot": self.excerpt_snapshot,
            "content_hash_snapshot": self.content_hash_snapshot,
            "source_locator_snapshot": {
                key: value for key, value in locator.items() if value is not None
            },
        }


@dataclass(frozen=True)
class RequiredRelationPlan:
    """一条必需边的登记计划：外部父（target_proposal_id）或同 manifest 目标（target_name）恰好其一。"""

    relation_type: str
    source_refs: tuple[FilingSourceRef, ...]
    target_name: str | None = None
    target_proposal_id: str | None = None
    content: dict[str, Any] = field(default_factory=dict)
    rationale: str | None = None


@dataclass(frozen=True)
class EntityFilingPlan:
    """单个实体的登记计划；字段与 memory_proposals.proposed_content 一一对应。"""

    name: str
    type_key: str
    content: dict[str, Any]
    source_refs: tuple[FilingSourceRef, ...]
    rationale: str | None = None
    strategic_level: str | None = None
    strategic_period: str | None = None
    outcome_level: str | None = None
    status_scope: str | None = None
    org_subtype: str | None = None
    is_moat: bool | None = None
    required_relations: tuple[RequiredRelationPlan, ...] = ()


@dataclass(frozen=True)
class ShadowFilingPlan:
    """一批影子归档计划。entities 必须已按 distill 拓扑序排列（父先于子）。"""

    tenant_id: str
    organization_id: str
    generation_label: str
    import_session_id: str
    pipeline: str
    document_id: str
    file_version: str
    entities: tuple[EntityFilingPlan, ...]
    proposed_by: str


@dataclass(frozen=True)
class EntityFilingOutcome:
    """单个实体的登记结果：proposed（skipped=False）或幂等 skip（skipped=True）。"""

    name: str
    type_key: str
    proposal_id: str
    skipped: bool


@dataclass(frozen=True)
class FilingPlanResult:
    """file_shadow_plan 的返回：generation 与逐实体结果（顺序 = plan.entities）。"""

    generation_id: str
    outcomes: tuple[EntityFilingOutcome, ...]
