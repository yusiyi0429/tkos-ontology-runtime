"""A3 public read visibility must not expand to internal company admission data."""
from copy import deepcopy

from acceptance.composition_a2_independent.fixture import withdraw_domain_read_policy
from acceptance.composition_a2_independent.cases_disclosure import assert_hidden
from . import oracle


def reads(flow):
    flow.ready()
    body = flow.prepare('ic', 'submit_deliverable', flow.submit_params([flow.upload()]), oid=flow.work_id)
    foreign_mission = next(m for m in flow.company_receipt['result']['missions'] if m['domain_id'] == flow.f['domains']['a'])
    forbidden_cas = {flow.round_id, flow.composition['object_id'], foreign_mission['object_id'], flow.submissions['a']['object_id']}
    assert not forbidden_cas.intersection(v['object_id'] for v in body['expected_versions'])
    submitted = flow.commit('ic', body)
    assert not forbidden_cas.intersection(submitted['result']['referenced_object_ids'])
    for oid in [flow.ec_id, flow.work_id, flow.plan_id, flow.ec_payload['mission_ref']['object_id'],
                flow.ec_payload['domain_commitment_ref']['object_id'], flow.company_ref['object_id']]:
        assert flow.clients['ic'].object(oid)['object_id'] == oid
    before = flow.h.snapshot(flow.f)
    for oid in [foreign_mission['object_id'], flow.submissions['a']['object_id'], flow.composition['object_id'], flow.round_id]:
        response = flow.clients['ic'].json('GET', '/v1/objects/' + oid, expected=404)
        assert response['error']['code'] == 'NOT_FOUND'
        assert_hidden(response, [oid])
    assert flow.h.snapshot(flow.f) == before
    receipt_url = '/v1/action-receipts/' + submitted['receipt_id']
    read_receipt = flow.clients['ic'].json('GET', receipt_url)
    assert read_receipt['receipt'] == submitted and read_receipt['effects'] == []
    withdraw_domain_read_policy(flow.h.env, flow.f, 'b')
    stable = flow.h.snapshot(flow.f)
    for path in ['/v1/objects/' + flow.work_id, receipt_url]:
        response = flow.clients['ic'].json('GET', path, expected=(403, 404))
        assert response['error']['code'] in {'FORBIDDEN', 'NOT_FOUND'}
    assert flow.h.snapshot(flow.f) == stable
    return {'own_responsibility_material_readable': True, 'hidden_company_objects_not_in_cas_or_receipt': True,
            'foreign_objects_hidden': True, 'current_read_policy_rechecked': True}
