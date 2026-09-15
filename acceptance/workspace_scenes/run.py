"""Real Runtime HTTP scenes. No Clark code or model invocation is part of this runner."""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import uuid

import httpx

from acceptance.method_independent.fixture import seed_authority, register_method
from acceptance.method_independent.harness import MethodHarness
from acceptance.method_independent.m1b_flow import MethodFlow
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from acceptance.runtime.client import Client
from .drop_response import drop_response


def uid():
    return str(uuid.uuid4())


class Scenes:
    def __init__(self, flow):
        self.flow = flow
        self.commands = []

    def get(self, sid, actor="a"):
        return self.flow.clients[actor].json("GET", "/v1/workspace-scenes/" + sid)

    def command(self, sid, event, actor="a", version=None):
        return {"contract_version": "tkos.workspace/0.1", "scene_id": sid,
                "expected_version": version if version is not None else self.get(sid, actor)["version"],
                "idempotency_key": uid(), "event": event}

    def post(self, actor, body, **kwargs):
        return self.flow.clients[actor].json("POST", "/v1/workspace-scenes/events", body, **kwargs)

    def write(self, sid, event, actor="a"):
        command = self.command(sid, event, actor)
        receipt = self.post(actor, command)
        self.commands.append((actor, command, receipt))
        return receipt["result"]["event_id"]

    def create(self, kind, anchor, actor="a", owner="a", participants=("b",)):
        sid = uid()
        event = {"kind": "create", "scene_type": kind, "anchor_ref": anchor, "external_id": uid(),
                 "title": "Synthetic Clark " + kind, "owner_assignment_id": self.flow.f["actors"][owner]["assignment_id"],
                 "participant_assignment_ids": [self.flow.f["actors"][a]["assignment_id"] for a in participants]}
        body = self.command(sid, event, actor, version=0)
        self.post(actor, body)
        return sid


