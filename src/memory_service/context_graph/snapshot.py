"""Context Graph 离线快照：确定性导出、对象存储回读和恢复前验证。

本模块刻意不提供 switch、delete 或恢复写入。数据库连接和对象存储均由调用方显式注入；
调用方负责提交/回滚数据库事务。对象存储写入先于快照账本，数据库回滚产生的孤儿对象应由
独立清理任务按内容寻址前缀回收。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, ContextManager, Protocol
from urllib.parse import quote

from psycopg import IsolationLevel
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from memory_service.context_graph.concurrency import (
    acquire_exclusive_session_lock,
    release_exclusive_session_lock,
)


# Historical immutable objects keep the pre-extraction format identifier forever.
FORMAT_V1 = "aw.context_graph.snapshot.v1"
FORMAT = "memory_service.context_graph.snapshot.v2"
_REQUIRED_SECTIONS = {
    "format", "generation", "entities", "relations", "entity_source_refs",
    "relation_source_refs", "proposals", "audits",
}
_V1_DOCUMENT_SECTIONS = {"fragments", "documents"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SnapshotError(RuntimeError):
    """快照模块基础异常。"""


class SnapshotScopeError(SnapshotError):
    """generation 不存在、状态不允许或 scope 不匹配。"""


class SnapshotAuthorizationError(SnapshotError):
    """快照责任人不是同 scope 的 human。"""


class SnapshotValidationError(SnapshotError):
    """快照不是可恢复的 canonical、闭合数据集。"""


class SnapshotStorageError(SnapshotError):
    """对象上传或回读失败。"""


class SnapshotConflictError(SnapshotError):
    """内容寻址对象或已有账本与当前结果冲突。"""


class SnapshotObjectStore(Protocol):
    """Immutable, create-only object storage supplied by the host."""

    def preflight_immutability(self) -> None:
        """Fail closed unless versioning, Object Lock, and retention are active."""

    def put_if_absent(self, key: str, data: bytes, *, content_type: str) -> bool | None:
        """仅当 key 不存在时写入；已存在可返回 False，但不得覆盖。"""

    def get(self, key: str) -> bytes:
        """读取完整对象字节。"""


@dataclass(frozen=True)
class SnapshotArtifact:
    payload: dict[str, Any]
    data: bytes
    sha256: str
    entity_count: int
    relation_count: int


def _json_value(value: Any) -> Any:
    """把 PG 值转为稳定 JSON 值；业务 JSON 原样保留，不做任何键删除。

    物理向量列 semantic_entities.embedding 由导出 SQL 的显式列清单排除，
    治理 JSON（proposal/audit）中的业务数据必须逐字节保留，否则快照不是全量。
    """
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        # timestamptz 的渲染随连接 session TimeZone 变化；统一规范化为 UTC 并用固定
        # +00:00 表示，naive 值（timestamptz 不会产生，纯防御）视为 UTC，保证同一
        # 数据库状态在任何连接时区下导出字节一致。
        normalized = (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
                      else value.astimezone(timezone.utc))
        return normalized.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, float) and not math.isfinite(value):
        raise SnapshotValidationError("snapshot cannot encode NaN or infinity")
    return value


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """RFC-8259 兼容的固定 key 顺序、无多余空白 UTF-8 JSON。"""
    try:
        return json.dumps(
            _json_value(payload), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SnapshotValidationError(f"snapshot is not JSON serializable: {exc}") from exc


def _rows(cur, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    return [_json_value(dict(row)) for row in cur.fetchall()]


def _one(cur, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    cur.execute(sql, params)
    row = cur.fetchone()
    return _json_value(dict(row)) if row is not None else None


def _session_exclusive_lock(conn, tenant_id: str, organization_id: str) -> None:
    acquire_exclusive_session_lock(conn, tenant_id, organization_id)


def _session_exclusive_unlock(conn, tenant_id: str, organization_id: str) -> None:
    release_exclusive_session_lock(conn, tenant_id, organization_id)


def _ensure_repeatable_read(conn) -> None:
    """确保多条导出查询共享同一 MVCC 视图，不依赖长时间 advisory lock。"""
    if conn.info.transaction_status == TransactionStatus.IDLE:
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
    with conn.cursor() as cur:
        cur.execute("SHOW transaction_isolation")
        isolation = cur.fetchone()[0]
    if isolation not in ("repeatable read", "serializable"):
        raise SnapshotError(
            "snapshot connection must be idle or already use repeatable read/serializable"
        )


def _require_human_in_scope(
    conn, *, user_id: str, tenant_id: str, organization_id: str, lock: bool = False,
) -> None:
    """校验 actor 是同 scope 的 human；lock=True 时对 actor 行 FOR SHARE。

    FOR SHARE 行锁在事务内持有至 commit，防止校验后、落账前的并发身份变更
    （users.kind / scope UPDATE）穿透为非同 scope human 的 created_by。
    """
    sql = "SELECT kind,tenant_id,organization_id FROM users WHERE user_id=%s"
    if lock:
        sql += " FOR SHARE"
    row = conn.execute(sql, (user_id,)).fetchone()
    if row is None:
        raise SnapshotAuthorizationError("snapshot actor not found")
    if row[0] != "human":
        raise SnapshotAuthorizationError("snapshot actor must be human")
    if row[1] != tenant_id or row[2] != organization_id:
        raise SnapshotAuthorizationError("snapshot actor is outside requested scope")


def _iter_source_refs(value: Any) -> Iterator[dict[str, Any]]:
    """递归产出治理 JSON 中受契约约束的 source_refs 条目。

    契约形态：任意 JSON 位置上名为 source_refs 的数组，其元素是含 fragment_id
    字符串键的对象。该投影出现在 proposal.proposed_content、
    required_relations[*].source_refs 以及 audit before/after_content 中，可能引用
    尚未确认的 pending fragment 或已被原子替换的历史 fragment，因此快照闭包不能
    只依赖当前实体/关系 refs 表。
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_refs" and isinstance(item, list):
                for entry in item:
                    if isinstance(entry, dict) and isinstance(entry.get("fragment_id"), str):
                        yield entry
            else:
                yield from _iter_source_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_source_refs(item)


