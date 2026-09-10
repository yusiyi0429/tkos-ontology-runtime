"""Offline tests for the optional contract_version request field (no database).

Absent/null must not change the legacy request_hash input
(model_dump(exclude_none=True)); unknown fields and non-A1 action types
(A2/A3) must be rejected by the strict model.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from pydantic import ValidationError

from memory_service_runtime.governed.models import ActionRequest


def _envelope(**overrides):
    base = getattr(_envelope, "_base", None)
    if base is None:
        base = {
            "action_type": "revoke_assignment",
            "target": None,
            "expected_versions": [],
            "idempotency_key": uuid.uuid4().hex + uuid.uuid4().hex[:8],
            "reason": "offline contract_version test",
            "params": {"assignment_id": str(uuid.uuid4())},
        }
        _envelope._base = base
    data = {**base, "params": dict(base["params"])}
    data.update(overrides)
    return data


def _legacy_hash(request: ActionRequest) -> str:
    canonical = json.dumps(request.model_dump(mode="json", exclude_none=True),
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def test_absent_and_null_contract_version_share_legacy_hash():
    absent = ActionRequest.model_validate(_envelope())
    explicit_null = ActionRequest.model_validate(_envelope(contract_version=None))
    assert absent.model_dump(mode="json", exclude_none=True) == (
        explicit_null.model_dump(mode="json", exclude_none=True))
    assert _legacy_hash(absent) == _legacy_hash(explicit_null)
    assert "contract_version" not in absent.model_dump(mode="json", exclude_none=True)


def test_explicit_contract_version_is_part_of_new_format_request():
    request = ActionRequest.model_validate(
        _envelope(contract_version="tkos.contract-a/0.1"))
    dumped = request.model_dump(mode="json", exclude_none=True)
    assert dumped["contract_version"] == "tkos.contract-a/0.1"
    assert _legacy_hash(request) != _legacy_hash(ActionRequest.model_validate(
        {**_envelope(), "idempotency_key": request.idempotency_key,
         "params": request.params.model_dump(mode="json")}))


def test_contract_version_must_be_a_nonempty_string():
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(_envelope(contract_version=""))
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(_envelope(contract_version=123))
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(_envelope(contract_version="x" * 201))


def test_unknown_envelope_field_rejected():
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(_envelope(unexpected_field="x"))


def test_a2_a3_action_types_rejected():
    for action_type in ("compose_company", "form_company", "ic_handover",
                        "record_composition_manifest"):
        with pytest.raises(ValidationError):
            ActionRequest.model_validate(_envelope(action_type=action_type))
