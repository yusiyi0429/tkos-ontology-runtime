"""Narrative protocol, identity fences and model failure boundaries."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import httpx
import pytest

from memory_service_app import narrative as api
from memory_service_runtime.governed import db
from memory_service_runtime.governed.errors import GovernedError
from tests import legacy_protocol_fixture

# Frozen Contract-A main-contract SHA256 (literal pin, not imported from the
# implementation under test).
CONTRACT_A_MAIN_CONTRACT_SHA256 = (
    "fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4")


DOMAIN = str(uuid.uuid4())
GENERATION = str(uuid.uuid4())
IDENTITY = db.AuthContext("scope", "tenant", "org", "principal", "human", 1,
    [{"domain_id": DOMAIN, "role": "CEO"}])
FACT = {"object_id": str(uuid.uuid4()), "revision_id": str(uuid.uuid4()),
        "object_type": "CompanyOutcome", "payload_hash": "a" * 64,
        "outcome_achievement": "not_achieved", "source_refs": []}


@pytest.fixture
def boundary(monkeypatch):
    for key in ("TKOS_NARRATIVE_ENABLED", "TKOS_NARRATIVE_DOMAIN_ID", "TKOS_NARRATIVE_LEGACY_ENABLED",
                "TKOS_NARRATIVE_COMPRESSION", "TKOS_NARRATIVE_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TKOS_NARRATIVE_ENABLED", "1")
    monkeypatch.setenv("MEMORY_TENANT", "tenant")
    monkeypatch.setenv("MEMORY_ORG", "org")
    calls = []
    state = {"ctx": IDENTITY, "facts": {"schema_version": "governed-facts.v1", "selected": [FACT], "excluded": [], "truncated": False}, "calls": calls}
    class Conn:
        def execute(self, sql, args=()):
            calls.append(sql)
            compact = " ".join(sql.split())
            if compact.startswith("SELECT generation_id"):
                return SimpleNamespace(fetchone=lambda: {"generation_id": GENERATION})
            # A1: the second transaction re-checks protocol read support for
            # every fact/source; only the fixture FACT object is registered
            # (frozen legacy rows), unknown objects stay unregistered.
            mode = state.get("protocol_mode", "legacy")
            if mode == "contract_a":
                protocol_rows = _as_contract_a(compact, args)
            else:
                protocol_rows = legacy_protocol_fixture.answer_query(
                    compact, args, IDENTITY.scope_id, {FACT["object_id"]})
                if mode == "read_revoked" and protocol_rows is not None:
                    protocol_rows = [
                        {**row, "content": {**row["content"], "can_read": False}}
                        if "registry_seq" in row else row
                        for row in protocol_rows]
            if protocol_rows is not None:
                return SimpleNamespace(
                    fetchone=lambda: protocol_rows[0] if protocol_rows else None,
                    fetchall=lambda: protocol_rows)
            raise AssertionError(f"unexpected SQL: {compact}")
    @contextmanager
    def transaction(token):
        assert token == "synthetic-token"
        yield Conn(), state["ctx"]
    monkeypatch.setattr(api.db, "transaction", transaction)
    monkeypatch.setattr(api.db, "authorize_domain", lambda conn, ctx, domain, action="read": calls.append((domain, action)))
    monkeypatch.setattr(api.db, "revision_row", lambda *args: calls.append(("revision", args[2], args[3])))
    monkeypatch.setattr(api.narrative_facts, "collect_facts", lambda *args, **kwargs: state["facts"])
    monkeypatch.setattr(api.narrative_facts, "render_facts", lambda facts: "交付通过不等于 Outcome 达成；Outcome=not_achieved；MF=investigating。")
    return state


def build(**kwargs):
    return api.build_narrative(api.NarrativeRequest(query="交付与目标情况", **kwargs), "synthetic-token")


def _as_contract_a(sql, args):
    """A legitimate control-plane rebind of the fixture FACT object to readable
    Contract-A metadata between the two transactions (B09): frozen A rows for
    the binding, the installed ProfileCore and the seq-1 A registry entry."""
    if legacy_protocol_fixture.BINDING_TABLE in sql:
        if str(args[1]) != FACT["object_id"]:
            return []
        return [{"scope_id": IDENTITY.scope_id, "object_id": FACT["object_id"],
                 "binding_id": "00000000-0000-0000-0000-0000000000b2", "binding_version": 2,
                 "protocol_id": "tkos.contract-a", "contract_version": "tkos.contract-a/0.1",
                 "profile_id": "urn:tkos:profile:a-test", "profile_revision": "0.1.0",
                 "profile_canonical_hash": "b" * 64, "record_origin": "synthetic",
                 "run_id": None, "registered_by": "unit-fixture", "receipt_id": None,
                 "detail": {}, "recorded_at": "2026-09-10T00:00:00+00:00"}]
    if legacy_protocol_fixture.PROFILE_TABLE in sql:
        if args[1] == "urn:tkos:profile:a-test" and args[2] == "0.1.0":
            return [{"scope_id": IDENTITY.scope_id, "profile_id": "urn:tkos:profile:a-test",
                     "revision": "0.1.0", "schema_version": "tkos.profile-core/0.1",
                     "canonical_hash": "b" * 64, "action_contract_ref": None,
                     "record_origin": "synthetic", "experimental": True,
                     "content": {"action_contract_ref": {
                         "contract_id": "tkos.contract-a", "revision": "0.1",
                         "content_sha256": CONTRACT_A_MAIN_CONTRACT_SHA256}},
                     "installed_by": "unit-fixture", "install_reason": "b09 test",
                     "recorded_at": "2026-09-10T00:00:00+00:00"}]
        return []
    if legacy_protocol_fixture.REGISTRY_TABLE in sql:
        if args[1] == "tkos.contract-a":
            return [{"scope_id": IDENTITY.scope_id, "protocol_id": "tkos.contract-a",
                     "contract_version": "tkos.contract-a/0.1", "registry_seq": 1,
                     "content": {"can_read": True, "can_create": False, "can_write": False,
                                 "evidence_upload": True, "actions": [], "object_types": [],
                                 "readonly_compat": ["tkos.contract-a/0.1"], "notes": "test"},
                     "recorded_by": "unit-fixture", "recorded_at": "2026-09-10T00:00:00+00:00"}]
        return []
    return None


def test_default_returns_clark_shape_without_persisting(boundary):
    result = build(include_raw=True)
    assert result["narrative"] == result["narrative_raw"]
    assert result["model"] == "deterministic-v1"
    assert result["tenant"] == "tenant" and result["org"] == "org"
    assert result["governed_facts"]["selected"][0]["outcome_achievement"] == "not_achieved"
    assert result["provenance"]["legacy"] is None
    assert result["provenance"]["narrative_sha256"] == api.digest(result["narrative"])
    assert "narrative_raw" not in build()
    assert ("revision", FACT["object_id"], FACT["revision_id"]) in boundary["calls"]


def test_contract_a_rebinding_between_transactions_is_source_changed(boundary):
    # B09: a legitimate control-plane rebind to readable Contract-A metadata
    # after assembly must not let legacy-interpreted facts ship.
    boundary["protocol_mode"] = "contract_a"
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "NARRATIVE_SOURCE_CHANGED"
    assert error.value.status == 409


def test_revoked_read_support_between_transactions_is_rejected(boundary):
    boundary["protocol_mode"] = "read_revoked"
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "PROTOCOL_NOT_SUPPORTED"
    assert error.value.status == 409


@pytest.mark.parametrize("kwargs", [{"tenant": "other"}, {"org": "other"}])
def test_scope_cannot_be_selected_by_request(boundary, kwargs):
    with pytest.raises(GovernedError) as error:
        build(**kwargs)
    assert error.value.code == "FORBIDDEN"


def test_multiple_domains_require_explicit_selection(boundary):
    boundary["ctx"] = replace(IDENTITY, assignments=[*IDENTITY.assignments, {"domain_id": str(uuid.uuid4())}])
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "INVALID_REQUEST"
    assert build(domain_id=DOMAIN)["provenance"]["domain_id"] == DOMAIN


def test_configured_domain_cannot_be_overridden(boundary, monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_DOMAIN_ID", DOMAIN)
    with pytest.raises(GovernedError) as error:
        build(domain_id=str(uuid.uuid4()))
    assert error.value.code == "FORBIDDEN"


def test_future_and_malformed_times_are_rejected(boundary):
    with pytest.raises(GovernedError) as error:
        build(known_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    assert error.value.code == "INVALID_REQUEST"
    for value in (1, "2026-01-01T00:00:00"):
        with pytest.raises(ValueError):
            api.NarrativeRequest(query="query", known_at=value)


def test_no_effective_facts_is_not_a_successful_empty_context(boundary):
    boundary["facts"]["selected"] = []
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "NARRATIVE_EMPTY"


def test_revoked_authority_during_assembly_is_rejected(boundary, monkeypatch):
    def render(_facts):
        boundary["ctx"] = replace(IDENTITY, auth_epoch=2)
        return "result must not reach revoked caller"
    monkeypatch.setattr(api.narrative_facts, "render_facts", render)
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "NARRATIVE_AUTH_CHANGED"


def test_legacy_requires_separate_permission_and_matching_process_scope(boundary, monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_LEGACY_ENABLED", "1")
    monkeypatch.setenv("MEMORY_TENANT", "other")
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "FORBIDDEN"
    assert (DOMAIN, "read_legacy_context") in boundary["calls"]


def test_legacy_cannot_claim_historical_snapshot(boundary, monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_LEGACY_ENABLED", "1")
    with pytest.raises(GovernedError) as error:
        build(known_at="2026-01-01T00:00:00Z")
    assert error.value.code == "INVALID_REQUEST"


def test_empty_required_legacy_pack_cannot_be_hidden_by_governed_facts(boundary, monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_LEGACY_ENABLED", "1")
    monkeypatch.setattr(api, "_legacy_pack", lambda *args: SimpleNamespace(main_paths=[], lateral_nodes=[]))
    with pytest.raises(GovernedError) as error:
        build()
    assert error.value.code == "NARRATIVE_EMPTY"


def test_compression_never_rewrites_governance(boundary, monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_LEGACY_ENABLED", "1")
    monkeypatch.setenv("TKOS_NARRATIVE_COMPRESSION", "chat")
    pack = SimpleNamespace(main_paths=[1], lateral_nodes=[], generation=SimpleNamespace(generation_id=GENERATION))
    monkeypatch.setattr(api, "_legacy_pack", lambda *args: pack)
    monkeypatch.setattr(api, "_legacy_provenance", lambda pack: {"generation_id": GENERATION})
    monkeypatch.setattr(api.context_pack, "render_narrative", lambda pack: "愿景原文。历史背景原文。")
    monkeypatch.setattr(api.context_pack, "root_statement", lambda pack: "愿景原文。")
    monkeypatch.setattr(api.context_pack, "_narrative_without_root", lambda pack: "历史背景原文。")
    class Compressor:
        model = "controlled-test-model"
        def __init__(self, timeout):
            pass
        def chat(self, messages, **kwargs):
            text = str(messages)
            assert "not_achieved" not in text
            assert "investigating" not in text
            assert "历史背景原文" in text
            return SimpleNamespace(content="历史背景摘要。")
    monkeypatch.setattr(api, "Compressor", Compressor)
    result = build(include_raw=True)
    assert "愿景原文。" in result["narrative"]
    assert "历史背景摘要。" in result["narrative"]
    assert "历史背景原文。" in result["narrative_raw"]
    assert result["narrative"].endswith("Outcome=not_achieved；MF=investigating。")


@pytest.mark.parametrize("endpoint", ["http://remote.test/v1", "https://user:secret@provider.test/v1", "https://provider.test/v1?token=secret", "file:///etc/passwd"])
def test_model_url_rejects_insecure_or_credential_embedded_endpoints(monkeypatch, endpoint):
    monkeypatch.setenv("TKOS_NARRATIVE_MODEL_BASE_URL", endpoint)
    with pytest.raises(GovernedError):
        api._endpoint("TKOS_NARRATIVE_MODEL_BASE_URL")


def test_provider_redirect_is_not_followed(monkeypatch):
    visited = []
    def respond(request):
        visited.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://unexpected.test/steal"})
    client = httpx.Client
    monkeypatch.setattr(api.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        api._provider_json("https://configured.test/chat/completions", "synthetic-secret", {}, 1)
    assert visited == ["https://configured.test/chat/completions"]


def test_public_error_never_echoes_upstream_or_key(monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_ENABLED", "1")
    def fail(*args):
        raise RuntimeError("Bearer synthetic-sensitive-upstream-error")
    monkeypatch.setattr(api, "build_narrative", fail)
    with pytest.raises(GovernedError) as error:
        api.narrative(api.NarrativeRequest(query="query"), "token")
    assert error.value.code == "NARRATIVE_UNAVAILABLE"
    assert "synthetic-sensitive" not in error.value.message
