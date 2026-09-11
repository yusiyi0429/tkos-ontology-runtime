"""A2 lock ordering, CAS races, expiry and atomic activation fault injection."""
from __future__ import annotations
from copy import deepcopy
import uuid

from . import concurrency,oracle
from .cases_authority import revoke_body


def same_cas(flow,book):
    h,f=flow.h,flow.f;flow.build()
    refs=[flow.create_reference(title='Synthetic amendment candidate '+str(i)) for i in range(2)]
    commands=[flow.prepare('ceo','amend_formation_round',flow.amend_params(['a','b'],company_ref=ref),oid=flow.round_id) for ref in refs]
    assert commands[0]['target']==commands[1]['target']
    results=concurrency.race(h,f,flow.url,'ceo',commands)
    assert sorted(x['status'] for x in results)==[200,409]
    loser=[x for x in results if x['status']==409][0]
    assert loser['body']['error']['code'] in {'VERSION_CONFLICT','COMPOSITION_INPUT_CHANGED'}
    winner=next(i for i,x in enumerate(results) if x['status']==200)
    receipt=results[winner]['body'];flow.receipts.append(receipt)
    flow.member_version=receipt['result']['member_set_version'];flow.input_version=receipt['result']['input_set_version']
    flow.company_ref=refs[winner]
    count=h.sql(f,"SELECT count(*) AS n FROM gov_action_receipts WHERE scope_id=%s AND action_type='amend_formation_round'",(f['scope_id'],))[0]['n']
    assert count==1
    book.check('A2-13','same_cas_one_winner',True,evidence={'statuses':[x['status'] for x in results],
        'winner_receipt_id':receipt['receipt_id'],'amend_receipt_count':count})
    candidate=flow.form();flow.sign_all(candidate)
    first=flow.activation_body(candidate);second=deepcopy(first);second['idempotency_key']='a2-competing-activation-'+uuid.uuid4().hex
    outcomes=concurrency.race(h,f,flow.url,'ceo',[first,second])
    assert sorted(x['status'] for x in outcomes)==[200,409]
    failed=next(x for x in outcomes if x['status']==409)
    assert failed['body']['error']['code'] in {'VERSION_CONFLICT','INVALID_STATE','COMPOSITION_INPUT_CHANGED'}
    which=next(i for i,x in enumerate(outcomes) if x['status']==200)
    receipt=outcomes[which]['body'];command=[first,second][which]
    book.check('A2-13','activation_one_winner',True,evidence={'statuses':[x['status'] for x in outcomes],
        'receipt_id':receipt['receipt_id']})
    stable=h.snapshot(f)
    for _ in range(2):assert flow.clients['ceo'].json('POST','/v1/actions',command)==receipt
    assert h.snapshot(f)==stable
    book.check('A2-13','idempotent_original_receipt',True,evidence={'receipt_id':receipt['receipt_id'],'state_unchanged':True})
    activation_rows=h.sql(f,'SELECT count(*) AS n FROM gov_activation_records WHERE scope_id=%s',(f['scope_id'],))[0]['n']
    assert activation_rows==1
    proof=oracle.receipt_integrity(h,f,receipt,action='activate_company_composition')
    book.check('A2-13','no_double_effect',True,evidence={**proof,'activation_record_count':activation_rows})
    return outcomes


def change_first(flow,mode,*,peer_url,primary_name,peer_name):
    c1=flow.build();activation=flow.activation_body(c1)
    if mode=='amend':
        change=flow.prepare('ceo','amend_formation_round',flow.amend_params(['a','b','c']),oid=flow.round_id);actor='ceo'
    elif mode=='source':change,_=flow.revise_capacity_body(2);actor='b'
    elif mode=='revoke':change=revoke_body(flow,'b');actor='ceo'
    else:raise ValueError(mode)
    evidence=concurrency.ordered(flow.h,flow.f,first_url=flow.url,second_url=peer_url,
        first_actor=actor,second_actor='ceo',first_body=change,second_body=activation,
        first_db_name=primary_name,second_db_name=peer_name,second_status=(403,409))
    code=evidence['second']['body']['error']['code']
    if mode=='revoke':assert code in {'FORBIDDEN','CONFIRMATION_INCOMPLETE'}
    else:assert code in {'COMPOSITION_INPUT_CHANGED','VERSION_CONFLICT'}
    assert flow.object(c1['object_id'])['lifecycle_status']!='active'
    assert flow.h.sql(flow.f,'SELECT count(*) AS n FROM gov_activation_records WHERE scope_id=%s',(flow.f['scope_id'],))[0]['n']==0
    return evidence


