"""GET /api/v1/context/dynamic?q&k&budget."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
import psycopg

from memory_service.context_graph import context_pack, query
from memory_service.context_graph.query_contracts import NoCurrentGenerationError, RetrievalBudgets
from memory_service_runtime import provider_http

from adapter.contracts import GkDynamic
from adapter.deps import get_conn
from adapter.errors import EmbeddingUnavailableError, mapped
from adapter.pack_projection import project_dynamic
from adapter.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/context", tags=["dynamic"])


class _ArkMultimodalEmbedder:
    """Adapt Ark's multimodal embeddings endpoint to memory_service's protocol."""

    def __init__(self, settings: Settings) -> None:
        self._url = provider_http.checked_base_url(settings.memory_embedding_base_url) + "/embeddings/multimodal"
        self._key = settings.memory_embedding_api_key
        self._model = settings.memory_embedding_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return [
                _embedding_from_response(
                    provider_http.post_json(
                        self._url,
                        self._key,
                        {"model": self._model, "input": [{"type": "text", "text": text}]},
                        60,
                    )
                )
                for text in texts
            ]
        except Exception as exc:
            # Do not include exception text: compatible clients sometimes echo request
            # headers or URLs, and the API key must never reach an HTTP error detail.
            raise EmbeddingUnavailableError(
                f"embedding 网关调用失败（{exc.__class__.__name__}）"
            ) from exc


def _embedding_from_response(payload: Any) -> list[float]:
    if not isinstance(payload, dict):
        raise ValueError("embedding response must be an object")
    data = payload.get("data")
    if isinstance(data, dict):
        row = data
    elif isinstance(data, list) and data:
        row = data[0]
    else:
        raise ValueError("embedding response data is missing")
    if not isinstance(row, dict):
        raise ValueError("embedding response item must be an object")
    vector = row.get("embedding")
    if not isinstance(vector, (list, tuple)):
        raise ValueError("embedding response item has no vector")
    return list(vector)


def _build_embedder(settings: Settings) -> _ArkMultimodalEmbedder:
    if not settings.embedding_configured:
        raise EmbeddingUnavailableError(
            "MEMORY_EMBEDDING_API_KEY/BASE_URL/MODEL 配置不完整，无法执行语义检索"
        )
    try:
        return _ArkMultimodalEmbedder(settings)
    except ValueError as exc:
        raise EmbeddingUnavailableError(
            "MEMORY_EMBEDDING_BASE_URL 必须是 https（本机可用 http），且不能带凭据、查询串或片段"
        ) from exc


@router.get("/dynamic", response_model=GkDynamic)
@mapped
def dynamic(
    q: Annotated[str, Query(min_length=1, max_length=100)],
    k: Annotated[int, Query(ge=1)],
    budget: Annotated[int, Query(ge=1)],
    *,
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GkDynamic:
    """Run one scoped strategic-path retrieval and project its Context Pack."""
    effective_query = q.strip()
    if not effective_query:
        raise HTTPException(status_code=422, detail="q 不能只包含空白字符")

    embedder = _build_embedder(settings)
    try:
        plan = query.plan_query(
            settings.memory_tenant,
            settings.memory_org,
            effective_query,
            embedder=embedder,
            budgets=RetrievalBudgets(
                hit_limit=k,
                max_paths=min(k, 5),
                lateral_budget=2,
            ),
            embedding_dim=settings.memory_embedding_dim,
        )
        retrieval = query.execute_query(plan, conn=conn)
    except NoCurrentGenerationError:
        return GkDynamic(
            query=effective_query,
            seeds=[],
            text=None,
            warning="no_current_context_graph_generation",
        )

    pack = context_pack.build_context_pack(retrieval)
    narrative = context_pack.render_narrative(pack)
    return project_dynamic(
        pack,
        query=effective_query,
        k=k,
        budget=budget,
        narrative=narrative,
    )


__all__ = ("router", "dynamic")
