"""Exercise real Clark cookie sessions -> BFF -> Runtime; never print credentials."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
import psycopg
from psycopg.rows import dict_row

from acceptance.runtime.client import Client, EvidenceLog, sha256, utc_now
from acceptance.runtime.harness import private_json


class BffClient(Client):
    def __init__(self, url, name, code, log):
        self.base_url, self.name, self.log = url, name, log
        self.http = httpx.Client(base_url=url, headers={"Origin": url}, timeout=35, trust_env=False)
        response = self.http.post("/api/runtime/session", json={"accessCode": code})
        assert response.status_code == 200, f"Identity login failed for {name}: {response.status_code}"
        body = response.json()
        assert not any(word in json.dumps(body) for word in ("runtimeToken", "accessCodeHash", "sessionSecret"))

    def request(self, method, path, body=None, **kwargs):
        if path.startswith("/v1/action-receipts/"):
            path = path.replace("/v1/action-receipts/", "/api/runtime/receipts/", 1)
        elif path.startswith("/v1/"):
            path = path.replace("/v1/", "/api/runtime/", 1)
        return super().request(method, path, body, **kwargs)


class Checks:
    def __init__(self, state_file):
        self.state_file = state_file
        self.state = json.loads(state_file.read_text())
        self.url = self.state["clark_url"]
        assert urlsplit(self.url).hostname == "127.0.0.1", "Local acceptance only"
        self.fixture = json.loads(Path(self.state["fixture_file"]).read_text())
        self.codes = json.loads(Path(self.state["codes_file"]).read_text())
        self.env = json.loads((ROOT / ".runtime-acceptance/env.json").read_text())
        self.output = Path(self.state["report_directory"])
        self.log = EvidenceLog(self.output / "clark-http")
        self.business = self.state["business"]["scenarios"]["http"]
        self.work = self.business["work_item"]["objectId"]
        self.outcome = self.business["outcome_id"]
        self.feedback = self.business["feedback_id"]
        self.clients = {}
        self.groups = []

    def sql(self, query, params=()):
        with psycopg.connect(self.env["APP_DATABASE_URL"], row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (self.fixture["scope_id"],))
            return conn.execute(query, params).fetchall()

    def save(self):
        report = {"run_id": self.state["run_id"], "finished_at": utc_now(), "groups": self.groups,
                  "status": "passed" if self.groups and all(row["status"] == "passed" for row in self.groups) else "incomplete_or_failed",
                  "transport": "Clark browser-cookie session -> Clark BFF -> real Runtime HTTP -> PostgreSQL/MinIO",
                  "http_joint_accepted": len(self.groups) == 4 and all(row["status"] == "passed" for row in self.groups),
                  "browser_accepted": False, "released": False, "production_deployed": False}
        (self.output / "clark-http-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    def group(self, name, callback):
        row = {"name": name, "started_at": utc_now(), "status": "running"}
        self.groups.append(row)
        print("RUN " + name, flush=True)
        try:
            row.update(callback() or {})
            row["status"] = "passed"
            print("PASS " + name, flush=True)
        except BaseException as exc:
            row.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:1000])
            raise
        finally:
            row["finished_at"] = utc_now()
            self.save()

    def target(self, oid):
        obj = self.clients["ceo"].object(oid)
        return {"object_id": oid, "revision_id": obj["latest_revision_id"], "expected_version": obj["object_version"]}

    def prepare(self, actor, action, params, oid=None):
        client = self.clients[actor]
        command = client.command(action, deepcopy(params), target=self.target(oid) if oid else None)
        command["expected_versions"] = client.json("POST", "/v1/actions/prepare", command)["expected_versions"]
        return command

    def commit(self, actor, command):
        receipt = self.clients[actor].json("POST", "/v1/actions", command)
        assert receipt["effect_task_ids"] == [], "Delivery/assessment/closure must not implicitly dispatch"
        return receipt

    def act(self, actor, action, params, oid=None):
        return self.commit(actor, self.prepare(actor, action, params, oid))

    def reject(self, actor, command, expected, code=None, *, headers=None):
        before = self.sql("SELECT receipt_id::text FROM gov_action_receipts WHERE scope_id=%s AND idempotency_key=%s ORDER BY receipt_id",
                          (self.fixture["scope_id"], command["idempotency_key"]))
        body = self.clients[actor].json("POST", "/v1/actions", command, expected=expected, headers=headers)
        if code:
            allowed = {code} if isinstance(code, str) else set(code)
            assert body["error"]["code"] in allowed, body
        after = self.sql("SELECT receipt_id::text FROM gov_action_receipts WHERE scope_id=%s AND idempotency_key=%s ORDER BY receipt_id",
                         (self.fixture["scope_id"], command["idempotency_key"]))
        assert before == after, "Rejected command inserted a receipt"
        return body

    def create(self, kind, payload, actor="ceo"):
        return self.act(actor, "create_object", {"object_type": kind, "domain_id": self.fixture["domain_id"], "payload": payload})["result"]["object_id"]

    def upload(self, content):
        result = self.clients["mission_dri"].json("POST", "/v1/evidence-assets", {
            "domain_id": self.fixture["domain_id"], "title": "Clark BFF synthetic evidence",
            "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain"})
        raw = self.clients["verifier"].request("GET", f"/v1/evidence-assets/{result['object_id']}/revisions/{result['revision_id']}").content
        assert raw == content and result["sha256"] == sha256(content)
        return result

    def authority(self):
        with httpx.Client(base_url=self.url, headers={"Origin": self.url}, trust_env=False) as public:
            assert public.get("/api/runtime/session").status_code == 401
            path = "/api/runtime/objects/" + self.work
            assert public.get(path).status_code == 401
            assert public.get(path, headers={"Authorization": "Bearer fabricated", "X-Runtime-Role": "CEO"}).status_code == 401
            shared = json.loads(Path(self.state["shared_access_file"]).read_text())["password"]
            assert public.post("/api/access", json={"password": shared, "seat": "ceo"}).status_code == 200
            assert public.get(path).status_code == 401
            assert public.post("/api/runtime/session", json={"accessCode": self.codes["ceo"]}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
            assert public.post("/api/runtime/session", json={"accessCode": self.codes["mission_dri"], "role": "CEO"}).status_code == 400
        self.clients = {name: BffClient(self.url, name, code, self.log) for name, code in self.codes.items()}
        for name, client in self.clients.items():
            session = client.json("GET", "/api/runtime/session")
            assert session["actor"]["id"] == name
        self.clients["outsider"].object(self.work, expected=(403, 404))
        command = self.prepare("mission_dri", "accept_work_item", {}, self.work)
        for name in ("ceo", "domain_dri", "verifier", "agent"):
            self.reject(name, command, 403, "FORBIDDEN")
        self.reject("ceo", command, 403, "FORBIDDEN", headers={"X-Runtime-Role": "MISSION_DRI", "X-Actor-Id": "mission_dri"})
        forged = deepcopy(command)
        forged["actor"] = {"id": "mission_dri", "role": "MISSION_DRI"}
        self.reject("ceo", forged, (400, 422), ("INVALID_REQUEST",))
        assert self.clients["ceo"].object(self.work)["lifecycle_status"] == "offered"
        return {"anonymous_denied": True, "shared_password_not_authority": True, "csrf_denied": True,
                "session_role_spoof_denied": True, "header_role_spoof_denied": True,
                "named_dri_enforced": True, "outside_domain_denied": True, "tokens_not_in_session": True}

    @staticmethod
    def review(submission, accepted):
        return {"deliverable_revision_id": submission["deliverable_revision_id"],
                "delivery_payload_hash": submission["payload_hash"],
                "verification_result": "accepted" if accepted else "changes_requested",
                "criterion_results": [{"criterion_id": "complete", "result": "passed" if accepted else "failed", "note": "Raw source checked"},
                                      {"criterion_id": "consistent", "result": "passed", "note": "Values reconcile"}],
                "review_note": "All criteria checked" if accepted else "Add the missing raw source"}

    def delivery(self):
        evidence1 = self.upload(b"Clark BFF v1: summary values present; raw source missing.\n")
        params1 = {"title": "Clark BFF delivery v1", "summary": "First submitted package",
                   "evidence_revision_ids": [evidence1["revision_id"]]}
        early = self.prepare("mission_dri", "submit_deliverable", params1, self.work)
        self.reject("mission_dri", early, 409, "INVALID_STATE")
        self.act("mission_dri", "accept_work_item", {}, self.work)
        command1 = self.prepare("mission_dri", "submit_deliverable", params1, self.work)
        stale = deepcopy(command1)
        stale["idempotency_key"] = str(uuid.uuid4())
        first = self.commit("mission_dri", command1)
        assert self.commit("mission_dri", command1) == first
        self.reject("mission_dri", stale, 409, ("VERSION_CONFLICT", "DEPENDENCY_MISSING"))
        conflict = deepcopy(command1)
        conflict["params"]["summary"] = "Different body with same key"
        self.reject("mission_dri", conflict, 409, "IDEMPOTENCY_CONFLICT")
        read_receipt = self.clients["mission_dri"].json("GET", "/v1/action-receipts/" + first["receipt_id"])
        assert read_receipt["receipt"] == first
        submit1 = first["result"]
        assert submit1["submission_seq"] == 1
        review1 = self.prepare("verifier", "review_deliverable", self.review(submit1, False), self.work)
        for actor in ("ceo", "domain_dri", "mission_dri", "agent"):
            self.reject(actor, review1, 403, "FORBIDDEN")
        invalid_hash = deepcopy(review1)
        invalid_hash["params"]["delivery_payload_hash"] = "0" * 64
        self.reject("verifier", invalid_hash, 409, ("STALE_DEPENDENCY", "INVALID_STATE"))
        returned = self.commit("verifier", review1)
        assert self.clients["ceo"].object(self.work)["lifecycle_status"] == "changes_requested"
        self.evidence = self.upload(b"Clark BFF v2: complete raw source; measured qualified deliveries 72 against target 80.\n")
        params2 = {"title": "Clark BFF delivery v2", "summary": "Raw source added after return",
                   "evidence_revision_ids": [self.evidence["revision_id"]],
                   "responds_to_acceptance_id": returned["result"]["acceptance_id"]}
        command2 = self.prepare("mission_dri", "submit_deliverable", params2, self.work)
        missing = deepcopy(command2)
        del missing["params"]["responds_to_acceptance_id"]
        self.reject("mission_dri", missing, 409, "STALE_DEPENDENCY")
        second = self.commit("mission_dri", command2)
        submit2 = second["result"]
        assert submit2["submission_seq"] == 2 and submit2["deliverable_object_id"] == submit1["deliverable_object_id"]
        review2 = self.prepare("verifier", "review_deliverable", self.review(submit2, True), self.work)
        stale_review = deepcopy(review2)
        stale_review["params"].update(deliverable_revision_id=submit1["deliverable_revision_id"], delivery_payload_hash=submit1["payload_hash"])
        self.reject("verifier", stale_review, 409, ("STALE_DEPENDENCY", "INVALID_STATE"))
        accepted = self.commit("verifier", review2)
        assert self.commit("verifier", review2) == accepted
        self.acceptance = accepted["result"]["acceptance_id"]
        self.deliverable = submit2["deliverable_object_id"]
        self.submissions = [submit1, submit2]
        self.delivery_receipts = [first, returned, second, accepted]
        assert self.clients["ceo"].object(self.work)["lifecycle_status"] == "delivery_accepted"
        return {"work_item_id": self.work, "deliverable_id": self.deliverable,
                "submission_revision_ids": [item["deliverable_revision_id"] for item in self.submissions],
                "receipt_ids": [item["receipt_id"] for item in self.delivery_receipts],
                "flow": ["offered", "in_progress", "submitted", "changes_requested", "submitted", "delivery_accepted"],
                "idempotent_original_receipt": True, "stale_submission_denied": True, "stale_review_denied": True}

    def independent_judgments(self):
        ceo = self.clients["ceo"]
        initial = {"delivery": ceo.object(self.work)["lifecycle_status"],
                   "outcome": ceo.object(self.outcome)["outcome_achievement"], "mf": ceo.object(self.feedback)["lifecycle_status"]}
        assert initial == {"delivery": "delivery_accepted", "outcome": "not_assessed", "mf": "investigating"}
        outcome_ref = {"object_id": self.outcome, "revision_id": ceo.object(self.outcome)["effective_revision_id"]}
        observation = self.create("MetricObservation", {"title": "Clark BFF metric observation", "metric_id": "qualified-deliveries",
            "value": 72, "unit": "deliveries", "valid_from": "2000-01-01T00:00:00Z", "upstream_refs": [outcome_ref,
                {"object_id": self.evidence["object_id"], "revision_id": self.evidence["revision_id"]}]})
        params = {"assessment_result": "not_achieved", "observation_revision_ids": [ceo.object(observation)["latest_revision_id"]],
                  "evidence_revision_ids": [self.evidence["revision_id"]], "delivery_acceptance_ids": [self.acceptance],
                  "assessment_note": "Delivery passed, but 72 remains below the target 80"}
        judgment = self.prepare("ceo", "record_outcome_assessment", params, self.outcome)
        self.reject("verifier", judgment, 403, "FORBIDDEN")
        assessment = self.commit("ceo", judgment)
        assert ceo.object(self.outcome)["outcome_achievement"] == "not_achieved"
        assert ceo.object(self.feedback)["lifecycle_status"] == "investigating"
        decision = self.create("Decision", {"title": "Clark BFF independent MF disposition", "statement": "Feedback resolved; no management baseline adjustment required"}, "domain_dri")
        self.act("domain_dri", "confirm_decision", {}, decision)
        revision = ceo.object(decision)["effective_revision_id"]
        self.act("domain_dri", "request_feedback_acceptance", {"decision_revision_id": revision}, self.feedback)
        mf_acceptance = self.act("verifier", "record_acceptance", {"decision_revision_id": revision,
            "evidence_revision_ids": [self.evidence["revision_id"]], "verification_result": "accepted"}, self.feedback)
        closure = {"acceptance_record_id": mf_acceptance["result"]["acceptance_id"],
                   "resolution_decision_revision_id": revision, "closure_evidence_revision_ids": [self.evidence["revision_id"]],
                   "disposition": "no_change", "closure_note": "MF separately verified; outcome shortfall remains explicitly recorded"}
        command = self.prepare("ceo", "confirm_closure", closure, self.feedback)
        wrong = deepcopy(command)
        wrong["params"]["acceptance_record_id"] = self.acceptance
        self.reject("ceo", wrong, 409, "INVALID_STATE")
        closed = self.commit("ceo", command)
        final = {"delivery": ceo.object(self.work)["lifecycle_status"],
                 "outcome": ceo.object(self.outcome)["outcome_achievement"], "mf": ceo.object(self.feedback)["lifecycle_status"]}
        assert final == {"delivery": "delivery_accepted", "outcome": "not_achieved", "mf": "closed"}
        return {"initial_states": initial, "final_states": final, "assessment_receipt_id": assessment["receipt_id"],
                "mf_acceptance_id": mf_acceptance["result"]["acceptance_id"], "mf_closure_receipt_id": closed["receipt_id"],
                "delivery_review_cannot_substitute_mf_acceptance": True}

    def physical_oracle(self):
        revisions = self.sql("SELECT revision_id::text, payload_hash FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s ORDER BY object_version",
                             (self.fixture["scope_id"], self.deliverable))
        reviews = self.sql("SELECT acceptance_id::text, submission_seq, deliverable_revision_id::text, payload_hash, verification_result, verifier_principal_id::text, action_id::text FROM gov_delivery_acceptances WHERE scope_id=%s AND work_item_object_id=%s ORDER BY submission_seq",
                           (self.fixture["scope_id"], self.work))
        assert len(revisions) == len(reviews) == 2
        assert [row["verification_result"] for row in reviews] == ["changes_requested", "accepted"]
        assert [row["submission_seq"] for row in reviews] == [1, 2]
        assert [row["revision_id"] for row in revisions] == [item["deliverable_revision_id"] for item in self.submissions]
        assert [row["payload_hash"] for row in revisions] == [row["payload_hash"] for row in reviews]
        assert all(row["verifier_principal_id"] == self.fixture["actors"]["verifier"]["principal_id"] for row in reviews)
        history = self.sql("SELECT assessment_result FROM gov_outcome_assessments WHERE scope_id=%s AND outcome_object_id=%s ORDER BY recorded_at",
                           (self.fixture["scope_id"], self.outcome))
        assert history == [{"assessment_result": "not_achieved"}]
        receipts = self.sql("SELECT receipt_id::text, principal_id::text, action_type FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=ANY(%s::uuid[])",
                            (self.fixture["scope_id"], [item["receipt_id"] for item in self.delivery_receipts]))
        assert len(receipts) == 4
        for row in receipts:
            actor = "mission_dri" if row["action_type"] == "submit_deliverable" else "verifier"
            assert row["principal_id"] == self.fixture["actors"][actor]["principal_id"]
        return {"database_role": "application", "transaction_read_only": True, "immutable_revisions": revisions,
                "reviews": reviews, "actual_principal_receipts": receipts, "outcome_history": history}

    def offline_check(self):
        client = BffClient(self.url, "ceo", self.codes["ceo"], self.log)
        pid = self.state["processes"]["api"]
        assert self.state["state"] == "ready", "Only a running owned acceptance API may be paused"
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=True).stdout
        assert "acceptance/runtime/server.py" in command and self.state["private_directory"] in command, "Recorded PID no longer belongs to this run"
        result = {"run_id": self.state["run_id"], "checked_at": utc_now()}
        try:
            os.kill(pid, signal.SIGSTOP)
            response = client.request("GET", "/v1/objects/" + self.work, expected=503)
            assert response.json()["error"]["code"] == "RUNTIME_UNAVAILABLE"
            result.update(status="passed", http_status=503, code="RUNTIME_UNAVAILABLE", mock_fallback=False)
        finally:
            os.kill(pid, signal.SIGCONT)
        assert client.object(self.work)["lifecycle_status"] == "delivery_accepted"
        result["recovery_read_passed"] = True
        (self.output / "clark-unavailable-report.json").write_text(json.dumps(result, indent=2))
        client.close()
        print(json.dumps(result))

    def checkpoint_browser(self, verify=False):
        client = BffClient(self.url, "ceo", self.codes["ceo"], self.log)
        browser = self.state["business"]["scenarios"]["browser"]
        try:
            objects = {name: client.object(oid) for name, oid in {
                "work_item": browser["work_item"]["objectId"], "outcome": browser["outcome_id"],
                "feedback": browser["feedback_id"],
            }.items()}
        finally:
            client.close()
        hashes = {name: {"object_id": obj["object_id"], "lifecycle_status": obj["lifecycle_status"],
                         "outcome_achievement": obj.get("outcome_achievement"),
                         "sha256": sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())}
                  for name, obj in objects.items()}
        baseline = self.output / "clark-browser-before-regression-hashes.json"
        if verify:
            original = json.loads(baseline.read_text())
            original_objects = json.loads((Path(self.state["private_directory"]) / "browser-before-regression-objects.json").read_text())
            raw_equal = hashes == original["objects"]
            normalized = deepcopy(objects)
            projection_change = None
            # A known, additive read projection was introduced during this run.
            # Report it explicitly; never call the changed raw JSON identical.
            before_feedback = original_objects["feedback"].get("feedback", {})
            after_feedback = normalized["feedback"].get("feedback", {})
            if ("resolution_decision_revision" not in before_feedback
                    and "resolution_decision_revision" in after_feedback
                    and after_feedback["resolution_decision_revision"] is None):
                del after_feedback["resolution_decision_revision"]
                projection_change = {"object": "feedback", "path": "/feedback/resolution_decision_revision",
                                     "change": "added_read_projection", "value": None}
            same_existing_fields = normalized == original_objects
            result = {"run_id": self.state["run_id"], "checked_at": utc_now(),
                      "status": "passed" if raw_equal or same_existing_fields else "failed",
                      "raw_read_objects_equal": raw_equal, "unchanged_existing_fields": same_existing_fields,
                      "projection_change": projection_change, "objects": hashes,
                      "business_recreated": False, "original_hashes": original["objects"]}
            (self.output / "clark-browser-resume-report.json").write_text(json.dumps(result, indent=2))
            assert result["status"] == "passed", "Browser business state changed across the controlled service resume"
        else:
            assert not baseline.exists(), "Refuse to overwrite an existing checkpoint"
            result = {"run_id": self.state["run_id"], "recorded_at": utc_now(), "objects": hashes}
            baseline.write_text(json.dumps(result, indent=2))
            private_json(Path(self.state["private_directory"]) / "browser-before-regression-objects.json", objects)
        print(json.dumps(result))

    def run(self):
        try:
            self.group("clark_01_cookie_identity_and_authority", self.authority)
            self.group("clark_02_versioned_real_dri_delivery", self.delivery)
            self.group("clark_03_independent_outcome_and_mf", self.independent_judgments)
            self.group("clark_04_read_only_physical_oracle", self.physical_oracle)
        finally:
            for client in self.clients.values():
                client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_file", type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline-only", action="store_true", help="Temporarily pause this run's API; coordinate after browser completion")
    modes.add_argument("--checkpoint-browser", choices=("capture", "verify"), help="Read-only exact object hashes around a controlled service resume")
    args = parser.parse_args()
    checks = Checks(args.state_file)
    if args.offline_only:
        checks.offline_check()
    elif args.checkpoint_browser:
        checks.checkpoint_browser(verify=args.checkpoint_browser == "verify")
    else:
        checks.run()
