"""0.5 delegates unchanged actions to the frozen 0.4 executor; light reads follow the caller's version (no DB)."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import (method_access, method_service, method_v04, method_v05,
                                             protocol, workbench)
from memory_service_runtime.governed.errors import GovernedError


def ref():
    return {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, head):
        self.head = head

    def execute(self, sql, params=None):
        return _Cursor(dict(self.head))


def _light_env(monkeypatch, binding_version):
    head = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'effective_revision_id': None, 'latest_revision_id': None}
    revision = {'object_id': head['object_id'], 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    monkeypatch.setattr(method_access, 'raw_revision', lambda conn, ctx, oid, rid: dict(revision))
    monkeypatch.setattr(protocol, 'current_binding',
                        lambda conn, scope_id, oid: {'contract_version': binding_version})
    return _Conn(head), SimpleNamespace(scope_id=str(uuid4()), principal_id=str(uuid4())), head, revision


def test_light_execution_follows_the_callers_contract_version(monkeypatch):
    conn, ctx, head, revision = _light_env(monkeypatch, 'tkos.method/0.5')
    reference = {'object_id': head['object_id'], 'revision_id': revision['revision_id'], 'payload_hash': 'a' * 64}
    with pytest.raises(GovernedError) as exc:  # default stays 0.4: a 0.5 binding is foreign to it
        method_v04._LightExecution(conn, ctx).ref(reference)
    assert exc.value.code == 'PROTOCOL_BINDING_CONFLICT'
    assert method_v05.light(conn, ctx).ref(reference)[0]['object_id'] == head['object_id']
    conn, ctx, head, revision = _light_env(monkeypatch, 'tkos.method/0.4')
    reference = {'object_id': head['object_id'], 'revision_id': revision['revision_id'], 'payload_hash': 'a' * 64}
    assert method_v04._LightExecution(conn, ctx).ref(reference)[0]['object_id'] == head['object_id']
    with pytest.raises(GovernedError):
        method_v05.light(conn, ctx).ref(reference)


def test_unchanged_actions_delegate_to_the_frozen_0_4_executor(monkeypatch):
    seen = []
    monkeypatch.setattr(method_v04, 'collect', lambda e: seen.append(('collect', e.kind)))
    monkeypatch.setattr(method_v04, 'run', lambda e: seen.append(('run', e.kind)) or {'ok': True})
    e = SimpleNamespace(kind='m1a_confirm_agreement', params={}, target_revision={'payload': {}})
    method_v05.collect(e)
    assert method_v05.run(e) == {'ok': True}
    assert seen == [('collect', 'm1a_confirm_agreement'), ('run', 'm1a_confirm_agreement')]


def test_registered_handlers_win_and_unknown_actions_are_refused(monkeypatch):
    seen = []
    monkeypatch.setitem(method_v05.COLLECTORS, 'probe_action', lambda e: seen.append('collect'))
    monkeypatch.setitem(method_v05.RUNNERS, 'probe_action', lambda e: seen.append('run') or {'done': True})
    monkeypatch.setattr(method_v05, 'ACTION_PARAMS', {**method_v05.ACTION_PARAMS, 'probe_action': object})
    e = SimpleNamespace(kind='probe_action', params={})
    assert method_v05.run(e) == {'done': True} and seen == ['collect', 'run']
    with pytest.raises(GovernedError) as exc:
        method_v05.collect(SimpleNamespace(kind='method_confirm_state', params={}))
    assert exc.value.code == 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'


def test_service_routes_0_5_to_the_0_5_executor():
    assert method_service.FORMAL_GOVERNANCE_VERSIONS == frozenset({'tkos.method/0.4', 'tkos.method/0.5'})
    assert workbench.object_types(None, None, 'tkos.method/0.5')['contract_version'] == 'tkos.method/0.5'
    assert any(item['object_type'] == 'Constraint' for item in workbench.object_types(None, None, 'tkos.method/0.5')['items'])
    assert ('tkos.method', 'tkos.method/0.5') in protocol.SUPPORTED_PROTOCOL_CONTRACTS
    assert 'method_confirm_state' not in method_v05.V05_SCOPED_ACTIONS
    assert {'m1b_confirm_constraint', 'm1b_revise_constraint', 'm1b_commit_candidate'} <= method_v05.V05_SCOPED_ACTIONS


# --------------------------------------- 0.4-bound objects vs 0.5-only actions
#
# Registering the 0.5 action names in this process makes them nameable by any
# client.  The authoritative version of an object is its BINDING, fenced by
# ``protocol.gate_target_action``; the request's ``contract_version`` only
# declares the client's action format (protocol.py module docstring).  These
# tests pin both halves of that fence for a 0.4-bound object, and the mandated
# ordering: 先认证/授权（404/403），再暴露协议错误.

V05_ONLY_ACTIONS = ('m1b_record_constraint', 'm1b_revise_constraint',
                    'm1b_confirm_constraint', 'm1b_confirm_review')

SCOPE, OID, DOMAIN = (str(uuid4()) for _ in range(3))


class _GateCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _GateConn:
    """Replays exactly the registration rows gate_target_action reads for a Method binding."""

    def __init__(self, binding_version):
        from memory_service_runtime.governed import method_v04_profile, method_v05_profile
        from memory_service_runtime.governed.method_models import registry
        source = method_v05_profile if binding_version == 'tkos.method/0.5' else method_v04_profile
        core = source.content()
        actions, _targets, payloads = registry(binding_version)
        self.binding = {'scope_id': SCOPE, 'object_id': OID, 'binding_version': 1,
                        'protocol_id': 'tkos.method', 'contract_version': binding_version,
                        'profile_id': core['profile_id'], 'profile_revision': core['revision'],
                        'profile_canonical_hash': core['canonical_hash'], 'record_origin': 'method'}
        self.profile_row = {'scope_id': SCOPE, 'profile_id': core['profile_id'],
                            'revision': core['revision'], 'schema_version': core['profile_core_schema_version'],
                            'canonical_hash': core['canonical_hash'], 'content': core}
        self.registry_row = {'scope_id': SCOPE, 'protocol_id': 'tkos.method',
                             'contract_version': binding_version,
                             'content': {'can_read': True, 'can_create': True, 'can_write': True,
                                         'evidence_upload': True, 'actions': sorted(actions),
                                         'object_types': sorted(set(payloads) | {'EvidenceAsset'}),
                                         'readonly_compat': [binding_version], 'notes': 'test'}}

    def execute(self, sql, params=()):
        if 'gov_object_protocol_bindings' in sql:
            return _GateCursor([self.binding] if str(params[1]) == OID else [])
        if 'gov_method_profile_revisions' in sql:
            return _GateCursor([self.profile_row])
        if 'gov_protocol_support_registry' in sql:
            return _GateCursor([self.registry_row])
        if 'gov_objects' in sql:
            return _GateCursor([{'object_type': 'PeriodReview'}])
        raise AssertionError(f'unexpected SQL: {sql}')


@pytest.mark.parametrize('action', V05_ONLY_ACTIONS)
def test_a_0_4_bound_object_cannot_execute_a_0_5_only_action(action):
    """Both declarations are refused: a 0.5 declaration conflicts with the binding,
    and a matching 0.4 declaration finds no such action in the 0.4 registry."""
    conn = _GateConn('tkos.method/0.4')
    with pytest.raises(GovernedError) as exc:
        protocol.gate_target_action(conn, SCOPE, OID, action, 'tkos.method/0.5')
    assert exc.value.code == 'PROTOCOL_BINDING_CONFLICT'
    with pytest.raises(GovernedError) as exc:
        protocol.gate_target_action(conn, SCOPE, OID, action, 'tkos.method/0.4')
    assert exc.value.code == 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'


def test_a_0_5_bound_object_accepts_a_0_5_only_action_through_the_same_gate():
    """The refusals above are version containment, not a blanket ban on the names."""
    conn = _GateConn('tkos.method/0.5')
    assert protocol.gate_target_action(conn, SCOPE, OID, 'm1b_confirm_review',
                                       'tkos.method/0.5') == 'tkos.method/0.5'
    with pytest.raises(GovernedError) as exc:
        protocol.gate_target_action(conn, SCOPE, OID, 'm1b_confirm_review', 'tkos.method/0.4')
    assert exc.value.code == 'PROTOCOL_BINDING_CONFLICT'


def _dispatch(action, declared, target=True):
    """Build the real envelope and ask the real factory which execution claims it."""
    from memory_service_runtime.governed import service
    from memory_service_runtime.governed.models import ActionRequest
    params = {'m1b_confirm_review': {'statement': 'Reviewed', 'findings': ['One']},
              'm1b_confirm_constraint': {'statement': 'Confirmed'},
              'm1b_revise_constraint': {'payload': {
                  'title': 'Headcount', 'applies_to': {'kind': 'company'}, 'statement': 'Frozen',
                  'constraint_type': 'people',
                  'effective': {'start': '2026-01-01T00:00:00Z', 'end': '2026-03-31T00:00:00Z'},
                  'source': 'Board', 'authority': 'CEO', 'severity': 'hard'}},
              'm1b_record_constraint': {'domain_id': DOMAIN, 'payload': {
                  'title': 'Headcount', 'applies_to': {'kind': 'company'}, 'statement': 'Frozen',
                  'constraint_type': 'people',
                  'effective': {'start': '2026-01-01T00:00:00Z', 'end': '2026-03-31T00:00:00Z'},
                  'source': 'Board', 'authority': 'CEO', 'severity': 'hard'}}}[action]
    body = {'action_type': action, 'contract_version': declared, 'expected_versions': [],
            'idempotency_key': 'v05-containment-key', 'reason': 'Version containment check',
            'params': params,
            'target': {'object_id': OID, 'revision_id': str(uuid4()), 'expected_version': 1} if target else None}
    request = ActionRequest.model_validate(body)
    return service._execution_factory(None, None, request)


def _raise(code):
    def boom(*a, **k):
        raise GovernedError(code)
    return boom


@pytest.mark.parametrize('action', ('m1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'))
@pytest.mark.parametrize('code', ('NOT_FOUND', 'FORBIDDEN'))
def test_authorization_resolves_before_any_protocol_error_is_revealed(action, code, monkeypatch):
    """An invisible (404) or unauthorized (403) caller never reaches the protocol fence.

    Both rungs are pinned, so naming a 0.5-only action can never be used to
    probe whether a 0.4-bound object exists.
    """
    from memory_service_runtime.governed import service as core
    execution = _dispatch(action, 'tkos.method/0.4')
    gate_calls = []
    monkeypatch.setattr(protocol, 'gate_target_action',
                        lambda *a, **k: gate_calls.append(a) or 'tkos.method/0.4')
    if code == 'NOT_FOUND':
        monkeypatch.setattr(method_access, 'head', _raise('NOT_FOUND'))
        monkeypatch.setattr(core.db, 'object_row', _raise('NOT_FOUND'))
    else:
        head = {'object_id': OID, 'object_type': 'PeriodReview', 'domain_id': DOMAIN,
                'latest_revision_id': execution.request.target.revision_id, 'object_version': 1}
        monkeypatch.setattr(method_access, 'head', lambda *a, **k: dict(head))
        monkeypatch.setattr(core.db, 'object_row', lambda *a, **k: dict(head))
        monkeypatch.setattr(core.db, 'revision_row', lambda *a, **k: {
            'object_id': OID, 'revision_id': execution.request.target.revision_id,
            'payload_hash': 'a' * 64, 'payload': {}})
        monkeypatch.setattr(core.db, 'authorize_domain', _raise('FORBIDDEN'))
    execution.ctx = SimpleNamespace(scope_id=SCOPE, principal_id=str(uuid4()),
                                    principal_type='human', assignments=[])
    with pytest.raises(GovernedError) as exc:
        execution.authorize()
    assert exc.value.code == code
    assert gate_calls == []


@pytest.mark.parametrize('action', ('m1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'))
def test_a_visible_0_4_object_reaches_the_protocol_fence_and_is_refused(action, monkeypatch):
    """With the object visible and the domain authorized, the fence is the refusal."""
    from memory_service_runtime.governed import service as core
    execution = _dispatch(action, 'tkos.method/0.4')
    head = {'object_id': OID, 'object_type': 'PeriodReview', 'domain_id': DOMAIN,
            'latest_revision_id': execution.request.target.revision_id, 'object_version': 1}
    gate = _GateConn('tkos.method/0.4')
    execution.conn = gate
    execution.ctx = SimpleNamespace(scope_id=SCOPE, principal_id=str(uuid4()),
                                    principal_type='human', assignments=[])
    monkeypatch.setattr(core.db, 'object_row', lambda *a, **k: dict(head))
    monkeypatch.setattr(core.db, 'revision_row', lambda *a, **k: {
        'object_id': OID, 'revision_id': execution.request.target.revision_id,
        'payload_hash': 'a' * 64, 'payload': {}})
    monkeypatch.setattr(core.db, 'authorize_domain',
                        lambda *a, **k: [{'assignment_id': str(uuid4()), 'role': 'CEO',
                                          'domain_id': DOMAIN, 'principal_id': str(uuid4())}])
    with pytest.raises(GovernedError) as exc:
        execution.authorize()
    assert exc.value.code == 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'


def test_a_targetless_0_5_action_is_refused_by_the_0_4_envelope_itself():
    """m1b_record_constraint has no target, which no 0.4 envelope rule permits."""
    with pytest.raises(ValueError):
        _dispatch('m1b_record_constraint', 'tkos.method/0.4', target=False)
