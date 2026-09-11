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
from .models import (
    A2_GENERIC_SOURCE_OBJECT_TYPES,
    A2_OBJECT_TYPE_NAMES,
)
from .a3_models import A3_GENERIC_CREATE_TYPES, A3_GENERIC_REVISE_TYPES


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


# A2 action -> target object_type allowlist. ``propose_revision`` is allowed on
# the two A2 source types only; the other four A2 action names are bound to a
# single target type. ``open_formation_round`` has no target and is handled by
# resolve_creation() instead.
A2_TARGET_OBJECT_TYPES: dict[str, frozenset[str]] = {
    "amend_formation_round": frozenset({"FormationRound"}),
    "publish_domain_submission": frozenset({"FormationRound"}),
    "form_company_composition": frozenset({"FormationRound"}),
    "confirm_company_composition": frozenset({"CompanyComposition"}),
    "activate_company_composition": frozenset({"CompanyComposition"}),
    # propose_revision only ever lands on the two source types (A2_ACTIONS-bound
    # objects cannot be rewritten through this generic path).
    "propose_revision": frozenset(A2_GENERIC_SOURCE_OBJECT_TYPES),
}

# A3 action -> target object_type allowlist (docs/runtime-a3-api.md).  The A3
# delivery loop reuses the legacy action NAMES on Contract-A-bound targets:
# accept/activate target an ExecutionCommitment, the delivery loop targets a
# WorkItem, record_outcome_assessment targets the adopted CompanyReference,
# and A3 propose_revision is limited to ExecutionCommitment/ExecutionPlan
# (existing WorkItem/Deliverable generic revision is prohibited).  The A2 maps
# above are unchanged: an A2-only registry never gains A3 support because the
# registry actions/object_types membership checks below still apply.
A3_TARGET_OBJECT_TYPES: dict[str, frozenset[str]] = {
    "accept_commitment": frozenset({"ExecutionCommitment"}),
    "activate_commitment": frozenset({"ExecutionCommitment"}),
    "accept_work_item": frozenset({"WorkItem"}),
    "submit_deliverable": frozenset({"WorkItem"}),
    "review_deliverable": frozenset({"WorkItem"}),
    "record_outcome_assessment": frozenset({"CompanyReference"}),
    "propose_revision": frozenset(A3_GENERIC_REVISE_TYPES),
}

# Object types whose Contract-A binding is interpreted with A3 execution
# semantics by the read paths (engineering section 6).  Deliverable joins the
# set because A3 submissions are Deliverable objects under a Contract-A
# binding inherited from their WorkItem.
A3_EXECUTION_OBJECT_TYPES = frozenset({
    "ExecutionCommitment", "ExecutionPlan", "WorkItem", "Deliverable",
})


def gate_target_action(conn: Any, scope_id: str, target_object_id: str,
                       action_type: str, declared: str | None) -> str:
    """Resolve and gate a targeted action; returns the binding contract_version.

    Contract-A support requires explicit registry installation and per-action
    target-type matching: amend/publish/form must land on a FormationRound,
    confirm/activate must land on a CompanyComposition, propose_revision is
    allowed only on the two A2 source types. All other A2 actions still fail
    with ACTION_NOT_SUPPORTED_FOR_PROTOCOL.
    """
    binding = current_binding(conn, scope_id, target_object_id)
    if binding is None:
        _fail("PROTOCOL_BINDING_MISSING")
    registry = _check_registry(conn, scope_id, binding["protocol_id"], binding["contract_version"])
    _check_binding_profile(conn, scope_id, binding)
    if binding["protocol_id"] == profile.CONTRACT_A_PROTOCOL_ID:
        if declared != binding["contract_version"]:
            _declared_mismatch(True, declared, binding["contract_version"])
        # Look up the actual target object type so we can match A2 action
        # allowlists against authoritative state. A2 targets are read here,
        # which the registry's read support gate already guards.  When the
        # type cannot be resolved (e.g. the object does not exist, or the
        # protocol does not name the object_type), the A2 allowlist is
        # considered unmatched — the legacy test matrix expected exactly
        # ACTION_NOT_SUPPORTED_FOR_PROTOCOL here, never a leaked NOT_FOUND.
        try:
            target_row = conn.execute(
                "SELECT object_type FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                (scope_id, target_object_id),
            ).fetchone()
        except Exception:
            target_row = None
        target_object_type = str(target_row["object_type"]) if target_row is not None else None
        # Union the compiled A2 and A3 target sets (review: propose_revision
        # exists in both and must accept A2 sources AND A3 EC/Plan).  The
        # installed registry's explicit actions/object_types membership below
        # still narrows the result, so an A2-only registry keeps rejecting
        # every A3 execution type.
        allowed_targets = (A2_TARGET_OBJECT_TYPES.get(action_type, frozenset())
                           | A3_TARGET_OBJECT_TYPES.get(action_type, frozenset()))
        if not allowed_targets:
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
        if target_object_type is None or target_object_type not in allowed_targets:
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
        if action_type not in registry.actions:
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
        if target_object_type not in registry.object_types:
            # Per-object support is explicit in the installed registry: an
            # A2-only registry never carries the A3 execution types, so the
            # original A2 registry keeps behaving as A2 only.
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
        if not registry.can_write:
            _fail("PROTOCOL_WRITE_DISABLED")
        if not registry.can_create and action_type == "propose_revision":
            # propose_revision on A2 sources needs can_create as well as
            # can_write per the explicit "new creation requires can_create AND
            # can_write AND action membership" rule.
            _fail("PROTOCOL_WRITE_DISABLED")
        return binding["contract_version"]
    if declared is not None and declared != binding["contract_version"]:
        _declared_mismatch(False, declared, binding["contract_version"])
    if not registry.can_write:
        _fail("PROTOCOL_WRITE_DISABLED")
    if action_type not in registry.actions:
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
    return binding["contract_version"]


