#!/usr/bin/env python3
"""Back up production read-only, restore into NEW local acceptance databases.

Default is a local plan. --execute-local-restore explicitly enables remote
pg_dump (reads only) and new LOCAL databases, never production mutation.
Requires the existing project .venv and already-running acceptance PostgreSQL.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

import plan as preflight

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "acceptance/runtime"))
sys.path.insert(0, str(ROOT / "src"))
import infra  # noqa: E402


def target_url(url: str, database: str) -> str:
    parts = urlsplit(url)
    if parts.hostname != "127.0.0.1" or not database.startswith("tkos_convergence_"):
        raise RuntimeError("TARGET_MUST_BE_A_NEW_LOCAL_CONVERGENCE_DATABASE")
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))


def fingerprint_query(schema: str, table: str) -> str:
    relation = f"{preflight.identifier(schema)}.{preflight.identifier(table)}"
    return f"""SELECT json_build_object('schema','{schema}','table','{table}',
 'rows',count(*),'sha256',encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex'))
 FROM (SELECT encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') AS h FROM {relation} r) hashed;"""


TABLE_QUERY = """SELECT json_build_object('schema',schemaname,'table',tablename)
 FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')
 ORDER BY schemaname,tablename;"""


class AcceptanceGateFailed(RuntimeError):
    """Only table names and fixed reason codes may enter these diagnostics."""

    def __init__(self, code: str, differences: list[dict] | None = None):
        super().__init__(code)
        self.differences = differences or []


def approved_legacy_inventory() -> set[tuple[str, str]]:
    """The reviewed preflight is the explicit table allowlist, not live discovery."""
    reviewed = json.loads(Path(__file__).with_name("preflight-20260907.json").read_text())["preflight"]
    candidates = [database for database in reviewed["databases"]
                  if database["container"] == preflight.DB_CONTAINERS[0] and database["tables"]]
    if len(candidates) != 1:
        raise AcceptanceGateFailed("APPROVED_SOURCE_INVENTORY_NOT_UNIQUE")
    return {(row["schema"], row["table"]) for row in candidates[0]["tables"]}


def source_equivalence_gate(memory: list[dict], aw: list[dict], wm_comparisons: list[dict]) -> dict:
    approved = approved_legacy_inventory()
    fingerprints = [{(row["schema"], row["table"]): row for row in rows} for rows in (memory, aw)]
    differences = []
    for source, rows, by_key in zip(("memory", "aw"), (memory, aw), fingerprints):
        if len(rows) != len(by_key):
            differences.append({"source": source, "reason": "DUPLICATE_TABLE_ENTRY"})
        for schema, table in sorted(approved - by_key.keys()):
            differences.append({"source": source, "table": schema + "." + table, "reason": "MISSING_APPROVED_TABLE"})
        for schema, table in sorted(by_key.keys() - approved):
            differences.append({"source": source, "table": schema + "." + table, "reason": "UNKNOWN_TABLE"})
    allowed_subsets = {("public", table) for table in preflight.ROW_COMPARE_TABLES}
    for key in sorted(approved & fingerprints[0].keys() & fingerprints[1].keys()):
        if key not in allowed_subsets:
            left, right = (source[key] for source in fingerprints)
            if left["rows"] != right["rows"] or left["sha256"] != right["sha256"]:
                differences.append({"table": ".".join(key), "reason": "NON_WM_ROWS_OR_CONTENT_DIFFER"})
    indexed = {row["table"]: row for row in wm_comparisons}
    if len(indexed) != len(wm_comparisons) or set(indexed) != set(preflight.ROW_COMPARE_TABLES):
        differences.append({"reason": "WORKING_MEMORY_COMPARISON_INCOMPLETE"})
    for table, row in indexed.items():
        if row["aw_only"] or row["shared_changed"]:
            differences.append({"table": "public." + table, "reason": "AW_ONLY_OR_SHARED_CONTENT_CONFLICT"})
        key = ("public", table)
        if all(key in source for source in fingerprints):
            if (row["shared"] + row["memory_only"] != fingerprints[0][key]["rows"]
                    or row["shared"] + row["aw_only"] != fingerprints[1][key]["rows"]):
                differences.append({"table": "public." + table, "reason": "ROW_COMPARISON_COUNT_MISMATCH"})
    if differences:
        raise AcceptanceGateFailed("FULL_SOURCE_EQUIVALENCE_GATE_FAILED", differences)
    return {"passed": True, "approved_tables": len(approved),
            "non_wm_tables_equal": len(approved - allowed_subsets),
            "allowed_public_wm_subset_tables": len(allowed_subsets), "differences": []}


def constraints_gate(result: dict) -> None:
    if (result.get("unvalidated_foreign_keys") != 0
            or result.get("wrong_public_table_owner") != 0
            or not isinstance(result.get("foreign_keys"), int) or result["foreign_keys"] <= 0):
        raise AcceptanceGateFailed("FOREIGN_KEY_OR_OWNERSHIP_GATE_FAILED")


def remote_fingerprints(container: str, database: str) -> list[dict]:
    tables = preflight.sql(container, TABLE_QUERY, database)
    if any(table["table"] == "gov_credentials" for table in tables):
        raise RuntimeError("SOURCE_CONTAINS_GOVERNED_CREDENTIALS_REQUIRES_SEPARATE_REVIEW")
    return preflight.sql(container, "\n".join(fingerprint_query(**t) for t in tables), database)


def local_fingerprints(admin_url: str, *, only_tables: set[tuple[str, str]] | None = None) -> list[dict]:
    import psycopg
    with psycopg.connect(admin_url) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        conn.execute("SET LOCAL statement_timeout='8s'")
        tables = [row[0] for row in conn.execute(TABLE_QUERY).fetchall()]
        return [conn.execute(fingerprint_query(**table)).fetchone()[0] for table in tables
                if only_tables is None or (table["schema"], table["table"]) in only_tables]


def run_quiet(command: list[str], *, stdin=None, stdout=None, timeout: int = 180) -> None:
    result = subprocess.run(command, stdin=stdin, stdout=stdout or subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        # pg_restore errors may contain rows, names or connection information.
        raise RuntimeError("DATABASE_BACKUP_OR_RESTORE_FAILED")


def production_dump(container: str, database: str, path: Path) -> dict:
    if container not in preflight.DB_CONTAINERS[:2]:
        raise RuntimeError("SOURCE_CONTAINER_NOT_ALLOWED")
    shell = ('export PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=60000"; '
             'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d ' + shlex.quote(database)
             + ' --format=custom')
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", preflight.HOST,
               shlex.join(["docker", "exec", container, "sh", "-c", shell])]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        run_quiet(command, stdout=stream)
    return {"file": path.name, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def create_local_database(admin_url: str, database: str) -> str:
    import psycopg
    from psycopg import sql
    restored_url = target_url(admin_url, database)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database,)).fetchone():
            raise RuntimeError("TARGET_DATABASE_ALREADY_EXISTS")
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
            sql.Identifier(database), sql.Identifier(infra.OWNER)))
    with psycopg.connect(restored_url) as conn:
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
            sql.Identifier(database), sql.Identifier(infra.OWNER), sql.Identifier(infra.APP)))
    return restored_url


