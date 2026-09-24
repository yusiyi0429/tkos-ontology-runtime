"""证据上传与下载：对象存储读写在 scope 栅栏之外，键按内容寻址，上传带幂等键可重放。

对象存储用内存假件；需要真实库的用例经 bootstrap 播种，标 owner。
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import os
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row
import pytest

from memory_service.context_graph import snapshot_storage
from memory_service_runtime.governed import db, evidence


class FakeStore:
    """内存里的版本化桶；gate 设上时，put/get 会停在那里等放行。"""

    def __init__(self) -> None:
        self.objects: dict[str, list[dict]] = {}
        self.puts = 0
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def _wait(self) -> None:
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(10)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _missing()
        latest = self.objects[Key][-1]
        return {"VersionId": latest["version"], "ContentLength": len(latest["body"]),
                "Metadata": latest["metadata"]}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        self._wait()
        self.puts += 1
        version = uuid4().hex
        self.objects.setdefault(Key, []).append({"version": version, "body": Body, "metadata": Metadata})
        return {"VersionId": version}

    def get_paginator(self, name):
        assert name == "list_object_versions"
        objects = self.objects

        class Pages:
            @staticmethod
            def paginate(Bucket, Prefix):
                yield {"Versions": [{"Key": key, "VersionId": item["version"], "Size": len(item["body"]),
                                     "LastModified": "2026-09-24T00:00:00Z"}
                                    for key, items in objects.items() if key.startswith(Prefix) for item in items]}

        return Pages()

    def get_object(self, Bucket, Key, VersionId):
        self._wait()
        body = next(item["body"] for item in self.objects[Key] if item["version"] == VersionId)
        return {"VersionId": VersionId, "Body": SimpleNamespace(read=lambda _n: body, close=lambda: None)}


def _missing() -> Exception:
    error = Exception("Not Found")
    error.response = {"Error": {"Code": "404"}}
    return error


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()

    @contextmanager
    def client():
        yield fake

    monkeypatch.setattr(evidence, "object_client", client)
    monkeypatch.setattr(snapshot_storage, "probe_immutability", lambda bucket, c: SimpleNamespace(passed=True))
    monkeypatch.setenv("TKOS_OBJECT_STORE_BUCKET", "evidence-test-bucket")
    return fake


def test_objects_are_keyed_by_content_and_a_retry_reuses_the_stored_version(store):
    scope, domain = str(uuid4()), str(uuid4())
    first = evidence.store_bytes(scope, domain, "报告", b"same bytes", "text/plain")
    again = evidence.store_bytes(scope, domain, "报告（重试）", b"same bytes", "text/plain")
    other = evidence.store_bytes(scope, domain, "另一份", b"other bytes", "text/plain")
    digest = hashlib.sha256(b"same bytes").hexdigest()
    assert first["key"] == f"gov/{scope}/{domain}/sha256/{digest}"
    assert (again["key"], again["version_id"]) == (first["key"], first["version_id"])
    assert again["title"] == "报告（重试）" and other["key"] != first["key"]
    assert store.puts == 2


# -- HTTP 路由，真实库 -------------------------------------------------------------

@pytest.fixture
def seeded():
    db._close_pool()
    label = uuid4().hex[:12]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        from memory_service_runtime.governed import bootstrap
        fixture = bootstrap.seed_scope(conn, f"runtime-acceptance-evidence-{label}",
                                       f"runtime-acceptance-evidence-company-{label}")
    yield fixture
    db._close_pool()


@pytest.fixture
def api(seeded, store):
    from memory_service_app.main import app
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {seeded['actors']['ceo']['token']}"
    return client


def _upload(api, seeded, content: bytes, key: str | None = None):
    body = {"domain_id": seeded["domain_id"], "title": "Synthetic evidence",
            "content_base64": base64.b64encode(content).decode(), "media_type": "text/plain"}
    if key:
        body["idempotency_key"] = key
    return api.post("/v1/evidence-assets", json=body)


def _fence_is_free(seeded) -> float:
    started = time.monotonic()
    with db.transaction(seeded["actors"]["domain_dri"]["token"]):
        pass
    return time.monotonic() - started


@pytest.mark.db
@pytest.mark.owner
def test_the_scope_fence_is_not_held_while_bytes_are_stored(api, seeded, store, monkeypatch):
    monkeypatch.setenv("GOVERNED_LOCK_TIMEOUT_MS", "2000")
    store.gate = threading.Event()
    box = {}
    uploader = threading.Thread(target=lambda: box.setdefault("response", _upload(api, seeded, b"slow bytes")))
    uploader.start()
    assert store.entered.wait(10)
    try:
        assert _fence_is_free(seeded) < 1
    finally:
        store.gate.set()
        uploader.join(10)
    assert box["response"].status_code == 200


@pytest.mark.db
@pytest.mark.owner
def test_the_scope_fence_is_not_held_while_bytes_are_downloaded(api, seeded, store, monkeypatch):
    monkeypatch.setenv("GOVERNED_LOCK_TIMEOUT_MS", "2000")
    uploaded = _upload(api, seeded, b"downloaded bytes").json()
    store.gate, store.entered = threading.Event(), threading.Event()
    box = {}
    path = f"/v1/evidence-assets/{uploaded['object_id']}/revisions/{uploaded['revision_id']}"
    reader = threading.Thread(target=lambda: box.setdefault("response", api.get(path)))
    reader.start()
    assert store.entered.wait(10)
    try:
        assert _fence_is_free(seeded) < 1
    finally:
        store.gate.set()
        reader.join(10)
    assert box["response"].status_code == 200 and box["response"].content == b"downloaded bytes"


@pytest.mark.db
@pytest.mark.owner
def test_an_upload_replayed_with_its_idempotency_key_returns_the_original_without_storing(api, seeded, store):
    key = f"evidence-replay-{uuid4()}"
    first = _upload(api, seeded, b"replayed bytes", key)
    again = _upload(api, seeded, b"replayed bytes", key)
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert store.puts == 1
    conflict = _upload(api, seeded, b"different bytes", key)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert store.puts == 1


@pytest.mark.db
@pytest.mark.owner
def test_the_orphan_report_lists_stored_versions_that_no_revision_references(api, seeded, store, monkeypatch):
    from memory_service_runtime.governed import control
    monkeypatch.setenv("MIGRATION_DATABASE_URL", os.environ["DATABASE_URL"])
    uploaded = _upload(api, seeded, b"referenced bytes").json()
    orphan = evidence.store_bytes(seeded["scope_id"], seeded["domain_id"], "second stage rejected",
                                  b"orphaned bytes", "text/plain")
    with control._connect() as conn:
        report = control.evidence_orphans(conn, SimpleNamespace(scope_id=seeded["scope_id"], actor="test"))
    assert (report["stored_versions"], report["referenced_versions"]) == (2, 1)
    assert [(item["key"], item["version_id"]) for item in report["orphans"]] == [(orphan["key"], orphan["version_id"])]
    assert report["missing"] == [] and uploaded["version_id"] != orphan["version_id"]
