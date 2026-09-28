"""tkos.world/0.2 Strategy 的门：指定本轮、Agreement、确认生效与再确认（票 #59，契约第 9、10.1、11、12 节，决 13；
不连数据库）。

HTTP 路径（草稿走到已生效、已生效时带候选的一轮写回、下游仍钉旧版本、再确认、各类拒绝与库快照、Agreement 的代记与
重放）在 acceptance/world_v02 的 strategy_gates 场景里；这里只测请求模型、它们与登记的对齐，以及按记录顺序重放出的
当前一轮与一条 Agreement 的准入（守卫事实、重复、钉住的内容）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from memory_service_runtime.governed import world_v02_models as models
from memory_service_runtime.governed import world_v02_strategy as strategy

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
ACTIONS = {item["action"]: item for item in REGISTRY["actions"]}
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
PID2 = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"
STRATEGY_ACTIONS = ["world_assign_strategy_round", "world_agree_strategy", "world_confirm_strategy",
                    "world_reconfirm_strategy"]
ON_BEHALF = {"principal_id": PID, "external_record_id": "tianshu:agree:1",
             "external_confirmed_at": "2026-10-12T15:30:00+08:00"}
CANDIDATE = {"title": "2027 战略", "blocks": {"choices": {"text": "聚焦两个战场。"}}}


# ------------------------------------------------------------------ 登记与实现对齐
@pytest.mark.parametrize("action", STRATEGY_ACTIONS)
def test_the_four_strategy_actions_are_implemented_on_the_strategy(action):
    spec = ACTIONS[action]
    assert action in models.ACTION_PARAMS and action in models.GATE_ACTIONS
    assert models.ACTION_TARGETS[action] == {"Strategy"} == set(spec["target_types"])
    assert spec["agent_face"] is False and action not in REGISTRY["agent_face"]["writes"]


def test_the_round_is_opened_by_a_record_event_and_the_rest_are_gates():
    rounds = REGISTRY["lifecycles"]["Strategy"]["rounds"]
    assert rounds["opened_by"] == rounds["candidate_carried_by"] == "world_assign_strategy_round"
    assert (rounds["agreed_by"], rounds["closed_by"]) == ("world_agree_strategy", "world_confirm_strategy")
    assert ACTIONS["world_assign_strategy_round"]["class"] == "record"
    assert ACTIONS["world_assign_strategy_round"]["event_kind"] == "assign"
    assert {ACTIONS[action]["class"] for action in STRATEGY_ACTIONS[1:]} == {"gate"}


def test_agreement_is_authorized_by_scope_and_designation_and_the_rest_by_the_activation_policy():
    """决 13：Agreement 不按激活策略的角色表判权；确认与再确认的角色仍写在策略里（ADR-0005）。"""
    assert ACTIONS["world_agree_strategy"]["authorization"] == "scope_and_designation"
    assert ACTIONS["world_agree_strategy"]["gate_roles"] is None
    assert {ACTIONS[action]["authorization"] for action in STRATEGY_ACTIONS if action != "world_agree_strategy"} \
        == {"policy"}
    assert ACTIONS["world_confirm_strategy"]["gate_roles"] == ACTIONS["world_reconfirm_strategy"]["gate_roles"] == ["CEO"]


@pytest.mark.parametrize("action", STRATEGY_ACTIONS)
def test_a_strategy_action_takes_a_candidate_exactly_when_the_registry_says_so(action):
    assert ("payload" in models.ACTION_PARAMS[action].model_fields) is ACTIONS[action]["candidate"]


def _literals(annotation):
    return {value for arg in get_args(annotation)
            for value in (_literals(arg) if get_args(arg) else [arg]) if value is not type(None)}


@pytest.mark.parametrize("action", STRATEGY_ACTIONS)
def test_a_strategy_action_takes_the_outcomes_the_registry_lists(action):
    field = models.ACTION_PARAMS[action].model_fields.get("outcome")
    assert (_literals(field.annotation) if field else set()) == set(ACTIONS[action]["outcomes"])
    assert (field is not None and field.is_required()) is (ACTIONS[action]["event_kind"] == "confirm")


@pytest.mark.parametrize("action", STRATEGY_ACTIONS)
def test_every_strategy_action_can_be_recorded_on_behalf_of_a_person(action):
    family = ACTIONS[action]["delegable"]
    assert family in {"gate", "assign"} and "on_behalf_of" in models.ACTION_PARAMS[action].model_fields
    assert action in next(item["actions"] for item in REGISTRY["delegation"]["families"] if item["id"] == family)


# ------------------------------------------------------------------ 参数
@pytest.mark.parametrize("params", [
    {"principal_ids": [PID]}, {"principal_ids": [PID, PID2]}, {"principal_ids": [PID], "payload": CANDIDATE},
    {"principal_ids": [PID2, PID], "on_behalf_of": ON_BEHALF},
])
def test_designating_a_round_names_at_least_one_person_and_may_carry_a_candidate(params):
    value = models.ACTION_PARAMS["world_assign_strategy_round"].model_validate(params).model_dump(
        mode="json", exclude_none=True)
    assert value["principal_ids"] == params["principal_ids"] and value.get("payload") == params.get("payload")


@pytest.mark.parametrize("params", [
    {},                                                        # 至少一人
    {"principal_ids": []},
    {"principal_ids": [PID, PID]},                             # 各不相同
    {"principal_ids": ["ceo"]},
    {"principal_ids": [PID.upper()]},                          # 规范形 UUID
    {"principal_id": PID},                                     # 这不是通用指派
    {"principal_ids": [PID], "payload": ["title"]},            # 候选是合并补丁（对象）
    {"principal_ids": [PID], "content": {"text": "x"}},        # 指定不带事件内容
    {"principal_ids": [PID], "outcome": "withdrawn", "supersedes_event_id": EID},  # 记录事件不撤回
    {"principal_ids": [PID], "declaration": {"scene": f"{OID}@1", "trigger": "x",
                                             "human_acceptance": {"required": False}}},
])
def test_designating_a_round_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS["world_assign_strategy_round"].model_validate(params)


@pytest.mark.parametrize("params", [
    {}, {"content": {"text": "同意，理由见会议纪要。", "artifacts": ["https://example.test/minutes"]}},
    {"outcome": "withdrawn", "supersedes_event_id": EID}, {"on_behalf_of": ON_BEHALF},
])
def test_an_agreement_carries_the_persons_judgment_or_is_a_withdrawal(params):
    value = models.ACTION_PARAMS["world_agree_strategy"].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("params", [
    {"outcome": "accepted"},                                   # 只在撤回时带结果
    {"outcome": "withdrawn"},                                  # 撤回要引用原事件
    {"supersedes_event_id": EID},
    {"payload": CANDIDATE},                                    # 候选随开轮的指定，不随 Agreement
    {"content": {"text": "   "}},
    {"declaration": {"scene": f"{OID}@1", "trigger": "x", "human_acceptance": {"required": False}}},
])
def test_an_agreement_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS["world_agree_strategy"].model_validate(params)


@pytest.mark.parametrize("params", [
    {"outcome": "accepted"}, {"outcome": "returned", "content": {"text": "假设不成立"}},
    {"outcome": "withdrawn", "supersedes_event_id": EID},
])
def test_confirming_a_strategy_carries_its_outcome(params):
    value = models.ACTION_PARAMS["world_confirm_strategy"].model_validate(params).model_dump(mode="json",
                                                                                           exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("params", [
    {}, {"outcome": "accepted", "payload": CANDIDATE},         # 确认必带结果；候选已随开轮的指定留存
    {"outcome": "accepted", "supersedes_event_id": EID}, {"outcome": "kept"},
])
def test_confirming_a_strategy_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS["world_confirm_strategy"].model_validate(params)


@pytest.mark.parametrize("params", [{}, {"content": {"text": "本季度判断：继续有效。"}}, {"on_behalf_of": ON_BEHALF}])
def test_reconfirming_a_strategy_is_one_event_with_optional_content(params):
    value = models.ACTION_PARAMS["world_reconfirm_strategy"].model_validate(params).model_dump(mode="json",
                                                                                             exclude_none=True)
    assert set(value) == set(params)


@pytest.mark.parametrize("params", [
    {"outcome": "accepted"}, {"outcome": "withdrawn", "supersedes_event_id": EID},  # 再确认不撤回（补 28）
    {"supersedes_event_id": EID}, {"payload": CANDIDATE},                          # 不带候选、不出新修订
])
def test_reconfirming_a_strategy_outside_its_parameters_is_refused(params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS["world_reconfirm_strategy"].model_validate(params)


def _request(action, params, contract="tkos.world/0.2", target=True):
    body = {"action_type": action, "idempotency_key": "world-v02-strategy-0001", "reason": "Synthetic strategy",
            "contract_version": contract, "params": params, "expected_versions": [], "target": None}
    if target:
        body["target"] = {"object_id": OID, "revision_id": RID, "expected_version": 1}
    return body


@pytest.mark.parametrize("action, params", [
    ("world_assign_strategy_round", {"principal_ids": [PID]}), ("world_agree_strategy", {}),
    ("world_confirm_strategy", {"outcome": "accepted"}), ("world_reconfirm_strategy", {}),
])
def test_a_strategy_action_parses_under_0_2_on_a_target_only(action, params):
    from memory_service_runtime.governed.models import ActionRequest
    assert type(ActionRequest.model_validate(_request(action, params)).params) is models.ACTION_PARAMS[action]
    with pytest.raises(ValueError):
        ActionRequest.model_validate(_request(action, params, target=False))
    with pytest.raises(ValueError):  # 0.1 的 Strategy 是存根，没有这些动作
        ActionRequest.model_validate(_request(action, params, contract="tkos.world/0.1"))


# ------------------------------------------------------------------ 按记录顺序重放出的当前一轮
A, B, C = "a" * 8, "b" * 8, "c" * 8
V1 = {"object_id": OID, "object_version": 1, "revision_id": "rev-1", "block": None, "component": None}
V2 = {**V1, "object_version": 2, "revision_id": "rev-2"}
V3 = {**V1, "object_version": 3, "revision_id": "rev-3"}
counter = iter(range(1, 10_000))


def ev(action, *, outcome=None, supersedes=None, detail=None, pinned=V1, person="ceo"):
    return {"event_id": f"e{next(counter)}", "action": action, "outcome": outcome, "supersedes_event_id": supersedes,
            "detail": detail, "pinned": pinned, "person": person}


def assign(*people, candidate=False, pinned=V1):
    detail = {"principal_ids": list(people), **({"candidate": CANDIDATE} if candidate else {})}
    return ev("world_assign_strategy_round", detail=detail, pinned=pinned)


def agree(round_event, person, *, pinned=V1, complete=False):
    """一条已记的 Agreement：detail 同服务写的——本轮、所同意的内容、记下时是否补齐本轮。"""
    detail = {"round_event_id": round_event["event_id"], "object_version": pinned["object_version"],
              "revision_id": pinned["revision_id"],
              "candidate_event_id": round_event["event_id"] if "candidate" in round_event["detail"] else None,
              "round_complete": complete}
    return ev("world_agree_strategy", detail=detail, pinned=pinned, person=person)


def withdraw(original, person=None):
    return ev(original["action"], outcome="withdrawn", supersedes=original["event_id"],
              person=person or original["person"])


def people(current):
    return [item["principal_id"] for item in current["agreements"]]


def test_there_is_no_round_before_the_first_designation():
    assert strategy.current_round([]) is None
    assert strategy.current_round([ev("world_create_object"), ev("world_revise_object")]) is None


def test_a_designation_opens_a_round_and_agreements_are_recorded_in_it():
    opening = assign(A, B)
    first = agree(opening, A)
    current = strategy.current_round([ev("world_create_object"), opening, first])
    assert current == {"event_id": opening["event_id"], "designated": [A, B], "candidate": False,
                       "pinned": {"object_version": 1, "revision_id": "rev-1"},
                       "agreements": [{"event_id": first["event_id"], "principal_id": A, "object_version": 1,
                                       "revision_id": "rev-1", "round_complete": False}]}


def test_designating_again_opens_a_new_round_and_the_earlier_agreements_are_void():
    first = assign(A, B)
    again = assign(A, C)
    current = strategy.current_round([first, agree(first, A), again])
    assert current["event_id"] == again["event_id"] and current["designated"] == [A, C] and current["agreements"] == []


def test_a_withdrawn_agreement_no_longer_counts():
    opening = assign(A, B)
    completing = agree(opening, B, complete=True)
    current = strategy.current_round([opening, agree(opening, A), completing, withdraw(completing)])
    assert people(current) == [A]


def test_a_confirmation_ends_the_round_and_withdrawing_it_brings_the_round_back():
    opening = assign(A, B)
    events = [opening, agree(opening, A), agree(opening, B, complete=True)]
    for outcome in ("returned", "accepted"):
        confirmation = ev("world_confirm_strategy", outcome=outcome)
        assert strategy.current_round([*events, confirmation]) is None
        restored = strategy.current_round([*events, confirmation, withdraw(confirmation)])
        assert restored["event_id"] == opening["event_id"] and people(restored) == [A, B]


def test_withdrawing_the_formal_confirmation_drops_a_re_run_opened_since():
    opening = assign(A)
    formal = ev("world_confirm_strategy", outcome="accepted")
    rerun = assign(B, candidate=True)
    current = strategy.current_round([opening, agree(opening, A, complete=True), formal, rerun, withdraw(formal)])
    assert current["event_id"] == opening["event_id"]


def test_a_reconfirmation_ends_only_a_completed_round_without_a_candidate():
    """同引擎：结论为不改的一轮由再确认结束；未齐的、带候选的一轮不因再确认结束（补 21）。"""
    reconfirm = ev("world_reconfirm_strategy")
    plain = assign(A)
    assert strategy.current_round([plain, agree(plain, A, complete=True), reconfirm]) is None
    unfinished = assign(A, B)
    assert strategy.current_round([unfinished, agree(unfinished, A), reconfirm])["event_id"] == unfinished["event_id"]
    carrying = assign(A, candidate=True)
    assert strategy.current_round([carrying, agree(carrying, A, complete=True), reconfirm])["candidate"] is True
    assert strategy.current_round([reconfirm]) is None


# ------------------------------------------------------------------ 一条 Agreement 的准入
def test_without_a_round_neither_guard_holds():
    admitted = strategy.admission(None, "draft", V1, A)
    assert admitted == {"guards": {"round_complete": False, "round_incomplete": False}, "duplicate": False,
                        "detail": None}


def test_a_draft_agreement_pins_the_latest_revision_and_the_last_one_completes_the_round():
    opening = assign(A, B)
    first = strategy.admission(strategy.current_round([opening]), "draft", V1, A)
    assert first == {"guards": {"round_complete": False, "round_incomplete": True}, "duplicate": False,
                     "detail": {"round_event_id": opening["event_id"], "object_version": 1, "revision_id": "rev-1",
                                "candidate_event_id": None, "round_complete": False}}
    last = strategy.admission(strategy.current_round([opening, agree(opening, A)]), "draft", V1, B)
    assert last["guards"] == {"round_complete": True, "round_incomplete": False} and last["detail"]["round_complete"]


def test_an_agreement_on_an_earlier_revision_of_the_draft_does_not_count():
    opening = assign(A, B)
    current = strategy.current_round([opening, agree(opening, A, pinned=V1)])
    admitted = strategy.admission(current, "draft", V2, B)
    assert admitted["guards"]["round_complete"] is False and admitted["duplicate"] is False
    assert (admitted["detail"]["object_version"], admitted["detail"]["revision_id"]) == (2, "rev-2")
    assert strategy.pending(current, "draft", V2) == [A, B]
    assert strategy.admission(current, "draft", V2, A)["duplicate"] is False  # 新内容，可以再记


def test_the_same_person_agrees_to_the_same_content_once():
    opening = assign(A, B)
    current = strategy.current_round([opening, agree(opening, A)])
    assert strategy.admission(current, "draft", V1, A)["duplicate"] is True
    assert strategy.pending(current, "draft", V1) == [B]


def test_an_effective_rounds_agreement_pins_the_designation_and_its_candidate():
    """已生效时 Agreement 钉住本轮候选：候选是对指派钉住的修订的补丁；此后活动属性的修订不换所同意的内容。"""
    opening = assign(A, B, candidate=True, pinned=V2)
    current = strategy.current_round([opening, agree(opening, A, pinned=V2)])
    admitted = strategy.admission(current, "effective", V3, B)
    assert admitted["guards"] == {"round_complete": True, "round_incomplete": False}
    assert admitted["detail"] == {"round_event_id": opening["event_id"], "object_version": 2, "revision_id": "rev-2",
                                  "candidate_event_id": opening["event_id"], "round_complete": True}
    assert strategy.admission(current, "effective", V3, A)["duplicate"] is True
    assert strategy.pending(current, "effective", V3) == [B]


def test_nobody_is_pending_once_the_round_has_been_agreed():
    opening = assign(A, B)
    current = strategy.current_round([opening, agree(opening, A), agree(opening, B, complete=True)])
    assert strategy.pending(current, "agreed", V1) == []
