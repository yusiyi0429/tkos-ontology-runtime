"""tkos.world/0.2 的读侧读 0.1 对象（票 #63，契约第 15.4 节）。

读端点对 0.1 对象默认仍给 0.1 的形状（world_v01_readers，一行不改）；请求带 ``view=tkos.world/0.2`` 时才走这里：
内容按 0.1 契约与登记解释——类型、块、属性与关系按 0.1 登记，生命周期按 0.1 的状态机、推出它的是 0.1 的事件——输出按
0.2 读投影的三组（第 15.1 节），键与 0.2 对象的相同。0.1 的引用读成 0.2 的对象或块形式：钉定结构补上 ``component: null``，
另给业务形式。类别按类型名取 0.2 登记的三层（0.1 的九种类型在 0.2 里同名）。0.1 没有的给空值：块类别为 null，没有组件、
组件台账与委托，不是候选类型；0.1 的读侧不投影进行中的一轮，这里同样给 null；没有已确认复盘与未处置的问题。

0.1 快照按只读的 ``legacy_0_1`` payload 给出（登记 ``state.legacy_read``：progress、issue、artifacts 三块），外壳同 0.2 的
快照视图：生成者是记下它的人（0.1 登记里快照的责任人是写入者），0.1 没有来源事件，给空列表；读侧同样标明未经确认。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from . import db
from . import world_v01_readers as v01
from . import world_v01_registry as v01_registry
from . import world_v02_registry as world_registry
from .errors import GovernedError
from .world_v01_models import utc_text
from .world_v02_models import citation
from .world_v02_readers import block_view, cited, principal, responsible_principals

VIEW = "tkos.world/0.2"
LEGACY_DISPLAY_NAME = "0.1 状态快照（只读）"


def pinned(value: Any) -> Any:
    """0.1 的钉定引用（对象或块）→ 0.2 的钉定结构：补上 component，列表逐项处理，空值原样。"""
    if isinstance(value, list):
        return [pinned(item) for item in value]
    return value and {**value, "component": None}


def blocks(object_id: str, version: int, specs: list[dict[str, Any]], stored: dict[str, Any]) -> list[dict[str, Any]]:
    """0.1 的块按 0.2 的块视图读：块类别为 null，块值不带组件（0.1 没有组件），块内引用读成 0.2 形式。"""
    return [block_view(object_id, version, {**spec, "class": None},
                       stored[spec["id"]] and {**stored[spec["id"]], "components": [],
                                               "refs": pinned(stored[spec["id"]]["refs"])})
            for spec in specs]


def category(object_type: str) -> dict[str, Any]:
    found = world_registry.category(world_registry.object_spec(object_type)["category"])
    return {"id": found["id"], "display_name": found["display_name"]}


def object_view(head: dict[str, Any], revision: dict[str, Any], metadata: dict[str, Any], *,
                responsible: list[dict[str, Any]], lifecycle: dict[str, Any] | None = None,
                latest_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """一个 0.1 业务对象的三组视图：business 按 0.1 登记的属性、关系与块；identity 是 0.1 规则解析的责任人；records 是
    0.1 状态机推出的生命周期与最新的 0.1 快照（legacy_0_1）。"""
    spec = v01_registry.object_spec(head["object_type"])
    payload, version = revision["payload"], revision["object_version"]
    business = {
        "object_id": head["object_id"], "object_type": head["object_type"], "type_display_name": spec["display_name"],
        "category": category(head["object_type"]), "candidate": False,
        "version": version, "revision_id": revision["revision_id"], "object_version": head["object_version"],
        "title": payload["title"],
        "attributes": {attribute["id"]: cited(pinned(payload.get(attribute["id"]))) if attribute["value"] == "ref"
                       else payload.get(attribute["id"]) for attribute in spec["attributes"] if attribute["id"] != "title"},
        "relations": [{"field": field["field"], "relation": field["relation"],
                       "value": cited(pinned(payload.get(field["field"])))} for field in spec["relation_fields"]],
        "blocks": blocks(head["object_id"], version, spec["blocks"], payload["blocks"]),
        "component_ledger": [],
        "formal": {"lifecycle_status": head["lifecycle_status"], "effective_revision_id": head["effective_revision_id"]},
        "round": None,
    }
    identity = {"responsible": {**spec["responsible"], "principals": responsible}, "delegations": []}
    records = {"lifecycle": lifecycle and {key: lifecycle[key] for key in ("status", "display_name", "event_id")},
               "latest_state": latest_state, "confirmed_review": None, "open_issues": []}
    return {"object_id": head["object_id"], "business": business, "identity": identity, "records": records,
            "protocol": metadata}


def snapshot_view(head: dict[str, Any], revision: dict[str, Any], *, generator: dict[str, Any] | None) -> dict[str, Any]:
    """一条 0.1 快照按 0.2 的快照外壳读：payload 类型是只读的 legacy_0_1，块是登记 state.legacy_read 列的三块。"""
    payload, version = revision["payload"], revision["object_version"]
    legacy = world_registry.registry()["state"]["legacy_read"]
    specs = {block["id"]: block for block in v01_registry.object_spec("StateSnapshot")["blocks"]}
    time_record = world_registry.category("time_record")
    return {"object_id": head["object_id"], "object_type": "StateSnapshot",
            "category": {"id": time_record["id"], "display_name": time_record["display_name"]},
            "version": version, "revision_id": revision["revision_id"], "ref": citation(head["object_id"], version),
            "title": payload["title"], "subject_ref": cited(pinned(payload["subject_ref"])), "as_of": payload["as_of"],
            "period": payload.get("period"), "generator": generator, "source_event_refs": [],
            "payload_type": {"id": legacy["payload_type"], "display_name": LEGACY_DISPLAY_NAME},
            "blocks": blocks(head["object_id"], version, [specs[block] for block in legacy["blocks"]], payload["blocks"]),
            "unconfirmed": True}


# ------------------------------------------------------------ reads
def writer(conn: Any, ctx: Any, snapshot_id: str) -> dict[str, Any] | None:
    """记下这条 0.1 快照的人：写它的那条 state.refreshed 事件的记录者。"""
    row = conn.execute(
        """SELECT principal_id FROM gov_world_events
            WHERE scope_id=%s AND kind='state.refreshed' AND subject_refs @> %s
            ORDER BY recorded_at, event_id LIMIT 1""",
        (ctx.scope_id, Jsonb([{"object_id": snapshot_id}]))).fetchone()
    return row and principal(conn, ctx, str(row["principal_id"]))


def latest_snapshot(conn: Any, ctx: Any, subject_id: str, as_of: Any = None) -> dict[str, Any] | None:
    """主体 as_of 不晚于该时点（不给即最新）的那条 0.1 快照（条件同 0.1 读侧），读成 legacy_0_1。"""
    row = conn.execute(
        "SELECT * FROM gov_object_revisions WHERE " + v01.SNAPSHOTS_OF_SUBJECT + """
              AND (%s::timestamptz IS NULL OR (payload->>'as_of')::timestamptz <= %s::timestamptz)
            ORDER BY (payload->>'as_of')::timestamptz DESC LIMIT 1""",
        (ctx.scope_id, subject_id, as_of, as_of)).fetchone()
    if row is None:
        return None
    revision = db.jsonable(row)
    return snapshot_view(revision, revision, generator=writer(conn, ctx, revision["object_id"]))


def read_object(conn: Any, ctx: Any, object_id: str, version: int | None = None) -> dict[str, Any]:
    """取 0.1 对象的 0.2 视图：判权与找不到同 0.1 的取对象；默认最新修订，给 version 取该修订序号。"""
    head, metadata = v01.readable(conn, ctx, object_id)
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
        return {**snapshot_view(head, revision, generator=writer(conn, ctx, head["object_id"])), "protocol": metadata}
    rule = v01_registry.object_spec(head["object_type"])["responsible"]
    return object_view(head, revision, metadata,
                       responsible=responsible_principals(conn, ctx, head, revision["payload"], rule),
                       lifecycle=v01.lifecycle(conn, ctx, head), latest_state=latest_snapshot(conn, ctx, head["object_id"]))


def state(conn: Any, ctx: Any, object_id: str, as_of: Any = None) -> dict[str, Any]:
    """取 0.1 对象的状态，快照读成 legacy_0_1；形状同 0.2 的取状态。"""
    head, _ = v01.readable(conn, ctx, object_id)
    return {"object_id": head["object_id"], "as_of": as_of and utc_text(as_of.isoformat()),
            "snapshot": latest_snapshot(conn, ctx, head["object_id"], as_of)}
