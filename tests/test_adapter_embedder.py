from __future__ import annotations

import httpx
import pytest

from adapter import routes_dynamic
from adapter.errors import EmbeddingUnavailableError
from adapter.settings import Settings


def _settings(base_url: str) -> Settings:
    return Settings(
        memory_tenant="tenant-embedder-test",
        memory_org="org-embedder-test",
        database_url="postgresql://unused.test/db",
        memory_embedding_api_key="synthetic-secret",
        memory_embedding_base_url=base_url,
        memory_embedding_model="test-model",
    )


def _serve(monkeypatch: pytest.MonkeyPatch, respond) -> list[str]:
    visited: list[str] = []
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(str(request.url))
        return respond(request)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    return visited


@pytest.mark.parametrize("base_url", ["http://remote.test/v1", "https://user:secret@provider.test/v1", "https://provider.test/v1?token=secret"])
def test_insecure_or_credential_embedded_base_url_is_unavailable(base_url):
    with pytest.raises(EmbeddingUnavailableError) as error:
        routes_dynamic._build_embedder(_settings(base_url))
    assert "secret" not in str(error.value)


def test_redirect_is_not_followed_with_the_key(monkeypatch):
    visited = _serve(monkeypatch, lambda request: httpx.Response(307, headers={"location": "https://unexpected.test/steal"}))
    embedder = routes_dynamic._build_embedder(_settings("https://ark.test/v1"))
    with pytest.raises(EmbeddingUnavailableError) as error:
        embedder.embed(["hello"])
    assert visited == ["https://ark.test/v1/embeddings/multimodal"]
    assert "synthetic-secret" not in str(error.value)


def test_oversized_response_is_unavailable(monkeypatch):
    # Valid JSON with a usable vector: only the size bound can reject it.
    padded = {"data": [{"embedding": [0.25, 0.5]}], "padding": "x" * 2_000_000}
    _serve(monkeypatch, lambda request: httpx.Response(200, json=padded))
    embedder = routes_dynamic._build_embedder(_settings("https://ark.test/v1"))
    with pytest.raises(EmbeddingUnavailableError):
        embedder.embed(["hello"])


def test_vector_is_read_from_the_provider_response(monkeypatch):
    _serve(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"embedding": [0.25, 0.5]}]}))
    assert routes_dynamic._build_embedder(_settings("http://127.0.0.1:9/v1")).embed(["hello"]) == [[0.25, 0.5]]
