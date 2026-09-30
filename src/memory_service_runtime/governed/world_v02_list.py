"""tkos.world/0.2 的列对象与按外部引用查找（票 #63，契约第 3.4、15.2 节，补 2、42）。

``GET /v1/world/objects`` 列本 scope 的 world 对象（业务对象与状态快照；0.2 读侧兼容读 0.1 对象，0.1 对象同样列出，
对象头写明契约版本），每项给对象头：id、类型、类别、标题、最新版本（修订序号与修订 id，另给对象行的并发版本，写入时作
expected_version）、生命周期（与取对象的 records.lifecycle 相同）、域、外部引用。读权限同取对象（第 15.1 节）：scope 内有
任一生效角色指派即可读，没有是 403。

筛选都按各对象的最新修订，可以组合：
- ``unit_id``（责任单元的对象 id，等于筛它所在的域：每个责任单元在自己的域）或 ``domain_id``，两者只给一个；
- ``type``：登记里的对象类型（含 StateSnapshot）；
- ``period``（``YYYY-MM``，补 42）：周期目标按自己的 period，Mission 按其周期目标，Task 按其 Mission、Activity 按其 Task 所属
  的 Mission 各自的周期目标，状态快照按自己的 period；其余类型没有周期，给 period 时不列出；
- ``external_system`` 与 ``external_id``：外部引用含这一对的对象；只给 external_system 时是带该系统任一外部引用的对象。
  (system, id) 在 scope 内唯一（第 3.4 节），所以两者都给时至多一项。查找走迁移 0039 的 external_refs 索引。

分页同其他列表端点：``limit``（1 至 100，默认 50）与不透明的 ``cursor``（上一页的 ``next_cursor``，绑定端点、scope、
调用者与筛选）；按对象的建立时刻与 id 排序，``next_cursor`` 为 null 即最后一页。返回 ``{"items": [...], "next_cursor"}``。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from . import db, workbench
from . import world_v01_readers as v01
from . import world_v01_registry as v01_registry
from . import world_v02_readers as v02
from . import world_v02_registry as world_registry
from .errors import GovernedError
from .world_v02_models import CONTRACT_VERSION

ENDPOINT = "world_objects"
# 查询参数的白名单：不认识的与重复的参数一律 INVALID_REQUEST，不静默忽略。各参数的形状在路由上校验：period 是
# YYYY-MM，external_system、external_id 与写入时外部引用的 system、id 同一约束（含非空白字符，不超过 256 字符）。
QUERY = frozenset({"unit_id", "domain_id", "type", "period", "external_system", "external_id", "limit", "cursor"})


def _invalid(message: str) -> GovernedError:
    return GovernedError("INVALID_REQUEST", message, status=422)


def filters(*, unit_id: Any = None, domain_id: Any = None, object_type: str | None = None, period: str | None = None,
            external_system: str | None = None, external_id: str | None = None) -> dict[str, Any]:
    """请求参数 → 筛选；不连库就能判的在这里拒绝（INVALID_REQUEST）。"""
    if unit_id is not None and domain_id is not None:
        raise _invalid("Filter by unit_id or by domain_id, not both.")
    if external_id is not None and external_system is None:
        raise _invalid("external_id is looked up together with external_system.")
    if object_type is not None and object_type not in world_registry.object_types():
        raise _invalid(f"type is one of {', '.join(sorted(world_registry.object_types()))}.")
    return {"unit_id": unit_id and str(unit_id), "domain_id": domain_id and str(domain_id), "type": object_type,
            "period": period, "external_system": external_system, "external_id": external_id}


# 周期口径（补 42）：某对象最新修订的载荷里 field 指向的对象，它的最新修订的载荷。
_LATEST_PAYLOAD = ("(SELECT x.payload FROM gov_objects y JOIN gov_object_revisions x"
                   " ON x.scope_id=y.scope_id AND x.revision_id=y.latest_revision_id"
                   " WHERE y.scope_id=o.scope_id AND y.object_id=({payload}->'{field}'->>'object_id')::uuid)")


def _up(payload: str, field: str) -> str:
    return _LATEST_PAYLOAD.format(payload=payload, field=field)


_PERIOD = f"""CASE o.object_type
    WHEN 'PeriodGoal' THEN r.payload->>'period'
    WHEN 'StateSnapshot' THEN r.payload->>'period'
    WHEN 'Mission' THEN {_up('r.payload', 'goal_ref')}->>'period'
    WHEN 'Task' THEN {_up(_up('r.payload', 'parent_ref'), 'goal_ref')}->>'period'
    WHEN 'Activity' THEN {_up(_up(_up('r.payload', 'parent_ref'), 'parent_ref'), 'goal_ref')}->>'period' END"""


def _cursor_key(ctx: Any, given: dict[str, Any], cursor: str | None) -> list[str] | None:
    """游标里是上一页最后一项的（建立时刻, 对象 id）；游标须是本端点、本 scope、本调用者、同一组筛选签出的。"""
    raw = workbench.decode_cursor(cursor, ENDPOINT, ctx, given)
    if raw is None:
        return None
    try:
        if len(raw) != 2 or not all(isinstance(item, str) for item in raw):
            raise ValueError
        datetime.fromisoformat(raw[0].replace("Z", "+00:00"))
        return [raw[0], db._uuid(raw[1])]
    except (ValueError, GovernedError):
        raise _invalid("The supplied cursor is not valid for this read.") from None


def _domain(conn: Any, ctx: Any, given: dict[str, Any]) -> str | None:
    """按单元筛即按它所在的域：单元须是本 scope 的 world 对象（否则 404），且是责任单元；域须是本 scope 的域。"""
    if given["unit_id"] is not None:
        unit = v02.head_and_binding(conn, ctx, given["unit_id"])[0]
        if unit["object_type"] != "ResponsibilityUnit":
            raise _invalid("unit_id names a responsibility unit.")
        return unit["domain_id"]
    if given["domain_id"] is not None and conn.execute(
            "SELECT 1 FROM gov_domains WHERE scope_id=%s AND domain_id=%s",
            (ctx.scope_id, given["domain_id"])).fetchone() is None:
        raise GovernedError("NOT_FOUND")
    return given["domain_id"]


def list_objects(conn: Any, ctx: Any, given: dict[str, Any], limit: int, cursor: str | None) -> dict[str, Any]:
    db._assignments(conn, ctx)  # scope 内没有任何生效指派的调用者是 403（同取对象）
    domain = _domain(conn, ctx, given)
    key = _cursor_key(ctx, given, cursor)
    sql = """SELECT o.object_id, o.object_type, o.domain_id, o.created_at, o.object_version,
                    r.object_version AS version, r.revision_id, r.payload->>'title' AS title,
                    r.payload->'external_refs' AS external_refs, b.contract_version
               FROM gov_objects o
               JOIN gov_object_revisions r ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
               JOIN LATERAL (SELECT protocol_id, contract_version FROM gov_object_protocol_bindings
                              WHERE scope_id=o.scope_id AND object_id=o.object_id
                              ORDER BY binding_version DESC, recorded_at DESC, binding_id DESC LIMIT 1) b ON true
              WHERE o.scope_id=%s AND b.protocol_id='tkos.world'"""
    params: list[Any] = [ctx.scope_id]
    if domain is not None:
        sql += " AND o.domain_id=%s"
        params.append(domain)
    if given["type"] is not None:
        sql += " AND o.object_type=%s"
        params.append(given["type"])
    if given["external_system"] is not None:
        probe = {"system": given["external_system"]}
        if given["external_id"] is not None:
            probe["id"] = given["external_id"]
        sql += " AND r.payload ? 'external_refs' AND r.payload->'external_refs' @> %s"
        params.append(Jsonb([probe]))
    if given["period"] is not None:
        sql += f" AND {_PERIOD} = %s"
        params.append(given["period"])
    if key is not None:
        sql += " AND (o.created_at, o.object_id) > (%s::timestamptz, %s::uuid)"
        params.extend(key)
    sql += " ORDER BY o.created_at, o.object_id LIMIT %s"
    params.append(limit + 1)
    rows = [db.jsonable(row) for row in conn.execute(sql, params).fetchall()]
    more, rows = len(rows) > limit, rows[:limit]
    derived = lifecycles(conn, ctx, rows)
    last = rows[-1] if rows else None
    return {"items": [header(row, derived[row["object_id"]]) for row in rows],
            "next_cursor": workbench.encode_cursor(ENDPOINT, ctx, given, [last["created_at"], last["object_id"]])
            if more and last else None}


def lifecycles(conn: Any, ctx: Any, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """一页对象的生命周期，走取对象的同一条推导：0.2 对象按 0.2 的状态表，0.1 对象按 0.1 的状态机。"""
    return {row["object_id"]: (v02.lifecycle if row["contract_version"] == CONTRACT_VERSION else v01.lifecycle)(
        conn, ctx, row) for row in rows}


def header(row: dict[str, Any], lifecycle: dict[str, Any] | None) -> dict[str, Any]:
    """对象头（契约第 15.2 节）。类型的中文名按对象绑定的版本取登记；类别按类型名取 0.2 登记的三层。"""
    legacy = row["contract_version"] != CONTRACT_VERSION
    spec = (v01_registry if legacy else world_registry).object_spec(row["object_type"])
    category = world_registry.category(world_registry.object_spec(row["object_type"])["category"])
    return {"object_id": row["object_id"], "object_type": row["object_type"], "type_display_name": spec["display_name"],
            "category": {"id": category["id"], "display_name": category["display_name"]}, "title": row["title"],
            "version": row["version"], "revision_id": row["revision_id"], "object_version": row["object_version"],
            "lifecycle": lifecycle and {key: lifecycle[key] for key in ("status", "display_name", "event_id")},
            "domain_id": row["domain_id"], "external_refs": row["external_refs"] or [],
            "contract_version": row["contract_version"]}
