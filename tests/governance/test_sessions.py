import hashlib
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from memory_service_app import governance_sessions as sessions
from memory_service_runtime.governed.errors import GovernedError


def request(sid, csrf='', background=False):
    headers = [(b'cookie', (sessions.COOKIE+'='+sid).encode()), (b'x-csrf-token', csrf.encode())]
    if background:
        headers.append((sessions.BACKGROUND_HEADER.encode(), b'1'))
    return Request({'type': 'http', 'headers': headers})


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
    with pytest.raises(GovernedError) as exc:
        sessions.authenticate(request(sid), write=True)
    assert (exc.value.code, exc.value.status) == ('CSRF_TOKEN_INVALID', 403)
    assert sessions.authenticate(request(sid, s['csrf']), write=True)[0] == 'server-private-bearer'


def test_wrong_login_code_is_a_login_failure_not_an_expired_session(setup):
    for username, code in (('alice', 'wrong-but-long-enough-code'), ('nobody', 'personal-long-login-code')):
        with pytest.raises(GovernedError) as exc:
            sessions.login(username, code, 'local')
        assert (exc.value.code, exc.value.status) == ('LOGIN_FAILED', 401)
    assert not sessions._SESSIONS


def test_background_polls_do_not_extend_the_idle_limit(setup, monkeypatch):
    sid, s, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    clock = {'now': s['created']}
    monkeypatch.setattr(sessions.time, 'monotonic', lambda: clock['now'])
    for minute in range(1, 30):
        clock['now'] = s['created'] + minute * 60
        sessions.authenticate(request(sid, background=True))
    clock['now'] = s['created'] + 1801
    with pytest.raises(GovernedError) as exc:
        sessions.authenticate(request(sid, background=True))
    assert exc.value.code == 'UNAUTHENTICATED'


def test_own_requests_and_writes_still_count_as_activity(setup, monkeypatch):
    sid, s, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    clock = {'now': s['created'] + 1000}
    monkeypatch.setattr(sessions.time, 'monotonic', lambda: clock['now'])
    sessions.authenticate(request(sid))
    clock['now'] = s['created'] + 2000
    sessions.authenticate(request(sid, background=True))
    # A write carrying the marker is still the person's own action.
    sessions.authenticate(request(sid, s['csrf'], background=True), write=True)
    clock['now'] = s['created'] + 3700
    assert sessions.authenticate(request(sid, background=True))[1]['principal_id'] == 'person'


def test_dashboard_reads_on_the_personal_session_honour_the_background_marker(setup, monkeypatch):
    from fastapi import FastAPI
    from memory_service_app import dashboard as facade
    from tests.asgi_client import get
    settings = SimpleNamespace(tkos_dashboard_enabled=True, tkos_dashboard_viewer_token_file='',
                               tkos_dashboard_env_label='synthetic', tkos_dashboard_synthetic=True,
                               tkos_dashboard_allowed_hosts='127.0.0.1:58802',
                               tkos_dashboard_allowed_origins='http://127.0.0.1:58802',
                               tkos_dashboard_assets_dir='', tkos_governance_workbench_enabled=True)
    monkeypatch.setattr(facade, '_enabled_from_env', lambda: True)
    monkeypatch.setattr(facade, 'get_settings', lambda: settings)
    monkeypatch.setattr(facade.reads, 'read_overview', lambda token, **_: {'personal': token == 'server-private-bearer'})
    app = FastAPI()
    facade.mount_dashboard(app)
    sid, s, _ = sessions.login('alice', 'personal-long-login-code', 'local')
    key = hashlib.sha256(sid.encode()).hexdigest()
    headers = {'host': '127.0.0.1:58802', 'cookie': f'{sessions.COOKIE}={sid}'}
    monkeypatch.setattr(sessions.time, 'monotonic', lambda: s['created'] + 600)
    polled = get(app, '/dashboard/api/v1/overview', headers={**headers, sessions.BACKGROUND_HEADER: '1'})
    assert polled.status_code == 200 and polled.json() == {'personal': True}
    assert sessions._SESSIONS[key]['seen'] == s['created']
    assert get(app, '/dashboard/api/v1/overview', headers=headers).status_code == 200
    assert sessions._SESSIONS[key]['seen'] == s['created'] + 600


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
