"""对照实验 B 试用回放的转写（票 #75）：把真实 scope 里一场 Mission 的记录转写成两条线共用的播种内容（lines）与执行脚本，
另出一份给 E&O DRI 审的审阅稿。做法按 docs/world-v02-experiment-b.md 第 8 节。

    转写  python -m experiments.world_v02.b_transcribe transcribe <base_url> --token-file F --mission <object_id> \\
              --principals T --private P --output O
    重写  python -m experiments.world_v02.b_transcribe write <P>/bundle.json --principals T --output O   （不连服务）

只读：读取只有 GET（取对象、取事件、列对象），不写真实 scope 的任何东西；凭证只从 --token-file 读，不打印。读到的原样
（含显示名）存进 --private 下的 bundle.json（仅本人可读），之后可以不连服务重写。

对照表（--principals，私有、不进仓库）：真实 scope 的 ids.json 的 principals 照抄，每个主体另加 role——人是 b_spec.json
的角色键（ceo、eo-dri、eo-owner、eo-ic），Agent 写 agent（按写入转写）或钉成 exec-agent、eo-coagent。

    {"format": "tkos-world-02-experiment-b-principals/0.1",
     "principals": {"<任意键>": {"principal_id": "…", "display_name": "…", "role": "eo-dri"}, …}}

输出（--output）只有角色键：b-lines.json（lines 0.2：b_lines.json 的主干，加转写出的门与建 Task、Task 的责任人与段）、
b-script.json（执行脚本 0.2）、b-review.md（审阅稿）。对照表里任何显示名（以及真实 scope 给这些主体的显示名）出现在
任一输出里，就报出现在哪（文件、步骤、字段、是谁的名字——只写角色键）并以 1 退出，什么也不写。

转写规则（第 8 节，细节见 docs/world-v02-experiment-b.md）：
- 行动者：代记的写入按被代记的人；人以自己身份的写入按对照表；Agent 以自己身份的写入按写入转写——提出、路由与退回
  形成问题是 eo-coagent（Co-Agent），其余是 exec-agent（执行）。
- 共同播种（b-lines.json）：周期目标与这条 Mission 的形成门（到第一次确认接受为止，按记录顺序，退回也照记）；每个
  Task 的建立（正文取第 1 版的任务定义、Task 计划等块，不带 Activity 全景块与外部引用，块内引用只留指回主干的）；Task
  的首次指派定它的责任人；Task 建立时（或首次指派之前）计划块里的计划条目是它的初始段，连同计划条目写了的预期产出、
  质量标准、执行主体（是某个主体的显示名时换成角色键）与分工。从没指派过的 Task 与 Activity 不进回放。
- 执行脚本（b-script.json，按记录顺序）：Mission、Task 与段（Activity）的生命周期；Task 的再指派；Task 指派之后新加的
  计划条目（plan，带它的四个可选属性）与改了责任人的计划条目（assign），这两种由 Task 当时的责任人记；Activity 的
  首次指派即划段（plan：instruction 块的文字与执行事项是段的正文，预期产出、成功 / 验收标准是段的预期产出、质量标准），
  之后的指派是 assign；快照里的进展条目按组件 id 对上段（计划条目 id、Activity 的外部引用）或 Task（外部引用）的写成
  那一处的 progress，对不上的与块里的文字合成快照主体上的一步（有对不上的进展条目为 progress，否则 refresh），时点取
  快照的真实 as_of，同一张快照拆出的第 i 步加 i 微秒（同一主体同一时点只能有一条快照）；只带问题、问题都提出了的快照
  并入提出；问题的提出（时点取提出事件的发生时刻）、路由、承接、处置与退回形成，问题键取原问题组件的 id。
- 不转写、只在审阅稿里列出：外部事件（会议等；作快照来源的并入快照）、Mission 与 Activity 的修订、Task 除计划条目以外
  的修订、计划条目改文字或改四个可选属性、Mission 的再指派、形成之后的门（一轮重走）与再确认、关注标记、建立跨链关系、
  更正；撤回的事件连同撤回本身一起略去。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import b_drive, b_seed
from .b_http import TransportError, utc_text

BUNDLE_FORMAT = "tkos-world-02-experiment-b-bundle/0.1"
TABLE_FORMAT = "tkos-world-02-experiment-b-principals/0.1"
HUMAN_ROLES = ("ceo", "eo-dri", "eo-owner", "eo-ic")
AGENT_ROLES = ("agent", "exec-agent", "eo-coagent")  # agent：按写入转写
COAGENT_KINDS = ("issue.raised", "issue.routed", "issue.returned")  # Agent 自己记这几种是 Co-Agent 的事，其余是执行
LIFECYCLE_KINDS = ("start", "deliver", "accept", "reject", "reopen", "cancel")
ISSUE_DO = {"issue.raised": "raise_issue", "issue.routed": "route_issue", "issue.owned": "own_issue",
            "issue.disposed": "dispose_issue", "issue.returned": "return_issue"}
OUTPUTS = {"lines": "b-lines.json", "script": "b-script.json", "review": "b-review.md"}
NO_REASON = "（真实记录没有写理由）"
REASONS = {
    "object.revised": "修订不在执行脚本的动词里（Task 只转写计划条目）",
    "event.recorded": "外部事件（会议、交付类、验收类记录等）不在执行脚本的动词里",
    "assign": "Mission 的再指派不在执行脚本的动词里",
    "reconfirm": "再确认不转写",
    "core_battle.marked": "关注标记不转写",
    "relate": "建立跨链关系不转写",
}
ATTRIBUTE_NAMES = {"expected_output": "预期产出", "quality_standard": "质量标准", "executor": "执行主体",
                   "division": "分工"}
# Activity 的 instruction 块里对得上段（计划条目）的组件：执行事项是段的正文，预期产出、成功 / 验收标准是段的预期产出、质量
# 标准；其余组件（贡献、时间边界、执行边界、执行约束）计划条目没有对应属性，回放不带，审阅稿提醒。
INSTRUCTION_FIELDS = {"work_definition": "text", "expected_output": "expected_output",
                      "acceptance_criterion": "quality_standard"}
GATE_NAMES = {"world_commit_period_goal": "承诺周期目标", "world_confirm_period_goal": "确认周期目标",
              "world_commit_mission": "承诺 Mission", "world_confirm_mission": "确认 Mission"}
OUTCOMES = {"accepted": "接受", "returned": "退回", None: "—"}


class TranscribeError(ValueError):
    """转写不成立（对照表缺主体、真实记录与主干对不上、输出里出现显示名……）：列出全部问题，什么也不写。"""


# ------------------------------------------------------------------ reading (HTTP, GET only)
class Reader:
    """真实 scope 的只读访问：只有 GET，凭证只从文件读、不打印。"""

    def __init__(self, base_url: str, token_file: Path):
        self.base = base_url.rstrip("/")
        self._token = Path(token_file).read_text(encoding="utf-8").strip()
        if not self._token:
            raise ValueError(f"{token_file} 是空的")

    def get(self, path: str):
        request = urllib.request.Request(self.base + path, method="GET")
        request.add_header("Authorization", "Bearer " + self._token)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise TransportError(f"读 {path.split('?')[0]}：{exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise TransportError(f"连不上 {self.base}（{type(exc).__name__}）") from None

    def listed(self, object_type: str) -> list[str]:
        """列对象：本 scope 这一类型的全部对象 id（按建立时刻），逐页取。"""
        ids, cursor = [], None
        while True:
            query = {"type": object_type, "limit": 100, **({"cursor": cursor} if cursor else {})}
            page = self.get("/v1/world/objects?" + urllib.parse.urlencode(query))
            ids += [item["object_id"] for item in page["items"]]
            cursor = page["next_cursor"]
            if not cursor:
                return ids


def relation(view: dict, field: str) -> str | None:
    value = {item["field"]: item["value"] for item in view["business"]["relations"]}.get(field)
    return value and value["object_id"]


def fetch(reader: Reader, mission_id: str) -> dict:
    """读出这场 Mission 的记录：Mission 与它的周期目标、挂在它下面的 Task 与 Task 下的 Activity（列对象再按 parent_ref
    筛）、这些对象的全部事件；Task 与 Activity 另取每个修订；事件里出现的快照（状态刷新的快照、问题所在的快照）逐个取。"""
    mission = reader.get(f"/v1/world/objects/{mission_id}")
    if mission["business"]["object_type"] != "Mission":
        raise ValueError(f"{mission_id} 不是 Mission")
    goal_id = relation(mission, "goal_ref")
    objects = {mission_id: {"type": "Mission", "latest": mission, "versions": {}}}
    if goal_id:
        objects[goal_id] = {"type": "PeriodGoal", "latest": reader.get(f"/v1/world/objects/{goal_id}"), "versions": {}}
    parents = {mission_id}
    for object_type in ("Task", "Activity"):
        for object_id in reader.listed(object_type):
            view = reader.get(f"/v1/world/objects/{object_id}")
            if relation(view, "parent_ref") in parents:
                objects[object_id] = {"type": object_type, "latest": view, "versions": {
                    str(n): reader.get(f"/v1/world/objects/{object_id}?version={n}")
                    for n in range(1, view["business"]["version"] + 1)}}
        parents = {object_id for object_id, item in objects.items() if item["type"] == object_type}
    events = {object_id: reader.get(f"/v1/world/objects/{object_id}/events")["events"] for object_id in objects}
    snapshots = {}
    for rows in events.values():
        for event in rows:
            if event["kind"] == "state.refreshed" or event["kind"].startswith("issue."):
                snapshot_id = event["subject_refs"][0]["object_id"]
                if snapshot_id not in snapshots:
                    snapshots[snapshot_id] = reader.get(f"/v1/world/objects/{snapshot_id}")
    return {"format": BUNDLE_FORMAT, "read_at": utc_text(datetime.now(timezone.utc).isoformat()),
            "mission_id": mission_id, "goal_id": goal_id, "objects": objects, "events": events, "snapshots": snapshots}


def private_json(path: Path, value) -> None:
    """仅本人可读的 JSON（目录 0700、文件 0600）。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600)
    os.chmod(path, 0o600)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ transcription (pure)
