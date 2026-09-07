"""Native HTTP response contracts owned by the standalone service."""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthReport(BaseModel):
    """Database and embedding readiness for one configured service scope."""

    ok: bool
    mode: str = "memory_service"
    db: bool = False
    embedding: bool = False
    chains: int = 0
    warnings: list[str] = Field(default_factory=list)
