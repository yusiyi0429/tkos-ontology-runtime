"""tkos.world/0.2 的取上下文（票 #56、#64，契约第 15.3 节）：0.1 的 B 固定路径（票 #25）搬到 0.2 对象上，引用细到组件。

沿用 0.1 的遍历、预算、裁剪、六问覆盖、检索计划、落表与默认值：从一个对象沿主干向上到 Company，每层取最新修订的
全部块（空块用标准句）、生命周期、责任人与跨链关系（只列引用，不递归）；当前对象向上直到 Mission（含）各层另取
最新状态快照与近期事件，一条事件以多层对象为主体时只放在离当前对象最近的那一层。从状态快照出发时，以它的主体为
当前对象，状态取这条快照。整包渲染成 Markdown，按字符预算裁剪（直接用 0.1 的纯函数 trim：每层条数上限先生效，
再先裁最旧的事件，然后从主干最远层起逐层裁跨链关系、多取的一跳、块、快照，最后裁当前对象的跨链关系与它多取的
一跳；当前对象的块与它的最新快照不裁；六问指引计入预算，每裁一条按留下的内容重新渲染），输出上下文包、检索计划
与六问覆盖三件，每次调用在 gov_world_context_packs 落一行（上下文包是时间记录，只追加）。

0.2 的改动：
- 引用按组件返回：块里的组件逐条带组件形式的引用 ``对象@版本#块/组件`` 与钉定结构，Markdown 里逐条写出；
  覆盖的依据里，块自己的文字、引用或文档链接给块引用，组件给组件引用；事件给事件引用 ``event:<事件 id>``。
- 生命周期按 0.2 的状态表推导（world_v02_lifecycle.derive），是否正式看内核的正式内容指针；「凭什么」里上层的
  经确认的正式内容只算已正式对象的正式块，活动块不经门、不算。
- 状态快照是 0.2 读侧的外壳视图（主体、时点、周期、生成者、来源事件、payload 类型），标明未经确认。
- 责任人按 0.2 的读投影，注明来自属性还是角色。
- 返回与包都写明契约版本，0.1 与 0.2 的包在同一张表里分得开。
- Why 多取一跳（#64）：主干之外，单元长期目标沿 goal_ref 多取一跳到公司级长期目标，只取它的定义类块（同 0.1
  批次 D），放在该层的 hop 里，检索计划记在 hops。主干本来就经责任单元走到 Strategy 与 Company，所以 Why（覆盖
  与指引）由近及远追到公司级长期目标、Strategy 与 Company；多取的一跳与上溯各层一样算作上层。
- Markdown 按六问组织（#64）：标题之后先是六问指引（同 0.1 批次 D，按问题给出处，只指向包里留下的内容），然后
  以六问为节，每条内容只出现一次，按登记的块类别分节：
  为什么——当前对象之上各层（由近及远）的跨链关系与定义类块，多取的一跳跟在它那一层之后；
  做什么——当前对象的定义类块与各层的计划类块；谁负责——逐层的责任人与来源；
  现在怎样——逐层的生命周期与正式内容，然后是各层的最新状态快照；发生了什么——各层的近期事件；
  凭什么——各层的约束类块。同一层的跨链关系、一跳、块与快照在文档里的先后同 0.1 的分层写法，所以裁剪顺序不变。
- 事件行写出记录者；代记的同时写出被代记的人，指派另写被指派者（#64，同 0.1 批次 D 写人名）。
- 形成时带入（#64 第二段，契约第 15.3 节与补 43）：出发对象是有门类型时，带入待带入的问题（_carried_in），放在
  六问指引之后自成一节，计入「凭什么」的覆盖，预算裁剪不裁。带入已确认的公司复盘与有效的长期目标等 #60。
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from psycopg.types.json import Jsonb

from . import db
from . import world_v02_lifecycle as world_lifecycle
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
# 主干之外多取的一跳（契约第 15.3 节）：类型 -> (字段, 显示名)。单元长期目标的 goal_ref 只能指向公司级长期目标。
_HOPS = {"LongTermGoal": ("goal_ref", "公司级长期目标")}
# 形成周期目标时，「本单元」的对象（补 43）：同一个域里的责任单元、长期目标与周期目标。
_UNIT_TYPES = ["ResponsibilityUnit", "LongTermGoal", "PeriodGoal"]
_CARRIED_NOTE = "处置为带入下次形成或立即重开、此后主受影响对象还没记过门事件的问题：必须看到，不必须采用。"
QUESTIONS = {"why": "为什么", "what": "做什么", "who": "谁负责", "now": "现在怎样", "happened": "发生了什么",
             "basis": "凭什么"}
_GAPS = {"why": "主干上层没有取到非空的定义类块", "what": "当前对象的定义类块都是空的",
         "who": "当前对象没有可解析的责任人", "now": "没有取到状态快照", "happened": "窗口内没有取到事件",
         "basis": "没有取到上层经确认的正式内容，也没有取到验收标准或约束"}
# 六问指引答不了时的缺口（指引的取法与覆盖不同，见 guide）。
_GUIDE_GAPS = {**_GAPS, "now": "当前对象没有生命周期，也没有取到状态快照",
               "basis": "没有取到验收标准、约束、上层已确认的正式内容或文档链接"}


# ------------------------------------------------------------ pure: coverage and the guide
def _reach(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """当前对象之上的内容：上溯各层，以及各层（含当前对象）多取的一跳，由近及远（同 0.1）。"""
    return [part for layer in layers for part in (layer, layer.get("hop")) if part][1:]


def _cites(block: dict[str, Any]) -> list[dict[str, str]]:
    """一块内容的出处（引用按组件返回）：块自己的文字、引用或文档链接给块引用，组件逐条给组件引用；空块没有出处。"""
    value = block["value"]
    if value is None:
        return []
    own = value["text"].strip() or value["refs"] or value["artifacts"]
    return ([{"ref": block["ref"]}] if own else []) + [{"ref": item["ref"]} for item in block["components"]]


def cover(layers: list[dict[str, Any]], carried: list[dict[str, Any]] = ()) -> dict[str, Any]:
    """六问各自答没答（按上下文包里留下的内容判），依据哪些引用，答不了的缺口。第 0 层是当前对象；上溯各层与多取的
    一跳都算上层。判法同 0.1，依据细到组件、事件给事件引用；「凭什么」的上层正式内容只算已正式对象的正式块，形成时
    带入的问题也算「凭什么」，依据是问题组件与处置事件。"""
    current, upper = layers[0], _reach(layers)

    def cites(chosen: list[dict[str, Any]], test) -> list[dict[str, str]]:
        return [item for layer in chosen for block in layer["blocks"] if test(block) for item in _cites(block)]
    basis = (cites([layer for layer in upper if layer["object"]["formal"]], lambda block: block["class"] == "formal")
             + cites(layers, lambda block: block["id"] in {"acceptance", "constraint"})
             + [{"ref": ref} for item in carried for ref in (item["issue_ref"]["ref"], item["disposed_by"]["ref"])])
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


def guide(layers: list[dict[str, Any]], carried: list[dict[str, Any]] = ()) -> str:
    """六问指引（同 0.1 批次 D）：Markdown 开头按问题给出处，内容在下文各节；只指向包里留下的内容，没有就写缺口。

    - 为什么：当前对象之上的各层与多取的一跳，由近及远直到 Company，各给非空的定义类块，没有就给对象。
    - 做什么：当前对象的非空定义类块，与各层非空的计划类块。
    - 谁负责：执行链（当前对象向上直到 Mission）各层的责任人，与当前对象最近一条指派事件。
    - 现在怎样：当前对象的生命周期与各层的最新状态快照。发生了什么：外部事件，其余事件只计条数。
    - 凭什么：形成时带入的问题、非空的验收标准与约束、上层已确认正式内容的对象、执行链上带文档链接的块、快照与事件。

    口径与 cover() 不同（同 0.1）：cover() 是契约的六问判定，这里是给模型的阅读出处。"""
    current, reach = layers[0], _reach(layers)
    executing = [layer for layer in layers if layer["level"] == 0 or layer["object"]["object_type"] in _EXECUTION_TYPES]
    events = [event for layer in layers for event in layer["events"]]

    def place(part: dict[str, Any]) -> str:
        return "当前对象" if part is current else part.get("label") or part["object"]["type_display_name"]

    def refs(part: dict[str, Any], test) -> list[str]:
        return [block["ref"] for block in part["blocks"] if not block["empty"] and test(block)]

    def code(values: list[str]) -> str:
        return "、".join(f"`{value}`" for value in values)

    def defs(part: dict[str, Any]) -> list[str]:
        return refs(part, lambda block: block["kind"] == "definition")

    doing = [(layer, (defs(layer) if layer is current else []) + refs(layer, lambda block: block["kind"] == "plan"))
             for layer in layers]
    who = [f"{place(layer)} `{layer['object']['ref']}`："
           + ("、".join(person["display_name"] for person in layer["object"]["responsible"]["principals"]) or "未指派")
           for layer in executing]
    assigned = next((event for event in current["events"] if event["kind"] == "assign"), None)  # 事件新的在前
    if assigned:
        who[0] += (f"，指派事件 `{assigned['ref']}`（{assigned['occurred_at']}，{_recorder(assigned)} 指派给 "
                   f"{assigned['assignee']['display_name']}）")
    stage = current["object"]["lifecycle"]
    states = [f"{place(layer)} `{layer['state']['ref']}`" for layer in layers if layer["state"]]
    now = ([f"当前对象 {stage['display_name']}（事件 `event:{stage['event_id']}`）"] if stage else []) \
        + (["最新快照 " + "、".join(states) + "（快照都未经确认）"] if states else [])
    external = [event for event in events if event["kind"] == "event.recorded"]
    happened = ("外部事件 " + "、".join(f"`{event['ref']}`（{_values('category')[event['category']]}）"
                                      for event in external)) if external else "窗口内没有外部事件"
    if len(events) > len(external):
        happened += f"；另有 {len(events) - len(external)} 条门、生命周期与其余记录事件"
    standards = [ref for layer in layers
                 for ref in refs(layer, lambda block: block["id"] == "acceptance" or block["kind"] == "constraint")]
    formal = [f"{place(part)} `{part['object']['ref']}`" for part in reach if part["object"]["formal"]]
    linked = [("块", [block["ref"] for layer in executing for block in layer["blocks"]
                     if block["value"] and block["value"]["artifacts"]]),
              ("快照", [block["ref"] for layer in executing if layer["state"] for block in layer["state"]["blocks"]
                       if block["value"] and block["value"]["artifacts"]]),
              ("事件", [event["ref"] for event in events if event["content"] and event["content"]["artifacts"]])]
    basis = ([f"形成时带入的问题 {code([item['issue_ref']['ref'] for item in carried])}"] if carried else []) \
        + ([f"验收标准与约束 {code(standards)}"] if standards else []) \
        + (["上层已确认 " + "、".join(formal)] if formal else []) \
        + (["文档链接在" + "，".join(f"{kind} {code(values)}" for kind, values in linked if values)]
           if any(values for _, values in linked) else [])
    answers = {
        "why": " → ".join(f"{place(part)} {code(defs(part) or [part['object']['ref']])}" for part in reach),
        "what": "；".join(f"{place(layer)} {code(values)}" for layer, values in doing if values),
        "who": "；".join(who),
        "now": "；".join(now),
        "happened": happened if events else "",
        "basis": "；".join(basis),
    }
    return "\n".join(["## 六问指引", "按问题给出处，内容在下文各节。"]
                     + [f"- {QUESTIONS[name]}：{answer or '（缺口）' + _GUIDE_GAPS[name]}"
                        for name, answer in answers.items()])


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
        "hop": None, "state": None, "events": [],
    }
    if level == 0 or head["object_type"] in _EXECUTION_TYPES:
        snapshot = state or readers.latest_snapshot(conn, ctx, object_id)
        layer["state"] = snapshot and _state(snapshot)
        for event in reversed(readers.events(conn, ctx, object_id, window)["events"]):  # 新的在前
            if event["event_id"] not in seen_events:  # 一条事件以多层对象为主体时，只放在离当前对象最近的那一层
                seen_events.add(event["event_id"])
                # 指派事件另带被指派者（detail 里的主体），事件行写出他的名字。
                assignee = (readers.principal(conn, ctx, event["detail"]["principal_id"])
                            if event["kind"] == "assign" else None)
                layer["events"].append({**event, "ref": f"event:{event['event_id']}", "assignee": assignee})
    return layer


def _hop(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    """主干之外多取的一跳：对象表头与它的定义类块（同 0.1：不带约束、跨链关系、状态与事件），块与组件钉到所读修订。"""
    return {"field": field, "label": label, "object": _object(conn, ctx, head, revision),
            "blocks": [block for block in _blocks(head, revision) if block["kind"] == "definition"]}


def _carried_in(conn: Any, ctx: Any, head: dict[str, Any]) -> list[dict[str, Any]]:
    """形成时带入的待带入问题（契约第 15.3 节与补 43；票 #64 第二段）：必须被看到、不必须采用。

    什么时候算「形成」：出发对象（head 是它的对象行）是登记里有门的类型，就按下面的规则带入，不看它当前在哪个生命
    周期段。「此后还没记过门事件」已经让问题自然失效；若只在草稿或进行中的一轮里才带，立即重开的问题在对象还没重开时
    就看不到了，违背「必须被看到」。
    - 从周期目标出发：主受影响对象是本单元的问题，本单元是与它同一个域里的责任单元、长期目标与周期目标；
    - 从其他有门对象（Strategy、长期目标、Mission）出发：主受影响对象是它本身的问题。
    待带入的问题：它的 Issue 事件（#61）推出的状态是已处置，推出它的处置是登记 issue.carried_into_next_formation 里的
    带入下次形成或立即重开，且处置之后主受影响对象没记过门事件（登记里 class 为 gate 的种类）。按处置事件的发生时刻
    与 id 排序。已确认的公司复盘与有效的长期目标（形成周期目标时）等 #60。"""
    if not world_registry.object_spec(head["object_type"])["gated"]:
        return []
    if head["object_type"] == "PeriodGoal":
        primaries = [str(row["object_id"]) for row in conn.execute(
            """SELECT object_id FROM gov_objects WHERE scope_id=%s AND domain_id=%s AND object_type = ANY(%s)
                ORDER BY created_at, object_id""", (ctx.scope_id, head["domain_id"], _UNIT_TYPES)).fetchall()]
    else:
        primaries = [head["object_id"]]
    registry = world_registry.registry()
    carried = []
    for primary in primaries:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in readers.issue_events(conn, ctx, primary):
            grouped.setdefault(event["component_id"], []).append(event)
        for events in grouped.values():
            derived = world_lifecycle.derive(registry, "Issue", events)
            if derived["status"] not in registry["issue"]["lifecycle"]["terminal"]:
                continue
            disposal = next(event for event in events if event["event_id"] == derived["event_id"])
            if disposal["disposition"] in registry["issue"]["carried_into_next_formation"]:
                item = _carried_issue(conn, ctx, disposal["event_id"], disposal["disposition"])
                if item is not None:
                    carried.append(item)
    return sorted(carried, key=lambda item: (datetime.fromisoformat(item["disposed_by"]["occurred_at"].replace(
        "Z", "+00:00")), item["disposed_by"]["event_id"]))


