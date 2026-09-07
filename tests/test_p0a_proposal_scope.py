"""P0A §5.1A/§5.2：Semantic proposal 全量 tenant/org scope 负向用例。

覆盖：跨 tenant/跨 organization 的 human 与 agent proposal；proposal 跨 scope
list/get/snapshot/update；scope 不匹配与对象不存在的泛化错误（无枚举侧信道）。
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from memory_service import queries
from memory_service.context_graph import service
from memory_service.context_graph.service import (
    ContextGraphError,
    NotHumanConfirmerError,
    ProposalConflictError,
)
from memory_service.governance import ProposalNotFoundError
from tests.conftest import Scope, connect
from tests.fixtures_p0a import GraphRig, borrowed_connection

_VALID_ENTITY_CONTENT = {
    "type_key": "CompanyVision",
    "name": "P0A 愿景",
    "content": {"statement": "P0A scope 测试愿景"},
    "source_refs": [],
}


@pytest.fixture
def rig():
    scope = Scope()
    graph_rig = GraphRig(scope)
    try:
        yield graph_rig
    finally:
        graph_rig.cleanup()
        scope.cleanup()


def _seed_pending_proposal(rig: GraphRig, conn, *, proposed_by: str, generation_id: str) -> str:
    result = service.propose(
        tenant_id=rig.tenant_id,
        organization_id=rig.organization_id,
        generation_id=generation_id,
        target_kind="entity",
        action="create",
        proposed_content=dict(_VALID_ENTITY_CONTENT),
        proposed_by=proposed_by,
        _connect=borrowed_connection(conn),
    )
    return result["proposal_id"]


@pytest.mark.parametrize("kind", ["human", "agent_service"])
@pytest.mark.parametrize("mode", ["tenant", "organization"])
def test_propose_rejects_out_of_scope_actor(rig: GraphRig, kind: str, mode: str):
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        foreign = (
            rig.foreign_tenant_scope() if mode == "tenant" else rig.foreign_organization_scope()
        )
        outsider = rig.insert_user_in(conn, foreign, kind=kind)

        with pytest.raises(ContextGraphError):
            service.propose(
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
                generation_id=generation_id,
                target_kind="entity",
                action="create",
                proposed_content=dict(_VALID_ENTITY_CONTENT),
                proposed_by=outsider,
                _connect=borrowed_connection(conn),
            )

        # 零部分写入：越权尝试不得留下提案。
        count = conn.execute(
            "SELECT count(*) FROM memory_proposals WHERE tenant_id=%s AND organization_id=%s",
            (rig.tenant_id, rig.organization_id),
        ).fetchone()[0]
        assert count == 0


def test_propose_target_out_of_scope_is_generic_error(rig: GraphRig):
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        foreign = rig.foreign_tenant_scope()
        foreign_rig_user = rig.insert_user_in(conn, foreign)
        foreign_generation = conn.execute(
            """INSERT INTO context_graph_versions(tenant_id, organization_id, label, status)
               VALUES (%s,%s,'g-foreign','current') RETURNING generation_id""",
            foreign,
        ).fetchone()[0]
        foreign_entity = conn.execute(
            """INSERT INTO semantic_entities
                 (tenant_id, organization_id, entity_type, name, normalized_name, content,
                  revision, status, type_key, confirmed_by, confirmed_at, graph_generation_id)
               VALUES (%s,%s,NULL,'外部愿景','外部愿景','{}',1,'confirmed','CompanyVision',
                       %s,now(),%s)
               RETURNING entity_id""",
            (*foreign, foreign_rig_user, str(foreign_generation)),
        ).fetchone()[0]

        def _propose_update(target_id: str) -> None:
            service.propose(
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
                generation_id=generation_id,
                target_kind="entity",
                action="update",
                target_id=target_id,
                base_revision=1,
                proposed_content=dict(_VALID_ENTITY_CONTENT),
                proposed_by=human,
                _connect=borrowed_connection(conn),
            )

        with pytest.raises(ContextGraphError) as cross_scope:
            _propose_update(str(foreign_entity))
        with pytest.raises(ContextGraphError) as missing:
            _propose_update(str(uuid.uuid4()))
        # 泛化错误：跨 scope 与不存在完全同形，无法枚举对象。
        assert str(cross_scope.value) == str(missing.value)


def test_list_proposals_requires_and_enforces_scope(rig: GraphRig):
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )

        mine = queries.list_proposals(
            conn, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )
        assert [p["proposal_id"] for p in mine] == [proposal_id]

        foreign = rig.foreign_organization_scope()
        assert (
            queries.list_proposals(conn, tenant_id=foreign[0], organization_id=foreign[1]) == []
        )

        # scope 为 keyword-only 必填：旧式 org_id 调用与缺参调用都直接拒绝。
        with pytest.raises(TypeError):
            queries.list_proposals(conn, org_id=rig.organization_id)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            queries.list_proposals(conn)  # type: ignore[call-arg]


def test_get_proposal_cross_scope_is_indistinguishable_from_missing(rig: GraphRig):
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )
        foreign = rig.foreign_tenant_scope()

        assert (
            queries.get_proposal(
                conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
            )
            is not None
        )
        assert (
            queries.get_proposal(conn, proposal_id, tenant_id=foreign[0], organization_id=foreign[1])
            is None
        )
        assert (
            queries.get_proposal(
                conn,
                str(uuid.uuid4()),
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
            )
            is None
        )


def test_get_current_snapshot_enforces_scope(rig: GraphRig):
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        entity_id = rig.insert_confirmed_entity(
            conn, generation_id=generation_id, name="scope 内实体", confirmed_by=human
        )
        foreign = rig.foreign_organization_scope()

        snapshot = queries.get_current_snapshot(
            conn, "entity", entity_id,
            tenant_id=rig.tenant_id, organization_id=rig.organization_id,
        )
        assert snapshot is not None and snapshot["entity_id"] == entity_id
        assert (
            queries.get_current_snapshot(
                conn, "entity", entity_id, tenant_id=foreign[0], organization_id=foreign[1]
            )
            is None
        )
        # 同 scope 不存在与跨 scope 同为 None。
        assert (
            queries.get_current_snapshot(
                conn, "entity", str(uuid.uuid4()),
                tenant_id=rig.tenant_id, organization_id=rig.organization_id,
            )
            is None
        )


def test_update_proposal_content_actor_scope_and_generic_errors(rig: GraphRig):
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )
        foreign = rig.foreign_tenant_scope()
        outsider = rig.insert_user_in(conn, foreign)

        # 1) 同 scope 编辑成功（正向对照）。
        edited = queries.update_proposal_content(
            conn,
            proposal_id,
            {**_VALID_ENTITY_CONTENT, "name": "改名愿景"},
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            actor=human,
        )
        assert edited["proposed_content"]["name"] == "改名愿景"
        assert edited["source"]["original_proposed_content"]["name"] == "P0A 愿景"
        row_before = queries.get_proposal(
            conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )

        # 2) actor 跨 scope、actor 在声明的 foreign scope 内有效但 proposal 在另一 scope、
        #    proposal 不存在：三者同类同文泛化错误，且文案不得包含 proposal_id。
        #    第二种情形不能被 actor-first 校验短路——actor 在其声明 scope 内是合法用户。
        cases = [
            dict(
                pid=proposal_id,
                kwargs=dict(
                    tenant_id=rig.tenant_id, organization_id=rig.organization_id, actor=outsider
                ),
            ),
            dict(
                pid=proposal_id,
                kwargs=dict(tenant_id=foreign[0], organization_id=foreign[1], actor=outsider),
            ),
            dict(
                pid=str(uuid.uuid4()),
                kwargs=dict(
                    tenant_id=rig.tenant_id, organization_id=rig.organization_id, actor=human
                ),
            ),
        ]
        messages = []
        for case in cases:
            with pytest.raises(ProposalNotFoundError) as excinfo:
                queries.update_proposal_content(
                    conn, case["pid"], dict(_VALID_ENTITY_CONTENT), **case["kwargs"]
                )
            messages.append(str(excinfo.value))
        assert len(set(messages)) == 1, messages
        assert proposal_id not in messages[0]

        # 跨 scope 尝试后目标行完整不变。
        assert (
            queries.get_proposal(
                conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
            )
            == row_before
        )

        # 3) actor 为 keyword-only 必填。
        with pytest.raises(TypeError):
            queries.update_proposal_content(  # type: ignore[call-arg]
                conn, proposal_id, dict(_VALID_ENTITY_CONTENT)
            )


def test_propose_invalid_uuid_actor_checked_before_generation(rig: GraphRig):
    """非法 UUID 的 proposed_by 转稳定领域错误，且先于 generation 查询（不可枚举 generation）。"""
    with connect() as conn, conn.transaction():
        rig.insert_user(conn)
        with pytest.raises(ContextGraphError, match="不是合法的用户标识"):
            service.propose(
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
                # 不存在的 generation：若 actor 校验落在 generation 查询之后，
                # 这里会先报 "proposal generation is not writable" 而暴露顺序错误。
                generation_id=str(uuid.uuid4()),
                target_kind="entity",
                action="create",
                proposed_content=dict(_VALID_ENTITY_CONTENT),
                proposed_by="not-a-uuid",
                _connect=borrowed_connection(conn),
            )
        # 零部分写入，且事务未被服务端类型错误毒化（可继续读）。
        count = conn.execute(
            "SELECT count(*) FROM memory_proposals WHERE tenant_id=%s AND organization_id=%s",
            (rig.tenant_id, rig.organization_id),
        ).fetchone()[0]
        assert count == 0


class _RecordingConn:
    """记录 SQL 与绑定参数执行顺序的 conn 代理（实现 cursor 协议，供 queries.* 使用）。"""

    def __init__(self, conn):
        self._conn = conn
        self.statements: list[str] = []
        self.bound: list = []

    def cursor(self, **kwargs):
        cur = self._conn.cursor(**kwargs)
        statements = self.statements
        bound = self.bound

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                cur.close()
                return False

            def execute(self, sql, params=None):
                statements.append(str(sql))
                bound.append(params)
                return cur.execute(sql, params)

            def fetchone(self):
                return cur.fetchone()

            def fetchall(self):
                return cur.fetchall()

            @property
            def rowcount(self):
                return cur.rowcount

        return _Cur()

    def bound_params(self) -> list:
        flat: list = []
        for params in self.bound:
            if params is None:
                continue
            flat.extend(params.values() if isinstance(params, dict) else params)
        return flat

    def transaction(self):
        """透传嵌套事务（savepoint）：embedding 隔离等路径在 spy 下行为与真连接一致。"""
        return self._conn.transaction()


def test_update_proposal_content_authorizes_before_proposal_lookup(rig: GraphRig):
    """SQL spy：第一条授权动作（users FOR SHARE）必须先于任何 memory_proposals 读取。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )

        rec = _RecordingConn(conn)
        edited = queries.update_proposal_content(
            rec,
            proposal_id,
            dict(_VALID_ENTITY_CONTENT),
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            actor=human,
        )
        assert edited["proposal_id"] == proposal_id
        first_users = next(i for i, s in enumerate(rec.statements) if "FROM users" in s)
        first_proposals = next(
            i for i, s in enumerate(rec.statements) if "FROM memory_proposals" in s
        )
        assert first_users < first_proposals
        assert "FOR SHARE" in rec.statements[first_users]


