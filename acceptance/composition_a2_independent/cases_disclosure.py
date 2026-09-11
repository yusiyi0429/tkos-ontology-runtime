"""A participant reads published terms; other-domain originals remain private."""
from __future__ import annotations
import base64
from copy import deepcopy
import json
import uuid


def assert_hidden(data,markers):
    text=json.dumps(data,ensure_ascii=False)
    assert not any(marker in text for marker in markers),'hidden identifier/name/data leaked in a response'


def disclosure(flow,book):
    h,f=flow.h,flow.f;c1=flow.build()
    ceo_manifest=flow.manifest(c1)
    assert flow.manifest(c1,'a')==ceo_manifest
    b_submission=flow.clients['a'].revision(flow.submissions['b']['object_id'],flow.submissions['b']['revision_id'])
    content=b_submission['payload']['submission']
    assert content['result_statement'] and content['missions'] and content['pdo']['result_criteria']
    published=flow.clients['a'].revision(flow.capacity_ref['object_id'],flow.capacity_ref['revision_id'])
    assert published['payload']['available']==3
    book.check('A2-18','complete_published_manifest_readable',True,evidence={
        'same_manifest_hash':c1['manifest_hash'],'other_domain_published_submission_revision':flow.submissions['b']['revision_id'],
        'explicitly_published_source_revision':flow.capacity_ref['revision_id'],'structured_terms_readable':True})
    before_list=flow.clients['a'].json('GET','/v1/objects?domain_id='+f['domains']['a']+'&limit=100')
    marker='SYNTHETIC_PRIVATE_B_'+uuid.uuid4().hex
    raw=(marker+' private original body').encode()
    evidence=flow.clients['b'].json('POST','/v1/evidence-assets',{
        'domain_id':f['domains']['b'],'title':marker,'content_base64':base64.b64encode(raw).decode(),'media_type':'text/plain'})
    private_source=deepcopy(flow.capacity_payload)
    private_source.update(title=marker+' source',resource_id=str(uuid.uuid4()),available=99,shared_with_domain_ids=[])
    receipt=flow.act('b','create_object',{'object_type':'CapacityObservation','domain_id':f['domains']['b'],'payload':private_source})
    private_id=receipt['result']['object_id'];private_ref=flow.ref(private_id)
    markers=[marker,evidence['object_id'],evidence['revision_id'],private_id,private_ref['revision_id']]
    private_responses=[]
    for path in ('/v1/objects/'+evidence['object_id'],
                 '/v1/evidence-assets/'+evidence['object_id']+'/revisions/'+evidence['revision_id'],
                 '/v1/objects/'+private_id,
                 '/v1/objects/'+private_id+'/revisions/'+private_ref['revision_id']):
        result=flow.clients['a'].json('GET',path,expected=404)
        assert result['error']['code']=='NOT_FOUND';assert_hidden(result,markers)
        private_responses.append({'path':path,'status':404})
    book.check('A2-18','private_direct_hidden',True,evidence=private_responses)
    relation=flow.clients['a'].json('GET','/v1/objects/'+private_id+'/relations',expected=404)
    assert_hidden(relation,markers)
    # The shared composition route must itself be usable, not produce an empty
    # "safe" test solely because every cross-domain object is inaccessible.
    relation=flow.clients['a'].json('GET','/v1/objects/'+c1['object_id']+'/relations')
    assert_hidden(relation,markers)
    book.check('A2-18','private_relation_hidden',True,evidence={'private_target_status':404,'shared_composition_relations_status':200})
    params=flow.submission_params('a')
    body=flow.prepare('a','publish_domain_submission',params,oid=flow.round_id)
    body['params']['submission']['upstream_refs']=[{'object_id':evidence['object_id'],'revision_id':evidence['revision_id']}]
    before=h.snapshot(f)
    for path in ('/v1/actions/prepare','/v1/actions'):
        response=flow.clients['a'].json('POST',path,body,expected=404)
        assert response['error']['code']=='NOT_FOUND';assert_hidden(response,markers)
        assert h.snapshot(f)==before
    book.check('A2-18','private_prepare_hidden',True,evidence={'prepare_status':404,'execute_status':404,'all_state_unchanged':True})
    # The existing workbench deliberately hides unreadable domain existence.
    inaccessible_list=flow.clients['a'].json('GET','/v1/objects?domain_id='+f['domains']['b']+'&limit=100',expected=404)
    assert inaccessible_list['error']['code']=='NOT_FOUND'
    assert_hidden(inaccessible_list,markers)
    after_list=flow.clients['a'].json('GET','/v1/objects?domain_id='+f['domains']['a']+'&limit=100')
    assert before_list==after_list
    # A later private revision of a once-published source must not be exposed by
    # granting blanket object history access through the old manifest reference.
    changed=deepcopy(flow.capacity_payload);changed.update(title=marker+' new private revision',available=97,shared_with_domain_ids=[])
    flow.act('b','propose_revision',{'payload':changed},oid=flow.capacity_ref['object_id'])
    latest=flow.ref(flow.capacity_ref['object_id'])
    new_hidden=markers+[latest['revision_id']]
    response=flow.clients['a'].json('GET','/v1/objects/'+latest['object_id']+'/revisions/'+latest['revision_id'],expected=404)
    assert_hidden(response,new_hidden)
    response=flow.clients['a'].json('GET','/v1/objects/'+latest['object_id'],expected=404)
    assert_hidden(response,new_hidden)
    book.check('A2-18','no_hidden_count_or_names',True,evidence={'own_list_unchanged':True,
        'other_domain_list_status':404,'new_private_source_revision_hidden':True})
    return {'private_evidence':evidence['object_id'],'private_source':private_id}


