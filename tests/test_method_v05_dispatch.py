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

    def __init__(self, binding_version, *, object_present=True):
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
        self.object_row = {'scope_id': SCOPE, 'object_id': OID, 'object_type': 'PeriodReview',
                           'domain_id': DOMAIN, 'object_version': 1,
                           'latest_revision_id': None, 'effective_revision_id': None} if object_present else None

    def execute(self, sql, params=()):
        if 'gov_object_protocol_bindings' in sql:
            return _GateCursor([self.binding] if str(params[1]) == OID else [])
        if 'gov_method_profile_revisions' in sql:
            return _GateCursor([self.profile_row])
        if 'gov_protocol_support_registry' in sql:
            return _GateCursor([self.registry_row])
        if 'gov_objects' in sql:
            return _GateCursor([self.object_row] if self.object_row else [])
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


def _dispatch(action, declared, target=None):
    """Build the real envelope and ask the real factory which execution claims it."""
    from memory_service_runtime.governed import service
    from memory_service_runtime.governed.method_v05_models import ACTION_TARGETS as V05_TARGETS
    from memory_service_runtime.governed.models import ActionRequest
    if target is None:
        target = bool(V05_TARGETS[action])
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
@pytest.mark.parametrize('scenario', ('absent', 'read_denied'))
def test_authorization_resolves_before_any_protocol_error_is_revealed(action, scenario, monkeypatch):
    """A correctly declared 0.5 action aimed at an object the caller cannot read.

    The real ``method_access.head`` runs here, so each rung is tied to the code that
    produces it: a missing row raises NOT_FOUND (method_access.py:185-186), and a
    denied read right comes from ``db.authorize_domain(conn, ctx, domain, 'read')``
    (method_access.py:189) which, with no scoped grant to rescue it, also answers
    NOT_FOUND (method_access.py:207-208) rather than disclosing existence.  Both run
    before ``protocol.gate_target_action`` (method_service.py:48), so naming a
    0.5-only action can never probe whether a 0.4-bound object exists.
    """
    from memory_service_runtime.governed import db as core_db, workspace_v02_guard
    execution = _dispatch(action, 'tkos.method/0.5')   # passes the membership guard
    execution.conn = _GateConn('tkos.method/0.4', object_present=scenario != 'absent')
    execution.ctx = SimpleNamespace(scope_id=SCOPE, principal_id=str(uuid4()),
                                    principal_type='human', assignments=[])
    gate_calls, read_checks = [], []
    monkeypatch.setattr(protocol, 'gate_target_action', lambda *a, **k: gate_calls.append(a))
    monkeypatch.setattr(workspace_v02_guard, 'enforce_object', lambda *a, **k: None)
    monkeypatch.setattr(core_db, '_assignments', lambda *a, **k: [])

    def denied(conn, ctx, domain_id, action_type=None, *a, **k):
        read_checks.append((domain_id, action_type))
        raise GovernedError('FORBIDDEN')

    monkeypatch.setattr(core_db, 'authorize_domain', denied)
    # Nothing rescues the denied read: this object carries no scoped Method grant.
    monkeypatch.setattr(method_access, 'is_method_object', lambda *a, **k: False)
    with pytest.raises(GovernedError) as exc:
        execution.authorize()
    assert exc.value.code == 'NOT_FOUND'
    assert gate_calls == []
    # The 403 rung really ran: the read right was the thing consulted and denied.
    assert read_checks == ([(DOMAIN, 'read')] if scenario == 'read_denied' else [])


@pytest.mark.parametrize('action', ('m1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'))
def test_a_readable_0_4_bound_object_is_refused_at_the_real_protocol_fence(action, monkeypatch):
    """End to end through MethodExecution: the object is readable, so the fence runs —
    and the 0.4 binding refuses the 0.5 declaration."""
    execution = _dispatch(action, 'tkos.method/0.5')
    head = {'object_id': OID, 'object_type': 'PeriodReview', 'domain_id': DOMAIN,
            'latest_revision_id': execution.request.target.revision_id, 'object_version': 1}
    execution.conn = _GateConn('tkos.method/0.4')
    execution.ctx = SimpleNamespace(scope_id=SCOPE, principal_id=str(uuid4()),
                                    principal_type='human', assignments=[])
    monkeypatch.setattr(method_access, 'head', lambda *a, **k: dict(head))
    monkeypatch.setattr(method_access, 'revision', lambda *a, **k: {
        'object_id': OID, 'revision_id': execution.request.target.revision_id,
        'payload_hash': 'a' * 64, 'payload': {}})
    with pytest.raises(GovernedError) as exc:
        execution.authorize()      # real protocol.gate_target_action against a 0.4 binding
    assert exc.value.code == 'PROTOCOL_BINDING_CONFLICT'


