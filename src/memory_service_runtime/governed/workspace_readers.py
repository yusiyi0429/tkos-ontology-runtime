"""Current-authority projections for the existing Clark work surfaces."""
from . import db, method_access as access, method_readers, workspace_service as scene
from .errors import GovernedError


def identity(conn, ctx):
    assignments = db._assignments(conn, ctx)
    principal = conn.execute("SELECT display_name FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
                             (ctx.scope_id, ctx.principal_id)).fetchone()
    return {"scope_id": ctx.scope_id, "tenant_id": ctx.tenant_id, "company_id": ctx.company_id,
            "principal_id": ctx.principal_id, "principal_type": ctx.principal_type,
            "display_name": principal["display_name"], "auth_epoch": ctx.auth_epoch,
            "assignments": assignments}


def missing(reason="not_recorded"):
    return {"status": "missing", "reason": reason, "value": None}


def responsibility(conn, ctx, assignment_id):
    # IDs come only from an already authorized scene/window definition.
    row = conn.execute("""SELECT a.assignment_id,a.principal_id,a.domain_id,a.role,p.display_name,p.principal_type,
        (a.active AND p.active AND a.valid_from<=clock_timestamp()
         AND (a.valid_to IS NULL OR a.valid_to>clock_timestamp())) AS current
        FROM gov_role_assignments a JOIN gov_principals p ON (a.scope_id,a.principal_id)=(p.scope_id,p.principal_id)
        WHERE a.scope_id=%s AND a.assignment_id=%s""", (ctx.scope_id, assignment_id)).fetchone()
    return db.jsonable(row) if row else missing("responsibility_unavailable")


def operation(conn, ctx, seed, head, kind, state_reason=None):
    try:
        scene.authorize_write(conn, ctx, seed, head, kind)
        reason = state_reason
    except GovernedError:
        reason = "current_role_not_permitted"
    return {"kind": kind, "allowed": reason is None, "reason": reason,
            "authority": "advisory_rechecked_on_submit"}


