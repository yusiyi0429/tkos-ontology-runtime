"""tkos.world/0.1 的生命周期推导与门准入（契约第 10、11 节、ADR-0002）：事件列表 → 所处的段与推出它的事件 id。

纯函数，不连数据库；状态机取自 world 登记的 ``lifecycles``。每条输入事件带 event_id、kind、
action（产生它的动作）、phase、category、outcome、supersedes_event_id；验收事件另带
by_spine_parent_responsible（记录者是不是当时上一级对象的责任人）。按列表顺序（记录顺序）逐条处理：

- 与当前段匹配的登记转移推出新段；自环（例如草稿、已承诺时标核心战役）不换段，也不换推出它的事件。
- 没有匹配转移的非门事件不改变生命周期；未承诺先确认这类未列出的门事件不会被记下（admit 拒绝）。
- 撤回只对推出当前段的那条门事件、且由同一动作同一 phase 记时生效，生命周期回到它之前的那一段，
  这一段改由该撤回事件推出；因此此后改变过生命周期的事件之前的门事件、以及撤回事件本身都不能再撤回。
- 第一次进入已成立或已确认的那条确认让对象有了正式内容；撤回它、或对象退回初始段（草稿）时正式内容一并收回；
  这样收回正式内容的退回不能撤回。
- 有正式内容之后，登记里没有的承诺与确认是一轮重走：从初始段起按同一张门表走，承诺带候选内容，
  走到已成立或已确认时写回候选，退回则作废；重走不改变生命周期段。同一条确认既推进生命周期又符合
  进行中的一轮时（已成立后标为核心战役、CEO 接受），两边一起推进。
"""
from __future__ import annotations

from typing import Any

from . import world_v01_registry as world_registry

# 确认接受后对象有正式内容的段（契约第 11 节）：Mission 已成立，长期目标与周期目标已确认。
FORMAL_STAGES = frozenset({"established", "confirmed"})
_GATE_KINDS = frozenset({"commit", "confirm"})


class Refused(ValueError):
    """这条门事件现在不能记（契约第 10 节）。"""


def _matches(transition: dict[str, Any], state: str, event: dict[str, Any], core_battle: bool) -> bool:
    if transition["from"] != state or transition["action"] != event["action"]:
        return False
    if any(transition[field] is not None and transition[field] != event[field]
           for field in ("phase", "category", "outcome")):
        return False
    guard = transition["guard"]
    return (guard is None or (guard == "core_battle" and core_battle) or (guard == "not_core_battle" and not core_battle)
            or (guard == "spine_parent_responsible" and event.get("by_spine_parent_responsible") is True))


