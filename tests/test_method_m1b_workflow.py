"""State-machine checks across independent content, opinions, and decisions.

This in-memory adapter exercises business transitions only. It does not claim
HTTP, database, concurrent-write, blob, or permission-boundary acceptance.
"""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import method_m1b
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.method_m1b_models import M1B_ACTION_PARAMS


def uid():
    return str(uuid4())


class Store:
    def __init__(self):
        self.heads, self.revisions, self.states, self.reviews = {}, {}, {}, []
        self.domain_id = uid()
        self.people = {name: uid() for name in ("ceo", "co_agent", "ceo_agent", "a", "b", "a_agent", "outsider")}
        roles = {"ceo": "CEO", "co_agent": "CO_AGENT", "ceo_agent": "CEO_AGENT", "a": "DOMAIN_DRI",
                 "b": "DOMAIN_DRI", "a_agent": "PERSONAL_AGENT", "outsider": "DOMAIN_DRI"}
        self.authority = {pid: {"assignment_id": uid(), "principal_id": pid,
                               "principal_type": "agent" if name.endswith("agent") else "human",
                               "role": roles[name], "active": True, "domain_id": self.domain_id} for name, pid in self.people.items()}
        self.personal = {self.people["a_agent"]: self.people["a"], self.people["ceo_agent"]: self.people["ceo"]}
        strategy = self.exec("ceo").create("Strategy", {"title": "S", "statement": "S", "map": {"units": [
            {"unit_id": "u1", "name": "Domain A", "judgment": "Grow"},
            {"unit_id": "u2", "name": "Domain B", "judgment": "Retain"}]}})
        strategy[0]["effective_revision_id"] = strategy[1]["revision_id"]
        self.strategy = self.exec("ceo").exact_ref(*strategy)

    def exec(self, who, kind=None, params=None, target=None):
        return Execution(self, who, kind, params, target)

    def call(self, who, kind, params, target=None):
        return method_m1b.run(self.exec(who, kind, params, target))

    def payload(self, ref):
        return deepcopy(self.revisions[ref["revision_id"]]["payload"])

    def participant(self, who):
        pid = self.people[who]
        result = {"principal_id": pid, "assignment_id": self.authority[pid]["assignment_id"]}
        if who == "a":
            result["personal_agent_id"] = self.people["a_agent"]
        return result


