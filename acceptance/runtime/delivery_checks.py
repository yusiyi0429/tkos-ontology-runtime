"""Independent v0.2 DRI delivery gates using real HTTP and read-only SQL oracles.

Call run_delivery_checks(scenario) before the v0.1 identity-revocation group.
The runner creates its own Outcome/BC/EC/Feedback through HTTP and leaves the
driver's saved business IDs unchanged. It never seeds successful business rows
or imports production action implementations. Infrastructure is owned by run.py.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
from typing import Any
import uuid

from acceptance.runtime.client import Client, assert_error, sha256, utc_now


def run_delivery_checks(scenario: Any) -> dict[str, Any]:
    s = scenario
    if s.worker is not None and s.worker.poll() is None:
        raise AssertionError("Run delivery checks while the scenario Worker is stopped")
    suffix = uuid.uuid4().hex[:8]
    reports = {}

    def prepared(client, kind, params, oid=None):
        command = client.command(kind, deepcopy(params), target=s.target(oid) if oid else None)
        response = client.json("POST", "/v1/actions/prepare", command)
        command["expected_versions"] = response["expected_versions"]
        return command

    def commit(client, command):
        receipt = client.json("POST", "/v1/actions", command)
        s.receipts.append(receipt)
        assert not receipt["effect_task_ids"], "Delivery/assessment must not dispatch implicitly"
        return receipt

    def rejected(client, command, codes, *, status=409):
        before = s.snapshot()
        denied = client.json("POST", "/v1/actions", command, expected=status)
        allowed = {codes} if isinstance(codes, str) else set(codes)
        assert denied["error"]["code"] in allowed, denied
        assert s.snapshot() == before, "Rejected delivery command produced durable business changes"
        return denied["error"]["code"]

    def upload(label, content):
        result = s.mission.json("POST", "/v1/evidence-assets", {
            "domain_id": s.domain, "title": label,
            "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain",
        })
        raw = s.verifier.request(
            "GET", f"/v1/evidence-assets/{result['object_id']}/revisions/{result['revision_id']}",
        ).content
        assert raw == content and result["sha256"] == sha256(content)
        return result

    def review_params(submission, result="accepted"):
        return {
            "deliverable_revision_id": submission["deliverable_revision_id"],
            "delivery_payload_hash": submission["payload_hash"],
            "verification_result": result,
            "criterion_results": [
                {"criterion_id": "complete", "result": "failed" if result == "changes_requested" else "passed",
                 "note": "Add the missing raw source" if result == "changes_requested" else "Source package complete"},
                {"criterion_id": "consistent", "result": "passed", "note": "Available values reconcile with source"},
            ],
            "review_note": "Please supplement the raw source" if result == "changes_requested" else "All frozen criteria independently checked",
        }

    with s.h.group("v02_01_frozen_work_item_and_exact_dri_authority") as report:
        outcome = s.create("CompanyOutcome", {
            "title": f"Independent delivery Outcome {suffix}",
            "terms": {"metric_id": "qualified-deliveries", "target": 80, "unit": "deliveries"},
            "outcome_statement": "Reach 80 qualified deliveries while retaining source evidence",
            "upstream_refs": [],
        })
        s.act(s.ceo, "confirm_outcome", {}, oid=outcome)
        outcome_revision = s.object(outcome)["effective_revision_id"]
        assert s.object(outcome)["outcome_assessment"] is None
        assert s.object(outcome)["outcome_achievement"] == "not_assessed"
        bc, signatures = s.signed("BusinessCommitment", outcome)
        bc_activation = s.activate(bc, signatures, outcome)
        ec, signatures = s.signed("ExecutionCommitment", bc)
        ec_activation = s.activate(ec, signatures, bc)
        feedback = s.feedback_investigation(f"Independent delivery feedback {suffix}")
        payload = {
            "title": f"Real DRI delivery {suffix}",
            "execution_commitment_ref": s.upstream(ec),
            "dri_assignment_id": s.f["actors"]["mission_dri"]["assignment_id"],
            "acceptor_assignment_id": s.f["actors"]["verifier"]["assignment_id"],
            "acceptance_criteria": [
                {"criterion_id": "complete", "description": "All raw sources are included"},
                {"criterion_id": "consistent", "description": "Values reconcile with the supplied sources"},
            ],
            "feedback_ref": {"object_id": feedback, "revision_id": s.object(feedback)["latest_revision_id"]},
        }
        creation = prepared(s.ceo, "create_object", {
            "object_type": "WorkItem", "domain_id": s.domain, "payload": payload,
        })
        rejected(s.mission, creation, "FORBIDDEN", status=403)
        rejected(s.agent, creation, "FORBIDDEN", status=403)
        self_acceptor = deepcopy(creation)
        self_acceptor["params"]["payload"]["acceptor_assignment_id"] = payload["dri_assignment_id"]
        rejected(s.ceo, self_acceptor, "FORBIDDEN", status=403)
        wrong_dri = deepcopy(creation)
        wrong_dri["params"]["payload"]["dri_assignment_id"] = s.f["actors"]["ceo"]["assignment_id"]
        rejected(s.ceo, wrong_dri, "FORBIDDEN", status=403)
        work_item = commit(s.ceo, creation)["result"]["object_id"]
        frozen_revision = s.object(work_item)["latest_revision_id"]
        assert s.object(work_item)["lifecycle_status"] == "offered"
        evidence1 = upload("Synthetic DRI v1 evidence", b"Delivery v1: summary and reconciled values; raw source absent.\n")
        submit1 = {"title": "Synthetic delivery v1", "summary": "First package for independent review",
                   "evidence_revision_ids": [evidence1["revision_id"]]}
        premature = prepared(s.mission, "submit_deliverable", submit1, work_item)
        rejected(s.mission, premature, "INVALID_STATE")
        acceptance = prepared(s.mission, "accept_work_item", {}, work_item)
        for actor in (s.ceo, s.dri, s.agent):
            rejected(actor, acceptance, "FORBIDDEN", status=403)
        commit(s.mission, acceptance)
        assert s.object(work_item)["lifecycle_status"] == "in_progress"
        assert s.object(work_item)["latest_revision_id"] == frozen_revision
        attempted_rewrite = s.ceo.command("propose_revision", {"payload": {**payload, "title": "Silently replaced baseline"}},
                                          target=s.target(work_item), expected_versions=s.deps(ec, feedback))
        rejected(s.ceo, attempted_rewrite, ("INVALID_STATE", "FORBIDDEN"), status=(403, 409))
        assert s.ceo.revision(work_item, frozen_revision)["payload"] == payload
        report.update(work_item_id=work_item, baseline_revision_id=frozen_revision,
                      execution_commitment_id=ec, business_commitment_id=bc, outcome_id=outcome,
                      feedback_id=feedback, exact_assignee_enforced=True,
                      frozen_criteria_ids=[item["criterion_id"] for item in payload["acceptance_criteria"]])
        reports["authority"] = report

    with s.h.group("v02_02_versioned_submission_cas_and_idempotency") as report:
        command = prepared(s.mission, "submit_deliverable", submit1, work_item)
        assert command["expected_versions"], "prepare omitted actual submission dependencies"
        missing = deepcopy(command)
        missing["expected_versions"] = []
        rejected(s.mission, missing, "DEPENDENCY_MISSING")
        for actor in (s.ceo, s.dri, s.agent):
            rejected(actor, command, "FORBIDDEN", status=403)
        commands = [deepcopy(command), deepcopy(command)]
        commands[1]["idempotency_key"] = f"delivery-cas-{uuid.uuid4()}"
        clients = [Client(s.api_url, s.f["actors"]["mission_dri"]["token"], f"delivery-cas-{number}", s.h.log)
                   for number in (1, 2)]
        barrier = threading.Barrier(2)

        def race(index):
            barrier.wait(timeout=5)
            response = clients[index].request("POST", "/v1/actions", commands[index], expected=(200, 409))
            return index, response.status_code, response.json()

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                raced = list(pool.map(race, (0, 1)))
        finally:
            for client in clients:
                client.close()
        assert sorted(item[1] for item in raced) == [200, 409]
        winner_index, _, winner = next(item for item in raced if item[1] == 200)
        loser = next(item[2] for item in raced if item[1] == 409)
        assert loser["error"]["code"] in {"VERSION_CONFLICT", "DEPENDENCY_MISSING"}
        s.receipts.append(winner)
        assert not winner["effect_task_ids"]
        submission1 = winner["result"]
        assert submission1["submission_seq"] == 1
        deliverable = submission1["deliverable_object_id"]
        first_revision = s.ceo.revision(deliverable, submission1["deliverable_revision_id"])
        assert first_revision["payload_hash"] == submission1["payload_hash"]
        assert first_revision["payload"]["evidence_revision_ids"] == [evidence1["revision_id"]]
        stable = s.snapshot()
        replay = s.mission.json("POST", "/v1/actions", commands[winner_index])
        assert replay == winner and s.snapshot() == stable
        conflict = deepcopy(commands[winner_index])
        conflict["params"]["summary"] = "Different content under an already committed key"
        rejected(s.mission, conflict, "IDEMPOTENCY_CONFLICT")
        waiting_submit = prepared(s.mission, "submit_deliverable", submit1, work_item)
        rejected(s.mission, waiting_submit, "INVALID_STATE")
        injected_type = s.ceo.command("create_object", {
            "object_type": "Deliverable", "domain_id": s.domain, "payload": first_revision["payload"],
        })
        rejected(s.ceo, injected_type, "INVALID_REQUEST", status=422)
        rewritten = s.ceo.command("propose_revision", {"payload": first_revision["payload"]},
                                  target=s.target(deliverable))
        rejected(s.ceo, rewritten, ("INVALID_STATE", "INVALID_REQUEST", "FORBIDDEN"), status=(403, 409, 422))
        known_v1 = utc_now()
        v1_context = s.context(s.ceo, [work_item, deliverable], known_v1, known_v1)
        assert {item["object_id"] for item in v1_context["selected"]} == {work_item, deliverable}
        initial_delivery = next(item for item in v1_context["selected"] if item["object_id"] == deliverable)
        assert initial_delivery["delivery_review"] is None and initial_delivery["delivery_status"] == "submitted"
        report.update(deliverable_object_id=deliverable, v1_revision_id=submission1["deliverable_revision_id"],
                      submission_seq=1, winning_receipt_id=winner["receipt_id"],
                      concurrent_statuses=[item[1] for item in raced], replay_returned_original=True,
                      old_context_snapshot_id=v1_context["context_snapshot_id"])
        reports["submission"] = report

    with s.h.group("v02_03_return_supplement_and_exact_version_acceptance") as report:
        review1 = prepared(s.verifier, "review_deliverable", review_params(submission1, "changes_requested"), work_item)
        for actor in (s.mission, s.ceo, s.dri, s.agent):
            rejected(actor, review1, "FORBIDDEN", status=403)
        wrong_hash = deepcopy(review1)
        wrong_hash["params"]["delivery_payload_hash"] = "0" * 64
        rejected(s.verifier, wrong_hash, ("INVALID_STATE", "STALE_DEPENDENCY"))
        incomplete = deepcopy(review1)
        incomplete["params"]["criterion_results"] = incomplete["params"]["criterion_results"][:1]
        rejected(s.verifier, incomplete, ("INVALID_REQUEST", "INVALID_STATE"), status=(409, 422))
        contradictory = deepcopy(review1)
        contradictory["params"]["verification_result"] = "accepted"
        rejected(s.verifier, contradictory, ("INVALID_REQUEST", "INVALID_STATE"), status=(409, 422))
        returned = commit(s.verifier, review1)
        return_id = returned["result"]["acceptance_id"]
        assert s.object(work_item)["lifecycle_status"] == s.object(deliverable)["lifecycle_status"] == "changes_requested"
        evidence2 = upload("Synthetic DRI v2 evidence", b"Delivery v2: summary, complete raw source and reconciled values.\n")
        submit2 = {"title": "Synthetic delivery v2", "summary": "Raw source added in response to the independent return",
                   "evidence_revision_ids": [evidence2["revision_id"]], "responds_to_acceptance_id": return_id}
        command2 = prepared(s.mission, "submit_deliverable", submit2, work_item)
        no_response = deepcopy(command2)
        del no_response["params"]["responds_to_acceptance_id"]
        rejected(s.mission, no_response, "STALE_DEPENDENCY")
        wrong_response = deepcopy(command2)
        wrong_response["params"]["responds_to_acceptance_id"] = str(uuid.uuid4())
        rejected(s.mission, wrong_response, ("INVALID_STATE", "STALE_DEPENDENCY", "NOT_FOUND"), status=(404, 409))
        # v2 has an existing Deliverable dependency in both commands. Unlike
        # the first-creation race above, the losing request must reach CAS and
        # report VERSION_CONFLICT rather than newly discovered dependencies.
        commands = [deepcopy(command2), deepcopy(command2)]
        commands[1]["idempotency_key"] = f"delivery-v2-cas-{uuid.uuid4()}"
        clients = [Client(s.api_url, s.f["actors"]["mission_dri"]["token"], f"delivery-v2-cas-{number}", s.h.log)
                   for number in (1, 2)]
        barrier = threading.Barrier(2)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                raced_v2 = list(pool.map(race, (0, 1)))
        finally:
            for client in clients:
                client.close()
        assert sorted(item[1] for item in raced_v2) == [200, 409]
        v2_winner = next(item[2] for item in raced_v2 if item[1] == 200)
        assert_error(next(item[2] for item in raced_v2 if item[1] == 409), "VERSION_CONFLICT")
        s.receipts.append(v2_winner)
        assert not v2_winner["effect_task_ids"]
        submission2 = v2_winner["result"]
        assert submission2["deliverable_object_id"] == deliverable
        assert submission2["submission_seq"] == 2
        assert submission2["deliverable_revision_id"] != submission1["deliverable_revision_id"]
        assert submission2["payload_hash"] != submission1["payload_hash"]
        assert s.ceo.revision(deliverable, submission1["deliverable_revision_id"]) == first_revision
        review2 = prepared(s.verifier, "review_deliverable", review_params(submission2), work_item)
        stale = deepcopy(review2)
        stale["params"]["deliverable_revision_id"] = submission1["deliverable_revision_id"]
        stale["params"]["delivery_payload_hash"] = submission1["payload_hash"]
        rejected(s.verifier, stale, ("INVALID_STATE", "STALE_DEPENDENCY"))
        no_failure = deepcopy(review2)
        no_failure["params"]["verification_result"] = "changes_requested"
        rejected(s.verifier, no_failure, ("INVALID_REQUEST", "INVALID_STATE"), status=(409, 422))
        approved = commit(s.verifier, review2)
        approved_id = approved["result"]["acceptance_id"]
        stable = s.snapshot()
        assert s.verifier.json("POST", "/v1/actions", review2) == approved
        assert s.snapshot() == stable
        assert s.object(work_item)["lifecycle_status"] == "delivery_accepted"
        assert s.object(deliverable)["lifecycle_status"] == "accepted"
        assert s.object(work_item)["latest_revision_id"] == frozen_revision
        rows = s.sql(
            """SELECT revision_id::text, object_id::text, payload_hash, payload
               FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s ORDER BY object_version""",
            (s.f["scope_id"], deliverable),
        )
        reviews = s.sql(
            """SELECT acceptance_id::text, work_item_revision_id::text, deliverable_object_id::text,
                      deliverable_revision_id::text, submission_seq, payload_hash, verification_result,
                      verifier_assignment_id::text, verifier_principal_id::text, action_id::text
               FROM gov_delivery_acceptances WHERE scope_id=%s AND work_item_object_id=%s ORDER BY submission_seq""",
            (s.f["scope_id"], work_item),
        )
        assert len(rows) == len(reviews) == 2
        assert [row["revision_id"] for row in rows] == [submission1["deliverable_revision_id"], submission2["deliverable_revision_id"]]
        assert [row["submission_seq"] for row in reviews] == [1, 2]
        assert [row["verification_result"] for row in reviews] == ["changes_requested", "accepted"]
        assert all(row["deliverable_object_id"] == deliverable and row["work_item_revision_id"] == frozen_revision for row in reviews)
        assert all(row["verifier_principal_id"] == s.f["actors"]["verifier"]["principal_id"] for row in reviews)
        assert [row["action_id"] for row in reviews] == [returned["receipt_id"], approved["receipt_id"]]
        assert [row["payload_hash"] for row in rows] == [row["payload_hash"] for row in reviews]
        readback = s.object(work_item)["delivery"]
        assert len(readback["submissions"]) == len(readback["acceptances"]) == 2
        assert readback["state"]["submission_seq"] == 2
        assert readback["state"]["latest_acceptance_id"] == approved_id
        historical = s.context(s.ceo, [work_item, deliverable], known_v1, known_v1)
        old_delivery = next(item for item in historical["selected"] if item["object_id"] == deliverable)
        assert old_delivery["revision_id"] == submission1["deliverable_revision_id"]
        assert old_delivery["delivery_review"] is None and old_delivery["delivery_status"] == "submitted"
        source_ids = {item["object_id"] for item in old_delivery["source_refs"]}
        assert {work_item, evidence1["object_id"]}.issubset(source_ids), "Typed delivery provenance is missing"
        saved = s.ceo.json("GET", f"/v1/context-packs/{v1_context['context_snapshot_id']}")
        assert saved["selected"] == v1_context["selected"]
        report.update(work_item_id=work_item, deliverable_id=deliverable, submission_revision_ids=[row["revision_id"] for row in rows],
                      sql_reviews=reviews, final_delivery_state="delivery_accepted", typed_historical_sources_verified=True,
                      v2_concurrent_statuses=[item[1] for item in raced_v2], v2_loser_code="VERSION_CONFLICT")
        reports["review"] = report

    with s.h.group("v02_04_delivery_outcome_and_mf_are_independent") as report:
        original = s.object(outcome)
        assert original["lifecycle_status"] == "confirmed"
        assert original["outcome_assessment"] is None and original["outcome_achievement"] == "not_assessed"
        assert s.object(feedback)["lifecycle_status"] == "investigating"
        assert s.object(work_item)["lifecycle_status"] == "delivery_accepted"
        observation_payload = {
            "title": "Qualified delivery observation below target", "metric_id": "qualified-deliveries",
            "value": 72, "unit": "deliveries", "valid_from": "2026-09-01T00:00:00Z",
            "upstream_refs": [s.upstream(outcome), s.upstream(evidence2["object_id"], evidence2["revision_id"])],
        }
        observation = s.create("MetricObservation", observation_payload)
        observation1 = s.object(observation)["latest_revision_id"]
        assessment_params = {
            "assessment_result": "not_achieved", "observation_revision_ids": [observation1],
            "evidence_revision_ids": [evidence2["revision_id"]], "delivery_acceptance_ids": [approved_id],
            "assessment_note": "The package passed delivery review but the observed metric remains 72 below the target 80",
        }
        judgment1 = prepared(s.ceo, "record_outcome_assessment", assessment_params, outcome)
        for actor in (s.dri, s.verifier, s.mission, s.agent):
            rejected(actor, judgment1, "FORBIDDEN", status=403)
        for label, start, end in (("expired", "2000-01-01T00:00:00Z", "2001-01-01T00:00:00Z"),
                                  ("future", "2099-01-01T00:00:00Z", None)):
            invalid_payload = {**observation_payload, "title": f"Synthetic {label} observation", "valid_from": start}
            if end:
                invalid_payload["valid_to"] = end
            invalid_observation = s.create("MetricObservation", invalid_payload)
            invalid_params = {**assessment_params,
                              "observation_revision_ids": [s.object(invalid_observation)["latest_revision_id"]]}
            rejected(s.ceo, prepared(s.ceo, "record_outcome_assessment", invalid_params, outcome), "INVALID_STATE")
        before_assessment = utc_now()
        failed_outcome = commit(s.ceo, judgment1)
        assessed1 = s.object(outcome)
        assert assessed1["outcome_achievement"] == "not_achieved"
        assert assessed1["object_version"] == original["object_version"] + 1
        assert assessed1["lifecycle_status"] == "confirmed" and assessed1["effective_revision_id"] == outcome_revision
        assert s.object(work_item)["lifecycle_status"] == "delivery_accepted"
        assert s.object(feedback)["lifecycle_status"] == "investigating"
        evidence3 = upload("Synthetic later Outcome evidence", b"Later qualified delivery measurement: 80; threshold now met.\n")
        improved_payload = {**observation_payload, "title": "Later qualified delivery observation at target", "value": 80,
                            "upstream_refs": [s.upstream(outcome), s.upstream(evidence3["object_id"], evidence3["revision_id"])]}
        improved = s.create("MetricObservation", improved_payload)
        assessment2 = {**assessment_params, "assessment_result": "achieved",
                       "observation_revision_ids": [s.object(improved)["latest_revision_id"]],
                       "evidence_revision_ids": [evidence3["revision_id"]],
                       "assessment_note": "A separate later observation now establishes the target of 80"}
        achieved_outcome = commit(s.ceo, prepared(s.ceo, "record_outcome_assessment", assessment2, outcome))
        assessed2 = s.object(outcome)
        assert assessed2["outcome_achievement"] == "achieved"
        assert assessed2["lifecycle_status"] == "confirmed" and assessed2["effective_revision_id"] == outcome_revision
        assert s.object(feedback)["lifecycle_status"] == "investigating", "Outcome achievement silently closed MF"
        after_assessment = utc_now()
        for valid_at, known_at in ((before_assessment, after_assessment), (after_assessment, before_assessment)):
            historic = s.context(s.ceo, [outcome], valid_at, known_at)
            assert len(historic["selected"]) == 1
            assert historic["selected"][0]["outcome_assessment"] is None
            assert historic["selected"][0]["outcome_achievement"] == "not_assessed", "A later judgment leaked into past validity/knowledge"
        current = s.context(s.ceo, [outcome], after_assessment, after_assessment)
        assert current["selected"][0]["outcome_achievement"] == "achieved"
        decisions = s.sql(
            """SELECT assessment_id::text, outcome_revision_id::text, assessment_result,
                      observation_revision_ids, evidence_revision_ids, delivery_acceptance_ids, action_id::text
               FROM gov_outcome_assessments WHERE scope_id=%s AND outcome_object_id=%s ORDER BY recorded_at,assessment_id""",
            (s.f["scope_id"], outcome),
        )
        assert len(decisions) == 2 and [item["assessment_result"] for item in decisions] == ["not_achieved", "achieved"]
        assert all(item["outcome_revision_id"] == outcome_revision for item in decisions)
        assert decisions[0]["observation_revision_ids"] == [observation1]
        assert decisions[0]["action_id"] == failed_outcome["receipt_id"]
        assert decisions[1]["action_id"] == achieved_outcome["receipt_id"]
        decision = s.decision("Separately reviewed MF disposition: no management baseline adjustment required")
        decision_revision = s.object(decision)["effective_revision_id"]
        s.act(s.dri, "request_feedback_acceptance", {"decision_revision_id": decision_revision}, oid=feedback, prepare=True)
        mf_params = {"decision_revision_id": decision_revision, "evidence_revision_ids": [evidence3["revision_id"]],
                     "verification_result": "accepted"}
        mf_acceptance = s.act(s.verifier, "record_acceptance", mf_params, oid=feedback, prepare=True)
        mf_acceptance_id = mf_acceptance["result"]["acceptance_id"]
        closure = {"acceptance_record_id": mf_acceptance_id, "resolution_decision_revision_id": decision_revision,
                   "closure_evidence_revision_ids": [evidence3["revision_id"]], "disposition": "no_change",
                   "closure_note": "Independent MF verification supports closing without changing the commitment baseline"}
        wrong_kind = prepared(s.ceo, "confirm_closure", closure, feedback)
        wrong_kind["params"]["acceptance_record_id"] = approved_id
        rejected(s.ceo, wrong_kind, "INVALID_STATE")
        closed = s.act(s.ceo, "confirm_closure", closure, oid=feedback, prepare=True)
        assert s.object(feedback)["lifecycle_status"] == "closed"
        assert s.object(outcome)["outcome_achievement"] == "achieved"
        assert s.object(work_item)["lifecycle_status"] == "delivery_accepted"
        activation_tasks = bc_activation["effect_task_ids"] + ec_activation["effect_task_ids"]
        s.start_worker()
        try:
            s.wait_tasks(activation_tasks)
        finally:
            s.stop_worker()
        report.update(outcome_id=outcome, feedback_id=feedback, delivery_acceptance_id=approved_id,
                      initial_states={"delivery": "delivery_accepted", "outcome": "not_assessed", "mf": "investigating"},
                      independent_outcome_assessments=decisions, mf_acceptance_id=mf_acceptance_id,
                      mf_closure_receipt_id=closed["receipt_id"], final_states={"delivery": "delivery_accepted", "outcome": "achieved", "mf": "closed"},
                      activation_task_ids=activation_tasks, expired_and_future_observations_rejected=True,
                      outcome_judgment_valid_and_known_time_verified=True)
        reports["independent_judgments"] = report

    return reports