def test_update_proposal_content_invalid_uuid_actor_generic_and_transaction_usable(rig: GraphRig):
    """非法 UUID actor：existing/missing proposal 同类同文，且同一事务之后仍可读。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )

        messages = []
        for pid in (proposal_id, str(uuid.uuid4())):
            with pytest.raises(ProposalNotFoundError) as excinfo:
                queries.update_proposal_content(
                    conn,
                    pid,
                    dict(_VALID_ENTITY_CONTENT),
                    tenant_id=rig.tenant_id,
                    organization_id=rig.organization_id,
                    actor="not-a-uuid",
                )
            messages.append(str(excinfo.value))
        # existing 与 missing 对非法 actor 完全同文，且不含 proposal_id。
        assert len(set(messages)) == 1
        assert proposal_id not in messages[0]
        # Python 侧预校验：事务未被毒化，仍可继续读。
        assert (
            queries.get_proposal(
                conn, proposal_id,
                tenant_id=rig.tenant_id, organization_id=rig.organization_id,
            )
            is not None
        )


def test_update_proposal_content_binds_canonical_ids(rig: GraphRig):
    """urn/无连字符等非规范 UUID 表示：SQL 绑定 canonical uuid.UUID，不绑定原始表示。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )

        rec = _RecordingConn(conn)
        edited = queries.update_proposal_content(
            rec,
            proposal_id.replace("-", ""),
            dict(_VALID_ENTITY_CONTENT),
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            actor=f"urn:uuid:{human}",
        )
        assert edited["proposal_id"] == proposal_id
        flat = rec.bound_params()
        assert proposal_id.replace("-", "") not in flat
        assert f"urn:uuid:{human}" not in flat
        assert uuid.UUID(proposal_id) in flat
        assert uuid.UUID(human) in flat


