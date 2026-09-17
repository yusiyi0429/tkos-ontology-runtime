"""MethodProfile 严格模型与协议常量（A1 服务端登记的工件语义）。

ProfileCore 模型抽取自已验收的契约包 A validator/models.py，只保留 Profile
相关的严格语义（未引入 A2 组合校验模块）。canonical_hash 采用 tkos-json-v1
（见 canon.py），剔除 canonical_hash 字段本身；action_contract_ref 绑定主契约
确切字节。安装校验由控制面 CLI 执行，普通应用路径只读已安装记录。
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)
from typing_extensions import Annotated

from . import canon


def _reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("all-whitespace string is not allowed")
    return value


NEStr = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
    AfterValidator(_reject_blank),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


def _require_bool(value: object) -> object:
    if type(value) is not bool:
        raise ValueError("must be a JSON boolean, not int/float/str")
    return value


ConstTrue = Annotated[Literal[True], BeforeValidator(_require_bool)]
ConstFalse = Annotated[Literal[False], BeforeValidator(_require_bool)]

_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"


def _valid_date(value: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {value!r}") from exc
    return value


IsoDate = Annotated[
    str,
    StringConstraints(pattern=_DATE_RE),
    AfterValidator(_valid_date),
    Field(json_schema_extra={"format": "date"}),
]

PROFILE_CORE_SCHEMA_VERSION = "tkos.profile-core/0.1"
LEGACY_PROFILE_SCHEMA_VERSION = "tkos.legacy-interpretation-record/0.2"
CONTRACT_ID = "tkos.contract-a"
CONTRACT_REVISION = "0.1"
# 编译支持 tkos.contract-a/0.1 绑定的主契约确切内容 SHA256（契约包 A §3.1）。
# 契约内容演化必须产生新版本；不同字节的内容不得仍被当作同一个已支持协议。
CONTRACT_A_MAIN_CONTRACT_SHA256 = "fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4"

# 冻结的协议身份（详见 docs/runtime-a1-protocol.md）。
LEGACY_PROTOCOL_ID = "tkos.legacy-governed"
LEGACY_CONTRACT_VERSION = "tkos.governed/v0.2"
CONTRACT_A_PROTOCOL_ID = "tkos.contract-a"
CONTRACT_A_CONTRACT_VERSION = f"{CONTRACT_ID}/{CONTRACT_REVISION}"
LEGACY_PROFILE_ID = "urn:tkos:legacy:governed-v0.2"
LEGACY_PROFILE_REVISION = "0.2.0"

# 进程能力声明：携带该 GUC 的连接才允许写治理业务表与 governance.dispatch 任务。
# 它防止未升级的旧 writer/worker 在迁移后继续写入；不是对抗任意 SQL 的安全边界。
WRITE_CAPABILITY = "tkos-runtime-a1"

_STRICT = ConfigDict(extra="forbid")


class SourcePolicy(BaseModel):
    model_config = _STRICT
    sources_are_reading_snapshots_not_live_sync: StrictBool
    source_revision_is_not_profile_revision: StrictBool
    new_source_revision_automatically_reinterprets_instances: StrictBool
    business_semantics_bound_to_instance_profile: StrictBool
    new_actions_check_current_authority_and_continuity: StrictBool
    unsupported_profile_write_behavior: NEStr
    migration_requires_explicit_record: StrictBool


class ProfileSourceRef(BaseModel):
    """来源引用：ID／原生修订／内容哈希；不含本机路径；可缺省观察值必填 nullable。"""

    model_config = _STRICT
    source_id: NEStr
    title: NEStr
    url: NEStr
    native_revision: Optional[NonNegativeInt]
    revision_kind: NEStr
    document_version_label: Optional[str]
    usage: NEStr
    observed_on: IsoDate
    artifact_sha256: Sha256Hex
    visual_artifact_sha256: Optional[Sha256Hex]
    table_id: Optional[NEStr]
    record_count: Optional[NonNegativeInt]
    corporate_approval_inferred: ConstFalse


class StableConcept(BaseModel):
    model_config = _STRICT
    concept_id: NEStr
    technical_label: NEStr
    meaning_in_this_experiment: NEStr
    designation_status: NEStr


class SourceAlias(BaseModel):
    model_config = _STRICT
    alias_id: NEStr
    label: NEStr
    source_id: NEStr
    locations: list[NEStr]
    candidate_concept_ids: list[NEStr]
    unresolved_note: Optional[NEStr]
    mapping_status: Literal["SOURCE_ALIAS_ONLY_PENDING_METHOD_CONFIRMATION"]
    automatic_equivalence_or_migration: ConstFalse


class CompositionRules(BaseModel):
    model_config = _STRICT
    scope: NEStr
    quorum: NEStr
    activation: NEStr
    partial_activation_permitted: StrictBool
    signature_inheritance_across_manifests: StrictBool
    single_active_formation_per_period: StrictBool
    cross_round_capacity_reservations_checked: StrictBool
    same_business_content_different_revision_is_new_input: StrictBool
    formal_submission_or_binding_change: NEStr
    local_unshared_draft_change: NEStr
    unrelated_auth_epoch_change: NEStr
    required_binding_changed_or_expired: NEStr
    required_judgments: list[NEStr]
    unresolved_hard_conflict: NEStr
    llm_judgment_can_override_hard_constraint: StrictBool
    complete_member_set_derived_by_server: StrictBool
    manifest_excludes: list[NEStr]
    manifest_hash_scheme: NEStr
    required_signer_rule: NEStr
    distinct_natural_persons_required: StrictBool
    member_add_remove_supported_by_same_profile: StrictBool
    member_change_effect: NEStr
    judgment_display_aliases: dict[str, NEStr]


class VersionAndDraftRules(BaseModel):
    model_config = _STRICT
    stable_object_id_separate_from_revision_and_cas: StrictBool
    unshared_working_draft_overwrite_permitted: StrictBool
    freeze_on: list[NEStr]
    historical_confirmations_append_only: StrictBool
    shared_reference_implies_commitment: StrictBool
    old_receipt_implies_current_execute_permission: StrictBool


class ResponsibilityAndAcceptanceRules(BaseModel):
    model_config = _STRICT
    area_dri_may_hold_mission_accountability_in_fixture: StrictBool
    dri_and_ic_same_natural_person_permitted: StrictBool
    what_handshake_parties: list[NEStr]
    both_sign_same_content_revision: StrictBool
    execution_authority_release_by: NEStr
    last_signature_implicitly_releases_execution: StrictBool
    work_receipt_is: NEStr
    acceptor_mode: NEStr
    acceptor_cannot_be: list[NEStr]
    agent_may_sign_or_accept: StrictBool
    handover_rewrites_historical_authors_or_signers: StrictBool
    future_actions_require_current_responsibility_binding: StrictBool
    three_results_independent: list[NEStr]


class WhatHowRules(BaseModel):
    model_config = _STRICT
    what_fields: list[NEStr]
    how_fields_allowed_within_what: list[NEStr]
    material_change_threshold: NEStr
    unknown_classification: NEStr
    plan_revision_can_change_acceptance_standard: StrictBool
    changed_what_can_reuse_old_handshake: StrictBool


class AuthorityAndVisibilityRules(BaseModel):
    model_config = _STRICT
    historical_fact_deleted_on_revocation: StrictBool
    read_and_replay_use_current_rights: StrictBool
    cross_area_read_grants_are_union_by_default: StrictBool
    all_composition_signers_read_full_published_manifest: StrictBool
    private_evidence_shared_by_merge_or_summary_automatically: StrictBool
    explicit_source_publication_grant_required: StrictBool
    legacy_endpoint_can_bypass_profile: StrictBool


class ProfileRules(BaseModel):
    model_config = _STRICT
    composition: CompositionRules
    version_and_draft: VersionAndDraftRules
    responsibility_and_acceptance: ResponsibilityAndAcceptanceRules
    what_how: WhatHowRules
    authority_and_visibility: AuthorityAndVisibilityRules


class PendingDecision(BaseModel):
    model_config = _STRICT
    decision_id: NEStr
    topic: NEStr
    owner: NEStr
    experiment_choice: NEStr
    production_block: NEStr


class ActionContractRef(BaseModel):
    """动作语义基线的不可变引用：主契约原始 UTF-8 字节的 SHA256。"""

    model_config = _STRICT
    contract_id: Literal["tkos.contract-a"]
    revision: Literal["0.1"]
    content_sha256: Sha256Hex


class ProfileCore(BaseModel):
    """可安装实验规则核心；canonical_hash 只覆盖剔除自身后的规范化规则核心。"""

    model_config = _STRICT
    profile_core_schema_version: Literal["tkos.profile-core/0.1"]
    profile_id: NEStr
    revision: NEStr
    display_name: NEStr
    experimental: ConstTrue
    record_origin: Literal["synthetic"]
    corporate_approved: ConstFalse
    source_policy: SourcePolicy
    sources: list[ProfileSourceRef]
    concepts: list[StableConcept]
    source_aliases: list[SourceAlias]
    rules: ProfileRules
    pending_decisions: list[PendingDecision]
    action_contract_ref: ActionContractRef
    canonical_hash: Sha256Hex


def check_profile_refs(core: ProfileCore) -> None:
    """来源／概念／别名引用完整性与 ID 唯一性（对齐已验收校验器 check_profile_refs）。"""
    source_ids = [s.source_id for s in core.sources]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("duplicate source_id")
    concept_ids = [c.concept_id for c in core.concepts]
    if len(set(concept_ids)) != len(concept_ids):
        raise ValueError("duplicate concept_id")
    alias_ids = [a.alias_id for a in core.source_aliases]
    if len(set(alias_ids)) != len(alias_ids):
        raise ValueError("duplicate alias_id")
    decision_ids = [d.decision_id for d in core.pending_decisions]
    if len(set(decision_ids)) != len(decision_ids):
        raise ValueError("duplicate decision_id")

    source_set = set(source_ids)
    concept_set = set(concept_ids)
    for alias in core.source_aliases:
        if alias.source_id not in source_set:
            raise ValueError(
                f"alias {alias.alias_id} references unknown source {alias.source_id}"
            )
        for cid in alias.candidate_concept_ids:
            if cid not in concept_set:
                raise ValueError(
                    f"alias {alias.alias_id} references unknown concept {cid}"
                )


def validate_profile_core(data: object) -> ProfileCore:
    """严格解析并核验自声明 canonical_hash 与引用完整性；任何偏差都抛异常。"""
    if isinstance(data, dict) and data.get("profile_core_schema_version") == "tkos.method-profile/0.4":
        from .method_v04_profile import validate
        return validate(data)
    if isinstance(data, dict) and data.get("profile_core_schema_version") == "tkos.method-profile/0.3":
        from .method_v03_profile import validate
        return validate(data)
    if isinstance(data, dict) and data.get("profile_core_schema_version") == "tkos.method-profile/0.2":
        from .method_v02_profile import validate
        return validate(data)
    if isinstance(data, dict) and data.get("profile_core_schema_version") == "tkos.method-profile/0.1":
        from .method_profile import validate
        return validate(data)
    core = ProfileCore.model_validate(data)
    computed = canon.digest_excluding(
        core.model_dump(mode="json"), frozenset({"canonical_hash"})
    )
    if computed != core.canonical_hash:
        raise ValueError(
            "canonical_hash mismatch: declared "
            f"{core.canonical_hash}, computed {computed}"
        )
    check_profile_refs(core)
    return core


def implied_protocol(schema_version: str, content: object) -> tuple[str, str] | None:
    """The (protocol_id, contract_version) an installed profile row semantically
    belongs to, per the mapping compiled into this runtime.

    A correct canonical_hash alone never proves protocol meaning: the legacy
    interpretation record is pinned to its frozen identity, and a ProfileCore
    belongs to Contract-A only through its action_contract_ref.  Rows matching
    no implemented mapping return None and every consumer must reject them.
    """
    if schema_version == "tkos.method-profile/0.4":
        from . import method_v04_profile
        try:
            method_v04_profile.validate(content)
        except (ValueError, TypeError):
            return None
        return (method_v04_profile.PROTOCOL_ID, method_v04_profile.CONTRACT_VERSION)
    if schema_version == "tkos.method-profile/0.3":
        from . import method_v03_profile
        try:
            method_v03_profile.validate(content)
        except (ValueError, TypeError):
            return None
        return (method_v03_profile.PROTOCOL_ID, method_v03_profile.CONTRACT_VERSION)
    if schema_version == "tkos.method-profile/0.2":
        from . import method_v02_profile
        try:
            method_v02_profile.validate(content)
        except (ValueError, TypeError):
            return None
        return (method_v02_profile.PROTOCOL_ID, method_v02_profile.CONTRACT_VERSION)
    if schema_version == "tkos.method-profile/0.1":
        from . import method_profile
        try:
            method_profile.validate(content)
        except (ValueError, TypeError):
            return None
        return (method_profile.PROTOCOL_ID, method_profile.CONTRACT_VERSION)
    if schema_version == LEGACY_PROFILE_SCHEMA_VERSION:
        if (isinstance(content, dict)
                and content.get("profile_id") == LEGACY_PROFILE_ID
                and content.get("revision") == LEGACY_PROFILE_REVISION
                and content.get("protocol_id") == LEGACY_PROTOCOL_ID
                and content.get("contract_version") == LEGACY_CONTRACT_VERSION):
            return (LEGACY_PROTOCOL_ID, LEGACY_CONTRACT_VERSION)
        return None
    if schema_version == PROFILE_CORE_SCHEMA_VERSION:
        if isinstance(content, dict):
            ref = content.get("action_contract_ref")
            if (isinstance(ref, dict)
                    and ref.get("contract_id") == CONTRACT_ID
                    and ref.get("revision") == CONTRACT_REVISION
                    and ref.get("content_sha256") == CONTRACT_A_MAIN_CONTRACT_SHA256):
                return (CONTRACT_A_PROTOCOL_ID, CONTRACT_A_CONTRACT_VERSION)
        return None
    return None


def canonical_hash_of(core: ProfileCore) -> str:
    return canon.digest_excluding(
        core.model_dump(mode="json"), frozenset({"canonical_hash"})
    )


# 旧 v0.2 协议的解释记录（非实验 ProfileCore，不宣称公司批准）。内容由迁移与
# 控制面共用；canonical_hash 为 tkos-json-v1 对 content 的摘要，必须保持确定。
LEGACY_PROFILE_CONTENT: dict = {
    "profile_kind": "tkos.legacy-interpretation-record",
    "profile_id": LEGACY_PROFILE_ID,
    "revision": LEGACY_PROFILE_REVISION,
    "display_name": "Legacy governed runtime v0.2 interpretation record",
    "protocol_id": LEGACY_PROTOCOL_ID,
    "contract_version": LEGACY_CONTRACT_VERSION,
    "experimental": False,
    "corporate_approved": False,
    "record_origin": "legacy",
    "semantics": (
        "Pre-contract-A governed runtime semantics. MISSION_DRI retains its "
        "legacy meaning; no IC handover, company composition or MethodProfile "
        "interpretation is implied. Historical payloads, receipts and hashes "
        "are preserved unchanged."
    ),
    "source": "runtime baseline 3cd9109d726a9a9069a7960a2f2665ce677785d2 behavior",
}
LEGACY_PROFILE_CANONICAL_HASH = canon.digest(LEGACY_PROFILE_CONTENT)

LEGACY_SCOPE_POLICY_CONTENT: dict = {
    "default_protocol": LEGACY_PROTOCOL_ID,
    "default_contract_version": LEGACY_CONTRACT_VERSION,
    "allow_legacy_create": True,
    "record_origin": "legacy",
    "default_profile_ref": {
        "profile_id": LEGACY_PROFILE_ID,
        "revision": LEGACY_PROFILE_REVISION,
    },
    "experimental": False,
    "notes": "Pre-A1 scope registered as legacy by migration 0018 or controlled bootstrap.",
}

LEGACY_REGISTRY_CONTENT: dict = {
    "can_read": True,
    "can_create": True,
    "can_write": True,
    "evidence_upload": True,
    "actions": [
        "accept_commitment", "accept_feedback", "accept_work_item",
        "activate_commitment", "confirm_adjustment", "confirm_closure",
        "confirm_decision", "confirm_outcome", "create_object",
        "investigate_feedback", "propose_revision", "record_acceptance",
        "record_outcome_assessment", "reopen_feedback",
        "request_feedback_acceptance", "review_deliverable", "revoke_assignment",
        "route_feedback", "submit_deliverable",
    ],
    "object_types": [
        "BusinessCommitment", "CompanyOutcome", "Decision", "Deliverable",
        "EvidenceAsset", "ExecutionCommitment", "FeedbackThread",
        "ManagementAdjustment", "MetricObservation", "WorkItem",
    ],
    "readonly_compat": [LEGACY_CONTRACT_VERSION],
    "notes": "Legacy v0.2 handlers remain the only executable business handlers in A1.",
}

CONTRACT_A_REGISTRY_CONTENT: dict = {
    "can_read": True,
    "can_create": False,
    "can_write": False,
    "evidence_upload": True,
    "actions": [],
    "object_types": [],
    "readonly_compat": [CONTRACT_A_CONTRACT_VERSION],
    "notes": (
        "Contract-A protocol registered. A1 provides metadata/read support only; "
        "business writes are rejected with ACTION_NOT_SUPPORTED_FOR_PROTOCOL."
    ),
}


def contract_a_policy_content(profile_id: str, revision: str) -> dict:
    return {
        "default_protocol": CONTRACT_A_PROTOCOL_ID,
        "default_contract_version": CONTRACT_A_CONTRACT_VERSION,
        "allow_legacy_create": False,
        "record_origin": "synthetic",
        "default_profile_ref": {"profile_id": profile_id, "revision": revision},
        "experimental": True,
        "notes": "Contract-A experimental creation scope; A1 registers metadata only.",
    }
