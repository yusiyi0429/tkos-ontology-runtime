"""tkos.world/0.2 的代记：委托的登记与撤销、代记写入的参数（票 #62，契约第 8.1、9、14 节；不连数据库）。

HTTP 路径（登记与撤销、按被代记的人判权的通过与各类拒绝、库快照不变、事件与回执、本人撤回、读投影 identity 的
当前有效委托、重放）在 acceptance/world_v02 的 delegation 场景里；这里测请求模型与它们和登记的对齐，以及议题族
（票 #71，补 49）的代记判定——换掉读库的几处，跑服务里真实的判权路径。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
ACTIONS = {item["action"]: item for item in REGISTRY["actions"]}
DELEGATION = REGISTRY["delegation"]
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
DID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"
GRANT = {"delegate_principal_id": PID, "families": ["gate", "lifecycle"], "domain_ids": [DID],
         "valid_until": "2026-10-31T23:59:59+08:00"}
ON_BEHALF = {"principal_id": PID, "external_record_id": "tianshu:confirm:123",
             "external_confirmed_at": "2026-10-12T15:30:00+08:00"}


# ------------------------------------------------------------------ 登记与实现对齐
def test_granting_and_revoking_a_delegation_are_implemented_as_scope_authorized_record_events_without_a_target():
    for action in (DELEGATION["grant_action"], DELEGATION["revoke_action"]):
        spec = ACTIONS[action]
        assert action in models.ACTION_PARAMS and models.ACTION_TARGETS[action] == frozenset()
        assert spec["class"] == "record" and spec["authorization"] == "scope" and spec["target_types"] == []
        assert spec["agent_face"] is False and spec["delegable"] is None
        assert action not in REGISTRY["agent_face"]["writes"]


def test_the_grant_takes_exactly_the_registered_fields_and_the_families_of_the_first_version():
    assert list(models.WorldV02GrantDelegationParams.model_fields) == DELEGATION["grant_fields"]
    assert models.DELEGATION_FAMILIES == tuple(family["id"] for family in DELEGATION["families"])
    assert "create" not in models.DELEGATION_FAMILIES  # 建对象不在本版（待决 6，登记 pending_families）
    assert {family["id"] for family in DELEGATION["pending_families"]} == {"create"}


def test_the_issue_family_holds_owning_disposing_and_returning_but_not_raising_or_routing():
    """补 49（#71）：议题族排在已有三族之后，是承接、处置与退回形成；提出与路由在 Agent 面上，不进任何族。"""
    assert [family["id"] for family in DELEGATION["families"]] == ["gate", "assign", "lifecycle", "issue"]
    issue = next(family for family in DELEGATION["families"] if family["id"] == "issue")
    assert issue["display_name"] == "议题"
    assert issue["actions"] == ["world_dispose_issue", "world_own_issue", "world_return_issue"]
    listed = {action for family in DELEGATION["families"] for action in family["actions"]}
    assert not {"world_raise_issue", "world_route_issue"} & listed


def test_writing_on_behalf_takes_exactly_the_registered_fields():
    assert list(models.OnBehalfOf.model_fields) == DELEGATION["write_fields"]["on_behalf_of"]


@pytest.mark.parametrize("action", sorted(models.ACTION_PARAMS))
def test_an_implemented_action_takes_on_behalf_of_exactly_when_the_registry_lists_it_in_a_family(action):
    delegable = ACTIONS[action]["delegable"]
    assert ("on_behalf_of" in models.ACTION_PARAMS[action].model_fields) is (delegable is not None)
    if delegable is not None:
        assert action in next(family["actions"] for family in DELEGATION["families"] if family["id"] == delegable)


# ------------------------------------------------------------------ 登记委托的参数
def test_a_grant_names_the_delegate_families_domains_and_a_valid_until_in_utc():
    value = models.WorldV02GrantDelegationParams.model_validate(GRANT).model_dump(mode="json")
    assert value == {**GRANT, "valid_until": "2026-10-31T15:59:59Z"}


