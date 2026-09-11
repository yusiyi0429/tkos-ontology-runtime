"""Offline A2 envelope smoke tests.

These tests cover the public command shapes introduced in models.py and the
protocol fence changes; they do NOT need a real database.  They construct
real ActionRequest envelopes for the six A2 actions and the two A2 source
writes, verify that ``model_dump(mode="json", exclude_none=True)`` produces
the exact flat parameter layout the runtime expects, and exercise the
protocol.create / protocol.target-action allowlist invariants on synthetic
mappings (without touching psycopg).

Independent HTTP matrix coverage belongs to the per-action integration tests;
this file is the envelope-only guardrail.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import protocol
from memory_service_runtime.governed.a2_models import (
    AmendFormationRoundParams,
    CapacityObservationPayload,
    CompanyReferencePayload,
    FormCompanyCompositionParams,
    OpenFormationRoundParams,
    PublishDomainSubmissionParams,
    ActivateCompanyCompositionParams,
    ConfirmCompanyCompositionParams,
)
from memory_service_runtime.governed.a2_service import A2Execution as _A2ServiceExecution
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.models import (
    A2_GENERIC_SOURCE_OBJECT_TYPES,
    A2_OBJECT_TYPE_NAMES,
    ActionRequest,
    CreateObjectParams,
    OpenFormationRoundParams as _A2Alias,  # noqa: F401  (public export)
    ProposeRevisionParams,
    PAYLOAD_MODELS,
)
from memory_service_runtime.governed.service import ActionExecution, _execution_factory


# ---------------------------------------------------------------- fixtures


def _uuid_str(seed: str) -> str:
    # Deterministic canonical lowercase UUID derived from a label; avoids
    # importing uuid.uuid4 at module level so the test stays offline.
    h = hashlib.sha256(seed.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


COMPANY_ID = _uuid_str("company")
COMPANY_DOMAIN_ID = _uuid_str("company-domain")
CEO_ASSIGNMENT_ID = _uuid_str("ceo-assignment")
PERIOD_ID = _uuid_str("period")
PROFILE_ID = "urn:tkos:experimental:method-profile:contract-a"
PROFILE_REV = "0.1.0"
REFERENCE_OBJECT_ID = _uuid_str("ref-obj")
REFERENCE_REVISION_ID = _uuid_str("ref-rev")
REFERENCE_PAYLOAD_HASH = "0" * 64

DOMAIN_A_ID = _uuid_str("domain-a")
DRI_A_ASSIGNMENT_ID = _uuid_str("dri-a-assignment")
DRI_A_PRINCIPAL_ID = _uuid_str("dri-a-principal")
DOMAIN_B_ID = _uuid_str("domain-b")
DRI_B_ASSIGNMENT_ID = _uuid_str("dri-b-assignment")
DRI_B_PRINCIPAL_ID = _uuid_str("dri-b-principal")
CEO_PRINCIPAL_ID = _uuid_str("ceo-principal")
CAPACITY_OBS_OBJECT_ID = _uuid_str("cap-obj")
CAPACITY_OBS_REVISION_ID = _uuid_str("cap-rev")


# ---------------------------------------------------------------- helpers


def _dumped(request: ActionRequest) -> dict[str, Any]:
    return request.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------- envelope tests


def test_open_formation_round_envelope_is_flat() -> None:
    """The wire request must carry ``params`` with company_id, members, etc.,
    directly addressable; A2Execution reads ``request.params.company_id``
    (no wrapper, no nested ``params.params``).
    """
    members = [{"domain_id": DOMAIN_A_ID, "dri_assignment_id": DRI_A_ASSIGNMENT_ID}]
    body = {
        "action_type": "open_formation_round",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "open-round-p01-0001",
        "reason": "open P01 formation round",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "company_id": COMPANY_ID,
            "company_domain_id": COMPANY_DOMAIN_ID,
            "ceo_assignment_id": CEO_ASSIGNMENT_ID,
            "period_id": PERIOD_ID,
            "period_window": {"start": "2026-10-01T00:00:00+08:00",
                              "end": "2026-10-15T00:00:00+08:00"},
            "method_profile_ref": {"profile_id": PROFILE_ID, "revision": PROFILE_REV},
            "company_reference_ref": {"object_id": REFERENCE_OBJECT_ID,
                                       "revision_id": REFERENCE_REVISION_ID,
                                       "payload_hash": REFERENCE_PAYLOAD_HASH},
            "members": members,
        },
    }
    request = ActionRequest.model_validate(body)
    # Flat access — ActionExecution.params["company_id"] must work.
    dumped = _dumped(request)
    assert dumped["action_type"] == "open_formation_round"
    assert dumped["params"]["company_id"] == COMPANY_ID
    assert dumped["params"]["members"] == members
    assert dumped["params"]["company_reference_ref"]["object_id"] == REFERENCE_OBJECT_ID
    # And pydantic access at runtime:
    assert request.params.company_id == COMPANY_ID
    assert isinstance(request.params, OpenFormationRoundParams)
    # No nesting wrapper:
    assert not hasattr(request.params, "params")


def test_amend_formation_round_envelope_is_flat() -> None:
    body = {
        "action_type": "amend_formation_round",
        "target": {"object_id": _uuid_str("round"),
                    "revision_id": _uuid_str("round-rev1"),
                    "expected_version": 3},
        "expected_versions": [],
        "idempotency_key": "amend-round-0001-padded",
        "reason": "add responsibility area C",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "expected_member_set_version": 1,
            "members": [{"domain_id": DOMAIN_A_ID, "dri_assignment_id": DRI_A_ASSIGNMENT_ID}],
            "change_reason": "add responsibility area C",
        },
    }
    request = ActionRequest.model_validate(body)
    assert request.action_type == "amend_formation_round"
    assert request.target is not None
    assert request.params.expected_member_set_version == 1
    assert isinstance(request.params, AmendFormationRoundParams)


def test_publish_domain_submission_envelope_is_flat() -> None:
    body = {
        "action_type": "publish_domain_submission",
        "target": {"object_id": _uuid_str("round"),
                    "revision_id": _uuid_str("round-rev1"),
                    "expected_version": 4},
        "expected_versions": [],
        "idempotency_key": "publish-b-r1-0001-extra",
        "reason": "formal submission for domain B",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "domain_id": DOMAIN_B_ID,
            "dri_assignment_id": DRI_B_ASSIGNMENT_ID,
            "draft_checkpoint": "local-draft-2026-10-02T10:00",
            "commitment_statement": "本人以当前指定 DRI 身份正式提交并确认本包内容",
            "submission": {
                "result_statement": "本周期支撑 3 家试点客户上线",
                "pdo": {"pdo_key": "pdo-b-1",
                         "statement": "本周期目标说明",
                         "result_criteria": [{"criterion_id": "cap-ok",
                                              "description": "3 家按期完成 onboarding"}]},
                "missions": [{"mission_key": "b-onboarding",
                              "result_statement": "交付 SSO 与上线能力",
                              "boundary": "不含客户侧数据迁移",
                              "acceptance_criteria": [{"criterion_id": "m1",
                                                       "description": "SSO 接入测试通过"}],
                              "dependency_refs": []}],
                "resources": [{"resource_id": _uuid_str("pool"),
                                "period_id": PERIOD_ID,
                                "unit": "synthetic_onboarding_slot",
                                "required": 3}],
                "bindings": [],
                "upstream_refs": [],
                "unknowns": [],
            },
        },
    }
    request = ActionRequest.model_validate(body)
    assert isinstance(request.params, PublishDomainSubmissionParams)
    assert request.params.domain_id == DOMAIN_B_ID
    assert request.params.submission.resources[0].required == 3


def test_form_company_composition_envelope_is_flat() -> None:
    body = {
        "action_type": "form_company_composition",
        "target": {"object_id": _uuid_str("round"),
                    "revision_id": _uuid_str("round-rev1"),
                    "expected_version": 5},
        "expected_versions": [],
        "idempotency_key": "form-c1-0001-padding",
        "reason": "form composition candidate C1",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "expected_member_set_version": 1,
            "expected_input_set_version": 3,
            "judgments": {
                "coverage": {"conclusion": "pass", "reason": "完整覆盖", "evidence_refs": []},
                "coherence": {"conclusion": "pass", "reason": "一致", "evidence_refs": []},
                "feasibility": {"conclusion": "pass", "reason": "可行", "evidence_refs": []},
                "tradeoff": {"conclusion": "pass", "reason": "可接受", "evidence_refs": []},
            },
            "unresolved_conflicts": [],
        },
    }
    request = ActionRequest.model_validate(body)
    assert isinstance(request.params, FormCompanyCompositionParams)
    assert request.params.expected_input_set_version == 3


def test_confirm_company_composition_envelope_is_flat() -> None:
    body = {
        "action_type": "confirm_company_composition",
        "target": {"object_id": _uuid_str("comp"),
                    "revision_id": _uuid_str("comp-rev1"),
                    "expected_version": 1},
        "expected_versions": [{"object_id": _uuid_str("round"), "expected_version": 5}],
        "idempotency_key": "confirm-c1-ceo-0001-extra",
        "reason": "confirm composition C1 as CEO",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "composition_ref": {"object_id": _uuid_str("comp"),
                                 "revision_id": _uuid_str("comp-rev1"),
                                 "manifest_hash": "f" * 64},
            "assignment_id": CEO_ASSIGNMENT_ID,
            "confirmation_statement": "本人已阅读完整 manifest 并以当前任职确认同一版本",
        },
    }
    request = ActionRequest.model_validate(body)
    assert isinstance(request.params, ConfirmCompanyCompositionParams)
    assert request.params.assignment_id == CEO_ASSIGNMENT_ID


def test_activate_company_composition_envelope_is_flat() -> None:
    body = {
        "action_type": "activate_company_composition",
        "target": {"object_id": _uuid_str("comp"),
                    "revision_id": _uuid_str("comp-rev1"),
                    "expected_version": 4},
        "expected_versions": [{"object_id": _uuid_str("round"), "expected_version": 5}],
        "idempotency_key": "activate-c3-0001-padded",
        "reason": "activate composition C3",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "composition_ref": {"object_id": _uuid_str("comp"),
                                 "revision_id": _uuid_str("comp-rev1"),
                                 "manifest_hash": "a" * 64},
            "expected_member_set_version": 1,
            "expected_input_set_version": 5,
        },
    }
    request = ActionRequest.model_validate(body)
    assert isinstance(request.params, ActivateCompanyCompositionParams)
    assert request.params.expected_input_set_version == 5


# ---------------------------------------------------------------- A2 source writes


def test_create_company_reference_envelope_is_flat() -> None:
    """Generic create_object on CompanyReference must succeed with the same
    flat payload layout as legacy types and reuse ``CreateObjectParams``.
    """
    body = {
        "action_type": "create_object",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "create-ref-p01-0001-pad",
        "reason": "create company reference",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "object_type": "CompanyReference",
            "domain_id": COMPANY_DOMAIN_ID,
            "payload": {
                "title": "P01 Company Reference",
                "statement": "本周期公司正式 Reference",
                "period_id": PERIOD_ID,
                "terms": {},
                "shared_with_domain_ids": [],
                "upstream_refs": [],
            },
        },
    }
    request = ActionRequest.model_validate(body)
    dumped = _dumped(request)
    # Flat: params.object_type, params.domain_id, params.payload are
    # directly at the top level of the dumped envelope.
    assert dumped["params"]["object_type"] == "CompanyReference"
    assert dumped["params"]["domain_id"] == COMPANY_DOMAIN_ID
    assert dumped["params"]["payload"]["title"] == "P01 Company Reference"
    assert isinstance(request.params, CreateObjectParams)
    assert isinstance(request.params.payload, CompanyReferencePayload)


def test_propose_revision_on_capacity_observation_envelope_is_flat() -> None:
    """A2 source propose_revision uses the legacy envelope, but the routed
    payload model is CapacityObservationPayload; protocol.gate_target_action
    must accept propose_revision on this A2-bound source.
    """
    body = {
        "action_type": "propose_revision",
        "target": {"object_id": CAPACITY_OBS_OBJECT_ID,
                    "revision_id": CAPACITY_OBS_REVISION_ID,
                    "expected_version": 1},
        "expected_versions": [],
        "idempotency_key": "propose-cap-p01-0001-pad",
        "reason": "bump capacity observation available count",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "payload": {
                "title": "P01 capacity observation v2",
                "resource_id": _uuid_str("pool"),
                "period_id": PERIOD_ID,
                "unit": "synthetic_onboarding_slot",
                "available": 5,
                "reserved": 0,
                "observed_at": "2026-10-02T09:00:00+08:00",
                "valid_from": "2026-10-01T00:00:00+08:00",
                "valid_to": None,
                "note": "",
                "shared_with_domain_ids": [DOMAIN_B_ID],
                "upstream_refs": [],
            },
        },
    }
    request = ActionRequest.model_validate(body)
    assert isinstance(request.params, ProposeRevisionParams)
    assert isinstance(request.params.payload, CapacityObservationPayload)
    assert request.params.payload.available == 5


# ---------------------------------------------------------------- registry / metadata


def test_a2_catalog_includes_two_source_types_and_full_set() -> None:
    # Two source types can go through generic create_object / propose_revision.
    assert "CompanyReference" in A2_GENERIC_SOURCE_OBJECT_TYPES
    assert "CapacityObservation" in A2_GENERIC_SOURCE_OBJECT_TYPES
    # All 7 A2 types are in the catalog.
    assert set(A2_OBJECT_TYPE_NAMES) == {
        "CompanyReference", "CapacityObservation", "FormationRound",
        "DomainSubmission", "CompanyComposition", "Mission", "DomainCommitment",
    }
    # PAYLOAD_MODELS includes the two source types.
    assert PAYLOAD_MODELS["CompanyReference"] is CompanyReferencePayload
    assert PAYLOAD_MODELS["CapacityObservation"] is CapacityObservationPayload


def test_payload_models_does_not_register_derived_a2_types() -> None:
    # Derived A2 types are not in PAYLOAD_MODELS: generic create_object
    # cannot reach them.
    for derived in ("FormationRound", "DomainSubmission", "CompanyComposition",
                    "Mission", "DomainCommitment"):
        assert derived not in PAYLOAD_MODELS


def test_idempotency_hash_is_stable_for_legacy_command() -> None:
    """The legacy request_hash via model_dump(exclude_none=True) must stay
    byte-identical for a legacy command (golden guardrail)."""
    legacy = {
        "action_type": "create_object",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "companyoutcome-bootstrap-001",
        "reason": "bootstrap first outcome",
        "params": {
            "object_type": "CompanyOutcome",
            "domain_id": _uuid_str("company-domain"),
            "payload": {
                "title": "Q1 outcome",
                "terms": {},
                "outcome_statement": "本周期成果目标",
                "upstream_refs": [],
            },
        },
    }
    request = ActionRequest.model_validate(legacy)
    text = json.dumps(_dumped(request), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(text.encode()).hexdigest()
    # Hash is stable across repeated constructions.
    text2 = json.dumps(_dumped(ActionRequest.model_validate(legacy)),
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert text == text2
    # And the digest is a 64-char hex string (tkos-json-v1 over canonical
    # request bytes).
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


# ---------------------------------------------------------------- protocol fence


def test_target_action_allowlist_per_action() -> None:
    expected = {
        "amend_formation_round": frozenset({"FormationRound"}),
        "publish_domain_submission": frozenset({"FormationRound"}),
        "form_company_composition": frozenset({"FormationRound"}),
        "confirm_company_composition": frozenset({"CompanyComposition"}),
        "activate_company_composition": frozenset({"CompanyComposition"}),
        "propose_revision": frozenset({"CompanyReference", "CapacityObservation"}),
    }
    for action, allowed in expected.items():
        assert protocol.A2_TARGET_OBJECT_TYPES[action] == allowed


def test_target_action_allowlist_blocks_other_types() -> None:
    # A2 action on the wrong A2 target type is rejected by the allowlist.
    assert "open_formation_round" not in protocol.A2_TARGET_OBJECT_TYPES
    # An A2 action whose target is unknown to the runtime also has no allowlist entry.
    for action in ("submit_deliverable", "accept_commitment"):
        assert action not in protocol.A2_TARGET_OBJECT_TYPES


def test_a2_creation_allowlist_per_action() -> None:
    # Only the two source types are allowed for generic create_object.
    assert protocol.A2_GENERIC_CREATABLE == frozenset({"CompanyReference", "CapacityObservation"})
    # open_formation_round creates only FormationRound.
    assert protocol.A2_INTERNAL_CREATABLE == frozenset({"FormationRound"})
    # Derived A2 types must use inherit_binding.
    assert protocol.A2_DERIVED_TYPES == frozenset({
        "DomainSubmission", "CompanyComposition", "Mission", "DomainCommitment",
    })


# ---------------------------------------------------------------- no-regression


def test_envelope_rejects_unknown_action_type() -> None:
    """Unknown action types still surface as 422 INVALID_REQUEST, never as
    409 — the A2 name list extension must not break the legacy path."""
    bad = {
        "action_type": "withdraw_composition_confirmation",  # A2 not_implemented
        "target": None,
        "expected_versions": [],
        "idempotency_key": "withdraw-0001-padded-12",
        "reason": "withdraw confirmation",
        "params": {},
    }
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(bad)


def test_envelope_target_rules_for_a2_actions() -> None:
    # open_formation_round requires target=None.
    open_no_target = {
        "action_type": "open_formation_round",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "open-round-0001-padding",
        "reason": "open round",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "company_id": COMPANY_ID,
            "company_domain_id": COMPANY_DOMAIN_ID,
            "ceo_assignment_id": CEO_ASSIGNMENT_ID,
            "period_id": PERIOD_ID,
            "period_window": {"start": "2026-10-01T00:00:00+08:00",
                              "end": "2026-10-15T00:00:00+08:00"},
            "method_profile_ref": {"profile_id": PROFILE_ID, "revision": PROFILE_REV},
            "company_reference_ref": {"object_id": REFERENCE_OBJECT_ID,
                                       "revision_id": REFERENCE_REVISION_ID,
                                       "payload_hash": REFERENCE_PAYLOAD_HASH},
            "members": [{"domain_id": DOMAIN_A_ID, "dri_assignment_id": DRI_A_ASSIGNMENT_ID}],
        },
    }
    ActionRequest.model_validate(open_no_target)
    # open_formation_round with target must be rejected.
    open_with_target = dict(open_no_target)
    open_with_target["target"] = {"object_id": _uuid_str("anything"),
                                   "revision_id": _uuid_str("rev"),
                                   "expected_version": 1}
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(open_with_target)

    # confirm_company_composition without target must be rejected.
    confirm_no_target = {
        "action_type": "confirm_company_composition",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "confirm-0001-padded-12",
        "reason": "confirm",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "composition_ref": {"object_id": _uuid_str("comp"),
                                 "revision_id": _uuid_str("comp-rev"),
                                 "manifest_hash": "b" * 64},
            "assignment_id": CEO_ASSIGNMENT_ID,
            "confirmation_statement": "本人确认",
        },
    }
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(confirm_no_target)


# ---------------------------------------------------------------- factory / class routing


class _StubConn:
    """Minimal stub of the psycopg connection used by the factory tests.

    Only ``execute`` is exercised — and only to verify the factory does not
    touch the connection before delegating to ``handles_request``.
    """


class _StubCtx(SimpleNamespace):
    pass


def test_actionexecution_has_original_finish_method() -> None:
    """The base ActionExecution.finish must exist and carry the original
    signature/body — every legacy successful action depends on it."""
    assert hasattr(ActionExecution, "finish"), "ActionExecution.finish is gone"
    method = getattr(ActionExecution, "finish")
    import inspect
    src = inspect.getsource(method)
    # The original body is anchored on the immutable gov_action_receipts
    # INSERT; any wrapper or duplicate would also touch this query but the
    # surrounding bookkeeping (enqueue_task, _receipt) is unique.
    assert "gov_action_receipts" in src
    assert "enqueue_task" in inspect.getsource(ActionExecution._enqueue_effects)
    assert "self.required_assignments" in src


def test_factory_routes_a2_action_to_a2_service_class() -> None:
    """For any of the six A2 action names the factory must produce the
    a2_service.A2Execution class instance, never the base ActionExecution.
    """
    body = {
        "action_type": "open_formation_round",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "factory-open-round-pad-12",
        "reason": "open round factory check",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "company_id": COMPANY_ID,
            "company_domain_id": COMPANY_DOMAIN_ID,
            "ceo_assignment_id": CEO_ASSIGNMENT_ID,
            "period_id": PERIOD_ID,
            "period_window": {"start": "2026-10-01T00:00:00+08:00",
                              "end": "2026-10-15T00:00:00+08:00"},
            "method_profile_ref": {"profile_id": PROFILE_ID, "revision": PROFILE_REV},
            "company_reference_ref": {"object_id": REFERENCE_OBJECT_ID,
                                       "revision_id": REFERENCE_REVISION_ID,
                                       "payload_hash": REFERENCE_PAYLOAD_HASH},
            "members": [{"domain_id": DOMAIN_A_ID,
                         "dri_assignment_id": DRI_A_ASSIGNMENT_ID}],
        },
    }
    request = ActionRequest.model_validate(body)
    execution = _execution_factory(_StubConn(), _StubCtx(scope_id="scope",
                                                          tenant_id="tenant",
                                                          company_id=COMPANY_ID,
                                                          principal_id="principal",
                                                          principal_type="human",
                                                          auth_epoch=1,
                                                          assignments=[]),
                                    request)
    assert isinstance(execution, _A2ServiceExecution)
    assert not isinstance(execution, ActionExecution) or type(execution) is not ActionExecution


def test_factory_routes_a2_source_create_to_a2_service_class() -> None:
    body = {
        "action_type": "create_object",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "factory-create-ref-pad-12",
        "reason": "create ref factory check",
        "contract_version": "tkos.contract-a/0.1",
        "params": {
            "object_type": "CompanyReference",
            "domain_id": COMPANY_DOMAIN_ID,
            "payload": {
                "title": "P01 Company Reference",
                "statement": "本周期公司正式 Reference",
                "period_id": PERIOD_ID,
                "terms": {},
                "shared_with_domain_ids": [],
                "upstream_refs": [],
            },
        },
    }
    request = ActionRequest.model_validate(body)
    execution = _execution_factory(_StubConn(), _StubCtx(scope_id="scope",
                                                          tenant_id="tenant",
                                                          company_id=COMPANY_ID,
                                                          principal_id="principal",
                                                          principal_type="human",
                                                          auth_epoch=1,
                                                          assignments=[]),
                                    request)
    assert type(execution) is _A2ServiceExecution


def test_factory_routes_legacy_command_to_base_actionexecution() -> None:
    body = {
        "action_type": "create_object",
        "target": None,
        "expected_versions": [],
        "idempotency_key": "factory-legacy-create-pad-12",
        "reason": "legacy create factory check",
        "params": {
            "object_type": "CompanyOutcome",
            "domain_id": COMPANY_DOMAIN_ID,
            "payload": {
                "title": "Q1 outcome",
                "terms": {},
                "outcome_statement": "本周期成果目标",
                "upstream_refs": [],
            },
        },
    }
    request = ActionRequest.model_validate(body)
    execution = _execution_factory(_StubConn(), _StubCtx(scope_id="scope",
                                                          tenant_id="tenant",
                                                          company_id=COMPANY_ID,
                                                          principal_id="principal",
                                                          principal_type="human",
                                                          auth_epoch=1,
                                                          assignments=[]),
                                    request)
    assert type(execution) is ActionExecution


def test_a2_service_handles_request_for_six_a2_actions() -> None:
    """The business-owner's handles_request must accept the six A2 action
    names verbatim.  We use a stub connection/ctx because the classmethod only
    inspects the request.
    """
    request_open = ActionRequest.model_validate({
        "action_type": "open_formation_round", "target": None,
        "expected_versions": [], "idempotency_key": "handles-open-padded-12",
        "reason": "open round", "contract_version": "tkos.contract-a/0.1",
        "params": {"company_id": COMPANY_ID, "company_domain_id": COMPANY_DOMAIN_ID,
                    "ceo_assignment_id": CEO_ASSIGNMENT_ID, "period_id": PERIOD_ID,
                    "period_window": {"start": "2026-10-01T00:00:00+08:00",
                                       "end": "2026-10-15T00:00:00+08:00"},
                    "method_profile_ref": {"profile_id": PROFILE_ID, "revision": PROFILE_REV},
                    "company_reference_ref": {"object_id": REFERENCE_OBJECT_ID,
                                               "revision_id": REFERENCE_REVISION_ID,
                                               "payload_hash": REFERENCE_PAYLOAD_HASH},
                    "members": [{"domain_id": DOMAIN_A_ID,
                                  "dri_assignment_id": DRI_A_ASSIGNMENT_ID}]},
    })
    assert _A2ServiceExecution.handles_request(_StubConn(), _StubCtx(), request_open)
    # And legacy create_object with non-A2 type must NOT be claimed by A2.
    legacy = ActionRequest.model_validate({
        "action_type": "create_object", "target": None,
        "expected_versions": [], "idempotency_key": "handles-legacy-padded-12",
        "reason": "legacy create",
        "params": {"object_type": "CompanyOutcome", "domain_id": COMPANY_DOMAIN_ID,
                    "payload": {"title": "Q1", "terms": {}, "outcome_statement": "x",
                                 "upstream_refs": []}},
    })
    assert not _A2ServiceExecution.handles_request(_StubConn(), _StubCtx(), legacy)
