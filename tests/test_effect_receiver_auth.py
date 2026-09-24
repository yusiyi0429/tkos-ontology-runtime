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


def test_the_dispatch_headers_carry_the_bearer_credential_and_the_effect_key():
    assert effects.dispatch_headers("r:t", "effect-receiver-secret-0123456789") == {
        "Idempotency-Key": "r:t", "Authorization": "Bearer effect-receiver-secret-0123456789"}
    assert effects.dispatch_headers("r:t", None) == {"Idempotency-Key": "r:t"}


def test_the_reference_receiver_accepts_any_current_token_so_rotation_can_overlap(tmp_path):
    """轮换：接收端先同时接受新旧两个令牌，派发端换成新的，再删掉旧的，中间不会有 401。"""
    from acceptance.runtime import receiver

    path = tmp_path / "tokens"
    path.write_text("old-effect-token-0123456789\n\nnew-effect-token-0123456789\n")
    tokens = receiver.load_tokens(path)
    assert tokens == ["old-effect-token-0123456789", "new-effect-token-0123456789"]
    assert receiver.authorized("Bearer old-effect-token-0123456789", tokens)
    assert receiver.authorized("Bearer new-effect-token-0123456789", tokens)
    assert not receiver.authorized("Bearer other-effect-token-0123456789", tokens)
    assert not receiver.authorized("", tokens)
    assert not receiver.authorized("Bearer old-effect-token-0123456789", [])  # 空文件：一律拒绝
