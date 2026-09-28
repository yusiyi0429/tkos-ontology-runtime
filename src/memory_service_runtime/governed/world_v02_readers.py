"""tkos.world/0.2 的读投影与回执授权。

读权限同 0.1（契约第 15.1 节）：scope 内有任一生效角色指派的责任主体可读该 scope 的全部
world 对象，不走域级 read 策略；非 world 对象一律 NOT_FOUND，授权先于任何协议错误。
读投影按三层分组：``business``、``identity``、``records``。``business`` 给块、组件、组件台账与关系，
引用同时给钉定结构与业务形式，另给正式内容指针与进行中的一轮（票 #54）；``identity`` 给责任人与委托范围覆盖
对象所在域的当前有效委托（票 #62）；``records`` 给生命周期与推出它的事件（按登记的状态表推导，ADR-0002）与最新
状态快照（标明未经确认）。复盘与问题随各自的票接入，在此之前给空值。状态快照是时间记录，按 id 读回的是快照视图，
不分三组。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from psycopg.types.json import Jsonb

from . import db, protocol
from . import world_v02_lifecycle as world_lifecycle
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


def responsible_principals(conn: Any, ctx: Any, head: dict[str, Any], payload: dict[str, Any],
                           rule: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """责任人（契约第 3.3 节）：按角色解析的是当前在对象所在域持该角色、启用的人；按属性解析的是
    responsible 属性上的身份，还没指派时为空。规则默认取 0.2 登记；读 0.1 对象时给 0.1 登记的规则（票 #63）。"""
    rule = rule or world_registry.object_spec(head["object_type"])["responsible"]
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


def lifecycle_events(conn: Any, ctx: Any, object_id: str) -> list[dict[str, Any]]:
    """推导生命周期的输入：以该对象为目标（subject_refs 的第一项）的 0.2 事件，按记录顺序，带取自回执的产生它的动作，
    以及是否带候选（候选随门事件的 detail 留存，契约第 12 节）。"""
    return [db.jsonable(row) for row in conn.execute(
        """SELECT e.event_id, r.action_type AS action, e.outcome, e.disposition, e.supersedes_event_id,
                  COALESCE(e.detail ? 'candidate', false) AS candidate
             FROM gov_world_events e JOIN gov_action_receipts r ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id
            WHERE e.scope_id=%s AND e.contract_version=%s AND e.subject_refs->0->>'object_id'=%s
            ORDER BY e.recorded_at, e.event_id""", (ctx.scope_id, CONTRACT_VERSION, object_id)).fetchall()]


def lifecycle(conn: Any, ctx: Any, head: dict[str, Any]) -> dict[str, Any] | None:
    """按事件的记录顺序推导生命周期（契约第 10 节）；没有生命周期的类型为 None。"""
    return world_lifecycle.derive(world_registry.registry(), head["object_type"],
                                  lifecycle_events(conn, ctx, head["object_id"]))


def round_view(conn: Any, ctx: Any, head: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any] | None:
    """进行中的一轮（契约第 12、15.1 节）：开轮的事件、这一轮走到哪一段、候选来自哪条事件与候选本身（合并补丁）；
    没有进行中的一轮为 None。"""
    if current is None:
        return None
    names = {state["id"]: state["display_name"]
             for state in world_registry.registry()["lifecycles"][head["object_type"]]["states"]}
    candidate = current["candidate_event_id"] and conn.execute(
        "SELECT detail->'candidate' AS candidate FROM gov_world_events WHERE scope_id=%s AND event_id=%s",
        (ctx.scope_id, current["candidate_event_id"])).fetchone()["candidate"]
    return {**current, "display_name": names[current["stage"]], "candidate": candidate}


def object_view(head: dict[str, Any], revision: dict[str, Any], metadata: dict[str, Any], *,
                responsible: list[dict[str, Any]], latest_state: dict[str, Any] | None = None,
                lifecycle: dict[str, Any] | None = None, current_round: dict[str, Any] | None = None,
                delegations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        "round": current_round,
    }
    identity = {"responsible": {**spec["responsible"], "principals": responsible}, "delegations": delegations or []}
    records = {"lifecycle": lifecycle and {key: lifecycle[key] for key in ("status", "display_name", "event_id")},
               "latest_state": latest_state, "confirmed_review": None, "open_issues": []}
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
    derived = lifecycle(conn, ctx, head)
    return object_view(head, revision, metadata, responsible=responsible_principals(conn, ctx, head, revision["payload"]),
                       latest_state=latest_snapshot(conn, ctx, head["object_id"]), lifecycle=derived,
                       current_round=round_view(conn, ctx, head, derived and derived["round"]),
                       delegations=[delegation_view(conn, ctx, row) for row in
                                    delegations_in_force(conn, ctx, domain_id=head["domain_id"])])


