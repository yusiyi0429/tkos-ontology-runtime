"""Strict ``tkos.method/0.5`` schemas: ontology v0.7 M1 alignment on top of frozen 0.4.

Only payloads and actions that differ from 0.4 are (re)defined here; every
other 0.4 model, action and target set is reused by reference.  The 0.4
module is frozen and never widened in place.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from . import method_v04_models as v4
from .a2_models import CanonicalUUID, IsoDateTime, NEStr, StrictModel
from .method_m1a_models import ExactRef
from .method_m1b_models import Period, distinct

CONTRACT_VERSION = "tkos.method/0.5"


# ------------------------------------------------------------- Constraint


class ConstraintScope(StrictModel):
    """适用范围三选一：company；scope（Battlefield / Domain 稳定 unit_id）；mission（确切 Mission 版本）。"""

    kind: Literal["company", "scope", "mission"]
    scope_id: NEStr | None = None
    mission_ref: ExactRef | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "ConstraintScope":
        if (self.kind == "scope") != (self.scope_id is not None):
            raise ValueError("a Scope constraint names exactly one unit_id")
        if (self.kind == "mission") != (self.mission_ref is not None):
            raise ValueError("a Mission constraint names exactly one exact Mission")
        return self


class ConstraintPayload(StrictModel):
    title: NEStr
    applies_to: ConstraintScope
    architecture_ref: ExactRef | None = None
    statement: NEStr
    constraint_type: Literal["people", "money", "capacity", "policy", "dependency", "other"]
    effective: Period
    source: NEStr
    authority: NEStr
    severity: Literal["hard", "soft"]
    evidence_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def scope_needs_its_architecture(self) -> "ConstraintPayload":
        if (self.applies_to.kind == "scope") != (self.architecture_ref is not None):
            raise ValueError("a Scope constraint cites the exact Architecture its unit belongs to; others do not")
        distinct([(r.object_id, r.revision_id) for r in self.evidence_refs], "constraint evidence")
        return self


class RecordConstraint(StrictModel):
    domain_id: CanonicalUUID
    payload: ConstraintPayload


class ReviseConstraint(StrictModel):
    payload: ConstraintPayload


class ConfirmConstraint(StrictModel):
    statement: NEStr


# ------------------------------------------------------------------- LTCO


class LTCOPayload(v4.LTCOPayload):
    realization_logic: NEStr
    key_assumptions: list[NEStr] = Field(default_factory=list)
    constraint_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_constraints(self) -> "LTCOPayload":
        distinct([(r.object_id, r.revision_id) for r in self.constraint_refs], "LTCO constraints")
        return self


class ProposeLTCO(v4.ProposeLTCO):
    payload: LTCOPayload


class ReviseLTCO(v4.ReviseLTCO):
    payload: LTCOPayload


class ConfirmLTCO(StrictModel):
    conclusion: Literal["established", "revised", "maintained"]
    statement: NEStr


# -------------------------------------------------------------------- PCO


class PCOPayload(v4.PCOPayload):
    period_review_ref: ExactRef | None = None
    boundary: NEStr
    constraint_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_constraints(self) -> "PCOPayload":
        distinct([(r.object_id, r.revision_id) for r in self.constraint_refs], "PCO constraints")
        return self


class DraftPCO(v4.DraftPCO):
    payload: PCOPayload


class RevisePCO(v4.RevisePCO):
    payload: PCOPayload


class CandidatePCO(v4.CandidatePCO):
    payload: PCOPayload


# ---------------------------------------------------------------- Mission


class MissionDependency(StrictModel):
    kind: Literal["mission", "scope"]
    mission_ref: ExactRef | None = None
    scope_id: NEStr | None = None
    needed_by: IsoDateTime
    note: NEStr

    @model_validator(mode="after")
    def exactly_one(self) -> "MissionDependency":
        if (self.kind == "mission") != (self.mission_ref is not None):
            raise ValueError("a Mission dependency names exactly one exact Mission")
        if (self.kind == "scope") != (self.scope_id is not None):
            raise ValueError("a Scope dependency names exactly one unit_id")
        return self


class _MissionExtras(StrictModel):
    boundary: NEStr
    contributes_to_scope_ids: list[NEStr] = Field(default_factory=list)
    dependencies: list[MissionDependency] = Field(default_factory=list)
    resource_needs: list[NEStr] = Field(default_factory=list)
    constraint_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_extras(self) -> "_MissionExtras":
        distinct(self.contributes_to_scope_ids, "Mission contributions")
        distinct([(r.object_id, r.revision_id) for r in self.constraint_refs], "Mission constraints")
        return self


class MissionPayload(v4.MissionPayload, _MissionExtras):
    pass


class DraftMission(v4.DraftMission):
    payload: MissionPayload


class ReviseMission(v4.ReviseMission):
    payload: MissionPayload


class CandidateMission(v4.CandidateMission, _MissionExtras):
    pass


class ResolveWindow(v4.ResolveWindow):
    pcos: list[CandidatePCO] = Field(min_length=1)
    missions: list[CandidateMission] = Field(min_length=1)


# ------------------------------------------------------ Operating State


class StatePayload(v4.StatePayload):
    period: Period
    drilldown_refs: list[ExactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def as_of_is_period_end(self) -> "StatePayload":
        if datetime.fromisoformat(self.as_of) != datetime.fromisoformat(self.period.end):
            raise ValueError("as_of must equal period.end")
        distinct([(r.object_id, r.revision_id) for r in self.drilldown_refs], "drill-down states")
        return self


class ProposeState(v4.ProposeState):
    payload: StatePayload


# ---------------------------------------------------------- Period Review


class ConfirmReview(StrictModel):
    statement: NEStr
    findings: list[NEStr] | None = Field(default=None, min_length=1)
    learnings: list[NEStr] | None = None
    implications: list[NEStr] | None = None


# --------------------------------------------------------------- tables


PAYLOAD_MODELS = {**v4.PAYLOAD_MODELS,
                  "Constraint": ConstraintPayload,
                  "LTCO": LTCOPayload,
                  "PCO": PCOPayload,
                  "Mission": MissionPayload,
                  "OperatingState": StatePayload}

V05_ONLY_ACTIONS = frozenset({"m1b_record_constraint", "m1b_revise_constraint",
                              "m1b_confirm_constraint", "m1b_confirm_review"})

ACTION_PARAMS = {**{k: v for k, v in v4.ACTION_PARAMS.items() if k != "method_confirm_state"},
                 "m1b_record_constraint": RecordConstraint,
                 "m1b_revise_constraint": ReviseConstraint,
                 "m1b_confirm_constraint": ConfirmConstraint,
                 "m1b_confirm_review": ConfirmReview,
                 "m1b_propose_ltco": ProposeLTCO,
                 "m1b_revise_ltco": ReviseLTCO,
                 "m1b_confirm_ltco": ConfirmLTCO,
                 "m1b_draft_pco": DraftPCO,
                 "m1b_revise_pco": RevisePCO,
                 "m1b_draft_mission": DraftMission,
                 "m1b_revise_mission": ReviseMission,
                 "m1b_resolve_window": ResolveWindow,
                 "method_propose_state": ProposeState}

ACTION_TARGETS = {**{k: v for k, v in v4.ACTION_TARGETS.items() if k != "method_confirm_state"},
                  "m1b_record_constraint": frozenset(),
                  "m1b_revise_constraint": frozenset({"Constraint"}),
                  "m1b_confirm_constraint": frozenset({"Constraint"}),
                  "m1b_confirm_review": frozenset({"PeriodReview"})}

OBJECT_TYPES = frozenset(PAYLOAD_MODELS) | {"EvidenceAsset"}

# Human actions offered to the governance workbench; Agent-only drafting stays out.
HUMAN_ACTIONS = (v4.HUMAN_ACTIONS - {"method_confirm_state"}) | V05_ONLY_ACTIONS
