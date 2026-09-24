"""world 0.1 的公开 HTTP 驱动：prepare + commit、拒绝时两条入口都校验库快照不变。"""
from __future__ import annotations

from copy import deepcopy
from urllib.parse import quote

from acceptance.runtime.client import Client

CONTRACT = 'tkos.world/0.1'


class Flow:
    def __init__(self, h, url, f):
        self.h, self.url, self.f = h, url, f
        self.clients = h.clients(url, f)
        self.receipts = []

    def close(self):
        for client in self.clients.values():
            client.close()

    def command(self, kind, params, *, key=None, expected_versions=None):
        body = Client.command(kind, deepcopy(params), expected_versions=expected_versions, key=key)
        body['contract_version'] = CONTRACT
        return body

    def prepare(self, actor, body):
        result = self.clients[actor].json('POST', '/v1/actions/prepare', body)
        body['expected_versions'] = result['expected_versions']
        return body

    def commit(self, actor, body):
        receipt = self.clients[actor].json('POST', '/v1/actions', body)
        assert receipt['status'] == 'committed' and receipt['effect_task_ids'] == []
        self.receipts.append(receipt)
        return receipt

    def act(self, actor, kind, params, *, key=None):
        return self.commit(actor, self.prepare(actor, self.command(kind, params, key=key)))

    def deny(self, actor, body, *, codes, prepare=True, says=None):
        """先后打 prepare 与 commit 两个入口（或只打 commit），每次都核对全部业务表不变；says 为错误信息须含的片段。"""
        before = self.h.snapshot(self.f)
        paths = ['/v1/actions/prepare', '/v1/actions'] if prepare else ['/v1/actions']
        for path in paths:
            response = self.clients[actor].json('POST', path, body, expected={400, 401, 403, 404, 409, 422})
            code = response.get('error', {}).get('code')
            assert code in codes, (path, code, codes)
            assert says is None or says in response['error']['message'], (path, response, says)
            assert self.h.snapshot(self.f) == before, path

    def read(self, actor, oid, *, version=None, expected=200):
        suffix = f'?version={version}' if version is not None else ''
        return self.clients[actor].json('GET', f'/v1/world/objects/{oid}{suffix}', expected=expected)

    def state(self, actor, oid, *, as_of=None, expected=200):
        suffix = f'?as_of={quote(as_of)}' if as_of else ''
        return self.clients[actor].json('GET', f'/v1/world/objects/{oid}/state{suffix}', expected=expected)

    def events(self, actor, oid, *, since=None, expected=200):
        suffix = f'?since={quote(since)}' if since else ''
        return self.clients[actor].json('GET', f'/v1/world/objects/{oid}/events{suffix}', expected=expected)

    def refresh(self, actor, payload, declaration=None):
        return self.act(actor, 'world_refresh_state', {'payload': payload, **({'declaration': declaration} if declaration else {})})

    def record(self, actor, params):
        return self.act(actor, 'world_record_event', params)

    def children(self, actor, oid, *, expected=200):
        return self.clients[actor].json('GET', f'/v1/world/objects/{oid}/children', expected=expected)

    def target(self, oid):
        """当前最新修订作为目标：对象 id、最新修订 id 与期望版本。"""
        view = self.read('outsider', oid)
        return {'object_id': oid, 'revision_id': view['revision_id'], 'expected_version': view['object_version']}

    def targeted(self, kind, oid, params, target=None):
        body = self.command(kind, params)
        body['target'] = target or self.target(oid)
        return body

    def revise(self, actor, oid, patch, declaration=None):
        params = {'payload': patch, **({'declaration': declaration} if declaration else {})}
        return self.commit(actor, self.prepare(actor, self.targeted('world_revise_object', oid, params)))

    def relate(self, actor, oid, field, refs, declaration=None):
        params = {'field': field, 'refs': refs, **({'declaration': declaration} if declaration else {})}
        return self.commit(actor, self.prepare(actor, self.targeted('world_relate', oid, params)))

    def company_params(self, *, title='E&O 合成公司', blocks=None, domain='company'):
        return {'domain_id': self.f['domains'][domain], 'object_type': 'Company',
                'payload': {'title': title, 'blocks': blocks if blocks is not None else {}}}

    def create_params(self, object_type, domain, payload, declaration=None):
        params = {'domain_id': self.f['domains'][domain], 'object_type': object_type,
                  'payload': {'blocks': {}, **payload}}
        if declaration is not None:
            params['declaration'] = declaration
        return params

    def create(self, actor, object_type, domain, payload, declaration=None):
        """经 prepare + commit 建一个 world 对象，返回回执。"""
        return self.act(actor, 'world_create_object', self.create_params(object_type, domain, payload, declaration))

    def deny_create(self, actor, object_type, domain, payload, declaration=None, **deny):
        """建对象被拒：同 deny，入口与库快照的核对不变。"""
        self.deny(actor, self.command('world_create_object', self.create_params(object_type, domain, payload, declaration)),
                  **deny)

    def rows(self, statement, params=()):
        return self.h.sql(self.f, statement, params)
