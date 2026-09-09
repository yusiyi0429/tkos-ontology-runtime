"""Independent workbench HTTP gates against an already running local Scenario.

Only run_gates(s) is public. Infrastructure, bootstrap identities and a fresh
scope belong to the caller. Successful business state is created only by HTTP.
Fixture SQL changes authority only; credentials are never queried or printed.
The Scenario Worker must remain stopped while durable-read fingerprints run.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
from urllib.parse import urlencode, urlsplit
import uuid

from psycopg.types.json import Jsonb

from acceptance.runtime.client import assert_error, sha256, utc_now


def run_gates(s):
    """Run ten fail-fast groups; return sanitized evidence, never acceptance flags."""
    address = urlsplit(s.api_url)
    assert address.scheme == "http" and address.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not address.username and not address.password and not address.query and not address.fragment
    assert s.f["tenant_id"].startswith(("runtime-acceptance-", "tkos-runtime-acceptance-"))
    assert s.worker is None or s.worker.poll() is not None, "Stop only this Scenario Worker before running gates"
    scope, domain = s.f["scope_id"], s.domain
    actors, h, ceo = s.f["actors"], s.h, s.ceo
    reports = {}
    suffix = uuid.uuid4().hex[:8]

    def get(client, path, params=None, *, expected=200):
        query = "?" + urlencode(params) if params else ""
        response = client.request("GET", path + query, expected=expected)
        if expected == 200:
            assert response.headers.get("cache-control") == "no-store", f"Missing no-store: {path}"
        return response.json()

    def pages(client, path, params=None, *, limit=1):
        result, cursors = [], []
        for _ in range(500):
            values = dict(params or {}, limit=limit)
            if cursors:
                values["cursor"] = cursors[-1]
            page = get(client, path, values)
            assert set(page) >= {"items", "next_cursor"} and isinstance(page["items"], list)
            assert len(page["items"]) <= limit
            result.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                return result, cursors
            assert page["items"], "Empty visible page may not expose a hidden-row cursor"
            assert isinstance(cursor, str) and cursor not in cursors and len(cursor) <= 4096
            cursors.append(cursor)
        raise AssertionError("Pagination did not terminate within the isolated fixture bound")

    def commit(client, kind, params, oid=None, *, extra=()):
        command = client.command(kind, deepcopy(params), target=s.target(oid) if oid else None)
        prepared = client.json("POST", "/v1/actions/prepare", command)
        command["expected_versions"] = prepared["expected_versions"]
        seen = {row["object_id"] for row in command["expected_versions"]}
        command["expected_versions"].extend(s.deps(*(oid for oid in extra if oid not in seen)))
        receipt = client.json("POST", "/v1/actions", command)
        s.receipts.append(receipt)
        return receipt

    def upload(label, content):
        result = s.mission.json("POST", "/v1/evidence-assets", {
            "domain_id": domain, "title": label,
            "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain",
        })
        raw = s.verifier.request("GET", f"/v1/evidence-assets/{result['object_id']}/revisions/{result['revision_id']}")
        assert raw.content == content and result["sha256"] == sha256(content)
        return result

    def targets(body):
        return {(item["target_ref"]["object_id"], item["target_ref"]["revision_id"]) for item in body["items"]}

    def relation(oid, rid=None):
        return get(ceo, f"/v1/objects/{oid}/relations", {"limit": 100, **({"revision_id": rid} if rid else {})})

    def err(client, path, code="INVALID_REQUEST", *, status=422):
        result = get(client, path, expected=status)
        assert_error(result, code)
        return result

    def epoch():
        s.sql("UPDATE gov_scopes SET auth_epoch=auth_epoch+1 WHERE scope_id=%s RETURNING auth_epoch", (scope,))

    def authority_assignment(principal, role, target_domain):
        assignment_id = str(uuid.uuid4())
        s.sql("""INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role)
                 VALUES(%s,%s,%s,%s,%s) RETURNING assignment_id""",
              (assignment_id, scope, principal, target_domain, role))
        epoch()
        return assignment_id

    def set_policy(target_domain, content):
        s.sql("""INSERT INTO gov_activation_policies
                 (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                 SELECT %s,scope_id,domain_id,policy_id,policy_seq+1,%s,%s
                 FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s
                 ORDER BY policy_seq DESC LIMIT 1 RETURNING policy_revision_id""",
              (str(uuid.uuid4()), Jsonb(content), actors["ceo"]["principal_id"], scope, target_domain))
        epoch()

    with h.group("workbench_independent_01_authenticated_type_and_domain_catalog") as report:
        catalog = get(ceo, "/v1/object-types")
        expected_types = {"CompanyOutcome", "BusinessCommitment", "ExecutionCommitment", "FeedbackThread",
                          "ManagementAdjustment", "Decision", "MetricObservation", "WorkItem", "EvidenceAsset", "Deliverable"}
        assert catalog["schema_version"] and {item["object_type"] for item in catalog["items"]} == expected_types
        assert len(catalog["items"]) == len(expected_types)
        for item in catalog["items"]:
            assert all(key in item for key in ("label", "creation_mode", "description", "payload_schema"))
            assert item["creation_mode"] == ("dedicated_action" if item["object_type"] in {"EvidenceAsset", "Deliverable"} else "generic_action")
        for path in ("/v1/object-types", "/v1/domains"):
            response = ceo.request("GET", path, headers={"Authorization": ""}, expected=401)
            assert_error(response.json(), "UNAUTHENTICATED")
        visible, _ = pages(ceo, "/v1/domains")
        assert [row["domain_id"] for row in visible] == [domain]
        assert all(set(row) == {"domain_id", "name"} for row in visible)
        second = s.sql("SELECT domain_id::text FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                       (scope, actors["outsider"]["assignment_id"]))[0]["domain_id"]
        hidden = get(ceo, "/v1/domains")
        assert second not in json.dumps(hidden) and "total" not in hidden
        err(ceo, f"/v1/objects?domain_id={second}", "NOT_FOUND", status=404)
        err(ceo, f"/v1/objects?domain_id={uuid.uuid4()}", "NOT_FOUND", status=404)
        report.update(types=sorted(expected_types), initially_visible_domains=[domain], hidden_domain_not_disclosed=True)
        reports["catalog"] = report

    with h.group("workbench_independent_02_live_delivery_v1_return_v2") as report:
        outcome = s.create("CompanyOutcome", {"title": f"Workbench independent Outcome {suffix}", "terms": {"target": 80}})
        s.act(ceo, "confirm_outcome", {}, oid=outcome)
        bc, signatures = s.signed("BusinessCommitment", outcome)
        s.activate(bc, signatures, outcome)
        ec, signatures = s.signed("ExecutionCommitment", bc)
        s.activate(ec, signatures, bc)
        feedback = s.feedback_investigation(f"Workbench feedback {suffix}")
        work = s.create("WorkItem", {
            "title": f"Workbench delivery {suffix}", "execution_commitment_ref": s.upstream(ec),
            "dri_assignment_id": actors["mission_dri"]["assignment_id"],
            "acceptor_assignment_id": actors["verifier"]["assignment_id"],
            "acceptance_criteria": [{"criterion_id": "source", "description": "Source evidence is complete and independently checked"}],
            "feedback_ref": s.upstream(feedback),
        })
        baseline = s.object(work)["latest_revision_id"]
        commit(s.mission, "accept_work_item", {}, work)
        evidence1 = upload(f"Synthetic original v1 {suffix}", b"Synthetic v1: source appendix missing.\n")
        submission1 = commit(s.mission, "submit_deliverable", {
            "title": "Synthetic deliverable v1", "summary": "First submission with a missing source appendix",
            "evidence_revision_ids": [evidence1["revision_id"]],
        }, work)
        delivered = submission1["result"]["deliverable_object_id"]
        r1 = submission1["result"]["deliverable_revision_id"]

        def review_params(submission, accepted):
            return {"deliverable_revision_id": submission["result"]["deliverable_revision_id"],
                    "delivery_payload_hash": submission["result"]["payload_hash"],
                    "verification_result": "accepted" if accepted else "changes_requested",
                    "criterion_results": [{"criterion_id": "source", "result": "passed" if accepted else "failed",
                                           "note": "Source complete" if accepted else "Source appendix missing"}],
                    "review_note": "Independent review of the exact frozen submission"}

        returned = commit(s.verifier, "review_deliverable", review_params(submission1, False), work)
        before_v2 = utc_now()
        old_context = s.context(s.verifier, [delivered, outcome, feedback], before_v2, before_v2)
        evidence2 = upload(f"Synthetic original v2 {suffix}", b"Synthetic v2: source appendix included and checked.\n")
        submission2 = commit(s.mission, "submit_deliverable", {
            "title": "Synthetic deliverable v2", "summary": "Supplemented source appendix in a new immutable version",
            "evidence_revision_ids": [evidence2["revision_id"]],
            "responds_to_acceptance_id": returned["result"]["acceptance_id"],
        }, work)
        accepted = commit(s.verifier, "review_deliverable", review_params(submission2, True), work)
        r2 = submission2["result"]["deliverable_revision_id"]
        state = s.object(work)
        assert r1 != r2 and state["latest_revision_id"] == baseline and state["lifecycle_status"] == "delivery_accepted"
        assert s.object(outcome)["outcome_achievement"] == "not_assessed"
        assert s.object(feedback)["lifecycle_status"] == "investigating"
        assert returned["status"] == accepted["status"] == "committed"
        report.update(work_item_id=work, baseline_revision_id=baseline, deliverable_id=delivered,
                      submission_revisions=[r1, r2], return_receipt=returned["receipt_id"],
                      accepted_receipt=accepted["receipt_id"], delivery="delivery_accepted",
                      outcome="not_assessed", feedback="investigating")
        reports["delivery"] = report

    with h.group("workbench_independent_03_object_and_revision_pagination") as report:
        objects, object_cursors = pages(ceo, "/v1/objects", {"domain_id": domain})
        expected = s.sql("SELECT object_id::text FROM gov_objects WHERE scope_id=%s AND domain_id=%s", (scope, domain))
        assert len(objects) == len({row["object_id"] for row in objects}) == len(expected)
        assert {row["object_id"] for row in objects} == {row["object_id"] for row in expected}
        assert objects == pages(ceo, "/v1/objects", {"domain_id": domain}, limit=100)[0]
        summaries = {row["object_id"]: row for row in objects}
        required = {"object_id", "domain_id", "object_type", "title", "lifecycle_status", "object_version",
                    "latest_revision_id", "effective_revision_id", "created_at"}
        assert all(set(row) == required for row in objects), "Object discovery must remain a minimal summary"
        types, _ = pages(ceo, "/v1/objects", {"domain_id": domain, "object_type": "WorkItem"})
        assert [row["object_id"] for row in types] == [work]
        empty = get(ceo, "/v1/objects", {"domain_id": domain, "object_type": "ManagementAdjustment", "limit": 1})
        assert empty["items"] == [] and empty["next_cursor"] is None
        revisions, revision_cursors = pages(ceo, f"/v1/objects/{delivered}/revisions")
        sql_revisions = s.sql("SELECT revision_id::text,object_version FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s",
                              (scope, delivered))
        assert {row["revision_id"] for row in revisions} == {r1, r2}
        assert len(revisions) == 2 and revisions == pages(ceo, f"/v1/objects/{delivered}/revisions", limit=100)[0]
        assert {row["revision_id"]: row["object_version"] for row in revisions} == {row["revision_id"]: row["object_version"] for row in sql_revisions}
        assert [row["revision_id"] for row in revisions if row["is_latest"]] == [r2]
        effective_delivery = s.object(delivered)["effective_revision_id"]
        assert all(row["is_effective"] == (row["revision_id"] == effective_delivery) for row in revisions)
        assert all(set(row) == {"revision_id", "object_id", "object_version", "payload_hash", "recorded_at",
                                "valid_from", "valid_to", "is_latest", "is_effective"} for row in revisions)
        baseline_rows, _ = pages(ceo, f"/v1/objects/{work}/revisions")
        assert len(baseline_rows) == 1 and baseline_rows[0]["object_version"] < summaries[work]["object_version"]
        assert baseline_rows[0]["revision_id"] == baseline and baseline_rows[0]["is_latest"]
        # A later candidate changes list title, never the confirmed effective revision.
        effective_outcome = s.object(outcome)["effective_revision_id"]
        s.act(ceo, "propose_revision", {"payload": {"title": "Unconfirmed latest candidate", "terms": {"target": 90}, "upstream_refs": []}}, oid=outcome, prepare=True)
        candidate = s.object(outcome)["latest_revision_id"]
        refreshed, _ = pages(ceo, "/v1/objects", {"domain_id": domain, "object_type": "CompanyOutcome"})
        listed = next(row for row in refreshed if row["object_id"] == outcome)
        assert listed["title"] == "Unconfirmed latest candidate" and listed["latest_revision_id"] == candidate
        assert listed["effective_revision_id"] == effective_outcome != candidate
        report.update(object_count=len(objects), revision_ids=[r1, r2], state_and_revision_versions_distinct=True,
                      latest_candidate_id=candidate, effective_outcome_revision=effective_outcome)
        reports["pagination"] = report

    with h.group("workbench_independent_04_exact_single_hop_relation_provenance") as report:
        first, second_version = relation(delivered, r1), relation(delivered, r2)
        first_expected = {(work, baseline), (ec, s.object(ec)["effective_revision_id"]),
                          (evidence1["object_id"], evidence1["revision_id"])}
        second_expected = {(work, baseline), (ec, s.object(ec)["effective_revision_id"]),
                           (evidence2["object_id"], evidence2["revision_id"])}
        assert targets(first) == first_expected and len(first["items"]) == len(first_expected)
        assert targets(second_version) == second_expected and len(second_version["items"]) == len(second_expected)
        assert first["source_ref"] == {"object_id": delivered, "revision_id": r1}
        assert relation(delivered)["source_ref"] == {"object_id": delivered, "revision_id": r2}
        assert all(edge["relation_type"] == "source_reference" and edge["source_ref"] == first["source_ref"] for edge in first["items"])
        assert bc not in {oid for oid, _ in targets(first)}, "One hop may not include the EC's BC source"
        paged, relation_cursors = pages(ceo, f"/v1/objects/{delivered}/relations", {"revision_id": r1})
        assert paged == first["items"]
        fake_oid, fake_rid = str(uuid.uuid4()), str(uuid.uuid4())
        decoy = s.create("CompanyOutcome", {"title": "JSON is not an ontology edge", "terms": {
            "untyped_example": {"object_id": fake_oid, "revision_id": fake_rid}}, "upstream_refs": []})
        assert relation(decoy)["items"] == [], "Arbitrary nested JSON was interpreted as an edge"
        decision = s.decision(f"Synthetic adjustment provenance {suffix}")
        adjustment = s.create("ManagementAdjustment", {
            "title": "Typed adjustment references", "feedback_revision_id": s.object(feedback)["latest_revision_id"],
            "decision_revision_id": s.object(decision)["effective_revision_id"], "changes": [],
        })
        assert targets(relation(adjustment)) == {(feedback, s.object(feedback)["latest_revision_id"]),
                                                  (decision, s.object(decision)["effective_revision_id"])}
        bc_r1 = s.object(bc)["effective_revision_id"]
        bc_payload = deepcopy(ceo.revision(bc, bc_r1)["payload"])
        bc_payload["terms"]["target"] = 82
        s.act(ceo, "propose_revision", {"payload": bc_payload, "bundle_id": adjustment}, oid=bc, prepare=True)
        bc_r2 = s.object(bc)["latest_revision_id"]
        adjustment_payload = deepcopy(s.object(adjustment)["latest_revision"]["payload"])
        adjustment_payload["changes"] = [{"object_id": bc, "from_revision_id": bc_r1, "to_revision_id": bc_r2}]
        s.act(ceo, "propose_revision", {"payload": adjustment_payload}, oid=adjustment, prepare=True)
        adjustment_edges = targets(relation(adjustment))
        assert (bc, bc_r1) in adjustment_edges and (bc, bc_r2) in adjustment_edges
        assert len(adjustment_edges) == 4, "Distinct source revisions of one object were collapsed"
        err(ceo, f"/v1/objects/{delivered}/relations?revision_id={baseline}", "NOT_FOUND", status=404)
        report.update(v1_target_count=len(first["items"]), v2_target_count=len(second_version["items"]),
                      one_hop_only=True, arbitrary_json_ignored=True, typed_adjustment_refs_verified=True,
                      same_object_distinct_revisions_preserved=True)
        reports["relations"] = report

    with h.group("workbench_independent_05_strict_parameters_and_cursor_binding") as report:
        base = f"/v1/objects?domain_id={domain}"
        paths = [base + "&limit=0", base + "&limit=-1", base + "&limit=101", base + "&limit=1.5",
                 base + "&limit=true", base + "&limit=1&limit=2", base + "&object_type=Mission",
                 base + "&sort=updated_at", base + "&domain_id=" + domain,
                 "/v1/objects", "/v1/objects?domain_id=bad-uuid", base + "&cursor=not_a_cursor",
                 base + "&cursor=" + "x" * 4097, "/v1/object-types?principal_id=" + actors["ceo"]["principal_id"],
                 "/v1/domains?unknown=1", f"/v1/objects/{work}/responsibility?role=CEO",
                 f"/v1/objects/{delivered}/relations?revision_id={r1}&revision_id={r2}"]
        for path in paths:
            err(ceo, path)
        assert object_cursors and revision_cursors and relation_cursors
        for path, params in (
            ("/v1/domains", {"cursor": object_cursors[0]}),
            ("/v1/objects", {"domain_id": domain, "object_type": "WorkItem", "cursor": object_cursors[0]}),
            (f"/v1/objects/{work}/revisions", {"cursor": revision_cursors[0]}),
            (f"/v1/objects/{delivered}/relations", {"revision_id": r2, "cursor": relation_cursors[0]}),
        ):
            err(ceo, path + "?" + urlencode(params))
        err(s.verifier, "/v1/objects?" + urlencode({"domain_id": domain, "cursor": object_cursors[0]}))
        # Keep the original endpoint, principal, filters and limit. Only the
        # cursor envelope version changes; Python equality must not coerce it.
        original_cursor = object_cursors[0]
        cursor_body = json.loads(base64.urlsafe_b64decode(original_cursor + "=" * (-len(original_cursor) % 4)))
        assert type(cursor_body["v"]) is int and cursor_body["v"] == 1
        get(ceo, "/v1/objects", {"domain_id": domain, "limit": 1, "cursor": original_cursor})
        for invalid_version in (True, 1.0):
            malformed = deepcopy(cursor_body)
            malformed["v"] = invalid_version
            encoded = base64.urlsafe_b64encode(json.dumps(malformed, separators=(",", ":")).encode()).decode().rstrip("=")
            err(ceo, "/v1/objects?" + urlencode({"domain_id": domain, "limit": 1, "cursor": encoded}))
        report.update(invalid_query_cases=len(paths), cursor_rebinding_cases=5,
                      cursor_version_type_cases=2, all_standard_422=True)
        reports["validation"] = report

    with h.group("workbench_independent_06_receipt_summaries_and_current_policy") as report:
        receipts, receipt_cursors = pages(ceo, f"/v1/objects/{work}/action-receipts")
        assert receipt_cursors and len(receipts) == len({row["receipt_id"] for row in receipts})
        assert receipts == pages(ceo, f"/v1/objects/{work}/action-receipts", limit=100)[0]
        assert all(set(row) == {"receipt_id", "action_type", "actor_id", "status", "recorded_at"} and row["status"] == "committed" for row in receipts)
        receipt_ids = {row["receipt_id"] for row in receipts}
        assert {returned["receipt_id"], accepted["receipt_id"], submission1["receipt_id"], submission2["receipt_id"]} <= receipt_ids
        related_sql = s.sql("""SELECT receipt_id::text FROM gov_action_receipts
                            WHERE scope_id=%s AND (target_object_id=%s
                            OR EXISTS(SELECT 1 FROM jsonb_array_elements(object_versions) entry WHERE entry->>'object_id'=%s)
                            OR COALESCE(result->'referenced_object_ids','[]'::jsonb) ? %s)""", (scope, work, work, work))
        assert receipt_ids == {row["receipt_id"] for row in related_sql}
        # Current policy is independent of a still-active assignment.
        policy_a = s.sql("SELECT content FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1", (scope, domain))[0]["content"]
        restricted = deepcopy(policy_a)
        restricted["action_roles"]["read"] = [role for role in restricted["action_roles"]["read"] if role != "DOMAIN_DRI"]
        set_policy(domain, restricted)
        try:
            assert get(s.dri, "/v1/domains")["items"] == []
            err(s.dri, f"/v1/objects?domain_id={domain}", "NOT_FOUND", status=404)
            err(s.dri, f"/v1/objects/{work}/action-receipts", "NOT_FOUND", status=404)
        finally:
            set_policy(domain, policy_a)
        assert get(s.dri, "/v1/domains")["items"]
        report.update(receipt_count=len(receipts), all_committed=True, current_policy_overrides_active_assignment=True)
        reports["receipts"] = report

    with h.group("workbench_independent_07_minimal_responsibility_and_no_action_inference") as report:
        responsible = get(ceo, f"/v1/objects/{work}/responsibility")
        assert set(responsible) == {"object_id", "baseline_revision_id", "dri", "acceptor"}
        assert responsible["object_id"] == work and responsible["baseline_revision_id"] == baseline
        allowed = {"assignment_id", "principal_id", "display_name", "role", "domain_id", "current_assignment_active"}
        for field, actor in (("dri", "mission_dri"), ("acceptor", "verifier")):
            actual = responsible[field]
            assert set(actual) == allowed and actual["assignment_id"] == actors[actor]["assignment_id"]
            assert actual["principal_id"] == actors[actor]["principal_id"] and actual["domain_id"] == domain
            assert actual["display_name"] == "Synthetic " + actor and actual["current_assignment_active"] is True
        err(ceo, f"/v1/objects/{feedback}/responsibility")
        # Keep the named person's assignment active while removing review Action permission.
        no_review = deepcopy(policy_a)
        no_review["action_roles"]["review_deliverable"] = ["CEO", "DOMAIN_DRI"]
        set_policy(domain, no_review)
        try:
            assert get(ceo, f"/v1/objects/{work}/responsibility")["acceptor"]["current_assignment_active"] is True
            before = s.snapshot()
            command = s.verifier.command("review_deliverable", review_params(submission2, True), target=s.target(work))
            assert_error(s.verifier.json("POST", "/v1/actions", command, expected=403), "FORBIDDEN")
            assert s.snapshot() == before, "Rejected action created a receipt or changed business state"
        finally:
            set_policy(domain, policy_a)
        assignment = actors["verifier"]["assignment_id"]
        old_time = s.sql("SELECT valid_from,valid_to FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s", (scope, assignment))[0]
        for clause in ("valid_from=clock_timestamp()-interval '2 days',valid_to=clock_timestamp()-interval '1 day'",
                       "valid_from=clock_timestamp()+interval '1 day',valid_to=NULL"):
            s.sql(f"UPDATE gov_role_assignments SET {clause} WHERE scope_id=%s AND assignment_id=%s RETURNING assignment_id", (scope, assignment))
            epoch()
            try:
                assert get(ceo, f"/v1/objects/{work}/responsibility")["acceptor"]["current_assignment_active"] is False
            finally:
                s.sql("UPDATE gov_role_assignments SET valid_from=%s,valid_to=%s WHERE scope_id=%s AND assignment_id=%s RETURNING assignment_id",
                      (old_time["valid_from"], old_time["valid_to"], scope, assignment))
                epoch()
        s.sql("UPDATE gov_principals SET active=false WHERE scope_id=%s AND principal_id=%s RETURNING principal_id", (scope, actors["verifier"]["principal_id"]))
        epoch()
        try:
            assert get(ceo, f"/v1/objects/{work}/responsibility")["acceptor"]["current_assignment_active"] is False
        finally:
            s.sql("UPDATE gov_principals SET active=true WHERE scope_id=%s AND principal_id=%s RETURNING principal_id", (scope, actors["verifier"]["principal_id"]))
            epoch()
        report.update(exact_minimal_keys=sorted(allowed), assignment_not_action_permission=True,
                      expired_future_and_inactive_principal_checked=True)
        reports["responsibility"] = report

    with h.group("workbench_independent_08_historical_context_and_read_only_fingerprints") as report:
        now = utc_now()
        before = s.snapshot()
        historic = s.context(ceo, [delivered, outcome], before_v2, before_v2)
        after = s.snapshot()
        changed_tables = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
        assert changed_tables == {"gov_context_snapshots"}, "POST Context Pack changed non-snapshot durable tables"
        selected = {item["object_id"]: item for item in historic["selected"]}
        assert selected[delivered]["revision_id"] == r1
        assert selected[delivered]["delivery_status"] == "changes_requested"
        assert selected[outcome]["revision_id"] == effective_outcome
        for valid_at, known_at in ((before_v2, now), (now, before_v2)):
            bounded = s.context(ceo, [delivered], valid_at, known_at)
            item = bounded["selected"][0]
            if known_at == before_v2:
                assert item["revision_id"] == r1 and item["delivery_status"] == "changes_requested"
            assert item["delivery_status"] != "accepted", "Future review leaked through one time boundary"
        before_reads = s.snapshot()
        frozen = s.verifier.json("GET", f"/v1/context-packs/{old_context['context_snapshot_id']}")
        assert frozen["selected"] == old_context["selected"] and frozen["excluded"] == old_context["excluded"]
        get(ceo, "/v1/object-types")
        get(ceo, "/v1/domains")
        get(ceo, "/v1/objects", {"domain_id": domain})
        get(ceo, f"/v1/objects/{work}/revisions")
        get(ceo, f"/v1/objects/{delivered}/relations")
        get(ceo, f"/v1/objects/{work}/action-receipts")
        get(ceo, f"/v1/objects/{work}/responsibility")
        assert s.snapshot() == before_reads, "New GET or old snapshot read changed durable state"
        report.update(historical_submission=r1, historical_review="changes_requested",
                      explicit_snapshot_only_write=True, seven_gets_business_read_only=True)
        reports["context"] = report

    with h.group("workbench_independent_09_cross_domain_receipt_all_or_nothing") as report:
        authority_assignment(actors["ceo"]["principal_id"], "CEO", second)
        retained = authority_assignment(actors["verifier"]["principal_id"], "IC", second)
        b_receipt = commit(s.clients["outsider"], "create_object", {
            "object_type": "FeedbackThread", "domain_id": second,
            "payload": {"title": "Synthetic retained B domain object", "description": "Positive control after A read authority is revoked"},
        })
        b_object = b_receipt["result"]["object_id"]
        both_domains, domain_cursors = pages(s.verifier, "/v1/domains")
        assert {item["domain_id"] for item in both_domains} == {domain, second} and domain_cursors
        # A genuine A-domain Action may include an explicitly declared B CAS dependency.
        cross = commit(ceo, "create_object", {
            "object_type": "FeedbackThread", "domain_id": domain,
            "payload": {"title": "Synthetic A receipt with a B dependency", "description": "Cross-domain extra CAS dependency tests whole-receipt authorization"},
        }, extra=(b_object,))
        assert b_object in cross["result"]["referenced_object_ids"]
        visible_receipts, _ = pages(s.verifier, f"/v1/objects/{b_object}/action-receipts")
        assert {b_receipt["receipt_id"], cross["receipt_id"]} <= {item["receipt_id"] for item in visible_receipts}
        verifier_cursor = get(s.verifier, "/v1/objects", {"domain_id": domain, "limit": 1})["next_cursor"]
        assert verifier_cursor
        commit(ceo, "revoke_assignment", {"assignment_id": actors["verifier"]["assignment_id"]})
        assert s.verifier.object(b_object)["object_id"] == b_object
        retained_domains = get(s.verifier, "/v1/domains", {"limit": 1})
        assert retained_domains["items"] == [{"domain_id": second, "name": "Synthetic separate domain"}]
        assert retained_domains["next_cursor"] is None and domain not in json.dumps(retained_domains)
        filtered = get(s.verifier, f"/v1/objects/{b_object}/action-receipts", {"limit": 1})
        assert [item["receipt_id"] for item in filtered["items"]] == [b_receipt["receipt_id"]]
        assert filtered["next_cursor"] is None and cross["receipt_id"] not in json.dumps(filtered)
        assert_error(s.verifier.json("GET", f"/v1/action-receipts/{cross['receipt_id']}", expected=403), "FORBIDDEN")
        report.update(retained_domain_id=second, retained_assignment_id=retained, retained_object_id=b_object,
                      hidden_cross_domain_receipt_id=cross["receipt_id"], hidden_rows_do_not_create_next_cursor=True)
        reports["whole_receipt_authorization"] = report

    with h.group("workbench_independent_10_revoked_history_cursor_and_original_evidence") as report:
        before = s.snapshot()
        paths = [f"/v1/objects/{work}", f"/v1/objects/{work}/revisions",
                 f"/v1/objects/{delivered}/relations?revision_id={r1}",
                 f"/v1/objects/{work}/action-receipts", f"/v1/objects/{work}/responsibility",
                 f"/v1/context-packs/{old_context['context_snapshot_id']}",
                 f"/v1/evidence-assets/{evidence1['object_id']}/revisions/{evidence1['revision_id']}",
                 "/v1/objects?" + urlencode({"domain_id": domain, "cursor": verifier_cursor})]
        for path in paths:
            assert_error(s.verifier.json("GET", path, expected=404), "NOT_FOUND")
        assert s.snapshot() == before, "Denied historical reads changed durable state"
        responsible = get(ceo, f"/v1/objects/{work}/responsibility")
        assert responsible["acceptor"]["current_assignment_active"] is False
        for evidence, raw in ((evidence1, b"Synthetic v1: source appendix missing.\n"),
                              (evidence2, b"Synthetic v2: source appendix included and checked.\n")):
            downloaded = ceo.request("GET", f"/v1/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}")
            assert downloaded.content == raw and sha256(downloaded.content) == evidence["sha256"]
        # A different principal with no remaining assignments is a 403 control.
        commit(ceo, "revoke_assignment", {"assignment_id": actors["agent"]["assignment_id"]})
        for path in ("/v1/object-types", "/v1/domains", f"/v1/objects/{work}/responsibility"):
            assert_error(s.agent.json("GET", path, expected=403), "FORBIDDEN")
        report.update(revoked_read_paths=len(paths), original_evidence_versions_preserved=True,
                      no_assignment_403_control=True, current_b_domain_200_control=True)
        reports["revocation"] = report

    return {"groups": reports, "scope_id": scope, "work_item_id": work,
            "deliverable_id": delivered, "outcome_id": outcome, "feedback_id": feedback,
            "submissions": [r1, r2], "context_snapshot_id": old_context["context_snapshot_id"],
            "limits": ["Cross-domain hidden relation-target branch requires separate focused test: legal business reference writes enforce same_domain.",
                       "Fresh scope and stopped Worker required; this gate is not disaster-recovery or production acceptance."]}
