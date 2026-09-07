#!/usr/bin/env python3
"""Read-only remote inventory and an unapplied convergence plan; stdlib only.

The default command never contacts a host. --collect executes metadata reads and
bounded, read-only SQL through the named PostgreSQL containers. It never reads
environment files, emits credentials/business text, creates backups or applies
migrations. A successful preflight is not a cutover approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HOST = "tokenhub-prod"
DB_CONTAINERS = (
    "tkos-memory-postgres-1", "aw-workspace-pg", "tkos-target-staging-postgres-1",
)
CONTAINERS = (
    "tkos-memory-memory-service-1", *DB_CONTAINERS,
    "tkos-target-staging-memory-api-1", "tkos-target-staging-memory-worker-1",
    "tkos-target-staging-minio-1", "tkos-target-staging-nginx-1",
)
# Identity/credential tables may be counted, but are deliberately never hashed.
FINGERPRINT_TABLES = {
    "semantic_entities", "semantic_relations", "semantic_entity_source_refs",
    "semantic_relation_source_refs", "context_graph_versions", "documents",
    "document_fragments", "wm_objects", "wm_object_versions", "wm_issue_chains",
    "wm_version_source_refs", "wm_issue_signals", "wm_agreement_parties",
    "wm_agreement_confirmations", "memory_proposals", "memory_audit",
}
SCOPE_TABLES = {"semantic_entities", "semantic_relations", "documents", "wm_issue_chains"}
ROW_COMPARE_TABLES = {
    "wm_issue_chains": ("chain_id",), "wm_objects": ("object_id",),
    "wm_object_versions": ("record_id",),
    "wm_version_source_refs": ("record_id", "fragment_id"),
    "wm_issue_signals": ("issue_object_id", "signal_object_id"),
    "wm_agreement_parties": ("agreement_record_id", "party"),
    "wm_agreement_confirmations": ("agreement_record_id", "confirmer"),
}
FINGERPRINT_ROW_CAP = 10000


class ReadFailed(Exception):
    """Safe error: remote stderr can contain connection identities or paths."""


def remote(arguments: list[str], *, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST,
         shlex.join(arguments)], input=stdin, capture_output=True, text=True, timeout=40,
        check=False,
    )
    if result.returncode:
        raise ReadFailed("REMOTE_READ_FAILED")
    return result.stdout


def sql(container: str, query: str, database: str | None = None) -> list:
    if container not in DB_CONTAINERS:
        raise ValueError("Database container is not in the fixed allowlist")
    database_arg = shlex.quote(database) if database else '"${POSTGRES_DB:-postgres}"'
    command = ('exec psql -X -q -A -t -v ON_ERROR_STOP=1 '
               '-U "${POSTGRES_USER:-postgres}" -d ' + database_arg)
    # No grants, schema writes, temp tables, function definitions or DML.
    wrapped = ("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
               "SET LOCAL statement_timeout='8s';\nSET LOCAL lock_timeout='1s';\n"
               "SET LOCAL TIME ZONE 'UTC';\n" + query + "\nROLLBACK;\n")
    output = remote(["docker", "exec", "-i", container, "sh", "-c", command], stdin=wrapped)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ReadFailed("UNEXPECTED_SCHEMA_IDENTIFIER")
    return '"' + value + '"'


def database_inventory(container: str, database: str, slot: int) -> dict:
    metadata = sql(container, """