def read(conn, ctx, scene_id):
    history = scene.rows(conn, ctx, scene_id)
    seed, head = scene.authorize_scene(conn, ctx, history)
    visible = []
    for event in history:
        try:
            scene.authorize_event_read(conn, ctx, history, event)
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                continue
            raise
        visible.append(event)
    live = scene.active(visible)
    withdrawn = {e["payload"]["event_id"] for e in visible if e["kind"] == "withdraw"}
    # CAS version describes the scene stream; counts below describe visible data only.
    result = {"contract_version": scene.CONTRACT, "scene_id": scene_id,
              "version": history[-1]["version"], "definition": seed,
              "responsibilities": {"owner": responsibility(conn, ctx, seed["owner_assignment_id"]),
                  "participants": [responsibility(conn, ctx, aid) for aid in seed["participant_assignment_ids"]]},
              "events": [{**e, "withdrawn": e["event_id"] in withdrawn} for e in visible],
              "formal_effect": "none", "materials": {}, "personal": {}, "operations": []}
    for kind in {"monthly_material", "weekly_material", "meeting_material", "meeting_publish"}:
        value = scene.latest(visible, kind)
        actual = scene.latest(history, kind)
        result["materials"][kind] = ({"status": "available", "value": value}
            if value and value == actual else missing("not_recorded_or_not_authorized"))
    for kind in scene.PERSONAL:
        value = scene.latest(visible, kind, ctx.principal_id)
        result["personal"][kind] = value
    for kind in sorted(scene.SCENE_KINDS[seed["scene_type"]] | {"read", "request_supplement", "bring_to_meeting"}):
        reason = None
        if kind in {"weekly_answer", "weekly_confirm"} and result["materials"]["weekly_material"]["status"] != "available":
            reason = "weekly_material_missing"
        if kind == "weekly_confirm" and not reason:
            pack = scene.latest(visible, "weekly_material")
            answers = scene.current_answers(visible, pack["event_id"], ctx.principal_id)
            if set(answers) != {q["question_id"] for q in pack["payload"]["questions"]}:
                reason = "current_personal_answers_missing"
        if kind == "meeting_start" and scene.latest(live, "meeting_start"):
            reason = "meeting_already_started"
        if kind == "meeting_finish" and (not scene.latest(live, "meeting_start") or scene.latest(live, "meeting_finish")):
            reason = "meeting_not_in_progress"
        if kind == "meeting_material" and not scene.latest(live, "meeting_finish"):
            reason = "meeting_not_ended"
        if kind == "meeting_publish" and result["materials"]["meeting_material"]["status"] != "available":
            reason = "transcript_material_missing"
        if kind == "diff_response":
            state = method_readers.object_state(conn, ctx, head["object_id"])["method_state"]
            if state.get("phase") not in {"resolved", "confirmed"}:
                reason = "current_candidate_missing"
            try:
                access.window_participant(conn, ctx, access.revision(conn, ctx, head["object_id"], seed["anchor_ref"]["revision_id"])["payload"])
            except GovernedError:
                reason = "current_window_participant_required"
        result["operations"].append(operation(conn, ctx, seed, head, kind, reason))
    if seed["scene_type"] == "monthly":
        result["monthly"] = monthly(conn, ctx, head["object_id"])
        result["monthly"]["presentation_materials"] = {}
        ack = scene.latest(visible, "diff_response", ctx.principal_id)
        current_candidate = result["monthly"]["candidate"].get("ref")
        result["monthly"]["reviewed_current_candidate"] = bool(ack and current_candidate
            and ack["payload"]["candidate_ref"] == current_candidate and ack["payload"]["response"] == "reviewed"
            and result["monthly"]["window"]["phase"] in {"resolved", "confirmed"})
        for event in live:
            if event["kind"] == "monthly_material":
                payload = event["payload"]
                result["monthly"]["presentation_materials"][payload["target_ref"]["object_id"]] = event
                if payload["feedback_deadline"] and result["monthly"]["feedback_deadline"].get("authority") != "runtime":
                    result["monthly"]["feedback_deadline"] = {"status": "available", "value": payload["feedback_deadline"], "source_event_id": event["event_id"]}
    elif seed["scene_type"] == "weekly":
        pack = scene.latest(visible, "weekly_material")
        if pack != scene.latest(history, "weekly_material"):
            pack = None
        confirmation = scene.latest(visible, "weekly_confirm", ctx.principal_id)
        answers = scene.current_answers(visible, pack["event_id"], ctx.principal_id) if pack else {}
        current = bool(pack and confirmation and confirmation["payload"]["material_event_id"] == pack["event_id"]
                       and set(confirmation["payload"]["answer_event_ids"]) == {a["event_id"] for a in answers.values()})
        refresh = scene.latest(visible, "refresh_sources")
        actual_pack = scene.latest(history, "weekly_material")
        refresh_state = "pending_partner_refresh" if refresh and (not actual_pack or refresh["version"] > actual_pack["version"]) else "no_pending_request"
        if actual_pack and not pack and refresh_state == "no_pending_request":
            refresh_state = "material_not_authorized"
        result["weekly"] = {"answers": list(answers.values()), "confirmed_current_material": current,
            "source_refresh": refresh_state,
            "period_review_approval": "not_applicable"}
    else:
        pack, publication = scene.latest(visible, "meeting_material"), scene.latest(visible, "meeting_publish")
        if pack != scene.latest(history, "meeting_material"):
            pack = None
        if publication != scene.latest(history, "meeting_publish"):
            publication = None
        published = bool(pack and publication and publication["payload"]["material_event_id"] == pack["event_id"])
        result["meeting"] = {"phase": "published" if published else "review" if pack else "ended" if scene.latest(live, "meeting_finish") else "in_progress" if scene.latest(live, "meeting_start") else "scheduled",
            "publication": publication if published else None, "message_delivery": "not_managed_by_runtime",
            "recording_status": "not_managed_by_runtime", "routing_status": "pending_handling" if published else "not_published"}
    return result