def load_table(table: dict) -> dict[str, dict]:
    """对照表 → 主体 id → {key, role, display_name}；格式或角色不对就抛 TranscribeError。"""
    if not isinstance(table, dict) or table.get("format") != TABLE_FORMAT:
        raise TranscribeError(f"对照表的 format 是 {TABLE_FORMAT}")
    rows, problems = {}, []
    for key, item in (table.get("principals") or {}).items():
        item = item if isinstance(item, dict) else {}
        if item.get("role") not in HUMAN_ROLES + AGENT_ROLES:
            problems.append(f"对照表 {key} 的 role 是 {'、'.join(HUMAN_ROLES + AGENT_ROLES)} 之一")
        if not isinstance(item.get("principal_id"), str) or item["principal_id"] in rows:
            problems.append(f"对照表 {key} 的 principal_id 缺了或重复")
            continue
        rows[item["principal_id"]] = {"key": key, "role": item.get("role"), "display_name": item.get("display_name") or ""}
    if not rows:
        problems.append("对照表是空的")
    if problems:
        raise TranscribeError("；".join(problems))
    return rows


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def shifted(as_of: str, micro: int) -> str:
    """同一张快照拆出的第 micro 步：时点加 micro 微秒。"""
    return utc_text((moment(as_of) + timedelta(microseconds=micro)).isoformat())


def value_of(view: dict, block: str) -> dict:
    """一个块的值（空块给空值）；view 是取对象的 business 组或快照的读回。"""
    found = next((item for item in view["blocks"] if item["id"] == block), None)
    return (found and found["value"]) or {"text": "", "components": [], "refs": [], "artifacts": []}


def one_line(text: str) -> str:
    return " ".join((text or "").split())


def item_text(item: dict) -> str:
    """一条进展条目的文字：正文、外部状态与本期条目（不带写入时的姓名与主体 id）。"""
    attributes = item.get("attributes") or {}
    parts = [item["text"] or item["id"]]
    if attributes.get("external_status"):
        parts.append(f"外部状态：{attributes['external_status']}")
    parts += [f"· {entry['text']}" for entry in attributes.get("entries") or [] if entry.get("text")]
    return "\n".join(parts)


def external_ids(view: dict) -> set[str]:
    return {item["id"] for item in view["business"]["attributes"].get("external_refs") or []}


