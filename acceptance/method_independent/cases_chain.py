"""Independent M1A/M1B success and history assertions from real HTTP outputs."""
from copy import deepcopy

from .flow import exact


def payload(flow, ref):
    return flow.clients['ceo'].revision(ref['object_id'], ref['revision_id'])['payload']


def check(book, case, name, evidence):
    book.check(case, name, True, evidence=evidence)


def mixed_role_facts(flow, period):
    """The caller's company role cannot replace its domain responsibility."""
    rows = flow.h.sql(flow.f, '''SELECT domain_id,role FROM gov_role_assignments
        WHERE scope_id=%s AND principal_id=%s AND active''',
        (flow.f['scope_id'], flow.f['actors']['mixed_role']['principal_id']))
    roles = {name: sorted(r['role'] for r in rows if str(r['domain_id']) == domain)
             for name, domain in flow.f['domains'].items()}
    assert roles['company'] == ['CEO'] and roles['a'] == ['DOMAIN_DRI'] and roles['b'] == ['IC']
    source = flow.upload(actor='mixed_role', domain='a', text='Synthetic domain a observed pilot count: 4')
    original_payload = {
        'fact_id': 'synthetic-domain-a-mixed-role-v1', 'subject_ref': {'topic': 'domain-a-pilot-evidence'},
        'as_of': period['end'], 'metric': 'verified_pilots', 'value': 4, 'unit': 'customer',
        'source_ref': source,
    }
    recorded = flow.act('mixed_role', 'm1b_record_fact', {
        'domain_id': flow.f['domains']['a'], 'payload': original_payload})
    fact = exact(recorded['result'])
    revised_source = flow.upload(actor='mixed_role', domain='a', text='Synthetic corrected domain a pilot count: 5')
    revised_payload = {**original_payload, 'fact_id': 'synthetic-domain-a-mixed-role-v2',
                       'value': 5, 'source_ref': revised_source, 'corrects_ref': fact,
                       'correction_reason': 'A second original source adds one verified pilot.'}
    corrected = flow.act('mixed_role', 'm1b_correct_fact', {'payload': revised_payload}, oid=fact['object_id'])
    correction = exact(corrected['result'])
    for ref, expected in [(fact, original_payload), (correction, revised_payload)]:
        actual = payload(flow, ref)
        assert all(actual[key] == value for key, value in expected.items())
    assert correction['object_id'] != fact['object_id']
    forbidden = flow.command('m1b_record_fact', {'domain_id': flow.f['domains']['b'],
        'payload': {**original_payload, 'fact_id': 'synthetic-unauthorized-domain-b'}}, actor='mixed_role')
    denied = flow.deny('mixed_role', forbidden, codes={'FORBIDDEN'})
    return {'observed_roles': roles, 'domain_a_fact': fact, 'domain_a_correction': correction,
            'record_receipt_id': recorded['receipt_id'], 'correction_receipt_id': corrected['receipt_id'],
            'original_fact_preserved': True, 'domain_b_ic_rejected': denied}


