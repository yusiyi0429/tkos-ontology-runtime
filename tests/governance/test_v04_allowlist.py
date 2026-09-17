"""B5: version-specific human allowlists in the governance session facade.

The facade may only accept the human actions the implemented contract names;
Agent-only drafting, scene Agent runs and unlisted versions stay out.  Core
prepare/commit still re-validates authority, so this layer narrows, never widens.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_app import governance_commands as journal
from memory_service_runtime.governed import governance
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.method_v04_models import HUMAN_ACTIONS as V04_HUMAN
from memory_service_runtime.governed.workspace_v02_models import SourceSceneCommand


def method_body(action, contract='tkos.method/0.4', params=None, target=True):
    body = {'action_type': action, 'contract_version': contract,
            'expected_versions': [], 'idempotency_key': 'x' * 32,
            'reason': 'Human decision recorded'}
    if target:
        body['target'] = {'object_id': str(uuid4()), 'revision_id': str(uuid4()),
                          'expected_version': 1}
    body['params'] = params if params is not None else {}
    return body


def test_v04_human_actions_are_accepted_only_with_the_04_contract():
    model, kind = journal.parse(method_body('m1a_confirm_agreement',
                                            params={'statement': 'Agreed'}))
    assert kind == 'method' and model.contract_version == 'tkos.method/0.4'
    # The same action under 0.3 is not a 0.3 human action.
    with pytest.raises(GovernedError) as error:
        journal.parse(method_body('m1a_confirm_agreement',
                                  contract='tkos.method/0.3', params={'statement': 'Agreed'}))
    assert error.value.code == 'FORBIDDEN'


def test_agent_only_v04_actions_are_rejected_by_the_facade():
    for action, params in [('m1a_draft_agreement', {}),
                           ('m1a_propose_update', {}),
                           ('m1b_draft_mission', {})]:
        assert action not in V04_HUMAN
        with pytest.raises(GovernedError) as error:
            journal.parse(method_body(action, params=params))
        assert error.value.code == 'FORBIDDEN'


def test_03_human_actions_are_not_reinterpreted_as_04():
    # m1b_confirm_candidates is a 0.3 window action; method_v04 has a different
    # activation action, so the facade must not accept the old name as 0.4.
    assert 'm1b_confirm_candidates' not in V04_HUMAN
    with pytest.raises(GovernedError) as error:
        journal.parse(method_body('m1b_confirm_candidates', params={'reason': 'Confirm'}))
    assert error.value.code == 'FORBIDDEN'


def test_v02_human_events_accepted_and_agent_run_rejected():
    scene_id = str(uuid4())
    command = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
               'expected_version': 0, 'idempotency_key': 'y' * 32,
               'event': {'kind': 'scene_create', 'scene_type': 'meeting',
                         'external_id': 'meeting-1', 'title': 'Standalone meeting',
                         'owner_principal_id': str(uuid4()),
                         'participant_principal_ids': [], 'agent_bindings': []}}
    model, kind = journal.parse(command)
    assert kind == 'scene_v02' and isinstance(model, SourceSceneCommand)

    run = {**command, 'expected_version': 1,
           'event': {'kind': 'agent_run', 'agent_principal_id': str(uuid4()),
                     'purpose': 'Summarize', 'model': {'model_id': 'x', 'revision': '1'},
                     'input_refs': [{'source_id': str(uuid4()), 'version_event_id': str(uuid4()),
                                     'payload_hash': 'a' * 64}],
                     'context_id': str(uuid4()), 'status': 'unknown',
                     'started_at': '2026-09-17T00:00:00Z'}}
    with pytest.raises(GovernedError) as error:
        journal.parse(run)
    assert error.value.code == 'FORBIDDEN'


def test_unknown_versions_and_shapes_fail_closed():
    with pytest.raises(GovernedError) as error:
        journal.parse({'contract_version': 'tkos.method/9.9', 'action_type': 'anything'})
    assert error.value.code in {'FORBIDDEN', 'INVALID_REQUEST'}


@pytest.fixture
def v04_projection(monkeypatch):
    from memory_service_runtime.governed import method_v04
    monkeypatch.setattr(method_v04, 'scoped_assignment', lambda *a, **k: {'assignment_id': 'a'})
    monkeypatch.setattr(method_v04, '_state_subject_owner_static', lambda *a, **k: 'p1')
    monkeypatch.setattr(governance.db, '_assignments', lambda *a: [{'assignment_id': 'a'}])


def _human(*, roles=(), domain='d', principal='p1'):
    return SimpleNamespace(principal_type='human', principal_id=principal,
                           assignments=[{'role': role, 'domain_id': domain, 'assignment_id': 'a'}
                                        for role in roles])


def test_method_object_actions_projection_is_allowlisted_and_version_scoped(v04_projection):
    agreement = {'object_id': str(uuid4()), 'object_type': 'StrategicAgreement',
                 'object_version': 3, 'domain_id': 'd',
                 'latest_revision': {'revision_id': str(uuid4()), 'payload_hash': 'b' * 64},
                 'protocol': {'contract_version': 'tkos.method/0.4'},
                 'method_state': {'phase': 'awaiting_confirmation'}}
    human = _human(roles=('CEO',))
    result = governance.method_object_actions(None, human, agreement)
    offered = {item['action_type']: item for item in result['items']}
    assert 'm1a_confirm_agreement' in offered
    assert offered['m1a_confirm_agreement']['allowed'] is True
    assert offered['m1a_confirm_agreement']['label']
    assert offered['m1a_confirm_agreement']['formal_effect'] == 'agreement_confirmation_record'
    assert offered['m1a_confirm_agreement']['target']['expected_version'] == 3
    # Agent identities never see an allowed human action.
    agent = SimpleNamespace(principal_type='agent', principal_id='g1', assignments=[])
    agent_result = governance.method_object_actions(None, agent, agreement)
    assert all(item['allowed'] is False and item['reason'] == 'human_identity_required'
               for item in agent_result['items'])
    # A 0.3-bound object is not served the 0.4 projection.
    legacy = {**agreement, 'protocol': {'contract_version': 'tkos.method/0.3'}}
    assert governance.method_object_actions(None, human, legacy) == {'items': []}
    # No allowlisted 0.4 human action targets an unrelated type.
    unrelated = {**agreement, 'object_type': 'PeriodReview'}
    assert governance.method_object_actions(None, human, unrelated)['items'] == []


def test_every_04_human_action_has_a_label_and_formal_effect():
    for action in V04_HUMAN:
        assert governance.HUMAN_ACTION_LABELS.get(action), action
        assert governance.FORMAL_EFFECT.get(action), action


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return _Rows(self._rows)


def _ctx():
    return SimpleNamespace(scope_id=str(uuid4()), principal_type='human',
                           principal_id=str(uuid4()))


def test_tasks_projection_handles_03_windows_without_unpack_regression(monkeypatch):
    """Regression: the 0.3 branch unpacked a chained assignment and 500'd live."""
    window_id = str(uuid4())
    value = {
        'object': {'object_id': window_id, 'object_type': 'ReviewWindow',
                   'method_state': {'phase': 'open'},
                   'latest_revision': {'payload': {'title': 'Window'}},
                   'protocol': {'contract_version': 'tkos.method/0.3'}},
        'monthly': {'my_reviews': [], 'candidate': {'status': 'unavailable'}},
        'actions': [{'action_type': 'm1b_comment', 'allowed': True}],
        'scenes': [],
    }
    monkeypatch.setattr(governance, 'window', lambda *_: value)
    result = governance.tasks(_FakeConn([{'object_id': window_id}]), _ctx())
    assert [item['object_id'] for item in result['items']] == [window_id]
    assert result['items'][0]['label'] == '参与共同核对'


