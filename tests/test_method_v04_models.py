"""Strict 0.4 schema, registry and dispatch boundaries (no DB required)."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import (
    method_models, method_readers, method_v04_models as m, method_v04_profile as profile,
    protocol, workbench,
)
from memory_service_runtime.governed.models import ActionRequest


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


def capability(capability_id='cap-1', domain='scope-1'):
    return {'capability_id': capability_id, 'name': 'Capability', 'definition': 'Definition',
            'primary_domain_id': domain, 'analysis_fields': ['field']}


def test_contract_pin_and_profile_validate():
    root = Path(__file__).resolve().parents[1]
    assert profile.validate(profile.content())
    from hashlib import sha256
    assert sha256((root / 'docs/contracts/tkos-method-0.4.md').read_bytes()).hexdigest() == profile.CONTRACT_SHA256
    assert 'transferred' not in m.CloseProblem.model_fields['disposition'].annotation.__args__


def test_registry_matches_generated_json_and_action_target_parity():
    root = Path(__file__).resolve().parents[1]
    registry = json.loads((root / 'docs/runtime-method-registry-0.4.json').read_text())
    assert set(registry['actions']) == set(m.ACTION_PARAMS)
    assert set(registry['object_types']) == set(m.PAYLOAD_MODELS) | {'EvidenceAsset'}
    params, targets, payloads = method_models.registry('tkos.method/0.4')
    assert params is m.ACTION_PARAMS and targets is m.ACTION_TARGETS and payloads is m.PAYLOAD_MODELS
    assert set(m.ACTION_PARAMS) == set(m.ACTION_TARGETS)
    for action in ('m1a_create_issue', 'm1a_confirm_agreement', 'm1a_confirm_update',
                   'm1b_commit_candidate', 'm1b_activate_candidates', 'm1b_reopen_candidates',
                   'm1a_transfer_problem', 'method_propose_state', 'm1b_generate_review'):
        assert action in m.ACTION_PARAMS
    assert ('tkos.method', 'tkos.method/0.4') in protocol.SUPPORTED_PROTOCOL_CONTRACTS


def test_required_capabilities_may_share_one_domain_but_ids_stay_unique():
    payload = {'title': 'Strategy', 'statement': 'Statement',
               'required_capabilities': [capability('cap-1', 'scope-1'), capability('cap-2', 'scope-1')]}
    assert len(m.StrategyPayload.model_validate(payload).required_capabilities) == 2
    with pytest.raises(ValidationError):
        m.StrategyPayload.model_validate({'title': 'S', 'statement': 'S',
                                          'required_capabilities': [capability('cap-1'), capability('cap-1')]})


def test_battlefield_and_domain_are_both_primary_scopes_with_explicit_mapping():
    auth = str(uuid4())
    battlefield = {'unit_id': 'bf-1', 'name': 'Battlefield', 'definition': 'Value field',
                   'strategic_basis': ['basis'], 'boundary': 'boundary',
                   'auth_domain_id': auth, 'current_dri_principal_id': str(uuid4())}
    domain = {'unit_id': 'dom-1', 'name': 'Domain', 'definition': 'Capability responsibility',
              'responsibility': 'Own it', 'auth_domain_id': str(uuid4())}
    architecture = m.StrategicArchitecturePayload.model_validate(
        {'title': 'Architecture', 'battlefields': [battlefield], 'domains': [domain]})
    assert architecture.battlefields[0].auth_domain_id is not None
    unit_ids = [u.unit_id for u in architecture.battlefields] + [u.unit_id for u in architecture.domains]
    assert len(unit_ids) == len(set(unit_ids))
    with pytest.raises(ValidationError):  # duplicate stable ID across both lists
        m.StrategicArchitecturePayload.model_validate(
            {'title': 'A', 'battlefields': [battlefield],
             'domains': [{**domain, 'unit_id': 'bf-1'}]})


def test_company_change_requires_explicit_retention_and_no_client_provenance():
    retained = ref()
    change = {'strategy_target_ref': retained, 'architecture_target_ref': None,
              'strategy': None,
              'architecture': {'title': 'A', 'battlefields': [
                  {'unit_id': 'bf', 'name': 'B', 'definition': 'D', 'strategic_basis': ['b'], 'boundary': 'x'}],
                  'domains': [{'unit_id': 'dom', 'name': 'D', 'definition': 'D', 'responsibility': 'R',
                               'auth_domain_id': str(uuid4())}]},
              'applicability_rationale': 'Strategy retains its exact revision.'}
    with pytest.raises(ValidationError):  # unchanged Strategy without its retained revision
        m.CompanyChange.model_validate({**change, 'strategy_target_ref': None})
    with pytest.raises(ValidationError):  # nothing changed
        m.CompanyChange.model_validate({**change, 'architecture': None, 'architecture_target_ref': retained})
    with pytest.raises(ValidationError):  # client-supplied Architecture Strategy binding
        m.CompanyChange.model_validate({**change, 'architecture': {**change['architecture'], 'strategy_ref': ref()}})
    assert m.CompanyChange.model_validate(change).applicability_rationale


def test_window_freezes_complete_ltco_set_and_unique_members():
    payload = {'title': 'W', 'period': {'start': '2026-01-01T00:00:00Z', 'end': '2026-02-01T00:00:00Z'},
               'architecture_ref': ref(), 'strategy_ref': ref(), 'ltco_refs': [ref(), ref()],
               'pco_refs': [ref()], 'mission_refs': [ref()],
               'participants': [{'principal_id': str(uuid4()), 'assignment_id': str(uuid4())}]}
    assert len(m.ReviewWindowPayload.model_validate(payload).ltco_refs) == 2
    with pytest.raises(ValidationError):  # duplicate LTCO identity
        m.ReviewWindowPayload.model_validate({**payload, 'ltco_refs': [payload['ltco_refs'][0], payload['ltco_refs'][0]]})
    with pytest.raises(ValidationError):  # duplicate PCO member
        m.ReviewWindowPayload.model_validate({**payload, 'pco_refs': [payload['pco_refs'][0], payload['pco_refs'][0]]})


def test_state_and_period_review_exactness_rules():
    subject = ref()
    with pytest.raises(ValidationError):
        m.StatePayload.model_validate({'subject_ref': subject, 'as_of': '2026-09-17T00:00:00Z',
                                       'summary': 's', 'rag': 'green', 'baseline_refs': [subject],
                                       'evidence_refs': [], 'data_gaps': [], 'generation_version': '1'})
    review = m.PeriodReviewPayload.model_validate({
        'review_id': 'r', 'title': 'R', 'period': {'start': '2026-09-01T00:00:00Z', 'end': '2026-09-30T00:00:00Z'},
        'target_refs': [subject], 'state_refs': [ref()], 'fact_refs': [ref()],
        'findings': ['f'], 'generation_version': '1'})
    assert review.fact_refs  # schema keeps the explicit field; the handler rejects unsupported facts


def test_resolution_dispositions_are_unique():
    pco_payload = {'title': 'P', 'primary_scope_id': 's',
                   'period': {'start': '2026-01-01T00:00:00Z', 'end': '2026-02-01T00:00:00Z'},
                   'parent_ltco_ref': ref(), 'architecture_ref': ref(), 'strategy_ref': ref(),
                   'current_reality': 'c', 'result_statement': 'r', 'criteria': ['c'],
                   'expected_lt_advance': 'a', 'why': 'w'}
    disposition = {'review_record_id': str(uuid4()), 'decision': 'adopted', 'rationale': 'a'}
    with pytest.raises(ValidationError):
        m.ResolveWindow.model_validate({
            'title': 'C', 'pcos': [{'object_id': str(uuid4()), 'payload': pco_payload}],
            'missions': [{'object_id': str(uuid4()), 'title': 'M', 'owner_principal_id': str(uuid4()),
                          'primary_scope_id': 's', 'why': 'w', 'requirements': ['r'], 'criteria': ['c'],
                          'evidence_refs': [],
                          'period': {'start': '2026-01-01T00:00:00Z', 'end': '2026-01-15T00:00:00Z'}}],
            'dispositions': [disposition, dict(disposition)],
            'unresolved_differences': [], 'summary': 's'})


def test_action_request_dispatches_v04_and_keeps_older_contracts_frozen():
    body = {'action_type': 'm1a_create_issue', 'target': None, 'expected_versions': [],
            'idempotency_key': 'v04-unit-dispatch-0001', 'reason': 'Dispatch boundary check',
            'contract_version': 'tkos.method/0.4',
            'params': {'domain_id': str(uuid4()),
                       'payload': {'title': 't', 'summary': 's', 'core_question': 'q',
                                   'business_scope': 'strategic', 'urgency': 'green', 'source_refs': [ref()]}}}
    accepted = ActionRequest.model_validate(body)
    assert accepted.params.payload.title == 't'
    for old in ('tkos.method/0.1', 'tkos.method/0.2', 'tkos.method/0.3'):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body, 'contract_version': old})
    with pytest.raises(ValidationError):  # targetless v0.4 action with a target
        ActionRequest.model_validate({**body, 'target': {'object_id': str(uuid4()), 'revision_id': str(uuid4()),
                                                         'expected_version': 1}})


def test_receipts_and_read_support_recognize_v04():
    assert method_readers.is_receipt({'action_type': 'm1a_confirm_update'})
    result = workbench.object_types(None, None, 'tkos.method/0.4')
    assert result['contract_version'] == 'tkos.method/0.4'
    assert any(item['object_type'] == 'StrategicArchitecture' for item in result['items'])
