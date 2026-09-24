"""tkos.world/0.1 的对象载荷与引用写法（票 #20）：由登记生成的严格模型，不连数据库。

引用在写入时是业务形式的字符串，钉定（解析出修订 id）在服务里做；这里只测形状。
"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import world_v01_models as models

C = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
S = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"


def test_a_reference_is_written_in_its_business_form():
    assert models.parse_ref(f"{C}@3#identity") == {"object_id": C, "object_version": 3, "block": "identity"}
    assert models.parse_ref(f"{C}@1") == {"object_id": C, "object_version": 1, "block": None}


@pytest.mark.parametrize("text", [
    C,                          # 没有版本号
    f"{C}@0",                   # 版本号从 1 起
    f"{C}@01",                  # 版本号只有一种写法
    f"{C.upper()}@1",           # 对象 id 只有小写一种写法
    f"{C}@1#",                  # 有 # 就要有块路径
    f"{C}@1#Identity",          # 块 id 是英文 snake_case
    f"{C}@1#a#b",               # 块路径只一层
    f" {C}@1",
    "company@1",
])
def test_a_reference_outside_the_business_form_is_refused(text):
    with pytest.raises(ValueError):
        models.parse_ref(text)


U = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
L = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
P = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"
M = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
T = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e07"

# 每类建对象的最小载荷，与写入后得到的形状：登记里的每个块都在、空块为 null、可空的关系引用为 null。
CREATE = {
    "Strategy": ({"title": "E&O 战略", "parent_ref": f"{C}@1"},
                 {"title": "E&O 战略", "parent_ref": f"{C}@1",
                  "blocks": dict.fromkeys(["choices", "path", "assumptions", "capabilities",
                                           "responsibility_structure", "constraint"])}),
    "ResponsibilityUnit": ({"title": "E&O", "unit_kind": "battlefield",
                            "architecture_ref": f"{S}@2#responsibility_structure"},
                           {"title": "E&O", "unit_kind": "battlefield",
                            "architecture_ref": f"{S}@2#responsibility_structure",
                            "blocks": dict.fromkeys(["definition", "boundary", "constraint"])}),
    "LongTermGoal": ({"title": "E&O 年度目标", "scope": "unit", "horizon": "2027 年底",
                      "parent_ref": f"{U}@1", "goal_ref": f"{L}@1"},
                     {"title": "E&O 年度目标", "scope": "unit", "horizon": "2027 年底",
                      "parent_ref": f"{U}@1", "goal_ref": f"{L}@1",
                      "blocks": dict.fromkeys(["outcome", "measures", "constraint"])}),
    "PeriodGoal": ({"title": "9 月", "period": "2026-09", "goal_ref": f"{L}@1"},
                   {"title": "9 月", "period": "2026-09", "goal_ref": f"{L}@1",
                    "blocks": dict.fromkeys(["outcome", "acceptance", "constraint"])}),
    "Mission": ({"title": "9 月底 Agent 真实可用", "goal_ref": f"{P}@1"},
                {"title": "9 月底 Agent 真实可用", "goal_ref": f"{P}@1",
                 "blocks": dict.fromkeys(["definition", "acceptance", "play", "constraint"])}),
    "Task": ({"title": "数据环境准备", "parent_ref": f"{M}@1"},
             {"title": "数据环境准备", "parent_ref": f"{M}@1",
              "blocks": dict.fromkeys(["definition", "acceptance", "plan", "constraint"])}),
    "Activity": ({"title": "隔离库迁移与播种", "parent_ref": f"{T}@1"},
                 {"title": "隔离库迁移与播种", "parent_ref": f"{T}@1",
                  "blocks": dict.fromkeys(["instruction", "constraint"])}),
}


@pytest.mark.parametrize("object_type", sorted(CREATE))
def test_each_type_is_written_with_its_attributes_relation_refs_and_blocks(object_type):
    given, stored = CREATE[object_type]
    assert models.validate_input(object_type, given) == stored


def test_a_company_level_long_term_goal_may_leave_goal_ref_empty():
    got = models.validate_input("LongTermGoal", {"title": "公司目标", "scope": "company", "horizon": "三年",
                                                 "parent_ref": f"{C}@1"})
    assert got["goal_ref"] is None and got["parent_ref"] == f"{C}@1"


def test_block_references_are_kept_as_written_until_the_service_pins_them():
    got = models.validate_input("Strategy", {"title": "E&O 战略", "parent_ref": f"{C}@1", "blocks": {
        "choices": {"text": "聚焦经营系统", "refs": [f"{C}@1#identity", f"{C}@1"]}}})
    assert got["blocks"]["choices"] == {"text": "聚焦经营系统", "refs": [f"{C}@1#identity", f"{C}@1"], "artifacts": []}


@pytest.mark.parametrize("object_type, change", [
    ("Strategy", {"parent_ref": None}),                                        # 主干引用必填
    ("Strategy", {"parent_ref": f"{C}@1#identity"}),                           # 指向对象本身，不带块
    ("ResponsibilityUnit", {"architecture_ref": f"{S}@2"}),                    # 必须指向责任结构块
    ("ResponsibilityUnit", {"architecture_ref": f"{S}@2#choices"}),
    ("ResponsibilityUnit", {"unit_kind": "team"}),                             # 枚举取值
    ("LongTermGoal", {"scope": "global"}),
    ("LongTermGoal", {"horizon": None}),
    ("PeriodGoal", {"period": "2026-9"}),                                      # 月份形如 YYYY-MM
    ("PeriodGoal", {"period": "2026-13"}),
    ("Mission", {"goal_ref": f"{P}@1#outcome"}),
    ("Mission", {"depends_on": [f"{M}@1"]}),                                   # 跨链关系只经 world_relate
    ("Mission", {"contributes_to": [f"{P}@1"]}),
    ("Mission", {"responsible": C}),                                           # 责任人只由 assign 写
    ("Mission", {"core_battle": True}),                                        # 核心战役只由标记事件写
    ("Task", {"depends_on": [f"{T}@1"]}),
    ("Task", {"parent_ref": "数据环境准备"}),                                   # 引用只收业务形式
    ("Activity", {"blocks": {"plan": {"text": "不是 Activity 的块"}}}),
    ("Activity", {"blocks": {"instruction": {"text": "x", "refs": [f"{T}@1#"]}}}),
])
def test_a_create_payload_that_breaks_the_registry_is_refused(object_type, change):
    payload = {key: value for key, value in {**CREATE[object_type][0], **change}.items() if value is not None}
    with pytest.raises(ValueError):
        models.validate_input(object_type, payload)


R1 = "1b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
R2 = "1b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"


def test_pinned_references_replace_the_written_ones_and_server_owned_fields_start_empty():
    given = models.validate_input("Mission", {"title": "9 月底 Agent 真实可用", "goal_ref": f"{P}@1", "blocks": {
        "play": {"text": "先打通 MCP", "refs": [f"{P}@1#outcome"]}}})
    pins = {f"{P}@1": {"object_id": P, "object_version": 1, "revision_id": R1, "block": None},
            f"{P}@1#outcome": {"object_id": P, "object_version": 1, "revision_id": R1, "block": "outcome"}}
    assert models.stored_payload("Mission", given, pins) == {
        "title": "9 月底 Agent 真实可用",
        "goal_ref": {"object_id": P, "object_version": 1, "revision_id": R1, "block": None},
        "depends_on": [], "contributes_to": [], "responsible": None, "core_battle": False,
        "blocks": {"definition": None, "acceptance": None, "constraint": None,
                   "play": {"text": "先打通 MCP", "artifacts": [],
                            "refs": [{"object_id": P, "object_version": 1, "revision_id": R1, "block": "outcome"}]}}}
    assert models.ref_texts("Mission", given) == [f"{P}@1", f"{P}@1#outcome"]


def test_a_written_reference_is_listed_for_pinning_wherever_it_appears():
    given = models.validate_input("StateSnapshot", {"title": "状态", "subject_ref": f"{M}@2",
                                                    "as_of": "2026-09-24T09:37:00Z",
                                                    "blocks": {"issue": {"text": "卡在数据", "refs": [f"{T}@1#plan"]}}})
    assert models.ref_texts("StateSnapshot", given) == [f"{M}@2", f"{T}@1#plan"]


NINE = ["Company", "Strategy", "ResponsibilityUnit", "LongTermGoal", "PeriodGoal", "Mission", "Task", "Activity",
        "StateSnapshot"]


def _paths(model):
    """模型的字段路径：顶层字段、blocks.<块 id>、blocks.<块 id>.<三件套字段>。"""
    from typing import get_args
    fields = model.model_fields
    paths = set(fields)
    if "blocks" in fields:
        for block_id, field in fields["blocks"].annotation.model_fields.items():
            paths.add(f"blocks.{block_id}")
            block_type = next(arg for arg in get_args(field.annotation) if arg is not type(None))
            paths.update(f"blocks.{block_id}.{name}" for name in block_type.model_fields)
    return paths


@pytest.mark.parametrize("object_type", NINE)
def test_the_stored_model_has_exactly_the_field_paths_the_registry_declares(object_type):
    import json
    from pathlib import Path
    registry = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/world-registry-0.1.json").read_text())
    spec = next(item for item in registry["objects"] if item["type"] == object_type)
    declared = {"blocks"} | {a["id"] for a in spec["attributes"]} | {f["field"] for f in spec["relation_fields"]}
    for block in spec["blocks"]:
        declared |= {f"blocks.{block['id']}"} | {f"blocks.{block['id']}.{name}" for name in registry["block_value_fields"]}
    assert _paths(models.stored_model(object_type)) == declared
    server_owned = {a["id"] for a in spec["attributes"] if a["set_by"]} | {
        f["field"] for f in spec["relation_fields"] if f["written_by"] != "world_create_object"}
    assert _paths(models.input_model(object_type)) == declared - server_owned


@pytest.mark.parametrize("given, stored", [
    ("2026-09-24T17:37:00+08:00", "2026-09-24T09:37:00Z"),
    ("2026-09-24T09:37:00.250Z", "2026-09-24T09:37:00.250000Z"),
    ("2026-09-24T09:37:00.000000+00:00", "2026-09-24T09:37:00Z"),
])
def test_a_snapshot_time_is_stored_as_utc_text_with_one_spelling_per_instant(given, stored):
    payload = {"title": "状态", "subject_ref": f"{M}@1", "as_of": given}
    assert models.validate_input("StateSnapshot", payload)["as_of"] == stored


@pytest.mark.parametrize("change", [
    {"as_of": "2026-09-24T09:37:00"},              # 没有时区的时刻有歧义
    {"as_of": "2026-09-24"},
    {"as_of": "2026-09-24 09:37:00Z"},
    {"subject_ref": f"{M}@1#play"},                # 主体是对象本身
    {"period": "2026-9"},
])
def test_a_snapshot_payload_outside_the_contract_is_refused(change):
    with pytest.raises(ValueError):
        models.validate_input("StateSnapshot", {"title": "状态", "subject_ref": f"{M}@1",
                                                "as_of": "2026-09-24T09:37:00Z", **change})


DECLARATION = {"scene": f"{M}@1", "trigger": "9/23 会后整理会议结论",
               "human_acceptance": {"required": True, "acceptor": C}}


def _create(**params):
    return {"domain_id": C, "object_type": "Task", "payload": {}, **params}


def test_a_write_declaration_names_its_scene_trigger_and_human_acceptance():
    params = models.WorldCreateObjectParams.model_validate(_create(declaration=DECLARATION))
    assert params.model_dump(mode="json", exclude_none=True)["declaration"] == DECLARATION
    unattended = {**DECLARATION, "human_acceptance": {"required": False}}
    params = models.WorldCreateObjectParams.model_validate(_create(declaration=unattended))
    assert params.model_dump(mode="json", exclude_none=True)["declaration"] == unattended
    assert "declaration" not in models.WorldCreateObjectParams.model_validate(_create()).model_dump(exclude_none=True)


@pytest.mark.parametrize("declaration", [
    {key: value for key, value in DECLARATION.items() if key != "scene"},
    {key: value for key, value in DECLARATION.items() if key != "trigger"},
    {key: value for key, value in DECLARATION.items() if key != "human_acceptance"},
    {**DECLARATION, "trigger": "  "},
    {**DECLARATION, "scene": f"{M}@1#play"},                                  # 场景是对象本身
    {**DECLARATION, "human_acceptance": {"required": True}},                  # 需要人工验收就要有验收人
    {**DECLARATION, "human_acceptance": {"required": False, "acceptor": C}},
    {**DECLARATION, "note": "x"},
])
def test_a_declaration_missing_or_misstating_an_item_is_refused(declaration):
    with pytest.raises(ValueError):
        models.WorldCreateObjectParams.model_validate(_create(declaration=declaration))
