"""graph query ``strategic_path_v1`` 类型化契约：常量、异常、向量校验与数据结构。

本模块只承载跨层契约，不含 SQL、渲染或编排逻辑：

- 常量与枚举值（retrieval_mode、横向预算区间、legacy generation 标识）；
- 检索层异常体系（含 fail-closed 来源解析异常的结构化载荷）；
- ``validate_query_vector``：embedding 维度/有限性/非零校验（规划阶段、事务外调用）；
- typed 数据结构（QueryPlan、GraphRetrieval、OwnerResolution、审计记录等）。

契约约定：
- 集合一律用 ``tuple``（不可变）；frozen dataclass 构造后不再原地修改。
- 业务 JSON content（EntityPayload.content / RelationPayload.content）保留 dict
  原样，属业务数据；核心控制状态不得用裸 dict 表达。
- ``OwnerResolution.resolvable`` 为严格语义：refs 非空 且 每条 ref 都真实解析
  （与治理写入对每条 ref 的快照校验一致）；任何一条失配/缺失即不可解析。

所有模块（query_repository / query / context_pack）只从这里 import 公共名字，
禁止互相 import 私有成员。
"""
from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol



class Embedder(Protocol):
    """可注入的向量化协议；生产实例由宿主显式注入，Memory 内无默认实现。"""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Compressor(Protocol):
    """可注入的叙事压缩协议（chat 补全）；生产实例由宿主显式注入，与
    ``Embedder`` 同一级别的 host capability boundary。返回对象需具备 ``.content`` 属性
    （与 ``episodic.py``/``aw.models.ChatResult`` 同形，不在本包内定义新结构）。
    """

    def chat(self, messages: list[dict[str, str]], *, max_tokens: int) -> Any: ...


STRATEGIC_PATH_V1 = "strategic_path_v1"
RETRIEVAL_MODES = (STRATEGIC_PATH_V1,)

# Lateral business nodes share one Context Pack budget in the inclusive range 1..2.
LATERAL_BUDGET_MIN = 1
LATERAL_BUDGET_MAX = 2

# Archived generation created by a published migration.  Its confirmed rows predate frozen source
# refs, so source fail-closed checks apply only to current typed generations.
LEGACY_GENERATION_ID = "00000000-0000-0000-0000-0000000000e1"


def is_legacy_generation(generation_id: str) -> bool:
    return str(generation_id) == LEGACY_GENERATION_ID


class QueryError(RuntimeError):
    """检索层基础异常。"""


class RetrievalModeError(QueryError):
    """retrieval_mode 不是当前唯一合法值 strategic_path_v1。"""


class EmbeddingValidationError(QueryError):
    """查询向量维度不符、含非有限值或范数为零。"""


class NoCurrentGenerationError(QueryError):
    """scope 下不存在 status='current' 的 Context Graph generation。"""


class BudgetValidationError(QueryError):
    """召回预算不满足契约（横向预算必须 1..2 等）。"""


class SourceResolutionError(QueryError):
    """新代 current 图被选 owner 的来源不可真实解析：fail closed。

    携带结构化 ``unresolved`` 载荷（owner 与 mismatch 原因），审计与排障
    不依赖异常消息字符串。
    """

    def __init__(self, unresolved: list[dict[str, Any]]):
        self.unresolved = unresolved
        owners = ", ".join(
            f"{item['owner_kind']}:{item['owner_id']}({item['mismatch']})" for item in unresolved
        )
        super().__init__(f"selected owners have unresolvable source refs: {owners}")


def validate_query_vector(
    vector: list[float] | tuple[float, ...], *, embedding_dim: int,
) -> tuple[float, ...]:
    """校验查询向量：维度等于调用方传入的 embedding_dim、全部有限、范数非零。

    在规划阶段（事务外）调用；非法向量立即失败，不进入数据库路径。
    embedding_dim 由宿主显式传入（host capability boundary：Memory 不读 settings）。
    """
    expected = int(embedding_dim)
    if not isinstance(vector, (list, tuple)):
        raise EmbeddingValidationError("query vector must be a list or tuple of floats")
    if len(vector) != expected:
        raise EmbeddingValidationError(
            f"query vector must have {expected} dimensions (embedding_dim), got {len(vector)}"
        )
    values: list[float] = []
    for value in vector:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise EmbeddingValidationError("query vector contains a non-numeric value") from exc
        if not math.isfinite(number):
            raise EmbeddingValidationError("query vector contains NaN or infinity")
        values.append(number)
    if math.sqrt(sum(number * number for number in values)) == 0.0:
        raise EmbeddingValidationError("query vector must not be the zero vector")
    return tuple(values)


