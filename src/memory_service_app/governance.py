"""Allowlisted local human-session facade; no Agent or arbitrary proxy capability."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from memory_service_runtime.governed import governance as reads, workspace_readers, readers, method_readers, dashboard, method_access
from memory_service_runtime.governed.errors import GovernedError
from . import governance_sessions as sessions, governance_commands as commands
from .settings import get_settings

router = APIRouter(prefix='/dashboard/api/v1', tags=['governance-session'])


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=16, max_length=256)


def guard(request: Request):
    settings = get_settings()
    if not settings.tkos_governance_workbench_enabled:
        raise GovernedError('NOT_FOUND')
    from .dashboard import _host_allowed
    if not _host_allowed(request):
        raise GovernedError('FORBIDDEN')
    origin = request.headers.get('origin')
    if request.method != 'GET' and origin != str(request.base_url).rstrip('/'):
        raise GovernedError('FORBIDDEN')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise GovernedError('FORBIDDEN')
    if request.headers.get('sec-fetch-site', 'same-origin') not in {'same-origin', 'none'}:
        raise GovernedError('FORBIDDEN')


def current(request: Request):
    guard(request)
    return sessions.authenticate(request, write=request.method != 'GET')


@router.post('/session')
def login(body: Login, request: Request):
    guard(request)
    sid, session, identity = sessions.login(body.username, body.code, request.client.host if request.client else 'local')
    sessions.logout(request)
    response = JSONResponse({'identity': identity, 'csrf': session['csrf']}, headers={'Cache-Control': 'no-store'})
    response.set_cookie(sessions.COOKIE, sid, max_age=8 * 3600, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https', path='/dashboard/')
    return response


@router.get('/session')
def session(request: Request):
    token, identity, session = current(request)
    return {'identity': identity, 'csrf': session['csrf']}


@router.delete('/session')
def logout(request: Request):
    current(request)
    sessions.logout(request)
    response = JSONResponse({'signed_out': True})
    response.delete_cookie(sessions.COOKIE, path='/dashboard/')
    return response


@router.get('/governance/tasks')
def tasks(request: Request, after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 25):
    token, _, _ = current(request)
    return reads.read(token, reads.tasks, str(after) if after else None, limit)


@router.get('/governance/review-windows/{object_id}')
def window(object_id: UUID, request: Request):
    token, _, _ = current(request)
    return reads.read(token, reads.window, str(object_id))


@router.get('/governance/bases')
def bases(request: Request):
    token, _, _ = current(request)
    return reads.read(token, reads.bases)


@router.get('/governance/method-tasks', include_in_schema=False)
@router.get('/governance/method/tasks')
def method_tasks(request: Request, after: UUID | None = None,
                 limit: Annotated[int, Query(ge=1, le=100)] = 25):
    token, _, _ = current(request)
    return reads.read(token, reads.method_tasks, str(after) if after else None, limit)


@router.get('/governance/objects/{object_id}/actions')
def object_actions(object_id: UUID, request: Request):
    token, _, _ = current(request)
    return reads.read(token, reads.actions, str(object_id))


@router.get('/governance/sources')
def sources(request: Request, scene_type: Annotated[str | None, Query(max_length=40)] = None,
            after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 25):
    token, _, _ = current(request)
    return reads.read(token, reads.source_scenes, scene_type,
                      str(after) if after else None, limit)


@router.get('/governance/sources/contexts/{context_id}')
def source_context(context_id: UUID, request: Request):
    token, _, _ = current(request)
    return reads.read(token, reads.source_context, str(context_id))


@router.get('/governance/sources/{scene_id}')
def source_scene(scene_id: UUID, request: Request):
    token, _, _ = current(request)
    return reads.read(token, reads.source_scene, str(scene_id))


@router.get('/governance/missions')
def missions(request: Request, after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 25):
    token, identity, _ = current(request)
    view = 'ceo' if any(a['role'] == 'CEO' for a in identity['assignments']) else 'dri'
    def projection(conn, ctx):
        result = workspace_readers.workspace(conn, ctx, view, 'missions', str(after) if after else None, limit)
        for item in result['items']:
            head = method_access.head(conn, ctx, item['object_id'])
            item['responsibilities'] = dashboard._responsibility_entries(conn, ctx, head, item['effective_revision']['payload'])
        return result
    return reads.read(token, projection)


@router.get('/governance/context-packs/{snapshot_id}')
def context(snapshot_id: UUID, request: Request):
    token, _, _ = current(request)
    return reads.read(token, readers.context_snapshot, str(snapshot_id))


@router.post('/commands/prepare')
def prepare(body: dict, request: Request):
    token, identity, _ = current(request)
    return commands.prepare(token, identity, body)


@router.get('/commands')
def list_commands(request: Request):
    token, identity, _ = current(request)
    return commands.listing(identity, token)


@router.get('/commands/{command_id}')
def get_command(command_id: UUID, request: Request):
    token, identity, _ = current(request)
    return commands.get(str(command_id), identity, token)


@router.post('/commands/{command_id}/commit')
def commit(command_id: UUID, request: Request):
    token, identity, _ = current(request)
    return commands.commit(str(command_id), identity, token)


@router.post('/commands/{command_id}/retry')
def retry(command_id: UUID, request: Request):
    token, identity, _ = current(request)
    return commands.commit(str(command_id), identity, token, retry=True)