class Execution:
    def __init__(self, store, who, kind, params, target):
        self.store, self.kind = store, kind
        self.params = M1B_ACTION_PARAMS[kind].model_validate(params).model_dump(exclude_none=True) if kind else {}
        self.domain_id, self.action_id = store.domain_id, uid()
        actor = store.authority[store.people[who]]
        self.ctx = SimpleNamespace(principal_id=actor["principal_id"], principal_type=actor["principal_type"], assignments=[actor])
        if target:
            self.target = store.heads[target["object_id"]]
            self.target_revision = store.revisions[target["revision_id"]]

    def create(self, kind, payload, domain_id=None, status="draft"):
        oid, rid = uid(), uid()
        head = {"object_id": oid, "object_type": kind, "domain_id": domain_id or self.domain_id,
                "latest_revision_id": rid, "effective_revision_id": None, "object_version": 1,
                "lifecycle_status": status}
        revision = self._revision(oid, rid, payload)
        self.store.heads[oid] = head
        self.store.revisions[rid] = revision
        return head, revision

    def _revision(self, oid, rid, payload):
        return {"object_id": oid, "revision_id": rid, "payload": deepcopy(payload),
                "payload_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}

    def revise(self, head, payload, status=None, effective=False):
        rid = uid()
        revision = self._revision(head["object_id"], rid, payload)
        self.store.revisions[rid] = revision
        head["latest_revision_id"] = rid
        self.transition(head, status, effective)
        return head, revision

    def transition(self, head, status=None, effective=False):
        head["object_version"] += 1
        if status:
            head["lifecycle_status"] = status
        if effective:
            head["effective_revision_id"] = head["latest_revision_id"]
        return head

    def state(self, head):
        return deepcopy(self.store.states.get(head["object_id"], {}))

    def set_state(self, head, state):
        self.store.states[head["object_id"]] = deepcopy(state)

    def exact_ref(self, head, revision):
        return {"object_id": head["object_id"], "revision_id": revision["revision_id"], "payload_hash": revision["payload_hash"]}

    def ref(self, ref, types=None, effective=False, current=True):
        head = self.store.heads[ref["object_id"]]
        revision = self.store.revisions[ref["revision_id"]]
        if types and head["object_type"] not in types:
            raise GovernedError("INVALID_REQUEST")
        if revision["object_id"] != head["object_id"] or revision["payload_hash"] != ref["payload_hash"]:
            raise GovernedError("INVALID_REQUEST")
        if current and head["latest_revision_id"] != ref["revision_id"]:
            raise GovernedError("VERSION_CONFLICT")
        if effective and head["effective_revision_id"] != ref["revision_id"]:
            raise GovernedError("STALE_DEPENDENCY")
        return head, revision

    def current_strategy(self, ref):
        if ref != self.store.strategy:
            raise GovernedError("STALE_DEPENDENCY")
        return self.ref(ref, types={"Strategy"}, effective=True, current=False)

    def require_actor(self, principal_id, principal_type):
        if principal_id != self.ctx.principal_id or principal_type != self.ctx.principal_type:
            raise GovernedError("FORBIDDEN")
        self.validate_principal(principal_id, principal_type)

    def require_role(self, role, principal_type="human", principal_id=None):
        self.validate_principal(principal_id or self.ctx.principal_id, principal_type, role)

    def validate_principal(self, pid, principal_type, role=None, domain_id=None):
        actor = self.store.authority[pid]
        if not actor["active"] or actor["principal_type"] != principal_type or (role and actor["role"] != role):
            raise GovernedError("FORBIDDEN")

    def validate_assignment(self, assignment_id, principal_id, principal_type="human", domain_id=None):
        self.validate_principal(principal_id, principal_type)
        if self.store.authority[principal_id]["assignment_id"] != assignment_id:
            raise GovernedError("FORBIDDEN")

    def check_personal_agent(self, agent_id, owner_id):
        if self.store.personal.get(agent_id) != owner_id:
            raise GovernedError("FORBIDDEN")

    def review(self, kind, target_ref, content, window_id=None):
        record_id = uid()
        self.store.reviews.append({"record_id": record_id, "kind": kind, "target_object_id": target_ref["object_id"],
                                   "target_revision_id": target_ref["revision_id"], "content": deepcopy(content),
                                   "window_id": window_id, "principal_id": self.ctx.principal_id, "action_id": self.action_id})
        return record_id

    def list_reviews(self, window_id):
        return [row for row in self.store.reviews if row["window_id"] == window_id]

    def checkpoint(self, name):
        pass


def exact(result):
    return {key: result[key] for key in ("object_id", "revision_id", "payload_hash")}


def ltco(store, strategy=None):
    payload = {"title": "Six month goals", "period": {"start": "2026-07-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"},
               "strategy_ref": strategy or store.strategy, "owner_principal_id": store.people["ceo"],
               "outcomes": [{"outcome_id": "l1", "unit_id": "u1", "title": "Grow", "result_statement": "Retained growth", "criteria": ["Evidence"]}]}
    draft = exact(store.call("ceo_agent", "m1b_propose_ltco", {"domain_id": store.domain_id, "payload": payload}))
    store.call("ceo", "m1b_confirm_ltco", {"reason": "Confirmed baseline"}, draft)
    return draft


def open_chain(store, ltco_ref=None):
    basis = ltco_ref or ltco(store)
    period = {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}
    payload = {"title": "September PCO", "period": period, "strategy_ref": store.strategy, "ltco_ref": basis,
               "unit_outcomes": [{"outcome_id": oid, "unit_id": "u1", "title": oid, "result_statement": oid,
                                  "criteria": ["Evidence"], "dri_principal_id": store.people["a"]} for oid in ("growth", "retention")]}
    pco = exact(store.call("co_agent", "m1b_draft_pco", {"domain_id": store.domain_id, "payload": payload}))
    mission_payload = {"title": "Combined mission", "pco_ref": pco, "owner_principal_id": store.people["a"],
                       "participants": [store.people["b"]], "supports": [{"outcome_ref": {**pco, "outcome_id": oid}, "contribution": oid}
                                                                          for oid in ("growth", "retention")],
                       "deliverable": "Business results", "acceptance_criteria": ["Result evidence"], "boundary": "Domain A",
                       "hard_deadline": "2026-09-30T00:00:00Z"}
    mission = exact(store.call("co_agent", "m1b_draft_mission", {"domain_id": store.domain_id, "payload": mission_payload}))
    window = exact(store.call("co_agent", "m1b_open_window", {"domain_id": store.domain_id,
        "payload": {"title": "Joint review", "period": period, "strategy_ref": store.strategy, "ltco_ref": basis,
                    "target_refs": [pco, mission], "participants": [store.participant("a"), store.participant("b")]}}))
    return pco, mission, window


def resolution(store, pco, mission, opinions=None):
    mission_payload = store.payload(mission)
    mission_payload.pop("pco_ref")
    mission_payload["object_id"] = mission["object_id"]
    mission_payload["supports"] = [{"outcome_id": item["outcome_ref"]["outcome_id"], "contribution": item["contribution"]}
                                   for item in mission_payload["supports"]]
    return {"title": "Resolved set", "pco_payload": store.payload(pco), "missions": [mission_payload],
            "dispositions": [{"review_record_id": record, "decision": "adopted", "rationale": "Applied to the set"} for record in (opinions or [])],
            "remaining_differences": [], "summary": "All effective opinions considered"}


def test_full_review_loop_preserves_comments_versions_and_separate_ceo_confirmation():
    store = Store()
    pco, mission, window = open_chain(store)
    original_content = store.payload(pco)
    original_version = store.heads[pco["object_id"]]["latest_revision_id"]
    old = store.call("a", "m1b_comment", {"target_ref": pco, "content": "Need more evidence"}, window)["review_record_id"]
    new = store.call("a", "m1b_comment", {"target_ref": pco, "content": "Evidence now clearer", "replaces_record_id": old}, window)["review_record_id"]
    withdraw = store.call("b", "m1b_comment", {"target_ref": mission, "content": "Potential issue"}, window)["review_record_id"]
    store.call("b", "m1b_withdraw_comment", {"review_record_id": withdraw, "reason": "Resolved"}, window)
    assert store.payload(pco) == original_content
    assert store.heads[pco["object_id"]]["latest_revision_id"] == original_version
    closed = store.call("co_agent", "m1b_close_window", {"reason": "Time to collect"}, window)
    assert closed["frozen_opinion_ids"] == [new]
    with pytest.raises(GovernedError):
        store.call("co_agent", "m1b_resolve_window", resolution(store, pco, mission), window)
    result = store.call("co_agent", "m1b_resolve_window", resolution(store, pco, mission, [new]), window)
    candidate = exact(result)
    assert not store.heads[pco["object_id"]]["effective_revision_id"]
    with pytest.raises(GovernedError):
        store.call("co_agent", "m1b_resolve_window", resolution(store, pco, mission, [new]), window)
    confirmed = store.call("ceo", "m1b_confirm_candidates", {"reason": "Confirm full set"}, candidate)
    refs = confirmed["target_refs"]
    new_pco = next(ref for ref in refs if ref["object_id"] == pco["object_id"])
    new_mission = next(ref for ref in refs if ref["object_id"] == mission["object_id"])
    assert store.payload(new_mission)["pco_ref"] == new_pco
    assert len(store.payload(new_mission)["supports"]) == 2
    assert store.heads[new_pco["object_id"]]["effective_revision_id"] == new_pco["revision_id"]
    assert confirmed["execution_authority_created"] is False
    assert len([row for row in store.reviews if row["kind"] == "candidate_set_confirmation"]) == 1
    assert store.payload(pco) == original_content


def test_agents_and_non_authors_cannot_publish_or_retract_human_opinions():
    store = Store()
    pco, mission, window = open_chain(store)
    record = store.call("a", "m1b_comment", {"target_ref": pco, "content": "Own opinion"}, window)["review_record_id"]
    for who in ("a_agent", "co_agent", "outsider"):
        with pytest.raises(GovernedError) as error:
            store.call(who, "m1b_comment", {"target_ref": pco, "content": "Impersonation"}, window)
        assert error.value.code == "FORBIDDEN"
    with pytest.raises(GovernedError):
        store.call("b", "m1b_withdraw_comment", {"review_record_id": record, "reason": "Another person"}, window)
    store.call("a", "m1b_withdraw_comment", {"review_record_id": record, "reason": "Own withdrawal"}, window)
    with pytest.raises(GovernedError):
        store.call("a", "m1b_withdraw_comment", {"review_record_id": record, "reason": "Already inactive"}, window)
    with pytest.raises(GovernedError):
        store.call("a", "m1b_comment", {"target_ref": pco, "content": "Already inactive", "replaces_record_id": record}, window)
    store.call("a_agent", "m1b_assist_review", {"target_ref": mission, "owner_principal_id": store.people["a"],
        "analysis": "Assistance only", "source_refs": [], "generation_version": "test-v1"}, window)
    assert not store.states[window["object_id"]]["active_opinions"][store.people["a"]]


def test_revoked_participant_and_closed_window_stop_new_opinions():
    store = Store()
    pco, _, window = open_chain(store)
    store.authority[store.people["a"]]["active"] = False
    with pytest.raises(GovernedError):
        store.call("a", "m1b_comment", {"target_ref": pco, "content": "Revoked"}, window)
    store.call("co_agent", "m1b_close_window", {"reason": "Close"}, window)
    with pytest.raises(GovernedError):
        store.call("b", "m1b_comment", {"target_ref": pco, "content": "Late"}, window)


def test_ceo_reopen_requires_new_window_before_another_formal_resolution():
    store = Store()
    pco, mission, window = open_chain(store)
    store.call("co_agent", "m1b_close_window", {"reason": "Close"}, window)
    candidate = exact(store.call("co_agent", "m1b_resolve_window", resolution(store, pco, mission), window))
    reopened = exact(store.call("ceo", "m1b_reopen_candidates", {"reason": "Need another review", "title": "Second window"}, candidate))
    assert reopened["object_id"] != window["object_id"]
    assert store.payload(reopened)["target_refs"] == store.payload(candidate)["target_refs"]
    with pytest.raises(GovernedError):
        store.call("ceo", "m1b_confirm_candidates", {"reason": "Old set"}, candidate)
    assert store.states[window["object_id"]]["phase"] == "reopened"


def test_source_change_blocks_pending_set_but_supports_explicit_rebase_recovery():
    store = Store()
    old_ltco = ltco(store)
    pco, mission, window = open_chain(store, old_ltco)
    store.call("co_agent", "m1b_close_window", {"reason": "Close"}, window)
    candidate = exact(store.call("co_agent", "m1b_resolve_window", resolution(store, pco, mission), window))
    old_strategy = store.strategy
    strategy_head, _ = store.exec("ceo").ref(old_strategy)
    updated_payload = store.payload(old_strategy)
    updated_payload["statement"] = "New strategy"
    changed = store.exec("ceo").revise(strategy_head, updated_payload, effective=True)
    store.strategy = store.exec("ceo").exact_ref(*changed)
    assert store.heads[old_ltco["object_id"]]["effective_revision_id"] == old_ltco["revision_id"]
    with pytest.raises(GovernedError) as rejected:
        store.call("ceo", "m1b_confirm_candidates", {"reason": "Obsolete basis"}, candidate)
    assert rejected.value.code == "STALE_DEPENDENCY"
    with pytest.raises(GovernedError):
        store.call("ceo", "m1b_reopen_candidates", {"reason": "Implicit rebase prohibited", "title": "Invalid"}, candidate)
    new_ltco = ltco(store)
    reopened = exact(store.call("ceo", "m1b_reopen_candidates", {"reason": "Explicit changed basis", "title": "Rebase review",
        "rebase_strategy_ref": store.strategy, "rebase_ltco_ref": new_ltco}, candidate))
    assert store.payload(reopened)["strategy_ref"] == store.strategy
    assert store.payload(reopened)["target_refs"] == store.payload(candidate)["target_refs"]


def test_fact_correction_and_review_regeneration_preserve_old_analysis_without_approval():
    store = Store()
    pco, mission, _ = open_chain(store)
    source = store.exec("co_agent").create("EvidenceAsset", {"title": "Original bytes"})
    evidence = store.exec("co_agent").exact_ref(*source)
    payload = {"fact_id": "fact-original", "subject_ref": {**pco, "outcome_id": "growth"}, "as_of": "2026-09-10T00:00:00Z",
               "metric": "retained_growth", "value": 10, "unit": "percent", "source_ref": evidence}
    fact = exact(store.call("co_agent", "m1b_record_fact", {"domain_id": store.domain_id, "payload": payload}))
    corrected = exact(store.call("co_agent", "m1b_correct_fact", {"payload": {**payload, "fact_id": "fact-corrected", "value": 11,
        "corrects_ref": fact, "correction_reason": "Source correction"}}, fact))
    assert store.payload(fact)["value"] == 10
    assert store.states[fact["object_id"]]["corrected_by_ref"] == corrected
    review_payload = {"review_id": "r1", "title": "Review", "period": store.payload(pco)["period"], "target_refs": [pco, mission],
        "fact_refs": [fact], "findings": ["Initial result"], "learnings": ["Retain cautiously"], "implications": [], "generation_version": "v1"}
    review = exact(store.call("co_agent", "m1b_generate_review", {"domain_id": store.domain_id, "payload": review_payload}))
    generated = exact(store.call("co_agent", "m1b_regenerate_review", {"payload": {**review_payload, "fact_refs": [corrected],
        "findings": ["Corrected result"], "generation_version": "v2"}}, review))
    assert store.payload(review)["fact_refs"] == [fact]
    assert store.payload(generated)["fact_refs"] == [corrected]
    assert store.states[review["object_id"]]["phase"] == "generated"
    assert store.heads[review["object_id"]]["lifecycle_status"] == "recorded"
    assert not any(row["kind"] == "review_approval" for row in store.reviews)


def test_ceo_personal_agent_can_assist_when_explicitly_named_in_window():
    store = Store()
    pco, mission, window = open_chain(store)
    # Membership is immutable in production: open a CEO-created successor with
    # the CEO and its explicitly bound CEO Agent as a named participant.
    ceo = store.participant("ceo")
    ceo["personal_agent_id"] = store.people["ceo_agent"]
    reopened = exact(store.call("ceo", "m1b_reopen_window", {
        "title": "CEO participates", "reason": "Explicitly add CEO review responsibility",
        "participants": [store.participant("a"), ceo]}, window))
    store.call("ceo_agent", "m1b_assist_review", {"target_ref": pco,
        "owner_principal_id": store.people["ceo"], "analysis": "CEO personal assistance",
        "source_refs": [], "generation_version": "v1"}, reopened)
    with pytest.raises(GovernedError):
        store.call("ceo_agent", "m1b_comment", {"target_ref": mission, "content": "Not a human decision"}, reopened)
    assert store.states[reopened["object_id"]]["active_opinions"] == {}


@pytest.mark.parametrize("kind", ["m1b_record_fact", "m1b_correct_fact"])
def test_fact_actor_uses_operation_domain_when_person_has_multiple_responsibilities(kind):
    company, domain = uid(), uid()
    selected = []
    assignments = [{"domain_id": company, "role": "CEO"},
                   {"domain_id": domain, "role": "DOMAIN_DRI"}]

    def require_role(role, principal_type):
        assert principal_type == "human"
        selected.append(role)
        if not any(row["domain_id"] == domain and row["role"] == role for row in assignments):
            raise GovernedError("FORBIDDEN")

    execution = SimpleNamespace(kind=kind, domain_id=domain,
        ctx=SimpleNamespace(principal_type="human", assignments=assignments),
        require_role=require_role)
    method_m1b._actor_roles(execution)
    assert selected == ["DOMAIN_DRI"]


@pytest.mark.parametrize("kind", ["m1b_record_fact", "m1b_correct_fact"])
def test_fact_actor_cannot_use_another_domain_ceo_role_to_replace_local_responsibility(kind):
    company, domain = uid(), uid()
    assignments = [{"domain_id": company, "role": "CEO"},
                   {"domain_id": domain, "role": "IC"}]

    def require_role(role, principal_type):
        assert principal_type == "human"
        if not any(row["domain_id"] == domain and row["role"] == role for row in assignments):
            raise GovernedError("FORBIDDEN")

    execution = SimpleNamespace(kind=kind, domain_id=domain,
        ctx=SimpleNamespace(principal_type="human", assignments=assignments),
        require_role=require_role)
    with pytest.raises(GovernedError) as denied:
        method_m1b._actor_roles(execution)
    assert denied.value.code == "FORBIDDEN"