@pytest.mark.parametrize("change", [
    {"valid_until": None},                               # 有效期必填
    {"valid_until": "2026-10-31T23:59:59"},              # 带时区
    {"families": []},                                    # 至少一族
    {"families": ["create"]},                            # 建对象待决，不在第一版
    {"families": ["issues"]},                            # 不是登记的族
    {"families": ["gate", "gate"]},                      # 族不重复
    {"domain_ids": []},                                  # 至少一个域
    {"domain_ids": [DID, DID]},                          # 域不重复
    {"domain_ids": ["company"]},
    {"delegate_principal_id": "tianshu"},
    {"delegate_principal_id": None},
    {"on_behalf_of": ON_BEHALF},                         # 登记委托本身不能代记（不可转委托）
    {"scope": "all"},
])
def test_a_grant_outside_its_parameters_is_refused(change):
    params = {key: value for key, value in {**GRANT, **change}.items() if value is not None}
    with pytest.raises(ValueError):
        models.WorldV02GrantDelegationParams.model_validate(params)


def test_a_revocation_references_the_grant_event_and_nothing_else():
    value = models.WorldV02RevokeDelegationParams.model_validate({"delegation_event_id": EID}).model_dump(mode="json")
    assert value == {"delegation_event_id": EID}
    for bad in ({}, {"delegation_event_id": f"event:{EID}"}, {"delegation_event_id": EID, "on_behalf_of": ON_BEHALF},
                {"delegation_event_id": EID, "families": ["gate"]}):
        with pytest.raises(ValueError):
            models.WorldV02RevokeDelegationParams.model_validate(bad)


# ------------------------------------------------------------------ 代记写入的参数
DELEGABLE = sorted(action for action in models.ACTION_PARAMS if ACTIONS[action]["delegable"])


ISSUE_REF = f"{OID}@1#issues/iss-1"


def _minimal(action):
    """每个可代记动作最小的合法参数。"""
    if action == "world_assign":
        return {"principal_id": PID}
    if action == "world_assign_strategy_round":
        return {"principal_ids": [PID]}
    if ACTIONS[action]["event_kind"] == "confirm":
        return {"outcome": "accepted"}
    if action == "world_dispose_issue":
        return {"issue_ref": ISSUE_REF, "disposition": "current_layer_action", "content": {"text": "本层处理"}}
    if ACTIONS[action]["delegable"] == "issue":
        return {"issue_ref": ISSUE_REF}
    return {}


@pytest.mark.parametrize("action", DELEGABLE)
def test_a_delegable_action_takes_on_behalf_of_with_the_confirmation_time_in_utc(action):
    value = models.ACTION_PARAMS[action].model_validate({**_minimal(action), "on_behalf_of": ON_BEHALF})
    assert value.model_dump(mode="json", exclude_none=True)["on_behalf_of"] == {
        **ON_BEHALF, "external_confirmed_at": "2026-10-12T07:30:00Z"}


@pytest.mark.parametrize("action", DELEGABLE)
@pytest.mark.parametrize("change", [
    {"external_record_id": "   "},                       # 外部记录 id 不能只有空白
    {"external_record_id": "x" * 257},
    {"external_confirmed_at": "2026-10-12T15:30:00"},    # 带时区
    {"principal_id": "ceo"},
    {"note": "x"},
])
def test_on_behalf_of_outside_its_fields_is_refused(action, change):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate({**_minimal(action), "on_behalf_of": {**ON_BEHALF, **change}})


@pytest.mark.parametrize("action", DELEGABLE)
@pytest.mark.parametrize("missing", sorted(ON_BEHALF))
def test_on_behalf_of_names_the_person_the_external_record_and_its_confirmation_time(action, missing):
    with pytest.raises(ValueError):
        models.ACTION_PARAMS[action].model_validate(
            {**_minimal(action), "on_behalf_of": {k: v for k, v in ON_BEHALF.items() if k != missing}})


