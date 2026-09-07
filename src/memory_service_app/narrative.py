"""Clark-compatible narrative reads over governed facts and optional legacy memory.

The language model may compress legacy background only. Authoritative delivery,
Outcome and MF facts are rendered deterministically and never sent for rewriting.
No action, receipt, context snapshot or business record is created by this API.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
import httpx
from pgvector.psycopg import register_vector
import psycopg
from psycopg.rows import dict_row
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from memory_service.context_graph import context_pack, query
from memory_service.context_graph.query_contracts import RetrievalBudgets
from memory_service_runtime.config import env_value
from memory_service_runtime.governed import db, narrative_facts
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.routes import bearer


router = APIRouter(prefix="/v1/context-graph", tags=["narrative"])
MAX_TEXT = 250_000


def enabled() -> bool:
    return os.environ.get("TKOS_NARRATIVE_ENABLED", "0") == "1"


def unavailable() -> GovernedError:
    return GovernedError("NARRATIVE_UNAVAILABLE", "Narrative sources or model are unavailable.", status=503)


def digest(value) -> str:
    raw = value if isinstance(value, str) else json.dumps(db.jsonable(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class NarrativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
    include_raw: bool = False
    tenant: str | None = Field(default=None, min_length=1, max_length=200)
    org: str | None = Field(default=None, min_length=1, max_length=200)
    domain_id: uuid.UUID | None = None
    object_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=50)
    valid_at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None

    @field_validator("valid_at", "known_at", mode="before")
    @classmethod
    def iso_time(cls, value):
        if value is not None and not isinstance(value, str):
            raise ValueError("Time must be an ISO8601 string with timezone")
        return value

    @field_validator("object_ids")
    @classmethod
    def distinct(cls, values):
        if values is not None and len(set(values)) != len(values):
            raise ValueError("Object IDs must be distinct")
        return values


class NarrativeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: str = Field(min_length=1)
    narrative_raw: str | None = None
    hit_paths: int = Field(ge=0)
    lateral_nodes: int = Field(ge=0)
    root_statement: str
    tenant: str
    org: str
    model: str
    narrative_raw_chars: int = Field(ge=0)
    narrative_chars: int = Field(ge=0)
    governed_facts: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class Config:
    legacy: bool
    compression: str
    domain_id: str
    timeout: float

    @classmethod
    def load(cls):
        try:
            mode = os.environ.get("TKOS_NARRATIVE_COMPRESSION", "none").strip()
            timeout = float(os.environ.get("TKOS_NARRATIVE_TIMEOUT_SECONDS", "20"))
            domain = os.environ.get("TKOS_NARRATIVE_DOMAIN_ID", "").strip()
            if mode not in {"none", "chat"} or not 1 <= timeout <= 60:
                raise ValueError()
            if domain:
                domain = str(uuid.UUID(domain))
            return cls(os.environ.get("TKOS_NARRATIVE_LEGACY_ENABLED", "0") == "1", mode, domain, timeout)
        except (ValueError, TypeError) as exc:
            raise unavailable() from exc


def _resolve_domain(conn, ctx, body: NarrativeRequest, config: Config) -> str:
    if (body.tenant is not None and body.tenant != ctx.tenant_id) or (body.org is not None and body.org != ctx.company_id):
        raise GovernedError("FORBIDDEN")
    requested = str(body.domain_id) if body.domain_id else config.domain_id
    if config.domain_id and requested != config.domain_id:
        raise GovernedError("FORBIDDEN")
    if not requested:
        allowed = []
        for domain in sorted({item["domain_id"] for item in ctx.assignments}):
            try:
                db.authorize_domain(conn, ctx, domain, "read")
                allowed.append(domain)
            except GovernedError as exc:
                if exc.code != "FORBIDDEN":
                    raise
        if len(allowed) != 1:
            raise GovernedError("INVALID_REQUEST", "Select one authorized domain for this narrative.")
        requested = allowed[0]
    db.authorize_domain(conn, ctx, requested, "read")
    if config.legacy:
        # Legacy rows have company scope but no per-domain ACL. A separate,
        # explicit policy grant is required before exposing company background.
        db.authorize_domain(conn, ctx, requested, "read_legacy_context")
        if os.environ.get("MEMORY_TENANT", "").strip() != ctx.tenant_id or os.environ.get("MEMORY_ORG", "").strip() != ctx.company_id:
            raise GovernedError("FORBIDDEN")
        if body.valid_at is not None or body.known_at is not None:
            raise GovernedError("INVALID_REQUEST", "Historical queries require governed-only mode; legacy graph retrieval selects its current generation.")
    return requested


def _endpoint(name: str) -> str:
    value = env_value(name, required=True).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise unavailable()
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise unavailable()
    return value


def _provider_json(url: str, key: str, payload: dict, timeout: float) -> dict:
    # Never follow a redirect with service credentials; never expose provider
    # response/error text, including credentials echoed by an upstream.
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=payload) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise unavailable()
    result = json.loads(data)
    if not isinstance(result, dict):
        raise unavailable()
    return result


class Embedder:
    def __init__(self, timeout: float):
        self.timeout = timeout

    def embed(self, texts):
        from adapter.routes_dynamic import _embedding_from_response
        key = env_value("MEMORY_EMBEDDING_API_KEY", required=True)
        base = _endpoint("MEMORY_EMBEDDING_BASE_URL")
        model = env_value("MEMORY_EMBEDDING_MODEL", required=True)
        return [_embedding_from_response(_provider_json(base + "/embeddings/multimodal", key,
                {"model": model, "input": [{"type": "text", "text": text}]}, self.timeout)) for text in texts]


class Compressor:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.model = env_value("TKOS_NARRATIVE_MODEL", required=True)

    def chat(self, messages, *, max_tokens):
        result = _provider_json(_endpoint("TKOS_NARRATIVE_MODEL_BASE_URL") + "/chat/completions",
            env_value("TKOS_NARRATIVE_MODEL_API_KEY", required=True),
            {"model": self.model, "messages": messages, "max_tokens": min(max_tokens, 4096)}, self.timeout)
        content = result["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_TEXT:
            raise unavailable()
        return SimpleNamespace(content=content)


@contextmanager
def _legacy_connection():
    with psycopg.connect(env_value("DATABASE_URL", required=True), connect_timeout=5, row_factory=dict_row) as conn:
        register_vector(conn)
        conn.commit()  # Type discovery only; query.execute_query requires IDLE.
        yield conn


def _legacy_pack(body: NarrativeRequest, config: Config, ctx):
    plan = query.plan_query(ctx.tenant_id, ctx.company_id, body.query,
        embedder=Embedder(config.timeout), embedding_dim=int(os.environ.get("MEMORY_EMBEDDING_DIM", "2048")),
        budgets=RetrievalBudgets(hit_limit=5, max_paths=5, lateral_budget=2))
    return context_pack.build_context_pack(query.execute_query(plan, _connect=_legacy_connection))


def _legacy_provenance(pack) -> dict:
    return {"generation_id": pack.generation.generation_id, "pack_sha256": digest(pack.to_json()),
        "temporal_mode": "current_generation",
        "source_resolutions": [{"owner_kind": owner.owner_kind, "owner_id": owner.owner_id,
            "resolvable": owner.resolvable,
            "refs": [{"fragment_id": ref.fragment_id, "document_id": ref.document_id,
                "content_hash": ref.content_hash, "resolved": ref.resolved} for ref in owner.refs]}
            for owner in pack.source_resolutions]}


def build_narrative(body: NarrativeRequest, token: str) -> dict:
    config = Config.load()
    now = datetime.now(timezone.utc)
    valid_at, known_at = body.valid_at or now, body.known_at or now
    if valid_at > now or known_at > now:
        raise GovernedError("INVALID_REQUEST", "Narrative query times cannot be in the future.")
    with db.transaction(token) as (conn, ctx):
        domain = _resolve_domain(conn, ctx, body, config)
        identity = (ctx.scope_id, ctx.principal_id, ctx.auth_epoch)
        facts = narrative_facts.collect_facts(conn, ctx, domain_id=domain, query=body.query,
            object_ids=[str(item) for item in body.object_ids] if body.object_ids else None,
            valid_at=valid_at, known_at=known_at)
    # No model/embedding network calls while holding the scope's auth fence.
    pack = _legacy_pack(body, config, ctx) if config.legacy else None
    legacy_retrieved_at = datetime.now(timezone.utc) if pack else None
    if pack and not pack.main_paths and not pack.lateral_nodes:
        raise GovernedError("NARRATIVE_EMPTY", "The required legacy graph has no matching context.", status=404)
    legacy_raw = context_pack.render_narrative(pack) if pack else ""
    root = context_pack.root_statement(pack) if pack else ""
    authoritative = narrative_facts.render_facts(facts) if facts["selected"] else ""
    if not authoritative and not legacy_raw.strip():
        raise GovernedError("NARRATIVE_EMPTY", "No authorized effective context is available.", status=404)
    if len(legacy_raw) + len(authoritative) > MAX_TEXT:
        raise GovernedError("NARRATIVE_TOO_LARGE", "Narrow the domain or object selection.", status=422)
    model = "deterministic-v1"
    compression_applied = False
    legacy_text = legacy_raw
    if legacy_raw.strip() and config.compression == "chat":
        # Preserve the root and all governance facts verbatim. Only legacy
        # background is subject to the existing lossy compression protocol.
        rest = context_pack._narrative_without_root(pack)
        if rest.strip():
            compressor = Compressor(config.timeout)
            compressed = context_pack.compress_narrative(rest, compressor=compressor)
            legacy_text = context_pack._join_root_and_rest(root, compressed)
            model = compressor.model
            compression_applied = True
    def combine(background):
        parts = []
        if background.strip():
            parts.append("【历史语义背景：不替代 Runtime 交付、Outcome 或 MF 验收结论】\n" + background)
        if authoritative:
            parts.append(authoritative)
        return "\n\n".join(parts)
    raw, text = combine(legacy_raw), combine(legacy_text)
    # A revoked credential/policy must not receive material after a slow model
    # call. Current rights fence the final response, including historical facts.
    with db.transaction(token) as (conn, current):
        if (current.scope_id, current.principal_id, current.auth_epoch) != identity:
            raise GovernedError("NARRATIVE_AUTH_CHANGED", "Authority changed while assembling context; retry with current rights.", status=409)
        _resolve_domain(conn, current, body, config)
        for fact in facts["selected"]:
            db.revision_row(conn, current, fact["object_id"], fact["revision_id"])
            for source in fact.get("source_refs", []):
                db.revision_row(conn, current, source["object_id"], source["revision_id"])
        if pack:
            row = conn.execute("SELECT generation_id FROM context_graph_versions WHERE tenant_id=%s AND organization_id=%s AND status='current'",
                (current.tenant_id, current.company_id)).fetchone()
            if row is None or str(row["generation_id"]) != pack.generation.generation_id:
                raise GovernedError("NARRATIVE_SOURCE_CHANGED", "The legacy context generation changed; retry.", status=409)
    result = {"narrative": text, "hit_paths": len(pack.main_paths) if pack else 0,
        "lateral_nodes": len(pack.lateral_nodes) if pack else 0, "root_statement": root,
        "tenant": ctx.tenant_id, "org": ctx.company_id, "model": model,
        "narrative_raw_chars": len(raw), "narrative_chars": len(text), "governed_facts": facts,
        "provenance": {"schema_version": "narrative-provenance.v1", "scope_id": ctx.scope_id,
            "domain_id": domain, "principal_id": ctx.principal_id, "auth_epoch": ctx.auth_epoch,
            "valid_at": valid_at, "known_at": known_at, "governed_sha256": digest(facts),
            "legacy": {**_legacy_provenance(pack), "retrieved_at": legacy_retrieved_at} if pack else None,
            "narrative_sha256": digest(text), "compression_mode": config.compression,
            "compression_applied": compression_applied}}
    if body.include_raw:
        result["narrative_raw"] = raw
    return db.jsonable(result)


@router.post("/narrative", response_model=NarrativeResponse, response_model_exclude_none=True)
def narrative(body: NarrativeRequest, token: Annotated[str, Depends(bearer)]):
    if not enabled():
        raise GovernedError("NARRATIVE_NOT_CONFIGURED", "Narrative integration is not enabled.", status=503)
    try:
        result = NarrativeResponse.model_validate(build_narrative(body, token))
        return JSONResponse(result.model_dump(mode="json", exclude_none=True), headers={"Cache-Control": "no-store"})
    except GovernedError:
        raise
    except Exception as exc:
        raise unavailable() from exc


def public_health() -> JSONResponse:
    """Minimal anonymous Clark probe, enabled only with the narrative surface."""
    ok = False
    try:
        with psycopg.connect(env_value("DATABASE_URL", required=True), connect_timeout=3) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                ok = conn.execute("SELECT 1").fetchone()[0] == 1
    except Exception:
        pass
    return JSONResponse({"ok": ok, "service": "tkos-ontology-runtime", "narrative": True},
                        status_code=200 if ok else 503, headers={"Cache-Control": "no-store"})
