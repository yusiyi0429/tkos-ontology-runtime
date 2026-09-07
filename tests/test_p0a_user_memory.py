"""P0A §5.1C/§5.2：User Memory ownership 与 conversation owner 校验。

覆盖：User A 不能更新/删除 User B 的 memory；owner 不匹配与对象不存在同类泛化错误；
extract_user_memories 显式 user 必须等于 conversation owner，且在任何
transcript/chat/embed/INSERT 之前失败。
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from memory_service import user_memory
from tests.conftest import Scope, connect
from tests.fixtures_p0a import RecordingConn


class _ExplodingGateway:
    """任何 chat/embed 调用都立即失败：证明 owner 校验先于网络调用。"""

    def chat(self, *args, **kwargs):
        raise AssertionError("owner 校验失败前不得发起 chat")

    def embed(self, *args, **kwargs):
        raise AssertionError("owner 校验失败前不得发起 embed")


@pytest.fixture
def users():
    """隔离 scope 内的 User A / User B；结束清理 user_memories、conversations、users。"""
    scope = Scope()
    try:
        with connect() as conn, conn.transaction():
            user_a = scope.ensure_human(conn)
            row = conn.execute(
                """INSERT INTO users(tenant_id, organization_id, kind, display_name)
                   VALUES (%s, %s, 'human', %s) RETURNING user_id""",
                (scope.tenant_id, scope.organization_id, f"用户B-{scope.tenant_id}"),
            ).fetchone()
            user_b = str(row[0])
        yield scope, user_a, user_b
    finally:
        with connect() as conn, conn.transaction():
            conn.execute(
                """DELETE FROM user_memories WHERE user_id IN (
                       SELECT user_id FROM users WHERE tenant_id=%s AND organization_id=%s)""",
                (scope.tenant_id, scope.organization_id),
            )
            # FK 安全顺序：run_events → runs → conversations → users。
            conn.execute(
                """DELETE FROM run_events WHERE run_id IN (
                       SELECT r.run_id FROM runs r
                       JOIN conversations c ON c.conversation_id = r.conversation_id
                       WHERE c.tenant_id=%s AND c.organization_id=%s)""",
                (scope.tenant_id, scope.organization_id),
            )
            conn.execute(
                """DELETE FROM runs WHERE conversation_id IN (
                       SELECT conversation_id FROM conversations
                       WHERE tenant_id=%s AND organization_id=%s)""",
                (scope.tenant_id, scope.organization_id),
            )
            conn.execute(
                "DELETE FROM conversations WHERE tenant_id=%s AND organization_id=%s",
                (scope.tenant_id, scope.organization_id),
            )
        scope.cleanup()


def _insert_memory(conn, user_id: str, content: str = "B 的长期偏好") -> str:
    row = conn.execute(
        "INSERT INTO user_memories(user_id, kind, content) VALUES (%s,'preference',%s)"
        " RETURNING memory_id",
        (user_id, content),
    ).fetchone()
    return str(row[0])


def _full_row(conn, memory_id: str):
    """整行快照：失败的写操作必须保持所有列完全不变。"""
    return conn.execute(
        "SELECT * FROM user_memories WHERE memory_id=%s", (memory_id,)
    ).fetchone()


def test_cross_user_update_rejected_and_generic(users):
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        memory_id = _insert_memory(conn, user_b)
        before = _full_row(conn, memory_id)

        with pytest.raises(LookupError) as cross:
            user_memory.update_user_memory(
                conn, memory_id, content="A 的篡改", owner_user_id=user_a
            )
        with pytest.raises(LookupError) as missing:
            user_memory.update_user_memory(
                conn, str(uuid.uuid4()), content="x", owner_user_id=user_a
            )
        # owner 不匹配与不存在同类同文，且文案不含 memory_id，不泄露目标是否存在。
        assert str(cross.value) == str(missing.value)
        assert memory_id not in str(cross.value)

        # 全行不变（含 content/updated_at/deleted 等所有列）。
        assert _full_row(conn, memory_id) == before


def test_cross_user_delete_rejected_and_generic(users):
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        memory_id = _insert_memory(conn, user_b)
        before = _full_row(conn, memory_id)

        with pytest.raises(LookupError) as cross:
            user_memory.delete_user_memory(conn, memory_id, owner_user_id=user_a)
        with pytest.raises(LookupError) as missing:
            user_memory.delete_user_memory(conn, str(uuid.uuid4()), owner_user_id=user_a)
        assert str(cross.value) == str(missing.value)
        assert memory_id not in str(cross.value)

        # 全行不变（含 deleted 标志）。
        assert _full_row(conn, memory_id) == before


def test_owner_can_update_and_delete(users):
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        memory_id = _insert_memory(conn, user_b)
        updated = user_memory.update_user_memory(
            conn, memory_id, content="B 自己修订", owner_user_id=user_b
        )
        assert updated["content"] == "B 自己修订"
        user_memory.delete_user_memory(conn, memory_id, owner_user_id=user_b)
        row = conn.execute(
            "SELECT deleted FROM user_memories WHERE memory_id=%s", (memory_id,)
        ).fetchone()
        assert row[0] is True


def _memory_count(scope: Scope) -> int:
    with connect() as conn:
        return conn.execute(
            """SELECT count(*) FROM user_memories WHERE user_id IN (
                   SELECT user_id FROM users WHERE tenant_id=%s AND organization_id=%s)""",
            (scope.tenant_id, scope.organization_id),
        ).fetchone()[0]


def test_extract_explicit_user_must_match_conversation_owner(users, monkeypatch):
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        conversation_id = str(
            conn.execute(
                """INSERT INTO conversations(tenant_id, organization_id, user_id, agent_id)
                   VALUES (%s,%s,%s,'p0a-agent') RETURNING conversation_id""",
                (scope.tenant_id, scope.organization_id, user_a),
            ).fetchone()[0]
        )

    # transcript spy：owner 不匹配时连 transcript 读取都不得发生（遑论 chat/embed/INSERT）。
    calls = {"transcript": 0}
    real_transcript = user_memory.conversation_transcript

    def spy(conn, cid):
        calls["transcript"] += 1
        return real_transcript(conn, cid)

    monkeypatch.setattr(user_memory, "conversation_transcript", spy)

    gateway = _ExplodingGateway()
    before = _memory_count(scope)
    # owner 不匹配：在 transcript/chat/embed/INSERT 之前失败。
    with pytest.raises(LookupError) as mismatch:
        user_memory.extract_user_memories(
            connect, conversation_id, user_id=user_b, gateway=gateway
        )
    # conversation 不存在：同类同文泛化错误。
    with pytest.raises(LookupError) as missing:
        user_memory.extract_user_memories(
            connect, str(uuid.uuid4()), user_id=user_b, gateway=gateway
        )
    assert str(mismatch.value) == str(missing.value)
    assert calls["transcript"] == 0
    assert _memory_count(scope) == before

    # 正向对照：显式 owner 与省略 user_id 都能走通（空 transcript 直接返回 []）。
    assert (
        user_memory.extract_user_memories(connect, conversation_id, user_id=user_a, gateway=gateway)
        == []
    )
    assert user_memory.extract_user_memories(connect, conversation_id, gateway=gateway) == []


class _FakeGateway:
    """可计数的正常 gateway：chat 返回一条合法抽取项，embed 返回 2048 维向量。"""

    def __init__(self) -> None:
        self.chat_calls = 0
        self.embed_calls = 0

    def chat(self, messages, **kwargs):
        self.chat_calls += 1
        return SimpleNamespace(
            content='[{"kind": "preference", "content": "喜欢黑咖啡"}]', model="fake-model"
        )

    def embed(self, texts, model=None):
        self.embed_calls += 1
        return [[0.0] * 2048 for _ in texts]


def _seed_conversation_with_transcript(scope: Scope, owner: str) -> str:
    """conversation + run + 一条 user_message 事件：transcript 非空，流程必经网络调用。"""
    with connect() as conn, conn.transaction():
        conversation_id = str(
            conn.execute(
                """INSERT INTO conversations(tenant_id, organization_id, user_id, agent_id)
                   VALUES (%s,%s,%s,'p0a-agent') RETURNING conversation_id""",
                (scope.tenant_id, scope.organization_id, owner),
            ).fetchone()[0]
        )
        run_id = str(
            conn.execute(
                """INSERT INTO runs(conversation_id, user_id, agent_id, status)
                   VALUES (%s,%s,'p0a-agent','completed') RETURNING run_id""",
                (conversation_id, owner),
            ).fetchone()[0]
        )
        conn.execute(
            """INSERT INTO run_events(run_id, sequence_number, event_type, payload)
               VALUES (%s,1,'user_message','{"text": "我喜欢黑咖啡"}')""",
            (run_id,),
        )
    return conversation_id


def test_extract_rechecks_owner_in_write_transaction(users):
    """网络调用后、INSERT 前，写事务内 FOR SHARE 复核 owner：被换掉即 LookupError 且零写。"""
    scope, user_a, user_b = users
    conversation_id = _seed_conversation_with_transcript(scope, user_a)
    gateway = _FakeGateway()
    before = _memory_count(scope)

    # 第二次 connect（写阶段）yield 前，用另一连接把 conversation owner 改为 user_b 并提交，
    # 模拟网络调用窗口内的并发 owner 变更。
    calls = {"n": 0}

    @contextmanager
    def flipping_factory():
        calls["n"] += 1
        if calls["n"] == 2:
            with connect() as other, other.transaction():
                other.execute(
                    "UPDATE conversations SET user_id=%s WHERE conversation_id=%s",
                    (user_b, conversation_id),
                )
        with connect() as conn:
            yield conn

    with pytest.raises(LookupError, match="conversation 不存在或与调用者不匹配"):
        user_memory.extract_user_memories(
            flipping_factory, conversation_id, user_id=user_a, gateway=gateway
        )
    # 流程确实走到了网络调用（transcript/chat/embed 都发生过），但写事务零插入。
    assert gateway.chat_calls == 1
    assert gateway.embed_calls == 1
    assert _memory_count(scope) == before


def test_update_delete_bind_canonical_ids(users):
    """urn/无连字符等非规范 UUID 表示：update/delete 的 SQL 绑定 canonical uuid.UUID。"""
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        memory_id = _insert_memory(conn, user_b)
        rec = RecordingConn(conn)
        updated = user_memory.update_user_memory(
            rec, f"urn:uuid:{memory_id}", content="B 自己修订", owner_user_id=user_b.replace("-", "")
        )
        assert updated["content"] == "B 自己修订"
        user_memory.delete_user_memory(
            rec, memory_id.replace("-", ""), owner_user_id=f"urn:uuid:{user_b}"
        )

        flat = rec.bound_params()
        assert f"urn:uuid:{memory_id}" not in flat
        assert memory_id.replace("-", "") not in flat
        assert user_b.replace("-", "") not in flat
        assert f"urn:uuid:{user_b}" not in flat
        assert uuid.UUID(memory_id) in flat
        assert uuid.UUID(user_b) in flat


def test_extract_invalid_conversation_id_generic_before_sql(users):
    """extract：非法 conversation_id 与不存在同文 LookupError，任何 SQL/网络前失败。"""
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        rec = RecordingConn(conn)

        @contextmanager
        def borrowed(autocommit: bool = False):
            yield rec

        calls_before = len(rec.calls)
        with pytest.raises(LookupError) as invalid:
            user_memory.extract_user_memories(
                borrowed, "not-a-uuid", user_id=user_a, gateway=_ExplodingGateway()
            )
        assert len(rec.calls) == calls_before, "非法 conversation_id 不得产生任何 SQL"
        with pytest.raises(LookupError) as missing:
            user_memory.extract_user_memories(
                borrowed, str(uuid.uuid4()), user_id=user_a, gateway=_ExplodingGateway()
            )
        # 同类同文，不回显非法输入。
        assert str(invalid.value) == str(missing.value)
        assert "not-a-uuid" not in str(invalid.value)
        # 事务未被毒化。
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_update_delete_invalid_ids_generic_and_readable(users):
    """非法 memory_id/owner_user_id 与 missing/foreign 同类同文，事务仍可读、行不变。"""
    scope, user_a, user_b = users
    with connect() as conn, conn.transaction():
        memory_id = _insert_memory(conn, user_b)
        before = _full_row(conn, memory_id)

        messages = []
        for bad_memory_id, bad_owner in (
            ("not-a-uuid", user_b),
            (memory_id, "not-a-uuid"),
        ):
            with pytest.raises(LookupError) as upd:
                user_memory.update_user_memory(
                    conn, bad_memory_id, content="x", owner_user_id=bad_owner
                )
            with pytest.raises(LookupError) as dele:
                user_memory.delete_user_memory(conn, bad_memory_id, owner_user_id=bad_owner)
            messages.extend([str(upd.value), str(dele.value)])

        # 与 foreign 同文，且不回显非法输入。
        with pytest.raises(LookupError) as foreign:
            user_memory.delete_user_memory(conn, memory_id, owner_user_id=user_a)
        assert set(messages) == {str(foreign.value)}
        assert all("not-a-uuid" not in m for m in messages)
        # 事务未被毒化，目标行全行不变。
        assert _full_row(conn, memory_id) == before
