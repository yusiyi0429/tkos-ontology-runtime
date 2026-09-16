"""Read-only HTTP/SQL checks against a source server and lawful isolated fixture.

The fixture is prepared by acceptance.dashboard_0_3.run. This observer performs
no business writes and emits only check names/counts, never records or tokens.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from acceptance.protocol_a1_independent.support import Harness, public_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--identities', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.identities.read_text())
    harness = Harness(args.env_file, args.output, args.private)
    before = harness.snapshot(fixture)
    checks = []

    def check(name: str, passed: bool) -> None:
        checks.append({'name': name, 'passed': bool(passed)})
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)

    with httpx.Client(base_url=args.url, timeout=30, trust_env=False) as client:
        facade = '/dashboard/api/v1'
        response = client.get(facade + '/ontology/catalog')
        check('catalog_current_authority_and_no_store', response.status_code == 200
              and response.headers.get('cache-control') == 'no-store')
        catalog = response.json()
        root = Path(__file__).resolve().parents[2]
        files = {'tkos.method/0.1': 'runtime-method-registry.json',
                 'tkos.method/0.2': 'runtime-method-registry-0.2.json',
                 'tkos.method/0.3': 'runtime-method-registry-0.3.json'}
        for version in catalog['versions']:
            expected = json.loads((root / 'docs' / files[version['contract_version']]).read_text())
            check('registry_exact_' + version['contract_version'],
                  sorted(version['object_types']) == sorted(expected['object_types']))
        check('embedded_definitions_not_registered',
              not {'Battlefield', 'Capability', 'Outcome'}.intersection(catalog['types']))
        records = {}
        for kind in catalog['types']:
            cursor = None
            items = []
            visited = set()
            while True:
                params = {'object_type': kind, 'limit': 2}
                if cursor:
                    params['cursor'] = cursor
                response = client.get(facade + '/catalog/objects', params=params)
                response.raise_for_status()
                page = response.json()
                assert page['loaded_count'] == len(page['items'])
                assert page['has_more'] == bool(page['next_cursor'])
                items.extend(page['items'])
                cursor = page['next_cursor']
                if not cursor:
                    break
                assert cursor not in visited, 'repeated cursor'
                visited.add(cursor)
            records[kind] = items
            check('readable_pagination_' + kind,
                  len(items) == len({item['object_id'] for item in items}))
        versions_match = True
        for item in records['Mission'] + records['PCO'] + records['OperatingState']:
            response = client.get(facade + '/objects/' + item['object_id'],
                                  params={'revision_id': item['basis_revision_id']})
            response.raise_for_status()
            detail = response.json()
            versions_match &= item['object_version'] == detail['selected_revision']['object_version']
        check('catalog_version_number_matches_selected_revision', versions_match)
        confirmed_agreements = []
        for item in records['StrategicAgreement']:
            detail = client.get(facade + '/objects/' + item['object_id'], params={
                'revision_id': item['basis_revision_id']}).json()
            if (detail['content_confirmation']['confirmed_for_selected_revision']
                    and item['basis_revision_id'] == item['effective_revision_id']):
                confirmed_agreements.append(item)
        check('confirmed_agreement_remains_formal', bool(confirmed_agreements)
              and all(item['formal_state']['formal'] for item in confirmed_agreements))
        issues_with_research = 0
        for item in records['StrategicIssue']:
            detail = client.get(facade + '/objects/' + item['object_id'], params={
                'revision_id': item['basis_revision_id']}).json()
            if any(edge['object_type'] in {'ResearchPlan', 'ResearchReport', 'ResearchMemo'}
                   for edge in detail['relations']['downstream']['items']):
                issues_with_research += 1
        check('strategic_issue_expands_real_research_refs', issues_with_research > 0)
        for kind in ('Battlefield', 'Capability', 'Outcome', 'NotRegistered'):
            response = client.get(facade + '/catalog/objects', params={'object_type': kind})
            check('reject_unregistered_' + kind, response.status_code == 422)
        for limit in (0, 101):
            response = client.get(facade + '/catalog/objects',
                                  params={'object_type': 'Mission', 'limit': limit})
            check('reject_limit_' + str(limit), response.status_code == 422)
        response = client.get('/v1/dashboard/ontology/catalog')
        check('direct_catalog_requires_authentication', response.status_code == 401)
        outsider = {'Authorization': 'Bearer ' + fixture['actors']['outsider']['token']}
        response = client.get('/v1/dashboard/catalog/objects', headers=outsider,
                              params={'object_type': 'Mission'})
        page = response.json()
        check('outsider_has_no_records_or_hidden_counts', response.status_code == 200
              and page['items'] == [] and page['loaded_count'] == 0
              and not page['has_more'] and not page['next_cursor'])
        first = client.get(facade + '/catalog/objects',
                           params={'object_type': 'Mission', 'limit': 1}).json()
        assert first['next_cursor'], 'fixture must have multiple Missions'
        response = client.get(facade + '/catalog/objects', params={
            'object_type': 'PCO', 'limit': 1, 'cursor': first['next_cursor']})
        check('cursor_cannot_change_type', response.status_code == 422)
        response = client.get('/v1/dashboard/catalog/objects', headers=outsider, params={
            'object_type': 'Mission', 'limit': 1, 'cursor': first['next_cursor']})
        check('cursor_cannot_change_identity', response.status_code == 422)
        response = client.post(facade + '/catalog/objects', json={'object_type': 'Mission'})
        check('catalog_exposes_no_write', response.status_code == 405)
    check('database_unchanged_by_all_reads', before == harness.snapshot(fixture))
    summary = {'scope': 'ontology-catalog-independent-http-sql',
               'passed': sum(c['passed'] for c in checks),
               'failed': sum(not c['passed'] for c in checks), 'checks': checks,
               'browser_accepted': False, 'deployed': False}
    public_json(args.output / 'summary.json', summary)
    if summary['failed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
