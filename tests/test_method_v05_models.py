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


def test_action_request_dispatches_v05_and_keeps_older_contracts_frozen():
    body = {'action_type': 'm1b_record_constraint', 'target': None, 'expected_versions': [],
            'idempotency_key': 'v05-unit-dispatch-0001', 'reason': 'Dispatch boundary check',
            'contract_version': 'tkos.method/0.5',
            'params': {'domain_id': str(uuid4()), 'payload': constraint_payload()}}
    accepted = ActionRequest.model_validate(body)
    assert accepted.params.payload.applies_to.kind == 'scope'
    for old in ('tkos.method/0.1', 'tkos.method/0.2', 'tkos.method/0.3', 'tkos.method/0.4'):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body, 'contract_version': old})
    with pytest.raises(ValidationError):  # 0.5 has no state confirmation
        ActionRequest.model_validate({**body, 'action_type': 'method_confirm_state', 'params': {'reason': 'x' * 8},
                                      'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'expected_version': 1}})
    with pytest.raises(ValidationError):  # targetless 0.5 action with a target
        ActionRequest.model_validate({**body, 'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()),
                                                         'expected_version': 1}})