def generate_snapshot(
    conn, *, tenant_id: str, organization_id: str, generation_id: str,
) -> SnapshotArtifact:
    """从显式 current/retired generation 生成确定性、闭合的 canonical JSON。

    函数使用 REPEATABLE READ 一致性视图但不提交事务。所有查询都显式列字段，因而
    semantic_entities.embedding 向量列永远不会进入快照；治理 JSON 中的业务数据
    （含名为 embedding 的业务键）原样保留。来源引用携带冻结的摘录、哈希和定位快照，
    快照不读取或复制 distill 所拥有的 documents/document_fragments。
    timestamptz 统一规范化为 UTC(+00:00) 再序列化，导出字节不受连接 session 时区影响。
    组织锁延后到回读成功后的账本登记阶段，对象存储 I/O 不占用锁窗。
    """
    _ensure_repeatable_read(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        generation = _one(
            cur,
            """SELECT generation_id,tenant_id,organization_id,label,status,baseline_audit_seq,
                      created_at,retired_at
                 FROM context_graph_versions WHERE generation_id=%s""",
            (generation_id,),
        )
        if generation is None:
            raise SnapshotScopeError("generation not found")
        if (generation["tenant_id"] != tenant_id
                or generation["organization_id"] != organization_id):
            raise SnapshotScopeError("generation is outside requested scope")
        if generation["status"] not in ("current", "retired"):
            raise SnapshotScopeError("only current or retired generation can be snapshotted")

        scope = (generation_id, tenant_id, organization_id)
        entities = _rows(cur, """SELECT entity_id,tenant_id,organization_id,entity_type,name,content,
                    revision,status,created_at,updated_at,normalized_name,type_key,rationale,
                    confirmed_by,confirmed_at,graph_generation_id,strategic_level,strategic_period,
                    outcome_level,status_scope,org_subtype,is_moat
               FROM semantic_entities
              WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s
              ORDER BY entity_id""", scope)
        relations = _rows(cur, """SELECT relation_id,tenant_id,organization_id,source_id,target_id,
                    relation_type,content,revision,status,created_at,rationale,confirmed_by,
                    confirmed_at,graph_generation_id,updated_at
               FROM semantic_relations
              WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s
              ORDER BY relation_id""", scope)
        entity_refs = _rows(cur, """SELECT entity_id,graph_generation_id,tenant_id,organization_id,
                    fragment_id,ordinal,excerpt_snapshot,content_hash_snapshot,source_locator_snapshot
               FROM semantic_entity_source_refs
              WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s
              ORDER BY entity_id,ordinal,fragment_id""", scope)
        relation_refs = _rows(cur, """SELECT relation_id,graph_generation_id,tenant_id,organization_id,
                    fragment_id,ordinal,excerpt_snapshot,content_hash_snapshot,source_locator_snapshot
               FROM semantic_relation_source_refs
              WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s
              ORDER BY relation_id,ordinal,fragment_id""", scope)
        proposals = _rows(cur, """SELECT proposal_id,tenant_id,organization_id,target_kind,target_id,
                    base_revision,action,proposed_content,change_reason,evidence,source,
                    import_session_id,confidence,proposed_by,status,created_at,graph_generation_id
               FROM memory_proposals
              WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s
              ORDER BY proposal_id""", scope)
        audits = _rows(cur, """SELECT a.audit_id,a.proposal_id,a.target_kind,a.target_id,a.revision,
                    a.before_content,a.after_content,a.decision,a.decided_by,a.decision_note,
                    a.decided_at,a.audit_seq
               FROM memory_audit a JOIN memory_proposals p ON p.proposal_id=a.proposal_id
              WHERE p.graph_generation_id=%s AND p.tenant_id=%s AND p.organization_id=%s
              ORDER BY a.audit_seq,a.audit_id""", scope)

    payload = {
        "format": FORMAT,
        "generation": generation,
        "entities": entities,
        "relations": relations,
        "entity_source_refs": entity_refs,
        "relation_source_refs": relation_refs,
        "proposals": proposals,
        "audits": audits,
    }
    data = canonical_json_bytes(payload)
    digest = hashlib.sha256(data).hexdigest()
    # 生成时也执行完整恢复前验证，避免上传一个内部不闭合的数据集。
    validate_snapshot(
        data, expected_sha256=digest, tenant_id=tenant_id,
        organization_id=organization_id, generation_id=generation_id,
    )
    return SnapshotArtifact(payload, data, digest, len(entities), len(relations))


def _require_list(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    value = payload.get(section)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SnapshotValidationError(f"snapshot section {section} must be a list of objects")
    return value


def _unique(rows: list[dict[str, Any]], key: str, section: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if value is None or str(value) in result:
            raise SnapshotValidationError(f"{section} has missing or duplicate {key}")
        result[str(value)] = row
    return result


def _check_scope(
    rows: list[dict[str, Any]], section: str, tenant_id: str,
    organization_id: str, generation_id: str | None = None,
) -> None:
    for row in rows:
        if row.get("tenant_id") != tenant_id or row.get("organization_id") != organization_id:
            raise SnapshotValidationError(f"{section} contains cross-scope row")
        if generation_id is not None and str(row.get("graph_generation_id")) != generation_id:
            raise SnapshotValidationError(f"{section} contains cross-generation row")


def validate_snapshot(
    data: bytes, *, expected_sha256: str | None = None, tenant_id: str | None = None,
    organization_id: str | None = None, generation_id: str | None = None,
) -> dict[str, Any]:
    """验证哈希、canonical 编码、必需段、scope/代际和恢复引用闭包。"""
    if not isinstance(data, bytes):
        raise SnapshotValidationError("snapshot data must be bytes")
    actual_hash = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and actual_hash != expected_sha256:
        raise SnapshotValidationError("snapshot SHA-256 mismatch")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotValidationError("snapshot is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise SnapshotValidationError("snapshot root must be an object")
    snapshot_format = payload.get("format")
    if snapshot_format not in (FORMAT, FORMAT_V1):
        raise SnapshotValidationError("unsupported snapshot format")
    required = _REQUIRED_SECTIONS | (_V1_DOCUMENT_SECTIONS if snapshot_format == FORMAT_V1 else set())
    actual_sections = set(payload)
    missing = required - actual_sections
    if missing:
        raise SnapshotValidationError(f"snapshot missing sections: {sorted(missing)}")
    unexpected = actual_sections - required
    if unexpected:
        raise SnapshotValidationError(f"snapshot has unexpected sections: {sorted(unexpected)}")
    if canonical_json_bytes(payload) != data:
        raise SnapshotValidationError("snapshot JSON is not canonical")

    generation = payload.get("generation")
    if not isinstance(generation, dict):
        raise SnapshotValidationError("snapshot generation must be an object")
    actual_tenant = str(generation.get("tenant_id"))
    actual_org = str(generation.get("organization_id"))
    actual_generation = str(generation.get("generation_id"))
    if generation.get("status") not in ("current", "retired"):
        raise SnapshotValidationError("snapshot generation status must be current or retired")
    if tenant_id is not None and actual_tenant != tenant_id:
        raise SnapshotValidationError("snapshot tenant mismatch")
    if organization_id is not None and actual_org != organization_id:
        raise SnapshotValidationError("snapshot organization mismatch")
    if generation_id is not None and actual_generation != str(generation_id):
        raise SnapshotValidationError("snapshot generation mismatch")

    entities = _require_list(payload, "entities")
    relations = _require_list(payload, "relations")
    entity_refs = _require_list(payload, "entity_source_refs")
    relation_refs = _require_list(payload, "relation_source_refs")
    proposals = _require_list(payload, "proposals")
    audits = _require_list(payload, "audits")
    fragments = _require_list(payload, "fragments") if snapshot_format == FORMAT_V1 else []
    documents = _require_list(payload, "documents") if snapshot_format == FORMAT_V1 else []
    for section, rows in (
        ("entities", entities), ("relations", relations),
        ("entity_source_refs", entity_refs), ("relation_source_refs", relation_refs),
        ("proposals", proposals),
    ):
        _check_scope(rows, section, actual_tenant, actual_org, actual_generation)
    if snapshot_format == FORMAT_V1:
        _check_scope(fragments, "fragments", actual_tenant, actual_org)
        _check_scope(documents, "documents", actual_tenant, actual_org)

    entity_by_id = _unique(entities, "entity_id", "entities")
    relation_by_id = _unique(relations, "relation_id", "relations")
    proposal_by_id = _unique(proposals, "proposal_id", "proposals")
    fragment_by_id = _unique(fragments, "fragment_id", "fragments")
    document_by_id = _unique(documents, "document_id", "documents")
    _unique(audits, "audit_id", "audits")

    for entity in entities:
        if "embedding" in entity:
            raise SnapshotValidationError("entity embedding must not be exported")
    for relation in relations:
        if str(relation.get("source_id")) not in entity_by_id or str(relation.get("target_id")) not in entity_by_id:
            raise SnapshotValidationError("relation has dangling endpoint")
    for audit in audits:
        if str(audit.get("proposal_id")) not in proposal_by_id:
            raise SnapshotValidationError("audit has dangling proposal")

    seen_ref_keys: set[tuple[str, str]] = set()
    seen_ref_ordinals: set[tuple[str, int]] = set()
    for section, refs, owner_key, owners in (
        ("entity_source_refs", entity_refs, "entity_id", entity_by_id),
        ("relation_source_refs", relation_refs, "relation_id", relation_by_id),
    ):
        for ref in refs:
            owner_id = str(ref.get(owner_key))
            fragment_id = str(ref.get("fragment_id"))
            if owner_id not in owners:
                raise SnapshotValidationError(f"{section} has dangling owner")
            fragment = fragment_by_id.get(fragment_id)
            if snapshot_format == FORMAT_V1 and fragment is None:
                raise SnapshotValidationError(f"{section} has dangling fragment")
            ref_key = (f"{section}:{owner_id}", fragment_id)
            if ref_key in seen_ref_keys:
                raise SnapshotValidationError(f"{section} has duplicate owner/fragment")
            seen_ref_keys.add(ref_key)
            # 镜像 DDL 的 UNIQUE(owner_id, ordinal)：恢复数据集不得违反重放约束。
            ordinal_key = (f"{section}:{owner_id}", ref.get("ordinal"))
            if ordinal_key in seen_ref_ordinals:
                raise SnapshotValidationError(f"{section} has duplicate ordinal for owner")
            seen_ref_ordinals.add(ordinal_key)
            excerpt = ref.get("excerpt_snapshot")
            content_hash = ref.get("content_hash_snapshot")
            if not isinstance(excerpt, str) or not isinstance(content_hash, str):
                raise SnapshotValidationError(f"{section} has invalid source snapshot")
            if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != content_hash:
                raise SnapshotValidationError(f"{section} source snapshot hash mismatch")
            if snapshot_format == FORMAT_V1:
                if excerpt != fragment.get("excerpt"):
                    raise SnapshotValidationError(f"{section} excerpt snapshot mismatch")
                if content_hash != fragment.get("content_hash"):
                    raise SnapshotValidationError(f"{section} content hash snapshot mismatch")
            if not isinstance(ref.get("ordinal"), int) or ref["ordinal"] < 1:
                raise SnapshotValidationError(f"{section} has invalid ordinal")

    if snapshot_format == FORMAT_V1:
        for section, rows in (("proposals", proposals), ("audits", audits)):
            for row in rows:
                for ref in _iter_source_refs(row):
                    fragment = fragment_by_id.get(str(ref.get("fragment_id")))
                    if fragment is None:
                        raise SnapshotValidationError(f"{section} source_refs has dangling fragment")
                    if ref.get("excerpt_snapshot") is not None and ref["excerpt_snapshot"] != fragment.get("excerpt"):
                        raise SnapshotValidationError(f"{section} source_refs excerpt snapshot mismatch")
                    if ref.get("content_hash_snapshot") is not None and ref["content_hash_snapshot"] != fragment.get("content_hash"):
                        raise SnapshotValidationError(f"{section} source_refs content hash snapshot mismatch")

    seen_fragment_positions: set[tuple[str, int, int]] = set()
    for fragment in fragments:
        # 镜像 DDL 的 UNIQUE(document_id, chunk_index, fragment_ordinal)。
        position = (str(fragment.get("document_id")), fragment.get("chunk_index"),
                    fragment.get("fragment_ordinal"))
        if position in seen_fragment_positions:
            raise SnapshotValidationError("fragments has duplicate document position")
        seen_fragment_positions.add(position)
        content_hash = fragment.get("content_hash")
        if not isinstance(content_hash, str) or not _SHA256_RE.fullmatch(content_hash):
            raise SnapshotValidationError("fragment has invalid content hash")
        if hashlib.sha256(str(fragment.get("excerpt", "")).encode("utf-8")).hexdigest() != content_hash:
            raise SnapshotValidationError("fragment excerpt does not match content hash")
        if str(fragment.get("document_id")) not in document_by_id:
            raise SnapshotValidationError("fragment has dangling document")
    for document in documents:
        content_hash = document.get("content_hash")
        if not isinstance(content_hash, str) or not _SHA256_RE.fullmatch(content_hash):
            raise SnapshotValidationError("referenced document has no locked SHA-256")

    return payload


def _readback_object(
    object_store: SnapshotObjectStore, *, key: str, expected_sha256: str,
    tenant_id: str, organization_id: str, generation_id: str,
) -> dict[str, Any]:
    try:
        data = object_store.get(key)
    except Exception as exc:
        raise SnapshotStorageError(f"snapshot readback failed: {exc}") from exc
    return validate_snapshot(
        data, expected_sha256=expected_sha256, tenant_id=tenant_id,
        organization_id=organization_id, generation_id=generation_id,
    )


def readback_snapshot(
    conn, object_store: SnapshotObjectStore, *, snapshot_id: str,
    tenant_id: str | None = None, organization_id: str | None = None,
    generation_id: str | None = None,
) -> dict[str, Any]:
    """按数据库账本的权威 key/hash 回读，并执行完整恢复前验证。"""
    with conn.cursor(row_factory=dict_row) as cur:
        row = _one(
            cur,
            """SELECT snapshot_id,graph_generation_id,tenant_id,organization_id,s3_key,
                      snapshot_sha256,hash_verified
                 FROM context_graph_snapshots WHERE snapshot_id=%s""",
            (snapshot_id,),
        )
    if row is None:
        raise SnapshotValidationError("snapshot ledger entry not found")
    ledger_tenant = row["tenant_id"]
    ledger_org = row["organization_id"]
    ledger_generation = str(row["graph_generation_id"])
    if tenant_id is not None and tenant_id != ledger_tenant:
        raise SnapshotValidationError("snapshot ledger tenant mismatch")
    if organization_id is not None and organization_id != ledger_org:
        raise SnapshotValidationError("snapshot ledger organization mismatch")
    if generation_id is not None and str(generation_id) != ledger_generation:
        raise SnapshotValidationError("snapshot ledger generation mismatch")
    if not row["hash_verified"]:
        raise SnapshotValidationError("snapshot ledger hash is not verified")
    return _readback_object(
        object_store, key=row["s3_key"], expected_sha256=row["snapshot_sha256"],
        tenant_id=ledger_tenant, organization_id=ledger_org,
        generation_id=ledger_generation,
    )


def _object_key(tenant_id: str, organization_id: str, generation_id: str, digest: str) -> str:
    parts = (tenant_id, organization_id, generation_id, digest)
    encoded = [quote(str(part), safe="") for part in parts]
    return "context-graph/snapshots/" + "/".join(encoded[:3]) + f"/{encoded[3]}.json"


def create_snapshot(
    db_connect: Callable[[], ContextManager[Any]], object_store: SnapshotObjectStore, *,
    tenant_id: str, organization_id: str, generation_id: str, created_by: str,
) -> dict[str, Any]:
    """以“导出事务 → 对象回读 → 短账本事务”生成并幂等登记快照。

    连接工厂而非长生命周期连接是有意的：对象存储 I/O 不得占用组织锁。账本连接先在
    READ COMMITTED 下排队取得 session 级组织锁，再开启新的 REPEATABLE READ 视图复算；
    因而既能看见先行并发请求的提交，也不会在等待锁期间固定旧 MVCC snapshot。actor
    校验前置到导出之前；账本阶段对 actor 行 FOR SHARE 持有至 commit，阻断身份 TOCTOU。
    """
    with db_connect() as export_conn:
        # Establish REPEATABLE READ and validate the actor before exporting the graph.  Unauthorized
        # callers neither generate a large artifact nor learn whether the generation exists.
        _ensure_repeatable_read(export_conn)
        _require_human_in_scope(
            export_conn, user_id=created_by, tenant_id=tenant_id,
            organization_id=organization_id,
        )
        artifact = generate_snapshot(
            export_conn, tenant_id=tenant_id, organization_id=organization_id,
            generation_id=generation_id,
        )

    key = _object_key(tenant_id, organization_id, generation_id, artifact.sha256)
    try:
        object_store.put_if_absent(key, artifact.data, content_type="application/json")
    except Exception as exc:
        raise SnapshotStorageError(f"snapshot upload failed: {exc}") from exc
    _readback_object(
        object_store, key=key, expected_sha256=artifact.sha256,
        tenant_id=tenant_id, organization_id=organization_id, generation_id=generation_id,
    )

    with db_connect() as ledger_conn:
        _session_exclusive_lock(ledger_conn, tenant_id, organization_id)
        try:
            current = generate_snapshot(
                ledger_conn, tenant_id=tenant_id, organization_id=organization_id,
                generation_id=generation_id,
            )
            # FOR SHARE 行锁持有至账本 commit：并发身份变更在校验后、落账前被阻塞，
            # 落出的 created_by/hash_verified_by 不可能穿透为非同 scope human。
            _require_human_in_scope(
                ledger_conn, user_id=created_by, tenant_id=tenant_id,
                organization_id=organization_id, lock=True,
            )
            if (current.sha256 != artifact.sha256
                    or current.entity_count != artifact.entity_count
                    or current.relation_count != artifact.relation_count):
                raise SnapshotConflictError("graph changed between export and ledger verification")

            with ledger_conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT * FROM context_graph_snapshots
                        WHERE graph_generation_id=%s AND tenant_id=%s AND organization_id=%s
                          AND snapshot_sha256=%s ORDER BY created_at,snapshot_id""",
                    (generation_id, tenant_id, organization_id, artifact.sha256),
                )
                existing = cur.fetchall()
                if existing:
                    if any(row["s3_key"] != key or not row["hash_verified"] for row in existing):
                        raise SnapshotConflictError(
                            "existing snapshot ledger conflicts with content-addressed object"
                        )
                    result = _json_value(dict(existing[0]))
                    result["idempotent"] = True
                else:
                    cur.execute(
                        """INSERT INTO context_graph_snapshots
                             (graph_generation_id,tenant_id,organization_id,s3_key,snapshot_sha256,
                              entity_count,relation_count,hash_verified,hash_verified_by,hash_verified_at,
                              created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s,now(),%s)
                           RETURNING *""",
                        (generation_id, tenant_id, organization_id, key, artifact.sha256,
                         artifact.entity_count, artifact.relation_count, created_by, created_by),
                    )
                    result = _json_value(dict(cur.fetchone()))
                    result["idempotent"] = False
            ledger_conn.commit()  # 账本提交后才释放 session lock
        except BaseException as original:
            # 回滚/解锁失败不得掩盖原始业务异常：诊断信息以异常注记链式保留，
            # 组织锁由连接关闭兜底释放（db.connect 在 finally 中 close）。
            try:
                ledger_conn.rollback()
            except Exception as rollback_error:
                original.add_note(f"snapshot ledger rollback failed: {rollback_error}")
            try:
                _session_exclusive_unlock(ledger_conn, tenant_id, organization_id)
            except Exception as unlock_error:
                original.add_note(f"snapshot advisory unlock failed: {unlock_error}")
            raise
        _session_exclusive_unlock(ledger_conn, tenant_id, organization_id)
    return result
