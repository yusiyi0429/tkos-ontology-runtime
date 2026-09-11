"""Synthetic business-event evaluation, separate from delivery and historical MF."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.runtime.client import Client

from . import oracle
from .flow import Flow, uid


def legacy_markers(h, source, url, f):
    """An explicitly bootstrapped legacy scope, then two real HTTP objects.

    This runs before Contract-A registration, with zero business bindings to
    backfill. It does not rebind existing A objects or pretend A3 implements MF.
    """
    assert h.sql(f, 'SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s', (f['scope_id'],))[0]['n'] == 0
    ControlAdapter(h, source, 'memory_service_runtime.governed.control').cli('a3-legacy-markers-' + uid(),
        ['backfill-legacy', '--scope-id', f['scope_id'], '--reason', 'Synthetic empty-scope legacy compatibility setup'], expected_exit=0)
    clients = h.clients(url, f)
    result = {}
    try:
        for object_type, payload in [
            ('FeedbackThread', {'title': 'Synthetic prior MF issue', 'description': 'Existing issue remains independently open.'}),
            ('CompanyOutcome', {'title': 'Synthetic prior legacy outcome', 'terms': {'target': 3}}),
        ]:
            body = Client.command('create_object', {'object_type': object_type,
                                  'domain_id': f['domains']['b'], 'payload': payload})
            prepared = clients['ceo'].json('POST', '/v1/actions/prepare', body)
            body['expected_versions'] = prepared['expected_versions']
            receipt = clients['ceo'].json('POST', '/v1/actions', body)
            oid = receipt['result']['object_id']
            result[object_type] = clients['ceo'].object(oid)
    finally:
        for client in clients.values():
            client.close()
    return result


def packet(flow, complete, *, partial=True):
    occurred = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    events = []
    for customer in flow.customer_ids[:complete]:
        for kind in ['onboarding_completion_event', 'first_valid_business_transaction_event']:
            events.append({'event_id': uid(), 'client_id': customer, 'event_type': kind, 'occurred_at': occurred})
    if partial and complete < len(flow.customer_ids):
        events.append({'event_id': uid(), 'client_id': flow.customer_ids[complete],
                       'event_type': 'onboarding_completion_event', 'occurred_at': occurred})
    return {'schema_version': 'tkos.synthetic.customer-events/0.1', 'record_origin': 'synthetic',
            'company_reference_ref': deepcopy(flow.company_ref), 'period_id': flow.f['period_id'], 'events': events}


def assessment_params(evidence, result, *, reviews=None):
    return {'assessment_result': result, 'observation_revision_ids': [evidence['revision_id']],
            'evidence_revision_ids': [evidence['revision_id']],
            'assessment_note': 'Independent judgment based on distinct qualified synthetic customer events.',
            'delivery_acceptance_ids': reviews or []}


def outcome(flow, book, *, markers):
    flow.ready()
    target_before = flow.object(flow.company_ref['object_id'])
    delivery = flow.submit()
    accepted = flow.review(delivery, accept=True)
    for value in markers.values():
        assert flow.object(value['object_id']) == value
    assert flow.object(flow.company_ref['object_id']) == target_before
    assert oracle.rows(flow, 'gov_a3_outcome_assessments') == []
    book.check('A3-11', 'accepted_delivery_leaves_outcome_and_mf_unchanged', True,
               evidence={'delivery_receipt': accepted['receipt_id'], 'outcome_assessments': [],
                         'legacy_markers_unchanged': list(markers), 'MF_scope': 'existing independent same-scope issue'})
    before_deliveries = oracle.rows(flow, 'gov_a3_delivery_acceptances')
    events = packet(flow, 2)
    # Duplicate same-customer events cannot supply the missing third customer.
    duplicate = deepcopy(events['events'][0])
    duplicate['event_id'] = uid()
    events['events'].append(duplicate)
    evidence = flow.upload(events, actor='ceo', domain='company')
    review_ids = [accepted['result']['delivery_acceptance_id']]
    bad = flow.command('record_outcome_assessment', assessment_params(evidence, 'achieved', reviews=review_ids),
                        oid=flow.company_ref['object_id'])
    failures = [oracle.denied(flow, 'ceo', bad, {'INVALID_STATE', 'INVALID_REQUEST', 'OUTCOME_EVIDENCE_MISMATCH'})]
    login_only = deepcopy(events)
    for event in login_only['events']:
        event['event_type'] = 'login_event'
    login_evidence = flow.upload(login_only, actor='ceo', domain='company')
    failures.append(oracle.denied(flow, 'ceo', flow.command('record_outcome_assessment',
        assessment_params(login_evidence, 'achieved', reviews=review_ids), oid=flow.company_ref['object_id']),
        {'INVALID_REQUEST', 'INVALID_STATE', 'OUTCOME_EVIDENCE_MISMATCH'}))
    future = packet(flow, 3, partial=False)
    for event in future['events']:
        event['occurred_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    future_evidence = flow.upload(future, actor='ceo', domain='company')
    failures.append(oracle.denied(flow, 'ceo', flow.command('record_outcome_assessment',
        assessment_params(future_evidence, 'achieved', reviews=review_ids), oid=flow.company_ref['object_id']),
        {'INVALID_REQUEST', 'INVALID_STATE', 'OUTCOME_EVIDENCE_MISMATCH'}))
    event_conflict = deepcopy(events)
    collision = deepcopy(event_conflict['events'][0])
    collision['client_id'] = 'SYN-C03'
    event_conflict['events'].append(collision)
    conflicting = flow.upload(event_conflict, actor='ceo', domain='company')
    failures.append(oracle.denied(flow, 'ceo', flow.command('record_outcome_assessment',
        assessment_params(conflicting, 'not_achieved'), oid=flow.company_ref['object_id']),
        {'INVALID_REQUEST', 'INVALID_STATE', 'OUTCOME_EVIDENCE_MISMATCH'}))
    p01 = flow.act('ceo', 'record_outcome_assessment', assessment_params(evidence, 'not_achieved', reviews=review_ids),
                   oid=flow.company_ref['object_id'])
    rows = oracle.rows(flow, 'gov_a3_outcome_assessments')
    assert len(rows) == 1
    assert rows[0]['qualified_customer_count'] == 2 and rows[0]['target_count'] == 3
    assert rows[0]['assessment_result'] == 'not_achieved'
    book.check('A3-11', 'p01_two_distinct_qualified_customers_not_three', True,
               evidence={'assessment': rows[0], 'negative_observations': failures, 'receipt': p01['receipt_id']})

    p02_fixture = deepcopy(flow.f)
    p02_fixture['period_id'] = uid()
    next_period = Flow(flow.h, flow.url, p02_fixture)
    try:
        next_period.customer_ids = ['SYN-C04', 'SYN-C05', 'SYN-C06']
        next_period.company_activation()
        old_packet = next_period.command('record_outcome_assessment', assessment_params(evidence, 'achieved'),
                                          oid=next_period.company_ref['object_id'])
        old_rejected = oracle.denied(next_period, 'ceo', old_packet,
                                     {'INVALID_STATE', 'INVALID_REQUEST', 'STALE_DEPENDENCY', 'OUTCOME_EVIDENCE_MISMATCH'})
        evidence2 = next_period.upload(packet(next_period, 3, partial=False), actor='ceo', domain='company')
        p02 = next_period.act('ceo', 'record_outcome_assessment', assessment_params(evidence2, 'achieved'),
                              oid=next_period.company_ref['object_id'])
        rows = oracle.rows(next_period, 'gov_a3_outcome_assessments')
        current = next(r for r in rows if r['company_reference_object_id'] == next_period.company_ref['object_id'])
        assert current['qualified_customer_count'] == current['target_count'] == 3
        assert current['assessment_result'] == 'achieved'
        assert current['period_id'] == p02_fixture['period_id']
        book.check('A3-11', 'p02_three_qualified_customers_achieved_independently', True,
                   evidence={'assessment': current, 'old_period_rejected': old_rejected, 'receipt': p02['receipt_id']})
    finally:
        next_period.close()
    assert oracle.rows(flow, 'gov_a3_delivery_acceptances') == before_deliveries
    for value in markers.values():
        assert flow.object(value['object_id']) == value
    after_target = flow.object(flow.company_ref['object_id'])
    assert after_target['latest_revision'] == target_before['latest_revision']
    assert after_target['effective_revision'] == target_before['effective_revision']
    book.check('A3-11', 'outcome_assessment_does_not_rewrite_delivery_or_mf', True,
               evidence={'delivery_reviews_unchanged': True, 'legacy_markers_unchanged': list(markers),
                         'target_revision_content_unchanged': True})
    return {'p01': p01, 'p02': p02}
