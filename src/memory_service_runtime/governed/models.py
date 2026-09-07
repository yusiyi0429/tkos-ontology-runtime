"""Strict public command and business payload schemas for the governed pilot."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, JsonValue,
    StrictInt, StrictStr, ValidationError, model_validator,
)


def _uuid(value: str) -> str:
    return str(UUID(value))


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


UUIDText = Annotated[StrictStr, AfterValidator(_uuid)]
Timestamp = Annotated[StrictStr, AfterValidator(_timestamp)]
Version = Annotated[StrictInt, Field(ge=1)]
Title = Annotated[StrictStr, Field(min_length=1, max_length=500)]
Hash256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Number = Union[StrictInt, Annotated[float, Field(strict=True, allow_inf_nan=False)]]


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


BusinessPayload = Union[CommitmentPayload, FeedbackPayload, DecisionPayload,
                        AdjustmentPayload, ObservationPayload, OutcomePayload]
ObjectType = Literal["CompanyOutcome", "BusinessCommitment", "ExecutionCommitment",
                     "FeedbackThread", "ManagementAdjustment", "Decision", "MetricObservation"]
PAYLOAD_MODELS: dict[str, type[StrictModel]] = {
    "CompanyOutcome": OutcomePayload,
    "BusinessCommitment": CommitmentPayload,
    "ExecutionCommitment": CommitmentPayload,
    "FeedbackThread": FeedbackPayload,
    "ManagementAdjustment": AdjustmentPayload,
    "Decision": DecisionPayload,
    "MetricObservation": ObservationPayload,
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
            data["payload"] = PAYLOAD_MODELS[data["object_type"]].model_validate(data.get("payload"))
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


class EmptyParams(StrictModel):
    pass


ACTION_PARAMS: dict[str, type[StrictModel]] = {
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
}

ActionType = Literal["create_object", "propose_revision", "accept_commitment", "activate_commitment",
                     "confirm_adjustment", "confirm_closure", "route_feedback", "accept_feedback",
                     "investigate_feedback", "confirm_decision", "confirm_outcome", "record_acceptance",
                     "request_feedback_acceptance", "reopen_feedback", "revoke_assignment"]
ActionParams = Union[CreateObjectParams, ProposeRevisionParams, AcceptCommitmentParams,
                     ActivateCommitmentParams, ConfirmAdjustmentParams, ConfirmClosureParams,
                     RouteFeedbackParams, RecordAcceptanceParams, RequestFeedbackAcceptanceParams,
                     RevokeAssignmentParams, EmptyParams]


class ActionRequest(StrictModel):
    action_type: ActionType
    target: ActionTarget | None
    expected_versions: list[ExpectedVersion]
    idempotency_key: Annotated[StrictStr, Field(min_length=16, max_length=128)]
    reason: Annotated[StrictStr, Field(min_length=5)]
    params: ActionParams

    @model_validator(mode="before")
    @classmethod
    def select_params(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("action_type"), str) and data["action_type"] in ACTION_PARAMS:
            data = dict(data)
            data["params"] = ACTION_PARAMS[data["action_type"]].model_validate(data.get("params"))
        return data

    @model_validator(mode="after")
    def envelope_rules(self) -> "ActionRequest":
        if (self.action_type in {"create_object", "revoke_assignment"}) != (self.target is None):
            raise ValueError("target must be null only for create_object/revoke_assignment")
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


__all__ = ["ActionRequest", "ActionTarget", "ExpectedVersion", "validated_payload", "PAYLOAD_MODELS"]
