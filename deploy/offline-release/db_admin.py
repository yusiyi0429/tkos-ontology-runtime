#!/usr/bin/env python3
"""Create least-privilege database roles and grant Runtime table access."""
from __future__ import annotations

import argparse
import json
import os
import re

import psycopg
from psycopg import sql


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MUTABLE_GOV_TABLES = {
    "gov_scopes", "gov_principals", "gov_credentials", "gov_role_assignments",
    "gov_objects", "gov_feedback_state", "gov_work_item_state",
    "gov_formation_round_state", "gov_round_formal_submissions",
    "gov_execution_state", "gov_a3_work_item_state", "gov_method_state",
    "gov_method_strategy_heads", "gov_method_runs",
}
CONTROL_PLANE_TABLES = {
    "gov_method_profile_revisions", "gov_protocol_policies",
    "gov_protocol_support_registry", "gov_protocol_control_events",
    "gov_method_agent_bindings",
}
RUNTIME_TABLES = {"runtime_tasks", "runtime_worker_heartbeats"}


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"MISSING_ENV:{name}")
    return value


def identifier(name: str) -> str:
    value = required(name)
    if not IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"INVALID_IDENTIFIER:{name}")
    return value


def expected_privileges(table: str) -> set[str]:
    if table == "schema_migrations" or table in CONTROL_PLANE_TABLES:
        return {"SELECT"}
    if table in RUNTIME_TABLES or table in MUTABLE_GOV_TABLES:
        return {"SELECT", "INSERT", "UPDATE"}
    if table.startswith("gov_"):
        return {"SELECT", "INSERT"}
    return {"SELECT", "INSERT", "UPDATE", "DELETE"}


def prepare() -> dict:
    database = identifier("POSTGRES_DB")
    owner = identifier("POSTGRES_OWNER_USER")
    app = identifier("POSTGRES_APP_USER")
    passwords = {
        owner: required("POSTGRES_OWNER_PASSWORD"),
        app: required("POSTGRES_APP_PASSWORD"),
    }
    with psycopg.connect(required("POSTGRES_ADMIN_URL"), autocommit=True) as conn:
        for role, password in passwords.items():
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(
                verb + " ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
            sql.Identifier(database), sql.Identifier(owner)))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
            sql.Identifier(database)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
            sql.Identifier(database), sql.Identifier(owner), sql.Identifier(app)))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
            sql.Identifier(owner)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
            sql.Identifier(app)))
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return {"ok": True, "operation": "prepare", "roles": 2, "extension": "vector"}


def grants() -> dict:
    app = identifier("POSTGRES_APP_USER")
    counts = {"control_read_only": 0, "governed_mutable": 0,
              "governed_append_only": 0, "runtime_mutable": 0, "legacy_mutable": 0}
    with psycopg.connect(required("POSTGRES_ADMIN_URL")) as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()]
        if "schema_migrations" not in tables or not any(t.startswith("gov_") for t in tables):
            raise RuntimeError("MIGRATIONS_NOT_APPLIED")
        for table in tables:
            relation = sql.Identifier("public", table)
            conn.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                relation, sql.Identifier(app)))
            privileges = expected_privileges(table)
            conn.execute(sql.SQL("GRANT " + ", ".join(sorted(privileges)) + " ON TABLE {} TO {}").format(
                relation, sql.Identifier(app)))
            if table in CONTROL_PLANE_TABLES or table == "schema_migrations":
                counts["control_read_only"] += 1
            elif table in RUNTIME_TABLES:
                counts["runtime_mutable"] += 1
            elif table in MUTABLE_GOV_TABLES:
                counts["governed_mutable"] += 1
            elif table.startswith("gov_"):
                counts["governed_append_only"] += 1
            else:
                counts["legacy_mutable"] += 1
        conn.execute(sql.SQL(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
        ).format(sql.Identifier(app)))
    return {"ok": True, "operation": "grants", "tables": len(tables), **counts}


def verify() -> dict:
    app = identifier("POSTGRES_APP_USER")
    with psycopg.connect(required("POSTGRES_ADMIN_URL")) as conn:
        role = conn.execute(
            "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication "
            "FROM pg_roles WHERE rolname=%s", (app,),
        ).fetchone()
        if role is None or any(role):
            raise RuntimeError("APP_ROLE_PRIVILEGED")
        owned = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND pg_get_userbyid(c.relowner)=%s", (app,),
        ).fetchone()[0]
        if owned:
            raise RuntimeError("APP_ROLE_OWNS_OBJECTS")
        tables = [row[0] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()]
        verbs = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
        for table in tables:
            actual = {
                verb for verb in verbs
                if conn.execute("SELECT has_table_privilege(%s,%s,%s)",
                                (app, f"public.{table}", verb)).fetchone()[0]
            }
            if actual != expected_privileges(table):
                raise RuntimeError(f"APP_TABLE_PRIVILEGE_DIVERGED:{table}")
        rls = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE relrowsecurity AND relforcerowsecurity) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relname LIKE 'gov_%'"
        ).fetchone()
        if not rls[0] or rls[0] != rls[1]:
            raise RuntimeError("GOVERNED_RLS_NOT_FORCED")
    return {"ok": True, "operation": "verify", "tables": len(tables),
            "governed_rls_forced": rls[0], "app_role_unprivileged": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "grants", "verify"))
    args = parser.parse_args()
    result = {"prepare": prepare, "grants": grants, "verify": verify}[args.operation]()
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
