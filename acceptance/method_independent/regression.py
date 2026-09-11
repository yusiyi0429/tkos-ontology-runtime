"""Source-pinned old-capability regression. Local acceptance DB only."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from psycopg.conninfo import conninfo_to_dict
from acceptance.protocol_a1_independent.support import Environment, public_json, source_manifest

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    env = Environment(args.env_file)
    legacy_env = Environment(args.legacy_env_file)
    if not conninfo_to_dict(legacy_env.values['APP_DATABASE_URL'])['dbname'].startswith('tkos_a1_a2_a3_'):
        raise ValueError('Existing A2/A3 runners require their dedicated isolated database prefix')
    database = conninfo_to_dict(env.values['APP_DATABASE_URL'])['dbname']
    if not database.startswith('tkos_a1_method_'):
        raise ValueError('Method regression requires its new isolated database')
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Preserve previous results: choose fresh output')
    args.output.mkdir(parents=True)
    args.private.mkdir(parents=True, mode=0o700)
    initial = source_manifest(args.source)
    jobs = {
        'pytest': [sys.executable, '-m', 'pytest', 'tests', '--ignore=tests/test_migrations.py', '--ignore=tests/test_narrative_legacy.py', '-q',
                   '--junitxml=' + str(args.private / 'pytest.xml')],
        'narrative': [sys.executable, '-m', 'pytest', 'tests/test_narrative_legacy.py', '-q',
                      '--junitxml=' + str(args.private / 'narrative.xml')],
        'a2': [sys.executable, '-m', 'acceptance.method_independent.legacy_regression', 'a2', '--env-file', str(args.legacy_env_file),
               '--source', str(args.source), '--private', str(args.private / 'a2'), '--output', str(args.output / 'a2')],
        'a3': [sys.executable, '-m', 'acceptance.method_independent.legacy_regression', 'a3', '--env-file', str(args.legacy_env_file),
               '--source', str(args.source), '--private', str(args.private / 'a3'), '--output', str(args.output / 'a3')],
    }

    def execute(item):
        name, command = item
        active_env = legacy_env if name in {'a2', 'a3'} else env
        child = active_env.child(owner=name=='narrative', updates={'TKOS_LEGACY_ACCEPTANCE_DATABASE': database,
            'APP_DATABASE_URL': active_env.values['APP_DATABASE_URL']})
        result = subprocess.run(command, cwd=ROOT, env=child, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log = args.private / (name + '.log')
        log.write_text(active_env.redact(result.stdout)); log.chmod(0o600)
        print(json.dumps({'suite': name, 'exit_code': result.returncode}), flush=True)
        return name, result.returncode

    with ThreadPoolExecutor(max_workers=3) as pool:
        exits = dict(pool.map(execute, jobs.items()))
    a2 = json.loads((args.output / 'a2/report.json').read_text())
    a3 = json.loads((args.output / 'a3/report.json').read_text())
    junit = ET.parse(args.private / 'pytest.xml')
    cases = junit.findall('.//testcase') + ET.parse(args.private / 'narrative.xml').findall('.//testcase')
    passed = [c for c in cases if c.find('failure') is None and c.find('error') is None and c.find('skipped') is None]
    a1_cases = [c for c in passed if 'protocol_a1' in c.attrib.get('classname', '')]
    goldens = [c for c in passed if 'legacy_protocol_fixture' in c.attrib.get('classname', '') or 'request_contract_version' in c.attrib.get('classname', '')]
    final = source_manifest(args.source)
    report = {
        'source_manifest': final, 'source_unchanged': initial == final,
        'pytest_exit': exits['pytest'] or exits['narrative'], 'narrative_exit': exits['narrative'], 'pytest_passed': len(passed), 'pytest_skipped': len(cases)-len(passed),
        'legacy_serialization_passed': bool(goldens) and exits['pytest'] == 0,
        'a1_regression_scope': 'Existing tests/protocol_a1 model, HTTP boundary, immutable protocol and serialization regression; not a rerun of every original A1 maintenance scenario.',
        'a1_test_cases_passed': len(a1_cases),
        'a2_local_matrix_accepted': a2.get('a2_local_matrix_accepted') is True,
        'a2_checks_passed': a2.get('checks_passed'), 'a2_exit': exits['a2'],
        'a3_local_matrix_accepted': a3.get('a3_local_matrix_accepted') is True,
        'a3_checks_passed': a3.get('checks_passed'), 'a3_exit': exits['a3'],
        'legacy_catalog_adapter': 'Only expected post-0021 control/mutable table sets are extended; original A2/A3 business cases and SQL/privilege assertions are unchanged.',
        'migration_test_scope': 'Schema migration independently executed from accepted empty base through 0021; runner replay verified. tests/test_migrations.py requires separate admin-CREATEDB fixture and is excluded.',
    }
    report['passed'] = initial == final and all(value == 0 for value in exits.values()) and report['legacy_serialization_passed'] and report['a2_local_matrix_accepted'] and report['a3_local_matrix_accepted']
    public_json(args.output / 'summary.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'source_manifest'}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--legacy-env-file', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'src')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    args = parser.parse_args()
    if not run(args)['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
