"""Only synthetic identities and policies are bootstrapped, as the DB owner.

No business objects, source observations, compositions, signatures, commitments,
activations or successful action receipts are inserted by this setup. All those
must come from authenticated HTTP. Secrets stay in a private fixture file.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import secrets
import uuid

import psycopg
from psycopg.types.json import Jsonb

from acceptance.protocol_a1_independent.support import Environment, private_json, public_json
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter

A_CONTRACT = 'tkos.contract-a/0.1'
A_PROTOCOL = 'tkos.contract-a'
PROFILE = {'profile_id':'urn:tkos:experimental:method-profile:contract-a','revision':'0.1.0',
           'canonical_hash':'5050d542cc521991f17932562c4397331c0482b673f9bcd8fe694749bd43fc76'}
ACTIONS = ['open_formation_round','amend_formation_round','publish_domain_submission',
           'form_company_composition','confirm_company_composition','activate_company_composition']


def uid(): return str(uuid.uuid4())


def seed_authority(env: Environment, path: Path, label: str, *, b_seconds: float | None = None) -> dict:
    if path.exists():
        raise ValueError('fixture must be a fresh private file')
    if not label.startswith('runtime-acceptance-'):
        raise ValueError('synthetic acceptance namespace required')
    domains = {name:uid() for name in ('company','a','b','c','outsider')}
    f={'scope_id':uid(),'tenant_id':label+'-'+uuid.uuid4().hex[:8],'company_id':uid(),
       'period_id':uid(),'domains':domains,'actors':{},'record_origin':'synthetic'}
    roles = ['CEO','DOMAIN_DRI','MISSION_DRI','VERIFIER','IC','AGENT']
    action_roles = {'read':roles,'upload_evidence':roles,
                    'create_object':['CEO','DOMAIN_DRI'],'propose_revision':['CEO','DOMAIN_DRI'],
                    'revoke_assignment':['CEO'],
                    'open_formation_round':['CEO'],'amend_formation_round':['CEO'],
                    'publish_domain_submission':['DOMAIN_DRI'],
                    'form_company_composition':['CEO'],
                    'confirm_company_composition':['CEO','DOMAIN_DRI'],
                    'activate_company_composition':['CEO'],
                    # Authorizing an unsupported protocol action must not enable it.
                    'accept_commitment':['CEO','DOMAIN_DRI','IC'],
                    'activate_commitment':['CEO','DOMAIN_DRI'],
                    'confirm_adjustment':['CEO','DOMAIN_DRI'],
                    'confirm_outcome':['CEO']}
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)",(f['scope_id'],))
        assert conn.execute('SELECT gov_control_plane_on()').fetchone()[0] is True
        conn.execute('INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES (%s,%s,%s)',
                     (f['scope_id'],f['tenant_id'],f['company_id']))
        for name,domain in domains.items():
            conn.execute('INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)',
                         (domain,f['scope_id'],'Synthetic A2 '+name))
        actors=[('ceo','CEO','company','human'),('a','DOMAIN_DRI','a','human'),
                ('b','DOMAIN_DRI','b','human'),('c','DOMAIN_DRI','c','human'),
                ('wrong_b','DOMAIN_DRI','b','human'),('unrelated','IC','company','human'),
                ('agent','DOMAIN_DRI','a','agent'),('outsider','DOMAIN_DRI','outsider','human')]
        for name,role,domain,ptype in actors:
            principal,assignment,token=uid(),uid(),secrets.token_urlsafe(48)
            expiry = datetime.now(timezone.utc)+timedelta(seconds=b_seconds) if name=='b' and b_seconds else None
            conn.execute('INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)',
                         (principal,f['scope_id'],ptype,'Synthetic A2 '+name))
            conn.execute('''INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role,valid_to)
                         VALUES (%s,%s,%s,%s,%s,%s)''',(assignment,f['scope_id'],principal,domains[domain],role,expiry))
            hashed=hashlib.sha256(token.encode()).hexdigest()
            conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)",(hashed,))
            conn.execute('INSERT INTO gov_credentials(scope_id,principal_id,credential_digest,label) VALUES (%s,%s,%s,%s)',
                         (f['scope_id'],principal,hashed,'Synthetic A2 '+name))
            f['actors'][name]={'principal_id':principal,'assignment_id':assignment,'token':token,
                              'domain_id':domains[domain],'role':role,'valid_to':expiry.isoformat() if expiry else None}
        f['ceo_domain_assignments']={}
        for name in ('a','b','c','outsider'):
            assignment=uid()
            conn.execute('INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)',
                         (assignment,f['scope_id'],f['actors']['ceo']['principal_id'],domains[name],'CEO'))
            f['ceo_domain_assignments'][name]=assignment
        # Additional DRI assignment for the SAME CEO is a negative distinct-person fixture.
        f['ceo_as_a_dri_assignment']=uid()
        conn.execute('INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)',
                     (f['ceo_as_a_dri_assignment'],f['scope_id'],f['actors']['ceo']['principal_id'],domains['a'],'DOMAIN_DRI'))
        # Same person retains ordinary own-domain read after the named DRI is
        # revoked. This must not preserve their cross-domain participant grant.
        f['a_secondary_ic_assignment']=uid()
        conn.execute('INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)',
                     (f['a_secondary_ic_assignment'],f['scope_id'],f['actors']['a']['principal_id'],domains['a'],'IC'))
        for domain in domains.values():
            policy={'version':'tkos.governed-policy/0.2','action_roles':action_roles,
                    'commitment_party_roles':{'BusinessCommitment':['CEO','DOMAIN_DRI'],
                                             'ExecutionCommitment':['DOMAIN_DRI','MISSION_DRI']},
                    'independent_verifier':True}
            conn.execute('''INSERT INTO gov_activation_policies(policy_revision_id,scope_id,domain_id,policy_id,
                         policy_seq,content,recorded_by) VALUES (%s,%s,%s,%s,1,%s,%s)''',
                         (uid(),f['scope_id'],domain,uid(),Jsonb(policy),f['actors']['ceo']['principal_id']))
        conn.execute("SELECT set_config('app.governed_credential_digest','',true)")
    private_json(path,f)
    return f


def withdraw_domain_read_policy(env: Environment, f: dict, domain_name: str):
    """Synthetic owner policy change; no business rows or receipts are seeded.

    Use the same scope row fence as Runtime authentication. A later policy
    revision must replace, rather than union with, an earlier read grant.
    """
    from psycopg.rows import dict_row
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'],row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)",(f['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE',(f['scope_id'],))
        old=conn.execute('''SELECT * FROM gov_activation_policies
            WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1''',
            (f['scope_id'],f['domains'][domain_name])).fetchone()
        content=dict(old['content']);content['action_roles']=dict(content['action_roles'])
        content['action_roles']['read']=[]
        conn.execute('''INSERT INTO gov_activation_policies(policy_revision_id,scope_id,domain_id,
            policy_id,policy_seq,content,recorded_by) VALUES (%s,%s,%s,%s,%s,%s,%s)''',
            (uid(),f['scope_id'],f['domains'][domain_name],old['policy_id'],old['policy_seq']+1,
             Jsonb(content),f['actors']['ceo']['principal_id']))
        conn.execute('UPDATE gov_scopes SET auth_epoch=auth_epoch+1 WHERE scope_id=%s',(f['scope_id'],))


def register_contract(h, source: Path, f: dict, registry: dict):
    adapter=ControlAdapter(h,source,'memory_service_runtime.governed.control')
    tag=uuid.uuid4().hex[:10]
    common=['--scope-id',f['scope_id'],'--reason','Synthetic A2 independent scope registration']
    adapter.cli('a2-install-'+tag,['install-profile',*common],expected_exit=0)
    policy={'default_protocol':A_PROTOCOL,'default_contract_version':A_CONTRACT,
            'allow_legacy_create':False,'record_origin':'synthetic',
            'default_profile_ref':{k:PROFILE[k] for k in ('profile_id','revision')},
            'experimental':True,'notes':'Synthetic A2 independent test scope; no production authorization'}
    policy_file=h.private/('policy-'+tag+'.json');private_json(policy_file,policy)
    adapter.cli('a2-policy-'+tag,['install-policy',*common,'--content-json',str(policy_file)],expected_exit=0)
    registry_file=h.private/('registry-'+tag+'.json');private_json(registry_file,registry)
    adapter.cli('a2-registry-'+tag,['set-registry',*common,'--protocol-id',A_PROTOCOL,
                '--contract-version',A_CONTRACT,'--content-json',str(registry_file)],expected_exit=0)
