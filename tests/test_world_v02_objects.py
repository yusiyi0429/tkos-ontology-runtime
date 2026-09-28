"""tkos.world/0.2 的七类业务对象、语义组件与四种引用（票 #50，契约第 3 至 6、9.3 节；不连数据库）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"


# ------------------------------------------------------------------ references
@pytest.mark.parametrize("text, parsed", [
    (f"{OID}@3", {"form": "object", "object_id": OID, "object_version": 3, "block": None, "component": None}),
    (f"{OID}@3#outcome", {"form": "block", "object_id": OID, "object_version": 3, "block": "outcome",
                          "component": None}),
    (f"{OID}@3#outcome/todo:42.a_b-c", {"form": "component", "object_id": OID, "object_version": 3,
                                        "block": "outcome", "component": "todo:42.a_b-c"}),
    (f"event:{EID}", {"form": "event", "event_id": EID}),
])
def test_the_four_reference_forms_parse(text, parsed):
    assert models.parse_ref(text) == parsed


@pytest.mark.parametrize("text", [
    f"{OID}", f"{OID}@0", f"{OID}@1#Outcome", f"{OID}@1#outcome/", f"{OID}@1#outcome/-x", f"{OID}@1/x",
    f"{OID}@1#outcome/{'x' * 129}", "event:not-a-uuid", f"event:{EID}#x", OID.upper() + "@1",
])
def test_a_malformed_reference_is_refused(text):
    with pytest.raises(ValueError):
        models.parse_ref(text)


def test_the_reference_patterns_are_the_registered_ones():
    """请求模型在导入时就要这些写法，不能等按需加载的登记；这里逐条与登记对齐。"""
    assert {item["form"]: item["pattern"] for item in REGISTRY["reference_forms"]} == models.REFERENCE_PATTERNS
    assert REGISTRY["components"]["id_pattern"] == models.COMPONENT_ID_PATTERN


@pytest.mark.parametrize("pinned, text", [
    ({"object_id": OID, "object_version": 2, "revision_id": RID, "block": None, "component": None}, f"{OID}@2"),
    ({"object_id": OID, "object_version": 2, "revision_id": RID, "block": "acceptance", "component": None},
     f"{OID}@2#acceptance"),
    ({"object_id": OID, "object_version": 2, "revision_id": RID, "block": "acceptance", "component": "ac-1"},
     f"{OID}@2#acceptance/ac-1"),
    ({"event_id": EID}, f"event:{EID}"),
])
def test_a_pinned_reference_renders_its_business_form(pinned, text):
    assert models.cite(pinned) == text


# ------------------------------------------------------------------ payloads of the seven types
MINIMAL = {
    "Strategy": {"title": "战略", "parent_ref": f"{OID}@1"},
    "ResponsibilityUnit": {"title": "单元", "unit_kind": "battlefield",
                           "architecture_ref": f"{OID}@2#responsibility_structure/unit-a"},
    "LongTermGoal": {"title": "长期目标", "scope": "unit", "horizon": "2027", "parent_ref": f"{OID}@1"},
    "PeriodGoal": {"title": "周期目标", "period": "2026-10", "goal_ref": f"{OID}@1"},
    "Mission": {"title": "Mission", "goal_ref": f"{OID}@1"},
    "Task": {"title": "Task", "parent_ref": f"{OID}@1"},
    "Activity": {"title": "Activity", "parent_ref": f"{OID}@1"},
}


@pytest.mark.parametrize("object_type", sorted(MINIMAL))
def test_each_business_object_type_takes_a_minimal_payload(object_type):
    value = models.validate_input(object_type, MINIMAL[object_type])
    spec = next(item for item in REGISTRY["objects"] if item["type"] == object_type)
    assert set(value["blocks"]) == {block["id"] for block in spec["blocks"]}
    assert all(block is None for block in value["blocks"].values())
    assert value["external_refs"] == []


@pytest.mark.parametrize("object_type, field, value", [
    ("Mission", "responsible", PID),                 # 责任人只由指派写
    ("Mission", "core_battle", True),                # 核心战役只由关注标记写
    ("Mission", "depends_on", [f"{OID}@1"]),          # 跨链关系只经 world_relate 写
    ("Mission", "contributes_to", [f"{OID}@1"]),
    ("PeriodGoal", "depends_on", [f"{OID}@1"]),
    ("Task", "responsible", PID),
    ("Mission", "component_ledger", []),             # 台账由服务维护
])
def test_fields_written_only_by_the_service_or_later_tickets_are_refused_on_creation(object_type, field, value):
    with pytest.raises(ValueError):
        models.validate_input(object_type, {**MINIMAL[object_type], field: value})


@pytest.mark.parametrize("object_type, field, text", [
    ("ResponsibilityUnit", "architecture_ref", f"{OID}@2"),                                # 须到责任单元条目
    ("ResponsibilityUnit", "architecture_ref", f"{OID}@2#responsibility_structure"),
    ("ResponsibilityUnit", "architecture_ref", f"{OID}@2#assumptions/unit-a"),              # 须在责任结构块
    ("Strategy", "parent_ref", f"{OID}@1#identity"),                                      # 主干引用指对象本身
    ("Mission", "goal_ref", f"{OID}@1#outcome/o-1"),
    ("Task", "parent_ref", f"event:{EID}"),
])
def test_relation_fields_take_the_form_the_registry_names(object_type, field, text):
    with pytest.raises(ValueError):
        models.validate_input(object_type, {**MINIMAL[object_type], field: text})


# ------------------------------------------------------------------ components
def _goal(**blocks):
    return {**MINIMAL["PeriodGoal"], "blocks": blocks}


def test_components_carry_writer_or_missing_ids_and_every_reference_form():
    refs = [f"{OID}@1", f"{OID}@1#measures", f"{OID}@1#measures/sc-1", f"event:{EID}"]
    value = models.validate_input("PeriodGoal", _goal(
        outcome={"components": [{"id": "todo:17", "type": "outcome", "text": "收入达成", "refs": refs},
                                {"type": "outcome", "text": "客户留存", "scope": f"{OID}@1"}]},
        acceptance={"text": "验收", "refs": refs}))
    first, second = value["blocks"]["outcome"]["components"]
    assert first == {"id": "todo:17", "type": "outcome", "scope": None, "text": "收入达成", "refs": refs,
                     "artifacts": [], "attributes": {}}
    assert second["id"] is None and second["scope"] == f"{OID}@1"
    assert value["blocks"]["outcome"]["text"] == "" and value["blocks"]["acceptance"]["refs"] == refs


def test_a_plan_item_carries_its_responsible_as_a_record():
    value = models.validate_input("Mission", {**MINIMAL["Mission"], "blocks": {"execution_plan": {"components": [
        {"id": "p-1", "type": "plan_item", "text": "搭环境", "attributes": {"responsible": PID}}]}}})
    assert value["blocks"]["execution_plan"]["components"][0]["attributes"] == {"responsible": PID}


@pytest.mark.parametrize("blocks", [
    {"outcome": {"components": [{"id": "x", "type": "outcome"}]},                    # 同一对象内 id 重复
     "acceptance": {"components": [{"id": "x", "type": "acceptance_criterion"}]}},
    {"outcome": {"components": [{"id": "x", "type": "outcome"}, {"id": "x", "type": "outcome"}]}},
    {"outcome": {"components": [{"type": "acceptance_criterion"}]}},                 # 块不允许的组件类型
    {"realization_logic": {"components": [{"type": "outcome"}]}},                    # 这个块不带组件
    {"outcome": {"components": [{"type": "no_such_type"}]}},
    {"outcome": {"components": [{"id": "-x", "type": "outcome"}]}},                  # id 字符集
    {"outcome": {"components": [{"id": "x" * 129, "type": "outcome"}]}},
    {"outcome": {"components": [{"type": "outcome", "attributes": {"core_question": "x"}}]}},  # 类型没有这个属性
    {"outcome": {"components": [{"type": "outcome", "note": "x"}]}},                 # 组件外壳只有登记的字段
    {"outcome": {"components": [{"type": "outcome", "scope": f"{OID}@1#outcome"}]}},  # 适用范围是对象形式
    {"outcome": {"components": [{"type": "outcome", "refs": [f"{OID}@1#Bad"]}]}},
])
def test_components_that_break_the_contract_are_refused(blocks):
    with pytest.raises(ValueError):
        models.validate_input("PeriodGoal", _goal(**blocks))


def test_a_plan_item_responsible_is_a_principal_id():
    with pytest.raises(ValueError):
        models.validate_input("Task", {**MINIMAL["Task"], "blocks": {"plan": {"components": [
            {"type": "plan_item", "attributes": {"responsible": "someone"}}]}}})


def test_a_component_type_with_a_required_attribute_refuses_it_missing():
    """对象块里允许的组件类型都没有必填属性；问题组件（快照的块里，票 #52）必带核心判断问题。"""
    issue = models.component_model("issue")
    assert issue.model_validate({"type": "issue", "attributes": {"core_question": "要不要换打法？"}})
    with pytest.raises(ValueError):
        issue.model_validate({"type": "issue", "attributes": {"responsible_hint": PID}})
    with pytest.raises(ValueError):
        issue.model_validate({"type": "issue"})


