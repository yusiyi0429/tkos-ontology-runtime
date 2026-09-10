"""Offline frozen error-matrix tests for the A1 protocol gates (no database).

A FakeConn replays canned registration rows; the real gate functions in
memory_service_runtime.governed.protocol make every decision, so the frozen
error matrix is exercised end to end without a PostgreSQL server.
"""
from __future__ import annotations

import copy

import pytest

from memory_service_runtime.governed import artifacts, canon, profile, protocol
from memory_service_runtime.governed.errors import GovernedError

SCOPE = "00000000-0000-0000-0000-000000000001"
DOMAIN = "00000000-0000-0000-0000-000000000002"
OID = "00000000-0000-0000-0000-000000000003"

_A_CORE = canon.load_json_strict(artifacts.profile_core_bytes().decode("utf-8"))
A_PROFILE_ID = _A_CORE["profile_id"]
A_PROFILE_REVISION = _A_CORE["revision"]
A_PROFILE_HASH = _A_CORE["canonical_hash"]

LEGACY_PAIR = (profile.LEGACY_PROTOCOL_ID, profile.LEGACY_CONTRACT_VERSION)
A_PAIR = (profile.CONTRACT_A_PROTOCOL_ID, profile.CONTRACT_A_CONTRACT_VERSION)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    """Routes the four registration queries of protocol.py to canned rows."""

    def __init__(self, *, bindings=None, profiles=None, registries=None,
                 domain_policies=None, scope_policy=None):
        self.bindings = bindings or {}
        self.profiles = profiles or {}
        self.registries = registries or {}
        self.domain_policies = domain_policies or {}
        self.scope_policy = scope_policy

    def execute(self, sql, params=()):
        if "gov_object_protocol_bindings" in sql:
            if "ANY(" in sql:
                return _Result([self.bindings[i] for i in map(str, params[1])
                                if i in self.bindings])
            row = self.bindings.get(str(params[1]))
            return _Result([row] if row else [])
        if "gov_method_profile_revisions" in sql:
            row = self.profiles.get((params[1], params[2]))
            return _Result([row] if row else [])
        if "gov_protocol_support_registry" in sql:
            row = self.registries.get(params[1])
            return _Result([row] if row else [])
        if "gov_protocol_policies" in sql:
            if "domain_id IS NULL" in sql:
                return _Result([self.scope_policy] if self.scope_policy else [])
            row = self.domain_policies.get(str(params[1]))
            return _Result([row] if row else [])
        raise AssertionError(f"unexpected SQL: {sql}")


def binding_row(protocol_id, contract_version, profile_id, profile_revision,
                profile_hash, record_origin="legacy"):
    return {
        "scope_id": SCOPE, "object_id": OID, "binding_version": 1,
        "protocol_id": protocol_id, "contract_version": contract_version,
        "profile_id": profile_id, "profile_revision": profile_revision,
        "profile_canonical_hash": profile_hash, "record_origin": record_origin,
    }


def legacy_binding():
    return binding_row(*LEGACY_PAIR, profile.LEGACY_PROFILE_ID,
                       profile.LEGACY_PROFILE_REVISION,
                       profile.LEGACY_PROFILE_CANONICAL_HASH)


def contract_a_binding():
    return binding_row(*A_PAIR, A_PROFILE_ID, A_PROFILE_REVISION, A_PROFILE_HASH,
                       record_origin="synthetic")


def profile_row(schema_version, content, canonical_hash, profile_id, revision):
    return {"schema_version": schema_version, "content": content,
            "canonical_hash": canonical_hash, "profile_id": profile_id,
            "revision": revision}


def legacy_profile_row():
    return profile_row(profile.LEGACY_PROFILE_SCHEMA_VERSION,
                       profile.LEGACY_PROFILE_CONTENT,
                       profile.LEGACY_PROFILE_CANONICAL_HASH,
                       profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION)


def contract_a_profile_row():
    return profile_row(profile.PROFILE_CORE_SCHEMA_VERSION, _A_CORE,
                       A_PROFILE_HASH, A_PROFILE_ID, A_PROFILE_REVISION)


