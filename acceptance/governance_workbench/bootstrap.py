"""Create a fresh 0.3 scope and an open M1B window via legal Runtime actions."""
import argparse
from pathlib import Path
from acceptance.anchors_v03.fixture import seed_v03, register_v03
from acceptance.anchors_v03.run import Flow
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import private_json
from memory_service_app.governance_accounts import provision


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--private', type=Path, required=True)
    a = p.parse_args()
    a.private = a.private.resolve()
    if (a.private / 'state.json').exists():
        p.error('Use a fresh private directory; an existing scenario is never overwritten')
    source = Path(__file__).resolve().parents[2] / 'src'
    h = MethodHarness(a.env_file, a.private / 'http', a.private / 'process')
    try:
        f = seed_v03(h.env, a.private / 'identities.json', 'runtime-acceptance-governance')
        register_v03(h, source, f)
        _, url, _ = h.start_api(source)
        flow = Flow(h, url, f)
        strategy = flow.strategy(complete=True)['strategy']
        ltco = flow.ltco(strategy)['ltco']
        targets = flow.open_window(flow.draft_targets(strategy, ltco))
        # No comments, candidates, or confirmations are prepopulated.
        for actor, username in [('ceo', 'ceo'), ('a', 'dri-a'), ('b', 'dri-b')]:
            token_file = a.private / (username + '.token')
            token_file.write_text(f['actors'][actor]['token']); token_file.chmod(0o600)
            identity = flow.clients[actor].json('GET', '/v1/identity')
            provision(a.private / 'accounts.json', username, token_file, identity, a.private / (username + '-login.json'))
        private_json(a.private / 'state.json', targets)
        print('Fresh isolated 0.3 scenario ready: CEO and two DRI accounts; open window with no opinions or candidates.')
    finally:
        h.close()


if __name__ == '__main__':
    main()
