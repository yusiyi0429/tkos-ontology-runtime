"""Independent HTTP, SQL and blob oracles for Method races and recovery.

Owns METHOD-15 and METHOD-16. It neither imports production validators nor
writes business rows directly. Failure hooks can only pause or raise inside the
real transaction; success always comes from a normally authorized API command.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
from uuid import uuid4

from acceptance.protocol_a1_independent.support import digest, public_json, wait
from .flow import exact


def _check(book, case, name, evidence):
    book.check(case, name, True, evidence=evidence)


def _error(response):
    return response.get('error', {}).get('code')


def _matching_receipts(flow, keys):
    # Hash/scope observer never exposes credentials.
    return flow.h.sql(flow.f,
        'SELECT receipt_id,action_type,status,idempotency_key FROM gov_action_receipts '
        'WHERE scope_id=%s AND idempotency_key=ANY(%s) ORDER BY receipt_id',
        (flow.f['scope_id'], list(keys)))


def _wait_lock(flow):
    def blocked():
        rows = flow.h.sql(flow.f,
            "SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() "
            "AND usename=current_user AND wait_event_type='Lock' AND query LIKE %s",
            ('%gov_scopes%',))
        return rows and int(rows[0]['n']) > 0
    return wait(blocked, timeout=12)


def _race(flow, first_actor, first, second_actor, second):
    """Prove the loser actually waits on the scope fence before releasing it."""
    token = 'method-race-' + uuid4().hex
    headers = flow.h.headers('auth_fence_acquired', mode='barrier', token=token)
    reached = flow.h.control / (token + '.reached.json')
    release = flow.h.control / (token + '.release')
    submitted = threading.Event()

    def competitor():
        submitted.set()
        return flow.clients[second_actor].json('POST', '/v1/actions', second, expected={200, 409})

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(flow.commit, first_actor, first, headers=headers)
        try:
            wait(lambda: reached.exists(), timeout=12)
            second_future = pool.submit(competitor)
            assert submitted.wait(5)
            assert _wait_lock(flow), 'Competing HTTP write did not reach the database lock'
            assert not second_future.done(), 'Competing write escaped the held governance fence'
        finally:
            release.write_text('release\n')
            release.chmod(0o600)
        first_receipt = first_future.result(timeout=20)
        second_result = second_future.result(timeout=20)
    proof = json.loads(reached.read_text())
    assert proof['checkpoint'] == 'auth_fence_acquired'
    return first_receipt, second_result, {
        'checkpoint': proof['checkpoint'], 'first_receipt_id': first_receipt['receipt_id'],
        'second_code': _error(second_result), 'database_lock_observed': True,
    }


def _fail_after_write(flow, actor, command, checkpoint):
    before = flow.h.snapshot(flow.f)
    stored_before = flow.h.storage_snapshot(flow.f['scope_id'])
    token = 'method-rollback-' + uuid4().hex
    response = flow.clients[actor].request('POST', '/v1/actions', command, expected=500,
        headers=flow.h.headers(checkpoint, mode='fail', token=token))
    assert response.status_code == 500
    reached = flow.h.control / (token + '.reached.json')
    assert reached.exists(), 'The injected failure did not reach the first business write'
    proof = json.loads(reached.read_text())
    assert proof['checkpoint'] == checkpoint and proof['mode'] == 'fail'
    after = flow.h.snapshot(flow.f)
    assert before == after, 'Partial business content, state, receipt, audit or outbox survived rollback'
    assert flow.h.storage_snapshot(flow.f['scope_id']) == stored_before
    assert _matching_receipts(flow, {command['idempotency_key']}) == []
    return {'checkpoint': checkpoint, 'snapshot_sha256': digest(after),
            'all_business_tables_unchanged': True, 'storage_unchanged': True,
            'successful_receipt_absent': True}


def concurrency(flow, book, strategy, ltco):
    targets = flow.draft_targets(strategy, ltco)
    flow.open_window(targets)
    oid = targets['window']['object_id']
    params = {'target_ref': targets['pco'], 'content': 'Personally authored recovery test opinion'}
    first = flow.prepare('a', 'm1b_comment', params, oid=oid)
    stale = flow.prepare('b', 'm1b_comment', {**params, 'content': 'Prepared on the old window CAS'}, oid=oid)
    receipt = flow.commit('a', first)
    before_replay = flow.h.snapshot(flow.f)
    replay = flow.clients['a'].json('POST', '/v1/actions', first)
    assert replay == receipt and flow.h.snapshot(flow.f) == before_replay
    _check(book, 'METHOD-15', 'same_request_replays_original_receipt', {
        'receipt_id': receipt['receipt_id'], 'snapshot_sha256': digest(before_replay)})
    changed = deepcopy(first)
    changed['params']['content'] = 'Different body under an already committed key'
    changed_proof = flow.deny('a', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    _check(book, 'METHOD-15', 'same_key_changed_body_conflicts', changed_proof)
    stale_proof = flow.deny('b', stale, codes={'VERSION_CONFLICT'}, prepare=False)
    # The prepared logical opinion can be submitted only after re-reading the
    # advanced window and preparing a fresh attempt.
    fresh = flow.prepare('b', 'm1b_comment', stale['params'], oid=oid)
    assert fresh['target']['expected_version'] > stale['target']['expected_version']
    flow.commit('b', fresh)
    _check(book, 'METHOD-15', 'stale_version_requires_reread', stale_proof)

    close = flow.prepare('co_agent', 'm1b_close_window', {'reason': 'Close during a competing comment'}, oid=oid)
    late = flow.prepare('a', 'm1b_comment', {**params, 'content': 'Competing with window close'}, oid=oid)
    closure, rejected, race_proof = _race(flow, 'co_agent', close, 'a', late)
    assert _error(rejected) in {'INVALID_STATE', 'VERSION_CONFLICT'}
    assert len(_matching_receipts(flow, {close['idempotency_key'], late['idempotency_key']})) == 1
    frozen = closure['result']['frozen_opinion_ids']
    assert len(frozen) == 2 and receipt['result']['review_record_id'] in frozen
    records = [r for r in flow.rows('gov_method_reviews') if str(r['window_id']) == oid and r['kind'] == 'window_comment']
    assert len(records) == 2
    _check(book, 'METHOD-15', 'close_and_comment_race_has_serialized_result', race_proof)
    targets['frozen_opinion_ids'] = frozen
    flow.resolve(targets)
    candidate_oid = targets['candidate']['object_id']
    confirm = flow.prepare('ceo', 'm1b_confirm_candidates', {'reason': 'First concurrent confirmation'}, oid=candidate_oid)
    competing = flow.prepare('ceo', 'm1b_confirm_candidates', {'reason': 'Second concurrent confirmation'}, oid=candidate_oid)
    before_count = len([r for r in flow.rows('gov_method_reviews') if r['kind'] == 'candidate_set_confirmation'])
    confirmation, rejected, proof = _race(flow, 'ceo', confirm, 'ceo', competing)
    assert _error(rejected) in {'INVALID_STATE', 'VERSION_CONFLICT'}
    decisions = [r for r in flow.rows('gov_method_reviews') if r['kind'] == 'candidate_set_confirmation']
    assert len(decisions) == before_count + 1
    assert len(_matching_receipts(flow, {confirm['idempotency_key'], competing['idempotency_key']})) == 1
    assert all(flow.object(ref['object_id'])['effective_revision_id'] == ref['revision_id']
               for ref in [targets['pco'], targets['mission']])
    _check(book, 'METHOD-15', 'concurrent_candidate_confirmation_has_single_winner', proof)
    targets['confirmation'] = confirmation
    return targets


def candidate_rollback(flow, book, strategy, ltco):
    targets = flow.draft_targets(strategy, ltco)
    flow.open_window(targets)
    flow.comment(targets)
    flow.close_window(targets)
    command = flow.prepare('co_agent', 'm1b_resolve_window', flow.resolution_params(targets), oid=targets['window']['object_id'])
    proof = _fail_after_write(flow, 'co_agent', command, 'after_method_candidate_write')
    assert flow.ref(targets['pco']['object_id']) == targets['pco']
    assert flow.ref(targets['mission']['object_id']) == targets['mission']
    assert flow.object(targets['window']['object_id'])['method_state']['phase'] == 'closed'
    result = flow.commit('co_agent', command)['result']
    targets['candidate'] = exact(result)
    updated = {ref['object_id']: ref for ref in result['target_refs']}
    targets['pco'] = updated[targets['pco']['object_id']]
    targets['mission'] = updated[targets['mission']['object_id']]
    assert targets['pco'] == flow.ref(targets['pco']['object_id'])
    assert targets['mission'] == flow.ref(targets['mission']['object_id'])
    proof['retry_created_complete_candidate_set'] = targets['candidate']
    _check(book, 'METHOD-16', 'candidate_first_write_failure_rolls_back_entire_set', proof)
    return targets


def cross_cycle_run_history(flow, targets, old_run_ref):
    """A historical source never assigns a new analysis to its old paused run."""
    fact = flow.fact(targets, value=7)
    old_run_id = old_run_ref['object_id']
    flow.act('ceo', 'method_pause_run', {'note': 'Pause the completed prior cycle while retaining historical meaning'}, oid=old_run_id)
    source_recovery = flow.clients['ceo'].json('GET', f"/v1/method/objects/{targets['pco']['object_id']}/recovery")
    assert len(source_recovery['runs']) == 1 and source_recovery['runs'][0]['run_id'] == old_run_id
    assert source_recovery['runs'][0]['phase'] == 'paused'

    independent = flow.review(targets, [fact])
    independent_receipt = flow.receipts[-1]
    assert independent_receipt['result']['run_ids'] == []
    independent_recovery = flow.clients['ceo'].json('GET', f"/v1/method/objects/{independent['object_id']}/recovery")
    assert independent_recovery['runs'] == []
    content = flow.clients['ceo'].revision(independent['object_id'], independent['revision_id'])['payload']
    assert content['target_refs'] == [targets['pco'], targets['mission']] and content['fact_refs'] == [fact]

    opened = flow.act('ceo', 'method_open_run', {'domain_id': flow.f['domains']['company'],
        'payload': {'title': 'Explicit next cycle analysis run', 'method': 'M1B'}})
    new_run_ref = exact(opened['result'])
    payload = deepcopy(content)
    payload['review_id'] = 'explicit-next-cycle-' + uuid4().hex
    payload['title'] = 'Next cycle review of historical paused-cycle targets'
    command = flow.command('m1b_generate_review', {'domain_id': flow.f['domains']['company'], 'payload': payload}, actor='co_agent')
    command['run_ref'] = new_run_ref
    command['step_key'] = 'next_cycle_period_review'
    prepared = flow.clients['co_agent'].json('POST', '/v1/actions/prepare', command)
    command['expected_versions'] = prepared['expected_versions']
    assert prepared['target'] is None
    associated_receipt = flow.commit('co_agent', command)
    associated = exact(associated_receipt['result'])
    assert associated_receipt['result']['run_ids'] == [new_run_ref['object_id']]
    associated_recovery = flow.clients['ceo'].json('GET', f"/v1/method/objects/{associated['object_id']}/recovery")
    assert [row['run_id'] for row in associated_recovery['runs']] == [new_run_ref['object_id']]
    assert any(attempt['step_key'] == 'next_cycle_period_review'
               and str(attempt['action_id']) == associated_receipt['receipt_id']
               for attempt in associated_recovery['runs'][0]['attempts'])
    assert flow.clients['ceo'].json('GET', f'/v1/method/objects/{old_run_id}/recovery')['runs'][0]['phase'] == 'paused'

    flow.act('ceo', 'method_pause_run', {'note': 'Pause the explicitly selected next cycle run'}, oid=new_run_ref['object_id'])
    next_payload = {**payload, 'generation_version': 'controlled-agent-v2'}
    rejected = flow.command('m1b_regenerate_review', {'payload': next_payload}, oid=associated['object_id'], actor='co_agent')
    denied = flow.deny('co_agent', rejected, codes={'INVALID_STATE'})
    assert flow.ref(associated['object_id']) == associated
    flow.act('ceo', 'method_resume_run', {'note': 'Explicitly resume next cycle analysis'}, oid=new_run_ref['object_id'])
    regenerated = flow.act('co_agent', 'm1b_regenerate_review', {'payload': next_payload}, oid=associated['object_id'])
    assert regenerated['result']['run_ids'] == [new_run_ref['object_id']]
    assert regenerated['result']['revision_id'] != associated['revision_id']
    assert flow.clients['ceo'].revision(associated['object_id'], associated['revision_id'])['payload'] == payload
    # Return the surrounding restart fixture to its original running phase.
    flow.act('ceo', 'method_resume_run', {'note': 'Resume prior fixture after cross-cycle independence check'}, oid=old_run_id)
    return {'historical_source_run': old_run_ref, 'independent_review_ref': independent,
            'independent_run_ids': [], 'explicit_new_run_ref': new_run_ref,
            'explicit_review_ref': associated, 'explicit_step_key': 'next_cycle_period_review',
            'new_run_pause_blocks_report_writes': denied,
            'regenerated_review_ref': exact(regenerated['result']),
            'old_paused_run_did_not_block_or_own_new_analysis': True}


def run_and_restart(flow, book, source, api_process, targets):
    opened = flow.act('ceo', 'method_open_run', {'domain_id': flow.f['domains']['company'],
        'payload': {'title': 'Independent resume and process restart', 'method': 'M1A+M1B'}})
    run_ref = exact(opened['result'])
    run_oid = run_ref['object_id']
    for reference in [targets['window'], targets['pco'], targets['mission'], targets['candidate']]:
        flow.act('ceo', 'method_attach_run', {'object_ref': reference}, oid=run_oid)
    flow.act('co_agent', 'method_record_attempt', {'step_key': 'candidate_confirmation', 'outcome': 'started',
        'note': 'Application begins explicit confirmation preparation'}, oid=run_oid)
    flow.act('ceo', 'method_pause_run', {'note': 'Pause before a human decision'}, oid=run_oid)
    paused = flow.command('m1b_confirm_candidates', {'reason': 'A paused run must not write'},
        oid=targets['candidate']['object_id'])
    denied = flow.deny('ceo', paused, codes={'INVALID_STATE'})
    flow.act('co_agent', 'method_record_attempt', {'step_key': 'candidate_confirmation', 'outcome': 'failed',
        'note': 'Paused run correctly denied the attempted next business step'}, oid=run_oid)
    paused_query = flow.clients['ceo'].json('GET', f'/v1/method/objects/{run_oid}/recovery')
    assert len(paused_query['runs']) == 1 and paused_query['runs'][0]['phase'] == 'paused'
    flow.act('ceo', 'method_resume_run', {'note': 'Resume after explicit decision to proceed'}, oid=run_oid)
    confirm_command = flow.prepare('ceo', 'm1b_confirm_candidates', {'reason': 'Confirm after explicit run resume'},
        oid=targets['candidate']['object_id'])
    confirm_receipt = flow.commit('ceo', confirm_command)
    cross_cycle = cross_cycle_run_history(flow, targets, run_ref)
    recovery_path = f'/v1/method/objects/{run_oid}/recovery'
    recovery = flow.clients['ceo'].json('GET', recovery_path)
    assert recovery['runs'][0]['phase'] == 'running'
    attempts = recovery['runs'][0]['attempts']
    assert {'started', 'failed', 'succeeded'} <= {a['outcome'] for a in attempts}
    assert any(a['step_key'] == 'candidate_confirmation' and a['outcome'] == 'failed' for a in attempts)
    assert any(a['step_key'] == 'm1b_confirm_candidates' and str(a['action_id']) == confirm_receipt['receipt_id'] for a in attempts)
    _check(book, 'METHOD-16', 'run_steps_attempts_and_pause_resume_are_queryable', {
        'run_ref': run_ref, 'query': recovery_path, 'attempts': len(attempts), 'paused_write_rejections': denied,
        'cross_cycle_history': cross_cycle})

    tracked = [targets['pco'], targets['mission'], targets['candidate'], run_ref]
    object_before = {ref['object_id']: flow.object(ref['object_id']) for ref in tracked}
    receipt_path = '/v1/action-receipts/' + confirm_receipt['receipt_id']
    historic_receipt = flow.clients['ceo'].json('GET', receipt_path)
    evidence = flow.evidence[0]
    evidence_path = f"/v1/evidence-assets/{evidence['ref']['object_id']}/revisions/{evidence['ref']['revision_id']}"
    response = flow.clients[evidence['actor']].request('GET', evidence_path)
    raw_hash = hashlib.sha256(response.content).hexdigest()
    assert raw_hash == evidence['sha256']
    before = flow.h.snapshot(flow.f)
    storage_before = flow.h.storage_snapshot(flow.f['scope_id'])
    old_pid = api_process.pid
    flow.close()
    flow.h.stop(api_process)
    assert api_process.poll() is not None
    process, url, ready = flow.h.start_api(source)
    assert process.pid != old_pid
    flow.url = url
    flow.clients = flow.h.clients(url, flow.f)
    assert {ref['object_id']: flow.object(ref['object_id']) for ref in tracked} == object_before
    assert flow.clients['ceo'].json('GET', receipt_path) == historic_receipt
    assert flow.clients['ceo'].json('GET', recovery_path) == recovery
    after_bytes = flow.clients[evidence['actor']].request('GET', evidence_path).content
    assert hashlib.sha256(after_bytes).hexdigest() == raw_hash
    assert flow.h.snapshot(flow.f) == before
    assert flow.h.storage_snapshot(flow.f['scope_id']) == storage_before
    _check(book, 'METHOD-16', 'restart_preserves_objects_receipts_sources_and_runs', {
        'old_pid': old_pid, 'new_pid': process.pid, 'object_ids': sorted(object_before),
        'receipt_id': confirm_receipt['receipt_id'], 'source_sha256': raw_hash,
        'snapshot_sha256': digest(before), 'run_ref': run_ref})
    replay = flow.clients['ceo'].json('POST', '/v1/actions', confirm_command)
    assert replay == confirm_receipt
    assert flow.h.snapshot(flow.f) == before
    assert flow.h.storage_snapshot(flow.f['scope_id']) == storage_before
    assert len(_matching_receipts(flow, {confirm_command['idempotency_key']})) == 1
    _check(book, 'METHOD-16', 'restart_replay_has_no_duplicate_effect', {
        'receipt_id': replay['receipt_id'], 'snapshot_sha256': digest(before), 'effect_task_ids': replay['effect_task_ids']})
    public_json(flow.h.output / 'api-restarted-identity.json', json.loads(ready.read_text()))
    return process, targets


def strategy_rollback(flow, book, strategy):
    pending = flow.strategy(strategy_ref=strategy, confirm=False)
    before_strategy = flow.object(strategy['object_id'])
    before_authorities = len(flow.rows('gov_execution_authorities'))
    storage_before_upload = flow.h.storage_snapshot(flow.f['scope_id'])
    unused = flow.upload(text='Synthetic independent raw material, deliberately not adopted by this strategy decision')
    assert flow.h.storage_snapshot(flow.f['scope_id'])['count'] > storage_before_upload['count']
    assert flow.object(strategy['object_id']) == before_strategy
    assert len(flow.rows('gov_execution_authorities')) == before_authorities
    heads = {str(row['object_id']): row['object_type'] for row in flow.rows('gov_objects')}
    uses = [row for row in flow.rows('gov_object_revisions')
            if heads[str(row['object_id'])] != 'EvidenceAsset' and unused['object_id'] in json.dumps(row['payload'], default=str)]
    assert uses == []
    proof = _fail_after_write(flow, 'ceo', pending['confirm_command'], 'after_method_strategy_write')
    assert flow.object(strategy['object_id']) == before_strategy
    assert flow.ref(strategy['object_id'], effective=True) == strategy
    assert len(flow.rows('gov_execution_authorities')) == before_authorities
    _check(book, 'METHOD-16', 'stored_unlinked_evidence_is_not_business_success', {
        'evidence_ref': unused, 'adopted_by_business_revisions': 0, 'failed_receipt_absent': True,
        'strategy_unchanged': True, 'execution_authority_count': before_authorities})
    committed = flow.commit('ceo', pending['confirm_command'])
    new_strategy = committed['result']['changed_refs'][0]
    assert new_strategy['revision_id'] != strategy['revision_id']
    assert flow.ref(strategy['object_id'], effective=True) == new_strategy
    proof['retry_receipt_id'] = committed['receipt_id']
    proof['strategy_ref'] = new_strategy
    _check(book, 'METHOD-16', 'strategy_first_write_failure_rolls_back_map_and_receipt', proof)
    return new_strategy


def run(flow, book, *, source: Path, api_process, chain=None, result=None):
    """Update flow's HTTP clients in place after restart; return current process."""
    if chain is None:
        chain = flow.strategy()
    strategy = flow.ref(chain['strategy']['object_id'], effective=True)
    ltco = flow.ltco(strategy)['ltco']
    concurrency_targets = concurrency(flow, book, strategy, ltco)
    recovered_targets = candidate_rollback(flow, book, strategy, ltco)
    api_process, recovered_targets = run_and_restart(flow, book, source, api_process, recovered_targets)
    strategy = strategy_rollback(flow, book, strategy)
    return {'api_process': api_process, 'url': flow.url, 'strategy': strategy,
            'concurrency_targets': concurrency_targets, 'recovered_targets': recovered_targets}
