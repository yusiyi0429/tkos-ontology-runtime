"""tkos.world/0.2 Strategy 的一轮（契约第 10.1、12 节，决 13，补 20、21）：谁被指定、谁的 Agreement 还算数。

Strategy 的生命周期段、开轮与写回由生命周期引擎按登记推导（world_v02_lifecycle）；引擎只按记录者类别与守卫事实判，
不知道本轮指定了谁。本模块按记录顺序重放 Strategy 自己的事件，算出当前的一轮，供服务判记录者类别 ``designated``
与守卫 ``round_complete``、``round_incomplete``，也供读投影给出进行中的一轮：

- 指定本轮责任人（``world_assign_strategy_round``）开新的一轮，事件 detail 列出被指定的人，已生效时另带候选；
  此前的 Agreement 随之作废（重新指定即开新一轮）。
- Agreement 记在当时的一轮里；被撤回的不再算。
- 确认（接受或退回）结束这一轮：接受时成为正式或写回，退回时本轮作废；撤回这条确认，这一轮连同它的 Agreement
  原样回来（撤回回到原事件之前）。
- 再确认只在本轮已补齐、不带候选时结束这一轮（结论为不改，补 21），其余情况不动这一轮，同引擎。

Agreement 所同意的内容：草稿时是记录时的最新修订，已生效时是本轮的指派钉住的修订与它带的候选。算数的 Agreement
是钉住当前内容的那些：草稿在补齐之前又被修订，钉住旧修订的不计；同一人对同一内容只记一条。本轮每位被指定的人都有
算数的 Agreement 时本轮补齐。
"""
from __future__ import annotations

from typing import Any

from . import db
from . import world_v02_registry as world_registry
from .world_v02_models import CONTRACT_VERSION

ASSIGN = "world_assign_strategy_round"
AGREE = "world_agree_strategy"
CONFIRM = "world_confirm_strategy"
RECONFIRM = "world_reconfirm_strategy"
AGREED = "agreed"  # 已达成判断：本轮补齐之后、CEO 确认或退回之前


def events(conn: Any, ctx: Any, object_id: str) -> list[dict[str, Any]]:
    """Strategy 自己的 0.2 事件（subject_refs 第一项是它），按记录顺序：产生它的动作、结果、撤回的原事件、detail、
    钉住的修订，以及记这一条的人（代记时是被代记的人）。"""
    return [db.jsonable(row) for row in conn.execute(
        """SELECT e.event_id, r.action_type AS action, e.outcome, e.supersedes_event_id, e.detail,
                  e.subject_refs->0 AS pinned, COALESCE(e.on_behalf_of, e.principal_id) AS person
             FROM gov_world_events e JOIN gov_action_receipts r ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id
            WHERE e.scope_id=%s AND e.contract_version=%s AND e.subject_refs->0->>'object_id'=%s
            ORDER BY e.recorded_at, e.event_id""", (ctx.scope_id, CONTRACT_VERSION, object_id)).fetchall()]


def _complete(current: dict[str, Any]) -> bool:
    return any(item["round_complete"] for item in current["agreements"])


