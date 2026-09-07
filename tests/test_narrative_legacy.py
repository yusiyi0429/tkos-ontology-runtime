"""Real isolated PostgreSQL legacy retrieval through the narrative HTTP API.

Only embedding/chat HTTP responses are controlled fixtures, not a live LLM gate.
Graph source resolution checks real frozen excerpt/hash rows, as required by the
Memory Service boundary; it does not claim to fetch the external source document.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
from pgvector import HalfVector
from pgvector.psycopg import register_vector
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from memory_service_app import narrative as api
from memory_service_app.main import app
from memory_service_runtime.governed import bootstrap
from tests.conftest import DATABASE_URL


EMBEDDING_KEY = "synthetic-legacy-embedding-key"
CHAT_KEY = "synthetic-legacy-chat-key"
VISION = "让每个业务域的交付都有清晰且可核验的业务依据。"
BACKGROUND = "经营原则要求依据确认后的证据评估业务进展。"


@pytest.fixture(scope="module")
def legacy_identity():
    """Provision one test-only governance scope; preserve append-only audit."""
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=3, row_factory=dict_row)
    except psycopg.Error:
        pytest.skip("Legacy integration requires the isolated acceptance PostgreSQL database")
    with conn:
        if conn.execute("SELECT current_database() AS name").fetchone()["name"] != "tkos_runtime_acceptance":
            pytest.skip("Legacy integration never seeds outside the isolated acceptance database")
        conn.commit()
        label = uuid4().hex
        seeded = bootstrap.seed_scope(conn, f"runtime-acceptance-narrative-{label}",
                                      f"runtime-acceptance-narrative-org-{label}")
        with conn.transaction():
            conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (seeded["scope_id"],))
            previous = conn.execute(
                "SELECT * FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s",
                (seeded["scope_id"], seeded["domain_id"]),
            ).fetchone()
            policy = previous["content"]
            policy["action_roles"]["read_legacy_context"] = ["CEO"]
            conn.execute(
                """INSERT INTO gov_activation_policies(policy_revision_id,scope_id,domain_id,
                   policy_id,policy_seq,content,recorded_by) VALUES(%s,%s,%s,%s,2,%s,%s)""",
                (str(uuid4()), seeded["scope_id"], seeded["domain_id"], previous["policy_id"],
                 Jsonb(policy), seeded["actors"]["ceo"]["principal_id"]),
            )
        return seeded


@pytest.fixture
def legacy_rig(legacy_identity, monkeypatch):
    identity = legacy_identity
    tenant, org = identity["tenant_id"], identity["company_id"]
    ids = {name: str(uuid4()) for name in ("generation", "user", "root", "leaf", "relation", "document")}
    expected_sources = {}
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        register_vector(conn)
        conn.execute("INSERT INTO users(user_id,tenant_id,organization_id,kind,display_name) VALUES(%s,%s,%s,'human','Synthetic narrative confirmer')",
                     (ids["user"], tenant, org))
        conn.execute("INSERT INTO context_graph_versions(generation_id,tenant_id,organization_id,label,status) VALUES(%s,%s,%s,'Synthetic narrative sources','current')",
                     (ids["generation"], tenant, org))
        for key, kind, title, statement in (
            ("root", "CompanyVision", "来源清晰的公司愿景", VISION),
            ("leaf", "OperatingPrinciple", "按证据判断进展", BACKGROUND),
        ):
            conn.execute(
                """INSERT INTO semantic_entities(entity_id,tenant_id,organization_id,entity_type,
                   type_key,name,normalized_name,content,rationale,revision,status,embedding,
                   confirmed_by,confirmed_at,graph_generation_id)
                   VALUES(%s,%s,%s,NULL,%s,%s,%s,%s,'Synthetic confirmed source',1,'confirmed',%s,%s,now(),%s)""",
                (ids[key], tenant, org, kind, title, title, Jsonb({"statement" if key == "root" else "principle": statement}),
                 HalfVector([1.0] * 2048), ids["user"], ids["generation"]),
            )
        conn.execute(
            """INSERT INTO semantic_relations(relation_id,tenant_id,organization_id,source_id,target_id,
               relation_type,content,rationale,revision,status,confirmed_by,confirmed_at,graph_generation_id)
               VALUES(%s,%s,%s,%s,%s,'primary_alignment','{}','Principle follows vision',1,'confirmed',%s,now(),%s)""",
            (ids["relation"], tenant, org, ids["leaf"], ids["root"], ids["user"], ids["generation"]),
        )
        for key, excerpt, owner_kind in (
            ("root", VISION, "entity"), ("leaf", BACKGROUND, "entity"),
            ("relation", "经营原则从公司愿景推导，并以证据明确业务进展。", "relation"),
        ):
            fragment = str(uuid4())
            digest = hashlib.sha256(excerpt.encode()).hexdigest()
            expected_sources[ids[key]] = {"fragment_id": fragment, "content_hash": digest}
            # The only varying SQL identifier is this fixed internal enum.
            table, column = ("semantic_entity_source_refs", "entity_id") if owner_kind == "entity" else ("semantic_relation_source_refs", "relation_id")
            conn.execute(
                f"""INSERT INTO {table}({column},graph_generation_id,tenant_id,organization_id,
                    fragment_id,ordinal,excerpt_snapshot,content_hash_snapshot,source_locator_snapshot)
                    VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s)""",
                (ids[key], ids["generation"], tenant, org, fragment, excerpt, digest,
                 Jsonb({"document_id": ids["document"], "filename": "Synthetic narrative source.md", "paragraph_pos": 1})),
            )
    environment = {
        "DATABASE_URL": os.environ.get("APP_DATABASE_URL", DATABASE_URL),
        "TKOS_NARRATIVE_ENABLED": "1", "TKOS_NARRATIVE_LEGACY_ENABLED": "1",
        "TKOS_NARRATIVE_DOMAIN_ID": identity["domain_id"], "TKOS_NARRATIVE_COMPRESSION": "none",
        "TKOS_NARRATIVE_TIMEOUT_SECONDS": "1", "MEMORY_TENANT": tenant, "MEMORY_ORG": org,
        "MEMORY_EMBEDDING_BASE_URL": "https://controlled-narrative.test/v1",
        "MEMORY_EMBEDDING_API_KEY": EMBEDDING_KEY, "MEMORY_EMBEDDING_MODEL": "controlled-embedding",
        "MEMORY_EMBEDDING_DIM": "2048", "TKOS_NARRATIVE_MODEL": "controlled-chat",
        "TKOS_NARRATIVE_MODEL_BASE_URL": "https://controlled-narrative.test/v1",
        "TKOS_NARRATIVE_MODEL_API_KEY": CHAT_KEY,
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    calls = []
    provider = {"chat": "ok", "embedding": "ok"}
    real_client = httpx.Client

    def respond(request):
        calls.append({"path": request.url.path, "payload": json.loads(request.content)})
        assert request.url.host == "controlled-narrative.test"
        if request.url.path.endswith("/embeddings/multimodal"):
            assert request.headers["authorization"] == f"Bearer {EMBEDDING_KEY}"
            if provider["embedding"] == "fail":
                return httpx.Response(502, json={"error": EMBEDDING_KEY})
            return httpx.Response(200, json={"data": {"embedding": [1.0] * 2048}})
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == f"Bearer {CHAT_KEY}"
        text = str(json.loads(request.content)["messages"])
        assert VISION not in text  # Root remains verbatim outside compression.
        assert "outcome_achievement" not in text and identity["outcome"]["object_id"] not in text
        if provider["chat"] == "redirect":
            return httpx.Response(307, headers={"location": "https://unexpected.test/collect"})
        if provider["chat"] == "fail":
            return httpx.Response(502, json={"error": CHAT_KEY})
        content = "" if provider["chat"] == "empty" else "按证据评估业务进展。"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(api.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    rig = SimpleNamespace(identity=identity, ids=ids, sources=expected_sources, calls=calls, provider=provider)
    try:
        yield rig
    finally:
        # Only legacy fixture rows are mutable. Governed bootstrap audit remains
        # in this isolated test database, never deleted by disabling safeguards.
        with psycopg.connect(DATABASE_URL) as conn:
            for table in ("semantic_relation_source_refs", "semantic_entity_source_refs",
                          "semantic_relations", "semantic_entities", "context_graph_versions", "users"):
                conn.execute(f"DELETE FROM {table} WHERE tenant_id=%s AND organization_id=%s", (tenant, org))


def request(rig, *, actor="ceo", **changes):
    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://narrative-test") as client:
            return await client.post("/v1/context-graph/narrative", headers={
                "Authorization": "Bearer " + rig.identity["actors"][actor]["token"],
            }, json={"query": "交付与公司目标有哪些证据依据", "include_raw": True, **changes})
    return asyncio.run(call())


def ledger_counts(rig):
    identity = rig.identity
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (identity["scope_id"],))
        counts = {}
        for table in ("gov_objects", "gov_object_revisions", "gov_lifecycle_events",
                      "gov_action_receipts", "gov_context_snapshots"):
            counts[table] = conn.execute(f"SELECT count(*) FROM {table} WHERE scope_id=%s", (identity["scope_id"],)).fetchone()[0]
        for table in ("context_graph_snapshots", "memory_proposals", "semantic_entities", "semantic_relations"):
            counts[table] = conn.execute(f"SELECT count(*) FROM {table} WHERE tenant_id=%s AND organization_id=%s",
                                        (identity["tenant_id"], identity["company_id"])).fetchone()[0]
        return counts


def test_combined_narrative_uses_real_current_generation_and_resolved_source_hashes(legacy_rig):
    before = ledger_counts(legacy_rig)
    response = request(legacy_rig)
    assert response.status_code == 200, response.text
    body = response.json()
    assert VISION in body["narrative"] and BACKGROUND in body["narrative"]
    assert VISION in body["root_statement"]
    assert body["hit_paths"] == 1
    assert body["governed_facts"]["selected"][0]["outcome_achievement"] == "not_assessed"
    assert "三项独立" in body["narrative"]
    provenance = body["provenance"]["legacy"]
    assert provenance["generation_id"] == legacy_rig.ids["generation"]
    assert provenance["temporal_mode"] == "current_generation"
    assert provenance["retrieved_at"]
    assert len(provenance["source_resolutions"]) == 3
    for owner in provenance["source_resolutions"]:
        assert owner["resolvable"] is True
        assert owner["refs"][0]["resolved"] is True
        assert owner["refs"][0]["document_id"] == legacy_rig.ids["document"]
        expected = legacy_rig.sources[owner["owner_id"]]
        assert owner["refs"][0]["content_hash"] == expected["content_hash"]
        assert owner["refs"][0]["fragment_id"] == expected["fragment_id"]
    assert response.headers["cache-control"] == "no-store"
    assert len(legacy_rig.calls) == 1
    assert ledger_counts(legacy_rig) == before


def test_legacy_company_background_requires_explicit_read_legacy_context_grant(legacy_rig):
    response = request(legacy_rig, actor="domain_dri")
    assert response.status_code == 403
    assert legacy_rig.calls == []
    assert VISION not in response.text


@pytest.mark.parametrize("field", ["tenant", "org", "domain_id"])
def test_caller_cannot_retarget_legacy_scope(legacy_rig, field):
    response = request(legacy_rig, **{field: str(uuid4())})
    assert response.status_code == 403
    assert legacy_rig.calls == []


def test_process_legacy_scope_mismatch_fails_before_embedding(legacy_rig, monkeypatch):
    monkeypatch.setenv("MEMORY_ORG", "runtime-acceptance-unrelated-org")
    response = request(legacy_rig)
    assert response.status_code == 403 and legacy_rig.calls == []


def test_empty_real_graph_is_404_even_with_governed_outcome(legacy_rig):
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("UPDATE semantic_entities SET embedding=NULL WHERE entity_id=%s AND tenant_id=%s AND organization_id=%s",
                     (legacy_rig.ids["leaf"], legacy_rig.identity["tenant_id"], legacy_rig.identity["company_id"]))
    response = request(legacy_rig)
    assert response.status_code == 404
    assert "NARRATIVE_EMPTY" in response.text
    assert len(legacy_rig.calls) == 1


@pytest.mark.parametrize("owner", ["entity", "relation"])
def test_mismatched_frozen_source_hash_fails_closed(legacy_rig, owner):
    table, column, key = ("semantic_entity_source_refs", "entity_id", "leaf") if owner == "entity" else ("semantic_relation_source_refs", "relation_id", "relation")
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(f"UPDATE {table} SET content_hash_snapshot=%s WHERE {column}=%s AND tenant_id=%s AND organization_id=%s",
                     ("0" * 64, legacy_rig.ids[key], legacy_rig.identity["tenant_id"], legacy_rig.identity["company_id"]))
    response = request(legacy_rig)
    assert response.status_code == 503
    assert "NARRATIVE_UNAVAILABLE" in response.text
    assert VISION not in response.text and legacy_rig.ids[key] not in response.text


def test_real_chat_boundary_compresses_legacy_only_and_preserves_governance(legacy_rig, monkeypatch):
    monkeypatch.setenv("TKOS_NARRATIVE_COMPRESSION", "chat")
    response = request(legacy_rig)
    assert response.status_code == 200, response.text
    body = response.json()
    assert VISION in body["narrative"]
    assert "按证据评估业务进展。" in body["narrative"]
    assert BACKGROUND in body["narrative_raw"]
    assert body["governed_facts"]["selected"][0]["outcome_achievement"] == "not_assessed"
    assert body["model"] == "controlled-chat"
    assert len(legacy_rig.calls) == 2


@pytest.mark.parametrize("failure", ["empty", "fail", "redirect"])
def test_chat_failure_is_503_without_secret_or_silent_fallback(legacy_rig, monkeypatch, failure):
    monkeypatch.setenv("TKOS_NARRATIVE_COMPRESSION", "chat")
    legacy_rig.provider["chat"] = failure
    response = request(legacy_rig)
    assert response.status_code == 503
    assert "NARRATIVE_UNAVAILABLE" in response.text
    assert CHAT_KEY not in response.text and EMBEDDING_KEY not in response.text
    assert VISION not in response.text and "narrative" not in response.json()
    assert len(legacy_rig.calls) == 2  # In particular, no redirected credential request.


def test_embedding_failure_is_503_without_key_or_governed_only_fallback(legacy_rig):
    legacy_rig.provider["embedding"] = "fail"
    response = request(legacy_rig)
    assert response.status_code == 503
    assert EMBEDDING_KEY not in response.text
    assert "narrative" not in response.json()
    assert len(legacy_rig.calls) == 1