def _carried_issue(conn: Any, ctx: Any, event_id: str, disposition: str) -> dict[str, Any] | None:
    """一条待带入的问题：问题组件（钉到处置事件引用的那条快照）、主受影响对象、核心判断问题、处置与处置事件、理由；
    处置之后主受影响对象记过门事件的为 None。"""
    gates = [item["kind"] for item in world_registry.registry()["event_kinds"] if item["class"] == "gate"]
    row = db.jsonable(conn.execute(
        """SELECT e.event_id, e.occurred_at, e.content, e.subject_refs, e.principal_id,
                  EXISTS (SELECT 1 FROM gov_world_events g
                           WHERE g.scope_id=e.scope_id AND g.contract_version=e.contract_version AND g.kind = ANY(%s)
                             AND g.subject_refs->0->>'object_id' = e.subject_refs->1->>'object_id'
                             AND g.recorded_at > e.recorded_at) AS gated_since
             FROM gov_world_events e WHERE e.scope_id=%s AND e.event_id=%s""",
        (gates, ctx.scope_id, event_id)).fetchone())
    if row["gated_since"]:
        return None
    issue_ref, primary = readers.cited(row["subject_refs"][0]), readers.cited(row["subject_refs"][1])

    def revision(revision_id: str) -> dict[str, Any]:
        return conn.execute("""SELECT o.object_type, r.payload FROM gov_object_revisions r JOIN gov_objects o
                                 ON o.scope_id=r.scope_id AND o.object_id=r.object_id
                                WHERE r.scope_id=%s AND r.revision_id=%s""", (ctx.scope_id, revision_id)).fetchone()
    component = next(item for item in revision(issue_ref["revision_id"])["payload"]["blocks"]["issues"]["components"]
                     if item["id"] == issue_ref["component"])
    target = revision(primary["revision_id"])
    dispositions = {item["id"]: item["display_name"] for item in world_registry.registry()["issue"]["dispositions"]}
    return {"kind": "issue", "issue_ref": issue_ref,
            "primary": {**primary, "object_type": target["object_type"],
                        "type_display_name": world_registry.object_spec(target["object_type"])["display_name"],
                        "title": target["payload"]["title"]},
            "text": component["text"], "core_question": component["attributes"]["core_question"],
            "disposition": {"id": disposition, "display_name": dispositions[disposition]},
            "disposed_by": {"event_id": row["event_id"], "ref": f"event:{row['event_id']}",
                            "occurred_at": utc_text(row["occurred_at"]),
                            "principal": readers.principal(conn, ctx, row["principal_id"])},
            "reason": row["content"]["text"]}


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


