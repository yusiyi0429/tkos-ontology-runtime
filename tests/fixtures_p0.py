"""[Peer C 所有] P0 渲染测试的种子夹具：两条链，一条 confirmed 判断、一条只有 unconfirmed。

全部走 conftest 的 Scope.seed_chain（signal→issue→judgment→confirm 治理路径），零裸 SQL。
"""
from __future__ import annotations

from tests.conftest import Scope


def seed_two_chains(
    scope: Scope,
    conn,
    *,
    confirmed_title: str = "在版判断链",
    unconfirmed_title: str = "未确认判断链",
) -> dict:
    """起两条种子链；title 不同保证两边 judgment statement 文本互不重叠，便于断言。"""
    confirmed = scope.seed_chain(conn, title=confirmed_title, confirmed_judgment=True)
    unconfirmed = scope.seed_chain(conn, title=unconfirmed_title, confirmed_judgment=False)
    return {"confirmed": confirmed, "unconfirmed": unconfirmed}
