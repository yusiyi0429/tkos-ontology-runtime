"""Read-only workbench queries for the governed runtime (workbench v0.1).

These reads serve the four workbench pages (objects and relations, instance
detail, actions and receipts, context pack).  Every function runs inside the
existing ``db.transaction(token)`` fence and reuses ``db.object_row`` /
``db.authorize_domain`` / ``db.revision_row`` / ``readers.authorize_receipt``
so historical reads always use current rights.  Nothing here mutates business
objects, authority, reviews, receipts or the outbox; the pre-existing
``POST /v1/context-packs`` audit snapshot remains the single documented
exception and is not part of this module.

Cursors are unsigned opaque bookmarks: they bind endpoint, principal, scope,
filters and the source revision where applicable, and every page re-checks
current authorization.  A leaked or replayed bookmark therefore cannot widen
access, but it is not a tamper-proof audit credential (see
docs/workbench-read-api.md).
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
import json
from typing import Any, Callable
from uuid import UUID

from memory_service_runtime.governed import db, delivery, protocol, readers
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.models import PAYLOAD_MODELS

SCHEMA_VERSION = "workbench-read/0.1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_CURSOR_LENGTH = 4096

GENERIC_TYPES = tuple(PAYLOAD_MODELS)
DEDICATED_TYPES = ("EvidenceAsset", "Deliverable")
REGISTRATION_TYPES = ("ProtocolSentinel",)
KNOWN_TYPES = frozenset([*GENERIC_TYPES, *DEDICATED_TYPES, *REGISTRATION_TYPES])

_VERSIONING_NOTE = (
    "内容按不可变 revision 保存；latest_revision_id 是最新候选版本，"
    "effective_revision_id 是当前生效版本，两者可能指向不同 revision。"
)

# Static type catalog.  Labels/descriptions are metadata only: they never name
# customer objects, counts or policy content, and the listed actions are type
# semantics, not a claim that the current caller may execute them.
TYPE_CATALOG: list[dict[str, Any]] = [
    {
        "object_type": "CompanyOutcome", "label": "公司成果", "creation_mode": "generic_action",
        "description": "公司层成果目标；bootstrap 可建立已确认起点，confirm_outcome 确认，record_outcome_assessment 记录独立成果评估。",
        "reference_fields": ["upstream_refs: 上游精确 revision 引用（可为空）"],
    },
    {
        "object_type": "BusinessCommitment", "label": "业务承诺", "creation_mode": "generic_action",
        "description": "CEO 与 DOMAIN_DRI 双方握手确认、activate_commitment 生效的业务承诺。",
        "reference_fields": ["upstream_refs: 指向上游 CompanyOutcome 的精确 revision"],
    },
    {
        "object_type": "ExecutionCommitment", "label": "执行承诺", "creation_mode": "generic_action",
        "description": "DOMAIN_DRI 与 MISSION_DRI 双方握手确认、activate_commitment 生效的执行承诺。",
        "reference_fields": ["upstream_refs: 指向上游 BusinessCommitment 的精确 revision"],
    },
    {
        "object_type": "FeedbackThread", "label": "管理反馈", "creation_mode": "generic_action",
        "description": "open → routed → accepted → investigating 的管理反馈线程；验收、关闭与重开均为显式治理动作。",
        "reference_fields": [],
    },
    {
        "object_type": "ManagementAdjustment", "label": "管理调整", "creation_mode": "generic_action",
        "description": "绑定已确认 Decision 与当前 FeedbackThread 的原子调整包；空 changes 壳永远不会被应用。",
        "reference_fields": [
            "feedback_revision_id: 来源 FeedbackThread 的精确 revision（按 revision 解析所属对象）",
            "decision_revision_id: 依据 Decision 的精确 revision（按 revision 解析所属对象）",
            "changes[].from_revision_id/to_revision_id: 每个被调整对象的精确版本迁移",
        ],
    },
    {
        "object_type": "Decision", "label": "决策", "creation_mode": "generic_action",
        "description": "人类 confirm_decision 确认后才可被调整或关闭流程引用的决策记录。",
        "reference_fields": [],
    },
    {
        "object_type": "MetricObservation", "label": "指标观测", "creation_mode": "generic_action",
        "description": "带 valid 区间的指标观测；纠错产生新的不可变 revision，recorded_at 由服务端记录。",
        "reference_fields": ["upstream_refs: 观测依据的精确 revision 引用（可为空）"],
    },
    {
        "object_type": "WorkItem", "label": "工作项", "creation_mode": "generic_action",
        "description": "冻结基线的 DRI 交付工作项；baseline 不可改写，交付状态经 accept/submit/review 推进。",
        "reference_fields": [
            "execution_commitment_ref: 指向 ExecutionCommitment 的精确 revision",
            "feedback_ref: 可选，指向来源 FeedbackThread 的精确 revision",
            "dri_assignment_id/acceptor_assignment_id: 冻结责任 assignment 引用，经 /responsibility 解析",
        ],
    },
    {
        "object_type": "EvidenceAsset", "label": "证据资产", "creation_mode": "dedicated_action",
        "description": "经 POST /v1/evidence-assets 上传的版本化证据原件；字节存于对象存储，下载逐字节校验 hash。",
        "reference_fields": [],
    },
    {
        "object_type": "Deliverable", "label": "交付物", "creation_mode": "dedicated_action",
        "description": "只能经 submit_deliverable 进入的交付提交；每次提交产生新 revision，评审结论绑定精确版本。",
        "reference_fields": [
            "work_item_ref: 所属 WorkItem 的冻结 baseline revision",
            "execution_commitment_ref: 继承的 ExecutionCommitment 精确 revision",
            "evidence_revision_ids: 原始证据 revision，已持久化进 upstream_refs，证据关系不丢失",
        ],
    },
    {
        "object_type": "ProtocolSentinel", "label": "协议登记哨兵", "creation_mode": "control_plane_only",
        "description": "A1 协议登记的合成哨兵，仅由控制面受控创建；无业务语义，不可经任何业务动作创建或推进，"
                       "仅用于验证协议绑定与读支持登记。不提供任何可执行业务动作。",
        "reference_fields": [],
    },
]


def catalog_items() -> list[dict[str, Any]]:
    items = []
    for entry in TYPE_CATALOG:
        model = PAYLOAD_MODELS.get(entry["object_type"])
        items.append({**entry,
                      "payload_schema": model.model_json_schema() if model is not None else None,
                      "versioning": _VERSIONING_NOTE})
    return items


def _invalid_cursor() -> GovernedError:
    return GovernedError("INVALID_REQUEST", "The supplied cursor is not valid for this read.", status=422)


def encode_cursor(endpoint: str, ctx: Any, filters: dict[str, Any], key: list[Any]) -> str:
    body = {"v": 1, "ep": endpoint, "scope": ctx.scope_id, "pid": ctx.principal_id,
            "f": filters, "k": key}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str | None, endpoint: str, ctx: Any, filters: dict[str, Any]) -> list[Any] | None:
    """Return the raw key list, or None for the first page; never leaks why it failed."""
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise _invalid_cursor()
    try:
        raw = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
        body = json.loads(raw)
    except (ValueError, binascii.Error, UnicodeDecodeError, UnicodeEncodeError):
        raise _invalid_cursor() from None
    if (not isinstance(body, dict) or set(body) != {"v", "ep", "scope", "pid", "f", "k"}
            or type(body["v"]) is not int or body["v"] != 1
            or not isinstance(body["ep"], str) or body["ep"] != endpoint
            or body["scope"] != ctx.scope_id or body["pid"] != ctx.principal_id
            or body["f"] != filters or not isinstance(body["k"], list)):
        raise _invalid_cursor()
    return body["k"]


def _key_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise _invalid_cursor()
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise _invalid_cursor() from None


def _key_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise _invalid_cursor()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise _invalid_cursor() from None
    if parsed.tzinfo is None:
        raise _invalid_cursor()
    return parsed


def strict_query(raw_params: Any, allowed: set[str]) -> None:
    """Reject unknown or repeated query parameters; allowlist only."""
    seen: set[str] = set()
    for key, _value in raw_params.multi_items():
        if key not in allowed or key in seen:
            raise GovernedError("INVALID_REQUEST", "The request does not satisfy the command schema.", status=422)
        seen.add(key)


def _page(fetch: Callable[[Any, int], list[Any]], keep: Callable[[Any], bool],
          keyfn: Callable[[Any], Any], limit: int, key: Any):
    """Keyset page with authorization filtering inside the pagination semantics.

    ``fetch(after_key, need)`` returns up to ``need`` raw rows strictly after
    ``after_key`` in the fixed sort order.  Rows failing ``keep`` are hidden
    without leaking their count: scanning continues until ``limit`` visible
    rows plus one extra visible row are known, or the source is exhausted.
    """
    items: list[Any] = []
    current = key
    while len(items) <= limit:
        need = limit + 1 - len(items)
        batch = fetch(current, need)
        if not batch:
            break
        current = keyfn(batch[-1])
        for row in batch:
            if keep(row):
                items.append(row)
        if len(batch) < need:
            break
    more = len(items) > limit
    return items[:limit], more


def object_types(conn: Any, ctx: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "items": catalog_items()}


def _readable_domain(conn: Any, ctx: Any, domain_id: str) -> None:
    """Uniform NOT_FOUND for missing, foreign-scope or currently unreadable domains."""
    domain_id = str(domain_id)
    row = conn.execute(
        "SELECT /*workbench:domain-check*/ domain_id FROM gov_domains WHERE scope_id=%s AND domain_id=%s",
        (ctx.scope_id, domain_id),
    ).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND")
    try:
        db.authorize_domain(conn, ctx, domain_id, "read")
    except GovernedError as exc:
        if exc.code == "FORBIDDEN":
            raise GovernedError("NOT_FOUND") from exc
        raise


def domains(conn: Any, ctx: Any, limit: int, cursor: str | None) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    raw = decode_cursor(cursor, "domains", ctx, filters)
    key = None
    if raw is not None:
        if len(raw) != 1:
            raise _invalid_cursor()
        key = [_key_uuid(raw[0])]

    def fetch(after, need):
        if after is None:
            return conn.execute(
                "SELECT /*workbench:domains*/ domain_id, name FROM gov_domains"
                " WHERE scope_id=%s ORDER BY domain_id LIMIT %s",
                (ctx.scope_id, need),
            ).fetchall()
        return conn.execute(
            "SELECT /*workbench:domains*/ domain_id, name FROM gov_domains"
            " WHERE scope_id=%s AND domain_id > %s ORDER BY domain_id LIMIT %s",
            (ctx.scope_id, after[0], need),
        ).fetchall()

    def keep(row):
        try:
            db.authorize_domain(conn, ctx, str(row["domain_id"]), "read")
        except GovernedError:
            return False
        return True

    rows, more = _page(fetch, keep, lambda row: [str(row["domain_id"])], limit, key)
    items = [{"domain_id": row["domain_id"], "name": row["name"]} for row in rows]
    next_cursor = (encode_cursor("domains", ctx, filters, [str(rows[-1]["domain_id"])])
                   if more and rows else None)
    return db.jsonable({"items": items, "next_cursor": next_cursor})


def objects(conn: Any, ctx: Any, domain_id: str, object_type: str | None,
            limit: int, cursor: str | None) -> dict[str, Any]:
    domain_id = str(domain_id)
    if object_type is not None and object_type not in KNOWN_TYPES:
        raise GovernedError("INVALID_REQUEST", "The request does not satisfy the command schema.", status=422)
    _readable_domain(conn, ctx, domain_id)
    filters = {"domain_id": domain_id, "object_type": object_type}
    raw = decode_cursor(cursor, "objects", ctx, filters)
    key = None
    if raw is not None:
        if len(raw) != 2:
            raise _invalid_cursor()
        key = [_key_timestamp(raw[0]), _key_uuid(raw[1])]

    def fetch(after, need):
        sql = ("SELECT /*workbench:objects*/ o.object_id, o.domain_id, o.object_type,"
               " o.lifecycle_status, o.object_version, o.latest_revision_id,"
               " o.effective_revision_id, o.created_at, r.payload->>'title' AS title"
               " FROM gov_objects o"
               " LEFT JOIN gov_object_revisions r"
               "   ON (r.scope_id, r.object_id, r.revision_id)"
               "    = (o.scope_id, o.object_id, o.latest_revision_id)"
               " WHERE o.scope_id=%s AND o.domain_id=%s")
        params: list[Any] = [ctx.scope_id, domain_id]
        if object_type is not None:
            sql += " AND o.object_type=%s"
            params.append(object_type)
        if after is not None:
            sql += " AND (o.created_at, o.object_id) > (%s, %s)"
            params.extend(after)
        sql += " ORDER BY o.created_at, o.object_id LIMIT %s"
        params.append(need)
        return conn.execute(sql, params).fetchall()

    rows, more = _page(fetch, lambda row: True,
                       lambda row: [row["created_at"], str(row["object_id"])], limit, key)
    keys = ("object_id", "domain_id", "object_type", "lifecycle_status", "object_version",
            "latest_revision_id", "effective_revision_id", "created_at", "title")
    # Every listed object carries its server-side protocol metadata; missing or
    # unsupported registrations are marked explicitly, never silently legacy.
    metadata = protocol.list_metadata(conn, ctx.scope_id, [str(row["object_id"]) for row in rows])
    items = [{**{field: row[field] for field in keys},
              "protocol": metadata[str(row["object_id"])]} for row in rows]
    last = rows[-1] if rows else None
    next_cursor = (encode_cursor("objects", ctx, filters,
                                 [db.jsonable(last["created_at"]), str(last["object_id"])])
                   if more and last else None)
    return db.jsonable({"items": items, "next_cursor": next_cursor})


def revisions(conn: Any, ctx: Any, object_id: str, limit: int, cursor: str | None) -> dict[str, Any]:
    head = db.object_row(conn, ctx, object_id)
    filters = {"object_id": head["object_id"]}
    raw = decode_cursor(cursor, "revisions", ctx, filters)
    key = None
    if raw is not None:
        if len(raw) != 2:
            raise _invalid_cursor()
        key = [_key_timestamp(raw[0]), _key_uuid(raw[1])]

    def fetch(after, need):
        sql = ("SELECT /*workbench:revisions*/ revision_id, object_id, object_version,"
               " payload_hash, recorded_at, valid_from, valid_to"
               " FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s")
        params: list[Any] = [ctx.scope_id, head["object_id"]]
        if after is not None:
            sql += " AND (recorded_at, revision_id) > (%s, %s)"
            params.extend(after)
        sql += " ORDER BY recorded_at, revision_id LIMIT %s"
        params.append(need)
        return conn.execute(sql, params).fetchall()

    rows, more = _page(fetch, lambda row: True,
                       lambda row: [row["recorded_at"], str(row["revision_id"])], limit, key)
    items = []
    for row in rows:
        items.append({
            "revision_id": row["revision_id"], "object_id": row["object_id"],
            "object_version": row["object_version"], "payload_hash": row["payload_hash"],
            "recorded_at": row["recorded_at"], "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "is_latest": str(row["revision_id"]) == str(head["latest_revision_id"]),
            "is_effective": str(row["revision_id"]) == str(head["effective_revision_id"]),
        })
    last = rows[-1] if rows else None
    next_cursor = (encode_cursor("revisions", ctx, filters,
                                 [db.jsonable(last["recorded_at"]), str(last["revision_id"])])
                   if more and last else None)
    return db.jsonable({"items": items, "next_cursor": next_cursor,
                        "protocol": protocol.read_metadata(conn, ctx.scope_id, head["object_id"])})


def _resolve_revision_owner(conn: Any, ctx: Any, revision_id: Any) -> str | None:
    try:
        rid = str(UUID(str(revision_id)))
    except (ValueError, TypeError, AttributeError):
        return None
    row = conn.execute(
        "SELECT /*workbench:revision-owner*/ object_id FROM gov_object_revisions"
        " WHERE scope_id=%s AND revision_id=%s",
        (ctx.scope_id, rid),
    ).fetchone()
    return str(row["object_id"]) if row else None


def relation_refs(conn: Any, ctx: Any, payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Typed one-hop source references of one exact revision payload.

    Supported fields (see docs/workbench-read-api.md): upstream_refs,
    execution_commitment_ref, feedback_ref, work_item_ref, and the
    ManagementAdjustment fields feedback_revision_id, decision_revision_id and
    changes[].from_revision_id/to_revision_id.  Revision-only references are
    resolved to their owning object inside the current scope; unresolvable
    references are skipped.  Identical references are deduplicated while the
    same object at different revisions is retained.
    """
    refs: list[tuple[str, str]] = []
    for ref in delivery.payload_references(payload):
        refs.append((str(ref["object_id"]), str(ref["revision_id"])))
    for field in ("feedback_revision_id", "decision_revision_id"):
        if payload.get(field):
            owner = _resolve_revision_owner(conn, ctx, payload[field])
            if owner:
                refs.append((owner, str(UUID(str(payload[field])))))
    changes = payload.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, dict):
                continue
            for field in ("from_revision_id", "to_revision_id"):
                if change.get(field):
                    owner = _resolve_revision_owner(conn, ctx, change[field])
                    if owner:
                        refs.append((owner, str(UUID(str(change[field])))))
    return list(dict.fromkeys(refs))


