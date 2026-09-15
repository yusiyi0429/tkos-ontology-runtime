"""Recovery, permissions and physical invariants independent of model outputs."""
from copy import deepcopy
from datetime import datetime, timezone
from .run import exact
from acceptance.method_independent.cases_recovery import _race, _fail_after_write
from acceptance.runtime.client import Client


def exercise(flow,h,source,process,chain,targets,ltco,state,payload,body,receipt,check):
    f=flow.f
    # Real reader and current-operation projections, including closed work removal.
    view=flow.clients['a'].json('GET','/v1/method/anchors/'+state['object_id'])
    check('anchor_read_returns_versions_and_action_reasons',view['method_state']['canonical_ref']==state and bool(view['operations']))
    page=flow.clients['ceo'].json('GET','/v1/workspaces/ceo?collection=operating-problems')
    check('transferred_problems_leave_active_work_surface',all(x['phase']=='open' for x in page['items']))
    flow.clients['outsider'].json('GET','/v1/method/anchors/'+state['object_id'],expected={403,404})
    check('cross_domain_anchor_read_rejected')
    now=datetime.now(timezone.utc).isoformat()
    context=flow.clients['ceo_agent'].json('POST','/v1/method/research-context-packs',{
        'contract_version':'tkos.method/0.3','run_ref':flow.intake_run,'object_ids':[chain['evidence']['object_id']],
        'stage':'research','purpose':'research','include_drafts':True,'valid_at':now,'known_at':now})
    snapshot_id=context['context_snapshot_id']
    check('agent_intake_context_uses_own_running_run',bool(context['selected']))
    # A non-DRI human can be the explicitly confirmed Mission Owner.
    other=flow.draft_targets(chain['strategy'],ltco)
    content=deepcopy(other['mission_payload']);content['owner_principal_id']=f['actors']['unrelated']['principal_id']
    other['mission']=exact(flow.act('co_agent','m1b_revise_mission',{'payload':content},oid=other['mission']['object_id'])['result'])
    other['mission_payload']=flow.object(other['mission']['object_id'])['latest_revision']['payload']
    flow.open_window(other);flow.close_window(other);flow.resolve(other);flow.confirm_candidates(other)
    non_dri_payload={**payload,'subject_ref':other['mission'],'baseline_refs':[other['mission']]}
    non_dri_state=exact(flow.act('co_agent','method_propose_state',{'domain_id':f['domains']['company'],'payload':non_dri_payload})['result'])
    confirm=flow.prepare('unrelated','method_confirm_state',{'reason':'Confirmed Mission Owner, without a DRI role'},oid=non_dri_state['object_id'])
    first,repeated,proof=_race(flow,'unrelated',confirm,'unrelated',confirm)
    check('non_dri_owner_and_concurrent_idempotent_confirmation',first==repeated and proof['database_lock_observed'])
    flow.clients['unrelated'].json('GET','/v1/workspaces/dri?collection=operating-states')
    check('non_dri_owner_can_load_personal_state_work_surface')
    # Ordinary closure is human-attributed and distinct from M1A transfer.
    pp={'state_ref':non_dri_state,'core_question':'Is the evidence quality concern resolved?',
        'statement':'Quality needs an accountable judgment','why_material':'Pilot criteria depend on source quality',
        'level':'mission','responsible_assignment_id':f['actors']['unrelated']['assignment_id'],'evidence_refs':[chain['evidence']]}
    problem=exact(flow.act('co_agent','method_open_problem',{'domain_id':f['domains']['company'],'payload':pp})['result'])
    close={'disposition':'no_further_action','reason':'Owner has checked this concern','evidence_refs':[chain['evidence']]}
    flow.deny('co_agent',flow.command('method_close_problem',close,oid=problem['object_id']),codes={'FORBIDDEN'})
    flow.act('unrelated','method_close_problem',close,oid=problem['object_id'])
    check('ordinary_problem_closure_requires_bound_human',flow.object(problem['object_id'])['method_state']['phase']=='no_further_action')
    pending_targets=flow.targets(chain['strategy'],ltco,confirm=False)
    # Related DRI proposes; CEO confirms the exact current Architecture version.
    arch=flow.object(chain['strategy']['object_id'])['method_state']['architecture_ref']
    architecture=deepcopy(flow.object(arch['object_id'])['latest_revision']['payload']);architecture.pop('source_proposal_ref',None)
    invalid=deepcopy(architecture);invalid['units'][0]['unit_type']='capability'
    flow.deny('ceo_agent',flow.command('method_revise_architecture',{'payload':invalid},oid=arch['object_id']),codes={'INVALID_REQUEST'})
    architecture['units'][0]['boundary']='Pilot value responsibility, with explicit interface'
    flow.act('a','method_revise_architecture',{'payload':architecture},oid=arch['object_id'])
    stale=flow.prepare('ceo','method_confirm_architecture',{'reason':'Confirm proposed responsibility boundary'},oid=arch['object_id'])
    architecture['units'][0]['boundary']='Pilot value responsibility; clarify input ownership'
    flow.act('a','method_revise_architecture',{'payload':architecture},oid=arch['object_id'])
    flow.deny('ceo',stale,codes={'STALE_DEPENDENCY','VERSION_CONFLICT'},prepare=False)
    flow.act('ceo','method_confirm_architecture',{'reason':'Confirm the latest boundary revision'},oid=arch['object_id'])
    check('architecture_related_dri_proposal_ceo_confirmation_and_cas')
    check('historical_mission_keeps_original_architecture',flow.object(targets['mission']['object_id'])['effective_revision']['payload']['architecture_ref']==arch)
    # Strategy and Architecture update together, without partial activation.
    second=flow.start_issue();flow.clarify(second);flow.report(second);flow.meeting(second)
    flow.update(second,strategy_ref=chain['strategy'],confirm=False)
    _fail_after_write(flow,'ceo',second['confirm_command'],'after_method_strategy_write')
    check('paired_strategy_architecture_failure_rolls_back_all_tables')
    update_receipt=flow.commit('ceo',second['confirm_command'])
    new_strategy=update_receipt['result']['changed_refs'][0]
    check('old_effective_mission_retained_after_strategy_change',flow.object(targets['mission']['object_id'])['effective_revision']['revision_id']==targets['mission']['revision_id'])
    old_confirmation=flow.command('m1b_confirm_candidates',{'reason':'Try old Strategy candidate'},oid=pending_targets['candidate']['object_id'])
    flow.deny('ceo',old_confirmation,codes={'STALE_DEPENDENCY'})
    new_ltco=flow.ltco(new_strategy)['ltco']
    reopened=flow.act('ceo','m1b_reopen_candidates',{'reason':'Explicitly adopt the new Strategy and LTCO','title':'Rebased window',
        'rebase_strategy_ref':new_strategy,'rebase_ltco_ref':new_ltco},oid=pending_targets['candidate']['object_id'])['result']
    pending_targets['window']=exact(reopened)
    pending_targets['strategy']=new_strategy;pending_targets['ltco']=new_ltco
    pending_targets['pco_payload'].update(strategy_ref=new_strategy,ltco_ref=new_ltco)
    flow.close_window(pending_targets);flow.resolve(pending_targets);flow.confirm_candidates(pending_targets)
    check('old_strategy_candidate_rejected_and_explicit_reopen_adopts_new_basis')
    # Restart after a committed response: replay returns the original immutable receipt.
    flow.close();h.stop(process)
    process,url,_=h.start_api(source);flow.clients=h.clients(url,f);flow.url=url
    check('restart_recovers_original_receipt',flow.commit('ceo_agent',body)==receipt)
    from acceptance.method_independent.fixture import register_method
    from acceptance.method_independent.m1b_flow import MethodFlow
    from acceptance.lifecycle_v02.fixture import register_v02
    from acceptance.lifecycle_v02.run import Flow as V02Flow
    from .fixture import register_v03
    register_method(h,source,f)
    legacy=MethodFlow(h,url,f);old1=legacy.start_issue()
    register_v02(h,source,f)
    v02=V02Flow(h,url,f);old2=v02.start_issue()
    register_v03(h,source,f)
    legacy.clarify(old1);v02.clarify(old2)
    check('v01_and_v02_still_writable_after_v03_registration')
    bad=flow.command('m1a_open_potential_issue',{'domain_id':f['domains']['company'],
        'payload':{'title':'Forbidden cross-version source','summary':'Exact old source','core_question':'Cross-version?',
        'business_scope':'strategic','urgency':'yellow','source_refs':[old2['signals'][0]]}})
    flow.deny('ceo_agent',bad,codes={'PROTOCOL_BINDING_CONFLICT'})
    check('v03_rejects_cross_version_sources')
    legacy.close();v02.close()
    # Revoke after prepare; neither the late write nor the historical snapshot revives authority.
    recommendation=exact(flow.act('co_agent','method_propose_state',{'domain_id':f['domains']['company'],
        'payload':{**payload,'generation_version':'controlled-late'},'previous_state_ref':state})['result'])
    pending=flow.prepare('a','method_confirm_state',{'reason':'Late confirmation after revocation'},oid=state['object_id'])
    assignments=h.sql(f,'SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND active',(f['scope_id'],f['actors']['a']['principal_id']))
    for assignment in assignments:
        flow.clients['ceo'].json('POST','/v1/actions',Client.command('revoke_assignment',{'assignment_id':str(assignment['assignment_id'])}))
    flow.deny('a',pending,codes={'FORBIDDEN','NOT_FOUND'},prepare=False)
    check('human_revocation_rejects_late_prepared_confirmation')
    flow.clients['ceo'].json('POST','/v1/actions',Client.command('revoke_assignment',{'assignment_id':f['actors']['ceo_agent']['assignment_id']}))
    flow.deny('ceo_agent',body,codes={'FORBIDDEN','NOT_FOUND'},prepare=False)
    flow.clients['ceo_agent'].json('GET','/v1/method/research-context-packs/'+snapshot_id,expected={403,404})
    check('agent_revocation_blocks_replay_and_context_recovery')
    check('physical_state_identity_and_canonical_pointer',len(flow.rows('gov_method_state_keys'))==len([r for r in flow.rows('gov_objects') if r['object_type']=='OperatingState']))
