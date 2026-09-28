"""tkos.world/0.2 的生命周期推导与门准入（契约第 10 至 13 节、ADR-0002）：按登记的状态表运行的纯函数。

登记以参数传入，不读文件、不读环境、不连数据库。各类对象的状态表取自 ``lifecycles``，Issue 的取自
``issue.lifecycle``。每条输入事件带 event_id、action（产生它的动作）、outcome、disposition、
supersedes_event_id，可选 candidate（是否带候选内容）与 guards（守卫 id → 真假）；admit 的新事件另带
recorders：记录者对这个对象满足的记录者类别（登记 ``recorders``，代记时按被代记的人算）。

- 推导按列表顺序（记录顺序）逐条处理，初始段由建对象推出（Issue 没有推出初始段的事件）。与当前状态匹配的
  转移推出新状态；自环不换状态，也不换推出它的事件；没有匹配转移的事件不改变生命周期。
- 推导不重判守卫与记录者：已记的事件在准入时判过，此后事实变了也不改历史。只有几条转移仅差守卫时
  （Strategy 的 Agreement：本轮未齐、本轮补齐）按事件带的守卫事实选，所以已记的 Agreement 要带
  ``round_complete``。准入则严格判：守卫事实为真、记录者类别符合转移的 ``by``，否则拒绝。
- 撤回（契约第 11 节）：只撤推出当前状态的那条门事件或生命周期事件，同一动作、按原转移的 ``by`` 记；
  状态回到它之前，这一段改由撤回事件推出。撤回事件、再确认、没推出状态的 Agreement、一轮重走中的
  事件因此都不能撤回。一轮写回过之后，让对象成为正式的那条事件也不能再撤回（登记
  ``rules.withdrawal.never`` 的 ``formal_confirm_after_write_back``）。
- 正式内容（契约第 12 节）：第一次进入 ``formal_on`` 的事件让对象有了正式内容，形成中带候选的事件
  （``rounds.candidate_carried_by``）的候选随之写回；撤回这条事件收回正式内容。
- 一轮重走：有正式内容以后，``rounds`` 里的动作从初始段起按同一张表虚走，不改变生命周期段，只在
  ``rounds.allowed_in`` 的状态里推进。开轮的事件要带候选（Strategy 这类有 Agreement 的一轮可以不带），
  一轮未完不能再开，只有 Strategy 未齐时重新指定是换成新的一轮；走到 ``formal_on`` 写回候选，退回初始段
  则本轮作废；长期目标这类没有开轮动作的，带候选的确认一步写回。Strategy 一轮已补齐且不带候选时，
  再确认结束本轮。

admit 返回记下后所处的状态，与这条事件对正式内容指针的作用：成为正式、收回正式、开轮、写回、结束本轮，
以及要写回的候选来自哪条事件。
"""
from __future__ import annotations

from typing import Any


