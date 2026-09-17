"""Run the current source with a private local scenario; never prints credentials."""
import argparse
import os
from pathlib import Path
from acceptance.protocol_a1_independent.support import Environment


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--private', type=Path, required=True)
    p.add_argument('--port', type=int, default=58803)
    a = p.parse_args()
    env = Environment(a.env_file)
    origin = 'http://127.0.0.1:' + str(a.port)
    os.environ.update(env.child(updates={
        'TKOS_DASHBOARD_ENABLED': '1', 'TKOS_GOVERNANCE_WORKBENCH_ENABLED': '1',
        'TKOS_GOVERNANCE_ACCOUNTS_FILE': str((a.private / 'accounts.json').resolve()),
        'TKOS_GOVERNANCE_COMMANDS_DIR': str((a.private / 'commands').resolve()),
        'TKOS_DASHBOARD_ALLOWED_HOSTS': '127.0.0.1:' + str(a.port),
        'TKOS_DASHBOARD_ALLOWED_ORIGINS': origin,
        'TKOS_DASHBOARD_ENV_LABEL': '本机隔离治理验收', 'TKOS_DASHBOARD_SYNTHETIC': 'true',
    }))
    import uvicorn
    uvicorn.run('memory_service_app.main:app', host='127.0.0.1', port=a.port, access_log=False)


if __name__ == '__main__':
    main()
