"""Two targeted adversarial checks missing from the initial eight-group run.

Integration (the driver remains owned by run.py):

* Call run_extra_bundle_checks(scenario) after group_handshake() and before the
  primary group_bundle(). It creates its own BC/EC/feedback/adjustment through
  HTTP and does not replace scenario.bc/ec/fb/adjustment. Its activation tasks
  are ordinary real tasks in scenario.receipts; group_closure() can drain them.
* Call run_evidence_unavailable_check(scenario) inside group_closure(), after
  scenario.acceptance_id has been assigned and before the successful closure.
  Run serially with all other MinIO/backup operations. It stops only the named
  disposable acceptance project's minio service and restores it in finally.

No implementation functions, fake successful rows, fabricated receipts or
monkeypatched business checks are used here.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
import uuid

import httpx

from acceptance.runtime.client import assert_error, sha256, utc_now
from acceptance.runtime.harness import wait_until


def run_extra_bundle_checks(scenario: Any) -> dict[str, Any]:
    """Reject a fully signed BC-only bundle that omits its still-active EC.

The request deliberately INCLUDES the EC's expected_version. Therefore a
rejection cannot be credited to missing CAS input; the implementation must
discover that this active dependent is absent from the actual change set.
"""
    s = scenario
    if s.worker is not None and s.worker.poll() is None:
        raise AssertionError("Run extra bundle checks while the scenario Worker is stopped")
    if hasattr(s, "adjustment"):
        raise AssertionError("Run this check before the primary group_bundle to keep its scenario independent")
    with s.h.group("extra_reverse_active_dependency_omission") as report:
        bc, bc_signatures = s.signed("BusinessCommitment", s.outcome)
        bc_activation = s.activate(bc, bc_signatures, s.outcome)
        ec, ec_signatures = s.signed("ExecutionCommitment", bc)
        ec_activation = s.activate(ec, ec_signatures, bc)
        bc_before = s.object(bc)
        ec_before = s.object(ec)
        bc_r1 = bc_before["effective_revision_id"]
        ec_r1 = ec_before["effective_revision_id"]
        assert bc_before["lifecycle_status"] == ec_before["lifecycle_status"] == "active"

        # This SQL relation is independent evidence that the omitted EC really
        # depends on the BC revision being replaced, not just a similarly named task.
        rows = s.sql(
            """SELECT o.object_id::text, o.effective_revision_id::text, r.payload
               FROM gov_objects o JOIN gov_object_revisions r
                 ON r.scope_id=o.scope_id AND r.object_id=o.object_id
                AND r.revision_id=o.effective_revision_id
               WHERE o.scope_id=%s AND o.object_id=%s AND o.lifecycle_status='active'""",
            (s.f["scope_id"], ec),
        )
        assert len(rows) == 1 and rows[0]["effective_revision_id"] == ec_r1
        assert {"object_id": bc, "revision_id": bc_r1} in rows[0]["payload"]["upstream_refs"]

        suffix = uuid.uuid4().hex[:8]
        feedback = s.feedback_investigation(f"Extra omitted-dependent feedback {suffix}")
        decision = s.decision(f"Extra omitted-dependent decision {suffix}")
        feedback_revision = s.object(feedback)["latest_revision_id"]
        decision_revision = s.object(decision)["effective_revision_id"]
        shell = {"title": f"Extra intentionally incomplete bundle {suffix}",
                 "feedback_revision_id": feedback_revision,
                 "decision_revision_id": decision_revision, "changes": []}
        adjustment = s.create("ManagementAdjustment", shell)
        bc_r2 = s.propose(
            bc, s.commitment_payload("BusinessCommitment", s.upstream(s.outcome), value=66),
            bundle_id=adjustment, deps=(s.outcome, adjustment),
        )
        incomplete_changes = [{"object_id": bc, "from_revision_id": bc_r1, "to_revision_id": bc_r2}]
        s.propose(adjustment, {**shell, "changes": incomplete_changes}, deps=(bc, feedback, decision))
        signatures = [s.accept(bc, role)["result"]["handshake_id"] for role in ("ceo", "domain_dri")]
        assert len(set(signatures)) == 2
        assert s.object(bc)["effective_revision_id"] == bc_r1
        assert s.object(ec)["effective_revision_id"] == ec_r1

        params = {"decision_revision_id": decision_revision, "feedback_revision_id": feedback_revision,
                  "changes": incomplete_changes}
        versions = s.deps(bc, ec, feedback, decision, s.outcome)
        assert ec in {item["object_id"] for item in versions}
        assert ec not in {item["object_id"] for item in incomplete_changes}
        command = s.ceo.command("confirm_adjustment", params, target=s.target(adjustment),
                                expected_versions=versions)
        before = s.snapshot()
        denied = s.ceo.json("POST", "/v1/actions", command, expected=409)
        assert_error(denied, "STALE_DEPENDENCY")
        after = s.snapshot()
        assert after == before, "Rejected incomplete bundle changed objects, events, receipts or outbox"
        assert s.object(bc)["effective_revision_id"] == bc_r1
        assert s.object(ec)["effective_revision_id"] == ec_r1
        assert s.object(adjustment)["lifecycle_status"] == "proposed"
        assert s.object(feedback)["lifecycle_status"] == "investigating"
        report.update(
            business_commitment_id=bc, omitted_execution_commitment_id=ec,
            adjustment_id=adjustment, attempted_change_objects=[bc],
            explicitly_supplied_dependency_objects=[item["object_id"] for item in versions],
            rejection_code=denied["error"]["code"], preserved_bc_revision=bc_r1,
            preserved_ec_revision=ec_r1, sql_snapshot_unchanged=True, snapshot=before,
            activation_task_ids=bc_activation["effect_task_ids"] + ec_activation["effect_task_ids"],
        )
    return report


def _storage_ready(endpoint: str) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            return client.get(endpoint.rstrip("/") + "/minio/health/ready").status_code == 200
    except httpx.TransportError:
        return False


def run_evidence_unavailable_check(scenario: Any) -> dict[str, Any]:
    """An existing accepted record cannot close feedback while real S3 is down.

