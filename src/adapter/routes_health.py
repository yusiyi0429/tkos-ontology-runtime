"""Clark-compatible GET /healthz readiness route."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from adapter.contracts import HealthzReport
from adapter.settings import Settings, get_settings
from memory_service_app.health import build_health_report

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthzReport)
def healthz(
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthzReport | JSONResponse:
    """Return 503 with the stable report body when the database is unavailable."""
    report = build_health_report(settings)
    if not report.db:
        return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
    return report
