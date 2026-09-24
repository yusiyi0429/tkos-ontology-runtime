"""payload_hash、请求摘要与 workspace 摘要统一按 tkos-json-v1（canon.py）计算。

已落库的哈希是按原先各处手写的 json.dumps 算的；换成 canon 之后，合法输入
必须逐字节得出同一个值，非法输入（孤立 surrogate、NaN）按 INVALID_REQUEST 拒绝，
而不是在编码时抛出未处理的异常。
"""
from __future__ import annotations

import datetime
import uuid

import pytest

from memory_service_runtime.governed import service, workspace_service, workspace_v02_service
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.models import ActionRequest

# 用切换前的实现算出的值；中文、BMP 以外字符、浮点、UUID 与时间都覆盖到。
REVISION_PAYLOAD = {
    "title": "中文标题 𠀀",
    "terms": {"target": 80, "ratio": 0.5, "tags": ["甲", "b"], "none": None},
    "upstream_refs": [{"object_id": uuid.UUID("11111111-2222-3333-4444-555555555555"), "version": 2}],
    "at": datetime.datetime(2026, 9, 24, 12, 0, tzinfo=datetime.timezone.utc),
}
REVISION_HASH = "89029b579e862b8746eacdb48fe311bf8b839add3260884fdc862e916220062a"
WORKSPACE_EVENT = {"segments": [{"text": "会议纪要 𠀀", "at": "2026-09-24T12:00:00Z"}], "n": 3, "f": 0.25}
WORKSPACE_HASH = "0af2db5c89c8bffe67eab5de256c612f84934f7e459e50384375b517e28b1b34"


def test_stored_revision_and_request_hashes_do_not_change():
    assert service._hash(REVISION_PAYLOAD) == REVISION_HASH


@pytest.mark.parametrize("digest", [workspace_service.digest, workspace_v02_service.digest])
def test_stored_workspace_hashes_do_not_change(digest):
    assert digest(WORKSPACE_EVENT) == WORKSPACE_HASH


def test_a_lone_surrogate_in_free_json_terms_is_an_invalid_request_not_a_server_error():
    # 严格字符串字段在模型校验时就拒绝孤立 surrogate；自由 JSON（这里是旧协议的 terms）会放行，
    # 由请求摘要按 tkos-json-v1 拒绝。
    request = ActionRequest.model_validate({
        "action_type": "create_object",
        "target": None, "expected_versions": [], "idempotency_key": "hashing-lone-surrogate",
        "reason": "规范化边界",
        "params": {"object_type": "CompanyOutcome", "domain_id": str(uuid.uuid4()),
                   "payload": {"title": "目标", "terms": {"note": "坏 \ud800"}}},
    })
    with pytest.raises(GovernedError) as caught:
        service.execute_action(None, None, request)
    assert (caught.value.code, caught.value.status) == ("INVALID_REQUEST", 422)


@pytest.mark.parametrize("digest", [workspace_service.digest, workspace_v02_service.digest])
@pytest.mark.parametrize("value", [{"text": "坏 \ud800"}, {"n": float("nan")}])
def test_workspace_digests_reject_values_outside_tkos_json_v1(digest, value):
    with pytest.raises(GovernedError) as caught:
        digest(value)
    assert caught.value.code == "INVALID_REQUEST"
