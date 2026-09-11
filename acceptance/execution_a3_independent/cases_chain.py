"""Normal delivery chain plus independent boundary and immutable version checks."""
from copy import deepcopy

from . import oracle
from .flow import uid


def chain(flow, book):
    flow.company_activation()
    empty = {t: oracle.rows(flow, t) for t in (
        'gov_execution_state', 'gov_execution_authorities', 'gov_acceptance_appointments', 'gov_work_receipts')}
    assert all(not values for values in empty.values())
    book.check('A3-01', 'company_activation_has_no_ic_or_work_receipt', True, evidence=empty)
    flow.create_execution()
    params = {'handshake_record_ids': [uid(), uid()],
              'activation_policy_revision_id': flow.f['policy_revision_ids']['b']}
    proof = oracle.denied(flow, 'b', flow.command('activate_commitment', params, oid=flow.ec_id, actor='b'),
                          {'CONFIRMATION_INCOMPLETE', 'INVALID_STATE', 'FORBIDDEN', 'NOT_FOUND'})
    book.check('A3-01', 'unaccepted_execution_rejected', True, evidence=proof)
    signs = [flow.confirm_execution('b'), flow.confirm_execution('ic')]
    confirms = oracle.rows(flow, 'gov_handshakes')
    assert len(confirms) == 2
    assert {r['principal_id'] for r in confirms} == {flow.f['actors'][a]['principal_id'] for a in ['b', 'ic']}
    assert {r['revision_id'] for r in confirms} == {flow.ref(flow.ec_id, 'ic')['revision_id']}
    book.check('A3-02', 'two_distinct_humans_exact_revision', True, evidence=confirms)
    flow.activate_execution(signs)
    authorities = oracle.rows(flow, 'gov_execution_authorities')
    assert len(authorities) == 1
    book.check('A3-03', 'single_current_execution_authority', True, evidence=authorities)
    flow.create_work()
    before_what = deepcopy(flow.ec_payload['what'])
    acceptance = flow.accept_work()
    work_receipts = oracle.rows(flow, 'gov_work_receipts')
    assert len(work_receipts) == 1
    wr = work_receipts[0]
    # The exact reference is independently compared against actual HTTP-created records.
    assert wr['work_item_revision_id'] == flow.ref(flow.work_id, 'ic')['revision_id']
    assert wr['authority_id'] == flow.authority['execution_authority_id']
    assert wr['execution_epoch'] == flow.authority['execution_epoch']
    book.check('A3-03', 'receipt_binds_task_revision_and_epoch', True, evidence=wr)
    assert flow.object(flow.ec_id, 'ic')['effective_revision']['payload']['what'] == before_what
    assert flow.object(flow.work_id, 'ic')['effective_revision']['payload']['acceptance_criteria'] == before_what['acceptance_criteria']
    book.check('A3-03', 'task_reception_does_not_duplicate_what', True, evidence=oracle.receipt(flow, acceptance))

    flow.create_plan()
    p1 = flow.ref(flow.plan_id, 'ic')
    assert flow.object(flow.plan_id, 'ic')['effective_revision_id'] == p1['revision_id']
    book.check('A3-04', 'designated_ic_creates_plan', True, evidence=p1)
    handshakes = oracle.rows(flow, 'gov_handshakes')
    flow.revise_plan()
    p2 = flow.ref(flow.plan_id, 'ic')
    assert p1['revision_id'] != p2['revision_id']
    assert flow.clients['ic'].revision(flow.plan_id, p1['revision_id'])['payload_hash'] == p1['payload_hash']
    assert flow.object(flow.ec_id, 'ic')['effective_revision']['payload']['what'] == before_what
    book.check('A3-04', 'how_revision_preserves_history', True, evidence={'v1': p1, 'v2': p2})
    assert oracle.rows(flow, 'gov_handshakes') == handshakes
    assert flow.object(flow.plan_id, 'ic')['effective_revision_id'] == p2['revision_id']
    book.check('A3-04', 'plan_has_no_extra_approval', True, evidence={'plan_ref': p2, 'signatures_unchanged': True})

    v1 = flow.submit()
    returned = flow.review(v1)
    v2 = flow.submit(responds_to=returned['result']['delivery_acceptance_id'])
    accepted = flow.review(v2, accept=True)
    assert v1['result']['deliverable_object_id'] == v2['result']['deliverable_object_id']
    assert v1['result']['deliverable_revision_id'] != v2['result']['deliverable_revision_id']
    deliverable = v1['result']['deliverable_object_id']
    revisions = [flow.clients['ic'].revision(deliverable, r['result']['deliverable_revision_id']) for r in (v1, v2)]
    assert [r['payload_hash'] for r in revisions] == [r['result']['payload_hash'] for r in (v1, v2)]
    book.check('A3-05', 'one_deliverable_two_immutable_revisions', True, evidence=revisions)
    assert revisions[1]['payload']['responds_to_acceptance_id'] == returned['result']['delivery_acceptance_id']
    book.check('A3-05', 'v2_responds_to_exact_return', True, evidence={'return_id': returned['result']['delivery_acceptance_id']})
    reviews = oracle.rows(flow, 'gov_a3_delivery_acceptances')
    assert len(reviews) == 2
    assert {r['deliverable_revision_id'] for r in reviews} == {r['revision_id'] for r in revisions}
    for row in reviews:
        assert row['verifier_principal_id'] == flow.f['actors']['reviewer']['principal_id']
        assert {c['criterion_id'] for c in row['criterion_results']} == {'proof'}
    book.check('A3-05', 'reviews_bind_each_revision_and_criteria', True, evidence=reviews)
    evidence = [oracle.evidence_bytes(flow, e) for e in flow.evidence]
    for revision in revisions:
        assert set(revision['payload']['author_principal_ids']) == {flow.f['actors']['ic']['principal_id']}
    book.check('A3-05', 'evidence_hash_author_and_receipt_traceable', True,
               evidence={'evidence': evidence, 'receipts': [oracle.receipt(flow, r) for r in (v1, returned, v2, accepted)]})
    repeat = flow.command('review_deliverable', flow.review_params(v2, accept=True), oid=flow.work_id, actor='reviewer')
    proof = oracle.denied(flow, 'reviewer', repeat, {'INVALID_STATE', 'DELIVERY_ALREADY_REVIEWED'})
    book.check('A3-07', 'new_key_cannot_repeat_terminal_review', True, evidence=proof)
    return {'v1': v1, 'returned': returned, 'v2': v2, 'accepted': accepted,
            'plan_refs': [p1, p2], 'no_effects': oracle.no_effects(flow)}


