"""Embedding text assembly for confirmed Context Graph entities.

``graph_entity_embedding_text`` builds the text that ``service.confirm`` embeds after commit.
"""
from __future__ import annotations

from typing import Any

from memory_service.context_graph.types import description_keys, type_label_zh


# 字段顺序 / 条件字段 / 类型标签均从 memory_service.context_graph.types 的契约派生函数取得，
# 本模块不再自存一份（避免与 context_pack 叙事投影双轨漂移）。


def graph_entity_embedding_text(type_key: str, name: str, rationale: str | None,
                                content: dict[str, Any]) -> str:
    """Build embedding text from the canonical type label and description fields."""
    parts: list[str] = [f"[{type_label_zh(type_key)}] {name}"]
    if rationale and rationale.strip():
        parts.append(rationale.strip())
    for key in description_keys(type_key):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif value is not None and not isinstance(value, (dict, list, bool)):
            parts.append(str(value))
    return "\n".join(parts)
