"""A2 公司组合核心：collect / form / validate_current / recheck_live。

本模块与 a2_service.py（独立开发者拥有）严格解耦——不 import 任何 a2_service
符号，避免循环并允许并行演进。复用 a2_models / a2_readers / protocol / db /
canon / errors。Codex 拥有独立验收并在本模块就位后负责接线。

所有函数在调用方持有的 scope 栅栏事务内运行；本模块不出 commit、不开连接、
不派生事务；只做服务端派生与权威重查。Server identity 集合（成员集合、正式
Submission 集合、当前 Round definition / state、当前 source revision / hash /
有效期）一律走原始 scoped SQL 现场派生并授权；执行器暴露给调用方的每条材料都
经过显式授权。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from . import canon, db, protocol
from .a2_models import CLOSURE_MAX_DEPTH, CLOSURE_MAX_NODES, CompositionManifest, CompositionValidationError, MANIFEST_SCHEMA_VERSION, compute_manifest_hash, static_conflict_reasons, validate_manifest
from .errors import GovernedError
from .service import ActionExecution


# ---------- shared failure + canonical helpers ----------

def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _canonical_uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        _fail("INVALID_REQUEST", f"invalid UUID: {value!r}", 422)


# ---------- execution.kind dispatch ----------

_A2_FORM_KINDS = {"form_company_composition",
                  "confirm_company_composition",
                  "activate_company_composition"}


# ---------- identity / visibility primitives ----------


def _round_state(execution: ActionExecution, round_object_id: str) -> dict[str, Any] | None:
    row = execution.conn.execute(
        """SELECT * FROM gov_formation_round_state
            WHERE scope_id=%s AND object_id=%s""",
        (execution.ctx.scope_id, _canonical_uuid(round_object_id)),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def _latest_formal_submission(execution, round_object_id, domain_id):
    row = execution.conn.execute(
        'SELECT * FROM gov_round_formal_submissions WHERE scope_id=%s AND round_object_id=%s AND domain_id=%s',
        (execution.ctx.scope_id, round_object_id, domain_id)).fetchone()
    return db.jsonable(row) if row else None


def _cross_domain_assignment(execution: ActionExecution,
                              assignment_id: str) -> dict[str, Any] | None:
    """Cross-domain live assignment read.

    Hook ``execution.a2_cross_domain_assignment`` lets the A2Execution subclass
    expose a wider read; otherwise a raw scoped SQL selects the assignment plus
    its principal row, applying DB-clock validity in SQL (not in Python) and
    rejecting non-human principals so callers can never see an agent slot.
    """
    hook = getattr(execution, "a2_cross_domain_assignment", None)
    if hook is not None:
        return hook(execution.conn, execution.ctx, _canonical_uuid(assignment_id))
    row = execution.conn.execute(
        """SELECT a.assignment_id, a.scope_id, a.domain_id, a.principal_id, a.role,
                  a.active, a.valid_from, a.valid_to,
                  p.principal_type, p.active AS principal_active
             FROM gov_role_assignments a JOIN gov_principals p
               ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
            WHERE a.scope_id=%s AND a.assignment_id=%s
              AND a.active AND p.active AND p.principal_type='human'
              AND a.valid_from <= clock_timestamp()
              AND (a.valid_to IS NULL OR clock_timestamp() < a.valid_to)""",
        (execution.ctx.scope_id, _canonical_uuid(assignment_id)),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def _require_visible_assignment(execution: ActionExecution,
                                 assignment_id: str,
                                 *, role: str | None = None,
                                 domain_id: str | None = None) -> dict[str, Any]:
    """Validate that the slot assignment exists, is currently valid, is human,
    and matches the expected role + domain.  Does NOT require the slot's
    principal to equal the current actor: the actor's own authorization is
    checked separately before core dispatch, and CEO must be allowed to form
    with distinct DRI principals."""
    assignment = _cross_domain_assignment(execution, assignment_id)
    if assignment is None:
        _fail("FORBIDDEN", f"assignment {assignment_id} is not currently valid.")
    if str(assignment.get("principal_type")) != "human":
        _fail("FORBIDDEN", f"assignment {assignment_id} principal is not human.")
    if role is not None and assignment["role"] != role:
        _fail("FORBIDDEN", f"assignment {assignment_id} role={assignment['role']} != required {role}.")
    if domain_id is not None and str(assignment["domain_id"]) != _canonical_uuid(domain_id):
        _fail("FORBIDDEN", f"assignment {assignment_id} domain mismatch.")
    return assignment


def _current_formal_submissions(execution, round_object_id, member_domains):
    head = execution.head(round_object_id)
    definition = execution.revision(round_object_id, head['latest_revision_id'])['payload']
    members = {m['domain_id']: m for m in definition['members']}
    result = {}
    for domain in member_domains:
        pointer = _latest_formal_submission(execution, round_object_id, domain)
        if pointer is None: continue
        slot = members[domain]
        assignment = execution.assignment(slot['dri_assignment_id'])
        if assignment['role'] != 'DOMAIN_DRI' or assignment['domain_id'] != domain or assignment['principal_id'] != slot['dri_principal_id']:
            _fail('FORBIDDEN')
        revision = execution.revision(pointer['submission_object_id'], pointer['submission_revision_id'])
        payload = revision['payload']
        if (payload['round_object_id'], payload['domain_id'], payload['dri_assignment_id'], payload['dri_principal_id']) != (
                round_object_id, domain, slot['dri_assignment_id'], slot['dri_principal_id']):
            _fail('COMPOSITION_INPUT_CHANGED')
        if (pointer['published_by_assignment_id'], pointer['published_by_principal_id']) != (slot['dri_assignment_id'], slot['dri_principal_id']):
            _fail('COMPOSITION_INPUT_CHANGED')
        result[domain] = dict(pointer, domain_id=domain, payload=payload, payload_hash=revision['payload_hash'])
    return result


# ---------- dependency id namespace (stable, canonical) ----------

_DEP_NAMESPACE = canon.digest({"ns": "tkos.composition.dependency_id/0.1"})


def _dependency_id(pool_key: tuple[str, str, str]) -> str:
    """Stable dependency UUID: identical (resource_id, period_id, unit) yields the
    same id across revalidation; no random UUID4 drift."""
    seed = f"{_DEP_NAMESPACE}:{pool_key[0]}:{pool_key[1]}:{pool_key[2]}"
    return str(UUID(bytes=bytes.fromhex(canon.digest({"seed": seed})[:32])))


def _ensure_signed_int(value: Any, *, field: str) -> int:
    """严格 int，不接受 bool / float / str；遵循 a2_models NonNegativeInt/PositiveInt。"""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("INVALID_REQUEST",
              f"{field} must be a strict non-negative integer, got {type(value).__name__}.",
              422)
    if value < 0:
        _fail("INVALID_REQUEST", f"{field} must be non-negative.", 422)
    return int(value)


# ---------- helpers for form ----------

def _objects_status_matches(obj: dict[str, Any], status: str) -> bool:
    """Read current lifecycle_status from gov_objects; absent status column → fail."""
    if "lifecycle_status" not in obj:
        _fail("INVALID_STATE", "lifecycle_status missing on object header.")
    return str(obj["lifecycle_status"]) == status


def _resolve_method_profile_ref(execution: ActionExecution,
                                 round_object_id: str,
                                 round_method_ref: dict[str, Any]) -> dict[str, Any]:
    """Adopt the bound method profile triple from the current Round's profile binding.

    The Round definition already carries the spec; this materializes the canonical
    triple (profile_id + revision + canonical_hash) by reading the live binding
    profile.  We never trust the round_payload's hash alone; we re-resolve.
    """
    binding = protocol.current_binding(execution.conn, execution.ctx.scope_id, round_object_id)
    if binding is None or binding["protocol_id"] != "tkos.contract-a":
        _fail("PROTOCOL_BINDING_MISSING")
    if binding.get("profile_id") != round_method_ref.get("profile_id"):
        _fail("METHOD_PROFILE_UNSUPPORTED",
              "method_profile_ref mismatch with Round binding.")
    if binding.get("profile_revision") != round_method_ref.get("revision"):
        _fail("METHOD_PROFILE_UNSUPPORTED",
              "method_profile_ref revision mismatch with Round binding.")
    return {
        "profile_id": str(binding["profile_id"]),
        "revision": str(binding["profile_revision"]),
        "canonical_hash": str(binding["profile_canonical_hash"]),
    }


# ---------- capacity aggregation (跨当前正式 Submission 集合) ----------

def _aggregate_capacity(execution, formal_submissions, round_period_id, participant_domains):
    from .a2_rounds import sources
    demands, bindings = {}, {}
    for sub in formal_submissions.values():
        payload = sub['payload']['submission']
        for resource in payload['resources']:
            key = tuple(resource[k] for k in ('resource_id', 'period_id', 'unit'))
            if key[1] != round_period_id: _fail('COMPOSITION_INPUT_CHANGED')
            value = _ensure_signed_int(resource['required'], field='required')
            demands[key] = demands.get(key, 0) + value
        for binding in payload['bindings']:
            if binding['relation_type'] != 'resource_capacity': _fail('COMPOSITION_NOT_READY')
            key = tuple(binding[k] for k in ('resource_id', 'period_id', 'unit'))
            if key[1] != round_period_id: _fail('COMPOSITION_INPUT_CHANGED')
            bindings.setdefault(key, []).append(binding['source_ref'])
    dependencies, conflicts = [], []
    for key in sorted(set(demands) | set(bindings)):
        refs = bindings.get(key, [])
        if not refs: _fail('COMPOSITION_INPUT_CHANGED')
        if any(ref != refs[0] for ref in refs): _fail('COMPOSITION_NOT_READY')
        ref = refs[0]
        sources(execution, [ref], participant_domains)
        obj, rev = execution.head(ref['object_id']), execution.revision(ref['object_id'], ref['revision_id'])
        payload = rev['payload']
        if obj['object_type'] != 'CapacityObservation' or tuple(payload[k] for k in ('resource_id','period_id','unit')) != key:
            _fail('COMPOSITION_INPUT_CHANGED')
        available = _ensure_signed_int(payload['available'], field='available')
        reserved = _ensure_signed_int(payload['reserved'], field='reserved')
        if reserved > available: _fail('COMPOSITION_NOT_READY')
        amount = available-reserved; required = demands.get(key,0)
        if required > amount: conflicts.append('Current total demand exceeds available capacity.')
        dependencies.append({'dependency_id': _dependency_id(key), 'relation_type':'resource_capacity', 'source_ref':ref,
            'constraint':{'kind':'capacity','resource_id':key[0],'period_id':key[1],'unit':key[2],
                          'required':required,'available':amount}})
    return sorted(dependencies,key=lambda d:d['dependency_id']), conflicts


# ---------- collect ----------

def collect(execution):
    from .a2_rounds import source_refs, sources
    round_id = execution.target['object_id'] if execution.kind == 'form_company_composition' else execution.target_revision['payload']['round_id']
    head = execution.add_dependency(round_id)
    definition = execution.revision(round_id, head['latest_revision_id'])['payload']
    domains = [m['domain_id'] for m in definition['members']]
    formal = _current_formal_submissions(execution, round_id, domains)
    refs = [definition['company_reference_ref']]
    for domain, sub in formal.items():
        execution.add_dependency(sub['submission_object_id'])
        refs.extend(source_refs(sub['payload']['submission']))
        for mission in sub['payload']['submission']['missions']:
            index = execution.conn.execute("""SELECT mission_object_id FROM gov_mission_index
                WHERE scope_id=%s AND round_object_id=%s AND domain_id=%s AND mission_key=%s""",
                (execution.ctx.scope_id, round_id, domain, mission['mission_key'])).fetchone()
            if index: execution.add_dependency(str(index['mission_object_id']))
    judgments = execution.params['judgments'] if execution.kind == 'form_company_composition' else execution.target_revision['payload']['judgments']
    for judgment in judgments.values(): refs.extend(judgment['evidence_refs'])
    # Prepare resolves visibility/CAS, including stale-but-visible revisions.
    # Business currentness and readiness are checked by form/confirm/activate.
    seen, active = set(), set()
    heights = {}
    def visit(ref, depth):
        oid, rid = ref['object_id'], ref['revision_id']
        execution.add_dependency(oid); rev = execution.revision(oid, rid)
        if oid in active or depth > CLOSURE_MAX_DEPTH: _fail('COMPOSITION_NOT_READY')
        if (oid,rid) in seen:
            if depth+heights[(oid,rid)]-1>CLOSURE_MAX_DEPTH: _fail('COMPOSITION_NOT_READY')
            return heights[(oid,rid)]
        seen.add((oid,rid))
        if len(seen)>CLOSURE_MAX_NODES: _fail('COMPOSITION_NOT_READY')
        active.add(oid)
        height = 1
        for child in source_refs(rev['payload']): height=max(height,1+visit(child,depth+1))
        active.remove(oid)
        heights[(oid,rid)] = height
        return height
    for ref in refs: visit(ref,1)
    return {'round_object_id':round_id, 'formal_submission_domains':sorted(formal)}


# ---------- form ----------

def form(execution: ActionExecution) -> dict[str, Any]:
    """form_company_composition 业务核心。

    规则（review 1/2/3/5/6/9/10）：
      - Round 必须 open（gov_objects.lifecycle_status，不靠 absent 状态列）；
      - 代次匹配（请求期望 == 当前 member_set_version / input_set_version）；
      - 派生当前成员集合 + 当前每域最新正式 Submission（强制完整，不接受子集）；
      - 现派生所有 designated live CEO + DRI 当前任职 + principal；
      - 全员跨域正式 Submission 汇总容量 + 派生 binding_dependencies；
      - 写入任何候选前，对 company_reference_ref + 全部正式 Submission
        source_refs + 四项 judgments evidence_refs 的并集走 a2_rounds.sources
        统一校验（类型/当前 effective/hash/DB 时钟有效期/TTL/对 company+全部
        成员域发布，闭包 64 节点/深度 8、DFS 活动栈判环）；
      - 4 项 judgments 静态校验 + 确定性容量冲突；
      - 发布 CompanyComposition（formed）+ 不可变 manifest revision（manifest_hash
        剔除自身）；非通过判断/容量冲突可落库作不可变审查候选，但 confirm/activate
        必拒；
      - 候选创建事件 detail 同时保存 manual unresolved_conflicts 与 deterministic
        hard_conflicts（不动不存在的状态列）；
      - 签名槽：1 company_decider + 每成员 DRI，全部不同自然人；
      - 派生使用 protocol.inherit_binding(...RoundID, registered_by=ctx.principal_id,
        receipt_id=action_id)，禁止通用 resolve_creation；
      - initial object_version / revision_version = 1；不必要不创建 v2；
      - immutable payload：manifest 不可变；后续签认/激活只动头部。
    """
    if execution.kind != "form_company_composition":
        _fail("INVALID_REQUEST", f"form() called with kind={execution.kind!r}", 422)
    params = execution.params
    round_id = _canonical_uuid(getattr(execution, "target", {})["object_id"])
    round_head = execution.head(round_id)
    if round_head["object_type"] != "FormationRound":
        _fail("STALE_DEPENDENCY")
    round_rev = execution.revision(round_id, round_head["latest_revision_id"])
    round_payload = round_rev["payload"]
    round_state = _round_state(execution, round_id)
    if round_state is None:
        _fail("NOT_FOUND", "Round state row missing.")
    if not _objects_status_matches(round_head, "open"):
        _fail("INVALID_STATE", "Round object lifecycle is not open.")

    expected_member_set = int(params["expected_member_set_version"])
    expected_input_set = int(params["expected_input_set_version"])
    if int(round_state["member_set_version"]) != expected_member_set:
        _fail("COMPOSITION_INPUT_CHANGED",
              f"member_set_version drift: expected={expected_member_set} "
              f"got={round_state['member_set_version']}.")
    if int(round_state["input_set_version"]) != expected_input_set:
        _fail("COMPOSITION_INPUT_CHANGED",
              f"input_set_version drift: expected={expected_input_set} "
              f"got={round_state['input_set_version']}.")

    # Live CEO + DRI human principals (cross-domain).
    ceo_assignment = _require_visible_assignment(
        execution, _canonical_uuid(round_payload["ceo_assignment_id"]),
        role="CEO", domain_id=round_payload["company_domain_id"])
    ceo_principal = _canonical_uuid(ceo_assignment["principal_id"])
    ceo_principal_id_from_def = _canonical_uuid(round_payload["ceo_principal_id"])
    if ceo_principal != ceo_principal_id_from_def:
        _fail("COMPOSITION_INPUT_CHANGED",
              "company_decider principal does not match Round definition.")

    members_payload = round_payload.get("members", []) or []
    member_domains = [_canonical_uuid(m["domain_id"]) for m in members_payload]
    formal = _current_formal_submissions(execution, round_id, member_domains)
    missing = [d for d in member_domains if d not in formal]
    if missing:
        _fail("COMPOSITION_INPUT_CHANGED",
              f"formal submissions missing for {missing}.")

    participants = [round_payload["company_domain_id"]] + member_domains

    # Unified source validation BEFORE any candidate write (review: judgment
    # evidence leak).  The union of the Round's company_reference_ref, every
    # formal submission's bound source refs, and all four judgments'
    # evidence_refs goes through the reviewed a2_rounds.sources walker:
    # typed A2 sources only (a private EvidenceAsset is rejected, never
    # auto-shared), exact revision currently effective, payload_hash match,
    # DB-clock validity, observed_at TTL freshness, and publication covering
    # the company plus ALL member domains.  The transitive closure is bounded
    # (<=64 nodes / depth <=8) with DFS active-stack cycle detection — a
    # repeated published node is a DAG edge, not a cycle.  Nothing is
    # silently filtered: any invalid input fails the form.
    from .a2_rounds import source_refs, sources
    company_ref = round_payload.get("company_reference_ref") or {}
    company_ref = {
        "object_id": _canonical_uuid(company_ref["object_id"]),
        "revision_id": _canonical_uuid(company_ref["revision_id"]),
        "payload_hash": str(company_ref["payload_hash"]),
    }
    source_ref_union: list[dict[str, Any]] = [company_ref]
    for sub in formal.values():
        source_ref_union.extend(source_refs(sub["payload"].get("submission") or {}))
    for name in ("coverage", "coherence", "feasibility", "tradeoff"):
        source_ref_union.extend(
            list(params["judgments"][name].get("evidence_refs") or []))
    sources(execution, source_ref_union, participants)

    # Aggregate capacity across the complete current formal set.
    binding_dependencies, hard_conflicts = _aggregate_capacity(
        execution, formal, _canonical_uuid(round_payload["period_id"]), participants)
    binding_dependencies.sort(key=lambda d: d["dependency_id"])

    # Live DRI assignments per member (cross-domain).
    live_dri: dict[str, dict[str, Any]] = {}
    for m in members_payload:
        dri_id = _canonical_uuid(m["dri_assignment_id"])
        dri = _require_visible_assignment(
            execution, dri_id, role="DOMAIN_DRI", domain_id=m["domain_id"])
        live_dri[_canonical_uuid(m["domain_id"])] = dri

    # Build manifest with EXACT 15 keys per CompositionManifest schema.
    judgments_input = params["judgments"]
    judgments: dict[str, dict[str, Any]] = {}
    for name in ("coverage", "coherence", "feasibility", "tradeoff"):
        j = judgments_input[name]
        judgments[name] = {
            "conclusion": str(j["conclusion"]),
            "reason": str(j["reason"]),
            "evidence_refs": list(j.get("evidence_refs") or []),
            "judge_principal_id": ceo_principal,
        }

    signers: list[dict[str, str]] = [{
        "principal_id": ceo_principal,
        "assignment_id": _canonical_uuid(round_payload["ceo_assignment_id"]),
        "responsibility_role": "company_decider",
    }]
    for m in members_payload:
        dri_principal = _canonical_uuid(live_dri[_canonical_uuid(m["domain_id"])]["principal_id"])
        if dri_principal == ceo_principal:
            _fail("COMPOSITION_INPUT_CHANGED",
                  "CEO principal must differ from every member DRI principal.")
        signers.append({
            "principal_id": dri_principal,
            "assignment_id": _canonical_uuid(m["dri_assignment_id"]),
            "responsibility_role": "area_accountable",
        })
    # distinct principals & assignments
    principals = [s["principal_id"] for s in signers]
    assignments = [s["assignment_id"] for s in signers]
    if len(set(principals)) != len(principals):
        _fail("COMPOSITION_INPUT_CHANGED", "duplicate principal in required_signers.")
    if len(set(assignments)) != len(assignments):
        _fail("COMPOSITION_INPUT_CHANGED", "duplicate assignment in required_signers.")
    signers.sort(key=lambda s: s["principal_id"])

    # members (sorted by domain_id) → include submission_ref.
    sorted_submissions = [formal[_canonical_uuid(m["domain_id"])] for m in
                          sorted(members_payload, key=lambda m: _canonical_uuid(m["domain_id"]))]
    comp_members: list[dict[str, Any]] = []
    for m, sub in zip(
        sorted(members_payload, key=lambda m: _canonical_uuid(m["domain_id"])),
        sorted_submissions,
    ):
        comp_members.append({
            "domain_id": _canonical_uuid(m["domain_id"]),
            "dri_assignment_id": _canonical_uuid(m["dri_assignment_id"]),
            "dri_principal_id": _canonical_uuid(m["dri_principal_id"]),
            "submission_ref": {
                "object_id": sub["submission_object_id"],
                "revision_id": sub["submission_revision_id"],
                "payload_hash": sub["payload_hash"],
            },
        })

    method_profile_ref = _resolve_method_profile_ref(
        execution, round_id, round_payload["method_profile_ref"])

    manifest_dict: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "scope_id": _canonical_uuid(execution.ctx.scope_id),
        "company_id": _canonical_uuid(round_payload["company_id"]),
        "round_id": round_id,
        "period_id": _canonical_uuid(round_payload["period_id"]),
        "method_profile_ref": method_profile_ref,
        "member_set_version": int(round_state["member_set_version"]),
        "input_set_version": int(round_state["input_set_version"]),
        "company_reference_ref": {
            "object_id": _canonical_uuid(company_ref["object_id"]),
            "revision_id": _canonical_uuid(company_ref["revision_id"]),
            "payload_hash": str(company_ref["payload_hash"]),
        },
        "members": comp_members,
        "binding_dependencies": binding_dependencies,
        "judgments": judgments,
        "required_signers": signers,
        "hash_scheme": "tkos-json-v1",
    }
    # hash excludes self only.
    manifest_dict["manifest_hash"] = compute_manifest_hash(
        {k: v for k, v in manifest_dict.items() if k != "manifest_hash"})

    # Validate static shape + hash; do not conflate static_conflict_reasons
    # with form denial — those are confirm/activate gates only.
    try:
        manifest = CompositionManifest.model_validate(manifest_dict)
        validate_manifest(manifest)
    except CompositionValidationError as exc:
        _fail(exc.code, str(exc))
    except (ValidationError, ValueError) as exc:
        _fail("INVALID_REQUEST", f"manifest validate failed: {exc}", 422)

    unresolved = list(params.get("unresolved_conflicts") or [])
    blocking_now = any(bool(c.get("blocking")) for c in unresolved)

    # Create CompanyComposition via inherit_binding from Round (NEVER resolve_creation).
    composition_object_id = str(uuid4())
    composition_domain_id = _canonical_uuid(round_payload["company_domain_id"])
    row = execution.conn.execute(
        """INSERT INTO gov_objects(object_id, scope_id, domain_id, object_type, lifecycle_status)
           VALUES (%s, %s, %s, 'CompanyComposition', 'formed') RETURNING *""",
        (composition_object_id, execution.ctx.scope_id, composition_domain_id),
    ).fetchone()
    comp_obj = db.jsonable(row)
    revision = execution.insert_revision(comp_obj, manifest_dict, version=1)
    protocol.inherit_binding(
        execution.conn, execution.ctx.scope_id,
        composition_object_id, round_id,
        registered_by=execution.ctx.principal_id, receipt_id=execution.action_id)
    comp_obj = db.jsonable(execution.conn.execute(
        """UPDATE gov_objects SET object_version=1, latest_revision_id=%s,
                                  effective_revision_id=%s,
                                  updated_at=clock_timestamp()
            WHERE scope_id=%s AND object_id=%s RETURNING *""",
        (revision["revision_id"], revision["revision_id"],
         execution.ctx.scope_id, composition_object_id),
    ).fetchone())
    execution.heads[composition_object_id] = comp_obj
    execution.changed[composition_object_id] = comp_obj

    # Persist manual + deterministic conflicts in immutable form event detail.
    execution.event(comp_obj, None, "form_company_composition",
                    detail={
                        "unresolved_conflicts": unresolved,
                        "deterministic_conflicts": hard_conflicts,
                        "blocking_at_form": blocking_now,
                        "manifest_hash": manifest_dict["manifest_hash"],
                        "member_set_version": manifest_dict["member_set_version"],
                        "input_set_version": manifest_dict["input_set_version"],
                    })

    static_reasons = static_conflict_reasons(manifest)
    readiness = {
        "judgments_all_pass": all(j["conclusion"] == "pass" for j in manifest_dict["judgments"].values()),
        "static_conflict_reasons": static_reasons,
        "hard_conflicts": hard_conflicts,
        "blocking_unresolved": bool(blocking_now),
    }
    return {
        "composition_object_id": composition_object_id,
        "composition_revision_id": revision["revision_id"],
        "manifest_hash": manifest_dict["manifest_hash"],
        "member_set_version": manifest_dict["member_set_version"],
        "input_set_version": manifest_dict["input_set_version"],
        "required_signers": signers,
        "binding_dependencies": binding_dependencies,
        "readiness": readiness,
        "effects": [],  # outer finish applies effects (none for form).
    }


# ---------- validate_current ----------

def validate_current(execution, *, require_ready=True):
    from .a2_rounds import source_refs, sources
    manifest = execution.target_revision['payload']; requested = execution.params['composition_ref']
    if requested != {'object_id':execution.target['object_id'], 'revision_id':execution.target_revision['revision_id'],
                     'manifest_hash':manifest['manifest_hash']}: _fail('COMPOSITION_INPUT_CHANGED')
    try: validate_manifest(CompositionManifest.model_validate(manifest))
    except (CompositionValidationError,ValidationError,ValueError): _fail('COMPOSITION_INPUT_CHANGED')
    rid = manifest['round_id']; head = execution.head(rid)
    definition = execution.revision(rid,head['latest_revision_id'])['payload']; current = _round_state(execution,rid)
    if current is None or head['lifecycle_status'] != 'open': _fail('INVALID_STATE')
    for key in ('member_set_version','input_set_version'):
        if manifest[key] != current[key]: _fail('COMPOSITION_INPUT_CHANGED')
        if key in execution.params and execution.params[key] != current[key]: _fail('COMPOSITION_INPUT_CHANGED')
        if 'expected_'+key in execution.params and execution.params['expected_'+key] != current[key]: _fail('COMPOSITION_INPUT_CHANGED')
    if (manifest['scope_id'],manifest['company_id'],manifest['period_id']) != (execution.ctx.scope_id,execution.ctx.company_id,definition['period_id']):
        _fail('COMPOSITION_INPUT_CHANGED')
    expected_members = sorted(definition['members'],key=lambda m:m['domain_id'])
    if [{k:m[k] for k in ('domain_id','dri_assignment_id','dri_principal_id')} for m in manifest['members']] != expected_members:
        _fail('COMPOSITION_INPUT_CHANGED')
    expected_signers = [{'principal_id':definition['ceo_principal_id'],'assignment_id':definition['ceo_assignment_id'],'responsibility_role':'company_decider'}]
    expected_signers += [{'principal_id':m['dri_principal_id'],'assignment_id':m['dri_assignment_id'],'responsibility_role':'area_accountable'} for m in expected_members]
    if manifest['required_signers'] != sorted(expected_signers,key=lambda s:s['principal_id']): _fail('COMPOSITION_INPUT_CHANGED')
    if manifest['method_profile_ref'] != definition['method_profile_ref'] or manifest['method_profile_ref'] != _resolve_method_profile_ref(execution,rid,definition['method_profile_ref']):
        _fail('COMPOSITION_INPUT_CHANGED')
    if manifest['company_reference_ref'] != definition['company_reference_ref']: _fail('COMPOSITION_INPUT_CHANGED')
    formal = _current_formal_submissions(execution,rid,[m['domain_id'] for m in expected_members])
    if len(formal)!=len(expected_members): _fail('COMPOSITION_INPUT_CHANGED')
    refs=[definition['company_reference_ref']]
    for member in manifest['members']:
        sub=formal[member['domain_id']]
        if member['submission_ref'] != {'object_id':sub['submission_object_id'],'revision_id':sub['submission_revision_id'],'payload_hash':sub['payload_hash']}:
            _fail('COMPOSITION_INPUT_CHANGED')
        refs.extend(source_refs(sub['payload']['submission']))
    for judgment in manifest['judgments'].values(): refs.extend(judgment['evidence_refs'])
    participants=[definition['company_domain_id'], *formal]
    sources(execution,refs,participants)
    dependencies,conflicts=_aggregate_capacity(execution,formal,definition['period_id'],participants)
    if dependencies!=manifest['binding_dependencies']: _fail('COMPOSITION_INPUT_CHANGED')
    event=execution.conn.execute("""SELECT detail FROM gov_lifecycle_events WHERE scope_id=%s AND object_id=%s
        AND event_type='form_company_composition' ORDER BY recorded_at LIMIT 1""",
        (execution.ctx.scope_id,execution.target['object_id'])).fetchone()
    if event is None: _fail('INVALID_STATE')
    detail=event['detail']
    if require_ready and (static_conflict_reasons(CompositionManifest.model_validate(manifest)) or conflicts
        or detail.get('deterministic_conflicts') or any(c['blocking'] for c in detail.get('unresolved_conflicts',[]))):
        _fail('COMPOSITION_NOT_READY')
    recheck_live(execution)
    return {'manifest':manifest,'round_object':head,'round_definition':definition,'state':current,'formal_submissions':formal}


# ---------- recheck_live ----------

def recheck_live(execution):
    from .a2_rounds import source_refs, sources
    manifest=execution.target_revision['payload']; rid=manifest['round_id']
    head=execution.head(rid); definition=execution.revision(rid,head['latest_revision_id'])['payload']
    for slot in manifest['required_signers']:
        row=_cross_domain_assignment(execution,slot['assignment_id'])
        role='CEO' if slot['responsibility_role']=='company_decider' else 'DOMAIN_DRI'
        domain=definition['company_domain_id'] if role=='CEO' else next(m['domain_id'] for m in manifest['members'] if m['dri_assignment_id']==slot['assignment_id'])
        if row is None or row['principal_type']!='human' or row['role']!=role or row['domain_id']!=domain or row['principal_id']!=slot['principal_id']:
            _fail('CONFIRMATION_INCOMPLETE')
        execution.required_assignments.add(slot['assignment_id'])
    refs=[manifest['company_reference_ref']]
    for member in manifest['members']:
        ref=member['submission_ref']; sub=execution.revision(ref['object_id'],ref['revision_id'])
        refs.extend(source_refs(sub['payload']['submission']))
    for judgment in manifest['judgments'].values(): refs.extend(judgment['evidence_refs'])
    sources(execution,refs,[definition['company_domain_id'], *[m['domain_id'] for m in manifest['members']]])
