"""Contract boundaries complement the independent real HTTP/PG acceptance."""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from memory_service_runtime.governed import method_v03_models as m, method_v03 as actions, method_v03_profile as profile
from memory_service_runtime.governed.models import ActionRequest
from memory_service_runtime.governed.errors import GovernedError


def ref():
    return {'object_id':str(uuid4()),'revision_id':str(uuid4()),'payload_hash':'a'*64}


def state_payload(**updates):
    subject=ref()
    return {'subject_ref':subject,'as_of':'2026-09-15T00:00:00Z','summary':'Evidence gap','rag':'unknown',
        'baseline_refs':[subject],'evidence_refs':[],'data_gaps':['Await evidence'],'generation_version':'test-1',**updates}


@pytest.mark.parametrize('rag',['green','yellow','red'])
def test_missing_evidence_cannot_be_known_rating(rag):
    with pytest.raises(ValidationError):
        m.StatePayload.model_validate(state_payload(rag=rag))


def test_unknown_requires_explanation_when_no_evidence():
    with pytest.raises(ValidationError):
        m.StatePayload.model_validate(state_payload(data_gaps=[]))


def test_override_is_complete_and_cannot_change_facts_or_scope():
    for value in ({'reason':'Reviewed','rag':'green'},{'reason':'Reviewed','summary':'Changed'},
                  {'reason':'Reviewed','subject_ref':ref()},{'reason':'Reviewed','evidence_refs':[ref()]}):
        with pytest.raises(ValidationError):
            m.ConfirmState.model_validate(value)


def test_duplicate_state_evidence_rejected():
    evidence=ref()
    with pytest.raises(ValidationError):
        m.StatePayload.model_validate(state_payload(evidence_refs=[evidence,evidence]))


def test_state_identity_normalizes_timezone_but_not_observation_time():
    p=state_payload()
    assert actions.state_key(p)==actions.state_key({**p,'as_of':'2026-09-15T08:00:00+08:00'})
    assert actions.state_key(p)!=actions.state_key({**p,'as_of':'2026-09-16T00:00:00Z'})


def test_problem_identity_ignores_refresh_but_preserves_core_question():
    p={'core_question':'Which  customer segment?'}
    s=state_payload()
    assert actions.problem_key(p,s)==actions.problem_key({'core_question':'Which customer segment?'},{**s,'generation_version':'2'})
    assert actions.problem_key(p,s)!=actions.problem_key({'core_question':'Which delivery responsibility?'},s)


def test_mission_owner_is_not_inferred_from_dri_role():
    owner=str(uuid4());calls=[]
    e=SimpleNamespace(ref=lambda *args,**kw: ({'object_type':'Mission'}, {'payload':{'owner_principal_id':owner}}),
        validate_principal=lambda *args:calls.append(args))
    assert actions.subject_owner(e,ref())[2]==owner
    assert calls==[(owner,'human',None)]


def test_new_actions_cannot_use_old_contract_envelopes():
    body={'action_type':'method_propose_state','target':None,'expected_versions':[],
        'idempotency_key':'version-boundary-test','reason':'Check contract boundary',
        'params':{'domain_id':str(uuid4()),'payload':state_payload()},'contract_version':m.CONTRACT_VERSION}
    assert ActionRequest.model_validate(body)
    for old in ('tkos.method/0.1','tkos.method/0.2'):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate({**body,'contract_version':old})


def test_architecture_stable_definition_ids_are_unique():
    unit={'unit_id':'capability-method','unit_type':'capability','name':'Method','definition':'Design operating methods',
          'strategic_basis':['Reusable management value'],'boundary':'Method definitions','domain_id':str(uuid4())}
    with pytest.raises(ValidationError):
        m.ArchitectureDefinition(title='Architecture',units=[unit,unit])


def test_frozen_contract_profile_and_explicit_type_registry():
    root=Path(__file__).resolve().parents[1]
    contract=root/'docs/contracts/tkos-method-0.3.md'
    assert sha256(contract.read_bytes()).hexdigest()==profile.CONTRACT_SHA256
    assert profile.validate(profile.content())
    assert profile.CONTRACT_SHA256 in (root/'src/memory_service_app/migrations/0025_method_anchors_v03.sql').read_text()
    assert 'StrategicSignal' not in m.OBJECT_TYPES
    assert 'm1a_create_direct_issue' not in m.ACTION_PARAMS
    assert set(m.ACTION_PARAMS)==set(m.ACTION_TARGETS)


def test_unknown_resolved_close_does_not_allow_transfer_by_ordinary_close():
    with pytest.raises(ValidationError):
        m.CloseProblem(disposition='transferred',reason='Skip handoff',evidence_refs=[ref()])


def test_period_review_requires_canonical_state_sources():
    with pytest.raises(ValidationError):
        m.PeriodReview(review_id='r',title='Review',period={'start':'2026-09-01T00:00:00Z','end':'2026-09-30T00:00:00Z'},
            target_refs=[ref()],findings=['Results'],generation_version='1',state_refs=[])
