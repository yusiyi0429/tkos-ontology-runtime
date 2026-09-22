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


# ------------------------------------------------------- scoped authority


required_committers = v4._required_committers   # Task 8 换成 0.5 规则（只有 PCO 的 DRI）


def constraint_assignment_static(conn, ctx, payload):
    """Constraint 范围责任人的只读解析（Task 5 实现）。"""
    raise GovernedError("FORBIDDEN")


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
