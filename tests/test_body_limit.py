"""请求体上限：超出的请求在读入应用之前就以 413 拒绝，证据上传有更高的上限。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from memory_service_app.limits import BodyLimitMiddleware, EVIDENCE_LIMIT


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware)

    @app.post("/v1/actions")
    async def actions(request: Request):
        return {"bytes": len(await request.body())}

    @app.post("/v1/evidence-assets")
    async def evidence(request: Request):
        return {"bytes": len(await request.body())}

    return app


def test_a_body_over_the_default_limit_is_rejected_with_413(monkeypatch):
    monkeypatch.setenv("GOVERNED_MAX_BODY_BYTES", "1000")
    client = TestClient(_app())
    assert client.post("/v1/actions", content=b"x" * 1000).json() == {"bytes": 1000}
    rejected = client.post("/v1/actions", content=b"x" * 1001)
    assert rejected.status_code == 413
    assert rejected.json() == {"error": {"code": "REQUEST_TOO_LARGE", "message": "The request body is too large."}}


def test_a_streamed_body_without_length_is_counted_as_it_arrives(monkeypatch):
    monkeypatch.setenv("GOVERNED_MAX_BODY_BYTES", "1000")
    client = TestClient(_app())
    chunks = (b"x" * 400 for _ in range(3))
    assert client.post("/v1/actions", content=chunks).status_code == 413


def test_evidence_uploads_have_room_for_a_full_two_mebibyte_file():
    client = TestClient(_app())
    near_limit = EVIDENCE_LIMIT - 1024
    assert client.post("/v1/evidence-assets", content=b"x" * near_limit).json() == {"bytes": near_limit}
    assert client.post("/v1/actions", content=b"x" * near_limit).status_code == 413
    assert client.post("/v1/evidence-assets", content=b"x" * (EVIDENCE_LIMIT + 1)).status_code == 413
