"""类型目录与 cursor 编解码的纯逻辑测试（无数据库）。"""
from __future__ import annotations

import base64
import json

import pytest

from memory_service_runtime.governed import workbench
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.models import PAYLOAD_MODELS, A2_OBJECT_TYPE_NAMES

from fakes import CTX, uid


def encode_raw(body) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).decode()


def test_catalog_preserves_legacy_and_adds_a2_types_with_explicit_creation_modes():
    result = workbench.object_types(None, CTX)
    assert result["schema_version"] == workbench.SCHEMA_VERSION
    items = {item["object_type"]: item for item in result["items"]}
    assert set(items) == {*PAYLOAD_MODELS, "EvidenceAsset", "Deliverable", "ProtocolSentinel", *A2_OBJECT_TYPE_NAMES}
    assert not {"Risk", "Lesson"} & set(items)
    for name in ("FormationRound", "DomainSubmission", "CompanyComposition", "Mission", "DomainCommitment"):
        assert items[name]["creation_mode"] == "a2_action"
        assert name not in PAYLOAD_MODELS
    for name, model in PAYLOAD_MODELS.items():
        assert items[name]["creation_mode"] == "generic_action"
        assert items[name]["payload_schema"] == model.model_json_schema()
    for name in ("EvidenceAsset", "Deliverable"):
        assert items[name]["creation_mode"] == "dedicated_action"
        assert items[name]["payload_schema"] is None
    # A1 registration sentinel: control-plane only, no payload schema, never a
    # generic or dedicated business creation path.
    assert items["ProtocolSentinel"]["creation_mode"] == "control_plane_only"
    assert items["ProtocolSentinel"]["payload_schema"] is None
    assert "ProtocolSentinel" not in PAYLOAD_MODELS
    for item in result["items"]:
        assert item["label"] and item["description"] and item["versioning"]


def test_cursor_roundtrip_preserves_filters_and_key():
    filters = {"domain_id": uid(1), "object_type": None}
    cursor = workbench.encode_cursor("objects", CTX, filters, ["2026-09-01T00:00:00+00:00", uid(2)])
    assert workbench.decode_cursor(cursor, "objects", CTX, filters) == ["2026-09-01T00:00:00+00:00", uid(2)]
    assert workbench.decode_cursor(None, "objects", CTX, filters) is None


def _valid_body(**overrides):
    body = {"v": 1, "ep": "domains", "scope": CTX.scope_id, "pid": CTX.principal_id,
            "f": {}, "k": [uid(3)]}
    body.update(overrides)
    return body


@pytest.mark.parametrize("cursor", [
    "!!!not-base64!!!",
    base64.urlsafe_b64encode(b"not json").decode(),
    base64.urlsafe_b64encode(b"[1, 2]").decode(),
    base64.urlsafe_b64encode(b'{"v": 1}').decode(),
    encode_raw(_valid_body(v=2)),
    encode_raw(_valid_body(v=True)),   # JSON true 不是 v1
    encode_raw(_valid_body(v=1.0)),    # JSON 1.0 不是 integer 1
    encode_raw(_valid_body(v="1")),
    encode_raw(_valid_body(ep="objects")),
    encode_raw(_valid_body(scope=uid(9999))),
    encode_raw(_valid_body(pid=uid(9998))),
    encode_raw(_valid_body(f={"domain_id": uid(1)})),
    encode_raw(_valid_body(k="not-a-list")),
    encode_raw(_valid_body(extra="field")),
    encode_raw(_valid_body())[:-4] + "====" + "A" * 5000,
])
def test_malicious_or_foreign_cursors_are_invalid_request(cursor):
    with pytest.raises(GovernedError) as caught:
        workbench.decode_cursor(cursor, "domains", CTX, {})
    assert caught.value.code == "INVALID_REQUEST"
    assert caught.value.status == 422


def test_cursor_from_another_endpoint_is_rejected():
    cursor = workbench.encode_cursor("domains", CTX, {}, [uid(3)])
    with pytest.raises(GovernedError) as caught:
        workbench.decode_cursor(cursor, "objects", CTX, {"domain_id": uid(1), "object_type": None})
    assert caught.value.code == "INVALID_REQUEST"
