"""0.5 公开 HTTP 驱动：0.4 的链路加 Constraint、审视结论、复盘确认、Mission 扩展字段与带周期的状态。"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from acceptance.method_independent.flow import exact
from acceptance.method_v04.flow import Flow as FlowV04, period


class Flow(FlowV04):
    def command(self, kind, params, *, oid=None, actor='ceo', key=None, run=None):
        body = super().command(kind, params, oid=oid, actor=actor, key=key, run=run)
        body['contract_version'] = 'tkos.method/0.5'
        return body

    # ------------------------------------------------------------ Constraint

    def record_constraint(self, applies_to, *, actor='co_agent', architecture_ref=None, effective, title='Synthetic constraint'):
        payload = {'title': title, 'applies_to': applies_to, 'architecture_ref': architecture_ref,
                   'statement': 'Only two engineers are available in this period.',
                   'constraint_type': 'people', 'effective': effective, 'source': 'Synthetic headcount plan',
                   'authority': 'Scope DRI', 'severity': 'hard', 'evidence_refs': []}
        return exact(self.act(actor, 'm1b_record_constraint',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_constraint(self, actor, constraint):
        return self.act(actor, 'm1b_confirm_constraint',
                        {'statement': f'{actor} confirms this exact constraint for its scope.'},
                        oid=constraint['object_id'])['result']

    # ------------------------------------------------------------------ M1B

    def propose_ltco(self, strategy_ref, architecture_ref, scope_id, ltco_period, *, title=None, constraints=()):
        payload = {'title': title or f'Synthetic LTCO {scope_id}',
                   'primary_scope_id': scope_id, 'period': ltco_period,
                   'architecture_ref': architecture_ref, 'strategy_ref': strategy_ref,
                   'result_statement': f'Long-term result for {scope_id} with traceable evidence.',
                   'criteria': ['Result is independently evidenced'],
                   'boundary': 'No execution or acceptance authority in this contract.',
                   'horizon': 'Rolling six months from now',
                   'why': 'The scope needs an explicit long-term result.',
                   'baseline_refs': [architecture_ref],
                   'realization_logic': 'Two key business shifts, sequenced.',
                   'key_assumptions': ['Demand holds through the horizon'],
                   'constraint_refs': list(constraints)}
        return exact(self.act('ceo_agent', 'm1b_propose_ltco',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_ltco(self, ltco, conclusion='established'):
        return self.act('ceo', 'm1b_confirm_ltco',
                        {'conclusion': conclusion, 'statement': 'The current CEO confirms this exact LTCO responsibility.'},
                        oid=ltco['object_id'])['result']

    def draft_pco(self, ltco_ref, scope_id, pco_period, *, title=None, period_review_ref=None, constraints=()):
        ltco = self.object(ltco_ref['object_id'])['latest_revision']['payload']
        payload = {'title': title or f'Synthetic PCO {scope_id}', 'primary_scope_id': scope_id,
                   'period': pco_period, 'parent_ltco_ref': ltco_ref, 'period_review_ref': period_review_ref,
                   'architecture_ref': ltco['architecture_ref'], 'strategy_ref': ltco['strategy_ref'],
                   'current_reality': 'Synthetic current reality with partial evidence.',
                   'result_statement': f'Period result for {scope_id}.',
                   'criteria': ['Period result is evidenced'],
                   'expected_lt_advance': 'Advances the long-term result by one stage.',
                   'why': 'The period needs an explicit result.',
                   'boundary': 'Not promised this period: the second market.',
                   'constraint_refs': list(constraints)}
        return exact(self.act('co_agent', 'm1b_draft_pco',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def draft_mission(self, pco_ref, owner, scope_id, mission_period, *, title=None, evidence=(),
                      contributes=(), dependencies=(), constraints=()):
        payload = {'title': title or f'Synthetic Mission {scope_id}', 'owner_principal_id': self.principal(owner),
                   'primary_scope_id': scope_id, 'parent_pco_ref': pco_ref,
                   'why': 'A necessary result unit under the PCO.',
                   'requirements': ['Deliver the scoped result with evidence'],
                   'criteria': ['Requirements are verifiable'],
                   'evidence_refs': list(evidence), 'period': mission_period,
                   'boundary': 'Does not cover onboarding.',
                   'contributes_to_scope_ids': list(contributes), 'dependencies': list(dependencies),
                   'resource_needs': ['one designer for two weeks'], 'constraint_refs': list(constraints)}
        return exact(self.act('co_agent', 'm1b_draft_mission',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    def confirm_review(self, review, *, findings=None):
        params = {'statement': 'The CEO confirms this exact period review.'}
        if findings is not None:
            params['findings'] = findings
        return self.act('ceo', 'm1b_confirm_review', params, oid=review['object_id'])['result']

    # ---------------------------------------------------------------- State

    def propose_state(self, subject_ref, *, actor='co_agent', rag='unknown', summary='Evidence gap',
                      evidence=(), data_gaps=('Awaiting independent evidence',), state_period=None, drilldown=()):
        state_period = state_period or period(-30, 0)
        payload = {'subject_ref': subject_ref, 'as_of': state_period['end'], 'period': state_period,
                   'summary': summary, 'rag': rag, 'baseline_refs': [subject_ref],
                   'evidence_refs': list(evidence), 'drilldown_refs': list(drilldown), 'data_gaps': list(data_gaps),
                   'generation_version': 'controlled-generation-1'}
        return exact(self.act(actor, 'method_propose_state',
                              {'domain_id': self.f['domains']['company'], 'payload': payload})['result'])

    # ---------------------------------------------------------------- reads

    def company_view(self, view_period, actor='ceo'):
        # period() 产生带 '+00:00' 的 ISO 时间；查询串中未编码的 '+' 会被服务端解码成空格（422），必须 URL 编码。
        query = urlencode({'period_start': view_period['start'], 'period_end': view_period['end']})
        return self.clients[actor].json('GET', '/v1/method/company-view?' + query)

    def confirmations(self, oid, actor='ceo'):
        return self.clients[actor].json('GET', f'/v1/method/objects/{oid}/confirmations')
