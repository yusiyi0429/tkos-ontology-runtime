#!/usr/bin/env python3
"""Target-only database bootstrap for the production Runtime.

The script never prints connection strings, passwords, or bearer-token digests.
It is intentionally separate from application startup so the API and Worker only
receive the restricted application connection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
# The release grant step's classes (deploy/offline-release/db_admin.py). That
# file cannot be imported: the db-admin service mounts only this one.
# tests/test_remote_production_grants.py fails if the two diverge.
MUTABLE_GOV_TABLES = {
    "gov_scopes",
    "gov_principals",
    "gov_credentials",
    "gov_role_assignments",
    "gov_objects",
    "gov_feedback_state",
    "gov_work_item_state",
    "gov_formation_round_state",
    "gov_round_formal_submissions",
    "gov_execution_state",
    "gov_a3_work_item_state",
    "gov_method_state",
    "gov_method_strategy_heads",
    "gov_method_runs",
}
CONTROL_PLANE_TABLES = {
    "gov_method_profile_revisions",
    "gov_protocol_policies",
    "gov_protocol_support_registry",
    "gov_protocol_control_events",
    "gov_method_agent_bindings",
}
RUNTIME_TABLES = {"runtime_tasks", "runtime_worker_heartbeats"}
PROVISIONED_TABLES = {
    "gov_scopes": 1,
    "gov_principals": 1,
    "gov_credentials": 1,
    "gov_domains": 1,
    "gov_role_assignments": 1,
    "gov_activation_policies": 1,
}


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"MISSING_ENV:{name}")
    return value


def identifier(name: str) -> str:
    value = env(name)
    if not IDENTIFIER.fullmatch(value):
        raise RuntimeError(f"INVALID_IDENTIFIER:{name}")
    return value


def uuid_env(name: str) -> str:
    try:
        return str(UUID(env(name)))
    except ValueError as exc:
        raise RuntimeError(f"INVALID_UUID:{name}") from exc


def token_digest() -> str:
    path = Path(env("TKOS_NARRATIVE_SERVICE_TOKEN_FILE"))
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("SERVICE_TOKEN_UNREADABLE") from exc
    if not 32 <= len(token) <= 1024 or token != token.strip():
        raise RuntimeError("SERVICE_TOKEN_INVALID")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def prepare() -> dict:
    database = identifier("POSTGRES_DB")
    owner = identifier("POSTGRES_OWNER_USER")
    app = identifier("POSTGRES_APP_USER")
    owner_password = env("POSTGRES_OWNER_PASSWORD")
    app_password = env("POSTGRES_APP_PASSWORD")
    with psycopg.connect(env("POSTGRES_ADMIN_URL"), autocommit=True) as conn:
        for role, password in ((owner, owner_password), (app, app_password)):
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)
            ).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(
                sql.SQL(
                    verb
                    + " ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
        conn.execute(
            sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                sql.Identifier(database), sql.Identifier(owner)
            )
        )
        conn.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(database)
            )
        )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                sql.Identifier(database), sql.Identifier(owner), sql.Identifier(app)
            )
        )
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        # Statement-level call counts for the performance baseline.  Requires
        # shared_preload_libraries=pg_stat_statements on the postgres service;
        # without it CREATE EXTENSION fails and prepare() reports the reason.
        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            stat_statements = True
        except psycopg.errors.Error as exc:
            stat_statements = str(exc).strip().splitlines()[0]
    return {"ok": True, "operation": "prepare", "roles": 2, "database": database,
            "pg_stat_statements": stat_statements}


def transfer() -> dict:
    """Move restored application objects from bootstrap admin to migration owner."""
    owner = identifier("POSTGRES_OWNER_USER")
    transferred = {"relations": 0, "routines": 0, "schemas": 0}
    with psycopg.connect(env("POSTGRES_ADMIN_URL")) as conn:
        relations = conn.execute(
            """SELECT n.nspname,c.relname,c.relkind FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                 AND c.relkind IN ('r','p','v','m','S')
                 AND NOT EXISTS (
                   SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                     AND d.objid=c.oid AND d.deptype='e')
               ORDER BY CASE WHEN c.relkind='S' THEN 1 ELSE 0 END,n.nspname,c.relname"""
        ).fetchall()
        for schema, name, kind in relations:
            noun = {"r": "TABLE", "p": "TABLE", "v": "VIEW",
                    "m": "MATERIALIZED VIEW", "S": "SEQUENCE"}[kind]
            conn.execute(
                sql.SQL("ALTER " + noun + " {} OWNER TO {}").format(
                    sql.Identifier(schema, name), sql.Identifier(owner)
                )
            )
        transferred["relations"] = len(relations)
        routines = conn.execute(
            """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),p.prokind
               FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
               WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                 AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp_%'
                 AND p.prokind IN ('f','p')
                 AND NOT EXISTS (
                   SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
                     AND d.objid=p.oid AND d.deptype='e')
               ORDER BY n.nspname,p.proname"""
        ).fetchall()
        for schema, name, arguments, kind in routines:
            noun = "PROCEDURE" if kind == "p" else "FUNCTION"
            conn.execute(
                sql.SQL("ALTER " + noun + " {}({}) OWNER TO {}").format(
                    sql.Identifier(schema, name), sql.SQL(arguments), sql.Identifier(owner)
                )
            )
        transferred["routines"] = len(routines)
        schemas = conn.execute(
            """SELECT nspname FROM pg_namespace
               WHERE nspname NOT IN ('pg_catalog','information_schema')
                 AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%'
               ORDER BY nspname"""
        ).fetchall()
        for (schema,) in schemas:
            conn.execute(
                sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                    sql.Identifier(schema), sql.Identifier(owner)
                )
            )
        transferred["schemas"] = len(schemas)
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    return {"ok": True, "operation": "transfer", **transferred}


def expected_privileges(table: str) -> set[str]:
    """The release's classes, except that legacy tables stay read-only here."""
    if table == "schema_migrations" or table in CONTROL_PLANE_TABLES:
        return {"SELECT"}
    if table in RUNTIME_TABLES or table in MUTABLE_GOV_TABLES:
        return {"SELECT", "INSERT", "UPDATE"}
    if table.startswith("gov_"):
        return {"SELECT", "INSERT"}
    return {"SELECT"}


