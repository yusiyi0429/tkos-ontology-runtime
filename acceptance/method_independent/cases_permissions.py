"""Independent human/Agent, window source-privacy and relationship negatives."""
from copy import deepcopy
from datetime import datetime, timezone

from acceptance.runtime.client import Client

from .cases_chain import check, payload


def run(flow, book, chain, business):
    strategy, ltco = chain['strategy'], business['ltco']['ltco']
    # Separate draft objects avoid changing already confirmed business history.
    targets = flow.draft_targets(strategy, ltco)
    invalid = deepcopy(targets['mission_payload'])
    invalid['supports'][0]['outcome_ref']['revision_id'] = business['prior']['pco']['revision_id']
    proof = flow.deny('co_agent', flow.command('m1b_draft_mission', {'domain_id': flow.f['domains']['company'], 'payload': invalid}), codes={'INVALID_REQUEST'})
    check(book, 'METHOD-11', 'wrong_pco_version_rejected', proof)
    invalid = deepcopy(targets['mission_payload'])
    invalid['supports'][0]['outcome_ref']['outcome_id'] = 'outcome-does-not-exist'
    proof = flow.deny('co_agent', flow.command('m1b_draft_mission', {'domain_id': flow.f['domains']['company'], 'payload': invalid}), codes={'INVALID_REQUEST'})
    check(book, 'METHOD-11', 'missing_outcome_rejected', proof)
    invalid = deepcopy(targets['mission_payload'])
    invalid['participants'].append(invalid['owner_principal_id'])
    proof = flow.deny('co_agent', flow.command('m1b_draft_mission', {'domain_id': flow.f['domains']['company'], 'payload': invalid}), codes={'INVALID_REQUEST'})
    check(book, 'METHOD-11', 'owner_not_duplicated_as_participant', proof)
    flow.open_window(targets)
    genuine = flow.prepare('a', 'm1b_comment', {'target_ref': targets['pco'], 'content': 'Personally reviewed the proposed target.'}, oid=targets['window']['object_id'])
    impersonation = flow.deny('dri_agent', genuine, codes={'FORBIDDEN'})
    wrong = flow.deny('wrong_a', genuine, codes={'NOT_FOUND', 'FORBIDDEN'})
    check(book, 'METHOD-14', 'wrong_responsible_human_rejected', wrong)
    # Agent cannot acquire the human's authority by posting their exact command.
    confirmation = flow.command('m1b_confirm_ltco', {'reason': 'Attempted impersonated CEO decision.'}, oid=ltco['object_id'])
    impersonation += flow.deny('ceo_agent', confirmation, codes={'FORBIDDEN'})
    check(book, 'METHOD-14', 'agent_cannot_impersonate_human_comment_or_decision', impersonation)
    receipt = flow.commit('a', genuine)
    assert flow.clients['b'].revision(targets['pco']['object_id'], targets['pco']['revision_id'])['payload_hash'] == targets['pco']['payload_hash']
    assert flow.clients['b'].revision(targets['mission']['object_id'], targets['mission']['revision_id'])['payload_hash'] == targets['mission']['payload_hash']
    opinions = flow.clients['b'].json('GET', '/v1/method/objects/' + targets['window']['object_id'] + '/reviews?effective_only=true')
    assert [r['record_id'] for r in opinions['items']] == [receipt['result']['review_record_id']]
    check(book, 'METHOD-14', 'window_read_grant_limited_to_targets_and_comments', {'window_ref': targets['window'], 'comment': receipt['result']['review_record_id']})
    private_source = flow.upload(text='Company-only evidence not shared with a research issue or window')
    for ref in [private_source, business['fact1'], business['review1']]:
        response = flow.clients['b'].json('GET', '/v1/objects/' + ref['object_id'] + '/revisions/' + ref['revision_id'], expected={403, 404})
        assert response['error']['code'] in {'FORBIDDEN', 'NOT_FOUND'}
    # Explicit projection must not recursively grant access to supporting source material.
    now = datetime.now(timezone.utc).isoformat()
    context = flow.clients['b'].json('POST', '/v1/context-packs', {'object_ids': [targets['pco']['object_id'], private_source['object_id']],
        'valid_at': now, 'known_at': now, 'contract_version': 'tkos.method/0.1', 'stage': 'M1B.review', 'purpose': 'review_window', 'include_drafts': True})
    assert all(r['object_id'] != private_source['object_id'] for r in context['selected'])
    assert any(r['reason'] == 'not_found_or_not_authorized' for r in context['excluded'])
    check(book, 'METHOD-14', 'cross_domain_source_material_remains_private', {'private_source': private_source, 'excluded': context['excluded']})
    for ref in [targets['window'], targets['pco'], targets['mission'], chain['issue']]:
        response = flow.clients['outsider'].json('GET', '/v1/objects/' + ref['object_id'], expected={403, 404})
        assert response['error']['code'] in {'FORBIDDEN', 'NOT_FOUND'}
    check(book, 'METHOD-14', 'unrelated_domain_read_denied', [targets['window'], chain['issue']])
    flow.close_window(targets)
    late = flow.command('m1b_comment', {'target_ref': targets['pco'], 'content': 'Too late'}, oid=targets['window']['object_id'])
    proof = flow.deny('a', late, codes={'INVALID_STATE'})
    check(book, 'METHOD-10', 'closed_window_rejects_comments', proof)
    flow.resolve(targets)
    repeat = flow.command('m1b_resolve_window', flow.resolution_params(targets), oid=targets['window']['object_id'])
    proof = flow.deny('co_agent', repeat, codes={'INVALID_STATE'})
    resolutions = [r for r in flow.rows('gov_method_reviews') if r['kind'] == 'window_resolution' and str(r['window_id']) == targets['window']['object_id']]
    assert len(resolutions) == 1
    check(book, 'METHOD-10', 'one_formal_resolution_per_window', proof)
    mission_ref = business['targets']['mission']
    handoff = flow.clients['ceo'].json('GET', '/v1/method/missions/' + mission_ref['object_id'] + '/handoff')
    assert handoff['mission_ref'] == mission_ref and handoff['confirmation_record_id']
    check(book, 'METHOD-17', 'new_mission_has_explicit_downstream_handoff_projection', handoff)
    assert handoff['execution_authority'] is None and handoff['delivery_accepted'] is None and handoff['outcome_achieved'] is None and handoff['mf_closed'] is None
    assert flow.rows('gov_execution_authorities') == [] and flow.rows('gov_work_receipts') == []
    check(book, 'METHOD-17', 'no_implicit_m2_execution_authority', handoff)
    revocation_targets = flow.draft_targets(strategy, ltco)
    flow.open_window(revocation_targets)
    return {'targets': targets, 'private_source': private_source, 'context': context, 'revocation_targets': revocation_targets}


