"""0.2 lifecycle changes on the existing atomic Method transaction."""
from copy import deepcopy
from datetime import datetime
from . import method_m1a as a, method_m1b as b
from .errors import GovernedError


def deadline_check(e):
    if e.kind not in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
        return
    deadline = datetime.fromisoformat(e.target_revision["payload"]["feedback_deadline"])
    now = e.conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
    if now >= deadline:
        raise GovernedError("REVIEW_DEADLINE_PASSED", "Review deadline passed; explicitly close or reopen the window.")


def _potential_sources(e, payload):
    for reference in payload.get("signal_refs", []):
        head, _ = e.ref(reference, types={"Signal"}, current=False)
        a._phase(e.state(head), "active", "converted")
    for reference in payload.get("source_refs", []):
        e.ref(reference, types={"PeriodReview"} if payload["origin"] == "period_review" else None, current=False)


def collect(e):
    p, kind = e.params, e.kind
    if kind.startswith("m1b_"):
        b.collect(e)
        deadline_check(e)
        if kind in {"m1b_open_window", "m1b_reopen_window", "m1b_reopen_candidates"}:
            value = p["payload"]["feedback_deadline"] if kind == "m1b_open_window" else p["feedback_deadline"]
            if datetime.fromisoformat(value) <= e.conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]:
                raise GovernedError("REVIEW_DEADLINE_PASSED", "New windows require a future feedback deadline.")
        return
    state = deepcopy(e.state(e.target)) if e.target else {}
    e._v02_state = state
    if kind in {"m1a_activate_signal", "m1a_archive_signal"}:
        e.require_role("CEO")
        a._phase(state, *(('captured', 'archived') if kind == "m1a_activate_signal" else ('captured', 'active', 'converted')))
    elif kind in {"m1a_open_potential_issue", "m1a_revise_potential_issue"}:
        a._source_actor(e)
        if e.target:
            a._phase(state, "potential")
            if state.get("created_by") != e.ctx.principal_id:
                e.require_role("CEO")
        _potential_sources(e, p["payload"])
    elif kind == "m1a_confirm_strategic_issue":
        e.require_role("CEO")
        a._phase(state, "potential")
        _potential_sources(e, e.target_revision["payload"])
    elif kind == "m1a_create_direct_issue":
        e.require_role("CEO")
        a._refs(e, p["payload"].get("direct_source_refs", []))
    elif kind == "m1a_publish_brief":
        a._phase(state, "research_assigned", "brief_drafting", "meeting_ready", "report_returned")
        a._agent(e, state, "ceo")
        a._issue_payload(e, p["payload"])
        a._refs(e, p["payload"]["source_refs"])
        if state.get("brief_ref"):
            e.ref(state["brief_ref"], types={"ResearchBrief"})
    elif kind == "m1a_confirm_brief":
        a._phase(state, "brief_drafting")
        a._participant(e, state, "ceo")
        a._selected(e, state, "brief_ref", p["brief_ref"], {"ResearchBrief"})
    elif kind == "m1a_open_meeting" and p.get("brief_ref"):
        a._phase(state, "meeting_ready")
        a._participant(e, state, "dri")
        a._selected(e, state, "brief_ref", p["brief_ref"], {"ResearchBrief"})
        if not a._same(state.get("brief_confirmation", {}).get("brief_ref"), p["brief_ref"]):
            a._fail("CEO must confirm this exact brief before the meeting.")
        a._refs(e, p["material_refs"])
    else:
        a.collect(e)


def run(e):
    collect(e)
    kind, p = e.kind, e.params
    if kind.startswith("m1b_"):
        return b.run(e)
    state = e._v02_state
    if kind in {"m1a_activate_signal", "m1a_archive_signal"}:
        state["phase"] = "archived" if kind == "m1a_archive_signal" else ("converted" if state.get("issue_refs") else "active")
        record = e.review("signal_disposition", a._exact(e.target, e.target_revision), {**p, "phase": state["phase"]})
        e.set_state(e.target, state)
        e.transition(e.target)
        return {**a._exact(e.target, e.target_revision), "phase": state["phase"], "review_record_id": record}
    if kind in {"m1a_open_potential_issue", "m1a_revise_potential_issue"}:
        if e.target:
            head, revision = e.revise(e.target, p["payload"])
        else:
            head, revision = e.create("PotentialIssue", p["payload"])
            e.set_state(head, {"phase": "potential", "created_by": e.ctx.principal_id})
        return {**a._exact(head, revision), "phase": "potential"}
    if kind in {"m1a_confirm_strategic_issue", "m1a_create_direct_issue"}:
        if e.target:
            original = e.target_revision["payload"]
            payload = {k: original[k] for k in ("title", "summary", "business_scope", "urgency", "urgency_reason") if k in original}
            payload.update(potential_issue_ref=a._exact(e.target, e.target_revision), confirmation_reason=p["reason"])
        else:
            payload = p["payload"]
        head, revision = e.create("StrategicIssue", payload)
        e.transition(head, status="active", effective=True)
        reference = a._exact(head, revision)
        e.set_state(head, {"phase": "issue_confirmed", "ceo_principal_id": e.ctx.principal_id})
        record = e.review("strategic_issue_confirmation", reference, {"reason": payload["confirmation_reason"]})
        if e.target:
            state.update(phase="promoted", strategic_issue_ref=reference)
            e.set_state(e.target, state)
            e.transition(e.target)
            for signal in original.get("signal_refs", []):
                source, _ = e.ref(signal, types={"Signal"}, current=False)
                source_state = e.state(source)
                source_state.setdefault("issue_refs", []).append(reference)
                source_state["phase"] = "converted"
                e.set_state(source, source_state)
                e.transition(source)
        return {**reference, "issue_ref": reference, "phase": "issue_confirmed", "review_record_id": record}
    if kind == "m1a_publish_brief":
        reference = a._artifact(e, state, "brief_ref", "ResearchBrief", p["payload"])
        for key in ("brief_confirmation", "precheck"):
            state.pop(key, None)
        state["phase"] = "brief_drafting"
        return a._save(e, state, {"brief_ref": reference})
    if kind == "m1a_confirm_brief":
        record = e.review("brief_sufficiency", p["brief_ref"], {"reason": p["reason"]})
        state.update(phase="meeting_ready", brief_confirmation={"brief_ref": p["brief_ref"], "review_record_id": record})
        return a._save(e, state, {"review_record_id": record})
    if kind == "m1a_open_meeting" and p.get("brief_ref"):
        number = state.get("meeting_round", 0) + 1
        head, revision = e.create("MeetingRound", {**p, "issue_ref": a._exact(e.target, e.target_revision), "round_number": number})
        for key in ("ceo_minutes_ref", "dri_minutes_ref", "final_minutes_ref", "minutes_confirmation"):
            state.pop(key, None)
        reference = a._exact(head, revision)
        state.update(phase="meeting_open", meeting_round=number, meeting_ref=reference)
        return a._save(e, state, {"meeting_ref": reference})
    result = a.run(e)
    if kind == "m1a_record_signal":
        head = e.heads[result["object_id"]]
        e.set_state(head, {"phase": "captured", "created_by": e.ctx.principal_id})
        result["phase"] = "captured"
    return result
