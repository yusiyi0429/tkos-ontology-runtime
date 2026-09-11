"""Create a fresh local Method database at the accepted A3 source baseline.

Imports do no work. This helper never resets, drops, or migrates an existing
database and never changes container lifecycle or credentials. A failed setup
retains its fresh database and private environment for diagnosis.
"""
from __future__ import annotations

import argparse
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

from acceptance.execution_a3_independent.database import grants, socket_sql
from acceptance.protocol_a1_independent.database import source_migrate
from acceptance.protocol_a1_independent.support import Environment, private_json, public_json

BASE_COMMIT = '1b8cec9cc30e561e3570bc5dd010a09126f283c2'
CONTAINER = 'tkos-ontology-runtime-acceptance-postgres-1'
BASE_MUTABLE = {'gov_execution_state', 'gov_a3_work_item_state'}
METHOD_MUTABLE = {'gov_method_state', 'gov_method_strategy_heads', 'gov_method_runs'}
METHOD_TABLES = METHOD_MUTABLE | {
    'gov_method_reviews', 'gov_method_impacts', 'gov_method_run_attempts',
    'gov_method_run_members', 'gov_method_agent_bindings',
}


def method_grants(env):
    grants(env, BASE_MUTABLE | METHOD_MUTABLE)
    app = conninfo_to_dict(env.values['APP_DATABASE_URL'])['user']
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute(sql.SQL('REVOKE INSERT ON gov_method_agent_bindings FROM {}').format(sql.Identifier(app)))


def upgrade(env_file, source, output):
    env = Environment(env_file)
    database = conninfo_to_dict(env.values['MIGRATION_DATABASE_URL'])['dbname']
    if not re.fullmatch(r'tkos_a1_method_[a-f0-9]{16}', database):
        raise ValueError('upgrade requires an explicitly generated Method acceptance database')
    first = source_migrate(env, source, output / 'method-migrate-first.json')
    second = source_migrate(env, source, output / 'method-migrate-repeat.json')
    assert first['applied'] == ['0021_method_foundation.sql']
    assert second['applied'] == []
    method_grants(env)
    result = {'database': database, 'applied': first['applied'], 'repeat_applied': second['applied'],
              'existing_databases_modified': False, 'containers_modified': False}
    public_json(output / 'upgrade.json', result)
    return result


def create(args):
    repo = Path(__file__).resolve().parents[2]
    if args.private.exists() or args.output.exists():
        raise ValueError('fresh private and public output paths are required')
    base = Environment(args.env_file)
    owner = conninfo_to_dict(base.values['MIGRATION_DATABASE_URL'])
    app = conninfo_to_dict(base.values['APP_DATABASE_URL'])
    if (owner.get('host'), owner.get('port')) != ('127.0.0.1', '54350'):
        raise ValueError('helper requires the observed local acceptance PostgreSQL endpoint')
    if args.container != CONTAINER:
        raise ValueError('unexpected acceptance PostgreSQL container')
    if subprocess.check_output(['docker', 'context', 'show'], text=True).strip() != 'desktop-linux':
        raise ValueError('unexpected Docker context')
    args.private.mkdir(parents=True, mode=0o700)
    source_root = args.private / 'base-source'
    source_root.mkdir()
    archive = subprocess.check_output(['git', 'archive', BASE_COMMIT, 'src'], cwd=repo)
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(source_root, filter='data')
    database = 'tkos_a1_method_' + uuid.uuid4().hex[:16]
    assert re.fullmatch(r'tkos_a1_method_[a-f0-9]{16}', database)
    statement = sql.SQL('CREATE DATABASE {} OWNER {};').format(
        sql.Identifier(database), sql.Identifier(owner['user'])).as_string()
    socket_sql(args.container, 'postgres', statement)
    values = dict(base.values)
    for key in ('APP_DATABASE_URL', 'MIGRATION_DATABASE_URL'):
        values[key] = make_conninfo(base.values[key], dbname=database)
    values['DATABASE_URL'] = values['APP_DATABASE_URL']
    env_file = args.private / 'env.json'
    private_json(env_file, values)
    socket_sql(args.container, database, 'CREATE EXTENSION IF NOT EXISTS vector;')
    with psycopg.connect(values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}, {}').format(
            sql.Identifier(database), sql.Identifier(owner['user']), sql.Identifier(app['user'])))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(app['user'])))
    env = Environment(env_file)
    first = source_migrate(env, source_root / 'src', args.output / 'base-migrate-first.json')
    second = source_migrate(env, source_root / 'src', args.output / 'base-migrate-repeat.json')
    assert first['applied'][-1] == '0020_execution_handover.sql'
    assert second['applied'] == []
    grants(env, BASE_MUTABLE)
    result = {
        'database': database, 'base_commit': BASE_COMMIT,
        'private_environment': str(env_file), 'base_source': str(source_root / 'src'),
        'existing_databases_modified': False, 'containers_modified': False,
        'migrations': first['applied'], 'repeat_applied': second['applied'],
    }
    public_json(args.output / 'database.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', nargs='?', choices=['create', 'upgrade'], default='create')
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--container', default=CONTAINER)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2] / 'src')
    args = parser.parse_args()
    if args.mode == 'create':
        if args.private is None:
            parser.error('create requires --private')
        result = create(args)
    else:
        if args.output.exists():
            parser.error('upgrade requires a fresh output directory')
        result = upgrade(args.env_file, args.source.resolve(), args.output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
