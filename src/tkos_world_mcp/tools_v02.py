"""tkos-world-mcp 的 tkos.world/0.2 Agent 面（票 #57，契约第 9.3 节、登记 agent_face）。

TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2 时启用。读与 0.1 是同样四个端点：HTTP 面按对象绑定的契约版本出形状，
0.2 对象分 business、identity、records 三组，读请求本身不带契约版本。写是契约第 9.3 节 Agent 面里已实现的五个动作：
记外部事件（含更正）、写状态快照、修订、开始（Mission 作为 Owner 的 Agent，Activity 作为其责任人）、交付（Activity
作为其责任人）。门、指派、建关系、建对象、关注标记不在 Agent 面上；代记只走 HTTP（契约第 14 节），所以开始、交付的
参数里没有 on_behalf_of。列对象（#63）与提出问题、路由问题、退回形成（#61）还没有 HTTP 实现，各随自己的票加一个
工具：写工具在 TOOLS 里加一项，读工具另在 READS 里给它的端点。

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
_LIFECYCLE = {"target": _TARGET, "content": {**_CONTENT, "description": "写进事件的内容，" + _CONTENT["description"]},
              "outcome": {"type": "string", "description": "撤回自己记的这条事件时为 withdrawn，另带 supersedes_event_id"},
              "supersedes_event_id": {"type": "string", "description": "撤回时，被撤回的那条事件的 id"},
              "declaration": _DECLARATION, "idempotency_key": _KEY}

# 工具名 -> (说明, 参数 schema)。读工具的参数与 0.1 相同，说明按 0.2 的读投影；写工具即同名的协议动作。
TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "world_get_object": (
        "取对象（GET /v1/world/objects/{object_id}）：0.2 对象分三组——business（类型、版本与修订 id、object_version、"
        "属性、关系、块与组件、组件台账、正式内容指针、进行中的一轮）、identity（责任人、当前有效的委托）、records"
        "（生命周期与推出它的事件、最新状态快照）；状态快照读回快照视图。version 取指定修订。写入的 target 取 business "
        "组里的 object_id、revision_id 与 object_version（作 expected_version）。",
        TOOLS_V01["world_get_object"][1]),
    "world_get_context": (
        "取上下文（POST /v1/world/objects/{object_id}/context）：从该对象沿主干向上组装上下文，返回上下文包 id、"
        "Markdown（沿主干分层列块与组件、最新状态快照与近期事件；引用细到组件 `对象@版本#块/组件`，事件写成 "
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
}
READS = _READS

FACE = Face(CONTRACT_VERSION, TOOLS, READS, REF, COMPONENT,
            "tkos.world/0.2 业务世界：四读五写（记外部事件、写状态快照、修订、开始、交付），写入须带三项声明。")
