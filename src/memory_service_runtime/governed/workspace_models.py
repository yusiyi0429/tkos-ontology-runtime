"""Clark scene records. These are not new tkos.method actions or decisions."""
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictBool, model_validator

from .a2_models import CanonicalUUID, IsoDateTime, NEStr, StrictModel
from .method_m1b_models import ExactRef, Period

Refs = Annotated[list[ExactRef], Field(max_length=100)]
Lines = Annotated[list[NEStr], Field(max_length=100)]


class Create(StrictModel):
    kind: Literal["create"]
    scene_type: Literal["monthly", "weekly", "meeting"]
    anchor_ref: ExactRef
    external_id: Annotated[NEStr, Field(max_length=200)]
    title: NEStr
    owner_assignment_id: CanonicalUUID
    participant_assignment_ids: Annotated[list[CanonicalUUID], Field(max_length=100)] = []


class CommentAnchor(StrictModel):
    kind: Literal["comment_anchor"]
    review_record_id: CanonicalUUID
    target_ref: ExactRef
    field_path: Annotated[str, Field(pattern=r"^(?:/(?:[^~/]|~[01])*)+$", max_length=500)]


class DiffResponse(StrictModel):
    kind: Literal["diff_response"]
    candidate_ref: ExactRef
    response: Literal["reviewed", "commented"]
    note: NEStr | None = None


class Milestone(StrictModel):
    due: IsoDateTime
    label: NEStr
    state: Literal["verified", "current", "later"]


class MonthlyMaterial(StrictModel):
    kind: Literal["monthly_material"]
    target_ref: ExactRef
    source_refs: Annotated[list[ExactRef], Field(min_length=1, max_length=100)]
    priority: Literal["P0", "P1"] | None = None
    why: NEStr | None = None
    milestones: list[Milestone] | None = None
    dependencies: Lines | None = None
    feedback_deadline: IsoDateTime | None = None


class ProgressSource(StrictModel):
    source_id: NEStr
    label: NEStr
    state: Literal["read", "stale", "unavailable"]
    observed_at: IsoDateTime
    evidence_ref: ExactRef | None = None
    reason: NEStr | None = None

    @model_validator(mode="after")
    def supported(self):
        if self.state == "read" and self.evidence_ref is None:
            raise ValueError("read sources require exact evidence")
        if self.state != "read" and self.reason is None:
            raise ValueError("unread sources require a reason")
        return self


class Question(StrictModel):
    question_id: NEStr
    prompt: NEStr
    reason: NEStr