def test_tasks_projection_handles_04_windows_without_monthly(monkeypatch):
    window_id = str(uuid4())
    value = {
        'object': {'object_id': window_id, 'object_type': 'ReviewWindow',
                   'method_state': {'phase': 'pending'},
                   'latest_revision': {'payload': {'title': '0.4 window'}},
                   'protocol': {'contract_version': 'tkos.method/0.4'}},
        'monthly': None,
        'actions': [{'action_type': 'm1b_commit_candidate', 'allowed': True}],
        'scenes': [],
    }
    monkeypatch.setattr(governance, 'window', lambda *_: value)
    result = governance.tasks(_FakeConn([{'object_id': window_id}]), _ctx())
    assert result['items'][0]['label'] == '0.4 人工确认事项'


def test_v02_context_snapshot_is_a_typed_journal_command():
    from memory_service_runtime.governed.workspace_v02_models import SourceContextCreate
    body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': str(uuid4()),
            'idempotency_key': 'z' * 32, 'purpose': 'Prepare follow-up',
            'items': [{'source_id': str(uuid4()), 'version_event_id': str(uuid4()),
                       'payload_hash': 'c' * 64}]}
    model, kind = journal.parse(body)
    assert kind == 'context_v02' and isinstance(model, SourceContextCreate)
    with pytest.raises(GovernedError) as error:
        journal.parse({**body, 'items': []})
    assert error.value.code == 'INVALID_REQUEST'


