"""Build typed Context Packs and render narrative model context.

``build_context_pack`` is a pure conversion from one consistent ``GraphRetrieval`` snapshot and
rejects missing owner resolutions.  ``render_narrative`` reads only typed business payloads and
frozen source anchors; similarity, rank, and budget metadata cannot enter the rendered path.
``strategic_context`` performs embedding outside the transaction, one consistent read, pack
assembly, and rendering.  Contracts, SQL, and query orchestration remain in their dedicated modules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from memory_service.context_graph.query import (
    Embedder, RetrievalBudgets, execute_query, plan_query,
)
from memory_service.context_graph.query_contracts import (
    STRATEGIC_PATH_V1,
    Compressor,
    EntityPayload,
    GenerationRef,
    GraphRetrieval,
    HitCandidate,
    OwnerResolution,
    PackAudit,
    QueryError,
    RelationPayload,
    SourcesAudit,
    UnresolvedRef,
    ZeroRefOwner,
    jsonable,
)
from memory_service.context_graph.types import description_keys, type_label_zh

PACK_VERSION = 2

# 业务文本箭头在叙事展示层的确定性归一（多字符先替换）。仅影响叙事投影，
# 结构化 Pack 保留原文；不扫描、不拒绝、不抛错。
_ARROW_DISPLAY_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("->", "到"), ("=>", "到"),
    ("→", "到"), ("⇒", "到"), ("➜", "到"), ("➞", "到"),
    ("←", "到"), ("⇐", "到"),
    ("↔", "与"),
)

# 业务关系到中文连接词/短式的固定映射（自然中文表达；方向均为 source → target）。
_RELATION_PHRASES: dict[str, str] = {
    "supports": "{source}支撑{target}",
    "depends_on": "{source}依赖{target}",
    "influences": "{source}影响{target}",
    "conflicts_with": "{source}与{target}存在冲突",
    "responsible_for": "{source}由{target}负责",
    "sub_strategy_of": "{source}是{target}的分战略",
    "domain_outcome_of": "{source}分解自{target}",
}


def _display_text(value: str) -> str:
    """叙事展示层的确定性箭头归一；业务原值不动。"""
    text = value
    for arrow, replacement in _ARROW_DISPLAY_REPLACEMENTS:
        if arrow in text:
            text = text.replace(arrow, replacement)
    return text


# ----------  结构化 Pack（typed） ----------


@dataclass(frozen=True)
class PackNode:
    entity: EntityPayload
    resolution: OwnerResolution
    depth_from_seed: int


@dataclass(frozen=True)
class PackEdge:
    relation: RelationPayload
    resolution: OwnerResolution


@dataclass(frozen=True)
class PackPath:
    seed_entity_id: str
    seed_rank: int
    nodes: tuple[PackNode, ...]  # 命中节点 → … → CompanyVision
    edges: tuple[PackEdge, ...]  # 长度 = len(nodes) - 1，子 → 父

    def to_json(self) -> dict[str, Any]:
        return {
            "seed_entity_id": self.seed_entity_id,
            "seed_rank": self.seed_rank,
            "nodes": [node.entity.to_json() | {
                "depth_from_seed": node.depth_from_seed,
                "source_resolution": node.resolution.to_json(),
            } for node in self.nodes],
            "relations": [edge.relation.to_json() | {
                "source_resolution": edge.resolution.to_json(),
            } for edge in self.edges],
        }


@dataclass(frozen=True)
class PackLateral:
    entity: EntityPayload
    relation: RelationPayload
    entity_resolution: OwnerResolution
    relation_resolution: OwnerResolution
    attached_to_entity_id: str
    similarity: float | None  # 审计字段：渲染路径不读取
    rank: int  # 审计字段：渲染路径不读取

    def to_json(self) -> dict[str, Any]:
        return self.entity.to_json() | {
            "similarity": self.similarity,
            "rank": self.rank,
            "attached_to_entity_id": self.attached_to_entity_id,
            "relation": self.relation.to_json() | {
                "source_resolution": self.relation_resolution.to_json(),
            },
            "source_resolution": self.entity_resolution.to_json(),
        }


@dataclass(frozen=True)
class NarrativeContextPack:
    """ 结构化 Context Pack（typed；序列化只在 to_json 边界发生）。"""

    pack_version: int
    retrieval_mode: str
    tenant_id: str
    organization_id: str
    generation: GenerationRef
    effective_query: str
    hit_nodes: tuple[HitCandidate, ...]
    main_paths: tuple[PackPath, ...]
    lateral_nodes: tuple[PackLateral, ...]
    source_resolutions: tuple[OwnerResolution, ...]
    audit: PackAudit

    def to_json(self) -> dict[str, Any]:
        return jsonable({
            "pack_version": self.pack_version,
            "retrieval_mode": self.retrieval_mode,
            "tenant_id": self.tenant_id,
            "organization_id": self.organization_id,
            "generation": self.generation.to_json(),
            "effective_query": self.effective_query,
            "hit_nodes": [hit.to_json() for hit in self.hit_nodes],
            "main_paths": [path.to_json() for path in self.main_paths],
            "lateral_nodes": [node.to_json() for node in self.lateral_nodes],
            "sources": [item.to_json() for item in self.source_resolutions],
            "audit": self.audit.to_json(),
        })


def build_context_pack(retrieval: GraphRetrieval) -> NarrativeContextPack:
    """Build a typed Pack from one GraphRetrieval snapshot without database access.

    每个被选 owner 的 resolution 必须已在 retrieval.source_resolutions 中；
    缺失即拒绝畸形 retrieval（fail closed，不静默造空 refs）。
    """
    resolutions = {(item.owner_kind, item.owner_id): item
                   for item in retrieval.source_resolutions}

    def _resolution(owner_kind: str, owner_id: str) -> OwnerResolution:
        resolution = resolutions.get((owner_kind, owner_id))
        if resolution is None:
            raise QueryError(
                f"malformed retrieval: missing resolution for {owner_kind}:{owner_id}"
            )
        return resolution

    main_paths = tuple(
        PackPath(
            seed_entity_id=path.seed_entity_id,
            seed_rank=path.seed_rank,
            nodes=tuple(PackNode(entity=node, resolution=_resolution("entity", node.entity_id),
                                 depth_from_seed=depth)
                        for depth, node in enumerate(path.nodes)),
            edges=tuple(PackEdge(relation=relation,
                                 resolution=_resolution("relation", relation.relation_id))
                        for relation in path.relations),
        )
        for path in retrieval.main_paths
    )
    lateral_nodes = tuple(
        PackLateral(
            entity=node.entity,
            relation=node.relation,
            entity_resolution=_resolution("entity", node.entity.entity_id),
            relation_resolution=_resolution("relation", node.relation.relation_id),
            attached_to_entity_id=node.attached_to_entity_id,
            similarity=node.similarity,
            rank=node.rank,
        )
        for node in retrieval.lateral_nodes
    )
    sources_audit = SourcesAudit(
        owners_checked=len(retrieval.source_resolutions),
        owners_resolvable=sum(1 for item in retrieval.source_resolutions if item.resolvable),
        owners_with_zero_refs=tuple(
            ZeroRefOwner(owner_kind=item.owner_kind, owner_id=item.owner_id)
            for item in retrieval.source_resolutions if not item.refs
        ),
        refs_total=sum(len(item.refs) for item in retrieval.source_resolutions),
        refs_resolved=sum(
            1 for item in retrieval.source_resolutions for ref in item.refs if ref.resolved
        ),
        refs_unresolved=tuple(
            UnresolvedRef(owner_kind=item.owner_kind, owner_id=item.owner_id,
                          fragment_id=ref.fragment_id, mismatch=ref.mismatch)
            for item in retrieval.source_resolutions for ref in item.refs if not ref.resolved
        ),
    )
    return NarrativeContextPack(
        pack_version=PACK_VERSION,
        retrieval_mode=retrieval.retrieval_mode,
        tenant_id=retrieval.tenant_id,
        organization_id=retrieval.organization_id,
        generation=retrieval.generation,
        effective_query=retrieval.effective_query,
        hit_nodes=retrieval.hit_nodes,
        main_paths=main_paths,
        lateral_nodes=lateral_nodes,
        source_resolutions=retrieval.source_resolutions,
        audit=PackAudit(retrieval=retrieval.audit, sources=sources_audit),
    )


# ----------  叙事渲染 ----------


def _describe(entity: EntityPayload) -> str:
    """按类型契约字段顺序把 content 业务值拼为中文描述（确定性）。"""
    parts: list[str] = []
    for key in description_keys(entity.type_key):
        value = entity.content.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif value is not None and not isinstance(value, (dict, list, bool)):
            parts.append(str(value))
    return "；".join(parts)


def _status_note(status: str | None) -> str:
    if status == "confirmed":
        return "已确认"
    return f"确认状态：{status}" if status else "确认状态未知"


def _edge_note(edge: PackEdge | None) -> list[str]:
    """关系（主路径 primary_alignment / 横向业务边）的确认/来源/理由补充。"""
    if edge is None:
        return []
    notes: list[str] = []
    if edge.relation.status == "confirmed":
        notes.append("关系已确认")
    if isinstance(edge.relation.rationale, str) and edge.relation.rationale.strip():
        notes.append(f"关系理由：{_display_text(edge.relation.rationale.strip())}")
    return notes


def _node_clause_parts(node: PackNode, edge: PackEdge | None, *, position: str) -> tuple[str, str]:
    """拆开主路径节点子句：(core, extras)，拼回就是完整子句。

    core 是实体名称 + 业务内容描述——战略内容本身。extras 是 rationale（为什么/
    如何确认的治理溯源叙事）+ 确认状态/关系尾巴——不是战略内容本身，可以随其余
    推导链一起压缩。这个拆分只在 ``root_statement``/``_narrative_without_root`` 里被
    消费；``_node_clause`` 本身仍返回完整子句，行为与拆分前完全一致。
    """
    entity = node.entity
    label = type_label_zh(entity.type_key)
    name = _display_text(entity.name)
    description = _describe(entity)
    if position == "root":
        head = f"公司经营愿景是「{name}」"
    elif position == "hit":
        head = f"与当前问题直接相关的是「{name}」（{label}）"
    else:
        head = f"在此之下是「{name}」（{label}）"
    if description:
        head += f"：{_display_text(description)}"
    core = head
    extras = ""
    if isinstance(entity.rationale, str) and entity.rationale.strip():
        extras += f"，其理由是{_display_text(entity.rationale.strip())}"
    tail = _status_note(entity.status)
    notes = _edge_note(edge)
    if notes:
        tail += "；" + "；".join(notes)
    extras += f"（{tail}）"
    return core, extras


def _node_clause(node: PackNode, edge: PackEdge | None, *, position: str) -> str:
    """主路径节点子句。position 为 root / inner / hit；edge 为进入本节点的主边。"""
    core, extras = _node_clause_parts(node, edge, position=position)
    return core + extras


def _relation_phrase(relation: RelationPayload, names: dict[str, str]) -> str:
    template = _RELATION_PHRASES.get(relation.relation_type)
    source = _display_text(names.get(relation.source_id) or "主路径中的相关节点")
    target = _display_text(names.get(relation.target_id) or "主路径中的相关节点")
    if template is None:
        return f"「{source}」与「{target}」在业务上相互关联"
    return template.format(source=f"「{source}」", target=f"「{target}」")


def _common_root_prefix(sequences: list[list[PackNode]]) -> list[PackNode]:
    """多条 root→seed 节点序列的最长公共前缀（按 entity_id 比较）。"""
    if len(sequences) < 2:
        return []
    common: list[PackNode] = []
    for node in sequences[0]:
        if all(len(seq) > len(common)
               and seq[len(common)].entity.entity_id == node.entity.entity_id
               for seq in sequences):
            common.append(node)
        else:
            break
    return common


_NO_CONTEXT_TEXT = "当前问题没有匹配到已确认的公司经营背景。"
_NARRATIVE_INTRO = "以下是与当前问题相关的公司经营背景，从公司愿景出发，沿主导航路径说明到与问题直接相关的节点。"


def render_narrative(pack: NarrativeContextPack) -> str:
    """把结构化 Pack 渲染为  单块自然中文叙事。

    单块无换行；公司愿景 → 命中节点顺序；多路径共享主干只陈述一次；横向节点
    作为与主路径相关的补充；主路径关系与横向关系都渲染确认状态与有值 rationale。
    不渲染来源锚点（文件名/页码/标题路径）——那是给审计看的，不是给模型看的：
    结构化 Pack（``to_json()``）完整保留 ``source_resolutions``，审计链路不受影响；
    模型侧不具备按锚点回查原文的能力，逐节点重复的文件名对当次问题没有边际信息量，
    在根节点/近根节点尤其明显（愿景等根节点几乎不变，每次检索都要重复一遍来源）。
    渲染器不读取 hit_nodes/lateral 的 similarity 与 rank、审计段——内部元数据靠结构不投影。
    """
    paths = pack.main_paths
    laterals = pack.lateral_nodes
    if not paths:
        return _NO_CONTEXT_TEXT

    names: dict[str, str] = {}
    for path in paths:
        for node in path.nodes:
            names[node.entity.entity_id] = node.entity.name
    for node in laterals:
        names.setdefault(node.entity.entity_id, node.entity.name)

    sentences: list[str] = [_NARRATIVE_INTRO]
    # PackPath.nodes 是种子 → 愿景（repository 顺序）；渲染前反转为愿景 → 种子。
    sequences = [list(reversed(path.nodes)) for path in paths]
    reversed_edges = [list(reversed(path.edges)) for path in paths]
    common = _common_root_prefix(sequences)
    common_len = len(common)

    def _clauses(seq: list[PackNode], positions: list[str], edges: list[PackEdge | None]) -> list[str]:
        """按 seq（愿景→种子片段）渲染节点子句；edges[i] 为进入 seq[i] 的主边（根为 None）。"""
        return [
            _node_clause(node, edges[index], position=position)
            for index, (node, position) in enumerate(zip(seq, positions, strict=True))
        ]

    if common:
        # 公共主干：首节点是根（无入边），其余为公共节点间的主边。
        positions = ["root"] + ["inner"] * (common_len - 1)
        clauses = _clauses(common, positions,
                           [None] + reversed_edges[0][:common_len - 1])
        sentences.append("。".join(clauses) + "。")

    ordinal_leads = ("其一，", "其二，", "其三，", "其四，", "其五，")
    multiple = len(sequences) > 1
    for index, path in enumerate(paths):
        branch = sequences[index][common_len:]
        if not branch:
            continue  # 防御：完整包含于公共主干的分支不会出现（叶子互不为祖先）
        if len(branch) == 1:
            positions = ["hit"]
        else:
            positions = (["root" if common_len == 0 else "inner"]
                         + ["inner"] * (len(branch) - 2))
            positions.append("hit")
        if common_len == 0:
            # 单路径整条渲染：首节点是根（无入边），其余为主边。
            edges: list[PackEdge | None] = [None] + reversed_edges[index]
        else:
            # 分支：首节点从公共主干承接一条主边。
            edges = reversed_edges[index][common_len - 1:]
        lead = ordinal_leads[index] if multiple and index < len(ordinal_leads) else ""
        clauses = _clauses(branch, positions, edges)
        sentences.append(lead + "。".join(clauses) + "。")

    for node in laterals:
        phrase = _relation_phrase(node.relation, names)
        entity = node.entity
        description = _describe(entity)
        label = type_label_zh(entity.type_key)
        head = f"「{_display_text(entity.name)}」（{label}）"
        if description:
            head += f"：{_display_text(description)}"
        if isinstance(entity.rationale, str) and entity.rationale.strip():
            head += f"，其理由是{_display_text(entity.rationale.strip())}"
        tail = _status_note(entity.status)
        notes = _edge_note(PackEdge(relation=node.relation, resolution=node.relation_resolution))
        if notes:
            tail += "；" + "；".join(notes)
        sentences.append(f"另与主路径相关：{phrase}，其中{head}（{tail}）。")

    return "".join(sentences)


def root_statement(pack: NarrativeContextPack) -> str:
    """公司愿景根节点的核心战略正文（名称 + 业务内容描述），逐字不经模型改写。

    不含 rationale（为什么/如何确认的治理溯源叙事，不是战略内容本身）、也不含确认
    状态尾巴——这两部分随其余推导链一起进压缩，不属于需要逐字保留的战略内容。

    系统里每个 org+tenant 只有一个 active 根节点（需求-阶段2.md §1），所有
    main_path 的最后一个节点都是它——取第一条路径即可，不需要额外遍历。没有命中
    任何路径时返回空串。
    """
    paths = pack.main_paths
    if not paths:
        return ""
    core, _extras = _node_clause_parts(paths[0].nodes[-1], None, position="root")
    return core


def _narrative_without_root(pack: NarrativeContextPack) -> str:
    """``render_narrative()`` 去掉根节点核心正文之后剩下的部分。

    根节点的 rationale/确认状态尾巴保留在这一段里（和其余推导链一样可压缩），只有
    ``root_statement()`` 那段核心正文被切掉。不重新实现拼句逻辑：从
    ``render_narrative()`` 的确定性输出里，按已知的固定结构（导言 + 根节点核心正文 +
    其余）原样切掉核心正文，保证与 ``render_narrative()`` 永远一致。没有根节点时原样
    返回全文（包括空背景占位句）。
    """
    full = render_narrative(pack)
    core = root_statement(pack)
    if not core:
        return full
    prefix = _NARRATIVE_INTRO + core
    if not full.startswith(prefix):
        raise QueryError("叙事结构不符合预期：根节点核心正文未出现在导言之后，拒绝拼接畸形分段")
    return _NARRATIVE_INTRO + full[len(prefix):]


class NarrativeCompressionError(RuntimeError):
    """叙事压缩失败（返回空文本）。

    Fail-closed：不存在"压缩失败退回未压缩全文"的静默降级路径——底层 ``compressor.chat``
    抛出的异常原样向上传播，本函数只额外守住"返回了空文本"这一种静默失败形态。
    """


_COMPRESSION_SYSTEM_PROMPT = (
    "下面这段文字接续公司战略背景，描述从公司愿景往下的推导链路、最终产出与当前问题"
    "相关的节点，内容已全部经过人工确认，可信。请压缩它：中间的推导环节可以省略或简写，"
    "但整体必须讲清楚——为什么会产出这些与当前问题相关的节点，推导的因果/依赖链条"
    "不能压没了、不能变成互不相关的事实罗列。可以省略「已确认」这类确认状态措辞，也可以省略"
    "来源出处这类元信息。不要添加原文没有的内容，不要改变原文的因果方向、数值或结论。"
    "直接输出压缩后的正文，不要加标题、不要加解释。"
)


def compress_narrative(text: str, *, compressor: Compressor) -> str:
    """用宿主注入的 gateway.chat 压缩 render_narrative 的输出。

    没有实质背景内容时（``_NO_CONTEXT_TEXT`` 占位句）跳过调用——既没有可压缩的东西，
    也不该让模型在空背景上凭空生成内容。fail-closed：底层异常原样向上抛；返回空文本
    视为失败同样抛错，不回退未压缩全文。
    """
    if text == _NO_CONTEXT_TEXT:
        return text
    result = compressor.chat(
        [
            {"role": "system", "content": _COMPRESSION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        max_tokens=800,
    )
    compressed = (getattr(result, "content", None) or "").strip()
    if not compressed:
        raise NarrativeCompressionError("叙事压缩返回空文本")
    return compressed


def _join_root_and_rest(root: str, compressed_rest: str) -> str:
    """拼回根节点核心正文与压缩后的剩余部分，避免 root 本身已以句末标点结尾时
    再补一个"。"变成重复句号（业务 statement 字段往往自己就是完整段落，已带结尾句号）。"""
    if not root:
        return compressed_rest
    joiner = "" if root.endswith(("。", "！", "？")) else "。"
    return f"{root}{joiner}{compressed_rest}"


def strategic_context(
    tenant_id: str,
    organization_id: str,
    effective_query: str,
    *,
    retrieval_mode: str = STRATEGIC_PATH_V1,
    embedder: Embedder,
    compressor: Compressor,
    budgets: RetrievalBudgets | None = None,
    embedding_dim: int,
    _connect: Callable[..., Any],
) -> dict[str, Any]:
    """全链路门面：规划（embedding 事务外）→ 单一只读一致性事务执行 → Pack → 叙事 → 压缩。

    ``embedder``/``compressor``/``embedding_dim``/``_connect`` 由宿主显式传入
    （host capability boundary）。根节点（公司愿景）的正文逐字保留、从不送进压缩调用：
    只压缩根节点以下的推导链/命中节点/横向节点，再与未动的根节点文本拼回。压缩在读时现算
    （每次调用一次 chat），fail-closed：压缩失败即整次调用失败，不静默回退未压缩全文。

    返回 {"pack": NarrativeContextPack, "narrative": 单块叙事文本（根节点原文 + 已压缩的推导链），
    "narrative_raw_chars": 压缩前字符数, "narrative_chars": 压缩后字符数}。
    pack 用于审计与调试（to_json()）；narrative 是唯一应注入模型的文本。
    """
    plan = plan_query(
        tenant_id, organization_id, effective_query,
        retrieval_mode=retrieval_mode, embedder=embedder, budgets=budgets,
        embedding_dim=embedding_dim,
    )
    retrieval = execute_query(plan, _connect=_connect)
    pack = build_context_pack(retrieval)
    raw_narrative = render_narrative(pack)
    root = root_statement(pack)
    rest = _narrative_without_root(pack)
    compressed_rest = compress_narrative(rest, compressor=compressor)
    narrative = _join_root_and_rest(root, compressed_rest)
    return {
        "pack": pack,
        "narrative": narrative,
        "narrative_raw_chars": len(raw_narrative),
        "narrative_chars": len(narrative),
    }
