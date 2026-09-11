"""Offline tests for a3_customer_events.evaluate_packets.

Uses self-contained synthetic packet bytes built in-file (no S3, no DB, no
clock reads). These tests assert only the function's input validation and
counting semantics; they do NOT pretend to verify packet provenance, caller
authority, or any server-side state — the caller supplies verified raw bytes
and the assessment clock.

Scenarios per engineering doc §5: P01 (two of three customers have both event
types -> target 3 not met), P02 (three fresh customers -> target 3 met), plus
duplication, conflicting event_id reuse, wrong period/target, future and
out-of-period events, non-admissible event types, and input immutability.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

from memory_service_runtime.governed import a3_customer_events as ce
from memory_service_runtime.governed.errors import GovernedError


def u(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def h(n: int) -> str:
    return f"{n:064x}"


TARGET_REF = {"object_id": u(500), "revision_id": u(501), "payload_hash": h(502)}
# period_id 为规范 UUID（对齐 A2 Round period_id）。
PERIOD_ID = u(600)
PERIOD_WINDOW = {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}
ASSESSED_AT = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

ONBOARD = "onboarding_completion_event"
TRANSACT = "first_valid_business_transaction_event"


def ev(n: int, client: str, event_type: str, at: str) -> dict:
    return {"event_id": u(n), "client_id": client, "event_type": event_type,
            "occurred_at": at}


def packet(events: list[dict], *, target: dict | None = None,
           period_id: str = PERIOD_ID) -> bytes:
    return json.dumps({
        "schema_version": "tkos.synthetic.customer-events/0.1",
        "record_origin": "synthetic",
        "company_reference_ref": target if target is not None else TARGET_REF,
        "period_id": period_id,
        "events": events,
    }).encode("utf-8")


def spec(client_ids: list[str], target_count: int = 3) -> dict:
    return {"metric": "activated_customers", "target_count": target_count,
            "client_ids": client_ids}


def evaluate(packets=None, **overrides):
    kwargs = {
        "packets": p01_packets() if packets is None else packets,
        "target_ref": TARGET_REF,
        "period_id": PERIOD_ID,
        "period_window": PERIOD_WINDOW,
        "outcome_spec": spec(["alice", "bob", "carol"]),
        "assessed_at": ASSESSED_AT,
    }
    kwargs.update(overrides)
    return ce.evaluate_packets(**kwargs)


def expect_invalid(**kwargs) -> GovernedError:
    with pytest.raises(GovernedError) as excinfo:
        evaluate(**kwargs)
    assert excinfo.value.code == "INVALID_REQUEST"
    return excinfo.value


# ------------------------------------------------------------------ P01 / P02

def p01_packets() -> list[bytes]:
    # alice 与 bob 两类事件齐全；carol 只有 onboarding —— 目标 3 未达成。
    return [
        packet([
            ev(1, "alice", ONBOARD, "2026-09-05T10:00:00Z"),
            ev(2, "alice", TRANSACT, "2026-09-06T10:00:00Z"),
            ev(3, "bob", ONBOARD, "2026-09-07T10:00:00Z"),
        ]),
        packet([
            ev(4, "bob", TRANSACT, "2026-09-08T10:00:00Z"),
            ev(5, "carol", ONBOARD, "2026-09-09T10:00:00Z"),
        ]),
    ]


def test_p01_two_of_three_qualified():
    result = evaluate(p01_packets())
    assert result == {
        "qualified_customer_count": 2,
        "target_count": 3,
        "qualified_customer_ids": ["alice", "bob"],
        "period_id": PERIOD_ID,
    }
    assert result["qualified_customer_count"] < result["target_count"]


def test_p02_three_fresh_customers_meet_target():
    packets = [
        packet([
            ev(11, "dora", ONBOARD, "2026-09-05T10:00:00Z"),
            ev(12, "dora", TRANSACT, "2026-09-06T10:00:00Z"),
            ev(13, "erin", ONBOARD, "2026-09-07T10:00:00Z"),
            ev(14, "erin", TRANSACT, "2026-09-08T10:00:00Z"),
            ev(15, "fred", ONBOARD, "2026-09-09T10:00:00Z"),
            ev(16, "fred", TRANSACT, "2026-09-10T10:00:00Z"),
        ]),
    ]
    result = evaluate(packets, outcome_spec=spec(["dora", "erin", "fred"]))
    assert result["qualified_customer_count"] == 3
    assert result["target_count"] == 3
    assert result["qualified_customer_ids"] == ["dora", "erin", "fred"]


# ------------------------------------------------------------- dedup/conflict

def test_identical_event_repeated_across_packets_counts_once():
    packets = p01_packets() + [
        packet([ev(2, "alice", TRANSACT, "2026-09-06T10:00:00Z")]),  # 完全重复
    ]
    result = evaluate(packets)
    assert result["qualified_customer_ids"] == ["alice", "bob"]


def test_reused_event_id_with_conflicting_content_fails():
    packets = p01_packets() + [
        packet([ev(2, "alice", TRANSACT, "2026-09-06T11:00:00Z")]),  # 时间不同
    ]
    expect_invalid(packets=packets)
    packets = p01_packets() + [
        packet([ev(2, "carol", TRANSACT, "2026-09-06T10:00:00Z")]),  # 客户不同
    ]
    expect_invalid(packets=packets)


def test_distinct_same_type_events_do_not_inflate_count():
    packets = [
        packet([
            ev(21, "alice", ONBOARD, "2026-09-05T10:00:00Z"),
            ev(22, "alice", ONBOARD, "2026-09-06T10:00:00Z"),  # 同客户同类第二条
            ev(23, "alice", TRANSACT, "2026-09-07T10:00:00Z"),
        ]),
    ]
    result = evaluate(packets, outcome_spec=spec(["alice"], target_count=1))
    assert result["qualified_customer_count"] == 1
    assert result["qualified_customer_ids"] == ["alice"]


def test_missing_second_event_type_not_counted():
    result = evaluate([packet([ev(31, "alice", ONBOARD, "2026-09-05T10:00:00Z")])],
                      outcome_spec=spec(["alice"], target_count=1))
    assert result["qualified_customer_count"] == 0
    assert result["qualified_customer_ids"] == []


# ------------------------------------------------------- target/period/time

def test_wrong_target_rejected():
    for bad in (
        {**TARGET_REF, "payload_hash": h(999)},      # hash 不符
        {**TARGET_REF, "object_id": u(999)},          # 对象不符
        {**TARGET_REF, "revision_id": u(999)},        # 版本不符
    ):
        expect_invalid(packets=[packet([ev(41, "alice", ONBOARD,
                                          "2026-09-05T10:00:00Z")], target=bad)])


def test_wrong_period_rejected():
    # 包 period_id 与评估期间不符（不同 UUID）。
    expect_invalid(packets=[packet([ev(42, "alice", ONBOARD, "2026-09-05T10:00:00Z")],
                                   period_id=u(601))])


def test_textual_or_noncanonical_period_id_rejected():
    # 文本型 period id 即使包与入参一致也拒绝（wire 形状对齐 A2 规范 UUID）。
    textual = "period-2026-09"
    expect_invalid(
        packets=[packet([ev(42, "alice", ONBOARD, "2026-09-05T10:00:00Z")],
                        period_id=textual)],
        period_id=textual,
    )
    # 同一 UUID 的非规范（大写）写法同样拒绝，不做大小写归一。
    upper = "abcdef00-0000-4000-8000-000000000600".upper()
    expect_invalid(
        packets=[packet([ev(42, "alice", ONBOARD, "2026-09-05T10:00:00Z")],
                        period_id="abcdef00-0000-4000-8000-000000000600")],
        period_id=upper,
    )


def test_out_of_period_events_rejected():
    # 早于 start
    expect_invalid(packets=[packet([ev(43, "alice", ONBOARD, "2026-08-31T23:59:59Z")])])
    # 等于 end（前闭后开，end 不含）
    expect_invalid(packets=[packet([ev(44, "alice", ONBOARD, "2026-10-01T00:00:00Z")])])


def test_period_start_is_inclusive():
    result = evaluate(
        [packet([
            ev(45, "alice", ONBOARD, "2026-09-01T00:00:00Z"),
            ev(46, "alice", TRANSACT, "2026-09-30T23:59:59Z"),
        ])],
        outcome_spec=spec(["alice"], target_count=1),
    )
    assert result["qualified_customer_count"] == 1


def test_future_events_rejected():
    expect_invalid(packets=[packet([ev(47, "alice", ONBOARD, "2026-10-03T00:00:00Z")])])


def test_event_at_assessed_at_allowed_but_in_period_required():
    # occurred_at == assessed_at 不超限，但须在期间内才合法。
    as_of = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = evaluate(
        [packet([
            ev(48, "alice", ONBOARD, "2026-09-10T12:00:00Z"),  # == assessed_at
            ev(49, "alice", TRANSACT, "2026-09-09T10:00:00Z"),
        ])],
        outcome_spec=spec(["alice"], target_count=1),
        assessed_at=as_of,
    )
    assert result["qualified_customer_count"] == 1


def test_naive_assessed_at_rejected():
    expect_invalid(assessed_at=datetime(2026, 10, 2, 12, 0, 0))


def test_unlisted_customer_rejected():
    expect_invalid(packets=[packet([ev(50, "mallory", ONBOARD, "2026-09-05T10:00:00Z")])])


# ------------------------------------------------------------ schema/shape

def test_login_only_event_type_rejected():
    expect_invalid(packets=[packet([ev(51, "alice", "login_event",
                                        "2026-09-05T10:00:00Z")])])


def test_wrong_schema_version_or_origin_rejected():
    bad = json.loads(packet([]))
    bad["schema_version"] = "tkos.synthetic.customer-events/0.2"
    expect_invalid(packets=[json.dumps(bad).encode()])
    bad = json.loads(packet([]))
    bad["record_origin"] = "production"
    expect_invalid(packets=[json.dumps(bad).encode()])


def test_unknown_fields_rejected_at_packet_and_event_level():
    bad = json.loads(packet([]))
    bad["extra"] = True
    expect_invalid(packets=[json.dumps(bad).encode()])
    bad = json.loads(packet([ev(52, "alice", ONBOARD, "2026-09-05T10:00:00Z")]))
    bad["events"][0]["note"] = "nope"
    expect_invalid(packets=[json.dumps(bad).encode()])


def test_malformed_bytes_rejected():
    expect_invalid(packets=[b"\xff\xfe not utf8"])                     # 非 UTF-8
    expect_invalid(packets=[b'{"a": 1, "a": 2}'])                       # 重复键
    expect_invalid(packets=[b'{"schema_version": 1e999}'])              # Infinity
    expect_invalid(packets=[b'{"schema_version": NaN}'])                # NaN
    expect_invalid(packets=[b'{"schema_version":'])                     # 语法错误


# 哨兵值：仅用于断言其不出现在公开错误中，不是真实秘密。
SENTINEL = "OPAQUE-SENTINEL-7f3a9c"


def assert_no_disclosure(exc: GovernedError) -> None:
    assert exc.code == "INVALID_REQUEST"
    assert SENTINEL not in str(exc)
    assert SENTINEL not in exc.message


def test_unknown_field_sentinel_not_disclosed():
    bad = json.loads(packet([ev(52, "alice", ONBOARD, "2026-09-05T10:00:00Z")]))
    bad["mystery"] = SENTINEL
    with pytest.raises(GovernedError) as excinfo:
        evaluate(packets=[json.dumps(bad).encode()])
    assert_no_disclosure(excinfo.value)
    # 事件级未知字段同样不外泄。
    bad = json.loads(packet([ev(52, "alice", ONBOARD, "2026-09-05T10:00:00Z")]))
    bad["events"][0]["mystery"] = SENTINEL
    with pytest.raises(GovernedError) as excinfo:
        evaluate(packets=[json.dumps(bad).encode()])
    assert_no_disclosure(excinfo.value)


def test_malformed_bytes_sentinel_not_disclosed():
    # 重复键名校验细节不得随错误外泄。
    payload = b'{"%s": 1, "%s": 2}' % (SENTINEL.encode(), SENTINEL.encode())
    with pytest.raises(GovernedError) as excinfo:
        evaluate(packets=[payload])
    assert_no_disclosure(excinfo.value)


def test_outcome_spec_sentinel_not_disclosed():
    bad_spec = {"metric": "activated_customers", "target_count": 1,
                "client_ids": ["alice"], "mystery": SENTINEL}
    with pytest.raises(GovernedError) as excinfo:
        evaluate(packets=p01_packets(), outcome_spec=bad_spec)
    assert_no_disclosure(excinfo.value)


def test_noncanonical_event_id_rejected():
    bad = json.loads(packet([ev(53, "alice", ONBOARD, "2026-09-05T10:00:00Z")]))
    bad["events"][0]["event_id"] = "00000000-0000-4000-8000-0000000000AA"  # 大写
    expect_invalid(packets=[json.dumps(bad).encode()])


# ------------------------------------------------------------- outcome spec

def test_outcome_spec_strictness():
    base_packets = p01_packets()
    for bad_spec in (
        {"metric": "activated_customers", "target_count": True,
         "client_ids": ["alice"]},                                       # bool
        {"metric": "activated_customers", "target_count": 0,
         "client_ids": ["alice"]},                                       # 非正
        {"metric": "activated_customers", "target_count": 2.0,
         "client_ids": ["alice"]},                                       # 浮点
        {"metric": "logins", "target_count": 1, "client_ids": ["alice"]},  # 未知指标
        {"metric": "activated_customers", "target_count": 1,
         "client_ids": ["alice", "alice"]},                              # 重复客户
        {"metric": "activated_customers", "target_count": 1,
         "client_ids": ["  "]},                                          # 空白客户
        {"metric": "activated_customers", "target_count": 1,
         "client_ids": ["alice"], "extra": 1},                           # 未知字段
    ):
        expect_invalid(packets=base_packets, outcome_spec=bad_spec)


def test_bad_target_ref_and_window_rejected():
    expect_invalid(target_ref={"object_id": u(500), "revision_id": u(501)})  # 缺 hash
    expect_invalid(period_window={"start": PERIOD_WINDOW["end"],
                                  "end": PERIOD_WINDOW["start"]})            # 倒序
    expect_invalid(period_window={"start": "2026-09-01T00:00:00Z"})          # 缺 end
    expect_invalid(period_id="  ")                                           # 空白
    expect_invalid(packets=b"not-a-list")                                    # 非 list
    expect_invalid(packets=[{"not": "bytes"}])                               # 非 bytes


# ------------------------------------------------------------- immutability

def test_inputs_are_not_mutated():
    packets = p01_packets()
    outcome = spec(["alice", "bob", "carol"])
    window = dict(PERIOD_WINDOW)
    target = dict(TARGET_REF)
    snapshot = copy.deepcopy([packets, outcome, window, target])
    evaluate(packets, outcome_spec=outcome, period_window=window, target_ref=target)
    assert [packets, outcome, window, target] == snapshot
