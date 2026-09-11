"""Independent public-HTTP A3 client. Never manufactures business database rows."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid

from acceptance.composition_a2_independent.flow import Flow as A2Flow


def uid():
    return str(uuid.uuid4())


class Flow(A2Flow):
    def __init__(self, h, url, f):
        super().__init__(h, url, f)
        self.period_start = datetime.now(timezone.utc) - timedelta(hours=1)
        self.period_end = datetime.now(timezone.utc) + timedelta(hours=12)
        self.customer_ids = ['SYN-C01', 'SYN-C02', 'SYN-C03']
        self.ec_id = self.work_id = self.plan_id = None
        self.authority = self.appointment = self.work_receipt = None
        self.ec_payload = self.plan_payload = None
        self.evidence = []

    def open_params(self):
        params = super().open_params()
        params['period_window'] = {'start': self.period_start.isoformat(),
                                   'end': self.period_end.isoformat()}
        return params

    def create_reference(self, *, title='Synthetic company result P01',
                         upstream_refs=None, shared=True):
        payload = {'title': title, 'statement': 'Synthetic target: three distinct activated customers',
                   'period_id': self.f['period_id'],
                   'terms': {'synthetic': True, 'outcome_spec': {
                       'metric': 'activated_customers', 'target_count': 3,
                       'client_ids': list(self.customer_ids)}},
                   'upstream_refs': upstream_refs or [],
                   'shared_with_domain_ids': self.share_domains() if shared else []}
        receipt = self.act('ceo', 'create_object', {'object_type': 'CompanyReference',
                           'domain_id': self.f['domains']['company'], 'payload': payload})
        return self.ref(receipt['result']['object_id'])

    def company_activation(self):
        self.composition = self.build()
        self.company_receipt = self.activate(self.composition)
        return self.company_receipt

    def execution_params(self, *, execution_seconds=3600, acceptance_seconds=7200):
        result = self.company_receipt['result']
        domain_id = self.f['domains']['b']
        mission = next(m for m in result['missions'] if m['domain_id'] == domain_id)
        commitment = next(c for c in result['domain_commitments'] if c['domain_id'] == domain_id)
        mission_ref = self.ref(mission['object_id'], 'b')
        commitment_ref = self.ref(commitment['object_id'], 'b')
        formal = self.object(mission_ref['object_id'], 'b')['effective_revision']['payload']
        now = datetime.now(timezone.utc)
        self.deadline = now + timedelta(hours=4)
        self.ec_payload = {
            'title': 'Synthetic DRI to IC execution commitment',
            'mission_ref': mission_ref, 'domain_commitment_ref': commitment_ref,
            'dri_assignment_id': self.f['actors']['b']['assignment_id'],
            'ic_assignment_id': self.f['actors']['ic']['assignment_id'],
            'acceptor_assignment_id': self.f['actors']['reviewer']['assignment_id'],
            'what': {'result_statement': formal['result_statement'], 'boundary': formal['boundary'],
                     'acceptance_criteria': deepcopy(formal['acceptance_criteria']),
                     'hard_deadline': self.deadline.isoformat(),
                     'external_dependency_refs': deepcopy(formal['dependency_refs'])},
            'execution_window': {'valid_from': (now - timedelta(seconds=1)).isoformat(),
                                 'valid_to': (now + timedelta(seconds=execution_seconds)).isoformat()},
            'acceptance_window': {'valid_from': (now - timedelta(seconds=1)).isoformat(),
                                  'valid_to': (now + timedelta(seconds=acceptance_seconds)).isoformat()}}
        return {'object_type': 'ExecutionCommitment', 'domain_id': domain_id,
                'payload': deepcopy(self.ec_payload)}

    def create_execution(self, **kwargs):
        receipt = self.act('b', 'create_object', self.execution_params(**kwargs))
        self.ec_id = receipt['result']['object_id']
        return receipt

    def confirm_params_ec(self, actor):
        return {'party_assignment_id': self.f['actors'][actor]['assignment_id'],
                'understanding': 'I personally accept the exact result, boundary, deadline and criteria.',
                'accepted_terms_hash': self.ref(self.ec_id, actor)['payload_hash']}

    def confirm_execution(self, actor):
        return self.act(actor, 'accept_commitment', self.confirm_params_ec(actor), oid=self.ec_id)

    def activate_execution(self, signatures):
        params = {'handshake_record_ids': [r['result']['handshake_id'] for r in signatures],
                  'activation_policy_revision_id': self.f['policy_revision_ids']['b']}
        receipt = self.act('b', 'activate_commitment', params, oid=self.ec_id)
        self.authority = {'execution_authority_id': receipt['result']['execution_authority_id'],
                          'execution_epoch': receipt['result']['execution_epoch']}
        self.appointment = {'appointment_id': receipt['result']['appointment_id'],
                            'appointment_version': receipt['result']['appointment_version']}
        return receipt

    def work_params(self):
        return {'object_type': 'WorkItem', 'domain_id': self.f['domains']['b'], 'payload': {
            'title': 'Synthetic task within the confirmed What',
            'execution_commitment_ref': self.ref(self.ec_id, 'b'), **self.authority,
            'acceptance_criteria': deepcopy(self.ec_payload['what']['acceptance_criteria']),
            'due_at': self.ec_payload['what']['hard_deadline']}}

    def create_work(self):
        receipt = self.act('b', 'create_object', self.work_params())
        self.work_id = receipt['result']['object_id']
        return receipt

    def accept_work(self):
        self.work_receipt = self.act('ic', 'accept_work_item', self.authority, oid=self.work_id)
        return self.work_receipt

    def make_plan_params(self):
        self.plan_payload = {
            'title': 'Synthetic IC execution plan v1', 'work_item_ref': self.ref(self.work_id, 'ic'),
            'execution_commitment_ref': self.ref(self.ec_id, 'ic'),
            'steps': [{'step_id': 'prepare', 'description': 'Prepare original evidence.',
                       'due_at': (self.deadline - timedelta(minutes=10)).isoformat(),
                       'depends_on_step_ids': []},
                      {'step_id': 'submit', 'description': 'Submit evidence against agreed criteria.',
                       'depends_on_step_ids': ['prepare']}]}
        return {'object_type': 'ExecutionPlan', 'domain_id': self.f['domains']['b'],
                'payload': deepcopy(self.plan_payload)}

    def create_plan(self):
        receipt = self.act('ic', 'create_object', self.make_plan_params())
        self.plan_id = receipt['result']['object_id']
        return receipt

    def revise_plan(self):
        payload = deepcopy(self.plan_payload)
        payload['title'] = 'Synthetic IC execution plan v2'
        payload['steps'][0]['description'] = 'Collect two independent evidence observations.'
        receipt = self.act('ic', 'propose_revision', {'payload': payload}, oid=self.plan_id)
        self.plan_payload = payload
        return receipt

    def upload(self, data=None, *, actor='ic', domain='b'):
        data = data or {'record_origin': 'synthetic', 'evidence_id': uid(), 'note': 'A3 actual uploaded bytes'}
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        result = self.clients[actor].json('POST', '/v1/evidence-assets', {
            'domain_id': self.f['domains'][domain], 'title': 'Synthetic A3 evidence',
            'media_type': 'application/json', 'content_base64': base64.b64encode(raw).decode()})
        self.evidence.append({'response': result, 'bytes_hex': raw.hex(), 'actor': actor})
        return result

    def submit_params(self, evidence, *, responds_to=None):
        return {'title': 'Synthetic delivery', 'summary': 'Actual evidence against the exact current plan.',
                'evidence_revision_ids': [e['revision_id'] for e in evidence],
                'responds_to_acceptance_id': responds_to,
                **self.authority, 'plan_ref': self.ref(self.plan_id, 'ic')}

    def submit(self, evidence=None, *, responds_to=None):
        return self.act('ic', 'submit_deliverable',
                        self.submit_params(evidence or [self.upload()], responds_to=responds_to),
                        oid=self.work_id)

    def review_params(self, submission, *, accept=False):
        result = submission['result']
        return {'deliverable_revision_id': result['deliverable_revision_id'],
                'delivery_payload_hash': result['payload_hash'],
                'verification_result': 'accepted' if accept else 'changes_requested',
                'criterion_results': [{'criterion_id': c['criterion_id'],
                                       'result': 'passed' if accept else 'failed',
                                       'note': 'Verified original evidence' if accept else 'More original evidence required'}
                                      for c in self.ec_payload['what']['acceptance_criteria']],
                'review_note': 'Independent exact-version review.', **self.appointment}

    def review(self, submission, *, accept=False):
        return self.act('reviewer', 'review_deliverable', self.review_params(submission, accept=accept),
                        oid=self.work_id)

    def ready(self, **kwargs):
        self.company_activation()
        self.create_execution(**kwargs)
        self.activate_execution([self.confirm_execution('b'), self.confirm_execution('ic')])
        self.create_work()
        self.accept_work()
        self.create_plan()
        return self