def differences(before, after, path=""):
    """Structural changes only. Interpretations/rationales stay source-attributed."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                result.append({"field_path": child, "before": before.get(key), "after": after.get(key),
                               "change": "added" if key in after else "removed"})
            else:
                result.extend(differences(before[key], after[key], child))
        return result
    if before == after:
        return []
    return [{"field_path": path or "/", "before": before, "after": after, "change": "changed"}]


def card(conn, ctx, object_id):
    obj = method_readers.object_state(conn, ctx, object_id)
    # Expose only directly authorized revisions; no unfiltered state/ref graph.
    result = {k: obj[k] for k in ("object_id", "object_type", "domain_id", "object_version", "latest_revision", "effective_revision")} | {
        "nature": obj["nature"],
        "phase": obj["method_state"].get("phase"),
        "presentation": {k: missing() for k in ("priority", "why", "milestones", "dependencies", "progress")}}
    if obj['protocol']['contract_version'] == 'tkos.method/0.2':
        from . import lifecycle_readers
        result['protocol'] = obj['protocol']
        result['operations'] = lifecycle_readers.operations(conn, ctx, obj)
    if obj['protocol']['contract_version'] == 'tkos.method/0.3':
        from . import method_v03_readers
        result['protocol'] = obj['protocol']
        result['operations'] = method_v03_readers.operations(conn,ctx,obj)
    return result


def monthly(conn, ctx, window_id):
    obj = method_readers.object_state(conn, ctx, window_id)
    if obj["object_type"] != "ReviewWindow" or not obj["latest_revision"]:
        scene.fail("NOT_FOUND")
    payload, state = obj["latest_revision"]["payload"], obj["method_state"]
    reviews = method_readers.review_records(conn, ctx, window_id)["items"]
    result = {"window": card(conn, ctx, window_id), "business_period": payload["period"],
        "feedback_deadline": missing(), "targets": [], "candidate": missing(), "differences": [],
        "reviews": reviews, "my_reviews": [r for r in reviews if r["principal_id"] == ctx.principal_id],
        "visible_effective_opinion_count": sum(r["effective_opinion"] for r in reviews),
        "members": payload["participants"], "operations": []}
    deadline = payload.get("feedback_deadline")
    if deadline:
        result["feedback_deadline"] = {"status": "available", "value": deadline, "authority": "runtime",
            "source_revision_id": obj["latest_revision"]["revision_id"]}
        deadline_passed = method_readers._time(deadline) <= conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    else:
        deadline_passed = False
    result["member_details"] = [responsibility(conn, ctx, member["assignment_id"]) for member in payload["participants"]]
    originals = {}
    for ref in payload["target_refs"]:
        _, revision = scene.exact(conn, ctx, ref)
        originals[ref["object_id"]] = (ref, revision)
        result["targets"].append({"ref": ref, "revision": revision})
    candidate_ref = state.get("candidate_ref")
    if candidate_ref:
        _, revision = scene.exact(conn, ctx, candidate_ref, {"CandidateSet"})
        check = revision["payload"]
        result["candidate"] = {"status": "available", "ref": candidate_ref, "revision": revision}
        for ref in check["target_refs"]:
            _, target = scene.exact(conn, ctx, ref)
            old_ref, old = originals[ref["object_id"]]
            result["differences"].append({"before_ref": old_ref, "after_ref": ref,
                "fields": differences(old["payload"], target["payload"]),
                "rationale_source": candidate_ref})
    for action in ("m1b_comment", "m1b_withdraw_comment", "m1b_close_window", "m1b_resolve_window", "m1b_confirm_candidates", "m1b_reopen_window", "m1b_reopen_candidates"):
        reason = None
        try:
            if action in {"m1b_comment", "m1b_withdraw_comment"}:
                member = access.window_participant(conn, ctx, payload)
                if ctx.principal_type != "human":
                    scene.fail("FORBIDDEN")
                db.authorize_domain(conn, ctx, member["domain_id"], action)
            else:
                roles = {"CO_AGENT"} if action in {"m1b_close_window", "m1b_resolve_window"} else {"CEO"}
                kind = "agent" if "CO_AGENT" in roles else "human"
                if ctx.principal_type != kind or not any(a["domain_id"] == obj["domain_id"] and a["role"] in roles for a in db._assignments(conn, ctx)):
                    scene.fail("FORBIDDEN")
                db.authorize_domain(conn, ctx, obj["domain_id"], action)
        except GovernedError:
            reason = "current_role_not_permitted"
        phases = {"m1b_comment": {"open"}, "m1b_withdraw_comment": {"open"}, "m1b_close_window": {"open"},
                  "m1b_resolve_window": {"closed"}, "m1b_confirm_candidates": {"resolved"},
                  "m1b_reopen_window": {"closed", "resolved"}, "m1b_reopen_candidates": {"resolved"}}
        if not reason and state.get("phase") not in phases[action]:
            reason = "window_state_not_permitted"
        if not reason and deadline_passed and action in {"m1b_comment", "m1b_withdraw_comment"}:
            reason = "review_deadline_passed"
        result["operations"].append({"action_type": action, "allowed": reason is None, "reason": reason,
            "authority": "advisory_full_dependencies_rechecked_by_method_commit"})
    return result


def list_scenes(conn, ctx, scene_type=None, after=None, limit=25):
    result = []
    found = conn.execute("""SELECT scene_id FROM gov_workspace_events WHERE scope_id=%s AND kind='create'
        AND (%s::text IS NULL OR payload->>'scene_type'=%s)
        AND (%s::uuid IS NULL OR scene_id>%s::uuid) ORDER BY scene_id""",
        (ctx.scope_id, scene_type, scene_type, after, after)).fetchall()
    for row in found:
        try:
            value = read(conn, ctx, str(row["scene_id"]))
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                continue
            raise
        result.append(value)
        if len(result) > limit:
            break
    return {"items": result[:limit], "next_after": result[limit - 1]["scene_id"] if len(result) > limit else None}


def workspace(conn, ctx, view, collection, after=None, limit=25):
    roles = {"CEO"} if view == "ceo" else {"DOMAIN_DRI", "MISSION_DRI"}
    if ctx.principal_type != "human" or (not (view == "dri" and collection in {"missions", "operating-states", "operating-problems"}) and not any(a["role"] in roles for a in db._assignments(conn, ctx))):
        scene.fail("FORBIDDEN")
    scene_types = {"monthly-reviews": "monthly", "weekly-reviews": "weekly", "meetings": "meeting"}
    if collection in scene_types:
        items = []
        found = conn.execute("""SELECT scene_id FROM gov_workspace_events WHERE scope_id=%s AND kind='create'
            AND payload->>'scene_type'=%s AND (%s::uuid IS NULL OR scene_id>%s::uuid) ORDER BY scene_id""",
            (ctx.scope_id, scene_types[collection], after, after)).fetchall()
        for row in found:
            try:
                history = scene.rows(conn, ctx, str(row["scene_id"]))
                _, head = scene.authorize_scene(conn, ctx, history)
                if view == "ceo" and not any(a["role"] == "CEO" and a["domain_id"] == head["domain_id"] for a in ctx.assignments):
                    continue
                value = read(conn, ctx, str(row["scene_id"]))
            except GovernedError as exc:
                if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                    continue
                raise
            items.append(value)
            if len(items) > limit:
                break
        return {"identity": identity(conn, ctx), "collection": collection, "items": items[:limit],
                "next_after": items[limit - 1]["scene_id"] if len(items) > limit else None,
                "refresh_interval_seconds": 5}
    kinds = {"review-windows": "ReviewWindow", "missions": "Mission", "strategies": "Strategy",
             "strategic-issues": "StrategicIssue", "signals": "Signal", "potential-issues": "PotentialIssue", "research-briefs": "ResearchBrief",
             "business-facts": "BusinessFact", "period-reviews": "PeriodReview",
             "architectures": "StrategicArchitecture", "operating-states": "OperatingState",
             "operating-problems": "OperatingProblem"}
    if collection not in kinds:
        scene.fail("INVALID_REQUEST")
    found = conn.execute("""SELECT object_id,domain_id FROM gov_objects WHERE scope_id=%s AND object_type=%s
        AND (%s::uuid IS NULL OR object_id>%s::uuid) ORDER BY object_id""",
        (ctx.scope_id, kinds[collection], after, after)).fetchall()
    items = []
    for row in found:
        if view == "ceo" and not any(a["role"] == "CEO" and a["domain_id"] == str(row["domain_id"]) for a in ctx.assignments):
            continue
        try:
            value = card(conn, ctx, str(row["object_id"]))
            if collection == "operating-problems" and value["phase"] in {"transferred", "resolved", "no_further_action"}:
                continue
            if collection == "missions":
                revision = value["effective_revision"]
                if not revision or (view == "dri" and revision["payload"]["owner_principal_id"] != ctx.principal_id):
                    continue
                value["handoff"] = method_readers.handoff(conn, ctx, str(row["object_id"]))
                value["handoff_state"] = "awaiting_execution_handoff"
            if collection == "review-windows":
                value["monthly"] = monthly(conn, ctx, str(row["object_id"]))
        except GovernedError as exc:
            if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
                continue
            raise
        items.append(value)
        if len(items) > limit:
            break
    return {"identity": identity(conn, ctx), "collection": collection, "items": items[:limit],
            "next_after": items[limit - 1]["object_id"] if len(items) > limit else None,
            "scene_collection_url": "/v1/workspace-scenes", "refresh_interval_seconds": 5}