def confirmations(flow, book):
    flow.company_activation()
    params = flow.execution_params()
    same = deepcopy(params)
    same['payload']['ic_assignment_id'] = flow.f['b_as_ic_assignment']
    proof = oracle.denied(flow, 'b', flow.command('create_object', same), {'FORBIDDEN', 'INVALID_REQUEST', 'INVALID_STATE'})
    book.check('A3-02', 'same_human_two_roles_rejected', True, evidence=proof)
    agent = deepcopy(params)
    agent['payload']['ic_assignment_id'] = flow.f['actors']['agent_ic']['assignment_id']
    agent_proof = oracle.denied(flow, 'b', flow.command('create_object', agent), {'FORBIDDEN', 'INVALID_REQUEST'})
    flow.create_execution()
    agent_params = flow.confirm_params_ec('b')
    agent_params['party_assignment_id'] = flow.f['actors']['agent_ic']['assignment_id']
    attempted_signature = flow.command('accept_commitment', agent_params, oid=flow.ec_id, actor='b')
    agent_proof += oracle.denied(flow, 'agent_ic', attempted_signature, {'FORBIDDEN'})
    book.check('A3-02', 'agent_signature_rejected', True, evidence=agent_proof)
    wrong = flow.confirm_params_ec('wrong_ic')
    proof = oracle.denied(flow, 'wrong_ic', flow.command('accept_commitment', wrong, oid=flow.ec_id, actor='wrong_ic'), {'FORBIDDEN'})
    book.check('A3-02', 'wrong_assignment_rejected', True, evidence=proof)
    first = flow.confirm_execution('b')
    original = flow.ref(flow.ec_id, 'b')
    payload = deepcopy(flow.ec_payload)
    payload['title'] = 'Revised interpretation awaiting fresh dual confirmation'
    flow.act('b', 'propose_revision', {'payload': payload}, oid=flow.ec_id)
    second = flow.confirm_execution('ic')
    assert flow.ref(flow.ec_id, 'ic')['revision_id'] != original['revision_id']
    activate = flow.command('activate_commitment', {
        'handshake_record_ids': [first['result']['handshake_id'], second['result']['handshake_id']],
        'activation_policy_revision_id': flow.f['policy_revision_ids']['b']}, oid=flow.ec_id, actor='b')
    proof = oracle.denied(flow, 'b', activate, {'CONFIRMATION_INCOMPLETE', 'INVALID_STATE', 'FORBIDDEN'})
    book.check('A3-02', 'different_revision_rejected', True,
               evidence={'old_exact_ref': original, 'rejection': proof, 'mutable_target_is_current': True})
    return {'confirmation_negatives_complete': True}
