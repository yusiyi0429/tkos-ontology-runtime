"""Runtime-only scene API; browser sessions and Agent orchestration belong to Clark."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from . import db, workbench, workspace_readers, workspace_service
from .routes import bearer
from .workspace_models import IdentityView, SceneList, SceneReceipt, SceneView, WorkspaceCommand, WorkspaceView

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


@router.post("/workspace-scenes/events", response_model=SceneReceipt)
def write(command: WorkspaceCommand, response: Response, token: Token):
    with db.transaction(token) as (conn, ctx):
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


@router.get("/workspaces/{view}", response_model=WorkspaceView)
def workspace(view: Literal["ceo", "dri"], request: Request, response: Response, token: Token,
              collection: str = "review-windows", after: UUID | None = None, limit: Limit = 25):
    workbench.strict_query(request.query_params, {"collection", "after", "limit"})
    with db.transaction(token) as (conn, ctx):
        result = workspace_readers.workspace(conn, ctx, view, collection, str(after) if after else None, limit)
    response.headers["Cache-Control"] = "no-store"
    return result