def _state_text(state: dict[str, Any], name: str) -> str:
    shell = [f"payload：{state['payload_type']['display_name']}"] + ([f"周期：{state['period']}"] if state["period"] else [])
    shell += [f"生成者：{state['generator']['display_name']}", "来源事件：" + _codes(state["source_event_refs"])]
    lines = [f"### {name} 的最新状态快照（未经确认，截至 {state['as_of']}） `{state['ref']}`", "；".join(shell)]
    lines += [_block_text(block, f"#### {block['display_name']} `{block['ref']}`") for block in state["blocks"]]
    return "\n".join(lines)


def _recorder(event: dict[str, Any]) -> str:
    """谁记的：记录者的名字；代记时写成「记录者 代 被代记的人」（契约第 14 节）。"""
    recorder = event["principal"]["display_name"]
    return f"{recorder} 代 {event['on_behalf_of']['display_name']}" if event["on_behalf_of"] else recorder


def _event_text(event: dict[str, Any], name: str, kinds: dict[str, str]) -> str:
    """一条事件：发生时刻、所在那一层的类型、种类与取值、事件引用、谁记的（代记写两个人，指派另写被指派者；迟记与
    原事件照取事件写出）、内容的文字。"""
    label = (kinds[event["kind"]] + _display("category", event["category"]) + _display("outcome", event["outcome"])
             + _display("disposition", event["disposition"]))
    notes = [f"{_recorder(event)} 记"] + ([f"指派给 {event['assignee']['display_name']}"] if event["assignee"] else []) \
        + ([f"原事件 `event:{event['supersedes_event_id']}`"] if event["supersedes_event_id"] else []) \
        + (["迟记"] if event["late"] else [])
    body = f"：{event['content']['text']}" if event["content"] and event["content"]["text"] else ""
    return f"- {event['occurred_at']} {name} {label}（事件 `{event['ref']}`，" + "，".join(notes) + f"）{body}"


