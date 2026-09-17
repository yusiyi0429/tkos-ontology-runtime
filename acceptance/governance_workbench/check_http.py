"""Independent HTTP/SQL acceptance against a running local workbench."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import httpx
from psycopg.rows import dict_row
import psycopg

from acceptance.anchors_v03.run import Flow
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import private_json, public_json


def run(url, env_file, private):
    f = json.loads((private/'identities.json').read_text())
    original = json.loads((private/'state.json').read_text())
    h = MethodHarness(env_file, private/'api-checks/http', private/'api-checks/process')
    flow = Flow(h, url, f)
    checks=[]
    def check(name, condition=True):
        assert condition, name
        checks.append(name)
        print('PASS '+name, flush=True)
    browsers={}
    csrf={}
    prefix='/dashboard/api/v1'
    try:
        for name in ['ceo','dri-a','dri-b']:
            c=httpx.Client(base_url=url,trust_env=False,timeout=30,headers={'Origin':url})
            response=c.post(prefix+'/session',json=json.loads((private/(name+'-login.json')).read_text()))
            assert response.status_code==200, 'login'
            csrf[name]=response.json()['csrf'];c.headers['X-CSRF-Token']=csrf[name];browsers[name]=c
            check('independent_session_'+name,'httponly' in response.headers['set-cookie'].lower())
        def call(actor,path,body=None,status=200):
            response=browsers[actor].get(prefix+path) if body is None else browsers[actor].post(prefix+path,json=body)
            assert response.status_code==status,(path,response.status_code)
            return response.json()
        def prepare(actor,body):return call(actor,'/commands/prepare',body)
        def commit(actor,c, retry=False):return call(actor,'/commands/'+c['command_id']+('/retry' if retry else '/commit'),{})
        targets=flow.open_window(flow.draft_targets(original['strategy'],original['ltco']))
        wid=targets['window']['object_id']
        w=call('ceo','/governance/review-windows/'+wid)
        check('no_initial_opinions_or_candidate',not w['monthly']['reviews'] and w['monthly']['candidate']['status']=='missing')
        denied=browsers['ceo'].post(prefix+'/commands/prepare',json={},headers={'X-CSRF-Token':'invalid'})
        check('csrf_denied',denied.status_code==403)
        check('cross_origin_denied',browsers['ceo'].get(prefix+'/governance/tasks',headers={'Origin':'https://invalid.example'}).status_code==403)
        seed={'contract_version':'tkos.workspace/0.1','scene_id':str(uuid4()),'expected_version':0,'idempotency_key':str(uuid4()),'event':{'kind':'create','scene_type':'monthly','anchor_ref':targets['window'],'external_id':'governance-http-'+str(uuid4()),'title':'HTTP acceptance monthly review','owner_assignment_id':f['actors']['ceo']['assignment_id'],'participant_assignment_ids':[f['actors'][a]['assignment_id'] for a in ['a','b']]}}
        c=prepare('ceo',seed); result=commit('ceo',c)
        check('monthly_scene_created_by_ceo',result['status']=='committed')
        call('dri-a','/commands/'+c['command_id'],status=404)
        check('private_command_cross_identity_hidden')
        def comment(actor, content, replaces=None):
            params={'target_ref':targets['pco'],'content':content}
            if replaces:params['replaces_record_id']=replaces
            body=flow.command('m1b_comment',params,oid=wid)
            c=prepare(actor,body)
            check('same_prepare_same_command',prepare(actor,body)['command_id']==c['command_id']) if not replaces and actor=='dri-a' else None
            return commit(actor,c),c
        result,c=comment('dri-a','Please clarify the source evidence')
        assert result['status']=='committed',result.get('error')
        first=result['receipt']['result']['review_record_id']
        again=commit('dri-a',c,True)
        check('committed_original_replay_same_receipt',again['receipt']['receipt_id']==result['receipt']['receipt_id'])
        result,_=comment('dri-a','Replace: include original evidence and quality criteria',first)
        second=result['receipt']['result']['review_record_id']
        result,_=comment('dri-b','Withdrawable second participant opinion')
        b=result['receipt']['result']['review_record_id']
        body=flow.command('m1b_withdraw_comment',{'review_record_id':b,'reason':'Personal withdrawal'},oid=wid)
        result=commit('dri-b',prepare('dri-b',body));assert result['status']=='committed'
        w=call('dri-a','/governance/review-windows/'+wid)
        check('replace_withdraw_history',w['monthly']['visible_effective_opinion_count']==1 and len(w['monthly']['reviews'])==4)
        scene=w['scenes'][0]
        anchor={**seed,'scene_id':scene['scene_id'],'expected_version':scene['version'],'idempotency_key':str(uuid4()),'event':{'kind':'comment_anchor','review_record_id':second,'target_ref':targets['pco'],'field_path':'/unit_outcomes/0/result_statement'}}
        assert commit('dri-a',prepare('dri-a',anchor))['status']=='committed'
        check('field_anchor_separate_receipt')
        stale=flow.command('m1b_comment',{'target_ref':targets['pco'],'content':'Stale prepared comment'},oid=wid)
        p=prepare('dri-a',stale)
        comment('dri-b','Concurrent participant opinion')
        assert commit('dri-a',p)['error']=='VERSION_CONFLICT'
        check('concurrent_comment_rejects_stale_commit')
        forbidden=flow.command('m1b_close_window',{'reason':'Forged human agent action'},oid=wid)
        call('ceo','/commands/prepare',forbidden,status=403)
        check('human_facade_cannot_call_agent_action')
        race=prepare('dri-a',flow.command('m1b_comment',{'target_ref':targets['pco'],'content':'Races with close'},oid=wid))
        flow.close_window(targets)
        rejected=commit('dri-a',race)
        check('close_wins_comment_race',rejected['status']=='rejected')
        context=flow.clients['co_agent'].json('POST','/v1/context-packs',{'contract_version':'tkos.method/0.3','object_ids':[wid,targets['pco']['object_id'],targets['mission']['object_id']],'valid_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'known_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'stage':'review','purpose':'analysis','include_drafts':True})
        flow.resolve(targets)
        w=call('ceo','/governance/review-windows/'+wid)
        check('complete_candidate_projection',w['monthly']['candidate']['status']=='available' and len(w['monthly']['candidate_targets'])==2)
        scene=w['scenes'][0]
        ack={**seed,'scene_id':scene['scene_id'],'expected_version':scene['version'],'idempotency_key':str(uuid4()),'event':{'kind':'diff_response','candidate_ref':targets['candidate'],'response':'reviewed'}}
        assert commit('dri-a',prepare('dri-a',ack))['status']=='committed'
        check('personal_ack_does_not_activate',flow.object(targets['candidate']['object_id'])['method_state']['phase']=='pending')
        body=flow.command('m1b_confirm_candidates',{'reason':'CEO confirms the complete exact candidate set'},oid=targets['candidate']['object_id'])
        call('dri-a','/commands/prepare',body,status=403)
        check('dri_cannot_confirm')
        prepared=prepare('ceo',body)
        done=commit('ceo',prepared)
        check('whole_set_confirmation',done['status']=='committed')
        # Independently forget the response locally and replay the exact core command;
        # no object or receipt is written directly by the observer.
        replay=flow.clients['ceo'].json('POST','/v1/actions',prepared['envelope'])
        check('response_loss_original_envelope_recovery',replay['receipt_id']==done['receipt']['receipt_id'])
        handoff=flow.clients['a'].json('GET','/v1/method/missions/'+targets['mission']['object_id']+'/handoff')
        check('formal_mission_no_execution',handoff['execution_authority'] is None and handoff['delivery_accepted'] is None and handoff['outcome_achieved'] is None)
        with psycopg.connect(h.env.values['MIGRATION_DATABASE_URL'],row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
            conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
            conn.execute("SELECT set_config('app.governed_scope_id',%s,true)",(f['scope_id'],))
            row=conn.execute('SELECT principal_id,action_type FROM gov_action_receipts WHERE receipt_id=%s',(done['receipt']['receipt_id'],)).fetchone()
            check('sql_receipt_human_ceo',str(row['principal_id'])==f['actors']['ceo']['principal_id'] and row['action_type']=='m1b_confirm_candidates')
        bad=browsers['dri-a'].delete(prefix+'/session');assert bad.status_code==200
        check('logout_invalidates_protected_reads',browsers['dri-a'].get(prefix+'/governance/tasks').status_code==401)
        public_json(private/'api-checks/summary.json',{'passed':len(checks),'checks':checks,'runtime_api':True,'browser':False,'real_model':False})
    finally:
        for c in browsers.values():c.close()
        h.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--url',required=True);p.add_argument('--env-file',type=Path,required=True);p.add_argument('--private',type=Path,required=True);a=p.parse_args()
    run(a.url,a.env_file,a.private)
