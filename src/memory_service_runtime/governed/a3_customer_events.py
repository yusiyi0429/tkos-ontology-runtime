"""A3-11 合成客户激活事件的离线评估（契约包 A §5；工程文档 §5 待预审项之一）。

纯函数：不读 S3/DB/认证/时钟。调用方独立提供已核验的原始字节（包来源与
真实性由调用方负责）与真实评估时钟 assessed_at；assessment_result 的判定
与交付引用也由调用方处理，本函数只返回合格客户计数，不改写任何交付或
MF 状态。

固定包结构：schema_version=tkos.synthetic.customer-events/0.1、
record_origin=synthetic、company_reference_ref 精确三元组、period_id
（规范 UUID，对齐 A2 Round period_id）、events[]（event_id 规范 UUID、
client_id、event_type、aware ISO occurred_at）。仅两类事件合法：
onboarding_completion_event 与 first_valid_business_transaction_event。
合格客户 = 名单内同一 client 在 [start, end) 内两类事件各至少一条；时间
不得超过 assessed_at。

所有畸形/不支持/冲突输入一律抛 GovernedError("INVALID_REQUEST")。公开错误
消息为固定文本：不内插校验细节、包字段或事件标识，避免上传内容经
HTTP 错误/日志外泄。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import TypeAdapter, ValidationError, model_validator

from . import canon
from .a2_models import CanonicalUUID, IsoDateTime, NEStr, PositiveInt, StrictModel
from .a3_models import ExactRef
from .errors import GovernedError

PACKET_SCHEMA_VERSION = "tkos.synthetic.customer-events/0.1"
RECORD_ORIGIN_SYNTHETIC = "synthetic"
METRIC_ACTIVATED_CUSTOMERS = "activated_customers"
EVENT_ONBOARDING = "onboarding_completion_event"
EVENT_FIRST_TRANSACTION = "first_valid_business_transaction_event"
ADMISSIBLE_EVENT_TYPES = frozenset({EVENT_ONBOARDING, EVENT_FIRST_TRANSACTION})

_CANONICAL_UUID = TypeAdapter(CanonicalUUID)


def _invalid(message: str) -> GovernedError:
    return GovernedError("INVALID_REQUEST", message)


class _Event(StrictModel):
    event_id: CanonicalUUID
    client_id: NEStr
    event_type: Literal["onboarding_completion_event", "first_valid_business_transaction_event"]
    occurred_at: IsoDateTime


class _Packet(StrictModel):
    schema_version: Literal["tkos.synthetic.customer-events/0.1"]
    record_origin: Literal["synthetic"]
    company_reference_ref: ExactRef
    period_id: CanonicalUUID
    events: list[_Event]


class _PeriodWindow(StrictModel):
    start: IsoDateTime
    end: IsoDateTime

    @model_validator(mode="after")
    def ordered(self) -> "_PeriodWindow":
        if datetime.fromisoformat(self.end) <= datetime.fromisoformat(self.start):
            raise ValueError("period window end must be later than start")
        return self


class _OutcomeSpec(StrictModel):
    metric: Literal["activated_customers"]
    target_count: PositiveInt  # 严格正整数；bool/浮点/字符串一律拒绝
    client_ids: list[NEStr]

    @model_validator(mode="after")
    def distinct(self) -> "_OutcomeSpec":
        if len(set(self.client_ids)) != len(self.client_ids):
            raise ValueError("duplicate client_ids in outcome_spec")
        return self


def evaluate_packets(
    *,
    packets: list[bytes],
    target_ref: dict,
    period_id: str,
    period_window: dict,
    outcome_spec: dict,
    assessed_at: datetime,
) -> dict:
    """评估合成客户事件包，返回合格客户计数。

    返回 {"qualified_customer_count", "target_count", "qualified_customer_ids",
    "period_id"}。不修改任何入参。
    """
    if not (
        isinstance(assessed_at, datetime)
        and assessed_at.tzinfo is not None
        and assessed_at.utcoffset() is not None
    ):
        raise _invalid("assessed_at must be a timezone-aware datetime")
    if not isinstance(period_id, str):
        raise _invalid("period_id must be a canonical UUID string")
    try:
        period_id = _CANONICAL_UUID.validate_python(period_id)
    except ValidationError as exc:
        raise _invalid("period_id must be a canonical UUID string") from exc
    if not isinstance(packets, list) or any(
        not isinstance(p, (bytes, bytearray)) for p in packets
    ):
        raise _invalid("packets must be a list of bytes")

    try:
        target = ExactRef.model_validate(target_ref)
        window = _PeriodWindow.model_validate(period_window)
        spec = _OutcomeSpec.model_validate(outcome_spec)
    except ValidationError as exc:
        raise _invalid(
            "malformed target_ref, period_window or outcome_spec"
        ) from exc

    start = datetime.fromisoformat(window.start)
    end = datetime.fromisoformat(window.end)
    listed = set(spec.client_ids)

    target_key = (target.object_id, target.revision_id, target.payload_hash)
    # event_id -> (client_id, event_type, occurred_at)；同一 event_id 内容冲突即拒绝。
    seen: dict[str, tuple[str, str, datetime]] = {}
    types_by_client: dict[str, set[str]] = {}

    for raw in packets:
        try:
            data = canon.load_json_bytes(bytes(raw))
        except canon.CanonError as exc:
            raise _invalid("malformed packet bytes") from exc
        try:
            packet = _Packet.model_validate(data)
        except ValidationError as exc:
            raise _invalid(
                "packet does not match the customer-events schema"
            ) from exc

        ref = packet.company_reference_ref
        if (ref.object_id, ref.revision_id, ref.payload_hash) != target_key:
            raise _invalid("packet company_reference_ref does not match target_ref")
        if packet.period_id != period_id:
            raise _invalid("packet period_id does not match the assessment period")

        for event in packet.events:
            occurred = datetime.fromisoformat(event.occurred_at)
            if occurred > assessed_at:
                raise _invalid("an event occurs after assessed_at")
            if not (start <= occurred < end):
                raise _invalid("an event is outside the period window")
            if event.client_id not in listed:
                raise _invalid("an event references an unlisted customer")

            content = (event.client_id, event.event_type, occurred)
            previous = seen.get(event.event_id)
            if previous is not None:
                if previous != content:
                    raise _invalid("an event_id is reused with conflicting content")
                continue  # 完全相同的重复事件只计一次
            seen[event.event_id] = content
            types_by_client.setdefault(event.client_id, set()).add(event.event_type)

    qualified = sorted(
        client_id
        for client_id, types in types_by_client.items()
        if types == ADMISSIBLE_EVENT_TYPES
    )
    return {
        "qualified_customer_count": len(qualified),
        "target_count": spec.target_count,
        "qualified_customer_ids": qualified,
        "period_id": period_id,
    }


__all__ = [
    "PACKET_SCHEMA_VERSION", "RECORD_ORIGIN_SYNTHETIC", "METRIC_ACTIVATED_CUSTOMERS",
    "EVENT_ONBOARDING", "EVENT_FIRST_TRANSACTION", "ADMISSIBLE_EVENT_TYPES",
    "evaluate_packets",
]