def _hop_text(hop: dict[str, Any]) -> str:
    obj = hop["object"]
    lines = [f"### 沿 {hop['field']} 多取一跳：{hop['label']}《{obj['title']}》 `{obj['ref']}`", *_status(obj)]
    lines += [_block_text(block, f"#### {block['display_name']} `{block['ref']}`") for block in hop["blocks"]]
    return "\n".join(lines)


def _carried_text(item: dict[str, Any]) -> str:
    """一条带入的问题：问题组件引用与正文、主受影响对象、核心判断问题、处置（处置事件、记录者、时刻）与理由。"""
    primary, disposed = item["primary"], item["disposed_by"]
    return "\n".join([
        f"### 问题 `{item['issue_ref']['ref']}`" + (f"：{item['text']}" if item["text"] else ""),
        f"主受影响对象：{primary['type_display_name']}《{primary['title']}》 `{primary['ref']}`",
        f"核心判断问题：{item['core_question']}",
        f"处置：{item['disposition']['display_name']}（事件 `{disposed['ref']}`，{disposed['principal']['display_name']} 记，"
        f"{disposed['occurred_at']}）",
        f"理由：{item['reason']}"])


def _where(layer: dict[str, Any]) -> str:
    return "当前对象" if layer["level"] == 0 else f"上溯第 {layer['level']} 层"


