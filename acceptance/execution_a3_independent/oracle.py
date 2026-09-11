"""Independent database/read observations, never application mutation helpers."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import uuid

from psycopg import sql


def rows(flow, table):
    assert table.startswith('gov_')
    with flow.h.app_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (flow.f['scope_id'],))
        result = conn.execute(sql.SQL('SELECT to_jsonb(t) AS row FROM {} t WHERE scope_id=%s ORDER BY to_jsonb(t)::text').format(
            sql.Identifier(table)), (flow.f['scope_id'],)).fetchall()
    return [r['row'] for r in result]


def denied(flow, actor, body, codes, *, prepare=True):
    """A semantic rejection must not be substituted by stale CAS or a 500."""
    if isinstance(codes, str):
        codes = {codes}
    before = flow.h.snapshot(flow.f)
    proofs = []
    for path in (['/v1/actions/prepare', '/v1/actions'] if prepare else ['/v1/actions']):
        response = flow.clients[actor].request('POST', path, deepcopy(body), expected=(403, 404, 409, 422))
        code = response.json()['error']['code']
        assert code in codes, (path, code, sorted(codes))
        assert flow.h.snapshot(flow.f) == before
        proofs.append({'path': path, 'status': response.status_code, 'error': code,
                       'all_business_state_unchanged': True})
    return proofs


def fresh(flow, body, *, actor=None):
    """Refresh only known mutable CAS, retaining the deliberately old content."""
    request = deepcopy(body)
    request['idempotency_key'] = 'a3-negative-' + uuid.uuid4().hex
    actor = actor or 'ceo'
    if request.get('target'):
        request['target']['expected_version'] = flow.object(request['target']['object_id'], actor)['object_version']
    for version in request.get('expected_versions', []):
        version['expected_version'] = flow.object(version['object_id'], actor)['object_version']
    return request


def evidence_bytes(flow, upload):
    result = upload['response']
    actual = flow.clients['ceo'].request('GET',
        f"/v1/evidence-assets/{result['object_id']}/revisions/{result['revision_id']}").content
    assert actual.hex() == upload['bytes_hex']
    assert hashlib.sha256(actual).hexdigest() == result['sha256']
    revision = flow.clients['ceo'].revision(result['object_id'], result['revision_id'])
    assert revision['payload_hash'] == result['payload_hash']
    return {'revision_id': result['revision_id'], 'sha256': result['sha256'],
            'actual_bytes_verified': True, 'expected_author': flow.f['actors'][upload['actor']]['principal_id']}


def receipt(flow, value):
    assert value['effect_task_ids'] == []
    read = flow.clients['ceo'].json('GET', '/v1/action-receipts/' + value['receipt_id'])
    assert read['receipt'] == value and read['effects'] == []
    return {'receipt_id': value['receipt_id'], 'retrievable': True, 'effect_task_ids': []}


def no_effects(flow):
    tasks = flow.h.sql(flow.f,
        'SELECT count(*) AS n FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s',
        (flow.f['tenant_id'], flow.f['company_id']))[0]['n']
    assert tasks == 0
    assert all(r['effect_task_ids'] == [] for r in flow.receipts)
    return {'task_count': tasks, 'receipts_checked': len(flow.receipts)}
