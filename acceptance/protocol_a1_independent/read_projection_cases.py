"""A1-12 typed read projections, observed through real HTTP and independent SQL.

No DB/process work occurs at import. ``run_read_projections`` requires a frozen
contract before opening clients: ``metadata_key``,
``unsupported_interpretation_status``, ``unsupported_context_mode`` (``excluded``
or ``metadata_only``), and ``unsupported_context_reason`` (explicit null allowed
only for metadata_only). Optional ``legacy_interpretation_status`` defaults to
the frozen legacy_v0_2 meaning; ``source_metadata_key`` defaults to metadata_key.
Metadata keys can be dot-separated paths, but are never inferred from responses.

``legacy_ids`` accepts LegacyFlow.build() output or a mapping using object type
names to object IDs / {object_id, revision_id} references. This module does not
create fixtures, execute business transitions, or import Runtime implementation
validators. Narrative's network/two-transaction race deliberately stays not_run.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from .control_adapter import A_CONTRACT, A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .support import digest, public_json


REQUIRED_TYPES = ("CompanyOutcome", "BusinessCommitment", "ExecutionCommitment", "WorkItem")
LEGACY_NAMES = {"outcome": "CompanyOutcome", "bc": "BusinessCommitment", "ec": "ExecutionCommitment",
                "work_item": "WorkItem", "feedback": "FeedbackThread", "deliverable": "Deliverable"}
INTERPRETATIONS = frozenset({"delivery", "delivery_review", "delivery_status", "outcome_assessment",
                            "outcome_achievement", "feedback", "dri", "acceptor", "baseline_revision_id",
                            "dri_assignment_id", "acceptor_assignment_id", "handshakes", "execution_authority"})


def _configuration(contract: dict) -> dict:
    required = {"metadata_key", "unsupported_interpretation_status", "unsupported_context_mode",
                "unsupported_context_reason"}
    if not isinstance(contract, dict) or required - contract.keys():
        raise NotReady("A1 read metadata and Context Pack contract has not been frozen")
    result = dict(contract)
    for key in ("metadata_key", "unsupported_interpretation_status"):
        if not isinstance(result[key], str) or not result[key].strip():
            raise NotReady("A1 read contract has an empty required field")
    if result["unsupported_context_mode"] not in {"excluded", "metadata_only"}:
        raise NotReady("unsupported Context Pack mode must be explicitly frozen")
    reason = result["unsupported_context_reason"]
    if (reason is None and result["unsupported_context_mode"] != "metadata_only") or (
            reason is not None and (not isinstance(reason, str) or not reason.strip())):
        raise NotReady("unsupported Context Pack reason must be explicitly frozen")
    result.setdefault("source_metadata_key", result["metadata_key"])
    result.setdefault("legacy_interpretation_status", "legacy_v0_2")
    for key in ("metadata_key", "source_metadata_key"):
        if not isinstance(result[key], str) or any(not part for part in result[key].split(".")):
            raise NotReady("metadata field path must be nonempty")
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _field(document: dict, path: str) -> Any:
    value: Any = document
    for part in path.split("."):
        assert isinstance(value, dict) and part in value, f"missing frozen metadata field {path}"
        value = value[part]
    return value


def _subset(original: Any, projected: Any, location: str = "$") -> None:
    if isinstance(original, dict):
        assert isinstance(projected, dict), f"historical projection changed at {location}"
        for key, value in original.items():
            assert key in projected, f"historical projection field missing at {location}.{key}"
            _subset(value, projected[key], location + "." + key)
    elif isinstance(original, list):
        assert isinstance(projected, list) and len(original) == len(projected), f"history list changed at {location}"
        for number, (before, after) in enumerate(zip(original, projected, strict=True)):
            _subset(before, after, f"{location}[{number}]")
    else:
        assert _json_value(original) == _json_value(projected), f"historical value changed at {location}"


def _no_legacy_interpretation(document: dict, *, role: str) -> None:
    # Source payloads remain raw evidence; only the surrounding projection is
    # tested. A legal raw payload may itself contain dri_assignment_id, etc.
    for key in INTERPRETATIONS:
        assert document.get(key) is None, f"A {role} exposed legacy interpretation: {key}"


def _metadata(document: dict, expected: dict, *, path: str, interpretation: str) -> None:
    value = _field(document, path)
    assert isinstance(value, dict), "protocol metadata must be an object"
    for key, reference in expected.items():
        assert value.get(key) == reference, f"HTTP protocol metadata differs from SQL binding: {key}"
    assert value.get("registration_status") == "registered"
    assert value.get("interpretation_status") == interpretation, "wrong read interpretation"


def _changed_tables(before: dict, after: dict) -> set[str]:
    assert set(before["tables"]) == set(after["tables"]), "read unexpectedly changed SQL table coverage"
    return {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}


def _validate_revision_page(page: dict, object_id: str, check_metadata) -> None:
    """A single-object revision page owns one explicit protocol envelope."""
    assert isinstance(page.get("items"), list), "revision page items missing"
    check_metadata(page, object_id)
    assert all(isinstance(item, dict) and item.get("object_id") == object_id for item in page["items"]), \
        "revision page contains a row belonging to another object"


def _validate_unsupported_responsibility(status: int, document: dict) -> None:
    """The frozen legacy-only endpoint returns an explicit public error."""
    assert status == 409 and isinstance(document, dict) and set(document) == {"error"}
    error = document["error"]
    assert isinstance(error, dict) and set(error) == {"code", "message"}
    assert error["code"] == "PROTOCOL_NOT_SUPPORTED"
    assert isinstance(error["message"], str) and error["message"]


def run_read_projections(harness, fixture: dict, *, url: str, typed_fixture: dict,
                         legacy_ids: dict, contract: dict) -> dict:
    """Run frozen HTTP projections; caller supplies an idle, isolated A1 fixture.

    Missing interface configuration raises NotReady before touching the harness.
    Failed assertions persist a failed partial report and re-raise. Even if all
    implemented checks pass, A1-12 remains incomplete until the separate Narrative
    race is executed and accepted by the caller's complete acceptance matrix.
    """
    config = _configuration(contract)
    types = typed_fixture.get("types", {})
    payloads = typed_fixture.get("payloads", {})
    assert set(REQUIRED_TYPES).issubset(types) and set(REQUIRED_TYPES).issubset(payloads), \
        "A1-12 needs A-bound CompanyOutcome/BC/EC/WorkItem, not only a sentinel"
    assert typed_fixture.get("record_origin") == "synthetic" and typed_fixture.get("business_success") is False
    legacy = {}
    for name, value in legacy_ids.items():
        kind = LEGACY_NAMES.get(name, name)
        if kind in (*REQUIRED_TYPES, "FeedbackThread", "Deliverable"):
            legacy[kind] = str(value["object_id"] if isinstance(value, dict) else value)
    assert set(REQUIRED_TYPES).issubset(legacy), "legacy comparison needs Outcome/BC/EC/WorkItem"
    rows: list[dict] = []
    report = {"gate": "a1_read_projections", "status": "incomplete", "checks": rows,
              "read_projection_passed": False, "contract_a1_accepted": False, "runtime_accepted": False,
              "narrative_network_second_transaction_recheck": "not_run", "frozen_contract": config}
    clients = harness.clients(url, fixture)
    ceo = clients["ceo"]
    scope = fixture["scope_id"]
    observed: dict[str, dict] = {}

    def done(name: str, **evidence: Any) -> None:
        rows.append({"check": name, "status": "passed", **evidence})

    def sql(statement: str, values: tuple = ()) -> list[dict]:
        return harness.sql(fixture, statement, values)

    def binding(object_id: str) -> dict:
        bindings = sql("SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s ORDER BY binding_version",
                       (scope, object_id))
        assert len(bindings) == 1, "initial protocol binding is missing or forked"
        row = _json_value(bindings[0])
        profiles = sql("SELECT canonical_hash FROM gov_method_profile_revisions WHERE scope_id=%s AND profile_id=%s AND revision=%s",
                       (scope, row["profile_id"], row["profile_revision"]))
        assert len(profiles) == 1 and profiles[0]["canonical_hash"] == row["profile_canonical_hash"]
        expected = {key: row[key] for key in ("protocol_id", "contract_version", "binding_version", "record_origin")}
        expected["method_profile_ref"] = {"profile_id": row["profile_id"], "revision": row["profile_revision"],
                                          "canonical_hash": row["profile_canonical_hash"]}
        return expected

    def check_metadata(document: dict, object_id: str, *, source: bool = False) -> None:
        expected = binding(object_id)
        protocol = (expected["protocol_id"], expected["contract_version"])
        assert protocol in {(A_PROTOCOL, A_CONTRACT), (LEGACY_PROTOCOL, LEGACY_CONTRACT)}
        interpretation = config["unsupported_interpretation_status"] if protocol == (A_PROTOCOL, A_CONTRACT) \
            else config["legacy_interpretation_status"]
        _metadata(document, expected, path=config["source_metadata_key" if source else "metadata_key"],
                  interpretation=interpretation)

    def check_source(source: dict) -> None:
        # A1 freezes source identity/current interpretation, not an additional
        # nested source_ref output field. When supplied, validate that field;
        # always verify the actual source through its authorized read surfaces.
        oid, rid = source["object_id"], source["revision_id"]
        if config["source_metadata_key"].split(".")[0] in source:
            check_metadata(source, oid, source=True)
        obj = ceo.json("GET", "/v1/objects/" + oid)
        revision = ceo.json("GET", f"/v1/objects/{oid}/revisions/{rid}")
        assert obj["object_id"] == oid and revision["revision_id"] == rid
        check_metadata(obj, oid)
        check_metadata(revision, oid)

    def paginate(path: str, params: dict, *, page_object_id: str | None = None) -> list[dict]:
        items, cursors = [], set()
        for _ in range(200):
            page = ceo.json("GET", path + "?" + urlencode(params))
            assert isinstance(page.get("items"), list), "workbench list items missing"
            if page_object_id is not None:
                _validate_revision_page(page, page_object_id, check_metadata)
            items.extend(page["items"])
            cursor = page.get("next_cursor")
            if cursor is None:
                return items
            assert isinstance(cursor, str) and cursor and cursor not in cursors, "pagination failed to advance"
            cursors.add(cursor)
            params = {**params, "cursor": cursor}
        raise AssertionError("read projection pagination exceeded its explicit acceptance bound")

    def snapshots() -> list[dict]:
        return sql("SELECT * FROM gov_context_snapshots WHERE scope_id=%s ORDER BY snapshot_id", (scope,))

    try:
        before_reads = harness.snapshot(fixture)
        storage_before = harness.storage_snapshot(scope)
        for is_a, collection in ((True, {kind: str(ref["object_id"]) for kind, ref in types.items()}),
                                  (False, legacy)):
            for kind, oid in collection.items():
                expected = binding(oid)
                assert (expected["protocol_id"], expected["contract_version"]) == (
                    (A_PROTOCOL, A_CONTRACT) if is_a else (LEGACY_PROTOCOL, LEGACY_CONTRACT))
                obj = ceo.object(oid)
                check_metadata(obj, oid)
                assert obj["object_type"] == kind
                observed[oid] = obj
                if is_a:
                    _no_legacy_interpretation(obj, role=kind)
                revision_id = str(types[kind]["revision_id"]) if is_a else str(obj["latest_revision_id"])
                revision = ceo.revision(oid, revision_id)
                check_metadata(revision, oid)
                stored = sql("SELECT payload,payload_hash FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
                             (scope, oid, revision_id))
                assert len(stored) == 1 and revision["payload"] == stored[0]["payload"]
                assert revision["payload_hash"] == stored[0]["payload_hash"]
                if is_a:
                    assert revision["payload"] == payloads[kind], "typed raw source payload was rewritten"
                    _no_legacy_interpretation(revision, role="revision")
                done(("a_" if is_a else "legacy_") + kind + "_object_and_revision", object_id=oid,
                     revision_id=revision_id, binding_sha256=digest(expected))
                listed = paginate(f"/v1/objects/{oid}/revisions", {"limit": 2}, page_object_id=oid)
                assert len({item["revision_id"] for item in listed}) == len(listed)
                assert revision_id in {item["revision_id"] for item in listed}
                for item in listed:
                    assert item["object_id"] == oid
                    # The single-object page envelope is mandatory above.
                    # Any additional per-row metadata must also agree; it
                    # cannot replace a missing or wrong page envelope.
                    if config["metadata_key"].split(".")[0] in item:
                        check_metadata(item, oid)
                    if is_a:
                        _no_legacy_interpretation(item, role="revision-list-item")
                done(("a_" if is_a else "legacy_") + kind + "_revision_list", count=len(listed))

        domains = {str(obj["domain_id"]) for obj in observed.values()}
        seen = set()
        for domain in sorted(domains):
            listed = paginate("/v1/objects", {"domain_id": domain, "limit": 2})
            assert len({item["object_id"] for item in listed}) == len(listed), "duplicate workbench object"
            for item in listed:
                oid = item["object_id"]
                if oid in observed:
                    check_metadata(item, oid)
                    if binding(oid)["protocol_id"] == A_PROTOCOL:
                        _no_legacy_interpretation(item, role="object-list-item")
                    seen.add(oid)
        assert seen == set(observed), "workbench omitted typed or legacy objects"
        done("all_typed_and_legacy_workbench_metadata", object_count=len(seen))

        for is_a, oid in ((True, str(types["WorkItem"]["object_id"])), (False, legacy["WorkItem"])):
            if is_a:
                state = sql("SELECT dri_assignment_id,acceptor_assignment_id FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s", (scope, oid))
                assert len(state) == 1 and all(state[0].values()), "typed adversarial state did not exist"
                prior, storage = harness.snapshot(fixture), harness.storage_snapshot(scope)
                response = ceo.request("GET", f"/v1/objects/{oid}/responsibility", expected=409)
                _validate_unsupported_responsibility(response.status_code, response.json())
                assert harness.snapshot(fixture) == prior and harness.storage_snapshot(scope) == storage
            else:
                responsibility = ceo.json("GET", f"/v1/objects/{oid}/responsibility")
                check_metadata(responsibility, oid)
                assert responsibility["object_id"] == oid
                state = sql("SELECT * FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s", (scope, oid))
                assert len(state) == 1
                state = _json_value(state[0])
                _subset(state, observed[oid]["delivery"]["state"])
                assert responsibility["baseline_revision_id"] == state["work_item_revision_id"]
                for role, field in (("dri", "dri_assignment_id"), ("acceptor", "acceptor_assignment_id")):
                    assignment = sql("SELECT assignment_id,principal_id,role,domain_id FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s",
                                     (scope, state[field]))
                    assert len(assignment) == 1
                    _subset(_json_value(assignment[0]), responsibility[role])
                assert observed[oid]["lifecycle_status"] == "delivery_accepted", "legacy delivery result lost"
                assert len(observed[oid]["delivery"]["submissions"]) == 2
                assert len(observed[oid]["delivery"]["acceptances"]) == 2
            done(("a_" if is_a else "legacy_") + "work_item_responsibility",
                 http_status=409 if is_a else 200, code="PROTOCOL_NOT_SUPPORTED" if is_a else None)
        assert observed[legacy["CompanyOutcome"]]["outcome_achievement"] == "not_assessed"
        if "FeedbackThread" in legacy:
            assert observed[legacy["FeedbackThread"]]["lifecycle_status"] == "investigating"
        done("legacy_delivery_outcome_feedback_meanings_independent")
        assert harness.snapshot(fixture) == before_reads, "ordinary GET changed durable SQL state"
        assert harness.storage_snapshot(scope) == storage_before, "ordinary GET changed S3 versions"
        done("ordinary_get_sql_and_s3_unchanged", snapshot_sha256=digest(before_reads))

        before_context = harness.snapshot(fixture)
        old_snapshots = snapshots()
        selected_at = datetime.now(timezone.utc).isoformat()
        object_ids = sorted(observed)
        pack = ceo.json("POST", "/v1/context-packs", {"object_ids": object_ids,
                        "valid_at": selected_at, "known_at": selected_at})
        after_context = harness.snapshot(fixture)
        assert _changed_tables(before_context, after_context) == {"gov_context_snapshots"}
        assert after_context["tables"]["gov_context_snapshots"]["row_count"] == \
            before_context["tables"]["gov_context_snapshots"]["row_count"] + 1
        current_snapshots = snapshots()
        old_by_id = {str(row["snapshot_id"]): row for row in old_snapshots}
        new_by_id = {str(row["snapshot_id"]): row for row in current_snapshots}
        assert set(new_by_id) - set(old_by_id) == {pack["context_snapshot_id"]}
        assert all(new_by_id[key] == row for key, row in old_by_id.items()), "stored historical snapshot was rewritten"
        stored_pack = new_by_id[pack["context_snapshot_id"]]
        _subset(stored_pack["selected"], pack["selected"])
        _subset(stored_pack["excluded"], pack["excluded"])
        assert harness.storage_snapshot(scope) == storage_before, "Context Pack changed S3 versions"
        done("context_post_only_appends_one_snapshot", context_snapshot_id=pack["context_snapshot_id"])

        selected = {item["object_id"]: item for item in pack["selected"]}
        excluded = {item["object_id"]: item for item in pack["excluded"]}
        assert len(selected) == len(pack["selected"]) and len(excluded) == len(pack["excluded"])
        assert not (selected.keys() & excluded.keys())
        assert selected.keys() | excluded.keys() == set(object_ids), "Context Pack dropped a requested object"
        for kind, ref in types.items():
            oid = str(ref["object_id"])
            destination = excluded if config["unsupported_context_mode"] == "excluded" else selected
            assert oid in destination, "A source did not follow the frozen unsupported context mode"
            item = destination[oid]
            check_metadata(item, oid)
            assert item.get("reason") != "no_effective_revision_at_requested_times", \
                "unsupported A semantics was misclassified as missing legacy effective history"
            if config["unsupported_context_reason"] is not None:
                assert item.get("reason") == config["unsupported_context_reason"]
            _no_legacy_interpretation(item, role="Context Pack " + kind)
            if "payload" in item:
                assert item["payload"] == payloads[kind]
        source_count = 0
        for item in [*pack["selected"], *pack["excluded"]]:
            check_metadata(item, item["object_id"])
            for source in item.get("source_refs", []):
                assert source.get("object_id") and source.get("revision_id"), "context source lost its identity"
                owners = sql("SELECT object_id::text FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                             (scope, source["revision_id"]))
                assert len(owners) == 1 and owners[0]["object_id"] == source["object_id"]
                check_source(source)
                source_count += 1
        assert source_count > 0, "source protocol checks require real selected legacy source_refs"
        assert legacy["CompanyOutcome"] in selected and selected[legacy["CompanyOutcome"]]["outcome_achievement"] == "not_assessed"
        done("context_unsupported_reason_and_all_source_protocols", source_refs=source_count,
             typed_types=sorted(types))

        before_get = harness.snapshot(fixture)
        read_back = ceo.json("GET", f"/v1/context-packs/{pack['context_snapshot_id']}")
        _subset(pack, read_back)
        for item in [*read_back["selected"], *read_back["excluded"]]:
            check_metadata(item, item["object_id"])
            for source in item.get("source_refs", []):
                check_source(source)
        assert harness.snapshot(fixture) == before_get, "GET Context Pack mutated its frozen stored snapshot"
        assert harness.storage_snapshot(scope) == storage_before
        done("context_get_preserves_snapshot_and_metadata")
        report["read_projection_passed"] = True
        return report
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        rows.append({"check": "narrative_network_second_transaction_recheck", "status": "not_run",
                     "reason": "requires separate controlled Narrative network race and final source revalidation"})
        report["passed"] = sum(row["status"] == "passed" for row in rows)
        report["not_run"] = sum(row["status"] == "not_run" for row in rows)
        public_json(harness.output / "read-projection-cases.json", report)
        for client in clients.values():
            client.close()