def grants() -> dict:
    app = identifier("POSTGRES_APP_USER")
    counts = {"legacy_select_only": 0, "runtime_mutable": 0, "governed_mutable": 0,
              "governed_append_only": 0, "control_read_only": 0}
    with psycopg.connect(env("POSTGRES_MIGRATION_URL")) as conn:
        schemas = [row[0] for row in conn.execute(
            """SELECT nspname FROM pg_namespace
               WHERE nspname NOT IN ('pg_catalog','information_schema')
                 AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%'
               ORDER BY nspname"""
        ).fetchall()]
        for schema in schemas:
            conn.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(app)
                )
            )
        conn.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app))
        )
        tables = [row[0] for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()]
        for table in tables:
            relation = sql.Identifier("public", table)
            conn.execute(
                sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                    relation, sql.Identifier(app)
                )
            )
            if table == "schema_migrations" or (
                not table.startswith("gov_") and table not in RUNTIME_TABLES
            ):
                counts["legacy_select_only"] += 1
            elif table in RUNTIME_TABLES:
                counts["runtime_mutable"] += 1
            elif table in MUTABLE_GOV_TABLES:
                counts["governed_mutable"] += 1
            elif table in CONTROL_PLANE_TABLES:
                counts["control_read_only"] += 1
            else:
                counts["governed_append_only"] += 1
            privileges = ", ".join(sorted(expected_privileges(table)))
            conn.execute(
                sql.SQL("GRANT " + privileges + " ON TABLE {} TO {}").format(
                    relation, sql.Identifier(app)
                )
            )
        conn.execute(
            sql.SQL(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"
            ).format(sql.Identifier(app))
        )
    checked = verify_permissions()
    return {"ok": True, "operation": "grants", **counts,
            "permission_check": checked}


def _expected_identity() -> dict[str, str]:
    return {
        "scope_id": uuid_env("TKOS_NARRATIVE_SCOPE_ID"),
        "domain_id": uuid_env("TKOS_NARRATIVE_DOMAIN_ID"),
        "principal_id": uuid_env("TKOS_NARRATIVE_PRINCIPAL_ID"),
        "assignment_id": uuid_env("TKOS_NARRATIVE_ASSIGNMENT_ID"),
        "credential_id": uuid_env("TKOS_NARRATIVE_CREDENTIAL_ID"),
        "policy_id": uuid_env("TKOS_NARRATIVE_POLICY_ID"),
        "policy_revision_id": uuid_env("TKOS_NARRATIVE_POLICY_REVISION_ID"),
        "tenant_id": env("MEMORY_TENANT"),
        "company_id": env("MEMORY_ORG"),
    }


