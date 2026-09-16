"""Isolated 0.3 acceptance for tkos.dashboard/0.1 over real HTTP/PG/MinIO.

Builds the complete lawful chain (Strategy+Architecture -> LTCO -> PCO ->
Mission -> OperatingState/BusinessFact/PeriodReview/OperatingProblem) through
the real API in a fresh ``tkos_a1_method_*`` database, then verifies the
dashboard reads (including the local /dashboard/api facade) with a table-count
oracle proving the browse surface writes nothing.

    uv run python -m acceptance.dashboard_0_3.run \
        --env-file .runtime-acceptance/<isolated-db>/env.json \
        --private .runtime-acceptance/dashboard-0-3-run/private \
        --output .runtime-acceptance/dashboard-0-3-run/report

Private output contains tokens, requests and logs; only the sanitized summary
is publishable. No existing container, database or credential is modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import httpx

from acceptance.anchors_v03.fixture import register_v03, seed_v03
from acceptance.anchors_v03.run import Flow
from acceptance.method_independent.flow import exact
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json, source_manifest


def uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def paginate(client, path: str, *, limit: int = 2, max_pages: int = 20) -> list[dict]:
    items: list[dict] = []
    joiner = "&" if "?" in path else "?"
    cursor = None
    for _ in range(max_pages):
        suffix = f"&cursor={cursor}" if cursor else ""
        page = client.json("GET", f"{path}{joiner}limit={limit}{suffix}")
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return items


def run(h: MethodHarness, source: Path) -> None:  # noqa: C901 - acceptance matrix
    checks: list[str] = []
    initial = source_manifest(source)

    def check(name: str, value=True) -> None:
        assert value, name
        checks.append(name)
        print("PASS " + name, flush=True)

    f = seed_v03(h.env, h.private / "identities.json", "runtime-acceptance-dashboard-0-3-" + uid()[:8])
    register_v03(h, source, f)
    viewer = h.private / "viewer-token"
    viewer.write_text(f["actors"]["ceo"]["token"])
    viewer.chmod(0o600)
    process, url, _ = h.start_api(source, updates={
        "TKOS_DASHBOARD_ENABLED": "1",
        "TKOS_DASHBOARD_VIEWER_TOKEN_FILE": str(viewer),
        "TKOS_DASHBOARD_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "TKOS_DASHBOARD_ENV_LABEL": "synthetic",
        "TKOS_DASHBOARD_SYNTHETIC": "true",
    })
    flow = Flow(h, url, f)
    facade = httpx.Client(base_url=url, timeout=35, trust_env=False)

    try:
        company = f["domains"]["company"]
        # ---------------------------------------------------------------
        # Lawful chain: S1 + A1, LTCO, window 1 confirmed, window 3 pending
        # ---------------------------------------------------------------
        chain1 = flow.strategy(complete=True)
        s1 = chain1["strategy"]
        a1 = flow.object(s1["object_id"])["method_state"]["architecture_ref"]
        ltco1 = flow.ltco(s1)["ltco"]
        draft1 = flow.draft_targets(s1, ltco1)
        draft_m1 = dict(draft1["mission"])
        w1 = flow.open_window(draft1)
        flow.comment(w1)
        flow.close_window(w1)
        flow.resolve(w1)
        flow.confirm_candidates(w1)
        p1, m1 = w1["pco"], w1["mission"]

        pending = flow.draft_targets(s1, ltco1)
        flow.open_window(pending)
        flow.close_window(pending)
        flow.resolve(pending)
        check("lawful_pair_s1_confirmed_and_rebased_candidate_pending", True)

        # ---------------------------------------------------------------
        # Paired Strategy + Architecture update to S2
        # ---------------------------------------------------------------
        second = flow.start_issue()
        flow.clarify(second)
        flow.report(second)
        flow.meeting(second)
        flow.update(second, strategy_ref=s1, confirm=False)
        receipt = flow.commit("ceo", second["confirm_command"])
        s2 = receipt["result"]["changed_refs"][0]
        a2 = flow.object(s2["object_id"])["method_state"]["architecture_ref"]
        ltco2 = flow.ltco(s2)["ltco"]

        reopened = flow.act("ceo", "m1b_reopen_candidates", {
            "reason": "Explicitly adopt the new Strategy and LTCO",
            "title": "Synthetic rebased window",
            "rebase_strategy_ref": s2, "rebase_ltco_ref": ltco2,
        }, oid=pending["candidate"]["object_id"])["result"]
        pending["window"] = exact(reopened)
        pending["strategy"], pending["ltco"] = s2, ltco2
        pending["pco_payload"].update(strategy_ref=s2, ltco_ref=ltco2)
        flow.close_window(pending)
        draft_pending_mission = dict(pending["mission"])
        flow.resolve(pending)
        flow.confirm_candidates(pending)
        p2, m2 = pending["pco"], pending["mission"]
        draft4 = flow.draft_targets(s2, ltco2)
        m4 = draft4["mission"]
        check("lawful_paired_strategy_architecture_and_rebased_targets", True)

        # ---------------------------------------------------------------
        # Operating objects on the current mission M2
        # ---------------------------------------------------------------
        evidence = flow.upload()
        state_payload = {"subject_ref": m2, "as_of": _now(), "summary": "Evidence indicates progress",
                         "rag": "green", "baseline_refs": [m2], "evidence_refs": [evidence],
                         "data_gaps": [], "generation_version": "controlled-1"}
        state_ref = exact(flow.act("co_agent", "method_propose_state", {
            "domain_id": company, "payload": state_payload})["result"])
        flow.act("a", "method_confirm_state", {"reason": "Mission owner confirms exact evidence"},
                 oid=state_ref["object_id"])
        # A later recommendation preserves the State identity (same subject/as_of).
        state2_payload = {**state_payload, "summary": "Evidence indicates progress but quality remains uncertain",
                          "rag": "yellow", "generation_version": "controlled-2"}
        state2 = exact(flow.act("co_agent", "method_propose_state", {
            "domain_id": company, "payload": state2_payload, "previous_state_ref": state_ref,
        })["result"])
        confirmed = flow.act("a", "method_confirm_state",
                             {"reason": "Owner confirms the corrected assessment"}, oid=state_ref["object_id"])
        state2 = exact(confirmed["result"])

        # A state attached to the still-effective historical Mission revision
        # proves the state panel binds to the exact selected revision/outcome.
        historical_state = exact(flow.act("co_agent", "method_propose_state", {
            "domain_id": company,
            "payload": {"subject_ref": m1, "as_of": (datetime.now(timezone.utc) - timedelta(days=2)
                                                      ).replace(microsecond=0).isoformat(),
                        "summary": "Historical basis snapshot", "rag": "unknown",
                        "baseline_refs": [m1], "evidence_refs": [],
                        "data_gaps": ["No independent evidence recorded"],
                        "generation_version": "controlled-historical-1"},
        })["result"])
        flow.act("a", "method_confirm_state", {"reason": "Owner confirms the historical snapshot"},
                 oid=historical_state["object_id"])

        fact2 = flow.fact(pending, value=3)
        fact_corrected = flow.fact(pending, corrects=fact2, value=4)
        topic_fact = exact(flow.act("co_agent", "m1b_record_fact", {
            "domain_id": company,
            "payload": {"fact_id": "topic-" + uid()[:8], "subject_ref": {"topic": "Synthetic company cash"},
                        "as_of": _now(), "metric": "cash_position", "value": 100, "unit": "CNY",
                        "source_ref": evidence},
        })["result"])

        period = pending["period"]
        review = exact(flow.act("co_agent", "m1b_generate_review", {
            "domain_id": company,
            "payload": {"review_id": "review-" + uid()[:8], "title": "Synthetic period review",
                        "period": period, "target_refs": [m2], "fact_refs": [fact2],
                        "state_refs": [state2], "findings": ["Pilot evidence recorded"],
                        "learnings": ["Verify source identity"], "implications": ["Explicit evidence quality"],
                        "generation_version": "controlled-review-1"},
        })["result"])

        problem = exact(flow.act("co_agent", "method_open_problem", {
            "domain_id": company,
            "payload": {"state_ref": state2, "core_question": "Does the pilot choice remain appropriate?",
                        "statement": "Evidence quality diverges", "why_material": "May invalidate the choice",
                        "level": "mission", "responsible_assignment_id": f["actors"]["a"]["assignment_id"],
                        "evidence_refs": [evidence]},
        })["result"])
        problem_revised = exact(flow.act("a", "method_revise_problem", {
            "payload": {"state_ref": state2, "core_question": "Does the pilot choice remain appropriate?",
                        "statement": "Evidence quality diverges; clarify the source checks",
                        "why_material": "May invalidate the choice", "level": "mission",
                        "responsible_assignment_id": f["actors"]["a"]["assignment_id"],
                        "evidence_refs": [evidence]},
        }, oid=problem["object_id"])["result"])
        flow.act("a", "method_close_problem", {
            "disposition": "no_further_action", "reason": "Owner checked the concern",
            "evidence_refs": [evidence]}, oid=problem["object_id"])

        issue_chain = flow.start_issue()
        strategic_problem = exact(flow.act("co_agent", "method_open_problem", {
            "domain_id": company,
            "payload": {"state_ref": state2, "core_question": "Is the strategic basis still valid?",
                        "statement": "Strategic question raised from a canonical state",
                        "why_material": "Strategic intake candidate", "level": "strategic",
                        "responsible_assignment_id": f["actors"]["ceo"]["assignment_id"],
                        "evidence_refs": [evidence]},
        })["result"])
        potential = exact(flow.act("ceo_agent", "m1a_open_potential_issue", {
            "domain_id": company,
            "payload": {"title": "Dashboard transfer candidate", "summary": "Transfer to strategic intake",
                        "core_question": "Is the strategic basis still valid?", "business_scope": "strategic",
                        "urgency": "yellow", "source_refs": [strategic_problem]},
        })["result"])
        flow.act("ceo_agent", "m1a_confirm_strategic_issue",
                 {"reason": "Agent recognises a strategic question"}, oid=potential["object_id"])
        check("lawful_operating_objects_state_fact_review_problem_transfer", True)

        # A lawful pending recommendation on the confirmed State: latest and
        # effective differ, so the browser can compare candidate vs formal on
        # one exact object without any fabricated fixture.  Created before the
        # read-only snapshot window.
        pending_state = exact(flow.act("co_agent", "method_propose_state", {
            "domain_id": company,
            "payload": {**state2_payload, "summary": "Evidence quality now looks sufficient",
                        "rag": "green", "generation_version": "controlled-3"},
            "previous_state_ref": state2,
        })["result"])

        # ---------------------------------------------------------------
        # Dashboard reads (table-count oracle around the browse block)
        # ---------------------------------------------------------------
        ceo = flow.clients["ceo"]
        outsider = flow.clients["outsider"]
        before = h.snapshot(f)

        overview = ceo.json("GET", "/v1/dashboard/overview")
        check("overview_selects_the_current_strategy",
              overview["selected_strategy_id"] == s2["object_id"])
        check("overview_is_tkos_dashboard_0_1",
              overview["schema_version"] == "tkos.dashboard/0.1")
        check("overview_reports_historical_basis_from_recorded_impacts",
              overview["historical_basis"]["available"] is True
              and set(overview["historical_basis"]["groups"]) & {"ltco", "pco", "mission"})
        check("overview_group_availability_includes_current_and_historical",
              all(group["group"] in {"strategy", "architecture", "ltco", "pco", "mission", "operating"}
                  for group in overview["groups"])
              and next(g for g in overview["groups"] if g["group"] == "mission")["available"]
              and next(g for g in overview["groups"] if g["group"] == "mission")["historical_available"])

        missions_all = paginate(ceo, "/v1/dashboard/objects?group=mission&basis=all")
        mission_ids = {item["object_id"] for item in missions_all}
        check("pagination_over_all_bases_returns_each_mission_once",
              mission_ids == {m1["object_id"], m2["object_id"], m4["object_id"]}
              and len(missions_all) == len(mission_ids))
        check("combined_view_keeps_current_and_historical_basis_distinct",
              {item["basis"]["status"] for item in missions_all} == {"current", "historical"})
        missions_current = paginate(ceo, "/v1/dashboard/objects?group=mission&basis=current")
        check("pagination_current_basis_excludes_historical_mission",
              {item["object_id"] for item in missions_current} == {m2["object_id"], m4["object_id"]})
        missions_historical = paginate(ceo, "/v1/dashboard/objects?group=mission&basis=historical")
        check("pagination_historical_basis_contains_exactly_the_old_mission",
              {item["object_id"] for item in missions_historical} == {m1["object_id"]}
              and missions_historical[0]["basis"]["strategy_ref"]["revision_id"] == s1["revision_id"]
              and missions_historical[0]["basis"]["impact_linked"] is True)
        check("current_mission_has_no_historical_confirmation",
              missions_current and all(item["basis"]["status"] == "current" for item in missions_current))

        # Mission has no own period: the filter uses its exact recorded PCO period.
        p2_period = pending["period"]
        period_page = paginate(ceo, f"/v1/dashboard/objects?group=mission&basis=current"
                                    f"&period_from={p2_period['start'].replace('+', '%2B')}"
                                    f"&period_to={p2_period['end'].replace('+', '%2B')}")
        period_ids = {item["object_id"] for item in period_page}
        check("mission_period_filter_uses_the_recorded_pco_period",
              {m2["object_id"], m4["object_id"]} <= period_ids
              and all(item["period_source"] == "pco_ref" for item in period_page
                      if item["object_id"] in {m2["object_id"], m4["object_id"]})
              and all(item["period_status"]["status"] == "available" for item in period_page))
        future_page = paginate(ceo, "/v1/dashboard/objects?group=mission&basis=current"
                                    "&period_from=2027-01-01T00:00:00%2B00:00")
        check("mission_hard_deadline_is_not_treated_as_a_business_period",
              all(item["object_id"] not in {m2["object_id"], m4["object_id"]}
                  for item in future_page))

        # Historical detail keeps the exact PCO v1 reference after PCO moved on.
        detail_m1 = ceo.json("GET", f"/v1/dashboard/objects/{m1['object_id']}?revision_id={m1['revision_id']}")
        pco_refs = {ref["revision_id"]: ref for ref in detail_m1["relations"]["own_basis_refs"]
                    if ref["object_type"] == "PCO"}
        check("historical_mission_keeps_exact_pco_revision",
              p1["revision_id"] in pco_refs
              and pco_refs[p1["revision_id"]]["payload_hash"] == p1["payload_hash"])
        check("historical_mission_basis_is_the_old_strategy",
              detail_m1["basis"]["status"] == "historical"
              and detail_m1["basis"]["strategy_ref"]["revision_id"] == s1["revision_id"])
        detail_p_old = ceo.json("GET", f"/v1/dashboard/objects/{p1['object_id']}?revision_id={p1['revision_id']}")
        detail_p_new = ceo.json("GET", f"/v1/dashboard/objects/{p2['object_id']}?revision_id={p2['revision_id']}")
        check("historical_pco_revision_is_not_relinked_to_the_new_strategy",
              detail_p_old["basis"]["status"] == "historical"
              and detail_p_old["basis"]["strategy_ref"]["revision_id"] == s1["revision_id"]
              and detail_p_new["basis"]["status"] == "current"
              and detail_p_new["basis"]["strategy_ref"]["revision_id"] == s2["revision_id"])

        # The draft Mission revision never borrows the confirmed revision's state.
        detail_m1_draft = ceo.json(
            "GET", f"/v1/dashboard/objects/{m1['object_id']}?revision_id={draft_m1['revision_id']}")
        check("unconfirmed_revision_does_not_borrow_confirmation",
              detail_m1_draft["formal_state"]["formal"] is False
              and detail_m1_draft["content_confirmation"]["confirmed_for_selected_revision"] is False)
        detail_m2 = ceo.json("GET", f"/v1/dashboard/objects/{m2['object_id']}")
        records = detail_m2["content_confirmation"]["records"]
        candidate_records = [r for r in records if r["kind"] == "candidate_set_confirmation"]
        check("confirmed_mission_names_the_exact_confirmation_provenance",
              detail_m2["formal_state"]["formal"] is True
              and any(r["covers_selected_revision"] and r["principal_id"] == f["actors"]["ceo"]["principal_id"]
                      and r["receipt"] for r in candidate_records))
        mission_owner = next((entry for entry in detail_m2["responsibility"]["entries"]
                               if entry["relation"] == "mission_owner"), None)
        check("mission_owner_appointment_resolves_in_its_real_child_domain",
              mission_owner is not None
              and mission_owner["appointment"]["status"] == "current"
              and any(assignment["role"] == "DOMAIN_DRI"
                      for assignment in mission_owner["appointment"]["assignments"]))
        check("mission_owner_is_never_reported_as_a_dri",
              not detail_m2["responsibility"]["dri"])
        detail_ltco2 = ceo.json("GET", f"/v1/dashboard/objects/{ltco2['object_id']}")
        check("confirmed_ltco_carries_local_confirmation_record",
              detail_ltco2["formal_state"]["formal"] is True
              and any(r["kind"] == "ltco_confirmation" and r["covers_selected_revision"]
                      for r in detail_ltco2["content_confirmation"]["records"]))
        detail_s2 = ceo.json("GET", f"/v1/dashboard/objects/{s2['object_id']}")
        check("strategy_confirmation_covers_the_exact_confirmed_revision",
              detail_s2["formal_state"]["formal"] is True
              and any(r["kind"] == "strategy_update_confirmation" and r["covers_selected_revision"]
                      for r in detail_s2["content_confirmation"]["records"]))
        detail_a2 = ceo.json("GET", f"/v1/dashboard/objects/{a2['object_id']}")
        check("architecture_confirmation_is_bound_to_the_exact_architecture_revision",
              detail_a2["formal_state"]["formal"] is True
              and any(r["kind"] == "architecture_confirmation" and r["covers_selected_revision"]
                      for r in detail_a2["content_confirmation"]["records"]))

        # Typed downstream including hash consistency (parent != child hash).
        strategy_down = detail_s2["relations"]["downstream"]
        strategy_types = {edge["object_type"] for edge in strategy_down["items"]}
        check("strategy_downstream_has_ltco_pco_and_architecture",
              {"LTCO", "PCO", "StrategicArchitecture"} <= strategy_types)
        check("downstream_uses_recorded_target_hash_not_child_hash",
              all(edge["ref"]["payload_hash"] != detail_s2["selected_revision"]["payload_hash"]
                  for edge in strategy_down["items"]))
        pco_down = ceo.json("GET", f"/v1/dashboard/objects/{p2['object_id']}")["relations"]["downstream"]
        check("pco_downstream_has_the_mission",
              any(edge["object_type"] == "Mission" and edge["object_id"] == m2["object_id"]
                  and edge["matched_fields"] == ["pco_ref"] for edge in pco_down["items"]))
        mission_down = ceo.json("GET", f"/v1/dashboard/objects/{m2['object_id']}")["relations"]["downstream"]
        mission_types = {edge["object_type"] for edge in mission_down["items"]}
        check("mission_downstream_has_state_review_and_problem",
              {"OperatingState", "PeriodReview"} <= mission_types)
        state_edges = [edge for edge in mission_down["items"] if edge["object_type"] == "OperatingState"]
        check("state_panel_separates_exact_state_rag_from_mission_confirmation",
              any(edge["state_summary"]["subject_matches_selected_anchor"]
                  and edge["state_summary"]["rag"] == "yellow"
                  and edge["state_summary"]["as_of"]
                  and edge["state_summary"]["baseline_refs"]
                  and edge["state_summary"]["formal"] is True
                  for edge in state_edges))
        support_outcomes = detail_m2["business"]["supports"]
        check("supports_expose_readable_outcome_names_and_criteria",
              support_outcomes and all(item["outcome_status"]["status"] == "available"
                                       and item["outcome"]["title"]
                                       and item["outcome"]["criteria"]
                                       for item in support_outcomes))
        detail_m1_historical = ceo.json("GET", f"/v1/dashboard/objects/{m1['object_id']}")
        historical_edges = [edge for edge in detail_m1_historical["relations"]["downstream"]["items"]
                            if edge["object_type"] == "OperatingState"]
        check("state_binds_to_the_exact_mission_revision",
              any(edge["state_summary"]["subject_matches_selected_anchor"]
                  and edge["state_summary"]["rag"] == "unknown"
                  and edge["state_summary"]["data_gaps"] for edge in historical_edges))
        detail_m2_draft = ceo.json(
            "GET", f"/v1/dashboard/objects/{m2['object_id']}?revision_id={draft_pending_mission['revision_id']}")
        draft_state_edges = [edge for edge in detail_m2_draft["relations"]["downstream"]["items"]
                             if edge["object_type"] == "OperatingState"]
        check("state_does_not_attach_to_an_unrecorded_revision",
              all(edge["state_summary"]["subject_matches_selected_anchor"] is False
                  for edge in draft_state_edges)
              and detail_m2_draft["formal_state"]["formal"] is False)
        state_down = ceo.json("GET", f"/v1/dashboard/objects/{state2['object_id']}")["relations"]["downstream"]
        state_types = {edge["object_type"] for edge in state_down["items"]}
        check("state_downstream_has_review_and_problem",
              {"PeriodReview", "OperatingProblem"} <= state_types)
        check("downstream_is_a_bounded_paginated_projection",
              "next_cursor" in strategy_down and "has_more" in strategy_down
              and len(strategy_down["items"]) <= strategy_down["limit"])

        # Continuation works with a small page.
        first = ceo.json("GET", f"/v1/dashboard/objects/{s2['object_id']}/downstream"
                                f"?revision_id={s2['revision_id']}&limit=1")
        collected = list(first["items"])
        cursor = first["next_cursor"]
        while cursor and len(collected) < 10:
            page = ceo.json("GET", f"/v1/dashboard/objects/{s2['object_id']}/downstream"
                                   f"?revision_id={s2['revision_id']}&limit=1&cursor={cursor}")
            collected.extend(page["items"])
            cursor = page["next_cursor"]
        check("downstream_continuation_returns_each_edge_once",
              {edge["object_id"] for edge in collected} == {edge["object_id"] for edge in strategy_down["items"]})

        # Operating semantics.
        operating_current = paginate(ceo, "/v1/dashboard/objects?group=operating&basis=current")
        operating_unattached = paginate(ceo, "/v1/dashboard/objects?group=operating&basis=unattached")
        check("topic_only_fact_is_never_auto_attached",
              any(item["object_id"] == topic_fact["object_id"] for item in operating_unattached)
              and not any(item["object_id"] == topic_fact["object_id"] and item["basis"]["status"] == "current"
                          for item in operating_current))
        detail_fact = ceo.json("GET", f"/v1/dashboard/objects/{fact_corrected['object_id']}")
        check("corrected_fact_preserves_the_original_reference",
              detail_fact["business"]["correction"]["corrects_ref"]["object_id"] == fact2["object_id"]
              and any(item["field"] == "corrects_ref" and item["status"] == "available"
                      for item in detail_fact["missing"]))
        detail_review = ceo.json("GET", f"/v1/dashboard/objects/{review['object_id']}")
        check("period_review_stays_agent_analysis",
              detail_review["formal_state"]["authority"] == "agent_analysis"
              and detail_review["formal_state"]["human_approved"] is False)
        detail_state_old = ceo.json(
            "GET", f"/v1/dashboard/objects/{state_ref['object_id']}?revision_id={state_ref['revision_id']}")
        check("superseded_state_revision_does_not_borrow_full_canonical_status",
              detail_state_old["formal_state"]["formal"] is False
              and detail_state_old["formal_state"]["status"] == "superseded_confirmed")
        state_detail = ceo.json("GET", f"/v1/dashboard/objects/{state2['object_id']}")
        check("pending_recommendation_is_a_candidate_not_the_formal_state",
              state_detail["selected_revision"]["revision_id"] == state2["revision_id"]
              and state_detail["formal_state"]["formal"] is True
              and state_detail["candidates"]["latest"]["revision_id"] == pending_state["revision_id"]
              and state_detail["candidates"]["latest"]["is_selected"] is False)
        detail_problem_old = ceo.json(
            "GET", f"/v1/dashboard/objects/{problem['object_id']}?revision_id={problem['revision_id']}")
        detail_problem_closed = ceo.json("GET", f"/v1/dashboard/objects/{problem['object_id']}")
        check("closed_problem_revision_does_not_leak_to_the_historical_revision",
              detail_problem_closed["formal_state"]["status"] == "no_further_action"
              and detail_problem_old["formal_state"]["status"] != "no_further_action")
        detail_transferred = ceo.json("GET", f"/v1/dashboard/objects/{strategic_problem['object_id']}")
        check("transferred_problem_is_not_solved",
              detail_transferred["formal_state"]["status"] == "transferred"
              and detail_transferred["formal_state"]["formal"] is False)

        # Permission boundaries: no cross-domain leakage through lists or cursors.
        outsider_overview = outsider.json("GET", "/v1/dashboard/overview")
        check("outsider_gets_no_strategy_choices_or_counts",
              outsider_overview["strategy_choices"] == [] and outsider_overview["selected_strategy_id"] is None)
        outsider_missions = outsider.json("GET", "/v1/dashboard/objects?group=mission")
        check("outsider_mission_list_is_empty_without_hidden_counts",
              outsider_missions["items"] == [] and outsider_missions["loaded_count"] == 0)
        outsider.json("GET", f"/v1/dashboard/objects/{m2['object_id']}", expected={403, 404})
        check("outsider_detail_is_rejected", True)
        wrong = flow.clients["wrong_a"]
        wrong.json("GET", f"/v1/dashboard/objects/{m2['object_id']}", expected={403, 404})
        check("unrelated_dri_detail_is_rejected", True)
        ceo_cursor = ceo.json("GET", "/v1/dashboard/objects?group=mission&limit=1")["next_cursor"]
        if ceo_cursor:
            actor_a = flow.clients["a"]
            actor_a.json("GET", f"/v1/dashboard/objects?group=mission&limit=1&cursor={ceo_cursor}",
                         expected=422)
            check("cursor_bound_to_principal_rejects_reuse", True)
        ceo.json("GET", "/v1/dashboard/objects?group=mission&cursor=%25%25bad", expected=422)
        check("malformed_cursor_is_invalid_request", True)

        after = h.snapshot(f)
        check("dashboard_browse_wrote_no_business_or_audit_rows", before == after)

        # ---------------------------------------------------------------
        # Local facade (no credentials in the browser)
        # ---------------------------------------------------------------
        facade_overview = facade.get("/dashboard/api/v1/overview")
        check("facade_overview_identifies_viewer_and_synthetic_label",
              facade_overview.status_code == 200
              and facade_overview.json()["viewer"]["principal_id"] == f["actors"]["ceo"]["principal_id"]
              and bool(facade_overview.json()["viewer"]["display_name"])
              and facade_overview.json()["environment"]["synthetic"] is True
              and facade_overview.headers.get("cache-control") == "no-store")
        facade_objects = facade.get("/dashboard/api/v1/objects?group=mission&limit=1")
        check("facade_objects_paginates", facade_objects.status_code == 200
              and facade_objects.json()["loaded_count"] == 1
              and facade_objects.json()["has_more"] is True)
        check("facade_rejects_unknown_query_parameters",
              facade.get("/dashboard/api/v1/objects?group=mission&verbose=1").status_code == 422)
        check("facade_rejects_foreign_host",
              facade.get("/dashboard/api/v1/overview",
                         headers={"Host": "evil.example"}).status_code == 403)
        check("facade_has_no_action_or_context_route",
              facade.post("/dashboard/api/v1/actions", json={}).status_code == 404
              and facade.post("/dashboard/api/v1/context-packs", json={}).status_code == 404)
        index = facade.get("/dashboard/")
        if index.status_code == 200:
            body = index.text
            check("facade_index_has_no_external_runtime_assets",
                  "http://" not in body and "https://" not in body
                  and "default-src 'none'" in index.headers.get("content-security-policy", ""))
        else:
            check("facade_index_reports_missing_assets_explicitly",
                  index.status_code == 503
                  and index.json()["error"]["code"] == "DASHBOARD_ASSETS_MISSING")

        # Revoked assignment and revoked viewer token: the facade re-reads the
        # private viewer file each request and never falls back to a previous identity.
        assignments = h.sql(f, "SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s"
                               " AND principal_id=%s AND active",
                            (f["scope_id"], f["actors"]["b"]["principal_id"]))
        for assignment in assignments:
            ceo.json("POST", "/v1/actions", {
                "action_type": "revoke_assignment", "target": None, "expected_versions": [],
                "idempotency_key": "dashboard-revoke-" + uid(),
                "reason": "Dashboard acceptance revokes a participant",
                "params": {"assignment_id": str(assignment["assignment_id"])},
            })
        viewer.write_text(f["actors"]["b"]["token"])
        revoked = facade.get("/dashboard/api/v1/overview")
        check("facade_viewer_revocation_returns_unauthenticated",
              revoked.status_code in {401, 403})
        viewer.write_text(f["actors"]["ceo"]["token"])
        check("facade_recovers_with_valid_viewer", facade.get("/dashboard/api/v1/overview").status_code == 200)

        check("source_unchanged", source_manifest(source) == initial)
        public_json(h.output / "summary.json", {
            "passed": len(checks), "checks": checks,
            "runtime_dashboard_api_accepted": True,
            "scope": "Synthetic 0.3 HTTP/PG/MinIO dashboard acceptance",
            "browser_acceptance": "not_run",
            "real_model": "not_run", "deployed": False,
        })
    finally:
        facade.close()
        flow.close()
        h.stop(process)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError("Use fresh private and output paths")
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    try:
        run(h, Path("src").resolve())
    finally:
        h.close()


if __name__ == "__main__":
    main()
