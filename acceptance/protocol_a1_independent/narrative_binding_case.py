"""B09: real Narrative legacy-to-A binding change between its two transactions.

This validates the static counterexample within A1-12. The fixture is a separate
synthetic scope made by the real bootstrap, with its sole starting Outcome; no
DRI delivery success is fabricated. The owner installs exact P1 through the real
CLI and later appends a legal version-2 A binding plus a control audit. Only the
actual render result is paused, never changed. No DB work occurs on import.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import uuid

import httpx
from psycopg.types.json import Jsonb
from acceptance.runtime.client import Client, utc_now

from .binding_cases import _owner
from .control_adapter import A_CONTRACT, A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .profile_cases import canonical_hash, P1_HASH, CONTRACT_HASH
from .support import HERE, digest, private_json, public_json, source_manifest, safe_traceback_frames, wait
from .worker_probe import start_receiver


def run_binding_change_case(h, *, source, adapter, control, profile_file, contract_file, scenario="fact"):
    if scenario not in {"fact", "source"}:
        raise ValueError("B09 binding scenario must be fact or source")
    if not profile_file or not contract_file or not Path(profile_file).is_file() or not Path(contract_file).is_file():
        raise NotReady("B09 requires actual P1 and exact raw main-contract artifacts")
    core = json.loads(Path(profile_file).read_text())
    assert core["canonical_hash"] == canonical_hash(core) == P1_HASH
    assert hashlib.sha256(Path(contract_file).read_bytes()).hexdigest() == CONTRACT_HASH
    if "install_profile" not in control.content.get("operations", {}):
        raise NotReady("B09 requires the reviewed real Profile installation CLI")
    source = Path(source).resolve()
    start = source_manifest(source)
    tag = uuid.uuid4().hex
    folder = h.private / ("narrative-binding-" + tag)
    folder.mkdir(mode=0o700, parents=True)
    fixture_file, ready, paused, release = [folder / name for name in ("fixture.json", "ready.json", "paused.json", "release.json")]
    token = uuid.uuid4().hex
    def publish_release():
        # Child observes existence before reading: publish complete JSON with
        # an atomic replace, never a partially written final marker.
        temporary = release.with_name(release.name + "." + uuid.uuid4().hex + ".tmp")
        private_json(temporary, {"barrier_token": token})
        temporary.replace(release)
    report = {"id": "legacy_to_a_" + scenario + "_binding_between_narrative_transactions", "status": "not_run",
              "binding_changed_on": scenario,
              "expected_status": 409, "expected_code": "NARRATIVE_SOURCE_CHANGED",
              "static_counterexample": "A metadata remains readable but cannot validate a previously assembled legacy interpretation",
              "separate_synthetic_scope": True, "delivery_success_seeded": False,
              "contract_a1_accepted": False, "source_manifest": start}
    process = receiver = clients = future = executor = None
    try:
        seeded = h.spawn("narrative-binding-seed", [sys.executable, "-I", str(HERE / "source_process.py"), "seed",
            "--source", str(source), "--out", str(fixture_file), "--namespace", "runtime-acceptance-a1-narrative-binding-" + tag], owner=True)
        seeded.wait(timeout=30)
        assert seeded.returncode == 0 and fixture_file.is_file(), "real isolated Narrative bootstrap failed"
        f = json.loads(fixture_file.read_text())
        scope, oid, rid = f["scope_id"], f["outcome"]["object_id"], f["outcome"]["revision_id"]
        report.update(scope_id=scope, object_id=oid, revision_id=rid)
        def sql(statement, params=()):
            return h.sql(f, statement, params)
        assert sql("SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s", (scope,))[0]["n"] == 1
        original_binding = adapter.binding(f, oid)
        assert (original_binding["protocol_id"], original_binding["contract_version"], original_binding["binding_version"]) == (LEGACY_PROTOCOL, LEGACY_CONTRACT, 1)
        control.invoke(adapter, "install_profile", "narrative-binding-" + scenario + "-install-p1", {
            "scope_id": scope, "domain_id": f["domain_id"], "profile_file": profile_file, "contract_file": contract_file,
            "reason": "Independent B09 separate synthetic scope", "operator": "codex-independent-acceptance"})
        assert adapter.installed_profile(f, core["profile_id"], core["revision"])["content"] == core
        registry = sql("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1", (scope, A_PROTOCOL))
        assert len(registry) == 1 and registry[0]["contract_version"] == A_CONTRACT and registry[0]["content"]["can_read"] is True
        receiver = start_receiver(h, "narrative-binding")
        process = h.spawn("narrative-binding-race", [sys.executable, "-I", str(HERE / "narrative_process.py"),
            "--source", str(source), "--ready-file", str(ready), "--paused", str(paused),
            "--release", str(release), "--barrier-token", token], updates={
                "MEMORY_TENANT": f["tenant_id"], "MEMORY_ORG": f["company_id"],
                "TKOS_NARRATIVE_ENABLED": "1", "TKOS_NARRATIVE_LEGACY_ENABLED": "0",
                "TKOS_NARRATIVE_COMPRESSION": "none", "TKOS_NARRATIVE_DOMAIN_ID": f["domain_id"],
                "GOVERNED_EFFECT_URL": receiver.url + "/effects"})
        def ready_state():
            if ready.exists():
                return json.loads(ready.read_text())
            assert process.poll() is None, "Narrative process exited before readiness"
        info = wait(ready_state, timeout=25)
        assert info["source_root"] == str(source) and len(info["patches"]) == 1
        assert info["http_management_endpoint_added"] is False
        for entry in info["provenance"].values():
            path = Path(entry["path"]).resolve()
            assert path.is_relative_to(source) and entry["sha256"] == start[path.relative_to(source).as_posix()]
        clients = h.clients(info["url"], f)
        client = clients["ceo"]
        client.http.timeout = httpx.Timeout(90)
        fact_oid, fact_rid = oid, rid
        if scenario == "source":
            # A real HTTP observation creates the sole requested fact. Its
            # frozen upstream Outcome is only a source, so changing that
            # Outcome cannot be caught merely by the fact-level fence.
            request = Client.command("create_object", {"object_type": "MetricObservation", "domain_id": f["domain_id"],
                "payload": {"title": "Independent B09 actual observed source", "metric_id": "b09-source-observation",
                    "value": 1, "unit": "synthetic unit", "valid_from": utc_now(),
                    "upstream_refs": [{"object_id": oid, "revision_id": rid}]}})
            pre_prepare = h.snapshot(f)
            request["expected_versions"] = client.json("POST", "/v1/actions/prepare", request)["expected_versions"]
            assert h.snapshot(f) == pre_prepare
            receipt = client.json("POST", "/v1/actions", request)
            assert receipt["status"] == "committed" and receipt["effect_task_ids"] == []
            fact_oid = receipt["result"]["object_id"]
            fact = client.object(fact_oid)
            fact_rid = fact["latest_revision_id"]
            assert fact["object_type"] == "MetricObservation" and fact["protocol"]["interpretation_status"] == "legacy_v0_2"
            assert fact["latest_revision"]["payload"]["upstream_refs"] == [{"object_id": oid, "revision_id": rid}]
            assert fact_oid != oid
            report["real_http_source_fixture_receipt_id"] = receipt["receipt_id"]
        before, storage, external = h.snapshot(f), h.storage_snapshot(scope), receiver.snapshot()
        prior_epoch = sql("SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s", (scope,))[0]["auth_epoch"]
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(client.request, "POST", "/v1/context-graph/narrative", {
            "query": "Independent frozen source interpretation", "object_ids": [fact_oid],
            "domain_id": f["domain_id"], "include_raw": True}, expected=409)
        def captured():
            if paused.exists():
                return json.loads(paused.read_text())
            if future.done():
                future.result()
                raise AssertionError("Narrative did not reach the real post-render pause")
        capture = wait(captured, timeout=25)
        assert capture["phase"] == "after_real_render_before_final_database_transaction" and capture["barrier_token"] == token
        assert capture["input_modified"] is False and capture["return_value_modified"] is False
        assert capture["facts_sha256"] == digest(capture["facts"])
        selected = capture["facts"]["selected"]
        assert len(selected) == 1 and selected[0]["object_id"] == fact_oid and selected[0]["revision_id"] == fact_rid
        assert selected[0]["protocol"]["interpretation_status"] == "legacy_v0_2"
        if scenario == "fact":
            assert selected[0]["outcome_achievement"] == "not_assessed"
        else:
            assert oid not in {item["object_id"] for item in selected}
            assert {"object_id": oid, "revision_id": rid} in [
                {key: ref[key] for key in ("object_id", "revision_id")} for ref in selected[0]["source_refs"]]
            assert selected[0]["object_type"] == "MetricObservation"
        assert h.snapshot(f) == before and h.storage_snapshot(scope) == storage and receiver.snapshot() == external
        with _owner(h, f) as conn:
            assert conn.execute("SELECT gov_control_plane_on() AS enabled").fetchone()["enabled"] is True
            conn.execute("SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (scope,))
            conn.execute("""INSERT INTO gov_object_protocol_bindings
                (scope_id,object_id,binding_version,protocol_id,contract_version,profile_id,profile_revision,
                 profile_canonical_hash,record_origin,registered_by,detail)
                VALUES(%s,%s,2,%s,%s,%s,%s,%s,'synthetic','independent-narrative-binding-race',%s)""",
                (scope, oid, A_PROTOCOL, A_CONTRACT, core["profile_id"], core["revision"], core["canonical_hash"],
                 Jsonb({"synthetic": True, "business_success": False, "reason": "B09 actual binding change during read"})))
            conn.execute("INSERT INTO gov_protocol_control_events(scope_id,event_type,detail,actor) VALUES(%s,%s,%s,%s)",
                (scope, "independent_narrative_binding_append", Jsonb({"object_id": oid, "binding_version": 2,
                 "record_origin": "synthetic", "business_success": False}), "codex-independent-acceptance"))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        after = h.snapshot(f)
        changed = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
        assert changed == {"gov_object_protocol_bindings", "gov_protocol_control_events"}
        assert all(after["tables"][name]["row_count"] == before["tables"][name]["row_count"] + 1 for name in changed)
        bindings = sql("SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s ORDER BY binding_version", (scope, oid))
        assert len(bindings) == 2 and bindings[0] == original_binding
        assert bindings[1]["binding_version"] == 2 and bindings[1]["protocol_id"] == A_PROTOCOL
        if scenario == "source":
            unchanged_fact_binding = adapter.binding(f, fact_oid)
            assert unchanged_fact_binding["protocol_id"] == LEGACY_PROTOCOL and unchanged_fact_binding["binding_version"] == 1
        assert sql("SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s", (scope,))[0]["auth_epoch"] == prior_epoch
        assert sql("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1", (scope, A_PROTOCOL)) == registry
        assert h.storage_snapshot(scope) == storage and receiver.snapshot() == external
        publish_release()
        response = future.result(timeout=35)
        value = response.json()
        assert response.status_code == 409 and set(value) == {"error"} and value["error"]["code"] == "NARRATIVE_SOURCE_CHANGED"
        encoded = json.dumps(value, ensure_ascii=False)
        assert rid not in encoded and fact_rid not in encoded and selected[0]["payload_hash"] not in encoded
        assert capture["original_result"] not in encoded, "rejected response leaked the old rendered narrative"
        assert h.snapshot(f) == after and h.storage_snapshot(scope) == storage and receiver.snapshot() == external
        current = client.object(oid)
        assert current["protocol"]["interpretation_status"] == "contract_a_metadata_read_only", "B09 must retain A metadata readability"
        assert current["object_version"] == 1 and current["latest_revision_id"] == rid
        assert h.snapshot(f) == after and h.storage_snapshot(scope) == storage and receiver.snapshot() == external
        assert source_manifest(source) == start
        report.update(status="passed", source_provenance=info, captured_facts_sha256=capture["facts_sha256"],
            requested_fact_id=fact_oid, changed_binding_object_id=oid,
            http_status=409, code=value["error"]["code"], binding_version_before=1, binding_version_after=2,
            registry_can_read_unchanged=True, auth_epoch_unchanged=True, frozen_object_revision_unchanged=True,
            zero_response_sql_s3_effects=True, effect_receiver_calls=external["total_calls"],
            llm_network_used=False, response_sha256=digest(value))
        return report
    except BaseException as error:
        report.update(status="failed", exception_type=type(error).__name__, frames=safe_traceback_frames(error))
        raise
    finally:
        if future is not None:
            if not release.exists():
                publish_release()
            try:
                future.result(timeout=10)
            except BaseException:
                if process is not None:
                    h.stop(process)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if process is not None:
            h.stop(process)
        if clients is not None:
            for client in clients.values():
                client.close()
        if receiver is not None:
            h.stop(receiver.process)
        public_json(h.output / ("narrative-" + scenario + "-binding-change-case.json"), report)
