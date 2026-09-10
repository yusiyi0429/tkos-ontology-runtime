"""Capture real pre-A1 HTTP history and verify it after root-managed migration.

capture seeds only identities/authority/starting fact using exported old source;
all commitments, acceptance, delivery and snapshots are produced by old HTTP.
This tool neither creates nor drops a database and never applies migrations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from acceptance.runtime import sql_oracle
from .legacy_flow import LegacyFlow
from .support import Harness, HERE, ROOT, digest, public_json, source_manifest


def historic_sql(harness: Harness, fixture: dict, template: dict) -> dict:
    """Hash original columns only, preserving the pre-migration history oracle."""
    output = {}
    with harness.app_connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SET LOCAL TIME ZONE 'UTC'")
        cursor.execute("SET LOCAL DateStyle='ISO, YMD'")
        cursor.execute("SELECT set_config('app.governed_scope_id',%s,true)", (fixture["scope_id"],))
        tables = sql_oracle._tables(cursor)
        by_name = {t["table_name"]: t for t in tables}
        columns = {name: sql_oracle._columns(cursor, table["oid"]) for name, table in by_name.items()}
        foreign_keys = sql_oracle._foreign_keys(cursor)
        for name, original in template["tables"].items():
            if name not in by_name:
                raise AssertionError("migration removed an original governed table")
            selected = original["hashed_columns"]
            if not set(selected) <= set(columns[name]):
                raise AssertionError("migration removed original governed columns")
            predicate, params, _ = sql_oracle._scope_filter(name, columns, foreign_keys,
                fixture["scope_id"], fixture["tenant_id"], fixture["company_id"])
            pairs = []
            for col in selected:
                pairs.extend([sql.Literal(col), sql.Identifier("row_data", col)])
            canonical = sql.SQL("jsonb_build_object({})::text").format(sql.SQL(", ").join(pairs))
            cursor.execute(sql.SQL("SELECT canonical_row FROM (SELECT {} AS canonical_row FROM {} AS row_data WHERE {}) rows ORDER BY canonical_row COLLATE \"C\"").format(
                canonical, sql.Identifier("public", name), predicate), params)
            count, hasher = 0, hashlib.sha256()
            for row in cursor:
                raw = row["canonical_row"].encode()
                hasher.update(len(raw).to_bytes(8, "big"))
                hasher.update(raw)
                count += 1
            output[name] = {"row_count": count, "sha256": hasher.hexdigest(), "hashed_columns": selected}
    return output


def old_subset(before, after, *, location="root") -> None:
    """New projection keys may be additive; every original value stays exact.

    This only applies to read projections. Original SQL rows and stored receipt
    JSON are independently compared and have no additive-key allowance.
    """
    if isinstance(before, dict):
        assert isinstance(after, dict), f"projection type changed at {location}"
        for key, value in before.items():
            assert key in after, f"projection field disappeared at {location}.{key}"
            old_subset(value, after[key], location=f"{location}.{key}")
    elif isinstance(before, list):
        assert isinstance(after, list) and len(before) == len(after), f"projection list changed at {location}"
        for i, (a, b) in enumerate(zip(before, after, strict=True)):
            old_subset(a, b, location=f"{location}[{i}]")
    else:
        assert before == after, f"original projection value changed at {location}"


def capture(args) -> dict:
    harness = Harness(args.env_file, args.output, args.private)
    source = args.source.resolve()
    old_hash = json.loads((HERE / "legacy-request-golden.json").read_text())["source_files_sha256"]
    for relative, expected in old_hash.items():
        assert hashlib.sha256((source / relative).read_bytes()).hexdigest() == expected, "old source drifted"
    with psycopg.connect(harness.env.values["MIGRATION_DATABASE_URL"]) as conn:
        migrations = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
    assert migrations and migrations[-1] == "0017_dri_delivery.sql", "capture requires a real pre-A1 schema"
    fixture_file = args.private / "legacy-fixture.json"
    process = harness.spawn("old-seed", [sys.executable, "-I", str(HERE / "source_process.py"), "seed",
        "--source", str(source), "--out", str(fixture_file), "--namespace",
        f"runtime-acceptance-a1-history-{uuid.uuid4().hex[:12]}"], owner=True)
    process.wait(timeout=30)
    assert process.returncode == 0, "old source identity bootstrap failed"
    fixture = json.loads(fixture_file.read_text())
    flow = None
    try:
        api, url, ready = harness.start_api(source, old=True, updates={
            "MEMORY_TENANT": fixture["tenant_id"], "MEMORY_ORG": fixture["company_id"]})
        flow = LegacyFlow(harness, url, fixture)
        result = flow.build()
        snapshot = flow.capture()
        snapshot["context_snapshot"] = flow.ceo.json("GET", f"/v1/context-packs/{result['snapshot_id']}")
        document = {"source_kind": "real_pre_a1_api", "legacy_git_head": "3cd9109d726a9a9069a7960a2f2665ce677785d2",
            "source_manifest": source_manifest(source), "migrations": migrations, "api_provenance": json.loads(ready.read_text()),
            "fixture_file": str(fixture_file), "scope_id": fixture["scope_id"], "flow": result, "history": snapshot,
            "a1_accepted": False}
        public_json(args.output / "pre-a1-history.json", document)
        return {"captured": True, "source_kind": document["source_kind"], "legacy_http_commands": len(flow.commands),
            "objects": len(flow.objects), "fixture_file": str(fixture_file),
            "history_file": str(args.output / "pre-a1-history.json"), "database_migrated": False}
    finally:
        if flow:
            flow.close()
        harness.close()


def verify(args) -> dict:
    harness = Harness(args.env_file, args.output, args.private)
    document = json.loads(args.history.read_text())
    fixture = json.loads(args.fixture.read_text())
    assert document["scope_id"] == fixture["scope_id"]
    before = document["history"]
    flow = None
    try:
        _, url, ready = harness.start_api(args.source, updates={"MEMORY_TENANT": fixture["tenant_id"],
                                                                "MEMORY_ORG": fixture["company_id"]})
        flow = LegacyFlow(harness, url, fixture)
        checks = []
        projected = historic_sql(harness, fixture, before["sql_snapshot"])
        for table, prior in before["sql_snapshot"]["tables"].items():
            assert projected[table]["sha256"] == prior["sha256"], f"historical table changed: {table}"
            assert projected[table]["row_count"] == prior["row_count"], f"historical row count changed: {table}"
            checks.append({"kind": "original_table", "table": table, "passed": True})
        for oid, original in before["objects"].items():
            old_subset(original, flow.ceo.object(oid), location=f"object:{oid}")
            checks.append({"kind": "object", "id": oid, "passed": True})
        for rid, original in before["revisions"].items():
            oid = original["object_id"]
            old_subset(original, flow.ceo.revision(oid, rid), location=f"revision:{rid}")
            checks.append({"kind": "revision", "id": rid, "passed": True})
        for rid, original in before["receipts"].items():
            after = flow.ceo.json("GET", f"/v1/action-receipts/{rid}")
            old_subset(original, after, location=f"receipt:{rid}")
            checks.append({"kind": "receipt", "id": rid, "passed": True})
        snapshot = flow.ceo.json("GET", f"/v1/context-packs/{document['flow']['snapshot_id']}")
        old_subset(before["context_snapshot"], snapshot)
        checks.append({"kind": "context_snapshot", "passed": True})
        for evidence in before["evidence"]:
            raw = flow.ceo.request("GET", f"/v1/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}").content
            assert raw.hex() == evidence["bytes_hex"] and hashlib.sha256(raw).hexdigest() == evidence["sha256"]
            checks.append({"kind": "original_evidence_bytes", "id": evidence["revision_id"], "passed": True})
        stable = harness.snapshot(fixture)
        for event in before["commands"]:
            replay = flow.clients[event["actor"]].json("POST", "/v1/actions", event["request"])
            assert replay == event["receipt"], "old request did not replay the exact original receipt"
            checks.append({"kind": "old_request_replay", "id": replay["receipt_id"], "passed": True})
        assert harness.snapshot(fixture) == stable, "historical replay produced business writes"
        assert harness.storage_snapshot(fixture["scope_id"]) == before["s3_snapshot"], "original storage inventory changed"
        result = {"gate": "pre_a1_history_preservation", "passed": len(checks), "failed": 0,
            "checks": checks, "api_provenance": json.loads(ready.read_text()),
            "source_manifest": source_manifest(args.source), "contract_a1_accepted": False}
        public_json(args.output / "history-verification.json", result)
        return {k: v for k, v in result.items() if k not in {"checks", "source_manifest"}}
    finally:
        if flow:
            flow.close()
        harness.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "verify"))
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--fixture", type=Path)
    args = parser.parse_args()
    if args.mode == "verify" and (not args.history or not args.fixture):
        parser.error("verify requires --history and --fixture")
    result = capture(args) if args.mode == "capture" else verify(args)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
