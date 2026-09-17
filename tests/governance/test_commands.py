from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from memory_service_app import governance_commands as journal
from memory_service_runtime.governed.errors import GovernedError


def test_only_human_m1b_and_monthly_scene_commands():
    with pytest.raises(GovernedError): journal.parse({'contract_version': 'anything'})
    body = {'action_type':'m1b_close_window','contract_version':'tkos.method/0.3','target':{'object_id':str(uuid4()),'revision_id':str(uuid4()),'expected_version':1},'expected_versions':[],'idempotency_key':'x'*32,'reason':'Close this window','params':{'reason':'Close this window'}}
    with pytest.raises(GovernedError) as exc: journal.parse(body)
    assert exc.value.code == 'FORBIDDEN'


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, 'get_settings', lambda: SimpleNamespace(tkos_governance_commands_dir=str(tmp_path / 'journal')))
    @contextmanager
    def transaction(_): yield None, SimpleNamespace(principal_type='human')
    monkeypatch.setattr(journal.db, 'transaction', transaction)
    monkeypatch.setattr(journal.method_access, 'head', lambda *_: {})
    identity = {'scope_id':str(uuid4()),'principal_id':str(uuid4())}
    body = {'action_type':'m1b_comment','contract_version':'tkos.method/0.3','target':{'object_id':str(uuid4()),'revision_id':str(uuid4()),'expected_version':1},'expected_versions':[],'idempotency_key':'x'*32,'reason':'Personal review comment','params':{'target_ref':{'object_id':str(uuid4()),'revision_id':str(uuid4()),'payload_hash':'a'*64},'content':'Please clarify the evidence'}}
    monkeypatch.setattr(journal.service,'prepare_action',lambda *_: {'target':body['target'],'expected_versions':[]})
    monkeypatch.setattr(journal.method_access,'revision',lambda *_:{})
    monkeypatch.setattr(journal.method_readers,'object_state',lambda *_:{'object_type':'ReviewWindow','latest_revision':{'payload':{'title':'Test window'}}})
    import memory_service_runtime.governed.readers as core_readers
    monkeypatch.setattr(core_readers,'action_receipt',lambda *_,**__:{'receipt_id':'r'})
    return identity, body


def test_prepare_duplicate_and_changed_intent(command):
    identity, body = command
    one = journal.prepare('token', identity, body)
    assert journal.prepare('token', identity, body) == one
    body['reason'] = 'Changed intent'
    with pytest.raises(GovernedError) as exc: journal.prepare('token',identity,body)
    assert exc.value.code == 'IDEMPOTENCY_CONFLICT'


def test_cross_identity_hides_local_journal(command):
    identity, body = command
    one=journal.prepare('token',identity,body)
    with pytest.raises(GovernedError) as exc: journal.get(one['command_id'], {**identity,'principal_id':str(uuid4())}, 'token')
    assert exc.value.code == 'NOT_FOUND'


def test_no_silent_target_cas_upgrade(command,monkeypatch):
    identity,body=command
    monkeypatch.setattr(journal.service,'prepare_action',lambda *_:{'target':{**body['target'],'expected_version':2},'expected_versions':[]})
    with pytest.raises(GovernedError) as exc: journal.prepare('token',identity,body)
    assert exc.value.code == 'VERSION_CONFLICT'


def test_unknown_replays_original_envelope_after_restart(command,monkeypatch):
    identity,body=command
    one=journal.prepare('token',identity,body)
    attempts=[]
    def fail(conn,ctx,request): attempts.append(request.model_dump(mode='json',exclude_none=True)); raise OSError('response lost')
    monkeypatch.setattr(journal.service,'execute_action',fail)
    assert journal.commit(one['command_id'],identity,'token')['status']=='unknown'
    with pytest.raises(GovernedError): journal.commit(one['command_id'],identity,'token')
    def success(conn,ctx,request): attempts.append(request.model_dump(mode='json',exclude_none=True)); return {'receipt_id':str(uuid4()),'result':{}}
    monkeypatch.setattr(journal.service,'execute_action',success)
    result=journal.commit(one['command_id'],identity,'token',retry=True)
    assert result['status']=='committed' and attempts[0]==attempts[1]==one['envelope']


def test_explicit_conflict_not_retried(command,monkeypatch):
    identity,body=command
    one=journal.prepare('token',identity,body)
    def reject(*_): raise GovernedError('VERSION_CONFLICT')
    monkeypatch.setattr(journal.service,'execute_action',reject)
    result=journal.commit(one['command_id'],identity,'token')
    assert result['status']=='rejected'
    monkeypatch.setattr(journal.service,'execute_action',lambda *_: pytest.fail('must not replay rejected command'))
    assert journal.commit(one['command_id'],identity,'token',retry=True)['status']=='rejected'
