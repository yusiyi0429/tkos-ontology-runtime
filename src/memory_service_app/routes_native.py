"""Product-neutral HTTP entry points used by Memory Service clients."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from memory_service_app.contracts import HealthReport
from memory_service_app.health import build_health_report
from memory_service_app.settings import Settings, get_settings

router = APIRouter(prefix="/v1", tags=["memory-service"])


@router.get("/health", response_model=HealthReport)
def health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthReport | JSONResponse:
    """Report service, database, and embedding readiness."""
    report = build_health_report(settings)
    if not report.db:
        return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
    return report