def registry_row(contract_version, **overrides):
    content = copy.deepcopy(profile.LEGACY_REGISTRY_CONTENT)
    content.update(overrides)
    return {"contract_version": contract_version, "content": content}


def policy_row(**overrides):
    content = copy.deepcopy(profile.LEGACY_SCOPE_POLICY_CONTENT)
    content.update(overrides)
    return {"content": content}


def contract_a_policy_row():
    return {"content": profile.contract_a_policy_content(A_PROFILE_ID, A_PROFILE_REVISION)}


def legacy_env(**kwargs):
    """A fully registered legacy scope: binding + profile + registry."""
    env = {"bindings": {OID: legacy_binding()},
           "profiles": {(profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION):
                        legacy_profile_row()},
           "registries": {profile.LEGACY_PROTOCOL_ID:
                          registry_row(profile.LEGACY_CONTRACT_VERSION)}}
    env.update(kwargs)
    return FakeConn(**env)


def contract_a_env(**kwargs):
    env = {"bindings": {OID: contract_a_binding()},
           "profiles": {(A_PROFILE_ID, A_PROFILE_REVISION): contract_a_profile_row()},
           "registries": {profile.CONTRACT_A_PROTOCOL_ID:
                          {"contract_version": profile.CONTRACT_A_CONTRACT_VERSION,
                           "content": copy.deepcopy(profile.CONTRACT_A_REGISTRY_CONTENT)}}}
    env.update(kwargs)
    return FakeConn(**env)


def expect(code, fn, *args):
    with pytest.raises(GovernedError) as info:
        fn(*args)
    assert info.value.code == code
    assert info.value.status == 409


# ------------------------------------------------------------ frozen matrix


def test_all_protocol_error_codes_are_409():
    from memory_service_runtime.governed import errors
    for code in ("PROTOCOL_UPGRADE_REQUIRED", "PROTOCOL_NOT_SUPPORTED",
                 "METHOD_PROFILE_UNSUPPORTED", "ACTION_NOT_SUPPORTED_FOR_PROTOCOL",
                 "PROFILE_CONTENT_CONFLICT", "PROTOCOL_POLICY_MISSING",
                 "PROTOCOL_BINDING_MISSING", "PROTOCOL_BINDING_CONFLICT",
                 "PROTOCOL_WRITE_DISABLED"):
        assert errors._STATUS[code] == 409


def test_target_action_binding_missing():
    conn = FakeConn()
    expect("PROTOCOL_BINDING_MISSING",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)


def test_target_action_unknown_pair_rejected_even_if_registry_claims_writable():
    binding = binding_row("tkos.unknown", "tkos.unknown/9.9",
                          profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION,
                          profile.LEGACY_PROFILE_CANONICAL_HASH)
    conn = FakeConn(bindings={OID: binding},
                    profiles={(profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION):
                              legacy_profile_row()},
                    registries={"tkos.unknown": registry_row("tkos.unknown/9.9")})
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)


def test_target_action_registry_version_mismatch():
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID: registry_row("tkos.governed/v9.9")})
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)


def test_target_action_contract_a_declared_matrix():
    conn = contract_a_env()
    expect("PROTOCOL_UPGRADE_REQUIRED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)
    expect("PROTOCOL_UPGRADE_REQUIRED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision",
           profile.LEGACY_CONTRACT_VERSION)
    expect("ACTION_NOT_SUPPORTED_FOR_PROTOCOL",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision",
           profile.CONTRACT_A_CONTRACT_VERSION)
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", "tkos.other/9")


def test_target_action_legacy_declared_unknown_version():
    conn = legacy_env()
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", "tkos.other/9")


def test_target_action_legacy_write_disabled_and_action_withdrawn():
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row(profile.LEGACY_CONTRACT_VERSION,
                                               can_write=False)})
    expect("PROTOCOL_WRITE_DISABLED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row(profile.LEGACY_CONTRACT_VERSION,
                                               actions=["create_object"])})
    expect("ACTION_NOT_SUPPORTED_FOR_PROTOCOL",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)