def provision_narrative() -> dict:
    identity = _expected_identity()
    digest = token_digest()
    policy = {
        "version": "tkos.governed-policy/production-narrative-read-v1",
        "action_roles": {
            "read": ["AGENT"],
            "read_legacy_context": ["AGENT"],
        },
        "commitment_party_roles": {},
        "independent_verifier": True,
    }
    with psycopg.connect(env("POSTGRES_ADMIN_URL")) as admin:
        gov_tables = [row[0] for row in admin.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename LIKE 'gov_%' ORDER BY tablename"
        ).fetchall()]
        before = {table: admin.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table))
        ).fetchone()[0] for table in gov_tables}
    if any(before.values()):
        verify_provisioned(identity, digest, strict_counts=False)
        return {"ok": True, "operation": "provision-narrative", "created": False,
                "scope_id": identity["scope_id"], "domain_id": identity["domain_id"]}

    with psycopg.connect(env("POSTGRES_MIGRATION_URL")) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('app.governed_scope_id', %s, true)",
                (identity["scope_id"],),
            )
            conn.execute(
                "INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES (%s,%s,%s)",
                (identity["scope_id"], identity["tenant_id"], identity["company_id"]),
            )
            conn.execute(
                "INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)",
                (identity["domain_id"], identity["scope_id"],
                 "Company narrative read domain"),
            )
            conn.execute(
                """INSERT INTO gov_principals
                   (principal_id,scope_id,principal_type,display_name)
                   VALUES (%s,%s,'agent',%s)""",
                (identity["principal_id"], identity["scope_id"],
                 "Clark production narrative reader"),
            )
            conn.execute(
                """INSERT INTO gov_role_assignments
                   (assignment_id,scope_id,principal_id,domain_id,role)
                   VALUES (%s,%s,%s,%s,'AGENT')""",
                (identity["assignment_id"], identity["scope_id"],
                 identity["principal_id"], identity["domain_id"]),
            )
            conn.execute(
                "SELECT set_config('app.governed_credential_digest', %s, true)",
                (digest,),
            )
            conn.execute(
                """INSERT INTO gov_credentials
                   (credential_id,scope_id,principal_id,credential_digest,label)
                   VALUES (%s,%s,%s,%s,%s)""",
                (identity["credential_id"], identity["scope_id"],
                 identity["principal_id"], digest,
                 "Clark production narrative read"),
            )
            conn.execute(
                """INSERT INTO gov_activation_policies
                   (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,
                    content,recorded_by)
                   VALUES (%s,%s,%s,%s,1,%s,%s)""",
                (identity["policy_revision_id"], identity["scope_id"],
                 identity["domain_id"], identity["policy_id"], Jsonb(policy),
                 identity["principal_id"]),
            )
            conn.execute("SELECT set_config('app.governed_credential_digest', '', true)")
    verify_provisioned(identity, digest, strict_counts=True)
    return {"ok": True, "operation": "provision-narrative", "created": True,
            "scope_id": identity["scope_id"], "domain_id": identity["domain_id"]}


