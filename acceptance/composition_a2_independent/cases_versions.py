"""Version, ABA and real capacity positive/negative scenarios."""
from __future__ import annotations
from copy import deepcopy
import json
import uuid

from acceptance.protocol_a1_independent.support import private_json
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from . import oracle
from .fixture import PROFILE


def rejected(flow,actor,body,code,status=409):
    return flow.h.rejection(flow.f,flow.clients[actor],'/v1/actions',body,status,code)


def reject_stale_manifest(flow,original,actor='ceo'):
    """Refresh every mutable CAS/generation while keeping the OLD signed content.

    This prevents a stale-CAS failure from falsely proving complete-manifest
    validation: a malicious caller can always fetch fresh mutable versions.
    """
    body=deepcopy(original)
    oid=body['target']['object_id']
    body['target']['expected_version']=flow.object(oid)['object_version']
    body['expected_versions']=flow.current_known_versions(oid)
    if 'expected_member_set_version' in body['params']:
        body['params']['expected_member_set_version']=flow.member_version
        body['params']['expected_input_set_version']=flow.input_version
    body['idempotency_key']='a2-stale-manifest-fresh-cas-'+uuid.uuid4().hex
    result=rejected(flow,actor,body,'COMPOSITION_INPUT_CHANGED')
    result['fresh_mutable_versions_with_old_signed_content']=True
    return result


