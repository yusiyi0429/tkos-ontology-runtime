"""凭证生命周期（#32 批次 E）：有效期、签发、轮换、吊销与列表，都经控制面（迁移所有者）完成。

新凭证只写进 0600 的私有文件，不出现在命令输出与审计事件里；列表不显示摘要。吊销与零宽限轮换先推进
auth_epoch，与在途的已授权事务串行。需要 owner 身份，标 owner；应用角色那条另要 APP_DATABASE_URL。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
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


def test_every_step_is_audited_without_the_credential_or_its_digest(seeded, tmp_path):
    first = _issue(seeded, tmp_path / "first.token")
    rotated = _run(control.rotate_credential, scope_id=seeded["scope_id"], credential_id=first["credential_id"],
                   grace_minutes=0, expires_in_days=30, token_file=str(tmp_path / "second.token"))
    _run(control.revoke_credential, scope_id=seeded["scope_id"], credential_id=rotated["credential_id"])
    tokens = [(tmp_path / name).read_text().strip() for name in ("first.token", "second.token")]
    assert not any(_authenticates(token) for token in tokens)
    with pytest.raises(control.ControlError):  # 已吊销的再吊销是错误
        _run(control.revoke_credential, scope_id=seeded["scope_id"], credential_id=rotated["credential_id"])
    with control._connect() as conn:
        control._begin(conn, seeded["scope_id"])
        events = conn.execute("SELECT event_type, detail FROM gov_protocol_control_events WHERE scope_id=%s"
                              " ORDER BY recorded_at, event_id", (seeded["scope_id"],)).fetchall()
    assert [event["event_type"] for event in events][-3:] == ["credential_issued", "credential_rotated",
                                                              "credential_revoked"]
    assert events[-2]["detail"]["auth_epoch"] < events[-1]["detail"]["auth_epoch"]
    written = json.dumps([event["detail"] for event in events])
    assert not any(value in written for token in tokens
                   for value in (token, hashlib.sha256(token.encode("utf-8")).hexdigest()))


@pytest.mark.parametrize("how", ["revoke", "rotate_without_grace"])
def test_revocation_waits_for_an_in_flight_transaction_that_already_passed_the_fence(seeded, tmp_path, how):
    issued = _issue(seeded, tmp_path / "held.token")
    token = (tmp_path / "held.token").read_text().strip()
    finished, failures = threading.Event(), []

    def revoke():
        try:
            if how == "revoke":
                _run(control.revoke_credential, scope_id=seeded["scope_id"], credential_id=issued["credential_id"])
            else:
                _run(control.rotate_credential, scope_id=seeded["scope_id"], credential_id=issued["credential_id"],
                     grace_minutes=0, expires_in_days=None, token_file=str(tmp_path / "next.token"))
            finished.set()
        except Exception as exc:  # noqa: BLE001 - 交给主线程断言
            failures.append(exc)

    with db.transaction(token):  # 已过栅栏、还没提交
        worker = threading.Thread(target=revoke)
        worker.start()
        assert _waits_for_a_lock("UPDATE gov_scopes SET auth_epoch")
        assert not finished.is_set()
    worker.join(30)
    assert not failures and finished.is_set()
    assert not _authenticates(token)


def _waits_for_a_lock(statement: str, seconds: float = 10.0) -> bool:
    """在 seconds 内看到另一个会话执行这条语句、正等着锁。autocommit：事务里的 pg_stat_activity 是一次快照。"""
    deadline = time.monotonic() + seconds
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as watcher:
        while time.monotonic() < deadline:
            if watcher.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s",
                               (statement + "%",)).fetchone()[0]:
                return True
            time.sleep(0.05)
    return False


def test_a_failed_issue_leaves_no_token_file_and_the_path_can_be_reused(seeded, tmp_path):
    path = tmp_path / "retry.token"
    with pytest.raises(control.ControlError) as failed:
        _run(control.issue_credential, scope_id=seeded["scope_id"], principal_id=str(uuid4()), label="missing",
             token_file=str(path), expires_in_days=None)
    assert failed.value.code == "PRINCIPAL_NOT_FOUND" and not path.exists()
    _issue(seeded, path)
    assert _authenticates(path.read_text().strip())


def test_the_application_role_sees_only_its_own_credential_even_with_the_control_plane_setting(seeded):
    app_url = os.environ.get("APP_DATABASE_URL", "").strip()
    if not app_url:
        pytest.fail("需要 APP_DATABASE_URL（应用角色）：用 infra.py run --migration 跑会注入它", pytrace=False)
    agent = seeded["actors"]["agent"]
    with psycopg.connect(app_url, row_factory=dict_row) as conn:
        db.set_write_capability(conn)
        conn.execute("SELECT set_config('app.gov_control_plane', 'on', true)")
        conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (seeded["scope_id"],))
        conn.execute("SELECT set_config('app.governed_credential_digest', %s, true)",
                     (hashlib.sha256(agent["token"].encode("utf-8")).hexdigest(),))
        assert conn.execute("SELECT gov_control_plane_on() AS on").fetchone()["on"] is False
        rows = conn.execute("SELECT principal_id FROM gov_credentials").fetchall()
    assert [str(row["principal_id"]) for row in rows] == [agent["principal_id"]]


def test_the_listing_shows_identities_and_dates_but_never_digests(seeded, tmp_path):
    _issue(seeded, tmp_path / "listed.token")
    listed = _run(control.list_credentials, scope_id=seeded["scope_id"], principal_id=None)
    assert listed["credentials"] and all(
        set(item) == {"credential_id", "principal_id", "label", "created_at", "expires_at", "revoked_at"}
        for item in listed["credentials"])
    assert "digest" not in json.dumps(listed, default=str)