def verify_provisioned(identity: dict[str, str], digest: str,
                       *, strict_counts: bool) -> None:
    with psycopg.connect(env("POSTGRES_ADMIN_URL")) as conn:
        rows = {
            "scope": conn.execute(
                "SELECT scope_id::text,tenant_id,company_id,auth_epoch FROM gov_scopes "
                "WHERE scope_id=%s", (identity["scope_id"],)
            ).fetchall(),
            "domain": conn.execute(
                "SELECT domain_id::text,scope_id::text,name FROM gov_domains "
                "WHERE domain_id=%s", (identity["domain_id"],)
            ).fetchall(),
            "principal": conn.execute(
                "SELECT principal_id::text,scope_id::text,principal_type,display_name,active "
                "FROM gov_principals WHERE principal_id=%s", (identity["principal_id"],)
            ).fetchall(),
            "assignment": conn.execute(
                "SELECT assignment_id::text,scope_id::text,principal_id::text," 
                "domain_id::text,role,active FROM gov_role_assignments "
                "WHERE assignment_id=%s", (identity["assignment_id"],)
            ).fetchall(),
            "credential": conn.execute(
                "SELECT credential_id::text,scope_id::text,principal_id::text," 
                "credential_digest,revoked_at FROM gov_credentials "
                "WHERE credential_id=%s", (identity["credential_id"],)
            ).fetchall(),
            "policy": conn.execute(
                "SELECT policy_revision_id::text,scope_id::text,domain_id::text," 
                "policy_id::text,policy_seq,content,recorded_by::text "
                "FROM gov_activation_policies WHERE policy_revision_id=%s",
                (identity["policy_revision_id"],)
            ).fetchall(),
        }
        if strict_counts:
            gov_tables = [row[0] for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename LIKE 'gov_%'"
            ).fetchall()]
            counts = {table: conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table))
            ).fetchone()[0] for table in gov_tables}
            expected = {table: PROVISIONED_TABLES.get(table, 0) for table in gov_tables}
            if counts != expected:
                raise RuntimeError("UNEXPECTED_GOVERNED_ROWS")
    expected_rows = {
        "scope": [(identity["scope_id"], identity["tenant_id"], identity["company_id"], 1)],
        "domain": [(identity["domain_id"], identity["scope_id"], "Company narrative read domain")],
        "principal": [(identity["principal_id"], identity["scope_id"], "agent",
                       "Clark production narrative reader", True)],
        "assignment": [(identity["assignment_id"], identity["scope_id"],
                        identity["principal_id"], identity["domain_id"], "AGENT", True)],
        "credential": [(identity["credential_id"], identity["scope_id"],
                        identity["principal_id"], digest, None)],
    }
    for name, expected in expected_rows.items():
        if rows[name] != expected:
            raise RuntimeError(f"PROVISIONED_{name.upper()}_DIVERGED")
    if len(rows["policy"]) != 1:
        raise RuntimeError("PROVISIONED_POLICY_DIVERGED")
    p = rows["policy"][0]
    if p[:5] != (identity["policy_revision_id"], identity["scope_id"],
                  identity["domain_id"], identity["policy_id"], 1):
        raise RuntimeError("PROVISIONED_POLICY_DIVERGED")
    if p[6] != identity["principal_id"] or p[5].get("action_roles") != {
        "read": ["AGENT"], "read_legacy_context": ["AGENT"]
    }:
        raise RuntimeError("PROVISIONED_POLICY_DIVERGED")


def verify_permissions() -> dict:
    app = identifier("POSTGRES_APP_USER")
    with psycopg.connect(env("POSTGRES_ADMIN_URL")) as conn:
        role = conn.execute(
            """SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolreplication
               FROM pg_roles WHERE rolname=%s""", (app,)
        ).fetchone()
        if role is None or any(role):
            raise RuntimeError("APP_ROLE_PRIVILEGED")
        owned = conn.execute(
            """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                 AND pg_get_userbyid(c.relowner)=%s""", (app,)
        ).fetchone()[0]
        if owned:
            raise RuntimeError("APP_ROLE_OWNS_OBJECTS")
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()
        for (table,) in tables:
            relation = "public." + table
            select_ok = conn.execute(
                "SELECT has_table_privilege(%s,%s,'SELECT')", (app, relation)
            ).fetchone()[0]
            insert_ok, update_ok, delete_ok, truncate_ok = conn.execute(
                """SELECT has_table_privilege(%s,%s,'INSERT'),
                          has_table_privilege(%s,%s,'UPDATE'),
                          has_table_privilege(%s,%s,'DELETE'),
                          has_table_privilege(%s,%s,'TRUNCATE')""",
                (app, relation, app, relation, app, relation, app, relation),
            ).fetchone()
            expected = expected_privileges(table)
            if (select_ok, insert_ok, update_ok, delete_ok, truncate_ok) != (
                True, "INSERT" in expected, "UPDATE" in expected, False, False
            ):
                raise RuntimeError("APP_TABLE_PRIVILEGE_DIVERGED:" + table)
        rls = conn.execute(
            """SELECT count(*),count(*) FILTER (WHERE relrowsecurity AND relforcerowsecurity)
               FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname='public' AND c.relkind IN ('r','p')
                 AND c.relname LIKE 'gov_%'"""
        ).fetchone()
        if not rls[0] or rls[0] != rls[1]:
            raise RuntimeError("GOVERNED_RLS_NOT_FORCED")
    return {"app_role_unprivileged": True, "owned_objects": 0,
            "tables_checked": len(tables), "governed_rls_forced": rls[0]}


