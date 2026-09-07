"""Parse ``{{}}`` Working Memory references and resolve their issue-chain binding.

This module owns the pure parser, read-only database resolver, and binding value object.  The TUI
owns completion and conversation state; the assembler owns context and effective-query assembly.

Layers:
- ``parse_inline_refs(text)``：纯文本解析，零 DB / 零 Textual 依赖，表驱动可测；
- ``resolve_message_refs(conn, text, ...)``：显式借用调用方连接 + tenant/org，
  只读调用 ``memory_service.working`` 既有查询（resolve_chain_by_title / list_chains /
  get_chain_scoped / list_chain_current_versions_by_type），不自开连接、无副作用、
  不自行写会话绑定（写回是调用方职责）。

语法 v1（冻结）：
    template := "{{" type [ ":" title ] "}}"   冒号半/全角等价（":" 或 "："取先出现者）
    type     := 六类规范名（大小写不敏感）或中文别名（信号/议题/判断/共识/战略任务/关闭）
    title    := 除 "}}" 外任意文本；第一个冒号是分隔符，其余冒号属 title 原文
    literal  := "\\{{"                          ASCII 反斜杠紧邻 "{{" 时，二者按字面文本，不开模板

Key semantics:
- 普通消息合法：无模板且无绑定 → 成功返回 ``binding=None``，不被业务解析器阻断；
- 绑定刷新：无模板但有绑定 → 按 chain_id + tenant + org 回库刷新 title/status；
  跨 scope 或链已删除 → CHAIN_NOT_FOUND（调用方据此决定清除绑定并引导显式引用）；
- 单消息单链：同消息全部变量解析出的目标链去重后必须 ≤ 1，否则 MULTIPLE_CHAINS；
- ``object_types`` 按 ``working.OBJECT_TYPE_ORDER`` canonical 顺序返回；
  多类型组合不按出现顺序抢占；
- ``missing_object_types``：引用类型在该链上当前版为空（W1 警告的数据源）；
- 同 title 重复 refs 只查一次库（去重后解析）；
- 错误结构只存纯文本 message/candidates，不拼 Rich markup（展示层职责）。
"""
from __future__ import annotations

from dataclasses import dataclass

from memory_service import working
from memory_service.working import OBJECT_TYPE_ORDER

_OPEN = "{{"
_CLOSE = "}}"
_EXAMPLE_EXPLICIT = "{{Signal:主线名}}"  # 错误文案示例（普通字符串，避开 f-string 花括号转义）
_MAX_CANDIDATES = 10

# 错误码（纯字符串常量；RefError.code 与之比较）
ERR_UNCLOSED = "UNCLOSED"
ERR_EMPTY = "EMPTY"
ERR_NESTED = "NESTED"
ERR_UNKNOWN_TYPE = "UNKNOWN_TYPE"
ERR_EMPTY_TITLE = "EMPTY_TITLE"
ERR_UNBOUND_SESSION = "UNBOUND_SESSION"
ERR_CHAIN_NOT_FOUND = "CHAIN_NOT_FOUND"
ERR_MULTIPLE_CHAINS = "MULTIPLE_CHAINS"

# 六类规范名的 casefold 查表（规范名单一事实源 = working.OBJECT_TYPE_ORDER）
_CANONICAL_LOOKUP: dict[str, str] = {t.casefold(): t for t in OBJECT_TYPE_ORDER}

# 中文别名 → 规范名（解析侧输入便利；补全回插只产规范英文名，别名只进不出）
TYPE_ALIASES: dict[str, str] = {
    "信号": "Signal",
    "议题": "Issue",
    "判断": "Judgment",
    "共识": "Agreement",
    "战略任务": "Strategic Mission",
    "关闭": "Close",
}

_TYPE_LOOKUP: dict[str, str] = {**_CANONICAL_LOOKUP, **TYPE_ALIASES}


# ---------------------------------------------------------------------------
# 值对象（frozen dataclass：绑定与解析结果是不可变事实，由调用方决定是否采纳）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InlineRef:
    """文本中的一个 `{{...}}` 引用。"""

    raw: str                      # 模板原文，含花括号
    object_type: str              # 归一后的规范类型名（六类词表成员）
    chain_title: str | None       # 显式主线名（strip 后）；None = 绑定形式 {{类型}}
    span: tuple[int, int]         # 在原文中的 [start, end)，end 指向 "}}" 之后


