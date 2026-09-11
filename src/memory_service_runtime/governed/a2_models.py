"""A2 公司组合的严格参数与载荷模型（Contract-A tkos.contract-a/0.1）。

语义对齐冻结契约包 A 的 validator/models.py 与 schemas/（静态形状、排序、唯一性、
hash 方案）。本模块独立于 governed/models.py（不 import，避免循环；信封集成在
下一步完成），运行时不 import 任何包外路径。规范化与摘要复用 governed/canon.py
（tkos-json-v1，与已验收校验器语义一致）。

本模块只做结构与静态不变量校验；不证明真实身份、当前授权、数据库对象存在、
最新完整集合或真实业务可行性——这些由 service 层在持有 scope 栅栏后查权威数据。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)
from typing_extensions import Annotated as TypingAnnotated

from . import canon

A2_ACTIONS = frozenset({
    "open_formation_round",
    "amend_formation_round",
    "publish_domain_submission",
    "form_company_composition",
    "confirm_company_composition",
    "activate_company_composition",
})

# 通用 create_object/propose_revision 对 Contract-A 只放行这两个输入类型。
A2_SOURCE_TYPES = frozenset({"CompanyReference", "CapacityObservation"})
# 只能由 A2 专用动作内部创建/派生，通用写一律拒绝。
A2_COMPOSITION_TYPES = frozenset({
    "FormationRound", "DomainSubmission", "CompanyComposition",
    "Mission", "DomainCommitment",
})
A2_OBJECT_TYPES = A2_SOURCE_TYPES | A2_COMPOSITION_TYPES

MANIFEST_SCHEMA_VERSION = "tkos.composition-manifest/0.1"
HASH_SCHEME = canon.HASH_SCHEME  # tkos-json-v1
CONSTRAINT_KIND_CAPACITY = "capacity"
RELATION_TYPE_RESOURCE_CAPACITY = "resource_capacity"
SOURCE_FRESHNESS_TTL_SECONDS = 86400
# 依赖闭包上限：超限/成环显式拒绝，绝不截断后按成功输出。
CLOSURE_MAX_NODES = 64
CLOSURE_MAX_DEPTH = 8


class CompositionValidationError(ValueError):
    """静态不变量拒绝；code 与冻结校验器 RejectionCode 对齐。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise CompositionValidationError(code, message)


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("all-whitespace string is not allowed")
    return value


NEStr = TypingAnnotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
    AfterValidator(_reject_blank),
]
Sha256Hex = TypingAnnotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = TypingAnnotated[StrictInt, Field(ge=1)]
NonNegativeInt = TypingAnnotated[StrictInt, Field(ge=0)]


def _canonical_uuid(value: str) -> str:
    from uuid import UUID
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid UUID: {value!r}") from exc
    if str(parsed) != value:
        raise ValueError(
            f"non-canonical UUID representation {value!r}; expected {str(parsed)!r}"
        )
    return value


# 只接受小写连字符规范形 UUID，避免摘要输入被模型无声改写（对齐冻结 C05/C14）。
CanonicalUUID = TypingAnnotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    ),
    AfterValidator(_canonical_uuid),
]

_DATETIME_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"


def _valid_datetime(value: str) -> str:
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO datetime: {value!r}") from exc
    return value


IsoDateTime = TypingAnnotated[
    str,
    StringConstraints(pattern=_DATETIME_RE),
    AfterValidator(_valid_datetime),
    Field(json_schema_extra={"format": "date-time"}),
]

_STRICT = ConfigDict(extra="forbid", allow_inf_nan=False)


class StrictModel(BaseModel):
    model_config = _STRICT


# ------------------------------------------------------------------ refs

class RevisionRef(StrictModel):
    object_id: CanonicalUUID
    revision_id: CanonicalUUID


class ObjectRef(StrictModel):
    """正式引用的精确三元组：object_id + revision_id + payload_hash。"""

    object_id: CanonicalUUID
    revision_id: CanonicalUUID
    payload_hash: Sha256Hex


class CompositionRef(StrictModel):
    """组合的精确引用：object + revision + manifest_hash。"""

    object_id: CanonicalUUID
    revision_id: CanonicalUUID
    manifest_hash: Sha256Hex


class ProfileRef(StrictModel):
    profile_id: NEStr
    revision: NEStr
    canonical_hash: Sha256Hex


