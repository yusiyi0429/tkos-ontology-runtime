"""Independent schema privileges and immutable-history probes.

Every write probe uses a transaction that is unconditionally rolled back. It
can only attempt to change existing synthetic A2 rows; it never fabricates a
successful composition. No production mutation helper is imported.
"""
from __future__ import annotations
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from .database import MUTABLE_A1,CONTROL


def catalog(h, *, new_tables: set[str], mutable_tables: set[str]):
    with h.app_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        identity=conn.execute('''SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,
            rolcreaterole FROM pg_roles WHERE rolname=current_user''').fetchone()
        assert identity['current_user']==identity['session_user']
        assert all(identity[name] is False for name in ('rolsuper','rolbypassrls','rolcreatedb','rolcreaterole'))
        rows=conn.execute('''SELECT c.relname AS name,c.relrowsecurity,c.relforcerowsecurity,
            pg_get_userbyid(c.relowner)=current_user AS app_owns,
            has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
            has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
            has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
            has_table_privilege(current_user,c.oid,'DELETE') AS can_delete
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='r' AND left(c.relname,4)='gov_'
            ORDER BY c.relname''').fetchall()
        names={row['name'] for row in rows}
        assert new_tables<=names,'missing required A2 table'
        for row in rows:
            assert not row['app_owns'] and row['relrowsecurity'] and row['relforcerowsecurity']
            assert row['can_select'] and not row['can_delete']
            assert row['can_update']==(row['name'] in MUTABLE_A1|mutable_tables), 'unexpected UPDATE grant: '+row['name']
            assert row['can_insert']==(row['name'] not in CONTROL), 'unexpected INSERT grant: '+row['name']
        return {'identity':dict(identity),'tables':[dict(r) for r in rows],'new_tables':sorted(new_tables)}


def guarded_no_capability(h,f,*, table: str, column: str, key_column: str, key: str):
    assert table.startswith('gov_')
    failure=None
    conn=psycopg.connect(h.env.values['APP_DATABASE_URL'],row_factory=dict_row)
    try:
        conn.execute("SELECT set_config('app.runtime_write_capability','',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)",(f['scope_id'],))
        # An actual existing state row is necessary: a WHERE matching zero rows
        # would never reach a row trigger and cannot demonstrate write fencing.
        count=conn.execute(sql.SQL('SELECT count(*) AS n FROM {} WHERE scope_id=%s AND {}=%s').format(
            sql.Identifier(table),sql.Identifier(key_column)),(f['scope_id'],key)).fetchone()['n']
        assert count==1,'probe row not visible; no trigger claim can be made'
        try:
            conn.execute(sql.SQL('UPDATE {} SET {}={} WHERE scope_id=%s AND {}=%s').format(
                sql.Identifier(table),sql.Identifier(column),sql.Identifier(column),sql.Identifier(key_column)),
                (f['scope_id'],key))
        except psycopg.Error as error:
            failure=error.sqlstate
        # 0018 explicitly specifies 55000 for the capability trigger.
        assert failure == '55000','write without implementation capability did not fail closed'
    finally:conn.rollback();conn.close()
    return {'table':table,'sqlstate':failure,'existing_row_matched':True,'probe_transaction_rolled_back':True}


def immutable_owner_probe(h,f,*, table: str, column: str, key_column: str, key: str):
    before=h.snapshot(f);failure=None
    conn=psycopg.connect(h.env.values['MIGRATION_DATABASE_URL'])
    try:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)",(f['scope_id'],))
        count=conn.execute(sql.SQL('SELECT count(*) FROM {} WHERE scope_id=%s AND {}=%s').format(
            sql.Identifier(table),sql.Identifier(key_column)),(f['scope_id'],key)).fetchone()[0]
        assert count==1
        try:
            conn.execute(sql.SQL('UPDATE {} SET {}={} WHERE scope_id=%s AND {}=%s').format(
                sql.Identifier(table),sql.Identifier(column),sql.Identifier(column),sql.Identifier(key_column)),
                (f['scope_id'],key))
        except psycopg.Error as error:failure=error.sqlstate
        # gov_reject_mutation (0016) deliberately returns object_not_in_prerequisite_state.
        assert failure == '55000','immutable history accepted an owner UPDATE'
    finally:conn.rollback();conn.close()
    assert h.snapshot(f)==before
    return {'table':table,'owner_write_rejected':True,'sqlstate':failure,'all_state_unchanged':True}
