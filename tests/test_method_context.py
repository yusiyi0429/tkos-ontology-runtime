"""Context selection tests with independently controlled versions and access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import method_readers as readers
from memory_service_runtime.governed.errors import GovernedError


class ContextFixture:
    def __init__(self, monkeypatch):
        self.now = datetime(2026, 9, 11, 12, tzinfo=timezone.utc)
        self.ctx = SimpleNamespace(scope_id=str(uuid4()), principal_id=str(uuid4()), principal_type="agent")
        self.heads, self.versions, self.states, self.activations, self.records = {}, {}, {}, {}, {}
        self.denied = set()
        monkeypatch.setattr(readers.access, "head_access", self.head_access)
        monkeypatch.setattr(readers.access, "revision", self.revision)
        monkeypatch.setattr(readers.protocol, "require_read_support", lambda *args: {"protocol_id": "tkos.method"})
        monkeypatch.setattr(readers.db, "_assignments", lambda *args: [{"role": "PERSONAL_AGENT"}])
        monkeypatch.setattr(readers, "_historical_state", lambda conn, ctx, oid, rid, *args: self.states.get((oid, rid)))
        monkeypatch.setattr(readers, "_activation_at", lambda conn, ctx, oid, rid, *args: self.activations.get((oid, rid)))
        monkeypatch.setattr(readers, "review_records", lambda conn, ctx, oid: {"items": self.records.get(oid, [])})

    def add(self, kind, payload=None, oid=None, effective=False):
        oid, rid = oid or str(uuid4()), str(uuid4())
        value = {"object_id": oid, "revision_id": rid, "payload_hash": "a" * 64,
                 "payload": payload or {"title": kind}, "recorded_at": (self.now - timedelta(hours=2)).isoformat(),
                 "valid_from": (self.now - timedelta(hours=2)).isoformat(), "valid_to": None}
        self.versions[oid, rid] = value
        old = self.heads.get(oid, {})
        self.heads[oid] = {"object_id": oid, "object_type": kind, "latest_revision_id": rid,
                           "effective_revision_id": rid if effective else old.get("effective_revision_id")}
        if effective:
            self.activations[oid, rid] = {"activated_at": (self.now - timedelta(hours=1)).isoformat(), "current_at_requested_times": True}
        return {"object_id": oid, "revision_id": rid, "payload_hash": value["payload_hash"]}

    def set_state(self, ref, **state):
        self.states[ref["object_id"], ref["revision_id"]] = {"event_id": str(uuid4()), "state": state}

    def head_access(self, conn, ctx, oid):
        if oid in self.denied:
            raise GovernedError("NOT_FOUND")
        return self.heads[oid], None

    def revision(self, conn, ctx, oid, rid):
        self.head_access(conn, ctx, oid)
        return self.versions[oid, rid]

    def execute(self, sql, args):
        if sql.startswith("INSERT INTO gov_context_snapshots"):
            self.snapshot_value = {"snapshot_id": args[0], "valid_at": args[3], "known_at": args[4],
                "selected": deepcopy(args[5].obj), "excluded": deepcopy(args[6].obj), "recorded_at": self.now}
            return SimpleNamespace(fetchone=lambda: {"recorded_at": self.now})
        oid = args[1]
        versions = [v for (o, _), v in self.versions.items() if o == oid]
        if "JOIN gov_object_revisions" in sql:
            versions = [v for v in versions if (oid, v["revision_id"]) in self.activations]
        return SimpleNamespace(fetchall=lambda: list(reversed(versions)))

    def pack(self, roots, stage="general", purpose="analysis", drafts=False):
        return readers.context_pack(self, self.ctx, [r["object_id"] for r in roots], self.now, self.now, stage, purpose, drafts)


def test_issue_context_follows_current_state_artifacts_and_filters_stage(monkeypatch):
    f = ContextFixture(monkeypatch)
    issue = f.add("StrategicIssue")
    report = f.add("ResearchReport", {"title": "Current report", "issue_ref": issue})
    proposal = f.add("StrategyUpdateProposal", {"title": "Unrelated stage proposal", "issue_ref": issue})
    f.set_state(issue, phase="report_submitted", report_ref=report, proposal_ref=proposal)
    result = f.pack([issue], stage="research")
    assert {r["object_id"] for r in result["selected"]} == {issue["object_id"], report["object_id"]}
    assert result["selected"][0]["method_phase"] == "report_submitted"
    assert any(r["reason"] == "not_selected_for_stage_or_purpose" and r["relationship"] == "proposal_ref" for r in result["excluded"])


def test_context_uses_state_from_selected_version_and_does_not_mix_report_reviews(monkeypatch):
    f = ContextFixture(monkeypatch)
    old = f.add("ResearchReport")
    current = f.add("ResearchReport", oid=old["object_id"])
    issue = f.add("StrategicIssue")
    f.set_state(issue, phase="historical_report", report_ref=old)
    now_string = (f.now - timedelta(minutes=1)).isoformat()
    f.records[old["object_id"]] = [
        {"record_id": "old-check", "target_revision_id": old["revision_id"], "recorded_at": now_string},
        {"record_id": "new-check", "target_revision_id": current["revision_id"], "recorded_at": now_string},
    ]
    result = f.pack([issue], stage="research")
    selected = next(i for i in result["selected"] if i["object_id"] == old["object_id"])
    assert selected["revision_id"] == old["revision_id"]
    assert [r["record_id"] for r in selected["collaboration_records"]] == ["old-check"]


def test_historical_adoption_uses_activation_evidence_not_todays_pointer(monkeypatch):
    f = ContextFixture(monkeypatch)
    old = f.add("Strategy", effective=True)
    current = f.add("Strategy", oid=old["object_id"], effective=True)
    issue = f.add("StrategicIssue", {"title": "Old basis", "strategy_ref": old})
    result = f.pack([issue])
    selected = next(i for i in result["selected"] if i["object_id"] == old["object_id"])
    assert selected["revision_id"] != current["revision_id"]
    assert selected["adoption"] == "effective_baseline"
    f.activations[old["object_id"], old["revision_id"]]["current_at_requested_times"] = False
    result = f.pack([issue])
    selected = next(i for i in result["selected"] if i["object_id"] == old["object_id"])
    assert selected["adoption"] == "historical_effective_baseline"


def test_formal_draft_is_not_smuggled_in_through_state_reference(monkeypatch):
    f = ContextFixture(monkeypatch)
    pco = f.add("PCO")
    window = f.add("ReviewWindow", {"title": "Review", "target_refs": [pco]})
    result = f.pack([window], stage="review")
    assert pco["object_id"] not in {r["object_id"] for r in result["selected"]}
    assert any(r["reason"] == "draft_not_requested" for r in result["excluded"])
    result = f.pack([window], stage="review", drafts=True)
    assert pco["object_id"] in {r["object_id"] for r in result["selected"]}


def test_private_evidence_excluded_and_saved_context_rechecks_current_permission(monkeypatch):
    f = ContextFixture(monkeypatch)
    evidence = f.add("EvidenceAsset")
    report = f.add("ResearchReport", {"title": "Analysis", "evidence_refs": [evidence]})
    f.pack([report], stage="research")
    saved = deepcopy(f.snapshot_value)
    f.denied.add(evidence["object_id"])
    with pytest.raises(GovernedError) as error:
        readers.snapshot(f, f.ctx, saved)
    assert error.value.code == "NOT_FOUND"
    result = f.pack([report], stage="research")
    assert evidence["object_id"] not in {r["object_id"] for r in result["selected"]}
    assert any(r["reason"] == "not_found_or_not_authorized" for r in result["excluded"])


def test_unknown_stage_and_free_text_purpose_have_explicit_fallback(monkeypatch):
    f = ContextFixture(monkeypatch)
    result = f.pack([f.add("ResearchMemo")], stage="custom-stage", purpose="Prepare for our discussion")
    selection = result["context_request"]
    assert selection["selection_stage"] == selection["selection_purpose"] == "general"
    assert selection["stage"] == "custom-stage" and selection["purpose"] == "Prepare for our discussion"
    assert len(selection["selection_notes"]) == 2