def test_propose_binds_canonical_proposed_by(rig: GraphRig):
    """service.propose：urn 形式的 proposed_by 授权后以 canonical uuid 写入 memory_proposals。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")

        rec = _RecordingConn(conn)
        result = service.propose(
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            generation_id=generation_id,
            target_kind="entity",
            action="create",
            proposed_content=dict(_VALID_ENTITY_CONTENT),
            proposed_by=f"urn:uuid:{human}",
            _connect=borrowed_connection(rec),
        )
        assert result["proposal_id"]
        flat = rec.bound_params()
        assert f"urn:uuid:{human}" not in flat
        assert uuid.UUID(human) in flat
        # 落库值也是 canonical 形式。
        stored = conn.execute(
            "SELECT proposed_by FROM memory_proposals WHERE proposal_id=%s",
            (result["proposal_id"],),
        ).fetchone()[0]
        assert str(stored) == human


def test_update_proposal_content_invalid_proposal_id_generic_and_readable(rig: GraphRig):
    """非法 proposal_id 与 valid-but-missing 同类同文，事务仍可读。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )

        messages = []
        for pid in ("not-a-uuid", str(uuid.uuid4())):
            with pytest.raises(ProposalNotFoundError) as excinfo:
                queries.update_proposal_content(
                    conn,
                    pid,
                    dict(_VALID_ENTITY_CONTENT),
                    tenant_id=rig.tenant_id,
                    organization_id=rig.organization_id,
                    actor=human,
                )
            messages.append(str(excinfo.value))
        assert len(set(messages)) == 1
        assert "not-a-uuid" not in messages[0]
        # Python 侧规范化：事务未被毒化，仍可继续读。
        assert (
            queries.get_proposal(
                conn, proposal_id,
                tenant_id=rig.tenant_id, organization_id=rig.organization_id,
            )
            is not None
        )


