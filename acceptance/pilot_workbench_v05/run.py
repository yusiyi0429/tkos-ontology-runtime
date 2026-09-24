"""试点链「Method 0.5 × 治理工作台」独立 HTTP / PostgreSQL 验收。

新建隔离库（升级到 HEAD）与新随机 scope；Agent 一侧经 /v1/actions 以 Agent 凭证驱动（复用
acceptance/method_v05 的 Flow），人的步骤一律经工作台个人会话（/dashboard/api/v1）的
prepare / commit 办理，信封按浏览器 methodEnvelope 的形状、从服务端给本人的动作投影派生。
SQL 只用于身份播种与独立断言。不证明真实模型行为，不代替浏览器复核。

- ``run``：建库、播种、驱动整条链并记录具名检查，写脱敏的 ``summary.json``；
- ``serve``：对 run 留下的同一个库与 scope 起带工作台的 API，供浏览器复核；只打印 URL 与私有文件路径；
- ``stage`` / ``consolidate``：浏览器复核的 Agent 一侧，对正在运行的 serve 新开一个 0.5 复核窗口 / 关窗并收拢。

登录码、Runtime 凭证、会话 cookie、CSRF 令牌只在私有目录（0700/0600）或进程内存里，不打印、不进记录。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import uuid

import httpx

from acceptance.method_independent.harness import MethodHarness
from acceptance.method_v04.run import _change, candidate_refs
from acceptance.method_v05 import database
from acceptance.method_v05.fixture import register_v05, seed_v05
from acceptance.method_v05.flow import Flow, period
from acceptance.protocol_a1_independent.support import digest, private_json, public_json, source_manifest, wait
from acceptance.runtime.client import EvidenceLog
from memory_service_app.governance_accounts import provision

ROOT = Path(__file__).resolve().parents[2]
PREFIX = '/dashboard/api/v1'
V05 = 'tkos.method/0.5'
# 工作台用户名 -> 夹具身份。owner-a 只作为 M1A Agreement 的必要确认人。
PEOPLE = {'ceo': 'ceo', 'dri-a': 'dri_a', 'dri-b': 'dri_b', 'owner-a': 'owner_a'}
KEEP = object()

# 全部具名检查，按执行顺序；中途失败时其余记为 not_run，不会被当成通过。
PLAN = [
    ('login_wrong_code_and_unknown_user_are_401_login_failed', '1 登录'),
    ('login_right_code_returns_session_cookie_and_csrf', '1 登录'),
    ('m1a_human_steps_via_workbench_under_0_5', '8 其余人工步骤'),
    ('ceo_method_todos_list_the_0_5_ltco_drafts', '2 待办与 0.5 绑定'),
    ('csrf_missing_or_wrong_token_is_403_csrf_token_invalid', '5 CSRF'),
    ('fresh_csrf_from_get_session_then_commit_succeeds', '5 CSRF'),
    ('ceo_confirms_0_5_ltcos_via_workbench', '8 其余人工步骤'),
    ('ceo_todos_list_the_0_5_review_window', '2 待办与 0.5 绑定'),
    ('review_window_view_is_method_0_5_bound', '2 待办与 0.5 绑定'),
    ('participant_handles_the_0_5_window_via_workbench', '8 其余人工步骤'),
    ('scope_dris_commit_the_candidate_set_via_workbench', '8 其余人工步骤'),
    ('non_ceo_activation_is_403_forbidden_and_session_survives', '4 业务拒绝'),
    ('ceo_activates_the_0_5_candidate_set_via_workbench', '3 人工门与正式回执'),
    ('recommit_retry_and_reprepare_return_the_same_receipt', '6 幂等与响应丢失'),
    ('core_replay_of_the_original_envelope_returns_the_same_receipt', '6 幂等与响应丢失'),
    ('ceo_method_todos_list_the_0_5_period_review', '2 待办与 0.5 绑定'),
    ('lost_commit_response_recovered_from_my_submissions', '6 幂等与响应丢失'),
    ('ceo_confirms_the_0_5_period_review_via_workbench', '8 其余人工步骤'),
    ('restart_keeps_receipts_state_and_my_submissions', '7 重启恢复'),
    ('restart_drops_in_memory_sessions_old_cookie_401', '7 重启恢复'),
    ('fresh_login_after_restart_and_replay_returns_the_same_receipt', '7 重启恢复'),
    ('session_facade_rejects_agent_and_api_only_actions', '8 其余人工步骤'),
    ('hardcoded_0_3_command_on_a_0_5_object_is_rejected', '8 其余人工步骤'),
    ('private_command_hidden_from_another_person', '8 其余人工步骤'),
    ('cross_origin_write_is_rejected', '8 其余人工步骤'),
    ('logout_invalidates_the_session', '8 其余人工步骤'),
    ('ceo_confirms_the_company_constraint_via_workbench', '8 其余人工步骤'),
    ('ceo_cannot_confirm_a_scope_constraint_403', '4 业务拒绝'),
    ('scope_dri_confirms_the_scope_constraint_via_workbench', '8 其余人工步骤'),
]

LIMITATIONS = [
    '会话只在 API 进程内存里：重启后旧 cookie 一律 401，需要重新登录（设计如此，'
    'tests/governance/test_sessions.py::test_restart_requires_login）；本验收把它作为已知限制核对，不算失败。',
    '工作台命令日志是单实例本机文件（TKOS_GOVERNANCE_COMMANDS_DIR），「我的提交」跨重启靠它；不支持多实例共享。',
    '登录限流按对端地址计数，成功的登录也占每分钟 10 次的额度（governance_sessions.login）；本验收每个 API 进程登录不超过 6 次。',
    '响应丢失用测试专用启动器 acceptance/runtime/server.py 的 before_business_commit 挂起点制造（客户端读超时断开）；'
    '挂起点只能暂停，不能改变授权或结果。',
]

UNCOVERED = [
    '浏览器界面（R02 路由、R03 四类错误的页面表现、轮询不冲掉「加载更多」、读边失败重试、404 不置整面板失效）：'
    '本工具只验 HTTP 面，浏览器复核用 serve 模式另做。',
    '后台轮询不续会话（X-TKOS-Background）、会话 8 小时 / 空闲 30 分钟过期、登录限流 429、重置登录码使旧会话失效：只有单元测试'
    '（tests/governance/test_sessions.py）。',
    '撤权竞争（prepare 之后撤销任职再 commit）与同一命令并发提交：未驱动。',
    '0.5 人工动作中未经工作台驱动的：m1b_replace_comment、m1b_withdraw_comment、m1b_reopen_window、m1b_reopen_candidates、'
    'method_open_problem、method_revise_problem、method_close_problem；LTCO 的 maintained / revised 结论。',
    '0.5 Agent 能力中本链未驱动的：复盘重新生成、Constraint 修订、LTCO 修订、五个集合列表、公司集合视图（见 acceptance/method_v05）。',
    '证据上传：工作台没有上传能力，本链的合成证据由 CEO 凭证经 /v1/evidence-assets 上传。',
    '容器镜像入口、真实模型、Clark 接线、企业身份（SSO）、远程部署。',
]


def now():
    return datetime.now(timezone.utc).isoformat()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def private_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True)


def workbench_env(private: Path, port: int) -> dict:
    """与 acceptance/governance_workbench/serve.py 同一组开关；账号文件与命令日志都在私有目录。"""
    return {'TKOS_DASHBOARD_ENABLED': '1', 'TKOS_GOVERNANCE_WORKBENCH_ENABLED': '1',
            'TKOS_GOVERNANCE_ACCOUNTS_FILE': str(private / 'accounts.json'),
            'TKOS_GOVERNANCE_COMMANDS_DIR': str(private / 'commands'),
            'TKOS_DASHBOARD_ALLOWED_HOSTS': f'127.0.0.1:{port}',
            'TKOS_DASHBOARD_ALLOWED_ORIGINS': f'http://127.0.0.1:{port}',
            'TKOS_DASHBOARD_ENV_LABEL': '本机隔离试点链验收', 'TKOS_DASHBOARD_SYNTHETIC': 'true'}


def redact(value):
    if isinstance(value, dict):
        return {key: '[redacted]' if key == 'csrf' else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def code(data):
    return (data.get('error') or {}).get('code') if isinstance(data, dict) else None


def outcome(reply):
    return [reply[0], code(reply[1])]


# ------------------------------------------------------------------ 记录


class Book:
    """具名检查；每记一条就重写一次 summary，失败即停，未跑到的记为 not_run。"""

    def __init__(self, path: Path, meta: dict):
        self.path, self.meta, self.results = path, meta, {}
        self.save()

    def check(self, name: str, passed: bool, evidence: dict):
        if name not in dict(PLAN) or name in self.results:
            raise ValueError('检查不在计划内或被重复记录：' + name)
        self.results[name] = {'name': name, 'group': dict(PLAN)[name], 'passed': bool(passed), 'evidence': evidence}
        self.save()
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise AssertionError('check failed: ' + name)

    def save(self, **extra):
        self.meta.update(extra)
        checks = [self.results.get(name, {'name': name, 'group': group, 'passed': None}) for name, group in PLAN]
        passed = sum(item['passed'] is True for item in checks)
        public_json(self.path, {
            'scope': '试点链 Method 0.5 × 治理工作台：真实 API 进程 + PostgreSQL + 工作台个人会话（合成数据）',
            **self.meta, 'updated_at': now(),
            'runtime_pilot_workbench_v05_accepted': passed == len(PLAN) and not self.meta.get('run_error'),
            'planned': len(PLAN), 'passed': passed, 'failed': sum(item['passed'] is False for item in checks),
            'not_run': [item['name'] for item in checks if item['passed'] is None],
            'checks': checks, 'limitations': LIMITATIONS, 'uncovered': UNCOVERED,
            'browser': 'not_run（本工具只验 HTTP 面；serve 模式供浏览器复核）',
            'real_model': 'not_run', 'partner_wiring': 'not_verified', 'deployed': False})


# ------------------------------------------------------------------ 工作台会话


class Person:
    """一个人的独立会话：自己的 cookie 罐、Origin 与 CSRF 令牌，相当于一个独立浏览器 profile。"""

    def __init__(self, url, name, transcript):
        self.url, self.name, self.transcript = url, name, transcript
        self.http = httpx.Client(base_url=url, trust_env=False, timeout=60, headers={'Origin': url})
        self.csrf = None

    def close(self):
        self.http.close()

    def login(self, body: dict, *, expected=200):
        response = self.http.post(PREFIX + '/session', json=body)
        data = response.json()
        # 登录请求体（含登录码）与 CSRF 令牌不进记录。
        self.transcript.record({'actor': self.name, 'method': 'POST', 'path': PREFIX + '/session',
                                'request': '[withheld]', 'status': response.status_code, 'response': redact(data)})
        assert response.status_code == expected, (self.name, 'login', response.status_code, code(data))
        if response.status_code == 200:
            self.csrf = data['csrf']
        return response.status_code, data, response.headers

    def call(self, method, path, body=None, *, expected=200, csrf=KEEP, headers=None):
        send = dict(headers or {})
        token = self.csrf if csrf is KEEP else csrf
        if method != 'GET' and token is not None:
            send['X-CSRF-Token'] = token
        response = self.http.request(method, PREFIX + path, json=body, headers=send)
        data = response.json() if 'application/json' in response.headers.get('content-type', '') else {}
        self.transcript.record({'actor': self.name, 'method': method, 'path': PREFIX + path, 'request': body,
                                'status': response.status_code, 'response': redact(data)})
        if expected is not None:
            assert response.status_code == expected, (self.name, method, path, response.status_code, code(data))
        return response.status_code, data

    def get(self, path):
        return self.call('GET', path)[1]

    def post(self, path, body=None):
        return self.call('POST', path, {} if body is None else body)[1]


def offer(person, object_id, action_type):
    matches = [item for item in person.get(f'/governance/objects/{object_id}/actions')['items']
               if item['action_type'] == action_type]
    assert len(matches) == 1, (person.name, action_type)
    return matches[0]


def envelope(item, params, reason):
    """与浏览器 methodEnvelope 同形：动作、契约版本、目标一律取自服务端给本人的动作投影。"""
    target = item['target']
    return {'action_type': item['action_type'], 'contract_version': item['contract_version'],
            'expected_versions': [], 'idempotency_key': str(uuid.uuid4()), 'reason': reason, 'params': params,
            'target': target and {key: target[key] for key in ('object_id', 'revision_id', 'expected_version')}}


def method_task(person, object_id):
    """在本人的 Method 待办（/governance/method/tasks）里按游标找一个对象的事项。"""
    after = None
    while True:
        page = person.get('/governance/method/tasks' + (f'?after={after}' if after else ''))
        for item in page['items']:
            if item['object_id'] == object_id:
                return item
        after = page['next_after']
        if not after:
            return {'actions': []}


def task_view(item):
    return {'object_type': item.get('object_type'), 'phase': item.get('phase'),
            'contract_version': item.get('contract_version'),
            'action_contract_versions': sorted({a['contract_version'] for a in item['actions']}),
            'allowed_actions': sorted(a['action_type'] for a in item['actions'] if a['allowed'])}


# ------------------------------------------------------------------ API 进程


class Api:
    """测试专用启动器 acceptance/runtime/server.py：固定回环端口（重启后同一 URL），带挂起点控制。"""

    def __init__(self, h, private: Path, port: int):
        self.h, self.port, self.url = h, port, f'http://127.0.0.1:{port}'
        self.updates = workbench_env(private, port)
        self.process, self.starts = None, 0

    def start(self):
        ready = self.h.private / f'api-ready-{uuid.uuid4().hex}.json'
        self.process = self.h.spawn(f'pilot-api-{self.starts}', [
            sys.executable, '-I', str(ROOT / 'acceptance/runtime/server.py'), '--port', str(self.port),
            '--control-dir', str(self.h.control), '--key-file', str(self.h.key_file), '--ready-file', str(ready)],
            updates=self.updates)
        self.starts += 1

        def started():
            if ready.exists():
                return json.loads(ready.read_text())
            if self.process.poll() is not None:
                raise AssertionError('API exited before readiness; inspect the redacted process log')
        assert wait(started)['port'] == self.port

    def stop(self):
        self.h.stop(self.process)


# ------------------------------------------------------------------ 试点链


class Pilot:
    def __init__(self, h, f, api, private: Path, output: Path, book: Book):
        self.h, self.f, self.api, self.private, self.book = h, f, api, private, book
        self.flow = Flow(h, api.url, f)
        self.transcript = EvidenceLog(output / 'workbench')
        self.people: dict[str, Person] = {}
        self.commands: dict[str, list[str]] = {}   # 每人经工作台建立的命令，重启前后逐条核对
        self.state: dict = {}

    def close(self):
        for person in self.people.values():
            person.close()
        self.flow.close()

    def login_body(self, username):
        return json.loads((self.private / 'login' / f'{username}-login.json').read_text())

    def person(self, username):
        person = Person(self.api.url, username, self.transcript)
        person.login(self.login_body(username))
        if username in self.people:
            self.people[username].close()
        self.people[username] = person
        return person

    def prepare(self, person, body):
        prepared = person.post('/commands/prepare', body)
        if prepared['command_id'] not in self.commands.setdefault(person.name, []):
            self.commands[person.name].append(prepared['command_id'])
        return prepared

    def handle(self, person, object_id, action_type, params, reason):
        """浏览器同路：读本人的动作投影 → 按投影组信封 → prepare → commit。"""
        item = offer(person, object_id, action_type)
        assert item['allowed'], (person.name, action_type, item['reason'])
        body = envelope(item, params, reason)
        done = person.post(f"/commands/{self.prepare(person, body)['command_id']}/commit")
        assert done['status'] == 'committed', (person.name, action_type, done.get('error'))
        return body, done

    def obj(self, object_id):
        return self.flow.object(object_id)

    def core(self, path, actor='ceo'):
        return self.flow.clients[actor].json('GET', path)

    def receipts(self, object_id, action_type):
        items = self.core(f'/v1/objects/{object_id}/action-receipts?limit=100')['items']
        return len([item for item in items if item['action_type'] == action_type])

    def unchanged(self, action):
        """动作前后本 scope 的业务表快照是否逐行相同。"""
        before = self.h.snapshot(self.f)
        reply = action()
        return reply, self.h.snapshot(self.f) == before

    def provision_accounts(self):
        """复用 memory_service_app.governance_accounts.provision；人的 Runtime 凭证只进私有令牌文件。"""
        for username, actor in PEOPLE.items():
            token_file = self.private / 'tokens' / f'{username}.token'
            private_text(token_file, self.f['actors'][actor]['token'])
            identity = self.flow.clients[actor].json('GET', '/v1/identity')
            provision(self.private / 'accounts.json', username, token_file, identity,
                      self.private / 'login' / f'{username}-login.json')

    # -------------------------------------------------------- 1 登录

    def login_checks(self):
        ceo = Person(self.api.url, 'ceo', self.transcript)
        wrong = ceo.login({'username': 'ceo', 'code': secrets.token_urlsafe(32)}, expected=401)
        unknown = ceo.login({'username': 'nobody-' + uuid.uuid4().hex[:6], 'code': secrets.token_urlsafe(32)},
                            expected=401)
        ev = {'wrong_code': outcome(wrong), 'unknown_user': outcome(unknown),
              'cookie_set_on_failure': 'set-cookie' in wrong[2] or 'set-cookie' in unknown[2],
              'session_read_without_cookie': outcome(ceo.call('GET', '/session', expected=401))}
        self.book.check('login_wrong_code_and_unknown_user_are_401_login_failed',
                        ev['wrong_code'] == ev['unknown_user'] == [401, 'LOGIN_FAILED'] and not ev['cookie_set_on_failure']
                        and ev['session_read_without_cookie'] == [401, 'UNAUTHENTICATED'], ev)
        status, data, headers = ceo.login(self.login_body('ceo'))
        cookie = headers.get('set-cookie', '').lower()
        ev = {'status': status, 'body_keys': sorted(data), 'csrf_length': len(data['csrf']),
              'identity_is_the_human_ceo': data['identity']['principal_id'] == self.f['actors']['ceo']['principal_id']
              and data['identity']['principal_type'] == 'human',
              'cookie_flags': [flag for flag in ('httponly', 'samesite=strict', 'path=/dashboard/') if flag in cookie],
              'runtime_credential_in_body': self.f['actors']['ceo']['token'] in json.dumps(data),
              'session_read_returns_same_csrf': ceo.get('/session')['csrf'] == data['csrf']}
        self.people['ceo'] = ceo
        self.book.check('login_right_code_returns_session_cookie_and_csrf',
                        ev['body_keys'] == ['csrf', 'identity'] and ev['csrf_length'] >= 32 and ev['identity_is_the_human_ceo']
                        and len(ev['cookie_flags']) == 3 and not ev['runtime_credential_in_body']
                        and ev['session_read_returns_same_csrf'], ev)
        for username in ('dri-a', 'dri-b', 'owner-a'):
            self.person(username)

    # -------------------------------------------------------- M1A：Agent 起草，人经工作台确认

    def m1a(self):
        flow, ceo = self.flow, self.people['ceo']
        evidence = flow.upload()                       # 工作台没有上传能力：CEO 凭证经 API 上传合成证据
        issue = flow.create_issue(flow.open_run(), evidence)
        options = offer(ceo, issue['object_id'], 'm1a_set_participants')['options']['participants']

        def pick(name):                                # 只从服务端给出的候选里选，个人 Agent 必须与夹具一致
            signer = flow.signer(name)
            row = next(row for row in options if (row['principal_id'], row['assignment_id'], row['personal_agent_id'])
                       == (signer['principal_id'], signer['assignment_id'], signer['personal_agent_id']))
            return {**{key: row[key] for key in ('principal_id', 'assignment_id', 'personal_agent_id')},
                    'research': name == 'dri_a'}
        steps = [('ceo', *self.handle(ceo, issue['object_id'], 'm1a_set_participants',
                                      {'participants': [pick(n) for n in ('ceo', 'dri_a', 'owner_a')]},
                                      'CEO 指定本议题的必要参与人'))]
        agreement = flow.draft_agreement(issue)
        for username in ('ceo', 'dri-a', 'owner-a'):
            steps.append((username, *self.handle(self.people[username], agreement['object_id'], 'm1a_confirm_agreement',
                                                 {'statement': f'{username} 本人确认这一确切的 Agreement 版本。'},
                                                 '本人确认 Agreement')))
        proposal = flow.propose_update(issue, flow.ref(agreement['object_id'], effective=True),
                                       _change(flow, rationale='Initial pair: no retained object exists yet.'))
        flow.review_update(proposal)
        steps.append(('ceo', *self.handle(ceo, proposal['object_id'], 'm1a_confirm_update',
                                          {'statement': 'CEO 本人最终确认这一原子正式更新。'}, 'CEO 最终确认正式更新')))
        strategy_ref, architecture_ref = steps[-1][2]['receipt']['result']['changed_refs']
        ev = {'steps': [[who, body['action_type'], body['contract_version'], done['status']] for who, body, done in steps],
              'strategy_effective': self.obj(strategy_ref['object_id'])['effective_revision_id'] == strategy_ref['revision_id']}
        self.book.check('m1a_human_steps_via_workbench_under_0_5',
                        ev['strategy_effective'] and all(s[2:] == [V05, 'committed'] for s in ev['steps']), ev)
        self.state.update(evidence=evidence, issue=issue, strategy=strategy_ref, architecture=architecture_ref)
        return evidence, strategy_ref, architecture_ref

    # -------------------------------------------------------- LTCO + CSRF

    def ltcos(self, strategy_ref, architecture_ref, ltco_period):
        flow, ceo = self.flow, self.people['ceo']
        refs = [flow.propose_ltco(strategy_ref, architecture_ref, scope, ltco_period) for scope in ('scope-a', 'scope-b')]
        ev = {'todos': [task_view(method_task(ceo, ref['object_id'])) for ref in refs]}
        self.book.check('ceo_method_todos_list_the_0_5_ltco_drafts', all(
            t == {'object_type': 'LTCO', 'phase': 'draft', 'contract_version': V05, 'action_contract_versions': [V05],
                  'allowed_actions': ['m1b_confirm_ltco']} for t in ev['todos']), ev)

        # CSRF：同一条已准备的命令，缺令牌 / 错令牌都在会话层 403 CSRF_TOKEN_INVALID，业务表不变、
        # 会话仍有效；页面按 R03 重读 GET /session 的令牌后再提交成功。
        params = {'conclusion': 'established', 'statement': 'CEO 本人确认这一确切的 LTCO 责任。'}
        prepared = self.prepare(ceo, envelope(offer(ceo, refs[0]['object_id'], 'm1b_confirm_ltco'), params,
                                              'CEO 确认 LTCO 正式版本'))
        path = f"/commands/{prepared['command_id']}/commit"
        missing, same_missing = self.unchanged(lambda: ceo.call('POST', path, {}, csrf=None, expected=403))
        wrong, same_wrong = self.unchanged(lambda: ceo.call('POST', path, {}, csrf=secrets.token_urlsafe(32), expected=403))
        session = ceo.call('GET', '/session', expected=None)
        ev = {'missing_token': outcome(missing), 'wrong_token': outcome(wrong),
              'business_tables_unchanged': same_missing and same_wrong, 'session_after': session[0],
              'command_status_after': ceo.get(f"/commands/{prepared['command_id']}")['status'],
              'ltco_phase_after': self.obj(refs[0]['object_id'])['method_state']['phase']}
        self.book.check('csrf_missing_or_wrong_token_is_403_csrf_token_invalid',
                        ev['missing_token'] == ev['wrong_token'] == [403, 'CSRF_TOKEN_INVALID']
                        and ev['business_tables_unchanged'] and ev['session_after'] == 200
                        and ev['command_status_after'] == 'prepared' and ev['ltco_phase_after'] == 'draft', ev)
        ceo.csrf = session[1]['csrf']
        first = ceo.post(path)
        ev = {'status': first['status'], 'receipt_action_type': first['receipt']['action_type'],
              'ltco_phase_after': self.obj(refs[0]['object_id'])['method_state']['phase']}
        self.book.check('fresh_csrf_from_get_session_then_commit_succeeds',
                        ev == {'status': 'committed', 'receipt_action_type': 'm1b_confirm_ltco', 'ltco_phase_after': 'confirmed'}, ev)

        _body, second = self.handle(ceo, refs[1]['object_id'], 'm1b_confirm_ltco', params, 'CEO 确认 LTCO 正式版本')
        ev = {'ltcos': []}
        for ref, done in zip(refs, (first, second)):
            obj = self.obj(ref['object_id'])
            ev['ltcos'].append([obj['method_state']['phase'], obj['method_state']['last_review']['conclusion'],
                                done['receipt']['result']['conclusion'], obj['protocol']['interpretation_status'],
                                obj['effective_revision_id'] == ref['revision_id']])
        self.book.check('ceo_confirms_0_5_ltcos_via_workbench', all(
            item == ['confirmed', 'established', 'established', 'method_v0_5', True] for item in ev['ltcos']), ev)
        effective = [flow.ref(ref['object_id'], effective=True) for ref in refs]
        self.state.update(ltco_a=effective[0], ltco_b=effective[1])
        return effective

    # -------------------------------------------------------- 0.5 复核窗口

    def window(self, evidence, ltco_a, ltco_b, pco_period, mission_period):
        flow, ceo, dri_a = self.flow, self.people['ceo'], self.people['dri-a']
        pco_a, pco_b = (flow.draft_pco(ltco, scope, pco_period) for ltco, scope in ((ltco_a, 'scope-a'), (ltco_b, 'scope-b')))
        mission_a = flow.draft_mission(pco_a, 'owner_a', 'scope-a', mission_period, evidence=[evidence])
        mission_b = flow.draft_mission(pco_b, 'owner_b', 'scope-b', mission_period)
        window = flow.open_window([pco_a, pco_b], [mission_a, mission_b], [ltco_a, ltco_b], pco_period,
                                  names=('dri_a', 'dri_b', 'owner_a', 'owner_b'), title='Synthetic 0.5 pilot review window')
        wid = window['object_id']

        todo = next((item for item in ceo.get('/governance/tasks')['items'] if item['object_id'] == wid), {'actions': []})
        ev = {'label': todo.get('label'), **task_view(todo)}
        self.book.check('ceo_todos_list_the_0_5_review_window',
                        ev['label'] == '0.5 人工确认事项' and ev['contract_version'] == V05 and ev['phase'] == 'open'
                        and ev['action_contract_versions'] == [V05], ev)

        views = {name: self.people[name].get(f'/governance/review-windows/{wid}') for name in ('ceo', 'dri-a')}
        ev = {name: {'monthly': view['monthly'], 'scenes': len(view['scenes']), 'can_create_scene': view['can_create_scene'],
                     'contract_version': view['object']['protocol']['contract_version'],
                     'interpretation_status': view['object']['protocol']['interpretation_status'],
                     'profile_id': (view['object']['protocol']['method_profile_ref'] or {}).get('profile_id'),
                     'action_contract_versions': sorted({a['contract_version'] for a in view['actions']}),
                     'allowed_actions': sorted(a['action_type'] for a in view['actions'] if a['allowed']),
                     'mentions_0_3': 'tkos.method/0.3' in json.dumps(view)} for name, view in views.items()}
        self.book.check('review_window_view_is_method_0_5_bound', all(
            (v['monthly'], v['scenes'], v['can_create_scene'], v['contract_version'], v['interpretation_status'],
             v['action_contract_versions'], v['mentions_0_3']) == (None, 0, False, V05, 'method_v0_5', [V05], False)
            for v in ev.values()) and 'm1b_comment' in ev['dri-a']['allowed_actions'], ev)

        # 参与人办理窗口：确切目标从本人的动作投影里选（浏览器不收手工 id / hash）。
        target = next(option['ref'] for option in offer(dri_a, wid, 'm1b_comment')['options']['targets']
                      if option['ref']['object_id'] == pco_a['object_id'])
        body, done = self.handle(dri_a, wid, 'm1b_comment',
                                 {'target_ref': target, 'content': '请在 PCO 中写明本期证据的来源与核验标准。'},
                                 '本人提交共同核对意见')
        opinion = done['receipt']['result']['review_record_id']
        records = [r for r in self.core(f'/v1/method/objects/{wid}/reviews')['items'] if r['record_id'] == opinion]
        ev = {'contract_version': body['contract_version'], 'status': done['status'],
              'records': [[r['kind'], str(r['principal_id']) == self.f['actors']['dri_a']['principal_id']] for r in records]}
        self.book.check('participant_handles_the_0_5_window_via_workbench',
                        ev == {'contract_version': V05, 'status': 'committed', 'records': [['window_comment', True]]}, ev)

        flow.close_window(window)
        params = {'title': 'Synthetic 0.5 pilot candidate set', 'pcos': [], 'missions': [],
                  'dispositions': [{'review_record_id': opinion, 'decision': 'adopted',
                                    'rationale': 'Adopted: evidence source and criteria are now explicit.'}],
                  'unresolved_differences': [], 'summary': 'Retain both PCO/Mission results for the period.'}
        for ref in (pco_a, pco_b):
            params['pcos'].append({'object_id': ref['object_id'],
                                   'payload': deepcopy(self.obj(ref['object_id'])['latest_revision']['payload'])})
        for ref in (mission_a, mission_b):
            payload = deepcopy(self.obj(ref['object_id'])['latest_revision']['payload'])
            payload.pop('parent_pco_ref')
            params['missions'].append({'object_id': ref['object_id'], **payload})
        candidate = flow.ref(flow.resolve_window(window, params)['object_id'])
        self.state.update(window=window, candidate=candidate, pco_a=pco_a, pco_b=pco_b,
                          mission_a=mission_a, mission_b=mission_b)
        return window, candidate, (pco_a, pco_b, mission_a, mission_b)

    # -------------------------------------------------------- 承诺、业务拒绝、正式人工门、幂等

    def candidate(self, window, candidate, members):
        flow, ceo, dri_a = self.flow, self.people['ceo'], self.people['dri-a']
        cid = candidate['object_id']
        by_object = {ref['object_id']: ref for ref in candidate_refs(self.h, self.f, candidate)}
        pco_a, pco_b, mission_a, mission_b = members
        ev = {'commitments': []}
        for username, pco in (('dri-a', pco_a), ('dri-b', pco_b)):
            options = offer(self.people[username], cid, 'm1b_commit_candidate')['options']['responsibilities']
            body, done = self.handle(self.people[username], cid, 'm1b_commit_candidate',
                                     {'responsibility_ref': options[0]['ref'],
                                      'statement': f'{username} 本人对这一确切候选中的本域责任作出承诺。'}, '本人提交责任承诺')
            ev['commitments'].append([username, [o['ref']['object_id'] for o in options] == [pco['object_id']],
                                      body['contract_version'], done['status']])
        rows = self.h.sql(self.f, 'SELECT principal_id FROM gov_method_commitments WHERE scope_id=%s AND candidate_revision_id=%s',
                          (self.f['scope_id'], candidate['revision_id']))
        ev['committed_by_the_two_dris'] = (sorted(str(r['principal_id']) for r in rows)
                                           == sorted(self.f['actors'][a]['principal_id'] for a in ('dri_a', 'dri_b')))
        self.book.check('scope_dris_commit_the_candidate_set_via_workbench', ev['committed_by_the_two_dris'] and all(
            c[1:] == [True, V05, 'committed'] for c in ev['commitments']), ev)

        # 4：DRI 没有 CEO 任职；按浏览器同形组出激活信封，核心在 prepare 拒绝，业务表不变、会话仍有效。
        denied = offer(dri_a, cid, 'm1b_activate_candidates')
        body = envelope(denied, {'statement': 'A DRI tries to activate the whole set.', 'notes': []}, 'DRI 试图整组激活候选集合')
        reply, same = self.unchanged(lambda: dri_a.call('POST', '/commands/prepare', body, expected=None))
        ev = {'offer': [denied['allowed'], denied['reason']], 'prepare': outcome(reply), 'business_tables_unchanged': same,
              'session_after': dri_a.call('GET', '/session', expected=None)[0],
              'journal_entries': len([c for c in dri_a.get('/commands')['items']
                                      if c['envelope'].get('action_type') == 'm1b_activate_candidates'])}
        self.book.check('non_ceo_activation_is_403_forbidden_and_session_survives',
                        ev == {'offer': [False, 'current_role_not_permitted'], 'prepare': [403, 'FORBIDDEN'],
                               'business_tables_unchanged': True, 'session_after': 200, 'journal_entries': 0}, ev)

        # 3：CEO 的正式人工门——整组激活。命令按投影发出 0.5；回执正式；状态按 0.5 契约变化（经 API 核对）。
        todo = task_view(method_task(ceo, cid))
        item = offer(ceo, cid, 'm1b_activate_candidates')
        before = self.obj(cid)
        body = envelope(item, {'statement': 'CEO 本人整组激活这一确切的候选集合。', 'notes': ['非阻断的集体备注随集合一起记录。']},
                        'CEO 整组激活候选集合')
        prepared = self.prepare(ceo, body)
        done = ceo.post(f"/commands/{prepared['command_id']}/commit")
        receipt, after = done['receipt'], self.obj(cid)
        members_after = {oid: self.obj(oid) for oid in by_object}
        ev = {'todo': todo, 'sent_contract_version': body['contract_version'],
              'stored_contract_version': ceo.get(f"/commands/{prepared['command_id']}")['envelope']['contract_version'],
              'preview_members': len(prepared['preview']['members']), 'status': done['status'],
              'receipt': [receipt['receipt_id'], receipt['status'], receipt['action_type']],
              'execution_authority_created': receipt['result']['execution_authority_created'],
              'core_receipt_read': self.core(f"/v1/action-receipts/{receipt['receipt_id']}")['receipt']['receipt_id']
              == receipt['receipt_id'],
              'candidate_phase': [before['method_state']['phase'], after['method_state']['phase']],
              'candidate_version': [before['object_version'], after['object_version']],
              'candidate_effective': after['effective_revision_id'] == candidate['revision_id'],
              'members_confirmed_at_candidate_revisions': all(
                  (members_after[oid]['effective_revision_id'], members_after[oid]['method_state']['phase'])
                  == (ref['revision_id'], 'confirmed') for oid, ref in by_object.items()),
              'missions_carry_owner_activation_record': all(
                  members_after[m['object_id']]['method_state'].get('owner_activation_record_id')
                  == receipt['result']['review_record_id'] for m in (mission_a, mission_b)),
              'window_phase': self.obj(window['object_id'])['method_state']['phase'],
              'execution_side_effects': len(flow.rows('gov_execution_authorities')) + len(flow.rows('gov_work_receipts')),
              # 观察项，不计入判定：已办结的 0.5 窗口是否仍留在 CEO 的「我的待办」（0.3 窗口按阶段过滤）。
              'observation_handled_window_still_in_ceo_todos': [
                  [t['phase'], len(t['actions'])] for t in ceo.get('/governance/tasks')['items']
                  if t['object_id'] == window['object_id']]}
        self.book.check('ceo_activates_the_0_5_candidate_set_via_workbench',
                        todo['contract_version'] == V05 and 'm1b_activate_candidates' in todo['allowed_actions']
                        and ev['sent_contract_version'] == ev['stored_contract_version'] == V05 and ev['preview_members'] == 4
                        and ev['status'] == 'committed' and ev['receipt'][1:] == ['committed', 'm1b_activate_candidates']
                        and ev['execution_authority_created'] is False and ev['core_receipt_read']
                        and ev['candidate_phase'] == ['pending', 'confirmed']
                        and ev['candidate_version'][1] == ev['candidate_version'][0] + 1 and ev['candidate_effective']
                        and ev['members_confirmed_at_candidate_revisions'] and ev['missions_carry_owner_activation_record']
                        and ev['window_phase'] == 'confirmed' and ev['execution_side_effects'] == 0, ev)

        # 6：同一命令再提交、重试、以同一幂等键再准备，都回到同一回执；不新增修订与回执。
        def footprint():
            return [self.obj(cid)['object_version'], len(self.core(f'/v1/objects/{cid}/revisions?limit=100')['items']),
                    self.receipts(cid, 'm1b_activate_candidates')]
        start = footprint()
        again = ceo.post(f"/commands/{prepared['command_id']}/commit")
        retry = ceo.post(f"/commands/{prepared['command_id']}/retry")
        reprepared = ceo.post('/commands/prepare', body)
        ev = {'receipt_ids_equal': len({receipt['receipt_id'], again['receipt']['receipt_id'], retry['receipt']['receipt_id'],
                                        reprepared['receipt']['receipt_id']}) == 1,
              'same_command_on_reprepare': reprepared['command_id'] == prepared['command_id'],
              'statuses': [again['status'], retry['status'], reprepared['status']],
              'version_revisions_receipts': [start, footprint()],
              'receipt_rows_for_key': self.h.sql(self.f, 'SELECT count(*) AS n FROM gov_action_receipts '
                                                 'WHERE scope_id=%s AND idempotency_key=%s',
                                                 (self.f['scope_id'], body['idempotency_key']))[0]['n']}
        self.book.check('recommit_retry_and_reprepare_return_the_same_receipt',
                        ev['receipt_ids_equal'] and ev['same_command_on_reprepare'] and ev['statuses'] == ['committed'] * 3
                        and ev['version_revisions_receipts'][0] == ev['version_revisions_receipts'][1]
                        and start[2] == 1 and ev['receipt_rows_for_key'] == 1, ev)
        # 客户端丢了结果、用本人凭证直接重放工作台保存的原始信封：核心按幂等键回放原回执。
        replay = flow.clients['ceo'].json('POST', '/v1/actions', prepared['envelope'])
        ev = {'same_receipt': replay['receipt_id'] == receipt['receipt_id'], 'footprint_unchanged': footprint() == start}
        self.book.check('core_replay_of_the_original_envelope_returns_the_same_receipt', all(ev.values()), ev)
        self.state.update(activation_command=prepared['command_id'], activation_receipt=receipt['receipt_id'])
        return by_object, prepared['command_id'], receipt['receipt_id']

    # -------------------------------------------------------- 复盘确认 + 真实的响应丢失

    def review(self, evidence, by_object, members):
        flow, ceo = self.flow, self.people['ceo']
        pco_a, _pco_b, mission_a, _mission_b = members
        pco_state = flow.propose_state(by_object[pco_a['object_id']], state_period=period(-30, 0))
        mission_state = flow.propose_state(by_object[mission_a['object_id']], state_period=period(-30, 0), rag='green',
                                           summary='Evidence supports progress', evidence=[evidence], data_gaps=())
        generated = flow.act('co_agent', 'm1b_generate_review', {'domain_id': self.f['domains']['company'], 'payload': {
            'review_id': 'synthetic-0-5-pilot-review', 'title': 'Synthetic 0.5 pilot period review', 'period': period(-30, 0),
            'target_refs': [by_object[pco_a['object_id']], by_object[mission_a['object_id']]],
            'state_refs': [pco_state, mission_state], 'fact_refs': [],
            'findings': ['Canonical states are the reviewed basis.'], 'learnings': [], 'implications': [],
            'generation_version': 'controlled-review-1'}})['result']
        rid = generated['object_id']
        ev = task_view(method_task(ceo, rid))
        self.book.check('ceo_method_todos_list_the_0_5_period_review', ev == {
            'object_type': 'PeriodReview', 'phase': 'generated', 'contract_version': V05,
            'action_contract_versions': [V05], 'allowed_actions': ['m1b_confirm_review']}, ev)

        # 提交在业务提交前挂起，客户端读超时断开（结果丢失）；放行后本人从「我的提交」拿回同一回执。
        body = envelope(offer(ceo, rid, 'm1b_confirm_review'),
                        {'statement': 'CEO 本人确认这一确切的周期复盘。', 'findings': ['CEO 调整后的发现。']}, 'CEO 确认周期复盘')
        cmd = self.prepare(ceo, body)['command_id']
        token = uuid.uuid4().hex
        headers = {**self.h.headers('before_business_commit', mode='barrier', token=token), 'X-CSRF-Token': ceo.csrf}
        lost = {}

        def send():
            with httpx.Client(base_url=self.api.url, trust_env=False, cookies=ceo.http.cookies,
                              headers={'Origin': self.api.url}) as client:
                try:
                    lost['status'] = client.post(f'{PREFIX}/commands/{cmd}/commit', json={}, headers=headers,
                                                 timeout=httpx.Timeout(30, read=4)).status_code
                except httpx.ReadTimeout:
                    lost['read_timeout'] = True
        thread = threading.Thread(target=send)
        thread.start()
        wait(lambda: (self.h.control / f'{token}.reached.json').exists())
        # 挂起期间 scope 栅栏被占住，HTTP 读也要等；此处只读私有命令日志文件的状态字段。
        in_flight = json.loads((self.private / 'commands' / f'{cmd}.json').read_text())['status']
        thread.join(30)
        (self.h.control / f'{token}.release').write_text('')

        def settled():
            data = ceo.get(f'/commands/{cmd}')
            return data if data['status'] == 'committed' else None
        recovered = wait(settled)
        receipt_id = recovered['receipt']['receipt_id']
        ev = {'client_read_timeout': lost.get('read_timeout', False), 'journal_status_in_flight': in_flight,
              'recovered_status': recovered['status'],
              'retry_and_recommit_same_receipt': ceo.post(f'/commands/{cmd}/retry')['receipt']['receipt_id']
              == ceo.post(f'/commands/{cmd}/commit')['receipt']['receipt_id'] == receipt_id,
              'review_confirmations': len([c for c in flow.confirmations(rid)['items'] if c['kind'] == 'review_confirmation']),
              'confirm_review_receipts_on_object': self.receipts(rid, 'm1b_confirm_review'),
              'listed_in_my_submissions': [c['status'] for c in ceo.get('/commands')['items'] if c['command_id'] == cmd]}
        self.book.check('lost_commit_response_recovered_from_my_submissions', ev == {
            'client_read_timeout': True, 'journal_status_in_flight': 'unknown', 'recovered_status': 'committed',
            'retry_and_recommit_same_receipt': True, 'review_confirmations': 1, 'confirm_review_receipts_on_object': 1,
            'listed_in_my_submissions': ['committed']}, ev)
        obj = self.obj(rid)
        ev = {'contract_version': body['contract_version'], 'phase': obj['method_state']['phase'],
              'effective_is_confirmed_revision': obj['effective_revision_id'] == recovered['receipt']['result']['revision_id'],
              'findings': obj['latest_revision']['payload']['findings'],
              'agent_generation_kept': obj['method_state']['agent_generation_ref']['revision_id'] == generated['revision_id']}
        self.book.check('ceo_confirms_the_0_5_period_review_via_workbench', ev == {
            'contract_version': V05, 'phase': 'confirmed', 'effective_is_confirmed_revision': True,
            'findings': ['CEO 调整后的发现。'], 'agent_generation_kept': True}, ev)
        self.state.update(review=generated, review_command=cmd)

    # -------------------------------------------------------- 7 重启恢复

    def restart(self, command, receipt_id, candidate):
        ceo, cid = self.people['ceo'], candidate['object_id']

        def mine(person):
            return sorted([c['command_id'], c['status'], (c.get('receipt') or {}).get('receipt_id')]
                          for c in person.get('/commands')['items'])
        before, before_obj, old_pid = mine(ceo), self.obj(cid), self.api.process.pid
        self.api.stop()
        self.api.start()                               # 同一端口、同一账号文件与命令日志的新进程
        old = [outcome(ceo.call('GET', '/session', expected=None)),
               outcome(ceo.call('POST', f'/commands/{command}/commit', {}, expected=None))]
        fresh = self.person('ceo')
        after, after_obj = mine(fresh), self.obj(cid)
        ev = {'new_process_same_port': [self.api.process.pid != old_pid, self.api.port],
              'my_submissions_before_after': [len(before), len(after)], 'identical': before == after,
              'exactly_the_commands_the_ceo_submitted': [c[0] for c in after] == sorted(self.commands['ceo']),
              'all_committed_with_receipt': all(c[1] == 'committed' and c[2] for c in after),
              'activation_receipt_kept': fresh.get(f'/commands/{command}')['receipt']['receipt_id'] == receipt_id,
              'core_receipt_read': self.core(f'/v1/action-receipts/{receipt_id}')['receipt']['action_type'],
              'candidate_version_phase': [[before_obj['object_version'], before_obj['method_state']['phase']],
                                          [after_obj['object_version'], after_obj['method_state']['phase']]]}
        self.book.check('restart_keeps_receipts_state_and_my_submissions',
                        ev['new_process_same_port'][0] and ev['identical'] and ev['exactly_the_commands_the_ceo_submitted']
                        and ev['all_committed_with_receipt'] and ev['activation_receipt_kept']
                        and ev['core_receipt_read'] == 'm1b_activate_candidates'
                        and ev['candidate_version_phase'][0] == ev['candidate_version_phase'][1], ev)
        ev = {'old_cookie_session_read': old[0], 'old_cookie_commit': old[1],
              'note': '已知限制：会话在进程内存，重启后需重新登录'}
        self.book.check('restart_drops_in_memory_sessions_old_cookie_401', old == [[401, 'UNAUTHENTICATED']] * 2, ev)
        replay = fresh.post(f'/commands/{command}/commit')
        ev = {'login': 200, 'replay': [replay['status'], replay['receipt']['receipt_id'] == receipt_id],
              'object_version_unchanged': self.obj(cid)['object_version'] == before_obj['object_version']}
        self.book.check('fresh_login_after_restart_and_replay_returns_the_same_receipt',
                        ev['replay'] == ['committed', True] and ev['object_version_unchanged'], ev)
        self.person('dri-a')

    # -------------------------------------------------------- 其余边界

    def boundaries(self, window, command):
        ceo, dri_a = self.people['ceo'], self.people['dri-a']
        head = self.obj(window['object_id'])
        target = {'object_id': window['object_id'], 'revision_id': head['latest_revision_id'],
                  'expected_version': head['object_version']}

        def body(action_type, version, params, reason, target=target):
            return {'action_type': action_type, 'contract_version': version, 'expected_versions': [],
                    'idempotency_key': str(uuid.uuid4()), 'reason': reason, 'target': target, 'params': params}
        ev = {'agent_only_m1b_close_window': outcome(ceo.call('POST', '/commands/prepare', body(
                  'm1b_close_window', V05, {'reason': 'A human tries an Agent-only action.'}, '人冒充 Agent 关窗'), expected=None)),
              'api_only_m1b_record_constraint': outcome(ceo.call('POST', '/commands/prepare', body(
                  'm1b_record_constraint', V05, {'domain_id': self.f['domains']['company'], 'payload': {}},
                  '浏览器登记 Constraint', target=None), expected=None))}
        self.book.check('session_facade_rejects_agent_and_api_only_actions',
                        list(ev.values()) == [[403, 'FORBIDDEN']] * 2, ev)

        # R02 之前旧窗口页写死 tkos.method/0.3：同形的参与人意见打到 0.5 窗口，核心按绑定拒绝，不按 0.3 语义执行。
        stale = body('m1b_comment', 'tkos.method/0.3', {'target_ref': self.state['pco_a'],
                                                        'content': 'Old page: hard-coded tkos.method/0.3 comment.'},
                     '按 0.3 写死的旧窗口页信封')
        reply, same = self.unchanged(lambda: dri_a.call('POST', '/commands/prepare', stale, expected=None))
        ev = {'prepare': outcome(reply), 'business_tables_unchanged': same}
        self.book.check('hardcoded_0_3_command_on_a_0_5_object_is_rejected',
                        ev == {'prepare': [409, 'PROTOCOL_BINDING_CONFLICT'], 'business_tables_unchanged': True}, ev)

        ev = {'read': outcome(dri_a.call('GET', f'/commands/{command}', expected=None)),
              'commit': outcome(dri_a.call('POST', f'/commands/{command}/commit', {}, expected=None)),
              'listed': command in [c['command_id'] for c in dri_a.get('/commands')['items']]}
        self.book.check('private_command_hidden_from_another_person',
                        ev == {'read': [404, 'NOT_FOUND'], 'commit': [404, 'NOT_FOUND'], 'listed': False}, ev)

        ev = {'prepare_with_foreign_origin': outcome(ceo.call('POST', '/commands/prepare', stale, expected=None,
                                                              headers={'Origin': 'https://invalid.example'}))}
        self.book.check('cross_origin_write_is_rejected', ev == {'prepare_with_foreign_origin': [403, 'FORBIDDEN']}, ev)

        ev = {'logout': dri_a.call('DELETE', '/session', expected=None)[0],
              'read_after_logout': outcome(dri_a.call('GET', '/governance/tasks', expected=None))}
        self.book.check('logout_invalidates_the_session',
                        ev == {'logout': 200, 'read_after_logout': [401, 'UNAUTHENTICATED']}, ev)
        self.person('dri-a')

    # -------------------------------------------------------- 0.5 独有的人工门：Constraint

    def constraints(self, architecture_ref, ltco_period, pco_period):
        flow, ceo, dri_a = self.flow, self.people['ceo'], self.people['dri-a']
        company = flow.record_constraint({'kind': 'company'}, effective=ltco_period, title='Synthetic company cash constraint')
        scope = flow.record_constraint({'kind': 'scope', 'scope_id': 'scope-a'}, architecture_ref=architecture_ref,
                                       effective=pco_period)
        todo = task_view(method_task(ceo, company['object_id']))
        body, done = self.handle(ceo, company['object_id'], 'm1b_confirm_constraint',
                                 {'statement': 'CEO 本人确认这一公司级约束。'}, 'CEO 确认公司级约束')
        obj = self.obj(company['object_id'])
        ev = {'todo': todo, 'sent': [body['contract_version'], done['status']], 'phase': obj['method_state']['phase'],
              'effective_is_recorded_revision': obj['effective_revision_id'] == company['revision_id']}
        self.book.check('ceo_confirms_the_company_constraint_via_workbench',
                        todo['object_type'] == 'Constraint' and todo['contract_version'] == V05
                        and todo['allowed_actions'] == ['m1b_confirm_constraint'] and ev['sent'] == [V05, 'committed']
                        and ev['phase'] == 'confirmed' and ev['effective_is_recorded_revision'], ev)

        denied = offer(ceo, scope['object_id'], 'm1b_confirm_constraint')
        reply, same = self.unchanged(lambda: ceo.call('POST', '/commands/prepare', envelope(
            denied, {'statement': 'The CEO is not the scope DRI.'}, 'CEO 试图确认范围约束'), expected=None))
        ev = {'offer': [denied['allowed'], denied['reason']], 'prepare': outcome(reply), 'business_tables_unchanged': same,
              'session_after': ceo.call('GET', '/session', expected=None)[0]}
        self.book.check('ceo_cannot_confirm_a_scope_constraint_403', ev == {
            'offer': [False, 'not_responsible_owner'], 'prepare': [403, 'FORBIDDEN'],
            'business_tables_unchanged': True, 'session_after': 200}, ev)

        # 范围 DRI 经工作台确认本域约束；逐项取证，结果不符合预期时留下足以定位的证据。
        item = offer(dri_a, scope['object_id'], 'm1b_confirm_constraint')
        body = envelope(item, {'statement': 'dri-a 本人确认本域约束。'}, '范围 DRI 确认本域约束')
        prepared = dri_a.call('POST', '/commands/prepare', body, expected=None)
        ev = {'offer_allowed': item['allowed'], 'contract_version': body['contract_version'], 'prepare': outcome(prepared)}
        if prepared[0] == 200:
            cmd = prepared[1]['command_id']
            commit = dri_a.call('POST', f'/commands/{cmd}/commit', {}, expected=None)
            obj = self.obj(scope['object_id'])
            journal = json.loads((self.private / 'commands' / f'{cmd}.json').read_text())
            ev.update({'commit': [commit[0], code(commit[1]) or commit[1].get('status')],
                       'constraint_after_ceo_read': [obj['method_state']['phase'],
                                                     obj['effective_revision_id'] == scope['revision_id']],
                       'journal_status_on_disk': journal['status'],
                       'listed_in_my_submissions': [c['status'] for c in dri_a.get('/commands')['items']
                                                    if c['command_id'] == cmd]})
            receipt_id = (journal.get('receipt') or {}).get('receipt_id')
            if receipt_id:
                read = flow.clients['dri_a'].request('GET', f'/v1/action-receipts/{receipt_id}', expected={200, 403, 404})
                replay = flow.clients['dri_a'].request('POST', '/v1/actions', journal['envelope'], expected={200, 403, 404, 409})
                ev.update({'core_receipt_read_by_the_dri': [read.status_code, code(read.json())],
                           'core_replay_by_the_dri': [replay.status_code, code(replay.json())],
                           'core_receipt_read_by_the_ceo': flow.clients['ceo'].request(
                               'GET', f'/v1/action-receipts/{receipt_id}', expected={200, 403, 404}).status_code})
        self.book.check('scope_dri_confirms_the_scope_constraint_via_workbench',
                        ev['offer_allowed'] and ev['contract_version'] == V05 and ev.get('commit') == [200, 'committed']
                        and ev.get('constraint_after_ceo_read') == ['confirmed', True]
                        and ev.get('listed_in_my_submissions') == ['committed']
                        and ev.get('core_receipt_read_by_the_dri', [None])[0] == 200
                        and ev.get('core_replay_by_the_dri', [None])[0] == 200, ev)


# ------------------------------------------------------------------ 入口


def run(args):
    private, output = args.private.resolve(), args.output.resolve()
    if private.exists() or output.exists():
        raise SystemExit('run 需要全新的 --private 与 --output 路径')
    private.mkdir(parents=True, mode=0o700)
    initial = source_manifest(ROOT / 'src')
    book = Book(output / 'summary.json', {'started_at': now(), 'source': {
        'commit': git('rev-parse', 'HEAD').strip(), 'branch': git('rev-parse', '--abbrev-ref', 'HEAD').strip(),
        'dirty_src_paths': sorted(line[3:] for line in git('status', '--porcelain', '--', 'src').splitlines()),
        'src_manifest_sha256': digest(initial),
        'tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}})
    h = pilot = error = None
    try:
        created = database.create(args.base_env.resolve(), private / 'db', output / 'db')
        upgraded = database.upgrade(private / 'db' / 'env.json', ROOT / 'src', output / 'db-upgrade')
        book.save(database={'name': created['database'], 'fresh': True, 'base_commit': created['base_commit'],
                            'migrations_after_baseline': len(upgraded['applied']),
                            'last_migration': upgraded['applied'][-1], 'repeat_applied': upgraded['repeat_applied']})
        h = MethodHarness(private / 'db' / 'env.json', output, private / 'harness')
        f = seed_v05(h.env, private / 'identities.json', 'runtime-acceptance-pilot-v05')
        register_v05(h, ROOT / 'src', f)
        book.save(scope_id=f['scope_id'], tenant_id=f['tenant_id'])
        api = Api(h, private, free_port())
        api.start()
        pilot = Pilot(h, f, api, private, output, book)
        pilot.provision_accounts()
        pilot.login_checks()
        evidence, strategy_ref, architecture_ref = pilot.m1a()
        ltco_period, pco_period, mission_period = period(-30, 335), period(-1, 30), period(0, 15)
        ltco_a, ltco_b = pilot.ltcos(strategy_ref, architecture_ref, ltco_period)
        window, candidate, members = pilot.window(evidence, ltco_a, ltco_b, pco_period, mission_period)
        by_object, command, receipt = pilot.candidate(window, candidate, members)
        pilot.review(evidence, by_object, members)
        pilot.restart(command, receipt, candidate)
        pilot.boundaries(window, command)
        pilot.constraints(architecture_ref, ltco_period, pco_period)
    except BaseException as exc:  # noqa: BLE001 - 记录后重新抛出
        error = exc
    finally:
        if pilot is not None:
            private_json(private / 'state.json', {'scope_id': pilot.f['scope_id'], 'objects': pilot.state})
            pilot.close()
        if h is not None:
            h.close()
        final = source_manifest(ROOT / 'src')
        if final != initial and error is None:
            error = RuntimeError('src changed during the acceptance run; rerun on a stable checkpoint')
        book.save(finished_at=now(), src_unchanged_during_run=final == initial,
                  run_error=None if error is None else f'{type(error).__name__}: {error}'[:300])
        if args.publish:
            public_json(args.publish.resolve(), json.loads((output / 'summary.json').read_text()))
    if error is not None:
        raise error


def serve(args):
    """对 run 留下的同一个库与 scope 起带工作台的 API（复用 acceptance/governance_workbench/serve.py）。"""
    private = args.private.resolve()
    env_file = private / 'db' / 'env.json'
    if not (env_file.is_file() and (private / 'accounts.json').is_file()):
        raise SystemExit('--private 必须是 run 留下的私有目录（含 db/env.json 与 accounts.json）')
    port = args.port or free_port()
    print(f'工作台：http://127.0.0.1:{port}/dashboard/', flush=True)
    for username in PEOPLE:
        print(f'{username} 的登录文件：{private / "login" / (username + "-login.json")}', flush=True)
    print(f'对象清单：{private / "state.json"}；登录码只在上述私有文件里，Ctrl-C 停止。', flush=True)
    os.chdir(ROOT)
    os.execv(sys.executable, [sys.executable, '-m', 'acceptance.governance_workbench.serve',
                              '--env-file', str(env_file), '--private', str(private), '--port', str(port)])


def agent_round(args):
    """浏览器复核的 Agent 一侧（对正在运行的 serve）：stage 新开一个 0.5 复核窗口，consolidate 关窗并收拢成候选集合。

    人的步骤（发表意见、责任承诺、整组激活）留给浏览器；这里不代人办理任何动作。
    """
    private = args.private.resolve()
    f = json.loads((private / 'identities.json').read_text())
    state = json.loads((private / 'state.json').read_text())
    objects = state['objects']
    rounds = objects.setdefault('browser_rounds', [])
    h = MethodHarness(private / 'db' / 'env.json', private / 'browser-round', private / 'browser-round')
    flow = Flow(h, args.url, f)
    try:
        if args.mode == 'stage':
            # 每一轮用下一段 30 天的期间，不与前几轮或 run 的对象冲突；PCO 引用 run 里 CEO 已确认的复盘。
            offset = 31 + 30 * len(rounds)
            pco_period, mission_period = period(offset, offset + 29), period(offset, offset + 14)
            review = flow.ref(objects['review']['object_id'], effective=True)
            pcos = [flow.draft_pco(objects[key], scope, pco_period, period_review_ref=review,
                                   title=f'Browser round {len(rounds) + 1} PCO {scope}')
                    for key, scope in (('ltco_a', 'scope-a'), ('ltco_b', 'scope-b'))]
            missions = [flow.draft_mission(pco, owner, scope, mission_period, title=f'Browser round {len(rounds) + 1} Mission {scope}')
                        for pco, owner, scope in zip(pcos, ('owner_a', 'owner_b'), ('scope-a', 'scope-b'))]
            window = flow.open_window(pcos, missions, [objects['ltco_a'], objects['ltco_b']], pco_period,
                                      names=('dri_a', 'dri_b', 'owner_a', 'owner_b'),
                                      title=f'Browser round {len(rounds) + 1} 0.5 review window')
            rounds.append({'window': window, 'pcos': pcos, 'missions': missions})
            print('已新开 0.5 复核窗口，参与人 dri-a、dri-b（及两位 Owner）可在「我的待办」办理：' + window['object_id'])
        else:
            current = rounds[-1]
            window = current['window']
            flow.close_window(window)
            opinions = [r['record_id'] for r in flow.clients['ceo'].json(
                'GET', f"/v1/method/objects/{window['object_id']}/reviews")['items']
                if r['kind'] == 'window_comment' and r.get('effective_opinion')]
            params = {'title': f'Browser round {len(rounds)} candidate set', 'pcos': [], 'missions': [],
                      'dispositions': [{'review_record_id': record, 'decision': 'adopted',
                                        'rationale': 'Adopted by the controlled Co-agent for the browser review.'}
                                       for record in opinions],
                      'unresolved_differences': [], 'summary': 'Retain both PCO/Mission results for the period.'}
            for ref in current['pcos']:
                params['pcos'].append({'object_id': ref['object_id'],
                                       'payload': deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])})
            for ref in current['missions']:
                payload = deepcopy(flow.object(ref['object_id'])['latest_revision']['payload'])
                payload.pop('parent_pco_ref')
                params['missions'].append({'object_id': ref['object_id'], **payload})
            current['candidate'] = flow.resolve_window(window, params)
            print(f'已关窗并收拢（{len(opinions)} 条意见已处置）；候选集合等待 dri-a、dri-b 承诺与 CEO 整组激活：'
                  + current['candidate']['object_id'])
        private_json(private / 'state.json', state)
    finally:
        flow.close()
        h.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='mode', required=True)
    run_parser = sub.add_parser('run', help='新建库与 scope，驱动整条试点链并记录检查')
    run_parser.add_argument('--base-env', type=Path, required=True, help='隔离验收栈的基础 env（.runtime-acceptance/env.json）')
    run_parser.add_argument('--private', type=Path, required=True)
    run_parser.add_argument('--output', type=Path, required=True)
    run_parser.add_argument('--publish', type=Path, help='另存一份脱敏 summary（如 docs/acceptance/pilot-workbench-v05-summary.json）')
    serve_parser = sub.add_parser('serve', help='对 run 留下的库与 scope 起工作台，供浏览器复核')
    serve_parser.add_argument('--private', type=Path, required=True)
    serve_parser.add_argument('--port', type=int, default=0)
    for mode, text in (('stage', '浏览器复核：Agent 新开一个 0.5 复核窗口'), ('consolidate', '浏览器复核：Agent 关窗并收拢成候选集合')):
        agent_parser = sub.add_parser(mode, help=text)
        agent_parser.add_argument('--private', type=Path, required=True)
        agent_parser.add_argument('--url', required=True, help='正在运行的 serve 地址，如 http://127.0.0.1:PORT')
    args = parser.parse_args()
    {'run': run, 'serve': serve}.get(args.mode, agent_round)(args)


if __name__ == '__main__':
    main()
