#!/usr/bin/env python3
"""从 tkos.world/0.2 登记重生成契约里由登记生成的 13 张表（块、组件、payload、事件词表、动作、处置、各状态表）。

登记 JSON 是唯一来源：改登记后跑这个脚本，契约里的这些表整表替换；表以「所在节的标题 + 表头行」定位，
契约里不加标记。登记没有的只有两类显示短语：状态表「谁记」一列按类型的写法、守卫的短名，放在下面的
映射里；登记出现映射里没有的记录者或守卫时报 KeyError，先补映射。

用法（在仓库根目录）：

    python scripts/render_world_v02_tables.py            # 重写契约里的表
    python scripts/render_world_v02_tables.py --check    # 只核对，有漂移返回 1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contracts" / "tkos-world-0.2.md"
REGISTRY = ROOT / "docs" / "contracts" / "world-registry-0.2.json"

# 状态表「谁记」一列：按类型给记录者（登记 recorders）的写法。gate_role 未列出时取动作的策略角色。
RECORDER_LABELS = {
    "Strategy": {"self": "Strategy 的责任人（CEO）", "designated": "本轮被指定的责任人各记一条", "gate_role": "CEO"},
    "LongTermGoal": {"self": "CEO", "gate_role": "CEO"},
    "PeriodGoal": {"parent": "CEO"},
    "Mission": {"self": "Owner", "self_or_agent": "Owner 或其 Agent", "parent": "DRI"},
    "Task": {"self": "Task 责任人", "parent": "Mission Owner"},
    "Activity": {"self": "Activity 责任人（人或 Agent）", "parent": "Task 责任人"},
    "Issue": {"raiser": "MF（Co-Agent）或主受影响对象主干上的责任人", "router": "路由者（同提出）",
              "route_target": "承接人本人", "owner": "承接人本人", "router_or_owner": "路由者或承接人"},
}
ROLE_LABELS = {"DOMAIN_DRI": "DRI", "OWNER": "Owner"}
GUARD_LABELS = {
    "parent_goal_confirmed": "父周期目标已确认",
    "formation_anchors": "锚定有效长期目标与已确认复盘",
    "round_complete": "本轮补齐",
    "round_incomplete": "本轮未齐",
    "review_of_subject": "快照以本目标为主体",
    "once": "只标一次",
}
FAMILY_LABELS = {"gate": "门", "assign": "指派", "lifecycle": "生命周期", "issue": "议题"}

LIFECYCLE_HEADER = "| 事件（动作） | 起始状态 | 进入状态 | 谁记 | 守卫 |"
# 表名 → 所在节的标题（行首）。同一节里有两张表时按表头行区分。
ANCHORS = {
    "blocks": "### 3.2 ",
    "components": "## 4. ",
    "payloads": "## 7. ",
    "event-kinds": "### 8.2 ",
    "actions": "### 9.1 ",
    "lifecycle-Strategy": "### 10.1 ",
    "lifecycle-LongTermGoal": "### 10.2 ",
    "lifecycle-PeriodGoal": "### 10.3 ",
    "lifecycle-Mission": "### 10.4 ",
    "lifecycle-Task": "### 10.5 ",
    "lifecycle-Activity": "### 10.6 ",
    "lifecycle-Issue": "## 13. ",
    "dispositions": "## 13. ",
}


def _table(header: str, rows: list[str]) -> str:
    return "\n".join([header, "|" + "-|" * header.count(" | ") + "-|", *rows])


def _lifecycle(registry: dict[str, Any], name: str, spec: dict[str, Any]) -> str:
    states = {item["id"]: item["display_name"] for item in spec["states"]}
    actions = {item["action"]: item for item in registry["actions"]}
    values = registry["event_attribute_values"]
    outcomes = {item["id"]: item["display_name"] for item in values["outcome"]}
    dispositions = {item["id"]: item["display_name"] for item in values["disposition"]}
    labels = RECORDER_LABELS[name]
    grouped: list[dict[str, Any]] = []
    index: dict[tuple, int] = {}
    for item in spec["transitions"]:
        same = item["to"] == item["from"]
        key = (item["action"], item["outcome"], item["disposition"], item["by"], item["guard"], same,
               None if same else item["to"])
        if key in index:
            grouped[index[key]]["from"].append(item["from"])
            continue
        index[key] = len(grouped)
        grouped.append({"item": item, "from": [item["from"]], "same": same})
    rows = []
    for group in grouped:
        item = group["item"]
        action = actions[item["action"]]
        name_text = action["display_name"]
        extra = [outcomes[item["outcome"]]] if item["outcome"] else []
        extra += [dispositions[item["disposition"]]] if item["disposition"] else []
        if extra and name_text.endswith("）"):
            label = name_text[:-1] + "，" + "，".join(extra) + "）"
        else:
            label = name_text + (f"（{'，'.join(extra)}）" if extra else "")
        if item["by"] == "gate_role" and "gate_role" not in labels:
            who = "、".join(ROLE_LABELS.get(role, role) for role in action["gate_roles"])
        else:
            who = labels[item["by"]]
        guard = GUARD_LABELS[item["guard"]] if item["guard"] else ""
        to = "不变" if group["same"] else states[item["to"]]
        rows.append(f"| {label} `{item['action']}` | {'、'.join(states[s] for s in group['from'])} | {to} | {who} | "
                    f"{guard} |")
    return _table(LIFECYCLE_HEADER, rows)


def _event_kinds(registry: dict[str, Any]) -> str:
    classes = {item["id"]: item["display_name"] for item in registry["event_classes"]}
    outcomes = {item["id"]: item["display_name"] for item in registry["event_attribute_values"]["outcome"]}
    rows = []
    for kind in registry["event_kinds"]:
        results = "、".join(outcomes[item] for item in kind["outcomes"])
        if kind["outcome_required"]:
            results += "（必带）"
        if kind["disposition"] == "required":
            results = "六类处置之一（必带）"
        if kind["category"] == "required":
            results = "category 必带"
        rows.append(f"| `{kind['kind']}` | {classes[kind['class']]} | {kind['display_name']} | "
                    f"{'是' if kind['backdating'] else '否'} | {results or '—'} | {kind['recorded_by']} |")
    return _table("| kind | 类 | 含义 | 可补记 | 结果 / 处置 | 谁记 |", rows)


def _actions(registry: dict[str, Any]) -> str:
    types = {item["type"]: item["display_name"] for item in registry["objects"]}
    rows = []
    for action in registry["actions"]:
        targets = "、".join(types[item] for item in action["target_types"]) or "—"
        if action["authorization"] == "scope":
            roles = "按 scope"
        elif action["authorization"] == "scope_and_designation":
            roles = "按 scope 与本轮指定"
        else:
            roles = "、".join(action["gate_roles"]) if action["gate_roles"] else "全部 world 角色"
        if action["agent_face"]:
            agent = "是：" + action["agent_note"] if action["agent_note"] else "是"
        else:
            agent = "否"
        rows.append(f"| `{action['action']}` | `{action['event_kind']}` | {targets} | {roles} | "
                    f"{action['recorded_by']} | {agent} | {FAMILY_LABELS.get(action['delegable'], '否')} |")
    return _table("| 动作 | 事件 | 目标 | 策略角色 | 谁记 | Agent 面 | 可代记 |", rows)


def _dispositions(registry: dict[str, Any]) -> str:
    states = {item["id"]: item["display_name"] for item in registry["issue"]["lifecycle"]["states"]}
    rows = [f"| `{item['id']}` | {item['display_name']} | {states[item['to']]} | {item['aliases']['M1-A'] or '—'} | "
            f"{item['aliases']['M3'] or '—'} |" for item in registry["issue"]["dispositions"]]
    return _table("| 处置 | 含义 | 进入状态 | M1-A 叫法 | M3 叫法 |", rows)


def _blocks(registry: dict[str, Any]) -> str:
    rows = []
    for item in registry["objects"]:
        if item["category"] != "business_object":
            continue
        cells: dict[str, list[str]] = {"formal": [], "activity": []}
        for block in item["blocks"]:
            cells[block["class"]].append(f"{block['id']} {block['display_name']}")
        name = item["type"] + ("（候选）" if item["candidate"] else "")
        rows.append(f"| {name} | {'、'.join(cells['formal'])} | {'、'.join(cells['activity']) or '—'} |")
    return _table("| 类型 | 正式块 | 活动块 |", rows)


def _components(registry: dict[str, Any]) -> str:
    component_types = registry["components"]["types"]
    where: dict[str, list[str]] = {item["id"]: [] for item in component_types}
    for owner, blocks in ([(item["type"], item["blocks"]) for item in registry["objects"]]
                          + [(item["id"], item["blocks"]) for item in registry["state"]["payload_types"]]):
        for block in blocks:
            for component in block["components"]:
                where[component].append(f"{owner}.{block['id']}")
    rows = []
    for item in component_types:
        attributes = "、".join(f"`{attr['id']}` {attr['display_name']}" + ("（必填）" if attr["required"] else "")
                               for attr in item["attributes"]) or "—"
        rows.append(f"| `{item['id']}` | {item['display_name']} | {'、'.join(dict.fromkeys(where[item['id']]))} | "
                    f"{attributes} | {item['source']} |")
    return _table("| 组件类型 | 中文名 | 允许的块 | 类型属性 | 来源 |", rows)


def _payloads(registry: dict[str, Any]) -> str:
    names = {item["id"]: item["display_name"] + ("" if item["display_name"].endswith("条目") else "组件")
             for item in registry["components"]["types"]}
    rows = []
    for payload in registry["state"]["payload_types"]:
        blocks = "、".join(f"{block['id']} {block['display_name']}"
                          + (f"（{'、'.join(names[c] for c in block['components'])}）" if block["components"] else "")
                          for block in payload["blocks"])
        rows.append(f"| `{payload['id']}` {payload['display_name']} | {'、'.join(payload['subjects'])} | {blocks} |")
    return _table("| payload 类型 | 主体 | 块（括号里是允许的组件） |", rows)


def render(registry: dict[str, Any]) -> dict[str, str]:
    """表名 → Markdown 表（不带结尾换行）。"""
    rendered = {
        "blocks": _blocks(registry),
        "components": _components(registry),
        "payloads": _payloads(registry),
        "event-kinds": _event_kinds(registry),
        "actions": _actions(registry),
    }
    for name, spec in registry["lifecycles"].items():
        rendered[f"lifecycle-{name}"] = _lifecycle(registry, name, spec)
    rendered["lifecycle-Issue"] = _lifecycle(registry, "Issue", registry["issue"]["lifecycle"])
    rendered["dispositions"] = _dispositions(registry)
    return rendered


def sync(contract: str, registry: dict[str, Any]) -> str:
    """返回把契约里由登记生成的表换成当前生成结果后的全文；找不到表时报 LookupError。"""
    lines = contract.split("\n")
    for name, table in render(registry).items():
        anchor, header = ANCHORS[name], table.split("\n", 1)[0]
        heading = next((i for i, line in enumerate(lines) if line.startswith(anchor)), None)
        if heading is None:
            raise LookupError(f"{name}: no heading starting with {anchor!r}")
        start = next((i for i in range(heading + 1, len(lines)) if lines[i] == header), None)
        if start is None:
            raise LookupError(f"{name}: no table {header!r} after {anchor!r}")
        end = start
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        lines[start:end] = table.split("\n")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    parser.add_argument("--check", action="store_true", help="只核对，契约里的表与登记不一致时返回 1")
    args = parser.parse_args(argv)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    contract = args.contract.read_text(encoding="utf-8")
    synced = sync(contract, registry)
    if synced == contract:
        print("ok: contract tables match the registry")
        return 0
    if args.check:
        print("drift: regenerate with python scripts/render_world_v02_tables.py", file=sys.stderr)
        return 1
    args.contract.write_text(synced, encoding="utf-8")
    print(f"rewrote the registry tables in {args.contract}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
