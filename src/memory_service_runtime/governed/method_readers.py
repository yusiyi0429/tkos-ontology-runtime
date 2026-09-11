"""Version-addressed Method queries and role/stage/purpose Context Packs."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from psycopg.types.json import Jsonb
from . import db, protocol, method_access as access
from .errors import GovernedError
from .method_profile import CONTRACT_VERSION
from .method_models import METHOD_ACTION_PARAMS

ANALYSIS_TYPES = {"ResearchMemo", "ResearchReport", "PeriodReview", "LTCOReviewAdvice", "StrategyUpdateProposal"}
FORMAL_TYPES = {"Strategy", "StrategicJudgment", "LTCO", "PCO", "Mission", "StrategicAgreement"}


def refs(value):
    if isinstance(value, dict):
        if isinstance(value.get("object_id"), str) and isinstance(value.get("revision_id"), str):
            yield value
        else:
            for child in value.values():
                yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def nature(kind):
    if kind == "EvidenceAsset":
        return "original_evidence"
    if kind == "BusinessFact":
        return "business_fact"
    if kind in ANALYSIS_TYPES:
        return "agent_analysis"
    if kind == "StrategicAgreement":
        return "human_decision"
    return "business_content"


def object_state(conn, ctx, object_id):
    head, allowed = access.head_access(conn, ctx, object_id)
    metadata = protocol.require_read_support(conn, ctx.scope_id, object_id)
    result = {**head, "protocol": metadata, "nature": nature(head["object_type"])}
    result["authorized_revision_ids"] = sorted(allowed) if allowed is not None else None
    for label in ("latest", "effective"):
        rid = head.get(label + "_revision_id")
        result[label + "_revision"] = (access.revision(conn, ctx, object_id, rid)
            if rid and (allowed is None or rid in allowed) else None)
        if rid and allowed is not None and rid not in allowed:
            result[label + "_revision_id"] = None
    row = conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s", (ctx.scope_id, object_id)).fetchone()
    result["method_state"] = db.jsonable(row["state"]) if row else {}
    result["impact_notices"] = db.jsonable(conn.execute("SELECT impact_id,strategy_object_id,strategy_revision_id,target_revision_id,old_strategy_ref,effect,recorded_at FROM gov_method_impacts WHERE scope_id=%s AND target_object_id=%s ORDER BY recorded_at,impact_id", (ctx.scope_id, object_id)).fetchall())
    return result


def revision(conn, ctx, object_id, revision_id):
    value = access.revision(conn, ctx, object_id, revision_id)
    return {**value, "protocol": protocol.require_read_support(conn, ctx.scope_id, object_id)}


def is_receipt(row):
    return row.get("action_type") in METHOD_ACTION_PARAMS


def authorize_receipt(conn, ctx, row, *, replay=False):
    row = db.jsonable(row)
    ids = {x["object_id"] for x in row.get("object_versions", [])}
    ids.update(row["result"].get("referenced_object_ids", []))
    if row.get("target_object_id"):
        ids.add(row["target_object_id"])
    for oid in ids:
        access.head(conn, ctx, oid)
    for reference in refs(row["result"]):
        access.revision(conn, ctx, reference["object_id"], reference["revision_id"])
    if replay:
        if row["principal_id"] != ctx.principal_id:
            raise GovernedError("FORBIDDEN")
        for aid in row["result"].get("required_assignment_ids", []):
            access.assignment(conn, ctx, aid)
        # Actor must still hold a current policy-permitted action role; scope
        # membership or an unrelated read role cannot revive a revoked command.
        target = row.get("target_object_id")
        domain = row["result"].get("domain_id")
        if target:
            head = access.head(conn, ctx, target)
            if head["object_type"] == "ReviewWindow" and row["action_type"] in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
                payload = access.raw_revision(conn, ctx, target, head["latest_revision_id"])["payload"]
                domain = access.window_participant(conn, ctx, payload)["domain_id"]
            elif head["object_type"] == "StrategicIssue":
                state = conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s", (ctx.scope_id, target)).fetchone()
                if state:
                    domain = access.research_participant(conn, ctx, state["state"])["domain_id"]
        db.authorize_domain(conn, ctx, domain, row["action_type"])


def review_records(conn, ctx, object_id, *, effective_only=False):
    head = access.head(conn, ctx, object_id)
    rows = conn.execute("SELECT * FROM gov_method_reviews WHERE scope_id=%s AND (target_object_id=%s OR window_id=%s) ORDER BY recorded_at,record_id",
                        (ctx.scope_id, object_id, object_id)).fetchall()
    active = None
    if head["object_type"] == "ReviewWindow":
        state = conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s", (ctx.scope_id, object_id)).fetchone()
        content = state["state"] if state else {}
        active = set(content.get("frozen_opinion_ids", [])) if content.get("phase") != "open" else {rid for items in content.get("active_opinions", {}).values() for rid in items}
    items = []
    for row in db.jsonable(rows):
        try:
            access.revision(conn, ctx, row["target_object_id"], row["target_revision_id"])
        except GovernedError:
            continue
        row["effective_opinion"] = active is not None and row["record_id"] in active
        if not effective_only or row["effective_opinion"]:
            items.append(row)
    return {"items": items}


def list_typed(conn, ctx, object_type, domain_id=None, after=None, limit=50):
    params = [ctx.scope_id, object_type]
    sql = "SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_type=%s"
    if domain_id:
        sql += " AND domain_id=%s"
        params.append(domain_id)
    if after:
        sql += " AND object_id>%s::uuid"
        params.append(after)
    sql += " ORDER BY object_id"
    rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        oid = str(row["object_id"])
        if not access.is_method_object(conn, ctx, oid):
            continue
        try:
            value = object_state(conn, ctx, oid)
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                continue
            raise
        items.append(value)
        if len(items) > limit:
            break
    return {"items": items[:limit], "next_after": items[limit-1]["object_id"] if len(items)>limit else None}


def recovery(conn, ctx, object_id):
    head = access.head(conn, ctx, object_id)
    rows = conn.execute("SELECT r.* FROM gov_method_runs r JOIN gov_method_run_members m ON (m.scope_id,m.run_id)=(r.scope_id,r.run_id) WHERE m.scope_id=%s AND m.object_id=%s", (ctx.scope_id, object_id)).fetchall()
    runs = []
    for row in db.jsonable(rows):
        attempts = conn.execute("SELECT * FROM gov_method_run_attempts WHERE scope_id=%s AND run_id=%s ORDER BY recorded_at,attempt_id", (ctx.scope_id, row["run_id"])).fetchall()
        visible = []
        for attempt in db.jsonable(attempts):
            try:
                access.head(conn, ctx, attempt["target_object_id"] or row["run_id"])
            except GovernedError:
                continue
            visible.append(attempt)
        runs.append({**row, "attempts": visible})
    return {"object_id": head["object_id"], "object_version": head["object_version"], "runs": runs,
            "resume_rule": "Read current state, prepare a legal next action; retry an identical completed command with its original idempotency key."}


def handoff(conn, ctx, object_id):
    value = object_state(conn, ctx, object_id)
    if value["object_type"] != "Mission" or value["method_state"].get("phase") != "confirmed" or not value["effective_revision"]:
        raise GovernedError("INVALID_STATE", "Only an explicitly confirmed Method Mission has a downstream handoff projection.")
    revision = value["effective_revision"]
    return {"contract_version": CONTRACT_VERSION, "mission_ref": {"object_id": object_id, "revision_id": revision["revision_id"], "payload_hash": revision["payload_hash"]},
            "definition": revision["payload"], "confirmation_record_id": value["method_state"].get("confirmation_record_id"),
            "execution_authority": None, "required_next_contract": "Explicit M2 DRI/IC/acceptor assignments, exact Mission baseline, execution and acceptance grants.",
            "delivery_accepted": None, "outcome_achieved": None, "mf_closed": None}


def relation_items(conn, ctx, object_id, revision_id=None):
    head = access.head(conn, ctx, object_id)
    revision_id = revision_id or head["latest_revision_id"]
    value = access.revision(conn, ctx, object_id, revision_id)
    items, excluded = [], []
    seen = set()
    for ref in refs(value["payload"]):
        key = (ref["object_id"], ref["revision_id"])
        if key in seen:
            continue
        seen.add(key)
        try:
            related = access.revision(conn, ctx, *key)
            items.append({"relation_type": "source_reference", "source_object_id": object_id, "source_revision_id": revision_id,
                          "target_object_id": key[0], "target_revision_id": key[1], "payload_hash": related["payload_hash"]})
        except GovernedError as exc:
            if exc.code not in {"NOT_FOUND", "FORBIDDEN"}:
                raise
            excluded.append({"reason": "source_not_authorized"})
    return {"items": items, "excluded": excluded, "next_cursor": None}


def _time(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))


CONTEXT_STAGE_KEYS = {
    "general": {"memo_ref", "plan_ref", "report_ref", "meeting_ref", "ceo_minutes_ref", "dri_minutes_ref",
                "final_minutes_ref", "agreement_ref", "proposal_ref", "changed_refs", "candidate_ref",
                "reopened_window_ref", "corrected_by_ref", "window_id"},
    "research": {"memo_ref", "plan_ref", "report_ref"},
    "meeting": {"report_ref", "meeting_ref", "ceo_minutes_ref", "dri_minutes_ref", "final_minutes_ref", "agreement_ref"},
    "strategic_update": {"report_ref", "final_minutes_ref", "agreement_ref", "proposal_ref", "changed_refs"},
    "planning": {"candidate_ref", "window_id", "corrected_by_ref"},
    "review": {"candidate_ref", "window_id", "reopened_window_ref"},
    "confirmation": {"final_minutes_ref", "agreement_ref", "proposal_ref", "changed_refs", "candidate_ref", "window_id"},
    "period_review": {"corrected_by_ref"},
    "handoff": {"candidate_ref", "window_id"},
}
CONTEXT_PURPOSES = {"general", "analysis", "review", "decision", "handoff"}


def _context_selection(stage, purpose):
    notes = []
    selected_stage = stage if stage in CONTEXT_STAGE_KEYS else "general"
    selected_purpose = purpose if purpose in CONTEXT_PURPOSES else "general"
    if selected_stage != stage:
        notes.append("unknown_stage_uses_general_selection")
    if selected_purpose != purpose:
        notes.append("free_text_purpose_preserved_with_general_selection")
    keys = set(CONTEXT_STAGE_KEYS[selected_stage])
    if selected_purpose == "decision":
        keys -= {"memo_ref", "plan_ref", "ceo_minutes_ref", "dri_minutes_ref"}
    elif selected_purpose == "handoff":
        keys &= {"changed_refs", "candidate_ref", "window_id"}
    return selected_stage, selected_purpose, keys, notes


def _historical_state(conn, ctx, object_id, revision_id, valid_at, known_at):
    """Read a state event belonging to this content version at both cutoffs."""
    row = conn.execute("""SELECT event_id,recorded_at,detail->'method_state' AS state
        FROM gov_lifecycle_events WHERE scope_id=%s AND object_id=%s
        AND detail ? 'method_state' AND detail->>'revision_id'=%s
        AND recorded_at<=%s AND recorded_at<=%s ORDER BY recorded_at DESC,event_id DESC LIMIT 1""",
        (ctx.scope_id, object_id, revision_id, known_at, valid_at)).fetchone()
    return db.jsonable(row) if row else None


def _activation_at(conn, ctx, object_id, revision_id, valid_at, known_at):
    # Creation time is not activation time. A draft created before valid_at but
    # approved afterwards must not appear as an earlier formal baseline.
    first = conn.execute("""SELECT recorded_at FROM gov_lifecycle_events
        WHERE scope_id=%s AND object_id=%s AND detail->>'effective_revision_id'=%s
        AND recorded_at<=%s AND recorded_at<=%s ORDER BY recorded_at,event_id LIMIT 1""",
        (ctx.scope_id, object_id, revision_id, known_at, valid_at)).fetchone()
    if first is None:
        return None
    current = conn.execute("""SELECT detail->>'effective_revision_id' AS revision_id FROM gov_lifecycle_events
        WHERE scope_id=%s AND object_id=%s AND detail ? 'effective_revision_id'
        AND recorded_at<=%s AND recorded_at<=%s ORDER BY recorded_at DESC,event_id DESC LIMIT 1""",
        (ctx.scope_id, object_id, known_at, valid_at)).fetchone()
    return db.jsonable({"activated_at": first["recorded_at"], "current_at_requested_times": bool(current and current["revision_id"] == revision_id)})


def _state_references(state, keys):
    for key in sorted(keys):
        value = state.get(key)
        if key == "window_id" and value:
            yield value, None
        else:
            for reference in refs(value):
                yield reference["object_id"], reference["revision_id"]


def context_pack(conn, ctx, object_ids, valid_at, known_at, stage, purpose, include_drafts=False):
    selected, excluded, seen = [], [], set()
    selected_stage, selected_purpose, state_keys, selection_notes = _context_selection(stage, purpose)
    context = {"contract_version": CONTRACT_VERSION, "stage": stage, "purpose": purpose,
               "actor_id": ctx.principal_id, "actor_type": ctx.principal_type,
               "roles": sorted({a["role"] for a in db._assignments(conn, ctx)}), "include_drafts": include_drafts,
               "selection_stage": selected_stage, "selection_purpose": selected_purpose,
               "selection_notes": selection_notes}
    queue = [(oid, None, True) for oid in object_ids]
    while queue:
        oid, rid, explicit = queue.pop(0)
        if (oid, rid) in seen:
            continue
        seen.add((oid, rid))
        if len(seen) > 500:
            excluded.append({"reason": "context_reference_limit", "context_request": context})
            break
        try:
            head, allowed = access.head_access(conn, ctx, oid)
            metadata = protocol.require_read_support(conn, ctx.scope_id, oid)
            if metadata["protocol_id"] != "tkos.method":
                excluded.append({"object_id": oid if explicit else None, "reason": "different_protocol", "context_request": context})
                continue
            if rid is None:
                if head["object_type"] in FORMAL_TYPES and not include_drafts:
                    candidates = conn.execute("""SELECT DISTINCT r.* FROM gov_lifecycle_events e JOIN gov_object_revisions r
                        ON r.scope_id=e.scope_id AND r.object_id=e.object_id AND r.revision_id=NULLIF(e.detail->>'effective_revision_id','')::uuid
                        WHERE e.scope_id=%s AND e.object_id=%s AND e.recorded_at<=%s AND e.recorded_at<=%s AND r.recorded_at<=%s
                        AND r.valid_from<=%s AND (r.valid_to IS NULL OR r.valid_to>%s) ORDER BY r.object_version DESC""",
                        (ctx.scope_id, oid, known_at, valid_at, known_at, valid_at, valid_at)).fetchall()
                else:
                    candidates = conn.execute("""SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s
                        AND recorded_at<=%s AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s) ORDER BY object_version DESC""",
                        (ctx.scope_id, oid, known_at, valid_at, valid_at)).fetchall()
                candidate = next((r for r in candidates if allowed is None or str(r["revision_id"]) in allowed), None)
                if candidate is None:
                    excluded.append({"object_id": oid, "reason": "no_effective_revision_at_requested_times", "context_request": context})
                    continue
                rid = str(candidate["revision_id"])
            value = access.revision(conn, ctx, oid, rid)
            if _time(value["recorded_at"]) > known_at or _time(value["valid_from"]) > valid_at or (value.get("valid_to") and _time(value["valid_to"]) <= valid_at):
                excluded.append({"object_id": oid, "revision_id": rid, "reason": "not_known_or_valid_at_requested_times", "context_request": context})
                continue
        except GovernedError as exc:
            if exc.code not in {"NOT_FOUND", "FORBIDDEN"}:
                raise
            excluded.append({"object_id": oid if explicit else None, "reason": "not_found_or_not_authorized", "context_request": context})
            continue
        if any(i["object_id"] == oid and i["revision_id"] == rid for i in selected):
            continue
        activation = _activation_at(conn, ctx, oid, rid, valid_at, known_at) if head["object_type"] in FORMAL_TYPES else None
        if head["object_type"] in FORMAL_TYPES and activation is None and not include_drafts:
            excluded.append({"object_id": oid, "revision_id": rid, "reason": "draft_not_requested", "context_request": context})
            continue
        collaborations = review_records(conn, ctx, oid)["items"]
        collaborations = [r for r in collaborations if str(r["target_revision_id"]) == rid
                          and _time(r["recorded_at"]) <= known_at and _time(r["recorded_at"]) <= valid_at]
        historical_state = _historical_state(conn, ctx, oid, rid, valid_at, known_at)
        state = historical_state["state"] if historical_state else {}
        adoption = "analysis_or_draft"
        if activation:
            adoption = "effective_baseline" if activation["current_at_requested_times"] else "historical_effective_baseline"
        selected.append({"object_id": oid, "object_type": head["object_type"], "revision_id": rid,
                         "payload_hash": value["payload_hash"], "payload": value["payload"],
                         "nature": nature(head["object_type"]), "protocol": metadata,
                         "adoption": adoption, "activation": activation,
                         "method_phase": state.get("phase"),
                         "state_event_id": historical_state["event_id"] if historical_state else None,
                         "collaboration_records": collaborations, "context_request": context})
        for reference in refs(value["payload"]):
            queue.append((reference["object_id"], reference["revision_id"], False))
        for related_oid, related_rid in _state_references(state, state_keys):
            queue.append((related_oid, related_rid, False))
        for key in CONTEXT_STAGE_KEYS["general"] - state_keys:
            if state.get(key):
                excluded.append({"object_id": oid, "reason": "not_selected_for_stage_or_purpose",
                                 "relationship": key, "context_request": context})
    snapshot_id = str(uuid4())
    row = conn.execute("INSERT INTO gov_context_snapshots(snapshot_id,scope_id,principal_id,valid_at,known_at,selected,excluded) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING recorded_at",
                       (snapshot_id, ctx.scope_id, ctx.principal_id, valid_at, known_at, Jsonb(selected), Jsonb(excluded))).fetchone()
    return db.jsonable({"context_snapshot_id": snapshot_id, "valid_at": valid_at, "known_at": known_at,
                       "selected": selected, "excluded": excluded, "context_request": context, "recorded_at": row["recorded_at"]})


def snapshot(conn, ctx, row):
    for item in row["selected"]:
        revision = access.revision(conn, ctx, item["object_id"], item["revision_id"])
        if revision["payload_hash"] != item["payload_hash"]:
            raise GovernedError("STALE_DEPENDENCY", "A snapshot does not match its immutable source revision.")
        for record in item.get("collaboration_records", []):
            access.revision(conn, ctx, record["target_object_id"], record["target_revision_id"])
    context = next((item.get("context_request") for item in row["selected"] + row["excluded"] if item.get("context_request")), {})
    # Preserve the attribution of the original snapshot; do not claim that it
    # was generated as the fetching caller or under today's role context.
    return db.jsonable({"context_snapshot_id": row["snapshot_id"], "valid_at": row["valid_at"], "known_at": row["known_at"],
                       "selected": row["selected"], "excluded": row["excluded"], "context_request": context, "recorded_at": row["recorded_at"]})
