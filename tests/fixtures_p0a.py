"""[P0A] 图/身份测试夹具：隔离 scope 种子、内存对象存储与按 scope 清理。

纪律（与 conftest 一致）：
1. 一切测试数据落在随机 tenant/org 的隔离 scope，绝不读写 local/local-org；
2. context_graph_versions / semantic_entities 没有可造 current 代的治理写路径
   （create_generation 只允许 shadow），种子用最小 SQL 直插；
   提案一律走 context_graph.service.propose 治理路径；
3. 用例结束按 scope 清理（图台账 + 测试用户），不留残留。
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from psycopg.types.json import Jsonb

from tests.conftest import Scope, connect


def borrowed_connection(conn):
    """与 ingest._borrowed_connection 相同的借用语义：治理函数复用调用方事务。"""

    @contextmanager
    def connect_factory(autocommit: bool = False):
        yield conn

    return connect_factory


class InMemoryObjectStore:
    """SnapshotObjectStore 协议的内存实现：create-only，满足不可变前置检查。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def preflight_immutability(self) -> None:
        return None

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = data
        return True

    def get(self, key: str) -> bytes:
        return self.objects[key]


class RecordingConn:
    """记录 (sql, params) 执行序列的 conn 代理：同时支持 execute 与 cursor 两种协议。

    用于证明授权动作先于资源读取、以及 canonical UUID 值实际进入 SQL 绑定。
    """

    def __init__(self, conn) -> None:
        self._conn = conn
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql, params=None, **kwargs):
        self.calls.append((str(sql), params))
        return self._conn.execute(sql, params, **kwargs)

    def cursor(self, **kwargs):
        cur = self._conn.cursor(**kwargs)
        calls = self.calls

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                cur.close()
                return False

            def execute(self, sql, params=None):
                calls.append((str(sql), params))
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
        """所有执行的绑定参数拍平为列表（dict 与 tuple/list 两种风格都支持）。"""
        flat: list = []
        for _sql, params in self.calls:
            if params is None:
                continue
            if isinstance(params, dict):
                flat.extend(params.values())
            else:
                flat.extend(params)
        return flat


class GraphRig:
    """一个隔离 scope 的图种子与清理；可派生同 tenant 异 org / 异 tenant 的越权 scope。"""

    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self._extra_scopes: list[tuple[str, str]] = []

    @property
    def tenant_id(self) -> str:
        return self.scope.tenant_id

    @property
    def organization_id(self) -> str:
        return self.scope.organization_id

    # -- 越权 scope 派生 -------------------------------------------------
    def foreign_tenant_scope(self) -> tuple[str, str]:
        pair = (f"p0a-tenant-{uuid.uuid4().hex[:8]}", self.scope.organization_id)
        self._extra_scopes.append(pair)
        return pair

    def foreign_organization_scope(self) -> tuple[str, str]:
        pair = (self.scope.tenant_id, f"p0a-org-{uuid.uuid4().hex[:8]}")
        self._extra_scopes.append(pair)
        return pair

    # -- 种子 ----------------------------------------------------------
    def insert_user(self, conn, *, kind: str = "human", display: str = "p0a-user") -> str:
        row = conn.execute(
            """INSERT INTO users(tenant_id, organization_id, kind, display_name)
               VALUES (%s, %s, %s, %s) RETURNING user_id""",
            (self.tenant_id, self.organization_id, kind, f"{display}-{self.tenant_id}"),
        ).fetchone()
        return str(row[0])

    def insert_user_in(
        self, conn, scope: tuple[str, str], *, kind: str = "human", display: str = "p0a-foreign"
    ) -> str:
        tenant_id, organization_id = scope
        row = conn.execute(
            """INSERT INTO users(tenant_id, organization_id, kind, display_name)
               VALUES (%s, %s, %s, %s) RETURNING user_id""",
            (tenant_id, organization_id, kind, f"{display}-{tenant_id}-{organization_id}"),
        ).fetchone()
        return str(row[0])

    def insert_generation(self, conn, *, label: str, status: str) -> str:
        row = conn.execute(
            """INSERT INTO context_graph_versions(tenant_id, organization_id, label, status)
               VALUES (%s,%s,%s,%s) RETURNING generation_id""",
            (self.tenant_id, self.organization_id, label, status),
        ).fetchone()
        return str(row[0])

    def insert_confirmed_entity(
        self, conn, *, generation_id: str, name: str, confirmed_by: str
    ) -> str:
        row = conn.execute(
            """INSERT INTO semantic_entities
                 (tenant_id, organization_id, entity_type, name, normalized_name, content,
                  revision, status, type_key, confirmed_by, confirmed_at, graph_generation_id)
               VALUES (%s,%s,NULL,%s,%s,'{}',1,'confirmed','CompanyVision',%s,now(),%s)
               RETURNING entity_id""",
            (self.tenant_id, self.organization_id, name, name.lower(),
             confirmed_by, generation_id),
        ).fetchone()
        return str(row[0])

    # -- 清理（按 scope，FK 安全顺序，幂等） ----------------------------
    def cleanup(self) -> None:
        scopes = [(self.tenant_id, self.organization_id), *self._extra_scopes]
        with connect() as conn, conn.transaction():
            for tenant_id, organization_id in scopes:
                scope = (tenant_id, organization_id)
                conn.execute(
                    "DELETE FROM context_graph_snapshots"
                    " WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    """DELETE FROM memory_audit WHERE proposal_id IN (
                           SELECT proposal_id FROM memory_proposals
                           WHERE tenant_id=%s AND organization_id=%s)""",
                    scope,
                )
                conn.execute(
                    "DELETE FROM memory_proposals WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    "DELETE FROM semantic_relation_source_refs"
                    " WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    "DELETE FROM semantic_entity_source_refs"
                    " WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    "DELETE FROM semantic_relations WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    "DELETE FROM semantic_entities WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    "DELETE FROM context_graph_versions"
                    " WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )
                conn.execute(
                    "DELETE FROM users WHERE tenant_id=%s AND organization_id=%s",
                    scope,
                )


__all__ = [
    "GraphRig",
    "InMemoryObjectStore",
    "RecordingConn",
    "borrowed_connection",
]
