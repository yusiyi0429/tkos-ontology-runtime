"""A1-08 real authority revocation, with history and dependency read oracles.

The caller must run this in the one-off A1 database while both test domains still
permit legitimate legacy creation. Only four new synthetic role assignments are
owner-provisioned. Every revocation and business object is created through HTTP;
old objects, source bytes, receipts and snapshots are never rewritten. The
MISSION_DRI principal retains B-domain read authority throughout, so 404s after
losing A cannot be explained by globally invalid authentication. A separate
DOMAIN_DRI A+B fixture tests loss of source B while target A remains readable.

No application module/validator is imported. Importing this module starts no DB,
HTTP, container or subprocess work. Successful evidence is local acceptance only.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from acceptance.runtime.client import Client
from .control_adapter import LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .matrix import REQUIRED
from .support import digest, public_json


def _inputs(fixture: dict, legacy_flow, legacy_result: dict) -> dict:
    """Reject absent prerequisites before the caller's DB or HTTP is touched."""
    needed = {"scope_id", "domain_id", "tenant_id", "company_id", "actors"}
    if not isinstance(fixture, dict) or not needed.issubset(fixture):
        raise NotReady("A1-08 requires an isolated scoped actor fixture")
    actors = fixture["actors"]
    if any(name not in actors or not {"principal_id", "assignment_id", "token"}.issubset(actors[name])
           for name in ("ceo", "mission_dri", "domain_dri", "outsider", "verifier")):
        raise NotReady("A1-08 requires real CEO, DRI, verifier and second-domain actors")
    if not isinstance(legacy_result, dict) or not {"work_item", "ec", "snapshot_id"}.issubset(legacy_result):
        raise NotReady("A1-08 needs the actual completed legacy delivery flow")
    if getattr(legacy_flow, "fixture", {}).get("scope_id") != fixture["scope_id"]:
        raise NotReady("legacy flow and authority fixture scopes differ")
    commands = [item for item in getattr(legacy_flow, "commands", [])
                if item.get("actor") == "mission_dri" and
                item.get("request", {}).get("action_type") == "submit_deliverable"]
    evidence = getattr(legacy_flow, "evidence", [])
    if len(commands) < 2 or len(evidence) < 2 or not getattr(legacy_flow, "objects", []):
        raise NotReady("real v1/v2 delivery commands and source bytes were not captured")
    original = deepcopy(commands[-1])
    if not original.get("receipt", {}).get("receipt_id") or not original["request"].get("idempotency_key"):
        raise NotReady("original principal/key/body/receipt evidence is incomplete")
    source = deepcopy(evidence[-1])
    if not {"object_id", "revision_id", "sha256"}.issubset(source):
        raise NotReady("original evidence identity and content digest are required")
    return {"original": original, "evidence": source}


