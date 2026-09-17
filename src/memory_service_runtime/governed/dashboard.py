"""Local business dashboard read models (``tkos.dashboard/0.1``).

The dashboard is a *read* surface over the existing governed runtime: it never
creates business objects, receipts, reviews, Context snapshots or tasks.  Every
projection here runs inside the existing ``db.transaction`` fence and reuses
``method_access.head_access`` / ``protocol.require_read_support`` so historical
reads always use current authority and unsupported registrations stay explicit
instead of being given a guessed legacy meaning.

Semantics deliberately kept from the frozen contracts:

* The navigation is a typed relationship hierarchy
  (Strategy -> Architecture -> LTCO/PCO -> Mission -> Operating objects), not a
  progress bar.  A group is only populated from exact, recorded references.
* Every traversal keeps the *exact* object + revision + payload hash it read.
  A Mission's registered contract names its parent PCO field explicitly
  (``pco_ref`` for 0.1-0.3, ``parent_pco_ref`` for 0.4); the recorded PCO
  revision is evaluated exactly even after the PCO head moves to a newer
  revision, and a payload is never read through another contract's field.
* Still-effective targets based on an older Strategy are reported with
  ``basis.status = "historical"`` and their own recorded Strategy reference;
  they are never re-attached to the currently selected Strategy, and a Strategy
  of another independent business domain is ``unrelated``, not historical.
* A topic-only BusinessFact has no strategy relation and is reported as
  ``unattached``; it is never attached to a Mission by unit or name.
* ``latest`` and ``effective`` revisions stay separate.  A candidate revision
  never becomes the formal state; formal state and content confirmation are
  derived from records covering the *selected exact revision*.
* Unrecorded or unreadable projections are explicit ``missing`` objects with a
  reason; non-authorized references are omitted entirely (no hidden counts) and
  hash-inconsistent or infrastructure-failing references raise instead of being
  presented as usable relationships.
* Names are resolved only for principals/assignments named by an authorized
  object payload.  Every entry carries the exact object/revision/payload hash
  and the server read time.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any, Callable

from . import db, method_access as access, method_readers, readers, protocol, workbench
from .errors import GovernedError

SCHEMA_VERSION = "tkos.dashboard/0.1"
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

OPERATING_TYPES = ("OperatingState", "BusinessFact", "PeriodReview", "OperatingProblem")
GROUP_TYPES: dict[str, tuple[str, ...]] = {
    "strategy": ("Strategy",),
    "architecture": ("StrategicArchitecture",),
    "ltco": ("LTCO",),
    "pco": ("PCO",),
    "mission": ("Mission",),
    "operating": OPERATING_TYPES,
}
GROUPS = tuple(GROUP_TYPES)
BASES = ("current", "historical", "unattached", "all")

# Human confirmation review kinds.  These are records, not a permission claim.
# ``agent_issue_initiation`` (0.3) is deliberately absent: it is the bound CEO
# Agent's formal initiation act, not a human confirmation.  ``tkos.method/0.4``
# writes the human CEO's candidate-set decision under ``candidate_set_activation``
# and records Agreement signer confirmations/formalization under
# ``agreement_confirmation``/``agreement_formalized``; those kinds only exist in
# the 0.4 registry, so naming them here cannot change any 0.1-0.3 projection.
CONFIRMATION_REVIEW_KINDS = frozenset({
    "strategy_update_confirmation", "ltco_confirmation", "candidate_set_confirmation",
    "architecture_confirmation", "state_confirmation", "problem_closure",
    "strategic_agreement_confirmation", "meeting_minutes_confirmation",
    "strategic_issue_confirmation", "brief_sufficiency",
    "candidate_set_activation", "agreement_confirmation", "agreement_formalized",
})

# Reference errors that degrade to an explicit unavailable relationship.
# Anything else (SQL failure, protocol conflict, bug) must not be hidden.
_UNAVAILABLE_CODES = frozenset({"NOT_FOUND", "FORBIDDEN", "PROTOCOL_NOT_SUPPORTED"})

# Typed inbound (downstream) reference fields.  Only these recorded top-level
# fields are searched; no free JSON scan and no name/unit inference.  The map
# covers every registered Method payload model so the full type directory —
# not just the six navigation groups — has usable recorded adjacency.
DOWNSTREAM_FIELDS: dict[str, tuple[str, ...]] = {
    "Strategy": ("source_agreement_ref", "source_proposal_ref"),
    "StrategicArchitecture": ("strategy_ref", "source_agreement_ref", "source_proposal_ref"),
    "StrategicJudgment": ("strategy_ref", "source_agreement_ref", "source_proposal_ref"),
    "LTCO": ("strategy_ref", "architecture_ref", "advice_ref", "baseline_refs"),
    "PCO": ("strategy_ref", "ltco_ref", "parent_ltco_ref", "architecture_ref"),
    "Mission": ("pco_ref", "parent_pco_ref", "architecture_ref", "evidence_refs"),
    "OperatingState": ("subject_ref", "baseline_refs", "evidence_refs"),
    "OperatingProblem": ("state_ref", "evidence_refs"),
    "BusinessFact": ("subject_ref", "corrects_ref", "source_ref"),
    "PeriodReview": ("state_refs", "target_refs", "fact_refs"),
    "LTCOReviewAdvice": ("period_review_ref", "strategy_ref", "ltco_ref"),
    "ReviewWindow": ("strategy_ref", "architecture_ref", "ltco_ref", "ltco_refs",
                     "target_refs", "pco_refs", "mission_refs", "previous_window_ref"),
    "CandidateSet": ("window_ref", "strategy_ref", "architecture_ref", "ltco_ref", "ltco_refs",
                     "target_refs"),
    "Signal": ("source_refs",),
    "PotentialIssue": ("signal_refs", "source_refs"),
    "StrategicIssue": ("potential_issue_ref", "direct_source_refs", "source_refs",
                       "reframe_of_ref", "strategy_ref", "architecture_ref"),
    "ResearchMemo": ("issue_ref", "source_refs"),
    "ResearchPlan": ("issue_ref", "memo_ref"),
    "ResearchReport": ("issue_ref", "plan_ref", "evidence_refs"),
    "ResearchBrief": ("issue_ref", "source_refs"),
    "MeetingRound": ("issue_ref", "report_ref", "brief_ref", "material_refs"),
    "MeetingMinutes": ("issue_ref", "meeting_ref", "source_refs"),
    "StrategicAgreement": ("issue_ref", "meeting_ref", "minutes_ref", "evidence_refs"),
    "StrategyUpdateProposal": ("issue_ref", "agreement_ref"),
}
DOWNSTREAM_ARRAY_FIELDS = frozenset({
    "state_refs", "target_refs", "fact_refs", "source_refs", "signal_refs",
    "direct_source_refs", "evidence_refs", "baseline_refs", "material_refs",
    "ltco_refs", "pco_refs", "mission_refs",
})
DOWNSTREAM_LIMIT = 25
DOWNSTREAM_MAX_LIMIT = 100


def _missing(reason: str, *, field: str | None = None) -> dict[str, Any]:
    return {"status": "missing", "reason": reason, "value": None, **({"field": field} if field else {})}


def _ref(object_id: Any, revision_id: Any, payload_hash: Any = None) -> dict[str, Any]:
    value = {"object_id": str(object_id), "revision_id": str(revision_id)}
    if payload_hash is not None:
        value["payload_hash"] = str(payload_hash)
    return value


def _same_ref(left: Any, right: Any) -> bool:
    """Exact identity: object and revision must both match. Hash is not identity."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return (str(left.get("object_id")) == str(right.get("object_id"))
            and str(left.get("revision_id")) == str(right.get("revision_id")))


