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
        from memory_service_runtime.governed import (method_profile, method_v02_profile,
                                                       method_v03_profile, method_v04_profile,
                                                       method_v05_profile)
        from memory_service_runtime.governed.method_models import registry
        core = {'tkos.method/0.1': method_profile, 'tkos.method/0.2': method_v02_profile,
                'tkos.method/0.3': method_v03_profile, 'tkos.method/0.4': method_v04_profile,
                'tkos.method/0.5': method_v05_profile}[binding_version].content()
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


# ------------------------------- the 0.5 read gate, driven through real code
#
# Every read of a bound object passes protocol.require_read_support, whose
# accepted-interpretation whitelist is enumerated per Method version.  Nothing
# in the suite had ever driven a 0.5 binding through it, which is exactly how
# the missing entry survived: the dispatch tests above stop at the write fence.

def _readable(monkeypatch, conn):
    """Let head_access reach require_read_support: the object exists and is readable."""
    from memory_service_runtime.governed import db as core_db, workspace_v02_guard
    monkeypatch.setattr(workspace_v02_guard, 'enforce_object', lambda *a, **k: None)
    monkeypatch.setattr(workspace_v02_guard, 'object_allowed', lambda *a, **k: True)
    monkeypatch.setattr(core_db, '_assignments', lambda *a, **k: [])
    monkeypatch.setattr(core_db, 'authorize_domain', lambda *a, **k: [])
    return conn, SimpleNamespace(scope_id=SCOPE, principal_id=str(uuid4()),
                                 principal_type='human', assignments=[])


@pytest.mark.parametrize('version,status', [('tkos.method/0.4', 'method_v0_4'),
                                            ('tkos.method/0.5', 'method_v0_5')])
def test_require_read_support_accepts_every_compiled_method_interpretation(version, status):
    """0.5 must be readable on exactly the same terms as 0.4."""
    metadata = protocol.require_read_support(_GateConn(version), SCOPE, OID)
    assert metadata['interpretation_status'] == status
    assert metadata['contract_version'] == version


@pytest.mark.parametrize('version', ('tkos.method/0.4', 'tkos.method/0.5'))
def test_method_access_head_reads_a_0_5_bound_object(monkeypatch, version):
    """Through the real read path: head_access, then the real require_read_support."""
    conn, ctx = _readable(monkeypatch, _GateConn(version))
    assert method_access.head(conn, ctx, OID)['object_id'] == OID


def test_every_compiled_method_contract_is_readable():
    """``require_read_support`` enumerates its accepted statuses by hand, so a newly
    compiled contract silently drops out of it.  This ties the accepted set to
    ``SUPPORTED_PROTOCOL_CONTRACTS`` instead of to a literal list.
    """
    method_versions = sorted(version for protocol_id, version in protocol.SUPPORTED_PROTOCOL_CONTRACTS
                             if protocol_id == 'tkos.method')
    assert 'tkos.method/0.5' in method_versions
    for version in method_versions:
        metadata = protocol.require_read_support(_GateConn(version), SCOPE, OID)
        assert metadata['registration_status'] == 'registered', version
        expected = 'method_v0_' + version.rsplit('.', 1)[-1]   # tkos.method/0.4 -> method_v0_4
        assert metadata['interpretation_status'] == expected, version


# ------------------------------------------------------------- Constraint


def _fake_execution(*, refs, states=None, ctx_type='human'):
    states = states or {}

    def ref(reference, types=None, effective=False, current=True):
        head, revision = refs[reference['object_id']]
        if types and head['object_type'] not in types:
            raise GovernedError('NOT_FOUND')
        return head, revision

    return SimpleNamespace(ref=ref, state=lambda head: states.get(head['object_id'], {}),
                           ctx=SimpleNamespace(principal_type=ctx_type, principal_id=str(uuid4()),
                                               scope_id=str(uuid4()), assignments=[]),
                           params={}, _v04={})


def _constraint(kind, scope_id=None, phase='confirmed'):
    oid = str(uuid4())
    applies = {'kind': kind}
    if kind == 'scope':
        applies['scope_id'] = scope_id
    if kind == 'mission':
        applies['mission_ref'] = ref()
    head = {'object_id': oid, 'object_type': 'Constraint'}
    revision = {'object_id': oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                'payload': {'applies_to': applies}}
    reference = {'object_id': oid, 'revision_id': revision['revision_id'], 'payload_hash': 'a' * 64}
    return oid, head, revision, reference, phase


def test_constraint_refs_must_be_confirmed_and_applicable():
    company = _constraint('company')
    own_scope = _constraint('scope', 'bf-1')
    other_scope = _constraint('scope', 'bf-2')
    draft = _constraint('company', phase='draft')
    refs = {c[0]: (c[1], c[2]) for c in (company, own_scope, other_scope, draft)}
    states = {c[0]: {'phase': c[4]} for c in (company, own_scope, other_scope, draft)}
    e = _fake_execution(refs=refs, states=states)
    method_v05._check_constraint_refs(e, [company[3], own_scope[3]], scope_id='bf-1')
    with pytest.raises(GovernedError) as exc:
        method_v05._check_constraint_refs(e, [other_scope[3]], scope_id='bf-1')
    assert exc.value.code == 'INVALID_REQUEST'
    with pytest.raises(GovernedError) as exc:
        method_v05._check_constraint_refs(e, [draft[3]], scope_id='bf-1')
    assert exc.value.code == 'STALE_DEPENDENCY'
    mission = _constraint('mission')
    refs[mission[0]] = (mission[1], mission[2])
    states[mission[0]] = {'phase': 'confirmed'}
    own = mission[2]['payload']['applies_to']['mission_ref']
    method_v05._check_constraint_refs(e, [mission[3]], scope_id='bf-1', mission_ref=own)
    with pytest.raises(GovernedError):  # a Mission constraint never applies to another Mission
        method_v05._check_constraint_refs(e, [mission[3]], scope_id='bf-1', mission_ref=ref())


