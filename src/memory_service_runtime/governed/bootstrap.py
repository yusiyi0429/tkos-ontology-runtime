"""Explicit test-only scope provisioning; never exposed as a public API.

Only identities, versioned authority and one already-confirmed starting Outcome
are seeded. All commitments, handshakes, decisions and feedback transitions in
the acceptance suite must still be produced through authenticated commands.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import profile, protocol
from .db import set_write_capability
from .errors import GovernedError


ALL_ROLES = ["CEO", "DOMAIN_DRI", "MISSION_DRI", "VERIFIER", "IC", "AGENT"]
ACTION_ROLES = {
    "read": ALL_ROLES,
    "upload_evidence": ALL_ROLES,
    "create_object": ["CEO", "DOMAIN_DRI", "MISSION_DRI", "AGENT"],
    "propose_revision": ["CEO", "DOMAIN_DRI", "MISSION_DRI", "AGENT"],
    "accept_commitment": ["CEO", "DOMAIN_DRI", "MISSION_DRI"],
    "activate_commitment": ["CEO", "DOMAIN_DRI"],
    "confirm_decision": ["CEO", "DOMAIN_DRI"],
    "confirm_outcome": ["CEO"],
    "route_feedback": ["CEO", "DOMAIN_DRI"],
    "accept_feedback": ["CEO", "DOMAIN_DRI", "MISSION_DRI", "IC"],
    "investigate_feedback": ["CEO", "DOMAIN_DRI", "MISSION_DRI", "IC"],
    "confirm_adjustment": ["CEO", "DOMAIN_DRI"],
    "request_feedback_acceptance": ["CEO", "DOMAIN_DRI", "MISSION_DRI"],
    "record_acceptance": ["VERIFIER"],
    "confirm_closure": ["CEO", "DOMAIN_DRI"],
    "reopen_feedback": ["CEO", "DOMAIN_DRI"],
    "record_observation": ["CEO", "DOMAIN_DRI", "MISSION_DRI", "AGENT"],
    "revoke_assignment": ["CEO"],
    "accept_work_item": ["MISSION_DRI"],
    "submit_deliverable": ["MISSION_DRI"],
    "review_deliverable": ["CEO", "DOMAIN_DRI", "VERIFIER"],
    "record_outcome_assessment": ["CEO"],
}


def _id() -> str:
    return str(uuid4())


def seed_scope(conn: psycopg.Connection, tenant_id: str, company_id: str) -> dict[str, Any]:
    """Seed only an explicit runtime-acceptance namespace under FORCE RLS."""
    allowed = ("runtime-acceptance-", "tkos-runtime-acceptance-")
    if (not isinstance(tenant_id, str) or not isinstance(company_id, str)
            or not tenant_id.startswith(allowed) or not company_id.startswith(allowed)):
        raise GovernedError("FORBIDDEN", "Bootstrap accepts only isolated runtime acceptance scopes.")
    conn.row_factory = dict_row
    scope_id, domain_id, outsider_domain_id = _id(), _id(), _id()
    policy_revision_id = _id()
    actors: dict[str, dict[str, str]] = {}
    with conn.transaction():
        # Controlled provisioning runs as the database owner: declare the A1
        # runtime capability (0018 write fences) and the control plane (owner-
        # checked by gov_control_plane_on()) before any protected statement.
        set_write_capability(conn)
        conn.execute("SELECT set_config('app.gov_control_plane', 'on', true)")
        conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope_id,))
        conn.execute("INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES (%s,%s,%s)",
                     (scope_id, tenant_id, company_id))
        for domain, name in ((domain_id, "Synthetic acceptance domain"),
                             (outsider_domain_id, "Synthetic separate domain")):
            conn.execute("INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)",
                         (domain, scope_id, name))
        role_names = {"ceo": "CEO", "domain_dri": "DOMAIN_DRI", "mission_dri": "MISSION_DRI",
                      "verifier": "VERIFIER", "ic": "IC", "agent": "AGENT", "outsider": "DOMAIN_DRI"}
        for name, role in role_names.items():
            principal_id, assignment_id = _id(), _id()
            token = secrets.token_urlsafe(48)
            digest = hashlib.sha256(token.encode()).hexdigest()
            conn.execute(
                """INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name)
                    VALUES (%s,%s,%s,%s)""",
                (principal_id, scope_id, "agent" if name == "agent" else "human", "Synthetic " + name),
            )
            conn.execute(
                """INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role)
                    VALUES (%s,%s,%s,%s,%s)""",
                (assignment_id, scope_id, principal_id, outsider_domain_id if name == "outsider" else domain_id, role),
            )
            conn.execute("SELECT set_config('app.governed_credential_digest', %s, true)", (digest,))
            conn.execute(
                """INSERT INTO gov_credentials(scope_id,principal_id,credential_digest,label)
                    VALUES (%s,%s,%s,%s)""", (scope_id, principal_id, digest, "acceptance " + name),
            )
            actors[name] = {"principal_id": principal_id, "assignment_id": assignment_id, "token": token}
        policy = {
            "version": "tkos.governed-policy/0.2", "action_roles": ACTION_ROLES,
            "commitment_party_roles": {"BusinessCommitment": ["CEO", "DOMAIN_DRI"],
                                       "ExecutionCommitment": ["DOMAIN_DRI", "MISSION_DRI"]},
            "independent_verifier": True,
        }
        for domain, revision in ((domain_id, policy_revision_id), (outsider_domain_id, _id())):
            conn.execute(
                """INSERT INTO gov_activation_policies(policy_revision_id,scope_id,domain_id,
                           policy_id,policy_seq,content,recorded_by)
                    VALUES (%s,%s,%s,%s,1,%s,%s)""",
                (revision, scope_id, domain, _id(), Jsonb(policy), actors["ceo"]["principal_id"]),
            )
        object_id, revision_id = _id(), _id()
        payload = {"title": "Synthetic confirmed CompanyOutcome",
                   "terms": {"target": 80, "unit": "deliveries"}, "upstream_refs": []}
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                separators=(",", ":")).encode()).hexdigest()
        conn.execute(
            """INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)
                VALUES (%s,%s,%s,'CompanyOutcome','confirmed')""", (object_id, scope_id, domain_id),
        )
        conn.execute(
            """INSERT INTO gov_object_revisions(revision_id,scope_id,object_id,object_version,
                       payload,payload_hash,recorded_by,valid_from)
                VALUES (%s,%s,%s,1,%s,%s,%s,'2000-01-01T00:00:00Z')""",
            (revision_id, scope_id, object_id, Jsonb(payload), payload_hash, actors["ceo"]["principal_id"]),
        )
        conn.execute(
            """UPDATE gov_objects SET latest_revision_id=%s,effective_revision_id=%s
                WHERE scope_id=%s AND object_id=%s""", (revision_id, revision_id, scope_id, object_id),
        )
        conn.execute(
            """INSERT INTO gov_lifecycle_events(scope_id,object_id,event_type,to_status,principal_id,detail)
                VALUES (%s,%s,'bootstrap_confirmed','confirmed',%s,%s)""",
            (scope_id, object_id, actors["ceo"]["principal_id"], Jsonb({
                "revision_id": revision_id, "effective_revision_id": revision_id,
                "before_version": 0, "object_version": 1, "test_only": True,
            })),
        )
        conn.execute("SELECT set_config('app.governed_credential_digest', '', true)")
        # A1 registration: a fresh acceptance scope is explicitly registered as
        # legacy — the interpretation record, the scope default policy, both
        # support-registry entries, and the seeded Outcome's binding.  New
        # scopes never default to legacy implicitly; this block is the record.
        conn.execute(
            """INSERT INTO gov_method_profile_revisions
               (scope_id, profile_id, revision, schema_version, canonical_hash,
                action_contract_ref, record_origin, experimental, content,
                installed_by, install_reason)
               VALUES (%s,%s,%s,%s,%s,NULL,'legacy',false,%s,%s,%s)""",
            (scope_id, profile.LEGACY_PROFILE_ID, profile.LEGACY_PROFILE_REVISION,
             "tkos.legacy-interpretation-record/0.2", profile.LEGACY_PROFILE_CANONICAL_HASH,
             Jsonb(profile.LEGACY_PROFILE_CONTENT), "bootstrap",
             "controlled acceptance scope legacy registration"),
        )
        conn.execute(
            """INSERT INTO gov_protocol_policies
               (scope_id, domain_id, policy_seq, content, recorded_by, reason)
               VALUES (%s,NULL,1,%s,%s,%s)""",
            (scope_id, Jsonb(profile.LEGACY_SCOPE_POLICY_CONTENT), "bootstrap",
             "controlled acceptance scope legacy registration"),
        )
        for protocol_id, contract_version, content in (
                (profile.LEGACY_PROTOCOL_ID, profile.LEGACY_CONTRACT_VERSION,
                 profile.LEGACY_REGISTRY_CONTENT),
                (profile.CONTRACT_A_PROTOCOL_ID, profile.CONTRACT_A_CONTRACT_VERSION,
                 profile.CONTRACT_A_REGISTRY_CONTENT)):
            conn.execute(
                """INSERT INTO gov_protocol_support_registry
                   (scope_id, protocol_id, contract_version, registry_seq, content, recorded_by)
                   VALUES (%s,%s,%s,1,%s,%s)""",
                (scope_id, protocol_id, contract_version, Jsonb(content), "bootstrap"),
            )
        protocol.insert_binding(
            conn, scope_id, object_id,
            {"protocol_id": profile.LEGACY_PROTOCOL_ID,
             "contract_version": profile.LEGACY_CONTRACT_VERSION,
             "profile_id": profile.LEGACY_PROFILE_ID,
             "profile_revision": profile.LEGACY_PROFILE_REVISION,
             "profile_canonical_hash": profile.LEGACY_PROFILE_CANONICAL_HASH,
             "record_origin": "legacy"},
            registered_by="bootstrap",
            detail={"registration": "legacy_bootstrap"},
        )
        conn.execute(
            """INSERT INTO gov_protocol_control_events (scope_id, event_type, detail, actor)
               VALUES (%s,'bootstrap_legacy_registration',%s,'bootstrap')""",
            (scope_id, Jsonb({"kind": "legacy_registration", "source": "bootstrap.seed_scope",
                              "seeded_object_ids": [object_id]})),
        )
    return {"scope_id": scope_id, "tenant_id": tenant_id, "company_id": company_id,
            "domain_id": domain_id, "policy_revision_id": policy_revision_id,
            "actors": actors, "outcome": {"object_id": object_id, "revision_id": revision_id}}
