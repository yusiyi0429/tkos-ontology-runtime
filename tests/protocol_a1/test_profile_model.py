"""Offline tests for the ProfileCore strict model and protocol implication.

Includes the R01 reference-integrity probes whose expectations are recorded
in the task output directory (profile-wip-probes.json: all three probes
must be REJECTED).
"""
from __future__ import annotations

import copy

import pytest

from memory_service_runtime.governed import artifacts, canon, profile


def _bundled_core_dict() -> dict:
    return canon.load_json_strict(artifacts.profile_core_bytes().decode("utf-8"))


def _rehash(data: dict) -> dict:
    """Recompute canonical_hash so a mutation is rejected only by the targeted
    check (reference integrity), never by a stale hash."""
    data = copy.deepcopy(data)
    data["canonical_hash"] = canon.digest_excluding(data, frozenset({"canonical_hash"}))
    return data


def test_legacy_constants_are_pinned_and_self_consistent():
    assert profile.LEGACY_PROFILE_CANONICAL_HASH == (
        "93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598")
    assert profile.LEGACY_PROFILE_CANONICAL_HASH == canon.digest(profile.LEGACY_PROFILE_CONTENT)


def test_legacy_identity_implies_legacy_pair():
    implied = profile.implied_protocol(
        profile.LEGACY_PROFILE_SCHEMA_VERSION, profile.LEGACY_PROFILE_CONTENT)
    assert implied == (profile.LEGACY_PROTOCOL_ID, profile.LEGACY_CONTRACT_VERSION)


def test_implied_protocol_rejects_wrong_semantics():
    # A legacy record with a mutated identity must not imply the legacy pair.
    tampered = dict(profile.LEGACY_PROFILE_CONTENT, profile_id="urn:tkos:legacy:other")
    assert profile.implied_protocol(profile.LEGACY_PROFILE_SCHEMA_VERSION, tampered) is None
    # A ProfileCore bound to different contract bytes is not Contract-A/0.1.
    core = _bundled_core_dict()
    core["action_contract_ref"]["content_sha256"] = "0" * 64
    assert profile.implied_protocol(profile.PROFILE_CORE_SCHEMA_VERSION, core) is None
    # Unknown schema versions never imply anything.
    assert profile.implied_protocol("tkos.unknown/9.9", {}) is None
    # Non-dict content never implies anything.
    assert profile.implied_protocol(profile.LEGACY_PROFILE_SCHEMA_VERSION, None) is None


def test_r01_probe_duplicate_source_rejected():
    data = _bundled_core_dict()
    assert data["sources"], "the frozen fixture must declare at least one source"
    data["sources"].append(copy.deepcopy(data["sources"][0]))
    with pytest.raises(ValueError, match="duplicate source_id"):
        profile.validate_profile_core(_rehash(data))


def test_r01_probe_dangling_alias_source_rejected():
    data = _bundled_core_dict()
    alias = {
        "alias_id": "probe-alias", "label": "probe", "source_id": "no-such-source",
        "locations": ["probe"], "candidate_concept_ids": [],
        "unresolved_note": None,
        "mapping_status": "SOURCE_ALIAS_ONLY_PENDING_METHOD_CONFIRMATION",
        "automatic_equivalence_or_migration": False,
    }
    data["source_aliases"].append(alias)
    with pytest.raises(ValueError, match="unknown source"):
        profile.validate_profile_core(_rehash(data))


def test_r01_probe_dangling_alias_concept_rejected():
    data = _bundled_core_dict()
    source_id = data["sources"][0]["source_id"]
    alias = {
        "alias_id": "probe-alias", "label": "probe", "source_id": source_id,
        "locations": ["probe"], "candidate_concept_ids": ["no-such-concept"],
        "unresolved_note": None,
        "mapping_status": "SOURCE_ALIAS_ONLY_PENDING_METHOD_CONFIRMATION",
        "automatic_equivalence_or_migration": False,
    }
    data["source_aliases"].append(alias)
    with pytest.raises(ValueError, match="unknown concept"):
        profile.validate_profile_core(_rehash(data))


def test_strict_model_rejects_extra_fields_and_relaxed_types():
    data = _bundled_core_dict()
    data["unexpected_field"] = True
    with pytest.raises(ValueError):
        profile.validate_profile_core(data)
    data = _bundled_core_dict()
    data["experimental"] = 1  # must be a JSON boolean, not int
    with pytest.raises(ValueError):
        profile.validate_profile_core(_rehash(data))


def test_frozen_policy_and_registry_constants_validate():
    from memory_service_runtime.governed import protocol

    protocol.PolicyContent.model_validate(profile.LEGACY_SCOPE_POLICY_CONTENT)
    protocol.RegistryContent.model_validate(profile.LEGACY_REGISTRY_CONTENT)
    protocol.RegistryContent.model_validate(profile.CONTRACT_A_REGISTRY_CONTENT)
    protocol.PolicyContent.model_validate(
        profile.contract_a_policy_content("urn:tkos:profile:x", "0.1.0"))