def authority_disclosure(flow,book):
    """Current role/policy checks supplement the frozen A2-18 assertions."""
    from .cases_authority import revoke_body
    from .fixture import withdraw_domain_read_policy
    h,f=flow.h,flow.f;c1=flow.build()
    a_sign=next(command['request'] for command in flow.commands
                if command['actor']=='a' and command['request']['action_type']=='confirm_company_composition')
    shared_submission=flow.submissions['b']
    assert flow.manifest(c1,'a')==flow.manifest(c1)
    assert flow.clients['a'].revision(shared_submission['object_id'],shared_submission['revision_id'])['payload']['domain_id']==f['domains']['b']
    flow.clients['ceo'].json('POST','/v1/actions',revoke_body(flow,'a'))
    # Same principal still has a live IC read assignment in its own domain.
    # This cannot substitute for the exact named DRI slot of the Round.
    assert flow.clients['a'].object(flow.submissions['a']['object_id'])['object_id']==flow.submissions['a']['object_id']
    before=h.snapshot(f)
    for path in ('/v1/objects/'+c1['object_id'],
                 '/v1/objects/'+shared_submission['object_id']+'/revisions/'+shared_submission['revision_id']):
        response=flow.clients['a'].request('GET',path,expected=(403,404))
        assert response.json()['error']['code'] in {'FORBIDDEN','NOT_FOUND'}
    response=flow.clients['a'].request('POST','/v1/actions',a_sign,expected=(403,404))
    assert response.json()['error']['code'] in {'FORBIDDEN','NOT_FOUND'}
    assert h.snapshot(f)==before
    # Source publication grants are explicit domain grants, independently of
    # the Round slot. Withdrawing the latest domain read policy must remove
    # them despite the retained older allow policy and live secondary role.
    assert flow.clients['a'].revision(flow.capacity_ref['object_id'],flow.capacity_ref['revision_id'])['payload']['available']==3
    withdraw_domain_read_policy(h.env,f,'a')
    before=h.snapshot(f)
    response=flow.clients['a'].request('GET','/v1/objects/'+flow.capacity_ref['object_id']+'/revisions/'+flow.capacity_ref['revision_id'],expected=404)
    assert response.json()['error']['code']=='NOT_FOUND' and h.snapshot(f)==before
    evidence={'retained_ic_did_not_replace_dri':True,'historical_own_signature_not_authority':True,
              'latest_read_policy_replaces_old_allow':True,'denied_reads_and_replay_did_not_write':True}
    book.gates['read_authority_rechecks']={'passed':True,'evidence':evidence}
    return evidence
