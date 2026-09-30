"""deploy/world-02 的「E&O 十月起点」播种：计划按 0.2 的模型与判权成立、按人分段的顺序、按人筛选、状态视图、代录说明，
以及脚本的提交与重发（假服务）。不连库、不起服务。

2026-09-29 起（#73）计划按天枢个人任务重播：公司层照旧，十月周期目标与三个 Mission 只建不过门（门留给天枢代记），
不建 Task，三人各给天枢登记含议题族的委托。2026-09-30 起（#81）块与组件按方法侧 Content Pact 重排，正文不变；同日（#67）
Company 与 Strategy 的正文换成公司知识库战略材料的原句。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
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
SEGMENTS = ["ceo", "eo-dri", "ceo", "eo-dri", "eo-owner"]
MISSIONS = {"mission_grounding": "eo-owner", "mission_blueprint": "eo-owner", "mission_context": "eo-dri"}
FAMILIES = ["gate", "assign", "lifecycle", "issue"]
UNIT_REFS = [{"system": "tianshu", "id": "capability:05"}]
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
            assert step["families"] == FAMILIES and step["valid_until"] == "2026-10-31T23:59:59+08:00"


def test_the_order_satisfies_the_0_2_guards() -> None:
    assert {"confirm_company_goal", "confirm_eo_goal"} <= ancestors("october_goal"), "按用户的段序：长期目标确认后才建"
    for mission in MISSIONS:
        assert "october_goal" in ancestors(mission)
        assert STEPS[f"assign_{mission}"]["after"] == [mission]
    assert {f"assign_{mission}" for mission, owner in MISSIONS.items() if owner == "eo-owner"} <= ancestors(
        "delegate_owner"), "Owner 被指派之后才登记委托"
    for key, step in STEPS.items():
        if key != "company":
            assert "company" in ancestors(key), "委托以 Company 为主体，其余都挂在它下面"
        if step["do"] == "delegate":
            mine = [other for other, item in STEPS.items() if item["by"] == step["by"] and other != key]
            assert set(mine) <= ancestors(key), "委托放在那个人那段的最后"
            assert [item["key"] for item in PLAN["steps"] if item["by"] == step["by"]][-1] == key


def test_the_period_goal_and_the_missions_are_left_for_tianshu_to_gate_and_no_task_is_seeded() -> None:
    """周期目标与三个 Mission 只建（Mission 另指派 Owner），门留给天枢代记；不建 Task；三人各登记一条含四族的委托。"""
    assert not [key for key, step in STEPS.items() if step["do"] == "create" and step["type"] in ("Task", "Activity")]
    gated = {"october_goal", *MISSIONS}
    assert not [key for key, step in STEPS.items() if step["do"] == "gate" and step["target"] in gated]
    assert {step["target"] for step in PLAN["steps"] if step["do"] == "gate"} == {"company_goal", "eo_goal"}
    assert {step["target"]: step["to"] for step in PLAN["steps"] if step["do"] == "assign"} == MISSIONS
    delegations = {step["by"]: step for step in PLAN["steps"] if step["do"] == "delegate"}
    assert set(delegations) == {"ceo", "eo-dri", "eo-owner"}
    assert all(step["to"] == "tianshu" and step["families"] == FAMILIES for step in delegations.values())
    assert delegations["ceo"]["domains"] == ["company", "eo"]
    assert delegations["eo-dri"]["domains"] == delegations["eo-owner"]["domains"] == ["eo"]


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
    assert counts == {"ceo": 7, "eo-dri": 9, "eo-owner": 1}


def test_a_segment_takes_only_that_persons_steps_whose_prerequisites_are_met() -> None:
    assert seed.runnable(PLAN, set(), "ceo") == ["company", "strategy", "unit_eo", "company_goal"]
    for who in ("eo-dri", "eo-owner", "tianshu", "nobody"):
        assert seed.runnable(PLAN, set(), who) == []
    first = set(seed.runnable(PLAN, set(), "ceo"))
    assert seed.runnable(PLAN, first, "ceo") == [], "CEO 的确认要等 DRI 建 E&O 长期目标"
    assert seed.runnable(PLAN, first, "eo-dri") == ["eo_goal"]
    assert seed.runnable(PLAN, first | {"eo_goal", "confirm_company_goal"}, "ceo") == ["confirm_eo_goal", "delegate_ceo"]
    before_missions = set(STEPS) - {"october_goal", *MISSIONS, *(f"assign_{m}" for m in MISSIONS), "delegate_dri",
                                    "delegate_owner"}
    assert seed.runnable(PLAN, before_missions, "eo-owner") == [], "Owner 的委托要等他被指派"


# ------------------------------------------------------------------ the content
def texts(payload: dict) -> list[str]:
    """一个对象正文里的全部文字：块的 text 与各组件的 text。"""
    found = []
    for value in payload["blocks"].values():
        found += [value["text"]] if value.get("text") else []
        found += [item["text"] for item in value.get("components", [])]
    return found


def test_the_company_and_the_strategy_are_the_ones_of_the_experiment_e_trunk() -> None:
    """Company 与 Strategy 的正文是公司知识库战略材料的原句（#67）：与实验 E 的主干所照的十月起点原计划
    （experiments/world_v02/b_source-2026-10.json）整步相同，那里逐条对 experiments/world_v02/strategy_material.json 的
    原句与页码核对（tests/test_world_v02_experiment_e.py）。"""
    original = {step["key"]: step for step in json.loads(
        (ROOT / "experiments/world_v02/b_source-2026-10.json").read_text(encoding="utf-8"))["steps"]}
    for key in ("company", "strategy"):
        assert {k: v for k, v in STEPS[key].items() if k != "note"} == {
            k: v for k, v in original[key].items() if k != "note"}, key
        assert "strategy_material.json" in STEPS[key]["note"] and "b_source-2026-10.json" in STEPS[key]["note"], key


def test_the_company_layer_moves_the_september_replay_text_into_the_new_blocks_without_adding_any() -> None:
    """E&O 单元与两条长期目标的正文取自九月回放（experiments/world_v01/seed.json），按 Content Pact 放进新块与组件（#81）：
    每一段正文都是同一对象某个原块正文的一段（去掉句末标点），原块正文拆出去之后只剩标点——不编造，也不丢。Company 与
    Strategy 的正文 #67 换成了战略材料的原句，见上一条。"""
    replay = {step["key"]: step for step in REPLAY["steps"] if step["do"] == "create"}
    for key in ("unit_eo", "company_goal", "eo_goal"):
        mine, theirs = STEPS[key]["payload"], replay[key]["payload"]
        assert (STEPS[key]["type"], STEPS[key]["domain"]) == (replay[key]["type"], replay[key]["domain"]), key
        assert {k: v for k, v in mine.items() if k != "blocks"} == {
            k: v for k, v in theirs.items() if k not in ("blocks", "architecture_ref")} | (
            {"architecture_ref": "@strategy#responsibility_structure/eo", "external_refs": UNIT_REFS}
            if key == "unit_eo" else {}), key
        pieces = [text.rstrip("。；") for text in texts(mine)]
        originals = [value["text"] for value in theirs["blocks"].values()]
        assert all(any(piece in original for original in originals) for piece in pieces), key
        for original in originals:
            for piece in sorted(pieces, key=len, reverse=True):
                original = original.replace(piece, "")
            assert re.fullmatch(r"[，。；：\s]*", original), (key, original)
        assert not any(value.get("artifacts") or any(item.get("artifacts") for item in value.get("components", []))
                       for value in mine["blocks"].values()), "feishu.example 占位链接不带"
    assert {key: {block: [(c["id"], c["type"]) for c in value.get("components", [])]
                  for block, value in STEPS[key]["payload"]["blocks"].items()} for key in replay
            if key in ("unit_eo", "company_goal", "eo_goal")} == {
        "unit_eo": {"definition": [("contribution", "contribution"), ("mandate", "mandate"),
                                   ("boundary", "scope_boundary"), ("method-frozen", "key_constraint"),
                                   ("no-graph-db", "key_constraint")]},
        "company_goal": {"target": [("outcome", "outcome")]},
        "eo_goal": {"target": [("outcome", "outcome")]}}, "没有原文的组件留空"


