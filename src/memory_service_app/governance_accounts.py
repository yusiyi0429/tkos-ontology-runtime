"""Provision a local login bound to an existing human Runtime credential."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

import httpx
from .governance_commands import write
from .governance_sessions import private_file


def provision(accounts_file, username, token_file, identity, login_file):
    if identity['principal_type'] != 'human' or not identity['assignments']:
        raise ValueError('A current human identity is required')
    accounts_file = Path(accounts_file)
    accounts_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = json.loads(private_file(accounts_file)) if accounts_file.exists() else {}
    code = secrets.token_urlsafe(32)
    config[username] = {'scope_id': identity['scope_id'], 'principal_id': identity['principal_id'],
        'token_file': str(Path(token_file).resolve()), 'code_digest': hashlib.sha256(code.encode()).hexdigest(), 'enabled': True}
    write(accounts_file, config)
    login_file = Path(login_file)
    login_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write(login_file, {'username': username, 'code': code})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--accounts-file', type=Path, required=True)
    parser.add_argument('--login-file', type=Path, required=True)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.username or parsed.password:
        parser.error('Only a local Runtime is supported')
    token = private_file(args.token_file).decode().strip()
    with httpx.Client(base_url=args.url, trust_env=False) as client:
        response = client.get('/v1/identity', headers={'Authorization': 'Bearer ' + token})
        response.raise_for_status()
        provision(args.accounts_file, args.username, args.token_file, response.json(), args.login_file)
    print('Local account provisioned. Login code saved in the requested private file; existing sessions are invalidated.')


if __name__ == '__main__':
    main()
