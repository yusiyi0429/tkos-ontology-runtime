"""Versioned lifecycle decisions; real HTTP/transaction acceptance is separate."""
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from hashlib import sha256

import pytest
from pydantic import ValidationError
from tests.test_method_m1a import MemoryExecution
from memory_service_runtime.governed import method_v02 as lifecycle
from memory_service_runtime.governed import method_v02_models as schemas
from memory_service_runtime.governed import method_v02_profile as profile
from memory_service_runtime.governed.errors import GovernedError


class Execution(MemoryExecution):
    contract_version = schemas.CONTRACT_VERSION
    action_params = schemas.ACTION_PARAMS
    payload_models = schemas.PAYLOAD_MODELS

    def __init__(self):
        super().__init__()
        self.heads = self.objects

    def call(self, actor, action, params, target=None):
        self.actor(actor)
        self.kind = 'm1a_' + action
        self.params = self.action_params[self.kind].model_validate(params).model_dump(mode='json', exclude_none=True)
        self.target = self.objects[target] if target else None
        self.target_revision = self.revisions[self.target['latest_revision_id']] if target else None
        return lifecycle.run(self)


def issue(e):
    result = e.call('ceo', 'create_direct_issue', {'domain_id': e.domain_id, 'payload': {
        'title': 'Business choice', 'summary': 'CEO direct proposal', 'business_scope': 'battlefield',
        'urgency': 'yellow', 'confirmation_reason': 'Research required'}})
    oid = result['object_id']
    e.call('ceo', 'assign_research', {'dri_principal_id': e.people['dri'], 'ceo_agent_id': e.people['ceo_agent'],
        'dri_agent_id': e.people['dri_agent'], 'co_agent_id': e.people['co_agent']}, oid)
    return oid


def brief(e, oid):
    return e.call('ceo_agent', 'publish_brief', {'payload': {'title': 'Exploration', 'issue_ref': e.current_ref(oid),
        'question': 'Which alternative?', 'analysis': 'Compare evidence', 'options': ['A', 'B'],
        'limitations': ['Synthetic evidence'], 'source_refs': [e.evidence]}}, oid)['brief_ref']


def test_light_material_requires_human_exact_confirmation():
    e = Execution()
    oid = issue(e)
    first = brief(e, oid)
    meeting = {'title': 'Review', 'objective': 'Evaluate alternatives', 'brief_ref': first, 'material_refs': [first]}
    with pytest.raises(GovernedError):
        e.call('dri', 'open_meeting', meeting, oid)
    with pytest.raises(GovernedError):
        e.call('ceo_agent', 'confirm_brief', {'brief_ref': first, 'reason': 'Sufficient'}, oid)
    e.call('ceo', 'confirm_brief', {'brief_ref': first, 'reason': 'Sufficient'}, oid)
    second = brief(e, oid)
    assert second != first and second['object_id'] == first['object_id']
    with pytest.raises(GovernedError):
        e.call('dri', 'open_meeting', meeting, oid)
    e.call('ceo', 'confirm_brief', {'brief_ref': second, 'reason': 'Rechecked revision'}, oid)
    meeting.update(brief_ref=second, material_refs=[second])
    opened = e.call('dri', 'open_meeting', meeting, oid)
    assert opened['meeting_ref']
    assert not any(o['object_type'] in {'Signal', 'PotentialIssue', 'ResearchReport', 'StrategicAgreement', 'Mission'} for o in e.objects.values())


def test_signal_requires_disposition_and_can_support_multiple_issues():
    e = Execution()
    signal = e.call('ceo_agent', 'record_signal', {'domain_id': e.domain_id, 'payload': {
        'title': 'Observation', 'kind': 'external', 'description': 'Evidence-based signal', 'source_refs': [e.evidence]}})
    oid = signal['object_id']
    payload = {'title': 'Question', 'summary': 'Explore', 'origin': 'signal', 'business_scope': 'strategic',
        'urgency': 'gray', 'signal_refs': [e.current_ref(oid)]}
    with pytest.raises(GovernedError):
        e.call('dri', 'open_potential_issue', {'domain_id': e.domain_id, 'payload': payload})
    with pytest.raises(GovernedError):
        e.call('ceo_agent', 'activate_signal', {'reason': 'Relevant'}, oid)
    e.call('ceo', 'activate_signal', {'reason': 'Relevant'}, oid)
    for _ in range(2):
        potential = e.call('dri', 'open_potential_issue', {'domain_id': e.domain_id, 'payload': payload})
        with pytest.raises(GovernedError):
            e.call('ceo_agent', 'confirm_strategic_issue', {'reason': 'Research'}, potential['object_id'])
        e.call('ceo', 'confirm_strategic_issue', {'reason': 'Research'}, potential['object_id'])
    assert len(e.states[oid]['issue_refs']) == 2
    e.call('ceo', 'archive_signal', {'reason': 'No further exploration'}, oid)
    with pytest.raises(GovernedError):
        e.call('dri', 'open_potential_issue', {'domain_id': e.domain_id, 'payload': payload})
    e.call('ceo', 'activate_signal', {'reason': 'New information'}, oid)
    assert e.states[oid]['phase'] == 'converted'


