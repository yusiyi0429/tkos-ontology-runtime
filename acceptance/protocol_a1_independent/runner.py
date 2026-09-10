"""Root-invoked independent A1 stages; no database/container lifecycle.

The default is an offline plan. --execute runs only explicitly selected stages
against a pre-provisioned one-off database. Missing stages/interfaces remain
NOT_READY; implementer self-tests and past JSON PASS flags are never imported
as acceptance checks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from psycopg.conninfo import conninfo_to_dict

from .control_adapter import ControlAdapter, FrozenControlContract, NotReady
from .golden import check as check_golden
from .matrix import REQUIRED
from .support import CaseBook, Harness, HERE, ROOT, public_json, source_manifest, digest, safe_traceback_frames


STAGES = {
    "golden": ("A1-09",), "history": ("A1-02", "A1-09", "A1-14"),
    "permissions": ("A1-11",), "legacy": ("A1-04", "A1-06", "A1-07"),
    "transactions": ("A1-04", "A1-09", "A1-10"),
    "profiles": ("A1-01", "A1-10"), "targets": ("A1-03", "A1-05"),
    "reads": ("A1-12",), "narrative": ("A1-12",), "adjustment": ("A1-05",),
    "db-controls": ("A1-01", "A1-11"), "worker": ("A1-09", "A1-13"),
    "revocation": ("A1-08",), "creation": ("A1-04",), "package": ("A1-14",),
    "binding-negatives": ("A1-07",),
    "maintenance": ("A1-14",),
}


class Runner:
    def __init__(self, args):
        self.args = args
        self.book = CaseBook(args.output, REQUIRED)
        self.start_manifest = source_manifest(args.source)
        self.not_ready = {}
        self.h = None
        self.fixture = None
        self.api_url = None
        self.flow = None
        self.legacy_result = None
        self.typed = None
        self.control = FrozenControlContract(args.control_contract)
        self.adapter = None
        self.scope_catalog_result = None
        self.binding_result = None

    def record(self, case, check, evidence):
        self.book.check(case, check, True, evidence=evidence)

    def current(self):
        if not self.args.env_file or not self.args.fixture:
            raise NotReady("a separate current synthetic fixture and private one-off environment are required")
        if self.h is None:
            fixture = json.loads(self.args.fixture.read_text())
            if self.args.history_fixture:
                old = json.loads(self.args.history_fixture.read_text())
                if old["scope_id"] == fixture["scope_id"]:
                    raise ValueError("mutation scenarios must not use the preserved preupgrade history scope")
            candidate = Harness(self.args.env_file, self.args.output, self.args.private)
            if not conninfo_to_dict(candidate.env.values["APP_DATABASE_URL"])["dbname"].startswith("tkos_a1_"):
                raise ValueError("A1 mutation stages require a one-off tkos_a1_* database")
            candidate.permissions()
            self.h, self.fixture = candidate, fixture
            self.adapter = ControlAdapter(self.h, self.args.source, self.control.module)
        return self.h, self.fixture

    def api(self):
        h, f = self.current()
        if self.api_url is None:
            _, self.api_url, ready = h.start_api(self.args.source, updates={
                "MEMORY_TENANT": f["tenant_id"], "MEMORY_ORG": f["company_id"]})
            public_json(h.output / "current-api-provenance.json", json.loads(ready.read_text()))
        return h, f, self.api_url

    def golden(self):
        result = check_golden(self.args.source, HERE / "legacy-request-golden.json",
                              report=self.args.output / "legacy-serialization.json")
        assert result["total"] == result["passed"] == 35 and result["failed"] == 0
        self.record("A1-09", "old_request_hash_35_golden_vectors", {"report": "legacy-serialization.json", "passed": 35})

    def history(self):
        if not all((self.args.env_file, self.args.history, self.args.history_fixture)):
            raise NotReady("history stage requires the real preupgrade history and private fixture")
        from .history import verify
        raw = json.loads(self.args.history.read_text())
        assert raw["source_kind"] == "real_pre_a1_api"
        assert raw["legacy_git_head"] == "3cd9109d726a9a9069a7960a2f2665ce677785d2"
        assert raw["migrations"][-1] == "0017_dri_delivery.sql"
        golden = json.loads((HERE / "legacy-request-golden.json").read_text())
        for name, expected in golden["source_files_sha256"].items():
            assert raw["source_manifest"][name] == expected
        self.record("A1-02", "real_old_source_and_schema", {"head": raw["legacy_git_head"], "schema": raw["migrations"][-1]})
        assert len(raw["history"]["commands"]) == 20
        assert raw["flow"]["delivery_accepted"] and raw["flow"]["outcome_achievement"] == "not_assessed"
        assert raw["flow"]["feedback_status"] == "investigating"
        self.record("A1-02", "old_http_history_created", {"commands": 20, "history_sha256": digest(raw)})
        verify(SimpleNamespace(env_file=self.args.env_file, source=self.args.source,
            output=self.args.output / "history", private=self.args.private / "history",
            history=self.args.history, fixture=self.args.history_fixture))
        evidence = {"report": "history/history-verification.json"}
        for name in ("original_sql_columns_unchanged", "original_object_revision_receipt_reads",
                     "original_context_and_evidence_unchanged", "no_ic_or_composition_success_backfilled"):
            self.record("A1-02", name, evidence)
        self.record("A1-09", "old_http_requests_replay_after_migration", evidence)
        # This verifies that the artifact and live isolated DB are the same
        # pre-A1 history. Creation/migration commands are root-owned evidence.
        self.record("A1-14", "second_oneoff_database_old_history", evidence)

    def permissions(self):
        h, f = self.current()
        result = h.permissions()
        public_json(h.output / "application-role-oracle.json", result)
        assert result["passed"]
        from .control_adapter import CONTROL_TABLES, BINDING_TABLE
        expected = set(CONTROL_TABLES) | {BINDING_TABLE}
        assert expected <= set(result["tables"])
        snapshot = h.snapshot(f)
        assert expected <= set(snapshot["tables"])
        self.record("A1-11", "real_app_nonowner_nonbypass", result["identity"])
        self.record("A1-11", "all_new_tables_oracles_and_rls", {name: result["tables"][name] for name in sorted(expected)})
        self.record("A1-11", "immutable_effective_table_column_rights", {"report": "application-role-oracle.json"})

    def legacy(self):
        from .legacy_flow import LegacyFlow
        h, f, url = self.api()
        self.flow = LegacyFlow(h, url, f)
        self.legacy_result = self.flow.build(include_negatives=True)
        registration = self.adapter.full_registration_coverage(f)
        bindings = {oid: self.adapter.binding(f, oid) for oid in self.flow.objects}
        assert all(row["protocol_id"] == "tkos.legacy-governed" and row["contract_version"] == "tkos.governed/v0.2"
                   for row in bindings.values())
        assert len(self.legacy_result["negative_results"]) == 7
        work = self.flow.object(self.legacy_result["work_item"])
        state = h.sql(f, "SELECT * FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s",
                      (f["scope_id"], self.legacy_result["work_item"]))[0]
        assert str(state["work_item_revision_id"]) == work["effective_revision_id"]
        assert str(state["dri_assignment_id"]) == f["actors"]["mission_dri"]["assignment_id"]
        assert str(state["acceptor_assignment_id"]) == f["actors"]["verifier"]["assignment_id"]
        assert work["latest_revision"]["payload"]["acceptance_criteria"] == [{"criterion_id": "source", "description": "Complete original source"}]
        evidence = {"flow": self.legacy_result, "registration": registration}
        public_json(h.output / "new-runtime-legacy-flow.json", evidence)
        for check in REQUIRED["A1-06"]:
            self.record("A1-06", check, {"report": "new-runtime-legacy-flow.json"})
        self.record("A1-07", "evidence_upload_atomic_binding", {"bindings": [bindings[e["object_id"]] for e in self.flow.evidence]})

    def transactions(self):
        from .transaction_cases import run_transactions
        h, f, url = self.api()
        result = run_transactions(h, f, url=url)
        self.record("A1-04", "trusted_legacy_create_atomic_binding", result["retry"])
        for name in ("checkpoint_reached_before_commit", "injected_failure_rolls_back_object_binding_receipt",
                     "same_key_retry_succeeds_once", "same_key_concurrent_create_no_fork"):
            self.record("A1-10", name, {"report": "transaction-cases.json"})
        for name in ("committed_response_loss_replays_same_receipt", "exactly_one_object_binding_event_receipt",
                     "same_key_changed_body_conflict"):
            self.record("A1-09", name, result["response_loss"])

    def profiles(self):
        if not self.args.profile or not self.args.contract_file:
            raise NotReady("profile stage requires the exact frozen profile-core and raw contract artifacts")
        from .profile_install_cases import run_profile_installs, run_concurrent_installs, run_cross_scope_concurrent_installs
        from .typed_fixtures import create_typed_fixtures
        h, f = self.current()
        result = run_profile_installs(h, f, self.adapter, self.control, self.args.profile, contract_file=self.args.contract_file,
            create_typed=lambda pid, rev: create_typed_fixtures(h, f, profile_id=pid, profile_revision=rev))
        self.typed = result["typed_fixture"]
        for name in ("install_exact_p1", "same_profile_idempotent", "different_content_conflict",
                     "wrong_hash_rejected", "wrong_contract_ref_rejected", "p2_p3_do_not_rebind_n1"):
            self.record("A1-01", name, {"report": "profile-install-cases.json", "input_count": len(result["profile_inputs"])})
        concurrent = run_concurrent_installs(h, f, self.adapter, self.control, self.args.profile, contract_file=self.args.contract_file)
        cross_scope = run_cross_scope_concurrent_installs(h, f, self.adapter, self.control, self.args.profile, contract_file=self.args.contract_file)
        self.record("A1-10", "concurrent_profile_or_binding_conflict_atomic", {"same_scope": concurrent, "cross_scope": cross_scope})

    def targets(self):
        if self.typed is None or self.flow is None:
            raise NotReady("targets stage requires profiles and a live independent legacy flow")
        from .http_fence_cases import run_target_fences
        from .typed_fence_cases import run_typed_fences
        h, f, url = self.api()
        # The early parameter gate may use a typed Outcome; the dedicated
        # handler checks below use each corresponding old object type.
        result = run_target_fences(h, f, url=url, sentinel_id=self.typed["types"]["CompanyOutcome"]["object_id"])
        for name in REQUIRED["A1-03"]:
            self.record("A1-03", name, {"report": "target-protocol-fences.json", "http_checks": len(result["checks"])})
        typed = run_typed_fences(h, f, url=url, typed_fixture=self.typed, legacy_flow=self.flow, legacy_result=self.legacy_result)
        for name in ("generic_propose_fenced", "accept_activate_fenced", "dedicated_delivery_fenced",
                     "legacy_target_new_dependency_fenced", "unknown_fields_strict"):
            self.record("A1-05", name, {"report": "typed-target-fences.json", "http_checks": len(typed["checks"])})

    def reads(self):
        if self.typed is None or self.legacy_result is None:
            raise NotReady("read stage requires current legacy and typed synthetic fixtures")
        from .read_projection_cases import run_read_projections
        h, f, url = self.api()
        result = run_read_projections(h, f, url=url, typed_fixture=self.typed,
            legacy_ids=self.legacy_result, contract=self.control.content.get("read_contract", {}))
        public_json(h.output / "read-projection-cases.json", result)
        assert result["read_projection_passed"] is True
        from .assessment_snapshot_cases import run_assessment_snapshot
        run_assessment_snapshot(h, f, legacy_flow=self.flow, legacy_result=self.legacy_result)
        from .read_binding_cases import run_read_binding_cases
        main_before, main_storage = h.snapshot(f), h.storage_snapshot(f["scope_id"])
        derived = run_read_binding_cases(h, source=self.args.source, adapter=self.adapter, control=self.control,
            profile_file=self.args.profile, contract_file=self.args.contract_file)
        assert derived["status"] == "passed" and all(row["scope_id"] != f["scope_id"] for row in derived["cases"])
        assert h.snapshot(f) == main_before and h.storage_snapshot(f["scope_id"]) == main_storage
        for name in ("objects_revisions_profile_metadata", "context_pack_protocol_metadata",
                     "workbench_direct_sql_metadata", "unsupported_interpretation_explicit",
                     "ordinary_reads_no_business_write", "context_post_only_audit_snapshot"):
            self.record("A1-12", name, {"report": "read-projection-cases.json", "derived_reads": "read-binding-cases.json"})
        before = h.snapshot(f)
        for original in self.flow.receipts:
            projected = self.flow.ceo.json("GET", f"/v1/action-receipts/{original['receipt_id']}")
            assert projected["receipt"] == original, "read projection rewrote an original frozen receipt"
        assert h.snapshot(f) == before
        self.record("A1-12", "receipt_original_meaning_preserved", {"real_http_receipts": len(self.flow.receipts)})

    def narrative(self):
        if self.typed is None or self.legacy_result is None:
            raise NotReady("Narrative stage requires typed and legacy fixtures")
        from .narrative_cases import run_narrative_cases
        h, f = self.current()
        result = run_narrative_cases(h, f, source=self.args.source, typed_fixture=self.typed,
            legacy_result=self.legacy_result, adapter=self.adapter, control=self.control,
            profile_file=self.args.profile, contract_file=self.args.contract_file)
        for name, check in result["checks"].items():
            assert check.get("passed") is True
            self.record("A1-12", name, check)

    def adjustment(self):
        if self.typed is None or self.flow is None:
            raise NotReady("adjustment stage requires typed and legacy fixtures")
        from .adjustment_cases import run_adjustment_cases
        h, f = self.current()
        result = run_adjustment_cases(h, f, legacy_flow=self.flow,
                                     legacy_result=self.legacy_result, typed_fixture=self.typed)
        assert result["passed"] is True
        self.record("A1-05", "adjustment_affected_set_fenced", {"report": "adjustment-affected-set-cases.json"})

    def db_controls(self):
        if not self.args.profile:
            raise NotReady("DB control probes require the frozen profile artifact")
        from .db_probe_specs import build_control_spec
        from .db_adversary import run_control_plane_probes
        h, f = self.current()
        oid = f["outcome"]["object_id"]
        binding = self.adapter.binding(f, oid)
        policy_seq = h.sql(f, "SELECT COALESCE(max(policy_seq),0)+1 AS n FROM gov_protocol_policies WHERE scope_id=%s AND domain_id=%s",
                          (f["scope_id"], f["domain_id"]))[0]["n"]
        registry_seq = h.sql(f, "SELECT COALESCE(max(registry_seq),0)+1 AS n FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s",
                            (f["scope_id"], "tkos.contract-a"))[0]["n"]
        spec = build_control_spec(f["scope_id"], f["domain_id"], oid, self.args.profile,
            existing_binding=binding, policy_seq=policy_seq, registry_seq=registry_seq)
        result = run_control_plane_probes(self.args.env_file, spec, execute=True)
        public_json(h.output / "db-control-probes.json", result)
        assert result["status"] == "passed", "application control-plane denial failed"
        self.record("A1-11", "broad_insert_grant_cannot_register_profile_policy", {"report": "db-control-probes.json"})
        self.record("A1-11", "app_cannot_rebind", {"report": "db-control-probes.json"})
        from .db_scope_cases import run_scope_and_function_catalog
        foreign = json.loads(self.args.history_fixture.read_text()) if self.args.history_fixture else None
        self.scope_catalog_result = run_scope_and_function_catalog(h, f, foreign_fixture=foreign,
            source=self.args.source, catalog=result["catalog"])
        self.record("A1-11", "security_definer_execute_catalog_and_denial_if_present", self.scope_catalog_result)
        if "profile_injection_denied" in self.book.cases["A1-03"]["checks"]:
            self.record("A1-01", "no_public_arbitrary_registration", {
                "broad_app_grants_and_guc_denied": "db-control-probes.json",
                "http_profile_protocol_actor_injection_denied": "target-protocol-fences.json"})

    def package(self):
        if not self.args.wheel and not self.args.bundle:
            raise NotReady("a final offline-built wheel or real offline bundle is required; src files are insufficient")
        from .package_probe import inspect_wheel, inspect_bundle
        result = inspect_bundle(self.args.bundle, expected_manifest=self.start_manifest) if self.args.bundle else inspect_wheel(self.args.wheel, expected_manifest=self.start_manifest)
        public_json(self.args.output / "wheel-verification.json", result)
        assert result["status"] == "passed", "actual wheel artifact verification failed"
        self.record("A1-14", "packaged_migration_profile_artifacts", {"report": "wheel-verification.json"})

    def creation(self):
        if self.typed is None or self.flow is None or not self.args.profile:
            raise NotReady("creation stage requires exact P1 and real current legacy/typed fixtures")
        from .creation_cases import run_creation_cases
        h, f, url = self.api()
        result = run_creation_cases(h, f, url=url, adapter=self.adapter, control=self.control,
            profile_file=self.args.profile, legacy_flow=self.flow, legacy_result=self.legacy_result)
        for name, check in result["checks"].items():
            assert check["passed"] is True
            self.record("A1-04", name, check)
        assert all(check.get("passed") is True for check in result["evidence_checks"].values())
        self.record("A1-07", "evidence_new_unknown_disabled_denied_before_s3", result["evidence_checks"])
        for name, check in result["authority_checks"].items():
            assert check["passed"] is True
            self.record("A1-05", name, check)

    def maintenance(self):
        if self.flow is None:
            raise NotReady("maintenance stage requires current real legacy history")
        from .maintenance_cases import run_maintenance
        h, f, url = self.api()
        result = run_maintenance(h, f, url=url, adapter=self.adapter, control=self.control,
                                 legacy_flow=self.flow, legacy_result=self.legacy_result)
        for name, check in result["checks"].items():
            assert check.get("passed") is True
            self.record("A1-14", name, check)
        from .external_evidence import verify_migration
        migration = verify_migration(h, self.args.migration_report, self.start_manifest, self.args.source)
        self.record("A1-14", "migration_replay_twice", migration)

    def binding_negatives(self):
        if self.typed is None or self.flow is None:
            raise NotReady("binding negatives require current typed and real legacy fixtures")
        from .binding_cases import run_binding_cases
        h, f, url = self.api()
        foreign = json.loads(self.args.history_fixture.read_text()) if self.args.history_fixture else None
        result = run_binding_cases(h, f, url=url, typed_fixture=self.typed,
            legacy_flow=self.flow, legacy_result=self.legacy_result, foreign_fixture=foreign)
        for name, check in result["checks"].items():
            assert check["passed"] is True
            self.record("A1-07", name, check)
        self.record("A1-04", "cross_protocol_parent_denied", result["checks"]["parent_binding_conflict_denied"])
        self.binding_result = result
        if self.scope_catalog_result and "cross_scope_binding_fk_denied" in result["checks"]:
            self.record("A1-11", "cross_scope_fk_and_reads", {"reads": self.scope_catalog_result,
                "fk": result["checks"]["cross_scope_binding_fk_denied"]})
        missing = result["checks"].get("missing_binding_denied", {}).get("evidence", {})
        if (missing.get("mode") == "database_rejected" and missing.get("sqlstate") == "23514"
                and "dedicated_delivery_fenced" in self.book.cases["A1-05"]["checks"]
                and "immutable_effective_table_column_rights" in self.book.cases["A1-11"]["checks"]):
            self.record("A1-13", "missing_or_mixed_binding_effect_denied", {
                "reachability": "missing binding cannot commit; mixed-protocol HTTP dependencies cannot produce authoritative effects",
                "deferred_commit_and_parent_checks": "binding-negative-cases.json",
                "all_a_business_commands_zero_outbox": "typed-target-fences.json",
                "actual_app_immutable_binding_rights": "application-role-oracle.json"})

    def revocation(self):
        if self.flow is None:
            raise NotReady("authority stage requires a real independent legacy flow")
        from .authority_cases import run_authority_cases
        h, f, url = self.api()
        result = run_authority_cases(h, f, url=url, legacy_flow=self.flow, legacy_result=self.legacy_result)
        for name, check in result["checks"].items():
            if check.get("passed") is True:
                self.record("A1-08", name, check)

    def worker(self):
        if self.flow is None or self.typed is None:
            raise NotReady("worker stage requires real current legacy tasks and typed metadata")
        from .new_worker_cases import run_new_worker_cases
        h, f, url = self.api()
        result = run_new_worker_cases(h, f, source=self.args.source, old_source=self.args.old_source,
            url=url, legacy_flow=self.flow, typed_fixture=self.typed, control=self.control)
        for name, check in result["checks"].items():
            assert check.get("passed") is True
            self.record("A1-13", name, check)
        assert result["replay_does_not_dispatch"]["passed"] is True
        self.record("A1-09", "replay_does_not_dispatch", result["replay_does_not_dispatch"])
        from .external_evidence import verify_worker
        cutover = verify_worker(h, self.args.worker_cutover_report, self.args.worker_preparation,
            manifest=self.start_manifest, source=self.args.source, old_source=self.args.old_source, history=self.args.history)
        for name, check in cutover.items():
            self.record("A1-13", name, check)

    def run(self):
        stages = self.args.stages.split(",") if self.args.stages != "all" else list(STAGES)
        if any(stage not in STAGES for stage in stages) or len(stages) != len(set(stages)):
            raise ValueError("unknown or duplicate acceptance stage")
        if not self.args.execute:
            return self.book.save(executed=False, stage_plan=stages, source_manifest=self.start_manifest,
                                  not_ready="offline plan; no database, HTTP or installation executed")
        try:
            for stage in stages:
                method = getattr(self, stage.replace("-", "_"), None)
                try:
                    if method is None:
                        raise NotReady(f"independent {stage} adapter is still being connected")
                    method()
                except NotReady as exc:
                    self.not_ready[stage] = str(exc)
                except Exception as exc:
                    # Avoid PostgreSQL DETAIL and exception repr, which can
                    # contain row data. A safe type/code plus stage is enough.
                    detail = {"exception_type": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None),
                              "frames": safe_traceback_frames(exc)}
                    for case in STAGES[stage]:
                        self.book.failure(case, f"{stage}: {json.dumps(detail)}")
                    public_json(self.args.output / (stage + "-failure.json"), detail)
                self.book.save(executed=True, source_manifest_start=self.start_manifest, not_ready=self.not_ready)
        finally:
            if self.flow:
                self.flow.close()
            if self.h:
                self.h.close()
            end_manifest = source_manifest(self.args.source)
            stable = self.start_manifest == end_manifest
            if not stable:
                for case in REQUIRED:
                    self.book.failure(case, "source changed during this run; observations cannot establish acceptance")
            result = self.book.save(executed=True, not_ready=self.not_ready,
                source_manifest_start=self.start_manifest, source_manifest_end=end_manifest,
                source_unchanged=stable, acceptance_evidence_trusted=stable)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stages", default="all")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "src")
    parser.add_argument("--old-source", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--history-fixture", type=Path)
    parser.add_argument("--migration-report", type=Path)
    parser.add_argument("--worker-cutover-report", type=Path)
    parser.add_argument("--worker-preparation", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--contract-file", type=Path)
    parser.add_argument("--control-contract", type=Path)
    package = parser.add_mutually_exclusive_group()
    package.add_argument("--wheel", type=Path)
    package.add_argument("--bundle", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    args = parser.parse_args()
    args.source = args.source.resolve()
    if (args.output / "report.json").exists():
        parser.error("use a new output directory; existing acceptance reports are not overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    result = Runner(args).run()
    print(json.dumps({key: result[key] for key in ("contract_a1_accepted", "passed", "failed", "not_complete")}))
    raise SystemExit(0 if result["contract_a1_accepted"] or not args.execute else 1)


if __name__ == "__main__":
    main()
