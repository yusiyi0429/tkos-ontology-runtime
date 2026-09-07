"""Public transactional write boundary for document distillers.

The single entry point is :func:`file_shadow_plan`: it receives a typed
:class:`~memory_service.contracts.ShadowFilingPlan` and performs generation
resolution, root uniqueness, external-parent validation, import-session
idempotency and pending-proposal registration in one transaction on the
caller-owned connection.  Memory never touches manifests or
``document_fragments``; frozen sources arrive inside the plan.  Proposal
persistence details remain private to Memory Service.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from memory_service.contracts import (
    EntityFilingOutcome,
    EntityFilingPlan,
    FilingPlanResult,
    ShadowFilingPlan,
)
from memory_service.context_graph import service
from memory_service.context_graph.types import (
    ContextGraphValidationError,
    is_typed_context_graph,
    validate_relation_matrix,
)


class MemoryIngestError(RuntimeError):
    """A distillation filing plan is inconsistent with Memory Service state."""


def file_shadow_plan(conn: Any, plan: ShadowFilingPlan) -> FilingPlanResult:
    """在同一 caller-owned conn 事务内消费一份 ShadowFilingPlan。

    步骤（全部使用传入 conn，commit/rollback 由调用方的 with 管理）：
      1. plan 形状自检（fail-closed：非空、实体名唯一、必需边选择器恰一）；
      2. generation 查找/创建（advisory exclusive lock，并发双 file 只产生 1 个）；
      3. 唯一 root（拒绝同 generation 其它 import session 的 CompanyVision 根
         与已 confirmed 的根实体）；
      4. import session 存量映射 + 完整性校验（重复名/action、source 漂移、manifest 缩减）；
      5. 按 plan.entities 顺序逐实体构造 proposed_content：外部父锁行校验、同 manifest
         目标解析 proposal_id；既有提案内容逐字比对（漂移拒绝），新实体 propose 为
         pending。从不 confirm。
    相同 plan 重试幂等：已登记且内容一致的实体原样 skip。
    """
    _validate_plan_shape(plan)
    generation_id = _get_or_create_shadow_generation(
        conn, tenant_id=plan.tenant_id, organization_id=plan.organization_id,
        label=plan.generation_label,
    )
    _assert_single_company_vision(
        conn, generation_id=generation_id, import_session_id=plan.import_session_id,
        tenant_id=plan.tenant_id, organization_id=plan.organization_id,
        candidate_present=any(e.type_key == "CompanyVision" for e in plan.entities),
    )
    existing_by_name = _list_session_entity_proposals(
        conn, generation_id=generation_id, import_session_id=plan.import_session_id,
        manifest_names={e.name for e in plan.entities}, pipeline=plan.pipeline,
        document_id=plan.document_id, file_version=plan.file_version,
    )
    proposal_id_by_name = {name: item["proposal_id"] for name, item in existing_by_name.items()}

    outcomes: list[EntityFilingOutcome] = []
    for entity in plan.entities:
        content = _proposal_content(conn, plan, entity, generation_id, proposal_id_by_name)
        existing = existing_by_name.get(entity.name)
        if existing is not None:
            if existing["proposed_content"] != content:
                raise MemoryIngestError(
                    f"[{entity.name}] 既有提案内容与本次 filing plan 不一致（内容漂移）——"
                    "拒绝静默跳过，请人工核对后再处理"
                )
            skipped = True
        else:
            result = _propose_graph_entity(
                conn, plan=plan, entity=entity, generation_id=generation_id,
                proposed_content=content,
            )
            proposal_id_by_name[entity.name] = result["proposal_id"]
            skipped = False
        outcomes.append(EntityFilingOutcome(
            name=entity.name, type_key=entity.type_key,
            proposal_id=proposal_id_by_name[entity.name], skipped=skipped,
        ))
    return FilingPlanResult(generation_id=generation_id, outcomes=tuple(outcomes))


def _validate_plan_shape(plan: ShadowFilingPlan) -> None:
    """fail-closed 的 plan 形状自检；不依赖 distill 侧已经校验过。"""
    if not isinstance(plan.proposed_by, str) or not plan.proposed_by.strip():
        raise MemoryIngestError("filing plan.proposed_by 必须是已解析的非空 actor")
    if not plan.entities:
        raise MemoryIngestError("filing plan 不能为空（至少一个实体）")
    names = [e.name for e in plan.entities]
    if len(names) != len(set(names)):
        raise MemoryIngestError("filing plan 内实体名重复")
    for entity in plan.entities:
        for rel in entity.required_relations:
            if (rel.target_proposal_id is None) == (rel.target_name is None):
                raise MemoryIngestError(
                    f"[{entity.name}] required_relations 必须恰好提供 target_name 或 target_proposal_id 之一"
                )


def _get_or_create_shadow_generation(
    conn: Any, *, tenant_id: str, organization_id: str, label: str,
) -> str:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"shadow-gen:{tenant_id}:{organization_id}:{label}",),
    )
    rows = conn.execute(
        """SELECT generation_id, status FROM context_graph_versions
           WHERE tenant_id=%s AND organization_id=%s AND label=%s FOR UPDATE""",
        (tenant_id, organization_id, label),
    ).fetchall()
    if len(rows) > 1:
        raise MemoryIngestError(
            f"generation 标签 {label!r} 在同 scope 下存在 {len(rows)} 行（多行必须拒绝）"
        )
    if rows:
        if rows[0][1] != "shadow":
            raise MemoryIngestError(
                f"generation 标签 {label!r} 已存在但 status={rows[0][1]!r}（非 shadow）"
            )
        return str(rows[0][0])
    return service.create_generation(
        tenant_id=tenant_id,
        organization_id=organization_id,
        label=label,
        status="shadow",
        _connect=_borrowed_connection(conn),
    )


def _borrowed_connection(conn: Any):
    @contextmanager
    def connect(autocommit: bool = False):
        yield conn
    return connect


def _validate_external_parent_proposal(
    conn: Any,
    proposal_id: str,
    *,
    relation_type: str,
    source_entity: EntityFilingPlan,
    generation_id: str,
    tenant_id: str,
    organization_id: str,
) -> None:
    row = conn.execute(
        """SELECT tenant_id, organization_id, graph_generation_id, target_kind, action, status,
                  source, proposed_content
           FROM memory_proposals WHERE proposal_id=%s FOR UPDATE""",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise MemoryIngestError(f"外部父提案 {proposal_id} 不存在")
    if str(row[0]) != tenant_id or str(row[1]) != organization_id:
        raise MemoryIngestError(f"外部父提案 {proposal_id} 的 scope 与本次登记不一致")
    if str(row[2]) != generation_id:
        raise MemoryIngestError(f"外部父提案 {proposal_id} 属于不同 generation")
    if row[3] != "entity" or row[4] != "create":
        raise MemoryIngestError(
            f"外部父提案 {proposal_id} 必须是 entity-create（实际 kind={row[3]}/action={row[4]}）"
        )
    if row[5] not in ("pending", "confirmed"):
        raise MemoryIngestError(
            f"外部父提案 {proposal_id} 状态 {row[5]!r} 不可作为父引用（仅 pending/confirmed）"
        )
    source = row[6] or {}
    if not is_typed_context_graph(source):
        raise MemoryIngestError(f"外部父提案 {proposal_id} 缺少 typed Context Graph marker")
    target_content = row[7] or {}
    target = {
        key: target_content.get(key)
        for key in ("type_key", "strategic_level", "strategic_period", "outcome_level")
    }
    source_content = {
        "type_key": source_entity.type_key,
        "strategic_level": source_entity.strategic_level,
        "strategic_period": source_entity.strategic_period,
        "outcome_level": source_entity.outcome_level,
    }
    try:
        validate_relation_matrix(relation_type, source_content, target)
    except ContextGraphValidationError as exc:
        raise MemoryIngestError(
            f"外部父提案 {proposal_id}（type_key={target['type_key']}）不满足 {relation_type} 矩阵：{exc}"
        ) from exc


def _assert_single_company_vision(
    conn: Any,
    *,
    generation_id: str,
    import_session_id: str,
    tenant_id: str,
    organization_id: str,
    candidate_present: bool,
) -> None:
    if not candidate_present:
        return
    other = conn.execute(
        """SELECT 1 FROM memory_proposals
           WHERE tenant_id=%s AND organization_id=%s AND graph_generation_id=%s
             AND target_kind='entity' AND action='create'
             AND status IN ('pending','confirmed')
             AND proposed_content->>'type_key'='CompanyVision'
             AND import_session_id IS DISTINCT FROM %s
           LIMIT 1 FOR UPDATE""",
        (tenant_id, organization_id, generation_id, import_session_id),
    ).fetchone()
    if other is not None:
        raise MemoryIngestError("同 generation 已有其它 import session 的 CompanyVision 根提案")
    confirmed = conn.execute(
        """SELECT 1 FROM semantic_entities e
           WHERE e.tenant_id=%s AND e.organization_id=%s AND e.graph_generation_id=%s
             AND e.type_key='CompanyVision' AND e.status='confirmed'
             AND NOT EXISTS (
                 SELECT 1 FROM memory_proposals own
                  WHERE own.tenant_id=e.tenant_id AND own.organization_id=e.organization_id
                    AND own.graph_generation_id=e.graph_generation_id
                    AND own.target_kind='entity' AND own.action='create'
                    AND own.target_id=e.entity_id AND own.status='confirmed'
                    AND own.import_session_id=%s)
           LIMIT 1 FOR UPDATE""",
        (tenant_id, organization_id, generation_id, import_session_id),
    ).fetchone()
    if confirmed is not None:
        raise MemoryIngestError("同 generation 已有 confirmed CompanyVision 根实体")


def _list_session_entity_proposals(
    conn: Any,
    *,
    generation_id: str,
    import_session_id: str,
    manifest_names: set[str],
    pipeline: str,
    document_id: str,
    file_version: str,
) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """SELECT proposal_id, proposed_content->>'name', status, source, proposed_content, action
           FROM memory_proposals
           WHERE graph_generation_id=%s AND import_session_id=%s AND target_kind='entity' FOR UPDATE""",
        (generation_id, import_session_id),
    ).fetchall()
    by_name: dict[str, list[Any]] = {}
    for row in rows:
        name = row[1]
        if name is None or not name.strip():
            raise MemoryIngestError(f"import session 下存在缺 name 的畸形实体提案 {row[0]}")
        if row[5] != "create":
            raise MemoryIngestError(f"实体 {name!r} 的既有提案 action={row[5]!r}，必须为 create")
        source = row[3] or {}
        if source.get("logical_name") != name:
            raise MemoryIngestError(f"实体 {name!r} 的 source.logical_name 与 name 不一致")
        by_name.setdefault(name, []).append(row)

    result: dict[str, dict[str, Any]] = {}
    for name, items in by_name.items():
        if len(items) != 1:
            raise MemoryIngestError(f"import session 下实体 {name!r} 存在 {len(items)} 条提案")
        row = items[0]
        if row[2] not in ("pending", "confirmed"):
            raise MemoryIngestError(f"实体 {name!r} 的既有提案 status={row[2]!r}，不可幂等复用")
        source = row[3] or {}
        if (
            not is_typed_context_graph(source)
            or source.get("pipeline") != pipeline
            or source.get("document_id") != document_id
            or source.get("file_version") != file_version
        ):
            raise MemoryIngestError(f"实体 {name!r} 的既有提案 source 与本次 manifest 不一致")
        result[name] = {
            "proposal_id": str(row[0]),
            "status": row[2],
            "proposed_content": row[4],
        }
    extra = set(result) - manifest_names
    if extra:
        raise MemoryIngestError(f"manifest 缩减，已有实体 {sorted(extra)} 不在当前 manifest")
    return result


def _proposal_content(
    conn: Any,
    plan: ShadowFilingPlan,
    entity: EntityFilingPlan,
    generation_id: str,
    proposal_id_by_name: dict[str, str],
) -> dict[str, Any]:
    """构造与 memory_proposals.proposed_content 逐字可比对的规范 dict。

    外部父（target_proposal_id）在此锁行校验；同 manifest 目标按已处理序解析
    proposal_id（拓扑序异常立即失败）。
    """
    relations: list[dict[str, Any]] = []
    for rel in entity.required_relations:
        if rel.target_proposal_id is not None:
            _validate_external_parent_proposal(
                conn, rel.target_proposal_id, relation_type=rel.relation_type,
                source_entity=entity, generation_id=generation_id,
                tenant_id=plan.tenant_id, organization_id=plan.organization_id,
            )
            target_proposal_id = rel.target_proposal_id
        else:
            target_proposal_id = proposal_id_by_name.get(rel.target_name)
            if target_proposal_id is None:
                raise MemoryIngestError(
                    f"[{entity.name}] required_relations 目标 {rel.target_name!r} 尚未登记（拓扑序异常）"
                )
        relations.append({
            "relation_type": rel.relation_type,
            "target_name": rel.target_name,
            "target_proposal_id": target_proposal_id,
            "content": rel.content,
            "rationale": rel.rationale,
            "source_refs": [ref.to_proposal_dict() for ref in rel.source_refs],
        })
    return {
        "type_key": entity.type_key, "name": entity.name, "content": entity.content,
        "rationale": entity.rationale, "strategic_level": entity.strategic_level,
        "strategic_period": entity.strategic_period, "outcome_level": entity.outcome_level,
        "status_scope": entity.status_scope, "org_subtype": entity.org_subtype,
        "is_moat": entity.is_moat,
        "source_refs": [ref.to_proposal_dict() for ref in entity.source_refs],
        "required_relations": relations,
    }


def _propose_graph_entity(
    conn: Any,
    *,
    plan: ShadowFilingPlan,
    entity: EntityFilingPlan,
    generation_id: str,
    proposed_content: dict[str, Any],
) -> dict[str, Any]:
    return service.propose(
        tenant_id=plan.tenant_id,
        organization_id=plan.organization_id,
        generation_id=generation_id,
        target_kind="entity",
        action="create",
        proposed_content=proposed_content,
        proposed_by=plan.proposed_by,
        source={"pipeline": plan.pipeline, "document_id": plan.document_id,
                "file_version": plan.file_version, "logical_name": entity.name},
        import_session_id=plan.import_session_id,
        _connect=_borrowed_connection(conn),
    )