# ------------------------------------------------------------------ storing
def _pins(texts):
    pins = {}
    for text in texts:
        ref = models.parse_ref(text)
        pins[text] = ({"event_id": ref["event_id"]} if ref["form"] == "event" else
                      {"object_id": ref["object_id"], "object_version": ref["object_version"], "revision_id": RID,
                       "block": ref["block"], "component": ref["component"]})
    return pins


def test_storing_pins_every_reference_names_missing_ids_and_opens_the_ledger():
    refs = [f"{OID}@1#measures/sc-1", f"event:{EID}"]
    written = models.validate_input("PeriodGoal", _goal(
        outcome={"components": [{"id": "o-1", "type": "outcome", "refs": refs},
                                {"type": "outcome", "scope": f"{OID}@1"}]},
        acceptance={"components": [{"type": "acceptance_criterion", "text": "按时"}], "refs": refs}))
    texts = models.ref_texts("PeriodGoal", written)
    assert texts == [f"{OID}@1", *refs, f"{OID}@1", *refs]
    stored = models.stored_payload("PeriodGoal", written, _pins(texts), version=1)
    outcome, acceptance = stored["blocks"]["outcome"], stored["blocks"]["acceptance"]
    generated = [outcome["components"][1]["id"], acceptance["components"][0]["id"]]
    assert all(models.COMPONENT_ID.fullmatch(cid) for cid in generated) and len(set(generated)) == 2
    assert stored["goal_ref"]["revision_id"] == RID and stored["review_ref"] is None and stored["depends_on"] == []
    assert outcome["components"][0]["refs"] == [_pins(refs)[refs[0]], {"event_id": EID}]
    assert outcome["components"][1]["scope"]["object_id"] == OID
    assert [models.cite(pin) for pin in acceptance["refs"]] == refs
    assert stored["component_ledger"] == [
        {"id": "o-1", "type": "outcome", "block": "outcome", "added_in_version": 1, "removed_in_version": None},
        {"id": generated[0], "type": "outcome", "block": "outcome", "added_in_version": 1, "removed_in_version": None},
        {"id": generated[1], "type": "acceptance_criterion", "block": "acceptance", "added_in_version": 1,
         "removed_in_version": None}]


