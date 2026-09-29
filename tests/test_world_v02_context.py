"""tkos.world/0.2 的取上下文（票 #56、#64、#70，契约第 15.3 节）：组件级引用与事件引用，0.2 的生命周期、正式内容、快照外壳
与责任人；六问覆盖、检索计划与落表沿用 0.1（票 #25）。#64 第一段：单元长期目标沿 goal_ref 多取一跳到
公司级长期目标，Why 追到公司级长期目标、Strategy 与 Company；Markdown 开头是六问指引、以六问为节；事件行写出
记录者、被代记的人与被指派者。#70：默认预算 100000 字符；裁剪保护 Why 链（补 48）；每对象事件上限不计当前对象
最近一次指派事件与推出它当前生命周期的事件。不连数据库。

覆盖判定是纯函数；组装在假连接上的一条 0.2 主干上做：读投影里取对象、快照与事件的三个入口换成按下表查的替身，
生命周期与责任人走 0.2 读侧的真代码，由假连接按语句应答。HTTP 路径（从 Activity 出发、引用逐条读回、scope 外
404、落表、确定性、0.1 对象不变；从单元周期目标出发的 Why、代记事件行）在 acceptance/world_v02 的 context_packs
与 context_fill 场景里。
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
# Activity → Task → Mission → 周期目标 → 单元长期目标 → 责任单元（架构引用指到责任单元条目）→ Strategy → Company；
# 单元长期目标的 goal_ref 指向公司级长期目标（主干之外多取的一跳）。Mission、周期目标与两条长期目标已正式；
# Activity 与 Mission 各有一条快照，Task 没有；事件八条，Mission 的确认由天枢代 DRI 记。
def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


COMPANY, STRATEGY, UNIT, GOAL, PERIOD, MISSION, TASK, ACTIVITY = (uid(n) for n in range(1, 9))
COMPANY_GOAL, OTHER_GOAL, OTHER_MISSION = uid(9), uid(10), uid(11)
GOAL_2, GOAL_ENDED, GOAL_DRAFT = uid(12), uid(13), uid(14)
SNAP_ACTIVITY, SNAP_MISSION = uid(21), uid(23)
CEO, DRI, OWNER, IC, AGENT, TIANSHU = (uid(n) for n in range(31, 37))
CREATED, ASSIGNED, CONFIRMED, MET, STARTED, LATE, REFRESHED, CORRECTED = (uid(n) for n in range(41, 49))
PEOPLE = {CEO: ("CEO", "human"), DRI: ("E&O DRI", "human"), OWNER: ("Mission Owner", "human"),
          IC: ("方案 IC", "human"), AGENT: ("E&O Agent", "agent"), TIANSHU: ("天枢", "agent")}
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
    COMPANY_GOAL: ("LongTermGoal", "company", 1, "confirmed",
                   {"title": "公司三年目标", "scope": "company", "horizon": "2028", "external_refs": [],
                    "parent_ref": pin(COMPANY), "goal_ref": None},
                   {"outcome": value(components=[part("cg-o1", "outcome", "成为企业经营系统的首选")]),
                    "constraint": value("公司级目标自己的约束")}),
    GOAL: ("LongTermGoal", "eo", 1, "confirmed",
           {"title": "E&O 六个月目标", "scope": "unit", "horizon": "六个月", "external_refs": [], "parent_ref": pin(UNIT),
            "goal_ref": pin(COMPANY_GOAL)},
           {"outcome": value(components=[part("lt-o1", "outcome", "建立 Enterprise Context",
                                              refs=[pin(COMPANY_GOAL, 1, "outcome", "cg-o1")])])}),
    # 本单元另外三条长期目标：已确认、已终止、草稿（形成周期目标时只带入已确认的）。
    GOAL_2: ("LongTermGoal", "eo", 1, "confirmed",
             {"title": "E&O 客户目标", "scope": "unit", "horizon": "一年", "external_refs": [], "parent_ref": pin(UNIT),
              "goal_ref": None},
             {"outcome": value(components=[part("g2-o1", "outcome", "两家标杆客户")])}),
    GOAL_ENDED: ("LongTermGoal", "eo", 1, "confirmed",
                 {"title": "已终止的目标", "scope": "unit", "horizon": "一年", "external_refs": [],
                  "parent_ref": pin(UNIT), "goal_ref": None}, {"outcome": value("不再追")}),
    GOAL_DRAFT: ("LongTermGoal", "eo", 1, "draft",
                 {"title": "草稿目标", "scope": "unit", "horizon": "一年", "external_refs": [],
                  "parent_ref": pin(UNIT), "goal_ref": None}, {"outcome": value("还没确认")}),
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
    event(CONFIRMED, "confirm", TIANSHU, "2026-09-22T02:00:00Z", [pin(MISSION, 2)], outcome="accepted",
          on_behalf_of={"principal_id": DRI, "display_name": "E&O DRI"},
          external_confirmation={"external_record_id": "tianshu:confirm:1",
                                 "external_confirmed_at": "2026-09-22T01:55:00Z"}),
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
    COMPANY_GOAL: [(uid(61), "world_create_object"), (uid(62), "world_confirm_long_term_goal", "accepted")],
    GOAL_2: [(uid(63), "world_create_object"), (uid(64), "world_confirm_long_term_goal", "accepted")],
    GOAL_ENDED: [(uid(65), "world_create_object"), (uid(66), "world_confirm_long_term_goal", "accepted"),
                 (uid(67), "world_cancel")],
    GOAL_DRAFT: [(uid(68), "world_create_object")],
    STRATEGY: [(uid(60), "world_create_object")],
}

# 问题（#61 的 Issue 事件，#64 第二段带入）：单元长期目标快照里三条已处置、一条还在路由；Mission 快照里一条立即重开。
# 待带入：处置为带入下次形成或立即重开，且此后主受影响对象没记过门事件（g-old 之后长期目标又记了一条再确认）。
SNAP_GOAL, SNAP_MISSION_ISSUES = uid(24), uid(25)
G_ISS, G_EARLY, G_OLD, G_CLOSED, G_OPEN, M_ISS = (uid(n) for n in range(71, 77))


def issue_part(cid: str, text: str, question: str) -> dict:
    return part(cid, "issue", text, attributes={"core_question": question})


ISSUE_SNAPSHOTS = {  # 修订 id -> (对象类型, 修订载荷)
    rev(SNAP_GOAL, 3): ("StateSnapshot", {"title": "目标状态", "blocks": {"issues": value(components=[
        issue_part("g-iss", "试点客户流失", "下个周期要不要换客户群？"),
        issue_part("g-early", "衡量口径不一", "收入按签约还是按回款算？"),
        issue_part("g-old", "旧问题", "已经在再确认里处理了吗？"),
        issue_part("g-closed", "不处理的问题", "要不要处理？"), issue_part("g-open", "还在路由的问题", "谁来接？")])}}),
    rev(SNAP_MISSION_ISSUES, 1): ("StateSnapshot", {"title": "执行状态", "blocks": {"issues": value(components=[
        issue_part("m-iss", "打法不成立", "要不要立即重开立项？")])}}),
}
ISSUE_EVENTS = {  # 主受影响对象 -> issue_events 给出的事件（按记录顺序）
    GOAL: [{"event_id": event_id, "action": action, "outcome": None, "disposition": disposition,
            "supersedes_event_id": None, "principal_id": DRI, "detail": None, "component_id": cid}
           for cid, events in (("g-iss", [(uid(80), "world_raise_issue", None), (uid(81), "world_route_issue", None),
                                          (uid(82), "world_own_issue", None), (G_ISS, "world_dispose_issue", "roll_forward")]),
                               ("g-early", [(uid(83), "world_raise_issue", None), (uid(84), "world_route_issue", None),
                                            (uid(85), "world_own_issue", None),
                                            (G_EARLY, "world_dispose_issue", "roll_forward")]),
                               ("g-old", [(uid(86), "world_raise_issue", None), (uid(87), "world_route_issue", None),
                                          (uid(88), "world_own_issue", None), (G_OLD, "world_dispose_issue", "roll_forward")]),
                               ("g-closed", [(uid(89), "world_raise_issue", None), (uid(90), "world_route_issue", None),
                                             (uid(91), "world_own_issue", None),
                                             (G_CLOSED, "world_dispose_issue", "no_action_close")]),
                               ("g-open", [(G_OPEN, "world_raise_issue", None)]))
           for event_id, action, disposition in events],
    MISSION: [{"event_id": event_id, "action": action, "outcome": None, "disposition": disposition,
               "supersedes_event_id": None, "principal_id": OWNER, "detail": None, "component_id": "m-iss"}
              for event_id, action, disposition in ((uid(92), "world_raise_issue", None),
                                                    (uid(93), "world_route_issue", None),
                                                    (uid(94), "world_own_issue", None),
                                                    (M_ISS, "world_dispose_issue", "immediate_reopen"))],
}
# 本 scope 最近的已确认公司复盘（#60 的 confirmed_company_review 给出的形状）。
REVIEW_SNAPSHOT, REVIEW_EVENT = uid(26), uid(95)


def company_review() -> dict:
    payload = {"title": "九月公司复盘", "subject_ref": pin(COMPANY), "as_of": "2026-09-30T15:59:59Z", "period": "2026-09",
               "payload_type": "company_review", "source_event_refs": [{"event_id": MET}], "generator": CEO,
               "blocks": {"results": value("营收达成八成"), "gaps": value("交付慢两周"), "causes": None,
                          "key_changes": value("换了交付负责人"), "implications": value("十月先补交付"),
                          "materials": value("复盘草稿", artifacts=["https://docs.example/review-draft"])}}
    row = {"revision_id": rev(REVIEW_SNAPSHOT, 1), "object_id": REVIEW_SNAPSHOT, "object_version": 1, "payload": payload}
    return {"event_id": REVIEW_EVENT, "ref": f"event:{REVIEW_EVENT}", "confirmed_at": "2026-10-01T02:00:00Z",
            "principal": {"principal_id": CEO, "principal_type": "human", "display_name": "CEO"}, "on_behalf_of": None,
            "snapshot": readers.snapshot_view({"object_id": REVIEW_SNAPSHOT}, row, generator={
                "principal_id": CEO, "principal_type": "human", "display_name": "CEO"})}


# 处置事件：发生时刻、理由、subject_refs（问题组件、主受影响对象）、记录者，此后主受影响对象是否记过门事件。
DISPOSALS = {
    G_ISS: ("2026-09-23T08:00:00Z", "客户流失原因要在下个周期回答", SNAP_GOAL, 3, "g-iss", GOAL, 1, DRI, False),
    G_EARLY: ("2026-09-23T06:00:00Z", "口径在形成时统一", SNAP_GOAL, 3, "g-early", GOAL, 1, DRI, False),
    G_OLD: ("2026-09-20T06:00:00Z", "旧的", SNAP_GOAL, 3, "g-old", GOAL, 1, DRI, True),
    M_ISS: ("2026-09-24T09:00:00Z", "打法前提不成立", SNAP_MISSION_ISSUES, 1, "m-iss", MISSION, 2, OWNER, False),
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
        if sql.startswith("SELECT * FROM gov_objects WHERE scope_id=%s AND domain_id=%s"):
            return [head(oid) for oid, spec in OBJECTS.items() if spec[1] == params[1] and spec[0] in params[2]]
        if sql.startswith("SELECT e.event_id, e.occurred_at, e.content, e.subject_refs"):
            at, reason, snap, snap_version, cid, primary, version, who, gated = DISPOSALS[params[-1]]
            return [{"event_id": params[-1], "occurred_at": datetime.fromisoformat(at.replace("Z", "+00:00")),
                     "content": value(reason), "principal_id": who, "gated_since": gated,
                     "subject_refs": [pin(snap, snap_version, "issues", cid), pin(primary, version)]}]
        if sql.startswith("SELECT o.object_type, r.payload FROM gov_object_revisions"):
            if params[1] in ISSUE_SNAPSHOTS:
                object_type, payload = ISSUE_SNAPSHOTS[params[1]]
                return [{"object_type": object_type, "payload": payload}]
            oid = next(oid for oid in OBJECTS if revision(oid)["revision_id"] == params[1])
            return [{"object_type": OBJECTS[oid][0], "payload": revision(oid)["payload"]}]
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
    monkeypatch.setattr(readers, "issue_events", lambda conn, ctx, primary_id, component_id=None: ISSUE_EVENTS.get(
        primary_id, []))
    monkeypatch.setattr(readers, "confirmed_company_review", lambda conn, ctx: company_review())
    return Conn()


def build(conn, start=ACTIVITY, question="为什么要做这条 Activity？", **request):
    return context.build(conn, SimpleNamespace(scope_id=uid(0), principal_id=AGENT), start,
                         WorldContextRequest.model_validate({"question": question, **request}))


def section(markdown: str, ref: str) -> list[str]:
    """Markdown 里以带这个引用的标题开头的那一段，逐行。"""
    return next(item for item in markdown.split("\n\n") if item.startswith("#") and f"`{ref}`" in item.splitlines()[0]
                ).splitlines()


def sections(markdown: str) -> dict[str, str]:
    """以「## 」起头的各节：节名 -> 节里的正文（去掉首尾空行）。"""
    return {part.split("\n", 1)[0]: (part.split("\n", 1) + [""])[1].strip("\n")
            for part in ("\n" + markdown).split("\n## ")[1:]}