def run_authority_cases(h, f: dict, *, url: str, legacy_flow, legacy_result: dict) -> dict:
    """Return exact A1-08 matrix checks, each backed by real HTTP/SQL observations.

    Missing preconditions raise NotReady. Failed checks retain an incomplete
    report and raise; no negative response is accepted without its unique frozen
    status/code and unchanged SQL/S3 snapshots. The existing same-domain
    business contract may make cross-domain source_refs unreachable. In that
    case, evidence combines the zero-write rejection, a legitimate mixed-domain
    Context Pack and every original receipt's independently enumerated sources.
    This intentionally leaves the
    synthetic revoked assignments revoked; callers must sequence later gates
    with unaffected actors, rather than restoring history behind the API.
    """
    inputs = _inputs(f, legacy_flow, legacy_result)
    connection_info = conninfo_to_dict(h.env.values["MIGRATION_DATABASE_URL"])
    if not connection_info.get("dbname", "").startswith("tkos_a1_"):
        raise NotReady("A1-08 owner provisioning requires a generated one-off tkos_a1_ database")
    actors, scope, domain_a = f["actors"], f["scope_id"], f["domain_id"]
    report = {"gate": "a1_authority", "status": "not_run", "runtime_accepted": False,
              "contract_a1_accepted": False, "checks": {
                  name: {"passed": False, "status": "not_run", "evidence": None}
                  for name in REQUIRED["A1-08"]}, "controls": [], "http_observations": []}
    clients = None
    current_check = None

    def sql(statement: str, values: tuple = ()) -> list[dict]:
        return h.sql(f, statement, values)

    def mark(name: str, evidence: dict) -> None:
        assert name in report["checks"] and report["checks"][name]["status"] == "not_run"
        report["checks"][name] = {"passed": True, "status": "passed", "evidence": evidence}

    def observed(client, method: str, path: str, body=None, *, status: int = 200,
                 code: str | None = None, binary: bool = False):
        before = h.snapshot(f)
        storage = h.storage_snapshot(scope)
        response = client.request(method, path, body, expected=status)
        value = response.content if binary else response.json()
        if code is not None:
            assert isinstance(value, dict) and value.get("error", {}).get("code") == code, \
                "authority negative returned a different frozen error code"
        assert h.snapshot(f) == before, "read/rejection/replay changed governed SQL state"
        assert h.storage_snapshot(scope) == storage, "read/rejection/replay changed S3 versions"
        report["http_observations"].append({"actor": client.name, "method": method, "path": path,
            "status": status, "code": code, "sql_sha256": digest(before), "s3_sha256": storage["sha256"]})
        return value

    def command(actor: str, kind: str, params: dict, *, target: dict | None = None) -> tuple[dict, dict]:
        request = Client.command(kind, params, target=target)
        prepared = observed(clients[actor], "POST", "/v1/actions/prepare", request)
        request["expected_versions"] = prepared["expected_versions"]
        storage = h.storage_snapshot(scope)
        receipt = clients[actor].json("POST", "/v1/actions", request)
        assert receipt.get("status") == "committed" and receipt.get("receipt_id")
        assert receipt.get("effect_task_ids") == [], "authority fixture must not dispatch external work"
        assert h.storage_snapshot(scope) == storage, "non-evidence fixture command wrote S3"
        report["controls"].append({"kind": "real_http_command", "actor": actor, "action_type": kind,
                                   "receipt_id": receipt["receipt_id"], "result_sha256": digest(receipt["result"])})
        return request, receipt

    def create(actor: str, kind: str, domain: str, payload: dict) -> tuple[dict, dict, dict]:
        request, receipt = command(actor, "create_object", {"object_type": kind, "domain_id": domain,
                                                             "payload": payload})
        obj = observed(clients[actor], "GET", "/v1/objects/" + receipt["result"]["object_id"])
        bindings = sql("SELECT protocol_id,contract_version FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s",
                       (scope, obj["object_id"]))
        assert len(bindings) == 1 and bindings[0] == {"protocol_id": LEGACY_PROTOCOL, "contract_version": LEGACY_CONTRACT}, \
            "authority fixture must use an actual registered legacy object"
        return obj, request, receipt

    def target(obj: dict) -> dict:
        return {"object_id": obj["object_id"], "revision_id": obj["latest_revision_id"],
                "expected_version": obj["object_version"]}

    def context(actor: str, object_ids: list[str]) -> dict:
        before = h.snapshot(f)
        storage = h.storage_snapshot(scope)
        at = datetime.now(timezone.utc).isoformat()
        pack = clients[actor].json("POST", "/v1/context-packs", {
            "object_ids": object_ids, "valid_at": at, "known_at": at})
        after = h.snapshot(f)
        assert set(before["tables"]) == set(after["tables"])
        changed = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
        assert changed == {"gov_context_snapshots"}, "Context fixture wrote business state"
        assert after["tables"]["gov_context_snapshots"]["row_count"] == before["tables"]["gov_context_snapshots"]["row_count"] + 1
        assert h.storage_snapshot(scope) == storage
        report["controls"].append({"kind": "context_snapshot", "actor": actor, "snapshot_id": pack["context_snapshot_id"]})
        return pack

    def revoke(assignment_id: str) -> dict:
        before = sql("SELECT active,principal_id::text,domain_id::text FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                     (scope, assignment_id))
        assert len(before) == 1 and before[0]["active"] is True
        _, receipt = command("ceo", "revoke_assignment", {"assignment_id": assignment_id})
        after = sql("SELECT active FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s", (scope, assignment_id))
        assert len(after) == 1 and after[0]["active"] is False
        result = receipt["result"]
        assert result["assignment_id"] == assignment_id and result["before_active"] is True and result["after_active"] is False
        assert result["auth_epoch"] == result["before_auth_epoch"] + 1
        report["controls"].append({"kind": "revocation_verified_by_sql", "assignment_id": assignment_id,
                                   "receipt_id": receipt["receipt_id"], "principal_id": before[0]["principal_id"],
                                   "domain_id": before[0]["domain_id"]})
        return receipt

    historical_object_ids = list(dict.fromkeys(map(str, legacy_flow.objects)))
    historical_receipt_ids = list(dict.fromkeys(str(item["receipt"]["receipt_id"])
        for item in legacy_flow.commands))
    historical_receipt_ids += [str(item["receipt_id"]) for item in legacy_flow.evidence if item.get("receipt_id")]

    def history_hashes() -> dict:
        # Fixed table/column names only; every row is scoped and restricted to
        # actual original IDs. New fixture facts do not hide old-row mutation.
        records = {}
        for table, column, identities in (
            ("gov_objects", "object_id", historical_object_ids),
            ("gov_object_revisions", "object_id", historical_object_ids),
            ("gov_lifecycle_events", "object_id", historical_object_ids),
            ("gov_handshakes", "object_id", historical_object_ids),
            ("gov_work_item_state", "object_id", [legacy_result["work_item"]]),
            ("gov_delivery_acceptances", "work_item_object_id", [legacy_result["work_item"]]),
            ("gov_action_receipts", "receipt_id", historical_receipt_ids),
            ("gov_context_snapshots", "snapshot_id", [legacy_result["snapshot_id"]]),
        ):
            values = sql(f"SELECT to_jsonb(t)::text AS row FROM {table} t WHERE scope_id=%s AND {column}=ANY(%s::uuid[]) ORDER BY to_jsonb(t)::text COLLATE \"C\"",
                         (scope, identities))
            records[table] = {"rows": len(values), "sha256": digest(values)}
        assert records["gov_objects"]["rows"] == len(historical_object_ids)
        assert records["gov_context_snapshots"]["rows"] == 1
        assert records["gov_delivery_acceptances"]["rows"] == 2
        return records

    try:
        domain_rows = sql("SELECT domain_id::text FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s AND active",
                          (scope, actors["outsider"]["assignment_id"]))
        if len(domain_rows) != 1:
            raise NotReady("a currently assigned second-domain actor is missing")
        domain_b = domain_rows[0]["domain_id"]
        if domain_b == domain_a or (f.get("outsider_domain_id") and f["outsider_domain_id"] != domain_b):
            raise NotReady("independent source B must be a distinct observed domain")
        for domain in (domain_a, domain_b):
            policies = sql("""SELECT content FROM gov_protocol_policies WHERE scope_id=%s AND (domain_id=%s OR domain_id IS NULL)
                ORDER BY CASE WHEN domain_id IS NULL THEN 0 ELSE 1 END DESC,policy_seq DESC LIMIT 1""", (scope, domain))
            if len(policies) != 1 or any(policies[0]["content"].get(key) != value for key, value in (
                    ("default_protocol", LEGACY_PROTOCOL), ("default_contract_version", LEGACY_CONTRACT),
                    ("allow_legacy_create", True))):
                raise NotReady("authority fixtures need both domains' trusted legacy creation policy before A policy switch")
            authority = sql("SELECT content FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1",
                            (scope, domain))
            roles = authority[0]["content"].get("action_roles", {}) if len(authority) == 1 else {}
            if "IC" not in roles.get("read", []) or "CEO" not in roles.get("revoke_assignment", []) \
                    or any("IC" in roles.get(action, []) for action in ("accept_work_item", "submit_deliverable")):
                raise NotReady("actual policy does not express retained read versus revoked delivery authority")
        active = sql("""SELECT assignment_id::text,principal_id::text,domain_id::text,role FROM gov_role_assignments
            WHERE scope_id=%s AND active AND valid_from<=clock_timestamp() AND (valid_to IS NULL OR valid_to>clock_timestamp())
            AND principal_id=ANY(%s::uuid[])""", (scope, [actors[name]["principal_id"] for name in ("mission_dri", "domain_dri", "ceo")]))
        mission_rows = [row for row in active if row["principal_id"] == actors["mission_dri"]["principal_id"]]
        if len(mission_rows) != 1 or mission_rows[0]["assignment_id"] != actors["mission_dri"]["assignment_id"] \
                or mission_rows[0]["domain_id"] != domain_a or mission_rows[0]["role"] != "MISSION_DRI":
            raise NotReady("mission principal must start with only its exact active A MISSION_DRI assignment")
        if any(row["domain_id"] == domain_b for row in active):
            raise NotReady("use a fresh scope; preexisting B grants would invalidate source-loss controls")
        original_history = history_hashes()
        original_receipts = sql("""SELECT receipt_id::text,result,object_versions,target_object_id::text
            FROM gov_action_receipts WHERE scope_id=%s ORDER BY receipt_id""", (scope,))
        if not original_receipts:
            raise NotReady("A1-08 must enumerate real existing historical receipts")
        receipt_dependencies = []
        for receipt in original_receipts:
            refs = set(map(str, receipt["result"].get("referenced_object_ids", [])))
            refs.update(str(item["object_id"]) for item in receipt["object_versions"])
            if receipt["target_object_id"]:
                refs.add(receipt["target_object_id"])
            domains = sql("SELECT object_id::text,domain_id::text FROM gov_objects WHERE scope_id=%s AND object_id=ANY(%s::uuid[]) ORDER BY object_id",
                          (scope, sorted(refs)))
            if len(domains) != len(refs) or any(row["domain_id"] != domain_a for row in domains):
                raise NotReady("original receipt dependencies must be complete and in the legacy A domain")
            declared_domain = receipt["result"].get("domain_id")
            if (declared_domain is not None and declared_domain != domain_a) or not (refs or declared_domain):
                raise NotReady("original objectless receipt needs a verified A-domain read anchor")
            receipt_dependencies.append({"receipt_id": receipt["receipt_id"], "object_ids": sorted(refs),
                "domains": domains, "result_domain_id": declared_domain,
                "denied_status": 403 if declared_domain else 404,
                "denied_code": "FORBIDDEN" if declared_domain else "NOT_FOUND"})
        grants = {name: str(uuid.uuid4()) for name in ("mission_read_a", "mission_read_b", "source_reader_b", "ceo_control_b")}
        grant_rows = [(grants["mission_read_a"], actors["mission_dri"]["principal_id"], domain_a, "IC"),
                      (grants["mission_read_b"], actors["mission_dri"]["principal_id"], domain_b, "IC"),
                      (grants["source_reader_b"], actors["domain_dri"]["principal_id"], domain_b, "IC"),
                      (grants["ceo_control_b"], actors["ceo"]["principal_id"], domain_b, "CEO")]
        with psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
            role = conn.execute("SELECT current_user::text AS current,session_user::text AS session").fetchone()
            assert role["current"] == role["session"] == connection_info["user"], "synthetic assignment owner identity mismatch"
            conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope,))
            conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
            conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
            for assignment, principal, domain, role_name in grant_rows:
                conn.execute("INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES(%s,%s,%s,%s,%s)",
                             (assignment, scope, principal, domain, role_name))
            conn.execute("INSERT INTO gov_protocol_control_events(scope_id,event_type,detail,actor) VALUES(%s,%s,%s,%s)",
                (scope, "independent_authority_fixture", Jsonb({"record_origin": "synthetic", "assignment_ids": list(grants.values()),
                  "business_success": False}), "independent-a1-08-fixture"))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        report["controls"].append({"kind": "owner_added_synthetic_assignments", "assignments": [
            {"assignment_id": aid, "principal_id": pid, "domain_id": domain, "role": role_name}
            for aid, pid, domain, role_name in grant_rows]})
        clients = h.clients(url, f)
        report["status"] = "running"
        # Separate cross-domain source authorization: target A remains visible.
        current_check = "all_receipt_and_snapshot_dependencies_reauthorized"
        timestamp = datetime.now(timezone.utc).isoformat()
        source_b, _, _ = create("outsider", "MetricObservation", domain_b, {
            "title": "A1 authority source B", "metric_id": "source-b", "value": 2, "unit": "clients",
            "valid_from": timestamp, "upstream_refs": []})
        source_ref = {"object_id": source_b["object_id"], "revision_id": source_b["latest_revision_id"]}
        a_payload = {
            "title": "A1 authority target A references B", "metric_id": "target-a", "value": 2, "unit": "clients",
            "valid_from": timestamp, "upstream_refs": [source_ref]}
        # Explicit positive controls prevent source invisibility or missing
        # create authority being mistaken for a same-domain contract limit.
        control_a, _, _ = create("domain_dri", "MetricObservation", domain_a,
                                  {**a_payload, "title": "A1 A create authority control", "upstream_refs": []})
        observed(clients["domain_dri"], "GET", "/v1/objects/" + source_b["object_id"])
        mixed_pack = context("domain_dri", [control_a["object_id"], source_b["object_id"]])
        assert {item["object_id"] for item in mixed_pack["selected"]} == {control_a["object_id"], source_b["object_id"]}
        assert mixed_pack["excluded"] == [], "mixed-domain positive control was not actually selected"
        mixed_path = "/v1/context-packs/" + mixed_pack["context_snapshot_id"]
        observed(clients["domain_dri"], "GET", mixed_path)
        dependency_request = Client.command("create_object", {"object_type": "MetricObservation",
            "domain_id": domain_a, "payload": a_payload})
        dependency_receipt = None
        cross_unavailable = False
        for path in ("/v1/actions/prepare", "/v1/actions"):
            before_cross = h.snapshot(f)
            storage_cross = h.storage_snapshot(scope)
            response = clients["domain_dri"].request("POST", path, dependency_request, expected={200, 404})
            value = response.json()
            assert h.storage_snapshot(scope) == storage_cross
            if response.status_code == 404:
                assert value.get("error", {}).get("code") == "NOT_FOUND"
                assert h.snapshot(f) == before_cross, "unavailable cross-domain fixture changed state"
                dependency_evidence = {
                    "reachability": "current legacy contract rejects cross-domain upstream_refs; no synthetic cross-domain history fabricated",
                    "request_phase": path, "status": 404, "code": "NOT_FOUND",
                    "domain_a_create_positive": control_a["object_id"], "domain_b_read_positive": source_b["object_id"],
                    "sql_unchanged_sha256": digest(before_cross), "s3_unchanged_sha256": storage_cross["sha256"]}
                cross_unavailable = True
                break
            if path.endswith("/prepare"):
                assert h.snapshot(f) == before_cross
                dependency_request["expected_versions"] = value["expected_versions"]
            else:
                assert value.get("status") == "committed" and value.get("effect_task_ids") == []
                dependency_receipt = value
                report["controls"].append({"kind": "real_http_cross_domain_create", "receipt_id": value["receipt_id"]})
        if not cross_unavailable:
            assert dependency_receipt is not None
            object_a = observed(clients["domain_dri"], "GET", "/v1/objects/" + dependency_receipt["result"]["object_id"])
            dependency_pack = context("domain_dri", [object_a["object_id"]])
            selected = dependency_pack["selected"]
            assert len(selected) == 1 and any(item["object_id"] == source_ref["object_id"] and item["revision_id"] == source_ref["revision_id"]
                                             for item in selected[0].get("source_refs", [])), "cross-domain source was not really included"
            dependency_path = "/v1/action-receipts/" + dependency_receipt["receipt_id"]
            pack_path = "/v1/context-packs/" + dependency_pack["context_snapshot_id"]
            observed(clients["domain_dri"], "GET", dependency_path)
            observed(clients["domain_dri"], "GET", pack_path)
            assert observed(clients["domain_dri"], "POST", "/v1/actions", dependency_request) == dependency_receipt
            revoke(grants["source_reader_b"])
            assert observed(clients["domain_dri"], "GET", "/v1/objects/" + object_a["object_id"])["object_id"] == object_a["object_id"]
            observed(clients["domain_dri"], "GET", "/v1/objects/" + source_b["object_id"], status=404, code="NOT_FOUND")
            for method, path, body in (("GET", dependency_path, None), ("GET", pack_path, None),
                                        ("POST", "/v1/actions", dependency_request)):
                observed(clients["domain_dri"], method, path, body, status=404, code="NOT_FOUND")
            at = datetime.now(timezone.utc).isoformat()
            observed(clients["domain_dri"], "POST", "/v1/context-packs", {
                "object_ids": [object_a["object_id"]], "valid_at": at, "known_at": at}, status=404, code="NOT_FOUND")
            observed(clients["ceo"], "GET", dependency_path)
            observed(clients["ceo"], "GET", pack_path)
            dependency_evidence = {"target_a_still_readable": object_a["object_id"], "revoked_source_b": source_b["object_id"],
                 "receipt_id": dependency_receipt["receipt_id"], "snapshot_id": dependency_pack["context_snapshot_id"],
                 "get_receipt_get_snapshot_replay_new_context": "404 NOT_FOUND", "other_reader": "ceo 200"}
        else:
            revoke(grants["source_reader_b"])
        assert observed(clients["domain_dri"], "GET", "/v1/objects/" + control_a["object_id"])["object_id"] == control_a["object_id"]
        observed(clients["domain_dri"], "GET", "/v1/objects/" + source_b["object_id"], status=404, code="NOT_FOUND")
        observed(clients["domain_dri"], "GET", mixed_path, status=404, code="NOT_FOUND")
        assert observed(clients["ceo"], "GET", mixed_path) == mixed_pack
        dependency_evidence["mixed_context"] = {"snapshot_id": mixed_pack["context_snapshot_id"],
            "requested_object_ids": [control_a["object_id"], source_b["object_id"]],
            "after_revocation": {"a_object": 200, "b_object": 404, "snapshot": 404, "other_current_reader": 200}}

        # Valid offered and in-progress targets prove denial of future delivery,
        # without relying solely on an already accepted historical target.
        current_check = "execute_revoked_new_write_denied"
        ec = observed(clients["ceo"], "GET", "/v1/objects/" + legacy_result["ec"])
        ec_ref = {"object_id": ec["object_id"], "revision_id": ec["effective_revision_id"]}
        assert ec_ref["revision_id"] and ec["lifecycle_status"] == "active"
        work_payload = {"title": "A1 authority future delivery target", "execution_commitment_ref": ec_ref,
            "dri_assignment_id": actors["mission_dri"]["assignment_id"],
            "acceptor_assignment_id": actors["verifier"]["assignment_id"],
            "acceptance_criteria": [{"criterion_id": "source", "description": "Unchanged original source"}]}
        offered, _, _ = create("ceo", "WorkItem", domain_a, work_payload)
        progress, _, _ = create("ceo", "WorkItem", domain_a, {**work_payload, "title": "A1 authority in-progress target"})
        command("mission_dri", "accept_work_item", {}, target=target(progress))
        progress = observed(clients["mission_dri"], "GET", "/v1/objects/" + progress["object_id"])
        assert offered["lifecycle_status"] == "offered" and progress["lifecycle_status"] == "in_progress"
        pending = [Client.command("accept_work_item", {}, target=target(offered)),
                   Client.command("submit_deliverable", {"title": "Revoked execution must fail", "summary": "Exact DRI is no longer active",
                     "evidence_revision_ids": [inputs["evidence"]["revision_id"]]}, target=target(progress))]
        for request in pending:
            prepared = observed(clients["mission_dri"], "POST", "/v1/actions/prepare", request)
            request["expected_versions"] = prepared["expected_versions"]
        original = inputs["original"]
        receipt_path = "/v1/action-receipts/" + original["receipt"]["receipt_id"]
        snapshot_path = "/v1/context-packs/" + legacy_result["snapshot_id"]
        evidence_path = f"/v1/evidence-assets/{inputs['evidence']['object_id']}/revisions/{inputs['evidence']['revision_id']}"
        old_receipt = observed(clients["mission_dri"], "GET", receipt_path)
        old_snapshot = observed(clients["mission_dri"], "GET", snapshot_path)
        all_receipt_before = {item["receipt_id"]: observed(clients["mission_dri"], "GET", "/v1/action-receipts/" + item["receipt_id"])
                              for item in receipt_dependencies}
        assert hashlib.sha256(observed(clients["mission_dri"], "GET", evidence_path, binary=True)).hexdigest() == inputs["evidence"]["sha256"]
        revoke(actors["mission_dri"]["assignment_id"])
        for request in pending:
            for path in ("/v1/actions/prepare", "/v1/actions"):
                observed(clients["mission_dri"], "POST", path, request, status=403, code="FORBIDDEN")
        mark(current_check, {"offered_work_item": offered["object_id"], "in_progress_work_item": progress["object_id"],
                            "prepare_execute_accept_submit": "403 FORBIDDEN", "prior_prepare": "200"})
        current_check = "read_retained_history_replay_allowed"
        assert observed(clients["mission_dri"], "GET", receipt_path) == old_receipt
        assert observed(clients["mission_dri"], "GET", snapshot_path) == old_snapshot
        assert observed(clients["mission_dri"], "POST", "/v1/actions", original["request"]) == original["receipt"]
        for receipt_id, original_read in all_receipt_before.items():
            assert observed(clients["mission_dri"], "GET", "/v1/action-receipts/" + receipt_id) == original_read
        assert hashlib.sha256(observed(clients["mission_dri"], "GET", evidence_path, binary=True)).hexdigest() == inputs["evidence"]["sha256"]
        assert history_hashes() == original_history, "revoking execution rewrote original delivery history"
        mark(current_check, {"retained_a_read_assignment": grants["mission_read_a"], "receipt_id": original["receipt"]["receipt_id"],
                            "snapshot_id": legacy_result["snapshot_id"], "receipt_snapshot_source_replay": "200 unchanged"})

        current_check = "read_revoked_receipt_snapshot_replay_denied"
        revoke(grants["mission_read_a"])
        active_a = sql("""SELECT assignment_id FROM gov_role_assignments WHERE scope_id=%s AND principal_id=%s AND domain_id=%s
            AND active AND valid_from<=clock_timestamp() AND (valid_to IS NULL OR valid_to>clock_timestamp())""",
            (scope, actors["mission_dri"]["principal_id"], domain_a))
        assert active_a == [], "A-domain read rights were not completely revoked"
        assert observed(clients["mission_dri"], "GET", "/v1/objects/" + source_b["object_id"])["object_id"] == source_b["object_id"]
        for method, path, body, status, code in (("GET", receipt_path, None, 403, "FORBIDDEN"),
                ("GET", snapshot_path, None, 404, "NOT_FOUND"), ("GET", evidence_path, None, 404, "NOT_FOUND"),
                ("POST", "/v1/actions", original["request"], 403, "FORBIDDEN")):
            observed(clients["mission_dri"], method, path, body, status=status, code=code)
        for item in receipt_dependencies:
            observed(clients["mission_dri"], "GET", "/v1/action-receipts/" + item["receipt_id"],
                     status=item["denied_status"], code=item["denied_code"])
        mark(current_check, {"a_read_removed": True, "b_positive_control": "200", "retained_b_assignment": grants["mission_read_b"],
                            "receipt_and_replay": "403 FORBIDDEN", "snapshot_and_evidence": "404 NOT_FOUND",
                            "all_original_receipt_dependencies": receipt_dependencies})
        current_check = "other_current_reader_retains_history"
        assert observed(clients["ceo"], "GET", receipt_path) == old_receipt
        assert observed(clients["ceo"], "GET", snapshot_path) == old_snapshot
        for receipt_id, original_read in all_receipt_before.items():
            assert observed(clients["ceo"], "GET", "/v1/action-receipts/" + receipt_id) == original_read
        assert hashlib.sha256(observed(clients["ceo"], "GET", evidence_path, binary=True)).hexdigest() == inputs["evidence"]["sha256"]
        assert observed(clients["ceo"], "GET", "/v1/objects/" + legacy_result["work_item"])["lifecycle_status"] == "delivery_accepted"
        final_history = history_hashes()
        assert final_history == original_history, "read revocation rewrote historical objects/receipts/snapshots"
        mark(current_check, {"current_reader": "ceo", "receipt_snapshot_original_source": "200 unchanged",
                            "historical_rows": final_history})
        dependency_evidence["all_historical_receipts"] = receipt_dependencies
        dependency_evidence["original_receipt_read_count"] = len(receipt_dependencies)
        dependency_evidence["original_receipts_denied_after_a_read_revocation"] = True
        dependency_evidence["scope_authentication_control"] = "A retained for domain_dri; B retained for mission_dri; both 200"
        mark("all_receipt_and_snapshot_dependencies_reauthorized", dependency_evidence)
        assert set(report["checks"]) == set(REQUIRED["A1-08"])
        report["status"] = "passed" if all(item["passed"] for item in report["checks"].values()) else "incomplete"
        return report
    except NotReady:
        report["status"] = "not_ready"
        raise
    except BaseException as exc:
        report["status"] = "failed"
        safe_error = ("SQLSTATE " + str(exc.sqlstate)) if isinstance(exc, psycopg.Error) else type(exc).__name__ + ": " + str(exc)
        report["error"] = safe_error
        if current_check is not None and not report["checks"][current_check]["passed"]:
            report["checks"][current_check] = {"passed": False, "status": "failed", "evidence": safe_error}
        raise
    finally:
        public_json(h.output / "authority-cases.json", report)
        if clients:
            for client in clients.values():
                client.close()
