"""Real legacy Outcome assessment and delivery-review snapshot source inventory."""
from __future__ import annotations

from acceptance.runtime.client import utc_now
from .support import digest, public_json


def run_assessment_snapshot(h, f, *, legacy_flow, legacy_result):
    flow = legacy_flow
    outcome = flow.create("CompanyOutcome", {"title": "Independent assessment snapshot target",
        "terms": {"target": 3}, "upstream_refs": []})
    flow.act("ceo", "confirm_outcome", {}, oid=outcome)
    observation = flow.create("MetricObservation", {"title": "Actual synthetic observation: two of three",
        "metric_id": "qualified-clients", "value": 2, "unit": "clients", "valid_from": utc_now(),
        "upstream_refs": [flow.ref(outcome)]})
    evidence = flow.evidence[-1]["revision_id"]
    assessed = flow.act("ceo", "record_outcome_assessment", {"assessment_result": "not_achieved",
        "observation_revision_ids": [flow.ref(observation)["revision_id"]], "evidence_revision_ids": [evidence],
        "assessment_note": "Two observed clients do not meet the target of three; delivery and MF remain separate",
        "delivery_acceptance_ids": []}, oid=outcome)
    assert flow.object(outcome)["outcome_achievement"] == "not_achieved"
    assert flow.object(legacy_result["outcome"])["outcome_achievement"] == "not_assessed"
    assert flow.object(legacy_result["work_item"])["lifecycle_status"] == "delivery_accepted"
    assert flow.object(legacy_result["feedback"])["lifecycle_status"] == "investigating"
    now = utc_now()
    pack = flow.ceo.json("POST", "/v1/context-packs", {"object_ids": [outcome, legacy_result["deliverable"]],
        "valid_at": now, "known_at": now})
    selected = {item["object_id"]: item for item in pack["selected"]}
    assessment = selected[outcome]["outcome_assessment"]
    review = selected[legacy_result["deliverable"]]["delivery_review"]
    assert assessment["assessment_id"] == assessed["result"]["assessment_id"]
    assert assessment["assessment_result"] == "not_achieved"
    assert review["verification_result"] == "accepted"
    assert str(review["work_item_object_id"]) == legacy_result["work_item"]
    sources = []
    for rid in [*assessment["observation_revision_ids"], *assessment["evidence_revision_ids"]]:
        owner = h.sql(f, "SELECT object_id::text FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                      (f["scope_id"], rid))
        assert len(owner) == 1
        sources.append({"object_id": owner[0]["object_id"], "revision_id": rid})
    sources.append({"object_id": review["work_item_object_id"], "revision_id": review["work_item_revision_id"]})
    assert len(sources) == 3
    before, storage = h.snapshot(f), h.storage_snapshot(f["scope_id"])
    returned = flow.ceo.json("GET", f"/v1/context-packs/{pack['context_snapshot_id']}")
    assert returned == pack, "stored snapshot with assessment/review changed during GET"
    for source in sources:
        revision = flow.ceo.revision(source["object_id"], source["revision_id"])
        assert revision["revision_id"] == source["revision_id"]
    assert h.snapshot(f) == before and h.storage_snapshot(f["scope_id"]) == storage
    result = {"passed": True, "context_snapshot_id": pack["context_snapshot_id"],
        "real_assessment_id": assessment["assessment_id"], "real_delivery_review_id": review["acceptance_id"],
        "source_refs": sources, "snapshot_sha256": digest(before), "new_outcome": "not_achieved",
        "original_outcome": "not_assessed", "delivery": "delivery_accepted", "mf": "investigating",
        "selective_cross_domain_source_revocation": "unreachable_under_same_domain_business_reference_constraint",
        "source_traversal_static_review_required": True, "contract_a1_accepted": False}
    public_json(h.output / "assessment-review-snapshot.json", result)
    return result
