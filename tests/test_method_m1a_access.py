"""Narrow provenance grants for derived domain judgments, not domain access."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from memory_service_runtime.governed import method_access as access
from memory_service_runtime.governed import method_m1a
from memory_service_runtime.governed.errors import GovernedError


def uid():
    return str(uuid4())


def reference(row):
    return {key: row[key] for key in ("object_id", "revision_id", "payload_hash")}


class ProvenanceFixture:
    def __init__(self, monkeypatch):
        self.actor, self.outsider, self.issue_id, self.judgment_id = uid(), uid(), uid(), uid()
        self.ctx = SimpleNamespace(scope_id=uid(), principal_id=self.actor)
        self.issue = {"object_id": self.issue_id, "state": {"participants": [self.actor]}}
        issue_ref = {"object_id": self.issue_id, "revision_id": uid(), "payload_hash": "a" * 64}
        self.agreement = {"object_id": uid(), "revision_id": uid(), "payload_hash": "b" * 64,
                          "object_type": "StrategicAgreement", "payload": {"issue_ref": issue_ref}}
        self.proposal = {"object_id": uid(), "revision_id": uid(), "payload_hash": "c" * 64,
                         "object_type": "StrategyUpdateProposal", "payload": {"issue_ref": issue_ref, "agreement_ref": reference(self.agreement)}}
        self.private_source = {"object_id": uid(), "revision_id": uid(), "payload_hash": "d" * 64}
        self.strategy = {"object_id": uid(), "revision_id": uid(), "payload_hash": "e" * 64}
        self.judgment = {"object_id": self.judgment_id, "revision_id": uid(), "payload_hash": "f" * 64,
                         "payload": {"source_agreement_ref": reference(self.agreement), "source_proposal_ref": reference(self.proposal),
                                     "strategy_ref": self.strategy, "source_refs": [self.private_source]}}
        self.judgment_versions = [self.judgment]
        self.artifacts = [self.agreement, self.proposal]
        monkeypatch.setattr(access, "is_method_object", lambda *args: True)
        def participant(conn, ctx, state):
            if ctx.principal_id not in state["participants"]:
                raise GovernedError("FORBIDDEN")
            return {"assignment_id": "current"}
        monkeypatch.setattr(access, "research_participant", participant)

    def execute(self, sql, args):
        if "SELECT o.object_id,s.state" in sql:
            rows = [self.issue]
        elif "JOIN gov_objects o" in sql:
            rows = self.artifacts
        else:
            rows = self.judgment_versions
        return SimpleNamespace(fetchall=lambda: rows)

    def grants(self, oid=None, kind="StrategicJudgment", actor=None):
        ctx = SimpleNamespace(scope_id=self.ctx.scope_id, principal_id=actor or self.actor)
        return access.research_grants(self, ctx, {"object_id": oid or self.judgment_id, "object_type": kind})


def test_derived_judgment_grants_exact_version_to_source_issue_participant(monkeypatch):
    f = ProvenanceFixture(monkeypatch)
    assert f.grants() == {f.judgment["revision_id"]}
    assert f.grants(actor=f.outsider) == set()
    f.issue["state"]["participants"] = []
    assert f.grants() == set()


def test_derived_judgment_does_not_grant_other_versions_or_source_material(monkeypatch):
    f = ProvenanceFixture(monkeypatch)
    unrelated_version = deepcopy(f.judgment)
    unrelated_version["revision_id"] = uid()
    unrelated_version["payload"]["source_agreement_ref"]["object_id"] = uid()
    f.judgment_versions.append(unrelated_version)
    assert f.grants() == {f.judgment["revision_id"]}
    assert f.grants(oid=f.private_source["object_id"], kind="EvidenceAsset") == set()
    assert f.grants(oid=f.strategy["object_id"], kind="Strategy") == set()
    assert f.grants(oid=uid(), kind="ResearchReport") == set()


def test_judgment_provenance_requires_hash_type_and_consistent_agreement(monkeypatch):
    f = ProvenanceFixture(monkeypatch)
    f.judgment["payload"]["source_proposal_ref"]["payload_hash"] = "0" * 64
    assert f.grants() == set()
    f.judgment["payload"]["source_proposal_ref"] = reference(f.proposal)
    f.proposal["object_type"] = "ResearchMemo"
    assert f.grants() == set()
    f.proposal["object_type"] = "StrategyUpdateProposal"
    f.proposal["payload"]["agreement_ref"] = {**reference(f.agreement), "revision_id": uid()}
    assert f.grants() == set()


def test_source_actor_selects_role_from_action_domain_not_other_domain_ceo():
    domain, elsewhere, calls = uid(), uid(), []
    execution = SimpleNamespace(domain_id=domain, method_scoped_domain=None,
        ctx=SimpleNamespace(principal_type="human", assignments=[
            {"domain_id": elsewhere, "role": "CEO"}, {"domain_id": domain, "role": "DOMAIN_DRI"}]),
        require_role=lambda role, principal_type: calls.append((role, principal_type)))
    method_m1a._source_actor(execution)
    assert calls == [("DOMAIN_DRI", "human")]