@pytest.mark.parametrize("model, params", [
    (models.WorldV02LifecycleParams, {}),
    (models.WorldV02ReturnIssueParams, {"issue_ref": ISSUE_REF}),   # 退回形成在 Agent 面上，也带声明（补 49）
])
def test_a_write_on_behalf_carries_no_declaration(model, params):
    declaration = {"scene": f"{OID}@1", "trigger": "天枢确认", "human_acceptance": {"required": False}}
    model.model_validate({**params, "declaration": declaration})
    with pytest.raises(ValueError, match="no declaration"):
        model.model_validate({**params, "declaration": declaration, "on_behalf_of": ON_BEHALF})


def test_a_withdrawal_can_be_written_on_behalf_too():
    params = {"outcome": "withdrawn", "supersedes_event_id": EID, "on_behalf_of": ON_BEHALF}
    assert set(models.WorldV02LifecycleParams.model_validate(params).model_dump(exclude_none=True)) == set(params)


@pytest.mark.parametrize("action", ["world_revise_object", "world_record_event", "world_refresh_state",
                                    "world_relate", "world_create_object",
                                    "world_raise_issue", "world_route_issue"])   # 提出与路由不进议题族（补 49）
def test_an_action_outside_the_delegable_families_does_not_take_on_behalf_of(action):
    assert "on_behalf_of" not in models.ACTION_PARAMS[action].model_fields


# ------------------------------------------------------------------ 请求信封
def _request(action, params, contract="tkos.world/0.2", target=False):
    body = {"action_type": action, "idempotency_key": "world-v02-delegation-0001", "reason": "Synthetic delegation",
            "contract_version": contract, "params": params, "expected_versions": [], "target": None}
    if target:
        body["target"] = {"object_id": OID, "revision_id": RID, "expected_version": 1}
    return body


@pytest.mark.parametrize("action, params, model", [
    ("world_grant_delegation", GRANT, "WorldV02GrantDelegationParams"),
    ("world_revoke_delegation", {"delegation_event_id": EID}, "WorldV02RevokeDelegationParams"),
])
def test_granting_and_revoking_parse_under_0_2_without_a_target(action, params, model):
    from memory_service_runtime.governed.models import ActionRequest
    assert type(ActionRequest.model_validate(_request(action, params)).params) is getattr(models, model)
    with pytest.raises(ValueError):
        ActionRequest.model_validate(_request(action, params, target=True))
    with pytest.raises(ValueError):  # 0.1 没有代记
        ActionRequest.model_validate(_request(action, params, contract="tkos.world/0.1"))


def test_a_command_without_on_behalf_of_hashes_as_before():
    """on_behalf_of 缺省为空、不进 exclude_none 的转储，已有命令的请求哈希不变。"""
    from memory_service_runtime.governed.models import ActionRequest
    request = ActionRequest.model_validate(_request("world_deliver", {}, target=True))
    assert request.model_dump(mode="json", exclude_none=True)["params"] == {}
    request = ActionRequest.model_validate(_request("world_assign", {"principal_id": PID}, target=True))
    assert request.model_dump(mode="json", exclude_none=True)["params"] == {"principal_id": PID}


# ------------------------------------------------------------------ 议题族的代记判定（票 #71，补 49；假连接）
# 换掉读库的几处（身份与指派、对象行、委托、Issue 事件、引用钉定、协议闸门），其余走服务里真实的判权路径：
# authorize_on_behalf → 按被代记的人 authorize_issue → issue_recorders → 生命周期引擎 admit。
PERSON = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"      # 被代记的人：路由指定的承接人，在另一单元（补 44）
OTHER = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e07"
PRIMARY = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e08"     # 主受影响对象；问题组件在快照 OID 的 issues 块里
DOMAIN_A, DOMAIN_B = DID, "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e09"  # A：快照与主受影响对象所在的域；B：承接人的单元
ISSUE_GRANT = {"event_id": "grant-issue", "grantor": PERSON, "delegate": PID, "families": ["issue"],
               "domain_ids": [DOMAIN_A]}
