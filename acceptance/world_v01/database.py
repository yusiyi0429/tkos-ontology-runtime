"""为 world 独立验收新建隔离库：先迁移到含 0.5 的基线，再单独应用 world 迁移。

- create：在隔离验收容器里新建 tkos_a1_world_* 库，用基线提交（origin/main ef31b02，含 0.5）
  的源码迁移到 0029，重复迁移为空；
- upgrade：用给定源码迁移（--commit 时用该提交 git archive 出的源码），必须恰好应用 0029 之后的
  全部迁移（world 迁移与其后的契约重钉迁移），重复迁移为空；记下每个迁移文件的 SHA256 供验收
  核对；核对迁移已授予应用角色事件表的查询与追加权限，再授予应用角色运行时表权限。

导入不做任何事；不打印凭据；不改动已有数据库，不改动容器生命周期。
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from acceptance.execution_a3_independent.database import socket_sql
from acceptance.method_independent.database import CONTAINER, method_grants
from acceptance.method_v05.database import same_instance
from acceptance.protocol_a1_independent.database import source_migrate
from acceptance.protocol_a1_independent.support import Environment, private_json, public_json

ROOT = Path(__file__).resolve().parents[2]
BASE_COMMIT = 'ef31b02'
BASELINE = '0029_method_v05.sql'
DATABASE = re.compile(r'tkos_a1_world_[a-f0-9]{16}')


def world_migrations(source: Path) -> dict[str, str]:
    """源码里基线之后的全部迁移及其 SHA256，按文件名排序（即应用顺序）。"""
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((source / 'memory_service_app/migrations').glob('*.sql')) if path.name > BASELINE}


def extract(commit: str, target: Path, paths: tuple[str, ...] = ('src',)) -> str:
    """把提交里的这些路径（默认 src）取到 target 下，返回完整提交号。"""
    sha = subprocess.check_output(['git', 'rev-parse', '--verify', commit + '^{commit}'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', sha, *paths], cwd=ROOT)
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(target, filter='data')
    return sha


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
    extract(BASE_COMMIT, source_root)
    database = 'tkos_a1_world_' + uuid.uuid4().hex[:16]
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
    assert first['applied'][-1] == BASELINE and second['applied'] == [], (first['applied'][-1:], second)
    method_grants(env)
    result = {'database': database, 'base_commit': BASE_COMMIT, 'private_environment': str(env_path),
              'baseline_last_migration': first['applied'][-1], 'repeat_applied': second['applied'],
              'existing_databases_modified': False, 'containers_modified': False}
    public_json(output / 'database.json', result)
    return result


def upgrade(env_file: Path, source: Path, output: Path, commit: str | None = None) -> dict:
    if output.exists():
        raise ValueError('upgrade requires a fresh output directory')
    env = Environment(env_file)
    database = conninfo_to_dict(env.values['MIGRATION_DATABASE_URL'])['dbname']
    if not DATABASE.fullmatch(database):
        raise ValueError('upgrade requires an explicitly generated world acceptance database')
    same_instance(env)
    migrations = world_migrations(source)
    expected = list(migrations)
    first = source_migrate(env, source, output / 'world-migrate-first.json')
    second = source_migrate(env, source, output / 'world-migrate-repeat.json')
    assert first['applied'] == expected, (first['applied'], expected)
    assert second['applied'] == []
    app = conninfo_to_dict(env.values['APP_DATABASE_URL'])['user']
    with psycopg.connect(env.values['MIGRATION_DATABASE_URL']) as conn:
        privileges = conn.execute(
            "SELECT has_table_privilege(%s,'gov_world_events','SELECT'), has_table_privilege(%s,'gov_world_events','INSERT'),"
            " has_table_privilege(%s,'gov_world_events','UPDATE'), has_table_privilege(%s,'gov_world_events','DELETE')",
            (app, app, app, app)).fetchone()
    assert tuple(privileges) == (True, True, False, False), privileges
    method_grants(env)
    result = {'database': database, 'source': f'git archive {commit}' if commit else str(source), 'commit': commit,
              'applied': first['applied'],
              'migration_sha256': migrations,
              'migration_granted_world_events': {'select': True, 'insert': True, 'update': False, 'delete': False},
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
    parser.add_argument('--commit', help='upgrade 用该提交的 src（冻结验收用），不看 --source')
    args = parser.parse_args()
    if args.mode == 'create':
        if args.private is None or args.commit:
            parser.error('create requires --private and always uses the baseline commit')
        result = create(args.env_file, args.private, args.output)
    elif args.commit:
        with tempfile.TemporaryDirectory() as checkout:
            sha = extract(args.commit, Path(checkout))
            result = upgrade(args.env_file, Path(checkout) / 'src', args.output, sha)
    else:
        result = upgrade(args.env_file, args.source.resolve(), args.output)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