def test_target_action_legacy_happy_path():
    conn = legacy_env()
    assert protocol.gate_target_action(conn, SCOPE, OID, "propose_revision", None) == (
        profile.LEGACY_CONTRACT_VERSION)
    assert protocol.gate_target_action(conn, SCOPE, OID, "propose_revision",
                                       profile.LEGACY_CONTRACT_VERSION) == (
        profile.LEGACY_CONTRACT_VERSION)


def test_target_action_profile_hash_mismatch_and_wrong_kind():
    conn = legacy_env(profiles={(profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION):
                                profile_row(profile.LEGACY_PROFILE_SCHEMA_VERSION,
                                            profile.LEGACY_PROFILE_CONTENT, "0" * 64,
                                            profile.LEGACY_PROFILE_ID,
                                            profile.LEGACY_PROFILE_REVISION)})
    expect("METHOD_PROFILE_UNSUPPORTED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)
    # A correct hash of the WRONG profile kind: legacy binding naming the
    # Contract-A core must never validate as a legacy binding.
    wrong_kind = legacy_binding()
    wrong_kind["profile_id"] = A_PROFILE_ID
    wrong_kind["profile_revision"] = A_PROFILE_REVISION
    wrong_kind["profile_canonical_hash"] = A_PROFILE_HASH
    conn = FakeConn(bindings={OID: wrong_kind},
                    profiles={(A_PROFILE_ID, A_PROFILE_REVISION): contract_a_profile_row()},
                    registries={profile.LEGACY_PROTOCOL_ID:
                                registry_row(profile.LEGACY_CONTRACT_VERSION)})
    expect("METHOD_PROFILE_UNSUPPORTED",
           protocol.gate_target_action, conn, SCOPE, OID, "propose_revision", None)


def test_gate_dependency_matrix():
    expect("PROTOCOL_BINDING_MISSING",
           protocol.gate_dependency, FakeConn(), SCOPE, OID, profile.LEGACY_CONTRACT_VERSION)
    conn = legacy_env()
    expect("PROTOCOL_BINDING_CONFLICT",
           protocol.gate_dependency, conn, SCOPE, OID, profile.CONTRACT_A_CONTRACT_VERSION)
    protocol.gate_dependency(conn, SCOPE, OID, profile.LEGACY_CONTRACT_VERSION)
    # Cross-protocol dependency (target legacy, dependency contract-A) conflicts.
    expect("PROTOCOL_BINDING_CONFLICT",
           protocol.gate_dependency, contract_a_env(), SCOPE, OID,
           profile.LEGACY_CONTRACT_VERSION)


def test_resolve_creation_matrix():
    expect("PROTOCOL_POLICY_MISSING",
           protocol.resolve_creation, FakeConn(), SCOPE, DOMAIN, "WorkItem", None)

    bad_policy = policy_row(default_protocol="tkos.unknown",
                            default_contract_version="tkos.unknown/9.9")
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.resolve_creation, FakeConn(scope_policy=bad_policy),
           SCOPE, DOMAIN, "WorkItem", None)

    a_env = FakeConn(scope_policy=contract_a_policy_row(),
                     profiles={(A_PROFILE_ID, A_PROFILE_REVISION): contract_a_profile_row()},
                     registries={profile.CONTRACT_A_PROTOCOL_ID:
                                 {"contract_version": profile.CONTRACT_A_CONTRACT_VERSION,
                                  "content": copy.deepcopy(profile.CONTRACT_A_REGISTRY_CONTENT)}})
    expect("PROTOCOL_UPGRADE_REQUIRED",
           protocol.resolve_creation, a_env, SCOPE, DOMAIN, "WorkItem", None)
    expect("PROTOCOL_UPGRADE_REQUIRED",
           protocol.resolve_creation, a_env, SCOPE, DOMAIN, "WorkItem",
           profile.LEGACY_CONTRACT_VERSION)
    expect("ACTION_NOT_SUPPORTED_FOR_PROTOCOL",
           protocol.resolve_creation, a_env, SCOPE, DOMAIN, "WorkItem",
           profile.CONTRACT_A_CONTRACT_VERSION)

    legacy_base = dict(scope_policy=policy_row(),
                       profiles={(profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION):
                                 legacy_profile_row()},
                       registries={profile.LEGACY_PROTOCOL_ID:
                                   registry_row(profile.LEGACY_CONTRACT_VERSION)})
    expect("PROTOCOL_WRITE_DISABLED", protocol.resolve_creation,
           FakeConn(**{**legacy_base, "scope_policy": policy_row(allow_legacy_create=False)}),
           SCOPE, DOMAIN, "WorkItem", None)
    expect("PROTOCOL_WRITE_DISABLED", protocol.resolve_creation,
           FakeConn(**{**legacy_base, "registries": {profile.LEGACY_PROTOCOL_ID:
                       registry_row(profile.LEGACY_CONTRACT_VERSION, can_create=False)}}),
           SCOPE, DOMAIN, "WorkItem", None)
    expect("ACTION_NOT_SUPPORTED_FOR_PROTOCOL", protocol.resolve_creation,
           FakeConn(**legacy_base), SCOPE, DOMAIN, "CompanyComposer", None)
    expect("METHOD_PROFILE_UNSUPPORTED", protocol.resolve_creation,
           FakeConn(**{**legacy_base, "profiles": {}}), SCOPE, DOMAIN, "WorkItem", None)
    # Policy naming a profile that does not semantically belong to its protocol.
    mismatched = policy_row(default_profile_ref={"profile_id": A_PROFILE_ID,
                                                 "revision": A_PROFILE_REVISION})
    expect("METHOD_PROFILE_UNSUPPORTED", protocol.resolve_creation,
           FakeConn(**{**legacy_base, "scope_policy": mismatched,
                       "profiles": {(A_PROFILE_ID, A_PROFILE_REVISION):
                                    contract_a_profile_row()}}),
           SCOPE, DOMAIN, "WorkItem", None)

    fields = protocol.resolve_creation(FakeConn(**legacy_base), SCOPE, DOMAIN, "WorkItem", None)
    assert fields["protocol_id"] == profile.LEGACY_PROTOCOL_ID
    assert fields["contract_version"] == profile.LEGACY_CONTRACT_VERSION
    assert fields["profile_canonical_hash"] == profile.LEGACY_PROFILE_CANONICAL_HASH


