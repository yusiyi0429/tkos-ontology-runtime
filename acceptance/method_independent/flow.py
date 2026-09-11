"""Plain public HTTP driver; exact references come from observed API receipts."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from acceptance.runtime.client import Client


def exact(result):
    return {key: result[key] for key in ('object_id', 'revision_id', 'payload_hash')}


def uid():
    return str(uuid.uuid4())


class Flow:
    def __init__(self, h, url, f):
        self.h, self.url, self.f = h, url, f
        self.clients = h.clients(url, f)
        self.commands, self.receipts, self.evidence = [], [], []

    def close(self):
        for client in self.clients.values():
            client.close()

    def object(self, oid, actor='ceo'):
        return self.clients[actor].object(oid)

    def ref(self, oid, actor='ceo', *, effective=False):
        obj = self.object(oid, actor)
        revision = obj['effective_revision'] if effective else obj['latest_revision']
        assert revision is not None
        return {'object_id': oid, 'revision_id': revision['revision_id'], 'payload_hash': revision['payload_hash']}

    def target(self, oid, actor='ceo'):
        obj = self.object(oid, actor)
        return {'object_id': oid, 'revision_id': obj['latest_revision_id'], 'expected_version': obj['object_version']}

    def command(self, kind, params, *, oid=None, actor='ceo', key=None):
        body = Client.command(kind, deepcopy(params), target=self.target(oid, actor) if oid else None, key=key)
        body['contract_version'] = 'tkos.method/0.1'
        return body

    def prepare(self, actor, kind, params, *, oid=None, key=None):
        body = self.command(kind, params, oid=oid, actor=actor, key=key)
        result = self.clients[actor].json('POST', '/v1/actions/prepare', body)
        body['expected_versions'] = result['expected_versions']
        if result['target']:
            assert result['target']['revision_id'] == body['target']['revision_id']
            body['target']['expected_version'] = result['target']['expected_version']
        return body

    def commit(self, actor, body, *, headers=None):
        receipt = self.clients[actor].json('POST', '/v1/actions', body, headers=headers)
        assert receipt['status'] == 'committed' and receipt['effect_task_ids'] == []
        self.commands.append({'actor': actor, 'request': deepcopy(body), 'receipt': deepcopy(receipt)})
        self.receipts.append(receipt)
        return receipt

    def act(self, actor, kind, params, *, oid=None, key=None):
        return self.commit(actor, self.prepare(actor, kind, params, oid=oid, key=key))

    def deny(self, actor, body, *, codes, prepare=True):
        """Observe both entrances and all business tables around a rejection."""
        before = self.h.snapshot(self.f)
        paths = ['/v1/actions/prepare', '/v1/actions'] if prepare else ['/v1/actions']
        proofs = []
        for path in paths:
            response = self.clients[actor].json('POST', path, body, expected={400, 401, 403, 404, 409, 422, 503})
            code = response.get('error', {}).get('code')
            assert code in codes, (path, code, codes)
            assert self.h.snapshot(self.f) == before
            proofs.append({'path': path, 'code': code, 'business_unchanged': True})
        return proofs

    def rows(self, table):
        # Hard-coded callers supply table identifiers; no user strings enter SQL.
        assert table.startswith('gov_method_') or table in {'gov_handshakes', 'gov_object_revisions', 'gov_objects', 'gov_action_receipts', 'gov_audit_events', 'gov_execution_authorities', 'gov_work_receipts'}
        from psycopg import sql
        return self.h.sql(self.f, sql.SQL('SELECT * FROM {} WHERE scope_id=%s').format(sql.Identifier(table)), (self.f['scope_id'],))

    def upload(self, *, actor='ceo', domain='company', text='Synthetic original source bytes'):
        raw = json.dumps({'record_origin': 'synthetic', 'id': uid(), 'text': text}, sort_keys=True).encode()
        response = self.clients[actor].json('POST', '/v1/evidence-assets', {
            'domain_id': self.f['domains'][domain], 'title': 'Synthetic Method original evidence',
            'media_type': 'application/json', 'content_base64': base64.b64encode(raw).decode(),
        })
        reference = self.ref(response['object_id'], actor)
        self.evidence.append({'response': response, 'ref': reference, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes_hex': raw.hex(), 'actor': actor})
        return reference

    def start_issue(self, *, iterate=False):
        evidence = self.upload()
        signals = []
        for kind in ['external', 'ceo_insight']:
            result = self.act('ceo', 'm1a_record_signal', {'domain_id': self.f['domains']['company'], 'payload': {
                'title': 'Synthetic ' + kind, 'kind': kind, 'description': 'Pilot demand and strategic prioritization',
                'source_refs': [evidence],
            }})['result']
            signals.append(exact(result))
        payload = {'title': 'Synthetic strategic pilot issue', 'summary': 'Clarify the next customer segment.', 'signal_refs': signals}
        potential = exact(self.act('ceo_agent', 'm1a_open_potential_issue', {
            'domain_id': self.f['domains']['company'], 'payload': payload})['result'])
        original_potential = deepcopy(potential)
        if iterate:
            payload['summary'] = 'Clarify the next customer segment, evidence and scope.'
            potential = exact(self.act('ceo_agent', 'm1a_revise_potential_issue', {'payload': payload}, oid=potential['object_id'])['result'])
        issue = self.act('ceo', 'm1a_confirm_strategic_issue', {'reason': 'Strategic relevance confirmed personally.'}, oid=potential['object_id'])['result']['issue_ref']
        self.act('ceo', 'm1a_assign_research', {
            'dri_principal_id': self.f['actors']['a']['principal_id'],
            'ceo_agent_id': self.f['actors']['ceo_agent']['principal_id'],
            'dri_agent_id': self.f['actors']['dri_agent']['principal_id'],
            'co_agent_id': self.f['actors']['co_agent']['principal_id'],
        }, oid=issue['object_id'])
        return {'issue': issue, 'potential': potential, 'original_potential': original_potential,
                'signals': signals, 'evidence': evidence}

    def clarify(self, chain, *, twice=False):
        issue = chain['issue']
        payload = {'title': 'Synthetic research memo', 'issue_ref': issue, 'question': 'Which pilot segment?',
                   'scope': 'Two synthetic customer segments', 'expected_output': 'Evidence-backed recommendation',
                   'source_refs': [chain['evidence']]}
        chain['memo'] = self.act('ceo_agent', 'm1a_publish_memo', {'payload': payload}, oid=issue['object_id'])['result']['memo_ref']
        chain['original_memo'] = deepcopy(chain['memo'])
        self.act('dri_agent', 'm1a_record_clarification', {'memo_ref': chain['memo'], 'content': 'Clarify the evidence threshold.'}, oid=issue['object_id'])
        if twice:
            self.act('ceo_agent', 'm1a_check_memo', {'memo_ref': chain['memo'], 'result': 'needs_clarification', 'explanation': 'Threshold still ambiguous.'}, oid=issue['object_id'])
            self.act('a', 'm1a_direct_clarification', {'memo_ref': chain['memo'], 'content': 'Three distinct pilots with original evidence.'}, oid=issue['object_id'])
            payload['expected_output'] = 'Recommendation supported by three distinct pilot observations'
            chain['memo'] = self.act('ceo_agent', 'm1a_publish_memo', {'payload': payload}, oid=issue['object_id'])['result']['memo_ref']
            self.act('dri_agent', 'm1a_record_clarification', {'memo_ref': chain['memo'], 'content': 'Second clarification incorporates scope and threshold.'}, oid=issue['object_id'])
        for actor in ['ceo_agent', 'dri_agent']:
            self.act(actor, 'm1a_check_memo', {'memo_ref': chain['memo'], 'result': 'clear', 'explanation': 'Scope and output are now clear.'}, oid=issue['object_id'])
        plan = {'title': 'Synthetic research plan', 'issue_ref': issue, 'memo_ref': chain['memo'],
                'human_work': ['Interview source owners'], 'agent_work': ['Compare observations'],
                'method': 'Triangulate independent observations',
                'due_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
        chain['plan'] = self.act('a', 'm1a_publish_research_plan', {'payload': plan}, oid=issue['object_id'])['result']['plan_ref']
        return chain

    def report(self, chain, *, returned=False):
        issue = chain['issue']
        payload = {'title': 'Synthetic research report', 'issue_ref': issue, 'plan_ref': chain['plan'],
                   'findings': ['Two target segments differ in evidence readiness'],
                   'conclusions': ['Prioritize the segment with verifiable evidence'],
                   'limitations': ['Synthetic observations cannot prove real business value'],
                   'evidence_refs': [chain['evidence']]}
        chain['report'] = self.act('dri_agent', 'm1a_publish_report', {'payload': payload}, oid=issue['object_id'])['result']['report_ref']
        chain['original_report'] = deepcopy(chain['report'])
        self.act('a', 'm1a_submit_report', {'report_ref': chain['report'], 'statement': 'Personally reviewed the exact report.'}, oid=issue['object_id'])
        if returned:
            self.act('ceo_agent', 'm1a_precheck_report', {'report_ref': chain['report'], 'result': 'return', 'findings': ['Explain counterexamples.']}, oid=issue['object_id'])
            payload['findings'].append('A counterexample limits the recommendation to this scope')
            chain['report'] = self.act('a', 'm1a_publish_report', {'payload': payload}, oid=issue['object_id'])['result']['report_ref']
            self.act('a', 'm1a_submit_report', {'report_ref': chain['report'], 'statement': 'Addressed the returned findings.'}, oid=issue['object_id'])
        self.act('ceo_agent', 'm1a_precheck_report', {'report_ref': chain['report'], 'result': 'pass', 'findings': ['Current exact report meets quality requirements.']}, oid=issue['object_id'])
        return chain

    def meeting(self, chain, *, achieved=True):
        oid = chain['issue']['object_id']
        meeting = self.act('a', 'm1a_open_meeting', {'report_ref': chain['report'], 'title': 'Synthetic strategy discussion',
            'objective': 'Reach an explicit strategic agreement', 'material_refs': [chain['evidence'], chain['report']]}, oid=oid)['result']['meeting_ref']
        minutes = {}
        for actor, account, body in [('ceo_agent', 'ceo', 'Prioritize ready pilots'), ('dri_agent', 'dri', 'Prioritize ready pilots after source checks')]:
            minutes[account] = self.act(actor, 'm1a_publish_minutes', {'meeting_ref': meeting, 'title': 'Synthetic ' + account + ' minutes',
                'account': account, 'body': body, 'source_refs': [chain['evidence']]}, oid=oid)['result']['minutes_ref']
        final = self.act('ceo_agent', 'm1a_reconcile_minutes', {'meeting_ref': meeting, 'ceo_minutes_ref': minutes['ceo'], 'dri_minutes_ref': minutes['dri'],
            'title': 'Reconciled synthetic minutes', 'body': 'Prioritize ready pilots with explicit source checks',
            'differences': [{'topic': 'Source check', 'ceo_account': 'Implicit', 'dri_account': 'Explicit', 'resolution': 'Make source checks explicit'}]}, oid=oid)['result']['minutes_ref']
        self.act('a', 'm1a_confirm_minutes', {'minutes_ref': final, 'statement': 'Personally confirm the exact reconciled minutes.'}, oid=oid)
        agreement = self.act('ceo', 'm1a_confirm_agreement', {'minutes_ref': final, 'title': 'Synthetic strategic agreement',
            'statement': 'Target the segment with verifiable pilot evidence', 'meeting_goal_achieved': achieved,
            'reason': 'Ready to decide' if achieved else 'Another discussion is needed on the scope'}, oid=oid)['result']['agreement_ref']
        chain.setdefault('meetings', []).append({'meeting': meeting, 'minutes': minutes, 'final_minutes': final, 'agreement': agreement, 'achieved': achieved})
        chain['agreement'] = agreement
        return chain

    def update(self, chain, *, strategy_ref=None, confirm=True):
        oid = chain['issue']['object_id']
        self.act('ceo', 'm1a_decide_update', {'agreement_ref': chain['agreement'], 'needs_update': True, 'reason': 'Revise strategic pilot priority.'}, oid=oid)
        payload = {'title': 'Synthetic strategy update proposal', 'issue_ref': chain['issue'], 'agreement_ref': chain['agreement'],
            'rationale': 'Confirmed evidence supports the change', 'changes': [{
                'scope': 'company', 'target_ref': strategy_ref, 'payload': {'title': 'Synthetic Strategy',
                    'statement': 'Prioritize evidenced pilot value', 'map': {'units': [
                        {'unit_id': 'unit-pilot', 'name': 'Pilot validation', 'judgment': 'Validate value with direct evidence',
                         'owner_principal_id': self.f['actors']['a']['principal_id']},
                        {'unit_id': 'unit-delivery', 'name': 'Delivery readiness', 'judgment': 'Maintain explicit acceptance quality',
                         'owner_principal_id': self.f['actors']['b']['principal_id']},
                    ]}}}]}
        if strategy_ref is None:
            payload['changes'][0].pop('target_ref')
        chain['proposal'] = self.act('ceo_agent', 'm1a_propose_update', {'payload': payload}, oid=oid)['result']['proposal_ref']
        self.act('co_agent', 'm1a_review_update', {'proposal_ref': chain['proposal'], 'accepted': True, 'impact_level': 'company',
            'findings': ['Company strategy changes; existing business commitments require explicit review.']}, oid=oid)
        chain['confirm_command'] = self.prepare('ceo', 'm1a_confirm_update', {'proposal_ref': chain['proposal'], 'reason': 'Personally confirm formal strategy update.'}, oid=oid)
        if confirm:
            result = self.commit('ceo', chain['confirm_command'])['result']
            chain['strategy'] = result['changed_refs'][0]
        return chain

    def strategy(self, *, complete=False, strategy_ref=None, confirm=True):
        chain = self.start_issue(iterate=complete)
        self.clarify(chain, twice=complete)
        self.report(chain, returned=complete)
        if complete:
            self.meeting(chain, achieved=False)
        self.meeting(chain)
        return self.update(chain, strategy_ref=strategy_ref, confirm=confirm)
