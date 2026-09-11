"""Membership changes, required submissions and precise Mission selection."""
from __future__ import annotations
from copy import deepcopy

from .cases_versions import rejected, reject_stale_manifest
from . import oracle


def add_members(flow,book):
    h,f=flow.h,flow.f;c1=flow.build();old_activation=flow.activation_body(c1)
    old_refs=deepcopy(flow.submissions);old_a_missions=deepcopy(flow.missions['a'])
    flow.amend(['a','b','c'])
    proof=reject_stale_manifest(flow,old_activation)
    assert flow.submissions==old_refs
    book.check('A2-05','add_invalidates_old_set',True,evidence={**proof,'unchanged_original_submissions':old_refs})
    body=flow.command('form_company_composition',flow.form_params(),oid=flow.round_id)
    body['expected_versions']=flow.current_known_versions(flow.round_id)
    proof=rejected(flow,'ceo',body,'COMPOSITION_INPUT_CHANGED')
    book.check('A2-05','new_member_submission_required',True,evidence=proof)
    errors=[]
    body=flow.command('amend_formation_round',flow.amend_params(['a','b']),oid=flow.round_id)
    body['expected_versions']=flow.current_known_versions(flow.round_id)
    errors.append(rejected(flow,'a',body,'FORBIDDEN',403))
    stale=deepcopy(body);stale['target']['expected_version']-=1
    errors.append(rejected(flow,'ceo',stale,'VERSION_CONFLICT'))
    for key,value in [('period_id',f['period_id']),('method_profile_ref',{'profile_id':'injected','revision':'P3'})]:
        invalid=deepcopy(body);invalid['params'][key]=value
        errors.append(rejected(flow,'ceo',invalid,'INVALID_REQUEST',422))
    book.check('A2-05','amend_authority_cas_fixed_profile_period',True,evidence=errors)
    flow.publish('c')
    # Formal removal of a previously proposed Mission must not activate its old
    # index entry when the rest of the company composition becomes effective.
    flow.publish('a',mission_keys=['a-revised-mission'])
    new=flow.form();manifest=flow.manifest(new)
    proof=oracle.manifest(manifest,f,round_id=flow.round_id,company_ref=flow.company_ref,
        submissions=flow.submissions,resource_id=flow.resource_id,source_ref=flow.capacity_ref,
        required=3,available=3,member_version=2)
    flow.confirm(new,'c')
    body=flow.command('activate_company_composition',flow.activation_params(new),oid=new['object_id'])
    body['expected_versions']=flow.current_known_versions(new['object_id'])
    rejected(flow,'ceo',body,'CONFIRMATION_INCOMPLETE')
    for actor in ('ceo','a','b'):flow.confirm(new,actor)
    receipt=flow.activate(new)
    rows=h.sql(f,'SELECT count(*) AS n FROM gov_composition_confirmations WHERE scope_id=%s AND composition_object_id=%s',(f['scope_id'],new['object_id']))
    assert rows[0]['n']==4
    for mission in old_a_missions:
        assert flow.object(mission['mission_object_id'])['effective_revision_id'] is None
    commitments=h.sql(f,"SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s AND object_type='DomainCommitment' AND lifecycle_status='active'",(f['scope_id'],))
    assert commitments[0]['n']==3
    book.check('A2-05','all_four_resign',True,evidence={**proof,'activation_receipt_id':receipt['receipt_id'],
        'current_domain_commitments':3,'withdrawn_missions_not_activated':old_a_missions})
    active_amend=flow.command('amend_formation_round',flow.amend_params(['a','b']),oid=flow.round_id)
    active_amend['expected_versions']=flow.current_known_versions(flow.round_id)
    proof=rejected(flow,'ceo',active_amend,'INVALID_STATE')
    book.check('A2-05','active_amend_rejected',True,evidence=proof)
    return receipt


def remove_members(flow,book):
    h,f=flow.h,flow.f;c1=flow.build();old_body=flow.activation_body(c1)
    old_b=deepcopy(flow.submissions['b']);old_b_missions=deepcopy(flow.missions['b'])
    flow.amend(['a'])
    negative=reject_stale_manifest(flow,old_body)
    flow.publish('a',source_ref=flow.capacity_ref)
    del flow.submissions['b'];del flow.missions['b']
    c2=flow.form();assert len(flow.manifest(c2)['required_signers'])==2
    flow.sign_all(c2);receipt=flow.activate(c2)
    for mission in old_b_missions:
        obj=flow.object(mission['mission_object_id'])
        assert obj['effective_revision_id'] is None and obj['lifecycle_status']!='active'
    history=oracle.immutable_history(h,f,[old_b])
    book.check('A2-05','remove_invalidates_old_set',True,evidence={**negative,**history,
        'current_signer_count':2,'removed_domain_missions_not_activated':old_b_missions,
        'new_activation_receipt_id':receipt['receipt_id']})
    return receipt
