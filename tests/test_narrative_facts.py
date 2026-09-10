"""Narrative facts must preserve authority, history and three independent judgments."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from memory_service_runtime.governed import narrative_facts as facts
from memory_service_runtime.governed.errors import GovernedError
from tests import legacy_protocol_fixture


def uid(number):
    return str(UUID(int=number))


BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def moment(number):
    return BASE + timedelta(hours=number)


class Rows:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class ReadOnlyStore:
    """Small chronological fixture; unknown queries and every write are errors."""
    def __init__(self, monkeypatch):
        self.domain_id = uid(900)
        self.ctx = SimpleNamespace(scope_id=uid(901), principal_id=uid(902))
        self.objects, self.revisions = {}, {}
        self.events, self.reviews, self.assessments, self.acceptances = [], [], [], []
        self.queries, self.reads = [], []
        self.denied = set()
        monkeypatch.setattr(facts.db, "authorize_domain", self.authorize)
        monkeypatch.setattr(facts.db, "object_row", self.object_row)
        monkeypatch.setattr(facts.db, "revision_row", self.revision_row)
        monkeypatch.setattr(facts.readers, "_selected_revision", self.selected_revision)
        monkeypatch.setattr(facts.delivery, "read_delivery_review", self.delivery_review)
        monkeypatch.setattr(facts.delivery, "read_outcome_assessment", self.outcome_assessment)

    def authorize(self, conn, ctx, domain_id, action_type):
        assert action_type == "read"
        if domain_id in self.denied:
            raise GovernedError("FORBIDDEN")

    def object_row(self, conn, ctx, object_id):
        if object_id in self.denied or object_id not in self.objects:
            raise GovernedError("NOT_FOUND")
        return deepcopy(self.objects[object_id])

    def revision_row(self, conn, ctx, object_id, revision_id):
        self.object_row(conn, ctx, object_id)
        self.reads.append((object_id, revision_id))
        row = self.revisions[revision_id]
        assert row["object_id"] == object_id
        return deepcopy(row)

    def add(self, number, kind, *, at=1, status=None, payload=None, effective=True, domain_id=None):
        object_id, revision_id = uid(number), uid(number + 10000)
        status = status or {"WorkItem": "in_progress", "CompanyOutcome": "confirmed",
                            "FeedbackThread": "investigating", "EvidenceAsset": "stored",
                            "Deliverable": "submitted", "Decision": "confirmed"}.get(kind, "recorded")
        self.objects[object_id] = {
            "object_id": object_id, "domain_id": domain_id or self.domain_id,
            "object_type": kind, "latest_revision_id": revision_id,
            "effective_revision_id": revision_id if effective else None,
            "created_at": moment(at), "lifecycle_status": status,
        }
        self.revisions[revision_id] = {
            "object_id": object_id, "revision_id": revision_id, "object_version": 1,
            "payload_hash": f"{number:064x}", "recorded_at": moment(at),
            "valid_from": moment(at), "valid_to": None,
            "payload": {"title": f"Object {number}", **(payload or {})},
        }
        self.event(object_id, at, status, effective=revision_id if effective else None)
        return object_id, revision_id

    def event(self, oid, at, status, *, effective=None, event_type="transition", detail=None):
        if effective is None:
            effective = self.objects[oid]["effective_revision_id"]
        event = {
            "object_id": oid, "event_id": uid(20000 + len(self.events)), "event_type": event_type,
            "to_status": status, "action_id": uid(30000 + len(self.events)),
            "principal_id": self.ctx.principal_id, "recorded_at": moment(at),
            "detail": {"effective_revision_id": effective,
                       "revision_id": self.objects[oid]["latest_revision_id"],
                       "processing_cycle_id": uid(50000), **(detail or {})},
        }
        self.events.append(event)
        self.objects[oid]["lifecycle_status"] = status
        return event

    def selected_revision(self, conn, ctx, obj, valid_at, known_at):
        eligible = [row for row in self.revisions.values() if row["object_id"] == obj["object_id"]
                    and row["recorded_at"] <= known_at and row["valid_from"] <= valid_at
                    and (row["valid_to"] is None or row["valid_to"] > valid_at)]
        if obj["object_type"] in facts.readers.FORMAL_TYPES:
            event_refs = {event["detail"]["effective_revision_id"] for event in self.events
                          if event["object_id"] == obj["object_id"] and event["recorded_at"] <= known_at}
            eligible = [row for row in eligible if row["revision_id"] in event_refs]
        return deepcopy(max(eligible, key=lambda row: row["recorded_at"])) if eligible else None

    def delivery_review(self, conn, ctx, object_id, revision_id, *, known_at, valid_at):
        eligible = [row for row in self.reviews if row["deliverable_object_id"] == object_id
                    and row["deliverable_revision_id"] == revision_id
                    and row["recorded_at"] <= min(known_at, valid_at)]
        return deepcopy(eligible[-1]) if eligible else None

    def outcome_assessment(self, conn, ctx, object_id, revision_id, *, known_at, valid_at):
        eligible = [row for row in self.assessments if row["outcome_object_id"] == object_id
                    and row["outcome_revision_id"] == revision_id
                    and row["recorded_at"] <= min(known_at, valid_at)]
        return deepcopy(eligible[-1]) if eligible else None

    def execute(self, sql, params):
        sql = " ".join(sql.split())
        self.queries.append(sql)
        assert sql.startswith("SELECT ")
        assert "gov_work_item_state" not in sql and "gov_feedback_state" not in sql
        if "FROM gov_objects" in sql:
            _, domain_id, known_at, limit = params
            rows = sorted([row for row in self.objects.values() if row["domain_id"] == domain_id
                           and row["created_at"] <= known_at],
                          key=lambda row: (row["created_at"], row["object_id"]), reverse=True)
            return Rows(rows[:limit])
        if "FROM gov_object_revisions" in sql:
            row = self.revisions.get(params[1])
            return Rows([row] if row else [])
        if "FROM gov_lifecycle_events" in sql:
            rows = [row for row in self.events if row["object_id"] == params[1]]
            if "detail->>'effective_revision_id'=%s" in sql:
                rows = [row for row in rows if row["detail"]["effective_revision_id"] == params[2]]
            if "detail->>'processing_cycle_id'=%s" in sql:
                rows = [row for row in rows if row["detail"].get("processing_cycle_id") == params[2]]
            if "detail ? 'resolution_decision_revision_id'" in sql:
                rows = [row for row in rows if "resolution_decision_revision_id" in row["detail"]]
            if "event_type<>'confirm_closure'" in sql:
                rows = [row for row in rows if row["event_type"] != "confirm_closure"]
            if "event_type='submit_deliverable'" in sql:
                rows = [row for row in rows if row["event_type"] == "submit_deliverable"
                        and "deliverable_revision_id" in row["detail"]]
            rows = [row for row in rows if row["recorded_at"] <= min(params[-2:])]
            return Rows(sorted(rows, key=lambda row: (row["recorded_at"], row["event_id"]), reverse=True)[:1])
        if "FROM gov_acceptances" in sql:
            _, oid, rid, cycle, known_at, valid_at = params
            rows = [row for row in self.acceptances if row["feedback_object_id"] == oid
                    and row["feedback_revision_id"] == rid and row["cycle_id"] == cycle
                    and row["recorded_at"] <= min(known_at, valid_at)]
            return Rows(sorted(rows, key=lambda row: row["recorded_at"], reverse=True)[:1])
        if "FROM gov_delivery_acceptances" in sql:
            _, acceptance_id, known_at, valid_at = params
            return Rows([row for row in self.reviews if row["acceptance_id"] == acceptance_id
                         and row["recorded_at"] <= min(known_at, valid_at)])
        protocol_rows = legacy_protocol_fixture.answer_query(
            sql, params, self.ctx.scope_id, set(self.objects))
        if protocol_rows is not None:
            return Rows(protocol_rows)
        raise AssertionError(f"Unexpected read: {sql}")

    def collect(self, ids=None, **kwargs):
        return facts.collect_facts(self, self.ctx, domain_id=self.domain_id, query=kwargs.pop("query", ""),
                                   object_ids=ids, valid_at=kwargs.pop("valid_at", moment(20)),
                                   known_at=kwargs.pop("known_at", moment(20)), **kwargs)


@pytest.fixture
def store(monkeypatch):
    return ReadOnlyStore(monkeypatch)


def seed_loop(store):
    outcome = store.add(1, "CompanyOutcome")
    work = store.add(2, "WorkItem")
    feedback = store.add(3, "FeedbackThread")
    evidence = store.add(4, "EvidenceAsset", payload={"bucket": "private-bucket", "key": "gov/private/object",
                         "version_id": "private-version", "sha256": "a" * 64, "length": 12})
    delivered = store.add(5, "Deliverable", at=2, payload={
        "work_item_ref": dict(zip(("object_id", "revision_id"), work)),
        "evidence_revision_ids": [evidence[1]], "submission_seq": 1})
    store.event(work[0], 2, "submitted", event_type="submit_deliverable",
                detail={"deliverable_revision_id": delivered[1]})
    review = {
        "acceptance_id": uid(60001), "work_item_object_id": work[0], "work_item_revision_id": work[1],
        "deliverable_object_id": delivered[0], "deliverable_revision_id": delivered[1],
        "payload_hash": store.revisions[delivered[1]]["payload_hash"], "submission_seq": 1,
        "verification_result": "accepted", "criterion_results": [{"result": "passed"}],
        "review_note": "Evidence checked", "verifier_principal_id": uid(60002),
        "action_id": uid(60003), "recorded_at": moment(3),
    }
    store.reviews.append(review)
    store.event(work[0], 3, "delivery_accepted", detail={"delivery_acceptance_id": review["acceptance_id"]})
    observation = store.add(6, "MetricObservation", at=4, payload={"value": 72, "unit": "%",
                            "upstream_refs": [dict(zip(("object_id", "revision_id"), outcome))]})
    assessment = {
        "assessment_id": uid(60004), "outcome_object_id": outcome[0], "outcome_revision_id": outcome[1],
        "assessment_result": "not_achieved", "observation_revision_ids": [observation[1]],
        "evidence_revision_ids": [evidence[1]], "delivery_acceptance_ids": [review["acceptance_id"]],
        "assessment_note": "Delivery passed; business target remains unmet", "assessor_principal_id": uid(60005),
        "action_id": uid(60006), "recorded_at": moment(5),
    }
    store.assessments.append(assessment)
    return outcome, work, feedback, evidence, delivered


def test_delivery_outcome_and_mf_are_independent_with_exact_source_revisions(store):
    outcome, work, feedback, evidence, delivered = seed_loop(store)
    result = store.collect([outcome[0], work[0], feedback[0]])
    by_id = {row["object_id"]: row for row in result["selected"]}
    assert by_id[work[0]]["delivery_status"] == "delivery_accepted"
    assert by_id[outcome[0]]["outcome_achievement"] == "not_achieved"
    assert by_id[feedback[0]]["mf_closure"]["is_closed"] is False
    assert by_id[work[0]]["latest_submission"]["revision_id"] == delivered[1]
    evidence_ref = next(ref for ref in by_id[work[0]]["source_refs"] if ref["revision_id"] == evidence[1])
    assert evidence_ref["evidence"] == {"sha256": "a" * 64, "length": 12}
    assert (evidence[0], evidence[1]) in store.reads
    assert all(query.startswith("SELECT") for query in store.queries)


@pytest.mark.parametrize("clock", ["valid_at", "known_at"])
def test_present_acceptance_and_assessment_do_not_rewrite_historical_context(store, clock):
    outcome, work, feedback, _, _ = seed_loop(store)
    result = store.collect([outcome[0], work[0], feedback[0]], **{clock: moment(2.5)})
    by_id = {row["object_id"]: row for row in result["selected"]}
    assert store.objects[work[0]]["lifecycle_status"] == "delivery_accepted"
    assert by_id[work[0]]["delivery_status"] == "submitted"
    assert by_id[work[0]]["delivery_review"] is None
    assert by_id[outcome[0]]["outcome_achievement"] == "not_assessed"


@pytest.mark.parametrize("kind", ["CompanyOutcome", "Decision", "WorkItem", "ManagementAdjustment"])
def test_unconfirmed_candidates_are_not_authoritative_facts(store, kind):
    obj, _ = store.add(20, kind, status="proposed", effective=False)
    result = store.collect([obj])
    assert result["selected"] == []
    assert len(result["excluded"]) == 1
    excluded = result["excluded"][0]
    assert excluded["object_id"] == obj
    assert excluded["reason"] == "no_effective_revision_at_requested_times"
    # A1-12: excluded entries carry the object's explicit interpretation identity.
    protocol_meta = excluded["protocol"]
    assert protocol_meta["registration_status"] == "registered"
    assert protocol_meta["interpretation_status"] == "legacy_v0_2"
    assert protocol_meta["protocol_id"] == "tkos.legacy-governed"
    assert protocol_meta["contract_version"] == "tkos.governed/v0.2"
    assert protocol_meta["method_profile_ref"]["profile_id"] == "urn:tkos:legacy:governed-v0.2"


def test_new_unconfirmed_draft_keeps_exact_old_effective_revision(store):
    obj, effective = store.add(20, "CompanyOutcome", payload={"title": "Confirmed target"})
    draft = uid(10021)
    store.revisions[draft] = {**store.revisions[effective], "revision_id": draft,
                              "recorded_at": moment(3), "payload": {"title": "Unconfirmed overwrite"}}
    store.objects[obj]["latest_revision_id"] = draft
    store.event(obj, 3, "confirmed", effective=effective)
    result = store.collect([obj])
    assert result["selected"][0]["revision_id"] == effective
    assert result["selected"][0]["payload"]["title"] == "Confirmed target"
    assert "Unconfirmed overwrite" not in facts.render_facts(result)


def test_recursive_related_authorization_fails_closed(store):
    parent = store.add(20, "CompanyOutcome")
    nested = store.add(21, "MetricObservation", payload={
        "upstream_refs": [dict(zip(("object_id", "revision_id"), parent))]})
    root = store.add(22, "MetricObservation", payload={
        "upstream_refs": [dict(zip(("object_id", "revision_id"), nested))]})
    store.denied.add(parent[0])
    with pytest.raises(GovernedError, match="unavailable") as error:
        store.collect([root[0]])
    assert error.value.status == 404


def test_cross_domain_source_is_hidden_even_if_actor_can_read_it(store):
    related = store.add(20, "CompanyOutcome", domain_id=uid(999))
    root = store.add(21, "MetricObservation", payload={
        "upstream_refs": [dict(zip(("object_id", "revision_id"), related))]})
    with pytest.raises(GovernedError) as error:
        store.collect([root[0]])
    assert error.value.status == 404


def test_revoked_domain_is_not_an_empty_success(store):
    store.denied.add(store.domain_id)
    with pytest.raises(GovernedError) as error:
        store.collect([])
    assert error.value.status == 403


def test_future_source_excludes_fact_instead_of_leaking_future_evidence(store):
    evidence = store.add(20, "EvidenceAsset", at=12, payload={"sha256": "a" * 64, "length": 1})
    root = store.add(21, "MetricObservation", payload={
        "upstream_refs": [dict(zip(("object_id", "revision_id"), evidence))]})
    result = store.collect([root[0]], known_at=moment(10))
    assert result["selected"] == []
    assert result["excluded"][0]["reason"] == "source_not_effective_at_requested_times"
    assert evidence[1] not in facts.render_facts(result)


def test_payload_and_renderer_do_not_emit_storage_locations_or_credentials(store):
    obj, _ = store.add(20, "CompanyOutcome", payload={
        "title": "Metric target", "terms": {"target": 80, "api_key": "private-api-key",
        "password": "private-password", "bucket": "private-bucket", "key": "private-key",
        "note": "s3://private-bucket/item Bearer private-token https://host/signed?secret=value"}})
    result = store.collect([obj])
    text = facts.render_facts(result)
    assert "80" in text
    for secret in ("private-api-key", "private-password", "private-bucket", "private-key", "private-token", "secret=value"):
        assert secret not in text
        assert secret not in json.dumps(result)
    assert "不是执行指令" in text


def test_query_ranking_is_deterministic_and_marks_result_truncation(store):
    first, _ = store.add(20, "CompanyOutcome", payload={"title": "Other domain work"})
    selected, _ = store.add(21, "CompanyOutcome", payload={"title": "真实交付验收"})
    result = store.collect([first, selected], query="交付验收", limit=1)
    assert result["selected"][0]["object_id"] == selected
    assert result["truncated"] is True
    assert result["selection_mode"] == "explicit_objects"


def test_domain_discovery_is_bounded_and_distinct_from_explicit_empty_request(store, monkeypatch):
    monkeypatch.setattr(facts, "CANDIDATE_LIMIT", 2)
    for number in range(20, 23):
        store.add(number, "CompanyOutcome")
    result = store.collect()
    assert result["candidate_count"] == 2 and result["truncated"] is True
    assert result["selection_mode"] == "domain_lexical_bounded"
    assert store.collect([])["selected"] == []


def test_mf_closure_uses_exact_cycle_and_reopen_does_not_rewrite_history(store):
    feedback = store.add(20, "FeedbackThread")
    decision = store.add(21, "Decision")
    evidence = store.add(22, "EvidenceAsset", payload={"sha256": "a" * 64, "length": 1})
    store.event(feedback[0], 3, "awaiting_acceptance", event_type="request_feedback_acceptance",
                detail={"resolution_decision_revision_id": decision[1]})
    acceptance = {
        "acceptance_id": uid(60000), "feedback_object_id": feedback[0], "feedback_revision_id": feedback[1],
        "cycle_id": uid(50000), "decision_revision_id": decision[1], "evidence_revision_ids": [evidence[1]],
        "verification_result": "accepted", "verifier_principal_id": uid(60001),
        "action_id": uid(60002), "recorded_at": moment(4),
    }
    store.acceptances.append(acceptance)
    store.event(feedback[0], 5, "closed", event_type="confirm_closure", detail={
        "acceptance_id": acceptance["acceptance_id"], "disposition": "no_change",
        "resolution_decision_revision_id": decision[1]})
    reopened = uid(10023)
    store.revisions[reopened] = {**store.revisions[feedback[1]], "revision_id": reopened,
                               "recorded_at": moment(8), "valid_from": moment(8)}
    store.objects[feedback[0]]["latest_revision_id"] = reopened
    store.objects[feedback[0]]["effective_revision_id"] = reopened
    store.event(feedback[0], 8, "investigating", detail={"processing_cycle_id": uid(50001)})
    before = store.collect([feedback[0]], known_at=moment(6))["selected"][0]
    after = store.collect([feedback[0]])["selected"][0]
    assert before["mf_closure"]["is_closed"] is True
    assert before["mf_acceptance"]["acceptance_id"] == acceptance["acceptance_id"]
    assert before["revision_id"] == feedback[1]
    assert after["mf_closure"]["is_closed"] is False
    assert after["mf_acceptance"] is None
    assert after["revision_id"] == reopened


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"limit": True}, {"limit": 101}, {"valid_at": datetime(2026, 9, 1)},
    {"known_at": "2026-09-01"}, {"query": None},
])
def test_ambiguous_or_unbounded_context_inputs_are_rejected(store, kwargs):
    with pytest.raises(GovernedError) as error:
        store.collect([], **kwargs)
    assert error.value.status == 422