def test_resolve_creation_domain_policy_overrides_scope_default():
    domain_env = FakeConn(
        domain_policies={DOMAIN: policy_row()},
        scope_policy=policy_row(allow_legacy_create=False),
        profiles={(profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION):
                  legacy_profile_row()},
        registries={profile.LEGACY_PROTOCOL_ID: registry_row(profile.LEGACY_CONTRACT_VERSION)})
    fields = protocol.resolve_creation(domain_env, SCOPE, DOMAIN, "WorkItem", None)
    assert fields["protocol_id"] == profile.LEGACY_PROTOCOL_ID


def test_resolve_creation_evidence_upload_path():
    a_env = FakeConn(scope_policy=contract_a_policy_row(),
                     profiles={(A_PROFILE_ID, A_PROFILE_REVISION): contract_a_profile_row()},
                     registries={profile.CONTRACT_A_PROTOCOL_ID:
                                 {"contract_version": profile.CONTRACT_A_CONTRACT_VERSION,
                                  "content": copy.deepcopy(profile.CONTRACT_A_REGISTRY_CONTENT)}})
    # Evidence upload is a metadata-level operation and is allowed for a
    # registered Contract-A scope even though business creation is not.
    fields = protocol.evidence_protocol_fields(a_env, SCOPE, DOMAIN)
    assert fields["protocol_id"] == profile.CONTRACT_A_PROTOCOL_ID
    disabled = copy.deepcopy(profile.LEGACY_REGISTRY_CONTENT)
    disabled["evidence_upload"] = False
    legacy_env_disabled = FakeConn(
        scope_policy=policy_row(),
        profiles={(profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION):
                  legacy_profile_row()},
        registries={profile.LEGACY_PROTOCOL_ID:
                    {"contract_version": profile.LEGACY_CONTRACT_VERSION,
                     "content": disabled}})
    expect("PROTOCOL_WRITE_DISABLED",
           protocol.evidence_protocol_fields, legacy_env_disabled, SCOPE, DOMAIN)


