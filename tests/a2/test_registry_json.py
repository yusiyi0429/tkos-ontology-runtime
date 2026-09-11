"""Offline consistency check: docs/runtime-a2-registry.json must match the
compiled A2 whitelist in a2_models (review 12 double control) and the registry
shape used by gov_protocol_support_registry content.
"""
from __future__ import annotations

import json
from pathlib import Path

from memory_service_runtime.governed import a2_models as a2

REGISTRY = (Path(__file__).resolve().parents[2] / "docs/runtime-a2-registry.json")


def _content() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_file_is_valid_json_object():
    assert isinstance(_content(), dict)


def test_registry_actions_match_compiled_whitelist():
    content = _content()
    assert set(content["actions"]) == set(a2.A2_ACTIONS) | {"create_object", "propose_revision"}


def test_registry_object_types_match_compiled_whitelist():
    content = _content()
    assert set(content["object_types"]) == set(a2.A2_OBJECT_TYPES)


def test_registry_switches_and_compat():
    content = _content()
    assert content["can_read"] is True
    assert content["can_create"] is True
    assert content["can_write"] is True
    assert content["evidence_upload"] is True
    assert content["readonly_compat"] == ["tkos.contract-a/0.1"]
    assert isinstance(content["notes"], str) and content["notes"]


def test_registry_matches_api_doc_section_7():
    doc = (Path(__file__).resolve().parents[2] / "docs/runtime-a2-api.md").read_text("utf-8")
    assert "docs/runtime-a2-registry.json" in doc
    for action in a2.A2_ACTIONS:
        assert f'"{action}"' in doc