Restores the exact same MinIO service/volume and proves the originally pinned
evidence bytes are readable afterward. No evidence metadata, hashes, versions,
Object Lock settings or business rows are modified to simulate this fault.
"""
    from acceptance.runtime import infra

    s = scenario
    if infra.PROJECT != "tkos-ontology-runtime-acceptance":
        raise AssertionError("Storage fault is restricted to the disposable acceptance project")
    endpoint = s.h.env["TKOS_OBJECT_STORE_ENDPOINT"]
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
        raise AssertionError("Storage fault must target the existing loopback acceptance MinIO endpoint")
    if s.object(s.fb)["lifecycle_status"] != "awaiting_acceptance" or not s.acceptance_id:
        raise AssertionError("Call this check after accepted verification and before successful closure")
    evidence_path = f"/v1/evidence-assets/{s.evidence}/revisions/{s.evidence_revision}"
    original = s.ceo.request("GET", evidence_path).content
    assert original == s.evidence_bytes
    evidence_hash = sha256(original)
    params = s.closure_params(s.acceptance_id, s.dec)
    command = s.ceo.command(
        "confirm_closure", params, target=s.target(s.fb),
        expected_versions=s.deps(s.dec, s.evidence, s.adjustment, s.bc, s.ec),
    )
    was_running = s.worker is not None and s.worker.poll() is None
    if was_running:
        s.stop_worker()
    try:
        with s.h.group("extra_real_evidence_unavailable_blocks_closure") as report:
            assert _storage_ready(endpoint), "Storage must be healthy before injecting the outage"
            before = s.snapshot()
            stopped = False
            try:
                stopped = True  # even a partial stop failure must attempt recovery
                infra.run(infra.compose("stop", "--timeout", "10", "minio"), timeout=30)
                wait_until(lambda: not _storage_ready(endpoint), timeout=10,
                           message="Acceptance MinIO did not actually become unavailable")
                report["storage_unavailable_at"] = utc_now()
                missing_bytes = s.ceo.json("GET", evidence_path, expected=503)
                assert_error(missing_bytes, "EVIDENCE_UNAVAILABLE")
                denied = s.ceo.json("POST", "/v1/actions", command, expected=503)
                assert_error(denied, "EVIDENCE_UNAVAILABLE")
                assert s.snapshot() == before, "Unavailable evidence caused a partial closure, receipt or outbox write"
                assert s.object(s.fb)["lifecycle_status"] == "awaiting_acceptance"
                report.update(
                    feedback_id=s.fb, existing_acceptance_id=s.acceptance_id,
                    evidence_object_id=s.evidence, exact_evidence_revision_id=s.evidence_revision,
                    evidence_sha256=evidence_hash, download_error=missing_bytes["error"]["code"],
                    closure_error=denied["error"]["code"], sql_snapshot_unchanged=True,
                    snapshot=before, fault_method="stop_named_disposable_minio_service",
                )
            finally:
                if stopped:
                    infra.run(infra.compose("start", "minio"), timeout=30)
                    wait_until(lambda: _storage_ready(endpoint), timeout=30,
                               message="Acceptance MinIO did not recover after the evidence fault")
                    restored = s.ceo.request("GET", evidence_path).content
                    assert restored == original and sha256(restored) == evidence_hash
                    report["storage_restored_at"] = utc_now()
                    report["exact_evidence_bytes_restored"] = True
    finally:
        if was_running:
            s.start_worker()
    return report


__all__ = ["run_extra_bundle_checks", "run_evidence_unavailable_check"]