def heads(body: str) -> list[str]:
    """一节里逐条内容的首行（条目之间空一行）。"""
    return [chunk.splitlines()[0] for chunk in body.split("\n\n") if chunk]


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
        f"### Task·验收标准 `{TASK}@2#acceptance`",
        f"- 验收条件 `{TASK}@2#acceptance/ac-1`：方案评审通过",
        f"  引用：`{MISSION}@2#acceptance/m-ac1`",
        f"- 验收条件 `{TASK}@2#acceptance/ac-2`：演示通过"]
    # 块自己的文字、引用与组件都在；组件的类型属性按登记的显示名写出。
    assert section(markdown, f"{ACTIVITY}@2#instruction") == [
        f"### Activity·执行指令 `{ACTIVITY}@2#instruction`", "按评审意见改材料", f"引用：`{TASK}@2#acceptance/ac-1`"]
    assert section(markdown, f"{MISSION}@2#execution_plan") == [
        f"### Mission·执行计划 `{MISSION}@2#execution_plan`", f"- 计划条目 `{MISSION}@2#execution_plan/plan-1`：搭环境",
        f"  责任人（只作记录）：`{OWNER}`"]
    assert section(markdown, f"{TASK}@2#constraint") == [f"### Task·约束 `{TASK}@2#constraint`", "当前没有约束"]


