"""Real API verification after isolated volume restart and independent restore.

Call run_recovery(harness, fixture) only after other runs using this acceptance
infrastructure have stopped. This function stops all processes owned by the
provided Harness and only starts short-lived read-verification APIs. It never
starts a Worker, invokes a business command or overwrites a source volume.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.rows import dict_row

from acceptance.runtime import backup_restore, infra
from acceptance.runtime.client import Client, utc_now
from acceptance.runtime.harness import Harness, wait_until
from acceptance.runtime.sql_oracle import (
    assert_application_role, assert_scope_integrity, snapshot_scope,
)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _inventory(env: dict, fixture: dict) -> dict:
    with psycopg.connect(env["APP_DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (fixture["scope_id"],))
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        scope, domain = fixture["scope_id"], fixture["domain_id"]
        objects = conn.execute(
            "SELECT object_id,object_type,lifecycle_status FROM gov_objects WHERE scope_id=%s AND domain_id=%s ORDER BY object_id",
            (scope, domain),
        ).fetchall()
        revisions = conn.execute(
            """SELECT r.object_id,r.revision_id,o.object_type FROM gov_object_revisions r
               JOIN gov_objects o ON (o.scope_id,o.object_id)=(r.scope_id,r.object_id)
               WHERE r.scope_id=%s AND o.domain_id=%s ORDER BY r.object_id,r.revision_id""", (scope, domain),
        ).fetchall()
        receipts = conn.execute(
            "SELECT receipt_id FROM gov_action_receipts WHERE scope_id=%s ORDER BY receipt_id", (scope,),
        ).fetchall()
        snapshots = conn.execute(
            "SELECT snapshot_id FROM gov_context_snapshots WHERE scope_id=%s ORDER BY snapshot_id", (scope,),
        ).fetchall()
    result = {
        "objects": [{key: str(value) for key, value in row.items()} for row in objects],
        "revisions": [{key: str(value) for key, value in row.items()} for row in revisions],
        "receipt_ids": [str(row["receipt_id"]) for row in receipts],
        "snapshot_ids": [str(row["snapshot_id"]) for row in snapshots],
    }
    types = {row["object_type"] for row in result["objects"]}
    assert {"CompanyOutcome", "BusinessCommitment", "ExecutionCommitment", "FeedbackThread",
            "ManagementAdjustment", "Decision", "EvidenceAsset", "MetricObservation", "WorkItem", "Deliverable"} <= types, "recovery requires the exercised business loop"
    assert any(row["object_type"] == "FeedbackThread" and row["lifecycle_status"] == "closed"
               for row in result["objects"]), "recovery requires a previously closed feedback object"
    assert any(row["object_type"] == "WorkItem" and row["lifecycle_status"] == "delivery_accepted"
               for row in result["objects"]), "recovery requires the exercised v0.2 delivery loop"
    assert result["receipt_ids"] and result["snapshot_ids"], "recovery requires immutable receipts and historical context snapshots"
    return result


def _oracles(env: dict, fixture: dict) -> dict:
    with psycopg.connect(env["APP_DATABASE_URL"]) as conn:
        role = assert_application_role(conn)
        integrity = assert_scope_integrity(conn, fixture["scope_id"], fixture["tenant_id"], fixture["company_id"])
        snapshot = snapshot_scope(conn, fixture["scope_id"], fixture["tenant_id"], fixture["company_id"])
    return {"application_role": role, "scope_integrity": integrity, "scope_snapshot": snapshot}


def _capture(harness: Harness, fixture: dict, env: dict, inventory: dict, stage: str) -> dict:
    ready_file = harness.private / f"recovery-{stage}-{uuid.uuid4().hex}-ready.json"
    updates = {key: value for key, value in env.items()
               if key in {"DATABASE_URL", "MEMORY_TENANT", "MEMORY_ORG"}
               or key.startswith("TKOS_OBJECT_STORE_")}
    updates.update(MEMORY_TENANT=fixture["tenant_id"], MEMORY_ORG=fixture["company_id"])
    api = harness.spawn("recovery-" + stage, "server.py", "--port", 0,
                        "--control-dir", harness.control, "--key-file", harness.key_file,
                        "--ready-file", ready_file, updates=updates)
    client = None
    try:
        ready = wait_until(lambda: json.loads(ready_file.read_text()) if ready_file.exists() else None,
                           timeout=25, message="recovery API did not start")
        harness.wait_http(ready["url"] + "/v1/health")
        client = Client(ready["url"], fixture["actors"]["ceo"]["token"], "recovery-ceo-" + stage, harness.log)
        result = {"objects": {}, "revisions": {}, "receipts": {}, "snapshots": {}, "evidence": {}}
        for row in inventory["objects"]:
            result["objects"][row["object_id"]] = client.object(row["object_id"])
        for row in inventory["revisions"]:
            oid, rid = row["object_id"], row["revision_id"]
            revision = client.revision(oid, rid)
            result["revisions"][rid] = revision
            if row["object_type"] == "EvidenceAsset":
                metadata = revision["payload"]
                response = client.request("GET", f"/v1/evidence-assets/{oid}/revisions/{rid}")
                digest = hashlib.sha256(response.content).hexdigest()
                assert digest == metadata["sha256"] and len(response.content) == metadata["length"], "API evidence bytes differ from immutable metadata"
                result["evidence"][rid] = {
                    "object_id": oid, "revision_id": rid, "bucket": metadata["bucket"],
                    "key": metadata["key"], "version_id": metadata["version_id"],
                    "sha256": digest, "length": len(response.content),
                }
        for receipt_id in inventory["receipt_ids"]:
            result["receipts"][receipt_id] = client.json("GET", f"/v1/action-receipts/{receipt_id}")
        for snapshot_id in inventory["snapshot_ids"]:
            result["snapshots"][snapshot_id] = client.json("GET", f"/v1/context-packs/{snapshot_id}")
        delivered = [o for o in result["objects"].values() if o["object_type"] == "WorkItem" and o["lifecycle_status"] == "delivery_accepted"]
        assert delivered, "no accepted delivery survived recovery"
        assert any(o["delivery"]["state"]["submission_seq"] == 2 and len(o["delivery"]["submissions"]) == 2
                   and [a["verification_result"] for a in o["delivery"]["acceptances"]] == ["changes_requested", "accepted"]
                   for o in delivered), "versioned submission/review history did not survive"
        assert any(o.get("outcome_achievement") == "achieved" for o in result["objects"].values()), "Outcome judgment did not survive"
        assert result["evidence"], "no real evidence bytes were verified"
        return result
    finally:
        if client is not None:
            client.close()
        harness.stop(api)


def _compare(before: dict, after: dict, stage: str) -> dict:
    categories = ("objects", "revisions", "receipts", "snapshots", "evidence")
    for category in categories:
        assert before[category] == after[category], f"{stage}: persisted {category} changed"
    return {category: {"equal": True, "count": len(before[category]), "sha256": _hash(before[category])}
            for category in categories}


def run_recovery(harness: Harness, fixture: dict) -> dict:
    """Stop this Harness, restart original volumes, restore new storage, assert.

    All other acceptance runs must also be quiesced. The returned report is safe
    to publish: it contains IDs, hashes and role metadata, never tokens or DSNs.
    The restored storage is retained and its private environment path is reported.
    The source stack is running afterwards; no API or Worker is left running.
    """
    harness.stop_all()
    assert all(process.poll() is not None for _, process in harness.processes), "a Harness writer is still running"
    source_env = dict(harness.env)
    actual = infra.load_environment()
    assert source_env["APP_DATABASE_URL"] == actual["APP_DATABASE_URL"], "recovery must target the dedicated source acceptance database"
    started = utc_now()
    with harness.group("09_volume_restart_and_independent_backup_restore") as result:
        inventory = _inventory(source_env, fixture)
        before_sql = _oracles(source_env, fixture)
        before = _capture(harness, fixture, source_env, inventory, "before")
        # Use Compose start, rather than infra.up(): a persistence test must not
        # rerun schema/bootstrap/bucket mutations or recreate a missing volume.
        stopped_at = utc_now()
        infra.run(infra.compose("stop", "--timeout", "30", "postgres", "minio"))
        try:
            infra.run(infra.compose("start", "--wait", "--wait-timeout", "90", "postgres", "minio"))
        finally:
            # On a failed readiness check restore availability without deleting
            # either source container or its named volume.
            infra.run(infra.compose("start", "postgres", "minio"))
        restarted_at = utc_now()
        assert _inventory(source_env, fixture) == inventory, "source inventory changed after volume restart"
        restarted = _capture(harness, fixture, source_env, inventory, "restarted")
        restart_compare = _compare(before, restarted, "source restart")
        restart_sql = _oracles(source_env, fixture)
        assert before_sql["scope_snapshot"] == restart_sql["scope_snapshot"], "source DB row hashes changed after restart"

        backup = backup_restore.restore_backup()
        restored_env = json.loads(Path(backup["private_env_file"]).read_text())
        assert restored_env["APP_DATABASE_URL"] != source_env["APP_DATABASE_URL"]
        assert restored_env["TKOS_OBJECT_STORE_ENDPOINT"] != source_env["TKOS_OBJECT_STORE_ENDPOINT"]
        assert _inventory(restored_env, fixture) == inventory, "restored inventory differs"
        restored = _capture(harness, fixture, restored_env, inventory, "restored")
        restore_compare = _compare(before, restored, "independent restore")
        restore_sql = _oracles(restored_env, fixture)
        assert before_sql["scope_snapshot"] == restore_sql["scope_snapshot"], "restored DB row hashes differ"
        result.update(
            started_at=started, source_stopped_at=stopped_at, source_restarted_at=restarted_at,
            fixture_scope_id=fixture["scope_id"], source_inventory=inventory,
            restart=restart_compare, independent_restore=restore_compare, backup=backup,
            source_sql=before_sql, restarted_sql=restart_sql, restored_sql=restore_sql,
            source_storage_preserved=True, all_verification_api_processes_stopped=True,
            boundary="new database in the same PostgreSQL instance and a separate MinIO volume; not whole-host disaster recovery",
        )
    report_file = harness.output / "recovery-report.json"
    report_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-file", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--writers-quiesced", action="store_true", required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture_file.read_text())
    harness = Harness(run_id=args.run_id or "recovery-" + uuid.uuid4().hex[:12])
    try:
        result = run_recovery(harness, fixture)
        print(json.dumps({"recovery_verified": result["status"] == "passed",
                          "report": str(harness.output / "recovery-report.json"),
                          "private_restored_environment": result["backup"]["private_env_file"]}))
    finally:
        harness.stop_all()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, AssertionError, OSError) as exc:
        print(infra.redact(f"acceptance recovery failed: {exc}"), file=sys.stderr)
        raise SystemExit(1) from exc
