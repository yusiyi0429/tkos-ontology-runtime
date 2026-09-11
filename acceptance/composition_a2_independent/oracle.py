"""Implementation-independent content and SQL assertions for A2.

The production serializer, policy interpreter and composition validator are
never imported. Expected participants, inputs and totals come from test actions,
not from implementation-provided expected/pass labels.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from uuid import UUID

from .fixture import PROFILE

MANIFEST_KEYS = {'manifest_schema_version','scope_id','company_id','round_id','period_id',
    'method_profile_ref','member_set_version','input_set_version','company_reference_ref',
    'members','binding_dependencies','judgments','required_signers','hash_scheme','manifest_hash'}


def canonical_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def canonical_uuid(value):
    assert isinstance(value,str) and str(UUID(value))==value, 'noncanonical UUID in signed content'


def exact_ref(value):
    assert set(value)=={'object_id','revision_id','payload_hash'}
    canonical_uuid(value['object_id']);canonical_uuid(value['revision_id'])
    assert isinstance(value['payload_hash'],str) and len(value['payload_hash'])==64
    assert all(c in '0123456789abcdef' for c in value['payload_hash'])


def manifest(content: dict, fixture: dict, *, round_id: str, company_ref: dict,
             submissions: dict[str,dict], resource_id: str, source_ref: dict,
             required: int, available: int, member_version: int | None = None,
             input_version: int | None = None):
    assert set(content)==MANIFEST_KEYS, 'manifest shape diverges from frozen contract'
    expected = {k:fixture[k] for k in ('scope_id','company_id','period_id')}
    expected.update(round_id=round_id,manifest_schema_version='tkos.composition-manifest/0.1',
                    hash_scheme='tkos-json-v1',method_profile_ref=PROFILE,company_reference_ref=company_ref)
    for key,value in expected.items(): assert content[key]==value, 'manifest differs: '+key
    for key in ('member_set_version','input_set_version'):
        assert type(content[key]) is int and content[key]>=1
    if member_version is not None:assert content['member_set_version']==member_version
    if input_version is not None:assert content['input_set_version']==input_version
    without_hash=deepcopy(content);without_hash.pop('manifest_hash')
    assert canonical_hash(without_hash)==content['manifest_hash'], 'server manifest digest differs from independent UTF-8 bytes'
    expected_members=[{'domain_id':fixture['domains'][name],
        'dri_assignment_id':fixture['actors'][name]['assignment_id'],
        'dri_principal_id':fixture['actors'][name]['principal_id'],'submission_ref':ref}
        for name,ref in submissions.items()]
    assert content['members']==sorted(expected_members,key=lambda m:m['domain_id']), 'full participant/input set mismatch'
    expected_signers=[{'principal_id':fixture['actors'][name]['principal_id'],
        'assignment_id':fixture['actors'][name]['assignment_id'],
        'responsibility_role':'company_decider' if name=='ceo' else 'area_accountable'}
        for name in ['ceo',*submissions]]
    assert content['required_signers']==sorted(expected_signers,key=lambda s:s['principal_id']), 'required signer set mismatch'
    assert len({s['principal_id'] for s in content['required_signers']})==len(content['required_signers'])
    deps=content['binding_dependencies']
    assert deps==sorted(deps,key=lambda d:d['dependency_id'])
    assert len({d['dependency_id'] for d in deps})==len(deps)
    assert len(deps)==1, 'the single synthetic capacity pool was omitted or duplicated'
    dep=deps[0]
    assert set(dep)=={'dependency_id','relation_type','source_ref','constraint'}
    canonical_uuid(dep['dependency_id']);exact_ref(dep['source_ref'])
    assert dep['source_ref']==source_ref and dep['relation_type']=='resource_capacity'
    constraint=dep['constraint']
    assert constraint=={'kind':'capacity','resource_id':resource_id,'period_id':fixture['period_id'],
                        'unit':'synthetic_onboarding_slot','required':required,'available':available}
    assert type(constraint['required']) is int and type(constraint['available']) is int
    assert set(content['judgments'])=={'coverage','coherence','feasibility','tradeoff'}
    for judgment in content['judgments'].values():
        assert set(judgment)=={'conclusion','reason','evidence_refs','judge_principal_id'}
        assert judgment['conclusion'] in {'pass','fail','unknown'}
        assert isinstance(judgment['reason'],str) and judgment['reason'].strip()
        assert judgment['judge_principal_id']==fixture['actors']['ceo']['principal_id']
        for ref in judgment['evidence_refs']:exact_ref(ref)
    return {'manifest_hash':content['manifest_hash'],'member_count':len(expected_members),
            'required_signers':len(expected_signers),'required':required,'available':available,
            'source_revision_id':source_ref['revision_id'],'independent_digest_matches':True}


def immutable_history(h, f, refs: list[dict]):
    hashes=[]
    for ref in refs:
        rows=h.sql(f,'''SELECT revision_id::text,payload,payload_hash FROM gov_object_revisions
                       WHERE scope_id=%s AND object_id=%s AND revision_id=%s''',
                       (f['scope_id'],ref['object_id'],ref['revision_id']))
        assert len(rows)==1 and rows[0]['payload_hash']==ref['payload_hash']
        assert canonical_hash(rows[0]['payload'])==ref['payload_hash'], 'stored immutable payload hash mismatch'
        hashes.append(rows[0]['payload_hash'])
    return {'revisions_retained':len(hashes),'hashes':hashes}


def receipt_integrity(h,f,receipt,*, action: str):
    assert receipt['action_type']==action and receipt['status']=='committed'
    assert receipt['effect_task_ids']==[], 'A2 must not dispatch external execution'
    rows=h.sql(f,'''SELECT principal_id::text,result,object_versions,effect_task_ids FROM gov_action_receipts
                   WHERE scope_id=%s AND receipt_id=%s''',(f['scope_id'],receipt['receipt_id']))
    assert len(rows)==1
    row=rows[0]
    assert row['principal_id']==receipt['actor_id']
    assert row['result']==receipt['result'] and row['object_versions']==receipt['object_versions']
    assert row['effect_task_ids']==[]
    for changed in receipt['object_versions']:
        result=h.sql(f,'SELECT object_version FROM gov_objects WHERE scope_id=%s AND object_id=%s',
                     (f['scope_id'],changed['object_id']))
        assert len(result)==1 and result[0]['object_version']==changed['object_version']
    tasks=h.sql(f,'SELECT count(*) AS n FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s',
                (f['tenant_id'],f['company_id']))
    assert tasks[0]['n']==0,'A2 produced an external dispatch task'
    return {'receipt_id':receipt['receipt_id'],'object_versions':receipt['object_versions'],
            'effect_task_ids':[],'outbox_dispatch_tasks':0}
