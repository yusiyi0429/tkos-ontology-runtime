"""Authority, plan/What separation and protocol downgrade resistance."""
from copy import deepcopy

from . import oracle
from .flow import Flow, uid


def admission(flow, book):
    flow.ready()
    evidence = flow.upload()
    submit = flow.submit_params([evidence])
    wrong_ic = flow.command('submit_deliverable', submit, oid=flow.work_id, actor='wrong_ic')
    proof = oracle.denied(flow, 'wrong_ic', wrong_ic, {'FORBIDDEN'})
    book.check('A3-06', 'wrong_ic_submission_rejected', True, evidence=proof)
    wrong_plan = flow.command('propose_revision', {'payload': flow.plan_payload}, oid=flow.plan_id, actor='wrong_ic')
    proof = oracle.denied(flow, 'wrong_ic', wrong_plan, {'FORBIDDEN'})
    book.check('A3-04', 'foreign_actor_plan_rejected', True, evidence=proof)

    changes = []
    for field, value in [('what', flow.ec_payload['what']), ('acceptance_criteria', []),
                         ('hard_deadline', flow.period_end.isoformat()),
                         ('ic_assignment_id', flow.f['actors']['wrong_ic']['assignment_id']),
                         ('external_dependency_refs', [flow.capacity_ref])]:
        payload = deepcopy(flow.plan_payload)
        payload[field] = value
        changes.append(oracle.denied(flow, 'ic', flow.command('propose_revision', {'payload': payload},
            oid=flow.plan_id, actor='ic'), {'INVALID_REQUEST'}))
    late = deepcopy(flow.plan_payload)
    late['steps'][0]['due_at'] = flow.period_end.isoformat()
    changes.append(oracle.denied(flow, 'ic', flow.command('propose_revision', {'payload': late},
        oid=flow.plan_id, actor='ic'), {'INVALID_REQUEST', 'INVALID_STATE'}))
    work = flow.work_params()
    work['payload']['acceptance_criteria'][0]['description'] = 'Add a new unconfirmed responsibility'
    changes.append(oracle.denied(flow, 'b', flow.command('create_object', work), {'INVALID_STATE', 'INVALID_REQUEST'}))
    book.check('A3-04', 'what_criteria_deadline_role_dependency_changes_rejected', True, evidence=changes)

    old_plan = flow.ref(flow.plan_id, 'ic')
    flow.revise_plan()
    stale_plan = deepcopy(submit)
    stale_plan['plan_ref'] = old_plan
    proof = oracle.denied(flow, 'ic', flow.command('submit_deliverable', stale_plan, oid=flow.work_id, actor='ic'),
                          {'STALE_DEPENDENCY', 'INVALID_STATE'})
    book.check('A3-04', 'current_plan_required', True, evidence=proof)
    before_task = flow.work_id
    before_plan = flow.plan_id
    flow.create_work()
    body = flow.command('submit_deliverable', flow.submit_params([evidence]), oid=flow.work_id, actor='ic')
    proof = oracle.denied(flow, 'ic', body, {'INVALID_STATE', 'WORK_ITEM_NOT_ACCEPTED'})
    book.check('A3-03', 'submit_before_reception_rejected', True, evidence=proof)
    flow.work_id, flow.plan_id = before_task, before_plan

    v1 = flow.submit([evidence])
    review = flow.review_params(v1)
    proofs = [oracle.denied(flow, 'ceo', flow.command('submit_deliverable', flow.submit_params([evidence]),
              oid=flow.work_id, actor='ceo'), {'FORBIDDEN'}),
              oracle.denied(flow, 'ceo', flow.command('review_deliverable', review,
              oid=flow.work_id, actor='ceo'), {'FORBIDDEN'})]
    book.check('A3-01', 'company_approval_cannot_submit_or_review', True, evidence=proofs)
    proof = oracle.denied(flow, 'ic', flow.command('review_deliverable', review, oid=flow.work_id, actor='ic'), {'FORBIDDEN'})
    book.check('A3-06', 'self_review_via_other_role_rejected', True,
               evidence={'actor_also_has_verifier_assignment': flow.f['ic_verifier_assignment'], 'rejection': proof})
    proof = oracle.denied(flow, 'wrong_ic', flow.command('review_deliverable', review, oid=flow.work_id, actor='wrong_ic'), {'FORBIDDEN'})
    # A valid appointment for the SAME human in another real period is still
    # the wrong authority for this WorkItem. No fake appointment is seeded.
    other_fixture = deepcopy(flow.f)
    other_fixture['period_id'] = uid()
    other_period = Flow(flow.h, flow.url, other_fixture)
    try:
        other_period.ready()
        foreign_appointment = deepcopy(review)
        foreign_appointment.update(other_period.appointment)
        assert foreign_appointment['appointment_id'] != flow.appointment['appointment_id']
        proof += oracle.denied(flow, 'reviewer', flow.command('review_deliverable', foreign_appointment,
            oid=flow.work_id, actor='reviewer'), {'FORBIDDEN', 'INVALID_STATE', 'STALE_DEPENDENCY'})
    finally:
        other_period.close()
    book.check('A3-06', 'unauthorized_review_rejected', True, evidence=proof)
    return {'admission_boundaries_complete': True}


