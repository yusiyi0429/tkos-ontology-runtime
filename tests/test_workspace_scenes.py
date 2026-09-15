"""Finite scene contracts and source/field handling; HTTP/DB checks live in acceptance."""
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.workspace_models import WorkspaceCommand
from memory_service_runtime.governed.workspace_readers import differences
from memory_service_runtime.governed.workspace_service import current_answers, digest, latest, pointer


def reference():
    return {"object_id": str(uuid4()), "revision_id": str(uuid4()), "payload_hash": "a" * 64}


def command():
    return {"contract_version": "tkos.workspace/0.1", "scene_id": str(uuid4()),
            "expected_version": 1, "idempotency_key": str(uuid4()),
            "event": {"kind": "read", "subject_ref": reference()}}


@pytest.mark.parametrize("change", [
    {"contract_version": "tkos.method/0.1"}, {"expected_version": True}, {"expected_version": -1},
    {"expected_version": 0}, {"idempotency_key": "short"}, {"principal_id": str(uuid4())},
    {"event": {"kind": "confirm_candidate"}}, {"event": {"kind": "progress", "percent": 80}},
    {"event": {"kind": "meeting_start", "recording_requested": "true"}},
    {"event": {"kind": "weekly_confirm", "material_event_id": str(uuid4()), "answer_event_ids": [], "signal": "done"}},
])
def test_rejects_unversioned_untyped_or_spoofed_writes(change):
    body = command() | change
    with pytest.raises(ValidationError):
        WorkspaceCommand.model_validate(body)


def test_scene_create_requires_zero_version_and_exact_anchor():
    body = command()
    body["event"] = {"kind": "create", "scene_type": "monthly", "anchor_ref": reference(),
        "external_id": "clark-review-1", "title": "Monthly review", "owner_assignment_id": str(uuid4())}
    with pytest.raises(ValidationError):
        WorkspaceCommand.model_validate(body)
    body["expected_version"] = 0
    assert WorkspaceCommand.model_validate(body).event.kind == "create"
    del body["event"]["anchor_ref"]["revision_id"]
    with pytest.raises(ValidationError):
        WorkspaceCommand.model_validate(body)


@pytest.mark.parametrize("field", ["/missing", "/items/-1", "/items/01", "/items/1", "/items/0/title/child"])
def test_field_anchor_must_exist_at_exact_version(field):
    with pytest.raises(GovernedError) as exc:
        pointer({"items": [{"title": "A"}]}, field)
    assert exc.value.code == "INVALID_REQUEST"


def test_json_pointer_escaping_and_list_fields():
    assert pointer({"a/b": {"c~d": ["text"]}}, "/a~1b/c~0d/0") == "text"


def test_diff_is_deterministic_and_never_invents_rationale():
    before = {"title": "Old", "criteria": ["one"], "removed": "old"}
    after = {"title": "New", "criteria": ["one", "two"], "added": "new"}
    result = differences(before, after)
    assert [r["field_path"] for r in result] == ["/added", "/criteria", "/removed", "/title"]
    assert result == differences(deepcopy(before), deepcopy(after))
    assert all("rationale" not in r for r in result)
    assert differences(before, before) == []


def test_workspace_hash_covers_full_material_and_order_independent_keys():
    body = command()
    assert digest(body) == digest(dict(reversed(list(body.items()))))
    changed = deepcopy(body)
    changed["event"]["note"] = "different statement"
    assert digest(body) != digest(changed)


def test_withdrawal_never_revives_superseded_answer_or_publication():
    history = [
        {"kind": "weekly_answer", "event_id": "old", "principal_id": "person", "payload": {"question_id": "q", "material_event_id": "m"}},
        {"kind": "weekly_answer", "event_id": "new", "principal_id": "person", "payload": {"question_id": "q", "material_event_id": "m"}},
        {"kind": "withdraw", "payload": {"event_id": "new"}},
    ]
    assert current_answers(history, "m", "person") == {}
    assert latest(history, "weekly_answer", "person") is None


def test_openapi_exposes_finite_event_union_without_changing_method_contract():
    from memory_service_app.main import app
    schema = app.openapi()
    event = schema["components"]["schemas"]["WorkspaceCommand"]["properties"]["event"]
    assert event["discriminator"]["propertyName"] == "kind"
    assert "meeting_publish" in event["discriminator"]["mapping"]
    assert "/v1/identity" in schema["paths"]
    assert "/v1/workspace-scenes/events" in schema["paths"]
    from memory_service_runtime.governed.method_models import METHOD_ACTION_PARAMS
    assert len(METHOD_ACTION_PARAMS) == 49
    assert not any(k.startswith("workspace.") for k in METHOD_ACTION_PARAMS)
