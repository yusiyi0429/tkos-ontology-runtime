"""tkos-world-mcp 的 tkos.world/0.2 Agent 面（票 #57，契约第 9.3 节、登记 agent_face）。

TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2 时启用。读与 0.1 是同样四个端点：HTTP 面按对象绑定的契约版本出形状，
0.2 对象分 business、identity、records 三组，读请求本身不带契约版本。写是契约第 9.3 节 Agent 面的八个动作：
记外部事件（含更正）、写状态快照、修订、开始（Mission 作为 Owner 的 Agent，Activity 作为其责任人）、交付（Activity
作为其责任人），以及提出问题、路由问题、退回形成（#61，作为 MF 即在主受影响对象所在域持 AGENT 的 Agent；以 issue_ref
指明问题，不带目标）。另有第五读列对象（#63，GET /v1/world/objects，按单元或域、类型、周期与外部引用筛选，分页）。
门、指派、建关系、建对象、关注标记、承接与处置问题不在 Agent 面上；代记只走 HTTP（契约第 14 节），所以写工具的参数里
没有 on_behalf_of。加一个写工具在 TOOLS 里加一项（名即动作名、参数即动作参数），读工具另在 READS 里给它的端点。

列对象返回的是对象头（id、类型、类别、标题、版本、生命周期、域、外部引用），不带块与组件，运行日志里不算读到内容：
它的 read_refs 为空，推出生命周期的事件只进 event_ids。

运行日志按 0.2 引用的四种业务形式识别引用（契约第 5 节）：对象、块、组件 `对象@版本#块/组件` 与事件 `event:<事件 id>`；
块里带着内容回来的组件算读到。
"""
from __future__ import annotations

import re
from typing import Any

from .server import _KEY, _OBJECT_ID, _READS, TOOLS as TOOLS_V01, _UUID, Face, _schema

CONTRACT_VERSION = "tkos.world/0.2"
_VERSION, _BLOCK, _COMPONENT = "@[1-9][0-9]*", "#[a-z][a-z0-9_]*", "/[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
REF = re.compile(f"{_UUID}{_VERSION}(?:{_BLOCK}(?:{_COMPONENT})?)?|event:{_UUID}")
COMPONENT = re.compile(f"{_UUID}{_VERSION}{_BLOCK}{_COMPONENT}")

_DECLARATION = {"type": ["object", "null"], "description": "写入声明三项：scene（任一业务对象的对象形式引用 <id>@<版本>）、"
                "trigger（触发事件的文字）、human_acceptance（{required, acceptor}）。Agent 写入必须带齐，缺一项由 HTTP 面拒绝。"}
_TARGET = {**_schema({"object_id": _OBJECT_ID, "revision_id": _OBJECT_ID,
                      "expected_version": {"type": "integer", "minimum": 1}},
                     ["object_id", "revision_id", "expected_version"]),
           "description": "目标对象的最新修订：取对象返回的 business 组里的 object_id、revision_id 与 object_version"
                          "（作 expected_version）"}
_CONTENT = {"type": "object", "description": "块值形状 {text, refs, artifacts}：text 为 Markdown，refs 为引用，artifacts 为文档链接"}
_ISSUE_REF = {"type": "string", "description": "问题组件的组件引用 <快照 id>@<版本>#issues/<组件 id>：主受影响对象某条"
              "状态快照 issues 块里的问题组件，取自取对象 records.open_issues 的 issue_ref.ref 或取状态返回的快照；"
              "同一问题在后续快照里带同一个 id，引用哪一条都指同一个问题"}
_ISSUE = {"issue_ref": _ISSUE_REF, "content": {**_CONTENT, "description": "写进事件的内容，" + _CONTENT["description"]},
          "declaration": _DECLARATION, "idempotency_key": _KEY}
_LIFECYCLE = {"target": _TARGET, "content": {**_CONTENT, "description": "写进事件的内容，" + _CONTENT["description"]},
              "outcome": {"type": "string", "description": "撤回自己记的这条事件时为 withdrawn，另带 supersedes_event_id"},
              "supersedes_event_id": {"type": "string", "description": "撤回时，被撤回的那条事件的 id"},
              "declaration": _DECLARATION, "idempotency_key": _KEY}

