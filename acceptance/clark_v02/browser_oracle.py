"""Read-only physical acceptance of the browser-operated delivery/Outcome/MF chain."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.rows import dict_row

from acceptance.clark_v02.check import BffClient, Checks
from acceptance.runtime.client import utc_now


def run(state_file: Path) -> dict:
    checks = Checks(state_file)
    f = checks.fixture
    b = checks.state["business"]["scenarios"]["browser"]
    work, outcome, feedback = b["work_item"]["objectId"], b["outcome_id"], b["feedback_id"]
    scope = f["scope_id"]
    actors = f["actors"]
    with psycopg.connect(checks.env["APP_DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope,))

        def rows(query, params=()):
            return conn.execute(query, params).fetchall()

        work_state = rows("""SELECT work_item_revision_id::text, dri_assignment_id::text,
            acceptor_assignment_id::text, accepted_by::text, deliverable_object_id::text,
            submission_seq, latest_submission_revision_id::text, latest_acceptance_id::text
            FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s""", (scope, work))[0]
        assert work_state["accepted_by"] == actors["mission_dri"]["principal_id"]
        assert work_state["dri_assignment_id"] == actors["mission_dri"]["assignment_id"]
        assert work_state["acceptor_assignment_id"] == actors["verifier"]["assignment_id"]
        assert work_state["submission_seq"] == 2
        deliverable = work_state["deliverable_object_id"]
        revisions = rows("""SELECT revision_id::text, object_version, payload_hash, payload,
            recorded_by::text, action_id::text FROM gov_object_revisions
            WHERE scope_id=%s AND object_id=%s ORDER BY object_version""", (scope, deliverable))
        reviews = rows("""SELECT acceptance_id::text, submission_seq, work_item_revision_id::text,
            deliverable_revision_id::text, payload_hash, verification_result,
            verifier_principal_id::text, verifier_assignment_id::text, action_id::text
            FROM gov_delivery_acceptances WHERE scope_id=%s AND work_item_object_id=%s
            ORDER BY submission_seq""", (scope, work))
        assert len(revisions) == len(reviews) == 2
        assert [r["payload"]["submission_seq"] for r in revisions] == [1, 2]
        assert [r["verification_result"] for r in reviews] == ["changes_requested", "accepted"]
        assert [r["submission_seq"] for r in reviews] == [1, 2]
        assert [r["revision_id"] for r in revisions] == [r["deliverable_revision_id"] for r in reviews]
        assert [r["payload_hash"] for r in revisions] == [r["payload_hash"] for r in reviews]
        assert revisions[1]["payload"]["responds_to_acceptance_id"] == reviews[0]["acceptance_id"]
        assert all(r["recorded_by"] == actors["mission_dri"]["principal_id"] for r in revisions)
        assert all(r["verifier_principal_id"] == actors["verifier"]["principal_id"] for r in reviews)
        assert all(r["verifier_assignment_id"] == actors["verifier"]["assignment_id"] for r in reviews)
        assert all(r["work_item_revision_id"] == work_state["work_item_revision_id"] for r in reviews)
        assert work_state["latest_acceptance_id"] == reviews[1]["acceptance_id"]
        assert work_state["latest_submission_revision_id"] == revisions[1]["revision_id"]

        assessments = rows("""SELECT assessment_id::text, outcome_revision_id::text, assessment_result,
            observation_revision_ids, evidence_revision_ids, delivery_acceptance_ids,
            assessor_principal_id::text, assessor_assignment_id::text, action_id::text
            FROM gov_outcome_assessments WHERE scope_id=%s AND outcome_object_id=%s
            ORDER BY recorded_at,assessment_id""", (scope, outcome))
        assert len(assessments) == 2
        assert [r["assessment_result"] for r in assessments] == ["not_achieved", "achieved"]
        assert all(r["assessor_principal_id"] == actors["ceo"]["principal_id"] for r in assessments)
        assert all(r["assessor_assignment_id"] == actors["ceo"]["assignment_id"] for r in assessments)
        assert len({r["outcome_revision_id"] for r in assessments}) == 1
        observations = rows("""SELECT r.revision_id::text, r.payload, o.object_type
            FROM gov_object_revisions r JOIN gov_objects o ON o.scope_id=r.scope_id AND o.object_id=r.object_id
            WHERE r.scope_id=%s AND r.revision_id=ANY(%s::uuid[])""",
            (scope, [rid for item in assessments for rid in item["observation_revision_ids"]]))
        assert len(observations) == 2 and all(r["object_type"] == "MetricObservation" for r in observations)
        for observation in observations:
            assert {"object_id": outcome, "revision_id": assessments[0]["outcome_revision_id"]} in observation["payload"]["upstream_refs"]

        mf_state = rows("""SELECT cycle_id::text, assignee_assignment_id::text, resolution_source,
            resolution_decision_revision_id::text FROM gov_feedback_state WHERE scope_id=%s AND object_id=%s""", (scope, feedback))[0]
        mf_acceptances = rows("""SELECT acceptance_id::text, cycle_id::text, feedback_revision_id::text,
            decision_revision_id::text, evidence_revision_ids, verification_result,
            verifier_assignment_id::text, verifier_principal_id::text, action_id::text
            FROM gov_acceptances WHERE scope_id=%s AND feedback_object_id=%s ORDER BY recorded_at""", (scope, feedback))
        assert len(mf_acceptances) == 1
        mf = mf_acceptances[0]
        assert mf_state["assignee_assignment_id"] == actors["domain_dri"]["assignment_id"]
        assert mf_state["resolution_source"] == "decision"
        assert mf["cycle_id"] == mf_state["cycle_id"] and mf["verification_result"] == "accepted"
        assert mf["decision_revision_id"] == mf_state["resolution_decision_revision_id"]
        assert mf["verifier_principal_id"] == actors["verifier"]["principal_id"]
        assert mf["verifier_assignment_id"] == actors["verifier"]["assignment_id"]
        assert mf["acceptance_id"] not in {row["acceptance_id"] for row in reviews}
        decision = rows("""SELECT r.revision_id::text, r.object_id::text, r.payload, r.payload_hash,
            r.recorded_by::text, r.action_id::text, o.object_type, o.effective_revision_id::text
            FROM gov_object_revisions r JOIN gov_objects o ON o.scope_id=r.scope_id AND o.object_id=r.object_id
            WHERE r.scope_id=%s AND r.revision_id=%s""", (scope, mf["decision_revision_id"]))[0]
        assert decision["object_type"] == "Decision" and decision["effective_revision_id"] == decision["revision_id"]
        assert decision["recorded_by"] == actors["domain_dri"]["principal_id"]
        closures = rows("""SELECT event_id::text, action_id::text, principal_id::text, to_status, detail
            FROM gov_lifecycle_events WHERE scope_id=%s AND object_id=%s AND event_type='confirm_closure'
            ORDER BY recorded_at""", (scope, feedback))
        assert len(closures) == 1
        closure = closures[0]
        assert closure["principal_id"] == actors["ceo"]["principal_id"] and closure["to_status"] == "closed"
        assert closure["detail"]["acceptance_id"] == mf["acceptance_id"]
        assert closure["detail"]["resolution_decision_revision_id"] == decision["revision_id"]
        assert mf["feedback_revision_id"] == rows("SELECT latest_revision_id::text FROM gov_objects WHERE scope_id=%s AND object_id=%s", (scope, feedback))[0]["latest_revision_id"]

        target_receipts = rows("""SELECT receipt_id::text, principal_id::text, action_type,
            target_object_id::text, result, effect_task_ids FROM gov_action_receipts
            WHERE scope_id=%s AND target_object_id=ANY(%s::uuid[]) ORDER BY recorded_at,receipt_id""",
            (scope, [work, outcome, feedback, decision["object_id"]]))
        expected_actors = {"accept_work_item": "mission_dri", "submit_deliverable": "mission_dri",
            "review_deliverable": "verifier", "record_outcome_assessment": "ceo",
            "confirm_decision": "domain_dri", "request_feedback_acceptance": "domain_dri",
            "record_acceptance": "verifier", "confirm_closure": "ceo"}
        expected_counts = {"accept_work_item": 1, "submit_deliverable": 2, "review_deliverable": 2,
            "record_outcome_assessment": 2, "confirm_decision": 1, "request_feedback_acceptance": 1,
            "record_acceptance": 1, "confirm_closure": 1}
        relevant_receipts = [r for r in target_receipts if r["action_type"] in expected_actors]
        for action, count in expected_counts.items():
            selected = [r for r in relevant_receipts if r["action_type"] == action]
            assert len(selected) == count, f"Unexpected {action} receipt count"
            assert all(r["principal_id"] == actors[expected_actors[action]]["principal_id"] for r in selected)
            assert all(not r["effect_task_ids"] for r in selected)
        receipt_ids = {row["receipt_id"] for row in relevant_receipts}
        assert all(r["action_id"] in receipt_ids for r in [*revisions, *reviews, *assessments, mf, closure])
        closure_receipt = next(r for r in relevant_receipts if r["receipt_id"] == closure["action_id"])
        assert closure_receipt["result"]["acceptance_id"] == mf["acceptance_id"]
        assert closure_receipt["result"]["cycle_id"] == mf["cycle_id"]
        states = rows("""SELECT object_id::text, object_type, lifecycle_status, object_version,
            latest_revision_id::text,effective_revision_id::text FROM gov_objects
            WHERE scope_id=%s AND object_id=ANY(%s::uuid[]) ORDER BY object_type""",
            (scope, [work, deliverable, outcome, feedback]))
        status_by_id = {r["object_id"]: r for r in states}
        assert status_by_id[work]["lifecycle_status"] == "delivery_accepted"
        assert status_by_id[deliverable]["lifecycle_status"] == "accepted"
        assert status_by_id[outcome]["lifecycle_status"] == "confirmed"
        assert status_by_id[feedback]["lifecycle_status"] == "closed"
        assert status_by_id[work]["latest_revision_id"] == work_state["work_item_revision_id"]
        assert status_by_id[outcome]["effective_revision_id"] == assessments[0]["outcome_revision_id"]

    client = BffClient(checks.url, "ceo", checks.codes["ceo"], checks.log)
    try:
        feedback_projection = client.object(feedback)["feedback"]
        exact_revision = feedback_projection["resolution_decision_revision"]
        assert exact_revision["revision_id"] == decision["revision_id"]
        assert exact_revision["payload"] == decision["payload"] and exact_revision["payload_hash"] == decision["payload_hash"]
        assert len(feedback_projection["acceptances"]) == 1
        assert feedback_projection["acceptances"][0]["acceptance_id"] == mf["acceptance_id"]
        assert client.object(outcome)["outcome_achievement"] == "achieved"
    finally:
        client.close()
    report = {"run_id": checks.state["run_id"], "checked_at": utc_now(), "status": "passed",
        "database_role": "application", "transaction": "REPEATABLE READ READ ONLY", "business_mutations": 0,
        "source": "Browser-operated chain; real PostgreSQL rows plus Clark BFF readback",
        "work_item_state": work_state, "objects": states, "delivery_revisions": revisions,
        "delivery_reviews": reviews, "outcome_assessments": assessments, "observations": observations,
        "mf_state": mf_state, "mf_acceptance": mf, "exact_resolution_decision": decision,
        "closure_event": closure, "actual_principal_receipts": relevant_receipts,
        "receipt_counts": expected_counts, "lost_response_replay_did_not_duplicate_submission": True,
        "decision_statement_projection_matches_exact_accepted_revision": True,
        "final_states": {"delivery": "delivery_accepted", "outcome": "achieved", "mf": "closed"},
        "released": False, "production_deployed": False}
    path = checks.output / "clark-browser-sql-oracle.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({"status": "passed", "report": str(path), "delivery_versions": 2,
                      "delivery_reviews": 2, "outcome_assessments": 2, "mf_acceptances": 1, "closures": 1}))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_file", type=Path)
    run(parser.parse_args().state_file)
