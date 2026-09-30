"""天枢已经在写的形状，按方法侧 Content Pact 替换登记时不许变（映射表 docs/world-v02-content-pact-mapping.md 第 5 节，票 #79）。

形状从下面这些文件里实际取出，不在这里另写清单：

- ``docs/world-v02-tianshu-examples.md``：给天枢的实测示例，由 deploy/world-02/examples.py 在冒烟 scope 上真打、脱敏渲染。
  取第 1 至 10 节的 POST 请求体（第 11 节是故意出错的请求，不算）：天枢服务主体写的快照 payload、组件、外部引用、代记与
  自己身份记的动作；另取人建对象与修订时带计划条目的块（Task 的 plan 块由 E&O 建，天枢按它读）。
- ``docs/world-v02-tianshu-interface-changes.md``：接口变化清单的组件表、payload 表（``blockers`` 只出现在这里，示例
  没有写它）、委托族与代记对照表、全文提到的动作名。
- ``deploy/world-02/seed-eo-2026-10.json``：十月起点给天枢登记的委托族、播种写好的外部引用。只取这两样：播种里对象的
  块 id（``acceptance``、``constraint`` 等）正是映射表第 5 节说天枢要改的，不钉。
- ``deploy/world-02/smoke.py`` 与 ``examples.py``：按语法树取 ``act("tianshu", <动作>, …)`` 的动作名与是否代记、三种
  组件字面量的类型属性键、带 ``payload_type`` 的快照字面量的块键、冒烟核对的 MCP 工具名。

登记直接读 docs/contracts/world-registry-0.2.json（不经随包副本的哈希核对）；快照与组件另按运行时模型校验一遍：天枢
按现在的写法写，替换后也必须通过。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import uuid

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
EXAMPLES = (ROOT / "docs/world-v02-tianshu-examples.md").read_text(encoding="utf-8")
CHANGES = (ROOT / "docs/world-v02-tianshu-interface-changes.md").read_text(encoding="utf-8")
SEED = json.loads((ROOT / "deploy/world-02/seed-eo-2026-10.json").read_text(encoding="utf-8"))
SOURCES = [ROOT / "deploy/world-02/smoke.py", ROOT / "deploy/world-02/examples.py"]
TIANSHU = "tianshu"
# 映射表第 5 节点名的三种组件：天枢写的进展条目与问题，天枢与 E&O 都写的计划条目。
COMPONENTS = ("progress_item", "issue", "plan_item")

OBJECTS = {item["type"]: item for item in REGISTRY["objects"]}
PAYLOADS = {item["id"]: item for item in REGISTRY["state"]["payload_types"]}
ACTIONS = {item["action"]: item for item in REGISTRY["actions"]}
COMPONENT_TYPES = {item["id"]: item for item in REGISTRY["components"]["types"]}
FAMILIES = {item["id"]: item for item in REGISTRY["delegation"]["families"]}


# ------------------------------------------------------------------ 从文件里取
def _requests() -> list[dict]:
    """实测示例第 1 至 10 节的动作请求（POST /v1/actions 与 prepare）：身份与请求体（请求行之后的第一个 json 块）。"""
    found, section = [], None
    lines = EXAMPLES.splitlines()
    for index, line in enumerate(lines):
        heading = re.match(r"^## (\d+)\. ", line)
        if line.startswith("## "):
            section = int(heading[1]) if heading else None
        request = re.match(r"^`POST (\S+)`，身份：`([^`]+)`", line)
        if request is None or section is None or not 1 <= section <= 10 or not request[1].startswith("/v1/actions"):
            continue
        start = next(i for i in range(index + 1, len(lines)) if lines[i].strip())
        assert lines[start] == "```json", f"line {index + 1}: a POST is followed by its request body"
        end = lines.index("```", start + 1)
        found.append({"section": section, "path": request[1], "who": request[2],
                      "body": json.loads("\n".join(lines[start + 1:end]))})
    return found


REQUESTS = _requests()
TIANSHU_WRITES = [item["body"] for item in REQUESTS if item["who"] == TIANSHU]
PLACEHOLDER = re.compile(r"<[^<>\s\"]+>")


def _concrete(value):
    """占位换成同名同值的 uuid，引用与组件 id 才能按运行时模型校验。"""
    return json.loads(PLACEHOLDER.sub(lambda match: str(uuid.uuid5(uuid.NAMESPACE_URL, match[0])), json.dumps(value)))


def _target_type(body: dict) -> str | None:
    """请求目标的类型：占位 <mission-1>、<task-2> 按示例的类型编号（examples.py 的 SLUGS）还原。"""
    target = body.get("target")
    if not target:
        return None
    slug = re.match(r"^<([a-z-]+?)(?:-\d+|:[a-z-]+)>$", target["object_id"])[1]
    return {"company": "Company", "strategy": "Strategy", "unit": "ResponsibilityUnit",
            "long-term-goal": "LongTermGoal", "period-goal": "PeriodGoal", "mission": "Mission", "task": "Task",
            "activity": "Activity", "snapshot": "StateSnapshot"}[slug]


def _table(text: str, header: str) -> list[list[str]]:
    """Markdown 表的行（不含表头与分隔行），按单元格拆开。"""
    lines = text.splitlines()
    start = lines.index(header) + 2
    rows = []
    while start < len(lines) and lines[start].startswith("|"):
        rows.append([cell.strip() for cell in lines[start].strip("|").split("|")])
        start += 1
    return rows


def _ticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def _trees() -> list[ast.Module]:
    return [ast.parse(path.read_text(encoding="utf-8")) for path in SOURCES]


def _keys(node: ast.Dict) -> dict[str, ast.expr]:
    return {key.value: value for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)}


def _dicts() -> list[dict[str, ast.expr]]:
    return [_keys(node) for tree in _trees() for node in ast.walk(tree) if isinstance(node, ast.Dict)]


def _tianshu_calls() -> list[tuple[str, bool]]:
    """smoke.py 与 examples.py 里 act("tianshu", <动作>, <参数>) 的动作名，与参数字面量里带没带 on_behalf_of；带
    expect= 的是故意出错的请求（示例第 11 节），不算。"""
    calls = []
    for tree in _trees():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "act"
                    and len(node.args) >= 2 and all(isinstance(arg, ast.Constant) for arg in node.args[:2])
                    and node.args[0].value == TIANSHU and not any(item.arg == "expect" for item in node.keywords)):
                params = node.args[2] if len(node.args) > 2 else None
                behalf = isinstance(params, ast.Dict) and "on_behalf_of" in _keys(params)
                calls.append((node.args[1].value, behalf))
    return calls


def _type_names() -> dict[str, str]:
    """接口清单里主体的写法：类型名或登记的中文显示名。"""
    return {**{item["type"]: item["type"] for item in REGISTRY["objects"]},
            **{item["display_name"]: item["type"] for item in REGISTRY["objects"]}}


# ------------------------------------------------------------------ 取到了东西（解析没有静默落空）
def test_the_sources_yield_what_tianshu_writes():
    actions = {body["action_type"] for body in TIANSHU_WRITES}
    assert {"world_refresh_state", "world_revise_object", "world_record_event", "world_commit_mission",
            "world_raise_issue", "world_dispose_issue"} <= actions
    assert len(_tianshu_calls()) >= 10 and any(behalf for _, behalf in _tianshu_calls())
    assert _table(CHANGES, "| 组件类型 | 放在哪 | 类型属性 |") and _table(CHANGES, "| `payload_type` | 主体 | 块 |")


# ------------------------------------------------------------------ 快照 payload 与块
def test_the_snapshot_payloads_and_blocks_tianshu_writes_stay_registered():
    """天枢写过的快照：payload 类型与块 id 仍在登记里，块仍允许写进去的组件类型。"""
    written = [(body["params"]["payload"]["payload_type"], body["params"]["payload"]["blocks"])
               for body in TIANSHU_WRITES if body["action_type"] == "world_refresh_state"]
    written += [(fields["payload_type"].value, {key: None for key in _keys(fields["blocks"])})
                for fields in _dicts() if isinstance(fields.get("payload_type"), ast.Constant)
                and isinstance(fields.get("blocks"), ast.Dict)]
    assert written
    for payload_type, blocks in written:
        allowed = {block["id"]: block["components"] for block in PAYLOADS[payload_type]["blocks"]}
        for block_id, value in blocks.items():
            assert block_id in allowed, (payload_type, block_id)
            for component in (value or {}).get("components", []):
                assert component["type"] in allowed[block_id], (payload_type, block_id, component["type"])


def test_the_payload_table_given_to_tianshu_stays_registered():
    """接口清单第四项的 payload 表：每种 payload 的主体与块都还在（blockers 只在这里出现）。"""
    names = _type_names()
    rows = _table(CHANGES, "| `payload_type` | 主体 | 块 |")
    assert {_ticks(row[0])[0] for row in rows} == {"execution_state", "goal_state", "unit_state", "strategy_state",
                                                   "company_review"}
    for cell, subjects, blocks in rows:
        payload = PAYLOADS[_ticks(cell)[0]]
        assert {names[name] for name in subjects.split("、")} == set(payload["subjects"]), payload["id"]
        assert set(_ticks(blocks)) <= {block["id"] for block in payload["blocks"]}, payload["id"]


def test_the_snapshots_tianshu_wrote_still_validate():
    """示例里天枢写的快照原样（占位换成 uuid）按运行时模型校验通过：新增的状态块都可以缺省。"""
    snapshots = [body["params"]["payload"] for body in TIANSHU_WRITES if body["action_type"] == "world_refresh_state"]
    assert snapshots
    for payload in snapshots:
        models.validate_snapshot(_concrete(payload))


# ------------------------------------------------------------------ 组件与类型属性
def _written_components() -> list[dict]:
    """示例请求里写过的三种组件（任何人写的，计划条目 E&O 也写）。"""
    found = []

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") in COMPONENTS and "id" in value:
                found.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk([item["body"] for item in REQUESTS])
    return found


def _observed_attributes() -> dict[str, set[str]]:
    """三种组件被写过或被告知天枢的类型属性：示例请求、smoke/examples 的字面量与接口清单的组件表。"""
    seen: dict[str, set[str]] = {name: set() for name in COMPONENTS}
    for component in _written_components():
        seen[component["type"]] |= set(component.get("attributes", {}))
    for fields in _dicts():
        kind = fields.get("type")
        if isinstance(kind, ast.Constant) and kind.value in COMPONENTS and isinstance(fields.get("attributes"), ast.Dict):
            seen[kind.value] |= set(_keys(fields["attributes"]))
    registered = {name: {item["id"] for item in COMPONENT_TYPES[name]["attributes"]} for name in COMPONENTS}
    for cell, _, attributes in _table(CHANGES, "| 组件类型 | 放在哪 | 类型属性 |"):
        name = _ticks(cell)[0]
        seen[name] |= {token for token in _ticks(attributes) if token in registered[name] or
                       re.fullmatch(r"[a-z]+_[a-z_]+|entries|responsible", token)}
    return seen


def test_the_components_tianshu_writes_keep_their_attributes():
    """三种组件仍登记，写过与告知过的类型属性都在；除此之外的属性（例如计划条目新加的）都可选。"""
    for name, seen in _observed_attributes().items():
        assert seen, name
        attributes = {item["id"]: item for item in COMPONENT_TYPES[name]["attributes"]}
        assert seen <= set(attributes), (name, seen - set(attributes))
        assert [item for item in attributes if item not in seen and attributes[item]["required"]] == [], name


def test_the_component_table_given_to_tianshu_stays_true():
    """接口清单第二项的组件表：进展条目的本期条目字段与来源取值、放在哪个快照块，都还对得上登记。"""
    fields = set(REGISTRY["components"]["progress_entry_fields"])
    sources = {item["id"] for item in REGISTRY["components"]["progress_entry_sources"]}
    shell = set(REGISTRY["components"]["shell"])
    for cell, place, attributes in _table(CHANGES, "| 组件类型 | 放在哪 | 类型属性 |"):
        name = _ticks(cell)[0]
        registered = {item["id"] for item in COMPONENT_TYPES[name]["attributes"]}
        for token in _ticks(attributes):
            inner = re.fullmatch(r"\{(.*)\}", token)
            if inner:
                assert {part.strip().rstrip("?") for part in inner[1].split(",")} <= fields, name
            else:
                assert token in registered | fields | sources | shell, (name, token)
        for block_id in _ticks(place):  # 「执行状态快照的 progress 块」「各类快照的 issues 块」
            holders = [PAYLOADS["execution_state"]] if "执行状态" in place else list(PAYLOADS.values())
            blocks = [block for payload in holders for block in payload["blocks"] if block["id"] == block_id]
            assert blocks and all(name in block["components"] for block in blocks), (name, block_id)


def test_the_components_written_in_the_examples_still_validate():
    """示例里写过的每条进展条目、问题与计划条目原样（占位换成 uuid）按运行时的组件模型校验通过。"""
    written = _written_components()
    assert {item["type"] for item in written} == set(COMPONENTS)
    for component in written:
        models.component_model(component["type"]).model_validate(_concrete(component))


# ------------------------------------------------------------------ 计划块与外部引用
def test_the_plan_blocks_keep_their_ids_and_stay_activity_blocks():
    """带计划条目的块：Mission 的 execution_plan 由天枢以 Agent 身份直接修订、不走门，Task 的 plan 由 E&O 建时写；
    两块仍在、仍允许计划条目、仍是活动块。"""
    found = set()
    for item in REQUESTS:
        body = item["body"]
        params = body.get("params", {})
        object_type = params.get("object_type") or _target_type(body)
        for block_id, value in params.get("payload", {}).get("blocks", {}).items() if object_type in OBJECTS else []:
            if any(component.get("type") == "plan_item" for component in (value or {}).get("components", [])):
                found.add((object_type, block_id))
    assert found == {("Mission", "execution_plan"), ("Task", "plan")}
    for object_type, block_id in found:
        block = next(block for block in OBJECTS[object_type]["blocks"] if block["id"] == block_id)
        assert "plan_item" in block["components"] and block["class"] == "activity", (object_type, block_id)


def test_external_refs_stay_an_activity_attribute_where_they_are_written():
    """外部引用：天枢写 Mission 的，E&O 建 Task 与播种责任单元时写；仍是这些类型的活动属性。"""
    written = {params.get("object_type") or _target_type(item["body"]) for item in REQUESTS
               for params in [item["body"].get("params", {})] if "external_refs" in params.get("payload", {})}
    written |= {step["type"] for step in SEED["steps"] if "external_refs" in step.get("payload", {})}
    assert {"Mission", "Task", "ResponsibilityUnit"} <= written
    for object_type in written:
        attribute = next(item for item in OBJECTS[object_type]["attributes"] if item["id"] == "external_refs")
        assert (attribute["class"], attribute["value"], attribute["required"]) == ("activity", "external_refs", False)


# ------------------------------------------------------------------ 动作、代记与委托
def test_the_actions_tianshu_calls_keep_their_names():
    """天枢调的动作名都还在；以自己身份写的仍在 Agent 面上，代记的仍可代记。"""
    face = set(REGISTRY["agent_face"]["writes"])
    calls = [(body["action_type"], "on_behalf_of" in body["params"]) for body in TIANSHU_WRITES] + _tianshu_calls()
    for action, behalf in calls:
        assert action in ACTIONS, action
        if behalf:
            assert ACTIONS[action]["delegable"] in FAMILIES, action
        else:
            assert action in face, action
    reads = {f"world_{name}" for name in REGISTRY["agent_face"]["reads"]}
    for action in set(re.findall(r"`(world_[a-z_]+)`", CHANGES)):
        assert action in ACTIONS or action in reads, action


def test_the_mcp_tools_the_smoke_checks_stay_on_the_agent_face():
    tools = next(node.value for tree in _trees() for node in ast.walk(tree) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "MCP_TOOLS" for target in node.targets))
    names = [item.value for item in tools.elts]
    face = set(REGISTRY["agent_face"]["writes"]) | {f"world_{name}" for name in REGISTRY["agent_face"]["reads"]}
    assert names and set(names) <= face


def test_the_delegation_families_and_fields_stay():
    """委托族 id 与中文名、接口清单对照表里的代记动作、登记与代记写入的字段都不变。对照表的「族」一列按行写，不逐个
    动作精确（例如 world_assign_strategy_round 在指派族，却列在门那一行），所以只钉动作仍可代记、行里的族仍登记。"""
    named = dict(re.findall(r"`(gate|assign|lifecycle|issue)` (门|指派|生命周期|议题)", CHANGES))
    assert {family: FAMILIES[family]["display_name"] for family in named} == named
    for step in SEED["steps"]:
        if step["do"] == "delegate":
            assert set(step["families"]) <= set(FAMILIES), step["key"]
    for _, actions, families in _table(CHANGES, "| 天枢里的人工确认 | 本体动作 | 族 |"):
        assert set(families.split("、")) <= {family["display_name"] for family in FAMILIES.values()}, families
        for action in re.findall(r"`(world_[a-z_]+)`", actions):
            assert action in FAMILIES[ACTIONS[action]["delegable"]]["actions"], action
    grants = [item["body"]["params"] for item in REQUESTS if item["body"]["action_type"] == "world_grant_delegation"]
    assert grants and all(set(params) == set(REGISTRY["delegation"]["grant_fields"]) for params in grants)
    behalf = [body["params"]["on_behalf_of"] for body in TIANSHU_WRITES if "on_behalf_of" in body["params"]]
    assert behalf and all(set(item) == set(REGISTRY["delegation"]["write_fields"]["on_behalf_of"]) for item in behalf)


@pytest.mark.parametrize("name", COMPONENTS)
def test_the_three_components_are_still_registered(name):
    assert name in COMPONENT_TYPES