def test_events_are_cited_by_event_reference_newest_first_and_once_at_the_nearest_level(world):
    result = build(world)
    layers = result["context_pack"]["layers"]
    assert [[item["event_id"] for item in layer["events"]] for layer in layers[:3]] == [
        [STARTED, MET, ASSIGNED, CREATED], [], [CORRECTED, REFRESHED, CONFIRMED, LATE]]
    assert all(item["ref"] == f"event:{item['event_id']}" for layer in layers for item in layer["events"])
    lines = result["context_pack"]["markdown"].splitlines()
    assert f"- 2026-09-23T02:23:00Z Activity 外部事件·会议（事件 `event:{MET}`，方案 IC 记）：评审方案" in lines
    # 迟记与更正、撤回所指的原事件照取事件写出。
    assert f"- 2026-09-21T00:00:00Z Mission 外部事件·会议（事件 `event:{LATE}`，Mission Owner 记，迟记）：立项前沟通" in lines
    assert (f"- 2026-09-24T12:30:00Z Mission 外部事件·更正（事件 `event:{CORRECTED}`，Mission Owner 记，"
            f"原事件 `event:{LATE}`）：沟通会的日期记错了" in lines)
    # 生命周期也以事件引用指出推出它的事件。
    assert f"- 当前对象 Activity 生命周期：进行中（事件 `event:{STARTED}`）" in lines


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
        f"### Activity 的最新状态快照（未经确认，截至 2026-09-24T10:00:00Z） `{SNAP_ACTIVITY}@1`",
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
    parts = sections(result["context_pack"]["markdown"])
    # 「谁负责」逐层写出责任人与来源，「现在怎样」逐层写出生命周期与正式内容。
    assert parts["谁负责"].splitlines() == [
        f"- 当前对象 Activity《改建模材料》 `{ACTIVITY}@2` 责任人（来自属性 responsible）：E&O Agent",
        f"- 上溯第 1 层 Task《方案设计》 `{TASK}@2` 责任人（来自属性 responsible）：方案 IC",
        f"- 上溯第 2 层 Mission《Agent 真实可用》 `{MISSION}@2` 责任人（来自属性 responsible）：Mission Owner",
        f"- 上溯第 3 层 周期目标《E&O 10 月》 `{PERIOD}@1` 责任人（来自角色 DOMAIN_DRI）：E&O DRI",
        f"- 上溯第 4 层 长期目标《E&O 六个月目标》 `{GOAL}@1` 责任人（来自角色 CEO）：CEO",
        f"- 上溯第 5 层 责任单元《E&O》 `{UNIT}@1` 责任人（来自角色 DOMAIN_DRI）：E&O DRI",
        f"- 上溯第 6 层 战略《总体战略》 `{STRATEGY}@1` 责任人（来自角色 CEO）：CEO",
        f"- 上溯第 7 层 公司《词元云集》 `{COMPANY}@1` 责任人（来自角色 CEO）：CEO"]
    assert parts["现在怎样"].split("\n\n")[0].splitlines() == [
        f"- 当前对象 Activity 生命周期：进行中（事件 `event:{STARTED}`）",
        f"- 上溯第 1 层 Task 生命周期：已指派（事件 `event:{uid(52)}`）",
        f"- 上溯第 2 层 Mission 生命周期：已成立（事件 `event:{CONFIRMED}`），正式内容：已确认",
        f"- 上溯第 3 层 周期目标 生命周期：已确认（事件 `event:{uid(57)}`），正式内容：已确认",
        f"- 上溯第 4 层 长期目标 生命周期：已确认（事件 `event:{uid(59)}`），正式内容：已确认",
        "- 上溯第 5 层 责任单元 生命周期：无（只有版本）",
        f"- 上溯第 6 层 战略 生命周期：草稿（事件 `event:{uid(60)}`），正式内容：尚未确认",
        "- 上溯第 7 层 公司 生命周期：无（只有版本）"]


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
    # 跨链关系跟着它那一层放在「为什么」一节，在该层的定义类块之前。
    assert (f"Mission 的跨链关系（只列引用，不展开）：\ncontributes_to：`{OTHER_GOAL}@1`\n"
            f"被 `{OTHER_MISSION}@1` 以 depends_on 引用") in sections(result["context_pack"]["markdown"])["为什么"].split("\n\n")


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


def test_the_default_budget_is_100000_chars_and_the_budget_record_keeps_its_shape(world):
    """决 18（#70）：0.2 预算只报成本、不作门，默认 100000 字符；每个对象 10 条事件、近期 30 天沿用 0.1。"""
    assert (context.DEFAULT_MAX_CHARS, context.DEFAULT_MAX_EVENTS_PER_OBJECT, context.DEFAULT_RECENT_DAYS) == (
        100000, 10, 30)
    result = build(world)
    markdown = result["context_pack"]["markdown"]
    assert result["budget"] == {"max_chars": 100000, "max_events_per_object": 10, "recent_days": 30,
                                "window_start": "2026-08-25T00:00:00Z", "used_chars": len(markdown),
                                "estimated_tokens": math.ceil(len(markdown) / 2), "over_budget": False}
    assert markdown.startswith(f"# 上下文\n\n问题：为什么要做这条 Activity？\n\n出发对象：`{ACTIVITY}@2`\n\n## 六问指引\n")
    assert result["plan"]["trimmed"] == [] and result["plan"]["over_budget"] is False
    # 请求里给的预算照旧生效。
    assert build(world, budget={"max_chars": 12000})["budget"]["max_chars"] == 12000


def why_chain(pack: dict) -> set[str]:
    """按契约补 48 从一个没裁过的包算 Why 链上的项：上层的定义类块、多取的一跳，以及被层号更小的块值引用或组件引用
    钉到的上层块（与实现各自算，只用包里读回的字段）。"""
    layers = pack["layers"]
    where = {(layer["object"]["object_id"], block["id"]): (layer["level"], f"block:{block['ref']}")
             for layer in layers if layer["level"] > 0 for block in layer["blocks"]}
    found = {f"hop:{layer['hop']['object']['ref']}" for layer in layers if layer["hop"]}
    found |= {f"block:{block['ref']}" for layer in layers[1:] for block in layer["blocks"] if block["kind"] == "definition"}
    for layer in layers:
        for part in (layer, layer["hop"]):
            for block in part["blocks"] if part else []:
                for pinned in (block["value"]["refs"] if block["value"] else []) + [
                        ref for item in block["components"] for ref in item["refs"]]:
                    target = where.get((pinned.get("object_id"), pinned.get("block")))
                    if target and target[0] > layer["level"]:
                        found.add(target[1])
    return found


def step(entry: dict, why: set[str]) -> int:
    """一条裁剪记录落在哪一步（契约补 48）：1 上层不在 Why 链上的块，2 事件，3 其余内容，4 Why 链上的项。"""
    if entry["kind"] == "event":
        return 2
    if entry["key"] in why:
        return 4
    return 1 if entry["kind"] == "block" else 3


def in_the_0_2_order(whole: dict, result: dict) -> bool:
    """裁剪记录按四步排列：步号不减；事件由旧到新；其余三步各自由远及近（当前对象的跨链关系在第 3 步最后）；
    当前对象的块与快照不在其中。"""
    why = why_chain(whole["context_pack"])
    moments = {item["ref"]: context._moment({"occurred_at": item["occurred_at"], "key": item["ref"]})
               for layer in whole["context_pack"]["layers"] for item in layer["events"]}
    over = [entry for entry in result["plan"]["trimmed"] if entry["reason"] == "over_budget"]
    steps = [step(entry, why) for entry in over]
    by_step = {n: [entry for entry in over if step(entry, why) == n] for n in (1, 2, 3, 4)}
    return (steps == sorted(steps)
            and [moments[entry["key"]] for entry in by_step[2]] == sorted(moments[entry["key"]] for entry in by_step[2])
            and all([entry["level"] for entry in by_step[n]] == sorted((entry["level"] for entry in by_step[n]),
                                                                       reverse=True) for n in (1, 3, 4))
            and not [entry for entry in over if entry["level"] == 0 and entry["kind"] in {"block", "snapshot"}])