# 工具名 -> (说明, 参数 schema)。读工具的参数与 0.1 相同，说明按 0.2 的读投影；写工具即同名的协议动作。
TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "world_get_object": (
        "取对象（GET /v1/world/objects/{object_id}）：0.2 对象分三组——business（类型、版本与修订 id、object_version、"
        "属性、关系、块与组件、组件台账、正式内容指针、进行中的一轮）、identity（责任人、当前有效的委托）、records"
        "（生命周期与推出它的事件、最新状态快照、open_issues 即主受影响对象是它且还没处置的问题，各带 issue_ref 与"
        "状态）；状态快照读回快照视图。version 取指定修订。写入的 target 取 business "
        "组里的 object_id、revision_id 与 object_version（作 expected_version）。",
        TOOLS_V01["world_get_object"][1]),
    "world_get_context": (
        "取上下文（POST /v1/world/objects/{object_id}/context）：从该对象沿主干向上组装上下文，返回上下文包 id、"
        "Markdown（开头的六问指引按问题给出处，下文以六问为节：为什么（上层的定义类块，单元长期目标多取一跳到公司级"
        "长期目标，直到 Strategy 与 Company）、做什么、谁负责、现在怎样（生命周期与最新状态快照）、发生了什么（近期"
        "事件，写出记录者、被代记的人与被指派者）、凭什么（约束）；引用细到组件 `对象@版本#块/组件`，事件写成 "
        "`event:<事件 id>`）、六问覆盖与预算裁剪摘要，每次调用落一行。question 只做记录，不改变返回的内容：同一问题"
        "取一次即可；budget.trimmed 为空说明没裁，调大预算也不会多出内容。",
        TOOLS_V01["world_get_context"][1]),
    "world_get_events": (
        "取事件（GET /v1/world/objects/{object_id}/events）：subject_refs 含该对象或其组件、occurred_at 不早于 since "
        "的事件，按发生时刻升序，带迟记、被更正与被撤回关系、代记信息与产生它的动作。",
        TOOLS_V01["world_get_events"][1]),
    "world_get_state": (
        "取状态（GET /v1/world/objects/{object_id}/state）：as_of 不晚于该时点的最新状态快照（外壳与 payload），未经确认。",
        TOOLS_V01["world_get_state"][1]),
    "world_list_objects": (
        "列对象（GET /v1/world/objects）：按 unit_id（责任单元的对象 id，即它所在的域）或 domain_id、type（对象类型，"
        "含 StateSnapshot）、period（YYYY-MM：周期目标按自己的，Mission 按其周期目标，Task、Activity 按其 Mission，"
        "快照按自己的）、external_system 与 external_id（外部引用；在 scope 内唯一，两者都给时至多一项）筛选，条件可以"
        "组合、都按最新修订。返回 items（对象头：object_id、object_type、category、title、version、revision_id、"
        "object_version、lifecycle、domain_id、external_refs、contract_version）与 next_cursor；limit 每页条数（默认 50，"
        "最多 100），next_cursor 不为空时原样作 cursor 取下一页。要块与组件时再取对象。",
        _schema({"unit_id": {**_OBJECT_ID, "description": "责任单元的对象 id"},
                 "domain_id": {**_OBJECT_ID, "description": "域 id"},
                 "type": {"type": "string", "description": "对象类型，例如 Mission、Task、StateSnapshot"},
                 "period": {"type": "string", "description": "周期 YYYY-MM"},
                 "external_system": {"type": "string", "description": "外部系统，例如 tianshu"},
                 "external_id": {"type": "string", "description": "外部系统里的 id，与 external_system 一起给"},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                 "cursor": {"type": "string", "description": "上一页返回的 next_cursor"}}, [])),
    "world_record_event": (
        "记外部事件（动作 world_record_event）：category（meeting、review、delivery、acceptance、other、correction）、"
        "subject_refs（对象或组件形式的引用，至少一条）、occurred_at（可以补记过去的时刻）、content；更正的 category "
        "为 correction，并以 supersedes_event_id 指向被更正的事件。",
        _schema({"category": {"type": "string"}, "subject_refs": {"type": "array", "items": {"type": "string"}},
                 "occurred_at": {"type": "string"}, "content": _CONTENT, "supersedes_event_id": {"type": "string"},
                 "declaration": _DECLARATION, "idempotency_key": _KEY},
                ["category", "subject_refs", "occurred_at", "content"])),
    "world_refresh_state": (
        "写状态快照（动作 world_refresh_state）：payload 是外壳加块——title、subject_ref（主体的对象形式引用）、as_of、"
        "可选 period（YYYY-MM）、payload_type（主体类型登记的那一种，Mission、Task、Activity 是 execution_state）、"
        "source_event_refs（来源事件 event:<事件 id>，至少一条）、blocks；生成者由 HTTP 面按凭证填，不要给。",
        _schema({"payload": {"type": "object"}, "declaration": _DECLARATION, "idempotency_key": _KEY}, ["payload"])),
    "world_revise_object": (
        "修订对象（动作 world_revise_object）：合并补丁 payload（块按字段合并；components 按 id 合并，{id, removed: true} "
        "删除）；target 取自取对象的 business 组。Agent 可以修订无门对象，有门对象只能修订活动块与活动属性；触及正式块"
        "或正式属性时，声明必须要求人工验收并给出验收人。",
        _schema({"target": _TARGET, "payload": {"type": "object"}, "declaration": _DECLARATION, "idempotency_key": _KEY},
                ["target", "payload"])),
    "world_start": (
        "开始（动作 world_start）：target 是已成立的 Mission（作为 Owner 的 Agent，须在它所在的域持 AGENT）或已指派给"
        "自己的 Activity，取自取对象的 business 组；可选 content 写进事件。撤回自己记的开始带 outcome withdrawn 与 "
        "supersedes_event_id。",
        _schema(_LIFECYCLE, ["target"])),
    "world_deliver": (
        "交付（动作 world_deliver）：target 是自己负责、进行中或调整中的 Activity，取自取对象的 business 组；可选 content "
        "写进事件（例如交付说明与文档链接）。Agent 记的交付由 Task 的责任人验收。撤回自己记的交付带 outcome withdrawn 与 "
        "supersedes_event_id。",
        _schema(_LIFECYCLE, ["target"])),
    "world_raise_issue": (
        "提出问题（动作 world_raise_issue）：issue_ref 是问题组件的组件引用（先写一条带该问题组件的状态快照），作为 MF"
        "（在主受影响对象所在域持 AGENT）提出，问题进入待路由；可选 content 写进事件。未提出与形成中的问题可以提出，"
        "正在处理的不能重复提出；已处置的不再提出，复发用新的组件 id，在 content 里引用原问题。",
        _schema(_ISSUE, ["issue_ref"])),
    "world_route_issue": (
        "路由问题（动作 world_route_issue）：issue_ref 是问题组件的组件引用，to_principal_id 是承接人——须是人，且在主受"
        "影响对象所在的域持角色；待路由的问题进入已路由，已路由的可以改路由。承接与处置由承接人本人经 HTTP 记。",
        _schema({**_ISSUE, "to_principal_id": {**_OBJECT_ID, "description": "承接人（人）的 principal id"}},
                ["issue_ref", "to_principal_id"])),
    "world_return_issue": (
        "退回形成（动作 world_return_issue）：issue_ref 是问题组件的组件引用；作为路由者把已路由或已承接的问题退回形成中，"
        "在 content 里写要补齐什么。补齐后再提出。",
        _schema(_ISSUE, ["issue_ref"])),
}
# 读工具 -> (方法, 路径, 查询参数)：0.1 的四读加列对象；列对象的路径里没有对象 id，筛选与分页都是查询参数。
READS = {**_READS, "world_list_objects": ("GET", "/v1/world/objects", (
    "unit_id", "domain_id", "type", "period", "external_system", "external_id", "limit", "cursor"))}

FACE = Face(CONTRACT_VERSION, TOOLS, READS, REF, COMPONENT,
            "tkos.world/0.2 业务世界：五读（取对象、取上下文、取事件、取状态、列对象）八写（记外部事件、写状态快照、修订、"
            "开始、交付、提出问题、路由问题、退回形成），写入须带三项声明。")
