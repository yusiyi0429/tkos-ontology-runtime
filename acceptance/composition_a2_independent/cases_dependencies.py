"""Binding freshness, legal input revisions, closure limits and hard conflicts."""
from __future__ import annotations
from copy import deepcopy

from . import oracle
from .cases_versions import rejected, reject_stale_manifest


def input_pointer_rows(flow):
    return flow.h.sql(flow.f,'''SELECT domain_id::text,submission_object_id::text,submission_revision_id::text
        FROM gov_round_formal_submissions WHERE scope_id=%s AND round_object_id=%s ORDER BY domain_id''',
        (flow.f['scope_id'],flow.round_id))


def implicit_binding(flow,book):
    c1=flow.build();old_body=flow.activation_body(c1)
    inputs=input_pointer_rows(flow);generation=flow.input_version
    flow.revise_capacity(3)
    assert input_pointer_rows(flow)==inputs and flow.input_version==generation
    proof=reject_stale_manifest(flow,old_body)
    book.check('A2-06','implicit_source_revision_invalidates',True,evidence={**proof,
        'formal_submissions_unchanged':inputs,'input_generation_before':generation,
        'new_source_revision':flow.capacity_ref['revision_id']})
    flow.publish('b')
    # A new formally included binding: no caller can add a binding only to the
    # manifest while keeping the formal Submission set fixed.
    additional=deepcopy(flow.capacity_payload)
    import uuid
    pool=str(uuid.uuid4());additional['resource_id']=pool;additional['available']=1
    receipt=flow.act('b','create_object',{'object_type':'CapacityObservation',
        'domain_id':flow.f['domains']['b'],'payload':additional})
    ref=flow.ref(receipt['result']['object_id'])
    params=flow.submission_params('b')
    params['submission']['resources'].append({'resource_id':pool,'period_id':flow.f['period_id'],
                                            'unit':'synthetic_onboarding_slot','required':0})
    params['submission']['bindings'].append({'relation_type':'resource_capacity','source_ref':ref,
        'resource_id':pool,'period_id':flow.f['period_id'],'unit':'synthetic_onboarding_slot'})
    submission=flow.act('b','publish_domain_submission',params,oid=flow.round_id)['result']
    flow.input_version=submission['input_set_version'];flow.submissions['b']={
        'object_id':submission['submission_object_id'],'revision_id':submission['submission_revision_id'],
        'payload_hash':submission['payload_hash']};flow.missions['b']=submission['missions']
    c2=flow.form();manifest=flow.manifest(c2)
    assert len(manifest['binding_dependencies'])==2
    assert {x['constraint']['resource_id'] for x in manifest['binding_dependencies']}=={pool,flow.resource_id}
    flow.sign_all(c2);activation=flow.activate(c2)
    book.check('A2-06','new_binding_formal_submission',True,evidence={
        'input_set_version':flow.input_version,'new_source_ref':ref,'signed_manifest_hash':c2['manifest_hash'],
        'activation_receipt_id':activation['receipt_id']})
    return activation


def informational(flow,book):
    c1=flow.build();original=flow.manifest(c1);old_body=flow.activation_body(c1)
    note=flow.create_reference(title='Unadopted informational draft',shared=False)
    payload=flow.object(note['object_id'])['latest_revision']['payload']
    payload=deepcopy(payload);payload['statement']='Changed unadopted background note'
    flow.act('ceo','propose_revision',{'payload':payload},oid=note['object_id'])
    assert flow.manifest(c1)==original
    activation=flow.commit('ceo',old_body)
    book.check('A2-06','informational_change_preserves',True,evidence={
        'unchanged_manifest_hash':c1['manifest_hash'],'unadopted_object_id':note['object_id'],
        'activation_receipt_id':activation['receipt_id']})
    return activation