def m1a(flow, book):
    chain = flow.strategy(complete=True)
    assert len(chain['signals']) == 2
    for signal in chain['signals']:
        assert payload(flow, signal)['source_refs'] == [chain['evidence']]
    check(book, 'METHOD-01', 'multi_source_signals_traceable', chain['signals'])
    assert chain['original_potential']['revision_id'] != chain['potential']['revision_id']
    assert payload(flow, chain['potential'])['signal_refs'] == chain['signals']
    assert payload(flow, chain['original_potential'])['summary'] != payload(flow, chain['potential'])['summary']
    check(book, 'METHOD-01', 'potential_issue_revisions_preserved', [chain['original_potential'], chain['potential']])
    issue = payload(flow, chain['issue'])
    assert issue['potential_issue_ref'] == chain['potential']
    records = flow.rows('gov_method_reviews')
    kinds = {}
    for row in records:
        kinds.setdefault(row['kind'], []).append(row)
    assert len(kinds['research_assignment']) == 1
    assignment = kinds['research_assignment'][0]
    assert str(assignment['principal_id']) == flow.f['actors']['ceo']['principal_id']
    assert assignment['content']['dri_principal_id'] == flow.f['actors']['a']['principal_id']
    check(book, 'METHOD-01', 'research_dri_explicitly_assigned', {'review_record_id': str(assignment['record_id'])})
    assert payload(flow, chain['memo'])['source_refs'] == [chain['evidence']]
    assert payload(flow, chain['memo'])['issue_ref'] == chain['issue']
    check(book, 'METHOD-02', 'memo_exact_version_and_sources', chain['memo'])
    clarifications = kinds['record_clarification']
    assert len(clarifications) == 2
    assert all(str(row['principal_id']) == flow.f['actors']['dri_agent']['principal_id'] for row in clarifications)
    check(book, 'METHOD-02', 'personal_agent_clarification', [str(r['record_id']) for r in clarifications])
    current_checks = [r for r in kinds['check_memo'] if str(r['target_revision_id']) == chain['memo']['revision_id']]
    assert {str(r['principal_id']) for r in current_checks} == {flow.f['actors'][a]['principal_id'] for a in ['ceo_agent', 'dri_agent']}
    assert all(r['content']['result'] == 'clear' for r in current_checks)
    check(book, 'METHOD-02', 'two_agent_crosscheck', [str(r['record_id']) for r in current_checks])
    assert chain['memo']['revision_id'] != chain['original_memo']['revision_id']
    assert len(kinds['direct_clarification']) == 1
    assert str(kinds['direct_clarification'][0]['principal_id']) == flow.f['actors']['a']['principal_id']
    check(book, 'METHOD-02', 'second_clarification_and_direct_human_resolution', {'memo_versions': [chain['original_memo'], chain['memo']]})
    plan = payload(flow, chain['plan'])
    assert plan['memo_ref'] == chain['memo'] and plan['human_work'] and plan['agent_work']
    plan_receipt = next(r for r in flow.commands if r['request']['action_type'] == 'm1a_publish_research_plan')
    assert plan_receipt['actor'] == 'a'
    check(book, 'METHOD-02', 'research_plan_published_by_designated_dri', chain['plan'])
    assert chain['original_report']['revision_id'] != chain['report']['revision_id']
    prechecks = kinds['research_quality_precheck']
    assert [r['content']['result'] for r in sorted(prechecks, key=lambda r: r['recorded_at'])] == ['return', 'pass']
    assert {str(r['target_revision_id']) for r in prechecks} == {chain['original_report']['revision_id'], chain['report']['revision_id']}
    check(book, 'METHOD-03', 'report_return_and_resubmission', [chain['original_report'], chain['report']])
    assert all(str(r['principal_id']) == flow.f['actors']['ceo_agent']['principal_id'] for r in prechecks)
    check(book, 'METHOD-03', 'ceo_agent_quality_gate', [str(r['record_id']) for r in prechecks])
    meetings = chain['meetings']
    assert [payload(flow, r['meeting'])['round_number'] for r in meetings] == [1, 2]
    assert all(chain['evidence'] in payload(flow, r['meeting'])['material_refs'] for r in meetings)
    check(book, 'METHOD-04', 'meeting_round_materials_and_raw_evidence', [r['meeting'] for r in meetings])
    for meeting in meetings:
        assert payload(flow, meeting['minutes']['ceo'])['body'] != payload(flow, meeting['minutes']['dri'])['body']
        assert len(payload(flow, meeting['final_minutes'])['differences']) == 1
        assert payload(flow, meeting['final_minutes'])['source_refs'] == list(meeting['minutes'].values())
    check(book, 'METHOD-04', 'two_minutes_versions_and_difference_record', [r['final_minutes'] for r in meetings])
    minute_decisions = kinds['meeting_minutes_confirmation']
    assert len(minute_decisions) == 2 and all(str(r['principal_id']) == flow.f['actors']['a']['principal_id'] for r in minute_decisions)
    assert {str(r['target_revision_id']) for r in minute_decisions} == {r['final_minutes']['revision_id'] for r in meetings}
    check(book, 'METHOD-04', 'dri_confirms_exact_final_minutes', [str(r['record_id']) for r in minute_decisions])
    agreement_decisions = kinds['strategic_agreement_confirmation']
    assert len(agreement_decisions) == 2 and all(str(r['principal_id']) == flow.f['actors']['ceo']['principal_id'] for r in agreement_decisions)
    assert {str(r['record_id']) for r in minute_decisions}.isdisjoint(str(r['record_id']) for r in agreement_decisions)
    check(book, 'METHOD-04', 'minutes_confirmation_is_not_agreement_confirmation', {'minutes': len(minute_decisions), 'agreements': len(agreement_decisions)})
    assert payload(flow, meetings[0]['agreement'])['meeting_goal_achieved'] is False
    assert payload(flow, meetings[1]['agreement'])['meeting_goal_achieved'] is True
    check(book, 'METHOD-04', 'unmet_goal_requires_another_meeting', [r['agreement'] for r in meetings])
    check(book, 'METHOD-04', 'ceo_confirms_agreement_separately', [str(r['record_id']) for r in agreement_decisions])
    for kind, actor, name in [
        ('strategy_adjustment_decision', 'ceo', 'ceo_decides_adjustment'),
        ('strategy_update_impact_review', 'co_agent', 'co_agent_reviews_impact_and_plan'),
        ('strategy_update_confirmation', 'ceo', 'ceo_confirms_formal_update'),
    ]:
        assert len(kinds[kind]) == 1 and str(kinds[kind][0]['principal_id']) == flow.f['actors'][actor]['principal_id']
        check(book, 'METHOD-05', name, {'review_record_id': str(kinds[kind][0]['record_id'])})
    proposal = payload(flow, chain['proposal'])
    assert proposal['agreement_ref'] == chain['agreement']
    proposal_command = next(r for r in flow.commands if r['request']['action_type'] == 'm1a_propose_update')
    assert proposal_command['actor'] == 'ceo_agent'
    check(book, 'METHOD-05', 'ceo_agent_drafts_exact_target_proposal', chain['proposal'])
    strategy = flow.object(chain['strategy']['object_id'])
    assert strategy['effective_revision_id'] == chain['strategy']['revision_id']
    assert {u['unit_id'] for u in strategy['effective_revision']['payload']['map']['units']} == {'unit-pilot', 'unit-delivery'}
    head = flow.rows('gov_method_strategy_heads')
    assert len(head) == 1 and str(head[0]['revision_id']) == chain['strategy']['revision_id']
    check(book, 'METHOD-05', 'strategy_and_stable_map_units_effective_atomically', chain['strategy'])
    assert strategy['effective_revision']['payload']['source_agreement_ref'] == chain['agreement']
    assert strategy['effective_revision']['payload']['source_proposal_ref'] == chain['proposal']
    assert str(head[0]['action_id']) == flow.receipts[-1]['receipt_id']
    check(book, 'METHOD-05', 'agreement_proposal_and_confirmation_provenance', {'strategy': chain['strategy'], 'receipt_id': str(head[0]['action_id'])})
    return chain