def relations(conn: Any, ctx: Any, object_id: str, revision_id: str | None,
              limit: int, cursor: str | None) -> dict[str, Any]:
    head = db.object_row(conn, ctx, object_id)
    head_protocol = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    if head_protocol["interpretation_status"] not in ("legacy_v0_2", "contract_a_metadata_read_only"):
        raise GovernedError("PROTOCOL_NOT_SUPPORTED",
                            "The object's protocol registration does not support relation reads.", status=409)
    if revision_id is None:
        revision_id = head["latest_revision_id"]
    source = db.revision_row(conn, ctx, head["object_id"], str(revision_id))
    source_ref = {"object_id": head["object_id"], "revision_id": str(source["revision_id"])}
    filters = dict(source_ref)
    raw = decode_cursor(cursor, "relations", ctx, filters)
    key = None
    if raw is not None:
        if len(raw) != 2:
            raise _invalid_cursor()
        key = [_key_uuid(raw[0]), _key_uuid(raw[1])]

    edges = sorted(set(relation_refs(conn, ctx, source["payload"])))
    if key is not None:
        edges = [edge for edge in edges if list(edge) > key]

    visible = []
    for target_object, target_revision in edges:
        try:
            target = db.object_row(conn, ctx, target_object)
            db.revision_row(conn, ctx, target_object, target_revision)
            protocol.require_read_support(conn, ctx.scope_id, target_object)
        except GovernedError:
            continue  # an unreadable or read-unsupported target hides the whole edge, including its count
        visible.append({
            "relation_type": "source_reference",
            "source_ref": source_ref,
            "target_ref": {"object_id": target_object, "revision_id": target_revision},
            "target_type": target["object_type"],
        })
    more = len(visible) > limit
    items = visible[:limit]
    next_cursor = None
    if more and items:
        last = items[-1]["target_ref"]
        next_cursor = encode_cursor("relations", ctx, filters, [last["object_id"], last["revision_id"]])
    return db.jsonable({"source_ref": source_ref, "items": items, "next_cursor": next_cursor,
                        "protocol": head_protocol})


