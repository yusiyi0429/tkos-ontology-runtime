from __future__ import annotations

import json

import httpx
import pytest

from adapter import routes_dynamic
from adapter.main import app
from adapter.settings import Settings, get_settings
from tests.conftest import DATABASE_URL, Scope
from tests.asgi_client import get_json as _request


def _settings(scope: Scope, *, embedding: bool) -> Settings:
    return Settings(
        memory_tenant=scope.tenant_id,
        memory_org=scope.organization_id,
        database_url=DATABASE_URL,
        adapter_auth="clark:test",
        viewer_user_id="",
        memory_embedding_api_key="secret-key" if embedding else "",
        memory_embedding_base_url="https://ark.test/v1" if embedding else "",
        memory_embedding_model="test-model" if embedding else "",
        memory_embedding_dim=2048,
    )


def _with_settings(scope: Scope, settings: Settings) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: settings


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        routes_dynamic.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def test_query_over_100_chars_is_422_without_building_embedder(
    scope: Scope, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_settings(scope, _settings(scope, embedding=True))
    monkeypatch.setattr(
        routes_dynamic,
        "_build_embedder",
        lambda settings: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    try:
        status, body = _request(
            app,
            "/api/v1/context/dynamic?q=" + ("q" * 101) + "&k=2&budget=100",
        )
    finally:
        _clear_overrides()
    assert status == 422
    assert "detail" in body


def test_missing_embedding_configuration_is_503_not_404(scope: Scope) -> None:
    _with_settings(scope, _settings(scope, embedding=False))
    try:
        status, body = _request(app, "/api/v1/context/dynamic?q=问题&k=2&budget=100")
    finally:
        _clear_overrides()
    assert status == 503
    assert status != 404
    assert "embedding" in body["detail"].lower()


def test_ark_http_failure_is_503_without_api_key_in_detail(
    monkeypatch: pytest.MonkeyPatch, scope: Scope
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://ark.test/v1/embeddings/multimodal")
        assert request.headers["Authorization"] == "Bearer secret-key"
        assert json.loads(request.content) == {
            "model": "test-model",
            "input": [{"type": "text", "text": "问题"}],
        }
        return httpx.Response(502, json={"error": "gateway offline"})

    _patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    _with_settings(scope, _settings(scope, embedding=True))
    try:
        status, body = _request(app, "/api/v1/context/dynamic?q=问题&k=2&budget=100")
    finally:
        _clear_overrides()
    assert status == 503
    assert status != 404
    assert "secret-key" not in json.dumps(body)
    assert "embedding" in body["detail"].lower()


def test_dict_ark_response_reaches_no_current_generation_200(
    monkeypatch: pytest.MonkeyPatch, scope: Scope
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://ark.test/v1/embeddings/multimodal")
        assert request.headers["Authorization"] == "Bearer secret-key"
        assert json.loads(request.content) == {
            "model": "test-model",
            "input": [{"type": "text", "text": "no-current-generation"}],
        }
        return httpx.Response(200, json={"data": {"embedding": [1.0] * 2048}})

    _patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    _with_settings(scope, _settings(scope, embedding=True))
    try:
        status, body = _request(
            app,
            "/api/v1/context/dynamic?q=no-current-generation&k=3&budget=1000",
        )
    finally:
        _clear_overrides()
    assert status == 200
    assert body == {
        "query": "no-current-generation",
        "seeds": [],
        "text": None,
        "warning": "no_current_context_graph_generation",
    }


def test_list_ark_response_is_supported(monkeypatch: pytest.MonkeyPatch, scope: Scope) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.25, 0.5]}]})

    _patch_httpx_client(monkeypatch, httpx.MockTransport(handler))
    embedder = routes_dynamic._ArkMultimodalEmbedder(_settings(scope, embedding=True))
    assert embedder.embed(["hello"]) == [[0.25, 0.5]]


def test_query_and_numeric_parameters_are_validated(scope: Scope) -> None:
    _with_settings(scope, _settings(scope, embedding=False))
    try:
        status, _ = _request(app, "/api/v1/context/dynamic?q=%20&k=1&budget=1")
        assert status == 422
        status, _ = _request(app, "/api/v1/context/dynamic?q=q&k=0&budget=1")
        assert status == 422
        status, _ = _request(app, "/api/v1/context/dynamic?q=q&k=1&budget=0")
        assert status == 422
    finally:
        _clear_overrides()
