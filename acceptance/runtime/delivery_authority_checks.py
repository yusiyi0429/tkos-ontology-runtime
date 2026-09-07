"""Independent delivery identity and revocation checks in a disposable scope.

Only identities, policies and one initial Outcome are seeded. Successful BC/EC,
WorkItem, submission, review and revocation transitions use the running HTTP API.
No Worker is started; this group deliberately does not accept external effects.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from urllib.parse import urlsplit
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.runtime.client import Client, assert_error
from acceptance.runtime.sql_oracle import snapshot_scope


def run_delivery_authority_checks(scenario) -> dict:
    h = scenario.h
    parsed = urlsplit(scenario.api_url)
    assert parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    assert not any((parsed.username, parsed.password, parsed.query, parsed.fragment))
    assert scenario.worker is None or scenario.worker.poll() is not None
    clients = []
    with h.group("v02_05_distinct_humans_named_acceptor_and_revocation") as report:
        from memory_service_runtime.governed.bootstrap import seed_scope

        namespace = "runtime-acceptance-delivery-authority-" + uuid.uuid4().hex[:10]
        self_verifier_assignment, other_verifier_assignment, retained_assignment = (
            str(uuid.uuid4()) for _ in range(3)
        )
        try:
            with psycopg.connect(h.env["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
                fixture = seed_scope(conn, namespace, namespace + "-company")
                scope = fixture["scope_id"]
                domain = fixture["domain_id"]
                actors = fixture["actors"]
                with conn.transaction():
                    conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope,))
                    domain_b = str(conn.execute(
                        "SELECT domain_id FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                        (scope, actors["outsider"]["assignment_id"]),
                    ).fetchone()["domain_id"])
                    for aid, principal, did, role in (
                        (self_verifier_assignment, actors["mission_dri"]["principal_id"], domain, "VERIFIER"),
                        (other_verifier_assignment, actors["ic"]["principal_id"], domain, "VERIFIER"),
                        (retained_assignment, actors["verifier"]["principal_id"], domain_b, "IC"),
                    ):
                        conn.execute(
                            """INSERT INTO gov_role_assignments
                               (assignment_id,scope_id,principal_id,domain_id,role) VALUES(%s,%s,%s,%s,%s)""",
                            (aid, scope, principal, did, role),
                        )
                    policy = conn.execute(
                        "SELECT * FROM gov_activation_policies WHERE scope_id=%s AND policy_revision_id=%s",
                        (scope, fixture["policy_revision_id"]),
                    ).fetchone()
        except psycopg.Error as exc:
            raise AssertionError(f"Delivery authority provisioning failed (SQLSTATE {exc.sqlstate})") from None

        try:
            def actor(name):
                value = Client(scenario.api_url, actors[name]["token"], "delivery-authority-" + name, h.log)
                clients.append(value)
                return value

            by_name = {name: actor(name) for name in ("ceo", "domain_dri", "mission_dri", "verifier", "ic", "outsider")}
            ceo, mission, verifier = (by_name[name] for name in ("ceo", "mission_dri", "verifier"))

            def sql(statement, params=()):
                with psycopg.connect(h.env["APP_DATABASE_URL"], row_factory=dict_row) as conn:
                    conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope,))
                    return conn.execute(statement, params).fetchall()

            def snapshot():
                with psycopg.connect(h.env["APP_DATABASE_URL"]) as conn:
                    return snapshot_scope(conn, scope, fixture["tenant_id"], fixture["company_id"])

            def target(oid):
                obj = ceo.object(oid)
                return {"object_id": oid, "revision_id": obj["latest_revision_id"], "expected_version": obj["object_version"]}

            def prepared(client, kind, params, oid=None):
                command = client.command(kind, deepcopy(params), target=target(oid) if oid else None)
                command["expected_versions"] = client.json("POST", "/v1/actions/prepare", command)["expected_versions"]
                return command

            def commit(client, kind, params, oid=None):
                return client.json("POST", "/v1/actions", prepared(client, kind, params, oid))

            def reject(client, command, *, status=403, code="FORBIDDEN"):
                before = snapshot()
                assert_error(client.json("POST", "/v1/actions", command, expected=status), code)
                assert snapshot() == before, "Rejected authority action changed durable scope state"

            def reference(oid):
                return {"object_id": oid, "revision_id": ceo.object(oid)["effective_revision_id"]}

            def commitment(kind, upstream):
                party_names = ("ceo", "domain_dri") if kind == "BusinessCommitment" else ("domain_dri", "mission_dri")
                oid = commit(ceo, "create_object", {
                    "object_type": kind, "domain_id": domain,
                    "payload": {"title": "Synthetic identity authority " + kind, "terms": {"target": 80},
                                "required_assignment_ids": [actors[n]["assignment_id"] for n in party_names],
                                "upstream_refs": [reference(upstream)]},
                })["result"]["object_id"]
                revision = ceo.object(oid)["latest_revision"]
                signatures = []
                for name in party_names:
                    receipt = commit(by_name[name], "accept_commitment", {
                        "party_assignment_id": actors[name]["assignment_id"],
                        "understanding": "I accept this exact synthetic commitment and understand its terms.",
                        "accepted_terms_hash": revision["payload_hash"],
                    }, oid)
                    signatures.append(receipt["result"]["handshake_id"])
                activation = commit(ceo, "activate_commitment", {
                    "handshake_record_ids": signatures,
                    "activation_policy_revision_id": fixture["policy_revision_id"],
                }, oid)
                assert len(activation["effect_task_ids"]) == 1
                return oid

            bc = commitment("BusinessCommitment", fixture["outcome"]["object_id"])
            ec = commitment("ExecutionCommitment", bc)
            payload = {
                "title": "Synthetic named-person delivery",
                "execution_commitment_ref": reference(ec),
                "dri_assignment_id": actors["mission_dri"]["assignment_id"],
                "acceptor_assignment_id": actors["verifier"]["assignment_id"],
                "acceptance_criteria": [{"criterion_id": "source", "description": "Source is independently checked"}],
            }
            creation = prepared(ceo, "create_object", {"object_type": "WorkItem", "domain_id": domain, "payload": payload})
            self_review_work = deepcopy(creation)
            self_review_work["params"]["payload"]["acceptor_assignment_id"] = self_verifier_assignment
            identity_rows = sql(
                "SELECT assignment_id::text,principal_id::text,role FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=ANY(%s::uuid[])",
                (scope, [actors["mission_dri"]["assignment_id"], self_verifier_assignment]),
            )
            assert len(identity_rows) == 2
            assert len({r["principal_id"] for r in identity_rows}) == 1
            assert {r["role"] for r in identity_rows} == {"MISSION_DRI", "VERIFIER"}
            reject(ceo, self_review_work)
            work = ceo.json("POST", "/v1/actions", creation)["result"]["object_id"]
            accept_command = prepared(mission, "accept_work_item", {}, work)

            def append_policy(sequence, content):
                try:
                    with psycopg.connect(h.env["MIGRATION_DATABASE_URL"]) as conn:
                        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope,))
                        conn.execute(
                            """INSERT INTO gov_activation_policies
                               (policy_revision_id,scope_id,domain_id,policy_id,policy_seq,content,recorded_by)
                               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), scope, domain, policy["policy_id"], sequence,
                             Jsonb(content), actors["ceo"]["principal_id"]),
                        )
                except psycopg.Error as exc:
                    raise AssertionError(f"Delivery authority policy provisioning failed (SQLSTATE {exc.sqlstate})") from None

            legacy = deepcopy(policy["content"])
            for key in ("accept_work_item", "submit_deliverable", "review_deliverable", "record_outcome_assessment"):
                legacy["action_roles"].pop(key, None)
            append_policy(2, legacy)
            reject(mission, accept_command)
            append_policy(3, policy["content"])
            mission.json("POST", "/v1/actions", accept_command)
            asset = mission.json("POST", "/v1/evidence-assets", {
                "domain_id": domain, "title": "Synthetic identity acceptance evidence",
                "content_base64": base64.b64encode(b"Synthetic independent source for identity checks.\n").decode(),
                "media_type": "text/plain",
            })
            submitted = commit(mission, "submit_deliverable", {
                "title": "Identity submission v1", "summary": "Initial source package",
                "evidence_revision_ids": [asset["revision_id"]],
            }, work)["result"]

            def review_params(submission, result):
                return {"deliverable_revision_id": submission["deliverable_revision_id"],
                        "delivery_payload_hash": submission["payload_hash"], "verification_result": result,
                        "criterion_results": [{"criterion_id": "source", "result": "failed" if result == "changes_requested" else "passed",
                                               "note": "Add source explanation" if result == "changes_requested" else "Source verified"}],
                        "review_note": "Independent named-person review"}

            return_command = prepared(verifier, "review_deliverable", review_params(submitted, "changes_requested"), work)
            reject(by_name["ic"], return_command)
            reject(mission, return_command)
            returned = verifier.json("POST", "/v1/actions", return_command)
            assert verifier.json("GET", f"/v1/action-receipts/{returned['receipt_id']}")["receipt"] == returned
            submission2 = commit(mission, "submit_deliverable", {
                "title": "Identity submission v2", "summary": "Source explanation supplemented",
                "evidence_revision_ids": [asset["revision_id"]],
                "responds_to_acceptance_id": returned["result"]["acceptance_id"],
            }, work)["result"]
            old_review_command = prepared(verifier, "review_deliverable", review_params(submission2, "accepted"), work)
            retained_object = by_name["outsider"].action("create_object", {
                "object_type": "FeedbackThread", "domain_id": domain_b,
                "payload": {"title": "Synthetic retained domain", "description": "Positive authorization control after domain A revocation"},
            })["result"]["object_id"]
            assert verifier.object(retained_object)["object_id"] == retained_object
            revocation = commit(ceo, "revoke_assignment", {"assignment_id": actors["verifier"]["assignment_id"]})
            assert revocation["auth_epoch"] > returned["auth_epoch"]
            persisted_authority = sql(
                "SELECT assignment_id::text,domain_id::text,role,active FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s ORDER BY assignment_id",
                (scope, actors["verifier"]["principal_id"]),
            )
            authority_by_id = {row["assignment_id"]: row for row in persisted_authority}
            assert authority_by_id[actors["verifier"]["assignment_id"]]["active"] is False
            assert authority_by_id[retained_assignment]["active"] is True
            assert verifier.object(retained_object)["object_id"] == retained_object
            reject(verifier, old_review_command, status=404, code="NOT_FOUND")
            reject(verifier, return_command)
            before = snapshot()
            assert_error(verifier.json("GET", f"/v1/action-receipts/{returned['receipt_id']}", expected=403), "FORBIDDEN")
            assert_error(verifier.object(work, expected=404), "NOT_FOUND")
            assert snapshot() == before
            reviews = sql(
                "SELECT verification_result,submission_seq FROM gov_delivery_acceptances WHERE scope_id=%s AND work_item_object_id=%s",
                (scope, work),
            )
            assert reviews == [{"verification_result": "changes_requested", "submission_seq": 1}]
            task_rows = sql(
                "SELECT state,count(*) AS count FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s GROUP BY state",
                (fixture["tenant_id"], fixture["company_id"]),
            )
            assert task_rows == [{"state": "queued", "count": 2}]
            report.update(scope_id=scope, work_item_id=work,
                          same_human_different_assignment_rejected=True, same_role_wrong_person_rejected=True,
                          legacy_policy_rejected=True, revoked_prepared_review_status=404,
                          revoked_receipt_replay_status=403, revoked_receipt_read_status=403,
                          retained_domain_positive_read_status=200, delivery_review_rows=reviews,
                          persisted_authority=persisted_authority,
                          unchanged_rejection_snapshot=before, commitment_effects=task_rows,
                          external_effects_accepted=False,
                          external_effects_note="Two commitment tasks remain queued in this independent scope; this group does not start a Worker or validate external effects.")
        finally:
            for client in clients:
                client.close()
    return report


__all__ = ["run_delivery_authority_checks"]
