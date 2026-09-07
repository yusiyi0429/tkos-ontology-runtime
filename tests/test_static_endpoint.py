"""P0 HTTP wiring over the accepted deterministic renderer."""
from __future__ import annotations

import uuid
from urllib.parse import urlencode

from adapter.main import app
from adapter.settings import Settings, get_settings
from adapter.wm_views import build_code_index
from tests.asgi_client import get
from tests.conftest import DATABASE_URL, Scope, connect


def _settings(scope: Scope, *, viewer_user_id: str) -> Settings:
    return Settings(
        memory_tenant=scope.tenant_id,
        memory_org=scope.organization_id,
        database_url=DATABASE_URL,
        adapter_auth="clark:test",
        viewer_user_id=viewer_user_id,
    )


def test_static_endpoint_returns_markdown_and_uses_configured_human() -> None:
    scope = Scope()
    try:
        with connect() as conn, conn.transaction():
            confirmed = scope.seed_chain(conn, title="P0 接线确认链", confirmed_judgment=True)
            pending = scope.seed_chain(conn, title="P0 接线未确认链", confirmed_judgment=False)
            index = build_code_index(
                conn, tenant_id=scope.tenant_id, organization_id=scope.organization_id,
            )
            confirmed_code = next(
                entry.code
                for entry in index.entries
                if entry.object_id == confirmed["judgment"]["object_id"]
            )

        app.dependency_overrides[get_settings] = lambda: _settings(
            scope, viewer_user_id=scope.user_id or "",
        )
        response = get(
            app,
            "/api/v1/context/static?"
            + urlencode({"viewer": "PSN-01", "as_of": "2026-08-31T00:00:00Z"}),
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/markdown")
        assert response.text.startswith("【公司经营状态】")
        assert (
            f"{confirmed_code} 关于 {confirmed['chain']['title']} 的测试判断"
            in response.text
        )
        assert f"关于 {pending['chain']['title']} 的测试判断" not in response.text
        assert f"viewer：适配器测试人类-{scope.tenant_id}" in response.text
        assert response.text.rstrip().endswith("台账：as_of=2026-08-31T00:00:00Z")
    finally:
        app.dependency_overrides.clear()
        scope.cleanup()


def test_static_endpoint_rejects_invalid_configured_human_as_403() -> None:
    scope = Scope()
    try:
        with connect() as conn, conn.transaction():
            scope.ensure_human(conn)

        app.dependency_overrides[get_settings] = lambda: _settings(
            scope, viewer_user_id=str(uuid.uuid4()),
        )
        response = get(
            app,
            "/api/v1/context/static?"
            + urlencode({"viewer": "PSN-01", "as_of": "2026-08-31T00:00:00Z"}),
        )
        assert response.status_code == 403
        assert response.status_code != 404
    finally:
        app.dependency_overrides.clear()
        scope.cleanup()