def test_the_ledger_keeps_removed_ids_and_records_new_ones_by_version():
    """台账按版本记出现与删除；删除的 id 留在台账里（合并修订在票 #51）。"""
    before = [{"id": "a", "type": "outcome", "block": "outcome", "added_in_version": 1, "removed_in_version": None},
              {"id": "b", "type": "outcome", "block": "outcome", "added_in_version": 1, "removed_in_version": None}]
    blocks = {"outcome": {"components": [{"id": "a", "type": "outcome"}, {"id": "c", "type": "outcome"}]},
              "acceptance": None}
    assert models.ledger_after(before, blocks, 2) == [
        before[0], {**before[1], "removed_in_version": 2},
        {"id": "c", "type": "outcome", "block": "outcome", "added_in_version": 2, "removed_in_version": None}]


# ------------------------------------------------------------------ declaration
def test_a_declared_scene_is_any_object_but_only_in_object_form():
    declaration = {"scene": f"{OID}@1", "trigger": "周会", "human_acceptance": {"required": False}}
    params = {"domain_id": OID, "object_type": "Task", "payload": MINIMAL["Task"], "declaration": declaration}
    assert models.WorldV02CreateObjectParams.model_validate(params).declaration.scene == f"{OID}@1"
    for scene in (f"{OID}@1#definition", f"{OID}@1#outcome/o-1", f"event:{EID}"):
        with pytest.raises(ValueError):
            models.WorldV02CreateObjectParams.model_validate(
                {**params, "declaration": {**declaration, "scene": scene}})


