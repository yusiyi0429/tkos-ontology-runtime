"""HTTP 边界测试：认证、参数 allowlist、no-store 头（事务用内存替身，无数据库）。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from memory_service_app.main import app
from memory_service_runtime.governed import db, routes, workbench
from tests.asgi_client import get

from fakes import CTX, FakeConn, domain_row, uid


TOKEN = "synthetic-token-for-http-boundary-tests-00000000000000000000"


@pytest.fixture
def fake_transaction(monkeypatch):
    @contextmanager
    def transaction(token):
        assert isinstance(token, str) and token
        yield FakeConn(domains=[domain_row(1, "域甲")]), CTX

    monkeypatch.setattr(db, "transaction", transaction)
    monkeypatch.setattr(workbench.db, "authorize_domain",
                        lambda conn, ctx, domain_id, action_type="read": [{"assignment_id": uid(1002)}])
    return transaction


def auth(extra=None):
    return {"authorization": f"Bearer {TOKEN}", **(extra or {})}


def test_read_endpoints_require_bearer_identity():
    for path in ("/v1/object-types", "/v1/domains", f"/v1/objects?domain_id={uid(1)}",
                 f"/v1/objects/{uid(1)}/revisions", f"/v1/objects/{uid(1)}/relations",
                 f"/v1/objects/{uid(1)}/action-receipts", f"/v1/objects/{uid(1)}/responsibility"):
        response = get(app, path, headers={})
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_object_types_returns_catalog_with_no_store(fake_transaction):
    response = get(app, "/v1/object-types", headers=auth())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schema_version"] == workbench.SCHEMA_VERSION
    # Original types, seven A2 types, and the A3-only ExecutionPlan.
    assert len(body["items"]) == 19
    modes = {item["object_type"]: item["creation_mode"] for item in body["items"]}
    assert modes["ProtocolSentinel"] == "control_plane_only"
    assert modes["ExecutionPlan"] == "generic_action"


def test_list_endpoints_set_no_store(fake_transaction):
    response = get(app, "/v1/domains", headers=auth())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["items"] == [{"domain_id": uid(1), "name": "域甲"}]


@pytest.mark.parametrize("path", [
    "/v1/domains?limit=0",
    "/v1/domains?limit=101",
    "/v1/domains?limit=abc",
    "/v1/domains?limit=10&limit=20",
    "/v1/domains?verbose=1",
    "/v1/domains?cursor=" + "A" * 5000,
    "/v1/objects",
    f"/v1/objects?domain_id=not-a-uuid",
    f"/v1/objects?domain_id={uid(1)}&domain_id={uid(2)}",
    f"/v1/objects/{uid(1)}/relations?revision_id=not-a-uuid",
])
def test_strict_query_validation_is_invalid_request(path):
    # 纯参数错误在打开事务前或参数解析阶段拒绝，无需数据库。
    response = get(app, path, headers=auth())
    assert response.status_code == 422, path
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_invalid_cursor_shape_is_422_not_500(fake_transaction):
    response = get(app, "/v1/domains?cursor=%%%not-base64%%%", headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
