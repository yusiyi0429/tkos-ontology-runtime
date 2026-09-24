"""tkos.world/0.1 的取上下文（票 #25，B 固定路径）：从一个对象沿主干向上到 Company 做确定性遍历。

每层取最新修订的定义类块与 constraint（空块用标准句）、生命周期、责任人与跨链关系（只列引用不递归）；
当前对象向上直到 Mission（含）各层另取最新状态快照与近期事件。主干之外只多走一跳：单元长期目标沿
goal_ref 到公司级长期目标，只取它的定义类块（实验报告建议 2），放在该层的 hop 里，检索计划记在 hops。
整包渲染成 Markdown（标题之后先是六问指引，再分层；事件行写出记录人与被指派者的名字），按字符预算裁剪，
输出上下文包、检索计划与六问覆盖三件，每次调用在 gov_world_context_packs 落一行。从状态快照出发时，
以它的主体为当前对象，状态取这条快照。

trim、cover 与 guide 是纯函数：裁剪时每层条数上限先生效（每个对象的事件取最新的若干条），仍超字符预算时
先裁最旧的事件，再从主干最远层起逐层裁跨链关系行、多取的一跳、块、快照，最后裁当前对象的跨链关系行与
它多取的一跳；当前对象的块与它的最新快照不裁。六问指引计入预算，每裁一条按留下的内容重新渲染。
覆盖与指引都只看上下文包里实际留下的内容。
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Callable

from psycopg.types.json import Jsonb

from . import db
from . import world_v01_registry as world_registry
from .world_v01_models import RESPONSIBLE_ROLES, WorldContextRequest, citation, utc_text
from .world_v01_readers import (block_view, cited, event_view, events_about, holds_role, latest_snapshot, lifecycle,
                                readable, referenced_by)

# 暂定默认值，实验后再定（规格 #17 未决项）。
DEFAULT_MAX_CHARS, DEFAULT_MAX_EVENTS_PER_OBJECT, DEFAULT_RECENT_DAYS = 12000, 10, 30
# token 只估算上报（规格 #17）：中文为主的 Markdown 粗按每 2 个字符 1 个 token 计。
CHARS_PER_TOKEN_ESTIMATE = 2
# 取最新快照与近期事件的层：当前对象，以及它向上直到 Mission（含）的执行链。
_EXECUTION_TYPES = frozenset({"Activity", "Task", "Mission"})
# 主干之外多走的一跳：类型 -> (字段, 显示名)。单元长期目标的 goal_ref 只能指向公司级长期目标（契约第 6 节）。
_HOPS = {"LongTermGoal": ("goal_ref", "公司级长期目标")}
# 六问指引：Why 沿的目标链，以及 What 在当前对象之外还看的执行层（Mission 之下）。
_GOAL_TYPES = frozenset({"Mission", "PeriodGoal", "LongTermGoal"})
_BELOW_MISSION = frozenset({"Task", "Activity"})
QUESTIONS = {"why": "为什么", "what": "做什么", "who": "谁负责", "now": "现在怎样", "happened": "发生了什么",
             "basis": "凭什么"}
_GAPS = {"why": "主干上层没有取到非空的定义类块", "what": "当前对象的定义类块都是空的",
         "who": "当前对象没有可解析的责任人", "now": "没有取到状态快照", "happened": "窗口内没有取到事件",
         "basis": "没有取到上层经确认的正式内容，也没有取到验收标准或约束"}


# ------------------------------------------------------------ pure: budget and coverage
def _markdown(items: list[dict[str, Any]], lead: Callable[[set[str]], str] | None = None) -> str:
    texts = [item["text"] for item in items]
    if lead is not None:  # 紧跟标题，由留下的条目渲染
        texts.insert(sum(item["kind"] == "title" for item in items), lead({item["key"] for item in items}))
    return "\n\n".join(texts)


def _moment(item: dict[str, Any]) -> tuple[datetime, str]:
    # UTC 规范文本按字符串比较不保序（同一秒里 …00Z 排在 …00.5Z 之后），比较前先解析。
    return datetime.fromisoformat(item["occurred_at"].replace("Z", "+00:00")), item["key"]


def trim(items: list[dict[str, Any]], *, max_chars: int, max_events_per_object: int,
         lead: Callable[[set[str]], str] | None = None) -> dict[str, Any]:
    """按文档顺序给出的条目裁到预算内；返回留下的条目、裁掉的条目（带原因）、渲染结果与字符数。
    lead 按留下条目的 key 渲染一段放在标题之后（六问指引），计入字符预算，每裁一条重新渲染。"""
    kept = list(items)
    trimmed: list[dict[str, Any]] = []

    def drop(item: dict[str, Any], reason: str) -> None:
        kept.remove(item)
        trimmed.append({"key": item["key"], "kind": item["kind"], "level": item["level"], "reason": reason})

    counted: dict[str, int] = {}
    for item in sorted((item for item in items if item["kind"] == "event"), key=_moment, reverse=True):
        counted[item["object_id"]] = counted.get(item["object_id"], 0) + 1
        if counted[item["object_id"]] > max_events_per_object:
            drop(item, "over_level_cap")
    order = sorted((item for item in kept if item["kind"] == "event"), key=_moment)
    for level in sorted({item["level"] for item in items if item["level"] > 0}, reverse=True):
        order += [item for item in kept if item["level"] == level and item["kind"] in {"relations", "hop"}]
        order += [item for item in reversed(kept) if item["level"] == level and item["kind"] == "block"]
        order += [item for item in kept if item["level"] == level and item["kind"] == "snapshot"]
    order += [item for item in kept if item["level"] == 0 and item["kind"] in {"relations", "hop"}]
    for item in order:
        if len(_markdown(kept, lead)) <= max_chars:
            break
        drop(item, "over_budget")
    markdown = _markdown(kept, lead)
    return {"kept": kept, "trimmed": trimmed, "markdown": markdown, "chars": len(markdown),
            "over_budget": len(markdown) > max_chars}


def _reach(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """当前对象之上的内容：上溯各层，以及各层（含当前对象）多取的一跳，由近及远。"""
    return [part for layer in layers for part in (layer, layer.get("hop")) if part][1:]


def cover(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """六问各自答没答（按上下文包里留下的内容判），依据哪些引用或事件，答不了的缺口。第 0 层是当前对象；
    多取的一跳与上溯各层一样算作上层。"""
    current, upper = layers[0], _reach(layers)

    def blocks(chosen: list[dict[str, Any]], test) -> list[dict[str, str]]:
        return [{"ref": block["ref"]} for layer in chosen for block in layer["blocks"] if not block["empty"] and test(block)]
    basis = (blocks([layer for layer in upper if layer["object"]["formal"]], lambda block: True)
             + blocks(layers, lambda block: block["id"] in {"acceptance", "constraint"}))
    evidence = {
        "why": blocks(upper, lambda block: block["kind"] == "definition"),
        "what": blocks([current], lambda block: block["kind"] == "definition"),
        "who": [{"ref": current["object"]["ref"]}] if current["object"]["responsible"] else [],
        "now": [{"ref": layer["state"]["ref"]} for layer in layers if layer["state"]],
        "happened": [{"event_id": event["event_id"]} for layer in layers for event in layer["events"]],
        "basis": [item for index, item in enumerate(basis) if item not in basis[:index]],
    }
    return {name: {"question": QUESTIONS[name], "answered": bool(found), "evidence": found,
                   "gap": None if found else _GAPS[name]} for name, found in evidence.items()}


def guide(layers: list[dict[str, Any]]) -> str:
    """六问指引（实验报告建议 3）：Markdown 开头按问题给出处，内容在下文各层；只指向包里留下的内容，没有就写缺口。

    - 为什么：目标链，即上溯各层与多取一跳里的 Mission、周期目标、长期目标，各给非空的定义类块，没有块就给对象；
      链上没有目标（从公司级长期目标、责任单元、战略出发）时改列上溯各层。
    - 做什么：当前对象与它上溯到 Mission 之前的执行层（Activity 的 Task）的非空定义类块。
    - 谁负责：执行链（当前对象向上直到 Mission）各层的责任人，与当前对象最近一条指派事件。
    - 现在怎样：各层的最新状态快照。发生了什么：外部事件，其余事件只计条数。
    - 凭什么：执行链上带文档链接的块、快照块与事件。

    口径与 cover() 不同：cover() 是 0.1 契约的六问判定，随冻结不改；这里是给模型的阅读出处。为什么、做什么、
    谁负责、凭什么四问的取法不一样，同一个包里可能指引给了出处而覆盖报缺口。统一口径留给下一个 world 版本。"""
    current, reach = layers[0], _reach(layers)
    executing = [layer for layer in layers if layer["level"] == 0 or layer["object"]["object_type"] in _EXECUTION_TYPES]
    events = [event for layer in layers for event in layer["events"]]

    def place(part: dict[str, Any]) -> str:
        return "当前对象" if part is current else part.get("label") or part["object"]["type_display_name"]

    def defs(part: dict[str, Any]) -> list[str]:
        return [block["ref"] for block in part["blocks"] if block["kind"] == "definition" and not block["empty"]]

    def code(values: list[str]) -> str:
        return "、".join(f"`{value}`" for value in values)

    goals = [part for part in reach if part["object"]["object_type"] in _GOAL_TYPES] or reach
    doing = [current] + [layer for layer in layers[1:] if layer["object"]["object_type"] in _BELOW_MISSION]
    who = [f"{place(layer)} `{layer['object']['ref']}`："
           + ("、".join(person["display_name"] for person in layer["object"]["responsible"]) or "未指派") for layer in executing]
    assigned = next((event for event in current["events"] if event["kind"] == "assign"), None)  # 事件新的在前
    if assigned:
        who[0] += (f"，指派事件 `{assigned['event_id']}`（{assigned['occurred_at']}，{assigned['principal_name']} "
                   f"指派给 {assigned['assignee_name']}）")
    states = [f"{place(layer)} `{layer['state']['ref']}`" for layer in layers if layer["state"]]
    external = [event for event in events if event["kind"] == "event.recorded"]
    happened = ("外部事件 " + "、".join(f"`{event['event_id']}`（{_values('category')[event['category']]}）"
                                      for event in external)) if external else "窗口内没有外部事件"
    if len(events) > len(external):
        happened += f"；另有 {len(events) - len(external)} 条对象、指派、门与快照的记录，在各层事件里"
    linked = [("块", [block["ref"] for layer in executing for block in layer["blocks"]
                     if block["value"] and block["value"]["artifacts"]]),
              ("快照", [block["ref"] for layer in executing if layer["state"] for block in layer["state"]["blocks"]
                       if block["value"] and block["value"]["artifacts"]]),
              ("事件", [event["event_id"] for event in events if event["content"] and event["content"]["artifacts"]])]
    answers = {
        "why": " → ".join(f"{place(part)} {code(defs(part) or [part['object']['ref']])}" for part in goals),
        "what": "；".join(f"{place(part)} {code(defs(part))}" for part in doing if defs(part)),
        "who": "；".join(who),
        "now": "、".join(states) + "（快照都未经确认）" if states else "",
        "happened": happened if events else "",
        "basis": "文档链接在" + "；".join(f"{kind} {code(refs)}" for kind, refs in linked if refs)
                 if any(refs for _, refs in linked) else "",
    }
    gaps = {**_GAPS, "basis": "执行链上没有取到带文档链接的块、快照或事件"}
    return "\n".join(["## 六问指引", "按问题给出处，内容在下文各层。"]
                     + [f"- {QUESTIONS[name]}：{answer or '（缺口）' + gaps[name]}" for name, answer in answers.items()])


# ------------------------------------------------------------ assembly from the database
def _responsible(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any]) -> list[dict[str, Any]]:
    """责任人（契约第 4 节）：按 responsible 属性（须当前在该域持有对应角色）、按角色（该域持有该角色的人）
    或快照的写入者。"""
    rule = world_registry.object_spec(head["object_type"])["responsible"]
    if rule["source"] == "attribute":
        person = revision["payload"].get("responsible")
        kind = person and conn.execute("SELECT principal_type FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
                                       (ctx.scope_id, person)).fetchone()
        role = kind and RESPONSIBLE_ROLES[head["object_type"]].get(kind["principal_type"])
        ids = [person] if role and holds_role(conn, ctx, person, head["domain_id"], role) else []
    elif rule["source"] == "writer":
        ids = [revision["recorded_by"]]
    else:
        ids = [row["principal_id"] for row in conn.execute(
            """SELECT DISTINCT a.principal_id FROM gov_role_assignments a JOIN gov_principals p
                 ON p.scope_id=a.scope_id AND p.principal_id=a.principal_id
                WHERE a.scope_id=%s AND a.domain_id=%s AND a.role=%s AND a.active AND p.active
                  AND p.principal_type='human' AND a.valid_from<=clock_timestamp()
                  AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to)
                ORDER BY a.principal_id""", (ctx.scope_id, head["domain_id"], rule["role"])).fetchall()]
    return _principals(conn, ctx, ids)


def _principals(conn: Any, ctx: Any, ids: Any) -> list[dict[str, Any]]:
    """这些责任主体的 id、显示名与类型，按 id 排序。"""
    if not ids:
        return []
    rows = conn.execute("""SELECT principal_id, display_name, principal_type FROM gov_principals
                            WHERE scope_id=%s AND principal_id = ANY(%s::uuid[]) ORDER BY principal_id""",
                        (ctx.scope_id, sorted(str(value) for value in ids))).fetchall()
    return [db.jsonable(row) for row in rows]


def _pinned(object_id: str, version: int, revision_id: str, block: str | None = None) -> dict[str, Any]:
    return cited({"object_id": object_id, "object_version": version, "revision_id": revision_id, "block": block})


def _state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """状态快照在上下文包里的样子：钉到它的修订，标明未经确认（契约第 7 节）。"""
    object_id, version, revision_id = snapshot["object_id"], snapshot["version"], snapshot["revision_id"]
    return {"ref": citation(object_id, version), "pinned": _pinned(object_id, version, revision_id),
            "as_of": snapshot["attributes"]["as_of"], "unconfirmed": True,
            "blocks": [{**block, "pinned": _pinned(object_id, version, revision_id, block["id"])}
                       for block in snapshot["blocks"]]}


def _object(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
    """一个对象在上下文包里的表头：类型、标题、钉到所读修订的引用、生命周期与正式内容状态。"""
    spec = world_registry.object_spec(head["object_type"])
    object_id, version = head["object_id"], revision["object_version"]
    return {"object_id": object_id, "object_type": head["object_type"], "type_display_name": spec["display_name"],
            "title": revision["payload"]["title"], "version": version, "ref": citation(object_id, version),
            "pinned": _pinned(object_id, version, revision["revision_id"]), "lifecycle": lifecycle(conn, ctx, head),
            "formal": head["lifecycle_status"] == "confirmed" if spec["gated"] else None}


def _blocks(head: dict[str, Any], revision: dict[str, Any], kinds: set[str] | None = None) -> list[dict[str, Any]]:
    """对象的块（给了 kinds 只取这几类），空块用标准句，每块钉到所读修订。"""
    object_id, version, payload = head["object_id"], revision["object_version"], revision["payload"]
    return [{**block_view(object_id, version, block, payload["blocks"][block["id"]]),
             "pinned": _pinned(object_id, version, revision["revision_id"], block["id"])}
            for block in world_registry.object_spec(head["object_type"])["blocks"] if kinds is None or block["kind"] in kinds]


def _layer(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any], level: int, window: Any,
           seen_events: set[str], state: dict[str, Any] | None) -> dict[str, Any]:
    spec = world_registry.object_spec(head["object_type"])
    object_id, payload = head["object_id"], revision["payload"]
    layer = {
        "level": level,
        "object": {**_object(conn, ctx, head, revision), "responsible": _responsible(conn, ctx, head, revision)},
        "blocks": _blocks(head, revision),
        "relations": [{"field": field["field"], "relation": field["relation"], "refs": cited(payload.get(field["field"], []))}
                      for field in spec["relation_fields"] if field["written_by"] == "world_relate"],
        "referenced_by": referenced_by(conn, ctx, object_id),
        "hop": None, "state": None, "events": [],
    }
    if level == 0 or head["object_type"] in _EXECUTION_TYPES:
        snapshot = state or latest_snapshot(conn, ctx, object_id)
        layer["state"] = snapshot and _state(snapshot)
        for row in reversed(events_about(conn, ctx, object_id, window, by_occurrence=True)):  # 新的在前
            if row["event_id"] not in seen_events:  # 一条事件以多层对象为主体时，只放在离当前对象最近的那一层
                seen_events.add(row["event_id"])
                layer["events"].append(event_view(row))
    return layer


def _hop(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    """主干之外多取的一跳：对象表头与它的定义类块，不带 constraint、责任人、状态与事件。"""
    return {"field": field, "label": label, "object": _object(conn, ctx, head, revision),
            "blocks": _blocks(head, revision, {"definition"})}


def _name_events(conn: Any, ctx: Any, layers: list[dict[str, Any]]) -> None:
    """事件带上记录人与被指派者的显示名（实验报告建议 4），id 照旧留着。"""
    events = [event for layer in layers for event in layer["events"]]
    names = {row["principal_id"]: row["display_name"] for row in _principals(
        conn, ctx, {value for event in events for value in (event["principal_id"], event["assignee"]) if value})}
    for event in events:
        event["principal_name"] = names.get(event["principal_id"], event["principal_id"])
        event["assignee_name"] = event["assignee"] and names.get(event["assignee"], event["assignee"])


def _values(group: str) -> dict[str, str]:
    """事件属性取值的中文名（登记）。"""
    return {item["id"]: item["display_name"] for item in world_registry.registry()["event_attribute_values"][group]}


def _display(group: str, value: str | None) -> str:
    return "" if value is None else "·" + _values(group)[value]


def _block_text(block: dict[str, Any], heading: str) -> str:
    lines = [heading, block["text"]]
    if block["value"] and block["value"]["refs"]:
        lines.append("引用：" + "、".join(f"`{ref['ref']}`" for ref in block["value"]["refs"]))
    if block["value"] and block["value"]["artifacts"]:
        lines.append("文档：" + "、".join(block["value"]["artifacts"]))
    return "\n".join(lines)


def _status(obj: dict[str, Any]) -> list[str]:
    """表头里的生命周期、责任人（主干各层才有）与正式内容状态。"""
    stage = obj["lifecycle"]
    lines = [f"生命周期：{stage['display_name']}（事件 `{stage['event_id']}`）" if stage else "生命周期：无（只有版本）"]
    if "responsible" in obj:
        lines.append("责任人：" + ("、".join(person["display_name"] for person in obj["responsible"]) or "未指派"))
    if obj["formal"] is not None:
        lines.append("正式内容：" + ("已确认" if obj["formal"] else "尚未确认"))
    return lines


def _hop_text(hop: dict[str, Any]) -> str:
    obj = hop["object"]
    lines = [f"### 沿 {hop['field']} 多取一跳：{hop['label']}《{obj['title']}》 `{obj['ref']}`", *_status(obj)]
    lines += [_block_text(block, f"#### {block['display_name']} `{block['ref']}`") for block in hop["blocks"]]
    return "\n".join(lines)


def _items(question: str, start: str, layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把上下文包渲染成按文档顺序排列、可逐条裁剪的 Markdown 片段。"""
    kinds = {item["kind"]: item["display_name"] for item in world_registry.registry()["event_kinds"]}

    def entry(key: str, kind: str, level: int, object_id: str | None, text: str,
              occurred_at: str | None = None) -> dict[str, Any]:
        return {"key": key, "kind": kind, "level": level, "object_id": object_id, "occurred_at": occurred_at, "text": text}
    items = [entry("title", "title", -1, None, f"# 上下文\n\n问题：{question}\n\n出发对象：`{start}`")]
    for layer in layers:
        obj, level = layer["object"], layer["level"]
        place = "当前对象" if level == 0 else f"上溯第 {level} 层"
        lines = [f"## {place}：{obj['type_display_name']}《{obj['title']}》 `{obj['ref']}`", *_status(obj)]
        items.append(entry(f"header:{level}", "header", level, obj["object_id"], "\n".join(lines)))
        relations = [f"{relation['field']}：" + "、".join(f"`{ref['ref']}`" for ref in relation["refs"])
                     for relation in layer["relations"] if relation["refs"]]
        relations += [f"被 `{item['source']['ref']}` 以 {item['field']} 引用" for item in layer["referenced_by"]]
        if relations:
            items.append(entry(f"relations:{level}", "relations", level, obj["object_id"],
                               "跨链关系（只列引用，不展开）：\n" + "\n".join(relations)))
        for block in layer["blocks"]:
            items.append(entry(f"block:{block['ref']}", "block", level, obj["object_id"],
                               _block_text(block, f"### {block['display_name']} `{block['ref']}`")))
        if layer["hop"]:
            items.append(entry(f"hop:{layer['hop']['object']['ref']}", "hop", level, layer["hop"]["object"]["object_id"],
                               _hop_text(layer["hop"])))
        if layer["state"]:
            state = layer["state"]
            text = [f"### 最新状态快照（未经确认，截至 {state['as_of']}） `{state['ref']}`"]
            text += [_block_text(block, f"#### {block['display_name']}") for block in state["blocks"]]
            items.append(entry(f"snapshot:{state['ref']}", "snapshot", level, obj["object_id"], "\n".join(text)))
        for event in layer["events"]:
            label = kinds[event["kind"]] + _display("category", event["category"]) + _display("phase", event["phase"]) \
                + _display("outcome", event["outcome"])
            body = f"：{event['content']['text']}" if event["content"] and event["content"]["text"] else ""
            who = f"，{event['principal_name']} 记" + (f"，指派给 {event['assignee_name']}" if event["assignee"] else "")
            items.append(entry(f"event:{event['event_id']}", "event", level, obj["object_id"],
                               f"- {event['occurred_at']} {label}（事件 `{event['event_id']}`{who}）{body}",
                               event["occurred_at"]))
    return items


