"""Clark compatibility authentication is enforced; native health stays internal."""
from __future__ import annotations

import base64

from adapter.main import app
from adapter.settings import Settings, get_settings
from tests.asgi_client import get
from tests.conftest import DATABASE_URL, Scope


def _settings(scope: Scope, auth: str) -> Settings:
    return Settings(
        memory_tenant=scope.tenant_id,
        memory_org=scope.organization_id,
        database_url=DATABASE_URL,
        adapter_auth=auth,
    )


def test_clark_routes_fail_closed_when_auth_is_not_configured(scope: Scope) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(scope, "")
    try:
        response = get(app, "/healthz", headers={})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert "ADAPTER_AUTH" in response.text


def test_clark_routes_reject_missing_and_wrong_basic_auth(scope: Scope) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(scope, "clark:test")
    wrong = base64.b64encode(b"clark:wrong").decode("ascii")
    try:
        missing = get(app, "/healthz", headers={})
        rejected = get(
            app,
            "/healthz",
            headers={"authorization": f"Basic {wrong}"},
        )
    finally:
        app.dependency_overrides.clear()
    for response in (missing, rejected):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Basic"


def test_native_health_does_not_require_clark_credential(scope: Scope) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(scope, "")
    try:
        response = get(app, "/v1/health", headers={})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["db"] is True
