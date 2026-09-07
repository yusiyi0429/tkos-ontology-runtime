"""GET /api/v1/entities/{code}?view=latest|chain."""
from __future__ import annotations

from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Query

from adapter.contracts import GkEntity, GkEntitySummary
from adapter.deps import get_conn
from adapter.errors import mapped
from adapter.settings import Settings, get_settings
from adapter.wm_views import list_issue_summaries, project_entity

router = APIRouter(prefix="/api/v1/entities", tags=["entities"])


@router.get("", response_model=list[GkEntitySummary])
@mapped
def entities(
    prefix: Annotated[Literal["ISS-"], Query()],
    *,
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[GkEntitySummary]:
    """List exact-title ISS anchor candidates for the clark compatibility path."""
    return list_issue_summaries(
        conn,
        tenant_id=settings.memory_tenant,
        organization_id=settings.memory_org,
    )


@router.get("/{code}", response_model=GkEntity)
@mapped
def entity(
    code: str,
    *,
    view: Literal["latest", "chain"] = "latest",
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GkEntity:
    """Return one scoped WM entity in M7 P1/P2 shape."""
    return project_entity(
        conn,
        code,
        view=view,
        tenant_id=settings.memory_tenant,
        organization_id=settings.memory_org,
    )
