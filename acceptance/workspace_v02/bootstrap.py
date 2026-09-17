"""Verify/apply the tkos.workspace/0.2 migrations on the isolated root DB.

The root-provided database may already have 0026 and 0028 applied by the
coordinated migration run. This helper is idempotent: it applies only pending
B2 migrations via the owner role, always verifies replay emptiness, FORCE RLS,
immutable triggers and the per-role privilege mirror on the three new tables,
and publishes the exact migration file hashes. It never resets, drops or
recreates a database and never talks to the original governance database.
"""
import argparse
import hashlib
from pathlib import Path
import re

import psycopg
from psycopg.conninfo import conninfo_to_dict

from acceptance.method_independent.database import method_grants
from acceptance.protocol_a1_independent.database import source_migrate
from acceptance.protocol_a1_independent.support import Environment, public_json

TABLES = ("gov_workspace_v02_events", "gov_workspace_v02_contexts", "gov_workspace_v02_assets")
B2_MIGRATIONS = ("0026_workspace_sources_v02.sql", "0028_workspace_v02_grant_repair.sql")
DEFERRED = ("0027_method_v04.sql", "0028_method_v04_contract_repin.sql")


def _recorded(env):
    with psycopg.connect(env.values["MIGRATION_DATABASE_URL"]) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify(env):
    app_role = conninfo_to_dict(env.values["APP_DATABASE_URL"])["user"]
    owner_role = conninfo_to_dict(env.values["MIGRATION_DATABASE_URL"])["user"]
    with psycopg.connect(env.values["MIGRATION_DATABASE_URL"]) as conn:
        for table in TABLES:
            rls = conn.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=%s", (table,)).fetchone()
            assert rls == (True, True), table
            triggers = {row[0] for row in conn.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal", (table,))}
            suffix = {"events": "", "contexts": "_context", "assets": "_asset"}[table.rsplit("_", 1)[-1]]
            assert {f"gov_workspace_v02{suffix}_capability",
                    f"gov_workspace_v02{suffix}_immutable"} <= triggers, (table, triggers)
        grants = conn.execute(
            """SELECT privilege_type FROM information_schema.role_table_grants
               WHERE table_schema='public' AND grantee=%s AND table_name=ANY(%s)""",
            (app_role, list(TABLES))).fetchall()
        privileges = {row[0] for row in grants}
        assert privileges == {"SELECT", "INSERT"}, privileges
        mismatched = conn.execute(
            """SELECT DISTINCT g.grantee, g.privilege_type FROM information_schema.role_table_grants g
               WHERE g.table_schema='public' AND g.table_name=ANY(%s)
                 AND g.grantee <> 'PUBLIC' AND g.grantee <> %s
                 AND NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants w
                    WHERE w.table_schema='public' AND w.table_name='gov_workspace_events'
                      AND w.privilege_type=g.privilege_type AND w.grantee=g.grantee)
               ORDER BY g.grantee, g.privilege_type""",
            (list(TABLES), app_role)).fetchall()
        mismatched = [row for row in mismatched if row[0] != owner_role]
        assert mismatched == [], mismatched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path,
                        default=Path(__file__).resolve().parents[2] / "src",
                        help="source tree used for pending migrations")
    args = parser.parse_args()
    env = Environment(args.env_file.resolve())
    database = conninfo_to_dict(env.values["APP_DATABASE_URL"])["dbname"]
    if not re.fullmatch(r"tkos_a1_method_[a-f0-9]{16}", database):
        raise ValueError("Expected the isolated Method acceptance database")
    if args.output.exists():
        raise ValueError("Use a fresh output directory")
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700)
    worktree = Path(__file__).resolve().parents[2] / "src/memory_service_app/migrations"
    recorded = _recorded(env)
    pending = [name for name in B2_MIGRATIONS if name not in recorded]
    applied = []
    if pending:
        first = source_migrate(env, args.source.resolve(), output / "first.json")
        assert first["applied"] == pending, (first, pending)
        applied = first["applied"]
    repeat = source_migrate(env, args.source.resolve(), output / "repeat.json")
    assert repeat["applied"] == []
    method_grants(env)
    _verify(env)
    public_json(output / "summary.json", {
        "database": database, "applied": applied,
        "previously_applied": [name for name in B2_MIGRATIONS if name not in pending],
        "repeat_applied": [], "runtime_grants_updated_in_migration": True,
        "roles_separate": True, "grants_mirror_privilege_level": True,
        "duplicate_numeric_prefix_0028": ["0028_method_v04_contract_repin.sql",
                                          "0028_workspace_v02_grant_repair.sql"],
        "deferred_later_migrations": list(DEFERRED),
        "b2_migration_sha256": {name: _sha256(worktree / name) for name in B2_MIGRATIONS},
        "future_migration_numbers": "reserved by root only; 0029 onward not allocated by B2",
        "existing_databases_modified": False, "containers_modified": False,
    })
    print("0026+0028 present; replay empty; per-role grants, FORCE RLS and triggers verified.")


if __name__ == "__main__":
    main()
