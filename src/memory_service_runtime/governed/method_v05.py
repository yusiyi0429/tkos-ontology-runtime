"""tkos.method/0.5：本体 v0.7 对齐增量执行器。

0.4 模块保持冻结；本模块只对 0.5 有差异的动作在 ``COLLECTORS`` / ``RUNNERS`` 登记
自己的处理器，其余动作原样委托 ``method_v04``。所有校验仍在同一治理事务内进行，
不产生执行副作用。Task 5–9 往两张登记表里加条目。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from . import db, method_access as access, method_v04 as v4
from .errors import GovernedError
from .method_v04 import _agent, _current_ceo, _exact, _human_ceo, _phase, _same, _scope_definition, _source_refs
from .method_v05_models import ACTION_PARAMS, HUMAN_ACTIONS

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
    _mission_head, mission_revision = e.ref(applies["mission_ref"], types={"Mission"}, current=False)
    _pco_head, pco_revision = e.ref(mission_revision["payload"]["parent_pco_ref"], types={"PCO"}, current=False)
    # 解析用 Mission 自身的 primary_scope_id（而非其父级 PCO 的）：该字段才是"其主 Scope"，
    # PCO 只提供解析所需的确切 Architecture 基准。
    return v4._pco_dri(e, {"architecture_ref": pco_revision["payload"]["architecture_ref"],
                           "primary_scope_id": mission_revision["payload"]["primary_scope_id"]})


def _check_constraint_payload(e, payload):
    applies = payload["applies_to"]
    if applies["kind"] == "scope":
        _head, revision = e.ref(payload["architecture_ref"], types={"StrategicArchitecture"}, effective=True, current=False)
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


required_committers = v4._required_committers   # Task 8 换成 0.5 规则（只有 PCO 的 DRI）


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


def activation_blockers(conn, ctx, obj):
    return v4.activation_blockers(conn, ctx, obj, CONTRACT_VERSION)   # Task 8 换成 0.5 规则
