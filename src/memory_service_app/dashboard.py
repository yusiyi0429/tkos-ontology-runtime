"""Local read-only business dashboard facade.

Serves the compiled React application under ``/dashboard/`` and an explicit
GET-only read API under ``/dashboard/api/v1/...``.  The facade does not forward
arbitrary URLs and exposes no Action, Context or business-write route: every
handler calls one of the named readers in
``memory_service_runtime.governed.dashboard_routes`` with the server-configured
viewer identity from a private token file.  The browser therefore never
receives, stores or sends a credential.

The facade is disabled unless ``TKOS_DASHBOARD_ENABLED`` is explicitly set.
Host/Origin/Sec-Fetch checks keep the loopback page on the loopback origin, and
all responses carry the static security headers and ``Cache-Control: no-store``
for API payloads.  The viewer token file must not be writable or readable by
group/other (mode ``0600`` for a bind mount; Docker secrets must be re-created
with owner-only permissions for the runtime user).
"""
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import stat
import uuid
from typing import Annotated

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime

from memory_service_runtime.governed import dashboard_routes as reads
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.workbench import MAX_CURSOR_LENGTH, strict_query

from .settings import get_settings

DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent / "dashboard_dist"

api = APIRouter(prefix="/dashboard/api/v1", tags=["dashboard-facade"])

_API_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}

_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'; object-src 'none'")


def _json_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}},
                        headers=_API_HEADERS)


def _split(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _enabled_from_env() -> bool:
    """Read the feature flag without forcing the full process settings at import."""
    return os.environ.get("TKOS_DASHBOARD_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _environment() -> dict:
    settings = get_settings()
    return {"label": settings.tkos_dashboard_env_label,
            "synthetic": settings.tkos_dashboard_synthetic,
            "source": "server_configuration"}


def _viewer_token() -> str | None:
    settings = get_settings()
    path = settings.tkos_dashboard_viewer_token_file
    if not path:
        return None
    try:
        info = os.stat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            return None
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token if 32 <= len(token) <= 1024 and token == token.strip() else None


def _host_allowed(request: Request) -> bool:
    allowed = _split(get_settings().tkos_dashboard_allowed_hosts)
    host = request.headers.get("host", "").strip()
    if not allowed or not host:
        return False
    if host.lower() in allowed:
        return True
    # A bare ``127.0.0.1`` entry also accepts the request's ephemeral port.
    bare = host.lower()
    if bare.startswith("["):
        bare = bare.split("]")[0] + "]"
    else:
        bare = bare.rsplit(":", 1)[0]
    return bare in allowed


def _guard(request: Request) -> JSONResponse | None:
    """Feature flag plus Origin/Sec-Fetch checks for the dashboard API."""
    settings = get_settings()
    if not settings.tkos_dashboard_enabled:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND",
                            "message": "The requested record is unavailable."}},
                            headers=_API_HEADERS)
    origin = request.headers.get("origin")
    if origin and origin not in _split(settings.tkos_dashboard_allowed_origins):
        return _json_error(403, "FORBIDDEN", "Dashboard reads require the loopback dashboard origin.")
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return _json_error(403, "FORBIDDEN", "Dashboard reads require the loopback dashboard origin.")
    return None


def _token_or_error() -> tuple[str | None, JSONResponse | None]:
    token = _viewer_token()
    if token is None:
        # A missing/invalid viewer never falls back to an elevated identity.
        return None, _json_error(503, "DASHBOARD_VIEWER_UNAVAILABLE",
                                 "The dashboard viewer identity is not configured.")
    return token, None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _validate(request: Request, allowed: set[str]) -> JSONResponse | None:
    try:
        strict_query(request.query_params, allowed)
        return None
    except GovernedError as exc:
        return _json_error(exc.status, exc.code, exc.message)


def _read(reader, *args, **kwargs):
    """Run a named reader and keep the dashboard JSON error shape/no-store."""
    try:
        return reader(*args, **kwargs)
    except GovernedError as exc:
        return _json_error(exc.status, exc.code, exc.message)


