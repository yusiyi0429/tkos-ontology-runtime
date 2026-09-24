"""tkos-world-mcp（票 #26）：用 mcp 客户端经 stdio 拉起 server 子进程，让它打一个假的 HTTP 面。

断言工具清单、参数校验、对 HTTP 面的透传（路径、查询、请求体、凭证、prepare 再 commit），以及运行日志。
不连数据库，也不启动真 API；真 API 上的四读三写在 acceptance/world_v01 里跑。
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "fake-agent-token-for-tests-only"
OBJ = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
REV = "1b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
EVENT = "2b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
DECLARATION = {"scene": f"{OBJ}@1", "trigger": "会后整理", "human_acceptance": {"required": True, "acceptor": REV}}
REFUSAL = {"error": {"code": "INVALID_REQUEST", "message": "An Agent write must declare its scene, trigger and human acceptance."}}
BROKEN = "9b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
# 形状照真读投影的对象：带内容的块、块内钉定的引用、关系、referenced_by、supersedes、状态快照与事件视图。
ACTIVITY = "3b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
SNAPSHOT = "4b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
OTHER = "5b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
LIFECYCLE_EVENT = "6b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"


def pinned(object_id: str, version: int, block: str | None = None) -> dict:
    return {"object_id": object_id, "object_version": version, "block": block,
            "ref": f"{object_id}@{version}" + (f"#{block}" if block else "")}


def block(object_id: str, version: int, name: str, text: str | None, refs: list | None = None) -> dict:
    return {"id": name, "display_name": name, "kind": "definition", "empty": text is None,
            "value": text and {"text": text, "refs": refs or [], "artifacts": []},
            "text": text or f"当前没有{name}", "ref": f"{object_id}@{version}#{name}"}


EVENT_VIEW = {"event_id": EVENT, "kind": "external", "category": "meeting", "occurred_at": "2026-09-23T02:23:00Z",
              "recorded_at": "2026-09-24T00:00:00Z", "subject_refs": [pinned(ACTIVITY, 2)],
              "content": {"text": "会上定了改法", "refs": [pinned(OTHER, 1, "definition")]}}
SNAPSHOT_VIEW = {"object_id": SNAPSHOT, "object_type": "StateSnapshot", "version": 1, "revision_id": REV,
                 "title": "9/23 进展", "attributes": {"subject_ref": pinned(ACTIVITY, 2)},
                 "blocks": [block(SNAPSHOT, 1, "progress", "改了一半")], "relations": [], "referenced_by": [],
                 "supersedes": None}
ACTIVITY_VIEW = {"object_id": ACTIVITY, "object_type": "Activity", "version": 2, "revision_id": REV, "object_version": 4,
                 "title": "改建模材料", "attributes": {"responsible": None},
                 "blocks": [block(ACTIVITY, 2, "instruction", "按 9/23 会议改", [pinned(OBJ, 3, "acceptance")]),
                            block(ACTIVITY, 2, "constraint", None)],
                 "relations": [{"field": "parent_ref", "relation": "contains", "value": pinned(OBJ, 3)}],
                 "referenced_by": [{**pinned(OTHER, 1), "field": "depends_on"}], "supersedes": pinned(ACTIVITY, 1),
                 "formal": {"lifecycle_status": "draft", "effective_revision_id": None}, "state": SNAPSHOT_VIEW}
ACTIVITY_PACK = {"question": "为什么？", "markdown": "…", "layers": [{
    "level": 0,
    "object": {"object_id": ACTIVITY, "version": 2, "title": "改建模材料", "ref": f"{ACTIVITY}@2",
               "pinned": pinned(ACTIVITY, 2), "lifecycle": {"status": "assigned", "event_id": LIFECYCLE_EVENT},
               "responsible": []},
    "blocks": [{**block(ACTIVITY, 2, "instruction", "按 9/23 会议改", [pinned(OBJ, 3, "acceptance")]),
                "pinned": pinned(ACTIVITY, 2, "instruction")}],
    "relations": [{"field": "depends_on", "relation": "depends_on", "refs": [pinned(OTHER, 1)]}],
    "referenced_by": [],
    "state": {"ref": f"{SNAPSHOT}@1", "pinned": pinned(SNAPSHOT, 1), "as_of": "2026-09-23T10:00:00Z", "unconfirmed": True,
              "blocks": [{**block(SNAPSHOT, 1, "progress", "改了一半"), "pinned": pinned(SNAPSHOT, 1, "progress")}]},
    "events": [EVENT_VIEW]}]}
REASON = "Agent write through tkos-world-mcp"
TOOLS = {"world_get_object", "world_get_context", "world_get_events", "world_get_state",
         "world_revise_object", "world_refresh_state", "world_record_event"}


class FakeApi:
    """记下收到的每个请求，按路径回固定的响应；没带声明的写入按 HTTP 面的规则回 422。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 不往测试输出里打请求日志
                pass

            def handle_one(self, method: str) -> None:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                api.requests.append({"method": method, "path": url.path, "query": parse_qs(url.query), "body": body,
                                     "authorization": self.headers.get("Authorization")})
                status, reply = api.reply(method, url.path, body)
                raw = (reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self.handle_one("GET")

            def do_POST(self):
                self.handle_one("POST")

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"

    def reply(self, method: str, path: str, body: dict | None) -> tuple[int, dict | str]:
        base = f"/v1/world/objects/{OBJ}"
        if path == f"/v1/world/objects/{BROKEN}":
            return 502, "<html>bad gateway</html>"
        if path == f"/v1/world/objects/{BROKEN}/state":
            return 401, {"error": "Unauthorized"}
        if path == base:
            return 200, {"object_id": OBJ, "version": 3, "revision_id": REV, "object_version": 5,
                         "blocks": [{"id": "instruction", "ref": f"{OBJ}@3#instruction", "text": "补齐播种脚本。"}]}
        if path == base + "/context":
            return 200, {"context_pack_id": EVENT, "context_pack": {"markdown": f"### 执行指令 `{OBJ}@3#instruction`"},
                         "plan": {"walked": [{"pinned": f"{OBJ}@1"}], "trimmed": [{"key": f"block:{OBJ}@3#constraint"}]},
                         "budget": {"used_chars": 29},
                         "coverage": {"what": {"answered": True, "evidence": [{"ref": f"{OBJ}@3#instruction"}]}}}
        if path == base + "/events":
            return 200, {"object_id": OBJ, "events": [{"event_id": EVENT, "subject_refs": [{"ref": f"{OBJ}@3"}]}]}
        if path == base + "/state":
            return 200, {"object_id": OBJ, "snapshot": None}
        activity = f"/v1/world/objects/{ACTIVITY}"
        if path == activity:
            return 200, ACTIVITY_VIEW
        if path == activity + "/state":
            return 200, {"object_id": ACTIVITY, "as_of": None, "snapshot": SNAPSHOT_VIEW}
        if path == activity + "/events":
            return 200, {"object_id": ACTIVITY, "events": [EVENT_VIEW]}
        if path == activity + "/context":
            return 200, {"context_pack_id": REV, "context_pack": ACTIVITY_PACK, "budget": {"used_chars": 1},
                         "plan": {"walked": [{"pinned": f"{OBJ}@3"}]}}
        if path in {"/v1/actions/prepare", "/v1/actions"}:
            if not body["params"].get("declaration"):
                return 422, REFUSAL
            if (body["params"].get("payload") or {}).get("title") == "prepare without versions" and path.endswith("prepare"):
                return 200, {"ok": True}
            if path.endswith("prepare"):
                return 200, {"expected_versions": [{"object_id": OBJ, "expected_version": 5}]}
            return 200, {"receipt_id": EVENT, "status": "committed", "result": {"ref": f"{OBJ}@4", "event_id": EVENT}}
        return 404, {"error": {"code": "NOT_FOUND"}}

    def close(self) -> None:
        self.http.shutdown()


@pytest.fixture
def api():
    fake = FakeApi()
    yield fake
    fake.close()


def run_session(api: FakeApi, log_dir: Path, calls: list[tuple[str, dict]], url: str | None = None) -> tuple[list[str], list]:
    """经命令入口拉起 server 子进程，列工具、依次调用，返回工具名与每次调用的结果。"""
    params = StdioServerParameters(command=sys.executable, args=["-m", "tkos_world_mcp.cli"], cwd=str(ROOT),
                                   env={"PYTHONPATH": str(ROOT / "src"), "TKOS_WORLD_API_URL": url or api.url,
                                        "TKOS_WORLD_AGENT_TOKEN": TOKEN, "TKOS_WORLD_MCP_LOG_DIR": str(log_dir)})

    async def main():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = [tool.name for tool in (await session.list_tools()).tools]
            results = [await session.call_tool(name, arguments) for name, arguments in calls]
            return names, results
    return anyio.run(main)


def text(result) -> str:
    return result.content[0].text


def test_the_server_offers_exactly_four_reads_and_three_writes(api, tmp_path):
    names, _ = run_session(api, tmp_path, [])
    assert set(names) == TOOLS and len(names) == len(TOOLS)
    assert not [name for name in names if any(word in name for word in ("commit", "confirm", "assign", "relate", "mark"))]


def test_reads_forward_to_the_http_endpoints_with_the_agent_token(api, tmp_path):
    budget = {"max_chars": 2000}
    _, results = run_session(api, tmp_path, [
        ("world_get_object", {"object_id": OBJ}),
        ("world_get_object", {"object_id": OBJ, "version": 2}),
        ("world_get_context", {"object_id": OBJ, "question": "为什么做？", "budget": budget, "recent_days": 7}),
        ("world_get_events", {"object_id": OBJ, "since": "2026-09-23T08:00:00+08:00"}),
        ("world_get_state", {"object_id": OBJ, "as_of": "2026-09-24T00:00:00Z"}),
    ])
    base = f"/v1/world/objects/{OBJ}"
    assert [(r["method"], r["path"], r["query"], r["body"]) for r in api.requests] == [
        ("GET", base, {}, None), ("GET", base, {"version": ["2"]}, None),
        ("POST", base + "/context", {}, {"question": "为什么做？", "budget": budget, "recent_days": 7}),
        ("GET", base + "/events", {"since": ["2026-09-23T08:00:00+08:00"]}, None),
        ("GET", base + "/state", {"as_of": ["2026-09-24T00:00:00Z"]}, None)]
    assert {r["authorization"] for r in api.requests} == {f"Bearer {TOKEN}"}
    assert not any(result.is_error for result in results)
    assert results[0].structured_content == api.reply("GET", base, None)[1]
    assert json.loads(text(results[3])) == api.reply("GET", base + "/events", None)[1]


def test_a_write_prepares_then_commits_the_same_command(api, tmp_path):
    target = {"object_id": OBJ, "revision_id": REV, "expected_version": 5}
    payload = {"blocks": {"instruction": {"text": "改用新脚本。"}}}
    _, results = run_session(api, tmp_path, [
        ("world_revise_object", {"target": target, "payload": payload, "declaration": DECLARATION,
                                 "idempotency_key": "mcp-test-revise-0001"}),
        ("world_refresh_state", {"payload": {"title": "x", "subject_ref": f"{OBJ}@3", "as_of": "2026-09-24T00:00:00Z"},
                                 "declaration": DECLARATION}),
    ])
    prepare, commit, prepare2, commit2 = api.requests
    assert [(r["method"], r["path"]) for r in api.requests] == [
        ("POST", "/v1/actions/prepare"), ("POST", "/v1/actions")] * 2
    assert prepare["body"] == {"action_type": "world_revise_object", "contract_version": "tkos.world/0.1",
                               "target": target, "expected_versions": [], "idempotency_key": "mcp-test-revise-0001",
                               "reason": REASON, "params": {"payload": payload, "declaration": DECLARATION}}
    assert commit["body"] == {**prepare["body"], "expected_versions": [{"object_id": OBJ, "expected_version": 5}]}
    assert prepare2["body"]["target"] is None and prepare2["body"]["action_type"] == "world_refresh_state"
    assert len(prepare2["body"]["idempotency_key"]) >= 16
    assert commit2["body"]["idempotency_key"] == prepare2["body"]["idempotency_key"]
    assert results[0].structured_content["result"]["ref"] == f"{OBJ}@4" and not results[0].is_error


def test_an_http_refusal_is_returned_verbatim_and_nothing_is_committed(api, tmp_path):
    event = {"category": "meeting", "subject_refs": [f"{OBJ}@3"], "occurred_at": "2026-09-24T00:00:00Z",
             "content": {"text": "会议纪要。"}}
    _, results = run_session(api, tmp_path, [("world_record_event", event)])
    assert results[0].is_error and json.loads(text(results[0])) == REFUSAL
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare"]
    assert api.requests[0]["body"]["params"] == event


@pytest.mark.parametrize("name, arguments", [
    ("world_get_object", {}),
    ("world_get_object", {"object_id": "not-a-uuid"}),
    ("world_get_object", {"object_id": OBJ + "\n"}),
    ("world_get_object", {"object_id": OBJ, "revision": 2}),
    ("world_get_context", {"object_id": OBJ}),
    ("world_revise_object", {"payload": {}, "declaration": DECLARATION}),
    ("world_revise_object", {"target": {"object_id": OBJ}, "payload": {}}),
    ("world_record_event", {"category": "meeting", "subject_refs": [f"{OBJ}@3"], "occurred_at": "x",
                            "content": {"text": "x"}, "action_type": "world_confirm_mission"}),
    ("world_confirm_mission", {"object_id": OBJ}),
])
def test_arguments_outside_a_tools_shape_are_refused_before_any_http_call(api, tmp_path, name, arguments):
    _, results = run_session(api, tmp_path, [(name, arguments)])
    assert results[0].is_error and api.requests == []


def test_every_call_is_logged_with_its_arguments_references_and_status(api, tmp_path):
    calls = [("world_get_object", {"object_id": OBJ}),
             ("world_get_events", {"object_id": OBJ}),
             ("world_record_event", {"category": "meeting", "subject_refs": [f"{OBJ}@3"],
                                     "occurred_at": "2026-09-24T00:00:00Z", "content": {"text": "x"}}),
             ("world_get_object", {})]
    _, results = run_session(api, tmp_path, calls)
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and TOKEN not in files[0].read_text()
    lines = [json.loads(line) for line in files[0].read_text().splitlines()]
    assert "idempotency_key" in lines[2] and len(lines[2]["idempotency_key"]) >= 16 and "idempotency_key" not in lines[0]
    assert [(line["seq"], line["tool"], line["arguments"], line["status"], line["error_code"]) for line in lines] == [
        (1, "world_get_object", calls[0][1], 200, None), (2, "world_get_events", calls[1][1], 200, None),
        (3, "world_record_event", calls[2][1], 422, "INVALID_REQUEST"), (4, "world_get_object", {}, None, "INVALID_ARGUMENTS")]
    assert lines[0]["refs"] == [f"{OBJ}@3#instruction"] and lines[0]["event_ids"] == []
    assert lines[1]["refs"] == [f"{OBJ}@3"] and lines[1]["event_ids"] == [EVENT]
    assert all(line["chars"] == len(text(result)) for line, result in zip(lines, results))
    assert len({line["session"] for line in lines}) == 1 and all(line["at"].endswith("Z") for line in lines)


def test_a_null_declaration_and_upper_case_ids_reach_the_http_face(api, tmp_path):
    _, results = run_session(api, tmp_path, [
        ("world_refresh_state", {"payload": {"title": "x"}, "declaration": None}),
        ("world_get_object", {"object_id": OBJ.upper()}),
    ])
    assert results[0].is_error and json.loads(text(results[0])) == REFUSAL
    assert [r["path"] for r in api.requests] == [
        "/v1/actions/prepare", f"/v1/world/objects/{OBJ.upper()}"]


def test_non_json_and_unexpected_http_answers_come_back_as_errors_and_are_logged(api, tmp_path):
    _, results = run_session(api, tmp_path, [
        ("world_get_object", {"object_id": BROKEN}),
        ("world_get_state", {"object_id": BROKEN}),
        ("world_refresh_state", {"payload": {"title": "prepare without versions"}, "declaration": DECLARATION}),
    ])
    assert [result.is_error for result in results] == [True, True, True]
    assert text(results[0]) == "<html>bad gateway</html>" and json.loads(text(results[1])) == {"error": "Unauthorized"}
    assert json.loads(text(results[2]))["error"]["code"] == "HTTP_UNEXPECTED"
    assert [r["path"] for r in api.requests][-1] == "/v1/actions/prepare"
    lines = [json.loads(line) for line in next(tmp_path.iterdir()).read_text().splitlines()]
    assert [(line["status"], line["error_code"]) for line in lines] == [(502, None), (401, None), (200, "HTTP_UNEXPECTED")]


def test_an_unreachable_http_face_returns_the_generated_key_so_the_write_can_be_replayed(api, tmp_path):
    _, results = run_session(api, tmp_path, [("world_refresh_state", {"payload": {"title": "x"}, "declaration": DECLARATION})],
                             url="http://127.0.0.1:9")
    error = json.loads(text(results[0]))["error"]
    assert results[0].is_error and error["code"] == "HTTP_UNAVAILABLE" and len(error["idempotency_key"]) >= 16


def test_a_log_that_cannot_be_written_does_not_turn_a_committed_write_into_an_error(api, tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x")
    _, results = run_session(api, blocked / "runs", [
        ("world_refresh_state", {"payload": {"title": "x"}, "declaration": DECLARATION})])
    assert not results[0].is_error and [r["path"] for r in api.requests] == ["/v1/actions/prepare", "/v1/actions"]


def test_the_context_log_counts_only_what_the_pack_holds_and_its_markdown_length(api, tmp_path):
    run_session(api, tmp_path, [("world_get_context", {"object_id": OBJ, "question": "为什么？"})])
    line = json.loads(next(tmp_path.iterdir()).read_text())
    assert line["refs"] == [f"{OBJ}@3#instruction"] and line["used_chars"] == 29 and line["context_pack_id"] == EVENT


def test_the_log_tells_what_came_back_with_its_content_from_what_was_only_cited(api, tmp_path):
    run_session(api, tmp_path, [(name, {"object_id": ACTIVITY}) for name in
                                ("world_get_object", "world_get_state", "world_get_events")]
                + [("world_get_context", {"object_id": ACTIVITY, "question": "为什么？"})])
    lines = [json.loads(line) for line in next(tmp_path.iterdir()).read_text().splitlines()]
    # 取对象：它自己这一版和它的块（空块也读到了——读到的是标准句），连同顺带返回的最新快照；
    # 块内引用、父级、反向引用、上一版只是引用。
    assert lines[0]["read_refs"] == sorted([f"{ACTIVITY}@2", f"{ACTIVITY}@2#instruction", f"{ACTIVITY}@2#constraint",
                                            f"{SNAPSHOT}@1", f"{SNAPSHOT}@1#progress"])
    assert {f"{OBJ}@3#acceptance", f"{OBJ}@3", f"{OTHER}@1", f"{ACTIVITY}@1"} <= set(lines[0]["refs"])
    assert lines[0]["read_event_ids"] == []
    # 取状态：读到的是快照和它的块，不是它的主体。
    assert lines[1]["read_refs"] == sorted([f"{SNAPSHOT}@1", f"{SNAPSHOT}@1#progress"])
    # 取事件：列出的事件读到了；事件内容里引用的块没读到。
    assert (lines[2]["read_refs"], lines[2]["read_event_ids"]) == ([], [EVENT])
    # 取上下文：一层的对象、留下的块、状态与事件；生命周期里钉的事件、跨链关系只是引用。
    assert lines[3]["read_refs"] == sorted([f"{ACTIVITY}@2", f"{ACTIVITY}@2#instruction",
                                            f"{SNAPSHOT}@1", f"{SNAPSHOT}@1#progress"])
    assert lines[3]["read_event_ids"] == [EVENT] and LIFECYCLE_EVENT in lines[3]["event_ids"]
