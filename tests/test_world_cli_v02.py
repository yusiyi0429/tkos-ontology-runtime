"""tkos-world 的 0.2 Agent 面（票 #57、#63）：TKOS_WORLD_CONTRACT_VERSION 选 tkos.world/0.2 时打一个假的 HTTP 面。

断言子命令与 act 的动作清单等于契约第 9.3 节 Agent 面里已实现的部分（登记 agent_face 去掉三个 Issue 动作），
面外的动作、取子对象与代记（--params 带 on_behalf_of）在发请求之前就以用法错误拒绝；每个读与写的请求形状、契约
版本与 prepare 再 commit；版本取值不认识时退出码 2、什么都不发。默认 0.1 的行为由 test_world_cli.py 原样覆盖。
不连数据库，也不启动真 API。
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from tests.test_world_cli import FakeApi, RECEIPT, TOKEN, VERSIONS
from tkos_world_cli.cli import main

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text())
V01, V02 = "tkos.world/0.1", "tkos.world/0.2"
OBJ = "0c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
REV = "1c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
EVENT = "2c5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
TARGET = {"object_id": OBJ, "revision_id": REV, "expected_version": 5}
DECLARATION = {"scene": f"{OBJ}@1", "trigger": "会后整理", "human_acceptance": {"required": False}}
COMMANDS = ["get", "state", "events", "list", "context", "act"]
ACTIONS = ["world_record_event", "world_refresh_state", "world_revise_object", "world_start", "world_deliver"]
# 登记里在 Agent 面上、但 HTTP 面还没实现的：Issue 的提出、路由、退回形成（#61）。
NOT_YET = {"world_raise_issue", "world_route_issue", "world_return_issue"}
LISTED = {"items": [{"object_id": OBJ, "object_type": "Mission", "version": 3}], "next_cursor": "c2"}


class ListingApi(FakeApi):
    """另回列对象：GET /v1/world/objects 回一页对象头。"""

    def reply(self, path, body):
        if path == "/v1/world/objects":
            return 200, LISTED
        return super().reply(path, body)


@pytest.fixture
def api(monkeypatch):
    fake = ListingApi()
    monkeypatch.setenv("TKOS_WORLD_API_URL", fake.url)
    monkeypatch.setenv("TKOS_WORLD_TOKEN", TOKEN)
    monkeypatch.setenv("TKOS_WORLD_CONTRACT_VERSION", V02)
    yield fake
    fake.close()


def choices(err: str) -> list[str]:
    """argparse 拒绝一个取值时列出的可选项。"""
    return [item.strip(" '") for item in re.search(r"\(choose from (.*)\)", err)[1].split(",")]


def test_the_0_2_cli_offers_exactly_the_implemented_agent_face(api, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["children", OBJ])
    assert exit_.value.code == 2 and choices(capsys.readouterr().err) == COMMANDS
    with pytest.raises(SystemExit) as exit_:
        main(["act", "world_assign", "--params", "{}", "--reason", "不在白名单"])
    assert exit_.value.code == 2 and choices(capsys.readouterr().err) == ACTIONS
    assert api.requests == []
    face = REGISTRY["agent_face"]
    assert set(face["writes"]) - NOT_YET == set(ACTIONS)
    assert {name for name in face["reads"] if name not in NOT_YET} == {"get_object", "get_context", "get_events",
                                                                        "get_state", "list_objects"}


def test_0_2_reads_forward_to_the_http_endpoints_with_the_token(api, capsys):
    base = f"/v1/world/objects/{OBJ}"
    assert main(["get", OBJ]) == 0
    assert json.loads(capsys.readouterr().out) == {"path": base, "title": "改建模材料"}
    assert main(["get", OBJ, "--version", "2"]) == 0
    assert main(["state", OBJ, "--as-of", "2026-09-28T00:00:00Z"]) == 0
    assert main(["events", OBJ, "--since", "2026-09-23T08:00:00+08:00"]) == 0
    assert main(["context", OBJ, "--question", "为什么做？", "--max-events-per-object", "3", "--recent-days", "7"]) == 0
    assert [(r["method"], r["path"], r["query"], r["body"]) for r in api.requests] == [
        ("GET", base, {}, None), ("GET", base, {"version": ["2"]}, None),
        ("GET", base + "/state", {"as_of": ["2026-09-28T00:00:00Z"]}, None),
        ("GET", base + "/events", {"since": ["2026-09-23T08:00:00+08:00"]}, None),
        ("POST", base + "/context", {}, {"question": "为什么做？", "budget": {"max_events_per_object": 3},
                                         "recent_days": 7})]
    assert {r["authorization"] for r in api.requests} == {f"Bearer {TOKEN}"}


def test_0_2_list_forwards_each_filter_and_the_cursor_as_query_parameters(api, capsys):
    """列对象（#63）：不带对象 id，给了的筛选与分页原样作查询参数；返回原样打出。"""
    assert main(["list"]) == 0
    assert json.loads(capsys.readouterr().out) == LISTED
    assert main(["list", "--unit-id", OBJ, "--type", "Mission", "--period", "2026-10", "--limit", "20",
                 "--cursor", "c2"]) == 0
    assert main(["list", "--domain-id", REV, "--external-system", "tianshu", "--external-id", "card:12"]) == 0
    assert [(r["method"], r["path"], r["query"]) for r in api.requests] == [
        ("GET", "/v1/world/objects", {}),
        ("GET", "/v1/world/objects", {"unit_id": [OBJ], "type": ["Mission"], "period": ["2026-10"], "limit": ["20"],
                                      "cursor": ["c2"]}),
        ("GET", "/v1/world/objects", {"domain_id": [REV], "external_system": ["tianshu"],
                                      "external_id": ["card:12"]})]
    assert {r["authorization"] for r in api.requests} == {f"Bearer {TOKEN}"}


