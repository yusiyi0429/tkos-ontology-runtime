"""本地 facade 的边界回归：默认关闭、viewer 注入、来源/权限检查、静态安全头。"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from memory_service_app import dashboard as facade
from tests.asgi_client import get


HOST = "127.0.0.1:58802"


def fetch(app, path, headers=None):
    merged = {"host": HOST}
    merged.update(headers or {})
    return get(app, path, headers=merged)


def settings(**overrides):
    values = {
        "tkos_dashboard_enabled": True,
        "tkos_dashboard_viewer_token_file": "",
        "tkos_dashboard_env_label": "synthetic",
        "tkos_dashboard_synthetic": True,
        "tkos_dashboard_allowed_hosts": "127.0.0.1:58802,localhost:58802",
        "tkos_dashboard_allowed_origins": "http://127.0.0.1:58802",
        "tkos_dashboard_assets_dir": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def calls(monkeypatch):
    recorded = {}

    def reader(name):
        def call(token, *args, **kwargs):
            recorded[name] = {"token": token, "args": args, "kwargs": kwargs}
            return {"reader": name, "kwargs": kwargs}
        return call

    for name in ("read_overview", "read_objects", "read_detail", "read_revisions",
                 "read_revision", "read_receipt", "read_reviews", "read_evidence",
                 "read_downstream", "read_ontology_catalog", "read_catalog_objects"):
        monkeypatch.setattr(facade.reads, name, reader(name))
    return recorded


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "viewer-token"
    path.write_text("viewer-token-" + "x" * 40)
    path.chmod(0o600)
    return path


def build(monkeypatch, **overrides):
    monkeypatch.setattr(facade, "_enabled_from_env", lambda: True)
    monkeypatch.setattr(facade, "get_settings", lambda: settings(**overrides))
    app = FastAPI()
    facade.mount_dashboard(app)
    return app


def test_disabled_dashboard_is_absent(monkeypatch):
    monkeypatch.setattr(facade, "_enabled_from_env", lambda: False)
    app = FastAPI()
    facade.mount_dashboard(app)
    assert fetch(app, "/dashboard/api/v1/overview").status_code == 404


def test_enabled_without_viewer_never_falls_back(monkeypatch, calls):
    app = build(monkeypatch)
    response = fetch(app, "/dashboard/api/v1/overview")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DASHBOARD_VIEWER_UNAVAILABLE"
    assert "read_overview" not in calls


def test_viewer_token_is_injected_server_side_only(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    response = fetch(app, "/dashboard/api/v1/overview")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert calls["read_overview"]["token"] == token_file.read_text()
    assert response.json()["kwargs"]["environment"]["label"] == "synthetic"
    assert "token" not in response.text


def test_group_or_world_accessible_token_file_is_rejected(monkeypatch, calls, tmp_path):
    path = tmp_path / "loose-token"
    path.write_text("viewer-token-" + "x" * 40)
    path.chmod(0o644)
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(path))
    assert fetch(app, "/dashboard/api/v1/overview").status_code == 503
    short = tmp_path / "short"
    short.write_text("too-short")
    short.chmod(0o600)
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(short))
    assert fetch(app, "/dashboard/api/v1/overview").status_code == 503
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(tmp_path / "missing"))
    assert fetch(app, "/dashboard/api/v1/overview").status_code == 503


def test_host_origin_and_sec_fetch_checks(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    assert get(app, "/dashboard/api/v1/overview").status_code == 403  # missing Host rejected
    assert get(app, "/dashboard/api/v1/overview", headers={"host": "evil.example"}).status_code == 403
    assert fetch(app, "/dashboard/api/v1/overview",
                 {"origin": "https://evil.example"}).status_code == 403
    assert fetch(app, "/dashboard/api/v1/overview",
                 {"sec-fetch-site": "cross-site"}).status_code == 403
    response = fetch(app, "/dashboard/api/v1/overview",
                     {"origin": "http://127.0.0.1:58802", "sec-fetch-site": "same-origin"})
    assert response.status_code == 200
    # An empty host policy fails closed instead of trusting any Host.
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file),
                tkos_dashboard_allowed_hosts="")
    response = fetch(app, "/dashboard/api/v1/overview")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DASHBOARD_HOST_POLICY_UNAVAILABLE"


def test_facade_registers_only_explicit_get_reads(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    assert fetch(app, "/dashboard/api/v1/actions").status_code == 404
    assert fetch(app, "/dashboard/api/v1/context-packs").status_code == 404
    assert fetch(app, "/dashboard/api/v1/objects").status_code == 422  # group is required


def test_facade_rejects_unknown_query_parameters(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    response = fetch(app, "/dashboard/api/v1/overview?verbose=1")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    response = fetch(app, "/dashboard/api/v1/objects?group=mission&owner=1")
    assert response.status_code == 422


def test_downstream_route_requires_an_exact_revision(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    object_id = "00000000-0000-0000-0000-000000000001"
    revision_id = "00000000-0000-0000-0000-000000000002"
    assert fetch(app, f"/dashboard/api/v1/objects/{object_id}/downstream").status_code == 422
    response = fetch(app, f"/dashboard/api/v1/objects/{object_id}/downstream?revision_id={revision_id}")
    assert response.status_code == 200
    assert calls["read_downstream"]["args"] == (object_id, revision_id)


def test_catalog_reads_are_explicit_token_injected_gets(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file))
    response = fetch(app, "/dashboard/api/v1/ontology/catalog")
    assert response.status_code == 200
    assert calls["read_ontology_catalog"]["token"] == token_file.read_text()
    assert "token" not in response.text
    assert fetch(app, "/dashboard/api/v1/catalog/objects").status_code == 422
    assert fetch(app, "/dashboard/api/v1/catalog/objects?object_type=Signal&verbose=1").status_code == 422
    response = fetch(app, "/dashboard/api/v1/catalog/objects?object_type=Signal&limit=3")
    assert response.status_code == 200
    assert calls["read_catalog_objects"]["kwargs"] == {"object_type": "Signal", "domain_id": None,
                                                       "limit": 3, "cursor": None}
    assert calls["read_catalog_objects"]["token"] == token_file.read_text()


def test_missing_compiled_assets_fail_readably(monkeypatch, calls, token_file, tmp_path):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file),
                tkos_dashboard_assets_dir=str(tmp_path / "no-assets"))
    response = fetch(app, "/dashboard/")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DASHBOARD_ASSETS_MISSING"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")


def test_static_index_and_hashed_assets_get_security_headers(monkeypatch, calls, token_file, tmp_path):
    assets = tmp_path / "dist"
    (assets / "assets").mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><div id=\"root\"></div>")
    (assets / "assets" / "app-abc123.js").write_text("console.log(1)")
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file),
                tkos_dashboard_assets_dir=str(assets))
    response = fetch(app, "/dashboard/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    response = fetch(app, "/dashboard/assets/app-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    assert get(app, "/dashboard/assets/app-abc123.js", headers={"host": "evil.example"}).status_code == 403


def test_bare_allowed_host_accepts_an_ephemeral_port(monkeypatch, calls, token_file):
    app = build(monkeypatch, tkos_dashboard_viewer_token_file=str(token_file),
                tkos_dashboard_allowed_hosts="127.0.0.1,localhost")
    assert fetch(app, "/dashboard/api/v1/overview", {"host": "127.0.0.1:54321"}).status_code == 200
    assert fetch(app, "/dashboard/api/v1/overview", {"host": "localhost:1234"}).status_code == 200
    assert get(app, "/dashboard/api/v1/overview",
               headers={"host": "127.0.0.1.evil.example"}).status_code == 403
