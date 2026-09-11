"""Static contract negatives that protect M1B's finalized semantic boundaries."""
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed.method_m1b_models import (
    BusinessFactPayload, CandidateMission, FactSubject, M1B_ACTION_PARAMS,
    MissionPayload, PeriodReviewPayload, ReopenCandidates, ReviewWindowPayload,
)


def ref():
    return {"object_id": str(uuid4()), "revision_id": str(uuid4()), "payload_hash": "a" * 64}


def mission():
    pco = ref()
    return {"title": "Two outcomes, one Owner", "pco_ref": pco,
            "owner_principal_id": str(uuid4()), "participants": [],
            "supports": [{"outcome_ref": {**pco, "outcome_id": key}, "contribution": key} for key in ["growth", "retention"]],
            "deliverable": "Retained customer growth", "acceptance_criteria": ["Measured retained growth"],
            "boundary": "Named domain", "hard_deadline": "2026-09-30T00:00:00Z"}


def test_single_mission_supports_two_outcomes_and_exposes_no_execution_authority():
    payload = MissionPayload.model_validate(mission()).model_dump()
    assert len(payload["supports"]) == 2
    assert "execution_authority_id" not in payload


@pytest.mark.parametrize("mutation", ["revision", "object", "hash", "duplicate", "owner_participant"])
def test_mission_rejects_ambiguous_support_or_responsibility(mutation):
    payload = mission()
    if mutation in {"revision", "object", "hash"}:
        field = {"revision": "revision_id", "object": "object_id", "hash": "payload_hash"}[mutation]
        payload["supports"][0]["outcome_ref"][field] = "b" * 64 if mutation == "hash" else str(uuid4())
    elif mutation == "duplicate":
        payload["supports"].append(deepcopy(payload["supports"][0]))
    else:
        payload["participants"] = [payload["owner_principal_id"]]
    with pytest.raises(ValidationError):
        MissionPayload.model_validate(payload)


def test_review_is_analysis_without_approval_fields_or_actions():
    payload = {"review_id": "review-202609", "title": "September review",
               "period": {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
               "target_refs": [ref()], "fact_refs": [], "findings": ["No facts available"],
               "learnings": [], "implications": [], "generation_version": "controlled-agent-1"}
    assert PeriodReviewPayload.model_validate(payload).findings
    for field in ("approved", "confirmed", "status", "approval_record"):
        with pytest.raises(ValidationError):
            PeriodReviewPayload.model_validate({**payload, field: True})
    assert not any("approve_review" in action or "confirm_review" in action for action in M1B_ACTION_PARAMS)


def test_business_fact_requires_original_source_and_immutable_correction_reference():
    base = {"fact_id": "fact-1", "subject_ref": {"topic": "Retention"}, "as_of": "2026-09-10T00:00:00Z",
            "metric": "renewal_rate", "value": 0.91, "unit": "ratio", "source_ref": ref()}
    assert BusinessFactPayload.model_validate(base).value == 0.91
    with pytest.raises(ValidationError):
        BusinessFactPayload.model_validate({**base, "corrects_ref": ref()})
    with pytest.raises(ValidationError):
        BusinessFactPayload.model_validate({**base, "correction_reason": "Correct the source observation"})
    correction = BusinessFactPayload.model_validate({**base, "corrects_ref": ref(), "correction_reason": "Correct source observation"})
    assert correction.corrects_ref


@pytest.mark.parametrize("payload", [{"object_id": str(uuid4())}, {**ref(), "topic": "mixed"},
                                     {"topic": "only-topic", "outcome_id": "mixed"}, {}])
def test_fact_subject_is_one_exact_form(payload):
    with pytest.raises(ValidationError):
        FactSubject.model_validate(payload)


def test_window_membership_has_exact_assignment_and_no_generic_scope_grant():
    owner = str(uuid4())
    participant = {"principal_id": owner, "assignment_id": str(uuid4())}
    payload = {"title": "Joint review", "period": {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
               "strategy_ref": ref(), "ltco_ref": ref(), "target_refs": [ref(), ref()], "participants": [participant]}
    assert ReviewWindowPayload.model_validate(payload).participants[0].principal_id == owner
    with pytest.raises(ValidationError):
        ReviewWindowPayload.model_validate({**payload, "grant_all_domain_sources": True})
    with pytest.raises(ValidationError):
        ReviewWindowPayload.model_validate({**payload, "participants": [participant, participant]})


def test_reopen_requires_explicit_paired_source_rebase():
    payload = {"reason": "Strategy changed", "title": "New review", "rebase_strategy_ref": ref()}
    with pytest.raises(ValidationError):
        ReopenCandidates.model_validate(payload)
    assert ReopenCandidates.model_validate({**payload, "rebase_ltco_ref": ref()}).rebase_strategy_ref


def test_candidate_mission_does_not_accept_guessed_future_pco_revision():
    current = mission()
    proposed = {key: value for key, value in current.items() if key not in {"pco_ref", "supports"}}
    proposed.update(object_id=str(uuid4()), supports=[{"outcome_id": "growth", "contribution": "Grow"}])
    assert CandidateMission.model_validate(proposed).supports
    with pytest.raises(ValidationError):
        CandidateMission.model_validate({**proposed, "pco_ref": ref()})
