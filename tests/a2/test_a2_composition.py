"""Pure-function unit tests for a2_composition helpers.

These tests exercise only deterministic helpers (capacity pool aggregation,
dependency-id namespace stability, canonical UUID / signed-int validation)
plus the live a2_rounds.source_refs / a2_rounds.sources boundary that
a2_composition actually dispatches to.  DB access is mocked at the
execution/conn boundary only (no fake SQL parser); full HTTP/SQL acceptance
is owned by Codex's independent acceptance suite.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from memory_service_runtime.governed import a2_composition as a2c


# ---- dependency id stability ----

def test_dependency_id_is_stable_for_same_pool_key() -> None:
    k = ("11111111-1111-4111-8111-111111111111",
         "22222222-2222-4222-8222-222222222222",
         "synthetic_onboarding_slot")
    a = a2c._dependency_id(k)
    b = a2c._dependency_id(k)
    assert a == b
    UUID(a)  # parses as canonical UUID


def test_dependency_id_differs_per_pool_key() -> None:
    k1 = ("11111111-1111-4111-8111-111111111111",
          "22222222-2222-4222-8222-222222222222", "u")
    k2 = ("11111111-1111-4111-8111-111111111111",
          "33333333-3333-4333-8333-333333333333", "u")
    assert a2c._dependency_id(k1) != a2c._dependency_id(k2)


# ---- signed int helpers ----

def test_ensure_signed_int_rejects_bool() -> None:
    with pytest.raises(Exception):
        a2c._ensure_signed_int(True, field="x")


def test_ensure_signed_int_rejects_str() -> None:
    with pytest.raises(Exception):
        a2c._ensure_signed_int("3", field="x")


def test_ensure_signed_int_rejects_negative() -> None:
    with pytest.raises(Exception):
        a2c._ensure_signed_int(-1, field="x")


def test_ensure_signed_int_accepts_zero_and_positive() -> None:
    assert a2c._ensure_signed_int(0, field="x") == 0
    assert a2c._ensure_signed_int(42, field="x") == 42


# ---- canonical uuid ----

def test_canonical_uuid_normalises_form() -> None:
    raw = "11111111111141118111111111111111"  # non-canonical no-hyphen
    out = a2c._canonical_uuid(raw)
    assert out == "11111111-1111-4111-8111-111111111111"


def test_canonical_uuid_rejects_garbage() -> None:
    from memory_service_runtime.governed.errors import GovernedError
    with pytest.raises(GovernedError):
        a2c._canonical_uuid("not-a-uuid")


# ---- _aggregate_capacity（form 当前使用的跨 Submission 全集聚合器）----
#
# 语义锚点（冻结契约）：
#   * 容量总需求 = 完整正式 Submission 集合的 resources[].required 之和，
#     不要求每个域 demand/binding 一一对应；
#   * 只有 binding 没有需求的池（可提供方）也产出 required=0 的 dependency；
#   * 有需求无 binding → COMPOSITION_INPUT_CHANGED；同池多来源 → COMPOSITION_NOT_READY；
#   * required > available-reserved → 记 conflict 不抛错；reserved > available → 拒绝。

from unittest import mock

from memory_service_runtime.governed import a2_rounds
from memory_service_runtime.governed.errors import GovernedError

_RES = "11111111-1111-4111-8111-111111111111"
_PERIOD = "22222222-2222-4222-8222-222222222222"
_SRC_OID = "33333333-3333-4333-8333-333333333333"
_SRC_RID = "44444444-4444-4444-8444-444444444444"
_SRC_REF = {"object_id": _SRC_OID, "revision_id": _SRC_RID,
            "payload_hash": "a" * 64}


def _sub(resources=(), bindings=()):
    return {"payload": {"submission": {"resources": list(resources),
                                       "bindings": list(bindings)}}}


def _resource(required, period=_PERIOD):
    return {"resource_id": _RES, "period_id": period, "unit": "slot",
            "required": required}


def _binding(source_ref=_SRC_REF, period=_PERIOD):
    return {"relation_type": "resource_capacity", "resource_id": _RES,
            "period_id": period, "unit": "slot", "source_ref": source_ref}


def _exec_for_capacity(available=10, reserved=2, object_type="CapacityObservation",
                       payload=None):
    e = mock.MagicMock()
    e.head = mock.MagicMock(return_value={"object_id": _SRC_OID,
                                          "object_type": object_type})
    source_payload = payload if payload is not None else {
        "resource_id": _RES, "period_id": _PERIOD, "unit": "slot",
        "available": available, "reserved": reserved,
    }
    e.revision = mock.MagicMock(return_value={"object_id": _SRC_OID,
                                              "revision_id": _SRC_RID,
                                              "payload": source_payload})
    return e


def test_aggregate_capacity_sums_full_formal_set() -> None:
    """A 域需求 3 + B 域需求 2，binding 只由 B 提供 → 单池 required=5。
    旧的一一对应语义会错误地要求 A 域也给出 binding。"""
    subs = {
        "domain-a": _sub(resources=[_resource(3)]),
        "domain-b": _sub(resources=[_resource(2)], bindings=[_binding()]),
    }
    e = _exec_for_capacity()
    with mock.patch.object(a2_rounds, "sources") as sources:
        deps, conflicts = a2c._aggregate_capacity(e, subs, _PERIOD,
                                                  ["domain-a", "domain-b"])
    assert conflicts == []
    assert len(deps) == 1
    assert deps[0]["constraint"]["required"] == 5
    assert deps[0]["constraint"]["available"] == 8
    assert deps[0]["source_ref"] == _SRC_REF
    sources.assert_called_once_with(e, [_SRC_REF], ["domain-a", "domain-b"])


def test_aggregate_capacity_binding_only_pool_produces_zero_required() -> None:
    """可提供方：池只有 binding 没有需求 → 产出 required=0 的 dependency。"""
    subs = {"domain-b": _sub(bindings=[_binding()])}
    e = _exec_for_capacity()
    with mock.patch.object(a2_rounds, "sources"):
        deps, conflicts = a2c._aggregate_capacity(e, subs, _PERIOD, ["domain-b"])
    assert conflicts == []
    assert len(deps) == 1
    assert deps[0]["constraint"]["required"] == 0
    assert deps[0]["constraint"]["available"] == 8


def test_aggregate_capacity_demand_without_binding_rejected() -> None:
    subs = {"domain-a": _sub(resources=[_resource(1)])}
    with mock.patch.object(a2_rounds, "sources"):
        with pytest.raises(GovernedError) as ei:
            a2c._aggregate_capacity(_exec_for_capacity(), subs, _PERIOD, [])
    assert ei.value.code == "COMPOSITION_INPUT_CHANGED"


def test_aggregate_capacity_conflicting_sources_rejected() -> None:
    """同池两个不同来源 ref → 硬拒绝（现行 form 路径直接拒绝，
    而不是把冲突记入 hard_conflicts 继续落库）。"""
    other = {"object_id": "55555555-5555-4555-8555-555555555555",
             "revision_id": "66666666-6666-4666-8666-666666666666",
             "payload_hash": "b" * 64}
    subs = {
        "domain-a": _sub(bindings=[_binding()]),
        "domain-b": _sub(bindings=[_binding(source_ref=other)]),
    }
    with mock.patch.object(a2_rounds, "sources"):
        with pytest.raises(GovernedError) as ei:
            a2c._aggregate_capacity(_exec_for_capacity(), subs, _PERIOD, [])
    assert ei.value.code == "COMPOSITION_NOT_READY"


def test_aggregate_capacity_over_demand_is_conflict_not_error() -> None:
    subs = {"domain-a": _sub(resources=[_resource(9)], bindings=[_binding()])}
    e = _exec_for_capacity(available=4, reserved=1)
    with mock.patch.object(a2_rounds, "sources"):
        deps, conflicts = a2c._aggregate_capacity(e, subs, _PERIOD, ["domain-a"])
    assert len(deps) == 1
    assert deps[0]["constraint"]["required"] == 9
    assert deps[0]["constraint"]["available"] == 3
    assert conflicts == ["Current total demand exceeds available capacity."]


def test_aggregate_capacity_reserved_exceeds_available_rejected() -> None:
    subs = {"domain-a": _sub(bindings=[_binding()])}
    e = _exec_for_capacity(available=1, reserved=5)
    with mock.patch.object(a2_rounds, "sources"):
        with pytest.raises(GovernedError) as ei:
            a2c._aggregate_capacity(e, subs, _PERIOD, [])
    assert ei.value.code == "COMPOSITION_NOT_READY"


def test_aggregate_capacity_period_mismatch_rejected() -> None:
    subs = {"domain-a": _sub(
        resources=[_resource(1, period="99999999-9999-4999-8999-999999999999")],
        bindings=[_binding()])}
    with mock.patch.object(a2_rounds, "sources"):
        with pytest.raises(GovernedError) as ei:
            a2c._aggregate_capacity(_exec_for_capacity(), subs, _PERIOD, [])
    assert ei.value.code == "COMPOSITION_INPUT_CHANGED"


def test_aggregate_capacity_non_observation_source_rejected() -> None:
    subs = {"domain-a": _sub(bindings=[_binding()])}
    e = _exec_for_capacity(object_type="CompanyReference")
    with mock.patch.object(a2_rounds, "sources"):
        with pytest.raises(GovernedError) as ei:
            a2c._aggregate_capacity(e, subs, _PERIOD, [])
    assert ei.value.code == "COMPOSITION_INPUT_CHANGED"


def test_aggregate_capacity_dependency_id_stable_across_calls() -> None:
    subs = {"domain-a": _sub(bindings=[_binding()])}
    with mock.patch.object(a2_rounds, "sources"):
        first, _ = a2c._aggregate_capacity(_exec_for_capacity(), subs, _PERIOD, [])
        second, _ = a2c._aggregate_capacity(_exec_for_capacity(), subs, _PERIOD, [])
    assert first[0]["dependency_id"] == second[0]["dependency_id"]


# ---- a2_rounds.source_refs（现行结构化 ref 选择器）----
#
# 语义锚点：source_refs 从 payload 选出 upstream_refs / dependency_refs /
# bindings[].source_ref / missions[*] 递归 refs，不自行去重——重复到达的
# (object_id, revision_id) 由 a2_rounds.sources 的 DFS seen 集合统一去重。


def _u(n: int) -> str:
    """Deterministic canonical UUID for graph node n."""
    return f"{n:08x}-0000-4000-8000-000000000000"


def test_source_refs_selects_all_sections_without_dedup() -> None:
    obj, rev = _u(1), _u(1001)
    dep_obj, dep_rev = _u(2), _u(1002)
    payload = {
        "upstream_refs": [{"object_id": obj, "revision_id": rev}],
        "dependency_refs": [{"object_id": dep_obj, "revision_id": dep_rev}],
        "bindings": [{
            "relation_type": "resource_capacity",
            "source_ref": {"object_id": obj, "revision_id": rev,
                           "payload_hash": "a" * 64},
            "resource_id": _RES, "period_id": _PERIOD, "unit": "slot",
        }],
        "missions": [{"upstream_refs": [{"object_id": obj, "revision_id": rev}]}],
    }
    refs = a2_rounds.source_refs(payload)
    # 顺序：upstream → dependency → binding → mission；重复 ref 原样保留。
    assert [(r["object_id"], r["revision_id"]) for r in refs] == [
        (obj, rev), (dep_obj, dep_rev), (obj, rev), (obj, rev)]


def test_source_refs_recurses_into_missions() -> None:
    inner = {"object_id": _u(5), "revision_id": _u(1005)}
    payload = {"missions": [{"missions": [{"dependency_refs": [inner]}]}]}
    assert a2_rounds.source_refs(payload) == [inner]


def test_source_refs_empty_payload_yields_nothing() -> None:
    assert a2_rounds.source_refs({}) == []


# ---- a2_rounds.sources（现行统一来源校验 walker）----
#
# 语义锚点：sources(e, refs, shared_domains) 无返回值；每个 ref 均为合法
# UUID。Mock 边界：e.add_dependency → head（含 object_type /
# effective_revision_id），e.revision → revision（含 payload_hash /
# payload），payload 必含 shared_with_domain_ids 与 upstream_refs；
# e.conn.execute(...).fetchone() 只返回 {"valid": ...}（DB 时钟有效期），
# 无 fake SQL parser。钻石共享尾节点不是环；真环 / 深度>8 / >64 节点 →
# COMPOSITION_NOT_READY；不可见 → NOT_FOUND 透传；共享域未覆盖 → FORBIDDEN；
# 非来源类型 → INVALID_STATE；effective_revision / payload_hash 不符与
# DB valid=False → COMPOSITION_INPUT_CHANGED。负向用例严格断言 code，
# 且不产生任何写 SQL。

_SHARED_DOMAINS = [_u(100), _u(101)]


def _source_ref(oid, rid, payload_hash=None):
    ref = {"object_id": oid, "revision_id": rid}
    if payload_hash is not None:
        ref["payload_hash"] = payload_hash
    return ref


def _graph_nodes(graph, *, shared=_SHARED_DOMAINS):
    """graph: node int -> [child node ints]；节点 n 的 object_id=_u(n)、
    revision_id=_u(1000+n)。"""
    return {
        _u(n): {
            "revision_id": _u(1000 + n),
            "payload": {
                "shared_with_domain_ids": list(shared),
                "upstream_refs": [_source_ref(_u(c), _u(1000 + c))
                                  for c in children],
            },
        }
        for n, children in graph.items()
    }


def _sources_exec(nodes, *, db_valid=True):
    """Mock 执行边界；不可见 object_id 与现行 A2Execution.add_dependency
    一样以 NOT_FOUND 拒绝。"""
    e = mock.MagicMock()
    e.ctx.scope_id = _u(0)

    def add_dependency(oid):
        node = nodes.get(oid)
        if node is None:
            raise GovernedError("NOT_FOUND")
        return {
            "object_id": oid,
            "object_type": node.get("object_type", "CapacityObservation"),
            "effective_revision_id": node.get("effective_revision_id",
                                              node["revision_id"]),
        }

    def revision(oid, rid):
        node = nodes[oid]
        return {
            "object_id": oid,
            "revision_id": rid,
            "payload_hash": node.get("payload_hash", "a" * 64),
            "payload": node["payload"],
        }

    e.add_dependency = mock.MagicMock(side_effect=add_dependency)
    e.revision = mock.MagicMock(side_effect=revision)
    e.conn.execute.return_value.fetchone.return_value = {"valid": db_valid}
    return e


def _ref_to(n):
    return _source_ref(_u(n), _u(1000 + n))


def _assert_no_write_sql(e) -> None:
    for call in e.conn.execute.call_args_list:
        statement = call.args[0].lstrip().upper()
        assert not statement.startswith(("INSERT", "UPDATE", "DELETE"))


def test_sources_diamond_shared_tail_is_not_a_cycle() -> None:
    """A→B, A→C, B→D, C→D：D 经两条路径重复到达，是 DAG 边不是环。"""
    nodes = _graph_nodes({1: [2, 3], 2: [4], 3: [4], 4: []})
    e = _sources_exec(nodes)
    assert a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS) is None
    visited = {call.args[0] for call in e.add_dependency.call_args_list}
    assert visited == {_u(1), _u(2), _u(3), _u(4)}
    _assert_no_write_sql(e)


def test_sources_shared_tail_counts_toward_longest_path() -> None:
    # The short branch populates the cache first. The second branch is nine
    # levels long and must not inherit the shorter branch's depth allowance.
    nodes = _graph_nodes({1:[5,2],2:[3],3:[4],4:[5],5:[6],6:[7],7:[8],8:[9],9:[]})
    e = _sources_exec(nodes)
    with pytest.raises(GovernedError) as error:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert error.value.code == 'COMPOSITION_NOT_READY'
    _assert_no_write_sql(e)


def test_sources_eight_level_shared_tail_is_allowed() -> None:
    nodes = _graph_nodes({1:[5,2],2:[3],3:[5],5:[6],6:[7],7:[8],8:[9],9:[]})
    e = _sources_exec(nodes)
    a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert {call.args[0] for call in e.add_dependency.call_args_list} == set(nodes)
    _assert_no_write_sql(e)


def test_sources_true_cycle_rejected() -> None:
    nodes = _graph_nodes({1: [2], 2: [1]})
    e = _sources_exec(nodes)
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert ei.value.code == "COMPOSITION_NOT_READY"
    _assert_no_write_sql(e)


def test_sources_depth_limit_rejected() -> None:
    # 链 1→…→9：第 9 个节点在 depth 9 > CLOSURE_MAX_DEPTH(8) 处拒绝。
    graph = {n: [n + 1] for n in range(1, 9)}
    graph[9] = []
    e = _sources_exec(_graph_nodes(graph))
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert ei.value.code == "COMPOSITION_NOT_READY"
    _assert_no_write_sql(e)


def test_sources_node_limit_rejected() -> None:
    # 65 个互不相同的根节点：第 65 个使 seen 超过 CLOSURE_MAX_NODES(64)。
    e = _sources_exec(_graph_nodes({n: [] for n in range(1, 66)}))
    refs = [_ref_to(n) for n in range(1, 66)]
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, refs, _SHARED_DOMAINS)
    assert ei.value.code == "COMPOSITION_NOT_READY"
    _assert_no_write_sql(e)


def test_sources_invisible_ref_propagates_not_found() -> None:
    e = _sources_exec({})  # nothing visible
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(9)], _SHARED_DOMAINS)
    assert ei.value.code == "NOT_FOUND"
    assert ei.value.status == 404
    _assert_no_write_sql(e)


def test_sources_uncovered_shared_domain_forbidden() -> None:
    # 源只对 _u(100) 发布，未覆盖 _u(101)。
    nodes = _graph_nodes({1: []}, shared=[_u(100)])
    e = _sources_exec(nodes)
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert ei.value.code == "FORBIDDEN"
    _assert_no_write_sql(e)


def test_sources_non_source_type_rejected() -> None:
    nodes = _graph_nodes({1: []})
    nodes[_u(1)]["object_type"] = "EvidenceAsset"
    e = _sources_exec(nodes)
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert ei.value.code == "INVALID_STATE"
    _assert_no_write_sql(e)


def test_sources_stale_effective_revision_rejected() -> None:
    nodes = _graph_nodes({1: []})
    nodes[_u(1)]["effective_revision_id"] = _u(2001)  # 不再是请求的 rid
    e = _sources_exec(nodes)
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert ei.value.code == "COMPOSITION_INPUT_CHANGED"
    _assert_no_write_sql(e)


def test_sources_payload_hash_mismatch_rejected() -> None:
    nodes = _graph_nodes({1: []})
    nodes[_u(1)]["payload_hash"] = "b" * 64
    e = _sources_exec(nodes)
    ref = _source_ref(_u(1), _u(1001), payload_hash="a" * 64)
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [ref], _SHARED_DOMAINS)
    assert ei.value.code == "COMPOSITION_INPUT_CHANGED"
    _assert_no_write_sql(e)


def test_sources_db_validity_false_rejected() -> None:
    e = _sources_exec(_graph_nodes({1: []}), db_valid=False)
    with pytest.raises(GovernedError) as ei:
        a2_rounds.sources(e, [_ref_to(1)], _SHARED_DOMAINS)
    assert ei.value.code == "COMPOSITION_INPUT_CHANGED"
    assert e.conn.execute.call_count == 1  # 仅有效期查询，无写
    _assert_no_write_sql(e)
