"""Fresh isolated scenario for the session-facade source/context acceptance.

Creates a new scope through the real identity fixture, provisions local
workbench accounts for the human owner/participant, and writes only private
files. No production module is modified and no business success is seeded:
the scene and sources are created later through the facade over real HTTP.
"""
import argparse
from pathlib import Path

from acceptance.anchors_v03.fixture import register_v03, seed_v03
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import private_json
from memory_service_app.governance_accounts import provision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    args = parser.parse_args()
    private = args.private.resolve()
    if (private / 'identities.json').exists():
        parser.error('Use a fresh private directory')
    private.mkdir(parents=True, mode=0o700)
    source = Path(__file__).resolve().parents[2] / 'src'
    h = MethodHarness(args.env_file, private / 'bootstrap-http', private / 'bootstrap-process')
    try:
        fixture = seed_v03(h.env, private / 'identities.json', 'runtime-acceptance-facade-' + __import__('uuid').uuid4().hex[:8])
        register_v03(h, source, fixture)
        _, url, _ = h.start_api(source)
        clients = h.clients(url, fixture)
        try:
            for actor, username in [('a', 'dri-a'), ('b', 'dri-b'), ('ceo', 'ceo')]:
                token_file = private / (username + '.token')
                token_file.write_text(fixture['actors'][actor]['token'])
                token_file.chmod(0o600)
                identity = clients[actor].json('GET', '/v1/identity')
                provision(private / 'accounts.json', username, token_file, identity,
                          private / (username + '-login.json'))
        finally:
            for client in clients.values():
                client.close()
    finally:
        h.close()
    private_json(private / 'state.json', {'facade_acceptance': True,
                                          'note': 'Scene/source/context records are created through the facade only.'})
    print('Fresh facade scenario ready: dri-a, dri-b and ceo local logins provisioned privately.')


if __name__ == '__main__':
    main()