def test_the_october_goal_and_the_three_missions_are_the_ones_asked_for() -> None:
    """周期目标沿用原草案（换任务卡之前的原计划存在 experiments/world_v02/b_source-2026-10.json）：目标定义块与
    那里的相同（原结果与验收标准两块的组件原样合进目标定义块）。三个 Mission 取天枢里 E&O 的三个个人任务：目标是战役定义块里的战役结果组件，每条
    验收一个成功 / 验收标准组件（ac-1 起编号），同在战役定义块；不写 Mission 计划块。"""
    goal = STEPS["october_goal"]["payload"]
    original = {step["key"]: step for step in json.loads(
        (ROOT / "experiments/world_v02/b_source-2026-10.json").read_text(encoding="utf-8"))["steps"]}
    before = original["october_goal"]["payload"]
    assert {k: v for k, v in goal.items() if k != "blocks"} == {k: v for k, v in before.items() if k != "blocks"}
    assert "review_ref" not in goal and list(goal["blocks"]) == ["target"]
    assert goal["blocks"]["target"]["components"] == before["blocks"]["target"]["components"]
    assert (goal["title"], goal["period"], goal["goal_ref"]) == (
        "E&O 10 月：tkos.world 0.2 在真实经营中跑通", "2026-10", "@eo_goal")
    missions = {key: STEPS[key]["payload"] for key in MISSIONS}
    assert [STEPS[key]["type"] for key in MISSIONS] == ["Mission"] * 3
    assert [payload["title"] for payload in missions.values()] == [
        "Ontology & Data Grounding", "ENO / Engine Blueprint", "可信 Context / Memory 与真实 Agent 读写闭环"]
    assert all(payload["goal_ref"] == "@october_goal" and "external_refs" not in payload for payload in missions.values())
    for key, payload in missions.items():
        assert set(payload["blocks"]) == {"definition"}, key
        outcome, *criteria = payload["blocks"]["definition"]["components"]
        assert (outcome["id"], outcome["type"]) == ("outcome", "outcome") and outcome["text"].strip(), key
        assert [c["id"] for c in criteria] == [f"ac-{n}" for n in range(1, len(criteria) + 1)], key
        assert all(c["type"] == "acceptance_criterion" and c["text"].strip() for c in criteria), key
    assert [len(payload["blocks"]["definition"]["components"]) - 1 for payload in missions.values()] == [4, 3, 4]
    assert "Receipt / Audit" in missions["mission_context"]["blocks"]["definition"]["components"][3]["text"]


