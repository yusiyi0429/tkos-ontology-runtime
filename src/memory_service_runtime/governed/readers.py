"""Authorized version and historical context reads from authoritative records."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from psycopg.types.json import Jsonb

from memory_service_runtime.governed import db, delivery, protocol
from memory_service_runtime.governed.errors import GovernedError


FORMAL_TYPES = {"BusinessCommitment", "ExecutionCommitment", "CompanyOutcome", "Decision", "WorkItem"}


def timestamp(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _is_a2_object(conn, ctx, object_id: str) -> bool:
    """True iff the object is one of the seven A2 types AND currently bound
    to Contract-A.  Legacy / unregistered / non-A2 objects return False;
    raw EvidenceAsset stays on the legacy boundary.
    """
    from . import a2_readers
    return bool(a2_readers.is_a2_object(conn, ctx, object_id))


def _delegate_a2_reader(name: str, conn, ctx, *args, **kwargs):
    """Delegate a read to the corresponding a2_readers function if it exists.
    Returns None if the A2 readers module is missing or does not implement
    the named helper — callers fall back to the legacy path.
    """
    from . import a2_readers
    func = getattr(a2_readers, name)
    return func(conn, ctx, *args, **kwargs)


def object_state(conn, ctx, object_id: str) -> dict:
    if _is_a2_object(conn, ctx, object_id):
        return db.jsonable(_delegate_a2_reader("object_state", conn, ctx, object_id))
    obj = db.object_row(conn, ctx, object_id)
    result = dict(obj)
    # Protocol metadata attaches to every authorized read; legacy type
    # interpretation (delivery/feedback/outcome projections) runs only when
    # the current registration actually supports legacy v0.2 interpretation.
    metadata = protocol.read_metadata(conn, ctx.scope_id, object_id)
    result["protocol"] = metadata
    legacy = metadata["interpretation_status"] == "legacy_v0_2"
    for label in ("latest", "effective"):
        rid = obj.get(f"{label}_revision_id")
        result[f"{label}_revision"] = db.revision_row(conn, ctx, object_id, str(rid)) if rid else None
    if not legacy:
        return db.jsonable(result)
    if obj["object_type"] == "WorkItem":
        result["delivery"] = delivery.read_work_item(conn, ctx, obj)
    elif obj["object_type"] == "FeedbackThread":
        result["feedback"] = feedback_state(conn, ctx, obj)
    elif obj["object_type"] == "CompanyOutcome":
        assessment = delivery.read_outcome_assessment(conn, ctx, object_id, obj["effective_revision_id"])
        result["outcome_assessment"] = assessment
        result["outcome_achievement"] = assessment["assessment_result"] if assessment else "not_assessed"
    elif obj["object_type"] == "Deliverable":
        result["delivery_review"] = delivery.read_delivery_review(conn, ctx, object_id, obj["latest_revision_id"])
    return db.jsonable(result)


def feedback_state(conn, ctx, obj: dict) -> dict:
    """Read MF handoff evidence with current authorization on every source.

    Consumers can resume the independent MF review after another person logs in
    without maintaining a second business ledger or reusing a delivery review.
    """
    state = conn.execute(
        "SELECT * FROM gov_feedback_state WHERE scope_id=%s AND object_id=%s",
        (ctx.scope_id, obj["object_id"]),
    ).fetchone()
    reviews = conn.execute(
        "SELECT * FROM gov_acceptances WHERE scope_id=%s AND feedback_object_id=%s ORDER BY recorded_at,acceptance_id",
        (ctx.scope_id, obj["object_id"]),
    ).fetchall()
    revision_ids = set()
    if state and state["resolution_decision_revision_id"]:
        revision_ids.add(str(state["resolution_decision_revision_id"]))
    for review in reviews:
        revision_ids.add(str(review["decision_revision_id"]))
        revision_ids.update(str(rid) for rid in review["evidence_revision_ids"])
    sources = {}
    for rid in sorted(revision_ids):
        source = conn.execute(
            "SELECT object_id FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
            (ctx.scope_id, rid),
        ).fetchone()
        if source is None:
            raise GovernedError("NOT_FOUND")
        source_id = str(source["object_id"])
        db.revision_row(conn, ctx, source_id, rid)
        sources[rid] = source_id
    decision = None
    decision_revision = None
    if state and state["resolution_decision_revision_id"]:
        rid = str(state["resolution_decision_revision_id"])
        decision = object_state(conn, ctx, sources[rid])
        decision_revision = db.revision_row(conn, ctx, sources[rid], rid)
    if state and state["resolution_adjustment_object_id"]:
        db.object_row(conn, ctx, str(state["resolution_adjustment_object_id"]))
    return db.jsonable({"state": state, "resolution_decision": decision,
                       "resolution_decision_revision": decision_revision, "acceptances": reviews})


def revision(conn, ctx, object_id: str, revision_id: str) -> dict:
    if _is_a2_object(conn, ctx, object_id):
        return db.jsonable(_delegate_a2_reader("revision", conn, ctx, object_id, revision_id))
    result = db.jsonable(db.revision_row(conn, ctx, object_id, revision_id))
    # A1-12: revision reads carry the object's current protocol identity, just
    # like object GET; stored revision bytes and payload_hash stay untouched.
    result["protocol"] = protocol.read_metadata(conn, ctx.scope_id, object_id)
    return result


def authorize_receipt(conn, ctx, receipt: dict):
    from .service import _is_a2_receipt
    if _is_a2_receipt(conn, ctx, receipt):
        return _delegate_a2_reader("authorize_receipt", conn, ctx, db.jsonable(receipt))
    # A2 receipts: the A2 reader authorizes by actor identity, current Round
    # membership, or current read rights on the Round's company domain — not
    # by a generic company-domain check that can wrongly deny a DRI's own
    # successful multi-scope receipt.  The actual A2 dispatch decision lives
    # in service._replay/_is_a2_receipt so legacy receipt reads never change.
    domain_id = receipt["result"].get("domain_id")
    if domain_id:
        db.authorize_domain(conn, ctx, str(domain_id), "read")
    ids = {str(item["object_id"]) for item in receipt["object_versions"]}
    ids.update(str(item) for item in receipt["result"].get("referenced_object_ids", []))
    if receipt.get("target_object_id"):
        ids.add(str(receipt["target_object_id"]))
    for object_id in ids:
        db.object_row(conn, ctx, object_id)
    if not ids:
        if str(receipt["principal_id"]) != ctx.principal_id:
            raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
        # An empty receipt (e.g. revocation) cannot be an authorization shortcut.
        if not ctx.assignments:
            raise GovernedError("FORBIDDEN", "No current read assignment", status=403)


def frozen_receipt(row: dict) -> dict:
    return db.jsonable({"receipt_id": row["receipt_id"], "action_type": row["action_type"],
                        "actor_id": row["principal_id"], "auth_epoch": row["auth_epoch"],
                        "status": row["status"], "result": row["result"],
                        "object_versions": row["object_versions"], "effect_task_ids": row["effect_task_ids"],
                        "recorded_at": row["recorded_at"]})


def action_receipt(conn, ctx, receipt_id: str) -> dict:
    receipt = conn.execute("SELECT * FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s",
                           (ctx.scope_id, receipt_id)).fetchone()
    if receipt is None:
        raise GovernedError("NOT_FOUND", "Receipt was not found", status=404)
    authorize_receipt(conn, ctx, receipt)
    tasks = []
    if receipt["effect_task_ids"]:
        tasks = conn.execute(
            "SELECT task_id, state, attempt, error_code, result FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s AND task_id=ANY(%s::uuid[]) ORDER BY task_id",
            (ctx.tenant_id, ctx.company_id, receipt["effect_task_ids"]),
        ).fetchall()
    return {"receipt": frozen_receipt(receipt), "effects": db.jsonable(tasks)}


def _selected_revision(conn, ctx, obj: dict, valid_at: datetime, known_at: datetime):
    if obj["object_type"] in FORMAL_TYPES:
        row = conn.execute(
            """SELECT r.* FROM gov_lifecycle_events e
               JOIN gov_object_revisions r ON r.scope_id=e.scope_id AND r.object_id=e.object_id
                 AND r.revision_id=NULLIF(e.detail->>'effective_revision_id','')::uuid
               WHERE e.scope_id=%s AND e.object_id=%s AND e.recorded_at<=%s
                 AND r.recorded_at<=%s AND r.valid_from<=%s AND (r.valid_to IS NULL OR r.valid_to>%s)
               ORDER BY e.recorded_at DESC, e.event_id DESC LIMIT 1""",
            (ctx.scope_id, obj["object_id"], known_at, known_at, valid_at, valid_at),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND recorded_at<=%s AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s) ORDER BY recorded_at DESC, object_version DESC LIMIT 1",
            (ctx.scope_id, obj["object_id"], known_at, valid_at, valid_at),
        ).fetchone()
    return row


def _sources(conn, ctx, row: dict, known_at: datetime) -> list[dict] | None:
    sources = []
    for reference in delivery.payload_references(row["payload"]):
        parent = db.object_row(conn, ctx, reference["object_id"])
        # Every source must also be read-supported under its current binding;
        # a cross-protocol parent never flows into a legacy interpretation.
        protocol.require_read_support(conn, ctx.scope_id, parent["object_id"])
        source = db.revision_row(conn, ctx, parent["object_id"], reference["revision_id"])
        if timestamp(source["recorded_at"]) > known_at:
            return None
        item = {"object_id": parent["object_id"], "revision_id": reference["revision_id"],
                "payload_hash": source["payload_hash"], "recorded_at": source["recorded_at"]}
        if parent["object_type"] == "EvidenceAsset":
            item["evidence"] = {key: source["payload"][key] for key in ("bucket", "key", "version_id", "sha256", "length")}
        sources.append(item)
    return db.jsonable(sources)


def context_pack(conn, ctx, object_ids: list[str], valid_at: datetime, known_at: datetime) -> dict:
    selected, excluded = [], []
    for object_id in object_ids:
        obj = db.object_row(conn, ctx, object_id)
        # Protocol support is resolved before the legacy FORMAL_TYPES
        # selection: an unsupported or non-legacy object is excluded with an
        # explicit reason, never silently read under legacy meaning.
        metadata = protocol.read_metadata(conn, ctx.scope_id, object_id)
        if metadata["interpretation_status"] != "legacy_v0_2":
            excluded.append({"object_id": object_id,
                             "reason": "protocol_interpretation_not_supported",
                             "protocol": metadata})
            continue
        row = _selected_revision(conn, ctx, obj, valid_at, known_at)
        if row is None:
            excluded.append({"object_id": object_id, "reason": "no_effective_revision_at_requested_times",
                             "protocol": metadata})
            continue
        sources = _sources(conn, ctx, row, known_at)
        if sources is None:
            excluded.append({"object_id": object_id, "reason": "source_not_known_at_requested_time",
                             "protocol": metadata})
            continue
        selection = {"object_id": object_id, "object_type": obj["object_type"],
                                     "revision_id": row["revision_id"], "payload": row["payload"],
                                     "payload_hash": row["payload_hash"], "recorded_at": row["recorded_at"],
                                     "valid_from": row["valid_from"], "valid_to": row["valid_to"],
                                     "source_refs": sources,
                                     "protocol": metadata}
        if obj["object_type"] == "CompanyOutcome":
            assessment = delivery.read_outcome_assessment(conn, ctx, object_id, str(row["revision_id"]), known_at=known_at, valid_at=valid_at)
            selection["outcome_assessment"] = assessment
            selection["outcome_achievement"] = assessment["assessment_result"] if assessment else "not_assessed"
        elif obj["object_type"] == "Deliverable":
            review = delivery.read_delivery_review(conn, ctx, object_id, str(row["revision_id"]), known_at=known_at, valid_at=valid_at)
            selection["delivery_review"] = review
            selection["delivery_status"] = review["verification_result"] if review else "submitted"
        selected.append(db.jsonable(selection))
    snapshot_id = str(uuid.uuid4())
    stored = conn.execute(
        "INSERT INTO gov_context_snapshots(snapshot_id,scope_id,principal_id,valid_at,known_at,selected,excluded) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING recorded_at",
        (snapshot_id, ctx.scope_id, ctx.principal_id, valid_at, known_at, Jsonb(selected), Jsonb(excluded)),
    ).fetchone()
    return db.jsonable({"context_snapshot_id": snapshot_id, "valid_at": valid_at, "known_at": known_at,
                        "selected": selected, "excluded": excluded, "recorded_at": stored["recorded_at"]})


def context_snapshot(conn, ctx, snapshot_id: str) -> dict:
    row = conn.execute("SELECT * FROM gov_context_snapshots WHERE scope_id=%s AND snapshot_id=%s",
                       (ctx.scope_id, snapshot_id)).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND", "Context snapshot was not found", status=404)
    for item in row["selected"]:
        db.object_row(conn, ctx, item["object_id"])
        # Frozen content is never recomputed, but a selected object whose read
        # support was withdrawn since the snapshot must not be served again.
        # The stored item was selected under legacy interpretation, so the
        # current registration must still be exactly legacy (B10).
        protocol.require_legacy_read_support(conn, ctx.scope_id, item["object_id"])
        for source in item.get("source_refs", []):
            db.revision_row(conn, ctx, source["object_id"], source["revision_id"])
            protocol.require_read_support(conn, ctx.scope_id, source["object_id"])
        review = item.get("delivery_review")
        if review:
            db.revision_row(conn, ctx, review["work_item_object_id"], review["work_item_revision_id"])
            protocol.require_legacy_read_support(conn, ctx.scope_id, review["work_item_object_id"])
        assessment = item.get("outcome_assessment")
        if assessment:
            delivery.read_outcome_assessment(conn, ctx, item["object_id"], item["revision_id"], known_at=row["known_at"], valid_at=row["valid_at"])
    for item in row["excluded"]:
        db.object_row(conn, ctx, item["object_id"])
        for source in item.get("source_refs", []):
            db.revision_row(conn, ctx, source["object_id"], source["revision_id"])
        review = item.get("delivery_review")
        if review:
            db.revision_row(conn, ctx, review["work_item_object_id"], review["work_item_revision_id"])
        assessment = item.get("outcome_assessment")
        if assessment:
            delivery.read_outcome_assessment(conn, ctx, item["object_id"], item["revision_id"], known_at=row["known_at"], valid_at=row["valid_at"])
    return db.jsonable({"context_snapshot_id": row["snapshot_id"], "valid_at": row["valid_at"],
                        "known_at": row["known_at"], "selected": row["selected"], "excluded": row["excluded"],
                        "recorded_at": row["recorded_at"]})