def transfer_local_ownership(admin_url: str) -> dict:
    import psycopg
    from psycopg import sql
    with psycopg.connect(admin_url) as conn:
        objects = conn.execute("""SELECT n.nspname,c.relname,c.relkind FROM pg_class c
 JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
 AND c.relkind IN ('r','p','v','m','S')
 AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e')
 ORDER BY CASE WHEN c.relkind='S' THEN 1 ELSE 0 END,c.relname""").fetchall()
        for schema, name, kind in objects:
            noun = {"r": "TABLE", "p": "TABLE", "v": "VIEW", "m": "MATERIALIZED VIEW", "S": "SEQUENCE"}[kind]
            conn.execute(sql.SQL("ALTER " + noun + " {} OWNER TO {}").format(
                sql.Identifier(schema, name), sql.Identifier(infra.OWNER)))
        functions = conn.execute("""SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),p.prokind
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
 AND p.prokind IN ('f','p')
 AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e')""").fetchall()
        for schema, name, arguments, kind in functions:
            conn.execute(sql.SQL("ALTER " + ("PROCEDURE" if kind == "p" else "FUNCTION") + " {}({}) OWNER TO {}").format(
                sql.Identifier(schema, name), sql.SQL(arguments), sql.Identifier(infra.OWNER)))
        schemas = conn.execute("""SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema')
 AND nspname NOT LIKE 'pg_toast%' AND nspname NOT LIKE 'pg_temp_%'""").fetchall()
        for schema, in schemas:
            conn.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(sql.Identifier(schema), sql.Identifier(infra.OWNER)))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(infra.APP)))
    return {"objects_transferred": len(objects), "functions_transferred": len(functions),
            "roles_created_or_altered": 0}


