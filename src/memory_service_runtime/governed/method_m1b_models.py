"""Strict M1B L5 revision 837 contract for tkos.method/0.1.

Analysis (PeriodReview and LTCOReviewAdvice), human review records, business
content, and window/candidate state have deliberately separate representations.
These models validate shape only; current identity, exact references, source
strategy, and lifecycle are checked in method_m1b under the governance lock.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .a2_models import CanonicalUUID, IsoDateTime, NEStr, Sha256Hex, StrictModel


class ExactRef(StrictModel):
    object_id: CanonicalUUID
    revision_id: CanonicalUUID
    payload_hash: Sha256Hex


class Period(StrictModel):
    start: IsoDateTime
    end: IsoDateTime

    @model_validator(mode="after")
    def ordered(self) -> "Period":
        from datetime import datetime
        if datetime.fromisoformat(self.start) >= datetime.fromisoformat(self.end):
            raise ValueError("period start must precede end")
        return self


def distinct(values: list, name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


class FactSubject(StrictModel):
    """An exact object/outcome reference, or an explicitly named company topic."""
    object_id: CanonicalUUID | None = None
    revision_id: CanonicalUUID | None = None
    payload_hash: Sha256Hex | None = None
    outcome_id: NEStr | None = None
    topic: NEStr | None = None

    @model_validator(mode="after")
    def one_form(self) -> "FactSubject":
        exact = (self.object_id, self.revision_id, self.payload_hash)
        if self.topic is not None:
            if any(value is not None for value in exact) or self.outcome_id is not None:
                raise ValueError("topic subject cannot contain object fields")
        elif not all(value is not None for value in exact):
            raise ValueError("object subjects require an exact revision reference")
        return self


class BusinessFactPayload(StrictModel):
    fact_id: NEStr
    subject_ref: FactSubject
    as_of: IsoDateTime
    metric: NEStr
    value: JsonValue
    unit: NEStr | None = None
    source_ref: ExactRef
    corrects_ref: ExactRef | None = None
    correction_reason: NEStr | None = None

    @model_validator(mode="after")
    def correction(self) -> "BusinessFactPayload":
        if (self.corrects_ref is None) != (self.correction_reason is None):
            raise ValueError("correction reference and reason must appear together")
        return self


class PeriodReviewPayload(StrictModel):
    review_id: NEStr
    title: NEStr
    period: Period
    target_refs: list[ExactRef] = Field(min_length=1)
    fact_refs: list[ExactRef] = Field(default_factory=list)
    findings: list[NEStr] = Field(min_length=1)
    learnings: list[NEStr] = Field(default_factory=list)
    implications: list[NEStr] = Field(default_factory=list)
    generation_version: NEStr

    @model_validator(mode="after")
    def unique_refs(self) -> "PeriodReviewPayload":
        for name in ("target_refs", "fact_refs"):
            distinct([(ref.object_id, ref.revision_id) for ref in getattr(self, name)], name)
        return self


class LTCOOutcome(StrictModel):
    outcome_id: NEStr
    unit_id: NEStr
    title: NEStr
    result_statement: NEStr
    criteria: list[NEStr] = Field(min_length=1)


class LTCOPayload(StrictModel):
    title: NEStr
    period: Period
    strategy_ref: ExactRef
    owner_principal_id: CanonicalUUID
    outcomes: list[LTCOOutcome] = Field(min_length=1)
    advice_ref: ExactRef | None = None

    @model_validator(mode="after")
    def unique_outcomes(self) -> "LTCOPayload":
        distinct([outcome.outcome_id for outcome in self.outcomes], "outcome_id")
        return self


class LTCOReviewAdvicePayload(StrictModel):
    title: NEStr
    period_review_ref: ExactRef
    strategy_ref: ExactRef
    ltco_ref: ExactRef | None = None
    recommendation: Literal["create", "retain", "revise"]
    observations: list[NEStr] = Field(min_length=1)
    generation_version: NEStr


class PCOOutcome(StrictModel):
    outcome_id: NEStr
    unit_id: NEStr
    title: NEStr
    result_statement: NEStr
    criteria: list[NEStr] = Field(min_length=1)
    dri_principal_id: CanonicalUUID


class PCOPayload(StrictModel):
    title: NEStr
    period: Period
    strategy_ref: ExactRef
    ltco_ref: ExactRef
    unit_outcomes: list[PCOOutcome] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_outcomes(self) -> "PCOPayload":
        distinct([outcome.outcome_id for outcome in self.unit_outcomes], "outcome_id")
        return self


class OutcomeRef(ExactRef):
    outcome_id: NEStr


class MissionSupport(StrictModel):
    outcome_ref: OutcomeRef
    contribution: NEStr


class MissionPayload(StrictModel):
    title: NEStr
    pco_ref: ExactRef
    owner_principal_id: CanonicalUUID
    participants: list[CanonicalUUID] = Field(default_factory=list)
    supports: list[MissionSupport] = Field(min_length=1)
    deliverable: NEStr
    acceptance_criteria: list[NEStr] = Field(min_length=1)
    boundary: NEStr
    hard_deadline: IsoDateTime

    @model_validator(mode="after")
    def consistent(self) -> "MissionPayload":
        distinct(self.participants, "participants")
        if self.owner_principal_id in self.participants:
            raise ValueError("Mission owner must not be repeated in participants")
        distinct([s.outcome_ref.outcome_id for s in self.supports], "supported outcomes")
        expected = self.pco_ref.model_dump()
        for support in self.supports:
            if support.outcome_ref.model_dump(exclude={"outcome_id"}) != expected:
                raise ValueError("every support must reference the exact pco_ref revision")
        return self


class WindowParticipant(StrictModel):
    principal_id: CanonicalUUID
    assignment_id: CanonicalUUID
    personal_agent_id: CanonicalUUID | None = None


class ReviewWindowPayload(StrictModel):
    title: NEStr
    period: Period
    strategy_ref: ExactRef
    ltco_ref: ExactRef
    target_refs: list[ExactRef] = Field(min_length=2)
    participants: list[WindowParticipant] = Field(min_length=1)
    previous_window_ref: ExactRef | None = None

    @model_validator(mode="after")
    def unique(self) -> "ReviewWindowPayload":
        distinct([ref.object_id for ref in self.target_refs], "window target objects")
        distinct([p.principal_id for p in self.participants], "window principals")
        distinct([p.assignment_id for p in self.participants], "window assignments")
        agents = [p.personal_agent_id for p in self.participants if p.personal_agent_id]
        distinct(agents, "window personal agents")
        return self


class OpinionDisposition(StrictModel):
    review_record_id: CanonicalUUID
    decision: Literal["adopted", "partially_adopted", "not_adopted", "unresolved"]
    rationale: NEStr


class CandidateSetPayload(StrictModel):
    title: NEStr
    window_ref: ExactRef
    strategy_ref: ExactRef
    ltco_ref: ExactRef
    target_refs: list[ExactRef] = Field(min_length=2)
    resolution_record_id: CanonicalUUID
    dispositions: list[OpinionDisposition] = Field(default_factory=list)
    remaining_differences: list[NEStr] = Field(default_factory=list)


class CreateFact(StrictModel):
    domain_id: CanonicalUUID
    payload: BusinessFactPayload


class CorrectFact(StrictModel):
    payload: BusinessFactPayload


class GenerateReview(StrictModel):
    domain_id: CanonicalUUID
    payload: PeriodReviewPayload


class RegenerateReview(StrictModel):
    payload: PeriodReviewPayload


class AdviseLTCO(StrictModel):
    domain_id: CanonicalUUID
    payload: LTCOReviewAdvicePayload


class ProposeLTCO(StrictModel):
    domain_id: CanonicalUUID
    payload: LTCOPayload


class ReviseLTCO(StrictModel):
    payload: LTCOPayload
    response: NEStr


class Reason(StrictModel):
    reason: NEStr


class Decision(StrictModel):
    reason: NEStr


class DraftPCO(StrictModel):
    domain_id: CanonicalUUID
    payload: PCOPayload


class RevisePCO(StrictModel):
    payload: PCOPayload


class DraftMission(StrictModel):
    domain_id: CanonicalUUID
    payload: MissionPayload


class ReviseMission(StrictModel):
    payload: MissionPayload


class OpenWindow(StrictModel):
    domain_id: CanonicalUUID
    payload: ReviewWindowPayload


class Comment(StrictModel):
    target_ref: ExactRef
    content: NEStr
    replaces_record_id: CanonicalUUID | None = None


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


class CandidateSupport(StrictModel):
    outcome_id: NEStr
    contribution: NEStr


class CandidateMission(StrictModel):
    """Server binds the generated PCO revision atomically, never a guessed ID."""
    object_id: CanonicalUUID
    title: NEStr
    owner_principal_id: CanonicalUUID
    participants: list[CanonicalUUID] = Field(default_factory=list)
    supports: list[CandidateSupport] = Field(min_length=1)
    deliverable: NEStr
    acceptance_criteria: list[NEStr] = Field(min_length=1)
    boundary: NEStr
    hard_deadline: IsoDateTime

    @model_validator(mode="after")
    def unique(self) -> "CandidateMission":
        distinct(self.participants, "Mission participants")
        if self.owner_principal_id in self.participants:
            raise ValueError("Mission owner must not be repeated in participants")
        distinct([s.outcome_id for s in self.supports], "supported outcomes")
        return self


class ResolveWindow(StrictModel):
    title: NEStr
    pco_payload: PCOPayload
    missions: list[CandidateMission] = Field(min_length=1)
    dispositions: list[OpinionDisposition] = Field(default_factory=list)
    remaining_differences: list[NEStr] = Field(default_factory=list)
    summary: NEStr

    @model_validator(mode="after")
    def unique(self) -> "ResolveWindow":
        distinct([m.object_id for m in self.missions], "candidate Mission IDs")
        distinct([d.review_record_id for d in self.dispositions], "opinion dispositions")
        return self


class ReopenCandidates(StrictModel):
    reason: NEStr
    title: NEStr
    participants: list[WindowParticipant] | None = Field(default=None, min_length=1)
    rebase_strategy_ref: ExactRef | None = None
    rebase_ltco_ref: ExactRef | None = None

    @model_validator(mode="after")
    def unique(self) -> "ReopenCandidates":
        if (self.rebase_strategy_ref is None) != (self.rebase_ltco_ref is None):
            raise ValueError("rebase strategy and LTCO references must appear together")
        if self.participants:
            distinct([p.principal_id for p in self.participants], "window principals")
            distinct([p.assignment_id for p in self.participants], "window assignments")
        return self


M1B_PAYLOAD_MODELS = {
    "BusinessFact": BusinessFactPayload,
    "PeriodReview": PeriodReviewPayload,
    "LTCOReviewAdvice": LTCOReviewAdvicePayload,
    "LTCO": LTCOPayload,
    "PCO": PCOPayload,
    "Mission": MissionPayload,
    "ReviewWindow": ReviewWindowPayload,
    "CandidateSet": CandidateSetPayload,
}
M1B_ACTION_PARAMS = {
    "m1b_record_fact": CreateFact,
    "m1b_correct_fact": CorrectFact,
    "m1b_generate_review": GenerateReview,
    "m1b_regenerate_review": RegenerateReview,
    "m1b_advise_ltco": AdviseLTCO,
    "m1b_propose_ltco": ProposeLTCO,
    "m1b_revise_ltco": ReviseLTCO,
    "m1b_return_ltco": Reason,
    "m1b_confirm_ltco": Decision,
    "m1b_draft_pco": DraftPCO,
    "m1b_revise_pco": RevisePCO,
    "m1b_draft_mission": DraftMission,
    "m1b_revise_mission": ReviseMission,
    "m1b_open_window": OpenWindow,
    "m1b_comment": Comment,
    "m1b_withdraw_comment": WithdrawComment,
    "m1b_assist_review": AssistReview,
    "m1b_close_window": CloseWindow,
    "m1b_resolve_window": ResolveWindow,
    "m1b_confirm_candidates": Decision,
    "m1b_reopen_candidates": ReopenCandidates,
    "m1b_reopen_window": ReopenCandidates,
}
M1B_ACTION_TARGETS = {
    "m1b_record_fact": frozenset(),
    "m1b_correct_fact": frozenset({"BusinessFact"}),
    "m1b_generate_review": frozenset(),
    "m1b_regenerate_review": frozenset({"PeriodReview"}),
    "m1b_advise_ltco": frozenset(),
    "m1b_propose_ltco": frozenset(),
    "m1b_revise_ltco": frozenset({"LTCO"}),
    "m1b_return_ltco": frozenset({"LTCO"}),
    "m1b_confirm_ltco": frozenset({"LTCO"}),
    "m1b_draft_pco": frozenset(),
    "m1b_revise_pco": frozenset({"PCO"}),
    "m1b_draft_mission": frozenset(),
    "m1b_revise_mission": frozenset({"Mission"}),
    "m1b_open_window": frozenset(),
    "m1b_comment": frozenset({"ReviewWindow"}),
    "m1b_withdraw_comment": frozenset({"ReviewWindow"}),
    "m1b_assist_review": frozenset({"ReviewWindow"}),
    "m1b_close_window": frozenset({"ReviewWindow"}),
    "m1b_resolve_window": frozenset({"ReviewWindow"}),
    "m1b_confirm_candidates": frozenset({"CandidateSet"}),
    "m1b_reopen_candidates": frozenset({"CandidateSet"}),
    "m1b_reopen_window": frozenset({"ReviewWindow"}),
}
M1B_OBJECT_TYPES = frozenset(M1B_PAYLOAD_MODELS)
M1B_ACTIONS = frozenset(M1B_ACTION_PARAMS)
