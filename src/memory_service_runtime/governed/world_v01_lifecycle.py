"""tkos.world/0.1 的生命周期推导（契约第 10 节、ADR-0002）：事件列表 → 所处的段与推出它的事件 id。

纯函数，不连数据库；状态机取自 world 登记的 ``lifecycles``。每条输入事件带 event_id、kind、
action（产生它的动作）、phase、category、outcome、supersedes_event_id；验收事件另带
by_spine_parent_responsible（记录者是不是当时上一级对象的责任人）。按列表顺序（记录顺序）逐条处理：

- 与当前段匹配的登记转移推出新段；自环（例如草稿、已承诺时标核心战役）不换段，也不换推出它的事件。
- 没有匹配转移的事件不改变生命周期：成立或确认后重走承诺与确认、未承诺先确认、非门事件都如此。
- 撤回只对推出当前段的那条门事件、且由同一动作同一 phase 记时生效，生命周期回到它之前的那一段，
  这一段改由该撤回事件推出；因此此后改变过生命周期的事件之前的门事件、以及撤回事件本身都不能再撤回。
"""
from __future__ import annotations

from typing import Any

from . import world_v01_registry as world_registry


def _matches(transition: dict[str, Any], state: str, event: dict[str, Any], core_battle: bool) -> bool:
    if transition["from"] != state or transition["action"] != event["action"]:
        return False
    if any(transition[field] is not None and transition[field] != event[field]
           for field in ("phase", "category", "outcome")):
        return False
    guard = transition["guard"]
    return (guard is None or (guard == "core_battle" and core_battle) or (guard == "not_core_battle" and not core_battle)
            or (guard == "spine_parent_responsible" and event.get("by_spine_parent_responsible") is True))


def derive(object_type: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """返回 {status, display_name, event_id}；没有生命周期的类型（只有版本）返回 None。"""
    spec = world_registry.registry()["lifecycles"].get(object_type)
    if spec is None:
        return None
    created = next((event["event_id"] for event in events if event["kind"] == "object.created"), None)
    stages = [(spec["initial"], created)]  # 段的历史：(段, 推出它的事件 id)；撤回时回到上一段
    seen: dict[str, dict[str, Any]] = {}
    core_battle = False
    for event in events:
        seen[event["event_id"]] = event
        state, producer = stages[-1]
        if event["outcome"] == "withdrawn":
            original = seen.get(event["supersedes_event_id"])
            if (len(stages) > 1 and original is not None and original["event_id"] == producer
                    and original["outcome"] != "withdrawn"
                    and (original["action"], original["phase"]) == (event["action"], event["phase"])):
                stages.pop()
                stages[-1] = (stages[-1][0], event["event_id"])
            continue
        transition = next((item for item in spec["transitions"] if _matches(item, state, event, core_battle)), None)
        if event["kind"] == "core_battle.marked":
            core_battle = True
        if transition is not None and transition["to"] != state:
            stages.append((transition["to"], event["event_id"]))
    state, producer = stages[-1]
    names = {item["id"]: item["display_name"] for item in spec["states"]}
    return {"status": state, "display_name": names[state], "event_id": producer}
