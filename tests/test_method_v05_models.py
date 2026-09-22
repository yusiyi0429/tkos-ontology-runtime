"""Strict 0.5 schema, registry and dispatch boundaries (no DB required)."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from memory_service_runtime.governed import method_v05_profile as profile

ROOT = Path(__file__).resolve().parents[1]


def test_contract_and_registry_pins_and_profile_validate():
    assert profile.validate(profile.content())
    assert sha256((ROOT / 'docs/contracts/tkos-method-0.5.md').read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    assert sha256((ROOT / 'docs/contracts/ontology-registry-0.7.json').read_bytes()).hexdigest() == profile.ONTOLOGY_REGISTRY_SHA256
    registry = json.loads((ROOT / 'docs/contracts/ontology-registry-0.7.json').read_text())
    assert registry['revision'] == profile.ONTOLOGY_REGISTRY_REVISION
    saved = json.loads((ROOT / 'docs/contracts/method-profile-0.5.json').read_text())
    assert profile.validate(saved).canonical_hash == profile.content()['canonical_hash']


import pytest
from pydantic import ValidationError
from uuid import uuid4

from memory_service_runtime.governed import method_models, method_v04_models as v4, method_v05_models as m, protocol
from memory_service_runtime.governed.models import ActionRequest


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


PERIOD = {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}


def constraint_payload(**updates):
    return {'title': 'Two engineers only', 'applies_to': {'kind': 'scope', 'scope_id': 'bf-1'},
            'architecture_ref': ref(), 'statement': 'Only two engineers are available this period.',
            'constraint_type': 'people', 'effective': PERIOD, 'source': 'Headcount plan',
            'authority': 'Scope DRI', 'severity': 'hard', 'evidence_refs': [], **updates}


def pco_payload(**updates):
    return {'title': 'P', 'primary_scope_id': 'bf-1', 'period': PERIOD, 'parent_ltco_ref': ref(),
            'period_review_ref': None, 'architecture_ref': ref(), 'strategy_ref': ref(),
            'current_reality': 'c', 'result_statement': 'r', 'criteria': ['c'],
            'expected_lt_advance': 'a', 'why': 'w', 'boundary': 'not promised', 'constraint_refs': [], **updates}


def mission_payload(**updates):
    return {'title': 'M', 'owner_principal_id': str(uuid4()), 'primary_scope_id': 'bf-1', 'parent_pco_ref': ref(),
            'why': 'w', 'requirements': ['r'], 'criteria': ['c'], 'evidence_refs': [],
            'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-15T00:00:00Z'},
            'boundary': 'b', 'contributes_to_scope_ids': ['dom-1'],
            'dependencies': [{'kind': 'scope', 'scope_id': 'dom-1', 'needed_by': '2026-10-10T00:00:00Z', 'note': 'n'}],
            'resource_needs': ['one designer'], 'constraint_refs': [], **updates}


def state_payload(**updates):
    subject = ref()
    return {'subject_ref': subject, 'as_of': '2026-10-31T00:00:00Z', 'period': PERIOD, 'summary': 'Evidence gap',
            'rag': 'unknown', 'baseline_refs': [subject], 'evidence_refs': [], 'drilldown_refs': [],
            'data_gaps': ['Awaiting evidence'], 'generation_version': 'test-1', **updates}


def test_registry_matches_generated_json_and_action_target_parity():
    registry = json.loads((ROOT / 'docs/runtime-method-registry-0.5.json').read_text())
    assert set(registry['actions']) == set(m.ACTION_PARAMS) and len(m.ACTION_PARAMS) == 44
    assert set(registry['object_types']) == m.OBJECT_TYPES and len(m.OBJECT_TYPES) == 16
    params, targets, payloads = method_models.registry('tkos.method/0.5')
    assert params is m.ACTION_PARAMS and targets is m.ACTION_TARGETS and payloads is m.PAYLOAD_MODELS
    assert set(m.ACTION_PARAMS) == set(m.ACTION_TARGETS)
    assert 'method_confirm_state' not in m.ACTION_PARAMS and 'method_confirm_state' in v4.ACTION_PARAMS
    assert m.V05_ONLY_ACTIONS == {'m1b_record_constraint', 'm1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'}
    assert m.V05_ONLY_ACTIONS <= m.HUMAN_ACTIONS and 'method_confirm_state' not in m.HUMAN_ACTIONS
    assert ('tkos.method', 'tkos.method/0.5') in protocol.SUPPORTED_PROTOCOL_CONTRACTS


def test_constraint_scope_is_exactly_one_of_company_scope_mission():
    assert m.ConstraintPayload.model_validate(constraint_payload()).applies_to.scope_id == 'bf-1'
    company = constraint_payload(applies_to={'kind': 'company'}, architecture_ref=None)
    assert m.ConstraintPayload.model_validate(company).architecture_ref is None
    with pytest.raises(ValidationError):  # scope without unit id
        m.ConstraintPayload.model_validate(constraint_payload(applies_to={'kind': 'scope'}))
    with pytest.raises(ValidationError):  # scope without the Architecture it belongs to
        m.ConstraintPayload.model_validate(constraint_payload(architecture_ref=None))
    with pytest.raises(ValidationError):  # company constraint must not carry a scope id
        m.ConstraintPayload.model_validate(constraint_payload(applies_to={'kind': 'company', 'scope_id': 'bf-1'}))
    mission = constraint_payload(applies_to={'kind': 'mission', 'mission_ref': ref()}, architecture_ref=None)
    assert m.ConstraintPayload.model_validate(mission).applies_to.kind == 'mission'


def test_ltco_confirmation_carries_a_conclusion_and_0_4_never_widens():
    assert m.ConfirmLTCO.model_validate({'conclusion': 'maintained', 'statement': 'Still valid'}).conclusion == 'maintained'
    with pytest.raises(ValidationError):
        m.ConfirmLTCO.model_validate({'statement': 'no conclusion'})
    with pytest.raises(ValidationError):
        v4.ConfirmLTCO.model_validate({'conclusion': 'maintained', 'statement': 'Still valid'})
    ltco = {'title': 'L', 'primary_scope_id': 'bf-1', 'period': PERIOD, 'architecture_ref': ref(), 'strategy_ref': ref(),
            'result_statement': 'r', 'criteria': ['c'], 'boundary': 'b', 'horizon': 'rolling 6 months', 'why': 'w',
            'baseline_refs': [ref()], 'realization_logic': 'logic', 'key_assumptions': ['a'], 'constraint_refs': []}
    assert m.LTCOPayload.model_validate(ltco).realization_logic == 'logic'
    with pytest.raises(ValidationError):
        v4.LTCOPayload.model_validate(ltco)


def test_pco_and_mission_keep_the_0_5_shape():
    assert m.PCOPayload.model_validate(pco_payload()).boundary == 'not promised'
    with pytest.raises(ValidationError):  # boundary is required in 0.5
        m.PCOPayload.model_validate({k: v for k, v in pco_payload().items() if k != 'boundary'})
    mission = m.MissionPayload.model_validate(mission_payload())
    assert mission.dependencies[0].scope_id == 'dom-1'
    with pytest.raises(ValidationError):  # a scope dependency never carries a mission ref
        m.MissionPayload.model_validate(mission_payload(dependencies=[
            {'kind': 'scope', 'scope_id': 'dom-1', 'mission_ref': ref(), 'needed_by': '2026-10-10T00:00:00Z', 'note': 'n'}]))
    with pytest.raises(ValidationError):  # duplicate contribution
        m.MissionPayload.model_validate(mission_payload(contributes_to_scope_ids=['dom-1', 'dom-1']))
    resolve = m.ResolveWindow.model_validate({
        'title': 'C', 'pcos': [{'object_id': str(uuid4()), 'payload': pco_payload()}],
        'missions': [{'object_id': str(uuid4()), **{k: v for k, v in mission_payload().items() if k != 'parent_pco_ref'}}],
        'dispositions': [], 'unresolved_differences': [], 'summary': 's'})
    assert resolve.missions[0].resource_needs == ['one designer']


def test_state_period_and_as_of_agree_and_confirmation_is_gone():
    assert m.StatePayload.model_validate(state_payload()).period.end == PERIOD['end']
    with pytest.raises(ValidationError):
        m.StatePayload.model_validate(state_payload(as_of='2026-10-15T00:00:00Z'))
    with pytest.raises(ValidationError):  # known rating without evidence is still forbidden
        m.StatePayload.model_validate(state_payload(rag='green', data_gaps=[]))
    with pytest.raises(ValidationError):  # 0.4 model never widens
        v4.StatePayload.model_validate(state_payload())
    assert m.ConfirmReview.model_validate({'statement': 'Confirmed', 'findings': ['f']}).learnings is None


def test_0_5_targetless_action_enforces_its_own_null_target_shape():
    body = {'action_type': 'm1b_record_constraint', 'target': None, 'expected_versions': [],
            'idempotency_key': 'v05-unit-dispatch-0001', 'reason': 'Dispatch boundary check',
            'contract_version': 'tkos.method/0.5',
            'params': {'domain_id': str(uuid4()), 'payload': constraint_payload()}}
    accepted = ActionRequest.model_validate(body)
    assert accepted.params.payload.applies_to.kind == 'scope'
    with pytest.raises(ValidationError):  # targetless 0.5 action with a target
        ActionRequest.model_validate({**body, 'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()),
                                                         'expected_version': 1}})


def test_unrecognized_action_with_null_target_hits_the_legacy_rule_not_a_version_check():
    """Not a version-freezing check.  ``m1b_record_constraint`` is not a
    recognized action name under 0.1-0.4 at all, so none of envelope_rules's
    version-specific branches match it there; it falls through to the legacy
    rule that a null target is only legal for create_object/revoke_assignment
    (models.py's final ``elif``).  That rule rejects *any* unclaimed action
    name carrying a null target — it says nothing about which contract
    versions accept or reject the action itself.
    """
    body = {'action_type': 'm1b_record_constraint', 'target': None, 'expected_versions': [],
            'idempotency_key': 'v05-unit-dispatch-0003', 'reason': 'Null-target legacy fallback check',
            'params': {'domain_id': str(uuid4()), 'payload': constraint_payload()}}
    for old in ('tkos.method/0.1', 'tkos.method/0.2', 'tkos.method/0.3', 'tkos.method/0.4'):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body, 'contract_version': old})


def test_confirm_ltco_and_confirm_state_pin_the_declared_version_binding():
    """Pins two real, version-gated envelope facts: ``m1b_confirm_ltco`` is a
    shared action name that ``select_params`` binds to a different,
    version-specific model depending on the declared contract_version, and
    ``method_confirm_state`` exists in 0.4's ACTION_TARGETS but not 0.5's
    (0.5 drops human state confirmation; see method_v05_models.HUMAN_ACTIONS).
    """
    target = {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'expected_version': 1}
    envelope = {'action_type': 'm1b_confirm_ltco', 'target': target, 'expected_versions': [],
                'idempotency_key': 'v05-unit-pin-confirm-ltco', 'reason': 'Pin the shared-name dispatch'}
    under_04 = ActionRequest.model_validate({**envelope, 'contract_version': 'tkos.method/0.4',
                                              'params': {'statement': 'Reaffirmed for this period.'}})
    assert type(under_04.params) is v4.ConfirmLTCO
    under_05 = ActionRequest.model_validate({**envelope, 'contract_version': 'tkos.method/0.5',
                                              'params': {'conclusion': 'maintained',
                                                         'statement': 'Reaffirmed for this period.'}})
    assert type(under_05.params) is m.ConfirmLTCO
    with pytest.raises(ValidationError):  # 0.5's conclusion is genuinely required, not incidentally accepted
        ActionRequest.model_validate({**envelope, 'contract_version': 'tkos.method/0.5',
                                      'params': {'statement': 'Missing conclusion'}})

    confirm_state = {'action_type': 'method_confirm_state', 'target': target, 'expected_versions': [],
                     'idempotency_key': 'v05-unit-pin-confirm-state', 'reason': 'Pin the 0.4-only action',
                     'params': {'reason': 'Reviewed and reaffirmed'}}
    accepted = ActionRequest.model_validate({**confirm_state, 'contract_version': 'tkos.method/0.4'})
    assert type(accepted.params) is v4.ConfirmState
    with pytest.raises(ValidationError):
        ActionRequest.model_validate({**confirm_state, 'contract_version': 'tkos.method/0.5'})


def test_envelope_does_not_gate_a_0_5_only_action_under_an_older_declared_version():
    """Documents a real, current limit of the request envelope, not a bug: a
    0.5-only *targeted* action (unlike the targetless one above) is not
    rejected just because an older contract_version was declared, when that
    action name is unclaimed by every version-specific branch in
    ``select_params``/``envelope_rules``.  ``select_params`` falls through to
    the outer ``params: ActionParams`` union, which now includes 0.5's models,
    so the shape still resolves to ``method_v05_models.ReviseConstraint``.

    This is not where version isolation lives.  protocol.py's module
    docstring states that a request's declared ``contract_version`` "only
    declares which action format the client understands" — the authoritative
    version is the *object's binding*, enforced by protocol.py's frozen
    rejection matrix via ``protocol._declared_mismatch``.  Task 4 proves end
    to end that an object bound to tkos.method/0.4 cannot execute a 0.5-only
    action, with authentication/authorization checked before any protocol
    error is revealed.
    """
    accepted = ActionRequest.model_validate({
        'action_type': 'm1b_revise_constraint',
        'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'expected_version': 1},
        'expected_versions': [], 'idempotency_key': 'v05-unit-envelope-gap-0001',
        'reason': 'Documents the envelope gap, not a bug', 'contract_version': 'tkos.method/0.4',
        'params': {'payload': constraint_payload()}})
    assert type(accepted.params) is m.ReviseConstraint
