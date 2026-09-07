"""Extract, list, update, and delete long-lived user memories.

写入确认规则：用户长期偏好/持续事项自动写入，用户事后可编辑删除——
所以 extract_user_memories 自动落库，list/update/delete 是用户管理入口。

连接边界（host capability boundary）：本模块不自开连接——单事务函数借用调用方传入的 ``conn``；
网络（chat/embed）与数据库分成多段的 ``extract_user_memories`` 显式接收
``connect_factory``：读事务取 transcript，网络调用不持有任何事务，写入用一个
短事务批量落库。
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory_service.common import conversation_transcript, embed_one

_GENERIC_LOOKUP = "user_memory 不存在或不属于该用户"


def _canonical_uuid_or_lookup(value) -> uuid.UUID:
    """Python 侧 UUID 规范化：非法输入与 missing/foreign 同类同文 LookupError，
    不送 PG、不毒化调用方事务；SQL 绑定 canonical ``uuid.UUID`` 而非原始替代表示。"""
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise LookupError(_GENERIC_LOOKUP) from exc

_EXTRACT_SYSTEM_PROMPT = (
    "你是用户长期记忆抽取助手。阅读对话事件记录，抽取值得长期记住的用户偏好、"
    "持续事项（ongoing_item）或重要结论（conclusion）。只抽取记录中明确出现的信息，"
    "不要编造。用 JSON 数组输出，每项形如 {\"kind\": \"preference|ongoing_item|conclusion\", "
    "\"content\": \"一句话描述\"}。没有可抽取内容时输出 []。只输出 JSON，不要任何其它文字。"
)

_KINDS = ("preference", "ongoing_item", "conclusion")


def list_user_memories(
    conn: Any, user_id: str, kind: str | None = None, include_deleted: bool = False,
) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        sql = "SELECT * FROM user_memories WHERE user_id=%(user_id)s"
        params: dict[str, Any] = {"user_id": user_id}
        if not include_deleted:
            sql += " AND NOT deleted"
        if kind:
            sql += " AND kind=%(kind)s"
            params["kind"] = kind
        sql += " ORDER BY updated_at DESC"
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def update_user_memory(
    conn: Any, memory_id: str, content: str | None = None, kind: str | None = None,
    *,
    owner_user_id: str,
) -> dict:
    if content is None and kind is None:
        raise ValueError("update_user_memory 需要至少一个待更新字段（content 或 kind）")
    if kind is not None and kind not in _KINDS:
        raise ValueError(f"非法 kind：{kind}，允许值 {_KINDS}")
    canonical_memory_id = _canonical_uuid_or_lookup(memory_id)
    canonical_owner = _canonical_uuid_or_lookup(owner_user_id)
    sets = ["updated_at = now()"]
    params: dict[str, Any] = {"memory_id": canonical_memory_id, "owner_user_id": canonical_owner}
    if content is not None:
        sets.append("content = %(content)s")
        params["content"] = content
    if kind is not None:
        sets.append("kind = %(kind)s")
        params["kind"] = kind
    with conn.cursor(row_factory=dict_row) as cur:
        # owner 不匹配与对象不存在走同一路径且文案不含 memory_id，不泄露目标是否存在。
        cur.execute(
            f"UPDATE user_memories SET {', '.join(sets)}"
            " WHERE memory_id=%(memory_id)s AND user_id=%(owner_user_id)s RETURNING *",
            params,
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError(_GENERIC_LOOKUP)
    return _row_to_dict(row)


def delete_user_memory(conn: Any, memory_id: str, *, owner_user_id: str) -> None:
    """GDPR 第 17 条：软删除，保留审计线索但不再出现在 list。"""
    canonical_memory_id = _canonical_uuid_or_lookup(memory_id)
    canonical_owner = _canonical_uuid_or_lookup(owner_user_id)
    r = conn.execute(
        "UPDATE user_memories SET deleted=true, updated_at=now()"
        " WHERE memory_id=%s AND user_id=%s RETURNING memory_id",
        (canonical_memory_id, canonical_owner),
    ).fetchone()
    if r is None:
        raise LookupError(_GENERIC_LOOKUP)


def _row_to_dict(row: dict) -> dict:
    out = dict(row)
    for k in ("memory_id", "user_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    out.pop("embedding", None)
    return out


def _parse_extracted_items(raw: str) -> list[dict]:
    raw = raw.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if match:
        raw = match.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    items = []
    for it in data:
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        content = it.get("content")
        if kind in _KINDS and isinstance(content, str) and content.strip():
            items.append({"kind": kind, "content": content.strip()})
    return items


def extract_user_memories(
    connect_factory: Callable[..., Any],
    conversation_id: str,
    *,
    user_id: str | None = None,
    gateway,
) -> list[dict]:
    """LLM 从对话事件提取偏好/持续事项/结论，自动写入 user_memories（source 记来源）。

    连接边界：读事务（resolve user_id + transcript）→ chat/embed 网络调用（不持有
    任何事务）→ 单个写事务批量 INSERT 抽取结果（原子：要么全部落库要么全不落）。

    显式传入 ``user_id`` 时必须等于 conversation owner；owner 不匹配与 conversation
    不存在使用同类泛化错误（LookupError），不泄露目标是否存在。

    ``conversation_id`` 在任何 SQL 前 Python 侧 canonical：非法输入与 conversation
    不存在同文，不触发 PG UUID cast 错误、不毒化事务；后续 SQL 绑定 canonical 形式。
    """
    try:
        conversation_id = str(uuid.UUID(str(conversation_id)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise LookupError("conversation 不存在或与调用者不匹配") from exc
    with connect_factory() as conn:
        row = conn.execute(
            "SELECT user_id FROM conversations WHERE conversation_id=%s", (conversation_id,)
        ).fetchone()
        if row is None:
            raise LookupError("conversation 不存在或与调用者不匹配")
        owner_user_id = str(row[0])
        if user_id is None:
            user_id = owner_user_id
        elif str(user_id) != owner_user_id:
            raise LookupError("conversation 不存在或与调用者不匹配")
        transcript, source_range = conversation_transcript(conn, conversation_id)

    if not transcript:
        return []

    chat_result = gateway.chat(
        [
            {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": f"对话事件记录：\n{transcript}"},
        ],
        max_tokens=600,
    )
    items = _parse_extracted_items(chat_result.content or "")
    embeddings = [embed_one(gateway, item["content"]) for item in items]

    created: list[dict] = []
    with connect_factory() as conn:
        # 网络调用（chat/embed）期间不持有事务，owner 可能在间隙被改：写事务内重新
        # SELECT ... FOR SHARE 复核 conversation owner 仍等于已解析的 user_id，然后才
        # INSERT；不匹配即领域错误，本事务零写入（调用方 rollback）。
        owner_row = conn.execute(
            "SELECT user_id FROM conversations WHERE conversation_id=%s FOR SHARE",
            (conversation_id,),
        ).fetchone()
        if owner_row is None or str(owner_row[0]) != str(user_id):
            raise LookupError("conversation 不存在或与调用者不匹配")
        for item, embedding in zip(items, embeddings):
            source = {
                "type": "extracted_from_conversation",
                "conversation_id": str(conversation_id),
                "source_event_range": source_range,
                "model_used": chat_result.model,
            }
            row = conn.execute(
                """INSERT INTO user_memories(user_id, kind, content, source, embedding)
                   VALUES (%s,%s,%s,%s,%s)
                   RETURNING memory_id, created_at, updated_at""",
                (user_id, item["kind"], item["content"], Jsonb(source), embedding),
            ).fetchone()
            created.append(
                {
                    "memory_id": str(row[0]),
                    "user_id": str(user_id),
                    "kind": item["kind"],
                    "content": item["content"],
                    "source": source,
                    "created_at": row[1],
                    "updated_at": row[2],
                }
            )
    return created
