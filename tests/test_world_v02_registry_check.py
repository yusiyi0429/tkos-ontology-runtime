"""tkos.world/0.2 登记自检：现有登记通过，故意改坏的登记被指出来。

登记直接读 docs/contracts/world-registry-0.2.json；每个用例改坏一处，断言自检报出这一处。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from memory_service_runtime.governed.world_v02_registry_check import check

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "world-registry-0.2.json"


@pytest.fixture
def registry():
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def broken(registry, edit):
    copied = copy.deepcopy(registry)
    edit(copied)
    return check(copied)


def test_the_registry_passes_its_self_check(registry):
    assert check(registry) == []


def test_deleting_an_action_is_caught(registry):
    def edit(reg):
        reg["actions"] = [item for item in reg["actions"] if item["action"] != "world_reopen"]

    errors = broken(registry, edit)
    assert any("world_reopen" in error for error in errors)


def test_cutting_off_a_state_is_caught(registry):
    def edit(reg):
        spec = reg["lifecycles"]["Mission"]
        spec["transitions"] = [item for item in spec["transitions"] if item["to"] != "adjusting"]

    errors = broken(registry, edit)
    assert any("Mission" in error and "adjusting" in error for error in errors)


def test_a_transition_into_an_undeclared_state_is_caught(registry):
    def edit(reg):
        reg["lifecycles"]["Task"]["transitions"][0]["to"] = "limbo"

    assert any("limbo" in error for error in broken(registry, edit))


def test_an_undefined_guard_is_caught(registry):
    def edit(reg):
        reg["guards"] = [item for item in reg["guards"] if item["id"] != "parent_goal_confirmed"]

    assert any("parent_goal_confirmed" in error for error in broken(registry, edit))


def test_an_undefined_recorder_is_caught(registry):
    def edit(reg):
        reg["recorders"] = [item for item in reg["recorders"] if item["id"] != "self_or_agent"]

    assert any("self_or_agent" in error for error in broken(registry, edit))


def test_a_transition_on_an_action_that_does_not_target_the_type_is_caught(registry):
    def edit(reg):
        spec = reg["lifecycles"]["Strategy"]
        spec["transitions"].append({**spec["transitions"][-1], "action": "world_start"})

    assert any("Strategy" in error and "world_start" in error for error in broken(registry, edit))


def test_an_outcome_the_action_does_not_have_is_caught(registry):
    def edit(reg):
        for item in reg["lifecycles"]["Task"]["transitions"]:
            if item["action"] == "world_accept":
                item["outcome"] = "accepted"

    assert any("accepted" in error and "world_accept" in error for error in broken(registry, edit))


def test_a_block_allowing_an_unknown_component_type_is_caught(registry):
    def edit(reg):
        mission = next(item for item in reg["objects"] if item["type"] == "Mission")
        mission["blocks"][0]["components"].append("milestone")

    assert any("milestone" in error for error in broken(registry, edit))


def test_a_component_type_no_block_allows_is_caught(registry):
    def edit(reg):
        for spec in reg["objects"] + reg["state"]["payload_types"]:
            for block in spec["blocks"]:
                block["components"] = [item for item in block["components"] if item != "assumption"]

    assert any("assumption" in error for error in broken(registry, edit))


def test_a_payload_that_does_not_list_its_subject_type_is_caught(registry):
    def edit(reg):
        payload = next(item for item in reg["state"]["payload_types"] if item["id"] == "execution_state")
        payload["subjects"].remove("Activity")

    assert any("Activity" in error and "execution_state" in error for error in broken(registry, edit))


def test_an_action_whose_event_kind_is_not_in_the_vocabulary_is_caught(registry):
    def edit(reg):
        reg["event_kinds"] = [item for item in reg["event_kinds"] if item["kind"] != "reopen"]

    assert any("reopen" in error for error in broken(registry, edit))


def test_an_event_kind_no_action_produces_is_caught(registry):
    def edit(reg):
        reg["event_kinds"].append({**reg["event_kinds"][0], "kind": "object.archived"})

    assert any("object.archived" in error for error in broken(registry, edit))


def test_an_action_whose_class_differs_from_its_event_kind_is_caught(registry):
    def edit(reg):
        next(item for item in reg["actions"] if item["action"] == "world_start")["class"] = "gate"

    assert any("world_start" in error for error in broken(registry, edit))


def test_a_round_naming_an_unknown_action_is_caught(registry):
    def edit(reg):
        reg["lifecycles"]["PeriodGoal"]["rounds"]["opened_by"] = "world_commit_goal"

    assert any("world_commit_goal" in error for error in broken(registry, edit))


def test_a_round_voided_in_an_undeclared_state_is_caught(registry):
    def edit(reg):
        reg["lifecycles"]["Mission"]["rounds"]["voided_in"] = ["closed", "archived"]

    assert any("Mission" in error and "archived" in error for error in broken(registry, edit))


def test_a_gated_type_without_formal_stage_is_caught(registry):
    def edit(reg):
        reg["lifecycles"]["Mission"]["formal_on"] = None

    assert any("Mission" in error and "formal_on" in error for error in broken(registry, edit))


def test_an_issue_disposition_without_a_transition_is_caught(registry):
    def edit(reg):
        spec = reg["issue"]["lifecycle"]
        spec["transitions"] = [item for item in spec["transitions"] if item["disposition"] != "pushback"]

    assert any("pushback" in error for error in broken(registry, edit))
