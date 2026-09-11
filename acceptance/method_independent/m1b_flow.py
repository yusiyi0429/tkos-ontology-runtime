"""M1B public API driver; bootstrap uses the same legal business actions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .flow import Flow, exact


class MethodFlow(Flow):
    def periods(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return ({'start': (now - timedelta(days=90)).isoformat(), 'end': (now + timedelta(days=90)).isoformat()},
                {'start': (now - timedelta(days=31)).isoformat(), 'end': (now - timedelta(days=1)).isoformat()},
                {'start': now.isoformat(), 'end': (now + timedelta(days=30)).isoformat()})

    def ltco(self, strategy, *, advice=None, returned=False):
        long_period, _, _ = self.periods()
        payload = {'title': 'Synthetic six month LTCO', 'period': long_period, 'strategy_ref': strategy,
                   'owner_principal_id': self.f['actors']['ceo']['principal_id'],
                   'outcomes': [{'outcome_id': 'ltco-value', 'unit_id': 'unit-pilot', 'title': 'Verified pilot value',
                       'result_statement': 'Demonstrate value with independently traceable evidence', 'criteria': ['Evidence is verifiable']}]}
        if advice:
            payload['advice_ref'] = advice
        reference = exact(self.act('ceo_agent', 'm1b_propose_ltco', {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])
        original = deepcopy(reference)
        if returned:
            self.act('ceo', 'm1b_return_ltco', {'reason': 'Clarify the expected business result.'}, oid=reference['object_id'])
            payload['outcomes'][0]['result_statement'] = 'Three verifiable pilot cases demonstrate reusable value'
            reference = exact(self.act('ceo_agent', 'm1b_revise_ltco', {'payload': payload, 'response': 'Made the expected result concrete.'}, oid=reference['object_id'])['result'])
        confirmed = self.act('ceo', 'm1b_confirm_ltco', {'reason': 'Personally confirm this exact six month target.'}, oid=reference['object_id'])
        return {'ltco': reference, 'original_ltco': original, 'payload': payload, 'confirmation': confirmed}

    def draft_targets(self, strategy, ltco, *, previous=False):
        _, previous_period, next_period = self.periods()
        period = previous_period if previous else next_period
        pco_payload = {'title': 'Synthetic monthly PCO', 'period': period, 'strategy_ref': strategy, 'ltco_ref': ltco,
            'unit_outcomes': [
                {'outcome_id': 'outcome-pilot', 'unit_id': 'unit-pilot', 'title': 'Pilot evidence',
                 'result_statement': 'Three distinct pilots with original evidence', 'criteria': ['Three verified pilots'],
                 'dri_principal_id': self.f['actors']['a']['principal_id']},
                {'outcome_id': 'outcome-quality', 'unit_id': 'unit-delivery', 'title': 'Evidence quality',
                 'result_statement': 'All pilot claims have traceable source evidence', 'criteria': ['Every claim has a source'],
                 'dri_principal_id': self.f['actors']['b']['principal_id']},
            ]}
        pco = exact(self.act('co_agent', 'm1b_draft_pco', {'domain_id': self.f['domains']['company'], 'payload': pco_payload})['result'])
        mission_payload = {'title': 'Synthetic pilot mission', 'pco_ref': pco,
            'owner_principal_id': self.f['actors']['a']['principal_id'],
            'participants': [self.f['actors']['b']['principal_id']],
            'supports': [{'outcome_ref': {**pco, 'outcome_id': outcome}, 'contribution': 'Collect and verify pilot evidence'}
                         for outcome in ['outcome-pilot', 'outcome-quality']],
            'deliverable': 'Traceable pilot evidence report', 'acceptance_criteria': ['Original sources attached'],
            'boundary': 'No automatic execution authorization', 'hard_deadline': period['end']}
        mission = exact(self.act('co_agent', 'm1b_draft_mission', {'domain_id': self.f['domains']['company'], 'payload': mission_payload})['result'])
        return {'pco': pco, 'mission': mission, 'pco_payload': pco_payload,
                'mission_payload': mission_payload, 'period': period, 'strategy': strategy, 'ltco': ltco}

    def participants(self):
        return [{'principal_id': self.f['actors'][actor]['principal_id'],
                 'assignment_id': self.f['actors'][actor]['assignment_id'],
                 'personal_agent_id': self.f['actors'][agent]['principal_id']}
                for actor, agent in [('a', 'dri_agent'), ('b', 'b_agent')]]

    def open_window(self, targets):
        payload = {'title': 'Synthetic shared review window', 'period': targets['period'],
                   'strategy_ref': targets['strategy'], 'ltco_ref': targets['ltco'],
                   'target_refs': [targets['pco'], targets['mission']], 'participants': self.participants()}
        targets['window'] = exact(self.act('co_agent', 'm1b_open_window', {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])
        return targets

    def comment(self, targets, actor='a', *, replaces=None):
        params = {'target_ref': targets['pco'], 'content': 'Synthetic participant asks for explicit evidence quality.'}
        if replaces:
            params['replaces_record_id'] = replaces
            params['content'] = 'Revised opinion: retain target and clarify source checks.'
        return self.act(actor, 'm1b_comment', params, oid=targets['window']['object_id'])['result']['review_record_id']

    def close_window(self, targets):
        result = self.act('co_agent', 'm1b_close_window', {'reason': 'The current review period is complete.'}, oid=targets['window']['object_id'])['result']
        targets['frozen_opinion_ids'] = result['frozen_opinion_ids']
        return targets

    def resolution_params(self, targets):
        mission = deepcopy(targets['mission_payload'])
        mission['object_id'] = targets['mission']['object_id']
        mission.pop('pco_ref')
        mission['supports'] = [{'outcome_id': s['outcome_ref']['outcome_id'], 'contribution': s['contribution']} for s in mission['supports']]
        return {'title': 'Synthetic candidate set', 'pco_payload': deepcopy(targets['pco_payload']),
                'missions': [mission], 'dispositions': [{'review_record_id': rid, 'decision': 'adopted', 'rationale': 'Clarified evidence quality'}
                                                      for rid in targets.get('frozen_opinion_ids', [])],
                'remaining_differences': [], 'summary': 'Retain targets and make evidence expectations explicit.'}

    def resolve(self, targets):
        result = self.act('co_agent', 'm1b_resolve_window', self.resolution_params(targets), oid=targets['window']['object_id'])['result']
        targets['candidate'] = exact(result)
        by_id = {ref['object_id']: ref for ref in result['target_refs']}
        targets['pco'] = by_id[targets['pco']['object_id']]
        targets['mission'] = by_id[targets['mission']['object_id']]
        targets['mission_payload'] = self.object(targets['mission']['object_id'])['latest_revision']['payload']
        return targets

    def confirm_candidates(self, targets):
        targets['confirmation'] = self.act('ceo', 'm1b_confirm_candidates', {'reason': 'Personally confirm the complete exact candidate set.'}, oid=targets['candidate']['object_id'])
        return targets

    def targets(self, strategy, ltco, *, previous=False, confirm=True):
        targets = self.draft_targets(strategy, ltco, previous=previous)
        self.open_window(targets)
        self.close_window(targets)
        self.resolve(targets)
        if confirm:
            self.confirm_candidates(targets)
        return targets

    def fact(self, targets, *, corrects=None, value=2):
        evidence = self.upload(text='Synthetic observed distinct pilot count: ' + str(value))
        payload = {'fact_id': 'synthetic-pilot-count-' + str(value), 'subject_ref': {**targets['pco'], 'outcome_id': 'outcome-pilot'},
                   'as_of': targets['period']['end'], 'metric': 'distinct_pilots', 'value': value,
                   'unit': 'customer', 'source_ref': evidence}
        if corrects:
            payload.update(corrects_ref=corrects, correction_reason='Original evidence was supplemented.')
            result = self.act('co_agent', 'm1b_correct_fact', {'payload': payload}, oid=corrects['object_id'])['result']
        else:
            result = self.act('co_agent', 'm1b_record_fact', {'domain_id': self.f['domains']['company'], 'payload': payload})['result']
        return exact(result)

    def review(self, targets, facts, *, previous=None):
        payload = {'review_id': 'synthetic-period-review', 'title': 'Synthetic period review',
                   'period': targets['period'], 'target_refs': [targets['pco'], targets['mission']], 'fact_refs': facts,
                   'findings': ['Pilot evidence has been recorded'], 'learnings': ['Verify source identity before counting'],
                   'implications': ['Make the next target evidence requirements explicit'],
                   'generation_version': 'controlled-agent-v2' if previous else 'controlled-agent-v1'}
        if previous:
            result = self.act('co_agent', 'm1b_regenerate_review', {'payload': payload}, oid=previous['object_id'])['result']
        else:
            result = self.act('co_agent', 'm1b_generate_review', {'domain_id': self.f['domains']['company'], 'payload': payload})['result']
        return exact(result)

    def advice(self, review, strategy, ltco):
        return exact(self.act('co_agent', 'm1b_advise_ltco', {'domain_id': self.f['domains']['company'], 'payload': {
            'title': 'Synthetic LTCO review advice', 'period_review_ref': review, 'strategy_ref': strategy,
            'ltco_ref': ltco, 'recommendation': 'revise', 'observations': ['Clarify next cycle evidence requirements'],
            'generation_version': 'controlled-agent-v1',
        }})['result'])
