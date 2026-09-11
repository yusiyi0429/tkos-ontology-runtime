"""Additional review gates: judgment inputs and honest read projections."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .cases_versions import rejected
from .concurrency import reached
from acceptance.protocol_a1_independent.support import wait


def review_boundaries(flow, book):
    candidate = flow.build(sign=False)
    # The CEO can read both of these inputs locally; publication/freshness must
    # still be checked before their refs enter a manifest shared with the DRIs.
    proofs = []
    for kind, expected_code, status in [('private', 'FORBIDDEN', 403),
                                        ('expired', 'COMPOSITION_INPUT_CHANGED', 409)]:
        payload = deepcopy(flow.capacity_payload)
        payload.update(resource_id=str(uuid4()), title='Synthetic judgment ' + kind)
        if kind == 'private':
            payload['shared_with_domain_ids'] = []
        else:
            payload['observed_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        created = flow.act('b', 'create_object', {'object_type': 'CapacityObservation',
            'domain_id': flow.f['domains']['b'], 'payload': payload})
        ref = flow.ref(created['result']['object_id'])
        body = flow.prepare('ceo', 'form_company_composition',
            flow.form_params(extra_evidence=[ref]), oid=flow.round_id)
        proofs.append({'kind': kind, **rejected(flow, 'ceo', body, expected_code, status)})
    # A shared tail is reached through both a short and a long branch. The
    # cache must not mask the nine-level path when this source is consumed.
    graph = {1:[5,2],2:[3],3:[4],4:[5],5:[6],6:[7],7:[8],8:[9],9:[]}
    refs = {}
    for node in (9,8,7,6,5,4,3,2,1):
        refs[node] = flow.create_reference(title='Synthetic shared-tail node '+str(node),
            upstream_refs=[{key:refs[child][key] for key in ('object_id','revision_id')}
                           for child in graph[node]])
    body = flow.command('form_company_composition', flow.form_params(extra_evidence=[refs[1]]), oid=flow.round_id)
    body['expected_versions'] = flow.current_known_versions(flow.round_id)
    before = flow.h.snapshot(flow.f)
    for path in ('/v1/actions/prepare', '/v1/actions'):
        response = flow.clients['ceo'].json('POST', path, body, expected=409)
        assert response['error']['code'] == 'COMPOSITION_NOT_READY'
        assert flow.h.snapshot(flow.f) == before
    proofs.append({'kind':'nine_level_shared_tail','prepare_and_execute':'COMPOSITION_NOT_READY',
                   'business_state_unchanged':True})
    conflicts = [{'summary': 'Synthetic unresolved release window', 'blocking': True}]
    blocked = flow.form(conflicts=conflicts)
    projected = flow.object(blocked['object_id'], 'a')['a2_projection']['composition']
    assert projected['unresolved_conflicts'] == conflicts
    readiness = projected['readiness']
    assert readiness['basis'] == 'at_form' and readiness['requires_live_admission'] is True
    assert readiness['blocking_unresolved'] is True
    body = flow.prepare('ceo', 'confirm_company_composition',
        flow.confirm_params(blocked, 'ceo'), oid=blocked['object_id'])
    rejected(flow, 'ceo', body, 'COMPOSITION_NOT_READY')
    # An unaffected good candidate is still usable. Creating private/unrelated
    # observations is not a blanket auth_epoch or generation invalidation.
    flow.sign_all(candidate)
    activated = flow.activate(candidate)
    ids = [flow.company_ref['object_id'], flow.capacity_ref['object_id'], flow.round_id,
           candidate['object_id'], flow.submissions['a']['object_id'],
           flow.missions['a'][0]['mission_object_id'],
           activated['result']['domain_commitments'][0]['object_id']]
    statuses = []
    for oid in ids:
        obj = flow.object(oid)
        assert obj['protocol']['interpretation_status'] == 'contract_a_v0_1'
        statuses.append(obj['object_type'])
    assert set(statuses) == {'CompanyReference', 'CapacityObservation', 'FormationRound',
                            'CompanyComposition', 'DomainSubmission', 'Mission', 'DomainCommitment'}
    evidence = {'judgment_inputs_rechecked_before_publication': proofs,
                'manual_conflicts_preserved_for_participant_review': True,
                'readiness_explicitly_at_form': True, 'all_seven_a2_read_types': statuses}
    book.gates['review_boundaries'] = {'passed': True, 'evidence': evidence}
    return evidence


def source_expiry(flow, book):
    """Source validity can expire while the scope fence still blocks writers."""
    flow.company_ref = flow.create_reference()
    flow.create_capacity()
    payload = deepcopy(flow.capacity_payload)
    payload['valid_to'] = (datetime.now(timezone.utc) + timedelta(seconds=25)).isoformat()
    flow.act('b', 'propose_revision', {'payload': payload}, oid=flow.capacity_ref['object_id'])
    flow.capacity_payload, flow.capacity_ref = payload, flow.ref(flow.capacity_ref['object_id'])
    flow.open()
    flow.publish('a'); flow.publish('b')
    candidate = flow.form(); flow.sign_all(candidate)
    body = flow.activation_body(candidate)
    h, f = flow.h, flow.f
    before = h.snapshot(f)
    token = 'a2-source-expiry-' + uuid4().hex
    epoch = h.sql(f, 'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s', (f['scope_id'],))[0]['auth_epoch']
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(flow.clients['ceo'].request, 'POST', '/v1/actions', body, expected=409,
                             headers=h.headers('before_business_commit', mode='barrier', token=token))
        try:
            barrier = reached(h, token)

            def expired():
                row = h.sql(f, '''SELECT valid_to,clock_timestamp() AS observed_at,
                    clock_timestamp()>=valid_to AS expired FROM gov_object_revisions
                    WHERE scope_id=%s AND object_id=%s AND revision_id=%s''',
                    (f['scope_id'], flow.capacity_ref['object_id'], flow.capacity_ref['revision_id']))[0]
                return row if row['expired'] else None

            observed = wait(expired, timeout=30)
            (h.control / (token + '.release')).touch(mode=0o600)
            response = future.result(timeout=10)
            assert response.json()['error']['code'] == 'COMPOSITION_INPUT_CHANGED'
            assert h.snapshot(f) == before
            assert h.sql(f, 'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s',
                         (f['scope_id'],))[0]['auth_epoch'] == epoch
        finally:
            (h.control / (token + '.release')).touch(mode=0o600, exist_ok=True)
            try: future.result(timeout=35)
            except Exception: pass
    evidence = {'barrier': barrier, 'database_clock': observed, 'auth_epoch_unchanged': True,
                'all_business_receipt_outbox_rolled_back': True, 'code': 'COMPOSITION_INPUT_CHANGED'}
    book.gates['source_expiry'] = {'passed': True, 'evidence': evidence}
    return evidence