class Transcription:
    """一次转写：读取原样 + 对照表 + 主干 → lines、脚本、审阅稿与未转写清单。问题都攒进 problems，最后一起报。"""

    def __init__(self, bundle: dict, table: dict, spine: dict, source: dict):
        if bundle.get("format") != BUNDLE_FORMAT:
            raise TranscribeError(f"读取原样的 format 是 {BUNDLE_FORMAT}")
        self.bundle, self.people, self.spine = bundle, load_table(table), spine
        self.problems, self.warnings, self.skipped = [], [], []
        plan = {step["key"]: step for step in source["steps"]}
        self.mission_key = spine["source"]["mission"]
        self.goal_key = plan[self.mission_key]["payload"]["goal_ref"].lstrip("@")
        self.spine_payload = {key: plan[key]["payload"] for key in (self.mission_key, self.goal_key)}
        self.owner = next(step["to"] for step in plan.values() if step["key"] in spine["source"]["steps"]
                          and step["do"] == "assign" and step["target"] == self.mission_key)
        self.mission, self.goal = bundle["mission_id"], bundle["goal_id"]
        self.events = sorted({event["event_id"]: event for rows in bundle["events"].values() for event in rows}.values(),
                             key=lambda event: (moment(event["recorded_at"]), event["event_id"]))
        created = {event["subject_refs"][0]["object_id"]: event["recorded_at"] for event in self.events
                   if event["kind"] == "object.created"}

        def ordered(object_type):
            return sorted((object_id for object_id, item in bundle["objects"].items() if item["type"] == object_type),
                          key=lambda object_id: (created.get(object_id, ""), object_id))
        self.task_ids, self.activity_ids = ordered("Task"), ordered("Activity")
        self.task_key = {object_id: f"task_{n}" for n, object_id in enumerate(self.task_ids, 1)}
        self.parent = {object_id: relation(bundle["objects"][object_id]["latest"], "parent_ref")
                       for object_id in self.task_ids + self.activity_ids}
        # 撤回：原事件与撤回一起略去
        self.withdrawn = set()
        for event in self.events:
            if event["outcome"] == "withdrawn" and event["supersedes_event_id"]:
                self.withdrawn |= {event["event_id"], event["supersedes_event_id"]}
        assigned = {event["subject_refs"][0]["object_id"] for event in self.events
                    if event["kind"] == "assign" and event["event_id"] not in self.withdrawn}
        self.left_out = {object_id for object_id in self.task_ids if object_id not in assigned}
        self.left_out |= {object_id for object_id in self.activity_ids
                          if object_id not in assigned or self.parent[object_id] in self.left_out}
        self.ours = {self.mission, *self.task_ids, *self.activity_ids} - self.left_out
        self.folded = {ref["event_id"] for view in bundle["snapshots"].values() for ref in view["source_event_refs"]}
        # 转写状态
        self.extra, self.extra_source, self.steps, self.sources = [], {}, [], []
        self.tasks: dict[str, dict] = {}        # Task id → {task, by, responsible, assign_event}
        self.holder: dict[str, str] = {}        # Task id → 当时的责任人（角色键）
        self.segments: dict[str, dict] = {}     # 段键 → {task, title, text, responsible, origin, initial}
        self.segment_of: dict[str, str] = {}    # Activity id → 段键
        self.item_segment: dict[tuple[str, str], str] = {}  # (Task id, 计划条目 id) → 段键
        self.issue_key: dict[tuple[str, str], str] = {}
        self.formed: set[str] = set()
        self.mission_assigned = False
        self.objects = {self.mission: "mission"}
        self.names = display_names(self)        # 显示名 → 角色键（计划条目的执行主体按它换）

    # ---------------------------------------------------------- principals
    def entry(self, principal_id: str | None, where: str) -> dict | None:
        found = self.people.get(principal_id)
        if found is None:
            self.problems.append(f"对照表里没有主体 {principal_id}（{where}）")
        return found

    def actor(self, event: dict) -> str:
        """这条事件在脚本里的行动者（角色键）：代记按被代记的人；人按对照表；Agent 按写入转写或对照表钉的角色键。"""
        where = f"event:{event['event_id']}，{event['kind']}"
        if event["on_behalf_of"]:
            found = self.entry(event["on_behalf_of"]["principal_id"], where)
            if found and found["role"] not in HUMAN_ROLES:
                self.problems.append(f"代记的只能是人：{where} 被代记的主体在对照表里的 role 是 {found['role']}")
            return found["role"] if found else "?"
        principal = event["principal"]
        found = self.entry(principal["principal_id"], where)
        if found is None:
            return "?"
        human = principal["principal_type"] == "human"
        if human != (found["role"] in HUMAN_ROLES):
            self.problems.append(f"主体 {principal['principal_id']} 在事件里是{'人' if human else ' Agent'}，"
                                 f"对照表给的 role 是 {found['role']}（{where}）")
        if found["role"] == "agent":
            return "eo-coagent" if event["kind"] in COAGENT_KINDS else "exec-agent"
        return found["role"]

    def assignee(self, principal_id: str | None, where: str) -> str:
        """被指派者、计划条目的责任人、承接人（角色键）；按写入转写的 Agent 当执行 Agent。"""
        found = self.entry(principal_id, where)
        if found is None:
            return "?"
        return "exec-agent" if found["role"] == "agent" else found["role"]

    # ---------------------------------------------------------- bookkeeping
    def about(self, object_id: str) -> str:
        if object_id == self.goal:
            return self.goal_key
        return self.objects.get(object_id) or self.task_key.get(object_id) or object_id

    def skip(self, event: dict, object_id: str, reason: str) -> None:
        self.skipped.append({"event_id": event["event_id"], "kind": event["kind"], "object": self.about(object_id),
                             "reason": reason, "delegated": bool(event["on_behalf_of"])})

    def target(self, object_id: str) -> dict:
        if object_id == self.mission:
            return {"mission": True}
        if object_id in self.task_key:
            return {"task": self.task_key[object_id]}
        return {"segment": self.segment_of[object_id]}

    def step(self, event: dict, do: str, by: str, where: dict, detail: str = "", **fields) -> None:
        self.steps.append({"do": do, "by": by, **where,
                           **{key: value for key, value in fields.items() if value is not None},
                           "note": f"event:{event['event_id']}"})
        self.sources.append({"event_id": event["event_id"], "delegated": bool(event["on_behalf_of"]),
                             "agent": event["principal"]["principal_type"] == "agent", "detail": detail})

    def add_extra(self, step: dict, event: dict) -> None:
        self.extra.append(step)
        self.extra_source[step["key"]] = {"event_id": event["event_id"], "delegated": bool(event["on_behalf_of"])}

    def version(self, object_id: str, number: int) -> dict:
        return self.bundle["objects"][object_id]["versions"][str(number)]["business"]

    def plan_items(self, object_id: str, number: int) -> list[dict]:
        return [item for item in value_of(self.version(object_id, number), "plan")["components"]
                if item["type"] == "plan_item"]

    def new_segment(self, task_id: str, title: str, text: str, responsible: str, origin: str, initial: bool,
                    attributes: dict | None = None) -> str:
        task = self.task_key[task_id]
        key = f"{task}.s{sum(1 for item in self.segments.values() if item['task'] == task) + 1}"
        self.segments[key] = {"task": task, "title": one_line(title or text)[:60], "text": text.strip() or title,
                              "responsible": responsible, "origin": origin, "initial": initial,
                              "attributes": attributes or {}}
        return key

    def plan_attributes(self, attributes: dict | None) -> dict:
        """计划条目写了的四个可选属性（b_drive.PLAN_ATTRIBUTES）：预期产出、质量标准与分工照抄；执行主体正是某个主体的
        显示名时换成它的角色键（按写入转写的 Agent 当 exec-agent），不是就照抄（输出里的显示名仍由拦截兜底）。"""
        out = {}
        for field in b_drive.PLAN_ATTRIBUTES:
            value = (attributes or {}).get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            if field == "executor" and value.strip() in self.names:
                value = "exec-agent" if self.names[value.strip()] == "agent" else self.names[value.strip()]
            out[field] = value
        return out

    def instruction(self, view: dict, where: str) -> tuple[str, dict]:
        """Activity 的 instruction 块 → 段的正文与属性：块的文字加执行事项组件是正文，预期产出、成功 / 验收标准组件是
        预期产出、质量标准（同类几条按行合并）；别的组件回放不带，记一条提醒。"""
        value = value_of(view, "instruction")
        found: dict[str, list[str]] = {}
        for item in value["components"]:
            if item["type"] in INSTRUCTION_FIELDS:
                found.setdefault(INSTRUCTION_FIELDS[item["type"]], []).extend([item["text"]] if item["text"] else [])
        others = sorted({item["type"] for item in value["components"] if item["type"] not in INSTRUCTION_FIELDS})
        if others:
            self.warnings.append(f"{where} 的 instruction 里 {'、'.join(others)} 回放不带（计划条目没有对应属性）")
        text = "\n".join([*([value["text"]] if value["text"] else []), *found.pop("text", [])])
        return text, {field: "\n".join(texts) for field, texts in found.items() if texts}

    def trunk_ref(self, pinned: dict, where: str) -> str | None:
        """块内引用：指回这条 Mission 或它的周期目标的换成主干的占位（组件要在主干的正文里）；别的不带，记一条提醒。"""
        key = {self.mission: self.mission_key, self.goal: self.goal_key}.get(pinned["object_id"])
        block, component = pinned.get("block"), pinned.get("component")
        if key and component is not None:
            components = (self.spine_payload[key]["blocks"].get(block) or {}).get("components", [])
            key = key if component in [item["id"] for item in components] else None
        if key is None:
            self.warnings.append(f"{where} 的引用 {pinned['ref']} 指向主干以外（或主干里没有的组件），回放不带")
            return None
        return f"@{key}" + (f"#{block}" if block else "") + (f"/{component}" if component else "")

    def task_payload(self, task_id: str) -> dict:
        """建 Task 的正文：第 1 版的标题与块（任务定义、Task 计划），不带 Activity 全景块 plan（段另记）与外部引用，
        组件只留 id、类型、文字与指回主干的引用。"""
        first = self.version(task_id, 1)
        payload = {"title": first["title"], "parent_ref": f"@{self.mission_key}", "blocks": {}}
        for block in first["blocks"]:
            if block["id"] == "plan" or block["value"] is None:
                continue
            value, out, components = block["value"], {}, []
            for item in value["components"]:
                component = {"id": item["id"], "type": item["type"], **({"text": item["text"]} if item["text"] else {})}
                refs = [self.trunk_ref(ref, f"{self.task_key[task_id]} 的 {block['id']}/{item['id']}")
                        for ref in item["refs"]]
                if any(refs):
                    component["refs"] = [ref for ref in refs if ref]
                components.append(component)
            out.update({"text": value["text"]} if value["text"] else {})
            out.update({"components": components} if components else {})
            out.update({"artifacts": value["artifacts"]} if value["artifacts"] else {})
            if out:
                payload["blocks"][block["id"]] = out
        return payload

    # ---------------------------------------------------------- the events, in record order
    def run(self) -> None:
        for event in self.events:
            subjects = [item["object_id"] for item in event["subject_refs"]]
            kind = event["kind"]
            main = subjects[1] if kind == "state.refreshed" or kind in ISSUE_DO else subjects[0]
            if event["event_id"] in self.withdrawn:
                self.skip(event, main, "撤回：原事件与撤回一起略去")
            elif kind in ("commit", "confirm") and main in (self.goal, self.mission):
                self.gate(event, main)
            elif not set(subjects) & (self.ours | self.left_out):
                continue  # 周期目标自己的其余记录不属于这场 Mission
            elif main in self.left_out:
                self.skip(event, main, "Task 或 Activity 从没指派过，不进回放")
            elif kind == "state.refreshed":
                self.snapshot(event)
            elif kind in ISSUE_DO:
                self.issue(event)
            elif kind == "object.created":
                self.created(event, main)
            elif kind == "assign":
                self.assigned(event, main)
            elif kind == "object.revised" and main in self.task_key:
                self.task_revised(event, main)
            elif kind in LIFECYCLE_KINDS:
                self.lifecycle(event, main)
            elif kind == "event.recorded" and event["event_id"] in self.folded and event["category"] != "correction":
                continue  # 快照的来源事件：并入那一步（驱动器会另记一条）
            else:
                self.skip(event, next((item for item in subjects if item in self.ours), main),
                          REASONS.get(kind, "不在执行脚本的动词里"))
        if not self.mission_assigned:
            self.problems.append("真实记录里没有这条 Mission 指派 Owner 的事件")

    def gate(self, event: dict, object_id: str) -> None:
        """形成门（到第一次确认接受为止）进共同播种；之后的（一轮重走）不转写。"""
        key = self.goal_key if object_id == self.goal else self.mission_key
        if object_id in self.formed:
            self.skip(event, object_id, "形成之后的门（一轮重走）不转写")
            return
        step = {"key": f"gate_{sum(1 for item in self.extra if item['do'] == 'gate') + 1}", "by": self.actor(event),
                "do": "gate", "after": [], "action": event["action"], "target": key}
        if event["kind"] == "confirm":
            step["outcome"] = event["outcome"]
            if event["outcome"] == "accepted":
                self.formed.add(object_id)
        self.add_extra(step, event)

    def created(self, event: dict, object_id: str) -> None:
        """建 Task 进共同播种，建立时计划块里的计划条目是初始段；Mission 在主干里，Activity 在首次指派时划段。"""
        if object_id not in self.task_key:
            return
        self.add_extra({"key": self.task_key[object_id], "by": self.actor(event), "do": "create", "after": [],
                        "type": "Task", "domain": "eo", "payload": self.task_payload(object_id)}, event)
        self.tasks[object_id] = {"task": self.task_key[object_id], "responsible": None, "assign_event": None}
        for item in self.plan_items(object_id, 1):
            self.initial_item(object_id, item, "建 Task 时计划块里的")

    def initial_item(self, task_id: str, item: dict, how: str) -> None:
        responsible = (item.get("attributes") or {}).get("responsible")
        key = self.new_segment(task_id, "", item["text"] or item["id"],
                               self.assignee(responsible, f"{self.task_key[task_id]} 的计划条目 {item['id']}")
                               if responsible else "?", f"{how}计划条目 `{item['id']}`", True,
                               self.plan_attributes(item.get("attributes")))
        self.item_segment[(task_id, item["id"])] = key

    def assigned(self, event: dict, object_id: str) -> None:
        to = self.assignee(event["detail"]["principal_id"], f"event:{event['event_id']}，指派")
        by = self.actor(event)
        if object_id == self.mission:
            if self.mission_assigned:
                self.skip(event, object_id, REASONS["assign"])
            elif to != self.owner:
                self.problems.append(f"真实记录里这条 Mission 的 Owner 是 {to}，主干（b_lines.json）指派的是 {self.owner}")
            self.mission_assigned = True
        elif object_id in self.task_key:
            task = self.tasks[object_id]
            if task["responsible"] is None:
                task.update(responsible=to, assign_event=event["event_id"])
                for key, item in self.segments.items():  # 没写责任人的初始段交给 Task 的责任人
                    if item["task"] == task["task"] and item["responsible"] == "?":
                        item["responsible"] = to
                if by != self.owner:
                    self.warnings.append(f"{task['task']} 的首次指派由 {by} 记（event:{event['event_id']}），"
                                         f"回放按共同播种由 Mission Owner {self.owner} 记")
            else:
                self.step(event, "assign", by, {"task": task["task"]}, to=to)
            self.holder[object_id] = to
        elif object_id not in self.segment_of:  # Activity 的首次指派即划段
            view = self.version(object_id, event["subject_refs"][0]["object_version"])
            text, attributes = self.instruction(view, f"Activity `{object_id}`")
            key = self.new_segment(self.parent[object_id], view["title"], text or view["title"], to,
                                   f"Activity `{object_id}`", False, attributes)
            self.segment_of[object_id] = key
            self.objects[object_id] = f"segment:{key}"
            self.step(event, "plan", by, {"segment": key}, task=self.task_key[self.parent[object_id]],
                      title=self.segments[key]["title"], text=self.segments[key]["text"], to=to, **attributes)
        else:
            self.step(event, "assign", by, {"segment": self.segment_of[object_id]}, to=to)

    def task_revised(self, event: dict, task_id: str) -> None:
        """Task 的修订：计划块里新加的计划条目是新的一段（带它写了的四个可选属性），改了责任人的是再指派，都由 Task
        当时的责任人记（首次指派之前的修订改的是初始划分）。其余改动（文字、四个可选属性、删掉的条目、别的块与属性）
        不转写。"""
        number = event["subject_refs"][0]["object_version"]
        before = {item["id"]: item for item in self.plan_items(task_id, number - 1)}
        after, rest, holder = self.plan_items(task_id, number), [], self.holder.get(task_id)
        for item in after:
            old, named = before.get(item["id"]), (item.get("attributes") or {}).get("responsible")
            responsible = (self.assignee(named, f"{self.task_key[task_id]} 的计划条目 {item['id']}") if named
                           else holder or "?")
            if old is None and holder is None:
                self.initial_item(task_id, item, "首次指派之前计划块里加的")
            elif old is None:
                attributes = self.plan_attributes(item.get("attributes"))
                key = self.new_segment(task_id, "", item["text"] or item["id"], responsible,
                                       f"计划块新加的计划条目 `{item['id']}`", False, attributes)
                self.item_segment[(task_id, item["id"])] = key
                self.step(event, "plan", holder, {"segment": key}, "计划块新加的计划条目", task=self.task_key[task_id],
                          title=self.segments[key]["title"], text=self.segments[key]["text"], to=responsible,
                          **attributes)
            elif (old.get("attributes") or {}).get("responsible") != named:
                key = self.item_segment[(task_id, item["id"])]
                if holder is None:
                    self.segments[key]["responsible"] = responsible
                else:
                    self.step(event, "assign", holder, {"segment": key}, "计划条目改了责任人", to=responsible)
            elif old["text"] != item["text"]:
                rest.append(f"计划条目 {item['id']} 改了文字")
            changed = [ATTRIBUTE_NAMES[field] for field in b_drive.PLAN_ATTRIBUTES if old is not None
                       and (old.get("attributes") or {}).get(field) != (item.get("attributes") or {}).get(field)]
            if changed:
                rest.append(f"计划条目 {item['id']} 改了{'、'.join(changed)}")
        kept = {item["id"] for item in after}
        rest += [f"删掉了计划条目 {item_id}" for item_id in before if item_id not in kept]
        previous, current = self.version(task_id, number - 1), self.version(task_id, number)

        def content(view):
            """标题、属性与计划块以外各块的内容（组件自己的引用带版本号，不算）。"""
            return (view["title"], view["attributes"], [
                (block["id"], block["value"] and {**block["value"], "components": [
                    {key: value for key, value in item.items() if key != "ref"}
                    for item in block["value"]["components"]]})
                for block in view["blocks"] if block["id"] != "plan"])
        if content(current) != content(previous):
            rest.append("计划块以外的改动")
        if rest:
            self.skip(event, task_id, "；".join(rest) + "：不在执行脚本的动词里")

    def lifecycle(self, event: dict, object_id: str) -> None:
        text = (event["content"] or {}).get("text") or None
        if event["kind"] == "reject" and not text:
            text = NO_REASON
            self.warnings.append(f"退回 event:{event['event_id']} 没有写理由，脚本写「{NO_REASON}」")
        self.step(event, event["kind"], self.actor(event), self.target(object_id), text=text)

    def snapshot(self, event: dict) -> None:
        """快照：进展条目按组件 id 对上段或 Task 的，各写成那一处的 progress；对不上的与块里的文字（进展、阻碍、没提出的
        问题）合成快照主体上的一步。时点取真实 as_of，同一张快照拆出的第 i 步加 i 微秒。"""
        snapshot_id, subject = (item["object_id"] for item in event["subject_refs"][:2])
        view, by = self.bundle["snapshots"][snapshot_id], self.actor(event)
        progress, count, rest, unmatched = value_of(view, "progress"), 0, [], False
        raised = {item["subject_refs"][0]["component"] for item in self.events  # 在哪张快照上提出的都算
                  if item["kind"] == "issue.raised" and item["subject_refs"][1]["object_id"] == subject}
        if progress["text"]:
            rest.append(progress["text"])
        for item in progress["components"]:
            where = self.match(item["id"]) if item["type"] == "progress_item" else None
            if where is None:
                rest.append(item_text(item))
                unmatched = True
                continue
            self.step(event, "progress", by, where, f"快照 `{snapshot_id}` 的进展条目 `{item['id']}`",
                      text=item_text(item), as_of=shifted(view["as_of"], count))
            count += 1
        blockers = value_of(view, "blockers")
        rest += [f"阻碍：{text}" for text in [blockers["text"], *(item["text"] for item in blockers["components"])] if text]
        rest += [f"问题（没有提出）：{item['text'] or (item.get('attributes') or {}).get('core_question')}"
                 for item in value_of(view, "issues")["components"] if item["id"] not in raised]
        if rest:
            self.step(event, "progress" if unmatched else "refresh", by, self.target(subject),
                      f"快照 `{snapshot_id}`", text="\n".join(rest), as_of=shifted(view["as_of"], count))

    def match(self, item_id: str) -> dict | None:
        """进展条目的组件 id 对上哪一处：段（计划条目 id、Activity 的外部引用）优先，其次 Task 的外部引用；对上不止一处
        时不拆，并入快照主体那一步。"""
        found = {key for (_, component), key in self.item_segment.items() if component == item_id}
        found |= {key for object_id, key in self.segment_of.items()
                  if item_id in external_ids(self.bundle["objects"][object_id]["latest"])}
        kind = "segment"
        if not found:
            kind = "task"
            found = {self.task_key[object_id] for object_id in self.tasks
                     if object_id in self.ours and item_id in external_ids(self.bundle["objects"][object_id]["latest"])}
        if len(found) > 1:
            self.warnings.append(f"进展条目 {item_id} 对上了不止一处（{'、'.join(sorted(found))}），并入快照主体那一步")
        return {kind: next(iter(found))} if len(found) == 1 else None

    def issue(self, event: dict) -> None:
        component_ref, primary = event["subject_refs"][0], event["subject_refs"][1]["object_id"]
        identity, do, by = (primary, component_ref["component"]), ISSUE_DO[event["kind"]], self.actor(event)
        text = (event["content"] or {}).get("text") or None
        if do == "raise_issue":
            if identity not in self.issue_key:
                key, n = component_ref["component"], 1
                while key in self.issue_key.values():
                    n += 1
                    key = f"{component_ref['component']}.{n}"
                self.issue_key[identity] = key
            view = self.bundle["snapshots"][component_ref["object_id"]]
            component = next(item for item in value_of(view, "issues")["components"]
                             if item["id"] == component_ref["component"])
            self.step(event, do, by, self.target(primary), f"快照 `{component_ref['object_id']}` 里的问题",
                      issue=self.issue_key[identity], question=component["attributes"]["core_question"],
                      text=component["text"] or None, as_of=event["occurred_at"])
        elif identity not in self.issue_key:
            self.problems.append(f"event:{event['event_id']} 流转的问题没有提出过")
        elif do == "route_issue":
            self.step(event, do, by, {"issue": self.issue_key[identity]},
                      to=self.assignee(event["detail"]["to_principal_id"], f"event:{event['event_id']}，路由"))
        elif do == "dispose_issue":
            self.step(event, do, by, {"issue": self.issue_key[identity]}, disposition=event["disposition"],
                      text=text or NO_REASON)
        else:
            self.step(event, do, by, {"issue": self.issue_key[identity]}, text=text if do == "return_issue" else None)

    # ---------------------------------------------------------- outputs
    def lines(self) -> dict:
        tasks = [{"task": task["task"], "responsible": task["responsible"], "segments": [
            {"key": key, "title": item["title"], "text": item["text"], "responsible": item["responsible"],
             **item["attributes"]}
            for key, item in self.segments.items() if item["task"] == task["task"] and item["initial"]]}
            for task in self.tasks.values()]
        steps = [dict(step) for step in self.extra]
        for n, step in enumerate(steps):
            step["after"] = [steps[n - 1]["key"] if n else self.spine["source"]["steps"][-1]]
        return {"format": b_seed.LINES_FORMAT_02, "title": self.spine["title"], "source": self.spine["source"],
                "event_text": self.spine["event_text"], "steps": steps, "tasks": tasks,
                "notes": f"由 b_transcribe 从真实 scope 的记录转写（读取时刻 {self.bundle['read_at']}）：主干照抄 "
                         "b_lines.json，steps 是形成门与建 Task，tasks 是 Task 的首次指派与初始段。审阅见 b-review.md。"}

    def script(self) -> dict:
        return {"format": b_drive.SCRIPT_FORMAT_02, "id": f"replay-{moment(self.bundle['read_at']):%Y%m%d}",
                "title": "试用回放：真实 scope 里这场 Mission 的记录", "steps": self.steps,
                "notes": f"由 b_transcribe 转写（读取时刻 {self.bundle['read_at']}），每步的 note 是它所转写的真实事件；"
                         "审阅见 b-review.md。"}