@dataclass(frozen=True)
class RefError:
    """解析/解析链失败。message/candidates 为纯文本，Rich markup 由展示层添加。

    ``code`` is one of the stable module-level ``ERR_*`` constants.
    """

    code: str
    message: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParseOutcome:
    """纯 parser 输出。error 非 None 时 refs 为出错前已解析的引用（仅供诊断）。"""

    refs: tuple[InlineRef, ...] = ()
    error: RefError | None = None


@dataclass(frozen=True)
class ChainBinding:
    """会话绑定的议题链值对象（TUI 内存态的数据形状；resolver 不持有、不修改它）。"""

    chain_id: str
    title: str
    status: str                   # open / monitoring / closed


@dataclass(frozen=True)
class MessageResolution:
    """一条消息的完整解析结果。

    - binding：本消息生效的议题链（显式解析或绑定刷新所得）；普通无模板消息为 None。
      是否把它写回会话绑定由调用方决定（resolver 无副作用）。
    - object_types：本消息引用的类型，按 canonical 顺序去重。
    - missing_object_types：object_types 中该链上当前版为空的类型（W1 警告数据源）。
    """

    binding: ChainBinding | None
    object_types: tuple[str, ...] = ()
    refs: tuple[InlineRef, ...] = ()
    missing_object_types: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 纯 parser：零 DB / 零 Textual
# ---------------------------------------------------------------------------


def _snippet(text: str, start: int, limit: int = 20) -> str:
    frag = text[start : start + limit]
    suffix = "…" if start + limit < len(text) else ""
    return frag + suffix


def _first_colon(content: str) -> int:
    """content 中第一个半角/全角冒号的位置；都没有返回 -1。"""
    positions = [p for p in (content.find(":"), content.find("：")) if p != -1]
    return min(positions) if positions else -1


def _parse_content(content: str) -> tuple[str, str | None] | RefError:
    """解析模板内容 → (规范类型, 主线名|None)；失败返回 RefError。

    校验顺序固定：EMPTY → NESTED → UNKNOWN_TYPE → EMPTY_TITLE（首错即返）。
    """
    if not content.strip():
        return RefError(ERR_EMPTY, f"空模板：请补全对象类型，如 {_EXAMPLE_EXPLICIT}")
    if _OPEN in content:
        return RefError(
            ERR_NESTED, f"内联模板嵌套：模板内容中不应再出现 {_OPEN}（「{_snippet(content, content.find(_OPEN))}」）"
        )

    colon_idx = _first_colon(content)
    if colon_idx == -1:
        type_token, title = content, None
    else:
        type_token, title = content[:colon_idx], content[colon_idx + 1 :].strip()

    key = type_token.strip().casefold()
    canonical = _TYPE_LOOKUP.get(key)
    if canonical is None:
        return RefError(
            ERR_UNKNOWN_TYPE,
            f"未知对象类型「{type_token.strip()}」。可用类型：{' / '.join(OBJECT_TYPE_ORDER)}。",
            candidates=tuple(OBJECT_TYPE_ORDER),
        )
    if colon_idx != -1 and not title:
        return RefError(
            ERR_EMPTY_TITLE,
            f"「{_OPEN}{canonical}:{_CLOSE}」冒号后缺主线名；或删去冒号，使用当前会话绑定的议题链",
        )
    return canonical, title


def parse_inline_refs(text: str) -> ParseOutcome:
    """扫描文本中的 `{{...}}` 内联模板（含 `\\{{` 字面转义），返回首个错误或全部引用。"""
    refs: list[InlineRef] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\" and text.startswith(_OPEN, i + 1):
            i += 1 + len(_OPEN)  # 字面转义：`\{{` 三个字符都按普通文本跳过，不开模板
            continue
        if not text.startswith(_OPEN, i):
            i += 1
            continue
        end = text.find(_CLOSE, i + len(_OPEN))
        if end == -1:
            return ParseOutcome(
                refs=tuple(refs),
                error=RefError(ERR_UNCLOSED, f"内联模板未闭合：缺少 {_CLOSE}（起自「{_snippet(text, i)}」）"),
            )
        content = text[i + len(_OPEN) : end]
        parsed = _parse_content(content)
        if isinstance(parsed, RefError):
            return ParseOutcome(refs=tuple(refs), error=parsed)
        canonical, title = parsed
        refs.append(
            InlineRef(
                raw=text[i : end + len(_CLOSE)],
                object_type=canonical,
                chain_title=title,
                span=(i, end + len(_CLOSE)),
            )
        )
        i = end + len(_CLOSE)
    return ParseOutcome(refs=tuple(refs), error=None)


