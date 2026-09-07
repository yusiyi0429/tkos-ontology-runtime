"""[Peer C 所有] P0 render_static 行为测试。

覆盖（任务书 §3 Peer C）：unconfirmed 判断绝不出现；confirmed 出现且含日期；
同库两次渲染逐字节相同；viewer 名字出现；无链时空骨架不抛异常；
viewer 非 scope 内 human 时 fail-closed。
"""
from __future__ import annotations

import re
import uuid

import pytest

from tests.conftest import Scope, connect
from tests.fixtures_p0 import seed_two_chains

from adapter.render_static import render_static
from adapter.wm_views import build_code_index

from memory_service.actors import HumanActorRequiredError


def _render(scope: Scope, conn, **kwargs):
    return render_static(
        conn,
        viewer_user_id=kwargs.pop("viewer_user_id", scope.user_id),
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        **kwargs,
    )


def test_confirmed_appears_with_date_unconfirmed_never_appears():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            seeded = seed_two_chains(s, conn)
            out = _render(s, conn)
            index = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            confirmed_code = next(
                entry.code
                for entry in index.entries
                if entry.object_id == seeded["confirmed"]["judgment"]["object_id"]
            )

            assert out.startswith("【公司经营状态】")
            confirmed_stmt = f"关于 {seeded['confirmed']['chain']['title']} 的测试判断"
            assert re.search(
                rf"· {re.escape(confirmed_code)} {re.escape(confirmed_stmt)}"
                rf"（\d{{4}}-\d{{2}}-\d{{2}}）",
                out,
            ), out
            unconfirmed_stmt = f"关于 {seeded['unconfirmed']['chain']['title']} 的测试判断"
            assert unconfirmed_stmt not in out, "unconfirmed 判断泄漏进 P0 输出"
    finally:
        s.cleanup()


def test_render_is_byte_identical_across_calls():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            seed_two_chains(s, conn)
            first = _render(s, conn)
            second = _render(s, conn)
            assert first == second
            # as_of 只进尾部台账行，且同为调用参数：同参两次调用仍逐字节相同
            first_asof = _render(s, conn, as_of="2026-08-31T00:00:00Z")
            second_asof = _render(s, conn, as_of="2026-08-31T00:00:00Z")
            assert first_asof == second_asof
            assert first_asof.rstrip().endswith("台账：as_of=2026-08-31T00:00:00Z")
    finally:
        s.cleanup()


def test_viewer_display_name_appears():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            seed_two_chains(s, conn)
            out = _render(s, conn)
            assert f"viewer：适配器测试人类-{s.tenant_id}" in out, out
    finally:
        s.cleanup()


def test_empty_scope_renders_valid_skeleton():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            s.ensure_human(conn)  # 只有人、没有链
            out = _render(s, conn)
            assert out.startswith("【公司经营状态】")
            assert "## 在版判断（confirmed）" in out
            assert "（暂无在版判断）" in out
            assert f"viewer：适配器测试人类-{s.tenant_id}" in out
    finally:
        s.cleanup()


def test_viewer_fail_closed_when_not_in_scope_human():
    """viewer 不是隔离 scope 内的 human 时 fail-closed。

    users 表有全局 agent_service 单例约束（0002_identity_uniq.sql），测试 scope 内
    造不出第二个 agent_service 行；absence / 越界 / 非 human 在
    memory_service.actors.require_actor 里走同一异常，以不存在用户路径覆盖。
    """
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            seed_two_chains(s, conn)
            with pytest.raises(HumanActorRequiredError):
                _render(s, conn, viewer_user_id=str(uuid.uuid4()))  # 不存在的用户
    finally:
        s.cleanup()
