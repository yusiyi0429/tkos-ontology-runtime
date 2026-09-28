"""deploy/world-02 的「E&O 十月起点」播种：计划按 0.2 的模型与判权成立、按人分段的顺序、按人筛选、状态视图、代录说明，
以及脚本的提交与重发（假服务）。不连库、不起服务。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import uuid

import pytest

from memory_service_runtime.governed import world_v02_models as models
from memory_service_runtime.governed import world_v02_registry as registry

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "world-02"
spec = importlib.util.spec_from_file_location("world_02_seed_eo", DEPLOY / "seed_eo.py")
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)

PLAN = json.loads((DEPLOY / "seed-eo-2026-10.json").read_text(encoding="utf-8"))
EO_SPEC = json.loads((DEPLOY / "spec.eo.example.json").read_text(encoding="utf-8"))
REPLAY = json.loads((ROOT / "experiments/world_v01/seed.json").read_text(encoding="utf-8"))
STEPS = {step["key"]: step for step in PLAN["steps"]}
SEGMENTS = ["ceo", "eo-dri", "ceo", "eo-dri", "ceo", "eo-dri", "eo-owner", "eo-dri", "eo-owner"]
FAKE = {key: str(uuid.uuid5(uuid.NAMESPACE_URL, key)) for key in [*STEPS, *EO_SPEC["principals"], *EO_SPEC["domains"]]}


def roles(principal: str, domain: str) -> list[str]:
    return EO_SPEC["principals"][principal]["roles"].get(domain, [])


def fake_ref(text):
    """把占位换成形状对的业务引用（每步一个固定的假 id，版本 1）。"""
    match = seed.PLACEHOLDER.match(text) if isinstance(text, str) else None
    if match is None:
        return text
    key, block, component = match.groups()
    return f"{FAKE[key]}@1" + (f"#{block}" if block else "") + (f"/{component}" if component else "")


def resolved(value):
    if isinstance(value, dict):
        return {key: resolved(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolved(item) for item in value]
    return fake_ref(value)


def ancestors(key: str) -> set[str]:
    found, todo = set(), list(STEPS[key]["after"])
    while todo:
        dep = todo.pop()
        if dep not in found:
            found.add(dep)
            todo.extend(STEPS[dep]["after"])
    return found


# ------------------------------------------------------------------ the plan under the 0.2 models and rules
def test_every_step_of_the_plan_validates_under_the_0_2_models() -> None:
    seed.check_plan(PLAN)
    for step in PLAN["steps"]:
        if step["do"] == "create":
            models.validate_input(step["type"], resolved(step["payload"]))
            for relation in registry.object_spec(step["type"])["relation_fields"]:
                for text in models.listed(step["payload"].get(relation["field"])):
                    target, block, component = seed.PLACEHOLDER.match(text).groups()
                    assert STEPS[target]["type"] in relation["targets"], step["key"]
                    assert block == relation["target_block"], step["key"]
                    if relation["target_component"]:
                        found = [c for c in STEPS[target]["payload"]["blocks"][block]["components"] if c["id"] == component]
                        assert [c["type"] for c in found] == [relation["target_component"]], step["key"]
            assert "external_refs" not in step["payload"], "天枢的战场、任务卡 id 联调时才给"
        elif step["do"] == "gate":
            for text in (PLAN["event_text"], PLAN["event_text"] + "。由E&O DRI代CEO录入，待本人复核"):
                params = {"content": {"text": text}, **({"outcome": step["outcome"]} if "outcome" in step else {})}
                models.ACTION_PARAMS[step["action"]].model_validate(params)
            assert STEPS[step["target"]]["type"] in models.ACTION_TARGETS[step["action"]]
        elif step["do"] == "assign":
            models.WorldV02AssignParams.model_validate({"principal_id": FAKE[step["to"]]})
            assert STEPS[step["target"]]["type"] in models.ACTION_TARGETS["world_assign"]
        else:
            models.WorldV02GrantDelegationParams.model_validate({
                "delegate_principal_id": FAKE[step["to"]], "families": step["families"],
                "domain_ids": [FAKE[domain] for domain in step["domains"]], "valid_until": step["valid_until"]})


def test_objects_sit_where_0_2_places_them() -> None:
    """同 check_placement：责任单元在自己的域、Strategy 在公司域；其余与主干上一级同域；公司级长期目标挂 Company，
    单元级挂单元并分解公司级；周期目标推进单元级长期目标。"""
    creates = {key: step for key, step in STEPS.items() if step["do"] == "create"}
    for key, step in creates.items():
        field = registry.object_spec(step["type"])["spine_parent_field"]
        if field is None:
            continue
        parent = creates[seed.PLACEHOLDER.match(step["payload"][field]).group(1)]
        assert (parent["domain"] == step["domain"]) == (step["type"] != "ResponsibilityUnit"), key
    assert creates["company_goal"]["payload"]["scope"] == "company" and creates["eo_goal"]["payload"]["scope"] == "unit"
    assert creates["eo_goal"]["payload"]["parent_ref"] == "@unit_eo" and creates["eo_goal"]["payload"]["goal_ref"] == "@company_goal"
    assert creates["october_goal"]["payload"]["goal_ref"] == "@eo_goal"


def test_each_step_is_recorded_by_the_person_0_2_lets_record_it() -> None:
    """判权按 0.2：建对象的人在对象的域持角色（激活策略对建对象开全部角色），并是主干上一级的责任人；门按登记的门角色，
    Mission 的承诺是被指派的 Owner 本人；指派由周期目标的 DRI 记、被指派者持 OWNER；委托由人本人记。"""
    owner_of = {step["target"]: step["to"] for step in PLAN["steps"] if step["do"] == "assign"}
    for step in PLAN["steps"]:
        who, key = step["by"], step["key"]
        assert EO_SPEC["principals"][who]["type"] == "human", key
        if step["do"] == "create":
            assert roles(who, step["domain"]), key
            parent = seed.PLACEHOLDER.match(step["payload"].get("parent_ref") or step["payload"].get("goal_ref")
                                            or step["payload"].get("architecture_ref") or "@company").group(1)
            if step["type"] in ("Company", "Strategy", "ResponsibilityUnit") or step["payload"].get("scope") == "company":
                assert "CEO" in roles(who, "company"), key
            elif step["type"] == "Task":
                assert owner_of[parent] == who and "OWNER" in roles(who, "eo"), key
            else:  # 单元级长期目标、周期目标、Mission：单元的 DRI（单元与周期目标的责任人都按角色 DOMAIN_DRI）
                assert "DOMAIN_DRI" in roles(who, "eo"), key
        elif step["do"] == "gate":
            target = STEPS[step["target"]]
            gate_roles = registry.action_spec(step["action"])["gate_roles"]
            assert set(gate_roles) & set(roles(who, target["domain"])), key
            if step["action"] == "world_commit_mission":
                assert owner_of[step["target"]] == who, key
        elif step["do"] == "assign":
            assert "DOMAIN_DRI" in roles(who, "eo") and "OWNER" in roles(step["to"], "eo"), key
        else:
            assert EO_SPEC["principals"][step["to"]]["type"] == "agent", key
            assert step["domains"] == list(EO_SPEC["principals"][who]["roles"]), key
            assert step["families"] == ["gate", "assign", "lifecycle"] and step["valid_until"] == "2026-10-31T23:59:59+08:00"


def test_the_order_satisfies_the_0_2_guards() -> None:
    assert "confirm_eo_goal" in ancestors("commit_october_goal"), "形成锚定：长期目标须已确认"
    assert "commit_october_goal" in ancestors("confirm_october_goal")
    for mission in ("mission_trial", "mission_experiments", "mission_lock"):
        commit, confirm = f"commit_{mission}", f"confirm_{mission}"
        assert {"confirm_october_goal", f"assign_{mission}"} <= ancestors(commit), "parent_goal_confirmed；Owner 本人"
        assert commit in ancestors(confirm)
        tasks = [key for key, step in STEPS.items() if step.get("payload", {}).get("parent_ref") == f"@{mission}"]
        assert tasks and all(f"assign_{mission}" in ancestors(task) for task in tasks), "Task 由 Mission 的 Owner 建"
    for key, step in STEPS.items():
        if key != "company":
            assert "company" in ancestors(key), "委托以 Company 为主体，其余都挂在它下面"
        if step["do"] == "delegate":
            mine = [other for other, item in STEPS.items() if item["by"] == step["by"] and other != key]
            assert set(mine) <= ancestors(key), "委托放在那个人那段的最后"
            assert [item["key"] for item in PLAN["steps"] if item["by"] == step["by"]][-1] == key


def test_the_segments_alternate_as_given_and_each_says_who_is_next() -> None:
    done: set[str] = set()
    for number, who in enumerate(SEGMENTS):
        todo = seed.runnable(PLAN, done, who)
        assert todo and all(STEPS[key]["by"] == who for key in todo), number
        done |= set(todo)
        up = seed.next_up(PLAN, done)
        assert [person for person, _ in up] == SEGMENTS[number + 1:number + 2], number
    assert done == set(STEPS)
    counts = {who: sum(1 for step in PLAN["steps"] if step["by"] == who) for who in ("ceo", "eo-dri", "eo-owner")}
    assert counts == {"ceo": 8, "eo-dri": 13, "eo-owner": 12}


def test_a_segment_takes_only_that_persons_steps_whose_prerequisites_are_met() -> None:
    assert seed.runnable(PLAN, set(), "ceo") == ["company", "strategy", "unit_eo", "company_goal"]
    for who in ("eo-dri", "eo-owner", "tianshu", "nobody"):
        assert seed.runnable(PLAN, set(), who) == []
    first = set(seed.runnable(PLAN, set(), "ceo"))
    assert seed.runnable(PLAN, first, "ceo") == [], "CEO 的确认要等 DRI 建 E&O 长期目标"
    assert seed.runnable(PLAN, first, "eo-dri") == ["eo_goal"]
    assert seed.runnable(PLAN, first | {"eo_goal", "confirm_company_goal"}, "ceo") == ["confirm_eo_goal"]


# ------------------------------------------------------------------ the content
def test_the_company_layer_copies_the_september_replay_text() -> None:
    replay = {step["key"]: step for step in REPLAY["steps"] if step["do"] == "create"}
    for key in ("company", "strategy", "unit_eo", "company_goal", "eo_goal"):
        mine, theirs = STEPS[key]["payload"], replay[key]["payload"]
        assert (STEPS[key]["type"], STEPS[key]["domain"]) == (replay[key]["type"], replay[key]["domain"]), key
        assert {k: v for k, v in mine.items() if k != "blocks"} == {
            k: v for k, v in theirs.items() if k not in ("blocks", "architecture_ref")} | (
            {"architecture_ref": "@strategy#responsibility_structure/eo"} if key == "unit_eo" else {}), key
        assert {block: value["text"] for block, value in mine["blocks"].items()} == {
            block: value["text"] for block, value in theirs["blocks"].items()}, key
        assert not any(value.get("artifacts") for value in mine["blocks"].values()), "feishu.example 占位链接不带"
    entries = STEPS["strategy"]["payload"]["blocks"]["responsibility_structure"]["components"]
    assert [(c["id"], c["type"]) for c in entries] == [("eo", "unit_entry")], "责任结构只列 E&O 一个条目"


def test_the_october_goal_missions_and_tasks_are_the_ones_asked_for() -> None:
    goal = STEPS["october_goal"]["payload"]
    assert (goal["title"], goal["period"], goal["goal_ref"]) == (
        "E&O 10 月：tkos.world 0.2 在真实经营中跑通", "2026-10", "@eo_goal") and "review_ref" not in goal
    assert [c["text"] for c in goal["blocks"]["acceptance"]["components"]] == [
        "10 月 16 日天枢联调验收通过",
        "E&O 十月的工作全部经本体记录（每张任务卡有每周快照，执行事项的完成由交付事件推出）",
        "tkos.world 0.2 锁版（验收冻结并钉定提交）"]
    assert len(goal["blocks"]["outcome"]["components"]) == 3
    tasks = {}
    for step in PLAN["steps"]:
        if step["do"] == "create" and step["type"] == "Task":
            tasks.setdefault(STEPS[step["payload"]["parent_ref"][1:]]["payload"]["title"], []).append(step["payload"]["title"])
    assert tasks == {
        "天枢 × 本体 0.2 试用": ["联调与委托登记（10/9–11）", "试用周每周快照与问题流转（10/12–16）", "10/16 联调验收"],
        "0.2 实验与报告": ["对照实验 B：Task-only 与 Task+Activity", "实验 E：五个场景、真实战略材料与标准答案",
                        "四种取法对照（含 RAG）与实验报告"],
        "0.2 锁版": ["验收冻结与钉定提交", "发版与实例切换"]}
    assert all(STEPS[step["target"]]["type"] == "Mission" for step in PLAN["steps"] if step["do"] == "assign"), \
        "Task 先不指派执行人"


# ------------------------------------------------------------------ plan and ids checks
@pytest.mark.parametrize(("change", "says"), [
    (lambda p: p["steps"][1].update({"after": ["unit_eo"]}), "after 只列前面的步骤"),
    (lambda p: p["steps"][3].update({"key": "company"}), "不重复"),
    (lambda p: p["steps"][3].update({"after": []}), "须是它前置里建对象的步骤"),
    (lambda p: p["steps"][2]["payload"].update({"architecture_ref": "@strategy#responsibility_structure/agents"}), "没有组件 agents"),
    (lambda p: p["steps"][5].update({"action": "world_fly"}), "门动作"),
    (lambda p: p["steps"][0].update({"email": "x"}), "必须有"),
    (lambda p: p.update({"format": "other"}), "format"),
])
def test_a_plan_that_does_not_hold_is_refused(change, says) -> None:
    plan = deepcopy(PLAN)
    change(plan)
    with pytest.raises(ValueError, match=says):
        seed.check_plan(plan)


def ids_from(spec: dict) -> dict:
    return {"scope_id": "s", "tenant_id": spec["tenant_id"], "domains": {key: FAKE.get(key, key) for key in spec["domains"]},
            "principals": {key: {"principal_id": FAKE.get(key, key), "type": item["type"],
                                 "display_name": item["display_name"]} for key, item in spec["principals"].items()}}


def test_the_plan_matches_the_experiment_scope_roster() -> None:
    assert seed.check_people(PLAN, ids_from(EO_SPEC)) == []
    thin = ids_from(EO_SPEC)
    del thin["principals"]["tianshu"], thin["domains"]["company"]
    problems = seed.check_people(PLAN, thin)
    assert any("tianshu" in p for p in problems) and any("company" in p for p in problems)


# ------------------------------------------------------------------ status view and proxy notes
NAMES = {key: item["display_name"] for key, item in EO_SPEC["principals"].items()}


def test_the_status_view_says_who_did_each_step_in_person_or_by_proxy_and_who_is_waited_on() -> None:
    done = {"company": {"event_id": "e1", "proxy": "eo-dri"}, "strategy": {"event_id": "e2", "proxy": None}}
    lines = seed.status_lines(PLAN, {"steps": done, "notes": []}, NAMES)
    text = "\n".join(lines)
    assert lines[0].startswith("== 状态：E&O 十月起点，共 33 步，已做 2（其中代录 1）")
    assert "建公司「词元云集（TokenKing）」 —— 已做·代录（eo-dri 代） event:e1" in text
    assert "建战略「词元云集总体战略（存根）」 —— 已做·本人 event:e2" in text
    assert "建责任单元「E&O」 —— 可以做" in text
    assert "建长期目标「E&O 六个月目标」 —— 在等 ceo：建责任单元「E&O」 等 2 步" in text
    assert "确认 Mission「0.2 锁版」 —— 在等 eo-owner：承诺 Mission「0.2 锁版」" in text
    assert lines[-1] == "下一步：CEO（ceo）做 建责任单元「E&O」 等 2 步"
    everything = {key: {"event_id": key, "proxy": None} for key in STEPS}
    assert seed.status_lines(PLAN, {"steps": everything, "notes": []}, NAMES)[-1] == "全部完成"


def test_the_proxy_note_lists_every_event_of_the_segment_and_keeps_its_key_on_rerun() -> None:
    keys = ["company", "strategy", "unit_eo"]
    assert seed.note_key("ceo", keys) == seed.note_key("ceo", list(reversed(keys)))
    assert seed.note_key("ceo", keys) != seed.note_key("eo-owner", keys) != seed.note_key("ceo", keys[:2])
    assert seed.note_key("ceo", keys).startswith("world-02-eo-seed:proxy-note:ceo:")
    events = [str(uuid.uuid4()) for _ in keys]
    entries = [("world_create_object", event, seed.describe(STEPS[key], STEPS)) for key, event in zip(keys, events)]
    refs = [f"{FAKE[key]}@1" for key in keys]
    params = seed.note_params("CEO", "E&O DRI", "2026-10-09", entries, refs, "2026-10-09T01:02:03.456789+00:00")
    lines = params["content"]["text"].splitlines()
    assert lines[0] == "以下CEO名下的记录由E&O DRI于 2026-10-09 代为录入，待本人复核："
    assert lines[1] == f"1. world_create_object event:{events[0]}（建公司「词元云集（TokenKing）」）" and len(lines) == 4
    assert params["content"]["refs"] == [f"event:{event}" for event in events] and params["category"] == "other"
    models.WorldV02RecordEventParams.model_validate(params)


# ------------------------------------------------------------------ the script against a fake service
class FakeWorld:
    """只够播种脚本用的假服务：读对象给版本，prepare 回空依赖，commit 按 (调用者, 幂等键) 记回执，同键同请求重放。"""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.receipts: dict[tuple, tuple] = {}
        self.posts: list[tuple] = []
        self.lose_next_response = False
        self.next_commit_status: int | None = None  # 下一次 commit 不执行，直接回这个状态码

    def __call__(self, method, path, body=None, who=None):
        if method == "GET":
            item = self.objects[path.rsplit("/", 1)[1]]
            return 200, {"business": {"version": item["version"], "object_version": item["object_version"],
                                      "revision_id": item["revision_id"]}}
        self.posts.append((path, who, body["idempotency_key"]))
        if path.endswith("/prepare"):
            return 200, {"expected_versions": []}
        if self.next_commit_status is not None:
            status, self.next_commit_status = self.next_commit_status, None
            return status, {"error": {"code": "SIMULATED"}}
        key = (who, body["idempotency_key"])
        if key in self.receipts:
            sent, receipt = self.receipts[key]
            return (200, receipt) if sent == body else (409, {"error": {"code": "IDEMPOTENCY_CONFLICT"}})
        result = {"contract_version": seed.V02, "event_id": str(uuid.uuid4())}
        if body["action_type"] == "world_create_object":
            object_id = str(uuid.uuid4())
            self.objects[object_id] = {"version": 1, "object_version": 1, "revision_id": str(uuid.uuid4()),
                                       "type": body["params"]["object_type"]}
            result.update(object_id=object_id, version=1)
        elif body["target"] is not None:
            item = self.objects[body["target"]["object_id"]]
            if body["target"]["expected_version"] != item["object_version"]:
                return 409, {"error": {"code": "VERSION_CONFLICT"}}
            item["object_version"] += 1
            if body["action_type"] == "world_assign":
                item["version"] += 1
            result.update(object_id=body["target"]["object_id"], version=item["version"])
        else:
            company = next(oid for oid, item in self.objects.items() if item["type"] == "Company")
            result["subject_refs"] = [{"object_id": company}]
        receipt = {"status": "committed", "action_type": body["action_type"], "receipt_id": str(uuid.uuid4()),
                   "recorded_at": datetime.now(timezone.utc).isoformat(), "result": result}
        self.receipts[key] = (deepcopy(body), receipt)
        if self.lose_next_response:
            self.lose_next_response = False
            seed.fail("连不上（假服务：提交了但回应丢了）")
        return 200, receipt


@pytest.fixture
def world(tmp_path, monkeypatch):
    (tmp_path / "ids.json").write_text(json.dumps(ids_from(EO_SPEC)), encoding="utf-8")
    fake = FakeWorld()
    monkeypatch.setattr(seed.Seeder, "call", lambda self, method, path, body=None, who=None: fake(method, path, body, who))
    return tmp_path, fake


def run(out, who, operator=None, dry_run=False):
    seeder = seed.Seeder("http://127.0.0.1:1", out, PLAN)
    seeder.segment(who, operator, dry_run)
    return seeder


def test_the_segments_seed_everything_once_with_fixed_keys_and_a_proxy_note_per_proxied_segment(world, capsys) -> None:
    out, fake = world
    operators = {"ceo": "eo-dri", "eo-owner": "eo-dri", "eo-dri": None}
    for who in SEGMENTS:
        run(out, who, operators[who])
        assert "下一步" in capsys.readouterr().out or who == SEGMENTS[-1]
    state = json.loads((out / seed.STATE_FILE).read_text(encoding="utf-8"))
    assert set(state["steps"]) == set(STEPS) and state["pending"] == {}
    commits = [key for path, _, key in fake.posts if path == "/v1/actions"]
    assert len(commits) == len(set(commits)) == len(STEPS) + len(state["notes"])
    assert {key for key in commits if ":proxy-note:" not in key} == {seed.KEY_PREFIX + key for key in STEPS}
    assert [(note["person"], note["operator"], len(note["steps"])) for note in state["notes"]] == [
        ("ceo", "eo-dri", 4), ("ceo", "eo-dri", 2), ("ceo", "eo-dri", 2), ("eo-owner", "eo-dri", 3), ("eo-owner", "eo-dri", 9)]
    for note in state["notes"]:
        sent = fake.receipts[("eo-dri", note["idempotency_key"])][0]["params"]
        assert sent["content"]["refs"] == [f"event:{state['steps'][key]['event_id']}" for key in note["steps"]]
        assert all(state["steps"][key]["note"] == note["event_id"] for key in note["steps"])
    gates = [body for body, _ in fake.receipts.values() if body["action_type"] in seed.GATES]
    assert {body["params"]["content"]["text"] for body in gates} == {
        "E&O 十月起点播种", "E&O 十月起点播种。由E&O DRI代CEO录入，待本人复核",
        "E&O 十月起点播种。由E&O DRI代E&O Mission Owner录入，待本人复核"}
    assert all(record["proxy"] == ("eo-dri" if record["by"] != "eo-dri" else None) for record in state["steps"].values())
    posts = len(fake.posts)
    for who in SEGMENTS:
        run(out, who, operators[who])
    assert len(fake.posts) == posts, "重跑什么都不再写"


def test_a_commit_whose_answer_was_lost_is_resent_as_is_and_not_recorded_twice(world) -> None:
    out, fake = world
    fake.lose_next_response = True
    with pytest.raises(SystemExit):
        run(out, "ceo", "eo-dri")
    state = json.loads((out / seed.STATE_FILE).read_text(encoding="utf-8"))
    assert list(state["pending"]) == [seed.KEY_PREFIX + "company"] and state["steps"] == {}
    with pytest.raises(SystemExit):
        run(out, "ceo")  # 上次是代录时中断的：换成本人执行要先按同样的方式重跑
    fake.next_commit_status = 503
    with pytest.raises(SystemExit):
        run(out, "ceo", "eo-dri")  # 重发时服务端错误：记下的请求留着
    assert list(json.loads((out / seed.STATE_FILE).read_text(encoding="utf-8"))["pending"]) == [seed.KEY_PREFIX + "company"]
    run(out, "ceo", "eo-dri")
    state = json.loads((out / seed.STATE_FILE).read_text(encoding="utf-8"))
    assert state["pending"] == {} and len(state["steps"]) == 4 and len(state["notes"]) == 1
    assert [key for path, _, key in fake.posts if path.endswith("/prepare")].count(seed.KEY_PREFIX + "company") == 1
    assert sum(1 for (_, key) in fake.receipts if key == seed.KEY_PREFIX + "company") == 1


def test_a_resend_the_service_refuses_was_never_committed_and_is_prepared_again(world) -> None:
    out, fake = world
    run(out, "ceo")
    run(out, "eo-dri")
    fake.lose_next_response = True
    with pytest.raises(SystemExit):
        run(out, "ceo")  # 确认公司级长期目标提交了但回应丢了
    fake.receipts = {key: value for key, value in fake.receipts.items()
                     if key[1] != seed.KEY_PREFIX + "confirm_company_goal"}  # 假装它其实没提交
    fake.next_commit_status = 409  # 原样重发被拒（例如对象此后又变了）
    run(out, "ceo")
    state = json.loads((out / seed.STATE_FILE).read_text(encoding="utf-8"))
    assert state["pending"] == {} and {"confirm_company_goal", "confirm_eo_goal"} <= set(state["steps"])
    assert [key for path, _, key in fake.posts if path.endswith("/prepare")].count(
        seed.KEY_PREFIX + "confirm_company_goal") == 2


def test_the_proxy_sentence_can_be_reworded_and_a_bad_template_is_refused(world) -> None:
    out, fake = world
    seeder = seed.Seeder("http://127.0.0.1:1", out, PLAN, "经{person}同意，由{operator}代为录入")
    assert seeder.content_text("ceo", "eo-dri") == "E&O 十月起点播种。经CEO同意，由E&O DRI代为录入"
    assert seeder.content_text("eo-dri", None) == "E&O 十月起点播种"
    with pytest.raises(SystemExit):
        seed.Seeder("http://127.0.0.1:1", out, PLAN, "由{someone}录入").segment("ceo", "eo-dri", dry_run=False)
    with pytest.raises(SystemExit):
        run(out, "ceo", "ceo")  # 代录人不能是本人
    assert fake.posts == []


def test_a_dry_run_lists_the_segment_and_writes_nothing(world, capsys) -> None:
    out, fake = world
    run(out, "ceo", "eo-dri", dry_run=True)
    printed = capsys.readouterr().out
    assert "CEO（ceo）这一段 4 步，由 E&O DRI（eo-dri）代录（dry-run，不写）" in printed
    assert "之后以 eo-dri 的凭证补记一条代录说明" in printed
    assert fake.posts == [] and not (out / seed.STATE_FILE).exists()