def normal_versions(flow,book):
    h,f=flow.h,flow.f
    c1=flow.build(sign=False);r1=deepcopy(flow.capacity_ref)
    first_manifest=flow.manifest(c1)
    oracle.manifest(first_manifest,f,round_id=flow.round_id,company_ref=flow.company_ref,
        submissions=flow.submissions,resource_id=flow.resource_id,source_ref=r1,required=3,available=3)
    stale_b=flow.prepare('b','confirm_company_composition',flow.confirm_params(c1,'b'),oid=c1['object_id'])
    head1=flow.object(c1['object_id'])['object_version']
    flow.confirm(c1,'ceo')
    assert flow.object(c1['object_id'])['object_version']==head1+1
    assert flow.manifest(c1)==first_manifest
    book.check('A2-02','signing_keeps_content',True,evidence={'manifest_hash':c1['manifest_hash'],'head_before':head1,'head_after':head1+1})
    proof=rejected(flow,'b',stale_b,'VERSION_CONFLICT')
    book.check('A2-02','stale_head_rejected',True,evidence=proof)
    flow.confirm(c1,'a');flow.confirm(c1,'b')
    assert flow.manifest(c1)==first_manifest
    book.check('A2-02','fresh_head_same_manifest',True,evidence={'revision_id':c1['revision_id'],'manifest_hash':c1['manifest_hash']})
    old_activation=flow.activation_body(c1)
    old_a=deepcopy(flow.submissions['a']);old_b=deepcopy(flow.submissions['b'])
    flow.revise_capacity(2);r2=deepcopy(flow.capacity_ref);flow.publish('b')
    assert flow.submissions['a']==old_a
    proof=reject_stale_manifest(flow,old_activation)
    book.check('A2-04','r2_invalidates_r1',True,evidence=proof)
    c2=flow.form()
    capacity_negative=oracle.manifest(flow.manifest(c2),f,round_id=flow.round_id,company_ref=flow.company_ref,
        submissions=flow.submissions,resource_id=flow.resource_id,source_ref=r2,required=3,available=2)
    review = flow.object(c2['object_id'],'a')['a2_projection']['composition']['readiness']
    assert review['judgments_all_pass'] is True and review['hard_conflicts']
    assert review['basis']=='at_form' and review['requires_live_admission'] is True
    # A valid exact target and current complete formal inputs: an unrelated stale
    # CAS, missing signature or malformed shape must not masquerade as capacity.
    body=flow.command('confirm_company_composition',flow.confirm_params(c2,'ceo'),oid=c2['object_id'])
    preparation=flow.clients['ceo'].request('POST','/v1/actions/prepare',body,expected=(200,409))
    if preparation.status_code==200:body['expected_versions']=preparation.json()['expected_versions']
    else:
        assert preparation.json()['error']['code']=='COMPOSITION_NOT_READY'
        body['expected_versions']=flow.current_known_versions(c2['object_id'])
    proof=rejected(flow,'ceo',body,'COMPOSITION_NOT_READY')
    book.check('A2-07','capacity_two_confirm_rejected',True,evidence={**capacity_negative,**proof})
    activation=flow.command('activate_company_composition',flow.activation_params(c2),oid=c2['object_id'])
    activation['expected_versions']=flow.current_known_versions(c2['object_id'])
    proof=rejected(flow,'ceo',activation,'COMPOSITION_NOT_READY')
    book.check('A2-07','capacity_two_activate_rejected',True,evidence=proof)
    # The extra capacity is a newly authorized synthetic allocation input;
    # authority is a real CEO HTTP publication, not a fixture flag or SQL edit.
    allocation=flow.create_reference(title='Synthetic CEO allocation of one additional onboarding slot')
    flow.revise_capacity(3,actor='ceo',upstream_refs=[{k:allocation[k] for k in ('object_id','revision_id')}])
    r3=deepcopy(flow.capacity_ref);flow.publish('b')
    proof=reject_stale_manifest(flow,old_activation)
    book.check('A2-04','r3_does_not_revive_r1',True,evidence=proof)
    assert len({r['revision_id'] for r in (r1,r2,r3)})==3
    c3=flow.form(extra_evidence=[allocation])
    manifest=flow.manifest(c3)
    actual=oracle.manifest(manifest,f,round_id=flow.round_id,company_ref=flow.company_ref,
        submissions=flow.submissions,resource_id=flow.resource_id,source_ref=r3,required=3,available=3)
    book.check('A2-01','r3_manifest_exact',True,evidence=actual)
    new_signs=flow.sign_all(c3)
    rows=h.sql(f,'''SELECT principal_id::text,assignment_id::text,manifest_hash,composition_revision_id::text
                   FROM gov_composition_confirmations WHERE scope_id=%s AND composition_object_id=%s''',
                   (f['scope_id'],c3['object_id']))
    assert len(rows)==3 and {r['principal_id'] for r in rows}=={f['actors'][a]['principal_id'] for a in ('ceo','a','b')}
    assert all(r['manifest_hash']==c3['manifest_hash'] and r['composition_revision_id']==c3['revision_id'] for r in rows)
    book.check('A2-01','all_current_signers',True,evidence={'confirmations':rows})
    book.check('A2-04','new_r3_all_resign',True,evidence={'new_confirmation_receipts':[r['receipt_id'] for r in new_signs]})
    receipt=flow.activate(c3)
    evidence=oracle.receipt_integrity(h,f,receipt,action='activate_company_composition')
    state=flow.object(c3['object_id']);assert state['lifecycle_status']=='active'
    records=h.sql(f,'SELECT count(*) AS n FROM gov_activation_records WHERE scope_id=%s',(f['scope_id'],))
    assert records[0]['n']==1
    for domain,missions in flow.missions.items():
        for mission in missions:
            row=flow.object(mission['mission_object_id'])
            assert row['lifecycle_status']=='active' and row['effective_revision_id']==mission['mission_revision_id']
    commitments=h.sql(f,"SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s AND object_type='DomainCommitment' AND lifecycle_status='active'",(f['scope_id'],))
    assert commitments[0]['n']==2
    book.check('A2-01','single_atomic_activation',True,evidence={**evidence,'active_domain_commitments':2,'activation_records':1})
    book.check('A2-07','capacity_three_passes',True,evidence={**actual,'activation_receipt_id':receipt['receipt_id']})
    unwanted=h.sql(f,"SELECT object_type,count(*) AS n FROM gov_objects WHERE scope_id=%s AND object_type IN ('ExecutionAuthority','ExecutionCommitment','WorkItem','WorkReceipt','ExecutionPlan') GROUP BY object_type",(f['scope_id'],))
    assert unwanted==[]
    book.check('A2-01','no_execution_release',True,evidence={'execution_objects':unwanted,'effect_task_ids':[]})
    retained=oracle.immutable_history(h,f,[r1,r2,r3,old_a,old_b])
    count=h.sql(f,'SELECT count(*) AS n FROM gov_composition_confirmations WHERE scope_id=%s AND composition_object_id=%s',(f['scope_id'],c1['object_id']))[0]['n']
    assert count==3
    book.check('A2-04','history_retained',True,evidence={**retained,'c1_confirmations_preserved':count})
    return {'c1':c1,'c2':c2,'c3':c3,'capacity_versions':[r1,r2,r3],'activation':receipt}