def _invalid(message: str = "The request does not satisfy the dashboard read schema.") -> GovernedError:
    return GovernedError("INVALID_REQUEST", message, status=422)


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time must carry a timezone")
    return parsed


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _read_at(conn: Any) -> str:
    return db.jsonable(conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"])


def _method_state(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s",
                       (ctx.scope_id, str(object_id))).fetchone()
    return db.jsonable(row["state"]) if row and isinstance(row["state"], dict) else {}


def _domain_name(conn: Any, ctx: Any, domain_id: Any) -> str | None:
    row = conn.execute("SELECT name FROM gov_domains WHERE scope_id=%s AND domain_id=%s",
                       (ctx.scope_id, str(domain_id))).fetchone()
    return row["name"] if row else None


# --------------------------------------------------------------------------
# Authorized reads of exact references
# --------------------------------------------------------------------------

def _visible_head(conn: Any, ctx: Any, object_id: str) -> tuple[dict[str, Any], set[str] | None]:
    # Linked tkos.workspace/0.2 source artifacts stay behind their scene fence
    # even on dashboard paths that read Method bindings directly. Unlinked
    # objects pass through unchanged.
    from . import workspace_v02_guard
    workspace_v02_guard.enforce_object(conn, ctx, str(object_id))
    head, allowed = access.head_access(conn, ctx, str(object_id))
    protocol.require_read_support(conn, ctx.scope_id, head["object_id"])
    return head, allowed


def _visible_revision(conn: Any, ctx: Any, object_id: str, revision_id: str,
                      allowed: set[str] | None) -> dict[str, Any]:
    if allowed is not None and str(revision_id) not in allowed:
        raise GovernedError("NOT_FOUND")
    return access.revision(conn, ctx, object_id, revision_id)


def _load_ref(conn: Any, ctx: Any, ref: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Load the *exact* referenced revision; None only when unavailable/unauthorized.

    A recorded reference that carries a ``payload_hash`` must still match the
    immutable revision.  A mismatch is an inconsistent relationship: it is
    rejected here and never presented as a usable link.  Infrastructure
    failures are not caught and therefore cannot be mistaken for "missing".
    """
    if not isinstance(ref, dict) or not ref.get("object_id") or not ref.get("revision_id"):
        return None
    try:
        head, allowed = _visible_head(conn, ctx, str(ref["object_id"]))
        revision = _visible_revision(conn, ctx, head["object_id"], str(ref["revision_id"]), allowed)
    except GovernedError as exc:
        if exc.code in _UNAVAILABLE_CODES:
            return None
        raise
    if ref.get("payload_hash") is not None and str(ref["payload_hash"]) != str(revision["payload_hash"]):
        return None
    return head, revision


def _try_ref(conn: Any, ctx: Any, ref: Any) -> dict[str, Any] | None:
    """Resolve an exact reference under current authority; None when unusable.

    The caller already knows the reference because it is part of an authorized
    payload; a uniform None never discloses whether the target exists.
    """
    loaded = _load_ref(conn, ctx, ref)
    if loaded is None:
        return None
    head, revision = loaded
    value = _ref(head["object_id"], revision["revision_id"], revision["payload_hash"])
    value["object_type"] = head["object_type"]
    value["lifecycle_status"] = head["lifecycle_status"]
    value["title"] = _payload_title(revision["payload"])
    value["domain_id"] = str(head["domain_id"])
    if ref.get("payload_hash") is not None:
        value["payload_hash_matches"] = True
    return value


def _payload_title(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("title", "core_question", "metric"):
            if isinstance(payload.get(key), str):
                return payload[key]
    return None


# --------------------------------------------------------------------------
# Strategy selection
# --------------------------------------------------------------------------

def strategy_choices(conn: Any, ctx: Any) -> list[dict[str, Any]]:
    """Readable current formal Strategy heads, one per business domain.

    The head row is the server-side formal pointer; an object that is not
    currently readable under the caller's authority is omitted (no count).
    """
    rows = conn.execute(
        """SELECT h.domain_id, h.object_id, h.revision_id, r.recorded_at AS head_recorded_at,
                  d.name AS domain_name
             FROM gov_method_strategy_heads h
             LEFT JOIN gov_domains d ON (d.scope_id, d.domain_id) = (h.scope_id, h.domain_id)
             LEFT JOIN gov_action_receipts r ON (r.scope_id, r.receipt_id) = (h.scope_id, h.action_id)
            WHERE h.scope_id=%s ORDER BY h.domain_id""",
        (ctx.scope_id,)).fetchall()
    choices: list[dict[str, Any]] = []
    for row in rows:
        try:
            head, allowed = _visible_head(conn, ctx, str(row["object_id"]))
            if head["object_type"] != "Strategy":
                continue
            if head["effective_revision_id"] is None or str(head["effective_revision_id"]) != str(row["revision_id"]):
                continue
            revision = _visible_revision(conn, ctx, head["object_id"], str(row["revision_id"]), allowed)
        except GovernedError as exc:
            if exc.code in _UNAVAILABLE_CODES:
                continue
            raise
        metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
        choices.append(db.jsonable({
            "strategy_id": head["object_id"], "revision_id": revision["revision_id"],
            "payload_hash": revision["payload_hash"], "domain_id": head["domain_id"],
            "domain_name": row["domain_name"], "title": _payload_title(revision["payload"]),
            "contract_version": metadata.get("contract_version"),
            "record_origin": metadata.get("record_origin"),
            "formal_source": "gov_method_strategy_heads",
            "head_recorded_at": row["head_recorded_at"],
            "protocol": metadata,
        }))
    return choices


def select_strategy(conn: Any, ctx: Any, strategy_id: str | None) -> dict[str, Any] | None:
    """Resolve the caller's explicit or unambiguous current Strategy."""
    choices = strategy_choices(conn, ctx)
    if not choices:
        return None
    if strategy_id is None:
        if len(choices) == 1:
            return choices[0]
        raise _invalid("Multiple current Strategies are readable; select one explicitly.")
    wanted = str(strategy_id)
    for choice in choices:
        if str(choice["strategy_id"]) == wanted:
            return choice
    raise GovernedError("NOT_FOUND")


# --------------------------------------------------------------------------
# Typed strategy basis (exact-reference traversal)
# --------------------------------------------------------------------------

def _selected_ref(strategy: dict[str, Any] | None) -> dict[str, Any] | None:
    if not strategy:
        return None
    return _ref(strategy["strategy_id"], strategy["revision_id"], strategy.get("payload_hash"))


def _basis(status: str, *, selected: dict[str, Any] | None, strategy_ref: Any = None,
           reason: str | None = None, impact_linked: bool = False,
           basis_revision: str | None = None) -> dict[str, Any]:
    return {"status": status, "reason": reason, "selected_strategy_ref": _selected_ref(selected),
            "strategy_ref": strategy_ref, "impact_linked": impact_linked,
            "basis_revision": basis_revision}


def _impact_linked(conn: Any, ctx: Any, selected: dict[str, Any] | None, object_id: str,
                   revision_id: str | None = None) -> bool:
    if not selected:
        return False
    sql = ("SELECT 1 FROM gov_method_impacts WHERE scope_id=%s AND strategy_object_id=%s"
           " AND strategy_revision_id=%s AND target_object_id=%s")
    params: list[Any] = [ctx.scope_id, str(selected["strategy_id"]),
                         str(selected["revision_id"]), str(object_id)]
    if revision_id is not None:
        sql += " AND target_revision_id=%s"
        params.append(str(revision_id))
    sql += " LIMIT 1"
    return bool(conn.execute(sql, params).fetchone())


def _compare_strategy_ref(conn: Any, ctx: Any, strategy_ref: Any, selected: dict[str, Any] | None,
                          *, impact_linked: bool = False,
                          basis_revision: str | None = None) -> dict[str, Any]:
    """Compare a recorded exact Strategy reference with the selected Strategy.

    Only a *same-domain* Strategy can be this object's historical basis.  A
    Strategy belonging to another independent business domain is reported as
    ``unrelated`` and never grouped as the selected Strategy's history, unless
    the server recorded an explicit impact link.
    """
    if selected is None:
        return _basis("unselected", selected=None,
                      strategy_ref=strategy_ref if isinstance(strategy_ref, dict) else None,
                      reason="explicit_strategy_required", impact_linked=impact_linked,
                      basis_revision=basis_revision)
    if not isinstance(strategy_ref, dict) or not strategy_ref.get("object_id"):
        return _basis("unavailable", selected=selected, reason="strategy_basis_not_recorded",
                      impact_linked=impact_linked, basis_revision=basis_revision)
    selected_ref = _selected_ref(selected)
    if _same_ref(strategy_ref, selected_ref):
        recorded_hash = strategy_ref.get("payload_hash")
        if recorded_hash is None or str(recorded_hash) == str(selected_ref.get("payload_hash")):
            return _basis("current", selected=selected, strategy_ref=strategy_ref,
                          impact_linked=impact_linked, basis_revision=basis_revision)
        # Same object+revision but a recorded hash that does not name the
        # immutable revision: never classify it as the current basis.
        if _load_ref(conn, ctx, strategy_ref) is None:
            return _basis("unavailable", selected=selected, strategy_ref=strategy_ref,
                          reason="strategy_basis_hash_mismatch", impact_linked=impact_linked,
                          basis_revision=basis_revision)
    loaded = _load_ref(conn, ctx, strategy_ref)
    if loaded is None:
        return _basis("unavailable", selected=selected,
                      strategy_ref=_ref(strategy_ref["object_id"], strategy_ref["revision_id"]),
                      reason="strategy_basis_unavailable", impact_linked=impact_linked,
                      basis_revision=basis_revision)
    other, _revision = loaded
    if other["object_type"] != "Strategy":
        return _basis("unavailable", selected=selected, strategy_ref=strategy_ref,
                      reason="strategy_basis_wrong_type", impact_linked=impact_linked,
                      basis_revision=basis_revision)
    if str(other["domain_id"]) != str(selected["domain_id"]) and not impact_linked:
        return _basis("unrelated", selected=selected, strategy_ref=strategy_ref,
                      reason="different_strategy_domain", impact_linked=False,
                      basis_revision=basis_revision)
    return _basis("historical", selected=selected, strategy_ref=strategy_ref,
                  reason="recorded_other_strategy", impact_linked=impact_linked,
                  basis_revision=basis_revision)


def _basis_of_exact_ref(conn: Any, ctx: Any, ref: Any, selected: dict[str, Any] | None,
                        *, visited: frozenset[tuple[str, str]],
                        impact_linked: bool | None = None) -> dict[str, Any]:
    """Basis through one recorded exact reference, preserving its revision."""
    if isinstance(ref, dict) and ref.get("topic") is not None:
        return _basis("unattached", selected=selected, reason="topic_only_subject",
                      impact_linked=bool(impact_linked))
    loaded = _load_ref(conn, ctx, ref)
    if loaded is None:
        return _basis("unavailable", selected=selected,
                      strategy_ref=ref if isinstance(ref, dict) else None,
                      reason="referenced_revision_unavailable", impact_linked=bool(impact_linked))
    head, revision = loaded
    key = (str(head["object_id"]), str(revision["revision_id"]))
    if key in visited:
        return _basis("unavailable", selected=selected,
                      strategy_ref=_ref(head["object_id"], revision["revision_id"]),
                      reason="basis_cycle", impact_linked=bool(impact_linked))
    return _basis_of_revision(conn, ctx, head, revision, selected,
                              visited=visited | {key}, impact_linked=impact_linked)


# The exact parent-PCO field each registered Method contract records on a
# Mission.  0.1-0.3 record ``pco_ref``; 0.4 records ``parent_pco_ref``.  The
# field is selected by the registration's explicit contract version, never by
# which key happens to exist in a payload, so one contract can never borrow
# another contract's reference.
MISSION_PARENT_REF_FIELDS: dict[str, str] = {
    "tkos.method/0.1": "pco_ref",
    "tkos.method/0.2": "pco_ref",
    "tkos.method/0.3": "pco_ref",
    "tkos.method/0.4": "parent_pco_ref",
}


def _mission_parent_ref_field(conn: Any, ctx: Any,
                              head: dict[str, Any]) -> tuple[str | None, str | None]:
    """Parent-PCO payload field from the Mission's explicit registration metadata.

    Returns ``(field, None)`` for a registered contract whose Mission model this
    runtime knows how to read, or ``(None, reason)`` when the registration's
    contract version names no known Mission contract.  An unregistered or
    unknown version is never guessed as legacy from the payload's shape.
    """
    metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    version = metadata.get("contract_version")
    field = MISSION_PARENT_REF_FIELDS.get(version) if isinstance(version, str) else None
    if field is None:
        return None, "mission_contract_version_unsupported"
    return field, None


def _basis_of_revision(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any],
                       selected: dict[str, Any] | None, *, visited: frozenset[tuple[str, str]],
                       impact_linked: bool | None = None) -> dict[str, Any]:
    """Strategy basis of one *exact* authorized revision, from recorded refs only."""
    kind = head["object_type"]
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    if impact_linked is None:
        impact_linked = _impact_linked(conn, ctx, selected, head["object_id"], revision["revision_id"])
    basis_revision = ("effective" if head["effective_revision_id"] is not None
                      and str(head["effective_revision_id"]) == str(revision["revision_id"])
                      else "latest" if head["latest_revision_id"] is not None
                      and str(head["latest_revision_id"]) == str(revision["revision_id"])
                      else "exact_ref")

    if kind == "Strategy":
        if _same_ref(_ref(head["object_id"], revision["revision_id"]), _selected_ref(selected)):
            return _basis("current", selected=selected, strategy_ref=_selected_ref(selected),
                          impact_linked=impact_linked, basis_revision="effective")
        if selected is not None and str(head["domain_id"]) != str(selected["domain_id"]) and not impact_linked:
            return _basis("unrelated", selected=selected,
                          strategy_ref=_ref(head["object_id"], revision["revision_id"]),
                          reason="different_strategy_domain", impact_linked=False,
                          basis_revision=basis_revision)
        return _basis("historical", selected=selected,
                      strategy_ref=_ref(head["object_id"], revision["revision_id"]),
                      reason="recorded_other_strategy", impact_linked=impact_linked,
                      basis_revision=basis_revision)
    if kind in {"StrategicArchitecture", "LTCO", "PCO", "ReviewWindow", "CandidateSet"}:
        return _compare_strategy_ref(conn, ctx, payload.get("strategy_ref"), selected,
                                     impact_linked=impact_linked, basis_revision=basis_revision)
    if kind == "Mission":
        field, reason = _mission_parent_ref_field(conn, ctx, head)
        if field is None:
            return _basis("unavailable", selected=selected, reason=reason,
                          impact_linked=impact_linked, basis_revision=basis_revision)
        return _basis_of_exact_ref(conn, ctx, payload.get(field), selected,
                                   visited=visited, impact_linked=impact_linked)
    if kind == "OperatingState":
        return _basis_of_exact_ref(conn, ctx, payload.get("subject_ref"), selected,
                                   visited=visited, impact_linked=impact_linked)
    if kind == "BusinessFact":
        subject = payload.get("subject_ref")
        if isinstance(subject, dict) and subject.get("topic") is not None:
            return _basis("unattached", selected=selected, reason="topic_only_subject",
                          impact_linked=impact_linked)
        return _basis_of_exact_ref(conn, ctx, subject, selected,
                                   visited=visited, impact_linked=impact_linked)
    if kind == "OperatingProblem":
        return _basis_of_exact_ref(conn, ctx, payload.get("state_ref"), selected,
                                   visited=visited, impact_linked=impact_linked)
    if kind == "PeriodReview":
        targets = payload.get("target_refs") or []
        statuses = set()
        for target in targets:
            child = _basis_of_exact_ref(conn, ctx, target, selected,
                                        visited=visited, impact_linked=impact_linked)
            statuses.add(child["status"])
        if not statuses:
            return _basis("unavailable", selected=selected, reason="review_targets_not_recorded",
                          impact_linked=impact_linked)
        if statuses == {"current"}:
            return _basis("current", selected=selected, impact_linked=impact_linked,
                          basis_revision=basis_revision)
        if statuses == {"historical"}:
            return _basis("historical", selected=selected, reason="recorded_other_strategy",
                          impact_linked=impact_linked, basis_revision=basis_revision)
        if statuses == {"unattached"}:
            return _basis("unattached", selected=selected, reason="topic_only_subject",
                          impact_linked=impact_linked, basis_revision=basis_revision)
        if "current" in statuses and (statuses & {"historical", "unattached"}):
            return _basis("mixed", selected=selected, reason="review_spans_strategy_basis",
                          impact_linked=impact_linked, basis_revision=basis_revision)
        return _basis("unavailable", selected=selected, reason="review_basis_unavailable",
                      impact_linked=impact_linked, basis_revision=basis_revision)
    return _basis("unavailable", selected=selected, reason="type_has_no_strategy_basis",
                  impact_linked=impact_linked)


def authorized_ref(conn: Any, ctx: Any, object_id: str, revision_id: str) -> dict[str, Any]:
    """Authorize one exact object revision for a typed read; 404 when unusable."""
    loaded = _load_ref(conn, ctx, {"object_id": str(object_id), "revision_id": str(revision_id)})
    if loaded is None:
        raise GovernedError("NOT_FOUND")
    head, revision = loaded
    return _ref(head["object_id"], revision["revision_id"])


def _basis_of_object(conn: Any, ctx: Any, object_id: str, selected: dict[str, Any] | None,
                     *, impact_linked: bool | None = None) -> dict[str, Any]:
    """Basis of an object's current effective (else latest) revision."""
    try:
        head, allowed = _visible_head(conn, ctx, object_id)
    except GovernedError as exc:
        if exc.code in _UNAVAILABLE_CODES:
            return _basis("unavailable", selected=selected, reason="object_unavailable")
        raise
    pointer = head["effective_revision_id"] or head["latest_revision_id"]
    if pointer is None:
        return _basis("unavailable", selected=selected, reason="no_readable_revision",
                      impact_linked=bool(impact_linked))
    try:
        revision = _visible_revision(conn, ctx, head["object_id"], str(pointer), allowed)
    except GovernedError as exc:
        if exc.code in _UNAVAILABLE_CODES:
            return _basis("unavailable", selected=selected, reason="revision_unavailable",
                          impact_linked=bool(impact_linked))
        raise
    return _basis_of_revision(conn, ctx, head, revision, selected, visited=frozenset(),
                              impact_linked=impact_linked)


def _basis_matches(basis: dict[str, Any], requested: str) -> bool:
    status = basis.get("status")
    if requested == "current":
        return status == "current"
    if requested == "historical":
        return status in {"historical", "mixed"}
    if requested == "unattached":
        return status == "unattached"
    # "all" is the same-lineage combined view: current + historical + mixed +
    # unattached + unavailable.  An object recorded against another independent
    # Strategy domain is never mixed into the selected Strategy's list.
    return status != "unrelated"


# --------------------------------------------------------------------------
# Responsibility projection
# --------------------------------------------------------------------------

def _principal(conn: Any, ctx: Any, principal_id: Any) -> dict[str, Any] | None:
    """Minimal identity projection for a principal named by an authorized object."""
    row = conn.execute(
        "SELECT principal_id, principal_type, display_name, active FROM gov_principals"
        " WHERE scope_id=%s AND principal_id=%s",
        (ctx.scope_id, str(principal_id))).fetchone()
    return db.jsonable(row) if row else None


def _assignment_rows(conn: Any, ctx: Any, principal_id: Any,
                     domain_id: Any = None) -> list[dict[str, Any]]:
    sql = """SELECT a.assignment_id, a.principal_id, a.domain_id, a.role, a.active,
                    a.valid_from, a.valid_to, p.display_name, p.principal_type,
                    (a.active AND p.active AND a.valid_from <= clock_timestamp()
                     AND (a.valid_to IS NULL OR a.valid_to > clock_timestamp()))
                      AS current,
                    (a.active AND p.active AND a.valid_from > clock_timestamp()) AS future
               FROM gov_role_assignments a
               JOIN gov_principals p ON (p.scope_id, p.principal_id) = (a.scope_id, a.principal_id)
              WHERE a.scope_id=%s AND a.principal_id=%s"""
    params: list[Any] = [ctx.scope_id, str(principal_id)]
    if domain_id is not None:
        sql += " AND a.domain_id=%s"
        params.append(str(domain_id))
    sql += " ORDER BY a.role, a.assignment_id"
    return db.jsonable(conn.execute(sql, params).fetchall())


def _domain_readable(conn: Any, ctx: Any, domain_id: Any) -> bool:
    """Whether the current viewer may see this responsibility domain at all."""
    try:
        db.authorize_domain(conn, ctx, str(domain_id), "read")
        return True
    except GovernedError as exc:
        if exc.code in _UNAVAILABLE_CODES or exc.code == "FORBIDDEN":
            return False
        raise


def _appointment(conn: Any, ctx: Any, principal_id: Any, domain_id: Any = None, *,
                 all_domains: bool = False) -> dict[str, Any]:
    """Current appointments for a principal referenced by an authorized object.

    Responsibility domains follow Method semantics, not object storage:
    a Mission is stored in the company domain while its Owner may hold the
    legitimate DRI appointment in a child business domain.  Unreadable domains
    are never listed (no domain metadata leak) and only the referenced
    principal's assignments are ever queried.  ``future`` (valid_from ahead) is
    distinct from revoked/expired, and an object Owner is never called a DRI.
    """
    rows = _assignment_rows(conn, ctx, principal_id, None if all_domains else domain_id)
    visible = [row for row in rows if _domain_readable(conn, ctx, row["domain_id"])]
    hidden = len(rows) - len(visible)
    if not visible:
        if hidden:
            return {"status": "not_visible", "assignments": [],
                    "reason": "assignment_domains_not_readable", "hidden_domain_count": hidden}
        return {"status": "not_recorded", "assignments": [],
                "reason": "no_assignment_for_referenced_principal"}
    current = [row for row in visible if row["current"]]
    if current:
        return {"status": "current", "assignments": current, "reason": None}
    if any(row["future"] for row in visible):
        return {"status": "future", "assignments": visible, "reason": "appointment_not_started"}
    now = datetime.now(timezone.utc)
    if any(row["active"] and row["valid_to"] is not None and _time(row["valid_to"]) <= now
           for row in visible):
        return {"status": "expired", "assignments": visible, "reason": "appointment_not_current"}
    return {"status": "revoked", "assignments": visible, "reason": "appointment_inactive"}


def _unit_domains(conn: Any, ctx: Any, payload: dict[str, Any]) -> dict[str, str]:
    """Exact Architecture unit -> domain map recorded by this revision."""
    ref = payload.get("architecture_ref")
    loaded = _load_ref(conn, ctx, ref) if isinstance(ref, dict) else None
    if loaded is None:
        return {}
    _head, revision = loaded
    units = revision["payload"].get("units") if isinstance(revision["payload"], dict) else None
    return {str(unit["unit_id"]): str(unit["domain_id"])
            for unit in units or [] if isinstance(unit, dict)
            and unit.get("unit_id") is not None and unit.get("domain_id") is not None}


def _assignment(conn: Any, ctx: Any, assignment_id: Any) -> dict[str, Any] | None:
    """Current assignment projection; it does not imply any Action permission."""
    row = conn.execute(
        """SELECT a.assignment_id, a.principal_id, a.domain_id, a.role, a.active,
                  a.valid_from, a.valid_to, p.display_name, p.principal_type,
                  (a.active AND p.active AND a.valid_from <= clock_timestamp()
                   AND (a.valid_to IS NULL OR a.valid_to > clock_timestamp()))
                    AS current_assignment_active
             FROM gov_role_assignments a
             JOIN gov_principals p ON (p.scope_id, p.principal_id) = (a.scope_id, a.principal_id)
            WHERE a.scope_id=%s AND a.assignment_id=%s""",
        (ctx.scope_id, str(assignment_id))).fetchone()
    return db.jsonable(row) if row else None


def _responsibility_entries(conn: Any, ctx: Any, head: dict[str, Any],
                            payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Owner/DRI/participant projections from recorded fields only.

    ``relation`` names the recorded business relation (mission_owner,
    outcome_dri, participant, responsible, ...).  It does not claim any Action
    permission, and an object Owner is never silently equated with a DRI.
    """
    entries: list[dict[str, Any]] = []
    unit_domains = _unit_domains(conn, ctx, payload)

    def add(relation: str, principal_id: Any, *, outcome_id: Any = None,
            assignment_id: Any = None, domain_id: Any = None,
            all_domains: bool = False) -> None:
        if not principal_id:
            return
        principal = _principal(conn, ctx, principal_id)
        assignment = _assignment(conn, ctx, assignment_id) if assignment_id else None
        if principal is None and assignment is None:
            return
        resolved_domain = domain_id if domain_id is not None else head["domain_id"]
        entries.append(db.jsonable({
            "relation": relation, "outcome_id": outcome_id,
            "principal": principal, "assignment": assignment,
            "assignment_id": str(assignment_id) if assignment_id else None,
            "appointment": _appointment(conn, ctx, principal_id, resolved_domain,
                                        all_domains=all_domains),
        }))

    kind = head["object_type"]
    if isinstance(payload.get("owner_principal_id"), str):
        if kind == "Mission":
            # A Mission is stored in the company domain while its Owner may hold
            # the legitimate current appointment in a child business domain:
            # keep actual visible appointments instead of blaming the storage domain.
            add("mission_owner", payload["owner_principal_id"], all_domains=True)
        else:
            add("owner", payload["owner_principal_id"])
    for outcome in payload.get("unit_outcomes") or []:
        if isinstance(outcome, dict):
            unit_id = str(outcome.get("unit_id")) if outcome.get("unit_id") is not None else ""
            add("outcome_dri", outcome.get("dri_principal_id"), outcome_id=outcome.get("outcome_id"),
                domain_id=unit_domains.get(unit_id))
    for outcome in payload.get("outcomes") or []:
        if isinstance(outcome, dict) and outcome.get("owner_principal_id"):
            add("outcome_owner", outcome["owner_principal_id"], outcome_id=outcome.get("outcome_id"))
    if isinstance(payload.get("map"), dict):
        for unit in payload["map"].get("units") or []:
            if isinstance(unit, dict) and unit.get("owner_principal_id"):
                add("strategy_unit_owner", unit["owner_principal_id"], outcome_id=unit.get("unit_id"),
                    all_domains=True)
    for participant in payload.get("participants") or []:
        if isinstance(participant, str):
            add("participant", participant, all_domains=True)
        elif isinstance(participant, dict) and participant.get("principal_id"):
            add("participant", participant["principal_id"],
                assignment_id=participant.get("assignment_id"), all_domains=True)
    if kind == "OperatingProblem" and payload.get("responsible_assignment_id"):
        assignment = _assignment(conn, ctx, payload["responsible_assignment_id"])
        if assignment is not None:
            principal_id = assignment["principal_id"]
            entries.append(db.jsonable({
                "relation": "responsible", "outcome_id": None,
                "principal": _principal(conn, ctx, principal_id),
                "assignment": assignment,
                "assignment_id": str(payload["responsible_assignment_id"]),
                "appointment": _appointment(conn, ctx, principal_id, assignment["domain_id"]),
            }))
    return entries


# Responsibility relations shown in lists and used by the owner filter.  A
# Mission participant is deliberately not a responsibility relation, and no
# State owner is invented when none is recorded.
LIST_RESPONSIBILITY_RELATIONS = ("owner", "mission_owner", "outcome_dri",
                                 "outcome_owner", "responsible")


def _list_responsibility(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry["relation"] in LIST_RESPONSIBILITY_RELATIONS]


def _owner_entry(entries: list[dict[str, Any]]) -> Any:
    for entry in entries:
        if entry["relation"] in {"owner", "mission_owner", "outcome_owner"}:
            return entry
    return _missing("owner_not_recorded")


def _dri_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if entry["relation"] == "outcome_dri"]


# --------------------------------------------------------------------------
# Business content projection
# --------------------------------------------------------------------------

def _normalised_business(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Typed business view over the exact payload.  It adds no interpretation."""
    period = payload.get("period") if isinstance(payload.get("period"), dict) else None
    deadline = None
    for key, kind_hint in (("hard_deadline", "hard"), ("decision_deadline", "decision"),
                           ("feedback_deadline", "feedback")):
        if payload.get(key):
            deadline = {"kind": kind_hint, "value": payload[key]}
            break
    outcomes = []
    for key in ("outcomes", "unit_outcomes"):
        for outcome in payload.get(key) or []:
            if not isinstance(outcome, dict):
                continue
            outcomes.append({
                "outcome_id": outcome.get("outcome_id"), "unit_id": outcome.get("unit_id"),
                "title": outcome.get("title"), "result_statement": outcome.get("result_statement"),
                "criteria": outcome.get("criteria") or [],
                "dri_principal_id": outcome.get("dri_principal_id"),
                "owner_principal_id": outcome.get("owner_principal_id"),
            })
    units = [unit for unit in (payload.get("units") or []) if isinstance(unit, dict)]
    if isinstance(payload.get("map"), dict):
        units.extend(unit for unit in (payload["map"].get("units") or []) if isinstance(unit, dict))
    supports = []
    for support in payload.get("supports") or []:
        if not isinstance(support, dict):
            continue
        outcome_ref = support.get("outcome_ref") or {}
        supports.append({"outcome_id": outcome_ref.get("outcome_id"),
                         "outcome_ref": outcome_ref,
                         "contribution": support.get("contribution")})
    return db.jsonable({
        "title": _payload_title(payload),
        "statement": payload.get("statement"),
        "summary": payload.get("summary") or payload.get("description") or payload.get("analysis"),
        "definition": payload.get("definition"),
        "boundary": payload.get("boundary"),
        "deliverable": payload.get("deliverable"),
        "acceptance_criteria": payload.get("acceptance_criteria") or [],
        "period": period,
        "deadline": deadline,
        "outcomes": outcomes,
        "units": units,
        "supports": supports,
        "core_question": payload.get("core_question"),
        "why_material": payload.get("why_material"),
        "level": payload.get("level"),
        "importance": payload.get("importance"),
        "rag": payload.get("rag"),
        "as_of": payload.get("as_of"),
        "data_gaps": payload.get("data_gaps") or [],
        "findings": payload.get("findings") or [],
        "learnings": payload.get("learnings") or [],
        "implications": payload.get("implications") or [],
        "observations": payload.get("observations") or [],
        "recommendation": payload.get("recommendation"),
        "metric": payload.get("metric"),
        "value": payload.get("value"),
        "unit": payload.get("unit"),
        "correction": ({"corrects_ref": payload.get("corrects_ref"),
                        "correction_reason": payload.get("correction_reason")}
                       if payload.get("corrects_ref") else None),
        "raw": payload,
    })


# --------------------------------------------------------------------------
# Confirmation provenance
# --------------------------------------------------------------------------

def _review_rows(conn: Any, ctx: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    rows = conn.execute(sql, params).fetchall()
    return db.jsonable(rows)


def _review_record(conn: Any, ctx: Any, row: dict[str, Any], *, covered_refs: list[Any] | None,
                   source: str, selected: dict[str, Any]) -> dict[str, Any] | None:
    target = {"object_id": row["target_object_id"], "revision_id": row["target_revision_id"]}
    try:
        if _load_ref(conn, ctx, target) is None:
            return None
    except GovernedError:
        return None
    covered = [_ref(ref["object_id"], ref["revision_id"], ref.get("payload_hash"))
               for ref in (covered_refs or []) if isinstance(ref, dict)
               and ref.get("object_id") and ref.get("revision_id")]
    covers_selected = (_same_ref(target, selected)
                       or any(_same_ref(ref, selected) for ref in covered))
    receipt = None
    if row.get("action_id"):
        try:
            receipt_row = conn.execute(
                "SELECT * FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s",
                (ctx.scope_id, str(row["action_id"]))).fetchone()
            if receipt_row is not None:
                receipt_data = db.jsonable(receipt_row)
                readers.authorize_receipt(conn, ctx, receipt_data)
                receipt = {"receipt_id": receipt_data.get("receipt_id"),
                           "action_type": receipt_data.get("action_type"),
                           "recorded_at": receipt_data.get("recorded_at")}
        except GovernedError:
            receipt = None
    return db.jsonable({
        "record_id": row["record_id"], "kind": row["kind"],
        "principal_id": row["principal_id"], "recorded_at": row["recorded_at"],
        "principal": _principal(conn, ctx, row["principal_id"]),
        "target_ref": target,
        "target_revision_id": row["target_revision_id"],
        "covered_refs": covered,
        "covers_selected_revision": covers_selected,
        "applies_to_selected_revision": _same_ref(target, selected),
        "human_confirmation": row["kind"] in CONFIRMATION_REVIEW_KINDS,
        "source": source,
        "content": row["content"],
        "receipt": receipt,
    })


def _review_covered_refs(row: dict[str, Any]) -> list[Any] | None:
    """Exact references one review records as covered content.

    ``candidate_set_confirmation`` (0.1-0.3) names the confirmed members in
    ``target_refs``; the 0.4 ``candidate_set_activation`` decision names the same
    exact member revisions in ``responsibilities`` instead.  Only that kind may
    use the 0.4 field, so a legacy record can never borrow coverage from it and
    coverage still requires the exact object+revision the content records.
    """
    content = row.get("content") if isinstance(row.get("content"), dict) else {}
    covered = content.get("target_refs")
    if row.get("kind") == "candidate_set_activation":
        responsibilities = content.get("responsibilities")
        if isinstance(responsibilities, list):
            covered = [*(covered if isinstance(covered, list) else []), *responsibilities]
    return covered if isinstance(covered, list) else None


def _covering_confirmations(conn: Any, ctx: Any, head: dict[str, Any],
                            revision: dict[str, Any]) -> list[dict[str, Any]]:
    """All authorized records that can confirm this exact object revision.

    Follows the recorded server-side pointers (``confirmation_record_id``,
    ``confirmed_candidate_ref``, proposal/agreement refs) instead of assuming
    every confirmation is stored against the member object itself.
    """
    object_id = head["object_id"]
    selected = _ref(object_id, revision["revision_id"], revision["payload_hash"])
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    state = _method_state(conn, ctx, object_id)
    records: dict[str, dict[str, Any]] = {}

    def consider(row: dict[str, Any], *, covered_refs=None, source: str) -> None:
        record = _review_record(conn, ctx, row, covered_refs=covered_refs, source=source,
                                selected=selected)
        if record is not None and record["record_id"] not in records:
            records[record["record_id"]] = record

    for row in method_readers.review_records(conn, ctx, object_id)["items"]:
        consider(db.jsonable(row), source="object_local")

    candidate_ref = state.get("confirmed_candidate_ref")
    if isinstance(candidate_ref, dict) and candidate_ref.get("object_id"):
        rows = _review_rows(conn, ctx,
                            "SELECT * FROM gov_method_reviews WHERE scope_id=%s"
                            " AND target_object_id=%s AND target_revision_id=%s"
                            " ORDER BY recorded_at, record_id",
                            [ctx.scope_id, str(candidate_ref["object_id"]),
                             str(candidate_ref["revision_id"])])
        for row in rows:
            consider(row, covered_refs=_review_covered_refs(row),
                     source="confirmed_candidate_set")

    record_id = state.get("confirmation_record_id")
    if record_id and str(record_id) not in records:
        rows = _review_rows(conn, ctx,
                            "SELECT * FROM gov_method_reviews WHERE scope_id=%s AND record_id=%s",
                            [ctx.scope_id, str(record_id)])
        for row in rows:
            consider(row, covered_refs=_review_covered_refs(row),
                     source="confirmation_record")

    for key in ("source_proposal_ref", "source_agreement_ref"):
        ref = payload.get(key)
        if not isinstance(ref, dict) or not ref.get("object_id"):
            continue
        rows = _review_rows(conn, ctx,
                            "SELECT * FROM gov_method_reviews WHERE scope_id=%s"
                            " AND target_object_id=%s AND target_revision_id=%s"
                            " ORDER BY recorded_at, record_id",
                            [ctx.scope_id, str(ref["object_id"]), str(ref["revision_id"])])
        for row in rows:
            # ``changed_refs`` names the exact content versions this update
            # confirmation covers (e.g. the resulting Strategy revision).
            covered = (row.get("content") or {}).get("changed_refs")
            consider(row, covered_refs=covered if isinstance(covered, list) else None,
                     source=key)

    # 0.1 records the strategic-issue confirmation against the source
    # PotentialIssue, whose state then points at the exact issue revision it
    # created; that pointer — not the target alone — decides coverage.
    potential_ref = payload.get("potential_issue_ref")
    if isinstance(potential_ref, dict) and potential_ref.get("object_id"):
        potential_state = _method_state(conn, ctx, potential_ref["object_id"])
        created = potential_state.get("strategic_issue_ref")
        rows = _review_rows(conn, ctx,
                            "SELECT * FROM gov_method_reviews WHERE scope_id=%s"
                            " AND target_object_id=%s AND target_revision_id=%s"
                            " ORDER BY recorded_at, record_id",
                            [ctx.scope_id, str(potential_ref["object_id"]),
                             str(potential_ref["revision_id"])])
        for row in rows:
            consider(row,
                     covered_refs=[created] if isinstance(created, dict) else None,
                     source="potential_issue_ref")

    return sorted(records.values(), key=lambda item: (str(item["recorded_at"]), str(item["record_id"])))


def _candidate_authority(record: dict[str, Any] | None) -> str:
    """Action that made the candidate set effective, from the covering record."""
    return ("m1b_activate_candidates"
            if record is not None and record.get("kind") == "candidate_set_activation"
            else "m1b_confirm_candidates")


def _formal_state(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any],
                  confirmations: list[dict[str, Any]]) -> dict[str, Any]:
    """Formal state for the *selected exact revision*.

    Confirmed content stays confirmed when a later proposal changes the mutable
    ``phase``; historical content never borrows another revision's confirmation,
    the current Problem disposition, or the object's current lifecycle phase.
    ``formal`` (the revision carries business efficacy now) and
    ``human_approved`` (a human confirmation covers this exact content) are
    projected separately.
    """
    kind = head["object_type"]
    state = _method_state(conn, ctx, head["object_id"])
    selected = _ref(head["object_id"], revision["revision_id"], revision["payload_hash"])
    effective = (head["effective_revision_id"] is not None
                 and str(head["effective_revision_id"]) == str(revision["revision_id"]))
    current = (head["latest_revision_id"] is not None
               and str(head["latest_revision_id"]) == str(revision["revision_id"]))

    def recorded_status() -> str:
        # Only the current revision may show the object's mutable phase.
        if current:
            return state.get("phase") or "not_recorded"
        return "historical"

    confirmed_by_record = next((record for record in confirmations
                                if record["covers_selected_revision"] and record["human_confirmation"]),
                               None)
    if kind == "Strategy":
        row = conn.execute("SELECT object_id, revision_id FROM gov_method_strategy_heads"
                           " WHERE scope_id=%s AND domain_id=%s",
                           (ctx.scope_id, str(head["domain_id"]))).fetchone()
        active = bool(row and _same_ref(_ref(row["object_id"], row["revision_id"]), selected))
        return {"status": "effective" if active else "superseded",
                "formal": active, "authority": "gov_method_strategy_heads",
                "applies_to_ref": selected if active else None,
                "current_ref": _ref(row["object_id"], row["revision_id"]) if row else None,
                "content_confirmation": confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"],
                "note": "Content confirmation stays separate from the effective Strategy pointer."}
    if kind == "StrategicArchitecture":
        formal = bool(effective and confirmed_by_record)
        return {"status": "confirmed" if formal else recorded_status(),
                "formal": formal, "authority": "method_confirm_architecture",
                "applies_to_ref": selected if formal else None,
                "content_confirmation": confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"]}
    if kind in {"LTCO", "PCO", "Mission"}:
        formal = bool(effective and confirmed_by_record)
        return {"status": "confirmed" if formal else recorded_status(),
                "formal": formal,
                "authority": ("m1b_confirm_ltco" if kind == "LTCO"
                              else _candidate_authority(confirmed_by_record)),
                "applies_to_ref": selected if formal else None,
                "confirmation_record_id": (confirmed_by_record or {}).get("record_id"),
                "content_confirmation": confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"],
                "note": "Confirmed content stays formal even if a later proposal changes the phase."}
    if kind == "OperatingState":
        canonical = state.get("canonical_ref")
        matches = _same_ref(canonical, selected)
        recommendation = _same_ref(state.get("recommendation_ref"), selected)
        if matches:
            status = "confirmed"
        elif confirmed_by_record:
            status = "superseded_confirmed"
        elif recommendation:
            status = "recommendation"
        else:
            status = recorded_status()
        return {"status": status, "formal": matches, "authority": "method_confirm_state",
                "applies_to_ref": canonical if matches else None,
                "canonical_ref": canonical, "recommendation_ref": state.get("recommendation_ref"),
                "confirmed_by": (confirmed_by_record or {}).get("principal_id"),
                "content_confirmation": confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"]}
    if kind == "OperatingProblem":
        closure = next((record for record in confirmations if record["kind"] == "problem_closure"
                        and record["covers_selected_revision"]), None)
        if closure is not None:
            disposition = (closure.get("content") or {}).get("disposition", "closed")
            return {"status": disposition, "formal": False,
                    "authority": "method_close_problem", "applies_to_ref": None,
                    "closure_record_id": closure["record_id"],
                    "closure_reason": (closure.get("content") or {}).get("reason"),
                    "content_confirmation": closure,
                    "note": "A closed disposition belongs to this exact revision; it is not current tracking.",
                    "lifecycle_status": head["lifecycle_status"]}
        tracking = effective and bool(state.get("tracking")) and state.get("phase") == "open"
        return {"status": "open" if tracking else "historical" if not effective else recorded_status(),
                "formal": tracking, "authority": "method_open_problem/method_close_problem",
                "applies_to_ref": selected if tracking else None,
                "tracking": tracking, "issue_ref": state.get("issue_ref"),
                "transfer_reason": state.get("transfer_reason"),
                "note": "transferred means the question moved to strategic intake; it is not solved.",
                "lifecycle_status": head["lifecycle_status"]}
    if kind == "BusinessFact":
        return {"status": "recorded", "formal": True, "authority": "m1b_record_fact",
                "applies_to_ref": selected, "lifecycle_status": head["lifecycle_status"],
                "correction": ({"corrects_ref": revision["payload"].get("corrects_ref"),
                                "correction_reason": revision["payload"].get("correction_reason")}
                               if revision["payload"].get("corrects_ref") else None)}
    if kind == "PeriodReview":
        # Agent analysis: the effective pointer does not make it human-approved.
        return {"status": "recorded", "formal": False, "authority": "agent_analysis",
                "human_approved": False, "applies_to_ref": None,
                "effective_pointer": _ref(head["object_id"], revision["revision_id"]) if effective else None,
                "lifecycle_status": head["lifecycle_status"]}
    if kind in {"StrategicAgreement", "MeetingMinutes", "CandidateSet"}:
        # Types outside the six navigation groups still have real human
        # confirmation acts; a covered, effective exact revision is confirmed
        # and must never be reported as unrecorded.
        formal = bool(effective and confirmed_by_record)
        return {"status": "confirmed" if formal else recorded_status(),
                "formal": formal,
                "human_approved": bool(confirmed_by_record),
                "authority": {"StrategicAgreement": "m1a_confirm_agreement",
                              "MeetingMinutes": "m1a_confirm_minutes",
                              "CandidateSet": _candidate_authority(confirmed_by_record)}[kind],
                "applies_to_ref": selected if formal else None,
                "confirmation_record_id": (confirmed_by_record or {}).get("record_id"),
                "content_confirmation": confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"]}
    if kind == "StrategicIssue":
        # 0.1/0.2: a human CEO confirmation act; 0.3: formal initiation by the
        # bound CEO Agent (``agent_issue_initiation``).  Both make the issue
        # formal; only the human act is a human approval.
        human_record = next((record for record in confirmations
                             if record["kind"] == "strategic_issue_confirmation"
                             and record["covers_selected_revision"]), None)
        agent_record = next((record for record in confirmations
                             if record["kind"] == "agent_issue_initiation"
                             and record["covers_selected_revision"]), None)
        record = human_record or agent_record
        formal = bool(effective and record)
        if formal:
            status = "confirmed"
        elif record is not None:
            status = "superseded_confirmed"
        else:
            status = recorded_status()
        return {"status": status, "formal": formal,
                "human_approved": bool(human_record),
                "initiation": ("human" if human_record else "agent" if agent_record else None),
                "authority": "m1a_confirm_strategic_issue",
                "applies_to_ref": selected if formal else None,
                "confirmation_record_id": (record or {}).get("record_id"),
                "content_confirmation": record or confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"]}
    if kind == "ResearchBrief":
        # CEO sufficiency confirmation (0.2+) is recorded against the brief
        # itself; an effective covered revision is confirmed.
        brief_record = next((record for record in confirmations
                             if record["kind"] == "brief_sufficiency"
                             and record["covers_selected_revision"]), None)
        formal = bool(effective and brief_record)
        if formal:
            status = "confirmed"
        elif brief_record is not None:
            status = "superseded_confirmed"
        else:
            status = recorded_status()
        return {"status": status, "formal": formal,
                "human_approved": bool(brief_record),
                "authority": "m1a_confirm_brief",
                "applies_to_ref": selected if formal else None,
                "confirmation_record_id": (brief_record or {}).get("record_id"),
                "content_confirmation": brief_record or confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"]}
    if kind == "StrategicJudgment":
        # Takes effect through the CEO's strategy-update confirmation; the
        # record sits on the proposal and covers this revision via
        # ``changed_refs`` (followed by ``_covering_confirmations``).
        formal = bool(effective and confirmed_by_record)
        if formal:
            status = "confirmed"
        elif confirmed_by_record is not None:
            status = "superseded_confirmed"
        else:
            status = recorded_status()
        return {"status": status, "formal": formal,
                "human_approved": bool(confirmed_by_record),
                "authority": "m1a_confirm_update",
                "applies_to_ref": selected if formal else None,
                "confirmation_record_id": (confirmed_by_record or {}).get("record_id"),
                "content_confirmation": confirmed_by_record,
                "lifecycle_status": head["lifecycle_status"]}
    # Remaining registered types (signals, potential issues, research material,
    # meeting rounds, review windows, runs, evidence, ...): the dashboard does
    # not project a separate formal efficacy for them.  The selected revision's
    # own recorded lifecycle phase is shown as-is — a historical revision never
    # borrows the object's current phase — and it is never presented as a human
    # approval.  A confirmation record that covers the revision is still
    # surfaced, not hidden.
    return {"status": recorded_status(), "formal": False,
            "authority": None, "human_approved": bool(confirmed_by_record),
            "applies_to_ref": None,
            "content_confirmation": confirmed_by_record,
            "note": "No separate formal-efficacy projection for this type; the recorded lifecycle phase is not an approval.",
            "lifecycle_status": head["lifecycle_status"]}


def _outcome_from_revision(conn: Any, ctx: Any, ref: Any, outcome_id: Any) -> tuple[Any, str | None]:
    """Readable Outcome name/criteria behind an exact outcome reference."""
    if not isinstance(ref, dict) or not outcome_id:
        return None, "no_outcome_recorded"
    loaded = _load_ref(conn, ctx, ref)
    if loaded is None:
        return None, "outcome_revision_unavailable"
    _head, revision = loaded
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    for key in ("unit_outcomes", "outcomes"):
        for outcome in payload.get(key) or []:
            if isinstance(outcome, dict) and str(outcome.get("outcome_id")) == str(outcome_id):
                return db.jsonable(outcome), None
    return None, "outcome_not_found_in_revision"


def _enrich_supports(conn: Any, ctx: Any, supports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for support in supports:
        outcome, reason = _outcome_from_revision(conn, ctx, support.get("outcome_ref"),
                                                 support.get("outcome_id"))
        support["outcome"] = outcome
        support["outcome_status"] = ({"status": "available", "reason": None}
                                     if outcome is not None else _missing(reason or "outcome_unavailable"))
    return supports


def _subject_outcome_view(conn: Any, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    subject = payload.get("subject_ref")
    if not isinstance(subject, dict) or not subject.get("outcome_id"):
        return _missing("no_outcome_recorded")
    outcome, reason = _outcome_from_revision(conn, ctx, subject, subject["outcome_id"])
    if outcome is None:
        return _missing(reason or "outcome_unavailable")
    return {"status": "available", "reason": None,
            "value": {"outcome_id": subject["outcome_id"], "outcome": outcome,
                      "subject_ref": subject}}


# --------------------------------------------------------------------------
# Object listing
# --------------------------------------------------------------------------

def _iter_candidates(conn: Any, ctx: Any, types: tuple[str, ...], after: Any, need: int,
                     domain_id: str | None) -> list[Any]:
    sql = ("SELECT /*dashboard:objects*/ object_id, domain_id, object_type, lifecycle_status,"
           " object_version, latest_revision_id, effective_revision_id, created_at"
           " FROM gov_objects WHERE scope_id=%s AND object_type = ANY(%s)")
    params: list[Any] = [ctx.scope_id, list(types)]
    if domain_id:
        sql += " AND domain_id=%s"
        params.append(domain_id)
    if after is not None:
        sql += " AND (created_at, object_id) > (%s, %s)"
        params.extend(after)
    sql += " ORDER BY created_at, object_id LIMIT %s"
    params.append(need)
    return conn.execute(sql, params).fetchall()


def _period_overlap(period: dict[str, Any], period_from: str | None, period_to: str | None) -> bool:
    if period_from:
        end = period.get("end") or period.get("start")
        if not end or _time(end) < _time(period_from):
            return False
    if period_to:
        start = period.get("start") or period.get("end")
        if not start or _time(start) > _time(period_to):
            return False
    return True


def _derived_period(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any],
                    *, depth: int = 0) -> tuple[dict[str, Any] | None, str | None]:
    """Business period of an exact revision, following recorded exact references.

    A Mission has no own period field: its business period is the period of the
    exact PCO revision it recorded, resolved under current authorization.  The
    Mission ``hard_deadline`` is deliberately *not* used as the business period.
    """
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    period = payload.get("period")
    if isinstance(period, dict) and period.get("start") and period.get("end"):
        return period, "payload"
    if depth > 3:
        return None, None
    ref: Any = None
    source = None
    if head["object_type"] == "Mission":
        ref, source = payload.get("pco_ref"), "pco_ref"
    elif head["object_type"] == "OperatingState":
        ref, source = payload.get("subject_ref"), "subject_ref"
    elif head["object_type"] == "BusinessFact":
        subject = payload.get("subject_ref")
        if isinstance(subject, dict) and subject.get("topic") is not None:
            return None, "topic_only"
        ref, source = subject, "subject_ref"
    elif head["object_type"] == "OperatingProblem":
        ref, source = payload.get("state_ref"), "state_ref"
    if not isinstance(ref, dict) or not ref.get("object_id"):
        return None, None
    loaded = _load_ref(conn, ctx, ref)
    if loaded is None:
        return None, source + "_unavailable"
    child_head, child = loaded
    period, inner = _derived_period(conn, ctx, child_head, child, depth=depth + 1)
    return period, source if period is not None else inner


def _owner_matches(entries: list[dict[str, Any]], owner_id: str | None) -> bool:
    if not owner_id:
        return True
    return any(str(entry.get("assignment_id")) == str(owner_id)
               or str((entry.get("principal") or {}).get("principal_id")) == str(owner_id)
               for entry in entries)


def _scope_matches(payload: dict[str, Any], scope_id: str | None) -> bool:
    if not scope_id:
        return True
    if payload.get("primary_scope_id") == scope_id:
        return True
    if any(u.get("unit_id") == scope_id for u in (payload.get("units") or []) if isinstance(u, dict)):
        return True
    return any(s.get("outcome_id") == scope_id
               or (s.get("outcome_ref") or {}).get("outcome_id") == scope_id
               for s in (payload.get("supports") or []) if isinstance(s, dict))


def _list_item(conn: Any, ctx: Any, head: dict[str, Any], selected: dict[str, Any] | None,
               revision: dict[str, Any], basis: dict[str, Any],
               confirmations: list[dict[str, Any]]) -> dict[str, Any]:
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    entries = _responsibility_entries(conn, ctx, head, payload)
    responsibility = _list_responsibility(entries)
    period, period_source = _derived_period(conn, ctx, head, revision)
    return db.jsonable({
        "object_id": head["object_id"], "object_type": head["object_type"],
        "domain_id": head["domain_id"], "domain_name": _domain_name(conn, ctx, head["domain_id"]),
        "title": _payload_title(payload), "summary": payload.get("summary"),
        "lifecycle_status": head["lifecycle_status"],
        "object_version": head["object_version"],
        "latest_revision_id": head["latest_revision_id"],
        "effective_revision_id": head["effective_revision_id"],
        "created_at": head["created_at"],
        "basis": basis,
        "basis_revision_id": revision["revision_id"],
        "formal_state": _formal_state(conn, ctx, head, revision, confirmations),
        "owner": _owner_entry(entries),
        "dri": _dri_entries(entries),
        "responsibility": responsibility,
        "period": period,
        "period_source": period_source,
        "period_status": ({"status": "available", "reason": None, "value": period}
                          if period is not None else _missing(period_source or "period_not_recorded")),
        "deadline": (payload.get("hard_deadline") or payload.get("decision_deadline")
                     or payload.get("feedback_deadline")),
    })


def _scan(conn: Any, ctx: Any, types: tuple[str, ...],
          keep: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], bool],
          *, selected: dict[str, Any] | None, domain_id: str | None, limit: int, key: Any
          ) -> tuple[list[tuple[dict[str, Any], list[Any]]], bool]:
    """Keyset scan with authorization/basis filtering inside pagination semantics.

    Returns ``(found, more)`` where each entry keeps the *examined row key* of
    the emitted item, so the caller builds its cursor after the last emitted
    item rather than after the lookahead row.
    """
    found: list[tuple[dict[str, Any], list[Any]]] = []
    current = key
    while len(found) <= limit:
        need = limit + 1 - len(found)
        batch = _iter_candidates(conn, ctx, types, current, need, domain_id)
        if not batch:
            break
        for row in batch:
            current = [row["created_at"], str(row["object_id"])]
            try:
                head, allowed = _visible_head(conn, ctx, str(row["object_id"]))
            except GovernedError as exc:
                if exc.code in _UNAVAILABLE_CODES:
                    continue  # unreadable candidates stay hidden without any count
                raise
            for pointer in (head["effective_revision_id"], head["latest_revision_id"]):
                if pointer is None:
                    continue
                try:
                    revision = _visible_revision(conn, ctx, head["object_id"], str(pointer), allowed)
                except GovernedError as exc:
                    if exc.code in _UNAVAILABLE_CODES:
                        continue
                    raise
                basis = _basis_of_revision(conn, ctx, head, revision, selected, visited=frozenset())
                if not keep(head, revision, basis):
                    break
                confirmations = _covering_confirmations(conn, ctx, head, revision)
                found.append((_list_item(conn, ctx, head, selected, revision, basis, confirmations),
                              current))
                break
        if len(batch) < need:
            break
    return found, len(found) > limit


def _cursor_key(cursor: str | None, ctx: Any, filters: dict[str, Any]) -> Any:
    raw = workbench.decode_cursor(cursor, "dashboard-objects", ctx, filters)
    if raw is None:
        return None
    if (not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], str)
            or not isinstance(raw[1], str)):
        raise workbench._invalid_cursor()
    try:
        parsed = datetime.fromisoformat(raw[0].replace("Z", "+00:00"))
        uuid.UUID(raw[1])
    except (ValueError, TypeError, AttributeError):
        raise workbench._invalid_cursor() from None
    if parsed.tzinfo is None:
        raise workbench._invalid_cursor()
    return [parsed, raw[1]]


def objects(conn: Any, ctx: Any, *, group: str, strategy_id: str | None = None,
            object_type: str | None = None, basis: str = "current", domain_id: str | None = None,
            period_from: str | None = None, period_to: str | None = None,
            owner_id: str | None = None, scope_id: str | None = None,
            limit: int = DEFAULT_LIMIT, cursor: str | None = None) -> dict[str, Any]:
    if group not in GROUP_TYPES:
        raise _invalid("Unknown dashboard group.")
    types = GROUP_TYPES[group]
    if object_type is not None and object_type not in types:
        raise _invalid("Object type is not part of this dashboard group.")
    if basis not in BASES:
        raise _invalid("Unknown basis selector.")
    if domain_id:
        workbench._readable_domain(conn, ctx, domain_id)
    selected = select_strategy(conn, ctx, strategy_id)
    if selected is None:
        return db.jsonable({
            "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
            "strategy": None, "group": group, "object_type": object_type, "basis": basis,
            "items": [], "next_cursor": None, "loaded_count": 0, "has_more": False,
            "selection_required": False, "strategy_choices": [],
            "hint": "no_current_strategy_is_readable",
        })
    filters = {"group": group, "object_type": object_type,
               "strategy_id": str(selected["strategy_id"]),
               "strategy_revision_id": str(selected["revision_id"]), "basis": basis,
               "domain_id": domain_id, "period_from": period_from,
               "period_to": period_to, "owner_id": owner_id, "scope_id": scope_id}
    key = _cursor_key(cursor, ctx, filters)

    def keep(head: dict[str, Any], revision: dict[str, Any], item_basis: dict[str, Any]) -> bool:
        if not _basis_matches(item_basis, basis):
            return False
        if object_type is not None and head["object_type"] != object_type:
            return False
        payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
        if (period_from or period_to):
            period, _source = _derived_period(conn, ctx, head, revision)
            # A period filter can only place objects with a recorded business
            # period; topic-only facts and unresolved chains are excluded, never
            # silently placed by hard_deadline or by name.
            if period is None or not _period_overlap(period, period_from, period_to):
                return False
        if owner_id and not _owner_matches(
                _list_responsibility(_responsibility_entries(conn, ctx, head, payload)), owner_id):
            return False
        if not _scope_matches(payload, scope_id):
            return False
        return True

    found, more = _scan(conn, ctx, types, keep, selected=selected, domain_id=domain_id,
                        limit=limit, key=key)
    items = [item for item, _key in found[:limit]]
    next_cursor = None
    if more and len(found) > limit:
        emitted_key = found[limit - 1][1]
        next_cursor = workbench.encode_cursor("dashboard-objects", ctx, filters,
                                              [db.jsonable(emitted_key[0]), str(emitted_key[1])])
    return db.jsonable({
        "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
        "strategy": selected, "group": group, "object_type": object_type, "basis": basis,
        "filters": {name: value for name, value in filters.items()
                    if name not in {"group", "object_type", "basis", "strategy_id",
                                    "strategy_revision_id"}},
        "items": items, "next_cursor": next_cursor,
        "loaded_count": len(items), "has_more": more,
        "selection_required": False,
        "note": "loaded_count is this response only; it is not a global total.",
    })


# --------------------------------------------------------------------------
# Ontology catalog (registered type directory + per-type record listing)
# --------------------------------------------------------------------------

# Business rule versions whose registered object types the ontology map shows.
# The directory comes from the compiled registries, never from current data.
# Compiled Method contract versions, from the real protocol set; a newly
# compiled version (e.g. 0.4) joins the concept directory automatically.
ONTOLOGY_CONTRACT_VERSIONS = tuple(sorted(
    version for protocol_id, version in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    if protocol_id == "tkos.method"))


def _registered_object_types(version: str) -> frozenset[str]:
    from . import method_models
    _params, _targets, payload_models = method_models.registry(version)
    return frozenset(payload_models) | {"EvidenceAsset"}


def _all_registered_object_types() -> frozenset[str]:
    result: set[str] = set()
    for version in ONTOLOGY_CONTRACT_VERSIONS:
        result |= _registered_object_types(version)
    return frozenset(result)


def ontology_catalog(conn: Any, ctx: Any) -> dict[str, Any]:
    """Registered Method object-type directory per business rule version.

    This is the *concept* directory: a type with no readable records still
    appears.  ``group`` names the classic navigation group when the type is
    part of it; ``listable`` records that the catalog listing below can page
    the type under current authority.  Definition items that live inside
    another object (Battlefield/Capability/Outcome) are not object types and
    are never invented as entries here.
    """
    versions = []
    for version in ONTOLOGY_CONTRACT_VERSIONS:
        versions.append({"contract_version": version,
                         "object_types": sorted(_registered_object_types(version))})
    readers = {}
    for name in sorted(_all_registered_object_types()):
        group = next((group for group, types in GROUP_TYPES.items() if name in types), None)
        readers[name] = {"group": group, "listable": True}
    return db.jsonable({
        "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
        "versions": versions, "types": readers,
        "note": ("Registered object types come from the compiled Method registries; "
                 "presence is not evidence of readable records, and an empty record "
                 "list only means none were read under the current identity."),
    })


def _catalog_cursor_key(cursor: str | None, ctx: Any, filters: dict[str, Any]) -> Any:
    raw = workbench.decode_cursor(cursor, "dashboard-catalog", ctx, filters)
    if raw is None:
        return None
    if (not isinstance(raw, list) or len(raw) != 2 or not isinstance(raw[0], str)
            or not isinstance(raw[1], str)):
        raise workbench._invalid_cursor()
    try:
        parsed = datetime.fromisoformat(raw[0].replace("Z", "+00:00"))
        uuid.UUID(raw[1])
    except (ValueError, TypeError, AttributeError):
        raise workbench._invalid_cursor() from None
    if parsed.tzinfo is None:
        raise workbench._invalid_cursor()
    return [parsed, raw[1]]


def _catalog_item(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    confirmations = _covering_confirmations(conn, ctx, head, revision)
    # The same authorized responsibility projection the group list and detail
    # already use, derived from the exact selected revision; participants are
    # not a responsibility relation and no Owner is inferred.
    responsibility = _list_responsibility(_responsibility_entries(conn, ctx, head, payload))
    return db.jsonable({
        "object_id": head["object_id"], "object_type": head["object_type"],
        "domain_id": head["domain_id"], "domain_name": _domain_name(conn, ctx, head["domain_id"]),
        "title": _payload_title(payload), "summary": payload.get("summary"),
        "lifecycle_status": head["lifecycle_status"],
        # The displayed content comes from the selected (effective, else
        # latest) revision; its version number must come from that same
        # revision, never from the head pointer.
        "object_version": revision["object_version"],
        "latest_revision_id": head["latest_revision_id"],
        "effective_revision_id": head["effective_revision_id"],
        "created_at": head["created_at"],
        "basis_revision_id": revision["revision_id"],
        "contract_version": metadata.get("contract_version"),
        "formal_state": _formal_state(conn, ctx, head, revision, confirmations),
        "responsibility": responsibility,
    })


def catalog_objects(conn: Any, ctx: Any, *, object_type: str, domain_id: str | None = None,
                    limit: int = DEFAULT_LIMIT, cursor: str | None = None) -> dict[str, Any]:
    """Keyset-paged readable records of one *registered* object type.

    Any type the compiled registries know is listable here under current
    authority; an unregistered name is a 422, never an empty page.  Unreadable
    objects stay hidden without a count, and ``loaded_count`` is this page only.
    """
    if object_type not in _all_registered_object_types():
        raise _invalid("Unknown or unregistered object type.")
    if domain_id:
        workbench._readable_domain(conn, ctx, domain_id)
    filters = {"object_type": object_type, "domain_id": domain_id}
    key = _catalog_cursor_key(cursor, ctx, filters)
    found: list[tuple[dict[str, Any], list[Any]]] = []
    current = key
    while len(found) <= limit:
        need = limit + 1 - len(found)
        batch = _iter_candidates(conn, ctx, (object_type,), current, need, domain_id)
        if not batch:
            break
        for row in batch:
            current = [row["created_at"], str(row["object_id"])]
            try:
                head, allowed = _visible_head(conn, ctx, str(row["object_id"]))
            except GovernedError as exc:
                if exc.code in _UNAVAILABLE_CODES:
                    continue  # unreadable candidates stay hidden without any count
                raise
            pointer = head["effective_revision_id"] or head["latest_revision_id"]
            if pointer is None:
                continue
            try:
                revision = _visible_revision(conn, ctx, head["object_id"], str(pointer), allowed)
            except GovernedError as exc:
                if exc.code in _UNAVAILABLE_CODES:
                    continue
                raise
            found.append((_catalog_item(conn, ctx, head, revision), current))
        if len(batch) < need:
            break
    more = len(found) > limit
    items = [item for item, _key in found[:limit]]
    next_cursor = None
    if more and len(found) > limit:
        emitted_key = found[limit - 1][1]
        next_cursor = workbench.encode_cursor("dashboard-catalog", ctx, filters,
                                              [db.jsonable(emitted_key[0]), str(emitted_key[1])])
    return db.jsonable({
        "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
        "object_type": object_type,
        "items": items, "next_cursor": next_cursor,
        "loaded_count": len(items), "has_more": more,
        "note": "loaded_count is this response only; it is not a global total.",
    })


def group_availability(conn: Any, ctx: Any, selected: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = []
    for group in GROUPS:
        if selected is None:
            result.append({"group": group, "available": False, "reason": "no_current_strategy",
                           "historical_available": False, "unattached_available": False})
            continue
        found, _more = _scan(conn, ctx, GROUP_TYPES[group],
                             lambda h, r, b: _basis_matches(b, "current"),
                             selected=selected, domain_id=None, limit=0, key=None)
        current_available = bool(found)
        historical, _m1 = _scan(conn, ctx, GROUP_TYPES[group],
                                lambda h, r, b: _basis_matches(b, "historical"),
                                selected=selected, domain_id=None, limit=0, key=None)
        unattached, _m2 = _scan(conn, ctx, GROUP_TYPES[group],
                                lambda h, r, b: _basis_matches(b, "unattached"),
                                selected=selected, domain_id=None, limit=0, key=None)
        reason = None
        if not current_available:
            reason = ("contract_version_has_no_architecture"
                      if group == "architecture" and selected.get("contract_version") != "tkos.method/0.3"
                      else "not_recorded_for_selected_strategy")
        result.append({"group": group, "available": current_available, "reason": reason,
                       "historical_available": bool(historical),
                       "unattached_available": bool(unattached)})
    return result


def historical_basis(conn: Any, ctx: Any, selected: dict[str, Any] | None) -> dict[str, Any]:
    if selected is None:
        return {"available": False, "reason": "no_current_strategy", "groups": [], "items": []}
    rows = conn.execute(
        """SELECT DISTINCT i.target_object_id, i.old_strategy_ref
             FROM gov_method_impacts i
            WHERE i.scope_id=%s AND i.strategy_object_id=%s AND i.strategy_revision_id=%s
            ORDER BY i.target_object_id""",
        (ctx.scope_id, str(selected["strategy_id"]), str(selected["revision_id"]))).fetchall()
    groups: set[str] = set()
    items = []
    for row in rows:
        try:
            head, _allowed = _visible_head(conn, ctx, str(row["target_object_id"]))
        except GovernedError as exc:
            if exc.code in _UNAVAILABLE_CODES:
                continue
            raise
        group = next((name for name, types in GROUP_TYPES.items() if head["object_type"] in types), None)
        if group:
            groups.add(group)
        items.append(db.jsonable({"object_id": head["object_id"], "object_type": head["object_type"],
                                  "old_strategy_ref": row["old_strategy_ref"]}))
    return {"available": bool(items), "groups": sorted(groups), "items": items,
            "reason": None if items else "no_recorded_previous_strategy_targets"}


def overview(conn: Any, ctx: Any, *, strategy_id: str | None = None,
             environment: dict[str, Any] | None = None,
             identity: dict[str, Any] | None = None) -> dict[str, Any]:
    choices = strategy_choices(conn, ctx)
    selected = None
    if strategy_id is not None:
        selected = select_strategy(conn, ctx, strategy_id)
    elif len(choices) == 1:
        selected = choices[0]
    return db.jsonable({
        "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
        "environment": environment or {"label": "unspecified", "synthetic": None},
        "viewer": identity,
        "strategy_choices": choices,
        "selected_strategy_id": selected["strategy_id"] if selected else None,
        "selection_required": len(choices) > 1,
        "groups": group_availability(conn, ctx, selected),
        "historical_basis": historical_basis(conn, ctx, selected),
        "refresh_interval_seconds": 5,
        "note": "Group availability is computed from currently readable objects only; no global totals.",
    })


# --------------------------------------------------------------------------
# Object detail
# --------------------------------------------------------------------------

def _tree_refs(value: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """All exact references recorded anywhere in an authorized payload tree."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if (isinstance(value.get("object_id"), str) and isinstance(value.get("revision_id"), str)
                and value.get("object_id") and value.get("revision_id")):
            found.append((path or "/", value))
        else:
            for key, child in value.items():
                found.extend(_tree_refs(child, path + "/" + str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_tree_refs(child, path + "/" + str(index)))
    return found


def _resolve_refs(conn: Any, ctx: Any, refs: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result = []
    for path, ref in refs:
        key = (str(ref.get("object_id")), str(ref.get("revision_id")))
        if key in seen:
            continue
        seen.add(key)
        resolved = _try_ref(conn, ctx, ref)
        if resolved is None:
            # Uniform omission: absent, unauthorized and hash-inconsistent
            # references are all omitted without a hidden marker.
            continue
        result.append(db.jsonable({"path": path, **resolved}))
    return result


# --------------------------------------------------------------------------
# Typed downstream references (bounded, authorized keyset pagination)
# --------------------------------------------------------------------------

def _downstream_unions(conn: Any, ctx: Any, ref: dict[str, Any], after: list[Any] | None,
                       need: int) -> list[Any]:
    parts = []
    params: list[Any] = []
    for kind, fields in sorted(DOWNSTREAM_FIELDS.items()):
        clauses = []
        clause_params: list[Any] = []
        for field in fields:
            if field in DOWNSTREAM_ARRAY_FIELDS:
                clauses.append(f"r.payload->'{field}' @> %s::jsonb")
                clause_params.append(_json([{"object_id": ref["object_id"],
                                            "revision_id": ref["revision_id"]}]))
            else:
                clauses.append(f"(r.payload->'{field}'->>'object_id' = %s"
                               f" AND r.payload->'{field}'->>'revision_id' = %s)")
                clause_params.extend([ref["object_id"], ref["revision_id"]])
        parts.append(
            f"""SELECT %s::text AS object_type, r.object_id, r.revision_id, r.payload_hash
                  FROM gov_object_revisions r
                  JOIN gov_objects o ON (o.scope_id, o.object_id) = (r.scope_id, r.object_id)
                 WHERE r.scope_id=%s AND o.object_type=%s
                   AND (o.effective_revision_id = r.revision_id OR o.latest_revision_id = r.revision_id)
                   AND ({" OR ".join(clauses)})""")
        params.extend([kind, ctx.scope_id, kind, *clause_params])
    sql = "SELECT * FROM (" + " UNION ALL ".join(parts) + ") t"
    tail: list[Any] = []
    if after is not None:
        sql += " WHERE (t.object_type, t.object_id, t.revision_id) > (%s, %s, %s)"
        tail = [str(after[0]), str(after[1]), str(after[2])]
    sql += " ORDER BY t.object_type, t.object_id, t.revision_id LIMIT %s"
    return conn.execute(sql, [*params, *tail, need]).fetchall()


def _refs_in_payload(payload: Any, field: str, ref: dict[str, Any]) -> list[dict[str, Any]]:
    """All recorded refs under ``field`` that name the exact target identity."""
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == field:
                candidates = value if isinstance(value, list) else [value]
                for candidate in candidates:
                    if isinstance(candidate, dict) and _same_ref(candidate, ref):
                        found.append(candidate)
            found.extend(_refs_in_payload(value, field, ref))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_refs_in_payload(item, field, ref))
    return found


def _downstream_cursor_key(cursor: str | None, ctx: Any, filters: dict[str, Any]) -> list[str] | None:
    raw = workbench.decode_cursor(cursor, "dashboard-downstream", ctx, filters)
    if raw is None:
        return None
    if (not isinstance(raw, list) or len(raw) != 3
            or any(not isinstance(item, str) or not item for item in raw)):
        raise workbench._invalid_cursor()
    try:
        for value in raw[1:]:
            uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise workbench._invalid_cursor() from None
    return raw


def downstream(conn: Any, ctx: Any, selected: dict[str, Any], *, limit: int = DOWNSTREAM_LIMIT,
               cursor: str | None = None) -> dict[str, Any]:
    """Authorized, bounded typed inbound references to one exact revision.

    Only the recorded top-level reference fields in ``DOWNSTREAM_FIELDS`` are
    searched.  Candidate rows are authorized *before* they count toward the
    page, the realized payload hash must match the target revision, and a
    continuation cursor is always returned when more candidates exist (never a
    silent truncation).
    """
    ref = {"object_id": str(selected["object_id"]), "revision_id": str(selected["revision_id"])}
    target = _load_ref(conn, ctx, ref)
    if target is None:
        raise GovernedError("NOT_FOUND")
    target_hash = str(target[1]["payload_hash"])
    filters = {"object_id": ref["object_id"], "revision_id": ref["revision_id"]}
    need_limit = max(1, min(int(limit), DOWNSTREAM_MAX_LIMIT))
    after = _downstream_cursor_key(cursor, ctx, filters)
    items: list[dict[str, Any]] = []
    examined = after
    while len(items) <= need_limit:
        need = need_limit + 1 - len(items)
        rows = _downstream_unions(conn, ctx, ref, examined, need)
        if not rows:
            break
        for row in rows:
            examined = [str(row["object_type"]), str(row["object_id"]), str(row["revision_id"])]
            try:
                head, allowed = _visible_head(conn, ctx, str(row["object_id"]))
                revision = _visible_revision(conn, ctx, head["object_id"], str(row["revision_id"]), allowed)
            except GovernedError as exc:
                if exc.code in _UNAVAILABLE_CODES:
                    continue
                raise
            matched_fields: list[str] = []
            consistent = True
            for field in DOWNSTREAM_FIELDS[head["object_type"]]:
                recorded = _refs_in_payload(revision["payload"], field, ref)
                if not recorded:
                    continue
                matched_fields.append(field)
                for recorded_ref in recorded:
                    if recorded_ref.get("payload_hash") is None:
                        consistent = False
                    elif str(recorded_ref["payload_hash"]) != target_hash:
                        consistent = False
            if not matched_fields or not consistent:
                # A reference whose recorded hash does not name this exact target
                # revision is an integrity failure, never a usable edge.
                continue
            confirmations = _covering_confirmations(conn, ctx, head, revision)
            formal = _formal_state(conn, ctx, head, revision, confirmations)
            edge: dict[str, Any] = {
                "object_id": head["object_id"], "object_type": head["object_type"],
                "domain_id": head["domain_id"], "title": _payload_title(revision["payload"]),
                "summary": (revision["payload"].get("summary")
                            if isinstance(revision["payload"], dict) else None),
                "ref": _ref(head["object_id"], revision["revision_id"], revision["payload_hash"]),
                "relation_type": "typed_recorded_reference",
                "matched_fields": matched_fields,
                "formal_state": formal,
            }
            if head["object_type"] == "OperatingState":
                # The state panel must show the exact RAG/as_of/baseline/evidence
                # for the anchor revision, separately from Mission content confirmation.
                subject = revision["payload"].get("subject_ref")
                outcome, reason = _outcome_from_revision(conn, ctx, subject,
                                                         (subject or {}).get("outcome_id"))
                edge["state_summary"] = db.jsonable({
                    "subject_ref": subject,
                    "subject_matches_selected_anchor": _same_ref(subject, ref),
                    "outcome_id": (subject or {}).get("outcome_id"),
                    "outcome": outcome,
                    "outcome_status": ({"status": "available", "reason": None}
                                       if outcome is not None else _missing(reason or "outcome_unavailable")),
                    "as_of": revision["payload"].get("as_of"),
                    "rag": revision["payload"].get("rag"),
                    "summary": revision["payload"].get("summary"),
                    "data_gaps": revision["payload"].get("data_gaps") or [],
                    "baseline_refs": revision["payload"].get("baseline_refs") or [],
                    "evidence_refs": revision["payload"].get("evidence_refs") or [],
                    "baseline_items": _resolve_refs(conn, ctx, [
                        ("baseline_refs", item) for item in revision["payload"].get("baseline_refs") or []
                        if isinstance(item, dict)]),
                    "evidence_items": _resolve_refs(conn, ctx, [
                        ("evidence_refs", item) for item in revision["payload"].get("evidence_refs") or []
                        if isinstance(item, dict)]),
                    "formal": formal["formal"], "status": formal["status"],
                    "applies_to_ref": formal.get("applies_to_ref"),
                    "canonical_ref": formal.get("canonical_ref"),
                    "confirmed_by": formal.get("confirmed_by"),
                })
            items.append(db.jsonable(edge))
        if len(rows) < need:
            break
    more = len(items) > need_limit
    page = items[:need_limit]
    next_cursor = None
    if more and page:
        last = page[-1]
        next_cursor = workbench.encode_cursor("dashboard-downstream", ctx, filters,
                                              [last["object_type"], last["object_id"],
                                               last["ref"]["revision_id"]])
    return {"items": page, "next_cursor": next_cursor, "has_more": more,
            "limit": need_limit, "bounded": DOWNSTREAM_MAX_LIMIT}


def _evidence_items(conn: Any, ctx: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[tuple[str, dict[str, Any]]] = []
    for field in ("evidence_refs", "source_refs", "material_refs", "baseline_refs"):
        for ref in payload.get(field) or []:
            if isinstance(ref, dict):
                refs.append((field, ref))
    for field in ("source_ref", "corrects_ref"):
        if isinstance(payload.get(field), dict):
            refs.append((field, payload[field]))
    items = []
    for resolved in _resolve_refs(conn, ctx, refs):
        item = dict(resolved)
        if resolved.get("object_type") == "EvidenceAsset":
            item["download"] = {"available": True, "object_id": resolved["object_id"],
                                "revision_id": resolved["revision_id"]}
        items.append(item)
    return items


def _diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    from .workspace_readers import differences
    return differences(before, after)


def detail(conn: Any, ctx: Any, object_id: str, *, revision_id: str | None = None,
           strategy_id: str | None = None, environment: dict[str, Any] | None = None,
           identity: dict[str, Any] | None = None) -> dict[str, Any]:
    head, allowed = _visible_head(conn, ctx, object_id)
    metadata = protocol.require_read_support(conn, ctx.scope_id, head["object_id"])
    if revision_id is not None:
        rid = str(revision_id)
        selection = "requested"
    else:
        rid = str(head["effective_revision_id"] or head["latest_revision_id"] or "")
        selection = "effective" if head["effective_revision_id"] else "latest"
        if not rid:
            raise GovernedError("NOT_FOUND")
    revision = _visible_revision(conn, ctx, head["object_id"], rid, allowed)
    payload = revision["payload"] if isinstance(revision["payload"], dict) else {}
    choices = strategy_choices(conn, ctx)
    selected = None
    context_note = None
    if strategy_id is not None:
        selected = select_strategy(conn, ctx, strategy_id)
    elif len(choices) == 1:
        selected = choices[0]
    elif len(choices) > 1:
        context_note = "multiple_current_strategies_readable"
    # Basis of the *selected detail revision*, not of the current head: a
    # historical revision keeps the references it actually recorded.
    basis = _basis_of_revision(conn, ctx, head, revision, selected, visited=frozenset())
    entries = _responsibility_entries(conn, ctx, head, payload)
    confirmations = _covering_confirmations(conn, ctx, head, revision)

    candidates: dict[str, Any] = {}
    for label, pointer in (("latest", head["latest_revision_id"]),
                           ("effective", head["effective_revision_id"])):
        if pointer is None:
            candidates[label] = _missing(f"{label}_revision_not_recorded")
            continue
        try:
            other = _visible_revision(conn, ctx, head["object_id"], str(pointer), allowed)
        except GovernedError as exc:
            if exc.code in _UNAVAILABLE_CODES:
                candidates[label] = _missing(f"{label}_revision_unavailable")
                continue
            raise
        entry = {"revision_id": other["revision_id"], "payload_hash": other["payload_hash"],
                 "recorded_at": other["recorded_at"], "object_version": other["object_version"],
                 "is_selected": str(other["revision_id"]) == str(revision["revision_id"])}
        if not entry["is_selected"]:
            entry["changed_fields_vs_selected"] = _diff(payload, other["payload"])
        candidates[label] = entry

    history = workbench.revisions(conn, ctx, head["object_id"], 25, None)
    receipts = workbench.action_receipts(conn, ctx, head["object_id"], 25, None)
    missing: list[dict[str, Any]] = []
    if isinstance(payload.get("corrects_ref"), dict):
        corrected = _try_ref(conn, ctx, payload["corrects_ref"])
        if corrected is None:
            missing.append({"field": "corrects_ref", "status": "missing",
                            "reason": "original_fact_unavailable"})
        else:
            missing.append({"field": "corrects_ref", "status": "available",
                            "reason": "correction_preserves_original", "value": corrected})
    if basis["status"] in {"unavailable", "unselected"}:
        missing.append({"field": "strategy_basis", "status": "missing", "reason": basis.get("reason")})
    if (head["object_type"] in {"OperatingState", "OperatingProblem", "BusinessFact"}
            and not payload.get("evidence_refs")):
        missing.append({"field": "evidence_refs", "status": "missing",
                        "reason": "no_evidence_recorded"})

    downstream_page = downstream(conn, ctx, _ref(head["object_id"], revision["revision_id"]),
                                 limit=DOWNSTREAM_LIMIT)
    # Direct typed bases are shown in their own section; the generic source
    # material list must not repeat the same exact reference.
    direct_keys: set[tuple[str, str]] = set()
    for value in (payload.get("strategy_ref"), payload.get("architecture_ref"),
                  payload.get("pco_ref"), payload.get("ltco_ref"),
                  payload.get("subject_ref"), payload.get("state_ref")):
        if isinstance(value, dict) and value.get("object_id") and value.get("revision_id"):
            direct_keys.add((str(value["object_id"]), str(value["revision_id"])))
    for support in payload.get("supports") or []:
        ref = support.get("outcome_ref") if isinstance(support, dict) else None
        if isinstance(ref, dict) and ref.get("object_id") and ref.get("revision_id"):
            direct_keys.add((str(ref["object_id"]), str(ref["revision_id"])))
    upstream_refs = _resolve_refs(conn, ctx, [
        (path, ref) for path, ref in _tree_refs(payload)
        if (str(ref["object_id"]), str(ref["revision_id"])) not in direct_keys])
    business = _normalised_business(head["object_type"], payload)
    if business.get("supports"):
        business["supports"] = _enrich_supports(conn, ctx, business["supports"])
    derived_period, period_source = _derived_period(conn, ctx, head, revision)
    business["period_view"] = ({"status": "available", "reason": None, "source": period_source,
                                 "value": derived_period}
                                if derived_period is not None
                                else _missing(period_source or "period_not_recorded"))
    business["subject_outcome"] = _subject_outcome_view(conn, ctx, payload)
    return db.jsonable({
        "schema_version": SCHEMA_VERSION, "read_at": _read_at(conn),
        "environment": environment or {"label": "unspecified", "synthetic": None},
        "viewer": identity,
        "object": {
            "object_id": head["object_id"], "object_type": head["object_type"],
            "domain_id": head["domain_id"], "domain_name": _domain_name(conn, ctx, head["domain_id"]),
            "lifecycle_status": head["lifecycle_status"], "object_version": head["object_version"],
            "created_at": head["created_at"], "latest_revision_id": head["latest_revision_id"],
            "effective_revision_id": head["effective_revision_id"],
        },
        "protocol": metadata,
        "selected_revision": db.jsonable({
            "revision_id": revision["revision_id"], "object_version": revision["object_version"],
            "payload_hash": revision["payload_hash"], "recorded_at": revision["recorded_at"],
            "recorded_by": revision["recorded_by"],
            "valid_from": revision["valid_from"], "valid_to": revision["valid_to"],
            "is_latest": str(revision["revision_id"]) == str(head["latest_revision_id"]),
            "is_effective": str(revision["revision_id"]) == str(head["effective_revision_id"]),
            "selection": selection,
        }),
        "content": payload,
        "business": business,
        "responsibility": {
            "entries": entries,
            "owner": _owner_entry(entries),
            "dri": _dri_entries(entries),
            "note": "Owner, DRI and participants are recorded relations; none implies an Action permission.",
        },
        "basis": basis,
        "context_note": context_note,
        "relations": {
            "own_basis_refs": _resolve_refs(conn, ctx, [
                (key, value) for key, value in (
                    ("strategy_ref", payload.get("strategy_ref")),
                    ("architecture_ref", payload.get("architecture_ref")),
                    ("pco_ref", payload.get("pco_ref")),
                    ("ltco_ref", payload.get("ltco_ref")),
                    ("subject_ref", payload.get("subject_ref")),
                    ("state_ref", payload.get("state_ref")),
                ) if isinstance(value, dict)]),
            "support_refs": _resolve_refs(conn, ctx, [
                (f"supports/{index}/outcome_ref", support.get("outcome_ref"))
                for index, support in enumerate(payload.get("supports") or [])
                if isinstance(support, dict) and isinstance(support.get("outcome_ref"), dict)]),
            "upstream_refs": upstream_refs,
            "downstream": downstream_page,
        },
        "formal_state": _formal_state(conn, ctx, head, revision, confirmations),
        "content_confirmation": {
            "records": confirmations,
            "confirmed_for_selected_revision": any(
                record["covers_selected_revision"] and record["human_confirmation"]
                for record in confirmations),
            "note": "Content confirmation is separate from formal state and from the effective pointer.",
        },
        "candidates": {"same_object_only": True, **candidates},
        "history": {"items": history["items"], "next_cursor": history.get("next_cursor")},
        "evidence": {"items": _evidence_items(conn, ctx, payload)},
        "receipts": {"items": receipts["items"], "next_cursor": receipts.get("next_cursor")},
        "missing": missing,
    })


__all__ = [
    "SCHEMA_VERSION", "DEFAULT_LIMIT", "MAX_LIMIT", "GROUPS", "BASES", "GROUP_TYPES",
    "OPERATING_TYPES", "DOWNSTREAM_LIMIT", "DOWNSTREAM_MAX_LIMIT",
    "ONTOLOGY_CONTRACT_VERSIONS",
    "strategy_choices", "select_strategy", "overview", "objects", "detail",
    "group_availability", "historical_basis", "downstream", "authorized_ref",
    "ontology_catalog", "catalog_objects",
]
