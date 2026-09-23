"""tkos.method/0.5：本体 v0.7 对齐增量执行器。

0.4 模块保持冻结；本模块只对 0.5 有差异的动作在 ``COLLECTORS`` / ``RUNNERS`` 登记
自己的处理器，其余动作原样委托 ``method_v04``。所有校验仍在同一治理事务内进行，
不产生执行副作用。Task 5–9 往两张登记表里加条目。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from . import db, method_access as access, method_v04 as v4, protocol
from .errors import GovernedError
from .method_v04 import _agent, _current_ceo, _exact, _human_ceo, _phase, _same, _scope_definition, _source_refs
from .method_v05_models import ACTION_PARAMS

CONTRACT_VERSION = "tkos.method/0.5"
COLLECTORS: dict = {}   # kind -> collect(e)
RUNNERS: dict = {}      # kind -> run(e)；由 run() 先调用 collect(e) 再执行

# 0.4 的 scoped 动作去掉已不存在的 method_confirm_state，加上按范围确认 / 修订的 Constraint。
V05_SCOPED_ACTIONS = (v4.V04_SCOPED_ACTIONS - {"method_confirm_state"}) | {"m1b_confirm_constraint", "m1b_revise_constraint"}


def fail(message: str, code: str = "INVALID_STATE") -> None:
    raise GovernedError(code, message)


def light(conn, ctx):
    """0.5 对象的只读责任解析；只接受 0.5 绑定。"""
    return v4._LightExecution(conn, ctx, CONTRACT_VERSION)


# -------------------------------------------------------------- dispatch


def collect(e):
    if e.kind not in ACTION_PARAMS:
        fail("Unsupported Method 0.5 action.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    handler = COLLECTORS.get(e.kind)
    if handler is not None:
        e._v04 = {}
        return handler(e)
    return v4.collect(e)


def run(e):
    if e.kind not in ACTION_PARAMS:
        fail("Unsupported Method 0.5 action.", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    runner = RUNNERS.get(e.kind)
    if runner is not None:
        collect(e)
        return runner(e)
    return v4.run(e)


# ------------------------------------------------------------- Constraint


def _constraint_responsibility(e, payload):
    """确认人：company → 当前 CEO；scope → 该 Scope 授权域的唯一当前 DRI；mission → 其主 Scope 的 DRI。"""
    applies = payload["applies_to"]
    if applies["kind"] == "company":
        return _current_ceo(e, e.domain_id if getattr(e, "domain_id", None) else e.target["domain_id"])
    if applies["kind"] == "scope":
        principal, _role, _auth_domain, assignment = v4._pco_responsibility(
            e, {"architecture_ref": payload["architecture_ref"], "primary_scope_id": applies["scope_id"]})
        return principal, assignment
    # 与 scope 分支同口径：Mission 与其父级 PCO 只是责任解析的基准，在服务端读取（同
    # constraint_assignment_static）。草稿期（尚无审视窗口）该 Mission 主 Scope 的 DRI 读不到它们，也不因此获得读取权。
    basis = light(e.conn, e.ctx)
    _mission_head, mission_revision = basis.ref(applies["mission_ref"], types={"Mission"})
    _pco_head, pco_revision = basis.ref(mission_revision["payload"]["parent_pco_ref"], types={"PCO"})
    # 解析用 Mission 自身的 primary_scope_id（而非其父级 PCO 的）：该字段才是"其主 Scope"，
    # PCO 只提供解析所需的确切 Architecture 基准。
    return v4._pco_dri(e, {"architecture_ref": pco_revision["payload"]["architecture_ref"],
                           "primary_scope_id": mission_revision["payload"]["primary_scope_id"]})


def _check_constraint_payload(e, payload):
    applies = payload["applies_to"]
    if applies["kind"] == "scope":
        # 与 v4._candidate_basis_current 同口径：Architecture 基准在服务端读取。范围责任人（只持有
        # 映射授权域任职的 DRI）按设计读不到公司域 Architecture，也不因此获得读取权。
        _head, revision = light(e.conn, e.ctx).ref(payload["architecture_ref"], types={"StrategicArchitecture"},
                                                   effective=True)
        _scope_definition(e, revision["payload"], applies["scope_id"])
    _source_refs(e, payload["evidence_refs"])


def _check_constraint_refs(e, refs, *, scope_id, mission_ref=None):
    """LTCO / PCO / Mission 只能引用已确认、且适用于公司或本对象主 Scope（或本 Mission）的 Constraint。"""
    for reference in refs:
        head, revision = e.ref(reference, types={"Constraint"}, effective=True, current=False)
        if e.state(head).get("phase") != "confirmed":
            fail("Only confirmed Constraints can be referenced.", "STALE_DEPENDENCY")
        applies = revision["payload"]["applies_to"]
        if applies["kind"] == "company":
            continue
        if applies["kind"] == "scope" and applies["scope_id"] == scope_id:
            continue
        if (applies["kind"] == "mission" and mission_ref is not None
                and applies["mission_ref"]["object_id"] == mission_ref["object_id"]):
            continue
        fail("A referenced Constraint must apply to the company or to this object's own Scope.", "INVALID_REQUEST")


def _constraint_actor(e, payload):
    owner, assignment = _constraint_responsibility(e, payload)
    if e.ctx.principal_type == "human":
        e.require_actor(owner, "human")
    else:
        _agent(e, "CO_AGENT")
    e._v04.update(constraint_owner=owner, constraint_assignment=assignment)


def _collect_record_constraint(e):
    payload = e.params["payload"]
    _check_constraint_payload(e, payload)
    _constraint_actor(e, payload)


def _collect_revise_constraint(e):
    _phase(e.state(e.target), "draft", "confirmed")
    payload = e.params["payload"]
    if payload["applies_to"] != e.target_revision["payload"]["applies_to"]:
        fail("A revision keeps the constraint's applicability; record a new Constraint instead.", "INVALID_REQUEST")
    _check_constraint_payload(e, payload)
    _constraint_actor(e, payload)


def _collect_confirm_constraint(e):
    _phase(e.state(e.target), "draft")
    payload = e.target_revision["payload"]
    _check_constraint_payload(e, payload)
    owner, assignment = _constraint_responsibility(e, payload)
    e.require_actor(owner, "human")
    e._v04.update(constraint_owner=owner, constraint_assignment=assignment)


def _run_record_constraint(e):
    head, revision = e.create("Constraint", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_constraint(e):
    head, revision = e.revise(e.target, e.params["payload"])
    state = deepcopy(e.state(head))
    state.update(phase="draft")
    e.set_state(head, state)
    return {**_exact(head, revision), "phase": "draft"}


def _run_confirm_constraint(e):
    record = e.review("constraint_confirmation", _exact(e.target, e.target_revision),
                      {"statement": e.params["statement"], "principal_id": e._v04["constraint_owner"],
                       "assignment_id": e._v04["constraint_assignment"]["assignment_id"]})
    state = deepcopy(e.state(e.target))
    state.update(phase="confirmed", confirmation_record_id=record)
    e.set_state(e.target, state)
    e.transition(e.target, status="confirmed", effective=True)
    return {**_exact(e.target, e.target_revision), "phase": "confirmed", "review_record_id": record}


COLLECTORS.update({"m1b_record_constraint": _collect_record_constraint,
                   "m1b_revise_constraint": _collect_revise_constraint,
                   "m1b_confirm_constraint": _collect_confirm_constraint})
RUNNERS.update({"m1b_record_constraint": _run_record_constraint,
                "m1b_revise_constraint": _run_revise_constraint,
                "m1b_confirm_constraint": _run_confirm_constraint})


# ------------------------------------------------------------------- LTCO


def _collect_ltco_draft(e):
    v4.collect(e)
    payload = e.params["payload"]
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])


def _collect_revise_ltco(e):
    """与 0.4 的修订校验逐条相同，只把阶段放宽到 draft | confirmed。

    §3 的 `revised`（对象已有正式版本，本次确认新草稿）要求能修订已确认的 LTCO；0.4 的
    修订只收 draft，而 0.4/0.5 没有任何动作把已确认 LTCO 挪回 draft，沿用它会让 `revised`
    永远不可达。修订产生新草稿版本，生效版本在 CEO 以 `revised` 确认前保持不变。
    """
    _agent(e, "CEO_AGENT")
    _phase(e.state(e.target), "draft", "confirmed")
    payload = e.params["payload"]
    v4._ltco_check(e, payload)
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])


def _collect_confirm_ltco(e):
    state = e.state(e.target)
    _phase(state, "draft", "confirmed")
    payload = e.target_revision["payload"]
    v4._ltco_check(e, payload)
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])
    owner, _assignment = _current_ceo(e, e.target["domain_id"])
    e.require_actor(owner, "human")
    conclusion = e.params["conclusion"]
    has_formal = e.target.get("effective_revision_id") is not None
    if state.get("phase") == "confirmed":
        if conclusion != "maintained" or not has_formal:
            fail("A confirmed LTCO can only be maintained; revise it for a new version.", "INVALID_REQUEST")
        if str(e.target["effective_revision_id"]) != str(e.target_revision["revision_id"]):
            fail("Maintain the exact effective LTCO version.", "STALE_DEPENDENCY")
        return
    expected = "revised" if has_formal else "established"
    if conclusion != expected:
        fail(f"This confirmation must conclude '{expected}'.", "INVALID_REQUEST")


def _run_propose_ltco(e):
    head, revision = e.create("LTCO", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_ltco(e):
    # 修订不是确认：state 重置为 draft，不沿用已确认状态里 0.4 遗留的 confirmation_record_id；
    # 但 last_review（若已存在）要保留——它是 §3 历次审视结论的记录，不应被一次修订抹去。
    head, revision = e.revise(e.target, e.params["payload"])
    old_state = e.state(head)
    state = {"phase": "draft"}
    if "last_review" in old_state:
        state["last_review"] = old_state["last_review"]
    e.set_state(head, state)
    record = e.review("ltco_revision_response", _exact(head, revision), {"response": e.params["response"]})
    return {**_exact(head, revision), "phase": "draft", "review_record_id": record}


def _run_confirm_ltco(e):
    conclusion = e.params["conclusion"]
    record = e.review("ltco_confirmation", _exact(e.target, e.target_revision),
                      {"conclusion": conclusion, "statement": e.params["statement"]})
    state = deepcopy(e.state(e.target))
    state.update(phase="confirmed", confirmation_record_id=record,
                 last_review={"conclusion": conclusion, "record_id": record})
    e.set_state(e.target, state)
    if conclusion == "maintained":
        e.transition(e.target)   # 只推进 CAS；正式版本不变
    else:
        e.transition(e.target, status="confirmed", effective=True)
    return {**_exact(e.target, e.target_revision), "phase": "confirmed", "conclusion": conclusion,
            "review_record_id": record}


COLLECTORS.update({"m1b_propose_ltco": _collect_ltco_draft, "m1b_revise_ltco": _collect_revise_ltco,
                   "m1b_confirm_ltco": _collect_confirm_ltco})
RUNNERS.update({"m1b_propose_ltco": _run_propose_ltco, "m1b_revise_ltco": _run_revise_ltco,
                "m1b_confirm_ltco": _run_confirm_ltco})


# ---------------------------------------------------------- Period Review


def _collect_confirm_review(e):
    _human_ceo(e)
    _phase(e.state(e.target), "generated")
    for reference in e.target_revision["payload"]["state_refs"]:
        v4._canonical_state(e, reference)


def _run_generate_review(e):
    # 0.5：不像 0.4 那样传 status="recorded"——create() 会把 recorded 判定为立即生效
    # （method_service.py ~277），在 CEO 确认之前就生效，违反契约 §4「确认后 effective，
    # 生效只来自确认」。省略 status 落默认的 draft，不产生 effective_revision_id。
    head, revision = e.create("PeriodReview", e.params["payload"])
    e.set_state(head, {"phase": "generated"})
    return {**_exact(head, revision), "nature": "agent_analysis", "phase": "generated"}


def _run_confirm_review(e):
    head, revision = e.target, e.target_revision
    generated = _exact(head, revision)
    overrides = {key: e.params[key] for key in ("findings", "learnings", "implications") if e.params.get(key) is not None}
    if overrides:
        head, revision = e.revise(head, {**revision["payload"], **overrides})
    record = e.review("review_confirmation", _exact(head, revision),
                      {"statement": e.params["statement"], "overrides": sorted(overrides)})
    e.transition(head, status="confirmed", effective=True)
    e.set_state(head, {"phase": "confirmed", "confirmation_record_id": record, "agent_generation_ref": generated})
    return {**_exact(head, revision), "phase": "confirmed", "review_record_id": record}


def _run_regenerate_review(e):
    # 0.5：重新生成不再使复盘生效——不传 status/effective，沿用 e.target 现有生命周期与
    # effective_revision_id（生效只来自 m1b_confirm_review）。已确认版本的 Agent 起草溯源
    # （state.agent_generation_ref，契约 §4）若存在则原样保留；其余状态字段（如已失效的
    # confirmation_record_id）重置。
    head, revision = e.revise(e.target, e.params["payload"])
    old_state = e.state(head)
    state = {"phase": "generated"}
    if "agent_generation_ref" in old_state:
        state["agent_generation_ref"] = old_state["agent_generation_ref"]
    e.set_state(head, state)
    return {**_exact(head, revision), "nature": "agent_analysis", "phase": "generated"}


# -------------------------------------------------------------------- PCO


def _check_period_review_ref(e, payload):
    start = datetime.fromisoformat(payload["period"]["start"])
    reference = payload.get("period_review_ref")
    if reference is not None:
        head, revision = e.ref(reference, types={"PeriodReview"}, effective=True, current=False)
        if e.state(head).get("phase") != "confirmed":
            fail("PCO must cite a CEO-confirmed Period Review.", "STALE_DEPENDENCY")
        if datetime.fromisoformat(revision["payload"]["period"]["end"]) > start:
            fail("The cited Period Review must precede the PCO period.", "INVALID_REQUEST")
        return
    rows = e.conn.execute(
        """SELECT o.object_id, r.payload FROM gov_objects o JOIN gov_object_revisions r
             ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
           WHERE o.scope_id=%s AND o.object_type='PeriodReview' AND o.effective_revision_id IS NOT NULL""",
        (e.ctx.scope_id,)).fetchall()
    for row in db.jsonable(rows):
        if datetime.fromisoformat(row["payload"]["period"]["end"]) > start:
            continue
        # 只统计当前绑定仍是 0.5 的 PeriodReview——原地升级不迁移旧绑定，0.4 生成即生效、无需确认，计入会致死锁且违反 §1「不就地重解释历史」。
        binding = protocol.current_binding(e.conn, e.ctx.scope_id, str(row["object_id"]))
        if binding is not None and binding["contract_version"] == CONTRACT_VERSION:
            fail("A confirmed Period Review exists for an earlier period; the PCO must cite it.", "INVALID_REQUEST")


def _check_pco_extras(e, payload):
    _check_period_review_ref(e, payload)
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"])


def _collect_pco_draft(e):
    v4.collect(e)
    _check_pco_extras(e, e.params["payload"])


def _collect_resolve_window(e):
    v4.collect(e)
    for candidate in e.params["pcos"]:
        frozen_payload = e._m1b["pcos"][str(candidate["object_id"])]["revision"]["payload"]
        candidate_ref = candidate["payload"].get("period_review_ref")
        frozen_ref = frozen_payload.get("period_review_ref")
        if not ((candidate_ref is None and frozen_ref is None) or _same(candidate_ref, frozen_ref)):
            fail("A candidate PCO must retain its drafted Period Review reference.", "STALE_DEPENDENCY")
        _check_pco_extras(e, candidate["payload"])


def _run_draft_pco(e):
    head, revision = e.create("PCO", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_pco(e):
    head, revision = e.revise(e.target, e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


COLLECTORS.update({"m1b_confirm_review": _collect_confirm_review,
                   "m1b_generate_review": lambda e: v4.collect(e),
                   "m1b_regenerate_review": lambda e: v4.collect(e),
                   "m1b_draft_pco": _collect_pco_draft, "m1b_revise_pco": _collect_pco_draft,
                   "m1b_resolve_window": _collect_resolve_window})
RUNNERS.update({"m1b_confirm_review": _run_confirm_review,
                "m1b_generate_review": _run_generate_review,
                "m1b_regenerate_review": _run_regenerate_review,
                "m1b_draft_pco": _run_draft_pco, "m1b_revise_pco": _run_revise_pco,
                "m1b_resolve_window": v4._resolve_window})


# ---------------------------------------------------------------- Mission


def _check_mission_extras(e, payload, *, architecture_ref, mission_ref=None):
    _head, revision = e.ref(architecture_ref, types={"StrategicArchitecture"}, current=False)
    units = {d["unit_id"] for d in [*revision["payload"]["battlefields"], *revision["payload"]["domains"]]}
    for unit in payload["contributes_to_scope_ids"]:
        if unit == payload["primary_scope_id"] or unit not in units:
            fail("Contributions name other Scopes of the exact Architecture.", "INVALID_REQUEST")
    start = datetime.fromisoformat(payload["period"]["start"])
    end = datetime.fromisoformat(payload["period"]["end"])
    for dependency in payload["dependencies"]:
        needed = datetime.fromisoformat(dependency["needed_by"])
        if not (start <= needed <= end):
            fail("A dependency's needed_by must fall inside the Mission period.", "INVALID_REQUEST")
        if dependency["kind"] == "mission":
            e.ref(dependency["mission_ref"], types={"Mission"}, current=False)
        elif dependency["scope_id"] not in units:
            fail("A Scope dependency must name a unit of the exact Architecture.", "INVALID_REQUEST")
    _check_constraint_refs(e, payload["constraint_refs"], scope_id=payload["primary_scope_id"], mission_ref=mission_ref)


def _collect_mission_draft(e):
    v4.collect(e)
    payload = e.params["payload"]
    _pco_head, pco_revision = e.ref(payload["parent_pco_ref"], types={"PCO"}, current=False)
    mission_ref = _exact(e.target, e.target_revision) if getattr(e, "target", None) else None
    _check_mission_extras(e, payload, architecture_ref=pco_revision["payload"]["architecture_ref"], mission_ref=mission_ref)


def _run_draft_mission(e):
    head, revision = e.create("Mission", e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _run_revise_mission(e):
    head, revision = e.revise(e.target, e.params["payload"])
    e.set_state(head, {"phase": "draft"})
    return {**_exact(head, revision), "phase": "draft"}


def _collect_resolve_window_missions(e):
    _collect_resolve_window(e)
    window = e.target_revision["payload"]
    frozen = e._m1b["missions"]
    for candidate in e.params["missions"]:
        head = frozen[str(candidate["object_id"])]["head"]
        revision = frozen[str(candidate["object_id"])]["revision"]
        v4._mission_owner(e, candidate)   # 候选的 Owner 可能与冻结版本不同，需按候选值重新校验
        _check_mission_extras(e, candidate, architecture_ref=window["architecture_ref"],
                              mission_ref=_exact(head, revision))


# ----------------------------------------------------- commitment / activation


def _required_committers(e, payload):
    """0.5：只有 PCO 责任需要承诺，由本域 DRI 做；Mission 由其 PCO 的承诺覆盖。"""
    required = {}
    for reference in payload["target_refs"]:
        e.ref(reference, types={"PCO", "Mission"}, current=True)
        member = e.head(reference["object_id"])
        if member["object_type"] != "PCO":
            continue
        revision = e.revision(reference["object_id"], reference["revision_id"])
        owner, role, domain_id, _assignment = v4._pco_responsibility(e, revision["payload"])
        required[str(reference["object_id"])] = {"reference": reference, "owner": owner,
                                                 "role": role, "domain_id": domain_id}
    return required


required_committers = _required_committers


def _collect_commit_candidate(e):
    if e.ctx.principal_type != "human":
        fail("Only the responsibility Scope DRI can commit.", "FORBIDDEN")
    state = _phase(e.state(e.target), "pending")
    payload = e.target_revision["payload"]
    v4._candidate_basis_current(e, payload)
    reference = e.params["responsibility_ref"]
    if not any(_same(reference, ref) for ref in payload["target_refs"]):
        fail("The commitment must name one exact candidate responsibility.", "INVALID_REQUEST")
    for ref in payload["target_refs"]:
        e.ref(ref, types={"PCO", "Mission"}, current=True)
    member = e.head(reference["object_id"])
    if member["object_type"] != "PCO":
        fail("0.5 commitments are made per responsibility Scope on its PCO; Missions are covered by their PCO.",
             "INVALID_REQUEST")
    revision = e.revision(reference["object_id"], reference["revision_id"])
    owner, expected_role, expected_domain, _assignment = v4._pco_responsibility(e, revision["payload"])
    if owner != e.ctx.principal_id:
        fail("A responsibility commitment can only be published by the Scope's own DRI.", "FORBIDDEN")
    assignment = None
    for row in db._assignments(e.conn, e.ctx):
        if row["principal_id"] != owner or row["role"] != expected_role or str(row["domain_id"]) != str(expected_domain):
            continue
        try:
            assignment = e.validate_assignment(row["assignment_id"], owner, "human")
            break
        except GovernedError:
            continue
    if assignment is None:
        fail("The DRI has no current assignment for this responsibility.", "FORBIDDEN")
    e._v04.update(commitment_owner=owner, commitment_assignment=assignment, candidate_state=state)


def _activation_findings(reader, conn, ctx, payload, required, candidate_ref):
    """m1b_activate_candidates 的有序 (code, message, blocker_code) 发现列表，供收集器的写路径与
    只读投影 activation_blockers 共用，避免规则变更只改到其中一处。覆盖：成员状态、每个候选 Mission
    的 Owner（§5 去掉的是 Owner 的承诺，不是 Owner 的有效性）、以及每个必需 PCO 的承诺。不判定候选
    集自身的 phase、基准是否过期（stale_basis）或 unresolved_differences（critical_difference）——
    这两项仍由两个调用方各自保留在原有位置。
    """
    findings = []
    for reference in payload["target_refs"]:
        member = reader.head(reference["object_id"])
        member_state = reader.state(member)
        if member_state.get("phase") != "candidate" or not _same(member_state.get("candidate_ref"), candidate_ref):
            findings.append(("STALE_DEPENDENCY", "The candidate set is no longer the authoritative member state.",
                             "member_state_changed"))
            break
    for reference in payload["target_refs"]:
        member = reader.head(reference["object_id"])
        if member["object_type"] != "Mission":
            continue
        revision = reader.revision(reference["object_id"], reference["revision_id"])
        try:
            v4._mission_owner(reader, revision["payload"])
        except GovernedError:
            # 重开只会冻结同一候选，再次收拢仍按同一 Owner 校验（FORBIDDEN）；唯一出路是恢复其任职。
            findings.append(("INVALID_STATE",
                             "A candidate Mission's Owner is no longer a current principal; "
                             "restore the Owner's appointment before activation.",
                             "mission_owner_invalid"))
    rows = conn.execute(
        """SELECT responsibility_object_id, responsibility_revision_id, principal_id, assignment_id
           FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s""",
        (ctx.scope_id, candidate_ref["revision_id"])).fetchall()
    committed = {str(r["responsibility_object_id"]): db.jsonable(r) for r in db.jsonable(rows)}
    for oid, spec in required.items():
        recorded = committed.get(oid)
        if recorded is None or str(recorded["principal_id"]) != str(spec["owner"]):
            findings.append(("INVALID_STATE",
                             "Every responsibility Scope DRI must commit the exact candidate set before activation.",
                             "missing_commitment"))
            continue
        if str(recorded["responsibility_revision_id"]) != str(spec["reference"]["revision_id"]):
            findings.append(("INVALID_STATE", "A recorded commitment names a different responsibility revision.",
                             "member_state_changed"))
            continue
        try:
            assignment = access.assignment(conn, ctx, str(recorded["assignment_id"]), spec["owner"], "human")
        except GovernedError:
            findings.append(("INVALID_STATE",
                             "A recorded commitment assignment is no longer current; explicit recommit is required.",
                             "commitment_assignment_revoked"))
            continue
        if assignment["role"] != spec["role"] or str(assignment["domain_id"]) != str(spec["domain_id"]):
            findings.append(("INVALID_STATE", "A recorded commitment assignment no longer matches the named responsibility.",
                             "commitment_assignment_mismatch"))
    return findings, committed


def _collect_activate_candidates(e):
    _human_ceo(e)
    state = _phase(e.state(e.target), "pending")
    payload = e.target_revision["payload"]
    v4._candidate_basis_current(e, payload)
    required = _required_committers(e, payload)
    candidate_ref = _exact(e.target, e.target_revision)
    findings, committed = _activation_findings(e, e.conn, e.ctx, payload, required, candidate_ref)
    if findings:
        code, message, _blocker = findings[0]
        fail(message, code)
    if [d for d in payload.get("unresolved_differences", []) if d["critical"]]:
        fail("A critical unresolved difference blocks activation.", "INVALID_STATE")
    window_head, window_revision = e.ref(payload["window_ref"], types={"ReviewWindow"}, current=False)
    if e.state(window_head).get("phase") != "resolved":
        fail("The reviewed window is no longer in its resolved state.", "INVALID_STATE")
    for oid, spec in required.items():
        recorded = committed[oid]
        e.validate_assignment(str(recorded["assignment_id"]), spec["owner"], "human")
    e._v04["activation"] = {"state": state, "payload": payload, "window_head": window_head,
                            "window_revision": window_revision, "required": required}


def _run_activate_candidates(e):
    result = v4._activate_candidates(e)
    for reference in e._v04["activation"]["payload"]["target_refs"]:
        member = e.head(reference["object_id"])
        if member["object_type"] != "Mission":
            continue
        state = deepcopy(e.state(member))
        state["owner_activation_record_id"] = result["review_record_id"]
        e.set_state(member, state)
    return result


def activation_blockers(conn, ctx, obj):
    """0.5 工作台投影：与 0.4 同结构，只是承诺人规则换成 PCO 的 DRI，并新增候选 Mission 的 Owner 校验。"""
    payload = (obj.get("latest_revision") or {}).get("payload") or {}
    context = light(conn, ctx)
    blockers = []
    try:
        v4._candidate_basis_current(context, payload)
    except GovernedError:
        blockers.append("stale_basis")
    if any(item.get("critical") for item in payload.get("unresolved_differences", [])):
        blockers.append("critical_difference")
    try:
        required = _required_committers(context, payload)
    except GovernedError:
        blockers.append("missing_commitment")
        return blockers
    try:
        candidate_head = context.head(obj["object_id"])
        candidate_revision = context.revision(obj["object_id"], obj["latest_revision"]["revision_id"])
        findings, _committed = _activation_findings(context, conn, ctx, payload, required,
                                                     _exact(candidate_head, candidate_revision))
        blockers.extend(finding[2] for finding in findings)
    except (GovernedError, KeyError):
        blockers.append("member_state_changed")
    return blockers


COLLECTORS.update({"m1b_draft_mission": _collect_mission_draft, "m1b_revise_mission": _collect_mission_draft,
                   "m1b_resolve_window": _collect_resolve_window_missions,
                   "m1b_commit_candidate": _collect_commit_candidate,
                   "m1b_activate_candidates": _collect_activate_candidates})
RUNNERS.update({"m1b_draft_mission": _run_draft_mission, "m1b_revise_mission": _run_revise_mission,
                "m1b_commit_candidate": v4._commit_candidate,
                "m1b_activate_candidates": _run_activate_candidates})


# ------------------------------------------------------ Operating State


def _collect_propose_state(e):
    v4._collect_propose_state(e)     # 责任解析、基线、证据、previous_state_ref 沿用 0.4
    payload = e.params["payload"]
    for reference in payload["drilldown_refs"]:
        head, revision = e.ref(reference, types={"OperatingState"}, effective=True, current=False)
        if not _same(e.state(head).get("canonical_ref"), reference):
            fail("Drill-down references must cite canonical States.", "STALE_DEPENDENCY")
        # 主体身份是对象（§6、v4._state_key），不是确切版本：同一主体较早版本的 State 也不能下钻。
        if str(revision["payload"]["subject_ref"]["object_id"]) == str(payload["subject_ref"]["object_id"]):
            fail("A State cannot drill down into its own subject.", "INVALID_REQUEST")
    previous = e.params.get("previous_state_ref")
    if previous:
        _head, prev = e.ref(previous, types={"OperatingState"})
        if prev["payload"]["period"] != payload["period"]:
            fail("A new generation must preserve the State identity and period.", "VERSION_CONFLICT")
    else:
        key = v4._state_key(payload)
        row = e.conn.execute(
            "SELECT state_id FROM gov_method_state_keys WHERE scope_id=%s AND subject_id=%s AND outcome_id=%s AND as_of=%s",
            (e.ctx.scope_id, *key)).fetchone()
        if row:
            fail("This subject and as-of already have a State; regenerate with previous_state_ref.", "VERSION_CONFLICT")


def _run_propose_state(e):
    payload = e.params["payload"]
    previous = e.params.get("previous_state_ref")
    if previous:
        head, _old = e.ref(previous, types={"OperatingState"})
        head, revision = e.revise(head, payload, status="active", effective=True)
    else:
        # T9-a：create() 传 status="active" 时，effective_revision_id 直接落在这唯一一个
        # 版本上（method_service.py ~277："status in {active,confirmed,recorded,stored}"
        # 才判定 effective），首次生成不需要再调用 e.transition(head, effective=True)——
        # 那只会对同一个治理动作多打一次 object_version、多记一条同类型事件。再生成走
        # revise(..., status="active", effective=True)，一次调用同样只产生一条事件，两条
        # 分支因此都恰好一次 object_version 变化、一条事件。
        head, revision = e.create("OperatingState", payload, status="active")
        e.conn.execute(
            "INSERT INTO gov_method_state_keys(scope_id,subject_id,outcome_id,as_of,state_id) VALUES(%s,%s,%s,%s,%s)",
            (e.ctx.scope_id, *v4._state_key(payload), head["object_id"]))
    reference = _exact(head, revision)
    state = deepcopy(e.state(head))
    state.update(phase="recorded", canonical_ref=reference, recommendation_ref=reference,
                 generated_by=e.ctx.principal_id)
    e.set_state(head, state)
    return {**reference, "phase": "recorded",
            "nature": "agent_analysis" if e.ctx.principal_type == "agent" else "owner_statement"}


COLLECTORS["method_propose_state"] = _collect_propose_state
RUNNERS["method_propose_state"] = _run_propose_state


def constraint_assignment_static(conn, ctx, payload):
    """只读解析 Constraint 范围责任人，供 scoped 授权回退与工作台可用性投影使用。"""
    context = light(conn, ctx)
    applies = payload["applies_to"]
    if applies["kind"] == "company":
        rows = conn.execute(
            "SELECT assignment_id, principal_id, domain_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND role='CEO'",
            (ctx.scope_id, ctx.principal_id)).fetchall()
        for row in db.jsonable(rows):
            try:
                return access.assignment(conn, ctx, str(row["assignment_id"]), ctx.principal_id, "human")
            except GovernedError:
                continue
        raise GovernedError("FORBIDDEN")
    if applies["kind"] == "scope":
        basis = {"architecture_ref": payload["architecture_ref"], "primary_scope_id": applies["scope_id"]}
    else:
        _head, mission = context.ref(applies["mission_ref"], types={"Mission"})
        _pco_head, pco = context.ref(mission["payload"]["parent_pco_ref"], types={"PCO"})
        # 同上：取 Mission 自身的 primary_scope_id，而非其父级 PCO 的。
        basis = {"architecture_ref": pco["payload"]["architecture_ref"], "primary_scope_id": mission["payload"]["primary_scope_id"]}
    principal, _role, _auth_domain, assignment = v4._pco_responsibility(context, basis)
    if principal != ctx.principal_id or ctx.principal_type != "human":
        raise GovernedError("FORBIDDEN")
    return assignment


# ------------------------------------------------------- scoped authority


def scoped_assignment(conn, ctx, kind, target, revision):
    """0.5 scoped 动作的本人责任；对 0.4 语义不变的动作委托 0.4 并带上 0.5 版本。"""
    payload = revision["payload"]
    if kind in {"m1b_confirm_constraint", "m1b_revise_constraint"}:
        return constraint_assignment_static(conn, ctx, payload)
    if kind == "m1b_commit_candidate":
        for reference in payload["target_refs"]:
            head = db.jsonable(conn.execute("SELECT object_type FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                                            (ctx.scope_id, reference["object_id"])).fetchone())
            if head is None or head["object_type"] != "PCO":
                continue
            owner = v4._commitment_owner(conn, ctx, reference, CONTRACT_VERSION)
            if owner is None:
                continue
            for row in db._assignments(conn, ctx):
                if row["principal_id"] == owner:
                    try:
                        return access.assignment(conn, ctx, row["assignment_id"], owner, "human")
                    except GovernedError:
                        continue
        raise GovernedError("FORBIDDEN")
    return v4.scoped_assignment(conn, ctx, kind, target, revision, CONTRACT_VERSION)


def state_subject_owner_static(conn, ctx, subject):
    return v4._state_subject_owner_static(conn, ctx, subject, CONTRACT_VERSION)
