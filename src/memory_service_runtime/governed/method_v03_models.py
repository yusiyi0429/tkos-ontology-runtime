"""Anchor and Agent-intake contract; older Method schemas remain frozen."""
from typing import Literal
from pydantic import Field, model_validator
from .a2_models import StrictModel, CanonicalUUID, NEStr, IsoDateTime
from . import method_v02_models as v2, method_m1a_models as a, method_m1b_models as b

CONTRACT_VERSION = 'tkos.method/0.3'

class ArchitectureUnit(StrictModel):
    unit_id: NEStr
    unit_type: Literal['battlefield', 'capability']
    name: NEStr
    definition: NEStr
    strategic_basis: list[NEStr] = Field(min_length=1)
    boundary: NEStr
    interfaces: list[NEStr] = Field(default_factory=list)
    domain_id: CanonicalUUID

class ArchitectureDefinition(StrictModel):
    title: NEStr
    units: list[ArchitectureUnit] = Field(min_length=1)

    @model_validator(mode='after')
    def unique(self):
        b.distinct([u.unit_id for u in self.units], 'Architecture unit IDs')
        return self

class ArchitecturePayload(ArchitectureDefinition):
    strategy_ref: a.ExactRef
    source_proposal_ref: a.ExactRef | None = None

class ProposeArchitecture(StrictModel):
    domain_id: CanonicalUUID
    payload: ArchitecturePayload

class ReviseArchitecture(StrictModel):
    payload: ArchitecturePayload

class StrategyChange(v2.StrategyChange):
    architecture: ArchitectureDefinition

class UpdateProposal(v2.UpdateProposal):
    changes: list[StrategyChange | a.JudgmentChange] = Field(min_length=1)

class ProposeUpdate(v2.ProposeUpdate):
    payload: UpdateProposal

class LTCO(b.LTCOPayload):
    architecture_ref: a.ExactRef

class PCO(b.PCOPayload):
    architecture_ref: a.ExactRef

class Mission(b.MissionPayload):
    architecture_ref: a.ExactRef
    primary_scope_id: NEStr

class ProposeLTCO(b.ProposeLTCO):
    payload: LTCO
class ReviseLTCO(b.ReviseLTCO):
    payload: LTCO
class DraftPCO(b.DraftPCO):
    payload: PCO
class RevisePCO(b.RevisePCO):
    payload: PCO
class DraftMission(b.DraftMission):
    payload: Mission
class ReviseMission(b.ReviseMission):
    payload: Mission
class CandidateMission(b.CandidateMission):
    architecture_ref: a.ExactRef
    primary_scope_id: NEStr
class ResolveWindow(b.ResolveWindow):
    pco_payload: PCO
    missions: list[CandidateMission] = Field(min_length=1)

class StateSubject(a.ExactRef):
    outcome_id: NEStr | None = None

class StatePayload(StrictModel):
    subject_ref: StateSubject
    as_of: IsoDateTime
    summary: NEStr
    rag: Literal['green', 'yellow', 'red', 'unknown']
    baseline_refs: list[a.ExactRef] = Field(min_length=1)
    evidence_refs: list[a.ExactRef] = Field(default_factory=list)
    data_gaps: list[NEStr] = Field(default_factory=list)
    generation_version: NEStr

    @model_validator(mode='after')
    def evidence(self):
        a._unique_refs(self.baseline_refs)
        a._unique_refs(self.evidence_refs)
        if not self.evidence_refs and (self.rag != 'unknown' or not self.data_gaps):
            raise ValueError('Absent evidence requires Unknown and explicit data gaps')
        return self

class ProposeState(StrictModel):
    domain_id: CanonicalUUID
    payload: StatePayload
    previous_state_ref: a.ExactRef | None = None

class ConfirmState(StrictModel):
    reason: NEStr
    summary: NEStr | None = None
    rag: Literal['green', 'yellow', 'red', 'unknown'] | None = None

    @model_validator(mode='after')
    def override(self):
        if (self.summary is None) != (self.rag is None):
            raise ValueError('Override needs both summary and rating')
        return self

