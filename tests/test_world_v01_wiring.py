"""tkos.world/0.1 的内核接线：迁移钉定、协议分支、执行派发、请求信封与取对象投影（不连数据库）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from memory_service_runtime.governed import world_v01_profile as profile

ROOT = Path(__file__).resolve().parents[1]
# 契约或登记每改一次就追加一个重钉迁移；当前的绑定门以最新那个为准。
MIGRATION = ROOT / "src/memory_service_app/migrations/0031_world_v01_contract_repin.sql"


def test_world_migration_pins_the_same_contract_and_registry_bytes_as_the_profile():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "NEW.protocol_id = 'tkos.world' AND NEW.contract_version = 'tkos.world/0.1'" in sql
    assert profile.CONTRACT_SHA256 in sql and profile.REGISTRY_SHA256 in sql
    assert f"prow.content->'world_registry_ref'->>'revision' = '{profile.REGISTRY_REVISION}'" in sql
    assert f"prow.schema_version = '{profile.SCHEMA_VERSION}'" in sql


def test_the_runtime_loads_a_bundled_registry_byte_identical_to_the_contract_copy():
    from memory_service_runtime.governed import world_v01_registry
    assert world_v01_registry.registry_bytes() == (ROOT / "docs/contracts/world-registry-0.1.json").read_bytes()
    assert world_v01_registry.registry()["revision"] == profile.REGISTRY_REVISION


def test_a_bundled_registry_that_does_not_match_the_pin_fails_closed(monkeypatch):
    from memory_service_runtime.governed import world_v01_registry
    monkeypatch.setattr(world_v01_registry.profile, "REGISTRY_SHA256", "0" * 64)
    world_v01_registry.registry.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="hash mismatch"):
            world_v01_registry.registry()
    finally:
        world_v01_registry.registry.cache_clear()


def test_a_company_payload_keeps_every_block_and_stores_empty_blocks_as_null():
    from memory_service_runtime.governed import world_v01_models as models
    assert models.validate_input("Company", {"title": "E&O 公司"}) == {
        "title": "E&O 公司", "blocks": {"identity": None, "constraint": None}}
    identity = {"text": "一家做企业经营系统的公司。", "artifacts": ["https://example.test/brief"]}
    assert models.validate_input("Company", {"title": "E&O 公司", "blocks": {"identity": identity}}) == {
        "title": "E&O 公司",
        "blocks": {"identity": {"text": "一家做企业经营系统的公司。", "refs": [],
                                "artifacts": ["https://example.test/brief"]},
                   "constraint": None}}


@pytest.mark.parametrize("payload", [
    {},                                                                   # 标题必填
    {"title": "   "},                                                     # 标题不能只有空白
    {"title": "E&O", "owner": "x"},                                       # 多余字段
    {"title": "E&O", "blocks": {"choices": {"text": "x"}}},               # 不是 Company 的块
    {"title": "E&O", "blocks": {"identity": {"text": "x", "note": "y"}}},  # 块只有三件套
    {"title": "E&O", "blocks": {"identity": {"text": "   "}}},             # 空内容不能冒充非空块
    {"title": "E&O", "blocks": {"identity": {"text": "x", "artifacts": ["ftp://example.test/a"]}}},
])
def test_a_company_payload_that_breaks_the_contract_is_refused(payload):
    from memory_service_runtime.governed import world_v01_models as models
    with pytest.raises(ValueError):
        models.validate_input("Company", payload)


def _create_company(**overrides):
    body = {"action_type": "world_create_object", "target": None, "expected_versions": [],
            "idempotency_key": "world-create-company-0001", "reason": "Synthetic world company root",
            "contract_version": "tkos.world/0.1",
            "params": {"domain_id": "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01", "object_type": "Company",
                       "payload": {"title": "E&O 公司"}}}
    body.update(overrides)
    return body


def test_a_world_create_request_parses_under_the_world_contract_only():
    from memory_service_runtime.governed.models import ActionRequest
    from memory_service_runtime.governed.world_v01_models import WorldCreateObjectParams
    request = ActionRequest.model_validate(_create_company())
    assert isinstance(request.params, WorldCreateObjectParams)
    assert request.params.object_type == "Company"


@pytest.mark.parametrize("overrides", [
    {"contract_version": None},
    {"contract_version": "tkos.method/0.5"},
    {"target": {"object_id": "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01", "revision_id": "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02",
                "expected_version": 1}},
    {"params": {"domain_id": "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01", "object_type": "Company",
                "payload": {"title": "E&O"}, "extra": 1}},
    {"params": {"domain_id": "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01", "object_type": "Constraint",
                "payload": {"title": "E&O"}}},
])
def test_a_world_create_request_outside_its_envelope_is_refused(overrides):
    from memory_service_runtime.governed.models import ActionRequest
    with pytest.raises(ValueError):
        ActionRequest.model_validate(_create_company(**overrides))


def test_world_actions_reach_the_world_executor_and_never_the_legacy_one():
    from types import SimpleNamespace
    from memory_service_runtime.governed import service, world_v01
    from memory_service_runtime.governed.models import ActionRequest
    request = ActionRequest.model_validate(_create_company())
    execution = service._execution_factory(None, SimpleNamespace(scope_id="s"), request)
    assert type(execution) is world_v01.WorldExecution


WORLD = ("tkos.world", "tkos.world/0.1")


def test_world_is_compiled_in_and_its_profile_belongs_to_it():
    from memory_service_runtime.governed import profile as core
    from memory_service_runtime.governed import protocol
    assert WORLD in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    assert core.implied_protocol(profile.SCHEMA_VERSION, profile.content()) == WORLD
    assert core.implied_protocol(profile.SCHEMA_VERSION, {**profile.content(), "display_name": "x"}) is None
    assert core.validate_profile_core(profile.content()).profile_id == profile.PROFILE_ID


def _world_binding_rows(can_read=True):
    content = profile.content()
    installed = {"schema_version": profile.SCHEMA_VERSION, "content": content,
                 "canonical_hash": content["canonical_hash"]}
    registry_row = {"contract_version": "tkos.world/0.1", "content": {
        "can_read": can_read, "can_create": True, "can_write": True, "evidence_upload": False,
        "actions": ["world_create_object"], "object_types": ["Company"],
        "readonly_compat": ["tkos.world/0.1"], "notes": "synthetic"}}
    binding = {"protocol_id": "tkos.world", "contract_version": "tkos.world/0.1",
               "profile_id": profile.PROFILE_ID, "profile_revision": profile.PROFILE_REVISION,
               "profile_canonical_hash": content["canonical_hash"]}
    return installed, registry_row, binding


def test_a_world_binding_is_interpreted_only_under_a_read_grant(monkeypatch):
    from memory_service_runtime.governed import protocol
    assert protocol._binding_interpretation(*_world_binding_rows())[0] == "world_v0_1"
    assert protocol._binding_interpretation(*_world_binding_rows(can_read=False))[0] == "read_unsupported"


def test_other_protocols_read_gate_never_interprets_a_world_object(monkeypatch):
    """共享的读支持闸门是 Method、A2、legacy 读取器的闸门；world 对象经它一律被拒。"""
    from memory_service_runtime.governed import protocol
    from memory_service_runtime.governed.errors import GovernedError
    monkeypatch.setattr(protocol, "read_metadata", lambda conn, scope, oid: {
        "registration_status": "registered", "interpretation_status": "world_v0_1"})
    with pytest.raises(GovernedError) as error:
        protocol.require_read_support(None, "s", "o")
    assert error.value.code == "PROTOCOL_NOT_SUPPORTED"


@pytest.mark.parametrize("declared, action, code", [
    ("tkos.method/0.5", "world_create_object", "PROTOCOL_BINDING_CONFLICT"),
    (None, "world_create_object", "PROTOCOL_BINDING_CONFLICT"),
    ("tkos.world/0.1", "world_create_object", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL"),
    ("tkos.world/0.1", "m1b_confirm_ltco", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL"),
])
def test_a_world_bound_target_refuses_foreign_declarations_and_untargeted_actions(declared, action, code, monkeypatch):
    from memory_service_runtime.governed import protocol
    from memory_service_runtime.governed.errors import GovernedError
    installed, registry_row, binding = _world_binding_rows()
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


def test_the_object_view_renders_every_block_with_its_citation_and_empty_ones_as_the_standard_sentence():
    from memory_service_runtime.governed import world_v01_readers as readers
    oid, rid = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01", "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
    head = {"object_id": oid, "object_type": "Company", "object_version": 1, "lifecycle_status": "recorded",
            "latest_revision_id": rid, "effective_revision_id": rid, "domain_id": "d"}
    revision = {"revision_id": rid, "object_version": 1, "payload": {
        "title": "E&O 公司", "blocks": {"identity": {"text": "做企业经营系统。", "refs": [], "artifacts": []},
                                       "constraint": None}}}
    view = readers.object_view(head, revision, {"interpretation_status": "world_v0_1"})
    assert (view["object_type"], view["type_display_name"], view["version"], view["title"]) == (
        "Company", "公司", 1, "E&O 公司")
    assert [(b["id"], b["display_name"], b["empty"], b["text"], b["ref"]) for b in view["blocks"]] == [
        ("identity", "身份", False, "做企业经营系统。", f"{oid}@1#identity"),
        ("constraint", "约束", True, "当前没有约束", f"{oid}@1#constraint"),
    ]
    assert view["attributes"] == {} and view["relations"] == []
    assert view["protocol"]["interpretation_status"] == "world_v0_1"
