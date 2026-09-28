"""tkos-world（HTTP 面的命令行薄封装）：打一个假的 HTTP 面，断言读投影与取上下文的转发（路径、查询、请求体、
凭证）、动作先 prepare 再 commit 同一条命令、--prepare-only、拒绝原样打出且不提交、对象 id 不是 UUID 时不发请求、
Agent 面之外的动作在发请求之前就拒绝、缺环境变量与不可达时的退出码，以及 commit 断连时交回幂等键。不连数据库，
也不启动真 API。
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from tkos_world_cli.cli import main

TOKEN = "fake-token-for-tests-only"
OBJ = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
REV = "1b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
RECEIPT = "2b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
REFUSAL = {"error": {"code": "INVALID_REQUEST", "message": "refused"}}
VERSIONS = [{"object_id": OBJ, "expected_version": 5}]


class FakeApi:
    """记下收到的每个请求，按路径回固定的响应；reason 为 refuse 的动作在 prepare 就回 422。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_one(self, method: str) -> None:
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                api.requests.append({"method": method, "path": url.path, "query": parse_qs(url.query), "body": body,
                                     "authorization": self.headers.get("Authorization")})
                status, reply = api.reply(url.path, body)
                if reply is None:  # 不作答就断开：commit 可能已经在服务端提交
                    self.close_connection = True
                    return
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

    def reply(self, path: str, body: dict | None) -> tuple[int, dict | str | None]:
        if path.startswith("/v1/world/objects/"):
            return 200, {"path": path, "title": "改建模材料"}
        if path == "/v1/actions/prepare":
            if body["reason"] == "refuse":
                return 422, REFUSAL
            if body["reason"] == "no versions":
                return 200, {"ok": True}
            return 200, {"expected_versions": VERSIONS}
        if path == "/v1/actions":
            if body["reason"] == "drop":
                return 0, None
            return 200, {"receipt_id": RECEIPT, "status": "committed"}
        return 404, {"error": {"code": "NOT_FOUND"}}

    def close(self) -> None:
        self.http.shutdown()


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setenv("TKOS_WORLD_API_URL", fake.url + "/")
    monkeypatch.setenv("TKOS_WORLD_TOKEN", TOKEN)
    yield fake
    fake.close()


def test_reads_forward_to_the_http_endpoints_with_the_token(api, capsys):
    base = f"/v1/world/objects/{OBJ}"
    assert main(["get", OBJ]) == 0
    assert json.loads(capsys.readouterr().out) == {"path": base, "title": "改建模材料"}
    assert main(["get", OBJ.upper(), "--version", "2"]) == 0
    assert main(["state", OBJ, "--as-of", "2026-09-24T00:00:00Z"]) == 0
    assert main(["events", OBJ, "--since", "2026-09-23T08:00:00+08:00"]) == 0
    assert main(["children", OBJ]) == 0
    assert main(["context", OBJ, "--question", "为什么做？", "--max-chars", "2000", "--recent-days", "7"]) == 0
    assert main(["context", OBJ, "--question", "做什么？"]) == 0
    assert [(r["method"], r["path"], r["query"], r["body"]) for r in api.requests] == [
        ("GET", base, {}, None), ("GET", base, {"version": ["2"]}, None),
        ("GET", base + "/state", {"as_of": ["2026-09-24T00:00:00Z"]}, None),
        ("GET", base + "/events", {"since": ["2026-09-23T08:00:00+08:00"]}, None),
        ("GET", base + "/children", {}, None),
        ("POST", base + "/context", {}, {"question": "为什么做？", "budget": {"max_chars": 2000}, "recent_days": 7}),
        ("POST", base + "/context", {}, {"question": "做什么？"})]
    assert {r["authorization"] for r in api.requests} == {f"Bearer {TOKEN}"}


