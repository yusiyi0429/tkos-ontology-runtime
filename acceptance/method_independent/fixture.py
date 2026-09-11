"""Synthetic identities and policy only; every business success comes from HTTP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import secrets
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.composition_a2_independent.fixture import seed_authority as seed_base_authority
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json

ROOT = Path(__file__).resolve().parents[2]
METHOD_ROLES = ['CEO', 'DOMAIN_DRI', 'IC', 'CEO_AGENT', 'CO_AGENT', 'PERSONAL_AGENT']
METHOD_ACTIONS = [
    'm1a_record_signal', 'm1a_open_potential_issue', 'm1a_revise_potential_issue',
    'm1a_confirm_strategic_issue', 'm1a_assign_research', 'm1a_publish_memo',
    'm1a_record_clarification', 'm1a_check_memo', 'm1a_direct_clarification',
    'm1a_publish_research_plan', 'm1a_publish_report', 'm1a_submit_report',
    'm1a_precheck_report', 'm1a_open_meeting', 'm1a_publish_minutes',
    'm1a_reconcile_minutes', 'm1a_confirm_minutes', 'm1a_confirm_agreement',
    'm1a_decide_update', 'm1a_propose_update', 'm1a_review_update', 'm1a_confirm_update',
    'm1b_record_fact', 'm1b_correct_fact', 'm1b_generate_review', 'm1b_regenerate_review',
    'm1b_advise_ltco', 'm1b_propose_ltco', 'm1b_revise_ltco', 'm1b_return_ltco',
    'm1b_confirm_ltco', 'm1b_draft_pco', 'm1b_revise_pco', 'm1b_draft_mission',
    'm1b_revise_mission', 'm1b_open_window', 'm1b_comment', 'm1b_withdraw_comment',
    'm1b_assist_review', 'm1b_close_window', 'm1b_resolve_window',
    'm1b_confirm_candidates', 'm1b_reopen_candidates', 'm1b_reopen_window',
    'method_open_run', 'method_attach_run', 'method_pause_run', 'method_resume_run', 'method_record_attempt',
]


def uid():
    return str(uuid.uuid4())


def seed_authority(env, path: Path, label: str):
    f = seed_base_authority(env, path, label)
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (f['scope_id'],))
        actors = [
            ('ceo_agent', 'CEO_AGENT', 'company', 'agent', 'ceo'),
            ('co_agent', 'CO_AGENT', 'company', 'agent', None),
            ('dri_agent', 'PERSONAL_AGENT', 'a', 'agent', 'a'),
            ('b_agent', 'PERSONAL_AGENT', 'b', 'agent', 'b'),
            ('outsider_agent', 'PERSONAL_AGENT', 'outsider', 'agent', 'outsider'),
            ('wrong_a', 'DOMAIN_DRI', 'a', 'human', None),
            ('mixed_role', 'CEO', 'company', 'human', None),
        ]
        for name, role, domain, kind, owner in actors:
            principal, assignment, token = uid(), uid(), secrets.token_urlsafe(48)
            conn.execute('''INSERT INTO gov_principals
                (principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)''',
                (principal, f['scope_id'], kind, 'Synthetic Method ' + name))
            conn.execute('''INSERT INTO gov_role_assignments
                (assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)''',
                (assignment, f['scope_id'], principal, f['domains'][domain], role))
            digest = hashlib.sha256(token.encode()).hexdigest()
            conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)", (digest,))
            conn.execute('''INSERT INTO gov_credentials
                (scope_id,principal_id,credential_digest,label) VALUES (%s,%s,%s,%s)''',
                (f['scope_id'], principal, digest, 'Synthetic Method ' + name))
            f['actors'][name] = {
                'principal_id': principal, 'assignment_id': assignment,
                'token': token, 'domain_id': f['domains'][domain], 'role': role,
                'principal_type': kind,
            }
            if owner:
                conn.execute('''INSERT INTO gov_method_agent_bindings
                    (scope_id,agent_principal_id,owner_principal_id,assignment_id)
                    VALUES (%s,%s,%s,%s)''',
                    (f['scope_id'], principal, f['actors'][owner]['principal_id'], assignment))
        # This actor deliberately has no domain CEO assignments. It catches
        # responsibility selection accidentally using a role from another domain.
        f['mixed_role_assignments'] = {}
        for domain, role in [('a', 'DOMAIN_DRI'), ('b', 'IC')]:
            assignment = uid()
            conn.execute('''INSERT INTO gov_role_assignments
                (assignment_id,scope_id,principal_id,domain_id,role) VALUES (%s,%s,%s,%s,%s)''',
                (assignment, f['scope_id'], f['actors']['mixed_role']['principal_id'],
                 f['domains'][domain], role))
            f['mixed_role_assignments'][domain] = assignment
        conn.execute("SELECT set_config('app.governed_credential_digest','',true)")
        for domain in f['domains'].values():
            old = conn.execute('''SELECT * FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s
                ORDER BY policy_seq DESC LIMIT 1''', (f['scope_id'], domain)).fetchone()
            policy = dict(old['content'])
            policy['action_roles'] = dict(policy['action_roles'])
            for action in METHOD_ACTIONS:
                policy['action_roles'][action] = METHOD_ROLES
            for action in ['read', 'upload_evidence']:
                policy['action_roles'][action] = sorted(set(policy['action_roles'][action]) | set(METHOD_ROLES))
            conn.execute('''INSERT INTO gov_activation_policies
                (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (uid(), f['scope_id'], domain, old['policy_id'], old['policy_seq'] + 1,
                 Jsonb(policy), f['actors']['ceo']['principal_id']))
    private_json(path, f)
    return f


def register_method(h, source, f):
    """Use the real maintenance API; no direct insert of protocol success rows."""
    profile_path = ROOT / 'docs/contracts/method-profile.json'
    registry_path = ROOT / 'docs/runtime-method-registry.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic Method independent API acceptance']
    adapter.cli('method-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-method-0.1.md'),
    ], expected_exit=0)
    policy = {
        'default_protocol': 'tkos.method', 'default_contract_version': 'tkos.method/0.1',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': profile['profile_id'], 'revision': profile['revision']},
        'experimental': True, 'notes': 'Synthetic Method fixture; no production authorization',
    }
    policy_file = h.private / ('method-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('method-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)], expected_exit=0)
    adapter.cli('method-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.method', '--contract-version', 'tkos.method/0.1',
        '--content-json', str(registry_path),
    ], expected_exit=0)
    return profile
