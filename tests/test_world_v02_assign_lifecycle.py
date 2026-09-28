"""tkos.world/0.2 的指派与 Task、Activity 生命周期动作（票 #53，契约第 9、10.5、10.6、11 节；不连数据库）。

HTTP 路径（每格一条、拒绝与库快照、Agent 验收、撤回）在 acceptance/world_v02 的 assign_lifecycle 场景里。
"""
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
LIFECYCLE = ["world_start", "world_deliver", "world_accept", "world_reject", "world_reopen", "world_cancel"]
DECLARATION = {"scene": f"{OID}@1", "trigger": "执行", "human_acceptance": {"required": False}}


# ------------------------------------------------------------------ 本票接入的动作与目标
def test_the_assignment_and_the_six_lifecycle_actions_are_implemented():
    assert {"world_assign", *LIFECYCLE} <= set(models.ACTION_PARAMS)
    assert models.ACTION_TARGETS["world_assign"] == {"ResponsibilityUnit", "Mission", "Task", "Activity"}
    for action in LIFECYCLE:  # Mission 随票 #55，长期目标与周期目标的取消随票 #60
        assert models.ACTION_TARGETS[action] == {"Task", "Activity"}


def test_the_targets_stay_within_the_registered_target_types():
    registered = {item["action"]: set(item["target_types"]) for item in REGISTRY["actions"]}
    for action in ("world_assign", *LIFECYCLE):
        assert models.ACTION_TARGETS[action] <= registered[action]
        assert next(item for item in REGISTRY["actions"] if item["action"] == action)["class"] == (
            "record" if action == "world_assign" else "lifecycle")


# ------------------------------------------------------------------ 参数
def test_an_assignment_names_only_the_assignee():
    assert models.WorldV02AssignParams.model_validate({"principal_id": PID}).principal_id == PID
    for bad in ({}, {"principal_id": "someone"}, {"principal_id": PID, "valid_from": "2026-10-01T00:00:00Z"}):
        with pytest.raises(ValueError):
            models.WorldV02AssignParams.model_validate(bad)


@pytest.mark.parametrize("params", [
    {},
    {"content": {"text": "交付说明", "refs": [f"{OID}@1#acceptance/ac-1", f"event:{EID}"],
                 "artifacts": ["https://example.test/delivery"]}},
    {"outcome": "withdrawn", "supersedes_event_id": EID},
    {"declaration": DECLARATION},
])
def test_a_lifecycle_action_takes_optional_content_a_withdrawal_and_a_declaration(params):
    value = models.WorldV02LifecycleParams.model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("params", [
    {"outcome": "withdrawn"},                                   # 撤回要引用原事件
    {"supersedes_event_id": EID},                               # 引用原事件的只能是撤回
    {"outcome": "accepted", "supersedes_event_id": EID},        # 生命周期事件只在撤回时带结果
    {"content": {"text": "   "}},                               # 空内容不能冒充内容
    {"content": {"text": "x", "components": [{"type": "acceptance_criterion", "text": "y"}]}},  # 事件内容不带组件
    {"content": {"text": "x", "refs": [OID]}},                  # 引用用业务形式
    {"declaration": {**DECLARATION, "scene": f"{OID}@1#acceptance"}},  # 场景只用对象形式
    {"payload": {"title": "x"}},                                # 生命周期动作不改内容
])
def test_a_lifecycle_action_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.WorldV02LifecycleParams.model_validate(params)


# ------------------------------------------------------------------ 请求信封
def _request(action, params, contract="tkos.world/0.2", target=True):
    body = {"action_type": action, "idempotency_key": "world-v02-lifecycle-0001", "reason": "Synthetic lifecycle",
            "contract_version": contract, "params": params, "expected_versions": []}
    if target:
        body["target"] = {"object_id": OID, "revision_id": RID, "expected_version": 1}
    return body


@pytest.mark.parametrize("action", LIFECYCLE)
def test_a_lifecycle_action_lands_on_a_target_and_parses_under_0_2(action):
    from memory_service_runtime.governed.models import ActionRequest
    assert type(ActionRequest.model_validate(_request(action, {})).params) is models.WorldV02LifecycleParams
    with pytest.raises(ValueError):
        ActionRequest.model_validate(_request(action, {}, target=False))
    with pytest.raises(ValueError):  # 0.1 没有这些动作
        ActionRequest.model_validate(_request(action, {}, contract="tkos.world/0.1"))


def test_an_assignment_parses_under_the_version_it_declares():
    from memory_service_runtime.governed.models import ActionRequest
    from memory_service_runtime.governed.world_v01_models import WorldAssignParams as V01
    assert type(ActionRequest.model_validate(_request("world_assign", {"principal_id": PID})).params) \
        is models.WorldV02AssignParams
    assert type(ActionRequest.model_validate(_request("world_assign", {"principal_id": PID},
                                                      contract="tkos.world/0.1")).params) is V01
