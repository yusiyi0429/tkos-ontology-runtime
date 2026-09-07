"""graph query ``strategic_path_v1`` 只读仓储层：全部 SQL 集中于此。

每个函数都在调用方事务内的 cursor 上执行（borrowed connection，同一只读
一致性事务贯穿 current generation 解析、图遍历与来源真实解析）。本模块：

- 不开启/提交事务，不管理连接生命周期；调用方必须传入 dict_row 游标；
- 只读 ``status='current'`` generation 与 ``status='confirmed'`` 图内容；
- 返回类型化契约对象（memory_service.context_graph.query_contracts），不返回裸 dict；
- 来源“可解析性”校验冻结 excerpt 与 SHA-256 快照一致；排序候选 hint 与
  OwnerResolution.resolvable 严格语义一致，定位信息来自写入时冻结的 snapshot。
- 不可变对象在本地累积后一次性构造，绝不在 frozen 对象上原地 append。
"""
from __future__ import annotations

import hashlib
from typing import Any

from pgvector import HalfVector

from memory_service.context_graph.query_contracts import (
    ClimbStep,
    EntityPayload,
    FragmentLocator,
    GenerationRef,
    HitCandidate,
    LateralCandidate,
    OwnerResolution,
    RelationPayload,
    ResolvedSourceRef,
    NoCurrentGenerationError,
)

_ENTITY_REF_SCOPE = (
    "er.graph_generation_id = %(generation_id)s AND "
    "er.tenant_id = %(tenant_id)s AND er.organization_id = %(organization_id)s"
)
_RELATION_REF_SCOPE = (
    "rr.graph_generation_id = %(generation_id)s AND "
    "rr.tenant_id = %(tenant_id)s AND rr.organization_id = %(organization_id)s"
)

_ENTITY_REF_GOOD_EXISTS = f"""
    EXISTS (SELECT 1 FROM semantic_entity_source_refs er
            WHERE er.entity_id = {{expr}} AND {_ENTITY_REF_SCOPE}
              AND er.excerpt_snapshot <> ''
              AND encode(sha256(convert_to(er.excerpt_snapshot, 'UTF8')), 'hex') = er.content_hash_snapshot)
"""
_ENTITY_REF_BAD_EXISTS = f"""
    EXISTS (SELECT 1 FROM semantic_entity_source_refs er
            WHERE er.entity_id = {{expr}} AND {_ENTITY_REF_SCOPE}
              AND (er.excerpt_snapshot = ''
                   OR encode(sha256(convert_to(er.excerpt_snapshot, 'UTF8')), 'hex') <> er.content_hash_snapshot))
"""
_RELATION_REF_GOOD_EXISTS = f"""
    EXISTS (SELECT 1 FROM semantic_relation_source_refs rr
            WHERE rr.relation_id = {{expr}} AND {_RELATION_REF_SCOPE}
              AND rr.excerpt_snapshot <> ''
              AND encode(sha256(convert_to(rr.excerpt_snapshot, 'UTF8')), 'hex') = rr.content_hash_snapshot)
"""
_RELATION_REF_BAD_EXISTS = f"""
    EXISTS (SELECT 1 FROM semantic_relation_source_refs rr
            WHERE rr.relation_id = {{expr}} AND {_RELATION_REF_SCOPE}
              AND (rr.excerpt_snapshot = ''
                   OR encode(sha256(convert_to(rr.excerpt_snapshot, 'UTF8')), 'hex') <> rr.content_hash_snapshot))
"""


def _entity_refs_resolvable(expr: str) -> str:
    """严格可解析 hint：至少一条全匹配 ref 且 无任何坏 ref。

    ``expr`` 是引用关系行端点/关系 ID 的 SQL 表达式（如 ``r.source_id``），
    由内部常量渲染，不接受外部字符串。
    """
    return (f"({_ENTITY_REF_GOOD_EXISTS.format(expr=expr)} "
            f"AND NOT ({_ENTITY_REF_BAD_EXISTS.format(expr=expr)}))")


