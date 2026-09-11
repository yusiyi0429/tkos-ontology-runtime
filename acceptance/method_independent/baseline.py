"""Read-only preservation evidence for deferred files and existing containers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]
DEFERRED = [
    'deploy/convergence/README.md', 'deploy/convergence/rehearse.py',
    'deploy/convergence/test_rehearse.py',
    'deploy/convergence/rehearsal-41b333f2b8c3.json',
    'deploy/convergence/rehearsal-a65aff5ad7ef.json',
    'deploy/remote-production/.env.example',
    'deploy/remote-production/ClarkOverlay.Dockerfile',
    'deploy/remote-production/README.md', 'deploy/remote-production/build_bundle.py',
    'deploy/remote-production/compose.yaml', 'deploy/remote-production/configure.py',
    'deploy/remote-production/db_admin.py',
    'deploy/remote-production/scripts/deploy-clark-image.sh',
    'deploy/remote-production/scripts/rollback.sh',
    'deploy/remote-production/scripts/start-candidate.sh',
    'deploy/remote-production/scripts/switch-memory-api.py',
    'deploy/remote-production/scripts/update-clark-env.py',
    'deploy/remote-production/scripts/verify.py',
    'docs/production-offline-components.md',
]


def capture():
    # No docker inspect: it may expose container environment secrets.
    result = subprocess.run([
        'docker', 'ps', '-a', '--no-trunc', '--format',
        '{{json .}}',
    ], text=True, capture_output=True, timeout=20, check=True)
    containers = []
    for line in result.stdout.splitlines():
        raw = json.loads(line)
        containers.append({key: raw[key] for key in ('ID', 'Names', 'Image', 'State', 'Ports')})
    return {
        'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in DEFERRED},
        'containers': sorted(containers, key=lambda row: row['ID']),
    }


def compare(before, after):
    return {
        'files_unchanged': before['files'] == after['files'],
        'containers_unchanged': before['containers'] == after['containers'],
        'file_count': len(before['files']),
        'container_count': len(before['containers']),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('fresh baseline output required')
    snapshot = capture()
    private_json(args.output, snapshot)
    result = compare(json.loads(args.compare.read_text()), snapshot) if args.compare else {
        'file_count': len(snapshot['files']), 'container_count': len(snapshot['containers']),
    }
    print(json.dumps(result))


if __name__ == '__main__':
    main()
