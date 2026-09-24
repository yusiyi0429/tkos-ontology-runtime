"""请求 ID 与结构化运行记录：每个请求带回 X-Request-ID，并记一行 http_request。"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from memory_service_runtime import observability


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(observability.RequestIdMiddleware)

    @app.get("/v1/things/{thing_id}")
    def thing(thing_id: str):
        return {"seen": observability.REQUEST_ID.get()}

    return app


def _records(caplog) -> list[dict]:
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == "tkos.runtime"]


def test_every_response_carries_a_request_id_and_one_log_line(caplog):
    caplog.set_level(logging.INFO, logger="tkos.runtime")
    response = TestClient(_app()).get("/v1/things/abc")
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32 and response.json() == {"seen": request_id}
    [line] = _records(caplog)
    assert line["event"] == "http_request" and line["request_id"] == request_id
    assert (line["method"], line["path"], line["status"]) == ("GET", "/v1/things/{thing_id}", 200)
    assert isinstance(line["duration_ms"], float)


def test_a_well_formed_incoming_request_id_is_kept_and_anything_else_replaced(caplog):
    caplog.set_level(logging.INFO, logger="tkos.runtime")
    client = TestClient(_app())
    kept = client.get("/v1/things/a", headers={"X-Request-ID": "clark-7f3a:trace.01"})
    assert kept.headers["x-request-id"] == "clark-7f3a:trace.01"
    for bad in ("has space", "x" * 129, "a/b", "<script>"):
        replaced = client.get("/v1/things/a", headers={"X-Request-ID": bad})
        assert replaced.headers["x-request-id"] != bad and len(replaced.headers["x-request-id"]) == 32


def test_the_log_line_holds_identifiers_and_timing_only(caplog):
    caplog.set_level(logging.INFO, logger="tkos.runtime")
    TestClient(_app()).get("/v1/things/secret-looking?token=abc")
    [line] = _records(caplog)
    assert set(line) == {"event", "request_id", "method", "path", "status", "duration_ms"}
    assert "token" not in json.dumps(line)
