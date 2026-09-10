"""Frozen legacy protocol registration rows for unit-test fakes (A1).

Every identity, version, schema and hash below is a FIXED LITERAL copied from
the frozen migration-0018 legacy registration — this module deliberately does
NOT import the production constants, so a drift in the implementation under
test fails these tests instead of silently updating the fixture.  The SHA256
pins are likewise literal.

`answer_query` accepts ONLY the four exact normalized single-statement SELECT
shapes issued by memory_service_runtime.governed.protocol (single binding,
batched binding, installed profile, current registry) with their exact
parameter counts and the fixture-fixed scope as params[0].  Writes,
multi-statements, unknown shapes over the registration tables and scope
mismatches raise AssertionError; statements over any other table return None
so the caller's own strict routing keeps handling them.

Only objects explicitly named by the caller (known_object_ids) receive a
legacy row; unknown objects get empty results and are therefore
"unregistered", never default-legacy.
"""
from __future__ import annotations

import copy

# --- frozen literals (migration 0018 legacy registration; do not import) ---

LEGACY_PROTOCOL_ID = "tkos.legacy-governed"
LEGACY_CONTRACT_VERSION = "tkos.governed/v0.2"
LEGACY_PROFILE_ID = "urn:tkos:legacy:governed-v0.2"
LEGACY_PROFILE_REVISION = "0.2.0"
LEGACY_PROFILE_SCHEMA_VERSION = "tkos.legacy-interpretation-record/0.2"
LEGACY_PROFILE_CANONICAL_HASH = (
    "93c5278a70c70af453e2f86c6d2b298caefe15b1e14b736c006c817c8a182598")

LEGACY_PROFILE_CONTENT: dict = {
    "profile_kind": "tkos.legacy-interpretation-record",
    "profile_id": LEGACY_PROFILE_ID,
    "revision": LEGACY_PROFILE_REVISION,
    "display_name": "Legacy governed runtime v0.2 interpretation record",
    "protocol_id": LEGACY_PROTOCOL_ID,
    "contract_version": LEGACY_CONTRACT_VERSION,
    "experimental": False,
    "corporate_approved": False,
    "record_origin": "legacy",
    "semantics": (
        "Pre-contract-A governed runtime semantics. MISSION_DRI retains its "
        "legacy meaning; no IC handover, company composition or MethodProfile "
        "interpretation is implied. Historical payloads, receipts and hashes "
        "are preserved unchanged."
    ),
    "source": "runtime baseline 3cd9109d726a9a9069a7960a2f2665ce677785d2 behavior",
}

LEGACY_REGISTRY_CONTENT: dict = {
    "can_read": True,
    "can_create": True,
    "can_write": True,
    "evidence_upload": True,
    "actions": [
        "accept_commitment", "accept_feedback", "accept_work_item",
        "activate_commitment", "confirm_adjustment", "confirm_closure",
        "confirm_decision", "confirm_outcome", "create_object",
        "investigate_feedback", "propose_revision", "record_acceptance",
        "record_outcome_assessment", "reopen_feedback",
        "request_feedback_acceptance", "review_deliverable", "revoke_assignment",
        "route_feedback", "submit_deliverable",
    ],
    "object_types": [
        "BusinessCommitment", "CompanyOutcome", "Decision", "Deliverable",
        "EvidenceAsset", "ExecutionCommitment", "FeedbackThread",
        "ManagementAdjustment", "MetricObservation", "WorkItem",
    ],
    "readonly_compat": [LEGACY_CONTRACT_VERSION],
    "notes": "Legacy v0.2 handlers remain the only executable business handlers in A1.",
}

BINDING_TABLE = "gov_object_protocol_bindings"
PROFILE_TABLE = "gov_method_profile_revisions"
REGISTRY_TABLE = "gov_protocol_support_registry"
KNOWN_TABLES = (BINDING_TABLE, PROFILE_TABLE, REGISTRY_TABLE)

# Exact full normalized queries issued by governed/protocol.py (whitespace
# collapsed the same way answer_query normalizes incoming SQL).  Matching is
# equality, not prefix: a drifted production query must fail the fixture.
_BINDING_SINGLE_QUERY = (
    "SELECT * FROM gov_object_protocol_bindings "
    "WHERE scope_id=%s AND object_id=%s "
    "ORDER BY binding_version DESC, recorded_at DESC, binding_id DESC LIMIT 1")
_BINDING_BATCH_QUERY = (
    "SELECT DISTINCT ON (object_id) * FROM gov_object_protocol_bindings "
    "WHERE scope_id=%s AND object_id=ANY(%s::uuid[]) "
    "ORDER BY object_id, binding_version DESC, recorded_at DESC, binding_id DESC")
_PROFILE_QUERY = (
    "SELECT * FROM gov_method_profile_revisions "
    "WHERE scope_id=%s AND profile_id=%s AND revision=%s")
_REGISTRY_QUERY = (
    "SELECT * FROM gov_protocol_support_registry "
    "WHERE scope_id=%s AND protocol_id=%s "
    "ORDER BY registry_seq DESC, recorded_at DESC, registry_row_id DESC LIMIT 1")

