"""Frozen M1A revision 21 command schemas for ``tkos.method/0.1``.

These schemas are independent of the legacy action envelope.  A valid shape is
not authority: the handler checks current identities, exact source revisions,
and the issue's phase in the governed transaction.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .a2_models import CanonicalUUID, IsoDateTime, NEStr, ObjectRef, StrictModel

ExactRef = ObjectRef


def _unique_refs(refs: list[ExactRef]) -> None:
    if len({(r.object_id, r.revision_id) for r in refs}) != len(refs):
        raise ValueError("duplicate exact reference")


class StrategyUnit(StrictModel):
    unit_id: NEStr
    name: NEStr
    judgment: NEStr
    owner_principal_id: CanonicalUUID | None = None


class StrategyMap(StrictModel):
    units: list[StrategyUnit] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_units(self) -> "StrategyMap":
        if len({u.unit_id for u in self.units}) != len(self.units):
            raise ValueError("Strategy map unit_id must be unique")
        return self


class StrategyPayload(StrictModel):
    title: NEStr
    statement: NEStr
    map: StrategyMap
    source_agreement_ref: ExactRef | None = None
    source_proposal_ref: ExactRef | None = None


class StrategicJudgmentPayload(StrictModel):
    title: NEStr
    statement: NEStr
    strategy_ref: ExactRef
    unit_id: NEStr
    source_agreement_ref: ExactRef | None = None
    source_proposal_ref: ExactRef | None = None


class SignalPayload(StrictModel):
    title: NEStr
    kind: Literal["external", "ceo_insight", "dri_escalation", "internal"]
    description: NEStr
    source_refs: list[ExactRef] = Field(min_length=1)


class PotentialIssuePayload(StrictModel):
    title: NEStr
    summary: NEStr
    signal_refs: list[ExactRef] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct(self) -> "PotentialIssuePayload":
        _unique_refs(self.signal_refs)
        return self


class StrategicIssuePayload(StrictModel):
    title: NEStr
    summary: NEStr
    potential_issue_ref: ExactRef
    confirmation_reason: NEStr


class ResearchMemoPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    question: NEStr
    scope: NEStr
    expected_output: NEStr
    source_refs: list[ExactRef] = Field(default_factory=list)


class ResearchPlanPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    memo_ref: ExactRef
    human_work: list[NEStr] = Field(min_length=1)
    agent_work: list[NEStr] = Field(min_length=1)
    method: NEStr
    due_at: IsoDateTime


class ResearchReportPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    plan_ref: ExactRef
    findings: list[NEStr] = Field(min_length=1)
    conclusions: list[NEStr] = Field(min_length=1)
    limitations: list[NEStr] = Field(min_length=1)
    evidence_refs: list[ExactRef] = Field(min_length=1)


class MeetingRoundPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    report_ref: ExactRef
    round_number: int = Field(ge=1, strict=True)
    objective: NEStr
    material_refs: list[ExactRef] = Field(min_length=1)


class MinuteDifference(StrictModel):
    topic: NEStr
    ceo_account: NEStr
    dri_account: NEStr
    resolution: NEStr


class MeetingMinutesPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    meeting_ref: ExactRef
    account: Literal["ceo", "dri", "reconciled"]
    body: NEStr
    source_refs: list[ExactRef] = Field(min_length=1)
    differences: list[MinuteDifference] = Field(default_factory=list)


class StrategicAgreementPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    meeting_ref: ExactRef
    minutes_ref: ExactRef
    statement: NEStr
    meeting_goal_achieved: StrictBool
    reason: NEStr


class StrategyChange(StrictModel):
    scope: Literal["company"]
    target_ref: ExactRef | None = None
    payload: StrategyPayload


class JudgmentChange(StrictModel):
    scope: Literal["domain"]
    domain_id: CanonicalUUID
    target_ref: ExactRef | None = None
    payload: StrategicJudgmentPayload


class StrategyUpdateProposalPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    agreement_ref: ExactRef
    rationale: NEStr
    changes: list[StrategyChange | JudgmentChange] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_targets(self) -> "StrategyUpdateProposalPayload":
        targets = [(c.scope, c.target_ref.object_id if c.target_ref else getattr(c, "domain_id", "company")) for c in self.changes]
        if len(set(targets)) != len(targets):
            raise ValueError("an update may change each target only once")
        if sum(c.scope == "company" for c in self.changes) > 1:
            raise ValueError("an update contains at most one company Strategy")
        return self


class RecordSignalParams(StrictModel):
    domain_id: CanonicalUUID
    payload: SignalPayload


class OpenPotentialIssueParams(StrictModel):
    domain_id: CanonicalUUID
    payload: PotentialIssuePayload


class RevisePotentialIssueParams(StrictModel):
    payload: PotentialIssuePayload


class ConfirmStrategicIssueParams(StrictModel):
    reason: NEStr


class AssignResearchParams(StrictModel):
    dri_principal_id: CanonicalUUID
    ceo_agent_id: CanonicalUUID
    dri_agent_id: CanonicalUUID
    co_agent_id: CanonicalUUID

    @model_validator(mode="after")
    def different_agents(self) -> "AssignResearchParams":
        if len({self.ceo_agent_id, self.dri_agent_id, self.co_agent_id}) != 3:
            raise ValueError("CEO, DRI and coordination agents need distinct principals")
        return self


class PublishMemoParams(StrictModel):
    payload: ResearchMemoPayload


class ClarificationParams(StrictModel):
    memo_ref: ExactRef
    content: NEStr


class CheckMemoParams(StrictModel):
    memo_ref: ExactRef
    result: Literal["clear", "needs_clarification"]
    explanation: NEStr


class PublishResearchPlanParams(StrictModel):
    payload: ResearchPlanPayload


class PublishReportParams(StrictModel):
    payload: ResearchReportPayload


class SubmitReportParams(StrictModel):
    report_ref: ExactRef
    statement: NEStr


class PrecheckReportParams(StrictModel):
    report_ref: ExactRef
    result: Literal["pass", "return"]
    findings: list[NEStr] = Field(min_length=1)


class OpenMeetingParams(StrictModel):
    report_ref: ExactRef
    title: NEStr
    objective: NEStr
    material_refs: list[ExactRef] = Field(min_length=1)


class PublishMinutesParams(StrictModel):
    meeting_ref: ExactRef
    title: NEStr
    account: Literal["ceo", "dri"]
    body: NEStr
    source_refs: list[ExactRef] = Field(min_length=1)


class ReconcileMinutesParams(StrictModel):
    meeting_ref: ExactRef
    ceo_minutes_ref: ExactRef
    dri_minutes_ref: ExactRef
    title: NEStr
    body: NEStr
    differences: list[MinuteDifference]


class ConfirmMinutesParams(StrictModel):
    minutes_ref: ExactRef
    statement: NEStr


class ConfirmAgreementParams(StrictModel):
    minutes_ref: ExactRef
    title: NEStr
    statement: NEStr
    meeting_goal_achieved: StrictBool
    reason: NEStr


class DecideUpdateParams(StrictModel):
    agreement_ref: ExactRef
    needs_update: StrictBool
    reason: NEStr


class ProposeUpdateParams(StrictModel):
    payload: StrategyUpdateProposalPayload


class ReviewUpdateParams(StrictModel):
    proposal_ref: ExactRef
    accepted: StrictBool
    impact_level: Literal["company", "domain", "company_and_domain"]
    findings: list[NEStr] = Field(min_length=1)


class ConfirmUpdateParams(StrictModel):
    proposal_ref: ExactRef
    reason: NEStr


M1A_ACTION_PARAMS = {
    "m1a_record_signal": RecordSignalParams,
    "m1a_open_potential_issue": OpenPotentialIssueParams,
    "m1a_revise_potential_issue": RevisePotentialIssueParams,
    "m1a_confirm_strategic_issue": ConfirmStrategicIssueParams,
    "m1a_assign_research": AssignResearchParams,
    "m1a_publish_memo": PublishMemoParams,
    "m1a_record_clarification": ClarificationParams,
    "m1a_check_memo": CheckMemoParams,
    "m1a_direct_clarification": ClarificationParams,
    "m1a_publish_research_plan": PublishResearchPlanParams,
    "m1a_publish_report": PublishReportParams,
    "m1a_submit_report": SubmitReportParams,
    "m1a_precheck_report": PrecheckReportParams,
    "m1a_open_meeting": OpenMeetingParams,
    "m1a_publish_minutes": PublishMinutesParams,
    "m1a_reconcile_minutes": ReconcileMinutesParams,
    "m1a_confirm_minutes": ConfirmMinutesParams,
    "m1a_confirm_agreement": ConfirmAgreementParams,
    "m1a_decide_update": DecideUpdateParams,
    "m1a_propose_update": ProposeUpdateParams,
    "m1a_review_update": ReviewUpdateParams,
    "m1a_confirm_update": ConfirmUpdateParams,
}
M1A_ACTION_TARGETS = {action: {"StrategicIssue"} for action in M1A_ACTION_PARAMS}
M1A_ACTION_TARGETS.update({
    "m1a_record_signal": set(),
    "m1a_open_potential_issue": set(),
    "m1a_revise_potential_issue": {"PotentialIssue"},
    "m1a_confirm_strategic_issue": {"PotentialIssue"},
})
M1A_PAYLOAD_MODELS = {
    "Signal": SignalPayload,
    "PotentialIssue": PotentialIssuePayload,
    "StrategicIssue": StrategicIssuePayload,
    "ResearchMemo": ResearchMemoPayload,
    "ResearchPlan": ResearchPlanPayload,
    "ResearchReport": ResearchReportPayload,
    "MeetingRound": MeetingRoundPayload,
    "MeetingMinutes": MeetingMinutesPayload,
    "StrategicAgreement": StrategicAgreementPayload,
    "StrategyUpdateProposal": StrategyUpdateProposalPayload,
    "Strategy": StrategyPayload,
    "StrategicJudgment": StrategicJudgmentPayload,
}