SELECT json_build_object(
  'server_version', current_setting('server_version'),
  'tables', COALESCE((SELECT json_agg(json_build_object(
    'schema', schemaname, 'table', tablename) ORDER BY schemaname,tablename)
    FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')), '[]'),
  'governed_rls', COALESCE((SELECT json_agg(json_build_object(
    'table', c.relname, 'enabled', c.relrowsecurity, 'forced', c.relforcerowsecurity)
    ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r' AND c.relname LIKE 'gov_%'), '[]')
);
""", database)[0]
    tables = [entry["table"] for entry in metadata["tables"] if entry["schema"] == "public"]
    queries = []
    for table in tables:
        quoted = identifier(table)
        queries.append(f"SELECT json_build_object('kind','count','table','{table}',"
                       f"'rows',count(*)) FROM public.{quoted};")
        if table in FINGERPRINT_TABLES:
            # Hashes are comparison diagnostics only, never imported business rows.
            queries.append(f"""
SELECT json_build_object('kind','fingerprint','table','{table}',
 'algorithm','sha256-sorted-row-json-utc-v1',
 'row_cap',{FINGERPRINT_ROW_CAP}, 'complete',total.n <= {FINGERPRINT_ROW_CAP},
 'sha256',CASE WHEN total.n <= {FINGERPRINT_ROW_CAP} THEN (
   SELECT encode(sha256(convert_to(COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex')
   FROM (SELECT encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') AS h
         FROM public.{quoted} r LIMIT {FINGERPRINT_ROW_CAP + 1}) hashes
 ) ELSE NULL END) FROM (SELECT count(*) AS n FROM public.{quoted}) total;
""")
        if table in SCOPE_TABLES:
            queries.append(f"""
SELECT json_build_object('kind','scope_counts','table','{table}',
 'scope_count',count(*),'sorted_row_counts',COALESCE(json_agg(n ORDER BY n),'[]'))
 FROM (SELECT count(*) AS n FROM public.{quoted}
       GROUP BY tenant_id,organization_id) scopes;
""")
    if "schema_migrations" in tables:
        queries.append("SELECT json_build_object('kind','migrations','names',"
                       "COALESCE(json_agg(name ORDER BY name),'[]')) FROM public.schema_migrations;")
    if "context_graph_versions" in tables:
        queries.append("SELECT json_build_object('kind','graph_status','status',status,'rows',count(*)) "
                       "FROM public.context_graph_versions GROUP BY status ORDER BY status;")
    rows = sql(container, "\n".join(queries), database) if queries else []
    constraints = sql(container, """
SELECT json_build_object('source_table',s.relname,'target_table',t.relname,
 'validated',c.convalidated,
 'source_columns',(SELECT json_agg(a.attname ORDER BY u.ord) FROM unnest(c.conkey)
   WITH ORDINALITY u(num,ord) JOIN pg_attribute a ON a.attrelid=s.oid AND a.attnum=u.num),
 'target_columns',(SELECT json_agg(a.attname ORDER BY u.ord) FROM unnest(c.confkey)
   WITH ORDINALITY u(num,ord) JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=u.num))
 FROM pg_constraint c JOIN pg_class s ON s.oid=c.conrelid JOIN pg_class t ON t.oid=c.confrelid
 JOIN pg_namespace n ON n.oid=s.relnamespace JOIN pg_namespace tn ON tn.oid=t.relnamespace
 WHERE c.contype='f' AND n.nspname='public' AND tn.nspname='public'
 ORDER BY s.relname,c.conname;
""", database)
    orphan_queries = []
    for index, constraint in enumerate(constraints):
        source = identifier(constraint["source_table"])
        target = identifier(constraint["target_table"])
        pairs = list(zip(constraint["source_columns"], constraint["target_columns"]))
        # Existing legacy FKs use MATCH SIMPLE: any NULL skips the parent check.
        present = " AND ".join(f"s.{identifier(a)} IS NOT NULL" for a, _ in pairs)
        equal = " AND ".join(f"s.{identifier(a)} = t.{identifier(b)}" for a, b in pairs)
        orphan_queries.append(f"SELECT json_build_object('index',{index},'orphan_rows',count(*)) "
                              f"FROM public.{source} s WHERE {present} AND NOT EXISTS "
                              f"(SELECT 1 FROM public.{target} t WHERE {equal});")
    for check in sql(container, "\n".join(orphan_queries), database) if orphan_queries else []:
        constraints[check["index"]]["orphan_rows"] = check["orphan_rows"]
    return {"database_slot": slot, **metadata, "statistics": rows, "foreign_keys": constraints}


def collect() -> dict:
    snapshot = {
        "schema": "tkos.convergence.preflight/1", "observed_at": datetime.now(timezone.utc).isoformat(),
        "host": HOST, "read_only": True, "business_text_collected": False,
        "credentials_collected": False, "containers": [], "databases": [], "errors": [],
    }
    # Fetch only explicitly named Docker fields; never inspect Config.Env.
    template = ('{"image":{{json .Config.Image}},"image_id":{{json .Image}},'
                '"ports":{{json .NetworkSettings.Ports}},'
                '"networks":{{json .NetworkSettings.Networks}},'
                '"running":{{json .State.Running}}}')
    for container in CONTAINERS:
        try:
            row = json.loads(remote(["docker", "inspect", "--format", template, container]))
            row["networks"] = sorted(row["networks"])
            snapshot["containers"].append({"name": container, **row})
        except (ReadFailed, subprocess.TimeoutExpired, json.JSONDecodeError):
            snapshot["errors"].append({"container": container, "code": "CONTAINER_READ_FAILED"})
    try:
        output = remote(["systemctl", "show", "aw-memory-api.service", "-p", "ActiveState",
                         "-p", "SubState", "-p", "WorkingDirectory", "-p", "FragmentPath"])
        snapshot["aw_memory_service"] = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    except (ReadFailed, subprocess.TimeoutExpired):
        snapshot["errors"].append({"service": "aw-memory-api", "code": "SERVICE_READ_FAILED"})
    source_databases: dict[str, list[str]] = {}
    for container in DB_CONTAINERS:
        try:
            # Database names are used only inside this process and never emitted.
            databases = sql(container, "SELECT to_json(datname) FROM pg_database "
                            "WHERE NOT datistemplate ORDER BY datname;")
            for slot, database in enumerate(databases):
                row = database_inventory(container, database, slot)
                snapshot["databases"].append({"container": container, **row})
                if container in DB_CONTAINERS[:2] and any(
                    entry["table"] == "semantic_entities" and entry["schema"] == "public"
                    for entry in row["tables"]
                ):
                    source_databases.setdefault(container, []).append(database)
        except (ReadFailed, subprocess.TimeoutExpired, json.JSONDecodeError):
            snapshot["errors"].append({"container": container, "code": "DATABASE_READ_FAILED"})
    if all(len(source_databases.get(container, [])) == 1 for container in DB_CONTAINERS[:2]):
        snapshot["working_memory_row_comparison"] = []
        for table, primary_key in ROW_COMPARE_TABLES.items():
            try:
                records = []
                for container in DB_CONTAINERS[:2]:
                    key_json = "jsonb_build_array(" + ",".join(identifier(key) for key in primary_key) + ")::text"
                    rows = sql(container, f"""
SELECT json_build_object('key',encode(sha256(convert_to({key_json},'UTF8')),'hex'),
 'row',encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex'))
 FROM public.{identifier(table)} r LIMIT {FINGERPRINT_ROW_CAP + 1};
""", source_databases[container][0])
                    records.append({row["key"]: row["row"] for row in rows})
                left, right = records
                complete = max(map(len, records)) <= FINGERPRINT_ROW_CAP
                shared = left.keys() & right.keys()
                snapshot["working_memory_row_comparison"].append({
                    "table": table, "complete": complete,
                    "legacy_only_rows": len(left.keys() - right.keys()) if complete else None,
                    "aw_only_rows": len(right.keys() - left.keys()) if complete else None,
                    "same_id_equal_rows": sum(left[key] == right[key] for key in shared) if complete else None,
                    "same_id_changed_rows": sum(left[key] != right[key] for key in shared) if complete else None,
                    "cross_database_atomic_snapshot": False,
                })
                # Record and primary-key digests never enter the output report.
            except (ReadFailed, subprocess.TimeoutExpired, json.JSONDecodeError):
                snapshot["errors"].append({"table": table, "code": "ROW_COMPARISON_FAILED"})
    return snapshot


def comparison(snapshot: dict) -> dict:
    sources = {}
    for container in DB_CONTAINERS[:2]:
        candidates = [db for db in snapshot.get("databases", []) if db["container"] == container
                      and any(t["table"] == "semantic_entities" and t["schema"] == "public" for t in db["tables"])]
        if len(candidates) != 1:
            return {"comparable": False, "reason": "EXACT_SOURCE_DATABASE_NOT_UNIQUE"}
        sources[container] = {s["table"]: s for s in candidates[0]["statistics"] if s["kind"] == "fingerprint"}
    names = sorted(set(sources[DB_CONTAINERS[0]]) | set(sources[DB_CONTAINERS[1]]))
    results = []
    for table in names:
        left, right = (sources[name].get(table) for name in DB_CONTAINERS[:2])
        comparable = bool(left and right and left["complete"] and right["complete"])
        results.append({"table": table, "comparable": comparable,
                        "equal": left["sha256"] == right["sha256"] if comparable else None})
    return {"comparable": bool(results) and all(r["comparable"] for r in results),
            "tables": results, "equal_fingerprints_do_not_establish_authority": True,
            "cross_database_atomic_snapshot": False}


def plan(snapshot: dict | None) -> dict:
    migrations = [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                  for p in sorted((ROOT / "src/memory_service_app/migrations").glob("*.sql"))]
    result = {
        "schema": "tkos.convergence.plan/1", "mode": "plan_only", "applied": False,
        "ready_for_production_replacement": False,
        "target_migrations": migrations, "database_gaps": [],
        "required_gates": [
            "Owner-approved source of truth and legacy-to-governed mapping, including conflicts",
            "Complete PostgreSQL backups, roles/grants, source evidence objects and release/config manifest",
            "Isolated restore with counts/hashes, foreign keys, roles/RLS and evidence byte verification",
            "0015/0016/0017 migrations applied only on the isolated restore; governed records explicitly imported/reviewed",
            "Real identities, assignments, policies and least-privilege application role; no acceptance fixture bootstrap",
            "Narrative contract and authorized authoritative read comparison; no model-derived fact promotion",
            "Target Runtime API/worker/receiver idempotency and the full DRI/Outcome/MF gate",
            "Reviewed write fence/delta catch-up, reversible read canary and rollback manifest",
            "Explicit cutover authorization after these concrete artifacts are reviewed",
        ],
        "rollback_rule": "Read-only canary may route back; after first governed write, preserve target writes and reconcile before rollback. Never discard the target database or replay receipts blindly.",
        "not_performed": ["backup", "restore", "migration", "business_import", "permission_changes",
                          "service_restart", "traffic_cutover", "external_narrative_request"],
    }
    if snapshot is None:
        result["blockers"] = ["NO_CURRENT_READ_ONLY_PREFLIGHT", "EXTERNAL_ACCEPTANCE_GATES_NOT_VERIFIED"]
        return result
    result["observed_at"] = snapshot.get("observed_at")
    result["source_comparison"] = comparison(snapshot)
    result["working_memory_row_comparison"] = snapshot.get("working_memory_row_comparison", [])
    row_comparison = result["working_memory_row_comparison"]
    source_dbs = [db for db in snapshot.get("databases", []) if db["container"] in DB_CONTAINERS[:2]
                  and any(t["table"] == "semantic_entities" and t["schema"] == "public" for t in db["tables"])]
    subset_checked = (
        len(row_comparison) == len(ROW_COMPARE_TABLES)
        and {row["table"] for row in row_comparison} == set(ROW_COMPARE_TABLES)
        and all(row["complete"] and row["aw_only_rows"] == 0 and row["same_id_changed_rows"] == 0
                for row in row_comparison)
        and len(source_dbs) == 2 and all(db.get("foreign_keys") for db in source_dbs)
        and all(fk["validated"] and fk["orphan_rows"] == 0 for db in source_dbs for fk in db["foreign_keys"])
        and result["source_comparison"]["comparable"]
        and all(row["equal"] is True or row["table"] in ROW_COMPARE_TABLES
                for row in result["source_comparison"]["tables"])
        and not snapshot.get("errors")
    )
    result["source_recommendation"] = {
        "candidate": None,
        "status": "two_source_preservation_manifest_required",
        "working_memory_subset_checked": subset_checked,
        "basis": "Working-memory subset checks do not cover AW-only runtime history; preserve both sources under the full 53-table manifest",
        "governed_authority_import_approved": False,
        "requires_fresh_write_fenced_comparison": True,
        "does_not_cover": ["unregistered tables", "external object bytes", "employee identity rights", "semantic mapping"],
    }
    for database in snapshot.get("databases", []):
        if not database["tables"]:
            continue
        applied = next((r["names"] for r in database["statistics"] if r["kind"] == "migrations"), [])
        result["database_gaps"].append({
            "container": database["container"], "database_slot": database["database_slot"],
            "missing_migrations": [m["name"] for m in migrations if m["name"] not in applied],
            "unknown_migrations": [name for name in applied if name not in {m["name"] for m in migrations}],
            "governed_tables": len(database["governed_rls"]),
        })
    result["blockers"] = ["EXTERNAL_ACCEPTANCE_GATES_NOT_VERIFIED", "NO_AUTHORIZED_CUTOVER_MANIFEST"]
    if snapshot.get("errors"):
        result["blockers"].append("PREFLIGHT_INCOMPLETE")
    if any(db["missing_migrations"] for db in result["database_gaps"]):
        result["blockers"].append("SOURCE_SCHEMA_HAS_NOT_BEEN_REHEARSED_AT_TARGET_VERSION")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--collect", action="store_true", help="Read the fixed host/containers; never mutate remote state")
    source.add_argument("--snapshot", type=Path, help="Generate plan using an existing sanitized preflight")
    parser.add_argument("--output", type=Path, help="Write sanitized JSON; refuses to overwrite an existing file")
    args = parser.parse_args()
    snapshot = collect() if args.collect else (json.loads(args.snapshot.read_text()) if args.snapshot else None)
    if isinstance(snapshot, dict) and "preflight" in snapshot:
        snapshot = snapshot["preflight"]
    if snapshot is not None and snapshot.get("schema") != "tkos.convergence.preflight/1":
        parser.error("Expected a tkos.convergence.preflight/1 snapshot")
    output = {"preflight": snapshot, "plan": plan(snapshot)} if args.collect else plan(snapshot)
    encoded = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf8") as file:
            file.write(encoded)
        print(json.dumps({"mode": "plan_only", "applied": False, "output": str(args.output)}))
    else:
        sys.stdout.write(encoded)


if __name__ == "__main__":
    main()
