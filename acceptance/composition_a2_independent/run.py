"""Run frozen A2 cases against real loopback HTTP and a separately migrated DB.

Never creates infrastructure, upgrades a database, or fabricates business rows.
Each scenario gets new synthetic authority, then progresses solely through HTTP.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import uuid

from psycopg.conninfo import make_conninfo
from acceptance.protocol_a1_independent.support import (
    private_json, public_json, source_manifest, safe_traceback_frames,
)
from .harness import A2Harness, A2Book
from .fixture import seed_authority, register_contract
from .flow import Flow
from . import cases_versions as versions, cases_members as members
from . import cases_dependencies as dependencies, cases_authority as authority
from . import cases_concurrency as concurrent, cases_disclosure as disclosure
from . import storage
from . import cases_sources, cases_review

ROOT = Path(__file__).resolve().parents[2]
NEW_TABLES = {'gov_formation_round_state', 'gov_round_formal_submissions',
              'gov_composition_confirmations', 'gov_mission_index', 'gov_activation_records'}
MUTABLE_TABLES = {'gov_formation_round_state', 'gov_round_formal_submissions'}
SCENARIOS = {
    'review_boundaries': (cases_review.review_boundaries, ['A2-07', 'A2-18']),
    'source_expiry': (cases_review.source_expiry, ['A2-06']),
    'source_inputs': (cases_sources.source_inputs, ['A2-06','A2-18']),
    'versions': (versions.normal_versions, ['A2-01', 'A2-02', 'A2-04', 'A2-07']),
    'draft_profile': (versions.draft_profile, ['A2-03', 'A2-12']),
    'add_member': (members.add_members, ['A2-05']),
    'remove_member': (members.remove_members, ['A2-05']),
    'implicit_binding': (dependencies.implicit_binding, ['A2-06']),
    'informational': (dependencies.informational, ['A2-06']),
    'judgments': (dependencies.nonpassing_judgments, ['A2-07']),
    'cycle': (dependencies.cyclic_source, ['A2-07']),
    'closure': (dependencies.closure_limit, ['A2-06']),
    'signers': (authority.signer_negatives, ['A2-08', 'A2-09']),
    'required_revoke': (authority.required_revocation, ['A2-10']),
    'unrelated_revoke': (authority.unrelated_revocation, ['A2-10']),
    'race': (concurrent.same_cas, ['A2-13']),
    'expiry': (concurrent.expiry, ['A2-11']),
    'rollback': (concurrent.rollback, ['A2-16']),
    'second_round': (authority.second_round, ['A2-17']),
    'disclosure': (disclosure.disclosure, ['A2-18']),
    'disclosure_authority': (disclosure.authority_disclosure, ['A2-18']),
    **{'change_first_' + mode: (None, ['A2-14']) for mode in ('amend', 'source', 'revoke')},
    **{'activation_first_' + mode: (None, ['A2-15']) for mode in ('amend', 'source', 'revoke')},
}


def execute(args):
    import json
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('use a fresh output directory; preserve earlier evidence')
    h = A2Harness(args.env_file, args.output, args.private)
    # The historical generic environment also allows the demo DB. A2 adds a
    # strict guard so a typo cannot run this matrix against that older fixture.
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(h.env.values['APP_DATABASE_URL'])['dbname'].startswith('tkos_a1_a2_')
    source = args.source.resolve()
    initial_source = source_manifest(source)
    public_json(args.output / 'source-before.json', initial_source)
    registry = json.loads((ROOT / 'docs/runtime-a2-registry.json').read_text())
    selected = args.scenario or list(SCENARIOS)
    book = A2Book(args.output)
    book.save(selected_scenarios=selected, source_root=str(source), scenarios={})
    primary_name = 'a2_primary_' + uuid.uuid4().hex[:12]
    peer_name = 'a2_peer_' + uuid.uuid4().hex[:12]
    changes, activations = {}, {}
    normal_fixture = normal_round = None
    try:
        urls = []
        for name in (primary_name, peer_name):
            dsn = make_conninfo(h.env.values['APP_DATABASE_URL'], application_name=name)
            _, url, ready = h.start_api(source, updates={'DATABASE_URL': dsn})
            urls.append(url)
            public_json(args.output / (name + '-identity.json'), json.loads(ready.read_text()))
        schema = storage.catalog(h, new_tables=NEW_TABLES, mutable_tables=MUTABLE_TABLES)
        book.gates['schema_privileges'] = {'passed': True, 'evidence': schema}
        for name in selected:
            function, affected = SCENARIOS[name]
            flow = None
            try:
                f = seed_authority(h.env, args.private / (name + '-fixture.json'),
                                   'runtime-acceptance-a2-' + name,
                                   b_seconds=25 if name == 'expiry' else None)
                h.fixture_tokens.extend(a['token'] for a in f['actors'].values())
                register_contract(h, source, f, registry)
                flow = Flow(h, urls[0], f)
                if name.startswith('change_first_'):
                    mode = name.removeprefix('change_first_')
                    result = concurrent.change_first(flow, mode, peer_url=urls[1],
                        primary_name=primary_name, peer_name=peer_name)
                    changes[mode] = result
                    book.check('A2-14', mode + '_first_rejected', True, evidence=result)
                elif name.startswith('activation_first_'):
                    mode = name.removeprefix('activation_first_')
                    result = concurrent.activation_first(flow, mode, peer_url=urls[1],
                        primary_name=primary_name, peer_name=peer_name)
                    activations[mode] = result
                    if mode == 'amend':
                        book.check('A2-15', 'waiting_amend_rejected', True, evidence=result)
                elif name == 'draft_profile':
                    result = function(flow, book, source)
                else:
                    result = function(flow, book)
                if name == 'versions':
                    normal_fixture, normal_round = f, flow.round_id
                book.metadata['scenarios'][name] = {'status': 'completed', 'scope_id': f['scope_id']}
                public_json(args.output / (name + '-evidence.json'), result)
            except Exception as error:
                details = {'exception_type': type(error).__name__, 'frames': safe_traceback_frames(error)}
                public_json(args.output / (name + '-failure.json'), details)
                book.metadata['scenarios'][name] = {'status': 'failed', **details}
                for case in affected:
                    book.failure(case, 'Scenario failed: ' + name + ' (' + type(error).__name__ + ')')
                if not args.keep_going:
                    raise
            finally:
                if flow:
                    private_json(args.private / (name + '-commands.json'), flow.commands)
                    flow.close()
                book.save()
        if set(changes) == {'amend', 'source', 'revoke'}:
            book.check('A2-14', 'lock_order_observed', True, evidence=changes)
        if set(activations) == {'amend', 'source', 'revoke'}:
            book.check('A2-15', 'activate_first_history_commits', True, evidence=activations)
            book.check('A2-15', 'later_source_and_revoke_preserve_history', True,
                       evidence={k: activations[k] for k in ('source', 'revoke')})
            book.check('A2-15', 'lock_order_observed', True, evidence=activations)
        if normal_fixture:
            proof = storage.guarded_no_capability(h, normal_fixture,
                table='gov_formation_round_state', column='input_set_version',
                key_column='object_id', key=normal_round)
            book.gates['write_capability'] = {'passed': True, 'evidence': proof}
            proof = storage.immutable_owner_probe(h, normal_fixture,
                table='gov_activation_records', column='manifest_hash',
                key_column='round_object_id', key=normal_round)
            book.gates['immutable_history'] = {'passed': True, 'evidence': proof}
    except Exception as error:
        book.save(run_error={'exception_type': type(error).__name__, 'frames': safe_traceback_frames(error)})
        raise
    finally:
        final_source = source_manifest(source)
        public_json(args.output / 'source-after.json', final_source)
        book.gates['source_unchanged'] = {'passed': final_source == initial_source}
        h.close()
        result = book.save()
        print(json.dumps({k: result[k] for k in ('a2_local_matrix_accepted', 'passed', 'failed',
                                               'not_complete', 'checks_passed')}, ensure_ascii=False))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--source', type=Path, default=ROOT / 'src')
    p.add_argument('--private', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--scenario', action='append', choices=SCENARIOS)
    p.add_argument('--keep-going', action='store_true')
    args = p.parse_args()
    execute(args)


if __name__ == '__main__':
    main()
