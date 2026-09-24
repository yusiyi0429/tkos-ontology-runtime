"""tkos.world/0.1 的修订与建关系载荷（票 #21）：合并修订、只由服务写的字段沿用、关系列表参数，不连数据库。"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import world_v01_models as models

C = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
P = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"
M = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
M2 = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e08"
R1 = "1b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
R2 = "1b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
PERSON = "2b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"


def pinned(object_id, version, revision_id, block=None):
    return {"object_id": object_id, "object_version": version, "revision_id": revision_id, "block": block}


STRATEGY = {
    "title": "E&O 战略", "parent_ref": pinned(C, 1, R1),
    "blocks": {"choices": {"text": "聚焦经营系统", "refs": [pinned(C, 1, R1, "identity")], "artifacts": []},
               "path": {"text": "先 E&O 后交付", "refs": [], "artifacts": []},
               "assumptions": None, "capabilities": None, "responsibility_structure": None, "constraint": None}}

MISSION = {
    "title": "9 月底 Agent 真实可用", "goal_ref": pinned(P, 1, R1), "responsible": PERSON, "core_battle": True,
    "depends_on": [pinned(M2, 1, R2)], "contributes_to": [],
    "blocks": {"definition": None, "acceptance": None, "play": None, "constraint": None}}


def test_a_revision_changes_only_the_fields_and_blocks_it_names():
    patch = {"title": "E&O 战略（修订）", "blocks": {"path": None, "assumptions": {"text": "客户愿意付费"}}}
    assert models.merge_revision("Strategy", STRATEGY, patch) == {
        "title": "E&O 战略（修订）", "parent_ref": f"{C}@1",
        "blocks": {"choices": {"text": "聚焦经营系统", "refs": [f"{C}@1#identity"], "artifacts": []},
                   "path": None,
                   "assumptions": {"text": "客户愿意付费", "refs": [], "artifacts": []},
                   "capabilities": None, "responsibility_structure": None, "constraint": None}}


def test_an_empty_patch_keeps_the_current_version_in_its_written_form():
    written = models.merge_revision("Mission", MISSION, {})
    assert written == {"title": "9 月底 Agent 真实可用", "goal_ref": f"{P}@1",
                       "blocks": {"definition": None, "acceptance": None, "play": None, "constraint": None}}


def test_fields_only_the_service_writes_carry_over_into_the_new_version():
    written = models.merge_revision("Mission", MISSION, {"blocks": {"play": {"text": "先打通 MCP"}}})
    pins = {f"{P}@1": pinned(P, 1, R1)}
    stored = models.stored_payload("Mission", written, pins, server=models.server_fields("Mission", MISSION))
    assert stored == {**MISSION, "blocks": {**MISSION["blocks"],
                                            "play": {"text": "先打通 MCP", "refs": [], "artifacts": []}}}


@pytest.mark.parametrize("patch", [
    {"responsible": PERSON},                       # 责任人只由 assign 写
    {"core_battle": False},                        # 核心战役只由标记事件写
    {"depends_on": []},                            # 跨链关系只经 world_relate 写
    {"owner": "x"},                                # 契约之外的字段
    {"title": None},                               # 必填属性不能清空
    {"blocks": {"plan": {"text": "不是 Mission 的块"}}},
    {"blocks": {"play": {"text": "  "}}},          # 空内容不能冒充有内容；清空块要给 null
    {"blocks": None},
])
def test_a_revision_patch_outside_the_contract_is_refused(patch):
    with pytest.raises(ValueError):
        models.merge_revision("Mission", MISSION, patch)


def test_a_relate_command_replaces_one_relation_list_with_object_references():
    params = models.WorldRelateParams.model_validate({"field": "depends_on", "refs": [f"{M2}@1", f"{P}@3"]})
    assert params.model_dump(mode="json", exclude_none=True) == {"field": "depends_on", "refs": [f"{M2}@1", f"{P}@3"]}
    assert models.WorldRelateParams.model_validate({"field": "contributes_to", "refs": []}).refs == []


@pytest.mark.parametrize("params", [
    {"field": "parent_ref", "refs": [f"{M2}@1"]},              # 只有跨链关系字段经 world_relate 写
    {"field": "depends_on", "refs": [f"{M2}@1#play"]},         # 关系指向对象本身
    {"field": "depends_on", "refs": [f"{M2}@1", f"{M2}@2"]},   # 同一对象不重复
    {"field": "depends_on"},
    {"field": "depends_on", "refs": [], "note": "x"},
])
def test_a_relate_command_outside_its_shape_is_refused(params):
    with pytest.raises(ValueError):
        models.WorldRelateParams.model_validate(params)


def test_a_revise_command_carries_a_patch_and_an_optional_declaration():
    declaration = {"scene": f"{M}@1", "trigger": "会后整理",
                   "human_acceptance": {"required": True, "acceptor": PERSON}}
    params = models.WorldReviseObjectParams.model_validate({"payload": {"title": "x"}, "declaration": declaration})
    assert params.model_dump(mode="json", exclude_none=True) == {"payload": {"title": "x"}, "declaration": declaration}
    with pytest.raises(ValueError):
        models.WorldReviseObjectParams.model_validate({"payload": {"title": "x"}, "domain_id": C})
