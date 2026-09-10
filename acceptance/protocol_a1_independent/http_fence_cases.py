"""Real-HTTP protocol fence negatives with fixed status/code expectations."""
from __future__ import annotations

from copy import deepcopy
import uuid

from acceptance.runtime.client import Client
from .control_adapter import A_CONTRACT, LEGACY_CONTRACT
from .support import Harness, public_json


def random_id():
    return str(uuid.uuid4())


def run_target_fences(harness: Harness, fixture: dict, *, url: str, sentinel_id: str) -> dict:
    clients = harness.clients(url, fixture)
    rows = []
    try:
        obj = clients["ceo"].object(sentinel_id)
        target = {"object_id": sentinel_id, "revision_id": obj["latest_revision_id"],
                  "expected_version": obj["object_version"]}
        assert target["revision_id"]
        base = Client.command("confirm_outcome", {}, target=target)
        for label, declared, expected in (
                ("omitted", ..., "PROTOCOL_UPGRADE_REQUIRED"),
                ("explicit_null", None, "PROTOCOL_UPGRADE_REQUIRED"),
                ("legacy", LEGACY_CONTRACT, "PROTOCOL_UPGRADE_REQUIRED"),
                ("unknown", "tkos.contract-a/999", "PROTOCOL_NOT_SUPPORTED"),
                ("exact", A_CONTRACT, "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")):
            command = deepcopy(base)
            command["idempotency_key"] = "a1-protocol-fence-" + random_id()
            if declared is not ...:
                command["contract_version"] = declared
            for path in ("/v1/actions/prepare", "/v1/actions"):
                checked = harness.rejection(fixture, clients["ceo"], path, command, 409, expected)
                rows.append({"id": "target_contract_" + label, "path": path, **checked})
        action_rows = [
            ("ceo", "propose_revision", {"payload": {"title": "Must not reinterpret A sentinel as legacy Outcome"}}),
            ("ceo", "accept_commitment", {"party_assignment_id": fixture["actors"]["ceo"]["assignment_id"],
                "understanding": "Should be fenced before a legacy handshake.", "accepted_terms_hash": "a" * 64}),
            ("ceo", "activate_commitment", {"handshake_record_ids": [random_id(), random_id()],
                "activation_policy_revision_id": fixture["policy_revision_id"]}),
            ("ceo", "confirm_adjustment", {"decision_revision_id": random_id(), "feedback_revision_id": random_id(),
                "changes": [{"object_id": random_id(), "from_revision_id": random_id(), "to_revision_id": random_id()}]}),
            ("mission_dri", "accept_work_item", {}),
            ("mission_dri", "submit_deliverable", {"title": "Do not create a legacy Deliverable",
                "summary": "Must be fenced before legacy execution", "evidence_revision_ids": [random_id()]}),
            ("verifier", "review_deliverable", {"deliverable_revision_id": random_id(), "delivery_payload_hash": "b" * 64,
                "verification_result": "accepted", "criterion_results": [
                    {"criterion_id": "c1", "result": "passed", "note": "Unexecuted synthetic check"}],
                "review_note": "Do not accept via legacy semantics"}),
        ]
        for actor, kind, params in action_rows:
            command = Client.command(kind, params, target=target)
            command["contract_version"] = A_CONTRACT
            for path in ("/v1/actions/prepare", "/v1/actions"):
                checked = harness.rejection(fixture, clients[actor], path, command, 409, "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
                rows.append({"id": "existing_action_" + kind, "path": path, **checked})
        for field, value in (("method_profile_ref", {"profile_id": "forged", "revision": "forged"}),
                             ("protocol_id", "tkos.legacy-governed"), ("actor_id", random_id())):
            command = {**deepcopy(base), field: value, "contract_version": A_CONTRACT}
            for path in ("/v1/actions/prepare", "/v1/actions"):
                checked = harness.rejection(fixture, clients["ceo"], path, command, 422, "INVALID_REQUEST")
                rows.append({"id": "unknown_field_" + field, "path": path, **checked})
        for declared in (A_CONTRACT, "tkos.contract-a/999"):
            command = {**deepcopy(base), "contract_version": declared}
            for path in ("/v1/actions/prepare", "/v1/actions"):
                checked = harness.rejection(fixture, clients["outsider"], path, command, 404, "NOT_FOUND")
                rows.append({"id": "hidden_protocol", "path": path, **checked})
        result = {"gate": "a1_target_protocol_fences", "checks": rows, "passed": len(rows),
                  "failed": 0, "contract_a1_accepted": False}
        public_json(harness.output / "target-protocol-fences.json", result)
        return result
    finally:
        for client in clients.values():
            client.close()


def run_creation_fences(harness: Harness, fixture: dict, *, url: str, a_domain_id: str,
                        existing_legacy_parents: dict) -> dict:
    clients = harness.clients(url, fixture)
    rows = []
    try:
        # Structurally valid no-parent objects prove this is a scope/policy gate,
        # not merely a missing-required-field 422 on a malformed commitment.
        payloads = [
            ("CompanyOutcome", {"title": "Orphan legacy Outcome attempt"}),
            ("FeedbackThread", {"title": "Orphan legacy feedback", "description": "No parent field to remove"}),
            ("BusinessCommitment", {"title": "Legacy BC under an A domain", "terms": {"target": 3},
                "required_assignment_ids": [fixture["actors"]["ceo"]["assignment_id"],
                                            fixture["actors"]["domain_dri"]["assignment_id"]],
                "upstream_refs": [existing_legacy_parents["BusinessCommitment"]]}),
            ("ExecutionCommitment", {"title": "Legacy EC under an A domain", "terms": {"target": 3},
                "required_assignment_ids": [fixture["actors"]["domain_dri"]["assignment_id"],
                                            fixture["actors"]["mission_dri"]["assignment_id"]],
                "upstream_refs": [existing_legacy_parents["ExecutionCommitment"]]}),
            ("WorkItem", {"title": "Legacy task under an A domain", "execution_commitment_ref": existing_legacy_parents["WorkItem"],
                "dri_assignment_id": fixture["actors"]["mission_dri"]["assignment_id"],
                "acceptor_assignment_id": fixture["actors"]["verifier"]["assignment_id"],
                "acceptance_criteria": [{"criterion_id": "c1", "description": "The legacy path must remain fenced"}]}),
        ]
        for kind, payload in payloads:
            for declared, expected in ((None, "PROTOCOL_UPGRADE_REQUIRED"),
                                      (LEGACY_CONTRACT, "PROTOCOL_UPGRADE_REQUIRED"),
                                      (A_CONTRACT, "ACTION_NOT_SUPPORTED_FOR_PROTOCOL")):
                command = Client.command("create_object", {"object_type": kind, "domain_id": a_domain_id, "payload": payload})
                if declared is not None:
                    command["contract_version"] = declared
                for path in ("/v1/actions/prepare", "/v1/actions"):
                    checked = harness.rejection(fixture, clients["ceo"], path, command, 409, expected)
                    rows.append({"id": "creation_" + kind, "declared": declared, "path": path, **checked})
        result = {"gate": "a1_creation_protocol_fences", "checks": rows, "passed": len(rows),
                  "failed": 0, "contract_a1_accepted": False}
        public_json(harness.output / "creation-protocol-fences.json", result)
        return result
    finally:
        for client in clients.values():
            client.close()