# ------------------------------------------------------------------ read projection
def test_a_mission_view_gives_blocks_components_ledger_relations_and_attribute_responsibility():
    from memory_service_runtime.governed import world_v02_readers as readers
    pin = {"object_id": OID, "object_version": 1, "revision_id": RID, "block": None, "component": None}
    component = {"object_id": OID, "object_version": 1, "revision_id": RID, "block": "acceptance",
                 "component": "ac-1"}
    ledger = [{"id": "m-ac-1", "type": "acceptance_criterion", "block": "acceptance", "added_in_version": 1,
               "removed_in_version": None}]
    payload = {"title": "M", "core_battle": False, "responsible": None, "external_refs": [],
               "goal_ref": pin, "depends_on": [], "contributes_to": [], "component_ledger": ledger,
               "blocks": {"definition": None, "play": None, "execution_plan": None, "constraint": None,
                          "acceptance": {"text": "", "refs": [{"event_id": EID}], "artifacts": [], "components": [
                              {"id": "m-ac-1", "type": "acceptance_criterion", "scope": pin, "text": "达标",
                               "refs": [component], "artifacts": [], "attributes": {}}]}}}
    head = {"object_id": "m", "object_type": "Mission", "object_version": 1, "lifecycle_status": "draft",
            "latest_revision_id": RID, "effective_revision_id": None, "domain_id": "d"}
    view = readers.object_view(head, {"revision_id": RID, "object_version": 1, "payload": payload},
                               {"interpretation_status": "world_v0_2"}, responsible=[])
    business = view["business"]
    acceptance = next(block for block in business["blocks"] if block["id"] == "acceptance")
    assert acceptance["value"]["refs"] == [{"event_id": EID, "ref": f"event:{EID}"}]
    assert acceptance["components"] == [{
        "id": "m-ac-1", "type": "acceptance_criterion", "scope": {**pin, "ref": f"{OID}@1"}, "text": "达标",
        "refs": [{**component, "ref": f"{OID}@1#acceptance/ac-1"}], "artifacts": [], "attributes": {},
        "ref": "m@1#acceptance/m-ac-1"}]
    assert acceptance["value"]["components"] == acceptance["components"]
    assert business["component_ledger"] == ledger
    assert business["relations"] == [
        {"field": "goal_ref", "relation": "serves", "value": {**pin, "ref": f"{OID}@1"}},
        {"field": "depends_on", "relation": "depends_on", "value": []},
        {"field": "contributes_to", "relation": "contributes_to", "value": []}]
    assert business["candidate"] is False and business["attributes"]["responsible"] is None
    assert view["identity"]["responsible"] == {"source": "attribute", "roles": {"human": "OWNER"}, "principals": []}
    assert view["records"]["lifecycle"] is None


def test_the_activity_is_marked_as_a_candidate_type():
    from memory_service_runtime.governed import world_v02_readers as readers
    pin = {"object_id": OID, "object_version": 1, "revision_id": RID, "block": None, "component": None}
    payload = {"title": "A", "responsible": None, "external_refs": [], "parent_ref": pin, "component_ledger": [],
               "blocks": {"instruction": None, "constraint": None}}
    head = {"object_id": "a", "object_type": "Activity", "object_version": 1, "lifecycle_status": "recorded",
            "latest_revision_id": RID, "effective_revision_id": RID, "domain_id": "d"}
    view = readers.object_view(head, {"revision_id": RID, "object_version": 1, "payload": payload},
                               {"interpretation_status": "world_v0_2"}, responsible=[])
    assert view["business"]["candidate"] is True