# ------------------------------------------------------------ delegations
def delegations_in_force(conn: Any, ctx: Any, *, grantor: str | None = None, delegate: str | None = None,
                         family: str | None = None, domain_id: str | None = None) -> list[dict[str, Any]]:
    """当前有效的代记委托（契约第 2、14 节）：身份投影，由委托事件投影、不建表——已登记、未撤销、未过期，且委托人
    仍是 scope 内有效的人（启用的人，当前有任一生效的角色指派）。可按委托人、受托的服务主体、动作族与域筛选，
    按登记顺序。"""
    return [db.jsonable(row) for row in conn.execute(
        """SELECT g.event_id, g.principal_id, g.detail, g.recorded_at FROM gov_world_events g
            WHERE g.scope_id=%s AND g.contract_version=%s AND g.kind='delegation.granted'
              AND (g.detail->>'valid_until')::timestamptz > clock_timestamp()
              AND NOT EXISTS (SELECT 1 FROM gov_world_events r
                               WHERE r.scope_id=g.scope_id AND r.contract_version=g.contract_version
                                 AND r.kind='delegation.revoked' AND r.detail->>'delegation_event_id'=g.event_id::text)
              AND EXISTS (SELECT 1 FROM gov_principals p
                           WHERE p.scope_id=g.scope_id AND p.principal_id=g.principal_id AND p.active
                             AND p.principal_type='human'
                             AND EXISTS (SELECT 1 FROM gov_role_assignments a
                                          WHERE a.scope_id=p.scope_id AND a.principal_id=p.principal_id AND a.active
                                            AND a.valid_from<=clock_timestamp()
                                            AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to)))
              AND (%s::uuid IS NULL OR g.principal_id=%s::uuid)
              AND (%s::text IS NULL OR g.detail->>'delegate_principal_id'=%s::text)
              AND (%s::text IS NULL OR g.detail->'families' ? %s::text)
              AND (%s::text IS NULL OR g.detail->'domain_ids' ? %s::text)
            ORDER BY g.recorded_at, g.event_id""",
        (ctx.scope_id, CONTRACT_VERSION, grantor, grantor, delegate, delegate, family, family,
         domain_id, domain_id)).fetchall()]


def delegation_view(conn: Any, ctx: Any, row: dict[str, Any]) -> dict[str, Any]:
    """一条当前有效的委托读回的样子：登记事件（及其事件引用）、委托人、受托的服务主体、动作族、域与有效期。"""
    detail = row["detail"]
    return {"event_id": row["event_id"], "ref": f"event:{row['event_id']}",
            "grantor": principal(conn, ctx, row["principal_id"]),
            "delegate": principal(conn, ctx, detail["delegate_principal_id"]),
            "families": detail["families"], "domain_ids": detail["domain_ids"], "valid_until": detail["valid_until"],
            "granted_at": utc_text(row["recorded_at"])}


def judged_context(ctx: Any, principal_id: str) -> Any:
    """代记时判权用的身份上下文（契约第 14 节）：同一 scope、同一授权纪元，身份换成被代记的人（委托人只能是人）；
    指派留空，由 db._assignments 按他当前的取。"""
    return replace(ctx, principal_id=principal_id, principal_type="human", assignments=[])


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
    代记的回执另要委托仍有效，指派、角色与责任关系按被代记的人复核。
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
    judged = ctx
    represented = (row["result"].get("on_behalf_of") or {}).get("principal_id")
    if represented is not None:  # 代记：委托仍有效，指派与责任关系按被代记的人复核（契约第 14 节）
        if not delegations_in_force(conn, ctx, grantor=represented, delegate=ctx.principal_id,
                                    family=world_registry.action_spec(row["action_type"])["delegable"],
                                    domain_id=row["result"]["domain_id"]):
            raise GovernedError("FORBIDDEN", "The delegation this command relied on is no longer in force.")
        judged = judged_context(ctx, represented)
    current = {item["assignment_id"] for item in db._assignments(conn, judged)}
    if any(aid not in current for aid in row["result"].get("required_assignment_ids", [])):
        raise GovernedError("FORBIDDEN", "An assignment this command relied on is no longer valid.")
    if world_registry.action_spec(row["action_type"])["authorization"] != "scope":  # 外部事件按 scope 判权
        db.authorize_domain(conn, judged, row["result"]["domain_id"], row["action_type"])
    through = row["result"].get("responsible_through")
    if through is not None and db.jsonable(conn.execute(
            """SELECT r.payload->>'responsible' AS responsible FROM gov_objects o JOIN gov_object_revisions r
                 ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                WHERE o.scope_id=%s AND o.object_id=%s""", (ctx.scope_id, through)).fetchone())["responsible"] != judged.principal_id:
        raise GovernedError("FORBIDDEN", "The responsibility this command relied on has moved to someone else.")
