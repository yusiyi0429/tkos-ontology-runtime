"""0.5 read projections: review effects, company view grouping, confirmations (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import method_readers
from memory_service_runtime.governed.errors import GovernedError


def test_every_0_4_and_0_5_review_kind_has_one_effect():
    kinds = {'agent_issue_initiation', 'agreement_confirmation', 'agreement_formalized', 'candidate_set_activation',
             'ceo_reopen', 'issue_association', 'issue_participants', 'issue_reframe', 'ltco_confirmation',
             'ltco_revision_response', 'personal_agent_analysis', 'problem_closure', 'problem_transfer',
             'state_confirmation', 'strategy_update_confirmation', 'strategy_update_impact_review', 'window_closed',
             'window_comment', 'window_opinion_withdrawal', 'window_resolution',
             'review_confirmation', 'constraint_confirmation'}
    effects = {kind: method_readers.review_effect(kind) for kind in kinds}
    assert set(effects.values()) == {'decision', 'opinion', 'analysis', 'record'}
    assert effects['ltco_confirmation'] == 'decision' and effects['constraint_confirmation'] == 'decision'
    assert effects['review_confirmation'] == 'decision' and effects['candidate_set_activation'] == 'decision'
    assert effects['window_comment'] == 'opinion' and effects['personal_agent_analysis'] == 'analysis'
    assert effects['window_closed'] == 'record' and method_readers.review_effect('unknown_kind') == 'record'


def test_company_view_groups_effective_objects_by_primary_scope(monkeypatch):
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    old_pco = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-08-01T00:00:00+00:00', 'end': '2026-08-31T00:00:00+00:00'}}}
    mission = {'object_id': str(uuid4()), 'object_type': 'Mission', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-15T00:00:00+00:00'}}}
    company = {'object_id': str(uuid4()), 'object_type': 'Constraint', 'payload': {'applies_to': {'kind': 'company'}, 'effective': period}}
    scoped = {'object_id': str(uuid4()), 'object_type': 'Constraint', 'payload': {'applies_to': {'kind': 'scope', 'scope_id': 'bf-1'}, 'effective': period}}
    rows = [ltco, pco, old_pco, mission, company, scoped]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    # T10-b: object_state now must carry a 0.5 protocol tag and a readable
    # effective_revision_id for company_view to place the object at all.
    monkeypatch.setattr(method_readers, 'object_state',
                        lambda conn, ctx, oid: {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                                                'protocol': {'contract_version': 'tkos.method/0.5'},
                                                'effective_revision_id': 'rev-eff-1'})
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    assert [s['scope_id'] for s in view['scopes']] == ['bf-1']
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert [p['object_id'] for p in scope['pcos']] == [pco['object_id']]
    assert [m['object_id'] for m in scope['missions']] == [mission['object_id']]
    assert [c['object_id'] for c in scope['constraints']] == [scoped['object_id']]
    assert [c['object_id'] for c in view['company_constraints']] == [company['object_id']]
    assert view['period'] == period and view['schema_version'] == 'method-read/0.5'


def test_company_view_excludes_an_object_bound_to_a_different_contract_version(monkeypatch):
    # T10-b(i): a 0.4-bound row must not be presented under 0.5 read semantics,
    # even though it otherwise matches the primary Scope and period.
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco_04 = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    rows = [ltco, pco_04]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)

    def fake_object_state(conn, ctx, oid):
        version = 'tkos.method/0.4' if oid == pco_04['object_id'] else 'tkos.method/0.5'
        return {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                'protocol': {'contract_version': version}, 'effective_revision_id': 'rev-eff-1'}

    monkeypatch.setattr(method_readers, 'object_state', fake_object_state)
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert scope['pcos'] == []


def test_company_view_excludes_an_object_without_a_readable_effective_revision(monkeypatch):
    # T10-b(ii): object_state blanks effective_revision_id to None when the
    # caller cannot read it; company_view must not place such an object either.
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco_hidden = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    rows = [ltco, pco_hidden]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)

    def fake_object_state(conn, ctx, oid):
        rid = None if oid == pco_hidden['object_id'] else 'rev-eff-1'
        return {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                'protocol': {'contract_version': 'tkos.method/0.5'}, 'effective_revision_id': rid}

    monkeypatch.setattr(method_readers, 'object_state', fake_object_state)
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert scope['pcos'] == []


def _reviews_and_commitments_conn(reviews, commitments):
    def execute(sql, params=None):
        if 'gov_method_reviews' in sql:
            return SimpleNamespace(fetchall=lambda: reviews)
        if 'gov_method_commitments' in sql:
            return SimpleNamespace(fetchall=lambda: commitments)
        raise AssertionError(f'unexpected query in confirmations(): {sql}')
    return SimpleNamespace(execute=execute)


def _commitment_row(*, responsibility_object_id):
    return {'commitment_id': str(uuid4()), 'candidate_object_id': str(uuid4()),
            'candidate_revision_id': str(uuid4()), 'responsibility_object_id': responsibility_object_id,
            'responsibility_revision_id': str(uuid4()), 'principal_id': str(uuid4()), 'assignment_id': str(uuid4()),
            'statement': 'DRI commits.', 'recorded_at': '2026-10-01T00:00:00+00:00'}


def test_confirmations_excludes_a_commitment_whose_referenced_revision_is_not_visible(monkeypatch):
    # T10-c: each commitment row names another PCO/Mission and another
    # person's assignment; it must be dropped whenever EITHER side (the
    # responsibility revision or the candidate revision) is not currently
    # visible to the caller, not only when the requested object itself is.
    object_id = str(uuid4())
    visible_commitment = _commitment_row(responsibility_object_id=object_id)
    hidden_by_responsibility = _commitment_row(responsibility_object_id=object_id)
    hidden_by_candidate = _commitment_row(responsibility_object_id=object_id)
    conn = _reviews_and_commitments_conn([], [visible_commitment, hidden_by_responsibility, hidden_by_candidate])
    monkeypatch.setattr(method_readers.access, 'head', lambda conn, ctx, oid: {'object_id': oid})

    def fake_revision(conn, ctx, oid, rid):
        if rid == hidden_by_responsibility['responsibility_revision_id']:
            raise GovernedError('FORBIDDEN')
        if rid == hidden_by_candidate['candidate_revision_id']:
            raise GovernedError('NOT_FOUND')
        return {'object_id': oid, 'revision_id': rid}

    monkeypatch.setattr(method_readers.access, 'revision', fake_revision)
    result = method_readers.confirmations(conn, SimpleNamespace(scope_id=str(uuid4())), object_id)
    assert [c['commitment_id'] for c in result['commitments']] == [visible_commitment['commitment_id']]


def test_confirmations_review_row_visibility_check_propagates_unexpected_error(monkeypatch):
    # T10-c: only NOT_FOUND/FORBIDDEN are a plain visibility filter; any other
    # GovernedError (e.g. from a protocol/read-support check) must surface,
    # not be silently swallowed like the brief's bare `except GovernedError`.
    object_id = str(uuid4())
    review_row = {'record_id': str(uuid4()), 'kind': 'ltco_confirmation', 'target_object_id': object_id,
                  'target_revision_id': str(uuid4()), 'window_id': None, 'principal_id': str(uuid4()),
                  'content': {}, 'action_id': str(uuid4()), 'recorded_at': '2026-10-01T00:00:00+00:00'}
    conn = _reviews_and_commitments_conn([review_row], [])
    monkeypatch.setattr(method_readers.access, 'head', lambda conn, ctx, oid: {'object_id': oid})

    def explode(conn, ctx, oid, rid):
        raise GovernedError('PROTOCOL_NOT_SUPPORTED')

    monkeypatch.setattr(method_readers.access, 'revision', explode)
    with pytest.raises(GovernedError) as excinfo:
        method_readers.confirmations(conn, SimpleNamespace(scope_id=str(uuid4())), object_id)
    assert excinfo.value.code == 'PROTOCOL_NOT_SUPPORTED'


def test_confirmations_commitment_row_visibility_check_propagates_unexpected_error(monkeypatch):
    object_id = str(uuid4())
    commitment_row = _commitment_row(responsibility_object_id=object_id)
    conn = _reviews_and_commitments_conn([], [commitment_row])
    monkeypatch.setattr(method_readers.access, 'head', lambda conn, ctx, oid: {'object_id': oid})

    def explode(conn, ctx, oid, rid):
        raise GovernedError('PROTOCOL_NOT_SUPPORTED')

    monkeypatch.setattr(method_readers.access, 'revision', explode)
    with pytest.raises(GovernedError) as excinfo:
        method_readers.confirmations(conn, SimpleNamespace(scope_id=str(uuid4())), object_id)
    assert excinfo.value.code == 'PROTOCOL_NOT_SUPPORTED'
