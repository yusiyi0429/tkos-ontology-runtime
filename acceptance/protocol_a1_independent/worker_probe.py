"""Root-controlled worker probes with a separately persisted HTTP receiver.

This module neither migrates nor seeds databases. Callers provide an isolated
synthetic scope and real task records produced by its HTTP history. All old
worker invocations import the exported old source directly; the probe does not
install a handler, alter a return value, or set a capability in that process.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sqlite3
import sys
import uuid

import httpx

from .support import Harness, HERE, ROOT, digest, private_json, public_json, wait


@dataclass
class Receiver:
    process: subprocess.Popen
    url: str
    ledger: Path

    def snapshot(self) -> dict:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.get(self.url + "/ledger")
            response.raise_for_status()
            result = response.json()
        assert {"total_calls", "unique_effects", "effects", "calls"} <= set(result)
        # Observe durable state independently of the receiver's HTTP report.
        with sqlite3.connect(self.ledger.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            assert conn.execute("SELECT count(*) FROM calls").fetchone()[0] == result["total_calls"]
            bodies = conn.execute("SELECT effect_key,payload_json FROM effects ORDER BY effect_key").fetchall()
            assert len(bodies) == result["unique_effects"]
            result["durable_payloads"] = {key: json.loads(body) for key, body in bodies}
        return result


@dataclass
class HeldClaim:
    process: subprocess.Popen
    ready: dict
    release_file: Path
    result_file: Path
    task_file: Path
    receiver: Receiver


def start_receiver(harness: Harness, label: str = "worker") -> Receiver:
    tag = uuid.uuid4().hex
    ready = harness.private / f"{label}-receiver-{tag}.json"
    ledger = harness.private / f"{label}-receiver-{tag}.sqlite"
    process = harness.spawn("receiver", [sys.executable, "-I", str(ROOT / "acceptance/runtime/receiver.py"),
        "--port", "0", "--ledger", str(ledger), "--ready-file", str(ready)])
    def started():
        if ready.exists():
            return json.loads(ready.read_text())
        if process.poll() is not None:
            raise AssertionError("independent receiver exited before readiness")
        return None
    info = wait(started)
    receiver = Receiver(process, info["url"], ledger)
    assert receiver.snapshot()["total_calls"] == 0
    return receiver


def _updates(fixture: dict, receiver: Receiver) -> dict:
    return {"MEMORY_TENANT": fixture["tenant_id"], "MEMORY_ORG": fixture["company_id"],
            "GOVERNED_EFFECT_URL": receiver.url + "/effects"}


def prepare_old_claim(harness: Harness, fixture: dict, old_source: Path,
                      receiver: Receiver | None = None) -> HeldClaim:
    """Call BEFORE migration, using a fresh worker-only synthetic scope."""
    receiver = receiver or start_receiver(harness, "old-held")
    tag = uuid.uuid4().hex
    ready = harness.private / f"old-held-{tag}-ready.json"
    release = harness.private / f"old-held-{tag}-release"
    result = harness.private / f"old-held-{tag}-result.json"
    task = harness.private / f"old-held-{tag}-task.json"
    process = harness.spawn("old-held-worker", [sys.executable, "-I", str(HERE / "worker_process.py"),
        "held-claim", "--legacy", "--source", str(old_source), "--result", str(result),
        "--task-file", str(task), "--ready", str(ready), "--release", str(release)],
        updates=_updates(fixture, receiver))
    def held():
        if ready.exists():
            return json.loads(ready.read_text())
        if process.poll() is not None:
            raise AssertionError("old worker failed before claiming; inspect its redacted log")
        return None
    info = wait(held)
    assert info["phase"] == "claimed_before_dispatch" and not info["database_transaction_open"]
    assert not info["capability_injected_by_probe"] and info["legacy"]
    assert receiver.snapshot()["total_calls"] == 0
    public_json(harness.output / "old-held-before-upgrade.json", info)
    return HeldClaim(process, info, release, result, task, receiver)


def release_old_claim(harness: Harness, fixture: dict, held: HeldClaim) -> dict:
    """Call AFTER migration; the pre-existing process still holds the old task."""
    before = harness.snapshot(fixture)
    external = held.receiver.snapshot()
    assert external["total_calls"] == 0
    private_json(held.release_file, {"release_after_isolated_migration": True})
    held.process.wait(timeout=20)
    assert held.process.returncode == 0 and held.result_file.exists()
    result = json.loads(held.result_file.read_text())
    after = harness.snapshot(fixture)
    received = held.receiver.snapshot()
    assert result["provenance"] == held.ready["provenance"], "held process source changed"
    result.update(sql_unchanged=before == after, receiver_unchanged=external == received,
                  before_sql_sha256=digest(before), after_sql_sha256=digest(after),
                  receiver_calls=received["total_calls"], held_before_upgrade=True,
                  already_admitted_http_revocation_claimed=False)
    result["passed"] = (not result["successful_dispatch"] and not result.get("unexpected_error")
        and result.get("error_code") == "governance_effect_scope_invalid"
        and result["sql_unchanged"] and result["receiver_unchanged"])
    public_json(harness.output / "old-held-after-upgrade.json", result)
    return result


def task_file(harness: Harness, fixture: dict, task_id: str, *, payload_updates: dict | None = None) -> Path:
    """Read a real task; optional queue metadata forgery affects only this input."""
    rows = harness.sql(fixture, """SELECT * FROM runtime_tasks WHERE task_id=%s
        AND tenant_id=%s AND organization_id=%s""", (task_id, fixture["tenant_id"], fixture["company_id"]))
    assert len(rows) == 1 and rows[0]["task_type"] == "governance.dispatch"
    row = dict(rows[0])
    if payload_updates:
        row["payload"] = {**row["payload"], **payload_updates}
    path = harness.private / f"dispatch-task-{uuid.uuid4().hex}.json"
    private_json(path, row)
    return path


def run_dispatch(harness: Harness, fixture: dict, source: Path, input_task: Path, *,
                 label: str, receiver: Receiver | None = None, legacy: bool = False,
                 expect_success: bool = False, expected_error_codes: tuple[str, ...] = ()) -> dict:
    receiver = receiver or start_receiver(harness, label)
    result_file = harness.private / f"dispatch-result-{uuid.uuid4().hex}.json"
    before, external = harness.snapshot(fixture), receiver.snapshot()
    args = [sys.executable, "-I", str(HERE / "worker_process.py"), "dispatch", "--source", str(source),
            "--task-file", str(input_task), "--result", str(result_file)]
    if legacy:
        args.append("--legacy")
    process = harness.spawn(label, args, updates=_updates(fixture, receiver))
    process.wait(timeout=20)
    assert process.returncode == 0 and result_file.exists()
    result = json.loads(result_file.read_text())
    after, received = harness.snapshot(fixture), receiver.snapshot()
    result.update(sql_unchanged=before == after, before_sql_sha256=digest(before),
                  after_sql_sha256=digest(after), receiver_before=external, receiver_after=received)
    if expect_success:
        actual_task = json.loads(input_task.read_text())
        expected_key = actual_task["payload"]["receipt_id"] + ":" + actual_task["task_id"]
        before_call_ids = {call["call_id"] for call in external["calls"]}
        added_calls = [call for call in received["calls"] if call["call_id"] not in before_call_ids]
        receipts = harness.sql(fixture, """SELECT receipt_id,action_type,object_versions
            FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s""",
            (fixture["scope_id"], actual_task["payload"]["receipt_id"]))
        assert len(receipts) == 1
        receipt = receipts[0]
        expected_body = {"effect_key": expected_key, "scope_id": fixture["scope_id"],
                         "receipt_id": str(receipt["receipt_id"]), "action_type": receipt["action_type"],
                         "object_versions": receipt["object_versions"]}
        result["passed"] = (result["successful_dispatch"] and before == after
            and received["total_calls"] == external["total_calls"] + 1 and len(added_calls) == 1
            and added_calls[0]["effect_key"] == expected_key
            and added_calls[0]["outcome"] in {"applied", "replayed"}
            and added_calls[0]["payload_hash"] == digest(expected_body)
            and received["durable_payloads"].get(expected_key) == expected_body)
    else:
        assert expected_error_codes, "negative dispatch requires a precise expected error"
        result["passed"] = (not result["successful_dispatch"] and not result.get("unexpected_error")
            and result.get("error_code") in expected_error_codes and before == after and external == received)
    public_json(harness.output / f"worker-{label}.json", result)
    return result


def run_old_worker_once(harness: Harness, fixture: dict, old_source: Path) -> dict:
    """A remaining queued governed task is mandatory; empty queues are not PASS."""
    pending = harness.sql(fixture, """SELECT task_id FROM runtime_tasks WHERE tenant_id=%s
        AND organization_id=%s AND task_type='governance.dispatch' AND state IN ('queued','retryable')
        AND available_at<=clock_timestamp()""", (fixture["tenant_id"], fixture["company_id"]))
    assert pending, "old worker claim negative requires an eligible real task"
    receiver = start_receiver(harness, "old-once")
    result_file = harness.private / f"old-once-result-{uuid.uuid4().hex}.json"
    before = harness.snapshot(fixture)
    process = harness.spawn("old-worker-once", [sys.executable, "-I", str(HERE / "worker_process.py"),
        "worker-once", "--legacy", "--source", str(old_source), "--result", str(result_file)],
        updates=_updates(fixture, receiver))
    process.wait(timeout=20)
    assert process.returncode == 0 and result_file.exists()
    result = json.loads(result_file.read_text())
    after, received = harness.snapshot(fixture), receiver.snapshot()
    result.update(sql_unchanged=before == after, receiver_calls=received["total_calls"],
                  eligible_queued_tasks=len(pending))
    result["passed"] = (result.get("sqlstate") == "55000" and not result.get("unexpected_error")
        and before == after and received["total_calls"] == 0)
    public_json(harness.output / "old-worker-once.json", result)
    return result
