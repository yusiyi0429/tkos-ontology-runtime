"""Launch the freshly built local image with human-only credentials and the API DB role."""
import argparse
import os
from pathlib import Path
import subprocess
from acceptance.protocol_a1_independent.support import Environment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--private', type=Path, required=True)
    p.add_argument('--image', required=True)
    p.add_argument('--name', default='tkos-governance-local')
    p.add_argument('--port', type=int, default=58804)
    a = p.parse_args()
    scenario = a.private.resolve()
    values = Environment(a.env_file).values
    env = {k: str(v).replace('127.0.0.1', 'host.docker.internal') for k, v in values.items()
           if k in {'DATABASE_URL', 'MEMORY_TENANT', 'MEMORY_ORG'} or k.startswith('TKOS_OBJECT_STORE_')}
    origin = 'http://127.0.0.1:' + str(a.port)
    env.update(TKOS_DASHBOARD_ENABLED='1', TKOS_GOVERNANCE_WORKBENCH_ENABLED='1',
               TKOS_GOVERNANCE_ACCOUNTS_FILE=str(scenario / 'accounts.json'),
               TKOS_GOVERNANCE_COMMANDS_DIR=str(scenario / 'commands'),
               TKOS_DASHBOARD_ALLOWED_HOSTS='127.0.0.1:' + str(a.port),
               TKOS_DASHBOARD_ALLOWED_ORIGINS=origin, TKOS_DASHBOARD_ENV_LABEL='本机隔离治理验收')
    config = scenario / 'container.env'
    config.write_text(''.join(k + '=' + v + '\n' for k, v in env.items()))
    config.chmod(0o600)
    args = ['docker', 'run', '-d', '--name', a.name, '--user', f'{os.getuid()}:{os.getgid()}',
            '-p', f'127.0.0.1:{a.port}:8010', '--env-file', str(config)]
    for name in ['accounts.json', 'ceo.token', 'dri-a.token', 'dri-b.token']:
        path = str(scenario / name)
        args += ['-v', path + ':' + path + ':ro']
    commands = scenario / 'commands'
    commands.mkdir(mode=0o700, exist_ok=True)
    args += ['-v', str(commands) + ':' + str(commands), a.image]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('Container launch failed; check name availability and Docker, without printing private env.')
    print('Local workbench: ' + origin + '/dashboard/')


if __name__ == '__main__':
    main()