def verify() -> dict:
    permissions = verify_permissions()
    identity = _expected_identity()
    verify_provisioned(identity, token_digest(), strict_counts=False)
    with psycopg.connect(env("POSTGRES_ADMIN_URL")) as conn:
        legacy = conn.execute(
            """SELECT count(*) FROM pg_tables WHERE schemaname='public'
               AND tablename NOT LIKE 'gov_%'
               AND tablename NOT IN ('runtime_tasks','runtime_worker_heartbeats','schema_migrations')"""
        ).fetchone()[0]
        migrations = conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
        heartbeat = conn.execute(
            """SELECT count(*) FROM runtime_worker_heartbeats
               WHERE tenant_id=%s AND organization_id=%s
                 AND heartbeat_at > clock_timestamp() - interval '60 seconds'""",
            (identity["tenant_id"], identity["company_id"]),
        ).fetchone()[0]
    return {"ok": True, "operation": "verify", "permissions": permissions,
            "legacy_public_tables": legacy, "migrations": migrations,
            "narrative_identity": "read-only-agent", "fresh_worker_heartbeats": heartbeat}


def fingerprint() -> dict:
    """Return a content digest without returning any row or credential material."""
    digest = hashlib.sha256()
    checked = 0
    with psycopg.connect(env("POSTGRES_ADMIN_URL")) as conn:
        tables = [row[0] for row in conn.execute(
            """SELECT tablename FROM pg_tables WHERE schemaname='public'
               AND tablename <> 'runtime_worker_heartbeats' ORDER BY tablename"""
        ).fetchall()]
        for table in tables:
            row = conn.execute(
                sql.SQL(
                    "SELECT count(*),md5(COALESCE(string_agg(row_md5,'' ORDER BY row_md5),'')) "
                    "FROM (SELECT md5(to_jsonb(t)::text) AS row_md5 FROM {} AS t) AS rows"
                ).format(sql.Identifier("public", table))
            ).fetchone()
            digest.update(f"{table}\0{row[0]}\0{row[1]}\n".encode("utf-8"))
            checked += 1
    return {"ok": True, "operation": "fingerprint", "tables": checked,
            "sha256": digest.hexdigest(), "excluded": ["runtime_worker_heartbeats"]}


def statements_reset() -> dict:
    """Zero the statement counters so the next window measures one scenario."""
    with psycopg.connect(env("POSTGRES_ADMIN_URL"), autocommit=True) as conn:
        conn.execute("SELECT pg_stat_statements_reset()")
    return {"ok": True, "operation": "statements-reset"}


def statements() -> dict:
    """Statement call counts since the last reset, most-called first.

    `calls` is the number of times the statement was executed, which is what a
    per-request SQL budget is measured against; table scan counters are not.
    """
    with psycopg.connect(env("POSTGRES_ADMIN_URL"), row_factory=dict_row) as conn:
        rows = conn.execute(
            """SELECT calls, round(total_exec_time::numeric, 1) AS total_ms,
                      round(mean_exec_time::numeric, 3) AS mean_ms, rows,
                      left(regexp_replace(query, '\\s+', ' ', 'g'), 200) AS query
                 FROM pg_stat_statements s
                 JOIN pg_roles r ON r.oid = s.userid
                WHERE r.rolname = %s
                ORDER BY calls DESC LIMIT 40""",
            (identifier("POSTGRES_APP_USER"),),
        ).fetchall()
    return {"ok": True, "operation": "statements", "role": env("POSTGRES_APP_USER"),
            "total_calls": sum(row["calls"] for row in rows),
            "statements": [{k: (float(v) if isinstance(v, Decimal) else v)
                            for k, v in row.items()} for row in rows]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "transfer", "grants",
                                               "provision-narrative", "verify", "fingerprint",
                                               "statements", "statements-reset"))
    args = parser.parse_args()
    operations = {"prepare": prepare, "transfer": transfer, "grants": grants,
                  "provision-narrative": provision_narrative, "verify": verify,
                  "fingerprint": fingerprint, "statements": statements,
                  "statements-reset": statements_reset}
    print(json.dumps(operations[args.operation](), ensure_ascii=False,
                     separators=(",", ":")))


if __name__ == "__main__":
    main()
