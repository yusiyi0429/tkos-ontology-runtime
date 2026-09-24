"""治理事务的锁等待与语句超时：等 scope 栅栏超过上限就返回可重试的 503，不无限挂着。

需要迁移所有者身份经 bootstrap 播种 scope，所以标 owner，只在迁移所有者那一轮跑。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from memory_service_runtime.governed import bootstrap, db
from memory_service_runtime.governed.errors import GovernedError

pytestmark = [pytest.mark.db, pytest.mark.owner]


@pytest.fixture
def seeded():
    db._close_pool()
    label = uuid4().hex[:12]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        fixture = bootstrap.seed_scope(conn, f"runtime-acceptance-timeouts-{label}",
                                       f"runtime-acceptance-timeouts-company-{label}")
    yield fixture
    db._close_pool()


def _hold_fence(scope_id: str) -> psycopg.Connection:
    """另一个会话拿住这家公司的 scope 行锁，模拟一个慢事务。"""
    holder = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
    db.set_write_capability(holder)
    holder.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope_id,))
    assert holder.execute("SELECT 1 FROM gov_scopes WHERE scope_id=%s FOR UPDATE", (scope_id,)).fetchone()
    return holder


def _lines(caplog, event: str) -> list[dict]:
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "tkos.runtime"]
    return [line for line in lines if line["event"] == event]


def test_a_request_waiting_on_a_held_scope_fence_gives_up_with_a_retryable_503(seeded, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tkos.runtime")
    monkeypatch.setenv("GOVERNED_LOCK_TIMEOUT_MS", "300")
    holder = _hold_fence(seeded["scope_id"])
    try:
        started = time.monotonic()
        with pytest.raises(GovernedError) as caught:
            with db.transaction(seeded["actors"]["ceo"]["token"]):
                pass
        waited = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert (caught.value.code, caught.value.status) == ("EVIDENCE_UNAVAILABLE", 503)
    assert waited < 3
    [line] = _lines(caplog, "governed_transaction")
    assert line["outcome"] == "EVIDENCE_UNAVAILABLE" and line["lock_wait_ms"] is None


def test_a_second_waiter_in_the_queue_is_bounded_by_one_lock_timeout(seeded, monkeypatch):
    """PostgreSQL 的 lock_timeout 按每次取锁计：排在别人后面等行锁，要先等元组锁、再等持锁事务，
    最多是两倍。栅栏的整条 FOR UPDATE 必须合起来受 GOVERNED_LOCK_TIMEOUT_MS 约束。"""
    monkeypatch.setenv("GOVERNED_LOCK_TIMEOUT_MS", "1000")
    token = seeded["actors"]["ceo"]["token"]
    outcomes: dict[str, tuple[str, float]] = {}

    def wait(name: str) -> None:
        started = time.monotonic()
        try:
            with db.transaction(token):
                outcomes[name] = ("committed", time.monotonic() - started)
        except GovernedError as exc:
            outcomes[name] = (exc.code, time.monotonic() - started)

    holder = _hold_fence(seeded["scope_id"])
    try:
        first = threading.Thread(target=wait, args=("first",))
        first.start()
        time.sleep(0.3)
        second = threading.Thread(target=wait, args=("second",))
        second.start()
        first.join(10)
        second.join(10)
    finally:
        holder.rollback()
        holder.close()
    assert outcomes["first"][0] == outcomes["second"][0] == "EVIDENCE_UNAVAILABLE"
    assert outcomes["second"][1] < 1.6, outcomes


def test_a_statement_over_the_statement_timeout_is_a_retryable_503(seeded, monkeypatch):
    monkeypatch.setenv("GOVERNED_STATEMENT_TIMEOUT_MS", "200")
    with pytest.raises(GovernedError) as caught:
        with db.transaction(seeded["actors"]["ceo"]["token"]) as (conn, _):
            conn.execute("SELECT pg_sleep(2)")
    assert (caught.value.code, caught.value.status) == ("EVIDENCE_UNAVAILABLE", 503)


def test_a_committed_transaction_records_pool_wait_lock_wait_and_hold(seeded, caplog):
    caplog.set_level(logging.INFO, logger="tkos.runtime")
    with db.transaction(seeded["actors"]["ceo"]["token"]) as (conn, ctx):
        assert conn.execute("SELECT current_setting('lock_timeout') AS v").fetchone()["v"] == "5s"
        assert conn.execute("SELECT current_setting('statement_timeout') AS v").fetchone()["v"] == "30s"
    [line] = _lines(caplog, "governed_transaction")
    assert line["outcome"] == "committed" and line["scope_id"] == seeded["scope_id"]
    assert all(isinstance(line[key], float) for key in ("pool_wait_ms", "lock_wait_ms", "held_ms"))

