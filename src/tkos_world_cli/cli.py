"""tkos-world：在命令行里读写业务世界（tkos.world/0.1、0.2），HTTP 面的薄封装。

持一枚凭证，把子命令转发到现有 HTTP 面，返回原样打到标准输出；不连数据库、不判权、不补目标、不重试。
只做 Agent 面，与 MCP 选同一契约版本时暴露的操作相同，TKOS_WORLD_CONTRACT_VERSION 选版本，默认 tkos.world/0.1。
0.1 是四读加三写（记外部事件、写状态快照、修订无门对象）；0.2 的写另有开始、交付与提出问题、路由问题、退回形成
（契约第 9.3 节）。门动作、指派、建关系、建对象与问题的承接、处置不暴露，在发请求之前就拒绝（契约第 9 节，人经工作台
或 HTTP 记）；0.2 的代记只走 HTTP，
--params 带 on_behalf_of 也在发请求之前拒绝。允许的动作之内能做什么仍由凭证的身份决定，由 HTTP 面判权；这里只把
对象 id 校验成 UUID（要拼进路径），其余交给 HTTP 面校验。

读：get、state、events（0.1 另有 children）对应 /v1/world/objects/{id} 的读投影，context 是取上下文（每次调用落一行）；
0.2 另有 list，即列对象（GET /v1/world/objects，按单元或域、类型、周期与外部引用筛选，--cursor 取下一页）。
写：act <动作> 先 prepare 再 commit，同一条命令、同一个幂等键；--prepare-only 只做 prepare。参数与目标按契约
原样给 JSON（字面量、@文件或 - 读标准输入）。target 取自 get 返回的 object_id、revision_id 与 object_version
（作 expected_version；0.2 在 business 组里），不在写入时替调用方重新取：那样会绕过版本检查。

退出码：0 成功；1 HTTP 面拒绝或不可达；2 用法或环境变量不对（含不认识的契约版本）。
环境变量：TKOS_WORLD_API_URL（HTTP 面的地址）、TKOS_WORLD_TOKEN（Bearer 凭证，不从命令行参数读）、
TKOS_WORLD_CONTRACT_VERSION（契约版本，tkos.world/0.1 或 tkos.world/0.2，默认 0.1）。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

import httpx

CONTRACT_VERSION = "tkos.world/0.1"
CONTRACT_V02 = "tkos.world/0.2"
# Agent 面的三个写动作，与 tkos-world-mcp 一致；其余动作不暴露。
AGENT_ACTIONS = ("world_record_event", "world_refresh_state", "world_revise_object")
# 0.2 Agent 面的写动作，与 tkos-world-mcp 选 0.2 时一致；提出问题、路由问题、退回形成随 #61 加入。
AGENT_ACTIONS_V02 = AGENT_ACTIONS + ("world_start", "world_deliver", "world_raise_issue", "world_route_issue",
                                     "world_return_issue")
# 子命令 -> (路径后缀, 查询参数)。
_READS = {"get": ("", "version"), "state": ("/state", "as_of"), "events": ("/events", "since"),
          "children": ("/children", None)}
# 列对象（#63）的查询参数：筛选与分页，与 HTTP 面同名；子命令的选项是它们的连字符写法。
_LIST_QUERY = ("unit_id", "domain_id", "type", "period", "external_system", "external_id", "limit", "cursor")
# 0.2 没有取子对象：它不在契约第 9.3 节的 Agent 面上（登记 agent_face 不含它）；HTTP 面对 0.2 对象支持取子对象
# （票 #93），要用直接打 HTTP。0.2 另有列对象，不带对象 id。
_READS_V02 = {**{name: read for name, read in _READS.items() if name != "children"}, "list": (None, _LIST_QUERY)}
# 契约版本 -> (act 接受的动作, 读子命令)。
FACES = {CONTRACT_VERSION: (AGENT_ACTIONS, _READS), CONTRACT_V02: (AGENT_ACTIONS_V02, _READS_V02)}


def _object_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an object id: {value!r}") from None


def _json(value: str) -> Any:
    """JSON 参数：字面量、@文件，或 - 读标准输入。"""
    try:
        if value == "-":
            return json.loads(sys.stdin.read())
        return json.loads(Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value)
    except (OSError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"not JSON ({exc.__class__.__name__}): {value[:80]!r}") from None


def _parser(version: str = CONTRACT_VERSION) -> argparse.ArgumentParser:
    actions, reads = FACES[version]
    parser = argparse.ArgumentParser(
        prog="tkos-world", description=f"{version} 业务世界的命令行（HTTP 面的薄封装）。"
        "环境变量 TKOS_WORLD_API_URL 给 HTTP 面的地址，TKOS_WORLD_TOKEN 给凭证，"
        "TKOS_WORLD_CONTRACT_VERSION 选契约版本（tkos.world/0.1 或 tkos.world/0.2，默认 0.1）。")
    commands = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    get = commands.add_parser("get", help="取对象：块、属性、关系引用、生命周期、最新状态快照")
    get.add_argument("object_id", type=_object_id)
    get.add_argument("--version", type=int, help="取指定修订")
    state = commands.add_parser("state", help="取状态：as_of 不晚于该时点的最新状态快照，未经确认")
    state.add_argument("object_id", type=_object_id)
    state.add_argument("--as-of", help="带时区的时刻")
    events = commands.add_parser("events", help="取事件：以该对象为主体、occurred_at 不早于 since 的事件")
    events.add_argument("object_id", type=_object_id)
    events.add_argument("--since", help="带时区的时刻")
    if "children" in reads:
        children = commands.add_parser("children", help="取子对象")
        children.add_argument("object_id", type=_object_id)
    if "list" in reads:
        listing = commands.add_parser("list", help="列对象：按责任单元或域、类型、周期、外部引用筛选，返回对象头，分页")
        listing.add_argument("--unit-id", help="责任单元的对象 id（即它所在的域）；与 --domain-id 只给一个")
        listing.add_argument("--domain-id", help="域 id")
        listing.add_argument("--type", help="对象类型，例如 Mission、Task、StateSnapshot")
        listing.add_argument("--period", help="周期 YYYY-MM：Mission 按其周期目标，Task、Activity 按其 Mission")
        listing.add_argument("--external-system", help="外部引用的系统，例如 tianshu")
        listing.add_argument("--external-id", help="外部引用的 id，与 --external-system 一起给")
        listing.add_argument("--limit", type=int, help="每页条数（默认 50，最多 100）")
        listing.add_argument("--cursor", help="上一页返回的 next_cursor")
    context = commands.add_parser("context", help="取上下文：从该对象沿主干向上组装上下文包，每次调用落一行")
    context.add_argument("object_id", type=_object_id)
    context.add_argument("--question", required=True, help="要回答的问题，只做记录，不影响返回的内容")
    context.add_argument("--max-chars", type=int, help="渲染后 Markdown 的字符数上限")
    context.add_argument("--max-events-per-object", type=int, help="每个对象的事件条数上限")
    context.add_argument("--recent-days", type=int, help="近期事件的天数")
    act = commands.add_parser("act", help="执行动作（只限 Agent 面的写动作）：先 prepare 再 commit")
    act.add_argument("action_type", choices=actions, metavar="<action>",
                     help="动作名，只接受 " + "、".join(actions) + "；门动作、指派、建关系与建对象不经此命令")
    act.add_argument("--params", type=_json, required=True, help="动作参数 JSON：字面量、@文件或 -（标准输入）")
    act.add_argument("--target", type=_json, help="目标 JSON {object_id, revision_id, expected_version}，"
                     + ("修订对象时给；写快照与记外部事件不给" if version == CONTRACT_VERSION else
                        "修订、开始、交付时给（取自 get 的 business 组）；写快照、记外部事件与问题动作不给"
                        "（问题以 --params 里的 issue_ref 指明）"))
    act.add_argument("--reason", required=True, help="写入理由，进审计")
    act.add_argument("--idempotency-key", help="幂等键，不给则生成；重放同一条命令时带上原来的键")
    act.add_argument("--prepare-only", action="store_true", help="只做 prepare，不提交")
    return parser


def _read(http: httpx.Client, args: argparse.Namespace) -> httpx.Response:
    if args.command == "list":  # 列对象：不带对象 id，给了的筛选与分页原样作查询参数
        query = {name: getattr(args, name) for name in _LIST_QUERY if getattr(args, name) is not None}
        return http.get("/v1/world/objects", params=query or None)
    path = f"/v1/world/objects/{args.object_id}"
    if args.command == "context":
        budget = {key: getattr(args, key) for key in ("max_chars", "max_events_per_object")
                  if getattr(args, key) is not None}
        body = {"question": args.question, "budget": budget or None, "recent_days": args.recent_days}
        return http.post(path + "/context", json={key: value for key, value in body.items() if value is not None})
    suffix, query = _READS[args.command]
    value = getattr(args, query) if query else None
    return http.get(path + suffix, params={query: value} if value is not None else None)


def _print(response: httpx.Response) -> None:
    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except ValueError:
        print(response.text)


def main(argv: list[str] | None = None) -> int:
    version = os.environ.get("TKOS_WORLD_CONTRACT_VERSION", "").strip() or CONTRACT_VERSION
    if version not in FACES:
        print(f"tkos-world: TKOS_WORLD_CONTRACT_VERSION must be {' or '.join(FACES)}, not {version[:40]!r}.",
              file=sys.stderr)
        return 2
    parser = _parser(version)
    args = parser.parse_args(argv)
    # 代记只走 HTTP、不属于 Agent 面（契约第 9.3、14 节）：0.2 的开始、交付虽可代记，这里也不发请求。
    params = args.params if args.command == "act" and isinstance(args.params, dict) else {}
    if version == CONTRACT_V02 and "on_behalf_of" in params:
        parser.error("on_behalf_of: recording on someone's behalf goes through the HTTP API only, not the Agent face")
    url, token = os.environ.get("TKOS_WORLD_API_URL", "").strip(), os.environ.get("TKOS_WORLD_TOKEN", "").strip()
    if not url or not token:
        print("tkos-world needs TKOS_WORLD_API_URL and TKOS_WORLD_TOKEN.", file=sys.stderr)
        return 2
    committing = None  # 已发出 commit 时的幂等键：此后不可达，写入可能已经提交
    try:
        with httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=60) as http:
            if args.command != "act":
                response = _read(http, args)
            else:
                body = {"action_type": args.action_type, "contract_version": version, "target": args.target,
                        "expected_versions": [], "idempotency_key": args.idempotency_key or f"world-cli-{uuid.uuid4()}",
                        "reason": args.reason, "params": args.params}
                response = http.post("/v1/actions/prepare", json=body)
                if response.status_code == 200 and not args.prepare_only:
                    try:
                        versions = response.json().get("expected_versions")
                    except (ValueError, AttributeError):
                        versions = None
                    if not isinstance(versions, list):
                        _print(response)
                        print("tkos-world: prepare answered without expected_versions; nothing committed.",
                              file=sys.stderr)
                        return 1
                    committing = body["idempotency_key"]
                    response = http.post("/v1/actions", json={**body, "expected_versions": versions})
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        hint = (f"; the write may have been committed, replay it with --idempotency-key {committing}"
                if committing else "")
        print(f"tkos-world: HTTP API unreachable ({exc.__class__.__name__}){hint}.", file=sys.stderr)
        return 1
    _print(response)
    if response.status_code >= 400:
        print(f"tkos-world: HTTP {response.status_code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
