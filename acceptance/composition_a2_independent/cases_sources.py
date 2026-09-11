"""Real HTTP admission for A2 inputs before the Round handlers are exercised."""
from copy import deepcopy


def source_inputs(flow, book):
    h,f=flow.h,flow.f
    company=flow.create_reference();flow.company_ref=company
    cap=flow.create_capacity()
    assert flow.clients['a'].revision(cap['object_id'],cap['revision_id'])['payload']['available']==3
    commands=deepcopy(flow.commands)
    before=h.snapshot(f)
    for command in commands:
        assert flow.clients[command['actor']].json('POST','/v1/actions',command['request'])==command['receipt']
    assert h.snapshot(f)==before
    # A source revision is a controlled publication with current visibility.
    # A domain grant on R1 does not grant access to a later private R2.
    private=deepcopy(flow.capacity_payload)
    private.update(available=2,shared_with_domain_ids=[])
    flow.act('b','propose_revision',{'payload':private},oid=cap['object_id'])
    latest=flow.ref(cap['object_id'])
    assert latest['revision_id']!=cap['revision_id']
    for path in ('/v1/objects/'+cap['object_id'],
                 '/v1/objects/'+cap['object_id']+'/revisions/'+latest['revision_id']):
        response=flow.clients['a'].json('GET',path,expected=404)
        assert response['error']['code']=='NOT_FOUND'
    assert flow.clients['a'].revision(cap['object_id'],cap['revision_id'])['payload']['available']==3
    # Both source types support their own authorized revision path.
    company_payload=deepcopy(flow.clients['ceo'].revision(company['object_id'],company['revision_id'])['payload'])
    company_payload['statement']='Synthetic revised company reference before Round adoption'
    flow.act('ceo','propose_revision',{'payload':company_payload},oid=company['object_id'])
    assert flow.ref(company['object_id'])['revision_id']!=company['revision_id']
    # A hidden source must stay hidden on prepare and execute; changing the
    # error code to a protocol/version error would disclose its existence.
    draft=deepcopy(flow.capacity_payload)
    draft['upstream_refs']=[{'object_id':cap['object_id'],'revision_id':latest['revision_id']}]
    body=flow.command('create_object',{'object_type':'CapacityObservation',
        'domain_id':f['domains']['a'],'payload':draft},actor='a')
    before=h.snapshot(f)
    for path in ('/v1/actions/prepare','/v1/actions'):
        response=flow.clients['a'].json('POST',path,body,expected=404)
        assert response['error']['code']=='NOT_FOUND' and h.snapshot(f)==before
    evidence={'company_reference_and_capacity_published_via_http':True,
              'both_source_revision_paths':True,'exact_old_revision_grant_preserved':True,
              'private_new_revision_and_head_hidden':True,'source_receipt_replay_no_write':True,
              'private_prepare_and_commit_rejected':True}
    book.gates['source_input_http']={'passed':True,'evidence':evidence}
    return evidence