def test_constraint_actions_are_registered_as_0_5_handlers():
    for kind in ('m1b_record_constraint', 'm1b_revise_constraint', 'm1b_confirm_constraint'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS


def test_mission_constraint_confirmer_uses_the_missions_own_scope_not_its_pcos(monkeypatch):
    """Contract §2: a mission-kind Constraint is confirmed by the DRI of *that Mission's*
    primary Scope -- not its parent PCO's, even though the two may differ (PCOPayload and
    MissionPayload each carry their own independent ``primary_scope_id``).

    ``v4._pco_dri`` is faked to key strictly off the ``primary_scope_id`` it is handed, so
    the two readings are distinguished by construction: whichever scope's DRI actually gets
    resolved is the one whose identity check below passes.  Confirming this scenario against
    the brief's sample code (which hands ``_pco_dri`` the PARENT PCO's own payload, i.e. the
    PCO's ``primary_scope_id``) makes both assertions fail -- the PCO-scope DRI would be
    accepted and the Mission-scope DRI refused, the exact inverse of what is asserted here.
    """
    mission_scope, pco_scope = 'mission-scope', 'pco-scope'
    mission_dri, pco_dri = str(uuid4()), str(uuid4())
    dri_for_scope = {mission_scope: mission_dri, pco_scope: pco_dri}

    def fake_pco_dri(_e, payload):
        principal = dri_for_scope[payload['primary_scope_id']]
        return principal, {'assignment_id': str(uuid4()), 'principal_id': principal, 'role': 'DOMAIN_DRI'}

    monkeypatch.setattr(method_v04, '_pco_dri', fake_pco_dri)

    pco_oid = str(uuid4())
    pco_head = {'object_id': pco_oid, 'object_type': 'PCO'}
    pco_revision = {'object_id': pco_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                    'payload': {'architecture_ref': ref(), 'primary_scope_id': pco_scope}}

    mission_oid = str(uuid4())
    mission_head = {'object_id': mission_oid, 'object_type': 'Mission'}
    parent_pco_ref = {'object_id': pco_oid, 'revision_id': pco_revision['revision_id'], 'payload_hash': 'a' * 64}
    mission_revision = {'object_id': mission_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                        'payload': {'parent_pco_ref': parent_pco_ref, 'primary_scope_id': mission_scope}}
    mission_ref = {'object_id': mission_oid, 'revision_id': mission_revision['revision_id'], 'payload_hash': 'a' * 64}

    constraint_oid = str(uuid4())
    head = {'object_id': constraint_oid, 'object_type': 'Constraint'}
    target_revision = {'object_id': constraint_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                       'payload': {'applies_to': {'kind': 'mission', 'mission_ref': mission_ref}, 'evidence_refs': []}}

    refs = {pco_oid: (pco_head, pco_revision), mission_oid: (mission_head, mission_revision)}
    states = {constraint_oid: {'phase': 'draft'}}
    e = _fake_execution(refs=refs, states=states)
    e.target, e.target_revision = head, target_revision
    # The Mission and its parent PCO are read by the server-side resolver; serve it the same fakes.
    e.conn = None
    monkeypatch.setattr(method_v05, 'light', lambda conn, ctx: SimpleNamespace(ref=e.ref))

    def require_actor(principal_id, principal_type='human'):
        if e.ctx.principal_id != principal_id or e.ctx.principal_type != principal_type:
            raise GovernedError('FORBIDDEN')
    e.require_actor = require_actor

    e.ctx.principal_id = pco_dri
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_confirm_constraint(e)
    assert exc.value.code == 'FORBIDDEN'   # the PCO's own Scope DRI is not this Mission's confirmer

    e.ctx.principal_id = mission_dri
    method_v05._collect_confirm_constraint(e)   # the Mission's own Scope DRI is
    assert e._v04['constraint_owner'] == mission_dri


class _ShapeConn:
    """Answers head_access by SQL shape: the head row, one Constraint's own revisions,
    and nothing for every other grant source (windows, Missions, 0.4 results)."""

    def __init__(self, head, revisions):
        self.head, self.revisions = head, revisions

    def execute(self, sql, params=None):
        if 'FROM gov_objects WHERE' in sql:
            rows = [dict(self.head)]
        elif 'FROM gov_object_revisions' in sql and 'JOIN' not in sql:
            rows = [dict(row) for row in self.revisions]
        else:
            rows = []
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)


def test_a_scope_dri_without_a_company_role_reads_exactly_the_constraint_versions_it_answers_for(monkeypatch):
    """Contract §2: a scope Constraint is confirmed (and may be revised) by the Scope's mapped
    DOMAIN_DRI, who in 0.4/0.5 holds only its auth-domain appointment.  The Constraint lives in
    the company domain, so without a responsibility grant head_access answered NOT_FOUND and the
    only permitted confirmer could never load its target (real HTTP/PG acceptance, Task 12).
    The grant is per exact version, resolved live by the scoped-authorization resolver; anyone
    else still gets NOT_FOUND, and no other object type gains anything from it."""
    from memory_service_runtime.governed import db as core_db, workspace_v02_guard
    dri_v1, dri_v2, ic = str(uuid4()), str(uuid4()), str(uuid4())
    oid, r1, r2 = str(uuid4()), str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'Constraint', 'domain_id': str(uuid4()),
            'latest_revision_id': r2, 'effective_revision_id': r1}

    def version(rid, dri):   # each version cites an Architecture version that maps the Scope to `dri`
        return {'revision_id': rid, 'payload': {'applies_to': {'kind': 'scope', 'scope_id': 'scope-a'},
                                                'architecture_ref': {'revision_id': dri}}}

    def responsible(conn, ctx, payload):
        if payload['architecture_ref']['revision_id'] != ctx.principal_id:
            raise GovernedError('FORBIDDEN')
        return {'assignment_id': str(uuid4()), 'principal_id': ctx.principal_id}

    def forbidden(*_args, **_kwargs):
        raise GovernedError('FORBIDDEN')

    monkeypatch.setattr(workspace_v02_guard, 'enforce_object', lambda *a, **k: None)
    monkeypatch.setattr(core_db, '_assignments', lambda *a, **k: [])
    monkeypatch.setattr(core_db, 'authorize_domain', forbidden)       # no company-domain read
    monkeypatch.setattr(method_access, 'is_method_object', lambda *a, **k: True)
    monkeypatch.setattr(method_access, 'research_grants', lambda *a, **k: set())
    monkeypatch.setattr(method_access, 'anchor_grants', lambda *a, **k: set())
    monkeypatch.setattr(protocol, 'current_binding', lambda conn, scope_id, object_id: {'contract_version': 'tkos.method/0.5'})
    monkeypatch.setattr(method_v05, 'constraint_assignment_static', responsible)
    conn = _ShapeConn(head, [version(r1, dri_v1), version(r2, dri_v2)])

    def ctx(principal_id):
        return SimpleNamespace(scope_id=str(uuid4()), principal_id=principal_id, principal_type='human', assignments=[])

    assert method_access.head_access(conn, ctx(dri_v1), oid) == (head, {r1})
    assert method_access.head_access(conn, ctx(dri_v2), oid) == (head, {r2})
    with pytest.raises(GovernedError) as exc:
        method_access.head_access(conn, ctx(ic), oid)
    assert exc.value.code == 'NOT_FOUND'
    conn.head = {**head, 'object_type': 'PCO'}            # the grant is the Constraint's alone
    with pytest.raises(GovernedError) as exc:
        method_access.head_access(conn, ctx(dri_v1), oid)
    assert exc.value.code == 'NOT_FOUND'


def test_scope_constraint_basis_is_checked_server_side_not_through_the_callers_grants(monkeypatch):
    """0.4/0.5 never grant a Scope DRI the company-domain Architecture
    (method_access.anchor_participant), so, like v4._candidate_basis_current, the exact
    Architecture basis of a scope Constraint is read server-side: its responsible DRI can record,
    revise and confirm it.  The basis must still be the exact effective version holding the unit."""
    arch_oid, arch_rid = str(uuid4()), str(uuid4())
    head = {'object_id': arch_oid, 'object_type': 'StrategicArchitecture',
            'effective_revision_id': arch_rid, 'latest_revision_id': arch_rid}
    architecture = {'object_id': arch_oid, 'revision_id': arch_rid, 'payload_hash': 'a' * 64,
                    'payload': {'battlefields': [], 'domains': [{'unit_id': 'scope-a'}]}}
    monkeypatch.setattr(method_access, 'raw_revision', lambda conn, ctx, oid, rid: dict(architecture))
    monkeypatch.setattr(protocol, 'current_binding', lambda conn, scope_id, oid: {'contract_version': 'tkos.method/0.5'})

    def caller_cannot_read(reference, types=None, effective=False, current=True):
        raise GovernedError('NOT_FOUND')

    e = SimpleNamespace(conn=_Conn(head), ref=caller_cannot_read,
                        ctx=SimpleNamespace(scope_id=str(uuid4()), principal_id=str(uuid4()), principal_type='human'))
    payload = {'applies_to': {'kind': 'scope', 'scope_id': 'scope-a'}, 'evidence_refs': [],
               'architecture_ref': {'object_id': arch_oid, 'revision_id': arch_rid, 'payload_hash': 'a' * 64}}
    method_v05._check_constraint_payload(e, payload)
    with pytest.raises(GovernedError) as exc:
        method_v05._check_constraint_payload(e, {**payload, 'applies_to': {'kind': 'scope', 'scope_id': 'scope-x'}})
    assert exc.value.code == 'INVALID_REQUEST'   # the unit must exist in that exact Architecture version
    head['effective_revision_id'] = str(uuid4())
    with pytest.raises(GovernedError) as exc:
        method_v05._check_constraint_payload(e, payload)
    assert exc.value.code == 'STALE_DEPENDENCY'  # and that version must still be the effective one


