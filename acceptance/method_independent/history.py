"""Real preupgrade A2/A3 history, checked unchanged after Method schema upgrade."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from acceptance.execution_a3_independent.fixture import seed_authority, register_contract
from acceptance.execution_a3_independent.flow import Flow
from acceptance.protocol_a1_independent.history import historic_sql, old_subset
from acceptance.protocol_a1_independent.support import Harness, private_json, public_json, source_manifest

ROOT = Path(__file__).resolve().parents[2]


def capture(args):
    h = Harness(args.env_file, args.output, args.private)
    flow = None
    try:
        f = seed_authority(h.env, args.private / 'fixture.json', 'runtime-acceptance-method-preupgrade')
        register_contract(h, args.source, f, json.loads((ROOT / 'docs/runtime-a3-registry.json').read_text()))
        _, url, ready = h.start_api(args.source)
        flow = Flow(h, url, f).ready()
        v1 = flow.submit()
        returned = flow.review(v1)
        v2 = flow.submit(responds_to=returned['result']['delivery_acceptance_id'])
        accepted = flow.review(v2, accept=True)
        ids = {version['object_id'] for receipt in flow.receipts for version in receipt['object_versions']}
        objects = {oid: flow.object(oid) for oid in ids}
        revisions = {rev['revision_id']: flow.clients['ceo'].revision(oid, rev['revision_id'])
                     for oid, obj in objects.items()
                     for rev in (obj['latest_revision'], obj['effective_revision']) if rev}
        record = {
            'scope_id': f['scope_id'], 'source_manifest': source_manifest(args.source),
            'api_provenance': json.loads(ready.read_text()),
            'delivery': {'v1': v1, 'returned': returned, 'v2': v2, 'accepted': accepted},
            'objects': objects, 'revisions': revisions,
            'receipts': {receipt['receipt_id']: receipt for receipt in flow.receipts},
            'sql_snapshot': h.snapshot(f), 'storage_snapshot': h.storage_snapshot(f['scope_id']),
        }
        private_json(args.private / 'commands.json', flow.commands)
        public_json(args.output / 'history.json', record)
        return {'captured_real_A2_A3_delivery': True, 'commands': len(flow.commands),
                'objects': len(objects), 'revisions': len(revisions)}
    finally:
        if flow:
            flow.close()
        h.close()


def verify(args):
    record = json.loads(args.history.read_text())
    f = json.loads(args.fixture.read_text())
    commands = json.loads(args.commands.read_text())
    assert record['scope_id'] == f['scope_id']
    h = Harness(args.env_file, args.output, args.private)
    flow = None
    source_before = source_manifest(args.source)
    try:
        _, url, ready = h.start_api(args.source)
        flow = Flow(h, url, f)
        actual = historic_sql(h, f, record['sql_snapshot'])
        for table, expected in record['sql_snapshot']['tables'].items():
            assert actual[table]['sha256'] == expected['sha256']
            assert actual[table]['row_count'] == expected['row_count']
        for oid, obj in record['objects'].items():
            old_subset(obj, flow.object(oid))
        for rid, rev in record['revisions'].items():
            old_subset(rev, flow.clients['ceo'].revision(rev['object_id'], rid))
        for rid, receipt in record['receipts'].items():
            old_subset(receipt, flow.clients['ceo'].json('GET', '/v1/action-receipts/' + rid)['receipt'])
        before = h.snapshot(f)
        for command in commands:
            assert flow.clients[command['actor']].json('POST', '/v1/actions', command['request']) == command['receipt']
        assert before == h.snapshot(f)
        assert h.storage_snapshot(f['scope_id']) == record['storage_snapshot']
        source_after = source_manifest(args.source)
        assert source_before == source_after, 'Source changed during historical verification'
        result = {
            'A2_A3_history_preserved': True, 'objects': len(record['objects']),
            'revisions': len(record['revisions']), 'receipts_replayed': len(commands),
            'historic_tables': len(actual), 'replay_business_state_unchanged': True,
            'raw_evidence_history_unchanged': True,
            'api_provenance': json.loads(ready.read_text()), 'source_manifest': source_after,
            'source_unchanged': True,
        }
        public_json(args.output / 'history-preservation.json', result)
        return {key: value for key, value in result.items() if key not in {'source_manifest', 'api_provenance'}}
    finally:
        if flow:
            flow.close()
        h.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['capture', 'verify'])
    for name in ['env-file', 'source', 'output', 'private', 'history', 'fixture', 'commands']:
        parser.add_argument('--' + name, type=Path, required=name in {'env-file', 'source', 'output', 'private'})
    args = parser.parse_args()
    print(json.dumps(capture(args) if args.mode == 'capture' else verify(args)))


if __name__ == '__main__':
    main()
