"""B10: real legacy derived reads cannot outlive a legal A binding change.

Three separate scopes use actual bootstrap/HTTP business actions. Owner writes
only an audited version-2 metadata binding, after installing exact P1 with the
real CLI. Raw revisions and immutable receipts stay readable and unchanged.
No DB, HTTP or process work happens on import. This is part of existing A1-12.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import uuid

from psycopg.types.json import Jsonb
from acceptance.runtime.client import utc_now

from .binding_cases import _owner
from .control_adapter import A_CONTRACT, A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .legacy_flow import LegacyFlow
from .profile_cases import canonical_hash, CONTRACT_HASH, P1_HASH
from .read_projection_cases import _metadata, _no_legacy_interpretation
from .support import HERE, digest, public_json, safe_traceback_frames, source_manifest
from .worker_probe import start_receiver


SCENARIOS = ("selected_outcome", "assessment_work_item", "assessment_deliverable")


def run_read_binding_cases(h, *, source, adapter, control, profile_file, contract_file):
    if not profile_file or not contract_file or not Path(profile_file).is_file() or not Path(contract_file).is_file():
        raise NotReady("B10 requires the exact P1 and raw main-contract artifacts")
    core = json.loads(Path(profile_file).read_text())
    assert canonical_hash(core) == core["canonical_hash"] == P1_HASH
    assert hashlib.sha256(Path(contract_file).read_bytes()).hexdigest() == CONTRACT_HASH
    if "install_profile" not in control.content.get("operations", {}):
        raise NotReady("B10 requires the reviewed real Profile installation CLI")
    source = Path(source).resolve()
    manifest = source_manifest(source)
    report = {"gate": "a1_legacy_derived_read_binding_changes", "status": "not_run", "cases": [],
        "source_manifest": manifest, "expected_status": 409, "expected_code": "PROTOCOL_NOT_SUPPORTED",
        "contract_a1_accepted": False, "runtime_accepted": False,
        "coverage_boundary": "Real rebinding covers selected Outcome, review WorkItem, assessment WorkItem/Deliverable; observation/evidence rebinding is source review and offline helper coverage only"}
    try:
        for scenario in SCENARIOS:
            row = _scenario(h, source=source, adapter=adapter, control=control, core=core,
                profile_file=profile_file, contract_file=contract_file, scenario=scenario)
            report["cases"].append(row)
        assert len(report["cases"]) == 3 and all(row["status"] == "passed" for row in report["cases"])
        assert len({row["scope_id"] for row in report["cases"]}) == 3
        assert source_manifest(source) == manifest
        report.update(status="passed", source_unchanged=True)
        return report
    except BaseException as error:
        report.update(status="failed", exception_type=type(error).__name__, frames=safe_traceback_frames(error))
        raise
    finally:
        public_json(h.output / "read-binding-cases.json", report)


def _scenario(h, *, source, adapter, control, core, profile_file, contract_file, scenario):
    assert scenario in SCENARIOS
    tag = uuid.uuid4().hex
    folder = h.private / ("read-binding-" + tag)
    folder.mkdir(mode=0o700, parents=True)
    fixture_file = folder / "fixture.json"
    row = {"id": scenario, "status": "not_run", "separate_synthetic_scope": True,
        "business_success_seeded": False, "checks": []}
    flow = process = receiver = None
    try:
        seeded = h.spawn("read-binding-seed", [sys.executable, "-I", str(HERE / "source_process.py"), "seed",
            "--source", str(source), "--out", str(fixture_file), "--namespace", "runtime-acceptance-a1-read-binding-" + tag], owner=True)
        seeded.wait(timeout=30)
        assert seeded.returncode == 0 and fixture_file.is_file(), "real isolated read bootstrap failed"
        f = json.loads(fixture_file.read_text())
        scope = f["scope_id"]
        row["scope_id"] = scope

        def sql(statement, params=()):
            return h.sql(f, statement, params)

        assert sql("SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s", (scope,))[0]["n"] == 1
        control.invoke(adapter, "install_profile", "read-binding-" + scenario + "-install-p1", {
            "scope_id": scope, "domain_id": f["domain_id"], "profile_file": profile_file, "contract_file": contract_file,
            "reason": "Independent B10 separate synthetic read scope", "operator": "codex-independent-acceptance"})
        assert adapter.installed_profile(f, core["profile_id"], core["revision"])["content"] == core
        registry = sql("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1", (scope, A_PROTOCOL))
        assert len(registry) == 1 and registry[0]["contract_version"] == A_CONTRACT and registry[0]["content"]["can_read"] is True
        receiver = start_receiver(h, "read-binding-" + scenario)
        process, url, ready = h.start_api(source, updates={"MEMORY_TENANT": f["tenant_id"],
            "MEMORY_ORG": f["company_id"], "GOVERNED_EFFECT_URL": receiver.url + "/effects"})
        provenance = json.loads(ready.read_text())
        assert provenance["source_root"] == str(source)
        flow = LegacyFlow(h, url, f)
        ceo = flow.ceo

        def metadata(document, oid):
            # This isolated scenario intentionally appends v2. The general
            # adapter correctly requires a unique initial v1 for other cases,
            # so do not weaken it to observe this legal owner rebind.
            bindings = sql("SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s ORDER BY binding_version", (scope, oid))
            assert [item["binding_version"] for item in bindings] in ([1], [1, 2])
            binding = bindings[-1]
            profiles = sql("SELECT canonical_hash FROM gov_method_profile_revisions WHERE scope_id=%s AND profile_id=%s AND revision=%s",
                (scope, binding["profile_id"], binding["profile_revision"]))
            assert len(profiles) == 1 and profiles[0]["canonical_hash"] == binding["profile_canonical_hash"]
            expected = {key: binding[key] for key in ("protocol_id", "contract_version", "binding_version", "record_origin")}
            expected["method_profile_ref"] = {"profile_id": binding["profile_id"], "revision": binding["profile_revision"],
                                              "canonical_hash": binding["profile_canonical_hash"]}
            interpretation = "contract_a_metadata_read_only" if binding["protocol_id"] == A_PROTOCOL else "legacy_v0_2"
            _metadata(document, expected, path="protocol", interpretation=interpretation)

        def context_body(oid):
            now = utc_now()
            return {"object_ids": [oid], "valid_at": now, "known_at": now}

        packs, targets = {}, []
        if scenario == "selected_outcome":
            changed_oid = f["outcome"]["object_id"]
            # C4 uses a real unconfirmed Outcome; no owner temporal rewrite.
            draft = flow.create("CompanyOutcome", {"title": "Independent unconfirmed exclusion", "terms": {}, "upstream_refs": []})
            excluded = ceo.json("POST", "/v1/context-packs", context_body(draft))
            assert excluded["selected"] == [] and len(excluded["excluded"]) == 1
            assert excluded["excluded"][0]["object_id"] == draft
            assert excluded["excluded"][0]["reason"] == "no_effective_revision_at_requested_times"
            metadata(excluded["excluded"][0], draft)
            # Frozen pre-A1 service.py create_object: CompanyOutcome starts
            # proposed, and only MetricObservation/FeedbackThread auto-effect.
            proposed = flow.object(draft)
            assert proposed["lifecycle_status"] == "proposed" and proposed["effective_revision_id"] is None
            row["checks"].append({"check": "unconfirmed_legacy_excluded_has_exact_metadata", "status": "passed", "object_id": draft})
            targets = [changed_oid]
        else:
            result = flow.build()
            outcome = result["outcome"]
            observation = flow.create("MetricObservation", {"title": "Independent two-of-three actual observation",
                "metric_id": "independent-read-clients", "value": 2, "unit": "clients", "valid_from": utc_now(),
                "upstream_refs": [flow.ref(outcome)]})
            assessment = flow.act("ceo", "record_outcome_assessment", {"assessment_result": "not_achieved",
                "observation_revision_ids": [flow.ref(observation)["revision_id"]],
                "evidence_revision_ids": [flow.evidence[-1]["revision_id"]],
                "delivery_acceptance_ids": [result["acceptance"]["acceptance_id"]],
                "assessment_note": "Accepted delivery contains two clients against three; MF investigation remains open"}, oid=outcome)
            current = flow.object(outcome)
            assert current["outcome_achievement"] == "not_achieved"
            assert current["outcome_assessment"]["assessment_id"] == assessment["result"]["assessment_id"]
            assert current["outcome_assessment"]["delivery_acceptance_ids"] == [result["acceptance"]["acceptance_id"]]
            assert flow.object(result["work_item"])["lifecycle_status"] == "delivery_accepted"
            assert flow.object(result["feedback"])["lifecycle_status"] == "investigating"
            changed_oid = result["work_item"] if scenario == "assessment_work_item" else result["deliverable"]
            targets = [outcome]
            if scenario == "assessment_work_item":
                targets.append(result["deliverable"])
            row.update(real_assessment_id=assessment["result"]["assessment_id"],
                real_delivery_acceptance_id=result["acceptance"]["acceptance_id"],
                real_observation_revision_id=flow.ref(observation)["revision_id"],
                real_evidence_revision_id=flow.evidence[-1]["revision_id"],
                business_states_before={"delivery": "delivery_accepted", "outcome": "not_achieved", "mf": "investigating"})
        originals = {}
        for oid in targets:
            originals[oid] = ceo.object(oid)
            metadata(originals[oid], oid)
            pack = ceo.json("POST", "/v1/context-packs", context_body(oid))
            assert len(pack["selected"]) == 1 and pack["selected"][0]["object_id"] == oid and pack["excluded"] == []
            metadata(pack["selected"][0], oid)
            assert ceo.json("GET", "/v1/context-packs/" + pack["context_snapshot_id"]) == pack
            if originals[oid]["object_type"] == "CompanyOutcome":
                assert pack["selected"][0]["outcome_achievement"] == ("not_assessed" if scenario == "selected_outcome" else "not_achieved")
            else:
                assert originals[oid]["object_type"] == "Deliverable"
                assert pack["selected"][0]["delivery_review"]["verification_result"] == "accepted"
                assert str(pack["selected"][0]["delivery_review"]["work_item_object_id"]) == changed_oid
            packs[oid] = pack

        revision_rows = sql("SELECT object_id::text,revision_id::text FROM gov_object_revisions WHERE scope_id=%s ORDER BY revision_id", (scope,))
        revision_data = {item["revision_id"]: ceo.revision(item["object_id"], item["revision_id"]) for item in revision_rows}
        receipt_rows = sql("SELECT receipt_id::text FROM gov_action_receipts WHERE scope_id=%s ORDER BY receipt_id", (scope,))
        receipts = {item["receipt_id"]: ceo.json("GET", "/v1/action-receipts/" + item["receipt_id"]) for item in receipt_rows}
        assert revision_rows and receipts
        old_binding = adapter.binding(f, changed_oid)
        assert (old_binding["protocol_id"], old_binding["contract_version"], old_binding["binding_version"]) == (LEGACY_PROTOCOL, LEGACY_CONTRACT, 1)
        before, storage, external = h.snapshot(f), h.storage_snapshot(scope), receiver.snapshot()
        epoch = sql("SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s", (scope,))[0]["auth_epoch"]
        with _owner(h, f) as conn:
            assert conn.execute("SELECT gov_control_plane_on() AS enabled").fetchone()["enabled"] is True
            conn.execute("SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (scope,))
            conn.execute("""INSERT INTO gov_object_protocol_bindings
                (scope_id,object_id,binding_version,protocol_id,contract_version,profile_id,profile_revision,
                 profile_canonical_hash,record_origin,registered_by,detail)
                VALUES(%s,%s,2,%s,%s,%s,%s,%s,'synthetic','independent-read-binding',%s)""",
                (scope, changed_oid, A_PROTOCOL, A_CONTRACT, core["profile_id"], core["revision"], core["canonical_hash"],
                 Jsonb({"synthetic": True, "business_success": False, "reason": "B10 audited metadata rebind", "scenario": scenario})))
            conn.execute("INSERT INTO gov_protocol_control_events(scope_id,event_type,detail,actor) VALUES(%s,%s,%s,%s)",
                (scope, "independent_read_binding_append", Jsonb({"object_id": changed_oid, "binding_version": 2,
                 "record_origin": "synthetic", "business_success": False}), "codex-independent-acceptance"))
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        after = h.snapshot(f)
        changed = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
        assert changed == {"gov_object_protocol_bindings", "gov_protocol_control_events"}
        assert all(after["tables"][name]["row_count"] == before["tables"][name]["row_count"] + 1 for name in changed)
        bindings = sql("SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s ORDER BY binding_version", (scope, changed_oid))
        assert len(bindings) == 2 and bindings[0] == old_binding and bindings[1]["binding_version"] == 2
        assert bindings[1]["protocol_id"] == A_PROTOCOL and bindings[1]["contract_version"] == A_CONTRACT
        for oid in targets:
            if oid != changed_oid:
                assert adapter.binding(f, oid)["protocol_id"] == LEGACY_PROTOCOL
        assert sql("SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s", (scope,))[0]["auth_epoch"] == epoch
        assert sql("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1", (scope, A_PROTOCOL)) == registry

        def deny(method, path, body=None):
            value = ceo.json(method, path, body, expected=409)
            assert set(value) == {"error"} and value["error"]["code"] == "PROTOCOL_NOT_SUPPORTED"
            assert h.snapshot(f) == after and h.storage_snapshot(scope) == storage and receiver.snapshot() == external
            row["checks"].append({"check": "derived_read_rejected", "method": method, "path": path,
                "status": "passed", "http_status": 409, "code": value["error"]["code"]})

        for oid in targets:
            if scenario != "selected_outcome":
                deny("GET", "/v1/objects/" + oid)
                deny("POST", "/v1/context-packs", context_body(oid))
            deny("GET", "/v1/context-packs/" + packs[oid]["context_snapshot_id"])
        raw_object = ceo.object(changed_oid)
        metadata(raw_object, changed_oid)
        _no_legacy_interpretation(raw_object, role="B10 rebound raw object")
        for item in revision_rows:
            projected = ceo.revision(item["object_id"], item["revision_id"])
            metadata(projected, item["object_id"])
            assert {k: v for k, v in projected.items() if k != "protocol"} == {
                k: v for k, v in revision_data[item["revision_id"]].items() if k != "protocol"}
        for rid, original in receipts.items():
            assert ceo.json("GET", "/v1/action-receipts/" + rid) == original
        assert h.snapshot(f) == after and h.storage_snapshot(scope) == storage and receiver.snapshot() == external
        row.update(status="passed", changed_binding_object_id=changed_oid, binding_version_before=1, binding_version_after=2,
            raw_revisions_preserved=len(revision_data), immutable_receipts_preserved=len(receipts),
            real_http_command_count=len(flow.commands), source_provenance=provenance,
            original_snapshot_ids=[pack["context_snapshot_id"] for pack in packs.values()],
            auth_epoch_unchanged=True, zero_response_sql_s3_effects=True, effect_receiver_calls=external["total_calls"],
            business_snapshot_sha256=digest(after))
        return row
    except BaseException as error:
        row.update(status="failed", exception_type=type(error).__name__, frames=safe_traceback_frames(error))
        raise
    finally:
        if flow is not None:
            flow.close()
        if process is not None:
            h.stop(process)
        if receiver is not None:
            h.stop(receiver.process)
        public_json(h.output / ("read-binding-" + scenario + ".json"), row)
