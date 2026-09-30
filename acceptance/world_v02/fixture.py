"""world 0.2 验收的登记：身份沿用 world 0.1 验收的播种（owner SQL），0.2 的 profile、策略、支持登记与
激活策略一律经真实控制面 CLI 安装；业务成功一律来自 HTTP。"""
from __future__ import annotations

import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.method_independent.fixture import uid
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json
from acceptance.world_v01.fixture import ROOT, WORLD_ROLES, owner_rows

CONTRACT = ROOT / 'docs/contracts/tkos-world-0.2.md'
REGISTRY = ROOT / 'docs/contracts/world-registry-0.2.json'
PROFILE_PATH = ROOT / 'docs/contracts/world-profile-0.2.json'
SUPPORT = ROOT / 'docs/runtime-world-support-0.2.json'
PROFILE = json.loads(PROFILE_PATH.read_text())
REGISTRY_ACTIONS = json.loads(REGISTRY.read_text())['actions']
IMPLEMENTED = json.loads(SUPPORT.read_text())['actions']


def action_roles():
    """已实现的 0.2 动作的角色：门动作取登记（ADR-0005），其余对全部 world 角色开放，谁能做什么由服务代码的
    责任人规则拦下。只开已实现的动作，0.1 对照 scope 里同名以外的角色保持不变。"""
    return {a['action']: a['gate_roles'] or WORLD_ROLES for a in REGISTRY_ACTIONS if a['action'] in IMPLEMENTED}


def activation_policy(env, scope_id, domain_id):
    """在该域当前的激活策略上开放已实现的 0.2 world 动作（见 action_roles）。"""
    content = owner_rows(env, scope_id, 'SELECT content FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s '
                                        'ORDER BY policy_seq DESC LIMIT 1', (scope_id, domain_id))[0]['content']
    return {**content, 'action_roles': {**content['action_roles'], **action_roles()}}


def install_activation_policies(h, source, scopes):
    """经真实维护 CLI 给这些 scope（{scope_id: {域名: 域 id}}）的每个域装激活策略。"""
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    for scope_id, domains in scopes.items():
        for domain in domains.values():
            tag = uid()[:8]
            path = h.private / f'activation-v02-{tag}.json'
            private_json(path, activation_policy(h.env, scope_id, domain))
            adapter.cli('world-v02-activation-' + tag, [
                'install-activation-policy', '--scope-id', scope_id, '--domain-id', domain,
                '--content-json', str(path), '--reason', 'Synthetic world 0.2 actions'], expected_exit=0)


def register_world_v02(h, source, scope_id, *, registry=REGISTRY, expected_exit=0, expected_error_code=None):
    """经真实维护 CLI 安装 world 0.2 profile（同时核对契约与登记字节）；成功时再装 scope 默认策略（默认契约 0.2）
    与 0.2 支持登记。"""
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', scope_id, '--reason', 'Synthetic world 0.2 independent API acceptance']
    adapter.cli('world-v02-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(PROFILE_PATH), '--contract-file', str(CONTRACT),
        '--world-registry-file', str(registry),
    ], expected_exit=expected_exit, expected_error_code=expected_error_code)
    if expected_exit != 0:
        return
    policy = {
        'default_protocol': 'tkos.world', 'default_contract_version': 'tkos.world/0.2',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': PROFILE['profile_id'], 'revision': PROFILE['revision']},
        'experimental': True, 'notes': 'Synthetic world 0.2 fixture; no production authorization',
    }
    policy_file = h.private / ('world-v02-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('world-v02-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)],
                expected_exit=0)
    adapter.cli('world-v02-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.world', '--contract-version', 'tkos.world/0.2',
        '--content-json', str(SUPPORT),
    ], expected_exit=0)


def owner(env, scope_id=None):
    """owner 连接（事务级打开控制面）；给 scope 时 RLS 限定到该 scope，不给时可跨 scope 只读核对。"""
    conn = psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row)
    conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
    conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
    if scope_id is not None:
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))
    return conn


def probe_binding_gate(env, f, forged_contract_sha):
    """在回滚的 owner 事务里伪造一份 world 0.2 profile（契约字节不对），看 0.2 绑定门是否拒绝。"""
    with owner(env, f['scope_id']) as conn:
        forged = {**PROFILE, 'profile_id': 'urn:tkos:world:forged-v02',
                  'action_contract_ref': {**PROFILE['action_contract_ref'], 'content_sha256': forged_contract_sha}}
        object_id = uid()
        try:
            conn.execute('''INSERT INTO gov_method_profile_revisions
                (scope_id, profile_id, revision, schema_version, canonical_hash, action_contract_ref,
                 record_origin, experimental, content, installed_by, install_reason)
                VALUES (%s,%s,%s,%s,%s,%s,'synthetic',true,%s,'acceptance-probe','forged world 0.2 profile')''',
                (f['scope_id'], forged['profile_id'], forged['revision'], forged['profile_core_schema_version'],
                 'f' * 64, Jsonb(forged['action_contract_ref']), Jsonb(forged)))
            conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) "
                         "VALUES (%s,%s,%s,'Company','recorded')", (object_id, f['scope_id'], f['domains']['company']))
            conn.execute('''INSERT INTO gov_object_protocol_bindings
                (scope_id, object_id, binding_version, protocol_id, contract_version, profile_id, profile_revision,
                 profile_canonical_hash, record_origin, registered_by, detail)
                VALUES (%s,%s,1,'tkos.world','tkos.world/0.2',%s,%s,%s,'synthetic','acceptance-probe','{}')''',
                (f['scope_id'], object_id, forged['profile_id'], forged['revision'], 'f' * 64))
        except psycopg.errors.CheckViolation as exc:
            conn.rollback()
            return str(exc).splitlines()[0]
        conn.rollback()
        return None


def probe_event_row(env, f, contract_version, **columns):
    """在回滚的 owner 事务里直接插一条 world 事件，返回被哪条检查约束拒绝（没被拒绝返回 None）。"""
    values = {'scope_id': f['scope_id'], 'contract_version': contract_version, 'kind': 'object.created',
              'subject_refs': Jsonb([{'object_id': uid()}]), 'principal_id': f['actors']['ceo']['principal_id'],
              'occurred_at': '2026-09-28T00:00:00Z', 'action_id': uid(), **columns}
    with owner(env, f['scope_id']) as conn:
        try:
            conn.execute(f"INSERT INTO gov_world_events ({', '.join(values)}) VALUES ({', '.join(['%s'] * len(values))})",
                         tuple(values.values()))
        except psycopg.errors.CheckViolation as exc:
            conn.rollback()
            return exc.diag.constraint_name
        conn.rollback()
        return None
