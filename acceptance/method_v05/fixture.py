"""0.5 身份与登记：复用 0.4 的合成身份，补 0.5 动作的角色策略，只把契约、profile 与注册表换成 0.5。"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.method_independent.fixture import METHOD_ROLES, uid
from acceptance.method_v04.fixture import seed_v04
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]
V05_ACTIONS = list(json.loads((ROOT / 'docs/runtime-method-registry-0.5.json').read_text())['actions'])


def seed_v05(env, path: Path, label: str):
    fixture = seed_v04(env, path, label)
    fixture['contract_version'] = 'tkos.method/0.5'
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (fixture['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (fixture['scope_id'],))
        for domain in (fixture['domains']['company'], fixture['domains']['a'], fixture['domains']['b'],
                       fixture['domains']['auth_a'], fixture['domains']['auth_b']):
            old = conn.execute(
                """SELECT policy_id,policy_seq,content FROM gov_activation_policies
                   WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1""",
                (fixture['scope_id'], domain)).fetchone()
            content = dict(old['content'])
            action_roles = dict(content.get('action_roles', {}))
            for action in V05_ACTIONS:
                action_roles[action] = sorted(set(METHOD_ROLES) | {'MISSION_DRI', 'AGENT'})
            content['action_roles'] = action_roles
            conn.execute(
                """INSERT INTO gov_activation_policies
                   (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (uid(), fixture['scope_id'], domain, old['policy_id'], old['policy_seq'] + 1,
                 Jsonb(content), fixture['actors']['ceo']['principal_id']))
    private_json(path, fixture)
    return fixture


def register_v05(h, source, f):
    """通过真实维护 CLI 安装 0.5 profile / policy / registry；profile 同时核对本体登记字节。"""
    profile_path = ROOT / 'docs/contracts/method-profile-0.5.json'
    registry_path = ROOT / 'docs/runtime-method-registry-0.5.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic Method 0.5 independent API acceptance']
    adapter.cli('method-v05-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-method-0.5.md'),
        '--ontology-registry-file', str(ROOT / 'docs/contracts/ontology-registry-0.7.json'),
    ], expected_exit=0)
    policy = {
        'default_protocol': 'tkos.method', 'default_contract_version': 'tkos.method/0.5',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': profile['profile_id'], 'revision': profile['revision']},
        'experimental': True, 'notes': 'Synthetic Method 0.5 fixture; no production authorization',
    }
    policy_file = h.private / ('method-v05-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('method-v05-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)], expected_exit=0)
    adapter.cli('method-v05-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.method',
        '--contract-version', 'tkos.method/0.5', '--content-json', str(registry_path),
    ], expected_exit=0)
    return profile
