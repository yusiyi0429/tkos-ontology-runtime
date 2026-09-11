"""Contract-A (A2) read-side projections.

This module provides the read helpers that A2 company-composition requires.
The fixed entry points are:

- :func:`visible_object` / :func:`visible_revision` — return the standard
  gov_objects / revision dict.  They run the normal same-domain read first
  via ``db.object_row`` / ``db.revision_row`` (the legacy 404/403 boundary);
  only when that path is exhausted do they evaluate a precisely bounded A2-18
  cross-domain exception for current Round participants.  Legacy / non-A2
  objects always go through the legacy path unchanged.

- :func:`current_assignment` — returns the currently valid human assignment
  row for the given ``assignment_id`` (live DB clock, principal_type=human,
  principal.active, scope-scoped).  Returns ``None`` if there is no current
  row.

- :func:`object_state` / :func:`revision` — the A2 read projection: for A2
  payloads the standard latest/effective revision + ``protocol`` block is
  augmented with the current Round / confirmation / activation projection
  that is useful to participants.  The CompanyComposition projection also
  carries the immutable at-form record: ``unresolved_conflicts`` /
  ``deterministic_conflicts`` from the creation event detail and a
  ``readiness`` block marked ``basis="at_form"`` /
  ``requires_live_admission=True`` — a form-time re-check record, never a
  claim that the candidate is currently activatable.

- :func:`authorize_receipt` — A2 replay authorization.  Identifies the
  current Round anchor and the current actor's authority; checks every
  referenced public material before permitting replay of the original
  immutable receipt.  No short-circuit on the historical actor_id; a
  revoked/replaced signer is denied even if they wrote the receipt.

- :func:`is_a2_object` — internal routing helper.  Returns ``True`` only for
  one of the seven A2 object types whose current binding is Contract-A.
  Raw existence is never exposed.

- :func:`relations` — A2 anchor relations view.  Matches the
  ``workbench.relations`` schema; non-A2 anchors delegate to the workbench
  helper unchanged.  For A2 anchors, only currently-readable endpoints are
  exposed and hidden counts are never returned.

The module never mutates business state.  It composes ``db.object_row``,
``db.revision_row``, ``db.authorize_domain``, ``protocol.read_metadata`` and
the existing ``workbench.relations`` helper so historical reads still use
current rights.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from memory_service_runtime.governed import db, delivery, protocol, readers, workbench
from memory_service_runtime.governed.a2_models import (
    A2_COMPOSITION_TYPES,
    A2_OBJECT_TYPES,
    A2_SOURCE_TYPES,
    CompositionManifest,
    static_conflict_reasons,
)
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.profile import CONTRACT_A_PROTOCOL_ID
from memory_service_runtime.governed.protocol import CONTRACT_A_READ_STATUSES


# ---------------------------------------------------------------------------
# small helpers (raw scoped reads — used only inside the access-policy logic)
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


# Roles accepted for the two Round slot kinds.
CEO_ROLES = frozenset({"CEO", "DOMAIN_CEO"})
DRI_ROLES = frozenset({"DOMAIN_DRI", "CEO"})


# ---------------------------------------------------------------------------
# Round identification helpers
# ---------------------------------------------------------------------------


def _round_id_for(head: dict[str, Any], revision: dict[str, Any]) -> str | None:
    """Resolve the round id backing an A2 object (best-effort)."""
    otype = head.get("object_type")
    payload = revision.get("payload") if isinstance(revision, dict) else None
    payload = payload if isinstance(payload, dict) else {}
    if otype == "FormationRound":
        return str(head["object_id"])
    if otype in {"DomainSubmission", "Mission", "DomainCommitment"}:
        ref = payload.get("round_object_id")
        if ref:
            return str(ref)
    if otype == "CompanyComposition":
        # Manifest payload uses ``round_id``; some legacy shapes also carry
        # ``round_object_id``.  Prefer round_id for the canonical A2 manifest.
        ref = payload.get("round_id") or payload.get("round_object_id")
        if ref:
            return str(ref)
    return None


def _load_head(conn: Any, scope_id: str, oid: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
        (scope_id, oid),
    )


def _load_revision(conn: Any, scope_id: str, oid: str, rid: str) -> dict[str, Any] | None:
    rid_uuid = _uuid_str(rid)
    if rid_uuid is None:
        return None
    return _row(
        conn,
        "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
        (scope_id, oid, rid_uuid),
    )


def _round_definition_row(conn: Any, scope_id: str,
                          round_object_id: str) -> dict[str, Any] | None:
    """Raw scoped fetch of the current Round head + its latest revision payload."""
    head = _load_head(conn, scope_id, round_object_id)
    if head is None:
        return None
    latest_rid = head.get("latest_revision_id")
    payload: dict[str, Any] = {}
    if latest_rid is not None:
        rev = _load_revision(conn, scope_id, str(head["object_id"]), str(latest_rid))
        if rev is not None and isinstance(rev.get("payload"), dict):
            payload = dict(rev["payload"])
    return {"head": head, "payload": payload, "latest_revision_id": latest_rid}


def _round_state_row(conn: Any, scope_id: str, round_object_id: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT * FROM gov_formation_round_state WHERE scope_id=%s AND object_id=%s",
        (scope_id, round_object_id),
    )


def _member_rows(conn: Any, scope_id: str, round_object_id: str) -> list[dict[str, Any]]:
    head = _load_head(conn, scope_id, round_object_id)
    if head is None:
        return []
    rev = _load_revision(conn, scope_id, str(head["object_id"]),
                         str(head["latest_revision_id"]))
    if rev is None or not isinstance(rev.get("payload"), dict):
        return []
    members = rev["payload"].get("members") or []
    return [m for m in members if isinstance(m, dict)]


def _formal_pointer_row(conn: Any, scope_id: str, round_object_id: str,
                        domain_id: str) -> dict[str, Any] | None:
    return _row(
        conn,
        """SELECT * FROM gov_round_formal_submissions
            WHERE scope_id=%s AND round_object_id=%s AND domain_id=%s""",
        (scope_id, round_object_id, domain_id),
    )


def _confirmation_rows(conn: Any, scope_id: str,
                       composition_object_id: str) -> list[dict[str, Any]]:
    return _rows(
        conn,
        """SELECT * FROM gov_composition_confirmations
            WHERE scope_id=%s AND composition_object_id=%s
            ORDER BY recorded_at, confirmation_id""",
        (scope_id, composition_object_id),
    )


def _activation_row(conn: Any, scope_id: str, round_object_id: str) -> dict[str, Any] | None:
    return _row(
        conn,
        "SELECT * FROM gov_activation_records WHERE scope_id=%s AND round_object_id=%s",
        (scope_id, round_object_id),
    )


# ---------------------------------------------------------------------------
# A2-18 participant resolution
# ---------------------------------------------------------------------------


def _actor_current_slot(conn: Any, ctx: Any, round_object_id: str) -> dict[str, Any] | None:
    """Resolve the actor's currently-valid Round slot (None if not a participant).

    A slot is "current" iff ALL of the following hold for the *stored* slot:

    * the live round definition names this exact ``assignment_id`` and this
      exact ``principal_id`` (BOTH must match — no OR shortcut);
    * :func:`current_assignment` confirms that ``assignment_id`` is
      currently valid for ``ctx.principal_id``, the principal is human and
      active, and the assignment's domain/role match the stored slot;
    * ``db.authorize_domain`` for that domain+``"read"`` returns that exact
      assignment among the currently-allowed assignments (so a revoked
      DRI who still holds some other read-role in the same domain is NOT
      a participant here).

    The historical actor_id from a receipt is never a shortcut — the slot
    must be currently live.
    """
    snap = _round_definition_row(conn, ctx.scope_id, round_object_id)
    if snap is None:
        return None
    payload = snap["payload"]
    head = snap["head"]
    members = payload.get("members") if isinstance(payload.get("members"), list) else []
    ceo_assignment_id = payload.get("ceo_assignment_id")
    ceo_principal_id = payload.get("ceo_principal_id")
    state = _round_state_row(conn, ctx.scope_id, round_object_id)
    company_domain_id = (state or {}).get("company_domain_id") or payload.get("company_domain_id")

    def _exact(slot: dict[str, Any], domain_id: str, role: str) -> dict[str, Any] | None:
        stored_aid = str(slot.get("assignment_id") or "")
        stored_pid = str(slot.get("principal_id") or "")
        if not (stored_aid and stored_pid):
            return None
        # Both stored IDs must match the actor (no OR shortcut).
        if stored_pid != ctx.principal_id:
            return None
        live = current_assignment(conn, ctx, stored_aid)
        if live is None:
            return None
        if str(live.get("principal_id") or "") != ctx.principal_id:
            return None
        if str(live.get("principal_type") or "") != "human":
            return None
        if not live.get("principal_active"):
            return None
        # The live assignment's role must EXACTLY match the slot's expected
        # role — never a broader DOMAIN_CEO fallback invented at read time.
        if str(live.get("role") or "") != role:
            return None
        if str(live.get("domain_id") or "") != str(domain_id):
            return None
        # The exact assignment must appear in the live policy's allowed set.
        try:
            allowed = db.authorize_domain(conn, ctx, str(domain_id), "read")
        except GovernedError:
            return None
        if not any(str(item.get("assignment_id") or "") == stored_aid
                   for item in (allowed or [])):
            return None
        return {
            "domain_id": str(domain_id),
            "assignment_id": stored_aid,
            "principal_id": stored_pid,
            "responsibility_role": ("company_decider"
                                    if role == "CEO" else "area_accountable"),
        }

    for member in members:
        if not isinstance(member, dict):
            continue
        domain_id = member.get("domain_id")
        if not domain_id:
            continue
        slot = _exact({
            "assignment_id": member.get("dri_assignment_id"),
            "principal_id": member.get("dri_principal_id"),
        }, str(domain_id), "DOMAIN_DRI")
        if slot is not None:
            return slot

    if ceo_assignment_id and ceo_principal_id:
        slot = _exact({
            "assignment_id": ceo_assignment_id,
            "principal_id": ceo_principal_id,
        }, str(company_domain_id) if company_domain_id else "", "CEO")
        if slot is not None and slot["domain_id"]:
            return slot
    return None


def _slot_has_current_read_policy(conn: Any, ctx: Any, slot: dict[str, Any]) -> bool:
    """Confirm the slot's domain currently grants the actor a read role."""
    domain_id = slot.get("domain_id")
    if not domain_id:
        return False
    try:
        db.authorize_domain(conn, ctx, str(domain_id), "read")
        return True
    except GovernedError:
        return False