def gate_dependency(conn: Any, scope_id: str, object_id: str, expected_contract: str) -> None:
    """Every non-target object pulled into a write must share the write's protocol.

    A2 source objects (CompanyReference / CapacityObservation) participate in
    the same authoritative dependency gate as legacy objects: a registered A2
    source binding plus matching profile and registry are required, but the
    declared contract_version is never trusted as authority.
    """
    binding = current_binding(conn, scope_id, object_id)
    if binding is None:
        _fail("PROTOCOL_BINDING_MISSING")
    _check_registry(conn, scope_id, binding["protocol_id"], binding["contract_version"])
    _check_binding_profile(conn, scope_id, binding)
    if binding["contract_version"] != expected_contract:
        _fail("PROTOCOL_BINDING_CONFLICT")


# Creation path policy for Contract-A. ``open_formation_round`` is the only
# A2 action that creates a non-derived object via the same policy lookup;
# everything else is derived by A2 handlers and must go through
# ``inherit_binding``.
A2_GENERIC_CREATABLE = frozenset(A2_GENERIC_SOURCE_OBJECT_TYPES)
A2_INTERNAL_CREATABLE = frozenset({"FormationRound"})
A2_DERIVED_TYPES = frozenset({"DomainSubmission", "CompanyComposition",
                              "Mission", "DomainCommitment"})

# A3 generic create_object allowlist (docs/runtime-a3-engineering.md §2):
# ExecutionCommitment drafts, restricted ExecutionPlan, and WorkItem under an
# existing execution authority.  Mission stays A2-controlled; existing
# WorkItem/Deliverable generic revision is prohibited, so A3 propose_revision
# only covers ExecutionCommitment/ExecutionPlan (A3_GENERIC_REVISE_TYPES).
A3_GENERIC_CREATABLE = frozenset(A3_GENERIC_CREATE_TYPES)


def resolve_creation(conn: Any, scope_id: str, domain_id: str, object_type: str,
                     declared: str | None, *, for_evidence: bool = False,
                     action_type: str | None = None) -> dict[str, Any]:
    """Resolve the server-side creation registration for a new object.

    Returns the binding fields to persist with the new object. Never trusts a
    client-selected protocol; unregistered scopes/domains fail closed.

    ``action_type`` lets the handler specify the A2 action name driving the
    creation (``open_formation_round``, ``create_object``, ``propose_revision``
    for source types).  ``for_evidence`` is the legacy EvidenceAsset upload
    shortcut and is not used as a bypass by A2 paths.
    """
    policy_row = creation_policy(conn, scope_id, domain_id)
    if policy_row is None:
        _fail("PROTOCOL_POLICY_MISSING")
    policy = _policy_content(policy_row)
    if (policy.default_protocol, policy.default_contract_version) not in SUPPORTED_PROTOCOL_CONTRACTS:
        _fail("PROTOCOL_NOT_SUPPORTED")
    if policy.default_protocol == profile.CONTRACT_A_PROTOCOL_ID:
        if for_evidence:
            # EvidenceAsset pre-upload check stays strictly legacy-shaped:
            # metadata-level, accepted only when the Contract-A registry
            # reports evidence_upload=True.  No A2 path uses this shortcut;
            # business creation still rejects WorkItem et al.
            pass
        else:
            if declared != policy.default_contract_version:
                _declared_mismatch(True, declared, policy.default_contract_version)
        registry = _check_registry(conn, scope_id, policy.default_protocol,
                                   policy.default_contract_version)
        if for_evidence:
            if not registry.evidence_upload:
                _fail("PROTOCOL_WRITE_DISABLED")
        else:
            # Compile-time A2 allowlist. Generic create_object on Contract-A is
            # limited to the two source types. open_formation_round creates only
            # FormationRound. Other derived A2 types must call inherit_binding()
            # from their handler instead of resolve_creation().  A3 adds its
            # three generic-creatable execution types; both allowlists are then
            # narrowed by the installed registry's explicit object_types.
            if action_type in (None, "create_object"):
                if object_type not in A2_GENERIC_CREATABLE and object_type not in A3_GENERIC_CREATABLE:
                    _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            elif action_type == "open_formation_round":
                if object_type not in A2_INTERNAL_CREATABLE:
                    _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            else:
                _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            if object_type in A3_GENERIC_CREATABLE and (
                    object_type not in registry.object_types
                    or (action_type or "create_object") not in registry.actions):
                _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            if not registry.can_create or not registry.can_write:
                _fail("PROTOCOL_WRITE_DISABLED")
            # Action membership: create_object/open_formation_round must be
            # listed in the compiled registry actions.
            if (action_type or "create_object") not in registry.actions:
                _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            if object_type not in registry.object_types:
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