def revoke(flow, book, chain, business, *, targets=None):
    if targets is None:
        targets = flow.draft_targets(chain['strategy'], business['ltco']['ltco'])
        flow.open_window(targets)
    posted = flow.prepare('b', 'm1b_comment', {'target_ref': targets['pco'], 'content': 'A real comment before revocation.'}, oid=targets['window']['object_id'])
    receipt = flow.commit('b', posted)
    pending = flow.prepare('b', 'm1b_comment', {'target_ref': targets['pco'], 'content': 'Prepared but not committed before revocation.'}, oid=targets['window']['object_id'])
    revoke_body = Client.command('revoke_assignment', {'assignment_id': flow.f['actors']['b']['assignment_id']})
    revoked = flow.clients['ceo'].json('POST', '/v1/actions', revoke_body)
    proof = flow.deny('b', pending, codes={'FORBIDDEN', 'NOT_FOUND'}, prepare=False)
    for actor in ['b', 'b_agent']:
        for reference in [targets['window'], targets['pco']]:
            result = flow.clients[actor].json('GET', '/v1/objects/' + reference['object_id'], expected={403, 404})
            assert result['error']['code'] in {'FORBIDDEN', 'NOT_FOUND'}
    check(book, 'METHOD-14', 'revocation_removes_window_grant_and_blocks_prepared_write', {'revocation_receipt': revoked['receipt_id'], 'denials': proof})
    proof = flow.deny('b', posted, codes={'FORBIDDEN', 'NOT_FOUND'}, prepare=False)
    assert any(str(r['record_id']) == receipt['result']['review_record_id'] for r in flow.rows('gov_method_reviews'))
    check(book, 'METHOD-14', 'revoked_actor_cannot_replay_to_restore_authority', proof)
    return {'revocation_receipt': revoked['receipt_id'], 'historic_comment_retained': receipt['result']['review_record_id']}