class PeriodReview(b.PeriodReviewPayload):
    state_refs: list[a.ExactRef] = Field(min_length=1)

    @model_validator(mode='after')
    def states(self):
        a._unique_refs(self.state_refs)
        return self
class GenerateReview(b.GenerateReview):
    payload: PeriodReview
class RegenerateReview(b.RegenerateReview):
    payload: PeriodReview

class ProblemPayload(StrictModel):
    state_ref: a.ExactRef
    core_question: NEStr
    statement: NEStr
    why_material: NEStr
    level: Literal['mission', 'domain', 'company', 'strategic']
    responsible_assignment_id: CanonicalUUID
    evidence_refs: list[a.ExactRef] = Field(default_factory=list)
    decision_deadline: IsoDateTime | None = None

class OpenProblem(StrictModel):
    domain_id: CanonicalUUID
    payload: ProblemPayload
class ReviseProblem(StrictModel):
    payload: ProblemPayload
class CloseProblem(StrictModel):
    disposition: Literal['resolved', 'no_further_action']
    reason: NEStr
    evidence_refs: list[a.ExactRef] = Field(min_length=1)

class PotentialIssue(v2.Classification):
    title: NEStr
    summary: NEStr
    core_question: NEStr
    source_refs: list[a.ExactRef] = Field(min_length=1)

    @model_validator(mode='after')
    def sources(self):
        a._unique_refs(self.source_refs)
        return self
class OpenPotential(v2.OpenPotential):
    payload: PotentialIssue
class RevisePotential(v2.RevisePotential):
    payload: PotentialIssue
class InitiateIssue(StrictModel):
    reason: NEStr
    existing_issue_ref: a.ExactRef | None = None
class StrategicIssue(PotentialIssue):
    potential_issue_ref: a.ExactRef
    confirmation_reason: NEStr

ACTION_PARAMS = {**v2.ACTION_PARAMS,
    'm1a_open_potential_issue': OpenPotential, 'm1a_revise_potential_issue': RevisePotential,
    'm1a_confirm_strategic_issue': InitiateIssue, 'm1a_propose_update': ProposeUpdate,
    'method_propose_architecture': ProposeArchitecture, 'method_revise_architecture': ReviseArchitecture,
    'method_confirm_architecture': b.Reason,
    'method_propose_state': ProposeState, 'method_confirm_state': ConfirmState,
    'method_open_problem': OpenProblem, 'method_revise_problem': ReviseProblem,
    'method_close_problem': CloseProblem,
    'm1b_propose_ltco': ProposeLTCO, 'm1b_revise_ltco': ReviseLTCO,
    'm1b_draft_pco': DraftPCO, 'm1b_revise_pco': RevisePCO,
    'm1b_draft_mission': DraftMission, 'm1b_revise_mission': ReviseMission,
    'm1b_resolve_window': ResolveWindow,
    'm1b_generate_review': GenerateReview, 'm1b_regenerate_review': RegenerateReview}
# Formal initiation has one Agent-attributed route in this contract.
ACTION_PARAMS.pop('m1a_create_direct_issue')
ACTION_TARGETS = {**v2.ACTION_TARGETS,
    'method_propose_architecture': frozenset(),
    'method_revise_architecture': frozenset({'StrategicArchitecture'}),
    'method_confirm_architecture': frozenset({'StrategicArchitecture'}),
    'method_propose_state': frozenset(), 'method_confirm_state': frozenset({'OperatingState'}),
    'method_open_problem': frozenset(), 'method_revise_problem': frozenset({'OperatingProblem'}),
    'method_close_problem': frozenset({'OperatingProblem'})}
ACTION_TARGETS.pop('m1a_create_direct_issue')
PAYLOAD_MODELS = {**v2.PAYLOAD_MODELS, 'StrategicArchitecture': ArchitecturePayload,
    'StrategyUpdateProposal': UpdateProposal, 'LTCO': LTCO, 'PCO': PCO, 'Mission': Mission,
    'OperatingState': StatePayload, 'OperatingProblem': ProblemPayload,
    'PotentialIssue': PotentialIssue, 'StrategicIssue': StrategicIssue, 'PeriodReview': PeriodReview}
OBJECT_TYPES = frozenset(PAYLOAD_MODELS) | {'EvidenceAsset'}
