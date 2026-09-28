"""tkos.world/0.2 Mission 的执行生命周期与关注标记（票 #55，契约第 9、10.4、11、12 节；不连数据库）。

HTTP 路径（状态表每一格、Owner 的 Agent 开始、判权、撤回、关注标记、上层不改变下层、各段的一轮重走与终态的修订
规则）在 acceptance/world_v02 的 mission_lifecycle 场景里；生命周期引擎按登记逐格穷举在 test_world_v02_lifecycle.py。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
ACTIONS = {item["action"]: item for item in REGISTRY["actions"]}
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
LIFECYCLE = ["world_start", "world_deliver", "world_accept", "world_reject", "world_reopen", "world_cancel"]
MARK = "world_mark_core_battle"


# ------------------------------------------------------------------ 本票接入的动作与目标
def test_the_six_lifecycle_actions_land_on_a_mission_as_well():
    for action in LIFECYCLE:  # 取消另接长期目标与周期目标（票 #60）
        assert models.ACTION_TARGETS[action] >= {"Mission", "Task", "Activity"}
        assert models.ACTION_TARGETS[action] <= set(ACTIONS[action]["target_types"])


def test_the_core_battle_mark_is_implemented_on_a_mission_only():
    assert MARK in models.ACTION_PARAMS
    assert models.ACTION_TARGETS[MARK] == {"Mission"} == set(ACTIONS[MARK]["target_types"])
    # 方案 A：一条记录事件，只由持策略角色 CEO 的人记，不在 Agent 面上，不撤回。
    assert ACTIONS[MARK]["class"] == "record" and ACTIONS[MARK]["gate_roles"] == ["CEO"]
    assert ACTIONS[MARK]["agent_face"] is False and ACTIONS[MARK]["outcomes"] == []


# ------------------------------------------------------------------ 关注标记的参数
@pytest.mark.parametrize("params", [
    {},
    {"content": {"text": "关注：影响 Q4 回款", "refs": [f"{OID}@1#play", f"event:{EID}"],
                 "artifacts": ["https://example.test/why"]}},
])
def test_a_mark_takes_only_optional_content(params):
    value = models.ACTION_PARAMS[MARK].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("params", [
    {"outcome": "withdrawn", "supersedes_event_id": EID},       # 记录事件不撤回
    {"supersedes_event_id": EID},
    {"outcome": "accepted"},
    {"payload": {"core_battle": True}},                          # core_battle 只由服务按事件置
    {"core_battle": True},
    {"declaration": {"scene": f"{OID}@1", "trigger": "x", "human_acceptance": {"required": False}}},  # 只由人记
    {"content": {"text": "   "}},                                # 空内容不能冒充内容
    {"content": {"text": "x", "components": [{"type": "outcome", "text": "y"}]}},  # 事件内容不带组件
    {"phase": "initiation"},                                     # 0.2 没有阶段
])
def test_a_mark_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[MARK].model_validate(params)


# ------------------------------------------------------------------ 请求信封
def _request(action, params, contract="tkos.world/0.2"):
    return {"action_type": action, "idempotency_key": "world-v02-mission-0001", "reason": "Synthetic mission",
            "contract_version": contract, "params": params, "expected_versions": [],
            "target": {"object_id": OID, "revision_id": RID, "expected_version": 1}}


def test_a_mark_parses_under_the_version_it_declares():
    from memory_service_runtime.governed.models import ActionRequest
    from memory_service_runtime.governed.world_v01_models import ACTION_PARAMS as V01
    assert type(ActionRequest.model_validate(_request(MARK, {})).params) is models.ACTION_PARAMS[MARK]
    assert type(ActionRequest.model_validate(_request(MARK, {}, contract="tkos.world/0.1")).params) is V01[MARK]
    assert models.ACTION_PARAMS[MARK] is not V01[MARK]
