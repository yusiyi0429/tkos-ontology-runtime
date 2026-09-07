"""Governed Context Graph write service.

Graph rows are applied only by :func:`confirm` under an organization-scoped shared advisory
lock.  Switching generations and destructive cleanup are separate host-controlled operations.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory_service.actors import (
    ActorRequiredError,
    HumanActorRequiredError,
    require_actor,
    require_human,
)
from memory_service.common import normalize_name
from memory_service.context_graph.concurrency import lock_shared_for_transaction
from memory_service.context_graph.query_contracts import is_legacy_generation
from memory_service.context_graph.types import (
    RELATION_TYPES,
    SCHEMA_VERSION,
    SOURCE_MARKER_KEY,
    ContextGraphValidationError,
    required_relation_types,
    validate_entity_content,
    validate_relation_matrix,
)


class ContextGraphError(RuntimeError):
    pass


class NotHumanConfirmerError(ContextGraphError):
    pass


class ProposalConflictError(ContextGraphError):
    pass


class SourceReferenceError(ContextGraphError):
    pass


class GraphInvariantError(ContextGraphError):
    pass


def _row(cur, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    cur.execute(sql, params)
    return cur.fetchone()


def _canonical_uuid_or_none(value) -> uuid.UUID | None:
    """Python 侧 UUID 规范化：非法返回 None（调用处映射为领域级泛化错误，不送 PG、
    不毒化借用事务）；合法返回 canonical ``uuid.UUID``（接受 urn:uuid:/大写/无连字符等
    ``uuid.UUID`` 可接受的等价形式）。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _require_human(cur, user_id: str, *, tenant_id: str, organization_id: str) -> None:
    try:
        require_human(
            cur,
            user_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            lock=True,
        )
    except HumanActorRequiredError as exc:
        raise NotHumanConfirmerError(str(exc)) from exc


def _shared_lock(cur, tenant_id: str, organization_id: str) -> None:
    lock_shared_for_transaction(cur, tenant_id, organization_id)


def _canonical_proposal_id(proposal_id) -> uuid.UUID:
    """proposal_id 在任何 SQL 前 Python 侧 canonical：非法值与 missing/foreign 同文，
    不触发 PG UUID cast 错误、不毒化借用事务。"""
    canonical = _canonical_uuid_or_none(proposal_id)
    if canonical is None:
        raise ProposalConflictError("proposal not found")
    return canonical


def _resolve_decider_scope(cur, decided_by) -> tuple[uuid.UUID, str, str]:
    """Actor 优先解析：canonical decided_by + 从 users 行派生其 home scope（FOR SHARE 锁定）。

    非法/missing/非 human 一律 NotHumanConfirmerError 泛化文案（不含 ID）；本函数不触碰
    memory_proposals——foreign proposal 在 actor 授权与 scope 解析前不被查询或锁定。
    """
    decided_by_uuid = _canonical_uuid_or_none(decided_by)
    if decided_by_uuid is None:
        raise NotHumanConfirmerError("decided_by 不是合法的用户标识")
    actor = _row(
        cur,
        "SELECT tenant_id, organization_id, kind FROM users WHERE user_id=%s FOR SHARE",
        (decided_by_uuid,),
    )
    if actor is None or actor["kind"] != "human":
        raise NotHumanConfirmerError("decided_by 不存在、不是 human 或不属于目标 scope")
    return decided_by_uuid, actor["tenant_id"], actor["organization_id"]


def create_generation(
    *, tenant_id: str, organization_id: str, label: str, status: str = "shadow",
    _connect: Callable[..., Any],
) -> str:
    """Create an empty generation. Switching status/current is intentionally not exposed."""
    if status != "shadow":
        raise ContextGraphError("graph governance may create shadow generations only")
    with _connect() as conn:
        row = conn.execute(
            """INSERT INTO context_graph_versions
                 (tenant_id, organization_id, label, status, baseline_audit_seq)
               VALUES (
                   %s,%s,%s,'shadow',
                   (SELECT coalesce(max(a.audit_seq),0)
                      FROM memory_audit a
                      JOIN memory_proposals p USING (proposal_id)
                     WHERE p.tenant_id=%s AND p.organization_id=%s)
               )
               RETURNING generation_id""",
            (tenant_id, organization_id, label, tenant_id, organization_id),
        ).fetchone()
    return str(row[0])