def run(h, source, output):
    checks = []
    initial_source = source_manifest(source)

    def check(name, predicate):
        assert predicate, name
        checks.append(name)
        print("PASS " + name, flush=True)

    f = seed_authority(h.env, h.private / "identities.json", "runtime-acceptance-workspace-" + uid()[:8])
    register_method(h, source, f)
    process, url, _ = h.start_api(source)
    flow = MethodFlow(h, url, f)
    scenes = Scenes(flow)
    try:
        response = flow.clients["a"].request("GET", "/v1/identity")
        check("identity_personal_no_store", response.json()["principal_id"] == f["actors"]["a"]["principal_id"] and response.headers["cache-control"] == "no-store")
        flow.clients["a"].request("GET", "/v1/identity?principal_id=" + f["actors"]["ceo"]["principal_id"], expected=422)
        check("identity_cannot_choose_actor", True)
        chain = flow.strategy()
        ltco = flow.ltco(chain["strategy"])["ltco"]
        targets = flow.open_window(flow.draft_targets(chain["strategy"], ltco))
        sid = scenes.create("monthly", targets["window"], actor="co_agent", owner="ceo", participants=("a", "b"))
        check("missing_presentation_not_filled_from_sample", scenes.get(sid)["monthly"]["feedback_deadline"]["status"] == "missing")
        scenes.write(sid, {"kind": "monthly_material", "target_ref": targets["mission"], "source_refs": [targets["mission"]],
                          "priority": "P1", "why": "Owner supplied planning note", "feedback_deadline": targets["period"]["start"]}, "co_agent")
        check("feedback_deadline_separate_no_implicit_close", scenes.get(sid)["monthly"]["window"]["phase"] == "open"
              and scenes.get(sid)["monthly"]["business_period"] == targets["period"])
        illegal_create = scenes.command(uid(), {"kind": "create", "scene_type": "monthly", "anchor_ref": targets["window"],
            "external_id": uid(), "title": "DRI cannot create CEO scene", "owner_assignment_id": f["actors"]["a"]["assignment_id"]}, version=0)
        scenes.post("a", illegal_create, expected=403)
        check("DRI_cannot_assume_domain_coagent_authority", True)
        comment = flow.comment(targets)
        anchored = scenes.write(sid, {"kind": "comment_anchor", "review_record_id": comment,
            "target_ref": targets["pco"], "field_path": "/unit_outcomes/0/result_statement"})
        check("comment_anchor_exact_field", any(e["event_id"] == anchored for e in scenes.get(sid)["events"]))
        replaced = flow.comment(targets, replaces=comment)
        b_comment = flow.comment(targets, actor="b")
        flow.act("b", "m1b_withdraw_comment", {"review_record_id": b_comment, "reason": "Withdraw own earlier opinion"}, oid=targets["window"]["object_id"])
        monthly = scenes.get(sid)["monthly"]
        check("opinion_history_and_effective_count", monthly["visible_effective_opinion_count"] == 1 and len(monthly["my_reviews"]) >= 2)
        spoof = scenes.command(sid, {"kind": "comment_anchor", "review_record_id": replaced,
            "target_ref": targets["pco"], "field_path": "/title"}, actor="b")
        scenes.post("b", spoof, expected=403)
        check("cannot_anchor_another_person_comment", True)
        flow.clients["outsider"].request("GET", "/v1/workspace-scenes/" + sid, expected=404)
        outsider = flow.clients["outsider"].json("GET", "/v1/workspace-scenes")
        check("cross_domain_list_no_counts_or_cursor", outsider == {"items": [], "next_after": None})
        stale_close = flow.prepare("co_agent", "m1b_close_window", {"reason": "Freeze current opinions"}, oid=targets["window"]["object_id"])
        flow.comment(targets, actor="b")
        flow.clients["co_agent"].request("POST", "/v1/actions", stale_close, expected=409)
        check("comment_vs_close_CAS", True)
        flow.close_window(targets)
        when = datetime.now(timezone.utc).isoformat()
        context = flow.clients["co_agent"].json("POST", "/v1/context-packs", {
            "object_ids": [targets[k]["object_id"] for k in ("window", "pco", "mission")], "valid_at": when, "known_at": when,
            "contract_version": "tkos.method/0.1", "stage": "review", "purpose": "analysis", "include_drafts": True})
        check("coagent_context_uses_own_identity_and_complete_roots", context["context_request"]["actor_id"] == f["actors"]["co_agent"]["principal_id"]
              and {targets[k]["object_id"] for k in ("window", "pco", "mission")} <= {item["object_id"] for item in context["selected"]})
        incomplete = flow.resolution_params(targets)
        incomplete["dispositions"] = []
        flow.clients["co_agent"].request("POST", "/v1/actions/prepare", flow.command("m1b_resolve_window", incomplete, oid=targets["window"]["object_id"], actor="co_agent"), expected=(409, 422))
        check("consolidation_cannot_omit_frozen_opinions", True)
        flow.resolve(targets)
        before = flow.object(targets["candidate"]["object_id"])
        ack = scenes.write(sid, {"kind": "diff_response", "candidate_ref": targets["candidate"], "response": "reviewed"})
        check("diff_ack_does_not_confirm_candidate", flow.object(targets["candidate"]["object_id"]) == before)
        check("candidate_differences_have_exact_versions", bool(scenes.get(sid)["monthly"]["differences"]))
        agent_ack = scenes.command(sid, {"kind": "diff_response", "candidate_ref": targets["candidate"], "response": "reviewed"}, actor="co_agent")
        scenes.post("co_agent", agent_ack, expected=403)
        check("agent_cannot_ack_for_human", True)
        flow.clients["co_agent"].request("POST", "/v1/actions/prepare", flow.command("m1b_confirm_candidates", {"reason": "Agent cannot confirm"}, oid=targets["candidate"]["object_id"], actor="co_agent"), expected=403)
        check("coagent_cannot_confirm_candidate_for_CEO", True)
        flow.confirm_candidates(targets)
        missions = flow.clients["a"].json("GET", "/v1/workspaces/dri?collection=missions")["items"]
        check("formal_mission_handoff_no_execution_authority", len(missions) == 1 and missions[0]["handoff"]["execution_authority"] is None and missions[0]["handoff"]["delivery_accepted"] is None)
        fact = flow.fact(targets)
        corrected = flow.fact(targets, corrects=fact, value=3)
        review = flow.review(targets, [corrected])
        regenerated = flow.review(targets, [corrected], previous=review)
        facts = flow.clients["ceo"].json("GET", "/v1/workspaces/ceo?collection=business-facts")["items"]
        reviews = flow.clients["ceo"].json("GET", "/v1/workspaces/ceo?collection=period-reviews")["items"]
        check("business_fact_correction_keeps_original", {fact["object_id"], corrected["object_id"]} <= {r["object_id"] for r in facts})
        check("period_review_regeneration_is_analysis_without_approval", reviews[0]["latest_revision"]["revision_id"] == regenerated["revision_id"] and reviews[0]["nature"] == "agent_analysis" and reviews[0]["phase"] == "generated")
        weekly = scenes.create("weekly", targets["mission"])
        # Source resides in the owner's domain. Sharing the scene never grants
        # another participant the right to read this evidence or count it.
        source_ref = flow.upload(actor="a", domain="a", text="Synthetic weekly evidence: original source recorded")
        material = {"kind": "weekly_material", "period": targets["period"], "headline": "Evidence is ready for review",
            "advances": ["Original source recorded"], "problems": [], "implications": [], "handling": [],
            "sources": [{"source_id": "src-1", "label": "Original source", "state": "read", "observed_at": targets["period"]["start"], "evidence_ref": source_ref}],
            "questions": [{"question_id": "q1", "prompt": "What remains?", "reason": "Clarify next step"}], "source_refs": [source_ref]}
        material_id = scenes.write(weekly, material)
        other = scenes.get(weekly, "b")
        check("material_sources_do_not_expand_read_grants", other["materials"]["weekly_material"]["status"] == "missing" and not any(e["kind"] == "weekly_material" for e in other["events"]))
        answer = scenes.write(weekly, {"kind": "weekly_answer", "material_event_id": material_id, "question_id": "q1", "answer": "Verify source quality"})
        scenes.write(weekly, {"kind": "weekly_confirm", "material_event_id": material_id, "answer_event_ids": [answer], "signal": "yellow"})
        check("weekly_exact_personal_confirmation", scenes.get(weekly)["weekly"]["confirmed_current_material"])
        scenes.write(weekly, {"kind": "withdraw", "event_id": answer, "reason": "Correct my answer"})
        check("withdraw_answer_invalidates_current_ack", not scenes.get(weekly)["weekly"]["confirmed_current_material"])
        material2 = scenes.write(weekly, {**material, "headline": "Updated evidence wording"})
        old = scenes.command(weekly, {"kind": "weekly_confirm", "material_event_id": material_id, "answer_event_ids": [answer], "signal": "green"})
        scenes.post("a", old, expected=409)
        check("old_weekly_material_cannot_be_confirmed", True)
        scenes.write(weekly, {"kind": "refresh_sources", "reason": "Please refresh source readings"})
        check("refresh_is_request_not_fake_source_read", scenes.get(weekly)["weekly"]["source_refresh"] == "pending_partner_refresh")
        scenes.write(weekly, {**material, "headline": "Co-agent links the exact Method facts and review", "source_refs": [corrected],
            "sources": [], "fact_refs": [corrected], "period_review_refs": [regenerated]}, "co_agent")
        check("weekly_material_can_reference_formal_fact_and_analysis_versions", scenes.get(weekly, "co_agent")["materials"]["weekly_material"]["value"]["payload"]["period_review_refs"] == [regenerated])
        check("hidden_new_material_does_not_fall_back_to_old", scenes.get(weekly)["materials"]["weekly_material"]["status"] == "missing" and not scenes.get(weekly)["weekly"]["confirmed_current_material"])
        meeting = scenes.create("meeting", targets["mission"], participants=("b",))
        scenes.write(meeting, {"kind": "read", "subject_ref": targets["mission"]})
        scenes.write(meeting, {"kind": "request_supplement", "subject_ref": targets["mission"], "note": "Bring source evidence"}, "b")
        scenes.write(meeting, {"kind": "bring_to_meeting", "subject_ref": targets["mission"]}, "b")
        invalid_finish = scenes.command(meeting, {"kind": "meeting_finish", "extract_requested": True})
        scenes.post("a", invalid_finish, expected=409)
        scenes.write(meeting, {"kind": "meeting_start", "recording_requested": True})
        scenes.write(meeting, {"kind": "meeting_finish", "extract_requested": True})
        transcript = flow.upload(actor="a", domain="a", text="DRI: Check original evidence.\nDRI: CEO must assess the mission boundary.")
        meeting_material = scenes.write(meeting, {"kind": "meeting_material", "transcript_ref": transcript,
            "transcript": [{"speaker": "DRI", "text": "Check original evidence."}, {"speaker": "DRI", "text": "CEO must assess the mission boundary."}]})
        publication = {"kind": "meeting_publish", "material_event_id": meeting_material, "summary": "Review evidence and escalate the boundary",
            "decisions": [], "actions": [{"text": "Check original evidence", "quote": {"line_index": 0, "text": "Check original evidence."}}],
            "open_questions": [], "ceo_judgments": [{"text": "Assess boundary", "quote": {"line_index": 1, "text": "CEO must assess the mission boundary."}, "why_ceo": "Company mission boundary", "category": "mission_boundary"}],
            "routes": [{"item_kind": "action", "item_index": 0, "recipient_assignment_id": f["actors"]["a"]["assignment_id"]},
                       {"item_kind": "ceo_judgment", "item_index": 0, "recipient_assignment_id": f["actors"]["ceo"]["assignment_id"]}]}
        bad = deepcopy(publication)
        bad["actions"][0]["quote"]["text"] = "Invented quotation"
        scenes.post("a", scenes.command(meeting, bad), expected=422)
        check("meeting_rejects_fabricated_quote", True)
        official_before = flow.object(targets["mission"]["object_id"])
        published = scenes.write(meeting, publication)
        check("one_meeting_publication_no_formal_mission_effect", flow.object(targets["mission"]["object_id"]) == official_before and scenes.get(meeting)["meeting"]["phase"] == "published")
        check("routing_does_not_claim_delivery", scenes.get(meeting)["meeting"]["message_delivery"] == "not_managed_by_runtime")
        check("DRI_aggregate_contains_meeting_state", flow.clients["a"].json("GET", "/v1/workspaces/dri?collection=meetings")["items"][0]["meeting"]["phase"] == "published")
        ceo_meetings = flow.clients["ceo"].json("GET", "/v1/workspaces/ceo?collection=meetings")["items"]
        check("authorized_CEO_reads_published_judgment_and_routing", ceo_meetings[0]["materials"]["meeting_publish"]["status"] == "available"
              and len(ceo_meetings[0]["meeting"]["publication"]["payload"]["ceo_judgments"]) == 1)
        check("meeting_membership_does_not_expand_source_permission", scenes.get(meeting, "b")["materials"]["meeting_publish"]["status"] == "missing")
        scenes.write(meeting, publication)
        second_publication = scenes.commands[-1][2]["result"]["event_id"]
        scenes.write(meeting, {"kind": "withdraw", "event_id": second_publication, "reason": "Correct publication"})
        check("publication_withdrawal_preserves_history_without_reviving_old_draft", scenes.get(meeting)["meeting"]["phase"] == "review")
        scenes.write(meeting, publication)
        # Duplicate clicks with one original envelope commit once.
        cmd = scenes.command(sid, {"kind": "read", "subject_ref": targets["mission"]})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: scenes.post("a", cmd), range(2)))
        check("concurrent_duplicate_click_same_receipt", results[0] == results[1])
        changed = deepcopy(cmd)
        changed["event"]["note"] = "changed content"
        scenes.post("a", changed, expected=409)
        check("idempotency_key_cannot_change_request", True)
        # Two different writes observing the same stream version conflict.
        first = scenes.command(sid, {"kind": "read", "subject_ref": targets["mission"]})
        second = scenes.command(sid, {"kind": "bring_to_meeting", "subject_ref": targets["mission"]})
        scenes.post("a", first)
        scenes.post("a", second, expected=409)
        check("scene_stream_CAS", True)
        # Private failure hook rolls back an inserted event before its receipt.
        atomic = scenes.command(sid, {"kind": "read", "subject_ref": targets["mission"]})
        old_version = scenes.get(sid)["version"]
        flow.clients["a"].request("POST", "/v1/workspace-scenes/events", atomic, expected=500, headers=h.headers("workspace_before_receipt"))
        # An injected unhandled failure closes the keep-alive connection.
        flow.close()
        flow = MethodFlow(h, url, f)
        scenes.flow = flow
        check("event_receipt_transaction_rollback", scenes.get(sid)["version"] == old_version)
        with drop_response(url) as (proxy_url, observed):
            with httpx.Client(trust_env=False, timeout=40) as client:
                try:
                    client.post(proxy_url + "/v1/workspace-scenes/events", json=atomic,
                        headers={"Authorization": "Bearer " + f["actors"]["a"]["token"]})
                except httpx.TransportError:
                    pass
                else:
                    raise AssertionError("The response was not dropped")
        check("actual_HTTP_response_loss_after_commit", observed["status"] == 200)
        stored = h.sql(f, "SELECT receipt_id FROM gov_action_receipts WHERE scope_id=%s AND principal_id=%s AND idempotency_key=%s",
                       (f["scope_id"], f["actors"]["a"]["principal_id"], atomic["idempotency_key"]))
        assert len(stored) == 1
        # Restart the real API and resend the identical durable envelope.
        before_restart = scenes.get(meeting)
        flow.close()
        h.stop(process)
        process, url, _ = h.start_api(source)
        flow = MethodFlow(h, url, f)
        scenes.flow = flow
        check("restart_preserves_material_publication_history", scenes.get(meeting) == before_restart)
        receipt = scenes.post("a", atomic)
        check("lost_result_original_envelope_retry", receipt["receipt_id"] == str(stored[0]["receipt_id"]) and scenes.get(sid)["version"] == old_version + 1)
        check("receipt_recovery_endpoint", flow.clients["a"].json("GET", "/v1/action-receipts/" + receipt["receipt_id"])["receipt"] == receipt)
        next_targets = flow.targets(chain["strategy"], ltco, confirm=False)
        next_scene = scenes.create("monthly", next_targets["window"], actor="co_agent", owner="ceo", participants=("a", "b"))
        scenes.write(next_scene, {"kind": "diff_response", "candidate_ref": next_targets["candidate"], "response": "reviewed"})
        flow.act("ceo", "m1b_reopen_candidates", {"reason": "Need another shared review", "title": "Explicitly reopened"}, oid=next_targets["candidate"]["object_id"])
        check("reopen_retains_ack_as_history_not_current_confirmation", not scenes.get(next_scene)["monthly"]["reviewed_current_candidate"])
        # Permission revocation uses the existing authorized governance action.
        b_read = scenes.command(sid, {"kind": "read", "subject_ref": targets["mission"]}, actor="b")
        b_receipt = scenes.post("b", b_read)
        flow.clients["ceo"].json("POST", "/v1/actions", Client.command("revoke_assignment", {"assignment_id": f["actors"]["b"]["assignment_id"]}))
        flow.clients["b"].request("GET", "/v1/workspace-scenes/" + sid, expected=(403, 404))
        scenes.post("b", b_read, expected=(403, 404))
        flow.clients["b"].request("GET", "/v1/action-receipts/" + b_receipt["receipt_id"], expected=(403, 404))
        check("revocation_blocks_scene_receipt_and_replay", True)
        events = h.sql(f, "SELECT e.event_id,e.version,e.scene_id,r.principal_id,r.action_type,r.effect_task_ids FROM gov_workspace_events e JOIN gov_action_receipts r ON (e.scope_id,e.action_id)=(r.scope_id,r.receipt_id) WHERE e.scope_id=%s", (f["scope_id"],))
        check("database_every_event_has_receipt_no_external_effect", len(events) > 20 and all(e["effect_task_ids"] == [] and e["action_type"].startswith("workspace.") for e in events))
        check("database_no_execution_objects", not h.sql(f, "SELECT object_id FROM gov_objects WHERE scope_id=%s AND object_type IN ('ExecutionCommitment','ExecutionPlan','WorkItem','Deliverable')", (f["scope_id"],)))
        candidates = h.sql(f, "SELECT r.payload FROM gov_object_revisions r JOIN gov_objects o ON (o.scope_id,o.object_id,o.latest_revision_id)=(r.scope_id,r.object_id,r.revision_id) WHERE o.scope_id=%s AND o.object_type='CandidateSet'", (f["scope_id"],))
        for candidate in candidates:
            for ref in candidate["payload"]["target_refs"]:
                member = h.sql(f, "SELECT payload_hash FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s", (f["scope_id"], ref["object_id"], ref["revision_id"]))
                assert member and member[0]["payload_hash"] == ref["payload_hash"]
        check("database_candidate_members_have_exact_persisted_versions", len(candidates) == 2)
        with h.app_connection() as conn:
            check("new_table_RLS_hides_scope_without_session_context", conn.execute("SELECT count(*) FROM gov_workspace_events").fetchone()["count"] == 0)
        with h.app_connection() as conn:
            privileges = conn.execute("SELECT has_table_privilege(current_user,'gov_workspace_events','UPDATE') AS update, has_table_privilege(current_user,'gov_workspace_events','DELETE') AS delete").fetchone()
            check("application_cannot_rewrite_or_delete_scene_history", not privileges["update"] and not privileges["delete"])
        check("production_source_unchanged_during_acceptance", source_manifest(source) == initial_source)
        public_json(output / "source-manifest.json", initial_source)
        public_json(output / "summary.json", {"runtime_scene_api_accepted": True, "checks": checks,
            "checks_passed": len(checks), "http_requests": h.log.counter, "partner_connected": False,
            "clark_browser_accepted": False, "real_model_accepted": False, "released": False, "deployed": False,
            "source_manifest_sha256": hashlib.sha256(json.dumps(initial_source, sort_keys=True).encode()).hexdigest()})
    finally:
        flow.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--private", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError("Use fresh output and private paths")
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    try:
        run(h, Path(__file__).resolve().parents[2] / "src", args.output.resolve())
    finally:
        h.close()


if __name__ == "__main__":
    main()