# ---------------------------------------------------------------------------
# Source publication validation (shared_with_domain_ids)
# ---------------------------------------------------------------------------


def _source_revision_published_to(conn: Any, scope_id: str,
                                  object_id: str, revision_id: str,
                                  actor_readable_domains: set[str]) -> bool:
    """True iff the exact requested revision is currently published to a
    domain for which the actor CURRENTLY has a live read policy.

    The publication is permission; referencing it from a manifest is not.
    Returns ``False`` for missing rows, EvidenceAssets, or unpublished heads.
    """
    head = _load_head(conn, scope_id, object_id)
    if head is None or head.get("object_type") == "EvidenceAsset":
        return False
    rev = _load_revision(conn, scope_id, object_id, revision_id)
    if rev is None:
        return False
    payload = rev.get("payload") if isinstance(rev.get("payload"), dict) else {}
    shared = payload.get("shared_with_domain_ids") or []
    shared = {str(d) for d in shared if isinstance(d, str)}
    return bool(shared & actor_readable_domains)


def _head_is_published_to(conn: Any, scope_id: str,
                          object_id: str, actor_readable_domains: set[str]) -> bool:
    """Head GET: requires the actual latest AND effective revision to each be
    currently published to a readable domain.  A later private head that
    superseded a published revision → 404, even if the older revision is
    still referenced by some stale manifest."""
    head = _load_head(conn, scope_id, object_id)
    if head is None:
        return False
    for label in ("effective", "latest"):
        rid = head.get(f"{label}_revision_id")
        if rid is None:
            return False
        if not _source_revision_published_to(
                conn, scope_id, object_id, str(rid), actor_readable_domains):
            return False
    return True


