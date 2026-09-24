"""tkos.world/0.1 的读投影与回执授权。

读权限按契约第 12 节：scope 内有任一生效角色指派的责任主体可读该 scope 的全部
world 对象，不走域级 read 策略。非 world 对象经 world 端点一律 NOT_FOUND，不透露
其存在；授权先于任何协议错误。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from . import db, protocol
from . import world_v01_registry as world_registry
from .errors import GovernedError
from .world_v01_models import ACTION_PARAMS, SCOPE_ACTIONS, citation, utc_text

# 空块的标准句在投影层配置（契约第 3 节）。
EMPTY_BLOCK_SENTENCE = "当前没有{name}"
# 按主体找状态快照的条件，与迁移 0030 的唯一索引 ux_gov_world_snapshot_subject_as_of 的谓词一致。
SNAPSHOTS_OF_SUBJECT = ("scope_id=%s AND payload->'subject_ref' ? 'object_version' AND payload ? 'as_of' "
                        "AND payload->'subject_ref'->>'object_id'=%s")


def cited(pinned: Any) -> Any:
    """钉定引用读回时同时给出结构化四项与业务形式（契约第 5 节）；列表逐项处理，空值原样。"""
    if isinstance(pinned, list):
        return [cited(item) for item in pinned]
    if pinned is None:
        return None
    return {**pinned, "ref": citation(pinned["object_id"], pinned["object_version"], pinned["block"])}


def object_view(head: dict[str, Any], revision: dict[str, Any], metadata: dict[str, Any],
                supersedes: dict[str, Any] | None = None,
                referenced_by: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    spec = world_registry.object_spec(head["object_type"])
    payload, version = revision["payload"], revision["object_version"]
    blocks = []
    for block in spec["blocks"]:
        value = payload["blocks"][block["id"]]
        blocks.append({"id": block["id"], "display_name": block["display_name"], "kind": block["kind"],
                       "value": value and {**value, "refs": cited(value["refs"])}, "empty": value is None,
                       "text": EMPTY_BLOCK_SENTENCE.format(name=block["display_name"]) if value is None else value["text"],
                       "ref": citation(head["object_id"], version, block["id"])})
    relations = [{"field": field["field"], "relation": field["relation"], "value": cited(payload.get(field["field"]))}
                 for field in spec["relation_fields"]]
    attributes = {attribute["id"]: cited(payload.get(attribute["id"])) if attribute["value"] == "ref"
                  else payload.get(attribute["id"]) for attribute in spec["attributes"] if attribute["id"] != "title"}
    return {"object_id": head["object_id"], "object_type": head["object_type"],
            "type_display_name": spec["display_name"], "version": version, "revision_id": revision["revision_id"],
            "title": payload["title"], "attributes": attributes, "blocks": blocks, "relations": relations,
            "referenced_by": referenced_by or [], "supersedes": cited(supersedes),
            # 对象行的并发版本：修订、建关系时作为 target.expected_version，与修订序号 version 不必相等。
            "object_version": head["object_version"],
            "formal": {"lifecycle_status": head["lifecycle_status"],
                       "effective_revision_id": head["effective_revision_id"]},
            "protocol": metadata}


def world_head(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    db._assignments(conn, ctx)  # 没有任何生效指派的调用者是 403
    object_id = db._uuid(object_id)
    row = conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                       (ctx.scope_id, object_id)).fetchone()
    binding = protocol.current_binding(conn, ctx.scope_id, object_id) if row is not None else None
    if row is None or binding is None or binding["protocol_id"] != "tkos.world":
        raise GovernedError("NOT_FOUND")
    return db.jsonable(row)


def _readable(conn: Any, ctx: Any, object_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    head = world_head(conn, ctx, object_id)
    # 共享的读支持闸门服务于其他协议的读取器，不接受 world；world 在这里自己核对解释状态。
    metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    if metadata["interpretation_status"] != "world_v0_1":
        raise GovernedError("PROTOCOL_NOT_SUPPORTED", "The object is not read under tkos.world/0.1.", status=409)
    return head, metadata


def _revision(conn: Any, ctx: Any, object_id: str, version: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND object_version=%s",
                       (ctx.scope_id, object_id, version)).fetchone()
    return db.jsonable(row) if row is not None else None


def read_object(conn: Any, ctx: Any, object_id: str, version: int | None = None) -> dict[str, Any]:
    """取对象：默认最新修订，给 version 取该修订序号；supersedes 指向它取代的上一版本（修订链），
    referenced_by 列出指向它的跨链关系（契约第 6 节）。修订序号从 1 连续递增。"""
    head, metadata = _readable(conn, ctx, object_id)
    if version is None:
        revision = db.jsonable(conn.execute(
            "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
            (ctx.scope_id, head["latest_revision_id"])).fetchone())
    else:
        revision = _revision(conn, ctx, head["object_id"], version)
    if revision is None:
        raise GovernedError("NOT_FOUND")
    previous = _revision(conn, ctx, head["object_id"], revision["object_version"] - 1)
    supersedes = previous and {"object_id": head["object_id"], "object_version": previous["object_version"],
                               "revision_id": previous["revision_id"], "block": None}
    view = object_view(head, revision, metadata, supersedes, _referenced_by(conn, ctx, head["object_id"]))
    if head["object_type"] == "StateSnapshot":
        view["unconfirmed"] = True  # 读侧展示快照都标明它未经确认（契约第 7 节）
    else:
        view["state"] = _snapshot(conn, ctx, head["object_id"])
    return view


def _snapshot(conn: Any, ctx: Any, subject_id: str, as_of: Any = None) -> dict[str, Any] | None:
    """主体的 as_of 不晚于该时点（不给即最新）的那条状态快照，标明未经确认（契约第 7 节）。"""
    row = conn.execute(
        "SELECT object_id FROM gov_object_revisions WHERE " + SNAPSHOTS_OF_SUBJECT + """
              AND (%s::timestamptz IS NULL OR (payload->>'as_of')::timestamptz <= %s::timestamptz)
            ORDER BY (payload->>'as_of')::timestamptz DESC LIMIT 1""",
        (ctx.scope_id, subject_id, as_of, as_of)).fetchone()
    return row and read_object(conn, ctx, str(row["object_id"]))


def state(conn: Any, ctx: Any, object_id: str, as_of: Any = None) -> dict[str, Any]:
    """取状态：按主体与时点。"""
    head, _ = _readable(conn, ctx, object_id)
    return {"object_id": head["object_id"], "as_of": as_of and utc_text(as_of.isoformat()),
            "snapshot": _snapshot(conn, ctx, head["object_id"], as_of)}


def events(conn: Any, ctx: Any, object_id: str, since: Any = None) -> dict[str, Any]:
    """取事件：subject_refs 含该对象、occurred_at 不早于起始时间的全部事件，按 occurred_at 升序；
    被更正的事件列出更正它的事件（契约第 12 节）。"""
    head, _ = _readable(conn, ctx, object_id)
    rows = [db.jsonable(row) for row in conn.execute(
        """SELECT * FROM gov_world_events
            WHERE scope_id=%s AND subject_refs @> %s AND (%s::timestamptz IS NULL OR occurred_at >= %s::timestamptz)
            ORDER BY occurred_at, recorded_at, event_id""",
        (ctx.scope_id, Jsonb([{"object_id": head["object_id"]}]), since, since)).fetchall()]
    corrected_by: dict[str, list[str]] = {}
    for row in conn.execute(
            """SELECT supersedes_event_id, event_id FROM gov_world_events
                WHERE scope_id=%s AND category='correction' AND supersedes_event_id = ANY(%s::uuid[])
                ORDER BY recorded_at, event_id""",
            (ctx.scope_id, [row["event_id"] for row in rows])).fetchall():
        corrected_by.setdefault(str(row["supersedes_event_id"]), []).append(str(row["event_id"]))
    return {"object_id": head["object_id"], "since": since and utc_text(since.isoformat()),
            "events": [{**{key: row[key] for key in ("event_id", "kind", "phase", "category", "outcome", "principal_id",
                                                      "action_id", "supersedes_event_id")},
                        "occurred_at": utc_text(row["occurred_at"]), "recorded_at": utc_text(row["recorded_at"]),
                        "subject_refs": cited(row["subject_refs"]),
                        "content": row["content"] and {**row["content"], "refs": cited(row["content"]["refs"])},
                        "corrected_by": corrected_by.get(row["event_id"], [])} for row in rows]}


def _referenced_by(conn: Any, ctx: Any, object_id: str) -> list[dict[str, Any]]:
    """最新修订的跨链关系列表里钉着该对象（任一版本）的 world 对象，逐条列出。"""
    fields = {field["field"]: field["relation"] for item in world_registry.registry()["objects"]
              for field in item["relation_fields"] if field["written_by"] == "world_relate"}
    probe = Jsonb([{"object_id": object_id}])
    rows = conn.execute(
        """SELECT o.object_id, r.object_version, r.revision_id, r.payload
             FROM gov_object_revisions r JOIN gov_objects o
               ON o.scope_id=r.scope_id AND o.object_id=r.object_id AND o.latest_revision_id=r.revision_id
            WHERE r.scope_id=%s AND (""" + " OR ".join(["r.payload->%s @> %s"] * len(fields)) + """)
            ORDER BY o.created_at, o.object_id""",
        (ctx.scope_id, *[value for field in fields for value in (field, probe)])).fetchall()
    found = []
    for row in map(db.jsonable, rows):
        source = {"object_id": row["object_id"], "object_version": row["object_version"],
                  "revision_id": row["revision_id"], "block": None}
        found.extend({"field": field, "relation": relation, "source": cited(source), "target": cited(pin)}
                     for field, relation in fields.items() for pin in row["payload"].get(field, [])
                     if pin["object_id"] == object_id)
    return found


def children(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    """反向查找：最新修订的 parent_ref 指向该对象（任一版本）的 world 对象。"""
    head, _ = _readable(conn, ctx, object_id)
    rows = conn.execute(
        """SELECT o.object_id, o.object_type, r.object_version, r.revision_id, r.payload
             FROM gov_object_revisions r JOIN gov_objects o
               ON o.scope_id=r.scope_id AND o.object_id=r.object_id AND o.latest_revision_id=r.revision_id
            WHERE r.scope_id=%s AND r.payload ? 'parent_ref' AND r.payload->'parent_ref' ? 'object_version'
              AND r.payload->'parent_ref'->>'object_id'=%s
            ORDER BY o.created_at, o.object_id""", (ctx.scope_id, head["object_id"])).fetchall()
    return {"object_id": head["object_id"], "children": [
        {"object_id": row["object_id"], "object_type": row["object_type"], "title": row["payload"]["title"],
         "version": row["object_version"], "revision_id": row["revision_id"],
         "ref": citation(row["object_id"], row["object_version"]), "parent_ref": cited(row["payload"]["parent_ref"])}
        for row in map(db.jsonable, rows)]}


def is_receipt(row: dict[str, Any]) -> bool:
    return row["action_type"] in ACTION_PARAMS


def authorize_receipt(conn: Any, ctx: Any, row: dict[str, Any], *, replay: bool = False) -> None:
    """读 world 回执按 world 读规则：调用者在 scope 内有生效指派，回执涉及的对象仍是 world 对象。

    重放是再次执行成功，另按当前权限复核：必须是原调用者，动作当时用到的指派仍然有效，
    且调用者在该域仍有这个动作的角色（按 scope 判权的外部事件只要仍在 scope 内）；
    scope 成员身份或无关角色不能复活被撤销的命令。
    """
    ids = {str(item["object_id"]) for item in row["object_versions"]}
    ids.update(str(item["object_id"]) for item in row["result"].get("subject_refs", []))
    if row["target_object_id"]:
        ids.add(str(row["target_object_id"]))
    for object_id in ids:
        world_head(conn, ctx, object_id)
    if not replay:
        return
    if str(row["principal_id"]) != ctx.principal_id:
        raise GovernedError("FORBIDDEN")
    current = {item["assignment_id"] for item in db._assignments(conn, ctx)}
    if any(aid not in current for aid in row["result"].get("required_assignment_ids", [])):
        raise GovernedError("FORBIDDEN", "An assignment this command relied on is no longer valid.")
    if row["action_type"] not in SCOPE_ACTIONS:  # 外部事件按 scope 判权，不看各域策略
        db.authorize_domain(conn, ctx, row["result"]["domain_id"], row["action_type"])
