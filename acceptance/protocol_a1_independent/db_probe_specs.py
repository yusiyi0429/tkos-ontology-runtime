"""Synthetic, owner-valid control-plane SQL inputs for broad-GRANT probes.

Inputs are based on reviewed 0018 columns. They do not create identities or
business successes. The test transaction rolls back both positive and negative
attempts. Call only after the target scope has its real legacy registration.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import uuid

from .control_adapter import A_CONTRACT, A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL
from .db_adversary import validate_spec
from .profile_cases import canonical_hash


def jsonb(value):
    return {"$jsonb": value}


def build_control_spec(scope_id: str, domain_id: str, existing_object_id: str, profile_file: Path,
                       *, existing_binding: dict, policy_seq: int, registry_seq: int) -> dict:
    """Pass observed current maxima+1; owner positive rejects invalid fixtures.

    The first four rows exercise app-controlled GUC escalation even when the
    normal deployment happened not to grant INSERT. The last row appends a new
    version to an existing binding, an operation ordinary app must not possess.
    """
    core = json.loads(profile_file.read_text())
    core["revision"] = "0.1.0-db-probe-" + uuid.uuid4().hex
    core["canonical_hash"] = canonical_hash(core)
    profile_columns = ["scope_id", "profile_id", "revision", "schema_version", "canonical_hash",
                       "action_contract_ref", "record_origin", "experimental", "content", "installed_by", "install_reason"]
    profile_values = [scope_id, core["profile_id"], core["revision"], core["profile_core_schema_version"],
                      core["canonical_hash"], jsonb(core["action_contract_ref"]), "synthetic", True,
                      jsonb(core), "independent-owner-probe", "Synthetic SQL permission acceptance"]
    policy = {"default_protocol": LEGACY_PROTOCOL, "default_contract_version": LEGACY_CONTRACT,
        "allow_legacy_create": True, "record_origin": "synthetic",
        "default_profile_ref": {"profile_id": existing_binding["profile_id"], "revision": existing_binding["profile_revision"]},
        "experimental": True, "notes": "Synthetic owner-valid policy probe; always rolled back"}
    registry = {"can_read": True, "can_create": False, "can_write": False, "evidence_upload": False,
        "actions": [], "object_types": [], "readonly_compat": [A_CONTRACT],
        "notes": "Synthetic registry probe; always rolled back"}
    fields = ["scope_id", "object_id", "binding_version", "protocol_id", "contract_version", "profile_id",
              "profile_revision", "profile_canonical_hash", "record_origin", "registered_by", "detail"]
    values = [scope_id, existing_object_id, existing_binding["binding_version"] + 1,
              existing_binding["protocol_id"], existing_binding["contract_version"], existing_binding["profile_id"],
              existing_binding["profile_revision"], existing_binding["profile_canonical_hash"],
              "synthetic", "independent-owner-probe", jsonb({"purpose": "unauthorized higher binding version probe"})]
    probes = [
        {"case_id": "A1-11-broad-profile-insert", "table": "gov_method_profile_revisions",
         "columns": profile_columns, "values": profile_values},
        {"case_id": "A1-11-broad-policy-insert", "table": "gov_protocol_policies",
         "columns": ["scope_id", "domain_id", "policy_seq", "content", "recorded_by", "reason"],
         "values": [scope_id, domain_id, policy_seq, jsonb(policy), "independent-owner-probe", "Synthetic probe"]},
        {"case_id": "A1-11-broad-registry-insert", "table": "gov_protocol_support_registry",
         "columns": ["scope_id", "protocol_id", "contract_version", "registry_seq", "content", "recorded_by"],
         "values": [scope_id, A_PROTOCOL, A_CONTRACT, registry_seq, jsonb(registry), "independent-owner-probe"]},
        {"case_id": "A1-11-broad-control-event-insert", "table": "gov_protocol_control_events",
         "columns": ["scope_id", "event_type", "detail", "actor"],
         "values": [scope_id, "independent_permission_probe", jsonb({"synthetic": True}), "independent-owner-probe"]},
        {"case_id": "A1-11-append-existing-binding-v2", "table": "gov_object_protocol_bindings",
         "columns": fields, "values": values},
    ]
    for probe in probes:
        probe["expected_sqlstates"] = ["55000"] if probe["table"] == "gov_object_protocol_bindings" else ["42501"]
    spec = {"scope_id": scope_id, "session_settings": {
        "app.gov_control_plane": "on", "app.runtime_write_capability": "tkos-runtime-a1"},
        "tables": probes, "functions": [], "function_allowlist": []}
    validate_spec(spec)
    return spec