class _TablesConn:
    """Fake connection serving in-memory rows for exactly the queries that the real
    head_access -> constraint_grants -> constraint_assignment_static path issues.  No Python
    function is faked; any other query fails loudly, so the test cannot drift from the real path."""

    row_factory = None

    def __init__(self, *, objects, revisions, principals, assignments, policies):
        self.objects, self.revisions, self.principals = objects, revisions, principals
        self.assignments, self.policies = assignments, policies

    def execute(self, sql, params=()):
        rows = self._rows(' '.join(sql.split()), [str(value) for value in params])
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)

    def _rows(self, s, p):
        if 'gov_workspace_v02_assets' in s:
            return []
        if s.startswith('SELECT active FROM gov_principals'):
            return [{'active': True}] if p[1] in self.principals else []
        if 'JOIN gov_principals p' in s:                               # method_access.assignment
            return [{**a, 'principal_type': self.principals[a['principal_id']]}
                    for a in self.assignments if a['assignment_id'] == p[1]]
        if 'FROM gov_role_assignments' in s:
            if 'ORDER BY domain_id, role, assignment_id' in s:          # db._assignments
                return [a for a in self.assignments if a['principal_id'] == p[1]]
            if "principal_id=%s AND role='CEO'" in s:                   # company-kind resolver
                return [a for a in self.assignments if a['principal_id'] == p[1] and a['role'] == 'CEO']
            if "domain_id=%s AND role='DOMAIN_DRI'" in s:               # Scope DRI resolution
                return [a for a in self.assignments if a['domain_id'] == p[1] and a['role'] == 'DOMAIN_DRI']
            if s == 'SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s':
                return [a for a in self.assignments if a['principal_id'] == p[1]]
        if 'FROM gov_activation_policies' in s:
            return [{'policy_revision_id': str(uuid4()), 'content': self.policies[p[1]]}] if p[1] in self.policies else []
        if any(marker in s for marker in ("'ReviewWindow'", "'StrategicIssue'", "'OperatingProblem'",
                                          "'OperatingState'", "o.object_type='Mission'", "IN ('LTCO','PCO','Mission')")):
            return []                   # no windows, issues, problems, states, Missions or effective results here
        if 'FROM gov_object_protocol_bindings' in s:
            return ([{'object_id': p[1], 'binding_version': 1, 'protocol_id': 'tkos.method',
                      'contract_version': 'tkos.method/0.5'}] if p[1] in self.objects else [])
        if "o.object_type='StrategicArchitecture'" in s:
            return [o for o in self.objects.values() if o['object_type'] == 'StrategicArchitecture']
        if s.startswith('SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s'):
            return [self.revisions[(p[1], p[2])]] if (p[1], p[2]) in self.revisions else []
        if s.startswith('SELECT revision_id, payload FROM gov_object_revisions'):
            return [row for (oid, _rid), row in self.revisions.items() if oid == p[1]]
        if s.startswith('SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s'):
            return [self.objects[p[1]]] if p[1] in self.objects else []
        raise AssertionError('query outside the head_access -> constraint_grants path: ' + s)


def test_a_company_constraint_grants_no_read_to_a_ceo_of_another_domain(monkeypatch):
    """The Constraint read grant exists only for the scope/mission confirmers, who hold no read on
    the company domain.  A company Constraint's confirmer is the CEO of the Constraint's own domain,
    who reads it through that domain.  Resolving company revisions through the scoped fallback
    accepted the caller's first current CEO appointment in *any* domain, so a CEO appointed only
    elsewhere (e.g. after losing the company appointment) read every company Constraint.  Driven
    through the real head_access, constraint_grants and resolver; only SQL rows are fake."""
    scope, company, auth_a, elsewhere = (str(uuid4()) for _ in range(4))
    ceo_elsewhere, dri_a = str(uuid4()), str(uuid4())
    arch, arch_r, company_c, company_r, scope_c, scope_r = (str(uuid4()) for _ in range(6))

    def head(oid, kind, rid):
        return {'object_id': oid, 'object_type': kind, 'domain_id': company,
                'latest_revision_id': rid, 'effective_revision_id': rid}

    def revision(oid, rid, payload):
        return {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64, 'payload': payload}

    def appointment(principal, domain, role):
        return {'assignment_id': str(uuid4()), 'scope_id': scope, 'principal_id': principal, 'domain_id': domain,
                'role': role, 'active': True, 'valid_from': '2026-01-01T00:00:00+00:00', 'valid_to': None}

    architecture_ref = {'object_id': arch, 'revision_id': arch_r, 'payload_hash': 'a' * 64}
    conn = _TablesConn(
        objects={arch: head(arch, 'StrategicArchitecture', arch_r), company_c: head(company_c, 'Constraint', company_r),
                 scope_c: head(scope_c, 'Constraint', scope_r)},
        revisions={(arch, arch_r): revision(arch, arch_r, {'battlefields': [], 'domains': [
                       {'unit_id': 'scope-a', 'auth_domain_id': auth_a, 'current_dri_principal_id': dri_a}]}),
                   (company_c, company_r): revision(company_c, company_r, {
                       'applies_to': {'kind': 'company'}, 'evidence_refs': []}),
                   (scope_c, scope_r): revision(scope_c, scope_r, {
                       'applies_to': {'kind': 'scope', 'scope_id': 'scope-a'}, 'architecture_ref': architecture_ref,
                       'evidence_refs': []})},
        principals={ceo_elsewhere: 'human', dri_a: 'human'},
        assignments=[appointment(ceo_elsewhere, elsewhere, 'CEO'), appointment(dri_a, auth_a, 'DOMAIN_DRI')],
        policies={company: {'action_roles': {'read': ['CEO', 'DOMAIN_DRI', 'CO_AGENT']}}})

    def ctx(principal_id):
        return SimpleNamespace(scope_id=scope, principal_id=principal_id, principal_type='human', assignments=[])

    with pytest.raises(GovernedError) as exc:   # a current CEO elsewhere, no read on the company domain
        method_access.head_access(conn, ctx(ceo_elsewhere), company_c)
    assert exc.value.code == 'NOT_FOUND'
    # The skip is kind-specific: the scope Constraint still grants exactly its resolved Scope DRI ...
    assert method_access.head_access(conn, ctx(dri_a), scope_c) == (conn.objects[scope_c], {scope_r})
    with pytest.raises(GovernedError) as exc:   # ... and nobody else.
        method_access.head_access(conn, ctx(ceo_elsewhere), scope_c)
    assert exc.value.code == 'NOT_FOUND'


class _HeadsConn:
    """Serves the server-side resolver's head lookups by object_id; nothing else is queried."""

    def __init__(self, heads):
        self.heads = heads

    def execute(self, sql, params=None):
        row = self.heads.get(str(params[1])) if 'FROM gov_objects' in sql else None
        return SimpleNamespace(fetchone=lambda: dict(row) if row else None)