def test_source_read_routes_are_registered():
    from memory_service_runtime.governed.governance import router
    paths = {route.path for route in router.routes}
    assert '/v1/governance/sources' in paths
    assert '/v1/governance/sources/{scene_id}' in paths
    assert '/v1/governance/sources/contexts/{context_id}' in paths
    assert '/v1/governance/method/tasks' in paths


def test_phase_rules_confirm_ltco_uses_implemented_draft_phase():
    assert governance.PHASE_RULES['m1b_confirm_ltco'] == {'draft'}


def test_activation_availability_reports_the_backend_blocker(monkeypatch):
    from memory_service_runtime.governed import method_v04
    monkeypatch.setattr(method_v04, 'activation_blockers',
                        lambda *a, **k: ['missing_commitment'])
    candidate = {'object_id': str(uuid4()), 'object_type': 'CandidateSet', 'object_version': 2,
                 'domain_id': 'd', 'latest_revision': {'revision_id': str(uuid4()),
                                                       'payload_hash': 'a' * 64, 'payload': {}},
                 'protocol': {'contract_version': 'tkos.method/0.4'},
                 'method_state': {'phase': 'pending'}}
    allowed, reason = governance._availability(object(), _human(roles=('CEO',)), candidate,
                                               'm1b_activate_candidates', ceo=True)
    assert allowed is False and reason == 'missing_commitment'
    monkeypatch.setattr(method_v04, 'activation_blockers', lambda *a, **k: ['stale_basis'])
    assert governance._availability(object(), _human(roles=('CEO',)), candidate,
                                    'm1b_activate_candidates', ceo=True) == (False, 'stale_basis')


def test_human_projection_never_offers_agent_actions_and_carries_options(v04_projection):
    human = _human(roles=('CEO',))
    for object_type in governance.METHOD_TASK_TYPES:
        obj = {'object_id': str(uuid4()), 'object_type': object_type, 'object_version': 1,
               'domain_id': 'd', 'latest_revision': {'revision_id': str(uuid4()),
                                                     'payload_hash': 'b' * 64, 'payload': {'title': 't'}},
               'protocol': {'contract_version': 'tkos.method/0.4'}, 'method_state': {'phase': 'draft'}}
        items = governance.method_object_actions(None, human, obj)['items']
        assert all(item['action_type'] in V04_HUMAN for item in items)
        assert all('options' in item and 'formal_effect' in item and item['label'] for item in items)


def test_operating_state_offers_targetless_problem_open(v04_projection):
    state = {'object_id': str(uuid4()), 'object_type': 'OperatingState', 'object_version': 2,
             'domain_id': 'd',
             'latest_revision': {'revision_id': str(uuid4()), 'payload_hash': 'd' * 64,
                                 'payload': {'subject_ref': {'object_id': str(uuid4()),
                                                            'revision_id': str(uuid4()),
                                                            'payload_hash': 'e' * 64}}},
             'protocol': {'contract_version': 'tkos.method/0.4'},
             'method_state': {'phase': 'confirmed'}}
    human = _human(roles=('CEO',))
    offered = {item['action_type']: item
               for item in governance.method_object_actions(None, human, state)['items']}
    assert 'method_confirm_state' in offered
    problem = offered['method_open_problem']
    assert problem['target'] is None
    assert problem['object_domain_id'] == state['domain_id']
    closed = {**state, 'object_type': 'OperatingProblem'}
    assert 'method_close_problem' in {item['action_type']
        for item in governance.method_object_actions(None, human, closed)['items']}


