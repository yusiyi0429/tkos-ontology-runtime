"""Real HTTP boundary cases for M1A research, no-change and Method context."""
from copy import deepcopy
from datetime import datetime, timezone

from .cases_chain import check, payload
from .flow import exact


def _context(flow, actor, roots, *, stage, purpose="analysis", when=None, drafts=False):
    when = when or datetime.now(timezone.utc).isoformat()
    return flow.clients[actor].json("POST", "/v1/context-packs", {
        "object_ids": [ref["object_id"] for ref in roots], "valid_at": when, "known_at": when,
        "contract_version": "tkos.method/0.1", "stage": stage, "purpose": purpose,
        "include_drafts": drafts,
    })


def _business_heads(flow):
    types = {"LTCO", "PCO", "Mission", "DomainCommitment", "ExecutionCommitment", "WorkItem", "Deliverable"}
    return {str(r["object_id"]): r for r in flow.rows("gov_objects") if r["object_type"] in types}


def run(flow, book, chain, business):
    # Use a fresh potential issue so this denial is about current CEO authority,
    # rather than an already-promoted issue's wrong phase.
    potential = exact(flow.act("ceo_agent", "m1a_open_potential_issue", {
        "domain_id": flow.f["domains"]["company"], "payload": {
            "title": "Independent CEO confirmation boundary", "summary": "A live potential issue",
            "signal_refs": chain["signals"],
        }})["result"])
    attempted = flow.command("m1a_confirm_strategic_issue", {"reason": "Agent tries to become CEO"}, oid=potential["object_id"])
    proof = flow.deny("ceo_agent", attempted, codes={"FORBIDDEN"})
    confirmed = flow.act("ceo", "m1a_confirm_strategic_issue", {"reason": "CEO personally confirms this issue"}, oid=potential["object_id"])
    assert confirmed["result"]["phase"] == "issue_confirmed"
    check(book, "METHOD-01", "only_ceo_confirms_strategic_issue", {"denials": proof, "ceo_receipt": confirmed["receipt_id"]})

    # Pass v1 first, then change the report: rejecting only an unsupported
    # revision operation would not prove invalidation of a real prior pass.
    loop = flow.start_issue()
    flow.clarify(loop)
    flow.report(loop)
    old = deepcopy(loop["report"])
    known_before_revision = datetime.now(timezone.utc).isoformat()
    old_prechecks = [r for r in flow.rows("gov_method_reviews") if r["kind"] == "research_quality_precheck"
                    and str(r["target_revision_id"]) == old["revision_id"]]
    assert len(old_prechecks) == 1 and old_prechecks[0]["content"]["result"] == "pass"
    changed = payload(flow, old)
    changed["findings"].append("An additional counterexample requires a new quality review")
    loop["report"] = flow.act("a", "m1a_publish_report", {"payload": changed}, oid=loop["issue"]["object_id"])["result"]["report_ref"]
    assert loop["report"]["revision_id"] != old["revision_id"]
    assert "precheck" not in flow.object(loop["issue"]["object_id"])["method_state"]
    open_attempt = flow.command("m1a_open_meeting", {
        "report_ref": loop["report"], "title": "Try reusing v1 pass", "objective": "Discuss the changed report",
        "material_refs": [loop["report"], loop["evidence"]],
    }, oid=loop["issue"]["object_id"])
    meeting_denial = flow.deny("a", open_attempt, codes={"INVALID_STATE"})
    flow.act("a", "m1a_submit_report", {"report_ref": loop["report"], "statement": "Submit the changed exact version"}, oid=loop["issue"]["object_id"])
    stale_precheck = flow.command("m1a_precheck_report", {"report_ref": old, "result": "pass", "findings": ["Review of the obsolete report"]}, oid=loop["issue"]["object_id"])
    precheck_denial = flow.deny("ceo_agent", stale_precheck, codes={"STALE_DEPENDENCY"})
    fresh_pass = flow.act("ceo_agent", "m1a_precheck_report", {"report_ref": loop["report"], "result": "pass", "findings": ["Changed report independently rechecked"]}, oid=loop["issue"]["object_id"])
    check(book, "METHOD-03", "report_revision_invalidates_prior_precheck", {"old_pass_record": str(old_prechecks[0]["record_id"]),
        "old_report": old, "new_report": loop["report"], "stale_precheck_denials": precheck_denial})
    flow.meeting(loop)
    check(book, "METHOD-03", "meeting_requires_current_passed_precheck", {"denials": meeting_denial, "fresh_pass": fresh_pass["receipt_id"], "meeting": loop["meetings"][0]["meeting"]})

    # No-change completes this round while preserving Strategy and every existing
    # target. Keeping issue lifecycle active is intentional, not a hidden close.
    strategy_before = flow.object(chain["strategy"]["object_id"])
    heads_before = _business_heads(flow)
    authority_before = flow.rows("gov_execution_authorities")
    no_change = flow.act("ceo", "m1a_decide_update", {"agreement_ref": loop["agreement"], "needs_update": False,
        "reason": "This research supports retaining the currently effective Strategy"}, oid=loop["issue"]["object_id"])
    issue_state = flow.object(loop["issue"]["object_id"])
    assert issue_state["method_state"]["phase"] == "completed" and issue_state["method_state"]["outcome"] == "no_change"
    assert issue_state["method_state"]["completion_reason"]
    check(book, "METHOD-06", "no_change_completes_round_with_reason", {"receipt_id": no_change["receipt_id"], "state": issue_state["method_state"]})
    assert flow.object(chain["strategy"]["object_id"]) == strategy_before
    check(book, "METHOD-06", "no_change_preserves_strategy", chain["strategy"])
    assert issue_state["lifecycle_status"] != "closed" and _business_heads(flow) == heads_before
    assert flow.rows("gov_execution_authorities") == authority_before
    check(book, "METHOD-06", "no_automatic_issue_closure_or_execution_creation", {"issue_status": issue_state["lifecycle_status"], "unchanged_existing_targets": len(heads_before)})

    # A domain update follows exactly the same three distinct decisions, but
    # changes StrategicJudgment only. It never edits PCO/Mission/Execution.
    domain_chain = flow.start_issue()
    flow.clarify(domain_chain)
    flow.report(domain_chain)
    flow.meeting(domain_chain)
    oid = domain_chain["issue"]["object_id"]
    flow.act("ceo", "m1a_decide_update", {"agreement_ref": domain_chain["agreement"], "needs_update": True,
        "reason": "Adjust a domain judgment while retaining the company Strategy"}, oid=oid)
    domain_chain["proposal"] = flow.act("ceo_agent", "m1a_propose_update", {"payload": {
        "title": "Domain judgment update", "issue_ref": domain_chain["issue"], "agreement_ref": domain_chain["agreement"],
        "rationale": "Evidence calls for a narrower domain assumption", "changes": [{"scope": "domain",
            "domain_id": flow.f["domains"]["a"], "payload": {"title": "Pilot domain judgment",
                "statement": "Use direct evidence before broadening pilot scope", "strategy_ref": chain["strategy"], "unit_id": "unit-pilot"}}],
    }}, oid=oid)["result"]["proposal_ref"]
    flow.act("co_agent", "m1a_review_update", {"proposal_ref": domain_chain["proposal"], "accepted": True,
        "impact_level": "domain", "findings": ["Only the domain strategic judgment changes; existing operating commitments retain their basis"]}, oid=oid)
    before_domain = _business_heads(flow)
    result = flow.act("ceo", "m1a_confirm_update", {"proposal_ref": domain_chain["proposal"], "reason": "Confirm the reviewed domain judgment"}, oid=oid)
    assert len(result["result"]["changed_refs"]) == 1
    judgment = result["result"]["changed_refs"][0]
    value = flow.object(judgment["object_id"])
    assert value["object_type"] == "StrategicJudgment" and value["domain_id"] == flow.f["domains"]["a"]
    assert value["effective_revision_id"] == judgment["revision_id"]
    assert value["effective_revision"]["payload"]["source_agreement_ref"] == domain_chain["agreement"]
    assert value["effective_revision"]["payload"]["source_proposal_ref"] == domain_chain["proposal"]
    assert _business_heads(flow) == before_domain and flow.object(chain["strategy"]["object_id"]) == strategy_before
    original_judgment, original_judgment_payload = deepcopy(judgment), deepcopy(value["effective_revision"]["payload"])
    first_confirmation = result["receipt_id"]

    # The same domain judgment must remain usable by its source issue's current
    # CEO/coordination agents in a later research issue, without granting their
    # company identity blanket read access to the domain.
    next_domain = flow.start_issue()
    flow.clarify(next_domain)
    flow.report(next_domain)
    flow.meeting(next_domain)
    next_oid = next_domain["issue"]["object_id"]
    flow.act("ceo", "m1a_decide_update", {"agreement_ref": next_domain["agreement"], "needs_update": True,
        "reason": "Revise the existing domain judgment against new evidence"}, oid=next_oid)
    next_domain["proposal"] = flow.act("ceo_agent", "m1a_propose_update", {"payload": {
        "title": "Domain judgment v2 proposal", "issue_ref": next_domain["issue"], "agreement_ref": next_domain["agreement"],
        "rationale": "New research refines the earlier domain assumption", "changes": [{"scope": "domain",
            "domain_id": flow.f["domains"]["a"], "target_ref": original_judgment,
            "payload": {"title": "Pilot domain judgment v2", "statement": "Require direct evidence and an explicit counterexample review",
                "strategy_ref": chain["strategy"], "unit_id": "unit-pilot"}}],
    }}, oid=next_oid)["result"]["proposal_ref"]
    private_domain_evidence = flow.upload(actor="a", domain="a", text="Private domain evidence not shared with any research issue")
    for actor in ["ceo_agent", "co_agent"]:
        assert flow.clients[actor].revision(original_judgment["object_id"], original_judgment["revision_id"])["payload_hash"] == original_judgment["payload_hash"]
        for path in ["/v1/objects/" + private_domain_evidence["object_id"] + "/revisions/" + private_domain_evidence["revision_id"],
                     "/v1/evidence-assets/" + private_domain_evidence["object_id"] + "/revisions/" + private_domain_evidence["revision_id"]]:
            denied = flow.clients[actor].json("GET", path, expected={403, 404})
            assert denied["error"]["code"] in {"FORBIDDEN", "NOT_FOUND"}
    flow.act("co_agent", "m1a_review_update", {"proposal_ref": next_domain["proposal"], "accepted": True,
        "impact_level": "domain", "findings": ["Reviewed the exact existing judgment and the new issue's evidence"]}, oid=next_oid)
    result = flow.act("ceo", "m1a_confirm_update", {"proposal_ref": next_domain["proposal"], "reason": "Confirm the reviewed domain judgment v2"}, oid=next_oid)
    assert len(result["result"]["changed_refs"]) == 1
    judgment = result["result"]["changed_refs"][0]
    assert judgment["object_id"] == original_judgment["object_id"] and judgment["revision_id"] != original_judgment["revision_id"]
    revised = flow.object(judgment["object_id"])
    assert revised["effective_revision_id"] == judgment["revision_id"]
    assert revised["effective_revision"]["payload"]["source_agreement_ref"] == next_domain["agreement"]
    assert revised["effective_revision"]["payload"]["source_proposal_ref"] == next_domain["proposal"]
    assert payload(flow, original_judgment) == original_judgment_payload
    for version in [original_judgment, judgment]:
        for actor in ["ceo_agent", "co_agent"]:
            assert flow.clients[actor].revision(version["object_id"], version["revision_id"])["payload_hash"] == version["payload_hash"]
        denied = flow.clients["outsider_agent"].json("GET", "/v1/objects/" + version["object_id"] + "/revisions/" + version["revision_id"], expected={403, 404})
        assert denied["error"]["code"] in {"FORBIDDEN", "NOT_FOUND"}
    assert _business_heads(flow) == before_domain and flow.object(chain["strategy"]["object_id"]) == strategy_before
    check(book, "METHOD-06", "domain_update_changes_strategic_judgment_only", {
        "original_judgment_ref": original_judgment, "judgment_ref": judgment,
        "confirmation_receipts": [first_confirmation, result["receipt_id"]],
        "v1_preserved": True, "v2_exact_dual_sources": [next_domain["agreement"], next_domain["proposal"]],
        "unassigned_actor_denied": True, "unshared_domain_evidence_metadata_and_bytes_denied": True,
    })

    # Current roles authorize every selected source, regardless of root access.
    complete_context = _context(flow, "ceo", [chain["issue"], chain["strategy"], business["review2"]], stage="general")
    kinds = {i["object_type"]: i for i in complete_context["selected"]}
    assert kinds["EvidenceAsset"]["nature"] == "original_evidence"
    assert kinds["ResearchReport"]["nature"] == "agent_analysis"
    assert kinds["StrategicAgreement"]["nature"] == "human_decision"
    assert kinds["PeriodReview"]["nature"] == "agent_analysis"
    assert kinds["PeriodReview"]["revision_id"] == business["review2"]["revision_id"]
    check(book, "METHOD-13", "context_distinguishes_raw_material_analysis_human_decision_and_review", {
        "snapshot_id": complete_context["context_snapshot_id"], "types": {key: item["nature"] for key, item in kinds.items()}})
    private_source = flow.upload(text="Not shared with any research issue")
    research_context = _context(flow, "dri_agent", [loop["issue"], private_source], stage="research")
    assert loop["report"]["revision_id"] in {i["revision_id"] for i in research_context["selected"]}
    assert all(i["object_id"] != private_source["object_id"] for i in research_context["selected"])
    assert any(i["reason"] == "not_found_or_not_authorized" for i in research_context["excluded"])
    assert all(i["payload_hash"] for i in research_context["selected"])
    historic_report = _context(flow, "ceo", [old], stage="research", when=known_before_revision)
    historical_item = next(i for i in historic_report["selected"] if i["object_id"] == old["object_id"])
    assert historical_item["revision_id"] == old["revision_id"]
    assert historical_item["collaboration_records"]
    assert all(r["target_revision_id"] == old["revision_id"] for r in historical_item["collaboration_records"])
    # Use the real original LTCO revision creation instant: approval occurred
    # later. Knowing the approval today must not make that earlier draft an
    # already-effective business baseline at its creation instant.
    ltco_original = flow.clients["ceo"].revision(business["ltco"]["original_ltco"]["object_id"], business["ltco"]["original_ltco"]["revision_id"])
    before_approval = flow.clients["ceo"].json("POST", "/v1/context-packs", {
        "object_ids": [business["ltco"]["ltco"]["object_id"]], "valid_at": ltco_original["recorded_at"],
        "known_at": datetime.now(timezone.utc).isoformat(), "contract_version": "tkos.method/0.1",
        "stage": "planning", "purpose": "decision", "include_drafts": False,
    })
    assert before_approval["selected"] == []
    assert any(i["reason"] == "no_effective_revision_at_requested_times" for i in before_approval["excluded"])
    check(book, "METHOD-13", "context_records_adopted_versions_and_exclusion_reasons", {
        "snapshot_id": research_context["context_snapshot_id"], "historical_report_snapshot": historic_report["context_snapshot_id"],
        "preapproval_ltco_snapshot": before_approval["context_snapshot_id"],
        "private_source_excluded": True, "old_report_does_not_mix_new_precheck": True,
        "later_approval_not_backdated_to_draft_creation": True})
    meeting_context = _context(flow, "dri_agent", [loop["issue"]], stage="meeting")
    handoff_context = _context(flow, "dri_agent", [loop["issue"]], stage="general", purpose="handoff")
    assert not any(i["object_type"] == "StrategicAgreement" for i in research_context["selected"])
    assert any(i["object_type"] == "MeetingMinutes" for i in meeting_context["selected"])
    assert not any(i["object_type"] == "ResearchReport" for i in handoff_context["selected"])
    assert research_context["context_request"]["actor_id"] == flow.f["actors"]["dri_agent"]["principal_id"]
    assert research_context["context_request"]["selection_stage"] == "research"
    assert handoff_context["context_request"]["selection_purpose"] == "handoff"
    check(book, "METHOD-13", "role_stage_and_purpose_filter_context", {
        "research_snapshot": research_context["context_snapshot_id"], "meeting_snapshot": meeting_context["context_snapshot_id"],
        "handoff_snapshot": handoff_context["context_snapshot_id"], "actor": "dri_agent"})
    return {"no_change_issue": loop["issue"], "domain_judgment": judgment, "current_strategy": chain["strategy"],
            "contexts": {"complete": complete_context["context_snapshot_id"], "research": research_context["context_snapshot_id"],
                         "historical_report": historic_report["context_snapshot_id"]}}
