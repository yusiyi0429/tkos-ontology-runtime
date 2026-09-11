"""A3 DRI–IC 执行交接治理：ExecutionCommitment 生命周期、A2 基线与独立授权门。

本模块承载 A3（Contract-A tkos.contract-a/0.1，docs/runtime-a3-engineering.md
§1–§6 冻结）下 ExecutionCommitment 草案/改版/签收/激活的业务语义，以及供
a3_delivery / a3_outcome 复用的准入助手。所有数据库写、提交、scope 栅栏与
回执由调用方（service.ActionExecution 事务）拥有；本模块不出 commit、不新建
连接、不派生独立事务。

关键不变量（与契约包 A §5 及工程文档 §1–§6 对齐）：

  - A2 基线核对针对「已激活组合」：以 gov_formation_round_state 已激活指针、
    gov_activation_records 与 DomainCommitment 存储引用为准，绝不调用
    a2_composition.validate_current（它面向未激活候选）。
  - ExecutionAuthority 与 AcceptanceAppointment 是相互独立的门：独立代次
    （execution_epoch / appointment_version）、独立时钟窗口；当前指针与代次
    存于 gov_execution_state（激活前为 0，首次激活置 1）。评审只核对任命，
    不核对执行授权——已提交交付在 IC 被撤销/授权到期后仍可被评审。
  - 内部跨域核对（组合 manifest / 正式 Submission / Round 定义）一律走
    scope 内 raw SQL，不冒名任何 actor、不登记依赖、不回显隐藏对象 ID；
    失败只暴露 STALE_DEPENDENCY。
  - Mission / DomainCommitment 是 IC 本域可见对象，经 ex.add_dependency 正常
    登记为 CAS 依赖（可见引用使用真实 actor 读权限）。
"""
from __future__ import annotations

from typing import Any
from datetime import datetime
from uuid import uuid4

from . import a2_rounds, db, protocol
from .a2_models import A2_SOURCE_TYPES, CLOSURE_MAX_DEPTH, CLOSURE_MAX_NODES
from .a3_models import A3ExecutionCommitmentPayload
from .errors import GovernedError


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


# ---------------------------------------------------------------------------
# raw scoped reads（内部核对专用：不登记依赖、不进入回执引用、不冒名 actor）
# ---------------------------------------------------------------------------


