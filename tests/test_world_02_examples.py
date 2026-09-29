"""deploy/world-02/examples.py 的纯逻辑：脱敏（凭证、uuid、显示名、运行标记、游标）、渲染与只认冒烟 scope。
用一小份录好的原始记录（tests/fixtures/world_02_examples/raw.json，id 都是假 uuid），不连库、不起服务。"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "world-02"
spec = importlib.util.spec_from_file_location("world_02_examples", DEPLOY / "examples.py")
examples = importlib.util.module_from_spec(spec)
spec.loader.exec_module(examples)

RAW = json.loads((ROOT / "tests/fixtures/world_02_examples/raw.json").read_text(encoding="utf-8"))
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
IDS = RAW["identities"]
MISSION = RAW["records"][0]["response"]["items"][0]["object_id"]
FAKE_NAME = IDS["principals"]["eo-owner"]["display_name"]


def test_the_sections_follow_the_ticket_order() -> None:
    assert examples.STEP_IDS == ["prep", *map(str, range(1, 11)), "end"]
    text = examples.render(RAW)
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h.split()[1].rstrip(".") for h in headings[1:-1]] == [str(n) for n in range(1, 11)]
    assert headings[0].startswith("## 准备") and headings[-1].startswith("## 收尾")


def test_every_uuid_becomes_a_placeholder_and_the_same_id_the_same_placeholder() -> None:
    names = examples.placeholders(RAW)
    text = examples.render(RAW)
    assert not UUID.search(text)
    assert names[MISSION] == "<mission-1>" and names[IDS["scope_id"]] == "<scope>"
    assert names[IDS["principals"]["tianshu"]["principal_id"]] == "<principal:tianshu>"
    assert names[IDS["domains"]["eo"]] == "<domain:eo>" and names[IDS["skeleton"]["unit-eo"]] == "<unit:eo>"
    # 同一个 Mission 出现在路径、请求体的目标、引用字符串、返回与上下文的 Markdown 里，处处同一个占位
    assert "/v1/world/objects/<mission-1>/events" in text and "/v1/world/objects/<mission-1>/context" in text
    assert '"target": {"object_id": "<mission-1>"' in text and '"ref": "<mission-1>@2"' in text
    assert "`<mission-1>@2`" in text and "?unit_id=<unit:eo>&" in text
    # 事件、修订、回执与类型未知时按键判种类，逐类编号
    assert {names[v] for k, v in RAW["records"][2]["response"]["result"].items() if k in ("event_id", "revision_id")} \
        == {"<event-2>", "<revision-2>"}
    assert names[RAW["records"][2]["response"]["receipt_id"]] == "<receipt-1>"


def test_an_id_nobody_can_name_stops_the_render() -> None:
    raw = deepcopy(RAW)
    raw["records"][0]["response"]["items"][0]["title"] = "见 0b6f0d5e-1c4a-4c55-9d7e-3f2a1b0c9d8e"
    with pytest.raises(ValueError, match="说不出指向什么"):
        examples.render(raw)


def test_display_names_become_role_names_everywhere() -> None:
    text = examples.render(RAW)
    assert FAKE_NAME not in text
    assert '"display_name": "E&O Mission Owner"' in text and "：E&O Mission Owner，" in text


def test_the_run_marker_and_the_cursor_are_replaced() -> None:
    text = examples.render(RAW)
    assert RAW["run"] not in text.split("为准")[1] and "card-submit:<run>-019" in text and "示例 <run> Mission" in text
    assert '"next_cursor": "<cursor>"' in text and "b3BhcXVlLWN1cnNvcg" not in text


def test_credentials_and_the_authorization_header_never_pass() -> None:
    text = examples.render(RAW)
    examples.assert_clean(text, ["secret-token-value"], [FAKE_NAME])
    for bad in ("secret-token-value", "Authorization: x", "Bearer abc"):
        with pytest.raises(ValueError):
            examples.assert_clean(text + bad, ["secret-token-value"])
    with pytest.raises(ValueError):
        examples.assert_clean(text + FAKE_NAME, (), [FAKE_NAME])


def test_the_recorder_keeps_the_key_of_the_identity_and_never_its_credential(tmp_path, monkeypatch) -> None:
    ids = {"scope_id": IDS["scope_id"], "tenant_id": "fixture-smoke", "domains": {k: k for k in examples.smoke.DOMAINS},
           "principals": {k: {"principal_id": k, "display_name": k} for k in examples.smoke.KEYS}}
    (tmp_path / "ids.json").write_text(json.dumps(ids))
    for key in examples.smoke.KEYS:
        (tmp_path / f"{key}.token").write_text(f"credential-of-{key}")
    run = examples.Examples("http://127.0.0.1:1", tmp_path)
    run.load_state()
    monkeypatch.setattr(examples.smoke.Smoke, "call", lambda self, method, path, body=None, who=None: (200, {"ok": 1}))
    run.step, run.caption = "1", "说明"
    run.call("POST", "/v1/actions/prepare", {"action_type": "x"}, "tianshu")
    run.step = None
    run.call("GET", "/v1/health")
    assert run.records == [{"step": "1", "caption": "说明", "method": "POST", "path": "/v1/actions/prepare",
                            "who": "tianshu", "body": {"action_type": "x"}, "status": 200, "response": {"ok": 1}}]
    assert "credential-of" not in json.dumps(run.raw("abc"))


def test_long_returns_are_cut_and_say_so() -> None:
    text = examples.render(RAW)
    assert "……（截去 5 项）" in text  # 八条事件留三条
    assert "……（截去 2 项）" in text  # 取上下文一节更紧：三层留一层
    markdown = RAW["records"][4]["response"]["context_pack"]["markdown"]
    cut = examples.SHORTEN["9"][2]
    assert f"……（截去 {len(examples.sanitize(RAW)['records'][4]['response']['context_pack']['markdown']) - cut} 字）" in text
    assert len(markdown) > cut
    # 请求不截：上下文一节的请求体原样给出
    assert '{"question": "为什么做？"}' in text


def test_prepared_objects_skipped_steps_and_steps_not_run_are_rendered() -> None:
    text = examples.render(RAW)
    assert "- `ceo`（CEO）建 Mission：`<mission-1>`" in text
    assert "**跳过**：委托还没有议题族（等 #71）。" in text and "| 8 | 议题：提出、路由、承接、处置、退回形成 | 跳过 |" in text
    assert "| 10 | 典型错误 | 未跑 |" in text and "本节没有跑到。" in text
    assert text.count("**列 E&O 单元本期的 Mission**") == 1  # 没有说明的请求（翻页）不进文档
    assert "服务提交 `0000000`、本机隔离栈上的运行 `20261012-073000-abcd`" in text


def test_a_record_of_another_format_is_refused() -> None:
    with pytest.raises(ValueError):
        examples.render({**RAW, "format": "something-else"})


def test_pretty_keeps_the_json_and_only_folds_long_lines() -> None:
    value = RAW["records"][2]["response"]
    assert json.loads(examples.pretty(value)) == value
    assert examples.pretty({"a": 1}) == '{"a": 1}'
    assert all(len(line) <= examples.WIDTH + 40 for line in examples.pretty(value).splitlines())


@pytest.mark.parametrize(("tenant", "allowed"), [("tokenking-world-02", False), ("tokenking-world-02-smoke", True)])
def test_run_takes_only_the_smoke_scope_and_refuses_before_any_request(tmp_path, monkeypatch, tenant, allowed) -> None:
    ids = {"scope_id": "s", "tenant_id": tenant, "domains": {k: k for k in examples.smoke.DOMAINS},
           "principals": {k: {"principal_id": k} for k in examples.smoke.KEYS}}
    (tmp_path / "ids.json").write_text(json.dumps(ids))
    for key in examples.smoke.KEYS:
        (tmp_path / f"{key}.token").write_text("not-a-credential")
    asked = []

    def no_network(*args, **kwargs):
        asked.append(args)
        raise AssertionError("no request is expected here")

    monkeypatch.setattr(examples.smoke.urllib.request, "urlopen", no_network)
    if allowed:
        with pytest.raises(AssertionError):  # 过了 scope 的检查，第一个请求才被这里拦下
            examples.main(["run", "http://127.0.0.1:1", str(tmp_path), str(tmp_path / "out")])
        assert len(asked) == 1
    else:
        with pytest.raises(SystemExit):
            examples.main(["run", "http://127.0.0.1:1", str(tmp_path), str(tmp_path / "out")])
        assert asked == []
    assert not (tmp_path / "out").exists()