def compare_rows(memory_url: str, aw_url: str) -> list[dict]:
    import psycopg
    comparisons = []
    with psycopg.connect(memory_url) as memory, psycopg.connect(aw_url) as aw:
        for conn in (memory, aw):
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
        for table, keys in preflight.ROW_COMPARE_TABLES.items():
            key_json = "jsonb_build_array(" + ",".join(preflight.identifier(k) for k in keys) + ")::text"
            query = ("SELECT encode(sha256(convert_to(" + key_json + ",'UTF8')),'hex'),"
                     "encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') FROM public."
                     + preflight.identifier(table) + " r")
            left, right = (dict(conn.execute(query).fetchall()) for conn in (memory, aw))
            shared = left.keys() & right.keys()
            comparisons.append({"table": table, "memory_only": len(left.keys() - right.keys()),
                                "aw_only": len(right.keys() - left.keys()), "shared": len(shared),
                                "shared_changed": sum(left[k] != right[k] for k in shared)})
    return comparisons


def local_foreign_keys(admin_url: str) -> dict:
    import psycopg
    with psycopg.connect(admin_url) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute("""SELECT count(*),count(*) FILTER (WHERE NOT c.convalidated)
 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
 WHERE c.contype='f' AND n.nspname NOT IN ('pg_catalog','information_schema')""").fetchone()
        bad = conn.execute("""SELECT count(*) FROM pg_tables
 WHERE schemaname='public' AND tableowner<>%s""", (infra.OWNER,)).fetchone()[0]
    return {"foreign_keys": row[0], "unvalidated_foreign_keys": row[1], "wrong_public_table_owner": bad}