def activation_first(flow,mode,*,peer_url,primary_name,peer_name):
    h,f=flow.h,flow.f;c1=flow.build();activation=flow.activation_body(c1)
    if mode=='amend':
        change=flow.prepare('ceo','amend_formation_round',flow.amend_params(['a','b','c']),oid=flow.round_id);actor='ceo';expected=(409,)
    elif mode=='source':change,_=flow.revise_capacity_body(2);actor='b';expected=200
    elif mode=='revoke':change=revoke_body(flow,'b');actor='ceo';expected=200
    else:raise ValueError(mode)
    evidence=concurrency.ordered(h,f,first_url=flow.url,second_url=peer_url,
        first_actor='ceo',second_actor=actor,first_body=activation,second_body=change,
        first_db_name=primary_name,second_db_name=peer_name,second_status=expected,
        checkpoint='before_business_commit')
    if mode=='amend':assert evidence['second']['body']['error']['code'] in {'INVALID_STATE','VERSION_CONFLICT'}
    assert flow.object(c1['object_id'])['lifecycle_status']=='active'
    rows=h.sql(f,'SELECT count(*) AS n FROM gov_activation_records WHERE scope_id=%s',(f['scope_id'],))
    assert rows[0]['n']==1
    historical=evidence['first']['body']
    # The original receipt may be read/replayed as history by a currently
    # authorized CEO; it must not create a new execution permission or event.
    before=h.snapshot(f)
    assert flow.clients['ceo'].json('POST','/v1/actions',activation)==historical
    assert h.snapshot(f)==before
    evidence['history_retained_without_new_business_effect']=True
    return evidence


def rollback(flow,book):
    h,f=flow.h,flow.f;c1=flow.build();body=flow.activation_body(c1)
    before=h.snapshot(f);token='a2-partial-rollback-'+uuid.uuid4().hex
    response=flow.clients['ceo'].request('POST','/v1/actions',body,expected=500,
        headers=h.headers('after_first_member_activation',mode='fail',token=token))
    checkpoint=concurrency.reached(h,token)
    assert checkpoint['checkpoint']=='after_first_member_activation'
    book.check('A2-16','first_member_write_checkpoint',True,evidence=checkpoint)
    after=h.snapshot(f);assert before==after
    book.check('A2-16','all_tables_rollback',True,evidence={'tables':sorted(after['tables']),
        'all_business_history_receipt_outbox_unchanged':True,'http_status':response.status_code})
    receipt=flow.commit('ceo',body)
    count=h.sql(f,'SELECT count(*) AS n FROM gov_activation_records WHERE scope_id=%s',(f['scope_id'],))[0]['n']
    assert count==1
    book.check('A2-16','retry_single_activation',True,evidence={'activation_records':count,'receipt_id':receipt['receipt_id']})
    return receipt


def expiry(flow,book):
    candidate=flow.build();body=flow.activation_body(candidate)
    result=concurrency.expire_at_admission(flow.h,flow.f,flow.url,body)
    book.check('A2-11','required_expiry_at_final_barrier',True,evidence=result)
    book.check('A2-11','ceo_valid_epoch_unchanged',True,evidence={'clock_evidence':result['clock_evidence'],'auth_epoch':result['auth_epoch']})
    book.check('A2-11','complete_rollback',True,evidence={'all_governed_state_unchanged':True})
    return result