def action_receipts(conn: Any, ctx: Any, object_id: str, limit: int, cursor: str | None) -> dict[str, Any]:
    head = db.object_row(conn, ctx, object_id)
    filters = {"object_id": head["object_id"]}
    raw = decode_cursor(cursor, "action-receipts", ctx, filters)
    key = None
    if raw is not None:
        if len(raw) != 2:
            raise _invalid_cursor()
        key = [_key_timestamp(raw[0]), _key_uuid(raw[1])]

    def fetch(after, need):
        sql = ("SELECT /*workbench:action-receipts*/ receipt_id, action_type, principal_id,"
               " status, recorded_at, result, object_versions, target_object_id"
               " FROM gov_action_receipts"
               " WHERE scope_id=%s AND (target_object_id=%s"
               "   OR object_versions @> %s::jsonb"
               "   OR COALESCE(result->'referenced_object_ids', '[]'::jsonb) @> %s::jsonb)")
        params: list[Any] = [ctx.scope_id, head["object_id"],
                             json.dumps([{"object_id": head["object_id"]}]),
                             json.dumps([head["object_id"]])]
        if after is not None:
            sql += " AND (recorded_at, receipt_id) > (%s, %s)"
            params.extend(after)
        sql += " ORDER BY recorded_at, receipt_id LIMIT %s"
        params.append(need)
        return conn.execute(sql, params).fetchall()

    def keep(row):
        try:
            readers.authorize_receipt(conn, ctx, db.jsonable(dict(row)))
        except GovernedError:
            return False  # any currently unreadable reference hides the whole receipt
        return True

    rows, more = _page(fetch, keep,
                       lambda row: [row["recorded_at"], str(row["receipt_id"])], limit, key)
    items = [{"receipt_id": row["receipt_id"], "action_type": row["action_type"],
              "actor_id": row["principal_id"], "status": row["status"],
              "recorded_at": row["recorded_at"]} for row in rows]
    last = rows[-1] if rows else None
    next_cursor = (encode_cursor("action-receipts", ctx, filters,
                                 [db.jsonable(last["recorded_at"]), str(last["receipt_id"])])
                   if more and last else None)
    return db.jsonable({"items": items, "next_cursor": next_cursor,
                        "protocol": protocol.read_metadata(conn, ctx.scope_id, head["object_id"])})


