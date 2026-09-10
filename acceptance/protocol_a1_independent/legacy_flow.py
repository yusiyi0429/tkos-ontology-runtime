"""Independent HTTP construction of the legacy commitment and delivery chain."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import uuid

from acceptance.runtime.client import Client, utc_now


class LegacyFlow:
    def __init__(self, harness, url: str, fixture: dict):
        self.h = harness
        self.url, self.fixture = url, fixture
        self.clients = harness.clients(url, fixture)
        self.ceo = self.clients["ceo"]
        self.objects: list[str] = [fixture["outcome"]["object_id"]]
        self.receipts: list[dict] = []
        self.commands: list[dict] = []
        self.evidence: list[dict] = []

    def close(self):
        for client in self.clients.values():
            client.close()

    def object(self, oid: str):
        return self.ceo.object(oid)

    def target(self, oid: str) -> dict:
        obj = self.object(oid)
        return {"object_id": oid, "revision_id": obj["latest_revision_id"],
                "expected_version": obj["object_version"]}

    def ref(self, oid: str) -> dict:
        obj = self.object(oid)
        return {"object_id": oid, "revision_id": obj["effective_revision_id"] or obj["latest_revision_id"]}

    def prepare(self, actor: str, kind: str, params: dict, *, oid: str | None = None,
                key: str | None = None) -> dict:
        command = Client.command(kind, deepcopy(params), target=self.target(oid) if oid else None,
                                 key=key or f"a1-independent-{uuid.uuid4()}")
        prepared = self.clients[actor].json("POST", "/v1/actions/prepare", command)
        command["expected_versions"] = prepared["expected_versions"]
        return command

    def commit(self, actor: str, command: dict) -> dict:
        receipt = self.clients[actor].json("POST", "/v1/actions", command)
        self.commands.append({"actor": actor, "request": deepcopy(command), "receipt": deepcopy(receipt)})
        self.receipts.append(receipt)
        return receipt

    def act(self, actor: str, kind: str, params: dict, *, oid: str | None = None) -> dict:
        return self.commit(actor, self.prepare(actor, kind, params, oid=oid))

    def create(self, kind: str, payload: dict) -> str:
        receipt = self.act("ceo", "create_object", {"object_type": kind,
            "domain_id": self.fixture["domain_id"], "payload": payload})
        oid = receipt["result"]["object_id"]
        self.objects.append(oid)
        return oid

    def commitment(self, kind: str, parent: str) -> str:
        actors = ("ceo", "domain_dri") if kind == "BusinessCommitment" else ("domain_dri", "mission_dri")
        payload = {"title": "Independent legacy " + kind, "terms": {"target": 3, "unit": "clients"},
                   "required_assignment_ids": [self.fixture["actors"][a]["assignment_id"] for a in actors],
                   "upstream_refs": [self.ref(parent)]}
        oid = self.create(kind, payload)
        signatures = []
        for actor in actors:
            obj = self.object(oid)
            revision = self.ceo.revision(oid, obj["latest_revision_id"])
            receipt = self.act(actor, "accept_commitment", {
                "party_assignment_id": self.fixture["actors"][actor]["assignment_id"],
                "understanding": "I accept the exact independent synthetic baseline.",
                "accepted_terms_hash": revision["payload_hash"]}, oid=oid)
            signatures.append(receipt["result"]["handshake_id"])
        activation = self.act("ceo", "activate_commitment", {"handshake_record_ids": signatures,
            "activation_policy_revision_id": self.fixture["policy_revision_id"]}, oid=oid)
        assert self.object(oid)["lifecycle_status"] == "active"
        assert activation["result"]["object_id"] == oid
        return oid

    def upload(self, version: int) -> dict:
        content = f"Independent synthetic source v{version}: actual two activated clients.\n".encode()
        result = self.clients["mission_dri"].json("POST", "/v1/evidence-assets", {
            "domain_id": self.fixture["domain_id"], "title": f"Independent evidence v{version}",
            "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain"})
        raw = self.clients["verifier"].request("GET",
            f"/v1/evidence-assets/{result['object_id']}/revisions/{result['revision_id']}").content
        assert raw == content
        assert result["sha256"] == hashlib.sha256(content).hexdigest()
        self.objects.append(result["object_id"])
        self.evidence.append({"object_id": result["object_id"], "revision_id": result["revision_id"],
                              "bytes_hex": content.hex(), "sha256": result["sha256"],
                              "receipt_id": result["receipt_id"]})
        return result

    @staticmethod
    def review_params(submission: dict, result: str) -> dict:
        return {"deliverable_revision_id": submission["deliverable_revision_id"],
                "delivery_payload_hash": submission["payload_hash"], "verification_result": result,
                "criterion_results": [{"criterion_id": "source", "result":
                    "passed" if result == "accepted" else "failed", "note": "Exact synthetic source checked"}],
                "review_note": "Source complete" if result == "accepted" else "Supplement source details"}

    def build(self, *, include_negatives: bool = False) -> dict:
        outcome = self.create("CompanyOutcome", {"title": "Independent three-client target",
            "terms": {"target": 3, "unit": "clients"}, "upstream_refs": []})
        self.act("ceo", "confirm_outcome", {}, oid=outcome)
        bc = self.commitment("BusinessCommitment", outcome)
        ec = self.commitment("ExecutionCommitment", bc)
        feedback = self.create("FeedbackThread", {"title": "Independent delivery shortfall",
            "description": "Raw source needs supplement; business target is assessed separately"})
        self.act("ceo", "route_feedback", {"responsible_assignment_id":
            self.fixture["actors"]["domain_dri"]["assignment_id"]}, oid=feedback)
        self.act("domain_dri", "accept_feedback", {}, oid=feedback)
        self.act("domain_dri", "investigate_feedback", {}, oid=feedback)
        work = self.create("WorkItem", {"title": "Independent precise DRI task",
            "execution_commitment_ref": self.ref(ec),
            "dri_assignment_id": self.fixture["actors"]["mission_dri"]["assignment_id"],
            "acceptor_assignment_id": self.fixture["actors"]["verifier"]["assignment_id"],
            "acceptance_criteria": [{"criterion_id": "source", "description": "Complete original source"}],
            "feedback_ref": self.ref(feedback)})
        negative_results = []
        if include_negatives:
            command = self.prepare("mission_dri", "accept_work_item", {}, oid=work)
            for actor in ("ceo", "domain_dri", "agent"):
                negative_results.append(self.h.rejection(self.fixture, self.clients[actor],
                    "/v1/actions", command, 403, "FORBIDDEN"))
        self.act("mission_dri", "accept_work_item", {}, oid=work)
        assert self.object(work)["lifecycle_status"] == "in_progress"
        v1 = self.upload(1)
        submitted1 = self.act("mission_dri", "submit_deliverable", {"title": "Independent v1",
            "summary": "First source package", "evidence_revision_ids": [v1["revision_id"]]}, oid=work)
        sub1 = submitted1["result"]
        deliverable = sub1["deliverable_object_id"]
        self.objects.append(deliverable)
        assert sub1["submission_seq"] == 1
        returned = self.act("verifier", "review_deliverable", self.review_params(sub1, "changes_requested"), oid=work)
        assert self.object(work)["lifecycle_status"] == "changes_requested"
        v2 = self.upload(2)
        submitted2 = self.act("mission_dri", "submit_deliverable", {"title": "Independent v2",
            "summary": "Supplemented source package", "evidence_revision_ids": [v2["revision_id"]],
            "responds_to_acceptance_id": returned["result"]["acceptance_id"]}, oid=work)
        sub2 = submitted2["result"]
        assert sub2["submission_seq"] == 2
        assert sub2["deliverable_object_id"] == deliverable
        assert sub2["deliverable_revision_id"] != sub1["deliverable_revision_id"]
        if include_negatives:
            command = self.prepare("verifier", "review_deliverable", self.review_params(sub2, "accepted"), oid=work)
            for actor in ("mission_dri", "ceo", "domain_dri", "agent"):
                negative_results.append(self.h.rejection(self.fixture, self.clients[actor],
                    "/v1/actions", command, 403, "FORBIDDEN"))
        accepted = self.act("verifier", "review_deliverable", self.review_params(sub2, "accepted"), oid=work)
        assert self.object(work)["lifecycle_status"] == "delivery_accepted"
        assert self.object(outcome)["outcome_achievement"] == "not_assessed"
        assert self.object(feedback)["lifecycle_status"] == "investigating"
        selected_at = utc_now()
        pack = self.ceo.json("POST", "/v1/context-packs", {"object_ids": [work, deliverable, outcome, feedback],
            "valid_at": selected_at, "known_at": selected_at})
        for receipt in (submitted1, returned, submitted2, accepted):
            assert receipt["effect_task_ids"] == [], "delivery must not dispatch business effects"
        return {"outcome": outcome, "bc": bc, "ec": ec, "feedback": feedback, "work_item": work,
                "deliverable": deliverable, "submission_v1": sub1, "submission_v2": sub2,
                "acceptance": accepted["result"], "snapshot_id": pack["context_snapshot_id"],
                "delivery_accepted": True, "outcome_achievement": "not_assessed",
                "feedback_status": "investigating", "negative_results": negative_results}

    def capture(self) -> dict:
        objects = {oid: self.object(oid) for oid in self.objects}
        revisions = self.h.sql(self.fixture,
            "SELECT object_id::text, revision_id::text, payload_hash FROM gov_object_revisions WHERE scope_id=%s ORDER BY revision_id",
            (self.fixture["scope_id"],))
        revision_data = {row["revision_id"]: self.ceo.revision(row["object_id"], row["revision_id"]) for row in revisions}
        receipts = self.h.sql(self.fixture,
            "SELECT receipt_id::text FROM gov_action_receipts WHERE scope_id=%s ORDER BY receipt_id",
            (self.fixture["scope_id"],))
        receipt_data = {row["receipt_id"]: self.ceo.json("GET", f"/v1/action-receipts/{row['receipt_id']}") for row in receipts}
        return {"objects": objects, "revisions": revision_data, "receipts": receipt_data,
                "commands": self.commands, "evidence": self.evidence,
                "sql_snapshot": self.h.snapshot(self.fixture),
                "s3_snapshot": self.h.storage_snapshot(self.fixture["scope_id"])}