def _keep(layers: list[dict[str, Any]], kept: set[str]) -> list[dict[str, Any]]:
    """裁剪后的上下文包：只留下没被裁掉的跨链关系、块、多取的一跳、快照与事件。"""
    return [{**layer,
             "relations": layer["relations"] if f"relations:{layer['level']}" in kept else [],
             "referenced_by": layer["referenced_by"] if f"relations:{layer['level']}" in kept else [],
             "blocks": [block for block in layer["blocks"] if f"block:{block['ref']}" in kept],
             "hop": layer["hop"] if layer["hop"] and f"hop:{layer['hop']['object']['ref']}" in kept else None,
             "state": layer["state"] if layer["state"] and f"snapshot:{layer['state']['ref']}" in kept else None,
             "events": [event for event in layer["events"] if f"event:{event['event_id']}" in kept]}
            for layer in layers]


def build(conn: Any, ctx: Any, object_id: str, request: WorldContextRequest) -> dict[str, Any]:
    """取上下文：遍历、渲染、裁剪、判覆盖，并在同一事务里落一行上下文包。"""
    head, _ = readable(conn, ctx, object_id)
    budget = {"max_chars": DEFAULT_MAX_CHARS, "max_events_per_object": DEFAULT_MAX_EVENTS_PER_OBJECT,
              **(request.budget.model_dump(exclude_none=True) if request.budget else {}),
              "recent_days": request.recent_days or DEFAULT_RECENT_DAYS}
    window = conn.execute("SELECT clock_timestamp() - make_interval(days => %s) AS start",
                          (budget["recent_days"],)).fetchone()["start"]

    def latest(row: dict[str, Any]) -> dict[str, Any]:
        return db.jsonable(conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                                        (ctx.scope_id, row["latest_revision_id"])).fetchone())
    start, state = None, None
    if head["object_type"] == "StateSnapshot":
        # 从状态快照出发：当前对象是它的主体，状态取这条快照（而不是主体最新的那条）。
        revision = latest(head)
        subject = revision["payload"]["subject_ref"]["object_id"]
        start = citation(head["object_id"], revision["object_version"])
        state = latest_snapshot(conn, ctx, subject, revision["payload"]["as_of"])
        head, _ = readable(conn, ctx, subject)
    layers: list[dict[str, Any]] = []
    walked: list[tuple[str, dict[str, Any]]] = []  # 沿主干走过的每一步：(引用字段, 钉定的引用)
    side_hops: list[dict[str, Any]] = []  # 主干之外多走的一跳
    seen_events: set[str] = set()
    while True:
        revision = latest(head)
        layer = _layer(conn, ctx, head, revision, len(layers), window, seen_events, None if layers else state)
        hop_field, hop_label = _HOPS.get(head["object_type"], (None, None))
        if hop_field and revision["payload"].get(hop_field):
            # 同主干一样读对方的最新修订，引用字段钉定的版本只作出处。
            target, _ = readable(conn, ctx, revision["payload"][hop_field]["object_id"])
            layer["hop"] = _hop(conn, ctx, target, latest(target), hop_field, hop_label)
            side_hops.append({"from": layer["object"]["ref"], "field": hop_field,
                              "pinned": cited(revision["payload"][hop_field])["ref"], "read": layer["hop"]["object"]["ref"]})
        layers.append(layer)
        parent_field = world_registry.object_spec(head["object_type"])["spine_parent_field"]
        if parent_field is None:
            break
        walked.append((parent_field, revision["payload"][parent_field]))
        head, _ = readable(conn, ctx, revision["payload"][parent_field]["object_id"])
    _name_events(conn, ctx, layers)
    start = start or layers[0]["object"]["ref"]
    result = trim(_items(request.question, start, layers), max_chars=budget["max_chars"],
                  max_events_per_object=budget["max_events_per_object"], lead=lambda kept: guide(_keep(layers, kept)))
    packed = _keep(layers, {item["key"] for item in result["kept"]})
    plan = {
        # 沿主干读的是上一级的最新修订，引用字段钉定的版本只作出处。
        "walked": [{"from": layers[index]["object"]["ref"], "field": field, "pinned": cited(pinned)["ref"],
                    "read": layers[index + 1]["object"]["ref"]} for index, (field, pinned) in enumerate(walked)],
        "hops": side_hops,  # 主干之外多走的一跳（只取定义类块）
        "shown_not_followed": [{"from": layer["object"]["ref"], "field": relation["field"], "to": ref["ref"]}
                               for layer in packed for relation in layer["relations"] for ref in relation["refs"]]
        + [{"from": item["source"]["ref"], "field": item["field"], "to": layer["object"]["ref"]}
           for layer in packed for item in layer["referenced_by"]],
        "taken": [{"kind": item["kind"], "level": item["level"], "key": item["key"]} for item in result["kept"]
                  if item["kind"] in {"relations", "block", "hop", "snapshot", "event"}],
        "trimmed": [{"kind": entry["kind"], "level": entry["level"], "key": entry["key"], "reason": entry["reason"]}
                    for entry in result["trimmed"]],
        "state_and_events_from_levels": [layer["level"] for layer in packed
                                         if layer["level"] == 0 or layer["object"]["object_type"] in _EXECUTION_TYPES],
        "over_budget": result["over_budget"],
    }
    usage = {**budget, "window_start": utc_text(window.isoformat()), "used_chars": result["chars"],
             "estimated_tokens": math.ceil(result["chars"] / CHARS_PER_TOKEN_ESTIMATE),
             "over_budget": result["over_budget"]}
    context_pack = {"question": request.question, "start": start, "layers": packed, "markdown": result["markdown"]}
    coverage = cover(packed)
    row = conn.execute(
        """INSERT INTO gov_world_context_packs (scope_id, principal_id, object_id, question, pack, plan, coverage, budget)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING context_pack_id, created_at""",
        (ctx.scope_id, ctx.principal_id, object_id, request.question, Jsonb(context_pack), Jsonb(plan), Jsonb(coverage),
         Jsonb(usage))).fetchone()
    return {"context_pack_id": str(row["context_pack_id"]), "created_at": utc_text(row["created_at"].isoformat()),
            "object_id": object_id, "question": request.question, "context_pack": context_pack, "plan": plan,
            "coverage": coverage, "budget": usage}
