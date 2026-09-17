"""Public HTTP driver for the 0.4 formal governance chain."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from acceptance.method_independent.flow import Flow as BaseFlow, exact, uid
from acceptance.runtime.client import Client


def period(start_days: int, end_days: int) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return {'start': (now + timedelta(days=start_days)).isoformat(),
            'end': (now + timedelta(days=end_days)).isoformat()}


class Flow(BaseFlow):
    PERSONAL_AGENTS = {
        'ceo': 'ceo_agent', 'dri_a': 'agent_a', 'dri_b': 'agent_b',
        'owner_a': 'owner_agent_a', 'a': 'dri_agent', 'b': 'b_agent',
    }

    def command(self, kind, params, *, oid=None, actor='ceo', key=None, run=None):
        body = Client.command(kind, deepcopy(params), target=self.target(oid, actor) if oid else None, key=key)
        body['contract_version'] = 'tkos.method/0.4'
        if run is not None:
            body['run_ref'] = run
        return body

    def prepare_run(self, actor, kind, params, *, oid=None, run=None, key=None):
        body = self.command(kind, params, oid=oid, actor=actor, key=key, run=run)
        result = self.clients[actor].json('POST', '/v1/actions/prepare', body)
        body['expected_versions'] = result['expected_versions']
        if result['target']:
            assert result['target']['revision_id'] == body['target']['revision_id']
            body['target']['expected_version'] = result['target']['expected_version']
        return body

    def act_run(self, actor, kind, params, *, oid=None, run=None):
        return self.commit(actor, self.prepare_run(actor, kind, params, oid=oid, run=run))

    # ------------------------------------------------------------- identities

    def actor(self, name):
        return self.f['actors'][name]

    def principal(self, name):
        return self.actor(name)['principal_id']

    def assignment(self, name):
        return self.actor(name)['assignment_id']

    def signer(self, name):
        return {'principal_id': self.principal(name), 'assignment_id': self.assignment(name),
                'personal_agent_id': self.principal(self.PERSONAL_AGENTS[name]) if name in self.PERSONAL_AGENTS else None}

    def participants(self, names):
        return [self.signer(name) for name in names]

    # ---------------------------------------------------------------- fields

    def strategy_fields(self, *, label='v1'):
        return {
            'title': f'Synthetic 0.4 Strategy {label}',
            'statement': 'Formalize value creation with explicit human responsibility.',
            'required_capabilities': [
                {'capability_id': 'cap-a', 'name': 'Scope A sensing',
                 'definition': 'Sense demand in the first business scope.',
                 'primary_domain_id': 'scope-a', 'analysis_fields': ['demand', 'evidence_quality']},
                {'capability_id': 'cap-b', 'name': 'Reusable delivery capability',
                 'definition': 'Build the reusable delivery capability inside the same Domain.',
                 'primary_domain_id': 'scope-a', 'analysis_fields': ['delivery_quality']},
                {'capability_id': 'cap-a2', 'name': 'Scope A follow-through',
                 'definition': 'A third capability owned by the same primary Domain.',
                 'primary_domain_id': 'scope-a', 'analysis_fields': ['follow_through']},
            ],
        }

    def architecture_fields(self, *, label='v1'):
        return {
            'title': f'Synthetic 0.4 Architecture {label}',
            'battlefields': [
                {'unit_id': 'bf-value', 'name': 'Value creation field',
                 'definition': 'Where synthetic customers receive measurable value.',
                 'strategic_basis': ['Reusable value requires explicit evidence'],
                 'boundary': 'Synthetic acceptance scope only', 'interfaces': []},
                {'unit_id': 'scope-b', 'name': 'Business scope B (Battlefield)',
                 'definition': 'Value-creation field with explicit responsibility mapping.',
                 'strategic_basis': ['Field-level results need an accountable DRI'],
                 'boundary': 'Synthetic acceptance scope only', 'interfaces': [],
                 'auth_domain_id': self.f['domains']['auth_b'],
                 'current_dri_principal_id': self.principal('dri_b')},
            ],
            'domains': [
                {'unit_id': 'scope-a', 'name': 'Business scope A (Domain)',
                 'definition': 'Long-term capability responsibility for scope A.',
                 'responsibility': 'Own scope A results and evidence',
                 'auth_domain_id': self.f['domains']['auth_a'],
                 'current_dri_principal_id': self.principal('dri_a')},
            ],
        }

    # ---------------------------------------------------------------- M1A

    def open_run(self, actor='ceo_agent', title='Synthetic 0.4 intake run'):
        return exact(self.act(actor, 'method_open_run', {
            'domain_id': self.f['domains']['company'],
            'payload': {'title': title, 'method': 'M1A'}})['result'])

    def create_issue(self, run, evidence, *, title='Synthetic 0.4 strategic issue',
                     strategy_ref=None, architecture_ref=None):
        payload = {'title': title,
                   'summary': 'Clarify the next value step with explicit human agreement.',
                   'core_question': 'Which value step should the company formalize next?',
                   'business_scope': 'strategic', 'urgency': 'green',
                   'source_refs': [evidence],
                   'strategy_ref': strategy_ref, 'architecture_ref': architecture_ref}
        return exact(self.act_run('ceo_agent', 'm1a_create_issue',
                                  {'domain_id': self.f['domains']['company'], 'payload': payload}, run=run)['result'])

    def set_participants(self, issue, names=('ceo', 'dri_a', 'owner_a')):
        return self.act('ceo', 'm1a_set_participants',
                        {'participants': [dict(self.signer(n), research=(n == 'dri_a')) for n in names]},
                        oid=issue['object_id'])

    def draft_agreement(self, issue, names=('ceo', 'dri_a', 'owner_a'), *, no_change=False,
                        statement='Every nominated human agrees to the exact same proposal.'):
        payload = {'title': 'Synthetic 0.4 Agreement', 'issue_ref': self.ref(issue['object_id']),
                   'statement': statement, 'participants': self.participants(names),
                   'evidence_refs': [], 'no_change': no_change}
        return exact(self.act('ceo_agent', 'm1a_draft_agreement', {'payload': payload},
                              oid=issue['object_id'])['result'])

    def confirm_agreement(self, actor, agreement):
        return self.act(actor, 'm1a_confirm_agreement',
                        {'statement': f'{actor} personally confirms this exact Agreement version.'},
                        oid=agreement['object_id'])

    def propose_update(self, issue, agreement, change):
        payload = {'title': 'Synthetic 0.4 update proposal', 'issue_ref': self.ref(issue['object_id']),
                   'agreement_ref': agreement, 'rationale': 'Agreement reached on the exact change.',
                   'change': change}
        return exact(self.act('ceo_agent', 'm1a_propose_update', {'payload': payload},
                              oid=issue['object_id'])['result'])

    def review_update(self, proposal, *, accepted=True):
        return self.act('co_agent', 'm1a_review_update',
                        {'accepted': accepted,
                         'findings': ['Exact change and applicability rationale reviewed.']},
                        oid=proposal['object_id'])

    def confirm_update(self, proposal):
        return self.act('ceo', 'm1a_confirm_update',
                        {'statement': 'Personally confirm the exact atomic formal update.'},
                        oid=proposal['object_id'])

    # ---------------------------------------------------------------- M1B

    def propose_ltco(self, strategy_ref, architecture_ref, scope_id, ltco_period, *, title=None):
        payload = {'title': title or f'Synthetic LTCO {scope_id}',
                   'primary_scope_id': scope_id, 'period': ltco_period,
                   'architecture_ref': architecture_ref, 'strategy_ref': strategy_ref,
                   'result_statement': f'Long-term result for {scope_id} with traceable evidence.',
                   'criteria': ['Result is independently evidenced'],
                   'boundary': 'No execution or acceptance authority in this contract.',
                   'horizon': 'Long-term scope horizon',
                   'why': 'The scope needs an explicit long-term result.',
                   'baseline_refs': [architecture_ref]}
        return exact(self.act('ceo_agent', 'm1b_propose_ltco',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_ltco(self, ltco):
        return self.act('ceo', 'm1b_confirm_ltco',
                        {'statement': 'The current CEO confirms this exact LTCO responsibility.'},
                        oid=ltco['object_id'])

    def draft_pco(self, ltco_ref, scope_id, pco_period, *, title=None):
        ltco = self.object(ltco_ref['object_id'])['latest_revision']['payload']
        payload = {'title': title or f'Synthetic PCO {scope_id}', 'primary_scope_id': scope_id,
                   'period': pco_period, 'parent_ltco_ref': ltco_ref,
                   'architecture_ref': ltco['architecture_ref'], 'strategy_ref': ltco['strategy_ref'],
                   'current_reality': 'Synthetic current reality with partial evidence.',
                   'result_statement': f'Period result for {scope_id}.',
                   'criteria': ['Period result is evidenced'],
                   'expected_lt_advance': 'Advances the long-term result.',
                   'why': 'The period needs an explicit result.'}
        return exact(self.act('co_agent', 'm1b_draft_pco',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def draft_mission(self, pco_ref, owner, scope_id, mission_period, *, title=None, evidence=()):
        payload = {'title': title or f'Synthetic Mission {scope_id}', 'owner_principal_id': self.principal(owner),
                   'primary_scope_id': scope_id, 'parent_pco_ref': pco_ref,
                   'why': 'A necessary result unit under the PCO.',
                   'requirements': ['Deliver the scoped result with evidence'],
                   'criteria': ['Requirements are verifiable'],
                   'evidence_refs': list(evidence), 'period': mission_period}
        return exact(self.act('co_agent', 'm1b_draft_mission',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def open_window(self, pco_refs, mission_refs, ltco_refs, pco_period, names, *, title='Synthetic 0.4 review window'):
        pco = self.object(pco_refs[0]['object_id'])['latest_revision']['payload']
        payload = {'title': title, 'period': pco_period, 'architecture_ref': pco['architecture_ref'],
                   'strategy_ref': pco['strategy_ref'], 'ltco_refs': list(ltco_refs),
                   'pco_refs': list(pco_refs), 'mission_refs': list(mission_refs),
                   'participants': self.participants(names)}
        return exact(self.act('co_agent', 'm1b_open_window',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def comment(self, window, target_ref, actor, *, replaces=None, content=None):
        params = {'target_ref': target_ref,
                  'content': content or 'Synthetic participant opinion on the exact frozen target.'}
        if replaces:
            params['replaces_record_id'] = replaces
            params['content'] = content or 'Replacement opinion: retain the target with explicit evidence checks.'
        return self.act(actor, 'm1b_replace_comment' if replaces else 'm1b_comment', params,
                        oid=window['object_id'])['result']['review_record_id']

    def withdraw(self, window, record_id, actor):
        return self.act(actor, 'm1b_withdraw_comment', {'review_record_id': record_id,
                                                        'reason': 'Author withdraws this opinion.'},
                        oid=window['object_id'])['result']

    def assist(self, window, target_ref, actor, owner):
        return self.act(actor, 'm1b_assist_review', {
            'target_ref': target_ref, 'owner_principal_id': self.principal(owner),
            'analysis': 'Synthetic personal-Agent analysis for the named participant.',
            'source_refs': [], 'generation_version': 'controlled-agent-1'}, oid=window['object_id'])['result']

    def close_window(self, window):
        return self.act('co_agent', 'm1b_close_window',
                        {'reason': 'The frozen review period is complete.'}, oid=window['object_id'])['result']

    def resolve_window(self, window, params):
        return exact(self.act('co_agent', 'm1b_resolve_window', params, oid=window['object_id'])['result'])

    def commit_candidate(self, candidate, responsibility_ref, actor):
        return self.act(actor, 'm1b_commit_candidate',
                        {'responsibility_ref': responsibility_ref,
                         'statement': f'{actor} commits personal responsibility for this exact candidate.'},
                        oid=candidate['object_id'])['result']

    def activate_candidates(self, candidate):
        return self.act('ceo', 'm1b_activate_candidates',
                        {'statement': 'The CEO activates the complete exact candidate set.',
                         'notes': ['Non-blocking collective note recorded with the set.']},
                        oid=candidate['object_id'])['result']

    # ------------------------------------------------------- State / Problem

    def propose_state(self, subject_ref, *, actor='co_agent', rag='unknown', summary='Evidence gap',
                      evidence=(), data_gaps=('Awaiting independent evidence',), as_of=None):
        payload = {'subject_ref': subject_ref, 'as_of': as_of or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                   'summary': summary, 'rag': rag, 'baseline_refs': [subject_ref],
                   'evidence_refs': list(evidence), 'data_gaps': list(data_gaps),
                   'generation_version': 'controlled-recommendation-1'}
        return exact(self.act(actor, 'method_propose_state',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_state(self, actor, state, *, summary=None, rag=None):
        params = {'reason': 'The responsible person confirms the exact business state.'}
        if summary is not None:
            params.update(summary=summary, rag=rag)
        return self.act(actor, 'method_confirm_state', params, oid=state['object_id'])['result']

    def open_problem(self, state_ref, level='strategic', responsible='ceo', *, question='Does the formal step remain appropriate?'):
        payload = {'state_ref': state_ref, 'core_question': question,
                   'statement': 'Synthetic evidence indicates a possible strategic divergence.',
                   'why_material': 'It could invalidate the current formal choice.',
                   'level': level, 'responsible_assignment_id': self.assignment(responsible),
                   'evidence_refs': []}
        return exact(self.act(responsible, 'method_open_problem',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def transfer_problem(self, issue, problem):
        return self.act('ceo_agent', 'm1a_transfer_problem',
                        {'problem_ref': problem, 'reason': 'Link this Problem to the strategic issue.'},
                        oid=issue['object_id'])['result']
