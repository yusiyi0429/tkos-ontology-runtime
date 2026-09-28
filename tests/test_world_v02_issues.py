"""tkos.world/0.2 的 Issue：问题组件的提出、路由、承接、处置与退回形成（票 #61，契约第 8、9、13、15.1 节；不连数据库）。

HTTP 路径（状态表每一格、六类处置、各类拒绝与库快照不变、事件与回执、业务对象的生命周期不变、读投影
records.open_issues、更正指向 Issue 事件）在 acceptance/world_v02 的 issues 场景里；这里只测请求模型、它们和登记的
对齐与读投影的装配。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
ACTIONS = {item["action"]: item for item in REGISTRY["actions"]}
ISSUE = REGISTRY["issue"]
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
ISSUE_REF = f"{OID}@1#issues/iss-1"
DECLARATION = {"scene": f"{OID}@1", "trigger": "Co-Agent 周检", "human_acceptance": {"required": False}}
REASON = {"text": "本层可以处理：把试点推迟一周。"}
# 每个动作最小的合法参数。
MINIMAL = {"world_raise_issue": {"issue_ref": ISSUE_REF},
           "world_route_issue": {"issue_ref": ISSUE_REF, "to_principal_id": PID},
           "world_own_issue": {"issue_ref": ISSUE_REF},
           "world_dispose_issue": {"issue_ref": ISSUE_REF, "disposition": "current_layer_action", "content": REASON},
           "world_return_issue": {"issue_ref": ISSUE_REF}}


# ------------------------------------------------------------------ 登记与实现对齐
def test_the_five_issue_actions_are_the_ones_of_the_registered_issue_table():
    assert models.ISSUE_ACTIONS == tuple(dict.fromkeys(item["action"] for item in ISSUE["lifecycle"]["transitions"]))
    assert set(models.ISSUE_ACTIONS) == set(MINIMAL)


@pytest.mark.parametrize("action", sorted(MINIMAL))
def test_an_issue_action_is_implemented_as_a_targetless_policy_authorized_record_event(action):
    spec = ACTIONS[action]
    assert action in models.ACTION_PARAMS and models.ACTION_TARGETS[action] == frozenset()
    assert spec["class"] == "record" and spec["authorization"] == "policy" and spec["target_types"] == []
    assert spec["event_kind"].startswith("issue.") and spec["delegable"] is None  # 第一版不可代记
    assert "on_behalf_of" not in models.ACTION_PARAMS[action].model_fields


@pytest.mark.parametrize("action", sorted(MINIMAL))
def test_an_issue_action_on_the_agent_face_takes_a_declaration_and_the_person_only_ones_do_not(action):
    on_face = action in REGISTRY["agent_face"]["writes"]
    assert on_face is ACTIONS[action]["agent_face"]
    assert ("declaration" in models.ACTION_PARAMS[action].model_fields) is on_face
    assert on_face is (action in {"world_raise_issue", "world_route_issue", "world_return_issue"})


def test_the_six_dispositions_are_the_registered_ones_in_order():
    assert models.DISPOSITIONS == tuple(item["id"] for item in ISSUE["dispositions"])
    assert models.DISPOSITIONS == tuple(item["id"] for item in REGISTRY["event_attribute_values"]["disposition"])
    assert ISSUE["disposition_reason_required"] is True


# ------------------------------------------------------------------ 参数
@pytest.mark.parametrize("action", sorted(MINIMAL))
def test_the_minimal_parameters_of_each_issue_action_parse(action):
    value = models.ACTION_PARAMS[action].model_validate(MINIMAL[action]).model_dump(mode="json", exclude_none=True)
    assert value["issue_ref"] == ISSUE_REF


@pytest.mark.parametrize("action", ["world_raise_issue", "world_route_issue", "world_return_issue"])
def test_an_agent_face_issue_action_takes_content_and_a_declaration(action):
    params = {**MINIMAL[action], "content": {"text": "见评审记录", "refs": [f"event:{EID}", f"{OID}@1#issues/iss-0"]},
              "declaration": DECLARATION}
    value = models.ACTION_PARAMS[action].model_validate(params).model_dump(mode="json", exclude_none=True)
    assert value["content"]["refs"] == [f"event:{EID}", f"{OID}@1#issues/iss-0"]
    assert value["declaration"]["scene"] == f"{OID}@1"


@pytest.mark.parametrize("disposition", list(models.DISPOSITIONS))
def test_each_of_the_six_dispositions_parses_with_its_reason(disposition):
    params = {"issue_ref": ISSUE_REF, "disposition": disposition, "content": REASON}
    value = models.WorldV02DisposeIssueParams.model_validate(params).model_dump(mode="json", exclude_none=True)
    assert value["disposition"] == disposition and value["content"]["text"] == REASON["text"]


@pytest.mark.parametrize("action", sorted(MINIMAL))
@pytest.mark.parametrize("change", [
    {"issue_ref": None},                                       # 必带问题
    {"issue_ref": f"{OID}@1"},                                 # 只收组件形式
    {"issue_ref": f"{OID}@1#issues"},
    {"issue_ref": f"event:{EID}"},
    {"issue_ref": "iss-1"},
    {"target": {"object_id": OID, "revision_id": RID, "expected_version": 1}},
    {"outcome": "withdrawn"},                                  # Issue 事件是记录事件，不撤回
    {"supersedes_event_id": EID},
    {"on_behalf_of": {"principal_id": PID, "external_record_id": "tianshu-1",
                      "external_confirmed_at": "2026-10-12T15:30:00+08:00"}},   # 第一版不可代记
    {"content": {"text": "   "}},                              # 内容给了就要有东西
    {"content": {"text": "x", "components": [{"type": "issue", "text": "y"}]}},  # 事件内容不带组件
])
def test_issue_parameters_outside_their_shape_are_refused(action, change):
    params = {key: value for key, value in {**MINIMAL[action], **change}.items() if value is not None}
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(params)


@pytest.mark.parametrize("change", [
    {"to_principal_id": None},                                 # 路由必带承接人
    {"to_principal_id": "owner-a"},
    {"to_principal_ids": [PID]},
])
def test_a_route_names_exactly_one_principal(change):
    params = {key: value for key, value in {**MINIMAL["world_route_issue"], **change}.items() if value is not None}
    with pytest.raises(ValueError):
        models.WorldV02RouteIssueParams.model_validate(params)


@pytest.mark.parametrize("change", [
    {"disposition": None},                                     # 处置必带六类之一
    {"disposition": "close"},
    {"disposition": "Route"},                                  # M1-A、M3 的叫法只作对照，不收
    {"content": None},                                         # 缺理由
    {"content": {"text": "  \n"}},
    {"content": {"refs": [f"event:{EID}"]}},                   # 理由写在文字里
    {"content": {"artifacts": ["https://example.test/why"]}},
])
def test_a_disposition_without_one_of_the_six_or_without_its_reason_is_refused(change):
    params = {key: value for key, value in {**MINIMAL["world_dispose_issue"], **change}.items() if value is not None}
    with pytest.raises(ValueError):
        models.WorldV02DisposeIssueParams.model_validate(params)


@pytest.mark.parametrize("action", ["world_own_issue", "world_dispose_issue"])
def test_owning_and_disposing_take_no_declaration(action):
    """承接与处置只由人记、不在 Agent 面上，不带写入声明（同指派）。"""
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate({**MINIMAL[action], "declaration": DECLARATION})


# ------------------------------------------------------------------ 请求信封
def _request(action, params, contract="tkos.world/0.2", target=None):
    return {"action_type": action, "idempotency_key": "world-v02-issues-0001", "reason": "Synthetic issue",
            "contract_version": contract, "params": params, "expected_versions": [], "target": target}


@pytest.mark.parametrize("action", sorted(MINIMAL))
def test_an_issue_action_parses_under_0_2_without_a_target_and_nowhere_else(action):
    from memory_service_runtime.governed.models import ActionRequest
    assert type(ActionRequest.model_validate(_request(action, MINIMAL[action])).params) is models.ACTION_PARAMS[action]
    with pytest.raises(ValueError):  # 问题以 issue_ref 指明，不带目标
        ActionRequest.model_validate(_request(action, MINIMAL[action],
                                              target={"object_id": OID, "revision_id": RID, "expected_version": 1}))
    with pytest.raises(ValueError):  # 0.1 没有 Issue 动作
        ActionRequest.model_validate(_request(action, MINIMAL[action], contract="tkos.world/0.1"))


# ------------------------------------------------------------------ 读投影
def test_the_records_group_carries_the_open_issues_it_is_given():
    from memory_service_runtime.governed import world_v02_readers as readers
    head = {"object_id": OID, "object_type": "Company", "object_version": 1, "lifecycle_status": "recorded",
            "latest_revision_id": RID, "effective_revision_id": RID, "domain_id": "d"}
    revision = {"revision_id": RID, "object_version": 1, "payload": {"title": "E&O", "blocks": {
        "identity": None, "constraint": None}}}
    opened = [{"component_id": "iss-1", "lifecycle": {"status": "routed"}}]
    view = readers.object_view(head, revision, {}, responsible=[], open_issues=opened)
    assert view["records"]["open_issues"] == opened
    assert readers.object_view(head, revision, {}, responsible=[])["records"]["open_issues"] == []