def cell(text) -> str:
    return one_line(str(text)).replace("|", "｜") if text not in (None, "") else "—"


def review(t: Transcription, lines: dict, script: dict) -> str:
    """给 E&O DRI 的审阅稿（Markdown）：只有角色键。"""
    mission = t.bundle["objects"][t.mission]["latest"]["business"]
    roles: dict[str, int] = {}
    for item in t.people.values():
        roles[item["role"]] = roles.get(item["role"], 0) + 1
    initial = sum(len(item["segments"]) for item in lines["tasks"])
    gates = [step for step in lines["steps"] if step["do"] == "gate"]
    out = [
        "# 对照实验 B 试用回放：转写审阅稿", "",
        "给这条 Mission 的 DRI（eo-dri）审：真实 scope 里这场 Mission 的记录怎样转写成两条线共用的播种内容（b-lines.json）与执行脚本"
        "（b-script.json）。本稿与两份产物都只有角色键（ceo、eo-dri、eo-owner、eo-ic、eo-coagent、exec-agent），不含显示名。"
        "天枢代记的写入按被代记的人写；Agent 以自己身份的写入按写入转写：提出、路由、退回形成问题为 eo-coagent，其余为 "
        "exec-agent。规则见 docs/world-v02-experiment-b.md 第 8 节。", "",
        f"- 读取时刻：{t.bundle['read_at']}",
        f"- Mission：`{t.mission}`「{mission['title']}」，读回时 {cell((t.bundle['objects'][t.mission]['latest']['records']['lifecycle'] or {}).get('status'))}；"
        f"周期目标：`{t.goal}`",
        f"- 真实记录：事件 {len(t.events)} 条，快照 {len(t.bundle['snapshots'])} 张，Task {len(t.task_ids)} 个，"
        f"Activity {len(t.activity_ids)} 个",
        f"- 转写：共同播种在主干之后加门 {len(gates)} 步、建 Task {len(lines['tasks'])} 步，初始段 {initial} 段；"
        f"执行脚本 {len(script['steps'])} 步（其中划段 {sum(1 for s in script['steps'] if s['do'] == 'plan')} 步）；"
        f"未转写 {len(t.skipped)} 条",
        "- 对照表：" + "、".join(f"{role} {n}" for role, n in roles.items()) + "（agent 为按写入转写）", "",
        "## 1. 共同播种（b-lines.json）", "",
        f"主干：`{lines['source']['plan']}` 里 `{lines['source']['mission']}` 到公司的 {len(lines['source']['steps'])} 步，"
        "照原计划（b_lines.json）。之后是真实记录里的形成门与建 Task（按记录顺序，提前到执行脚本之前），再由 Mission Owner"
        f"（{t.owner}）按首次指派指派各 Task、把执行计划写成每个 Task 一条带责任人的计划条目，最后划初始段。", "",
        "| 步 | 记录者 | 动作 | 对象 | 结果 | 来源 |", "|-|-|-|-|-|-|"]
    for step in lines["steps"]:
        source = t.extra_source[step["key"]]
        what = GATE_NAMES.get(step.get("action"), "建 Task") if step["do"] == "gate" else "建 Task"
        out.append(f"| {step['key']} | {step['by']} | {what} | {step['target'] if step['do'] == 'gate' else cell(step['payload']['title'])} "
                   f"| {OUTCOMES.get(step.get('outcome'), step.get('outcome'))} | event:{source['event_id']}"
                   f"{'，代记' if source['delegated'] else ''} |")
    out += ["", "| Task | 标题 | 责任人（首次指派） | 初始段 | 真实对象 |", "|-|-|-|-|-|"]
    for object_id, task in t.tasks.items():
        title = next(step for step in lines["steps"] if step.get("key") == task["task"])["payload"]["title"]
        out.append(f"| {task['task']} | {cell(title)} | {task['responsible']}（event:{task['assign_event']}） "
                   f"| {sum(1 for item in t.segments.values() if item['task'] == task['task'] and item['initial'])} "
                   f"| `{object_id}` |")
    out += ["", "| 段 | Task | 标题 | 责任人 | 带的属性 | 划分 | 来源 |", "|-|-|-|-|-|-|-|"]
    planned = {step["segment"]: n for n, step in enumerate(script["steps"], 1) if step["do"] == "plan"}
    for key, item in t.segments.items():
        carried = "、".join(f"{ATTRIBUTE_NAMES[field]}「{cell(item['attributes'][field])[:30]}」"
                           for field in b_drive.PLAN_ATTRIBUTES if field in item["attributes"])
        out.append(f"| {key} | {item['task']} | {cell(item['title'])} | {item['responsible']} | {carried or '—'} "
                   f"| {'初始划分' if item['initial'] else f'脚本第 {planned[key]} 步'} | {item['origin']} |")
    if t.left_out:
        out += ["", "从没指派过、不进回放的：" + "、".join(f"`{object_id}`" for object_id in sorted(t.left_out)) + "。"]
    out += ["", "## 2. 执行脚本（b-script.json）", "",
            "按记录顺序。生命周期与指派不能补记，回放时按原顺序在回放时刻记下（保留顺序、不保留时间）；快照与问题的提出带"
            "真实时点，回放时读侧会标迟记。", "",
            "| 步 | 动作 | 行动者 | 目标 | 内容 | 时点 | 来源 |", "|-|-|-|-|-|-|-|"]
    for n, (step, source) in enumerate(zip(script["steps"], t.sources), 1):
        kind, key = b_drive.target_of(step)
        where = "Mission" if kind == "mission" else f"{'问题 ' if kind == 'issue' else ''}{key}"
        if "to" in step:
            where += f" → {step['to']}"
        content = step.get("question") or step.get("text") or step.get("title")
        if step["do"] == "dispose_issue":
            content = f"{step['disposition']}：{step['text']}"
        how = "代记" if source["delegated"] else ("Agent 自己记" if source["agent"] else "本人记")
        detail = f"，{source['detail']}" if source["detail"] else ""
        out.append(f"| {n} | {b_drive.VERB_NAMES[step['do']]} | {step['by']} | {where} | {cell(content)[:80]} "
                   f"| {step.get('as_of', '—')} | event:{source['event_id']}，{how}{detail} |")
    out += ["", "## 3. 未转写", "", "| 事件 | 种类 | 对象 | 原因 |", "|-|-|-|-|"]
    out += [f"| event:{item['event_id']} | {item['kind']}{'（代记）' if item['delegated'] else ''} | {item['object']} "
            f"| {item['reason']} |" for item in t.skipped] or ["| — | — | — | — |"]
    out += ["", "## 4. 请审的判断", ""]
    out += [f"- {item}" for item in t.warnings] or ["- 转写没有提出要审的个案。"]
    out += ["- 段的划分：初始段取 Task 建立时（或首次指派之前）计划块里的计划条目；之后新加的计划条目与 Activity 在脚本里划出。"
            "划段与段的再指派由 Task 当时的责任人记，与 Task+Activity 线上「Activity 由 Task 责任人建并指派」一致。",
            "- 段带的属性：计划条目写了的预期产出、质量标准、执行主体与分工随段带进两条线（执行主体是某个主体的显示名时已换成"
            "角色键）；Activity 的执行事项、预期产出与成功 / 验收标准组件对应段的正文、预期产出与质量标准。Task-only 线写成"
            "计划条目的属性；Task+Activity 线的 Activity 写预期产出与质量标准，执行主体由指派承担，分工没有对应组件。",
            "- 快照里的进展条目按组件 id 对上段或 Task 的拆成那一处的进展；对不上的留在快照主体上。同一张快照拆出的第 i 步"
            "时点加 i 微秒。",
            "- 门、Task 的建立与首次指派并入共同播种，提前到执行脚本之前；它们在两条线上相同，不进五项观测。"]
    return "\n".join(out) + "\n"


