"""Real PostgreSQL replay test for the packaged append-only migrations."""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memory_service_app import migrate as migrate_module
from memory_service_app.migrate import MIGRATIONS_DIR, migrate
from tests.conftest import DATABASE_URL

pytestmark = [pytest.mark.db, pytest.mark.owner]


def test_packaged_migrations_replay_from_empty_database() -> None:
    database = f"tkos_memory_test_{uuid.uuid4().hex[:12]}"
    admin_url = make_conninfo(
        os.environ.get("TEST_ADMIN_DATABASE_URL", DATABASE_URL), dbname="postgres"
    )
    test_url = make_conninfo(DATABASE_URL, dbname=database)
    with psycopg.connect(DATABASE_URL) as source:
        migration_owner = source.execute("SELECT current_user").fetchone()[0]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(database), sql.Identifier(migration_owner)
        ))
    try:
        # pgvector installation needs an administrator; ordinary migrations and
        # runtime traffic continue to use separate, non-superuser identities.
        with psycopg.connect(make_conninfo(admin_url, dbname=database)) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        expected = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
        assert expected == [
            "0001_init.sql",
            "0002_identity_uniq.sql",
            "0003_entity_normalized_name.sql",
            "0004_evals_sync_state.sql",
            "0005_context_graph.sql",
            "0006_working_memory.sql",
            "0007_memory_service_boundary.sql",
            # 0008-0014 are reserved for the documented governance roadmap.
            "0015_runtime_tasks.sql",
            "0016_governed_runtime.sql",
            "0017_dri_delivery.sql",
            "0018_method_protocol.sql",
            "0019_company_composition.sql",
            "0020_execution_handover.sql",
            "0021_method_foundation.sql",
            "0022_workspace_scenes.sql",
            "0023_method_lifecycle_v02.sql",
            "0024_method_v02_binding_gate.sql",
            "0025_method_anchors_v03.sql",
            "0026_workspace_sources_v02.sql",
            "0027_method_v04.sql",
            "0028_method_v04_contract_repin.sql",
            "0028_workspace_v02_grant_repair.sql",
            "0029_method_v05.sql",
            "0030_world_v01.sql",
            "0031_world_v01_contract_repin.sql",
            "0032_world_v01_revise_repin.sql",
            "0033_world_v01_state_events_repin.sql",
            "0034_world_v01_assign_owner.sql",
            "0035_world_v01_gates_repin.sql",
            "0036_world_v01_context_packs.sql",
            "0037_append_only_grant_repair.sql",
        ]
        assert migrate(test_url) == expected
        assert migrate(test_url) == []
        with psycopg.connect(test_url) as conn:
            applied = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema='public'"""
                )
            }
        assert applied == len(expected)
        assert {
            "users",
            "wm_issue_chains",
            "context_graph_versions",
            "runtime_tasks",
            "runtime_worker_heartbeats",
            "gov_objects",
            "gov_object_revisions",
            "gov_action_receipts",
            "gov_work_item_state",
            "gov_delivery_acceptances",
            "gov_outcome_assessments",
            "gov_method_profile_revisions",
            "gov_protocol_policies",
            "gov_protocol_support_registry",
            "gov_protocol_control_events",
            "gov_object_protocol_bindings",
            # migration 0019 (A2 公司组合)
            "gov_formation_round_state",
            "gov_round_formal_submissions",
            "gov_composition_confirmations",
            "gov_mission_index",
            "gov_activation_records",
            # migration 0020 (A3 执行交接)
            "gov_execution_state",
            "gov_execution_authorities",
            "gov_acceptance_appointments",
            "gov_a3_work_item_state",
            "gov_work_receipts",
            "gov_a3_delivery_acceptances",
            "gov_a3_outcome_assessments",
            "gov_method_state_keys",
            "gov_method_problem_keys",
            "gov_method_commitments",
        } <= tables
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
                (database,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))


# 0027/0030/0036 granted SELECT, INSERT on a new append-only table to every role
# that could read the source table; 0037 must take INSERT back from readers.
REPAIRED = {
    "gov_method_commitments": "gov_method_state",
    "gov_world_events": "gov_object_revisions",
    "gov_world_context_packs": "gov_object_revisions",
}


def _privileges(url: str, roles: tuple[str, ...]) -> dict[tuple[str, str], tuple[bool, bool]]:
    with psycopg.connect(url) as conn:
        return {
            (role, table): conn.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT'), has_table_privilege(%s, %s, 'INSERT')",
                (role, f"public.{table}", role, f"public.{table}"),
            ).fetchone()
            for role in roles
            for table in REPAIRED
        }


def test_append_only_tables_grant_insert_only_to_writers_of_the_source(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = f"tkos_memory_test_{uuid.uuid4().hex[:12]}"
    reader = f"tkos_test_reader_{uuid.uuid4().hex[:8]}"
    writer = f"tkos_test_writer_{uuid.uuid4().hex[:8]}"
    admin_url = make_conninfo(
        os.environ.get("TEST_ADMIN_DATABASE_URL", DATABASE_URL), dbname="postgres"
    )
    test_url = make_conninfo(DATABASE_URL, dbname=database)
    with psycopg.connect(DATABASE_URL) as source:
        migration_owner = source.execute("SELECT current_user").fetchone()[0]
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(database), sql.Identifier(migration_owner)
        ))
        for role in (reader, writer):
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    try:
        with psycopg.connect(make_conninfo(admin_url, dbname=database)) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        # Both roles exist before the migrations run and receive table grants the
        # way a reporting role and the runtime role would: one reads, one writes.
        with psycopg.connect(test_url) as conn:
            conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}")
                         .format(sql.Identifier(reader)))
            conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT ON TABLES TO {}")
                         .format(sql.Identifier(writer)))

        before_repair = tmp_path / "migrations"
        before_repair.mkdir()
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name < "0037":
                (before_repair / path.name).write_bytes(path.read_bytes())
        monkeypatch.setattr(migrate_module, "MIGRATIONS_DIR", before_repair)
        migrate(test_url)
        widened = _privileges(test_url, (reader, writer))
        assert all(widened[(reader, table)] == (True, True) for table in REPAIRED)

        monkeypatch.undo()
        assert migrate(test_url) == ["0037_append_only_grant_repair.sql"]
        repaired = _privileges(test_url, (reader, writer))
        assert {key: value for key, value in repaired.items() if key[0] == reader} == {
            (reader, table): (True, False) for table in REPAIRED
        }
        assert {key: value for key, value in repaired.items() if key[0] == writer} == {
            (writer, table): (True, True) for table in REPAIRED
        }

        # Idempotent: running the repair again changes nothing.
        with psycopg.connect(test_url) as conn:
            conn.execute((MIGRATIONS_DIR / "0037_append_only_grant_repair.sql").read_text(encoding="utf-8"))
        assert _privileges(test_url, (reader, writer)) == repaired

        # Writers stay fenced to their scope: row security is forced and every
        # repaired table checks the scope on insert.
        with psycopg.connect(test_url) as conn:
            fenced = conn.execute(
                """SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity,
                          bool_or(p.with_check LIKE '%%gov_scope_matches(scope_id)%%')
                     FROM pg_class c JOIN pg_policies p ON p.tablename = c.relname
                    WHERE c.relname = ANY(%s) GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity""",
                (list(REPAIRED),),
            ).fetchall()
        assert sorted(fenced) == [(table, True, True) for table in sorted(REPAIRED)]
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
                (database,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
            for role in (reader, writer):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


# 迁移器的校验和、互斥锁与编号约束（#32 批次 E）。用临时目录里的迁移副本，不动真实文件。

def _fresh_database(admin_url: str, owner: str) -> str:
    database = f"tkos_memory_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(owner)))
    with psycopg.connect(make_conninfo(admin_url, dbname=database)) as admin:
        admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return database


def _drop(admin_url: str, database: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s", (database,))
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """一份迁移副本和一个空库；测试可以改副本，看迁移器怎么反应。"""
    admin_url = make_conninfo(os.environ.get("TEST_ADMIN_DATABASE_URL", DATABASE_URL), dbname="postgres")
    with psycopg.connect(DATABASE_URL) as source:
        owner = source.execute("SELECT current_user").fetchone()[0]
    copy = tmp_path / "migrations"
    copy.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        (copy / path.name).write_bytes(path.read_bytes())
    monkeypatch.setattr(migrate_module, "MIGRATIONS_DIR", copy)
    database = _fresh_database(admin_url, owner)
    try:
        yield copy, make_conninfo(DATABASE_URL, dbname=database)
    finally:
        _drop(admin_url, database)


def test_every_applied_migration_records_the_sha256_of_its_file(scratch):
    copy, url = scratch
    migrate(url)
    with psycopg.connect(url) as conn:
        recorded = dict(conn.execute("SELECT name, sha256 FROM schema_migrations").fetchall())
    import hashlib
    assert recorded == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in copy.glob("*.sql")}


def test_an_applied_migration_whose_file_changed_stops_the_run(scratch):
    copy, url = scratch
    migrate(url)
    changed = copy / "0030_world_v01.sql"
    changed.write_text(changed.read_text(encoding="utf-8") + "\n-- edited after it was applied\n", encoding="utf-8")
    (copy / "9999_after_the_edit.sql").write_text("CREATE TABLE after_the_edit(x int);\n", encoding="utf-8")
    with pytest.raises(migrate_module.MigrationError, match="0030_world_v01.sql"):
        migrate(url)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('after_the_edit')").fetchone()[0] is None


def test_a_new_duplicate_number_is_refused_before_anything_is_applied(scratch):
    copy, url = scratch
    (copy / "0035_duplicate_number.sql").write_text("CREATE TABLE duplicate_number(x int);\n", encoding="utf-8")
    with pytest.raises(migrate_module.MigrationError, match="0035"):
        migrate(url)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None


def test_concurrent_runs_apply_each_migration_once(scratch):
    copy, url = scratch
    import threading
    results, errors = [], []

    def run():
        try:
            results.append(migrate(url))
        except Exception as exc:  # noqa: BLE001 — the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    assert errors == []
    assert sorted(len(applied) for applied in results) == [0, 0, len(list(copy.glob("*.sql")))]


def test_rows_recorded_by_the_old_runner_get_their_sha256_backfilled(scratch):
    copy, url = scratch
    migrate(url)
    with psycopg.connect(url) as conn:
        conn.execute("ALTER TABLE schema_migrations DROP COLUMN sha256")
    assert migrate(url) == []
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations WHERE sha256 IS NULL").fetchone()[0] == 0
