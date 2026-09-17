"""tkos.workspace/0.2 standalone source collaboration records.

No Method anchor, no MethodRun, no formal business effect. Sources are private
by default and readable only through scene membership plus exact-material
grants. These records are deliberately a separate contract from
``tkos.workspace/0.1``; 0.1 scene semantics are not relaxed and 0.2 scenes never
require an anchor.
"""
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import Field, JsonValue, StrictBool, model_validator

from .a2_models import CanonicalUUID, IsoDateTime, NEStr, Sha256Hex, StrictModel
from .workspace_models import WorkspaceCommand

ContractV02 = Literal["tkos.workspace/0.2"]
MAX_SEGMENTS = 2000
MAX_ITEMS = 50
MAX_CITATIONS = 100


class AgentBinding(StrictModel):
    agent_principal_id: CanonicalUUID
    owner_principal_id: CanonicalUUID


class SceneCreate(StrictModel):
    kind: Literal["scene_create"]
    scene_type: Literal["meeting", "document", "selected_conversation"]
    external_id: Annotated[NEStr, Field(max_length=200)]
    title: NEStr
    owner_principal_id: CanonicalUUID
    participant_principal_ids: Annotated[list[CanonicalUUID], Field(max_length=100)] = []
    agent_bindings: Annotated[list[AgentBinding], Field(max_length=100)] = []

    @model_validator(mode="after")
    def distinct_people(self):
        people = [self.owner_principal_id, *self.participant_principal_ids]
        if len(people) != len(set(people)):
            raise ValueError("scene owner and participants must be distinct")
        agents = [item.agent_principal_id for item in self.agent_bindings]
        if len(agents) != len(set(agents)):
            raise ValueError("agent bindings must be distinct per agent")
        return self


class Segment(StrictModel):
    speaker: Annotated[NEStr, Field(max_length=200)] | None = None
    text: Annotated[NEStr, Field(max_length=8000)]
    occurred_at: IsoDateTime | None = None


class SourceAdd(StrictModel):
    kind: Literal["source_add"]
    system: Annotated[NEStr, Field(max_length=100)]
    external_id: Annotated[NEStr, Field(max_length=300)]
    title: NEStr
    media_type: Annotated[NEStr, Field(max_length=120)]
    acquired_at: IsoDateTime
    origin_label: NEStr | None = None
    source_revision: Annotated[NEStr, Field(max_length=200)] | None = None
    sensitivity: Literal["private"] = "private"


class EvidenceRef(StrictModel):
    """Exact EvidenceAsset revision under its active domain protocol.

    Deliberately protocol-neutral: any registered 0.3+ (or later) EvidenceAsset
    is accepted. The Runtime never converts it into a Method object.
    """
    object_id: CanonicalUUID
    revision_id: CanonicalUUID
    payload_hash: Sha256Hex


class SourceVersion(StrictModel):
    kind: Literal["source_version"]
    source_id: CanonicalUUID
    fingerprint: Sha256Hex
    media_type: Annotated[NEStr, Field(max_length=120)]
    acquired_at: IsoDateTime
    origin_label: NEStr | None = None
    segments: Annotated[list[Segment], Field(min_length=1, max_length=MAX_SEGMENTS)]
    evidence_ref: EvidenceRef | None = None


class SourceCorrect(StrictModel):
    kind: Literal["source_correct"]
    source_id: CanonicalUUID
    corrects_event_id: CanonicalUUID
    reason: NEStr
    fingerprint: Sha256Hex
    media_type: Annotated[NEStr, Field(max_length=120)]
    acquired_at: IsoDateTime
    origin_label: NEStr | None = None
    segments: Annotated[list[Segment], Field(min_length=1, max_length=MAX_SEGMENTS)]
    evidence_ref: EvidenceRef | None = None


class SourceWithdraw(StrictModel):
    kind: Literal["source_withdraw"]
    source_id: CanonicalUUID
    version_event_id: CanonicalUUID | None = None
    reason: NEStr


class SourceShare(StrictModel):
    kind: Literal["source_share"]
    source_id: CanonicalUUID
    version_event_id: CanonicalUUID
    payload_hash: Sha256Hex
    share_to_principal_id: CanonicalUUID
    note: NEStr | None = None


class SourceUnshare(StrictModel):
    kind: Literal["source_unshare"]
    share_event_id: CanonicalUUID
    reason: NEStr


class ModelSpec(StrictModel):
    provider: Annotated[NEStr, Field(max_length=200)]
    name: Annotated[NEStr, Field(max_length=200)]
    version: Annotated[NEStr, Field(max_length=200)]
    parameters: dict[str, JsonValue] | None = None


class SourceVersionRef(StrictModel):
    source_id: CanonicalUUID
    version_event_id: CanonicalUUID
    payload_hash: Sha256Hex


