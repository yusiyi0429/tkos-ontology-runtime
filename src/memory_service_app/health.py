"""Canonical service readiness check shared by native and compatibility routes."""
from __future__ import annotations

import psycopg

from memory_service import working
from memory_service_app.contracts import HealthReport
from memory_service_app.settings import Settings


def build_health_report(settings: Settings) -> HealthReport:
    warnings: list[str] = []
    db_ok = False
    chains = 0
    conn: psycopg.Connection | None = None
    try:
        conn = psycopg.connect(
            settings.database_url, connect_timeout=settings.db_connect_timeout
        )
        conn.execute("SELECT 1").fetchone()
        db_ok = True
        chains = len(
            working.list_chains(
                conn,
                tenant_id=settings.memory_tenant,
                organization_id=settings.memory_org,
            )
        )
    except psycopg.Error as exc:
        warnings.append(f"数据库不可达：{exc}")
    finally:
        if conn is not None:
            conn.close()

    embedding = settings.embedding_configured
    if not embedding:
        warnings.append(
            "embedding 未配置（MEMORY_EMBEDDING_API_KEY/BASE_URL/MODEL），P3 将不可用"
        )
    if not settings.viewer_user_id:
        warnings.append("VIEWER_USER_ID 未配置，依赖 viewer 的端点将不可用")

    return HealthReport(
        ok=db_ok,
        db=db_ok,
        embedding=embedding,
        chains=chains,
        warnings=warnings,
    )
