"""world 验收的合成身份与登记：身份只由 owner SQL 播种，业务成功一律来自 HTTP。"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.composition_a2_independent.fixture import seed_authority
from acceptance.method_independent.fixture import uid
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]
WORLD_ROLES = ['AGENT', 'CEO', 'DOMAIN_DRI', 'IC', 'MISSION_DRI', 'VERIFIER']
WORLD_ACTIONS = [a['action'] for a in json.loads((ROOT / 'docs/contracts/world-registry-0.1.json').read_text())['actions']]


def seed_world(env, path: Path, label: str):
    """一个 world scope（五个域与 A2 基础身份），外加另一 scope 的一名 CEO 作为 scope 外的读者。

    world 动作在每个域的激活策略里对全部角色开放：谁能做什么由服务代码的责任人规则拦下，
    这样拒绝用例证明的是代码规则，而不是策略没配。
    """
    foreign = seed_authority(env, path.with_name('foreign-' + path.name), label + '-foreign')
    f = seed_authority(env, path, label)
    f['actors']['foreign_ceo'] = foreign['actors']['ceo']
    f['foreign_scope_id'] = foreign['scope_id']
    f['foreign_domains'] = foreign['domains']
    for scope in (f, foreign):
        _open_world_actions(env, scope)
    f['bystander_principal_id'] = _seed_bystander(env, f)
    private_json(path, f)
    return f


def _seed_bystander(env, f):
    """本 scope 里一个启用的人，但没有任何角色指派：不算 scope 内有效的人（契约第 12 节）。"""
    principal = uid()
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute('INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)',
                     (principal, f['scope_id'], 'human', 'Synthetic world bystander'))
    return principal


def _open_world_actions(env, f):
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (f['scope_id'],))
        for domain in f['domains'].values():
            old = conn.execute('''SELECT * FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s
                ORDER BY policy_seq DESC LIMIT 1''', (f['scope_id'], domain)).fetchone()
            content = dict(old['content'])
            content['action_roles'] = {**content['action_roles'], **{action: WORLD_ROLES for action in WORLD_ACTIONS}}
            conn.execute('''INSERT INTO gov_activation_policies
                (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (uid(), f['scope_id'], domain, old['policy_id'], old['policy_seq'] + 1,
                 Jsonb(content), f['actors']['ceo']['principal_id']))


def revoke_assignment(env, f, assignment_id):
    """合成的权限变更（owner SQL）：撤掉一条角色指派，不写任何业务行或回执。"""
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (f['scope_id'],))
        row = conn.execute('UPDATE gov_role_assignments SET active=false WHERE scope_id=%s AND assignment_id=%s RETURNING 1',
                           (f['scope_id'], assignment_id)).fetchone()
        assert row is not None


def probe_binding_gate(env, f, forged_contract_sha):
    """在回滚的 owner 事务里伪造一份 world profile（契约字节不对），看 world 绑定门是否拒绝。"""
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        profile = json.loads((ROOT / 'docs/contracts/world-profile-0.1.json').read_text())
        forged = {**profile, 'profile_id': 'urn:tkos:world:forged',
                  'action_contract_ref': {**profile['action_contract_ref'], 'content_sha256': forged_contract_sha}}
        object_id = uid()
        try:
            conn.execute('''INSERT INTO gov_method_profile_revisions
                (scope_id, profile_id, revision, schema_version, canonical_hash, action_contract_ref,
                 record_origin, experimental, content, installed_by, install_reason)
                VALUES (%s,%s,%s,%s,%s,%s,'synthetic',true,%s,'acceptance-probe','forged world profile')''',
                (f['scope_id'], forged['profile_id'], forged['revision'], forged['profile_core_schema_version'],
                 'f' * 64, Jsonb(forged['action_contract_ref']), Jsonb(forged)))
            conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) VALUES (%s,%s,%s,'Company','recorded')",
                         (object_id, f['scope_id'], f['domains']['company']))
            conn.execute('''INSERT INTO gov_object_protocol_bindings
                (scope_id, object_id, binding_version, protocol_id, contract_version, profile_id, profile_revision,
                 profile_canonical_hash, record_origin, registered_by, detail)
                VALUES (%s,%s,1,'tkos.world','tkos.world/0.1',%s,%s,%s,'synthetic','acceptance-probe','{}')''',
                (f['scope_id'], object_id, forged['profile_id'], forged['revision'], 'f' * 64))
        except psycopg.errors.CheckViolation as exc:
            conn.rollback()
            return str(exc).splitlines()[0]
        conn.rollback()
        return None


def register_world(h, source, f):
    """经真实维护 CLI 安装 world profile（同时核对契约与 world 登记字节）、scope 默认策略与支持登记。"""
    profile_path = ROOT / 'docs/contracts/world-profile-0.1.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic world 0.1 independent API acceptance']
    adapter.cli('world-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-world-0.1.md'),
        '--world-registry-file', str(ROOT / 'docs/contracts/world-registry-0.1.json'),
    ], expected_exit=0)
    policy = {
        'default_protocol': 'tkos.world', 'default_contract_version': 'tkos.world/0.1',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': profile['profile_id'], 'revision': profile['revision']},
        'experimental': True, 'notes': 'Synthetic world 0.1 fixture; no production authorization',
    }
    policy_file = h.private / ('world-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('world-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)], expected_exit=0)
    adapter.cli('world-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.world', '--contract-version', 'tkos.world/0.1',
        '--content-json', str(ROOT / 'docs/runtime-world-support-0.1.json'),
    ], expected_exit=0)
    return profile
