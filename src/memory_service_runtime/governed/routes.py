"""Product-neutral HTTP boundary for the governed business runtime."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb

from memory_service_runtime.governed import db, evidence, readers, service, workbench
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.models import ActionRequest


router = APIRouter(prefix="/v1", tags=["governed-runtime"])


def bearer(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise GovernedError("UNAUTHENTICATED", "A valid bearer identity is required", status=401)
    return authorization[7:].strip()


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    valid_at: AwareDatetime
    known_at: AwareDatetime

    @field_validator("valid_at", "known_at", mode="before")
    @classmethod
    def iso_string(cls, value):
        if not isinstance(value, str):
            raise ValueError("time must be an ISO8601 string with timezone")
        return value

    @field_validator("object_ids")
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("object_ids must be distinct")
        return value


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    content_base64: str = Field(min_length=1, max_length=2_796_204)
    media_type: str = Field(default="application/octet-stream", min_length=3, max_length=120,
                           pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


@router.post("/actions")
def action(body: ActionRequest, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return db.jsonable(service.execute_action(conn, ctx, body))


@router.post("/actions/prepare")
def prepare(body: ActionRequest, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return db.jsonable(service.prepare_action(conn, ctx, body))


@router.get("/objects/{object_id}")
def object_get(object_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return readers.object_state(conn, ctx, str(object_id))


@router.get("/objects/{object_id}/revisions/{revision_id}")
def revision_get(object_id: uuid.UUID, revision_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return readers.revision(conn, ctx, str(object_id), str(revision_id))


@router.get("/action-receipts/{receipt_id}")
def receipt_get(receipt_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return readers.action_receipt(conn, ctx, str(receipt_id))


@router.post("/context-packs")
def context_create(body: ContextRequest, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return readers.context_pack(conn, ctx, [str(oid) for oid in body.object_ids], body.valid_at, body.known_at)


Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)]


@router.get("/object-types")
def object_types_list(request: Request, response: Response, token: Annotated[str, Depends(bearer)]):
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        result = workbench.object_types(conn, ctx)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/domains")
def domain_list(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                limit: Limit = 50, cursor: Cursor = None):
    workbench.strict_query(request.query_params, {"limit", "cursor"})
    with db.transaction(token) as (conn, ctx):
        result = workbench.domains(conn, ctx, limit, cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects")
def object_list(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                domain_id: Annotated[uuid.UUID, Query()],
                object_type: Annotated[str | None, Query()] = None,
                limit: Limit = 50, cursor: Cursor = None):
    workbench.strict_query(request.query_params, {"domain_id", "object_type", "limit", "cursor"})
    with db.transaction(token) as (conn, ctx):
        result = workbench.objects(conn, ctx, str(domain_id), object_type, limit, cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/revisions")
def revision_list(request: Request, response: Response, object_id: uuid.UUID,
                  token: Annotated[str, Depends(bearer)], limit: Limit = 50, cursor: Cursor = None):
    workbench.strict_query(request.query_params, {"limit", "cursor"})
    with db.transaction(token) as (conn, ctx):
        result = workbench.revisions(conn, ctx, str(object_id), limit, cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/relations")
def relation_list(request: Request, response: Response, object_id: uuid.UUID,
                  token: Annotated[str, Depends(bearer)],
                  revision_id: Annotated[uuid.UUID | None, Query()] = None,
                  limit: Limit = 50, cursor: Cursor = None):
    workbench.strict_query(request.query_params, {"revision_id", "limit", "cursor"})
    with db.transaction(token) as (conn, ctx):
        result = workbench.relations(conn, ctx, str(object_id),
                                     str(revision_id) if revision_id else None, limit, cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/action-receipts")
def action_receipt_list(request: Request, response: Response, object_id: uuid.UUID,
                        token: Annotated[str, Depends(bearer)], limit: Limit = 50, cursor: Cursor = None):
    workbench.strict_query(request.query_params, {"limit", "cursor"})
    with db.transaction(token) as (conn, ctx):
        result = workbench.action_receipts(conn, ctx, str(object_id), limit, cursor)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/objects/{object_id}/responsibility")
def responsibility_get(request: Request, response: Response, object_id: uuid.UUID,
                       token: Annotated[str, Depends(bearer)]):
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        result = workbench.responsibility(conn, ctx, str(object_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/context-packs/{snapshot_id}")
def snapshot_get(snapshot_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        return readers.context_snapshot(conn, ctx, str(snapshot_id))


@router.post("/evidence-assets")
def evidence_create(body: EvidenceRequest, token: Annotated[str, Depends(bearer)]):
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GovernedError("INVALID_REQUEST", "Evidence content is not valid base64", status=422) from exc
    with db.transaction(token) as (conn, ctx):
        db.authorize_domain(conn, ctx, str(body.domain_id), "upload_evidence")
        payload = evidence.store_bytes(ctx, str(body.domain_id), body.title, content, body.media_type)
        db.authorize_domain(conn, ctx, str(body.domain_id), "upload_evidence")
        oid, rid, receipt_id = (str(uuid.uuid4()) for _ in range(3))
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) VALUES(%s,%s,%s,'EvidenceAsset','stored')",
                     (oid, ctx.scope_id, str(body.domain_id)))
        revision = conn.execute(
            "INSERT INTO gov_object_revisions(revision_id,scope_id,object_id,object_version,payload,payload_hash,recorded_by,action_id) VALUES(%s,%s,%s,1,%s,%s,%s,%s) RETURNING recorded_at",
            (rid, ctx.scope_id, oid, Jsonb(payload), digest, ctx.principal_id, receipt_id),
        ).fetchone()
        conn.execute("UPDATE gov_objects SET latest_revision_id=%s,effective_revision_id=%s WHERE scope_id=%s AND object_id=%s",
                     (rid, rid, ctx.scope_id, oid))
        conn.execute(
            "INSERT INTO gov_lifecycle_events(scope_id,object_id,event_type,to_status,action_id,principal_id,detail) VALUES(%s,%s,'upload_evidence','stored',%s,%s,%s)",
            (ctx.scope_id, oid, receipt_id, ctx.principal_id,
             Jsonb({"before_version": 0, "object_version": 1, "effective_revision_id": rid, "revision_id": rid})),
        )
        result = {"object_id": oid, "revision_id": rid, "payload_hash": digest, "sha256": payload["sha256"],
                  "length": payload["length"], "version_id": payload["version_id"], "referenced_object_ids": [oid]}
        conn.execute(
            "INSERT INTO gov_action_receipts(receipt_id,scope_id,principal_id,idempotency_key,request_hash,action_type,auth_epoch,result,object_versions,target_object_id) VALUES(%s,%s,%s,%s,%s,'upload_evidence',%s,%s,%s,%s)",
            (receipt_id, ctx.scope_id, ctx.principal_id, f"evidence-upload-{uuid.uuid4()}", digest, ctx.auth_epoch,
             Jsonb(result), Jsonb([{"object_id": oid, "object_version": 1}]), oid),
        )
        return {**result, "receipt_id": receipt_id, "recorded_at": db.jsonable(revision["recorded_at"])}


@router.get("/evidence-assets/{object_id}/revisions/{revision_id}")
def evidence_download(object_id: uuid.UUID, revision_id: uuid.UUID, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        obj = db.object_row(conn, ctx, str(object_id))
        if obj["object_type"] != "EvidenceAsset":
            raise GovernedError("NOT_FOUND", "Evidence was not found", status=404)
        revision = db.revision_row(conn, ctx, str(object_id), str(revision_id))
        payload = revision["payload"]
        content = evidence.fetch_payload(payload, scope_id=ctx.scope_id, domain_id=obj["domain_id"])
        db.authorize_domain(conn, ctx, obj["domain_id"], "read")
        return Response(content, media_type=payload["media_type"],
                        headers={"ETag": f'"{payload["sha256"]}"',
                                 "Content-Disposition": f'attachment; filename="{object_id}"',
                                 "Cache-Control": "no-store"})


def install_errors(app):
    @app.exception_handler(GovernedError)
    async def governed_error(_request: Request, exc: GovernedError):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}})

    # Keep compatibility routes' existing validation format unchanged.
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith(("/v1/actions", "/v1/objects", "/v1/action-receipts", "/v1/evidence-assets", "/v1/context-packs", "/v1/object-types", "/v1/domains", "/v1/context-graph/narrative")):
            return JSONResponse(status_code=422, content={"error": {"code": "INVALID_REQUEST", "message": "Request does not match the governed API schema"}})
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)