# Accepted interpretation statuses for require_read_support.  Legacy v0.2 and
# the existing Contract-A metadata/read-only status stay accepted verbatim.
# A new "contract_a_v0_1" status reports A2-aware read support: the current
# registry grants both read and write to Contract-A, the bound profile is
# installed and hash-matched, and the object is one of the A2 types.
# "contract_a_a3_execution" reports A3 execution-handover read support for
# ExecutionCommitment/ExecutionPlan/WorkItem/Deliverable objects under a
# writable Contract-A registry that explicitly lists the object type.
CONTRACT_A_READ_STATUSES = ("legacy_v0_2", "contract_a_metadata_read_only", "contract_a_v0_1",
                            "contract_a_a3_execution")


def _binding_interpretation(installed: dict[str, Any] | None,
                            registry_row: dict[str, Any] | None,
                            binding: dict[str, Any]) -> tuple[str, str]:
    """(interpretation_status, note) for a binding under current registrations.

    The new ``contract_a_v0_1`` status fires only when the current registry
    grants both read and write to Contract-A.  Resolving the actual
    object_type from the binding requires a DB lookup; callers that have a
    live connection must pass it through ``_binding_interpretation_with_conn``;
    legacy callers without a connection keep the existing A1 readonly
    metadata label.
    """
    profile_ok = (
        installed is not None
        and installed["canonical_hash"] == binding["profile_canonical_hash"]
        and profile.implied_protocol(installed["schema_version"], installed["content"])
            == (binding["protocol_id"], binding["contract_version"])
    )
    read_supported = False
    registry = None
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


def _binding_interpretation_with_conn(conn: Any,
                                      installed: dict[str, Any] | None,
                                      registry_row: dict[str, Any] | None,
                                      binding: dict[str, Any]) -> tuple[str, str]:
    """A2-aware variant: looks up the actual object_type to distinguish the
    new ``contract_a_v0_1`` status (any of the seven A2 object types under an
    explicitly writable Contract-A registry) from the existing
    ``contract_a_metadata_read_only`` label (everything else).  The object
    lookup SQL runs unguarded: a database error must never be downgraded
    into metadata output.
    """
    base_status, base_note = _binding_interpretation(installed, registry_row, binding)
    if base_status != "contract_a_metadata_read_only":
        return base_status, base_note
    registry = None
    if registry_row is not None and registry_row["contract_version"] == binding["contract_version"]:
        try:
            registry = _registry_content(registry_row)
        except GovernedError:
            registry = None
    if registry is None or not registry.can_write:
        return base_status, base_note
    row = conn.execute(
        "SELECT object_type FROM gov_objects WHERE scope_id=%s AND object_id=%s",
        (binding["scope_id"], binding["object_id"]),
    ).fetchone()
    object_type = str(row["object_type"]) if row is not None else None
    if object_type in A2_OBJECT_TYPE_NAMES:
        return "contract_a_v0_1", (
            "Contract-A v0.1 registration with active A2 read support. "
            "Reads of all seven A2 object types project through the A2 readers; "
            "legacy DRI fields are not reinterpreted.")
    if object_type in A3_EXECUTION_OBJECT_TYPES and object_type in registry.object_types:
        return "contract_a_a3_execution", (
            "Contract-A v0.1 registration with active A3 execution-handover read "
            "support. ExecutionCommitment/ExecutionPlan/WorkItem/Deliverable reads "
            "project authority, appointment, plan and receipt state through the A3 "
            "readers; legacy DRI fields are not reinterpreted.")
    return base_status, base_note


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
    status, note = _binding_interpretation_with_conn(conn, installed, registry_row, binding)
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
        status, note = _binding_interpretation_with_conn(conn, profiles[pkey],
                                                          registries[binding["protocol_id"]], binding)
        result[oid] = _metadata_dict(binding, status, note)
    return result


def require_read_support(conn: Any, scope_id: str, object_id: str) -> dict[str, Any]:
    """Hard read gate for paths with external side effects (object-store fetches)
    or legacy interpretation: returns metadata only when the binding exists and
    the current registry grants read support; otherwise PROTOCOL_NOT_SUPPORTED.
    """
    metadata = read_metadata(conn, scope_id, object_id)
    # Accepted statuses now include the A2-aware contract_a_v0_1 label for
    # any of the seven A2 object types when the registry grants write, and
    # the A3-aware contract_a_a3_execution label for the A3 execution object
    # types under a registry that explicitly lists them.
    # Legacy v0.2 and the existing A1 readonly label remain accepted verbatim.
    if metadata["registration_status"] != "registered" or metadata["interpretation_status"] not in (
            "legacy_v0_2", "contract_a_metadata_read_only", "contract_a_v0_1",
            "contract_a_a3_execution"):
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