def test_gate_effect_dispatch_matrix():
    protocol.gate_effect_dispatch(legacy_env(), SCOPE, OID, "propose_revision")
    expect("PROTOCOL_BINDING_MISSING",
           protocol.gate_effect_dispatch, FakeConn(), SCOPE, OID, "propose_revision")
    # B04: withdrawing a single action stops already-queued dispatches.
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row(profile.LEGACY_CONTRACT_VERSION,
                                               actions=["create_object"])})
    expect("ACTION_NOT_SUPPORTED_FOR_PROTOCOL",
           protocol.gate_effect_dispatch, conn, SCOPE, OID, "propose_revision")
    # Kill switch: can_write=false blocks every queued dispatch.
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row(profile.LEGACY_CONTRACT_VERSION,
                                               can_write=False)})
    expect("PROTOCOL_WRITE_DISABLED",
           protocol.gate_effect_dispatch, conn, SCOPE, OID, "propose_revision")
    expect("ACTION_NOT_SUPPORTED_FOR_PROTOCOL",
           protocol.gate_effect_dispatch, contract_a_env(), SCOPE, OID, "propose_revision")


def test_read_support_matrix():
    conn = FakeConn()
    metadata = protocol.read_metadata(conn, SCOPE, OID)
    assert metadata["registration_status"] == "unregistered"
    assert metadata["interpretation_status"] == "unsupported_unregistered"
    assert metadata["protocol_id"] is None
    expect("PROTOCOL_NOT_SUPPORTED", protocol.require_read_support, conn, SCOPE, OID)

    metadata = protocol.require_read_support(legacy_env(), SCOPE, OID)
    assert metadata["interpretation_status"] == "legacy_v0_2"
    assert metadata["protocol_id"] == profile.LEGACY_PROTOCOL_ID

    metadata = protocol.require_read_support(contract_a_env(), SCOPE, OID)
    assert metadata["interpretation_status"] == "contract_a_metadata_read_only"

    # Profile missing: explicitly unsupported, never silently legacy.
    conn = legacy_env(profiles={})
    metadata = protocol.read_metadata(conn, SCOPE, OID)
    assert metadata["interpretation_status"] == "profile_unsupported"
    expect("PROTOCOL_NOT_SUPPORTED", protocol.require_read_support, conn, SCOPE, OID)

    # Registry revokes read support.
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row(profile.LEGACY_CONTRACT_VERSION,
                                               can_read=False)})
    metadata = protocol.read_metadata(conn, SCOPE, OID)
    assert metadata["interpretation_status"] == "read_unsupported"
    expect("PROTOCOL_NOT_SUPPORTED", protocol.require_read_support, conn, SCOPE, OID)

    # Registry row naming a different contract version is not read support.
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row("tkos.governed/v9.9")})
    expect("PROTOCOL_NOT_SUPPORTED", protocol.require_read_support, conn, SCOPE, OID)


def test_list_metadata_marks_unregistered_objects():
    conn = legacy_env()
    result = protocol.list_metadata(conn, SCOPE, [OID, "00000000-0000-0000-0000-000000000099"])
    assert result[OID]["interpretation_status"] == "legacy_v0_2"
    assert result["00000000-0000-0000-0000-000000000099"]["registration_status"] == "unregistered"


# ------------------------------------------------------- B10 legacy gate


def test_require_legacy_read_support_matrix():
    # Legacy-registered object passes and returns the metadata.
    metadata = protocol.require_legacy_read_support(legacy_env(), SCOPE, OID)
    assert metadata["interpretation_status"] == "legacy_v0_2"

    # Readable Contract-A metadata is NOT legacy interpretation (B10 core).
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.require_legacy_read_support, contract_a_env(), SCOPE, OID)

    # Missing binding / withdrawn read support / unsupported profile keep
    # their original hard-gate error.
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.require_legacy_read_support, FakeConn(), SCOPE, OID)
    conn = legacy_env(registries={profile.LEGACY_PROTOCOL_ID:
                                  registry_row(profile.LEGACY_CONTRACT_VERSION,
                                               can_read=False)})
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.require_legacy_read_support, conn, SCOPE, OID)
    conn = legacy_env(profiles={})
    expect("PROTOCOL_NOT_SUPPORTED",
           protocol.require_legacy_read_support, conn, SCOPE, OID)
