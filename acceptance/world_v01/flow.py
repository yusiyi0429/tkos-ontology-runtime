"""world 0.1 的公开 HTTP 驱动：prepare + commit、拒绝时两条入口都校验库快照不变。"""
from __future__ import annotations

from copy import deepcopy

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

    def deny(self, actor, body, *, codes, prepare=True):
        """先后打 prepare 与 commit 两个入口（或只打 commit），每次都核对全部业务表不变。"""
        before = self.h.snapshot(self.f)
        paths = ['/v1/actions/prepare', '/v1/actions'] if prepare else ['/v1/actions']
        for path in paths:
            response = self.clients[actor].json('POST', path, body, expected={400, 401, 403, 404, 409, 422})
            code = response.get('error', {}).get('code')
            assert code in codes, (path, code, codes)
            assert self.h.snapshot(self.f) == before, path

    def read(self, actor, oid, *, expected=200):
        return self.clients[actor].json('GET', f'/v1/world/objects/{oid}', expected=expected)

    def company_params(self, *, title='E&O 合成公司', blocks=None, domain='company'):
        return {'domain_id': self.f['domains'][domain], 'object_type': 'Company',
                'payload': {'title': title, 'blocks': blocks if blocks is not None else {}}}

    def rows(self, statement, params=()):
        return self.h.sql(self.f, statement, params)
