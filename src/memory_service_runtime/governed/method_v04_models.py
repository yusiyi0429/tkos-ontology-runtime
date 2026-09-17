"""Strict ``tkos.method/0.4`` schemas: formal Agreement, paired Strategy.

Architecture, multi-PCO M1B windows and canonical State.

These models validate shape only; current appointments, exact versions, roster
membership and CAS are checked in ``method_v04`` inside the governed
transaction.  Older Method schemas are frozen and never widened in place.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, model_validator

from .a2_models import CanonicalUUID, IsoDateTime, NEStr, StrictModel
from .method_common_models import METHOD_COMMON_PARAMS, METHOD_COMMON_TARGETS, RunPayload
from .method_m1a_models import ExactRef
from .method_m1b_models import Period, distinct

CONTRACT_VERSION = "tkos.method/0.4"


# --------------------------------------------------------------- M1A: issue


class StrategicIssuePayload(StrictModel):
    title: NEStr
    summary: NEStr
    core_question: NEStr
    business_scope: NEStr
    urgency: Literal["red", "yellow", "green"]
    urgency_reason: NEStr | None = None
    source_refs: list[ExactRef] = Field(default_factory=list)
    strategy_ref: ExactRef | None = None
    architecture_ref: ExactRef | None = None
    reframe_of_ref: ExactRef | None = None

    @model_validator(mode="after")
    def consistent(self) -> "StrategicIssuePayload":
        from . import method_m1a_models as a
        a._unique_refs(self.source_refs)
        if (self.strategy_ref is None) != (self.architecture_ref is None):
            raise ValueError("Strategy and Architecture bases appear together")
        if self.urgency == "red" and not self.urgency_reason:
            raise ValueError("red urgency requires a reason")
        return self


class CreateIssue(StrictModel):
    domain_id: CanonicalUUID
    payload: StrategicIssuePayload


class ReframeIssue(StrictModel):
    payload: StrategicIssuePayload


class AssociateIssue(StrictModel):
    source_refs: list[ExactRef] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> "AssociateIssue":
        distinct([(r.object_id, r.revision_id) for r in self.source_refs], "source_refs")
        return self


class IssueParticipant(StrictModel):
    principal_id: CanonicalUUID
    assignment_id: CanonicalUUID
    personal_agent_id: CanonicalUUID | None = None
    research: StrictBool = False


class SetParticipants(StrictModel):
    participants: list[IssueParticipant] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> "SetParticipants":
        distinct([p.principal_id for p in self.participants], "issue participants")
        distinct([p.assignment_id for p in self.participants], "issue participant assignments")
        distinct([p.personal_agent_id for p in self.participants if p.personal_agent_id], "issue personal agents")
        return self


# ----------------------------------------------------------- M1A: agreement


class AgreementSigner(StrictModel):
    principal_id: CanonicalUUID
    assignment_id: CanonicalUUID
    personal_agent_id: CanonicalUUID | None = None


class AgreementPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    statement: NEStr
    participants: list[AgreementSigner] = Field(min_length=1)
    evidence_refs: list[ExactRef] = Field(default_factory=list)
    no_change: StrictBool = False

    @model_validator(mode="after")
    def unique(self) -> "AgreementPayload":
        from . import method_m1a_models as a
        a._unique_refs(self.evidence_refs)
        distinct([p.principal_id for p in self.participants], "agreement signers")
        distinct([p.assignment_id for p in self.participants], "agreement signer assignments")
        distinct([p.personal_agent_id for p in self.participants if p.personal_agent_id], "agreement personal agents")
        return self


class DraftAgreement(StrictModel):
    payload: AgreementPayload


class ReviseAgreement(StrictModel):
    payload: AgreementPayload


class ConfirmAgreement(StrictModel):
    statement: NEStr


# ------------------------------------------- M1A: paired Strategy/Architecture


class RequiredCapability(StrictModel):
    """Strategy analysis semantics assigned to a Domain primary responsibility."""

    capability_id: NEStr
    name: NEStr
    definition: NEStr
    primary_domain_id: NEStr
    analysis_fields: list[NEStr] = Field(default_factory=list)


class StrategyPayload(StrictModel):
    title: NEStr
    statement: NEStr
    required_capabilities: list[RequiredCapability] = Field(default_factory=list)
    source_agreement_ref: ExactRef | None = None
    source_proposal_ref: ExactRef | None = None

    @model_validator(mode="after")
    def unique(self) -> "StrategyPayload":
        distinct([c.capability_id for c in self.required_capabilities], "capability IDs")
        return self


class BattlefieldDefinition(StrictModel):
    unit_id: NEStr
    name: NEStr
    definition: NEStr
    strategic_basis: list[NEStr] = Field(min_length=1)
    boundary: NEStr
    interfaces: list[NEStr] = Field(default_factory=list)
    # Explicit business-scope mapping only; it never grants access.  Present
    # when the Battlefield is used as an LTCO/PCO primary Scope.
    auth_domain_id: CanonicalUUID | None = None
    current_dri_principal_id: CanonicalUUID | None = None


class DomainDefinition(StrictModel):
    unit_id: NEStr
    name: NEStr
    definition: NEStr
    responsibility: NEStr
    auth_domain_id: CanonicalUUID
    current_dri_principal_id: CanonicalUUID | None = None


class ArchitectureDefinition(StrictModel):
    title: NEStr
    battlefields: list[BattlefieldDefinition] = Field(min_length=1)
    domains: list[DomainDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> "ArchitectureDefinition":
        ids = [u.unit_id for u in self.battlefields] + [u.unit_id for u in self.domains]
        distinct(ids, "architecture stable definition IDs")
        return self


class StrategicArchitecturePayload(ArchitectureDefinition):
    strategy_ref: ExactRef | None = None
    source_agreement_ref: ExactRef | None = None
    source_proposal_ref: ExactRef | None = None


class CompanyChange(StrictModel):
    """One exact company change; unchanged objects keep their revision explicitly."""

    strategy_target_ref: ExactRef | None = None
    architecture_target_ref: ExactRef | None = None
    strategy: StrategyPayload | None = None
    architecture: StrategicArchitecturePayload | None = None
    applicability_rationale: NEStr

    @model_validator(mode="after")
    def exact_forms(self) -> "CompanyChange":
        if self.strategy is None and self.architecture is None:
            raise ValueError("a company change names at least one changed object")
        if self.strategy is None and self.strategy_target_ref is None:
            raise ValueError("an unchanged Strategy requires its exact retained revision")
        if self.architecture is None and self.architecture_target_ref is None:
            raise ValueError("an unchanged Architecture requires its exact retained revision")
        for payload in (self.strategy, self.architecture):
            if payload is not None and payload.source_agreement_ref is not None:
                raise ValueError("update provenance is assigned by the confirming transaction")
            if payload is not None and payload.source_proposal_ref is not None:
                raise ValueError("update provenance is assigned by the confirming transaction")
        if self.architecture is not None and self.architecture.strategy_ref is not None:
            raise ValueError("the Architecture Strategy binding is assigned by the confirming transaction")
        return self


class UpdateProposalPayload(StrictModel):
    title: NEStr
    issue_ref: ExactRef
    agreement_ref: ExactRef
    rationale: NEStr
    change: CompanyChange


class ProposeUpdate(StrictModel):
    payload: UpdateProposalPayload


class ReviewUpdate(StrictModel):
    accepted: StrictBool
    findings: list[NEStr] = Field(min_length=1)


class ConfirmUpdate(StrictModel):
    statement: NEStr


# ---------------------------------------------- B4: M1B scope goals, windows


class LTCOPayload(StrictModel):
    title: NEStr
    primary_scope_id: NEStr
    period: Period
    architecture_ref: ExactRef
    strategy_ref: ExactRef
    result_statement: NEStr
    criteria: list[NEStr] = Field(min_length=1)
    boundary: NEStr
    horizon: NEStr
    why: NEStr
    baseline_refs: list[ExactRef] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> "LTCOPayload":
        distinct([(r.object_id, r.revision_id) for r in self.baseline_refs], "LTCO baselines")
        return self


class ProposeLTCO(StrictModel):
    domain_id: CanonicalUUID
    payload: LTCOPayload


class ReviseLTCO(StrictModel):
    payload: LTCOPayload
    response: NEStr


class ConfirmLTCO(StrictModel):
    statement: NEStr


class PCOPayload(StrictModel):
    title: NEStr
    primary_scope_id: NEStr
    period: Period
    parent_ltco_ref: ExactRef
    architecture_ref: ExactRef
    strategy_ref: ExactRef
    current_reality: NEStr
    result_statement: NEStr
    criteria: list[NEStr] = Field(min_length=1)
    expected_lt_advance: NEStr
    why: NEStr


class DraftPCO(StrictModel):
    domain_id: CanonicalUUID
    payload: PCOPayload


class RevisePCO(StrictModel):
    payload: PCOPayload


class MissionPayload(StrictModel):
    title: NEStr
    owner_principal_id: CanonicalUUID
    primary_scope_id: NEStr
    parent_pco_ref: ExactRef
    why: NEStr
    requirements: list[NEStr] = Field(min_length=1)
    criteria: list[NEStr] = Field(min_length=1)
    evidence_refs: list[ExactRef] = Field(default_factory=list)
    period: Period

    @model_validator(mode="after")
    def unique(self) -> "MissionPayload":
        distinct([(r.object_id, r.revision_id) for r in self.evidence_refs], "Mission evidence")
        return self


class DraftMission(StrictModel):
    domain_id: CanonicalUUID
    payload: MissionPayload


class ReviseMission(StrictModel):
    payload: MissionPayload


class WindowParticipant(StrictModel):
    principal_id: CanonicalUUID
    assignment_id: CanonicalUUID
    personal_agent_id: CanonicalUUID | None = None


class ReviewWindowPayload(StrictModel):
    title: NEStr
    period: Period
    architecture_ref: ExactRef
    strategy_ref: ExactRef
    ltco_refs: list[ExactRef] = Field(min_length=1)
    pco_refs: list[ExactRef] = Field(min_length=1)
    mission_refs: list[ExactRef] = Field(min_length=1)
    participants: list[WindowParticipant] = Field(min_length=1)
    previous_window_ref: ExactRef | None = None

    @model_validator(mode="after")
    def unique(self) -> "ReviewWindowPayload":
        distinct([r.object_id for r in self.ltco_refs], "window LTCO objects")
        distinct([r.object_id for r in self.pco_refs], "window PCO objects")
        distinct([r.object_id for r in self.mission_refs], "window Mission objects")
        distinct([p.principal_id for p in self.participants], "window principals")
        distinct([p.assignment_id for p in self.participants], "window assignments")
        distinct([p.personal_agent_id for p in self.participants if p.personal_agent_id], "window personal agents")
        return self


class OpenWindow(StrictModel):
    domain_id: CanonicalUUID
    payload: ReviewWindowPayload


class Comment(StrictModel):
    target_ref: ExactRef
    content: NEStr


class ReplaceComment(StrictModel):
    target_ref: ExactRef
    content: NEStr
    replaces_record_id: CanonicalUUID


class WithdrawComment(StrictModel):
    review_record_id: CanonicalUUID
    reason: NEStr


class AssistReview(StrictModel):
    target_ref: ExactRef
    owner_principal_id: CanonicalUUID
    analysis: NEStr
    source_refs: list[ExactRef] = Field(default_factory=list)
    generation_version: NEStr


class CloseWindow(StrictModel):
    reason: NEStr


class OpinionDisposition(StrictModel):
    review_record_id: CanonicalUUID
    decision: Literal["adopted", "partially_adopted", "not_adopted", "unresolved"]
    rationale: NEStr


class UnresolvedDifference(StrictModel):
    topic: NEStr
    statement: NEStr
    critical: StrictBool


class CandidatePCO(StrictModel):
    """Server binds the generated PCO revision atomically, never a guessed ID."""

    object_id: CanonicalUUID
    payload: PCOPayload


class CandidateMission(StrictModel):
    object_id: CanonicalUUID
    title: NEStr
    owner_principal_id: CanonicalUUID
    primary_scope_id: NEStr
    why: NEStr
    requirements: list[NEStr] = Field(min_length=1)
    criteria: list[NEStr] = Field(min_length=1)
    evidence_refs: list[ExactRef] = Field(default_factory=list)
    period: Period

    @model_validator(mode="after")
    def unique(self) -> "CandidateMission":
        distinct([(r.object_id, r.revision_id) for r in self.evidence_refs], "candidate Mission evidence")
        return self


class ResolveWindow(StrictModel):
    title: NEStr
    pcos: list[CandidatePCO] = Field(min_length=1)
    missions: list[CandidateMission] = Field(min_length=1)
    dispositions: list[OpinionDisposition] = Field(default_factory=list)
    unresolved_differences: list[UnresolvedDifference] = Field(default_factory=list)
    summary: NEStr

    @model_validator(mode="after")
    def unique(self) -> "ResolveWindow":
        distinct([p.object_id for p in self.pcos], "candidate PCO IDs")
        distinct([m.object_id for m in self.missions], "candidate Mission IDs")
        distinct([d.review_record_id for d in self.dispositions], "opinion dispositions")
        return self


class CommitCandidate(StrictModel):
    responsibility_ref: ExactRef
    statement: NEStr


class ActivateCandidates(StrictModel):
    statement: NEStr
    notes: list[NEStr] = Field(default_factory=list)


class ReopenCandidates(StrictModel):
    reason: NEStr
    title: NEStr
    participants: list[WindowParticipant] | None = Field(default=None, min_length=1)
    pco_refs: list[ExactRef] | None = None
    mission_refs: list[ExactRef] | None = None
    rebase_strategy_ref: ExactRef | None = None
    rebase_architecture_ref: ExactRef | None = None

    @model_validator(mode="after")
    def consistent(self) -> "ReopenCandidates":
        if (self.pco_refs is None) != (self.mission_refs is None):
            raise ValueError("reopen membership requires PCO and Mission references together")
        if (self.rebase_strategy_ref is None) != (self.rebase_architecture_ref is None):
            raise ValueError("rebase Strategy and Architecture references appear together")
        if self.participants:
            distinct([p.principal_id for p in self.participants], "window principals")
            distinct([p.assignment_id for p in self.participants], "window assignments")
        return self


class PeriodReviewPayload(StrictModel):
    review_id: NEStr
    title: NEStr
    period: Period
    target_refs: list[ExactRef] = Field(min_length=1)
    state_refs: list[ExactRef] = Field(min_length=1)
    fact_refs: list[ExactRef] = Field(default_factory=list)
    findings: list[NEStr] = Field(min_length=1)
    learnings: list[NEStr] = Field(default_factory=list)
    implications: list[NEStr] = Field(default_factory=list)
    generation_version: NEStr

    @model_validator(mode="after")
    def unique(self) -> "PeriodReviewPayload":
        for name in ("target_refs", "state_refs", "fact_refs"):
            distinct([(r.object_id, r.revision_id) for r in getattr(self, name)], name)
        return self


class GenerateReview(StrictModel):
    domain_id: CanonicalUUID
    payload: PeriodReviewPayload


class RegenerateReview(StrictModel):
    payload: PeriodReviewPayload


# --------------------------------------------------- State and ordinary problem


class StatePayload(StrictModel):
    subject_ref: ExactRef
    as_of: IsoDateTime
    summary: NEStr
    rag: Literal["green", "yellow", "red", "unknown"]
    baseline_refs: list[ExactRef] = Field(min_length=1)
    evidence_refs: list[ExactRef] = Field(default_factory=list)
    data_gaps: list[NEStr] = Field(default_factory=list)
    generation_version: NEStr

    @model_validator(mode="after")
    def evidence(self) -> "StatePayload":
        from . import method_m1a_models as a
        a._unique_refs(self.baseline_refs)
        a._unique_refs(self.evidence_refs)
        if not self.evidence_refs and (self.rag != "unknown" or not self.data_gaps):
            raise ValueError("Absent evidence requires Unknown and explicit data gaps")
        return self


class ProposeState(StrictModel):
    domain_id: CanonicalUUID
    payload: StatePayload
    previous_state_ref: ExactRef | None = None


class ConfirmState(StrictModel):
    reason: NEStr
    summary: NEStr | None = None
    rag: Literal["green", "yellow", "red", "unknown"] | None = None

    @model_validator(mode="after")
    def override(self) -> "ConfirmState":
        if (self.summary is None) != (self.rag is None):
            raise ValueError("Override needs both summary and rating")
        return self


class ProblemPayload(StrictModel):
    state_ref: ExactRef
    core_question: NEStr
    statement: NEStr
    why_material: NEStr
    level: Literal["mission", "domain", "company", "strategic"]
    responsible_assignment_id: CanonicalUUID
    evidence_refs: list[ExactRef] = Field(default_factory=list)
    decision_deadline: IsoDateTime | None = None


class OpenProblem(StrictModel):
    domain_id: CanonicalUUID
    payload: ProblemPayload


class ReviseProblem(StrictModel):
    payload: ProblemPayload


class CloseProblem(StrictModel):
    disposition: Literal["resolved", "no_further_action"]
    reason: NEStr
    evidence_refs: list[ExactRef] = Field(min_length=1)


class TransferProblem(StrictModel):
    problem_ref: ExactRef
    reason: NEStr
    evidence_refs: list[ExactRef] = Field(default_factory=list)


class CandidateSetPayload(StrictModel):
    title: NEStr
    window_ref: ExactRef
    strategy_ref: ExactRef
    architecture_ref: ExactRef
    ltco_refs: list[ExactRef] = Field(min_length=1)
    target_refs: list[ExactRef] = Field(min_length=2)
    resolution_record_id: CanonicalUUID
    dispositions: list[OpinionDisposition] = Field(default_factory=list)
    unresolved_differences: list[UnresolvedDifference] = Field(default_factory=list)
    notes: list[NEStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> "CandidateSetPayload":
        distinct([r.object_id for r in self.target_refs], "candidate target objects")
        return self


PAYLOAD_MODELS = {
    "StrategicIssue": StrategicIssuePayload,
    "StrategicAgreement": AgreementPayload,
    "Strategy": StrategyPayload,
    "StrategicArchitecture": StrategicArchitecturePayload,
    "StrategyUpdateProposal": UpdateProposalPayload,
    "LTCO": LTCOPayload,
    "PCO": PCOPayload,
    "Mission": MissionPayload,
    "ReviewWindow": ReviewWindowPayload,
    "CandidateSet": CandidateSetPayload,
    "OperatingState": StatePayload,
    "OperatingProblem": ProblemPayload,
    "PeriodReview": PeriodReviewPayload,
    "MethodRun": RunPayload,
}

ACTION_PARAMS = {
    **METHOD_COMMON_PARAMS,
    "m1a_create_issue": CreateIssue,
    "m1a_reframe_issue": ReframeIssue,
    "m1a_associate_issue": AssociateIssue,
    "m1a_set_participants": SetParticipants,
    "m1a_draft_agreement": DraftAgreement,
    "m1a_revise_agreement": ReviseAgreement,
    "m1a_confirm_agreement": ConfirmAgreement,
    "m1a_propose_update": ProposeUpdate,
    "m1a_review_update": ReviewUpdate,
    "m1a_confirm_update": ConfirmUpdate,
    "m1a_transfer_problem": TransferProblem,
    "m1b_propose_ltco": ProposeLTCO,
    "m1b_revise_ltco": ReviseLTCO,
    "m1b_confirm_ltco": ConfirmLTCO,
    "m1b_draft_pco": DraftPCO,
    "m1b_revise_pco": RevisePCO,
    "m1b_draft_mission": DraftMission,
    "m1b_revise_mission": ReviseMission,
    "m1b_open_window": OpenWindow,
    "m1b_comment": Comment,
    "m1b_replace_comment": ReplaceComment,
    "m1b_withdraw_comment": WithdrawComment,
    "m1b_assist_review": AssistReview,
    "m1b_close_window": CloseWindow,
    "m1b_resolve_window": ResolveWindow,
    "m1b_commit_candidate": CommitCandidate,
    "m1b_activate_candidates": ActivateCandidates,
    "m1b_reopen_candidates": ReopenCandidates,
    "m1b_reopen_window": ReopenCandidates,
    "m1b_generate_review": GenerateReview,
    "m1b_regenerate_review": RegenerateReview,
    "method_propose_state": ProposeState,
    "method_confirm_state": ConfirmState,
    "method_open_problem": OpenProblem,
    "method_revise_problem": ReviseProblem,
    "method_close_problem": CloseProblem,
}

ACTION_TARGETS = {
    **METHOD_COMMON_TARGETS,
    "m1a_create_issue": frozenset(),
    "m1a_reframe_issue": frozenset({"StrategicIssue"}),
    "m1a_associate_issue": frozenset({"StrategicIssue"}),
    "m1a_set_participants": frozenset({"StrategicIssue"}),
    "m1a_draft_agreement": frozenset({"StrategicIssue"}),
    "m1a_revise_agreement": frozenset({"StrategicAgreement"}),
    "m1a_confirm_agreement": frozenset({"StrategicAgreement"}),
    "m1a_propose_update": frozenset({"StrategicIssue"}),
    "m1a_review_update": frozenset({"StrategyUpdateProposal"}),
    "m1a_confirm_update": frozenset({"StrategyUpdateProposal"}),
    "m1a_transfer_problem": frozenset({"StrategicIssue"}),
    "m1b_propose_ltco": frozenset(),
    "m1b_revise_ltco": frozenset({"LTCO"}),
    "m1b_confirm_ltco": frozenset({"LTCO"}),
    "m1b_draft_pco": frozenset(),
    "m1b_revise_pco": frozenset({"PCO"}),
    "m1b_draft_mission": frozenset(),
    "m1b_revise_mission": frozenset({"Mission"}),
    "m1b_open_window": frozenset(),
    "m1b_comment": frozenset({"ReviewWindow"}),
    "m1b_replace_comment": frozenset({"ReviewWindow"}),
    "m1b_withdraw_comment": frozenset({"ReviewWindow"}),
    "m1b_assist_review": frozenset({"ReviewWindow"}),
    "m1b_close_window": frozenset({"ReviewWindow"}),
    "m1b_resolve_window": frozenset({"ReviewWindow"}),
    "m1b_commit_candidate": frozenset({"CandidateSet"}),
    "m1b_activate_candidates": frozenset({"CandidateSet"}),
    "m1b_reopen_candidates": frozenset({"CandidateSet"}),
    "m1b_reopen_window": frozenset({"ReviewWindow"}),
    "m1b_generate_review": frozenset(),
    "m1b_regenerate_review": frozenset({"PeriodReview"}),
    "method_propose_state": frozenset(),
    "method_confirm_state": frozenset({"OperatingState"}),
    "method_open_problem": frozenset(),
    "method_revise_problem": frozenset({"OperatingProblem"}),
    "method_close_problem": frozenset({"OperatingProblem"}),
}

OBJECT_TYPES = frozenset(PAYLOAD_MODELS) | {"EvidenceAsset"}

# Human actions offered to the governance workbench; Agent-only drafting stays out.
HUMAN_ACTIONS = frozenset({
    "m1a_set_participants", "m1a_confirm_agreement", "m1a_confirm_update",
    "m1b_confirm_ltco", "m1b_comment", "m1b_replace_comment", "m1b_withdraw_comment",
    "m1b_commit_candidate", "m1b_activate_candidates", "m1b_reopen_candidates",
    "m1b_reopen_window", "method_confirm_state", "method_open_problem",
    "method_revise_problem", "method_close_problem",
})
AGENT_ACTIONS = frozenset(set(ACTION_PARAMS) - HUMAN_ACTIONS - {
    "method_open_run", "method_attach_run", "method_pause_run", "method_resume_run",
    "method_record_attempt",
})
