"""A protocol fences on matching historical object types and real dependencies."""
from __future__ import annotations

from copy import deepcopy
import uuid

from acceptance.runtime.client import Client
from .control_adapter import A_CONTRACT
from .legacy_flow import LegacyFlow
from .support import Harness, public_json


def run_typed_fences(h: Harness, f: dict, *, url: str, typed_fixture: dict, legacy_flow: LegacyFlow,
                     legacy_result: dict) -> dict:
    clients = h.clients(url, f)
    refs = typed_fixture["types"]
    payloads = typed_fixture["payloads"]
    evidence_id = legacy_flow.evidence[-1]["revision_id"]
    sub = legacy_result["submission_v2"]
    rows = []
    def reject(kind, actor, action, params):
        request = Client.command(action, params, target=refs[kind])
        request["contract_version"] = A_CONTRACT
        for path in ("/v1/actions/prepare", "/v1/actions"):
            checked = h.rejection(f, clients[actor], path, request, 409, "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
            rows.append({"object_type": kind, "action_type": action, "path": path, **checked})
    try:
        for kind in refs:
            reject(kind, "ceo", "propose_revision", {"payload": deepcopy(payloads[kind])})
        reject("CompanyOutcome", "ceo", "confirm_outcome", {})
        reject("Decision", "ceo", "confirm_decision", {})
        for kind, actor in (("BusinessCommitment", "ceo"), ("ExecutionCommitment", "domain_dri")):
            revision = clients["ceo"].revision(refs[kind]["object_id"], refs[kind]["revision_id"])
            reject(kind, actor, "accept_commitment", {"party_assignment_id": f["actors"][actor]["assignment_id"],
                "understanding": "This exact syntactically valid commitment cannot use a legacy handler.",
                "accepted_terms_hash": revision["payload_hash"]})
            legacy_id = legacy_result["bc" if kind == "BusinessCommitment" else "ec"]
            signatures = h.sql(f, "SELECT handshake_id::text FROM gov_handshakes WHERE scope_id=%s AND object_id=%s ORDER BY recorded_at",
                               (f["scope_id"], legacy_id))
            assert len(signatures) == 2
            reject(kind, "ceo", "activate_commitment", {"handshake_record_ids": [r["handshake_id"] for r in signatures],
                "activation_policy_revision_id": f["policy_revision_id"]})
        reject("WorkItem", "mission_dri", "accept_work_item", {})
        reject("WorkItem", "mission_dri", "submit_deliverable", {"title": "Synthetic fenced submit",
            "summary": "Actual existing evidence but no Contract-A business handler", "evidence_revision_ids": [evidence_id]})
        reject("WorkItem", "verifier", "review_deliverable", LegacyFlow.review_params(sub, "accepted"))
        reject("FeedbackThread", "ceo", "route_feedback", {"responsible_assignment_id": f["actors"]["domain_dri"]["assignment_id"]})
        for action in ("accept_feedback", "investigate_feedback", "reopen_feedback"):
            reject("FeedbackThread", "domain_dri", action, {})
        reject("FeedbackThread", "domain_dri", "request_feedback_acceptance", {"decision_revision_id": refs["Decision"]["revision_id"]})
        reject("FeedbackThread", "verifier", "record_acceptance", {"decision_revision_id": refs["Decision"]["revision_id"],
            "evidence_revision_ids": [evidence_id], "verification_result": "accepted"})
        reject("FeedbackThread", "ceo", "confirm_closure", {"acceptance_record_id": legacy_result["acceptance"]["acceptance_id"],
            "resolution_decision_revision_id": refs["Decision"]["revision_id"], "closure_evidence_revision_ids": [evidence_id],
            "disposition": "resolved", "closure_note": "Metadata fixture must not close through a legacy handler"})
        reject("CompanyOutcome", "ceo", "record_outcome_assessment", {"assessment_result": "not_achieved",
            "observation_revision_ids": [refs["MetricObservation"]["revision_id"]], "evidence_revision_ids": [evidence_id],
            "assessment_note": "Metadata fixture has no business interpretation"})

        # Real legacy target plus a real A parent tests dependency resolution,
        # as distinct from rejecting a target whose protocol was already A.
        legacy_bc = legacy_result["bc"]
        command = Client.command("propose_revision", {"payload": {
            **deepcopy(legacy_flow.object(legacy_bc)["latest_revision"]["payload"]),
            "upstream_refs": [{k: refs["CompanyOutcome"][k] for k in ("object_id", "revision_id")}] }},
            target=legacy_flow.target(legacy_bc))
        for path in ("/v1/actions/prepare", "/v1/actions"):
            checked = h.rejection(f, clients["ceo"], path, command, 409, "PROTOCOL_BINDING_CONFLICT")
            rows.append({"object_type": "BusinessCommitment", "action_type": "legacy_target_new_dependency", "path": path, **checked})
        result = {"checks": rows, "matching_typed_objects": len(refs), "contract_a1_accepted": False,
                  "adjustment_affected_set_status": "not_run"}
        public_json(h.output / "typed-target-fences.json", result)
        return result
    finally:
        for client in clients.values():
            client.close()