def test_propose_foreign_and_missing_actor_identical_error(rig: GraphRig):
    """propose：foreign actor 与 missing actor 错误类型与完整消息完全一致，不泄露任一 ID。"""
    with connect() as conn, conn.transaction():
        rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        foreign = rig.foreign_organization_scope()
        outsider = rig.insert_user_in(conn, foreign)
        missing = str(uuid.uuid4())

        messages = []
        for actor in (outsider, missing):
            with pytest.raises(ContextGraphError) as excinfo:
                service.propose(
                    tenant_id=rig.tenant_id,
                    organization_id=rig.organization_id,
                    generation_id=generation_id,
                    target_kind="entity",
                    action="create",
                    proposed_content=dict(_VALID_ENTITY_CONTENT),
                    proposed_by=actor,
                    _connect=borrowed_connection(conn),
                )
            messages.append(str(excinfo.value))
        assert messages[0] == messages[1]
        assert outsider not in messages[0] and missing not in messages[0]


def test_propose_canonicalizes_uuid_inputs(rig: GraphRig):
    """propose：urn/无连字符形式的 generation_id/target_id/import_session_id 以 canonical
    形式绑定 SQL，原始表示不出现。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        entity_id = rig.insert_confirmed_entity(
            conn, generation_id=generation_id, name="既有愿景", confirmed_by=human
        )
        import_session_id = str(uuid.uuid4())

        rec = _RecordingConn(conn)
        result = service.propose(
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            generation_id=f"urn:uuid:{generation_id}",
            target_kind="entity",
            action="update",
            target_id=f"urn:uuid:{entity_id}",
            base_revision=1,
            proposed_content=dict(_VALID_ENTITY_CONTENT),
            proposed_by=human,
            import_session_id=import_session_id.replace("-", ""),
            _connect=borrowed_connection(rec),
        )
        assert result["proposal_id"]
        flat = rec.bound_params()
        for raw in (
            f"urn:uuid:{generation_id}",
            f"urn:uuid:{entity_id}",
            import_session_id.replace("-", ""),
        ):
            assert raw not in flat
        for canonical in (generation_id, entity_id, import_session_id):
            assert canonical in flat


def test_propose_invalid_uuid_inputs_fail_before_any_sql(rig: GraphRig):
    """propose：非法 generation_id/target_id/import_session_id 在任何 SQL 前以领域级
    泛化错误失败（不回显输入），借用事务零语句、未被毒化。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        entity_id = rig.insert_confirmed_entity(
            conn, generation_id=generation_id, name="既有愿景", confirmed_by=human
        )

        base = dict(
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            target_kind="entity",
            proposed_content=dict(_VALID_ENTITY_CONTENT),
            proposed_by=human,
        )
        cases = [
            dict(generation_id="not-a-uuid", action="create"),
            dict(
                generation_id=generation_id, action="update", target_id="not-a-uuid",
                base_revision=1,
            ),
            dict(generation_id=generation_id, action="create", import_session_id="not-a-uuid"),
        ]
        for bad in cases:
            rec = _RecordingConn(conn)
            with pytest.raises(ContextGraphError) as excinfo:
                service.propose(**{**base, **bad}, _connect=borrowed_connection(rec))
            assert "not-a-uuid" not in str(excinfo.value)
            assert rec.statements == [], "非法 UUID 不得产生任何 SQL"
            # 借用事务未被毒化。
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        count = conn.execute(
            "SELECT count(*) FROM memory_proposals WHERE tenant_id=%s AND organization_id=%s",
            (rig.tenant_id, rig.organization_id),
        ).fetchone()[0]
        assert count == 0