@api.get("/overview")
def overview(request: Request, strategy_id: Annotated[uuid.UUID | None, Query()] = None):
    invalid = _validate(request, {"strategy_id"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_overview, token, strategy_id=str(strategy_id) if strategy_id else None,
                               environment=_environment())


@api.get("/objects")
def objects(request: Request, group: Annotated[str, Query(min_length=1, max_length=32)],
            strategy_id: Annotated[uuid.UUID | None, Query()] = None,
            object_type: Annotated[str | None, Query(max_length=64)] = None,
            basis: Annotated[str, Query(min_length=1, max_length=32)] = "current",
            domain_id: Annotated[uuid.UUID | None, Query()] = None,
            period_from: Annotated[AwareDatetime | None, Query()] = None,
            period_to: Annotated[AwareDatetime | None, Query()] = None,
            owner_id: Annotated[uuid.UUID | None, Query()] = None,
            scope_id: Annotated[str | None, Query(max_length=200)] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 25,
            cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None):
    invalid = _validate(request, {"group", "strategy_id", "object_type", "basis", "domain_id",
                                        "period_from", "period_to", "owner_id", "scope_id",
                                        "limit", "cursor"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_objects, token, group=group,
                              strategy_id=str(strategy_id) if strategy_id else None,
                              object_type=object_type, basis=basis,
                              domain_id=str(domain_id) if domain_id else None,
                              period_from=_iso(period_from), period_to=_iso(period_to),
                              owner_id=str(owner_id) if owner_id else None,
                              scope_id=scope_id, limit=limit, cursor=cursor)


@api.get("/objects/{object_id}")
def object_detail(request: Request, object_id: uuid.UUID,
                  revision_id: Annotated[uuid.UUID | None, Query()] = None,
                  strategy_id: Annotated[uuid.UUID | None, Query()] = None):
    invalid = _validate(request, {"revision_id", "strategy_id"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_detail, token, str(object_id),
                             revision_id=str(revision_id) if revision_id else None,
                             strategy_id=str(strategy_id) if strategy_id else None,
                             environment=_environment())


@api.get("/objects/{object_id}/revisions")
def revisions(request: Request, object_id: uuid.UUID,
              limit: Annotated[int, Query(ge=1, le=100)] = 50,
              cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None):
    invalid = _validate(request, {"limit", "cursor"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_revisions, token, str(object_id), limit=limit, cursor=cursor)


@api.get("/objects/{object_id}/revisions/{revision_id}")
def revision(request: Request, object_id: uuid.UUID, revision_id: uuid.UUID):
    invalid = _validate(request, set())
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_revision, token, str(object_id), str(revision_id))


@api.get("/objects/{object_id}/reviews")
def reviews(request: Request, object_id: uuid.UUID,
            effective_only: Annotated[bool, Query()] = False):
    invalid = _validate(request, {"effective_only"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_reviews, token, str(object_id), effective_only=effective_only)


@api.get("/objects/{object_id}/receipts")
def object_receipts(request: Request, object_id: uuid.UUID,
                    limit: Annotated[int, Query(ge=1, le=100)] = 25,
                    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None):
    invalid = _validate(request, {"limit", "cursor"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_object_receipts, token, str(object_id), limit=limit, cursor=cursor)


@api.get("/objects/{object_id}/downstream")
def downstream(request: Request, object_id: uuid.UUID,
               revision_id: Annotated[uuid.UUID, Query()],
               limit: Annotated[int, Query(ge=1, le=100)] = 25,
               cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None):
    invalid = _validate(request, {"revision_id", "limit", "cursor"})
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_downstream, token, str(object_id), str(revision_id), limit=limit, cursor=cursor)


@api.get("/action-receipts/{receipt_id}")
def receipt(request: Request, receipt_id: uuid.UUID):
    invalid = _validate(request, set())
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_receipt, token, str(receipt_id))


@api.get("/evidence-assets/{object_id}/revisions/{revision_id}")
def evidence(request: Request, object_id: uuid.UUID, revision_id: uuid.UUID):
    invalid = _validate(request, set())
    if invalid:
        return invalid
    blocked = _guard(request)
    if blocked:
        return blocked
    token, error = _token_or_error()
    if error:
        return error
    return _read(reads.read_evidence, token, str(object_id), str(revision_id))


def assets_dir() -> Path:
    override = get_settings().tkos_dashboard_assets_dir
    return Path(override) if override else DEFAULT_ASSETS_DIR


def mount_dashboard(app: FastAPI) -> None:
    """Attach the facade, compiled assets and security headers when enabled."""
    if not _enabled_from_env():
        return
    app.include_router(api)
    directory = assets_dir()
    index = directory / "index.html"

    @app.middleware("http")
    async def dashboard_gate(request: Request, call_next):
        path = request.url.path
        if path == "/dashboard" or path.startswith("/dashboard/"):
            settings = get_settings()
            allowed_hosts = _split(settings.tkos_dashboard_allowed_hosts)
            host = request.headers.get("host", "").strip()
            if not allowed_hosts:
                return _json_error(503, "DASHBOARD_HOST_POLICY_UNAVAILABLE",
                                   "The dashboard host policy is not configured.")
            # DNS-rebinding guard: the page and its API are loopback-only, and
            # a missing Host is rejected instead of being treated as trusted.
            if not host or not _host_allowed(request):
                return _json_error(403, "FORBIDDEN",
                                   "Dashboard reads require the loopback dashboard origin.")
            response = await call_next(request)
            if path.startswith("/dashboard/api/"):
                response.headers.setdefault("Cache-Control", "no-store")
            response.headers["Content-Security-Policy"] = _CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            if path.startswith("/dashboard/assets/"):
                response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
            return response
        return await call_next(request)

    if index.is_file() and (directory / "assets").is_dir():
        @app.get("/dashboard", include_in_schema=False)
        @app.get("/dashboard/", include_in_schema=False)
        def dashboard_index():
            return FileResponse(index, media_type="text/html",
                                headers={"Cache-Control": "no-store"})

        app.mount("/dashboard/assets", StaticFiles(directory=str(directory / "assets")),
                  name="dashboard-assets")
    else:
        @app.get("/dashboard", include_in_schema=False)
        @app.get("/dashboard/", include_in_schema=False)
        def dashboard_missing_assets():
            return JSONResponse(status_code=503, content={"error": {
                "code": "DASHBOARD_ASSETS_MISSING",
                "message": "The compiled dashboard assets are not installed in this build."}})


__all__ = ["api", "mount_dashboard", "assets_dir", "DEFAULT_ASSETS_DIR"]