def propose(
    *, tenant_id: str, organization_id: str, generation_id: str,
    target_kind: str, action: str, proposed_content: dict[str, Any],
    target_id: str | None = None, base_revision: int | None = None,
    change_reason: str | None = None, proposed_by: str,
    source: dict[str, Any] | None = None, import_session_id: str | None = None,
    confidence: float | None = None, _connect: Callable[..., Any],
) -> dict[str, Any]:
    """Create a pending proposal for an explicitly selected current/shadow generation.

    ``proposed_by`` is required, stripped, and rejected fail-closed when blank.
    """
    if is_legacy_generation(generation_id):
        raise ContextGraphError("the archived graph generation is read-only")
    if target_kind not in ("entity", "relation"):
        raise ContextGraphError("target_kind must be entity/relation")
    if action not in ("create", "update", "deprecate"):
        raise ContextGraphError("unsupported proposal action")
    if action != "create" and not target_id:
        raise ContextGraphError(f"{action} requires target_id")
    if not isinstance(proposed_by, str) or not proposed_by.strip():
        raise ContextGraphError("proposed_by must be a non-empty string supplied by the caller")
    proposed_by = proposed_by.strip()
    # generation/target/import_session 的 UUID 在任何 SQL 前完成 Python 侧 canonical：
    # 非法输入走领域级泛化错误（与 missing 同文），不触发 PG 类型错误、不毒化借用事务。
    canonical_generation = _canonical_uuid_or_none(generation_id)
    if canonical_generation is None:
        raise ContextGraphError("proposal generation is not writable")
    generation_id = str(canonical_generation)
    # canonical 后再判 legacy：urn:uuid:/大写等等价形式不得绕过归档代只读门禁。
    if is_legacy_generation(generation_id):
        raise ContextGraphError("the archived graph generation is read-only")
    if target_id is not None:
        canonical_target = _canonical_uuid_or_none(target_id)
        if canonical_target is None:
            raise ContextGraphError("proposal target is outside its generation")
        target_id = str(canonical_target)
    if import_session_id is not None:
        canonical_import = _canonical_uuid_or_none(import_session_id)
        if canonical_import is None:
            raise ContextGraphError("invalid import_session_id")
        import_session_id = str(canonical_import)
    # The service owns the schema marker; callers cannot forge or remove it.
    proposal_source = {**(source or {}), SOURCE_MARKER_KEY: SCHEMA_VERSION}
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        # actor 校验（scoped SQL + FOR SHARE）必须先于 generation 查询：未授权 actor
        # 不能通过 generation 存在性/状态差异形成枚举侧信道。
        try:
            require_actor(
                cur,
                proposed_by,
                tenant_id=tenant_id,
                organization_id=organization_id,
                field_name="proposed_by",
                lock=True,
            )
        except ActorRequiredError as exc:
            raise ContextGraphError(str(exc)) from exc
        # require_actor 已通过即说明 proposed_by 是合法 UUID：后续 INSERT/audit 一律绑定
        # canonical 值，不绑定 urn/大写/无连字符等原始替代表示。
        proposed_by = str(uuid.UUID(proposed_by))
        generation = _row(
            cur,
            """SELECT status FROM context_graph_versions
                 WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s
                 FOR SHARE""",
            (generation_id, tenant_id, organization_id),
        )
        if generation is None or generation["status"] not in ("current", "shadow"):
            raise ContextGraphError("proposal generation is not writable")
        if target_id:
            table, key = (("semantic_entities", "entity_id") if target_kind == "entity"
                          else ("semantic_relations", "relation_id"))
            # SQL 同时约束 generation+tenant+org 并 FOR SHARE（check-to-insert 之间
            # 目标状态/scope 不可被并发改变）；泛化错误：target 不存在与 target 跨
            # scope/跨代走同一路径，不形成对象枚举侧信道。
            owner = _row(
                cur,
                f"SELECT revision FROM {table}"
                f" WHERE {key}=%s AND graph_generation_id=%s AND tenant_id=%s AND organization_id=%s"
                f" FOR SHARE",
                (target_id, generation_id, tenant_id, organization_id),
            )
            if owner is None:
                raise ContextGraphError("proposal target is outside its generation")
            if base_revision is None:
                raise ContextGraphError("update/deprecate requires base_revision")
        cur.execute(
            """INSERT INTO memory_proposals
                 (tenant_id, organization_id, graph_generation_id, target_kind, target_id,
                  base_revision, action, proposed_content, change_reason, source,
                  import_session_id, confidence, proposed_by, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending')
               RETURNING proposal_id, created_at""",
            (tenant_id, organization_id, generation_id, target_kind, target_id,
             base_revision, action, Jsonb(proposed_content), change_reason, Jsonb(proposal_source),
             import_session_id, confidence, proposed_by),
        )
        result = cur.fetchone()
    return {"proposal_id": str(result["proposal_id"]), "status": "pending", "created_at": result["created_at"]}


