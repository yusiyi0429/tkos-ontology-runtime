"""Root-invoked current synthetic scope setup; never modifies old history.

No database/container creation or migration. The real selected bootstrap seeds
only synthetic authority and the existing legacy starting Outcome. All tested
DRI/feedback/assessment transitions are subsequently performed via real HTTP.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from psycopg.conninfo import conninfo_to_dict

from .support import Environment, HERE, public_json, source_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    env = Environment(args.env_file)
    if not conninfo_to_dict(env.values['APP_DATABASE_URL'])['dbname'].startswith('tkos_a1_'):
        parser.error('current fixture requires a one-off tkos_a1_* database')
    if args.fixture.exists() or args.report.exists():
        parser.error('choose fresh fixture and report paths')
    if not args.namespace.startswith('runtime-acceptance-'):
        parser.error('current fixture needs an explicit runtime-acceptance namespace')
    start = source_manifest(args.source)
    completed = subprocess.run([sys.executable, '-I', str(HERE/'source_process.py'), 'seed',
        '--source', str(args.source.resolve()), '--out', str(args.fixture.resolve()), '--namespace', args.namespace],
        env=env.child(owner=True), text=True, capture_output=True, timeout=30)
    end = source_manifest(args.source)
    report = {'exit_code': completed.returncode, 'stdout': env.redact(completed.stdout),
        'stderr': env.redact(completed.stderr), 'source_manifest_start': start, 'source_manifest_end': end,
        'source_unchanged': start == end, 'contract_a1_accepted': False}
    public_json(args.report, report)
    assert completed.returncode == 0 and start == end
    assert args.fixture.is_file() and args.fixture.stat().st_mode & 0o077 == 0
    print(json.dumps({'current_fixture_created': True, 'fixture_file': str(args.fixture), 'report_file': str(args.report),
                      'contract_a1_accepted': False}))


if __name__ == '__main__':
    main()
