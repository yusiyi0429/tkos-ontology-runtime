"""P0A §5.1D/§5.2：非破坏式 Context Graph switch 与生产 scope fail closed。

覆盖：switch 后旧 generation 的实体/关系/来源引用（entity+relation）/提案/审计六类表
逐 PK 全行保留、旧代 retired、目标代 current、返回显式 retained（relation_source_refs
非零种子）与零 deleted；生产 scope 在任何 DB/preflight/snapshot/object-store/lock 之前
即 aborted（allow_production=True 也不例外）；未授权 actor（跨 scope human / 同 scope
agent / 不存在）在仅一次只读身份校验后即 aborted，store/preflight/snapshot/lock 零副作用；
run_switch_transaction 事务自包含：内部失败或跨 scope switched_by 复核失败，调用方
catch 后 commit 也不留半切换。
"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory_service.context_graph import switch as switch_module
from memory_service.context_graph.switch import SwitchGateError, switch_graph
from tests.conftest import Scope, connect
from tests.fixtures_p0a import GraphRig, InMemoryObjectStore


@pytest.fixture
def rig():
    scope = Scope()
    graph_rig = GraphRig(scope)
    try:
        yield graph_rig
    finally:
        graph_rig.cleanup()
        scope.cleanup()


def _seed_two_generations(rig: GraphRig, conn) -> dict:
    """current 代（实体×2 + 关系 + 来源引用 + 提案 + 审计）与 shadow 代（实体×1）。"""
    human = rig.insert_user(conn)
    old_generation = rig.insert_generation(conn, label="g-old", status="current")
    new_generation = rig.insert_generation(conn, label="g-new", status="shadow")

    entity_a = rig.insert_confirmed_entity(
        conn, generation_id=old_generation, name="旧代根愿景", confirmed_by=human
    )
    # uq_ent_vision_root：每代至多一个 CompanyVision；第二实体用 OperatingPrinciple。
    entity_b = str(
        conn.execute(
            """INSERT INTO semantic_entities
                 (tenant_id, organization_id, entity_type, name, normalized_name, content,
                  revision, status, type_key, confirmed_by, confirmed_at, graph_generation_id)
               VALUES (%s,%s,NULL,'旧代原则','旧代原则','{}',1,'confirmed','OperatingPrinciple',
                       %s,now(),%s)
               RETURNING entity_id""",
            (rig.tenant_id, rig.organization_id, human, old_generation),
        ).fetchone()[0]
    )
    rig.insert_confirmed_entity(conn, generation_id=new_generation, name="新代愿景", confirmed_by=human)

    relation_id = str(
        conn.execute(
            """INSERT INTO semantic_relations
                 (tenant_id, organization_id, source_id, target_id, relation_type, content,
                  revision, status, confirmed_by, confirmed_at, graph_generation_id)
               VALUES (%s,%s,%s,%s,'supports','{}',1,'confirmed',%s,now(),%s)
               RETURNING relation_id""",
            (rig.tenant_id, rig.organization_id, entity_b, entity_a, human, old_generation),
        ).fetchone()[0]
    )

    excerpt = "旧代来源摘录"
    conn.execute(
        """INSERT INTO semantic_entity_source_refs
             (entity_id, graph_generation_id, tenant_id, organization_id, fragment_id,
              ordinal, excerpt_snapshot, content_hash_snapshot)
           VALUES (%s,%s,%s,%s,%s,1,%s,%s)""",
        (entity_a, old_generation, rig.tenant_id, rig.organization_id, str(uuid.uuid4()),
         excerpt, hashlib.sha256(excerpt.encode("utf-8")).hexdigest()),
    )
    # relation_source_refs 必须非零：retained 计数与全行对比不能覆盖一张空表。
    rel_excerpt = "旧代关系来源摘录"
    conn.execute(
        """INSERT INTO semantic_relation_source_refs
             (relation_id, graph_generation_id, tenant_id, organization_id, fragment_id,
              ordinal, excerpt_snapshot, content_hash_snapshot)
           VALUES (%s,%s,%s,%s,%s,1,%s,%s)""",
        (relation_id, old_generation, rig.tenant_id, rig.organization_id, str(uuid.uuid4()),
         rel_excerpt, hashlib.sha256(rel_excerpt.encode("utf-8")).hexdigest()),
    )

    proposal_id = str(
        conn.execute(
            """INSERT INTO memory_proposals
                 (tenant_id, organization_id, graph_generation_id, target_kind, target_id,
                  base_revision, action, proposed_content, proposed_by, status)
               VALUES (%s,%s,%s,'entity',%s,1,'create',%s,%s,'confirmed')
               RETURNING proposal_id""",
            (rig.tenant_id, rig.organization_id, old_generation, entity_a,
             Jsonb({"name": "旧代根愿景"}), human),
        ).fetchone()[0]
    )
    conn.execute(
        """INSERT INTO memory_audit
             (proposal_id, target_kind, target_id, revision, decision, decided_by)
           VALUES (%s,'entity',%s,1,'confirmed',%s)""",
        (proposal_id, entity_a, human),
    )

    return {
        "human": human,
        "old": old_generation,
        "new": new_generation,
        "entities": (entity_a, entity_b),
    }


def _old_generation_counts(conn, rig: GraphRig, generation_id: str) -> dict[str, int]:
    scope = (generation_id, rig.tenant_id, rig.organization_id)
    return {
        "entities": conn.execute(
            "SELECT count(*) FROM semantic_entities"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s", scope,
        ).fetchone()[0],
        "relations": conn.execute(
            "SELECT count(*) FROM semantic_relations"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s", scope,
        ).fetchone()[0],
        "entity_source_refs": conn.execute(
            "SELECT count(*) FROM semantic_entity_source_refs"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s", scope,
        ).fetchone()[0],
        "relation_source_refs": conn.execute(
            "SELECT count(*) FROM semantic_relation_source_refs"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s", scope,
        ).fetchone()[0],
        "proposals": conn.execute(
            "SELECT count(*) FROM memory_proposals"
            " WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s", scope,
        ).fetchone()[0],
        "audits": conn.execute(
            "SELECT count(*) FROM memory_audit a JOIN memory_proposals p USING (proposal_id)"
            " WHERE p.graph_generation_id=%s AND p.tenant_id=%s AND p.organization_id=%s", scope,
        ).fetchone()[0],
    }


def _old_generation_rows(conn, rig: GraphRig, generation_id: str) -> dict[str, list[dict]]:
    """旧代六类台账表的逐 PK 排序全行快照（切换前后必须完全相等）。"""
    scope = (generation_id, rig.tenant_id, rig.organization_id)

    def dump(sql: str) -> list[dict]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, scope)
            return [dict(r) for r in cur.fetchall()]

    return {
        "semantic_entities": dump(
            "SELECT * FROM semantic_entities WHERE graph_generation_id=%s"
            " AND tenant_id=%s AND organization_id=%s ORDER BY entity_id"
        ),
        "semantic_relations": dump(
            "SELECT * FROM semantic_relations WHERE graph_generation_id=%s"
            " AND tenant_id=%s AND organization_id=%s ORDER BY relation_id"
        ),
        "semantic_entity_source_refs": dump(
            "SELECT * FROM semantic_entity_source_refs WHERE graph_generation_id=%s"
            " AND tenant_id=%s AND organization_id=%s ORDER BY entity_id, fragment_id"
        ),
        "semantic_relation_source_refs": dump(
            "SELECT * FROM semantic_relation_source_refs WHERE graph_generation_id=%s"
            " AND tenant_id=%s AND organization_id=%s ORDER BY relation_id, fragment_id"
        ),
        "memory_proposals": dump(
            "SELECT * FROM memory_proposals WHERE graph_generation_id=%s"
            " AND tenant_id=%s AND organization_id=%s ORDER BY proposal_id"
        ),
        "memory_audit": dump(
            "SELECT a.* FROM memory_audit a JOIN memory_proposals p USING (proposal_id)"
            " WHERE p.graph_generation_id=%s AND p.tenant_id=%s AND p.organization_id=%s"
            " ORDER BY a.audit_id"
        ),
    }


def _generation_statuses(conn, rig: GraphRig) -> dict[str, tuple[str, object]]:
    return {
        str(row[0]): (row[1], row[2])
        for row in conn.execute(
            "SELECT generation_id, status, retired_at FROM context_graph_versions"
            " WHERE tenant_id=%s AND organization_id=%s",
            (rig.tenant_id, rig.organization_id),
        ).fetchall()
    }
def test_switch_is_non_destructive_and_reports_retained(rig: GraphRig):
    with connect() as conn, conn.transaction():
        seeded = _seed_two_generations(rig, conn)
    with connect() as conn:
        before = _old_generation_counts(conn, rig, seeded["old"])
        rows_before = _old_generation_rows(conn, rig, seeded["old"])
    # relation_source_refs 必须非零，否则 retained/全行对比覆盖的是一张空表。
    assert before == {
        "entities": 2, "relations": 1, "entity_source_refs": 1, "relation_source_refs": 1,
        "proposals": 1, "audits": 1,
    }
    assert rows_before["semantic_relation_source_refs"], "relation_source_refs 种子不能为空"

    store = InMemoryObjectStore()
    outcome = switch_graph(
        tenant_id=rig.tenant_id,
        organization_id=rig.organization_id,
        from_generation=seeded["old"],
        to_generation=seeded["new"],
        # urn 形式的 switched_by：编排层必须规范化为 canonical 后再下传（PG 不接受 urn）。
        switched_by=f"urn:uuid:{seeded['human']}",
        db_connect=connect,
        object_store=store,
    )

    assert outcome.status == "ok", outcome.aborted_reason
    # 显式零删除 + 显式保留计数，调用方不会把“未返回 deleted”误解为清理成功。
    assert outcome.deleted == {"deleted_entity_count": 0, "deleted_relation_count": 0}
    assert outcome.retained == before
    assert len(store.objects) == 1, "旧代不可变快照应已写入对象存储"

    with connect() as conn:
        # 快照账本 created_by 落库为 canonical uuid（urn 形式未穿透到 SQL）。
        snapshot_created_by = conn.execute(
            "SELECT created_by FROM context_graph_snapshots"
            " WHERE tenant_id=%s AND organization_id=%s",
            (rig.tenant_id, rig.organization_id),
        ).fetchone()[0]
        assert str(snapshot_created_by) == seeded["human"]
        # 旧代六类台账表逐 PK 全行一致（非破坏式：不止计数，连内容都不变）。
        assert _old_generation_counts(conn, rig, seeded["old"]) == before
        assert _old_generation_rows(conn, rig, seeded["old"]) == rows_before
        statuses = _generation_statuses(conn, rig)
        old_status, old_retired_at = statuses[seeded["old"]]
        new_status, _ = statuses[seeded["new"]]
        assert old_status == "retired" and old_retired_at is not None
        assert new_status == "current"


@pytest.mark.parametrize("allow_production", [False, True])
def test_production_scope_switch_always_fails_closed(allow_production: bool):
    """生产 scope 在任何 DB/preflight/snapshot/object-store/lock 之前即拒绝。"""

    def forbidden_connect():
        raise AssertionError("生产 scope 切换不得触碰数据库")

    class ForbiddenStore:
        def preflight_immutability(self):
            raise AssertionError("生产 scope 切换不得触碰对象存储")

    outcome = switch_graph(
        tenant_id="local",
        organization_id="local-org",
        from_generation=str(uuid.uuid4()),
        to_generation=str(uuid.uuid4()),
        switched_by=str(uuid.uuid4()),
        db_connect=forbidden_connect,
        object_store=ForbiddenStore(),
        allow_production=allow_production,
    )
    assert outcome.status == "aborted"
    assert outcome.retained is None and outcome.deleted is None
    assert "生产" in outcome.aborted_reason


@pytest.mark.parametrize("actor_case", ["foreign_human", "in_scope_agent", "missing"])
def test_unauthorized_actor_aborts_before_any_side_effect(
    rig: GraphRig, actor_case: str, monkeypatch
):
    """未授权 actor：preflight/snapshot/lock/switch-txn 全部零调用（forbidden spy 证明），
    store 零触碰，DB 仅允许一次身份校验连接。"""
    with connect() as conn, conn.transaction():
        seeded = _seed_two_generations(rig, conn)
        if actor_case == "foreign_human":
            switched_by = rig.insert_user_in(conn, rig.foreign_tenant_scope(), kind="human")
        elif actor_case == "in_scope_agent":
            switched_by = rig.insert_user(conn, kind="agent_service", display="p0a-switch-agent")
        else:
            switched_by = str(uuid.uuid4())

    forbidden_calls: list[str] = []

    def _forbidden(name):
        def _spy(*args, **kwargs):
            forbidden_calls.append(name)
            raise AssertionError(f"未授权 switch 不得调用 {name}")

        return _spy

    for name in (
        "minimal_preflight", "create_switch_snapshot", "acquire_exclusive_lock",
        "release_exclusive_lock", "run_switch_transaction",
    ):
        monkeypatch.setattr(switch_module, name, _forbidden(name))

    calls = {"connect": 0}

    def counting_connect():
        calls["connect"] += 1
        if calls["connect"] > 1:
            raise AssertionError("未授权 switch 只允许一次只读身份校验连接")
        return connect()

    class ForbiddenStore:
        def preflight_immutability(self):
            raise AssertionError("未授权 switch 不得触碰对象存储")

        def put_if_absent(self, *args, **kwargs):
            raise AssertionError("未授权 switch 不得写入对象存储")

    outcome = switch_graph(
        tenant_id=rig.tenant_id,
        organization_id=rig.organization_id,
        from_generation=seeded["old"],
        to_generation=seeded["new"],
        switched_by=switched_by,
        db_connect=counting_connect,
        object_store=ForbiddenStore(),
    )
    assert outcome.status == "aborted"
    assert outcome.retained is None and outcome.deleted is None
    assert outcome.snapshot is None
    # preflight/snapshot/lock/switch 事务函数全部零调用。
    assert forbidden_calls == []
    # 仅身份校验这一次连接；preflight/snapshot/lock 都没有第二次连库机会。
    assert calls["connect"] == 1

    with connect() as conn:
        # 无快照台账行，两代状态完全不变。
        snapshots = conn.execute(
            "SELECT count(*) FROM context_graph_snapshots"
            " WHERE tenant_id=%s AND organization_id=%s",
            (rig.tenant_id, rig.organization_id),
        ).fetchone()[0]
        assert snapshots == 0
        statuses = _generation_statuses(conn, rig)
        assert statuses[seeded["old"]][0] == "current"
        assert statuses[seeded["new"]][0] == "shadow"


def test_switch_invalid_generation_id_aborts_before_side_effects(rig: GraphRig, monkeypatch):
    """非法 from/to generation UUID：任何 DB/store/preflight/lock 之前 fail-closed aborted。"""
    forbidden_calls: list[str] = []

    def _forbidden(name):
        def _spy(*args, **kwargs):
            forbidden_calls.append(name)
            raise AssertionError(f"非法 generation_id 不得调用 {name}")

        return _spy

    for name in (
        "minimal_preflight", "create_switch_snapshot", "acquire_exclusive_lock",
        "release_exclusive_lock", "run_switch_transaction",
    ):
        monkeypatch.setattr(switch_module, name, _forbidden(name))

    def forbidden_connect():
        raise AssertionError("非法 generation_id 不得触碰数据库")

    class ForbiddenStore:
        def preflight_immutability(self):
            raise AssertionError("非法 generation_id 不得触碰对象存储")

    for from_gen, to_gen in (("not-a-uuid", str(uuid.uuid4())), (str(uuid.uuid4()), "not-a-uuid")):
        outcome = switch_graph(
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            from_generation=from_gen,
            to_generation=to_gen,
            switched_by=str(uuid.uuid4()),
            db_connect=forbidden_connect,
            object_store=ForbiddenStore(),
        )
        assert outcome.status == "aborted"
        assert "UUID" in outcome.aborted_reason
        assert outcome.snapshot is None and outcome.retained is None and outcome.deleted is None
    assert forbidden_calls == []


def test_run_switch_transaction_failure_rolls_back_even_if_caller_commits(
    rig: GraphRig, monkeypatch
):
    """事务函数内部失败（两处 UPDATE 之后）：异常返回前已 rollback，调用方 commit 也无半切换。"""
    with connect() as conn, conn.transaction():
        seeded = _seed_two_generations(rig, conn)

    def boom(*args, **kwargs):
        raise RuntimeError("模拟 retained 统计失败")

    monkeypatch.setattr(switch_module, "_count_retained_rows", boom)

    conn = connect()
    try:
        with pytest.raises(RuntimeError, match="模拟 retained 统计失败"):
            switch_module.run_switch_transaction(
                conn,
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
                from_generation=seeded["old"],
                to_generation=seeded["new"],
                switched_by=seeded["human"],
            )
        conn.commit()  # 调用方 catch 后 commit：不得留下 old=retired/new 未 current
    finally:
        conn.close()

    with connect() as conn:
        statuses = _generation_statuses(conn, rig)
        assert statuses[seeded["old"]][0] == "current"
        assert statuses[seeded["old"]][1] is None  # retired_at 不得被写上
        assert statuses[seeded["new"]][0] == "shadow"


def test_run_switch_transaction_rechecks_switched_by_in_transaction(rig: GraphRig):
    """直接调用事务函数：跨 scope switched_by 在同一事务内复核即拒，commit 后无半切换。"""
    with connect() as conn, conn.transaction():
        seeded = _seed_two_generations(rig, conn)
        foreign_human = rig.insert_user_in(conn, rig.foreign_tenant_scope(), kind="human")

    conn = connect()
    try:
        with pytest.raises(SwitchGateError, match="切换人校验失败"):
            switch_module.run_switch_transaction(
                conn,
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
                from_generation=seeded["old"],
                to_generation=seeded["new"],
                switched_by=foreign_human,
            )
        conn.commit()
    finally:
        conn.close()

    with connect() as conn:
        statuses = _generation_statuses(conn, rig)
        assert statuses[seeded["old"]][0] == "current"
        assert statuses[seeded["new"]][0] == "shadow"


def test_minimal_preflight_count_query_is_scope_constrained(rig: GraphRig):
    """defense-in-depth：confirmed 实体计数显式约束 generation+tenant+org（SQL 记录证明）。"""
    with connect() as conn, conn.transaction():
        seeded = _seed_two_generations(rig, conn)

    statements: list[str] = []

    def recording_connect():
        conn = connect()

        class _Rec:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                conn.close()
                return False

            def execute(self, sql, params=None):
                statements.append(str(sql))
                return conn.execute(sql, params)

        return _Rec()

    problems = switch_module.minimal_preflight(
        recording_connect,
        tenant_id=rig.tenant_id,
        organization_id=rig.organization_id,
        from_generation=seeded["old"],
        to_generation=seeded["new"],
    )
    assert problems == []
    count_sql = next(s for s in statements if "FROM semantic_entities" in s)
    assert "graph_generation_id=%s" in count_sql
    assert "tenant_id=%s" in count_sql
    assert "organization_id=%s" in count_sql


def test_minimal_preflight_foreign_scope_generation_is_missing(rig: GraphRig):
    """跨 scope 的 shadow 代在 preflight 中等同不存在（scope 约束的行为面）。"""
    with connect() as conn, conn.transaction():
        seeded = _seed_two_generations(rig, conn)
        foreign = rig.foreign_tenant_scope()
        foreign_generation = str(
            conn.execute(
                """INSERT INTO context_graph_versions(tenant_id, organization_id, label, status)
                   VALUES (%s,%s,'g-foreign-shadow','shadow') RETURNING generation_id""",
                foreign,
            ).fetchone()[0]
        )

    problems = switch_module.minimal_preflight(
        connect,
        tenant_id=rig.tenant_id,
        organization_id=rig.organization_id,
        from_generation=seeded["old"],
        to_generation=foreign_generation,
    )
    assert problems and "不存在" in problems[0]