class _Replay:
    """按记录顺序重放一个对象的事件：段的历史、是否核心战役、正式内容由哪条确认带来、进行中的重走。"""

    def __init__(self, spec: dict[str, Any], events: list[dict[str, Any]]) -> None:
        self.spec = spec
        created = next((event["event_id"] for event in events if event["kind"] == "object.created"), None)
        self.stages = [(spec["initial"], created)]  # (段, 推出它的事件 id)；撤回时回到上一段
        self.seen: dict[str, dict[str, Any]] = {}
        self.core_battle = False
        self.formal_by: str | None = None
        self.round: dict[str, Any] | None = None  # {"stage": 重走走到的段, "commit": 带候选的承诺}
        self.took_back: set[str] = set()  # 退回草稿时收回了正式内容的确认；撤回它无法复原那份正式内容
        for event in events:
            try:
                self.step(event)
            except Refused:
                pass  # 不改变生命周期的事件

    def transition(self, state: str, event: dict[str, Any]) -> dict[str, Any] | None:
        return next((item for item in self.spec["transitions"] if _matches(item, state, event, self.core_battle)), None)

    def step(self, event: dict[str, Any]) -> dict[str, Any]:
        """记下一条事件，返回它对正式内容指针的作用；不能记则抛 Refused，状态不变。"""
        effect = {"makes_formal": False, "unmakes_formal": False, "opens_round": False, "writes_back": False,
                  "candidate_commit": None}
        state, producer = self.stages[-1]
        if event["outcome"] == "withdrawn":
            original = self.seen.get(event["supersedes_event_id"])
            if not (len(self.stages) > 1 and original is not None and original["event_id"] == producer
                    and original["outcome"] != "withdrawn"
                    and (original["action"], original["phase"]) == (event["action"], event["phase"])):
                raise Refused("Only the commitment or confirmation that produced the current stage is withdrawn, "
                              "by the same action and phase.")
            if original["event_id"] in self.took_back:
                raise Refused("A return that took the formal content back is not withdrawn; re-run the initiation.")
            self.stages.pop()
            self.stages[-1] = (self.stages[-1][0], event["event_id"])
            if self.formal_by == original["event_id"]:
                self.take_back_formal(effect)
        elif event["kind"] == "core_battle.marked" and self.core_battle:
            raise Refused("The Mission is already a core battle.")
        elif (found := self.transition(state, event)) is not None:
            if found["to"] != state:
                self.stages.append((found["to"], event["event_id"]))
            if event["kind"] == "core_battle.marked":
                self.core_battle = True
            if found["to"] in FORMAL_STAGES and self.formal_by is None:
                self.formal_by = event["event_id"]
                effect["makes_formal"] = True
            elif found["to"] == self.spec["initial"] and self.formal_by is not None:
                self.take_back_formal(effect)  # 例如已成立后标为核心战役、被 CEO 退回草稿
                self.took_back.add(event["event_id"])
            elif self.round is not None and event["kind"] == "confirm":
                self.rerun(event, effect, required=False)
        elif event["kind"] in _GATE_KINDS and self.formal_by is not None:
            self.rerun(event, effect, required=True)
        else:
            raise Refused(f"{event['action']} is not allowed at this stage ({state}).")
        self.seen[event["event_id"]] = event
        return effect

    def take_back_formal(self, effect: dict[str, Any]) -> None:
        self.formal_by, self.round = None, None
        effect["unmakes_formal"] = True

    def rerun(self, event: dict[str, Any], effect: dict[str, Any], *, required: bool) -> None:
        """重走承诺与确认（契约第 11 节）：从初始段起按同一张门表走一轮，不动生命周期段。required 为假时
        这条事件已推进了生命周期，只在它也符合进行中的一轮时顺带推进这一轮。"""
        initial = self.spec["initial"]
        if self.round is not None and event["kind"] == "commit" and self.transition(initial, event) is not None:
            raise Refused("A candidate already awaits confirmation; the confirmer accepts or returns it first.")
        start = self.round["stage"] if self.round else initial
        found = self.transition(start, event)
        if found is None or (self.round is None and found["to"] == start):
            if not required:
                return
            raise Refused(f"{event['action']} is not allowed at this stage ({self.stages[-1][0]}).")
        if found["to"] in FORMAL_STAGES:
            effect["writes_back"] = True
            effect["candidate_commit"] = self.round["commit"] if self.round else None
            self.round = None
        elif found["to"] == initial:
            self.round = None  # 退回：候选作废
        else:
            effect["opens_round"] = self.round is None
            self.round = {"stage": found["to"], "commit": self.round["commit"] if self.round else event["event_id"]}


def _view(spec: dict[str, Any], replay: _Replay) -> dict[str, Any]:
    state, producer = replay.stages[-1]
    names = {item["id"]: item["display_name"] for item in spec["states"]}
    return {"status": state, "display_name": names[state], "event_id": producer}


def derive(object_type: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """返回 {status, display_name, event_id}；没有生命周期的类型（只有版本）返回 None。"""
    spec = world_registry.registry()["lifecycles"].get(object_type)
    if spec is None:
        return None
    return _view(spec, _Replay(spec, events))


def admit(object_type: str, events: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
    """一条门事件接在 events 之后能不能记；能则返回它对正式内容指针的作用与记下后所处的段，否则抛 Refused。"""
    spec = world_registry.registry()["lifecycles"][object_type]
    replay = _Replay(spec, events)
    effect = replay.step(event)
    return {**effect, **_view(spec, replay)}