def current_round(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按记录顺序重放，返回当前的一轮：{event_id（开轮的指派）, designated（被指定的人）, candidate（是否带候选）,
    pinned（指派钉住的修订）, agreements}；agreements 是这一轮里未撤回的 Agreement，按记录顺序，每项
    {event_id, principal_id, object_version, revision_id（所同意的内容）, round_complete（记下时是否补齐本轮）}。
    没有进行中的一轮时为 None。"""
    current: dict[str, Any] | None = None
    ended: dict[str, dict[str, Any] | None] = {}  # 结束一轮的确认 → 它结束的那一轮，撤回这条确认时原样回来
    for event in history:
        action, withdrawn = event["action"], event["outcome"] == "withdrawn"
        if action == ASSIGN:
            current = {"event_id": event["event_id"], "designated": list(event["detail"]["principal_ids"]),
                       "candidate": "candidate" in event["detail"],
                       "pinned": {key: event["pinned"][key] for key in ("object_version", "revision_id")},
                       "agreements": []}
        elif action == AGREE and current is not None:
            if withdrawn:
                current["agreements"] = [item for item in current["agreements"]
                                         if item["event_id"] != event["supersedes_event_id"]]
            elif event["detail"]["round_event_id"] == current["event_id"]:
                detail = event["detail"]
                current["agreements"].append({"event_id": event["event_id"], "principal_id": event["person"],
                                              "object_version": detail["object_version"],
                                              "revision_id": detail["revision_id"],
                                              "round_complete": detail["round_complete"]})
        elif action == CONFIRM and withdrawn:
            if event["supersedes_event_id"] in ended:
                current = ended.pop(event["supersedes_event_id"])
        elif action == CONFIRM or (action == RECONFIRM and current is not None and not current["candidate"]
                                   and _complete(current)):
            ended[event["event_id"]] = current
            current = None
    return current


def _content(current: dict[str, Any], status: str, latest: dict[str, Any]) -> dict[str, Any]:
    """这一轮的 Agreement 所同意的内容：草稿（初始段）时是当前最新修订，此后是本轮的指派钉住的修订（与它的候选）。"""
    initial = world_registry.registry()["lifecycles"]["Strategy"]["initial"]
    pinned = latest if status == initial else current["pinned"]
    return {"object_version": pinned["object_version"], "revision_id": pinned["revision_id"]}


def _counted(current: dict[str, Any], content: dict[str, Any]) -> set[str]:
    return {item["principal_id"] for item in current["agreements"] if item["revision_id"] == content["revision_id"]}


def admission(current: dict[str, Any] | None, status: str, latest: dict[str, Any], person: str) -> dict[str, Any]:
    """person 此刻记一条 Agreement：守卫事实（记下后本轮是否补齐）、是否重复（他已有钉住同一内容的 Agreement）与
    要写进事件 detail 的内容（本轮、所同意的修订与候选、是否补齐）。没有进行中的一轮时两条守卫都不成立。"""
    if current is None:
        return {"guards": {"round_complete": False, "round_incomplete": False}, "duplicate": False, "detail": None}
    content = _content(current, status, latest)
    counted = _counted(current, content)
    complete = set(current["designated"]) <= counted | {person}
    return {"guards": {"round_complete": complete, "round_incomplete": not complete}, "duplicate": person in counted,
            "detail": {"round_event_id": current["event_id"], **content,
                       "candidate_event_id": current["event_id"] if current["candidate"] else None,
                       "round_complete": complete}}


def pending(current: dict[str, Any] | None, status: str, latest: dict[str, Any]) -> list[str]:
    """本轮还没有算数的 Agreement 的被指定的人，按指定的顺序；已达成判断时为空。"""
    if current is None or status == AGREED:
        return []
    counted = _counted(current, _content(current, status, latest))
    return [person for person in current["designated"] if person not in counted]


def round_view(conn: Any, ctx: Any, head: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any] | None:
    """读投影 business.round 的 Strategy 形状（契约第 15.1 节）：草稿与已生效时都可以有进行中的一轮。同其他有门对象
    给开轮事件、所在段、候选，另给被指定的人、这一轮里未撤回的 Agreement 与还差谁。"""
    current = current_round(events(conn, ctx, head["object_id"]))
    if current is None:
        return None
    spec = world_registry.registry()["lifecycles"]["Strategy"]
    stage = derived["round"]["stage"] if derived["round"] else derived["status"]
    latest = db.jsonable(conn.execute(
        "SELECT object_version, revision_id FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
        (ctx.scope_id, head["latest_revision_id"])).fetchone())
    candidate = conn.execute(
        "SELECT detail->'candidate' AS candidate FROM gov_world_events WHERE scope_id=%s AND event_id=%s",
        (ctx.scope_id, current["event_id"])).fetchone()["candidate"] if current["candidate"] else None
    return {"opened_by_event_id": current["event_id"], "stage": stage,
            "display_name": next(item["display_name"] for item in spec["states"] if item["id"] == stage),
            "candidate_event_id": current["event_id"] if current["candidate"] else None,
            "candidate": candidate, "designated": current["designated"],
            "agreements": [{key: item[key] for key in ("event_id", "principal_id", "object_version", "revision_id")}
                           for item in current["agreements"]],
            "pending": pending(current, AGREED if stage == AGREED else derived["status"], latest)}
