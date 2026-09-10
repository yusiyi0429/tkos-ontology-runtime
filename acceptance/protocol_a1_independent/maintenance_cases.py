"""A1-14 actual maintenance CLI replay/freeze, independently observed by SQL.

No migration runs here. The original-schema and two-migration-run evidence is
root-owned and cannot be replaced by two calls to backfill-legacy. This module
checks only backfill_replay_twice, new_write_disabled_history_readable and
disabled_no_legacy_fallback. It uses real CLI templates backfill_legacy,
freeze_writes and set_registry, then restores the original registry by a new
set-registry version even when an assertion fails after a freeze commit.

Import performs no DB/HTTP work. Run before intentionally unbound fixtures are
left in this scope, and with a current CEO who can read the supplied legacy flow.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import uuid
import psycopg

from acceptance.runtime.client import Client
from .control_adapter import LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .matrix import REQUIRED
from .support import digest, private_json, public_json


CHECKS = ("backfill_replay_twice", "new_write_disabled_history_readable", "disabled_no_legacy_fallback")
OPERATIONS = ("backfill_legacy", "freeze_writes", "set_registry")


def _prerequisites(f, control, legacy_flow, legacy_result):
    operations = getattr(control, "content", {}).get("operations", {})
    for name in OPERATIONS:
        operation = operations.get(name)
        if not isinstance(operation, dict) or not isinstance(operation.get("args"), list) \
                or type(operation.get("success", {}).get("exit")) is not int:
            raise NotReady("maintenance CLI operation/exit contract has not been frozen: " + name)
    if not isinstance(f, dict) or not {"scope_id", "domain_id", "actors"}.issubset(f) \
            or "ceo" not in f["actors"]:
        raise NotReady("maintenance needs the current scoped CEO fixture")
    if getattr(legacy_flow, "fixture", {}).get("scope_id") != f["scope_id"] \
            or not getattr(legacy_flow, "objects", None) or not isinstance(legacy_result, dict):
        raise NotReady("maintenance must use the real legacy flow in this scope")
    commands = [row for row in getattr(legacy_flow, "commands", []) if row.get("actor") == "ceo"
                and row.get("receipt", {}).get("receipt_id") and row.get("request", {}).get("idempotency_key")]
    if not commands or not legacy_result.get("work_item"):
        raise NotReady("maintenance needs a real committed CEO command and delivery history")
    return deepcopy(commands[0])


def _changed(before, after):
    assert set(before["tables"]) == set(after["tables"]), "maintenance changed oracle table coverage"
    return {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}


def _object_history_equal(before, after):
    """Compare business interpretation; current protocol/write metadata may vary."""
    for key in ("object_id", "object_type", "domain_id", "lifecycle_status", "object_version",
                "latest_revision_id", "effective_revision_id", "outcome_achievement"):
        if key in before:
            assert after.get(key) == before[key], "freeze altered historical object interpretation: " + key
    for key in ("latest_revision", "effective_revision"):
        original = before.get(key)
        if original is None:
            assert after.get(key) is None
        else:
            projected = after.get(key)
            assert isinstance(projected, dict)
            for field in ("object_id", "revision_id", "object_version", "payload", "payload_hash"):
                assert projected.get(field) == original.get(field), "freeze changed raw historical revision"
    for key in ("delivery", "feedback", "delivery_review", "outcome_assessment"):
        if key in before:
            assert after.get(key) == before[key], "freeze changed historical judgment/processing state"


def run_maintenance(h, f, url, adapter, control, legacy_flow, legacy_result):
    original_request = _prerequisites(f, control, legacy_flow, legacy_result)
    assert set(CHECKS) <= set(REQUIRED["A1-14"])
    scope, domain = f["scope_id"], f["domain_id"]
    report = {"gate": "a1_maintenance", "status": "not_run", "runtime_accepted": False,
              "contract_a1_accepted": False, "migration_replay_evidence": "not_run_by_this_module",
              "checks": {name: {"passed": False, "status": "not_run", "evidence": None} for name in CHECKS},
              "controls": [], "requests": []}
    clients = None
    restore_needed = False
    original_registry = None
    current_check = None

    def sql(statement, values=()):
        return h.sql(f, statement, values)

    def registry():
        rows = sql("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1",
                   (scope, LEGACY_PROTOCOL))
        if len(rows) != 1:
            raise NotReady("the actual legacy support registry is missing")
        return rows[0]

    def versioned_rows(table):
        assert table in {"gov_protocol_control_events", "gov_protocol_support_registry"}
        return sql(f"SELECT to_jsonb(t)::text AS row FROM {table} t WHERE scope_id=%s ORDER BY to_jsonb(t)::text COLLATE \"C\"", (scope,))

    def append_only(old, new, *, minimum=0, maximum=1):
        old_rows, new_rows = [row["row"] for row in old], [row["row"] for row in new]
        assert len(set(old_rows)) == len(old_rows) and len(set(new_rows)) == len(new_rows)
        assert set(old_rows) <= set(new_rows), "control operation rewrote/deleted old history"
        assert minimum <= len(new_rows) - len(old_rows) <= maximum, "unexpected control append count"

    def cli(operation, label, *, document=None):
        document_path = h.private / ("maintenance-" + label + ".json")
        if document is not None:
            private_json(document_path, document)
        values = {"scope_id": scope, "domain_id": domain, "document_file": document_path,
                  "protocol_id": LEGACY_PROTOCOL,
                  "contract_version": original_registry["contract_version"] if original_registry else LEGACY_CONTRACT,
                  "reason": "Independent synthetic maintenance acceptance: " + label,
                  "operator": "codex-independent-acceptance"}
        outcome = control.invoke(adapter, operation, "maintenance-" + label, values)
        response = outcome.get("response")
        assert isinstance(response, dict) and response.get("ok") is True, "maintenance CLI did not return its structured success document"
        report["controls"].append({"operation": operation, "label": label,
                                   "exit_code": outcome["exit_code"], "response": response})
        return response

    def readonly(method, path, body=None, *, binary=False):
        before, storage = h.snapshot(f), h.storage_snapshot(scope)
        response = clients["ceo"].request(method, path, body)
        value = response.content if binary else response.json()
        assert h.snapshot(f) == before, "maintenance read/replay/prepare wrote SQL"
        assert h.storage_snapshot(scope) == storage, "maintenance read/replay/prepare wrote S3"
        report["requests"].append({"method": method, "path": path, "status": 200,
                                   "sql_sha256": digest(before), "s3_sha256": storage["sha256"]})
        return value

    def commit(request):
        prepared = readonly("POST", "/v1/actions/prepare", request)
        request["expected_versions"] = prepared["expected_versions"]
        storage = h.storage_snapshot(scope)
        receipt = clients["ceo"].json("POST", "/v1/actions", request)
        assert receipt["status"] == "committed" and receipt["effect_task_ids"] == []
        assert h.storage_snapshot(scope) == storage
        report["controls"].append({"operation": "real_http_positive", "action_type": request["action_type"],
                                   "receipt_id": receipt["receipt_id"]})
        return receipt

    def mark(name, evidence):
        report["checks"][name] = {"passed": True, "status": "passed", "evidence": evidence}

    try:
        unbound = sql("""SELECT o.object_id FROM gov_objects o WHERE o.scope_id=%s AND NOT EXISTS
            (SELECT 1 FROM gov_object_protocol_bindings b WHERE b.scope_id=o.scope_id AND b.object_id=o.object_id)""", (scope,))
        if unbound:
            raise NotReady("backfill replay gate requires complete registrations; do not turn missing-binding negative fixtures into legacy objects")
        registration = adapter.full_registration_coverage(f)
        original_registry = registry()
        if original_registry["contract_version"] != LEGACY_CONTRACT \
                or any(original_registry["content"].get(key) is not True for key in ("can_read", "can_write", "can_create", "evidence_upload")):
            raise NotReady("maintenance freeze must start from the exact readable/writable legacy registry")
        policies = sql("""SELECT content FROM gov_protocol_policies WHERE scope_id=%s AND (domain_id=%s OR domain_id IS NULL)
            ORDER BY CASE WHEN domain_id IS NULL THEN 0 ELSE 1 END DESC,policy_seq DESC LIMIT 1""", (scope, domain))
        if len(policies) != 1 or policies[0]["content"].get("default_protocol") != LEGACY_PROTOCOL \
                or policies[0]["content"].get("default_contract_version") != LEGACY_CONTRACT \
                or policies[0]["content"].get("allow_legacy_create") is not True:
            raise NotReady("maintenance positives require current trusted legacy creation policy")
        clients = h.clients(url, f)
        report["status"] = "running"
        current_check = "backfill_replay_twice"
        replay_evidence = []
        baseline_backfill = h.snapshot(f)
        for number in (1, 2):
            before, storage = h.snapshot(f), h.storage_snapshot(scope)
            old_events = versioned_rows("gov_protocol_control_events")
            outcome = cli("backfill_legacy", "backfill-" + str(number))
            assert outcome.get("backfilled") == 0 and outcome.get("scope_id") == scope
            after = h.snapshot(f)
            assert _changed(before, after) <= {"gov_protocol_control_events"}, "repeated backfill changed Profile/binding/policy/registry/business state"
            append_only(old_events, versioned_rows("gov_protocol_control_events"))
            assert h.storage_snapshot(scope) == storage
            coverage = adapter.full_registration_coverage(f)
            assert coverage == registration
            replay_evidence.append({"attempt": number, "backfilled": 0, "changed_tables": sorted(_changed(before, after)),
                                    "full_registration": coverage, "sql_sha256": digest(after)})
        final_backfill = h.snapshot(f)
        assert _changed(baseline_backfill, final_backfill) <= {"gov_protocol_control_events"}
        mark(current_check, {"cli_replays": replay_evidence, "migration_executed": False})

        # Concrete valid HTTP positive controls. These are new, registered
        # synthetic facts; original delivery history is not modified.
        create_request = Client.command("create_object", {"object_type": "CompanyOutcome", "domain_id": domain,
            "payload": {"title": "Independent maintenance live write positive"}})
        canary_receipt = commit(create_request)
        canary_id = canary_receipt["result"]["object_id"]
        canary = readonly("GET", "/v1/objects/" + canary_id)
        binding = adapter.binding(f, canary_id)
        assert (binding["protocol_id"], binding["contract_version"]) == (LEGACY_PROTOCOL, LEGACY_CONTRACT)
        propose = Client.command("propose_revision", {"payload": {"title": "Independent maintenance valid revision positive"}}, target={
            "object_id": canary_id, "revision_id": canary["latest_revision_id"], "expected_version": canary["object_version"]})
        commit(propose)
        canary = readonly("GET", "/v1/objects/" + canary_id)
        evidence_bytes = b"Independent synthetic maintenance upload positive.\n"
        evidence_body = {"domain_id": domain, "title": "Independent maintenance evidence positive",
                         "media_type": "text/plain", "content_base64": base64.b64encode(evidence_bytes).decode()}
        evidence = clients["ceo"].json("POST", "/v1/evidence-assets", evidence_body)
        evidence_binding = adapter.binding(f, evidence["object_id"])
        assert (evidence_binding["protocol_id"], evidence_binding["contract_version"]) == (LEGACY_PROTOCOL, LEGACY_CONTRACT)
        evidence_path = f"/v1/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}"
        assert readonly("GET", evidence_path, binary=True) == evidence_bytes
        report["controls"].append({"operation": "real_http_upload_positive", "object_id": evidence["object_id"],
                                   "revision_id": evidence["revision_id"], "sha256": hashlib.sha256(evidence_bytes).hexdigest()})
        historical_ids = list(dict.fromkeys([*map(str, legacy_flow.objects), canary_id, evidence["object_id"]]))
        originals = {oid: readonly("GET", "/v1/objects/" + oid) for oid in historical_ids}
        receipt_path = "/v1/action-receipts/" + original_request["receipt"]["receipt_id"]
        historical_receipt = readonly("GET", receipt_path)
        assert readonly("POST", "/v1/actions", original_request["request"]) == original_request["receipt"]
        pre_freeze, storage = h.snapshot(f), h.storage_snapshot(scope)
        old_registry_rows, old_events = versioned_rows("gov_protocol_support_registry"), versioned_rows("gov_protocol_control_events")
        current_check = "new_write_disabled_history_readable"
        restore_needed = True  # A CLI exit/parser failure can follow a commit.
        frozen_response = cli("freeze_writes", "freeze-legacy")
        assert frozen_response.get("frozen") is True
        frozen = registry()
        assert frozen["contract_version"] == original_registry["contract_version"]
        assert frozen["registry_seq"] == original_registry["registry_seq"] + 1
        assert frozen["content"]["can_read"] is True
        assert all(frozen["content"][key] is False for key in ("can_write", "can_create", "evidence_upload"))
        for key, value in original_registry["content"].items():
            if key not in {"can_write", "can_create", "evidence_upload", "notes"}:
                assert frozen["content"].get(key) == value, "freeze changed a non-write support setting"
        post_freeze = h.snapshot(f)
        assert _changed(pre_freeze, post_freeze) == {"gov_protocol_support_registry", "gov_protocol_control_events"}
        append_only(old_registry_rows, versioned_rows("gov_protocol_support_registry"), minimum=1)
        append_only(old_events, versioned_rows("gov_protocol_control_events"), minimum=1)
        assert h.storage_snapshot(scope) == storage
        for oid, previous in originals.items():
            _object_history_equal(previous, readonly("GET", "/v1/objects/" + oid))
        assert readonly("GET", receipt_path) == historical_receipt
        assert readonly("POST", "/v1/actions", original_request["request"]) == original_request["receipt"]
        assert readonly("GET", evidence_path, binary=True) == evidence_bytes
        assert h.snapshot(f) == post_freeze
        mark(current_check, {"frozen_registry_seq": frozen["registry_seq"], "can_read": True,
            "historical_objects_read": historical_ids, "original_receipt_id": original_request["receipt"]["receipt_id"],
            "same_request_same_receipt": True, "evidence_bytes_unchanged": True})

        current_check = "disabled_no_legacy_fallback"
        negative_requests = [Client.command("create_object", {"object_type": "CompanyOutcome", "domain_id": domain,
                                "payload": {"title": "Frozen create must not choose a fallback handler"}}),
                             Client.command("propose_revision", {"payload": {"title": "Frozen propose must not choose a fallback handler"}},
                                target={"object_id": canary_id, "revision_id": canary["latest_revision_id"],
                                        "expected_version": canary["object_version"]})]
        denials = []
        for request in negative_requests:
            for declared in (None, LEGACY_CONTRACT):
                candidate = deepcopy(request)
                candidate["idempotency_key"] = "maintenance-frozen-" + uuid.uuid4().hex
                if declared is not None:
                    candidate["contract_version"] = declared
                for path in ("/v1/actions/prepare", "/v1/actions"):
                    denial = h.rejection(f, clients["ceo"], path, candidate, 409, "PROTOCOL_WRITE_DISABLED", check_storage=True)
                    denials.append({"action": candidate["action_type"], "path": path, "declared": declared, **denial})
        denied_evidence = {**evidence_body, "title": "Frozen upload must fail before writing object storage"}
        denials.append({"action": "upload_evidence", **h.rejection(f, clients["ceo"], "/v1/evidence-assets",
                       denied_evidence, 409, "PROTOCOL_WRITE_DISABLED", check_storage=True)})
        assert len(denials) == 9 and h.snapshot(f) == post_freeze and h.storage_snapshot(scope) == storage
        mark(current_check, {"frozen_requests": denials, "successful_control_types": ["create_object", "propose_revision", "upload_evidence"],
                            "existing_object_binding_unchanged": True})
        report["status"] = "passed"
        return report
    except NotReady:
        report["status"] = "not_ready"
        raise
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = "SQLSTATE " + str(exc.sqlstate) if isinstance(exc, psycopg.Error) else type(exc).__name__ + ": " + str(exc)
        if current_check is not None:
            report["checks"][current_check] = {"passed": False, "status": "failed", "evidence": report["error"]}
        raise
    finally:
        try:
            if restore_needed and original_registry is not None:
                before, storage = h.snapshot(f), h.storage_snapshot(scope)
                old_registries, old_events = versioned_rows("gov_protocol_support_registry"), versioned_rows("gov_protocol_control_events")
                cli("set_registry", "restore-legacy", document=deepcopy(original_registry["content"]))
                restored = registry()
                assert restored["contract_version"] == original_registry["contract_version"]
                assert restored["content"] == original_registry["content"], "maintenance did not restore the exact original registry"
                after = h.snapshot(f)
                assert _changed(before, after) == {"gov_protocol_support_registry", "gov_protocol_control_events"}
                append_only(old_registries, versioned_rows("gov_protocol_support_registry"), minimum=1)
                append_only(old_events, versioned_rows("gov_protocol_control_events"), minimum=1)
                assert h.storage_snapshot(scope) == storage
                report["restoration"] = {"restored": True, "registry_seq": restored["registry_seq"],
                    "contract_version": restored["contract_version"], "content_sha256": digest(restored["content"])}
        except BaseException as exc:
            report["status"] = "failed"
            report["restoration"] = {"restored": False, "error": "SQLSTATE " + str(exc.sqlstate) if isinstance(exc, psycopg.Error) else type(exc).__name__ + ": " + str(exc)}
            for item in report["checks"].values():
                item["passed"] = False
                item["status"] = "failed"
            raise
        finally:
            public_json(h.output / "maintenance-cases.json", report)
            if clients:
                for client in clients.values():
                    client.close()
