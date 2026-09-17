"""Real HTTP+PG acceptance of the tkos.method/0.4 formal governance chain.

Happy path and the negative/rollback/replay matrix all run through
/v1/actions/prepare + /v1/actions. SQL is used only for independent assertions
and identity fixture setup. This script does not prove real-model behaviour.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import psycopg

from acceptance.method_independent.harness import MethodHarness
from acceptance.method_independent.cases_recovery import _fail_after_write
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import register_v04, seed_v04
from .flow import Flow, period


def _change(flow, *, strategy_target=None, architecture_target=None, strategy=True, architecture=True,
            label='v1', rationale='Explicit applicability rationale for the retained object.'):
    return {'strategy_target_ref': strategy_target, 'architecture_target_ref': architecture_target,
            'strategy': flow.strategy_fields(label=label) if strategy else None,
            'architecture': flow.architecture_fields(label=label) if architecture else None,
            'applicability_rationale': rationale}


def _frame(flow, evidence, title, strategy_ref, architecture_ref):
    return {'title': title, 'summary': 'Reframe the issue while preserving historical records.',
            'core_question': 'Which value step should the company formalize next?',
            'business_scope': 'strategic', 'urgency': 'green', 'source_refs': [evidence],
            'strategy_ref': strategy_ref, 'architecture_ref': architecture_ref}


def _revoke(h, f, assignment_id):
    with psycopg.connect(h.env.values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute("UPDATE gov_role_assignments SET active=false WHERE scope_id=%s AND assignment_id=%s",
                     (f['scope_id'], assignment_id))


def _appoint(h, f, name, domain_key, role):
    """Control-plane identity fixture: a fresh current appointment for an
    existing principal. No business row is written here."""
    from uuid import uuid4
    assignment = str(uuid4())
    with psycopg.connect(h.env.values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute("INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES(%s,%s,%s,%s,%s)",
                     (assignment, f['scope_id'], f['actors'][name]['principal_id'], f['domains'][domain_key], role))
    return assignment


def candidate_refs(h, f, candidate):
    return h.sql(f, "SELECT payload->'target_refs' AS refs FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                 (f['scope_id'], candidate['revision_id']))[0]['refs']


def happy_path(h, f, flow):
    checks = []

    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)

    evidence = flow.upload()
    run_ref = flow.open_run()
    issue = flow.create_issue(run_ref, evidence)
    check('direct_agent_issue_without_existing_pair',
          flow.object(issue['object_id'])['method_state']['phase'] == 'issue_confirmed')
    check('initiating_agent_recorded',
          flow.object(issue['object_id'])['method_state']['initiating_agent_id'] == flow.principal('ceo_agent'))
    flow.set_participants(issue)
    check('ceo_sets_participants_and_research',
          flow.object(issue['object_id'])['method_state']['research_principal_ids'] == [flow.principal('dri_a')])

    agreement = flow.draft_agreement(issue)
    flow.confirm_agreement('ceo', agreement)
    flow.confirm_agreement('dri_a', agreement)
    check('agreement_waits_for_all_exact_signers',
          flow.object(agreement['object_id'])['method_state']['phase'] == 'awaiting_confirmation')
    finalized = flow.confirm_agreement('owner_a', agreement)
    agreement = {k: finalized['result'][k] for k in ('object_id', 'revision_id', 'payload_hash')}
    check('agreement_formal_after_all_current_signers',
          flow.object(agreement['object_id'])['method_state']['phase'] == 'formal')
    check('agreement_is_effective_only_after_formalization',
          flow.object(agreement['object_id'])['effective_revision_id'] is not None)

    proposal = flow.propose_update(issue, agreement, _change(
        flow, rationale='Initial pair: no retained object exists yet.'))
    flow.review_update(proposal)
    confirmed = flow.confirm_update(proposal)
    strategy_ref, architecture_ref = confirmed['result']['changed_refs']
    check('initial_pair_created_atomically',
          flow.object(strategy_ref['object_id'])['effective_revision_id'] == strategy_ref['revision_id']
          and flow.object(architecture_ref['object_id'])['effective_revision_id'] == architecture_ref['revision_id'])
    check('strategy_binds_its_architecture',
          flow.object(strategy_ref['object_id'])['method_state']['architecture_ref'] == architecture_ref)
    strategy_v1, architecture_v1 = strategy_ref, architecture_ref
    check('strategy_head_registered',
          len(h.sql(f, "SELECT * FROM gov_method_strategy_heads WHERE scope_id=%s", (f['scope_id'],))) == 1)
    check('no_execution_side_effects',
          not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))

    # ------------------------------------------------------ Architecture-only
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (architecture round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    agreement2 = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement2)
    agreement2 = flow.ref(agreement2['object_id'], effective=True)
    architecture_only = flow.propose_update(issue, agreement2, _change(
        flow, strategy_target=strategy_ref, architecture_target=architecture_ref,
        strategy=False, architecture=True, label='v2',
        rationale='Strategy fields remain applicable unchanged to the revised Architecture.'))
    flow.review_update(architecture_only)
    arch_result = flow.confirm_update(architecture_only)['result']
    check('architecture_only_change_keeps_strategy_revision',
          arch_result['strategy_ref'] == strategy_ref
          and arch_result['architecture_ref'] != architecture_ref
          and arch_result['changed_refs'] == [arch_result['architecture_ref']])
    architecture_ref = arch_result['architecture_ref']
    check('retained_strategy_unchanged',
          flow.object(strategy_ref['object_id'])['latest_revision_id'] == strategy_ref['revision_id'])

    # ---------------------------------------------------------- Strategy-only
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (strategy round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    agreement3 = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement3)
    agreement3 = flow.ref(agreement3['object_id'], effective=True)
    strategy_only = flow.propose_update(issue, agreement3, _change(
        flow, strategy_target=strategy_ref, architecture_target=architecture_ref,
        strategy=True, architecture=False, label='v2',
        rationale='Architecture definitions remain applicable unchanged to the revised Strategy.'))
    flow.review_update(strategy_only)
    strat_result = flow.confirm_update(strategy_only)['result']
    check('strategy_only_change_keeps_architecture_revision',
          strat_result['architecture_ref'] == architecture_ref
          and strat_result['strategy_ref'] != strategy_ref
          and strat_result['changed_refs'] == [strat_result['strategy_ref']])
    strategy_ref = strat_result['strategy_ref']

    # ------------------------------------------------------------------ M1B
    ltco_period = period(-30, 335)
    pco_period = period(-1, 30)
    mission_period = period(0, 15)
    ltco_a = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-a', ltco_period)
    ltco_b = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-b', ltco_period)
    flow.confirm_ltco(ltco_a)
    flow.confirm_ltco(ltco_b)
    ltco_a = flow.ref(ltco_a['object_id'], effective=True)
    ltco_b = flow.ref(ltco_b['object_id'], effective=True)
    check('two_ltco_results_share_basis_but_keep_identity',
          ltco_a['object_id'] != ltco_b['object_id']
          and flow.object(ltco_a['object_id'])['method_state']['phase'] == 'confirmed')
    pco_a = flow.draft_pco(ltco_a, 'scope-a', pco_period)
    pco_b = flow.draft_pco(ltco_b, 'scope-b', pco_period)
    mission_a = flow.draft_mission(pco_a, 'owner_a', 'scope-a', mission_period, evidence=[evidence])
    mission_b = flow.draft_mission(pco_b, 'owner_b', 'scope-b', mission_period)
    check('pco_exact_parent_ltco_and_independent_identity',
          flow.object(pco_a['object_id'])['latest_revision']['payload']['parent_ltco_ref'] == ltco_a
          and flow.object(pco_b['object_id'])['latest_revision']['payload']['parent_ltco_ref'] == ltco_b)

    window = flow.open_window([pco_a, pco_b], [mission_a, mission_b], [ltco_a, ltco_b],
                              pco_period, names=('dri_a', 'dri_b', 'owner_a', 'owner_b'))
    check('window_freezes_multiple_ltcos',
          flow.object(window['object_id'])['latest_revision']['payload']['ltco_refs'] == [ltco_a, ltco_b])
    first = flow.comment(window, pco_a, 'dri_a')
    second = flow.comment(window, mission_a, 'dri_a')
    owner_opinion = flow.comment(window, mission_a, 'owner_a')
    replacement = flow.comment(window, pco_a, 'dri_a', replaces=first)
    flow.withdraw(window, owner_opinion, 'owner_a')
    flow.assist(window, mission_a, 'owner_agent_a', 'owner_a')
    closed = flow.close_window(window)
    # Multiple opinions per member are legal; replacing one record preserves the
    # other, and every frozen opinion is resolved exactly once.
    check('replace_withdraw_preserves_other_frozen_opinions',
          set(closed['frozen_opinion_ids']) == {second, replacement}
          and owner_opinion not in closed['frozen_opinion_ids'])

    resolve_params = {'title': 'Synthetic 0.4 candidate set', 'pcos': [], 'missions': [],
                      'dispositions': [{'review_record_id': second, 'decision': 'adopted',
                                        'rationale': 'The exact frozen opinion is adopted.'},
                                       {'review_record_id': replacement, 'decision': 'adopted',
                                        'rationale': 'The exact frozen replacement is adopted.'}],
                      'unresolved_differences': [], 'summary': 'Retain both PCO/Mission results.'}
    for ref in (pco_a, pco_b):
        payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
        payload['result_statement'] += ' (candidate revision)'
        resolve_params['pcos'].append({'object_id': ref['object_id'], 'payload': payload})
    for ref in (mission_a, mission_b):
        payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
        payload.pop('parent_pco_ref')
        resolve_params['missions'].append({'object_id': ref['object_id'], **payload})
    candidate = flow.resolve_window(window, resolve_params)
    check('candidate_covers_every_frozen_member', len(candidate_refs(h, f, candidate)) == 4)
    candidate = flow.ref(candidate['object_id'])
    by_object = {ref['object_id']: ref for ref in candidate_refs(h, f, candidate)}
    flow.commit_candidate(candidate, by_object[pco_a['object_id']], 'dri_a')
    flow.commit_candidate(candidate, by_object[pco_b['object_id']], 'dri_b')
    flow.commit_candidate(candidate, by_object[mission_a['object_id']], 'owner_a')
    flow.commit_candidate(candidate, by_object[mission_b['object_id']], 'owner_b')
    activation = flow.activate_candidates(candidate)
    check('whole_candidate_set_activated_by_ceo',
          activation['execution_authority_created'] is False
          and all(flow.object(ref['object_id'])['effective_revision_id'] == ref['revision_id']
                  for ref in by_object.values()))
    check('commitments_are_separate_from_opinions',
          len(h.sql(f, "SELECT * FROM gov_method_commitments WHERE scope_id=%s", (f['scope_id'],))) == 4)
    check('no_execution_or_acceptance_after_m1b',
          not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))

    # ---------------------------------------------------------------- State
    pco_state = flow.propose_state(by_object[pco_a['object_id']], actor='dri_a')
    flow.deny('dri_a', flow.command('method_propose_state', {
        'domain_id': f['domains']['company'],
        'payload': {'subject_ref': by_object[pco_b['object_id']], 'as_of': '2026-09-17T00:00:00Z',
                    'summary': 'A non-owner proposes another scope state.', 'rag': 'unknown',
                    'baseline_refs': [by_object[pco_b['object_id']]],
                    'evidence_refs': [], 'data_gaps': ['gap'], 'generation_version': 'invalid-owner'}}),
        codes={'FORBIDDEN', 'INVALID_REQUEST'})
    check('noncompany_owner_can_only_propose_own_state', True)
    flow.confirm_state('dri_a', pco_state)
    pco_state = flow.ref(pco_state['object_id'], effective=True)
    check('pco_state_responsibility_is_scope_dri',
          flow.object(pco_state['object_id'])['method_state']['canonical_ref'] == pco_state)
    # A Battlefield primary Scope resolves its own explicitly mapped DRI.
    pco_b_state = flow.propose_state(by_object[pco_b['object_id']])
    flow.confirm_state('dri_b', pco_b_state)
    pco_b_state = flow.ref(pco_b_state['object_id'], effective=True)
    check('battlefield_scope_dri_confirms_own_state',
          flow.object(pco_b_state['object_id'])['method_state']['canonical_ref'] == pco_b_state)
    mission_state = flow.propose_state(by_object[mission_a['object_id']], actor='owner_a', rag='green',
                                       summary='Evidence supports progress', evidence=[evidence], data_gaps=())
    confirmed_state = flow.confirm_state('owner_a', mission_state, summary='Quality gap remains', rag='yellow')
    mission_state = {k: confirmed_state[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    state_obj = flow.object(mission_state['object_id'])
    check('mission_owner_not_dri_confirms_own_state',
          state_obj['method_state']['canonical_ref'] == mission_state
          and state_obj['latest_revision']['payload']['rag'] == 'yellow'
          and state_obj['method_state']['recommendation_ref'] != mission_state)
    check('period_review_uses_canonical_states',
          flow.act('co_agent', 'm1b_generate_review', {
              'domain_id': f['domains']['company'],
              'payload': {'review_id': 'synthetic-0-4-review', 'title': 'Synthetic 0.4 period review',
                          'period': pco_period, 'target_refs': [by_object[pco_a['object_id']], by_object[mission_a['object_id']]],
                          'state_refs': [pco_state, mission_state], 'fact_refs': [],
                          'findings': ['Canonical states are the reviewed basis.'], 'learnings': [],
                          'implications': [], 'generation_version': 'controlled-review-1'}})['status'] == 'committed')

    # -------------------------------------------------------------- Problem
    ltco_state = flow.propose_state(ltco_a)
    flow.confirm_state('ceo', ltco_state)
    ltco_state = flow.ref(ltco_state['object_id'], effective=True)
    problem = flow.open_problem(ltco_state)
    flow.deny('owner_b', flow.command('method_close_problem', {
        'disposition': 'resolved', 'reason': 'Unrelated actor tries to close.',
        'evidence_refs': [evidence]}, oid=problem['object_id']), codes={'FORBIDDEN', 'NOT_FOUND'})
    # A completed round is terminal: an open Problem cannot be silently parked there.
    flow.deny('ceo_agent', flow.command('m1a_transfer_problem', {
        'problem_ref': problem, 'reason': 'A completed round must reject the transfer.'},
        oid=issue['object_id']), codes={'INVALID_STATE'})
    check('completed_issue_rejects_transfer_and_keeps_tracking',
          flow.object(problem['object_id'])['method_state']['tracking'] is True
          and flow.object(problem['object_id'])['method_state']['phase'] == 'open')
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (transfer round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    transferred = flow.transfer_problem(issue, problem)
    check('problem_transfer_is_atomic_and_not_solved',
          flow.object(problem['object_id'])['method_state']['phase'] == 'transferred'
          and flow.object(problem['object_id'])['method_state']['tracking'] is False
          and transferred['transferred'] is True)
    check('historical_transfer_link_visible_after_round',
          any(item['object_id'] == problem['object_id']
              for item in flow.object(issue['object_id'])['method_state'].get('transferred_problems', [])))

    return {'checks': checks, 'evidence': evidence, 'issue': issue,
            'strategy_ref': strategy_ref, 'architecture_ref': architecture_ref,
            'strategy_v1': strategy_v1, 'architecture_v1': architecture_v1,
            'ltco_a': ltco_a, 'ltco_b': ltco_b, 'ltco_state': ltco_state}


def negatives(h, f, flow, ctx):
    checks = []

    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)

    evidence = ctx['evidence']
    issue = ctx['issue']
    strategy_ref = ctx['strategy_ref']
    architecture_ref = ctx['architecture_ref']
    ltco_a, ltco_b = ctx['ltco_a'], ctx['ltco_b']

    # ------------------------------------------- replay / duplicate response
    run2 = flow.open_run(title='Synthetic 0.4 replay run')
    replay_body = flow.prepare_run('ceo_agent', 'm1a_create_issue', {
        'domain_id': f['domains']['company'],
        'payload': {'title': 'Synthetic replay issue', 'summary': 'Replay the same original envelope.',
                    'core_question': 'Does the same envelope return the same receipt?', 'business_scope': 'strategic',
                    'urgency': 'green', 'source_refs': [evidence],
                    'strategy_ref': ctx['strategy_ref'], 'architecture_ref': ctx['architecture_ref']}}, run=run2)
    first_receipt = flow.commit('ceo_agent', replay_body)
    second_receipt = flow.commit('ceo_agent', replay_body)
    check('original_envelope_replay_returns_same_receipt', first_receipt == second_receipt)

    # ------------------------------------------------------ agreement denials
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (denial round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    agreement = flow.draft_agreement(issue, names=('ceo', 'unrelated', 'owner_a'))
    flow.deny('ceo_agent', flow.command('m1a_confirm_agreement',
                                        {'statement': 'An Agent tries to confirm.'},
                                        oid=agreement['object_id']), codes={'FORBIDDEN'})
    check('agent_cannot_confirm_agreement', True)
    flow.confirm_agreement('ceo', agreement)
    flow.confirm_agreement('unrelated', agreement)
    _revoke(h, f, flow.assignment('unrelated'))
    revoked_body = flow.prepare('owner_a', 'm1a_confirm_agreement',
                                {'statement': 'Last signer confirms after a peer was revoked.'},
                                oid=agreement['object_id'])
    flow.deny('owner_a', revoked_body, codes={'FORBIDDEN'}, prepare=False)
    check('revoked_signer_blocks_formalization',
          flow.object(agreement['object_id'])['method_state']['phase'] == 'awaiting_confirmation')

    # A revision changes the exact version and invalidates collected confirmations.
    agreement = flow.act('ceo_agent', 'm1a_revise_agreement', {'payload': {
        'title': 'Synthetic 0.4 Agreement (revised roster)',
        'issue_ref': flow.ref(issue['object_id']),
        'statement': 'The roster and body changed; every confirmation must be collected again.',
        'participants': flow.participants(('ceo', 'dri_a', 'owner_a')),
        'evidence_refs': [], 'no_change': False}}, oid=agreement['object_id'])['result']
    agreement = {k: agreement[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    flow.confirm_agreement('ceo', agreement)
    revised = flow.act('ceo_agent', 'm1a_revise_agreement', {'payload': {
        'title': 'Synthetic 0.4 Agreement (body change)',
        'issue_ref': flow.ref(issue['object_id']),
        'statement': 'The body changed after one confirmation; pending confirmations are invalid.',
        'participants': flow.participants(('ceo', 'dri_a', 'owner_a')),
        'evidence_refs': [], 'no_change': False}}, oid=agreement['object_id'])['result']
    agreement = {k: revised[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    state = flow.object(agreement['object_id'])['method_state']
    check('body_change_invalidates_collected_confirmations',
          state['phase'] == 'draft' and state.get('confirmed_principal_ids') == [])
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement)
    agreement = flow.ref(agreement['object_id'], effective=True)

    # -------------------------------------------------- stale update denials
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (stale basis round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    stale_proposal = flow.command('m1a_propose_update', {'payload': {
        'title': 'Stale agreement proposal', 'issue_ref': flow.ref(issue['object_id']),
        'agreement_ref': agreement, 'rationale': 'This agreement binds an older issue revision.',
        'change': _change(flow, strategy_target=strategy_ref, architecture_target=architecture_ref,
                          label='stale', rationale='Explicit rationale.')}}, oid=issue['object_id'])
    flow.deny('ceo_agent', stale_proposal, codes={'INVALID_STATE', 'STALE_DEPENDENCY', 'INVALID_REQUEST'})
    check('proposal_requires_formal_current_round_agreement', True)

    # ------------------------------------------------------ paired rollback
    agreement = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement)
    agreement = flow.ref(agreement['object_id'], effective=True)
    paired = flow.propose_update(issue, agreement, _change(
        flow, strategy_target=strategy_ref, architecture_target=architecture_ref,
        strategy=True, architecture=True, label='v3',
        rationale='Both objects change together under current-basis CAS.'))
    flow.review_update(paired)
    stale_pair = flow.command('m1a_propose_update', {'payload': {
        'title': 'Stale paired proposal', 'issue_ref': flow.ref(issue['object_id']),
        'agreement_ref': agreement, 'rationale': 'The architecture target is no longer effective.',
        'change': _change(flow, strategy_target=strategy_ref, architecture_target=ctx['architecture_v1'],
                          strategy=True, architecture=True, label='stale',
                          rationale='Explicit rationale.')}}, oid=issue['object_id'])
    flow.deny('ceo_agent', stale_pair, codes={'STALE_DEPENDENCY'})
    check('stale_paired_basis_rejected', True)
    confirm_body = flow.prepare('ceo', 'm1a_confirm_update',
                                {'statement': 'Rollback proof for the paired atomic update.'},
                                oid=paired['object_id'])
    _fail_after_write(flow, 'ceo', confirm_body, 'after_method_pair_strategy_write')
    check('paired_update_rollback_leaves_no_partial_revision',
          flow.object(strategy_ref['object_id'])['latest_revision_id'] == strategy_ref['revision_id']
          and flow.object(architecture_ref['object_id'])['latest_revision_id'] == architecture_ref['revision_id'])
    paired_result = flow.commit('ceo', confirm_body)['result']
    strategy_ref = paired_result['strategy_ref']
    architecture_ref = paired_result['architecture_ref']
    check('paired_update_retry_after_rollback_succeeds',
          flow.object(strategy_ref['object_id'])['effective_revision_id'] == strategy_ref['revision_id']
          and flow.object(architecture_ref['object_id'])['effective_revision_id'] == architecture_ref['revision_id'])

    # ------------------------------------------- window / opinion / candidate
    # The paired update created a new exact basis: fresh results cite it.
    ltco_period = period(-30, 335)
    ltco_c = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-a', ltco_period)
    ltco_d = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-b', ltco_period)
    flow.confirm_ltco(ltco_c)
    flow.confirm_ltco(ltco_d)
    ltco_c = flow.ref(ltco_c['object_id'], effective=True)
    ltco_d = flow.ref(ltco_d['object_id'], effective=True)
    pco_period = period(31, 60)
    mission_period = period(31, 45)
    pco_c = flow.draft_pco(ltco_c, 'scope-a', pco_period)
    pco_d = flow.draft_pco(ltco_d, 'scope-b', pco_period)
    mission_c = flow.draft_mission(pco_c, 'owner_a', 'scope-a', mission_period)
    mission_d = flow.draft_mission(pco_d, 'owner_b', 'scope-b', mission_period)
    incomplete = flow.command('m1b_open_window', {'domain_id': f['domains']['company'], 'payload': {
        'title': 'Incomplete window', 'period': pco_period,
        'architecture_ref': flow.object(pco_c['object_id'])['latest_revision']['payload']['architecture_ref'],
        'strategy_ref': flow.object(pco_c['object_id'])['latest_revision']['payload']['strategy_ref'],
        'ltco_refs': [ltco_c, ltco_d], 'pco_refs': [pco_c], 'mission_refs': [mission_c],
        'participants': flow.participants(('dri_a', 'dri_b', 'owner_a', 'owner_b'))}})
    flow.deny('co_agent', incomplete, codes={'INVALID_REQUEST'})
    check('window_rejects_phantom_omission', True)
    duplicate = flow.command('m1b_open_window', {'domain_id': f['domains']['company'], 'payload': {
        'title': 'Duplicate window', 'period': pco_period,
        'architecture_ref': flow.object(pco_c['object_id'])['latest_revision']['payload']['architecture_ref'],
        'strategy_ref': flow.object(pco_c['object_id'])['latest_revision']['payload']['strategy_ref'],
        'ltco_refs': [ltco_c, ltco_d], 'pco_refs': [pco_c, pco_c], 'mission_refs': [mission_c, mission_d],
        'participants': flow.participants(('dri_a', 'dri_b', 'owner_a', 'owner_b'))}})
    flow.deny('co_agent', duplicate, codes={'INVALID_REQUEST'})
    check('window_rejects_duplicate_members', True)

    window = flow.open_window([pco_c, pco_d], [mission_c, mission_d], [ltco_c, ltco_d], pco_period,
                              names=('dri_a', 'dri_b', 'owner_a', 'owner_b'))
    opinion = flow.comment(window, pco_c, 'dri_a')
    opinion2 = flow.comment(window, mission_c, 'dri_a')
    check('same_member_may_publish_distinct_field_opinions', opinion != opinion2)
    flow.close_window(window)

    def candidate_params(dispositions, differences, *, pcos=(pco_c, pco_d), missions=(mission_c, mission_d)):
        params = {'title': 'Synthetic 0.4 candidate set', 'pcos': [], 'missions': [],
                  'dispositions': dispositions, 'unresolved_differences': differences,
                  'summary': 'Retain the frozen results with explicit unresolved differences.'}
        for ref in pcos:
            payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
            params['pcos'].append({'object_id': ref['object_id'], 'payload': payload})
        for ref in missions:
            if ref is None:
                continue
            payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
            payload.pop('parent_pco_ref')
            params['missions'].append({'object_id': ref['object_id'], **payload})
        return params

    missing_opinion = flow.command('m1b_resolve_window', candidate_params([], []), oid=window['object_id'])
    flow.deny('co_agent', missing_opinion, codes={'INVALID_REQUEST'})
    check('resolution_requires_every_frozen_opinion', True)
    duplicate_opinion = flow.command('m1b_resolve_window', candidate_params([
        {'review_record_id': opinion, 'decision': 'adopted', 'rationale': 'once'},
        {'review_record_id': opinion, 'decision': 'not_adopted', 'rationale': 'twice'}], []), oid=window['object_id'])
    flow.deny('co_agent', duplicate_opinion, codes={'INVALID_REQUEST'})
    check('resolution_rejects_duplicate_dispositions', True)
    flow.deny('co_agent', flow.command('m1b_resolve_window',
                                       candidate_params([], [], missions=(mission_c,)), oid=window['object_id']),
              codes={'INVALID_REQUEST'})
    check('resolution_requires_every_frozen_mission', True)
    critical = [{'topic': 'Evidence sufficiency', 'statement': 'Evidence sufficiency remains unresolved.',
                 'critical': True}]
    adopted = [{'review_record_id': opinion, 'decision': 'adopted', 'rationale': 'Adopted exactly once.'},
               {'review_record_id': opinion2, 'decision': 'not_adopted', 'rationale': 'The other exact field opinion.'}]
    # Rollback proof: the whole candidate member set is one atomic write.
    resolve_body = flow.prepare('co_agent', 'm1b_resolve_window',
                                candidate_params(adopted, critical), oid=window['object_id'])
    _fail_after_write(flow, 'co_agent', resolve_body, 'after_method_candidate_write')
    check('candidate_set_rollback_is_atomic',
          flow.object(pco_c['object_id'])['latest_revision_id'] == pco_c['revision_id']
          and flow.object(mission_d['object_id'])['latest_revision_id'] == mission_d['revision_id'])
    candidate = flow.resolve_window(window, resolve_body['params'])
    candidate = flow.ref(candidate['object_id'])
    by_object = {ref['object_id']: ref for ref in candidate_refs(h, f, candidate)}
    flow.deny('owner_b', flow.command('m1b_commit_candidate', {
        'responsibility_ref': by_object[pco_c['object_id']],
        'statement': 'A non-owner tries to commit scope A responsibility.'}, oid=candidate['object_id']),
        codes={'FORBIDDEN'})
    check('commit_requires_own_responsibility', True)
    flow.deny('dri_a', flow.command('m1b_commit_candidate', {
        'responsibility_ref': pco_c, 'statement': 'A stale draft revision cannot be committed.'},
        oid=candidate['object_id']), codes={'INVALID_REQUEST'})
    check('stale_candidate_reference_rejected', True)
    flow.deny('ceo', flow.command('m1b_activate_candidates', {
        'statement': 'Activate before commitments.', 'notes': []}, oid=candidate['object_id']),
        codes={'INVALID_STATE'})
    check('missing_commitments_block_activation', True)
    flow.commit_candidate(candidate, by_object[pco_c['object_id']], 'dri_a')
    flow.commit_candidate(candidate, by_object[pco_d['object_id']], 'dri_b')
    flow.commit_candidate(candidate, by_object[mission_c['object_id']], 'owner_a')
    flow.commit_candidate(candidate, by_object[mission_d['object_id']], 'owner_b')
    flow.deny('ceo', flow.command('m1b_activate_candidates', {
        'statement': 'Critical difference still blocks.', 'notes': []}, oid=candidate['object_id']),
        codes={'INVALID_STATE'})
    check('critical_difference_blocks_activation', True)

    # Reopen invalidates the pending candidate and its commitments stay historical.
    reopened = flow.act('ceo', 'm1b_reopen_candidates', {
        'reason': 'Resolve the critical difference through an explicit member round.',
        'title': 'Synthetic 0.4 reopened window'}, oid=candidate['object_id'])['result']
    check('member_change_requires_explicit_reopen',
          flow.object(candidate['object_id'])['method_state']['phase'] == 'reopened')
    window3 = flow.ref(reopened['object_id'])
    flow.close_window(window3)
    resolve_params3 = candidate_params([], [])
    candidate3 = flow.resolve_window(window3, resolve_params3)
    candidate3 = flow.ref(candidate3['object_id'])
    by_object3 = {ref['object_id']: ref for ref in candidate_refs(h, f, candidate3)}
    flow.deny('ceo', flow.command('m1b_activate_candidates', {
        'statement': 'Still missing commitments.', 'notes': []}, oid=candidate3['object_id']),
        codes={'INVALID_STATE'})
    # Concurrency: two preparations of the same commitment; the second commit is stale.
    commit_params = {'responsibility_ref': by_object3[pco_c['object_id']],
                     'statement': 'Concurrent commitment attempt.'}
    body_one = flow.prepare('dri_a', 'm1b_commit_candidate', commit_params, oid=candidate3['object_id'])
    body_two = flow.prepare('dri_a', 'm1b_commit_candidate', commit_params, oid=candidate3['object_id'])
    flow.commit('dri_a', body_one)
    stale_response = flow.clients['dri_a'].json('POST', '/v1/actions', body_two, expected={409})
    check('concurrent_stale_commit_is_rejected',
          stale_response['error']['code'] in {'VERSION_CONFLICT', 'INVALID_STATE'})
    flow.commit_candidate(candidate3, by_object3[pco_d['object_id']], 'dri_b')
    flow.commit_candidate(candidate3, by_object3[mission_c['object_id']], 'owner_a')
    flow.commit_candidate(candidate3, by_object3[mission_d['object_id']], 'owner_b')
    flow.activate_candidates(candidate3)
    check('reopened_round_activates_whole_set',
          all(flow.object(ref['object_id'])['effective_revision_id'] == ref['revision_id']
              for ref in by_object3.values()))

    # ---------------- stale basis: prepared activation then a formal update
    def build_cycle(pco_period, mission_period):
        ltco_x = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-a', ltco_period)
        ltco_y = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-b', ltco_period)
        flow.confirm_ltco(ltco_x)
        flow.confirm_ltco(ltco_y)
        ltco_x = flow.ref(ltco_x['object_id'], effective=True)
        ltco_y = flow.ref(ltco_y['object_id'], effective=True)
        pco_x = flow.draft_pco(ltco_x, 'scope-a', pco_period)
        pco_y = flow.draft_pco(ltco_y, 'scope-b', pco_period)
        mission_x = flow.draft_mission(pco_x, 'owner_a', 'scope-a', mission_period)
        mission_y = flow.draft_mission(pco_y, 'owner_b', 'scope-b', mission_period)
        window_x = flow.open_window([pco_x, pco_y], [mission_x, mission_y], [ltco_x, ltco_y], pco_period,
                                    names=('dri_a', 'dri_b', 'owner_a', 'owner_b'))
        opinion = flow.comment(window_x, pco_x, 'dri_a')
        frozen = flow.close_window(window_x)['frozen_opinion_ids']
        assert frozen == [opinion]
        params = candidate_params([{'review_record_id': opinion, 'decision': 'adopted',
                                    'rationale': 'Adopted exactly once.'}], [],
                                  pcos=(pco_x, pco_y), missions=(mission_x, mission_y))
        candidate = flow.resolve_window(window_x, params)
        candidate = flow.ref(candidate['object_id'])
        return {'window': window_x, 'candidate': candidate,
                'by_object': {ref['object_id']: ref for ref in candidate_refs(h, f, candidate)},
                'pco_x': pco_x, 'pco_y': pco_y, 'mission_x': mission_x, 'mission_y': mission_y}

    def commit_all(cycle, actor_owners=('dri_a', 'dri_b', 'owner_a', 'owner_b')):
        for ref, actor in zip((cycle['pco_x'], cycle['pco_y'], cycle['mission_x'], cycle['mission_y']), actor_owners):
            flow.commit_candidate(cycle['candidate'], cycle['by_object'][ref['object_id']], actor)

    cycle4 = build_cycle(period(91, 120), period(91, 105))
    commit_all(cycle4)
    activation4 = flow.prepare('ceo', 'm1b_activate_candidates', {
        'statement': 'Prepared before the formal Strategy update.', 'notes': []},
        oid=cycle4['candidate']['object_id'])
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (stale-candidate round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    agreement4 = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement4)
    agreement4 = flow.ref(agreement4['object_id'], effective=True)
    proposal4 = flow.propose_update(issue, agreement4, _change(
        flow, strategy_target=strategy_ref, architecture_target=architecture_ref,
        strategy=True, architecture=True, label='v4',
        rationale='A later formal update changes the exact adopted basis.'))
    flow.review_update(proposal4)
    updated4 = flow.confirm_update(proposal4)['result']
    strategy_ref, architecture_ref = updated4['strategy_ref'], updated4['architecture_ref']
    flow.deny('ceo', activation4, codes={'STALE_DEPENDENCY'}, prepare=False)
    check('prepared_activation_rejected_after_strategy_change',
          flow.object(cycle4['candidate']['object_id'])['method_state']['phase'] == 'pending')
    flow.deny('ceo', flow.command('m1b_activate_candidates', {
        'statement': 'Fresh prepare of a stale candidate.', 'notes': []},
        oid=cycle4['candidate']['object_id']), codes={'STALE_DEPENDENCY'})
    check('stale_candidate_cannot_be_freshly_prepared', True)
    flow.deny('dri_b', flow.command('m1b_commit_candidate', {
        'responsibility_ref': cycle4['by_object'][cycle4['pco_y']['object_id']],
        'statement': 'A stale candidate cannot collect new commitments.'},
        oid=cycle4['candidate']['object_id']), codes={'STALE_DEPENDENCY'})
    check('stale_candidate_rejects_new_commitments', True)
    check('historical_effective_goals_preserved',
          flow.object(by_object3[pco_c['object_id']]['object_id'])['effective_revision_id'] == by_object3[pco_c['object_id']]['revision_id']
          and flow.object(cycle4['pco_x']['object_id'])['effective_revision_id'] is None)

    # Recovery on the new exact basis while the historical results stay effective.
    cycle5 = build_cycle(period(121, 150), period(121, 135))
    commit_all(cycle5)
    flow.activate_candidates(cycle5['candidate'])
    check('recovery_activates_new_basis_cycle',
          all(flow.object(ref['object_id'])['effective_revision_id'] == ref['revision_id']
              for ref in cycle5['by_object'].values()))

    # ---------------- commitment assignment currency and explicit recovery
    cycle6 = build_cycle(period(151, 180), period(151, 165))
    commit_all(cycle6)
    recorded = h.sql(f, """SELECT assignment_id FROM gov_method_commitments
        WHERE scope_id=%s AND candidate_revision_id=%s AND responsibility_object_id=%s""",
        (f['scope_id'], cycle6['candidate']['revision_id'], cycle6['mission_y']['object_id']))
    committed_assignment = str(recorded[0]['assignment_id'])
    _revoke(h, f, committed_assignment)
    flow.deny('ceo', flow.command('m1b_activate_candidates', {
        'statement': 'Activation after the committer assignment was revoked.', 'notes': []},
        oid=cycle6['candidate']['object_id']), codes={'FORBIDDEN', 'INVALID_STATE'})
    check('revoked_committer_assignment_blocks_activation', True)
    _appoint(h, f, 'owner_b', 'auth_b', 'IC')
    flow.deny('ceo', flow.command('m1b_activate_candidates', {
        'statement': 'A fresh appointment cannot revive the old commitment.', 'notes': []},
        oid=cycle6['candidate']['object_id']), codes={'INVALID_STATE'})
    check('reappointment_alone_cannot_reuse_old_commitment', True)
    reopened6 = flow.act('ceo', 'm1b_reopen_candidates', {
        'reason': 'Explicit reopen to re-collect commitments under current appointments.',
        'title': 'Synthetic commitment recovery window'}, oid=cycle6['candidate']['object_id'])['result']
    window6 = flow.ref(reopened6['object_id'])
    flow.close_window(window6)
    params6 = candidate_params([], [], pcos=(cycle6['pco_x'], cycle6['pco_y']),
                               missions=(cycle6['mission_x'], cycle6['mission_y']))
    candidate6 = flow.ref(flow.resolve_window(window6, params6)['object_id'])
    cycle6['candidate'] = candidate6
    cycle6['by_object'] = {ref['object_id']: ref for ref in candidate_refs(h, f, candidate6)}
    commit_all(cycle6)
    flow.activate_candidates(candidate6)
    check('explicit_reopen_recommit_activates_after_reappointment',
          all(flow.object(ref['object_id'])['effective_revision_id'] == ref['revision_id']
              for ref in cycle6['by_object'].values()))

    # ---------------------------------------------------------- State denial
    mission_c = by_object3[mission_c['object_id']]
    flow.deny('co_agent', flow.command('method_propose_state', {
        'domain_id': f['domains']['company'],
        'payload': {'subject_ref': mission_c, 'as_of': '2026-09-17T00:00:00Z', 'summary': 'Known without evidence',
                    'rag': 'green', 'baseline_refs': [mission_c], 'evidence_refs': [], 'data_gaps': [],
                    'generation_version': 'invalid-1'}}), codes={'INVALID_REQUEST'})
    check('missing_evidence_cannot_be_known_rating', True)
    mission_state = flow.propose_state(mission_c)
    flow.deny('ceo', flow.command('method_confirm_state',
                                   {'reason': 'A reader without the exact responsibility tries to confirm.'},
                                   oid=mission_state['object_id']), codes={'FORBIDDEN'})
    check('state_requires_exact_responsible_person', True)
    flow.confirm_state('owner_a', mission_state)
    forged_review = flow.command('m1b_generate_review', {
        'domain_id': f['domains']['company'],
        'payload': {'review_id': 'forged-fact-review', 'title': 'Forged fact review', 'period': pco_period,
                    'target_refs': [mission_c], 'state_refs': [mission_state],
                    'fact_refs': [{'object_id': evidence['object_id'], 'revision_id': evidence['revision_id'],
                                   'payload_hash': 'f' * 64}],
                    'findings': ['Unsupported fact basis.'], 'generation_version': 'forged-1'}})
    flow.deny('co_agent', forged_review, codes={'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'})
    check('period_review_rejects_unsupported_fact_refs', True)
    mismatched_review = flow.command('m1b_generate_review', {
        'domain_id': f['domains']['company'],
        'payload': {'review_id': 'mismatched-target-review', 'title': 'Mismatched target review', 'period': pco_period,
                    'target_refs': [{'object_id': mission_c['object_id'], 'revision_id': evidence['revision_id'],
                                     'payload_hash': evidence['payload_hash']}],
                    'state_refs': [mission_state], 'fact_refs': [],
                    'findings': ['Exact target revision required.'], 'generation_version': 'mismatch-1'}})
    flow.deny('co_agent', mismatched_review, codes={'INVALID_REQUEST'})
    check('period_review_requires_exact_target_revision', True)

    # ------------------------------------------------- transfer rollback
    flow.act('ceo_agent', 'm1a_reframe_issue', {'payload': _frame(
        flow, evidence, 'Synthetic 0.4 issue (rollback transfer round)', strategy_ref, architecture_ref)},
        oid=issue['object_id'])
    problem = flow.open_problem(ctx['ltco_state'], question='Should the rollback transfer create a second distinct Problem?')
    transfer_body = flow.prepare('ceo_agent', 'm1a_transfer_problem', {
        'problem_ref': problem, 'reason': 'Rollback proof for the atomic transfer.'},
        oid=issue['object_id'])
    before = flow.h.snapshot(f)
    _fail_after_write(flow, 'ceo_agent', transfer_body, 'after_method_problem_transfer_link')
    after = flow.h.snapshot(f)
    check('transfer_failure_keeps_original_tracking',
          before == after
          and flow.object(problem['object_id'])['method_state'].get('tracking') is True
          and flow.object(problem['object_id'])['method_state']['phase'] == 'open')
    flow.commit('ceo_agent', transfer_body)
    check('transfer_retry_after_rollback_links_and_stops_tracking',
          flow.object(problem['object_id'])['method_state']['tracking'] is False
          and flow.object(problem['object_id'])['method_state']['phase'] == 'transferred')

    # -------------------------------- private workspace source fence
    from datetime import datetime, timezone
    from uuid import uuid4
    private_evidence = flow.upload(actor='owner_a', domain='auth_a',
                                   text='Synthetic private source evidence bytes')
    scene_id = str(uuid4())

    def source_command(actor, version, event):
        body = {'contract_version': 'tkos.workspace/0.2', 'scene_id': scene_id,
                'expected_version': version,
                'idempotency_key': 'acceptance-v04-source-' + uuid4().hex[:20], 'event': event}
        return flow.clients[actor].json('POST', '/v1/workspace-scenes/events', body)

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    source_command('owner_a', 0, {'kind': 'scene_create', 'scene_type': 'document',
        'external_id': 'synthetic-private-' + uuid4().hex[:8], 'title': 'Synthetic private source scene',
        'owner_principal_id': flow.principal('owner_a'),
        'participant_principal_ids': [flow.principal('dri_a')], 'agent_bindings': []})
    added = source_command('owner_a', 1, {'kind': 'source_add', 'system': 'synthetic-source',
        'external_id': 'synthetic-doc-' + uuid4().hex[:8], 'title': 'Private document source',
        'media_type': 'text/plain', 'acquired_at': now, 'source_revision': '1', 'sensitivity': 'private'})
    versioned = source_command('owner_a', 2, {'kind': 'source_version',
        'source_id': added['result']['source_id'], 'fingerprint': 'a' * 64, 'media_type': 'text/plain',
        'acquired_at': now, 'segments': [{'text': 'Private segment that must not leak.'}],
        'evidence_ref': private_evidence})
    check('private_source_links_exact_evidence',
          versioned['result']['evidence_linked'] is True)
    denied_state = flow.command('method_propose_state', {
        'domain_id': f['domains']['company'],
        'payload': {'subject_ref': by_object3[pco_c['object_id']], 'as_of': now, 'summary': 'Private evidence attempt',
                    'rag': 'green', 'baseline_refs': [by_object3[pco_c['object_id']]], 'evidence_refs': [private_evidence],
                    'data_gaps': [], 'generation_version': 'private-fence-1'}})
    flow.deny('dri_a', denied_state, codes={'NOT_FOUND'})
    check('formal_action_cannot_cite_unshared_private_source', True)
    source_command('owner_a', 3, {'kind': 'source_share', 'source_id': added['result']['source_id'],
        'version_event_id': versioned['result']['event_id'],
        'payload_hash': versioned['result']['payload_hash'],
        'share_to_principal_id': flow.principal('dri_a'), 'note': 'Exact-version share.'})
    flow.propose_state(by_object3[pco_c['object_id']], actor='dri_a', rag='green',
                       summary='Shared exact evidence', evidence=[private_evidence], data_gaps=())
    check('exact_share_allows_citing_exact_private_revision', True)

    return checks


def run(h: MethodHarness, source: Path):
    f = seed_v04(h.env, h.private / 'identities.json', 'runtime-acceptance-method-v04')
    register_v04(h, source, f)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        ctx = happy_path(h, f, flow)
        negative_checks = negatives(h, f, flow, ctx)
        public_json(h.output / 'summary.json', {
            'happy_path_passed': True, 'happy_path_checks': ctx['checks'],
            'negative_matrix_passed': True, 'negative_checks': negative_checks,
            'runtime_method_v04_api_accepted': True,
            'scope': 'Synthetic 0.4 HTTP/PG chain; controlled Agent inputs, not real-model acceptance',
            'partner_wiring': 'not_verified', 'clark_browser': 'not_verified', 'real_model': 'not_run'})
    finally:
        flow.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError('Use fresh private and output paths')
    args.private.mkdir(parents=True, mode=0o700)
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    initial = source_manifest(Path('src').resolve())
    error = None
    try:
        run(h, Path('src').resolve())
    except BaseException as exc:  # noqa: BLE001 - re-raised after the harness is closed
        error = exc
    finally:
        try:
            h.close()
        finally:
            # Concurrent development may edit sources mid-run. Report it without
            # masking the original failure and never leak the API subprocess.
            final = source_manifest(Path('src').resolve())
            if final != initial:
                if error is None:
                    error = RuntimeError(
                        'source changed during the acceptance run; the final acceptance run requires a stable checkpoint')
                else:
                    print('WARNING: src changed during the run; original exception retained', flush=True)
    if error is not None:
        raise error


if __name__ == '__main__':
    main()
