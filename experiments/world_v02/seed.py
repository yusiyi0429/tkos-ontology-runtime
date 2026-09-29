"""实验 E（票 #67）：在新建的隔离库上新开一个随机 scope，经 HTTP 播种五个场景，再做回放检查。

身份只由 owner SQL 播种（沿用 0.1 实验的 seed_scope），控制面经维护 CLI 装各域激活策略与 world 0.2 的 profile、默认
策略、支持登记，业务记录一律经 HTTP 的 prepare 与 commit 写入。每次运行新开 scope，可以在同一个库里重复运行。库用
method_v05 的建库工具新建并升级到工作区源码（见 README）。

    python -m experiments.world_v02.seed --env-file P/env.json --private P2 --output O2

不打印凭据；凭据只写进 --private 下仅本人可读的文件，manifest.json 不含凭据。只有回放检查全部通过，退出码才为 0。
"""
from __future__ import annotations

import argparse
from datetime import timezone
import json
from pathlib import Path
import uuid

from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json, public_json
from acceptance.world_v02.fixture import action_roles, register_world_v02
from experiments.world_v01.seed import seed_scope

from . import gold, replay, spec

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[1]
SCENARIOS = FOLDER / 'scenarios.json'
GOLD = FOLDER / 'gold.json'
CONTRACT = 'tkos.world/0.2'
REASON = 'Experiment E scenario seeding (tkos.world/0.2)'
LABEL = 'v02-e'  # 随机 tenant 的前缀：experiment-world-v02-e-<随机>
READER = 'ceo'  # 回放检查与版本读取用的身份：scope 内有生效指派的人都能读全部 world 对象


def new_scope(h, scenarios: dict) -> dict:
    """新开一个 scope（随机 tenant），建域与身份：身份按角色名显示，每条角色一条指派，各持一个凭证。"""
    single = {'scopes': {LABEL: {'domains': scenarios['domains']}},
              'identities': {key: {**identity, 'scope': LABEL} for key, identity in scenarios['identities'].items()}}
    return seed_scope(h.env, LABEL, single)


def install(h, source: Path, scope: dict) -> None:
    """控制面：各域装激活策略（门动作按登记的门角色，其余已实现动作对全部 world 角色开放），再装 0.2 的 profile、
    默认策略（默认契约 0.2）与支持登记。"""
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    for domain_id in scope['domains'].values():
        tag = uuid.uuid4().hex[:8]
        path = h.private / f'activation-{tag}.json'
        private_json(path, {'action_roles': action_roles(), 'notes': 'Experiment E scenarios (tkos.world/0.2)'})
        adapter.cli('experiment-e-activation-' + tag, [
            'install-activation-policy', '--scope-id', scope['scope_id'], '--domain-id', domain_id,
            '--content-json', str(path), '--reason', REASON], expected_exit=0)
    register_world_v02(h, source, scope['scope_id'])