def test_mission_constraint_confirmer_is_resolved_server_side_not_through_the_callers_grants(monkeypatch):
    """Contract §2: a mission Constraint is confirmed by the current DRI of that Mission's primary
    Scope.  While the Mission is still a draft (before any review window) that DRI holds no read
    grant on the Mission or on its parent PCO, so resolving the confirmer through the caller's
    grants answered NOT_FOUND (real HTTP/PG acceptance, Task 12).  As in the scope branch, the
    Mission and its parent PCO are only the responsibility basis and are read server-side; the
    Mission's own Scope DRI still resolves, and nobody else passes."""
    mission_dri, other = str(uuid4()), str(uuid4())
    monkeypatch.setattr(method_v04, '_pco_dri', lambda _e, payload: (
        {'scope-a': mission_dri}.get(payload['primary_scope_id'], other), {'assignment_id': str(uuid4())}))
    pco_oid, pco_rid, mission_oid, mission_rid = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    parent_pco_ref = {'object_id': pco_oid, 'revision_id': pco_rid, 'payload_hash': 'a' * 64}
    mission_ref = {'object_id': mission_oid, 'revision_id': mission_rid, 'payload_hash': 'a' * 64}
    revisions = {
        (pco_oid, pco_rid): {'object_id': pco_oid, 'revision_id': pco_rid, 'payload_hash': 'a' * 64,
                             'payload': {'architecture_ref': ref(), 'primary_scope_id': 'scope-b'}},
        (mission_oid, mission_rid): {'object_id': mission_oid, 'revision_id': mission_rid, 'payload_hash': 'a' * 64,
                                     'payload': {'parent_pco_ref': parent_pco_ref, 'primary_scope_id': 'scope-a'}},
    }
    heads = {pco_oid: {'object_id': pco_oid, 'object_type': 'PCO', 'effective_revision_id': None,
                       'latest_revision_id': pco_rid},
             mission_oid: {'object_id': mission_oid, 'object_type': 'Mission', 'effective_revision_id': None,
                           'latest_revision_id': mission_rid}}
    monkeypatch.setattr(method_access, 'raw_revision', lambda conn, ctx, oid, rid: dict(revisions[(str(oid), str(rid))]))
    monkeypatch.setattr(protocol, 'current_binding', lambda conn, scope_id, oid: {'contract_version': 'tkos.method/0.5'})

    def caller_cannot_read(reference, types=None, effective=False, current=True):
        raise GovernedError('NOT_FOUND')   # the draft Mission and PCO are invisible to the Scope DRI

    constraint_oid = str(uuid4())
    e = _fake_execution(refs={}, states={constraint_oid: {'phase': 'draft'}})
    e.ref, e.conn = caller_cannot_read, _HeadsConn(heads)
    e.target = {'object_id': constraint_oid, 'object_type': 'Constraint'}
    e.target_revision = {'object_id': constraint_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                         'payload': {'applies_to': {'kind': 'mission', 'mission_ref': mission_ref},
                                     'evidence_refs': []}}

    def require_actor(principal_id, principal_type='human'):
        if e.ctx.principal_id != principal_id or e.ctx.principal_type != principal_type:
            raise GovernedError('FORBIDDEN')
    e.require_actor = require_actor

    e.ctx.principal_id = other
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_confirm_constraint(e)
    assert exc.value.code == 'FORBIDDEN'
    e.ctx.principal_id = mission_dri
    method_v05._collect_confirm_constraint(e)
    assert e._v04['constraint_owner'] == mission_dri


def _ltco_target(phase, effective):
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'LTCO', 'domain_id': str(uuid4()),
            'effective_revision_id': rid if effective else None, 'latest_revision_id': rid}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64,
                'payload': {'primary_scope_id': 'bf-1', 'constraint_refs': []}}
    return head, revision, {oid: {'phase': phase}}


def test_ltco_conclusion_must_match_the_objects_history(monkeypatch):
    monkeypatch.setattr(method_v04, '_ltco_check', lambda e, payload: None)
    monkeypatch.setattr(method_v05, '_current_ceo', lambda e, domain_id: (e.ctx.principal_id, {'assignment_id': 'a'}))
    for phase, effective, conclusion, ok in [('draft', False, 'established', True), ('draft', False, 'revised', False),
                                             ('draft', True, 'revised', True), ('draft', True, 'established', False),
                                             ('draft', True, 'maintained', False), ('confirmed', True, 'maintained', True),
                                             ('confirmed', True, 'revised', False)]:
        head, revision, states = _ltco_target(phase, effective)
        e = _fake_execution(refs={}, states=states)
        e.target, e.target_revision = head, revision
        e.params = {'conclusion': conclusion, 'statement': 'reason'}
        e.require_actor = lambda principal, kind: None
        if ok:
            method_v05._collect_confirm_ltco(e)
        else:
            with pytest.raises(GovernedError) as exc:
                method_v05._collect_confirm_ltco(e)
            assert exc.value.code == 'INVALID_REQUEST', (phase, effective, conclusion)
    for kind in ('m1b_propose_ltco', 'm1b_revise_ltco', 'm1b_confirm_ltco'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS


def test_revise_ltco_state_carries_last_review_but_drops_everything_else():
    """Deviation from the brief (see _run_revise_ltco): a revise resets state to
    {'phase': 'draft'}, carrying the prior last_review forward only when present --
    never a stale confirmation_record_id, and never a written last_review: None."""

    def _revise_case(prior_state):
        oid, rid = str(uuid4()), str(uuid4())
        head = {'object_id': oid}
        revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}
        written = {}
        e = SimpleNamespace(target=head, params={'payload': {}, 'response': 'ack'},
                            revise=lambda target, payload: (head, revision),
                            state=lambda h: prior_state,
                            set_state=lambda h, s: written.update(state=s),
                            review=lambda kind, ref, content: str(uuid4()))
        method_v05._run_revise_ltco(e)
        return written['state']

    last_review = {'conclusion': 'maintained', 'record_id': 'r1'}
    assert _revise_case({'phase': 'confirmed', 'confirmation_record_id': 'r1', 'last_review': last_review}) == \
        {'phase': 'draft', 'last_review': last_review}
    assert _revise_case({'phase': 'confirmed', 'confirmation_record_id': 'r1'}) == {'phase': 'draft'}


# ------------------------------------------------------ Period Review / PCO


def _review(phase, end, effective=True):
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'PeriodReview', 'effective_revision_id': rid if effective else None}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64,
                'payload': {'period': {'start': '2026-09-01T00:00:00Z', 'end': end}}}
    return oid, head, revision, {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}, phase


class _RowsConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        rows = self.rows
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)


