"""world 0.1 验收的源码钉定与环境门槛。

- checkout：从钉定的提交取出 src 与 world 契约文件；工作区里的验收代码（整个 acceptance/，含共享助手）与契约
  文件必须与该提交一致。
- frozen_files：冻结检查点列出的文件及其 SHA256。
- privileges、immutable_history、no_external_effects：三个环境门槛的观察。只读，唯一的写是不可变探针，
  全部回滚并核对库快照不变。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.composition_a2_independent.storage import immutable_owner_probe

from .database import BASE_COMMIT, extract

ROOT = Path(__file__).resolve().parents[2]
HARNESS = 'acceptance'  # world 验收还导入 method_independent、protocol_a1_independent 等目录里的共享助手
DOCS = ('docs/contracts/tkos-world-0.1.md', 'docs/contracts/world-registry-0.1.json',
        'docs/contracts/world-profile-0.1.json', 'docs/runtime-world-support-0.1.json')
WORLD_TABLES = {'gov_world_events', 'gov_world_context_packs'}


def _git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def drift(sha: str) -> list[str]:
    """工作区里与该提交不一致的验收代码与 world 契约文件（改过的与未跟踪的）。"""
    return (_git('diff', '--name-only', sha, '--', HARNESS, *DOCS).split()
            + _git('ls-files', '--others', '--exclude-standard', '--', HARNESS, *DOCS).split())


def checkout(commit: str, private: Path) -> tuple[Path, str]:
    """取出钉定提交的 src 与 world 契约文件，返回取出的根目录与完整提交号。验收代码与它读的契约文件在工作区
    里跑，必须与该提交逐字节一致，否则拒绝开跑；运行结束时 main 再核对一次。"""
    sha = _git('rev-parse', '--verify', commit + '^{commit}')
    if drift(sha):
        raise ValueError('the acceptance code or world contract files differ from the pinned commit: '
                         + ', '.join(drift(sha)))
    root = private / 'checkout'
    root.mkdir()
    extract(sha, root, ('src', *DOCS))
    return root, sha


def frozen_files(root: Path, sha: str) -> dict[str, str]:
    """冻结检查点的文件：相对基线改动过、在该提交里仍存在的 src/ 文件，加上 world 契约、登记、profile 与支持登记。"""
    changed = _git('diff', '--name-only', '--diff-filter=d', BASE_COMMIT, sha, '--', 'src').splitlines()
    return {path: _sha256(root / path) for path in sorted({*changed, *DOCS})}


def scopes(h) -> int:
    """库里已有的 scope 数（owner 经控制面可见全部 scope）；新建的验收库在播种前为 0。"""
    with psycopg.connect(h.env.values['MIGRATION_DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        return conn.execute('SELECT count(*) AS n FROM gov_scopes').fetchone()['n']


def privileges(h) -> dict:
    """应用库角色是普通身份：用自己的登录、不是超级用户、不能绕过 RLS、不能建库建角色；gov_ 表都开启并强制 RLS、
    不归它所有、可查询、不给 DELETE；world 的事件表与上下文包表只查询与追加。"""
    with h.app_connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        identity = conn.execute('''SELECT current_user = session_user AS own_login, rolsuper, rolbypassrls,
                                          rolcreatedb, rolcreaterole
                                     FROM pg_roles WHERE rolname=current_user''').fetchone()
        tables = conn.execute('''SELECT c.relname AS name, c.relrowsecurity, c.relforcerowsecurity,
                                        pg_get_userbyid(c.relowner)=current_user AS app_owns,
                                        has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
                                        has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
                                        has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
                                        has_table_privilege(current_user,c.oid,'DELETE') AS can_delete
                                   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                                  WHERE n.nspname='public' AND c.relkind='r' AND left(c.relname,4)='gov_'
                                  ORDER BY c.relname''').fetchall()
    assert identity['own_login'] and not any(identity[flag] for flag in
                                             ('rolsuper', 'rolbypassrls', 'rolcreatedb', 'rolcreaterole')), identity
    world = {row['name']: row for row in tables if row['name'] in WORLD_TABLES}
    assert set(world) == WORLD_TABLES
    for row in tables:
        assert not row['app_owns'] and row['relrowsecurity'] and row['relforcerowsecurity'], row['name']
        assert row['can_select'] and not row['can_delete'], row['name']
    for row in world.values():
        assert row['can_insert'] and not row['can_update'], row['name']
    return {'identity': dict(identity), 'gov_tables': len(tables), 'world_tables': sorted(world)}


def _append_without_capability(h, f, row: dict) -> dict:
    """应用连接不声明写能力，照一条现有事件追加一条新事件：写能力触发器必须在任何约束之前拒绝（55000）。"""
    before = h.snapshot(f)
    values = {key: Jsonb(value) if isinstance(value, (dict, list)) else value
              for key, value in {**row, 'event_id': uuid.uuid4()}.items()}
    statement = sql.SQL('INSERT INTO gov_world_events ({}) VALUES ({})').format(
        sql.SQL(',').join(map(sql.Identifier, values)), sql.SQL(',').join(sql.Placeholder() * len(values)))
    failure = None
    conn = psycopg.connect(h.env.values['APP_DATABASE_URL'])
    try:
        conn.execute("SELECT set_config('app.runtime_write_capability','',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f['scope_id'],))
        try:
            conn.execute(statement, list(values.values()))
        except psycopg.Error as error:
            failure = error.sqlstate
        assert failure == '55000', 'an append without the write capability was not refused by its trigger'
    finally:
        conn.rollback()
        conn.close()
    assert h.snapshot(f) == before
    return {'table': 'gov_world_events', 'append_without_capability_rejected': True, 'sqlstate': failure,
            'all_state_unchanged': True}


def immutable_history(h, f) -> list[dict]:
    """world 事件与上下文包只追加：owner 打开控制面也改不了已有的行，没声明写能力的应用连接追加不进去。"""
    event = h.sql(f, 'SELECT * FROM gov_world_events WHERE scope_id=%s ORDER BY recorded_at, event_id LIMIT 1',
                  (f['scope_id'],))[0]
    pack = h.sql(f, 'SELECT context_pack_id FROM gov_world_context_packs WHERE scope_id=%s LIMIT 1', (f['scope_id'],))[0]
    return [
        immutable_owner_probe(h, f, table='gov_world_events', column='kind', key_column='event_id',
                              key=str(event['event_id'])),
        immutable_owner_probe(h, f, table='gov_world_context_packs', column='question', key_column='context_pack_id',
                              key=str(pack['context_pack_id'])),
        _append_without_capability(h, f, event),
    ]


def no_external_effects(h, f) -> dict:
    """world 动作不派发外部效果：本 scope 的回执都不带效果任务，任务队列里也没有这家公司的任务。"""
    receipts = h.sql(f, "SELECT count(*) AS n, count(*) FILTER (WHERE effect_task_ids <> '[]'::jsonb) AS with_effects"
                        ' FROM gov_action_receipts WHERE scope_id=%s', (f['scope_id'],))[0]
    tasks = h.sql(f, 'SELECT count(*) AS n FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s',
                  (f['tenant_id'], f['company_id']))[0]['n']
    assert receipts['n'] > 0 and receipts['with_effects'] == 0 and tasks == 0, (receipts, tasks)
    return {'receipts': receipts['n'], 'receipts_with_effects': 0, 'runtime_tasks': 0}