def nonpassing_judgments(flow,book):
    flow.build(sign=False);proofs=[]
    for name in ('coverage','coherence','feasibility','tradeoff'):
        for conclusion in ('fail','unknown'):
            candidate=flow.form(conclusions={name:conclusion})
            body=flow.command('confirm_company_composition',flow.confirm_params(candidate,'ceo'),oid=candidate['object_id'])
            body['expected_versions']=flow.current_known_versions(candidate['object_id'])
            proof=rejected(flow,'ceo',body,'COMPOSITION_NOT_READY')
            count=flow.h.sql(flow.f,'''SELECT count(*) AS n FROM gov_composition_confirmations
                WHERE scope_id=%s AND composition_object_id=%s''',(flow.f['scope_id'],candidate['object_id']))[0]['n']
            assert count==0
            proofs.append({'judgment':name,'conclusion':conclusion,'confirmations':count,**proof})
    conflict=flow.form(conflicts=[{'summary':'Unresolved synthetic external timing dependency','blocking':True}])
    body=flow.command('confirm_company_composition',flow.confirm_params(conflict,'ceo'),oid=conflict['object_id'])
    body['expected_versions']=flow.current_known_versions(conflict['object_id'])
    conflict_proof=rejected(flow,'ceo',body,'COMPOSITION_NOT_READY')
    # Each domain fits by itself (2 <= 3), while the complete demand set does
    # not (2 + 2 > 3). This detects implementations that only check each member
    # independently or accidentally ignore the provider's own resource demand.
    for name in ('a','b'):
        flow.publish(name,required=2)
    aggregate=flow.form()
    manifest_proof=oracle.manifest(flow.manifest(aggregate),flow.f,
        round_id=flow.round_id,company_ref=flow.company_ref,
        submissions=flow.submissions,resource_id=flow.resource_id,
        source_ref=flow.capacity_ref,required=4,available=3)
    aggregate_denials=[]
    for kind,params in (
        ('confirm_company_composition',flow.confirm_params(aggregate,'ceo')),
        ('activate_company_composition',flow.activation_params(aggregate))):
        body=flow.command(kind,params,oid=aggregate['object_id'])
        body['expected_versions']=flow.current_known_versions(aggregate['object_id'])
        aggregate_denials.append(rejected(flow,'ceo',body,'COMPOSITION_NOT_READY'))
    book.check('A2-07','fail_and_unknown_rejected',True,evidence={
        'all_four_judgments_both_nonpassing_values':proofs,'human_reported_conflict':conflict_proof,
        'complete_set_demand_sum':manifest_proof,'aggregate_capacity_denials':aggregate_denials})
    return proofs


def cyclic_source(flow,book):
    flow.build(sign=False)
    original=deepcopy(flow.capacity_ref)
    parent=flow.create_reference(title='Synthetic binding back to current capacity',
        upstream_refs=[{k:original[k] for k in ('object_id','revision_id')}])
    payload=deepcopy(flow.capacity_payload)
    payload['upstream_refs']=[{k:parent[k] for k in ('object_id','revision_id')}]
    body=flow.command('propose_revision',{'payload':payload},oid=original['object_id'],actor='b')
    body['expected_versions']=flow.current_known_versions(original['object_id'])
    proof=rejected(flow,'b',body,'COMPOSITION_NOT_READY')
    assert flow.ref(original['object_id'])==original
    book.check('A2-07','cycle_rejected',True,evidence={**proof,'cycle_object_path':[
        original['object_id'],parent['object_id'],original['object_id']],
        'source_version_preserved':original['revision_id']})
    return proof


