"""Explicit root-invoked one-off acceptance database creation/upgrade.

No automatic container lifecycle, existing database reset, database drop or
credential creation. All credentials remain in root-provided private files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from .support import Environment, HERE, private_json, public_json, source_manifest


LEGACY_MUTABLE = frozenset({"gov_scopes", "gov_principals", "gov_credentials", "gov_role_assignments",
                            "gov_objects", "gov_feedback_state", "gov_work_item_state"})


def source_migrate(environment: Environment, source: Path, output: Path) -> dict:
    completed = subprocess.run([sys.executable, "-I", str(HERE / "source_process.py"), "migrate",
        "--source", str(source)], env=environment.child(owner=True), text=True, capture_output=True, timeout=60)
    public_json(output, {"exit_code": completed.returncode,
        "stdout": environment.redact(completed.stdout), "stderr": environment.redact(completed.stderr)})
    if completed.returncode:
        raise AssertionError("controlled source migration failed; inspect redacted artifact")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def legacy_grants(environment: Environment) -> None:
    app = conninfo_to_dict(environment.values["APP_DATABASE_URL"])["user"]
    with psycopg.connect(environment.values["MIGRATION_DATABASE_URL"]) as conn:
        applied = [row[0] for row in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
        if not applied or applied[-1] != "0017_dri_delivery.sql":
            raise AssertionError("legacy grant setup is allowed only before A1 migration")
        tables = [row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
        for table in tables:
            if table.startswith("gov_"):
                privileges = "SELECT, INSERT, UPDATE" if table in LEGACY_MUTABLE else "SELECT, INSERT"
            elif table == "schema_migrations":
                privileges = "SELECT"
            else:
                privileges = "SELECT, INSERT, UPDATE, DELETE"
            conn.execute(sql.SQL("GRANT " + privileges + " ON TABLE public.{} TO {}").format(
                sql.Identifier(table), sql.Identifier(app)))
        conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(app)))


def create(args) -> dict:
    base = Environment(args.env_file)
    admin_url = os.environ.get("TEST_ADMIN_DATABASE_URL", "")
    if not admin_url:
        raise ValueError("root must inject TEST_ADMIN_DATABASE_URL only for this child")
    admin = conninfo_to_dict(admin_url)
    owner = conninfo_to_dict(base.values["MIGRATION_DATABASE_URL"])
    app = conninfo_to_dict(base.values["APP_DATABASE_URL"])
    if admin.get("host") != owner.get("host") or admin.get("port", "5432") != owner.get("port", "5432"):
        raise ValueError("admin and acceptance PostgreSQL endpoints differ")
    if args.out_env.exists():
        raise ValueError("refusing to overwrite existing private one-off database environment")
    name = "tkos_a1_" + uuid.uuid4().hex[:16]
    # Name is generated; this command cannot select/reset an existing database.
    with psycopg.connect(make_conninfo(admin_url, dbname="postgres"), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(owner["user"])))
    values = dict(base.values)
    values["MIGRATION_DATABASE_URL"] = make_conninfo(base.values["MIGRATION_DATABASE_URL"], dbname=name)
    values["APP_DATABASE_URL"] = make_conninfo(base.values["APP_DATABASE_URL"], dbname=name)
    values["DATABASE_URL"] = values["APP_DATABASE_URL"]
    private_json(args.out_env, values)
    # Failure retains the new DB and private mapping, never silently drops data.
    with psycopg.connect(make_conninfo(admin_url, dbname=name)) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    with psycopg.connect(values["MIGRATION_DATABASE_URL"]) as conn:
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
            sql.Identifier(name), sql.Identifier(owner["user"]), sql.Identifier(app["user"])))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app["user"])))
    environment = Environment(args.out_env)
    args.output.mkdir(parents=True, exist_ok=True)
    migrated = source_migrate(environment, args.source, args.output / "old-migration.json")
    assert migrated["applied"][-1] == "0017_dri_delivery.sql", "one-off history database must start pre-A1"
    legacy_grants(environment)
    result = {"created_database": name, "private_env_file": str(args.out_env),
              "old_migrations": migrated["applied"], "existing_database_modified": False,
              "database_dropped": False}
    public_json(args.output / "one-off-database.json", result)
    return result


def upgrade(args) -> dict:
    environment = Environment(args.env_file)
    dbname = conninfo_to_dict(environment.values["MIGRATION_DATABASE_URL"])["dbname"]
    if not dbname.startswith("tkos_a1_"):
        raise ValueError("upgrade helper only accepts its generated one-off databases")
    args.output.mkdir(parents=True, exist_ok=True)
    start = source_manifest(args.source)
    first = source_migrate(environment, args.source, args.output / "a1-migration-first.json")
    second = source_migrate(environment, args.source, args.output / "a1-migration-second.json")
    assert first["applied"] == ["0018_method_protocol.sql"], "expected only the A1 migration"
    assert second["applied"] == [], "migration replay was not idempotent"
    end = source_manifest(args.source)
    assert start == end, "source changed while applying the acceptance migration"
    result = {"database": dbname, "first": first, "second": second,
              "source_manifest_start": start, "source_manifest_end": end, "source_unchanged": True,
              "migration_replay_passed": True, "history_verified": False,
              "contract_a1_accepted": False}
    public_json(args.output / "migration-replay.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "upgrade"))
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--out-env", type=Path)
    args = parser.parse_args()
    if args.mode == "create" and not args.out_env:
        parser.error("create requires --out-env")
    print(json.dumps(create(args) if args.mode == "create" else upgrade(args)))


if __name__ == "__main__":
    main()
