"""graph query ``strategic_path_v1`` 查询规划与编排。

模块边界：
- ``plan_query``：retrieval_mode 校验、预算校验、effective_query 经可注入 embedder
  向量化（含维度/有限/非零校验）。纯内存，不碰数据库（embedding 永远在事务外）。
- ``execute_query``：在同一个 borrowed connection 的只读一致性事务内完成 current
  generation 解析、图遍历、被选 owner 的来源真实解析，返回 ``GraphRetrieval``
  （同快照）。事务生命周期由 ``conn.transaction()`` 完整管理：首句
  ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY``（不触碰会话默认值），
  成功/失败后借用连接均回到 IDLE 且原默认不变。可用 ``conn=`` 借用调用方连接。
- ``retrieve``：规划 + 执行的便捷门面。

分层：契约在 query_contracts，SQL 在 query_repository，Pack/渲染在 context_pack。
本模块不含 SQL 与渲染；核心控制状态一律用 typed 契约对象，不用裸 dict 表达。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from memory_service.context_graph import query_repository as repository
from memory_service.context_graph.query_contracts import (
    RETRIEVAL_MODES,
    STRATEGIC_PATH_V1,
    ClimbStep,
    Embedder,
    EntityPayload,
    GenerationRef,
    GraphRetrieval,
    HitCandidate,
    LateralCandidate,
    LateralNode,
    LateralPrune,
    LateralSelection,
    LateralSelectionStats,
    MainPath,
    OwnerResolution,
    PathDrop,
    QueryError,
    QueryPlan,
    RetrievalAudit,
    RetrievalBudgets,
    RetrievalModeError,
    SeedDrop,
    SourceResolutionError,
    is_legacy_generation,
    validate_query_vector,
)

__all__ = [
    "STRATEGIC_PATH_V1", "RETRIEVAL_MODES", "RetrievalBudgets", "QueryPlan",
    "GraphRetrieval", "QueryError", "RetrievalModeError", "plan_query",
    "execute_query", "retrieve", "Embedder",
]


def plan_query(
    tenant_id: str,
    organization_id: str,
    effective_query: str,
    *,
    retrieval_mode: str = STRATEGIC_PATH_V1,
    embedder: Embedder,
    budgets: RetrievalBudgets | None = None,
    embedding_dim: int,
) -> QueryPlan:
    """校验 retrieval_mode、向量化 effective_query 并固化预算。

    纯内存规划：current generation 的解析推迟到执行阶段的一致性事务内，
    避免规划与执行之间发生受控硬切换后读到退役图。
    """
    if retrieval_mode not in RETRIEVAL_MODES:
        raise RetrievalModeError(
            f"unsupported retrieval_mode: {retrieval_mode!r}; "
            f"valid modes: {list(RETRIEVAL_MODES)}"
        )
    if not isinstance(effective_query, str) or not effective_query.strip():
        raise QueryError("effective_query must be a non-empty string")
    raw_vector = embedder.embed([effective_query])
    if not isinstance(raw_vector, list) or len(raw_vector) != 1:
        raise QueryError("embedder must return exactly one vector for the query")
    query_vector = validate_query_vector(raw_vector[0], embedding_dim=embedding_dim)
    return QueryPlan(
        retrieval_mode=retrieval_mode,
        tenant_id=tenant_id,
        organization_id=organization_id,
        effective_query=effective_query,
        query_vector=query_vector,
        budgets=RetrievalBudgets() if budgets is None else budgets,
    )


def _select_lateral_entries(
    candidates: tuple[LateralCandidate, ...], path_node_ids: list[str], lateral_budget: int,
) -> tuple[tuple[LateralSelection, ...], LateralSelectionStats]:
    """确定性横向选择：全 Pack 总预算，跨路径共享，不按节点重复计数。

    排序键 =（查询相关度降序，来源可解析降序，relation_type 字典序，锚点位置，
    实体 ID，关系 ID）。relation_type 仅作无业务语义的稳定 tie-break，不引入
    supports 优先于 influences 之类的业务权重。确认状态因素在候选生成
    阶段全量完成（非 confirmed 节点/关系不进入候选集）。同一横向节点有多条边可达
    主路径时取排序最优的一条。
    """
    path_index = {entity_id: idx for idx, entity_id in enumerate(path_node_ids)}
    path_set = set(path_index)
    per_node: dict[str, LateralSelection] = {}
    intra_path_edges = 0
    for cand in candidates:
        source_on = cand.source_id in path_set
        target_on = cand.target_id in path_set
        if source_on and target_on:
            intra_path_edges += 1
            continue
        if not source_on and not target_on:
            continue
        lateral_id = cand.target_id if source_on else cand.source_id
        if lateral_id in path_set:
            continue
        anchor_id = cand.source_id if source_on else cand.target_id
        similarity = cand.target_similarity if source_on else cand.source_similarity
        resolvable = bool(
            (cand.target_refs_resolvable if source_on else cand.source_refs_resolvable)
            and cand.relation_refs_resolvable
        )
        candidate = LateralSelection(
            entity_id=lateral_id,
            relation_id=cand.relation_id,
            relation_type=cand.relation_type,
            attached_to_entity_id=anchor_id,
            similarity=similarity,
            refs_resolvable=resolvable,
            anchor_index=path_index[anchor_id],
            rank=-1,
        )
        existing = per_node.get(lateral_id)
        if existing is None or _lateral_sort_key(candidate) < _lateral_sort_key(existing):
            per_node[lateral_id] = candidate

    ranked = sorted(per_node.values(), key=_lateral_sort_key)
    selected_raw = ranked[:lateral_budget]
    selected = tuple(
        replace(entry, rank=rank) for rank, entry in enumerate(selected_raw)
    )
    pruned = tuple(
        LateralPrune(entity_id=entry.entity_id, reason="lateral_budget")
        for entry in ranked[lateral_budget:]
    )
    stats = LateralSelectionStats(
        candidate_nodes=len(per_node),
        intra_path_business_edges_skipped=intra_path_edges,
        selected=len(selected),
        pruned=pruned,
    )
    return selected, stats


def _lateral_sort_key(entry: LateralSelection) -> tuple:
    similarity = entry.similarity
    return (
        -(similarity if similarity is not None else float("-inf")),
        -int(entry.refs_resolvable),
        entry.relation_type,
        entry.anchor_index,
        entry.entity_id,
        entry.relation_id,
    )


def _select_paths(
    chains: dict[str, tuple[ClimbStep, ...]],
    hits: tuple[HitCandidate, ...],
    entities: dict[str, EntityPayload],
    *,
    max_paths: int,
    max_climb_depth: int,
) -> tuple[tuple[tuple[HitCandidate, tuple[ClimbStep, ...]], ...], tuple[SeedDrop, ...], tuple[PathDrop, ...]]:
    """筛选终于 CompanyVision 的主路径（种子 + 上溯行）；多路径按节点集去重并确定性裁剪。

    只做选择，不构造 MainPath（关系载荷在横向选定后统一读取）。
    """
    seeds_dropped: list[SeedDrop] = []
    paths_dropped: list[PathDrop] = []
    built: list[tuple[HitCandidate, tuple[ClimbStep, ...]]] = []
    for hit in hits:  # hits 已按（距离 ASC, entity_id ASC）排序，rank 唯一
        rows = chains.get(hit.entity_id)
        if not rows:
            seeds_dropped.append(SeedDrop(hit.entity_id, "no_chain"))
            continue
        rows = tuple(sorted(rows, key=lambda step: (step.depth, step.relation_id or "")))
        depths = [step.depth for step in rows]
        if len(set(depths)) != len(depths):
            # 唯一父节点不变量被破坏（理论上被部分唯一索引排除）；防御性丢弃。
            seeds_dropped.append(SeedDrop(hit.entity_id, "ambiguous_path"))
            continue
        top = rows[-1]
        top_entity = entities.get(top.entity_id)
        if top_entity is None or top_entity.type_key != "CompanyVision":
            reason = "depth_cap" if top.depth >= max_climb_depth else "broken_chain"
            seeds_dropped.append(SeedDrop(hit.entity_id, reason))
            continue
        if top.depth == 0:
            seeds_dropped.append(SeedDrop(hit.entity_id, "seed_is_root"))
            continue
        built.append((hit, rows))

    selected: list[tuple[HitCandidate, tuple[ClimbStep, ...]]] = []
    for hit, rows in built:
        node_set = {step.entity_id for step in rows}
        subsuming = next(
            (kept for kept in selected
             if node_set <= {step.entity_id for step in kept[1]}),
            None,
        )
        if subsuming is not None:
            paths_dropped.append(PathDrop(hit.entity_id, f"subsumed_by:{subsuming[0].entity_id}"))
            continue
        if len(selected) >= max_paths:
            paths_dropped.append(PathDrop(hit.entity_id, "max_paths"))
            continue
        selected.append((hit, rows))
    return tuple(selected), tuple(seeds_dropped), tuple(paths_dropped)


def _fail_closed_if_unresolvable(
    generation: GenerationRef, resolutions: tuple[OwnerResolution, ...],
) -> None:
    """新代 current 图存在不可解析被选 owner 时 fail closed（legacy 代豁免）。

    严格语义：owner 的 refs 非空 且 每条都真实解析；任何坏 ref 都不可解析。
    """
    if is_legacy_generation(generation.generation_id):
        return
    unresolved = [
        {"owner_kind": item.owner_kind, "owner_id": item.owner_id,
         "mismatch": item.mismatch, "ref_count": len(item.refs)}
        for item in resolutions if not item.resolvable
    ]
    if unresolved:
        raise SourceResolutionError(unresolved)


def _run_read_only_transaction(conn, plan: QueryPlan) -> GraphRetrieval:
    """在借用连接上开启并完整管理一只读一致性事务。

    - 进入前要求连接 IDLE（否则无法把 SET TRANSACTION 作为事务首句）；
    - ``conn.transaction()`` 负责 BEGIN/COMMIT/ROLLBACK，成功或异常后连接都回到
      IDLE；首句 ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY``
      只影响本事务，不改会话默认值。
    """
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise QueryError("borrowed connection must be idle to start a read-only snapshot")
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with conn.cursor(row_factory=dict_row) as cur:
            return _execute_in_transaction(cur, plan)


def execute_query(
    plan: QueryPlan, *, conn=None, _connect: Callable[..., Any] | None = None,
) -> GraphRetrieval:
    """在单一只读一致性事务内执行 strategic_path_v1 图查询。

    - ``conn=None``：用 ``_connect`` 开新连接（门面 strategic_context/retrieve
      走这条路，保证同快照）；
    - ``conn=``：借用调用方连接（须空闲）；提交/回滚由本函数经
      ``conn.transaction()`` 完整管理，调用方只负责最终关闭连接。

    事务步骤：current generation 解析（REPEATABLE READ 快照隔离）→ 向量命中 →
    主导航上溯 → 横向候选 → 载荷读取 → 被选 owner 来源真实解析（新代 fail closed）。
    """
    if conn is not None:
        return _run_read_only_transaction(conn, plan)
    if _connect is None:
        raise QueryError("execute_query requires either conn or _connect (host capability boundary: no default connection)")
    with _connect() as fresh_conn:
        return _run_read_only_transaction(fresh_conn, plan)


def _execute_in_transaction(cur, plan: QueryPlan) -> GraphRetrieval:
    generation = repository.resolve_current_generation(
        cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
    )
    hits = repository.hit_candidates(
        cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
        generation=generation, query_vector=plan.query_vector,
        hit_limit=plan.budgets.hit_limit,
    )

    seeds_dropped: tuple[SeedDrop, ...] = ()
    paths_dropped: tuple[PathDrop, ...] = ()
    lateral_stats = LateralSelectionStats(0, 0, 0, ())
    paths: tuple[MainPath, ...] = ()
    lateral_nodes: tuple[LateralNode, ...] = ()

    if hits:
        chains = repository.climb_paths(
            cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
            generation=generation,
            seed_entity_ids=[hit.entity_id for hit in hits],
            max_climb_depth=plan.budgets.max_climb_depth,
        )
        path_entity_ids = sorted({
            step.entity_id for rows in chains.values() for step in rows
        })
        entities = repository.entity_payloads(
            cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
            generation=generation, entity_ids=path_entity_ids,
        )
        selected, seeds_dropped, paths_dropped = _select_paths(
            chains, hits, entities,
            max_paths=plan.budgets.max_paths,
            max_climb_depth=plan.budgets.max_climb_depth,
        )

        path_node_ids: list[str] = []
        for _hit, rows in selected:
            for step in rows:
                if step.entity_id not in path_node_ids:
                    path_node_ids.append(step.entity_id)

        relation_pool: set[str] = {
            step.relation_id for _hit, rows in selected for step in rows
            if step.relation_id is not None
        }
        if selected:
            candidates = repository.lateral_candidates(
                cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
                generation=generation, query_vector=plan.query_vector,
                path_node_ids=path_node_ids,
            )
            selections, lateral_stats = _select_lateral_entries(
                candidates, path_node_ids, plan.budgets.lateral_budget,
            )
            relation_pool |= {entry.relation_id for entry in selections}
            lateral_entity_ids = sorted({entry.entity_id for entry in selections})
            entities.update(repository.entity_payloads(
                cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
                generation=generation, entity_ids=lateral_entity_ids,
            ))
        else:
            selections = ()

        relations = repository.relation_payloads(
            cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
            generation=generation, relation_ids=sorted(relation_pool),
        )
        paths = tuple(
            MainPath(
                seed_entity_id=hit.entity_id,
                seed_rank=hit.rank,
                nodes=tuple(entities[step.entity_id] for step in rows),  # 种子 → 愿景
                relations=tuple(relations[step.relation_id] for step in rows
                                if step.relation_id is not None),
            )
            for hit, rows in selected
        )
        lateral_nodes = tuple(
            LateralNode(
                entity=entities[entry.entity_id],
                relation=relations[entry.relation_id],
                attached_to_entity_id=entry.attached_to_entity_id,
                similarity=entry.similarity,
                rank=entry.rank,
            )
            for entry in (selections if selected else ())
        )

    # 全部被选 owner 的权威来源解析（同一事务）。
    entity_owner_ids = sorted({
        node.entity_id for path in paths for node in path.nodes
    } | {node.entity.entity_id for node in lateral_nodes})
    relation_owner_ids = sorted({
        relation.relation_id for path in paths for relation in path.relations
    } | {node.relation.relation_id for node in lateral_nodes})
    resolutions = repository.resolve_owner_refs(
        cur, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
        generation=generation,
        entity_owner_ids=entity_owner_ids, relation_owner_ids=relation_owner_ids,
    )
    _fail_closed_if_unresolvable(generation, resolutions)

    audit = RetrievalAudit(
        budgets=plan.budgets,
        generation=generation,
        hit_candidates=len(hits),
        seeds_dropped=seeds_dropped,
        paths_dropped=paths_dropped,
        lateral_candidate_nodes=lateral_stats.candidate_nodes,
        lateral_selected=lateral_stats.selected,
        lateral_pruned=lateral_stats.pruned,
        intra_path_business_edges_skipped=lateral_stats.intra_path_business_edges_skipped,
    )
    return GraphRetrieval(
        retrieval_mode=plan.retrieval_mode,
        tenant_id=plan.tenant_id,
        organization_id=plan.organization_id,
        generation=generation,
        effective_query=plan.effective_query,
        hit_nodes=hits,
        main_paths=paths,
        lateral_nodes=lateral_nodes,
        source_resolutions=resolutions,
        audit=audit,
    )


def retrieve(
    tenant_id: str,
    organization_id: str,
    effective_query: str,
    *,
    retrieval_mode: str = STRATEGIC_PATH_V1,
    embedder: Embedder,
    budgets: RetrievalBudgets | None = None,
    embedding_dim: int,
    _connect: Callable[..., Any],
) -> GraphRetrieval:
    """规划并执行一次 strategic_path_v1 检索（embedding 在事务外）。

    ``embedder``/``embedding_dim``/``_connect`` 均由宿主显式传入（host capability boundary）。
    """
    plan = plan_query(
        tenant_id, organization_id, effective_query,
        retrieval_mode=retrieval_mode, embedder=embedder, budgets=budgets,
        embedding_dim=embedding_dim,
    )
    return execute_query(plan, _connect=_connect)
