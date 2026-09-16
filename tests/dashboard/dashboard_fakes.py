"""无数据库的 dashboard 读取聚焦测试辅助。

运行方式（避免 tests/conftest.py 的 DATABASE_URL 夹具）：

    PYTHONPATH=src pytest tests/dashboard --confcutdir=tests/dashboard

FakeConn 只响应 dashboard.py 中带明确标记/固定形状的查询；对象/修订读取与
协议元数据由 monkeypatch 换成内存判定，从而在无数据库环境下验证分组、
basis、权限隐藏、cursor 绑定和显式缺失状态。
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from memory_service_runtime.governed.db import AuthContext


def uid(value: int) -> str:
    return str(UUID(int=value))


def ts(second: int) -> datetime:
    return datetime(2026, 9, 1, 0, 0, second, tzinfo=timezone.utc)


CTX = AuthContext(scope_id=uid(1000), tenant_id="t", company_id="c",
                  principal_id=uid(1001), principal_type="human", auth_epoch=1,
                  assignments=[{"assignment_id": uid(1002), "domain_id": uid(1), "role": "CEO"}])


def head(object_id: str, object_type: str, domain_id: str | None = None, *,
         latest: str | None = None, effective: str | None = None,
         version: int = 1) -> dict:
    return {
        "object_id": object_id, "domain_id": domain_id or uid(1), "object_type": object_type,
        "lifecycle_status": "active", "object_version": version,
        "latest_revision_id": latest, "effective_revision_id": effective,
        "created_at": ts(1),
    }


def revision(object_id: str, revision_id: str, payload: dict, *, version: int = 1) -> dict:
    return {
        "object_id": object_id, "revision_id": revision_id, "object_version": version,
        "payload": payload, "payload_hash": "a" * 64, "recorded_at": ts(2),
        "recorded_by": uid(1001), "valid_from": ts(2), "valid_to": None, "action_id": uid(2000),
    }


class Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)