def draft_profile(flow,book,source):
    h,f=flow.h,flow.f;c1=flow.build();original=flow.manifest(c1);before=h.snapshot(f)
    draft=flow.submission_params('b');draft['submission']['resources'][0]['required']=2
    private_json(h.private/('unpublished-draft-'+uuid.uuid4().hex+'.json'),draft)
    assert flow.manifest(c1)==original and h.snapshot(f)==before
    book.check('A2-03','draft_not_formal',True,evidence={'manifest_hash':c1['manifest_hash'],'formal_input_version':original['input_set_version']})
    p3=json.loads((source/'memory_service_runtime/governed/resources/profile-core.json').read_text())
    p3['revision']='0.2.0-independent-semantics'
    p3['rules']['responsibility_and_acceptance']['acceptor_mode']='independent_named_assessor_v2'
    p3.pop('canonical_hash');p3['canonical_hash']=oracle.canonical_hash(p3)
    file=h.private/('unused-profile-'+uuid.uuid4().hex+'.json');private_json(file,p3)
    ControlAdapter(h,source,'memory_service_runtime.governed.control').cli('a2-unadopted-'+uuid.uuid4().hex[:10],
        ['install-profile','--scope-id',f['scope_id'],'--profile-json',str(file),
         '--reason','Synthetic unused proposal; no rebind or policy change'],expected_exit=0)
    assert flow.manifest(c1)==original
    binding=h.sql(f,'''SELECT profile_id,profile_revision,profile_canonical_hash FROM gov_object_protocol_bindings
                      WHERE scope_id=%s AND object_id=%s''',(f['scope_id'],flow.round_id))
    assert binding==[{'profile_id':PROFILE['profile_id'],'profile_revision':PROFILE['revision'],
                     'profile_canonical_hash':PROFILE['canonical_hash']}]
    book.check('A2-12','unused_profile_not_migration',True,evidence={'round_binding':binding,'installed_unused_revision':p3['revision']})
    generic=flow.command('propose_revision',{'payload':{'title':'Injected profile replacement',
        'terms':{'method_profile_ref':{'profile_id':p3['profile_id'],'revision':p3['revision']}}}},oid=flow.round_id)
    rejected(flow,'ceo',generic,'ACTION_NOT_SUPPORTED_FOR_PROTOCOL')
    book.check('A2-12','generic_rebind_rejected',True)
    amend=flow.command('amend_formation_round',flow.amend_params(['a','b']),oid=flow.round_id)
    amend['params']['method_profile_ref']={'profile_id':p3['profile_id'],'revision':p3['revision']}
    rejected(flow,'ceo',amend,'INVALID_REQUEST',422)
    book.check('A2-12','injected_profile_rejected',True)
    receipt=flow.activate(c1)
    assert flow.manifest(c1)==original
    book.check('A2-03','unadopted_document_not_binding',True,evidence={'same_manifest_hash':c1['manifest_hash'],'activation_receipt_id':receipt['receipt_id']})
    return receipt