def display_names(t: Transcription) -> dict[str, str]:
    """要拦的显示名 → 角色键：对照表里的，以及真实 scope 给对照表里的主体的显示名。"""
    names = {item["display_name"]: item["role"] for item in t.people.values() if item["display_name"]}

    def walk(value):
        if isinstance(value, dict):
            found = t.people.get(value.get("principal_id"))
            if found and isinstance(value.get("display_name"), str) and value["display_name"]:
                names.setdefault(value["display_name"], found["role"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(t.bundle)
    return names


def locate(name: str, lines: dict, script: dict, text: str) -> list[str]:
    """显示名出现在哪：b-lines.json 的路径、b-script.json 的第几步哪个字段、b-review.md 的第几行。"""
    found = []

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for n, item in enumerate(value):
                walk(item, f"{path}[{n}]")
        elif isinstance(value, str) and name in value:
            found.append(f"{OUTPUTS['lines']} 的 {path}")
    walk(lines, "")
    for key, value in script.items():
        if isinstance(value, str) and name in value:
            found.append(f"{OUTPUTS['script']} 的 {key}")
    for n, step in enumerate(script["steps"], 1):
        found += [f"{OUTPUTS['script']} 第 {n} 步的 {key}" for key, value in step.items()
                  if isinstance(value, str) and name in value]
    found += [f"{OUTPUTS['review']} 第 {n} 行" for n, row in enumerate(text.splitlines(), 1) if name in row]
    return found


def transcribe(bundle: dict, table: dict, spine: dict | None = None, source: dict | None = None) -> dict:
    """读取原样 + 对照表 → {lines, script, review, skipped, warnings, objects}。转写不成立、产物用不了（b_seed.derive、
    b_drive.check_script 过不了）或任一输出里有显示名，就抛 TranscribeError，列出全部问题。"""
    spine = spine or json.loads(b_seed.LINES_FILE.read_text(encoding="utf-8"))
    source = source or json.loads((b_seed.ROOT / spine["source"]["plan"]).read_text(encoding="utf-8"))
    t = Transcription(bundle, table, spine, source)
    t.run()
    if t.problems:
        raise TranscribeError("；".join(dict.fromkeys(t.problems)))
    lines, script = t.lines(), t.script()
    try:
        derived = b_seed.derive(lines, source)
        people = set(json.loads(b_seed.SPEC_FILE.read_text(encoding="utf-8"))["principals"])
        b_drive.check_script(script, derived["segments"], derived["tasks"], people)
    except ValueError as exc:
        raise TranscribeError(f"转写产物用不了：{exc}") from None
    text = review(t, lines, script)
    hits = [f"{where} 里出现了 {role} 的显示名" for name, role in display_names(t).items()
            for where in locate(name, lines, script, text)]
    if hits:
        raise TranscribeError("输出里出现了显示名，什么也没写：" + "；".join(hits))
    return {"lines": lines, "script": script, "review": text, "skipped": t.skipped, "warnings": t.warnings,
            "objects": {object_id: t.objects.get(object_id) or t.task_key[object_id]
                        for object_id in [t.mission, *t.task_ids, *t.activity_ids] if object_id not in t.left_out}}


def write(bundle: dict, table: dict, output: Path) -> dict:
    """转写并写出三份产物（先全部转写、过拦截，再写；不成立什么也不写）。"""
    result = transcribe(bundle, table)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output, 0o700)  # 产物只有角色键，但正文照抄真实记录：仅本人可读
    (output / OUTPUTS["lines"]).write_text(json.dumps(result["lines"], ensure_ascii=False, indent=1), encoding="utf-8")
    (output / OUTPUTS["script"]).write_text(json.dumps(result["script"], ensure_ascii=False, indent=1), encoding="utf-8")
    (output / OUTPUTS["review"]).write_text(result["review"], encoding="utf-8")
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    read = sub.add_parser("transcribe", help="只读取回真实 scope 里这场 Mission 的记录并转写")
    read.add_argument("base_url")
    read.add_argument("--token-file", type=Path, required=True, help="只读用的凭证文件（不打印）")
    read.add_argument("--mission", required=True, help="Mission 的 object_id")
    read.add_argument("--principals", type=Path, required=True, help="私有的对照表")
    read.add_argument("--private", type=Path, required=True, help="读取原样 bundle.json 放这里（仅本人可读）")
    read.add_argument("--output", type=Path, required=True)
    again = sub.add_parser("write", help="从存下的读取原样重新转写，不连服务")
    again.add_argument("bundle", type=Path)
    again.add_argument("--principals", type=Path, required=True)
    again.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        table = json.loads(args.principals.read_text(encoding="utf-8"))
        if args.command == "transcribe":
            bundle = fetch(Reader(args.base_url, args.token_file), args.mission)
            private_json(args.private / "bundle.json", bundle)
        else:
            bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
        result = write(bundle, table, args.output)
    except (TransportError, ValueError) as exc:
        print(f"FAIL {exc}", flush=True)
        sys.exit(1)
    print(f"转写：执行脚本 {len(result['script']['steps'])} 步，Task {len(result['lines']['tasks'])} 个，"
          f"未转写 {len(result['skipped'])} 条，请审 {len(result['warnings'])} 条；产物在 {args.output}")


if __name__ == "__main__":
    main()
