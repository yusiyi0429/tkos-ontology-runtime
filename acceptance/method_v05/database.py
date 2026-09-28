"""为 0.5 独立验收新建隔离的 Method 数据库，并升级到给定源码（HEAD）。

步骤与已删除的 acceptance/method_independent/database.py create / upgrade 相同，只有两处不同：

- 端点不写死：主机与端口取自基础 env，但必须正是隔离验收容器（compose 项目
  tkos-ontology-runtime-acceptance）当前发布的回环端口——建库走该容器的本地 socket，
  连接走 env，二者必须是同一个实例；
- upgrade 必须恰好应用给定源码中 0020 之后的全部迁移，重复执行不再应用任何迁移。

导入不做任何事；不打印凭据；不重置、删除或迁移已有数据库，不改动容器生命周期。
失败时保留新建的库与私有 env 供核查。
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
from acceptance.method_independent.database import BASE_COMMIT, CONTAINER, method_grants
from acceptance.protocol_a1_independent.database import source_migrate
from acceptance.protocol_a1_independent.support import Environment, private_json, public_json

ROOT = Path(__file__).resolve().parents[2]
PROJECT = 'tkos-ontology-runtime-acceptance'
BASELINE = '0020_execution_handover.sql'
DATABASE = re.compile(r'tkos_a1_method_[a-f0-9]{16}')


def same_instance(env: Environment) -> None:
    """env 的 owner 端点必须就是 CONTAINER 当前发布的回环端口，且 CONTAINER 属于隔离验收项目。"""
    owner = conninfo_to_dict(env.values['MIGRATION_DATABASE_URL'])
    label = subprocess.check_output(
        ['docker', 'inspect', CONTAINER, '--format', '{{index .Config.Labels "com.docker.compose.project"}}'],
        text=True).strip()
    if label != PROJECT:
        raise ValueError('the acceptance PostgreSQL container is not part of the isolated acceptance project')
    published = subprocess.check_output(['docker', 'port', CONTAINER, '5432'], text=True).split()
    if published != [f"{owner.get('host')}:{owner.get('port')}"]:
        raise ValueError('the env does not reach the running isolated acceptance PostgreSQL')
    if subprocess.check_output(['docker', 'context', 'show'], text=True).strip() != 'desktop-linux':
        raise ValueError('unexpected Docker context')


def create(env_file: Path, private: Path, output: Path) -> dict:
    if private.exists() or output.exists():
        raise ValueError('fresh private and public output paths are required')
    base = Environment(env_file)
    same_instance(base)
    owner = conninfo_to_dict(base.values['MIGRATION_DATABASE_URL'])
    app = conninfo_to_dict(base.values['APP_DATABASE_URL'])
    private.mkdir(parents=True, mode=0o700)
    source_root = private / 'base-source'
    source_root.mkdir()
    archive = subprocess.check_output(['git', 'archive', BASE_COMMIT, 'src'], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(source_root, filter='data')
    database = 'tkos_a1_method_' + uuid.uuid4().hex[:16]
    assert DATABASE.fullmatch(database)
    socket_sql(CONTAINER, 'postgres', sql.SQL('CREATE DATABASE {} OWNER {};').format(
        sql.Identifier(database), sql.Identifier(owner['user'])).as_string())
    values = dict(base.values)
    for key in ('APP_DATABASE_URL', 'MIGRATION_DATABASE_URL'):
        values[key] = make_conninfo(base.values[key], dbname=database)
    values['DATABASE_URL'] = values['APP_DATABASE_URL']
    env_path = private / 'env.json'
    private_json(env_path, values)
    socket_sql(CONTAINER, database, 'CREATE EXTENSION IF NOT EXISTS vector;')
    with psycopg.connect(values['MIGRATION_DATABASE_URL']) as conn:
        conn.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        conn.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}, {}').format(
            sql.Identifier(database), sql.Identifier(owner['user']), sql.Identifier(app['user'])))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(app['user'])))
    env = Environment(env_path)
    first = source_migrate(env, source_root / 'src', output / 'base-migrate-first.json')
    second = source_migrate(env, source_root / 'src', output / 'base-migrate-repeat.json')
    assert first['applied'][-1] == BASELINE and second['applied'] == []
    grants(env)
    result = {'database': database, 'base_commit': BASE_COMMIT, 'private_environment': str(env_path),
              'base_source': str(source_root / 'src'), 'migrations': first['applied'],
              'repeat_applied': second['applied'], 'existing_databases_modified': False,
              'containers_modified': False}
    public_json(output / 'database.json', result)
    return result


def upgrade(env_file: Path, source: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError('upgrade requires a fresh output directory')
    env = Environment(env_file)
    database = conninfo_to_dict(env.values['MIGRATION_DATABASE_URL'])['dbname']
    if not DATABASE.fullmatch(database):
        raise ValueError('upgrade requires an explicitly generated Method acceptance database')
    same_instance(env)
    expected = sorted(path.name for path in (source / 'memory_service_app/migrations').glob('*.sql')
                      if path.name > BASELINE)
    first = source_migrate(env, source, output / 'method-migrate-first.json')
    second = source_migrate(env, source, output / 'method-migrate-repeat.json')
    assert first['applied'] == expected, (first['applied'], expected)
    assert second['applied'] == []
    method_grants(env)
    result = {'database': database, 'source': str(source), 'applied': first['applied'],
              'repeat_applied': second['applied'], 'existing_databases_modified': False,
              'containers_modified': False}
    public_json(output / 'upgrade.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['create', 'upgrade'])
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'src')
    args = parser.parse_args()
    if args.mode == 'create':
        if args.private is None:
            parser.error('create requires --private')
        result = create(args.env_file, args.private, args.output)
    else:
        result = upgrade(args.env_file, args.source.resolve(), args.output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
