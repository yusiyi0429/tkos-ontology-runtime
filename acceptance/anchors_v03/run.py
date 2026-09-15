"""Real HTTP/PG/MinIO acceptance of the isolated 0.3 increment."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from acceptance.method_independent.m1b_flow import MethodFlow
from acceptance.method_independent.flow import Flow as BaseFlow, exact, uid
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import seed_v03, register_v03


class Flow(MethodFlow):
    def command(self, kind, params, **kwargs):
        p=deepcopy(params)
        if kind=='m1a_propose_update':
            for change in p['payload']['changes']:
                if change['scope']=='company':
                    units=change['payload']['map']['units']
                    for unit in units: unit['unit_type']='battlefield'
                    change['architecture']={'title':'Synthetic responsibility architecture','units':[
                        {'unit_id':u['unit_id'],'unit_type':u['unit_type'],'name':u['name'],
                         'definition':u['judgment'],'strategic_basis':['Pilot value strategy'],
                         'boundary':'Explicit synthetic business responsibility','interfaces':[],
                         'domain_id':self.f['domains']['a' if i==0 else 'b']} for i,u in enumerate(units)]}
        if kind in {'m1b_propose_ltco','m1b_revise_ltco','m1b_draft_pco','m1b_revise_pco','m1b_draft_mission','m1b_revise_mission'}:
            strategy_ref=p['payload'].get('strategy_ref')
            if not strategy_ref:
                strategy_ref=self.object(p['payload']['pco_ref']['object_id'])['latest_revision']['payload']['strategy_ref']
            p['payload']['architecture_ref']=self.object(strategy_ref['object_id'])['method_state']['architecture_ref']
            if 'mission' in kind: p['payload']['primary_scope_id']='unit-pilot'
        if kind=='m1b_resolve_window':
            p['pco_payload']['architecture_ref']=self.object(p['pco_payload']['strategy_ref']['object_id'])['method_state']['architecture_ref']
            for mission in p['missions']:
                mission.update(architecture_ref=p['pco_payload']['architecture_ref'],primary_scope_id='unit-pilot')
        if kind in {'m1b_open_window','m1b_reopen_window','m1b_reopen_candidates'}:
            (p['payload'] if kind=='m1b_open_window' else p).setdefault('feedback_deadline',(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        body=BaseFlow.command(self,kind,p,**kwargs)
        body['contract_version']='tkos.method/0.3'
        if kind=='m1a_open_potential_issue': body['run_ref']=self.intake_run
        return body

    def start_issue(self, **kwargs):
        self.intake_run=exact(self.act('ceo_agent','method_open_run',{'domain_id':self.f['domains']['company'],
            'payload':{'title':'Synthetic M1A intake','method':'M1A'}})['result'])
        evidence=self.upload()
        self.act('ceo_agent','method_attach_run',{'object_ref':evidence},oid=self.intake_run['object_id'])
        potential=exact(self.act('ceo_agent','m1a_open_potential_issue',{'domain_id':self.f['domains']['company'],
            'payload':{'title':'Synthetic issue','summary':'Compare business options','core_question':'Which customer segment?',
                       'business_scope':'strategic','urgency':'yellow','source_refs':[evidence]}})['result'])
        issue=self.act('ceo_agent','m1a_confirm_strategic_issue',{'reason':'Agent initiates research-worthy issue'},oid=potential['object_id'])['result']['issue_ref']
        self.act('ceo','m1a_assign_research',{'dri_principal_id':self.f['actors']['a']['principal_id'],
            'ceo_agent_id':self.f['actors']['ceo_agent']['principal_id'],'dri_agent_id':self.f['actors']['dri_agent']['principal_id'],
            'co_agent_id':self.f['actors']['co_agent']['principal_id']},oid=issue['object_id'])
        return {'issue':issue,'potential':potential,'original_potential':potential,'signals':[],'evidence':evidence}


def run(h,source):
    checks=[]
    initial=source_manifest(source)
    def check(name,value=True):
        assert value,name
        checks.append(name);print('PASS '+name,flush=True)
    f=seed_v03(h.env,h.private/'identities.json','runtime-acceptance-anchors-v03-'+uid()[:8])
    register_v03(h,source,f)
    process,url,_=h.start_api(source)
    flow=Flow(h,url,f)
    try:
        chain=flow.strategy(complete=True)
        strategy=chain['strategy']
        arch=flow.object(strategy['object_id'])['method_state']['architecture_ref']
        check('legal_agent_initiation_research_and_atomic_strategy_architecture',flow.object(arch['object_id'])['effective_revision']['payload']['strategy_ref']==strategy)
        check('initiating_agent_separate_from_human_ceo',flow.object(chain['issue']['object_id'])['method_state']['initiating_agent_id']==f['actors']['ceo_agent']['principal_id'])
        ltco=flow.ltco(strategy)['ltco']
        targets=flow.open_window(flow.draft_targets(strategy,ltco))
        first=flow.comment(targets)
        flow.comment(targets,replaces=first)
        other=flow.comment(targets,'b')
        flow.act('b','m1b_withdraw_comment',{'review_record_id':other,'reason':'Personal withdrawal'},oid=targets['window']['object_id'])
        flow.close_window(targets);flow.resolve(targets);flow.confirm_candidates(targets)
        check('m1b_comments_replacement_withdrawal_full_candidate_confirmation')
        handoff=flow.clients['a'].json('GET','/v1/method/missions/'+targets['mission']['object_id']+'/handoff')
        check('mission_has_exact_architecture_without_execution',handoff['execution_authority'] is None)
        payload={'subject_ref':targets['mission'],'as_of':datetime.now(timezone.utc).isoformat(),
            'summary':'Evidence gap','rag':'unknown','baseline_refs':[targets['mission']],
            'evidence_refs':[],'data_gaps':['Awaiting outcome evidence'],'generation_version':'controlled-1'}
        state=exact(flow.act('co_agent','method_propose_state',{'domain_id':f['domains']['company'],'payload':payload})['result'])
        flow.deny('b',flow.command('method_confirm_state',{'reason':'Unrelated DRI attempts confirmation'},oid=state['object_id']),codes={'FORBIDDEN','NOT_FOUND'})
        flow.act('a','method_confirm_state',{'reason':'Owner confirms exact evidence gap'},oid=state['object_id'])
        check('state_exact_owner_confirmation_and_unrelated_dri_denial')
        flow.deny('co_agent',flow.command('method_propose_state',{'domain_id':f['domains']['company'],'payload':payload}),codes={'VERSION_CONFLICT'})
        check('state_same_subject_asof_unique')
        payload.update(summary='Evidence indicates progress',rag='green',evidence_refs=[chain['evidence']],data_gaps=[],generation_version='controlled-2')
        proposal=exact(flow.act('co_agent','method_propose_state',{'domain_id':f['domains']['company'],'payload':payload,'previous_state_ref':state})['result'])
        check('new_recommendation_retains_old_canonical',flow.object(state['object_id'])['method_state']['canonical_ref']==state)
        confirmed=flow.act('a','method_confirm_state',{'reason':'Evidence supports progress but quality remains uncertain','summary':'Quality gap remains','rag':'yellow'},oid=state['object_id'])
        state=exact(confirmed['result'])
        check('override_keeps_recommendation_history',state!=proposal and flow.object(state['object_id'])['effective_revision']['payload']['rag']=='yellow')
        pp={'state_ref':state,'core_question':'Does the pilot choice remain appropriate?', 'statement':'Evidence and customer expectations diverge',
            'why_material':'May invalidate current strategic choice','level':'strategic',
            'responsible_assignment_id':f['actors']['ceo']['assignment_id'],'evidence_refs':[chain['evidence']]}
        problem=exact(flow.act('co_agent','method_open_problem',{'domain_id':f['domains']['company'],'payload':pp})['result'])
        flow.deny('co_agent',flow.command('method_open_problem',{'domain_id':f['domains']['company'],'payload':pp}),codes={'VERSION_CONFLICT'})
        check('problem_identity_survives_repeat_discovery')
        # A real canonical PCO State feeds the analysis artifact.
        pco_state_payload={**payload,'subject_ref':targets['pco'],'baseline_refs':[targets['pco']]}
        ps=exact(flow.act('co_agent','method_propose_state',{'domain_id':f['domains']['company'],'payload':pco_state_payload})['result'])
        flow.act('ceo','method_confirm_state',{'reason':'Company CEO confirms current PCO state'},oid=ps['object_id'])
        review_payload={'review_id':uid(),'title':'Synthetic review','period':targets['period'],'target_refs':[targets['pco']],
            'fact_refs':[],'state_refs':[ps],'findings':['Pilot expectations differ'],'learnings':['Re-examine assumptions'],
            'implications':['Bring to M1A'],'generation_version':'controlled-review-1'}
        review=exact(flow.act('co_agent','m1b_generate_review',{'domain_id':f['domains']['company'],'payload':review_payload})['result'])
        check('period_review_consumes_canonical_state')
        potential=exact(flow.act('ceo_agent','m1a_open_potential_issue',{'domain_id':f['domains']['company'],
            'payload':{'title':'Unified review finding','summary':'Same strategic question from two sources','core_question':pp['core_question'],
                'business_scope':'strategic','urgency':'yellow','source_refs':[review,problem]}})['result'])
        flow.deny('ceo',flow.command('m1a_confirm_strategic_issue',{'reason':'Human attempts Agent initiation'},oid=potential['object_id']),codes={'FORBIDDEN'})
        body=flow.prepare('ceo_agent','m1a_confirm_strategic_issue',{'reason':'Agent recognizes one question across sources'},oid=potential['object_id'])
        from acceptance.method_independent.cases_recovery import _fail_after_write
        _fail_after_write(flow,'ceo_agent',body,'after_method_problem_transfer')
        check('transfer_rollback_keeps_original_problem_open',flow.object(problem['object_id'])['method_state']['tracking'])
        receipt=flow.commit('ceo_agent',body)
        issue=receipt['result']['issue_ref']
        check('two_sources_one_issue_and_atomic_transfer',flow.object(problem['object_id'])['method_state']['issue_ref']==issue and not flow.object(problem['object_id'])['method_state']['tracking'])
        check('original_envelope_replay',flow.commit('ceo_agent',body)==receipt)
        assign={'dri_principal_id':f['actors']['a']['principal_id'],'ceo_agent_id':f['actors']['ceo_agent']['principal_id'],
            'dri_agent_id':f['actors']['dri_agent']['principal_id'],'co_agent_id':f['actors']['co_agent']['principal_id']}
        flow.deny('ceo_agent',flow.command('m1a_assign_research',assign,oid=issue['object_id'],actor='ceo_agent'),codes={'FORBIDDEN'})
        flow.act('ceo','m1a_assign_research',assign,oid=issue['object_id'])
        check('research_assignment_remains_human_ceo')
        duplicate=exact(flow.act('ceo_agent','m1a_open_potential_issue',{'domain_id':f['domains']['company'],
            'payload':{'title':'Another discovery','summary':'Associate an existing question','core_question':pp['core_question'],
                'business_scope':'strategic','urgency':'yellow','source_refs':[review]}})['result'])
        count=len([r for r in flow.rows('gov_objects') if r['object_type']=='StrategicIssue'])
        linked=flow.act('ceo_agent','m1a_confirm_strategic_issue',{'reason':'Same core judgment, preserve sources','existing_issue_ref':flow.ref(issue['object_id'])},oid=duplicate['object_id'])
        check('agent_association_does_not_duplicate_issue',count==len([r for r in flow.rows('gov_objects') if r['object_type']=='StrategicIssue']))
        from .extended import exercise
        exercise(flow,h,source,process,chain,targets,ltco,state,payload,body,receipt,check)
        check('no_execution_or_delivery_acceptance',not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))
        check('source_unchanged',source_manifest(source)==initial)
        public_json(h.output/'summary.json',{'passed':len(checks),'checks':checks,'runtime_anchor_delta_api_accepted':True,
            'scope':'Synthetic 0.3 HTTP/PG/MinIO increment; not full release acceptance',
            'partner_wiring':'not_verified','clark_browser':'not_verified','real_model':'not_run'})
    finally:
        flow.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--env-file',type=Path,required=True);p.add_argument('--private',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.private.exists() or args.output.exists(): raise ValueError('Use fresh paths')
    h=MethodHarness(args.env_file.resolve(),args.output.resolve(),args.private.resolve())
    try: run(h,Path('src').resolve())
    finally: h.close()

if __name__=='__main__': main()