def test_a_tight_budget_trims_blocks_off_the_why_chain_then_old_events_then_the_rest_and_the_why_chain_last(world):
    whole = build(world)
    tight = build(world, budget={"max_chars": whole["budget"]["used_chars"] // 2})
    tiny = build(world, budget={"max_chars": 10})
    for result in (tight, tiny):
        assert in_the_0_2_order(whole, result)
        assert {entry["reason"] for entry in result["plan"]["trimmed"]} == {"over_budget"}
        assert result["budget"]["used_chars"] == len(result["context_pack"]["markdown"])
        # 当前对象的块与它的最新快照不裁。
        current = result["context_pack"]["layers"][0]
        assert [block["ref"] for block in current["blocks"]] == [f"{ACTIVITY}@2#instruction", f"{ACTIVITY}@2#constraint"]
        assert current["state"] == whole["context_pack"]["layers"][0]["state"]
    assert tight["budget"]["over_budget"] is False
    assert tight["budget"]["used_chars"] <= whole["budget"]["used_chars"] // 2
    assert tiny["budget"]["over_budget"] is True and all(layer["blocks"] == [] for layer in tiny["context_pack"]["layers"][1:])
    # 覆盖只看留下的内容。
    assert not tiny["coverage"]["why"]["answered"] and not tiny["coverage"]["happened"]["answered"]
    # 同样的世界与预算裁出同样的结果。
    again = build(world, budget={"max_chars": whole["budget"]["used_chars"] // 2})
    assert {key: tight[key] for key in ("context_pack", "plan", "coverage", "budget")} == {
        key: again[key] for key in ("context_pack", "plan", "coverage", "budget")}


def test_the_per_object_event_cap_keeps_the_newest_events_and_the_current_objects_assignment_and_lifecycle_event(
        world):
    """上限不计当前对象最近一次指派事件（ASSIGNED）与推出它当前生命周期的事件（STARTED，进行中）；其余事件按上限留
    最新的。Mission 不是当前对象，推出它生命周期的确认照样按上限裁。"""
    result = build(world, budget={"max_events_per_object": 1})
    assert [[item["event_id"] for item in layer["events"]] for layer in result["context_pack"]["layers"][:3]] == [
        [STARTED, MET, ASSIGNED], [], [CORRECTED]]
    assert sorted((entry["key"], entry["reason"]) for entry in result["plan"]["trimmed"]) == sorted(
        (f"event:{event_id}", "over_level_cap") for event_id in (CREATED, REFRESHED, CONFIRMED, LATE))


def test_newer_events_do_not_push_the_current_objects_latest_assignment_or_lifecycle_event_out_of_the_pack(world,
                                                                                                         monkeypatch):
    """当前对象又记了三条更新的事件（其中一条再指派）：上限 2 留最新的两条其余事件；最近一次指派（再指派那条）与推出
    生命周期的开始都留下，早先那次指派按上限裁；「谁负责」的指引仍指到最近一次指派。"""
    later = [uid(n) for n in (96, 97, 98)]
    monkeypatch.setitem(globals(), "EVENTS", EVENTS + [
        event(later[0], "assign", IC, "2026-09-25T00:00:00Z", [pin(ACTIVITY, 2)], detail={"principal_id": AGENT}),
        event(later[1], "event.recorded", IC, "2026-09-26T00:00:00Z", [pin(ACTIVITY, 2)], category="meeting", text="对齐"),
        event(later[2], "event.recorded", AGENT, "2026-09-27T00:00:00Z", [pin(ACTIVITY, 2)], category="meeting",
              text="再对齐")])
    assert context.uncapped_events(build(world)["context_pack"]["layers"]) == {f"event:{later[0]}", f"event:{STARTED}"}
    result = build(world, budget={"max_events_per_object": 2})
    assert [item["event_id"] for item in result["context_pack"]["layers"][0]["events"]] == [
        later[2], later[1], later[0], STARTED]
    assert sorted(entry["key"] for entry in result["plan"]["trimmed"] if entry["level"] == 0) == sorted(
        f"event:{event_id}" for event_id in (MET, ASSIGNED, CREATED))
    assert f"指派事件 `event:{later[0]}`" in sections(result["context_pack"]["markdown"])["六问指引"]


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


# ------------------------------------------------------------ #64 第一段：Why 多取一跳、按六问组织、事件行写人
def test_a_unit_goal_takes_one_hop_along_goal_ref_to_the_company_goals_definition_blocks(world):
    result = build(world)
    layers = result["context_pack"]["layers"]
    assert [layer["object"]["object_type"] for layer in layers] == registry.registry()["spine"]  # 主干本身不变
    hop = layers[4]["hop"]
    assert (hop["field"], hop["label"], hop["object"]["ref"], hop["object"]["pinned"], hop["object"]["formal"],
            hop["object"]["lifecycle"]["status"]) == (
        "goal_ref", "公司级长期目标", f"{COMPANY_GOAL}@1", cited(COMPANY_GOAL), True, "confirmed")
    # 只取定义类块：公司级目标自己的约束不随这一跳进来；空块照样读标准句；组件钉到所读修订。
    assert [(block["ref"], block["text"]) for block in hop["blocks"]] == [
        (f"{COMPANY_GOAL}@1#outcome", ""), (f"{COMPANY_GOAL}@1#measures", "当前没有衡量")]
    assert [(item["ref"], item["pinned"]) for item in hop["blocks"][0]["components"]] == [
        (f"{COMPANY_GOAL}@1#outcome/cg-o1", cited(COMPANY_GOAL, 1, "outcome", "cg-o1"))]
    assert [layer["hop"] for layer in layers if layer["level"] != 4] == [None] * 7
    assert result["plan"]["hops"] == [{"from": f"{GOAL}@1", "field": "goal_ref", "pinned": f"{COMPANY_GOAL}@1",
                                       "read": f"{COMPANY_GOAL}@1"}]
    assert {"kind": "hop", "level": 4, "key": f"hop:{COMPANY_GOAL}@1"} in result["plan"]["taken"]
    markdown = result["context_pack"]["markdown"]
    assert section(markdown, f"{COMPANY_GOAL}@1") == [
        f"### 沿 goal_ref 多取一跳：公司级长期目标《公司三年目标》 `{COMPANY_GOAL}@1`",
        f"生命周期：已确认（事件 `event:{uid(62)}`）", "责任人（来自角色 CEO）：CEO", "正式内容：已确认",
        f"#### 结果 `{COMPANY_GOAL}@1#outcome`", f"- 结果 `{COMPANY_GOAL}@1#outcome/cg-o1`：成为企业经营系统的首选",
        f"#### 衡量 `{COMPANY_GOAL}@1#measures`", "当前没有衡量"]
    assert "公司级目标自己的约束" not in markdown
    # 这一跳的组件是 Why 的依据；公司级目标已确认，它的正式块也算凭什么。
    assert {"ref": f"{COMPANY_GOAL}@1#outcome/cg-o1"} in result["coverage"]["why"]["evidence"]
    assert {"ref": f"{COMPANY_GOAL}@1#outcome/cg-o1"} in result["coverage"]["basis"]["evidence"]


def test_why_from_a_unit_period_goal_reaches_the_company_goal_the_strategy_and_the_company(world):
    result = build(world, start=PERIOD, question="这个周期目标为什么要做？")
    assert [layer["object"]["object_type"] for layer in result["context_pack"]["layers"]] == \
        registry.registry()["spine"][3:]
    # 覆盖：由近及远，单元长期目标、多取一跳的公司级长期目标、责任单元、Strategy、Company 的块或组件。
    assert result["coverage"]["why"]["evidence"] == [
        {"ref": f"{GOAL}@1#outcome/lt-o1"}, {"ref": f"{COMPANY_GOAL}@1#outcome/cg-o1"}, {"ref": f"{UNIT}@1#definition"},
        {"ref": f"{STRATEGY}@1#choices"}, {"ref": f"{STRATEGY}@1#responsibility_structure"},
        {"ref": f"{STRATEGY}@1#responsibility_structure/unit-eo"}, {"ref": f"{COMPANY}@1#identity"},
        {"ref": f"{GOAL_2}@1#outcome"}]  # 形成时带入的本单元有效长期目标，排在最后
    parts = sections(result["context_pack"]["markdown"])
    assert parts["六问指引"].splitlines()[1] == (
        f"- 为什么：长期目标 `{GOAL}@1#outcome` → 公司级长期目标 `{COMPANY_GOAL}@1#outcome` → "
        f"责任单元 `{UNIT}@1#definition` → 战略 `{STRATEGY}@1#choices`、`{STRATEGY}@1#responsibility_structure` → "
        f"公司 `{COMPANY}@1#identity`；形成时带入的有效长期目标 `{GOAL_2}@1`")
    assert heads(parts["为什么"]) == [
        f"### 长期目标·结果 `{GOAL}@1#outcome`", f"### 长期目标·衡量 `{GOAL}@1#measures`",
        f"### 沿 goal_ref 多取一跳：公司级长期目标《公司三年目标》 `{COMPANY_GOAL}@1`",
        f"### 责任单元·定义 `{UNIT}@1#definition`", f"### 责任单元·边界 `{UNIT}@1#boundary`",
        f"### 战略·战略选择 `{STRATEGY}@1#choices`", f"### 战略·路径 `{STRATEGY}@1#path`",
        f"### 战略·关键假设 `{STRATEGY}@1#assumptions`", f"### 战略·能力 `{STRATEGY}@1#capabilities`",
        f"### 战略·责任结构 `{STRATEGY}@1#responsibility_structure`", f"### 公司·身份 `{COMPANY}@1#identity`"]


def test_the_markdown_opens_with_the_guide_and_takes_the_six_questions_as_its_sections(world):
    markdown = build(world)["context_pack"]["markdown"]
    assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
        "## 六问指引", "## 为什么", "## 做什么", "## 谁负责", "## 现在怎样", "## 发生了什么", "## 凭什么"]
    parts = sections(markdown)
    # 为什么：当前对象之上各层（由近及远）的跨链关系与定义类块，单元长期目标之后是多取的一跳，一直到 Company。
    assert heads(parts["为什么"]) == [
        f"### Task·定义 `{TASK}@2#definition`", f"### Task·验收标准 `{TASK}@2#acceptance`",
        "Mission 的跨链关系（只列引用，不展开）：",
        f"### Mission·定义 `{MISSION}@2#definition`", f"### Mission·验收标准 `{MISSION}@2#acceptance`",
        f"### Mission·打法 `{MISSION}@2#play`",
        f"### 周期目标·结果 `{PERIOD}@1#outcome`", f"### 周期目标·实现逻辑 `{PERIOD}@1#realization_logic`",
        f"### 周期目标·验收标准 `{PERIOD}@1#acceptance`",
        f"### 长期目标·结果 `{GOAL}@1#outcome`", f"### 长期目标·衡量 `{GOAL}@1#measures`",
        f"### 沿 goal_ref 多取一跳：公司级长期目标《公司三年目标》 `{COMPANY_GOAL}@1`",
        f"### 责任单元·定义 `{UNIT}@1#definition`", f"### 责任单元·边界 `{UNIT}@1#boundary`",
        f"### 战略·战略选择 `{STRATEGY}@1#choices`", f"### 战略·路径 `{STRATEGY}@1#path`",
        f"### 战略·关键假设 `{STRATEGY}@1#assumptions`", f"### 战略·能力 `{STRATEGY}@1#capabilities`",
        f"### 战略·责任结构 `{STRATEGY}@1#responsibility_structure`", f"### 公司·身份 `{COMPANY}@1#identity`"]
    # 做什么：当前对象的定义类块，与各层的计划类块。
    assert heads(parts["做什么"]) == [f"### Activity·执行指令 `{ACTIVITY}@2#instruction`", f"### Task·计划 `{TASK}@2#plan`",
                                      f"### Mission·执行计划 `{MISSION}@2#execution_plan`"]
    # 现在怎样：各层的生命周期，然后是各层的最新状态快照。
    assert heads(parts["现在怎样"])[1:] == [
        f"### Activity 的最新状态快照（未经确认，截至 2026-09-24T10:00:00Z） `{SNAP_ACTIVITY}@1`",
        f"### Mission 的最新状态快照（未经确认，截至 2026-09-24T11:00:00Z） `{SNAP_MISSION}@1`"]
    # 发生了什么：各层的事件，由近及远，每层新的在前。
    assert [line.split("（事件 `")[1].split("`")[0] for line in parts["发生了什么"].split("\n\n")] == [
        f"event:{event_id}" for event_id in (STARTED, MET, ASSIGNED, CREATED, CORRECTED, REFRESHED, CONFIRMED, LATE)]
    # 凭什么：各层的约束类块。
    assert heads(parts["凭什么"]) == [f"### {name}·约束 `{oid}@{version}#constraint`" for name, oid, version in (
        ("Activity", ACTIVITY, 2), ("Task", TASK, 2), ("Mission", MISSION, 2), ("周期目标", PERIOD, 1),
        ("长期目标", GOAL, 1), ("责任单元", UNIT, 1), ("战略", STRATEGY, 1), ("公司", COMPANY, 1))]


def test_the_guide_answers_each_question_by_pointing_at_what_the_pack_holds(world):
    assert sections(build(world)["context_pack"]["markdown"])["六问指引"].splitlines() == [
        "按问题给出处，内容在下文各节。",
        f"- 为什么：Task `{TASK}@2#definition`、`{TASK}@2#acceptance` → Mission `{MISSION}@2#definition`、"
        f"`{MISSION}@2#acceptance` → 周期目标 `{PERIOD}@1#outcome`、`{PERIOD}@1#acceptance` → 长期目标 `{GOAL}@1#outcome` → "
        f"公司级长期目标 `{COMPANY_GOAL}@1#outcome` → 责任单元 `{UNIT}@1#definition` → 战略 `{STRATEGY}@1#choices`、"
        f"`{STRATEGY}@1#responsibility_structure` → 公司 `{COMPANY}@1#identity`",
        f"- 做什么：当前对象 `{ACTIVITY}@2#instruction`；Task `{TASK}@2#plan`；Mission `{MISSION}@2#execution_plan`",
        f"- 谁负责：当前对象 `{ACTIVITY}@2`：E&O Agent，指派事件 `event:{ASSIGNED}`（2026-09-22T01:00:00Z，方案 IC 指派给 "
        f"E&O Agent）；Task `{TASK}@2`：方案 IC；Mission `{MISSION}@2`：Mission Owner",
        f"- 现在怎样：当前对象 进行中（事件 `event:{STARTED}`）；最新快照 当前对象 `{SNAP_ACTIVITY}@1`、Mission "
        f"`{SNAP_MISSION}@1`（快照都未经确认）",
        f"- 发生了什么：外部事件 `event:{MET}`（会议）、`event:{CORRECTED}`（更正）、`event:{LATE}`（会议）；另有 5 条门、"
        "生命周期与其余记录事件",
        f"- 凭什么：验收标准与约束 `{TASK}@2#acceptance`、`{MISSION}@2#acceptance`、`{MISSION}@2#constraint`、"
        f"`{PERIOD}@1#acceptance`；上层已确认 Mission `{MISSION}@2`、周期目标 `{PERIOD}@1`、长期目标 `{GOAL}@1`、"
        f"公司级长期目标 `{COMPANY_GOAL}@1`"]


def test_event_lines_name_the_recorder_the_person_recorded_on_behalf_of_and_the_assignee(world):
    result = build(world)
    lines = result["context_pack"]["markdown"].splitlines()
    # 代记：记录者（天枢）与被代记的人（DRI）都写名字。
    assert f"- 2026-09-22T02:00:00Z Mission 确认·接受（事件 `event:{CONFIRMED}`，天枢 代 E&O DRI 记）" in lines
    # 指派另写被指派者；本人记的只写记录者。
    assert f"- 2026-09-22T01:00:00Z Activity 指派（事件 `event:{ASSIGNED}`，方案 IC 记，指派给 E&O Agent）" in lines
    assert f"- 2026-09-23T03:00:00Z Activity 开始（事件 `event:{STARTED}`，E&O Agent 记）" in lines
    # 名字也留在包里的事件上：记录者与被代记的人照取事件给出，指派事件另带被指派者，其余为空。
    events = {item["event_id"]: item for layer in result["context_pack"]["layers"] for item in layer["events"]}
    assert (events[CONFIRMED]["principal"]["display_name"], events[CONFIRMED]["on_behalf_of"]) == (
        "天枢", {"principal_id": DRI, "display_name": "E&O DRI"})
    assert events[ASSIGNED]["assignee"] == {"principal_id": AGENT, "principal_type": "agent", "display_name": "E&O Agent"}
    assert all(item["assignee"] is None for key, item in events.items() if key != ASSIGNED)


def blocks(oid, version, *ids):
    return [f"block:{oid}@{version}#{block}" for block in ids]


def test_trimming_protects_the_why_chain_and_takes_the_hop_before_its_levels_blocks(world):
    """契约补 48 的完整顺序：上层不在 Why 链上的块（约束、计划）由远及近、同层从后往前；再裁最旧的事件；再由远及近
    裁跨链关系与快照；Why 链上的项（上层的定义类块与多取的一跳）最后由远及近裁，同一层先裁一跳、再从后往前裁块。
    当前对象的块与最新快照不裁。"""
    result = build(world, budget={"max_chars": 10})
    assert [entry["key"] for entry in result["plan"]["trimmed"]] == [
        *blocks(COMPANY, 1, "constraint"), *blocks(STRATEGY, 1, "constraint"), *blocks(UNIT, 1, "constraint"),
        *blocks(GOAL, 1, "constraint"), *blocks(PERIOD, 1, "constraint"),
        *blocks(MISSION, 2, "constraint", "execution_plan"), *blocks(TASK, 2, "constraint", "plan"),
        *[f"event:{event_id}" for event_id in (LATE, CREATED, ASSIGNED, CONFIRMED, MET, STARTED, REFRESHED, CORRECTED)],
        "relations:2", f"snapshot:{SNAP_MISSION}@1",
        *blocks(COMPANY, 1, "identity"),
        *blocks(STRATEGY, 1, "responsibility_structure", "capabilities", "assumptions", "path", "choices"),
        *blocks(UNIT, 1, "boundary", "definition"),
        f"hop:{COMPANY_GOAL}@1", *blocks(GOAL, 1, "measures", "outcome"),
        *blocks(PERIOD, 1, "acceptance", "realization_logic", "outcome"),
        *blocks(MISSION, 2, "play", "acceptance", "definition"),
        *blocks(TASK, 2, "acceptance", "definition")]
    assert {entry["reason"] for entry in result["plan"]["trimmed"]} == {"over_budget"}
    assert result["context_pack"]["layers"][4]["hop"] is None and result["plan"]["hops"] != []


def test_a_long_why_block_outlasts_a_long_block_off_the_why_chain_and_every_event(world, monkeypatch):
    """Strategy 的战略选择（Why 链上）与约束（不在 Why 链上）都很长。预算只差约束那么多时，只裁上层的约束，事件与
    Why 链上的项都在（0.1 的顺序会先裁掉全部事件，再裁 Company 的身份）；预算再紧、要动到战略选择时，不在 Why 链上的
    块、全部事件、跨链关系与快照都已先裁掉，近处的 Why 链（Task、Mission 的定义类块）仍在。"""
    object_type, domain, version, status, attributes, content = OBJECTS[STRATEGY]
    choices, constraint = "主线：企业经营系统。" * 600, "不追求 Token 用量最大化。" * 300
    monkeypatch.setitem(OBJECTS, STRATEGY, (object_type, domain, version, status, attributes, {
        **content, "choices": value(choices), "constraint": value(constraint)}))
    whole = build(world)
    events = [item["ref"] for layer in whole["context_pack"]["layers"] for item in layer["events"]]
    loose = build(world, budget={"max_chars": whole["budget"]["used_chars"] - len(constraint)})
    assert [entry["key"] for entry in loose["plan"]["trimmed"]] == [
        *blocks(COMPANY, 1, "constraint"), *blocks(STRATEGY, 1, "constraint")]
    kept = [item["ref"] for layer in loose["context_pack"]["layers"] for item in layer["events"]]
    assert kept == events and choices in loose["context_pack"]["markdown"] and loose["budget"]["over_budget"] is False

    # 少掉约束与大半条战略选择才够：别的内容全裁掉也凑不够，战略选择必须裁，裁了它就够。
    tight = build(world, budget={"max_chars": whole["budget"]["used_chars"] - len(constraint) - len(choices) + 100})
    keys = [entry["key"] for entry in tight["plan"]["trimmed"]]
    first_why = keys.index(blocks(COMPANY, 1, "identity")[0])
    assert set(keys[:first_why]) == {
        *blocks(COMPANY, 1, "constraint"), *blocks(STRATEGY, 1, "constraint"), *blocks(UNIT, 1, "constraint"),
        *blocks(GOAL, 1, "constraint"), *blocks(PERIOD, 1, "constraint"),
        *blocks(MISSION, 2, "constraint", "execution_plan"), *blocks(TASK, 2, "constraint", "plan"),
        *events, "relations:2", f"snapshot:{SNAP_MISSION}@1"}
    assert keys[-1] == blocks(STRATEGY, 1, "choices")[0] and choices not in tight["context_pack"]["markdown"]
    assert tight["budget"]["over_budget"] is False and in_the_0_2_order(whole, tight)
    assert [block["ref"] for block in tight["context_pack"]["layers"][1]["blocks"]] == [
        f"{TASK}@2#definition", f"{TASK}@2#acceptance"]


def test_a_block_pinned_from_a_nearer_level_is_on_the_why_chain_even_when_it_is_not_a_definition(world, monkeypatch):
    """Activity 的执行指令另引 Task 计划里的一条：Task 的计划块（计划类）因此在 Why 链上，事件、跨链关系与快照都裁掉
    之后才裁；没被引到的 Mission 执行计划仍在第一步裁。"""
    object_type, domain, version, status, attributes, content = OBJECTS[ACTIVITY]
    monkeypatch.setitem(OBJECTS, ACTIVITY, (object_type, domain, version, status, attributes, {
        "instruction": value("按评审意见改材料", refs=[pin(TASK, 2, "acceptance", "ac-1"), pin(TASK, 2, "plan", "p-1")])}))
    result = build(world, budget={"max_chars": 10})
    keys = [entry["key"] for entry in result["plan"]["trimmed"]]
    assert keys.index(blocks(MISSION, 2, "execution_plan")[0]) < keys.index(f"event:{LATE}")
    assert keys[-3:] == blocks(TASK, 2, "plan", "acceptance", "definition")
    assert blocks(TASK, 2, "plan")[0] in why_chain(build(world)["context_pack"])


def test_why_keys_count_references_from_nearer_levels_by_object_and_block_ids_only():
    """补 48 的第二条逐项判：层号更小的块值引用与组件引用钉到上层的块（不看版本）才算；同层、从远处往近处、只钉对象
    与事件引用都不算；主干那一步的引用字段钉到块也算。"""
    def blk(oid, bid, kind="constraint", refs=(), component_refs=()):
        return {"id": bid, "kind": kind, "ref": f"{oid}@1#{bid}",
                "value": {"refs": list(refs), "text": "", "components": [], "artifacts": []},
                "components": [{"refs": list(component_refs)}]}

    def ref(oid, bid=None, version=1, component=None):
        return {"object_id": oid, "object_version": version, "block": bid, "component": component}
    layers = [
        {"level": 0, "object": {"object_id": "a"}, "hop": None,
         "blocks": [blk("a", "own", "definition", refs=[ref("b", "plan", version=7), ref("c"), {"event_id": "e"}])]},
        {"level": 1, "object": {"object_id": "b"}, "hop": None,
         "blocks": [blk("b", "plan", "plan"), blk("b", "note"),
                    blk("b", "other", component_refs=[ref("c", "limits", component="x")])]},
        {"level": 2, "object": {"object_id": "c"}, "hop": {"object": {"ref": "h@1"}, "blocks": [
            blk("h", "outcome", "definition", refs=[ref("d", "rules")])]},
         "blocks": [blk("c", "limits", refs=[ref("c", "loose")]), blk("c", "loose"), blk("c", "entry")]},
        {"level": 3, "object": {"object_id": "d"}, "hop": None,
         "blocks": [blk("d", "rules"), blk("d", "idea", "definition", refs=[ref("b", "note")]), blk("d", "spare")]},
    ]
    assert context.why_keys(layers, [("parent_ref", ref("b")), ("parent_ref", ref("c", "entry", component="u")),
                                     ("parent_ref", ref("d"))]) == {
        "hop:h@1", "block:d@1#idea",  # 上层的定义类块与多取的一跳
        "block:b@1#plan",             # 当前对象钉到上一层的计划块（钉的版本与所读的不同也算）
        "block:c@1#limits",           # 第 1 层的组件引用钉到第 2 层
        "block:c@1#entry",            # 主干第 1 步（第 1 层到第 2 层）的引用字段钉到块里的组件
        "block:d@1#rules"}            # 第 2 层多取一跳里的引用钉到第 3 层
    # 不算的：b#note 只被更远的第 3 层引用，c#loose 只被同层引用，只钉对象（c、d）与事件引用不指块，b#other、d#spare
    # 没人引用。


@pytest.mark.parametrize("share", [0.8, 0.5, 0.3, 0])
def test_the_guide_counts_toward_the_budget_and_points_only_at_what_the_pack_still_holds(world, share):
    whole = build(world)["budget"]["used_chars"]
    result = build(world, budget={"max_chars": max(10, int(whole * share))})
    markdown = result["context_pack"]["markdown"]
    guide = sections(markdown)["六问指引"]
    # 推出当前生命周期的事件一直在「现在怎样」里，指引可以指它；其余裁掉的内容指引都不再指。
    stage = f"event:{result['context_pack']['layers'][0]['object']['lifecycle']['event_id']}"
    gone = [entry["key"] if entry["kind"] == "event" else entry["key"].split(":", 1)[1]
            for entry in result["plan"]["trimmed"] if entry["kind"] in {"block", "hop", "snapshot", "event"}]
    assert gone and not [ref for ref in gone if f"`{ref}" in guide and ref != stage]
    assert f"`{stage}`" in sections(markdown)["现在怎样"]
    assert result["budget"]["used_chars"] == len(markdown)
    assert result["budget"]["over_budget"] is (len(markdown) > result["budget"]["max_chars"])


def test_starting_from_the_company_the_empty_sections_and_the_guide_name_their_gaps(world):
    parts = sections(build(world, start=COMPANY, question="公司是做什么的？")["context_pack"]["markdown"])
    assert parts["为什么"] == "主干上层没有取到非空的定义类块" and parts["发生了什么"] == "窗口内没有取到事件"
    assert parts["六问指引"].splitlines()[1:] == [
        "- 为什么：（缺口）主干上层没有取到非空的定义类块", f"- 做什么：当前对象 `{COMPANY}@1#identity`",
        f"- 谁负责：当前对象 `{COMPANY}@1`：CEO", "- 现在怎样：（缺口）当前对象没有生命周期，也没有取到状态快照",
        "- 发生了什么：（缺口）窗口内没有取到事件", f"- 凭什么：文档链接在块 `{COMPANY}@1#identity`"]


# ------------------------------------------------------------ #64 第二段：形成时带入
def carried_markdown(result) -> list[str]:
    return sections(result["context_pack"]["markdown"])["形成时带入"].split("\n\n")


def test_a_period_goal_carries_the_latest_confirmed_company_review_its_units_effective_goals_and_pending_issues(world):
    """从周期目标出发（补 43）：本 scope 最近的已确认公司复盘（钉到快照修订，结果、缺口、原因、关键变化、经营含义五块，
    材料不带）、本单元已确认的长期目标（goal_ref 指的那条已在「为什么」里，不重复；已终止与草稿不带），以及主受影响
    对象是本单元的待带入问题。自成一节，放在六问指引之后。"""
    result = build(world, start=PERIOD, question="形成这个周期目标要看什么？")
    carried = result["context_pack"]["carried"]
    assert list(carried) == ["company_review", "long_term_goals", "issues"]
    review = carried["company_review"]
    assert (review["ref"], review["confirmed_at"], review["principal"]["display_name"], review["on_behalf_of"]) == (
        f"event:{REVIEW_EVENT}", "2026-10-01T02:00:00Z", "CEO", None)
    snapshot = review["snapshot"]
    assert (snapshot["ref"], snapshot["pinned"], snapshot["as_of"], snapshot["payload_type"]["id"]) == (
        f"{REVIEW_SNAPSHOT}@1", cited(REVIEW_SNAPSHOT), "2026-09-30T15:59:59Z", "company_review")
    assert [(block["id"], block["pinned"]) for block in snapshot["blocks"]] == [
        (block, cited(REVIEW_SNAPSHOT, 1, block)) for block in ("results", "gaps", "causes", "key_changes", "implications")]
    goals = carried["long_term_goals"]
    assert [goal["ref"] for goal in goals] == [f"{GOAL_2}@1"]
    assert (goals[0]["title"], goals[0]["lifecycle"]["status"], goals[0]["pinned"]) == (
        "E&O 客户目标", "confirmed", cited(GOAL_2))
    assert goals[0]["definition_refs"] == [
        {"id": "outcome", "display_name": "结果", "ref": f"{GOAL_2}@1#outcome", "pinned": cited(GOAL_2, 1, "outcome"),
         "empty": False},
        {"id": "measures", "display_name": "衡量", "ref": f"{GOAL_2}@1#measures", "pinned": cited(GOAL_2, 1, "measures"),
         "empty": True}]
    assert [item["issue_ref"]["ref"] for item in carried["issues"]] == [
        f"{SNAP_GOAL}@3#issues/g-early", f"{SNAP_GOAL}@3#issues/g-iss"]
    markdown = result["context_pack"]["markdown"]
    assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
        "## 六问指引", "## 形成时带入", "## 为什么", "## 做什么", "## 谁负责", "## 现在怎样", "## 发生了什么", "## 凭什么"]
    parts = carried_markdown(result)
    assert parts[0] == ("形成周期目标时必须看到、不必须采用：本 scope 最近的已确认公司复盘、本单元有效的长期目标，以及处置为"
                        "带入下次形成或立即重开、此后主受影响对象还没记过门事件的问题（主受影响对象是责任单元的，看本单元的"
                        "周期目标此后有没有记过门事件）。")
    assert parts[1].splitlines() == [
        f"### 已确认的公司复盘《九月公司复盘》 `{REVIEW_SNAPSHOT}@1`（截至 2026-09-30T15:59:59Z）",
        f"确认事件 `event:{REVIEW_EVENT}`（CEO 记，2026-10-01T02:00:00Z）",
        f"#### 结果 `{REVIEW_SNAPSHOT}@1#results`", "营收达成八成", f"#### 缺口 `{REVIEW_SNAPSHOT}@1#gaps`", "交付慢两周",
        f"#### 原因 `{REVIEW_SNAPSHOT}@1#causes`", "当前没有原因",
        f"#### 关键变化 `{REVIEW_SNAPSHOT}@1#key_changes`", "换了交付负责人",
        f"#### 经营含义 `{REVIEW_SNAPSHOT}@1#implications`", "十月先补交付"]
    assert parts[2].splitlines() == [f"### 有效的长期目标《E&O 客户目标》 `{GOAL_2}@1`",
                                     f"生命周期：已确认（事件 `event:{uid(64)}`）", f"定义类块：`{GOAL_2}@1#outcome`"]
    assert parts[3].splitlines() == [
        f"### 待带入的问题 `{SNAP_GOAL}@3#issues/g-early`：衡量口径不一",
        f"主受影响对象：长期目标《E&O 六个月目标》 `{GOAL}@1`", "核心判断问题：收入按签约还是按回款算？",
        f"处置：带入下次形成（事件 `event:{G_EARLY}`，E&O DRI 记，2026-09-23T06:00:00Z）", "理由：口径在形成时统一"]
    assert len(parts) == 5 and "复盘草稿" not in markdown
    # 计入覆盖：复盘与问题是「凭什么」，有效的长期目标是「为什么」；六问指引指向它们；检索计划记下取过它们。
    basis = [item["ref"] for item in result["coverage"]["basis"]["evidence"]]
    assert {f"{REVIEW_SNAPSHOT}@1", f"{REVIEW_SNAPSHOT}@1#results", f"{SNAP_GOAL}@3#issues/g-iss", f"event:{G_ISS}"} \
        <= set(basis) and f"{REVIEW_SNAPSHOT}@1#materials" not in basis
    assert {"ref": f"{GOAL_2}@1#outcome"} in result["coverage"]["why"]["evidence"]
    guide = sections(markdown)["六问指引"].splitlines()
    assert guide[1].endswith(f"；形成时带入的有效长期目标 `{GOAL_2}@1`")
    assert guide[-1].startswith(f"- 凭什么：形成时带入的公司复盘 `{REVIEW_SNAPSHOT}@1`；形成时带入的问题 "
                                f"`{SNAP_GOAL}@3#issues/g-early`、`{SNAP_GOAL}@3#issues/g-iss`；")
    assert [entry["key"] for entry in result["plan"]["taken"] if entry["kind"] == "carried"] == [
        f"carried:review:{REVIEW_SNAPSHOT}@1", f"carried:goal:{GOAL_2}@1", f"carried:event:{G_EARLY}",
        f"carried:event:{G_ISS}"]


def test_a_pending_issue_is_carried_with_its_pinned_component_disposition_and_reason(world):
    """待带入的问题：处置为带入下次形成或立即重开、此后主受影响对象没记过门事件，按处置事件的时刻与 id 排序；
    Mission 上的问题不在本单元里，不随周期目标带入。"""
    issues = build(world, start=PERIOD)["context_pack"]["carried"]["issues"]
    assert [item["disposed_by"]["ref"] for item in issues] == [f"event:{G_EARLY}", f"event:{G_ISS}"]
    assert issues[1] == {
        "issue_ref": cited(SNAP_GOAL, 3, "issues", "g-iss"),
        "primary": {**cited(GOAL), "object_type": "LongTermGoal", "type_display_name": "长期目标",
                    "title": "E&O 六个月目标"},
        "text": "试点客户流失", "core_question": "下个周期要不要换客户群？",
        "disposition": {"id": "roll_forward", "display_name": "带入下次形成"},
        "disposed_by": {"event_id": G_ISS, "ref": f"event:{G_ISS}", "occurred_at": "2026-09-23T08:00:00Z",
                        "principal": {"principal_id": DRI, "principal_type": "human", "display_name": "E&O DRI"}},
        "reason": "客户流失原因要在下个周期回答"}


def test_without_a_confirmed_company_review_the_carry_in_names_the_gap(world, monkeypatch):
    monkeypatch.setattr(readers, "confirmed_company_review", lambda conn, ctx: None)
    result = build(world, start=PERIOD)
    assert result["context_pack"]["carried"]["company_review"] is None
    assert carried_markdown(result)[1] == "### 已确认的公司复盘\n（缺口）本 scope 里还没有已确认的公司复盘"
    assert not [item for item in result["coverage"]["basis"]["evidence"] if item["ref"].startswith(REVIEW_SNAPSHOT)]


def test_another_gated_object_carries_only_the_pending_issues_it_is_the_primary_affected_object_of(world):
    """从其他有门对象出发：只带主受影响对象是它本身的问题，不带复盘与长期目标；立即重开的同样带入。已关闭、还在处理与
    处置之后又记过门事件的不带；没有待带入的问题就没有这一节。"""
    mission = build(world, start=MISSION, question="Mission 要重开吗？")["context_pack"]
    assert list(mission["carried"]) == ["issues"]
    assert [(item["issue_ref"]["ref"], item["disposition"]["id"]) for item in mission["carried"]["issues"]] == [
        (f"{SNAP_MISSION_ISSUES}@1#issues/m-iss", "immediate_reopen")]
    assert sections(mission["markdown"])["形成时带入"].split("\n\n")[0] == (
        "形成时必须看到、不必须采用：处置为带入下次形成或立即重开、此后主受影响对象还没记过门事件的问题。")
    assert (f"处置：立即重开（事件 `event:{M_ISS}`，Mission Owner 记，2026-09-24T09:00:00Z）"
            in mission["markdown"].splitlines())
    goal = build(world, start=GOAL, question="长期目标要再确认吗？")["context_pack"]
    assert [item["disposed_by"]["event_id"] for item in goal["carried"]["issues"]] == [G_EARLY, G_ISS]
    quiet = build(world, start=GOAL_2)["context_pack"]
    assert quiet["carried"] == {"issues": []} and "## 形成时带入" not in quiet["markdown"]


def test_objects_without_a_gate_carry_nothing(world):
    for start in (ACTIVITY, TASK, UNIT, COMPANY):
        result = build(world, start=start)
        assert result["context_pack"]["carried"] is None and "## 形成时带入" not in result["context_pack"]["markdown"]


def test_the_carry_in_is_never_trimmed_and_the_same_world_gives_the_same_carry_in(world):
    whole, again = build(world, start=PERIOD), build(world, start=PERIOD)
    tiny = build(world, start=PERIOD, budget={"max_chars": 10})
    assert tiny["budget"]["over_budget"] is True and carried_markdown(tiny) == carried_markdown(whole)
    assert tiny["context_pack"]["carried"] == whole["context_pack"]["carried"]
    assert not [entry for entry in tiny["plan"]["trimmed"] if entry["kind"] == "carried"]
    assert {key: whole[key] for key in ("context_pack", "plan", "coverage", "budget")} == {
        key: again[key] for key in ("context_pack", "plan", "coverage", "budget")}


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