def coauthor(flow, book):
    flow.ready()
    evidence = flow.upload(actor='reviewer')
    submission = flow.submit([evidence])
    revision = flow.clients['ceo'].revision(submission['result']['deliverable_object_id'],
                                            submission['result']['deliverable_revision_id'])
    assert set(revision['payload']['author_principal_ids']) == {
        flow.f['actors']['ic']['principal_id'], flow.f['actors']['reviewer']['principal_id']}
    body = flow.command('review_deliverable', flow.review_params(submission, accept=True),
                         oid=flow.work_id, actor='reviewer')
    proof = oracle.denied(flow, 'reviewer', body, {'FORBIDDEN'})
    book.check('A3-06', 'trusted_coauthor_review_rejected', True,
               evidence={'trusted_authors': revision['payload']['author_principal_ids'], 'rejection': proof})
    return {'trusted_evidence_author_conflict_checked': True}


def revision_errors(flow, book):
    flow.ready()
    v1 = flow.submit()
    returned = flow.review(v1)
    v2 = flow.submit(responds_to=returned['result']['delivery_acceptance_id'])
    stale = flow.command('review_deliverable', flow.review_params(v1, accept=True), oid=flow.work_id, actor='reviewer')
    proof = oracle.denied(flow, 'reviewer', stale, {'STALE_DEPENDENCY', 'INVALID_STATE', 'DELIVERY_ALREADY_REVIEWED'})
    book.check('A3-07', 'old_revision_review_rejected', True, evidence=proof)
    # Another real task has its own real v1/return; the two return IDs are not fungible.
    flow.create_work()
    flow.accept_work()
    flow.create_plan()
    other_v1 = flow.submit()
    other_return = flow.review(other_v1)
    wrong = flow.command('submit_deliverable', flow.submit_params([flow.upload()],
                         responds_to=returned['result']['delivery_acceptance_id']), oid=flow.work_id, actor='ic')
    proof = oracle.denied(flow, 'ic', wrong, {'INVALID_STATE', 'STALE_DEPENDENCY'})
    book.check('A3-07', 'other_task_return_rejected', True,
               evidence={'correct_return_id': other_return['result']['delivery_acceptance_id'], 'rejection': proof})
    return {'old_revision_id': v1['result']['deliverable_revision_id'], 'current_revision_id': v2['result']['deliverable_revision_id']}


def protocol(flow, book):
    flow.ready()
    params = flow.submit_params([flow.upload()])
    good = flow.prepare('ic', 'submit_deliverable', params, oid=flow.work_id)
    proofs = []
    for contract in [None, 'tkos.governed/v0.2']:
        body = deepcopy(good)
        body['idempotency_key'] = 'a3-downgrade-' + uid()
        if contract is None:
            body.pop('contract_version')
        else:
            body['contract_version'] = contract
        proofs.append(oracle.denied(flow, 'ic', body, {'PROTOCOL_UPGRADE_REQUIRED', 'INVALID_REQUEST'}))
    book.check('A3-12', 'absent_or_legacy_contract_cannot_downgrade', True, evidence=proofs)
    generic = []
    for object_type in ['Deliverable', 'Mission', 'DomainCommitment', 'CompanyOutcome', 'MetricObservation']:
        body = flow.command('create_object', {'object_type': object_type, 'domain_id': flow.f['domains']['b'],
            'payload': {'title': 'Attempted generic A3 bypass', 'terms': {}}})
        generic.append(oracle.denied(flow, 'b', body, {'ACTION_NOT_SUPPORTED_FOR_PROTOCOL', 'INVALID_REQUEST'}))
    body = flow.command('propose_revision', {'payload': flow.work_params()['payload']}, oid=flow.work_id, actor='b')
    generic.append(oracle.denied(flow, 'b', body, {'ACTION_NOT_SUPPORTED_FOR_PROTOCOL', 'INVALID_REQUEST'}))
    old_shape = {'title': 'Legacy-shaped attempt against a Contract-A execution commitment',
                 'terms': {'target': 99},
                 'required_assignment_ids': [flow.f['actors'][a]['assignment_id'] for a in ['b', 'ic']],
                 'upstream_refs': [{k: flow.ec_payload['mission_ref'][k] for k in ['object_id', 'revision_id']}]}
    body = flow.command('create_object', {'object_type': 'ExecutionCommitment',
                        'domain_id': flow.f['domains']['b'], 'payload': old_shape})
    generic.append(oracle.denied(flow, 'b', body, {'INVALID_REQUEST', 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL', 'INVALID_STATE'}))
    body = flow.command('propose_revision', {'payload': old_shape}, oid=flow.ec_id, actor='b')
    generic.append(oracle.denied(flow, 'b', body, {'INVALID_REQUEST', 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL', 'INVALID_STATE'}))
    timed_creation = flow.work_params()
    timed_creation['valid_from'] = flow.period_end.isoformat()
    generic.append(oracle.denied(flow, 'b', flow.command('create_object', timed_creation), {'INVALID_REQUEST'}))
    book.check('A3-12', 'generic_create_propose_cannot_bypass', True, evidence=generic)
    assert all(row['path'] == '/v1/actions/prepare' for pair in proofs + generic for row in pair[:1])
    book.check('A3-12', 'prepare_enforces_same_protocol_boundaries', True, evidence={'negative_pairs': len(proofs + generic)})
    return {'protocol_boundaries_checked': True}
