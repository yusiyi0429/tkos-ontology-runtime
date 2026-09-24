"""凭证生命周期（#32 批次 E）：有效期、签发、轮换、吊销与列表，都经控制面（迁移所有者）完成。

新凭证只写进 0600 的私有文件，不出现在命令输出里；列表不显示摘要。需要 owner 身份，标 owner。
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from memory_service_runtime.governed import bootstrap, control, db
from memory_service_runtime.governed.errors import GovernedError

pytestmark = [pytest.mark.db, pytest.mark.owner]


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", os.environ["DATABASE_URL"])
    db._close_pool()
    label = uuid4().hex[:12]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        fixture = bootstrap.seed_scope(conn, f"runtime-acceptance-credentials-{label}",
                                       f"runtime-acceptance-credentials-company-{label}")
    yield fixture
    db._close_pool()


def _run(handler, **kwargs) -> dict:
    with control._connect() as conn:
        return handler(conn, SimpleNamespace(actor="test", reason="credential lifecycle test", **kwargs))


def _authenticates(token: str) -> bool:
    try:
        with db.transaction(token):
            return True
    except GovernedError as exc:
        assert exc.code == "UNAUTHENTICATED"
        return False


def _issue(seeded, path, **kwargs) -> dict:
    return _run(control.issue_credential, scope_id=seeded["scope_id"],
                principal_id=seeded["actors"]["agent"]["principal_id"], label="agent pilot",
                token_file=str(path), **{"expires_in_days": None, **kwargs})


def test_an_issued_credential_goes_only_to_its_private_file_and_authenticates(seeded, tmp_path):
    report = _issue(seeded, tmp_path / "agent.token", expires_in_days=30)
    token = (tmp_path / "agent.token").read_text().strip()
    assert oct((tmp_path / "agent.token").stat().st_mode & 0o777) == "0o600"
    assert token not in json.dumps(report, default=str) and len(token) >= 32
    assert report["principal_id"] == seeded["actors"]["agent"]["principal_id"] and report["expires_at"]
    assert _authenticates(token)


def test_the_token_file_is_never_overwritten(seeded, tmp_path):
    path = tmp_path / "exists.token"
    path.write_text("keep me")
    with pytest.raises(control.ControlError):
        _issue(seeded, path)
    assert path.read_text() == "keep me"


def test_an_expired_credential_is_refused(seeded, tmp_path):
    report = _issue(seeded, tmp_path / "expiring.token", expires_in_days=1)
    token = (tmp_path / "expiring.token").read_text().strip()
    with control._connect() as conn:
        control._begin(conn, seeded["scope_id"])
        conn.execute("UPDATE gov_credentials SET expires_at = clock_timestamp() - interval '1 second' "
                     "WHERE credential_id=%s", (report["credential_id"],))
    assert not _authenticates(token)


def test_rotation_without_grace_revokes_the_old_credential_at_once(seeded, tmp_path):
    old = _issue(seeded, tmp_path / "old.token")
    rotated = _run(control.rotate_credential, scope_id=seeded["scope_id"], credential_id=old["credential_id"],
                   grace_minutes=0, expires_in_days=None, token_file=str(tmp_path / "new.token"))
    assert rotated["replaced_credential_id"] == old["credential_id"]
    assert not _authenticates((tmp_path / "old.token").read_text().strip())
    assert _authenticates((tmp_path / "new.token").read_text().strip())


def test_rotation_with_grace_keeps_the_old_credential_until_the_grace_ends(seeded, tmp_path):
    old = _issue(seeded, tmp_path / "old.token")
    _run(control.rotate_credential, scope_id=seeded["scope_id"], credential_id=old["credential_id"],
         grace_minutes=60, expires_in_days=None, token_file=str(tmp_path / "new.token"))
    assert _authenticates((tmp_path / "old.token").read_text().strip())
    listed = _run(control.list_credentials, scope_id=seeded["scope_id"], principal_id=None)
    [entry] = [item for item in listed["credentials"] if item["credential_id"] == old["credential_id"]]
    assert entry["expires_at"] is not None and entry["revoked_at"] is None


def test_a_revoked_credential_is_refused_and_every_step_is_audited(seeded, tmp_path):
    issued = _issue(seeded, tmp_path / "revoked.token")
    _run(control.revoke_credential, scope_id=seeded["scope_id"], credential_id=issued["credential_id"])
    assert not _authenticates((tmp_path / "revoked.token").read_text().strip())
    with pytest.raises(control.ControlError):
        _run(control.revoke_credential, scope_id=seeded["scope_id"], credential_id=issued["credential_id"])
    status = _run(control.status, scope_id=seeded["scope_id"])
    events = [event["event_type"] for event in status["recent_control_events"]]
    assert {"credential_issued", "credential_revoked"} <= set(events)


def test_the_listing_shows_identities_and_dates_but_never_digests(seeded, tmp_path):
    _issue(seeded, tmp_path / "listed.token")
    listed = _run(control.list_credentials, scope_id=seeded["scope_id"], principal_id=None)
    assert listed["credentials"] and all(
        set(item) == {"credential_id", "principal_id", "label", "created_at", "expires_at", "revoked_at"}
        for item in listed["credentials"])
    assert "digest" not in json.dumps(listed, default=str)
