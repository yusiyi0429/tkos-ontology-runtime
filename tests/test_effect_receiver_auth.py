"""外部效果接收端认证：派发带 Bearer 凭证；非本机接收端没配凭证就是配置错误，不派发。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from memory_service_runtime.governed import effects
from memory_service_runtime.handlers import TaskExecutionError

TASK = SimpleNamespace(task_id="t", tenant_id="x", organization_id="y",
                       payload={"scope_id": "s", "receipt_id": "r"})


def test_a_remote_receiver_without_a_credential_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("GOVERNED_EFFECT_URL", "https://effects.example.com/effects")
    monkeypatch.delenv("GOVERNED_EFFECT_TOKEN", raising=False)
    monkeypatch.delenv("GOVERNED_EFFECT_TOKEN_FILE", raising=False)
    with pytest.raises(TaskExecutionError) as caught:
        effects.governance_dispatch(TASK)
    assert (caught.value.code, caught.value.retryable) == ("governance_effect_credential_missing", False)


def test_the_dispatch_headers_carry_the_bearer_credential_and_the_effect_key(monkeypatch):
    monkeypatch.delenv("GOVERNED_EFFECT_TOKEN_FILE", raising=False)
    monkeypatch.setenv("GOVERNED_EFFECT_TOKEN", "effect-receiver-secret-0123456789")
    assert effects.dispatch_headers("r:t") == {"Idempotency-Key": "r:t",
                                               "Authorization": "Bearer effect-receiver-secret-0123456789"}
    monkeypatch.delenv("GOVERNED_EFFECT_TOKEN")
    assert effects.dispatch_headers("r:t") == {"Idempotency-Key": "r:t"}
