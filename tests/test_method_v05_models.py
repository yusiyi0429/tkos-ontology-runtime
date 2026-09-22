"""Strict 0.5 schema, registry and dispatch boundaries (no DB required)."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from memory_service_runtime.governed import method_v05_profile as profile

ROOT = Path(__file__).resolve().parents[1]


def test_contract_and_registry_pins_and_profile_validate():
    assert profile.validate(profile.content())
    assert sha256((ROOT / 'docs/contracts/tkos-method-0.5.md').read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    assert sha256((ROOT / 'docs/contracts/ontology-registry-0.7.json').read_bytes()).hexdigest() == profile.ONTOLOGY_REGISTRY_SHA256
    registry = json.loads((ROOT / 'docs/contracts/ontology-registry-0.7.json').read_text())
    assert registry['revision'] == profile.ONTOLOGY_REGISTRY_REVISION
    saved = json.loads((ROOT / 'docs/contracts/method-profile-0.5.json').read_text())
    assert profile.validate(saved).canonical_hash == profile.content()['canonical_hash']
