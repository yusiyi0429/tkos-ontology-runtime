"""Preserve a genuine active A2 baseline across the A3 schema/code upgrade."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from acceptance.composition_a2_independent.fixture import seed_authority, register_contract
from acceptance.composition_a2_independent.flow import Flow
from acceptance.protocol_a1_independent.history import historic_sql, old_subset
from acceptance.protocol_a1_independent.support import (
    Harness, private_json, public_json, source_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def capture(args):
    h = Harness(args.env_file, args.output, args.private)
    flow = None
    try:
        fixture_path = args.private / 'fixture.json'
        f = seed_authority(h.env, fixture_path, 'runtime-acceptance-a3-preupgrade-a2')
        register_contract(h, args.source, f, json.loads((ROOT / 'docs/runtime-a2-registry.json').read_text()))
        _, url, ready = h.start_api(args.source)
        flow = Flow(h, url, f)
        comp = flow.build()
        receipt = flow.activate(comp)
        objects = {oid: flow.object(oid) for oid in {
            v['object_id'] for r in flow.receipts for v in r['object_versions']}}
        revisions = {r['revision_id']: flow.clients['ceo'].revision(oid, r['revision_id'])
                     for oid, obj in objects.items()
                     for r in (obj['latest_revision'], obj['effective_revision']) if r}
        record = {'scope_id': f['scope_id'], 'source_manifest': source_manifest(args.source),
                  'api_provenance': json.loads(ready.read_text()),
                  'activation': receipt, 'objects': objects, 'revisions': revisions,
                  'receipts': {r['receipt_id']: r for r in flow.receipts},
                  'sql_snapshot': h.snapshot(f)}
        private_json(args.private / 'commands.json', flow.commands)
        public_json(args.output / 'history.json', record)
        return {'captured_real_A2_activation': True, 'commands': len(flow.commands),
                'objects': len(objects), 'source_files': len(record['source_manifest'])}
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
        result = {'A2_history_preserved': True, 'objects': len(record['objects']),
                  'revisions': len(record['revisions']), 'receipts_replayed': len(commands),
                  'historic_tables': len(actual), 'replay_business_state_unchanged': True,
                  'api_provenance': json.loads(ready.read_text()), 'source_manifest': source_manifest(args.source)}
        public_json(args.output / 'history-preservation.json', result)
        return {k: v for k, v in result.items() if k not in {'source_manifest', 'api_provenance'}}
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