def jsonable(value: Any) -> Any:
    """把 PG/datetime/UUID 值转为稳定 JSON 值；业务 JSON 原样保留。

    边界序列化的唯一公共入口（替代跨模块私有 helper）。
    """
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bool, int)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QueryError("retrieval result contains non-finite float")
        return value
    if isinstance(value, str):
        return value
    return str(value)


def _jsonable_dict(data: Any) -> dict[str, Any]:
    return {str(key): jsonable(item) for key, item in asdict(data).items()}


@dataclass(frozen=True)
class RetrievalBudgets:
    """召回预算。默认值即  首版裁剪策略。

    - hit_limit：向量命中的候选叶子数上限（多路径材料）；
    - max_paths：去重后保留的主路径条数上限；
    - lateral_budget：横向业务节点总数预算，硬约束 1..2，不按路径或节点重复计算；
    - max_climb_depth：primary_alignment 上溯步数护栏，防御图数据异常。
    """

    hit_limit: int = 3
    max_paths: int = 2
    lateral_budget: int = 2
    max_climb_depth: int = 64

    def __post_init__(self) -> None:
        if self.hit_limit < 1:
            raise BudgetValidationError(f"hit_limit must be >= 1, got {self.hit_limit}")
        if self.max_paths < 1:
            raise BudgetValidationError(f"max_paths must be >= 1, got {self.max_paths}")
        if not (LATERAL_BUDGET_MIN <= self.lateral_budget <= LATERAL_BUDGET_MAX):
            raise BudgetValidationError(
                "lateral_budget is a whole-pack total and must be "
                f"{LATERAL_BUDGET_MIN}..{LATERAL_BUDGET_MAX}, got {self.lateral_budget}"
            )
        if not (1 <= self.max_climb_depth <= 256):
            raise BudgetValidationError(
                f"max_climb_depth must be within 1..256, got {self.max_climb_depth}"
            )

    def to_json(self) -> dict[str, int]:
        return {
            "hit_limit": self.hit_limit,
            "max_paths": self.max_paths,
            "lateral_budget": self.lateral_budget,
            "max_climb_depth": self.max_climb_depth,
        }


@dataclass(frozen=True)
class QueryPlan:
    """查询规划产物（纯内存，无数据库状态）。

    current generation 的解析移入执行阶段的一致性事务内；
    query_vector 只存在于规划对象与 SQL 参数中，永不进入 Pack 与叙事。
    """

    retrieval_mode: str
    tenant_id: str
    organization_id: str
    effective_query: str
    query_vector: tuple[float, ...]
    budgets: RetrievalBudgets

    def to_json(self) -> dict[str, Any]:
        payload = _jsonable_dict(self)
        payload.pop("query_vector", None)  # 向量绝不序列化
        return payload


@dataclass(frozen=True)
class GenerationRef:
    generation_id: str
    label: str
    status: str

    def to_json(self) -> dict[str, str]:
        return {"generation_id": self.generation_id, "label": self.label, "status": self.status}


@dataclass(frozen=True)
class HitCandidate:
    """向量命中的候选叶子（最小投影，不含 embedding）。"""

    entity_id: str
    type_key: str
    name: str
    distance: float
    similarity: float
    rank: int

    def to_json(self) -> dict[str, Any]:
        return _jsonable_dict(self)


@dataclass(frozen=True)
class EntityPayload:
    """confirmed 实体的业务载荷（无检索元数据）。"""

    entity_id: str
    type_key: str
    name: str
    content: dict[str, Any]
    rationale: str | None
    revision: int
    status: str
    confirmed_by: str | None
    confirmed_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        return _jsonable_dict(self)


