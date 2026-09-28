"""tkos.world/0.2 有门对象的承诺与确认、候选与写回（票 #54，契约第 9、10.2–10.4、11、12 节；不连数据库）。

HTTP 路径（三类对象走门、带候选写回、一轮重走、守卫、块类别的修订规则、拒绝与库快照）在 acceptance/world_v02 的
gates 场景里；这里只测请求模型、候选的形状与写回内容的计算。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
ACTIONS = {item["action"]: item for item in REGISTRY["actions"]}
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
GATES = {"world_commit_period_goal": "PeriodGoal", "world_confirm_period_goal": "PeriodGoal",
         "world_confirm_long_term_goal": "LongTermGoal", "world_commit_mission": "Mission",
         "world_confirm_mission": "Mission"}
CONFIRMS = ["world_confirm_period_goal", "world_confirm_long_term_goal", "world_confirm_mission"]
COMMITS = ["world_commit_period_goal", "world_commit_mission"]


# ------------------------------------------------------------------ 本票接入的门动作
def test_the_five_gates_of_this_ticket_are_implemented_on_their_registered_targets():
    for action, target in GATES.items():
        assert action in models.ACTION_PARAMS
        assert models.ACTION_TARGETS[action] == {target} == set(ACTIONS[action]["target_types"])
        assert ACTIONS[action]["class"] == "gate" and ACTIONS[action]["agent_face"] is False


def test_the_gates_of_later_tickets_are_not_implemented_yet():
    for action in ("world_reconfirm_period_goal", "world_reconfirm_long_term_goal", "world_confirm_review",
                   "world_agree_strategy", "world_confirm_strategy"):  # 关注标记随票 #55 接入
        assert action not in models.ACTION_PARAMS


@pytest.mark.parametrize("action", sorted(GATES))
def test_a_gate_takes_a_candidate_exactly_when_the_registry_says_so(action):
    assert ("payload" in models.ACTION_PARAMS[action].model_fields) is ACTIONS[action]["candidate"]


def _literals(annotation):
    """Literal 与 Optional[Literal] 里列出的取值。"""
    return {value for arg in get_args(annotation)
            for value in (_literals(arg) if get_args(arg) else [arg]) if value is not type(None)}


@pytest.mark.parametrize("action", sorted(GATES))
def test_a_gate_takes_the_outcomes_the_registry_lists(action):
    field = models.ACTION_PARAMS[action].model_fields["outcome"]
    assert _literals(field.annotation) == set(ACTIONS[action]["outcomes"])
    assert field.is_required() is (ACTIONS[action]["event_kind"] == "confirm")  # 确认必带 outcome


# ------------------------------------------------------------------ 参数
CANDIDATE = {"title": "新标题", "blocks": {"outcome": {"text": "签 5 家"}}}
CONTENT = {"text": "候选稿", "refs": [f"{OID}@1#outcome", f"event:{EID}"], "artifacts": ["https://example.test/c"]}


@pytest.mark.parametrize("action", COMMITS)
@pytest.mark.parametrize("params", [
    {}, {"payload": CANDIDATE}, {"content": CONTENT}, {"payload": CANDIDATE, "content": CONTENT},
    {"outcome": "withdrawn", "supersedes_event_id": EID},
])
def test_a_commitment_may_carry_a_candidate_content_or_be_a_withdrawal(action, params):
    value = models.ACTION_PARAMS[action].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params) and value.get("payload") == params.get("payload")


@pytest.mark.parametrize("action", COMMITS)
@pytest.mark.parametrize("params", [
    {"outcome": "withdrawn"},                                                  # 撤回要引用原事件
    {"supersedes_event_id": EID},                                              # 引用原事件的只能是撤回
    {"outcome": "accepted"},                                                   # 承诺只在撤回时带结果
    {"outcome": "withdrawn", "supersedes_event_id": EID, "payload": CANDIDATE},  # 撤回不带候选
    {"payload": ["title"]},                                                    # 候选是合并补丁（对象）
    {"content": {"text": "   "}},                                              # 空内容不能冒充内容
    {"content": {"text": "x", "components": [{"type": "outcome", "text": "y"}]}},  # 事件内容不带组件
    {"declaration": {"scene": f"{OID}@1", "trigger": "x", "human_acceptance": {"required": False}}},  # 门不带声明
    {"phase": "initiation"},                                                   # 0.2 没有阶段
])
def test_a_commitment_outside_its_parameters_is_refused(action, params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(params)


@pytest.mark.parametrize("action", CONFIRMS)
@pytest.mark.parametrize("params", [
    {"outcome": "accepted"}, {"outcome": "returned", "content": {"text": "验收标准不可测"}},
    {"outcome": "withdrawn", "supersedes_event_id": EID},
])
def test_a_confirmation_carries_its_outcome(action, params):
    value = models.ACTION_PARAMS[action].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert set(value) == set(params) and value["outcome"] == params["outcome"]


@pytest.mark.parametrize("action", CONFIRMS)
@pytest.mark.parametrize("params", [
    {},                                                          # 确认必带 outcome
    {"content": {"text": "x"}},
    {"outcome": "withdrawn"},
    {"outcome": "accepted", "supersedes_event_id": EID},
    {"outcome": "returned", "supersedes_event_id": EID},
    {"outcome": "kept"},
])
def test_a_confirmation_outside_its_parameters_is_refused(action, params):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(params)


@pytest.mark.parametrize("action", ["world_confirm_period_goal", "world_confirm_mission"])
def test_only_a_long_term_goal_confirmation_carries_a_candidate(action):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate({"outcome": "accepted", "payload": CANDIDATE})


def test_a_long_term_goal_confirmation_carries_a_candidate_only_when_it_accepts():
    params = models.ACTION_PARAMS["world_confirm_long_term_goal"]
    assert params.model_validate({"outcome": "accepted", "payload": CANDIDATE}).payload == CANDIDATE
    for refused in ({"outcome": "returned", "payload": CANDIDATE},
                    {"outcome": "withdrawn", "supersedes_event_id": EID, "payload": CANDIDATE}):
        with pytest.raises(ValueError):
            params.model_validate(refused)


# ------------------------------------------------------------------ 请求信封
def _request(action, params, contract="tkos.world/0.2"):
    return {"action_type": action, "idempotency_key": "world-v02-gates-0001", "reason": "Synthetic gate",
            "contract_version": contract, "params": params, "expected_versions": [],
            "target": {"object_id": OID, "revision_id": RID, "expected_version": 1}}


@pytest.mark.parametrize("action", sorted(GATES))
def test_a_gate_parses_under_the_version_it_declares(action):
    from memory_service_runtime.governed.models import ActionRequest
    from memory_service_runtime.governed import world_v01_models
    params = {"outcome": "accepted"} if action in CONFIRMS else {}
    assert type(ActionRequest.model_validate(_request(action, params)).params) is models.ACTION_PARAMS[action]
    v01 = {**params, "phase": "initiation"} if action.endswith("_mission") else params
    assert type(ActionRequest.model_validate(_request(action, v01, contract="tkos.world/0.1")).params) \
        is world_v01_models.ACTION_PARAMS[action]
    with pytest.raises(ValueError):  # 门落在对象上
        ActionRequest.model_validate({**_request(action, params), "target": None})


# ------------------------------------------------------------------ 候选（契约第 12 节）
def _pin(text):
    ref = models.parse_ref(text)
    if ref["form"] == "event":
        return {"event_id": ref["event_id"]}
    return {"object_id": ref["object_id"], "object_version": ref["object_version"], "revision_id": RID,
            "block": ref["block"], "component": ref["component"]}


def _store(object_type, written, *, version=1, ledger=None, server=None):
    pins = {text: _pin(text) for text in models.ref_texts(object_type, written)}
    return models.stored_payload(object_type, written, pins, version=version, ledger=ledger, server=server)


def _mission():
    """版本 1 的 Mission：打法、验收两条验收条件、执行计划两条计划条目、一条外部引用，已指派 Owner。"""
    written = models.validate_input("Mission", {
        "title": "试点", "goal_ref": f"{OID}@1", "external_refs": [{"system": "tianshu", "id": "m-1"}],
        "blocks": {"play": {"text": "两场试点。"},
                   "acceptance": {"components": [{"id": "ac-1", "type": "acceptance_criterion", "text": "客户签字"},
                                                 {"id": "ac-2", "type": "acceptance_criterion", "text": "上线"}]},
                   "execution_plan": {"components": [{"id": "p-1", "type": "plan_item", "text": "搭环境"},
                                                      {"id": "p-2", "type": "plan_item", "text": "培训"}]}}})
    return _store("Mission", written, server={"responsible": PID, "core_battle": False, "depends_on": [],
                                              "contributes_to": []})


def _revised(stored, patch, version):
    """stored 之后按补丁直接修订出的下一版（同服务：台账续记，只由服务写的字段沿用）。"""
    return _store("Mission", models.merge_revision("Mission", stored, patch), version=version,
                  ledger=stored["component_ledger"], server=models.server_fields("Mission", stored))


@pytest.mark.parametrize("object_type, patch", [
    ("Mission", {"title": "x", "goal_ref": f"{OID}@2", "blocks": {"play": {"text": "x"}, "acceptance": None}}),
    ("PeriodGoal", {"period": "2026-11", "blocks": {"realization_logic": {"text": "x"}}}),
    ("LongTermGoal", {"horizon": "2029", "blocks": {"measures": None}}),
    ("Mission", {}),
])
def test_a_candidate_carries_formal_blocks_formal_attributes_and_creation_relations(object_type, patch):
    models.check_candidate(object_type, patch)


@pytest.mark.parametrize("object_type, patch", [
    ("Mission", {"blocks": {"execution_plan": {"text": "x"}}}),       # 活动块直接修订，不走候选
    ("Mission", {"external_refs": []}),                               # 活动属性同上
    ("Mission", {"responsible": PID}),                                # 只由服务写的字段
    ("Mission", {"core_battle": True}),
    ("Mission", {"depends_on": []}),                                  # 只经 world_relate 写
    ("PeriodGoal", {"review_ref": f"{OID}@1"}),                       # 依据复盘随票 #60
    ("Mission", {"no_such_field": "x"}),
    ("Mission", {"blocks": {"no_such_block": {"text": "x"}}}),
    ("Mission", ["title"]),
    ("Mission", {"blocks": ["play"]}),
])
def test_a_candidate_with_anything_but_formal_content_is_refused(object_type, patch):
    with pytest.raises(ValueError):
        models.check_candidate(object_type, patch)


def test_new_components_of_a_candidate_get_their_ids_before_it_is_kept():
    patch = {"title": "x", "blocks": {
        "acceptance": {"text": "验收", "components": [{"type": "acceptance_criterion", "text": "新"},
                                                     {"id": "ac-1", "type": "acceptance_criterion", "text": "改"},
                                                     {"id": "ac-2", "removed": True}]},
        "play": None, "definition": {"text": "定义"}}}
    filled = models.with_component_ids(patch)
    new, kept, removed = filled["blocks"]["acceptance"]["components"]
    assert models.COMPONENT_ID.fullmatch(new["id"]) and len(new["id"]) == 36
    assert {**new, "id": None} == {**patch["blocks"]["acceptance"]["components"][0], "id": None}
    assert (kept, removed) == (patch["blocks"]["acceptance"]["components"][1], {"id": "ac-2", "removed": True})
    assert {key: value for key, value in filled.items() if key != "blocks"} == {"title": "x"}
    assert filled["blocks"]["play"] is None and filled["blocks"]["definition"] == {"text": "定义"}
    assert patch["blocks"]["acceptance"]["components"][0] == {"type": "acceptance_criterion", "text": "新"}
    assert models.with_component_ids(filled) == filled  # 定下之后不再变
    for shapeless in (["x"], {"blocks": "x"}, {"blocks": {"play": {"components": "x"}}}):
        assert models.with_component_ids(shapeless) == shapeless  # 形状留给合并去拒绝


CANDIDATE_MISSION = {"title": "试点（改打法）", "blocks": {
    "play": {"text": "一场试点加一次复盘。"},
    "acceptance": {"components": [{"id": "ac-3", "type": "acceptance_criterion", "text": "复盘通过"},
                                  {"id": "ac-2", "removed": True}]}}}


def test_writing_back_takes_formal_content_from_the_candidate_and_activity_content_as_it_is_now():
    base = _mission()
    # 一轮进行中：执行计划与外部引用照常直接修订，之后又被再指派（只由服务写的字段）。
    later = _revised(base, {"external_refs": [{"system": "tianshu", "id": "m-2"}], "blocks": {"execution_plan": {
        "components": [{"id": "p-3", "type": "plan_item", "text": "联调"}, {"id": "p-1", "removed": True}]}}}, 2)
    written = models.written_back("Mission", base, CANDIDATE_MISSION, later)
    assert written["title"] == "试点（改打法）" and written["goal_ref"] == f"{OID}@1"
    assert written["blocks"]["play"]["text"] == "一场试点加一次复盘。"
    assert [c["id"] for c in written["blocks"]["acceptance"]["components"]] == ["ac-1", "ac-3"]
    assert [c["id"] for c in written["blocks"]["execution_plan"]["components"]] == ["p-2", "p-3"]
    assert written["external_refs"] == [{"system": "tianshu", "id": "m-2", "url": None}]
    assert "responsible" not in written and "component_ledger" not in written
    stored = _store("Mission", written, version=3, ledger=later["component_ledger"],
                    server=models.server_fields("Mission", later))
    ledger = {entry["id"]: entry for entry in stored["component_ledger"]}
    assert (ledger["ac-2"]["removed_in_version"], ledger["ac-3"]["added_in_version"]) == (3, 3)
    assert (ledger["p-1"]["removed_in_version"], ledger["p-3"]["added_in_version"]) == (2, 2)


def test_writing_back_on_the_revision_the_candidate_was_made_on_is_the_merged_revision():
    base = _mission()
    assert models.written_back("Mission", base, CANDIDATE_MISSION, base) == \
        models.merge_revision("Mission", base, CANDIDATE_MISSION)


def test_writing_the_same_candidate_back_again_gives_the_same_formal_content():
    """撤回让内容成为正式的确认后再确认，候选原样写回：组件是同一批 id，删掉的仍是删掉。"""
    base = _mission()
    candidate = models.with_component_ids({"blocks": {"acceptance": {"components": [
        {"type": "acceptance_criterion", "text": "复盘通过"}, {"id": "ac-2", "removed": True}]}}})
    first = _store("Mission", models.written_back("Mission", base, candidate, base), version=2,
                   ledger=base["component_ledger"], server=models.server_fields("Mission", base))
    again = models.written_back("Mission", base, candidate, first)
    assert again == models.written_form("Mission", first)


@pytest.mark.parametrize("since, says", [
    # 候选新加的组件 id 在它提交之后被执行计划用上了：组件 id 在对象内唯一、不跨块
    ({"blocks": {"execution_plan": {"components": [{"id": "ac-3", "type": "plan_item", "text": "x"}]}}}, "move"),
    # 候选改写的组件在它提交之后被删掉了（退回、直接修订、再撤回那条退回的路上）：删掉的 id 不再复用
    ({"blocks": {"acceptance": {"components": [{"id": "ac-1", "removed": True}]}}}, "reused"),
])
def test_writing_back_a_candidate_that_no_longer_fits_the_ledger_is_refused(since, says):
    base = _mission()
    later = _revised(base, since, 2)
    candidate = {"blocks": {"acceptance": {"components": [
        {"id": "ac-1", "type": "acceptance_criterion", "text": "客户书面签字"},
        {"id": "ac-3", "type": "acceptance_criterion", "text": "复盘通过"}]}}}
    with pytest.raises(ValueError) as error:
        models.written_back("Mission", base, candidate, later)
    assert says in str(error.value)


def test_writing_back_refuses_a_candidate_that_is_not_formal_content():
    with pytest.raises(ValueError):
        models.written_back("Mission", _mission(), {"blocks": {"execution_plan": None}}, _mission())
