"""[Peer C 所有] P0 公司经营状态渲染：WM 全 scope confirmed Judgment 汇总 + viewer 身份。

设计见 docs/design.md §D4。CG/CompanyVision 叙事段本期明账不做（主会话裁定，
等图侧读取 API 议定后另发任务）。边界：本模块绝不读写 semantic_* / memory_* 表；
WM 数据一律走 memory_service.working 公开函数；裸 SQL 仅限 users 表 SELECT。

确定性：输出只随库内 confirmed 状态变化——now() 等非确定源不进正文；
as_of 只进尾部台账行（design §D7：接收但仅用于台账，不提供历史重建）。
"""
from __future__ import annotations

from memory_service import working
from memory_service.actors import require_human

from adapter.wm_views import build_code_index

_HEADER = "【公司经营状态】"
_JUDGMENT_SECTION = "## 在版判断（confirmed）"
_EMPTY_SECTION = "（暂无在版判断）"


def _confirmed_judgment_lines(conn, *, tenant_id: str, organization_id: str) -> list[str]:
    """全 scope 逐链取 Judgment 的 latest confirmed 版本，unconfirmed 绝不出现。

    选择语义即 design §D4 的 latest_confirmed_version：某 Judgment 对象即使已有
    unconfirmed 翻版，仍显示其最近一个 confirmed 版本；从未 confirmed 的对象整条跳过。
    """
    index = build_code_index(
        conn, tenant_id=tenant_id, organization_id=organization_id,
    )
    code_by_object = {
        target.object_id: target.code
        for target in index.entries
        if target.kind == "JDG" and target.object_id is not None
    }
    # (confirmed_at, object_id, code, statement)：同一事务内确认的多条判断
    # confirmed_at 同值，用 object_id（UUID）做次序兜底，保证逐字节确定性。
    entries: list[tuple] = []
    for chain in working.list_chains(conn, tenant_id=tenant_id, organization_id=organization_id):
        for current in working.list_chain_current_versions_by_type(conn, chain["chain_id"], "Judgment"):
            confirmed = working.get_latest_confirmed_version(conn, current["object_id"])
            if confirmed is None:
                continue
            object_id = current["object_id"]
            code = code_by_object.get(object_id)
            if code is None:
                raise working.ObjectNotFoundError(f"判断短码索引缺少对象：{object_id}")
            statement = confirmed["content"]["statement"]
            entries.append((confirmed["confirmed_at"], object_id, code, statement))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return [
        f"· {code} {statement}（{confirmed_at:%Y-%m-%d}）"
        for confirmed_at, _oid, code, statement in entries
    ]


def render_static(
    conn,
    *,
    viewer_user_id,
    tenant_id: str,
    organization_id: str,
    as_of: str | None = None,
) -> str:
    """渲染 M7 P0 同形的「公司经营状态」markdown。

    借用调用方连接（不自开、不管事务）。scope 必须由调用方显式注入
    （adapter/settings.py 归 Peer A，占位期明确不可被 import 依赖，故不从 settings 读）。
    viewer 必须是该 scope 内 users.kind='human' 的用户，否则 fail-closed 抛
    HumanActorRequiredError（复用 memory_service.actors 的 canonical 校验）。
    """
    require_human(
        conn,
        viewer_user_id,
        tenant_id=tenant_id,
        organization_id=organization_id,
        field_name="viewer",
    )
    display_name = conn.execute(
        "SELECT display_name FROM users WHERE user_id=%s", (viewer_user_id,),
    ).fetchone()[0]

    lines = [_HEADER, "", _JUDGMENT_SECTION]
    judgment_lines = _confirmed_judgment_lines(
        conn, tenant_id=tenant_id, organization_id=organization_id,
    )
    lines.extend(judgment_lines if judgment_lines else [_EMPTY_SECTION])
    lines.append("")
    lines.append(f"viewer：{display_name}")
    if as_of is not None:
        lines.append(f"台账：as_of={as_of}")
    return "\n".join(lines) + "\n"
