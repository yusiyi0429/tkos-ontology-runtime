"""Real PostgreSQL replay test for the packaged append-only migrations."""
from __future__ import annotations

import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from memory_service_app.migrate import MIGRATIONS_DIR, migrate
from tests.conftest import DATABASE_URL


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
        } <= tables
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
                (database,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
