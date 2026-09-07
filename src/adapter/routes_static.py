"""GET /api/v1/context/static wire-compatible P0 endpoint."""
from __future__ import annotations

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Query, Response

from adapter.deps import get_conn
from adapter.errors import mapped
from adapter.render_static import render_static
from adapter.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/context", tags=["context"])


@router.get("/static", response_class=Response)
@mapped
def static_context(
    conn: Annotated[psycopg.Connection, Depends(get_conn)],
    settings: Annotated[Settings, Depends(get_settings)],
    viewer: Annotated[str, Query()] = "",
    as_of: Annotated[str | None, Query()] = None,
) -> Response:
    """Return P0 markdown using the configured in-scope human as authority.

    ``viewer`` remains in the signature for M7 wire compatibility.  Clark's
    legacy business code (for example ``PSN-01``) is not an identity authority;
    governance uses ``VIEWER_USER_ID`` after canonical human/scope validation.
    """
    del viewer
    text = render_static(
        conn,
        viewer_user_id=settings.viewer_user_id,
        tenant_id=settings.memory_tenant,
        organization_id=settings.memory_org,
        as_of=as_of,
    )
    return Response(content=text, media_type="text/markdown")
