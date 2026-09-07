"""共享夹具（冻结文件，主会话所有）：隔离 scope + human confirmer + WM 治理种子链。

纪律（每个 peer 的测试都必须遵守）：
1. 一切测试数据落在随机 tenant/org 的隔离 scope，绝不读写 local/local-org；
2. WM 种子只走 memory_service.working 的治理写路径，禁止裸 SQL 写 wm_*；
3. users 表是 workspace 拥有的身份表，沿用 workspsce tests/conftest.py 的惯例直接插行；
4. 用例结束按 scope 清理（wm 表 + 测试用户），不留残留。
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from memory_service import working


def _database_url() -> str:
    """Require the same explicit database URL used by the adapter process."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL 未设置")
    return url


DATABASE_URL = _database_url()


def connect():
    """给夹具与测试用的裸连接（autocommit=False；测试自行 with conn.transaction()）。"""
    return psycopg.connect(DATABASE_URL)


class Scope:
    """隔离 scope：随机 tenant/org + 一个 human 用户 + 可复用的 WM 种子链。"""

    def __init__(self) -> None:
        self.tenant_id = f"adapter-test-{uuid.uuid4().hex[:8]}"
        self.organization_id = f"adapter-org-{uuid.uuid4().hex[:8]}"
        self.user_id: str | None = None
        self._seeded = False

    # -- 身份（users 表直插，workspsce 测试同款惯例） --------------------------
    def ensure_human(self, conn) -> str:
        if self.user_id is None:
            row = conn.execute(
                """INSERT INTO users(tenant_id, organization_id, kind, display_name)
                   VALUES (%s, %s, 'human', %s)
                   RETURNING user_id""",
                (self.tenant_id, self.organization_id, f"适配器测试人类-{self.tenant_id}"),
            ).fetchone()
            self.user_id = str(row[0])
        return self.user_id

    # -- WM 种子链（全部走治理写路径） ---------------------------------------
    def seed_chain(self, conn, *, title: str, confirmed_judgment: bool = True) -> dict:
        """signal → issue(potential) → judgment →（可选）human confirm 一条龙。

        返回 {"chain","signal","issue","judgment"}，其中 signal/issue 带 object_id、
        judgment 带 record_id（confirmed_judgment=True 时已是 confirmed）。
        """
        human = self.ensure_human(conn)
        chain = working.create_chain_with_first_signal(
            conn,
            title=title,
            signal_content={"title": f"{title} 的信号", "description": "adapter 测试种子信号"},
            created_by=human,
            tenant_id=self.tenant_id,
            organization_id=self.organization_id,
        )
        issue = working.form_issue(
            conn,
            chain_id=chain["chain_id"],
            signal_object_ids=[chain["signal"]["object_id"]],
            key_question=f"{title} 应该怎么走？",
            rationale="adapter 测试种子",
            created_by=human,
        )
        judgment = working.create_judgment(
            conn,
            chain_id=chain["chain_id"],
            issue_id=issue["object_id"],
            statement=f"关于 {title} 的测试判断",
            responsible_party="测试责任人",
            created_by=human,
        )
        if confirmed_judgment:
            working.confirm_judgment(conn, judgment["record_id"], confirmed_by=human)
        self._seeded = True
        return {"chain": chain, "signal": chain["signal"], "issue": issue, "judgment": judgment}

    # -- 清理（按 scope，幂等） ----------------------------------------------
    def cleanup(self) -> None:
        if not self._seeded and self.user_id is None:
            return
        with connect() as conn, conn.transaction():
            t, o = self.tenant_id, self.organization_id
            _chains = """SELECT chain_id FROM wm_issue_chains
                          WHERE tenant_id=%s AND organization_id=%s"""
            _records = """SELECT v.record_id FROM wm_object_versions v
                           JOIN wm_objects b ON b.object_id = v.object_id
                           WHERE b.chain_id IN ({c})""".format(c=_chains)
            conn.execute(
                """DELETE FROM wm_version_source_refs
                   WHERE chain_id IN ({c})""".format(c=_chains),
                (t, o),
            )
            conn.execute(
                "DELETE FROM wm_agreement_confirmations WHERE agreement_record_id IN ({r})".format(
                    r=_records
                ),
                (t, o),
            )
            conn.execute(
                "DELETE FROM wm_agreement_parties WHERE agreement_record_id IN ({r})".format(
                    r=_records
                ),
                (t, o),
            )
            conn.execute(
                "DELETE FROM wm_issue_signals WHERE chain_id IN ({c})".format(c=_chains),
                (t, o),
            )
            conn.execute(
                "DELETE FROM wm_object_versions WHERE object_id IN "
                "(SELECT object_id FROM wm_objects WHERE chain_id IN ({c}))".format(c=_chains),
                (t, o),
            )
            conn.execute(
                "DELETE FROM wm_objects WHERE chain_id IN ({c})".format(c=_chains), (t, o)
            )
            conn.execute(
                "DELETE FROM wm_issue_chains WHERE tenant_id=%s AND organization_id=%s", (t, o)
            )
            conn.execute(
                "DELETE FROM users WHERE tenant_id=%s AND organization_id=%s", (t, o)
            )


@pytest.fixture
def scope():
    """隔离 scope 夹具：用例结束自动清理。用法见 docs/peer-plan.md 各任务书。"""
    s = Scope()
    try:
        yield s
    finally:
        s.cleanup()