class Seeder:
    """按步骤写入，记下每个对象键的 object_id、类型与最新版本、每个事件键的 event_id、每个写 now 的时刻。"""

    def __init__(self, h, clients: dict, scenarios: dict, scope: dict):
        self.h, self.clients, self.scenarios, self.scope = h, clients, scenarios, scope
        self.principals = {key: actor['principal_id'] for key, actor in scope['actors'].items()}
        self.objects: dict[str, dict] = {}
        self.events: dict[str, str] = {}
        self.times: dict[str, str] = {}

    def now(self) -> str:
        """这一步的时刻：数据库时钟（只读 SQL），晚于此前记下的每条事件、早于这一步的记录时刻。"""
        moment = self.h.sql(self.scope, 'SELECT clock_timestamp() AS now')[0]['now']
        return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    def timed(self, value: dict, field: str, key: str | None) -> dict:
        if value.get(field) != spec.NOW:
            return value
        value = {**value, field: self.now()}
        if key:
            self.times[key] = value[field]
        return value

    def read(self, key: str) -> dict:
        return self.clients[READER].json('GET', f"/v1/world/objects/{self.objects[key]['object_id']}")['business']

    def act(self, step: dict, params: dict, target: str | None = None) -> dict:
        body = {'action_type': spec.action(step), 'contract_version': CONTRACT, 'target': None, 'expected_versions': [],
                'idempotency_key': 'experiment-e-' + uuid.uuid4().hex, 'reason': REASON,
                'params': spec.resolve(params, self.objects, self.principals, self.events)}
        if target is not None:
            business = self.read(target)
            body['target'] = {'object_id': business['object_id'], 'revision_id': business['revision_id'],
                              'expected_version': business['object_version']}
        client = self.clients[step['by']]
        body['expected_versions'] = client.json('POST', '/v1/actions/prepare', body)['expected_versions']
        receipt = client.json('POST', '/v1/actions', body)
        assert receipt['status'] == 'committed' and receipt['result']['contract_version'] == CONTRACT, receipt
        return receipt

    def apply(self, step: dict) -> None:
        do, key = step['do'], step.get('key')
        if do == 'create':
            receipt = self.act(step, {'domain_id': self.scope['domains'][step['domain']], 'object_type': step['type'],
                                      'payload': step['payload']})
        elif do == 'refresh':
            receipt = self.act(step, {'payload': self.timed(step['payload'], 'as_of', key)})
        elif do == 'record':
            receipt = self.act(step, self.timed(step['params'], 'occurred_at', key))
        elif do == 'revise':
            receipt = self.act(step, {'payload': step['payload']}, step['target'])
        elif do == 'relate':
            receipt = self.act(step, {'field': step['field'], 'refs': step['refs']}, step['target'])
        elif do == 'assign':
            receipt = self.act(step, {'principal_id': f"${step['to']}"}, step['target'])
        else:  # 门与生命周期
            receipt = self.act(step, step['params'], step['target'])
        result = receipt['result']
        if do in spec.MAKES_OBJECT:
            self.objects[key] = {'object_id': result['object_id'], 'version': result['version'],
                                 'type': spec.object_type(step)}
        elif key:
            self.events[key] = result['event_id']
        if 'target' in step:  # 修订、建关系、指派与写回候选的确认出新版本
            self.objects[step['target']]['version'] = self.read(step['target'])['version']


def seed(h, source: Path, url: str, scenarios: dict, answers: dict) -> tuple[dict, dict, dict]:
    """新开 scope、装控制面、经 HTTP 按步骤播种；核对每个对象播种结束时的版本与场景文件推演的一致。返回 manifest
    （不含凭据）、各身份的 HTTP 客户端（由调用者关闭）与 scope（含凭据，只写进私有文件）。"""
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    scope = new_scope(h, scenarios)
    private_json(h.private / f"scope-{scope['scope_id'][:8]}.json", scope)
    install(h, source, scope)
    clients = h.clients(url, {'actors': scope['actors']})
    try:
        seeder = Seeder(h, clients, scenarios, scope)
        for _, step in spec.steps(scenarios):
            seeder.apply(step)
        seeded = {key: item['version'] for key, item in seeder.objects.items()}
        expected = spec.final_versions(scenarios)
        if seeded != expected:
            raise AssertionError({key: (seeded.get(key), expected.get(key)) for key in set(seeded) | set(expected)
                                  if seeded.get(key) != expected.get(key)})
    except BaseException:
        for client in clients.values():
            client.close()
        raise
    manifest = {
        'content_sha256': gold.content_sha256(answers, scenarios),  # 与批准段里的哈希同一口径
        'scope': {'scope_id': scope['scope_id'], 'tenant_id': scope['tenant_id']},
        'domains': scope['domains'], 'identities': seeder.principals,
        'objects': seeder.objects, 'events': seeder.events, 'times': seeder.times,
    }
    return manifest, clients, scope


def run(env_file: Path, private: Path, output: Path) -> dict:
    if private.exists() or output.exists():
        raise ValueError('use fresh private and output paths')
    scenarios = json.loads(SCENARIOS.read_text())
    answers = json.loads(GOLD.read_text())
    private.mkdir(parents=True, mode=0o700)
    source = ROOT / 'src'
    h = MethodHarness(env_file.resolve(), output.resolve(), private.resolve())
    clients: dict = {}
    try:
        _, url, _ = h.start_api(source)
        manifest, clients, scope = seed(h, source, url, scenarios, answers)
        public_json(output / 'manifest.json', manifest)
        result = replay.check(answers, scenarios, manifest, clients[READER], h, scope)
        public_json(output / 'replay.json', result)
        return result
    finally:
        for client in clients.values():
            client.close()
        h.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.env_file, args.private, args.output)
    print(json.dumps({'recoverable': result['recoverable'], 'scenarios': result['summary']}, ensure_ascii=False))
    if not result['recoverable']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