def m1b(flow, book, strategy):
    old_ltco = flow.ltco(strategy)
    prior = flow.targets(strategy, old_ltco['ltco'], previous=True)
    fact1 = flow.fact(prior)
    review1 = flow.review(prior, [fact1])
    fact2 = flow.fact(prior, corrects=fact1, value=3)
    review2 = flow.review(prior, [fact1, fact2], previous=review1)
    assert payload(flow, fact1)['value'] == 2 and payload(flow, fact2)['value'] == 3
    assert payload(flow, fact2)['corrects_ref'] == fact1
    mixed_role = mixed_role_facts(flow, prior['period'])
    check(book, 'METHOD-07', 'atomic_business_fact_fields_and_raw_source',
          {'fact': fact1, 'multiple_domain_responsibilities': mixed_role})
    check(book, 'METHOD-07', 'fact_correction_preserves_original', [fact1, fact2])
    assert payload(flow, review1)['target_refs'] == [prior['pco'], prior['mission']]
    assert payload(flow, review1)['fact_refs'] == [fact1]
    check(book, 'METHOD-07', 'period_review_records_exact_targets_facts_and_generation', review1)
    assert review1['object_id'] == review2['object_id'] and review1['revision_id'] != review2['revision_id']
    assert payload(flow, review2)['generation_version'] != payload(flow, review1)['generation_version']
    check(book, 'METHOD-07', 'period_review_regeneration_preserves_history', [review1, review2])
    assert flow.object(review2['object_id'])['lifecycle_status'] == 'recorded'
    assert not any('review' in row['kind'] and ('approval' in row['kind'] or 'confirmation' in row['kind']) for row in flow.rows('gov_method_reviews'))
    check(book, 'METHOD-07', 'review_has_no_confirmation_or_approval_gate', review2)
    advice = flow.advice(review2, strategy, old_ltco['ltco'])
    assert payload(flow, advice)['period_review_ref'] == review2
    assert flow.object(prior['pco']['object_id'])['effective_revision_id'] == prior['pco']['revision_id']
    assert flow.rows('gov_execution_authorities') == []
    check(book, 'METHOD-07', 'review_usable_as_analysis_without_changing_targets_or_authority', advice)
    new_ltco = flow.ltco(strategy, advice=advice, returned=True)
    assert new_ltco['ltco']['revision_id'] != new_ltco['original_ltco']['revision_id']
    ltco_records = [r for r in flow.rows('gov_method_reviews') if str(r['target_object_id']) == new_ltco['ltco']['object_id']]
    assert {'ltco_feedback', 'ltco_revision_response', 'ltco_confirmation'} <= {r['kind'] for r in ltco_records}
    check(book, 'METHOD-08', 'ltco_advice_and_ceo_return_feedback', advice)
    feedback = next(r for r in ltco_records if r['kind'] == 'ltco_feedback')
    confirmed = next(r for r in ltco_records if r['kind'] == 'ltco_confirmation')
    assert str(feedback['target_revision_id']) == new_ltco['original_ltco']['revision_id']
    assert str(confirmed['target_revision_id']) == new_ltco['ltco']['revision_id']
    check(book, 'METHOD-08', 'feedback_revision_rechecked', [str(feedback['record_id']), str(confirmed['record_id'])])
    assert flow.object(new_ltco['ltco']['object_id'])['effective_revision_id'] == new_ltco['ltco']['revision_id']
    check(book, 'METHOD-08', 'ceo_confirms_exact_ltco', new_ltco['ltco'])
    assert payload(flow, new_ltco['ltco'])['strategy_ref'] == strategy
    check(book, 'METHOD-08', 'current_effective_strategy_is_explicit_baseline', strategy)
    targets = flow.draft_targets(strategy, new_ltco['ltco'])
    original_targets = [deepcopy(targets['pco']), deepcopy(targets['mission'])]
    flow.open_window(targets)
    assert payload(flow, targets['window'])['target_refs'] == original_targets
    check(book, 'METHOD-09', 'pco_and_mission_drafts_form_exact_window_set', targets['window'])
    assert payload(flow, targets['window'])['participants'] == flow.participants()
    check(book, 'METHOD-09', 'window_membership_is_explicit', flow.participants())
    comment1 = flow.comment(targets)
    replaced = flow.comment(targets, replaces=comment1)
    withdrawn = flow.comment(targets, actor='b')
    flow.act('b', 'm1b_withdraw_comment', {'review_record_id': withdrawn, 'reason': 'Withdraw after independent source review.'}, oid=targets['window']['object_id'])
    flow.act('dri_agent', 'm1b_assist_review', {'target_ref': targets['pco'], 'owner_principal_id': flow.f['actors']['a']['principal_id'],
        'analysis': 'Evidence requirements are clear', 'source_refs': [], 'generation_version': 'controlled-agent-v1'}, oid=targets['window']['object_id'])
    for ref in original_targets:
        assert flow.ref(ref['object_id']) == ref
    check(book, 'METHOD-13', 'collaboration_comments_do_not_revise_business_body', original_targets)
    comments = [r for r in flow.rows('gov_method_reviews') if str(r['window_id']) == targets['window']['object_id']]
    assert len([r for r in comments if r['kind'] == 'window_comment']) == 3
    assert any(str(r['record_id']) == comment1 and str(r['principal_id']) == flow.f['actors']['a']['principal_id'] for r in comments)
    check(book, 'METHOD-09', 'participant_own_comment', comment1)
    assert any(r['kind'] == 'window_opinion_withdrawal' and r['content']['withdrawn_record_id'] == withdrawn for r in comments)
    check(book, 'METHOD-09', 'withdraw_retains_history', withdrawn)
    assist = next(r for r in comments if r['kind'] == 'personal_agent_analysis')
    assert str(assist['principal_id']) == flow.f['actors']['dri_agent']['principal_id']
    check(book, 'METHOD-09', 'personal_agent_analysis_is_not_human_comment', str(assist['record_id']))
    flow.close_window(targets)
    assert targets['frozen_opinion_ids'] == [replaced]
    check(book, 'METHOD-09', 'replacement_retains_original_and_single_effective_opinion', [comment1, replaced])
    check(book, 'METHOD-10', 'close_freezes_effective_opinions', [replaced])
    flow.resolve(targets)
    candidate1 = deepcopy(targets['candidate'])
    candidate_payload = payload(flow, candidate1)
    assert candidate_payload['dispositions'][0]['review_record_id'] == replaced
    check(book, 'METHOD-10', 'resolution_records_choices_and_candidates', candidate1)
    reopened = flow.act('ceo', 'm1b_reopen_candidates', {'reason': 'Request one final joint verification.', 'title': 'Synthetic final review'}, oid=candidate1['object_id'])['result']
    targets['window'] = {k: reopened[k] for k in ('object_id', 'revision_id', 'payload_hash')}
    assert payload(flow, targets['window'])['target_refs'] == candidate_payload['target_refs']
    check(book, 'METHOD-10', 'ceo_reopen_requires_reason_and_exact_candidate_baseline', targets['window'])
    flow.close_window(targets)
    flow.resolve(targets)
    flow.confirm_candidates(targets)
    assert all(flow.object(ref['object_id'])['effective_revision_id'] == ref['revision_id'] for ref in [targets['pco'], targets['mission']])
    check(book, 'METHOD-10', 'ceo_confirms_entire_candidate_set_atomically', targets['confirmation']['receipt_id'])
    assert flow.rows('gov_handshakes') == []
    check(book, 'METHOD-10', 'new_confirmation_has_no_old_all_dri_signature_gate', {'handshakes': 0})
    mission = payload(flow, targets['mission'])
    assert len(mission['supports']) == 2 and {s['outcome_ref']['outcome_id'] for s in mission['supports']} == {'outcome-pilot', 'outcome-quality'}
    check(book, 'METHOD-11', 'mission_single_owner_supports_two_outcomes', targets['mission'])
    pco = payload(flow, targets['pco'])
    assert mission['owner_principal_id'] == pco['unit_outcomes'][0]['dri_principal_id']
    check(book, 'METHOD-11', 'owner_may_equal_target_dri', mission['owner_principal_id'])
    assert 'supports' not in pco and all('supports' not in outcome for outcome in pco['unit_outcomes'])
    check(book, 'METHOD-11', 'support_edges_exist_only_on_mission', targets['pco'])
    assert all('fact_refs' not in payload(flow, ref) for ref in [new_ltco['ltco'], targets['pco'], targets['mission']])
    check(book, 'METHOD-08', 'business_facts_not_copied_into_target_body', [new_ltco['ltco'], targets['pco'], targets['mission']])
    assert pco['strategy_ref'] == strategy
    check(book, 'METHOD-12', 'm1b_consumes_m1a_effective_strategy_exact_revision', strategy)
    return {'prior': prior, 'fact1': fact1, 'fact2': fact2, 'review1': review1, 'review2': review2,
            'mixed_role_facts': mixed_role,
            'advice': advice, 'ltco': new_ltco, 'targets': targets, 'old_candidate': candidate1}
