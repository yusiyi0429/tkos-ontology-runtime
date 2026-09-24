"""tkos.world/0.1 的读投影与回执授权。

读权限按契约第 12 节：scope 内有任一生效角色指派的责任主体可读该 scope 的全部
world 对象，不走域级 read 策略。非 world 对象经 world 端点一律 NOT_FOUND，不透露
其存在；授权先于任何协议错误。
"""
from __future__ import annotations

from typing import Any

from . import db, protocol
from . import world_v01_registry as world_registry
from .errors import GovernedError
from .world_v01_models import ACTION_PARAMS

# 空块的标准句在投影层配置（契约第 3 节）。
EMPTY_BLOCK_SENTENCE = "当前没有{name}"


def citation(object_id: str, version: int, block: str | None = None) -> str:
    return f"{object_id}@{version}" + (f"#{block}" if block else "")


def object_view(head: dict[str, Any], revision: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    spec = world_registry.object_spec(head["object_type"])
    payload, version = revision["payload"], revision["object_version"]
    blocks = []
    for block in spec["blocks"]:
        value = payload["blocks"][block["id"]]
        blocks.append({"id": block["id"], "display_name": block["display_name"], "kind": block["kind"],
                       "value": value, "empty": value is None,
                       "text": EMPTY_BLOCK_SENTENCE.format(name=block["display_name"]) if value is None else value["text"],
                       "ref": citation(head["object_id"], version, block["id"])})
    relations = [{"field": field["field"], "relation": field["relation"], "value": payload.get(field["field"])}
                 for field in spec["relation_fields"]]
    attributes = {attribute["id"]: payload.get(attribute["id"]) for attribute in spec["attributes"]
                  if attribute["id"] != "title"}
    return {"object_id": head["object_id"], "object_type": head["object_type"],
            "type_display_name": spec["display_name"], "version": version, "revision_id": revision["revision_id"],
            "title": payload["title"], "attributes": attributes, "blocks": blocks, "relations": relations,
            "formal": {"lifecycle_status": head["lifecycle_status"],
                       "effective_revision_id": head["effective_revision_id"]},
            "protocol": metadata}


def _world_head(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    db._assignments(conn, ctx)  # 没有任何生效指派的调用者是 403
    object_id = db._uuid(object_id)
    row = conn.execute("SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s",
                       (ctx.scope_id, object_id)).fetchone()
    binding = protocol.current_binding(conn, ctx.scope_id, object_id) if row is not None else None
    if row is None or binding is None or binding["protocol_id"] != "tkos.world":
        raise GovernedError("NOT_FOUND")
    return db.jsonable(row)


def read_object(conn: Any, ctx: Any, object_id: str) -> dict[str, Any]:
    head = _world_head(conn, ctx, object_id)
    # 共享的读支持闸门服务于其他协议的读取器，不接受 world；world 在这里自己核对解释状态。
    metadata = protocol.read_metadata(conn, ctx.scope_id, head["object_id"])
    if metadata["interpretation_status"] != "world_v0_1":
        raise GovernedError("PROTOCOL_NOT_SUPPORTED", "The object is not read under tkos.world/0.1.", status=409)
    revision = db.jsonable(conn.execute(
        "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s",
        (ctx.scope_id, head["object_id"], head["latest_revision_id"])).fetchone())
    return object_view(head, revision, metadata)


def is_receipt(row: dict[str, Any]) -> bool:
    return row["action_type"] in ACTION_PARAMS


def authorize_receipt(conn: Any, ctx: Any, row: dict[str, Any], *, replay: bool = False) -> None:
    """读 world 回执按 world 读规则：调用者在 scope 内有生效指派，回执涉及的对象仍是 world 对象。

    重放是再次执行成功，另按当前权限复核：必须是原调用者，动作当时用到的指派仍然有效，
    且调用者在该域仍有这个动作的角色；scope 成员身份或无关角色不能复活被撤销的命令。
    """
    ids = {str(item["object_id"]) for item in row["object_versions"]}
    if row["target_object_id"]:
        ids.add(str(row["target_object_id"]))
    for object_id in ids:
        _world_head(conn, ctx, object_id)
    if not replay:
        return
    if str(row["principal_id"]) != ctx.principal_id:
        raise GovernedError("FORBIDDEN")
    current = {item["assignment_id"] for item in db._assignments(conn, ctx)}
    if any(aid not in current for aid in row["result"].get("required_assignment_ids", [])):
        raise GovernedError("FORBIDDEN", "An assignment this command relied on is no longer valid.")
    db.authorize_domain(conn, ctx, row["result"]["domain_id"], row["action_type"])
