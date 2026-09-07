"""Episodic Memory：Conversation 摘要与语义召回。

``run_events`` 由 Workspace 管理；本模块只保存可重建的派生摘要，
不覆盖原始事件。

连接边界（host capability boundary）：本模块不自开连接——
- 单事务函数（``get_or_create_conversation_summary``）借用调用方传入的 ``conn``；
- 网络（chat/embed）与数据库分成多段的函数（``summarize_conversation``/
  ``save_run_summary``/``recall_episodes``）显式接收 ``connect_factory``，
  网络调用期间不持有任何连接/事务。
"""
from __future__ import annotations

from typing import Any, Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from memory_service.common import conversation_transcript, embed_one

_SUMMARY_SYSTEM_PROMPT = (
    "你是记忆整理助手。请阅读下面这段对话事件记录，用中文写一段简洁摘要"
    "（3-6 句），保留：用户目标、已确认结论、未决事项。不要编造事件记录中不存在的信息。"
)

_SUMMARY_USER_TMPL = "对话事件记录（按时间顺序）：\n{transcript}\n\n请输出摘要正文，不要加标题。"


def _generate_summary_text(gateway, transcript: str) -> tuple[str, Any]:
    """Generate summary text without holding a database connection."""
    if not transcript:
        transcript = "(本 Conversation 暂无可摘要的事件)"
    chat_result = gateway.chat(
        [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": _SUMMARY_USER_TMPL.format(transcript=transcript)},
        ],
        max_tokens=400,
    )
    summary_text = (chat_result.content or "").strip() or "(摘要生成为空)"
    return summary_text, chat_result


def summarize_conversation(connect_factory: Callable[..., Any], conversation_id: str, *, gateway) -> dict:
    """用 gateway.chat 生成 Conversation 摘要，写 episodic_summaries（含 embedding）。

    摘要是可重建派生数据，保存来源事件范围与生成模型信息，不覆盖原始事件。

    Transcript reads and INSERTs use separate short transactions.  Chat and embedding calls hold no
    database connection.
    """
    with connect_factory() as conn:
        transcript, source_range = conversation_transcript(conn, conversation_id)

    summary_text, chat_result = _generate_summary_text(gateway, transcript)
    embedding = embed_one(gateway, summary_text)

    with connect_factory() as conn:
        row = conn.execute(
            """INSERT INTO episodic_summaries
                 (conversation_id, content, source_event_range, model_used, embedding)
               VALUES (%s,%s,%s,%s,%s)
               RETURNING summary_id, created_at""",
            (conversation_id, summary_text, Jsonb(source_range), chat_result.model, embedding),
        ).fetchone()
        return {
            "summary_id": str(row[0]),
            "conversation_id": str(conversation_id),
            "content": summary_text,
            "source_event_range": source_range,
            "model_used": chat_result.model,
            "created_at": row[1],
        }


def save_run_summary(
    connect_factory: Callable[..., Any],
    conversation_id: str,
    run_id: str,
    content: str,
    source_event_range: dict[str, Any],
    *,
    model_used: str,
    gateway,
) -> str:
    """Persist a run-scoped derived summary through the Memory Service boundary.

    连接边界：embedding（网络）先生成，再开单个短事务写 INSERT。
    """
    embedding = embed_one(gateway, content)
    with connect_factory() as conn:
        row = conn.execute(
            """INSERT INTO episodic_summaries
                 (conversation_id, run_id, content, source_event_range, model_used, embedding)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING summary_id""",
            (conversation_id, run_id, content, Jsonb(source_event_range), model_used, embedding),
        ).fetchone()
    return str(row[0])


def get_or_create_conversation_summary(conn: Any, conversation_id: str, *, gateway) -> dict:
    """获取最近一次摘要；不存在则生成一次（conversation_summary_get 语义）。

    连接边界：借用调用方的 ``conn``（单事务）。check-then-create 用
    pg_advisory_xact_lock(hashtext(conversation_id)) 在同一 conversation_id 上互斥，
    避免并发调用都判定"不存在"从而各自触发一次摘要生成（重复摘要+重复计费）。锁是
    xact 级的；为了真正串行住"检查-创建"这个整体，这里的 chat+embed 网络调用会在
    锁持有期间发生——这是故意的权衡（同一 conversation 真正并发的概率低，频率远低于
    governance.confirm）。
    """
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (conversation_id,))
    row = conn.execute(
        """SELECT summary_id, conversation_id, content, source_event_range, model_used, created_at
           FROM episodic_summaries WHERE conversation_id=%s
           ORDER BY created_at DESC LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    if row:
        return {
            "summary_id": str(row[0]),
            "conversation_id": str(row[1]),
            "content": row[2],
            "source_event_range": row[3],
            "model_used": row[4],
            "created_at": row[5],
        }

    transcript, source_range = conversation_transcript(conn, conversation_id)
    summary_text, chat_result = _generate_summary_text(gateway, transcript)
    embedding = embed_one(gateway, summary_text)
    new_row = conn.execute(
        """INSERT INTO episodic_summaries
             (conversation_id, content, source_event_range, model_used, embedding)
           VALUES (%s,%s,%s,%s,%s)
           RETURNING summary_id, created_at""",
        (conversation_id, summary_text, Jsonb(source_range), chat_result.model, embedding),
    ).fetchone()
    return {
        "summary_id": str(new_row[0]),
        "conversation_id": str(conversation_id),
        "content": summary_text,
        "source_event_range": source_range,
        "model_used": chat_result.model,
        "created_at": new_row[1],
    }


def recall_episodes(connect_factory: Callable[..., Any], user_id: str, query: str, top_k: int = 5, *, gateway) -> list[dict]:
    """pgvector halfvec 余弦检索：某用户名下 Conversation 的相关历史 Episode 摘要。

    连接边界：query embedding（网络）先生成，再开单个只读短事务。
    """
    query_embedding = embed_one(gateway, query)
    with connect_factory() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT es.summary_id, es.conversation_id, es.run_id, es.content,
                      es.source_event_range, es.created_at,
                      es.embedding <=> %(q)s AS distance
               FROM episodic_summaries es
               JOIN conversations c ON c.conversation_id = es.conversation_id
               WHERE c.user_id = %(user_id)s AND es.embedding IS NOT NULL
               ORDER BY distance ASC
               LIMIT %(top_k)s""",
            {"q": query_embedding, "user_id": user_id, "top_k": top_k},
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "summary_id": str(r["summary_id"]),
                "conversation_id": str(r["conversation_id"]),
                "run_id": str(r["run_id"]) if r["run_id"] else None,
                "content": r["content"],
                "source_event_range": r["source_event_range"],
                "created_at": r["created_at"],
                "distance": float(r["distance"]),
                "score": 1.0 - float(r["distance"]),
            }
        )
    return out
