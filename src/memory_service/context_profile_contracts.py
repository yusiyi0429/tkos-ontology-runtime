"""Typed contracts for Working Memory Context Profiles.

This module contains the closed vocabulary, errors, strict profile models, immutable section views,
pack values, budget audits, and effective-query results.  SQL belongs to the repository, narrative
rendering to the renderer, and orchestration to ``context_profile``.

``working_contracts.OBJECT_TYPE_ORDER`` is the sole canonical order.  Profile fields are limited to
values consumed by rendering or query augmentation.  All Pydantic models forbid unknown fields and are
frozen; sequence fields reject duplicates, and the default YAML is cached by path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
import pathlib
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from memory_service.working_contracts import OBJECT_TYPE_ORDER

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class ContextProfileError(Exception):
    """Context Profile 服务错误基类。"""


class ProfileConfigError(ContextProfileError):
    """Profile YAML 缺失/解析失败/严格校验失败。"""


class InvalidRequestedTypesError(ContextProfileError):
    """请求的对象类型不在六类封闭词表内（含 Research Task / Research Result）。"""


class ChainScopeError(ContextProfileError):
    """链不存在，或不属于请求声明的 tenant + organization。

    错误信息刻意不区分“不存在”与“scope 不匹配”，也不携带链的真实 tenant/org，
    防止跨租户调用借错误信息探测链归属。
    """


# ---------------------------------------------------------------------------
# Profile 配置契约（严格校验 + 消费方明确）
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """全部配置模型的公共基类：未知键拒绝、实例冻结。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EffectiveQueryConfig(_StrictModel):
    """effective-query 扩充的可调参数（全部被 augment_effective_query 消费）。

    - object_types：允许参与扩充的对象类型，⊆ {Signal, Issue}；
    - signal_fields / issue_fields：参与拼接的业务字段（按序）；
    - max_added_chars：新增上下文（换行分隔符 + 前缀 + 各条目 + 结尾句号）的
      独立字符上限，防止大量 Signal 淹没原查询；预算覆盖完整新增段。

    字段一律使用 tuple。Pydantic frozen 只禁止字段赋值，tuple 才能保证缓存后的
    配置集合不会被调用方原位修改。
    """

    object_types: tuple[str, ...]
    signal_fields: tuple[str, ...] = ("title", "description")
    issue_fields: tuple[str, ...] = ("key_question", "rationale")
    max_added_chars: int = Field(default=1200, gt=0)

    @field_validator("object_types")
    @classmethod
    def _no_dup_object_types(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("effective_query.object_types 不能为空")
        if len(set(v)) != len(v):
            raise ValueError(f"effective_query.object_types 不允许重复：{v}")
        return v

    @field_validator("signal_fields", "issue_fields")
    @classmethod
    def _no_dup_fields(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v:
            raise ValueError("字段列表不能为空")
        if len(set(v)) != len(v):
            raise ValueError(f"字段列表不允许重复：{v}")
        return v


class WorkingMemoryProfileConfig(_StrictModel):
    """Working Memory Context Profile 配置。

    profile_id 和 name 进入 ContextProfilePack 元数据。character_budget 是叙事默认
    预算，effective_query 的全部字段都由 augment_effective_query 消费。六档语义固定在
    repository，确认状态展示属于不变量，不能由配置关闭。
    """

    profile_id: str
    name: str
    character_budget: int = Field(gt=0)
    effective_query: EffectiveQueryConfig


PROFILES_DIR = pathlib.Path(__file__).resolve().parent / "profiles"
DEFAULT_PROFILE_PATH = PROFILES_DIR / "working_memory_v1.yaml"

_EFFECTIVE_QUERY_ELIGIBLE: frozenset[str] = frozenset({"Signal", "Issue"})


@lru_cache(maxsize=None)
def _load_cached(path_str: str) -> WorkingMemoryProfileConfig:
    """Cache parsed frozen configuration by its resolved path."""
    p = pathlib.Path(path_str)
    if not p.exists():
        raise ProfileConfigError(f"Working Memory Profile 不存在：{p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ProfileConfigError(f"Profile YAML 解析失败：{p}: {e}") from e
    if not isinstance(raw, dict):
        raise ProfileConfigError(f"Profile 配置必须是映射（dict）类型：{p}")
    try:
        cfg = WorkingMemoryProfileConfig.model_validate(raw)
    except ValidationError as e:
        raise ProfileConfigError(f"Profile 配置校验失败：{p}:\n{e}") from e

    eq = cfg.effective_query.object_types
    unknown_eq = [t for t in eq if t not in _EFFECTIVE_QUERY_ELIGIBLE]
    if unknown_eq:
        raise ProfileConfigError(
            f"effective_query.object_types 只允许 Signal / Issue，非法项：{unknown_eq}"
        )
    return cfg


def load_working_memory_profile(
    path: pathlib.Path | str | None = None,
) -> WorkingMemoryProfileConfig:
    """加载 Working Memory Context Profile（默认配置走缓存）。"""
    p = pathlib.Path(path) if path is not None else DEFAULT_PROFILE_PATH
    return _load_cached(str(p.resolve()))


def normalize_requested_types(raw, *, allow_empty: bool) -> tuple[str, ...]:
    """规范化请求类型：拒词表外类型（含 Research Task/Result），去重后按 canonical 排序。"""
    seen: list[str] = []
    unknown: list[str] = []
    for t in raw:
        if t not in OBJECT_TYPE_ORDER:
            if t not in unknown:
                unknown.append(t)
            continue
        if t not in seen:
            seen.append(t)
    if unknown:
        raise InvalidRequestedTypesError(
            f"对象类型必须在六类封闭词表内（{list(OBJECT_TYPE_ORDER)}）；"
            f"非法项：{unknown}。Research Task / Research Result 不是合法对象类型"
        )
    ordered = tuple(t for t in OBJECT_TYPE_ORDER if t in seen)
    if not ordered and not allow_empty:
        raise InvalidRequestedTypesError("requested_object_types 至少包含一个合法对象类型")
    return ordered


# ---------------------------------------------------------------------------
# typed 请求 / 视图 / pack（frozen dataclass）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextProfileRequest:
    """分级召回请求：链 + scope + 本消息显式请求的对象类型集合。

    requested_object_types 允许任意顺序传入（例如消息里的出现顺序），构建 pack
    时一律规范化为 canonical OBJECT_TYPE_ORDER——顺序敏感性只属于 canonical
    顺序，不属于消息出现顺序，不能采用 first-wins。
    """

    chain_id: str
    tenant_id: str
    organization_id: str
    requested_object_types: tuple[str, ...]


@dataclass(frozen=True)
class SourceRefView:
    """单条来源引用的解析视图（只读投影 + 快照校验结果）。

    resolution 取值：resolved / hash_and_excerpt_mismatch。解析只校验服务自持
    冻结摘录的 SHA-256 完整性，不读取 distill 的实时片段表。
    """

    ordinal: int
    fragment_id: str
    document_id: str | None
    filename: str | None
    locator: str
    chunk_index: int | None
    fragment_ordinal: int | None
    excerpt_snapshot: str
    content_hash_snapshot: str
    resolution: str
    resolution_note: str


@dataclass(frozen=True)
class VersionView:
    """一个版本行的 typed 视图：业务内容 + 确认状态 + 来源解析。

    content 是 jsonb 业务负载（dict），属于数据不属于契约形状；其余字段全部定型。
    """

    record_id: str
    object_id: str
    object_type: str
    version: int
    supersedes: str | None
    confirmation_status: str
    confirmed_by: str | None
    confirmed_at: str | None
    created_by: str
    issue_state: str | None
    confirmed_judgment_record_id: str | None
    agreement_record_id: str | None
    content: dict[str, Any] = field(default_factory=dict)
    source_refs: tuple[SourceRefView, ...] = ()


@dataclass(frozen=True)
class SignalSection:
    """Signal 档：同链全部 Signal 稳定对象，各带当前版（稳定对象 created_at 升序）。"""

    signals: tuple[VersionView, ...] = ()


@dataclass(frozen=True)
class IssueSection:
    """Issue 档：该 Issue 完整版本链 + current/latest/latest-confirmed 标记与状态。"""

    issue_object_id: str | None = None
    versions: tuple[VersionView, ...] = ()
    current_record_id: str | None = None
    current_state: str | None = None
    latest_confirmed_record_id: str | None = None


@dataclass(frozen=True)
class JudgmentSection:
    """Judgment 档：同链 Judgment 完整版本链，高亮最新 confirmed 版。"""

    judgment_object_id: str | None = None
    versions: tuple[VersionView, ...] = ()
    latest_confirmed_record_id: str | None = None


@dataclass(frozen=True)
class PartyView:
    """Agreement 当前版本的确认参与人：名单成员 + 该版本上的逐人确认状态。"""

    party_id: str
    display_name: str
    confirmed_at: str | None
    confirmation_note: str | None


@dataclass(frozen=True)
class AgreementSection:
    """Agreement 档：Judgment 全版本链 + Agreement 当前版 + 确切 confirmed Judgment
    版本引用 + parties/confirmations（多人确认的完整展开）。

    judgment 子结构即使链上尚无 Agreement 也保留（renderer 需要先渲染判断演进，
    再说明"尚无共识"，）。
    """

    judgment: JudgmentSection = field(default_factory=JudgmentSection)
    agreement_object_id: str | None = None
    current: VersionView | None = None
    confirmed_judgment_record_id: str | None = None
    confirmed_judgment_version: int | None = None
    confirmation_complete: bool = False
    parties: tuple[PartyView, ...] = ()


@dataclass(frozen=True)
class MissionSection:
    """Mission 档：最新 Strategic Mission + Issue/Agreement 最小承接引用。

    最小承接包含 Issue 当前版完整 VersionView（key_question/确认状态/来源锚点，
    ），不只给状态。
    """

    mission_object_id: str | None = None
    current: VersionView | None = None
    issue_current: VersionView | None = None
    agreement_record_id: str | None = None
    agreement_version: int | None = None
    agreement_statement: str | None = None


@dataclass(frozen=True)
class CloseSection:
    """Close 档：最新 Close（含关闭原因）+ Issue 当前版 + Agreement 引用。"""

    close_object_id: str | None = None
    current: VersionView | None = None
    reason: str | None = None
    issue_current: VersionView | None = None
    agreement_record_id: str | None = None
    agreement_version: int | None = None
    agreement_statement: str | None = None


@dataclass(frozen=True)
class SourceResolutionAudit:
    """pack 级来源解析审计：每条引用只计一次（按 record_id+fragment_id+ordinal 去重）。"""

    refs_checked: int = 0
    resolved: int = 0
    mismatched: int = 0
    missing: int = 0
    details: tuple[tuple[str, str, str], ...] = ()  # (record_id, fragment_id, resolution)


@dataclass(frozen=True)
class ContextProfilePack:
    """分级召回的结构化结果（审计/调试用；叙事是它的渲染投影）。"""

    profile_id: str
    profile_name: str
    chain_id: str
    tenant_id: str
    organization_id: str
    chain_title: str
    chain_status: str
    requested_object_types: tuple[str, ...]  # 已规范为 canonical 顺序
    missing_object_types: tuple[str, ...]    # 请求了但链上不存在的类型
    character_budget_default: int
    signal_section: SignalSection | None = None
    issue_section: IssueSection | None = None
    judgment_section: JudgmentSection | None = None
    agreement_section: AgreementSection | None = None
    mission_section: MissionSection | None = None
    close_section: CloseSection | None = None
    source_resolution_audit: SourceResolutionAudit = field(default_factory=SourceResolutionAudit)


# ---------------------------------------------------------------------------
# 叙事预算审计与渲染结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NarrativeBudget:
    """叙事预算审计（绝不进入模型可见文本）。

    - included_blocks/truncated_blocks/dropped_blocks 按 canonical 顺序记录块级结果；
    - included_entries/dropped_entries 记录条目级结果（entry 标识 = 对象类型:record_id
      或块级摘要行的固定标识）；
    - truncated=True 时 len(text) == used <= limit 恒成立。
    """

    limit: int
    used: int
    untruncated_length: int
    truncated: bool
    included_blocks: tuple[str, ...] = ()
    truncated_blocks: tuple[str, ...] = ()
    dropped_blocks: tuple[str, ...] = ()
    included_entries: tuple[str, ...] = ()
    dropped_entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedNarrative:
    text: str
    budget: NarrativeBudget


@dataclass(frozen=True)
class EffectiveQueryResult:
    """effective-query 扩充结果：只拼本消息显式引用的 Signal/Issue 当前版业务字段。

    used_record_ids 只含实际进入 augmented_query 的版本行（canonical 顺序）；
    truncated 表示新增上下文是否被 max_added_chars 截断。无显式 Signal/Issue
    引用时 augmented_query == base_query、used 为空（只有绑定时绝不擅自扩 query）。
    """

    base_query: str
    augmented_query: str
    used_object_types: tuple[str, ...]
    used_record_ids: tuple[str, ...]
    truncated: bool = False


def iso_or_none(value: Any) -> str | None:
    """datetime → ISO 字符串；None 透传；其余 str() 化。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
