"""Shared deterministic utilities and minimal capability protocols for Memory Service."""
from __future__ import annotations

from typing import Protocol


class EmbeddingGateway(Protocol):
    """embedding 能力最小接口，测试可用 fake 实现替换 aw.models.ModelGateway。"""

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...


def normalize_name(name: str) -> str:
    """Return the canonical identity key for a semantic entity name."""
    return " ".join(name.strip().lower().split())