ISSUE_STATUS = {"world_own_issue": "owned", "world_dispose_issue": "disposed", "world_return_issue": "forming"}


def _issue_event(event_id, action, principal_id, detail=None, on_behalf_of=None):
    return {"event_id": event_id, "action": action, "outcome": None, "disposition": None, "supersedes_event_id": None,
            "principal_id": principal_id, "on_behalf_of": on_behalf_of, "detail": detail, "component_id": "iss-1"}


def _judging(monkeypatch, action, *, grants=(ISSUE_GRANT,), route_to=PERSON, caller=PID, caller_type="agent",
             on_behalf=True):
    """一条议题族动作的执行对象与委托查询的记录。问题已由 Agent 提出、路由给 route_to；处置与退回时 route_to 已承接——
    那条承接由服务主体代他记，承接人按被代记的人算。"""
    from memory_service_runtime.governed import db, protocol, world_v02
    from memory_service_runtime.governed.models import ActionRequest

    assignments = {PID: [{"assignment_id": "as-agent", "domain_id": DOMAIN_A, "role": "AGENT"}],
                   PERSON: [{"assignment_id": "as-person", "domain_id": DOMAIN_B, "role": "DOMAIN_DRI"}],
                   OTHER: [{"assignment_id": "as-other", "domain_id": DOMAIN_A, "role": "IC"}]}
    events = [_issue_event("e-raised", "world_raise_issue", PID),
              _issue_event("e-routed", "world_route_issue", PID, {"to_principal_id": route_to})]
    if action != "world_own_issue":
        events.append(_issue_event("e-owned", "world_own_issue", PID, on_behalf_of=route_to))
    asked = []

    def in_force(conn, ctx, *, grantor, delegate, family, domain_id):
        """同 delegations_in_force 的筛选：委托人、受托人、族与域。"""
        asked.append({"grantor": grantor, "delegate": delegate, "family": family, "domain_id": domain_id})
        return [grant for grant in grants if (grant["grantor"], grant["delegate"]) == (grantor, delegate)
                and family in grant["families"] and domain_id in grant["domain_ids"]]

    def pin(self, text):
        ref = models.parse_ref(text)
        self.referenced[text] = {"object_id": ref["object_id"], "domain_id": DOMAIN_A,
                                 "object_type": "StateSnapshot" if ref["object_id"] == OID else "Mission",
                                 "component": {"id": ref["component"], "type": "issue"},
                                 "payload": {"subject_ref": {"object_id": PRIMARY}}}
        return {"object_id": ref["object_id"], "object_version": ref["object_version"], "revision_id": RID,
                "block": ref["block"], "component": ref["component"]}

    monkeypatch.setattr(db, "_assignments", lambda conn, ctx: assignments[ctx.principal_id])
    monkeypatch.setattr(world_v02, "head_and_binding", lambda conn, ctx, object_id: (
        {"object_id": object_id, "object_type": "StateSnapshot", "domain_id": DOMAIN_A}, {"protocol_id": "tkos.world"}))
    monkeypatch.setattr(world_v02, "delegations_in_force", in_force)
    monkeypatch.setattr(world_v02, "issue_events", lambda conn, ctx, primary_id, component_id: events)
    monkeypatch.setattr(protocol, "gate_world_action", lambda conn, scope_id, kind, version: version)
    monkeypatch.setattr(world_v02.WorldExecution, "not_in_future", lambda self, moment, message: None)
    monkeypatch.setattr(world_v02.WorldExecution, "pin", pin)
    monkeypatch.setattr(world_v02.WorldExecution, "current_version", lambda self, object_id: 1)
    monkeypatch.setattr(world_v02.WorldExecution, "responsible_up_the_spine",
                        lambda self, object_id, required=True: None)
    params = _minimal(action)
    if on_behalf:
        params["on_behalf_of"] = {**ON_BEHALF, "principal_id": PERSON}
    ctx = db.AuthContext(scope_id="scope", tenant_id="tenant", company_id="company", principal_id=caller,
                         principal_type=caller_type, auth_epoch=1, assignments=[])
    return world_v02.WorldExecution(None, ctx, ActionRequest.model_validate(_request(action, params))), asked


