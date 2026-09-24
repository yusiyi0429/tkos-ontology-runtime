"""Remote production grants the application role the release's gov_* privileges.

deploy/remote-production/db_admin.py is the only file its db-admin container
mounts, so it keeps its own copy of the release grant step's table classes
(deploy/offline-release/db_admin.py, expected_privileges). Both scripts must
give every gov_* table the migrations create, the runtime tables and
schema_migrations the same privileges. Legacy non-gov tables intentionally stay
SELECT-only in remote production. No database is needed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re

from memory_service_app.migrate import MIGRATIONS_DIR


DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REMOTE = load("remote_db_admin", DEPLOY / "remote-production" / "db_admin.py")
RELEASE = load("release_db_admin", DEPLOY / "offline-release" / "db_admin.py")
MIGRATED_TABLES = set(re.findall(
    r"CREATE TABLE (?:IF NOT EXISTS )?([a-z][a-z0-9_]*)",
    "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS_DIR.glob("*.sql")),
))


def test_remote_production_gives_every_gov_table_the_release_privileges() -> None:
    tables = {table for table in MIGRATED_TABLES if table.startswith("gov_")} | {"schema_migrations"}
    for module in (REMOTE, RELEASE):
        tables |= module.MUTABLE_GOV_TABLES | module.CONTROL_PLANE_TABLES | module.RUNTIME_TABLES
    diverged = {
        table: {"remote": REMOTE.expected_privileges(table), "release": RELEASE.expected_privileges(table)}
        for table in sorted(tables)
        if REMOTE.expected_privileges(table) != RELEASE.expected_privileges(table)
    }
    assert diverged == {}


def test_remote_production_keeps_legacy_tables_select_only_and_never_grants_delete_or_truncate() -> None:
    legacy = {table for table in MIGRATED_TABLES if not table.startswith("gov_")} - REMOTE.RUNTIME_TABLES
    assert legacy
    assert {table for table in legacy if REMOTE.expected_privileges(table) != {"SELECT"}} == set()
    assert {table for table in MIGRATED_TABLES | {"schema_migrations"}
            if REMOTE.expected_privileges(table) & {"DELETE", "TRUNCATE"}} == set()
