"""A3 DRI–IC 执行交接的严格参数与载荷模型（Contract-A tkos.contract-a/0.1）。

语义对齐契约包 A §5/§6 与 docs/runtime-a3-engineering.md §1–§4 的字段映射。
本模块独立于 governed/models.py（不 import，避免循环；信封/ActionRequest 集成
不在本轮范围）。标量类型复用 governed/a2_models.py 的共享定义（NEStr、
CanonicalUUID、Sha256Hex、IsoDateTime、PositiveInt、StrictModel、
AcceptanceCriterion），规范化/摘要约定沿用 tkos-json-v1（governed/canon.py，
本模块不重复实现）。

本模块只做结构与静态不变量校验。它不证明：服务端 actor 身份、任职当前有效性、
对象真实存在或当前生效、ExecutionAuthority/AcceptanceAppointment 的当前状态、
跨对象一致性（WorkItem 标准与已确认 What 一致、步骤时间不超 What 硬期限等）。
这些只能由 service 层在持有 scope 栅栏后查权威数据。

Outcome 模型本轮整体推迟（待接口预审收敛），不在此定义。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Union

from pydantic import Field, StringConstraints, model_validator
from typing_extensions import Annotated as TypingAnnotated

from .a2_models import (
    AcceptanceCriterion,
    CanonicalUUID,
    IsoDateTime,
    NEStr,
    PositiveInt,
    Sha256Hex,
    StrictModel,
)

# ---------------------------------------------------------------- constants

# A3 复用既有动作名（受限 handler 由服务端协议登记选择；契约包 A §5.2）。
A3_ACTIONS = frozenset({
    "create_object",
    "propose_revision",
    "accept_commitment",
    "activate_commitment",
    "accept_work_item",
    "submit_deliverable",
    "review_deliverable",
})

# 通用 create_object 白名单（§5.2）：ExecutionCommitment 草案、受限
# ExecutionPlan、已有执行授权下的 WorkItem。Mission 由 A2 控制候选与生效，
# 已有 WorkItem/Deliverable 禁止通用改版，故 propose 只放行 EC 与 Plan。
A3_GENERIC_CREATE_TYPES = frozenset({
    "ExecutionCommitment", "WorkItem", "ExecutionPlan",
})
A3_GENERIC_REVISE_TYPES = frozenset({"ExecutionCommitment", "ExecutionPlan"})


# --------------------------------------------------------------------- refs

class ExactRef(StrictModel):
    """A3 精确引用三元组：object_id + revision_id + payload_hash（§2）。"""

    object_id: CanonicalUUID
    revision_id: CanonicalUUID
    payload_hash: Sha256Hex


def _distinct_refs(refs: list[ExactRef], field: str) -> None:
    keys = [(r.object_id, r.revision_id) for r in refs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate refs in {field}")


def _distinct_criteria(criteria: list[AcceptanceCriterion], field: str) -> None:
    ids = [c.criterion_id for c in criteria]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate acceptance criterion ids in {field}")


# ------------------------------------------------------------------ windows

class A3Window(StrictModel):
    """前闭后开 [valid_from, valid_to)。验收窗口与执行窗口相互独立：
    本模型不施加两者之间的先后约束（执行权失效不解释为验收任命失效）。"""

    valid_from: IsoDateTime
    valid_to: IsoDateTime

    @model_validator(mode="after")
    def ordered(self) -> "A3Window":
        if datetime.fromisoformat(self.valid_to) <= datetime.fromisoformat(self.valid_from):
            raise ValueError("window valid_to must be later than valid_from (half-open [from, to))")
        return self


# -------------------------------------------------------------------- what

class A3What(StrictModel):
    """结构化 What：结果/边界/标准/硬期限/对外依赖。

    与 A2 Mission 正式内容的一致性核对属于 service 层；此处只做结构校验。
    字符串按来源原样保留（不 strip、不改写）。
    """

    result_statement: NEStr
    boundary: NEStr
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    hard_deadline: IsoDateTime
    external_dependency_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct(self) -> "A3What":
        _distinct_criteria(self.acceptance_criteria, "acceptance_criteria")
        _distinct_refs(self.external_dependency_refs, "external_dependency_refs")
        return self


# ---------------------------------------------------------------- payloads

class A3ExecutionCommitmentPayload(StrictModel):
    """ExecutionCommitment revision 载荷：同一 Mission 下 DRI 与 IC 对
    What/边界的同版承诺，绑定三位不同自然人的任职（草案形态）。

    dri/ic/acceptor 三者任职两两不同（静态形状校验）；其背后是不同自然人、
    任职当前有效等只能由 service 层核对。执行窗口截止不得晚于 What 硬期限。
    """

    title: NEStr
    mission_ref: ExactRef
    domain_commitment_ref: ExactRef
    dri_assignment_id: CanonicalUUID
    ic_assignment_id: CanonicalUUID
    acceptor_assignment_id: CanonicalUUID
    what: A3What
    execution_window: A3Window
    acceptance_window: A3Window

    @model_validator(mode="after")
    def consistent(self) -> "A3ExecutionCommitmentPayload":
        assignments = {
            "dri_assignment_id": self.dri_assignment_id,
            "ic_assignment_id": self.ic_assignment_id,
            "acceptor_assignment_id": self.acceptor_assignment_id,
        }
        if len(set(assignments.values())) != len(assignments):
            raise ValueError(
                "dri/ic/acceptor assignments must be pairwise distinct"
            )
        if datetime.fromisoformat(self.execution_window.valid_to) > datetime.fromisoformat(
            self.what.hard_deadline
        ):
            raise ValueError(
                "execution_window.valid_to must not exceed what.hard_deadline"
            )
        return self


class A3WorkItemPayload(StrictModel):
    """WorkItem revision 载荷：不超出 What 的交付单元。

    acceptance_criteria/due_at 与已确认 What 的一致性是跨对象核对，
    属 service 层；此处只保证结构、唯一性与精确引用形状。
    """

    title: NEStr
    execution_commitment_ref: ExactRef
    execution_authority_id: CanonicalUUID
    execution_epoch: PositiveInt
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    due_at: IsoDateTime

    @model_validator(mode="after")
    def distinct(self) -> "A3WorkItemPayload":
        _distinct_criteria(self.acceptance_criteria, "acceptance_criteria")
        return self


class A3PlanStep(StrictModel):
    """ExecutionPlan 的单步。description 是自由文本但不具有改变结构化
    What 的效力（无结构化字段可承载标准/责任/依赖改写）。"""

    step_id: NEStr
    description: NEStr
    due_at: IsoDateTime | None = None
    depends_on_step_ids: list[NEStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct(self) -> "A3PlanStep":
        if len(set(self.depends_on_step_ids)) != len(self.depends_on_step_ids):
            raise ValueError("duplicate depends_on_step_ids")
        return self


class A3ExecutionPlanPayload(StrictModel):
    """ExecutionPlan revision 载荷：IC 的 How。

    本载荷不含 What：传入结构化 what（或任何未知字段）一律拒绝——计划
    文本/结构不得改写结果、标准、责任或外部依赖。step_id 唯一、依赖引用
    必须存在且无循环。步骤时间不超 What 硬期限属跨对象核对（service 层）。
    """

    title: NEStr
    work_item_ref: ExactRef
    execution_commitment_ref: ExactRef
    steps: list[A3PlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def acyclic(self) -> "A3ExecutionPlanPayload":
        ids = [s.step_id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate step_id in steps")
        known = set(ids)
        for step in self.steps:
            for dep in step.depends_on_step_ids:
                if dep not in known:
                    raise ValueError(
                        f"step {step.step_id!r} depends on unknown step {dep!r}"
                    )
        graph = {s.step_id: list(s.depends_on_step_ids) for s in self.steps}
        # 迭代三色 DFS：白=未访问，灰=在当前栈，黑=已完成。
        color: dict[str, int] = dict.fromkeys(graph, 0)
        for root in graph:
            if color[root] != 0:
                continue
            stack: list[tuple[str, int]] = [(root, 0)]
            color[root] = 1
            while stack:
                node, idx = stack[-1]
                deps = graph[node]
                if idx < len(deps):
                    stack[-1] = (node, idx + 1)
                    nxt = deps[idx]
                    if color[nxt] == 1:
                        raise ValueError(
                            f"dependency cycle detected involving step {nxt!r}"
                        )
                    if color[nxt] == 0:
                        color[nxt] = 1
                        stack.append((nxt, 0))
                else:
                    color[node] = 2
                    stack.pop()
        return self


A3PayloadUnion = Union[
    A3ExecutionCommitmentPayload, A3WorkItemPayload, A3ExecutionPlanPayload,
]

A3_PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    "ExecutionCommitment": A3ExecutionCommitmentPayload,
    "WorkItem": A3WorkItemPayload,
    "ExecutionPlan": A3ExecutionPlanPayload,
}


# -------------------------------------------------- generic create / propose

class A3CreateObjectParams(StrictModel):
    """A3 通用创建形状：白名单 EC 草案 / 受限 Plan / 有授权下的 WorkItem。

    只决定参数形状与 payload 分派；类型是否可写、当前责任与
    ProtocolBinding 由 service 层核对。与 ActionRequest 信封的集成不在
    本轮范围。
    """

    object_type: Literal["ExecutionCommitment", "WorkItem", "ExecutionPlan"]
    domain_id: CanonicalUUID
    payload: A3PayloadUnion

    @model_validator(mode="before")
    @classmethod
    def select_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            object_type = data.get("object_type")
            model = A3_PAYLOAD_MODELS.get(object_type) if isinstance(object_type, str) else None
            if model is not None:
                data = dict(data)
                data["payload"] = model.model_validate(data.get("payload"))
        return data

    @model_validator(mode="after")
    def payload_matches_type(self) -> "A3CreateObjectParams":
        if not isinstance(self.payload, A3_PAYLOAD_MODELS[self.object_type]):
            raise ValueError("payload does not match object_type")
        return self


class A3ProposeRevisionParams(StrictModel):
    """A3 通用改版形状：仅限未激活 ExecutionCommitment 与受限
    ExecutionPlan（契约包 A §5.2：已有 WorkItem/Deliverable 禁止通用改版）。
    目标对象的真实类型与当前状态由 service 层核对。"""

    payload: Union[A3ExecutionCommitmentPayload, A3ExecutionPlanPayload]
    bundle_id: CanonicalUUID | None = None


# ------------------------------------------------------------ action params
# accept_commitment / activate_commitment 沿用既有 wire 参数形状（工程文档
# §2：分别校验本人精确任职/terms hash 与指定同版双签/当前激活策略），
# 此处用 A3 严格标量重述，字段名与 governed/models.py 保持一致。

# 既有 AcceptCommitmentParams.understanding 的 min_length=10；NEStr 已含
# min_length=1，无法叠加覆盖，故独立定义。
UnderstandingText = TypingAnnotated[
    str, StringConstraints(min_length=10, pattern=r"\S")
]


class A3AcceptCommitmentParams(StrictModel):
    """与既有 AcceptCommitmentParams 相同的 wire 形状（A3 严格标量版）。"""

    party_assignment_id: CanonicalUUID
    understanding: UnderstandingText
    accepted_terms_hash: Sha256Hex


class A3ActivateCommitmentParams(StrictModel):
    """与既有 ActivateCommitmentParams 相同的 wire 形状（A3 严格标量版）。"""

    handshake_record_ids: list[CanonicalUUID] = Field(min_length=2)
    activation_policy_revision_id: CanonicalUUID

    @model_validator(mode="after")
    def distinct(self) -> "A3ActivateCommitmentParams":
        if len(set(self.handshake_record_ids)) != len(self.handshake_record_ids):
            raise ValueError("duplicate handshake ids")
        return self


class A3AcceptWorkItemParams(StrictModel):
    """IC 接收任务：显式绑定接收时的执行授权 ID 与代次（§2）。

    接收人即当前 IC 由服务端 actor 推导；标准/硬期限与已确认 What 的一致
    性（不以接收暗加责任）由 service 层核对。
    """

    execution_authority_id: CanonicalUUID
    execution_epoch: PositiveInt


class A3SubmitDeliverableParams(StrictModel):
    """保留既有 submit 字段（标题、摘要、真实证据版本、回应退回 ID），
    另精确绑定接收时执行授权/代次及当前计划引用（§2）。

    作者集合由服务端提交人与证据实际记录者推导，本形状不接受作者字段。
    """

    title: NEStr
    summary: NEStr
    evidence_revision_ids: list[CanonicalUUID] = Field(min_length=1)
    responds_to_acceptance_id: CanonicalUUID | None = None
    execution_authority_id: CanonicalUUID
    execution_epoch: PositiveInt
    plan_ref: ExactRef

    @model_validator(mode="after")
    def distinct(self) -> "A3SubmitDeliverableParams":
        if len(set(self.evidence_revision_ids)) != len(self.evidence_revision_ids):
            raise ValueError("duplicate evidence revisions")
        return self


class A3CriterionResult(StrictModel):
    criterion_id: NEStr
    result: Literal["passed", "failed"]
    note: NEStr


class A3ReviewDeliverableParams(StrictModel):
    """保留既有 review 字段（精确交付版本/hash、逐项判断、评语），另绑定
    验收任命 ID/版本（§2）。

    当前验收者不得是 DRI/IC 或可信作者/共同作者——该核对需要服务端
    actor 与任命记录，不在本模型范围。
    """

    deliverable_revision_id: CanonicalUUID
    delivery_payload_hash: Sha256Hex
    verification_result: Literal["accepted", "changes_requested"]
    criterion_results: list[A3CriterionResult] = Field(min_length=1)
    review_note: NEStr
    appointment_id: CanonicalUUID
    appointment_version: PositiveInt

    @model_validator(mode="after")
    def distinct(self) -> "A3ReviewDeliverableParams":
        ids = [r.criterion_id for r in self.criterion_results]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate criterion result ids")
        return self


A3_ACTION_PARAMS: dict[str, type[StrictModel]] = {
    "create_object": A3CreateObjectParams,
    "propose_revision": A3ProposeRevisionParams,
    "accept_commitment": A3AcceptCommitmentParams,
    "activate_commitment": A3ActivateCommitmentParams,
    "accept_work_item": A3AcceptWorkItemParams,
    "submit_deliverable": A3SubmitDeliverableParams,
    "review_deliverable": A3ReviewDeliverableParams,
}


__all__ = [
    "A3_ACTIONS", "A3_GENERIC_CREATE_TYPES", "A3_GENERIC_REVISE_TYPES",
    "A3_ACTION_PARAMS", "A3_PAYLOAD_MODELS", "A3PayloadUnion",
    "ExactRef", "A3Window", "A3What",
    "A3ExecutionCommitmentPayload", "A3WorkItemPayload",
    "A3PlanStep", "A3ExecutionPlanPayload",
    "A3CreateObjectParams", "A3ProposeRevisionParams",
    "A3AcceptCommitmentParams", "A3ActivateCommitmentParams",
    "A3AcceptWorkItemParams", "A3SubmitDeliverableParams",
    "A3CriterionResult", "A3ReviewDeliverableParams",
    "UnderstandingText",
]
