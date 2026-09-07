"""conftest 地基自检（主会话所有）：隔离 scope 种子链 + 治理确认 + 清理闭环。

peer 开工前先跑这个文件；它红说明共享夹具坏了，报主会话而不是自己修 conftest。
"""
from __future__ import annotations

from tests.conftest import Scope, connect

from memory_service import working


def test_seed_chain_confirms_and_cleans_up():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            seeded = s.seed_chain(conn, title="地基自检链", confirmed_judgment=True)
            chain_id = seeded["chain"]["chain_id"]

            # 治理路径断言：链/对象/版本落库，判断已由 human 确认
            row = conn.execute(
                """SELECT v.confirmation_status, u.kind
                     FROM wm_object_versions v
                     JOIN users u ON u.user_id = v.confirmed_by
                    WHERE v.record_id = %s""",
                (seeded["judgment"]["record_id"],),
            ).fetchone()
            assert row is not None, "判断版本未落库"
            assert row[0] == "confirmed"
            assert row[1] == "human", "确认人不是 human——治理不变量被破坏"

            # 读取路径可用（working 公开查询）
            chain = working.get_chain(conn, chain_id)
            assert chain is not None and chain["title"] == "地基自检链"
    finally:
        s.cleanup()

    # 清理闭环断言：scope 内无残留
    with connect() as conn:
        left = conn.execute(
            """SELECT (SELECT count(*) FROM wm_issue_chains
                       WHERE tenant_id=%s AND organization_id=%s)
                     + (SELECT count(*) FROM users
                        WHERE tenant_id=%s AND organization_id=%s)""",
            (s.tenant_id, s.organization_id, s.tenant_id, s.organization_id),
        ).fetchone()[0]
        assert left == 0, f"清理后有残留：{left} 行"
