"""tkos.world/0.2 的读投影与回执授权。

读权限同 0.1（契约第 15.1 节）：scope 内有任一生效角色指派的责任主体可读该 scope 的全部
world 对象，不走域级 read 策略；非 world 对象一律 NOT_FOUND，授权先于任何协议错误。
读投影按三层分组：``business``、``identity``、``records``。本票只到 Company 需要的部分：
组件、台账、一轮、委托、快照、复盘与问题随各自的票接入，在此之前给空值。
"""
from __future__ import annotations

from typing import Any

from . import db, protocol
from . import world_v02_registry as world_registry
from .errors import GovernedError
from .world_v02_models import CONTRACT_VERSION, citation

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


def block_view(object_id: str, version: int, spec: dict[str, Any], value: dict[str, Any] | None) -> dict[str, Any]:
    """一个块读回的样子：带块类别与组件，空块渲染标准句（契约第 3.2、4 节）。"""
    return {"id": spec["id"], "display_name": spec["display_name"], "kind": spec["kind"], "class": spec["class"],
            "value": value, "empty": value is None,
            "text": EMPTY_BLOCK_SENTENCE.format(name=spec["display_name"]) if value is None else value["text"],
            "components": value["components"] if value is not None else [],
            "ref": citation(object_id, version, spec["id"])}


def responsible_principals(conn: Any, ctx: Any, head: dict[str, Any]) -> list[dict[str, Any]]:
    """按角色解析的责任人（契约第 3.3 节）：当前在对象所在域持该角色、启用的人。"""
    rule = world_registry.object_spec(head["object_type"])["responsible"]
    if rule["source"] != "role":
        raise NotImplementedError("responsibility by attribute arrives with world 0.2 assignments")
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
                responsible: list[dict[str, Any]]) -> dict[str, Any]:
    spec = world_registry.object_spec(head["object_type"])
    if head["object_type"] in world_registry.registry()["lifecycles"]:
        raise NotImplementedError("world 0.2 lifecycles are read once the lifecycle engine is wired")
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
        "relations": [],
        "blocks": [block_view(head["object_id"], version, block, payload["blocks"][block["id"]])
                   for block in spec["blocks"]],
        "component_ledger": [],
        "formal": {"lifecycle_status": head["lifecycle_status"], "effective_revision_id": head["effective_revision_id"]},
        "round": None,
    }
    rule = spec["responsible"]
    identity = {"responsible": {"source": rule["source"], "role": rule["role"], "principals": responsible},
                "delegations": []}
    records = {"lifecycle": None, "latest_state": None, "confirmed_review": None, "open_issues": []}
    return {"object_id": head["object_id"], "business": business, "identity": identity, "records": records,
            "protocol": metadata}


def read_object(conn: Any, ctx: Any, object_id: str, version: int | None = None) -> dict[str, Any]:
    """取对象：默认最新修订，给 version 取该修订序号。"""
    head, metadata = readable(conn, ctx, object_id)
    if version is None:
        row = conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                           (ctx.scope_id, head["latest_revision_id"])).fetchone()
    else:
        row = conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND object_version=%s",
                           (ctx.scope_id, head["object_id"], version)).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND")
    return object_view(head, db.jsonable(row), metadata, responsible=responsible_principals(conn, ctx, head))


def is_receipt(row: dict[str, Any]) -> bool:
    """0.2 的回执在结果里写明契约版本；0.1 与 0.2 的动作同名，只按动作名分不开。"""
    return (row.get("result") or {}).get("contract_version") == CONTRACT_VERSION


def authorize_receipt(conn: Any, ctx: Any, row: dict[str, Any], *, replay: bool = False) -> None:
    """读 0.2 回执按 world 读规则：调用者在 scope 内有生效指派，回执涉及的对象仍是 world 对象。

    重放是再次执行成功，另按当前权限复核：必须是原调用者，动作当时用到的指派仍然有效，
    且调用者在该域仍有这个动作的角色。
    """
    ids = {str(item["object_id"]) for item in row["object_versions"]}
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
    db.authorize_domain(conn, ctx, row["result"]["domain_id"], row["action_type"])
