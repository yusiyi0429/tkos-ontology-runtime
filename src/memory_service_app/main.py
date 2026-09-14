"""Standalone HTTP process for the governed TKOS Memory Service."""
from __future__ import annotations

from fastapi import Depends, FastAPI

from adapter.auth import require_clark_auth
from adapter.routes_dynamic import router as clark_dynamic_router
from adapter.routes_entities import router as clark_entities_router
from adapter.routes_health import router as clark_health_router
from adapter.routes_static import router as clark_static_router
from memory_service_app.routes_native import router as native_router
from memory_service_runtime.governed.routes import install_errors, router as governed_router
from memory_service_app import narrative

app = FastAPI(
    title="TKOS Memory Service",
    version="0.2.1",
    description=(
        "Standalone governed Memory Service with a native API and a temporary "
        "Clark GraphKnowledge compatibility façade."
    ),
)

app.include_router(native_router)
app.include_router(governed_router)
app.include_router(narrative.router)
install_errors(app)


@app.middleware("http")
async def narrative_health(request, call_next):
    if request.method == "GET" and request.url.path == "/healthz" and not request.headers.get("authorization") and narrative.enabled():
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(narrative.public_health)
    return await call_next(request)
_clark_auth = [Depends(require_clark_auth)]
app.include_router(clark_health_router, dependencies=_clark_auth)
app.include_router(clark_static_router, dependencies=_clark_auth)
app.include_router(clark_entities_router, dependencies=_clark_auth)
app.include_router(clark_dynamic_router, dependencies=_clark_auth)
