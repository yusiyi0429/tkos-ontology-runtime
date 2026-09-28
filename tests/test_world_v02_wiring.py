"""tkos.world/0.2 的内核接线：钉定、迁移 0039、协议分支、执行派发、请求信封与三层读投影（不连数据库）。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from memory_service_runtime.governed import world_v02_profile as profile

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/contracts/tkos-world-0.2.md"
REGISTRY = ROOT / "docs/contracts/world-registry-0.2.json"
PROFILE = ROOT / "docs/contracts/world-profile-0.2.json"
SUPPORT = ROOT / "docs/runtime-world-support-0.2.json"
MIGRATION = ROOT / "src/memory_service_app/migrations/0039_world_v02.sql"
V01_REPIN = ROOT / "src/memory_service_app/migrations/0035_world_v01_gates_repin.sql"
WORLD_V02 = ("tkos.world", "tkos.world/0.2")
DOMAIN = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"


# ------------------------------------------------------------------ pins
def test_the_profile_pins_the_current_contract_and_registry_bytes():
    """改了契约或登记没重钉，这条先失败：重钉 world_v02_profile、profile JSON 与迁移 0039（ADR-0009）。"""
    assert hashlib.sha256(CONTRACT.read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    assert hashlib.sha256(REGISTRY.read_bytes()).hexdigest() == profile.REGISTRY_SHA256
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert (registry["contract_version"], registry["revision"]) == (profile.CONTRACT_VERSION, profile.REGISTRY_REVISION)
    assert json.loads(PROFILE.read_text(encoding="utf-8")) == profile.content()
    assert profile.validate(profile.content()).revision == "0.2.0"


def test_the_runtime_loads_a_bundled_registry_byte_identical_to_the_contract_copy():
    from memory_service_runtime.governed import world_v02_registry
    assert world_v02_registry.registry_bytes() == REGISTRY.read_bytes()
    assert world_v02_registry.registry()["revision"] == profile.REGISTRY_REVISION


def test_a_bundled_registry_that_does_not_match_the_pin_fails_closed(monkeypatch):
    from memory_service_runtime.governed import world_v02_registry
    monkeypatch.setattr(world_v02_registry.profile, "REGISTRY_SHA256", "0" * 64)
    world_v02_registry.registry.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="hash mismatch"):
            world_v02_registry.registry()
    finally:
        world_v02_registry.registry.cache_clear()


def test_migration_0039_pins_the_same_bytes_as_the_profile_and_keeps_world_0_1_as_0035_pinned_it():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "NEW.protocol_id = 'tkos.world' AND NEW.contract_version = 'tkos.world/0.2'" in sql
    assert profile.CONTRACT_SHA256 in sql and profile.REGISTRY_SHA256 in sql
    assert f"prow.schema_version = '{profile.SCHEMA_VERSION}'" in sql
    assert f"prow.content->'world_registry_ref'->>'revision' = '{profile.REGISTRY_REVISION}'" in sql

    def branch(text, version):
        start = text.index(f"NEW.contract_version = '{version}' THEN")
        return text[start:text.index("END IF;", start)]
    assert branch(sql, "tkos.world/0.1") == branch(V01_REPIN.read_text(encoding="utf-8"), "tkos.world/0.1")


def test_migration_0039_allows_every_registered_event_kind_and_value():
    sql = MIGRATION.read_text(encoding="utf-8")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    kinds = re.search(r"ck_gov_world_event_kind CHECK \(kind IN \((.*?)\)\)", sql, re.S)[1]
    assert set(re.findall(r"'([^']+)'", kinds)) == {item["kind"] for item in registry["event_kinds"]}
    for field in ("category", "outcome", "disposition"):
        allowed = re.search(rf"ck_gov_world_event_{field}\s+CHECK \({field} IN \((.*?)\)\)", sql, re.S)[1]
        assert re.findall(r"'([^']+)'", allowed) == [v["id"] for v in registry["event_attribute_values"][field]]
    delegable = {a["event_kind"] for a in registry["actions"] if a["delegable"]}
    on_behalf = re.search(r"on_behalf_of IS NULL OR kind IN \((.*?)\)\)", sql, re.S)[1]
    assert set(re.findall(r"'([^']+)'", on_behalf)) == delegable


def test_the_support_registry_lists_only_what_is_implemented():
    support = json.loads(SUPPORT.read_text(encoding="utf-8"))
    from memory_service_runtime.governed import world_v02_models as models
    assert support["actions"] == sorted(models.ACTION_PARAMS)
    assert support["object_types"] == sorted(models.CREATABLE)
    assert support["readonly_compat"] == ["tkos.world/0.2"] and support["evidence_upload"] is False


def test_the_image_ships_the_world_0_2_installation_material():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for name in ("docs/contracts/world-profile-0.2.json", "docs/contracts/tkos-world-0.2.md",
                 "docs/contracts/world-registry-0.2.json", "docs/runtime-world-support-0.2.json"):
        assert name in dockerfile


# ------------------------------------------------------------------ protocol
def test_world_0_2_is_compiled_in_beside_0_1_and_its_profile_belongs_to_it():
    from memory_service_runtime.governed import profile as core
    from memory_service_runtime.governed import protocol
    assert WORLD_V02 in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    assert ("tkos.world", "tkos.world/0.1") in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    assert core.implied_protocol(profile.SCHEMA_VERSION, profile.content()) == WORLD_V02
    assert core.implied_protocol(profile.SCHEMA_VERSION, {**profile.content(), "display_name": "x"}) is None
    assert core.validate_profile_core(profile.content()).profile_id == profile.PROFILE_ID


def _binding_rows(contract="tkos.world/0.2"):
    from memory_service_runtime.governed import world_v01_profile
    module = profile if contract == "tkos.world/0.2" else world_v01_profile
    content = module.content()
    installed = {"schema_version": module.SCHEMA_VERSION, "content": content, "canonical_hash": content["canonical_hash"]}
    registry_row = {"contract_version": contract, "content": {
        "can_read": True, "can_create": True, "can_write": True, "evidence_upload": False,
        "actions": ["world_create_object"], "object_types": ["Company"], "readonly_compat": [contract], "notes": "x"}}
    binding = {"protocol_id": "tkos.world", "contract_version": contract, "profile_id": module.PROFILE_ID,
               "profile_revision": module.PROFILE_REVISION, "profile_canonical_hash": content["canonical_hash"]}
    return installed, registry_row, binding


def test_each_world_binding_is_interpreted_under_its_own_version():
    from memory_service_runtime.governed import protocol
    assert protocol._binding_interpretation(*_binding_rows("tkos.world/0.2"))[0] == "world_v0_2"
    assert protocol._binding_interpretation(*_binding_rows("tkos.world/0.1"))[0] == "world_v0_1"


@pytest.mark.parametrize("declared, action, code", [
    ("tkos.world/0.1", "world_relate", "PROTOCOL_BINDING_CONFLICT"),
    ("tkos.world/0.2", "world_relate", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL"),  # Company 不能建关系
])
def test_a_world_0_2_bound_target_takes_its_targets_from_the_0_2_table(declared, action, code, monkeypatch):
    from memory_service_runtime.governed import protocol
    from memory_service_runtime.governed.errors import GovernedError
    _, registry_row, binding = _binding_rows()
    registry_row["content"]["actions"].append("world_relate")
    monkeypatch.setattr(protocol, "current_binding", lambda conn, scope, oid: binding)
    monkeypatch.setattr(protocol, "_check_registry",
                        lambda conn, scope, pid, cv: protocol.RegistryContent.model_validate(registry_row["content"]))
    monkeypatch.setattr(protocol, "_check_binding_profile", lambda conn, scope, b: None)

    class Conn:
        def execute(self, sql, params=None):
            return type("Cursor", (), {"fetchone": lambda self: {"object_type": "Company"}})()

    with pytest.raises(GovernedError) as error:
        protocol.gate_target_action(Conn(), "s", "o", action, declared)
    assert error.value.code == code


# ------------------------------------------------------------------ payloads
def test_a_company_payload_takes_the_0_2_block_value_and_stores_empty_blocks_as_null():
    from memory_service_runtime.governed import world_v02_models as models
    assert models.validate_input("Company", {"title": "E&O 公司"}) == {
        "title": "E&O 公司", "external_refs": [], "blocks": {"identity": None, "constraint": None}}
    identity = {"text": "一家做企业经营系统的公司。", "artifacts": ["https://example.test/brief"]}
    refs = [{"system": "tianshu", "id": "company-1", "url": "https://example.test/c/1"}, {"system": "crm", "id": "7"}]
    assert models.validate_input("Company", {"title": "E&O 公司", "external_refs": refs,
                                             "blocks": {"identity": identity}}) == {
        "title": "E&O 公司", "external_refs": [refs[0], {**refs[1], "url": None}],
        "blocks": {"identity": {"text": "一家做企业经营系统的公司。", "components": [], "refs": [],
                                "artifacts": ["https://example.test/brief"]},
                   "constraint": None}}


@pytest.mark.parametrize("payload", [
    {},                                                                    # 标题必填
    {"title": "   "},                                                      # 标题不能只有空白
    {"title": "E&O", "owner": "x"},                                        # 多余字段
    {"title": "E&O", "blocks": {"choices": {"text": "x"}}},                # 不是 Company 的块
    {"title": "E&O", "blocks": {"identity": {"text": "x", "note": "y"}}},  # 块值只有四个字段
    {"title": "E&O", "blocks": {"identity": {"text": "   "}}},              # 空内容不能冒充非空块
    {"title": "E&O", "blocks": {"identity": {"text": "x", "artifacts": ["ftp://example.test/a"]}}},
    {"title": "E&O", "blocks": {"identity": {"text": "x", "components": [{"type": "outcome", "text": "y"}]}}},  # 身份块不带组件
    {"title": "E&O", "blocks": {"identity": {"text": "x", "refs": ["0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01@1#Bad"]}}},
    {"title": "E&O", "external_refs": [{"system": "tianshu"}]},             # 外部引用缺 id
    {"title": "E&O", "external_refs": [{"system": " ", "id": "1"}]},        # system 不能只有空白
    {"title": "E&O", "external_refs": [{"system": "t", "id": "1", "url": "ftp://x"}]},
    {"title": "E&O", "external_refs": [{"system": "t", "id": "1", "note": "x"}]},
])
def test_a_company_payload_that_breaks_the_0_2_contract_is_refused(payload):
    from memory_service_runtime.governed import world_v02_models as models
    with pytest.raises(ValueError):
        models.validate_input("Company", payload)


# ------------------------------------------------------------------ envelope and dispatch
def _create_company(contract="tkos.world/0.2", **overrides):
    body = {"action_type": "world_create_object", "target": None, "expected_versions": [],
            "idempotency_key": "world-v02-create-company-0001", "reason": "Synthetic world company root",
            "contract_version": contract,
            "params": {"domain_id": DOMAIN, "object_type": "Company", "payload": {"title": "E&O 公司"}}}
    body.update(overrides)
    return body


def test_the_same_action_name_parses_under_the_version_it_declares():
    from memory_service_runtime.governed.models import ActionRequest
    from memory_service_runtime.governed.world_v01_models import WorldCreateObjectParams as V01
    from memory_service_runtime.governed.world_v02_models import WorldV02CreateObjectParams as V02
    assert type(ActionRequest.model_validate(_create_company()).params) is V02
    assert type(ActionRequest.model_validate(_create_company("tkos.world/0.1")).params) is V01


@pytest.mark.parametrize("overrides", [
    {"action_type": "world_grant_delegation",                    # 0.2 还没有实现代记
     "params": {"delegate_principal_id": DOMAIN}},
    {"action_type": "world_revise_object", "params": {"payload": {"title": "x"}}},  # 修订要带目标
    {"target": {"object_id": DOMAIN, "revision_id": DOMAIN, "expected_version": 1}},
    {"params": {"domain_id": DOMAIN, "object_type": "Company", "payload": {"title": "x"}, "extra": 1}},
    {"params": {"domain_id": DOMAIN, "object_type": "Constraint", "payload": {"title": "x"}}},
    {"params": {"domain_id": DOMAIN, "object_type": "Company", "payload": {"title": "x"},  # 场景只用对象形式
                "declaration": {"scene": DOMAIN + "@1#identity", "trigger": "x", "human_acceptance": {"required": False}}}},
])
def test_a_world_0_2_request_outside_its_envelope_is_refused(overrides):
    from memory_service_runtime.governed.models import ActionRequest
    with pytest.raises(ValueError):
        ActionRequest.model_validate(_create_company(**overrides))


def test_each_version_reaches_its_own_executor():
    from memory_service_runtime.governed import service, world_v01, world_v02
    from memory_service_runtime.governed.models import ActionRequest
    ctx = SimpleNamespace(scope_id="s")
    v02 = service._execution_factory(None, ctx, ActionRequest.model_validate(_create_company()))
    v01 = service._execution_factory(None, ctx, ActionRequest.model_validate(_create_company("tkos.world/0.1")))
    assert type(v02) is world_v02.WorldExecution and type(v01) is world_v01.WorldExecution


def test_world_0_2_receipts_are_told_apart_from_0_1_receipts_by_their_contract():
    from memory_service_runtime.governed import world_v02_readers
    row = {"action_type": "world_create_object", "result": {"contract_version": "tkos.world/0.2"}}
    assert world_v02_readers.is_receipt(row)
    assert not world_v02_readers.is_receipt({**row, "result": {}})


# ------------------------------------------------------------------ read projection
def test_the_company_view_is_grouped_into_business_identity_and_records():
    from memory_service_runtime.governed import world_v02_readers as readers
    oid, rid = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01", "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
    head = {"object_id": oid, "object_type": "Company", "object_version": 1, "lifecycle_status": "recorded",
            "latest_revision_id": rid, "effective_revision_id": rid, "domain_id": "d"}
    revision = {"revision_id": rid, "object_version": 1, "payload": {
        "title": "E&O 公司", "external_refs": [{"system": "tianshu", "id": "c-1", "url": None}],
        "blocks": {"identity": {"text": "做企业经营系统。", "components": [], "refs": [], "artifacts": []},
                   "constraint": None}}}
    ceo = [{"principal_id": "p", "principal_type": "human", "display_name": "CEO"}]
    view = readers.object_view(head, revision, {"interpretation_status": "world_v0_2"}, responsible=ceo)
    assert set(view) == {"object_id", "business", "identity", "records", "protocol"}
    business = view["business"]
    assert (business["object_type"], business["type_display_name"], business["category"], business["candidate"],
            business["version"], business["revision_id"], business["title"]) == (
        "Company", "公司", {"id": "business_object", "display_name": "业务对象"}, False, 1, rid, "E&O 公司")
    assert business["attributes"] == {"external_refs": [{"system": "tianshu", "id": "c-1", "url": None}]}
    assert [(b["id"], b["class"], b["empty"], b["text"], b["components"], b["ref"]) for b in business["blocks"]] == [
        ("identity", "formal", False, "做企业经营系统。", [], f"{oid}@1#identity"),
        ("constraint", "formal", True, "当前没有约束", [], f"{oid}@1#constraint"),
    ]
    assert business["relations"] == [] and business["component_ledger"] == [] and business["round"] is None
    assert business["formal"] == {"lifecycle_status": "recorded", "effective_revision_id": rid}
    assert view["identity"] == {"responsible": {"source": "role", "role": "CEO", "principals": ceo}, "delegations": []}
    assert view["records"] == {"lifecycle": None, "latest_state": None, "confirmed_review": None, "open_issues": []}
