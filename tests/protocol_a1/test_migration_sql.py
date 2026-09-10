"""Static checks on migration 0018 (no database): the SQL text itself must
carry the pinned frozen constants, the fail-closed guards, and no secrets or
host-local absolute paths.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from memory_service_runtime.governed import profile

MIGRATION = (Path(__file__).resolve().parents[2]
             / "src/memory_service_app/migrations/0018_method_protocol.sql")


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_file_exists():
    assert MIGRATION.is_file()


def test_new_tables_created_and_forced_rls():
    text = _text()
    for table in ("gov_method_profile_revisions", "gov_protocol_policies",
                  "gov_protocol_support_registry", "gov_protocol_control_events",
                  "gov_object_protocol_bindings"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in text
        assert f"'{table}'" in text  # part of the FORCE RLS / append-only loops
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "ENABLE ROW LEVEL SECURITY" in text


def test_identity_tables_have_restrictive_capability_policies():
    text = _text()
    for table in ("gov_scopes", "gov_principals", "gov_role_assignments", "gov_credentials"):
        assert f"'{table}'" in text
    assert "AS RESTRICTIVE FOR SELECT" in text


def test_pinned_hashes_embedded_match_profile_constants():
    text = _text()
    assert profile.LEGACY_PROFILE_CANONICAL_HASH in text
    assert profile.CONTRACT_A_MAIN_CONTRACT_SHA256 in text
    assert "93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598" in text


def test_embedded_json_literals_equal_python_constants():
    text = _text()
    literals = [json.loads(match) for match in re.findall(r"'(\{[^']*\})'::jsonb", text)]
    expected = [profile.LEGACY_PROFILE_CONTENT, profile.LEGACY_SCOPE_POLICY_CONTENT,
                profile.LEGACY_REGISTRY_CONTENT, profile.CONTRACT_A_REGISTRY_CONTENT]
    for constant in expected:
        assert constant in literals, f"embedded literal missing: {constant!r}"


def test_b08_fail_closed_guards():
    text = _text()
    # NULL must never skip a guard: the profile-semantic checks and the
    # control-plane/capability guards use IS NOT TRUE.
    assert "IS NOT TRUE THEN" in text
    assert "IF NOT (prow.schema_version" not in text
    assert "IF NOT gov_control_plane_on()" not in text
    assert "IS NOT DISTINCT FROM 'on'" in text
    assert "IS NOT DISTINCT FROM 'tkos-runtime-a1'" in text


def test_no_secrets_or_host_absolute_paths():
    text = _text().lower()
    for needle in ("/users/", "/semantica", "password", "postgres://", "postgresql://",
                   "secret", "token"):
        assert needle not in text, f"migration text must not contain {needle!r}"


def test_binding_gate_rejects_unknown_protocol_pairs():
    text = _text()
    assert "is not implemented by this schema" in text
    assert "gov_object_protocol_bindings" in text
