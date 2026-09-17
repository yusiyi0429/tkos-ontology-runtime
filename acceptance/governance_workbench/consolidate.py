"""Controlled acceptance driver, separate from the human workbench (no model claim)."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from acceptance.anchors_v03.run import Flow
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import private_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--private', type=Path, required=True)
    a = p.parse_args()
    f = json.loads((a.private / 'identities.json').read_text())
    targets = json.loads((a.private / 'state.json').read_text())
    h = MethodHarness(a.env_file, a.private / 'agent-http', a.private / 'agent-process')
    try:
        flow = Flow(h, a.url, f)
        flow.close_window(targets)
        private_json(a.private / 'state.json', targets)
        now = datetime.now(timezone.utc).isoformat()
        context = flow.clients['co_agent'].json('POST', '/v1/context-packs', {
            'contract_version': 'tkos.method/0.3',
            'object_ids': [targets[k]['object_id'] for k in ('window', 'pco', 'mission')],
            'valid_at': now, 'known_at': now, 'stage': 'review', 'purpose': 'analysis', 'include_drafts': True})
        params = flow.resolution_params(targets)
        params['pco_payload']['unit_outcomes'][0]['criteria'].append('每个试点保留原始证据，并逐项核验来源和质量标准。')
        private_json(a.private / 'controlled-generation.json', {'model': None, 'controlled': True, 'context': context, 'params': params})
        result = flow.act('co_agent', 'm1b_resolve_window', params, oid=targets['window']['object_id'])['result']
        private_json(a.private / 'controlled-result.json', result)
        print('Controlled Co-agent candidate submitted; CEO confirmation remains pending. No real model was called.')
    finally:
        h.close()


if __name__ == '__main__':
    main()