def test_an_action_prepares_then_commits_the_same_command(api, capsys, tmp_path, monkeypatch):
    target = {"object_id": OBJ, "revision_id": REV, "expected_version": 5}
    params = {"payload": {"blocks": {"instruction": {"text": "改用新脚本。"}}}}
    (tmp_path / "target.json").write_text(json.dumps(target))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(params)))
    assert main(["act", "world_revise_object", "--params", "-", "--target", f"@{tmp_path / 'target.json'}",
                 "--reason", "按 9/23 会议改", "--idempotency-key", "cli-test-revise-0001"]) == 0
    assert json.loads(capsys.readouterr().out) == {"receipt_id": RECEIPT, "status": "committed"}
    prepare, commit = api.requests
    assert (prepare["path"], commit["path"]) == ("/v1/actions/prepare", "/v1/actions")
    assert prepare["body"] == {"action_type": "world_revise_object", "contract_version": "tkos.world/0.1",
                               "target": target, "expected_versions": [], "idempotency_key": "cli-test-revise-0001",
                               "reason": "按 9/23 会议改", "params": params}
    assert commit["body"] == {**prepare["body"], "expected_versions": VERSIONS}


def test_a_generated_key_is_shared_by_prepare_and_commit(api):
    assert main(["act", "world_refresh_state", "--params", '{"payload": {"title": "周进展"}}', "--reason", "写快照"]) == 0
    prepare, commit = api.requests
    assert prepare["body"]["target"] is None
    assert len(prepare["body"]["idempotency_key"]) >= 16
    assert commit["body"]["idempotency_key"] == prepare["body"]["idempotency_key"]


def test_prepare_only_does_not_commit(api, capsys):
    assert main(["act", "world_record_event", "--params", "{}", "--reason", "只看看", "--prepare-only"]) == 0
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare"]
    assert json.loads(capsys.readouterr().out) == {"expected_versions": VERSIONS}


def test_a_refusal_is_printed_verbatim_and_nothing_is_committed(api, capsys):
    assert main(["act", "world_record_event", "--params", "{}", "--reason", "refuse"]) == 1
    out = capsys.readouterr()
    assert json.loads(out.out) == REFUSAL and "HTTP 422" in out.err
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare"]


def test_a_prepare_without_expected_versions_is_not_committed(api, capsys):
    assert main(["act", "world_record_event", "--params", "{}", "--reason", "no versions"]) == 1
    assert "nothing committed" in capsys.readouterr().err
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare"]


@pytest.mark.parametrize("argv", [["get", "../actions"], ["get", f"{OBJ}\n"], ["children", "x"],
                                  ["act", "world_revise_object", "--params", "{", "--reason", "坏 JSON"],
                                  ["act", "world_revise_object", "--params", "@/nonexistent.json", "--reason", "没文件"],
                                  ["context", OBJ]])
def test_bad_arguments_are_refused_before_any_http_call(api, capsys, argv):
    with pytest.raises(SystemExit) as exit_:
        main(argv)
    assert exit_.value.code == 2
    assert api.requests == []


@pytest.mark.parametrize("action", ["world_create_object", "world_assign", "world_relate", "world_commit_mission",
                                    "world_confirm_period_goal", "world_mark_core_battle", "not_an_action"])
def test_actions_outside_the_agent_face_are_refused_before_any_http_call(api, capsys, action):
    with pytest.raises(SystemExit) as exit_:
        main(["act", action, "--params", "{}", "--reason", "不在白名单"])
    assert exit_.value.code == 2
    assert "world_revise_object" in capsys.readouterr().err  # 用法信息列出允许的三个动作
    assert api.requests == []


def test_missing_environment_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.delenv("TKOS_WORLD_TOKEN", raising=False)
    monkeypatch.setenv("TKOS_WORLD_API_URL", "http://127.0.0.1:9")
    assert main(["get", OBJ]) == 2
    assert "TKOS_WORLD_TOKEN" in capsys.readouterr().err


def test_an_unreachable_api_exits_non_zero_without_a_traceback(monkeypatch, capsys):
    monkeypatch.setenv("TKOS_WORLD_API_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("TKOS_WORLD_TOKEN", TOKEN)
    assert main(["act", "world_record_event", "--params", "{}", "--reason", "不可达"]) == 1
    err = capsys.readouterr().err
    assert "unreachable" in err and "may have been committed" not in err  # prepare 就没到，什么也没提交


def test_a_commit_that_gets_no_answer_hands_back_the_key_for_replay(api, capsys):
    assert main(["act", "world_record_event", "--params", "{}", "--reason", "drop",
                 "--idempotency-key", "cli-test-dropped-0001"]) == 1
    assert "replay it with --idempotency-key cli-test-dropped-0001" in capsys.readouterr().err
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare", "/v1/actions"]