def _relation_refs_resolvable(expr: str) -> str:
    return (f"({_RELATION_REF_GOOD_EXISTS.format(expr=expr)} "
            f"AND NOT ({_RELATION_REF_BAD_EXISTS.format(expr=expr)}))")


def resolve_current_generation(
    cur, *, tenant_id: str, organization_id: str,
) -> GenerationRef:
    """解析 scope 下唯一 status='current' 的 generation。

    检索路径运行在 REPEATABLE READ 只读事务内：首次读取即固定 MVCC 快照，
    并发受控硬切换不会影响本事务内的视图，无需行锁（只读事务也禁止 FOR SHARE）。
    """
    cur.execute(
        """SELECT generation_id, label, status FROM context_graph_versions
             WHERE tenant_id=%(tenant_id)s AND organization_id=%(organization_id)s
               AND status='current'""",
        {"tenant_id": tenant_id, "organization_id": organization_id},
    )
    row = cur.fetchone()
    if row is None:
        raise NoCurrentGenerationError(
            f"no current Context Graph generation for tenant={tenant_id} org={organization_id}"
        )
    return GenerationRef(
        generation_id=str(row["generation_id"]),
        label=str(row["label"]),
        status=str(row["status"]),
    )


def hit_candidates(
    cur, *, tenant_id: str, organization_id: str, generation: GenerationRef,
    query_vector: tuple[float, ...], hit_limit: int,
) -> tuple[HitCandidate, ...]:
    """向量命中候选叶子：confirmed、当前代、带 embedding、主导航上无 confirmed 子节点。

    排序按 pgvector HNSW 距离操作符直接升序（``ORDER BY e.embedding <=> q ASC``），
    相似度只作投影计算。公司愿景根是所有路径的终点而非命中材料，结构性排除；
    叶子判定只看 primary_alignment 主导航投影。
    """
    vector = HalfVector(list(query_vector))
    cur.execute(
        """SELECT e.entity_id, e.type_key, e.name,
                  (e.embedding <=> %(q)s) AS distance,
                  1 - (e.embedding <=> %(q)s) AS similarity
             FROM semantic_entities e
            WHERE e.tenant_id=%(tenant_id)s AND e.organization_id=%(organization_id)s
              AND e.graph_generation_id=%(generation_id)s
              AND e.status='confirmed'
              AND e.type_key IS NOT NULL
              AND e.type_key <> 'CompanyVision'
              AND e.embedding IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1 FROM semantic_relations c
                     WHERE c.graph_generation_id=%(generation_id)s
                       AND c.tenant_id=%(tenant_id)s AND c.organization_id=%(organization_id)s
                       AND c.target_id=e.entity_id
                       AND c.relation_type='primary_alignment'
                       AND c.status='confirmed')
            ORDER BY e.embedding <=> %(q)s ASC, e.entity_id ASC
            LIMIT %(hit_limit)s""",
        {"q": vector, "tenant_id": tenant_id, "organization_id": organization_id,
         "generation_id": generation.generation_id, "hit_limit": hit_limit},
    )
    return tuple(
        HitCandidate(
            entity_id=str(row["entity_id"]),
            type_key=str(row["type_key"]),
            name=str(row["name"]),
            distance=float(row["distance"]),
            similarity=float(row["similarity"]),
            rank=rank,
        )
        for rank, row in enumerate(cur.fetchall())
    )


