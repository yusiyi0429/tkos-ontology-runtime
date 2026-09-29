"""对照实验 B 试用回放的转写（票 #75）的无库测试：用录好的读投影（tests/fixtures/world_v02_transcribe/bundle.json，隔离库上
仿真实 scope、按天枢的写法造的一段试用记录，经 b_transcribe 只读取回）核对读取只有 GET、角色映射、代记还原、快照的真实
时点、问题流转（含代记的承接、退回形成与处置）、显示名拦截，以及转写产物能被 b_seed 与 b_drive 直接使用。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import urllib.parse

import pytest

from experiments.world_v02 import b_drive, b_seed, b_transcribe

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/world_v02_transcribe"
BUNDLE = json.loads((FIXTURE / "bundle.json").read_text(encoding="utf-8"))
TABLE = json.loads((FIXTURE / "principals.json").read_text(encoding="utf-8"))
EO_PLAN = json.loads((ROOT / "deploy/world-02/seed-eo-2026-10.json").read_text(encoding="utf-8"))
SPEC = json.loads(b_seed.SPEC_FILE.read_text(encoding="utf-8"))
NAMES = {item["display_name"]: item["role"] for item in TABLE["principals"].values()}
PID = {key: item["principal_id"] for key, item in TABLE["principals"].items()}


def transcribed(bundle=None, table=None):
    return b_transcribe.transcribe(deepcopy(bundle or BUNDLE), deepcopy(table or TABLE))


RESULT = transcribed()
SCRIPT, LINES = RESULT["script"], RESULT["lines"]


def events(bundle=BUNDLE):
    """真实记录里的全部事件，按记录顺序。"""
    rows = {event["event_id"]: event for items in bundle["events"].values() for event in items}
    return sorted(rows.values(), key=lambda event: (event["recorded_at"], event["event_id"]))


def objects(object_type):
    return [object_id for object_id, item in BUNDLE["objects"].items() if item["type"] == object_type]


def titled(title):
    return next(object_id for object_id, item in BUNDLE["objects"].items()
                if item["latest"]["business"].get("title") == title)


LINK, DATA, PROBE = titled("接入 0.2 的读写链路"), titled("Context 包的真实数据验收"), titled("临时排查：代记失败的请求")
ISSUES = list(dict.fromkeys(event["subject_refs"][0]["component"] for event in events()
                            if event["kind"] == "issue.raised"))


def target(step):
    kind, key = b_drive.target_of(step)
    return kind if kind == "mission" else f"{kind}:{key}"


# ------------------------------------------------------------------ reading: GET only
class FakeService:
    """按录好的读投影回答 GET：列对象（每页两条，另混进一个别的 Mission 下的 Task）、取对象（含 version）、取事件。"""

    def __init__(self):
        self.requests = []
        other = deepcopy(BUNDLE["objects"][PROBE]["latest"])
        other["business"]["relations"] = [{"field": "parent_ref", "relation": "decomposed_into",
                                           "value": {"object_id": "00000000-0000-0000-0000-00000000beef"}}]
        self.other = {"object_id": "00000000-0000-0000-0000-00000000cafe", "view": other}

    def listed(self, object_type):
        ids = [object_id for object_id in BUNDLE["objects"] if BUNDLE["objects"][object_id]["type"] == object_type]
        return ids + ([self.other["object_id"]] if object_type == "Task" else [])

    def __call__(self, request, timeout=None):
        self.requests.append((request.get_method(), request.full_url, request.get_header("Authorization")))
        url = urllib.parse.urlsplit(request.full_url)
        query = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.split("/")
        if url.path == "/v1/world/objects":
            ids = self.listed(query["type"])
            start = int(query.get("cursor", 0))
            body = {"items": [{"object_id": object_id} for object_id in ids[start:start + 2]],
                    "next_cursor": str(start + 2) if start + 2 < len(ids) else None}
        elif parts[-1] == "events":
            body = {"events": BUNDLE["events"][parts[-2]]}
        elif parts[-1] == self.other["object_id"]:
            body = self.other["view"]
        elif parts[-1] in BUNDLE["snapshots"]:
            body = BUNDLE["snapshots"][parts[-1]]
        else:
            item = BUNDLE["objects"][parts[-1]]
            body = item["versions"][query["version"]] if "version" in query else item["latest"]
        return Response(body)


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_reading_is_get_only_and_reads_back_exactly_this_missions_records(monkeypatch, tmp_path):
    service = FakeService()
    monkeypatch.setattr(b_transcribe.urllib.request, "urlopen", service)
    token = tmp_path / "reader.token"
    token.write_text("secret-reader-token\n", encoding="utf-8")
    bundle = b_transcribe.fetch(b_transcribe.Reader("http://world.test/", token), BUNDLE["mission_id"])
    assert {method for method, _, _ in service.requests} == {"GET"}
    assert all(urllib.parse.urlsplit(url).path.startswith("/v1/world/objects") for _, url, _ in service.requests)
    assert {auth for _, _, auth in service.requests} == {"Bearer secret-reader-token"}
    # 别的 Mission 下的 Task 不读进来；读到的与录好的完全相同（读取时刻除外），凭证不进读取原样
    assert {key: value for key, value in bundle.items() if key != "read_at"} == {
        key: value for key, value in BUNDLE.items() if key != "read_at"}
    assert "secret-reader-token" not in json.dumps(bundle)
    assert not hasattr(b_transcribe.Reader, "post")


# ------------------------------------------------------------------ the transcription of the recorded trial
def test_the_script_replays_the_trial_in_record_order_with_role_keys_only():
    k1, k2 = ISSUES
    assert SCRIPT["format"] == b_drive.SCRIPT_FORMAT_02 and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", SCRIPT["id"])
    assert [(step["do"], step["by"], target(step)) for step in SCRIPT["steps"]] == [
        ("start", "exec-agent", "mission"),                 # 天枢作为 Owner 的 Agent 开始 Mission
        ("start", "eo-owner", "task:task_1"), ("start", "eo-dri", "task:task_2"),
        ("plan", "eo-dri", "segment:task_2.s1"),            # Activity 的首次指派即划段
        ("start", "exec-agent", "segment:task_2.s1"), ("progress", "exec-agent", "segment:task_2.s1"),
        ("progress", "exec-agent", "task:task_1"), ("progress", "exec-agent", "task:task_2"),
        ("progress", "exec-agent", "mission"),              # 周快照：两条进展对上 Task，余下的留在 Mission
        ("raise_issue", "eo-coagent", "mission"), ("route_issue", "eo-coagent", f"issue:{k1}"),
        ("own_issue", "eo-owner", f"issue:{k1}"), ("return_issue", "eo-owner", f"issue:{k1}"),
        ("raise_issue", "eo-coagent", "mission"), ("route_issue", "eo-coagent", f"issue:{k1}"),
        ("own_issue", "eo-owner", f"issue:{k1}"), ("dispose_issue", "eo-owner", f"issue:{k1}"),
        ("assign", "eo-owner", "segment:task_1.s1"), ("plan", "eo-owner", "segment:task_1.s3"),
        ("raise_issue", "eo-coagent", "task:task_1"), ("route_issue", "eo-coagent", f"issue:{k2}"),
        ("own_issue", "eo-dri", f"issue:{k2}"), ("dispose_issue", "eo-dri", f"issue:{k2}"),
        ("refresh", "eo-owner", "task:task_1"),
        ("deliver", "exec-agent", "segment:task_2.s1"), ("accept", "eo-dri", "segment:task_2.s1"),
        ("deliver", "eo-owner", "task:task_1"), ("reject", "eo-dri", "task:task_1"),
        ("deliver", "eo-owner", "task:task_1"), ("accept", "eo-dri", "task:task_1"),
        ("reopen", "eo-dri", "task:task_1"), ("deliver", "eo-owner", "task:task_1"),
        ("accept", "eo-dri", "task:task_1"),
        ("deliver", "eo-dri", "task:task_2"), ("accept", "eo-dri", "task:task_2"),
        ("cancel", "eo-dri", "task:task_3"),
        ("deliver", "eo-dri", "mission"), ("accept", "eo-dri", "mission")]
    people = {step[field] for step in SCRIPT["steps"] for field in ("by", "to") if field in step}
    assert people <= set(SPEC["principals"])
    steps = SCRIPT["steps"]
    assert (steps[3]["to"], steps[3]["task"], steps[3]["title"]) == ("exec-agent", "task_2", "整理真实数据清单")
    assert steps[3]["text"] == "列出 8–9 月进入 Context 的数据与来源。"
    assert (steps[17]["to"], steps[18]["to"], steps[18]["title"]) == ("eo-dri", "exec-agent", "补写周快照的来源事件")
    assert steps[27]["text"] == "打回：缺验收材料" and steps[35]["text"] == "并入接入任务"


def test_the_seed_carries_the_gates_and_the_tasks_with_their_first_assignment():
    assert LINES["format"] == b_seed.LINES_FORMAT_02 and LINES["source"] == json.loads(
        b_seed.LINES_FILE.read_text(encoding="utf-8"))["source"]
    gates = [(step["by"], step["action"], step["target"], step.get("outcome")) for step in LINES["steps"]
             if step["do"] == "gate"]
    assert gates == [("eo-dri", "world_commit_period_goal", "october_goal", None),
                     ("ceo", "world_confirm_period_goal", "october_goal", "accepted"),
                     ("eo-dri", "world_commit_mission", "mission_context", None),
                     ("eo-dri", "world_confirm_mission", "mission_context", "returned"),
                     ("eo-dri", "world_commit_mission", "mission_context", None),
                     ("eo-dri", "world_confirm_mission", "mission_context", "accepted")]
    created = {step["key"]: step for step in LINES["steps"] if step["do"] == "create"}
    assert list(created) == ["task_1", "task_2", "task_3"] and {step["by"] for step in created.values()} == {"eo-dri"}
    # 建 Task 的正文：不带计划块与外部引用，指回 Mission 验收条件的引用换成主干的占位
    assert created["task_1"]["payload"] == {
        "title": "接入 0.2 的读写链路", "parent_ref": "@mission_context",
        "blocks": {"definition": {"text": "天枢按接口变化清单接入 0.2，读写两条链路都跑通。"},
                   "acceptance": {"components": [{"id": "t-ac1", "type": "acceptance_criterion",
                                                  "text": "读写链路按接口清单逐项跑通",
                                                  "refs": ["@mission_context#acceptance/ac-3"]}]}}}
    assert [(item["task"], item["responsible"], [(s["key"], s["title"], s["responsible"]) for s in item["segments"]])
            for item in LINES["tasks"]] == [
        ("task_1", "eo-owner", [("task_1.s1", "对齐接口变化清单", "eo-owner"), ("task_1.s2", "写入声明与代记联调", "exec-agent")]),
        ("task_2", "eo-dri", []), ("task_3", "eo-owner", [])]
    assert RESULT["objects"] == {BUNDLE["mission_id"]: "mission", LINK: "task_1", DATA: "task_2", PROBE: "task_3",
                                 objects("Activity")[0]: "segment:task_2.s1"}
    assert RESULT["warnings"] == []


def test_the_transcript_is_seedable_and_drivable_as_it_is():
    derived = b_seed.derive(LINES, EO_PLAN)
    assert derived["mission"] == "mission_context" and derived["owner"] == "eo-dri"
    assert derived["mission_status"] == "established"
    b_drive.check_script(SCRIPT, derived["segments"], derived["tasks"], set(SPEC["principals"]))
    table = b_drive.segment_table(derived["segments"], SCRIPT)
    assert b_drive.segment_counts(table) == {"task_1": 3, "task_2": 1}


def rewrite(bundle, event_id, **changes):
    for rows in bundle["events"].values():
        for event in rows:
            if event["event_id"] == event_id:
                event.update(changes)
    return bundle


def test_delegated_writes_are_transcribed_as_the_person_they_were_recorded_for():
    delegated = [event for event in events() if event["on_behalf_of"]]
    assert delegated and {event["principal"]["principal_id"] for event in delegated} == {PID["tianshu"]}
    # 同一条代记事件，被代记的人换了，转写的行动者跟着换；记录者是谁不影响
    start = next(event for event in delegated if event["kind"] == "start")
    bundle = rewrite(deepcopy(BUNDLE), start["event_id"],
                     on_behalf_of={"principal_id": PID["eo-dri"], "display_name": "E&O DRI"})
    assert transcribed(bundle)["script"]["steps"][1]["by"] == "eo-dri"
    # 天枢以自己身份记的按写入：开始是执行，提出与路由问题是 Co-Agent
    assert SCRIPT["steps"][0]["by"] == "exec-agent" and SCRIPT["steps"][9]["by"] == "eo-coagent"
    # 代记的人只能是人
    bundle = rewrite(deepcopy(BUNDLE), start["event_id"],
                     on_behalf_of={"principal_id": PID["exec-agent"], "display_name": "执行 Agent"})
    with pytest.raises(b_transcribe.TranscribeError, match="代记"):
        transcribed(bundle)


def test_snapshots_keep_their_real_as_of():
    weekly = next(view for view in BUNDLE["snapshots"].values() if view["subject_ref"]["object_id"] == BUNDLE["mission_id"])
    moment = weekly["as_of"].replace("Z", "")
    steps = SCRIPT["steps"]
    # 同一张周快照拆出的三步：时点就是它的 as_of，第 i 步加 i 微秒（同一主体同一时点只能有一条快照）
    assert [steps[n]["as_of"] for n in (6, 7, 8)] == [f"{moment}Z", f"{moment}.000001Z", f"{moment}.000002Z"]
    activity = next(view for view in BUNDLE["snapshots"].values()
                    if view["subject_ref"]["object_id"] == objects("Activity")[0])
    assert steps[5]["as_of"] == activity["as_of"]
    own = next(view for view in BUNDLE["snapshots"].values()
               if view["subject_ref"]["object_id"] == LINK and view["generator"]["principal_id"] == PID["eo-owner"])
    assert steps[23]["as_of"] == own["as_of"] and steps[23]["text"] == "对齐完成，联调中。"
    # 进展条目的文字带上本期条目；写入时的姓名、主体 id 不进脚本
    assert steps[6]["text"].startswith("接入 0.2 的本周进展") and "提交接入改动" in steps[6]["text"]
    assert steps[8]["text"].startswith("本周同步：读写链路在联调") and "周会纪要整理" in steps[8]["text"]
    assert not any(pid in json.dumps(SCRIPT) for pid in PID.values())
    # 提出问题的时点是提出事件的发生时刻
    raised = [event for event in events() if event["kind"] == "issue.raised"]
    assert [steps[n]["as_of"] for n in (9, 13, 19)] == [event["occurred_at"] for event in raised]


def test_the_issue_flow_including_the_delegated_ownership_return_and_disposal():
    k1, k2 = ISSUES
    flow = [(step["do"], step["by"], step.get("to"), step.get("disposition")) for step in SCRIPT["steps"]
            if step.get("issue") == k1]
    assert flow == [("raise_issue", "eo-coagent", None, None), ("route_issue", "eo-coagent", "eo-owner", None),
                    ("own_issue", "eo-owner", None, None), ("return_issue", "eo-owner", None, None),
                    ("raise_issue", "eo-coagent", None, None), ("route_issue", "eo-coagent", "eo-owner", None),
                    ("own_issue", "eo-owner", None, None),
                    ("dispose_issue", "eo-owner", None, "current_layer_action")]
    steps = {step["do"]: step for step in SCRIPT["steps"] if step.get("issue") == k1}
    assert steps["raise_issue"]["question"] == "天枢里的指派要不要同步成本体的 Task 指派？"
    assert steps["return_issue"]["text"] == "核心问题要先补齐再路由"
    assert steps["dispose_issue"]["text"] == "本层处理：执行计划里加一条同步指派的计划条目"
    # Co-Agent 在 Task 上以自己身份提的问题：只带问题的快照并入提出，人本人承接、处置
    second = [(step["do"], step["by"], target(step)) for step in SCRIPT["steps"] if step.get("issue") == k2]
    assert second == [("raise_issue", "eo-coagent", "task:task_1"), ("route_issue", "eo-coagent", f"issue:{k2}"),
                      ("own_issue", "eo-dri", f"issue:{k2}"), ("dispose_issue", "eo-dri", f"issue:{k2}")]
    assert not any(step["do"] in ("progress", "refresh") and step.get("task") == "task_1" and "委托范围" in step["text"]
                   for step in SCRIPT["steps"])


def test_what_is_not_transcribed_is_listed_in_the_review():
    skipped = {item["event_id"]: item for item in RESULT["skipped"]}
    kinds = sorted((item["kind"], item["object"]) for item in skipped.values())
    assert kinds == [("event.recorded", "mission"), ("object.revised", "mission"), ("object.revised", "mission"),
                     ("object.revised", "mission")]
    review = RESULT["review"]
    assert all(f"event:{event_id}" in review for event_id in skipped)
    for heading in ("## 1. 共同播种", "## 2. 执行脚本", "## 3. 未转写", "## 4. 请审的判断"):
        assert heading in review
    assert all(f"| {n} |" in review for n in range(1, len(SCRIPT["steps"]) + 1))


def test_withdrawn_events_are_left_out_with_their_withdrawal():
    bundle = deepcopy(BUNDLE)
    original = next(event for event in events(bundle) if event["kind"] == "start" and event["subject_refs"][0][
        "object_id"] == DATA)
    withdrawal = {**deepcopy(original), "event_id": "00000000-0000-0000-0000-0000000000aa", "outcome": "withdrawn",
                  "supersedes_event_id": original["event_id"]}
    bundle["events"][DATA].append(withdrawal)
    result = transcribed(bundle)
    assert ("start", "eo-dri", "task:task_2") not in [(s["do"], s["by"], target(s)) for s in result["script"]["steps"]]
    assert {original["event_id"], withdrawal["event_id"]} <= {item["event_id"] for item in result["skipped"]}


def test_the_mission_owner_must_be_the_spines():
    bundle = deepcopy(BUNDLE)
    first = next(event for event in events(bundle) if event["kind"] == "assign"
                 and event["subject_refs"][0]["object_id"] == BUNDLE["mission_id"])
    rewrite(bundle, first["event_id"], detail={"principal_id": PID["eo-owner"]})
    with pytest.raises(b_transcribe.TranscribeError, match="Owner"):
        transcribed(bundle)


# ------------------------------------------------------------------ the table and the display-name guard
def test_the_table_must_name_every_actor_with_a_role_that_fits():
    table = deepcopy(TABLE)
    del table["principals"]["eo-owner"]
    with pytest.raises(b_transcribe.TranscribeError, match=PID["eo-owner"]):
        transcribed(table=table)
    for key, role in (("eo-dri", "exec-agent"), ("tianshu", "eo-dri"), ("ceo", "boss")):
        table = deepcopy(TABLE)
        table["principals"][key]["role"] = role
        with pytest.raises(b_transcribe.TranscribeError, match="role"):
            transcribed(table=table)
    with pytest.raises(b_transcribe.TranscribeError, match="format"):
        transcribed(table={**TABLE, "format": "other"})


def test_no_display_name_is_in_the_outputs_of_the_recorded_trial():
    for text in (json.dumps(LINES, ensure_ascii=False), json.dumps(SCRIPT, ensure_ascii=False), RESULT["review"]):
        assert not any(name in text for name in NAMES)


@pytest.mark.parametrize("where", ["dispose", "task_title"])
def test_a_display_name_in_any_output_stops_the_transcription(where, tmp_path):
    bundle = deepcopy(BUNDLE)
    if where == "dispose":
        disposed = next(event for event in events(bundle) if event["kind"] == "issue.disposed")
        rewrite(bundle, disposed["event_id"], content={**disposed["content"], "text": "与 E&O DRI 商量后本层处理"})
        expected = r"b-script\.json 第 \d+ 步的 text"
    else:
        for view in bundle["objects"][LINK]["versions"].values():
            view["business"]["title"] = "E&O Mission Owner 负责的接入"
        expected = r"b-lines\.json"
    with pytest.raises(b_transcribe.TranscribeError, match=expected) as caught:
        b_transcribe.write(bundle, deepcopy(TABLE), tmp_path / "out")
    message = str(caught.value)
    assert ("eo-dri" if where == "dispose" else "eo-owner") in message and not any(name in message for name in NAMES)
    assert not (tmp_path / "out").exists()


def test_a_name_the_real_scope_shows_is_caught_even_if_the_table_spells_it_differently():
    table = deepcopy(TABLE)
    table["principals"]["eo-dri"]["display_name"] = "张某"
    bundle = deepcopy(BUNDLE)
    disposed = next(event for event in events(bundle) if event["kind"] == "issue.disposed")
    rewrite(bundle, disposed["event_id"], content={**disposed["content"], "text": "与 E&O DRI 商量后本层处理"})
    with pytest.raises(b_transcribe.TranscribeError, match="eo-dri"):
        transcribed(bundle, table)


# ------------------------------------------------------------------ files and command line
def test_write_puts_the_three_outputs_and_the_command_line_rewrites_from_a_saved_bundle(tmp_path):
    first = b_transcribe.write(deepcopy(BUNDLE), deepcopy(TABLE), tmp_path / "a")
    names = sorted(path.name for path in (tmp_path / "a").iterdir())
    assert names == ["b-lines.json", "b-review.md", "b-script.json"]
    assert json.loads((tmp_path / "a" / "b-script.json").read_text(encoding="utf-8")) == first["script"]
    saved, table = tmp_path / "bundle.json", tmp_path / "principals.json"
    saved.write_text(json.dumps(BUNDLE, ensure_ascii=False), encoding="utf-8")
    table.write_text(json.dumps(TABLE, ensure_ascii=False), encoding="utf-8")
    b_transcribe.main(["write", str(saved), "--principals", str(table), "--output", str(tmp_path / "b")])
    for name in names:
        assert (tmp_path / "a" / name).read_text(encoding="utf-8") == (tmp_path / "b" / name).read_text(encoding="utf-8")


def test_the_committed_files_carry_no_person_numbered_tianshu_id():
    """仓库里只有不带人名的外部编号：实测示例的 mission:demo-、todo:<uuid>、issue:demo-，以及能力域 capability:05。"""
    for path in (FIXTURE / "bundle.json", FIXTURE / "principals.json", b_seed.LINES_FILE,
                 ROOT / "experiments/world_v02/b_transcribe.py", ROOT / "experiments/world_v02/b_rehearse.py"):
        text = path.read_text(encoding="utf-8")
        found = set(re.findall(r"\b(?:mission|capability):[A-Za-z0-9][\w.-]*", text))
        assert {item for item in found if not item.startswith("mission:demo-")} <= {"capability:05"}, (path.name, found)
