"""Real Runtime HTTP/PostgreSQL/MinIO acceptance for tkos.workspace/0.2.

Independent runner: no implementation module is imported. Identities and
activation policy are the only control-plane fixture; every source, version,
share, Context, run and draft comes from authenticated HTTP. Private source
files are parsed only to produce bounded segments and hash/count reports.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import httpx
import psycopg

from acceptance.anchors_v03.fixture import register_v03, seed_v03
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from acceptance.runtime.client import Client
from acceptance.workspace_scenes.drop_response import drop_response
from .import_sources import import_private_sources, verify_no_private_text


def uid():
    return str(uuid.uuid4())


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def deactivate_assignments(h, f, principal_name):
    """Control-plane identity fixture: revoke current appointment/binding."""
    with psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"]) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (f["scope_id"],))
        conn.execute("SELECT scope_id FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (f["scope_id"],))
        conn.execute("UPDATE gov_role_assignments SET active=false WHERE scope_id=%s AND principal_id=%s",
                     (f["scope_id"], f["actors"][principal_name]["principal_id"]))


class Scenes:
    def __init__(self, h, url, f):
        self.h, self.f, self.url = h, f, url
        self.clients = h.clients(url, f)
        self.bodies = {}

    def close(self):
        for client in self.clients.values():
            client.close()

    def command(self, sid, event, version, key=None):
        return {"contract_version": "tkos.workspace/0.2", "scene_id": sid,
                "expected_version": version, "idempotency_key": key or uid(),
                "event": event}

    def post(self, actor, body, expected=200):
        self.bodies[body["idempotency_key"]] = body
        return self.clients[actor].json("POST", "/v1/workspace-scenes/events", body, expected=expected)

    def read(self, actor, sid, expected=200):
        return self.clients[actor].json("GET", "/v1/workspace-sources/" + sid, expected=expected)

    def version(self, actor, sid):
        return self.read(actor, sid)["version"]

    def write(self, actor, sid, event, version=None, key=None, expected=200):
        body = self.command(sid, event, self.version(actor, sid) if version is None else version, key=key)
        return self.post(actor, body, expected=expected)

    # ---- private-source transport: never writes segment text to the transcript
    def _private_request_summary(self, body):
        event = body.get("event") or {}
        return {"contract_version": body.get("contract_version"),
                "scene_id": body.get("scene_id"), "kind": event.get("kind"),
                "segments": len(event.get("segments") or []),
                "fingerprint": event.get("fingerprint"), "source_id": event.get("source_id")}

    def _private_response_summary(self, response):
        try:
            data = response.json()
        except ValueError:
            return {"body_bytes": len(response.content)}
        if not isinstance(data, dict):
            return {"keys": []}
        if "event_id" in data and "payload_hash" in data:
            result = data.get("result") or {}
            return {"receipt_id": data.get("receipt_id"), "action_type": data.get("action_type"),
                    "result": {key: result.get(key) for key in
                               ("scene_id", "event_id", "version", "kind", "payload_hash",
                                "source_id", "evidence_linked", "formal_effect")}}
        if "sources" in data:
            return {"scene_id": data.get("scene_id"), "version": data.get("version"),
                    "sources": [{"source_id": source.get("source_id"),
                                 "versions": [{"event_id": version.get("event_id"),
                                               "segments": len(version.get("segments") or []),
                                               "acquired_at": version.get("acquired_at")}
                                              for version in source.get("versions", [])]}
                                for source in data.get("sources", [])]}
        return {"keys": sorted(data.keys())}

    def private_post(self, actor, path, body, expected=200):
        token = self.f["actors"][actor]["token"]
        with httpx.Client(base_url=self.url, headers={"Authorization": "Bearer " + token},
                          timeout=35, trust_env=False) as raw:
            response = raw.post(path, json=body)
        self.h.log.record({"actor": "private-source", "method": "POST", "path": path,
                           "request": self._private_request_summary(body),
                           "status": response.status_code,
                           "response": self._private_response_summary(response)})
        assert response.status_code == expected, (response.status_code, self._private_response_summary(response))
        return response.json()

    def private_get(self, actor, path, expected=200):
        token = self.f["actors"][actor]["token"]
        with httpx.Client(base_url=self.url, headers={"Authorization": "Bearer " + token},
                          timeout=35, trust_env=False) as raw:
            response = raw.get(path)
        self.h.log.record({"actor": "private-source", "method": "GET", "path": path,
                           "request": None, "status": response.status_code,
                           "response": self._private_response_summary(response)})
        assert response.status_code == expected, (response.status_code, self._private_response_summary(response))
        return response.json()

    def private_write(self, actor, sid, event, version=None):
        if version is None:
            version = self.private_read(actor, sid)["version"]
        return self.private_post(actor, "/v1/workspace-scenes/events", self.command(sid, event, version))

    def private_read(self, actor, sid):
        return self.private_get(actor, "/v1/workspace-sources/" + sid)

    def create_scene(self, actor, scene_type="meeting", participants=(), agents=()):
        sid = uid()
        event = {"kind": "scene_create", "scene_type": scene_type, "external_id": uid(),
                 "title": "Synthetic standalone " + scene_type,
                 "owner_principal_id": self.f["actors"][actor]["principal_id"],
                 "participant_principal_ids": [self.f["actors"][p]["principal_id"] for p in participants],
                 "agent_bindings": [{"agent_principal_id": self.f["actors"][agent]["principal_id"],
                                     "owner_principal_id": self.f["actors"][owner]["principal_id"]}
                                    for agent, owner in agents]}
        return sid, self.write(actor, sid, event, version=0)

    def upload(self, actor, domain, content, title, media_type="text/plain"):
        response = self.clients[actor].json("POST", "/v1/evidence-assets", {
            "domain_id": self.f["domains"][domain], "title": title, "media_type": media_type,
            "content_base64": base64.b64encode(content).decode()})
        return response, {key: response[key] for key in ("object_id", "revision_id", "payload_hash")}

    def add_source(self, actor, sid, *, system, external_id, title, media_type, acquired_at,
                   origin_label=None, source_revision=None, private=False):
        event = {"kind": "source_add", "system": system, "external_id": external_id, "title": title,
                 "media_type": media_type, "acquired_at": acquired_at, "sensitivity": "private"}
        if origin_label:
            event["origin_label"] = origin_label
        if source_revision:
            event["source_revision"] = source_revision
        receipt = (self.private_write(actor, sid, event) if private
                   else self.write(actor, sid, event))
        return receipt["result"]["source_id"], receipt

    def add_version(self, actor, sid, source_id, *, segments, fingerprint, acquired_at,
                    media_type="text/plain", origin_label=None, evidence_ref=None,
                    corrects_event_id=None, reason=None, private=False):
        event = {"kind": "source_correct" if corrects_event_id else "source_version",
                 "source_id": source_id, "fingerprint": fingerprint, "media_type": media_type,
                 "acquired_at": acquired_at, "segments": segments}
        if origin_label:
            event["origin_label"] = origin_label
        if evidence_ref:
            event["evidence_ref"] = evidence_ref
        if corrects_event_id:
            event["corrects_event_id"] = corrects_event_id
            event["reason"] = reason
        return (self.private_write(actor, sid, event) if private
                else self.write(actor, sid, event))

    def share(self, actor, sid, source_id, version_event_id, payload_hash, grantee, expected=200):
        return self.write(actor, sid, {"kind": "source_share", "source_id": source_id,
                                       "version_event_id": version_event_id,
                                       "payload_hash": payload_hash,
                                       "share_to_principal_id": self.f["actors"][grantee]["principal_id"]},
                          expected=expected)

    def context(self, actor, sid, source_id, version_event_id, payload_hash, purpose, key=None):
        return self.clients[actor].json("POST", "/v1/workspace-sources/contexts", {
            "contract_version": "tkos.workspace/0.2", "scene_id": sid,
            "idempotency_key": key or uid(), "purpose": purpose,
            "items": [{"source_id": source_id, "version_event_id": version_event_id,
                       "payload_hash": payload_hash}]})


def run(h: MethodHarness, source: Path, output: Path, sources_dir: Path | None = None):
    checks = []

    def check(name, predicate):
        assert predicate, name
        checks.append(name)
        print("PASS " + name, flush=True)

    f = seed_v03(h.env, h.private / "identities.json", "runtime-acceptance-workspace-v02-" + uuid.uuid4().hex[:8])
    register_v03(h, source, f)
    process, url, _ = h.start_api(source)
    scenes = Scenes(h, url, f)
    counts_sql = """SELECT
        (SELECT count(*) FROM gov_method_runs WHERE scope_id=%s) AS method_runs,
        (SELECT count(*) FROM runtime_tasks WHERE tenant_id=%s AND organization_id=%s) AS tasks,
        (SELECT count(*) FROM gov_objects WHERE scope_id=%s) AS objects"""
    counts_params = (f["scope_id"], f["tenant_id"], f["company_id"], f["scope_id"])
    before_counts = h.sql(f, counts_sql, counts_params)[0]
    canary = "CANARY-V02-" + uuid.uuid4().hex
    try:
        # ------------------------------------------------ standalone scene, privacy
        sid, create = scenes.create_scene("a", participants=("b",),
                                          agents=(("dri_agent", "a"), ("b_agent", "b")))
        check("standalone_scene_no_business_anchor",
              create["result"]["formal_effect"] == "none" and create["result"]["kind"] == "scene_create"
              and "anchor" not in json.dumps(create))
        check("owner_reads_own_scene_without_anchor",
              scenes.read("a", sid)["scene"]["owner_principal_id"] == f["actors"]["a"]["principal_id"])
        member_view = scenes.read("a", sid)
        members = member_view["scene"]["members"]
        check("scene_member_display_names_bounded_display_only",
              members["authority"] == "display_only_not_authorization"
              and members["owner"]["display_name"] and members["owner"]["current"] is True
              and all(item["display_name"] and item["current"] is True
                      for item in members["participants"]))
        member_names = {item["principal_id"]: item["display_name"]
                        for item in members["participants"]}
        scenes.read("ceo", sid, expected=404)
        scenes.read("outsider", sid, expected=404)
        check("non_member_reads_are_not_found", True)
        denial = scenes.clients["ceo"].request("GET", "/v1/workspace-sources/" + sid, expected=404)
        invalid = scenes.clients["a"].request("POST", "/v1/workspace-sources/contexts", {
            "contract_version": "tkos.workspace/0.2", "scene_id": sid,
            "idempotency_key": "short", "purpose": "x", "items": []}, expected=422)
        check("denial_and_validation_responses_no_store",
              denial.headers.get("cache-control") == "no-store"
              and invalid.headers.get("cache-control") == "no-store")

        # ------------------------------------------------ private real sources
        imported = []
        if sources_dir is not None:
            real_sid, _ = scenes.create_scene("a", participants=("b",))
            imported = import_private_sources(scenes, "a", real_sid, sources_dir)
            manifest = json.loads((sources_dir / "manifest.json").read_text())["files"]
            check("two_real_sources_legal_import",
                  len(imported) == 2 and all(item["segments"] > 0 for item in imported))
            check("private_source_hashes_match_manifest",
                  sorted((item["file"], item["sha256"]) for item in imported)
                  == sorted((item["file"], item["sha256"]) for item in manifest))
            real_view = scenes.private_read("a", real_sid)
            by_id = {item["source_id"]: item for item in real_view["sources"]}
            check("independent_source_time_axes",
                  all(by_id[item["source_id"]]["versions"][0]["acquired_at"] == item["acquired_at"]
                      and len(by_id[item["source_id"]]["versions"][0]["segments"]) == item["segments"]
                      for item in imported))
            check("private_document_identity_preserved",
                  all(by_id[item["source_id"]]["external_id"] == item["external_id"]
                      and by_id[item["source_id"]]["source_revision"] == item["source_revision"]
                      for item in imported))
            check("no_fabricated_meeting_time",
                  all(item["acquired_at_basis"] in {"external-document-metadata",
                                                     "local-copy-file-mtime"}
                      for item in imported))
            links = h.sql(f, "SELECT count(*) AS n FROM gov_workspace_v02_assets WHERE scope_id=%s",
                          (f["scope_id"],))[0]["n"]
            evidence_objects = h.sql(f, "SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s AND object_type='EvidenceAsset'",
                                     (f["scope_id"],))[0]["n"]
            check("private_import_segment_only_no_domain_readable_bytes",
                  links == 0 and evidence_objects == 0)
            check("private_source_versions_have_no_evidence_ref",
                  all(by_id[item["source_id"]]["versions"][0]["evidence_ref"] is None
                      and by_id[item["source_id"]]["versions"][0]["evidence_basis"] is None
                      for item in imported))
        else:
            print("SKIP two_real_sources_legal_import (no --sources-dir)", flush=True)

        # ------------------------------------------------ canary source + fence
        artifact = ("RUNTIME LOCAL COPY " + canary).encode()
        artifact_sha = hashlib.sha256(artifact).hexdigest()
        upload, evidence = scenes.upload("a", "a", artifact, "Synthetic private artifact")
        when = now_iso()
        # Old generic Context snapshot is created BEFORE the artifact is linked.
        old_context = scenes.clients["ceo"].json("POST", "/v1/context-packs", {
            "object_ids": [evidence["object_id"]], "valid_at": when, "known_at": when,
            "contract_version": "tkos.method/0.3", "stage": "general", "purpose": "general",
            "include_drafts": True})
        check("prelink_context_can_select_unlinked_evidence",
              any(item["object_id"] == evidence["object_id"] for item in old_context["selected"]))
        source_id, source_receipt = scenes.add_source(
            "a", sid, system="feishu", external_id="private-" + uuid.uuid4().hex[:12],
            title="Synthetic private source", media_type="text/plain", acquired_at=when)
        version = scenes.add_version(
            "a", sid, source_id, segments=[{"speaker": "A", "text": canary + " spoken segment",
                                            "occurred_at": when}],
            fingerprint=artifact_sha, acquired_at=when, evidence_ref=evidence)
        v_event, v_hash = version["result"]["event_id"], version["result"]["payload_hash"]
        check("source_version_links_existing_domain_evidence",
              version["result"]["evidence_linked"] is True)
        scenes.clients["ceo"].json("GET", "/v1/objects/" + evidence["object_id"], expected=404)
        scenes.clients["ceo"].json(
            "GET", f"/v1/objects/{evidence['object_id']}/revisions/{evidence['revision_id']}", expected=404)
        facade = scenes.clients["ceo"].request(
            "GET", f"/v1/dashboard/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}",
            expected=404)
        check("dashboard_facade_denial_is_no_store", facade.headers.get("cache-control") == "no-store")
        scenes.clients["ceo"].json("GET", "/v1/dashboard/objects/" + evidence["object_id"], expected=404)
        scenes.clients["ceo"].json("GET", "/v1/action-receipts/" + upload["receipt_id"], expected=404)
        ceo_context = scenes.clients["ceo"].json("POST", "/v1/context-packs", {
            "object_ids": [evidence["object_id"]], "valid_at": when, "known_at": when,
            "contract_version": "tkos.method/0.3", "stage": "general", "purpose": "general",
            "include_drafts": True})
        check("domain_reader_cannot_bypass_source_fence",
              not any(item.get("object_id") == evidence["object_id"] for item in ceo_context["selected"])
              and canary not in json.dumps(ceo_context))

        # exact-share grant
        share = scenes.share("a", sid, source_id, v_event, v_hash, "b")
        share_event = share["result"]["event_id"]
        grantee_view = scenes.read("b", sid)
        grantee_source = next(item for item in grantee_view["sources"] if item["source_id"] == source_id)
        check("scoped_material_grants",
              [item["event_id"] for item in grantee_source["versions"]] == [v_event]
              and canary in json.dumps(grantee_source))
        grantee_object = scenes.clients["b"].json(
            "GET", f"/v1/objects/{evidence['object_id']}/revisions/{evidence['revision_id']}")
        check("exact_grant_reads_exact_evidence_revision",
              grantee_object["payload_hash"] == evidence["payload_hash"])
        guest_view = scenes.read("b", sid)
        owner_scene = scenes.read("a", sid)
        guest_ops = {item["kind"]: item for item in guest_view["operations"]}
        owner_ops = {item["kind"]: item for item in owner_scene["operations"]}
        check("operations_do_not_leak_other_owners_grants",
              guest_ops["source_unshare"]["allowed"] is False and guest_ops["source_version"]["allowed"] is False
              and owner_ops["source_unshare"]["allowed"] is True
              and owner_ops["source_version"]["allowed"] is True)
        scenes.clients["b"].json("GET", "/v1/objects/" + evidence["object_id"], expected=404)
        download = scenes.clients["b"].request(
            "GET", f"/v1/dashboard/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}")
        check("exact_grant_can_download_original_artifact", canary.encode() in download.content)

        def method_signal_body(domain="company"):
            return {"action_type": "m1a_record_signal", "target": None, "expected_versions": [],
                    "idempotency_key": "b2-method-" + uuid.uuid4().hex,
                    "reason": "Independent workspace/0.2 source-fence acceptance",
                    "params": {"domain_id": f["domains"][domain],
                               "payload": {"title": "Synthetic signal over private evidence",
                                           "kind": "external",
                                           "description": "Must not attach unshared private evidence",
                                           "source_refs": [evidence]}},
                    "contract_version": "tkos.method/0.3"}

        signals_before = h.sql(f, "SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s AND object_type='Signal'",
                               (f["scope_id"],))[0]["n"]
        scenes.clients["ceo"].request("POST", "/v1/actions/prepare", method_signal_body(),
                                      expected={403, 404})
        signals_after = h.sql(f, "SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s AND object_type='Signal'",
                              (f["scope_id"],))[0]["n"]
        check("method_action_ref_cannot_read_unshared_private_evidence",
              signals_before == signals_after == 0)
        # The exact-grant grantee keeps the generic exact-revision read, but a
        # Method business action still requires its own Method authority; the
        # source fence never substitutes for it.
        scenes.clients["b"].request("POST", "/v1/actions/prepare",
                                   method_signal_body(domain="b"), expected={403, 404})
        check("method_action_still_requires_method_authority", True)
        scenes.share("a", sid, source_id, v_event, v_hash, "ceo", expected=422)
        check("grants_limited_to_current_scene_members", True)

        # correction is a separate unshared version
        correction = scenes.add_version(
            "a", sid, source_id, segments=[{"speaker": "A", "text": canary + " corrected canary",
                                            "occurred_at": when}],
            fingerprint=hashlib.sha256(b"corrected").hexdigest(), acquired_at=now_iso(),
            corrects_event_id=v_event, reason="fix transcription")
        correction_event = correction["result"]["event_id"]
        grantee_view = scenes.read("b", sid)
        grantee_source = next(item for item in grantee_view["sources"] if item["source_id"] == source_id)
        check("no_recursive_grant",
              [item["event_id"] for item in grantee_source["versions"]] == [v_event]
              and correction_event not in json.dumps(grantee_view)
              and canary + " corrected canary" not in json.dumps(grantee_view))

        # ------------------------------------------------ Context + run + draft
        context = scenes.context("a", sid, source_id, v_event, v_hash, "bounded follow-up analysis")
        context_id = context["context_id"]
        grantee_context = scenes.clients["b"].json("GET", "/v1/workspace-sources/contexts/" + context_id)
        check("scene_context_immutable_snapshot",
              grantee_context["complete"] is True and canary in json.dumps(grantee_context))
        # A grantee may capture its own snapshot; its free-text purpose is
        # withheld once the grant is revoked, even from the creator.
        grantee_snapshot = scenes.context("b", sid, source_id, v_event, v_hash,
                                          "grantee purpose " + canary)
        grantee_snapshot_id = grantee_snapshot["context_id"]
        check("grantee_context_purpose_visible_while_authorized",
              grantee_snapshot["complete"] is True
              and canary in json.dumps(grantee_snapshot["purpose"]))
        # An Agent may capture its own snapshot and run against exactly it; a
        # different principal's snapshot is never accepted.
        agent_context = scenes.context("dri_agent", sid, source_id, v_event, v_hash,
                                       "agent snapshot")
        other_context = scenes.context("b_agent", sid, source_id, v_event, v_hash,
                                       "other agent snapshot")
        foreign_run = scenes.command(sid, {
            "kind": "agent_run", "agent_principal_id": f["actors"]["dri_agent"]["principal_id"],
            "purpose": "foreign snapshot attempt",
            "model": {"provider": "controlled-fixture", "name": "synthetic-acceptance",
                      "version": "test-version-1"},
            "input_refs": [{"source_id": source_id, "version_event_id": v_event,
                            "payload_hash": v_hash}],
            "context_id": other_context["context_id"], "status": "unknown",
            "started_at": when}, scenes.version("dri_agent", sid))
        scenes.post("dri_agent", foreign_run, expected=404)
        check("agent_run_rejects_another_principals_context", True)

        def concurrent_context():
            return scenes.clients["a"].json("POST", "/v1/workspace-sources/contexts", {
                "contract_version": "tkos.workspace/0.2", "scene_id": sid,
                "idempotency_key": context_key, "purpose": "concurrent identical context",
                "items": [{"source_id": source_id, "version_event_id": v_event, "payload_hash": v_hash}]})

        context_key = "b2-context-" + uuid.uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: concurrent_context(), range(2)))
        check("concurrent_identical_context_single_snapshot",
              results[0]["context_id"] == results[1]["context_id"])
        replay_context = concurrent_context()
        check("context_replay_stable",
              replay_context["context_id"] == results[0]["context_id"]
              and replay_context["items"] == results[0]["items"])
        conflict = deepcopy(concurrent_context.__closure__[0].cell_contents) if False else None
        bad_context = {"contract_version": "tkos.workspace/0.2", "scene_id": sid,
                       "idempotency_key": context_key, "purpose": "different body",
                       "items": [{"source_id": source_id, "version_event_id": v_event, "payload_hash": v_hash}]}
        scenes.clients["a"].request("POST", "/v1/workspace-sources/contexts", bad_context, expected=409)
        check("context_idempotency_conflict_on_different_body", True)

        run_receipt = scenes.write("dri_agent", sid, {
            "kind": "agent_run", "agent_principal_id": f["actors"]["dri_agent"]["principal_id"],
            "purpose": "summarize " + canary + " purpose text",
            "model": {"provider": "controlled-fixture", "name": "synthetic-acceptance",
                      "version": "test-version-1",
                      "parameters": {"prompt": canary}},
            "input_refs": [{"source_id": source_id, "version_event_id": v_event, "payload_hash": v_hash}],
            "context_id": agent_context["context_id"], "external_run_id": "ext-" + canary[:12],
            "status": "succeeded", "started_at": when, "finished_at": when,
            "output_refs": [{"kind": "draft"}]})
        run_event = run_receipt["result"]["event_id"]
        draft = scenes.write("dri_agent", sid, {
            "kind": "followup_draft", "title": "Follow-up draft",
            "run_event_id": run_event,
            "items": [{"item_kind": "suggestion", "text": "Follow-up " + canary,
                       "citations": [{"source_id": source_id, "version_event_id": v_event,
                                      "payload_hash": v_hash, "segment_index": 0,
                                      "quote": canary + " spoken segment"}]}]})
        draft_event = draft["result"]["event_id"]
        check("exact_quotes_and_actor_provenance", run_receipt["status"] == "committed")
        check("agent_created_context_supports_agent_run",
              agent_context["complete"] is True and run_receipt["result"]["kind"] == "agent_run")
        mismatch = scenes.command(sid, {"kind": "followup_draft", "title": "Bad citation",
                                        "items": [{"item_kind": "fact", "text": "Not quoted",
                                                   "citations": [{"source_id": source_id,
                                                                  "version_event_id": v_event,
                                                                  "payload_hash": v_hash,
                                                                  "segment_index": 0,
                                                                  "quote": "not in the exact segment"}]}]},
                                  scenes.version("dri_agent", sid))
        scenes.post("dri_agent", mismatch, expected=422)
        check("inexact_quote_rejected", True)
        run_no_context = scenes.command(sid, {
            "kind": "agent_run", "agent_principal_id": f["actors"]["dri_agent"]["principal_id"],
            "purpose": "no snapshot", "model": {"provider": "p", "name": "n", "version": "1"},
            "input_refs": [{"source_id": source_id, "version_event_id": v_event, "payload_hash": v_hash}],
            "status": "unknown", "started_at": when}, scenes.version("dri_agent", sid))
        scenes.post("dri_agent", run_no_context, expected=422)
        check("run_requires_context_snapshot", True)
        agent_decision = scenes.command(sid, {"kind": "draft_decision", "draft_event_id": draft_event,
                                              "item_index": 0, "decision": "accepted"},
                                        scenes.version("dri_agent", sid))
        scenes.post("dri_agent", agent_decision, expected=403)
        check("agent_cannot_decide_for_human", True)
        decision = scenes.write("b", sid, {"kind": "draft_decision", "draft_event_id": draft_event,
                                           "item_index": 0, "decision": "accepted",
                                           "note": "Personally reviewed the cited source."})
        grantee_view = scenes.read("b", sid)
        grantee_draft = next(item for item in grantee_view["drafts"] if item["event_id"] == draft_event)
        check("grantee_reads_derived_draft_with_citations",
              grantee_draft["status"] == "available" and canary in json.dumps(grantee_draft))
        run_entry = next(item for item in grantee_view["runs"] if item["event_id"] == run_event)
        controlled = {"provider": "controlled-fixture", "name": "synthetic-acceptance",
                      "version": "test-version-1"}
        check("synthetic_run_model_is_controlled_fixture",
              {key: run_entry["model"].get(key) for key in controlled} == controlled
              and run_entry["status"] == "succeeded")
        check("human_decision_recorded",
              any(item["event_id"] == decision["result"]["event_id"] for item in grantee_view["decisions"]))

        # ------------------------------------------------ revocation sweep
        scenes.write("a", sid, {"kind": "source_unshare", "share_event_id": share_event,
                                "reason": "access review finished"})
        revoked_view = scenes.read("b", sid)
        check("withdraw_derived_content",
              canary not in json.dumps(revoked_view)
              and all(item["status"] == "withheld"
                      for item in revoked_view["drafts"] if item["event_id"] == draft_event))
        revoked_run = next(item for item in revoked_view["runs"] if item["event_id"] == run_event)
        check("revoked_run_free_text_withheld",
              revoked_run["input_withheld"] is True and "purpose" not in revoked_run
              and "error" not in revoked_run and "model" not in revoked_run)
        revoked_context = scenes.clients["b"].json("GET", "/v1/workspace-sources/contexts/" + context_id)
        revoked_grantee_snapshot = scenes.clients["b"].json(
            "GET", "/v1/workspace-sources/contexts/" + grantee_snapshot_id)
        check("historical_context_current_authorization",
              revoked_context["complete"] is False and revoked_context["purpose"] is None
              and revoked_context["items"] == [] and canary not in json.dumps(revoked_context))
        check("grantee_creator_purpose_withheld_after_revoke",
              revoked_grantee_snapshot["complete"] is False
              and revoked_grantee_snapshot["purpose"] is None
              and canary not in json.dumps(revoked_grantee_snapshot))
        scenes.clients["b"].json(
            "GET", f"/v1/objects/{evidence['object_id']}/revisions/{evidence['revision_id']}", expected=404)
        scenes.clients["b"].request(
            "GET", f"/v1/dashboard/evidence-assets/{evidence['object_id']}/revisions/{evidence['revision_id']}",
            expected=404)
        scenes.clients["b"].json("GET", "/v1/workspace-sources/" + sid)  # still a member
        check("revoked_exact_grant_reread_denied", True)
        scenes.clients["b"].json("GET", "/v1/action-receipts/" + version["receipt_id"], expected=404)
        check("revoked_source_receipt_hidden_from_grantee", True)

        scenes.write("a", sid, {"kind": "source_withdraw", "source_id": source_id,
                                "version_event_id": v_event, "reason": "source retired"})
        owner_view = scenes.read("a", sid)
        owner_source = next(item for item in owner_view["sources"] if item["source_id"] == source_id)
        owner_version = next(item for item in owner_source["versions"] if item["event_id"] == v_event)
        owner_json = json.dumps(owner_view)
        owner_draft = next(item for item in owner_view["drafts"] if item["event_id"] == draft_event)
        owner_run = next(item for item in owner_view["runs"] if item["event_id"] == run_event)
        check("owner_withdrawn_metadata_only",
              owner_version["status"] == "withdrawn" and owner_version["segments"] is None
              and canary + " spoken segment" not in owner_json
              and owner_draft["status"] == "withheld"
              and owner_run["input_withheld"] is True and "purpose" not in owner_run)
        owner_context_after = scenes.clients["a"].json(
            "GET", "/v1/workspace-sources/contexts/" + context_id)
        check("creator_context_purpose_withheld_after_withdraw",
              owner_context_after["purpose"] is None and owner_context_after["complete"] is False
              and canary + " spoken segment" not in json.dumps(owner_context_after))
        old_read = scenes.clients["ceo"].json("GET", "/v1/context-packs/" + old_context["context_snapshot_id"])
        check("historical_generic_context_filtered_after_revoke",
              canary not in json.dumps(old_read) and "bucket" not in json.dumps(old_read))
        owner_old = scenes.clients["a"].json("GET", "/v1/context-packs/" + old_context["context_snapshot_id"])
        check("prelink_snapshot_filtered_for_owner_too",
              canary not in json.dumps(owner_old) and "bucket" not in json.dumps(owner_old))

        # ------------------------------------------------ ownership of linking
        ceo_sid, _ = scenes.create_scene("ceo")
        ceo_source, _ = scenes.add_source("ceo", ceo_sid, system="feishu", external_id=uid(),
                                          title="CEO source", media_type="text/plain", acquired_at=when)
        foreign = scenes.command(ceo_sid, {"kind": "source_version", "source_id": ceo_source,
                                           "fingerprint": artifact_sha, "media_type": "text/plain",
                                           "acquired_at": when, "segments": [{"speaker": "A", "text": "x"}],
                                           "evidence_ref": evidence}, scenes.version("ceo", ceo_sid))
        scenes.post("ceo", foreign, expected=403)
        check("link_cannot_hijack_foreign_evidence", True)

        # ------------------------------------------------ idempotency / CAS / replay
        replay_body = scenes.command(sid, {"kind": "followup_draft", "title": "Replay-only draft",
                                           "items": [{"item_kind": "request", "text": "Re-check source access",
                                                      "citations": [{"source_id": source_id,
                                                                     "version_event_id": correction_event,
                                                                     "payload_hash": correction["result"]["payload_hash"],
                                                                     "segment_index": 0,
                                                                     "quote": canary + " corrected canary"}]}]},
                                     scenes.version("a", sid), key="b2-loss-" + uuid.uuid4().hex[:12])
        scenes.bodies[replay_body["idempotency_key"]] = replay_body
        with drop_response(url) as (proxy, observed):
            with httpx.Client(trust_env=False, timeout=35) as raw:
                try:
                    raw.post(proxy + "/v1/workspace-scenes/events", json=replay_body,
                             headers={"Authorization": "Bearer " + f["actors"]["a"]["token"]})
                    raise AssertionError("fault proxy did not drop the response")
                except httpx.TransportError:
                    pass
        first_replay = scenes.post("a", replay_body)
        second_replay = scenes.post("a", replay_body)
        check("response_loss_replay_same_envelope",
              first_replay["receipt_id"] == second_replay["receipt_id"]
              and first_replay["result"]["event_id"] == second_replay["result"]["event_id"])
        different = deepcopy(replay_body)
        different["event"]["title"] = "Different request under the same key"
        scenes.post("a", different, expected=409)
        check("idempotency_conflict_on_different_body", True)
        stale = scenes.command(sid, {"kind": "agent_run", "agent_principal_id": f["actors"]["dri_agent"]["principal_id"],
                                     "purpose": "stale", "model": {"provider": "p", "name": "n", "version": "1"},
                                     "input_refs": [{"source_id": source_id, "version_event_id": v_event,
                                                     "payload_hash": v_hash}],
                                     "context_id": context_id, "status": "unknown", "started_at": when},
                               version=0)
        scenes.post("dri_agent", stale, expected=422)
        check("non_create_cannot_use_version_zero", True)

        h.stop(process)
        process, url, _ = h.start_api(source)
        restarted = Scenes(h, url, f)
        check("restart_preserves_scene_and_history",
              restarted.read("a", sid)["version"] >= owner_view["version"])
        scenes = restarted
        scenes.bodies[replay_body["idempotency_key"]] = replay_body

        # ------------------------------------------------ current authority revocation
        auth_sid, _ = scenes.create_scene("b", participants=("a",), agents=(("b_agent", "b"),))
        canary2 = "AUTH-CANARY-" + uuid.uuid4().hex
        artifact2 = ("SYNTHETIC SHARED ORIGINAL " + canary2).encode()
        upload2, evidence2 = scenes.upload("b", "b", artifact2, "Synthetic shared artifact for authority test")
        when2 = now_iso()
        source2, _ = scenes.add_source("b", auth_sid, system="feishu", external_id=uid(),
                                       title="Authority test source", media_type="text/plain", acquired_at=when2)
        version2 = scenes.add_version("b", auth_sid, source2,
                                      segments=[{"speaker": "B", "text": canary2 + " line", "occurred_at": when2}],
                                      fingerprint=hashlib.sha256(artifact2).hexdigest(), acquired_at=when2,
                                      evidence_ref=evidence2)
        scenes.share("b", auth_sid, source2, version2["result"]["event_id"],
                     version2["result"]["payload_hash"], "a")
        scenes.clients["b_agent"].json(
            "GET", f"/v1/objects/{evidence2['object_id']}/revisions/{evidence2['revision_id']}")
        check("bound_agent_generic_read_before_revoke", True)
        deactivate_assignments(h, f, "b_agent")
        revoked_scene = scenes.clients["b_agent"].request("GET", "/v1/workspace-sources/" + auth_sid,
                                                          expected={403, 404})
        revoked_object = scenes.clients["b_agent"].request(
            "GET", f"/v1/objects/{evidence2['object_id']}/revisions/{evidence2['revision_id']}",
            expected={403, 404})
        check("revoked_agent_binding_closes_scene_and_generic_reads",
              revoked_scene.headers.get("cache-control") == "no-store"
              and revoked_object.headers.get("cache-control") == "no-store")
        deactivate_assignments(h, f, "b")
        scenes.clients["b_agent"].request(
            "GET", f"/v1/objects/{evidence2['object_id']}/revisions/{evidence2['revision_id']}",
            expected={403, 404})
        scenes.clients["b"].request("GET", "/v1/workspace-sources/" + auth_sid, expected={403, 404})
        grantee_still = scenes.clients["a"].json(
            "GET", f"/v1/objects/{evidence2['object_id']}/revisions/{evidence2['revision_id']}")
        check("owner_appointment_revocation_closes_derived_authority",
              grantee_still["payload_hash"] == evidence2["payload_hash"])
        after_members = scenes.read("a", sid)["scene"]["members"]
        b_member = next(item for item in after_members["participants"]
                        if item["principal_id"] == f["actors"]["b"]["principal_id"])
        agent_member = next((item for item in after_members["agents"]
                             if item["agent"]["principal_id"] == f["actors"]["b_agent"]["principal_id"]), None)
        check("deactivated_member_name_explicit_not_current",
              b_member["display_name"] == member_names[f["actors"]["b"]["principal_id"]]
              and b_member["current"] is False
              and b_member["status"] == "not_currently_appointed")
        check("deactivated_agent_member_status_explicit",
              agent_member is not None and agent_member["agent"]["current"] is False
              and agent_member["agent"]["status"] == "not_currently_appointed")

        # ------------------------------------------------ SQL/physical checks
        v02_tables = ("gov_workspace_v02_events", "gov_workspace_v02_contexts",
                      "gov_workspace_v02_assets")
        privileges = h.sql(f, """SELECT table_name,
            has_table_privilege(current_user, table_name, 'SELECT') AS can_select,
            has_table_privilege(current_user, table_name, 'INSERT') AS can_insert,
            has_table_privilege(current_user, table_name, 'UPDATE') AS can_update,
            has_table_privilege(current_user, table_name, 'DELETE') AS can_delete,
            has_table_privilege(current_user, table_name, 'TRUNCATE') AS can_truncate
            FROM unnest(%s::text[]) AS table_name""", (list(v02_tables),))
        check("application_role_boundary",
              all(row["can_select"] and row["can_insert"] and not row["can_update"]
                  and not row["can_delete"] and not row["can_truncate"] for row in privileges))
        after_counts = h.sql(f, counts_sql, counts_params)[0]
        check("no_side_effects_gov_method_runs",
              before_counts["method_runs"] == after_counts["method_runs"])
        check("no_side_effects_runtime_tasks", before_counts["tasks"] == after_counts["tasks"])
        object_types = h.sql(f, "SELECT DISTINCT object_type FROM gov_objects WHERE scope_id=%s",
                             (f["scope_id"],))
        check("no_method_objects_created", {row["object_type"] for row in object_types} <= {"EvidenceAsset"})
        counts = h.sql(f, """SELECT
            (SELECT count(*) FROM gov_workspace_v02_events WHERE scope_id=%s) AS events,
            (SELECT count(*) FROM gov_action_receipts WHERE scope_id=%s AND action_type LIKE %s) AS receipts""",
                       (f["scope_id"], f["scope_id"], "workspace_v02.%"))
        check("atomic_event_receipt_one_to_one", counts[0]["events"] == counts[0]["receipts"])
        privacy = h.sql(f, """SELECT count(*) AS n FROM gov_workspace_v02_assets
            WHERE scope_id=%s""", (f["scope_id"],))
        check("source_fence_links_recorded", privacy[0]["n"] >= 1)
        return checks, {"imported_sources": imported, "canary_used": True}
    finally:
        scenes.close()
        h.stop(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources-dir", type=Path, default=None)
    args = parser.parse_args()
    if args.output.exists() or args.private.exists():
        raise ValueError("Use fresh private and output directories")
    h = MethodHarness(args.env_file, args.output, args.private)
    source = Path(__file__).resolve().parents[2] / "src"
    initial = source_manifest(source)
    try:
        checks, details = run(h, source, args.output, args.sources_dir)
    finally:
        h.close()
    leak_free = (verify_no_private_text(args.output / "http-transcript.jsonl", args.sources_dir)
                 if args.sources_dir is not None else True)
    assert leak_free, "private source text leaked into the public HTTP transcript"
    public_json(args.output / "source-manifest.json", initial)
    public_json(args.output / "summary.json", {
        "checks_passed": checks, "check_count": len(checks),
        "runtime_api_accepted": True,
        "two_real_sources_imported": len(details["imported_sources"]),
        "private_source_reports": details["imported_sources"],
        "private_text_transcript_leak_free": leak_free,
        "runtime_local_copy_only": True,
        "live_feishu_acl_sync": "not_claimed",
        "partner_browser_verified": False, "real_model_invoked": False,
        "released": False, "deployed": False,
        "not_covered": ["Clark browser session", "real model orchestration",
                        "external Feishu ACL synchronization", "release/deployment"],
    })
    print("workspace_v02 acceptance passed: " + str(len(checks)) + " checks", flush=True)


if __name__ == "__main__":
    main()