def raw_object(ex: Any, object_id: Any) -> dict[str, Any] | None:
    row = ex.conn.execute(
        "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
        (ex.ctx.scope_id, str(object_id)),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def raw_revision(ex: Any, object_id: Any, revision_id: Any) -> dict[str, Any] | None:
    row = ex.conn.execute(
        "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
        (ex.ctx.scope_id, str(object_id), str(revision_id)),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def raw_assignment(ex: Any, assignment_id: Any) -> dict[str, Any] | None:
    """Scoped assignment+principal row WITHOUT any currency requirement.

    Used only to derive stored principal identities (e.g. the reviewer
    exclusion set).  Admission checks that require a CURRENT assignment must
    use ``ex.assignment`` instead.
    """
    row = ex.conn.execute(
        """SELECT a.assignment_id, a.scope_id, a.domain_id, a.principal_id, a.role,
                  a.active, a.valid_from, a.valid_to,
                  p.principal_type, p.active AS principal_active
             FROM gov_role_assignments a JOIN gov_principals p
               ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
            WHERE a.scope_id=%s AND a.assignment_id=%s""",
        (ex.ctx.scope_id, str(assignment_id)),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


# ---------------------------------------------------------------------------
# A3 ExecutionCommitment payload
# ---------------------------------------------------------------------------


def ec_payload(ex: Any) -> dict[str, Any]:
    """The target revision's payload strictly revalidated as an A3 commitment."""
    try:
        model = A3ExecutionCommitmentPayload.model_validate(ex.target_revision["payload"])
    except Exception:
        _fail("INVALID_REQUEST", "The commitment revision does not carry A3 execution content.", 422)
    return model.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Mission / DomainCommitment（IC 本域可见对象：真实读权限 + CAS 依赖登记）
# ---------------------------------------------------------------------------


def load_mission(ex: Any, ref: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    obj = ex.add_dependency(str(ref["object_id"]))
    ex.same_domain(obj)
    rev = ex.revision(obj["object_id"], str(ref["revision_id"]))
    if obj["object_type"] != "Mission":
        _fail("INVALID_REQUEST", "mission_ref must reference a Mission revision.", 422)
    if rev["payload_hash"] != ref["payload_hash"]:
        _fail("STALE_DEPENDENCY", "The Mission reference no longer matches the stored revision.")
    if obj["effective_revision_id"] != rev["revision_id"] or obj["lifecycle_status"] != "active":
        _fail("STALE_DEPENDENCY", "The referenced Mission is not the currently active revision.")
    return obj, rev


def load_domain_commitment(ex: Any, ref: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    obj = ex.add_dependency(str(ref["object_id"]))
    ex.same_domain(obj)
    rev = ex.revision(obj["object_id"], str(ref["revision_id"]))
    if obj["object_type"] != "DomainCommitment":
        _fail("INVALID_REQUEST", "domain_commitment_ref must reference a DomainCommitment revision.", 422)
    if rev["payload_hash"] != ref["payload_hash"]:
        _fail("STALE_DEPENDENCY", "The DomainCommitment reference no longer matches the stored revision.")
    if obj["effective_revision_id"] != rev["revision_id"] or obj["lifecycle_status"] != "active":
        _fail("STALE_DEPENDENCY", "The referenced DomainCommitment is not the currently active revision.")
    return obj, rev


# ---------------------------------------------------------------------------
# A2 已激活基线（stored active pointers；绝不调用 validate_current）
# ---------------------------------------------------------------------------


def _criteria_key(criteria: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((str(c.get("criterion_id")), str(c.get("description"))) for c in criteria)


def check_execution_baseline(ex: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from .a3_baseline import active_composition
    mission_obj, mission_rev = load_mission(ex, payload["mission_ref"])
    dc_obj, dc_rev = load_domain_commitment(ex, payload["domain_commitment_ref"])
    mission, dc = mission_rev["payload"], dc_rev["payload"]
    baseline = active_composition(ex, dc["round_object_id"], dc["composition_ref"])
    domain = str(ex.domain_id)
    if mission["round_object_id"] != dc["round_object_id"] or mission["domain_id"] != domain or dc["domain_id"] != domain:
        _fail("STALE_DEPENDENCY")
    formal = baseline["formal_submissions"].get(domain)
    if formal is None:
        _fail("STALE_DEPENDENCY")
    sub_ref = {k: formal[k] for k in ("object_id", "revision_id", "payload_hash")}
    if mission["origin_submission_ref"] != sub_ref or dc["submission_ref"] != sub_ref:
        _fail("STALE_DEPENDENCY")
    mission_ref = {"mission_key": mission["mission_key"], "object_id": mission_obj["object_id"],
                   "revision_id": mission_rev["revision_id"]}
    detail = baseline["activation"]["detail"]
    if (mission_ref not in dc["mission_refs"]
            or dict(mission_ref, domain_id=domain) not in detail["missions"]
            or {"domain_id": domain, "object_id": dc_obj["object_id"], "revision_id": dc_rev["revision_id"]}
                not in detail["domain_commitments"]):
        _fail("STALE_DEPENDENCY")
    definition = next((m for m in formal["payload"]["submission"]["missions"]
                       if m["mission_key"] == mission["mission_key"]), None)
    if definition is None or any(mission.get(k) != v for k, v in definition.items()):
        _fail("STALE_DEPENDENCY")
    member = next(m for m in baseline["manifest"]["members"] if m["domain_id"] == domain)
    if payload["dri_assignment_id"] != member["dri_assignment_id"]:
        _fail("FORBIDDEN", "The DRI must be this active company member's designated DRI.")
    what = payload["what"]
    if (what["result_statement"] != mission["result_statement"] or what["boundary"] != mission["boundary"]
            or _criteria_key(what["acceptance_criteria"]) != _criteria_key(mission["acceptance_criteria"])):
        _fail("INVALID_REQUEST", "The What must preserve the Mission result, boundary and criteria.", 422)
    exact = lambda refs: sorted((r["object_id"], r["revision_id"], r["payload_hash"]) for r in refs)
    if exact(what["external_dependency_refs"]) != exact(mission["dependency_refs"]):
        _fail("INVALID_REQUEST", "The What must preserve all formal external dependencies.", 422)
    window = baseline["definition"]["period_window"]
    deadline = datetime.fromisoformat(what["hard_deadline"])
    if not datetime.fromisoformat(window["start"]) < deadline <= datetime.fromisoformat(window["end"]):
        _fail("INVALID_REQUEST", "The execution deadline must lie inside the Round period.", 422)
    baseline.update(mission_obj=mission_obj, mission_rev=mission_rev, dc_obj=dc_obj, dc_rev=dc_rev)
    return baseline


# ---------------------------------------------------------------------------
# 上游来源当前性/新鲜度（raw closure；不冒名、不泄露）
# ---------------------------------------------------------------------------


def check_upstream_currency(ex: Any, baseline: dict[str, Any]) -> None:
    """Re-validate the adopted composition's full source closure by DB clock.

    Mirrors a2_rounds.sources (type, effective revision, payload hash,
    shared_with_domain_ids coverage, validity window, observed_at TTL,
    64-node/depth-8 closure, cycle detection) but reads exclusively through
    scope-scoped raw SQL: company-domain manifest/submission contents are
    governing facts the IC cannot see, so they are never registered as
    dependencies and never echoed.  Every failure is STALE_DEPENDENCY.
    """
    definition = baseline["definition"]
    manifest = baseline["manifest"]
    shared = {str(definition["company_domain_id"])}
    shared.update(str(m["domain_id"]) for m in definition.get("members", []))

    refs = baseline["source_refs"]

    seen: set[tuple[str, str]] = set()
    active: set[str] = set()
    heights: dict[tuple[str, str], int] = {}

    def visit(ref: dict[str, Any], depth: int) -> int:
        oid, rid = str(ref["object_id"]), str(ref["revision_id"])
        if oid in active or depth > CLOSURE_MAX_DEPTH:
            _fail("STALE_DEPENDENCY", "A source dependency cycle or depth limit was reached.")
        head = raw_object(ex, oid)
        rev = raw_revision(ex, oid, rid)
        if head is None or rev is None:
            _fail("STALE_DEPENDENCY", "A governing source is unavailable.")
        if head["object_type"] not in A2_SOURCE_TYPES:
            _fail("STALE_DEPENDENCY", "A source dependency is not a published source object.")
        if str(head["effective_revision_id"] or "") != rid:
            _fail("STALE_DEPENDENCY", "A governing source is no longer the effective revision.")
        if (ref.get("payload_hash") or rev["payload_hash"]) != rev["payload_hash"]:
            _fail("STALE_DEPENDENCY", "A governing source hash no longer matches.")
        if not shared.issubset({str(d) for d in rev["payload"].get("shared_with_domain_ids", [])}):
            _fail("STALE_DEPENDENCY", "A governing source is no longer shared with the composition domains.")
        live = ex.conn.execute(
            """SELECT valid_from<=clock_timestamp()
                    AND (valid_to IS NULL OR clock_timestamp()<valid_to)
                    AND (NOT payload ? 'observed_at'
                         OR ((payload->>'observed_at')::timestamptz<=clock_timestamp()
                             AND clock_timestamp()-(payload->>'observed_at')::timestamptz
                                 <= interval '86400 seconds')) AS valid
               FROM gov_object_revisions
               WHERE scope_id=%s AND object_id=%s AND revision_id=%s""",
            (ex.ctx.scope_id, oid, rid),
        ).fetchone()
        if live is None or not live["valid"]:
            _fail("STALE_DEPENDENCY", "A governing source is outside its validity or freshness window.")
        key = (oid, rid)
        if key in seen:
            if depth + heights[key] - 1 > CLOSURE_MAX_DEPTH:
                _fail("STALE_DEPENDENCY", "A source dependency depth limit was reached.")
            return heights[key]
        seen.add(key)
        if len(seen) > CLOSURE_MAX_NODES:
            _fail("STALE_DEPENDENCY", "A source dependency node limit was reached.")
        active.add(oid)
        height = 1
        for child in a2_rounds.source_refs(rev["payload"]):
            height = max(height, 1 + visit(child, depth + 1))
        active.remove(oid)
        heights[key] = height
        return height

    for ref in refs:
        visit(ref, 1)


# ---------------------------------------------------------------------------
# 三方当事人与独立授权门
# ---------------------------------------------------------------------------


def validate_ec_parties(ex: Any, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """DRI / IC / acceptor must be three CURRENTLY valid human assignments in
    the action domain, held by three distinct principals."""
    parties: dict[str, dict[str, Any]] = {}
    principals: set[str] = set()
    for key, field in (("dri", "dri_assignment_id"), ("ic", "ic_assignment_id"),
                       ("acceptor", "acceptor_assignment_id")):
        row = ex.assignment(str(payload[field]))  # currently valid human row
        if (row.get("principal_type") != "human" or not row.get("principal_active", True)
                or row["role"] != {"dri": "DOMAIN_DRI", "ic": "IC", "acceptor": "VERIFIER"}[key]):
            _fail("FORBIDDEN", "Commitment parties must be humans.")
        if ex.domain_id is not None and str(row["domain_id"]) != str(ex.domain_id):
            _fail("FORBIDDEN", "A commitment party assignment belongs to another domain.")
        if str(row["principal_id"]) in principals:
            _fail("FORBIDDEN", "DRI, IC and acceptor must be three distinct people.")
        principals.add(str(row["principal_id"]))
        parties[key] = row
        ex.required_assignments.add(str(row["assignment_id"]))
    return parties


def party_principals(ex: Any, payload: dict[str, Any]) -> dict[str, str]:
    """Stored party principal ids WITHOUT any currency requirement (raw read).

    Used by the review path to exclude DRI/IC/trusted authors even after an
    assignment expired: the exclusion is identity-based, not currency-based.
    """
    result: dict[str, str] = {}
    for key, field in (("dri", "dri_assignment_id"), ("ic", "ic_assignment_id"),
                       ("acceptor", "acceptor_assignment_id")):
        row = raw_assignment(ex, payload[field])
        if row is None:
            _fail("INVALID_STATE", "A recorded commitment party assignment is missing.")
        result[key] = str(row["principal_id"])
    return result


def current_execution_state(ex: Any, commitment_object_id: Any) -> dict[str, Any] | None:
    row = ex.conn.execute(
        "SELECT * FROM gov_execution_state WHERE scope_id=%s AND commitment_object_id=%s",
        (ex.ctx.scope_id, str(commitment_object_id)),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def require_current_authority(ex: Any, commitment_object_id: Any,
                              authority_id: Any, epoch: Any) -> dict[str, Any]:
    """The request's (authority_id, execution_epoch) must BE the matched
    current generation in gov_execution_state, the authority record must bind
    this exact commitment+epoch, its independent window must contain the DB
    clock, and the recorded IC assignment must be currently valid for the
    recorded IC principal.  No reassignment path exists; a stale generation
    or expired window rejects with STALE_DEPENDENCY.
    """
    state = current_execution_state(ex, commitment_object_id)
    if (state is None
            or str(state.get("current_authority_id") or "") != str(authority_id)
            or int(state.get("current_execution_epoch") or 0) != int(epoch)):
        _fail("STALE_DEPENDENCY", "The execution authority is not the current generation.")
    row = ex.conn.execute(
        """SELECT *, (valid_from<=clock_timestamp() AND clock_timestamp()<valid_to)
                  AS window_current, clock_timestamp()>=valid_to AS window_expired
             FROM gov_execution_authorities WHERE scope_id=%s AND authority_id=%s""",
        (ex.ctx.scope_id, str(authority_id)),
    ).fetchone()
    row = db.jsonable(row) if row is not None else None
    if (row is None
            or str(row["commitment_object_id"]) != str(commitment_object_id)
            or int(row["execution_epoch"]) != int(epoch)):
        _fail("STALE_DEPENDENCY", "The execution authority does not bind this commitment generation.")
    if not row["window_current"]:
        _fail("EXECUTION_AUTHORITY_EXPIRED" if row["window_expired"] else "FORBIDDEN")
    assignment = ex.assignment(str(row["ic_assignment_id"]))
    if str(assignment["principal_id"]) != str(row["ic_principal_id"]):
        _fail("FORBIDDEN", "The recorded IC identity no longer matches its assignment.")
    ex.required_assignments.add(str(row["ic_assignment_id"]))
    return row


def require_current_appointment(ex: Any, commitment_object_id: Any,
                                appointment_id: Any, version: Any, *,
                                expected_mission_object_id: Any = None) -> dict[str, Any]:
    """Appointment gate, independent of the execution authority: the request's
    (appointment_id, appointment_version) must BE the current generation in
    gov_execution_state, the appointment record must bind this exact
    commitment (and, when given, the exact governing Mission — a same-person
    appointment from another period never authorizes this WorkItem), its
    independent acceptance window must contain the DB clock, and the recorded
    acceptor assignment must be currently valid for the recorded principal.
    """
    state = current_execution_state(ex, commitment_object_id)
    if (state is None
            or str(state.get("current_appointment_id") or "") != str(appointment_id)
            or int(state.get("current_appointment_version") or 0) != int(version)):
        _fail("STALE_DEPENDENCY", "The acceptance appointment is not the current generation.")
    row = ex.conn.execute(
        """SELECT *, (valid_from<=clock_timestamp() AND clock_timestamp()<valid_to)
                  AS window_current, clock_timestamp()>=valid_to AS window_expired
             FROM gov_acceptance_appointments WHERE scope_id=%s AND appointment_id=%s""",
        (ex.ctx.scope_id, str(appointment_id)),
    ).fetchone()
    row = db.jsonable(row) if row is not None else None
    if (row is None
            or str(row["commitment_object_id"]) != str(commitment_object_id)
            or int(row["appointment_version"]) != int(version)):
        _fail("STALE_DEPENDENCY", "The acceptance appointment does not bind this commitment generation.")
    if (expected_mission_object_id is not None
            and str(row["mission_object_id"]) != str(expected_mission_object_id)):
        _fail("STALE_DEPENDENCY", "The acceptance appointment governs a different Mission.")
    if not row["window_current"]:
        _fail("ACCEPTANCE_APPOINTMENT_EXPIRED" if row["window_expired"] else "FORBIDDEN")
    assignment = ex.assignment(str(row["acceptor_assignment_id"]))
    if str(assignment["principal_id"]) != str(row["acceptor_principal_id"]):
        _fail("FORBIDDEN", "The recorded acceptor identity no longer matches its assignment.")
    ex.required_assignments.add(str(row["acceptor_assignment_id"]))
    return row


# ---------------------------------------------------------------------------
# collect_*：准入验证（prepare 与 execute 共用同一代码路径；只读，登记依赖）
# ---------------------------------------------------------------------------


def collect_create_commitment(ex: Any) -> dict[str, Any]:
    payload = ex.payload  # already strictly validated as A3 EC by checked_payload
    baseline = check_execution_baseline(ex, payload)
    check_upstream_currency(ex, baseline)
    parties = validate_ec_parties(ex, payload)
    if str(ex.ctx.principal_id) != str(parties["dri"]["principal_id"]):
        _fail("FORBIDDEN", "Only the designated DRI can draft the execution commitment.")
    ex.a3_context = {"payload": payload, "baseline": baseline, "parties": parties,
                     "recheck_baseline": True}
    return ex.a3_context


def collect_propose_commitment(ex: Any) -> dict[str, Any]:
    if ex.params.get("bundle_id"):
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    if ex.target["lifecycle_status"] != "offered" or ex.target["effective_revision_id"] is not None:
        _fail("INVALID_STATE", "Only an unactivated execution commitment draft can be revised.")
    context = collect_create_commitment(ex)
    context["commitment_object_id"] = ex.target["object_id"]
    return context


def collect_accept_commitment(ex: Any) -> dict[str, Any]:
    if ex.target["lifecycle_status"] != "offered":
        _fail("INVALID_STATE", "Only an offered execution commitment draft can be accepted.")
    payload = ec_payload(ex)
    baseline = check_execution_baseline(ex, payload)
    check_upstream_currency(ex, baseline)
    parties = validate_ec_parties(ex, payload)
    party = next((parties[k] for k in ("dri", "ic")
                  if parties[k]["assignment_id"] == ex.params["party_assignment_id"]), None)
    if party is None or party["principal_id"] != ex.ctx.principal_id:
        _fail("FORBIDDEN", "Only the assigned person can sign their own party slot.")
    if ex.params["accepted_terms_hash"] != ex.target_revision["payload_hash"]:
        _fail("INVALID_STATE", "The signed terms hash must match the exact revision.")
    ex.a3_context = {"payload": payload, "baseline": baseline, "parties": parties,
                     "commitment_object_id": ex.target["object_id"],
                     "recheck_baseline": True}
    return ex.a3_context


def _signed_parties(ex: Any, parties: dict[str, dict[str, Any]],
                    revision: dict[str, Any]) -> list[dict[str, Any]]:
    """Exactly the DRI and IC assignments must have signed THIS revision with
    the matching terms hash; the acceptor never signs the What handshake."""
    rows = ex.conn.execute(
        "SELECT * FROM gov_handshakes WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
        (ex.ctx.scope_id, ex.target["object_id"], revision["revision_id"]),
    ).fetchall()
    signatures = db.jsonable(rows)
    by_assignment = {str(row["assignment_id"]): row for row in signatures}
    expected = {str(parties["dri"]["assignment_id"]), str(parties["ic"]["assignment_id"])}
    if set(by_assignment) != expected:
        _fail("INVALID_STATE", "Both required same-revision signatures are missing.")
    for key in ("dri", "ic"):
        party = parties[key]
        signature = by_assignment[str(party["assignment_id"])]
        if (str(signature["principal_id"]) != str(party["principal_id"])
                or signature["terms_hash"] != revision["payload_hash"]):
            _fail("INVALID_STATE", "Signature does not bind the required person and terms.")
        ex.required_assignments.add(str(party["assignment_id"]))
    return signatures


def collect_activate_commitment(ex: Any) -> dict[str, Any]:
    if ex.target["lifecycle_status"] != "offered" or ex.target["effective_revision_id"] is not None:
        _fail("INVALID_STATE", "Only an unactivated offered commitment can activate.")
    payload = ec_payload(ex)
    baseline = check_execution_baseline(ex, payload)
    check_upstream_currency(ex, baseline)
    parties = validate_ec_parties(ex, payload)
    signatures = _signed_parties(ex, parties, ex.target_revision)
    policy = ex.current_policy()
    if parties["dri"]["principal_id"] != ex.ctx.principal_id:
        _fail("FORBIDDEN", "Only the designated DRI can release execution authority.")
    if policy["policy_revision_id"] != ex.params["activation_policy_revision_id"]:
        _fail("STALE_DEPENDENCY", "Activation policy has changed.")
    if {r["handshake_id"] for r in signatures} != set(ex.params["handshake_record_ids"]):
        _fail("INVALID_STATE", "Activation must supply exactly the two current signatures.")
    ex.a3_context = {"payload": payload, "baseline": baseline, "parties": parties,
                     "signatures": signatures, "policy": policy,
                     "commitment_object_id": ex.target["object_id"],
                     "recheck_baseline": True}
    return ex.a3_context


# ---------------------------------------------------------------------------
# 写路径（在同一事务内重跑同一准入路径后落库）
# ---------------------------------------------------------------------------


def create_commitment(ex: Any) -> dict[str, Any]:
    context = collect_create_commitment(ex)
    payload = context["payload"]
    object_id = str(uuid4())
    obj = db.jsonable(ex.conn.execute(
        """INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)
           VALUES (%s,%s,%s,'ExecutionCommitment','offered') RETURNING *""",
        (object_id, ex.ctx.scope_id, ex.domain_id),
    ).fetchone())
    revision = ex.insert_revision(obj, payload, version=1)
    obj = db.jsonable(ex.conn.execute(
        """UPDATE gov_objects SET object_version=1, latest_revision_id=%s,
                                  effective_revision_id=NULL, updated_at=clock_timestamp()
           WHERE scope_id=%s AND object_id=%s RETURNING *""",
        (revision["revision_id"], ex.ctx.scope_id, object_id),
    ).fetchone())
    protocol.insert_binding(ex.conn, ex.ctx.scope_id, object_id, ex.creation_fields,
                            registered_by=ex.ctx.principal_id, receipt_id=ex.action_id)
    ex.heads[object_id] = ex.changed[object_id] = obj
    context["commitment_object_id"] = object_id
    ex.event(obj, None, "create_object")
    return {"object_id": object_id, "revision_id": revision["revision_id"]}


def propose_commitment_revision(ex: Any) -> dict[str, Any]:
    context = collect_propose_commitment(ex)
    revision = ex.insert_revision(ex.target, context["payload"],
                                  version=ex.target["object_version"] + 1)
    obj = ex.bump(ex.target, latest=revision["revision_id"],
                  detail={"supersedes_content_revision_id": ex.target["latest_revision_id"]})
    return {"object_id": obj["object_id"], "revision_id": revision["revision_id"]}


def accept_commitment(ex: Any) -> dict[str, Any]:
    context = collect_accept_commitment(ex)
    payload, parties = context["payload"], context["parties"]
    revision = ex.target_revision
    requested = str(ex.params["party_assignment_id"])
    party = next((parties[key] for key in ("dri", "ic")
                  if str(parties[key]["assignment_id"]) == requested), None)
    if party is None or str(party["principal_id"]) != str(ex.ctx.principal_id):
        _fail("FORBIDDEN", "A caller can accept only its own DRI or IC party slot.")
    if revision["payload_hash"] != ex.params["accepted_terms_hash"]:
        _fail("INVALID_STATE", "Accepted terms hash differs from the immutable revision.")
    ex.required_assignments.add(str(party["assignment_id"]))
    existing = ex.conn.execute(
        """SELECT * FROM gov_handshakes
           WHERE scope_id=%s AND object_id=%s AND revision_id=%s AND assignment_id=%s""",
        (ex.ctx.scope_id, ex.target["object_id"], revision["revision_id"],
         party["assignment_id"]),
    ).fetchone()
    if existing is not None:
        return {"object_id": ex.target["object_id"], "revision_id": revision["revision_id"],
                "handshake_id": str(existing["handshake_id"]), "already_accepted": True}
    handshake_id = str(uuid4())
    ex.conn.execute(
        """INSERT INTO gov_handshakes
           (handshake_id,scope_id,object_id,revision_id,assignment_id,principal_id,
            terms_hash,understanding,action_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (handshake_id, ex.ctx.scope_id, ex.target["object_id"], revision["revision_id"],
         party["assignment_id"], ex.ctx.principal_id, revision["payload_hash"],
         ex.params["understanding"], ex.action_id),
    )
    ex.bump(ex.target, detail={"handshake_id": handshake_id,
                               "accepted_revision_id": revision["revision_id"]})
    return {"object_id": ex.target["object_id"], "revision_id": revision["revision_id"],
            "handshake_id": handshake_id}


def activate_commitment(ex: Any) -> dict[str, Any]:
    """Activate: release ExecutionAuthority epoch 1 to the exact IC and appoint
    AcceptanceAppointment version 1 to the exact acceptor — two INDEPENDENT
    gates with independent generations and windows, then move both current
    pointers in gov_execution_state.  No external effect is ever emitted.
    """
    context = collect_activate_commitment(ex)
    payload, parties = context["payload"], context["parties"]
    policy, signatures = context["policy"], context["signatures"]
    if str(policy["policy_revision_id"]) != str(ex.params["activation_policy_revision_id"]):
        _fail("STALE_DEPENDENCY", "Activation policy has changed.")
    if {str(r["handshake_id"]) for r in signatures} != {
            str(v) for v in ex.params["handshake_record_ids"]}:
        _fail("INVALID_STATE", "Activation must provide the exact required handshake records.")

    revision = ex.target_revision
    commitment_object_id = ex.target["object_id"]
    authority_id = str(uuid4())
    appointment_id = str(uuid4())
    appointed_by = parties["dri"]
    ex.conn.execute(
        """INSERT INTO gov_execution_authorities
           (authority_id,scope_id,commitment_object_id,commitment_revision_id,
            execution_epoch,ic_assignment_id,ic_principal_id,
            released_by_assignment_id,released_by_principal_id,
            valid_from,valid_to,action_id)
           VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s)""",
        (authority_id, ex.ctx.scope_id, commitment_object_id, revision["revision_id"],
         payload["ic_assignment_id"], parties["ic"]["principal_id"],
         payload["dri_assignment_id"], parties["dri"]["principal_id"],
         payload["execution_window"]["valid_from"], payload["execution_window"]["valid_to"],
         ex.action_id),
    )
    ex.conn.execute(
        """INSERT INTO gov_acceptance_appointments
           (appointment_id,scope_id,commitment_object_id,commitment_revision_id,
            appointment_version,acceptor_assignment_id,acceptor_principal_id,
            mission_object_id,standards_revision_id,valid_from,valid_to,
            appointed_by_assignment_id,action_id)
           VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (appointment_id, ex.ctx.scope_id, commitment_object_id, revision["revision_id"],
         payload["acceptor_assignment_id"], parties["acceptor"]["principal_id"],
         payload["mission_ref"]["object_id"], payload["mission_ref"]["revision_id"],
         payload["acceptance_window"]["valid_from"], payload["acceptance_window"]["valid_to"],
         appointed_by["assignment_id"], ex.action_id),
    )
    ex.conn.execute(
        """INSERT INTO gov_execution_state
           (commitment_object_id,scope_id,current_authority_id,current_execution_epoch,
            current_appointment_id,current_appointment_version)
           VALUES (%s,%s,%s,1,%s,1)
           ON CONFLICT (commitment_object_id) DO UPDATE SET
               current_authority_id=EXCLUDED.current_authority_id,
               current_execution_epoch=EXCLUDED.current_execution_epoch,
               current_appointment_id=EXCLUDED.current_appointment_id,
               current_appointment_version=EXCLUDED.current_appointment_version,
               updated_at=clock_timestamp()""",
        (commitment_object_id, ex.ctx.scope_id, authority_id, appointment_id),
    )
    context["authority"] = {"authority_id": authority_id, "execution_epoch": 1}
    context["appointment"] = {"appointment_id": appointment_id, "appointment_version": 1}
    obj = ex.bump(ex.target, status="active", effective=revision["revision_id"],
                  detail={"authority_id": authority_id, "appointment_id": appointment_id,
                          "execution_epoch": 1, "appointment_version": 1,
                          "policy_revision_id": str(policy["policy_revision_id"])})
    return {"object_id": obj["object_id"], "revision_id": obj["effective_revision_id"],
            "execution_authority_id": authority_id, "appointment_id": appointment_id,
            "execution_epoch": 1, "appointment_version": 1}


# ---------------------------------------------------------------------------
# before_business_commit 之后的最终复核（DB 时钟；自然到期不可被栅栏阻挡）
# ---------------------------------------------------------------------------


def recheck_final(ex: Any) -> None:
    """Re-verify the admitted dependencies/generations after the
    before_business_commit checkpoint: authority/appointment generation
    pointers and windows by DB clock for the action's commitment, and — when
    the action was admitted on the A2 baseline — the full baseline and
    upstream currency once more."""
    context = getattr(ex, "a3_context", None)
    if not context:
        return
    commitment_object_id = context.get("commitment_object_id")
    authority = context.get("authority")
    if authority and commitment_object_id:
        require_current_authority(ex, commitment_object_id,
                                  authority["authority_id"], authority["execution_epoch"])
    appointment = context.get("appointment")
    if appointment and commitment_object_id:
        require_current_appointment(ex, commitment_object_id,
                                    appointment["appointment_id"],
                                    appointment["appointment_version"])
    if context.get("recheck_baseline") and context.get("payload") is not None:
        baseline = check_execution_baseline(ex, context["payload"])
        check_upstream_currency(ex, baseline)
    if context.get("outcome"):
        from . import a3_outcome
        a3_outcome.recheck_final(ex)


__all__ = [
    "raw_object", "raw_revision", "raw_assignment",
    "ec_payload", "load_mission", "load_domain_commitment",
    "check_execution_baseline", "check_upstream_currency",
    "validate_ec_parties", "party_principals",
    "current_execution_state", "require_current_authority",
    "require_current_appointment",
    "collect_create_commitment", "collect_propose_commitment",
    "collect_accept_commitment", "collect_activate_commitment",
    "create_commitment", "propose_commitment_revision",
    "accept_commitment", "activate_commitment", "recheck_final",
]
