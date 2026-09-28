"""tkos.world/0.2 的合并修订、组件台账与建关系（票 #51，契约第 4、6、9.3、12 节；不连数据库）。"""
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
MID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"


def _pin(text):
    ref = models.parse_ref(text)
    if ref["form"] == "event":
        return {"event_id": ref["event_id"]}
    return {"object_id": ref["object_id"], "object_version": ref["object_version"], "revision_id": RID,
            "block": ref["block"], "component": ref["component"]}


def _store(object_type, written, *, version=1, ledger=None, server=None):
    pins = {text: _pin(text) for text in models.ref_texts(object_type, written)}
    return models.stored_payload(object_type, written, pins, version=version, ledger=ledger, server=server)


def _mission(**blocks):
    """版本 1 的 Mission：验收块两条验收条件，执行计划两条计划条目，打法块只有文字。"""
    written = models.validate_input("Mission", {
        "title": "试点", "goal_ref": f"{OID}@1", "external_refs": [{"system": "tianshu", "id": "m-1"}],
        "blocks": {"acceptance": {"text": "验收", "refs": [f"event:{EID}"], "components": [
                       {"id": "ac-1", "type": "acceptance_criterion", "text": "客户签字", "refs": [f"{OID}@1#acceptance/pg-1"]},
                       {"id": "ac-2", "type": "acceptance_criterion", "text": "上线"}]},
                   "execution_plan": {"components": [{"id": "p-1", "type": "plan_item", "text": "搭环境"},
                                                      {"id": "p-2", "type": "plan_item", "text": "培训",
                                                       "scope": f"{OID}@1"}]},
                   "play": {"text": "两场试点。"}, **blocks}})
    return _store("Mission", written, server={"responsible": PID, "core_battle": False,
                                              "depends_on": [_pin(f"{MID}@1")], "contributes_to": []})


def _merge(stored, patch, object_type="Mission"):
    return models.merge_revision(object_type, stored, patch)


def _ids(written, block):
    return [item["id"] for item in (written["blocks"][block] or {"components": []})["components"]]


# ------------------------------------------------------------------ written form
def test_the_written_form_turns_pins_back_into_text_and_drops_what_the_service_writes():
    stored = _mission()
    written = models.written_form("Mission", stored)
    assert set(written) == {"title", "external_refs", "goal_ref", "blocks"}
    assert written["goal_ref"] == f"{OID}@1"
    acceptance = written["blocks"]["acceptance"]
    assert acceptance["refs"] == [f"event:{EID}"]
    assert acceptance["components"][0]["refs"] == [f"{OID}@1#acceptance/pg-1"]
    assert written["blocks"]["execution_plan"]["components"][1]["scope"] == f"{OID}@1"
    assert models.validate_input("Mission", written) == written


def test_server_fields_are_the_event_written_attributes_and_the_relate_only_relations():
    assert models.server_fields("Mission", _mission()) == {
        "responsible": PID, "core_battle": False, "depends_on": [_pin(f"{MID}@1")], "contributes_to": []}


# ------------------------------------------------------------------ merging
def test_only_the_given_fields_and_blocks_change():
    stored = _mission()
    merged = _merge(stored, {"title": "试点二"})
    assert merged == {**models.written_form("Mission", stored), "title": "试点二"}


def test_text_refs_and_artifacts_of_a_block_are_replaced_whole_and_components_are_kept():
    merged = _merge(_mission(), {"blocks": {"acceptance": {"text": "新验收", "artifacts": ["https://example.test/a"]}}})
    acceptance = merged["blocks"]["acceptance"]
    assert acceptance["text"] == "新验收" and acceptance["artifacts"] == ["https://example.test/a"]
    assert acceptance["refs"] == [f"event:{EID}"] and _ids(merged, "acceptance") == ["ac-1", "ac-2"]
    merged = _merge(_mission(), {"blocks": {"acceptance": {"refs": []}}})
    assert merged["blocks"]["acceptance"]["refs"] == [] and merged["blocks"]["acceptance"]["text"] == "验收"


