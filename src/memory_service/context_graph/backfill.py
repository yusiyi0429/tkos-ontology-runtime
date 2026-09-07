"""Idempotent embedding backfill for confirmed Context Graph entities.

Network calls run outside database transactions.  Existing vectors are skipped, failures are
isolated per entity, and snapshot hashes are unaffected because embeddings are not serialized.
The host supplies both the model gateway and connection factory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from pgvector import HalfVector
from psycopg.rows import dict_row

from memory_service.context_graph.types import description_keys, type_label_zh


class EmbeddingGateway(Protocol):
    """embed 能力最小接口；生产走 aw.models.get_gateway()。"""

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...


@dataclass(frozen=True)
class BackfillResult:
    """回填结果（幂等；含失败清单）。"""

    generation_id: str
    total_entities: int
    already_embedded: int
    newly_embedded: int
    failed: tuple[tuple[str, str], ...]  # (entity_id, error_message)

    @property
    def pending(self) -> int:
        return self.total_entities - self.already_embedded

    def to_json(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "total_entities": self.total_entities,
            "already_embedded": self.already_embedded,
            "newly_embedded": self.newly_embedded,
            "failed": [{"entity_id": entity_id, "error": msg} for entity_id, msg in self.failed],
            "pending_after": self.pending - self.newly_embedded - len(self.failed),
        }


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


def backfill_generation_embeddings(
    generation_id: str,
    *,
    tenant_id: str,
    organization_id: str,
    gateway: EmbeddingGateway,
    _connect: Callable[..., Any],
) -> BackfillResult:
    """Idempotently backfill embeddings for confirmed typed entities in one generation.

    Embedding network calls stay outside database transactions:
    1. 短事务 SELECT：获取该 generation 下 type_key IS NOT NULL 且 embedding IS NULL
       的实体清单（幂等：已有 embedding 的跳过）。
    2. 逐个 embed（网络）：失败隔离，不影响其他实体。
    3. 逐个 UPDATE（短事务）：单行 CAS 更新 embedding WHERE entity_id AND embedding IS NULL
       （防并发重复写：另一进程可能先写了，CAS 失败算成功不报错）。
    """
    # Read the next batch in a short transaction.
    with _connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT count(*) AS total FROM semantic_entities
                WHERE graph_generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s
                  AND type_key IS NOT NULL AND status='confirmed'""",
            {"gen": generation_id, "t": tenant_id, "o": organization_id},
        )
        total = cur.fetchone()["total"]
        cur.execute(
            """SELECT entity_id, type_key, name, rationale, content
                FROM semantic_entities
               WHERE graph_generation_id=%(gen)s AND tenant_id=%(t)s AND organization_id=%(o)s
                 AND type_key IS NOT NULL AND status='confirmed'
                 AND embedding IS NULL
               ORDER BY entity_id""",
            {"gen": generation_id, "t": tenant_id, "o": organization_id},
        )
        pending_rows = cur.fetchall()
    already_embedded = total - len(pending_rows)

    # Embed outside transactions, then persist each result in a short transaction.
    newly_embedded = 0
    failed: list[tuple[str, str]] = []
    for row in pending_rows:
        entity_id = str(row["entity_id"])
        try:
            text = graph_entity_embedding_text(
                type_key=str(row["type_key"]),
                name=str(row["name"]),
                rationale=row["rationale"],
                content=row["content"] or {},
            )
            vectors = gateway.embed([text])
            if not vectors or not vectors[0]:
                raise ValueError("embedder returned empty vector")
            vector = HalfVector(vectors[0])
        except Exception as exc:
            failed.append((entity_id, str(exc)))
            continue
        # Persist with a short CAS transaction to avoid concurrent duplicate writes.
        try:
            with _connect() as conn:
                updated = conn.execute(
                    """UPDATE semantic_entities SET embedding=%(vec)s
                        WHERE entity_id=%(id)s AND embedding IS NULL
                          AND graph_generation_id=%(gen)s
                          AND tenant_id=%(t)s AND organization_id=%(o)s""",
                    {"vec": vector, "id": entity_id, "gen": generation_id,
                     "t": tenant_id, "o": organization_id},
                ).rowcount
            if updated:
                newly_embedded += 1
            # updated==0 意味着另一进程已先写入，视为成功不报错。
        except Exception as exc:
            failed.append((entity_id, f"UPDATE failed: {exc}"))

    return BackfillResult(
        generation_id=generation_id,
        total_entities=total,
        already_embedded=already_embedded,
        newly_embedded=newly_embedded,
        failed=tuple(failed),
    )
