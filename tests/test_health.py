"""Peer A health and viewer dependency checks."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pgvector import HalfVector

from adapter.deps import get_conn, resolve_viewer
from adapter.main import app
from adapter.settings import Settings, get_settings
from tests.conftest import DATABASE_URL, connect
from tests.conftest import Scope
from tests.asgi_client import get_json as _request


def _settings(
    scope: Scope,
    *,
    database_url: str = DATABASE_URL,
    viewer: str = "",
    embedding: bool = False,
) -> Settings:
    return Settings(
        memory_tenant=scope.tenant_id,
        memory_org=scope.organization_id,
        database_url=database_url,
        adapter_auth="clark:test",
        viewer_user_id=viewer,
        memory_embedding_api_key="test-key" if embedding else "",
        memory_embedding_base_url="http://embedding.test" if embedding else "",
        memory_embedding_model="test-model" if embedding else "",
    )


def test_healthz_reports_db_and_scope_chains(scope: Scope) -> None:
    with connect() as conn, conn.transaction():
        viewer = scope.ensure_human(conn)
        scope.seed_chain(conn, title="healthz 测试链")

    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: _settings(scope, viewer=viewer)
    try:
        status, body = _request(app, "/healthz")
    finally:
        app.dependency_overrides.clear()

    assert status == 200
    assert body["ok"] is True
    assert body["mode"] == "memory_service"
    assert body["db"] is True
    assert body["chains"] == 1
    assert body["embedding"] is False
    assert any("MEMORY_EMBEDDING_" in warning for warning in body["warnings"])


def test_native_health_uses_service_contract_without_clark_auth_warning(scope: Scope) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: _settings(scope)
    try:
        status, body = _request(app, "/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert status == 200
    assert body["mode"] == "memory_service"
    assert body["db"] is True
    assert not any("ADAPTER_AUTH" in warning for warning in body["warnings"])


def test_healthz_reports_embedding_when_configured(scope: Scope) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: _settings(scope, embedding=True)
    try:
        status, body = _request(app, "/healthz")
    finally:
        app.dependency_overrides.clear()

    assert status == 200
    assert body["embedding"] is True
    assert not any("MEMORY_EMBEDDING_" in warning for warning in body["warnings"])


def test_healthz_db_down_is_503_not_404(scope: Scope) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = lambda: _settings(
        scope,
        database_url="postgresql://postgres:devpass@127.0.0.1:1/does_not_exist",
    )
    try:
        status, body = _request(app, "/healthz")
    finally:
        app.dependency_overrides.clear()

    assert status == 503
    assert status != 404
    assert body["ok"] is False
    assert body["db"] is False
    assert body["chains"] == 0
    assert body["warnings"]


def test_settings_reject_empty_scope_and_bad_embedding_dim() -> None:
    with pytest.raises(ValidationError):
        Settings(memory_tenant=" ", memory_org="org", database_url=DATABASE_URL)
    with pytest.raises(ValidationError):
        Settings(memory_tenant="tenant", memory_org=" ", database_url=DATABASE_URL)
    with pytest.raises(ValidationError):
        Settings(
            memory_tenant="tenant",
            memory_org="org",
            database_url=DATABASE_URL,
            memory_embedding_dim=0,
        )


def test_settings_embedding_is_complete_and_stripped() -> None:
    settings = Settings(
        memory_tenant=" tenant ",
        memory_org=" org ",
        database_url=" postgres://example ",
        memory_embedding_api_key=" key ",
        memory_embedding_base_url=" http://embed ",
        memory_embedding_model=" model ",
    )
    assert settings.memory_tenant == "tenant"
    assert settings.memory_org == "org"
    assert settings.embedding_configured is True
    assert settings.memory_embedding_dim == 2048


def test_settings_requires_explicit_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL 未设置"):
        Settings(memory_tenant="tenant", memory_org="org")


def test_settings_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Settings(
            memory_tenant="tenant",
            memory_org="org",
            database_url=DATABASE_URL,
            unexpected="value",
        )


def test_request_connection_registers_pgvector_and_closes(scope: Scope) -> None:
    dependency = get_conn(_settings(scope))
    conn = next(dependency)
    try:
        value = conn.execute("SELECT %s::halfvec", (HalfVector([1.0, 2.0]),)).fetchone()[0]
        assert value.to_list() == [1.0, 2.0]
        assert conn.closed is False
    finally:
        dependency.close()
    assert conn.closed is True


def test_viewer_resolves_in_scope_human(scope: Scope) -> None:
    with connect() as conn, conn.transaction():
        user_id = scope.ensure_human(conn)
        settings = _settings(scope, viewer=user_id)
        first = resolve_viewer(conn, settings)
        second = resolve_viewer(conn, settings)

    assert first.user_id == user_id
    assert first.display_name.startswith("适配器测试人类-")
    assert second == first


def test_viewer_must_be_configured(scope: Scope) -> None:
    with connect() as conn:
        with pytest.raises(HTTPException) as raised:
            resolve_viewer(conn, _settings(scope))
    assert raised.value.status_code == 500
    assert "VIEWER_USER_ID" in str(raised.value.detail)


def test_viewer_rejects_non_human(scope: Scope) -> None:
    # The schema intentionally permits only one agent_service user globally;
    # reuse that fixture row and verify it cannot cross into this human scope.
    with connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE kind='agent_service' LIMIT 1"
        ).fetchone()
        if row is None:
            pytest.skip("数据库没有 agent_service 用户可用于非 human viewer 检查")
        user_id = str(row[0])
        with pytest.raises(HTTPException) as raised:
            resolve_viewer(conn, _settings(scope, viewer=user_id))
    assert raised.value.status_code == 500
    assert "viewer 配置无效" in str(raised.value.detail)
