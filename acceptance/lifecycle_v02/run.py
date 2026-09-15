"""Versioned public HTTP acceptance; business data is created through legal actions."""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from acceptance.method_independent.m1b_flow import MethodFlow
from acceptance.method_independent.flow import exact, uid
from acceptance.method_independent.fixture import register_method
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import seed_v02, register_v02


class Flow(MethodFlow):
    def command(self, kind, params, **kwargs):
        params = deepcopy(params)
        if kind in {'m1a_open_potential_issue', 'm1a_revise_potential_issue'}:
            params['payload'].update(origin='signal', business_scope='strategic', urgency='yellow')
        if kind == 'm1a_propose_update':
            for change in params['payload']['changes']:
                if change['scope'] == 'company':
                    for unit in change['payload']['map']['units']:
                        unit['unit_type'] = 'battlefield'
        if kind in {'m1b_open_window', 'm1b_reopen_window', 'm1b_reopen_candidates'}:
            target = params['payload'] if kind == 'm1b_open_window' else params
            target.setdefault('feedback_deadline', (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        body = super().command(kind, params, **kwargs)
        body['contract_version'] = 'tkos.method/0.2'
        return body

    def act(self, actor, kind, params, **kwargs):
        result = super().act(actor, kind, params, **kwargs)
        if kind == 'm1a_record_signal':
            super().act('ceo', 'm1a_activate_signal', {'reason': 'CEO triage before exploration'}, oid=result['result']['object_id'])
        return result


def run(h, source):
    checks = []
    initial_source = source_manifest(source)
    def check(name, predicate=True):
        assert predicate, name
        checks.append(name)
        print('PASS '+name, flush=True)
    f = seed_v02(h.env, h.private/'identities.json', 'runtime-acceptance-lifecycle-v02-'+uid()[:8])
    register_method(h, source, f)
    process, url, _ = h.start_api(source)
    legacy = MethodFlow(h,url,f)
    old_chain = legacy.start_issue()
    register_v02(h, source, f)
    legacy.clarify(old_chain)
    check('v01_remains_writable_after_v02_registration')
    legacy.close()
    flow = Flow(h, url, f)
    try:
        chain = flow.strategy(complete=True)
        check('deep_research_preserves_human_decisions_and_strategy_update')
        ltco = flow.ltco(chain['strategy'])['ltco']
        targets = flow.open_window(flow.draft_targets(chain['strategy'], ltco))
        first = flow.comment(targets)
        flow.comment(targets, replaces=first)
        other = flow.comment(targets, actor='b')
        flow.act('b','m1b_withdraw_comment',{'review_record_id':other,'reason':'Withdraw personally'},oid=targets['window']['object_id'])
        from acceptance.method_independent.cases_recovery import _race, _fail_after_write
        close = flow.prepare('co_agent','m1b_close_window',{'reason':'Close competing with comment'},oid=targets['window']['object_id'])
        late = flow.prepare('a','m1b_comment',{'target_ref':targets['pco'],'content':'Concurrent late opinion'},oid=targets['window']['object_id'])
        closure,rejected,proof = _race(flow,'co_agent',close,'a',late)
        targets['frozen_opinion_ids'] = closure['result']['frozen_opinion_ids']
        check('comment_close_race_serialized',rejected['error']['code'] in {'VERSION_CONFLICT','INVALID_STATE'} and proof['database_lock_observed'])
        check('close_freezes_only_effective_comments',len(targets['frozen_opinion_ids']) == 1)
        resolve = flow.prepare('co_agent','m1b_resolve_window',flow.resolution_params(targets),oid=targets['window']['object_id'])
        _fail_after_write(flow,'co_agent',resolve,'after_method_candidate_write')
        check('candidate_partial_write_rolls_back_all_tables')
        flow.resolve(targets)
        confirm = flow.prepare('ceo','m1b_confirm_candidates',{'reason':'Confirm complete set'},oid=targets['candidate']['object_id'])
        first,repeated,proof = _race(flow,'ceo',confirm,'ceo',confirm)
        check('double_click_same_envelope_same_receipt',first == repeated)
        handoff = flow.clients['a'].json('GET','/v1/method/missions/'+targets['mission']['object_id']+'/handoff')
        check('formal_mission_has_v02_handoff_without_execution', handoff['contract_version']=='tkos.method/0.2' and handoff['execution_authority'] is None)
        body = flow.commands[-1]['request']
        receipt = flow.commands[-1]['receipt']
        check('original_confirmation_replay',flow.commit('ceo',body)==receipt)
        direct = flow.act('ceo','m1a_create_direct_issue',{'domain_id':f['domains']['company'],'payload':{
            'title':'Direct question','summary':'Explore alternatives','business_scope':'battlefield','urgency':'red',
            'urgency_reason':'Time-sensitive evidence','confirmation_reason':'CEO requests research'}})['result']
        oid = direct['object_id']
        flow.act('ceo','m1a_assign_research',{'dri_principal_id':f['actors']['a']['principal_id'],
            'ceo_agent_id':f['actors']['ceo_agent']['principal_id'],'dri_agent_id':f['actors']['dri_agent']['principal_id'],
            'co_agent_id':f['actors']['co_agent']['principal_id']},oid=oid)
        brief = flow.act('ceo_agent','m1a_publish_brief',{'payload':{'title':'Light exploration','issue_ref':flow.ref(oid),
            'question':'Which alternative?','analysis':'Compare sources','options':['A','B'],'limitations':['Synthetic evidence'],
            'source_refs':[chain['evidence']]}},oid=oid)['result']['brief_ref']
        meeting = {'title':'Light review','objective':'Discuss evidence','brief_ref':brief,'material_refs':[brief]}
        flow.deny('a',flow.command('m1a_open_meeting',meeting,oid=oid,actor='a'),codes={'INVALID_STATE'})
        flow.deny('ceo_agent',flow.command('m1a_confirm_brief',{'brief_ref':brief,'reason':'Sufficient'},oid=oid,actor='ceo_agent'),codes={'FORBIDDEN'})
        flow.act('ceo','m1a_confirm_brief',{'brief_ref':brief,'reason':'Sufficient for this discussion'},oid=oid)
        flow.act('a','m1a_open_meeting',meeting,oid=oid)
        check('light_meeting_requires_ceo_exact_sufficiency')
        types = [row['object_type'] for row in flow.rows('gov_objects')]
        check('no_execution_or_acceptance_records',not flow.rows('gov_execution_authorities') and not flow.rows('gov_work_receipts'))
        check('research_brief_persisted',types.count('ResearchBrief') == 1)
        baseline = flow.ref(targets['pco']['object_id'])
        fact = flow.fact(targets)
        corrected = flow.fact(targets,corrects=fact,value=3)
        review = flow.review(targets,[corrected])
        materials = flow.clients['ceo'].json('GET','/v1/method/pcos/'+targets['pco']['object_id']+'/review-materials')
        check('PCO_aggregates_independent_facts_and_review_without_revision',len(materials['business_facts']) == 2
            and len(materials['period_reviews']) == 1 and baseline == flow.ref(targets['pco']['object_id']))
        workface = flow.clients['ceo'].json('GET','/v1/workspaces/ceo?collection=strategic-issues')
        check('lifecycle_workface_has_authority_hints',any(item.get('operations') for item in workface['items']))
        now = datetime.now(timezone.utc).isoformat()
        context = {'contract_version':'tkos.method/0.2','object_ids':[chain['signals'][0]['object_id']],
            'valid_at':now,'known_at':now,'stage':'research','purpose':'analysis','include_drafts':True}
        pack = flow.clients['ceo_agent'].json('POST','/v1/context-packs',context)
        check('dialogue_excludes_signal',not pack['selected'] and any(x['reason']=='signal_not_for_dialogue' for x in pack['excluded']))
        bad = {**context,'purpose':'research'}
        flow.clients['ceo_agent'].request('POST','/v1/context-packs',bad,expected=403)
        run_ref = exact(flow.act('ceo','method_open_run',{'domain_id':f['domains']['company'],
            'payload':{'title':'Authorized exploration','method':'M1A'}})['result'])
        flow.act('ceo','method_attach_run',{'object_ref':chain['signals'][0]},oid=run_ref['object_id'])
        research = {**bad,'run_ref':run_ref}
        pack = flow.clients['ceo_agent'].json('POST','/v1/method/research-context-packs',research)
        check('research_signal_requires_run',any(x['object_type']=='Signal' for x in pack['selected']))
        sid = pack['context_snapshot_id']
        flow.clients['ceo_agent'].request('GET','/v1/context-packs/'+sid,expected=403)
        flow.clients['ceo_agent'].request('GET','/v1/method/research-context-packs/'+sid)
        flow.clients['ceo'].request('POST','/v1/method/research-context-packs',research,expected=403)
        flow.act('ceo','method_pause_run',{'note':'Pause research'},oid=run_ref['object_id'])
        flow.clients['ceo_agent'].request('GET','/v1/method/research-context-packs/'+sid,expected=409)
        check('research_snapshot_rechecks_run_and_identity')
        expired = flow.draft_targets(chain['strategy'],ltco)
        deadline = datetime.now(timezone.utc)+timedelta(seconds=3)
        expired['window'] = exact(flow.act('co_agent','m1b_open_window',{'domain_id':f['domains']['company'],'payload':{
            'title':'Deadline boundary','period':expired['period'],'strategy_ref':expired['strategy'],'ltco_ref':expired['ltco'],
            'target_refs':[expired['pco'],expired['mission']],'participants':flow.participants(),
            'feedback_deadline':deadline.isoformat()}})['result'])
        comment = flow.prepare('a','m1b_comment',{'target_ref':expired['pco'],'content':'Late commit'},oid=expired['window']['object_id'])
        import time
        time.sleep(max(0,(deadline-datetime.now(timezone.utc)).total_seconds())+0.05)
        flow.deny('a',comment,codes={'REVIEW_DEADLINE_PASSED'},prepare=False)
        flow.close_window(expired)
        reopened=flow.act('ceo','m1b_reopen_window',{'reason':'New review opportunity','title':'Reopened review'},oid=expired['window']['object_id'])['result']
        check('deadline_enforced_at_commit_explicit_close_and_new_window',reopened['object_id']!=expired['window']['object_id'])

        flow.close()
        h.stop(process)
        _, url, _ = h.start_api(source)
        flow.clients = h.clients(url,f)
        check('restart_recovers_original_confirmation',flow.commit('ceo',body)==receipt)
        cross = flow.command('m1a_create_direct_issue',{'domain_id':f['domains']['company'],'payload':{
            'title':'Cross-version input','summary':'Must reject','business_scope':'strategic','urgency':'gray',
            'confirmation_reason':'Boundary check','direct_source_refs':[old_chain['signals'][0]]}})
        flow.deny('ceo',cross,codes={'PROTOCOL_BINDING_CONFLICT'})
        check('cross_version_dependencies_rejected')
        from acceptance.runtime.client import Client
        actor = f['actors']['b']
        flow.clients['ceo'].json('POST','/v1/actions',Client.command('revoke_assignment',{'assignment_id':actor['assignment_id']}))
        flow.clients['b'].request('GET','/v1/objects/'+targets['window']['object_id'],expected={403,404})
        flow.clients['b_agent'].request('GET','/v1/objects/'+targets['window']['object_id'],expected={403,404})
        check('revocation_removes_human_and_agent_window_access')
        from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
        adapter = ControlAdapter(h,source,'memory_service_runtime.governed.control')
        adapter.cli('freeze-both',['freeze-writes','--scope-id',f['scope_id'],'--protocol-id','tkos.method',
            '--reason','Acceptance checks both installed versions'],expected_exit=0)
        frozen = flow.command('m1a_archive_signal',{'reason':'Write after freeze'},oid=chain['signals'][0]['object_id'])
        flow.deny('ceo',frozen,codes={'PROTOCOL_WRITE_DISABLED'})
        old = MethodFlow(h,url,f)
        try:
            frozen_old = old.command('m1a_assign_research',{'dri_principal_id':f['actors']['a']['principal_id'],
                'ceo_agent_id':f['actors']['ceo_agent']['principal_id'],'dri_agent_id':f['actors']['dri_agent']['principal_id'],
                'co_agent_id':f['actors']['co_agent']['principal_id']},oid=old_chain['issue']['object_id'])
            old.deny('ceo',frozen_old,codes={'PROTOCOL_WRITE_DISABLED'})
        finally:
            old.close()
        check('protocol_freeze_covers_both_versions')
        check('source_unchanged',initial_source == source_manifest(source))
        public_json(h.output/'summary.json',{'checks':checks,'passed':len(checks),'runtime_lifecycle_delta_api_accepted':True,
            'scope':'Synthetic lifecycle 0.2 HTTP, database and storage acceptance; not full release acceptance',
            'partner_wiring':'not_verified','clark_browser':'not_verified','real_model':'not_run'})
    finally:
        flow.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--env-file',type=Path,required=True);p.add_argument('--private',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError('Use fresh output and private paths')
    h=MethodHarness(args.env_file.resolve(),args.output.resolve(),args.private.resolve())
    try:
        run(h,Path('src').resolve())
    finally:
        h.close()

if __name__=='__main__': main()
