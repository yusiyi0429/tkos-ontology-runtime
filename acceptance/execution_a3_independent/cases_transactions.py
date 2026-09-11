"""Real independent HTTP races, final admission expiry and first-write failures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import sys

from acceptance.composition_a2_independent.concurrency import race, reached, ordered
from acceptance.composition_a2_independent.cases_authority import revoke_body
from acceptance.protocol_a1_independent.support import wait, HERE
from acceptance.runtime.client import Client

from . import oracle
from .fixture import invalidate_execution_epoch
from .flow import uid


def races(flow, book):
    flow.ready()
    uploaded = [flow.upload(), flow.upload()]
    commands = [flow.prepare('ic', 'submit_deliverable', flow.submit_params([e]), oid=flow.work_id) for e in uploaded]
    assert commands[0]['target'] == commands[1]['target']
    responses = race(flow.h, flow.f, flow.url, 'ic', commands)
    assert sorted(r['status'] for r in responses) == [200, 409]
    winner = next(i for i, r in enumerate(responses) if r['status'] == 200)
    submission = responses[winner]['body']
    assert responses[1-winner]['body']['error']['code'] in {'VERSION_CONFLICT', 'INVALID_STATE'}
    book.check('A3-09', 'same_cas_single_submission_winner', True, evidence=responses)
    stable = flow.h.snapshot(flow.f)
    for _ in range(2):
        assert flow.clients['ic'].json('POST', '/v1/actions', commands[winner]) == submission
    assert flow.h.snapshot(flow.f) == stable
    book.check('A3-09', 'original_request_replays_same_receipt', True, evidence={'receipt_id': submission['receipt_id']})
    conflict = deepcopy(commands[winner])
    conflict['params']['evidence_revision_ids'] = [uploaded[1-winner]['revision_id']]
    proof = oracle.denied(flow, 'ic', conflict, {'IDEMPOTENCY_CONFLICT'}, prepare=False)
    book.check('A3-09', 'same_key_changed_evidence_conflicts', True, evidence=proof)
    review = flow.prepare('reviewer', 'review_deliverable', flow.review_params(submission, accept=True), oid=flow.work_id)
    other = deepcopy(review)
    other['idempotency_key'] = 'a3-review-race-' + uid()
    reviews = race(flow.h, flow.f, flow.url, 'reviewer', [review, other])
    assert sorted(r['status'] for r in reviews) == [200, 409]
    receipts = oracle.rows(flow, 'gov_action_receipts')
    assert len([r for r in receipts if r['action_type'] == 'submit_deliverable']) == 1
    assert len([r for r in receipts if r['action_type'] == 'review_deliverable']) == 1
    assert len(oracle.rows(flow, 'gov_a3_delivery_acceptances')) == 1
    book.check('A3-09', 'no_duplicate_submission_or_review', True, evidence=reviews)
    return {'submission_race': responses, 'review_race': reviews}


def rollback(flow, book):
    flow.ready()
    upload = flow.upload()
    body = flow.prepare('ic', 'submit_deliverable', flow.submit_params([upload]), oid=flow.work_id)
    before = flow.h.snapshot(flow.f)
    storage = flow.h.storage_snapshot(flow.f['scope_id'])
    response = flow.clients['ic'].request('POST', '/v1/actions', body, expected=500,
        headers=flow.h.headers('after_a3_submission_write', mode='fail', token='a3-fail-submit-' + uid()))
    assert flow.h.snapshot(flow.f) == before
    assert flow.h.storage_snapshot(flow.f['scope_id']) == storage
    book.check('A3-10', 'submission_first_write_failure_rolls_back', True,
               evidence={'status': response.status_code, 'all_tables_unchanged': True, 'storage_unchanged': True})
    assert not [r for r in oracle.rows(flow, 'gov_action_receipts') if r['action_type'] == 'submit_deliverable']
    assert not [r for r in oracle.rows(flow, 'gov_objects') if r['object_type'] == 'Deliverable']
    oracle.evidence_bytes(flow, flow.evidence[-1])
    book.check('A3-10', 'unlinked_storage_is_not_successful_delivery', True,
               evidence={'uploaded_revision': upload['revision_id'], 'deliverables': 0, 'successful_submission_receipts': 0})
    submitted = flow.commit('ic', body)
    review = flow.prepare('reviewer', 'review_deliverable', flow.review_params(submitted, accept=True), oid=flow.work_id)
    before = flow.h.snapshot(flow.f)
    response = flow.clients['reviewer'].request('POST', '/v1/actions', review, expected=500,
        headers=flow.h.headers('after_a3_review_write', mode='fail', token='a3-fail-review-' + uid()))
    assert flow.h.snapshot(flow.f) == before
    assert oracle.rows(flow, 'gov_a3_delivery_acceptances') == []
    book.check('A3-10', 'review_first_write_failure_rolls_back', True,
               evidence={'status': response.status_code, 'all_tables_unchanged': True, 'reviews': 0})
    accepted = flow.commit('reviewer', review)
    return {'retried_submission': submitted['receipt_id'], 'retried_review': accepted['receipt_id']}


def revoke(flow, book):
    flow.ready()
    submitted = flow.submit()
    returned = flow.review(submitted)
    params = flow.submit_params([flow.upload()], responds_to=returned['result']['delivery_acceptance_id'])
    body = flow.prepare('ic', 'submit_deliverable', params, oid=flow.work_id)
    history = {t: oracle.rows(flow, t) for t in ['gov_execution_authorities', 'gov_handshakes', 'gov_work_receipts', 'gov_object_revisions']}
    response = flow.clients['ceo'].json('POST', '/v1/actions', revoke_body(flow, 'ic'))
    proof = oracle.denied(flow, 'ic', body, {'FORBIDDEN'}, prepare=False)
    book.check('A3-08', 'ic_revoked_after_prepare_rejected', True, evidence={'revocation_receipt': response['receipt_id'], 'rejection': proof})
    receipt_command = next(x for x in flow.commands if x['receipt']['receipt_id'] == flow.work_receipt['receipt_id'])
    proof = oracle.denied(flow, 'ic', receipt_command['request'], {'FORBIDDEN'}, prepare=False)
    book.check('A3-08', 'old_receipt_does_not_restore_authority', True, evidence=proof)
    assert history == {t: oracle.rows(flow, t) for t in history}
    assignments = oracle.rows(flow, 'gov_role_assignments')
    row = next(r for r in assignments if r['assignment_id'] == flow.f['actors']['ic']['assignment_id'])
    assert row['active'] is False
    book.check('A3-08', 'historic_assignments_and_authors_preserved', True,
               evidence={'historic_tables_unchanged': list(history), 'revoked_assignment_retained': row})
    return {'revocation_after_prepare_checked': True}


def epoch(flow, book):
    flow.ready()
    body = flow.prepare('ic', 'submit_deliverable', flow.submit_params([flow.upload()]), oid=flow.work_id)
    authorities = oracle.rows(flow, 'gov_execution_authorities')
    invalidate_execution_epoch(flow.h, flow.f, flow.ec_id)
    assert authorities == oracle.rows(flow, 'gov_execution_authorities')
    state = next(r for r in oracle.rows(flow, 'gov_execution_state') if r['commitment_object_id'] == flow.ec_id)
    assert state['current_execution_epoch'] == flow.authority['execution_epoch'] + 1
    body = oracle.fresh(flow, body, actor='ic')
    proof = oracle.denied(flow, 'ic', body, {'STALE_DEPENDENCY', 'INVALID_STATE', 'FORBIDDEN', 'EXECUTION_AUTHORITY_STALE'})
    book.check('A3-08', 'old_authority_epoch_rejected', True,
               evidence={'negative_only_epoch_invalidation': True, 'historic_authority_unchanged': True,
                         'current_epoch': state['current_execution_epoch'],
                         'historic_epoch': flow.authority['execution_epoch'], 'rejection': proof})
    return {'epoch_invalidation_checked': True, 'reassignment_implemented': False}


def expiry(flow, book):
    flow.ready(execution_seconds=18)
    body = flow.prepare('ic', 'submit_deliverable', flow.submit_params([flow.upload()]), oid=flow.work_id)
    token = 'a3-expiry-' + uid()
    before = flow.h.snapshot(flow.f)
    epochs = flow.h.sql(flow.f, 'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s', (flow.f['scope_id'],))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(flow.clients['ic'].request, 'POST', '/v1/actions', body, expected=(403, 409),
            headers=flow.h.headers('before_business_commit', mode='barrier', token=token))
        try:
            barrier = reached(flow.h, token)
            def expired():
                values = flow.h.sql(flow.f, '''SELECT valid_from,valid_to,clock_timestamp() AS observed_at,
                    valid_from<=clock_timestamp() AND clock_timestamp()<valid_to AS valid
                    FROM gov_execution_authorities WHERE scope_id=%s''', (flow.f['scope_id'],))
                return values if len(values) == 1 and values[0]['valid'] is False else None
            times = wait(expired, timeout=25)
            (flow.h.control / (token + '.release')).touch(mode=0o600)
            response = future.result(timeout=20)
            assert response.json()['error']['code'] in {'FORBIDDEN', 'INVALID_STATE', 'EXECUTION_AUTHORITY_EXPIRED'}
            assert flow.h.snapshot(flow.f) == before
            assert flow.h.sql(flow.f, 'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s', (flow.f['scope_id'],)) == epochs
            book.check('A3-08', 'authority_naturally_expires_at_final_barrier', True,
                       evidence={'barrier': barrier, 'database_clock': times, 'no_epoch_mutation': True,
                                 'all_business_state_unchanged': True, 'status': response.status_code})
            return {'natural_expiry_checked': True}
        finally:
            (flow.h.control / (token + '.release')).touch(mode=0o600, exist_ok=True)
            try:
                future.result(timeout=35)
            except Exception:
                pass


def appointment_expiry(flow, book):
    flow.ready(acceptance_seconds=10)
    submitted = flow.submit()
    body = flow.prepare('reviewer', 'review_deliverable', flow.review_params(submitted, accept=True), oid=flow.work_id)
    def expired():
        values = flow.h.sql(flow.f, 'SELECT valid_to,clock_timestamp() AS observed_at FROM gov_acceptance_appointments WHERE scope_id=%s', (flow.f['scope_id'],))
        return values if values and values[0]['valid_to'] <= values[0]['observed_at'] else None
    times = wait(expired, timeout=15)
    proof = oracle.denied(flow, 'reviewer', body, {'FORBIDDEN', 'INVALID_STATE', 'ACCEPTANCE_APPOINTMENT_EXPIRED'})
    book.check('A3-06', 'expired_appointment_review_rejected', True,
               evidence={'appointment_clock': times, 'rejection': proof})
    return {'appointment_expiry_checked': True}


def review_after_execution_end(flow):
    """A still-appointed independent verifier may assess already submitted work.

    The IC's expired/revoked execution right cannot be silently reused as the
    verifier's appointment gate. No replacement or temporary delegation occurs.
    """
    flow.ready(execution_seconds=10)
    submitted = flow.submit()
    original = flow.clients['ceo'].revision(submitted['result']['deliverable_object_id'],
                                             submitted['result']['deliverable_revision_id'])
    flow.clients['ceo'].json('POST', '/v1/actions', revoke_body(flow, 'ic'))
    def expired():
        rows = flow.h.sql(flow.f,
            'SELECT valid_to,clock_timestamp() AS observed_at FROM gov_execution_authorities WHERE scope_id=%s',
            (flow.f['scope_id'],))
        return rows if rows and rows[0]['valid_to'] <= rows[0]['observed_at'] else None
    times = wait(expired, timeout=15)
    receipt = flow.review(submitted, accept=True)
    assert flow.clients['ceo'].revision(original['object_id'], original['revision_id']) == original
    return {'execution_expired_at_database_clock': times, 'ic_assignment_revoked': True,
            'independent_current_appointment_accepted_submission': receipt['receipt_id'],
            'original_submission_and_authors_unchanged': True}


def source_change(flow, book):
    flow.ready()
    prepared = flow.prepare('ic', 'submit_deliverable', flow.submit_params([flow.upload()]), oid=flow.work_id)
    flow.revise_capacity(2)
    prepared = oracle.fresh(flow, prepared, actor='ic')
    proof = oracle.denied(flow, 'ic', prepared, {'COMPOSITION_INPUT_CHANGED', 'STALE_DEPENDENCY'})
    book.check('A3-13', 'binding_source_change_rechecked_on_submit', True, evidence=proof)
    other = deepcopy(flow.capacity_payload)
    other['available'] = 3
    body = flow.command('propose_revision', {'payload': other}, oid=flow.capacity_ref['object_id'], actor='ic_a')
    proof = oracle.denied(flow, 'ic_a', body, {'FORBIDDEN'})
    book.check('A3-13', 'ic_cannot_repair_foreign_domain_dependency', True, evidence=proof)
    return {'binding_revalidation_checked': True}


def source_order(flow, *, peer_url, primary_name, peer_name, source_first):
    flow.ready()
    submission = flow.prepare('ic', 'submit_deliverable', flow.submit_params([flow.upload()]), oid=flow.work_id)
    source, _ = flow.revise_capacity_body(2)
    first, second = (source, submission) if source_first else (submission, source)
    result = ordered(flow.h, flow.f, first_url=flow.url, second_url=peer_url,
        first_actor='b' if source_first else 'ic', second_actor='ic' if source_first else 'b',
        first_body=first, second_body=second, first_db_name=primary_name, second_db_name=peer_name,
        first_status=200, second_status=(403, 409) if source_first else 200,
        checkpoint='before_business_commit')
    submissions = [r for r in oracle.rows(flow, 'gov_action_receipts') if r['action_type'] == 'submit_deliverable']
    assert len(submissions) == (0 if source_first else 1)
    if source_first:
        assert result['second']['body']['error']['code'] in {'COMPOSITION_INPUT_CHANGED', 'STALE_DEPENDENCY', 'VERSION_CONFLICT'}
    else:
        assert submissions[0]['receipt_id'] == result['first']['body']['receipt_id']
    return {**result, 'source_first': source_first, 'successful_submissions': len(submissions)}


def restart(flow, book, *, source, api_process):
    flow.ready()
    submitted = flow.submit()
    accepted = flow.review(submitted, accept=True)
    before = flow.h.snapshot(flow.f)
    old_pids = [p.pid for p, _, _ in flow.h.processes]
    workers = []
    for cycle in ['before', 'after']:
        if cycle == 'after':
            flow.close()
            flow.h.stop(api_process)
            process, url, ready = flow.h.start_api(source)
            assert process.pid not in old_pids
            flow.url = url
            flow.clients = flow.h.clients(url, flow.f)
        path = flow.h.private / ('worker-' + cycle + '-' + uid() + '.json')
        proc = flow.h.spawn('a3-worker-' + cycle, [sys.executable, '-I', str(HERE / 'worker_process.py'),
            'worker-once', '--source', str(source), '--result', str(path)], updates={
            'MEMORY_TENANT': flow.f['tenant_id'], 'MEMORY_ORG': flow.f['company_id'],
            'GOVERNED_EFFECT_URL': 'http://127.0.0.1:1/effects'})
        proc.wait(timeout=20)
        assert proc.returncode == 0
        result = json.loads(path.read_text())
        assert result['completed'] is True and result['did_work'] is False
        workers.append({'pid': proc.pid, 'result': result})
    assert flow.h.snapshot(flow.f) == before
    book.check('A3-14', 'restart_preserves_binding_epoch_author_hash_and_receipt', True,
               evidence={'all_governed_tables_unchanged': True, 'receipt': oracle.receipt(flow, accepted)})
    for entry in flow.commands:
        if entry['request']['action_type'] in {'accept_commitment', 'activate_commitment', 'accept_work_item', 'submit_deliverable', 'review_deliverable'}:
            assert flow.clients[entry['actor']].json('POST', '/v1/actions', entry['request']) == entry['receipt']
    assert flow.h.snapshot(flow.f) == before
    book.check('A3-14', 'restart_replay_produces_no_new_effect', True, evidence=oracle.no_effects(flow))
    book.check('A3-14', 'api_and_worker_restart_scoped_to_acceptance', True,
               evidence={'stopped_api_pid': api_process.pid, 'replacement_api_pid': process.pid, 'worker_processes': workers,
                         'database_scoped': True, 'containers_restarted': []})
    return {'restart_checked': True}
