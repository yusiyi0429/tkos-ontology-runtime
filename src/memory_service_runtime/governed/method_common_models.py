"""Method-specific transport; does not change old action serialization."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, BeforeValidator
from uuid import UUID
from .a2_models import ObjectRef


def uuid_text(value):
    if not isinstance(value, str):
        raise ValueError("UUID must be text")
    return str(UUID(value))

UUIDText = Annotated[StrictStr, BeforeValidator(uuid_text)]
Text = Annotated[StrictStr, Field(min_length=1, max_length=10000)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunPayload(Strict):
    title: Text
    method: Literal["M1A", "M1B", "M1A+M1B"]


class OpenRun(Strict):
    domain_id: UUIDText
    payload: RunPayload


class AttachRun(Strict):
    object_ref: ObjectRef


class RunReason(Strict):
    note: Text


class Attempt(Strict):
    step_key: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    outcome: Literal["started", "failed", "abandoned"]
    note: Text


METHOD_COMMON_PARAMS = {
    "method_open_run": OpenRun, "method_attach_run": AttachRun,
    "method_pause_run": RunReason, "method_resume_run": RunReason,
    "method_record_attempt": Attempt,
}
METHOD_COMMON_TARGETS = {key: frozenset({"MethodRun"}) for key in METHOD_COMMON_PARAMS}
METHOD_COMMON_TARGETS["method_open_run"] = frozenset()
METHOD_COMMON_PAYLOADS = {"MethodRun": RunPayload}
