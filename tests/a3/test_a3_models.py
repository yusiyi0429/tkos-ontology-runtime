"""Offline strict-model tests for the A3 execution-handover foundation.

Covers memory_service_runtime.governed.a3_models only: strict scalar typing,
ExactRef shape, window ordering, duplicate reference/criterion/step rejection,
plan dependency graph integrity (missing step, cycle, self-loop), self
same-assignment rejection, unknown-field rejection, boolean-as-integer
rejection, structured-What exclusion from ExecutionPlan, and the A3 action
param shapes (accept/activate wire parity with the legacy models, authority
id/epoch on accept_work_item and submit_deliverable, appointment id/version
on review_deliverable).

These tests assert structural/static invariants ONLY. They deliberately do
not assert server actor identity, assignment validity, object existence,
ExecutionAuthority/AcceptanceAppointment state, or cross-object consistency
(WorkItem vs confirmed What, step times vs hard deadline) — none of that is
verifiable at the model layer, and the models do not claim it.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import a3_models as a3
from memory_service_runtime.governed.models import (
    AcceptCommitmentParams,
    ActivateCommitmentParams,
)


def u(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def h(n: int) -> str:
    return f"{n:064x}"


def ref(n: int) -> dict:
    return {"object_id": u(n), "revision_id": u(n + 1000), "payload_hash": h(n)}


def what() -> dict:
    return {
        "result_statement": "deliver the onboarding flow",
        "boundary": "only the onboarding domain",
        "acceptance_criteria": [
            {"criterion_id": "c1", "description": "signup completes end to end"},
            {"criterion_id": "c2", "description": "no P0 defects open"},
        ],
        "hard_deadline": "2026-10-01T00:00:00Z",
        "external_dependency_refs": [ref(50)],
    }


def ec_payload() -> dict:
    return {
        "title": "EC for onboarding",
        "mission_ref": ref(10),
        "domain_commitment_ref": ref(20),
        "dri_assignment_id": u(30),
        "ic_assignment_id": u(31),
        "acceptor_assignment_id": u(32),
        "what": what(),
        "execution_window": {
            "valid_from": "2026-09-15T00:00:00Z",
            "valid_to": "2026-10-01T00:00:00Z",
        },
        "acceptance_window": {
            "valid_from": "2026-09-20T00:00:00Z",
            "valid_to": "2026-10-05T00:00:00Z",
        },
    }


def work_item_payload() -> dict:
    return {
        "title": "WI: build signup page",
        "execution_commitment_ref": ref(10),
        "execution_authority_id": u(40),
        "execution_epoch": 1,
        "acceptance_criteria": [
            {"criterion_id": "c1", "description": "signup completes end to end"},
        ],
        "due_at": "2026-09-28T00:00:00Z",
    }


def plan_payload() -> dict:
    return {
        "title": "Plan v1",
        "work_item_ref": ref(60),
        "execution_commitment_ref": ref(10),
        "steps": [
            {"step_id": "s1", "description": "design"},
            {"step_id": "s2", "description": "build", "depends_on_step_ids": ["s1"],
             "due_at": "2026-09-25T00:00:00Z"},
            {"step_id": "s3", "description": "verify", "depends_on_step_ids": ["s2"]},
        ],
    }


# ------------------------------------------------------------------ ExactRef

def test_exact_ref_accepts_canonical_triple():
    r = a3.ExactRef.model_validate(ref(1))
    assert r.object_id == u(1)
    assert r.revision_id == u(1001)
    assert r.payload_hash == h(1)


@pytest.mark.parametrize("field,value", [
    ("object_id", "00000000-0000-4000-8000-00000000000A"),  # uppercase
    ("object_id", "not-a-uuid"),
    ("revision_id", "0000000040008000000000000000000a"),  # no dashes
    ("payload_hash", "A" * 64),  # uppercase hex
    ("payload_hash", "abc123"),  # too short
])
def test_exact_ref_rejects_noncanonical_scalars(field, value):
    bad = {**ref(1), field: value}
    with pytest.raises(ValidationError):
        a3.ExactRef.model_validate(bad)


def test_exact_ref_preserves_source_strings():
    r = a3.ExactRef.model_validate(ref(2))
    # 哈希/UUID 不被改写，逐字符等于输入。
    assert r.model_dump() == ref(2)


# ------------------------------------------------------------------- windows

def test_window_half_open_ordering():
    a3.A3Window.model_validate(
        {"valid_from": "2026-09-15T00:00:00Z", "valid_to": "2026-09-16T00:00:00Z"}
    )
    for bad_to in ("2026-09-15T00:00:00Z", "2026-09-14T23:59:59Z"):
        with pytest.raises(ValidationError, match="valid_to"):
            a3.A3Window.model_validate(
                {"valid_from": "2026-09-15T00:00:00Z", "valid_to": bad_to}
            )


def test_acceptance_window_is_independent_of_execution_window():
    # 验收窗口早于执行窗口开始也合法（两窗口相互独立）。
    data = ec_payload()
    data["acceptance_window"] = {
        "valid_from": "2026-09-01T00:00:00Z",
        "valid_to": "2026-09-10T00:00:00Z",
    }
    a3.A3ExecutionCommitmentPayload.model_validate(data)


# ------------------------------------------------------- ExecutionCommitment

def test_ec_payload_happy_path_and_string_preservation():
    data = ec_payload()
    data["title"] = "  padded title kept verbatim  "
    model = a3.A3ExecutionCommitmentPayload.model_validate(data)
    assert model.title == "  padded title kept verbatim  "
    assert model.what.result_statement == "deliver the onboarding flow"


@pytest.mark.parametrize("field", ["dri_assignment_id", "ic_assignment_id"])
def test_ec_rejects_self_same_assignment(field):
    data = ec_payload()
    data[field] = data["acceptor_assignment_id"]
    with pytest.raises(ValidationError, match="pairwise distinct"):
        a3.A3ExecutionCommitmentPayload.model_validate(data)


def test_ec_rejects_dri_ic_same_assignment():
    data = ec_payload()
    data["ic_assignment_id"] = data["dri_assignment_id"]
    with pytest.raises(ValidationError, match="pairwise distinct"):
        a3.A3ExecutionCommitmentPayload.model_validate(data)


def test_ec_execution_deadline_within_hard_deadline():
    data = ec_payload()
    # valid_to == hard_deadline 允许（不晚于）。
    a3.A3ExecutionCommitmentPayload.model_validate(data)
    data["execution_window"]["valid_to"] = "2026-10-01T00:00:01Z"
    with pytest.raises(ValidationError, match="hard_deadline"):
        a3.A3ExecutionCommitmentPayload.model_validate(data)


def test_ec_rejects_duplicate_external_dependency_refs():
    data = ec_payload()
    data["what"]["external_dependency_refs"] = [ref(50), ref(50)]
    with pytest.raises(ValidationError, match="duplicate refs"):
        a3.A3ExecutionCommitmentPayload.model_validate(data)


def test_ec_rejects_duplicate_what_criteria():
    data = ec_payload()
    data["what"]["acceptance_criteria"].append(
        {"criterion_id": "c1", "description": "different text, same id"}
    )
    with pytest.raises(ValidationError, match="duplicate acceptance criterion"):
        a3.A3ExecutionCommitmentPayload.model_validate(data)


@pytest.mark.parametrize("key", ["outcome", "what_extra", "outcome_ref", "x"])
def test_ec_rejects_unknown_fields(key):
    data = ec_payload()
    data[key] = "surprise"
    with pytest.raises(ValidationError):
        a3.A3ExecutionCommitmentPayload.model_validate(data)


# ------------------------------------------------------------------ WorkItem

def test_work_item_happy_path():
    model = a3.A3WorkItemPayload.model_validate(work_item_payload())
    assert model.execution_epoch == 1


def test_work_item_rejects_duplicate_criteria():
    data = work_item_payload()
    data["acceptance_criteria"].append(
        {"criterion_id": "c1", "description": "duplicate id"}
    )
    with pytest.raises(ValidationError, match="duplicate acceptance criterion"):
        a3.A3WorkItemPayload.model_validate(data)


@pytest.mark.parametrize("epoch", [True, False, 0, -1, 1.0, "1"])
def test_work_item_rejects_non_positive_or_non_integer_epoch(epoch):
    data = work_item_payload()
    data["execution_epoch"] = epoch
    with pytest.raises(ValidationError):
        a3.A3WorkItemPayload.model_validate(data)


# -------------------------------------------------------------- ExecutionPlan

def test_plan_happy_path_dag():
    model = a3.A3ExecutionPlanPayload.model_validate(plan_payload())
    assert [s.step_id for s in model.steps] == ["s1", "s2", "s3"]


def test_plan_rejects_structured_what():
    data = plan_payload()
    data["what"] = what()
    with pytest.raises(ValidationError):
        a3.A3ExecutionPlanPayload.model_validate(data)
    # 单个步骤也不允许携带结构化 what。
    data = plan_payload()
    data["steps"][0]["what"] = what()
    with pytest.raises(ValidationError):
        a3.A3ExecutionPlanPayload.model_validate(data)


def test_plan_rejects_duplicate_step_ids():
    data = plan_payload()
    data["steps"].append({"step_id": "s1", "description": "again"})
    with pytest.raises(ValidationError, match="duplicate step_id"):
        a3.A3ExecutionPlanPayload.model_validate(data)


def test_plan_rejects_missing_step_reference():
    data = plan_payload()
    data["steps"][1]["depends_on_step_ids"] = ["ghost"]
    with pytest.raises(ValidationError, match="unknown step"):
        a3.A3ExecutionPlanPayload.model_validate(data)


def test_plan_rejects_self_dependency():
    data = plan_payload()
    data["steps"][0]["depends_on_step_ids"] = ["s1"]
    with pytest.raises(ValidationError, match="cycle"):
        a3.A3ExecutionPlanPayload.model_validate(data)


def test_plan_rejects_two_step_cycle():
    data = plan_payload()
    data["steps"][0]["depends_on_step_ids"] = ["s2"]
    # s1 -> s2, s2 -> s1
    with pytest.raises(ValidationError, match="cycle"):
        a3.A3ExecutionPlanPayload.model_validate(data)


def test_plan_rejects_longer_cycle():
    data = plan_payload()
    data["steps"][0]["depends_on_step_ids"] = ["s3"]
    # s3 -> s2 -> s1 -> s3
    with pytest.raises(ValidationError, match="cycle"):
        a3.A3ExecutionPlanPayload.model_validate(data)


def test_plan_rejects_duplicate_depends_on_within_step():
    data = plan_payload()
    data["steps"][2]["depends_on_step_ids"] = ["s2", "s2"]
    with pytest.raises(ValidationError, match="duplicate depends_on"):
        a3.A3ExecutionPlanPayload.model_validate(data)


# --------------------------------------------------- generic create / propose

@pytest.mark.parametrize("object_type,payload_fn", [
    ("ExecutionCommitment", ec_payload),
    ("WorkItem", work_item_payload),
    ("ExecutionPlan", plan_payload),
])
def test_generic_create_dispatches_payload_by_type(object_type, payload_fn):
    params = a3.A3CreateObjectParams.model_validate({
        "object_type": object_type,
        "domain_id": u(70),
        "payload": payload_fn(),
    })
    assert isinstance(params.payload, a3.A3_PAYLOAD_MODELS[object_type])


def test_generic_create_rejects_mismatched_payload():
    with pytest.raises(ValidationError):
        a3.A3CreateObjectParams.model_validate({
            "object_type": "WorkItem",
            "domain_id": u(70),
            "payload": plan_payload(),
        })


@pytest.mark.parametrize("object_type", ["Mission", "DomainCommitment", "Outcome", "Widget"])
def test_generic_create_rejects_non_whitelisted_types(object_type):
    with pytest.raises(ValidationError):
        a3.A3CreateObjectParams.model_validate({
            "object_type": object_type,
            "domain_id": u(70),
            "payload": ec_payload(),
        })


def test_generic_propose_allows_ec_and_plan_only():
    a3.A3ProposeRevisionParams.model_validate({"payload": ec_payload()})
    a3.A3ProposeRevisionParams.model_validate({"payload": plan_payload()})
    # WorkItem 禁止通用改版（契约包 A §5.2）。
    with pytest.raises(ValidationError):
        a3.A3ProposeRevisionParams.model_validate({"payload": work_item_payload()})


# ------------------------------------------------------------- action params

def test_accept_and_activate_commitment_wire_shape_unchanged():
    # 与既有 wire 参数逐字段同名（工程文档 §2：沿用现有参数形状）。
    assert set(a3.A3AcceptCommitmentParams.model_fields) == set(
        AcceptCommitmentParams.model_fields
    )
    assert set(a3.A3ActivateCommitmentParams.model_fields) == set(
        ActivateCommitmentParams.model_fields
    )


def test_accept_commitment_params_validation():
    a3.A3AcceptCommitmentParams.model_validate({
        "party_assignment_id": u(30),
        "understanding": "I confirm the exact same What revision",
        "accepted_terms_hash": h(3),
    })
    with pytest.raises(ValidationError):  # understanding < 10 chars
        a3.A3AcceptCommitmentParams.model_validate({
            "party_assignment_id": u(30),
            "understanding": "short",
            "accepted_terms_hash": h(3),
        })


def test_activate_commitment_params_reject_duplicate_handshakes():
    with pytest.raises(ValidationError, match="duplicate handshake"):
        a3.A3ActivateCommitmentParams.model_validate({
            "handshake_record_ids": [u(80), u(80)],
            "activation_policy_revision_id": u(81),
        })
    with pytest.raises(ValidationError):  # min_length=2
        a3.A3ActivateCommitmentParams.model_validate({
            "handshake_record_ids": [u(80)],
            "activation_policy_revision_id": u(81),
        })


def test_accept_work_item_params_authority_and_epoch():
    model = a3.A3AcceptWorkItemParams.model_validate({
        "execution_authority_id": u(40),
        "execution_epoch": 2,
    })
    assert model.execution_epoch == 2
    with pytest.raises(ValidationError):  # bool 不是整数
        a3.A3AcceptWorkItemParams.model_validate({
            "execution_authority_id": u(40),
            "execution_epoch": True,
        })
    with pytest.raises(ValidationError):  # 缺 epoch
        a3.A3AcceptWorkItemParams.model_validate({"execution_authority_id": u(40)})


def test_submit_deliverable_params():
    model = a3.A3SubmitDeliverableParams.model_validate({
        "title": "v1",
        "summary": "first submission",
        "evidence_revision_ids": [u(90)],
        "execution_authority_id": u(40),
        "execution_epoch": 2,
        "plan_ref": ref(60),
    })
    assert model.responds_to_acceptance_id is None
    assert model.plan_ref.payload_hash == h(60)


def test_submit_deliverable_requires_authority_epoch_and_plan_ref():
    base = {
        "title": "v1",
        "summary": "first submission",
        "evidence_revision_ids": [u(90)],
    }
    with pytest.raises(ValidationError):
        a3.A3SubmitDeliverableParams.model_validate(base)
    # 作者集合不由请求填写。
    with pytest.raises(ValidationError):
        a3.A3SubmitDeliverableParams.model_validate({
            **base,
            "execution_authority_id": u(40),
            "execution_epoch": 1,
            "plan_ref": ref(60),
            "authors": [u(99)],
        })
    with pytest.raises(ValidationError, match="duplicate evidence"):
        a3.A3SubmitDeliverableParams.model_validate({
            **base,
            "evidence_revision_ids": [u(90), u(90)],
            "execution_authority_id": u(40),
            "execution_epoch": 1,
            "plan_ref": ref(60),
        })


def test_review_deliverable_params():
    model = a3.A3ReviewDeliverableParams.model_validate({
        "deliverable_revision_id": u(91),
        "delivery_payload_hash": h(92),
        "verification_result": "changes_requested",
        "criterion_results": [
            {"criterion_id": "c1", "result": "failed", "note": "signup 500s"},
        ],
        "review_note": "please fix and resubmit",
        "appointment_id": u(93),
        "appointment_version": 1,
    })
    assert model.appointment_version == 1


def test_review_deliverable_requires_appointment_binding():
    base = {
        "deliverable_revision_id": u(91),
        "delivery_payload_hash": h(92),
        "verification_result": "accepted",
        "criterion_results": [
            {"criterion_id": "c1", "result": "passed", "note": "ok"},
        ],
        "review_note": "accepted",
    }
    with pytest.raises(ValidationError):
        a3.A3ReviewDeliverableParams.model_validate(base)
    with pytest.raises(ValidationError):  # bool 不是整数
        a3.A3ReviewDeliverableParams.model_validate({
            **base, "appointment_id": u(93), "appointment_version": True,
        })
    with pytest.raises(ValidationError, match="duplicate criterion"):
        a3.A3ReviewDeliverableParams.model_validate({
            **base,
            "criterion_results": [
                {"criterion_id": "c1", "result": "passed", "note": "ok"},
                {"criterion_id": "c1", "result": "failed", "note": "not ok"},
            ],
            "appointment_id": u(93),
            "appointment_version": 1,
        })


@pytest.mark.parametrize("cls,data", [
    (a3.A3AcceptWorkItemParams,
     {"execution_authority_id": u(40), "execution_epoch": 1, "extra": 1}),
    (a3.A3SubmitDeliverableParams,
     {"title": "t", "summary": "s", "evidence_revision_ids": [u(90)],
      "execution_authority_id": u(40), "execution_epoch": 1,
      "plan_ref": ref(60), "extra": 1}),
    (a3.A3ReviewDeliverableParams,
     {"deliverable_revision_id": u(91), "delivery_payload_hash": h(92),
      "verification_result": "accepted",
      "criterion_results": [{"criterion_id": "c1", "result": "passed", "note": "ok"}],
      "review_note": "ok", "appointment_id": u(93), "appointment_version": 1,
      "extra": 1}),
])
def test_action_params_reject_unknown_fields(cls, data):
    with pytest.raises(ValidationError):
        cls.model_validate(data)


# ----------------------------------------------------------------- registries

def test_payload_models_registry():
    assert set(a3.A3_PAYLOAD_MODELS) == {
        "ExecutionCommitment", "WorkItem", "ExecutionPlan",
    }
    assert a3.A3_PAYLOAD_MODELS["ExecutionCommitment"] is a3.A3ExecutionCommitmentPayload
    assert a3.A3_PAYLOAD_MODELS["WorkItem"] is a3.A3WorkItemPayload
    assert a3.A3_PAYLOAD_MODELS["ExecutionPlan"] is a3.A3ExecutionPlanPayload


def test_action_params_registry():
    assert set(a3.A3_ACTION_PARAMS) == a3.A3_ACTIONS
    assert a3.A3_ACTION_PARAMS["accept_work_item"] is a3.A3AcceptWorkItemParams
    assert a3.A3_ACTION_PARAMS["submit_deliverable"] is a3.A3SubmitDeliverableParams
    assert a3.A3_ACTION_PARAMS["review_deliverable"] is a3.A3ReviewDeliverableParams
    assert a3.A3_ACTION_PARAMS["accept_commitment"] is a3.A3AcceptCommitmentParams
    assert a3.A3_ACTION_PARAMS["activate_commitment"] is a3.A3ActivateCommitmentParams


def test_generic_type_sets():
    assert a3.A3_GENERIC_CREATE_TYPES == {
        "ExecutionCommitment", "WorkItem", "ExecutionPlan",
    }
    assert a3.A3_GENERIC_REVISE_TYPES == {"ExecutionCommitment", "ExecutionPlan"}