def closure_limit(flow,book):
    flow.build(sign=False)
    # All prefix references are current, shared, same-period and otherwise
    # valid. The first bounded-closure rejection must carry its actual code.
    parent=None;created=[];denial=None
    for depth in range(1,67):
        refs=[] if parent is None else [{k:parent[k] for k in ('object_id','revision_id')}]
        payload={'title':'Synthetic bounded source depth '+str(depth),
            'statement':'Synthetic binding chain with no hidden evidence or cycle',
            'period_id':flow.f['period_id'],'terms':{},'upstream_refs':refs,
            'shared_with_domain_ids':flow.share_domains()}
        body=flow.command('create_object',{'object_type':'CompanyReference',
            'domain_id':flow.f['domains']['company'],'payload':payload})
        before=flow.h.snapshot(flow.f)
        prepare=flow.clients['ceo'].request('POST','/v1/actions/prepare',body,expected=(200,409))
        if prepare.status_code==409:
            assert prepare.json()['error']['code']=='COMPOSITION_NOT_READY'
            assert flow.h.snapshot(flow.f)==before
            body['expected_versions']=flow.current_known_versions()
        else:body['expected_versions']=prepare.json()['expected_versions']
        response=flow.clients['ceo'].request('POST','/v1/actions',body,expected=(200,409))
        if response.status_code==409:
            assert response.json()['error']['code']=='COMPOSITION_NOT_READY'
            assert flow.h.snapshot(flow.f)==before
            denial={'stage':'typed_source_write','attempted_depth':depth,'code':'COMPOSITION_NOT_READY'}
            break
        receipt=response.json();flow.receipts.append(receipt)
        parent=flow.ref(receipt['result']['object_id']);created.append(parent)
        if depth==10:
            # Some implementations bound the composition traversal rather than
            # independent source authoring. Exercise that legal admission path.
            proposed=deepcopy(flow.capacity_payload)
            proposed['upstream_refs']=[{k:parent[k] for k in ('object_id','revision_id')}]
            command=flow.command('propose_revision',{'payload':proposed},oid=flow.capacity_ref['object_id'],actor='b')
            command['expected_versions']=flow.current_known_versions(flow.capacity_ref['object_id'])
            before=flow.h.snapshot(flow.f)
            response=flow.clients['b'].request('POST','/v1/actions',command,expected=(200,409))
            if response.status_code==409:
                assert response.json()['error']['code']=='COMPOSITION_NOT_READY'
                assert flow.h.snapshot(flow.f)==before
                denial={'stage':'capacity_binding_adoption','attempted_depth':depth+1,'code':'COMPOSITION_NOT_READY'}
                break
            # Accepting an oversized graph here still requires rejecting its
            # formal publication. No impossible business state is SQL-seeded.
            receipt=response.json();flow.receipts.append(receipt)
            flow.capacity_payload=proposed;flow.capacity_ref=flow.ref(flow.capacity_ref['object_id'])
            publish=flow.command('publish_domain_submission',flow.submission_params('b'),oid=flow.round_id,actor='b')
            publish['expected_versions']=flow.current_known_versions(flow.round_id)
            before=flow.h.snapshot(flow.f)
            response=flow.clients['b'].request('POST','/v1/actions',publish,expected=(200,409))
            if response.status_code==409:
                assert response.json()['error']['code']=='COMPOSITION_NOT_READY'
                assert flow.h.snapshot(flow.f)==before
                denial={'stage':'formal_submission_admission','attempted_depth':depth+1,'code':'COMPOSITION_NOT_READY'}
                break
            receipt=response.json();flow.receipts.append(receipt)
            result=receipt['result'];flow.input_version=result['input_set_version']
            flow.submissions['b']={'object_id':result['submission_object_id'],
                'revision_id':result['submission_revision_id'],'payload_hash':result['payload_hash']}
            flow.missions['b']=result['missions']
            form=flow.command('form_company_composition',flow.form_params(),oid=flow.round_id)
            form['expected_versions']=flow.current_known_versions(flow.round_id)
            before=flow.h.snapshot(flow.f)
            response=flow.clients['ceo'].request('POST','/v1/actions',form,expected=409)
            assert response.json()['error']['code']=='COMPOSITION_NOT_READY'
            assert flow.h.snapshot(flow.f)==before
            denial={'stage':'composition_form','attempted_depth':depth+1,'code':'COMPOSITION_NOT_READY'}
            break
    assert denial is not None and len(created)>=1
    book.check('A2-06','closure_limit_rejects',True,evidence={**denial,'valid_prefix_sources':len(created),
        'no_truncated_success':True,'all_sources_created_through_http':True})
    return denial