def test_session_facade_registers_every_frontend_source_route():
    from memory_service_app import governance as facade
    paths = {route.path for route in facade.router.routes}
    for path in ('/dashboard/api/v1/governance/method/tasks',
                 '/dashboard/api/v1/governance/method-tasks',
                 '/dashboard/api/v1/governance/sources',
                 '/dashboard/api/v1/governance/sources/{scene_id}',
                 '/dashboard/api/v1/governance/sources/contexts/{context_id}',
                 '/dashboard/api/v1/governance/objects/{object_id}/actions'):
        assert path in paths, path


def test_get_uncommitted_scene_create_rechecks_identity_without_scene_read(tmp_path, monkeypatch):
    """Double-click/list of an uncommitted create must not need the scene row."""
    monkeypatch.setattr(journal, 'get_settings',
                        lambda: SimpleNamespace(tkos_governance_commands_dir=str(tmp_path)))
    from memory_service_runtime.governed import workspace_v02_service as v02
    calls = []
    monkeypatch.setattr(v02, 'validate_create', lambda conn, ctx, history, event: calls.append(event))

    def not_persisted(*_a, **_k):
        # The scene does not exist yet: only then may validate_create run.
        raise GovernedError('NOT_FOUND')
    monkeypatch.setattr(journal, '_scene_reader', not_persisted)

    @contextmanager
    def transaction(_token):
        yield object(), SimpleNamespace(scope_id='s', principal_id='p')
    monkeypatch.setattr(journal.db, 'transaction', transaction)
    identity = {'scope_id': 's', 'principal_id': 'p'}
    command_id = str(uuid4())
    data = {'command_id': command_id, 'scope_id': 's', 'principal_id': 'p', 'input_hash': 'x',
            'kind': 'scene_v02', 'anchor_id': 'a',
            'envelope': {'contract_version': 'tkos.workspace/0.2', 'scene_id': str(uuid4()),
                         'expected_version': 0, 'idempotency_key': 'k' * 16,
                         'event': {'kind': 'scene_create', 'scene_type': 'meeting'}},
            'scene_id': 'a', 'status': 'prepared', 'receipt': None, 'error': None,
            'preview': {'members': []}}
    journal.write(journal.root() / (command_id + '.json'), data)
    loaded = journal.get(command_id, identity, 'token')
    assert loaded['command_id'] == command_id
    assert calls and calls[0]['kind'] == 'scene_create'
    # A persisted/committed scene goes through scene authorization instead and
    # must not re-run create validation (external_id uniqueness is not reimposed).
    calls.clear()
    monkeypatch.setattr(journal, '_scene_reader',
                        lambda *_a, **_k: ([{'event_id': 'e'}], 'reader', {}))
    data['receipt'] = {'receipt_id': str(uuid4())}
    journal.write(journal.root() / (command_id + '.json'), data)
    import memory_service_runtime.governed.readers as core_readers
    monkeypatch.setattr(core_readers, 'action_receipt', lambda *_a, **_k: {'receipt_id': 'r'})
    loaded = journal.get(command_id, identity, 'token')
    assert loaded['receipt'] == {'receipt_id': 'r'} and not calls
    with pytest.raises(GovernedError) as error:
        journal.get(command_id, {**identity, 'principal_id': 'other'}, 'token')
    assert error.value.code == 'NOT_FOUND'


# ---------------------------------------------------------------- UI envelope
# The browser builds its envelope in MethodActions.methodEnvelope; this table
# mirrors that exact wire shape so the real ActionRequest schema (extra=forbid)
# verifies it instead of only mirroring the TS helper.
def _ui_exact_ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


def _ui_envelope(action, params, *, target=True):
    body = {'action_type': action, 'contract_version': 'tkos.method/0.4', 'expected_versions': [],
            'idempotency_key': 'u' * 32, 'reason': '人类工作台精确预览', 'params': params}
    if target is True:
        body['target'] = {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'expected_version': 3}
    elif target is None:
        # ActionRequest.target is required-but-nullable: the key must be present.
        body['target'] = None
    return body


