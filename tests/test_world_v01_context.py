"""tkos.world/0.1 的取上下文（票 #25）：预算裁剪与六问覆盖两个纯函数，以及假连接上的整包组装，不连数据库。

裁剪：每层条数上限先生效，仍超字符预算时先裁最旧的事件，再从主干最远层起裁块与快照；
当前对象的块与最新快照不裁。覆盖：按上下文包里实际留下的内容逐问判定，给出依据或缺口。
组装（实验报告建议 2–4）：单元长期目标沿 goal_ref 多取一跳；Markdown 先给六问指引再分层；事件行写出人名。
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from memory_service_runtime.governed import world_v01_context as context
from memory_service_runtime.governed import world_v01_models as models
from memory_service_runtime.governed import world_v01_readers as readers
from memory_service_runtime.governed import world_v01_registry as registry
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.world_v01_context import cover, trim


def item(key, kind, level, text, occurred_at=None, object_id=None):
    return {"key": key, "kind": kind, "level": level, "text": text, "occurred_at": occurred_at,
            "object_id": object_id or f"o{level}"}


def pack():
    """文档顺序：标题、第 0 层（当前对象）到第 2 层，每层表头、块、快照、事件（新的在前）。"""
    return [item("title", "title", -1, "# 上下文"),
            item("h0", "header", 0, "## Activity"), item("b0", "block", 0, "x" * 40), item("s0", "snapshot", 0, "s" * 30),
            item("e0-new", "event", 0, "e" * 20, "2026-09-24T02:00:00.5Z"),  # 同一秒里晚半秒：UTC 文本按字符串比较会排反
            item("e0-old", "event", 0, "e" * 20, "2026-09-24T02:00:00Z"),
            item("h1", "header", 1, "## Task"), item("b1a", "block", 1, "y" * 40), item("b1b", "block", 1, "y" * 40),
            item("s1", "snapshot", 1, "s" * 30), item("e1", "event", 1, "e" * 20, "2026-09-23T02:00:00Z"),
            item("h2", "header", 2, "## Company"), item("b2", "block", 2, "z" * 40)]


def keys(result, field="kept"):
    return [entry["key"] for entry in result[field]]


def test_a_pack_within_budget_keeps_everything_and_counts_the_rendered_markdown():
    result = trim(pack(), max_chars=10_000, max_events_per_object=10)
    markdown = "\n\n".join(entry["text"] for entry in pack())
    assert keys(result) == keys({"kept": pack()}) and result["trimmed"] == []
    assert result["markdown"] == markdown and result["chars"] == len(markdown) and result["over_budget"] is False


def test_the_per_object_event_cap_keeps_the_newest_events_first():
    result = trim(pack(), max_chars=10_000, max_events_per_object=1)
    assert [(entry["key"], entry["reason"]) for entry in result["trimmed"]] == [("e0-old", "over_level_cap")]


@pytest.mark.parametrize("limit, trimmed", [
    (len("\n\n".join(e["text"] for e in pack())) - 1, ["e1"]),                      # 先裁最旧的事件
    (260, ["e1", "e0-old", "e0-new", "b2"]),                                           # 事件裁完才裁最远层的块
    (120, ["e1", "e0-old", "e0-new", "b2", "b1b", "b1a", "s1"]),                       # 由远及近，块在前、快照在后
])
def test_over_budget_trims_old_events_first_then_the_farthest_levels(limit, trimmed):
    result = trim(pack(), max_chars=limit, max_events_per_object=10)
    assert keys(result, "trimmed") == trimmed
    assert all(entry["reason"] == "over_budget" for entry in result["trimmed"])
    assert result["chars"] <= limit and result["over_budget"] is False


def test_the_current_object_and_its_snapshot_stay_even_over_budget():
    result = trim(pack(), max_chars=10, max_events_per_object=10)
    assert keys(result) == ["title", "h0", "b0", "s0", "h1", "h2"] and result["over_budget"] is True


def layer(level, *, blocks=(), state=None, events=(), responsible=(), formal=None):
    return {"level": level, "object": {"ref": f"o{level}@1", "formal": formal, "responsible": list(responsible)},
            "blocks": [{"ref": f"o{level}@1#{block_id}", "id": block_id, "kind": kind, "empty": empty}
                       for block_id, kind, empty in blocks],
            "state": state and {"ref": state}, "events": [{"event_id": event_id} for event_id in events]}


def test_each_question_is_answered_from_what_the_pack_holds():
    layers = [layer(0, blocks=[("instruction", "definition", False), ("constraint", "constraint", True)],
                    state="s0@1", events=["e1"], responsible=["p1"]),
              layer(1, blocks=[("definition", "definition", False), ("acceptance", "definition", False)]),
              layer(2, blocks=[("outcome", "definition", False)], formal=True)]
    coverage = cover(layers)
    assert {name: answer["evidence"] for name, answer in coverage.items()} == {
        "why": [{"ref": "o1@1#definition"}, {"ref": "o1@1#acceptance"}, {"ref": "o2@1#outcome"}],
        "what": [{"ref": "o0@1#instruction"}],
        "who": [{"ref": "o0@1"}],
        "now": [{"ref": "s0@1"}],
        "happened": [{"event_id": "e1"}],
        "basis": [{"ref": "o2@1#outcome"}, {"ref": "o1@1#acceptance"}]}
    assert all(answer["answered"] and answer["gap"] is None for answer in coverage.values())
    assert [answer["question"] for answer in coverage.values()] == ["为什么", "做什么", "谁负责", "现在怎样", "发生了什么",
                                                                     "凭什么"]


def test_a_question_the_pack_cannot_answer_is_a_gap_with_its_reason():
    coverage = cover([layer(0, blocks=[("instruction", "definition", True)]), layer(1, formal=False)])
    assert all(not answer["answered"] and answer["evidence"] == [] and answer["gap"] for answer in coverage.values())


@pytest.mark.parametrize("body", [
    {"question": "为什么要做这件事？", "budget": {"max_chars": 2000, "max_events_per_object": 3}, "recent_days": 7},
    {"question": "为什么要做这件事？"},
])
def test_a_context_request_names_a_question_and_optionally_its_budget(body):
    assert models.WorldContextRequest.model_validate(body).model_dump(mode="json", exclude_none=True) == body


@pytest.mark.parametrize("body", [
    {}, {"question": "  "}, {"question": "x", "budget": {"max_chars": 0}}, {"question": "x", "recent_days": 0},
    {"question": "x", "recent_days": 3_000_000}, {"question": "x" * 2001},
    {"question": "x", "budget": {"max_tokens": 10}}, {"question": "x", "scope": "all"},
])
def test_a_context_request_outside_its_shape_is_refused(body):
    with pytest.raises(ValueError):
        models.WorldContextRequest.model_validate(body)


def test_relation_lines_are_trimmed_with_their_level_and_the_current_objects_last():
    items = [item("title", "title", -1, "# 上下文"), item("h0", "header", 0, "## A"), item("r0", "relations", 0, "r" * 50),
             item("b0", "block", 0, "x" * 10), item("h1", "header", 1, "## T"), item("r1", "relations", 1, "r" * 50),
             item("b1", "block", 1, "y" * 10)]
    assert keys(trim(items, max_chars=10, max_events_per_object=10), "trimmed") == ["r1", "b1", "r0"]
    assert keys(trim(items, max_chars=100, max_events_per_object=10), "trimmed") == ["r1"]


def test_formal_upper_content_counts_as_basis_only_while_the_pack_still_holds_it():
    trimmed_away = [layer(0, blocks=[("instruction", "definition", False)]), layer(1, formal=True)]
    assert cover(trimmed_away)["basis"]["answered"] is False
    held = [layer(0), layer(1, blocks=[("outcome", "definition", False)], formal=True)]
    assert cover(held)["basis"]["evidence"] == [{"ref": "o1@1#outcome"}]


def test_a_hop_is_trimmed_before_the_blocks_of_its_level_and_the_current_objects_hop_last():
    items = [item("title", "title", -1, "# 上下文"), item("h0", "header", 0, "## G"), item("b0", "block", 0, "x" * 10),
             item("hop0", "hop", 0, "g" * 50), item("h1", "header", 1, "## U"), item("b1", "block", 1, "y" * 20),
             item("hop1", "hop", 1, "z" * 50), item("h2", "header", 2, "## C"), item("b2", "block", 2, "u" * 20)]
    assert keys(trim(items, max_chars=10, max_events_per_object=10), "trimmed") == ["b2", "hop1", "b1", "hop0"]


# ------------------------------------------------------------ build：假连接上的一条 E&O 主干
# Activity 沿主干到 Company，单元长期目标经 goal_ref 指向公司级长期目标；执行链三层各有一条快照，事件四条。
# 读投影与生命周期换成按下表查的替身，其余 SQL 由假连接按语句应答。
def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


COMPANY, STRATEGY, UNIT, COMPANY_GOAL, UNIT_GOAL, PERIOD, MISSION, TASK, ACTIVITY = (uid(n) for n in range(1, 10))
SNAP_ACTIVITY, SNAP_TASK, SNAP_MISSION = uid(21), uid(22), uid(23)
CEO, DRI, OWNER, IC, AGENT = (uid(n) for n in range(31, 36))
CONFIRMED, MET, ASSIGNED, DELIVERED = (uid(n) for n in range(41, 45))
PEOPLE = {CEO: ("CEO", "human"), DRI: ("E&O DRI", "human"), OWNER: ("Mission Owner", "human"),
          IC: ("方案 IC", "human"), AGENT: ("E&O Agent", "agent")}
ROLES = {("company", "CEO"): [CEO], ("eo", "CEO"): [CEO], ("eo", "DOMAIN_DRI"): [DRI], ("eo", "OWNER"): [OWNER],
         ("eo", "IC"): [IC], ("eo", "AGENT"): [AGENT]}
STAGES = {"in_progress": "进行中", "established": "已成立", "confirmed": "已确认"}


def pin(object_id: str, version: int = 1, block: str | None = None) -> dict:
    return {"object_id": object_id, "object_version": version, "revision_id": f"r{object_id[-2:]}-{version}",
            "block": block}


def value(text: str, **more) -> dict:
    return {"text": text, "refs": [], "artifacts": [], **more}


# 对象 -> (类型, 域, 最新版本, 生命周期, 属性, 非空块)；其余块为空，门对象都已确认。
OBJECTS = {
    COMPANY: ("Company", "company", 1, None, {"title": "词元云集"}, {"identity": value("企业经营系统")}),
    STRATEGY: ("Strategy", "company", 1, None, {"title": "总体战略", "parent_ref": pin(COMPANY)},
               {"responsibility_structure": value("E&O 与 Agents 两个域")}),
    UNIT: ("ResponsibilityUnit", "eo", 1, None,
           {"title": "E&O", "unit_kind": "domain", "architecture_ref": pin(STRATEGY, 1, "responsibility_structure")},
           {"definition": value("本体与 Context Runtime")}),
    COMPANY_GOAL: ("LongTermGoal", "company", 1, "confirmed",
                   {"title": "春节前核心业务被真实证明", "scope": "company", "horizon": "春节前", "parent_ref": pin(COMPANY)},
                   {"outcome": value("跑通主线", artifacts=["https://docs.example/battlefield"]),
                    "constraint": value("公司级目标自己的约束")}),
    UNIT_GOAL: ("LongTermGoal", "eo", 1, "confirmed",
                {"title": "E&O 六个月目标", "scope": "unit", "horizon": "六个月", "parent_ref": pin(UNIT),
                 "goal_ref": pin(COMPANY_GOAL)},
                {"outcome": value("建立 Enterprise Context")}),
    PERIOD: ("PeriodGoal", "eo", 1, "confirmed", {"title": "E&O 9 月", "period": "2026-09", "goal_ref": pin(UNIT_GOAL)},
             {"outcome": value("核心本体逐步稳定")}),
    MISSION: ("Mission", "eo", 1, "established",
              {"title": "9 月底 Agent 真实可用", "goal_ref": pin(PERIOD), "responsible": OWNER, "depends_on": [],
               "contributes_to": []},
              {"definition": value("为 Agent 提供底座"),
               "play": value("四块推进", artifacts=["https://docs.example/framework"]),
               "constraint": value("写入先限得死一点")}),
    TASK: ("Task", "eo", 2, "in_progress",
           {"title": "方案设计", "parent_ref": pin(MISSION), "responsible": IC, "depends_on": []},
           {"definition": value("节前完成整体方案", artifacts=["https://docs.example/framework"]),
            "acceptance": value("9/24 对一版")}),
    ACTIVITY: ("Activity", "eo", 2, "in_progress",
               {"title": "改建模材料", "parent_ref": pin(TASK, 2), "responsible": AGENT},
               {"instruction": value("按 9/23 会议改材料", refs=[pin(TASK, 2, "definition")])}),
}


def head(object_id: str) -> dict:
    object_type, domain, version, _, _, _ = OBJECTS[object_id]
    gated = registry.object_spec(object_type)["gated"]
    return {"object_id": object_id, "object_type": object_type, "domain_id": domain,
            "latest_revision_id": pin(object_id, version)["revision_id"],
            "lifecycle_status": "confirmed" if gated else "recorded"}


def revision(object_id: str) -> dict:
    object_type, _, version, _, attributes, blocks = OBJECTS[object_id]
    ids = [block["id"] for block in registry.object_spec(object_type)["blocks"]]
    return {"revision_id": pin(object_id, version)["revision_id"], "object_id": object_id, "object_version": version,
            "recorded_by": CEO, "payload": {**attributes, "blocks": {block: blocks.get(block) for block in ids}}}


def snapshot(snapshot_id: str, as_of: str, **blocks) -> dict:
    """取对象给出的快照视图：as_of 与三块。"""
    return {"object_id": snapshot_id, "version": 1, "revision_id": pin(snapshot_id)["revision_id"],
            "attributes": {"as_of": as_of},
            "blocks": [readers.block_view(snapshot_id, 1, block, blocks.get(block["id"]))
                       for block in registry.object_spec("StateSnapshot")["blocks"]]}


SNAPSHOTS = {ACTIVITY: snapshot(SNAP_ACTIVITY, "2026-09-23T13:00:00Z", progress=value("改了一半"),
                                artifacts=value("方案初稿", artifacts=["https://docs.example/draft"])),
             TASK: snapshot(SNAP_TASK, "2026-09-24T10:00:00Z", progress=value("等 CEO 对齐")),
             MISSION: snapshot(SNAP_MISSION, "2026-09-24T10:00:00Z", progress=value("验收完成"))}


def event(event_id, kind, principal, occurred_at, subjects, *, text=None, links=(), **fields) -> dict:
    """取事件的一行：带产生它的动作与指派的被指派者。"""
    return {"event_id": event_id, "kind": kind, "action": None, "phase": None, "category": None, "outcome": None,
            "assignee": None, "action_id": uid(90), "supersedes_event_id": None, **fields, "principal_id": principal,
            "occurred_at": occurred_at, "recorded_at": occurred_at, "subject_refs": [pin(*subject) for subject in subjects],
            "content": text and value(text, artifacts=list(links))}


# 一条事件以多层对象为主体时只放在最近的一层：Activity 层是指派与交付，Task 层是会议，Mission 层是确认。
EVENTS = [
    event(CONFIRMED, "confirm", DRI, "2026-09-22T01:00:00Z", [(MISSION, 1)], phase="initiation", outcome="accepted"),
    event(MET, "event.recorded", IC, "2026-09-23T02:23:00Z", [(MISSION, 1), (TASK, 2)], category="meeting",
          text="9/23 会议定分工", links=["https://docs.example/framework"]),
    event(ASSIGNED, "assign", OWNER, "2026-09-23T03:00:00Z", [(ACTIVITY, 2)], assignee=AGENT),
    event(DELIVERED, "event.recorded", IC, "2026-09-24T02:00:00Z", [(TASK, 2), (ACTIVITY, 2)], category="delivery",
          text="方案 v0.1 提交", links=["https://docs.example/plan"]),
]


class Conn:
    """按语句应答的假连接：修订、责任人与人名、窗口起点，落表的那一行记在 inserted。"""

    def __init__(self) -> None:
        self.inserted = None

    def execute(self, sql, params=()):
        rows = self.answer(" ".join(sql.split()), params)
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)

    def answer(self, sql, params):
        if "make_interval" in sql:
            return [{"start": datetime(2026, 8, 25, tzinfo=timezone.utc)}]
        if sql.startswith("SELECT * FROM gov_object_revisions"):
            return [revision(oid) for oid in OBJECTS if revision(oid)["revision_id"] == params[1]]
        if sql.startswith("SELECT principal_type FROM gov_principals"):
            return [{"principal_type": PEOPLE[params[1]][1]}]
        if sql.startswith("SELECT 1 FROM gov_role_assignments"):
            return [{"held": 1}] if params[1] in ROLES.get((params[2], params[3]), []) else []
        if sql.startswith("SELECT DISTINCT a.principal_id"):
            return [{"principal_id": person} for person in ROLES.get((params[1], params[2]), [])]
        if "FROM gov_principals" in sql:
            return [{"principal_id": person, "display_name": PEOPLE[person][0], "principal_type": PEOPLE[person][1]}
                    for person in sorted(params[1]) if person in PEOPLE]
        if sql.startswith("INSERT INTO gov_world_context_packs"):
            self.inserted = params
            return [{"context_pack_id": uid(99), "created_at": datetime(2026, 9, 24, 12, tzinfo=timezone.utc)}]
        raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture
def world(monkeypatch):
    def readable(conn, ctx, object_id):
        if object_id not in OBJECTS:
            raise GovernedError("NOT_FOUND")
        return head(object_id), {"interpretation_status": "world_v0_1"}

    def lifecycle(conn, ctx, row):
        stage = OBJECTS[row["object_id"]][3]
        return stage and {"status": stage, "display_name": STAGES[stage], "event_id": uid(80)}

    def events_about(conn, ctx, object_id, since=None, *, by_occurrence=False):
        return [row for row in EVENTS if object_id in {ref["object_id"] for ref in row["subject_refs"]}]

    monkeypatch.setattr(context, "readable", readable)
    monkeypatch.setattr(context, "lifecycle", lifecycle)
    monkeypatch.setattr(context, "latest_snapshot", lambda conn, ctx, subject, as_of=None: SNAPSHOTS.get(subject))
    monkeypatch.setattr(context, "referenced_by", lambda conn, ctx, object_id: [])
    monkeypatch.setattr(context, "events_about", events_about)
    return Conn()


def build(conn, question="为什么要做这条 Activity？", **request):
    return context.build(conn, SimpleNamespace(scope_id=uid(0), principal_id=AGENT), ACTIVITY,
                         models.WorldContextRequest.model_validate({"question": question, **request}))


def test_a_unit_goal_takes_one_hop_along_goal_ref_to_the_company_goals_definition_blocks(world):
    result = build(world)
    layers = result["context_pack"]["layers"]
    assert [layer["object"]["object_type"] for layer in layers] == registry.registry()["spine"]  # 主干本身不变
    assert [hop["field"] for hop in result["plan"]["walked"]] == [
        "parent_ref", "parent_ref", "goal_ref", "goal_ref", "parent_ref", "architecture_ref", "parent_ref"]
    hop = layers[4]["hop"]
    assert (hop["field"], hop["object"]["ref"], hop["object"]["formal"]) == ("goal_ref", f"{COMPANY_GOAL}@1", True)
    # 只取定义类块：公司级目标自己的约束不随这一跳进来；空块照样读标准句。
    assert [(block["ref"], block["text"]) for block in hop["blocks"]] == [
        (f"{COMPANY_GOAL}@1#outcome", "跑通主线"), (f"{COMPANY_GOAL}@1#measures", "当前没有衡量")]
    assert [layer["hop"] for layer in layers if layer["level"] != 4] == [None] * 7
    assert result["plan"]["hops"] == [{"from": f"{UNIT_GOAL}@1", "field": "goal_ref", "pinned": f"{COMPANY_GOAL}@1",
                                       "read": f"{COMPANY_GOAL}@1"}]
    assert {"kind": "hop", "level": 4, "key": f"hop:{COMPANY_GOAL}@1"} in result["plan"]["taken"]
    markdown = result["context_pack"]["markdown"]
    assert f"公司级长期目标《春节前核心业务被真实证明》 `{COMPANY_GOAL}@1`" in markdown
    assert "跑通主线" in markdown and "当前没有衡量" in markdown and "公司级目标自己的约束" not in markdown
    # 这一跳的块是 Why 的依据；公司级目标已确认，它的块也算凭什么。
    assert {"ref": f"{COMPANY_GOAL}@1#outcome"} in result["coverage"]["why"]["evidence"]
    assert {"ref": f"{COMPANY_GOAL}@1#outcome"} in result["coverage"]["basis"]["evidence"]
    assert world.inserted is not None and result["budget"]["used_chars"] == len(markdown)


def test_event_lines_name_who_recorded_them_and_whom_an_assignment_went_to(world):
    result = build(world)
    lines = result["context_pack"]["markdown"].splitlines()
    assert f"- 2026-09-23T03:00:00Z 指派（事件 `{ASSIGNED}`，Mission Owner 记，指派给 E&O Agent）" in lines
    assert f"- 2026-09-23T02:23:00Z 外部事件·会议（事件 `{MET}`，方案 IC 记）：9/23 会议定分工" in lines
    assert f"- 2026-09-22T01:00:00Z 确认·立项·接受（事件 `{CONFIRMED}`，E&O DRI 记）" in lines
    # 名字也留在包里的事件上，id 照旧。
    events = {event["event_id"]: event for layer in result["context_pack"]["layers"] for event in layer["events"]}
    assert [(events[key]["principal_id"], events[key]["principal_name"], events[key]["assignee_name"])
            for key in (ASSIGNED, MET)] == [(OWNER, "Mission Owner", "E&O Agent"), (IC, "方案 IC", None)]
