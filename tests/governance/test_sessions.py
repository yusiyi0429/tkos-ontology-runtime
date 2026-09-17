from contextlib import contextmanager
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from memory_service_app import governance_sessions as sessions
from memory_service_runtime.governed.errors import GovernedError


def request(sid, csrf=''):
    return Request({'type': 'http', 'headers': [(b'cookie', (sessions.COOKIE+'='+sid).encode()), (b'x-csrf-token', csrf.encode())]})


@pytest.fixture
def setup(monkeypatch):
    sessions._SESSIONS.clear(); sessions._FAILURES.clear()
    identity = {'scope_id': 'scope', 'principal_id': 'person', 'assignments': [{}]}
    account = {'code_digest': hashlib.sha256(b'personal-long-login-code').hexdigest()}
    monkeypatch.setattr(sessions, 'accounts', lambda: {'alice': account})
    monkeypatch.setattr(sessions, 'resolve', lambda _: (account, 'server-private-bearer', identity))
    return account, identity


def test_session_returns_no_runtime_credential_and_csrf_required(setup):
    sid, s, identity = sessions.login('alice', 'personal-long-login-code', 'local')
    assert 'server-private-bearer' not in repr((sid, s, identity))
    assert sessions.authenticate(request(sid))[1] == identity
    with pytest.raises(GovernedError, match='Current authority'):
        sessions.authenticate(request(sid), write=True)
    assert sessions.authenticate(request(sid, s['csrf']), write=True)[0] == 'server-private-bearer'


def test_logout_revokes_cookie(setup):
    sid, _, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    sessions.logout(request(sid))
    with pytest.raises(GovernedError): sessions.authenticate(request(sid))


def test_reset_code_invalidates_existing_session(setup):
    account, _ = setup
    sid, _, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    account['code_digest'] = 'changed'
    with pytest.raises(GovernedError): sessions.authenticate(request(sid))


def test_remapped_account_cannot_inherit_session(setup):
    _, identity = setup
    sid, _, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    identity['principal_id'] = 'different'
    with pytest.raises(GovernedError): sessions.authenticate(request(sid))


@pytest.mark.parametrize('advance', [1801, 8 * 3600 + 1])
def test_session_expiration(setup, monkeypatch, advance):
    sid, s, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    monkeypatch.setattr(sessions.time, 'monotonic', lambda: s['created'] + advance)
    with pytest.raises(GovernedError): sessions.authenticate(request(sid))


def test_login_rate_limit_across_usernames(setup):
    for i in range(10):
        with pytest.raises(GovernedError): sessions.login(str(i), 'wrong', 'local')
    with pytest.raises(GovernedError) as exc: sessions.login('alice', 'personal-long-login-code', 'local')
    assert exc.value.status == 429


def test_no_shared_or_symlinked_credentials(tmp_path):
    path = tmp_path / 'token'; path.write_text('secret'); path.chmod(0o644)
    with pytest.raises(GovernedError): sessions.private_file(path)
    path.chmod(0o600)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(GovernedError): sessions.private_file(link)


def test_restart_requires_login(setup):
    sid, _, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    sessions._SESSIONS.clear()
    with pytest.raises(GovernedError): sessions.authenticate(request(sid))
