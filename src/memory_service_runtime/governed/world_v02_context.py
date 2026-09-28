"""tkos.world/0.2 的取上下文（票 #56，契约第 15.3 节）：0.1 的 B 固定路径（票 #25）搬到 0.2 对象上，引用细到组件。

沿用 0.1 的遍历、预算、裁剪、六问覆盖、检索计划、落表与默认值：从一个对象沿主干向上到 Company，每层取最新修订的
全部块（空块用标准句）、生命周期、责任人与跨链关系（只列引用，不递归）；当前对象向上直到 Mission（含）各层另取
最新状态快照与近期事件，一条事件以多层对象为主体时只放在离当前对象最近的那一层。从状态快照出发时，以它的主体为
当前对象，状态取这条快照。整包渲染成 Markdown，按字符预算裁剪（直接用 0.1 的纯函数 trim：每层条数上限先生效，
再先裁最旧的事件，然后从主干最远层起裁；当前对象的块与它的最新快照不裁），输出上下文包、检索计划与六问覆盖三件，
每次调用在 gov_world_context_packs 落一行（上下文包是时间记录，只追加）。

0.2 的改动：
- 引用按组件返回：块里的组件逐条带组件形式的引用 ``对象@版本#块/组件`` 与钉定结构，Markdown 里逐条写出；
  覆盖的依据里，块自己的文字、引用或文档链接给块引用，组件给组件引用；事件给事件引用 ``event:<事件 id>``。
- 生命周期按 0.2 的状态表推导（world_v02_lifecycle.derive），是否正式看内核的正式内容指针；「凭什么」里上层的
  经确认的正式内容只算已正式对象的正式块，活动块不经门、不算。
- 状态快照是 0.2 读侧的外壳视图（主体、时点、周期、生成者、来源事件、payload 类型），标明未经确认。
- 责任人按 0.2 的读投影，注明来自属性还是角色。
- 返回与包都写明契约版本，0.1 与 0.2 的包在同一张表里分得开。

不在本票（票 #64）：Why 多取一跳（单元长期目标到公司级长期目标、Strategy 与 Company），Markdown 按六问组织，
事件行写出记录者与被代记的人，形成时带入已确认的复盘与待带入的问题。0.1 实现里对应的多取一跳、六问指引与事件行的
人名在这里都还没有。
"""
from __future__ import annotations

import math
from typing import Any

from psycopg.types.json import Jsonb

from . import db
from . import world_v02_readers as readers
from . import world_v02_registry as world_registry
from .world_v01_context import trim
from .world_v01_models import WorldContextRequest, utc_text
from .world_v02_models import CONTRACT_VERSION, citation, component_spec

# 契约第 15.3 节（决 18）：沿用 0.1 的默认值，实验后再定。
DEFAULT_MAX_CHARS, DEFAULT_MAX_EVENTS_PER_OBJECT, DEFAULT_RECENT_DAYS = 12000, 10, 30
# token 只估算上报：中文为主的 Markdown 粗按每 2 个字符 1 个 token 计（同 0.1）。
CHARS_PER_TOKEN_ESTIMATE = 2
# 取最新快照与近期事件的层：当前对象，以及它向上直到 Mission（含）的执行链。
_EXECUTION_TYPES = frozenset({"Activity", "Task", "Mission"})
QUESTIONS = {"why": "为什么", "what": "做什么", "who": "谁负责", "now": "现在怎样", "happened": "发生了什么",
             "basis": "凭什么"}
_GAPS = {"why": "主干上层没有取到非空的定义类块", "what": "当前对象的定义类块都是空的",
         "who": "当前对象没有可解析的责任人", "now": "没有取到状态快照", "happened": "窗口内没有取到事件",
         "basis": "没有取到上层经确认的正式内容，也没有取到验收标准或约束"}


# ------------------------------------------------------------ pure: coverage
def _cites(block: dict[str, Any]) -> list[dict[str, str]]:
    """一块内容的出处（引用按组件返回）：块自己的文字、引用或文档链接给块引用，组件逐条给组件引用；空块没有出处。"""
    value = block["value"]
    if value is None:
        return []
    own = value["text"].strip() or value["refs"] or value["artifacts"]
    return ([{"ref": block["ref"]}] if own else []) + [{"ref": item["ref"]} for item in block["components"]]