UI_ENVELOPES = [
    _ui_envelope('m1a_confirm_agreement', {'statement': '本人确认该精确版本'}),
    _ui_envelope('m1a_confirm_update', {'statement': '本人最终确认'}),
    _ui_envelope('m1b_confirm_ltco', {'statement': '本人确认'}),
    _ui_envelope('m1b_activate_candidates', {'statement': '整组激活', 'notes': ['说明']}),
    _ui_envelope('m1b_reopen_candidates', {'reason': '重开', 'title': '新窗口'}),
    _ui_envelope('m1b_reopen_window', {'reason': '重开', 'title': '新窗口'}),
    _ui_envelope('method_confirm_state', {'reason': '确认'}),
    _ui_envelope('method_open_problem', {'domain_id': str(uuid4()), 'payload': {
        'state_ref': _ui_exact_ref(), 'core_question': '为什么偏差?', 'statement': '偏差持续',
        'why_material': '影响结果', 'level': 'domain', 'responsible_assignment_id': str(uuid4()),
        'evidence_refs': []}}, target=None),
    _ui_envelope('method_revise_problem', {'payload': {
        'state_ref': _ui_exact_ref(), 'core_question': '为什么偏差?', 'statement': '偏差持续',
        'why_material': '影响结果', 'level': 'domain', 'responsible_assignment_id': str(uuid4()),
        'evidence_refs': []}}),
    _ui_envelope('method_close_problem', {'disposition': 'resolved', 'reason': '关闭',
                                          'evidence_refs': [_ui_exact_ref()]}),
    _ui_envelope('m1b_commit_candidate', {'responsibility_ref': _ui_exact_ref(), 'statement': '本人承诺'}),
    _ui_envelope('m1a_set_participants', {'participants': [
        {'principal_id': str(uuid4()), 'assignment_id': str(uuid4()),
         'personal_agent_id': None, 'research': False}]}),
    _ui_envelope('m1b_comment', {'target_ref': _ui_exact_ref(), 'content': '本人意见'}),
    _ui_envelope('m1b_replace_comment', {'target_ref': _ui_exact_ref(), 'content': '替代意见',
                                         'replaces_record_id': str(uuid4())}),
    _ui_envelope('m1b_withdraw_comment', {'review_record_id': str(uuid4()), 'reason': '撤回'}),
]


@pytest.mark.parametrize('body', UI_ENVELOPES, ids=[item['action_type'] for item in UI_ENVELOPES])
def test_ui_envelope_is_accepted_by_the_real_action_request_schema(body):
    from pydantic import ValidationError
    from memory_service_runtime.governed.models import ActionRequest
    request = ActionRequest.model_validate(body)
    assert request.contract_version == 'tkos.method/0.4'
    assert request.action_type in V04_HUMAN
    if request.target is not None:
        assert not hasattr(request.target, 'payload_hash')
        # The exact hash belongs to params ExactRefs; a target copy is the bug
        # that made every browser prepare fail with INVALID_REQUEST.
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body, 'target': {**body['target'], 'payload_hash': 'b' * 64}})
    else:
        assert 'target' not in body or body['target'] is None


def test_ui_reason_below_the_schema_minimum_is_rejected_and_the_fallback_is_accepted():
    from pydantic import ValidationError
    from memory_service_runtime.governed.models import ActionRequest
    short = _ui_envelope('m1b_comment', {'target_ref': _ui_exact_ref(), 'content': '短'})
    short['reason'] = '发表意见'
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(short)
    fixed = {**short, 'reason': '发表意见：本人工作台提交'}
    request = ActionRequest.model_validate(fixed)
    assert request.action_type == 'm1b_comment' and len(request.reason) >= 5


def test_my_opinions_option_exposes_an_exact_ref_for_replacement(monkeypatch):
    from memory_service_runtime.governed import governance
    record_id, object_id, revision_id = str(uuid4()), str(uuid4()), str(uuid4())
    record = {'record_id': record_id, 'kind': 'window_comment', 'principal_id': 'p1',
              'effective_opinion': True, 'target_object_id': object_id,
              'target_revision_id': revision_id, 'content': {'content': '本人意见'}}
    monkeypatch.setattr(governance.method_readers, 'review_records', lambda *a, **k: {'items': [record]})
    monkeypatch.setattr(governance.method_access, 'revision', lambda *a, **k: {'payload_hash': 'a' * 64})
    ctx = SimpleNamespace(scope_id='s', principal_id='p1', principal_type='human', assignments=[])
    options = governance._my_opinions(object(), ctx, {'object_id': str(uuid4())})
    assert options and options[0]['ref'] == options[0]['target_ref']
    assert options[0]['ref'] == {'object_id': object_id, 'revision_id': revision_id, 'payload_hash': 'a' * 64}
