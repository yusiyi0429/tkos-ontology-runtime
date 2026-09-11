"""Offline strict-model tests for the A2 foundation (no database).

Covers the structural/static invariants of
memory_service_runtime.governed.a2_models: strict scalar typing, canonical
UUID / hash shapes, forbidden extra fields, cross-field validators, the golden
composition-manifest vector pinned against the frozen Contract-A validator
(hash fc4d04cf...20 generated offline with the semantica validator), and the
validate_manifest rejection codes.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import a2_models as a2


def u(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def h(n: int) -> str:
    return f"{n:064x}"


GOLDEN_MANIFEST = {
    "manifest_schema_version": "tkos.composition-manifest/0.1",
    "scope_id": u(1), "company_id": u(2), "round_id": u(3), "period_id": u(4),
    "method_profile_ref": {
        "profile_id": "urn:tkos:experimental:method-profile:contract-a",
        "revision": "0.1.0", "canonical_hash": h(9),
    },
    "member_set_version": 1, "input_set_version": 1,
    "company_reference_ref": {"object_id": u(5), "revision_id": u(6), "payload_hash": h(7)},
    "members": [
        {"domain_id": u(10), "dri_assignment_id": u(11), "dri_principal_id": u(12),
         "submission_ref": {"object_id": u(13), "revision_id": u(14), "payload_hash": h(15)}},
        {"domain_id": u(20), "dri_assignment_id": u(21), "dri_principal_id": u(22),
         "submission_ref": {"object_id": u(23), "revision_id": u(24), "payload_hash": h(25)}},
    ],
    "binding_dependencies": [
        {"dependency_id": u(30), "relation_type": "resource_capacity",
         "source_ref": {"object_id": u(31), "revision_id": u(32), "payload_hash": h(33)},
         "constraint": {"kind": "capacity", "resource_id": u(34), "period_id": u(4),
                        "unit": "synthetic_onboarding_slot", "required": 3, "available": 3}},
    ],
    "judgments": {
        k: {"conclusion": "pass", "reason": f"{k} ok", "evidence_refs": [],
            "judge_principal_id": u(40)}
        for k in ("coverage", "coherence", "feasibility", "tradeoff")
    },
    "required_signers": [
        {"principal_id": u(12), "assignment_id": u(11),
         "responsibility_role": "area_accountable"},
        {"principal_id": u(22), "assignment_id": u(21),
         "responsibility_role": "area_accountable"},
        {"principal_id": u(40), "assignment_id": u(41),
         "responsibility_role": "company_decider"},
    ],
    "hash_scheme": "tkos-json-v1",
    "manifest_hash": "fc4d04cfc9161a27c12d13c40020d98908173ce157d2923efcc382ffc087ac20",
}


def _capacity_payload(**overrides):
    payload = {
        "title": "cap", "resource_id": u(30), "unit": "hours",
        "available": 10, "reserved": 3, "period_id": u(4),
        "observed_at": "2026-09-01T00:00:00+00:00",
        "valid_from": "2026-09-01T00:00:00+00:00",
        "valid_to": None,
        "shared_with_domain_ids": [u(10)],
    }
    payload.update(overrides)
    return payload


def _submission_content(**overrides):
    content = {
        "result_statement": "rs",
        "pdo": {"pdo_key": "p1", "statement": "s",
                "result_criteria": [{"criterion_id": "p1c1", "description": "d"}]},
        "missions": [{
            "mission_key": "m1", "result_statement": "r", "boundary": "b",
            "acceptance_criteria": [{"criterion_id": "c1", "description": "d"}],
            "dependency_refs": [],
        }],
        "resources": [], "bindings": [], "upstream_refs": [],
    }
    content.update(overrides)
    return content


# --- golden manifest vector -------------------------------------------------


def test_golden_manifest_validates_and_hash_matches_frozen_validator():
    manifest = a2.CompositionManifest.model_validate(GOLDEN_MANIFEST)
    assert a2.compute_manifest_hash(GOLDEN_MANIFEST) == GOLDEN_MANIFEST["manifest_hash"]
    a2.validate_manifest(manifest)
    assert a2.static_conflict_reasons(manifest) == []


def test_manifest_hash_recomputation_detects_tampering():
    tampered = copy.deepcopy(GOLDEN_MANIFEST)
    tampered["member_set_version"] = 2
    manifest = a2.CompositionManifest.model_validate(tampered)
    with pytest.raises(a2.CompositionValidationError) as excinfo:
        a2.validate_manifest(manifest)
    assert excinfo.value.code == "MANIFEST_HASH_MISMATCH"


def _validated_with(mutation):
    manifest_dict = copy.deepcopy(GOLDEN_MANIFEST)
    mutation(manifest_dict)
    manifest_dict["manifest_hash"] = a2.compute_manifest_hash(manifest_dict)
    return a2.CompositionManifest.model_validate(manifest_dict)


def test_manifest_members_must_be_sorted():
    manifest = _validated_with(lambda d: d["members"].reverse())
    with pytest.raises(a2.CompositionValidationError) as excinfo:
        a2.validate_manifest(manifest)
    assert excinfo.value.code == "ORDERING_VIOLATION"


def test_manifest_duplicate_signer_principal_rejected():
    def mutate(d):
        d["required_signers"][1]["principal_id"] = d["required_signers"][0]["principal_id"]
        d["required_signers"][1]["assignment_id"] = u(99)
    manifest = _validated_with(mutate)
    with pytest.raises(a2.CompositionValidationError) as excinfo:
        a2.validate_manifest(manifest)
    assert excinfo.value.code == "DUPLICATE_SIGNER"


def test_manifest_judge_must_be_company_decider():
    def mutate(d):
        for j in d["judgments"].values():
            j["judge_principal_id"] = u(12)  # a member DRI, not the CEO
    manifest = _validated_with(mutate)
    with pytest.raises(a2.CompositionValidationError) as excinfo:
        a2.validate_manifest(manifest)
    assert excinfo.value.code == "JUDGMENT_JUDGE_MISMATCH"


def test_manifest_duplicate_capacity_pool_rejected():
    def mutate(d):
        dup = copy.deepcopy(d["binding_dependencies"][0])
        dup["dependency_id"] = u(35)  # keeps sort order (u(30) < u(35))
        d["binding_dependencies"].append(dup)
    manifest = _validated_with(mutate)
    with pytest.raises(a2.CompositionValidationError) as excinfo:
        a2.validate_manifest(manifest)
    assert excinfo.value.code == "DUPLICATE_CAPACITY_POOL"


def test_manifest_signer_set_must_match_member_dris():
    def mutate(d):
        # replace one area_accountable signer with an outsider pair
        d["required_signers"][1] = {
            "principal_id": u(28), "assignment_id": u(27),
            "responsibility_role": "area_accountable",
        }
    manifest = _validated_with(mutate)
    with pytest.raises(a2.CompositionValidationError) as excinfo:
        a2.validate_manifest(manifest)
    assert excinfo.value.code == "SIGNER_SET_MISMATCH"


def test_static_conflict_reasons_flags_capacity_shortage_and_non_pass():
    manifest = _validated_with(
        lambda d: d["binding_dependencies"][0]["constraint"].update(required=4))
    reasons = a2.static_conflict_reasons(manifest)
    assert any("deterministic capacity conflict" in r for r in reasons)

    def mutate(d):
        d["judgments"]["feasibility"]["conclusion"] = "unknown"
    manifest = _validated_with(mutate)
    reasons = a2.static_conflict_reasons(manifest)
    assert any("judgment feasibility" in r for r in reasons)


# --- strict scalar / payload behaviour --------------------------------------


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        a2.CompanyReferencePayload.model_validate(
            {"title": "x", "statement": "y", "period_id": u(4), "bogus": 1})


@pytest.mark.parametrize("bad", [True, 1.5, "3", -1])
def test_capacity_counts_are_strict_non_negative_ints(bad):
    with pytest.raises(ValidationError):
        a2.CapacityObservationPayload.model_validate(_capacity_payload(available=bad))


def test_reserved_must_not_exceed_available():
    with pytest.raises(ValidationError):
        a2.CapacityObservationPayload.model_validate(_capacity_payload(reserved=11))


def test_valid_to_must_be_later_than_valid_from():
    with pytest.raises(ValidationError):
        a2.CapacityObservationPayload.model_validate(
            _capacity_payload(valid_to="2026-08-01T00:00:00+00:00"))


def test_uuid_must_be_canonical_lowercase():
    mixed_case = "aaaaaaaa-0000-4000-8000-000000000010".upper()
    with pytest.raises(ValidationError):
        a2.CompanyReferencePayload.model_validate({
            "title": "x", "statement": "y", "period_id": u(4),
            "shared_with_domain_ids": [mixed_case],
        })


def test_shared_with_domain_ids_unique():
    with pytest.raises(ValidationError):
        a2.CapacityObservationPayload.model_validate(
            _capacity_payload(shared_with_domain_ids=[u(10), u(10)]))


def test_submission_accepts_demand_only_or_provider_only_input():
    # Demand-only: a domain declaring capacity it needs (no bindings yet).
    demand_only = a2.SubmissionContent.model_validate(_submission_content(
        resources=[{"resource_id": u(30), "period_id": u(4), "unit": "hours",
                    "required": 2}],
    ))
    assert len(demand_only.resources) == 1 and demand_only.bindings == []
    # Provider-only: a domain contributing capacity (no demand of its own).
    provider_only = a2.SubmissionContent.model_validate(_submission_content(
        bindings=[{"relation_type": "resource_capacity",
                   "source_ref": {"object_id": u(31), "revision_id": u(32),
                                   "payload_hash": h(33)},
                   "resource_id": u(30), "period_id": u(4), "unit": "hours"}],
    ))
    assert len(provider_only.bindings) == 1 and provider_only.resources == []


def test_submission_rejects_duplicate_capacity_pool_in_resources():
    with pytest.raises(ValidationError):
        a2.SubmissionContent.model_validate(_submission_content(
            resources=[
                {"resource_id": u(30), "period_id": u(4), "unit": "hours",
                 "required": 2},
                {"resource_id": u(30), "period_id": u(4), "unit": "hours",
                 "required": 1},
            ],
            bindings=[{"relation_type": "resource_capacity",
                       "source_ref": {"object_id": u(31), "revision_id": u(32),
                                       "payload_hash": h(33)},
                       "resource_id": u(30), "period_id": u(4), "unit": "hours"}],
        ))


def test_submission_rejects_duplicate_capacity_pool_in_bindings():
    src = {"object_id": u(31), "revision_id": u(32), "payload_hash": h(33)}
    with pytest.raises(ValidationError):
        a2.SubmissionContent.model_validate(_submission_content(
            bindings=[
                {"relation_type": "resource_capacity", "source_ref": src,
                 "resource_id": u(30), "period_id": u(4), "unit": "hours"},
                {"relation_type": "resource_capacity", "source_ref": src,
                 "resource_id": u(30), "period_id": u(4), "unit": "hours"},
            ],
        ))


def test_submission_duplicate_mission_key_rejected():
    mission = _submission_content()["missions"][0]
    with pytest.raises(ValidationError):
        a2.SubmissionContent.model_validate(
            _submission_content(missions=[mission, dict(mission)]))


def test_amend_requires_a_real_change():
    with pytest.raises(ValidationError):
        a2.AmendFormationRoundParams.model_validate({"expected_member_set_version": 1})


def test_form_params_judgments_require_all_four_dimensions():
    jd = {"conclusion": "pass", "reason": "r", "evidence_refs": []}
    with pytest.raises(ValidationError):
        a2.FormCompanyCompositionParams.model_validate({
            "expected_member_set_version": 1, "expected_input_set_version": 1,
            "judgments": {"coverage": jd, "coherence": jd, "feasibility": jd},
            "unresolved_conflicts": [],
        })
    params = a2.FormCompanyCompositionParams.model_validate({
        "expected_member_set_version": 1, "expected_input_set_version": 1,
        "judgments": {k: jd for k in ("coverage", "coherence", "feasibility", "tradeoff")},
        "unresolved_conflicts": [{"summary": "s", "blocking": False}],
    })
    assert params.expected_member_set_version == 1


def test_confirm_params_carry_explicit_assignment_and_manifest_pin():
    params = a2.ConfirmCompanyCompositionParams.model_validate({
        "composition_ref": {"object_id": u(50), "revision_id": u(51),
                            "manifest_hash": h(52)},
        "assignment_id": u(41),
        "confirmation_statement": "I confirm",
    })
    assert params.composition_ref.manifest_hash == h(52)
    with pytest.raises(ValidationError):
        a2.ConfirmCompanyCompositionParams.model_validate({
            "composition_ref": {"object_id": u(50), "revision_id": u(51),
                                "manifest_hash": "A" * 64},
            "assignment_id": u(41),
            "confirmation_statement": "I confirm",
        })


def test_open_params_reject_duplicate_member_domain():
    base = {
        "company_id": u(1), "company_domain_id": u(2), "ceo_assignment_id": u(41),
        "period_id": u(4),
        "period_window": {"start": "2026-10-01T00:00:00+00:00",
                          "end": "2026-12-31T23:59:59+00:00"},
        "method_profile_ref": {"profile_id": "tkos.method-a2/1", "revision": "1"},
        "company_reference_ref": {"object_id": u(60), "revision_id": u(61),
                                  "payload_hash": h(60)},
    }
    a2.OpenFormationRoundParams.model_validate({**base, "members": [
        {"domain_id": u(10), "dri_assignment_id": u(11)},
        {"domain_id": u(20), "dri_assignment_id": u(21)},
    ]})
    with pytest.raises(ValidationError):
        a2.OpenFormationRoundParams.model_validate({**base, "members": [
            {"domain_id": u(10), "dri_assignment_id": u(11)},
            {"domain_id": u(10), "dri_assignment_id": u(21)},
        ]})


def test_constants_cover_six_actions_and_seven_types():
    assert a2.A2_ACTIONS == frozenset({
        "open_formation_round", "amend_formation_round",
        "publish_domain_submission", "form_company_composition",
        "confirm_company_composition", "activate_company_composition",
    })
    assert a2.A2_SOURCE_TYPES == frozenset({"CompanyReference", "CapacityObservation"})
    assert a2.A2_COMPOSITION_TYPES == frozenset({
        "FormationRound", "DomainSubmission", "CompanyComposition",
        "Mission", "DomainCommitment",
    })
    assert set(a2.A2_ACTION_PARAMS) == a2.A2_ACTIONS
    assert set(a2.A2_PAYLOAD_MODELS) == a2.A2_OBJECT_TYPES
    assert a2.SOURCE_FRESHNESS_TTL_SECONDS == 86400
    assert (a2.CLOSURE_MAX_NODES, a2.CLOSURE_MAX_DEPTH) == (64, 8)


def test_no_import_of_governed_models():
    # Static guarantee: a2_models stays independent of governed.models (no
    # circular import once models.py wires the A2 params in the next step).
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2]
              / "src/memory_service_runtime/governed/a2_models.py").read_text("utf-8")
    assert "import models" not in source
    assert "from .models" not in source
    assert "from memory_service_runtime.governed.models" not in source