@pytest.mark.parametrize("action, target, params", [
    ("world_record_event", None, {"category": "correction", "subject_refs": [f"{OBJ}@3#acceptance/ac-1"],
                                  "occurred_at": "2026-09-28T01:00:00Z", "content": {"text": "更正"},
                                  "supersedes_event_id": EVENT, "declaration": DECLARATION}),
    ("world_refresh_state", None, {"payload": {"title": "进展", "subject_ref": f"{OBJ}@3", "as_of": "2026-09-28T02:00:00Z",
                                               "payload_type": "execution_state", "source_event_refs": [f"event:{EVENT}"],
                                               "blocks": {"progress": {"text": "改了一半"}}}, "declaration": DECLARATION}),
    ("world_revise_object", TARGET, {"payload": {"blocks": {"plan": {"text": "先写测试"}}}, "declaration": DECLARATION}),
    ("world_start", TARGET, {"declaration": DECLARATION}),
    ("world_deliver", TARGET, {"content": {"text": "交付说明"}, "declaration": DECLARATION}),
    ("world_start", TARGET, {"outcome": "withdrawn", "supersedes_event_id": EVENT, "declaration": DECLARATION}),
])
def test_each_0_2_action_prepares_then_commits_the_same_command_under_contract_0_2(api, capsys, action, target, params):
    argv = ["act", action, "--params", json.dumps(params, ensure_ascii=False), "--reason", "Agent 面 0.2"]
    assert main(argv + (["--target", json.dumps(target)] if target else [])) == 0
    assert json.loads(capsys.readouterr().out) == {"receipt_id": RECEIPT, "status": "committed"}
    prepare, commit = api.requests
    assert (prepare["path"], commit["path"]) == ("/v1/actions/prepare", "/v1/actions")
    key = prepare["body"]["idempotency_key"]
    assert prepare["body"] == {"action_type": action, "contract_version": V02, "target": target,
                               "expected_versions": [], "idempotency_key": key, "reason": "Agent 面 0.2", "params": params}
    assert len(key) >= 16 and commit["body"] == {**prepare["body"], "expected_versions": VERSIONS}


def test_prepare_only_under_0_2_does_not_commit(api, capsys):
    assert main(["act", "world_start", "--target", json.dumps(TARGET), "--params", "{}", "--reason", "只看看",
                 "--prepare-only"]) == 0
    assert [r["path"] for r in api.requests] == ["/v1/actions/prepare"]
    assert api.requests[0]["body"]["contract_version"] == V02


@pytest.mark.parametrize("action", sorted({item["action"] for item in REGISTRY["actions"] if not item["agent_face"]}
                                          | NOT_YET))
def test_actions_outside_the_0_2_agent_face_are_refused_before_any_http_call(api, capsys, action):
    with pytest.raises(SystemExit) as exit_:
        main(["act", action, "--target", json.dumps(TARGET), "--params", "{}", "--reason", "不在白名单"])
    assert exit_.value.code == 2 and choices(capsys.readouterr().err) == ACTIONS
    assert api.requests == []


@pytest.mark.parametrize("action", ["world_start", "world_deliver"])
def test_on_behalf_recording_is_refused_before_any_http_call(api, capsys, action):
    """代记只走 HTTP、不属于 Agent 面（契约第 9.3、14 节）：开始、交付虽可代记，经 CLI 带 on_behalf_of 也不发请求。"""
    on_behalf = {"principal_id": REV, "external_record_id": "tianshu-1", "external_confirmed_at": "2026-09-28T00:00:00Z"}
    with pytest.raises(SystemExit) as exit_:
        main(["act", action, "--target", json.dumps(TARGET), "--params", json.dumps({"on_behalf_of": on_behalf}),
              "--reason", "代记"])
    assert exit_.value.code == 2 and "on_behalf_of" in capsys.readouterr().err
    assert api.requests == []


@pytest.mark.parametrize("value", ["tkos.world/0.3", "0.2", "tkos.method/0.5", "TKOS.WORLD/0.2"])
def test_an_unknown_contract_version_is_a_usage_error_before_any_http_call(api, capsys, monkeypatch, value):
    monkeypatch.setenv("TKOS_WORLD_CONTRACT_VERSION", value)
    assert main(["get", OBJ]) == 2
    err = capsys.readouterr().err
    assert "TKOS_WORLD_CONTRACT_VERSION" in err and V01 in err and V02 in err and TOKEN not in err
    assert api.requests == []


@pytest.mark.parametrize("value", [None, "", " ", V01])
def test_an_unset_empty_or_explicit_0_1_keeps_the_0_1_face(api, capsys, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("TKOS_WORLD_CONTRACT_VERSION")
    else:
        monkeypatch.setenv("TKOS_WORLD_CONTRACT_VERSION", value)
    assert main(["children", OBJ]) == 0
    assert main(["act", "world_record_event", "--params", json.dumps({"on_behalf_of": {}}), "--reason", "0.1 照旧"]) == 0
    assert [r["body"]["contract_version"] for r in api.requests if r["body"]] == [V01, V01]
    with pytest.raises(SystemExit) as exit_:
        main(["act", "world_start", "--params", "{}", "--reason", "0.1 没有开始"])
    assert exit_.value.code == 2 and choices(capsys.readouterr().err) == [
        "world_record_event", "world_refresh_state", "world_revise_object"]
    with pytest.raises(SystemExit) as exit_:
        main(["list"])
    assert exit_.value.code == 2 and choices(capsys.readouterr().err) == [
        "get", "state", "events", "children", "context", "act"]