def responsibility(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    """Resolve the frozen WorkItem assignment references to minimal identity projections."""
    head = db.object_row(conn, ctx, object_id)
    if head["object_type"] != "WorkItem":
        raise GovernedError("INVALID_REQUEST", "The request does not satisfy the command schema.", status=422)
    # The DRI/acceptor projection is legacy v0.2 semantics: an object bound to
    # another protocol (or lacking read support) never gets the old MISSION_DRI
    # reading of these fields.
    metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    if metadata["interpretation_status"] != "legacy_v0_2":
        raise GovernedError("PROTOCOL_NOT_SUPPORTED",
                            "The legacy responsibility projection is not supported for this object's "
                            "protocol registration.", status=409)
    state = conn.execute(
        "SELECT /*workbench:responsibility*/ work_item_revision_id, dri_assignment_id,"
        " acceptor_assignment_id FROM gov_work_item_state WHERE scope_id=%s AND object_id=%s",
        (ctx.scope_id, head["object_id"]),
    ).fetchone()
    if state is None:
        raise GovernedError("INVALID_STATE", "WorkItem processing state is absent.", status=409)

    def project(assignment_id: Any) -> dict[str, Any]:
        row = conn.execute(
            "SELECT /*workbench:assignment*/ a.assignment_id, a.principal_id, a.role,"
            " a.domain_id, p.display_name,"
            " (p.active AND a.active AND a.valid_from <= clock_timestamp()"
            "  AND (a.valid_to IS NULL OR a.valid_to > clock_timestamp()))"
            "   AS current_assignment_active"
            " FROM gov_role_assignments a"
            " JOIN gov_principals p ON (p.scope_id, p.principal_id) = (a.scope_id, a.principal_id)"
            " WHERE a.scope_id=%s AND a.assignment_id=%s",
            (ctx.scope_id, str(assignment_id)),
        ).fetchone()
        if row is None:
            raise GovernedError("NOT_FOUND")
        return {field: row[field] for field in ("assignment_id", "principal_id", "display_name",
                                                "role", "domain_id", "current_assignment_active")}

    return db.jsonable({
        "object_id": head["object_id"],
        "baseline_revision_id": state["work_item_revision_id"],
        "dri": project(state["dri_assignment_id"]),
        "acceptor": project(state["acceptor_assignment_id"]),
        "protocol": metadata,
    })


__all__ = ["SCHEMA_VERSION", "KNOWN_TYPES", "object_types", "domains", "objects", "revisions",
           "relations", "action_receipts", "responsibility", "relation_refs",
           "encode_cursor", "decode_cursor", "strict_query", "catalog_items"]
