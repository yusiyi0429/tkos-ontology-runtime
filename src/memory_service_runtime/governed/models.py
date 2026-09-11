"""Strict public command and business payload schemas for the governed pilot."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, JsonValue,
    StrictInt, StrictStr, TypeAdapter, ValidationError, model_validator,
)


def _uuid(value: str) -> str:
    return str(UUID(value))


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must contain non-whitespace characters")
    return value


UUIDText = Annotated[StrictStr, AfterValidator(_uuid)]
Timestamp = Annotated[StrictStr, AfterValidator(_timestamp)]
Version = Annotated[StrictInt, Field(ge=1)]
Title = Annotated[StrictStr, Field(min_length=1, max_length=500)]
Hash256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Number = Union[StrictInt, Annotated[float, Field(strict=True, allow_inf_nan=False)]]
NonBlankText = Annotated[StrictStr, Field(min_length=1, max_length=20000), AfterValidator(_non_blank)]
CriterionId = Annotated[StrictStr, Field(min_length=1, max_length=200), AfterValidator(_non_blank)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class RevisionRef(StrictModel):
    object_id: UUIDText
    revision_id: UUIDText


class ActionTarget(RevisionRef):
    expected_version: Version


class ExpectedVersion(StrictModel):
    object_id: UUIDText
    expected_version: Version


class OutcomePayload(StrictModel):
    title: Title
    terms: dict[str, JsonValue] = Field(default_factory=dict)
    outcome_statement: StrictStr | None = None
    upstream_refs: list[RevisionRef] = Field(default_factory=list)


class CommitmentPayload(StrictModel):
    title: Title
    terms: dict[str, JsonValue]
    required_assignment_ids: Annotated[list[UUIDText], Field(min_length=2, max_length=2)]
    upstream_refs: Annotated[list[RevisionRef], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct_parties_and_refs(self) -> "CommitmentPayload":
        if len(set(self.required_assignment_ids)) != 2:
            raise ValueError("two distinct required assignments are required")
        if len({ref.object_id for ref in self.upstream_refs}) != len(self.upstream_refs):
            raise ValueError("duplicate upstream objects")
        return self


class FeedbackPayload(StrictModel):
    title: Title
    description: Annotated[StrictStr, Field(min_length=1, max_length=20000)]


class DecisionPayload(StrictModel):
    title: Title
    statement: Annotated[StrictStr, Field(min_length=1, max_length=20000)]


class RevisionChange(StrictModel):
    object_id: UUIDText
    from_revision_id: UUIDText
    to_revision_id: UUIDText

    @model_validator(mode="after")
    def changed(self) -> "RevisionChange":
        if self.from_revision_id == self.to_revision_id:
            raise ValueError("revision change must select a new revision")
        return self


class AdjustmentPayload(StrictModel):
    title: Title
    feedback_revision_id: UUIDText
    decision_revision_id: UUIDText
    # An empty shell is a proposal only and can never be applied.
    changes: list[RevisionChange]

    @model_validator(mode="after")
    def distinct_objects(self) -> "AdjustmentPayload":
        if len({change.object_id for change in self.changes}) != len(self.changes):
            raise ValueError("duplicate change object")
        return self


class ObservationPayload(StrictModel):
    title: Title
    metric_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    value: Number
    unit: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    valid_from: Timestamp
    valid_to: Timestamp | None = None
    upstream_refs: list[RevisionRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_window(self) -> "ObservationPayload":
        if self.valid_to and datetime.fromisoformat(self.valid_to) <= datetime.fromisoformat(self.valid_from):
            raise ValueError("valid_to must be later than valid_from")
        return self


class AcceptanceCriterion(StrictModel):
    criterion_id: CriterionId
    description: NonBlankText


class WorkItemPayload(StrictModel):
    title: Title
    execution_commitment_ref: RevisionRef
    dri_assignment_id: UUIDText
    acceptor_assignment_id: UUIDText
    acceptance_criteria: Annotated[list[AcceptanceCriterion], Field(min_length=1)]
    feedback_ref: RevisionRef | None = None
    due_at: Timestamp | None = None

    @model_validator(mode="after")
    def distinct_criteria(self) -> "WorkItemPayload":
        ids = [criterion.criterion_id for criterion in self.acceptance_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate acceptance criterion ids")
        return self


# ---------------------------------------------------------------- A2 schemas
#
# A2 公司组合 (Contract-A tkos.contract-a/0.1) schemas are imported directly
# from a2_models.  a2_models has no dependency on this module (only on canon),
# so this direct import is safe and keeps the public envelope flat: every A2
# command's ``params`` is a single model instance with the A2 spec fields
# directly accessible (e.g. ``request.params.company_id``).

from .a2_models import (  # noqa: E402  (intentional module-level wiring)
    ActivateCompanyCompositionParams,
    AmendFormationRoundParams,
    A2_ACTION_PARAMS,
    CapacityObservationPayload,
    CompanyReferencePayload,
    ConfirmCompanyCompositionParams,
    FormCompanyCompositionParams,
    OpenFormationRoundParams,
    PublishDomainSubmissionParams,
)

# A3 DRI-IC execution handover (Contract-A tkos.contract-a/0.1) schemas are
# imported directly from a3_models, same wiring idiom as a2_models.  Legacy
# members stay exactly the legacy set; the A3 models only widen the unions so
# the A3 wire shapes can validate under the server-side protocol gate.
from .a3_models import (  # noqa: E402  (intentional module-level wiring)
    A3AcceptWorkItemParams,
    A3ExecutionCommitmentPayload,
    A3ExecutionPlanPayload,
    A3ReviewDeliverableParams,
    A3SubmitDeliverableParams,
    A3WorkItemPayload,
    A3_PAYLOAD_MODELS,
)


# Extended BusinessPayload: the two A2 source types are accepted by generic
# create_object / propose_revision for Contract-A scopes.  Legacy Union
# members stay exactly the legacy set; this widens the discriminated union
# only to allow the two source payload models to validate.  The three A3
# payload models are appended last so legacy dicts keep their legacy match.
BusinessPayload = Union[
    CommitmentPayload, FeedbackPayload, DecisionPayload,
    AdjustmentPayload, ObservationPayload, OutcomePayload, WorkItemPayload,
    CompanyReferencePayload, CapacityObservationPayload,
    A3ExecutionCommitmentPayload, A3WorkItemPayload, A3ExecutionPlanPayload,
]


# Extended ObjectType literal: adds the two source types so the existing
# ``CreateObjectParams.select_payload`` validator can route them through the
# PAYLOAD_MODELS table.  Legacy members stay byte-identical.  ``ExecutionPlan``
# is added for the A3 execution handover; it validates through the strict A3
# model registered in PAYLOAD_MODELS.
ObjectType = Literal[
    "CompanyOutcome", "BusinessCommitment", "ExecutionCommitment",
    "FeedbackThread", "ManagementAdjustment", "Decision", "MetricObservation",
    "WorkItem",
    "CompanyReference", "CapacityObservation",
    "ExecutionPlan",
]

# Full catalog of A2 types (Contract-A).  Used by readers / workbench / a2_service.
A2_OBJECT_TYPE_NAMES = ("CompanyReference", "CapacityObservation", "FormationRound",
                        "DomainSubmission", "CompanyComposition", "Mission",
                        "DomainCommitment")

# Generic create_object / propose_revision are only allowed for these two
# source types; the other five A2 types are derived exclusively by A2 handlers
# and must never be reached through generic create/propose.
A2_GENERIC_SOURCE_OBJECT_TYPES = ("CompanyReference", "CapacityObservation")


PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    "CompanyOutcome": OutcomePayload,
    "BusinessCommitment": CommitmentPayload,
    "ExecutionCommitment": CommitmentPayload,
    "FeedbackThread": FeedbackPayload,
    "ManagementAdjustment": AdjustmentPayload,
    "Decision": DecisionPayload,
    "MetricObservation": ObservationPayload,
    "WorkItem": WorkItemPayload,
    "CompanyReference": CompanyReferencePayload,
    "CapacityObservation": CapacityObservationPayload,
    # A3-only type: no legacy payload model exists.  ExecutionCommitment and
    # WorkItem keep their legacy entries here; the A3 variants live in
    # a3_models.A3_PAYLOAD_MODELS and are tried as a fallback in
    # CreateObjectParams.select_payload.
    "ExecutionPlan": A3ExecutionPlanPayload,
}


class CreateObjectParams(StrictModel):
    object_type: ObjectType
    domain_id: UUIDText
    payload: BusinessPayload
    valid_from: Timestamp | None = None

    @model_validator(mode="before")
    @classmethod
    def select_payload(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("object_type"), str) and data["object_type"] in PAYLOAD_MODELS:
            data = dict(data)
            object_type = data["object_type"]
            model = PAYLOAD_MODELS[object_type]
            a3_model = A3_PAYLOAD_MODELS.get(object_type)
            if a3_model is None or a3_model is model:
                data["payload"] = model.model_validate(data.get("payload"))
            else:
                # ExecutionCommitment / WorkItem exist under both the legacy
                # and the A3 payload shapes; the two are disjoint (distinct
                # required fields, extra=forbid).  Legacy is tried first so a
                # legacy request keeps byte-identical validation and error
                # behavior; only a legacy failure falls back to the strict A3
                # model, and if both fail the legacy error is re-raised.
                try:
                    data["payload"] = model.model_validate(data.get("payload"))
                except ValidationError as legacy_error:
                    try:
                        data["payload"] = a3_model.model_validate(data.get("payload"))
                    except ValidationError:
                        raise legacy_error
        return data


class ProposeRevisionParams(StrictModel):
    payload: BusinessPayload
    bundle_id: UUIDText | None = None


class AcceptCommitmentParams(StrictModel):
    party_assignment_id: UUIDText
    understanding: Annotated[StrictStr, Field(min_length=10)]
    accepted_terms_hash: Hash256


class ActivateCommitmentParams(StrictModel):
    handshake_record_ids: Annotated[list[UUIDText], Field(min_length=2)]
    activation_policy_revision_id: UUIDText

    @model_validator(mode="after")
    def distinct(self) -> "ActivateCommitmentParams":
        if len(set(self.handshake_record_ids)) != len(self.handshake_record_ids):
            raise ValueError("duplicate handshake ids")
        return self


class ConfirmAdjustmentParams(StrictModel):
    decision_revision_id: UUIDText
    changes: Annotated[list[RevisionChange], Field(min_length=1)]
    feedback_revision_id: UUIDText

    @model_validator(mode="after")
    def distinct(self) -> "ConfirmAdjustmentParams":
        if len({change.object_id for change in self.changes}) != len(self.changes):
            raise ValueError("duplicate change object")
        return self


class ConfirmClosureParams(StrictModel):
    acceptance_record_id: UUIDText
    resolution_decision_revision_id: UUIDText
    closure_evidence_revision_ids: Annotated[list[UUIDText], Field(min_length=1)]
    disposition: Literal["resolved", "dismissed", "no_change"]
    closure_note: Annotated[StrictStr, Field(min_length=10)]

    @model_validator(mode="after")
    def distinct(self) -> "ConfirmClosureParams":
        if len(set(self.closure_evidence_revision_ids)) != len(self.closure_evidence_revision_ids):
            raise ValueError("duplicate evidence revisions")
        return self


class RouteFeedbackParams(StrictModel):
    responsible_assignment_id: UUIDText


class RecordAcceptanceParams(StrictModel):
    decision_revision_id: UUIDText
    evidence_revision_ids: Annotated[list[UUIDText], Field(min_length=1)]
    verification_result: Literal["accepted", "changes_requested"]

    @model_validator(mode="after")
    def distinct(self) -> "RecordAcceptanceParams":
        if len(set(self.evidence_revision_ids)) != len(self.evidence_revision_ids):
            raise ValueError("duplicate evidence revisions")
        return self


class RequestFeedbackAcceptanceParams(StrictModel):
    decision_revision_id: UUIDText


class RevokeAssignmentParams(StrictModel):
    assignment_id: UUIDText


class SubmitDeliverableParams(StrictModel):
    title: Title
    summary: NonBlankText
    evidence_revision_ids: Annotated[list[UUIDText], Field(min_length=1)]
    responds_to_acceptance_id: UUIDText | None = None

    @model_validator(mode="after")
    def distinct_evidence(self) -> "SubmitDeliverableParams":
        if len(set(self.evidence_revision_ids)) != len(self.evidence_revision_ids):
            raise ValueError("duplicate evidence revisions")
        return self


class CriterionResult(StrictModel):
    criterion_id: CriterionId
    result: Literal["passed", "failed"]
    note: NonBlankText


class ReviewDeliverableParams(StrictModel):
    deliverable_revision_id: UUIDText
    delivery_payload_hash: Hash256
    verification_result: Literal["accepted", "changes_requested"]
    criterion_results: Annotated[list[CriterionResult], Field(min_length=1)]
    review_note: NonBlankText

    @model_validator(mode="after")
    def distinct_criteria(self) -> "ReviewDeliverableParams":
        ids = [result.criterion_id for result in self.criterion_results]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate criterion result ids")
        return self


class RecordOutcomeAssessmentParams(StrictModel):
    assessment_result: Literal["achieved", "not_achieved", "inconclusive"]
    observation_revision_ids: Annotated[list[UUIDText], Field(min_length=1)]
    evidence_revision_ids: Annotated[list[UUIDText], Field(min_length=1)]
    assessment_note: NonBlankText
    delivery_acceptance_ids: list[UUIDText] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_references(self) -> "RecordOutcomeAssessmentParams":
        for field in ("observation_revision_ids", "evidence_revision_ids", "delivery_acceptance_ids"):
            values = getattr(self, field)
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {field}")
        return self


class EmptyParams(StrictModel):
    pass


# A2 action_type -> strict param class.  Each value is the A2 model from
# a2_models with its spec fields directly addressable (no wrapper, no nesting,
# no lazy registration).  The six names line up with a2_models.A2_ACTION_PARAMS.
# action_type -> strict param spec.  Each value is a model class, or — for the
# three delivery-loop actions that exist under both legacy and A3 semantics —
# a Union with the LEGACY member first so a legacy request keeps its legacy
# match and error behavior (the A3 wire shapes carry extra required fields and
# are disjoint from the legacy ones under extra=forbid).
ACTION_PARAMS: dict[str, Any] = {
    "create_object": CreateObjectParams,
    "propose_revision": ProposeRevisionParams,
    "accept_commitment": AcceptCommitmentParams,
    "activate_commitment": ActivateCommitmentParams,
    "confirm_adjustment": ConfirmAdjustmentParams,
    "confirm_closure": ConfirmClosureParams,
    "route_feedback": RouteFeedbackParams,
    "accept_feedback": EmptyParams,
    "investigate_feedback": EmptyParams,
    "confirm_decision": EmptyParams,
    "confirm_outcome": EmptyParams,
    "record_acceptance": RecordAcceptanceParams,
    "request_feedback_acceptance": RequestFeedbackAcceptanceParams,
    "reopen_feedback": EmptyParams,
    "revoke_assignment": RevokeAssignmentParams,
    "accept_work_item": Union[EmptyParams, A3AcceptWorkItemParams],
    "submit_deliverable": Union[SubmitDeliverableParams, A3SubmitDeliverableParams],
    "review_deliverable": Union[ReviewDeliverableParams, A3ReviewDeliverableParams],
    "record_outcome_assessment": RecordOutcomeAssessmentParams,
    "open_formation_round": OpenFormationRoundParams,
    "amend_formation_round": AmendFormationRoundParams,
    "publish_domain_submission": PublishDomainSubmissionParams,
    "form_company_composition": FormCompanyCompositionParams,
    "confirm_company_composition": ConfirmCompanyCompositionParams,
    "activate_company_composition": ActivateCompanyCompositionParams,
}

ActionType = Literal["create_object", "propose_revision", "accept_commitment", "activate_commitment",
                     "confirm_adjustment", "confirm_closure", "route_feedback", "accept_feedback",
                     "investigate_feedback", "confirm_decision", "confirm_outcome", "record_acceptance",
                     "request_feedback_acceptance", "reopen_feedback", "revoke_assignment",
                     "accept_work_item", "submit_deliverable", "review_deliverable",
                     "record_outcome_assessment",
                     "open_formation_round", "amend_formation_round",
                     "publish_domain_submission", "form_company_composition",
                     "confirm_company_composition", "activate_company_composition"]
ActionParams = Union[CreateObjectParams, ProposeRevisionParams, AcceptCommitmentParams,
                     ActivateCommitmentParams, ConfirmAdjustmentParams, ConfirmClosureParams,
                     RouteFeedbackParams, RecordAcceptanceParams, RequestFeedbackAcceptanceParams,
                     RevokeAssignmentParams, SubmitDeliverableParams, ReviewDeliverableParams,
                     RecordOutcomeAssessmentParams, EmptyParams,
                     A3AcceptWorkItemParams, A3SubmitDeliverableParams, A3ReviewDeliverableParams,
                     OpenFormationRoundParams, AmendFormationRoundParams,
                     PublishDomainSubmissionParams, FormCompanyCompositionParams,
                     ConfirmCompanyCompositionParams, ActivateCompanyCompositionParams]


from .a2_models import ObjectRef as MethodRunRef
from .method_models import METHOD_ACTION_PARAMS, METHOD_ACTION_TARGETS, MethodActionType, MethodActionParams
ACTION_PARAMS.update(METHOD_ACTION_PARAMS)
ActionType = Union[ActionType, MethodActionType]
ActionParams = Union[ActionParams, MethodActionParams]

_PARAM_ADAPTERS: dict[str, "TypeAdapter[Any]"] = {}


def _validate_action_params(action_type: str, raw: Any) -> Any:
    """Validate ``params`` against the action's spec (model class or Union).

    Union specs (the legacy/A3 dual-shape delivery actions) go through a cached
    TypeAdapter; everything else keeps the plain model_validate call.
    """
    spec = ACTION_PARAMS[action_type]
    if isinstance(spec, type) and issubclass(spec, BaseModel):
        return spec.model_validate(raw)
    adapter = _PARAM_ADAPTERS.get(action_type)
    if adapter is None:
        adapter = TypeAdapter(spec)
        _PARAM_ADAPTERS[action_type] = adapter
    return adapter.validate_python(raw)


class ActionRequest(StrictModel):
    action_type: ActionType
    target: ActionTarget | None
    expected_versions: list[ExpectedVersion]
    idempotency_key: Annotated[StrictStr, Field(min_length=16, max_length=128)]
    reason: Annotated[StrictStr, Field(min_length=5)]
    params: ActionParams
    # Declares which action format the client understands; the server alone
    # decides the object's actual protocol.  Absent/null must never change the
    # legacy request_hash (model_dump(exclude_none=True)).
    contract_version: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None
    # Method run association is explicit for new roots; absent fields remain
    # excluded from old command hashes and do not reinterpret old envelopes.
    run_ref: "MethodRunRef | None" = None
    step_key: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="before")
    @classmethod
    def select_params(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("action_type"), str):
            action_type = data["action_type"]
            if action_type in ACTION_PARAMS:
                data = dict(data)
                data["params"] = _validate_action_params(action_type, data.get("params"))
        return data

    @model_validator(mode="after")
    def envelope_rules(self) -> "ActionRequest":
        # A2 actions: open_formation_round has target=None; the other five
        # A2 action names target an A2-bound object. None of them support
        # the legacy create_object / revoke_assignment null-target exception.
        if self.action_type in METHOD_ACTION_TARGETS:
            needs_target = bool(METHOD_ACTION_TARGETS[self.action_type])
            if needs_target != (self.target is not None):
                raise ValueError("Method action target does not match its typed contract")
            if self.contract_version != "tkos.method/0.1":
                raise ValueError("Method actions require explicit tkos.method/0.1")
        elif self.action_type == "open_formation_round":
            if self.target is not None:
                raise ValueError("open_formation_round must carry target=None")
        elif self.action_type in {"amend_formation_round", "publish_domain_submission",
                                   "form_company_composition"}:
            if self.target is None:
                raise ValueError("amend/publish/form actions must target a FormationRound")
        elif self.action_type in {"confirm_company_composition", "activate_company_composition"}:
            if self.target is None:
                raise ValueError("confirm/activate actions must target a CompanyComposition")
        elif (self.action_type in {"create_object", "revoke_assignment"}) != (self.target is None):
            raise ValueError("target must be null only for create_object/revoke_assignment")
        if self.action_type == "method_open_run" and (self.run_ref is not None or self.step_key is not None):
            raise ValueError("A Method run is an independent root")
        if self.action_type not in METHOD_ACTION_TARGETS and (self.run_ref is not None or self.step_key is not None):
            raise ValueError("Run association belongs only to tkos.method/0.1")
        if self.idempotency_key != self.idempotency_key.strip():
            raise ValueError("idempotency_key cannot have surrounding whitespace")
        ids = [item.object_id for item in self.expected_versions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate expected-version objects")
        if self.target and self.target.object_id in ids:
            raise ValueError("target must not be duplicated in expected_versions")
        return self


def validated_payload(object_type: str, payload: Any) -> dict[str, Any]:
    """Revalidate proposals against the existing object's actual type."""
    model = PAYLOAD_MODELS.get(object_type)
    if model is None:
        raise ValueError("unsupported object type")
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json", exclude_none=True)
    return model.model_validate(payload).model_dump(mode="json", exclude_none=True)


__all__ = [
    "ActionRequest", "ActionTarget", "ExpectedVersion",
    "validated_payload", "PAYLOAD_MODELS",
    "A2_OBJECT_TYPE_NAMES", "A2_GENERIC_SOURCE_OBJECT_TYPES",
    "OpenFormationRoundParams", "AmendFormationRoundParams",
    "PublishDomainSubmissionParams", "FormCompanyCompositionParams",
    "ConfirmCompanyCompositionParams", "ActivateCompanyCompositionParams",
    "CompanyReferencePayload", "CapacityObservationPayload",
    "A3_PAYLOAD_MODELS",
    "A3ExecutionCommitmentPayload", "A3WorkItemPayload", "A3ExecutionPlanPayload",
    "A3AcceptWorkItemParams", "A3SubmitDeliverableParams", "A3ReviewDeliverableParams",
]
