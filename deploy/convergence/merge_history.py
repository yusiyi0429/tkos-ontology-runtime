"""Explicit, bounded preservation of AW-only history in a fresh local restore.

No production connections, no overwrite, no inference of governed authority.
Only the reviewed manifest and its four allowed append tables can be imported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import plan as preflight

AW_TABLES = {"conversations": ("conversation_id",), "runs": ("run_id",),
             "run_events": ("event_id",), "evals_sync_state": ("audit_id",)}
INSERT_ORDER = tuple(AW_TABLES)
MANIFEST = Path(__file__).with_name("merge-manifest-v1.json")


class MergeRejected(RuntimeError):
    pass


def require_local_database(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.hostname != "127.0.0.1" or not parsed.path.startswith("/tkos_convergence_"):
        raise MergeRejected("MERGE_TARGET_MUST_BE_LOCAL_CONVERGENCE_DATABASE")


def relation(entry: dict) -> str:
    return preflight.identifier(entry["schema"]) + "." + preflight.identifier(entry["table"])


def key_expression(entry: dict) -> str:
    keys = ",".join(preflight.identifier(key) for key in entry["primary_key"])
    return "encode(sha256(convert_to(jsonb_build_array(" + keys + ")::text,'UTF8')),'hex')"


def records(conn, entry: dict) -> dict[str, str]:
    rows = conn.execute("SELECT " + key_expression(entry)
                        + ",encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') FROM "
                        + relation(entry) + " r").fetchall()
    result = dict(rows)
    if len(result) != len(rows):
        raise MergeRejected("DUPLICATE_PRIMARY_KEY_DIGEST")
    return result


def fingerprint(rows: dict[str, str]) -> dict:
    return {"rows": len(rows), "sha256": hashlib.sha256("".join(sorted(rows.values())).encode()).hexdigest()}


def validate_records(entry: dict, memory: dict, aw: dict) -> dict:
    shared = memory.keys() & aw.keys()
    memory_only, aw_only = memory.keys() - aw.keys(), aw.keys() - memory.keys()
    if any(memory[key] != aw[key] for key in shared):
        raise MergeRejected("SHARED_PRIMARY_KEY_CONTENT_CONFLICT:" + entry["schema"] + "." + entry["table"])
    mode = entry["mode"]
    if mode not in {"equal", "memory_superset", "aw_superset"}:
        raise MergeRejected("UNKNOWN_MERGE_MODE")
    if (mode == "equal" and (memory_only or aw_only)
            or mode == "memory_superset" and aw_only
            or mode == "aw_superset" and memory_only):
        raise MergeRejected("SUBSET_DIRECTION_REJECTED:" + entry["schema"] + "." + entry["table"])
    union = {**memory, **aw}
    actual = {"memory_only": len(memory_only), "aw_only": len(aw_only), "shared": len(shared)}
    if actual != entry["expected_sets"] or fingerprint(memory) != entry["memory_fingerprint"] or fingerprint(aw) != entry["aw_fingerprint"]:
        raise MergeRejected("SOURCE_DOES_NOT_MATCH_REVIEWED_MANIFEST:" + entry["schema"] + "." + entry["table"])
    if fingerprint(union) != entry["union_fingerprint"]:
        raise MergeRejected("UNION_DOES_NOT_MATCH_REVIEWED_MANIFEST")
    return {"schema": entry["schema"], "table": entry["table"], "mode": mode,
            **actual, "shared_changed": 0, "union_fingerprint": fingerprint(union)}


def validate_manifest(manifest: dict, approved: set[tuple[str, str]]) -> None:
    entries = manifest.get("tables", [])
    names = {(entry["schema"], entry["table"]) for entry in entries}
    if names != approved or len(entries) != len(names) or len(names) != 53:
        raise MergeRejected("UNKNOWN_OR_MISSING_MANIFEST_TABLE")
    modes = {"equal": 0, "memory_superset": 0, "aw_superset": 0}
    for entry in entries:
        key = (entry["schema"], entry["table"])
        expected = "aw_superset" if key[0] == "public" and key[1] in AW_TABLES else (
            "memory_superset" if key[0] == "public" and key[1] in preflight.ROW_COMPARE_TABLES else "equal")
        if entry["mode"] != expected or not entry["primary_key"]:
            raise MergeRejected("MANIFEST_TABLE_CLASSIFICATION_REJECTED")
        if expected == "aw_superset" and tuple(entry["primary_key"]) != AW_TABLES[key[1]]:
            raise MergeRejected("APPEND_TABLE_PRIMARY_KEY_REJECTED")
        modes[expected] += 1
    if modes != {"equal": 42, "memory_superset": 7, "aw_superset": 4}:
        raise MergeRejected("MANIFEST_CLASS_COUNTS_REJECTED")


def catalog(conn) -> dict[tuple[str, str], dict]:
    tables = conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2").fetchall()
    result = {}
    for schema, table in tables:
        primary_key = conn.execute("""SELECT array_agg(a.attname ORDER BY key.ord)
 FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace
 CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS key(num,ord)
 JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=key.num
 WHERE c.contype='p' AND n.nspname=%s AND t.relname=%s""", (schema, table)).fetchone()[0]
        columns = conn.execute("""SELECT column_name,udt_schema,udt_name,is_nullable,column_default
 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position""", (schema, table)).fetchall()
        result[schema, table] = {"primary_key": primary_key, "columns": [list(row) for row in columns]}
    return result


def require_catalog(conn, manifest: dict, *, after_migration: bool = False) -> None:
    current = catalog(conn)
    expected = {(entry["schema"], entry["table"]): entry for entry in manifest["tables"]}
    extra = current.keys() - expected.keys()
    allowed_extra = {tuple(entry) for entry in manifest.get("additional_tables", [])} if after_migration else set()
    if not expected.keys() <= current.keys() or extra != allowed_extra:
        raise MergeRejected("DATABASE_TABLE_INVENTORY_REJECTED")
    for key, entry in expected.items():
        if current[key] != {name: entry[name] for name in ("primary_key", "columns")}:
            raise MergeRejected("DATABASE_SCHEMA_OR_PRIMARY_KEY_REJECTED:" + ".".join(key))


def union_gate(conn, manifest: dict, *, after_migration: bool = False) -> dict:
    require_catalog(conn, manifest, after_migration=after_migration)
    checks = []
    for entry in manifest["tables"]:
        observed = records(conn, entry)
        if after_migration and (entry["schema"], entry["table"]) == ("public", "schema_migrations"):
            # Migration rows are the only authorized legacy change. Verify the
            # original rows unchanged and permit precisely the three target names.
            rows = conn.execute("SELECT name FROM public.schema_migrations ORDER BY name").fetchall()
            if [row[0] for row in rows] != manifest["migration_names"] + manifest["additional_migrations"]:
                raise MergeRejected("UNEXPECTED_MIGRATION_LEDGER")
            original = dict(conn.execute("SELECT " + key_expression(entry)
                + ",encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') FROM public.schema_migrations r WHERE name=ANY(%s)",
                (manifest["migration_names"],)).fetchall())
            observed = original
        if fingerprint(observed) != entry["union_fingerprint"]:
            raise MergeRejected("CANDIDATE_UNION_NOT_PRESERVED:" + entry["schema"] + "." + entry["table"])
        checks.append({"table": entry["schema"] + "." + entry["table"], "union_equal": True, **fingerprint(observed)})
    return {"passed": True, "tables": checks, "all_53_union_preserved": True,
            "authorized_migration_rows_excluded": after_migration}


def foreign_key_gate(conn) -> dict:
    fks = conn.execute("""SELECT ns.nspname,s.relname,nt.nspname,t.relname,c.convalidated,
 (SELECT array_agg(a.attname ORDER BY u.ord) FROM unnest(c.conkey) WITH ORDINALITY u(num,ord)
 JOIN pg_attribute a ON a.attrelid=s.oid AND a.attnum=u.num),
 (SELECT array_agg(a.attname ORDER BY u.ord) FROM unnest(c.confkey) WITH ORDINALITY u(num,ord)
 JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=u.num)
 FROM pg_constraint c JOIN pg_class s ON s.oid=c.conrelid JOIN pg_namespace ns ON ns.oid=s.relnamespace
 JOIN pg_class t ON t.oid=c.confrelid JOIN pg_namespace nt ON nt.oid=t.relnamespace
 WHERE c.contype='f' AND ns.nspname NOT IN ('pg_catalog','information_schema')""").fetchall()
    for schema, table, parent_schema, parent, validated, columns, parent_columns in fks:
        if not validated:
            raise MergeRejected("UNVALIDATED_FOREIGN_KEY:" + schema + "." + table)
        identifier = preflight.identifier
        nullable = " AND ".join("s." + identifier(column) + " IS NOT NULL" for column in columns)
        equals = " AND ".join("s." + identifier(left) + " = t." + identifier(right) for left, right in zip(columns, parent_columns))
        query = ("SELECT count(*) FROM " + identifier(schema) + "." + identifier(table) + " s WHERE " + nullable
                 + " AND NOT EXISTS (SELECT 1 FROM " + identifier(parent_schema) + "." + identifier(parent) + " t WHERE " + equals + ")")
        if conn.execute(query).fetchone()[0]:
            raise MergeRejected("FOREIGN_KEY_ORPHANS:" + schema + "." + table)
    return {"checked": len(fks), "all_validated": True, "orphan_rows": 0}


def restrict_legacy_application_role(migration_url: str, app_url: str, app: str) -> dict:
    import psycopg
    from psycopg import sql
    manifest = json.loads(MANIFEST.read_text())
    public_tables = [entry["table"] for entry in manifest["tables"] if entry["schema"] == "public"]
    with psycopg.connect(migration_url) as conn:
        for table in public_tables:
            conn.execute(sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.{} FROM {}").format(sql.Identifier(table), sql.Identifier(app)))
            conn.execute(sql.SQL("GRANT SELECT ON TABLE public.{} TO {}").format(sql.Identifier(table), sql.Identifier(app)))
    with psycopg.connect(app_url) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for table in public_tables:
            privileges = conn.execute("SELECT has_table_privilege(current_user,%s,'SELECT'),has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                                      ("public." + table, "public." + table)).fetchone()
            if privileges != (True, False):
                raise MergeRejected("LEGACY_APPLICATION_ROLE_PRIVILEGES_TOO_BROAD")
    return {"legacy_public_tables": len(public_tables), "select_only_verified": True,
            "governed_privileges": "runtime mutable/append-only classification retained"}


def preserve(memory_url: str, aw_url: str, approved: set[tuple[str, str]]) -> dict:
    import psycopg
    require_local_database(memory_url)
    require_local_database(aw_url)
    manifest = json.loads(MANIFEST.read_text())
    validate_manifest(manifest, approved)
    report = {"manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
              "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_checks": [], "inserted": {},
              "replay_scope": "original missing-row insert plan; full preserve() requires a fresh restore"}
    with psycopg.connect(memory_url) as memory, psycopg.connect(aw_url) as aw:
        aw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for conn in (memory, aw):
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
            require_catalog(conn, manifest)
        # Locks only the new local candidate's four append targets. The outer
        # connection transaction rolls back every insertion if any gate fails.
        memory.execute("LOCK TABLE " + ",".join("public." + preflight.identifier(table) for table in INSERT_ORDER)
                       + " IN SHARE ROW EXCLUSIVE MODE")
        missing_by_table = {}
        for entry in manifest["tables"]:
            left, right = records(memory, entry), records(aw, entry)
            report["source_checks"].append(validate_records(entry, left, right))
            if entry["mode"] == "aw_superset":
                missing_by_table[entry["table"]] = sorted(right.keys() - left.keys())
        for table in INSERT_ORDER:
            entry = next(entry for entry in manifest["tables"] if entry["schema"] == "public" and entry["table"] == table)
            payloads = aw.execute("SELECT to_jsonb(r)::text FROM " + relation(entry) + " r WHERE " + key_expression(entry) + "=ANY(%s)",
                                  (missing_by_table[table],)).fetchall()
            count = 0
            for payload, in payloads:
                # Existing rows are never updated. Any conflict is caught by
                # exact union verification before this transaction can commit.
                conflict_key = ",".join(preflight.identifier(key) for key in entry["primary_key"])
                inserted = memory.execute("INSERT INTO " + relation(entry) + " SELECT (jsonb_populate_record(NULL::"
                    + relation(entry) + ",%s::jsonb)).* ON CONFLICT (" + conflict_key + ") DO NOTHING RETURNING 1", (payload,)).fetchone()
                count += inserted is not None
            report["inserted"][table] = count
        report["union_after_insert"] = union_gate(memory, manifest)
        report["foreign_keys_after_insert"] = foreign_key_gate(memory)
    # Verify rerunning the exact insert plan adds nothing and never overwrites.
    with psycopg.connect(memory_url) as memory, psycopg.connect(aw_url) as aw:
        aw.execute("SET TRANSACTION READ ONLY")
        for conn in (memory, aw):
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
        replay = 0
        for table in INSERT_ORDER:
            entry = next(entry for entry in manifest["tables"] if entry["schema"] == "public" and entry["table"] == table)
            for payload, in aw.execute("SELECT to_jsonb(r)::text FROM " + relation(entry) + " r WHERE "
                                       + key_expression(entry) + "=ANY(%s)", (missing_by_table[table],)).fetchall():
                conflict_key = ",".join(preflight.identifier(key) for key in entry["primary_key"])
                inserted = memory.execute("INSERT INTO " + relation(entry) + " SELECT (jsonb_populate_record(NULL::"
                    + relation(entry) + ",%s::jsonb)).* ON CONFLICT (" + conflict_key + ") DO NOTHING RETURNING 1", (payload,)).fetchone()
                replay += inserted is not None
        if replay:
            raise MergeRejected("PRESERVATION_REPLAY_INSERTED_ROWS")
        report["union_after_replay"] = union_gate(memory, manifest)
    report["replay_inserted_rows"] = 0
    report["negative_database_checks"] = negative_checks(memory_url, aw_url, manifest)
    return report


def negative_checks(memory_url: str, aw_url: str, manifest: dict) -> dict:
    """Inject violations only inside rolled-back local transactions."""
    import psycopg
    result = {}
    runs = next(entry for entry in manifest["tables"] if entry["schema"] == "public" and entry["table"] == "runs")
    with psycopg.connect(memory_url) as memory, psycopg.connect(aw_url) as aw:
        for conn in (memory, aw):
            conn.execute("SET LOCAL TIME ZONE 'UTC'")
        before = records(aw, runs)
        aw.execute("UPDATE public.runs SET total_cost_usd=COALESCE(total_cost_usd,0)+0.125 WHERE run_id=(SELECT run_id FROM public.runs ORDER BY run_id LIMIT 1)")
        try:
            validate_records(runs, records(memory, runs), records(aw, runs))
        except MergeRejected as exc:
            result["shared_content_conflict_rejected"] = str(exc).startswith("SHARED_PRIMARY_KEY_CONTENT_CONFLICT:")
        finally:
            aw.rollback()
        aw.execute("SET LOCAL TIME ZONE 'UTC'")
        if records(aw, runs) != before:
            raise MergeRejected("CONFLICT_INJECTION_NOT_ROLLED_BACK")
    with psycopg.connect(memory_url) as conn:
        conn.execute("CREATE TABLE public.convergence_unreviewed(id integer PRIMARY KEY)")
        try:
            require_catalog(conn, manifest)
        except MergeRejected as exc:
            result["unknown_table_rejected"] = str(exc) == "DATABASE_TABLE_INVENTORY_REJECTED"
        finally:
            conn.rollback()
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        require_catalog(conn, manifest)
        conn.execute("ALTER TABLE public.conversations ADD CONSTRAINT convergence_negative_fk FOREIGN KEY (user_id) REFERENCES public.users(user_id) NOT VALID")
        try:
            foreign_key_gate(conn)
        except MergeRejected as exc:
            result["not_valid_foreign_key_rejected"] = str(exc).startswith("UNVALIDATED_FOREIGN_KEY:")
        finally:
            conn.rollback()
        conn.execute("SET LOCAL TIME ZONE 'UTC'")
        result["injections_rolled_back"] = union_gate(conn, manifest)["passed"] and foreign_key_gate(conn)["all_validated"]
    if result != {"shared_content_conflict_rejected": True, "unknown_table_rejected": True,
                  "not_valid_foreign_key_rejected": True, "injections_rolled_back": True}:
        raise MergeRejected("NEGATIVE_DATABASE_CHECK_FAILED")
    return result