def _authorize_object(conn: Any, ctx: Any, object_id: str,
                      expected_revision_id: str | None = None) -> bool:
    """Authorize one object end-to-end against current scope/policy/binding.

    Uses the public :func:`visible_object` / :func:`visible_revision`
    surface (which itself runs the same-domain path first, then the A2-18
    participant or shared-source exception).  Returns True only on success.
    The expected_revision_id is honored when the object type is an A2 source
    so a private later head cannot substitute for a published earlier
    revision — otherwise :func:`visible_revision` would already enforce that
    on its own.
    """
    try:
        visible_object(conn, ctx, object_id)
        if expected_revision_id is not None:
            visible_revision(conn, ctx, object_id, expected_revision_id)
        protocol.require_read_support(conn, ctx.scope_id, object_id)
        return True
    except GovernedError:
        return False


# ---------------------------------------------------------------------------
# public callable interfaces
# ---------------------------------------------------------------------------


def _formal_pointer(conn: Any, scope_id: str, round_object_id: str,
                     domain_id: str) -> dict[str, Any] | None:
    return _formal_pointer_row(conn, scope_id, round_object_id, domain_id)


def _actor_is_required_signer(manifest: dict[str, Any] | None,
                              principal_id: str) -> bool:
    if not isinstance(manifest, dict):
        return False
    signers = manifest.get("required_signers")
    if not isinstance(signers, list):
        return False
    return any(isinstance(s, dict) and str(s.get("principal_id") or "") == principal_id
               for s in signers)


def _mission_origin_matches_current(mission_payload: dict[str, Any] | None,
                                    formal: dict[str, Any] | None) -> bool:
    """The Mission revision is sharable only when its ``origin_submission_ref``
    points at the current formal pointer (object_id + revision_id + hash)."""
    if not isinstance(mission_payload, dict) or not isinstance(formal, dict):
        return False
    origin = mission_payload.get("origin_submission_ref")
    if not isinstance(origin, dict):
        return False
    return (str(origin.get("object_id") or "") == str(formal.get("submission_object_id") or "")
            and str(origin.get("revision_id") or "") == str(formal.get("submission_revision_id") or ""))


