"""Contract-A (A3) execution-handover read-side projections.

This module is the read half of the A3 DRI-IC execution handover defined by
docs/runtime-a3-engineering.md and physically recorded by migration
0020_execution_handover.sql.  It owns ONLY reads; every write, replay and
current-authority validation belongs to the A3 service/governance modules,
which this module never imports.

Fixed public entry points (frozen for the core development job):

- :func:`is_a3_object` — internal routing helper.  ``True`` only for
  ExecutionCommitment / WorkItem / ExecutionPlan / Deliverable objects whose
  CURRENT binding is Contract-A.  Legacy objects with the same type names
  return ``False``.  Raw scoped loads only; never exposed via a route.

- :func:`is_a3_receipt` — internal receipt classifier.  ``True`` only when
  the immutable receipt's real scoped target/versions, server-recorded
  action_type, object type and current binding prove A3 execution semantics.
  ``record_outcome_assessment`` counts as A3 only when its real target is a
  Contract-A bound CompanyReference.  No client-supplied marker participates
  in classification.  Callers must evaluate this BEFORE the generic A2
  receipt path, because A3 receipts legitimately reference A2 material (the
  Mission / DomainCommitment an ExecutionCommitment cites).

- :func:`object_state` — authorized head + protocol metadata + latest and
  effective revisions + ``a3_projection`` of the exact independent A3 state:
  ExecutionCommitment exposes its ExecutionAuthority / current epoch versus
  AcceptanceAppointment / current version; WorkItem exposes its reception
  record, current plan pointer, submissions and reviews; ExecutionPlan and
  Deliverable expose their trace back to the governing WorkItem /
  ExecutionCommitment.  Legacy fields are never reinterpreted.

- :func:`revision` — authorized read of one exact immutable revision.

- :func:`authorize_receipt` — current domain/source read rights for every
  referenced object/revision exposed by an A3 receipt.  It does NOT require
  the reader to be a CEO/DRI Round signer merely because the receipt's
  commitment refers to an A2 Mission, and it never impersonates the
  historical actor.  Read authorization is deliberately separate from
  write/replay current-authority validation (owned by A3Execution).

- :func:`relations` — workbench-compatible relations view
  (``{"source_ref", "items", "next_cursor", "protocol"}``).  Only currently
  visible own-responsibility and source endpoints are surfaced; hidden
  CompanyComposition / Round / other-domain material and hidden counts are
  never returned.  Same-domain targets authorize via ``db``; explicitly
  shared CompanyReference / CapacityObservation targets authorize via
  ``a2_readers.visible_object`` / ``visible_revision`` (which already
  implement the source grants).

- :func:`project_outcome` — A3 outcome-assessment projection for a
  Contract-A bound CompanyReference.  Returns ``None`` when the target is
  not applicable or not currently readable.  An assessment is shown only
  when its exact target revision, every observation/evidence revision and
  every referenced A3 review source are currently readable; otherwise the
  assessment is omitted whole (its evidence IDs and counts never leak), and
  no older visible assessment is ever labeled the authoritative latest while
  a later unreadable assessment exists.

The module never mutates business state, never creates owner sessions, never
touches ``app.gov_control_plane``, grants or actor synthesis.  All SQL is
scoped by ``ctx.scope_id`` on the ordinary application connection.  Errors
are the fixed public ``GovernedError`` codes; no raw private payloads appear
in error messages.
"""
from __future__ import annotations

from typing import Any

from memory_service_runtime.governed import db, delivery, protocol
from memory_service_runtime.governed.a2_models import A2_OBJECT_TYPES, A2_SOURCE_TYPES
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.profile import CONTRACT_A_PROTOCOL_ID
from memory_service_runtime.governed.protocol import (
    A3_EXECUTION_OBJECT_TYPES,
    CONTRACT_A_READ_STATUSES,
)

__all__ = [
    "is_a3_object", "is_a3_receipt", "object_state", "revision",
    "authorize_receipt", "relations", "project_outcome",
]

# Targeted actions an A3 execution receipt can carry.  accept_commitment /
# activate_commitment / propose_revision / create_object and the three
# delivery-loop names also exist under legacy semantics, so the action name
# alone never classifies a receipt — the real target/binding/type does.
_A3_EXECUTION_ACTIONS = frozenset({
    "create_object", "propose_revision", "accept_commitment",
    "activate_commitment", "accept_work_item", "submit_deliverable",
    "review_deliverable",
})
_A3_OUTCOME_ACTION = "record_outcome_assessment"