def _who_text(layers: list[dict[str, Any]]) -> str:
    """「谁负责」：逐层的对象与责任人（注明来源）。"""
    lines = []
    for layer in layers:
        obj, responsible = layer["object"], layer["object"]["responsible"]
        source = "属性 responsible" if responsible["source"] == "attribute" else f"角色 {responsible['role']}"
        lines.append(f"- {_where(layer)} {obj['type_display_name']}《{obj['title']}》 `{obj['ref']}` 责任人（来自{source}）："
                     + ("、".join(person["display_name"] for person in responsible["principals"]) or "未指派"))
    return "\n".join(lines)


def _now_text(layers: list[dict[str, Any]]) -> str:
    """「现在怎样」的第一条：逐层的生命周期（推出它的事件写成事件引用）与正式内容状态。"""
    lines = []
    for layer in layers:
        obj, stage = layer["object"], layer["object"]["lifecycle"]
        line = f"- {_where(layer)} {obj['type_display_name']} 生命周期：" + (
            f"{stage['display_name']}（事件 `event:{stage['event_id']}`）" if stage else "无（只有版本）")
        if obj["formal"] is not None:
            line += "，正式内容：" + ("已确认" if obj["formal"] else "尚未确认")
        lines.append(line)
    return "\n".join(lines)