def _refused(execution):
    from memory_service_runtime.governed.errors import GovernedError
    with pytest.raises(GovernedError) as refused:
        execution.authorize()
    return refused.value


@pytest.mark.parametrize("action", sorted(ISSUE_STATUS))
def test_the_service_principal_owns_disposes_and_returns_an_issue_judged_as_the_person(monkeypatch, action):
    """委托有效、族是议题、域覆盖问题所在的域（快照的域），按被代记的人判：承接人在另一单元也放行（按 scope），
    用到的是他的指派，最终复核同样先核委托再按他复核；判完换回调用者。"""
    execution, asked = _judging(monkeypatch, action)
    execution.authorize()
    wanted = {"grantor": PERSON, "delegate": PID, "family": "issue", "domain_id": DOMAIN_A}
    assert asked == [wanted] and execution.delegation is ISSUE_GRANT
    assert execution.effect["status"] == ISSUE_STATUS[action]
    assert execution.required_assignments == {"as-person"} and execution.domain_id == DOMAIN_A
    assert (execution.ctx.principal_id, execution.ctx.principal_type) == (PID, "agent")
    execution.recheck_final_barrier()
    assert asked == [wanted, wanted]


@pytest.mark.parametrize("action", sorted(ISSUE_STATUS))
def test_a_delegation_without_the_issue_family_does_not_cover_an_issue_action(monkeypatch, action):
    grant = {**ISSUE_GRANT, "families": ["gate", "assign", "lifecycle"]}
    execution, asked = _judging(monkeypatch, action, grants=(grant,))
    refused = _refused(execution)
    assert (refused.code, asked[0]["family"]) == ("FORBIDDEN", "issue")
    assert "No delegation in force" in refused.message and not hasattr(execution, "effect")


def test_a_delegation_that_covers_only_the_persons_own_unit_does_not_cover_an_issue_elsewhere(monkeypatch):
    """委托的域要覆盖问题所在的域（主受影响对象所在的域），不是承接人自己的单元。"""
    execution, _ = _judging(monkeypatch, "world_own_issue", grants=({**ISSUE_GRANT, "domain_ids": [DOMAIN_B]},))
    refused = _refused(execution)
    assert refused.code == "FORBIDDEN" and "No delegation in force" in refused.message


def test_an_issue_is_not_owned_on_behalf_of_a_person_who_is_not_its_route_target(monkeypatch):
    execution, _ = _judging(monkeypatch, "world_own_issue", route_to=OTHER)
    refused = _refused(execution)
    assert refused.code == "FORBIDDEN" and "route_target" in refused.message


@pytest.mark.parametrize("action", ["world_own_issue", "world_dispose_issue"])
def test_an_agent_still_does_not_own_or_dispose_an_issue_in_its_own_name(monkeypatch, action):
    execution, asked = _judging(monkeypatch, action, route_to=PID, on_behalf=False)
    refused = _refused(execution)
    assert refused.code == "FORBIDDEN" and "by a person" in refused.message and asked == []


@pytest.mark.parametrize("action", sorted(ISSUE_STATUS))
def test_the_person_still_records_it_in_person(monkeypatch, action):
    execution, asked = _judging(monkeypatch, action, caller=PERSON, caller_type="human", on_behalf=False)
    execution.authorize()
    assert execution.effect["status"] == ISSUE_STATUS[action] and execution.delegation is None and asked == []
    assert execution.required_assignments == {"as-person"}
