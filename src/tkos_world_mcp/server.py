"""tkos-world-mcp：Agent 经 stdio 的 MCP 读写业务世界（票 #26、ADR-0004）。

薄壳：持一个 Agent 身份的凭证，把工具调用转发到现有 HTTP 面；不连数据库、不判权、不做事务。
工具是四读（取对象、取上下文、取事件、取状态）加三写（world_revise_object、world_refresh_state、
world_record_event），名称与参数和 HTTP 面一一对应；门动作、指派与建关系不暴露（契约第 9 节）。
这里只校验工具参数的形状（未知参数、缺少的定位字段），写入内容与三项声明由 HTTP 面校验，
HTTP 的拒绝原样返回。写入先 prepare 再 commit，同一条命令、同一个幂等键；不替调用方补目标、不重试。
取上下文成功时只把上下文包 id、渲染后的 Markdown、六问覆盖与预算裁剪摘要交给调用方（实验报告建议 1）；
分层 JSON、检索计划与钉定信息留在 HTTP 面与上下文包表里。其余返回原样交出。

每次工具调用在运行日志（JSONL，每个进程一个文件）里记一行：时间、会话、序号、工具、参数、HTTP 状态
与错误码、返回里的引用与事件 id、交给调用方的字符数；读工具另记带着内容回来的引用与事件 id（read_refs、
read_event_ids，只以引用形式出现的不算）；写入另记幂等键，取上下文另记上下文包 id 与渲染后 Markdown 的
字符数，且引用与读到的内容按 HTTP 面返回的上下文包本身算：交出去的 Markdown 就是这个包渲染的，检索计划里
裁掉的条目与主干上钉定的旧版本不计入。凭证不进日志；日志写不进去只在 stderr 提示，不影响已完成的调用。

环境变量：TKOS_WORLD_API_URL（HTTP 面的地址）、TKOS_WORLD_AGENT_TOKEN（Agent 身份的凭证）、
TKOS_WORLD_MCP_LOG_DIR（运行日志目录，默认 artifacts/world-mcp-runs）。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from uuid import uuid4

import anyio
import httpx
import jsonschema
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

CONTRACT_VERSION = "tkos.world/0.1"
REASON = "Agent write through tkos-world-mcp"
_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# 引用的业务形式 `<对象 id>@<版本号>#<块路径>`（契约第 5 节）：运行日志按它从返回里取引用集合。
_REF = re.compile(_UUID + r"@[1-9][0-9]*(?:#[a-z][a-z0-9_]*)?")

# 对象 id 只校验到能安全地拼进路径（HTTP 面按 UUID 解析，大小写都收）；长度上限挡住结尾的换行。
_OBJECT_ID = {"type": "string", "pattern": f"^(?i:{_UUID})$", "maxLength": 36, "description": "world 对象 id"}
_DECLARATION = {"type": ["object", "null"], "description": "写入声明三项：scene（所属 Mission 或 Task 的引用 <id>@<版本>）、"
                "trigger（触发事件的文字）、human_acceptance（{required, acceptor}）。Agent 写入必须带齐，缺一项由 HTTP 面拒绝。"}
_KEY = {"type": "string", "minLength": 16, "maxLength": 128, "description": "幂等键；不给则每次调用生成一个。"}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


# 工具名 -> (说明, 参数 schema)。读工具对应 /v1/world/objects/{id} 的四个读投影，写工具即同名的协议动作。
TOOLS: dict[str, tuple[str, dict[str, Any]]] = {
    "world_get_object": (
        "取对象（GET /v1/world/objects/{object_id}）：块、属性、关系引用、生命周期、最新状态快照；version 取指定修订。",
        _schema({"object_id": _OBJECT_ID, "version": {"type": "integer", "minimum": 1}}, ["object_id"])),
    "world_get_context": (
        "取上下文（POST /v1/world/objects/{object_id}/context）：从该对象沿主干向上组装上下文，返回上下文包 id、"
        "Markdown（开头的六问指引按问题给出处，下文分层列块、最新状态快照与近期事件）、六问覆盖与预算裁剪摘要，"
        "每次调用落一行。question 只做记录，不改变返回的内容：同一问题取一次即可；budget.trimmed 为空说明没裁，"
        "调大预算也不会多出内容。",
        _schema({"object_id": _OBJECT_ID, "question": {"type": "string", "minLength": 1,
                                                       "description": "要回答的问题，只做记录，不影响返回的内容"},
                 "budget": _schema({"max_chars": {"type": "integer", "minimum": 1},
                                    "max_events_per_object": {"type": "integer", "minimum": 1}}, []),
                 "recent_days": {"type": "integer", "minimum": 1}}, ["object_id", "question"])),
    "world_get_events": (
        "取事件（GET /v1/world/objects/{object_id}/events）：以该对象为主体、occurred_at 不早于 since 的事件。",
        _schema({"object_id": _OBJECT_ID, "since": {"type": "string", "description": "带时区的时刻"}}, ["object_id"])),
    "world_get_state": (
        "取状态（GET /v1/world/objects/{object_id}/state）：as_of 不晚于该时点的最新状态快照，未经确认。",
        _schema({"object_id": _OBJECT_ID, "as_of": {"type": "string", "description": "带时区的时刻"}}, ["object_id"])),
    "world_revise_object": (
        "修订对象（动作 world_revise_object）：合并补丁 payload；target 取自取对象返回的 object_id、revision_id 与 "
        "object_version（作 expected_version）。Agent 只能修订无门类型，且声明里要人工验收并给出验收人。",
        _schema({"target": _schema({"object_id": _OBJECT_ID, "revision_id": _OBJECT_ID,
                                    "expected_version": {"type": "integer", "minimum": 1}},
                                   ["object_id", "revision_id", "expected_version"]),
                 "payload": {"type": "object"}, "declaration": _DECLARATION, "idempotency_key": _KEY},
                ["target", "payload"])),
    "world_refresh_state": (
        "写状态快照（动作 world_refresh_state）：payload 含 title、subject_ref、as_of 与 progress、issue、artifacts 三块。",
        _schema({"payload": {"type": "object"}, "declaration": _DECLARATION, "idempotency_key": _KEY}, ["payload"])),
    "world_record_event": (
        "记外部事件（动作 world_record_event）：category、subject_refs、occurred_at、content，更正另带 supersedes_event_id。",
        _schema({"category": {"type": "string"}, "subject_refs": {"type": "array", "items": {"type": "string"}},
                 "occurred_at": {"type": "string"}, "content": {"type": "object"},
                 "supersedes_event_id": {"type": "string"}, "declaration": _DECLARATION, "idempotency_key": _KEY},
                ["category", "subject_refs", "occurred_at", "content"])),
}
_READS = {"world_get_object": ("GET", "", "version"), "world_get_context": ("POST", "/context", None),
          "world_get_events": ("GET", "/events", "since"), "world_get_state": ("GET", "/state", "as_of")}


def _is_object(response: httpx.Response) -> bool:
    try:
        return isinstance(response.json(), dict)
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _event_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {value["event_id"]} if isinstance(value.get("event_id"), str) else set()
        return found.union(*(_event_ids(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_event_ids(item) for item in value))
    return set()


def _content(value: Any) -> tuple[set[str], set[str]]:
    """返回里带着内容回来的引用与事件：对象视图（带块的对象、取对象顺带的最新快照、上下文包里一层的对象与状态）、
    块视图、事件视图。块内引用、关系、referenced_by、supersedes、生命周期里钉的事件只以引用形式出现，不算读到。"""
    refs: set[str] = set()
    events: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for entry in item:
                walk(entry)
            return
        if not isinstance(item, dict):
            return
        # 对象视图（带 object_id 与 version）、包里的状态（带 ref 与块）、包里一层的对象（带 ref 与标题）；
        # 包里的一层本身也带块，但它没有 ref，也没有 object_id，不算一个对象。
        if isinstance(item.get("blocks"), list) and {"object_id", "version"} <= item.keys():
            refs.add(f"{item['object_id']}@{item['version']}")
        elif isinstance(item.get("blocks"), list) and "ref" in item or {"title", "ref"} <= item.keys():
            refs.add(str(item["ref"]))
        if {"empty", "text", "ref"} <= item.keys() and _REF.fullmatch(str(item["ref"])):  # 块视图（空块读到的是标准句）
            refs.add(item["ref"])
        if isinstance(item.get("event_id"), str) and "occurred_at" in item:  # 事件视图
            events.add(item["event_id"])
        for entry in item.values():
            walk(entry)

    walk(value)
    return refs, events


class RunLog:
    """运行日志：每个进程一个 JSONL 文件，每次工具调用一行。"""

    def __init__(self, directory: Path) -> None:
        self.session = str(uuid4())
        self.path = directory / f"{_utc_now()[:19].replace(':', '')}-{self.session[:8]}.jsonl"
        self.seq = 0

    def write(self, tool: str, arguments: dict[str, Any], status: int | None, error_code: str | None, text: str,
              body: Any, idempotency_key: str | None) -> None:
        self.seq += 1
        # 取上下文：引用只取上下文包本身；检索计划记的是裁掉的条目与钉定的出处，不是返回的内容。
        taken = body["context_pack"] if tool == "world_get_context" and isinstance(body, dict) \
            and isinstance(body.get("context_pack"), dict) else None
        line = {"at": _utc_now(), "session": self.session, "seq": self.seq, "tool": tool, "arguments": arguments,
                "status": status, "error_code": error_code,
                "refs": sorted(set(_REF.findall(json.dumps(taken, ensure_ascii=False) if taken else text))),
                "event_ids": sorted(_event_ids(taken or body)), "chars": len(text)}
        if tool in _READS:
            read_refs, read_event_ids = _content(taken or body) if status is not None and status < 400 else (set(), set())
            line["read_refs"], line["read_event_ids"] = sorted(read_refs), sorted(read_event_ids)
        if idempotency_key is not None:
            line["idempotency_key"] = idempotency_key
        if taken is not None:
            line["context_pack_id"] = body.get("context_pack_id")
            line["used_chars"] = (body.get("budget") or {}).get("used_chars")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as exc:  # 日志写不进去不能把已完成的调用（尤其已提交的写入）变成失败
            print(f"tkos-world-mcp: run log not written ({exc.__class__.__name__})", file=sys.stderr)


def _envelope(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """写工具的参数即动作参数；目标与幂等键由调用方给（幂等键不给则生成）。"""
    params = {key: value for key, value in arguments.items() if key not in {"target", "idempotency_key"}}
    return {"action_type": name, "contract_version": CONTRACT_VERSION, "target": arguments.get("target"),
            "expected_versions": [], "idempotency_key": arguments.get("idempotency_key") or f"world-mcp-{uuid4()}",
            "reason": REASON, "params": params}


def _error(code: str, message: str, **detail: Any) -> str:
    """本层的错误（参数不合形状、HTTP 面不可达或答非所问），与 HTTP 面的错误同形。"""
    return json.dumps({"error": {"code": code, "message": message, **detail}}, ensure_ascii=False)


def _shown(name: str, body: Any) -> dict[str, Any] | None:
    """取上下文成功时交给调用方的部分：包 id、Markdown、六问覆盖，以及预算加上按原因、按类计的裁剪条数。
    其余工具与不成形的返回为 None，原样交出。"""
    if name != "world_get_context" or not isinstance(body, dict) or not isinstance(body.get("context_pack"), dict):
        return None
    trimmed: dict[str, dict[str, int]] = {}
    for entry in (body.get("plan") or {}).get("trimmed") or []:
        counted = trimmed.setdefault(str(entry.get("reason")), {})
        counted[str(entry.get("kind"))] = counted.get(str(entry.get("kind")), 0) + 1
    return {"context_pack_id": body.get("context_pack_id"), "markdown": body["context_pack"].get("markdown"),
            "coverage": body.get("coverage"), "budget": {**(body.get("budget") or {}), "trimmed": trimmed}}


class WorldTools:
    def __init__(self, http: httpx.AsyncClient, log: RunLog) -> None:
        self.http, self.log = http, log

    async def list_tools(self, ctx: ServerRequestContext, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[types.Tool(name=name, description=description, input_schema=schema)
                                            for name, (description, schema) in TOOLS.items()])

    async def call_tool(self, ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        name, arguments = params.name, dict(params.arguments or {})
        try:
            jsonschema.validate(arguments, TOOLS[name][1])
        except (KeyError, jsonschema.ValidationError) as exc:
            message = f"unknown tool {name}" if isinstance(exc, KeyError) else exc.message
            return self.finish(name, arguments, None, _error("INVALID_ARGUMENTS", message))
        body = None if name in _READS else _envelope(name, arguments)
        key = body and body["idempotency_key"]
        try:
            if body is None:
                response = await self.read(name, arguments)
            else:
                response = await self.http.post("/v1/actions/prepare", json=body)
                if response.status_code == 200:
                    versions = response.json().get("expected_versions") if _is_object(response) else None
                    if not isinstance(versions, list):
                        return self.finish(name, arguments, 200, _error(
                            "HTTP_UNEXPECTED", "prepare answered without expected_versions", idempotency_key=key), key)
                    response = await self.http.post("/v1/actions", json={**body, "expected_versions": versions})
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # 写入可能已经提交：把用过的幂等键交回去，调用方用它重放即可，不会重复写。
            detail = {"idempotency_key": key} if key else {}
            return self.finish(name, arguments, None, _error("HTTP_UNAVAILABLE", exc.__class__.__name__, **detail), key)
        return self.finish(name, arguments, response.status_code, response.text, key)

    async def read(self, name: str, arguments: dict[str, Any]) -> httpx.Response:
        method, suffix, query = _READS[name]
        path = f"/v1/world/objects/{arguments['object_id']}{suffix}"
        if method == "POST":
            return await self.http.post(path, json={key: value for key, value in arguments.items() if key != "object_id"})
        return await self.http.get(path, params={query: arguments[query]} if query in arguments else None)

    def finish(self, name: str, arguments: dict[str, Any], status: int | None, text: str,
               idempotency_key: str | None = None) -> types.CallToolResult:
        """把 HTTP 的返回（或本层的错误）交给调用方，并记一行运行日志：取上下文成功时只交出 _shown 的部分，其余原样。
        status 为 None 表示没有拿到 HTTP 的答复。"""
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        failed = status is None or status >= 400 or code is not None
        shown = None if failed else _shown(name, body)
        if shown is not None:
            text = json.dumps(shown, ensure_ascii=False, separators=(",", ":"))
        self.log.write(name, arguments, status, code if failed else None, text, body, idempotency_key)
        structured = shown if shown is not None else body if isinstance(body, dict) else None
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                    structured_content=structured, is_error=failed)


async def _serve(url: str, token: str, log_dir: Path) -> None:
    async with httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=60) as http:
        tools = WorldTools(http, RunLog(log_dir))
        server = Server("tkos-world", instructions="tkos.world/0.1 业务世界：四读三写，写入须带三项声明。",
                        on_list_tools=tools.list_tools, on_call_tool=tools.call_tool)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())


def main() -> None:
    url, token = os.environ.get("TKOS_WORLD_API_URL", "").strip(), os.environ.get("TKOS_WORLD_AGENT_TOKEN", "").strip()
    if not url or not token:
        print("tkos-world-mcp needs TKOS_WORLD_API_URL and TKOS_WORLD_AGENT_TOKEN.", file=sys.stderr)
        raise SystemExit(2)
    log_dir = Path(os.environ.get("TKOS_WORLD_MCP_LOG_DIR") or "artifacts/world-mcp-runs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # 启动时就提示；之后每次调用仍照常，只是不记日志
        print(f"tkos-world-mcp: run log directory unavailable ({exc.__class__.__name__})", file=sys.stderr)
    anyio.run(_serve, url.rstrip("/"), token, log_dir)


if __name__ == "__main__":
    main()