def cover(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """六问各自答没答（按上下文包里留下的内容判），依据哪些引用，答不了的缺口。第 0 层是当前对象，其余是上层。
    判法同 0.1，依据细到组件、事件给事件引用；「凭什么」的上层正式内容只算已正式对象的正式块。"""
    current, upper = layers[0], layers[1:]

    def cites(chosen: list[dict[str, Any]], test) -> list[dict[str, str]]:
        return [item for layer in chosen for block in layer["blocks"] if test(block) for item in _cites(block)]
    basis = (cites([layer for layer in upper if layer["object"]["formal"]], lambda block: block["class"] == "formal")
             + cites(layers, lambda block: block["id"] in {"acceptance", "constraint"}))
    evidence = {
        "why": cites(upper, lambda block: block["kind"] == "definition"),
        "what": cites([current], lambda block: block["kind"] == "definition"),
        "who": [{"ref": current["object"]["ref"]}] if current["object"]["responsible"]["principals"] else [],
        "now": [{"ref": layer["state"]["ref"]} for layer in layers if layer["state"]],
        "happened": [{"ref": event["ref"]} for layer in layers for event in layer["events"]],
        "basis": [item for index, item in enumerate(basis) if item not in basis[:index]],
    }
    return {name: {"question": QUESTIONS[name], "answered": bool(found), "evidence": found,
                   "gap": None if found else _GAPS[name]} for name, found in evidence.items()}


# ------------------------------------------------------------ assembly from the database
def _pinned(object_id: str, version: int, revision_id: str, block: str | None = None,
            component: str | None = None) -> dict[str, Any]:
    """钉定结构同时给业务形式（契约第 5 节）。"""
    return readers.cited({"object_id": object_id, "object_version": version, "revision_id": revision_id,
                          "block": block, "component": component})


def _pin_block(view: dict[str, Any], object_id: str, version: int, revision_id: str) -> dict[str, Any]:
    """读侧的块视图加上钉定结构：块钉到所读修订的这一块，组件钉到这一块里的这一条。"""
    components = [{**item, "pinned": _pinned(object_id, version, revision_id, view["id"], item["id"])}
                  for item in view["components"]]
    return {**view, "value": view["value"] and {**view["value"], "components": components}, "components": components,
            "pinned": _pinned(object_id, version, revision_id, view["id"])}


def _blocks(head: dict[str, Any], revision: dict[str, Any]) -> list[dict[str, Any]]:
    object_id, version, payload = head["object_id"], revision["object_version"], revision["payload"]
    return [_pin_block(readers.block_view(object_id, version, spec, payload["blocks"][spec["id"]]), object_id, version,
                       revision["revision_id"])
            for spec in world_registry.object_spec(head["object_type"])["blocks"]]


def _state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """状态快照在上下文包里的样子：读侧的外壳视图，快照与它的块、组件都钉到这条快照的修订，标明未经确认。"""
    object_id, version, revision_id = snapshot["object_id"], snapshot["version"], snapshot["revision_id"]
    return {**snapshot, "pinned": _pinned(object_id, version, revision_id),
            "blocks": [_pin_block(block, object_id, version, revision_id) for block in snapshot["blocks"]]}


def _object(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
    """一个对象在上下文包里的表头：类型、标题、钉到所读修订的引用、按 0.2 状态表推导的生命周期、内核的正式内容
    指针（有门类型才有），与按读投影解析、注明来源的责任人。"""
    spec = world_registry.object_spec(head["object_type"])
    object_id, version = head["object_id"], revision["object_version"]
    stage = readers.lifecycle(conn, ctx, head)
    return {"object_id": object_id, "object_type": head["object_type"], "type_display_name": spec["display_name"],
            "title": revision["payload"]["title"], "version": version, "ref": citation(object_id, version),
            "pinned": _pinned(object_id, version, revision["revision_id"]),
            "lifecycle": stage and {key: stage[key] for key in ("status", "display_name", "event_id")},
            "formal": head["lifecycle_status"] == "confirmed" if spec["gated"] else None,
            "responsible": {**spec["responsible"],
                            "principals": readers.responsible_principals(conn, ctx, head, revision["payload"])}}


def _referenced_by(conn: Any, ctx: Any, object_id: str) -> list[dict[str, Any]]:
    """最新修订的跨链关系列表里钉着该对象（任一版本）的对象，逐条列出（同 0.1，关系字段取 0.2 登记）。"""
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
        source = _pinned(row["object_id"], row["object_version"], row["revision_id"])
        found.extend({"field": field, "relation": relation, "source": source, "target": readers.cited(pin)}
                     for field, relation in fields.items() for pin in row["payload"].get(field, [])
                     if pin["object_id"] == object_id)
    return found


def _layer(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any], level: int, window: Any,
           seen_events: set[str], state: dict[str, Any] | None) -> dict[str, Any]:
    spec = world_registry.object_spec(head["object_type"])
    object_id, payload = head["object_id"], revision["payload"]
    layer = {
        "level": level,
        "object": _object(conn, ctx, head, revision),
        "blocks": _blocks(head, revision),
        "relations": [{"field": field["field"], "relation": field["relation"],
                       "refs": readers.cited(payload.get(field["field"], []))}
                      for field in spec["relation_fields"] if field["written_by"] == "world_relate"],
        "referenced_by": _referenced_by(conn, ctx, object_id),
        "state": None, "events": [],
    }
    if level == 0 or head["object_type"] in _EXECUTION_TYPES:
        snapshot = state or readers.latest_snapshot(conn, ctx, object_id)
        layer["state"] = snapshot and _state(snapshot)
        for event in reversed(readers.events(conn, ctx, object_id, window)["events"]):  # 新的在前
            if event["event_id"] not in seen_events:  # 一条事件以多层对象为主体时，只放在离当前对象最近的那一层
                seen_events.add(event["event_id"])
                layer["events"].append({**event, "ref": f"event:{event['event_id']}"})
    return layer


# ------------------------------------------------------------ Markdown
def _values(group: str) -> dict[str, str]:
    """事件属性取值的中文名（登记）。"""
    return {item["id"]: item["display_name"] for item in world_registry.registry()["event_attribute_values"][group]}


def _display(group: str, value: str | None) -> str:
    return "" if value is None else "·" + _values(group)[value]


def _codes(refs: list[dict[str, Any]]) -> str:
    return "、".join(f"`{ref['ref']}`" for ref in refs)


def _attribute_text(attribute: dict[str, Any], value: Any) -> str:
    """组件类型属性的写法：主体写 id，本期条目逐条写时刻、来源与内容，其余照原值。"""
    if attribute["value"] == "principal":
        return f"`{value}`"
    if attribute["value"] == "progress_entries":
        sources = {item["id"]: item["display_name"]
                   for item in world_registry.registry()["components"]["progress_entry_sources"]}
        return "；".join(f"{entry['at']} {sources[entry['source']]}：{entry['text']}"
                        + (f"（{entry['url']}）" if entry.get("url") else "") for entry in value)
    return str(value)


def _component_text(component: dict[str, Any]) -> str:
    """一条组件：类型、组件引用与正文，其下是有值的类型属性、适用范围、引用与文档链接。"""
    spec = component_spec(component["type"])
    lines = [f"- {spec['display_name']} `{component['ref']}`" + (f"：{component['text']}" if component["text"] else "")]
    lines += [f"  {attribute['display_name']}：{_attribute_text(attribute, component['attributes'][attribute['id']])}"
              for attribute in spec["attributes"] if component["attributes"].get(attribute["id"]) not in (None, "", [])]
    if component["scope"]:
        lines.append(f"  适用范围：`{component['scope']['ref']}`")
    if component["refs"]:
        lines.append("  引用：" + _codes(component["refs"]))
    if component["artifacts"]:
        lines.append("  文档：" + "、".join(component["artifacts"]))
    return "\n".join(lines)


def _block_text(block: dict[str, Any], heading: str) -> str:
    """一块：标题带块引用，然后是块自己的文字（空块是标准句）、引用、文档链接，最后逐条写组件。"""
    value = block["value"]
    lines = [heading] + ([block["text"]] if block["text"] else [])
    if value and value["refs"]:
        lines.append("引用：" + _codes(value["refs"]))
    if value and value["artifacts"]:
        lines.append("文档：" + "、".join(value["artifacts"]))
    lines += [_component_text(item) for item in block["components"]]
    return "\n".join(lines)


def _status(obj: dict[str, Any]) -> list[str]:
    """表头里的生命周期（推出它的事件写成事件引用）、责任人（注明来源）与正式内容状态。"""
    stage, responsible = obj["lifecycle"], obj["responsible"]
    source = "属性 responsible" if responsible["source"] == "attribute" else f"角色 {responsible['role']}"
    lines = [f"生命周期：{stage['display_name']}（事件 `event:{stage['event_id']}`）" if stage else "生命周期：无（只有版本）",
             f"责任人（来自{source}）：" + ("、".join(person["display_name"] for person in responsible["principals"])
                                        or "未指派")]
    if obj["formal"] is not None:
        lines.append("正式内容：" + ("已确认" if obj["formal"] else "尚未确认"))
    return lines


def _state_text(state: dict[str, Any]) -> str:
    shell = [f"payload：{state['payload_type']['display_name']}"] + ([f"周期：{state['period']}"] if state["period"] else [])
    shell += [f"生成者：{state['generator']['display_name']}", "来源事件：" + _codes(state["source_event_refs"])]
    lines = [f"### 最新状态快照（未经确认，截至 {state['as_of']}） `{state['ref']}`", "；".join(shell)]
    lines += [_block_text(block, f"#### {block['display_name']} `{block['ref']}`") for block in state["blocks"]]
    return "\n".join(lines)


def _event_text(event: dict[str, Any], kinds: dict[str, str]) -> str:
    """一条事件：发生时刻、种类与取值、事件引用（迟记与原事件照取事件写出）、内容的文字。"""
    label = (kinds[event["kind"]] + _display("category", event["category"]) + _display("outcome", event["outcome"])
             + _display("disposition", event["disposition"]))
    notes = ([f"原事件 `event:{event['supersedes_event_id']}`"] if event["supersedes_event_id"] else []) \
        + (["迟记"] if event["late"] else [])
    body = f"：{event['content']['text']}" if event["content"] and event["content"]["text"] else ""
    return f"- {event['occurred_at']} {label}（事件 `{event['ref']}`" + "".join(f"，{note}" for note in notes) + f"）{body}"


def _items(question: str, start: str, layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把上下文包渲染成按文档顺序排列、可逐条裁剪的 Markdown 片段（条目的形状同 0.1，交给 trim）。"""
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
        relations = [f"{relation['field']}：" + _codes(relation["refs"]) for relation in layer["relations"] if relation["refs"]]
        relations += [f"被 `{item['source']['ref']}` 以 {item['field']} 引用" for item in layer["referenced_by"]]
        if relations:
            items.append(entry(f"relations:{level}", "relations", level, obj["object_id"],
                               "跨链关系（只列引用，不展开）：\n" + "\n".join(relations)))
        for block in layer["blocks"]:
            items.append(entry(f"block:{block['ref']}", "block", level, obj["object_id"],
                               _block_text(block, f"### {block['display_name']} `{block['ref']}`")))
        if layer["state"]:
            items.append(entry(f"snapshot:{layer['state']['ref']}", "snapshot", level, obj["object_id"],
                               _state_text(layer["state"])))
        for event in layer["events"]:
            items.append(entry(event["ref"], "event", level, obj["object_id"], _event_text(event, kinds),
                               event["occurred_at"]))
    return items


def _keep(layers: list[dict[str, Any]], kept: set[str]) -> list[dict[str, Any]]:
    """裁剪后的上下文包：只留下没被裁掉的跨链关系、块、快照与事件。"""
    return [{**layer,
             "relations": layer["relations"] if f"relations:{layer['level']}" in kept else [],
             "referenced_by": layer["referenced_by"] if f"relations:{layer['level']}" in kept else [],
             "blocks": [block for block in layer["blocks"] if f"block:{block['ref']}" in kept],
             "state": layer["state"] if layer["state"] and f"snapshot:{layer['state']['ref']}" in kept else None,
             "events": [event for event in layer["events"] if event["ref"] in kept]}
            for layer in layers]


def build(conn: Any, ctx: Any, object_id: str, request: WorldContextRequest) -> dict[str, Any]:
    """取上下文：遍历、渲染、裁剪、判覆盖，并在同一事务里落一行上下文包。scope 外与找不到的对象是 NOT_FOUND，
    判权与找不到的规则同取对象（契约第 15.1 节）。"""
    head, _ = readers.readable(conn, ctx, object_id)
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
        state = readers.latest_snapshot(conn, ctx, subject, revision["payload"]["as_of"])
        head, _ = readers.readable(conn, ctx, subject)
    layers: list[dict[str, Any]] = []
    walked: list[tuple[str, dict[str, Any]]] = []  # 沿主干走过的每一步：(引用字段, 钉定的引用)
    seen_events: set[str] = set()
    while True:
        revision = latest(head)
        layers.append(_layer(conn, ctx, head, revision, len(layers), window, seen_events, None if layers else state))
        parent_field = world_registry.object_spec(head["object_type"])["spine_parent_field"]
        if parent_field is None:
            break
        walked.append((parent_field, revision["payload"][parent_field]))
        head, _ = readers.readable(conn, ctx, revision["payload"][parent_field]["object_id"])
    start = start or layers[0]["object"]["ref"]
    result = trim(_items(request.question, start, layers), max_chars=budget["max_chars"],
                  max_events_per_object=budget["max_events_per_object"])
    packed = _keep(layers, {item["key"] for item in result["kept"]})
    plan = {
        # 沿主干读的是上一级的最新修订，引用字段钉定的版本（责任单元的是责任单元条目）只作出处。
        "walked": [{"from": layers[index]["object"]["ref"], "field": field, "pinned": readers.cited(pinned)["ref"],
                    "read": layers[index + 1]["object"]["ref"]} for index, (field, pinned) in enumerate(walked)],
        "shown_not_followed": [{"from": layer["object"]["ref"], "field": relation["field"], "to": ref["ref"]}
                               for layer in packed for relation in layer["relations"] for ref in relation["refs"]]
        + [{"from": item["source"]["ref"], "field": item["field"], "to": layer["object"]["ref"]}
           for layer in packed for item in layer["referenced_by"]],
        "taken": [{"kind": item["kind"], "level": item["level"], "key": item["key"]} for item in result["kept"]
                  if item["kind"] in {"relations", "block", "snapshot", "event"}],
        "trimmed": [{"kind": entry["kind"], "level": entry["level"], "key": entry["key"], "reason": entry["reason"]}
                    for entry in result["trimmed"]],
        "state_and_events_from_levels": [layer["level"] for layer in packed
                                         if layer["level"] == 0 or layer["object"]["object_type"] in _EXECUTION_TYPES],
        "over_budget": result["over_budget"],
    }
    usage = {**budget, "window_start": utc_text(window.isoformat()), "used_chars": result["chars"],
             "estimated_tokens": math.ceil(result["chars"] / CHARS_PER_TOKEN_ESTIMATE),
             "over_budget": result["over_budget"]}
    context_pack = {"contract_version": CONTRACT_VERSION, "question": request.question, "start": start,
                    "layers": packed, "markdown": result["markdown"]}
    coverage = cover(packed)
    row = conn.execute(
        """INSERT INTO gov_world_context_packs (scope_id, principal_id, object_id, question, pack, plan, coverage, budget)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING context_pack_id, created_at""",
        (ctx.scope_id, ctx.principal_id, object_id, request.question, Jsonb(context_pack), Jsonb(plan), Jsonb(coverage),
         Jsonb(usage))).fetchone()
    return {"contract_version": CONTRACT_VERSION, "context_pack_id": str(row["context_pack_id"]),
            "created_at": utc_text(row["created_at"].isoformat()), "object_id": object_id, "question": request.question,
            "context_pack": context_pack, "plan": plan, "coverage": coverage, "budget": usage}
