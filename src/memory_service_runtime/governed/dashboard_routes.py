"""GET-only HTTP boundary for the business dashboard read contract.

``tkos.dashboard/0.1`` adds no business action, no Context snapshot and no
write route.  The three dashboard reads plus the exact-version / receipt /
evidence reads the page needs are exposed under ``/v1/dashboard``; they reuse
the same readers as the existing ``/v1`` API and the same current-authority
transaction fence.  The local facade in ``memory_service_app.dashboard`` calls
the ``read_*`` functions below with the server-configured viewer identity, so
the browser never receives or stores a credential.
"""
from __future__ import annotations

from datetime import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AwareDatetime

from . import dashboard, db, method_map, method_readers, readers, workbench, workspace_readers
from .errors import GovernedError
from .routes import bearer
from .workbench import strict_query


router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])

Limit = Annotated[int, Query(ge=1, le=dashboard.MAX_LIMIT)]


def read_overview(token: str, *, strategy_id: str | None = None,
                  environment: dict | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        identity = workspace_readers.identity(conn, ctx)
        return dashboard.overview(conn, ctx, strategy_id=strategy_id,
                                  environment=environment, identity=identity)


def read_objects(token: str, *, group: str, strategy_id: str | None = None,
                 object_type: str | None = None, basis: str = "current",
                 domain_id: str | None = None, period_from: str | None = None,
                 period_to: str | None = None, owner_id: str | None = None,
                 scope_id: str | None = None, limit: int = dashboard.DEFAULT_LIMIT,
                 cursor: str | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        return dashboard.objects(conn, ctx, group=group, strategy_id=strategy_id,
                                 object_type=object_type, basis=basis, domain_id=domain_id,
                                 period_from=period_from, period_to=period_to,
                                 owner_id=owner_id, scope_id=scope_id, limit=limit,
                                 cursor=cursor)


def read_detail(token: str, object_id: str, *, revision_id: str | None = None,
                strategy_id: str | None = None, environment: dict | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        identity = workspace_readers.identity(conn, ctx)
        return dashboard.detail(conn, ctx, object_id, revision_id=revision_id,
                                strategy_id=strategy_id, environment=environment,
                                identity=identity)


def read_revisions(token: str, object_id: str, *, limit: int = 50,
                   cursor: str | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        return workbench.revisions(conn, ctx, object_id, limit, cursor)


def read_revision(token: str, object_id: str, revision_id: str) -> dict:
    with db.transaction(token) as (conn, ctx):
        return readers.revision(conn, ctx, object_id, revision_id)


def read_receipt(token: str, receipt_id: str) -> dict:
    with db.transaction(token) as (conn, ctx):
        return readers.action_receipt(conn, ctx, receipt_id)


def read_reviews(token: str, object_id: str, *, effective_only: bool = False) -> dict:
    with db.transaction(token) as (conn, ctx):
        return method_readers.review_records(conn, ctx, object_id, effective_only=effective_only)


def read_object_receipts(token: str, object_id: str, *, limit: int = 25,
                         cursor: str | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        return workbench.action_receipts(conn, ctx, object_id, limit, cursor)


def read_downstream(token: str, object_id: str, revision_id: str, *, limit: int = dashboard.DOWNSTREAM_LIMIT,
                    cursor: str | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        selected = dashboard.authorized_ref(conn, ctx, object_id, revision_id)
        return dashboard.downstream(conn, ctx, selected, limit=limit, cursor=cursor)


def read_evidence(token: str, object_id: str, revision_id: str) -> Response:
    from . import routes
    return routes.evidence_download(uuid.UUID(object_id), uuid.UUID(revision_id), token)


def read_ontology_catalog(token: str) -> dict:
    with db.transaction(token) as (conn, ctx):
        return dashboard.ontology_catalog(conn, ctx)


def read_method_map(token: str, *, availability: str = "none") -> dict:
    """Read-only Method→Runtime map; it adds no action and no business write."""
    with db.transaction(token) as (conn, ctx):
        if not ctx.assignments:
            raise GovernedError("FORBIDDEN", "No current read assignment", status=403)
        return method_map.build(conn, ctx, availability=availability)


def read_catalog_objects(token: str, *, object_type: str, domain_id: str | None = None,
                         limit: int = dashboard.DEFAULT_LIMIT, cursor: str | None = None) -> dict:
    with db.transaction(token) as (conn, ctx):
        return dashboard.catalog_objects(conn, ctx, object_type=object_type,
                                         domain_id=domain_id, limit=limit, cursor=cursor)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.get("/overview")
def overview_route(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                   strategy_id: Annotated[uuid.UUID | None, Query()] = None):
    strict_query(request.query_params, {"strategy_id"})
    result = read_overview(token, strategy_id=str(strategy_id) if strategy_id else None)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects")
def objects_route(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                  group: Annotated[str, Query(min_length=1, max_length=32)],
                  strategy_id: Annotated[uuid.UUID | None, Query()] = None,
                  object_type: Annotated[str | None, Query(max_length=64)] = None,
                  basis: Annotated[str, Query(min_length=1, max_length=32)] = "current",
                  domain_id: Annotated[uuid.UUID | None, Query()] = None,
                  period_from: Annotated[AwareDatetime | None, Query()] = None,
                  period_to: Annotated[AwareDatetime | None, Query()] = None,
                  owner_id: Annotated[uuid.UUID | None, Query()] = None,
                  scope_id: Annotated[str | None, Query(max_length=200)] = None,
                  limit: Limit = dashboard.DEFAULT_LIMIT,
                  cursor: Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)] = None):
    strict_query(request.query_params, {"group", "strategy_id", "object_type", "basis", "domain_id",
                                        "period_from", "period_to", "owner_id", "scope_id",
                                        "limit", "cursor"})
    result = read_objects(token, group=group,
                          strategy_id=str(strategy_id) if strategy_id else None,
                          object_type=object_type, basis=basis,
                          domain_id=str(domain_id) if domain_id else None,
                          period_from=_iso(period_from), period_to=_iso(period_to),
                          owner_id=str(owner_id) if owner_id else None,
                          scope_id=scope_id, limit=limit, cursor=cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}")
def object_route(request: Request, response: Response, object_id: uuid.UUID,
                 token: Annotated[str, Depends(bearer)],
                 revision_id: Annotated[uuid.UUID | None, Query()] = None,
                 strategy_id: Annotated[uuid.UUID | None, Query()] = None):
    strict_query(request.query_params, {"revision_id", "strategy_id"})
    result = read_detail(token, str(object_id),
                         revision_id=str(revision_id) if revision_id else None,
                         strategy_id=str(strategy_id) if strategy_id else None)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/revisions")
def revisions_route(request: Request, response: Response, object_id: uuid.UUID,
                    token: Annotated[str, Depends(bearer)], limit: Limit = 50,
                    cursor: Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)] = None):
    strict_query(request.query_params, {"limit", "cursor"})
    result = read_revisions(token, str(object_id), limit=limit, cursor=cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/revisions/{revision_id}")
def revision_route(request: Request, response: Response, object_id: uuid.UUID,
                   revision_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    strict_query(request.query_params, set())
    result = read_revision(token, str(object_id), str(revision_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/reviews")
def reviews_route(request: Request, response: Response, object_id: uuid.UUID,
                  token: Annotated[str, Depends(bearer)],
                  effective_only: Annotated[bool, Query()] = False):
    strict_query(request.query_params, {"effective_only"})
    result = read_reviews(token, str(object_id), effective_only=effective_only)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/receipts")
def receipt_list_route(request: Request, response: Response, object_id: uuid.UUID,
                       token: Annotated[str, Depends(bearer)], limit: Limit = 25,
                       cursor: Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)] = None):
    strict_query(request.query_params, {"limit", "cursor"})
    result = read_object_receipts(token, str(object_id), limit=limit, cursor=cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/downstream")
def downstream_route(request: Request, response: Response, object_id: uuid.UUID,
                     token: Annotated[str, Depends(bearer)],
                     revision_id: Annotated[uuid.UUID, Query()],
                     limit: Annotated[int, Query(ge=1, le=dashboard.DOWNSTREAM_MAX_LIMIT)] = dashboard.DOWNSTREAM_LIMIT,
                     cursor: Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)] = None):
    strict_query(request.query_params, {"revision_id", "limit", "cursor"})
    result = read_downstream(token, str(object_id), str(revision_id), limit=limit, cursor=cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/ontology/method-map")
def ontology_method_map_route(request: Request, response: Response,
                              token: Annotated[str, Depends(bearer)],
                              availability: Annotated[str, Query(max_length=16)] = "none"):
    strict_query(request.query_params, {"availability"})
    result = read_method_map(token, availability=availability)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/ontology/catalog")
def ontology_catalog_route(request: Request, response: Response,
                           token: Annotated[str, Depends(bearer)]):
    strict_query(request.query_params, set())
    result = read_ontology_catalog(token)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/catalog/objects")
def catalog_objects_route(request: Request, response: Response,
                          token: Annotated[str, Depends(bearer)],
                          object_type: Annotated[str, Query(min_length=1, max_length=64)],
                          domain_id: Annotated[uuid.UUID | None, Query()] = None,
                          limit: Limit = dashboard.DEFAULT_LIMIT,
                          cursor: Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)] = None):
    strict_query(request.query_params, {"object_type", "domain_id", "limit", "cursor"})
    result = read_catalog_objects(token, object_type=object_type,
                                  domain_id=str(domain_id) if domain_id else None,
                                  limit=limit, cursor=cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/action-receipts/{receipt_id}")
def receipt_route(request: Request, response: Response, receipt_id: uuid.UUID,
                  token: Annotated[str, Depends(bearer)]):
    strict_query(request.query_params, set())
    result = read_receipt(token, str(receipt_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/evidence-assets/{object_id}/revisions/{revision_id}")
def evidence_route(request: Request, response: Response, object_id: uuid.UUID,
                   revision_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    strict_query(request.query_params, set())
    return read_evidence(token, str(object_id), str(revision_id))


__all__ = ["router", "read_overview", "read_objects", "read_detail", "read_revisions",
           "read_revision", "read_receipt", "read_reviews", "read_evidence", "read_downstream",
           "read_object_receipts", "read_ontology_catalog", "read_method_map",
           "read_catalog_objects"]
