"""Independent client for the reviewed A2 HTTP engineering mapping.

It uses only public HTTP and its own action receipts for business progress. SQL
is an observer, never a substitute for the business transitions tested here.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import uuid

from acceptance.runtime.client import Client
from .fixture import A_CONTRACT,PROFILE


def iso_now():return datetime.now(timezone.utc).isoformat()


class Flow:
    def __init__(self,h,url,f):
        self.h,self.url,self.f=h,url,f
        self.clients=h.clients(url,f)
        self.round_id=None;self.company_ref=None;self.capacity_ref=None
        self.capacity_payload=None;self.resource_id=str(uuid.uuid4())
        self.members=['a','b'];self.member_version=1;self.input_version=1
        self.submissions={};self.missions={};self.compositions={}
        self.commands=[];self.receipts=[]

    def close(self):
        for client in self.clients.values():client.close()

    def object(self,oid,actor='ceo'):return self.clients[actor].object(oid)

    def target(self,oid,actor='ceo'):
        obj=self.object(oid,actor)
        return {'object_id':oid,'revision_id':obj['latest_revision_id'],'expected_version':obj['object_version']}

    def ref(self,oid,actor='ceo'):
        obj=self.object(oid,actor)
        revision=obj['effective_revision'] or obj['latest_revision']
        return {'object_id':oid,'revision_id':revision['revision_id'],'payload_hash':revision['payload_hash']}

    def command(self,kind,params,*,oid=None,actor='ceo',key=None):
        body=Client.command(kind,deepcopy(params),target=self.target(oid,actor) if oid else None,key=key)
        body['contract_version']=A_CONTRACT
        return body

    def prepare(self,actor,kind,params,*,oid=None,key=None):
        body=self.command(kind,params,oid=oid,actor=actor,key=key)
        prepared=self.clients[actor].json('POST','/v1/actions/prepare',body)
        body['expected_versions']=prepared['expected_versions']
        if prepared['target']:
            assert prepared['target']['revision_id']==body['target']['revision_id']
            body['target']['expected_version']=prepared['target']['expected_version']
        return body

    def commit(self,actor,body,*,headers=None):
        receipt=self.clients[actor].json('POST','/v1/actions',body,headers=headers)
        self.commands.append({'actor':actor,'request':deepcopy(body),'receipt':deepcopy(receipt)})
        self.receipts.append(receipt)
        assert receipt['status']=='committed' and receipt['effect_task_ids']==[]
        return receipt

    def act(self,actor,kind,params,*,oid=None,key=None):
        return self.commit(actor,self.prepare(actor,kind,params,oid=oid,key=key))

    def current_known_versions(self,exclude=None):
        """Refresh versions of objects already returned by our own HTTP receipts.

        Useful for deliberately executing a non-ready candidate when prepare
        itself refuses it. No hidden rows or owner SQL supply the command.
        """
        ids={v['object_id'] for r in self.receipts for v in r['object_versions']}
        return [{'object_id':oid,'expected_version':self.object(oid)['object_version']}
                for oid in sorted(ids) if oid!=exclude]

    def share_domains(self):return [self.f['domains'][n] for n in ('company','a','b','c')]

    def create_reference(self,*,title='Synthetic company result for P01',upstream_refs=None,shared=True):
        payload={'title':title,'statement':'Synthetic target: three distinct onboarded pilot customers',
            'period_id':self.f['period_id'],'terms':{'synthetic':True,'target':3},
            'upstream_refs':upstream_refs or [],'shared_with_domain_ids':self.share_domains() if shared else []}
        receipt=self.act('ceo','create_object',{'object_type':'CompanyReference',
            'domain_id':self.f['domains']['company'],'payload':payload})
        return self.ref(receipt['result']['object_id'])

    def create_capacity(self,available=3,*,reserved=0,shared=True,upstream_refs=None):
        now=datetime.now(timezone.utc)
        payload={'title':'Synthetic capacity source R1','resource_id':self.resource_id,
            'period_id':self.f['period_id'],'unit':'synthetic_onboarding_slot','available':available,
            'reserved':reserved,'valid_from':(now-timedelta(seconds=2)).isoformat(),
            'valid_to':(now+timedelta(hours=12)).isoformat(),'observed_at':now.isoformat(),
            'note':'Synthetic controlled observation, no real company data',
            'upstream_refs':upstream_refs or [],'shared_with_domain_ids':self.share_domains() if shared else []}
        receipt=self.act('b','create_object',{'object_type':'CapacityObservation',
            'domain_id':self.f['domains']['b'],'payload':payload})
        self.capacity_payload=payload;self.capacity_ref=self.ref(receipt['result']['object_id'])
        return self.capacity_ref

    def revise_capacity_body(self,available,*,actor='b',upstream_refs=None,reserved=None,note=None):
        payload=deepcopy(self.capacity_payload)
        payload.update(available=available,observed_at=iso_now(),title='Synthetic revised capacity')
        if upstream_refs is not None:payload['upstream_refs']=deepcopy(upstream_refs)
        if reserved is not None:payload['reserved']=reserved
        if note is not None:payload['note']=note
        body=self.prepare(actor,'propose_revision',{'payload':payload},oid=self.capacity_ref['object_id'])
        return body,payload

    def revise_capacity(self,available,*,actor='b',upstream_refs=None,reserved=None):
        body,payload=self.revise_capacity_body(available,actor=actor,upstream_refs=upstream_refs,reserved=reserved)
        self.commit(actor,body)
        self.capacity_payload=payload;self.capacity_ref=self.ref(self.capacity_ref['object_id'])
        return self.capacity_ref

    def member_rows(self,names=None):
        return [{'domain_id':self.f['domains'][name],
                 'dri_assignment_id':self.f['actors'][name]['assignment_id']} for name in names or self.members]

    def open_params(self):
        return {'company_id':self.f['company_id'],'company_domain_id':self.f['domains']['company'],
            'ceo_assignment_id':self.f['actors']['ceo']['assignment_id'],'period_id':self.f['period_id'],
            'period_window':{'start':'2026-10-01T00:00:00+08:00','end':'2026-10-15T00:00:00+08:00'},
            'method_profile_ref':{k:PROFILE[k] for k in ('profile_id','revision')},
            'company_reference_ref':self.company_ref,'members':self.member_rows()}

    def open(self):
        receipt=self.act('ceo','open_formation_round',self.open_params())
        result=receipt['result'];self.round_id=result['round_object_id']
        self.member_version=result['member_set_version'];self.input_version=result['input_set_version']
        return receipt

    def amend_params(self,names,*,company_ref=None):
        return {'expected_member_set_version':self.member_version,'members':self.member_rows(names),
            'company_reference_ref':company_ref or self.company_ref,
            'change_reason':'Synthetic controlled membership or reference amendment'}

    def amend(self,names,*,company_ref=None):
        receipt=self.act('ceo','amend_formation_round',self.amend_params(names,company_ref=company_ref),oid=self.round_id)
        self.members=list(names);self.member_version=receipt['result']['member_set_version']
        self.input_version=receipt['result']['input_set_version']
        if company_ref:self.company_ref=company_ref
        return receipt

    def submission_params(self,name,*,required=None,mission_keys=None,source_ref=None):
        required=(3 if name=='a' else 0) if required is None else required
        pool={'resource_id':self.resource_id,'period_id':self.f['period_id'],'unit':'synthetic_onboarding_slot'}
        mission_keys=mission_keys or [name+'-mission']
        return {'domain_id':self.f['domains'][name],'dri_assignment_id':self.f['actors'][name]['assignment_id'],
            'draft_checkpoint':'synthetic-local-'+uuid.uuid4().hex,
            'commitment_statement':'I personally publish and confirm this exact synthetic submission as designated DRI.',
            'submission':{'result_statement':'Synthetic '+name+' responsibility result',
                'pdo':{'pdo_key':name+'-pdo','statement':'Synthetic result within P01',
                       'result_criteria':[{'criterion_id':'result','description':'Three distinct activated customers'}]},
                'missions':[{'mission_key':key,'result_statement':'Synthetic '+key+' result',
                    'boundary':'No expansion of the formal result or independent execution authority',
                    'acceptance_criteria':[{'criterion_id':'proof','description':'Exact traceable source'}],
                    'dependency_refs':[]} for key in mission_keys],
                'resources':[{**pool,'required':required}],
                'bindings':([{'relation_type':'resource_capacity','source_ref':source_ref or self.capacity_ref,**pool}] if name=='b' or source_ref is not None else []),
                'upstream_refs':[],'unknowns':[]}}

    def publish(self,name,**kwargs):
        params=self.submission_params(name,**kwargs)
        receipt=self.act(name,'publish_domain_submission',params,oid=self.round_id)
        result=receipt['result'];self.submissions[name]={'object_id':result['submission_object_id'],
            'revision_id':result['submission_revision_id'],'payload_hash':result['payload_hash']}
        self.missions[name]=result['missions'];self.input_version=result['input_set_version']
        return receipt

    def form_params(self,*,conclusions=None,conflicts=None,extra_evidence=None):
        judgments={name:{'conclusion':(conclusions or {}).get(name,'pass'),
            'reason':'CEO independent synthetic '+name+' review against exact current sources',
            'evidence_refs':[self.company_ref,self.capacity_ref,*(extra_evidence or [])]}
            for name in ('coverage','coherence','feasibility','tradeoff')}
        return {'expected_member_set_version':self.member_version,'expected_input_set_version':self.input_version,
                'judgments':judgments,'unresolved_conflicts':conflicts or []}

    def form(self,**kwargs):
        receipt=self.act('ceo','form_company_composition',self.form_params(**kwargs),oid=self.round_id)
        result=receipt['result'];oid=result['composition_object_id']
        ref={'object_id':oid,'revision_id':result['composition_revision_id'],'manifest_hash':result['manifest_hash']}
        self.compositions[oid]=ref
        return ref

    def manifest(self,composition,actor='ceo'):
        obj=self.object(composition['object_id'],actor)
        payload=obj['latest_revision']['payload']
        assert payload['manifest_hash']==composition['manifest_hash']
        return payload

    def confirm_params(self,composition,actor):
        return {'composition_ref':composition,'assignment_id':self.f['actors'][actor]['assignment_id'],
            'confirmation_statement':'I read and personally confirm the entire exact published composition.'}

    def confirm(self,composition,actor):
        return self.act(actor,'confirm_company_composition',self.confirm_params(composition,actor),oid=composition['object_id'])

    def sign_all(self,composition,actors=None):
        return [self.confirm(composition,actor) for actor in actors or ['ceo',*self.members]]

    def activation_params(self,composition):
        return {'composition_ref':composition,'expected_member_set_version':self.member_version,
                'expected_input_set_version':self.input_version}

    def activation_body(self,composition):
        return self.prepare('ceo','activate_company_composition',self.activation_params(composition),oid=composition['object_id'])

    def activate(self,composition):return self.commit('ceo',self.activation_body(composition))

    def build(self,*,available=3,sign=True):
        self.company_ref=self.create_reference();self.create_capacity(available);self.open()
        for name in self.members:self.publish(name)
        composition=self.form()
        if sign:self.sign_all(composition)
        return composition