@pytest.mark.parametrize('action', ['m1b_comment', 'm1b_withdraw_comment', 'm1b_assist_review'])
def test_deadline_uses_database_clock_and_rejects_exact_boundary(action):
    now = datetime.now(timezone.utc)
    e = SimpleNamespace(kind=action, target_revision={'payload': {'feedback_deadline': now.isoformat()}},
        conn=SimpleNamespace(execute=lambda sql: SimpleNamespace(fetchone=lambda: {'now': now})))
    with pytest.raises(GovernedError) as error:
        lifecycle.deadline_check(e)
    assert error.value.code == 'REVIEW_DEADLINE_PASSED'
    e.kind = 'm1b_close_window'
    lifecycle.deadline_check(e)


def test_classification_and_versioned_contract():
    with pytest.raises(ValidationError):
        schemas.Classification(business_scope='strategic', urgency='red')
    with pytest.raises(ValidationError):
        schemas.PotentialIssuePayload(title='X', summary='Y', business_scope='strategic', urgency='gray', origin='period_review')
    assert profile.validate(profile.content())
    contract = Path(__file__).resolve().parents[1] / 'docs/contracts/tkos-method-0.2.md'
    assert sha256(contract.read_bytes()).hexdigest() == profile.CONTRACT_SHA256


def test_v02_context_excludes_recursive_signals_and_rejects_unknown_modes(monkeypatch):
    from tests.test_method_context import ContextFixture
    from memory_service_runtime.governed import method_readers
    f = ContextFixture(monkeypatch)
    monkeypatch.setattr(method_readers.protocol, 'require_read_support', lambda *args: {
        'protocol_id':'tkos.method','contract_version':'tkos.method/0.2'})
    signal = f.add('Signal', {'description':'Signal content must not enter dialogue'})
    potential = f.add('PotentialIssue', {'signal_refs':[signal]})
    pack = method_readers.context_pack(f,f.ctx,[potential['object_id']],f.now,f.now,'research','analysis',True,'tkos.method/0.2')
    assert [item['object_type'] for item in pack['selected']] == ['PotentialIssue']
    assert any(item['reason'] == 'signal_not_for_dialogue' for item in pack['excluded'])
    for stage,purpose in [('unknown','analysis'),('research','unknown')]:
        with pytest.raises(GovernedError) as error:
            method_readers.context_pack(f,f.ctx,[signal['object_id']],f.now,f.now,stage,purpose,True,'tkos.method/0.2')
        assert error.value.code == 'INVALID_REQUEST'


def test_dialogue_snapshot_cannot_replay_signal_payload(monkeypatch):
    from tests.test_method_context import ContextFixture
    from memory_service_runtime.governed import method_readers
    f = ContextFixture(monkeypatch)
    with pytest.raises(GovernedError) as error:
        method_readers.snapshot(f,f.ctx,{'selected':[{'object_type':'Signal','context_request':{
            'contract_version':'tkos.method/0.2','purpose':'analysis'}}],'excluded':[]})
    assert error.value.code == 'FORBIDDEN'


def test_object_catalog_selects_exact_version():
    from memory_service_runtime.governed.workbench import object_types
    old = object_types(None,None,'tkos.method/0.1')
    new = object_types(None,None,'tkos.method/0.2')
    assert 'ResearchBrief' not in {item['object_type'] for item in old['items']}
    assert 'ResearchBrief' in {item['object_type'] for item in new['items']}
    assert new['schema_version'] == 'method-read/0.2'
