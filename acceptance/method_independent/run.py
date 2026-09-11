"""Run independent Method cases against a fresh, explicitly migrated local DB."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from acceptance.protocol_a1_independent.support import private_json, public_json, safe_traceback_frames, source_manifest

from . import cases_chain, cases_compatibility, cases_m1a_boundaries, cases_permissions, cases_recovery, gates
from .fixture import register_method, seed_authority
from .harness import MethodBook, MethodHarness
from .m1b_flow import MethodFlow

ROOT = Path(__file__).resolve().parents[2]


def execute(args):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('fresh output required; previous evidence is retained')
    h = MethodHarness(args.env_file, args.output, args.private)
    database = conninfo_to_dict(h.env.values['APP_DATABASE_URL'])['dbname']
    if not database.startswith('tkos_a1_method_'):
        raise ValueError('Method acceptance only runs on its fresh explicit database')
    source = args.source.resolve()
    initial = source_manifest(source)
    public_json(args.output / 'source-before.json', initial)
    book = MethodBook(args.output)
    selected = ['m1a_full', 'm1b_full', 'm1a_boundaries', 'permissions', 'compatibility', 'recovery', 'revocation', 'gates', 'final_regressions']
    book.save(selected_scenarios=selected, scenarios={}, source_root=str(source))
    flow = None
    scenario = 'm1a_full'
    try:
        api_process, url, ready = h.start_api(source)
        public_json(args.output / 'api-identity.json', json.loads(ready.read_text()))
        f = seed_authority(h.env, args.private / 'fixture.json', 'runtime-acceptance-method-full')
        h.fixture_tokens.extend(actor['token'] for actor in f['actors'].values())
        register_method(h, source, f)
        flow = MethodFlow(h, url, f)
        chain = cases_chain.m1a(flow, book)
        public_json(args.output / 'm1a-full.json', chain)
        book.metadata['scenarios']['m1a_full'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'm1b_full'
        result = cases_chain.m1b(flow, book, chain['strategy'])
        public_json(args.output / 'm1b-full.json', result)
        book.metadata['scenarios']['m1b_full'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'm1a_boundaries'
        boundary_result = cases_m1a_boundaries.run(flow, book, chain, result)
        public_json(args.output / 'm1a-boundaries.json', boundary_result)
        book.metadata['scenarios']['m1a_boundaries'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'permissions'
        permission_result = cases_permissions.run(flow, book, chain, result)
        public_json(args.output / 'permissions.json', permission_result)
        book.metadata['scenarios']['permissions'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'compatibility'
        compatibility = cases_compatibility.run(flow, book, chain, result)
        public_json(args.output / 'compatibility.json', compatibility)
        book.metadata['scenarios']['compatibility'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'recovery'
        recovered = cases_recovery.run(flow, book, source=source, api_process=api_process, chain=chain, result=result)
        api_process = recovered.pop('api_process')
        public_json(args.output / 'recovery.json', recovered)
        book.metadata['scenarios']['recovery'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'revocation'
        revoked = cases_permissions.revoke(flow, book, chain, result, targets=permission_result['revocation_targets'])
        public_json(args.output / 'revocation.json', revoked)
        book.metadata['scenarios']['revocation'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'gates'
        gates.run(flow, book, database_evidence=args.database_evidence,
                  upgrade_evidence=args.upgrade_evidence, preservation_baseline=args.preservation_baseline)
        book.metadata['scenarios']['gates'] = {'status': 'completed', 'scope_id': f['scope_id']}
        scenario = 'final_regressions'
        if args.regression_evidence and args.history_evidence:
            cases_compatibility.final_regressions(flow, book, source=source,
                regression_file=args.regression_evidence, history_file=args.history_evidence)
            book.metadata['scenarios']['final_regressions'] = {'status': 'completed'}
        else:
            book.metadata['scenarios']['final_regressions'] = {'status': 'not_run', 'reason': 'Source-matched external regression and history evidence are required.'}
    except Exception as error:
        details = {'exception_type': type(error).__name__, 'frames': safe_traceback_frames(error)}
        public_json(args.output / (scenario + '-failure.json'), details)
        book.metadata['scenarios'][scenario] = {'status': 'failed', **details}
        book.save(run_error=details)
        raise
    finally:
        if flow:
            private_json(args.private / 'commands.json', flow.commands)
            flow.close()
        final = source_manifest(source)
        public_json(args.output / 'source-after.json', final)
        book.gates['source_unchanged'] = {'passed': initial == final}
        h.close()
        result = book.save()
        print(json.dumps({k: result[k] for k in ['method_api_accepted', 'passed', 'failed', 'not_complete', 'checks_passed']}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'src')
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--database-evidence', type=Path, default=ROOT / 'artifacts/runtime-acceptance/method-database-20260911/database.json')
    parser.add_argument('--upgrade-evidence', type=Path, default=ROOT / 'artifacts/runtime-acceptance/method-upgrade-20260911/upgrade.json')
    parser.add_argument('--preservation-baseline', type=Path, default=ROOT / '.runtime-acceptance/method-20260911/baseline-before.json')
    parser.add_argument('--regression-evidence', type=Path)
    parser.add_argument('--history-evidence', type=Path)
    args = parser.parse_args()
    result = execute(args)
    # Partial evidence is a useful diagnostic, never a successful acceptance exit.
    if result['method_api_accepted'] is not True:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
