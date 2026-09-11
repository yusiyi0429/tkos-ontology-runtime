"""Actual identity, exact signer slots, revocation and unsupported extensions."""
from __future__ import annotations
from copy import deepcopy
import uuid

from acceptance.runtime.client import Client
from .cases_versions import rejected, reject_stale_manifest


def visible_denial(flow,actor,body,allowed):
    before=flow.h.snapshot(flow.f)
    response=flow.clients[actor].request('POST','/v1/actions',body,expected=tuple({p[0] for p in allowed}))
    pair=(response.status_code,response.json()['error']['code'])
    assert pair in allowed and flow.h.snapshot(flow.f)==before
    return {'status':pair[0],'code':pair[1],'business_state_unchanged':True}


def signer_negatives(flow,book):
    h,f=flow.h,flow.f
    flow.company_ref=flow.create_reference();flow.create_capacity()
    bad=flow.open_params();bad['members'][0]['dri_assignment_id']=f['ceo_as_a_dri_assignment']
    body=flow.command('open_formation_round',bad)
    body['expected_versions']=flow.current_known_versions()
    proof=visible_denial(flow,'ceo',body,{(403,'FORBIDDEN'),(409,'COMPOSITION_NOT_READY')})
    book.check('A2-08','duplicate_human_rejected',True,evidence=proof)
    flow.open();flow.publish('a');flow.publish('b');c1=flow.form()
    ceo_receipt=flow.confirm(c1,'ceo');ceo_command=deepcopy(flow.commands[-1]['request'])
    flow.confirm(c1,'a')
    body=flow.command('activate_company_composition',flow.activation_params(c1),oid=c1['object_id'])
    body['expected_versions']=flow.current_known_versions(c1['object_id'])
    proof=rejected(flow,'ceo',body,'CONFIRMATION_INCOMPLETE')
    book.check('A2-08','missing_signer_rejected',True,evidence=proof)
    body=flow.command('confirm_company_composition',flow.confirm_params(c1,'b'),oid=c1['object_id'])
    body['expected_versions']=flow.current_known_versions(c1['object_id'])
    proof=visible_denial(flow,'agent',body,{(403,'FORBIDDEN'),(404,'NOT_FOUND')})
    book.check('A2-08','agent_rejected',True,evidence=proof)
    wrong=deepcopy(body);wrong['params']['assignment_id']=f['actors']['wrong_b']['assignment_id']
    proof=rejected(flow,'b',wrong,'FORBIDDEN',403)
    # A different human with the same role cannot replace the named B slot.
    second=visible_denial(flow,'wrong_b',wrong,{(403,'FORBIDDEN'),(404,'NOT_FOUND')})
    book.check('A2-08','wrong_assignment_rejected',True,evidence={'wrong_slot':proof,'same_role_wrong_person':second})
    duplicate=flow.command('confirm_company_composition',flow.confirm_params(c1,'ceo'),oid=c1['object_id'])
    duplicate['expected_versions']=flow.current_known_versions(c1['object_id'])
    rejected(flow,'ceo',duplicate,'INVALID_STATE')
    before=h.snapshot(f)
    assert flow.clients['ceo'].json('POST','/v1/actions',ceo_command)==ceo_receipt
    assert h.snapshot(f)==before
    rows=h.sql(f,'''SELECT principal_id::text,count(*) AS n FROM gov_composition_confirmations
        WHERE scope_id=%s AND composition_object_id=%s GROUP BY principal_id''',(f['scope_id'],c1['object_id']))
    assert len(rows)==2 and all(r['n']==1 for r in rows)
    book.check('A2-08','duplicate_vote_no_extra_weight',True,evidence={'votes':rows,'idempotent_original_receipt':ceo_receipt['receipt_id']})
    old_sign=deepcopy(body)
    flow.revise_capacity(3);flow.publish('b')
    proof=reject_stale_manifest(flow,old_sign,actor='b')
    book.check('A2-08','old_manifest_rejected',True,evidence=proof)
    unknown=Client.command('withdraw_composition_confirmation',{'confirmation_id':ceo_receipt['result']['confirmation_id']},
                           target=flow.target(c1['object_id']))
    unknown['contract_version']='tkos.contract-a/0.1'
    proof=rejected(flow,'ceo',unknown,'INVALID_REQUEST',422)
    book.check('A2-09','unknown_withdrawal_rejected',True,evidence={**proof,'withdrawal_implemented':False})
    return c1


