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
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from typing import Literal
from psycopg.types.json import Jsonb

from memory_service_runtime.governed import db, evidence, protocol, readers, service, workbench
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
    contract_version: Literal["tkos.method/0.1"] | None = None
    stage: str | None = Field(default=None, min_length=1, max_length=200)
    purpose: str | None = Field(default=None, min_length=1, max_length=500)
    include_drafts: StrictBool = False

    @model_validator(mode="after")
    def method_context(self):
        if self.contract_version and (not self.stage or not self.purpose):
            raise ValueError("Method context requires stage and purpose")
        if not self.contract_version and (self.stage or self.purpose or self.include_drafts):
            raise ValueError("Method context options require explicit protocol")
        return self

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


@router.get("/method-profiles")
def method_profiles_get(request: Request, response: Response, token: Annotated[str, Depends(bearer)]):
    """Read-only registration view: profiles, creation policies and the support
    registry installed for the caller's current scope (A1)."""
    workbench.strict_query(request.query_params, set())
    with db.transaction(token) as (conn, ctx):
        if not ctx.assignments:
            raise GovernedError("FORBIDDEN", "No current read assignment", status=403)
        profiles = conn.execute(
            """SELECT profile_id, revision, schema_version, canonical_hash, record_origin,
                      experimental, installed_by, install_reason, recorded_at
               FROM gov_method_profile_revisions WHERE scope_id=%s
               ORDER BY profile_id, revision""", (ctx.scope_id,),
        ).fetchall()
        policies = conn.execute(
            """SELECT domain_id, policy_seq, content, recorded_by, reason, recorded_at
               FROM gov_protocol_policies WHERE scope_id=%s
               ORDER BY domain_id NULLS FIRST, policy_seq""", (ctx.scope_id,),
        ).fetchall()
        registries = conn.execute(
            """SELECT protocol_id, contract_version, registry_seq, content, recorded_by, recorded_at
               FROM gov_protocol_support_registry WHERE scope_id=%s
               ORDER BY protocol_id, registry_seq""", (ctx.scope_id,),
        ).fetchall()
        result = db.jsonable({"scope_id": ctx.scope_id, "profiles": profiles,
                              "policies": policies, "support_registry": registries})
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/context-packs")
def context_create(body: ContextRequest, token: Annotated[str, Depends(bearer)]):
    with db.transaction(token) as (conn, ctx):
        if body.contract_version:
            from . import method_readers
            return method_readers.context_pack(conn, ctx, [str(oid) for oid in body.object_ids], body.valid_at, body.known_at,
                                               body.stage, body.purpose, body.include_drafts)
        return readers.context_pack(conn, ctx, [str(oid) for oid in body.object_ids], body.valid_at, body.known_at)


Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=workbench.MAX_CURSOR_LENGTH)]


@router.get("/object-types")
def object_types_list(request: Request, response: Response, token: Annotated[str, Depends(bearer)],
                      contract_version: Literal["tkos.method/0.1"] | None = None):
    workbench.strict_query(request.query_params, {"contract_version"})
    with db.transaction(token) as (conn, ctx):
        result = workbench.object_types(conn, ctx, contract_version) if contract_version else workbench.object_types(conn, ctx)
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
        # Protocol/namespace registration resolves BEFORE any object-store
        # write: a rejected upload must leave S3 untouched and no DB rows.
        creation = protocol.evidence_protocol_fields(conn, ctx.scope_id, str(body.domain_id))
        payload = evidence.store_bytes(ctx, str(body.domain_id), body.title, content, body.media_type)
        db.authorize_domain(conn, ctx, str(body.domain_id), "upload_evidence")
        oid, rid, receipt_id = (str(uuid.uuid4()) for _ in range(3))
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        conn.execute("INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status) VALUES(%s,%s,%s,'EvidenceAsset','stored')",
                     (oid, ctx.scope_id, str(body.domain_id)))
        protocol.insert_binding(conn, ctx.scope_id, oid, creation,
                                registered_by=ctx.principal_id, receipt_id=receipt_id)
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
        from . import method_access
        method = method_access.is_method_object(conn, ctx, str(object_id))
        obj = method_access.head(conn, ctx, str(object_id)) if method else db.object_row(conn, ctx, str(object_id))
        if obj["object_type"] != "EvidenceAsset":
            raise GovernedError("NOT_FOUND", "Evidence was not found", status=404)
        revision = (method_access.revision(conn, ctx, str(object_id), str(revision_id)) if method
                    else db.revision_row(conn, ctx, str(object_id), str(revision_id)))
        # Read-support re-check before touching the object store: unregistered
        # or read-unsupported bindings never reach S3.
        protocol.require_read_support(conn, ctx.scope_id, str(object_id))
        payload = revision["payload"]
        content = evidence.fetch_payload(payload, scope_id=ctx.scope_id, domain_id=obj["domain_id"])
        if method:
            method_access.revision(conn, ctx, str(object_id), str(revision_id))
        else:
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
        if request.url.path.startswith(("/v1/actions", "/v1/objects", "/v1/action-receipts", "/v1/evidence-assets", "/v1/context-packs", "/v1/object-types", "/v1/domains", "/v1/method-profiles", "/v1/context-graph/narrative")):
            return JSONResponse(status_code=422, content={"error": {"code": "INVALID_REQUEST", "message": "Request does not match the governed API schema"}})
        from fastapi.exception_handlers import request_validation_exception_handler
        return await request_validation_exception_handler(request, exc)


@router.get("/method/{collection}")
def method_list(collection: str, request: Request, response: Response,
                token: Annotated[str, Depends(bearer)], domain_id: uuid.UUID | None = None,
                after: uuid.UUID | None = None, limit: Limit = 50):
    from . import method_readers
    kinds = {"review-windows": "ReviewWindow", "candidate-sets": "CandidateSet", "business-facts": "BusinessFact",
             "period-reviews": "PeriodReview", "strategies": "Strategy", "strategic-issues": "StrategicIssue", "runs": "MethodRun"}
    if collection not in kinds:
        raise GovernedError("NOT_FOUND")
    workbench.strict_query(request.query_params, {"domain_id", "after", "limit"})
    with db.transaction(token) as (conn, ctx):
        result = method_readers.list_typed(conn, ctx, kinds[collection], str(domain_id) if domain_id else None,
                                          str(after) if after else None, limit)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/method/objects/{object_id}/reviews")
def method_reviews(object_id: uuid.UUID, request: Request, response: Response,
                   token: Annotated[str, Depends(bearer)], effective_only: bool = False):
    from . import method_readers
    workbench.strict_query(request.query_params, {"effective_only"})
    with db.transaction(token) as (conn, ctx):
        result = method_readers.review_records(conn, ctx, str(object_id), effective_only=effective_only)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/method/objects/{object_id}/recovery")
def method_recovery(object_id: uuid.UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    from . import method_readers
    with db.transaction(token) as (conn, ctx):
        result = method_readers.recovery(conn, ctx, str(object_id))
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/method/missions/{object_id}/handoff")
def method_mission_handoff(object_id: uuid.UUID, response: Response, token: Annotated[str, Depends(bearer)]):
    from . import method_readers
    with db.transaction(token) as (conn, ctx):
        result = method_readers.handoff(conn, ctx, str(object_id))
    response.headers["Cache-Control"] = "no-store"
    return result