def test_components_merge_by_id_rewrite_in_place_remove_and_append_new_ones():
    merged = _merge(_mission(), {"blocks": {"execution_plan": {"components": [
        {"type": "plan_item", "text": "复盘"},                        # 新组件，id 由服务生成
        {"id": "p-1", "type": "plan_item", "text": "搭环境（已完成）"},  # 改写不换 id、不换位置
        {"id": "p-3", "type": "plan_item", "text": "验收会"},          # 写入者给 id 的新组件
        {"id": "p-2", "removed": True}]}}})
    plan = merged["blocks"]["execution_plan"]["components"]
    assert [item["id"] for item in plan] == ["p-1", None, "p-3"]
    assert plan[0]["text"] == "搭环境（已完成）" and plan[1]["text"] == "复盘"


def test_a_rewritten_component_is_replaced_whole():
    merged = _merge(_mission(), {"blocks": {"execution_plan": {"components": [
        {"id": "p-2", "type": "plan_item", "text": "培训（改）"}]}}})
    assert merged["blocks"]["execution_plan"]["components"][1] == {
        "id": "p-2", "type": "plan_item", "scope": None, "text": "培训（改）", "refs": [], "artifacts": [],
        "attributes": {"responsible": None}}


def test_a_null_block_clears_it_and_a_block_merged_to_nothing_is_stored_as_null():
    assert _merge(_mission(), {"blocks": {"execution_plan": None}})["blocks"]["execution_plan"] is None
    emptied = _merge(_mission(), {"blocks": {"execution_plan": {"components": [
        {"id": "p-1", "removed": True}, {"id": "p-2", "removed": True}]}}})
    assert emptied["blocks"]["execution_plan"] is None


def test_a_block_that_was_empty_takes_new_content():
    merged = _merge(_mission(), {"blocks": {"definition": {"components": []}, "constraint": {"text": "预算 10 万"}}})
    assert merged["blocks"]["definition"] is None and merged["blocks"]["constraint"]["text"] == "预算 10 万"


def _removed(stored, block, cid, version=2):
    """把某个组件在 version 删掉之后的存储载荷。"""
    written = _merge(stored, {"blocks": {block: {"components": [{"id": cid, "removed": True}]}}})
    return _store("Mission", written, version=version, ledger=stored["component_ledger"],
                  server=models.server_fields("Mission", stored))


def test_the_ledger_records_the_removal_version_and_new_components():
    stored = _removed(_mission(), "execution_plan", "p-2")
    ledger = {entry["id"]: entry for entry in stored["component_ledger"]}
    assert ledger["p-2"]["removed_in_version"] == 2 and ledger["p-1"]["removed_in_version"] is None
    written = _merge(stored, {"blocks": {"execution_plan": None}})
    after = _store("Mission", written, version=3, ledger=stored["component_ledger"])
    ledger = {entry["id"]: entry for entry in after["component_ledger"]}
    assert ledger["p-1"]["removed_in_version"] == 3 and ledger["p-2"]["removed_in_version"] == 2


@pytest.mark.parametrize("patch, says", [
    # 删掉的 id 不再复用
    ({"blocks": {"execution_plan": {"components": [{"id": "p-2", "type": "plan_item", "text": "又来"}]}}},
     "reused"),
    # 组件不跨块移动：另一块里还在的 id 不能出现在这一块
    ({"blocks": {"acceptance": {"components": [{"id": "p-1", "type": "acceptance_criterion", "text": "x"}]}}},
     "move"),
    # 删了再在另一块用同一个 id 加也不行
    ({"blocks": {"execution_plan": {"components": [{"id": "p-1", "removed": True}]},
                 "acceptance": {"components": [{"id": "p-1", "type": "acceptance_criterion", "text": "x"}]}}},
     "move"),
    # 同一个 id 不换类型
    ({"blocks": {"acceptance": {"components": [{"id": "ac-1", "type": "outcome", "text": "x"}]}}}, None),
    # 删除只删这一块里现存的组件
    ({"blocks": {"execution_plan": {"components": [{"id": "no-such", "removed": True}]}}}, "present"),
    ({"blocks": {"acceptance": {"components": [{"id": "p-1", "removed": True}]}}}, "present"),
    ({"blocks": {"execution_plan": {"components": [{"id": "p-2", "removed": True}]}}}, "present"),
    # 删除标记就是 {"id": …, "removed": true}
    ({"blocks": {"execution_plan": {"components": [{"id": "p-1", "removed": True, "text": "x"}]}}}, None),
    ({"blocks": {"execution_plan": {"components": [{"id": "p-1", "removed": False}]}}}, None),
    # 一次补丁里同一个 id 只出现一次
    ({"blocks": {"execution_plan": {"components": [{"id": "p-1", "type": "plan_item", "text": "a"},
                                                   {"id": "p-1", "removed": True}]}}}, "once"),
    # 补丁的形状
    ({"blocks": []}, None),
    ({"blocks": {"execution_plan": "x"}}, None),
    ({"blocks": {"execution_plan": {"components": {}}}}, None),
    ({"blocks": {"no_such_block": {"text": "x"}}}, None),
    ({"blocks": {"play": {"text": "x", "owner": "y"}}}, None),
    # 只由服务写的字段不能经修订写
    ({"responsible": PID}, None), ({"core_battle": True}, None), ({"depends_on": []}, None),
    ({"component_ledger": []}, None),
])
def test_a_revision_that_breaks_the_component_rules_is_refused(patch, says):
    stored = _removed(_mission(), "execution_plan", "p-2")
    with pytest.raises(ValueError) as error:
        _merge(stored, patch)
    assert says is None or says in str(error.value)


