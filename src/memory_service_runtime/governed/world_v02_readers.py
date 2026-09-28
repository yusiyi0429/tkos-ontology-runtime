"""tkos.world/0.2 的读投影与回执授权。

读权限同 0.1（契约第 15.1 节）：scope 内有任一生效角色指派的责任主体可读该 scope 的全部
world 对象，不走域级 read 策略；非 world 对象一律 NOT_FOUND，授权先于任何协议错误。
读投影按三层分组：``business``、``identity``、``records``。``business`` 给块、组件、组件台账与关系，
引用同时给钉定结构与业务形式；``records`` 给最新状态快照（标明未经确认）。生命周期、一轮、委托、复盘与问题
随各自的票接入，在此之前给空值。状态快照是时间记录，按 id 读回的是快照视图，不分三组。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from . import db, protocol
from . import world_v02_registry as world_registry
from .errors import GovernedError
from .world_v01_models import utc_text
from .world_v02_models import CONTRACT_VERSION, citation, cite, payload_spec

# 空块的标准句在投影层配置（契约第 4 节）。
EMPTY_BLOCK_SENTENCE = "当前没有{name}"


def head_and_binding(conn: Any, ctx: Any, object_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """本 scope 的 world 对象行与它当前的绑定；调用者没有任何生效指派是 403，找不到或不是 world 对象是 404。"""
    db._assignments(conn, ctx)
    object_id = db._uuid(object_id)
    row = conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                       (ctx.scope_id, object_id)).fetchone()
    binding = protocol.current_binding(conn, ctx.scope_id, object_id) if row is not None else None
    if row is None or binding is None or binding["protocol_id"] != "tkos.world":
        raise GovernedError("NOT_FOUND")
    return db.jsonable(row), binding


def bound_contract(conn: Any, ctx: Any, object_id: str) -> str:
    """读端点按对象绑定的契约版本分派；判权与找不到的规则同取对象。"""
    return head_and_binding(conn, ctx, object_id)[1]["contract_version"]


def readable(conn: Any, ctx: Any, object_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    head, binding = head_and_binding(conn, ctx, object_id)
    metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    if binding["contract_version"] != CONTRACT_VERSION or metadata["interpretation_status"] != "world_v0_2":
        raise GovernedError("PROTOCOL_NOT_SUPPORTED", "The object is not read under tkos.world/0.2.", status=409)
    return head, metadata


def cited(pinned: Any) -> Any:
    """钉定引用读回时同时给出钉定结构与业务形式（契约第 5 节）；列表逐项处理，空值原样。"""
    if isinstance(pinned, list):
        return [cited(item) for item in pinned]
    if pinned is None:
        return None
    return {**pinned, "ref": cite(pinned)}


def block_view(object_id: str, version: int, spec: dict[str, Any], value: dict[str, Any] | None) -> dict[str, Any]:
    """一个块读回的样子：带块类别；块内与组件里的引用同时给出两种形式，组件另给自己的组件引用；
    空块渲染标准句（契约第 3.2、4、5 节）。"""
    components = [{**item, "scope": cited(item["scope"]), "refs": cited(item["refs"]),
                   "ref": citation(object_id, version, spec["id"], item["id"])}
                  for item in (value["components"] if value is not None else [])]
    return {"id": spec["id"], "display_name": spec["display_name"], "kind": spec["kind"], "class": spec["class"],
            "value": value and {**value, "components": components, "refs": cited(value["refs"])},
            "empty": value is None,
            "text": EMPTY_BLOCK_SENTENCE.format(name=spec["display_name"]) if value is None else value["text"],
            "components": components, "ref": citation(object_id, version, spec["id"])}


def responsible_principals(conn: Any, ctx: Any, head: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """责任人（契约第 3.3 节）：按角色解析的是当前在对象所在域持该角色、启用的人；按属性解析的是
    responsible 属性上的身份，还没指派时为空。"""
    rule = world_registry.object_spec(head["object_type"])["responsible"]
    if rule["source"] == "attribute":
        if payload.get("responsible") is None:
            return []
        return [db.jsonable(row) for row in conn.execute(
            """SELECT principal_id, principal_type, display_name FROM gov_principals
                WHERE scope_id=%s AND principal_id=%s""", (ctx.scope_id, payload["responsible"])).fetchall()]
    return [db.jsonable(row) for row in conn.execute(
        """SELECT DISTINCT p.principal_id, p.principal_type, p.display_name
             FROM gov_role_assignments a JOIN gov_principals p
               ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
            WHERE a.scope_id=%s AND a.domain_id=%s AND a.role=%s AND a.active AND p.active
              AND p.principal_type='human'
              AND a.valid_from<=clock_timestamp() AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to)
            ORDER BY p.principal_id""",
        (ctx.scope_id, head["domain_id"], rule["role"])).fetchall()]


def object_view(head: dict[str, Any], revision: dict[str, Any], metadata: dict[str, Any], *,
                responsible: list[dict[str, Any]], latest_state: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = world_registry.object_spec(head["object_type"])
    payload, version = revision["payload"], revision["object_version"]
    category = world_registry.category(spec["category"])
    business = {
        "object_id": head["object_id"], "object_type": head["object_type"], "type_display_name": spec["display_name"],
        "category": {"id": category["id"], "display_name": category["display_name"]}, "candidate": spec["candidate"],
        "version": version, "revision_id": revision["revision_id"],
        # 对象行的并发版本：之后修订时作为 target.expected_version，与修订序号 version 不必相等。
        "object_version": head["object_version"], "title": payload["title"],
        "attributes": {attribute["id"]: payload.get(attribute["id"]) for attribute in spec["attributes"]
                       if attribute["id"] != "title"},
        "relations": [{"field": field["field"], "relation": field["relation"], "value": cited(payload.get(field["field"]))}
                      for field in spec["relation_fields"]],
        "blocks": [block_view(head["object_id"], version, block, payload["blocks"][block["id"]])
                   for block in spec["blocks"]],
        "component_ledger": payload.get("component_ledger", []),
        "formal": {"lifecycle_status": head["lifecycle_status"], "effective_revision_id": head["effective_revision_id"]},
        "round": None,
    }
    identity = {"responsible": {**spec["responsible"], "principals": responsible}, "delegations": []}
    # 生命周期读回随票 #53、#54。
    records = {"lifecycle": None, "latest_state": latest_state, "confirmed_review": None, "open_issues": []}
    return {"object_id": head["object_id"], "business": business, "identity": identity, "records": records,
            "protocol": metadata}


def read_object(conn: Any, ctx: Any, object_id: str, version: int | None = None) -> dict[str, Any]:
    """取对象：默认最新修订，给 version 取该修订序号。业务对象附最新状态快照；状态快照读回快照视图。"""
    head, metadata = readable(conn, ctx, object_id)
    if version is None:
        row = conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                           (ctx.scope_id, head["latest_revision_id"])).fetchone()
    else:
        row = conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND object_version=%s",
                           (ctx.scope_id, head["object_id"], version)).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND")
    revision = db.jsonable(row)
    if head["object_type"] == "StateSnapshot":
        return {**snapshot_view(head, revision, generator=principal(conn, ctx, revision["payload"]["generator"])),
                "protocol": metadata}
    return object_view(head, revision, metadata, responsible=responsible_principals(conn, ctx, head, revision["payload"]),
                       latest_state=latest_snapshot(conn, ctx, head["object_id"]))


# ------------------------------------------------------------ state snapshots
def principal(conn: Any, ctx: Any, principal_id: str) -> dict[str, Any]:
    return db.jsonable(conn.execute(
        "SELECT principal_id, principal_type, display_name FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
        (ctx.scope_id, principal_id)).fetchone())


def snapshot_view(head: dict[str, Any], revision: dict[str, Any], *, generator: dict[str, Any]) -> dict[str, Any]:
    """一条状态快照读回的样子（契约第 7 节）：统一外壳、payload 类型与它的块，引用两种形式都给；
    快照写入即生效、无人确认，读侧标明未经确认。"""
    payload, version = revision["payload"], revision["object_version"]
    spec = payload_spec(payload["payload_type"])
    category = world_registry.category("time_record")
    return {"object_id": head["object_id"], "object_type": "StateSnapshot",
            "category": {"id": category["id"], "display_name": category["display_name"]},
            "version": version, "revision_id": revision["revision_id"], "ref": citation(head["object_id"], version),
            "title": payload["title"], "subject_ref": cited(payload["subject_ref"]), "as_of": payload["as_of"],
            "period": payload["period"], "generator": generator,
            "source_event_refs": cited(payload["source_event_refs"]),
            "payload_type": {"id": spec["id"], "display_name": spec["display_name"]},
            "blocks": [block_view(head["object_id"], version, block, payload["blocks"][block["id"]])
                       for block in spec["blocks"]],
            "unconfirmed": True}


# 本 scope 里以某对象为主体的 0.2 状态快照（主体引用带版本号的才是 world 快照，同迁移 0030 的唯一索引）。
SNAPSHOTS_OF_SUBJECT = """r.scope_id=%s AND o.object_type='StateSnapshot'
      AND r.payload->'subject_ref' ? 'object_version' AND r.payload->'subject_ref'->>'object_id'=%s"""


def latest_snapshot(conn: Any, ctx: Any, subject_id: str, as_of: Any = None) -> dict[str, Any] | None:
    """主体 as_of 不晚于该时点（不给即最新）的那条状态快照。"""
    row = conn.execute(
        """SELECT r.*, o.domain_id FROM gov_object_revisions r
             JOIN gov_objects o ON o.scope_id=r.scope_id AND o.object_id=r.object_id
            WHERE """ + SNAPSHOTS_OF_SUBJECT + """
              AND (%s::timestamptz IS NULL OR (r.payload->>'as_of')::timestamptz <= %s::timestamptz)
            ORDER BY (r.payload->>'as_of')::timestamptz DESC LIMIT 1""",
        (ctx.scope_id, subject_id, as_of, as_of)).fetchone()
    if row is None:
        return None
    revision = db.jsonable(row)
    return snapshot_view(revision, revision, generator=principal(conn, ctx, revision["payload"]["generator"]))


def state(conn: Any, ctx: Any, object_id: str, as_of: Any = None) -> dict[str, Any]:
    """取状态：按主体与时点（契约第 15.1 节）。"""
    head, _ = readable(conn, ctx, object_id)
    return {"object_id": head["object_id"], "as_of": as_of and utc_text(as_of.isoformat()),
            "snapshot": latest_snapshot(conn, ctx, head["object_id"], as_of)}


# ------------------------------------------------------------ events
def event_view(row: dict[str, Any], *, corrected_by: list[str], withdrawn_by: list[str]) -> dict[str, Any]:
    """一条事件读回的样子（契约第 8.1 节）：类、记录者、代记信息、迟记、被更正与被撤回的关系、产生它的动作；
    时刻为 UTC 规范文本，引用两种形式都给。"""
    kind = next(item for item in world_registry.registry()["event_kinds"] if item["kind"] == row["kind"])
    content = row["content"]
    return {"event_id": row["event_id"], "scope_id": row["scope_id"], "kind": row["kind"], "class": kind["class"],
            "category": row["category"], "outcome": row["outcome"], "disposition": row["disposition"],
            "subject_refs": cited(row["subject_refs"]),
            "principal": {"principal_id": row["principal_id"], "principal_type": row["principal_type"],
                          "display_name": row["principal_name"]},
            "on_behalf_of": row["on_behalf_of"] and {"principal_id": row["on_behalf_of"],
                                                     "display_name": row["on_behalf_of_name"]},
            "external_confirmation": row["external_record_id"] and {
                "external_record_id": row["external_record_id"],
                "external_confirmed_at": utc_text(row["external_confirmed_at"])},
            "occurred_at": utc_text(row["occurred_at"]), "recorded_at": utc_text(row["recorded_at"]),
            "late": row["late"],
            "content": content and {**content, "refs": cited(content.get("refs", []))},
            "detail": row["detail"], "action": row["action"], "action_id": row["action_id"],
            "supersedes_event_id": row["supersedes_event_id"],
            "corrected_by": corrected_by, "withdrawn_by": withdrawn_by}


def events(conn: Any, ctx: Any, object_id: str, since: Any = None) -> dict[str, Any]:
    """取事件（契约第 11、15.1 节）：subject_refs 含该对象或其组件、发生时刻不早于起始时间的事件，按发生时刻
    升序。迟记：这条事件记下时，同一主体已有发生时刻晚于它的事件（不看起始时间）。被更正、被撤回的事件
    列出更正、撤回它的事件。"""
    head, _ = readable(conn, ctx, object_id)
    probe = Jsonb([{"object_id": head["object_id"]}])
    rows = [db.jsonable(row) for row in conn.execute(
        """SELECT e.*, r.action_type AS action, p.principal_type, p.display_name AS principal_name,
                  b.display_name AS on_behalf_of_name,
                  EXISTS (SELECT 1 FROM gov_world_events x
                           WHERE x.scope_id=e.scope_id AND x.subject_refs @> %s
                             AND x.recorded_at < e.recorded_at AND x.occurred_at > e.occurred_at) AS late
             FROM gov_world_events e
             JOIN gov_action_receipts r ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id
             JOIN gov_principals p ON p.scope_id=e.scope_id AND p.principal_id=e.principal_id
             LEFT JOIN gov_principals b ON b.scope_id=e.scope_id AND b.principal_id=e.on_behalf_of
            WHERE e.scope_id=%s AND e.subject_refs @> %s
              AND (%s::timestamptz IS NULL OR e.occurred_at >= %s::timestamptz)
            ORDER BY e.occurred_at, e.recorded_at, e.event_id""",
        (probe, ctx.scope_id, probe, since, since)).fetchall()]
    later: dict[str, dict[str, list[str]]] = {row["event_id"]: {"corrected_by": [], "withdrawn_by": []} for row in rows}
    for row in conn.execute(
            """SELECT supersedes_event_id, event_id, category FROM gov_world_events
                WHERE scope_id=%s AND supersedes_event_id = ANY(%s::uuid[]) ORDER BY recorded_at, event_id""",
            (ctx.scope_id, list(later))).fetchall():
        relation = "corrected_by" if row["category"] == "correction" else "withdrawn_by"
        later[str(row["supersedes_event_id"])][relation].append(str(row["event_id"]))
    return {"object_id": head["object_id"], "since": since and utc_text(since.isoformat()),
            "events": [event_view(row, **later[row["event_id"]]) for row in rows]}


def is_receipt(row: dict[str, Any]) -> bool:
    """0.2 的回执在结果里写明契约版本；0.1 与 0.2 的动作同名，只按动作名分不开。"""
    return (row.get("result") or {}).get("contract_version") == CONTRACT_VERSION


def authorize_receipt(conn: Any, ctx: Any, row: dict[str, Any], *, replay: bool = False) -> None:
    """读 0.2 回执按 world 读规则：调用者在 scope 内有生效指派，回执涉及的对象仍是 world 对象。

    重放是再次执行成功，另按当前权限复核：必须是原调用者，动作当时用到的指派仍然有效，
    且调用者在该域仍有这个动作的角色；经某对象的 responsible 属性成为责任人的，该属性仍须指向他。
    """
    ids = {str(item["object_id"]) for item in row["object_versions"]}
    ids.update(str(item["object_id"]) for item in row["result"].get("subject_refs", []))
    if row["target_object_id"]:
        ids.add(str(row["target_object_id"]))
    for object_id in ids:
        head_and_binding(conn, ctx, object_id)
    if not replay:
        return
    if str(row["principal_id"]) != ctx.principal_id:
        raise GovernedError("FORBIDDEN")
    current = {item["assignment_id"] for item in db._assignments(conn, ctx)}
    if any(aid not in current for aid in row["result"].get("required_assignment_ids", [])):
        raise GovernedError("FORBIDDEN", "An assignment this command relied on is no longer valid.")
    if world_registry.action_spec(row["action_type"])["authorization"] != "scope":  # 外部事件按 scope 判权
        db.authorize_domain(conn, ctx, row["result"]["domain_id"], row["action_type"])
    through = row["result"].get("responsible_through")
    if through is not None and db.jsonable(conn.execute(
            """SELECT r.payload->>'responsible' AS responsible FROM gov_objects o JOIN gov_object_revisions r
                 ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                WHERE o.scope_id=%s AND o.object_id=%s""", (ctx.scope_id, through)).fetchone())["responsible"] != ctx.principal_id:
        raise GovernedError("FORBIDDEN", "The responsibility this command relied on has moved to someone else.")
