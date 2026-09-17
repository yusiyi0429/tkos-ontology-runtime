"""Runtime-only scene API; browser sessions and Agent orchestration belong to Clark.

``POST /v1/workspace-scenes/events`` dispatches the explicit contract version:
``tkos.workspace/0.1`` keeps its required Method anchor and authorization
unchanged, while ``tkos.workspace/0.2`` creates standalone source scenes with
scene-scoped privacy. Reads for 0.2 live under ``/v1/workspace-sources``.
"""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from . import db, workbench, workspace_readers, workspace_service
from . import workspace_v02_readers, workspace_v02_service
from .routes import bearer
from .workspace_models import IdentityView, SceneList, SceneReceipt, SceneView, WorkspaceView
from .workspace_v02_models import (SourceContextCreate, SourceContextView, SourceSceneList,
                                   SourceSceneReceipt, SourceSceneView, WorkspaceCommandEnvelope)

router = APIRouter(prefix="/v1", tags=["workspace-scenes"])
Token = Annotated[str, Depends(bearer)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/identity", response_model=IdentityView)
def identity(request: Request, response: Response, token: Token):
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        result = workspace_readers.identity(conn, ctx)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/workspace-scenes/events", response_model=SceneReceipt | SourceSceneReceipt)
def write(command: WorkspaceCommandEnvelope, response: Response, token: Token):
    with db.transaction(token) as (conn, ctx):
        if command.contract_version == "tkos.workspace/0.2":
            result = workspace_v02_service.execute(conn, ctx, command)
        else:
            result = workspace_service.execute(conn, ctx, command)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/workspace-scenes", response_model=SceneList)
def list_scenes(request: Request, response: Response, token: Token,
                scene_type: Literal["monthly", "weekly", "meeting"] | None = None,
                after: UUID | None = None, limit: Limit = 25):
    workbench.strict_query(request.query_params, {"scene_type", "after", "limit"})
    with db.transaction(token) as (conn, ctx):
        result = workspace_readers.list_scenes(conn, ctx, scene_type, str(after) if after else None, limit)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/workspace-scenes/{scene_id}", response_model=SceneView)
def read_scene(scene_id: UUID, request: Request, response: Response, token: Token):
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        result = workspace_readers.read(conn, ctx, str(scene_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/workspace-sources/contexts", response_model=SourceContextView)
def create_source_context(body: SourceContextCreate, response: Response, token: Token):
    with db.transaction(token) as (conn, ctx):
        result = workspace_v02_service.create_context(conn, ctx, body)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/workspace-sources/contexts/{context_id}", response_model=SourceContextView)
def read_source_context(context_id: UUID, request: Request, response: Response, token: Token):
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        result = workspace_v02_readers.read_context(conn, ctx, str(context_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/workspace-sources", response_model=SourceSceneList)
def list_source_scenes(request: Request, response: Response, token: Token,
                       scene_type: Literal["meeting", "document", "selected_conversation"] | None = None,
                       after: UUID | None = None, limit: Limit = 25):
    workbench.strict_query(request.query_params, {"scene_type", "after", "limit"})
    with db.transaction(token) as (conn, ctx):
        result = workspace_v02_readers.list_scenes(conn, ctx, scene_type, str(after) if after else None, limit)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/workspace-sources/{scene_id}", response_model=SourceSceneView)
def read_source_scene(scene_id: UUID, request: Request, response: Response, token: Token):
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        result = workspace_v02_readers.read(conn, ctx, str(scene_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/workspaces/{view}", response_model=WorkspaceView)
def workspace(view: Literal["ceo", "dri"], request: Request, response: Response, token: Token,
              collection: str = "review-windows", after: UUID | None = None, limit: Limit = 25):
    workbench.strict_query(request.query_params, {"collection", "after", "limit"})
    with db.transaction(token) as (conn, ctx):
        result = workspace_readers.workspace(conn, ctx, view, collection, str(after) if after else None, limit)
    response.headers["Cache-Control"] = "no-store"
    return result