class Refused(ValueError):
    """这条事件现在不能记。reason：state（状态表或撤回、重走规则不允许）、recorder（记录者不符）、guard（守卫不成立）。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _spec(registry: dict[str, Any], object_type: str) -> dict[str, Any] | None:
    if object_type == "Issue":
        return registry["issue"]["lifecycle"]
    return registry["lifecycles"].get(object_type)


class _Replay:
    """按记录顺序重放一个对象的事件：状态的历史、正式内容由哪条事件带来、形成中的候选、进行中的一轮。"""

    def __init__(self, registry: dict[str, Any], spec: dict[str, Any], events: list[dict[str, Any]]) -> None:
        self.spec = spec
        self.actions = {item["action"]: item for item in registry["actions"]}
        self.withdrawable = set(registry["rules"]["withdrawal"]["classes"])
        self.keeps_rewritten = "formal_confirm_after_write_back" in registry["rules"]["withdrawal"]["never"]
        self.rounds = spec.get("rounds") or {}
        self.round_actions = {self.rounds[key] for key in ("opened_by", "agreed_by", "closed_by", "candidate_carried_by")
                              if self.rounds.get(key)}
        created = next((event["event_id"] for event in events if event["action"] == "world_create_object"), None)
        # 状态的历史，撤回时回到上一段：{state, producer（推出它的事件）, transition, candidate（这一段里待写回的
        # 形成期候选来自哪条事件）}
        self.stages: list[dict[str, Any]] = [{"state": spec["initial"], "producer": created, "transition": None,
                                              "candidate": None}]
        self.seen: dict[str, dict[str, Any]] = {}
        self.formal_by: str | None = None
        self.written_back = False  # 一轮重走写回过
        self.round: dict[str, Any] | None = None  # {"opened_by", "stage", "candidate"}
        for event in events:
            try:
                self.step(event, admitting=False)
            except Refused:
                pass  # 不改变生命周期的事件

    def choose(self, state: str, event: dict[str, Any], admitting: bool) -> dict[str, Any] | None:
        found = [item for item in self.spec["transitions"]
                 if item["from"] == state and item["action"] == event["action"]
                 and item["outcome"] == event.get("outcome") and item["disposition"] == event.get("disposition")]
        facts = event.get("guards") or {}
        if not admitting:
            found = [item for item in found if item["guard"] is None or facts.get(item["guard"]) is not False]
            found.sort(key=lambda item: facts.get(item["guard"]) is not True)
            return found[0] if found else None
        if not found:
            return None
        held = [item for item in found if item["guard"] is None or facts.get(item["guard"]) is True]
        if not held:
            raise Refused("guard", f"{event['action']} needs {' or '.join(item['guard'] for item in found)}.")
        allowed = [item for item in held if item["by"] in event["recorders"]]
        if not allowed:
            raise Refused("recorder", f"{event['action']} at {state} is recorded by "
                                      f"{' or '.join(item['by'] for item in held)}.")
        return allowed[0]

    def step(self, event: dict[str, Any], *, admitting: bool) -> dict[str, Any]:
        """记下一条事件，返回它对正式内容指针的作用；不能记则抛 Refused，状态不变。"""
        effect = {"makes_formal": False, "unmakes_formal": False, "opens_round": False, "writes_back": False,
                  "ends_round": False, "candidate_event_id": None}
        if event.get("outcome") == "withdrawn":
            self.withdraw(event, effect, admitting)
        elif self.formal_by is not None and event["action"] in self.round_actions:
            self.rerun(event, effect, admitting)
        else:
            self.advance(event, effect, admitting)
        self.seen[event["event_id"]] = event
        return effect

    def advance(self, event: dict[str, Any], effect: dict[str, Any], admitting: bool) -> None:
        top = self.stages[-1]
        found = self.choose(top["state"], event, admitting)
        if found is None:
            raise Refused("state", f"{event['action']} is not allowed at this stage ({top['state']}).")
        initial = self.spec["initial"]
        if event.get("candidate") and not (event["action"] == self.rounds.get("candidate_carried_by")
                                           and self.rounds.get("agreed_by") is None and found["to"] != initial):
            raise Refused("state", f"{event['action']} does not carry a candidate here; only a draft commitment "
                                   "(a long-term goal's confirmation) does.")
        candidate = event["event_id"] if event.get("candidate") else None if found["to"] == initial else top["candidate"]
        if found["to"] != top["state"]:
            top = {"state": found["to"], "producer": event["event_id"], "transition": found}
            self.stages.append(top)
        top["candidate"] = candidate
        if found["to"] == self.spec.get("formal_on") and self.formal_by is None:
            self.formal_by = event["event_id"]
            effect.update(makes_formal=True, candidate_event_id=candidate)
            top["candidate"] = None
        if (self.round is not None and self.actions[event["action"]]["event_kind"] == "reconfirm"
                and self.round["candidate"] is None and self.round["stage"] != self.spec["initial"]):
            self.round = None  # 结论为不改的一轮由再确认结束
            effect["ends_round"] = True

    def withdraw(self, event: dict[str, Any], effect: dict[str, Any], admitting: bool) -> None:
        producing = self.stages[-1]["transition"]
        original = self.seen.get(event.get("supersedes_event_id"))
        if (original is None or producing is None or original["event_id"] != self.stages[-1]["producer"]
                or original.get("outcome") == "withdrawn" or original["action"] != event["action"]
                or self.actions[event["action"]]["class"] not in self.withdrawable):
            raise Refused("state", "Only the gate or lifecycle event that produced the current state is withdrawn, "
                                   "by the same action.")
        if self.formal_by == original["event_id"] and self.written_back and self.keeps_rewritten:
            raise Refused("state", "A re-run has rewritten the formal content; the event that made it formal "
                                   "is no longer withdrawn. Open another re-run instead.")
        if admitting and producing["by"] not in event["recorders"]:
            raise Refused("recorder", f"The withdrawal is recorded by {producing['by']}, like the original.")
        self.stages.pop()  # 上一段连同它的候选原样回来，只是改由撤回事件推出
        self.stages[-1]["producer"] = event["event_id"]
        if self.formal_by == original["event_id"]:
            self.formal_by = None
            effect["unmakes_formal"] = True
            if self.round is not None:
                self.round = None  # 一轮改的是正式内容，正式内容收回，这一轮随之作废
                effect["ends_round"] = True

    def rerun(self, event: dict[str, Any], effect: dict[str, Any], admitting: bool) -> None:
        """一轮重走（契约第 12 节）：从初始段起按同一张表虚走，不动生命周期段。"""
        state, initial, formal = self.stages[-1]["state"], self.spec["initial"], self.spec["formal_on"]
        if state not in self.rounds["allowed_in"]:
            raise Refused("state", f"No re-run of the gate at this stage ({state}).")
        action, carries = event["action"], bool(event.get("candidate"))
        opening = action == self.rounds["opened_by"]
        one_shot = self.rounds["opened_by"] is None and action == self.rounds["closed_by"]  # 长期目标：一步写回
        if self.round is None and not (opening or one_shot):
            raise Refused("state", "No re-run of the gate is open.")
        found = self.choose(self.round["stage"] if self.round else initial, event, admitting)
        if found is None or (one_shot and found["to"] != formal):
            raise Refused("state", "A re-run of the gate is still open; the confirmer accepts or returns it first."
                          if self.round else f"{action} does not re-run the gate at this stage ({state}).")
        if carries and not (opening or one_shot):
            raise Refused("state", f"{action} does not carry a candidate; the round's opening event does.")
        if (opening or one_shot) and not carries and self.rounds["agreed_by"] is None:
            raise Refused("state", "A re-run of the gate on formal content carries the candidate content.")
        if opening:
            self.round = {"opened_by": event["event_id"], "stage": found["to"],
                          "candidate": event["event_id"] if carries else None}
            effect["opens_round"] = True
        elif found["to"] == formal:
            candidate = self.round["candidate"] if self.round else event["event_id"]
            if candidate is None:
                raise Refused("state", "A re-run without a candidate ends with a reconfirmation.")
            self.round = None
            self.written_back = True
            effect.update(writes_back=True, candidate_event_id=candidate)
        elif found["to"] == initial and found["from"] != initial:
            self.round = None  # 退回：候选作废
            effect["ends_round"] = True
        else:
            self.round["stage"] = found["to"]

    def view(self) -> dict[str, Any]:
        state, producer = self.stages[-1]["state"], self.stages[-1]["producer"]
        names = {item["id"]: item["display_name"] for item in self.spec["states"]}
        current = self.round and {"opened_by_event_id": self.round["opened_by"], "stage": self.round["stage"],
                                  "candidate_event_id": self.round["candidate"]}
        return {"status": state, "display_name": names[state], "event_id": producer,
                "formal_event_id": self.formal_by, "round": current}


def derive(registry: dict[str, Any], object_type: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """返回 {status, display_name, event_id（推出它的事件）, formal_event_id, round}；没有生命周期的类型返回 None。"""
    spec = _spec(registry, object_type)
    if spec is None:
        return None
    return _Replay(registry, spec, events).view()


def admit(registry: dict[str, Any], object_type: str, events: list[dict[str, Any]],
          event: dict[str, Any]) -> dict[str, Any]:
    """一条事件接在 events 之后能不能记；能则返回它的作用与记下后所处的状态，否则抛 Refused。"""
    spec = _spec(registry, object_type)
    if spec is None:
        raise KeyError(f"{object_type} has no lifecycle")
    replay = _Replay(registry, spec, events)
    effect = replay.step(event, admitting=True)
    return {**effect, **replay.view()}
