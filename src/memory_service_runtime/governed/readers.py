"""Authorized version and historical context reads from authoritative records."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from psycopg.types.json import Jsonb

from memory_service_runtime.governed import db
from memory_service_runtime.governed.errors import GovernedError


FORMAL_TYPES = {"BusinessCommitment", "ExecutionCommitment", "CompanyOutcome", "Decision"}


def timestamp(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def object_state(conn, ctx, object_id: str) -> dict:
    obj = db.object_row(conn, ctx, object_id)
    result = dict(obj)
    for label in ("latest", "effective"):
        rid = obj.get(f"{label}_revision_id")
        result[f"{label}_revision"] = db.revision_row(conn, ctx, object_id, str(rid)) if rid else None
    return db.jsonable(result)


def revision(conn, ctx, object_id: str, revision_id: str) -> dict:
    return db.jsonable(db.revision_row(conn, ctx, object_id, revision_id))


def authorize_receipt(conn, ctx, receipt: dict):
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
    for reference in row["payload"].get("upstream_refs", []):
        parent = db.object_row(conn, ctx, reference["object_id"])
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
        row = _selected_revision(conn, ctx, obj, valid_at, known_at)
        if row is None:
            excluded.append({"object_id": object_id, "reason": "no_effective_revision_at_requested_times"})
            continue
        sources = _sources(conn, ctx, row, known_at)
        if sources is None:
            excluded.append({"object_id": object_id, "reason": "source_not_known_at_requested_time"})
            continue
        selected.append(db.jsonable({"object_id": object_id, "object_type": obj["object_type"],
                                     "revision_id": row["revision_id"], "payload": row["payload"],
                                     "payload_hash": row["payload_hash"], "recorded_at": row["recorded_at"],
                                     "valid_from": row["valid_from"], "valid_to": row["valid_to"],
                                     "source_refs": sources}))
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
    for item in [*row["selected"], *row["excluded"]]:
        db.object_row(conn, ctx, item["object_id"])
        for source in item.get("source_refs", []):
            db.revision_row(conn, ctx, source["object_id"], source["revision_id"])
    return db.jsonable({"context_snapshot_id": row["snapshot_id"], "valid_at": row["valid_at"],
                        "known_at": row["known_at"], "selected": row["selected"], "excluded": row["excluded"],
                        "recorded_at": row["recorded_at"]})