def test_pco_must_cite_a_confirmed_earlier_period_review_when_one_exists(monkeypatch):
    confirmed = _review('confirmed', '2026-09-30T00:00:00Z')
    generated = _review('generated', '2026-09-30T00:00:00Z', effective=False)
    late = _review('confirmed', '2026-10-15T00:00:00Z')
    refs = {r[0]: (r[1], r[2]) for r in (confirmed, generated, late)}
    states = {r[0]: {'phase': r[4]} for r in (confirmed, generated, late)}
    payload = {'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}}
    e = _fake_execution(refs=refs, states=states)
    e.conn = _RowsConn([])
    method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': confirmed[3]})
    with pytest.raises(GovernedError) as exc:
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': generated[3]})
    assert exc.value.code == 'STALE_DEPENDENCY'
    with pytest.raises(GovernedError) as exc:
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': late[3]})
    assert exc.value.code == 'INVALID_REQUEST'
    method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': None})   # first period
    e.conn = _RowsConn([{'object_id': confirmed[0], 'payload': confirmed[2]['payload']}])
    # this scope's copy of `confirmed` is bound to 0.5 itself, so it still counts (see the
    # dedicated cross-version test below for the 0.4-bound / unbound cases the fix addresses).
    monkeypatch.setattr(protocol, 'current_binding',
                        lambda conn, scope_id, object_id: {'contract_version': method_v05.CONTRACT_VERSION})
    with pytest.raises(GovernedError) as exc:  # a confirmed earlier review exists and must be cited
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': None})
    assert exc.value.code == 'INVALID_REQUEST'
    for kind in ('m1b_confirm_review', 'm1b_generate_review', 'm1b_regenerate_review',
                'm1b_draft_pco', 'm1b_revise_pco', 'm1b_resolve_window'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS


def test_period_review_gate_only_counts_reviews_currently_bound_to_0_5(monkeypatch):
    """Controller finding (fix round 1): a scope can still hold PeriodReview objects left from an
    in-place protocol upgrade, bound to an older Method version (gov_protocol_policies is per
    (scope, domain) with a sequence; an in-place upgrade does not migrate old bindings).  0.4 makes
    a PeriodReview effective at generation with no confirmation, so counting it here would deadlock
    the first 0.5 PCO -- it could neither cite it (e.ref -> gate_dependency ->
    PROTOCOL_BINDING_CONFLICT, a mismatched contract_version) nor omit it (this SQL would still see
    it and refuse INVALID_REQUEST) -- and would flout contract Sec1 "不就地重解释历史" by treating a
    0.4 review as if it were 0.5-confirmed.  Only a review whose CURRENT binding is 0.5 must count."""
    confirmed = _review('confirmed', '2026-09-30T00:00:00Z')
    payload = {'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}}
    e = _fake_execution(refs={}, states={})
    e.conn = _RowsConn([{'object_id': confirmed[0], 'payload': confirmed[2]['payload']}])

    for binding in (None, {'contract_version': 'tkos.method/0.4'}):   # no binding, or an older one
        monkeypatch.setattr(protocol, 'current_binding', lambda conn, scope_id, object_id, b=binding: b)
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': None})   # does not block

    monkeypatch.setattr(protocol, 'current_binding',
                        lambda conn, scope_id, object_id: {'contract_version': method_v05.CONTRACT_VERSION})
    with pytest.raises(GovernedError) as exc:   # the same row, now bound to 0.5, blocks again
        method_v05._check_period_review_ref(e, {**payload, 'period_review_ref': None})
    assert exc.value.code == 'INVALID_REQUEST'


def test_generate_review_runner_does_not_make_it_effective():
    """T7-a: the brief's plan delegated m1b_generate_review to 0.4 unchanged, whose runner
    passes status="recorded" to create() -- create() marks status in {active, confirmed,
    recorded, stored} effective immediately (method_service.py ~277), so every 0.5 generated
    review would be CEO-confirmed-effective before any CEO confirmation.  0.5's own runner must
    omit status (create()'s default is "draft", which is not in that effective-making set)."""
    payload = {'review_id': 'r1', 'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}}
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}
    created = {}

    def create(object_type, payload_arg, **kwargs):
        created.update(object_type=object_type, payload=payload_arg, kwargs=kwargs)
        return head, revision

    written = {}
    e = SimpleNamespace(params={'payload': payload}, create=create,
                        set_state=lambda h, s: written.update(state=s))
    method_v05._run_generate_review(e)
    assert created == {'object_type': 'PeriodReview', 'payload': payload, 'kwargs': {}}
    assert created['kwargs'].get('status') not in {'active', 'confirmed', 'recorded', 'stored'}
    assert written['state'] == {'phase': 'generated'}


def test_regenerate_review_runner_keeps_lifecycle_and_carries_agent_generation_ref_only_if_present():
    """T7-b: after T7-a, m1b_regenerate_review must not pass status="recorded" either -- a
    recorded-but-not-effective revision would break the codebase convention recorded => effective.
    No status/effective goes to revise() at all, leaving e.target's lifecycle untouched (effective
    only ever comes from m1b_confirm_review).  State carry-over: state.agent_generation_ref (the
    confirmed version's agent-draft provenance, contract Sec4) is kept only when the prior state
    actually had one; everything else, e.g. a stale confirmation_record_id, resets."""

    def _regenerate_case(prior_state):
        oid, rid = str(uuid4()), str(uuid4())
        head = {'object_id': oid}
        revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}
        revise_calls = []

        def revise(target, payload, **kwargs):
            revise_calls.append((target, payload, kwargs))
            return head, revision

        written = {}
        e = SimpleNamespace(target=head, params={'payload': {'title': 'Q4'}},
                            revise=revise, state=lambda h: prior_state,
                            set_state=lambda h, s: written.update(state=s))
        method_v05._run_regenerate_review(e)
        assert revise_calls == [(head, {'title': 'Q4'}, {})]   # no status, no effective
        return written['state']

    assert _regenerate_case({'phase': 'confirmed', 'confirmation_record_id': 'r1',
                             'agent_generation_ref': {'object_id': 'g1'}}) == \
        {'phase': 'generated', 'agent_generation_ref': {'object_id': 'g1'}}
    assert _regenerate_case({'phase': 'confirmed', 'confirmation_record_id': 'r1'}) == \
        {'phase': 'generated'}


def test_resolve_window_candidates_must_retain_their_period_review_ref(monkeypatch):
    """T7-d: contract Sec4 "候选 PCO 保留起草时的 period_review_ref" as value retention -- the
    same STALE_DEPENDENCY code 0.4 uses for its own window retention checks (strategy/architecture/
    parent LTCO/period/primary_scope_id, method_v04.py ~869-877).  method_v04.collect is faked to
    populate e._m1b the way the real 0.4 _collect_resolve_window does, and _check_pco_extras is
    stubbed out (it has its own coverage) so this isolates the retention check alone."""
    monkeypatch.setattr(method_v05, '_check_pco_extras', lambda e, payload: None)
    pco_oid = str(uuid4())
    same, other = ref(), ref()

    def _case(candidate_ref, frozen_ref):
        def fake_collect(e):
            e._m1b = {'pcos': {pco_oid: {'revision': {'payload':
                     {} if frozen_ref is None else {'period_review_ref': frozen_ref}}}}}
        monkeypatch.setattr(method_v04, 'collect', fake_collect)
        candidate_payload = {} if candidate_ref is None else {'period_review_ref': candidate_ref}
        e = SimpleNamespace(params={'pcos': [{'object_id': pco_oid, 'payload': candidate_payload}]})
        method_v05._collect_resolve_window(e)

    with pytest.raises(GovernedError) as exc:
        _case(other, same)          # candidate ref != frozen ref
    assert exc.value.code == 'STALE_DEPENDENCY'
    with pytest.raises(GovernedError) as exc:
        _case(None, same)           # frozen ref given, candidate dropped it
    assert exc.value.code == 'STALE_DEPENDENCY'
    _case(same, same)               # identical refs: passes
    _case(None, None)               # both None (e.g. first period): passes


# --------------------------------------------------------------- Mission


def _architecture_env():
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid, 'object_type': 'StrategicArchitecture'}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64,
                'payload': {'battlefields': [{'unit_id': 'bf-1'}], 'domains': [{'unit_id': 'dom-1'}]}}
    return {oid: (head, revision)}, {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}


def _mission(**updates):
    return {'primary_scope_id': 'bf-1', 'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-15T00:00:00Z'},
            'contributes_to_scope_ids': ['dom-1'], 'dependencies': [], 'constraint_refs': [], **updates}


def test_mission_contributions_and_dependencies_bind_to_the_exact_architecture():
    refs, architecture = _architecture_env()
    e = _fake_execution(refs=refs)
    method_v05._check_mission_extras(e, _mission(), architecture_ref=architecture)
    with pytest.raises(GovernedError):  # contributing to its own primary Scope
        method_v05._check_mission_extras(e, _mission(contributes_to_scope_ids=['bf-1']), architecture_ref=architecture)
    with pytest.raises(GovernedError):  # unknown unit
        method_v05._check_mission_extras(e, _mission(contributes_to_scope_ids=['bf-9']), architecture_ref=architecture)
    inside = {'kind': 'scope', 'scope_id': 'dom-1', 'needed_by': '2026-10-10T00:00:00Z', 'note': 'n'}
    outside = {**inside, 'needed_by': '2026-11-10T00:00:00Z'}
    method_v05._check_mission_extras(e, _mission(dependencies=[inside]), architecture_ref=architecture)
    with pytest.raises(GovernedError):
        method_v05._check_mission_extras(e, _mission(dependencies=[outside]), architecture_ref=architecture)


def test_only_pco_responsibilities_need_a_commitment(monkeypatch):
    pco_id, mission_id = str(uuid4()), str(uuid4())
    heads = {pco_id: {'object_id': pco_id, 'object_type': 'PCO'}, mission_id: {'object_id': mission_id, 'object_type': 'Mission'}}
    e = SimpleNamespace(ref=lambda reference, **kw: None, head=lambda oid: heads[oid],
                        revision=lambda oid, rid: {'payload': {'primary_scope_id': 'bf-1'}})
    monkeypatch.setattr(method_v04, '_pco_responsibility', lambda e, payload: ('dri', 'DOMAIN_DRI', 'auth', {'assignment_id': 'x'}))
    payload = {'target_refs': [{'object_id': pco_id, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64},
                               {'object_id': mission_id, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64}]}
    required = method_v05._required_committers(e, payload)
    assert set(required) == {pco_id} and required[pco_id]['owner'] == 'dri' and required[pco_id]['role'] == 'DOMAIN_DRI'
    assert method_v05.required_committers is method_v05._required_committers
    for kind in ('m1b_draft_mission', 'm1b_revise_mission', 'm1b_commit_candidate', 'm1b_activate_candidates'):
        assert kind in method_v05.COLLECTORS and kind in method_v05.RUNNERS