def climb_paths(
    cur, *, tenant_id: str, organization_id: str, generation: GenerationRef,
    seed_entity_ids: list[str], max_climb_depth: int,
) -> dict[str, tuple[ClimbStep, ...]]:
    """沿 confirmed primary_alignment 从每个种子上溯；每深度至多一行（唯一父节点不变量）。

    递归成员同时 join 父实体 confirmed + 同代同 scope，链在退役/越权节点处自然截断；
    截断的种子随后因未到达公司愿景被丢弃并记入审计。种子行 relation_id 为 None。
    """
    cur.execute(
        """WITH RECURSIVE climb(seed_id, entity_id, depth, node_path, relation_id) AS (
               SELECT s.id, s.id, 0, ARRAY[s.id], NULL::uuid
                 FROM unnest(%(seed_ids)s::uuid[]) AS s(id)
               UNION ALL
               SELECT cl.seed_id, r.target_id, cl.depth + 1,
                      cl.node_path || r.target_id, r.relation_id
                 FROM climb cl
                 JOIN semantic_relations r
                   ON r.source_id = cl.entity_id
                  AND r.relation_type = 'primary_alignment'
                  AND r.status = 'confirmed'
                  AND r.graph_generation_id = %(generation_id)s
                  AND r.tenant_id = %(tenant_id)s AND r.organization_id = %(organization_id)s
                 JOIN semantic_entities parent
                   ON parent.entity_id = r.target_id
                  AND parent.status = 'confirmed'
                  AND parent.graph_generation_id = %(generation_id)s
                  AND parent.tenant_id = %(tenant_id)s AND parent.organization_id = %(organization_id)s
                WHERE cl.depth < %(max_depth)s
                  AND NOT r.target_id = ANY(cl.node_path)
           )
           SELECT cl.seed_id, cl.entity_id, cl.depth, cl.relation_id
             FROM climb cl
            ORDER BY cl.seed_id, cl.depth, cl.relation_id""",
        {"seed_ids": seed_entity_ids, "generation_id": generation.generation_id,
         "tenant_id": tenant_id, "organization_id": organization_id,
         "max_depth": max_climb_depth},
    )
    chains: dict[str, list[ClimbStep]] = {}
    for row in cur.fetchall():
        chains.setdefault(str(row["seed_id"]), []).append(ClimbStep(
            entity_id=str(row["entity_id"]),
            depth=int(row["depth"]),
            relation_id=str(row["relation_id"]) if row["relation_id"] is not None else None,
        ))
    return {seed: tuple(steps) for seed, steps in chains.items()}


def _scope_filter() -> str:
    return ("tenant_id=%(tenant_id)s AND organization_id=%(organization_id)s "
            "AND graph_generation_id=%(generation_id)s")


def entity_payloads(
    cur, *, tenant_id: str, organization_id: str, generation: GenerationRef,
    entity_ids: list[str],
) -> dict[str, EntityPayload]:
    if not entity_ids:
        return {}
    cur.execute(
        f"""SELECT entity_id, type_key, name, content, rationale, revision, status,
                   confirmed_by, confirmed_at
              FROM semantic_entities
             WHERE entity_id = ANY(%(ids)s) AND {_scope_filter()} AND status='confirmed'
             ORDER BY entity_id""",
        {"ids": entity_ids, "tenant_id": tenant_id, "organization_id": organization_id,
         "generation_id": generation.generation_id},
    )
    return {
        str(row["entity_id"]): EntityPayload(
            entity_id=str(row["entity_id"]),
            type_key=str(row["type_key"]),
            name=str(row["name"]),
            content=row["content"] or {},
            rationale=row["rationale"],
            revision=int(row["revision"]),
            status=str(row["status"]),
            confirmed_by=str(row["confirmed_by"]) if row["confirmed_by"] is not None else None,
            confirmed_at=row["confirmed_at"],
        )
        for row in cur.fetchall()
    }