class WeeklyMaterial(StrictModel):
    kind: Literal["weekly_material"]
    period: Period
    headline: NEStr
    caveat: NEStr | None = None
    advances: Lines
    problems: Lines
    implications: Lines
    handling: Lines
    sources: Annotated[list[ProgressSource], Field(max_length=100)]
    questions: Annotated[list[Question], Field(max_length=100)]
    fact_refs: Refs = []
    period_review_refs: Refs = []
    source_refs: Annotated[list[ExactRef], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_ids(self):
        for values in ([x.source_id for x in self.sources], [x.question_id for x in self.questions]):
            if len(values) != len(set(values)):
                raise ValueError("duplicate material IDs")
        return self


class RefreshSources(StrictModel):
    kind: Literal["refresh_sources"]
    reason: NEStr


class WeeklyAnswer(StrictModel):
    kind: Literal["weekly_answer"]
    material_event_id: CanonicalUUID
    question_id: NEStr
    answer: NEStr


class WeeklyConfirm(StrictModel):
    kind: Literal["weekly_confirm"]
    material_event_id: CanonicalUUID
    answer_event_ids: Annotated[list[CanonicalUUID], Field(max_length=100)]
    signal: Literal["green", "yellow", "red"]


class PreRead(StrictModel):
    kind: Literal["read", "request_supplement", "bring_to_meeting"]
    subject_ref: ExactRef
    note: NEStr | None = None


class MeetingStart(StrictModel):
    kind: Literal["meeting_start"]
    recording_requested: StrictBool


class MeetingFinish(StrictModel):
    kind: Literal["meeting_finish"]
    extract_requested: StrictBool


class TranscriptLine(StrictModel):
    speaker: NEStr
    text: NEStr


class MeetingMaterial(StrictModel):
    kind: Literal["meeting_material"]
    transcript_ref: ExactRef
    transcript: Annotated[list[TranscriptLine], Field(min_length=1, max_length=1000)]
    source_refs: Refs = []


class Quote(StrictModel):
    line_index: Annotated[int, Field(strict=True, ge=0)]
    text: NEStr


class MeetingItem(StrictModel):
    text: NEStr
    quote: Quote


class MeetingAction(MeetingItem):
    owner_assignment_id: CanonicalUUID | None = None
    due: IsoDateTime | None = None


class CeoJudgment(MeetingItem):
    why_ceo: NEStr
    category: Literal["company_direction", "resource_allocation", "external_commitment", "major_risk", "mission_boundary"]


class Route(StrictModel):
    item_kind: Literal["action", "ceo_judgment"]
    item_index: Annotated[int, Field(strict=True, ge=0)]
    recipient_assignment_id: CanonicalUUID


class MeetingPublish(StrictModel):
    kind: Literal["meeting_publish"]
    material_event_id: CanonicalUUID
    summary: NEStr
    decisions: Annotated[list[MeetingItem], Field(max_length=100)]
    actions: Annotated[list[MeetingAction], Field(max_length=100)]
    open_questions: Annotated[list[MeetingItem], Field(max_length=100)]
    ceo_judgments: Annotated[list[CeoJudgment], Field(max_length=100)]
    routes: Annotated[list[Route], Field(max_length=200)]


class Withdraw(StrictModel):
    kind: Literal["withdraw"]
    event_id: CanonicalUUID
    reason: NEStr


Event = Annotated[Create | CommentAnchor | DiffResponse | MonthlyMaterial | WeeklyMaterial
    | RefreshSources | WeeklyAnswer | WeeklyConfirm | PreRead | MeetingStart | MeetingFinish
    | MeetingMaterial | MeetingPublish | Withdraw, Field(discriminator="kind")]


class WorkspaceCommand(StrictModel):
    contract_version: Literal["tkos.workspace/0.1"]
    scene_id: CanonicalUUID
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    idempotency_key: Annotated[NEStr, Field(min_length=16, max_length=128)]
    event: Event

    @model_validator(mode="after")
    def create_version(self):
        if self.idempotency_key != self.idempotency_key.strip():
            raise ValueError("idempotency key must not have surrounding whitespace")
        if (self.event.kind == "create") != (self.expected_version == 0):
            raise ValueError("only create uses version zero")
        return self


class IdentityView(StrictModel):
    scope_id: CanonicalUUID
    tenant_id: NEStr
    company_id: NEStr
    principal_id: CanonicalUUID
    principal_type: Literal["human", "agent"]
    display_name: str
    auth_epoch: int
    assignments: list[dict[str, JsonValue]]


class SceneResult(StrictModel):
    contract_version: Literal["tkos.workspace/0.1"]
    scene_id: CanonicalUUID
    event_id: CanonicalUUID
    version: int
    payload_hash: str
    formal_effect: Literal["none"]


class SceneReceipt(StrictModel):
    receipt_id: CanonicalUUID
    action_type: str
    actor_id: CanonicalUUID
    auth_epoch: int
    status: Literal["committed"]
    result: SceneResult
    object_versions: list[JsonValue]
    effect_task_ids: list[JsonValue]
    recorded_at: IsoDateTime


class SceneEventView(StrictModel):
    scope_id: CanonicalUUID
    scene_id: CanonicalUUID
    event_id: CanonicalUUID
    version: int
    anchor_object_id: CanonicalUUID
    anchor_revision_id: CanonicalUUID
    principal_id: CanonicalUUID
    action_id: CanonicalUUID
    kind: str
    payload: Event
    payload_hash: str
    recorded_at: IsoDateTime
    withdrawn: StrictBool


class SceneView(StrictModel):
    contract_version: Literal["tkos.workspace/0.1"]
    scene_id: CanonicalUUID
    version: int
    definition: Create
    responsibilities: dict[str, JsonValue]
    events: list[SceneEventView]
    formal_effect: Literal["none"]
    materials: dict[str, JsonValue]
    personal: dict[str, JsonValue]
    operations: list[dict[str, JsonValue]]
    monthly: dict[str, JsonValue] | None = None
    weekly: dict[str, JsonValue] | None = None
    meeting: dict[str, JsonValue] | None = None


class SceneList(StrictModel):
    items: list[SceneView]
    next_after: CanonicalUUID | None


class WorkspaceView(StrictModel):
    identity: IdentityView
    collection: str
    items: list[dict[str, JsonValue]]
    next_after: CanonicalUUID | None
    refresh_interval_seconds: int
    scene_collection_url: str | None = None
