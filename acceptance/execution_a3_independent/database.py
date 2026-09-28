"""Explicit creation of a fresh A3 test database from an immutable A2 source.

Uses local PostgreSQL's existing socket administration, never reveals a password,
never changes an existing database or container, never drops data. Requires an
explicit invocation; imports perform no work. Private environments are passed
only to children of their intended privilege level.
"""
from __future__ import annotations
import argparse
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import tarfile
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from acceptance.protocol_a1_independent.support import Environment, private_json, public_json
from acceptance.protocol_a1_independent.database import source_migrate

BASE = 'e7d0d6b821f6ca94672c73610a3da85aa5ce6577'
MUTABLE_A1 = {'gov_scopes', 'gov_principals', 'gov_credentials', 'gov_role_assignments',
              'gov_objects', 'gov_feedback_state', 'gov_work_item_state',
              'gov_formation_round_state', 'gov_round_formal_submissions'}
CONTROL = {'gov_method_profile_revisions', 'gov_protocol_policies',
           'gov_protocol_support_registry', 'gov_protocol_control_events'}


def socket_sql(container: str, database: str, statement: str):
    # Identifiers are handled by psycopg.sql before stdin. Command text is fixed;
    # POSTGRES_USER resolves only inside the existing private container process.
    run = subprocess.run(['docker', 'exec', '-i', container, 'sh', '-c',
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"', 'sh', database],
        input=statement, text=True, capture_output=True, timeout=30)
    if run.returncode:
        raise AssertionError('local PostgreSQL administration failed; no credential/log output emitted')


RELEASE_DB_ADMIN = Path(__file__).resolve().parents[2] / 'deploy' / 'offline-release' / 'db_admin.py'
PRIVILEGE_VERBS = ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')


def release_rules():
    """The release grant step (deploy/offline-release/db_admin.py), loaded by path like infra.GRANTS."""
    spec = importlib.util.spec_from_file_location('release_db_admin', RELEASE_DB_ADMIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def grants(environment: Environment):
    """Grant the application role exactly the release privileges, then verify them as that role.

    Every table's privileges come from the release's expected_privileges (the same
    source infra.GRANTS uses), after REVOKE ALL; a private copy of the rules here
    drifted before (it granted DELETE on the runtime queue tables).
    """
    release = release_rules()
    app = conninfo_to_dict(environment.values['APP_DATABASE_URL'])['user']
    with psycopg.connect(environment.values['MIGRATION_DATABASE_URL']) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()]
        for table in tables:
            conn.execute(sql.SQL('REVOKE ALL ON TABLE public.{} FROM {}').format(
                sql.Identifier(table), sql.Identifier(app)))
            privileges = ', '.join(sorted(release.expected_privileges(table)))
            conn.execute(sql.SQL('GRANT ' + privileges + ' ON TABLE public.{} TO {}').format(
                sql.Identifier(table), sql.Identifier(app)))
        conn.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(app)))
    with psycopg.connect(environment.values['APP_DATABASE_URL']) as conn:
        effective = conn.execute(
            "SELECT tablename, " + ", ".join(
                "has_table_privilege(current_user, quote_ident(schemaname)||'.'||quote_ident(tablename), '"
                + verb + "')" for verb in PRIVILEGE_VERBS)
            + " FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    diverged = [row[0] for row in effective
                if {verb for verb, granted in zip(PRIVILEGE_VERBS, row[1:]) if granted}
                != release.expected_privileges(row[0])]
    if diverged:
        raise AssertionError('application role privileges diverge from the release: ' + ', '.join(diverged))


def create(args):
    repo = Path(__file__).resolve().parents[2]
    if args.private.exists() or args.output.exists():
        raise ValueError('fresh private and public output paths are required')
    base = Environment(args.env_file)
    owner = conninfo_to_dict(base.values['MIGRATION_DATABASE_URL'])
    app = conninfo_to_dict(base.values['APP_DATABASE_URL'])
    if owner.get('host') != '127.0.0.1' or owner.get('port') != '54350':
        raise ValueError('this local helper requires the observed acceptance PostgreSQL endpoint')
    if args.container != 'tkos-ontology-runtime-acceptance-postgres-1':
        raise ValueError('this helper only targets the existing local acceptance PostgreSQL container')
    if subprocess.check_output(['docker','context','show'],text=True).strip() != 'desktop-linux':
        raise ValueError('unexpected Docker context')
    args.private.mkdir(parents=True, mode=0o700)
    source_root = args.private / 'base-source'
    source_root.mkdir()
    archive = subprocess.check_output(['git','archive',BASE,'src'],cwd=repo)
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(source_root, filter='data')
    name = 'tkos_a1_a2_a3_' + uuid.uuid4().hex[:16]
    assert re.fullmatch(r'tkos_a1_a2_a3_[a-f0-9]{16}',name)
    statement = sql.SQL('CREATE DATABASE {} OWNER {};').format(sql.Identifier(name),sql.Identifier(owner['user'])).as_string()
    socket_sql(args.container,'postgres',statement)
    values = dict(base.values)
    for key in ('APP_DATABASE_URL','MIGRATION_DATABASE_URL'):
        values[key] = make_conninfo(base.values[key],dbname=name)
    values['DATABASE_URL'] = values['APP_DATABASE_URL']
    env_file = args.private/'env.json'
    private_json(env_file,values)
    socket_sql(args.container,name,'CREATE EXTENSION IF NOT EXISTS vector;')
    with psycopg.connect(values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}, {}').format(
            sql.Identifier(name),sql.Identifier(owner['user']),sql.Identifier(app['user'])))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(app['user'])))
    env = Environment(env_file)
    first = source_migrate(env,source_root/'src',args.output/'base-migrate-first.json')
    second = source_migrate(env,source_root/'src',args.output/'base-migrate-repeat.json')
    assert first['applied'][-1]=='0019_company_composition.sql' and second['applied']==[]
    grants(env)
    result = {'database':name,'base_commit':BASE,'private_environment':str(env_file),
              'base_source':str(source_root/'src'),'existing_databases_modified':False,
              'containers_modified':False,'migrations':first['applied'],'repeat_applied':second['applied']}
    public_json(args.output/'database.json',result)
    print(json.dumps(result))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env-file',type=Path,required=True)
    p.add_argument('--private',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--container',required=True)
    create(p.parse_args())

if __name__=='__main__': main()
