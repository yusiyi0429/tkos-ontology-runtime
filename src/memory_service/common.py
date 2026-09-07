"""Shared deterministic utilities and minimal capability protocols for Memory Service."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from pgvector import HalfVector

class EmbeddingGateway(Protocol):
    """embedding 能力最小接口，测试可用 fake 实现替换 aw.models.ModelGateway。"""

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...


def embed_one(gateway: EmbeddingGateway, text: str) -> HalfVector:
    """生成单条文本的 embedding，包装为可直接写入 halfvec 列的 HalfVector。"""
    vec = gateway.embed([text])[0]
    return HalfVector(vec)


def normalize_name(name: str) -> str:
    """Return the canonical identity key for a semantic entity name."""
    return " ".join(name.strip().lower().split())


def sha256_hex(value: Any) -> str:
    """内容哈希：dict/list 走稳定 JSON 序列化，字符串直接编码。"""
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def conversation_transcript(conn, conversation_id: str) -> tuple[str, dict]:
    """拼出某 Conversation 下全部 Run Event 的可读文本，及来源事件范围元信息。

    供 episodic.summarize_conversation 与 user_memory.extract_user_memories 复用。
    """
    rows = conn.execute(
        """SELECT e.event_id, e.run_id, e.sequence_number, e.event_type, e.payload, e.occurred_at
           FROM run_events e
           JOIN runs r ON r.run_id = e.run_id
           WHERE r.conversation_id = %s
           ORDER BY r.started_at, e.sequence_number""",
        (conversation_id,),
    ).fetchall()
    lines: list[str] = []
    run_ids: set[str] = set()
    first_event_id = last_event_id = None
    for event_id, run_id, _seq, event_type, payload, _occurred_at in rows:
        run_ids.add(str(run_id))
        if first_event_id is None:
            first_event_id = str(event_id)
        last_event_id = str(event_id)
        text = None
        if isinstance(payload, dict):
            text = payload.get("text") or payload.get("content")
        if event_type in ("user_message", "final_response") and text:
            speaker = "用户" if event_type == "user_message" else "Agent"
            lines.append(f"{speaker}: {text}")
        elif event_type == "tool_result" and text:
            lines.append(f"工具结果: {text}")
    source_range = {
        "conversation_id": str(conversation_id),
        "run_ids": sorted(run_ids),
        "event_count": len(rows),
        "first_event_id": first_event_id,
        "last_event_id": last_event_id,
    }
    return "\n".join(lines), source_range
