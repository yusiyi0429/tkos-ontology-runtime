"""对照实验 B（票 #66）的无库测试：两条线的共同播种取自 E&O 十月起点的 mission_trial、名单只有角色名、执行脚本的格式、
驱动器按线的写法与三种表达结果（假 HTTP），以及五项观测的统计——用录好的一次冒烟运行日志（tests/fixtures/
world_v02_experiment_b/，隔离库上经真 API 跑出）核对每项观测的发生与未发生、三种表达结果与结论规则。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.world_v02 import b_drive, b_observe, b_seed

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/world_v02_experiment_b"
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
SOURCE = json.loads((ROOT / "deploy/world-02/seed-eo-2026-10.json").read_text(encoding="utf-8"))
SPEC = json.loads(b_seed.SPEC_FILE.read_text(encoding="utf-8"))
SMOKE = json.loads(b_seed.SMOKE_FILE.read_text(encoding="utf-8"))
DERIVED, _ = b_seed.load()


def recorded():
    return (json.loads((FIXTURES / "task_only.json").read_text(encoding="utf-8")),
            json.loads((FIXTURES / "task_activity.json").read_text(encoding="utf-8")))


# ------------------------------------------------------------------ lines, spec and script
def test_the_shared_seed_is_the_trial_mission_chain_of_the_eo_october_plan():
    source = {step["key"]: step for step in SOURCE["steps"]}
    shared = {step["key"]: step for step in DERIVED["shared"]}
    kept = [key for key in source if key in shared]
    # 原计划的正文与顺序原样照搬，只留 mission_trial 到 Company 的主干
    assert all(shared[key] == source[key] for key in kept)
    assert kept == [key for key in source if key in json.loads(b_seed.LINES_FILE.read_text())["source"]["steps"]]
    assert DERIVED["mission"] == "mission_trial" and source["mission_trial"]["payload"]["title"] == "天枢 × 本体 0.2 试用"
    assert DERIVED["tasks"] == ["task_trial_integration", "task_trial_week", "task_trial_acceptance"]
    assert not {"mission_experiments", "mission_lock", "task_experiment_b", "delegate_ceo", "delegate_dri",
                "delegate_owner"} & set(shared)
    # 之后两条线同样：Owner 指派三个 Task、把执行计划写成每个 Task 一条带责任人的计划条目
    assert [shared[f"assign_{task}"]["by"] for task in DERIVED["tasks"]] == ["eo-owner"] * 3
    plan = shared["execution_plan"]["payload"]["blocks"]["execution_plan"]["components"]
    assert [item["id"] for item in plan] == DERIVED["tasks"]
    assert [item["attributes"]["responsible"] for item in plan] == [shared[f"assign_{task}"]["to"]
                                                                   for task in DERIVED["tasks"]]


def test_the_lines_do_not_derive_from_a_broken_plan():
    lines = json.loads(b_seed.LINES_FILE.read_text(encoding="utf-8"))
    broken = deepcopy(lines)
    broken["source"]["steps"].remove("confirm_mission_trial")
    with pytest.raises(ValueError, match="前置"):
        b_seed.derive(broken, SOURCE)
    broken = deepcopy(lines)
    broken["tasks"][1]["segments"][0]["key"] = "integration"
    with pytest.raises(ValueError, match="段键"):
        b_seed.derive(broken, SOURCE)
    broken = deepcopy(lines)
    broken["tasks"][0]["task"] = "task_experiment_b"
    with pytest.raises(ValueError, match="task_experiment_b"):
        b_seed.derive(broken, SOURCE)


def test_the_spec_has_only_role_names_and_passes_provision():
    b_seed._deploy_module("provision").check_spec(SPEC)
    assert set(SPEC["principals"]) == {"ceo", "eo-dri", "eo-owner", "eo-ic", "eo-coagent", "exec-agent"}
    assert {item["display_name"] for item in SPEC["principals"].values()} == {
        "CEO", "E&O DRI", "E&O Mission Owner", "E&O 执行者", "E&O Co-Agent", "执行 Agent"}
    used = {step["by"] for step in DERIVED["shared"]} | {step.get("to") for step in DERIVED["shared"]} - {None}
    used |= {item["responsible"] for item in DERIVED["segments"].values()}
    used |= {step["by"] for step in SMOKE["steps"]} | {step["to"] for step in SMOKE["steps"] if "to" in step}
    assert used <= set(SPEC["principals"])


def test_the_segments_fit_the_registry_and_the_roles():
    """段是 Task 计划块里的计划条目（Task-only）或 Task 下的 Activity（Task+Activity）：计划块允许计划条目，段的责任人在
    E&O 域持 Activity 要的角色（人 IC、Agent AGENT），Task 的责任人持 IC。"""
    task = next(item for item in REGISTRY["objects"] if item["type"] == "Task")
    activity = next(item for item in REGISTRY["objects"] if item["type"] == "Activity")
    assert "plan_item" in next(block for block in task["blocks"] if block["id"] == "plan")["components"]
    roles = {key: set(item["roles"].get("eo", [])) for key, item in SPEC["principals"].items()}
    for segment in DERIVED["segments"].values():
        kind = SPEC["principals"][segment["responsible"]]["type"]
        assert activity["responsible"]["roles"][kind] in roles[segment["responsible"]]
    for step in DERIVED["shared"]:
        if step["do"] == "assign" and step["target"] in DERIVED["tasks"]:
            assert "IC" in roles[step["to"]]


def test_the_smoke_script_is_valid_and_splits_the_tasks_as_planned():
    b_drive.check_script(SMOKE, DERIVED["segments"], DERIVED["tasks"], set(SPEC["principals"]))
    table = b_drive.segment_table(DERIVED["segments"], SMOKE)
    assert b_drive.segment_counts(table) == {"task_trial_integration": 2, "task_trial_week": 2,
                                             "task_trial_acceptance": 1}
    dos = {step["do"] for step in SMOKE["steps"]}
    assert dos == set(b_drive.TARGETS) - {"plan"}
    assert SMOKE["steps"][-1] == {"do": "accept", "by": "eo-dri", "mission": True}
    assert any(step["by"] == "exec-agent" and step["do"] == "progress" for step in SMOKE["steps"])


@pytest.mark.parametrize("step,message", [
    ({"do": "finish", "by": "eo-ic", "segment": "integration"}, "do 是"),
    ({"do": "start", "by": "eo-ic", "segment": "integration", "task": "task_trial_week"}, "只写一个目标"),
    ({"do": "start", "by": "eo-ic", "segment": "nowhere"}, "还没划出"),
    ({"do": "reject", "by": "eo-owner", "segment": "integration"}, "必带"),
    ({"do": "route_issue", "by": "eo-coagent", "issue": "never_raised", "to": "eo-owner"}, "还没提出"),
    ({"do": "start", "by": "eo-ic", "segment": "integration", "as_of": "2026-10-12T09:00:00Z"}, "带 as_of"),
    ({"do": "start", "by": "someone", "segment": "integration"}, "ids.json 里没有主体"),
    ({"do": "assign", "by": "eo-owner", "mission": True, "to": "eo-ic"}, "assign 的目标是"),
    ({"do": "plan", "by": "eo-owner", "segment": "integration", "task": "task_trial_integration",
      "title": "重复", "text": "重复", "to": "eo-ic"}, "还没划过"),
    ({"do": "dispose_issue", "by": "eo-owner", "issue": "x", "disposition": "ignore", "text": "t"}, "disposition"),
])
def test_a_broken_script_step_is_named(step, message):
    script = {**SMOKE, "steps": [step]}
    with pytest.raises(ValueError, match=message):
        b_drive.check_script(script, DERIVED["segments"], DERIVED["tasks"], set(SPEC["principals"]))


# ------------------------------------------------------------------ driver: the writing on each line (fake HTTP)
class FakeLine:
    scope_id = "scope"

    def principal(self, key):
        return f"p-{key}"

    def principal_type(self, key):
        return "agent" if key.endswith("agent") else "human"

    def domain(self, key):
        return f"d-{key}"

    def ref(self, object_id, who):
        return f"{object_id}@1"

    def target(self, object_id, who):
        return {"object_id": object_id, "revision_id": "r", "expected_version": 1}


@pytest.fixture
def fake(monkeypatch):
    """假提交：refuse(body, who) 给出错误码就拒绝，否则提交；记下每个请求体。"""
    sent, rules = [], {"refuse": lambda body, who: None}
    counter = iter(range(10_000))

    def submit(line, pending, save, key, who, build):
        body = build()
        sent.append((who, body))
        code = rules["refuse"](body, who)
        if code:
            return {"committed": False, "stage": "prepare", "status": 403, "error_code": code}
        n = next(counter)
        return {"committed": True, "receipt": {"receipt_id": f"rc{n}", "recorded_at": f"2026-10-12T09:00:{n % 60:02d}Z",
                                               "result": {"event_id": f"ev{n}", "object_id": f"ob{n}",
                                                          "ref": f"ob{n}@1"}}}
    monkeypatch.setattr(b_drive, "submit", submit)
    return sent, rules


def driver(line_name, steps=()):
    log = {"line": line_name, "mission": "mission_trial", "objects": {
        "mission_trial": {"object_id": "mission", "type": "Mission", "domain": "eo"},
        **{task: {"object_id": task, "type": "Task", "domain": "eo"} for task in DERIVED["tasks"]},
        **({f"segment:{key}": {"object_id": f"act-{key}", "type": "Activity", "domain": "eo", "task": value["task"]}
            for key, value in DERIVED["segments"].items()} if line_name == "task_activity" else {})},
        "seed": {"x": {"recorded_at": "2026-10-12T08:00:00Z"}}, "steps": list(steps), "pending": {}}
    return b_drive.Driver(FakeLine(), log, lambda: None, b_drive.segment_table(DERIVED["segments"], None)), log


def run(drv, log, step, key):
    record = drv.run_step({**step, "n": int(key) if key.isdigit() else 0}, key, "script")
    log["steps"].append(record)
    return record


def actions(record):
    return [(attempt["role"], attempt["action"], attempt["object_id"], attempt["committed"]) for attempt in record["attempts"]]


def test_task_activity_writes_every_segment_step_on_the_activity(fake):
    drv, log = driver("task_activity")
    record = run(drv, log, {"do": "start", "by": "exec-agent", "segment": "weekly_snapshot"}, "1")
    assert actions(record) == [("primary", "world_start", "act-weekly_snapshot", True)]
    assert record["expression"] == "native" and record["writing"] == "activity"
    who, body = fake[0][-1]
    assert who == "exec-agent" and body["params"]["declaration"]["scene"] == "act-weekly_snapshot@1"
    record = run(drv, log, {"do": "assign", "by": "eo-ic", "segment": "weekly_snapshot", "to": "exec-agent"}, "2")
    assert actions(record) == [("primary", "world_assign", "act-weekly_snapshot", True)]
    assert fake[0][-1][1]["params"] == {"principal_id": "p-exec-agent"}


def test_task_only_writes_a_segment_of_a_split_task_as_its_plan_item_and_keeps_the_actor(fake):
    drv, log = driver("task_only")
    record = run(drv, log, {"do": "deliver", "by": "eo-ic", "segment": "integration", "text": "跑通"}, "1")
    assert actions(record) == [("primary", "world_revise_object", "task_trial_integration", True)]
    assert record["expression"] == "coarse" and record["writing"] == "plan_item"
    who, body = fake[0][-1]
    component = body["params"]["payload"]["blocks"]["plan"]["components"][0]
    assert who == "eo-ic" and component["id"] == "integration" and "交付" in component["text"]
    assert component["attributes"] == {"responsible": "p-eo-ic"}
    # 行动者不在这个 Task 的主干上：被拒，记下错误码，不换人
    fake[1]["refuse"] = lambda body, who: "FORBIDDEN" if who == "eo-ic" else None
    record = run(drv, log, {"do": "deliver", "by": "eo-ic", "segment": "integration"}, "2")
    assert record["expression"] == "rejected" and record["error_codes"] == ["FORBIDDEN"]
    assert {who for who, _ in fake[0]} == {"eo-ic"}


def test_task_only_writes_a_single_segment_on_the_task_and_pairs_it_with_the_task_step(fake):
    drv, log = driver("task_only")
    first = run(drv, log, {"do": "start", "by": "eo-owner", "task": "task_trial_acceptance"}, "1")
    merged = run(drv, log, {"do": "start", "by": "eo-owner", "segment": "acceptance_run"}, "2")
    assert actions(first) == [("primary", "world_start", "task_trial_acceptance", True)]
    assert merged["attempts"] == [] and merged["merged_into"] == "1" and merged["expression"] == "native"
    delivered = run(drv, log, {"do": "deliver", "by": "eo-owner", "segment": "acceptance_run"}, "3")
    assert actions(delivered) == [("primary", "world_deliver", "task_trial_acceptance", True)]
    assert delivered["writing"] == "task_level" and delivered["expression"] == "native"
    later = run(drv, log, {"do": "deliver", "by": "eo-owner", "task": "task_trial_acceptance"}, "4")
    assert later["merged_into"] == "3" and later["attempts"] == []
    # Task 一级被拒（验收由 Mission Owner 记）：同一人改计划条目的状态，粗粒度；之后 Task 一级的验收照常写
    fake[1]["refuse"] = lambda body, who: "FORBIDDEN" if body["action_type"] == "world_accept" and who == "eo-ic" else None
    fallback = run(drv, log, {"do": "accept", "by": "eo-ic", "segment": "acceptance_run"}, "5")
    assert actions(fallback) == [("primary", "world_accept", "task_trial_acceptance", False),
                                 ("fallback", "world_revise_object", "task_trial_acceptance", True)]
    assert fallback["expression"] == "coarse" and fallback["error_codes"] == ["FORBIDDEN"]
    task = run(drv, log, {"do": "accept", "by": "eo-owner", "task": "task_trial_acceptance"}, "6")
    assert actions(task) == [("primary", "world_accept", "task_trial_acceptance", True)] and task["merged_into"] is None


def test_progress_records_an_external_event_then_a_snapshot_on_the_lines_subject(fake):
    drv, log = driver("task_only")
    record = run(drv, log, {"do": "progress", "by": "exec-agent", "segment": "weekly_snapshot", "text": "周快照"}, "1")
    assert actions(record) == [("support", "world_record_event", "task_trial_week", True),
                               ("primary", "world_refresh_state", "task_trial_week", True)]
    assert record["expression"] == "coarse" and record["writing"] == "task_subject"
    (_, event), (_, snapshot) = fake[0][-2:]
    assert event["params"]["subject_refs"] == ["task_trial_week@1#plan/weekly_snapshot"]
    assert event["params"]["occurred_at"] == "2026-10-12T08:00:00Z"
    payload = snapshot["params"]["payload"]
    assert payload["subject_ref"] == "task_trial_week@1" and payload["source_event_refs"] == [f"event:{record['attempts'][0]['event_id']}"]
    assert payload["as_of"] == record["attempts"][0]["recorded_at"]
    item = payload["blocks"]["progress"]["components"][0]
    assert item["refs"] == ["task_trial_week@1#plan/weekly_snapshot"] and item["attributes"]["principal_id"] == "p-exec-agent"
    # 配套的外部事件被拒，这一步就是被拒
    fake[1]["refuse"] = lambda body, who: "INVALID_REQUEST" if body["action_type"] == "world_record_event" else None
    record = run(drv, log, {"do": "refresh", "by": "eo-owner", "segment": "acceptance_run", "text": "验收"}, "2")
    assert record["expression"] == "rejected" and len(record["attempts"]) == 1


def test_an_issue_is_raised_on_the_lines_subject_and_routed_by_its_reference(fake):
    for line_name, subject in (("task_activity", "act-issue_flow"), ("task_only", "task_trial_week")):
        drv, log = driver(line_name)
        raised = run(drv, log, {"do": "raise_issue", "by": "eo-coagent", "segment": "issue_flow", "issue": "scope",
                                "question": "要不要覆盖公司域？"}, "1")
        assert [item[:2] for item in actions(raised)] == [("support", "world_record_event"),
                                                          ("support", "world_refresh_state"),
                                                          ("primary", "world_raise_issue")]
        issue_ref = raised["issue_ref"]
        assert issue_ref.endswith("#issues/scope") and raised["issue_primary_id"] == subject
        routed = run(drv, log, {"do": "route_issue", "by": "eo-coagent", "issue": "scope", "to": "eo-owner"}, "2")
        who, body = fake[0][-1]
        assert body["params"]["issue_ref"] == issue_ref and body["params"]["to_principal_id"] == "p-eo-owner"
        assert body["params"]["declaration"]["scene"] == f"{subject}@1" and routed["expression"] == "native"
        owned = run(drv, log, {"do": "own_issue", "by": "eo-owner", "issue": "scope"}, "3")
        assert "declaration" not in fake[0][-1][1]["params"] and owned["expression"] == "native"
        fake[0].clear()


def test_a_step_on_an_issue_that_was_not_raised_on_this_line_is_rejected_without_a_request(fake):
    drv, log = driver("task_only")
    record = run(drv, log, {"do": "own_issue", "by": "eo-owner", "issue": "scope"}, "1")
    assert record["expression"] == "rejected" and record["error_codes"] == ["ISSUE_NOT_RAISED"] and fake[0] == []


@pytest.mark.parametrize("attempts,merged,grain,expected", [
    ([], "3", "native", "native"),
    ([("primary", True)], None, "native", "native"),
    ([("primary", True)], None, "coarse", "coarse"),
    ([("primary", False)], None, "native", "rejected"),
    ([("primary", False), ("fallback", True)], None, "native", "coarse"),
    ([("primary", False), ("fallback", False)], None, "native", "rejected"),
    ([("support", True), ("primary", True)], None, "coarse", "coarse"),
    ([("support", False)], None, "native", "rejected"),
    ([("support", True), ("support", True), ("primary", False)], None, "native", "rejected"),
])
def test_the_three_expression_results_of_a_step(attempts, merged, grain, expected):
    record = {"attempts": [{"role": role, "committed": ok, "error_code": None if ok else "FORBIDDEN"}
                           for role, ok in attempts], "merged_into": merged, "grain": grain}
    assert b_drive.record_expression(record)[0] == expected


# ------------------------------------------------------------------ observations on the recorded smoke
def test_the_fixture_holds_no_credentials_or_names():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf-8").lower()
        assert not any(word in text for word in ("token", "bearer", "password", "postgres", "display_name"))
        log = json.loads(text)
        assert log["pending"] == {} and log["seed_done"] and log["script_done"]


def test_the_recorded_smoke_gives_the_documented_observations():
    task_only, task_activity = recorded()
    result = b_observe.observe(task_only, task_activity)
    assert result["expressions"] == {"task_only": {"native": 20, "coarse": 21, "rejected": 8},
                                     "task_activity": {"native": 49, "coarse": 0, "rejected": 0}}
    verdicts = {name: (item["occurred"], item["task_only"]) for name, item in result["observations"].items()}
    assert verdicts == {"assign": (True, "coarse"), "execute": (True, "inexpressible"), "retry": (True, "coarse"),
                        "accept": (True, "coarse"), "manage": (True, "inexpressible")}
    assert result["conclusion"]["activity"] == "object" and result["conclusion"]["because"] == ["execute", "manage"]
    assert result["agent_subject"]["needs"] == "Activity" and result["rejected_in_task_activity"] == []
    steps = {name: item["evidence"]["steps"] for name, item in result["observations"].items()}
    assert steps == {"assign": ["plan:integration", "15"], "execute": ["16", "17", "18", "20", "29", "30"],
                     "retry": ["7", "19", "28"], "accept": ["9", "21", "31"], "manage": ["5", "17", "23", "29"]}
    execute = result["observations"]["execute"]
    assert execute["task_only_counts"] == {"native": 0, "coarse": 2, "inexpressible": 4}
    assert [item["step"] for item in execute["coarse"]] == ["17", "29"]
    events = {event["event_id"] for event in task_activity["evidence"]["events"]}
    assert all(event_id in events for item in result["observations"].values()
               for event_id in item["evidence"]["task_activity_event_ids"])
    assert "结论 Activity 留作对象（执行、管理）" in b_observe.render(result)


def test_what_does_not_count_as_independent_on_the_recorded_smoke():
    result = b_observe.observe(*recorded())
    independent = {name: {(item["step"], item["kind"]): item["independent"] for item in value["occurrences"]}
                   for name, value in result["observations"].items()}
    # 指派给当时这个 Task 的责任人不算
    assert independent["assign"][("plan:delegation_setup", "assign")] is False
    assert independent["assign"][("plan:integration", "assign")] is True
    # 自己验自己不算；Task 只有这一段时与 Task 的验收配对，不算
    assert independent["accept"][("11", "accept")] is False and independent["accept"][("32", "accept")] is False
    assert independent["accept"][("39", "accept")] is False and independent["accept"][("9", "accept")] is True
    # Task 只有这一段时，以它为主体的快照与以 Task 为主体同一粒度
    assert independent["manage"][("37", "state.refreshed")] is False
    # 发问题的 Co-Agent 不是这一段的责任人：它的写入算管理，不算执行
    assert ("23", "state.refreshed") in independent["manage"] and not any(
        step == "23" for step, _ in independent["execute"])


def drop(logs, keys):
    """假设这些步骤没有发生：从两条线的运行日志里拿掉它们。"""
    for log in logs:
        log["steps"] = [record for record in log["steps"] if record["key"] not in keys]
    return logs


@pytest.mark.parametrize("name,keys", [
    ("assign", {"plan:integration", "15"}),
    ("execute", {"16", "17", "18", "20", "29", "30"}),
    ("retry", {"7", "19", "28"}),
    ("accept", {"9", "21", "31"}),
    ("manage", {"5", "17", "23", "29"}),
])
def test_each_observation_can_be_absent(name, keys):
    result = b_observe.observe(*drop(recorded(), keys))
    item = result["observations"][name]
    assert item["occurred"] is False and item["task_only"] is None and item["keeps_activity"] is False
    assert item["evidence"] == {"task_activity_event_ids": [], "steps": []}


def set_expression(log, keys, expression):
    for record in log["steps"]:
        if record["key"] in keys:
            record["expression"] = expression
    return log


def test_the_task_only_side_of_an_observation_is_native_coarse_or_inexpressible():
    task_only, task_activity = recorded()
    assert b_observe.observe(task_only, task_activity)["observations"]["retry"]["task_only"] == "coarse"
    native = b_observe.observe(set_expression(deepcopy(task_only), {"7", "19", "28"}, "native"), task_activity)
    assert native["observations"]["retry"]["task_only"] == "native"
    assert native["observations"]["retry"]["task_only_counts"] == {"native": 3, "coarse": 0, "inexpressible": 0}
    worst = b_observe.observe(set_expression(deepcopy(task_only), {"19"}, "rejected"), task_activity)
    assert worst["observations"]["retry"]["task_only"] == "inexpressible"
    assert worst["observations"]["retry"]["task_only_counts"] == {"native": 0, "coarse": 2, "inexpressible": 1}
    assert worst["conclusion"]["because"] == ["execute", "retry", "manage"]


def test_the_conclusion_rule():
    task_only, task_activity = recorded()
    rejected = {record["key"] for record in task_only["steps"] if record["expression"] == "rejected"}
    # 粗粒度算能表达：Task-only 线没有被拒的，Activity 降为组件
    coarse = b_observe.observe(set_expression(deepcopy(task_only), rejected, "coarse"), task_activity)
    assert coarse["conclusion"] == {"activity": "component", "because": [], "rule": b_observe.RULE}
    assert all(item["occurred"] for item in coarse["observations"].values())
    # 只有执行表达不了
    only = b_observe.observe(set_expression(deepcopy(task_only), rejected - {"16"}, "coarse"), task_activity)
    assert only["conclusion"]["activity"] == "object" and only["conclusion"]["because"] == ["execute"]
    # 不是独立发生的，Task-only 线表达不了也不留对象（39 是只有一段的 Task 与 Task 验收配对的那一步）
    paired = b_observe.observe(set_expression(set_expression(deepcopy(task_only), rejected, "coarse"), {"39", "37"},
                                              "rejected"), task_activity)
    assert paired["conclusion"]["activity"] == "component"
    # 五项都没有独立发生
    quiet = drop(recorded(), {"plan:integration", "15", "16", "17", "18", "20", "29", "30", "7", "19", "28", "9",
                              "21", "31", "5", "23"})
    result = b_observe.observe(*quiet)
    assert not any(item["occurred"] for item in result["observations"].values())
    assert result["conclusion"]["activity"] == "component"


def test_the_agent_subject():
    task_only, task_activity = recorded()
    assert b_observe.observe(task_only, task_activity)["agent_subject"]["needs"] == "Activity"
    agent_steps = {record["key"] for record in task_only["steps"] if record["by"] in ("exec-agent", "eo-coagent")}
    task = b_observe.observe(set_expression(deepcopy(task_only), agent_steps, "coarse"), task_activity)
    assert task["agent_subject"]["needs"] == "Task"
    none = b_observe.observe(*drop(recorded(), agent_steps))
    assert none["agent_subject"] == {"needs": None, "steps": []}


def test_the_two_logs_must_be_one_comparison():
    task_only, task_activity = recorded()
    with pytest.raises(ValueError, match="格式或线"):
        b_observe.observe(task_activity, task_only)
    with pytest.raises(ValueError, match="脚本不同"):
        b_observe.observe({**task_only, "script": {"id": "other", "sha256": "0"}}, task_activity)
    with pytest.raises(ValueError, match="取证"):
        b_observe.observe({**task_only, "evidence": None}, task_activity)
    with pytest.raises(ValueError, match="对不上"):
        b_observe.observe({**task_only, "steps": task_only["steps"][1:]}, task_activity)