_WRITE_MARKERS = ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ",
                  "TRUNCATE ", "FOR UPDATE", "FOR SHARE")


def binding_row(scope_id, object_id):
    """Frozen version-1 legacy binding for one explicitly known fixture object."""
    return {
        "scope_id": scope_id,
        "object_id": object_id,
        "binding_id": "00000000-0000-0000-0000-0000000000b1",
        "binding_version": 1,
        "protocol_id": LEGACY_PROTOCOL_ID,
        "contract_version": LEGACY_CONTRACT_VERSION,
        "profile_id": LEGACY_PROFILE_ID,
        "profile_revision": LEGACY_PROFILE_REVISION,
        "profile_canonical_hash": LEGACY_PROFILE_CANONICAL_HASH,
        "record_origin": "legacy",
        "run_id": None,
        "registered_by": "unit-fixture",
        "receipt_id": None,
        "detail": {"registration": "legacy_backfill", "migration": "0018_method_protocol"},
        "recorded_at": "2026-09-10T00:00:00+00:00",
    }


def profile_row(scope_id):
    """The installed legacy interpretation record, as migration 0018 installs it."""
    return {
        "scope_id": scope_id,
        "profile_id": LEGACY_PROFILE_ID,
        "revision": LEGACY_PROFILE_REVISION,
        "schema_version": LEGACY_PROFILE_SCHEMA_VERSION,
        "canonical_hash": LEGACY_PROFILE_CANONICAL_HASH,
        "action_contract_ref": None,
        "record_origin": "legacy",
        "experimental": False,
        "content": copy.deepcopy(LEGACY_PROFILE_CONTENT),
        "installed_by": "unit-fixture",
        "install_reason": "unit fixture",
        "recorded_at": "2026-09-10T00:00:00+00:00",
    }


def registry_row(scope_id):
    """The seq-1 legacy support registry entry, as migration 0018 installs it."""
    return {
        "scope_id": scope_id,
        "protocol_id": LEGACY_PROTOCOL_ID,
        "contract_version": LEGACY_CONTRACT_VERSION,
        "registry_seq": 1,
        "content": copy.deepcopy(LEGACY_REGISTRY_CONTENT),
        "recorded_by": "unit-fixture",
        "recorded_at": "2026-09-10T00:00:00+00:00",
    }


def answer_query(sql, params, scope_id, known_object_ids):
    """Answer one of the four exact protocol registration reads.

    Returns a list of row dicts (possibly empty) for a recognized read, or
    None when the statement does not touch a registration table — the caller
    keeps its existing strict routing for every other statement.  Anything
    malformed over a registration table (write, multi-statement, unknown
    shape, wrong parameter count, wrong scope) raises AssertionError.
    """
    text = " ".join(str(sql).split())
    hits = [table for table in KNOWN_TABLES if table in text]
    upper = text.upper()
    if (";" in text or not upper.startswith("SELECT ")
            or any(marker in upper for marker in _WRITE_MARKERS)):
        if hits:
            raise AssertionError(f"non-read statement over a registration table: {text[:120]}")
        return None
    if not hits:
        return None
    if len(hits) > 1:
        raise AssertionError(f"ambiguous registration read: {text[:120]}")
    if not params or str(params[0]) != str(scope_id):
        raise AssertionError(f"registration read with foreign/missing scope: {text[:120]}")
    table = hits[0]
    if table == BINDING_TABLE:
        if text == _BINDING_SINGLE_QUERY:
            if len(params) != 2:
                raise AssertionError(f"binding read with {len(params)} params: {text[:120]}")
            object_id = str(params[1])
            return ([binding_row(scope_id, object_id)]
                    if object_id in {str(oid) for oid in known_object_ids} else [])
        if text == _BINDING_BATCH_QUERY:
            if len(params) != 2:
                raise AssertionError(f"batched binding read with {len(params)} params: {text[:120]}")
            known = {str(oid) for oid in known_object_ids}
            return [binding_row(scope_id, oid) for oid in dict.fromkeys(map(str, params[1]))
                    if oid in known]
        raise AssertionError(f"unknown binding read shape: {text[:120]}")
    if table == PROFILE_TABLE:
        if text != _PROFILE_QUERY:
            raise AssertionError(f"unknown profile read shape: {text[:120]}")
        if len(params) != 3:
            raise AssertionError(f"profile read with {len(params)} params: {text[:120]}")
        if params[1] == LEGACY_PROFILE_ID and params[2] == LEGACY_PROFILE_REVISION:
            return [profile_row(scope_id)]
        return []
    if text != _REGISTRY_QUERY:
        raise AssertionError(f"unknown registry read shape: {text[:120]}")
    if len(params) != 2:
        raise AssertionError(f"registry read with {len(params)} params: {text[:120]}")
    if params[1] == LEGACY_PROTOCOL_ID:
        return [registry_row(scope_id)]
    return []
