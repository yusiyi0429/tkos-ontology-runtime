"""Human-UI browser fixture for tkos.method/0.4.

Creates fresh isolated 0.4 scopes with current appointments (no revocations)
using only identity bootstrap SQL plus legal HTTP actions from the same Flow the
acceptance suite uses:

* ``agreement_pending`` — a direct Agent issue whose Agreement is drafted and
  awaiting every nominated human confirmation in the browser.
* ``candidate_pending`` — a fully adopted Strategy/Architecture pair with two
  LTCOs (one Domain scope, one Battlefield scope), two PCO/Mission pairs, a
  reviewed window and a resolved CandidateSet awaiting the named DRI/Owner
  commitments and the CEO activation in the browser.
* ``window_open`` — the same legal preparation stopped at an open review
  window; the browser publishes/replaces/withdraws opinions, the Co-agent
  condenses with the controlled CLI and the CEO activates.  No comment, close
  or resolution is written by this helper.

``--stage`` builds one scope, or all three by default.  The helper writes public
object ids and a private accounts/token file, then keeps the API process alive
until SIGTERM. It never writes business SQL and does not claim browser
acceptance; human/Clark validation stays open.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import private_json, public_json
from .fixture import register_v04, seed_v04
from .flow import Flow, period


def _accounts(f):
    return {name: {'principal_id': actor['principal_id'], 'assignment_id': actor['assignment_id'],
                   'role': actor['role'], 'domain_id': actor['domain_id'],
                   'principal_type': actor.get('principal_type', 'human'), 'token': actor['token']}
            for name, actor in f['actors'].items()}


def _agreement_stage(h, source, url, tag):
    f = seed_v04(h.env, h.private / f'identities-{tag}.json', f'runtime-acceptance-browser-{tag}')
    register_v04(h, source, f)
    flow = Flow(h, url, f)
    evidence = flow.upload()
    run_ref = flow.open_run(title='Browser fixture intake run')
    issue = flow.create_issue(run_ref, evidence, title='Browser fixture strategic issue')
    flow.set_participants(issue)
    agreement = flow.draft_agreement(issue)
    return flow, {
        'stage': 'agreement_pending', 'scope_id': f['scope_id'], 'url': url,
        'objects': {'issue': issue, 'agreement': agreement},
        'ui_steps': [
            'Sign in as ceo, dri_a and owner_a with the private account tokens.',
            'Each current signer confirms the exact Agreement revision via /v1/actions.',
            'After all three confirmations the Agreement becomes formal (method_state.phase=formal).',
        ],
        'accounts': _accounts(f),
    }


def _prepare_candidate_chain(flow):
    """Legal Flow through an opened review window; no comments/close/resolve."""
    evidence = flow.upload()
    run_ref = flow.open_run(title='Browser fixture intake run')
    issue = flow.create_issue(run_ref, evidence, title='Browser fixture strategic issue')
    flow.set_participants(issue)
    agreement = flow.draft_agreement(issue)
    for actor in ('ceo', 'dri_a', 'owner_a'):
        flow.confirm_agreement(actor, agreement)
    agreement = flow.ref(agreement['object_id'], effective=True)
    change = {'strategy_target_ref': None, 'architecture_target_ref': None,
              'strategy': flow.strategy_fields(), 'architecture': flow.architecture_fields(),
              'applicability_rationale': 'Initial pair: no retained object exists yet.'}
    proposal = flow.propose_update(issue, agreement, change)
    flow.review_update(proposal)
    adopted = flow.confirm_update(proposal)['result']
    strategy_ref, architecture_ref = adopted['strategy_ref'], adopted['architecture_ref']
    ltco_period, pco_period, mission_period = period(-30, 335), period(-1, 30), period(0, 15)
    ltco_a = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-a', ltco_period)
    ltco_b = flow.propose_ltco(strategy_ref, architecture_ref, 'scope-b', ltco_period)
    for ltco in (ltco_a, ltco_b):
        flow.confirm_ltco(ltco)
    ltco_a = flow.ref(ltco_a['object_id'], effective=True)
    ltco_b = flow.ref(ltco_b['object_id'], effective=True)
    pco_a = flow.draft_pco(ltco_a, 'scope-a', pco_period)
    pco_b = flow.draft_pco(ltco_b, 'scope-b', pco_period)
    mission_a = flow.draft_mission(pco_a, 'owner_a', 'scope-a', mission_period, evidence=[evidence])
    mission_b = flow.draft_mission(pco_b, 'owner_b', 'scope-b', mission_period)
    window = flow.open_window([pco_a, pco_b], [mission_a, mission_b], [ltco_a, ltco_b], pco_period,
                              names=('dri_a', 'dri_b', 'owner_a', 'owner_b'))
    return {'evidence': evidence, 'issue': issue, 'agreement': agreement, 'strategy': strategy_ref,
            'architecture': architecture_ref, 'ltco_a': ltco_a, 'ltco_b': ltco_b,
            'pco_a': pco_a, 'pco_b': pco_b, 'mission_a': mission_a, 'mission_b': mission_b,
            'window': window}


def _candidate_stage(h, source, url, tag):
    f = seed_v04(h.env, h.private / f'identities-{tag}.json', f'runtime-acceptance-browser-{tag}')
    register_v04(h, source, f)
    flow = Flow(h, url, f)
    chain = _prepare_candidate_chain(flow)
    window = chain['window']
    opinion = flow.comment(window, chain['pco_a'], 'dri_a')
    second_opinion = flow.comment(window, chain['mission_a'], 'owner_a')
    flow.close_window(window)
    params = {'title': 'Browser fixture candidate set', 'pcos': [], 'missions': [],
              'dispositions': [{'review_record_id': opinion, 'decision': 'adopted',
                                'rationale': 'Browser fixture adopted this exact frozen opinion.'},
                               {'review_record_id': second_opinion, 'decision': 'adopted',
                                'rationale': 'Browser fixture adopted the second exact frozen opinion.'}],
              'unresolved_differences': [], 'summary': 'Browser fixture candidate set.'}
    for ref in (chain['pco_a'], chain['pco_b']):
        params['pcos'].append({'object_id': ref['object_id'],
                               'payload': deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])})
    for ref in (chain['mission_a'], chain['mission_b']):
        payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
        payload.pop('parent_pco_ref')
        params['missions'].append({'object_id': ref['object_id'], **payload})
    candidate = flow.resolve_window(window, params)
    candidate = flow.ref(candidate['object_id'])
    return flow, {
        'stage': 'candidate_pending', 'scope_id': f['scope_id'], 'url': url,
        'objects': {'issue': chain['issue'], 'agreement': chain['agreement'], 'strategy': chain['strategy'],
                    'architecture': chain['architecture'], 'ltco_domain': chain['ltco_a'],
                    'ltco_battlefield': chain['ltco_b'], 'pco_domain': chain['pco_a'],
                    'pco_battlefield': chain['pco_b'], 'mission_owner_a': chain['mission_a'],
                    'mission_owner_b': chain['mission_b'], 'window': window, 'candidate': candidate},
        'ui_steps': [
            'Sign in as dri_a/dri_b/owner_a/owner_b and commit each own responsibility against the exact candidate revision.',
            'Sign in as ceo and activate the whole committed candidate set (critical differences still block).',
        ],
        'accounts': _accounts(f),
    }


def _window_stage(h, source, url, tag):
    """Same legal preparation as candidate_pending, stopped at the open window.

    No comment, replacement, withdrawal, close or resolution is written here:
    the browser performs those actions, then the Co-agent condenses via the
    controlled CLI.
    """
    f = seed_v04(h.env, h.private / f'identities-{tag}.json', f'runtime-acceptance-browser-{tag}')
    register_v04(h, source, f)
    flow = Flow(h, url, f)
    chain = _prepare_candidate_chain(flow)
    return flow, {
        'stage': 'window_open', 'scope_id': f['scope_id'], 'url': url,
        'objects': {'issue': chain['issue'], 'agreement': chain['agreement'], 'strategy': chain['strategy'],
                    'architecture': chain['architecture'], 'ltco_domain': chain['ltco_a'],
                    'ltco_battlefield': chain['ltco_b'], 'pco_domain': chain['pco_a'],
                    'pco_battlefield': chain['pco_b'], 'mission_owner_a': chain['mission_a'],
                    'mission_owner_b': chain['mission_b'], 'window': chain['window']},
        'ui_steps': [
            'Sign in as dri_a/dri_b/owner_a/owner_b with the private account tokens.',
            'Publish, replace or withdraw personal opinions on the exact frozen targets while the window is open.',
            'Do not close the window: the Co-agent condenses it via the controlled CLI, then the CEO activates.',
        ],
        'accounts': _accounts(f),
    }


STAGE_BUILDERS = {
    'agreement_pending': (_agreement_stage, '-a'),
    'candidate_pending': (_candidate_stage, '-b'),
    'window_open': (_window_stage, '-w'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path('src'))
    parser.add_argument('--stage', choices=['all', *STAGE_BUILDERS], default='all',
                        help='Build one fixture scope, or all three by default.')
    args = parser.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError('Use fresh private and output paths')
    args.private.mkdir(parents=True, mode=0o700)
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    process = url = None
    flows = []
    try:
        process, url, _ = h.start_api(args.source.resolve())
        tag = uuid4().hex[:8]
        selected = list(STAGE_BUILDERS) if args.stage == 'all' else [args.stage]
        scopes, accounts = [], {}
        for stage in selected:
            builder, suffix = STAGE_BUILDERS[stage]
            flow, scope = builder(h, args.source.resolve(), url, tag + suffix)
            flows.append(flow)
            accounts[scope['stage']] = scope.pop('accounts')
            scopes.append(scope)
        private_json(args.private / 'accounts.json', accounts)
        public_json(args.output / 'browser-fixture.json', {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'url': url,
            'scopes': scopes,
            'accounts_file': str(args.private / 'accounts.json'),
            'business_sql_used': False,
            'browser_acceptance': 'not_run',
            'note': 'Human/Clark browser actions are the acceptance step; the helper only stages legal state.',
        })
        summary = {'url': url, 'accounts_file': str(args.private / 'accounts.json'),
                   'browser_acceptance': 'not_run'}
        for scope in scopes:
            summary[scope['stage']] = scope['scope_id']
        print(json.dumps(summary), flush=True)
        stopping = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        signal.signal(signal.SIGINT, lambda *_: stopping.set())
        stopping.wait()
    finally:
        for flow in flows:
            flow.close()
        h.close()
        if process is not None and process.poll() is None:
            process.terminate()


if __name__ == '__main__':
    main()
