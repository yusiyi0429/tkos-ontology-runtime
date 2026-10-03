"""tkos.world/0.2 的取上下文（票 #56、#64、#70，契约第 15.3 节）：0.1 的 B 固定路径（票 #25）搬到 0.2 对象上，引用细到组件。

沿用 0.1 的遍历、六问覆盖、检索计划与落表：从一个对象沿主干向上到 Company，每层取最新修订的
全部块（空块用标准句）、生命周期、责任人与跨链关系（只列引用，不递归）；当前对象向上直到 Mission（含）各层另取
最新状态快照与近期事件，一条事件以多层对象为主体时只放在离当前对象最近的那一层。从状态快照出发时，以它的主体为
当前对象，状态取这条快照。整包渲染成 Markdown，按字符预算裁剪（0.2 自己的纯函数 trim，见它的说明；0.1 的 trim
冻结不改），输出上下文包、检索计划与六问覆盖三件，每次调用在 gov_world_context_packs 落一行（上下文包是时间记录，
只追加）。

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
  以六问为节，每条内容只出现一次，块按登记的块类（kind）分节：
  为什么——当前对象之上各层（由近及远）的跨链关系与定义类块，多取的一跳跟在它那一层之后；
  做什么——当前对象的定义类块与各层的计划类块，出发对象是 Mission 或责任单元时另有它的投影项；
  谁负责——逐层的责任人与来源；现在怎样——逐层的生命周期与正式内容，然后是各层的最新状态快照；
  发生了什么——各层的近期事件；凭什么——逐层带约束与验收角色的组件引用。同一层的跨链关系、一跳、块与快照在文档
  里的先后同 0.1 的分层写法。
- 约束、验收与贡献按组件类型的取上下文角色（登记 components.types[].context_role）取，精确到组件引用（#79，契约
  第 4、15.3 节，映射表第 1 节第 4 条），不再按块 id acceptance 或块类 constraint 取：约束沿主干逐层读，约束与验收
  计入「凭什么」，当前对象的贡献计入「为什么」。约束不再单独成块，所以「凭什么」一节是逐层的组件引用清单，内容在
  它们所在的块里，不重复。
- 投影项（#79，契约第 15.1 节，映射表对齐点 5）：出发对象是 Mission 时给「Task 预期结果与质量标准」，是责任单元时
  给「战役引用」，读取时从下级对象投影（world_v02_readers.projections），不存；放在「做什么」，预算裁剪不裁。
- 事件行写出记录者；代记的同时写出被代记的人，指派另写被指派者（#64，同 0.1 批次 D 写人名）。
- 形成时带入（#64 第二段，契约第 15.3 节与补 43，见 _carried_in）：出发对象是有门类型时，带入待带入的问题；从周期
  目标出发另带本 scope 最近的已确认公司复盘与本单元有效的长期目标。放在六问指引之后自成一节，预算裁剪不裁；复盘与
  问题计入「凭什么」的覆盖，有效的长期目标计入「为什么」。
- 预算与裁剪（#70，契约第 15.3 节、补 48 与决 18）：默认预算 100000 字符（0.2 预算只报成本、不作门；12000 是 0.1
  为预算作门定的），每个对象 10 条事件与近期 30 天沿用 0.1。超预算时的裁剪保护 Why 链（见 why_keys 与 trim）：先
  由远及近裁上层不在 Why 链上的块，再裁最旧的事件，再由远及近裁其余内容，Why 链上的项最后才裁。每个对象的事件
  条数上限不计当前对象最近一次指派事件与推出它当前生命周期的事件（见 uncapped_events）。
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Callable, Collection

from psycopg.types.json import Jsonb

from . import db
from . import world_v02_lifecycle as world_lifecycle
from . import world_v02_readers as readers
from . import world_v02_registry as world_registry
from .world_v01_context import _markdown, _moment
from .world_v01_models import WorldContextRequest, utc_text
from .world_v02_models import CONTRACT_VERSION, citation, component_spec

# 契约第 15.3 节（决 18）：0.2 预算只报成本、不作门，默认 100000 字符；0.1 的 12000 是为预算作门定的。每个对象的
# 事件条数与近期窗口沿用 0.1 的默认值。
DEFAULT_MAX_CHARS, DEFAULT_MAX_EVENTS_PER_OBJECT, DEFAULT_RECENT_DAYS = 100000, 10, 30
# token 只估算上报：中文为主的 Markdown 粗按每 2 个字符 1 个 token 计（同 0.1）。
CHARS_PER_TOKEN_ESTIMATE = 2
# 取最新快照与近期事件的层：当前对象，以及它向上直到 Mission（含）的执行链。
_EXECUTION_TYPES = frozenset({"Activity", "Task", "Mission"})
# 主干之外多取的一跳（契约第 15.3 节）：类型 -> (字段, 显示名)。单元长期目标的 goal_ref 只能指向公司级长期目标。
_HOPS = {"LongTermGoal": ("goal_ref", "公司级长期目标")}
# 形成周期目标时，「本单元」的对象（补 43）：同一个域里的责任单元、长期目标与周期目标。
_UNIT_TYPES = ["ResponsibilityUnit", "LongTermGoal", "PeriodGoal"]
_PENDING = "处置为带入下次形成或立即重开、此后主受影响对象还没记过门事件的问题"
_CARRIED_NOTES = {"PeriodGoal": "形成周期目标时必须看到、不必须采用：本 scope 最近的已确认公司复盘、本单元有效的长期目标，"
                                f"以及{_PENDING}（主受影响对象是责任单元的，看本单元的周期目标此后有没有记过门事件）。",
                  None: f"形成时必须看到、不必须采用：{_PENDING}。"}
# 公司复盘里带入的块：材料放的是 Agent 起草的内容与候选稿（契约第 7 节），不是复盘本身，不带。
_REVIEW_LEFT_OUT = {"materials"}
# 取上下文角色（契约第 4 节）：「凭什么」取约束与验收，「为什么」另取当前对象的贡献。
BASIS_ROLES, WHY_ROLES = ("constraint", "acceptance"), ("contribution",)
QUESTIONS = {"why": "为什么", "what": "做什么", "who": "谁负责", "now": "现在怎样", "happened": "发生了什么",
             "basis": "凭什么"}
_GAPS = {"why": "主干上层没有取到非空的定义类块", "what": "当前对象的定义类块都是空的",
         "who": "当前对象没有可解析的责任人", "now": "没有取到状态快照", "happened": "窗口内没有取到事件",
         "basis": "没有取到上层经确认的正式内容，也没有取到验收标准或约束"}
# 六问指引答不了时的缺口（指引的取法与覆盖不同，见 guide）。
_GUIDE_GAPS = {**_GAPS, "now": "当前对象没有生命周期，也没有取到状态快照",
               "basis": "没有取到验收标准、约束、上层已确认的正式内容或文档链接"}


# ------------------------------------------------------------ pure: budget and trimming（#70）
def why_keys(layers: list[dict[str, Any]], walked: list[tuple[str, dict[str, Any]]]) -> set[str]:
    """Why 链上的项（契约第 15.3 节与补 48），按条目的 key 给出，裁剪时最后才裁。只看当前对象之上的内容，即上溯
    各层（level > 0）的块与各层（含当前对象）多取的一跳；当前对象的块本来就不裁。以裁剪的单位（块、多取的一跳）判：

    - 定义类的块（登记里 kind 为 definition），也就是 cover() 与 guide() 回答「为什么」所用的那些；
    - 被更近的层钉到的块：有引用钉到这一块或其中某个组件，引用来自沿主干走的那一步的引用字段（parent_ref、goal_ref、
      architecture_ref，walked 里第 i 步算第 i 层），或来自包里任一块（不论类别）的块值引用与组件引用（多取的一跳里
      的块算它那一层），且引用所在的层号小于这一块所在的层号。按对象 id 与块 id 认，不看版本：包读的是各层的最新
      修订，引用钉定的版本只作出处。只钉到对象本身的引用不另指块，那个对象的定义类块已按上一条算；事件引用不指块；
    - 多取的一跳整条算：它只带定义类块。

    按现在的登记，主干的引用字段钉的是对象或责任单元条目（在定义类的战略责任结构块里），正式块都是定义类（#79 起约束
    不再单独成块），所以第二条实际多出来的，是块值与组件引用钉到的计划类块（Task 全景、Activity 全景）。裁剪以块为
    单位：块里只要有一条组件被钉到，整块都算。"""
    targets = {(layer["object"]["object_id"], block["id"]): (layer["level"], f"block:{block['ref']}")
               for layer in layers if layer["level"] > 0 for block in layer["blocks"]}
    keys = {f"hop:{layer['hop']['object']['ref']}" for layer in layers if layer["hop"]}
    keys |= {f"block:{block['ref']}" for layer in layers if layer["level"] > 0 for block in layer["blocks"]
             if block["kind"] == "definition"}
    pins = [(level, pinned) for level, (_, pinned) in enumerate(walked)]
    for layer in layers:
        for part in (layer, layer["hop"]):
            for block in part["blocks"] if part else []:
                pins += [(layer["level"], pinned) for pinned in (block["value"]["refs"] if block["value"] else [])]
                pins += [(layer["level"], pinned) for item in block["components"] for pinned in item["refs"]]
    for level, pinned in pins:  # 事件引用没有对象 id
        target = targets.get((pinned.get("object_id"), pinned.get("block")))
        if target and target[0] > level:
            keys.add(target[1])
    return keys


def uncapped_events(layers: list[dict[str, Any]]) -> set[str]:
    """每个对象的事件条数上限不计的事件（契约第 15.3 节），按条目的 key（事件引用）给出：当前对象（第 0 层）最近一次
    指派事件，与推出它当前生命周期的那条事件（读投影 records.lifecycle.event_id）。两条都只在近期窗口里取到时才在包里；
    它们不占上限，也就不会被上限挤掉，其余事件照旧按上限留最新的。预算裁剪不另保护它们。"""
    current = layers[0]
    stage = current["object"]["lifecycle"]
    assigned = next((event for event in current["events"] if event["kind"] == "assign"), None)  # 事件新的在前
    return {event["ref"] for event in current["events"]
            if event is assigned or (stage is not None and event["event_id"] == stage["event_id"])}


def trim(items: list[dict[str, Any]], *, max_chars: int, max_events_per_object: int,
         why: Collection[str] = frozenset(), uncapped: Collection[str] = frozenset(),
         lead: Callable[[set[str]], str] | None = None) -> dict[str, Any]:
    """按文档顺序给出的条目（形状同 0.1）裁到预算内；返回留下的条目、裁掉的条目（带原因）、渲染结果与字符数。0.1 的
    trim 冻结不改，这里是 0.2 自己的顺序（契约第 15.3 节与补 48）。why 是 Why 链上的项（why_keys），uncapped 是
    不计入每对象事件上限的事件（uncapped_events）。lead 按留下条目的 key 渲染六问指引，计入字符预算，每裁一条重新
    渲染（同 0.1）。

    每个对象的事件条数上限先生效：除 uncapped 外，每个对象按发生时刻留最新的 max_events_per_object 条，其余记
    over_level_cap。仍超预算的，依次裁下面四步，裁到不超为止，都记 over_budget：
    1. 上溯各层里不在 Why 链上的块，由远及近，同一层从后往前；
    2. 最旧的事件，不分层；
    3. 其余内容由远及近：上溯各层的跨链关系、不在 Why 链上的多取一跳、快照，最后是当前对象的跨链关系与它不在 Why
       链上的多取一跳（按 why_keys，多取的一跳都在 Why 链上，这里实际没有）；
    4. Why 链上的项由远及近：同一层先裁多取的一跳，再从后往前裁块；当前对象多取的一跳最后裁。
    永不裁：当前对象的块与它的最新快照、形成时带入、标题与节名、「谁负责」「现在怎样」的逐层清单。顺序只由条目的
    文档顺序、层号与事件的发生时刻决定，同样的输入裁出同样的结果。"""
    kept = list(items)
    trimmed: list[dict[str, Any]] = []

    def drop(item: dict[str, Any], reason: str) -> None:
        kept.remove(item)
        trimmed.append({"key": item["key"], "kind": item["kind"], "level": item["level"], "reason": reason})

    counted: dict[str, int] = {}
    for item in sorted((item for item in items if item["kind"] == "event" and item["key"] not in uncapped),
                       key=_moment, reverse=True):
        counted[item["object_id"]] = counted.get(item["object_id"], 0) + 1
        if counted[item["object_id"]] > max_events_per_object:
            drop(item, "over_level_cap")

    def at(level: int, kinds: set[str], chosen: bool, backwards: bool = False) -> list[dict[str, Any]]:
        """这一层里这几类条目，按是否在 Why 链上挑，按文档顺序（backwards 为从后往前）。"""
        return [item for item in (reversed(kept) if backwards else kept)
                if item["level"] == level and item["kind"] in kinds and (item["key"] in why) is chosen]
    far = sorted({item["level"] for item in items if item["level"] > 0}, reverse=True)
    order = [item for level in far for item in at(level, {"block"}, False, backwards=True)]
    order += sorted((item for item in kept if item["kind"] == "event"), key=_moment)
    order += [item for level in far for kinds in ({"relations", "hop"}, {"snapshot"}) for item in at(level, kinds, False)]
    order += at(0, {"relations", "hop"}, False)
    order += [item for level in far for item in at(level, {"hop"}, True) + at(level, {"block"}, True, backwards=True)]
    order += at(0, {"hop"}, True)
    for item in order:
        if len(_markdown(kept, lead)) <= max_chars:
            break
        drop(item, "over_budget")
    markdown = _markdown(kept, lead)
    return {"kept": kept, "trimmed": trimmed, "markdown": markdown, "chars": len(markdown),
            "over_budget": len(markdown) > max_chars}


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


def _role(component: dict[str, Any]) -> str | None:
    """组件类型登记的取上下文角色，没有为 None。"""
    return component_spec(component["type"]).get("context_role")


def _role_refs(layers: list[dict[str, Any]], roles: Collection[str]) -> list[dict[str, str]]:
    """各层块里带这些角色的组件，逐条给组件引用（按层、块与组件的顺序）。"""
    return [{"ref": item["ref"]} for layer in layers for block in layer["blocks"] for item in block["components"]
            if _role(item) in roles]


def cover(layers: list[dict[str, Any]], carried: dict[str, Any] | None = None) -> dict[str, Any]:
    """六问各自答没答（按上下文包里留下的内容判），依据哪些引用，答不了的缺口。第 0 层是当前对象；上溯各层与多取的
    一跳都算上层。判法同 0.1，依据细到组件、事件给事件引用；「凭什么」的上层正式内容只算已正式对象的正式块。约束、
    验收与贡献按组件的取上下文角色取（#79）：主干各层（不含多取的一跳）带约束与验收角色的组件算「凭什么」，当前对象
    带贡献角色的组件算「为什么」。形成时带入的也计入：公司复盘（快照与它有内容的块、组件）与待带入的问题（问题组件与
    处置事件）算「凭什么」，本单元有效的长期目标（非空的定义类块）算「为什么」。"""
    current, upper = layers[0], _reach(layers)
    carried = carried or {}
    review = carried.get("company_review")

    def cites(chosen: list[dict[str, Any]], test) -> list[dict[str, str]]:
        return [item for layer in chosen for block in layer["blocks"] if test(block) for item in _cites(block)]
    basis = (cites([layer for layer in upper if layer["object"]["formal"]], lambda block: block["class"] == "formal")
             + _role_refs(layers, BASIS_ROLES)
             + ([{"ref": review["snapshot"]["ref"]}] + [item for block in review["snapshot"]["blocks"]
                                                           for item in _cites(block)] if review else [])
             + [{"ref": ref} for item in carried.get("issues", [])
                for ref in (item["issue_ref"]["ref"], item["disposed_by"]["ref"])])
    evidence = {
        "why": _role_refs([current], WHY_ROLES) + cites(upper, lambda block: block["kind"] == "definition")
        + [{"ref": block["ref"]} for goal in carried.get("long_term_goals", [])
           for block in goal["definition_refs"] if not block["empty"]],
        "what": cites([current], lambda block: block["kind"] == "definition"),
        "who": [{"ref": current["object"]["ref"]}] if current["object"]["responsible"]["principals"] else [],
        "now": [{"ref": layer["state"]["ref"]} for layer in layers if layer["state"]],
        "happened": [{"ref": event["ref"]} for layer in layers for event in layer["events"]],
        "basis": [item for index, item in enumerate(basis) if item not in basis[:index]],
    }
    return {name: {"question": QUESTIONS[name], "answered": bool(found), "evidence": found,
                   "gap": None if found else _GAPS[name]} for name, found in evidence.items()}


def guide(layers: list[dict[str, Any]], carried: dict[str, Any] | None = None) -> str:
    """六问指引（同 0.1 批次 D）：Markdown 开头按问题给出处，内容在下文各节；只指向包里留下的内容，没有就写缺口。

    - 为什么：当前对象带贡献角色的组件；当前对象之上的各层与多取的一跳，由近及远直到 Company，各给非空的定义类块，
      没有就给对象。
    - 做什么：当前对象的非空定义类块，与各层非空的计划类块；出发对象的投影项。
    - 谁负责：执行链（当前对象向上直到 Mission）各层的责任人，与当前对象最近一条指派事件。
    - 现在怎样：当前对象的生命周期与各层的最新状态快照。发生了什么：外部事件，其余事件只计条数。
    - 凭什么：形成时带入的公司复盘与问题、各层带验收与约束角色的组件、上层已确认正式内容的对象、执行链上带文档链接
      的块、快照与事件。为什么另指形成时带入的有效长期目标。

    口径与 cover() 不同（同 0.1）：cover() 是契约的六问判定，这里是给模型的阅读出处。"""
    current, reach = layers[0], _reach(layers)
    executing = [layer for layer in layers if layer["level"] == 0 or layer["object"]["object_type"] in _EXECUTION_TYPES]
    events = [event for layer in layers for event in layer["events"]]
    carried = carried or {}
    review, goals, pending = carried.get("company_review"), carried.get("long_term_goals", []), carried.get("issues", [])

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
    projection = current.get("projection")
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
    standards = [item["ref"] for item in _role_refs(layers, BASIS_ROLES)]
    contributions = [item["ref"] for item in _role_refs([current], WHY_ROLES)]
    formal = [f"{place(part)} `{part['object']['ref']}`" for part in reach if part["object"]["formal"]]
    linked = [("块", [block["ref"] for layer in executing for block in layer["blocks"]
                     if block["value"] and block["value"]["artifacts"]]),
              ("快照", [block["ref"] for layer in executing if layer["state"] for block in layer["state"]["blocks"]
                       if block["value"] and block["value"]["artifacts"]]),
              ("事件", [event["ref"] for event in events if event["content"] and event["content"]["artifacts"]])]
    basis = ([f"形成时带入的公司复盘 `{review['snapshot']['ref']}`"] if review else []) \
        + ([f"形成时带入的问题 {code([item['issue_ref']['ref'] for item in pending])}"] if pending else []) \
        + ([f"验收标准与约束 {code(standards)}"] if standards else []) \
        + (["上层已确认 " + "、".join(formal)] if formal else []) \
        + (["文档链接在" + "，".join(f"{kind} {code(values)}" for kind, values in linked if values)]
           if any(values for _, values in linked) else [])
    answers = {
        "why": "；".join(part for part in (
            f"当前对象的贡献 {code(contributions)}" if contributions else "",
            " → ".join(f"{place(part)} {code(defs(part) or [part['object']['ref']])}" for part in reach),
            f"形成时带入的有效长期目标 {code([goal['ref'] for goal in goals])}" if goals else "") if part),
        "what": "；".join([f"{place(layer)} {code(values)}" for layer, values in doing if values]
                         + ([f"当前对象的投影项「{projection['display_name']}」"
                             f"（{len(projection['items'])} 项，读取时从下级对象投影）"] if projection else [])),
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
    return [_pin_block(readers.block_view(object_id, version, spec, payload["blocks"].get(spec["id"])), object_id, version,
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
        "referenced_by": readers.referenced_by(conn, ctx, object_id),
        "hop": None, "state": None, "events": [],
        # 投影项只给出发对象（契约第 15.3 节）：Mission 的 Task 预期结果与质量标准、责任单元的战役引用。
        "projection": readers.projections(conn, ctx, head) if level == 0 else None,
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


def _latest(conn: Any, ctx: Any, head: dict[str, Any]) -> dict[str, Any]:
    return db.jsonable(conn.execute("SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
                                    (ctx.scope_id, head["latest_revision_id"])).fetchone())


def _hop(conn: Any, ctx: Any, head: dict[str, Any], revision: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    """主干之外多取的一跳：对象表头与它的定义类块（同 0.1：不带约束、跨链关系、状态与事件），块与组件钉到所读修订。"""
    return {"field": field, "label": label, "object": _object(conn, ctx, head, revision),
            "blocks": [block for block in _blocks(head, revision) if block["kind"] == "definition"]}


def _carried_in(conn: Any, ctx: Any, head: dict[str, Any], anchored: str | None) -> dict[str, Any] | None:
    """形成时带入（契约第 15.3 节与补 43；票 #64 第二段）：必须被看到、不必须采用。出发对象（head 是它的对象行）没有门
    时为 None。

    什么时候算「形成」：出发对象是登记里有门的类型，就按下面的规则带入，不看它当前在哪个生命周期段。「此后还没记过
    门事件」已经让问题自然失效；若只在草稿或进行中的一轮里才带，立即重开的问题在对象还没重开时就看不到了，违背「必须
    被看到」。
    - 从周期目标出发：本 scope 最近的已确认公司复盘（#60；同一主体多条时以 as_of 最新的为准，没有为 None）、本单元
      （与它同一个域）里当前已确认的长期目标（草稿与已终止都不算；anchored 是它 goal_ref 指的那条，已在「为什么」
      里，不重复带），以及主受影响对象是本单元（同域的责任单元、长期目标与周期目标）的待带入问题。公司级长期目标
      不另带：它经单元长期目标的 goal_ref 进来（「为什么」多取的一跳），别的公司级目标不是本单元形成的依据。
    - 从其他有门对象（Strategy、长期目标、Mission）出发：主受影响对象是它本身的待带入问题。
    待带入的问题：它的 Issue 事件（#61）推出的状态是已处置，推出它的处置是登记 issue.carried_into_next_formation 里的
    带入下次形成或立即重开，且处置之后主受影响对象没记过门事件（登记里 class 为 gate 的种类）；主受影响对象是责任单元
    的（它没有门），看本单元任一周期目标在处置之后有没有记过门事件（补 46，#69）。按处置事件的发生时刻与 id 排序；长期
    目标按建立的先后与 id 排序。"""
    if not world_registry.object_spec(head["object_type"])["gated"]:
        return None
    if head["object_type"] != "PeriodGoal":
        return {"issues": _pending_issues(conn, ctx, [head["object_id"]])}
    unit = [db.jsonable(row) for row in conn.execute(
        """SELECT * FROM gov_objects WHERE scope_id=%s AND domain_id=%s AND object_type = ANY(%s)
            ORDER BY created_at, object_id""", (ctx.scope_id, head["domain_id"], _UNIT_TYPES)).fetchall()]
    review = readers.confirmed_company_review(conn, ctx)
    if review is not None:  # 钉到这条快照的修订，材料不带
        snapshot = _state(review["snapshot"])
        review = {**review, "snapshot": {**snapshot, "blocks": [
            block for block in snapshot["blocks"] if block["id"] not in _REVIEW_LEFT_OUT]}}
    goals = [row for row in unit if row["object_type"] == "LongTermGoal" and row["object_id"] != anchored
             and readers.lifecycle(conn, ctx, row)["status"] == "confirmed"]
    return {"company_review": review, "long_term_goals": [_carried_goal(conn, ctx, row) for row in goals],
            "issues": _pending_issues(conn, ctx, [row["object_id"] for row in unit])}


def _carried_goal(conn: Any, ctx: Any, head: dict[str, Any]) -> dict[str, Any]:
    """一条带入的有效长期目标：对象表头（钉到最新修订）与它定义类块的引用（钉定，注明是否为空），不带块的内容。"""
    revision = _latest(conn, ctx, head)
    return {**_object(conn, ctx, head, revision),
            "definition_refs": [{key: block[key] for key in ("id", "display_name", "ref", "pinned", "empty")}
                                for block in _blocks(head, revision) if block["kind"] == "definition"]}


def _pending_issues(conn: Any, ctx: Any, primaries: list[str]) -> list[dict[str, Any]]:
    """主受影响对象在 primaries 里的待带入问题，按处置事件的发生时刻与 id 排序。"""
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
    处置之后已失效的为 None。失效（补 43、46）：处置之后主受影响对象记过门事件；主受影响对象是责任单元时（它没有门），
    改看本单元（同一个域）任一周期目标在处置之后记过门事件。"""
    gates = [item["kind"] for item in world_registry.registry()["event_kinds"] if item["class"] == "gate"]
    row = db.jsonable(conn.execute(
        """SELECT e.event_id, e.occurred_at, e.content, e.subject_refs, e.principal_id, e.on_behalf_of,
                  EXISTS (SELECT 1 FROM gov_world_events g
                           WHERE g.scope_id=e.scope_id AND g.contract_version=e.contract_version AND g.kind = ANY(%s)
                             AND g.recorded_at > e.recorded_at
                             AND (g.subject_refs->0->>'object_id' = e.subject_refs->1->>'object_id'
                                  OR EXISTS (SELECT 1 FROM gov_objects u JOIN gov_objects p
                                               ON p.scope_id=u.scope_id AND p.domain_id=u.domain_id
                                              AND p.object_type='PeriodGoal'
                                             WHERE u.scope_id=e.scope_id AND u.object_type='ResponsibilityUnit'
                                               AND u.object_id::text = e.subject_refs->1->>'object_id'
                                               AND p.object_id::text = g.subject_refs->0->>'object_id'))) AS gated_since
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
    return {"issue_ref": issue_ref,
            "primary": {**primary, "object_type": target["object_type"],
                        "type_display_name": world_registry.object_spec(target["object_type"])["display_name"],
                        "title": target["payload"]["title"]},
            "text": component["text"], "core_question": component["attributes"]["core_question"],
            "disposition": {"id": disposition, "display_name": dispositions[disposition]},
            "disposed_by": {"event_id": row["event_id"], "ref": f"event:{row['event_id']}",
                            "occurred_at": utc_text(row["occurred_at"]),
                            "principal": readers.principal(conn, ctx, row["principal_id"]),
                            "on_behalf_of": (readers.principal(conn, ctx, row["on_behalf_of"])
                                             if row.get("on_behalf_of") else None)},
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


def _review_text(review: dict[str, Any] | None) -> str:
    """带入的已确认公司复盘：快照与时点、确认事件（记录者，代记时写两个人）、复盘的各块；没有时写缺口。"""
    if review is None:
        return "### 已确认的公司复盘\n（缺口）本 scope 里还没有已确认的公司复盘"
    snapshot = review["snapshot"]
    who = review["principal"]["display_name"] + (f" 代 {review['on_behalf_of']['display_name']}"
                                                 if review["on_behalf_of"] else "")
    lines = [f"### 已确认的公司复盘《{snapshot['title']}》 `{snapshot['ref']}`（截至 {snapshot['as_of']}）",
             f"确认事件 `{review['ref']}`（{who} 记，{review['confirmed_at']}）"]
    lines += [_block_text(block, f"#### {block['display_name']} `{block['ref']}`") for block in snapshot["blocks"]]
    return "\n".join(lines)


def _goal_text(goal: dict[str, Any]) -> str:
    """带入的有效长期目标：对象表头、生命周期与非空的定义类块引用。"""
    stage, refs = goal["lifecycle"], [block["ref"] for block in goal["definition_refs"] if not block["empty"]]
    return "\n".join([f"### 有效的长期目标《{goal['title']}》 `{goal['ref']}`",
                      f"生命周期：{stage['display_name']}（事件 `event:{stage['event_id']}`）",
                      "定义类块：" + (_codes([{"ref": ref} for ref in refs]) if refs else "都是空的")])


def _carried_text(item: dict[str, Any]) -> str:
    """一条带入的问题：问题组件引用与正文、主受影响对象、核心判断问题、处置（处置事件、记录者——代记时写两个人、
    时刻）与理由。"""
    primary, disposed = item["primary"], item["disposed_by"]
    return "\n".join([
        f"### 待带入的问题 `{item['issue_ref']['ref']}`" + (f"：{item['text']}" if item["text"] else ""),
        f"主受影响对象：{primary['type_display_name']}《{primary['title']}》 `{primary['ref']}`",
        f"核心判断问题：{item['core_question']}",
        f"处置：{item['disposition']['display_name']}（事件 `{disposed['ref']}`，{_recorder(disposed)} 记，"
        f"{disposed['occurred_at']}）",
        f"理由：{item['reason']}"])


def _where(layer: dict[str, Any]) -> str:
    return "当前对象" if layer["level"] == 0 else f"上溯第 {layer['level']} 层"


def _roles_text(layers: list[dict[str, Any]]) -> str | None:
    """「凭什么」：逐层带约束与验收角色的组件引用（约束沿关系读），注明组件类型；内容在它们所在的块里，不重复。
    这份清单同「谁负责」「现在怎样」的逐层清单一样不裁：所在的块被预算裁掉时引用照样有效，可以按引用另取。没有这样
    的组件时为 None。"""
    names = {item["id"]: item["display_name"] for item in world_registry.registry()["components"]["context_roles"]}
    lines = []
    for layer in layers:
        found = [item for block in layer["blocks"] for item in block["components"] if _role(item) in BASIS_ROLES]
        if found:
            obj = layer["object"]
            lines.append(f"- {_where(layer)} {obj['type_display_name']}《{obj['title']}》：" + "；".join(
                f"{names[role]} " + "、".join(f"`{item['ref']}`（{component_spec(item['type'])['display_name']}）"
                                             for item in found if _role(item) == role)
                for role in BASIS_ROLES if any(_role(item) == role for item in found)))
    return "\n".join(["按组件的取上下文角色逐层列出约束与验收（内容在它们所在的块里）：", *lines]) if lines else None


def _projection_text(projection: dict[str, Any], name: str) -> str:
    """出发对象的投影项：读取时从下级对象投影，不存。Task 预期结果与质量标准逐个 Task 写出它的组件；战役引用只列引用。"""
    lines = [f"### {name}·{projection['display_name']}（读取时从下级对象投影，不存）"]
    for item in projection["items"]:
        lines.append(f"- {item['type_display_name']}《{item['title']}》 `{item['ref']}`")
        lines += ["  " + line for component in item.get("components", [])
                  for line in _component_text(component).split("\n")]
    if not projection["items"]:
        lines.append(f"{projection['display_name']}：暂无")
    return "\n".join(lines)


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
           carried: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把上下文包渲染成按文档顺序排列、可逐条裁剪的 Markdown 片段（条目的形状同 0.1，交给本模块的 trim）。

    标题之后由 trim 插入六问指引；形成时带入（从周期目标出发，或有待带入的问题时）自成一节，依次是公司复盘（或缺口）、
    有效的长期目标、待带入的问题；然后以六问为节。块按块类分节：定义类块在当前对象是「做什么」、在上层是「为什么」，
    计划类块是「做什么」；跨链关系与多取的一跳放在「为什么」，分别在该层的块之前与之后。这样同一层的条目在文档里的
    先后与 0.1 的分层写法一致（登记里每类对象的块都是定义类在前、计划类在后），trim 同一层里「从后往前」裁块的先后
    也同 0.1。约束不再单独成块（#79）：「凭什么」是逐层带约束与验收角色的组件引用清单；出发对象的投影项放在「做什么」。
    节名、「谁负责」「现在怎样」「凭什么」的逐层清单与投影项不裁。"""
    kinds = {item["kind"]: item["display_name"] for item in world_registry.registry()["event_kinds"]}

    def entry(key: str, kind: str, level: int, object_id: str | None, text: str,
              occurred_at: str | None = None) -> dict[str, Any]:
        return {"key": key, "kind": kind, "level": level, "object_id": object_id, "occurred_at": occurred_at, "text": text}
    parts: dict[str, list[dict[str, Any]]] = {name: [] for name in QUESTIONS}
    parts["who"].append(entry("who", "header", -1, None, _who_text(layers)))
    parts["now"].append(entry("now", "header", -1, None, _now_text(layers)))
    roles = _roles_text(layers)
    if roles:
        parts["basis"].append(entry("basis", "header", -1, None, roles))
    for layer in layers:
        obj, level, name = layer["object"], layer["level"], layer["object"]["type_display_name"]
        relations = [f"{relation['field']}：" + _codes(relation["refs"]) for relation in layer["relations"] if relation["refs"]]
        relations += [f"被 `{item['source']['ref']}` 以 {item['field']} 引用" for item in layer["referenced_by"]]
        if relations:
            parts["why"].append(entry(f"relations:{level}", "relations", level, obj["object_id"],
                                      f"{name} 的跨链关系（只列引用，不展开）：\n" + "\n".join(relations)))
        for block in layer["blocks"]:
            answers = "what" if block["kind"] == "plan" or level == 0 else "why"
            parts[answers].append(entry(f"block:{block['ref']}", "block", level, obj["object_id"],
                                         _block_text(block, f"### {name}·{block['display_name']} `{block['ref']}`")))
        if layer["projection"]:
            parts["what"].append(entry(f"projection:{obj['ref']}", "projection", level, obj["object_id"],
                                       _projection_text(layer["projection"], name)))
        if layer["hop"]:
            parts["why"].append(entry(f"hop:{layer['hop']['object']['ref']}", "hop", level,
                                      layer["hop"]["object"]["object_id"], _hop_text(layer["hop"])))
        if layer["state"]:
            parts["now"].append(entry(f"snapshot:{layer['state']['ref']}", "snapshot", level, obj["object_id"],
                                      _state_text(layer["state"], name)))
        parts["happened"] += [entry(event["ref"], "event", level, obj["object_id"], _event_text(event, name, kinds),
                                    event["occurred_at"]) for event in layer["events"]]
    items = [entry("title", "title", -1, None, f"# 上下文\n\n问题：{question}\n\n出发对象：`{start}`")]
    if carried and ("company_review" in carried or carried["issues"]):
        formation = "PeriodGoal" if "company_review" in carried else None
        items.append(entry("section:carried", "section", -1, None, f"## 形成时带入\n{_CARRIED_NOTES[formation]}"))
        if formation:
            review = carried["company_review"]
            items.append(entry(f"carried:review:{review['snapshot']['ref']}" if review else "carried:review", "carried",
                               0, review and review["snapshot"]["object_id"], _review_text(review)))
            items += [entry(f"carried:goal:{goal['ref']}", "carried", 0, goal["object_id"], _goal_text(goal))
                      for goal in carried["long_term_goals"]]
        items += [entry(f"carried:{item['disposed_by']['ref']}", "carried", 0, item["primary"]["object_id"],
                        _carried_text(item)) for item in carried["issues"]]
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

    start, state = None, None
    if head["object_type"] == "StateSnapshot":
        # 从状态快照出发：当前对象是它的主体，状态取这条快照（而不是主体最新的那条）。
        revision = _latest(conn, ctx, head)
        subject = revision["payload"]["subject_ref"]["object_id"]
        start = citation(head["object_id"], revision["object_version"])
        state = readers.latest_snapshot(conn, ctx, subject, revision["payload"]["as_of"])
        head, _ = readers.readable(conn, ctx, subject)
    current = head
    layers: list[dict[str, Any]] = []
    walked: list[tuple[str, dict[str, Any]]] = []  # 沿主干走过的每一步：(引用字段, 钉定的引用)
    hops: list[dict[str, Any]] = []  # 主干之外多取的一跳
    seen_events: set[str] = set()
    while True:
        revision = _latest(conn, ctx, head)
        layer = _layer(conn, ctx, head, revision, len(layers), window, seen_events, None if layers else state)
        hop_field, hop_label = _HOPS.get(head["object_type"], (None, None))
        if hop_field and revision["payload"].get(hop_field):
            # 同主干一样读对方的最新修订，引用字段钉定的版本只作出处。
            target, _ = readers.readable(conn, ctx, revision["payload"][hop_field]["object_id"])
            layer["hop"] = _hop(conn, ctx, target, _latest(conn, ctx, target), hop_field, hop_label)
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
    # 形成时带入：周期目标 goal_ref 指的长期目标就是主干的上一层。
    carried = _carried_in(conn, ctx, current, layers[1]["object"]["object_id"] if len(layers) > 1 else None)
    result = trim(_items(request.question, start, layers, carried), max_chars=budget["max_chars"],
                  max_events_per_object=budget["max_events_per_object"], why=why_keys(layers, walked),
                  uncapped=uncapped_events(layers), lead=lambda kept: guide(_keep(layers, kept), carried))
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
