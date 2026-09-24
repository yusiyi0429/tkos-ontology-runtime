"""控制面 status：取上下文每次落一行、只增不删（ADR-0007），status 报出这家公司的行数与占用。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from memory_service_runtime.governed import bootstrap, control

pytestmark = [pytest.mark.db, pytest.mark.owner]


def test_control_status_reports_how_much_the_context_pack_trail_holds(monkeypatch):
    # 迁移所有者这一轮里 DATABASE_URL 就是 owner；控制面按自己的变量名取同一个地址。
    monkeypatch.setenv("MIGRATION_DATABASE_URL", os.environ["DATABASE_URL"])
    label = uuid4().hex[:12]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        seeded = bootstrap.seed_scope(conn, f"runtime-acceptance-status-{label}",
                                      f"runtime-acceptance-status-company-{label}")
    with control._connect() as conn:
        report = control.status(conn, SimpleNamespace(scope_id=seeded["scope_id"], actor="test"))
    assert report["context_packs"] == {"rows": 0, "bytes": 0, "oldest": None, "newest": None}