def _domain_commitment_allowed(commitment_payload: dict[str, Any] | None,
                              activation: dict[str, Any] | None,
                              round_object_id: str,
                              target_domain_id: str,
                              object_id: str, revision_id: str) -> bool:
    """A DomainCommitment revision is sharable only when it matches the
    activation record AND the commitment is named in
    ``activation.detail.domain_commitments`` for the target domain.

    The activation.detail.domain_commitments list carries explicit
    ``{domain_id, object_id, revision_id}`` triples; the commitment must
    be named for *its own* target domain, not the actor's slot domain.
    """
    if not isinstance(commitment_payload, dict) or not isinstance(activation, dict):
        return False
    if str(commitment_payload.get("round_object_id") or "") != str(round_object_id):
        return False
    composition_ref = commitment_payload.get("composition_ref")
    if not isinstance(composition_ref, dict):
        return False
    if str(composition_ref.get("object_id") or "") != str(
            activation.get("composition_object_id") or ""):
        return False
    if str(composition_ref.get("revision_id") or "") != str(
            activation.get("composition_revision_id") or ""):
        return False
    if composition_ref.get("manifest_hash") != activation.get("manifest_hash"):
        return False
    if str(commitment_payload.get("domain_id") or "") != target_domain_id:
        return False
    detail = activation.get("detail") if isinstance(activation.get("detail"), dict) else {}
    entries = detail.get("domain_commitments") if isinstance(detail.get("domain_commitments"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("domain_id") or "") != str(target_domain_id):
            continue
        if str(entry.get("object_id") or "") != str(object_id):
            continue
        if str(entry.get("revision_id") or "") != str(revision_id):
            continue
        return True
    return False


def visible_object(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    """Return the standard gov_objects dict, 404 if invisible.

    Strategy:

    1. Same-domain ``db.object_row`` path (legacy 404/403 boundary).
    2. On NOT_FOUND/FORBIDDEN, evaluate the bounded A2-18 exception for
       A2 object types whose current binding is Contract-A and whose
       identity/scope checks have already passed.
    3. A2 source heads require the actual latest AND effective revision to
       each be currently published to a domain readable by the actor.
    4. A2 composition heads require the actor to be a current Round
       participant whose slot domain still grants read.
    """
    oid = _uuid_str(object_id)
    if oid is None:
        raise GovernedError("NOT_FOUND")
    try:
        return db.object_row(conn, ctx, oid)
    except GovernedError as exc:
        if exc.code not in {"NOT_FOUND", "FORBIDDEN"}:
            raise

    head = _load_head(conn, ctx.scope_id, oid)
    if head is None or head.get("object_type") not in A2_OBJECT_TYPES:
        raise GovernedError("NOT_FOUND")

    # Identity / scope checks must succeed BEFORE any protocol error so that
    # a private object with a stale registry never leaks existence via 409.
    binding = protocol.current_binding(conn, ctx.scope_id, oid)
    if binding is None or binding.get("protocol_id") != CONTRACT_A_PROTOCOL_ID:
        raise GovernedError("NOT_FOUND")

    otype = head["object_type"]
    if otype in A2_SOURCE_TYPES:
        actor_domains = _actor_current_read_domains(conn, ctx)
        if not _head_is_published_to(conn, ctx.scope_id, oid, actor_domains):
            raise GovernedError("NOT_FOUND")
        return db.jsonable(dict(head))

    # Composition types (Round / DomainSubmission / Mission / CompanyComposition /
    # DomainCommitment): require a current Round participant slot OR a
    # native same-domain read (which the legacy path above would have allowed).
    rev = _load_revision(conn, ctx.scope_id, head["object_id"],
                         str(head.get("latest_revision_id") or ""))
    round_object_id = _round_id_for(head, rev or {"payload": {}})
    if round_object_id is None:
        raise GovernedError("NOT_FOUND")
    slot = _actor_current_slot(conn, ctx, round_object_id)
    if slot is None or not _slot_has_current_read_policy(conn, ctx, slot):
        raise GovernedError("NOT_FOUND")

    # Composition-specific tightening: the head must point at currently
    # sharable material, not historic leftovers from removed members.
    # The formal pointer is keyed by (round, target's own domain_id), NOT
    # by the actor's slot domain — a DRI must use the submission's domain
    # so cross-domain revisions land on the right pointer row.
    target_domain_id = str(head.get("domain_id") or slot["domain_id"])
    if otype == "DomainSubmission":
        formal = _formal_pointer(conn, ctx.scope_id, round_object_id, target_domain_id)
        if formal is None or str(formal.get("submission_object_id") or "") != oid:
            raise GovernedError("NOT_FOUND")
    elif otype == "CompanyComposition":
        # The requesting principal must be in the composition's exact
        # required_signers set — being a Round participant alone is not
        # enough if this composition belongs to another manifest revision.
        manifest = (rev or {}).get("payload") if isinstance(rev, dict) else None
        if not _actor_is_required_signer(manifest, ctx.principal_id):
            raise GovernedError("NOT_FOUND")
    elif otype == "Mission":
        formal = _formal_pointer(conn, ctx.scope_id, round_object_id, target_domain_id)
        if not _mission_origin_matches_current((rev or {}).get("payload"), formal):
            raise GovernedError("NOT_FOUND")
    elif otype == "DomainCommitment":
        activation = _activation_row(conn, ctx.scope_id, round_object_id)
        if not _domain_commitment_allowed((rev or {}).get("payload"),
                                          activation, round_object_id,
                                          target_domain_id, oid, str(rev["revision_id"])):
            raise GovernedError("NOT_FOUND")
    return db.jsonable(dict(head))


def visible_revision(conn: Any, ctx: Any, object_id: str, revision_id: str) -> dict[str, Any]:
    """Return the standard revision dict, 404 if invisible.

    Same two-tier logic as :func:`visible_object`.  Exact revision GET may
    authorize its own R1 grant without leaking the newer head — the actor
    sees the requested revision only when its publication includes a domain
    the actor can currently read."""
    oid = _uuid_str(object_id)
    rid = _uuid_str(revision_id)
    if oid is None or rid is None:
        raise GovernedError("NOT_FOUND")
    try:
        return db.revision_row(conn, ctx, oid, rid)
    except GovernedError as exc:
        if exc.code not in {"NOT_FOUND", "FORBIDDEN"}:
            raise

    head = _load_head(conn, ctx.scope_id, oid)
    if head is None or head.get("object_type") not in A2_OBJECT_TYPES:
        raise GovernedError("NOT_FOUND")

    binding = protocol.current_binding(conn, ctx.scope_id, oid)
    if binding is None or binding.get("protocol_id") != CONTRACT_A_PROTOCOL_ID:
        raise GovernedError("NOT_FOUND")

    rev = _load_revision(conn, ctx.scope_id, oid, rid)
    if rev is None:
        raise GovernedError("NOT_FOUND")

    otype = head["object_type"]
    if otype in A2_SOURCE_TYPES:
        actor_domains = _actor_current_read_domains(conn, ctx)
        if not _source_revision_published_to(
                conn, ctx.scope_id, oid, rid, actor_domains):
            raise GovernedError("NOT_FOUND")
        return db.jsonable(dict(rev))

    # Composition: round participant + composition-specific tightening.
    round_object_id = _round_id_for(head, rev)
    if round_object_id is None:
        raise GovernedError("NOT_FOUND")
    slot = _actor_current_slot(conn, ctx, round_object_id)
    if slot is None or not _slot_has_current_read_policy(conn, ctx, slot):
        raise GovernedError("NOT_FOUND")

    payload = rev.get("payload") if isinstance(rev.get("payload"), dict) else {}
    # Formal pointer is keyed by (round, target's own domain_id), not by
    # the actor's slot domain — same cross-domain rule as visible_object.
    target_domain_id = str(head.get("domain_id") or slot["domain_id"])
    if otype == "DomainSubmission":
        formal = _formal_pointer(conn, ctx.scope_id, round_object_id, target_domain_id)
        if formal is None:
            raise GovernedError("NOT_FOUND")
        if str(formal.get("submission_object_id") or "") != oid:
            raise GovernedError("NOT_FOUND")
        if str(formal.get("submission_revision_id") or "") != rid:
            raise GovernedError("NOT_FOUND")
    elif otype == "CompanyComposition":
        if not _actor_is_required_signer(payload, ctx.principal_id):
            raise GovernedError("NOT_FOUND")
    elif otype == "Mission":
        formal = _formal_pointer(conn, ctx.scope_id, round_object_id, target_domain_id)
        if not _mission_origin_matches_current(payload, formal):
            raise GovernedError("NOT_FOUND")
    elif otype == "DomainCommitment":
        activation = _activation_row(conn, ctx.scope_id, round_object_id)
        if not _domain_commitment_allowed(payload, activation, round_object_id,
                                          target_domain_id, oid, rid):
            raise GovernedError("NOT_FOUND")
    return db.jsonable(dict(rev))


def _actor_current_read_domains(conn: Any, ctx: Any) -> set[str]:
    """Domains for which the actor currently holds a live read role.

    Implementation:

    * Pull the actor's currently active, human assignments in this scope.
    * For each unique domain, call :func:`db.authorize_domain` (which uses
      the **latest** activation policy and the live assignment validity).
      Include only the domains where authorize_domain actually returns
      the actor's current assignment among the allowed set.

    This never consults older policy revisions: a withdrawn read policy
    immediately strips the actor's access even if an older allow row would
    have matched.
    """
    candidates = _rows(
        conn,
        """SELECT a.assignment_id, a.domain_id
             FROM gov_role_assignments a JOIN gov_principals p
               ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
            WHERE a.scope_id=%s AND a.principal_id=%s
              AND p.principal_type='human' AND p.active
              AND a.active
              AND a.valid_from <= clock_timestamp()
              AND (a.valid_to IS NULL OR a.valid_to > clock_timestamp())""",
        (ctx.scope_id, ctx.principal_id),
    )
    allowed: set[str] = set()
    for cand in candidates:
        domain_id = str(cand["domain_id"])
        cand_aid = str(cand["assignment_id"])
        try:
            rows = db.authorize_domain(conn, ctx, domain_id, "read")
        except GovernedError:
            continue
        if any(str(r.get("assignment_id") or "") == cand_aid for r in (rows or [])):
            allowed.add(domain_id)
    return allowed


def current_assignment(conn: Any, ctx: Any, assignment_id: str) -> dict[str, Any] | None:
    """Return the currently valid human assignment row for ``assignment_id``.

    Live DB clock, scope-scoped, principal_type='human', principal.active.
    Returns ``None`` if no row exists in this scope, if the principal is not
    a human, or if the assignment is not currently valid (expired, inactive,
    or in the future).  Returns the joined row including ``principal_type``
    and ``principal_active`` so handlers can decide ownership.
    """
    aid = _uuid_str(assignment_id)
    if aid is None:
        return None
    row = _row(
        conn,
        """SELECT a.assignment_id, a.scope_id, a.domain_id, a.principal_id, a.role,
                  a.active, a.valid_from, a.valid_to,
                  p.principal_type, p.active AS principal_active
             FROM gov_role_assignments a JOIN gov_principals p
               ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
            WHERE a.scope_id=%s AND a.assignment_id=%s
              AND p.principal_type='human' AND p.active AND a.active
              AND a.valid_from <= clock_timestamp()
              AND (a.valid_to IS NULL OR a.valid_to > clock_timestamp())
            LIMIT 1""",
        (ctx.scope_id, aid),
    )
    if row is None:
        return None
    return db.jsonable(dict(row))


# ---------------------------------------------------------------------------
# Object state / revision projections
# ---------------------------------------------------------------------------


def _round_block(conn: Any, ctx: Any, round_object_id: str) -> dict[str, Any]:
    head = visible_object(conn, ctx, round_object_id)
    head_metadata = protocol.require_read_support(conn, ctx.scope_id, head["object_id"])
    definition_payload: dict[str, Any] | None = None
    if head.get("latest_revision_id"):
        rev = _load_revision(conn, ctx.scope_id, str(head["object_id"]),
                             str(head["latest_revision_id"]))
        if rev is not None and isinstance(rev.get("payload"), dict):
            definition_payload = rev["payload"]
    state = _round_state_row(conn, ctx.scope_id, round_object_id)
    formal = _rows(
        conn,
        """SELECT domain_id, submission_object_id, submission_revision_id,
                  published_by_principal_id, published_by_assignment_id,
                  action_id, published_at
             FROM gov_round_formal_submissions
            WHERE scope_id=%s AND round_object_id=%s
            ORDER BY domain_id""",
        (ctx.scope_id, round_object_id),
    )
    member_domains = {m["domain_id"] for m in (definition_payload or {}).get("members", [])}
    formal = [row for row in formal if str(row["domain_id"]) in member_domains]
    return {
        "round_object_id": str(head["object_id"]),
        "round": db.jsonable(dict(head)),
        "definition": db.jsonable(definition_payload) if definition_payload else None,
        "state": db.jsonable(dict(state)) if state is not None else None,
        "formal_submissions": db.jsonable(formal),
        "protocol": head_metadata,
    }


def _composition_block(conn: Any, ctx: Any, composition_object_id: str) -> dict[str, Any]:
    head = visible_object(conn, ctx, composition_object_id)
    head_metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    rid = head.get("effective_revision_id") or head.get("latest_revision_id")
    manifest_payload: dict[str, Any] | None = None
    if rid is not None:
        rev = visible_revision(conn, ctx, head["object_id"], str(rid))
        if isinstance(rev.get("payload"), dict):
            manifest_payload = rev["payload"]
    if manifest_payload is None:
        raise GovernedError(
            "INVALID_STATE",
            "The composition manifest revision is not readable.")
    confirmations = _confirmation_rows(conn, ctx.scope_id, head["object_id"])
    # Pending-conflict context comes from the exact immutable
    # form_company_composition event whose detail revision_id matches this
    # candidate's manifest revision (scope-limited).  A missing or mismatched
    # event is an integrity failure, never a silently empty readiness.
    form_event = _row(
        conn,
        """SELECT detail FROM gov_lifecycle_events
            WHERE scope_id=%s AND object_id=%s AND event_type='form_company_composition'
            ORDER BY recorded_at LIMIT 1""",
        (ctx.scope_id, str(head["object_id"])),
    )
    detail = form_event.get("detail") if isinstance(form_event, dict) else None
    if not isinstance(detail, dict) or str(detail.get("revision_id") or "") != str(rid):
        raise GovernedError(
            "INVALID_STATE",
            "The immutable form event detail for this candidate revision is missing.")
    unresolved = list(detail.get("unresolved_conflicts") or [])
    deterministic = list(detail.get("deterministic_conflicts") or [])
    try:
        manifest_model = CompositionManifest.model_validate(manifest_payload)
    except ValidationError as exc:
        raise GovernedError(
            "INVALID_STATE", "The stored manifest does not validate.") from exc
    static_reasons = static_conflict_reasons(manifest_model)
    # Readiness here is the at-form re-check record (immutable manifest +
    # creation event detail).  It does NOT claim the candidate is currently
    # activatable: confirm/activate re-run the full live admission checks
    # (member/input generations, source currency/publication/freshness,
    # capacity aggregation, signer coverage) at the final admission point.
    readiness = {
        "judgments_all_pass": all(j["conclusion"] == "pass" for j in manifest_payload["judgments"].values()),
        "static_conflict_reasons": static_reasons,
        "hard_conflicts": deterministic,
        "blocking_unresolved": bool(detail.get("blocking_at_form"))
            or any(bool(c.get("blocking")) for c in unresolved),
        "basis": "at_form",
        "requires_live_admission": True,
    }
    return {
        "composition_object_id": str(head["object_id"]),
        "composition": head,
        "manifest": db.jsonable(manifest_payload),
        "confirmations": db.jsonable(confirmations),
        "unresolved_conflicts": db.jsonable(unresolved),
        "deterministic_conflicts": db.jsonable(deterministic),
        "readiness": readiness,
        "protocol": head_metadata,
    }


def _activation_block(conn: Any, ctx: Any, round_object_id: str) -> dict[str, Any] | None:
    row = _activation_row(conn, ctx.scope_id, round_object_id)
    return db.jsonable(dict(row)) if row is not None else None


def object_state(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    """A2 read projection: latest/effective revision+protocol, plus the A2
    round / composition / activation context for participants.

    Legacy / non-A2 objects go through ``readers.object_state`` unchanged
    so the existing delivery / outcome / feedback projections still apply.
    """
    oid = _uuid_str(object_id)
    if oid is None:
        raise GovernedError("NOT_FOUND")
    head = _load_head(conn, ctx.scope_id, oid)
    if head is None or head.get("object_type") not in A2_OBJECT_TYPES:
        return readers.object_state(conn, ctx, oid)

    result = visible_object(conn, ctx, oid)
    metadata = protocol.require_read_support(conn, ctx.scope_id, head["object_id"])
    result["protocol"] = metadata
    for label in ("latest", "effective"):
        rid = head.get(f"{label}_revision_id")
        if rid is not None:
            try:
                result[f"{label}_revision"] = visible_revision(conn, ctx, oid, str(rid))
            except GovernedError:
                result[f"{label}_revision"] = None
        else:
            result[f"{label}_revision"] = None

    projection: dict[str, Any] = {}
    latest = result.get("latest_revision") or {}
    round_object_id = _round_id_for(head, latest if isinstance(latest, dict) else {})
    if round_object_id is not None:
        try:
            projection["round"] = _round_block(conn, ctx, round_object_id)
            projection["activation"] = _activation_block(conn, ctx, round_object_id)
        except GovernedError:
            pass
    if head.get("object_type") == "CompanyComposition":
        try:
            projection["composition"] = _composition_block(conn, ctx, head["object_id"])
        except GovernedError:
            pass
    if projection:
        result["a2_projection"] = projection
    return db.jsonable(result)


def revision(conn: Any, ctx: Any, oid: str, rid: str) -> dict[str, Any]:
    """Return the revision+protocol block.  Delegates to ``readers.revision``
    for non-A2 anchors; for A2 anchors, uses :func:`visible_revision` so the
    A2-18 cross-domain exception is honored."""
    oid_uuid = _uuid_str(oid)
    if oid_uuid is None:
        raise GovernedError("NOT_FOUND")
    head = _load_head(conn, ctx.scope_id, oid_uuid)
    if head is None or head.get("object_type") not in A2_OBJECT_TYPES:
        return readers.revision(conn, ctx, oid, rid)
    result = visible_revision(conn, ctx, oid_uuid, _uuid_str(rid) or rid)
    binding = protocol.current_binding(conn, ctx.scope_id, head["object_id"])
    if binding is None or binding.get("protocol_id") != CONTRACT_A_PROTOCOL_ID:
        raise GovernedError("NOT_FOUND")
    result["protocol"] = protocol.require_read_support(conn, ctx.scope_id, head["object_id"])
    return result


# ---------------------------------------------------------------------------
# A2 receipt authorization
# ---------------------------------------------------------------------------


_A2_ACTION_TYPES = frozenset({
    "open_formation_round", "amend_formation_round",
    "publish_domain_submission", "form_company_composition",
    "confirm_company_composition", "activate_company_composition",
})


def _receipt_anchor_round(conn: Any, ctx: Any, receipt: dict[str, Any]) -> str | None:
    """Resolve the round_object_id the receipt anchors to.

    Order of preference: target_object_id when the target is the Round; the
    manifest round_id / round_object_id for composition receipts; the first
    object_versions entry whose head type is FormationRound.
    """
    target = receipt.get("target_object_id")
    if target:
        target_uuid = _uuid_str(target)
        if target_uuid:
            head = _load_head(conn, ctx.scope_id, target_uuid)
            if head is not None:
                if head.get("object_type") == "FormationRound":
                    return target_uuid
                rev = _load_revision(conn, ctx.scope_id, target_uuid,
                                     str(head.get("effective_revision_id")
                                         or head.get("latest_revision_id") or ""))
                if rev is not None and isinstance(rev.get("payload"), dict):
                    ref = rev["payload"].get("round_id") or rev["payload"].get("round_object_id")
                    if ref:
                        return str(ref)
    for item in receipt.get("object_versions") or []:
        if not isinstance(item, dict):
            continue
        oid = _uuid_str(item.get("object_id"))
        if oid is None:
            continue
        head = _load_head(conn, ctx.scope_id, oid)
        if head is None:
            continue
        if head.get("object_type") == "FormationRound":
            return oid
    return None


def _authorize_referenced_sources(conn: Any, ctx: Any,
                                  receipt: dict[str, Any]) -> bool:
    """Every actual referenced material must be currently readable.

    Walks ``receipt["target_object_id"]``, every entry in
    ``receipt["object_versions"]``, and every id in
    ``receipt["result"]["referenced_object_ids"]`` and re-authorizes each
    against the current scope/policy/binding via :func:`visible_object`
    (which already runs the same-domain gate, then the bounded A2-18
    exception).  If a referenced revision id is provided, it is validated
    against the requested head via :func:`visible_revision`.  No short-circuit
    on the first native-readable object; the first denial fails the whole
    receipt.
    """
    target = receipt.get("target_object_id")
    if target:
        if not _authorize_object(conn, ctx, str(target)):
            return False
    for item in receipt.get("object_versions") or []:
        if not isinstance(item, dict):
            continue
        oid = _uuid_str(item.get("object_id"))
        if oid is None:
            return False
        rid = _uuid_str(item.get("revision_id"))
        if not _authorize_object(conn, ctx, oid, rid):
            return False
    referenced = (receipt.get("result") or {}).get("referenced_object_ids") or []
    for item in referenced:
        oid = _uuid_str(item)
        if oid is None:
            return False
        if not _authorize_object(conn, ctx, oid):
            return False
    return True


def _company_ceo_current_read(conn: Any, ctx: Any,
                              round_object_id: str) -> bool:
    """The company domain currently grants the actor read AND the actor is
    the CEO whose stored round ``ceo_assignment_id/ceo_principal_id`` is
    their own.  This is the only "history after revocation" path."""
    snap = _round_definition_row(conn, ctx.scope_id, round_object_id)
    if snap is None:
        return False
    state = _round_state_row(conn, ctx.scope_id, round_object_id)
    company_domain_id = (state or {}).get("company_domain_id") or snap["payload"].get(
        "company_domain_id")
    if not company_domain_id:
        return False
    try:
        allowed = db.authorize_domain(conn, ctx, str(company_domain_id), "read")
    except GovernedError:
        return False
    ceo_aid = snap["payload"].get("ceo_assignment_id")
    ceo_pid = snap["payload"].get("ceo_principal_id")
    if not (ceo_aid and ceo_pid):
        return False
    if str(ceo_pid) != ctx.principal_id:
        return False
    return any(str(item.get("assignment_id") or "") == str(ceo_aid)
               for item in (allowed or []))


def authorize_receipt(conn: Any, ctx: Any, receipt: dict[str, Any]) -> None:
    """A2 receipt replay authorization.  Preserves the immutable receipt.

    Algorithm:

    * Every referenced material is checked against current rights: the
      ``target_object_id`` (when present), every entry in
      ``object_versions``, and every id in
      ``result.referenced_object_ids``.  The check uses
      :func:`visible_object` / :func:`visible_revision` (same-domain first,
      then the bounded A2-18 exception).  A2 source receipts (create_object
      / propose_revision) typically have NO round anchor; they pass when
      every referenced material is currently readable.

    * For the six A2 composition actions the actor must also EITHER hold a
      current Round slot (exact stored assignment+principal, live
      assignment validity, role+domain match, and the exact assignment
      among the live ``authorize_domain`` allowed set) OR currently have
      company-domain read authority AND be the stored CEO.  The mere
      historical actor id is not sufficient: a revoked signer is denied.

    * No short-circuit: the first unreadable reference denies the whole
      replay, even if some earlier visible reference already passed.
    """
    referenced = (receipt.get("result") or {}).get("referenced_object_ids") or []
    object_versions = receipt.get("object_versions") or []
    target = receipt.get("target_object_id")
    action_type = receipt.get("action_type")

    # Always authorize the immutable references against current rights.
    if target:
        if not _authorize_object(conn, ctx, str(target)):
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
    for item in object_versions:
        if not isinstance(item, dict):
            continue
        oid = _uuid_str(item.get("object_id"))
        if oid is None:
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
        rid = _uuid_str(item.get("revision_id"))
        if not _authorize_object(conn, ctx, oid, rid):
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
    for item in referenced:
        oid = _uuid_str(item)
        if oid is None:
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
        if not _authorize_object(conn, ctx, oid):
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)

    # A2 source actions (create_object / propose_revision) have NO round
    # anchor; the references above already authorized every visible piece.
    if action_type not in _A2_ACTION_TYPES:
        return

    round_object_id = _receipt_anchor_round(conn, ctx, receipt)
    if round_object_id is None:
        # No live Round anchor.  Source actions pass on reference authz
        # alone; composition receipts without a round anchor are denied
        # because the receipt is no longer grounded in a live composition.
        return

    slot = _actor_current_slot(conn, ctx, round_object_id)
    if slot is None:
        # Company-CEO history exception: a CEO with current company-domain
        # read whose stored ceo_assignment_id/ceo_principal_id still points
        # to them may replay their own historical receipts even after other
        # signers were revoked.
        if not _company_ceo_current_read(conn, ctx, round_object_id):
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
    else:
        if not _slot_has_current_read_policy(conn, ctx, slot):
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)


# ---------------------------------------------------------------------------
# internal routing helper
# ---------------------------------------------------------------------------


def is_a2_object(conn: Any, ctx: Any, oid: str) -> bool:
    """Internal routing helper: ``True`` iff this object is one of the seven
    A2 object types AND its current binding is Contract-A.

    Returns ``False`` for missing, foreign-scope, non-A2, or legacy-bound
    objects without raising.  Never expose the boolean via a public route —
    the public read endpoints always go through :func:`visible_object` /
    :func:`visible_revision`, which enforce full access policy.
    """
    target = _uuid_str(oid)
    if target is None:
        return False
    head = _load_head(conn, ctx.scope_id, target)
    if head is None or head.get("object_type") not in A2_OBJECT_TYPES:
        return False
    binding = protocol.current_binding(conn, ctx.scope_id, target)
    if binding is None:
        return False
    return binding.get("protocol_id") == CONTRACT_A_PROTOCOL_ID


# ---------------------------------------------------------------------------
# relations view
# ---------------------------------------------------------------------------


def _a2_anchor_refs(definition: dict[str, Any] | None,
                    manifest: dict[str, Any] | None,
                    submission_payload: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Materialize A2 anchor source references as ``(object_id, revision_id)``."""
    refs: list[tuple[str, str]] = []

    def _take(d: dict[str, Any] | None, key: str) -> None:
        if not isinstance(d, dict):
            return
        value = d.get(key)
        if isinstance(value, dict) and value.get("object_id") and value.get("revision_id"):
            refs.append((str(value["object_id"]), str(value["revision_id"])))

    _take(definition, "company_reference_ref")
    if isinstance(manifest, dict):
        _take(manifest, "company_reference_ref")
        members = manifest.get("members")
        if isinstance(members, list):
            for member in members:
                if isinstance(member, dict):
                    _take(member, "submission_ref")
        deps = manifest.get("binding_dependencies")
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, dict):
                    _take(dep, "source_ref")
    if isinstance(submission_payload, dict):
        for upstream in submission_payload.get("upstream_refs") or []:
            if isinstance(upstream, dict) and upstream.get("object_id") and upstream.get("revision_id"):
                refs.append((str(upstream["object_id"]), str(upstream["revision_id"])))
        for binding in submission_payload.get("bindings") or []:
            if isinstance(binding, dict):
                _take(binding, "source_ref")
    return list(dict.fromkeys(refs))


def relations(conn: Any, ctx: Any, object_id: str,
              revision_id: str | None = None,
              limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    """A2 anchor relations view.

    Matches :func:`workbench.relations` response schema:
    ``{"source_ref", "items", "next_cursor", "protocol"}``; each item is
    ``{"relation_type", "source_ref", "target_ref", "target_type"}``.  The
    anchor authorization uses :func:`visible_object` so the same-domain read
    path runs first, then the A2-18 exception is evaluated — preserving the
    regular 404 boundary.  Non-A2 anchors delegate to the workbench helper
    unchanged.  For A2 anchors the edges are the source references declared
    by the Round / Composition / Submission payload; only currently readable
    endpoints are surfaced (hidden counts are never returned).
    """
    oid = _uuid_str(object_id)
    if oid is None:
        raise GovernedError("NOT_FOUND")
    head = _load_head(conn, ctx.scope_id, oid)
    if head is None or head.get("object_type") not in A2_OBJECT_TYPES:
        return workbench.relations(conn, ctx, object_id, revision_id, limit, cursor)

    head_view = visible_object(conn, ctx, oid)
    head_metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
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
    source = visible_revision(conn, ctx, head["object_id"], str(source_rid))
    source_ref = {"object_id": str(head["object_id"]),
                  "revision_id": str(source["revision_id"])}
    cursor_key = workbench.decode_cursor(cursor, "relations", ctx, dict(source_ref))
    if cursor_key is not None:
        if not isinstance(cursor_key, list) or len(cursor_key) != 2 or not all(
                isinstance(value, str) and _uuid_str(value) == value for value in cursor_key):
            raise GovernedError("INVALID_REQUEST", "Invalid relation cursor.", status=422)

    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    if head["object_type"] == "FormationRound":
        definition, manifest, submission = payload, None, None
    elif head["object_type"] == "CompanyComposition":
        definition, manifest, submission = None, payload, None
    elif head["object_type"] == "DomainSubmission":
        definition, manifest, submission = None, None, payload
    else:
        definition, manifest, submission = None, None, None
    edges = sorted(set(_a2_anchor_refs(definition, manifest, submission)))

    visible: list[dict[str, Any]] = []
    for tgt_oid, tgt_rid in edges:
        if cursor_key is not None and (tgt_oid, tgt_rid) <= tuple(cursor_key):
            continue
        try:
            tgt_head = _load_head(conn, ctx.scope_id, tgt_oid)
            if tgt_head is None:
                continue
            # Native same-domain read OK; otherwise check publication for
            # source types, or participant visibility for composition types.
            try:
                db.authorize_domain(conn, ctx, str(tgt_head["domain_id"]), "read")
            except GovernedError:
                if tgt_head.get("object_type") == "EvidenceAsset":
                    continue
                if tgt_head.get("object_type") in A2_SOURCE_TYPES:
                    actor_domains = _actor_current_read_domains(conn, ctx)
                    if not _source_revision_published_to(
                            conn, ctx.scope_id, tgt_oid, tgt_rid, actor_domains):
                        continue
                else:
                    # Composition target: require current Round participant.
                    tgt_round_id = _round_id_for(
                        tgt_head,
                        _load_revision(conn, ctx.scope_id, tgt_oid, tgt_rid) or {},
                    )
                    if tgt_round_id is None:
                        continue
                    tgt_slot = _actor_current_slot(conn, ctx, tgt_round_id)
                    if tgt_slot is None or not _slot_has_current_read_policy(
                            conn, ctx, tgt_slot):
                        continue
            # Endpoint must currently support read interpretation.
            protocol.require_read_support(conn, ctx.scope_id, tgt_oid)
            # Authorize the specific target revision through visible_revision
            # so a private newer head never substitutes for a published R1.
            visible_revision(conn, ctx, tgt_oid, tgt_rid)
        except GovernedError:
            continue
        visible.append({
            "relation_type": "source_reference",
            "source_ref": source_ref,
            "target_ref": {"object_id": tgt_oid, "revision_id": tgt_rid},
            "target_type": tgt_head["object_type"],
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
