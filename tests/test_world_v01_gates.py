"""tkos.world/0.1 的门（票 #24）：门动作的参数形状，不连数据库；期望值取自契约第 9 节的门表。
门准入（一条门事件现在能不能记、对正式内容指针做什么）在 test_world_v01_lifecycle.py 里与生命周期推导一起测。
"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import world_v01_models as models

E = "3b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
GATES = {"world_commit_period_goal": "PeriodGoal", "world_commit_mission": "Mission",
         "world_confirm_long_term_goal": "LongTermGoal", "world_confirm_period_goal": "PeriodGoal",
         "world_confirm_mission": "Mission", "world_confirm_mission_core_battle": "Mission",
         "world_mark_core_battle": "Mission"}


def gate(action, params):
    return models.ACTION_PARAMS[action].model_validate(params).model_dump(mode="json", exclude_none=True)


# ------------------------------------------------------------ gate action parameters
def test_each_gate_action_targets_exactly_its_type():
    assert {action: set(models.ACTION_TARGETS[action]) for action in GATES} == {
        action: {object_type} for action, object_type in GATES.items()}


def test_a_mission_gate_names_its_phase_and_a_confirmation_its_outcome():
    assert gate("world_commit_mission", {"phase": "initiation"}) == {"phase": "initiation"}
    assert gate("world_confirm_mission", {"phase": "delivery", "outcome": "returned",
                                          "content": {"text": "验收标准没有达到。"}}) == {
        "phase": "delivery", "outcome": "returned",
        "content": {"text": "验收标准没有达到。", "refs": [], "artifacts": []}}
    assert gate("world_confirm_period_goal", {"outcome": "accepted"}) == {"outcome": "accepted"}
    assert gate("world_mark_core_battle", {}) == {}


def test_a_withdrawal_references_the_event_it_withdraws():
    assert gate("world_commit_period_goal", {"outcome": "withdrawn", "supersedes_event_id": E}) == {
        "outcome": "withdrawn", "supersedes_event_id": E}


def test_a_candidate_travels_with_a_commitment_or_an_accepting_confirmation():
    patch = {"blocks": {"play": {"text": "先打通数据环境。"}}}
    assert gate("world_commit_mission", {"phase": "initiation", "payload": patch})["payload"] == patch
    assert gate("world_confirm_long_term_goal", {"outcome": "accepted", "payload": {"title": "x"}})["payload"] == {
        "title": "x"}


@pytest.mark.parametrize("action, params", [
    ("world_commit_mission", {}),                                              # Mission 的门带 phase
    ("world_commit_mission", {"phase": "review"}),
    ("world_confirm_mission_core_battle", {"phase": "delivery", "outcome": "accepted"}),  # CEO 只确认立项
    ("world_commit_period_goal", {"phase": "initiation"}),                     # 目标的门不带 phase
    ("world_confirm_period_goal", {}),                                         # 确认必须带 outcome
    ("world_confirm_long_term_goal", {"outcome": "approved"}),
    ("world_commit_period_goal", {"outcome": "accepted"}),                     # 承诺只在撤回时带 outcome
    ("world_commit_period_goal", {"outcome": "withdrawn"}),                    # 撤回必须引用原事件
    ("world_confirm_period_goal", {"outcome": "accepted", "supersedes_event_id": E}),  # 只有撤回引用原事件
    ("world_confirm_period_goal", {"outcome": "returned", "payload": {"title": "x"}}),  # 退回不带候选
    ("world_commit_period_goal", {"outcome": "withdrawn", "supersedes_event_id": E, "payload": {"title": "x"}}),
    ("world_mark_core_battle", {"outcome": "accepted"}),                       # 标核心战役没有 outcome
    ("world_mark_core_battle", {"payload": {"core_battle": True}}),
    ("world_confirm_mission", {"phase": "initiation", "outcome": "accepted", "content": {"text": " "}}),
    ("world_confirm_mission", {"phase": "initiation", "outcome": "accepted", "declaration": {}}),
])
def test_a_gate_command_outside_its_shape_is_refused(action, params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(params)


def test_gate_phases_and_outcomes_are_the_registrys():
    import json
    from pathlib import Path
    registry = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/world-registry-0.1.json").read_text())
    gated = {spec["action"]: spec for spec in registry["actions"] if spec["gate_roles"] is not None}
    assert {action: (spec["target_type"], spec["event_kind"], tuple(spec["phases"]), tuple(spec["outcomes"]))
            for action, spec in gated.items()} == models.GATES
    for spec in gated.values():
        for phase in spec["phases"] or [None]:
            for outcome in spec["outcomes"] + ([None] if spec["event_kind"] != "confirm" else []):
                params = {key: value for key, value in (("phase", phase), ("outcome", outcome)) if value}
                if outcome == "withdrawn":
                    params["supersedes_event_id"] = E
                assert gate(spec["action"], params) == params
