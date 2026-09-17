"""Synthetic 0.4 identities/policy only; every business success comes from HTTP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.method_independent.fixture import METHOD_ROLES, seed_authority, uid
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]
V04_ACTIONS = list(json.loads((ROOT / 'docs/runtime-method-registry-0.4.json').read_text())['actions'])


def _credential(conn, fixture, name, role, domain, kind, label, owner=None, agent=None):
    principal, assignment, token = uid(), uid(), secrets.token_urlsafe(48)
    conn.execute("INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)",
                 (principal, fixture['scope_id'], kind, 'Synthetic Method 0.4 ' + name))
    conn.execute("INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)",
                 (assignment, fixture['scope_id'], principal, domain, role))
    digest = hashlib.sha256(token.encode()).hexdigest()
    conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)", (digest,))
    conn.execute("INSERT INTO gov_credentials(scope_id,principal_id,credential_digest,label) VALUES (%s,%s,%s,%s)",
                 (fixture['scope_id'], principal, digest, label))
    if owner is not None and agent is not None:
        conn.execute("""INSERT INTO gov_method_agent_bindings(scope_id,agent_principal_id,owner_principal_id,assignment_id)
                        VALUES (%s,%s,%s,%s)""",
                     (fixture['scope_id'], principal, owner, assignment))
    fixture['actors'][name] = {'principal_id': principal, 'assignment_id': assignment, 'token': token,
                               'domain_id': domain, 'role': role, 'principal_type': kind}


def seed_v04(env, path: Path, label: str):
    """Base synthetic authority plus two fresh auth domains with a single DRI each.

    Mission Owners deliberately hold only an IC appointment in their own auth
    domain, so State/commitment authorization must use the scoped fallback, not
    a company-domain CEO path.
    """
    fixture = seed_authority(env, path, label)
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (fixture['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (fixture['scope_id'],))
        auth_a, auth_b = uid(), uid()
        for domain, name in ((auth_a, 'Synthetic Method 0.4 scope A'), (auth_b, 'Synthetic Method 0.4 scope B')):
            conn.execute('INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)',
                         (domain, fixture['scope_id'], name))
        # 0.4 requires one unique current CEO per company domain; the legacy
        # mixed-role negative fixture keeps its other appointments only.
        conn.execute('UPDATE gov_role_assignments SET active=false WHERE scope_id=%s AND assignment_id=%s',
                     (fixture['scope_id'], fixture['actors']['mixed_role']['assignment_id']))
        fixture['domains']['auth_a'] = auth_a
        fixture['domains']['auth_b'] = auth_b
        _credential(conn, fixture, 'dri_a', 'DOMAIN_DRI', auth_a, 'human', 'Synthetic Method 0.4 scope A DRI')
        _credential(conn, fixture, 'dri_b', 'DOMAIN_DRI', auth_b, 'human', 'Synthetic Method 0.4 scope B DRI')
        _credential(conn, fixture, 'owner_a', 'IC', auth_a, 'human', 'Synthetic Method 0.4 scope A Mission Owner')
        _credential(conn, fixture, 'owner_b', 'IC', auth_b, 'human', 'Synthetic Method 0.4 scope B Mission Owner')
        _credential(conn, fixture, 'agent_a', 'PERSONAL_AGENT', auth_a, 'agent', 'Synthetic Method 0.4 A DRI Agent',
                    owner=fixture['actors']['dri_a']['principal_id'], agent=True)
        _credential(conn, fixture, 'agent_b', 'PERSONAL_AGENT', auth_b, 'agent', 'Synthetic Method 0.4 B DRI Agent',
                    owner=fixture['actors']['dri_b']['principal_id'], agent=True)
        _credential(conn, fixture, 'owner_agent_a', 'PERSONAL_AGENT', auth_a, 'agent',
                    'Synthetic Method 0.4 A Mission Owner Agent',
                    owner=fixture['actors']['owner_a']['principal_id'], agent=True)
        # Role policy for every 0.4 action on the domains this scenario exercises.
        company_policy = conn.execute(
            """SELECT content FROM gov_activation_policies
               WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1""",
            (fixture['scope_id'], fixture['domains']['company'])).fetchone()
        for domain in (fixture['domains']['company'], fixture['domains']['a'], fixture['domains']['b'], auth_a, auth_b):
            old = conn.execute(
                """SELECT policy_id,policy_seq,content FROM gov_activation_policies
                   WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1""",
                (fixture['scope_id'], domain)).fetchone()
            if old is None:
                content = dict(company_policy['content'])
                policy_id, policy_seq = uid(), 1
            else:
                content = dict(old['content'])
                policy_id, policy_seq = old['policy_id'], old['policy_seq'] + 1
            action_roles = dict(content.get('action_roles', {}))
            for action in V04_ACTIONS:
                action_roles[action] = sorted(set(METHOD_ROLES) | {'MISSION_DRI', 'AGENT'})
            content['action_roles'] = action_roles
            conn.execute(
                """INSERT INTO gov_activation_policies
                   (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (uid(), fixture['scope_id'], domain, policy_id, policy_seq,
                 Jsonb(content), fixture['actors']['ceo']['principal_id']))
        conn.execute("SELECT set_config('app.governed_credential_digest','',true)")
    private_json(path, fixture)
    return fixture


def register_v04(h, source, f):
    """Install the 0.4 profile/policy/registry through the real maintenance CLI."""
    profile_path = ROOT / 'docs/contracts/method-profile-0.4.json'
    registry_path = ROOT / 'docs/runtime-method-registry-0.4.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic Method 0.4 independent API acceptance']
    adapter.cli('method-v04-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-method-0.4.md'),
    ], expected_exit=0)
    policy = {
        'default_protocol': 'tkos.method', 'default_contract_version': 'tkos.method/0.4',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': profile['profile_id'], 'revision': profile['revision']},
        'experimental': True, 'notes': 'Synthetic Method 0.4 fixture; no production authorization',
    }
    policy_file = h.private / ('method-v04-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('method-v04-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)], expected_exit=0)
    adapter.cli('method-v04-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.method',
        '--contract-version', 'tkos.method/0.4', '--content-json', str(registry_path),
    ], expected_exit=0)
    return profile
