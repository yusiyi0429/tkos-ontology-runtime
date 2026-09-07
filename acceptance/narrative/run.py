"""Accept the governed Narrative adapter through real HTTP and read-only SQL.

Creates new synthetic scopes, never resets the shared acceptance environment.
All delivery/Outcome/MF transitions use the actual governed Action API.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import httpx
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.clark_v02.start import setup_one
from acceptance.runtime.client import Client, sha256, utc_now
from acceptance.runtime.harness import Harness, private_json, wait_until
from acceptance.runtime.run import Scenario
from acceptance.runtime.seed import create_fixture
from acceptance.runtime.sql_oracle import snapshot_scope

ENDPOINT = "/v1/context-graph/narrative"
HASH = re.compile(r"^[0-9a-f]{64}$")


class NarrativeHarness(Harness):
    def process_environment(self, updates=None):
        env = super().process_environment(updates)
        env.update({key: str(value) for key, value in self.env.items() if key.startswith("TKOS_NARRATIVE_")})
        if updates:
            env.update({key: str(value) for key, value in updates.items()})
        return env

    def save_report(self, **extra):
        passed = bool(self.complete and self.groups and all(row["status"] == "passed" for row in self.groups))
        return super().save_report(runtime_accepted=False, narrative_adapter_accepted=passed,
                                   real_model_accepted=False, clark_client_accepted=passed,
                                   boundary="Isolated synthetic identities and real PostgreSQL/MinIO/HTTP; deterministic narrative; no release or deployment",
                                   **extra)


def snapshot(s):
    with psycopg.connect(s.h.env["APP_DATABASE_URL"]) as conn:
        return snapshot_scope(conn, s.f["scope_id"], s.f["tenant_id"], s.f["company_id"])


def readonly_sql(s, statement, params=()):
    with psycopg.connect(s.h.env["APP_DATABASE_URL"]) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (s.f["scope_id"],))
            return conn.execute(statement, params).fetchall()


@contextmanager
def variant_api(s, updates):
    """Own an additional loopback API process, with no change to the main API."""
    ready = s.h.private / f"narrative-variant-{uuid.uuid4().hex}.json"
    proc = s.h.spawn("narrative-variant-" + uuid.uuid4().hex[:8], "server.py", "--port", 0,
        "--control-dir", s.h.control, "--key-file", s.h.key_file, "--ready-file", ready, updates=updates)
    client = None
    try:
        state = wait_until(lambda: json.loads(ready.read_text()) if ready.exists() else None, message="Narrative variant API start")
        client = Client(state["url"], s.f["actors"]["ceo"]["token"], "variant-ceo", s.h.log)
        yield client
    finally:
        if client:
            client.close()
        s.h.stop(proc)


def grant_test_legacy_read(s):
    """Append a test-only authority revision; never seed or change business facts."""
    assert s.f["tenant_id"].startswith("runtime-acceptance-narrative-")
    with psycopg.connect(s.h.env["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (s.f["scope_id"],))
        policy = conn.execute("SELECT policy_id,policy_seq,content FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1", (s.f["scope_id"], s.domain)).fetchone()
        content = {**policy["content"], "action_roles": {**policy["content"]["action_roles"], "read_legacy_context": ["CEO"]}}
        conn.execute("INSERT INTO gov_activation_policies(policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (str(uuid.uuid4()), s.f["scope_id"], s.domain, policy["policy_id"], policy["policy_seq"] + 1, Jsonb(content), s.f["actors"]["ceo"]["principal_id"]))


@contextmanager
def failing_embedding_provider():
    """Only an upstream failure fixture; it produces no vectors or model prose."""
    calls = []
    marker = "UPSTREAM_DIAGNOSTIC_MUST_NOT_LEAK"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            # Do not retain request credentials or content.
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            calls.append({"method": "POST", "path": self.path})
            body = json.dumps({"error": marker}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, marker
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class NarrativeSuite:
    def __init__(self, s, business):
        self.s, self.h, self.business = s, s.h, business
        self.work = business["work_item"]["objectId"]
        self.outcome = business["outcome_id"]
        self.feedback = business["feedback_id"]
        self.ids = [self.work, self.outcome, self.feedback]
        self.reads = 0
        self.checkpoints = {}

    def call(self, *, client=None, expected=200, body=None, **overrides):
        body = {"query": "真实 DRI 交付、Outcome 达成与 MF 关闭分别判断", "domain_id": self.s.domain,
                "object_ids": self.ids, "include_raw": True, **(body or {}), **overrides}
        before = snapshot(self.s)
        response = (client or self.s.ceo).json("POST", ENDPOINT, body, expected=expected)
        after = snapshot(self.s)
        assert before == after, "Narrative POST mutated governed business, snapshots, receipts or outbox"
        self.reads += 1
        if expected == 200:
            self.contract(response, body)
        return response

    def contract(self, response, body):
        for key in ("narrative", "hit_paths", "lateral_nodes", "root_statement", "tenant", "org", "model", "narrative_raw_chars", "narrative_chars", "governed_facts", "provenance"):
            assert key in response, f"Missing compatible Narrative response field: {key}"
        assert response["narrative"].strip()
        assert response["model"] == "deterministic-v1"
        assert response["tenant"] == self.s.f["tenant_id"] and response["org"] == self.s.f["company_id"]
        assert response["narrative_chars"] == len(response["narrative"])
        if body.get("include_raw"):
            assert response["narrative_raw"].strip()
            assert response["narrative_raw_chars"] == len(response["narrative_raw"])
        else:
            assert "narrative_raw" not in response
        facts = response["governed_facts"]
        assert facts["schema_version"] == "governed-facts.v1"
        assert facts["domain_id"] == self.s.domain
        assert facts["returned_count"] == len(facts["selected"])
        assert facts["truncated"] is False, "Small fixture was unexpectedly truncated"
        required = {"object_id", "object_type", "revision_id", "payload_hash", "payload", "recorded_at", "valid_from", "valid_to", "lifecycle_status", "state_source", "source_refs"}
        for row in facts["selected"]:
            assert required.issubset(row), "Selected fact omitted authority/temporal provenance"
            assert HASH.fullmatch(row["payload_hash"])
            exact = self.s.ceo.revision(row["object_id"], row["revision_id"])
            assert all(value == exact["payload"][key] for key, value in row["payload"].items())
            assert row["payload_hash"] == exact["payload_hash"]
            for source in row["source_refs"]:
                exact_source = self.s.ceo.revision(source["object_id"], source["revision_id"])
                assert source["payload_hash"] == exact_source["payload_hash"]
        provenance = response["provenance"]
        assert provenance["schema_version"] == "narrative-provenance.v1"
        assert provenance["scope_id"] == self.s.f["scope_id"] and provenance["domain_id"] == self.s.domain
        assert provenance["valid_at"] == facts["valid_at"] and provenance["known_at"] == facts["known_at"]
        assert provenance["legacy"] is None and provenance["compression_mode"] == "none"
        for field in ("governed_sha256", "narrative_sha256"):
            assert HASH.fullmatch(provenance[field])
        assert provenance["narrative_sha256"] == sha256(response["narrative"].encode())
        assert provenance["governed_sha256"] == sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())

    @staticmethod
    def selected(response):
        return {row["object_id"]: row for row in response["governed_facts"]["selected"]}

    def state(self, expected_work, expected_outcome, expected_feedback, **kwargs):
        response = self.call(**kwargs)
        facts = self.selected(response)
        assert facts[self.work]["lifecycle_status"] == expected_work
        assert facts[self.outcome]["outcome_achievement"] == expected_outcome
        assert facts[self.feedback]["lifecycle_status"] == expected_feedback
        return response

    def upload(self, version, value):
        content = f"Synthetic narrative acceptance source v{version}: qualified deliveries={value}.\n".encode()
        asset = self.s.mission.json("POST", "/v1/evidence-assets", {"domain_id": self.s.domain,
            "title": f"Narrative source v{version}", "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain"})
        raw = self.s.verifier.request("GET", f"/v1/evidence-assets/{asset['object_id']}/revisions/{asset['revision_id']}").content
        assert raw == content and asset["sha256"] == sha256(content)
        return asset

    def review_params(self, submission, result):
        return {"deliverable_revision_id": submission["deliverable_revision_id"], "delivery_payload_hash": submission["payload_hash"],
            "verification_result": result, "criterion_results": [
                {"criterion_id": "complete", "result": "failed" if result == "changes_requested" else "passed", "note": "Raw source needs supplement" if result == "changes_requested" else "Raw source checked"},
                {"criterion_id": "consistent", "result": "passed", "note": "Values reconcile with submitted source"}],
            "review_note": "Supplement original evidence" if result == "changes_requested" else "All frozen criteria independently checked"}

    def delivery(self):
        s = self.s
        with self.h.group("narrative_01_real_dri_delivery_versions") as report:
            s.act(s.mission, "accept_work_item", {}, oid=self.work, prepare=True)
            self.state("in_progress", "not_assessed", "investigating")
            self.evidence1 = self.upload(1, 72)
            self.v1 = s.act(s.mission, "submit_deliverable", {"title": "Narrative delivery v1", "summary": "Initial source package", "evidence_revision_ids": [self.evidence1["revision_id"]]}, oid=self.work, prepare=True)["result"]
            self.deliverable = self.v1["deliverable_object_id"]
            self.ids.append(self.deliverable)
            self.checkpoints["v1_submitted"] = utc_now()
            first = self.state("submitted", "not_assessed", "investigating")
            assert self.selected(first)[self.deliverable]["delivery_status"] == "submitted"
            self.returned = s.act(s.verifier, "review_deliverable", self.review_params(self.v1, "changes_requested"), oid=self.work, prepare=True)
            returned = self.state("changes_requested", "not_assessed", "investigating")
            assert self.selected(returned)[self.deliverable]["delivery_status"] == "changes_requested"
            self.checkpoints["v1_returned"] = utc_now()
            self.evidence2 = self.upload(2, 72)
            self.v2 = s.act(s.mission, "submit_deliverable", {"title": "Narrative delivery v2", "summary": "Supplemented original evidence", "evidence_revision_ids": [self.evidence2["revision_id"]], "responds_to_acceptance_id": self.returned["result"]["acceptance_id"]}, oid=self.work, prepare=True)["result"]
            self.checkpoints["v2_submitted"] = utc_now()
            self.state("submitted", "not_assessed", "investigating")
            self.approved = s.act(s.verifier, "review_deliverable", self.review_params(self.v2, "accepted"), oid=self.work, prepare=True)
            accepted = self.state("delivery_accepted", "not_assessed", "investigating")
            selected = self.selected(accepted)[self.deliverable]
            assert selected["revision_id"] == self.v2["deliverable_revision_id"] and selected["delivery_status"] == "accepted"
            assert self.evidence2["revision_id"] in {row["revision_id"] for row in selected["source_refs"]}
            self.checkpoints["delivery_accepted"] = utc_now()
            rows = readonly_sql(s, "SELECT submission_seq,verification_result,verifier_principal_id::text FROM gov_delivery_acceptances WHERE scope_id=%s AND work_item_object_id=%s ORDER BY submission_seq", (s.f["scope_id"], self.work))
            assert rows == [(1, "changes_requested", s.f["actors"]["verifier"]["principal_id"]), (2, "accepted", s.f["actors"]["verifier"]["principal_id"])]
            report.update(submissions=2, immutable_reviews=2, named_human_verifier_verified=True, after_delivery={"work": "delivery_accepted", "outcome": "not_assessed", "mf": "investigating"})

    def assess(self, result, value, asset):
        s = self.s
        observation = s.create("MetricObservation", {"title": f"Narrative observation {value}", "metric_id": "qualified-deliveries", "value": value, "unit": "deliveries", "valid_from": utc_now(), "upstream_refs": [s.upstream(self.outcome), s.upstream(asset["object_id"], asset["revision_id"])]})
        return s.act(s.ceo, "record_outcome_assessment", {"assessment_result": result, "observation_revision_ids": [s.object(observation)["latest_revision_id"]], "evidence_revision_ids": [asset["revision_id"]], "delivery_acceptance_ids": [self.approved["result"]["acceptance_id"]], "assessment_note": f"Independent human assessment: observed {value} against target 80."}, oid=self.outcome, prepare=True)

    def independent_judgments(self):
        s = self.s
        with self.h.group("narrative_02_outcome_and_mf_remain_independent") as report:
            self.assess("not_achieved", 72, self.evidence2)
            self.checkpoints["outcome_not_achieved"] = utc_now()
            self.state("delivery_accepted", "not_achieved", "investigating")
            decision = s.decision("Narrative independent MF no-change resolution")
            decision_revision = s.object(decision)["effective_revision_id"]
            s.act(s.dri, "request_feedback_acceptance", {"decision_revision_id": decision_revision}, oid=self.feedback, prepare=True)
            mf_acceptance = s.act(s.verifier, "record_acceptance", {"decision_revision_id": decision_revision, "evidence_revision_ids": [self.evidence2["revision_id"]], "verification_result": "accepted"}, oid=self.feedback, prepare=True)
            s.act(s.ceo, "confirm_closure", {"acceptance_record_id": mf_acceptance["result"]["acceptance_id"], "resolution_decision_revision_id": decision_revision, "closure_evidence_revision_ids": [self.evidence2["revision_id"]], "disposition": "no_change", "closure_note": "Separately resolved evidence completeness; Outcome remains below target."}, oid=self.feedback, prepare=True)
            self.state("delivery_accepted", "not_achieved", "closed")
            self.checkpoints["mf_closed"] = utc_now()
            self.evidence3 = self.upload(3, 80)
            self.assess("achieved", 80, self.evidence3)
            self.checkpoints["outcome_achieved"] = utc_now()
            self.state("delivery_accepted", "achieved", "closed")
            rows = readonly_sql(s, "SELECT assessment_result,assessor_principal_id::text FROM gov_outcome_assessments WHERE scope_id=%s AND outcome_object_id=%s ORDER BY recorded_at,assessment_id", (s.f["scope_id"], self.outcome))
            assert rows == [("not_achieved", s.f["actors"]["ceo"]["principal_id"]), ("achieved", s.f["actors"]["ceo"]["principal_id"])]
            report.update(assessment_history=["not_achieved", "achieved"], mf_closed_while_outcome_not_achieved=True, named_human_ceo_verified=True)

    def historical_and_drafts(self):
        s = self.s
        with self.h.group("narrative_03_temporal_authority_and_draft_exclusion") as report:
            current = utc_now()
            for checkpoint, work_status, outcome_status, mf_status, delivery_revision, delivery_status in (
                ("v1_submitted", "submitted", "not_assessed", "investigating", self.v1["deliverable_revision_id"], "submitted"),
                ("v1_returned", "changes_requested", "not_assessed", "investigating", self.v1["deliverable_revision_id"], "changes_requested"),
                ("v2_submitted", "submitted", "not_assessed", "investigating", self.v2["deliverable_revision_id"], "submitted"),
                ("delivery_accepted", "delivery_accepted", "not_assessed", "investigating", self.v2["deliverable_revision_id"], "accepted"),
                ("outcome_not_achieved", "delivery_accepted", "not_achieved", "investigating", self.v2["deliverable_revision_id"], "accepted"),
                ("mf_closed", "delivery_accepted", "not_achieved", "closed", self.v2["deliverable_revision_id"], "accepted"),
            ):
                at = self.checkpoints[checkpoint]
                for valid_at, known_at in ((at, current), (current, at)):
                    response = self.state(work_status, outcome_status, mf_status, valid_at=valid_at, known_at=known_at)
                    exact = self.selected(response)[self.deliverable]
                    assert exact["revision_id"] == delivery_revision and exact["delivery_status"] == delivery_status
            self.draft_marker = "DRAFT-DO-NOT-SHOW-" + self.h.run_id
            draft = s.create("Decision", {"title": self.draft_marker, "statement": "Unconfirmed draft is not authoritative."})
            response = self.call(object_ids=[self.work, draft])
            assert draft not in self.selected(response)
            assert draft in {row["object_id"] for row in response["governed_facts"]["excluded"]}
            assert self.draft_marker not in response["narrative"]
            empty = self.call(object_ids=[draft], expected=404)
            assert empty["error"]["code"] == "NARRATIVE_EMPTY"
            bc = self.business["business_commitment_id"]
            original = s.object(bc)["effective_revision_id"]
            new_payload = {**s.object(bc)["effective_revision"]["payload"], "title": self.draft_marker + "-candidate"}
            s.propose(bc, new_payload, deps=(self.outcome,))
            baseline = self.call(object_ids=[bc])
            assert self.selected(baseline)[bc]["revision_id"] == original
            assert self.draft_marker not in baseline["narrative"]
            report.update(historical_checkpoints=6, both_time_axes_checked=True, draft_excluded=True, unconfirmed_candidate_did_not_replace_effective=True)

    def repetitions_and_client(self, clark_root):
        with self.h.group("narrative_04_repeatable_read_and_real_clark_client") as report:
            at = utc_now()
            responses = [self.call(valid_at=at, known_at=at) for _ in range(3)]
            assert all(item == responses[0] for item in responses), "Fixed-time repeated Narrative response changed"
            self.call(include_raw=False)
            config = self.h.private / "clark-narrative-client.json"
            output = self.h.output / "clark-client-report.json"
            private_json(config, {"clark_root": str(clark_root), "fixture_file": str(self.h.private / "fixture.json"), "api_url": self.s.api_url, "query": "Clark 联调：Narrative 实际交付", "required_markers": [], "forbidden_markers": [self.draft_marker], "output_file": str(output)})
            before = snapshot(self.s)
            node = shutil.which("node")
            assert node, "Node.js is required to execute the actual Clark client"
            completed = subprocess.run([node, str(Path(__file__).with_name("clark_client.cjs")), str(config)], capture_output=True, text=True, timeout=45, cwd=clark_root)
            assert completed.returncode == 0, "Actual Clark NarrativeClient failed; inspect sanitized client output"
            assert snapshot(self.s) == before, "Clark repeated Narrative POST mutated Runtime state"
            client_report = json.loads(output.read_text())
            assert client_report["status"] == "passed" and len(client_report["results"]) == 3
            report.update(repeated_identical_fixed_time_calls=3, entire_scope_rows_unchanged=True,
                          real_clark_client=client_report, snapshot_and_receipt_count_unchanged=True)

    def isolation_and_revocation(self):
        s = self.s
        with self.h.group("narrative_05_scope_domain_and_revocation") as report:
            _, other_path = create_fixture(self.h.run_id + "-other")
            other = json.loads(other_path.read_text())
            foreign = Client(s.api_url, other["actors"]["ceo"]["token"], "other-tenant-ceo", self.h.log)
            try:
                own = foreign.json("POST", ENDPOINT, {"query": "Synthetic confirmed CompanyOutcome", "object_ids": [other["outcome"]["object_id"]], "domain_id": other["domain_id"]})
                assert own["tenant"] == other["tenant_id"]
                for body in ({"tenant": other["tenant_id"]}, {"org": other["company_id"]}, {"domain_id": other["domain_id"]}):
                    rejected = self.call(expected=403, **body)
                    assert rejected["error"]["code"] == "FORBIDDEN"
                self.call(object_ids=[other["outcome"]["object_id"]], expected=404)
                self.call(client=foreign, expected=403)
                malformed = self.call(body={"scope_id": other["scope_id"]}, expected=422)
                assert malformed["error"]["code"] == "INVALID_REQUEST"
            finally:
                foreign.close()
            self.call(client=s.clients["outsider"], expected=403)
            response = httpx.post(s.api_url + ENDPOINT, json={"query": "company"}, trust_env=False)
            assert response.status_code == 401
            before = snapshot(s)
            forged = s.ceo.json("POST", ENDPOINT, {"query": "company", "domain_id": s.domain, "object_ids": [self.work]}, headers={"X-Tenant-ID": "forged-tenant", "X-Organization-ID": "forged-company"})
            assert forged["tenant"] == s.f["tenant_id"] and forged["org"] == s.f["company_id"]
            assert snapshot(s) == before
            pre = self.call(client=s.verifier)
            assert pre["provenance"]["principal_id"] == s.f["actors"]["verifier"]["principal_id"]
            s.act(s.ceo, "revoke_assignment", {"assignment_id": s.f["actors"]["verifier"]["assignment_id"]})
            denied = self.call(client=s.verifier, expected=403)
            assert denied["error"]["code"] == "FORBIDDEN"
            self.state("delivery_accepted", "achieved", "closed")
            report.update(cross_tenant_denied=True, cross_domain_denied=True, body_scope_spoof_denied=True,
                          forged_headers_did_not_change_scope=True, revoked_identity_denied=True, other_tenant_positive_control=True,
                          authorized_ceo_still_reads=True, unauthorized_http_status=401)

    def availability_boundaries(self):
        s = self.s
        with self.h.group("narrative_06_explicit_enable_and_upstream_failure_boundary") as report:
            health = httpx.get(s.api_url + "/healthz", trust_env=False)
            assert health.status_code == 200
            assert health.json() == {"ok": True, "service": "tkos-ontology-runtime", "narrative": True}
            for updates, status, code in (
                ({"TKOS_NARRATIVE_ENABLED": "0"}, 503, "NARRATIVE_NOT_CONFIGURED"),
                ({"TKOS_NARRATIVE_COMPRESSION": "invalid-mode"}, 503, "NARRATIVE_UNAVAILABLE"),
                ({"TKOS_NARRATIVE_LEGACY_ENABLED": "1"}, 403, "FORBIDDEN"),
            ):
                with variant_api(s, updates) as client:
                    response = self.call(client=client, expected=status)
                    assert response["error"]["code"] == code
            grant_test_legacy_read(s)
            with failing_embedding_provider() as (url, calls, marker):
                with variant_api(s, {"TKOS_NARRATIVE_LEGACY_ENABLED": "1", "MEMORY_EMBEDDING_BASE_URL": url,
                        "MEMORY_EMBEDDING_API_KEY": "synthetic-acceptance-placeholder", "MEMORY_EMBEDDING_MODEL": "failure-boundary-only"}) as client:
                    failure = self.call(client=client, query="Synthetic unique upstream boundary " + self.h.run_id, expected=503)
                    assert failure["error"]["code"] == "NARRATIVE_UNAVAILABLE"
                    assert marker not in json.dumps(failure)
                assert calls == [{"method": "POST", "path": "/embeddings/multimodal"}], "The actual adapter did not reach the isolated failure provider"
            self.state("delivery_accepted", "achieved", "closed")
            report.update(minimal_anonymous_health=True, explicit_enable_required=True, legacy_read_permission_required=True,
                          actual_embedding_failure_http_calls=1, upstream_diagnostic_redacted=True,
                          failure_fixture_only=True, vectors_generated=False, chat_model_called=False,
                          business_and_snapshots_unchanged_by_failed_reads=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    parser.add_argument("--clark-root", type=Path, default=Path("/Users/yusiyi/ysy/clark"))
    args = parser.parse_args()
    run_id, fixture = create_fixture(args.run_id or "narrative-" + uuid.uuid4().hex[:12])
    h = NarrativeHarness(run_id=run_id)
    # The default acceptance route must be deterministic and must not contact any LLM.
    h.env.update(TKOS_NARRATIVE_ENABLED="1", TKOS_NARRATIVE_COMPRESSION="none", TKOS_NARRATIVE_LEGACY_ENABLED="0")
    s = None
    try:
        s = Scenario(h, json.loads(fixture.read_text()))
        with h.group("narrative_00_isolated_http_business_setup") as report:
            s.start_worker()
            business = setup_one(s, "narrative", "Narrative 实际交付")
            s.stop_worker()
            report.update(delivery_preseeded=False, outcome_assessment_preseeded=False, mf_closed=False, activation_effects_terminal=True)
        suite = NarrativeSuite(s, business)
        suite.delivery()
        suite.independent_judgments()
        suite.historical_and_drafts()
        suite.repetitions_and_client(args.clark_root)
        suite.isolation_and_revocation()
        suite.availability_boundaries()
        h.complete = True
        manifest = {str(path.relative_to(ROOT)): sha256(path.read_bytes()) for path in sorted((ROOT / "acceptance/narrative").glob("*")) if path.is_file()}
        path = h.save_report(narrative_read_calls=suite.reads, acceptance_source_sha256=manifest)
        print(json.dumps({"report": str(path), "narrative_adapter_accepted": True, "real_model_accepted": False}))
    finally:
        if s:
            for client in s.clients.values():
                client.close()
        h.stop_all()


if __name__ == "__main__":
    main()