class _ExplodingGateway:
    """任何 chat/embed 调用都立即失败：证明负面路径不触网络。"""

    def chat(self, *args, **kwargs):
        raise AssertionError("负面路径不得发起 chat")

    def embed(self, *args, **kwargs):
        raise AssertionError("负面路径不得发起 embed")


class _EmbedGateway:
    def __init__(self):
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return [[0.0]]


def _confirmable_content() -> dict:
    excerpt = "冻结来源摘录"
    return {
        **_VALID_ENTITY_CONTENT,
        "source_refs": [
            {
                "fragment_id": str(uuid.uuid4()),
                "ordinal": 1,
                "excerpt_snapshot": excerpt,
                "content_hash_snapshot": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            }
        ],
    }


def test_confirm_missing_foreign_invalid_uniform(rig: GraphRig):
    """confirm：invalid/missing/foreign 统一泛化；授权前不锁定 foreign proposal、不改行。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )
        foreign = rig.foreign_tenant_scope()
        foreign_human = rig.insert_user_in(conn, foreign, kind="human")
        row_before = queries.get_proposal(
            conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )

        # 1) 非法 proposal_id：任何 SQL 前失败，零语句。
        rec = _RecordingConn(conn)
        with pytest.raises(ProposalConflictError) as invalid:
            service.confirm(
                "not-a-uuid", decided_by=human,
                gateway=_ExplodingGateway(), _connect=borrowed_connection(rec),
            )
        assert rec.statements == []
        assert "not-a-uuid" not in str(invalid.value)

        # 2) missing actor（随机 UUID 不存在）：仅一次 users 授权读，无 advisory 锁、
        #    不触碰 memory_proposals。
        rec = _RecordingConn(conn)
        with pytest.raises(NotHumanConfirmerError):
            service.confirm(
                proposal_id, decided_by=str(uuid.uuid4()),
                gateway=_ExplodingGateway(), _connect=borrowed_connection(rec),
            )
        assert not any("pg_advisory" in sql for sql in rec.statements)
        assert not any("memory_proposals" in sql for sql in rec.statements)

        # 3) foreign-existing 与 missing 同类型同文、不含 ID；两种情形都在 scoped lookup
        #    阶段失败，advisory lock 零次；foreign 情形下唯一触碰 memory_proposals 的语句
        #    按 actor 自身 scope 约束（零行），绝不按 proposal 的 home scope 锁定该行。
        rec_foreign = _RecordingConn(conn)
        with pytest.raises(ProposalConflictError) as foreign_exc:
            service.confirm(
                proposal_id, decided_by=foreign_human,
                gateway=_ExplodingGateway(), _connect=borrowed_connection(rec_foreign),
            )
        rec_missing = _RecordingConn(conn)
        with pytest.raises(ProposalConflictError) as missing_exc:
            service.confirm(
                str(uuid.uuid4()), decided_by=human,
                gateway=_ExplodingGateway(), _connect=borrowed_connection(rec_missing),
            )
        assert str(foreign_exc.value) == str(missing_exc.value)
        assert proposal_id not in str(foreign_exc.value)
        for rec in (rec_foreign, rec_missing):
            assert not any("pg_advisory" in sql for sql in rec.statements), (
                "missing/foreign 必须先于 advisory lock 失败"
            )
        for sql, params in zip(rec_foreign.statements, rec_foreign.bound):
            if "memory_proposals" in sql:
                assert params is not None and foreign[0] in params and rig.tenant_id not in params

        # 4) 全部负面尝试后目标行完整不变、事务可读。
        assert (
            queries.get_proposal(
                conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
            )
            == row_before
        )


def test_reject_missing_foreign_invalid_uniform(rig: GraphRig):
    """reject：invalid/missing/foreign 统一泛化；授权前不锁定 foreign proposal、不改行。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )
        foreign = rig.foreign_tenant_scope()
        foreign_human = rig.insert_user_in(conn, foreign, kind="human")
        row_before = queries.get_proposal(
            conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )

        rec = _RecordingConn(conn)
        with pytest.raises(ProposalConflictError):
            service.reject("not-a-uuid", decided_by=human, _connect=borrowed_connection(rec))
        assert rec.statements == []

        rec = _RecordingConn(conn)
        with pytest.raises(NotHumanConfirmerError):
            service.reject(
                proposal_id, decided_by=str(uuid.uuid4()), _connect=borrowed_connection(rec)
            )
        assert not any("pg_advisory" in sql for sql in rec.statements)
        assert not any("memory_proposals" in sql for sql in rec.statements)

        rec = _RecordingConn(conn)
        with pytest.raises(ProposalConflictError) as foreign_exc:
            service.reject(proposal_id, decided_by=foreign_human, _connect=borrowed_connection(rec))
        rec_missing = _RecordingConn(conn)
        with pytest.raises(ProposalConflictError) as missing_exc:
            service.reject(
                str(uuid.uuid4()), decided_by=human, _connect=borrowed_connection(rec_missing)
            )
        assert str(foreign_exc.value) == str(missing_exc.value)
        assert proposal_id not in str(foreign_exc.value)
        for r in (rec, rec_missing):
            assert not any("pg_advisory" in sql for sql in r.statements), (
                "missing/foreign 必须先于 advisory lock 失败"
            )
        for sql, params in zip(rec.statements, rec.bound):
            if "memory_proposals" in sql:
                assert params is not None and foreign[0] in params and rig.tenant_id not in params

        assert (
            queries.get_proposal(
                conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
            )
            == row_before
        )