_RECEIPT_NOT_FOUND = ("NOT_FOUND", "Receipt was not found", 404)


# ---------------------------------------------------------------------------
# raw scoped row helpers (no authorization — used by classification and by
# the access-policy logic only)
# ---------------------------------------------------------------------------


def _row(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    return conn.execute(sql, params).fetchone()


def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return list(conn.execute(sql, params).fetchall())


def _uuid_str(value: Any) -> str | None:
    try:
        from uuid import UUID
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _load_head(conn: Any, scope_id: str, oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:head*/ * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
        (scope_id, oid),
    )


def _load_revision(conn: Any, scope_id: str, oid: str, rid: str) -> dict[str, Any] | None:
    rid_uuid = _uuid_str(rid)
    if rid_uuid is None:
        return None
    return _row(
        conn,
        "SELECT /*a3:revision*/ * FROM gov_object_revisions"
        " WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
        (scope_id, oid, rid_uuid),
    )


def _revision_owner(conn: Any, scope_id: str, revision_id: Any) -> str | None:
    rid = _uuid_str(revision_id)
    if rid is None:
        return None
    row = _row(
        conn,
        "SELECT /*a3:revision-owner*/ object_id FROM gov_object_revisions"
        " WHERE scope_id=%s AND revision_id=%s",
        (scope_id, rid),
    )
    return str(row["object_id"]) if row else None


def _binding_is_contract_a(conn: Any, scope_id: str, oid: str) -> bool:
    binding = protocol.current_binding(conn, scope_id, oid)
    return binding is not None and binding.get("protocol_id") == CONTRACT_A_PROTOCOL_ID


# ---------------------------------------------------------------------------
# internal routing helpers
# ---------------------------------------------------------------------------


def is_a3_object(conn: Any, ctx: Any, object_id: str) -> bool:
    """``True`` iff the object is one of the four A3 execution types AND its
    current binding is Contract-A.

    Legacy / unregistered / foreign-scope objects and legacy objects that
    merely share the ExecutionCommitment / WorkItem / Deliverable type names
    return ``False`` without raising.  Raw existence is never exposed.
    """
    oid = _uuid_str(object_id)
    if oid is None:
        return False
    head = _load_head(conn, ctx.scope_id, oid)
    if head is None or head.get("object_type") not in A3_EXECUTION_OBJECT_TYPES:
        return False
    return _binding_is_contract_a(conn, ctx.scope_id, oid)


def _receipt_candidate_ids(receipt: dict[str, Any]) -> list[str]:
    """Every object id the immutable receipt exposes, in stable order."""
    ids: list[str] = []
    seen: set[str] = set()

    def _take(value: Any) -> None:
        oid = _uuid_str(value)
        if oid is not None and oid not in seen:
            seen.add(oid)
            ids.append(oid)

    _take(receipt.get("target_object_id"))
    for item in receipt.get("object_versions") or []:
        if isinstance(item, dict):
            _take(item.get("object_id"))
    result = receipt.get("result")
    if isinstance(result, dict):
        for item in result.get("referenced_object_ids") or []:
            _take(item)
    return ids


def is_a3_receipt(conn: Any, ctx: Any, receipt: dict[str, Any]) -> bool:
    """Classify an immutable receipt as A3 by real scoped state.

    ``True`` only when the server-recorded action_type can carry A3
    execution semantics AND the receipt's real target/versions resolve to
    in-scope objects of the four A3 execution types currently bound to
    Contract-A — or, for ``record_outcome_assessment``, when the real target
    is a Contract-A bound CompanyReference.  Legacy receipts with the same
    action names stay legacy; A2 composition receipts stay A2.  Raw scoped
    reads only; classification never depends on client markers and never
    raises on missing/foreign objects.
    """
    action_type = receipt.get("action_type")
    if action_type == _A3_OUTCOME_ACTION:
        target = _uuid_str(receipt.get("target_object_id"))
        if target is None:
            return False
        head = _load_head(conn, ctx.scope_id, target)
        if head is None or head.get("object_type") != "CompanyReference":
            return False
        return _binding_is_contract_a(conn, ctx.scope_id, target)
    if action_type not in _A3_EXECUTION_ACTIONS:
        return False
    for oid in _receipt_candidate_ids(receipt):
        head = _load_head(conn, ctx.scope_id, oid)
        if head is None or head.get("object_type") not in A3_EXECUTION_OBJECT_TYPES:
            continue
        if _binding_is_contract_a(conn, ctx.scope_id, oid):
            return True
    return False


# ---------------------------------------------------------------------------
# A3 record readers (raw scoped; callers hold the authorization)
# ---------------------------------------------------------------------------


def _execution_state(conn: Any, scope_id: str, commitment_oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:execution-state*/ * FROM gov_execution_state"
        " WHERE scope_id=%s AND commitment_object_id=%s",
        (scope_id, commitment_oid),
    )


def _authority_row(conn: Any, scope_id: str, authority_id: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:authority*/ * FROM gov_execution_authorities"
        " WHERE scope_id=%s AND authority_id=%s",
        (scope_id, authority_id),
    )


def _appointment_row(conn: Any, scope_id: str, appointment_id: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:appointment*/ * FROM gov_acceptance_appointments"
        " WHERE scope_id=%s AND appointment_id=%s",
        (scope_id, appointment_id),
    )


def _work_item_state(conn: Any, scope_id: str, work_item_oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:wi-state*/ * FROM gov_a3_work_item_state"
        " WHERE scope_id=%s AND object_id=%s",
        (scope_id, work_item_oid),
    )


def _work_item_state_by_plan(conn: Any, scope_id: str, plan_oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:wi-state-by-plan*/ * FROM gov_a3_work_item_state"
        " WHERE scope_id=%s AND plan_object_id=%s",
        (scope_id, plan_oid),
    )


def _work_item_state_by_deliverable(conn: Any, scope_id: str,
                                    deliverable_oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:wi-state-by-deliverable*/ * FROM gov_a3_work_item_state"
        " WHERE scope_id=%s AND deliverable_object_id=%s",
        (scope_id, deliverable_oid),
    )


def _work_items_by_commitment(conn: Any, scope_id: str,
                              commitment_oid: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT /*a3:wi-by-commitment*/ * FROM gov_a3_work_item_state"
        " WHERE scope_id=%s AND commitment_object_id=%s ORDER BY object_id",
        (scope_id, commitment_oid),
    )


def _reception_row(conn: Any, scope_id: str, work_item_oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:reception*/ * FROM gov_work_receipts"
        " WHERE scope_id=%s AND work_item_object_id=%s",
        (scope_id, work_item_oid),
    )


def _reviews_by_work_item(conn: Any, scope_id: str, work_item_oid: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT /*a3:reviews-by-work-item*/ * FROM gov_a3_delivery_acceptances"
        " WHERE scope_id=%s AND work_item_object_id=%s"
        " ORDER BY recorded_at, acceptance_id",
        (scope_id, work_item_oid),
    )


def _reviews_by_deliverable(conn: Any, scope_id: str,
                            deliverable_oid: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT /*a3:reviews-by-deliverable*/ * FROM gov_a3_delivery_acceptances"
        " WHERE scope_id=%s AND deliverable_object_id=%s"
        " ORDER BY recorded_at, acceptance_id",
        (scope_id, deliverable_oid),
    )


def _review_row(conn: Any, scope_id: str, acceptance_id: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT /*a3:review-by-id*/ * FROM gov_a3_delivery_acceptances"
        " WHERE scope_id=%s AND acceptance_id=%s",
        (scope_id, acceptance_id),
    )


def _deliverable_revisions(conn: Any, scope_id: str,
                           deliverable_oid: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT /*a3:deliverable-revisions*/ * FROM gov_object_revisions"
        " WHERE scope_id=%s AND object_id=%s ORDER BY recorded_at, object_version, revision_id",
        (scope_id, deliverable_oid),
    )


def _assessments_by_target(conn: Any, scope_id: str,
                           target_oid: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        "SELECT /*a3:assessments*/ * FROM gov_a3_outcome_assessments"
        " WHERE scope_id=%s AND company_reference_object_id=%s"
        " ORDER BY recorded_at, assessment_id",
        (scope_id, target_oid),
    )


# ---------------------------------------------------------------------------
# object_state / revision projections
# ---------------------------------------------------------------------------


def _require_a3_head(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    """Authorized A3 head read: same-domain current read rights only.

    A3 execution objects are domain-internal responsibility material; the
    bounded A2 cross-domain exceptions deliberately do not apply.  Legacy
    objects with the same type names are never reinterpreted.
    """
    oid = _uuid_str(object_id)
    if oid is None or not is_a3_object(conn, ctx, oid):
        raise GovernedError("NOT_FOUND")
    return db.object_row(conn, ctx, oid)


def _commitment_projection(conn: Any, ctx: Any, oid: str) -> dict[str, Any]:
    """Exact independent state: current authority/epoch vs appointment/version."""
    state = _execution_state(conn, ctx.scope_id, oid)
    authority = None
    appointment = None
    if state is not None:
        if state.get("current_authority_id"):
            authority = _authority_row(conn, ctx.scope_id, str(state["current_authority_id"]))
        if state.get("current_appointment_id"):
            appointment = _appointment_row(conn, ctx.scope_id, str(state["current_appointment_id"]))
    work_items = [
        {"object_id": str(row["object_id"]),
         "work_item_revision_id": str(row["work_item_revision_id"]),
         "authority_id": str(row["authority_id"]),
         "execution_epoch": row["execution_epoch"]}
        for row in _work_items_by_commitment(conn, ctx.scope_id, oid)
    ]
    return {
        "execution_state": db.jsonable(state) if state is not None else None,
        "current_authority": db.jsonable(authority) if authority is not None else None,
        "current_appointment": db.jsonable(appointment) if appointment is not None else None,
        "work_items": db.jsonable(work_items),
    }


def _work_item_projection(conn: Any, ctx: Any, oid: str) -> dict[str, Any]:
    """Reception record, current plan pointer, submissions and reviews."""
    state = _work_item_state(conn, ctx.scope_id, oid)
    reception = _reception_row(conn, ctx.scope_id, oid)
    current_plan = None
    submissions: list[dict[str, Any]] = []
    deliverable_object_id = None
    if state is not None:
        if state.get("plan_object_id") and state.get("current_plan_revision_id"):
            current_plan = {
                "object_id": str(state["plan_object_id"]),
                "revision_id": str(state["current_plan_revision_id"]),
                "revision": db.revision_row(conn, ctx, str(state["plan_object_id"]),
                                            str(state["current_plan_revision_id"])),
            }
        if state.get("deliverable_object_id"):
            deliverable_object_id = str(state["deliverable_object_id"])
            submissions = _deliverable_revisions(conn, ctx.scope_id, deliverable_object_id)
    return {
        "work_item_state": db.jsonable(state) if state is not None else None,
        "reception": db.jsonable(reception) if reception is not None else None,
        "current_plan": db.jsonable(current_plan) if current_plan is not None else None,
        "deliverable_object_id": deliverable_object_id,
        "submissions": db.jsonable(submissions),
        "reviews": db.jsonable(_reviews_by_work_item(conn, ctx.scope_id, oid)),
    }


def _exact_ref(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    ref = payload.get(key)
    if isinstance(ref, dict) and ref.get("object_id") and ref.get("revision_id"):
        return {"object_id": str(ref["object_id"]), "revision_id": str(ref["revision_id"])}
    return None


def _plan_projection(conn: Any, ctx: Any, oid: str,
                     latest_payload: dict[str, Any]) -> dict[str, Any]:
    """Trace from the plan to its governing WorkItem / ExecutionCommitment."""
    state = _work_item_state_by_plan(conn, ctx.scope_id, oid)
    is_current = False
    if state is not None and state.get("current_plan_revision_id"):
        head = _load_head(conn, ctx.scope_id, oid)
        is_current = bool(head is not None
                          and str(state["current_plan_revision_id"]) == str(head.get("latest_revision_id")))
    return {
        "work_item_ref": _exact_ref(latest_payload, "work_item_ref"),
        "execution_commitment_ref": _exact_ref(latest_payload, "execution_commitment_ref"),
        "work_item_state": db.jsonable(state) if state is not None else None,
        "is_current_plan": is_current,
    }


def _deliverable_projection(conn: Any, ctx: Any, oid: str) -> dict[str, Any]:
    """Trace from the deliverable to its WorkItem plus every A3 review."""
    state = _work_item_state_by_deliverable(conn, ctx.scope_id, oid)
    reviews = _reviews_by_deliverable(conn, ctx.scope_id, oid)
    is_latest_submission = False
    if state is not None and state.get("latest_submission_revision_id"):
        head = _load_head(conn, ctx.scope_id, oid)
        is_latest_submission = bool(
            head is not None
            and str(state["latest_submission_revision_id"]) == str(head.get("latest_revision_id")))
    work_item = None
    if state is not None:
        work_item = {"object_id": str(state["object_id"]),
                     "work_item_revision_id": str(state["work_item_revision_id"])}
    return {
        "work_item": work_item,
        "work_item_state": db.jsonable(state) if state is not None else None,
        "reviews": db.jsonable(reviews),
        "is_latest_submission": is_latest_submission,
    }


def object_state(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    """A3 read projection: authorized head, protocol metadata, latest and
    effective revisions, plus the ``a3_projection`` of exact independent
    execution state.  No business writes; no legacy reinterpretation."""
    head = _require_a3_head(conn, ctx, object_id)
    oid = str(head["object_id"])
    result = dict(head)
    result["protocol"] = protocol.require_read_support(conn, ctx.scope_id, oid)
    for label in ("latest", "effective"):
        rid = head.get(f"{label}_revision_id")
        result[f"{label}_revision"] = (
            db.revision_row(conn, ctx, oid, str(rid)) if rid else None)
    latest = result.get("latest_revision") or {}
    latest_payload = latest.get("payload") if isinstance(latest, dict) else None
    latest_payload = latest_payload if isinstance(latest_payload, dict) else {}
    otype = head["object_type"]
    if otype == "ExecutionCommitment":
        projection = _commitment_projection(conn, ctx, oid)
    elif otype == "WorkItem":
        projection = _work_item_projection(conn, ctx, oid)
    elif otype == "ExecutionPlan":
        projection = _plan_projection(conn, ctx, oid, latest_payload)
    else:  # Deliverable
        projection = _deliverable_projection(conn, ctx, oid)
    result["a3_projection"] = projection
    for revision_row in (result.get("latest_revision"), result.get("effective_revision")):
        if revision_row:
            _authorize_payload(conn, ctx, revision_row["payload"])
    for revision_row in projection.get("submissions", []):
        _authorize_payload(conn, ctx, revision_row["payload"])
    return db.jsonable(result)


def revision(conn: Any, ctx: Any, object_id: str, revision_id: str) -> dict[str, Any]:
    """Authorized read of one exact immutable A3 revision + protocol block."""
    head = _require_a3_head(conn, ctx, object_id)
    rid = _uuid_str(revision_id)
    if rid is None:
        raise GovernedError("NOT_FOUND")
    result = db.jsonable(db.revision_row(conn, ctx, str(head["object_id"]), rid))
    result["protocol"] = protocol.require_read_support(conn, ctx.scope_id,
                                                       str(head["object_id"]))
    _authorize_payload(conn, ctx, result["payload"])
    return result


# ---------------------------------------------------------------------------
# A3 receipt read authorization
# ---------------------------------------------------------------------------


def _authorize_referenced(conn: Any, ctx: Any, oid: Any, rid: Any = None) -> None:
    """One exposed reference must be currently readable.

    A2 material (Mission / DomainCommitment / CompanyReference / ...) goes
    through the A2 visible-object path, which already implements the
    same-domain gate plus the bounded participant / shared-source grants —
    an IC with current domain read rights is NOT required to be a CEO/DRI
    Round signer just because its commitment cites an A2 Mission.  Everything
    else (the A3 execution objects, EvidenceAssets) requires the ordinary
    same-domain read.  Every denial collapses to the fixed 404 so receipt
    existence never leaks.
    """
    object_id = _uuid_str(oid)
    revision_id = _uuid_str(rid) if rid is not None else None
    if object_id is None or (rid is not None and revision_id is None):
        raise GovernedError(*_RECEIPT_NOT_FOUND)
    head = _load_head(conn, ctx.scope_id, object_id)
    if head is None:
        raise GovernedError(*_RECEIPT_NOT_FOUND)
    try:
        if head.get("object_type") in A2_OBJECT_TYPES:
            from . import a2_readers
            a2_readers.visible_object(conn, ctx, object_id)
            if revision_id is not None:
                a2_readers.visible_revision(conn, ctx, object_id, revision_id)
        else:
            db.object_row(conn, ctx, object_id)
            if revision_id is not None:
                db.revision_row(conn, ctx, object_id, revision_id)
        protocol.require_read_support(conn, ctx.scope_id, object_id)
    except GovernedError as exc:
        raise GovernedError(*_RECEIPT_NOT_FOUND) from exc


def _authorize_payload(conn, ctx, payload):
    refs = [payload[k] for k in ("mission_ref", "domain_commitment_ref", "execution_commitment_ref",
                                "work_item_ref", "plan_ref") if payload.get(k)]
    refs += payload.get("what", {}).get("external_dependency_refs", [])
    for ref in refs:
        _authorize_referenced(conn, ctx, ref["object_id"], ref["revision_id"])
    for rid in payload.get("evidence_revision_ids", []):
        owner = _revision_owner(conn, ctx.scope_id, rid)
        _authorize_referenced(conn, ctx, owner, rid)


def authorize_receipt(conn: Any, ctx: Any, receipt: dict[str, Any]) -> None:
    """A3 receipt read authorization against CURRENT rights.

    Every object/revision the immutable receipt exposes — target, every
    object_versions entry, every result.referenced_object_ids entry — must be
    currently readable by the actor on its own merits.  No actor impersonation
    and no Round-signer requirement beyond the real read grants.  Replay
    current-authority validation is NOT repeated here; A3Execution owns it.
    """
    if not is_a3_receipt(conn, ctx, receipt):
        raise GovernedError(*_RECEIPT_NOT_FOUND)
    if receipt.get("target_object_id"):
        _authorize_referenced(conn, ctx, receipt["target_object_id"])
    for item in receipt.get("object_versions") or []:
        if not isinstance(item, dict):
            continue
        _authorize_referenced(conn, ctx, item.get("object_id"), item.get("revision_id"))
    result = receipt.get("result")
    if isinstance(result, dict):
        for item in result.get("referenced_object_ids") or []:
            _authorize_referenced(conn, ctx, item)


# ---------------------------------------------------------------------------
# relations view (workbench.relations-compatible shape)
# ---------------------------------------------------------------------------


def _payload_edge_refs(conn: Any, ctx: Any, head: dict[str, Any],
                       payload: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Typed one-hop references of an A3 payload as ``{(oid, rid): relation_type}``."""
    edges: dict[tuple[str, str], str] = {}

    def _take(ref: Any) -> None:
        if isinstance(ref, dict) and ref.get("object_id") and ref.get("revision_id"):
            oid, rid = _uuid_str(ref["object_id"]), _uuid_str(ref["revision_id"])
            if oid is not None and rid is not None:
                edges.setdefault((oid, rid), "source_reference")

    otype = head.get("object_type")
    if otype == "ExecutionCommitment":
        _take(payload.get("mission_ref"))
        _take(payload.get("domain_commitment_ref"))
        what = payload.get("what")
        if isinstance(what, dict):
            for ref in what.get("external_dependency_refs") or []:
                _take(ref)
    elif otype in {"WorkItem", "ExecutionPlan", "Deliverable"}:
        # delivery.payload_references covers upstream_refs,
        # execution_commitment_ref and work_item_ref with dedup.
        for ref in delivery.payload_references(payload):
            _take(ref)
    if payload.get("plan_ref"):
        _take(payload["plan_ref"])
    if otype == "Deliverable":
        for revision_id in payload.get("evidence_revision_ids") or []:
            owner = _revision_owner(conn, ctx.scope_id, revision_id)
            rid = _uuid_str(revision_id)
            if owner is not None and rid is not None:
                edges.setdefault((owner, rid), "source_reference")
    return edges


def _state_edge_refs(conn: Any, ctx: Any, head: dict[str, Any],
                     edges: dict[tuple[str, str], str]) -> None:
    """Own-responsibility edges from the independent A3 state tables."""
    oid = str(head["object_id"])
    otype = head.get("object_type")

    def _take(object_id: Any, revision_id: Any) -> None:
        o, r = _uuid_str(object_id), _uuid_str(revision_id)
        if o is not None and r is not None:
            edges.setdefault((o, r), "execution_trace")

    if otype == "ExecutionCommitment":
        for row in _work_items_by_commitment(conn, ctx.scope_id, oid):
            _take(row.get("object_id"), row.get("work_item_revision_id"))
    elif otype == "WorkItem":
        state = _work_item_state(conn, ctx.scope_id, oid)
        if state is not None:
            _take(state.get("commitment_object_id"), state.get("commitment_revision_id"))
            if state.get("plan_object_id"):
                _take(state["plan_object_id"], state.get("current_plan_revision_id"))
            if state.get("deliverable_object_id"):
                _take(state["deliverable_object_id"], state.get("latest_submission_revision_id"))
    elif otype == "ExecutionPlan":
        state = _work_item_state_by_plan(conn, ctx.scope_id, oid)
        if state is not None:
            _take(state.get("object_id"), state.get("work_item_revision_id"))
    elif otype == "Deliverable":
        state = _work_item_state_by_deliverable(conn, ctx.scope_id, oid)
        if state is not None:
            _take(state.get("object_id"), state.get("work_item_revision_id"))


def _visible_edge_target(conn: Any, ctx: Any, oid: str, rid: str) -> str | None:
    """Return the target's object_type iff the exact endpoint is currently
    visible; ``None`` hides the whole edge (including any count of it).

    Same-domain targets authorize through ``db``; explicitly shared
    CompanyReference / CapacityObservation targets authorize through the A2
    source grants.  Hidden CompanyComposition / FormationRound / other-domain
    material is never surfaced.
    """
    head = _load_head(conn, ctx.scope_id, oid)
    if head is None:
        return None
    try:
        db.object_row(conn, ctx, oid)
        db.revision_row(conn, ctx, oid, rid)
        protocol.require_read_support(conn, ctx.scope_id, oid)
        return str(head["object_type"])
    except GovernedError:
        pass
    if head.get("object_type") in A2_SOURCE_TYPES:
        try:
            from . import a2_readers
            a2_readers.visible_object(conn, ctx, oid)
            a2_readers.visible_revision(conn, ctx, oid, rid)
            protocol.require_read_support(conn, ctx.scope_id, oid)
            return str(head["object_type"])
        except GovernedError:
            return None
    return None


def relations(conn: Any, ctx: Any, object_id: str,
              revision_id: str | None = None,
              limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    """A3 anchor relations view, shape-compatible with workbench.relations.

    ``{"source_ref", "items", "next_cursor", "protocol"}`` with items of
    ``{"relation_type", "source_ref", "target_ref", "target_type"}``.  Edges
    are the anchor's own payload source references plus its A3 state
    responsibility trace; only currently readable endpoints appear and hidden
    counts are never returned.
    """
    from . import workbench

    oid = _uuid_str(object_id)
    if oid is None or not is_a3_object(conn, ctx, oid):
        raise GovernedError("NOT_FOUND")
    head = db.object_row(conn, ctx, oid)
    head_metadata = protocol.read_metadata(conn, ctx.scope_id, oid)
    if head_metadata.get("interpretation_status") not in CONTRACT_A_READ_STATUSES:
        raise GovernedError(
            "PROTOCOL_NOT_SUPPORTED",
            "The object's protocol registration does not support relation reads.",
            status=409,
        )
    source_rid = revision_id or head.get("latest_revision_id")
    if source_rid is None:
        raise GovernedError("INVALID_REQUEST",
                            "The anchor has no readable revision to anchor from.",
                            status=422)
    source = db.revision_row(conn, ctx, oid, str(source_rid))
    source_ref = {"object_id": oid, "revision_id": str(source["revision_id"])}
    cursor_key = workbench.decode_cursor(cursor, "relations", ctx, dict(source_ref))
    if cursor_key is not None:
        if not isinstance(cursor_key, list) or len(cursor_key) != 2 or not all(
                isinstance(value, str) and _uuid_str(value) == value for value in cursor_key):
            raise GovernedError("INVALID_REQUEST", "Invalid relation cursor.", status=422)

    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    edges = _payload_edge_refs(conn, ctx, head, payload)
    _state_edge_refs(conn, ctx, head, edges)
    ordered = sorted(edges)

    visible: list[dict[str, Any]] = []
    for tgt_oid, tgt_rid in ordered:
        if cursor_key is not None and (tgt_oid, tgt_rid) <= tuple(cursor_key):
            continue
        target_type = _visible_edge_target(conn, ctx, tgt_oid, tgt_rid)
        if target_type is None:
            continue
        visible.append({
            "relation_type": edges[(tgt_oid, tgt_rid)],
            "source_ref": source_ref,
            "target_ref": {"object_id": tgt_oid, "revision_id": tgt_rid},
            "target_type": target_type,
        })

    more = len(visible) > limit
    items = visible[:limit]
    next_cursor = None
    if more and items:
        last = items[-1]["target_ref"]
        next_cursor = workbench.encode_cursor(
            "relations", ctx, dict(source_ref),
            [last["object_id"], last["revision_id"]],
        )
    return db.jsonable({
        "source_ref": source_ref,
        "items": items,
        "next_cursor": next_cursor,
        "protocol": head_metadata,
    })


# ---------------------------------------------------------------------------
# A3 outcome-assessment projection (Contract-A CompanyReference only)
# ---------------------------------------------------------------------------


def _assessment_sources_readable(conn: Any, ctx: Any, row: dict[str, Any]) -> bool:
    """Every exact source the assessment exposes must be currently readable.

    Observation/evidence entries are in-scope EvidenceAsset revisions (the
    hashed event packets); they authorize through the ordinary same-domain
    read — EvidenceAssets are never shared through the A2 source exception,
    so an IC with only a shared target never sees hidden evidence IDs.
    Delivery references are A3 review rows whose governing WorkItem must be
    currently readable.
    """
    for key in ("observation_revision_ids", "evidence_revision_ids"):
        for revision_id in row.get(key) or []:
            owner = _revision_owner(conn, ctx.scope_id, revision_id)
            rid = _uuid_str(revision_id)
            if owner is None or rid is None:
                return False
            try:
                db.revision_row(conn, ctx, owner, rid)
                protocol.require_read_support(conn, ctx.scope_id, owner)
            except GovernedError:
                return False
    for acceptance_id in row.get("delivery_acceptance_ids") or []:
        aid = _uuid_str(acceptance_id)
        if aid is None:
            return False
        review = _review_row(conn, ctx.scope_id, aid)
        if review is None:
            return False
        try:
            work_item_id = str(review["work_item_object_id"])
            db.object_row(conn, ctx, work_item_id)
            db.revision_row(conn, ctx, work_item_id, str(review["work_item_revision_id"]))
            protocol.require_read_support(conn, ctx.scope_id, work_item_id)
            delivery_row = db.revision_row(conn, ctx, str(review["deliverable_object_id"]),
                                           str(review["deliverable_revision_id"]))
            _authorize_payload(conn, ctx, delivery_row["payload"])
        except GovernedError:
            return False
    return True


def project_outcome(conn: Any, ctx: Any, object_id: str) -> dict[str, Any] | None:
    """A3 outcome projection for a Contract-A bound CompanyReference.

    Returns ``None`` when the object is not a Contract-A CompanyReference or
    the target is not currently readable.  Each independently appended
    assessment appears only when its exact target revision and every
    observation/evidence/review source are currently readable; an assessment
    that fails any check is omitted whole.  ``latest_assessment_id`` is set
    only when the chronologically latest assessment is itself readable, so an
    older visible assessment is never mislabeled the authoritative latest.
    No side effects; stored source payloads are never altered.
    """
    oid = _uuid_str(object_id)
    if oid is None:
        return None
    head = _load_head(conn, ctx.scope_id, oid)
    if head is None or head.get("object_type") != "CompanyReference":
        return None
    if not _binding_is_contract_a(conn, ctx.scope_id, oid):
        return None
    try:
        from . import a2_readers
        a2_readers.visible_object(conn, ctx, oid)
        protocol.require_read_support(conn, ctx.scope_id, oid)
    except GovernedError:
        return None

    rows = _assessments_by_target(conn, ctx.scope_id, oid)
    if not rows:
        return None
    assessments: list[dict[str, Any]] = []
    latest_row_id = str(rows[-1]["assessment_id"]) if rows else None
    for row in rows:
        target_rid = str(row["company_reference_revision_id"])
        try:
            from . import a2_readers
            target_revision = a2_readers.visible_revision(conn, ctx, oid, target_rid)
            protocol.require_read_support(conn, ctx.scope_id, oid)
        except GovernedError:
            continue
        if not _assessment_sources_readable(conn, ctx, row):
            continue
        entry = {
            "assessment_id": str(row["assessment_id"]),
            "company_reference_ref": {
                "object_id": oid,
                "revision_id": target_rid,
                "payload_hash": target_revision.get("payload_hash"),
            },
            "period_id": str(row["period_id"]),
            "assessment_result": row["assessment_result"],
            "qualified_customer_count": row["qualified_customer_count"],
            "target_count": row["target_count"],
            "observation_revision_ids": [str(rid) for rid in row["observation_revision_ids"]],
            "evidence_revision_ids": [str(rid) for rid in row["evidence_revision_ids"]],
            "delivery_acceptance_ids": [str(aid) for aid in row["delivery_acceptance_ids"]],
            "assessment_note": row["assessment_note"],
            "assessor_assignment_id": str(row["assessor_assignment_id"]),
            "recorded_at": row["recorded_at"],
            "is_latest": False,
        }
        composition_ref = None
        try:
            from . import a2_readers
            a2_readers.visible_object(conn, ctx, str(row["composition_object_id"]))
            a2_readers.visible_revision(conn, ctx, str(row["composition_object_id"]),
                                        str(row["composition_revision_id"]))
            composition_ref = {"object_id": str(row["composition_object_id"]),
                               "revision_id": str(row["composition_revision_id"])}
        except GovernedError:
            composition_ref = None
        if composition_ref is not None:
            entry["composition_ref"] = composition_ref
        assessments.append(entry)

    latest_assessment_id = None
    if latest_row_id is not None:
        for entry in assessments:
            if entry["assessment_id"] == latest_row_id:
                entry["is_latest"] = True
                latest_assessment_id = latest_row_id
                break
    return db.jsonable({
        "company_reference_ref": {"object_id": oid},
        "assessments": assessments,
        "latest_assessment_id": latest_assessment_id,
    })
