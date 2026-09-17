"""Local human sessions. Runtime credentials never cross the browser boundary."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import stat
import threading
import time
from pathlib import Path

from memory_service_runtime.governed import db, workspace_readers
from memory_service_runtime.governed.errors import GovernedError
from .settings import get_settings

COOKIE = 'tkos_governance_session'
_LOCK = threading.RLock()
_SESSIONS: dict[str, dict] = {}
_FAILURES: dict[str, list[float]] = {}


def private_file(path: Path) -> bytes:
    if path.is_symlink():
        raise GovernedError('WORKBENCH_NOT_CONFIGURED', status=503)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise GovernedError('WORKBENCH_NOT_CONFIGURED', status=503)
    return path.read_bytes()


def accounts() -> dict:
    try:
        return json.loads(private_file(Path(get_settings().tkos_governance_accounts_file)))
    except (OSError, ValueError):
        raise GovernedError('WORKBENCH_NOT_CONFIGURED', status=503) from None


def resolve(username: str):
    config = accounts()
    account = config.get(username)
    if not account or not account.get('enabled', True):
        raise GovernedError('UNAUTHENTICATED')
    try:
        token = private_file(Path(account['token_file'])).decode().strip()
    except (OSError, ValueError, KeyError):
        raise GovernedError('WORKBENCH_NOT_CONFIGURED', status=503) from None
    with db.transaction(token) as (conn, ctx):
        if (ctx.principal_type != 'human' or ctx.scope_id != account['scope_id']
                or ctx.principal_id != account['principal_id'] or not ctx.assignments):
            raise GovernedError('FORBIDDEN')
        identity = workspace_readers.identity(conn, ctx)
    return account, token, identity


def login(username: str, code: str, peer: str):
    now = time.monotonic()
    # Global peer bucket also bounds arbitrary-username attacks in this local service.
    with _LOCK:
        for key in list(_FAILURES):
            _FAILURES[key] = [t for t in _FAILURES[key] if now - t < 60]
            if not _FAILURES[key]:
                del _FAILURES[key]
        if len(_FAILURES.get(peer, [])) >= 10:
            raise GovernedError('LOGIN_RATE_LIMITED', status=429)
        _FAILURES.setdefault(peer, []).append(now)
    account = accounts().get(username, {})
    digest = hashlib.sha256(code.encode()).hexdigest()
    if not hmac.compare_digest(digest, account.get('code_digest', '0' * 64)):
        raise GovernedError('UNAUTHENTICATED')
    account, _, identity = resolve(username)
    sid = secrets.token_urlsafe(32)
    session = {'username': username, 'code_digest': account['code_digest'],
               'scope_id': identity['scope_id'], 'principal_id': identity['principal_id'],
               'csrf': secrets.token_urlsafe(32), 'created': now, 'seen': now}
    with _LOCK:
        for key, old in list(_SESSIONS.items()):
            if now - old['created'] > 8 * 3600 or now - old['seen'] > 1800:
                del _SESSIONS[key]
        _SESSIONS[hashlib.sha256(sid.encode()).hexdigest()] = session
    return sid, session, identity


def authenticate(request, *, write=False):
    sid = request.cookies.get(COOKIE, '')
    key = hashlib.sha256(sid.encode()).hexdigest()
    now = time.monotonic()
    with _LOCK:
        session = _SESSIONS.get(key)
        if not session or now - session['created'] > 8 * 3600 or now - session['seen'] > 1800:
            _SESSIONS.pop(key, None)
            raise GovernedError('UNAUTHENTICATED')
        session = dict(session)
    if write and not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf']):
        raise GovernedError('FORBIDDEN')
    account, token, identity = resolve(session['username'])
    if (account['code_digest'] != session['code_digest'] or identity['scope_id'] != session['scope_id']
            or identity['principal_id'] != session['principal_id']):
        logout(request)
        raise GovernedError('UNAUTHENTICATED')
    with _LOCK:
        if key not in _SESSIONS:
            raise GovernedError('UNAUTHENTICATED')
        _SESSIONS[key]['seen'] = now
    return token, identity, session


def logout(request):
    with _LOCK:
        _SESSIONS.pop(hashlib.sha256(request.cookies.get(COOKIE, '').encode()).hexdigest(), None)