@dataclass(frozen=True)
class RelationPayload:
    """confirmed 关系的业务载荷（无检索元数据）。"""

    relation_id: str
    relation_type: str
    source_id: str
    target_id: str
    content: dict[str, Any]
    rationale: str | None
    revision: int
    status: str

    def to_json(self) -> dict[str, Any]:
        return _jsonable_dict(self)


@dataclass(frozen=True)
class ClimbStep:
    """primary_alignment 上溯的一步（种子行 relation_id 为 None）。"""

    entity_id: str
    depth: int
    relation_id: str | None


@dataclass(frozen=True)
class MainPath:
    seed_entity_id: str
    seed_rank: int
    nodes: tuple[EntityPayload, ...]  # 命中节点 → … → CompanyVision
    relations: tuple[RelationPayload, ...]  # 长度 = len(nodes) - 1，子 → 父

    def to_json(self) -> dict[str, Any]:
        return {
            "seed_entity_id": self.seed_entity_id,
            "seed_rank": self.seed_rank,
            "nodes": [node.to_json() | {"depth_from_seed": depth}
                      for depth, node in enumerate(self.nodes)],
            "relations": [relation.to_json() for relation in self.relations],
        }


@dataclass(frozen=True)
class LateralCandidate:
    """横向业务关系候选行（仓储层产出，含两侧相似度与真实解析 hint）。"""

    relation_id: str
    relation_type: str
    source_id: str
    target_id: str
    source_similarity: float | None
    target_similarity: float | None
    source_refs_resolvable: bool
    target_refs_resolvable: bool
    relation_refs_resolvable: bool


@dataclass(frozen=True)
class LateralSelection:
    """被选横向节点（按节点去重后的一条确定性选择）。"""

    entity_id: str
    relation_id: str
    relation_type: str
    attached_to_entity_id: str
    similarity: float | None
    refs_resolvable: bool
    anchor_index: int
    rank: int


@dataclass(frozen=True)
class LateralSelectionStats:
    """横向选择审计（typed）。"""

    candidate_nodes: int
    intra_path_business_edges_skipped: int
    selected: int
    pruned: tuple["LateralPrune", ...]


@dataclass(frozen=True)
class LateralNode:
    entity: EntityPayload
    relation: RelationPayload
    attached_to_entity_id: str  # 该业务边落在主路径上的端点
    similarity: float | None  # 横向端点与查询的余弦相似度；无 embedding 时为 None
    rank: int

    def to_json(self) -> dict[str, Any]:
        payload = self.entity.to_json()
        payload |= {
            "similarity": self.similarity,
            "rank": self.rank,
            "attached_to_entity_id": self.attached_to_entity_id,
            "relation": self.relation.to_json(),
        }
        return payload


@dataclass(frozen=True)
class FragmentLocator:
    """来源片段定位。0 是合法值，不得用 truthy 判断丢弃。"""

    heading_path: str | None
    page_no: int | None
    paragraph_pos: int | None


@dataclass(frozen=True)
class ResolvedSourceRef:
    owner_kind: str  # entity / relation
    owner_id: str
    fragment_id: str
    ordinal: int
    document_id: str | None
    filename: str | None
    locator: FragmentLocator
    excerpt: str  # refs 表快照（治理侧写入值）
    content_hash: str
    resolved: bool
    mismatch: str | None  # source_snapshot_hash_mismatch

    def to_json(self) -> dict[str, Any]:
        payload = _jsonable_dict(self)
        payload["locator"] = _jsonable_dict(self.locator)
        return payload


@dataclass(frozen=True)
class OwnerResolution:
    """单个被选 owner 的来源解析结果（owner-level，含零 refs 的显式报告）。

    ``resolvable`` 为严格语义：refs 非空且每条服务自持冻结快照完整。
    混合一条好 ref + 一条坏 ref 仍视为不可解析。
    """

    owner_kind: str
    owner_id: str
    refs: tuple[ResolvedSourceRef, ...]

    @property
    def resolvable(self) -> bool:
        return bool(self.refs) and all(ref.resolved for ref in self.refs)

    @property
    def mismatch(self) -> str | None:
        """不可解析原因；严格可解析时为 None。"""
        if self.resolvable:
            return None
        if not self.refs:
            return "no_source_refs"
        return next((ref.mismatch for ref in self.refs if not ref.resolved), "no_source_refs")

    def to_json(self) -> dict[str, Any]:
        return {
            "owner_kind": self.owner_kind,
            "owner_id": self.owner_id,
            "ref_count": len(self.refs),
            "resolvable": self.resolvable,
            "mismatch": self.mismatch,
            "refs": [ref.to_json() for ref in self.refs],
        }


