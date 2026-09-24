"""Version-addressed Method queries and role/stage/purpose Context Packs."""
from __future__ import annotations
from datetime import datetime
from uuid import uuid4
from psycopg.types.json import Jsonb
from . import db, protocol, method_access as access
from .errors import GovernedError
from .method_profile import CONTRACT_VERSION
from .method_models import METHOD_ACTION_PARAMS

ANALYSIS_TYPES = {"ResearchMemo", "ResearchReport", "ResearchBrief", "PeriodReview", "LTCOReviewAdvice", "StrategyUpdateProposal"}
FORMAL_TYPES = {"StrategicArchitecture", "OperatingState", "Strategy", "StrategicJudgment", "LTCO", "PCO", "Mission", "StrategicAgreement"}


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
    from .method_v05_models import ACTION_PARAMS as V05_PARAMS
    from .method_v04_models import ACTION_PARAMS as V04_PARAMS
    from .method_v03_models import ACTION_PARAMS
    from .method_v02_models import ACTION_PARAMS as OLD_PARAMS
    return (row.get("action_type") in ACTION_PARAMS or row.get("action_type") in OLD_PARAMS
            or row.get("action_type") in V04_PARAMS or row.get("action_type") in V05_PARAMS)


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
            if head["object_type"] == "ReviewWindow" and row["action_type"] in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review", "m1b_replace_comment"}:
                payload = access.raw_revision(conn, ctx, target, head["latest_revision_id"])["payload"]
                domain = access.window_participant(conn, ctx, payload)["domain_id"]
            elif head["object_type"] == "StrategicIssue":
                state = conn.execute("SELECT state FROM gov_method_state WHERE scope_id=%s AND object_id=%s", (ctx.scope_id, target)).fetchone()
                binding = protocol.current_binding(conn, ctx.scope_id, target)
                if binding is not None and binding["contract_version"] in {"tkos.method/0.4", "tkos.method/0.5"}:
                    # 0.4 direct issue initiation has no research assignment gate;
                    # replay still requires a current role in the issue's own domain.
                    domain = head["domain_id"]
                    if state:
                        try:
                            domain = access.research_participant(conn, ctx, state["state"])["domain_id"]
                        except GovernedError:
                            pass
                elif state:
                    domain = access.research_participant(conn, ctx, state["state"])["domain_id"]
        db.authorize_domain(conn, ctx, domain, row["action_type"])


# 决定类 = 有权人对业务对象的正式确认 / 正式化 / 激活 / 重开 / 关闭 / 移交（对象随之
# 进入 confirmed/active/formal 等正式状态，或是该决定点唯一的另一分支，如
# ltco_feedback 之于 ltco_confirmation）；意见类 = 参与者对他人产出发表的看法；
# 分析类 = Agent 产出的分析 / 核验结果；其余（含窗口关闭等程序性步骤）为系统记录。
# 本清单覆盖 method_m1a/m1b/v02/v03/v04/v05.py 里 e.review(...) 实际写过的每个
# kind（含 method_m1a.py 里按 action 名动态拼出的三个、以及仅出现在三元表达式
# else 分支的 ltco_feedback）；逐条依据见 tests/test_method_v05_readers.py 里的
# EXPECTED_REVIEW_EFFECTS。
DECISION_KINDS = frozenset({
    "ltco_confirmation", "review_confirmation", "constraint_confirmation", "candidate_set_activation",
    "agreement_confirmation", "agreement_formalized", "strategy_update_confirmation", "state_confirmation",
    "ceo_reopen", "problem_closure", "problem_transfer",
    "strategic_issue_confirmation", "meeting_minutes_confirmation", "strategic_agreement_confirmation",
    "strategy_adjustment_decision", "architecture_confirmation", "candidate_set_confirmation",
    "signal_disposition", "brief_sufficiency", "ltco_feedback", "agent_issue_initiation", "issue_reframe",
})
OPINION_KINDS = frozenset({"window_comment", "window_opinion_withdrawal", "ltco_revision_response",
                           "record_clarification", "direct_clarification"})
ANALYSIS_KINDS = frozenset({"personal_agent_analysis", "strategy_update_impact_review", "window_resolution",
                            "check_memo", "research_quality_precheck"})


