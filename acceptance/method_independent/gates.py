"""Read-only catalog/storage evidence plus rollback-only immutability probes."""
import json
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from acceptance.composition_a2_independent.storage import guarded_no_capability, immutable_owner_probe
from acceptance.execution_a3_independent.oracle import evidence_bytes, no_effects
from acceptance.execution_a3_independent.database import MUTABLE_A1, CONTROL

from .baseline import capture, compare
from .database import BASE_COMMIT, BASE_MUTABLE, METHOD_MUTABLE, METHOD_TABLES


def schema(h):
    mutable = MUTABLE_A1 | BASE_MUTABLE | METHOD_MUTABLE
    control = CONTROL | {'gov_method_agent_bindings'}
    with h.app_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        identity = conn.execute('''SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole
            FROM pg_roles WHERE rolname=current_user''').fetchone()
        assert identity['current_user'] == identity['session_user']
        assert all(identity[name] is False for name in ['rolsuper', 'rolbypassrls', 'rolcreatedb', 'rolcreaterole'])
        rows = conn.execute('''SELECT c.relname AS name,c.relrowsecurity,c.relforcerowsecurity,
            pg_get_userbyid(c.relowner)=current_user AS app_owns,
            has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
            has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
            has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
            has_table_privilege(current_user,c.oid,'DELETE') AS can_delete
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r' AND left(c.relname,4)='gov_' ORDER BY c.relname''').fetchall()
        assert METHOD_TABLES <= {row['name'] for row in rows}
        for row in rows:
            assert not row['app_owns'] and row['relrowsecurity'] and row['relforcerowsecurity']
            assert row['can_select'] and not row['can_delete']
            assert row['can_update'] is (row['name'] in mutable), 'unexpected UPDATE grant: ' + row['name']
            assert row['can_insert'] is (row['name'] not in control), 'unexpected INSERT grant: ' + row['name']
        return {'identity': dict(identity), 'tables': [dict(row) for row in rows]}


def run(flow, book, *, database_evidence, upgrade_evidence, preservation_baseline):
    h = flow.h
    database = conninfo_to_dict(h.env.values['APP_DATABASE_URL'])['dbname']
    created = json.loads(Path(database_evidence).read_text())
    migrated = json.loads(Path(upgrade_evidence).read_text())
    assert created['database'] == migrated['database'] == database
    assert created['base_commit'] == BASE_COMMIT and created['migrations'][-1] == '0020_execution_handover.sql'
    assert created['existing_databases_modified'] is False and created['containers_modified'] is False
    book.gates['fresh_database_from_accepted_base'] = {'passed': True, 'evidence': created}
    assert migrated['applied'] == ['0021_method_foundation.sql'] and migrated['repeat_applied'] == []
    book.gates['migration_replay'] = {'passed': True, 'evidence': migrated}
    book.gates['ordinary_application_privileges'] = {'passed': True, 'evidence': schema(h)}
    state = flow.rows('gov_method_state')
    reviews = flow.rows('gov_method_reviews')
    assert state and reviews
    probes = [
        guarded_no_capability(h, flow.f, table='gov_method_state', column='state', key_column='object_id', key=str(state[0]['object_id'])),
        immutable_owner_probe(h, flow.f, table='gov_method_reviews', column='kind', key_column='record_id', key=str(reviews[0]['record_id'])),
    ]
    book.gates['immutable_history'] = {'passed': True, 'evidence': probes}
    assert flow.evidence
    evidence = [evidence_bytes(flow, upload) for upload in flow.evidence]
    stored = h.storage_snapshot(flow.f['scope_id'])
    assert stored['count'] >= len(flow.evidence)
    book.gates['raw_evidence_storage'] = {'passed': True, 'evidence': {'uploads': evidence, 'storage': stored}}
    book.gates['no_external_effects'] = {'passed': True, 'evidence': no_effects(flow)}
    before = json.loads(Path(preservation_baseline).read_text())
    proof = compare(before, capture())
    assert proof['files_unchanged'] and proof['containers_unchanged']
    book.gates['existing_environment_preserved'] = {'passed': True, 'evidence': proof}
    book.save()
    return {'database': database, 'evidence_uploads_verified': len(evidence), 'preservation': proof}