def test_a_targetless_0_5_action_is_refused_by_the_0_4_envelope_itself():
    """m1b_record_constraint has no target, which no 0.4 envelope rule permits."""
    with pytest.raises(ValueError):
        _dispatch('m1b_record_constraint', 'tkos.method/0.4', target=False)


# ----------------------------- routing: 0.5-only actions reach the 0.5 executor
#
# ``handles_request`` claims a request by action NAME only.  Until it learned
# 0.5's table the four 0.5-only names fell through to the legacy execution, so
# the 0.5 executor was unreachable even under a correct 0.5 declaration.  The
# guard below is what makes claiming them safe: the declared version's registry
# is the one that must contain the action.

OLDER_DECLARATIONS = ('tkos.method/0.1', 'tkos.method/0.2', 'tkos.method/0.3', 'tkos.method/0.4')


@pytest.mark.parametrize('action', V05_ONLY_ACTIONS)
def test_a_0_5_only_action_under_a_0_5_declaration_reaches_the_method_executor(action):
    from memory_service_runtime.governed.method_service import MethodExecution
    assert isinstance(_dispatch(action, 'tkos.method/0.5'), MethodExecution)


@pytest.mark.parametrize('action', ('m1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'))
def test_a_0_5_declared_action_reaches_the_protocol_fence_with_its_own_version(action, monkeypatch):
    """The declared version is handed to the fence unchanged, after the object is read."""
    execution = _dispatch(action, 'tkos.method/0.5')
    head = {'object_id': OID, 'object_type': 'PeriodReview', 'domain_id': DOMAIN,
            'latest_revision_id': execution.request.target.revision_id, 'object_version': 1}
    monkeypatch.setattr(method_access, 'head', lambda *a, **k: dict(head))
    monkeypatch.setattr(method_access, 'revision', lambda *a, **k: {
        'object_id': OID, 'revision_id': execution.request.target.revision_id,
        'payload_hash': 'a' * 64, 'payload': {}})
    gate_calls = []
    monkeypatch.setattr(protocol, 'gate_target_action',
                        lambda *a, **k: gate_calls.append(a) or (_ for _ in ()).throw(
                            GovernedError('PROTOCOL_BINDING_CONFLICT')))
    execution.ctx = SimpleNamespace(scope_id=SCOPE, principal_id=str(uuid4()),
                                    principal_type='human', assignments=[])
    with pytest.raises(GovernedError) as exc:
        execution.authorize()
    assert exc.value.code == 'PROTOCOL_BINDING_CONFLICT'
    assert [call[3:] for call in gate_calls] == [(action, 'tkos.method/0.5')]


@pytest.mark.parametrize('action', ('m1b_revise_constraint', 'm1b_confirm_constraint', 'm1b_confirm_review'))
@pytest.mark.parametrize('declared', OLDER_DECLARATIONS)
def test_a_0_5_only_action_under_an_older_declaration_is_refused_not_a_crash(action, declared, monkeypatch):
    """The action must belong to the registry of the version the caller declared.

    Without the membership guard this is a bare ``KeyError`` out of
    ``self.action_params[self.kind]`` — an unhandled 500 — rather than a
    governed refusal.  Nothing about any object is read to decide it.
    """
    execution = _dispatch(action, declared)
    probes = []
    monkeypatch.setattr(method_access, 'head', lambda *a, **k: probes.append('head'))
    monkeypatch.setattr(protocol, 'gate_target_action', lambda *a, **k: probes.append('gate'))
    with pytest.raises(GovernedError) as exc:
        execution.authorize()
    assert exc.value.code == 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'
    assert probes == []


def test_no_method_action_name_of_any_version_falls_through_to_the_legacy_execution():
    """Routing is by action NAME; the declared version is enforced later by the guard.

    A name no version-aware execution claims lands on the legacy ActionExecution,
    which is how the four 0.5-only names were unreachable before.
    """
    from memory_service_runtime.governed.method_service import MethodExecution
    from memory_service_runtime.governed.method_models import registry
    for version in (*OLDER_DECLARATIONS, 'tkos.method/0.5'):
        params, _targets, _payloads = registry(version)
        assert params, version
        for kind in params:
            assert MethodExecution.handles_request(
                None, None, SimpleNamespace(action_type=kind)), (version, kind)
