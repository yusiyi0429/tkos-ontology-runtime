"""tkos.world/0.1 契约正文、world 登记与 profile 钉定。

期望值逐条取自规格 #17「一、语义模型」与 2026-09-24 的补充决定，不从登记本身推导。
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import world_v01_profile as profile

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs/contracts/world-registry-0.1.json"
CONTRACT_PATH = ROOT / "docs/contracts/tkos-world-0.1.md"
PROFILE_PATH = ROOT / "docs/contracts/world-profile-0.1.json"

NINE_TYPES = ["Company", "Strategy", "ResponsibilityUnit", "LongTermGoal", "PeriodGoal",
              "Mission", "Task", "Activity", "StateSnapshot"]


def registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def objects():
    return {item["type"]: item for item in registry()["objects"]}


def test_registry_is_canonical_json_naming_the_nine_first_class_object_types():
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    data = registry()
    assert (data["registry_id"], data["revision"], data["protocol_id"], data["contract_version"]) == (
        "tkos.world-registry", "0.1.4", "tkos.world", "tkos.world/0.1")
    assert [item["type"] for item in data["objects"]] == NINE_TYPES
    assert {t for t, item in objects().items() if item["stub"]} == {"Company", "Strategy"}
    assert {t for t, item in objects().items() if item["gated"]} == {"LongTermGoal", "PeriodGoal", "Mission"}
    assert data["evidence_upload"] is False


# 规格「临时定义类块清单」；方法侧正式清单到了整体替换登记。
DEFINITION_BLOCKS = {
    "Company": ["identity"],
    "Strategy": ["choices", "path", "assumptions", "capabilities", "responsibility_structure"],
    "ResponsibilityUnit": ["definition", "boundary"],
    "LongTermGoal": ["outcome", "measures"],
    "PeriodGoal": ["outcome", "acceptance"],
    "Mission": ["definition", "acceptance", "play"],
    "Task": ["definition", "acceptance", "plan"],
    "Activity": ["instruction"],
}


def test_blocks_are_the_interim_definition_list_plus_constraint_and_snapshots_have_three_fixed_blocks():
    assert registry()["block_value_fields"] == ["text", "refs", "artifacts"]
    for type_, item in objects().items():
        blocks = item["blocks"]
        ids = [block["id"] for block in blocks]
        assert len(ids) == len(set(ids)), type_
        assert all(block["display_name"].strip() for block in blocks), type_
        if type_ == "StateSnapshot":
            assert [(b["id"], b["kind"]) for b in blocks] == [
                ("progress", "state"), ("issue", "state"), ("artifacts", "state")]
        else:
            assert [b["id"] for b in blocks if b["kind"] == "definition"] == DEFINITION_BLOCKS[type_]
            assert [(b["id"], b["kind"]) for b in blocks if b["kind"] != "definition"] == [
                ("constraint", "constraint")], type_


# (id, value, required, values, set_by)；规格「属性」一段与决定 6。
ATTRIBUTES = {
    "Company": {("title", "text", True, None, None)},
    "Strategy": {("title", "text", True, None, None)},
    "ResponsibilityUnit": {("title", "text", True, None, None),
                           ("unit_kind", "enum", True, ("battlefield", "domain"), None)},
    "LongTermGoal": {("title", "text", True, None, None),
                     ("scope", "enum", True, ("company", "unit"), None),
                     ("horizon", "text", True, None, None)},
    "PeriodGoal": {("title", "text", True, None, None), ("period", "month", True, None, None)},
    "Mission": {("title", "text", True, None, None), ("core_battle", "boolean", False, None, "core_battle.marked"),
                ("responsible", "principal", False, None, "assign")},
    "Task": {("title", "text", True, None, None), ("responsible", "principal", False, None, "assign")},
    "Activity": {("title", "text", True, None, None), ("responsible", "principal", False, None, "assign")},
    "StateSnapshot": {("title", "text", True, None, None), ("subject_ref", "ref", True, None, None),
                      ("as_of", "timestamp", True, None, None), ("period", "month", False, None, None)},
}
RESPONSIBLE = {"Company": ("role", "CEO"), "Strategy": ("role", "CEO"), "LongTermGoal": ("role", "CEO"),
               "ResponsibilityUnit": ("role", "DOMAIN_DRI"), "PeriodGoal": ("role", "DOMAIN_DRI"),
               "Mission": ("attribute", None), "Task": ("attribute", None), "Activity": ("attribute", None),
               "StateSnapshot": ("writer", None)}


def test_attributes_and_who_is_responsible_per_type():
    for type_, item in objects().items():
        attrs = item["attributes"]
        assert len({a["id"] for a in attrs}) == len(attrs), type_
        assert {(a["id"], a["value"], a["required"],
                 tuple(v["id"] for v in a["values"]) if a["values"] else None, a["set_by"])
                for a in attrs} == ATTRIBUTES[type_], type_
        assert (item["responsible"]["source"], item["responsible"]["role"]) == RESPONSIBLE[type_], type_


RELATIONS = ["has", "defines", "decomposes_to", "advances", "serves", "decomposed_into",
             "responsible_for", "depends_on", "contributes_to", "supersedes"]
# 类型 -> {字段: (many, required, 目标类型, 目标块)}；方案 1.3 关系表与决定 5。
RELATION_FIELDS = {
    "Company": {},
    "Strategy": {"parent_ref": (False, True, ("Company",), None)},
    "ResponsibilityUnit": {"architecture_ref": (False, True, ("Strategy",), "responsibility_structure")},
    "LongTermGoal": {"parent_ref": (False, True, ("Company", "ResponsibilityUnit"), None),
                     "goal_ref": (False, False, ("LongTermGoal",), None)},
    "PeriodGoal": {"goal_ref": (False, True, ("LongTermGoal",), None)},
    "Mission": {"goal_ref": (False, True, ("PeriodGoal",), None),
                "depends_on": (True, False, ("Mission",), None),
                "contributes_to": (True, False, ("PeriodGoal", "LongTermGoal"), None)},
    "Task": {"parent_ref": (False, True, ("Mission",), None), "depends_on": (True, False, ("Task",), None)},
    "Activity": {"parent_ref": (False, True, ("Task",), None)},
    "StateSnapshot": {},
}
SPINE = ["Activity", "Task", "Mission", "PeriodGoal", "LongTermGoal", "ResponsibilityUnit", "Strategy", "Company"]
SPINE_PARENT_FIELD = {"Company": None, "Strategy": "parent_ref", "ResponsibilityUnit": "architecture_ref",
                      "LongTermGoal": "parent_ref", "PeriodGoal": "goal_ref", "Mission": "goal_ref",
                      "Task": "parent_ref", "Activity": "parent_ref", "StateSnapshot": None}
# 跨链关系只经 world_relate 写入（2026-09-24 决定）；其余引用字段在建对象时写入。
WRITTEN_BY_RELATE = {("Mission", "depends_on"), ("Mission", "contributes_to"), ("Task", "depends_on")}


def test_ten_relations_are_carried_by_declared_reference_fields_and_the_spine_links_up():
    data = registry()
    assert [r["id"] for r in data["relations"]] == RELATIONS
    carried = {}
    for relation in data["relations"]:
        for carrier in relation["carriers"]:
            if carrier["via"] == "ref_field":
                key = (carrier["on"], carrier["field"])
                assert key not in carried, key
                carried[key] = relation["id"]
            else:
                assert carrier["via"] in {"role_assignment", "attribute", "revision_chain"}, relation["id"]
    for type_, item in objects().items():
        fields = {f["field"]: (f["many"], f["required"], tuple(f["targets"]), f["target_block"])
                  for f in item["relation_fields"]}
        assert len(fields) == len(item["relation_fields"]) and fields == RELATION_FIELDS[type_], type_
        for f in item["relation_fields"]:
            assert carried.pop((type_, f["field"])) == f["relation"], (type_, f["field"])
            expected = "world_relate" if (type_, f["field"]) in WRITTEN_BY_RELATE else "world_create_object"
            assert f["written_by"] == expected, (type_, f["field"])
        assert item["spine_parent_field"] == SPINE_PARENT_FIELD[type_], type_
    assert not carried
    assert data["spine"] == SPINE
    for child, parent in zip(SPINE, SPINE[1:]):
        field = SPINE_PARENT_FIELD[child]
        assert parent in RELATION_FIELDS[child][field][2], (child, parent)


EVENT_FIELDS = ["event_id", "scope", "kind", "phase", "category", "outcome", "subject_refs", "principal",
                "occurred_at", "recorded_at", "content", "action_id", "supersedes_event_id"]
# 取值 -> 规格里的中文名（决定 7：取值英文 snake_case，显示名中文）。
PHASES = {"initiation": "立项", "delivery": "交付"}
CATEGORIES = {"meeting": "会议", "review": "评审", "delivery": "交付", "acceptance": "验收",
              "other": "其他", "correction": "更正"}
OUTCOMES = {"accepted": "接受", "returned": "退回", "withdrawn": "撤回"}
# kind -> (phase, category, outcome 取值)；phase 为 by_action 时由各门动作决定。
EVENT_KINDS = {
    "object.created": ("forbidden", "forbidden", []),
    "object.revised": ("forbidden", "forbidden", []),
    "state.refreshed": ("forbidden", "forbidden", []),
    "event.recorded": ("forbidden", "required", []),
    "commit": ("by_action", "forbidden", ["withdrawn"]),
    "confirm": ("by_action", "forbidden", ["accepted", "returned", "withdrawn"]),
    "assign": ("forbidden", "forbidden", []),
    "relate": ("forbidden", "forbidden", []),
    "core_battle.marked": ("forbidden", "forbidden", []),
}


def test_event_fields_kinds_and_their_structured_attributes():
    data = registry()
    assert data["event_fields"] == EVENT_FIELDS
    values = data["event_attribute_values"]
    assert {v["id"]: v["display_name"] for v in values["phase"]} == PHASES
    assert {v["id"]: v["display_name"] for v in values["category"]} == CATEGORIES
    assert {v["id"]: v["display_name"] for v in values["outcome"]} == OUTCOMES
    kinds = data["event_kinds"]
    assert [k["kind"] for k in kinds] == list(EVENT_KINDS)
    for k in kinds:
        assert (k["phase"], k["category"], k["outcomes"]) == EVENT_KINDS[k["kind"]], k["kind"]
        assert k["display_name"].strip() and k["recorded_by"].strip(), k["kind"]
    assert data["assign_effective_time"] == "occurred_at"
    confirm = next(k for k in kinds if k["kind"] == "confirm")
    assert confirm["outcome_required"] is True
    assert all(k["outcome_required"] is False for k in kinds if k["kind"] != "confirm")


# 动作 -> (事件 kind, 目标类型, 门角色, phase 取值, outcome 取值)；规格「动作清单」、ADR-0005 与决定 7。
ACTIONS = {
    "world_create_object": ("object.created", None, None, [], []),
    "world_revise_object": ("object.revised", None, None, [], []),
    "world_refresh_state": ("state.refreshed", None, None, [], []),
    "world_record_event": ("event.recorded", None, None, [], []),
    "world_assign": ("assign", None, None, [], []),
    "world_relate": ("relate", None, None, [], []),
    "world_commit_period_goal": ("commit", "PeriodGoal", ["DOMAIN_DRI"], [], ["withdrawn"]),
    "world_commit_mission": ("commit", "Mission", ["OWNER"], ["initiation", "delivery"], ["withdrawn"]),
    "world_confirm_long_term_goal": ("confirm", "LongTermGoal", ["CEO"], [], ["accepted", "returned", "withdrawn"]),
    "world_confirm_period_goal": ("confirm", "PeriodGoal", ["CEO"], [], ["accepted", "returned", "withdrawn"]),
    "world_confirm_mission": ("confirm", "Mission", ["DOMAIN_DRI"], ["initiation", "delivery"],
                              ["accepted", "returned", "withdrawn"]),
    "world_confirm_mission_core_battle": ("confirm", "Mission", ["CEO"], ["initiation"],
                                          ["accepted", "returned", "withdrawn"]),
    "world_mark_core_battle": ("core_battle.marked", "Mission", ["CEO"], [], []),
}
MCP_WRITES = {"world_revise_object", "world_refresh_state", "world_record_event"}


def test_thirteen_actions_gate_tables_and_the_mcp_write_surface():
    data = registry()
    actions = {a["action"]: a for a in data["actions"]}
    assert len(actions) == len(data["actions"]) == 13
    assert {name: (a["event_kind"], a["target_type"], a["gate_roles"], a["phases"], a["outcomes"])
            for name, a in actions.items()} == ACTIONS
    assert {name for name, a in actions.items() if a["mcp"]} == MCP_WRITES
    kinds = {k["kind"]: k for k in data["event_kinds"]}
    for name, a in actions.items():
        kind = kinds[a["event_kind"]]
        assert set(a["outcomes"]) <= set(kind["outcomes"]), name
        assert a["phases"] == [] or kind["phase"] == "by_action", name
    confirmed = {a["target_type"] for a in actions.values() if a["event_kind"] == "confirm"}
    assert confirmed == {t for t, item in objects().items() if item["gated"]}


# (from, action, phase, outcome, category, guard, to)；规格生命周期与决定 1–3。
TASK_OR_ACTIVITY = {
    ("unassigned", "world_assign", None, None, None, None, "assigned"),
    ("assigned", "world_refresh_state", None, None, None, None, "in_progress"),
    ("in_progress", "world_record_event", None, None, "delivery", None, "delivered"),
    ("delivered", "world_record_event", None, None, "acceptance", "spine_parent_responsible", "closed"),
}
LIFECYCLES = {
    "LongTermGoal": ("draft", {"draft": "草稿", "confirmed": "已确认"}, {
        ("draft", "world_confirm_long_term_goal", None, "accepted", None, None, "confirmed"),
        ("draft", "world_confirm_long_term_goal", None, "returned", None, None, "draft"),
    }),
    "PeriodGoal": ("draft", {"draft": "草稿", "committed": "已承诺", "confirmed": "已确认"}, {
        ("draft", "world_commit_period_goal", None, None, None, None, "committed"),
        ("committed", "world_confirm_period_goal", None, "accepted", None, None, "confirmed"),
        ("committed", "world_confirm_period_goal", None, "returned", None, None, "draft"),
    }),
    "Mission": ("draft", {"draft": "草稿", "committed": "已承诺", "established": "已成立",
                          "awaiting_ceo": "等 CEO 确认", "in_progress": "进行中", "delivered": "已交付",
                          "adjusting": "调整", "closed": "已关闭"}, {
        ("draft", "world_commit_mission", "initiation", None, None, None, "committed"),
        ("committed", "world_confirm_mission", "initiation", "accepted", None, "not_core_battle", "established"),
        ("committed", "world_confirm_mission", "initiation", "accepted", None, "core_battle", "awaiting_ceo"),
        ("committed", "world_confirm_mission", "initiation", "returned", None, None, "draft"),
        ("awaiting_ceo", "world_confirm_mission_core_battle", "initiation", "accepted", None, None, "established"),
        ("awaiting_ceo", "world_confirm_mission_core_battle", "initiation", "returned", None, None, "draft"),
        ("draft", "world_mark_core_battle", None, None, None, None, "draft"),
        ("committed", "world_mark_core_battle", None, None, None, None, "committed"),
        ("established", "world_mark_core_battle", None, None, None, None, "awaiting_ceo"),
        ("established", "world_refresh_state", None, None, None, None, "in_progress"),
        ("in_progress", "world_commit_mission", "delivery", None, None, None, "delivered"),
        ("delivered", "world_confirm_mission", "delivery", "accepted", None, None, "closed"),
        ("delivered", "world_confirm_mission", "delivery", "returned", None, None, "adjusting"),
        ("adjusting", "world_refresh_state", None, None, None, None, "in_progress"),
        ("adjusting", "world_commit_mission", "delivery", None, None, None, "delivered"),
    }),
    "Task": ("unassigned", {"unassigned": "未指派", "assigned": "已指派", "in_progress": "进行中",
                            "delivered": "已交付", "closed": "已关闭"}, TASK_OR_ACTIVITY),
    "Activity": ("unassigned", {"unassigned": "未指派", "assigned": "已指派", "in_progress": "进行中",
                                "delivered": "已交付", "closed": "已关闭"}, TASK_OR_ACTIVITY),
}


def test_lifecycle_state_machines_edges_and_withdrawal_rules():
    data = registry()
    machines = data["lifecycles"]
    assert set(machines) == set(LIFECYCLES)
    actions = {a["action"]: a for a in data["actions"]}
    categories = {v["id"] for v in data["event_attribute_values"]["category"]}
    for type_, (initial, states, edges) in LIFECYCLES.items():
        machine = machines[type_]
        assert machine["initial"] == initial, type_
        assert {s["id"]: s["display_name"] for s in machine["states"]} == states, type_
        assert len(machine["states"]) == len(states), type_
        got = {(t["from"], t["action"], t["phase"], t["outcome"], t["category"], t["guard"], t["to"])
               for t in machine["transitions"]}
        assert len(got) == len(machine["transitions"]) and got == edges, type_
        for t in machine["transitions"]:
            action = actions[t["action"]]
            assert action["target_type"] in (None, type_), (type_, t)
            assert (t["phase"] in action["phases"]) if action["phases"] else t["phase"] is None, (type_, t)
            assert t["outcome"] is None or t["outcome"] in action["outcomes"], (type_, t)
            assert t["category"] is None or t["category"] in categories, (type_, t)
        reached, frontier = {initial}, [initial]
        while frontier:
            here = frontier.pop()
            for t in machine["transitions"]:
                if t["from"] == here and t["to"] not in reached:
                    reached.add(t["to"])
                    frontier.append(t["to"])
        assert reached == set(states), type_
    assert data["withdrawal"] == {"outcome": "withdrawn", "reverts": "gate_event_that_produced_current_state",
                                  "recorded_by": "same_role", "references": "supersedes_event_id"}
    assert data["supersedes_required_when"] == {"category": ["correction"], "outcome": ["withdrawn"]}


def test_profile_pins_the_exact_contract_and_registry_bytes():
    assert (profile.PROTOCOL_ID, profile.CONTRACT_VERSION, profile.SCHEMA_VERSION) == (
        "tkos.world", "tkos.world/0.1", "tkos.world-profile/0.1")
    assert sha256(CONTRACT_PATH.read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    assert sha256(REGISTRY_PATH.read_bytes()).hexdigest() == profile.REGISTRY_SHA256
    assert registry()["revision"] == profile.REGISTRY_REVISION
    saved = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    assert saved == profile.content()
    profile.validate(saved)


def terms_missing_from(text):
    """登记里代码与数据会引用的名字（类型、块、属性及其取值、关系、事件 kind 与属性取值、动作、
    生命周期状态名）都必须在契约正文里作为完整的词出现，正文与登记不能各说各话。"""
    data = registry()
    names = set()
    for item in data["objects"]:
        names.add(item["type"])
        names |= {b["id"] for b in item["blocks"]}
        for a in item["attributes"]:
            names.add(a["id"])
            names |= {v["id"] for v in a["values"] or []}
    names |= {r["id"] for r in data["relations"]} | {r["name"] for r in data["relations"]}
    names |= {k["kind"] for k in data["event_kinds"]}
    names |= {v["id"] for values in data["event_attribute_values"].values() for v in values}
    names |= {a["action"] for a in data["actions"]}
    names |= {s["display_name"] for m in data["lifecycles"].values() for s in m["states"]}
    return sorted(name for name in names
                  if not re.search(rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_])", text))


def test_contract_text_names_everything_the_registry_declares():
    assert terms_missing_from(CONTRACT_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("change", [
    {"display_name": "World 0.1 changed"},
    {"world_registry_ref": {"registry_id": "tkos.world-registry", "revision": "0.1.4", "content_sha256": "0" * 64}},
    {"action_contract_ref": {"contract_id": "tkos.world", "revision": "0.1", "content_sha256": "0" * 64}},
])
def test_profile_rejects_changed_content_and_foreign_pins(change):
    with pytest.raises(ValidationError):
        profile.validate({**profile.content(), **change})


def test_profile_rejects_a_canonical_hash_that_does_not_match_its_content():
    with pytest.raises(ValueError, match="canonical_hash mismatch"):
        profile.validate({**profile.content(), "canonical_hash": "0" * 64})
