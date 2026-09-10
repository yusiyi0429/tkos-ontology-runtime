"""Re-verify root-owned migration/old-worker observations against this live run.

A stored passed flag is insufficient: exact source identities, captured outcomes,
SQLSTATEs, HTTP delta/body and current immutable receipt are checked independently.
No migration, process release or database lifecycle operation occurs here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from .control_adapter import NotReady
from .support import digest


def stable(report, manifest):
    assert report.get("source_unchanged") is True
    assert report.get("source_manifest_start") == report.get("source_manifest_end") == manifest, \
        "root evidence and this acceptance run used different source bytes"


def verify_migration(h, path, manifest, source):
    if path is None or not path.is_file():
        raise NotReady("actual root migration replay evidence has not been produced")
    report = json.loads(path.read_text())
    stable(report, manifest)
    assert report["database"] == conninfo_to_dict(h.env.values["APP_DATABASE_URL"])["dbname"]
    assert report["first"]["applied"] == ["0018_method_protocol.sql"]
    assert report["second"]["applied"] == []
    for key in ("first", "second"):
        assert Path(report[key]["source"]).resolve() == source.resolve()
    with h.app_connection() as conn:
        rows = conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
    applied = [row["name"] for row in rows]
    assert applied[-2:] == ["0017_dri_delivery.sql", "0018_method_protocol.sql"]
    assert len(applied) == len(set(applied))
    return {"passed": True, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "database": report["database"], "first_applied": report["first"]["applied"],
            "second_applied": [], "live_schema": applied, "source_manifest_matches": True}


def _provenance(result, source, manifest):
    assert Path(result["source"]).resolve() == source.resolve()
    assert result["database_role_is_app"] is True and result["capability_injected_by_probe"] is False
    for name, record in result["provenance"].items():
        path = Path(record["path"]).resolve()
        assert path.is_relative_to(source.resolve())
        relative = path.relative_to(source.resolve()).as_posix()
        assert record["sha256"] == manifest[relative] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert {"memory_service_runtime.repository", "memory_service_runtime.governed.effects",
            "memory_service_runtime.governed.db", "memory_service_runtime.handlers"} <= result["provenance"].keys()


def verify_worker(h, path, preparation_path, *, manifest, source, old_source, history):
    if not all(item and item.is_file() for item in (path, preparation_path, history)) or old_source is None:
        raise NotReady("real root preclaimed-worker cutover evidence is not available")
    report, preparation, old_history = [json.loads(item.read_text()) for item in (path, preparation_path, history)]
    stable(report, manifest)
    assert report["gate"] == "real_old_worker_cutover_subset"
    assert preparation["phase"] == "old_task_claimed_in_memory"
    assert preparation["preupgrade_history_scope_unchanged"] is True
    assert preparation["business_transitions_from_real_old_http"] is True
    assert Path(preparation["old_source"]).resolve() == old_source.resolve()
    assert old_history["legacy_git_head"] == "3cd9109d726a9a9069a7960a2f2665ce677785d2"
    old_manifest = old_history["source_manifest"]
    checks = report["checks"]
    held, once, current = [checks[name] for name in ("preclaimed_old_task", "old_worker_once", "current_legacy_dispatch")]
    assert held["task_id"] == current["task_id"] == preparation["task_id"], \
        "cutover evidence did not dispatch the actual pre-upgrade held task"
    for item in (held, once):
        _provenance(item, old_source, old_manifest)
        assert item["legacy"] is True and not item.get("unexpected_error")
        assert item["sql_unchanged"] is True and item["receiver_calls"] == 0
    assert held["operation"] == "held-claim" and held["held_before_upgrade"] is True
    assert held["successful_dispatch"] is False and held["error_code"] == "governance_effect_scope_invalid"
    assert held["receiver_unchanged"] is True
    assert held["before_sql_sha256"] == held["after_sql_sha256"]
    assert held["already_admitted_http_revocation_claimed"] is False
    assert once["operation"] == "worker-once" and once["sqlstate"] == "55000" and once["eligible_queued_tasks"] >= 1
    _provenance(current, source, manifest)
    assert current["legacy"] is False and current["successful_dispatch"] is True and not current.get("unexpected_error")
    assert current["sql_unchanged"] is True and current["before_sql_sha256"] == current["after_sql_sha256"]
    before, after = current["receiver_before"], current["receiver_after"]
    old_calls = {row["call_id"] for row in before["calls"]}
    added = [row for row in after["calls"] if row["call_id"] not in old_calls]
    assert len(added) == 1 and after["total_calls"] == before["total_calls"] + 1
    call = added[0]
    body = after["durable_payloads"][call["effect_key"]]
    assert body["scope_id"] == preparation["scope_id"]
    expected_key = body["receipt_id"] + ":" + preparation["task_id"]
    assert call["effect_key"] == body["effect_key"] == expected_key, "cutover effect key names a different task"
    assert call["outcome"] in {"applied", "replayed"} and call["payload_hash"] == digest(body)
    rows = h.sql({"scope_id": preparation["scope_id"]},
        "SELECT receipt_id,action_type,object_versions,effect_task_ids FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s",
        (preparation["scope_id"], body["receipt_id"]))
    assert len(rows) == 1
    assert preparation["task_id"] in rows[0]["effect_task_ids"], "held task is not owned by this immutable receipt"
    expected = {"effect_key": expected_key, "scope_id": preparation["scope_id"],
                "receipt_id": str(rows[0]["receipt_id"]), "action_type": rows[0]["action_type"],
                "object_versions": rows[0]["object_versions"]}
    assert body == expected
    return {"real_old_worker_cannot_claim_or_dispatch": {"passed": True,
                "evidence": {"held_error": held["error_code"], "old_claim_sqlstate": once["sqlstate"],
                    "eligible_queued_tasks": once["eligible_queued_tasks"], "positive_http_delta": 1,
                    "current_body_matches_live_immutable_receipt": True, "same_preclaimed_task_verified": True,
                    "task_owned_by_immutable_receipt": True, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}},
            "preclaimed_old_task_cutover_precondition": {"passed": True,
                "evidence": {"task_id": preparation["task_id"], "paused_before_dispatch": True,
                    "old_source_verified": True, "held_before_upgrade": True,
                    "already_admitted_http_revocation_claimed": False,
                    "operational_precondition": "quiesce old writers and drain requests already past admission before cutover"}}}