def test_a_revision_patch_is_an_object():
    with pytest.raises(ValueError):
        _merge(_mission(), ["title"])


# ------------------------------------------------------------------ what a revision touches
@pytest.mark.parametrize("object_type, patch, formal", [
    ("Mission", {"blocks": {"execution_plan": None}}, False),
    ("Mission", {"external_refs": []}, False),
    ("Mission", {"external_refs": [], "blocks": {"execution_plan": {"text": "x"}}}, False),
    ("Mission", {"blocks": {"play": {"text": "x"}}}, True),
    ("Mission", {"title": "x"}, True),
    ("Mission", {"goal_ref": f"{OID}@2"}, True),         # 建对象时写的关系按正式算
    ("Task", {"blocks": {"plan": {"text": "x"}}}, False),
    ("Task", {"blocks": {"plan": {"text": "x"}, "constraint": None}}, True),
    ("Activity", {"external_refs": []}, False),
    ("Activity", {"blocks": {"instruction": {"text": "x"}}}, True),
    ("Company", {}, False),
])
def test_a_revision_touches_formal_content_when_the_patch_names_a_formal_block_attribute_or_relation(
        object_type, patch, formal):
    assert models.touches_formal(object_type, patch) is formal


# ------------------------------------------------------------------ requests
def _revise(**params):
    return models.ACTION_PARAMS["world_revise_object"].model_validate({"payload": {"title": "x"}, **params})


def test_a_revision_request_carries_a_patch_and_an_optional_declaration():
    declaration = {"scene": f"{OID}@1", "trigger": "周会", "human_acceptance": {"required": False}}
    assert _revise(declaration=declaration).declaration.scene == f"{OID}@1"
    with pytest.raises(ValueError):
        _revise(payload="x")
    with pytest.raises(ValueError):
        _revise(extra=1)


def _relate(**params):
    return models.ACTION_PARAMS["world_relate"].model_validate({"field": "depends_on", "refs": [f"{OID}@1"], **params})


def test_a_relation_request_replaces_one_list_of_distinct_objects():
    assert _relate().refs == [f"{OID}@1"] and _relate(refs=[]).refs == []
    assert _relate(field="contributes_to").field == "contributes_to"
    for bad in ({"field": "goal_ref"}, {"refs": [f"{OID}@1", f"{OID}@2"]}, {"refs": [f"{OID}@1#play"]},
                {"refs": [f"event:{EID}"]}, {"declaration": {"scene": f"{OID}@1"}}):
        with pytest.raises(ValueError):
            _relate(**bad)


def test_revise_and_relate_are_implemented_on_their_registered_targets():
    registered = {item["action"]: frozenset(item["target_types"]) for item in REGISTRY["actions"]}
    for action in ("world_revise_object", "world_relate"):
        assert models.ACTION_TARGETS[action] == registered[action]
    assert models.ACTION_TARGETS["world_relate"] == frozenset({"PeriodGoal", "Mission", "Task"})
    relate_only = {(item["type"], relation["field"]) for item in REGISTRY["objects"]
                   for relation in item["relation_fields"] if relation["written_by"] == "world_relate"}
    assert {field for _, field in relate_only} == {"depends_on", "contributes_to"}
    assert {object_type for object_type, _ in relate_only} == models.ACTION_TARGETS["world_relate"]
