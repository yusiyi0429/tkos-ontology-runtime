"""tkos.world/0.2 登记自检：登记内部的引用都能对上，登记改错立刻报错。

纯函数，登记以参数传入，不读文件、不连数据库。返回错误描述的列表，空列表即通过。核对：

- 动作：事件种类在词表里、类别与种类一致、结果是种类允许的；词表里每个种类都有动作产生；目标类型存在。
- 块与组件：块允许的组件类型已登记，每个组件类型至少有一个块允许；payload 的主体与类型的 payload 对得上。
- 状态表（各类型与 Issue）：状态已声明且都可达；转移的动作存在、以该类型为目标；守卫、记录者（``by``）、
  处置已定义；结果是动作允许的；终态只经重开离开；没有重复转移。
- 一轮与正式段：有门类型都有 ``formal_on`` 与 ``rounds``，其中的动作与状态（可开轮的、进入即作废的）存在；每种处置都有转移。
- 关系、委托动作族与 Agent 面里的动作存在。
"""
from __future__ import annotations

from typing import Any


def _lifecycle_errors(name: str, spec: dict[str, Any], actions: dict[str, Any], defined: dict[str, set[str]]) -> list[str]:
    errors = []
    ids = {item["id"] for item in spec["states"]}
    for state in [spec["initial"], *spec["terminal"]]:
        if state not in ids:
            errors.append(f"{name}: state {state} is not declared")
    seen = set()
    for item in spec["transitions"]:
        key = (item["action"], item["from"], item["outcome"], item["guard"], item["disposition"])
        if key in seen:
            errors.append(f"{name}: duplicate transition {key}")
        seen.add(key)
        action = actions.get(item["action"])
        if action is None:
            errors.append(f"{name}: transition on unknown action {item['action']}")
        else:
            if name != "Issue" and item["via"] is None and name not in action["target_types"]:
                errors.append(f"{name}: {item['action']} does not target {name}")
            if item["outcome"] is not None and item["outcome"] not in action["outcomes"]:
                errors.append(f"{name}: outcome {item['outcome']} is not allowed for {item['action']}")
        for state in (item["from"], item["to"]):
            if state not in ids:
                errors.append(f"{name}: {item['action']} uses undeclared state {state}")
        if item["from"] in spec["terminal"] and item["to"] != item["from"] and item["action"] != "world_reopen":
            errors.append(f"{name}: {item['action']} leaves terminal state {item['from']}")
        for field, known in (("by", defined["recorders"]), ("guard", defined["guards"]),
                             ("disposition", defined["dispositions"])):
            if item[field] is not None and item[field] not in known:
                errors.append(f"{name}: {item['action']} {field} {item[field]} is not defined")
    reachable, frontier = {spec["initial"]}, [spec["initial"]]
    while frontier:
        state = frontier.pop()
        for item in spec["transitions"]:
            if item["from"] == state and item["to"] not in reachable:
                reachable.add(item["to"])
                frontier.append(item["to"])
    for state in sorted(ids - reachable):
        errors.append(f"{name}: state {state} is unreachable")
    return errors


def check(registry: dict[str, Any]) -> list[str]:
    errors = []
    actions = {item["action"]: item for item in registry["actions"]}
    kinds = {item["kind"]: item for item in registry["event_kinds"]}
    types = {item["type"]: item for item in registry["objects"]}
    payloads = {item["id"]: item for item in registry["state"]["payload_types"]}
    component_types = {item["id"] for item in registry["components"]["types"]}
    defined = {"recorders": {item["id"] for item in registry["recorders"]},
               "guards": {item["id"] for item in registry["guards"]},
               "dispositions": {item["id"] for item in registry["issue"]["dispositions"]}}

    for action in registry["actions"]:
        kind = kinds.get(action["event_kind"])
        if kind is None:
            errors.append(f"action {action['action']}: event kind {action['event_kind']} is not in the vocabulary")
        else:
            if kind["class"] != action["class"]:
                errors.append(f"action {action['action']}: class {action['class']} differs from its event kind")
            for outcome in set(action["outcomes"]) - set(kind["outcomes"]):
                errors.append(f"action {action['action']}: outcome {outcome} is not allowed for {kind['kind']}")
        for target in action["target_types"]:
            if target not in types:
                errors.append(f"action {action['action']}: target type {target} is not registered")
    for kind in sorted(set(kinds) - {item["event_kind"] for item in registry["actions"]}):
        errors.append(f"event kind {kind} is produced by no action")

    allowed = set()
    for owner, blocks in ([(item["type"], item["blocks"]) for item in registry["objects"]]
                          + [(item["id"], item["blocks"]) for item in registry["state"]["payload_types"]]):
        for block in blocks:
            for component in block["components"]:
                allowed.add(component)
                if component not in component_types:
                    errors.append(f"{owner}.{block['id']}: component type {component} is not registered")
    for component in sorted(component_types - allowed):
        errors.append(f"component type {component} is allowed in no block")
    for item in registry["objects"]:
        payload = item["state_payload"]
        if payload is not None and item["type"] not in payloads.get(payload, {}).get("subjects", []):
            errors.append(f"{item['type']}: payload {payload} does not list it as a subject")
        for field in item["relation_fields"]:
            for target in field["targets"]:
                if target not in types:
                    errors.append(f"{item['type']}.{field['field']}: target type {target} is not registered")
            if field["written_by"] not in actions:
                errors.append(f"{item['type']}.{field['field']}: written by unknown action {field['written_by']}")
    for payload in payloads.values():
        for subject in payload["subjects"]:
            if types.get(subject, {}).get("state_payload") != payload["id"]:
                errors.append(f"payload {payload['id']}: subject {subject} does not use it")

    for name, spec in registry["lifecycles"].items():
        if name not in types:
            errors.append(f"lifecycle {name}: type is not registered")
        errors += _lifecycle_errors(name, spec, actions, defined)
        states = {item["id"] for item in spec["states"]}
        if spec["formal_on"] is not None and spec["formal_on"] not in states:
            errors.append(f"{name}: formal_on {spec['formal_on']} is not declared")
        rounds = spec["rounds"] or {}
        for state in rounds.get("allowed_in", []) + rounds.get("voided_in", []):
            if state not in states:
                errors.append(f"{name}: round state {state} is not declared")
        for key in ("opened_by", "agreed_by", "closed_by", "candidate_carried_by"):
            if rounds.get(key) is not None and rounds[key] not in actions:
                errors.append(f"{name}: round {key} names unknown action {rounds[key]}")
    for item in registry["objects"]:
        spec = registry["lifecycles"].get(item["type"])
        if item["gated"] and (spec is None or spec["formal_on"] is None or spec["rounds"] is None):
            errors.append(f"{item['type']}: gated type needs a lifecycle with formal_on and rounds")

    issue = registry["issue"]["lifecycle"]
    errors += _lifecycle_errors("Issue", issue, actions, defined)
    issue_states = {item["id"] for item in issue["states"]}
    for disposition in registry["issue"]["dispositions"]:
        if disposition["to"] not in issue_states:
            errors.append(f"disposition {disposition['id']}: state {disposition['to']} is not declared")
        if not any(item["disposition"] == disposition["id"] and item["to"] == disposition["to"]
                   for item in issue["transitions"]):
            errors.append(f"disposition {disposition['id']}: no transition records it")

    for family in registry["delegation"]["families"]:
        for name in family["actions"]:
            if name not in actions:
                errors.append(f"delegation family {family['id']}: unknown action {name}")
    for name in registry["agent_face"]["writes"]:
        if name not in actions:
            errors.append(f"agent face: unknown action {name}")
    return errors
