"""Narrative renderer for typed Working Memory Context Packs.

The renderer has no SQL or retrieval dependency.  It renders version history and confirmation
state without claiming that an unconfirmed Close has closed an Issue.  Each entry keeps its
confirmation suffix when body text is truncated.  Mandatory entries are budgeted before optional
history, with Issue context taking priority.  A core entry that cannot fit is dropped as a whole
rather than emitted as a misleading partial object.

Model-visible text is built only from business fields, so retrieval scores and budget metadata stay
in the audit value while legitimate business text remains untouched.  Document filenames/ids are not
rendered into this text —— the model cannot act on them (no source-anchor-driven re-read), and the
frozen source_refs remain fully available on the structured pack for audit.
"""
from __future__ import annotations

from dataclasses import dataclass

from memory_service.context_profile_contracts import (
    AgreementSection,
    CloseSection,
    ContextProfilePack,
    IssueSection,
    JudgmentSection,
    MissionSection,
    NarrativeBudget,
    RenderedNarrative,
    SignalSection,
    VersionView,
)

_TRUNCATION_MARKER = "……（内容已截断）"


# ---------------------------------------------------------------------------
# 条目：正文 + 不可分离的尾巴（确认状态）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    """一个可独立计费的渲染条目。

    body 是业务字段文本（超预算时可安全截断）；tail 承载确认状态与来源锚点。
    只要条目出现，tail 就必须完整出现，不能输出缺确认状态的半个对象。
    note 型条目（关系说明等非对象行）tail 可为空。
    """

    entry_id: str
    body: str
    tail: str = ""

    @property
    def full(self) -> str:
        return self.body + self.tail

    def truncated_body(self, body_budget: int) -> str:
        return self.body[:body_budget] + _TRUNCATION_MARKER


@dataclass(frozen=True)
class _Block:
    """一个类型块：intro（块首行）+ entries。

    - core_ids：本档位  核心语义的最小 entry 集合（mandatory）。预算保底
      阶段先为它预留；放不下则整块 dropped。
    - core_truncatable：core 是否允许截断正文（仅 Signal——信号描述是开放自由
      文本，截断片段仍有提示意义；决策性语句如 Issue 关键问题/判断/共识必须完整）。
    - placeholder_only：只有 intro、无 entries（档位缺失的诚实占位）。
    """

    object_type: str
    intro: str
    entries: tuple[_Entry, ...]
    core_ids: frozenset[str] = frozenset()
    core_truncatable: bool = False
    placeholder_only: bool = False


# ---------------------------------------------------------------------------
# 业务字段提取与尾巴构造
# ---------------------------------------------------------------------------


def _conf_label(status: str) -> str:
    return "已确认" if status == "confirmed" else "未确认"


def _c(content: dict, key: str) -> str:
    v = content.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else ""


def _required(content: dict, key: str, missing_label: str) -> str:
    """Render required business content honestly when persisted history is malformed."""
    return _c(content, key) or f"（未记载{missing_label}）"


def _disposition_text(content: dict) -> str:
    disposition = _c(content, "disposition")
    labels = {"strategic_mission": "进入战略任务", "close": "关闭议题"}
    return labels.get(disposition, "（未记载或非法）")


def _version_tail(v: VersionView) -> str:
    return f"（{_conf_label(v.confirmation_status)}）"


def _version_markers(v: VersionView, *, current_id: str | None,
                     latest_confirmed_id: str | None) -> str:
    marks: list[str] = []
    if current_id is not None and v.record_id == current_id:
        marks.append("当前版")
    if latest_confirmed_id is not None and v.record_id == latest_confirmed_id:
        marks.append("最新已确认版")
    return f"（{'、'.join(marks)}）" if marks else ""


# ---------------------------------------------------------------------------
# 六档 → 块（每个 builder 指定 core_ids： 核心语义的保底集合）
# ---------------------------------------------------------------------------


def _signal_block(sec: SignalSection) -> _Block:
    entries = tuple(
        _Entry(
            entry_id=f"Signal:{v.record_id}",
            body=(
                f"· 「{_required(v.content, 'title', '标题')}」"
                f"{_required(v.content, 'description', '描述')}"
            ),
            tail=_version_tail(v),
        )
        for v in sec.signals
    )
    core_ids = frozenset({entries[0].entry_id}) if entries else frozenset()
    return _Block(
        object_type="Signal",
        intro=f"该议题链当前积累的信号，共 {len(sec.signals)} 条：",
        entries=entries,
        core_ids=core_ids,          # 一条信号即最小完整语义；其余信号 optional
        core_truncatable=True,      # 信号描述可截断（正文截断、尾巴保留）
        placeholder_only=not sec.signals,
    )


def _issue_block(sec: IssueSection) -> _Block:
    if sec.issue_object_id is None or not sec.versions:
        return _Block("Issue", "该议题链尚未形成稳定议题。", (), placeholder_only=True)
    intro = f"围绕该链已形成稳定议题，版本链共 {len(sec.versions)} 版，当前状态：{sec.current_state}。"
    entries = tuple(
        _Entry(
            entry_id=f"Issue:{v.record_id}",
            body=(
                f"· 第{v.version}版（议题状态：{v.issue_state}）："
                f"关键问题：{_required(v.content, 'key_question', '关键问题')}"
                + (f"；理由：{_c(v.content, 'rationale')}" if _c(v.content, "rationale") else "")
                + (f"；状态说明：{_c(v.content, 'transition_note')}" if _c(v.content, "transition_note") else "")
                + _version_markers(
                    v, current_id=sec.current_record_id,
                    latest_confirmed_id=sec.latest_confirmed_record_id,
                )
            ),
            tail=_version_tail(v),
        )
        for v in sec.versions
    )
    core_ids = {
        f"Issue:{rid}"
        for rid in (sec.current_record_id, sec.latest_confirmed_record_id)
        if rid is not None
    }
    return _Block(
        "Issue", intro, entries,
        core_ids=core_ids,          # 当前版 + 最新已确认版 = 核心语义；历史版本 optional
    )


def _judgment_block(sec: JudgmentSection) -> _Block:
    if sec.judgment_object_id is None or not sec.versions:
        return _Block("Judgment", "该议题链尚无组织判断记录。", (), placeholder_only=True)
    current_id = sec.versions[-1].record_id
    entries = tuple(
        _Entry(
            entry_id=f"Judgment:{v.record_id}",
            body=(
                f"· 第{v.version}版判断：{_required(v.content, 'statement', '判断内容')}"
                f"（责任人：{_required(v.content, 'responsible_party', '责任人')}）"
                + _version_markers(
                    v, current_id=current_id,
                    latest_confirmed_id=sec.latest_confirmed_record_id,
                )
            ),
            tail=_version_tail(v),
        )
        for v in sec.versions
    )
    core_ids = {
        f"Judgment:{rid}"
        for rid in (current_id, sec.latest_confirmed_record_id)
        if rid is not None
    }
    return _Block(
        "Judgment", f"该议题的组织判断演进，共 {len(sec.versions)} 版：", entries,
        core_ids=core_ids,          # 当前判断 + 最新已确认判断 = 核心语义；历史 optional
    )


def _agreement_block(sec: AgreementSection) -> _Block:
    """Agreement 档 = Judgment 演进 + Agreement 当前版 + 精确引用 + 多人确认。

    core_ids：
    - Agreement 存在 → 共识当前版 + 确切 Judgment ref + parties（Judgment 历史 optional，
      避免历史吃满预算把真正的共识/精确引用/确认人全丢）；
    - Agreement 缺失、Judgment 存在 → 当前判断 + 最新已确认判断（核心），
      配"尚无共识确认记录"intro；
    - 两者都缺失 → placeholder，intro 同时诚实表达两种缺失（不可拆语义）。
    """
    jb = _judgment_block(sec.judgment)
    agreement_absent = sec.current is None
    core_ids: set[str] = set(jb.core_ids)
    if jb.placeholder_only:
        # judgment 缺失 ⟹ agreement 必然也缺失：同时诚实表达两种缺失，且两句都在 intro
        intro = "该议题链尚无组织判断记录。该议题链尚无共识确认记录。"
        entries: list[_Entry] = list(jb.entries)
        placeholder = True
    else:
        intro = ("该议题链尚无共识确认记录。" if agreement_absent else "") + jb.intro
        entries = list(jb.entries)
        placeholder = False

    if not agreement_absent:
        v = sec.current
        ag_entry = _Entry(
            entry_id=f"Agreement:{v.record_id}",
            body=(
                f"共识确认（当前第{v.version}版）：{_required(v.content, 'statement', '共识内容')}"
                f"处置意图：{_disposition_text(v.content)}。"
            ),
            tail=_version_tail(v),
        )
        entries.append(ag_entry)
        core_ids.add(ag_entry.entry_id)
        if sec.confirmed_judgment_version is not None:
            ref_entry = _Entry(
                f"Agreement:{v.record_id}:judgment_ref",
                f"该共识指向确切已确认的判断版本：第{sec.confirmed_judgment_version}版。",
            )
            entries.append(ref_entry)
            core_ids.add(ref_entry.entry_id)
        if sec.parties:
            names = "、".join(p.display_name for p in sec.parties)
            confirmed_names = "、".join(p.display_name for p in sec.parties if p.confirmed_at is not None)
            done = len([p for p in sec.parties if p.confirmed_at is not None])
            parties_entry = _Entry(
                f"Agreement:{v.record_id}:parties",
                f"确认名单：{names}；已完成确认：{confirmed_names or '暂无'}（{done}/{len(sec.parties)}）。",
            )
            entries.append(parties_entry)
            core_ids.add(parties_entry.entry_id)
    return _Block("Agreement", intro, tuple(entries), core_ids=frozenset(core_ids),
                  placeholder_only=placeholder)


def _mission_block(sec: MissionSection) -> _Block:
    if sec.mission_object_id is None or sec.current is None:
        return _Block("Strategic Mission", "该议题链尚无战略任务。", (), placeholder_only=True)
    v = sec.current
    entries: list[_Entry] = [
        _Entry(
            entry_id=f"Strategic Mission:{v.record_id}",
            body=(
                f"战略任务（当前第{v.version}版）：{_required(v.content, 'title', '任务名')}"
                f"。预期结果：{_required(v.content, 'outcome', '预期结果')}"
                f"。负责人：{_required(v.content, 'owner', '负责人')}"
                f"。边界：{_required(v.content, 'boundary', '边界')}"
            ),
            tail=_version_tail(v),
        )
    ]
    if sec.agreement_record_id:
        ref = f"承接已确认的共识确认版本：第{sec.agreement_version}版。" if sec.agreement_version else "承接已确认的共识确认。"
        if sec.agreement_statement:
            ref += f"共识内容：{sec.agreement_statement}。"
        entries.append(_Entry(f"Strategic Mission:{v.record_id}:agreement_ref", ref))
    if sec.issue_current is not None:
        iv = sec.issue_current
        entries.append(
            _Entry(
                entry_id=f"Strategic Mission:issue:{iv.record_id}",
                body=(
                    f"所挂议题（当前第{iv.version}版，状态：{iv.issue_state}）："
                    f"{_required(iv.content, 'key_question', '关键问题')}"
                ),
                tail=_version_tail(iv),
            )
        )
    # Mission 档 = 最新 Mission + Issue/Agreement 最小承接：全部是核心语义，
    # Never keep the body while dropping its minimum supporting refs.
    return _Block("Strategic Mission", "", tuple(entries),
                  core_ids=frozenset(e.entry_id for e in entries))


def _close_block(sec: CloseSection) -> _Block:
    if sec.close_object_id is None or sec.current is None:
        return _Block("Close", "该议题链尚无关闭记录。", (), placeholder_only=True)
    v = sec.current
    detail = (
        f"关闭原因：{sec.reason or _required(v.content, 'reason', '关闭原因')}"
        f"。关键前提：{_required(v.content, 'assumptions', '关键前提')}"
        f"。持续监测：{_required(v.content, 'monitor', '监测项')}"
        f"。重开条件：{_required(v.content, 'reopen_condition', '重开条件')}"
    )
    entries: list[_Entry]
    if v.confirmation_status != "confirmed":
        # An unconfirmed Close is only a pending closure proposal; the chain remains open.
        entries = [
            _Entry(
                entry_id=f"Close:{v.record_id}",
                body=f"关闭提案（提案第{v.version}版，待人工确认）：{detail}",
                tail=_version_tail(v),
            )
        ]
        state_note = f"（议题当前状态：{sec.issue_current.issue_state}）" if sec.issue_current else ""
        entries.append(
            _Entry(f"Close:{v.record_id}:pending", f"该议题链尚未关闭：关闭需人工确认后生效{state_note}。")
        )
    else:
        entries = [
            _Entry(
                entry_id=f"Close:{v.record_id}",
                body=f"议题已关闭（关闭记录第{v.version}版）：{detail}",
                tail=_version_tail(v),
            )
        ]
    if sec.agreement_record_id:
        ref = f"承接已确认的共识确认版本：第{sec.agreement_version}版。" if sec.agreement_version else "承接已确认的共识确认。"
        if sec.agreement_statement:
            ref += f"共识内容：{sec.agreement_statement}。"
        entries.append(_Entry(f"Close:{v.record_id}:agreement_ref", ref))
    if sec.issue_current is not None:
        iv = sec.issue_current
        entries.append(
            _Entry(
                entry_id=f"Close:issue:{iv.record_id}",
                body=(
                    f"议题当前为第{iv.version}版，状态：{iv.issue_state}："
                    f"{_required(iv.content, 'key_question', '关键问题')}"
                ),
                tail=_version_tail(iv),
            )
        )
    # Close 档 = 最新 Close + Issue 当前版 + Agreement + 关闭原因：全部核心语义
    return _Block("Close", "", tuple(entries),
                  core_ids=frozenset(e.entry_id for e in entries))


_BLOCK_BUILDERS = {
    "Signal": _signal_block,
    "Issue": _issue_block,
    "Judgment": _judgment_block,
    "Agreement": _agreement_block,
    "Strategic Mission": _mission_block,
    "Close": _close_block,
}


def _section_of(pack: ContextProfilePack, object_type: str):
    return {
        "Signal": pack.signal_section,
        "Issue": pack.issue_section,
        "Judgment": pack.judgment_section,
        "Agreement": pack.agreement_section,
        "Strategic Mission": pack.mission_section,
        "Close": pack.close_section,
    }[object_type]


# ---------------------------------------------------------------------------
# 组装与预算（两阶段保底：core 预留 → optional 填充）
# ---------------------------------------------------------------------------


def _blocks_for_pack(pack: ContextProfilePack) -> list[_Block]:
    blocks: list[_Block] = []
    for t in pack.requested_object_types:  # 已是 canonical 顺序
        section = _section_of(pack, t)
        if section is None:
            continue
        blocks.append(_BLOCK_BUILDERS[t](section))
    return blocks


def _assemble(blocks: list[_Block], header: str) -> str:
    parts: list[str] = [header]
    parts.extend(x for b in blocks for x in ([b.intro] if b.intro else []) + [e.full for e in b.entries])
    return "\n".join(parts)


@dataclass(frozen=True)
class _BudgetState:
    text: str
    included_blocks: tuple[str, ...]
    truncated_blocks: tuple[str, ...]
    dropped_blocks: tuple[str, ...]
    included_entries: tuple[str, ...]
    dropped_entries: tuple[str, ...]


def _core_cost(b: _Block) -> int:
    """core 保底成本：intro + 各 core entry。

    Signal 的 core 允许截断，只预留"最小可呈现"（换行 + 标记 + 尾巴 + 至少 1 字符正文）；
    其余类型 core 必须完整（决策性语句截断即失去语义）。
    """
    cost = (1 + len(b.intro)) if b.intro else 0
    for e in b.entries:
        if e.entry_id not in b.core_ids:
            continue
        if b.core_truncatable:
            cost += 1 + len(_TRUNCATION_MARKER) + len(e.tail) + 1
        else:
            cost += 1 + len(e.full)
    return cost


def _apply_budget(header: str, blocks: list[_Block], limit: int) -> _BudgetState:
    """两阶段预算：core 保底 → optional 填充。

    Phase A（core 选择，跨 blocks）：按优先级（Issue 优先——议题本体不被内部
    上下文淹没，其余按 canonical）为每个请求块预留 core 成本；某块 core 放不下
    则整块 dropped，绝不输出误导性半档。
    Phase B（输出）：canonical 块序 + 块内自然叙事序。每块维护自己的 core 预留
    res（intro + core 条目）：非 Signal core 从 res 内完整输出（放不下则防御性
    丢弃，Phase A 已保证放得下）；Signal core 用 res 保底（至少截断+尾巴）并
    允许用共享 pool 扩容——Signal 的 pool 消费只会挤占其他块的 optional，绝不
    侵蚀其他块的 core 预留。optional（历史/Signals）从 pool 贪心填充（正文可
    截断、尾巴保留）。

    恒有 len(text) <= limit；部分块（有条目被丢或正文被截断）同时进
    included_blocks 与 truncated_blocks。
    """
    if limit <= 0:
        raise ValueError(f"character_budget 必须为正整数，实际：{limit}")

    # header（纯文本，可硬截断）
    if len(header) >= limit:
        return _BudgetState(
            text=header[:limit],
            included_blocks=(),
            truncated_blocks=(),
            dropped_blocks=tuple(b.object_type for b in blocks),
            included_entries=(),
            dropped_entries=tuple(e.entry_id for b in blocks for e in b.entries),
        )

    # ---- Phase A：跨 blocks core 保底/选择
    by_type = {b.object_type: b for b in blocks}
    priority = ["Issue"] + [
        t for t in ("Signal", "Judgment", "Agreement", "Strategic Mission", "Close")
        if t in by_type
    ]
    core_cost = {b.object_type: _core_cost(b) for b in blocks}
    sim = limit - len(header)
    active: set[str] = set()
    for t in priority:
        if t not in by_type:
            continue
        if sim >= core_cost[t]:
            active.add(t)
            sim -= core_cost[t]
    pool = sim  # optional 填充预算（跨 block 共享，canonical 顺序消耗）

    # ---- Phase B：canonical 顺序输出（每块独立 core 预留 res）
    lines: list[str] = [header]
    used = len(header)
    included_blocks: list[str] = []
    truncated_blocks: list[str] = []
    dropped_blocks: list[str] = []
    included_entries: list[str] = []
    dropped_entries: list[str] = []

    for b in blocks:
        t = b.object_type
        if t not in active:
            dropped_blocks.append(t)
            dropped_entries.extend(e.entry_id for e in b.entries)
            continue
        res = core_cost[t]
        if b.placeholder_only:
            lines.append(b.intro)
            used += 1 + len(b.intro)
            res -= 1 + len(b.intro)
            included_blocks.append(t)
            continue
        if b.intro:
            lines.append(b.intro)
            used += 1 + len(b.intro)
            res -= 1 + len(b.intro)
        emitted: list[str] = []
        dropped_in: list[str] = []
        any_trunc = False
        for e in b.entries:
            if e.entry_id in b.core_ids:
                if not b.core_truncatable:
                    # 决策性 core：从本块预留内完整输出（Phase A 已保证放得下）
                    cost = 1 + len(e.full)
                    if res >= cost:
                        lines.append(e.full)
                        used += cost
                        res -= cost
                        emitted.append(e.entry_id)
                    else:
                        dropped_in.append(e.entry_id)  # 防御：不应发生
                    continue
                # Signal core：res 保底 + pool 扩容（不侵蚀其他块 core 预留）
                full_cost = 1 + len(e.full)
                avail = res + pool
                if avail >= full_cost:
                    from_res = min(res, full_cost)
                    from_pool = full_cost - from_res
                    lines.append(e.full)
                    used += full_cost
                    res -= from_res
                    pool -= from_pool
                    emitted.append(e.entry_id)
                    continue
                min_cost = 1 + len(_TRUNCATION_MARKER) + len(e.tail) + 1
                if avail >= min_cost:
                    body_budget = avail - 1 - len(_TRUNCATION_MARKER) - len(e.tail)
                    text = e.truncated_body(body_budget) + e.tail
                    lines.append(text)
                    used += 1 + len(text)
                    res = 0
                    pool = 0
                    emitted.append(e.entry_id)
                    any_trunc = True
                    continue
                dropped_in.append(e.entry_id)
                continue
            # optional：pool 内贪心（可截断，尾巴保留）
            cost = 1 + len(e.full)
            if pool >= cost:
                lines.append(e.full)
                used += cost
                pool -= cost
                emitted.append(e.entry_id)
                continue
            if pool >= 1 + len(_TRUNCATION_MARKER) + len(e.tail) + 1:
                body_budget = pool - 1 - len(_TRUNCATION_MARKER) - len(e.tail)
                text = e.truncated_body(body_budget) + e.tail
                lines.append(text)
                used += 1 + len(text)
                pool -= 1 + len(text)
                emitted.append(e.entry_id)
                any_trunc = True
                continue
            dropped_in.append(e.entry_id)
        included_blocks.append(t)
        included_entries.extend(emitted)
        dropped_entries.extend(dropped_in)
        if dropped_in or any_trunc:
            truncated_blocks.append(t)

    return _BudgetState(
        text="\n".join(lines),
        included_blocks=tuple(included_blocks),
        truncated_blocks=tuple(dict.fromkeys(truncated_blocks)),
        dropped_blocks=tuple(dropped_blocks),
        included_entries=tuple(included_entries),
        dropped_entries=tuple(dropped_entries),
    )


def render_model_context(
    pack: ContextProfilePack, *, character_budget: int | None = None
) -> RenderedNarrative:
    """把 typed pack 渲染为朴素业务叙事（模型可见文本）。

    - 多类型按 canonical OBJECT_TYPE_ORDER 组合（pack.requested_object_types 已
      规范化），共享统一字符预算：两阶段保底（core 预留 → optional 填充），
      core 放不下的档位整块 dropped；正文可截断、确认状态与来源锚点永不缺席；
    - 任意正 limit 保证 len(text) <= limit；截断标记不携带预算数字，预算细节
      只进 NarrativeBudget 审计；
    - 正文只来自 typed 视图的业务字段，构造上不投影 score/rank/depth/distance/budget。
    """
    limit = character_budget if character_budget is not None else pack.character_budget_default
    if limit is None or limit <= 0:
        raise ValueError(f"character_budget 必须为正整数，实际：{limit}")

    header = (
        f"以下是与当前工作相关的议题链「{pack.chain_title}」的经营背景。"
        f"议题链当前状态：{pack.chain_status}。"
    )
    blocks = _blocks_for_pack(pack)
    full = _assemble(blocks, header)
    state = _apply_budget(header, blocks, limit)
    return RenderedNarrative(
        text=state.text,
        budget=NarrativeBudget(
            limit=limit,
            used=len(state.text),
            untruncated_length=len(full),
            truncated=len(state.text) < len(full),
            included_blocks=state.included_blocks,
            truncated_blocks=state.truncated_blocks,
            dropped_blocks=state.dropped_blocks,
            included_entries=state.included_entries,
            dropped_entries=state.dropped_entries,
        ),
    )


# ---------------------------------------------------------------------------
# effective-query 新增段（供 facade 拼接；预算覆盖完整新增段）
# ---------------------------------------------------------------------------

_QUERY_PREFIX = "相关业务上下文："


def build_effective_query_suffix(
    entries: list[tuple[str, str, str]],
    *,
    max_added_chars: int,
    needs_newline: bool,
    prefix: str = _QUERY_PREFIX,
) -> tuple[str | None, tuple[str, ...], bool]:
    """把 (entry_id, label, text) 条目组装成 effective-query 的新增段。

    预算覆盖完整新增段（不含原 base_query）：needs_newline 时先计 1 个换行
    分隔符，之后是 prefix + 各条目以句号连接 + 结尾句号；恒有
        len(新增段) = len(\\n? + suffix) <= max_added_chars。
    条目按顺序贪心放入；放不下的整条丢弃（不产生半条）；一条都放不下时返回
    (None, (), truncated=True)。
    返回 (suffix, included_ids, truncated)；suffix=None 表示无任何条目进入。
    """
    budget = max_added_chars - (1 if needs_newline else 0)
    if budget <= 0:
        return None, (), True
    if len(prefix) + 1 > budget:
        return None, (), True
    parts: list[str] = []
    included: list[str] = []
    truncated = False
    for eid, label, text in entries:
        piece = f"{label}：{text}"
        # 直接对候选完整 suffix 计长（前缀 + 各条以句号连接 + 结尾句号），
        # Count the separator here so joined punctuation stays inside the budget.
        candidate = prefix + "。".join(parts + [piece]) + "。"
        if len(candidate) <= budget:
            parts.append(piece)
            included.append(eid)
        else:
            truncated = True
    if not parts:
        return None, (), truncated
    return prefix + "。".join(parts) + "。", tuple(included), truncated