# ---------------------------------------------------------------------------
# DB resolver：显式借用 conn，只读，无副作用
# ---------------------------------------------------------------------------


def _binding_from_row(row: dict) -> ChainBinding:
    return ChainBinding(chain_id=str(row["chain_id"]), title=row["title"], status=row["status"])


def _chain_candidates(conn, *, tenant_id: str, organization_id: str) -> tuple[tuple[str, ...], int]:
    """当前 tenant/org 内的链 title 候选（scope 隔离；截断到 _MAX_CANDIDATES，返回 (候选, 总数)）。

    ``working.list_chains`` currently materializes the scope before this display-only truncation.
    """
    rows = working.list_chains(conn, tenant_id=tenant_id, organization_id=organization_id)
    titles = tuple(r["title"] for r in rows)
    return titles[:_MAX_CANDIDATES], len(titles)


def _candidates_text(titles: tuple[str, ...], total: int) -> str:
    if not titles:
        return "当前范围内没有可选的议题链。"
    shown = "、".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
    if total > len(titles):
        return f"候选议题链：{shown} …等 {total} 条"
    return f"候选议题链：{shown}"


def _stale_binding_error(bound: ChainBinding, titles: tuple[str, ...], total: int) -> RefError:
    return RefError(
        ERR_CHAIN_NOT_FOUND,
        "已绑定的议题链（chain "
        f"{bound.chain_id}，原主线名「{bound.title}」）在当前范围内不存在或已删除，绑定已失效。"
        + _candidates_text(titles, total),
        candidates=titles,
    )


def resolve_message_refs(
    conn,
    text: str,
    *,
    tenant_id: str,
    organization_id: str,
    bound: ChainBinding | None,
) -> MessageResolution | RefError:
    """把一条消息解析为议题链绑定 + 引用类型；失败返回 RefError（纯文本）。

    连接契约：conn 由调用方借用给本函数（同一事务内完成全部只读查询），
    本函数不开连接、不提交、不回滚、不写任何表。会话绑定的写回是调用方职责。

    分支：
    1. 纯 parser 失败 → 原样返回错误（E4-E8）；
    2. 无模板 + 无绑定 → 成功，binding=None（普通聊天不被阻断）；
    3. 无模板 + 有绑定 → 按 chain_id + scope 回库刷新 title/status；
    4. 显式变量：title 去重后逐个 resolve_chain_by_title；未命中 → CHAIN_NOT_FOUND；
    5. 全部变量目标链去重 > 1 → MULTIPLE_CHAINS（单消息单链）；
    6. 仅绑定形式变量：用本消息显式链（混合形式），否则刷新后的绑定链；都没有 → UNBOUND_SESSION；
    7. 对 object_types（canonical 序）逐类查当前版，空 → missing_object_types（W1）。
    """
    outcome = parse_inline_refs(text)
    if outcome.error is not None:
        return outcome.error

    if not outcome.refs:
        if bound is None:
            return MessageResolution(binding=None)
        refreshed = working.get_chain_scoped(
            conn, chain_id=bound.chain_id, tenant_id=tenant_id, organization_id=organization_id
        )
        if refreshed is None:
            titles, total = _chain_candidates(conn, tenant_id=tenant_id, organization_id=organization_id)
            return _stale_binding_error(bound, titles, total)
        return MessageResolution(binding=_binding_from_row(refreshed))

    # 显式变量：同 title（lower 口径，与 uq_wm_chains_title 的 lower 匹配一致）只查一次库
    resolved_by_title: dict[str, dict] = {}
    for ref in outcome.refs:
        if ref.chain_title is None:
            continue
        key = ref.chain_title.lower()
        if key in resolved_by_title:
            continue
        row = working.resolve_chain_by_title(
            conn, tenant_id=tenant_id, organization_id=organization_id, title=ref.chain_title
        )
        if row is None:
            titles, total = _chain_candidates(conn, tenant_id=tenant_id, organization_id=organization_id)
            return RefError(
                ERR_CHAIN_NOT_FOUND,
                f"未找到议题链「{ref.chain_title}」。可复制候选名，用 {_EXAMPLE_EXPLICIT} 显式引用重发。"
                + _candidates_text(titles, total),
                candidates=titles,
            )
        resolved_by_title[key] = row

    chain_rows = list(resolved_by_title.values())
    distinct_ids = {r["chain_id"] for r in chain_rows}
    if len(distinct_ids) > 1:
        first = chain_rows[0]
        second = next(r for r in chain_rows[1:] if r["chain_id"] != first["chain_id"])
        return RefError(
            ERR_MULTIPLE_CHAINS,
            "一条消息只能引用一条议题链：本条同时出现「"
            f"{first['title']}」与「{second['title']}」。请只保留一条链的引用后重发。",
            candidates=(first["title"], second["title"]),
        )

    if chain_rows:
        chain_row = chain_rows[0]
    else:
        # 只有绑定形式变量：优先本消息显式链（上面已排除——此分支即无显式变量）
        if bound is None:
            titles, total = _chain_candidates(conn, tenant_id=tenant_id, organization_id=organization_id)
            return RefError(
                ERR_UNBOUND_SESSION,
                f"尚未绑定议题链，{_OPEN}对象类型{_CLOSE} 无法解析；请用 {_EXAMPLE_EXPLICIT} 显式选择。"
                + _candidates_text(titles, total),
                candidates=titles,
            )
        chain_row = working.get_chain_scoped(
            conn, chain_id=bound.chain_id, tenant_id=tenant_id, organization_id=organization_id
        )
        if chain_row is None:
            titles, total = _chain_candidates(conn, tenant_id=tenant_id, organization_id=organization_id)
            return _stale_binding_error(bound, titles, total)

    referenced = {r.object_type for r in outcome.refs}
    object_types = tuple(t for t in OBJECT_TYPE_ORDER if t in referenced)  # canonical 序，非出现序
    missing = tuple(
        t for t in object_types if not working.list_chain_current_versions_by_type(conn, chain_row["chain_id"], t)
    )
    return MessageResolution(
        binding=_binding_from_row(chain_row),
        object_types=object_types,
        refs=outcome.refs,
        missing_object_types=missing,
    )