def _items(question: str, start: str, layers: list[dict[str, Any]],
           carried: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把上下文包渲染成按文档顺序排列、可逐条裁剪的 Markdown 片段（条目的形状同 0.1，交给 trim）。

    标题之后由 trim 插入六问指引；形成时带入（有时）自成一节；然后以六问为节。条目按块类别分节：定义类块在当前对象
    是「做什么」、在上层是「为什么」，计划类块是「做什么」，约束类块是「凭什么」；跨链关系与多取的一跳放在「为什么」，
    分别在该层的块之前与之后。这样同一层的条目在文档里的先后与 0.1 的分层写法一致（登记里每类对象的块都是定义类在
    前、计划类其次、约束类最后），trim 的裁剪顺序不变。节名、「谁负责」与「现在怎样」的逐层清单不裁。"""
    kinds = {item["kind"]: item["display_name"] for item in world_registry.registry()["event_kinds"]}

    def entry(key: str, kind: str, level: int, object_id: str | None, text: str,
              occurred_at: str | None = None) -> dict[str, Any]:
        return {"key": key, "kind": kind, "level": level, "object_id": object_id, "occurred_at": occurred_at, "text": text}
    parts: dict[str, list[dict[str, Any]]] = {name: [] for name in QUESTIONS}
    parts["who"].append(entry("who", "header", -1, None, _who_text(layers)))
    parts["now"].append(entry("now", "header", -1, None, _now_text(layers)))
    for layer in layers:
        obj, level, name = layer["object"], layer["level"], layer["object"]["type_display_name"]
        relations = [f"{relation['field']}：" + _codes(relation["refs"]) for relation in layer["relations"] if relation["refs"]]
        relations += [f"被 `{item['source']['ref']}` 以 {item['field']} 引用" for item in layer["referenced_by"]]
        if relations:
            parts["why"].append(entry(f"relations:{level}", "relations", level, obj["object_id"],
                                      f"{name} 的跨链关系（只列引用，不展开）：\n" + "\n".join(relations)))
        for block in layer["blocks"]:
            answers = ("basis" if block["kind"] == "constraint" else
                       "what" if block["kind"] == "plan" or level == 0 else "why")
            parts[answers].append(entry(f"block:{block['ref']}", "block", level, obj["object_id"],
                                         _block_text(block, f"### {name}·{block['display_name']} `{block['ref']}`")))
        if layer["hop"]:
            parts["why"].append(entry(f"hop:{layer['hop']['object']['ref']}", "hop", level,
                                      layer["hop"]["object"]["object_id"], _hop_text(layer["hop"])))
        if layer["state"]:
            parts["now"].append(entry(f"snapshot:{layer['state']['ref']}", "snapshot", level, obj["object_id"],
                                      _state_text(layer["state"], name)))
        parts["happened"] += [entry(event["ref"], "event", level, obj["object_id"], _event_text(event, name, kinds),
                                    event["occurred_at"]) for event in layer["events"]]
    items = [entry("title", "title", -1, None, f"# 上下文\n\n问题：{question}\n\n出发对象：`{start}`")]
    if carried:
        items.append(entry("section:carried", "section", -1, None, f"## 形成时带入\n{_CARRIED_NOTE}"))
        items += [entry(f"carried:{item['disposed_by']['ref']}", "carried", 0, item["primary"]["object_id"],
                        _carried_text(item)) for item in carried]
    for name, display in QUESTIONS.items():
        # 取的时候就没有内容的节，节名下写出缺口；被裁空的节只留节名，缺口见六问指引。
        items.append(entry(f"section:{name}", "section", -1, None,
                           f"## {display}" + ("" if parts[name] else f"\n{_GAPS[name]}")))
        items += parts[name]
    return items


def _keep(layers: list[dict[str, Any]], kept: set[str]) -> list[dict[str, Any]]:
    """裁剪后的上下文包：只留下没被裁掉的跨链关系、块、多取的一跳、快照与事件。"""
    return [{**layer,
             "relations": layer["relations"] if f"relations:{layer['level']}" in kept else [],
             "referenced_by": layer["referenced_by"] if f"relations:{layer['level']}" in kept else [],
             "blocks": [block for block in layer["blocks"] if f"block:{block['ref']}" in kept],
             "hop": layer["hop"] if layer["hop"] and f"hop:{layer['hop']['object']['ref']}" in kept else None,
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
    carried = _carried_in(conn, ctx, head)
    layers: list[dict[str, Any]] = []
    walked: list[tuple[str, dict[str, Any]]] = []  # 沿主干走过的每一步：(引用字段, 钉定的引用)
    hops: list[dict[str, Any]] = []  # 主干之外多取的一跳
    seen_events: set[str] = set()
    while True:
        revision = latest(head)
        layer = _layer(conn, ctx, head, revision, len(layers), window, seen_events, None if layers else state)
        hop_field, hop_label = _HOPS.get(head["object_type"], (None, None))
        if hop_field and revision["payload"].get(hop_field):
            # 同主干一样读对方的最新修订，引用字段钉定的版本只作出处。
            target, _ = readers.readable(conn, ctx, revision["payload"][hop_field]["object_id"])
            layer["hop"] = _hop(conn, ctx, target, latest(target), hop_field, hop_label)
            hops.append({"from": layer["object"]["ref"], "field": hop_field,
                         "pinned": readers.cited(revision["payload"][hop_field])["ref"],
                         "read": layer["hop"]["object"]["ref"]})
        layers.append(layer)
        parent_field = world_registry.object_spec(head["object_type"])["spine_parent_field"]
        if parent_field is None:
            break
        walked.append((parent_field, revision["payload"][parent_field]))
        head, _ = readers.readable(conn, ctx, revision["payload"][parent_field]["object_id"])
    start = start or layers[0]["object"]["ref"]
    result = trim(_items(request.question, start, layers, carried), max_chars=budget["max_chars"],
                  max_events_per_object=budget["max_events_per_object"],
                  lead=lambda kept: guide(_keep(layers, kept), carried))
    packed = _keep(layers, {item["key"] for item in result["kept"]})
    plan = {
        # 沿主干读的是上一级的最新修订，引用字段钉定的版本（责任单元的是责任单元条目）只作出处。
        "walked": [{"from": layers[index]["object"]["ref"], "field": field, "pinned": readers.cited(pinned)["ref"],
                    "read": layers[index + 1]["object"]["ref"]} for index, (field, pinned) in enumerate(walked)],
        "hops": hops,  # 主干之外多取的一跳（只取定义类块）
        "shown_not_followed": [{"from": layer["object"]["ref"], "field": relation["field"], "to": ref["ref"]}
                               for layer in packed for relation in layer["relations"] for ref in relation["refs"]]
        + [{"from": item["source"]["ref"], "field": item["field"], "to": layer["object"]["ref"]}
           for layer in packed for item in layer["referenced_by"]],
        "taken": [{"kind": item["kind"], "level": item["level"], "key": item["key"]} for item in result["kept"]
                  if item["kind"] in {"relations", "block", "hop", "snapshot", "event", "carried"}],
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
                    "layers": packed, "carried": carried, "markdown": result["markdown"]}
    coverage = cover(packed, carried)
    row = conn.execute(
        """INSERT INTO gov_world_context_packs (scope_id, principal_id, object_id, question, pack, plan, coverage, budget)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING context_pack_id, created_at""",
        (ctx.scope_id, ctx.principal_id, object_id, request.question, Jsonb(context_pack), Jsonb(plan), Jsonb(coverage),
         Jsonb(usage))).fetchone()
    return {"contract_version": CONTRACT_VERSION, "context_pack_id": str(row["context_pack_id"]),
            "created_at": utc_text(row["created_at"].isoformat()), "object_id": object_id, "question": request.question,
            "context_pack": context_pack, "plan": plan, "coverage": coverage, "budget": usage}