def execute(from_state: Path | None = None, *, preserve_aw_history: bool = False) -> dict:
    import psycopg
    from memory_service_app.migrate import migrate
    if preserve_aw_history and from_state is None:
        raise AcceptanceGateFailed("PRESERVATION_MERGE_REQUIRES_PRIOR_PRIVATE_DUMPS")
    if not infra.ENV_FILE.exists() or not infra.SECRETS.exists():
        raise RuntimeError("EXISTING_ACCEPTANCE_INFRASTRUCTURE_REQUIRED")
    env = infra.load_environment()
    admin_url = infra.test_admin_url()
    identity = secrets.token_hex(6)
    folder = infra.STATE / ("convergence-" + identity)
    folder.mkdir(mode=0o700)
    report = {"schema": "tkos.convergence.rehearsal/1", "run_id": "convergence-" + identity,
              "started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "production_mutations": False, "production_services_restarted": False,
              "local_isolated_restore": True, "backups": [], "restores": [], "migrations": [],
              "s3_objects_restored": False, "production_roles_restored": False,
              "gov_identity_or_authority_imported": False, "traffic_switched": False}
    report["rehearsal_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["approved_inventory_sha256"] = hashlib.sha256(Path(__file__).with_name("preflight-20260907.json").read_bytes()).hexdigest()
    report["preservation_merge_enabled"] = preserve_aw_history
    state = {"run_id": report["run_id"], "private_directory": str(folder),
             "output_directory": str(folder), "sources": [], "source_dumps": []}
    state_file = folder / "state.json"
    infra.private_json(state_file, state)
    infra.private_json(folder / "report.json", report)
    try:
        previous_state = previous_report = None
        if from_state is not None:
            previous_folder = from_state.resolve().parent
            if previous_folder.parent != infra.STATE.resolve() or not previous_folder.name.startswith("convergence-"):
                raise AcceptanceGateFailed("REUSED_BACKUP_MUST_BELONG_TO_A_PRIOR_CONVERGENCE_RUN")
            previous_state = json.loads(from_state.read_text())
            previous_report = json.loads((previous_folder / "report.json").read_text())
            report["reused_backup_run"] = previous_state["run_id"]
            report["production_contacted_this_run"] = False
        else:
            report["production_contacted_this_run"] = True
        for label, container in zip(("memory", "aw"), preflight.DB_CONTAINERS[:2]):
            dump = folder / (label + ".dump")
            if previous_state is not None:
                original = next(row for row in previous_state["sources"] if row["source"] == label)
                original_dump = Path(original["dump"])
                if original_dump.resolve().parent != previous_folder or original_dump.is_symlink():
                    raise AcceptanceGateFailed("REUSED_BACKUP_PATH_REJECTED")
                metadata = next(row for row in previous_report["backups"] if row["source"] == label)
                if preserve_aw_history:
                    import merge_history
                    manifest = json.loads(merge_history.MANIFEST.read_text())
                    if metadata["sha256"] != manifest["source_dumps"][label]:
                        raise AcceptanceGateFailed("SOURCE_DUMP_NOT_IN_PRESERVATION_MANIFEST")
                contents = original_dump.read_bytes()
                if hashlib.sha256(contents).hexdigest() != metadata["sha256"]:
                    raise AcceptanceGateFailed("REUSED_BACKUP_SHA256_MISMATCH")
                with os.fdopen(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                    stream.write(contents)
                before = next(row["fingerprints"] for row in previous_report["restores"] if row["source"] == label)
                backup = {"file": dump.name, "bytes": len(contents), "sha256": metadata["sha256"]}
                source_evidence = {"source_stable_around_original_dump": metadata.get("source_stable_around_dump", False),
                                   "production_stability_rechecked_this_run": False}
            else:
                candidates = []
                for database in preflight.sql(container, "SELECT to_json(datname) FROM pg_database WHERE NOT datistemplate ORDER BY datname;"):
                    found = preflight.sql(container, "SELECT to_json(to_regclass('public.semantic_entities') IS NOT NULL);", database)[0]
                    if found:
                        candidates.append(database)
                if len(candidates) != 1:
                    raise RuntimeError("EXACT_PRODUCTION_DATABASE_NOT_UNIQUE")
                source_database = candidates[0]
                before = remote_fingerprints(container, source_database)
                backup = production_dump(container, source_database, dump)
                after = remote_fingerprints(container, source_database)
                if before != after:
                    raise RuntimeError("SOURCE_CHANGED_DURING_BACKUP_RETRY_UNDER_WRITE_FENCE")
                source_evidence = {"source_stable_around_dump": True}
            report["backups"].append({"source": label, **backup, **source_evidence, "table_count": len(before)})
            state["source_dumps"].append({"source": label, "path": str(dump), **backup})
            database = "tkos_convergence_" + label + "_" + identity
            restored_url = create_local_database(admin_url, database)
            source_state = {"source": label, "database": database, "admin_url": restored_url,
                            "migration_url": target_url(env["MIGRATION_DATABASE_URL"], database),
                            "app_url": target_url(env["APP_DATABASE_URL"], database), "dump": str(dump)}
            state["sources"].append(source_state)
            infra.private_json(state_file, state)
            with dump.open("rb") as stream:
                run_quiet(infra.compose("exec", "-T", "postgres", "pg_restore", "-U", infra.ADMIN,
                                        "-d", database, "--no-owner", "--no-acl", "--exit-on-error"), stdin=stream)
            restored = local_fingerprints(restored_url)
            if before != restored:
                raise RuntimeError("RESTORED_LEGACY_FINGERPRINT_MISMATCH")
            ownership = transfer_local_ownership(restored_url)
            report["restores"].append({"source": label, "legacy_tables_equal": True,
                                       "table_count": len(restored), "ownership": ownership,
                                       "fingerprints": restored})
            infra.private_json(folder / "report.json", report)
        memory, aw = state["sources"]
        report["aw_subset_comparison"] = compare_rows(memory["admin_url"], aw["admin_url"])
        baseline = local_fingerprints(memory["admin_url"])
        if preserve_aw_history:
            import merge_history
            report["preservation_merge"] = merge_history.preserve(memory["admin_url"], aw["admin_url"], approved_legacy_inventory())
            baseline = local_fingerprints(memory["admin_url"])
        else:
            report["full_source_equivalence"] = source_equivalence_gate(
                baseline, local_fingerprints(aw["admin_url"]), report["aw_subset_comparison"])
        report["migrations"] = migrate(memory["migration_url"])
        migrated = local_fingerprints(memory["admin_url"], only_tables={(row["schema"], row["table"]) for row in baseline})
        keep = lambda rows: [row for row in rows if not (row["schema"] == "public" and row["table"] == "schema_migrations")]
        if keep(baseline) != keep(migrated):
            raise RuntimeError("MIGRATION_CHANGED_LEGACY_DATA")
        report["migrations_replay"] = migrate(memory["migration_url"])
        if report["migrations_replay"]:
            raise RuntimeError("MIGRATION_REPLAY_NOT_EMPTY")
        grants = infra.child_python(infra.GRANTS, {"migration_url": memory["migration_url"],
                                                 "app_url": memory["app_url"], "app": infra.APP,
                                                 "mutable_gov_tables": sorted(infra.GOV_MUTABLE_TABLES)})
        report["application_role"] = {key: grants[key] for key in ("superuser", "bypassrls", "owned_tables")}
        report["foreign_keys_after_migration"] = local_foreign_keys(memory["admin_url"])
        constraints_gate(report["foreign_keys_after_migration"])
        if preserve_aw_history:
            report["legacy_application_privileges"] = merge_history.restrict_legacy_application_role(
                memory["migration_url"], memory["app_url"], infra.APP)
            with psycopg.connect(memory["admin_url"]) as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.execute("SET LOCAL TIME ZONE 'UTC'")
                report["union_after_migrations"] = merge_history.union_gate(conn, manifest, after_migration=True)
                report["foreign_keys_after_union_migrations"] = merge_history.foreign_key_gate(conn)
        report["legacy_data_unchanged_after_migrations"] = True
        with psycopg.connect(memory["admin_url"]) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            scopes = conn.execute("SELECT DISTINCT tenant_id,organization_id FROM public.semantic_entities ORDER BY 1,2").fetchall()
            state["legacy_scopes"] = [{"tenant_id": tenant, "organization_id": org} for tenant, org in scopes]
            governed = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'gov_%' ORDER BY tablename").fetchall()
            report["governed_tables"] = len(governed)
            report["governed_rows"] = sum(conn.execute('SELECT count(*) FROM public.' + preflight.identifier(table)).fetchone()[0] for table, in governed)
        if report["governed_rows"] != 0:
            raise AcceptanceGateFailed("UNAUTHORIZED_GOVERNED_ROWS_GENERATED")
        state["selected_source"] = "memory_plus_aw_history" if preserve_aw_history else "memory"
        state["selected_admin_url"] = memory["admin_url"]
        state["selected_migration_url"] = memory["migration_url"]
        state["selected_app_url"] = memory["app_url"]
        state["MIGRATION_DATABASE_URL"] = memory["migration_url"]
        state["APP_DATABASE_URL"] = memory["app_url"]
        state["scopes"] = state["legacy_scopes"]
        state["scope_requires_selection"] = len(state["scopes"]) != 1
        state["primary_scope"] = state["scopes"][0] if len(state["scopes"]) == 1 else None
        report["status"] = "passed"
        report["completed_at"] = datetime.now(timezone.utc).isoformat()
        report["limits"] = ["Database-only local rehearsal; external object bytes and global roles are not restored",
                            "Source writes were not fenced; stable before/after hashes and restore equivalence verified",
                            "Legacy rows retained; no automatic legacy-to-governed authority conversion",
                            "Target server deployment, personal identity acceptance and production cutover remain separate"]
    except Exception as exc:
        report["status"] = "failed"
        report["error_code"] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if isinstance(exc, AcceptanceGateFailed):
            report["gate_differences"] = exc.differences
        raise RuntimeError("REHEARSAL_FAILED_SEE_PRIVATE_SANITIZED_REPORT") from None
    finally:
        infra.private_json(state_file, state)
        infra.private_json(folder / "report.json", report)
    public = Path(__file__).with_name("rehearsal-" + identity + ".json")
    with public.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"status": report["status"], "run_id": report["run_id"], "state_file": str(state_file),
            "report_file": str(public), "legacy_data_unchanged": True,
            "gov_rows": report["governed_rows"], "production_mutations": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-local-restore", action="store_true")
    parser.add_argument("--from-state", type=Path, help="Restore prior private dumps into fresh local databases; no production reads")
    parser.add_argument("--preserve-aw-history", action="store_true", help="Apply the reviewed two-source union manifest only on a new local restore")
    args = parser.parse_args()
    if not args.execute_local_restore:
        print(json.dumps({"mode": "plan_only", "production_mutations": False,
                          "next": "--execute-local-restore reads two production dumps and creates two new local databases"}))
        return
    print(json.dumps(execute(args.from_state, preserve_aw_history=args.preserve_aw_history)))


if __name__ == "__main__":
    main()
