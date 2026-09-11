"""M1A decision/state tests. Real transaction/HTTP acceptance is separate."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed import method_m1a as m1a
from memory_service_runtime.governed.method_m1a_models import (
    AssignResearchParams, M1A_ACTION_PARAMS, M1A_ACTION_TARGETS,
    StrategyPayload, StrategyUpdateProposalPayload,
)


class MemoryExecution:
    """Strict in-memory helper double; deliberately does not simulate SQL commits."""

    def __init__(self):
        self.domain_id, self.scope_id = str(uuid4()), str(uuid4())
        self.people = {name: str(uuid4()) for name in ("ceo", "dri", "ceo_agent", "dri_agent", "co_agent", "outsider")}
        self.roles = {"ceo": "CEO", "dri": "DOMAIN_DRI", "ceo_agent": "CEO_AGENT",
                      "dri_agent": "PERSONAL_AGENT", "co_agent": "CO_AGENT", "outsider": "PERSONAL_AGENT"}
        self.objects, self.revisions, self.states, self.records = {}, {}, {}, []
        self.target = self.target_revision = None
        self.conn = self
        self.actor("ceo")
        head, revision = self.create("EvidenceAsset", {"title": "Source bytes", "text": "controlled source"})
        self.evidence = m1a._exact(head, revision)

    def actor(self, name):
        self.ctx = SimpleNamespace(principal_id=self.people[name],
            principal_type="agent" if "agent" in name or name == "outsider" else "human",
            assignments=[{"role": self.roles[name], "domain_id": self.domain_id}], scope_id=self.scope_id)

    def require_role(self, role, principal_type="human", principal_id=None):
        if self.ctx.principal_type != principal_type or role not in {a["role"] for a in self.ctx.assignments}:
            raise GovernedError("FORBIDDEN")
        if principal_id is not None:
            self.require_actor(principal_id, principal_type)

    def require_actor(self, principal_id, principal_type):
        if self.ctx.principal_id != principal_id or self.ctx.principal_type != principal_type:
            raise GovernedError("FORBIDDEN")

    def validate_principal(self, principal_id, principal_type, role=None, domain_id=None):
        name = next((n for n, i in self.people.items() if i == principal_id), None)
        if name is None or (role and self.roles[name] != role):
            raise GovernedError("FORBIDDEN")

    def check_personal_agent(self, agent_id, owner_id):
        if (agent_id, owner_id) not in {(self.people["ceo_agent"], self.people["ceo"]),
                                       (self.people["dri_agent"], self.people["dri"])}:
            raise GovernedError("FORBIDDEN")

    def create(self, object_type, payload, domain_id=None, status="draft"):
        oid = str(uuid4())
        head = {"object_id": oid, "object_type": object_type, "domain_id": domain_id or self.domain_id,
                "object_version": 1, "effective_revision_id": None, "lifecycle_status": status}
        revision = self._revision(head, payload)
        head["latest_revision_id"] = revision["revision_id"]
        self.objects[oid] = head
        return head, revision

    def _revision(self, head, payload):
        revision = {"revision_id": str(uuid4()), "payload": deepcopy(payload), "object_id": head["object_id"],
                    "payload_hash": sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
        self.revisions[revision["revision_id"]] = revision
        return revision

    def revise(self, head, payload, status=None, effective=False):
        revision = self._revision(head, payload)
        head["latest_revision_id"] = revision["revision_id"]
        self.transition(head, status=status, effective=effective)
        return head, revision

    def transition(self, head, status=None, effective=False):
        head["object_version"] += 1
        if status:
            head["lifecycle_status"] = status
        if effective:
            head["effective_revision_id"] = head["latest_revision_id"]
        return head

    def state(self, head):
        return deepcopy(self.states.get(head["object_id"], {}))

    def set_state(self, head, state):
        self.states[head["object_id"]] = deepcopy(state)

    def ref(self, reference, types=None, effective=False, current=True):
        head = self.objects.get(reference["object_id"])
        revision = self.revisions.get(reference["revision_id"])
        if not head or not revision or revision["object_id"] != head["object_id"]:
            raise GovernedError("NOT_FOUND")
        if reference["payload_hash"] != revision["payload_hash"]:
            raise GovernedError("STALE_DEPENDENCY")
        if types and head["object_type"] not in types:
            raise GovernedError("INVALID_REQUEST")
        if current and head["latest_revision_id"] != reference["revision_id"]:
            raise GovernedError("STALE_DEPENDENCY")
        if effective and head["effective_revision_id"] != reference["revision_id"]:
            raise GovernedError("STALE_DEPENDENCY")
        return head, revision

    def review(self, kind, target_ref, content, window_id=None):
        record_id = str(uuid4())
        self.records.append({"record_id": record_id, "kind": kind, "target_ref": deepcopy(target_ref),
                             "content": deepcopy(content), "principal_id": self.ctx.principal_id})
        return record_id

    def execute(self, query, args):
        _, domain, object_type = args
        found = next((o for o in self.objects.values() if o["domain_id"] == domain
                      and o["object_type"] == object_type and o["effective_revision_id"]), None)
        return SimpleNamespace(fetchone=lambda: found)

    def activate_strategy(self, target_ref, payload, source_agreement_ref, source_proposal_ref):
        payload = {**payload, "source_agreement_ref": source_agreement_ref, "source_proposal_ref": source_proposal_ref}
        if target_ref:
            head, _ = self.ref(target_ref, types={"Strategy"}, effective=True)
            return self.revise(head, payload, status="active", effective=True)
        head, revision = self.create("Strategy", payload)
        self.transition(head, status="active", effective=True)
        return head, revision

    def call(self, actor, action, params, target=None):
        self.actor(actor)
        self.kind, self.params = f"m1a_{action}", params
        self.target = self.objects[target] if target else None
        self.target_revision = self.revisions[self.target["latest_revision_id"]] if target else None
        m1a.collect(self)
        return m1a.run(self)

    def current_ref(self, oid):
        head = self.objects[oid]
        return m1a._exact(head, self.revisions[head["latest_revision_id"]])


@pytest.fixture
def execution():
    return MemoryExecution()


def _start(e):
    signal = e.call("ceo_agent", "record_signal", {"domain_id": e.domain_id, "payload": {
        "title": "Market change", "kind": "external", "description": "A sourced signal", "source_refs": [e.evidence]}})
    potential = e.call("ceo_agent", "open_potential_issue", {"domain_id": e.domain_id, "payload": {
        "title": "Strategic question", "summary": "Clarify the strategic choice", "signal_refs": [e.current_ref(signal["object_id"])]}})
    issue = e.call("ceo", "confirm_strategic_issue", {"reason": "Requires research"}, potential["object_id"])
    oid = issue["object_id"]
    e.call("ceo", "assign_research", {"dri_principal_id": e.people["dri"], "ceo_agent_id": e.people["ceo_agent"],
        "dri_agent_id": e.people["dri_agent"], "co_agent_id": e.people["co_agent"]}, oid)
    return oid


def _memo(e, oid, question="What should change?"):
    return e.call("ceo_agent", "publish_memo", {"payload": {"title": "Research memo", "issue_ref": e.current_ref(oid),
        "question": question, "scope": "Company strategy", "expected_output": "Sourced recommendation", "source_refs": [e.evidence]}}, oid)["memo_ref"]


def _memo_ready(e, oid):
    memo = _memo(e, oid)
    for actor in ("ceo_agent", "dri_agent"):
        e.call(actor, "check_memo", {"memo_ref": memo, "result": "clear", "explanation": "Scope and expected output are clear"}, oid)
    return memo


def _report(e, oid, memo):
    plan = e.call("dri", "publish_research_plan", {"payload": {"title": "Research plan", "issue_ref": e.current_ref(oid),
        "memo_ref": memo, "human_work": ["Interview domain experts"], "agent_work": ["Compare evidence"],
        "method": "Evaluate alternative explanations", "due_at": "2026-10-01T00:00:00Z"}}, oid)["plan_ref"]
    payload = {"title": "Research report", "issue_ref": e.current_ref(oid), "plan_ref": plan,
               "findings": ["Two viable alternatives"], "conclusions": ["Run a focused initiative"],
               "limitations": ["Synthetic fixture; no claim of research quality"], "evidence_refs": [e.evidence]}
    report = e.call("dri_agent", "publish_report", {"payload": payload}, oid)["report_ref"]
    return report, payload


def _submit_pass(e, oid, report):
    e.call("dri", "submit_report", {"report_ref": report, "statement": "I submit this exact report"}, oid)
    e.call("ceo_agent", "precheck_report", {"report_ref": report, "result": "pass", "findings": ["Required evidence and limitations are included"]}, oid)


def _meeting(e, oid, report, achieved=True):
    meeting = e.call("dri", "open_meeting", {"report_ref": report, "title": "Strategic review", "objective": "Resolve the strategic choice",
        "material_refs": [report, e.evidence]}, oid)["meeting_ref"]
    accounts = {}
    for account in ("ceo", "dri"):
        accounts[account] = e.call(f"{account}_agent", "publish_minutes", {"meeting_ref": meeting,
            "title": f"{account} minutes", "account": account, "body": f"{account} account of scope",
            "source_refs": [e.evidence]}, oid)["minutes_ref"]
    final = e.call("ceo_agent", "reconcile_minutes", {"meeting_ref": meeting, "ceo_minutes_ref": accounts["ceo"],
        "dri_minutes_ref": accounts["dri"], "title": "Reconciled minutes", "body": "Shared scope and unresolved choices",
        "differences": [{"topic": "Scope", "ceo_account": "Company", "dri_account": "Business domain", "resolution": "Company decision, explicit domain implications"}]}, oid)["minutes_ref"]
    e.call("dri", "confirm_minutes", {"minutes_ref": final, "statement": "Accurate record of this meeting"}, oid)
    return e.call("ceo", "confirm_agreement", {"minutes_ref": final, "title": "Strategic agreement",
        "statement": "Adopt the stated direction" if achieved else "More discussion is needed",
        "meeting_goal_achieved": achieved, "reason": "Documented CEO assessment"}, oid)["agreement_ref"]


def _proposal(e, oid, agreement, target=None):
    change = {"scope": "company", "payload": {"title": "Company Strategy", "statement": "Focus on the agreed direction",
        "map": {"units": [{"unit_id": "growth", "name": "Growth", "judgment": "Invest selectively", "owner_principal_id": e.people["dri"]}]}}}
    if target:
        change["target_ref"] = target
    return e.call("ceo_agent", "propose_update", {"payload": {"title": "Strategic update proposal", "issue_ref": e.current_ref(oid),
        "agreement_ref": agreement, "rationale": "Implements the confirmed agreement", "changes": [change]}}, oid)["proposal_ref"]


def test_complete_m1a_loops_preserve_decision_boundaries(execution):
    e = execution
    oid = _start(e)
    memo_v1 = _memo(e, oid)
    e.call("dri_agent", "check_memo", {"memo_ref": memo_v1, "result": "needs_clarification", "explanation": "Expected outcome is ambiguous"}, oid)
    e.call("dri", "direct_clarification", {"memo_ref": memo_v1, "content": "CEO and DRI clarified the objective directly"}, oid)
    memo_v2 = _memo(e, oid, "Which company direction meets the agreed criteria?")
    assert memo_v2["object_id"] == memo_v1["object_id"] and memo_v2 != memo_v1
    for actor in ("ceo_agent", "dri_agent"):
        e.call(actor, "check_memo", {"memo_ref": memo_v2, "result": "clear", "explanation": "Clarification incorporated"}, oid)
    report_v1, payload = _report(e, oid, memo_v2)
    e.call("dri", "submit_report", {"report_ref": report_v1, "statement": "Ready for precheck"}, oid)
    e.call("ceo_agent", "precheck_report", {"report_ref": report_v1, "result": "return", "findings": ["Add counterevidence"]}, oid)
    payload["findings"].append("Counterevidence is now considered")
    report_v2 = e.call("dri", "publish_report", {"payload": payload}, oid)["report_ref"]
    assert report_v1 != report_v2
    _submit_pass(e, oid, report_v2)
    first = _meeting(e, oid, report_v2, achieved=False)
    assert e.states[oid]["phase"] == "meeting_ready"
    agreement = _meeting(e, oid, report_v2)
    assert first != agreement and e.states[oid]["meeting_round"] == 2
    e.call("ceo", "decide_update", {"agreement_ref": agreement, "needs_update": True, "reason": "Company direction changes"}, oid)
    proposal = _proposal(e, oid, agreement)
    e.call("co_agent", "review_update", {"proposal_ref": proposal, "accepted": True, "impact_level": "company", "findings": ["Scope and baselines reviewed"]}, oid)
    result = e.call("ceo", "confirm_update", {"proposal_ref": proposal, "reason": "Approve this exact update"}, oid)
    strategy, revision = e.ref(result["changed_refs"][0], effective=True)
    assert strategy["object_type"] == "Strategy"
    assert revision["payload"]["source_agreement_ref"] == agreement
    assert revision["payload"]["source_proposal_ref"] == proposal
    assert revision["payload"]["map"]["units"][0]["unit_id"] == "growth"
    assert e.states[oid]["outcome"] == "updated"
    assert not any(o["object_type"] in {"Mission", "ExecutionAuthority"} for o in e.objects.values())
    confirmations = [r["kind"] for r in e.records if r["kind"].endswith("confirmation")]
    assert confirmations.count("meeting_minutes_confirmation") == 2
    assert confirmations.count("strategic_agreement_confirmation") == 2
    assert confirmations.count("strategy_update_confirmation") == 1


def test_no_change_preserves_existing_strategy_and_keeps_issue_content(execution):
    e = execution
    strategy, old = e.create("Strategy", {"title": "Existing strategy"})
    e.transition(strategy, effective=True)
    old_head = deepcopy(strategy)
    oid = _start(e)
    issue_ref = e.current_ref(oid)
    report, _ = _report(e, oid, _memo_ready(e, oid))
    _submit_pass(e, oid, report)
    agreement = _meeting(e, oid, report)
    e.call("ceo", "decide_update", {"agreement_ref": agreement, "needs_update": False, "reason": "Evidence supports the existing direction"}, oid)
    assert strategy == old_head and e.revisions[old["revision_id"]] == old
    assert e.current_ref(oid) == issue_ref
    assert e.states[oid]["phase"] == "completed" and e.states[oid]["outcome"] == "no_change"
    assert e.objects[oid]["lifecycle_status"] != "closed"


def test_only_both_bound_agents_can_clear_memo_and_comments_are_not_revisions(execution):
    e = execution
    oid = _start(e)
    memo = _memo(e, oid)
    before = len(e.revisions)
    e.call("ceo_agent", "check_memo", {"memo_ref": memo, "result": "clear", "explanation": "Clear"}, oid)
    assert e.states[oid]["phase"] == "memo_clarifying"
    with pytest.raises(GovernedError, match="permit") as error:
        e.call("outsider", "check_memo", {"memo_ref": memo, "result": "clear", "explanation": "I impersonate DRI agent"}, oid)
    assert error.value.code == "FORBIDDEN"
    e.call("dri_agent", "check_memo", {"memo_ref": memo, "result": "clear", "explanation": "Clear"}, oid)
    assert e.states[oid]["phase"] == "memo_ready" and len(e.revisions) == before


def test_old_report_precheck_cannot_release_new_report(execution):
    e = execution
    oid = _start(e)
    old, payload = _report(e, oid, _memo_ready(e, oid))
    _submit_pass(e, oid, old)
    payload["conclusions"] = ["A changed conclusion"]
    current = e.call("dri", "publish_report", {"payload": payload}, oid)["report_ref"]
    assert "precheck" not in e.states[oid]
    with pytest.raises(GovernedError) as blocked:
        e.call("dri", "open_meeting", {"report_ref": current, "title": "Try previous pass", "objective": "Discuss",
               "material_refs": [e.evidence]}, oid)
    assert blocked.value.code == "INVALID_STATE"
    e.call("dri", "submit_report", {"report_ref": current, "statement": "New report"}, oid)
    with pytest.raises(GovernedError) as error:
        e.call("ceo_agent", "precheck_report", {"report_ref": old, "result": "pass", "findings": ["Old report checked"]}, oid)
    assert error.value.code == "STALE_DEPENDENCY"
    assert e.states[oid]["phase"] == "report_submitted"
    e.call("ceo_agent", "precheck_report", {"report_ref": current, "result": "pass", "findings": ["Rechecked the current report"]}, oid)
    assert e.states[oid]["phase"] == "meeting_ready"


def test_agent_cannot_publish_human_decision(execution):
    e = execution
    oid = _start(e)
    memo = _memo_ready(e, oid)
    with pytest.raises(GovernedError) as error:
        e.call("dri_agent", "publish_research_plan", {"payload": {"title": "Plan", "issue_ref": e.current_ref(oid), "memo_ref": memo,
            "human_work": ["Interview"], "agent_work": ["Analyze"], "method": "Research", "due_at": "2026-10-01T00:00:00Z"}}, oid)
    assert error.value.code == "FORBIDDEN"


def test_ceo_confirmation_rechecks_current_strategy_baseline(execution):
    e = execution
    strategy, rev = e.create("Strategy", {"title": "Old", "statement": "Old", "map": {"units": [{"unit_id": "growth", "name": "Growth", "judgment": "Wait"}]}})
    e.transition(strategy, effective=True)
    baseline = m1a._exact(strategy, rev)
    oid = _start(e)
    report, _ = _report(e, oid, _memo_ready(e, oid))
    _submit_pass(e, oid, report)
    agreement = _meeting(e, oid, report)
    e.call("ceo", "decide_update", {"agreement_ref": agreement, "needs_update": True, "reason": "Change"}, oid)
    proposal = _proposal(e, oid, agreement, target=baseline)
    e.call("co_agent", "review_update", {"proposal_ref": proposal, "accepted": True, "impact_level": "company", "findings": ["Reviewed"]}, oid)
    e.revise(strategy, {**rev["payload"], "statement": "Another issue already changed this"}, effective=True)
    with pytest.raises(GovernedError) as error:
        e.call("ceo", "confirm_update", {"proposal_ref": proposal, "reason": "Try stale proposal"}, oid)
    assert error.value.code == "STALE_DEPENDENCY"
    assert e.states[oid]["phase"] == "update_reviewed"
    fresh_proposal = _proposal(e, oid, agreement, target=e.current_ref(strategy["object_id"]))
    with pytest.raises(GovernedError) as unreviewed:
        e.call("ceo", "confirm_update", {"proposal_ref": fresh_proposal, "reason": "Try previous Co-agent review"}, oid)
    assert unreviewed.value.code == "INVALID_STATE"
    e.call("co_agent", "review_update", {"proposal_ref": fresh_proposal, "accepted": True, "impact_level": "company", "findings": ["Reviewed current basis"]}, oid)
    e.call("ceo", "confirm_update", {"proposal_ref": fresh_proposal, "reason": "Current proposal confirmed"}, oid)
    assert e.states[oid]["outcome"] == "updated"


def test_strict_model_and_action_registry_boundaries():
    assert set(M1A_ACTION_PARAMS) == set(M1A_ACTION_TARGETS)
    with pytest.raises(ValidationError):
        AssignResearchParams(dri_principal_id=str(uuid4()), ceo_agent_id="x", dri_agent_id="x", co_agent_id="x")
    with pytest.raises(ValidationError):
        StrategyPayload.model_validate({"title": "S", "statement": "S", "map": {"units": [
            {"unit_id": "same", "name": "A", "judgment": "A"}, {"unit_id": "same", "name": "B", "judgment": "B"}]}})
    with pytest.raises(ValidationError):
        M1A_ACTION_PARAMS["m1a_confirm_update"].model_validate({"proposal_ref": {"object_id": str(uuid4()),
            "revision_id": str(uuid4()), "payload_hash": "a" * 64}, "reason": "Confirm", "actor_id": str(uuid4())})