def review_effect(kind):
    if kind in DECISION_KINDS:
        return "decision"
    if kind in OPINION_KINDS:
        return "opinion"
    if kind in ANALYSIS_KINDS:
        return "analysis"
    return "record"


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
        row["effect"] = review_effect(row["kind"])
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
    return {"contract_version": value["protocol"]["contract_version"], "mission_ref": {"object_id": object_id, "revision_id": revision["revision_id"], "payload_hash": revision["payload_hash"]},
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


def context_pack(conn, ctx, object_ids, valid_at, known_at, stage, purpose, include_drafts=False,
                 contract_version=CONTRACT_VERSION, *, research_run=None):
    if contract_version in {"tkos.method/0.2", "tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5"}:
        if stage not in CONTEXT_STAGE_KEYS or purpose not in CONTEXT_PURPOSES | {"dialogue", "research"}:
            raise GovernedError("INVALID_REQUEST", "Unknown lifecycle Context stage or purpose.", status=422)
        if purpose == "research" and research_run is None:
            raise GovernedError("FORBIDDEN", "Use the authorized research Context endpoint.")
        if research_run is not None:
            research_gate(conn, ctx, research_run, object_ids, contract_version)
    selected, excluded, seen = [], [], set()
    selected_stage, selected_purpose, state_keys, selection_notes = _context_selection(stage, purpose)
    if contract_version in {"tkos.method/0.2", "tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5"}:
        selected_purpose, selection_notes = purpose, []
        if stage in {"general", "research", "meeting", "confirmation"}:
            state_keys = state_keys | {"brief_ref"}
    if contract_version in {"tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5"}:
        state_keys = state_keys | {"architecture_ref", "canonical_ref", "recommendation_ref", "source_refs", "issue_ref"}
    if contract_version in {"tkos.method/0.4", "tkos.method/0.5"}:
        state_keys = state_keys | {"participants", "strategy_ref", "transferred_problems"}
    context = {"contract_version": contract_version, "stage": stage, "purpose": purpose,
               "actor_id": ctx.principal_id, "actor_type": ctx.principal_type,
               "roles": sorted({a["role"] for a in db._assignments(conn, ctx)}), "include_drafts": include_drafts,
               "selection_stage": selected_stage, "selection_purpose": selected_purpose,
               "selection_notes": selection_notes}
    if research_run is not None:
        context.update(research_run_ref=research_run, research_root_ids=object_ids)
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
            if metadata["protocol_id"] != "tkos.method" or metadata["contract_version"] != contract_version:
                excluded.append({"object_id": oid if explicit else None, "reason": "different_protocol", "context_request": context})
                continue
            if contract_version in {"tkos.method/0.2", "tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5"} and head["object_type"] == "Signal" and research_run is None:
                excluded.append({"object_id": oid if explicit else None, "reason": "signal_not_for_dialogue", "context_request": context})
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


def research_gate(conn, ctx, run_ref, roots, contract_version="tkos.method/0.2"):
    if ctx.principal_type != "agent":
        raise GovernedError("FORBIDDEN", "Research Context requires the Agent's own identity.")
    head = access.head(conn, ctx, run_ref["object_id"])
    revision = access.revision(conn, ctx, head["object_id"], run_ref["revision_id"])
    protocol.gate_dependency(conn, ctx.scope_id, head["object_id"], contract_version)
    if head["object_type"] != "MethodRun" or revision["payload_hash"] != run_ref["payload_hash"]:
        raise GovernedError("INVALID_REQUEST", "An exact MethodRun reference is required.")
    assignments = db.authorize_domain(conn, ctx, head["domain_id"], "method_record_attempt")
    if not any(a["role"] in {"CEO_AGENT", "CO_AGENT", "PERSONAL_AGENT"} for a in assignments):
        raise GovernedError("FORBIDDEN")
    if contract_version in {"tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5"} and any(a['role']=='CEO_AGENT' for a in assignments):
        bindings=conn.execute('SELECT owner_principal_id FROM gov_method_agent_bindings WHERE scope_id=%s AND agent_principal_id=%s',(ctx.scope_id,ctx.principal_id)).fetchall()
        owners=set()
        for binding in db.jsonable(bindings):
            try:
                agent=access.personal_agent(conn,ctx,ctx.principal_id,binding['owner_principal_id'])
                if agent['domain_id'] != head['domain_id']:
                    continue
                people=conn.execute("SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND domain_id=%s AND role='CEO'",(ctx.scope_id,binding['owner_principal_id'],head['domain_id'])).fetchall()
                for person in people:
                    access.assignment(conn,ctx,str(person['assignment_id']),binding['owner_principal_id'],'human',head['domain_id'])
                    owners.add(binding['owner_principal_id'])
            except GovernedError:
                continue
        if len(owners)!=1:
            raise GovernedError('FORBIDDEN','Intake Context requires the current CEO identity binding.')
    run = conn.execute("SELECT phase FROM gov_method_runs WHERE scope_id=%s AND run_id=%s",
                       (ctx.scope_id, head["object_id"])).fetchone()
    if not run or run["phase"] != "running":
        raise GovernedError("INVALID_STATE", "Research run is not running.")
    for oid in roots:
        access.head(conn, ctx, oid)
        member = conn.execute("SELECT 1 FROM gov_method_run_members WHERE scope_id=%s AND run_id=%s AND object_id=%s",
                              (ctx.scope_id, head["object_id"], oid)).fetchone()
        if not member:
            raise GovernedError("FORBIDDEN", "Research roots must be attached by the run owner.")


def snapshot(conn, ctx, row, *, research=False):
    context = next((item.get("context_request") for item in row["selected"] + row["excluded"] if item.get("context_request")), {})
    if context.get("contract_version") in {"tkos.method/0.2", "tkos.method/0.3", "tkos.method/0.4", "tkos.method/0.5"}:
        if context.get("purpose") == "research":
            if not research or context.get("actor_id") != ctx.principal_id:
                raise GovernedError("FORBIDDEN", "Research snapshots are not dialogue Context.")
            research_gate(conn, ctx, context["research_run_ref"], context["research_root_ids"], context["contract_version"])
        elif any(i["object_type"] == "Signal" for i in row["selected"]):
            raise GovernedError("FORBIDDEN", "This historical snapshot is not valid dialogue Context.")
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


def confirmations(conn, ctx, object_id):
    """一个对象的决定类记录与承诺行；不可见对象按 head 的 NOT_FOUND / FORBIDDEN 处理，
    承诺行还要按责任版本与候选版本两侧各自的当前可见性过滤：一行承诺带着另一个
    人的 principal_id、assignment_id 与承诺文本，请求对象自身的 head 校验并不能
    代表另一侧引用的可见性（撤权后历史内容也要按当前权限读取）。"""
    access.head(conn, ctx, object_id)
    rows = conn.execute(
        "SELECT * FROM gov_method_reviews WHERE scope_id=%s AND target_object_id=%s ORDER BY recorded_at, record_id",
        (ctx.scope_id, object_id)).fetchall()
    items = []
    for row in db.jsonable(rows):
        if review_effect(row["kind"]) != "decision":
            continue
        try:
            access.revision(conn, ctx, row["target_object_id"], row["target_revision_id"])
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                continue
            raise
        row["effect"] = "decision"
        items.append(row)
    commitment_rows = db.jsonable(conn.execute(
        """SELECT commitment_id, candidate_object_id, candidate_revision_id, responsibility_object_id,
                  responsibility_revision_id, principal_id, assignment_id, statement, recorded_at
           FROM gov_method_commitments WHERE scope_id=%s AND (responsibility_object_id=%s OR candidate_object_id=%s)
           ORDER BY recorded_at, commitment_id""",
        (ctx.scope_id, object_id, object_id)).fetchall())
    commitments = []
    for row in commitment_rows:
        try:
            access.revision(conn, ctx, row["responsibility_object_id"], row["responsibility_revision_id"])
            access.revision(conn, ctx, row["candidate_object_id"], row["candidate_revision_id"])
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                continue
            raise
        commitments.append(row)
    return {"items": items, "commitments": commitments}


def _overlaps(period, start, end):
    return _time(period["start"]) < _time(end) and _time(period["end"]) > _time(start)


def company_view(conn, ctx, period_start, period_end):
    """按主 Scope 汇总当前正式 LTCO、时段内 PCO / Mission 与生效 Constraint；投影，不是对象。"""
    rows = db.jsonable(conn.execute(
        """SELECT o.object_id, o.object_type, r.payload FROM gov_objects o
           JOIN gov_object_revisions r ON (r.scope_id, r.object_id, r.revision_id) = (o.scope_id, o.object_id, o.effective_revision_id)
           WHERE o.scope_id=%s AND o.object_type IN ('LTCO','PCO','Mission','Constraint')
           ORDER BY o.object_type, o.object_id""", (ctx.scope_id,)).fetchall())
    # 这条 SQL 按对象类型选出同一 scope 里所有协议版本的 LTCO/PCO/Mission/
    # Constraint——0.1-0.4 的 Mission（A2、0.1、0.3 各自的 MissionPayload）根本
    # 没有 period / primary_scope_id 字段。必须先按当前协议绑定把非 0.5 的行
    # 挡在外面，再去按字段名读 payload；否则混合协议 scope 里的任何调用者都会
    # 因为别的对象形状不同而炸 KeyError，与他自己看不看得见那个对象无关。
    metadata = protocol.list_metadata(conn, ctx.scope_id, [str(row["object_id"]) for row in rows])
    rows = [row for row in rows if metadata[str(row["object_id"])]["contract_version"] == "tkos.method/0.5"]
    scopes, company_constraints, mission_constraints = {}, [], {}

    def visible(oid):
        if not access.is_method_object(conn, ctx, oid):
            return None
        try:
            item = object_state(conn, ctx, oid)
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                return None
            raise
        # 只认 0.5 自身的投影：scope 内若还留有 0.4 对象，按 0.5 摆放等于就地
        # 重解释历史（合同 §1）；生效版本对调用者不可读时，也不能用它的载荷
        # 判定所属 Scope / 时段（SQL 是按 payload 原样读取的）。
        if item["protocol"]["contract_version"] != "tkos.method/0.5" or item["effective_revision_id"] is None:
            return None
        return item

    def bucket(unit):
        return scopes.setdefault(unit, {"scope_id": unit, "ltco": None, "pcos": [], "missions": [], "constraints": []})

    for row in rows:
        oid, kind, payload = str(row["object_id"]), row["object_type"], row["payload"]
        if kind == "Constraint":
            if not _overlaps(payload["effective"], period_start, period_end):
                continue
            item = visible(oid)
            if item is None:
                continue
            applies = payload["applies_to"]
            if applies["kind"] == "company":
                company_constraints.append(item)
            elif applies["kind"] == "scope":
                bucket(applies["scope_id"])["constraints"].append(item)
            else:
                mission_constraints.setdefault(str(applies["mission_ref"]["object_id"]), []).append(item)
            continue
        if kind != "LTCO" and not _overlaps(payload["period"], period_start, period_end):
            continue
        item = visible(oid)
        if item is None:
            continue
        target = bucket(payload["primary_scope_id"])
        if kind == "LTCO":
            target["ltco"] = item
        elif kind == "PCO":
            target["pcos"].append(item)
        else:
            record = (item.get("method_state") or {}).get("owner_activation_record_id")
            item["owner_effective_from"] = None
            if record:
                found = conn.execute(
                    "SELECT target_object_id, target_revision_id, recorded_at FROM gov_method_reviews WHERE scope_id=%s AND record_id=%s",
                    (ctx.scope_id, record)).fetchone()
                if found:
                    found = db.jsonable(found)
                    # candidate_set_activation 是针对那次被激活的 CandidateSet
                    # 版本写的；请求方能读到这个 Mission 不代表也能读到那个
                    # CandidateSet 版本——撤权后历史内容也要按当前权限读取，
                    # 与 /reviews、/confirmations 对同一条记录的处理口径一致。
                    try:
                        access.revision(conn, ctx, found["target_object_id"], found["target_revision_id"])
                    except GovernedError as exc:
                        if exc.code not in {"NOT_FOUND", "FORBIDDEN"}:
                            raise
                    else:
                        item["owner_effective_from"] = found["recorded_at"]
            target["missions"].append(item)
    return {"schema_version": "method-read/0.5", "period": {"start": period_start, "end": period_end},
            "scopes": [scopes[key] for key in sorted(scopes)],
            "company_constraints": company_constraints, "mission_constraints": mission_constraints}
