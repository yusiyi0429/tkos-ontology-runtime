"""tkos.world/0.2 长期目标与周期目标的收口：再确认、复盘确认、取消与终止、形成锚定（票 #60，契约第 6、7、10.2、10.3、
11、14 节；不连数据库）。

HTTP 路径（再确认不出修订、returns_to 只作记录、复盘确认的两种作用与撤回规则、取消与终止、形成锚定的放行与拒绝、
review_ref 的写入与草稿期改指、records.confirmed_review、各动作的代记）在 acceptance/world_v02 的 goal_closure 场景里；
这里只测请求模型、依据复盘的写入模型，以及它们和登记的对齐。
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
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
SID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"
RECONFIRM_LTG, RECONFIRM_PG = "world_reconfirm_long_term_goal", "world_reconfirm_period_goal"
REVIEW, CONFIRM_LTG = "world_confirm_review", "world_confirm_long_term_goal"
GATES = {RECONFIRM_LTG: "LongTermGoal", RECONFIRM_PG: "PeriodGoal", REVIEW: "StateSnapshot"}
CONTENT = {"text": "继续有效", "refs": [f"{OID}@1#outcome", f"event:{EID}"], "artifacts": ["https://example.test/r"]}
ON_BEHALF = {"principal_id": PID, "external_record_id": "tianshu:review:1",
             "external_confirmed_at": "2026-10-12T15:30:00+08:00"}
CANDIDATE = {"horizon": "2029"}


# ------------------------------------------------------------------ 本票接入的动作与目标
def test_the_reconfirmations_and_the_review_confirmation_are_gates_on_their_registered_targets():
    for action, target in GATES.items():
        assert action in models.ACTION_PARAMS
        assert models.ACTION_TARGETS[action] == {target} == set(ACTIONS[action]["target_types"])
        spec = ACTIONS[action]
        assert spec["class"] == "gate" and spec["agent_face"] is False and spec["delegable"] == "gate"
        assert spec["gate_roles"] == ["CEO"]


def test_cancel_lands_on_long_term_and_period_goals_as_well():
    assert models.ACTION_TARGETS["world_cancel"] == set(ACTIONS["world_cancel"]["target_types"]) == {
        "LongTermGoal", "PeriodGoal", "Mission", "Task", "Activity"}
    for action in ("world_start", "world_deliver", "world_accept", "world_reject", "world_reopen"):
        assert models.ACTION_TARGETS[action] == {"Mission", "Task", "Activity"}


def test_the_registry_routes_a_period_goal_review_through_its_snapshot_and_keeps_company_reviews():
    """本票依赖的两条登记：以周期目标为主体的复盘确认按周期目标的状态表走（经快照的主体），公司复盘确认不撤回。"""
    closing = [item for item in REGISTRY["lifecycles"]["PeriodGoal"]["transitions"] if item["action"] == REVIEW]
    assert [(item["from"], item["to"], item["via"], item["guard"], item["by"]) for item in closing] == [
        ("confirmed", "closed", "snapshot_subject", "review_of_subject", "gate_role")]
    assert all(REVIEW not in {item["action"] for item in spec["transitions"]}
               for object_type, spec in REGISTRY["lifecycles"].items() if object_type != "PeriodGoal")
    assert "review_confirmed_on_company" in REGISTRY["rules"]["withdrawal"]["never"]
    assert "reconfirm" in REGISTRY["rules"]["withdrawal"]["never"]


# ------------------------------------------------------------------ 再确认的参数
@pytest.mark.parametrize("action", [RECONFIRM_LTG, RECONFIRM_PG])
def test_a_reconfirmation_takes_neither_a_candidate_nor_an_outcome(action):
    fields = models.ACTION_PARAMS[action].model_fields
    assert ACTIONS[action]["candidate"] is False and ACTIONS[action]["outcomes"] == []
    assert not {"payload", "outcome", "supersedes_event_id", "declaration"} & set(fields)


@pytest.mark.parametrize("action", [RECONFIRM_LTG, RECONFIRM_PG])
@pytest.mark.parametrize("params", [{}, {"content": CONTENT}, {"on_behalf_of": ON_BEHALF},
                                    {"content": CONTENT, "on_behalf_of": ON_BEHALF}])
def test_a_reconfirmation_may_carry_content_and_be_recorded_on_behalf(action, params):
    value = models.ACTION_PARAMS[action].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("action", [RECONFIRM_LTG, RECONFIRM_PG])
@pytest.mark.parametrize("params", [
    {"payload": CANDIDATE},                                     # 不带候选、不出新修订
    {"outcome": "accepted"},                                    # 再确认没有结果
    {"outcome": "withdrawn", "supersedes_event_id": EID},       # 再确认不能撤回
    {"supersedes_event_id": EID},
    {"content": {"text": "   "}},
    {"content": {"text": "x", "components": [{"type": "outcome", "text": "y"}]}},
    {"declaration": {"scene": f"{OID}@1", "trigger": "x", "human_acceptance": {"required": False}}},
    {"phase": "initiation"},
])
def test_a_reconfirmation_outside_its_parameters_is_refused(action, params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(params)


# ------------------------------------------------------------------ 返回 M1-A（补 23）
@pytest.mark.parametrize("action, params", [
    (RECONFIRM_LTG, {"returns_to": "strategy"}),
    (CONFIRM_LTG, {"outcome": "accepted", "returns_to": "strategy"}),
    (CONFIRM_LTG, {"outcome": "accepted", "payload": CANDIDATE, "returns_to": "strategy"}),
    (CONFIRM_LTG, {"outcome": "returned", "returns_to": "strategy"}),
])
def test_a_long_term_goal_confirmation_or_reconfirmation_may_say_it_returns_to_strategy(action, params):
    value = models.ACTION_PARAMS[action].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert value["returns_to"] == "strategy" and set(value) == set(params)


@pytest.mark.parametrize("action, params", [
    (RECONFIRM_LTG, {"returns_to": "m1a"}),                                              # 只有 strategy
    (RECONFIRM_LTG, {"returns_to": ""}),
    (CONFIRM_LTG, {"outcome": "withdrawn", "supersedes_event_id": EID, "returns_to": "strategy"}),  # 撤回不是确认结果
    (RECONFIRM_PG, {"returns_to": "strategy"}),                                          # 只在长期目标上
    ("world_confirm_period_goal", {"outcome": "accepted", "returns_to": "strategy"}),
    ("world_commit_period_goal", {"returns_to": "strategy"}),
    (REVIEW, {"returns_to": "strategy"}),
])
def test_returns_to_is_only_strategy_and_only_on_a_long_term_goal_confirmation_or_reconfirmation(action, params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(params)


# ------------------------------------------------------------------ 复盘确认的参数
def test_a_review_confirmation_takes_the_outcomes_the_registry_lists_and_no_candidate():
    fields = models.ACTION_PARAMS[REVIEW].model_fields
    assert ACTIONS[REVIEW]["outcomes"] == ["withdrawn"] and ACTIONS[REVIEW]["candidate"] is False
    assert "payload" not in fields and not fields["outcome"].is_required()


@pytest.mark.parametrize("params", [
    {}, {"content": CONTENT}, {"outcome": "withdrawn", "supersedes_event_id": EID},
    {"on_behalf_of": ON_BEHALF}, {"outcome": "withdrawn", "supersedes_event_id": EID, "on_behalf_of": ON_BEHALF},
])
def test_a_review_confirmation_may_carry_content_be_a_withdrawal_or_be_recorded_on_behalf(params):
    value = models.ACTION_PARAMS[REVIEW].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("params", [
    {"outcome": "accepted"}, {"outcome": "returned"},           # 复盘确认只在撤回时带结果
    {"outcome": "withdrawn"}, {"supersedes_event_id": EID},     # 撤回与原事件成对
    {"payload": {"title": "x"}},                                # 目标是快照，快照不修订
    {"declaration": {"scene": f"{OID}@1", "trigger": "x", "human_acceptance": {"required": False}}},
])
def test_a_review_confirmation_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[REVIEW].model_validate(params)


# ------------------------------------------------------------------ 请求信封
def _request(action, params, contract="tkos.world/0.2", target=True):
    return {"action_type": action, "idempotency_key": "world-v02-closure-0001", "reason": "Synthetic closure",
            "contract_version": contract, "params": params, "expected_versions": [],
            "target": {"object_id": OID, "revision_id": RID, "expected_version": 1} if target else None}


@pytest.mark.parametrize("action", sorted(GATES))
def test_the_new_gates_parse_under_0_2_on_a_target_and_are_not_0_1_actions(action):
    from memory_service_runtime.governed.models import ActionRequest
    assert type(ActionRequest.model_validate(_request(action, {})).params) is models.ACTION_PARAMS[action]
    for refused in (_request(action, {}, target=False), _request(action, {}, contract="tkos.world/0.1")):
        with pytest.raises(ValueError):
            ActionRequest.model_validate(refused)


def test_a_cancel_command_without_the_new_fields_hashes_as_before():
    from memory_service_runtime.governed.models import ActionRequest
    request = ActionRequest.model_validate(_request("world_cancel", {}))
    assert request.model_dump(mode="json", exclude_none=True)["params"] == {}


# ------------------------------------------------------------------ 依据复盘 review_ref（契约第 6 节，补 7、24）
MINIMAL_GOAL = {"title": "10 月目标", "period": "2026-10", "goal_ref": f"{OID}@1"}


def test_a_period_goal_names_the_company_review_it_is_based_on_when_created():
    value = models.validate_input("PeriodGoal", {**MINIMAL_GOAL, "review_ref": f"{SID}@1"})
    assert value["review_ref"] == f"{SID}@1"
    assert models.ref_texts("PeriodGoal", value)[:2] == [f"{OID}@1", f"{SID}@1"]
    pins = {text: {"object_id": models.parse_ref(text)["object_id"], "object_version": 1, "revision_id": RID,
                   "block": None, "component": None} for text in models.ref_texts("PeriodGoal", value)}
    stored = models.stored_payload("PeriodGoal", value, pins, version=1)
    assert stored["review_ref"]["object_id"] == SID and stored["review_ref"]["revision_id"] == RID
    assert models.validate_input("PeriodGoal", MINIMAL_GOAL)["review_ref"] is None  # 第一个周期可以空


@pytest.mark.parametrize("text", [f"{SID}@1#results", f"{SID}@1#results/r-1", f"event:{EID}", "latest"])
def test_review_ref_names_the_snapshot_itself(text):
    with pytest.raises(ValueError):
        models.validate_input("PeriodGoal", {**MINIMAL_GOAL, "review_ref": text})


def _stored_goal(review_ref=None):
    written = models.validate_input("PeriodGoal", {**MINIMAL_GOAL, "review_ref": review_ref})
    pins = {text: {"object_id": models.parse_ref(text)["object_id"], "object_version": 1, "revision_id": RID,
                   "block": None, "component": None} for text in models.ref_texts("PeriodGoal", written)}
    return models.stored_payload("PeriodGoal", written, pins, version=1)


def test_review_ref_is_formal_content_that_a_direct_revision_rewrites():
    """review_ref 是建对象时写的正式关系：修订补丁里出现它就触及正式内容（只在草稿直接改，服务判）；合并时它按给出的
    值整体替换，可以改指另一条快照，也可以清空（改指与否的规则在服务里判，见补 7）。"""
    assert models.touches_formal("PeriodGoal", {"review_ref": None})
    assert models.merge_revision("PeriodGoal", _stored_goal(), {"review_ref": f"{SID}@1"})["review_ref"] == f"{SID}@1"
    assert models.merge_revision("PeriodGoal", _stored_goal(f"{SID}@1"), {"review_ref": None})["review_ref"] is None
    assert models.merge_revision("PeriodGoal", _stored_goal(f"{SID}@1"), {"title": "x"})["review_ref"] == f"{SID}@1"


def test_a_candidate_may_name_review_ref_and_keeps_it_when_written_back():
    models.check_candidate("PeriodGoal", {"review_ref": f"{SID}@1"})
    base = _stored_goal(f"{SID}@1")
    written = models.written_back("PeriodGoal", base, {"blocks": {"realization_logic": {"text": "两场试点"}}}, base)
    assert written["review_ref"] == f"{SID}@1"
