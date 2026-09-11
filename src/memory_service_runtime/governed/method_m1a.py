"""M1A's issue-to-strategy state machine, governed by MethodExecution.

``collect`` is read-only and is shared by prepare and execute.  All mutations
in ``run`` use the caller's single governed transaction; collaboration never
rewrites a business payload.  There is no model-provider or workflow-engine
dependency: agent outputs are attributed inputs, not automatic decisions.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import GovernedError
from .method_m1a_models import M1A_ACTION_PARAMS, M1A_PAYLOAD_MODELS


def _fail(message: str, code: str = "INVALID_STATE") -> None:
    raise GovernedError(code, message)


def _exact(head: dict, revision: dict) -> dict:
    return {"object_id": head["object_id"], "revision_id": revision["revision_id"],
            "payload_hash": revision["payload_hash"]}


def _same(a: dict | None, b: dict | None) -> bool:
    return bool(a and b) and all(a.get(k) == b.get(k) for k in ("object_id", "revision_id", "payload_hash"))


def _phase(state: dict, *allowed: str) -> None:
    if state.get("phase") not in allowed:
        _fail("This action is not available in the current M1A phase.")


def _role(e: Any, role: str, actor_type: str) -> None:
    e.require_role(role, principal_type=actor_type)


def _participant(e: Any, state: dict, person: str) -> None:
    _role(e, "CEO" if person == "ceo" else "DOMAIN_DRI", "human")
    e.require_actor(state[f"{person}_principal_id"], "human")


def _agent(e: Any, state: dict, agent: str) -> None:
    _role(e, {"ceo": "CEO_AGENT", "dri": "PERSONAL_AGENT", "co": "CO_AGENT"}[agent], "agent")
    e.require_actor(state[f"{agent}_agent_id"], "agent")
    if agent in {"ceo", "dri"}:
        e.check_personal_agent(state[f"{agent}_agent_id"], state[f"{agent}_principal_id"])


def _either_dri(e: Any, state: dict) -> None:
    if e.ctx.principal_type == "human":
        _participant(e, state, "dri")
    else:
        _agent(e, state, "dri")


def _source_actor(e: Any) -> None:
    roles = ("CEO", "DOMAIN_DRI") if e.ctx.principal_type == "human" else ("CEO_AGENT", "PERSONAL_AGENT", "CO_AGENT")
    action_domain = getattr(e, "method_scoped_domain", None) or e.domain_id
    held = {a["role"] for a in e.ctx.assignments if a["domain_id"] == action_domain}
    role = next((r for r in roles if r in held), None)
    if role is None:
        _fail("The actor cannot contribute a strategic source.", "FORBIDDEN")
    _role(e, role, e.ctx.principal_type)


def _refs(e: Any, refs: list[dict], types: set[str] | None = None) -> None:
    for reference in refs:
        e.ref(reference, types=types, current=False)


def _selected(e: Any, state: dict, key: str, reference: dict, types: set[str]) -> tuple[dict, dict]:
    if not _same(state.get(key), reference):
        _fail("The action must reference the exact active artifact version.", "STALE_DEPENDENCY")
    return e.ref(reference, types=types)


def _issue_payload(e: Any, payload: dict) -> None:
    if not _same(payload["issue_ref"], _exact(e.target, e.target_revision)):
        _fail("Artifact belongs to a different strategic issue.", "INVALID_REQUEST")


def _changes(e: Any, proposal: dict) -> None:
    """Check current update baselines without changing any strategic target."""
    company_change = next((c for c in proposal["changes"] if c["scope"] == "company"), None)
    for change in proposal["changes"]:
        reference = change.get("target_ref")
        expected_type = "Strategy" if change["scope"] == "company" else "StrategicJudgment"
        if reference:
            head, _ = e.ref(reference, types={expected_type}, effective=True)
            if change["scope"] == "domain" and head["domain_id"] != change["domain_id"]:
                _fail("Judgment target is outside the selected domain.", "INVALID_REQUEST")
        else:
            domain_id = e.domain_id if change["scope"] == "company" else change["domain_id"]
            existing = e.conn.execute(
                "SELECT object_id FROM gov_objects WHERE scope_id=%s AND domain_id=%s "
                "AND object_type=%s AND effective_revision_id IS NOT NULL LIMIT 1",
                (e.ctx.scope_id, domain_id, expected_type),
            ).fetchone()
            if existing:
                _fail("Bootstrap cannot replace an existing effective target.", "STALE_DEPENDENCY")
        payload = change["payload"]
        # Provenance is server-written from the actual confirmed records.
        if payload.get("source_agreement_ref") or payload.get("source_proposal_ref"):
            _fail("Update source references are assigned by the confirming transaction.", "INVALID_REQUEST")
        if change["scope"] == "company":
            for unit in payload["map"]["units"]:
                if unit.get("owner_principal_id"):
                    e.validate_principal(unit["owner_principal_id"], "human")
        else:
            _, strategy = e.ref(payload["strategy_ref"], types={"Strategy"}, effective=True)
            resulting_map = strategy["payload"]["map"]
            if company_change and _same(company_change.get("target_ref"), payload["strategy_ref"]):
                resulting_map = company_change["payload"]["map"]
            if payload["unit_id"] not in {u["unit_id"] for u in resulting_map["units"]}:
                _fail("Judgment names an unknown strategic map unit.", "INVALID_REQUEST")


def collect(e: Any) -> None:
    """Check the whole action, including authority and exact source versions."""
    action = e.kind
    p = M1A_ACTION_PARAMS[action].model_validate(e.params).model_dump(mode="json", exclude_none=True)
    state = deepcopy(e.state(e.target)) if e.target else {}
    prepared: dict[str, Any] = {"params": p, "state": state}
    e._m1a = prepared
    # A content revision also mutates its artifact head. Include that head in
    # prepare's CAS closure before run starts writing it.
    revised_artifact = {
        "m1a_publish_memo": ("memo_ref", "ResearchMemo"),
        "m1a_publish_research_plan": ("plan_ref", "ResearchPlan"),
        "m1a_publish_report": ("report_ref", "ResearchReport"),
        "m1a_reconcile_minutes": ("final_minutes_ref", "MeetingMinutes"),
        "m1a_propose_update": ("proposal_ref", "StrategyUpdateProposal"),
    }.get(action)
    if action == "m1a_publish_minutes":
        revised_artifact = (f"{p['account']}_minutes_ref", "MeetingMinutes")
    if revised_artifact and state.get(revised_artifact[0]):
        e.ref(state[revised_artifact[0]], types={revised_artifact[1]})

    if action == "m1a_record_signal":
        _source_actor(e)
        _refs(e, p["payload"]["source_refs"])
        return
    if action == "m1a_open_potential_issue":
        _source_actor(e)
        _refs(e, p["payload"]["signal_refs"], {"Signal"})
        return
    if action in {"m1a_revise_potential_issue", "m1a_confirm_strategic_issue"}:
        _phase(state, "potential")
        if action == "m1a_confirm_strategic_issue":
            _role(e, "CEO", "human")
            _refs(e, e.target_revision["payload"]["signal_refs"], {"Signal"})
        else:
            _source_actor(e)
            if e.ctx.principal_id != state["created_by"]:
                _role(e, "CEO", "human")
            _refs(e, p["payload"]["signal_refs"], {"Signal"})
        return
    if action == "m1a_assign_research":
        _phase(state, "issue_confirmed")
        _participant(e, state, "ceo")
        # The CEO may appoint a domain DRI to a company research issue. This
        # grants access to this case, not a company-wide DRI assignment.
        e.validate_principal(p["dri_principal_id"], "human", role="DOMAIN_DRI")
        for key, role in (("ceo_agent_id", "CEO_AGENT"), ("dri_agent_id", "PERSONAL_AGENT"), ("co_agent_id", "CO_AGENT")):
            e.validate_principal(p[key], "agent", role=role,
                                 domain_id=None if key == "dri_agent_id" else e.domain_id)
        e.check_personal_agent(p["ceo_agent_id"], state["ceo_principal_id"])
        e.check_personal_agent(p["dri_agent_id"], p["dri_principal_id"])
        return
    if action == "m1a_publish_memo":
        _phase(state, "research_assigned", "memo_clarifying", "memo_ready", "report_returned", "meeting_ready")
        _agent(e, state, "ceo")
        _issue_payload(e, p["payload"])
        _refs(e, p["payload"]["source_refs"])
        return
    if action in {"m1a_record_clarification", "m1a_check_memo", "m1a_direct_clarification"}:
        _phase(state, "memo_clarifying")
        _selected(e, state, "memo_ref", p["memo_ref"], {"ResearchMemo"})
        if action == "m1a_record_clarification":
            _either_dri(e, state)
        elif action == "m1a_check_memo":
            agent = "ceo" if e.ctx.principal_id == state["ceo_agent_id"] else "dri"
            _agent(e, state, agent)
            prepared["agent"] = agent
        else:
            person = "ceo" if e.ctx.principal_id == state["ceo_principal_id"] else "dri"
            _participant(e, state, person)
            if not any(c["result"] == "needs_clarification" for c in state.get("memo_checks", {}).values()):
                _fail("Direct clarification follows an unresolved agent check.")
        return
    if action == "m1a_publish_research_plan":
        _phase(state, "memo_ready")
        _participant(e, state, "dri")
        _issue_payload(e, p["payload"])
        _selected(e, state, "memo_ref", p["payload"]["memo_ref"], {"ResearchMemo"})
        return
    if action == "m1a_publish_report":
        _phase(state, "plan_published", "report_drafting", "report_returned", "meeting_ready")
        _either_dri(e, state)
        _issue_payload(e, p["payload"])
        _selected(e, state, "plan_ref", p["payload"]["plan_ref"], {"ResearchPlan"})
        _refs(e, p["payload"]["evidence_refs"])
        return
    if action in {"m1a_submit_report", "m1a_precheck_report"}:
        _phase(state, "report_drafting" if action == "m1a_submit_report" else "report_submitted")
        _selected(e, state, "report_ref", p["report_ref"], {"ResearchReport"})
        if action == "m1a_submit_report":
            _participant(e, state, "dri")
        else:
            _agent(e, state, "ceo")
        return
    if action == "m1a_open_meeting":
        _phase(state, "meeting_ready")
        _participant(e, state, "dri")
        _selected(e, state, "report_ref", p["report_ref"], {"ResearchReport"})
        gate = state.get("precheck", {})
        if gate.get("result") != "pass" or not _same(gate.get("report_ref"), p["report_ref"]):
            _fail("Meeting requires a passing precheck of this exact report.")
        _refs(e, p["material_refs"])
        return
    if action == "m1a_publish_minutes":
        _phase(state, "meeting_open", "minutes_drafting")
        _agent(e, state, p["account"])
        _selected(e, state, "meeting_ref", p["meeting_ref"], {"MeetingRound"})
        _refs(e, p["source_refs"])
        return
    if action == "m1a_reconcile_minutes":
        _phase(state, "minutes_drafting")
        _agent(e, state, "ceo")
        _selected(e, state, "meeting_ref", p["meeting_ref"], {"MeetingRound"})
        _, ceo = _selected(e, state, "ceo_minutes_ref", p["ceo_minutes_ref"], {"MeetingMinutes"})
        _, dri = _selected(e, state, "dri_minutes_ref", p["dri_minutes_ref"], {"MeetingMinutes"})
        if ceo["payload"]["body"] != dri["payload"]["body"] and not p["differences"]:
            _fail("Different minutes require an explicit discrepancy resolution.", "INVALID_REQUEST")
        return
    if action == "m1a_confirm_minutes":
        _phase(state, "minutes_reconciled")
        _participant(e, state, "dri")
        _selected(e, state, "final_minutes_ref", p["minutes_ref"], {"MeetingMinutes"})
        return
    if action == "m1a_confirm_agreement":
        _phase(state, "minutes_confirmed")
        _participant(e, state, "ceo")
        _selected(e, state, "final_minutes_ref", p["minutes_ref"], {"MeetingMinutes"})
        return
    if action == "m1a_decide_update":
        _phase(state, "agreement_confirmed")
        _participant(e, state, "ceo")
        _selected(e, state, "agreement_ref", p["agreement_ref"], {"StrategicAgreement"})
        return
    if action == "m1a_propose_update":
        _phase(state, "update_requested", "update_returned", "update_proposed", "update_reviewed")
        _agent(e, state, "ceo")
        _issue_payload(e, p["payload"])
        _selected(e, state, "agreement_ref", p["payload"]["agreement_ref"], {"StrategicAgreement"})
        _changes(e, p["payload"])
        return
    if action in {"m1a_review_update", "m1a_confirm_update"}:
        _phase(state, "update_proposed" if action == "m1a_review_update" else "update_reviewed")
        _, proposal = _selected(e, state, "proposal_ref", p["proposal_ref"], {"StrategyUpdateProposal"})
        _changes(e, proposal["payload"])
        prepared["proposal"] = proposal["payload"]
        if action == "m1a_review_update":
            _agent(e, state, "co")
            scopes = {c["scope"] for c in proposal["payload"]["changes"]}
            expected = "company_and_domain" if len(scopes) == 2 else next(iter(scopes))
            if p["impact_level"] != expected:
                _fail("Impact review must cover the proposal's exact change scope.", "INVALID_REQUEST")
        else:
            _participant(e, state, "ceo")
            gate = state.get("update_review", {})
            if not gate.get("accepted") or not _same(gate.get("proposal_ref"), p["proposal_ref"]):
                _fail("CEO confirmation needs the accepted review of this exact proposal.")
        return
    _fail("Unknown M1A action.", "INVALID_REQUEST")


def _artifact(e: Any, state: dict, key: str, object_type: str, payload: dict) -> dict:
    payload = M1A_PAYLOAD_MODELS[object_type].model_validate(payload).model_dump(mode="json", exclude_none=True)
    if state.get(key):
        head, _ = e.ref(state[key], types={object_type})
        head, revision = e.revise(head, payload)
    else:
        head, revision = e.create(object_type, payload)
    reference = _exact(head, revision)
    state[key] = reference
    return reference


def _save(e: Any, state: dict, result: dict | None = None) -> dict:
    e.set_state(e.target, state)
    e.transition(e.target)
    return {"object_id": e.target["object_id"], "revision_id": e.target_revision["revision_id"],
            "issue_ref": _exact(e.target, e.target_revision), "phase": state["phase"], **(result or {})}


def run(e: Any) -> dict:
    """Execute one checked step. The caller commits receipt, audit and writes."""
    collect(e)
    action = e.kind
    p, state = e._m1a["params"], e._m1a["state"]
    if action in {"m1a_record_signal", "m1a_open_potential_issue"}:
        object_type = "Signal" if action == "m1a_record_signal" else "PotentialIssue"
        head, revision = e.create(object_type, p["payload"], domain_id=p["domain_id"])
        new_state = {"phase": "recorded" if object_type == "Signal" else "potential", "created_by": e.ctx.principal_id}
        e.set_state(head, new_state)
        return {**_exact(head, revision), "phase": new_state["phase"]}
    if action == "m1a_revise_potential_issue":
        head, revision = e.revise(e.target, p["payload"])
        return {**_exact(head, revision), "phase": "potential"}
    if action == "m1a_confirm_strategic_issue":
        potential_ref = _exact(e.target, e.target_revision)
        original = e.target_revision["payload"]
        head, revision = e.create("StrategicIssue", {
            "title": original["title"], "summary": original["summary"],
            "potential_issue_ref": potential_ref, "confirmation_reason": p["reason"],
        })
        head = e.transition(head, status="active", effective=True)
        issue_ref = _exact(head, revision)
        e.set_state(head, {"phase": "issue_confirmed", "ceo_principal_id": e.ctx.principal_id})
        state.update(phase="promoted", strategic_issue_ref=issue_ref)
        e.review("strategic_issue_confirmation", potential_ref, p)
        e.set_state(e.target, state)
        e.transition(e.target)
        return {**issue_ref, "issue_ref": issue_ref, "phase": "issue_confirmed"}
    if action == "m1a_assign_research":
        state.update(p, phase="research_assigned")
        e.review("research_assignment", _exact(e.target, e.target_revision), p)
        return _save(e, state)
    if action == "m1a_publish_memo":
        reference = _artifact(e, state, "memo_ref", "ResearchMemo", p["payload"])
        state.update(phase="memo_clarifying", memo_checks={})
        # Reopening research invalidates report/meeting gates, not their history.
        state.pop("precheck", None)
        return _save(e, state, {"memo_ref": reference})
    if action in {"m1a_record_clarification", "m1a_check_memo", "m1a_direct_clarification"}:
        record = e.review(action.removeprefix("m1a_"), p["memo_ref"], p)
        if action == "m1a_check_memo":
            checks = state.setdefault("memo_checks", {})
            checks[e._m1a["agent"]] = {**p, "record_id": record, "principal_id": e.ctx.principal_id}
            if all(checks.get(agent, {}).get("result") == "clear" for agent in ("ceo", "dri")):
                state["phase"] = "memo_ready"
        else:
            # New unresolved input cannot be bypassed with an earlier clear check.
            state["memo_checks"] = {}
        return _save(e, state, {"review_record_id": record})
    if action == "m1a_publish_research_plan":
        reference = _artifact(e, state, "plan_ref", "ResearchPlan", p["payload"])
        state["phase"] = "plan_published"
        return _save(e, state, {"plan_ref": reference})
    if action == "m1a_publish_report":
        reference = _artifact(e, state, "report_ref", "ResearchReport", p["payload"])
        state.update(phase="report_drafting")
        state.pop("precheck", None)
        return _save(e, state, {"report_ref": reference})
    if action == "m1a_submit_report":
        record = e.review("research_report_submission", p["report_ref"], p)
        state["phase"] = "report_submitted"
        return _save(e, state, {"review_record_id": record})
    if action == "m1a_precheck_report":
        record = e.review("research_quality_precheck", p["report_ref"], p)
        state.update(phase="meeting_ready" if p["result"] == "pass" else "report_returned",
                     precheck={**p, "record_id": record})
        return _save(e, state, {"review_record_id": record})
    if action == "m1a_open_meeting":
        round_number = state.get("meeting_round", 0) + 1
        head, revision = e.create("MeetingRound", {
            "title": p["title"], "issue_ref": _exact(e.target, e.target_revision),
            "report_ref": p["report_ref"], "round_number": round_number,
            "objective": p["objective"], "material_refs": p["material_refs"],
        })
        for key in ("ceo_minutes_ref", "dri_minutes_ref", "final_minutes_ref", "minutes_confirmation"):
            state.pop(key, None)
        reference = _exact(head, revision)
        state.update(phase="meeting_open", meeting_round=round_number, meeting_ref=reference)
        return _save(e, state, {"meeting_ref": reference})
    if action == "m1a_publish_minutes":
        payload = {**p, "issue_ref": _exact(e.target, e.target_revision), "differences": []}
        reference = _artifact(e, state, f"{p['account']}_minutes_ref", "MeetingMinutes", payload)
        state["phase"] = "minutes_drafting"
        return _save(e, state, {"minutes_ref": reference})
    if action == "m1a_reconcile_minutes":
        payload = {"title": p["title"], "issue_ref": _exact(e.target, e.target_revision),
                   "meeting_ref": p["meeting_ref"], "account": "reconciled", "body": p["body"],
                   "source_refs": [p["ceo_minutes_ref"], p["dri_minutes_ref"]], "differences": p["differences"]}
        reference = _artifact(e, state, "final_minutes_ref", "MeetingMinutes", payload)
        state["phase"] = "minutes_reconciled"
        return _save(e, state, {"minutes_ref": reference})
    if action == "m1a_confirm_minutes":
        record = e.review("meeting_minutes_confirmation", p["minutes_ref"], p)
        head, _ = e.ref(p["minutes_ref"], types={"MeetingMinutes"})
        e.transition(head, status="active", effective=True)
        state.update(phase="minutes_confirmed", minutes_confirmation=record)
        return _save(e, state, {"review_record_id": record})
    if action == "m1a_confirm_agreement":
        payload = {**p, "issue_ref": _exact(e.target, e.target_revision), "meeting_ref": state["meeting_ref"]}
        head, revision = e.create("StrategicAgreement", payload)
        e.transition(head, status="active", effective=True)
        reference = _exact(head, revision)
        record = e.review("strategic_agreement_confirmation", reference, p)
        state.update(phase="agreement_confirmed" if p["meeting_goal_achieved"] else "meeting_ready",
                     agreement_ref=reference)
        return _save(e, state, {"agreement_ref": reference, "review_record_id": record})
    if action == "m1a_decide_update":
        record = e.review("strategy_adjustment_decision", p["agreement_ref"], p)
        state.update(phase="update_requested" if p["needs_update"] else "completed",
                     update_decision={**p, "record_id": record})
        if not p["needs_update"]:
            state.update(outcome="no_change", completion_reason=p["reason"])
        return _save(e, state, {"review_record_id": record})
    if action == "m1a_propose_update":
        reference = _artifact(e, state, "proposal_ref", "StrategyUpdateProposal", p["payload"])
        state.update(phase="update_proposed")
        state.pop("update_review", None)
        return _save(e, state, {"proposal_ref": reference})
    if action == "m1a_review_update":
        record = e.review("strategy_update_impact_review", p["proposal_ref"], p)
        state.update(phase="update_reviewed" if p["accepted"] else "update_returned",
                     update_review={**p, "record_id": record})
        return _save(e, state, {"review_record_id": record})
    if action == "m1a_confirm_update":
        changed_refs = []
        activated_strategy = None
        company_baseline = None
        # A domain judgment within the same change must cite the resulting
        # Strategy, while its proposal binds the old exact baseline. Ordering
        # supplied by the client does not determine business meaning.
        changes = sorted(e._m1a["proposal"]["changes"], key=lambda c: c["scope"] != "company")
        for change in changes:
            if change["scope"] == "company":
                head, revision = e.activate_strategy(change.get("target_ref"), change["payload"],
                    source_agreement_ref=state["agreement_ref"], source_proposal_ref=p["proposal_ref"])
                activated_strategy, company_baseline = _exact(head, revision), change.get("target_ref")
            else:
                payload = {**change["payload"], "source_agreement_ref": state["agreement_ref"],
                           "source_proposal_ref": p["proposal_ref"]}
                if activated_strategy and _same(company_baseline, payload["strategy_ref"]):
                    payload["strategy_ref"] = activated_strategy
                if change.get("target_ref"):
                    target, _ = e.ref(change["target_ref"], types={"StrategicJudgment"}, effective=True)
                    head, revision = e.revise(target, payload, status="active", effective=True)
                else:
                    head, revision = e.create("StrategicJudgment", payload, domain_id=change["domain_id"])
                    head = e.transition(head, status="active", effective=True)
            changed_refs.append(_exact(head, revision))
        record = e.review("strategy_update_confirmation", p["proposal_ref"], {**p, "changed_refs": changed_refs})
        state.update(phase="completed", outcome="updated", changed_refs=changed_refs, confirmation_record_id=record)
        return _save(e, state, {"changed_refs": changed_refs, "review_record_id": record})
    _fail("Unknown M1A action.", "INVALID_REQUEST")
