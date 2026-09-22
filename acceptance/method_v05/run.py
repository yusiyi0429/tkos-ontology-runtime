"""Real HTTP+PG acceptance of the tkos.method/0.5 ontology-alignment chain.

Happy path and the negative matrix both run through /v1/actions/prepare +
/v1/actions. SQL is used only for independent assertions and identity fixture
setup. This script does not prove real-model behaviour.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from acceptance.method_independent.harness import MethodHarness
from acceptance.method_v04.run import _change, candidate_refs
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import ROOT, register_v05, seed_v05
from .flow import Flow, period


def happy_path(h, f, flow):
    checks = []

    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)

    evidence = flow.upload()
    run_ref = flow.open_run()
    issue = flow.create_issue(run_ref, evidence)
    flow.set_participants(issue)
    agreement = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement)
    agreement = flow.ref(agreement['object_id'], effective=True)
    proposal = flow.propose_update(issue, agreement, _change(flow, rationale='Initial pair: no retained object exists yet.'))
    flow.review_update(proposal)
    strategy_ref, architecture_ref = flow.confirm_update(proposal)['result']['changed_refs']
    check('m1a_chain_unchanged_under_0_5',
          flow.object(strategy_ref['object_id'])['effective_revision_id'] == strategy_ref['revision_id'])

    # ------------------------------------------------------------ Constraint
    ltco_period, pco_period, mission_period = period(-30, 335), period(-1, 30), period(0, 15)
    scope_constraint = flow.record_constraint({'kind': 'scope', 'scope_id': 'scope-a'}, architecture_ref=architecture_ref,
                                              effective=pco_period)
    # 契约不授予 IC 读取范围 Constraint；运行时先授权、统一不披露，看不到即 NOT_FOUND——两种码都证明 IC 无法确认。
    flow.deny('owner_a', flow.command('m1b_confirm_constraint', {'statement': 'An IC tries to confirm.'},
                                      oid=scope_constraint['object_id']), codes={'FORBIDDEN', 'NOT_FOUND'})
    flow.deny('ceo', flow.command('m1b_confirm_constraint', {'statement': 'The CEO is not the scope DRI.'},
                                  oid=scope_constraint['object_id']), codes={'FORBIDDEN'})
    flow.confirm_constraint('dri_a', scope_constraint)
    scope_constraint = flow.ref(scope_constraint['object_id'], effective=True)
    check('scope_constraint_confirmed_by_its_dri_only',
          flow.object(scope_constraint['object_id'])['method_state']['phase'] == 'confirmed')
    company_constraint = flow.record_constraint({'kind': 'company'}, actor='ceo', effective=ltco_period,
                                                title='Synthetic company cash constraint')
    flow.confirm_constraint('ceo', company_constraint)
    company_constraint = flow.ref(company_constraint['object_id'], effective=True)
    check('company_constraint_confirmed_by_ceo', True)

    # ------------------------------------------------------------------ LTCO
    ltco_a = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-a', ltco_period,
                               constraints=[company_constraint, scope_constraint])
    ltco_b = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-b', ltco_period)
    flow.deny('ceo', flow.command('m1b_confirm_ltco', {'conclusion': 'maintained', 'statement': 'Cannot maintain a draft.'},
                                  oid=ltco_a['object_id']), codes={'INVALID_REQUEST'})
    flow.confirm_ltco(ltco_a)
    flow.confirm_ltco(ltco_b)
    ltco_a = flow.ref(ltco_a['object_id'], effective=True)
    ltco_b = flow.ref(ltco_b['object_id'], effective=True)
    maintained = flow.confirm_ltco(ltco_a, conclusion='maintained')
    check('maintained_review_keeps_the_effective_version_and_records_the_conclusion',
          maintained['conclusion'] == 'maintained'
          and flow.object(ltco_a['object_id'])['effective_revision_id'] == ltco_a['revision_id']
          and flow.object(ltco_a['object_id'])['method_state']['last_review']['conclusion'] == 'maintained')
    conf = flow.confirmations(ltco_a['object_id'])
    check('confirmations_projection_lists_both_ltco_decisions',
          [item['content']['conclusion'] for item in conf['items'] if item['kind'] == 'ltco_confirmation'] == ['established', 'maintained'])
    flow.deny('ceo_agent', flow.command('m1b_propose_ltco', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(ltco_b['object_id'])['latest_revision']['payload'], 'title': 'Cross-scope constraint',
        'constraint_refs': [scope_constraint]}}), codes={'INVALID_REQUEST'})
    check('ltco_cannot_reference_another_scopes_constraint', True)

    # ------------------------------------------------------ PCO / Mission
    pco_a = flow.draft_pco(ltco_a, 'scope-a', pco_period, constraints=[scope_constraint])
    pco_b = flow.draft_pco(ltco_b, 'scope-b', pco_period)
    mission_a = flow.draft_mission(pco_a, 'owner_a', 'scope-a', mission_period, evidence=[evidence],
                                   contributes=['scope-b'],
                                   dependencies=[{'kind': 'scope', 'scope_id': 'scope-b',
                                                  'needed_by': mission_period['end'], 'note': 'shared platform'}],
                                   constraints=[scope_constraint])
    mission_b = flow.draft_mission(pco_b, 'owner_b', 'scope-b', mission_period)
    flow.deny('co_agent', flow.command('m1b_draft_mission', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(mission_a['object_id'])['latest_revision']['payload'], 'contributes_to_scope_ids': ['scope-a']}}),
        codes={'INVALID_REQUEST'})
    check('mission_cannot_contribute_to_its_own_scope', True)

    # --------------------------------------- Mission Constraint（契约 §2 第三种确认人）
    # 任一确切 Mission 版本都可作 applies_to；在草稿期登记确认，Mission 才能在后续版本中引用"本 Mission 的"约束。
    mission_constraint = flow.record_constraint({'kind': 'mission', 'mission_ref': mission_a},
                                                effective=mission_period, title='Synthetic mission capacity constraint')
    recorded = flow.object(mission_constraint['object_id'])
    check('mission_constraint_recorded_against_the_exact_mission_version',
          recorded['latest_revision']['payload']['applies_to'] == {'kind': 'mission', 'mission_ref': mission_a}
          and recorded['method_state']['phase'] == 'draft' and recorded['effective_revision_id'] is None)
    # CEO 经公司域读得到该对象但不是确认人，只能是 FORBIDDEN；NOT_FOUND 意味着可见性回退，必须失败。
    flow.deny('ceo', flow.command('m1b_confirm_constraint', {'statement': 'The CEO is not the Mission Scope DRI.'},
                                  oid=mission_constraint['object_id']), codes={'FORBIDDEN'})
    flow.confirm_constraint('dri_a', mission_constraint)
    confirmed_constraint = flow.object(mission_constraint['object_id'])
    confirmers = [item['principal_id'] for item in flow.confirmations(mission_constraint['object_id'])['items']
                  if item['kind'] == 'constraint_confirmation']
    check('mission_constraint_confirmed_by_the_missions_scope_dri_only',
          confirmed_constraint['method_state']['phase'] == 'confirmed'
          and confirmed_constraint['effective_revision_id'] == mission_constraint['revision_id']
          and confirmers == [flow.principal('dri_a')])

    window = flow.open_window([pco_a, pco_b], [mission_a, mission_b], [ltco_a, ltco_b], pco_period,
                              names=('dri_a', 'dri_b', 'owner_a', 'owner_b'))
    opinion = flow.comment(window, pco_a, 'dri_a')
    flow.close_window(window)
    resolve_params = {'title': 'Synthetic 0.5 candidate set', 'pcos': [], 'missions': [],
                      'dispositions': [{'review_record_id': opinion, 'decision': 'adopted', 'rationale': 'Adopted.'}],
                      'unresolved_differences': [], 'summary': 'Retain both PCO/Mission results.'}
    for ref in (pco_a, pco_b):
        resolve_params['pcos'].append({'object_id': ref['object_id'],
                                       'payload': deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])})
    for ref in (mission_a, mission_b):
        payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
        payload.pop('parent_pco_ref')
        resolve_params['missions'].append({'object_id': ref['object_id'], **payload})
    candidate = flow.resolve_window(window, resolve_params)
    candidate = flow.ref(candidate['object_id'])
    by_object = {ref['object_id']: ref for ref in candidate_refs(h, f, candidate)}
    check('candidate_missions_keep_contributions_and_dependencies',
          flow.object(mission_a['object_id'])['latest_revision']['payload']['contributes_to_scope_ids'] == ['scope-b'])
    flow.deny('owner_a', flow.command('m1b_commit_candidate', {
        'responsibility_ref': by_object[mission_a['object_id']], 'statement': 'An Owner tries to commit a Mission.'},
        oid=candidate['object_id']), codes={'INVALID_REQUEST', 'FORBIDDEN'})
    check('mission_owner_commitment_rejected_in_0_5', True)
    flow.deny('ceo', flow.command('m1b_activate_candidates', {'statement': 'Activate before commitments.', 'notes': []},
                                  oid=candidate['object_id']), codes={'INVALID_STATE'})
    flow.commit_candidate(candidate, by_object[pco_a['object_id']], 'dri_a')
    flow.commit_candidate(candidate, by_object[pco_b['object_id']], 'dri_b')
    activation = flow.activate_candidates(candidate)
    check('two_dri_commitments_activate_the_whole_set',
          len(h.sql(f, "SELECT * FROM gov_method_commitments WHERE scope_id=%s", (f['scope_id'],))) == 2
          and activation['execution_authority_created'] is False)
    check('owner_activation_recorded_on_missions',
          flow.object(mission_a['object_id'])['method_state']['owner_activation_record_id'] == activation['review_record_id'])

    # ---------------------------------------------------------------- State
    pco_state = flow.propose_state(by_object[pco_a['object_id']], state_period=period(-30, 0))
    state_obj = flow.object(pco_state['object_id'])
    check('generated_state_is_canonical_without_confirmation',
          state_obj['method_state']['phase'] == 'recorded' and state_obj['method_state']['canonical_ref'] == pco_state
          and state_obj['effective_revision_id'] == pco_state['revision_id'])
    denied = flow.clients['dri_a'].json('POST', '/v1/actions/prepare', flow.command(
        'method_confirm_state', {'reason': 'There is no confirmation in 0.5.'}, oid=pco_state['object_id']),
        expected={400, 422})
    check('state_confirmation_is_not_a_0_5_action', denied['error']['code'] == 'INVALID_REQUEST')
    mission_state = flow.propose_state(by_object[mission_a['object_id']], state_period=period(-30, 0), rag='green',
                                       summary='Evidence supports progress', evidence=[evidence], data_gaps=())
    ltco_state = flow.propose_state(ltco_a, state_period=period(-30, 0), drilldown=[pco_state, mission_state])
    check('upper_state_drills_down_to_lower_canonical_states',
          flow.object(ltco_state['object_id'])['latest_revision']['payload']['drilldown_refs'] == [pco_state, mission_state])

    # -------------------------------------------------------- Period Review
    review = flow.act('co_agent', 'm1b_generate_review', {'domain_id': f['domains']['company'], 'payload': {
        'review_id': 'synthetic-0-5-review', 'title': 'Synthetic 0.5 period review', 'period': period(-30, 0),
        'target_refs': [by_object[pco_a['object_id']], by_object[mission_a['object_id']]],
        'state_refs': [pco_state, mission_state], 'fact_refs': [],
        'findings': ['Canonical states are the reviewed basis.'], 'learnings': [], 'implications': [],
        'generation_version': 'controlled-review-1'}})['result']
    review = {k: review[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    generated = flow.object(review['object_id'])
    check('generated_review_is_not_effective_before_ceo_confirmation',
          generated['effective_revision_id'] is None and generated['method_state']['phase'] == 'generated')
    next_period = period(1, 31)
    flow.deny('co_agent', flow.command('m1b_draft_pco', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(pco_a['object_id'])['latest_revision']['payload'], 'period': next_period,
        'period_review_ref': review, 'title': 'Cites an unconfirmed review'}}), codes={'STALE_DEPENDENCY'})
    confirmed = flow.confirm_review(review, findings=['CEO-adjusted finding.'])
    review = {k: confirmed[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    review_obj = flow.object(review['object_id'])
    check('ceo_confirmation_makes_the_review_effective_with_overrides',
          review_obj['method_state']['phase'] == 'confirmed' and review_obj['effective_revision_id'] == review['revision_id']
          and review_obj['latest_revision']['payload']['findings'] == ['CEO-adjusted finding.']
          and review_obj['method_state']['agent_generation_ref']['revision_id'] != review['revision_id'])
    flow.deny('co_agent', flow.command('m1b_draft_pco', {'domain_id': f['domains']['company'], 'payload': {
        **flow.object(pco_a['object_id'])['latest_revision']['payload'], 'period': next_period,
        'period_review_ref': None, 'title': 'Omits the confirmed review'}}), codes={'INVALID_REQUEST'})
    next_pco = flow.draft_pco(ltco_a, 'scope-a', next_period, period_review_ref=review, title='Next period PCO')
    check('next_pco_cites_the_confirmed_review',
          flow.object(next_pco['object_id'])['latest_revision']['payload']['period_review_ref'] == review)

    # ----------------------------------------------------------------- reads
    view = flow.company_view(pco_period)
    scope_a = next(s for s in view['scopes'] if s['scope_id'] == 'scope-a')
    check('company_view_groups_by_scope',
          scope_a['ltco']['object_id'] == ltco_a['object_id']
          and [p['object_id'] for p in scope_a['pcos']] == [pco_a['object_id']]
          and scope_a['missions'][0]['owner_effective_from'] is not None
          and [c['object_id'] for c in scope_a['constraints']] == [scope_constraint['object_id']]
          and [c['object_id'] for c in view['company_constraints']] == [company_constraint['object_id']])
    reviews = flow.clients['ceo'].json('GET', f"/v1/method/objects/{ltco_a['object_id']}/reviews")
    check('review_records_carry_effects',
          {item['effect'] for item in reviews['items']} == {'decision'})
    # 真实 0.5 对象的读取必须带 0.5 解释状态（protocol.require_read_support 白名单含 method_v0_5）。
    read_objects = [scope_constraint, company_constraint, ltco_a, by_object[pco_a['object_id']],
                    by_object[mission_a['object_id']], pco_state, review, next_pco]
    check('object_reads_carry_method_v0_5_interpretation',
          all(flow.object(ref['object_id'])['protocol']['interpretation_status'] == 'method_v0_5'
              and flow.object(ref['object_id'])['protocol']['contract_version'] == 'tkos.method/0.5'
              for ref in read_objects))
    # 本 scope 经 HTTP 产生的每个对象都有一条 0.5 绑定行，钉定 0.5 profile 身份；这些行是真实 INSERT，
    # 且绑定表上生效的 trg_gov_binding_insert_gate 就是 0029 版（含 0.5 分支与契约、本体登记两个 SHA）。
    profile = json.loads((ROOT / 'docs/contracts/method-profile-0.5.json').read_text())
    bindings = h.sql(f, """SELECT b.object_id, o.object_type, b.binding_version, b.protocol_id, b.contract_version,
                                  b.profile_id, b.profile_revision, b.profile_canonical_hash
                           FROM gov_object_protocol_bindings b
                           JOIN gov_objects o ON (o.scope_id, o.object_id) = (b.scope_id, b.object_id)
                           WHERE b.scope_id=%s""", (f['scope_id'],))
    objects = h.sql(f, "SELECT object_id FROM gov_objects WHERE scope_id=%s", (f['scope_id'],))
    gate = h.sql(f, """SELECT t.tgenabled, p.prosrc FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
                       WHERE t.tgrelid = 'gov_object_protocol_bindings'::regclass
                         AND t.tgname = 'trg_gov_binding_insert_gate'""")
    pinned = (1, 'tkos.method', 'tkos.method/0.5', profile['profile_id'], profile['revision'], profile['canonical_hash'])
    check('real_0_5_binding_rows_passed_the_0029_insert_gate',
          len(gate) == 1 and gate[0]['tgenabled'] == 'O'
          and "'tkos.method/0.5'" in gate[0]['prosrc']
          and profile['action_contract_ref']['content_sha256'] in gate[0]['prosrc']
          and profile['ontology_registry_ref']['content_sha256'] in gate[0]['prosrc']
          and bindings and {str(b['object_id']) for b in bindings} == {str(o['object_id']) for o in objects}
          and all((b['binding_version'], b['protocol_id'], b['contract_version'], b['profile_id'],
                   b['profile_revision'], b['profile_canonical_hash']) == pinned for b in bindings)
          and {'Constraint', 'LTCO', 'PCO', 'Mission', 'OperatingState', 'PeriodReview', 'CandidateSet'}
          <= {b['object_type'] for b in bindings})
    check('no_execution_or_acceptance_side_effects',
          not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))
    return {'checks': checks}


def run(h: MethodHarness, source: Path):
    f = seed_v05(h.env, h.private / 'identities.json', 'runtime-acceptance-method-v05')
    register_v05(h, source, f)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        ctx = happy_path(h, f, flow)
        public_json(h.output / 'summary.json', {
            'happy_path_passed': True, 'happy_path_checks': ctx['checks'],
            'runtime_method_v05_api_accepted': True,
            'scope': 'Synthetic 0.5 HTTP/PG chain; controlled Agent inputs, not real-model acceptance',
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
            final = source_manifest(Path('src').resolve())
            if final != initial:
                if error is None:
                    error = RuntimeError('source changed during the acceptance run; rerun on a stable checkpoint')
                else:
                    print('WARNING: src changed during the run; original exception retained', flush=True)
    if error is not None:
        raise error


if __name__ == '__main__':
    main()