class AgentRun(StrictModel):
    kind: Literal["agent_run"]
    agent_principal_id: CanonicalUUID
    purpose: NEStr
    model: ModelSpec
    input_refs: Annotated[list[SourceVersionRef], Field(min_length=1, max_length=100)]
    context_id: CanonicalUUID
    external_run_id: Annotated[NEStr, Field(max_length=200)] | None = None
    status: Literal["succeeded", "failed", "unknown"]
    started_at: IsoDateTime
    finished_at: IsoDateTime | None = None
    error: NEStr | None = None
    output_refs: Annotated[list[JsonValue], Field(max_length=100)] = []
    draft_event_id: CanonicalUUID | None = None

    @model_validator(mode="after")
    def honest_status(self):
        produced = bool(self.output_refs) or self.draft_event_id is not None
        if self.status == "succeeded":
            if self.finished_at is None or not produced or self.error is not None:
                raise ValueError("succeeded runs require finished_at, output and no error")
        elif self.status == "failed":
            if self.finished_at is None or self.error is None or produced:
                raise ValueError("failed runs require finished_at and error, without output")
        elif produced:
            raise ValueError("unknown runs must not claim output")
        if self.finished_at is not None:
            start, end = datetime.fromisoformat(self.started_at), datetime.fromisoformat(self.finished_at)
            if end < start:
                raise ValueError("run cannot finish before it starts")
        return self


class Citation(StrictModel):
    source_id: CanonicalUUID
    version_event_id: CanonicalUUID
    payload_hash: Sha256Hex
    segment_index: Annotated[int, Field(strict=True, ge=0)]
    quote: Annotated[NEStr, Field(max_length=4000)]


class DraftItem(StrictModel):
    item_kind: Literal["fact", "request", "suggestion", "accepted"]
    text: NEStr
    citations: Annotated[list[Citation], Field(min_length=1, max_length=MAX_CITATIONS)]


class FollowupDraft(StrictModel):
    kind: Literal["followup_draft"]
    title: NEStr
    run_event_id: CanonicalUUID | None = None
    items: Annotated[list[DraftItem], Field(min_length=1, max_length=MAX_ITEMS)]


class DraftDecision(StrictModel):
    kind: Literal["draft_decision"]
    draft_event_id: CanonicalUUID
    item_index: Annotated[int, Field(strict=True, ge=0)]
    decision: Literal["accepted", "rejected", "noted"]
    note: NEStr | None = None


SourceEvent = Annotated[
    Union[SceneCreate, SourceAdd, SourceVersion, SourceCorrect, SourceWithdraw,
          SourceShare, SourceUnshare, AgentRun, FollowupDraft, DraftDecision],
    Field(discriminator="kind"),
]


class SourceSceneCommand(StrictModel):
    contract_version: ContractV02
    scene_id: CanonicalUUID
    expected_version: Annotated[int, Field(strict=True, ge=0)]
    idempotency_key: Annotated[NEStr, Field(min_length=16, max_length=128)]
    event: SourceEvent

    @model_validator(mode="after")
    def create_version(self):
        if self.idempotency_key != self.idempotency_key.strip():
            raise ValueError("idempotency key must not have surrounding whitespace")
        if (self.event.kind == "scene_create") != (self.expected_version == 0):
            raise ValueError("only scene_create uses version zero")
        return self


WorkspaceCommandEnvelope = Annotated[
    Union[WorkspaceCommand, SourceSceneCommand], Field(discriminator="contract_version")
]


class SourceContextItem(StrictModel):
    source_id: CanonicalUUID
    version_event_id: CanonicalUUID
    payload_hash: Sha256Hex


class SourceContextCreate(StrictModel):
    contract_version: ContractV02
    scene_id: CanonicalUUID
    idempotency_key: Annotated[NEStr, Field(min_length=16, max_length=128)]
    purpose: NEStr
    items: Annotated[list[SourceContextItem], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def distinct_items(self):
        if self.idempotency_key != self.idempotency_key.strip():
            raise ValueError("idempotency key must not have surrounding whitespace")
        keys = [(item.source_id, item.version_event_id) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("context items must be distinct exact source versions")
        return self


class SourceSceneResult(StrictModel):
    contract_version: ContractV02
    scene_id: CanonicalUUID
    event_id: CanonicalUUID
    version: int
    kind: str
    payload_hash: Sha256Hex
    source_id: CanonicalUUID | None = None
    evidence_linked: StrictBool = False
    formal_effect: Literal["none"]


class SourceSceneReceipt(StrictModel):
    receipt_id: CanonicalUUID
    action_type: str
    actor_id: CanonicalUUID
    auth_epoch: int
    status: Literal["committed"]
    result: SourceSceneResult
    object_versions: list[JsonValue]
    effect_task_ids: list[JsonValue]
    recorded_at: IsoDateTime


class SourceSceneView(StrictModel):
    contract_version: ContractV02
    scene_id: CanonicalUUID
    version: int
    scene: dict[str, JsonValue]
    sources: list[dict[str, JsonValue]]
    runs: list[dict[str, JsonValue]]
    drafts: list[dict[str, JsonValue]]
    decisions: list[dict[str, JsonValue]]
    operations: list[dict[str, JsonValue]]
    formal_effect: Literal["none"]


class SourceSceneSummary(StrictModel):
    scene_id: CanonicalUUID
    scene_type: str
    external_id: str
    title: str
    owner_principal_id: CanonicalUUID
    participant_principal_ids: list[str]
    version: int
    created_at: IsoDateTime


class SourceSceneList(StrictModel):
    items: list[SourceSceneSummary]
    next_after: CanonicalUUID | None


class SourceContextView(StrictModel):
    contract_version: ContractV02
    context_id: CanonicalUUID
    scene_id: CanonicalUUID
    purpose: str | None = None
    created_by_principal_id: CanonicalUUID
    recorded_at: IsoDateTime
    complete: StrictBool
    withheld: StrictBool = False
    items: list[dict[str, JsonValue]]
