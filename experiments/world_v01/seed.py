"""把 E&O 九月回放播种到实验库，再做回放检查。身份只由 owner SQL 播种，控制面经维护 CLI 装 world，业务记录一律经
HTTP 的 prepare 与 commit 写入。每次运行新开 scope，可以在同一个实验库里重复运行；清理即删掉整个实验库。

    建库  python -m experiments.world_v01.seed create --env-file .runtime-acceptance/env.json --private P --output O
    播种  python -m experiments.world_v01.seed run --env-file P/env.json --private P2 --output O2
    清理  python -m experiments.world_v01.seed clean --env-file P/env.json

不打印凭据；凭据只写进 --private 下仅本人可读的文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from acceptance.execution_a3_independent.database import socket_sql
from acceptance.method_independent.database import CONTAINER
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import Environment, private_json, public_json
from acceptance.world_v01 import database
from acceptance.world_v01.fixture import GATE_ROLES, WORLD_ACTIONS, WORLD_ROLES, register_world

from . import gold, replay, spec

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[1]
SEED = FOLDER / 'seed.json'
GOLD = FOLDER / 'gold.json'
CONTRACT = 'tkos.world/0.1'
REASON = 'E&O September replay seeding (tkos.world/0.1 experiment)'


def _declare(conn, scope_id: str) -> None:
    """owner 连接在本事务里声明写能力、打开控制面并设 scope（与验收夹具相同的合成控制面操作）。"""
    conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
    conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
    conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))


def seed_scope(env: Environment, name: str, seed: dict) -> dict:
    """新开一个 scope（随机 tenant），建域与身份：身份按角色名显示，每条角色一条指派，各持一个凭证。"""
    scope = {'scope_id': str(uuid.uuid4()), 'tenant_id': f'experiment-world-{name}-{uuid.uuid4().hex[:8]}',
             'company_id': str(uuid.uuid4()), 'actors': {},
             'domains': {domain: str(uuid.uuid4()) for domain in seed['scopes'][name]['domains']}}
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:  # 退出时提交
        _declare(conn, scope['scope_id'])
        conn.execute('INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES (%s,%s,%s)',
                     (scope['scope_id'], scope['tenant_id'], scope['company_id']))
        for domain, display in seed['scopes'][name]['domains'].items():
            conn.execute('INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)',
                         (scope['domains'][domain], scope['scope_id'], display))
        for key, identity in seed['identities'].items():
            if identity['scope'] != name:
                continue
            principal, token = str(uuid.uuid4()), secrets.token_urlsafe(48)
            digest = hashlib.sha256(token.encode()).hexdigest()
            conn.execute('INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)',
                         (principal, scope['scope_id'], identity['type'], identity['display_name']))
            for domain, roles in identity['roles'].items():
                for role in roles:
                    conn.execute('INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role)'
                                 ' VALUES (%s,%s,%s,%s,%s)',
                                 (str(uuid.uuid4()), scope['scope_id'], principal, scope['domains'][domain], role))
            conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)", (digest,))
            conn.execute('INSERT INTO gov_credentials(scope_id,principal_id,credential_digest,label) VALUES (%s,%s,%s,%s)',
                         (scope['scope_id'], principal, digest, 'Experiment ' + identity['display_name']))
            scope['actors'][key] = {'principal_id': principal, 'token': token}
    return scope


def install_world(h: MethodHarness, source: Path, scope: dict) -> None:
    """控制面：各域的激活策略（门动作按登记门表，其余动作对全部角色开放），再装 profile、默认策略与支持登记。"""
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    roles = {action: GATE_ROLES.get(action, WORLD_ROLES) for action in WORLD_ACTIONS}
    for domain_id in scope['domains'].values():
        path = h.private / f'activation-{uuid.uuid4().hex[:8]}.json'
        private_json(path, {'action_roles': roles, 'notes': 'E&O September replay experiment'})
        adapter.cli('experiment-activation-' + uuid.uuid4().hex[:8], [
            'install-activation-policy', '--scope-id', scope['scope_id'], '--domain-id', domain_id,
            '--content-json', str(path), '--reason', REASON], expected_exit=0)
    register_world(h, source, scope)


class Seeder:
    """按步骤写入，记下每个对象键的 object_id 与最新版本、每个事件键的 event_id。"""

    def __init__(self, h, clients, seed, scopes):
        self.h, self.clients, self.seed, self.scopes = h, clients, seed, scopes
        self.principals = {key: actor['principal_id'] for scope in scopes.values() for key, actor in scope['actors'].items()}
        self.objects: dict[str, dict] = {}
        self.events: dict[str, str] = {}

    def scope_of(self, identity: str) -> dict:
        return self.scopes[self.seed['identities'][identity]['scope']]

    def act(self, step: dict, action: str, params: dict, target: str | None = None) -> dict:
        client = self.clients[step['by']]
        body = {'action_type': action, 'contract_version': CONTRACT, 'target': None, 'expected_versions': [],
                'idempotency_key': 'experiment-' + uuid.uuid4().hex, 'reason': REASON,
                'params': spec.resolve(params, self.objects, self.principals)}
        if target is not None:
            view = self.read(step['by'], target)
            body['target'] = {'object_id': view['object_id'], 'revision_id': view['revision_id'],
                              'expected_version': view['object_version']}
        prepared = client.json('POST', '/v1/actions/prepare', body)
        body['expected_versions'] = prepared['expected_versions']
        receipt = client.json('POST', '/v1/actions', body)
        assert receipt['status'] == 'committed', receipt
        return receipt

    def read(self, identity: str, key: str) -> dict:
        return self.clients[identity].json('GET', f"/v1/world/objects/{self.objects[key]['object_id']}")

    def apply(self, step: dict) -> None:
        do = step['do']
        declared = {'declaration': step['declaration']} if 'declaration' in step else {}
        if do == 'create':
            scope = self.scope_of(step['by'])
            receipt = self.act(step, 'world_create_object', {
                'domain_id': scope['domains'][step['domain']], 'object_type': step['type'],
                'payload': step['payload'], **declared})
        elif do == 'refresh':
            receipt = self.act(step, 'world_refresh_state', {'payload': step['payload'], **declared})
        elif do == 'record':
            receipt = self.act(step, 'world_record_event', {**step['params'], **declared})
        elif do == 'revise':
            receipt = self.act(step, 'world_revise_object', {'payload': step['payload'], **declared}, step['target'])
        elif do == 'relate':
            receipt = self.act(step, 'world_relate', {'field': step['field'], 'refs': step['refs'], **declared}, step['target'])
        elif do == 'assign':
            receipt = self.act(step, 'world_assign', {'principal_id': f"${step['to']}"}, step['target'])
        else:
            receipt = self.act(step, step['action'], step['params'], step['target'])
        if do in spec.MAKES_OBJECT:
            self.objects[step['key']] = {'object_id': receipt['result']['object_id'], 'version': 1,
                                         'scope': self.seed['identities'][step['by']]['scope'],
                                         'type': spec.object_type(step)}
        if 'target' in step:  # 修订、建关系、指派、门都可能出新版本
            self.objects[step['target']]['version'] = self.read(step['by'], step['target'])['version']
        if step.get('key') and do in spec.MAKES_EVENT:
            scope = self.scope_of(step['by'])
            self.events[step['key']] = str(self.h.sql(scope, 'SELECT event_id FROM gov_world_events'
                                                             ' WHERE scope_id=%s AND action_id=%s',
                                                      (scope['scope_id'], receipt['receipt_id']))[0]['event_id'])


def run(env_file: Path, private: Path, output: Path) -> dict:
    if private.exists() or output.exists():
        raise ValueError('use fresh private and output paths')
    seed = json.loads(SEED.read_text())
    answers = json.loads(GOLD.read_text())
    spec.validate(seed)
    spec.validate_gold(answers, seed)
    private.mkdir(parents=True, mode=0o700)
    source = ROOT / 'src'
    h = MethodHarness(env_file.resolve(), output.resolve(), private.resolve())
    clients = {}
    try:
        scopes = {name: seed_scope(h.env, name, seed) for name in seed['scopes']}
        private_json(private / 'scopes.json', scopes)
        for scope in scopes.values():
            install_world(h, source, scope)
        _, url, _ = h.start_api(source)
        clients = h.clients(url, {'actors': {key: actor for scope in scopes.values()
                                             for key, actor in scope['actors'].items()}})
        seeder = Seeder(h, clients, seed, scopes)
        for step in seed['steps']:
            seeder.apply(step)
        manifest = {
            'content_sha256': gold.content_sha256(answers, seed),  # 与批准段里的哈希同一口径
            'scopes': {name: {'scope_id': scope['scope_id'], 'tenant_id': scope['tenant_id']} for name, scope in scopes.items()},
            'identities': seeder.principals, 'objects': seeder.objects, 'events': seeder.events,
        }
        public_json(output / 'manifest.json', manifest)
        result = replay.check(answers, seed, manifest, clients, h, scopes)
        public_json(output / 'replay.json', result)
        return result
    finally:
        for client in clients.values():
            client.close()
        h.close()


def create(env_file: Path, private: Path, output: Path) -> dict:
    """新建实验库：用验收的建库工具从基线迁移，再把工作区 src 的 world 迁移作为升级应用。"""
    created = database.create(env_file, private, output)
    upgraded = database.upgrade(private / 'env.json', ROOT / 'src', output.with_name(output.name + '-upgrade'))
    return {'database': created['database'], 'applied': upgraded['applied'], 'private_environment': str(private / 'env.json')}


def clean(env_file: Path) -> dict:
    """删掉整个实验库并核对它已不存在；只删验收建库工具生成的 tkos_a1_world_* 库。"""
    env = Environment(env_file)
    name = conninfo_to_dict(env.values['MIGRATION_DATABASE_URL'])['dbname']
    if not database.DATABASE.fullmatch(name):
        raise ValueError('clean only drops a generated experiment database')
    socket_sql(CONTAINER, 'postgres', sql.SQL('DROP DATABASE {} WITH (FORCE);').format(sql.Identifier(name)).as_string())
    with psycopg.connect(make_conninfo(env.values['MIGRATION_DATABASE_URL'], dbname='postgres')) as conn:
        left = conn.execute('SELECT count(*) FROM pg_database WHERE datname=%s', (name,)).fetchone()[0]
    assert left == 0, 'the experiment database is still there'
    return {'database': name, 'dropped': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['create', 'run', 'clean'])
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'clean':
        print(json.dumps(clean(args.env_file)))
        return
    if args.private is None or args.output is None:
        parser.error(f'{args.command} needs --private and --output')
    if args.command == 'create':
        print(json.dumps(create(args.env_file, args.private, args.output)))
        return
    result = run(args.env_file, args.private, args.output)
    print(json.dumps({key: result[key] for key in ('recoverable', 'questions', 'decoys')}, ensure_ascii=False))
    if not result['recoverable']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
