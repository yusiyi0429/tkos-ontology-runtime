"""Server-side protocol attribution, support matrix and write fences (A1).

Protocol ownership is decided by the server from registered bindings, policies
and the support registry — never from client declarations. The optional request
field ``contract_version`` only declares which action format the client
understands; when absent it must not change the legacy request_hash
(model_dump(exclude_none=True)).

Gate ordering: the caller is authenticated and authorized (404/403 boundaries)
BEFORE any protocol error is revealed, so protocol details never leak the
existence of an invisible object. prepare and execute share this module, so a
request that will be fenced at execute is also fenced at prepare.

The database-level fences (migration 0018) complement these gates: every object
needs a binding at commit time, and governed writes require the per-transaction
write-capability GUC carried only by current runtime code. That stops stale
pre-A1 binaries from writing; it is not a security boundary against arbitrary
SQL from the application role.
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from . import db, profile
from .errors import GovernedError


def _fail(code: str, message: str = "") -> None:
    raise GovernedError(code, message, status=409)


# 本进程编译支持的 (protocol_id, contract_version) 全集。登记内容（registry
# 行的 can_write/actions 等）不能扩大该集合：未在此集合中的组合一律
# PROTOCOL_NOT_SUPPORTED，即使控制面登记声称可写。新增支持必须改代码并发布。
SUPPORTED_PROTOCOL_CONTRACTS = frozenset(
    {
        (profile.LEGACY_PROTOCOL_ID, profile.LEGACY_CONTRACT_VERSION),
        (profile.CONTRACT_A_PROTOCOL_ID, profile.CONTRACT_A_CONTRACT_VERSION),
    }
)


# ---------------------------------------------------------------- row readers


def current_binding(conn: Any, scope_id: str, object_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM gov_object_protocol_bindings
           WHERE scope_id=%s AND object_id=%s
           ORDER BY binding_version DESC, recorded_at DESC, binding_id DESC LIMIT 1""",
        (scope_id, object_id),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def installed_profile(conn: Any, scope_id: str, profile_id: str, revision: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM gov_method_profile_revisions
           WHERE scope_id=%s AND profile_id=%s AND revision=%s""",
        (scope_id, profile_id, revision),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def current_registry(conn: Any, scope_id: str, protocol_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM gov_protocol_support_registry
           WHERE scope_id=%s AND protocol_id=%s
           ORDER BY registry_seq DESC, recorded_at DESC, registry_row_id DESC LIMIT 1""",
        (scope_id, protocol_id),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


def creation_policy(conn: Any, scope_id: str, domain_id: str) -> dict[str, Any] | None:
    """Domain policy overrides the scope default; absence of both fails closed."""
    row = conn.execute(
        """SELECT * FROM gov_protocol_policies
           WHERE scope_id=%s AND domain_id=%s
           ORDER BY policy_seq DESC, recorded_at DESC, policy_row_id DESC LIMIT 1""",
        (scope_id, domain_id),
    ).fetchone()
    if row is not None:
        return db.jsonable(row)
    row = conn.execute(
        """SELECT * FROM gov_protocol_policies
           WHERE scope_id=%s AND domain_id IS NULL
           ORDER BY policy_seq DESC, recorded_at DESC, policy_row_id DESC LIMIT 1""",
        (scope_id,),
    ).fetchone()
    return db.jsonable(row) if row is not None else None


# ------------------------------------------------------- strict content models


class ProfileRefSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: StrictStr
    revision: StrictStr


class PolicyContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_protocol: StrictStr
    default_contract_version: StrictStr
    allow_legacy_create: StrictBool
    record_origin: StrictStr
    default_profile_ref: ProfileRefSpec
    experimental: StrictBool
    notes: StrictStr


class RegistryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    can_read: StrictBool
    can_create: StrictBool
    can_write: StrictBool
    evidence_upload: StrictBool
    actions: list[StrictStr]
    object_types: list[StrictStr]
    readonly_compat: list[StrictStr]
    notes: StrictStr


def _policy_content(row: dict[str, Any]) -> PolicyContent:
    try:
        return PolicyContent.model_validate(row["content"])
    except Exception as exc:
        _fail("PROTOCOL_NOT_SUPPORTED", "The registered protocol policy is not interpretable.")
        raise AssertionError from exc  # unreachable


def _registry_content(row: dict[str, Any]) -> RegistryContent:
    try:
        return RegistryContent.model_validate(row["content"])
    except Exception as exc:
        _fail("PROTOCOL_NOT_SUPPORTED", "The protocol support registry entry is not interpretable.")
        raise AssertionError from exc  # unreachable


def _check_binding_profile(conn: Any, scope_id: str, binding: dict[str, Any]) -> None:
    """The bound profile must be installed, hash-matched AND semantically belong
    to the binding's protocol — a correct hash of the wrong profile kind never
    makes a legacy binding out of a Contract-A core, or vice versa."""
    installed = installed_profile(conn, scope_id, binding["profile_id"], binding["profile_revision"])
    if installed is None or installed["canonical_hash"] != binding["profile_canonical_hash"]:
        _fail("METHOD_PROFILE_UNSUPPORTED")
    implied = profile.implied_protocol(installed["schema_version"], installed["content"])
    if implied != (binding["protocol_id"], binding["contract_version"]):
        _fail("METHOD_PROFILE_UNSUPPORTED")


def _check_registry(conn: Any, scope_id: str, protocol_id: str,
                    contract_version: str) -> RegistryContent:
    """Registry row must exist, match the exact contract_version, and name a
    (protocol_id, contract_version) pair compiled into this runtime."""
    if (protocol_id, contract_version) not in SUPPORTED_PROTOCOL_CONTRACTS:
        _fail("PROTOCOL_NOT_SUPPORTED")
    row = current_registry(conn, scope_id, protocol_id)
    if row is None or row["contract_version"] != contract_version:
        _fail("PROTOCOL_NOT_SUPPORTED")
    return _registry_content(row)


def _declared_mismatch(code_for_known_legacy_or_absent: bool, declared: str | None,
                       binding_version: str) -> None:
    """Frozen rejection matrix for a declared contract_version ≠ the binding's."""
    if declared is None or declared == profile.LEGACY_CONTRACT_VERSION:
        if code_for_known_legacy_or_absent:
            _fail("PROTOCOL_UPGRADE_REQUIRED")
    _fail("PROTOCOL_NOT_SUPPORTED")


# ------------------------------------------------------------ write-time gates


def gate_target_action(conn: Any, scope_id: str, target_object_id: str,
                       action_type: str, declared: str | None) -> str:
    """Resolve and gate a targeted action; returns the binding contract_version."""
    binding = current_binding(conn, scope_id, target_object_id)
    if binding is None:
        _fail("PROTOCOL_BINDING_MISSING")
    registry = _check_registry(conn, scope_id, binding["protocol_id"], binding["contract_version"])
    _check_binding_profile(conn, scope_id, binding)
    if binding["protocol_id"] == profile.CONTRACT_A_PROTOCOL_ID:
        if declared != binding["contract_version"]:
            _declared_mismatch(True, declared, binding["contract_version"])
        # A1 registers the Contract-A protocol for metadata/read support only;
        # no business handler exists for any action on Contract-A objects yet.
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    if declared is not None and declared != binding["contract_version"]:
        _declared_mismatch(False, declared, binding["contract_version"])
    if not registry.can_write:
        _fail("PROTOCOL_WRITE_DISABLED")
    if action_type not in registry.actions:
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    return binding["contract_version"]


def gate_dependency(conn: Any, scope_id: str, object_id: str, expected_contract: str) -> None:
    """Every non-target object pulled into a write must share the write's protocol."""
    binding = current_binding(conn, scope_id, object_id)
    if binding is None:
        _fail("PROTOCOL_BINDING_MISSING")
    _check_registry(conn, scope_id, binding["protocol_id"], binding["contract_version"])
    _check_binding_profile(conn, scope_id, binding)
    if binding["contract_version"] != expected_contract:
        _fail("PROTOCOL_BINDING_CONFLICT")


def resolve_creation(conn: Any, scope_id: str, domain_id: str, object_type: str,
                     declared: str | None, *, for_evidence: bool = False) -> dict[str, Any]:
    """Resolve the server-side creation registration for a new object.

    Returns the binding fields to persist with the new object. Never trusts a
    client-selected protocol; unregistered scopes/domains fail closed.
    """
    policy_row = creation_policy(conn, scope_id, domain_id)
    if policy_row is None:
        _fail("PROTOCOL_POLICY_MISSING")
    policy = _policy_content(policy_row)
    if (policy.default_protocol, policy.default_contract_version) not in SUPPORTED_PROTOCOL_CONTRACTS:
        _fail("PROTOCOL_NOT_SUPPORTED")
    if policy.default_protocol == profile.CONTRACT_A_PROTOCOL_ID:
        if declared != policy.default_contract_version and not for_evidence:
            _declared_mismatch(True, declared, policy.default_contract_version)
        if not for_evidence:
            # Contract-A object creation requires A2/A3 handlers, not in A1.
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    else:
        if policy.default_protocol != profile.LEGACY_PROTOCOL_ID:
            _fail("PROTOCOL_NOT_SUPPORTED")
        if declared is not None and declared != policy.default_contract_version:
            _declared_mismatch(False, declared, policy.default_contract_version)
        if not policy.allow_legacy_create:
            _fail("PROTOCOL_WRITE_DISABLED")
    registry = _check_registry(conn, scope_id, policy.default_protocol,
                               policy.default_contract_version)
    if for_evidence:
        if not registry.evidence_upload:
            _fail("PROTOCOL_WRITE_DISABLED")
    else:
        if not registry.can_create:
            _fail("PROTOCOL_WRITE_DISABLED")
        if object_type not in registry.object_types:
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    installed = installed_profile(conn, scope_id, policy.default_profile_ref.profile_id,
                                  policy.default_profile_ref.revision)
    if installed is None:
        _fail("METHOD_PROFILE_UNSUPPORTED")
    implied = profile.implied_protocol(installed["schema_version"], installed["content"])
    if implied != (policy.default_protocol, policy.default_contract_version):
        # The policy's default profile must semantically belong to its protocol;
        # a correct hash of the wrong profile kind is rejected here too.
        _fail("METHOD_PROFILE_UNSUPPORTED")
    return {
        "protocol_id": policy.default_protocol,
        "contract_version": policy.default_contract_version,
        "profile_id": installed["profile_id"],
        "profile_revision": installed["revision"],
        "profile_canonical_hash": installed["canonical_hash"],
        "record_origin": policy.record_origin,
    }


def insert_binding(conn: Any, scope_id: str, object_id: str, fields: dict[str, Any],
                   *, registered_by: str, receipt_id: str | None = None,
                   detail: dict[str, Any] | None = None) -> str:
    """Append the initial (version 1) binding for a newly created object."""
    row = conn.execute(
        """INSERT INTO gov_object_protocol_bindings
           (scope_id, object_id, binding_version, protocol_id, contract_version,
            profile_id, profile_revision, profile_canonical_hash, record_origin,
            run_id, registered_by, receipt_id, detail)
           VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s)
           RETURNING binding_id""",
        (scope_id, object_id, fields["protocol_id"], fields["contract_version"],
         fields["profile_id"], fields["profile_revision"], fields["profile_canonical_hash"],
         fields["record_origin"], registered_by, receipt_id, Jsonb(detail or {})),
    ).fetchone()
    return str(row["binding_id"])


def gate_effect_dispatch(conn: Any, scope_id: str, object_id: str, action_type: str) -> None:
    """Re-check, for every object named by the immutable receipt, that the
    current binding is still the supported legacy protocol whose registry still
    lists the receipt's action — evaluated immediately before any external HTTP
    effect is sent.

    Queue payload declarations are never trusted here; a task claimed by a
    stale binary or enqueued with forged fields cannot turn into an effect.
    Withdrawing a single action from the registry stops already-queued
    dispatches of that action just as the global can_write switch does.
    """
    binding = current_binding(conn, scope_id, object_id)
    if binding is None:
        _fail("PROTOCOL_BINDING_MISSING")
    registry = _check_registry(conn, scope_id, binding["protocol_id"], binding["contract_version"])
    _check_binding_profile(conn, scope_id, binding)
    if binding["protocol_id"] != profile.LEGACY_PROTOCOL_ID:
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    if not registry.can_write:
        _fail("PROTOCOL_WRITE_DISABLED")
    if action_type not in registry.actions:
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")


def inherit_binding(conn: Any, scope_id: str, object_id: str, source_object_id: str,
                    *, registered_by: str, receipt_id: str | None = None) -> str:
    """Bind a server-created child object (e.g. Deliverable) to its parent's protocol."""
    source = current_binding(conn, scope_id, source_object_id)
    if source is None:
        _fail("PROTOCOL_BINDING_MISSING")
    return insert_binding(
        conn, scope_id, object_id,
        {key: source[key] for key in ("protocol_id", "contract_version", "profile_id",
                                      "profile_revision", "profile_canonical_hash",
                                      "record_origin")},
        registered_by=registered_by, receipt_id=receipt_id,
        detail={"inherited_from_object_id": source_object_id},
    )


# ------------------------------------------------------------------ read paths


def _binding_interpretation(installed: dict[str, Any] | None,
                            registry_row: dict[str, Any] | None,
                            binding: dict[str, Any]) -> tuple[str, str]:
    """(interpretation_status, note) for a binding under current registrations."""
    profile_ok = (
        installed is not None
        and installed["canonical_hash"] == binding["profile_canonical_hash"]
        and profile.implied_protocol(installed["schema_version"], installed["content"])
            == (binding["protocol_id"], binding["contract_version"])
    )
    read_supported = False
    if registry_row is not None and registry_row["contract_version"] == binding["contract_version"]:
        try:
            registry = _registry_content(registry_row)
        except GovernedError:
            registry = None
        if registry is not None:
            read_supported = (
                registry.can_read
                and binding["contract_version"] in registry.readonly_compat
                and (binding["protocol_id"], binding["contract_version"]) in SUPPORTED_PROTOCOL_CONTRACTS
            )
    if not profile_ok:
        return "profile_unsupported", "The bound method profile is not installed; interpretation unsupported."
    if not read_supported:
        return "read_unsupported", ("The current support registry does not grant read interpretation "
                                    "for this protocol/contract version; no legacy meaning is attached.")
    if binding["protocol_id"] == profile.LEGACY_PROTOCOL_ID:
        return "legacy_v0_2", "Legacy v0.2 semantics; dri_assignment fields keep their original MISSION_DRI meaning."
    if binding["protocol_id"] == profile.CONTRACT_A_PROTOCOL_ID:
        return "contract_a_metadata_read_only", (
            "Contract-A registration. A1 exposes metadata/read support only; "
            "no composition, IC handover or delivery interpretation is attached "
            "and legacy DRI fields are not reinterpreted.")
    return "unsupported_protocol", "The registered protocol is not interpreted by this runtime."


def _metadata_dict(binding: dict[str, Any] | None, status: str | None, note: str | None) -> dict[str, Any]:
    if binding is None:
        return {
            "registration_status": "unregistered",
            "interpretation_status": "unsupported_unregistered",
            "protocol_id": None,
            "contract_version": None,
            "method_profile_ref": None,
            "binding_version": None,
            "record_origin": None,
            "note": "No server-side protocol registration exists for this object; "
                    "it is not interpreted as legacy by default.",
        }
    return {
        "registration_status": "registered",
        "interpretation_status": status,
        "protocol_id": binding["protocol_id"],
        "contract_version": binding["contract_version"],
        "method_profile_ref": {
            "profile_id": binding["profile_id"],
            "revision": binding["profile_revision"],
            "canonical_hash": binding["profile_canonical_hash"],
        },
        "binding_version": binding["binding_version"],
        "record_origin": binding["record_origin"],
        "note": note,
    }


def read_metadata(conn: Any, scope_id: str, object_id: str) -> dict[str, Any]:
    """Explicit protocol metadata for authorized reads; absence is marked, not guessed.

    Interpretation is only reported when the current support registry row exists,
    matches the binding's exact contract_version, and grants read support
    (can_read plus readonly_compat membership). Otherwise the metadata states
    the unsupported status explicitly instead of implying legacy meaning.
    """
    binding = current_binding(conn, scope_id, object_id)
    if binding is None:
        return _metadata_dict(None, None, None)
    installed = installed_profile(conn, scope_id, binding["profile_id"], binding["profile_revision"])
    registry_row = current_registry(conn, scope_id, binding["protocol_id"])
    status, note = _binding_interpretation(installed, registry_row, binding)
    return _metadata_dict(binding, status, note)


def list_metadata(conn: Any, scope_id: str, object_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batched read metadata for list projections (one query per registration table)."""
    result: dict[str, dict[str, Any]] = {}
    ids = [str(oid) for oid in dict.fromkeys(object_ids)]
    if not ids:
        return result
    rows = conn.execute(
        """SELECT DISTINCT ON (object_id) * FROM gov_object_protocol_bindings
           WHERE scope_id=%s AND object_id=ANY(%s::uuid[])
           ORDER BY object_id, binding_version DESC, recorded_at DESC, binding_id DESC""",
        (scope_id, ids),
    ).fetchall()
    bindings = {str(row["object_id"]): db.jsonable(row) for row in rows}
    profiles: dict[tuple[str, str], dict[str, Any] | None] = {}
    registries: dict[str, dict[str, Any] | None] = {}
    for oid in ids:
        binding = bindings.get(oid)
        if binding is None:
            result[oid] = _metadata_dict(None, None, None)
            continue
        pkey = (binding["profile_id"], binding["profile_revision"])
        if pkey not in profiles:
            profiles[pkey] = installed_profile(conn, scope_id, *pkey)
        if binding["protocol_id"] not in registries:
            registries[binding["protocol_id"]] = current_registry(conn, scope_id, binding["protocol_id"])
        status, note = _binding_interpretation(profiles[pkey], registries[binding["protocol_id"]], binding)
        result[oid] = _metadata_dict(binding, status, note)
    return result


def require_read_support(conn: Any, scope_id: str, object_id: str) -> dict[str, Any]:
    """Hard read gate for paths with external side effects (object-store fetches)
    or legacy interpretation: returns metadata only when the binding exists and
    the current registry grants read support; otherwise PROTOCOL_NOT_SUPPORTED.
    """
    metadata = read_metadata(conn, scope_id, object_id)
    if metadata["registration_status"] != "registered" or metadata["interpretation_status"] not in (
            "legacy_v0_2", "contract_a_metadata_read_only"):
        _fail("PROTOCOL_NOT_SUPPORTED",
              "The object's protocol registration does not support read interpretation.")
    return metadata


def require_legacy_read_support(conn: Any, scope_id: str, object_id: str) -> dict[str, Any]:
    """Hard gate for LEGACY BUSINESS INTERPRETATION of derived reads (B10).

    Calls require_read_support first, so a missing binding or withdrawn read
    support keeps its original error; on top of that the current
    interpretation identity must still be exactly legacy_v0_2.  An object that
    became readable Contract-A metadata (e.g. a control-plane rebind after the
    fact) is rejected with PROTOCOL_NOT_SUPPORTED 409 instead of being served
    under legacy meaning.

    This gates only legacy interpretation consumers (delivery reviews, outcome
    assessments, stored snapshot selected items).  It must NOT be applied to
    raw object/revision metadata GETs, immutable receipts, general
    source_reference edges, or evidence originals read as Contract-A metadata.
    Callers keep their existing db.object_row/db.revision_row authorization
    before this helper.
    """
    metadata = require_read_support(conn, scope_id, object_id)
    if metadata["interpretation_status"] != "legacy_v0_2":
        _fail("PROTOCOL_NOT_SUPPORTED",
              "The object's current protocol registration no longer supports legacy interpretation.")
    return metadata


def evidence_protocol_fields(conn: Any, scope_id: str, domain_id: str) -> dict[str, Any]:
    """Pre-upload registration check; runs before any object-store write."""
    return resolve_creation(conn, scope_id, domain_id, "EvidenceAsset", None, for_evidence=True)