def test_confirm_accepts_canonical_urn_proposal_id(rig: GraphRig):
    """confirm 正向：urn 形式 proposal_id 规范后命中，SQL 绑定 canonical。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = service.propose(
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            generation_id=generation_id,
            target_kind="entity",
            action="create",
            proposed_content=_confirmable_content(),
            proposed_by=human,
            _connect=borrowed_connection(conn),
        )["proposal_id"]

        rec = _RecordingConn(conn)
        result = service.confirm(
            f"urn:uuid:{proposal_id}", decided_by=human,
            gateway=_EmbedGateway(), _connect=borrowed_connection(rec),
        )
        assert result["status"] == "confirmed"
        flat = rec.bound_params()
        assert f"urn:uuid:{proposal_id}" not in flat
        assert uuid.UUID(proposal_id) in flat
        row = queries.get_proposal(
            conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )
        assert row["status"] == "confirmed"


def test_reject_accepts_canonical_urn_proposal_id(rig: GraphRig):
    """reject 正向：urn 形式 proposal_id 规范后命中，SQL 绑定 canonical。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = _seed_pending_proposal(
            rig, conn, proposed_by=human, generation_id=generation_id
        )

        rec = _RecordingConn(conn)
        result = service.reject(
            f"urn:uuid:{proposal_id}", decided_by=human, _connect=borrowed_connection(rec)
        )
        assert result["status"] == "rejected"
        flat = rec.bound_params()
        assert f"urn:uuid:{proposal_id}" not in flat
        assert uuid.UUID(proposal_id) in flat
        row = queries.get_proposal(
            conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )
        assert row["status"] == "rejected"