def relation_payloads(
    cur, *, tenant_id: str, organization_id: str, generation: GenerationRef,
    relation_ids: list[str],
) -> dict[str, RelationPayload]:
    if not relation_ids:
        return {}
    cur.execute(
        f"""SELECT relation_id, relation_type, source_id, target_id, content, rationale,
                   revision, status
              FROM semantic_relations
             WHERE relation_id = ANY(%(ids)s) AND {_scope_filter()} AND status='confirmed'
             ORDER BY relation_id""",
        {"ids": relation_ids, "tenant_id": tenant_id, "organization_id": organization_id,
         "generation_id": generation.generation_id},
    )
    return {
        str(row["relation_id"]): RelationPayload(
            relation_id=str(row["relation_id"]),
            relation_type=str(row["relation_type"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            content=row["content"] or {},
            rationale=row["rationale"],
            revision=int(row["revision"]),
            status=str(row["status"]),
        )
        for row in cur.fetchall()
    }


def lateral_candidates(
    cur, *, tenant_id: str, organization_id: str, generation: GenerationRef,
    query_vector: tuple[float, ...], path_node_ids: list[str],
) -> tuple[LateralCandidate, ...]:
    """横向业务关系候选：confirmed 非 primary 关系，恰一端在主路径上。

    每行携带横向端点相似度与严格来源可解析 hint（entity 与 relation 两侧都要求
    “至少一条全匹配 ref 且无坏 ref”）。不含任何业务关系权重。
    """
    vector = HalfVector(list(query_vector))
    params: dict[str, Any] = {
        "q": vector, "tenant_id": tenant_id, "organization_id": organization_id,
        "generation_id": generation.generation_id, "path_ids": path_node_ids,
    }
    cur.execute(
        f"""SELECT r.relation_id, r.relation_type, r.source_id, r.target_id,
                  CASE WHEN se.embedding IS NULL THEN NULL
                       ELSE 1 - (se.embedding <=> %(q)s) END AS source_similarity,
                  CASE WHEN te.embedding IS NULL THEN NULL
                       ELSE 1 - (te.embedding <=> %(q)s) END AS target_similarity,
                  {_entity_refs_resolvable("r.source_id")} AS source_refs_resolvable,
                  {_entity_refs_resolvable("r.target_id")} AS target_refs_resolvable,
                  {_relation_refs_resolvable("r.relation_id")} AS relation_refs_resolvable
             FROM semantic_relations r
             JOIN semantic_entities se
               ON se.entity_id = r.source_id AND se.status='confirmed'
              AND se.graph_generation_id=%(generation_id)s
              AND se.tenant_id=%(tenant_id)s AND se.organization_id=%(organization_id)s
              AND se.type_key IS NOT NULL
             JOIN semantic_entities te
               ON te.entity_id = r.target_id AND te.status='confirmed'
              AND te.graph_generation_id=%(generation_id)s
              AND te.tenant_id=%(tenant_id)s AND te.organization_id=%(organization_id)s
              AND te.type_key IS NOT NULL
            WHERE r.tenant_id=%(tenant_id)s AND r.organization_id=%(organization_id)s
              AND r.graph_generation_id=%(generation_id)s
              AND r.status='confirmed'
              AND r.relation_type <> 'primary_alignment'
              AND (r.source_id = ANY(%(path_ids)s) OR r.target_id = ANY(%(path_ids)s))""",
        params,
    )
    rows: list[LateralCandidate] = []
    for row in cur.fetchall():
        rows.append(LateralCandidate(
            relation_id=str(row["relation_id"]),
            relation_type=str(row["relation_type"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            source_similarity=(float(row["source_similarity"])
                               if row["source_similarity"] is not None else None),
            target_similarity=(float(row["target_similarity"])
                               if row["target_similarity"] is not None else None),
            source_refs_resolvable=bool(row["source_refs_resolvable"]),
            target_refs_resolvable=bool(row["target_refs_resolvable"]),
            relation_refs_resolvable=bool(row["relation_refs_resolvable"]),
        ))
    return tuple(rows)


def _ref_row_to_resolved(row: dict[str, Any], *, owner_kind: str) -> ResolvedSourceRef:
    """Build a resolved source from service-owned frozen snapshots."""
    locator_data = row.get("source_locator_snapshot") or {}
    document_id = locator_data.get("document_id")
    actual_hash = hashlib.sha256(row["excerpt_snapshot"].encode("utf-8")).hexdigest()
    mismatch = None if actual_hash == row["content_hash_snapshot"] else "source_snapshot_hash_mismatch"
    return ResolvedSourceRef(
        owner_kind=owner_kind,
        owner_id=str(row["owner_id"]),
        fragment_id=str(row["fragment_id"]),
        ordinal=int(row["ordinal"]),
        document_id=str(document_id) if document_id is not None else None,
        filename=locator_data.get("filename"),
        locator=FragmentLocator(
            heading_path=locator_data.get("heading_path"),
            page_no=locator_data.get("page_no"),
            paragraph_pos=locator_data.get("paragraph_pos"),
        ),
        excerpt=row["excerpt_snapshot"],
        content_hash=row["content_hash_snapshot"],
        resolved=mismatch is None,
        mismatch=mismatch,
    )


def resolve_owner_refs(
    cur, *, tenant_id: str, organization_id: str, generation: GenerationRef,
    entity_owner_ids: list[str], relation_owner_ids: list[str],
) -> tuple[OwnerResolution, ...]:
    """对全部被选 owner 做权威来源解析：零-ref owner 显式在列并标记不可解析。

    excerpt/hash 快照失配按 mismatch 逐条记录。请求列表中的 owner 若无任何 ref 行，返回空 refs 的
    OwnerResolution（mismatch='no_source_refs'）。refs 在本地累积后一次性构造
    tuple，不在 frozen 对象上原地 append。
    """
    requested = [("entity", entity_id) for entity_id in entity_owner_ids] + [
        ("relation", relation_id) for relation_id in relation_owner_ids
    ]
    refs_by_owner: dict[tuple[str, str], list[ResolvedSourceRef]] = {}
    if entity_owner_ids:
        cur.execute(
            """SELECT er.entity_id AS owner_id, er.fragment_id, er.ordinal,
                      er.excerpt_snapshot, er.content_hash_snapshot, er.source_locator_snapshot
                 FROM semantic_entity_source_refs er
                WHERE er.graph_generation_id = %(generation_id)s
                  AND er.tenant_id = %(tenant_id)s AND er.organization_id = %(organization_id)s
                  AND er.entity_id = ANY(%(ids)s)
                ORDER BY er.entity_id, er.ordinal, er.fragment_id""",
            {"ids": entity_owner_ids, "generation_id": generation.generation_id,
             "tenant_id": tenant_id, "organization_id": organization_id},
        )
        for row in cur.fetchall():
            entry = _ref_row_to_resolved(row, owner_kind="entity")
            refs_by_owner.setdefault(("entity", entry.owner_id), []).append(entry)
    if relation_owner_ids:
        cur.execute(
            """SELECT rr.relation_id AS owner_id, rr.fragment_id, rr.ordinal,
                      rr.excerpt_snapshot, rr.content_hash_snapshot, rr.source_locator_snapshot
                 FROM semantic_relation_source_refs rr
                WHERE rr.graph_generation_id = %(generation_id)s
                  AND rr.tenant_id = %(tenant_id)s AND rr.organization_id = %(organization_id)s
                  AND rr.relation_id = ANY(%(ids)s)
                ORDER BY rr.relation_id, rr.ordinal, rr.fragment_id""",
            {"ids": relation_owner_ids, "generation_id": generation.generation_id,
             "tenant_id": tenant_id, "organization_id": organization_id},
        )
        for row in cur.fetchall():
            entry = _ref_row_to_resolved(row, owner_kind="relation")
            refs_by_owner.setdefault(("relation", entry.owner_id), []).append(entry)

    resolutions: list[OwnerResolution] = []
    seen: set[tuple[str, str]] = set()
    for kind, owner_id in requested:
        if (kind, owner_id) in seen:
            continue
        seen.add((kind, owner_id))
        resolutions.append(OwnerResolution(
            owner_kind=kind,
            owner_id=owner_id,
            refs=tuple(refs_by_owner.get((kind, owner_id), [])),
        ))
    return tuple(resolutions)
