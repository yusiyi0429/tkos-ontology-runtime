"""Contract, access and source-fence tests for tkos.workspace/0.2."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed import workspace_v02_collaboration as collaboration
from memory_service_runtime.governed import workspace_v02_guard as guard
from memory_service_runtime.governed import workspace_v02_service as service
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.workspace_v02_models import (SourceContextCreate,
                                                                  SourceSceneCommand)

OWNER = "11111111-1111-1111-1111-111111111111"
GUEST = "22222222-2222-2222-2222-222222222222"
AGENT = "33333333-3333-3333-3333-333333333333"
CEO = "44444444-4444-4444-4444-444444444444"
SCENE = "55555555-5555-5555-5555-555555555555"
SOURCE = "66666666-6666-6666-6666-666666666666"
EVIDENCE = "77777777-7777-7777-7777-777777777777"
DERIVED = "88888888-8888-8888-8888-888888888888"
REV1 = "99999999-9999-9999-9999-999999999999"
REV2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
DREV = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def uid(seed):
    return f"{seed:08d}-0000-0000-0000-000000000000"


def make_row(kind, payload, event_id, version, principal_id=OWNER, payload_hash="a" * 64):
    return {"scope_id": "scope", "scene_id": SCENE, "event_id": event_id, "version": version,
            "principal_id": principal_id, "action_id": uid(version), "kind": kind,
            "payload": payload, "payload_hash": payload_hash,
            "recorded_at": f"2026-01-0{version}T00:00:00+00:00"}


def history(with_share=True, with_unshare=False, withdrawn=False, withdrawn_version=False, with_run=False):
    rows = [
        make_row("scene_create", {"kind": "scene_create", "scene_type": "meeting",
                                  "external_id": "meeting-1", "title": "Synthetic meeting",
                                  "owner_principal_id": OWNER,
                                  "participant_principal_ids": [GUEST],
                                  "agent_bindings": [{"agent_principal_id": AGENT,
                                                      "owner_principal_id": GUEST}]}, uid(1), 1),
        make_row("source_add", {"kind": "source_add", "system": "feishu", "external_id": "doc-1",
                                "title": "Synthetic source", "media_type": "text/plain",
                                "acquired_at": "2026-01-01T00:00:00+00:00",
                                "sensitivity": "private", "owner_principal_id": OWNER,
                                "recorded_by_principal_id": OWNER}, SOURCE, 2),
        make_row("source_version", {"kind": "source_version", "source_id": SOURCE,
                                    "fingerprint": "b" * 64, "media_type": "text/plain",
                                    "acquired_at": "2026-01-01T00:00:00+00:00", "origin_label": "export",
                                    "segments": [{"speaker": "A", "text": "canary exact source segment",
                                                  "occurred_at": "2026-01-01T00:00:00+00:00"}],
                                    "version_seq": 1, "segments_hash": "c" * 64,
                                    "evidence_ref": {"object_id": EVIDENCE, "revision_id": REV1,
                                                     "payload_hash": "d" * 64}}, REV1, 3),
    ]
    if with_share:
        rows.append(make_row("source_share", {"kind": "source_share", "source_id": SOURCE,
                                              "version_event_id": REV1, "payload_hash": "e" * 64,
                                              "share_to_principal_id": GUEST,
                                              "shared_by_principal_id": OWNER,
                                              "shared_by_human_principal_id": OWNER}, uid(4), 4,
                             payload_hash="e" * 64))
    if with_unshare:
        rows.append(make_row("source_unshare", {"kind": "source_unshare", "share_event_id": uid(4),
                                                "reason": "review finished"}, uid(5), 5))
    if withdrawn_version:
        rows.append(make_row("source_withdraw", {"kind": "source_withdraw", "source_id": SOURCE,
                                                 "version_event_id": REV1, "reason": "superseded",
                                                 "source_owner_principal_id": OWNER}, uid(6), 6))
    if withdrawn:
        rows.append(make_row("source_withdraw", {"kind": "source_withdraw", "source_id": SOURCE,
                                                 "reason": "source retired",
                                                 "source_owner_principal_id": OWNER}, uid(7), 7))
    if with_run:
        rows.append(make_row("agent_run", {"kind": "agent_run", "agent_principal_id": AGENT,
                                            "purpose": "purpose quotes canary exact source segment",
                                            "model": {"provider": "controlled-fixture",
                                                      "name": "synthetic-acceptance",
                                                      "version": "test-version-1",
                                                      "parameters": {"prompt": "canary exact source segment"}},
                                            "input_refs": [{"source_id": SOURCE, "version_event_id": REV1,
                                                            "payload_hash": "a" * 64}],
                                            "status": "failed",
                                            "started_at": "2026-01-01T00:00:00+00:00",
                                            "finished_at": "2026-01-01T00:01:00+00:00",
                                            "error": "error canary exact source segment",
                                            "output_refs": []}, uid(8), 8,
                             principal_id=AGENT))
    return rows


def ctx(principal_id=OWNER, principal_type="human"):
    return SimpleNamespace(scope_id="scope", principal_id=principal_id, principal_type=principal_type)


class Rows:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)

    def fetchall(self):
        return self.values

    def fetchone(self):
        return self.values[0] if self.values else None


class FakeConn:
    """Minimal SQL dispatcher for guard/collaboration tests; no implementation import."""

    def __init__(self, links=(), rows=None, revisions=None, evidence_creator=None,
                 heads=None, revision_rows=None, principals=None):
        self.links = list(links)
        self.rows = rows if rows is not None else []
        self.revisions = revisions or {}
        self.evidence_creator = evidence_creator
        self.heads = heads or {}
        self.revision_rows = revision_rows or {}
        self.principals = principals or {}
        self.principal_query = None

    def execute(self, statement, params=()):
        if "FROM gov_workspace_v02_assets" in statement:
            scope, object_id = params
            return Rows([link for link in self.links if link["object_id"] == str(object_id)])
        if "FROM gov_principals" in statement:
            scope, ids = params
            self.principal_query = list(ids)
            return Rows([self.principals[pid] for pid in ids if pid in self.principals])
        if "SELECT revision_id FROM gov_object_revisions" in statement:
            scope, object_id = params
            return Rows([{"revision_id": rid} for rid in self.revisions.get(str(object_id), [])])
        if "SELECT * FROM gov_objects" in statement:
            scope, object_id = params
            head = self.heads.get(str(object_id))
            return Rows([head] if head is not None else [])
        if "SELECT * FROM gov_object_revisions" in statement:
            scope, object_id, revision_id = params
            row = self.revision_rows.get(str(object_id), {}).get(str(revision_id))
            return Rows([row] if row is not None else [])
        if "FROM gov_workspace_v02_events" in statement:
            scope, scene_id = params
            return Rows([row for row in self.rows if str(row["scene_id"]) == str(scene_id)])
        if "FROM gov_object_revisions r" in statement and "JOIN gov_principals p" in statement:
            return Rows(list(self.evidence_creator or []))
        raise AssertionError("unexpected SQL in unit fake: " + statement[:80])


def links_for(scene_rows):
    version = next(row for row in scene_rows if row["kind"] == "source_version")
    return [{"object_id": EVIDENCE, "revision_id": REV1, "scene_id": SCENE,
             "source_id": SOURCE, "version_event_id": version["event_id"]}]


# ---------------------------------------------------------------- models

def command(event, version=1):
    return {"contract_version": "tkos.workspace/0.2", "scene_id": SCENE,
            "expected_version": version, "idempotency_key": "acceptance-key-0001",
            "event": event}


def create_event():
    return {"kind": "scene_create", "scene_type": "meeting", "external_id": "meeting-1",
            "title": "Synthetic meeting", "owner_principal_id": OWNER,
            "participant_principal_ids": [GUEST],
            "agent_bindings": [{"agent_principal_id": AGENT, "owner_principal_id": GUEST}]}


def test_scene_create_requires_zero_version_and_accepts_standalone_scene():
    body = command(create_event())
    with pytest.raises(ValidationError):
        SourceSceneCommand.model_validate(body)
    body["expected_version"] = 0
    assert SourceSceneCommand.model_validate(body).event.scene_type == "meeting"
    body["event"] = {**create_event(), "anchor_ref": {"object_id": SOURCE}}
    with pytest.raises(ValidationError):
        SourceSceneCommand.model_validate(body)


@pytest.mark.parametrize("change", [
    {"contract_version": "tkos.workspace/0.1"},
    {"idempotency_key": "short"},
    {"expected_version": True},
    {"event": {"kind": "source_version", "source_id": SOURCE, "fingerprint": "x",
               "media_type": "text/plain", "acquired_at": "2026-01-01T00:00:00+00:00",
               "segments": []}},
    {"event": {"kind": "agent_run", "agent_principal_id": AGENT, "purpose": "p",
               "model": {"provider": "x", "name": "y", "version": "1"},
               "input_refs": [], "status": "succeeded",
               "started_at": "2026-01-01T00:00:00+00:00"}},
    {"event": {"kind": "draft_decision", "draft_event_id": DREV, "item_index": 0,
               "decision": "approved"}},
])
def test_rejects_unversioned_or_dishonest_events(change):
    body = command(create_event()) | change
    with pytest.raises(ValidationError):
        SourceSceneCommand.model_validate(body)


def test_failed_run_cannot_claim_output_and_succeeded_run_requires_it():
    base = {"kind": "agent_run", "agent_principal_id": AGENT, "purpose": "summarize",
            "model": {"provider": "controlled-fixture", "name": "synthetic-acceptance",
                      "version": "test-version-1"},
            "input_refs": [{"source_id": SOURCE, "version_event_id": REV1, "payload_hash": "a" * 64}],
            "context_id": DREV,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:01:00+00:00"}
    body = command({**base, "status": "failed", "error": "provider timeout"})
    assert SourceSceneCommand.model_validate(body).event.status == "failed"
    for change in [{"output_refs": [{"draft": "x"}]}, {"error": None}]:
        with pytest.raises(ValidationError):
            SourceSceneCommand.model_validate(command({**base, "status": "failed", "error": "timeout"} | change))


def test_agent_run_requires_an_exact_input_snapshot():
    base = {"kind": "agent_run", "agent_principal_id": AGENT, "purpose": "summarize",
            "model": {"provider": "controlled-fixture", "name": "synthetic-acceptance",
                      "version": "test-version-1"},
            "input_refs": [{"source_id": SOURCE, "version_event_id": REV1, "payload_hash": "a" * 64}],
            "context_id": DREV, "status": "unknown",
            "started_at": "2026-01-01T00:00:00+00:00"}
    assert SourceSceneCommand.model_validate(command(base)).event.context_id == DREV
    for change in [{"context_id": None}, {"input_refs": []}, {"input_refs": ["not-a-ref"]}]:
        with pytest.raises(ValidationError):
            SourceSceneCommand.model_validate(command(base | change))


def test_citation_requires_exact_source_segment_shape():
    draft = {"kind": "followup_draft", "title": "Follow-up",
             "items": [{"item_kind": "suggestion", "text": "Ask for owners.",
                        "citations": [{"source_id": SOURCE, "version_event_id": REV1,
                                       "payload_hash": "a" * 64, "segment_index": 0,
                                       "quote": "canary exact source segment"}]}]}
    assert SourceSceneCommand.model_validate(command(draft)).event.items[0].item_kind == "suggestion"
    broken = deepcopy(draft)
    broken["items"][0]["citations"][0]["segment_index"] = -1
    with pytest.raises(ValidationError):
        SourceSceneCommand.model_validate(command(broken))


def test_context_items_must_be_distinct_exact_versions():
    item = {"source_id": SOURCE, "version_event_id": REV1, "payload_hash": "a" * 64}
    assert SourceContextCreate.model_validate({"contract_version": "tkos.workspace/0.2",
                                               "scene_id": SCENE, "idempotency_key": "acceptance-key-0001",
                                               "purpose": "prepare a bounded context",
                                               "items": [item]}).items[0].source_id == SOURCE
    with pytest.raises(ValidationError):
        SourceContextCreate.model_validate({"contract_version": "tkos.workspace/0.2",
                                            "scene_id": SCENE, "idempotency_key": "acceptance-key-0001",
                                            "purpose": "prepare a bounded context",
                                            "items": [item, item]})


# --------------------------------------------------- pure access semantics

def test_exact_share_never_traverses_to_other_versions_or_withdrawn_content():
    rows = history()
    states = collaboration.source_states(rows)
    state = states[SOURCE]
    assert collaboration.version_readable(state, REV1, OWNER)
    assert collaboration.version_readable(state, REV1, GUEST)
    assert not collaboration.version_readable(state, REV2, GUEST)
    assert collaboration.member_owner(collaboration.scene_seed(rows), AGENT, "agent") == GUEST
    assert collaboration.member_owner(collaboration.scene_seed(rows), CEO, "human") is None
    unshared = collaboration.source_states(history(with_unshare=True))[SOURCE]
    assert not collaboration.version_readable(unshared, REV1, GUEST)
    withdrawn = collaboration.source_states(history(withdrawn_version=True))[SOURCE]
    assert not collaboration.version_readable(withdrawn, REV1, OWNER)
    retired = collaboration.source_states(history(withdrawn=True))[SOURCE]
    assert not collaboration.version_readable(retired, REV1, OWNER)


def test_draft_requires_every_citation_to_be_currently_readable():
    rows = history()
    states = collaboration.source_states(rows)
    draft = {"items": [{"citations": [{"source_id": SOURCE, "version_event_id": REV1,
                                       "payload_hash": "a" * 64}]}]}
    assert collaboration.draft_readable(states, draft, GUEST)
    assert collaboration.draft_readable(states, draft, OWNER)
    assert not collaboration.draft_readable(states, draft, CEO)
    wrong = {"items": [{"citations": [{"source_id": SOURCE, "version_event_id": REV1,
                                        "payload_hash": "f" * 64}]}]}
    assert not collaboration.draft_readable(states, wrong, OWNER)


# --------------------------------------------------------- source fence

def _passthrough_current_member(monkeypatch):
    """Guard unit tests isolate fence logic; current_member has its own test."""
    monkeypatch.setattr(service, "current_member",
                        lambda conn, c, seed: collaboration.member_owner(seed, c.principal_id,
                                                                        c.principal_type))


def test_object_fence_allows_owner_and_exact_grantee_only(monkeypatch):
    _passthrough_current_member(monkeypatch)
    rows = history()
    conn = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1]})
    assert guard.object_allowed(conn, ctx(OWNER), EVIDENCE, REV1)
    assert guard.object_allowed(conn, ctx(GUEST), EVIDENCE, REV1)
    assert not guard.object_allowed(conn, ctx(CEO), EVIDENCE, REV1)
    assert guard.object_allowed(FakeConn(), ctx(OWNER), EVIDENCE, REV1)  # unlinked object untouched


def test_object_head_requires_every_revision_to_stay_readable(monkeypatch):
    _passthrough_current_member(monkeypatch)
    rows = history()
    conn = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1]})
    assert guard.object_allowed(conn, ctx(OWNER), EVIDENCE)
    extended = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1, REV2]})
    assert not guard.object_allowed(extended, ctx(OWNER), EVIDENCE)


def test_unshare_and_withdraw_revoke_the_object_fence(monkeypatch):
    _passthrough_current_member(monkeypatch)
    unshared = history(with_unshare=True)
    conn = FakeConn(links=links_for(unshared), rows=unshared, revisions={EVIDENCE: [REV1]})
    assert not guard.object_allowed(conn, ctx(GUEST), EVIDENCE, REV1)
    assert guard.object_allowed(conn, ctx(OWNER), EVIDENCE, REV1)
    retired = history(withdrawn=True)
    conn = FakeConn(links=links_for(retired), rows=retired, revisions={EVIDENCE: [REV1]})
    assert not guard.object_allowed(conn, ctx(OWNER), EVIDENCE, REV1)


def test_filter_withholds_whole_derived_item_and_never_leaks_count_in_snapshots(monkeypatch):
    _passthrough_current_member(monkeypatch)
    rows = history()
    conn = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1], DERIVED: [DREV]})
    selected = [{"object_id": DERIVED, "revision_id": DREV,
                 "payload": {"body": "derived canary text"},
                 "source_refs": [{"object_id": EVIDENCE, "revision_id": REV1}]}]
    excluded = [{"object_id": EVIDENCE, "revision_id": REV1, "reason": "original_reason"}]
    kept, moved = guard.filter_context_items(conn, ctx(CEO), selected, excluded, explicit=False)
    kept, moved = list(kept), list(moved)
    assert kept == []
    assert all("canary" not in str(item) for item in kept + moved)
    assert all(item.get("object_id") != EVIDENCE for item in moved)
    # Live requests may name what the caller asked for, but still never the body.
    kept, moved = guard.filter_context_items(conn, ctx(CEO), selected, excluded, explicit=True)
    assert kept == []
    assert {"reason": "source_not_authorized", "object_id": DERIVED} in moved
    assert all("canary" not in str(item) for item in kept + moved)
    assert all("payload" not in item for item in kept + moved)


def test_source_view_hides_never_shared_correction_from_exact_grantee():
    from memory_service_runtime.governed import workspace_v02_readers as readers
    rows = history()
    correction = make_row("source_correct", {"kind": "source_correct", "source_id": SOURCE,
                                              "corrects_event_id": REV1, "reason": "fix transcription",
                                              "fingerprint": "f" * 64, "media_type": "text/plain",
                                              "acquired_at": "2026-01-02T00:00:00+00:00",
                                              "segments": [{"speaker": "A",
                                                            "text": "correction canary text"}],
                                              "version_seq": 2, "segments_hash": "9" * 64}, uid(9), 9,
                          payload_hash="f" * 64)
    rows.append(correction)
    state = collaboration.source_states(rows)[SOURCE]
    guest = readers._source_entry(SOURCE, state, GUEST)
    assert [version["event_id"] for version in guest["versions"]] == [REV1]
    assert "correction canary" not in str(guest)
    assert uid(9) not in str(guest)
    assert "f" * 64 not in str(guest)
    owner = readers._source_entry(SOURCE, state, OWNER)
    assert [version["event_id"] for version in owner["versions"]] == [REV1, uid(9)]


def test_run_free_text_is_withheld_once_any_input_source_is_revoked():
    from memory_service_runtime.governed import workspace_v02_readers as readers
    active = history(with_run=True)
    entry = readers._runs(active, collaboration.source_states(active), GUEST)[0]
    assert entry["purpose"] == "purpose quotes canary exact source segment"
    revoked = history(with_run=True, with_unshare=True)
    entry = readers._runs(revoked, collaboration.source_states(revoked), GUEST)[0]
    assert entry["input_withheld"] is True
    assert "canary" not in str(entry)
    assert "purpose" not in entry and "error" not in entry and "model" not in entry


def test_filter_snapshot_passes_independent_items_without_linked_inputs(monkeypatch):
    _passthrough_current_member(monkeypatch)
    rows = history()
    conn = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1], DERIVED: [DREV]})
    private = [{"object_id": DERIVED, "revision_id": DREV, "payload": {"body": "canary"},
                "source_refs": [{"object_id": EVIDENCE, "revision_id": REV1}]}]
    public = [{"object_id": DERIVED, "revision_id": DREV, "payload": {"body": "independent"}}]
    row = guard.filter_snapshot(conn, ctx(CEO), {"selected": public, "excluded": []})
    assert row["selected"][0]["payload"]["body"] == "independent"
    row = guard.filter_snapshot(conn, ctx(CEO), {"selected": private, "excluded": []})
    assert row["selected"] == []


def test_context_view_withholds_purpose_and_hidden_counts_from_unauthorized_readers(monkeypatch):
    from memory_service_runtime.governed import workspace_v02_readers as readers
    rows = history()
    monkeypatch.setattr(readers.service, "rows", lambda conn, ctx, sid: rows)
    monkeypatch.setattr(readers.service, "scene_of",
                        lambda conn, ctx, sid, hist: (collaboration.scene_seed(hist), ctx.principal_id))
    readable = {"source_id": SOURCE, "version_event_id": REV1, "payload_hash": "a" * 64,
                "segments": [{"speaker": "A", "text": "available canary"}]}
    hidden = {"source_id": SOURCE, "version_event_id": REV2, "payload_hash": "z" * 64,
              "segments": [{"speaker": "A", "text": "hidden canary"}]}
    row = {"context_id": DREV, "scene_id": SCENE, "principal_id": OWNER,
           "purpose": "purpose quotes canary", "recorded_at": "2026-01-01T00:00:00+00:00",
           "selected": [readable, hidden]}
    partial = readers.context_view(None, ctx(GUEST), row)
    assert partial["purpose"] is None and partial["withheld"] is True
    assert [item["status"] for item in partial["items"]] == ["available"]
    assert "purpose quotes canary" not in json.dumps(partial)
    never = readers.context_view(None, ctx(CEO), row)
    assert never["purpose"] is None and never["items"] == [] and never["withheld"] is True
    assert "canary" not in json.dumps(never)
    creator = readers.context_view(None, ctx(OWNER), row)
    assert creator["purpose"] is None and creator["withheld"] is True
    denied = next(item for item in creator["items"] if item["status"] == "withheld")
    assert denied["version_event_id"] == REV2 and denied["segments"] is None
    complete_row = {**row, "selected": [readable]}
    for reader in (OWNER, GUEST):
        complete_view = readers.context_view(None, ctx(reader), complete_row)
        assert complete_view["complete"] is True and complete_view["withheld"] is False
        assert complete_view["purpose"] == "purpose quotes canary"


def test_current_member_requires_live_agent_binding_and_human_assignment(monkeypatch):
    seed = collaboration.scene_seed(history())
    monkeypatch.setattr(service.db, "_assignments", lambda *args: [])
    assert service.current_member(None, ctx(OWNER), seed) is None
    monkeypatch.setattr(service, "_current_agent",
                        lambda *args: (_ for _ in ()).throw(GovernedError("FORBIDDEN")))
    assert service.current_member(None, ctx(AGENT, "agent"), seed) is None
    monkeypatch.setattr(service, "_current_agent", lambda *args: None)
    assert service.current_member(None, ctx(AGENT, "agent"), seed) == GUEST
    assert service.current_member(None, ctx(CEO), seed) is None


def test_evidence_link_is_limited_to_the_asset_creator(monkeypatch):
    conn = FakeConn(evidence_creator=[{"recorded_by": OWNER, "principal_type": "human"}])
    assert service.evidence_owner_allowed(conn, ctx(OWNER), EVIDENCE, OWNER)
    assert not service.evidence_owner_allowed(conn, ctx(CEO), EVIDENCE, CEO)
    assert not service.evidence_owner_allowed(conn, ctx(OWNER), EVIDENCE, GUEST)
    agent_conn = FakeConn(evidence_creator=[{"recorded_by": AGENT, "principal_type": "agent"}])
    monkeypatch.setattr(service.access, "personal_agent", lambda *args: {"assignment_id": uid(9)})
    assert service.evidence_owner_allowed(agent_conn, ctx(GUEST, "agent"), EVIDENCE, GUEST)
    monkeypatch.setattr(service.access, "personal_agent",
                        lambda *args: (_ for _ in ()).throw(GovernedError("FORBIDDEN")))
    assert not service.evidence_owner_allowed(agent_conn, ctx(CEO), EVIDENCE, CEO)


def test_shared_revision_serves_exact_grant_without_domain_or_method(monkeypatch):
    _passthrough_current_member(monkeypatch)
    rows = history()
    conn = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1]},
                    heads={EVIDENCE: {"scope_id": "scope", "object_id": EVIDENCE,
                                      "domain_id": "domain-a", "object_type": "EvidenceAsset"}},
                    revision_rows={EVIDENCE: {REV1: {"scope_id": "scope", "object_id": EVIDENCE,
                                                     "revision_id": REV1, "payload_hash": "a" * 64}}})
    head, revision = guard.shared_revision(conn, ctx(GUEST), EVIDENCE, REV1)
    assert head["object_id"] == EVIDENCE and revision["revision_id"] == REV1
    assert guard.shared_revision(conn, ctx(CEO), EVIDENCE, REV1) is None
    assert guard.shared_revision(FakeConn(), ctx(GUEST), EVIDENCE, REV1) is None


def test_guard_receipts_hide_linked_assets_from_unrelated_readers(monkeypatch):
    _passthrough_current_member(monkeypatch)
    rows = history()
    conn = FakeConn(links=links_for(rows), rows=rows, revisions={EVIDENCE: [REV1]})
    receipt = {"object_versions": [{"object_id": EVIDENCE, "revision_id": REV1}],
               "target_object_id": None, "result": {}}
    guard.enforce_receipt(conn, ctx(OWNER), receipt)
    with pytest.raises(GovernedError) as exc:
        guard.enforce_receipt(conn, ctx(CEO), receipt)
    assert exc.value.code == "NOT_FOUND"


def test_member_directory_is_bounded_explicit_and_display_only(monkeypatch):
    from memory_service_runtime.governed import workspace_v02_readers as readers
    from memory_service_runtime.governed.db import AuthContext
    seed = collaboration.scene_seed(history())
    missing = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    seed = {**seed, "participant_principal_ids": [*seed["participant_principal_ids"], missing]}
    conn = FakeConn(principals={
        OWNER: {"principal_id": OWNER, "display_name": "Owner Name", "active": True},
        GUEST: {"principal_id": GUEST, "display_name": "Guest Name", "active": False},
        AGENT: {"principal_id": AGENT, "display_name": "Agent Name", "active": True},
    })
    monkeypatch.setattr(readers.db, "_assignments", lambda conn, c: [{"assignment_id": "x"}])
    monkeypatch.setattr(readers.access, "personal_agent", lambda *args: {"assignment_id": "y"})
    owner_ctx = AuthContext(scope_id="scope", tenant_id="t", company_id="c",
                            principal_id=OWNER, principal_type="human", auth_epoch=1, assignments=[])
    directory = readers._member_directory(conn, owner_ctx, seed)
    assert directory["authority"] == "display_only_not_authorization"
    assert directory["owner"] == {"principal_id": OWNER, "principal_type": "human",
                                  "display_name": "Owner Name", "current": True, "status": "current"}
    guest = directory["participants"][0]
    assert guest["display_name"] == "Guest Name" and guest["current"] is False
    assert guest["status"] == "inactive"
    absent = directory["participants"][1]
    assert absent == {"principal_id": missing, "principal_type": "human",
                      "display_name": None, "current": False, "status": "missing"}
    agent = directory["agents"][0]["agent"]
    assert agent["display_name"] == "Agent Name" and agent["current"] is True
    assert set(conn.principal_query) == {OWNER, GUEST, AGENT, missing}


def test_operations_never_infer_another_owners_private_grant_or_source():
    from memory_service_runtime.governed import workspace_v02_readers as readers
    states = collaboration.source_states(history())
    guest_ops = {item["kind"]: item for item in readers._operations(None, ctx(GUEST), GUEST, states)}
    assert guest_ops["source_unshare"]["allowed"] is False
    assert guest_ops["source_unshare"]["reason"] == "no_owned_source"
    assert guest_ops["source_version"]["allowed"] is False
    outsider_ops = {item["kind"]: item for item in readers._operations(None, ctx(CEO), CEO, states)}
    assert outsider_ops["source_unshare"]["reason"] == "no_owned_source"
    assert outsider_ops["followup_draft"]["reason"] == "no_readable_source"
    owner_ops = {item["kind"]: item for item in readers._operations(None, ctx(OWNER), OWNER, states)}
    assert owner_ops["source_unshare"]["allowed"] is True
    assert owner_ops["followup_draft"]["allowed"] is True


def test_operations_are_conservative_for_withdrawn_owned_sources():
    from memory_service_runtime.governed import workspace_v02_readers as readers
    retired = collaboration.source_states(history(withdrawn=True))
    ops = {item["kind"]: item for item in readers._operations(None, ctx(OWNER), OWNER, retired)}
    assert ops["source_version"]["reason"] == "owned_source_withdrawn"
    assert ops["source_correct"]["reason"] == "owned_source_withdrawn"
    assert ops["source_withdraw"]["allowed"] is False
    assert ops["followup_draft"]["allowed"] is False
    version_withdrawn = collaboration.source_states(history(withdrawn_version=True))
    ops = {item["kind"]: item for item in readers._operations(None, ctx(OWNER), OWNER, version_withdrawn)}
    # The source itself is still open, so a new version or whole-source
    # withdrawal is possible, but correcting/sharing the withdrawn version is not.
    assert ops["source_version"]["allowed"] is True
    assert ops["source_correct"]["reason"] == "no_active_owned_version"
    assert ops["source_share"]["reason"] == "no_active_owned_version"
    assert ops["source_withdraw"]["allowed"] is True


def test_receipt_authorization_handles_source_add_and_unshare_kinds(monkeypatch):
    rows = history(with_unshare=True)
    seed = collaboration.scene_seed(rows)
    monkeypatch.setattr(service, "rows", lambda conn, ctx, sid: rows)
    monkeypatch.setattr(service, "scene_of",
                        lambda conn, ctx, sid, hist: (seed, ctx.principal_id))
    add_event = next(row for row in rows if row["kind"] == "source_add")
    unshare_event = next(row for row in rows if row["kind"] == "source_unshare")
    add_receipt = {"result": {"scene_id": SCENE, "event_id": add_event["event_id"]},
                   "principal_id": OWNER}
    unshare_receipt = {"result": {"scene_id": SCENE, "event_id": unshare_event["event_id"]},
                       "principal_id": OWNER}
    assert service.authorize_receipt(None, ctx(OWNER), add_receipt) is None
    assert service.authorize_receipt(None, ctx(OWNER), unshare_receipt) is None
    assert service.authorize_receipt(None, ctx(GUEST), unshare_receipt) is None
    with pytest.raises(GovernedError):
        service.authorize_receipt(None, ctx(GUEST), add_receipt)


def test_method_access_revision_preserves_exact_source_grant(monkeypatch):
    from memory_service_runtime.governed import method_access
    row = {"revision_id": REV1, "object_id": EVIDENCE}
    monkeypatch.setattr(guard, "shared_revision",
                        lambda conn, ctx, oid, rid: ({"object_id": EVIDENCE}, row))
    monkeypatch.setattr(method_access.protocol, "require_read_support",
                        lambda *args, **kwargs: {"protocol_id": "tkos.method"})
    seen = {}

    def fake_raw(conn, c, oid, rid):
        seen["raw"] = (oid, rid)
        return row

    monkeypatch.setattr(method_access, "raw_revision", fake_raw)
    assert method_access.revision(None, ctx(GUEST), EVIDENCE, REV1) is row
    assert seen["raw"] == (EVIDENCE, REV1)
    # Without an exact grant the normal guarded object-level path is used.
    monkeypatch.setattr(guard, "shared_revision", lambda *args: None)
    monkeypatch.setattr(method_access, "head_access",
                        lambda *args: (_ for _ in ()).throw(GovernedError("NOT_FOUND")))
    with pytest.raises(GovernedError):
        method_access.revision(None, ctx(GUEST), EVIDENCE, REV1)


def test_openapi_dispatches_both_workspace_contracts_without_weakening_0_1():
    from memory_service_app.main import app
    schema = app.openapi()
    body = schema["paths"]["/v1/workspace-scenes/events"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["discriminator"]["propertyName"] == "contract_version"
    assert set(body["discriminator"]["mapping"]) == {"tkos.workspace/0.1", "tkos.workspace/0.2"}
    assert "/v1/workspace-sources" in schema["paths"]
    assert "/v1/workspace-sources/contexts" in schema["paths"]
    assert "/v1/workspace-sources/contexts/{context_id}" in schema["paths"]
    old = schema["components"]["schemas"]["WorkspaceCommand"]["properties"]["event"]
    assert old["discriminator"]["propertyName"] == "kind"
    assert "meeting_publish" in old["discriminator"]["mapping"]
