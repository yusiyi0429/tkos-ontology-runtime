"""Independent cross-method version and protocol/compatibility acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from acceptance.runtime.client import Client
from acceptance.protocol_a1_independent.support import source_manifest
from .flow import exact
from .cases_chain import check, payload

ROOT = Path(__file__).resolve().parents[2]


def run(flow, book, chain, business):
    """Run after cases requiring the original Strategy, before revocations."""
    old = chain['strategy']
    old_strategy = payload(flow, old)
    old_refs = [business['ltco']['ltco'], business['targets']['pco'], business['targets']['mission']]
    before = {ref['object_id']: flow.object(ref['object_id']) for ref in old_refs}
    pending = flow.targets(old, business['ltco']['ltco'], confirm=False)
    change = flow.strategy(strategy_ref=old, confirm=False)
    draft = payload(flow, change['proposal'])
    draft['changes'][0]['payload']['map']['units'][0]['name'] = 'Renamed pilot validation'
    draft['changes'][0]['payload']['map']['units'][0]['owner_principal_id'] = flow.f['actors']['ceo']['principal_id']
    change['proposal'] = flow.act('ceo_agent', 'm1a_propose_update', {'payload': draft}, oid=change['issue']['object_id'])['result']['proposal_ref']
    assert flow.object(old['object_id'])['effective_revision_id'] == old['revision_id']
    assert payload(flow, business['targets']['pco'])['strategy_ref'] == old
    check(book, 'METHOD-12', 'strategy_draft_cannot_replace_formal_basis', change['proposal'])
    flow.act('co_agent', 'm1a_review_update', {'proposal_ref': change['proposal'], 'accepted': True,
        'impact_level': 'company', 'findings': ['Explicitly reviewed renamed unit and owner; existing commitments retain their basis.']}, oid=change['issue']['object_id'])
    confirmed = flow.act('ceo', 'm1a_confirm_update', {'proposal_ref': change['proposal'], 'reason': 'Personally confirm the changed strategic baseline.'}, oid=change['issue']['object_id'])
    new = confirmed['result']['changed_refs'][0]
    assert old['object_id'] == new['object_id'] and old['revision_id'] != new['revision_id']
    notices = flow.rows('gov_method_impacts')
    impacted = {str(row['target_object_id']) for row in notices if str(row['strategy_revision_id']) == new['revision_id']}
    assert {ref['object_id'] for ref in old_refs} <= impacted
    assert all(row['effect'] == 'review_required_effectiveness_preserved' for row in notices)
    check(book, 'METHOD-12', 'strategy_update_appends_impact_notice', {'new_strategy': new, 'impacted_object_ids': sorted(impacted)})
    for reference in old_refs:
        previous, current = before[reference['object_id']], flow.object(reference['object_id'])
        for key in ('object_version', 'latest_revision_id', 'effective_revision_id', 'lifecycle_status'):
            assert current[key] == previous[key]
        assert current['effective_revision']['payload'] == previous['effective_revision']['payload']
        assert current['impact_notices']
    check(book, 'METHOD-12', 'confirmed_old_targets_keep_meaning_and_effectiveness', old_refs)
    rejected = flow.deny('ceo', flow.command('m1b_confirm_candidates', {'reason': 'Attempt to confirm stale source basis.'}, oid=pending['candidate']['object_id']), codes={'STALE_DEPENDENCY'})
    check(book, 'METHOD-12', 'pending_stale_basis_confirmation_rejected', rejected)
    fresh_ltco = flow.ltco(new)
    fresh = flow.targets(new, fresh_ltco['ltco'])
    assert payload(flow, fresh['pco'])['strategy_ref'] == new
    assert flow.object(fresh['mission']['object_id'])['effective_revision_id'] == fresh['mission']['revision_id']
    check(book, 'METHOD-12', 'new_cycle_uses_new_strategy', {'strategy': new, 'pco': fresh['pco'], 'mission': fresh['mission']})
    now = datetime.now(timezone.utc).isoformat()
    context = flow.clients['ceo'].json('POST', '/v1/context-packs', {
        'object_ids': [business['targets']['pco']['object_id']], 'valid_at': now, 'known_at': now,
        'contract_version': 'tkos.method/0.1', 'stage': 'general', 'purpose': 'analysis', 'include_drafts': False})
    selected = next(item for item in context['selected'] if item['object_id'] == old['object_id'] and item['revision_id'] == old['revision_id'])
    assert selected['payload']['map']['units'][0]['name'] == old_strategy['map']['units'][0]['name']
    assert selected['payload']['map']['units'][0]['owner_principal_id'] == old_strategy['map']['units'][0]['owner_principal_id']
    assert selected['payload_hash'] == old['payload_hash']
    assert payload(flow, new)['map']['units'][0]['name'] != selected['payload']['map']['units'][0]['name']
    check(book, 'METHOD-13', 'historic_name_and_owner_resolve_at_referenced_revision', {'snapshot': context['context_snapshot_id'], 'selected_strategy': old, 'current_strategy': new})
    # Use syntactically valid legacy commands: these negatives must reach the
    # authoritative object's protocol gate, not merely reject malformed JSON.
    legacy = Client.command('confirm_outcome', {}, target=flow.target(new['object_id']))
    denied = flow.deny('ceo', legacy, codes={'PROTOCOL_BINDING_CONFLICT'})
    check(book, 'METHOD-17', 'protocol_bound_server_side_not_request_downgrade', denied)
    revise = Client.command('propose_revision', {'payload': {'title': 'Legacy decision shape', 'statement': 'Must not replace a Method Strategy.'}}, target=flow.target(new['object_id']))
    create = Client.command('create_object', {'object_type': 'Decision', 'domain_id': flow.f['domains']['company'], 'payload': {'title': 'Legacy create shape', 'statement': 'Must not bypass the Method profile.'}})
    failures = flow.deny('ceo', revise, codes={'PROTOCOL_BINDING_CONFLICT'})
    failures += flow.deny('ceo', create, codes={'PROTOCOL_BINDING_CONFLICT', 'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'})
    check(book, 'METHOD-17', 'generic_legacy_create_or_propose_cannot_bypass_method', failures)
    assert {item['path'] for item in failures} == {'/v1/actions/prepare', '/v1/actions'}
    check(book, 'METHOD-17', 'prepare_and_execute_enforce_same_boundaries', failures)
    return {'old_strategy': old, 'new_strategy': new, 'old_confirmed_refs': old_refs,
            'new_targets': fresh, 'pending_stale_targets': pending, 'changed_issue': change,
            'historical_context': context['context_snapshot_id']}


def final_regressions(flow, book, *, source, regression_file, history_file):
    """Require actual, source-matched final results; absent reports never pass."""
    regression = json.loads(Path(regression_file).read_text())
    history = json.loads(Path(history_file).read_text())
    current = source_manifest(Path(source))
    assert regression['passed'] is True and regression['source_unchanged'] is True
    assert history['source_unchanged'] is True
    assert regression['source_manifest'] == current
    assert history['source_manifest'] == current
    assert regression['pytest_exit'] == 0 and regression['legacy_serialization_passed']
    check(book, 'METHOD-18', 'legacy_request_serialization_unchanged', {'report': str(regression_file), 'hash_goldens_passed': True})
    assert regression['a2_local_matrix_accepted'] is True and regression['a3_local_matrix_accepted'] is True
    check(book, 'METHOD-18', 'old_a1_a2_a3_and_legacy_delivery_regressions_pass', {'report': str(regression_file), 'pytest_passed': regression['pytest_passed']})
    assert history['A2_A3_history_preserved'] and history['receipts_replayed'] >= 21 and history['replay_business_state_unchanged']
    check(book, 'METHOD-18', 'preupgrade_history_receipts_and_effectiveness_unchanged', {'report': str(history_file), 'replayed': history['receipts_replayed']})
    assert history['raw_evidence_history_unchanged'] and history['objects'] >= 14
    check(book, 'METHOD-18', 'new_method_does_not_reinterpret_contract_a', {'report': str(history_file), 'objects': history['objects']})
    # Fetching a historical artifact is still checked against current rights.
    old_ref = next(item['ref'] for item in flow.evidence if item['actor'] == 'ceo')
    denied = flow.clients['outsider'].json('GET', f"/v1/objects/{old_ref['object_id']}/revisions/{old_ref['revision_id']}", expected={403,404})
    assert denied['error']['code'] in {'NOT_FOUND','FORBIDDEN'}
    check(book, 'METHOD-18', 'historical_reads_remain_currently_authorized', {'code': denied['error']['code'], 'historical_revision': old_ref['revision_id']})
