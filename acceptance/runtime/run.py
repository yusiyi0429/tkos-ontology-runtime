"""Independent acceptance through real HTTP, with database and external oracles.

Only provisioning uses a fixture file. All business transitions below must be
created by the API under test. Do not replace failed assertions with seeded state.
"""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import psycopg
from psycopg.rows import dict_row

from acceptance.runtime.client import Client, assert_error, sha256, utc_now
from acceptance.runtime.harness import Harness, private_json, wait_until


class Scenario:
    def __init__(self, harness: Harness, fixture: dict):
        self.h = harness
        self.f = fixture
        self.h.env.update(MEMORY_TENANT=fixture["tenant_id"], MEMORY_ORG=fixture["company_id"])
        self.worker = None
        self.worker_number = 0
        self.receipts = []
        self.evidence_bytes = b"Synthetic verification: delivery measured 72, independently checked against source.\n"
        self.ledger = self.h.private / "receiver.sqlite"
        self.receiver = self.h.spawn("receiver", "receiver.py", "--port", 0, "--ledger", self.ledger,
                                     "--ready-file", self.h.private / "receiver-ready.json")
        receiver = wait_until(lambda: self.ready("receiver-ready.json"), message="receiver start")
        self.receiver_url = receiver["url"]
        self.h.env["GOVERNED_EFFECT_URL"] = self.receiver_url + "/effects"
        self.start_api()
        self.clients = {
            name: Client(self.api_url, actor["token"], name, self.h.log)
            for name, actor in fixture["actors"].items()
        }
        self.ceo = self.clients["ceo"]
        self.dri = self.clients["domain_dri"]
        self.mission = self.clients["mission_dri"]
        self.verifier = self.clients["verifier"]
        self.agent = self.clients["agent"]
        self.outcome = fixture["outcome"]["object_id"]
        self.domain = fixture["domain_id"]

    def ready(self, filename):
        path = self.h.private / filename
        return json.loads(path.read_text()) if path.exists() else None

    def start_api(self):
        ready_file = self.h.private / f"api-ready-{uuid.uuid4().hex}.json"
        self.api = self.h.spawn("api", "server.py", "--port", 0, "--control-dir", self.h.control,
                                "--key-file", self.h.key_file, "--ready-file", ready_file)
        ready = wait_until(lambda: json.loads(ready_file.read_text()) if ready_file.exists() else None,
                           timeout=25, message="API start")
        self.api_url = ready["url"]
        self.h.wait_http(self.api_url + "/v1/health")

    def start_worker(self, *, pause=None, task=None):
        assert self.worker is None or self.worker.poll() is not None
        self.worker_number += 1
        args = ["--control-dir", self.h.control]
        if pause:
            args += ["--pause-after-effect", pause, "--task-id", task]
        self.worker = self.h.spawn(
            f"worker-{self.worker_number}", "worker.py", *args,
            updates={"RUNTIME_WORKER_ID": f"{self.h.run_id}-{self.worker_number}",
                     "RUNTIME_TASK_LEASE_SECONDS": 5, "RUNTIME_WORKER_POLL_SECONDS": 0.1,
                     "RUNTIME_TASK_RETRY_BASE_SECONDS": 1},
        )

    def stop_worker(self, *, kill=False):
        if self.worker:
            self.h.stop(self.worker, kill=kill)
            self.worker = None

    def sql(self, statement, params=(), *, app=False):
        url = self.h.env["APP_DATABASE_URL" if app else "MIGRATION_DATABASE_URL"]
        with psycopg.connect(url, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (self.f["scope_id"],))
            return conn.execute(statement, params).fetchall()

    def object(self, object_id):
        return self.ceo.object(object_id)

    def target(self, object_id, revision_id=None):
        obj = self.object(object_id)
        return {"object_id": object_id, "expected_version": obj["object_version"],
                "revision_id": revision_id or obj["latest_revision_id"]}

    def deps(self, *object_ids):
        return [{"object_id": oid, "expected_version": self.object(oid)["object_version"]}
                for oid in dict.fromkeys(object_ids)]

    def act(self, client, kind, params, *, oid=None, revision=None, deps=(), prepare=False, **kwargs):
        target = self.target(oid, revision) if oid else None
        versions = self.deps(*deps)
        if prepare:
            proposal = client.command(kind, params, target=target, expected_versions=versions)
            prepared = client.json("POST", "/v1/actions/prepare", proposal)
            versions = prepared["expected_versions"]
        receipt = client.action(kind, params, target=target, expected_versions=versions, **kwargs)
        if "receipt_id" in receipt:
            self.receipts.append(receipt)
        return receipt

    def create(self, kind, payload, *, client=None):
        receipt = self.act(client or self.ceo, "create_object",
                           {"object_type": kind, "domain_id": self.domain, "payload": payload}, prepare=True)
        return receipt["result"]["object_id"]

    def upstream(self, object_id, revision_id=None):
        return {"object_id": object_id,
                "revision_id": revision_id or self.object(object_id)["effective_revision_id"]}

    def commitment_payload(self, kind, upstream, *, value=80):
        parties = ("ceo", "domain_dri") if kind == "BusinessCommitment" else ("domain_dri", "mission_dri")
        return {"title": f"Synthetic {kind}", "terms": {"target": value, "unit": "deliveries"},
                "required_assignment_ids": [self.f["actors"][name]["assignment_id"] for name in parties],
                "upstream_refs": [upstream]}

    def accept(self, object_id, actor, *, expected=200, **kwargs):
        target = self.target(object_id)
        revision = self.ceo.revision(object_id, target["revision_id"])
        params = {"party_assignment_id": self.f["actors"][actor]["assignment_id"],
                  "understanding": "I understand and accept these exact synthetic terms.",
                  "accepted_terms_hash": revision["payload_hash"]}
        versions = []
        if expected == 200:
            prepared = self.clients[actor].json("POST", "/v1/actions/prepare", self.clients[actor].command("accept_commitment", params, target=target))
            versions = prepared["expected_versions"]
        result = self.clients[actor].action("accept_commitment", params, target=target,
                                           expected_versions=versions, expected=expected, **kwargs)
        if "receipt_id" in result:
            self.receipts.append(result)
        return result

    def signed(self, kind, parent):
        oid = self.create(kind, self.commitment_payload(kind, self.upstream(parent)))
        actors = ("ceo", "domain_dri") if kind == "BusinessCommitment" else ("domain_dri", "mission_dri")
        handshakes = [self.accept(oid, actor)["result"]["handshake_id"] for actor in actors]
        return oid, handshakes

    def activation_params(self, handshakes):
        return {"handshake_record_ids": handshakes,
                "activation_policy_revision_id": self.f["policy_revision_id"]}

    def activate(self, oid, handshakes, parent, **kwargs):
        return self.act(self.ceo, "activate_commitment", self.activation_params(handshakes),
                        oid=oid, deps=(parent,), **kwargs)

    def tasks(self, task_ids):
        return self.sql("SELECT task_id::text, state, attempt, result, error_code FROM runtime_tasks WHERE task_id = ANY(%s::uuid[]) ORDER BY task_id",
                        (task_ids,))

    def wait_tasks(self, task_ids, *, state="succeeded"):
        assert task_ids, "a real outbox task is required"
        def terminal():
            rows = self.tasks(task_ids)
            if any(row["state"] == "failed" for row in rows) and state != "failed":
                raise AssertionError(f"unexpected task failure: {rows}")
            return rows if len(rows) == len(task_ids) and all(row["state"] == state for row in rows) else None
        return wait_until(terminal, timeout=30, message=f"tasks did not reach {state}")

    def receipt_effects(self):
        return [task for receipt in self.receipts for task in receipt.get("effect_task_ids", [])]

    def snapshot(self):
        from acceptance.runtime.sql_oracle import snapshot_scope
        with psycopg.connect(self.h.env["MIGRATION_DATABASE_URL"]) as conn:
            return snapshot_scope(conn, self.f["scope_id"], self.f["tenant_id"], self.f["company_id"])

    def group_handshake(self):
        with self.h.group("01_authenticated_two_party_activation") as result:
            unauth = httpx.get(self.api_url + f"/v1/objects/{self.outcome}", trust_env=False)
            assert unauth.status_code == 401
            self.bc = self.create("BusinessCommitment", self.commitment_payload("BusinessCommitment", self.upstream(self.outcome)))
            assert self.object(self.bc)["effective_revision_id"] is None
            first = self.accept(self.bc, "ceo")
            first_id = first["result"]["handshake_id"]
            assert self.object(self.bc)["effective_revision_id"] is None
            # A duplicate party cannot provide the missing independent signature.
            rejected = self.activate(self.bc, [first_id, str(uuid.uuid4())], self.outcome, expected=409)
            assert rejected.get("error")
            assert self.object(self.bc)["effective_revision_id"] is None
            assert self.accept(self.bc, "agent", expected=403).get("error")
            second = self.accept(self.bc, "domain_dri")
            assert self.object(self.bc)["effective_revision_id"] is None, "second signature is not activation"
            self.bc_activation = self.activate(self.bc, [first_id, second["result"]["handshake_id"]], self.outcome)
            assert self.object(self.bc)["lifecycle_status"] == "active"
            self.ec, ec_signatures = self.signed("ExecutionCommitment", self.bc)
            self.ec_activation = self.activate(self.ec, ec_signatures, self.bc)
            assert self.object(self.ec)["lifecycle_status"] == "active"
            self.bc_r1 = self.object(self.bc)["effective_revision_id"]
            self.ec_r1 = self.object(self.ec)["effective_revision_id"]
            rows = self.sql("SELECT object_id::text, count(*) AS signatures, count(DISTINCT principal_id) AS people, count(DISTINCT revision_id) AS revisions, count(DISTINCT terms_hash) AS hashes FROM gov_handshakes WHERE object_id=ANY(%s::uuid[]) GROUP BY object_id", ([self.bc, self.ec],))
            assert len(rows) == 2 and all(row["signatures"] == row["people"] == 2 and row["revisions"] == row["hashes"] == 1 for row in rows)
            result.update(business_commitment=self.bc, execution_commitment=self.ec, sql_handshakes=rows)
            self.start_worker()
            self.wait_tasks(self.receipt_effects())
            self.stop_worker()

    def feedback_investigation(self, title):
        oid = self.create("FeedbackThread", {"title": title, "description": "Synthetic business feedback requiring investigation."})
        self.act(self.ceo, "route_feedback", {"responsible_assignment_id": self.f["actors"]["domain_dri"]["assignment_id"]}, oid=oid)
        self.act(self.dri, "accept_feedback", {}, oid=oid)
        self.act(self.dri, "investigate_feedback", {}, oid=oid)
        assert self.object(oid)["lifecycle_status"] == "investigating"
        return oid

    def decision(self, title):
        oid = self.create("Decision", {"title": title, "statement": "Human-approved synthetic resolution decision."})
        self.act(self.ceo, "confirm_decision", {}, oid=oid)
        assert self.object(oid)["lifecycle_status"] == "confirmed"
        return oid

    def propose(self, oid, payload, *, bundle_id=None, deps=()):
        params = {"payload": payload}
        if bundle_id:
            params["bundle_id"] = bundle_id
        self.act(self.ceo, "propose_revision", params, oid=oid, deps=deps)
        return self.object(oid)["latest_revision_id"]

    def group_bundle(self):
        with self.h.group("02_atomic_adjustment_and_rollback") as result:
            self.fb = self.feedback_investigation("Synthetic commitment delivery shortfall")
            self.dec = self.decision("Adjust both business and execution commitments")
            feedback_revision = self.object(self.fb)["latest_revision_id"]
            decision_revision = self.object(self.dec)["effective_revision_id"]
            shell = {"title": "Synthetic complete adjustment bundle", "feedback_revision_id": feedback_revision,
                     "decision_revision_id": decision_revision, "changes": []}
            self.adjustment = self.create("ManagementAdjustment", shell)
            self.bc_r2 = self.propose(self.bc, self.commitment_payload("BusinessCommitment", self.upstream(self.outcome), value=72),
                                     bundle_id=self.adjustment, deps=(self.outcome, self.adjustment))
            self.ec_r2 = self.propose(self.ec, self.commitment_payload("ExecutionCommitment", self.upstream(self.bc, self.bc_r2), value=72),
                                     bundle_id=self.adjustment, deps=(self.bc, self.adjustment))
            self.changes = [{"object_id": self.bc, "from_revision_id": self.bc_r1, "to_revision_id": self.bc_r2},
                            {"object_id": self.ec, "from_revision_id": self.ec_r1, "to_revision_id": self.ec_r2}]
            self.propose(self.adjustment, {**shell, "changes": self.changes}, deps=(self.bc, self.ec, self.fb, self.dec))
            bc_signatures = [self.accept(self.bc, role)["result"]["handshake_id"] for role in ("ceo", "domain_dri")]
            for role in ("domain_dri", "mission_dri"):
                self.accept(self.ec, role)
            assert self.object(self.bc)["effective_revision_id"] == self.bc_r1
            assert self.object(self.ec)["effective_revision_id"] == self.ec_r1
            denied = self.activate(self.bc, bc_signatures, self.outcome, expected=409)
            assert denied.get("error"), "bundle candidate must never activate alone"
            params = {"decision_revision_id": decision_revision, "changes": self.changes,
                      "feedback_revision_id": feedback_revision}
            deps = (self.bc, self.ec, self.fb, self.dec, self.outcome)
            before = self.snapshot()
            denied = self.act(self.ceo, "confirm_adjustment", params, oid=self.adjustment,
                              deps=(self.bc, self.fb, self.dec, self.outcome), expected=409)
            assert_error(denied, "DEPENDENCY_MISSING")
            assert self.snapshot() == before, "missing dependency changed durable state"
            token = "bundle-fail-" + uuid.uuid4().hex[:8]
            body = self.ceo.command("confirm_adjustment", params, target=self.target(self.adjustment), expected_versions=self.deps(*deps))
            self.ceo.request("POST", "/v1/actions", body, expected=500,
                             headers=self.h.headers("after_first_bundle_update", "fail", token))
            self.h.reached(token)
            assert self.snapshot() == before, "injected mid-bundle failure did not roll back all state"
            self.adjustment_receipt = self.act(self.ceo, "confirm_adjustment", params, oid=self.adjustment, deps=deps)
            assert self.object(self.bc)["effective_revision_id"] == self.bc_r2
            assert self.object(self.ec)["effective_revision_id"] == self.ec_r2
            assert self.object(self.adjustment)["lifecycle_status"] == "applied"
            assert self.object(self.fb)["lifecycle_status"] == "awaiting_acceptance"
            ec_revision = self.ceo.revision(self.ec, self.ec_r2)
            assert {"object_id": self.bc, "revision_id": self.bc_r2} in ec_revision["payload"]["upstream_refs"]
            assert all(row["state"] == "queued" for row in self.tasks(self.adjustment_receipt["effect_task_ids"]))
            result.update(adjustment_id=self.adjustment, feedback_id=self.fb, receipt_id=self.adjustment_receipt["receipt_id"],
                          rollback_snapshot=before, effective_revisions={self.bc: self.bc_r2, self.ec: self.ec_r2})

    def group_concurrency(self):
        with self.h.group("03_real_concurrent_version_compare_and_swap") as result:
            target = self.target(self.bc)
            before = self.object(self.bc)
            barrier = threading.Barrier(2)
            commands = [self.ceo.command("propose_revision", {"payload": self.commitment_payload("BusinessCommitment", self.upstream(self.outcome), value=value)},
                                         target=target, expected_versions=self.deps(self.outcome)) for value in (70, 71)]
            def submit(client, body):
                barrier.wait(timeout=5)
                return client.request("POST", "/v1/actions", body, expected=(200, 409))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit, client, command) for client, command in zip((self.ceo, self.dri), commands)]
                responses = [future.result(timeout=30) for future in futures]
            assert sorted(response.status_code for response in responses) == [200, 409]
            winner = next(response.json() for response in responses if response.status_code == 200)
            loser = next(response.json() for response in responses if response.status_code == 409)
            assert_error(loser, "VERSION_CONFLICT")
            after = self.object(self.bc)
            assert after["object_version"] == before["object_version"] + 1
            assert after["effective_revision_id"] == before["effective_revision_id"] == self.bc_r2
            self.receipts.append(winner)
            result.update(winning_receipt_id=winner["receipt_id"], before_version=before["object_version"], after_version=after["object_version"])

    def group_idempotency(self):
        with self.h.group("04_idempotency_after_committed_response_loss") as result:
            oid, signatures = self.signed("BusinessCommitment", self.outcome)
            params = self.activation_params(signatures)
            command = self.ceo.command("activate_commitment", params, target=self.target(oid), expected_versions=self.deps(self.outcome))
            ready_file = self.h.private / "proxy-ready.json"
            proxy = self.h.spawn("proxy", "proxy.py", "--port", 0, "--upstream", self.api_url,
                                  "--control-dir", self.h.control, "--key-file", self.h.key_file, "--ready-file", ready_file)
            ready = wait_until(lambda: json.loads(ready_file.read_text()) if ready_file.exists() else None)
            client = Client(ready["url"], self.f["actors"]["ceo"]["token"], "ceo-via-loss-proxy", self.h.log)
            token = "drop-" + uuid.uuid4().hex[:8]
            try:
                try:
                    client.request("POST", "/v1/actions", command,
                                   headers={"X-Acceptance-Key": self.h.key_file.read_text(), "X-Acceptance-Drop-Response": token})
                except httpx.TransportError:
                    pass
                else:
                    raise AssertionError("proxy did not actually lose the committed response")
                self.h.reached(token, "dropped")
                committed = self.snapshot()
                replay = client.json("POST", "/v1/actions", command)
                assert self.snapshot() == committed, "replay produced another durable change"
                again = self.ceo.json("POST", "/v1/actions", command)
                assert again == replay, "idempotent result is not the original frozen receipt"
                altered = {**command, "reason": "This is a different command body with the same key"}
                denied = self.ceo.json("POST", "/v1/actions", altered, expected=409)
                assert_error(denied, "IDEMPOTENCY_CONFLICT")
                assert self.snapshot() == committed
                assert self.object(oid)["lifecycle_status"] == "active"
                self.receipts.append(replay)
                result.update(receipt_id=replay["receipt_id"], effect_task_ids=replay["effect_task_ids"], post_commit_snapshot=committed)
            finally:
                client.close()
                self.h.stop(proxy)

    def upload_evidence(self):
        response = self.ceo.json("POST", "/v1/evidence-assets",
                                 {"domain_id": self.domain, "title": "Synthetic independent verification evidence",
                                  "content_base64": base64.b64encode(self.evidence_bytes).decode(), "media_type": "text/plain"})
        self.evidence = response["object_id"]
        self.evidence_revision = response["revision_id"]
        downloaded = self.ceo.request("GET", f"/v1/evidence-assets/{self.evidence}/revisions/{self.evidence_revision}").content
        assert downloaded == self.evidence_bytes
        return response

    def acceptance_params(self, decision):
        return {"decision_revision_id": self.object(decision)["effective_revision_id"],
                "evidence_revision_ids": [self.evidence_revision], "verification_result": "accepted"}

    def closure_params(self, acceptance, decision, disposition="resolved"):
        return {"acceptance_record_id": acceptance, "resolution_decision_revision_id": self.object(decision)["effective_revision_id"],
                "closure_evidence_revision_ids": [self.evidence_revision], "disposition": disposition,
                "closure_note": "Synthetic resolution independently verified with immutable source bytes."}

    def group_closure(self):
        with self.h.group("05_evidence_independent_acceptance_and_closure") as result:
            evidence = self.upload_evidence()
            deps = (self.dec, self.evidence, self.adjustment, self.bc, self.ec)
            denied = self.act(self.verifier, "record_acceptance", self.acceptance_params(self.dec), oid=self.fb, deps=deps, expected=409)
            assert denied.get("error"), "pending external effects must prevent acceptance"
            self.start_worker()
            self.wait_tasks(self.receipt_effects())
            denied = self.act(self.dri, "record_acceptance", self.acceptance_params(self.dec), oid=self.fb, deps=deps, expected=403)
            assert_error(denied, "FORBIDDEN")
            acceptance = self.act(self.verifier, "record_acceptance", self.acceptance_params(self.dec), oid=self.fb, deps=deps)
            self.acceptance_command = self.verifier.last_action_command
            self.acceptance_receipt_id = acceptance["receipt_id"]
            self.acceptance_id = acceptance["result"]["acceptance_id"]
            params = self.closure_params(self.acceptance_id, self.dec)
            denied = self.act(self.agent, "confirm_closure", params, oid=self.fb, deps=deps, expected=403)
            assert_error(denied, "FORBIDDEN")
            wrong = {**params, "closure_evidence_revision_ids": [str(uuid.uuid4())]}
            denied = self.act(self.ceo, "confirm_closure", wrong, oid=self.fb, deps=deps, expected=(404, 409))
            assert denied.get("error")
            assert self.object(self.fb)["lifecycle_status"] == "awaiting_acceptance"
            from acceptance.runtime.negative_checks import run_evidence_unavailable_check
            run_evidence_unavailable_check(self)
            self.closure_receipt = self.act(self.ceo, "confirm_closure", params, oid=self.fb, deps=deps)
            assert self.object(self.fb)["lifecycle_status"] == "closed"
            dispositions = {}
            for disposition in ("no_change", "dismissed"):
                fb = self.feedback_investigation(f"Synthetic {disposition} feedback")
                dec = self.decision(f"Synthetic {disposition} resolution")
                self.act(self.dri, "request_feedback_acceptance",
                         {"decision_revision_id": self.object(dec)["effective_revision_id"]}, oid=fb, deps=(dec,))
                accepted = self.act(self.verifier, "record_acceptance", self.acceptance_params(dec), oid=fb, deps=(dec, self.evidence))
                other_acceptance = accepted["result"]["acceptance_id"]
                cross_cycle = self.closure_params(self.acceptance_id, dec, disposition)
                denied = self.act(self.ceo, "confirm_closure", cross_cycle, oid=fb, deps=(dec, self.evidence), expected=409)
                assert denied.get("error")
                self.act(self.ceo, "confirm_closure", self.closure_params(other_acceptance, dec, disposition), oid=fb, deps=(dec, self.evidence))
                state = self.object(fb)["lifecycle_status"]
                assert state == ("dismissed" if disposition == "dismissed" else "closed")
                dispositions[disposition] = {"feedback_id": fb, "state": state}
                self.act(self.ceo, "reopen_feedback", {}, oid=fb)
                assert self.object(fb)["lifecycle_status"] == "investigating"
                denied = self.act(self.ceo, "confirm_closure", self.closure_params(other_acceptance, dec, disposition), oid=fb, deps=(dec, self.evidence), expected=409)
                assert denied.get("error"), "old-cycle acceptance reused after reopen"
            result.update(closure_receipt_id=self.closure_receipt["receipt_id"], acceptance_id=self.acceptance_id,
                          evidence_object=self.evidence, evidence_revision=self.evidence_revision,
                          evidence_sha256=sha256(self.evidence_bytes), dispositions=dispositions)
            self.stop_worker()

    def context(self, client, object_ids, valid_at, known_at, **kwargs):
        return client.json("POST", "/v1/context-packs", {"object_ids": object_ids, "valid_at": valid_at, "known_at": known_at}, **kwargs)

    def group_temporal(self):
        with self.h.group("07_valid_time_known_time_and_immutable_sources") as result:
            payload = {"title": "Synthetic August delivery measurement", "metric_id": "delivery-count",
                       "value": 80, "unit": "deliveries", "valid_from": "2026-08-01T00:00:00Z",
                       "valid_to": "2026-09-01T00:00:00Z", "upstream_refs": [self.upstream(self.evidence, self.evidence_revision)]}
            self.observation = self.create("MetricObservation", payload)
            r1 = self.object(self.observation)["latest_revision_id"]
            before_correction = utc_now()
            self.propose(self.observation, {**payload, "value": 72}, deps=(self.evidence,))
            r2 = self.object(self.observation)["latest_revision_id"]
            after_correction = utc_now()
            old = self.context(self.ceo, [self.observation], "2026-08-31T12:00:00Z", before_correction)
            current = self.context(self.ceo, [self.observation], "2026-08-31T12:00:00Z", after_correction)
            assert len(old["selected"]) == len(current["selected"]) == 1
            assert old["selected"][0]["revision_id"] == r1 and old["selected"][0]["payload"]["value"] == 80
            assert current["selected"][0]["revision_id"] == r2 and current["selected"][0]["payload"]["value"] == 72
            assert old["selected"][0]["payload"]["upstream_refs"] == [self.upstream(self.evidence, self.evidence_revision)]
            future = self.create("MetricObservation", {**payload, "title": "Synthetic future observation", "value": 100,
                                                       "valid_from": "2026-10-01T00:00:00Z", "valid_to": "2026-11-01T00:00:00Z"})
            historic = self.context(self.ceo, [self.observation, future], "2026-08-31T12:00:00Z", utc_now())
            assert {item["object_id"] for item in historic["selected"]} == {self.observation}
            frozen = self.ceo.json("GET", f"/v1/context-packs/{old['context_snapshot_id']}")
            assert frozen["selected"] == old["selected"], "snapshot body silently changed to latest"
            # The candidate produced by the CAS test must not replace the active commitment in context.
            active = self.context(self.ceo, [self.bc], utc_now(), utc_now())
            assert active["selected"][0]["revision_id"] == self.bc_r2
            assert self.ceo.revision(self.observation, r1)["payload"]["value"] == 80
            result.update(observation_id=self.observation, old_revision=r1, corrected_revision=r2,
                          old_snapshot_id=old["context_snapshot_id"], old_value=80, corrected_value=72,
                          excluded_future_object=future)

    def ledger_state(self):
        with httpx.Client(trust_env=False, timeout=3) as client:
            response = client.get(self.receiver_url + "/ledger")
            response.raise_for_status()
            return response.json()

    def group_worker(self):
        with self.h.group("08_worker_crash_replay_and_external_reconciliation") as result:
            self.stop_worker()
            oid, signatures = self.signed("BusinessCommitment", self.outcome)
            receipt = self.activate(oid, signatures, self.outcome)
            assert len(receipt["effect_task_ids"]) == 1
            task_id = receipt["effect_task_ids"][0]
            assert self.tasks([task_id])[0]["state"] == "queued"
            before = self.ledger_state()
            token = "worker-crash-" + uuid.uuid4().hex[:8]
            self.start_worker(pause=token, task=task_id)
            marker = self.h.reached(token)
            after_effect = self.ledger_state()
            assert after_effect["unique_effects"] == before["unique_effects"] + 1
            task = self.tasks([task_id])[0]
            assert task["state"] == "in_progress" and task["attempt"] == 1
            self.stop_worker(kill=True)
            self.start_worker()
            recovered = self.wait_tasks([task_id])[0]
            assert recovered["attempt"] >= 2
            final = self.ledger_state()
            assert final["unique_effects"] == after_effect["unique_effects"]
            assert final["replayed_calls"] >= after_effect["replayed_calls"] + 1
            # SQL against a separately persisted receiver is independent of the Runtime's report.
            with sqlite3.connect(self.ledger) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                assert "effects" in tables
                assert conn.execute("SELECT count(*) FROM effects").fetchone()[0] == final["unique_effects"]
            self.stop_worker()
            self.h.stop(self.receiver)
            ready_file = self.h.private / "receiver-restarted.json"
            self.receiver = self.h.spawn("receiver-restarted", "receiver.py", "--port", 0,
                                         "--ledger", self.ledger, "--ready-file", ready_file)
            ready = wait_until(lambda: json.loads(ready_file.read_text()) if ready_file.exists() else None)
            self.receiver_url = ready["url"]
            self.h.env["GOVERNED_EFFECT_URL"] = self.receiver_url + "/effects"
            assert self.ledger_state() == final, "receiver restart lost or changed its durable ledger"
            stored = self.ceo.json("GET", f"/v1/action-receipts/{receipt['receipt_id']}")
            assert stored["receipt"] == receipt
            assert all(effect["state"] == "succeeded" for effect in stored["effects"])
            assert self.object(oid)["lifecycle_status"] == "active"
            result.update(task_id=task_id, attempts=recovered["attempt"], receipt_id=receipt["receipt_id"],
                          unique_effects_before=before["unique_effects"], unique_effects_after=final["unique_effects"],
                          replayed_calls=final["replayed_calls"], receiver_restart_preserved_ledger=True,
                          crash_marker=marker)
            self.stop_worker()

    def group_revocation(self):
        with self.h.group("06_revocation_old_reads_and_authorization_races") as result:
            snapshot = self.context(self.verifier, [self.fb, self.evidence], utc_now(), utc_now())
            old_snapshot_id = snapshot["context_snapshot_id"]
            self.verifier.json("GET", f"/v1/action-receipts/{self.acceptance_receipt_id}")
            fb = self.feedback_investigation("Synthetic authorization fence acceptance")
            dec = self.decision("Synthetic authorization fence resolution")
            self.act(self.dri, "request_feedback_acceptance", {"decision_revision_id": self.object(dec)["effective_revision_id"]}, oid=fb, deps=(dec,))
            command = self.verifier.command("record_acceptance", self.acceptance_params(dec), target=self.target(fb), expected_versions=self.deps(dec, self.evidence))
            revocation = self.ceo.command("revoke_assignment", {"assignment_id": self.f["actors"]["verifier"]["assignment_id"]})
            token = "write-before-revoke-" + uuid.uuid4().hex[:8]
            with ThreadPoolExecutor(max_workers=2) as pool:
                accepted = pool.submit(self.verifier.json, "POST", "/v1/actions", command,
                                       headers=self.h.headers("auth_fence_acquired", "barrier", token))
                self.h.reached(token)
                revoked = pool.submit(self.ceo.json, "POST", "/v1/actions", revocation)
                time.sleep(0.2)
                assert not revoked.done(), "revocation bypassed the held authorization fence"
                self.h.release(token)
                acceptance = accepted.result(timeout=20)
                revoke_receipt = revoked.result(timeout=20)
            assert acceptance["auth_epoch"] < revoke_receipt["auth_epoch"]
            for path in (f"/v1/objects/{self.fb}", f"/v1/context-packs/{old_snapshot_id}",
                         f"/v1/evidence-assets/{self.evidence}/revisions/{self.evidence_revision}",
                         f"/v1/action-receipts/{self.acceptance_receipt_id}"):
                denied = self.verifier.json("GET", path, expected=403)
                assert_error(denied, "FORBIDDEN")
            assert_error(self.verifier.json("POST", "/v1/actions", self.acceptance_command, expected=403), "FORBIDDEN")
            # The reverse schedule: revocation owns the fence before a proposal starts.
            agent_command = self.agent.command("create_object", {"object_type": "FeedbackThread", "domain_id": self.domain,
                                                                  "payload": {"title": "This revoked proposal must not exist", "description": "Synthetic auth race"}})
            revoke_agent = self.ceo.command("revoke_assignment", {"assignment_id": self.f["actors"]["agent"]["assignment_id"]})
            token = "revoke-before-write-" + uuid.uuid4().hex[:8]
            with ThreadPoolExecutor(max_workers=2) as pool:
                revoked = pool.submit(self.ceo.json, "POST", "/v1/actions", revoke_agent,
                                      headers=self.h.headers("before_business_commit", "barrier", token))
                self.h.reached(token)
                proposed = pool.submit(self.agent.json, "POST", "/v1/actions", agent_command, expected=403)
                time.sleep(0.2)
                assert not proposed.done()
                self.h.release(token)
                revoke_first = revoked.result(timeout=20)
                assert_error(proposed.result(timeout=20), "FORBIDDEN")
            # Pending execution cannot bypass a revoked required party.
            oid, signatures = self.signed("BusinessCommitment", self.outcome)
            pending = self.activate(oid, signatures, self.outcome)
            assert all(task["state"] == "queued" for task in self.tasks(pending["effect_task_ids"]))
            self.act(self.ceo, "revoke_assignment", {"assignment_id": self.f["actors"]["domain_dri"]["assignment_id"]})
            ledger_before = self.ledger_state()["unique_effects"]
            self.start_worker()
            tasks = self.wait_tasks(pending["effect_task_ids"], state="failed")
            assert self.ledger_state()["unique_effects"] == ledger_before, "revoked pending action was dispatched"
            self.stop_worker()
            result.update(old_snapshot_id=old_snapshot_id, write_first_receipt=acceptance["receipt_id"],
                          revoke_after_receipt=revoke_receipt["receipt_id"], revoke_first_receipt=revoke_first["receipt_id"],
                          blocked_external_tasks=tasks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-file", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--through", type=int, choices=range(1, 9), default=8,
                        help="development stop after N business groups; partial runs never pass the full gate")
    args = parser.parse_args()
    if args.fixture_file:
        run_id, fixture_file = args.run_id, args.fixture_file
    else:
        from acceptance.runtime.seed import create_fixture
        run_id, fixture_file = create_fixture(args.run_id)
    harness = Harness(run_id=run_id)
    fixture = json.loads(fixture_file.read_text())
    scenario = None
    try:
        scenario = Scenario(harness, fixture)
        with harness.group("00_application_role_and_contract_preflight") as result:
            from acceptance.runtime.sql_oracle import assert_application_role
            with psycopg.connect(harness.env["APP_DATABASE_URL"]) as conn:
                result["database_authority"] = assert_application_role(conn)
            openapi = httpx.get(scenario.api_url + "/openapi.json", trust_env=False).json()
            for path in ("/v1/actions", "/v1/actions/prepare", "/v1/context-packs", "/v1/evidence-assets"):
                assert path in openapi["paths"]
            spec = json.dumps(openapi, sort_keys=True, indent=2).encode()
            (harness.output / "openapi.json").write_bytes(spec)
            result["openapi_sha256"] = sha256(spec)
            outsider = scenario.clients["outsider"]
            assert_error(outsider.object(scenario.outcome, expected=404), "NOT_FOUND")
            from memory_service_runtime.governed.bootstrap import seed_scope
            foreign_tag = "runtime-acceptance-foreign-" + uuid.uuid4().hex[:10]
            with psycopg.connect(harness.env["MIGRATION_DATABASE_URL"]) as conn:
                foreign = seed_scope(conn, foreign_tag, foreign_tag + "-company")
            foreign_client = Client(scenario.api_url, foreign["actors"]["ceo"]["token"], "foreign-company-ceo", harness.log)
            try:
                assert_error(foreign_client.object(scenario.outcome, expected=404), "NOT_FOUND")
            finally:
                foreign_client.close()
            invalid = scenario.ceo.command("create_object", {"object_type": "FeedbackThread", "domain_id": scenario.domain,
                                                               "payload": {"title": "Forged status", "description": "Must remain absent", "lifecycle_status": "closed"}})
            assert_error(scenario.ceo.json("POST", "/v1/actions", invalid, expected=422), "INVALID_REQUEST")
            forged_actor = scenario.ceo.command("create_object", {"object_type": "FeedbackThread", "domain_id": scenario.domain,
                                                                    "payload": {"title": "Forged identity", "description": "Must remain absent"}})
            forged_actor["actor_id"] = foreign["actors"]["ceo"]["principal_id"]
            assert_error(scenario.ceo.json("POST", "/v1/actions", forged_actor, expected=422), "INVALID_REQUEST")
            invalid_version = scenario.ceo.command("propose_revision", {"payload": {"title": "Invalid boolean version", "terms": {}}},
                                                   target={**scenario.target(scenario.outcome), "expected_version": True})
            assert_error(scenario.ceo.json("POST", "/v1/actions", invalid_version, expected=422), "INVALID_REQUEST")
            hidden = scenario.ceo.command("create_object", {"object_type": "BusinessCommitment", "domain_id": scenario.domain,
                                                             "payload": scenario.commitment_payload("BusinessCommitment", scenario.upstream(scenario.outcome))})
            denied = outsider.json("POST", "/v1/actions/prepare", hidden, expected=403)
            assert_error(denied, "FORBIDDEN")
            assert scenario.outcome not in json.dumps(denied)
        groups = [scenario.group_handshake, scenario.group_bundle, scenario.group_concurrency,
                  scenario.group_idempotency, scenario.group_closure, scenario.group_temporal,
                  scenario.group_worker, scenario.group_revocation]
        for index, group in enumerate(groups[:args.through]):
            group()
            if index == 0 and args.through > 1:
                from acceptance.runtime.negative_checks import run_extra_bundle_checks
                run_extra_bundle_checks(scenario)
        if args.through == 8:
            from acceptance.runtime.receipt_domain_check import run_receipt_domain_check
            with harness.group("extra_old_receipt_after_single_domain_revocation") as result:
                result.update(run_receipt_domain_check(harness, scenario.api_url))
            from acceptance.runtime.recovery import run_recovery
            run_recovery(harness, fixture)
            from acceptance.runtime.final_checks import run_regression, save_source_and_receiver
            with harness.group("10_original_regression_migration_replay_and_build") as result:
                result.update(run_regression(harness))
            with harness.group("11_source_manifest_and_durable_receiver_evidence") as result:
                result.update(save_source_and_receiver(harness))
            harness.complete = True
    finally:
        if scenario:
            for client in scenario.clients.values():
                client.close()
        harness.stop_all()
        report = harness.save_report()
        from acceptance.runtime.final_checks import checksum_artifacts
        checksum_artifacts(harness)
        print(f"Evidence: {report}", flush=True)


if __name__ == "__main__":
    main()
