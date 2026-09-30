"""tkos-world-mcp 的 0.2 Agent 面（票 #57、#63）：TKOS_WORLD_CONTRACT_VERSION 选 tkos.world/0.2 时，用 mcp 客户端经
stdio 拉起 server 子进程，让它打一个假的 HTTP 面。

断言工具清单等于契约第 9.3 节的 Agent 面（登记 agent_face：五读八写，Issue 的提出、路由、退回形成随 #61 加入），
面外的工具与代记在发请求之前就拒绝；每个工具的请求形状、契约版本与 prepare 再 commit；运行日志按 0.2 的四种引用形式记下组件
与事件引用、不含凭证；取上下文仍只交出包 id、Markdown、覆盖与预算摘要；版本取值不认识时启动即退出。默认 0.1 的
行为由 test_world_mcp.py 原样覆盖。不连数据库，也不启动真 API；真 API 上的 0.2 Agent 面在 acceptance/world_v02 里跑。
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from tests.test_world_mcp import FakeApi as FakeApiV01  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text())
V01, V02 = "tkos.world/0.1", "tkos.world/0.2"
TOKEN = "fake-agent-token-for-v02-tests-only"
REASON = "Agent write through tkos-world-mcp"
TASK = "0c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
MISSION = "1c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
SNAPSHOT = "2c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
REV = "3c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
EVENT = "4c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
START = "5c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
TRIMMED = "6c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
PACK = "7c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
UNIT = "8c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
AGENT = "9c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
DELEGATION = "ac5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
REFUSED = "bc5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
# 场景可以是任一业务对象（契约第 9.3 节），这里是责任单元。
DECLARATION = {"scene": f"{UNIT}@1", "trigger": "会后整理", "human_acceptance": {"required": True, "acceptor": AGENT}}
TARGET = {"object_id": TASK, "revision_id": REV, "expected_version": 4}
REFUSAL = {"error": {"code": "INVALID_REQUEST",
                     "message": "An Agent write must declare its scene, trigger and human acceptance."}}
FORBIDDEN = {"error": {"code": "FORBIDDEN", "message": "not the responsible"}}
READS = {"world_get_object", "world_get_context", "world_get_events", "world_get_state", "world_list_objects"}
ISSUES = {"world_raise_issue", "world_route_issue", "world_return_issue"}
WRITES = {"world_record_event", "world_refresh_state", "world_revise_object", "world_start", "world_deliver", *ISSUES}


# ------------------------------------------------------------ 0.2 读投影的形状（world_v02_readers、world_v02_context）
def pinned(object_id: str, version: int, block: str | None = None, component: str | None = None) -> dict:
    return {"object_id": object_id, "object_version": version, "revision_id": REV, "block": block,
            "component": component,
            "ref": f"{object_id}@{version}" + (f"#{block}" if block else "") + (f"/{component}" if component else "")}


def event_pin(event_id: str) -> dict:
    return {"event_id": event_id, "ref": f"event:{event_id}"}


def component(object_id: str, version: int, block: str, cid: str, kind: str, text: str, refs: list | None = None) -> dict:
    return {"id": cid, "type": kind, "scope": None, "text": text, "refs": refs or [], "artifacts": [], "attributes": {},
            "ref": f"{object_id}@{version}#{block}/{cid}"}


def block(object_id: str, version: int, name: str, text: str | None, components: list | None = None,
          refs: list | None = None) -> dict:
    comps = components or []
    value = None if text is None and not comps else {"text": text or "", "components": comps, "refs": refs or [],
                                                     "artifacts": []}
    return {"id": name, "display_name": name, "kind": "definition", "class": "formal", "value": value,
            "empty": value is None, "text": f"{name}：暂无" if value is None else value["text"], "components": comps,
            "ref": f"{object_id}@{version}#{name}"}


def with_pins(view: dict, object_id: str, version: int) -> dict:
    """上下文包里的块：块与组件另带钉定结构。"""
    comps = [{**item, "pinned": pinned(object_id, version, view["id"], item["id"])} for item in view["components"]]
    return {**view, "value": view["value"] and {**view["value"], "components": comps}, "components": comps,
            "pinned": pinned(object_id, version, view["id"])}


ISSUE = component(SNAPSHOT, 1, "issues", "iss-1", "issue", "裁剪以块为单位")
SNAPSHOT_VIEW = {"object_id": SNAPSHOT, "object_type": "StateSnapshot", "category": {"id": "time_record"},
                 "version": 1, "revision_id": REV, "ref": f"{SNAPSHOT}@1", "title": "进展", "subject_ref": pinned(TASK, 3),
                 "as_of": "2026-09-28T02:00:00Z", "period": None, "generator": {"principal_id": AGENT},
                 "source_event_refs": [event_pin(EVENT)], "payload_type": {"id": "execution_state"},
                 "blocks": [block(SNAPSHOT, 1, "progress", "改了一半"), block(SNAPSHOT, 1, "issues", "", [ISSUE])],
                 "unconfirmed": True}
CRITERIA = [component(TASK, 3, "acceptance", "ac-1", "acceptance_criterion", "引用细到组件",
                      [pinned(MISSION, 2, "acceptance", "m-ac1")]),
            component(TASK, 3, "acceptance", "ac-2", "acceptance_criterion", "事件以事件引用给出")]
TASK_BLOCKS = [block(TASK, 3, "definition", "把取上下文接给执行 Agent。"), block(TASK, 3, "acceptance", "两条验收。", CRITERIA),
               block(TASK, 3, "constraint", None)]
TASK_VIEW = {"object_id": TASK,
             "business": {"object_id": TASK, "object_type": "Task", "version": 3, "revision_id": REV, "object_version": 4,
                          "title": "上下文 Task", "attributes": {"external_refs": []},
                          "relations": [{"field": "parent_ref", "relation": "contains", "value": pinned(MISSION, 2)}],
                          "blocks": TASK_BLOCKS, "component_ledger": [{"id": "ac-1", "type": "acceptance_criterion",
                                                                       "block": "acceptance", "added_in_version": 1,
                                                                       "removed_in_version": None}],
                          "formal": {"lifecycle_status": "recorded", "effective_revision_id": REV}, "round": None},
             "identity": {"responsible": {"source": "attribute", "principals": [{"principal_id": AGENT}]},
                          "delegations": [{"event_id": DELEGATION, "ref": f"event:{DELEGATION}", "families": ["lifecycle"]}]},
             "records": {"lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": START},
                         "latest_state": SNAPSHOT_VIEW, "confirmed_review": None, "open_issues": []},
             "protocol": {"contract_version": V02, "interpretation_status": "world_v0_2"}}
EVENT_VIEW = {"event_id": EVENT, "kind": "event.recorded", "class": "record", "category": "review",
              "subject_refs": [pinned(TASK, 3, "acceptance", "ac-1"), pinned(TASK, 3)],
              "principal": {"principal_id": AGENT}, "on_behalf_of": None, "occurred_at": "2026-09-28T01:00:00Z",
              "recorded_at": "2026-09-28T01:00:01Z", "late": False,
              "content": {"text": "评审组件级引用", "components": [], "refs": [pinned(MISSION, 2, "play")], "artifacts": []},
              "action": "world_record_event", "supersedes_event_id": None, "corrected_by": [], "withdrawn_by": []}
CONTEXT_PACK = {"contract_version": V02, "question": "为什么？", "start": f"{TASK}@3",
                "markdown": f"生命周期：进行中（事件 `event:{START}`）\n### 验收 `{TASK}@3#acceptance/ac-1`",
                "layers": [{"level": 0,
                            "object": {"object_id": TASK, "object_type": "Task", "title": "上下文 Task", "version": 3,
                                       "ref": f"{TASK}@3", "pinned": pinned(TASK, 3),
                                       "lifecycle": {"status": "in_progress", "event_id": START}, "formal": None},
                            "blocks": [with_pins(TASK_BLOCKS[1], TASK, 3)],
                            "relations": [], "referenced_by": [],
                            "state": {**SNAPSHOT_VIEW, "pinned": pinned(SNAPSHOT, 1),
                                      "blocks": [with_pins(item, SNAPSHOT, 1) for item in SNAPSHOT_VIEW["blocks"]]},
                            "events": [{**EVENT_VIEW, "ref": f"event:{EVENT}"}]}]}
COVERAGE = {"basis": {"question": "凭什么", "answered": True, "evidence": [{"ref": f"{TASK}@3#acceptance/ac-1"}], "gap": None}}
CONTEXT = {"contract_version": V02, "context_pack_id": PACK, "created_at": "2026-09-28T03:00:00Z", "object_id": TASK,
           "question": "为什么？", "context_pack": CONTEXT_PACK, "coverage": COVERAGE,
           "budget": {"max_chars": 12000, "used_chars": 33, "over_budget": False},
           "plan": {"walked": [], "trimmed": [{"kind": "event", "level": 0, "key": f"event:{TRIMMED}",
                                                "reason": "over_level_cap"}]}}


# 列对象的一页（world_v02_list 的对象头）：不带块与组件，运行日志里不算读到内容。
HEADER = {"object_id": TASK, "object_type": "Task", "type_display_name": "Task",
          "category": {"id": "business_object", "display_name": "业务对象"}, "title": "上下文 Task", "version": 3,
          "revision_id": REV, "object_version": 4,
          "lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": START}, "domain_id": UNIT,
          "external_refs": [{"system": "tianshu", "id": "card:12", "url": None}], "contract_version": V02}
LISTED = {"items": [HEADER, {**HEADER, "object_id": MISSION, "object_type": "Mission", "lifecycle": None,
                             "external_refs": []}], "next_cursor": "cursor-2"}


class FakeApi(FakeApiV01):
    """回 0.2 读投影的形状；没带声明的写入回 422，目标是 REFUSED 的回 403。"""

    def reply(self, method: str, path: str, body: dict | None) -> tuple[int, dict | str]:
        base = f"/v1/world/objects/{TASK}"
        replies = {base: TASK_VIEW, base + "/state": {"object_id": TASK, "as_of": None, "snapshot": SNAPSHOT_VIEW},
                   base + "/events": {"object_id": TASK, "since": None, "events": [EVENT_VIEW]}, base + "/context": CONTEXT,
                   "/v1/world/objects": LISTED}
        if path in replies:
            return 200, replies[path]
        if path in {"/v1/actions/prepare", "/v1/actions"}:
            if not body["params"].get("declaration"):
                return 422, REFUSAL
            if (body["target"] or {}).get("object_id") == REFUSED:
                return 403, FORBIDDEN
            if path.endswith("prepare"):
                return 200, {"expected_versions": [{"object_id": TASK, "expected_version": 4}]}
            return 200, {"receipt_id": EVENT, "status": "committed", "result": {"contract_version": V02, "event_id": EVENT}}
        return 404, {"error": {"code": "NOT_FOUND"}}


@pytest.fixture
def api():
    fake = FakeApi()
    yield fake
    fake.close()


def run_session(api: FakeApi, log_dir: Path, calls: list[tuple[str, dict]], version: str | None = V02) -> tuple[dict, list]:
    """经命令入口拉起 server 子进程（version 为 None 时不设 TKOS_WORLD_CONTRACT_VERSION），列工具、依次调用。"""
    env = {"PYTHONPATH": str(ROOT / "src"), "TKOS_WORLD_API_URL": api.url, "TKOS_WORLD_AGENT_TOKEN": TOKEN,
           "TKOS_WORLD_MCP_LOG_DIR": str(log_dir), **({"TKOS_WORLD_CONTRACT_VERSION": version} if version is not None else {})}
    params = StdioServerParameters(command=sys.executable, args=["-m", "tkos_world_mcp.cli"], cwd=str(ROOT), env=env)

    async def main():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            results = [await session.call_tool(name, arguments) for name, arguments in calls]
            return tools, results
    return anyio.run(main)


def text(result) -> str:
    return result.content[0].text


def log_lines(log_dir: Path) -> list[dict]:
    files = list(log_dir.iterdir())
    assert len(files) == 1 and TOKEN not in files[0].read_text()
    return [json.loads(line) for line in files[0].read_text().splitlines()]


def test_the_0_2_server_offers_exactly_the_implemented_agent_face(api, tmp_path):
    tools, _ = run_session(api, tmp_path, [])
    assert set(tools) == READS | WRITES and len(tools) == 13
    face = REGISTRY["agent_face"]
    # 与登记一致：读是登记的读（加 world_ 前缀），写是登记的写。
    assert {f"world_{name}" for name in face["reads"]} == READS
    assert set(face["writes"]) == WRITES
    outside = {item["action"] for item in REGISTRY["actions"] if not item["agent_face"]}
    assert not outside & set(tools) and {"world_assign", "world_relate", "world_create_object",
                                         "world_mark_core_battle", "world_grant_delegation"} <= outside
    # 开始、交付的目标取自取对象 business 组；代记只走 HTTP，参数里没有 on_behalf_of。
    for name in ("world_start", "world_deliver", "world_revise_object"):
        assert set(tools[name].input_schema["properties"]["target"]["required"]) == {
            "object_id", "revision_id", "expected_version"} and "business" in tools[name].description
    assert not any("on_behalf_of" in tool.input_schema["properties"] for tool in tools.values())
    assert "business" in tools["world_get_object"].description and "只做记录" in tools["world_get_context"].description


def test_tools_outside_the_0_2_agent_face_and_on_behalf_writes_are_refused_before_any_http_call(api, tmp_path):
    outside = sorted({item["action"] for item in REGISTRY["actions"] if not item["agent_face"]}
                     | {"world_get_children"})
    calls = [(name, {"target": TARGET}) for name in outside]
    calls += [("world_start", {"target": TARGET, "on_behalf_of": {"principal_id": AGENT}}),
              ("world_deliver", {"target": TARGET, "declaration": DECLARATION,
                                 "on_behalf_of": {"principal_id": AGENT, "external_record_id": "x",
                                                  "external_confirmed_at": "2026-09-28T00:00:00Z"}}),
              ("world_start", {"declaration": DECLARATION}),
              ("world_deliver", {"target": {"object_id": TASK, "revision_id": REV}, "declaration": DECLARATION}),
              ("world_record_event", {"category": "meeting", "subject_refs": [f"{TASK}@3"], "occurred_at": "x",
                                      "content": {"text": "x"}, "action_type": "world_assign"})]
    _, results = run_session(api, tmp_path, calls)
    assert all(result.is_error for result in results) and api.requests == []
    lines = log_lines(tmp_path)
    assert [(line["tool"], line["status"], line["error_code"]) for line in lines] == [
        (name, None, "INVALID_ARGUMENTS") for name, _ in calls]
    assert "unknown tool world_assign" in text(results[outside.index("world_assign")])


def test_0_2_reads_forward_to_the_same_http_endpoints_with_the_agent_token(api, tmp_path):
    base = f"/v1/world/objects/{TASK}"
    _, results = run_session(api, tmp_path, [
        ("world_get_object", {"object_id": TASK}),
        ("world_get_object", {"object_id": TASK, "version": 2}),
        ("world_get_context", {"object_id": TASK, "question": "为什么做？", "budget": {"max_chars": 2000},
                               "recent_days": 7}),
        ("world_get_events", {"object_id": TASK, "since": "2026-09-23T08:00:00+08:00"}),
        ("world_get_state", {"object_id": TASK, "as_of": "2026-09-28T00:00:00Z"}),
    ])
    # 读端点按对象绑定的契约版本出形状，请求本身不带契约版本。
    assert [(r["method"], r["path"], r["query"], r["body"]) for r in api.requests] == [
        ("GET", base, {}, None), ("GET", base, {"version": ["2"]}, None),
        ("POST", base + "/context", {}, {"question": "为什么做？", "budget": {"max_chars": 2000}, "recent_days": 7}),
        ("GET", base + "/events", {"since": ["2026-09-23T08:00:00+08:00"]}, None),
        ("GET", base + "/state", {"as_of": ["2026-09-28T00:00:00Z"]}, None)]
    assert {r["authorization"] for r in api.requests} == {f"Bearer {TOKEN}"}
    assert not any(result.is_error for result in results)
    assert results[0].structured_content == TASK_VIEW and results[0].structured_content["business"]["object_version"] == 4
    assert json.loads(text(results[3])) == api.reply("GET", base + "/events", None)[1]


def test_0_2_list_objects_forwards_filters_and_cursor_and_logs_headers_as_references_only(api, tmp_path):
    """列对象（#63）：GET /v1/world/objects，给了的筛选与分页原样作查询参数；返回原样交出。对象头不带块与组件，
    运行日志不把它当读到的内容：read_refs 为空，推出生命周期的事件只进 event_ids。"""
    _, results = run_session(api, tmp_path, [
        ("world_list_objects", {}),
        ("world_list_objects", {"unit_id": UNIT, "type": "Mission", "period": "2026-10", "limit": 20,
                                "cursor": "cursor-2"}),
        ("world_list_objects", {"domain_id": UNIT, "external_system": "tianshu", "external_id": "card:12"}),
        ("world_list_objects", {"object_id": TASK}),
        ("world_list_objects", {"limit": 0}),
    ])
    assert [(r["method"], r["path"], r["query"], r["body"]) for r in api.requests] == [
        ("GET", "/v1/world/objects", {}, None),
        ("GET", "/v1/world/objects", {"unit_id": [UNIT], "type": ["Mission"], "period": ["2026-10"], "limit": ["20"],
                                      "cursor": ["cursor-2"]}, None),
        ("GET", "/v1/world/objects", {"domain_id": [UNIT], "external_system": ["tianshu"],
                                      "external_id": ["card:12"]}, None)]
    assert [result.structured_content for result in results[:3]] == [LISTED] * 3
    assert [result.is_error for result in results] == [False, False, False, True, True]
    lines = log_lines(tmp_path)
    assert [(line["status"], line["error_code"]) for line in lines] == [(200, None)] * 3 + [
        (None, "INVALID_ARGUMENTS")] * 2
    assert all(line["read_refs"] == [] and line["read_event_ids"] == [] and line["refs"] == []
               and line["event_ids"] == [START] for line in lines[:3])


def test_each_0_2_write_prepares_then_commits_the_same_command_under_contract_0_2(api, tmp_path):
    snapshot = {"title": "进展", "subject_ref": f"{TASK}@3", "as_of": "2026-09-28T02:00:00Z",
                "payload_type": "execution_state", "source_event_refs": [f"event:{EVENT}"],
                "blocks": {"progress": {"text": "改了一半"}}}
    calls = [
        ("world_record_event", {"category": "correction", "subject_refs": [f"{TASK}@3#acceptance/ac-1"],
                                "occurred_at": "2026-09-28T01:00:00Z", "content": {"text": "更正评审"},
                                "supersedes_event_id": EVENT, "declaration": DECLARATION,
                                "idempotency_key": "mcp-v02-record-0001"}),
        ("world_refresh_state", {"payload": snapshot, "declaration": DECLARATION}),
        ("world_revise_object", {"target": TARGET, "payload": {"blocks": {"plan": {"components": [
            {"id": "p-1", "type": "plan_item", "text": "先写测试"}]}}}, "declaration": DECLARATION}),
        ("world_start", {"target": TARGET, "declaration": DECLARATION, "idempotency_key": "mcp-v02-start-00001"}),
        ("world_deliver", {"target": TARGET, "content": {"text": "交付说明", "artifacts": ["https://example.test/d"]},
                           "declaration": DECLARATION}),
        ("world_start", {"target": TARGET, "outcome": "withdrawn", "supersedes_event_id": START,
                         "declaration": DECLARATION}),
    ]
    _, results = run_session(api, tmp_path, calls)
    assert not any(result.is_error for result in results)
    assert [(r["method"], r["path"]) for r in api.requests] == [
        ("POST", "/v1/actions/prepare"), ("POST", "/v1/actions")] * len(calls)
    for (name, arguments), prepare, commit in zip(calls, api.requests[::2], api.requests[1::2]):
        key = prepare["body"]["idempotency_key"]
        assert prepare["body"] == {
            "action_type": name, "contract_version": V02, "target": arguments.get("target"), "expected_versions": [],
            "idempotency_key": arguments.get("idempotency_key", key), "reason": REASON,
            "params": {k: v for k, v in arguments.items() if k not in {"target", "idempotency_key"}}}
        assert len(key) >= 16 and commit["body"] == {
            **prepare["body"], "expected_versions": [{"object_id": TASK, "expected_version": 4}]}
    assert results[3].structured_content["result"]["contract_version"] == V02
    lines = log_lines(tmp_path)
    assert [line["idempotency_key"] for line in lines] == [r["body"]["idempotency_key"] for r in api.requests[::2]]


def test_the_three_issue_tools_prepare_then_commit_without_a_target_under_contract_0_2(api, tmp_path):
    """提出问题、路由问题、退回形成（#61）：名即动作名、参数即动作参数，以 issue_ref（问题组件的组件引用）指明问题、
    不带目标；路由另带承接人。承接与处置只由人记、不在 Agent 面上，代记也不在。"""
    issue_ref = f"{SNAPSHOT}@1#issues/iss-1"
    calls = [
        ("world_raise_issue", {"issue_ref": issue_ref, "content": {"text": "排期冲突要人判断",
                                                                   "refs": [f"event:{EVENT}"]},
                               "declaration": DECLARATION, "idempotency_key": "mcp-v02-raise-00001"}),
        ("world_route_issue", {"issue_ref": issue_ref, "to_principal_id": AGENT, "declaration": DECLARATION}),
        ("world_return_issue", {"issue_ref": issue_ref, "content": {"text": "缺核心判断问题，退回补齐"},
                                "declaration": DECLARATION}),
    ]
    tools, results = run_session(api, tmp_path, calls + [
        ("world_route_issue", {"issue_ref": issue_ref, "declaration": DECLARATION}),              # 缺承接人
        ("world_raise_issue", {"target": TARGET, "issue_ref": issue_ref, "declaration": DECLARATION}),
        ("world_return_issue", {"issue_ref": issue_ref, "declaration": DECLARATION,
                                "on_behalf_of": {"principal_id": AGENT, "external_record_id": "x",
                                                 "external_confirmed_at": "2026-09-28T00:00:00Z"}})])
    for name in ("world_raise_issue", "world_route_issue", "world_return_issue"):
        schema = tools[name].input_schema
        assert "issue_ref" in schema["required"] and "target" not in schema["properties"]
        assert "组件引用" in tools[name].description
    assert tools["world_route_issue"].input_schema["required"] == ["issue_ref", "to_principal_id"]
    assert [result.is_error for result in results] == [False] * 3 + [True] * 3
    assert [(r["method"], r["path"]) for r in api.requests] == [
        ("POST", "/v1/actions/prepare"), ("POST", "/v1/actions")] * len(calls)
    for (name, arguments), prepare, commit in zip(calls, api.requests[::2], api.requests[1::2]):
        key = prepare["body"]["idempotency_key"]
        assert prepare["body"] == {
            "action_type": name, "contract_version": V02, "target": None, "expected_versions": [],
            "idempotency_key": arguments.get("idempotency_key", key), "reason": REASON,
            "params": {k: v for k, v in arguments.items() if k != "idempotency_key"}}
        assert commit["body"] == {**prepare["body"], "expected_versions": [{"object_id": TASK, "expected_version": 4}]}
    lines = log_lines(tmp_path)
    assert [(line["tool"], line["status"], line["error_code"]) for line in lines] == [
        (name, 200, None) for name, _ in calls] + [(name, None, "INVALID_ARGUMENTS") for name in (
            "world_route_issue", "world_raise_issue", "world_return_issue")]
    assert [line["idempotency_key"] for line in lines[:3]] == [r["body"]["idempotency_key"] for r in api.requests[::2]]


def test_a_0_2_http_refusal_is_returned_verbatim_and_nothing_is_committed(api, tmp_path):
    refused = {"object_id": REFUSED, "revision_id": REV, "expected_version": 1}
    _, results = run_session(api, tmp_path, [
        ("world_start", {"target": refused, "declaration": DECLARATION}),
        ("world_deliver", {"target": TARGET}),
    ])
    assert [json.loads(text(result)) for result in results] == [FORBIDDEN, REFUSAL]
    assert all(result.is_error for result in results)
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare"] * 2
    assert [(line["status"], line["error_code"]) for line in log_lines(tmp_path)] == [(403, "FORBIDDEN"),
                                                                                       (422, "INVALID_REQUEST")]


def test_the_0_2_run_log_records_component_and_event_references(api, tmp_path):
    run_session(api, tmp_path, [(name, {"object_id": TASK}) for name in
                                ("world_get_object", "world_get_state", "world_get_events")])
    got, state, events = log_lines(tmp_path)
    task_read = {f"{TASK}@3", f"{TASK}@3#definition", f"{TASK}@3#acceptance", f"{TASK}@3#constraint",
                 f"{TASK}@3#acceptance/ac-1", f"{TASK}@3#acceptance/ac-2"}
    snapshot_read = {f"{SNAPSHOT}@1", f"{SNAPSHOT}@1#progress", f"{SNAPSHOT}@1#issues", f"{SNAPSHOT}@1#issues/iss-1"}
    # 取对象：它这一版、它的块与块里的组件，连同顺带返回的最新快照；组件引用整条记下（不截到块）。
    assert got["read_refs"] == sorted(task_read | snapshot_read) and got["read_event_ids"] == []
    # 块内与组件里引用的组件、父级、来源事件、推出生命周期的事件与委托的登记事件只以引用形式出现。
    assert {f"{MISSION}@2#acceptance/m-ac1", f"{MISSION}@2", f"event:{EVENT}", f"event:{DELEGATION}"} <= set(got["refs"])
    assert task_read | snapshot_read <= set(got["refs"])
    assert set(got["event_ids"]) == {START, EVENT, DELEGATION}
    assert state["read_refs"] == sorted(snapshot_read) and f"event:{EVENT}" in state["refs"]
    # 取事件：列出的事件读到了；以组件为主体的引用与内容里的引用只是引用。
    assert (events["read_refs"], events["read_event_ids"]) == ([], [EVENT])
    assert {f"{TASK}@3#acceptance/ac-1", f"{TASK}@3", f"{MISSION}@2#play"} <= set(events["refs"])


def test_0_2_get_context_hands_only_the_four_items_and_logs_what_the_pack_holds(api, tmp_path):
    _, results = run_session(api, tmp_path, [("world_get_context", {"object_id": TASK, "question": "为什么？"})])
    shown = json.loads(text(results[0]))
    assert shown == {"context_pack_id": PACK, "markdown": CONTEXT_PACK["markdown"], "coverage": COVERAGE,
                     "budget": {"max_chars": 12000, "used_chars": 33, "over_budget": False,
                                "trimmed": {"over_level_cap": {"event": 1}}}}
    assert results[0].structured_content == shown and not results[0].is_error
    (line,) = log_lines(tmp_path)
    assert (line["context_pack_id"], line["used_chars"], line["chars"]) == (PACK, 33, len(text(results[0])))
    assert line["read_refs"] == sorted({f"{TASK}@3", f"{TASK}@3#acceptance", f"{TASK}@3#acceptance/ac-1",
                                        f"{TASK}@3#acceptance/ac-2", f"{SNAPSHOT}@1", f"{SNAPSHOT}@1#progress",
                                        f"{SNAPSHOT}@1#issues", f"{SNAPSHOT}@1#issues/iss-1"})
    assert line["read_event_ids"] == [EVENT] and {START, EVENT} <= set(line["event_ids"])
    assert {f"event:{EVENT}", f"event:{START}", f"{MISSION}@2#acceptance/m-ac1"} <= set(line["refs"])
    # 检索计划里裁掉的事件不是包的内容。
    assert f"event:{TRIMMED}" not in line["refs"] and TRIMMED not in line["event_ids"]


@pytest.mark.parametrize("version", [None, "", " ", V01])
def test_an_unset_empty_or_explicit_0_1_keeps_the_0_1_face(api, tmp_path, version):
    tools, results = run_session(api, tmp_path, [("world_record_event", {
        "category": "meeting", "subject_refs": [f"{TASK}@3"], "occurred_at": "2026-09-28T00:00:00Z",
        "content": {"text": "x"}, "declaration": DECLARATION})], version=version)
    assert set(tools) == {"world_get_object", "world_get_context", "world_get_events", "world_get_state",
                          "world_revise_object", "world_refresh_state", "world_record_event"}
    assert {r["body"]["contract_version"] for r in api.requests} == {V01} and not results[0].is_error


@pytest.mark.parametrize("version", ["tkos.world/0.3", "0.2", "tkos.method/0.5", "TKOS.WORLD/0.2"])
def test_an_unknown_contract_version_stops_the_server_at_start(version, tmp_path):
    run = subprocess.run([sys.executable, "-m", "tkos_world_mcp.cli"], cwd=ROOT, capture_output=True, text=True,
                         timeout=60, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin",
                                          "TKOS_WORLD_API_URL": "http://127.0.0.1:9", "TKOS_WORLD_AGENT_TOKEN": TOKEN,
                                          "TKOS_WORLD_MCP_LOG_DIR": str(tmp_path),
                                          "TKOS_WORLD_CONTRACT_VERSION": version})
    assert run.returncode == 2, run.stderr[-500:]
    assert "TKOS_WORLD_CONTRACT_VERSION" in run.stderr and V02 in run.stderr and "Traceback" not in run.stderr
    assert TOKEN not in run.stderr and list(tmp_path.iterdir()) == []