# ---------------------------------------------------------------------------
# 补全候选查询（inline completion 新增，只读，不改任何既有契约函数）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainCandidate:
    """补全候选的议题链（inline completion 新增）。

    与 ChainBinding 结构相同（chain_id/title/status）但语义不同：ChainCandidate 是
    待选展示项，ChainBinding 是已采纳的会话绑定态——不混用同一类型，避免把候选误当
    绑定（或反之）。
    """

    chain_id: str
    title: str
    status: str  # open / monitoring / closed（TUI 用它标注 closed 只读）


def list_chain_candidates(
    conn,
    *,
    tenant_id: str,
    organization_id: str,
    prefix: str = "",
    limit: int = 10,
) -> tuple[ChainCandidate, ...]:
    """补全用只读候选（inline completion 新增，不改既有契约函数）。

    内部只调 working.list_chains（不自写查询），在 Python 侧按 prefix（casefold
    startswith）过滤 + limit 截断；scope 由 tenant/org 隔离。prefix 为空返回该 scope
    全部链（截断到 limit）。前缀过滤在服务返回的 scope 内完成。
    """
    rows = working.list_chains(conn, tenant_id=tenant_id, organization_id=organization_id)
    p = prefix.casefold()
    out: list[ChainCandidate] = []
    for r in rows:
        title = r["title"]
        if p and not title.casefold().startswith(p):
            continue
        out.append(ChainCandidate(chain_id=str(r["chain_id"]), title=title, status=r["status"]))
        if len(out) >= limit:
            break
    return tuple(out)


__all__ = [
    "OBJECT_TYPE_ORDER",
    "TYPE_ALIASES",
    "InlineRef",
    "RefError",
    "ParseOutcome",
    "ChainBinding",
    "MessageResolution",
    "ChainCandidate",
    "parse_inline_refs",
    "resolve_message_refs",
    "list_chain_candidates",
    # 错误码
    "ERR_UNCLOSED",
    "ERR_EMPTY",
    "ERR_NESTED",
    "ERR_UNKNOWN_TYPE",
    "ERR_EMPTY_TITLE",
    "ERR_UNBOUND_SESSION",
    "ERR_CHAIN_NOT_FOUND",
    "ERR_MULTIPLE_CHAINS",
]
