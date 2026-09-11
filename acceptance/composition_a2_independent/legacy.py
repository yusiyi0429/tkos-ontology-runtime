"""Capture a real A1 delivery history, then verify it after the A2 upgrade.

This runs only on the newly created A2 acceptance database. Existing demo data
is neither selected nor changed. No migration happens in this module.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import uuid

from acceptance.protocol_a1_independent.support import Harness,HERE,public_json,source_manifest
from acceptance.protocol_a1_independent.legacy_flow import LegacyFlow
from acceptance.protocol_a1_independent.history import historic_sql,old_subset


def capture(args):
    h=Harness(args.env_file,args.output,args.private)
    if not h.env.values['DATABASE_URL'] or 'a2_' not in h.env.values['DATABASE_URL']:
        raise ValueError('explicit A2 acceptance database required')
    fixture_file=args.private/'legacy-fixture.json'
    flow=None
    try:
        proc=h.spawn('a1-history-seed',[sys.executable,'-I',str(HERE/'source_process.py'),'seed',
            '--source',str(args.source.resolve()),'--out',str(fixture_file.resolve()),'--namespace',
            'runtime-acceptance-a2-history-'+uuid.uuid4().hex[:10]],owner=True)
        proc.wait(timeout=30);assert proc.returncode==0
        f=json.loads(fixture_file.read_text())
        _,url,ready=h.start_api(args.source,updates={'MEMORY_TENANT':f['tenant_id'],'MEMORY_ORG':f['company_id']})
        flow=LegacyFlow(h,url,f);result=flow.build(include_negatives=True)
        history=flow.capture()
        history['context_snapshot']=flow.ceo.json('GET','/v1/context-packs/'+result['snapshot_id'])
        record={'source_kind':'real_legacy_API_from_pinned_source','scope_id':f['scope_id'],
                'fixture_file':str(fixture_file),'source_manifest':source_manifest(args.source),
                'api_provenance':json.loads(ready.read_text()),'flow':result,'history':history}
        public_json(args.output/'pre-a2-history.json',record)
        return {'captured':True,'http_commands':len(flow.commands),'history':str(args.output/'pre-a2-history.json'),
                'delivery':'delivery_accepted','outcome':'not_assessed','MF':'investigating'}
    finally:
        if flow:flow.close()
        h.close()


def verify(args):
    h=Harness(args.env_file,args.output,args.private);flow=None
    document=json.loads(args.history.read_text());f=json.loads(args.fixture.read_text())
    before=document['history'];assert document['scope_id']==f['scope_id']
    try:
        _,url,ready=h.start_api(args.source,updates={'MEMORY_TENANT':f['tenant_id'],'MEMORY_ORG':f['company_id']})
        flow=LegacyFlow(h,url,f)
        actual=historic_sql(h,f,before['sql_snapshot']);checks=[]
        for name,old in before['sql_snapshot']['tables'].items():
            assert actual[name]['sha256']==old['sha256'] and actual[name]['row_count']==old['row_count']
            checks.append('unchanged_table:'+name)
        for oid,old in before['objects'].items():
            old_subset(old,flow.ceo.object(oid));checks.append('object:'+oid)
        for rid,old in before['revisions'].items():
            old_subset(old,flow.ceo.revision(old['object_id'],rid));checks.append('revision:'+rid)
        for rid,old in before['receipts'].items():
            old_subset(old,flow.ceo.json('GET','/v1/action-receipts/'+rid));checks.append('receipt:'+rid)
        old_subset(before['context_snapshot'],flow.ceo.json('GET','/v1/context-packs/'+document['flow']['snapshot_id']))
        stable=h.snapshot(f)
        for entry in before['commands']:
            assert flow.clients[entry['actor']].json('POST','/v1/actions',entry['request'])==entry['receipt']
            checks.append('replay:'+entry['receipt']['receipt_id'])
        assert h.snapshot(f)==stable
        assert h.storage_snapshot(f['scope_id'])==before['s3_snapshot']
        for evidence in before['evidence']:
            data=flow.ceo.request('GET',f"/v1/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}").content
            assert data.hex()==evidence['bytes_hex'];checks.append('source_bytes:'+evidence['revision_id'])
        result={'legacy_A1_history_preserved':True,'checks':checks,'passed':len(checks),
                'api_provenance':json.loads(ready.read_text()),'source_manifest':source_manifest(args.source),
                'replay_business_state_unchanged':True,'original_storage_unchanged':True}
        public_json(args.output/'legacy-preservation.json',result)
        return {k:v for k,v in result.items() if k not in {'checks','source_manifest'}}
    finally:
        if flow:flow.close()
        h.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['capture','verify'])
    for name in ['env-file','source','output','private','history','fixture']:
        p.add_argument('--'+name,type=Path,required=name in {'env-file','source','output','private'})
    args=p.parse_args()
    print(json.dumps(capture(args) if args.mode=='capture' else verify(args)))

if __name__=='__main__':main()
