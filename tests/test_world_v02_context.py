"""tkos.world/0.2 的取上下文（票 #56，契约第 15.3 节）：组件级引用与事件引用，0.2 的生命周期、正式内容、快照外壳
与责任人；预算、裁剪、六问覆盖、检索计划与落表沿用 0.1（票 #25）。不连数据库。

覆盖判定是纯函数；组装在假连接上的一条 0.2 主干上做：读投影里取对象、快照与事件的三个入口换成按下表查的替身，
生命周期与责任人走 0.2 读侧的真代码，由假连接按语句应答。HTTP 路径（从 Activity 出发、引用逐条读回、scope 外
404、落表、确定性、0.1 对象不变）在 acceptance/world_v02 的 context 场景里。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import math
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from memory_service_runtime.governed import world_v01_context
from memory_service_runtime.governed import world_v02_context as context
from memory_service_runtime.governed import world_v02_readers as readers
from memory_service_runtime.governed import world_v02_registry as registry
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.world_v01_models import WorldContextRequest

V02 = "tkos.world/0.2"


# ------------------------------------------------------------ pure: coverage
def layer(level, *, blocks=(), state=None, events=(), responsible=(), formal=None):
    """覆盖判定看的那几项：对象引用、是否正式、责任人，块（类别、块类别、组件）、快照与事件的引用。"""
    base = f"o{level}@1"

    def block(id, kind="definition", klass="formal", text="", components=(), empty=False):
        ref = f"{base}#{id}"
        items = [{"id": cid, "ref": f"{ref}/{cid}"} for cid in components]
        return {"id": id, "kind": kind, "class": klass, "ref": ref, "empty": empty, "components": items,
                "value": None if empty else {"text": text, "components": items, "refs": [], "artifacts": []}}
    return {"level": level, "object": {"ref": base, "formal": formal,
                                       "responsible": {"source": "attribute", "principals": list(responsible)}},
            "blocks": [block(**spec) for spec in blocks], "state": state and {"ref": state},
            "events": [{"event_id": event_id, "ref": f"event:{event_id}"} for event_id in events]}


def test_evidence_cites_components_where_content_is_a_component_and_events_by_event_reference():
    layers = [layer(0, blocks=[{"id": "instruction", "text": "改材料"},
                               {"id": "constraint", "kind": "constraint", "empty": True}],
                    state="s0@1", events=["e1"], responsible=["p1"]),
              layer(1, blocks=[{"id": "definition", "text": "方案"},
                               {"id": "acceptance", "components": ["ac-1", "ac-2"]},
                               {"id": "plan", "kind": "plan", "klass": "activity", "components": ["p-1"]}]),
              layer(2, blocks=[{"id": "acceptance", "text": "总验收", "components": ["m-ac1"]},
                               {"id": "execution_plan", "kind": "plan", "klass": "activity", "components": ["plan-1"]}],
                    formal=True)]
    coverage = context.cover(layers)
    assert {name: answer["evidence"] for name, answer in coverage.items()} == {
        # 块自己的文字给块引用，组件逐条给组件引用；计划类的块不是 Why。
        "why": [{"ref": "o1@1#definition"}, {"ref": "o1@1#acceptance/ac-1"}, {"ref": "o1@1#acceptance/ac-2"},
                {"ref": "o2@1#acceptance"}, {"ref": "o2@1#acceptance/m-ac1"}],
        "what": [{"ref": "o0@1#instruction"}],
        "who": [{"ref": "o0@1"}],
        "now": [{"ref": "s0@1"}],
        "happened": [{"ref": "event:e1"}],
        # 上层已正式的对象只算正式块（活动块不经门），再加各层的验收标准与约束，去重。
        "basis": [{"ref": "o2@1#acceptance"}, {"ref": "o2@1#acceptance/m-ac1"}, {"ref": "o1@1#acceptance/ac-1"},
                  {"ref": "o1@1#acceptance/ac-2"}]}
    assert all(answer["answered"] and answer["gap"] is None for answer in coverage.values())
    assert [answer["question"] for answer in coverage.values()] == ["为什么", "做什么", "谁负责", "现在怎样", "发生了什么",
                                                                     "凭什么"]


def test_a_question_the_pack_cannot_answer_is_a_gap_with_its_reason():
    coverage = context.cover([layer(0, blocks=[{"id": "instruction", "empty": True}]),
                              layer(1, blocks=[{"id": "execution_plan", "kind": "plan", "klass": "activity",
                                                "components": ["plan-1"]}], formal=True)])
    assert all(not answer["answered"] and answer["evidence"] == [] and answer["gap"] for answer in coverage.values())


# ------------------------------------------------------------ build：假连接上的一条 0.2 主干
# Activity → Task → Mission → 周期目标 → 单元长期目标 → 责任单元（架构引用指到责任单元条目）→ Strategy → Company。
# Mission、周期目标与长期目标已正式；Activity 与 Mission 各有一条快照，Task 没有；事件七条。
def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


COMPANY, STRATEGY, UNIT, GOAL, PERIOD, MISSION, TASK, ACTIVITY = (uid(n) for n in range(1, 9))
OTHER_GOAL, OTHER_MISSION = uid(10), uid(11)
SNAP_ACTIVITY, SNAP_MISSION = uid(21), uid(23)
CEO, DRI, OWNER, IC, AGENT = (uid(n) for n in range(31, 36))
CREATED, ASSIGNED, CONFIRMED, MET, STARTED, LATE, REFRESHED, CORRECTED = (uid(n) for n in range(41, 49))
PEOPLE = {CEO: ("CEO", "human"), DRI: ("E&O DRI", "human"), OWNER: ("Mission Owner", "human"),
          IC: ("方案 IC", "human"), AGENT: ("E&O Agent", "agent")}
ROLES = {("company", "CEO"): [CEO], ("eo", "CEO"): [CEO], ("eo", "DOMAIN_DRI"): [DRI]}


def rev(object_id: str, version: int) -> str:
    return f"r{object_id[-2:]}-{version}"


def pin(object_id: str, version: int = 1, block: str | None = None, component: str | None = None) -> dict:
    return {"object_id": object_id, "object_version": version, "revision_id": rev(object_id, version), "block": block,
            "component": component}


def cited(object_id: str, version: int = 1, block: str | None = None, component: str | None = None) -> dict:
    ref = f"{object_id}@{version}" + (f"#{block}" if block else "") + (f"/{component}" if component else "")
    return {**pin(object_id, version, block, component), "ref": ref}


def value(text: str = "", components=(), refs=(), artifacts=()) -> dict:
    return {"text": text, "components": list(components), "refs": list(refs), "artifacts": list(artifacts)}


def part(cid: str, ctype: str, text: str, *, refs=(), attributes=None) -> dict:
    return {"id": cid, "type": ctype, "scope": None, "text": text, "refs": list(refs), "artifacts": [],
            "attributes": attributes or {}}


# 对象 -> (类型, 域, 最新版本, 内核的正式内容指针, 属性与关系, 非空块)。
OBJECTS = {
    COMPANY: ("Company", "company", 1, "recorded", {"title": "词元云集", "external_refs": []},
              {"identity": value("企业经营系统", artifacts=["https://docs.example/company"])}),
    STRATEGY: ("Strategy", "company", 1, "draft", {"title": "总体战略", "external_refs": [], "parent_ref": pin(COMPANY)},
               {"choices": value("聚焦经营系统"),
                "responsibility_structure": value("两个域", [part("unit-eo", "unit_entry", "E&O 域")])}),
    UNIT: ("ResponsibilityUnit", "eo", 1, "recorded",
           {"title": "E&O", "unit_kind": "domain", "external_refs": [],
            "architecture_ref": pin(STRATEGY, 1, "responsibility_structure", "unit-eo")},
           {"definition": value("本体与 Context Runtime")}),
    GOAL: ("LongTermGoal", "eo", 1, "confirmed",
           {"title": "E&O 六个月目标", "scope": "unit", "horizon": "六个月", "external_refs": [], "parent_ref": pin(UNIT),
            "goal_ref": None},
           {"outcome": value(components=[part("lt-o1", "outcome", "建立 Enterprise Context")])}),
    PERIOD: ("PeriodGoal", "eo", 1, "confirmed",
             {"title": "E&O 10 月", "period": "2026-10", "external_refs": [], "goal_ref": pin(GOAL), "review_ref": None,
              "depends_on": []},
             {"outcome": value(components=[part("pg-o1", "outcome", "核心本体稳定",
                                                refs=[pin(GOAL, 1, "outcome", "lt-o1")])]),
              "acceptance": value(components=[part("pg-ac1", "acceptance_criterion", "两个 Agent 用上")])}),
    MISSION: ("Mission", "eo", 2, "confirmed",
              {"title": "Agent 真实可用", "external_refs": [], "goal_ref": pin(PERIOD), "responsible": OWNER,
               "core_battle": False, "depends_on": [], "contributes_to": [pin(OTHER_GOAL)]},
              {"definition": value("为 Agent 提供底座"),
               "acceptance": value("整体验收", [part("m-ac1", "acceptance_criterion", "Agent 能取到上下文",
                                                    refs=[pin(PERIOD, 1, "acceptance", "pg-ac1")])]),
               "execution_plan": value(components=[part("plan-1", "plan_item", "搭环境",
                                                        attributes={"responsible": OWNER})]),
               "constraint": value("写入先限得死一点")}),
    TASK: ("Task", "eo", 2, "recorded",
           {"title": "方案设计", "external_refs": [], "parent_ref": pin(MISSION, 2), "responsible": IC, "depends_on": []},
           {"definition": value("节前完成整体方案"),
            "acceptance": value(components=[part("ac-1", "acceptance_criterion", "方案评审通过",
                                                 refs=[pin(MISSION, 2, "acceptance", "m-ac1")]),
                                            part("ac-2", "acceptance_criterion", "演示通过")]),
            "plan": value(components=[part("p-1", "plan_item", "写脚本")])}),
    ACTIVITY: ("Activity", "eo", 2, "recorded",
               {"title": "改建模材料", "external_refs": [], "parent_ref": pin(TASK, 2), "responsible": AGENT},
               {"instruction": value("按评审意见改材料", refs=[pin(TASK, 2, "acceptance", "ac-1")])}),
}
# 以跨链关系指向 Mission 的另一个对象（最新修订），读侧只列、不展开。
RELATING = [{"object_id": OTHER_MISSION, "object_version": 1, "revision_id": rev(OTHER_MISSION, 1),
             "payload": {"depends_on": [pin(MISSION, 2)], "contributes_to": []}}]


def head(object_id: str) -> dict:
    if object_id in SNAPSHOTS_BY_ID:
        return {"object_id": object_id, "object_type": "StateSnapshot", "domain_id": "eo",
                "latest_revision_id": rev(object_id, 1), "lifecycle_status": "recorded"}
    object_type, domain, version, status, _, _ = OBJECTS[object_id]
    return {"object_id": object_id, "object_type": object_type, "domain_id": domain,
            "latest_revision_id": rev(object_id, version), "lifecycle_status": status}


def revision(object_id: str) -> dict:
    object_type, _, version, _, attributes, blocks = OBJECTS[object_id]
    ids = [block["id"] for block in registry.object_spec(object_type)["blocks"]]
    return {"revision_id": rev(object_id, version), "object_id": object_id, "object_version": version,
            "payload": {**attributes, "blocks": {block: blocks.get(block) for block in ids}}}


def snapshot(snapshot_id: str, subject: str, as_of: str, source: str, **blocks) -> tuple[dict, dict]:
    """一条执行状态快照：修订行与取对象给出的外壳视图。"""
    payload = {"title": "执行状态快照", "subject_ref": pin(subject, OBJECTS[subject][2]), "as_of": as_of,
               "period": "2026-10", "payload_type": "execution_state", "source_event_refs": [{"event_id": source}],
               "generator": AGENT,
               "blocks": {block: blocks.get(block) for block in ("progress", "blockers", "issues", "materials")}}
    row = {"revision_id": rev(snapshot_id, 1), "object_id": snapshot_id, "object_version": 1, "payload": payload}
    generator = {"principal_id": AGENT, "principal_type": "agent", "display_name": "E&O Agent"}
    return row, readers.snapshot_view({"object_id": snapshot_id}, row, generator=generator)


SNAPSHOTS = {
    ACTIVITY: snapshot(SNAP_ACTIVITY, ACTIVITY, "2026-09-24T10:00:00Z", MET, progress=value(components=[
        part("todo:17", "progress_item", "改了一半", attributes={
            "principal_id": AGENT, "principal_name": "E&O Agent", "external_status": "进行中",
            "entries": [{"at": "2026-09-24T09:00:00Z", "source": "codex", "text": "改完第一节", "url": None}]})]),
        issues=value(components=[part("iss-1", "issue", "评审意见有冲突",
                                      attributes={"core_question": "按哪条意见改？", "responsible_hint": IC})])),
    MISSION: snapshot(SNAP_MISSION, MISSION, "2026-09-24T11:00:00Z", CONFIRMED, progress=value("按计划推进")),
}
SNAPSHOTS_BY_ID = {view["object_id"]: (row, view) for row, view in SNAPSHOTS.values()}
ACTIONS = {"object.created": "world_create_object", "assign": "world_assign", "confirm": "world_confirm_mission",
           "event.recorded": "world_record_event", "start": "world_start", "state.refreshed": "world_refresh_state"}
CLASSES = {item["kind"]: item["class"] for item in registry.registry()["event_kinds"]}


def event(event_id, kind, principal, occurred_at, subjects, *, text=None, late=False, recorded_at=None, **fields) -> dict:
    """取事件给出的一条事件（0.2 读侧的形状）。"""
    return {"event_id": event_id, "scope_id": uid(0), "kind": kind, "class": CLASSES[kind], "category": None,
            "outcome": None, "disposition": None, "subject_refs": readers.cited(subjects),
            "principal": {"principal_id": principal, "principal_type": PEOPLE[principal][1],
                          "display_name": PEOPLE[principal][0]},
            "on_behalf_of": None, "external_confirmation": None, "occurred_at": occurred_at,
            "recorded_at": recorded_at or occurred_at, "late": late, "content": text and value(text), "detail": None,
            "action": ACTIONS[kind], "action_id": uid(90), "supersedes_event_id": None, "corrected_by": [],
            "withdrawn_by": [], **fields}


# 一条事件以多层对象为主体时只放在最近的一层：会议以 Task 的验收条件与 Activity 为主体，落在 Activity 层。
# Mission 层有一条迟记的会议与更正它的外部事件。
EVENTS = [
    event(CREATED, "object.created", DRI, "2026-09-22T00:00:00Z", [pin(ACTIVITY, 1)]),
    event(ASSIGNED, "assign", IC, "2026-09-22T01:00:00Z", [pin(ACTIVITY, 2)], detail={"principal_id": AGENT}),
    event(CONFIRMED, "confirm", DRI, "2026-09-22T02:00:00Z", [pin(MISSION, 2)], outcome="accepted"),
    event(MET, "event.recorded", IC, "2026-09-23T02:23:00Z", [pin(TASK, 2, "acceptance", "ac-1"), pin(ACTIVITY, 2)],
          category="meeting", text="评审方案"),
    event(STARTED, "start", AGENT, "2026-09-23T03:00:00Z", [pin(ACTIVITY, 2)]),
    event(LATE, "event.recorded", OWNER, "2026-09-21T00:00:00Z", [pin(MISSION, 2)], category="meeting",
          text="立项前沟通", late=True, recorded_at="2026-09-24T12:00:00Z", corrected_by=[CORRECTED]),
    event(REFRESHED, "state.refreshed", AGENT, "2026-09-24T11:00:00Z", [pin(SNAP_MISSION, 1), pin(MISSION, 2)]),
    event(CORRECTED, "event.recorded", OWNER, "2026-09-24T12:30:00Z", [pin(MISSION, 2)], category="correction",
          text="沟通会的日期记错了", supersedes_event_id=LATE),
]
# 推导生命周期的输入（以对象为目标的事件，按记录顺序）：由 0.2 的状态表推导。
LIFECYCLE = {
    ACTIVITY: [(CREATED, "world_create_object"), (ASSIGNED, "world_assign"), (STARTED, "world_start")],
    TASK: [(uid(51), "world_create_object"), (uid(52), "world_assign")],
    MISSION: [(uid(53), "world_create_object"), (uid(54), "world_commit_mission"),
              (CONFIRMED, "world_confirm_mission", "accepted")],
    PERIOD: [(uid(55), "world_create_object"), (uid(56), "world_commit_period_goal"),
             (uid(57), "world_confirm_period_goal", "accepted")],
    GOAL: [(uid(58), "world_create_object"), (uid(59), "world_confirm_long_term_goal", "accepted")],
    STRATEGY: [(uid(60), "world_create_object")],
}


class Conn:
    """按语句应答的假连接：修订、生命周期的事件、责任人、跨链关系的反查、窗口起点，落表的那一行记在 inserted。"""

    def __init__(self) -> None:
        self.inserted = None

    def execute(self, sql, params=()):
        rows = self.answer(" ".join(sql.split()), params)
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)

    def answer(self, sql, params):
        if "make_interval" in sql:
            return [{"start": datetime(2026, 8, 25, tzinfo=timezone.utc)}]
        if sql.startswith("SELECT * FROM gov_object_revisions"):
            rows = [revision(oid) for oid in OBJECTS] + [row for row, _ in SNAPSHOTS.values()]
            return [row for row in rows if row["revision_id"] == params[1]]
        if "subject_refs->0->>'object_id'" in sql:
            return [{"event_id": item[0], "action": item[1], "outcome": item[2] if len(item) > 2 else None,
                     "disposition": None, "supersedes_event_id": None} for item in LIFECYCLE.get(params[2], [])]
        if sql.startswith("SELECT principal_id, principal_type, display_name FROM gov_principals"):
            name, kind = PEOPLE[params[1]]
            return [{"principal_id": params[1], "principal_type": kind, "display_name": name}]
        if sql.startswith("SELECT DISTINCT p.principal_id"):
            return [{"principal_id": person, "principal_type": PEOPLE[person][1], "display_name": PEOPLE[person][0]}
                    for person in ROLES.get((params[1], params[2]), [])]
        if sql.startswith("SELECT o.object_id, r.object_version, r.revision_id, r.payload"):
            target = params[2].obj[0]["object_id"]
            return [row for row in RELATING
                    if any(item["object_id"] == target for field in ("depends_on", "contributes_to")
                           for item in row["payload"].get(field, []))]
        if sql.startswith("INSERT INTO gov_world_context_packs"):
            self.inserted = params
            return [{"context_pack_id": uid(99), "created_at": datetime(2026, 9, 24, 12, tzinfo=timezone.utc)}]
        raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture
def world(monkeypatch):
    def readable(conn, ctx, object_id):
        if object_id not in OBJECTS and object_id not in SNAPSHOTS_BY_ID:
            raise GovernedError("NOT_FOUND")
        return head(object_id), {"interpretation_status": "world_v0_2"}

    def latest_snapshot(conn, ctx, subject, as_of=None):
        found = SNAPSHOTS.get(subject)
        return found and (as_of is None or found[1]["as_of"] <= as_of) and found[1] or None

    def events(conn, ctx, object_id, since=None):
        about = [row for row in EVENTS if object_id in {ref["object_id"] for ref in row["subject_refs"]}
                 and (since is None or datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00")) >= since)]
        return {"object_id": object_id, "events": sorted(about, key=lambda row: (row["occurred_at"], row["recorded_at"],
                                                                                  row["event_id"]))}

    monkeypatch.setattr(readers, "readable", readable)
    monkeypatch.setattr(readers, "latest_snapshot", latest_snapshot)
    monkeypatch.setattr(readers, "events", events)
    return Conn()


def build(conn, start=ACTIVITY, question="为什么要做这条 Activity？", **request):
    return context.build(conn, SimpleNamespace(scope_id=uid(0), principal_id=AGENT), start,
                         WorldContextRequest.model_validate({"question": question, **request}))


def section(markdown: str, ref: str) -> list[str]:
    """Markdown 里以带这个引用的标题开头的那一段，逐行。"""
    return next(item for item in markdown.split("\n\n") if item.startswith("#") and f"`{ref}`" in item.splitlines()[0]
                ).splitlines()


def test_the_context_walks_the_0_2_spine_and_pins_each_step(world):
    result = build(world)
    layers = result["context_pack"]["layers"]
    assert [layer["object"]["object_type"] for layer in layers] == registry.registry()["spine"]
    # 沿主干读的是上一级的最新修订；责任单元的架构引用钉在责任单元条目上（组件形式）。
    assert [(step["field"], step["pinned"], step["read"]) for step in result["plan"]["walked"]] == [
        ("parent_ref", f"{TASK}@2", f"{TASK}@2"), ("parent_ref", f"{MISSION}@2", f"{MISSION}@2"),
        ("goal_ref", f"{PERIOD}@1", f"{PERIOD}@1"), ("goal_ref", f"{GOAL}@1", f"{GOAL}@1"),
        ("parent_ref", f"{UNIT}@1", f"{UNIT}@1"),
        ("architecture_ref", f"{STRATEGY}@1#responsibility_structure/unit-eo", f"{STRATEGY}@1"),
        ("parent_ref", f"{COMPANY}@1", f"{COMPANY}@1")]
    assert [layer["object"]["pinned"] for layer in layers[:2]] == [cited(ACTIVITY, 2), cited(TASK, 2)]
    assert result["contract_version"] == result["context_pack"]["contract_version"] == V02
    assert result["context_pack"]["start"] == f"{ACTIVITY}@2"


def test_each_component_is_cited_in_component_form_pinned_to_the_revision_read(world):
    result = build(world)
    task = result["context_pack"]["layers"][1]
    acceptance = next(block for block in task["blocks"] if block["id"] == "acceptance")
    assert acceptance["pinned"] == cited(TASK, 2, "acceptance")
    assert [(item["ref"], item["pinned"]) for item in acceptance["components"]] == [
        (f"{TASK}@2#acceptance/ac-1", cited(TASK, 2, "acceptance", "ac-1")),
        (f"{TASK}@2#acceptance/ac-2", cited(TASK, 2, "acceptance", "ac-2"))]
    # 组件里的引用读回两种形式；块值里的组件与块上的组件是同一份。
    assert acceptance["components"][0]["refs"] == [cited(MISSION, 2, "acceptance", "m-ac1")]
    assert acceptance["value"]["components"] == acceptance["components"]
    markdown = result["context_pack"]["markdown"]
    assert section(markdown, f"{TASK}@2#acceptance") == [
        f"### 验收标准 `{TASK}@2#acceptance`",
        f"- 验收条件 `{TASK}@2#acceptance/ac-1`：方案评审通过",
        f"  引用：`{MISSION}@2#acceptance/m-ac1`",
        f"- 验收条件 `{TASK}@2#acceptance/ac-2`：演示通过"]
    # 块自己的文字、引用与组件都在；组件的类型属性按登记的显示名写出。
    assert section(markdown, f"{ACTIVITY}@2#instruction") == [
        f"### 执行指令 `{ACTIVITY}@2#instruction`", "按评审意见改材料", f"引用：`{TASK}@2#acceptance/ac-1`"]
    assert section(markdown, f"{MISSION}@2#execution_plan") == [
        f"### 执行计划 `{MISSION}@2#execution_plan`", f"- 计划条目 `{MISSION}@2#execution_plan/plan-1`：搭环境",
        f"  责任人（只作记录）：`{OWNER}`"]
    assert section(markdown, f"{TASK}@2#constraint") == [f"### 约束 `{TASK}@2#constraint`", "当前没有约束"]


def test_events_are_cited_by_event_reference_newest_first_and_once_at_the_nearest_level(world):
    result = build(world)
    layers = result["context_pack"]["layers"]
    assert [[item["event_id"] for item in layer["events"]] for layer in layers[:3]] == [
        [STARTED, MET, ASSIGNED, CREATED], [], [CORRECTED, REFRESHED, CONFIRMED, LATE]]
    assert all(item["ref"] == f"event:{item['event_id']}" for layer in layers for item in layer["events"])
    lines = result["context_pack"]["markdown"].splitlines()
    assert f"- 2026-09-23T02:23:00Z 外部事件·会议（事件 `event:{MET}`）：评审方案" in lines
    assert f"- 2026-09-22T02:00:00Z 确认·接受（事件 `event:{CONFIRMED}`）" in lines
    # 迟记与更正、撤回所指的原事件照取事件写出。
    assert f"- 2026-09-21T00:00:00Z 外部事件·会议（事件 `event:{LATE}`，迟记）：立项前沟通" in lines
    assert (f"- 2026-09-24T12:30:00Z 外部事件·更正（事件 `event:{CORRECTED}`，原事件 `event:{LATE}`）：沟通会的日期记错了"
            in lines)
    # 表头的生命周期也以事件引用指出推出它的事件。
    assert section(result["context_pack"]["markdown"], f"{ACTIVITY}@2")[1] == f"生命周期：进行中（事件 `event:{STARTED}`）"


def test_the_latest_snapshot_is_its_shell_view_marked_unconfirmed(world):
    result = build(world)
    state = result["context_pack"]["layers"][0]["state"]
    assert (state["ref"], state["pinned"], state["unconfirmed"], state["as_of"], state["payload_type"],
            state["generator"]["principal_id"], state["source_event_refs"], state["subject_ref"]) == (
        f"{SNAP_ACTIVITY}@1", cited(SNAP_ACTIVITY), True, "2026-09-24T10:00:00Z",
        {"id": "execution_state", "display_name": "执行状态"}, AGENT, [{"event_id": MET, "ref": f"event:{MET}"}],
        cited(ACTIVITY, 2))
    issues = next(block for block in state["blocks"] if block["id"] == "issues")
    assert [(item["ref"], item["pinned"]) for item in issues["components"]] == [
        (f"{SNAP_ACTIVITY}@1#issues/iss-1", cited(SNAP_ACTIVITY, 1, "issues", "iss-1"))]
    assert section(result["context_pack"]["markdown"], f"{SNAP_ACTIVITY}@1") == [
        f"### 最新状态快照（未经确认，截至 2026-09-24T10:00:00Z） `{SNAP_ACTIVITY}@1`",
        f"payload：执行状态；周期：2026-10；生成者：E&O Agent；来源事件：`event:{MET}`",
        f"#### 进展 `{SNAP_ACTIVITY}@1#progress`",
        f"- 进展条目 `{SNAP_ACTIVITY}@1#progress/todo:17`：改了一半",
        f"  本体主体 id：`{AGENT}`", "  姓名（写入时）：E&O Agent", "  外部状态：进行中",
        "  本期条目：2026-09-24T09:00:00Z Codex：改完第一节",
        f"#### 阻塞与偏差 `{SNAP_ACTIVITY}@1#blockers`", "当前没有阻塞与偏差",
        f"#### 问题 `{SNAP_ACTIVITY}@1#issues`",
        f"- 问题 `{SNAP_ACTIVITY}@1#issues/iss-1`：评审意见有冲突", "  核心判断问题：按哪条意见改？",
        f"  最低充分责任主体：`{IC}`",
        f"#### 材料 `{SNAP_ACTIVITY}@1#materials`", "当前没有材料"]
    # 快照只取当前对象与它向上直到 Mission 的执行链；Task 没有快照。
    assert [layer["state"] and layer["state"]["ref"] for layer in result["context_pack"]["layers"]] == [
        f"{SNAP_ACTIVITY}@1", None, f"{SNAP_MISSION}@1", None, None, None, None, None]
    assert result["plan"]["state_and_events_from_levels"] == [0, 1, 2]


def test_lifecycle_follows_the_0_2_state_table_and_formal_the_kernel_pointer(world):
    objects = {layer["object"]["object_type"]: layer["object"] for layer in build(world)["context_pack"]["layers"]}
    assert {name: (obj["lifecycle"] and (obj["lifecycle"]["status"], obj["lifecycle"]["event_id"]), obj["formal"])
            for name, obj in objects.items()} == {
        "Activity": (("in_progress", STARTED), None), "Task": (("assigned", uid(52)), None),
        "Mission": (("established", CONFIRMED), True), "PeriodGoal": (("confirmed", uid(57)), True),
        "LongTermGoal": (("confirmed", uid(59)), True), "ResponsibilityUnit": (None, None),
        "Strategy": (("draft", uid(60)), False), "Company": (None, None)}
    assert objects["Mission"]["lifecycle"] == {"status": "established", "display_name": "已成立", "event_id": CONFIRMED}


def test_responsible_follows_the_read_projection_and_names_its_source(world):
    result = build(world)
    layers = result["context_pack"]["layers"]
    assert layers[0]["object"]["responsible"] == {
        "source": "attribute", "roles": {"agent": "AGENT", "human": "IC"},
        "principals": [{"principal_id": AGENT, "principal_type": "agent", "display_name": "E&O Agent"}]}
    assert layers[3]["object"]["responsible"] == {
        "source": "role", "role": "DOMAIN_DRI",
        "principals": [{"principal_id": DRI, "principal_type": "human", "display_name": "E&O DRI"}]}
    markdown = result["context_pack"]["markdown"]
    assert section(markdown, f"{ACTIVITY}@2")[2] == "责任人（来自属性 responsible）：E&O Agent"
    assert section(markdown, f"{PERIOD}@1")[1:] == ["生命周期：已确认（事件 `event:" + uid(57) + "`）",
                                                    "责任人（来自角色 DOMAIN_DRI）：E&O DRI", "正式内容：已确认"]


def test_cross_chain_relations_are_listed_and_not_followed(world):
    result = build(world)
    mission = result["context_pack"]["layers"][2]
    assert [(item["field"], [ref["ref"] for ref in item["refs"]]) for item in mission["relations"]] == [
        ("depends_on", []), ("contributes_to", [f"{OTHER_GOAL}@1"])]
    assert [(item["field"], item["source"]["ref"]) for item in mission["referenced_by"]] == [
        ("depends_on", f"{OTHER_MISSION}@1")]
    assert result["plan"]["shown_not_followed"] == [
        {"from": f"{MISSION}@2", "field": "contributes_to", "to": f"{OTHER_GOAL}@1"},
        {"from": f"{OTHER_MISSION}@1", "field": "depends_on", "to": f"{MISSION}@2"}]
    assert "跨链关系（只列引用，不展开）：\n" + f"contributes_to：`{OTHER_GOAL}@1`\n被 `{OTHER_MISSION}@1` 以 depends_on 引用" \
        in result["context_pack"]["markdown"]


def test_coverage_cites_components_blocks_and_events_that_the_pack_holds(world):
    coverage = build(world)["coverage"]
    assert coverage["what"]["evidence"] == [{"ref": f"{ACTIVITY}@2#instruction"}]
    assert coverage["who"]["evidence"] == [{"ref": f"{ACTIVITY}@2"}]
    assert coverage["now"]["evidence"] == [{"ref": f"{SNAP_ACTIVITY}@1"}, {"ref": f"{SNAP_MISSION}@1"}]
    assert coverage["happened"]["evidence"] == [{"ref": f"event:{event_id}"} for event_id in (
        STARTED, MET, ASSIGNED, CREATED, CORRECTED, REFRESHED, CONFIRMED, LATE)]
    basis = coverage["basis"]["evidence"]
    assert {"ref": f"{TASK}@2#acceptance/ac-1"} in basis and {"ref": f"{MISSION}@2#acceptance/m-ac1"} in basis
    assert {"ref": f"{MISSION}@2#definition"} in basis and {"ref": f"{GOAL}@1#outcome/lt-o1"} in basis
    # 活动块不是经确认的正式内容；草稿 Strategy 的块只是 Why。
    assert {"ref": f"{MISSION}@2#execution_plan/plan-1"} not in basis
    assert {"ref": f"{STRATEGY}@1#choices"} not in basis and {"ref": f"{STRATEGY}@1#choices"} in coverage["why"]["evidence"]
    assert {"ref": f"{STRATEGY}@1#responsibility_structure/unit-eo"} in coverage["why"]["evidence"]
    assert all(answer["answered"] for answer in coverage.values())


def test_the_defaults_and_the_budget_record_follow_0_1(world):
    result = build(world)
    markdown = result["context_pack"]["markdown"]
    assert result["budget"] == {"max_chars": 12000, "max_events_per_object": 10, "recent_days": 30,
                                "window_start": "2026-08-25T00:00:00Z", "used_chars": len(markdown),
                                "estimated_tokens": math.ceil(len(markdown) / 2), "over_budget": False}
    assert markdown.startswith(f"# 上下文\n\n问题：为什么要做这条 Activity？\n\n出发对象：`{ACTIVITY}@2`\n\n## 当前对象：")
    assert result["plan"]["trimmed"] == [] and result["plan"]["over_budget"] is False


def test_a_tight_budget_trims_old_events_first_then_the_farthest_levels_and_keeps_the_current_object(world):
    whole = build(world)
    tight = build(world, budget={"max_chars": whole["budget"]["used_chars"] // 2})
    trimmed = tight["plan"]["trimmed"]
    moments = {item["ref"]: item["occurred_at"] for layer in whole["context_pack"]["layers"] for item in layer["events"]}
    events = [entry["key"] for entry in trimmed if entry["kind"] == "event"]
    rest = [entry["level"] for entry in trimmed if entry["kind"] != "event"]
    assert trimmed[:len(events)] == [entry for entry in trimmed if entry["kind"] == "event"]
    assert [moments[key] for key in events] == sorted(moments[key] for key in events) and len(events) == len(moments)
    assert rest and rest == sorted(rest, reverse=True) and 0 not in rest
    assert {entry["reason"] for entry in trimmed} == {"over_budget"} and tight["budget"]["over_budget"] is False
    assert tight["budget"]["used_chars"] == len(tight["context_pack"]["markdown"]) <= whole["budget"]["used_chars"] // 2
    tiny = build(world, budget={"max_chars": 10})
    for pack in (tight, tiny):
        current = pack["context_pack"]["layers"][0]
        assert [block["ref"] for block in current["blocks"]] == [f"{ACTIVITY}@2#instruction", f"{ACTIVITY}@2#constraint"]
        assert current["state"] == whole["context_pack"]["layers"][0]["state"]
    assert tiny["budget"]["over_budget"] is True and all(layer["blocks"] == [] for layer in tiny["context_pack"]["layers"][1:])
    # 覆盖只看留下的内容。
    assert not tiny["coverage"]["why"]["answered"] and not tiny["coverage"]["happened"]["answered"]


def test_the_per_object_event_cap_keeps_the_newest_events(world):
    result = build(world, budget={"max_events_per_object": 1})
    assert [[item["event_id"] for item in layer["events"]] for layer in result["context_pack"]["layers"][:3]] == [
        [STARTED], [], [CORRECTED]]
    assert sorted((entry["key"], entry["reason"]) for entry in result["plan"]["trimmed"]) == sorted(
        (f"event:{event_id}", "over_level_cap") for event_id in (MET, ASSIGNED, CREATED, REFRESHED, CONFIRMED, LATE))


def test_the_same_world_gives_the_same_pack(world):
    first, second = build(world), build(world)
    assert {key: first[key] for key in ("context_pack", "plan", "coverage", "budget")} == {
        key: second[key] for key in ("context_pack", "plan", "coverage", "budget")}


def test_each_call_records_one_row_holding_what_was_returned(world):
    result = build(world)
    scope, principal, object_id, question, pack, plan, coverage, budget = world.inserted
    assert (scope, principal, object_id, question) == (uid(0), AGENT, ACTIVITY, "为什么要做这条 Activity？")
    assert (pack.obj, plan.obj, coverage.obj, budget.obj) == (result["context_pack"], result["plan"], result["coverage"],
                                                              result["budget"])
    assert (result["context_pack_id"], result["created_at"], result["object_id"]) == (uid(99), "2026-09-24T12:00:00Z",
                                                                                      ACTIVITY)


def test_starting_from_a_snapshot_takes_its_subject_as_the_current_object_and_that_snapshot_as_its_state(world):
    result = build(world, start=SNAP_MISSION, question="Mission 现在怎样？")
    layers = result["context_pack"]["layers"]
    assert result["context_pack"]["start"] == f"{SNAP_MISSION}@1" and result["object_id"] == SNAP_MISSION
    assert [layer["object"]["object_type"] for layer in layers] == registry.registry()["spine"][2:]
    assert layers[0]["state"]["ref"] == f"{SNAP_MISSION}@1"
    assert result["coverage"]["now"]["evidence"] == [{"ref": f"{SNAP_MISSION}@1"}]


def test_an_unknown_object_is_not_found_and_records_nothing(world):
    with pytest.raises(GovernedError) as error:
        build(world, start=uid(77))
    assert error.value.code == "NOT_FOUND" and world.inserted is None


# ------------------------------------------------------------ HTTP：按对象绑定的契约版本分派
def test_the_context_endpoint_serves_each_object_under_the_contract_it_is_bound_to(monkeypatch):
    """0.2 对象交给 0.2 的组装，0.1 对象仍交给 0.1 的组装（输出不变）；找不到（含 scope 外）是 404。"""
    from memory_service_runtime.governed import db, routes
    v01, v02, elsewhere = uid(101), uid(102), uid(103)
    bound = {v01: "tkos.world/0.1", v02: V02}

    @contextmanager
    def transaction(token):
        yield "conn", SimpleNamespace(scope_id=uid(0), principal_id=AGENT)

    def bound_contract(conn, ctx, object_id):
        if object_id not in bound:
            raise GovernedError("NOT_FOUND")
        return bound[object_id]

    def built_by(version):
        def fake(conn, ctx, object_id, request):
            return {"built_by": version, "object_id": object_id, "question": request.question}
        return fake

    monkeypatch.setattr(db, "transaction", transaction)
    monkeypatch.setattr(readers, "bound_contract", bound_contract)
    monkeypatch.setattr(world_v01_context, "build", built_by("0.1"))
    monkeypatch.setattr(context, "build", built_by("0.2"))
    app = FastAPI()
    app.include_router(routes.router)
    routes.install_errors(app)
    client = TestClient(app)

    def post(object_id):
        return client.post(f"/v1/world/objects/{object_id}/context", json={"question": "为什么？"},
                           headers={"Authorization": "Bearer synthetic"})

    for object_id, version in ((v01, "0.1"), (v02, "0.2")):
        response = post(object_id)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert response.json() == {"built_by": version, "object_id": object_id, "question": "为什么？"}
    missing = post(elsewhere)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
