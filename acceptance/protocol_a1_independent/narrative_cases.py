"""A1-12 governed Narrative read and registry races, using real HTTP/SQL.

Requires control.content['narrative_contract'] with metadata_key,
unsupported_interpretation_status, unsupported_mode ('excluded'/'metadata_only'),
unsupported_reason (explicit null only for metadata_only), and races containing
legacy_read_disabled and legacy_registry_version_unknown. Each race freezes
mode='reject', status and code, or mode='reselect', status=200 plus the three
unsupported_* fields for the replacement facts. No HTTP result teaches the
probe its expected error code. Registry changes use the explicitly authorized
owner append path in a synthetic tkos_a1_* scope, followed by append restoration.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import uuid

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .control_adapter import A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .read_projection_cases import _metadata, _no_legacy_interpretation
from .support import HERE, digest, private_json, public_json, source_manifest, wait


RACES = ("legacy_read_disabled", "legacy_registry_version_unknown")
CHECK = "narrative_protocol_recheck_if_enabled"


def _publish_release(path: Path, token: str) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    private_json(temporary, {"barrier_token": token})
    temporary.replace(path)


def _configuration(control) -> dict:
    config = deepcopy(getattr(control, "content", {}).get("narrative_contract"))
    required = {"metadata_key", "unsupported_interpretation_status", "unsupported_mode", "unsupported_reason", "races"}
    if not isinstance(config, dict) or required - config.keys():
        raise NotReady("Narrative protocol read/race contract has not been frozen")
    if not isinstance(config["races"], dict) or any(not isinstance(config["races"].get(name), dict) for name in RACES):
        raise NotReady("both Narrative race expectations must be explicit objects")
    if not isinstance(config["metadata_key"], str) or not config["metadata_key"].strip():
        raise NotReady("Narrative metadata field is not frozen")
    config.setdefault("source_metadata_key", config["metadata_key"])
    config.setdefault("legacy_interpretation_status", "legacy_v0_2")
    for rule in [config, *[config["races"].get(name, {}) for name in RACES]]:
        if rule is not config and rule.get("mode") == "reject":
            if type(rule.get("status")) is not int or not 400 <= rule["status"] <= 599 \
                    or not isinstance(rule.get("code"), str) or not rule["code"]:
                raise NotReady("Narrative rejection requires one fixed status and code")
            continue
        if rule is not config and (rule.get("mode") != "reselect" or rule.get("status") != 200):
            raise NotReady("each Narrative race must freeze rejection or reselected facts")
        if rule.get("unsupported_mode") not in {"excluded", "metadata_only"} \
                or not isinstance(rule.get("unsupported_interpretation_status"), str) \
                or not rule["unsupported_interpretation_status"] \
                or "unsupported_reason" not in rule:
            raise NotReady("Narrative unsupported interpretation contract is incomplete")
        reason = rule["unsupported_reason"]
        if (reason is None and rule["unsupported_mode"] != "metadata_only") \
                or (reason is not None and (not isinstance(reason, str) or not reason)):
            raise NotReady("Narrative unsupported reason is not frozen")
    return config


def run_narrative_cases(h, f: dict, *, source: Path, typed_fixture: dict,
                        legacy_result: dict, adapter, control, profile_file=None, contract_file=None) -> dict:
    config = _configuration(control)  # Must precede every process/DB operation.
    if not profile_file or not contract_file:
        raise NotReady("Narrative binding-race acceptance requires exact P1 and main-contract files")
    source = Path(source).resolve()
    owner_info = conninfo_to_dict(h.env.values["MIGRATION_DATABASE_URL"])
    if not owner_info.get("dbname", "").startswith("tkos_a1_") \
            or typed_fixture.get("record_origin") != "synthetic" \
            or typed_fixture.get("business_success") is not False:
        raise NotReady("Narrative mutation probe requires the current synthetic one-off scope")
    types = typed_fixture.get("types", {})
    required_types = {"CompanyOutcome", "BusinessCommitment", "ExecutionCommitment", "WorkItem"}
    if not required_types <= types.keys() or not {"outcome", "bc", "ec", "work_item", "deliverable", "feedback"} <= legacy_result.keys():
        raise NotReady("Narrative requires real legacy history and four typed A metadata fixtures")
    legacy_ids = [legacy_result[name] for name in ("outcome", "bc", "ec", "work_item", "deliverable", "feedback")]
    typed_ids = [str(types[kind]["object_id"]) for kind in sorted(required_types)]
    scope = f["scope_id"]
    start_manifest = source_manifest(source)
    report = {"gate": "a1_narrative", "status": "not_run", "runtime_accepted": False,
              "contract_a1_accepted": False, "checks": {CHECK: {"passed": False, "status": "not_run", "evidence": None}},
              "frozen_contract": config, "baseline": None, "races": [], "source_manifest": start_manifest}
    processes, client_groups = [], []

    def sql(statement, params=()):
        return h.sql(f, statement, params)

    def epoch():
        rows = sql("SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s", (scope,))
        assert len(rows) == 1
        return rows[0]["auth_epoch"]

    def registry():
        rows = sql("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1",
                   (scope, LEGACY_PROTOCOL))
        assert len(rows) == 1
        return rows[0]

    def expected_metadata(oid):
        row = adapter.binding(f, oid)
        return {"protocol_id": row["protocol_id"], "contract_version": row["contract_version"],
                "binding_version": row["binding_version"], "record_origin": row["record_origin"],
                "method_profile_ref": {"profile_id": row["profile_id"], "revision": row["profile_revision"],
                                       "canonical_hash": row["profile_canonical_hash"]}}

    def check_facts(facts, requested, *, replacement=None):
        selected, excluded = facts["selected"], facts["excluded"]
        assert isinstance(selected, list) and isinstance(excluded, list)
        combined = selected + excluded
        assert len({item["object_id"] for item in combined}) == len(combined)
        assert {item["object_id"] for item in combined} == set(requested), "Narrative dropped requested typed metadata"
        source_ids = []
        for item in combined:
            oid = str(item["object_id"])
            expected = expected_metadata(oid)
            is_a = expected["protocol_id"] == A_PROTOCOL
            rule = config if is_a else replacement
            interpretation = rule["unsupported_interpretation_status"] if rule else config["legacy_interpretation_status"]
            _metadata(item, expected, path=config["metadata_key"], interpretation=interpretation)
            if rule:
                destination = excluded if rule["unsupported_mode"] == "excluded" else selected
                assert item in destination
                assert item.get("reason") != "no_effective_revision_at_requested_times"
                if rule["unsupported_reason"] is not None:
                    assert item.get("reason") == rule["unsupported_reason"]
                _no_legacy_interpretation(item, role="Narrative unsupported fact")
                assert item.get("mf_closure") is None and item.get("mf_acceptance") is None
            for ref in item.get("source_refs", []):
                expected_source = expected_metadata(str(ref["object_id"]))
                source_rule = config if expected_source["protocol_id"] == A_PROTOCOL else replacement
                source_interpretation = source_rule["unsupported_interpretation_status"] if source_rule else config["legacy_interpretation_status"]
                if config["source_metadata_key"].split(".")[0] in ref:
                    _metadata(ref, expected_source, path=config["source_metadata_key"], interpretation=source_interpretation)
                source_object = client.json("GET", "/v1/objects/" + str(ref["object_id"]))
                source_revision = client.json("GET", f"/v1/objects/{ref['object_id']}/revisions/{ref['revision_id']}")
                assert source_revision["revision_id"] == ref["revision_id"]
                for projected in (source_object, source_revision):
                    _metadata(projected, expected_source, path=config["metadata_key"], interpretation=source_interpretation)
                rows = sql("SELECT object_id::text,payload_hash FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                           (scope, ref["revision_id"]))
                assert len(rows) == 1 and rows[0]["object_id"] == ref["object_id"] and rows[0]["payload_hash"] == ref["payload_hash"]
                source_ids.append({"object_id": ref["object_id"], "revision_id": ref["revision_id"]})
        if replacement is None:
            assert source_ids, "Narrative source metadata check must not be vacuous"
        return {"selected_ids": [item["object_id"] for item in selected],
                "excluded_ids": [item["object_id"] for item in excluded], "source_refs": source_ids,
                "facts_sha256": digest(facts)}

    def start(label, *, barrier=False):
        folder = h.private / ("narrative-" + label + "-" + uuid.uuid4().hex)
        folder.mkdir(mode=0o700, parents=True)
        ready, paused, release = (folder / name for name in ("ready.json", "paused.json", "release.json"))
        token = uuid.uuid4().hex
        arguments = [sys.executable, "-I", str(HERE / "narrative_process.py"), "--source", str(source), "--ready-file", str(ready)]
        if barrier:
            arguments += ["--paused", str(paused), "--release", str(release), "--barrier-token", token]
        process = h.spawn("narrative-" + label, arguments, updates={"MEMORY_TENANT": f["tenant_id"],
            "MEMORY_ORG": f["company_id"], "TKOS_NARRATIVE_ENABLED": "1", "TKOS_NARRATIVE_LEGACY_ENABLED": "0",
            "TKOS_NARRATIVE_COMPRESSION": "none", "TKOS_NARRATIVE_DOMAIN_ID": f["domain_id"]})
        processes.append(process)
        def ready_state():
            if ready.exists():
                return json.loads(ready.read_text())
            if process.poll() is not None:
                raise AssertionError("Narrative process exited before readiness; inspect private/redacted log")
            return None
        info = wait(ready_state, timeout=25)
        assert info["source_root"] == str(source)
        for entry in info["provenance"].values():
            path = Path(entry["path"]).resolve()
            assert path.is_relative_to(source) and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert len(info["patches"]) == (1 if barrier else 0) and not info["http_management_endpoint_added"]
        clients = h.clients(info["url"], f)
        client_groups.append(clients)
        clients["ceo"].http.timeout = httpx.Timeout(90)
        return process, clients["ceo"], paused, release, token, info

    def append_registry(contract_version, content, label):
        before, storage, prior_epoch = h.snapshot(f), h.storage_snapshot(scope), epoch()
        old_rows = sql("SELECT to_jsonb(r)::text AS row FROM gov_protocol_support_registry r WHERE scope_id=%s ORDER BY registry_row_id", (scope,))
        with psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
            identity = conn.execute("SELECT current_user::text AS current,session_user::text AS session").fetchone()
            assert identity["current"] == identity["session"] == owner_info["user"]
            for key, value in (("app.governed_scope_id", scope), ("app.gov_control_plane", "on"),
                               ("app.runtime_write_capability", "tkos-runtime-a1")):
                conn.execute("SELECT set_config(%s,%s,true)", (key, value))
            conn.execute("SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (scope,))
            seq = conn.execute("SELECT COALESCE(max(registry_seq),0)+1 AS next FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s",
                               (scope, LEGACY_PROTOCOL)).fetchone()["next"]
            row = conn.execute("""INSERT INTO gov_protocol_support_registry(scope_id,protocol_id,contract_version,registry_seq,content,recorded_by)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING registry_row_id::text""", (scope, LEGACY_PROTOCOL,
                contract_version, seq, Jsonb(content), "independent-narrative-" + label)).fetchone()
            conn.execute("INSERT INTO gov_protocol_control_events(scope_id,event_type,detail,actor) VALUES(%s,%s,%s,%s)",
                (scope, "independent_narrative_registry_append", Jsonb({"record_origin": "synthetic",
                    "registry_row_id": row["registry_row_id"], "registry_seq": seq, "protocol_id": LEGACY_PROTOCOL,
                    "contract_version": contract_version, "label": label}), "independent-a1-12-narrative"))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        after = h.snapshot(f)
        assert set(before["tables"]) == set(after["tables"])
        changed = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
        assert changed == {"gov_protocol_support_registry", "gov_protocol_control_events"}
        for table in changed:
            assert after["tables"][table]["row_count"] == before["tables"][table]["row_count"] + 1
        new_rows = sql("SELECT to_jsonb(r)::text AS row FROM gov_protocol_support_registry r WHERE scope_id=%s AND registry_row_id<>%s ORDER BY registry_row_id",
                       (scope, row["registry_row_id"]))
        assert new_rows == old_rows, "registry append rewrote old versions"
        assert epoch() == prior_epoch, "protocol mutation test accidentally changed auth_epoch"
        assert h.storage_snapshot(scope) == storage
        latest = registry()
        assert latest["contract_version"] == contract_version and latest["content"] == content
        return {"registry_row_id": row["registry_row_id"], "registry_seq": seq, "auth_epoch": prior_epoch,
                "protocol_id": LEGACY_PROTOCOL, "contract_version": contract_version,
                "content_sha256": digest(content), "can_read": content.get("can_read"), "can_write": content.get("can_write"),
                "sql_sha256": digest(after), "changed_tables": sorted(changed)}

    def body(ids):
        return {"query": "Independent synthetic delivery Outcome MF interpretation", "include_raw": True,
                "domain_id": f["domain_id"], "object_ids": ids}

    try:
        original = registry()
        if original["contract_version"] != LEGACY_CONTRACT or original["content"].get("can_read") is not True:
            raise NotReady("Narrative race must start with the exact readable legacy registry")
        process, client, _, _, _, info = start("baseline")
        before, storage = h.snapshot(f), h.storage_snapshot(scope)
        baseline = client.json("POST", "/v1/context-graph/narrative", body(legacy_ids + typed_ids))
        assert h.snapshot(f) == before and h.storage_snapshot(scope) == storage, "normal Narrative wrote SQL/S3"
        assert baseline["provenance"]["legacy"] is None and baseline["provenance"]["compression_applied"] is False
        assert baseline["model"] == "deterministic-v1"
        facts_evidence = check_facts(baseline["governed_facts"], legacy_ids + typed_ids)
        report["baseline"] = {"status": 200, "source_provenance": info, **facts_evidence}
        h.stop(process)
        for label in RACES:
            rule = config["races"][label]
            old = registry()
            assert old["contract_version"] == original["contract_version"] and old["content"] == original["content"]
            process, client, paused, release, token, info = start(label, barrier=True)
            before, storage, old_epoch = h.snapshot(f), h.storage_snapshot(scope), epoch()
            race = {"id": label, "status": "not_run", "source_provenance": info}
            report["races"].append(race)
            changed = False
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(client.request, "POST", "/v1/context-graph/narrative", body(legacy_ids), expected=rule["status"])
            try:
                def is_paused():
                    if paused.exists():
                        return json.loads(paused.read_text())
                    if future.done():
                        future.result()
                        raise AssertionError("Narrative returned without reaching the real post-render barrier")
                    return None
                capture = wait(is_paused, timeout=25)
                assert capture["barrier_token"] == token and capture["phase"] == "after_real_render_before_final_database_transaction"
                assert not capture["input_modified"] and not capture["return_value_modified"]
                assert capture["facts_sha256"] == digest(capture["facts"])
                paused_evidence = check_facts(capture["facts"], legacy_ids)
                assert paused_evidence["selected_ids"], "race needs genuinely selected legacy facts"
                assert h.snapshot(f) == before and h.storage_snapshot(scope) == storage
                race["paused_facts"] = {**paused_evidence, "rendered_sha256": capture["original_result_sha256"]}
                content, version = deepcopy(old["content"]), old["contract_version"]
                if label == "legacy_read_disabled":
                    content["can_read"] = False
                else:
                    version = "tkos.governed/independent-unsupported-" + uuid.uuid4().hex
                # Set before the call: if a post-commit independent oracle
                # fails, restoration must still happen. A rolled-back rejected
                # mutation may receive an explicit same-content restore append.
                changed = True
                try:
                    race["mutation"] = append_registry(version, content, label)
                except psycopg.errors.CheckViolation:
                    if label == "legacy_registry_version_unknown":
                        race.update(status="not_ready", reason="schema rejects unknown registry contract; race mutation is unreachable")
                        raise NotReady("unknown registry version is prohibited by the actual schema") from None
                    raise
                after_mutation = h.snapshot(f)
                _publish_release(release, token)
                response = future.result(timeout=35)
                value = response.json()
                assert epoch() == old_epoch and h.snapshot(f) == after_mutation and h.storage_snapshot(scope) == storage
                if rule["mode"] == "reject":
                    assert value.get("error", {}).get("code") == rule["code"]
                    assert set(value) == {"error"}, "protocol race leaked captured facts in its rejected response"
                    encoded = json.dumps(value, ensure_ascii=False)
                    for item in capture["facts"]["selected"]:
                        assert item["revision_id"] not in encoded and item["payload_hash"] not in encoded
                else:
                    refreshed = value["governed_facts"]
                    check_facts(refreshed, legacy_ids, replacement=rule)
                    assert digest(refreshed) != capture["facts_sha256"]
                    for name in ("narrative", "narrative_raw"):
                        if value.get(name):
                            assert hashlib.sha256(value[name].encode()).hexdigest() != capture["original_result_sha256"]
                            assert capture["original_result"] not in value[name], "reselected response retained its old rendered facts"
                    # A new label around the same old legacy delivery/Outcome
                    # conclusions is caught by check_facts' interpretation gate.
                race.update(status="passed", http_status=response.status_code, code=value.get("error", {}).get("code"),
                            response_sha256=digest(value), auth_epoch_unchanged=True, narrative_sql_s3_unchanged=True)
            finally:
                if not release.exists():
                    _publish_release(release, token)
                # Ensure the active request has left its final transaction
                # before restoring the original registry through a new version.
                try:
                    if not future.done():
                        future.result(timeout=10)
                except Exception:
                    h.stop(process)
                if changed:
                    race["restoration"] = append_registry(old["contract_version"], deepcopy(old["content"]), label + "-restore")
                h.stop(process)
                executor.shutdown(wait=True, cancel_futures=True)
            assert source_manifest(source) == start_manifest, "Narrative implementation changed during independent acceptance"
        assert all(row["status"] == "passed" for row in report["races"]) and len(report["races"]) == 2
        from .narrative_binding_case import run_binding_change_case
        main_scope_before, main_storage_before = h.snapshot(f), h.storage_snapshot(scope)
        for scenario in ("fact", "source"):
            binding_race = run_binding_change_case(h, source=source, adapter=adapter, control=control,
                profile_file=profile_file, contract_file=contract_file, scenario=scenario)
            assert binding_race["status"] == "passed" and binding_race["scope_id"] != scope
            assert h.snapshot(f) == main_scope_before and h.storage_snapshot(scope) == main_storage_before
            report["races"].append(binding_race)
        report["checks"][CHECK] = {"passed": True, "status": "passed", "evidence": {
            "baseline": report["baseline"], "races": report["races"], "source_manifest_sha256": digest(start_manifest),
            "patch_scope": "real render_facts return pause only", "auth_epoch_changed": False,
            "legacy_graph_created": False, "llm_network_used": False}}
        report["status"] = "passed"
        return report
    except NotReady:
        report["status"] = "not_ready"
        raise
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = "SQLSTATE " + str(exc.sqlstate) if isinstance(exc, psycopg.Error) else type(exc).__name__ + ": " + str(exc)
        report["checks"][CHECK]["status"] = "failed"
        raise
    finally:
        for process in processes:
            h.stop(process)
        for clients in client_groups:
            for client in clients.values():
                client.close()
        public_json(h.output / "narrative-cases.json", report)