def test_propose_legacy_generation_urn_still_read_only(rig: GraphRig):
    """legacy 代 UUID 的 urn:uuid: 等价形式不得绕过只读门禁：canonical 后再判 legacy，
    零 SQL、事务不毒化。"""
    from memory_service.context_graph.query_contracts import LEGACY_GENERATION_ID

    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        rec = _RecordingConn(conn)
        with pytest.raises(ContextGraphError) as excinfo:
            service.propose(
                tenant_id=rig.tenant_id,
                organization_id=rig.organization_id,
                generation_id=f"urn:uuid:{LEGACY_GENERATION_ID}",
                target_kind="entity",
                action="create",
                proposed_content=dict(_VALID_ENTITY_CONTENT),
                proposed_by=human,
                _connect=borrowed_connection(rec),
            )
        assert "read-only" in str(excinfo.value)
        assert rec.statements == [], "legacy 门禁失败前不得产生任何 SQL"
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_confirm_embedding_failure_leaves_borrowed_transaction_usable(rig: GraphRig):
    """1 维向量写入 halfvec(2048) 列必失败：savepoint 隔离后同一 caller 事务仍可读、
    治理写（proposal confirmed + 实体行）仍在。"""
    with connect() as conn, conn.transaction():
        human = rig.insert_user(conn)
        generation_id = rig.insert_generation(conn, label="g-current", status="current")
        proposal_id = service.propose(
            tenant_id=rig.tenant_id,
            organization_id=rig.organization_id,
            generation_id=generation_id,
            target_kind="entity",
            action="create",
            proposed_content=_confirmable_content(),
            proposed_by=human,
            _connect=borrowed_connection(conn),
        )["proposal_id"]

        gateway = _EmbedGateway()  # 返回 1 维向量：UPDATE halfvec(2048) 必失败
        result = service.confirm(
            proposal_id, decided_by=human, gateway=gateway, _connect=borrowed_connection(conn)
        )
        assert result["status"] == "confirmed"
        assert result["embedding"].startswith("failed"), result["embedding"]
        # 外层借用事务未被 embedding UPDATE 的失败毒化：仍可读。
        assert conn.execute("SELECT 1").fetchone()[0] == 1
        row = queries.get_proposal(
            conn, proposal_id, tenant_id=rig.tenant_id, organization_id=rig.organization_id
        )
        assert row["status"] == "confirmed"
        entity = conn.execute(
            "SELECT status FROM semantic_entities WHERE entity_id=%s", (result["target_id"],)
        ).fetchone()
        assert entity is not None and entity[0] == "confirmed"