@dataclass(frozen=True)
class SeedDrop:
    seed_entity_id: str
    reason: str


@dataclass(frozen=True)
class PathDrop:
    seed_entity_id: str
    reason: str


@dataclass(frozen=True)
class LateralPrune:
    entity_id: str
    reason: str


@dataclass(frozen=True)
class RetrievalAudit:
    budgets: RetrievalBudgets
    generation: GenerationRef
    hit_candidates: int
    seeds_dropped: tuple[SeedDrop, ...]
    paths_dropped: tuple[PathDrop, ...]
    lateral_candidate_nodes: int
    lateral_selected: int
    lateral_pruned: tuple[LateralPrune, ...]
    intra_path_business_edges_skipped: int

    def to_json(self) -> dict[str, Any]:
        return _jsonable_dict(self)


@dataclass(frozen=True)
class GraphRetrieval:
    """strategic_path_v1 图检索结果（同一只读一致性事务内的完整快照）。

    source_resolutions 覆盖全部被选 owner（主路径节点/边 + 横向节点/边），
    零-ref owner 也在列；新代 current 图存在不可解析 owner 时执行阶段
    已 fail closed（SourceResolutionError）。
    """

    retrieval_mode: str
    tenant_id: str
    organization_id: str
    generation: GenerationRef
    effective_query: str
    hit_nodes: tuple[HitCandidate, ...]
    main_paths: tuple[MainPath, ...]
    lateral_nodes: tuple[LateralNode, ...]
    source_resolutions: tuple[OwnerResolution, ...]
    audit: RetrievalAudit

    def owner_resolution(self, owner_kind: str, owner_id: str) -> OwnerResolution | None:
        for resolution in self.source_resolutions:
            if resolution.owner_kind == owner_kind and resolution.owner_id == owner_id:
                return resolution
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "retrieval_mode": self.retrieval_mode,
            "tenant_id": self.tenant_id,
            "organization_id": self.organization_id,
            "generation": self.generation.to_json(),
            "effective_query": self.effective_query,
            "hit_nodes": [hit.to_json() for hit in self.hit_nodes],
            "main_paths": [path.to_json() for path in self.main_paths],
            "lateral_nodes": [node.to_json() for node in self.lateral_nodes],
            "source_resolutions": [item.to_json() for item in self.source_resolutions],
            "audit": self.audit.to_json(),
        }


# ---------- Pack 审计（typed，供结构化 Pack） ----------


@dataclass(frozen=True)
class ZeroRefOwner:
    owner_kind: str
    owner_id: str

    def to_json(self) -> dict[str, str]:
        return {"owner_kind": self.owner_kind, "owner_id": self.owner_id}


@dataclass(frozen=True)
class UnresolvedRef:
    owner_kind: str
    owner_id: str
    fragment_id: str
    mismatch: str | None

    def to_json(self) -> dict[str, Any]:
        return _jsonable_dict(self)


@dataclass(frozen=True)
class SourcesAudit:
    owners_checked: int
    owners_resolvable: int
    owners_with_zero_refs: tuple[ZeroRefOwner, ...]
    refs_total: int
    refs_resolved: int
    refs_unresolved: tuple[UnresolvedRef, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "owners_checked": self.owners_checked,
            "owners_resolvable": self.owners_resolvable,
            "owners_with_zero_refs": [item.to_json() for item in self.owners_with_zero_refs],
            "refs_total": self.refs_total,
            "refs_resolved": self.refs_resolved,
            "refs_unresolved": [item.to_json() for item in self.refs_unresolved],
        }


@dataclass(frozen=True)
class PackAudit:
    retrieval: RetrievalAudit
    sources: SourcesAudit

    def to_json(self) -> dict[str, Any]:
        return {**self.retrieval.to_json(), "sources": self.sources.to_json()}