def _entity(cur, entity_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock else ""
    return _row(cur, f"SELECT * FROM semantic_entities WHERE entity_id=%s{suffix}", (entity_id,))


def _relation(cur, relation_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock else ""
    return _row(cur, f"SELECT * FROM semantic_relations WHERE relation_id=%s{suffix}", (relation_id,))


def _serializable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value.__class__.__module__ == "uuid" else value


def _snapshot(row: dict[str, Any], *, entity: bool) -> dict[str, Any]:
    keys = (
        ("type_key", "name", "content", "rationale", "strategic_level", "strategic_period",
         "outcome_level", "status_scope", "org_subtype", "is_moat", "revision", "status")
        if entity else
        ("source_id", "target_id", "relation_type", "content", "rationale", "revision", "status")
    )
    return _serializable({key: row.get(key) for key in keys})


def _validate_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not refs:
        raise SourceReferenceError("new-generation confirmed revisions require at least one source ref")
    ordinals: set[int] = set()
    checked: list[dict[str, Any]] = []
    for ref in refs:
        try:
            ordinal = int(ref["ordinal"])
            fragment_id = str(ref["fragment_id"])
            excerpt = ref["excerpt_snapshot"]
            content_hash = ref["content_hash_snapshot"]
            locator = dict(ref.get("source_locator_snapshot") or {})
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceReferenceError("invalid source ref shape") from exc
        if ordinal < 1 or ordinal in ordinals:
            raise SourceReferenceError("source ref ordinals must be unique positive integers")
        ordinals.add(ordinal)
        if not isinstance(excerpt, str) or not isinstance(content_hash, str):
            raise SourceReferenceError("source ref excerpt/hash snapshots must be strings")
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != content_hash:
            raise SourceReferenceError(f"fragment {fragment_id} excerpt/hash snapshot mismatch")
        checked.append({
            "fragment_id": fragment_id, "ordinal": ordinal,
            "excerpt_snapshot": excerpt, "content_hash_snapshot": content_hash,
            "source_locator_snapshot": locator,
        })
    return checked


def _insert_refs(
    cur, *, owner_kind: str, owner_id: str, generation_id: str,
    tenant_id: str, organization_id: str, refs: list[dict[str, Any]], replace: bool = False,
) -> None:
    table = "semantic_entity_source_refs" if owner_kind == "entity" else "semantic_relation_source_refs"
    owner_col = "entity_id" if owner_kind == "entity" else "relation_id"
    checked = _validate_refs(refs)
    if replace:
        cur.execute(f"DELETE FROM {table} WHERE {owner_col}=%s", (owner_id,))
    for ref in checked:
        cur.execute(
            f"""INSERT INTO {table}
                  ({owner_col}, graph_generation_id, tenant_id, organization_id, fragment_id,
                   ordinal, excerpt_snapshot, content_hash_snapshot, source_locator_snapshot)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (owner_id, generation_id, tenant_id, organization_id, ref["fragment_id"],
             ref["ordinal"], ref["excerpt_snapshot"], ref["content_hash_snapshot"],
             Jsonb(ref["source_locator_snapshot"])),
        )


def _current_refs(cur, owner_kind: str, owner_id: str) -> list[dict[str, Any]]:
    table = "semantic_entity_source_refs" if owner_kind == "entity" else "semantic_relation_source_refs"
    owner_col = "entity_id" if owner_kind == "entity" else "relation_id"
    cur.execute(
        f"""SELECT fragment_id, ordinal, excerpt_snapshot, content_hash_snapshot, source_locator_snapshot
              FROM {table} WHERE {owner_col}=%s ORDER BY ordinal""",
        (owner_id,),
    )
    return [_serializable(dict(row)) for row in cur.fetchall()]


def _reachable_root(cur, start_id: str, *, banned_id: str | None = None) -> bool:
    row = _row(
        cur,
        """WITH RECURSIVE up(entity_id, path) AS (
               SELECT %s::uuid, ARRAY[%s::uuid]
               UNION ALL
               SELECT r.target_id, up.path || r.target_id
                 FROM up JOIN semantic_relations r ON r.source_id=up.entity_id
                WHERE r.status='confirmed' AND r.relation_type='primary_alignment'
                  AND (%s::uuid IS NULL OR r.target_id<>%s::uuid)
                  AND NOT r.target_id=ANY(up.path)
             )
             SELECT coalesce(bool_or(e.type_key='CompanyVision' AND e.status='confirmed'),false) AS ok
               FROM up JOIN semantic_entities e ON e.entity_id=up.entity_id""",
        (start_id, start_id, banned_id, banned_id),
    )
    return bool(row["ok"])


def _would_cycle(cur, source_id: str, target_id: str, relation_type: str) -> bool:
    row = _row(
        cur,
        """WITH RECURSIVE up(entity_id, path) AS (
               SELECT %s::uuid, ARRAY[%s::uuid]
               UNION ALL
               SELECT r.target_id, up.path || r.target_id
                 FROM up JOIN semantic_relations r ON r.source_id=up.entity_id
                WHERE r.status='confirmed' AND r.relation_type=%s
                  AND NOT r.target_id=ANY(up.path)
             ) SELECT EXISTS(SELECT 1 FROM up WHERE entity_id=%s::uuid) AS cycles""",
        (target_id, target_id, relation_type, source_id),
    )
    return bool(row["cycles"])


def _validate_relation(
    cur, *, relation_type: str, source: dict[str, Any], target: dict[str, Any],
    generation_id: str, tenant_id: str, organization_id: str,
    check_cycle: bool = True, banned_root_path: str | None = None,
) -> None:
    for endpoint in (source, target):
        if endpoint.get("status") != "confirmed":
            raise GraphInvariantError("relation endpoints must be confirmed")
        if str(endpoint.get("graph_generation_id")) != str(generation_id):
            raise GraphInvariantError("cross-generation relation rejected")
        if endpoint.get("tenant_id") != tenant_id or endpoint.get("organization_id") != organization_id:
            raise GraphInvariantError("cross-scope relation rejected")
    try:
        validate_relation_matrix(relation_type, source, target)
    except ContextGraphValidationError as exc:
        raise GraphInvariantError(str(exc)) from exc
    source_id, target_id = str(source["entity_id"]), str(target["entity_id"])
    if source_id == target_id:
        raise GraphInvariantError("self relation rejected")
    if relation_type == "conflicts_with" and source_id >= target_id:
        raise GraphInvariantError("conflicts_with endpoints must be canonical")
    if check_cycle and relation_type in ("primary_alignment", "sub_strategy_of", "domain_outcome_of"):
        if _would_cycle(cur, source_id, target_id, relation_type):
            raise GraphInvariantError(f"{relation_type} would create a cycle")
    if relation_type == "primary_alignment" and not _reachable_root(cur, target_id, banned_id=banned_root_path):
        raise GraphInvariantError("primary_alignment target is not connected to CompanyVision")


def _draft_entity(content: dict[str, Any]) -> dict[str, Any]:
    required = ("type_key", "name", "content", "source_refs")
    missing = [key for key in required if key not in content]
    if missing:
        raise GraphInvariantError(f"entity proposal missing {missing}")
    draft = dict(content)
    try:
        validate_entity_content(
            draft["type_key"], draft["content"], draft.get("rationale"),
            strategic_level=draft.get("strategic_level"), is_moat=draft.get("is_moat"),
            strategic_period=draft.get("strategic_period"), outcome_level=draft.get("outcome_level"),
            status_scope=draft.get("status_scope"), org_subtype=draft.get("org_subtype"),
        )
    except ContextGraphValidationError as exc:
        raise GraphInvariantError(str(exc)) from exc
    return draft


def validate_proposed_content(target_kind: str, content: dict[str, Any]) -> None:
    """Validate current typed proposal content before a pending edit is saved.

    Entities use the same ``_draft_entity`` contract as confirmation.  Relations receive only
    database-independent shape and vocabulary checks here.  Endpoint, scope, generation, matrix,
    cycle, and reachability checks remain under the confirmation transaction's row locks.
    """
    if target_kind == "entity":
        _draft_entity(content)
        return
    if target_kind == "relation":
        missing = [k for k in ("source_id", "target_id", "relation_type") if not content.get(k)]
        if missing:
            raise GraphInvariantError(f"relation proposal missing {missing}")
        relation_type = str(content["relation_type"])
        if relation_type not in RELATION_TYPES:
            raise GraphInvariantError(
                f"relation_type '{relation_type}' 不在关系词表内：{sorted(RELATION_TYPES)}"
            )
        return
    raise GraphInvariantError(f"非法 target_kind：{target_kind}")



def _relation_target_selector(spec: dict[str, Any]) -> tuple[str, str]:
    """Return the one explicit UUID selector accepted by a required relation."""
    keys = [key for key in ("target_id", "target_proposal_id") if key in spec]
    if len(keys) != 1:
        raise GraphInvariantError(
            "required relation must provide exactly one of target_id or target_proposal_id"
        )
    key = keys[0]
    value = spec[key]
    try:
        parsed = uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise GraphInvariantError(f"required relation {key} must be an explicit UUID") from exc
    if parsed.int == 0:
        raise GraphInvariantError(f"required relation {key} must not be a placeholder UUID")
    return key, str(parsed)


def _validate_required_relations(draft: dict[str, Any]) -> list[dict[str, Any]]:
    relations = draft.get("required_relations") or []
    if draft["type_key"] == "CompanyVision" and relations:
        raise GraphInvariantError("CompanyVision must not carry required_relations")
    required = required_relation_types(draft)
    counts: dict[str, int] = {}
    for relation in relations:
        if not isinstance(relation, dict):
            raise GraphInvariantError("required relation must be an object")
        _relation_target_selector(relation)
        relation_type = relation.get("relation_type")
        counts[relation_type] = counts.get(relation_type, 0) + 1
    for relation_type, count in required.items():
        if counts.get(relation_type) != count:
            raise GraphInvariantError(f"entity requires exactly {count} {relation_type} relation(s)")
    unexpected = set(counts) - set(required)
    if unexpected:
        raise GraphInvariantError(f"required_relations contains non-required types: {sorted(unexpected)}")
    return relations


def _resolve_required_relation_target(
    cur, *, spec: dict[str, Any], tenant_id: str, organization_id: str, generation_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Resolve and lock one required-relation target without guessing names or future IDs.

    ``target_proposal_id`` is deliberately a confirmation-time reference.  It may point only
    at a confirmed entity-create proposal in the same graph scope, backed by exactly one
    internally consistent confirmed audit row.  The parent is never confirmed as a side effect.
    """
    selector, selector_id = _relation_target_selector(spec)
    if selector == "target_id":
        target = _entity(cur, selector_id, lock=True)
        if target is None:
            raise GraphInvariantError(f"required relation target missing: {selector_id}")
        return target, {"target_id": selector_id}

    parent = _row(
        cur, "SELECT * FROM memory_proposals WHERE proposal_id=%s FOR UPDATE", (selector_id,),
    )
    if parent is None:
        raise ProposalConflictError("target proposal not found")
    if (parent.get("tenant_id") != tenant_id
            or parent.get("organization_id") != organization_id
            or str(parent.get("graph_generation_id")) != str(generation_id)):
        raise ProposalConflictError("target proposal is outside child scope or generation")
    if parent.get("target_kind") != "entity" or parent.get("action") != "create":
        raise ProposalConflictError("target proposal must be an entity create proposal")
    if parent.get("status") != "confirmed":
        raise ProposalConflictError("target proposal must already be confirmed")

    cur.execute(
        """SELECT a.decision,a.target_kind,a.target_id,a.decided_by,u.kind AS decided_by_kind
             FROM memory_audit a
             JOIN users u ON u.user_id=a.decided_by
            WHERE a.proposal_id=%s
            ORDER BY a.audit_seq
            FOR UPDATE OF a,u""",
        (selector_id,),
    )
    audits = cur.fetchall()
    if len(audits) != 1:
        raise ProposalConflictError("target proposal must have exactly one audit row")
    audit = audits[0]
    audit_target = audit.get("target_id")
    if (audit.get("decision") != "confirmed" or audit.get("target_kind") != "entity"
            or audit_target is None):
        raise ProposalConflictError("target proposal audit is not a confirmed entity decision")
    if audit.get("decided_by_kind") != "human":
        raise ProposalConflictError("target proposal must have been confirmed by a human")
    target_id = str(audit_target)
    if parent.get("target_id") is None or str(parent["target_id"]) != target_id:
        raise ProposalConflictError("target proposal and audit target_id are inconsistent")

    target = _entity(cur, target_id, lock=True)
    if target is None:
        raise ProposalConflictError("confirmed target entity is missing")
    if target.get("status") != "confirmed":
        raise ProposalConflictError("target entity is not confirmed")
    if (str(target.get("graph_generation_id")) != str(generation_id)
            or target.get("tenant_id") != tenant_id
            or target.get("organization_id") != organization_id):
        raise ProposalConflictError("target entity is outside child scope or generation")
    return target, {"target_proposal_id": selector_id, "target_id": target_id}


def _write_audit(
    cur, *, proposal: dict[str, Any], target_id: str | None, revision: int | None,
    before: dict[str, Any] | None, after: dict[str, Any] | None,
    decision: str, decided_by: str, decision_note: str | None,
) -> str:
    cur.execute(
        """INSERT INTO memory_audit
             (proposal_id,target_kind,target_id,revision,before_content,after_content,
              decision,decided_by,decision_note)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING audit_id""",
        (proposal["proposal_id"], proposal["target_kind"], target_id, revision,
         Jsonb(before) if before is not None else None,
         Jsonb(after) if after is not None else None,
         decision, decided_by, decision_note),
    )
    return str(cur.fetchone()["audit_id"])


def _insert_relation(
    cur, *, source: dict[str, Any], target: dict[str, Any], spec: dict[str, Any],
    generation_id: str, tenant_id: str, organization_id: str, decided_by: str,
    banned_root_path: str | None = None,
) -> str:
    relation_type = spec.get("relation_type")
    _validate_relation(
        cur, relation_type=relation_type, source=source, target=target,
        generation_id=generation_id, tenant_id=tenant_id, organization_id=organization_id,
        banned_root_path=banned_root_path,
    )
    refs = _validate_refs(spec.get("source_refs") or [])
    cur.execute(
        """INSERT INTO semantic_relations
             (tenant_id,organization_id,graph_generation_id,source_id,target_id,relation_type,
              content,rationale,revision,status,confirmed_by,confirmed_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,'confirmed',%s,now())
           RETURNING relation_id""",
        (tenant_id, organization_id, generation_id, source["entity_id"], target["entity_id"],
         relation_type, Jsonb(spec.get("content") or {}), spec.get("rationale"), decided_by),
    )
    relation_id = str(cur.fetchone()["relation_id"])
    _insert_refs(
        cur, owner_kind="relation", owner_id=relation_id, generation_id=generation_id,
        tenant_id=tenant_id, organization_id=organization_id, refs=refs,
    )
    return relation_id


def _confirm_entity_create(cur, proposal: dict[str, Any], decided_by: str) -> tuple[str, int, dict[str, Any]]:
    draft = _draft_entity(proposal["proposed_content"])
    relation_specs = _validate_required_relations(draft)
    tenant, org, generation = proposal["tenant_id"], proposal["organization_id"], str(proposal["graph_generation_id"])
    # Resolve every parent before the child INSERT.  Failures therefore leave no graph row even
    # before transaction rollback, while the FOR UPDATE locks remain held through child confirm.
    resolved_targets = [
        _resolve_required_relation_target(
            cur, spec=spec, tenant_id=tenant, organization_id=org, generation_id=generation,
        )
        for spec in relation_specs
    ]
    refs = _validate_refs(draft["source_refs"])
    cur.execute(
        """INSERT INTO semantic_entities
             (tenant_id,organization_id,graph_generation_id,type_key,name,normalized_name,content,
              rationale,strategic_level,strategic_period,outcome_level,status_scope,org_subtype,is_moat,
              revision,status,confirmed_by,confirmed_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'confirmed',%s,now())
           RETURNING *""",
        (tenant, org, generation, draft["type_key"], draft["name"], normalize_name(draft["name"]),
         Jsonb(draft["content"]), draft.get("rationale"), draft.get("strategic_level"),
         draft.get("strategic_period"), draft.get("outcome_level"), draft.get("status_scope"),
         draft.get("org_subtype"), draft.get("is_moat"), decided_by),
    )
    entity = cur.fetchone()
    entity_id = str(entity["entity_id"])
    _insert_refs(
        cur, owner_kind="entity", owner_id=entity_id, generation_id=generation,
        tenant_id=tenant, organization_id=org, refs=refs,
    )
    relation_ids: list[str] = []
    resolved_relations: list[dict[str, str]] = []
    for spec, (target, resolution) in zip(relation_specs, resolved_targets, strict=True):
        relation_id = _insert_relation(
            cur, source=entity, target=target, spec=spec, generation_id=generation,
            tenant_id=tenant, organization_id=org, decided_by=decided_by,
        )
        relation_ids.append(relation_id)
        resolved_relations.append({
            "relation_id": relation_id,
            "relation_type": str(spec.get("relation_type")),
            **resolution,
        })
    after = _snapshot(entity, entity=True)
    after["source_refs"] = refs
    after["required_relation_ids"] = relation_ids
    after["resolved_required_relations"] = resolved_relations
    return entity_id, int(entity["revision"]), after


def _confirm_entity_update(cur, proposal: dict[str, Any], decided_by: str) -> tuple[str, int, dict[str, Any], dict[str, Any]]:
    current = _entity(cur, str(proposal["target_id"]), lock=True)
    if current is None or current["status"] != "confirmed":
        raise ProposalConflictError("entity is absent or not confirmed")
    if int(current["revision"]) != proposal["base_revision"]:
        raise ProposalConflictError("stale entity base_revision")
    draft = _draft_entity(proposal["proposed_content"])
    immutable = ("type_key", "strategic_level", "strategic_period", "outcome_level", "status_scope", "org_subtype", "is_moat")
    if any(draft.get(key) != current.get(key) for key in immutable):
        raise GraphInvariantError("entity type/discriminator changes require a new entity, not in-place update")
    if draft.get("required_relations"):
        raise GraphInvariantError("entity update cannot create required_relations")
    tenant, org, generation = proposal["tenant_id"], proposal["organization_id"], str(proposal["graph_generation_id"])
    refs = _validate_refs(draft["source_refs"])
    before = _snapshot(current, entity=True)
    before["source_refs"] = _current_refs(cur, "entity", str(current["entity_id"]))
    new_revision = int(current["revision"]) + 1
    cur.execute(
        """UPDATE semantic_entities
              SET name=%s,normalized_name=%s,content=%s,rationale=%s,revision=%s,
                  confirmed_by=%s,confirmed_at=now(),updated_at=now()
            WHERE entity_id=%s AND revision=%s AND graph_generation_id=%s
            RETURNING *""",
        (draft["name"], normalize_name(draft["name"]), Jsonb(draft["content"]), draft.get("rationale"),
         new_revision, decided_by, current["entity_id"], proposal["base_revision"], generation),
    )
    updated = cur.fetchone()
    if updated is None:
        raise ProposalConflictError("entity CAS failed")
    _insert_refs(
        cur, owner_kind="entity", owner_id=str(current["entity_id"]), generation_id=generation,
        tenant_id=tenant, organization_id=org, refs=refs, replace=True,
    )
    after = _snapshot(updated, entity=True)
    after["source_refs"] = refs
    return str(current["entity_id"]), new_revision, before, after


def _confirm_entity_deprecate(cur, proposal: dict[str, Any], decided_by: str) -> tuple[str, int, dict[str, Any], dict[str, Any]]:
    current = _entity(cur, str(proposal["target_id"]), lock=True)
    if current is None or current["status"] != "confirmed":
        raise ProposalConflictError("entity is absent or not confirmed")
    if int(current["revision"]) != proposal["base_revision"]:
        raise ProposalConflictError("stale entity base_revision")
    if current["type_key"] == "CompanyVision":
        raise GraphInvariantError("CompanyVision cannot be deprecated in graph governance")
    tenant, org, generation = proposal["tenant_id"], proposal["organization_id"], str(proposal["graph_generation_id"])
    cur.execute(
        """SELECT r.source_id FROM semantic_relations r
             WHERE r.target_id=%s AND r.relation_type='primary_alignment' AND r.status='confirmed'
             ORDER BY r.source_id FOR UPDATE""",
        (current["entity_id"],),
    )
    child_ids = {str(row["source_id"]) for row in cur.fetchall()}
    replacements = proposal["proposed_content"].get("reparent_children") or []
    replacement_ids = {str(item.get("child_id")) for item in replacements}
    if child_ids != replacement_ids or len(replacements) != len(replacement_ids):
        raise GraphInvariantError("reparent_children must cover every direct primary child exactly once")
    before = _snapshot(current, entity=True)
    before["source_refs"] = _current_refs(cur, "entity", str(current["entity_id"]))
    new_edges: list[str] = []
    for item in replacements:
        child = _entity(cur, str(item["child_id"]))
        parent = _entity(cur, str(item["new_parent_id"]))
        if child is None or parent is None:
            raise GraphInvariantError("reparent endpoint missing")
        spec = {
            "relation_type": "primary_alignment", "content": item.get("content") or {},
            "rationale": item.get("rationale"), "source_refs": item.get("source_refs") or [],
        }
        # Old primary edges still exist now; cycle validation remains valid, and root reachability must avoid retired node.
        _validate_relation(
            cur, relation_type="primary_alignment", source=child, target=parent,
            generation_id=generation, tenant_id=tenant, organization_id=org,
            banned_root_path=str(current["entity_id"]),
        )
        _validate_refs(spec["source_refs"])
    cur.execute(
        """UPDATE semantic_relations
              SET status='deprecated',revision=revision+1,confirmed_by=%s,confirmed_at=now(),updated_at=now()
            WHERE status='confirmed' AND (source_id=%s OR target_id=%s)""",
        (decided_by, current["entity_id"], current["entity_id"]),
    )
    # Child->old-parent edges are now deprecated, so create every replacement atomically.
    for item in replacements:
        child = _entity(cur, str(item["child_id"]))
        parent = _entity(cur, str(item["new_parent_id"]))
        new_edges.append(_insert_relation(
            cur, source=child, target=parent,
            spec={"relation_type": "primary_alignment", "content": item.get("content") or {},
                  "rationale": item.get("rationale"), "source_refs": item.get("source_refs") or []},
            generation_id=generation, tenant_id=tenant, organization_id=org, decided_by=decided_by,
            banned_root_path=str(current["entity_id"]),
        ))
    new_revision = int(current["revision"]) + 1
    cur.execute(
        """UPDATE semantic_entities
              SET status='deprecated',revision=%s,confirmed_by=%s,confirmed_at=now(),updated_at=now()
            WHERE entity_id=%s AND revision=%s AND graph_generation_id=%s RETURNING *""",
        (new_revision, decided_by, current["entity_id"], proposal["base_revision"], generation),
    )
    updated = cur.fetchone()
    if updated is None:
        raise ProposalConflictError("entity deprecate CAS failed")
    after = _snapshot(updated, entity=True)
    after["source_refs"] = before["source_refs"]
    after["reparented_relation_ids"] = new_edges
    return str(current["entity_id"]), new_revision, before, after


def _confirm_relation(cur, proposal: dict[str, Any], decided_by: str) -> tuple[str, int, dict[str, Any] | None, dict[str, Any]]:
    content = proposal["proposed_content"]
    tenant, org, generation = proposal["tenant_id"], proposal["organization_id"], str(proposal["graph_generation_id"])
    action = proposal["action"]
    if action == "create":
        source, target = _entity(cur, str(content.get("source_id"))), _entity(cur, str(content.get("target_id")))
        if source is None or target is None:
            raise GraphInvariantError("relation endpoint missing")
        relation_id = _insert_relation(
            cur, source=source, target=target, spec=content, generation_id=generation,
            tenant_id=tenant, organization_id=org, decided_by=decided_by,
        )
        relation = _relation(cur, relation_id)
        after = _snapshot(relation, entity=False)
        after["source_refs"] = _current_refs(cur, "relation", relation_id)
        return relation_id, 1, None, after
    current = _relation(cur, str(proposal["target_id"]), lock=True)
    if current is None or current["status"] != "confirmed" or int(current["revision"]) != proposal["base_revision"]:
        raise ProposalConflictError("relation is absent, terminal, or stale")
    relation_id = str(current["relation_id"])
    before = _snapshot(current, entity=False)
    before["source_refs"] = _current_refs(cur, "relation", relation_id)
    new_revision = int(current["revision"]) + 1
    if action == "deprecate":
        cur.execute(
            """UPDATE semantic_relations SET status='deprecated',revision=%s,confirmed_by=%s,
                      confirmed_at=now(),updated_at=now()
                WHERE relation_id=%s AND revision=%s AND graph_generation_id=%s RETURNING *""",
            (new_revision, decided_by, relation_id, proposal["base_revision"], generation),
        )
        updated = cur.fetchone()
        after = _snapshot(updated, entity=False)
        after["source_refs"] = before["source_refs"]
        return relation_id, new_revision, before, after
    immutable = ("source_id", "target_id", "relation_type")
    if any(str(content.get(key)) != str(current[key]) for key in immutable):
        raise GraphInvariantError("relation endpoints/type cannot change in-place")
    refs = _validate_refs(content.get("source_refs") or [])
    cur.execute(
        """UPDATE semantic_relations SET content=%s,rationale=%s,revision=%s,confirmed_by=%s,
                  confirmed_at=now(),updated_at=now()
            WHERE relation_id=%s AND revision=%s AND graph_generation_id=%s RETURNING *""",
        (Jsonb(content.get("content") or {}), content.get("rationale"), new_revision, decided_by,
         relation_id, proposal["base_revision"], generation),
    )
    updated = cur.fetchone()
    if updated is None:
        raise ProposalConflictError("relation CAS failed")
    _insert_refs(
        cur, owner_kind="relation", owner_id=relation_id, generation_id=generation,
        tenant_id=tenant, organization_id=org, refs=refs, replace=True,
    )
    after = _snapshot(updated, entity=False)
    after["source_refs"] = refs
    return relation_id, new_revision, before, after


def confirm(
    proposal_id: str, *, decided_by: str, decision_note: str | None = None,
    gateway: Any,
    _connect: Callable[..., Any],
) -> dict[str, Any]:
    """Confirm one proposal and all required edges/source refs in one transaction.

    Entity confirmation, required relations, and source refs commit atomically.  Embedding is a
    network call and therefore runs after that transaction.  A failed embedding does not roll back
    valid governance data; the result reports the failure and ``backfill_generation_embeddings``
    can fill the temporary retrieval gap idempotently.
    """
    pid = _canonical_proposal_id(proposal_id)
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        # actor 优先：scope 从 users 行派生并 FOR SHARE 锁定；foreign proposal 在此前不被
        # 查询或锁定。
        decided_by_uuid, scope_t, scope_o = _resolve_decider_scope(cur, decided_by)
        # scoped lookup 先行：missing/foreign 在 advisory lock 之前即失败（零锁、同文、无 ID）。
        proposal = _row(
            cur,
            """SELECT * FROM memory_proposals
                WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s""",
            (pid, scope_t, scope_o),
        )
        if proposal is None:
            raise ProposalConflictError("proposal not found")
        # lookup 通过后才取 advisory 锁；锁后 FOR UPDATE 重读并复核 actor/scope，
        # 消除 lookup 与锁定之间的状态窗口。
        _shared_lock(cur, scope_t, scope_o)
        proposal = _row(
            cur,
            """SELECT * FROM memory_proposals
                WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s
                FOR UPDATE""",
            (pid, scope_t, scope_o),
        )
        if proposal is None:
            raise ProposalConflictError("proposal not found")
        # FOR UPDATE 同时同事务复核 actor 仍为本 scope human（users 行 FOR SHARE 仍持有）。
        _require_human(cur, decided_by_uuid, tenant_id=scope_t, organization_id=scope_o)
        decided_by = str(decided_by_uuid)
        if proposal["status"] != "pending":
            raise ProposalConflictError(f"proposal is terminal: {proposal['status']}")
        generation = _row(
            cur,
            """SELECT status FROM context_graph_versions
                 WHERE generation_id=%s AND tenant_id=%s AND organization_id=%s""",
            (proposal["graph_generation_id"], proposal["tenant_id"], proposal["organization_id"]),
        )
        if generation is None or generation["status"] not in ("current", "shadow"):
            raise ProposalConflictError("proposal generation is no longer writable")
        if proposal["target_kind"] == "entity":
            if proposal["action"] == "create":
                target_id, revision, after = _confirm_entity_create(cur, proposal, decided_by)
                before = None
            elif proposal["action"] == "update":
                target_id, revision, before, after = _confirm_entity_update(cur, proposal, decided_by)
            else:
                target_id, revision, before, after = _confirm_entity_deprecate(cur, proposal, decided_by)
        else:
            target_id, revision, before, after = _confirm_relation(cur, proposal, decided_by)
        audit_id = _write_audit(
            cur, proposal=proposal, target_id=target_id, revision=revision,
            before=before, after=after, decision="confirmed", decided_by=decided_by,
            decision_note=decision_note,
        )
        cur.execute(
            "UPDATE memory_proposals SET status='confirmed',target_id=%s WHERE proposal_id=%s AND status='pending'",
            (target_id, pid),
        )
        if cur.rowcount != 1:
            raise ProposalConflictError("proposal CAS failed")

    # ---------- 提交后：事务外补 embedding ----------
    embedding = _embed_confirmed_entity(
        target_kind=str(proposal["target_kind"]), action=str(proposal["action"]),
        entity_id=target_id, revision=revision, gateway=gateway, _connect=_connect,
    )
    return {
        "proposal_id": proposal_id, "target_kind": proposal["target_kind"],
        "target_id": target_id, "revision": revision, "status": "confirmed",
        "audit_id": audit_id, "embedding": embedding,
    }


def _embed_confirmed_entity(
    *, target_kind: str, action: str, entity_id: str | None, revision: int | None,
    gateway: Any, _connect: Callable[..., Any],
) -> str:
    """为刚确认的实体生成并写入 embedding，返回诚实的结果标识。

    只处理 entity 的 create/update：relation 表无 embedding 列，deprecate 后实体不再
    参与检索。文本装配复用 backfill.graph_entity_embedding_text，不在本模块维护第二份
    字段清单（否则会与叙事投影双轨漂移）。

    写入用 revision 做 CAS：若在 embed 的网络窗口里实体已被另一次确认推到更新的
    revision，本次的旧向量就不得覆盖它（结果标为 stale）。update 动作下内容已变，
    向量必须重算，所以这里不能用 backfill 那条 embedding IS NULL 的幂等条件。
    """
    if target_kind != "entity" or action not in ("create", "update") or entity_id is None:
        return "skipped"
    try:
        from pgvector import HalfVector

        from memory_service.context_graph.backfill import graph_entity_embedding_text

        with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = _row(
                cur,
                """SELECT type_key,name,rationale,content,tenant_id,organization_id
                     FROM semantic_entities WHERE entity_id=%s""",
                (entity_id,),
            )
        if row is None or row["type_key"] is None:
            return "skipped"
        text = graph_entity_embedding_text(
            type_key=str(row["type_key"]), name=str(row["name"]),
            rationale=row["rationale"], content=row["content"] or {},
        )
        vectors = gateway.embed([text])
        if not vectors or not vectors[0]:
            raise ValueError("embedder returned empty vector")
        with _connect() as conn:
            # 显式嵌套事务：借用调用方事务时这是一个 savepoint——embedding UPDATE 失败只
            # 回滚到 savepoint，不把 caller 的外层事务打进 aborted 状态（否则治理写会
            # 在 caller commit 时被一并回滚）。
            with conn.transaction():
                updated = conn.execute(
                    """UPDATE semantic_entities SET embedding=%s
                        WHERE entity_id=%s AND revision=%s""",
                    (HalfVector(vectors[0]), entity_id, revision),
                ).rowcount
        return "ok" if updated == 1 else "stale"
    except Exception as exc:  # 确认已提交，embed 失败不能把它拖下水
        return f"failed: {exc}"


def reject(
    proposal_id: str, *, decided_by: str, decision_note: str | None = None,
    _connect: Callable[..., Any],
) -> dict[str, Any]:
    pid = _canonical_proposal_id(proposal_id)
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        # actor 优先：scope 从 users 行派生并 FOR SHARE 锁定；foreign proposal 在此前不被
        # 查询或锁定。
        decided_by_uuid, scope_t, scope_o = _resolve_decider_scope(cur, decided_by)
        # scoped lookup 先行：missing/foreign 在 advisory lock 之前即失败（零锁、同文、无 ID）。
        proposal = _row(
            cur,
            """SELECT * FROM memory_proposals
                WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s""",
            (pid, scope_t, scope_o),
        )
        if proposal is None:
            raise ProposalConflictError("proposal not found")
        # lookup 通过后才取 advisory 锁；锁后 FOR UPDATE 重读并复核 actor/scope。
        _shared_lock(cur, scope_t, scope_o)
        proposal = _row(
            cur,
            """SELECT * FROM memory_proposals
                WHERE proposal_id=%s AND tenant_id=%s AND organization_id=%s
                FOR UPDATE""",
            (pid, scope_t, scope_o),
        )
        if proposal is None:
            raise ProposalConflictError("proposal not found")
        # FOR UPDATE 同时同事务复核 actor 仍为本 scope human（users 行 FOR SHARE 仍持有）。
        _require_human(cur, decided_by_uuid, tenant_id=scope_t, organization_id=scope_o)
        decided_by = str(decided_by_uuid)
        if proposal["status"] != "pending":
            raise ProposalConflictError(f"proposal is terminal: {proposal['status']}")
        before = None
        if proposal["target_id"]:
            current = (_entity(cur, str(proposal["target_id"])) if proposal["target_kind"] == "entity"
                       else _relation(cur, str(proposal["target_id"])))
            before = _snapshot(current, entity=proposal["target_kind"] == "entity") if current else None
        audit_id = _write_audit(
            cur, proposal=proposal, target_id=str(proposal["target_id"]) if proposal["target_id"] else None,
            revision=None, before=before, after=None, decision="rejected", decided_by=decided_by,
            decision_note=decision_note,
        )
        cur.execute("UPDATE memory_proposals SET status='rejected' WHERE proposal_id=%s", (pid,))
    return {"proposal_id": proposal_id, "status": "rejected", "audit_id": audit_id}
