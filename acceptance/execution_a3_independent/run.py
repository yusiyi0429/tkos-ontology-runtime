"""Run frozen A3 cases via real loopback HTTP on a separately migrated test DB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import uuid

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from acceptance.composition_a2_independent import storage
from acceptance.protocol_a1_independent import golden
from acceptance.protocol_a1_independent.support import (
    private_json, public_json, safe_traceback_frames, source_manifest,
)

from .harness import A3Harness, A3Book
from .fixture import seed_authority, register_contract
from .flow import Flow
from . import cases_chain, cases_boundaries, cases_transactions, cases_outcome, cases_reads, oracle

ROOT = Path(__file__).resolve().parents[2]
NEW_TABLES = {'gov_execution_state', 'gov_execution_authorities', 'gov_acceptance_appointments',
              'gov_a3_work_item_state', 'gov_work_receipts', 'gov_a3_delivery_acceptances', 'gov_a3_outcome_assessments'}
MUTABLE_TABLES = {'gov_formation_round_state', 'gov_round_formal_submissions',
                  'gov_execution_state', 'gov_a3_work_item_state'}
SCENARIOS = {
    'chain': (cases_chain.chain, ['A3-01', 'A3-02', 'A3-03', 'A3-04', 'A3-05', 'A3-07']),
    'confirmations': (cases_chain.confirmations, ['A3-02']),
    'admission': (cases_boundaries.admission, ['A3-01', 'A3-03', 'A3-04', 'A3-06']),
    'coauthor': (cases_boundaries.coauthor, ['A3-06']),
    'revision_errors': (cases_boundaries.revision_errors, ['A3-07']),
    'protocol': (cases_boundaries.protocol, ['A3-12']),
    'races': (cases_transactions.races, ['A3-09']),
    'rollback': (cases_transactions.rollback, ['A3-10']),
    'revoke': (cases_transactions.revoke, ['A3-08']),
    'epoch': (cases_transactions.epoch, ['A3-08']),
    'expiry': (cases_transactions.expiry, ['A3-08']),
    'appointment_expiry': (cases_transactions.appointment_expiry, ['A3-06']),
    'review_after_execution_end': (None, []),
    'source_change': (cases_transactions.source_change, ['A3-13']),
    'source_first': (None, ['A3-13']),
    'submission_first': (None, ['A3-13']),
    'outcome': (None, ['A3-11']),
    'reads': (None, []),
    'restart': (None, ['A3-14']),
}


def execute(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('fresh output required; earlier evidence is immutable')
    h = A3Harness(args.env_file, args.output, args.private)
    assert conninfo_to_dict(h.env.values['APP_DATABASE_URL'])['dbname'].startswith('tkos_a1_a2_a3_')
    source = args.source.resolve()
    initial = source_manifest(source)
    public_json(args.output / 'source-before.json', initial)
    registry = json.loads((ROOT / 'docs/runtime-a3-registry.json').read_text())
    selected = args.scenario or list(SCENARIOS)
    book = A3Book(args.output)
    book.save(selected_scenarios=selected, source_root=str(source), scenarios={})
    db_names = ['a3_primary_' + uuid.uuid4().hex[:12], 'a3_peer_' + uuid.uuid4().hex[:12]]
    order_proofs, effects = {}, {}
    normal_fixture = normal_ec = normal_work = None
    try:
        urls, processes = [], []
        for name in db_names:
            dsn = make_conninfo(h.env.values['APP_DATABASE_URL'], application_name=name)
            process, url, ready = h.start_api(source, updates={'DATABASE_URL': dsn})
            urls.append(url)
            processes.append(process)
            public_json(args.output / (name + '-identity.json'), json.loads(ready.read_text()))
        schema = storage.catalog(h, new_tables=NEW_TABLES, mutable_tables=MUTABLE_TABLES)
        book.gates['schema_privileges'] = {'passed': True, 'evidence': schema}
        serialized = golden.check(source, golden.GOLDEN, report=args.output / 'legacy-serialization.json')
        assert serialized['passed'] == serialized['total'] == 35
        book.gates['legacy_serialization'] = {'passed': True, 'evidence': {'passed': 35, 'file': 'legacy-serialization.json'}}
        for name in selected:
            function, affected = SCENARIOS[name]
            flow = None
            try:
                f = seed_authority(h.env, args.private / (name + '-fixture.json'), 'runtime-acceptance-a3-' + name)
                h.fixture_tokens.extend(a['token'] for a in f['actors'].values())
                markers = cases_outcome.legacy_markers(h, source, urls[0], f) if name == 'outcome' else None
                register_contract(h, source, f, registry)
                flow = Flow(h, urls[0], f)
                if name in ['source_first', 'submission_first']:
                    result = cases_transactions.source_order(flow, peer_url=urls[1],
                        primary_name=db_names[0], peer_name=db_names[1], source_first=name == 'source_first')
                    order_proofs[name] = result
                elif name == 'outcome':
                    result = cases_outcome.outcome(flow, book, markers=markers)
                elif name == 'reads':
                    result = cases_reads.reads(flow)
                    book.gates['read_authority_rechecks'] = {'passed': True, 'evidence': result}
                elif name == 'review_after_execution_end':
                    result = cases_transactions.review_after_execution_end(flow)
                    book.gates['independent_acceptance_authority'] = {'passed': True, 'evidence': result}
                elif name == 'restart':
                    result = cases_transactions.restart(flow, book, source=source, api_process=processes[0])
                    # Restart is last by default. Keep subsequent explicitly selected
                    # scenarios connected to the replacement if a caller reorders them.
                    urls[0] = flow.url
                else:
                    result = function(flow, book)
                if name == 'chain':
                    normal_fixture, normal_ec, normal_work = f, flow.ec_id, flow.work_id
                effects[name] = oracle.no_effects(flow)
                book.metadata['scenarios'][name] = {'status': 'completed', 'scope_id': f['scope_id']}
                public_json(args.output / (name + '-evidence.json'), result)
            except Exception as error:
                details = {'exception_type': type(error).__name__, 'frames': safe_traceback_frames(error)}
                public_json(args.output / (name + '-failure.json'), details)
                book.metadata['scenarios'][name] = {'status': 'failed', **details}
                for case in affected:
                    book.failure(case, 'Scenario failed: ' + name + ' (' + type(error).__name__ + ')')
                if not args.keep_going:
                    raise
            finally:
                if flow:
                    private_json(args.private / (name + '-commands.json'), flow.commands)
                    flow.close()
                book.save()
        if set(order_proofs) == {'source_first', 'submission_first'}:
            book.check('A3-13', 'source_changes_at_final_barrier_have_ordered_outcome', True, evidence=order_proofs)
        if set(effects) == set(selected):
            book.gates['no_external_effects'] = {'passed': True, 'evidence': effects}
        if normal_fixture:
            probes = [storage.guarded_no_capability(h, normal_fixture, table=table, column=column,
                key_column=key_column, key=key) for table, column, key_column, key in [
                    ('gov_execution_state', 'current_execution_epoch', 'commitment_object_id', normal_ec),
                    ('gov_a3_work_item_state', 'execution_epoch', 'object_id', normal_work)]]
            book.gates['write_capability'] = {'passed': True, 'evidence': probes}
            probes = [storage.immutable_owner_probe(h, normal_fixture, table=table, column=column,
                key_column=key_column, key=normal_ec) for table, column, key_column in [
                    ('gov_execution_authorities', 'execution_epoch', 'commitment_object_id'),
                    ('gov_acceptance_appointments', 'appointment_version', 'commitment_object_id')]]
            book.gates['immutable_history'] = {'passed': True, 'evidence': probes}
    except Exception as error:
        book.save(run_error={'exception_type': type(error).__name__, 'frames': safe_traceback_frames(error)})
        raise
    finally:
        final = source_manifest(source)
        public_json(args.output / 'source-after.json', final)
        book.gates['source_unchanged'] = {'passed': initial == final}
        h.close()
        result = book.save()
        print(json.dumps({k: result[k] for k in ['a3_local_matrix_accepted', 'passed', 'failed', 'not_complete', 'checks_passed']}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'src')
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', action='append', choices=SCENARIOS)
    parser.add_argument('--keep-going', action='store_true')
    args = parser.parse_args()
    result = execute(args)
    if result['failed'] or result.get('run_error'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
