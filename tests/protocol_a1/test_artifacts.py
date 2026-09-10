"""Offline tests for the bundled frozen Contract-A artifacts (no database).

The runtime must carry the exact main-contract bytes and the frozen
profile-core fixture inside the package; nothing may resolve Semantica
output paths at run time.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from memory_service_runtime.governed import artifacts, canon, profile

PINNED_PROFILE_CORE_CANONICAL_HASH = "5050d542cc521991f17932562c4397331c0482b673f9bcd8fe694749bd43fc76"


def test_bundled_contract_bytes_match_pinned_sha():
    data = artifacts.contract_a_bytes()
    assert hashlib.sha256(data).hexdigest() == profile.CONTRACT_A_MAIN_CONTRACT_SHA256
    assert data  # non-empty


def test_bundled_profile_core_bytes_match_pinned_sha():
    data = artifacts.profile_core_bytes()
    assert hashlib.sha256(data).hexdigest() == artifacts.PROFILE_CORE_SHA256


def test_bundled_profile_core_validates_and_matches_frozen_canonical_hash():
    data = canon.load_json_strict(artifacts.profile_core_bytes().decode("utf-8"))
    core = profile.validate_profile_core(data)
    assert core.canonical_hash == PINNED_PROFILE_CORE_CANONICAL_HASH
    assert core.action_contract_ref.content_sha256 == profile.CONTRACT_A_MAIN_CONTRACT_SHA256


def test_bundled_profile_core_implies_contract_a_pair():
    data = canon.load_json_strict(artifacts.profile_core_bytes().decode("utf-8"))
    implied = profile.implied_protocol(profile.PROFILE_CORE_SCHEMA_VERSION, data)
    assert implied == (profile.CONTRACT_A_PROTOCOL_ID, profile.CONTRACT_A_CONTRACT_VERSION)


def test_artifacts_module_has_no_semantica_absolute_path():
    source = Path(artifacts.__file__).read_text(encoding="utf-8")
    assert "/Users/" not in source
    assert "/semantica" not in source
    assert "importlib.resources" in source or "files(" in source
