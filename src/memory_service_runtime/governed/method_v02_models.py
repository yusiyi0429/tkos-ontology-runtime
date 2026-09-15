"""Lifecycle additions. Frozen 0.1 schemas are never widened in place."""
from typing import Literal
from pydantic import Field, model_validator
from . import method_m1a_models as a, method_m1b_models as b
from .a2_models import CanonicalUUID, IsoDateTime, NEStr, StrictModel
from .method_models import METHOD_ACTION_PARAMS, METHOD_ACTION_TARGETS, METHOD_PAYLOAD_MODELS

CONTRACT_VERSION = "tkos.method/0.2"


class Classification(StrictModel):
    business_scope: Literal["strategic", "battlefield"]
    urgency: Literal["red", "yellow", "gray"]
    urgency_reason: NEStr | None = None

    @model_validator(mode="after")
    def red_reason(self):
        if self.urgency == "red" and not self.urgency_reason:
            raise ValueError("red urgency requires a reason")
        return self


class PotentialIssuePayload(Classification):
    title: NEStr
    summary: NEStr
    origin: Literal["signal", "direct", "period_review"]
    signal_refs: list[a.ExactRef] = Field(default_factory=list)
    source_refs: list[a.ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def provenance(self):
        a._unique_refs(self.signal_refs)
        a._unique_refs(self.source_refs)
        if self.origin == "signal":
            if not self.signal_refs:
                raise ValueError("signal origin requires signals")
        elif self.signal_refs or (self.origin == "period_review" and not self.source_refs):
            raise ValueError("direct origins must not fabricate signals; review origin needs sources")
        return self


class StrategicIssuePayload(Classification):
    title: NEStr
    summary: NEStr
    potential_issue_ref: a.ExactRef | None = None
    direct_source_refs: list[a.ExactRef] = Field(default_factory=list)
    confirmation_reason: NEStr


class OpenPotential(StrictModel):
    domain_id: CanonicalUUID
    payload: PotentialIssuePayload


class RevisePotential(StrictModel):
    payload: PotentialIssuePayload


class DirectIssue(StrictModel):
    domain_id: CanonicalUUID
    payload: StrategicIssuePayload

    @model_validator(mode="after")
    def direct(self):
        if self.payload.potential_issue_ref is not None:
            raise ValueError("direct creation does not invent a potential issue")
        return self


class SignalDisposition(StrictModel):
    reason: NEStr


class ResearchBriefPayload(StrictModel):
    title: NEStr
    issue_ref: a.ExactRef
    question: NEStr
    analysis: NEStr
    options: list[NEStr] = Field(min_length=1)
    limitations: list[NEStr] = Field(min_length=1)
    source_refs: list[a.ExactRef] = Field(min_length=1)


class PublishBrief(StrictModel):
    payload: ResearchBriefPayload


class ConfirmBrief(StrictModel):
    brief_ref: a.ExactRef
    reason: NEStr


class OpenMeeting(StrictModel):
    title: NEStr
    objective: NEStr
    report_ref: a.ExactRef | None = None
    brief_ref: a.ExactRef | None = None
    material_refs: list[a.ExactRef] = Field(min_length=1)

    @model_validator(mode="after")
    def one_basis(self):
        if (self.report_ref is None) == (self.brief_ref is None):
            raise ValueError("meeting requires exactly one report or brief")
        return self


class MeetingRoundPayload(OpenMeeting):
    issue_ref: a.ExactRef
    round_number: int = Field(ge=1, strict=True)


class StrategyUnit(a.StrategyUnit):
    unit_type: Literal["battlefield", "capability"]


class StrategyMap(a.StrategyMap):
    units: list[StrategyUnit] = Field(min_length=1)


class StrategyPayload(a.StrategyPayload):
    map: StrategyMap


class StrategyChange(a.StrategyChange):
    payload: StrategyPayload


class UpdateProposal(a.StrategyUpdateProposalPayload):
    changes: list[StrategyChange | a.JudgmentChange] = Field(min_length=1)


class ProposeUpdate(a.ProposeUpdateParams):
    payload: UpdateProposal


class ReviewWindowPayload(b.ReviewWindowPayload):
    feedback_deadline: IsoDateTime


class OpenWindow(b.OpenWindow):
    payload: ReviewWindowPayload


class ReopenCandidates(b.ReopenCandidates):
    feedback_deadline: IsoDateTime


ACTION_PARAMS = {**METHOD_ACTION_PARAMS,
    "m1a_open_potential_issue": OpenPotential, "m1a_revise_potential_issue": RevisePotential,
    "m1a_create_direct_issue": DirectIssue, "m1a_activate_signal": SignalDisposition,
    "m1a_archive_signal": SignalDisposition, "m1a_publish_brief": PublishBrief,
    "m1a_confirm_brief": ConfirmBrief, "m1a_open_meeting": OpenMeeting,
    "m1a_propose_update": ProposeUpdate,
    "m1b_open_window": OpenWindow, "m1b_reopen_candidates": ReopenCandidates,
    "m1b_reopen_window": ReopenCandidates}
ACTION_TARGETS = {**METHOD_ACTION_TARGETS, "m1a_create_direct_issue": frozenset(),
    "m1a_activate_signal": frozenset({"Signal"}), "m1a_archive_signal": frozenset({"Signal"}),
    "m1a_publish_brief": frozenset({"StrategicIssue"}), "m1a_confirm_brief": frozenset({"StrategicIssue"})}
PAYLOAD_MODELS = {**METHOD_PAYLOAD_MODELS, "PotentialIssue": PotentialIssuePayload,
    "StrategicIssue": StrategicIssuePayload, "ResearchBrief": ResearchBriefPayload,
    "MeetingRound": MeetingRoundPayload, "Strategy": StrategyPayload,
    "StrategyUpdateProposal": UpdateProposal, "ReviewWindow": ReviewWindowPayload}
OBJECT_TYPES = frozenset(PAYLOAD_MODELS) | {"EvidenceAsset"}
