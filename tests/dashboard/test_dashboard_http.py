"""HTTP 边界测试：Bearer 要求、allowlist、no-store、错误形状（无数据库）。"""
from __future__ import annotations

import pytest

from memory_service_app.main import app
from memory_service_runtime.governed import dashboard, dashboard_routes
from tests.asgi_client import get

from dashboard_fakes import CTX, uid


TOKEN = "synthetic-token-for-dashboard-http-tests-0000000000000000000"

ENDPOINTS = [
    "/v1/dashboard/overview",
    f"/v1/dashboard/objects?group=mission",
    f"/v1/dashboard/objects/{uid(10)}",
    f"/v1/dashboard/objects/{uid(10)}/revisions",
    f"/v1/dashboard/objects/{uid(10)}/revisions/{uid(11)}",
    f"/v1/dashboard/objects/{uid(10)}/reviews",
    f"/v1/dashboard/action-receipts/{uid(12)}",
    f"/v1/dashboard/evidence-assets/{uid(10)}/revisions/{uid(11)}",
]


def auth(extra=None):
    return {"authorization": f"Bearer {TOKEN}", **(extra or {})}


def test_every_dashboard_endpoint_requires_bearer_identity():
    for path in ENDPOINTS:
        response = get(app, path, headers={})
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.fixture
def patched_reads(monkeypatch):
    calls = {}

    def make(name):
        def reader(token, *args, **kwargs):
            calls[name] = {"token": token, "args": args, "kwargs": kwargs}
            if name == "read_evidence":
                return {"evidence": True}
            return {"schema_version": dashboard.SCHEMA_VERSION, "reader": name,
                    "args": list(args), "kwargs": kwargs}
        return reader

    for name in ("read_overview", "read_objects", "read_detail", "read_revisions",
                 "read_revision", "read_receipt", "read_reviews", "read_evidence"):
        monkeypatch.setattr(dashboard_routes, name, make(name))
    return calls


def test_overview_returns_reader_payload_with_no_store(patched_reads):
    response = get(app, "/v1/dashboard/overview", headers=auth())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["reader"] == "read_overview"
    assert patched_reads["read_overview"]["token"] == TOKEN


def test_objects_query_is_parsed_and_forwarded(patched_reads):
    response = get(app, f"/v1/dashboard/objects?group=mission&basis=historical&limit=7"
                        f"&object_type=Mission&owner_id={uid(50)}&period_from=2026-09-01T00:00:00Z",
                   headers=auth())
    assert response.status_code == 200
    kwargs = patched_reads["read_objects"]["kwargs"]
    assert kwargs["group"] == "mission"
    assert kwargs["basis"] == "historical"
    assert kwargs["limit"] == 7
    assert kwargs["object_type"] == "Mission"
    assert kwargs["owner_id"] == uid(50)
    assert kwargs["period_from"].startswith("2026-09-01T00:00:00")


def test_detail_parses_optional_selection(patched_reads):
    response = get(app, f"/v1/dashboard/objects/{uid(10)}?revision_id={uid(11)}&strategy_id={uid(12)}",
                   headers=auth())
    assert response.status_code == 200
    kwargs = patched_reads["read_detail"]["kwargs"]
    assert kwargs["revision_id"] == uid(11)
    assert kwargs["strategy_id"] == uid(12)


@pytest.mark.parametrize("path", [
    "/v1/dashboard/overview?verbose=1",
    "/v1/dashboard/overview?strategy_id=1&strategy_id=2",
    "/v1/dashboard/overview?strategy_id=not-a-uuid",
    "/v1/dashboard/objects",
    "/v1/dashboard/objects?group=mission&group=pco",
    "/v1/dashboard/objects?group=mission&basis=",
    f"/v1/dashboard/objects?group=mission&limit=0",
    f"/v1/dashboard/objects?group=mission&limit=101",
    f"/v1/dashboard/objects?group=mission&limit=abc",
    f"/v1/dashboard/objects?group=mission&domain_id=not-a-uuid",
    f"/v1/dashboard/objects?group=mission&period_from=not-a-time",
    f"/v1/dashboard/objects/{uid(10)}?cursor=x",
    f"/v1/dashboard/objects/{uid(10)}/revisions?cursor={'A' * 5000}",
    f"/v1/dashboard/objects/{uid(10)}/revisions/{uid(11)}?extra=1",
])
def test_strict_query_validation_is_invalid_request(path, patched_reads):
    response = get(app, path, headers=auth())
    assert response.status_code == 422, path
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_unknown_route_does_not_fall_back_to_an_action_route():
    response = get(app, "/v1/dashboard/actions", headers=auth())
    assert response.status_code == 404
    response = get(app, "/v1/dashboard/objects", headers=auth(), )
    assert response.status_code == 422  # missing required group, never an implicit listing