def test_the_unit_carries_its_tianshu_reference_from_the_seed_and_nothing_else_does() -> None:
    """E&O 责任单元的外部引用由播种写好（责任单元无门，天枢不能以 Agent 身份修订它）；Mission 的外部引用
    mission:<编号> 由天枢以修订写入，不预写。"""
    assert STEPS["unit_eo"]["payload"]["external_refs"] == UNIT_REFS
    assert [key for key, step in STEPS.items() if "external_refs" in step.get("payload", {})] == ["unit_eo"]


# 两个拼音样例以摘要存，不把它们本身写进仓库；按整个字母串比对（编号里的拼音在数字与标点处断开）。
PINYIN_DIGESTS = {"4e54dfd7486137e1fd0ff3fd0307897604e9c44ebb27b238374fef9b7e3d2379",
                  "9ecd2947a7832336c3656fe2ca932fe2805ecd6d59eb6bd9229e1a7e46c7f851"}


def test_no_real_name_and_no_person_numbered_tianshu_id_is_in_the_repository_files() -> None:
    """真名只在主机的 spec.json：样例名单里人的显示名是角色名。天枢编号只有不带人名的 capability:05 与实测示例用的
    mission:demo-<run>；个人任务编号（mission:<编号>，带人名拼音）不进计划、名单与说明。"""
    humans = {key: item["display_name"] for key, item in EO_SPEC["principals"].items() if item["type"] == "human"}
    assert humans == {"ceo": "CEO", "eo-dri": "E&O DRI", "eo-owner": "E&O Mission Owner"}
    files = [DEPLOY / name for name in ("seed-eo-2026-10.json", "spec.eo.example.json", "README.md", "seed_eo.py")]
    for path in files:
        text = path.read_text(encoding="utf-8")
        ids = set(re.findall(r"\b(?:mission|capability|todo):[A-Za-z0-9][\w.-]*", text))
        assert {i for i in ids if not i.startswith("mission:demo-")} <= {"capability:05"}, (path.name, ids)
        assert not {hashlib.sha256(run.encode()).hexdigest() for run in re.findall(r"[a-z]+", text.lower())} & \
            PINYIN_DIGESTS, path.name


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
    assert lines[0].startswith("== 状态：E&O 十月起点，共 17 步，已做 2（其中代录 1）")
    assert "建公司「词元云集（TokenKing）」 —— 已做·代录（eo-dri 代） event:e1" in text
    assert "建战略「词元云集总体战略（存根）」 —— 已做·本人 event:e2" in text
    assert "建责任单元「E&O」 —— 可以做" in text
    assert "建长期目标「E&O 六个月目标」 —— 在等 ceo：建责任单元「E&O」 等 2 步" in text
    assert ("eo-owner 登记给 tianshu 的委托（门、指派、生命周期、议题；域 eo） —— "
            "在等 eo-dri：指派 Mission「Ontology & Data Grounding」给 eo-owner 等 2 步") in text
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
        ("ceo", "eo-dri", 4), ("ceo", "eo-dri", 3), ("eo-owner", "eo-dri", 1)]
    for note in state["notes"]:
        sent = fake.receipts[("eo-dri", note["idempotency_key"])][0]["params"]
        assert sent["content"]["refs"] == [f"event:{state['steps'][key]['event_id']}" for key in note["steps"]]
        assert all(state["steps"][key]["note"] == note["event_id"] for key in note["steps"])
    gates = [body for body, _ in fake.receipts.values() if body["action_type"] in seed.GATES]
    assert len(gates) == 2 and {body["params"]["content"]["text"] for body in gates} == {
        "E&O 十月起点播种。由E&O DRI代CEO录入，待本人复核"}, "门只剩 CEO 确认两个长期目标"
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