def revoke_body(flow,actor_name):
    # Authority control is not a protocol-bound business object. Preserve the
    # established legacy envelope instead of claiming a new revocation contract.
    return Client.command('revoke_assignment',{'assignment_id':flow.f['actors'][actor_name]['assignment_id']})


def required_revocation(flow,book):
    h,f=flow.h,flow.f;c1=flow.build();activate=flow.activation_body(c1)
    before=h.sql(f,'SELECT confirmation_id::text,principal_id::text FROM gov_composition_confirmations WHERE scope_id=%s ORDER BY confirmation_id',(f['scope_id'],))
    flow.clients['ceo'].json('POST','/v1/actions',revoke_body(flow,'b'))
    proof=visible_denial(flow,'ceo',activate,{(403,'FORBIDDEN'),(409,'CONFIRMATION_INCOMPLETE')})
    book.check('A2-10','required_revoke_rejected',True,evidence=proof)
    after=h.sql(f,'SELECT confirmation_id::text,principal_id::text FROM gov_composition_confirmations WHERE scope_id=%s ORDER BY confirmation_id',(f['scope_id'],))
    assert before==after and len(after)==3
    book.check('A2-10','confirmation_history_retained',True,evidence={'retained_confirmations':after})
    return c1


def unrelated_revocation(flow,book):
    h,f=flow.h,flow.f;c1=flow.build();activate=flow.activation_body(c1)
    before=h.sql(f,'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s',(f['scope_id'],))[0]['auth_epoch']
    flow.clients['ceo'].json('POST','/v1/actions',revoke_body(flow,'unrelated'))
    after=h.sql(f,'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s',(f['scope_id'],))[0]['auth_epoch']
    assert after>before
    receipt=flow.commit('ceo',activate)
    count=h.sql(f,'SELECT count(*) AS n FROM gov_composition_confirmations WHERE scope_id=%s',(f['scope_id'],))[0]['n']
    assert count==3
    book.check('A2-10','unrelated_revoke_allows',True,evidence={'auth_epoch_before':before,
        'auth_epoch_after':after,'unchanged_confirmation_count':3,'activation_receipt_id':receipt['receipt_id']})
    return receipt


def second_round(flow,book):
    h,f=flow.h,flow.f;c1=flow.build()
    body=flow.command('open_formation_round',flow.open_params())
    body['expected_versions']=flow.current_known_versions()
    proof=rejected(flow,'ceo',body,'INVALID_STATE')
    book.check('A2-17','second_same_period_round_rejected',True,evidence=proof)
    receipt=flow.activate(c1)
    count=h.sql(f,'SELECT count(*) AS n FROM gov_formation_round_state WHERE scope_id=%s AND company_id=%s AND period_id=%s',
        (f['scope_id'],f['company_id'],f['period_id']))[0]['n']
    activations=h.sql(f,'SELECT count(*) AS n FROM gov_activation_records WHERE scope_id=%s',(f['scope_id'],))[0]['n']
    assert count==activations==1
    body['expected_versions']=flow.current_known_versions()
    proof=rejected(flow,'ceo',body,'INVALID_STATE')
    book.check('A2-17','capacity_not_double_reserved',True,evidence={**proof,'round_count':count,
        'activation_count':activations,'activation_receipt_id':receipt['receipt_id']})
    return receipt
