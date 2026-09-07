"""Public v0.2 command boundaries; no database or service implementation needed."""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed.models import ActionRequest, validated_payload


def uid(value: int) -> str:
    return str(UUID(int=value))


def work_item() -> dict:
    return {
        "title": "交付业务域验收报告",
        "execution_commitment_ref": {"object_id": uid(1), "revision_id": uid(2)},
        "dri_assignment_id": uid(3),
        "acceptor_assignment_id": uid(4),
        "acceptance_criteria": [{"criterion_id": "coverage", "description": "覆盖全部约定场景"}],
    }


def action(action_type: str, params: dict) -> dict:
    return {
        "action_type": action_type,
        "target": {"object_id": uid(10), "revision_id": uid(11), "expected_version": 1},
        "expected_versions": [],
        "idempotency_key": "delivery-model-test-0001",
        "reason": "检验交付闭环的公开命令边界",
        "params": params,
    }


def submit() -> dict:
    return {"title": "验收报告 v1", "summary": "覆盖三个场景", "evidence_revision_ids": [uid(20)]}


def review() -> dict:
    return {
        "deliverable_revision_id": uid(30),
        "delivery_payload_hash": "a" * 64,
        "verification_result": "accepted",
        "criterion_results": [{"criterion_id": "coverage", "result": "passed", "note": "三项场景均核对"}],
        "review_note": "已按约定标准逐项验收",
    }


def assess() -> dict:
    return {
        "assessment_result": "not_achieved",
        "observation_revision_ids": [uid(40)],
        "evidence_revision_ids": [uid(20)],
        "assessment_note": "报告已通过，但业务指标尚未达到目标",
    }


def test_work_item_creation_preserves_exact_baseline_and_normalizes_due_time() -> None:
    payload = work_item()
    payload["due_at"] = "2026-09-30T18:00:00+08:00"
    payload["feedback_ref"] = {"object_id": uid(50), "revision_id": uid(51)}
    request = action("create_object", {"object_type": "WorkItem", "domain_id": uid(5), "payload": payload})
    request["target"] = None
    parsed = ActionRequest.model_validate(request)
    actual = parsed.params.model_dump(mode="json")["payload"]
    assert actual["execution_commitment_ref"] == payload["execution_commitment_ref"]
    assert actual["acceptance_criteria"] == payload["acceptance_criteria"]
    assert actual["feedback_ref"] == payload["feedback_ref"]
    assert actual["due_at"] == "2026-09-30T10:00:00+00:00"


@pytest.mark.parametrize("change", [
    {"acceptance_criteria": []},
    {"acceptance_criteria": [{"criterion_id": "coverage", "description": "说明一"},
                             {"criterion_id": "coverage", "description": "说明二"}]},
    {"acceptance_criteria": [{"criterion_id": " ", "description": "说明"}]},
    {"acceptance_criteria": [{"criterion_id": "coverage", "description": "\t"}]},
    {"execution_commitment_ref": {"object_id": uid(1)}},
    {"due_at": "2026-09-30T18:00:00"},
    {"dri_assignment_id": 3},
    {"lifecycle_status": "delivery_accepted"},
])
def test_work_item_rejects_ambiguous_or_untrusted_baselines(change: dict) -> None:
    with pytest.raises(ValidationError):
        validated_payload("WorkItem", {**work_item(), **change})


def test_deliverable_can_only_enter_through_submit_action() -> None:
    request = action("create_object", {
        "object_type": "Deliverable", "domain_id": uid(5), "payload": {"title": "绕过提交"},
    })
    request["target"] = None
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(request)
    with pytest.raises(ValueError, match="unsupported object type"):
        validated_payload("Deliverable", submit())
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(action("propose_revision", {"payload": submit()}))


@pytest.mark.parametrize(("action_type", "params"), [
    ("accept_work_item", {}),
    ("submit_deliverable", submit()),
    ("review_deliverable", review()),
    ("record_outcome_assessment", assess()),
])
def test_new_actions_require_a_versioned_target(action_type: str, params: dict) -> None:
    request = action(action_type, params)
    assert ActionRequest.model_validate(request).action_type == action_type
    request["target"] = None
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(request)
    request["target"] = {"object_id": uid(10), "revision_id": uid(11), "expected_version": True}
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(request)


def test_resubmission_cites_the_specific_return_record() -> None:
    params = {**submit(), "responds_to_acceptance_id": uid(60)}
    parsed = ActionRequest.model_validate(action("submit_deliverable", params))
    assert parsed.params.model_dump(mode="json")["responds_to_acceptance_id"] == uid(60)
    for invalid in ([], [uid(20), uid(20)]):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate(action("submit_deliverable", {**params, "evidence_revision_ids": invalid}))


@pytest.mark.parametrize("change", [
    {"delivery_payload_hash": "not-a-hash"},
    {"criterion_results": []},
    {"criterion_results": review()["criterion_results"] * 2},
    {"criterion_results": [{"criterion_id": "coverage", "result": "passed", "note": " "}]},
    {"verifier_principal_id": uid(90)},
    {"review_note": ""},
])
def test_review_requires_specific_hash_criteria_and_server_resolved_actor(change: dict) -> None:
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(action("review_deliverable", {**review(), **change}))


def test_delivery_judgment_does_not_encode_other_two_judgments() -> None:
    for field, value in (("outcome_achieved", True), ("mf_closed", True)):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate(action("review_deliverable", {**review(), field: value}))
    parsed = ActionRequest.model_validate(action("record_outcome_assessment", assess()))
    assert parsed.params.model_dump(mode="json")["delivery_acceptance_ids"] == []
    assert parsed.params.model_dump(mode="json")["assessment_result"] == "not_achieved"


@pytest.mark.parametrize("field", ["observation_revision_ids", "evidence_revision_ids", "delivery_acceptance_ids"])
def test_outcome_assessment_rejects_repeated_reference_votes(field: str) -> None:
    params = deepcopy(assess())
    params[field] = [uid(80), uid(80)]
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(action("record_outcome_assessment", params))


@pytest.mark.parametrize("field", ["observation_revision_ids", "evidence_revision_ids"])
def test_outcome_assessment_requires_observation_and_evidence(field: str) -> None:
    params = {**assess(), field: []}
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(action("record_outcome_assessment", params))