class ProfileRefSpec(StrictModel):
    """请求侧 Profile 引用；canonical_hash 由服务端从已安装记录核对。"""

    profile_id: NEStr
    revision: NEStr


class PeriodWindow(StrictModel):
    start: IsoDateTime
    end: IsoDateTime

    @model_validator(mode="after")
    def ordered(self) -> "PeriodWindow":
        if datetime.fromisoformat(self.end) <= datetime.fromisoformat(self.start):
            raise ValueError("period window end must be later than start")
        return self


# ------------------------------------------------------- controlled sources

def _unique_domain_ids(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("duplicate domain_id in shared_with_domain_ids")
    return value


SharedDomainIds = TypingAnnotated[
    list[CanonicalUUID], AfterValidator(_unique_domain_ids)
]


class CompanyReferencePayload(StrictModel):
    """公司正式 Reference（受控输入）。shared_with_domain_ids 为发布者显式
    选择的本 scope 域白名单；空 = 私有。"""

    title: NEStr
    statement: NEStr
    period_id: CanonicalUUID
    terms: dict[str, JsonValue] = Field(default_factory=dict)
    shared_with_domain_ids: SharedDomainIds = Field(default_factory=list)
    upstream_refs: list[RevisionRef] = Field(default_factory=list)


class CapacityObservationPayload(StrictModel):
    """容量来源观察（受控输入）。available/reserved 为非负严格整数；
    observed_at 是显式观察时刻（未来时刻在准入时拒绝）。"""

    title: NEStr
    resource_id: CanonicalUUID
    period_id: CanonicalUUID
    unit: NEStr
    available: NonNegativeInt
    reserved: NonNegativeInt
    observed_at: IsoDateTime
    valid_from: IsoDateTime
    valid_to: IsoDateTime | None = None
    note: str = ""
    shared_with_domain_ids: SharedDomainIds = Field(default_factory=list)
    upstream_refs: list[RevisionRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> "CapacityObservationPayload":
        if self.reserved > self.available:
            raise ValueError("reserved must not exceed available")
        if self.valid_to is not None and (
            datetime.fromisoformat(self.valid_to) <= datetime.fromisoformat(self.valid_from)
        ):
            raise ValueError("valid_to must be later than valid_from")
        return self


# --------------------------------------------------------------- submission

class AcceptanceCriterion(StrictModel):
    criterion_id: NEStr
    description: NEStr


class MissionSpec(StrictModel):
    """Submission 内的 Mission 定义；mission_key 稳定，身份由服务端派生。"""

    mission_key: NEStr
    result_statement: NEStr
    boundary: NEStr
    acceptance_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1)]
    # 绑定引用（review 7）：进入有界闭包，不是自由备注。
    dependency_refs: list[RevisionRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct(self) -> "MissionSpec":
        ids = [c.criterion_id for c in self.acceptance_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate acceptance criterion ids")
        refs = [(r.object_id, r.revision_id) for r in self.dependency_refs]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate dependency refs")
        return self


class ResourceDemand(StrictModel):
    """唯一需求量（review 5）；非负严格整数，bool/float/str 一律拒绝。"""

    resource_id: CanonicalUUID
    period_id: CanonicalUUID
    unit: NEStr
    required: NonNegativeInt


class BindingSpec(StrictModel):
    """绑定只标识精确来源/资源/周期/单位，不含第二个需求量（review 5）。"""

    relation_type: Literal["resource_capacity"]
    source_ref: ObjectRef
    resource_id: CanonicalUUID
    period_id: CanonicalUUID
    unit: NEStr


class SubmissionContent(StrictModel):
    """正式 Submission 的结构化内容（review 5）。

    单个 Submission 的 `resources[]` 与 `bindings[]` 各自保持严格类型与池键
    唯一性，但不再要求两份池键一一对应——每个独立域可只发布
    demand-only（仅 `resources[]`）或 provider-only（仅 `bindings[]`）。
    全员正式 Submission 之间的完整池覆盖与每个池键
    `(resource_id, period_id, unit)` 唯一一致的 binding，由 service 层在
    form/activate 阶段跨全部当前有效 Submission 汇总并强制。
    """

    result_statement: NEStr
    pdo: "PdoSpec"
    missions: Annotated[list[MissionSpec], Field(min_length=1)]
    resources: list[ResourceDemand] = Field(default_factory=list)
    bindings: list[BindingSpec] = Field(default_factory=list)
    upstream_refs: list[RevisionRef] = Field(default_factory=list)
    unknowns: list[NEStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> "SubmissionContent":
        keys = [m.mission_key for m in self.missions]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate mission_key in missions")
        demand_pools = [(r.resource_id, r.period_id, r.unit) for r in self.resources]
        if len(set(demand_pools)) != len(demand_pools):
            raise ValueError("duplicate capacity pool in resources")
        binding_pools = [(b.resource_id, b.period_id, b.unit) for b in self.bindings]
        if len(set(binding_pools)) != len(binding_pools):
            raise ValueError("duplicate capacity pool in bindings")
        refs = [(r.object_id, r.revision_id) for r in self.upstream_refs]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate upstream refs")
        return self


class PdoSpec(StrictModel):
    pdo_key: NEStr
    statement: NEStr
    result_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct(self) -> "PdoSpec":
        ids = [c.criterion_id for c in self.result_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate result criterion ids")
        return self


class DomainSubmissionPayload(StrictModel):
    """Submission revision 的存储 payload（review 9）：本人身份/任职/草稿
    检查点/承诺声明与结构化内容一起不可变持久化；hash 覆盖全部、不含派生
    Mission 的 hash。"""

    round_object_id: CanonicalUUID
    domain_id: CanonicalUUID
    dri_assignment_id: CanonicalUUID
    dri_principal_id: CanonicalUUID
    draft_checkpoint: NEStr
    commitment_statement: NEStr
    submission: SubmissionContent


# ------------------------------------------------- derived business objects

class MissionPayload(StrictModel):
    """Mission revision 的存储 payload；origin_submission_ref 是激活选择
    与溯源核对的依据（review 3）。"""

    round_object_id: CanonicalUUID
    domain_id: CanonicalUUID
    mission_key: NEStr
    origin_submission_ref: ObjectRef
    result_statement: NEStr
    boundary: NEStr
    acceptance_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1)]
    dependency_refs: list[RevisionRef] = Field(default_factory=list)


class MissionRef(StrictModel):
    mission_key: NEStr
    object_id: CanonicalUUID
    revision_id: CanonicalUUID


class DomainCommitmentPayload(StrictModel):
    """生效时建立的域承诺：精确引用组合与本域 Submission/Mission 选择。"""

    round_object_id: CanonicalUUID
    domain_id: CanonicalUUID
    composition_ref: CompositionRef
    submission_ref: ObjectRef
    mission_refs: list[MissionRef] = Field(default_factory=list)


# ------------------------------------------------------------------- round

class RoundMember(StrictModel):
    domain_id: CanonicalUUID
    dri_assignment_id: CanonicalUUID
    dri_principal_id: CanonicalUUID


class RoundDefinitionPayload(StrictModel):
    """FormationRound 定义 revision 的存储 payload（不可变；代次与可变头
    分离）。period 与 Profile 在定义间不可变由 service 强制。"""

    company_id: CanonicalUUID
    company_domain_id: CanonicalUUID
    ceo_assignment_id: CanonicalUUID
    ceo_principal_id: CanonicalUUID
    period_id: CanonicalUUID
    period_window: PeriodWindow
    method_profile_ref: ProfileRef
    company_reference_ref: ObjectRef
    members: Annotated[list[RoundMember], Field(min_length=1)]
    member_set_version: PositiveInt
    change_reason: NEStr | None = None

    @model_validator(mode="after")
    def consistent(self) -> "RoundDefinitionPayload":
        domains = [m.domain_id for m in self.members]
        if len(set(domains)) != len(domains):
            raise ValueError("duplicate member domain_id")
        assignments = [m.dri_assignment_id for m in self.members]
        if len(set(assignments)) != len(assignments):
            raise ValueError("duplicate member dri_assignment_id")
        principals = [m.dri_principal_id for m in self.members]
        if len(set(principals)) != len(principals):
            raise ValueError("member DRI principals must be distinct natural persons")
        if self.ceo_principal_id in set(principals):
            raise ValueError("CEO principal must differ from every member DRI principal")
        return self


# ------------------------------------------------------------ action params

class MemberSpec(StrictModel):
    domain_id: CanonicalUUID
    dri_assignment_id: CanonicalUUID


def _unique_member_specs(members: list[MemberSpec]) -> None:
    domains = [m.domain_id for m in members]
    if len(set(domains)) != len(domains):
        raise ValueError("duplicate member domain_id")
    assignments = [m.dri_assignment_id for m in members]
    if len(set(assignments)) != len(assignments):
        raise ValueError("duplicate member dri_assignment_id")


class OpenFormationRoundParams(StrictModel):
    company_id: CanonicalUUID
    company_domain_id: CanonicalUUID
    ceo_assignment_id: CanonicalUUID
    period_id: CanonicalUUID
    period_window: PeriodWindow
    method_profile_ref: ProfileRefSpec
    company_reference_ref: ObjectRef
    members: Annotated[list[MemberSpec], Field(min_length=1)]

    @model_validator(mode="after")
    def consistent(self) -> "OpenFormationRoundParams":
        _unique_member_specs(self.members)
        return self


class AmendFormationRoundParams(StrictModel):
    """未激活 Round 的定义修订；period/Profile 不可变（字段不存在，携带即
    422）。members 与 company_reference_ref 至少一项实际变化（review 2）。"""

    expected_member_set_version: PositiveInt
    members: Annotated[list[MemberSpec], Field(min_length=1)] | None = None
    company_reference_ref: ObjectRef | None = None
    change_reason: NEStr

    @model_validator(mode="after")
    def consistent(self) -> "AmendFormationRoundParams":
        if self.members is None and self.company_reference_ref is None:
            raise ValueError("amend requires a member or reference change; no-op rejected")
        if self.members is not None:
            _unique_member_specs(self.members)
        return self


class PublishDomainSubmissionParams(StrictModel):
    domain_id: CanonicalUUID
    dri_assignment_id: CanonicalUUID
    draft_checkpoint: NEStr
    commitment_statement: NEStr
    submission: SubmissionContent


class JudgmentInput(StrictModel):
    """四项判断之一；judge_principal_id 由服务端记为 actor（CEO）。"""

    conclusion: Literal["pass", "fail", "unknown"]
    reason: NEStr
    evidence_refs: list[ObjectRef] = Field(default_factory=list)


class JudgmentsInput(StrictModel):
    coverage: JudgmentInput
    coherence: JudgmentInput
    feasibility: JudgmentInput
    tradeoff: JudgmentInput


class DeclaredConflict(StrictModel):
    """人工声明的未决冲突；blocking=true 使候选不得确认/生效（review 10）。"""

    summary: NEStr
    blocking: StrictBool


class FormCompanyCompositionParams(StrictModel):
    expected_member_set_version: PositiveInt
    expected_input_set_version: PositiveInt
    judgments: JudgmentsInput
    unresolved_conflicts: list[DeclaredConflict] = Field(default_factory=list)


class ConfirmCompanyCompositionParams(StrictModel):
    composition_ref: CompositionRef
    # 显式任职声明（review 4）：与 actor 本人及 manifest 槽位三方核对。
    assignment_id: CanonicalUUID
    confirmation_statement: NEStr


class ActivateCompanyCompositionParams(StrictModel):
    composition_ref: CompositionRef
    expected_member_set_version: PositiveInt
    expected_input_set_version: PositiveInt


A2_ACTION_PARAMS: dict[str, type[StrictModel]] = {
    "open_formation_round": OpenFormationRoundParams,
    "amend_formation_round": AmendFormationRoundParams,
    "publish_domain_submission": PublishDomainSubmissionParams,
    "form_company_composition": FormCompanyCompositionParams,
    "confirm_company_composition": ConfirmCompanyCompositionParams,
    "activate_company_composition": ActivateCompanyCompositionParams,
}

# ---------------------------------------------------------------- manifest

class Judgment(StrictModel):
    conclusion: Literal["pass", "fail", "unknown"]
    reason: NEStr
    evidence_refs: list[ObjectRef]
    judge_principal_id: CanonicalUUID


class Judgments(StrictModel):
    coverage: Judgment
    coherence: Judgment
    feasibility: Judgment
    tradeoff: Judgment


class CompositionMember(StrictModel):
    domain_id: CanonicalUUID
    dri_assignment_id: CanonicalUUID
    dri_principal_id: CanonicalUUID
    submission_ref: ObjectRef


class CapacityConstraint(StrictModel):
    """同资源池汇总约束；kind/relation 本期只支持固定值。available 由服务端
    以来源 available-reserved 计算（review 5）。"""

    kind: Literal["capacity"]
    resource_id: CanonicalUUID
    period_id: CanonicalUUID
    unit: NEStr
    required: NonNegativeInt
    available: NonNegativeInt


class BindingDependency(StrictModel):
    dependency_id: CanonicalUUID
    relation_type: Literal["resource_capacity"]
    source_ref: ObjectRef
    constraint: CapacityConstraint


class RequiredSigner(StrictModel):
    principal_id: CanonicalUUID
    assignment_id: CanonicalUUID
    responsibility_role: Literal["company_decider", "area_accountable"]


class CompositionManifest(StrictModel):
    """冻结 schema tkos.composition-manifest/0.1 的最小字段。

    manifest_hash 为 tkos-json-v1 对剔除 manifest_hash 后的规范化字节取
    SHA-256；hash 不含签认进度、回执或对象头 CAS。
    成员按 domain_id、签认人按 principal_id、依赖按 dependency_id 升序；
    集合内唯一；同一 resource_id+period_id+unit 容量池唯一。
    """

    manifest_schema_version: Literal["tkos.composition-manifest/0.1"]
    scope_id: CanonicalUUID
    company_id: CanonicalUUID
    round_id: CanonicalUUID
    period_id: CanonicalUUID
    method_profile_ref: ProfileRef
    member_set_version: PositiveInt
    input_set_version: PositiveInt
    company_reference_ref: ObjectRef
    members: Annotated[list[CompositionMember], Field(min_length=1)]
    binding_dependencies: list[BindingDependency]
    judgments: Judgments
    required_signers: Annotated[list[RequiredSigner], Field(min_length=1)]
    hash_scheme: Literal["tkos-json-v1"]
    manifest_hash: Sha256Hex


def compute_manifest_hash(manifest: dict) -> str:
    """对剔除 manifest_hash 的 dict 计算 tkos-json-v1 摘要。"""
    return canon.digest_excluding(manifest, frozenset({"manifest_hash"}))


def _sorted(keys: list[str]) -> bool:
    return keys == sorted(keys)


def static_conflict_reasons(manifest: CompositionManifest) -> list[str]:
    """四项判断与确定性容量冲突（confirm/activate 门槛共用）。"""
    reasons: list[str] = []
    judgments = manifest.judgments.model_dump(mode="json")
    for name in ("coverage", "coherence", "feasibility", "tradeoff"):
        conclusion = judgments[name]["conclusion"]
        if conclusion != "pass":
            reasons.append(f"judgment {name} conclusion={conclusion}")
    for dep in manifest.binding_dependencies:
        c = dep.constraint
        if c.required > c.available:
            reasons.append(
                f"deterministic capacity conflict: dependency {dep.dependency_id} "
                f"resource {c.resource_id} period {c.period_id} required={c.required} > available={c.available}"
            )
    return reasons


def validate_manifest(manifest: CompositionManifest) -> None:
    """服务端复算冻结校验器 check_manifest 的静态不变量。

    唯一性、固定排序、hash、签认集合、判断主体、周期一致。失败抛
    CompositionValidationError（code 与冻结 RejectionCode 对齐）。
    """
    members = manifest.members
    signers = manifest.required_signers
    deps = manifest.binding_dependencies

    member_keys = [str(m.domain_id) for m in members]
    if len(set(member_keys)) != len(member_keys):
        _fail("DUPLICATE_MEMBER", "duplicate domain_id in members")
    if not _sorted(member_keys):
        _fail("ORDERING_VIOLATION", "members must be sorted by domain_id ascending")

    member_dri_principals = [str(m.dri_principal_id) for m in members]
    member_dri_assignments = [str(m.dri_assignment_id) for m in members]
    if len(set(member_dri_principals)) != len(member_dri_principals):
        _fail("MEMBER_DRI_NOT_UNIQUE", "dri_principal_id shared across member domains")
    if len(set(member_dri_assignments)) != len(member_dri_assignments):
        _fail("MEMBER_DRI_NOT_UNIQUE", "dri_assignment_id shared across member domains")

    signer_principals = [str(s.principal_id) for s in signers]
    signer_assignments = [str(s.assignment_id) for s in signers]
    if len(set(signer_principals)) != len(signer_principals):
        _fail("DUPLICATE_SIGNER", "duplicate principal_id in required_signers")
    if len(set(signer_assignments)) != len(signer_assignments):
        _fail("DUPLICATE_SIGNER", "duplicate assignment_id in required_signers")
    if not _sorted(signer_principals):
        _fail("ORDERING_VIOLATION", "required_signers must be sorted by principal_id ascending")

    dep_keys = [str(d.dependency_id) for d in deps]
    if len(set(dep_keys)) != len(dep_keys):
        _fail("DUPLICATE_DEPENDENCY", "duplicate dependency_id in binding_dependencies")
    if not _sorted(dep_keys):
        _fail("ORDERING_VIOLATION", "binding_dependencies must be sorted by dependency_id ascending")

    pool_keys = [
        (str(d.constraint.resource_id), str(d.constraint.period_id), d.constraint.unit)
        for d in deps
    ]
    if len(set(pool_keys)) != len(pool_keys):
        _fail("DUPLICATE_CAPACITY_POOL",
              "duplicate capacity constraint for the same resource_id+period_id+unit pool")

    payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    actual = compute_manifest_hash(payload)
    if actual != manifest.manifest_hash:
        _fail("MANIFEST_HASH_MISMATCH",
              f"declared {manifest.manifest_hash} != computed {actual}")

    ceo = [s for s in signers if s.responsibility_role == "company_decider"]
    dri = [s for s in signers if s.responsibility_role == "area_accountable"]
    if len(ceo) != 1:
        _fail("SIGNER_SET_MISMATCH", f"expected exactly 1 company_decider signer, got {len(ceo)}")
    if str(ceo[0].principal_id) in set(member_dri_principals):
        _fail("SIGNER_SET_MISMATCH",
              "company_decider principal must differ from every member DRI principal")
    member_pairs = set(zip(member_dri_principals, member_dri_assignments))
    dri_pairs = {(str(s.principal_id), str(s.assignment_id)) for s in dri}
    if dri_pairs != member_pairs:
        _fail("SIGNER_SET_MISMATCH",
              "area_accountable signer pairs must exactly equal member DRI pairs")

    ceo_principal = str(ceo[0].principal_id)
    for name, judgment in manifest.judgments.model_dump(mode="json").items():
        if judgment["judge_principal_id"] != ceo_principal:
            _fail("JUDGMENT_JUDGE_MISMATCH",
                  f"judgment {name} judge must be the company_decider")

    for dep in deps:
        if dep.constraint.period_id != manifest.period_id:
            _fail("PERIOD_MISMATCH",
                  f"dependency {dep.dependency_id} constraint period "
                  f"{dep.constraint.period_id} != manifest period {manifest.period_id}")


A2_PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    "CompanyReference": CompanyReferencePayload,
    "CapacityObservation": CapacityObservationPayload,
    "FormationRound": RoundDefinitionPayload,
    "DomainSubmission": DomainSubmissionPayload,
    "CompanyComposition": CompositionManifest,
    "Mission": MissionPayload,
    "DomainCommitment": DomainCommitmentPayload,
}


__all__ = [
    "A2_ACTIONS", "A2_SOURCE_TYPES", "A2_COMPOSITION_TYPES", "A2_OBJECT_TYPES",
    "A2_ACTION_PARAMS", "A2_PAYLOAD_MODELS",
    "MANIFEST_SCHEMA_VERSION", "HASH_SCHEME", "SOURCE_FRESHNESS_TTL_SECONDS",
    "CLOSURE_MAX_NODES", "CLOSURE_MAX_DEPTH",
    "CompositionValidationError",
    "RevisionRef", "ObjectRef", "CompositionRef", "ProfileRef", "ProfileRefSpec",
    "PeriodWindow", "CompanyReferencePayload", "CapacityObservationPayload",
    "AcceptanceCriterion", "MissionSpec", "ResourceDemand", "BindingSpec",
    "SubmissionContent", "PdoSpec", "DomainSubmissionPayload",
    "MissionPayload", "MissionRef", "DomainCommitmentPayload",
    "RoundMember", "RoundDefinitionPayload", "MemberSpec",
    "OpenFormationRoundParams", "AmendFormationRoundParams",
    "PublishDomainSubmissionParams", "JudgmentInput", "JudgmentsInput",
    "DeclaredConflict", "FormCompanyCompositionParams",
    "ConfirmCompanyCompositionParams", "ActivateCompanyCompositionParams",
    "Judgment", "Judgments", "CompositionMember", "CapacityConstraint",
    "BindingDependency", "RequiredSigner", "CompositionManifest",
    "compute_manifest_hash", "static_conflict_reasons", "validate_manifest",
]
