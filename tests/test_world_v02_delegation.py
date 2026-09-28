"""tkos.world/0.2 的代记：委托的登记与撤销、代记写入的参数（票 #62，契约第 8.1、9、14 节；不连数据库）。

HTTP 路径（登记与撤销、按被代记的人判权的通过与各类拒绝、库快照不变、事件与回执、本人撤回、读投影 identity 的
当前有效委托、重放）在 acceptance/world_v02 的 delegation 场景里；这里只测请求模型与它们和登记的对齐。
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
    assert "create" not in models.DELEGATION_FAMILIES  # 建对象待决 6（登记 pending_families）
    assert {family["id"] for family in DELEGATION["pending_families"]} == {"create"}


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
    {"families": ["issue"]},
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


def _minimal(action):
    """每个可代记动作最小的合法参数。"""
    if action == "world_assign":
        return {"principal_id": PID}
    if ACTIONS[action]["event_kind"] == "confirm":
        return {"outcome": "accepted"}
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


def test_a_write_on_behalf_carries_no_declaration():
    declaration = {"scene": f"{OID}@1", "trigger": "天枢确认", "human_acceptance": {"required": False}}
    models.WorldV02LifecycleParams.model_validate({"declaration": declaration})
    with pytest.raises(ValueError, match="no declaration"):
        models.WorldV02LifecycleParams.model_validate({"declaration": declaration, "on_behalf_of": ON_BEHALF})


def test_a_withdrawal_can_be_written_on_behalf_too():
    params = {"outcome": "withdrawn", "supersedes_event_id": EID, "on_behalf_of": ON_BEHALF}
    assert set(models.WorldV02LifecycleParams.model_validate(params).model_dump(exclude_none=True)) == set(params)


@pytest.mark.parametrize("action", ["world_revise_object", "world_record_event", "world_refresh_state",
                                    "world_relate", "world_create_object"])
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