# ---------------------------- pin: the PCO-only-committer deadlock closure
#
# Before 0.5 got its own _required_committers, this module inherited 0.4's
# version, which also lists a Mission's Owner as a required committer -- while
# 0.5's own scoped_assignment (m1b_commit_candidate branch, Task 4) and
# _collect_commit_candidate (this task) already refuse to let ANYONE commit a
# Mission directly (contract Sec5).  Any candidate set naming a Mission could
# then never collect every "required" commitment: activation was unreachable.
# The two tests below pin both halves of the closure directly against the
# real _collect_commit_candidate / _collect_activate_candidates, not just
# against _required_committers in isolation.

def _pco_and_mission():
    """A (PCO, Mission) pair of object/revision/ref triples for candidate-set fixtures."""
    pco_oid, mission_oid = str(uuid4()), str(uuid4())
    pco_head = {'object_id': pco_oid, 'object_type': 'PCO'}
    pco_revision = {'object_id': pco_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    mission_head = {'object_id': mission_oid, 'object_type': 'Mission'}
    mission_revision = {'object_id': mission_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    pco_ref = {'object_id': pco_oid, 'revision_id': pco_revision['revision_id'], 'payload_hash': 'a' * 64}
    mission_ref = {'object_id': mission_oid, 'revision_id': mission_revision['revision_id'], 'payload_hash': 'a' * 64}
    return (pco_oid, pco_head, pco_revision, pco_ref), (mission_oid, mission_head, mission_revision, mission_ref)


def test_commit_candidate_refuses_a_mission_responsibility_ref_even_for_the_pcos_own_dri(monkeypatch):
    """Pin (a): contract Sec5 "m1b_commit_candidate 指向 Mission 一律拒绝（INVALID_REQUEST）"
    holds even for someone who genuinely is the named PCO's current DRI -- the refusal turns on
    the target's object type, not on whether the caller's identity would otherwise pass."""
    (pco_oid, pco_head, pco_revision, pco_ref), (mission_oid, mission_head, _mrev, mission_ref) = _pco_and_mission()
    candidate_oid = str(uuid4())
    candidate_revision = {'object_id': candidate_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                          'payload': {'target_refs': [pco_ref, mission_ref]}}
    refs = {pco_oid: (pco_head, pco_revision), mission_oid: (mission_head, _mrev)}
    e = _fake_execution(refs=refs, states={candidate_oid: {'phase': 'pending'}})
    e.target, e.target_revision = {'object_id': candidate_oid}, candidate_revision
    e.head = lambda oid: {pco_oid: pco_head, mission_oid: mission_head}[oid]
    e.params = {'responsibility_ref': mission_ref}
    dri = str(uuid4())
    e.ctx.principal_id = dri   # genuinely the PCO's own current DRI (see the mock below)
    monkeypatch.setattr(method_v04, '_candidate_basis_current', lambda e, payload: None)
    monkeypatch.setattr(method_v04, '_pco_responsibility',
                        lambda e, payload: (dri, 'DOMAIN_DRI', str(uuid4()), {'assignment_id': 'a1'}))
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_commit_candidate(e)
    assert exc.value.code == 'INVALID_REQUEST'


def test_activate_candidates_succeeds_when_only_the_pco_dri_committed(monkeypatch):
    """Pin (b): the exact scenario that used to deadlock -- a pending candidate set naming one
    PCO and one Mission, with only the PCO DRI's commitment recorded (pin (a) above shows no
    other commitment can ever exist for the Mission). Activation must succeed, and the Mission
    must never appear in the required-committer set that gated it."""
    (pco_oid, pco_head, pco_revision, pco_ref), (mission_oid, mission_head, mission_revision, mission_ref) = _pco_and_mission()
    mission_revision = {**mission_revision, 'payload': {'owner_principal_id': str(uuid4())}}
    window_oid = str(uuid4())
    window_head = {'object_id': window_oid, 'object_type': 'ReviewWindow'}
    window_revision = {'object_id': window_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    window_ref = {'object_id': window_oid, 'revision_id': window_revision['revision_id'], 'payload_hash': 'a' * 64}
    candidate_oid = str(uuid4())
    candidate_revision = {'object_id': candidate_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                          'payload': {'target_refs': [pco_ref, mission_ref], 'window_ref': window_ref,
                                      'unresolved_differences': []}}
    candidate_ref = {'object_id': candidate_oid, 'revision_id': candidate_revision['revision_id'], 'payload_hash': 'a' * 64}
    refs = {pco_oid: (pco_head, pco_revision), mission_oid: (mission_head, mission_revision),
            window_oid: (window_head, window_revision)}
    states = {candidate_oid: {'phase': 'pending'},
              pco_oid: {'phase': 'candidate', 'candidate_ref': candidate_ref},
              mission_oid: {'phase': 'candidate', 'candidate_ref': candidate_ref},
              window_oid: {'phase': 'resolved'}}
    e = _fake_execution(refs=refs, states=states)
    e.target, e.target_revision = {'object_id': candidate_oid}, candidate_revision
    e.head = lambda oid: {pco_oid: pco_head, mission_oid: mission_head, window_oid: window_head}[oid]
    e.revision = lambda oid, rid: {pco_oid: pco_revision, mission_oid: mission_revision}[oid]
    e.validate_principal = lambda principal_id, principal_type='human': {'assignment_id': 'owner-a1'}   # Owner is current
    dri = str(uuid4())
    e.conn = _RowsConn([{'responsibility_object_id': pco_oid, 'responsibility_revision_id': pco_revision['revision_id'],
                         'principal_id': dri, 'assignment_id': 'a1'}])
    e.validate_assignment = lambda assignment_id, principal_id, principal_type: None
    domain_id = str(uuid4())
    monkeypatch.setattr(method_v05, '_human_ceo', lambda e: None)
    monkeypatch.setattr(method_v04, '_candidate_basis_current', lambda e, payload: None)
    monkeypatch.setattr(method_v04, '_pco_responsibility',
                        lambda e, payload: (dri, 'DOMAIN_DRI', domain_id, {'assignment_id': 'a1'}))
    monkeypatch.setattr(method_access, 'assignment',
                        lambda conn, ctx, assignment_id, principal_id, principal_type:
                            {'role': 'DOMAIN_DRI', 'domain_id': domain_id, 'assignment_id': assignment_id})
    method_v05._collect_activate_candidates(e)
    assert set(e._v04['activation']['required']) == {pco_oid}   # the Mission was never required


# ------------------------- fix round 1: candidate Mission Owner re-validation
#
# Finding 1: 0.5 removed the Mission's own required commitment (contract Sec5),
# which in 0.4 was what proved a candidate Mission's Owner was a current human
# principal, at both commit and activation (_required_committers ->
# _mission_owner -> validate_principal). Nothing replaced that proof, so a
# candidate naming a never-validated or since-revoked Owner could resolve and
# activate, and activation now stamps state.owner_activation_record_id on it.
# Finding 2: the fix must not duplicate its checks between the collector and
# the activation_blockers projection, so these tests probe both.

def _activation_fixture(*, owner_ok):
    """One resolved-window CandidateSet naming one PCO (its DRI will be the sole committer)
    and one Mission, for activation-path tests. owner_ok=False makes the Mission's
    owner_principal_id fail principal validation."""
    (pco_oid, pco_head, pco_revision, pco_ref), (mission_oid, mission_head, mission_revision, mission_ref) = _pco_and_mission()
    owner = str(uuid4())
    mission_revision = {**mission_revision, 'payload': {'owner_principal_id': owner}}
    window_oid = str(uuid4())
    window_head = {'object_id': window_oid, 'object_type': 'ReviewWindow'}
    window_revision = {'object_id': window_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    window_ref = {'object_id': window_oid, 'revision_id': window_revision['revision_id'], 'payload_hash': 'a' * 64}
    candidate_oid = str(uuid4())
    candidate_revision = {'object_id': candidate_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                          'payload': {'target_refs': [pco_ref, mission_ref], 'window_ref': window_ref,
                                      'unresolved_differences': []}}
    candidate_head = {'object_id': candidate_oid, 'object_type': 'CandidateSet'}
    candidate_ref = {'object_id': candidate_oid, 'revision_id': candidate_revision['revision_id'], 'payload_hash': 'a' * 64}
    dri = str(uuid4())
    heads = {pco_oid: pco_head, mission_oid: mission_head, window_oid: window_head, candidate_oid: candidate_head}
    revisions = {pco_oid: pco_revision, mission_oid: mission_revision, window_oid: window_revision,
                candidate_oid: candidate_revision}
    refs = {pco_oid: (pco_head, pco_revision), mission_oid: (mission_head, mission_revision),
            window_oid: (window_head, window_revision), candidate_oid: (candidate_head, candidate_revision)}
    states = {candidate_oid: {'phase': 'pending'},
              pco_oid: {'phase': 'candidate', 'candidate_ref': candidate_ref},
              mission_oid: {'phase': 'candidate', 'candidate_ref': candidate_ref},
              window_oid: {'phase': 'resolved'}}
    commitment_rows = [{'responsibility_object_id': pco_oid, 'responsibility_revision_id': pco_revision['revision_id'],
                        'principal_id': dri, 'assignment_id': 'a1'}]

    def validate_principal(principal_id, principal_type='human'):
        if not owner_ok:
            raise GovernedError('FORBIDDEN')
        return {'assignment_id': 'owner-a1'}

    return SimpleNamespace(pco_oid=pco_oid, mission_oid=mission_oid, window_oid=window_oid, candidate_oid=candidate_oid,
                           heads=heads, revisions=revisions, refs=refs, states=states, dri=dri, owner=owner,
                           candidate_revision=candidate_revision, candidate_ref=candidate_ref,
                           commitment_rows=commitment_rows, validate_principal=validate_principal)


def test_resolve_window_refuses_a_candidate_mission_whose_owner_fails_validation(monkeypatch):
    """Finding 1(a): resolution must validate the CANDIDATE's owner_principal_id (which can
    rename the Owner away from the frozen revision's -- CandidateMission carries its own field)
    with v4._mission_owner, exactly as 0.4 validates the frozen Owner when a window opens. Its
    error propagates unmodified (FORBIDDEN, from validate_principal)."""
    mission_oid = str(uuid4())
    mission_head = {'object_id': mission_oid, 'object_type': 'Mission'}
    mission_revision = {'object_id': mission_oid, 'revision_id': str(uuid4()), 'payload_hash': 'a' * 64, 'payload': {}}
    candidate = {'object_id': mission_oid, 'owner_principal_id': str(uuid4())}
    e = SimpleNamespace(target_revision={'payload': {}}, params={'missions': [candidate]},
                        validate_principal=lambda principal_id, principal_type='human':
                            (_ for _ in ()).throw(GovernedError('FORBIDDEN')))
    monkeypatch.setattr(method_v05, '_collect_resolve_window',
                        lambda e: setattr(e, '_m1b', {'missions': {mission_oid: {'head': mission_head,
                                                                                 'revision': mission_revision}}}))
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_resolve_window_missions(e)
    assert exc.value.code == 'FORBIDDEN'


def test_activate_candidates_and_blockers_agree_when_a_missions_owner_is_invalid(monkeypatch):
    """Finding 1(b)/(c) and Finding 2's shared helper: activation must re-validate every Mission
    member's Owner even though Missions are never required committers (pin (b) above), and the
    read-only projection must report the same reason -- mission_owner_invalid -- rather than
    silently allowing what the collector refuses."""
    fx = _activation_fixture(owner_ok=False)
    domain_id = str(uuid4())
    monkeypatch.setattr(method_v04, '_candidate_basis_current', lambda e, payload: None)
    monkeypatch.setattr(method_v04, '_pco_responsibility',
                        lambda e, payload: (fx.dri, 'DOMAIN_DRI', domain_id, {'assignment_id': 'a1'}))
    monkeypatch.setattr(method_access, 'assignment',
                        lambda conn, ctx, assignment_id, principal_id, principal_type:
                            {'role': 'DOMAIN_DRI', 'domain_id': domain_id, 'assignment_id': assignment_id})

    # collector half: INVALID_STATE, not the FORBIDDEN validate_principal itself raises
    e = _fake_execution(refs=fx.refs, states=fx.states)
    e.target, e.target_revision = {'object_id': fx.candidate_oid}, fx.candidate_revision
    e.head = lambda oid: fx.heads[oid]
    e.revision = lambda oid, rid: fx.revisions[oid]
    e.conn = _RowsConn(fx.commitment_rows)
    e.validate_assignment = lambda assignment_id, principal_id, principal_type: None
    e.validate_principal = fx.validate_principal
    monkeypatch.setattr(method_v05, '_human_ceo', lambda e: None)
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_activate_candidates(e)
    assert exc.value.code == 'INVALID_STATE'

    # projection half: the same underlying scenario, through activation_blockers
    context = _fake_execution(refs=fx.refs, states=fx.states)
    context.head = lambda oid: fx.heads[oid]
    context.revision = lambda oid, rid: fx.revisions[oid]
    context.validate_principal = fx.validate_principal
    monkeypatch.setattr(method_v05, 'light', lambda conn, ctx: context)
    conn = _RowsConn(fx.commitment_rows)
    ctx = SimpleNamespace(scope_id=str(uuid4()))
    obj = {'object_id': fx.candidate_oid, 'latest_revision': fx.candidate_revision}
    assert method_v05.activation_blockers(conn, ctx, obj) == ['mission_owner_invalid']


def test_activation_blockers_is_empty_for_the_deadlock_fix_scenario(monkeypatch):
    """The workbench half of the deadlock fix pinned above (test_activate_candidates_succeeds_
    when_only_the_pco_dri_committed): the same pending PCO+Mission candidate set, only the PCO
    DRI committed, Owner valid -- activation_blockers must agree it is ready to activate."""
    fx = _activation_fixture(owner_ok=True)
    domain_id = str(uuid4())
    context = _fake_execution(refs=fx.refs, states=fx.states)
    context.head = lambda oid: fx.heads[oid]
    context.revision = lambda oid, rid: fx.revisions[oid]
    context.validate_principal = fx.validate_principal
    monkeypatch.setattr(method_v05, 'light', lambda conn, ctx: context)
    monkeypatch.setattr(method_v04, '_candidate_basis_current', lambda e, payload: None)
    monkeypatch.setattr(method_v04, '_pco_responsibility',
                        lambda e, payload: (fx.dri, 'DOMAIN_DRI', domain_id, {'assignment_id': 'a1'}))
    monkeypatch.setattr(method_access, 'assignment',
                        lambda conn, ctx, assignment_id, principal_id, principal_type:
                            {'role': 'DOMAIN_DRI', 'domain_id': domain_id, 'assignment_id': assignment_id})
    conn = _RowsConn(fx.commitment_rows)
    ctx = SimpleNamespace(scope_id=str(uuid4()))
    obj = {'object_id': fx.candidate_oid, 'latest_revision': fx.candidate_revision}
    assert method_v05.activation_blockers(conn, ctx, obj) == []


# --------------------------------------------------------- Operating State


def test_drilldown_refs_must_be_canonical_states_of_other_subjects(monkeypatch):
    monkeypatch.setattr(method_v04, '_collect_propose_state', lambda e: None)
    subject = ref()
    lower_id, lower_rid = str(uuid4()), str(uuid4())
    lower_ref = {'object_id': lower_id, 'revision_id': lower_rid, 'payload_hash': 'a' * 64}
    lower_head = {'object_id': lower_id, 'object_type': 'OperatingState'}
    lower_revision = {'object_id': lower_id, 'revision_id': lower_rid, 'payload_hash': 'a' * 64, 'payload': {'subject_ref': ref()}}
    e = _fake_execution(refs={lower_id: (lower_head, lower_revision)}, states={lower_id: {'canonical_ref': lower_ref}})
    e.conn = _RowsConn([])
    e.params = {'payload': {'subject_ref': subject, 'as_of': '2026-10-31T00:00:00Z',
                            'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'},
                            'drilldown_refs': [lower_ref]}}
    method_v05._collect_propose_state(e)
    e.params['payload']['drilldown_refs'] = [{**lower_ref, 'revision_id': str(uuid4())}]
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_propose_state(e)
    assert exc.value.code == 'STALE_DEPENDENCY'
    lower_revision['payload']['subject_ref'] = subject
    e.params['payload']['drilldown_refs'] = [lower_ref]
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_propose_state(e)
    assert exc.value.code == 'INVALID_REQUEST'
    assert 'method_propose_state' in method_v05.COLLECTORS and 'method_propose_state' in method_v05.RUNNERS
    assert 'method_confirm_state' not in method_v05.COLLECTORS


def test_regenerate_must_preserve_the_state_identity_and_period(monkeypatch):
    """Fix round 1: the regenerate branch (previous_state_ref set) was untested. 0.5 adds a
    period check on top of 0.4's own previous_state_ref validation (method_v04.py ~1217, which
    only compares subject_ref and as_of): the cited previous State's period must match exactly,
    or VERSION_CONFLICT (contract Sec6, "同主体、同 period"). The new-identity "already have a
    State" lookup (the else branch, against gov_method_state_keys) must never run on this
    branch -- e.conn is seeded with a row that would raise the OTHER VERSION_CONFLICT message
    if that branch were mistakenly reached, so a regression that falls through is caught by the
    message assertion below rather than passing silently on a coincidentally-matching code."""
    monkeypatch.setattr(method_v04, '_collect_propose_state', lambda e: None)
    prev_id, prev_rid = str(uuid4()), str(uuid4())
    prev_ref = {'object_id': prev_id, 'revision_id': prev_rid, 'payload_hash': 'a' * 64}
    prev_head = {'object_id': prev_id, 'object_type': 'OperatingState'}
    period = {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}
    prev_revision = {'object_id': prev_id, 'revision_id': prev_rid, 'payload_hash': 'a' * 64,
                     'payload': {'period': period}}
    e = _fake_execution(refs={prev_id: (prev_head, prev_revision)}, states={})
    e.conn = _RowsConn([{'state_id': str(uuid4())}])   # would wrongly fire "already have a State" if consulted
    e.params = {'payload': {'subject_ref': ref(), 'as_of': period['end'], 'period': period, 'drilldown_refs': []},
               'previous_state_ref': prev_ref}
    method_v05._collect_propose_state(e)   # same period: passes without consulting gov_method_state_keys

    different_period = {'start': '2026-09-01T00:00:00Z', 'end': period['end']}   # same as_of, different start
    e.params['payload']['period'] = different_period
    with pytest.raises(GovernedError) as exc:
        method_v05._collect_propose_state(e)
    assert exc.value.code == 'VERSION_CONFLICT'
    assert exc.value.message == 'A new generation must preserve the State identity and period.'


def test_propose_state_runner_makes_a_new_identity_canonical_without_a_second_transition():
    """T9-a: create() with status="active" already sets effective_revision_id to the one
    version this branch ever writes (method_service.py ~277), so the new-identity branch of
    _run_propose_state must not also call e.transition -- that would double the object_version
    bump and the lifecycle event for one governed action. The fake execution below carries no
    `transition` attribute at all, so any reintroduced call fails loudly with AttributeError
    instead of silently passing."""
    oid, rid = str(uuid4()), str(uuid4())
    head = {'object_id': oid}
    revision = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}
    created = {}

    def create(object_type, payload_arg, **kwargs):
        created.update(object_type=object_type, payload=payload_arg, kwargs=kwargs)
        return head, revision

    executed = []

    class _Conn:
        def execute(self, sql, params=None):
            executed.append((sql, params))
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    principal_id = str(uuid4())
    scope_id = str(uuid4())
    payload = {'subject_ref': ref(), 'as_of': '2026-10-31T00:00:00Z',
              'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}, 'drilldown_refs': []}
    written = {}
    e = SimpleNamespace(params={'payload': payload}, create=create, conn=_Conn(),
                        ctx=SimpleNamespace(principal_id=principal_id, principal_type='human', scope_id=scope_id),
                        state=lambda h: {}, set_state=lambda h, s: written.update(state=s))

    result = method_v05._run_propose_state(e)

    assert created == {'object_type': 'OperatingState', 'payload': payload, 'kwargs': {'status': 'active'}}
    reference = {'object_id': oid, 'revision_id': rid, 'payload_hash': 'a' * 64}
    assert len(executed) == 1
    sql, params = executed[0]
    assert 'gov_method_state_keys' in sql and 'INSERT' in sql
    assert params == (scope_id, *method_v04._state_key(payload), oid)
    assert written['state'] == {'phase': 'recorded', 'canonical_ref': reference,
                                'recommendation_ref': reference, 'generated_by': principal_id}
    assert result == {**reference, 'phase': 'recorded', 'nature': 'owner_statement'}


def test_propose_state_runner_regenerates_via_revise_without_touching_state_keys():
    """Fix round 1: the regenerate branch (previous_state_ref set) was untested. It must
    resolve the head via e.ref and call e.revise on it with exactly status="active",
    effective=True -- never e.create, and never an INSERT into gov_method_state_keys (that
    table tracks brand-new identities only; a regeneration reuses the existing one). The fake
    execution below has no `create`/`transition` attribute, so a wrong branch or a reintroduced
    transition call fails loudly with AttributeError instead of silently passing. State
    carry-over: an unrelated pre-existing key in the prior state survives the update; the four
    keys the runner owns are overwritten to point at the freshly revised version."""
    prev_oid, prev_rid = str(uuid4()), str(uuid4())
    prev_head = {'object_id': prev_oid}
    new_rid = str(uuid4())
    new_revision = {'object_id': prev_oid, 'revision_id': new_rid, 'payload_hash': 'b' * 64}
    prev_ref = {'object_id': prev_oid, 'revision_id': prev_rid, 'payload_hash': 'a' * 64}

    ref_calls = []

    def fake_ref(reference, **kwargs):
        ref_calls.append((reference, kwargs))
        return prev_head, {'object_id': prev_oid, 'revision_id': prev_rid, 'payload_hash': 'a' * 64}

    revise_calls = []

    def revise(head, payload_arg, **kwargs):
        revise_calls.append((head, payload_arg, kwargs))
        return prev_head, new_revision

    executed = []

    class _Conn:
        def execute(self, sql, params=None):
            executed.append((sql, params))
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    principal_id = str(uuid4())
    payload = {'subject_ref': ref(), 'as_of': '2026-10-31T00:00:00Z',
              'period': {'start': '2026-10-01T00:00:00Z', 'end': '2026-10-31T00:00:00Z'}, 'drilldown_refs': []}
    prior_state = {'unrelated_key': 'kept', 'phase': 'recorded', 'canonical_ref': {'object_id': 'stale'}}
    written = {}
    e = SimpleNamespace(params={'payload': payload, 'previous_state_ref': prev_ref}, ref=fake_ref, revise=revise,
                        conn=_Conn(), ctx=SimpleNamespace(principal_id=principal_id, principal_type='human'),
                        state=lambda h: prior_state, set_state=lambda h, s: written.update(state=s))

    result = method_v05._run_propose_state(e)

    assert ref_calls == [(prev_ref, {'types': {'OperatingState'}})]
    assert revise_calls == [(prev_head, payload, {'status': 'active', 'effective': True})]
    assert executed == []   # no gov_method_state_keys INSERT on the regenerate branch
    reference = {'object_id': prev_oid, 'revision_id': new_rid, 'payload_hash': 'b' * 64}
    assert written['state'] == {'unrelated_key': 'kept', 'phase': 'recorded', 'canonical_ref': reference,
                                'recommendation_ref': reference, 'generated_by': principal_id}
    assert result == {**reference, 'phase': 'recorded', 'nature': 'owner_statement'}
