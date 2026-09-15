"""Independent versioned registration through the control CLI."""
import json
from pathlib import Path
from acceptance.method_independent.fixture import uid
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json
ROOT = Path(__file__).resolve().parents[2]


def register_v03(h, source, f):
    """Use the real maintenance API; no direct insert of protocol success rows."""
    profile_path = ROOT / 'docs/contracts/method-profile-0.3.json'
    registry_path = ROOT / 'docs/runtime-method-registry-0.3.json'
    profile = json.loads(profile_path.read_text())
    tag = uid()[:8]
    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    common = ['--scope-id', f['scope_id'], '--reason', 'Synthetic Method independent API acceptance']
    adapter.cli('method-profile-' + tag, [
        'install-profile', *common, '--profile-json', str(profile_path),
        '--contract-file', str(ROOT / 'docs/contracts/tkos-method-0.3.md'),
    ], expected_exit=0)
    policy = {
        'default_protocol': 'tkos.method', 'default_contract_version': 'tkos.method/0.3',
        'allow_legacy_create': False, 'record_origin': 'synthetic',
        'default_profile_ref': {'profile_id': profile['profile_id'], 'revision': profile['revision']},
        'experimental': True, 'notes': 'Synthetic Method fixture; no production authorization',
    }
    policy_file = h.private / ('method-policy-' + tag + '.json')
    private_json(policy_file, policy)
    adapter.cli('method-policy-' + tag, ['install-policy', *common, '--content-json', str(policy_file)], expected_exit=0)
    adapter.cli('method-registry-' + tag, [
        'set-registry', *common, '--protocol-id', 'tkos.method', '--contract-version', 'tkos.method/0.3',
        '--content-json', str(registry_path),
    ], expected_exit=0)
    return profile


def seed_v03(env, path, label):
    """Only synthetic identity/policy setup uses SQL, never business objects."""
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from acceptance.method_independent.fixture import seed_authority, METHOD_ROLES
    fixture = seed_authority(env, path, label)
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (fixture['scope_id'],))
        conn.execute('SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE', (fixture['scope_id'],))
        # This scope models one company CEO; legacy mixed-role fixture remains unchanged.
        conn.execute('UPDATE gov_role_assignments SET active=false WHERE scope_id=%s AND assignment_id=%s',
                     (fixture['scope_id'], fixture['actors']['mixed_role']['assignment_id']))
        for domain in fixture['domains'].values():
            old = conn.execute('SELECT * FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1', (fixture['scope_id'], domain)).fetchone()
            content = old['content']
            from memory_service_runtime.governed.method_v03_models import ACTION_PARAMS
            for action in ACTION_PARAMS:
                content['action_roles'][action] = METHOD_ROLES
            conn.execute('INSERT INTO gov_activation_policies (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (uid(),fixture['scope_id'],domain,old['policy_id'],old['policy_seq']+1,Jsonb(content),fixture['actors']['ceo']['principal_id']))
    return fixture
