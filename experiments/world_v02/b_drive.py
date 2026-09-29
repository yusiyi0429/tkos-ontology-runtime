"""对照实验 B 的执行脚本与驱动器（票 #66）：同一份与线无关的执行脚本，按各线的原生写法落到 Task-only 与 Task+Activity
两条线上，逐步记下发出的动作、回执与事件 id、表达结果，存成运行日志（<目录>/b-run.json），作五项观测的统计输入。

    驱动  python -m experiments.world_v02.b_drive drive <base_url> <目录> [--script experiments/world_v02/b_smoke.json]
    取证  python -m experiments.world_v02.b_drive collect <base_url> <目录>
    状态  python -m experiments.world_v02.b_drive status <目录>

<目录> 是一条线的 scope（ids.json、每个主体的 <键>.token，先经 b_seed 播种）；凭证只从文件读，不打印。重跑从运行日志
接着做：记过的步骤不再做，中途断掉的那一步用同样的幂等键原样重发。

执行脚本（format tkos-world-02-experiment-b-script/0.1 或 0.2）的步骤按出现顺序从 1 编号，每步：
- do：plan（划出一段并定责任人）、assign（指派）、start、deliver、accept、reject、reopen（生命周期）、progress（在某一段
  上写进展）、refresh（状态刷新）、raise_issue、route_issue、own_issue、dispose_issue（问题流转）；0.2 另有 cancel（取消，
  生命周期）与 return_issue（退回形成，问题流转），供试用回放的转写用；
- by：主体键（b_spec.json 的角色键）；
- 目标四选一：segment（段键）、task（Task 的步骤键）、mission（true）、issue（问题键，路由、承接、处置、退回形成用）；
- 按 do 另带：to（plan、assign、route_issue）、task 与 title（plan）、text（正文：进展、状态、问题、理由；reject、
  progress、refresh、dispose_issue 必带）、question（raise_issue 的核心判断问题）、disposition（dispose_issue）；
- 可选：as_of（progress、refresh、raise_issue 的时点，回放真实记录时给）、declaration（Agent 写入声明的 trigger 与
  acceptor 主体键；Agent 的写入都带声明，不给就用默认的触发说明、不要求人工验收）、note（说明，不写入）。

两条线的写法（docs/world-v02-experiment-b.md 第 3 节的表）：
- Task+Activity 线：段就是 Task 下的 Activity。plan 是 Task 责任人建 Activity 并指派；段上的动作都以 Activity 为目标
  或主体。
- Task-only 线：段是 Task 计划块里带责任人的计划条目。plan 与 assign 改计划条目（粗粒度）。段上的生命周期动作：Task
  只有这一段时写同名的 Task 生命周期动作（原生），与 Task 一级的同名步骤按次序配对，先到的记、后到的并入、不再发动作；
  被拒则由同一人改计划条目的状态（粗粒度）。Task 有多段时只改计划条目的状态（粗粒度）。段上的进展、状态刷新与问题以
  Task 为主体、引用计划条目（Task 只有这一段时原生，多段时粗粒度）。
- 两条线里 Task、Mission 与问题上的步骤写同一个动作（原生）。
任何写法被协议拒绝即为被拒，记下每个被拒动作的错误码。行动者一律是脚本写的那个人，不替换成别人来让某条线成功。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .b_http import Line, TransportError, submit, utc_text

FOLDER = Path(__file__).resolve().parent
SCRIPT_FORMAT = "tkos-world-02-experiment-b-script/0.1"
SCRIPT_FORMAT_02 = "tkos-world-02-experiment-b-script/0.2"  # 加 cancel 与 return_issue（转写试用记录用）
VERBS_02 = ("cancel", "return_issue")
RUN_FORMAT = "tkos-world-02-experiment-b-run/0.1"
RUN_FILE = "b-run.json"
V02 = "tkos.world/0.2"
LINES = ("task_only", "task_activity")
LINE_NAMES = {"task_only": "Task-only", "task_activity": "Task+Activity"}
KEY_PREFIX = "world-02-b"
REASON = "world-02 对照实验 B"
LIFECYCLE = {"start": "world_start", "deliver": "world_deliver", "accept": "world_accept", "reject": "world_reject",
             "reopen": "world_reopen", "cancel": "world_cancel"}
VERB_NAMES = {"plan": "划段", "assign": "指派", "start": "开始", "deliver": "交付", "accept": "验收通过", "reject": "退回",
              "reopen": "重开", "cancel": "取消", "progress": "写进展", "refresh": "状态刷新", "raise_issue": "提出问题",
              "route_issue": "路由问题", "own_issue": "承接问题", "dispose_issue": "处置问题",
              "return_issue": "退回形成"}
SNAPSHOTS = ("progress", "refresh", "raise_issue")
ISSUE_STEPS = ("route_issue", "own_issue", "dispose_issue", "return_issue")
TARGET_KINDS = ("segment", "task", "mission", "issue")
# do → 允许的目标；必带字段（目标与 by 之外）。
TARGETS = {"plan": {"segment"}, "assign": {"segment", "task"},
           **{verb: {"segment", "task", "mission"} for verb in (*LIFECYCLE, *SNAPSHOTS)},
           **{verb: {"issue"} for verb in ISSUE_STEPS}}
REQUIRED = {"plan": {"task", "title", "text", "to"}, "assign": {"to"}, "reject": {"text"}, "progress": {"text"},
            "refresh": {"text"}, "raise_issue": {"issue", "question"}, "route_issue": {"to"},
            "dispose_issue": {"disposition", "text"}}
OPTIONAL = {"text", "note", "declaration", "as_of"}
DISPOSITIONS = ("no_action_close", "current_layer_action", "roll_forward", "immediate_reopen", "route_escalate",
                "pushback")
COMPONENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


# ------------------------------------------------------------------ script (pure)
def target_of(step: dict) -> tuple[str, str | None]:
    """一步的目标：(segment | task | mission | issue, 键)；mission 的键为 None。plan 的目标是它划出的段（task 是参数），
    raise_issue 的目标是问题所在的段、Task 或 Mission（issue 是参数：问题键）。"""
    found = [kind for kind in TARGET_KINDS if kind in step and not (kind == "task" and step.get("do") == "plan")
             and not (kind == "issue" and step.get("do") == "raise_issue")]
    if len(found) != 1:
        raise ValueError("一步只写一个目标：segment、task、mission 或 issue")
    return found[0], (None if found[0] == "mission" else step[found[0]])


def check_script(script: dict, segments: dict, tasks: list[str], principals: set[str] | None = None) -> None:
    """脚本不成立就抛 ValueError，列出全部问题。segments 是播种划好的段（键 → {task, …}），tasks 是 Task 的步骤键；
    脚本里的 plan 可以再划新段。principals 给出时核对 by、to 与 acceptor 都是 ids.json 里的主体键。"""
    if not isinstance(script, dict) or script.get("format") not in (SCRIPT_FORMAT, SCRIPT_FORMAT_02):
        raise ValueError(f"脚本的 format 是 {SCRIPT_FORMAT} 或 {SCRIPT_FORMAT_02}")
    problems = []
    if not isinstance(script.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", script["id"]):
        problems.append("id 是小写字母、数字、下划线与连字符（它进运行日志）")
    known, raised = dict(segments), set()
    steps = script.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("steps 不能为空")
        steps = []
    for n, step in enumerate(steps, 1):
        where = f"第 {n} 步"
        if not isinstance(step, dict) or step.get("do") not in TARGETS:
            problems.append(f"{where}：do 是 {'、'.join(TARGETS)} 之一")
            continue
        do = step["do"]
        if do in VERBS_02 and script["format"] != SCRIPT_FORMAT_02:
            problems.append(f"{where}：{do} 是 {SCRIPT_FORMAT_02} 才有的动词")
            continue
        try:
            kind, key = target_of(step)
        except ValueError as exc:
            problems.append(f"{where}：{exc}")
            continue
        if kind not in TARGETS[do]:
            problems.append(f"{where}：{do} 的目标是 {'、'.join(sorted(TARGETS[do]))}")
            continue
        required = {"by", kind} | REQUIRED.get(do, set())
        if not required <= set(step) or set(step) - required - OPTIONAL - {"do"}:
            problems.append(f"{where}：{do} 必带 {sorted(required)}，另外只能有 {sorted(OPTIONAL)}")
            continue
        declaration = step.get("declaration")
        if declaration is not None and (not isinstance(declaration, dict) or not declaration
                                        or set(declaration) - {"trigger", "acceptor"}):
            problems.append(f"{where}：declaration 只有 trigger 与 acceptor")
            declaration = None
        people = [step["by"], *([step["to"]] if "to" in step else []),
                  *([declaration["acceptor"]] if declaration and "acceptor" in declaration else [])]
        if not all(isinstance(item, str) and item for item in people):
            problems.append(f"{where}：by、to 与 acceptor 是主体键")
        elif principals is not None and set(people) - principals:
            problems.append(f"{where}：ids.json 里没有主体 {sorted(set(people) - principals)}")
        for field in ("text", "title", "question", "note"):
            if field in step and (not isinstance(step[field], str) or not step[field].strip()):
                problems.append(f"{where}：{field} 是非空文本")
        if declaration and "trigger" in declaration and (not isinstance(declaration["trigger"], str)
                                                         or not declaration["trigger"].strip()):
            problems.append(f"{where}：declaration.trigger 是非空文本")
        if "as_of" in step:
            if do not in SNAPSHOTS:
                problems.append(f"{where}：只有 {'、'.join(SNAPSHOTS)} 带 as_of")
            else:
                try:
                    utc_text(step["as_of"])
                except (AttributeError, TypeError, ValueError):
                    problems.append(f"{where}：as_of 是带时区的时刻")
        if kind == "mission" and step["mission"] is not True:
            problems.append(f"{where}：mission 写 true")
        if kind == "task" and key not in tasks:
            problems.append(f"{where}：没有 Task {key!r}")
        if do == "plan":
            if not isinstance(key, str) or not COMPONENT_ID.match(key) or key in known:
                problems.append(f"{where}：段键是组件 id 的写法，且还没划过")
            elif step["task"] not in tasks:
                problems.append(f"{where}：没有 Task {step['task']!r}")
            else:
                known[key] = {"task": step["task"]}
        elif kind == "segment" and key not in known:
            problems.append(f"{where}：段 {key!r} 还没划出")
        if do == "raise_issue":
            if not isinstance(step["issue"], str) or not COMPONENT_ID.match(step["issue"]):
                problems.append(f"{where}：问题键是组件 id 的写法")
            else:
                raised.add(step["issue"])
        if kind == "issue" and key not in raised:
            problems.append(f"{where}：问题 {key!r} 还没提出")
        if do == "dispose_issue" and step["disposition"] not in DISPOSITIONS:
            problems.append(f"{where}：disposition 是 {'、'.join(DISPOSITIONS)} 之一")
    if problems:
        raise ValueError("；".join(problems))


def segment_table(segments: dict, script: dict | None) -> dict:
    """段的全表：播种划的段加上脚本里 plan 划的段（键 → {task, title, text, responsible}）。"""
    table = {key: dict(value) for key, value in segments.items()}
    for step in (script or {}).get("steps", []):
        if step["do"] == "plan":
            table[step["segment"]] = {"task": step["task"], "title": step["title"], "text": step["text"],
                                      "responsible": step["to"]}
    return table


def segment_counts(table: dict) -> dict[str, int]:
    """每个 Task 下有几段（k）：播种与脚本里划的段合计，整次运行不变。"""
    counts: dict[str, int] = {}
    for value in table.values():
        counts[value["task"]] = counts.get(value["task"], 0) + 1
    return counts


def numbered(script: dict) -> list[dict]:
    return [{**step, "n": n} for n, step in enumerate(script["steps"], 1)]


def sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def record_expression(record: dict) -> tuple[str, list[str]]:
    """一步在一条线上的表达结果与被拒动作的错误码：
    - 并入配对的另一半（没有发动作）：原生；
    - 配套动作（support，例如快照前的外部事件）有一个被拒：被拒；
    - 主写法（primary）全部提交：按这一步写法的粒度（record["grain"]，原生或粗粒度）；
    - 主写法被拒、退路（fallback）提交：粗粒度；
    - 其余：被拒。"""
    attempts = record["attempts"]
    codes = [attempt["error_code"] for attempt in attempts if not attempt["committed"]]
    if record.get("merged_into") is not None:
        return "native", codes
    if any(attempt["role"] == "support" and not attempt["committed"] for attempt in attempts):
        return "rejected", codes
    primary = [attempt for attempt in attempts if attempt["role"] == "primary"]
    fallback = [attempt for attempt in attempts if attempt["role"] == "fallback"]
    if primary and all(attempt["committed"] for attempt in primary):
        return record["grain"], codes
    if fallback and all(attempt["committed"] for attempt in fallback):
        return "coarse", codes
    return "rejected", codes


# ------------------------------------------------------------------ run log
def new_log(line_name: str, line: Line, lines_sha: str) -> dict:
    """一条线的运行日志：播种与脚本的每一步都记在这里，是统计的唯一输入；不含凭证与显示名。"""
    return {"format": RUN_FORMAT, "line": line_name, "scope_id": line.scope_id, "lines_sha256": lines_sha,
            "principals": {item["principal_id"]: {"key": key, "type": item["type"]}
                           for key, item in line.ids["principals"].items()},
            "mission": None, "tasks": [], "segments": {}, "objects": {}, "seed": {}, "seed_done": False,
            "script": None, "script_done": False, "steps": [], "pending": {}, "evidence": None}


def load_log(out: Path) -> dict | None:
    path = Path(out) / RUN_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def saver(out: Path, log: dict):
    path = Path(out) / RUN_FILE

    def save() -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    return save


def last_recorded_at(log: dict) -> str:
    """这条线目前最晚的回执时刻（服务端时钟）。回执在事件之后写，所以它不早于这条线上任何事件的发生时刻，又已经
    过去：外部事件以它为发生时刻不会被标成迟记，也不会在服务端的将来。"""
    moments = [record["recorded_at"] for record in log["seed"].values()]
    moments += [attempt["recorded_at"] for record in log["steps"] for attempt in record["attempts"]
                if attempt["committed"]]
    return max(moments, key=utc_text)


# ------------------------------------------------------------------ driver (HTTP)
class Driver:
    """把脚本的一步按这条线的写法落下去，记下每个动作与表达结果。segments 是段的全表（segment_table）。"""

    def __init__(self, line: Line, log: dict, save, segments: dict):
        if log["line"] not in LINES:
            raise ValueError(f"line 是 {'、'.join(LINES)} 之一")
        self.line, self.log, self.save, self.name = line, log, save, log["line"]
        self.segments, self.k = segments, segment_counts(segments)

    # ---------------------------------------------------------- helpers
    def object_id(self, key: str) -> str | None:
        item = self.log["objects"].get(key)
        return item and item["object_id"]

    def attempt(self, record: dict, role: str, action: str, subject_key: str, subject_id: str | None, who: str,
                build) -> dict:
        """发一个动作（role：primary 主写法、fallback 退路、support 配套），把结果记进这一步的 attempts。"""
        key = f"{KEY_PREFIX}:{self.name}:{record['key']}:{len(record['attempts'])}"
        outcome = submit(self.line, self.log["pending"], self.save, key, who, build)
        item = {"role": role, "action": action, "subject": subject_key, "object_id": subject_id,
                "by": who, "idempotency_key": key, "committed": outcome["committed"]}
        if outcome["committed"]:
            receipt, result = outcome["receipt"], outcome["receipt"]["result"]
            item.update(receipt_id=receipt["receipt_id"], event_id=result["event_id"],
                        recorded_at=utc_text(receipt["recorded_at"]), result_object_id=result.get("object_id"),
                        result_ref=result.get("ref"))
        else:
            item.update(stage=outcome["stage"], status=outcome["status"], error_code=outcome["error_code"])
        record["attempts"].append(item)
        return item

    def refused_by_driver(self, record: dict, code: str) -> None:
        """这条线上前提没成（段没划成、问题没提出成），这一步表达不了，也不发动作。"""
        record["attempts"].append({"role": "primary", "action": None, "subject": None, "object_id": None,
                                   "by": record["by"], "committed": False, "stage": "driver", "status": None,
                                   "error_code": code})

    def declaration(self, step: dict, who: str, scene_id: str) -> dict:
        """写入声明只对 Agent 强制（契约第 9.3 节），人不带。场景是这一步在这条线上的主体。"""
        if self.line.principal_type(who) != "agent":
            return {}
        given = step.get("declaration") or {}
        acceptor = given.get("acceptor")
        return {"declaration": {
            "scene": self.line.ref(scene_id, who),
            "trigger": given.get("trigger") or f"对照实验 B 执行脚本第 {step['n']} 步（{VERB_NAMES[step['do']]}）",
            "human_acceptance": ({"required": True, "acceptor": self.line.principal(acceptor)} if acceptor
                                 else {"required": False})}}

    def body(self, action: str, params: dict, target_id: str | None, who: str) -> dict:
        return {"action_type": action, "contract_version": V02,
                "target": self.line.target(target_id, who) if target_id else None,
                "expected_versions": [], "idempotency_key": "", "reason": REASON, "params": params}

    def responsible(self, segment: str) -> str:
        """这一段当前记下的责任人：段表里的初始责任人，之后每条没被拒的 plan 或 assign 改写它。"""
        current = self.segments[segment]["responsible"]
        for item in self.log["steps"]:
            if (item["target"]["kind"] == "segment" and item["target"]["key"] == segment
                    and item["do"] in ("plan", "assign") and item["expression"] != "rejected"):
                current = item["to"]
        return current

    # ---------------------------------------------------------- writings shared by both lines
    def lifecycle(self, record, step, role, subject_key, subject_id):
        who, action = step["by"], LIFECYCLE[step["do"]]
        return self.attempt(record, role, action, subject_key, subject_id, who, lambda: self.body(action, {
            **({"content": {"text": step["text"]}} if "text" in step else {}),
            **self.declaration(step, who, subject_id)}, subject_id, who))

    def assign(self, record, step, subject_key, subject_id, to):
        who = step["by"]
        return self.attempt(record, "primary", "world_assign", subject_key, subject_id, who, lambda: self.body(
            "world_assign", {"principal_id": self.line.principal(to)}, subject_id, who))

    def snapshot(self, record, step, subject_key, subject_id, event_subject: str | None, refs: list[str]):
        """先记一条外部事件（category other，正文是这一步的 text），再以它为来源事件写主体的执行状态快照（契约补 10：
        快照至少有一条来源事件）。进展写成进展条目，状态刷新写进展块的文字，提出问题写 issues 块里的问题组件。
        外部事件的发生时刻取这条线最晚的回执时刻，快照的时点取外部事件回执的时刻（脚本给了 as_of 就都用它）。"""
        who, do = step["by"], step["do"]

        def record_event():
            return self.body("world_record_event", {
                "category": "other", "subject_refs": [event_subject or self.line.ref(subject_id, who)],
                "occurred_at": utc_text(step["as_of"]) if "as_of" in step else last_recorded_at(self.log),
                "content": {"text": step.get("text") or step["question"]},
                **self.declaration(step, who, subject_id)}, None, who)
        source = self.attempt(record, "support", "world_record_event", subject_key, subject_id, who, record_event)
        if not source["committed"]:
            return None
        as_of = utc_text(step["as_of"]) if "as_of" in step else source["recorded_at"]
        label = self.segments[step["segment"]]["title"] if "segment" in step else VERB_NAMES[do]
        if do == "progress":
            blocks = {"progress": {"components": [{
                "id": step.get("segment") or step.get("task") or "mission", "type": "progress_item",
                "text": step["text"], "refs": refs,
                "attributes": {"principal_id": self.line.principal(who),
                               "entries": [{"at": as_of, "source": "other", "text": step["text"]}]}}]}}
        elif do == "refresh":
            blocks = {"progress": {"text": step["text"], "refs": refs}}
        else:
            blocks = {"issues": {"components": [{
                "id": step["issue"], "type": "issue", "text": step.get("text") or step["question"], "refs": refs,
                "attributes": {"core_question": step["question"]}}]}}

        def refresh():
            payload = {"title": f"{label}：{VERB_NAMES[do]}（执行脚本第 {step['n']} 步）",
                       "subject_ref": self.line.ref(subject_id, who), "as_of": as_of, "payload_type": "execution_state",
                       "source_event_refs": [f"event:{source['event_id']}"], "blocks": blocks}
            return self.body("world_refresh_state", {"payload": payload, **self.declaration(step, who, subject_id)},
                             None, who)
        return self.attempt(record, "support" if do == "raise_issue" else "primary", "world_refresh_state",
                            subject_key, subject_id, who, refresh)

    def raise_issue(self, record, step, subject_key, subject_id, event_subject, refs):
        """提出问题：外部事件、带问题组件的快照、提出。问题的身份是（主受影响对象，组件 id），后面的流转引用它。"""
        record["target"]["issue"] = step["issue"]
        record["issue_primary_id"] = subject_id
        written = self.snapshot(record, step, subject_key, subject_id, event_subject, refs)
        if written is None or not written["committed"]:
            return
        record["issue_ref"] = f"{written['result_ref']}#issues/{step['issue']}"
        self.issue_action(record, step, "world_raise_issue", record["issue_ref"], subject_id, {})

    def issue_action(self, record, step, action, issue_ref, primary_id, extra):
        who = step["by"]

        def build():
            params = {"issue_ref": issue_ref, **extra}
            if action not in ("world_own_issue", "world_dispose_issue"):  # 承接与处置只由人记，不收写入声明
                params.update(self.declaration(step, who, primary_id))
            return self.body(action, params, None, who)
        return self.attempt(record, "primary", action, f"issue:{record['target'].get('issue')}", primary_id, who, build)

    # ---------------------------------------------------------- one step
    def run_step(self, step: dict, key: str, phase: str) -> dict:
        kind, target = target_of(step)
        record = {"key": key, "phase": phase, "n": step["n"], "do": step["do"], "by": step["by"],
                  "target": {"kind": kind, "key": target}, "line": self.name, "writing": None, "grain": "native",
                  "attempts": [], "merged_into": None}
        if "to" in step:
            record["to"] = step["to"]
        if kind == "segment":
            task = self.segments[target]["task"]
            record["target"].update(task=task, segments_in_task=self.k[task])
        if kind == "issue":
            record["target"]["issue"] = target
            self.on_issue(record, step)
        elif kind == "segment" and self.name == "task_activity":
            self.on_activity(record, step)
        elif kind == "segment":
            self.on_plan_item(record, step)
        else:
            self.on_object(record, step, kind, target)
        record["expression"], record["error_codes"] = record_expression(record)
        return record

    def on_issue(self, record, step):
        """路由、承接、处置、退回形成：两条线写同一个动作，问题引用取这条线上最近提出它的那一步。"""
        record["writing"] = "issue"
        raised = [item for item in self.log["steps"] if item["do"] == "raise_issue"
                  and item["target"].get("issue") == step["issue"] and item["expression"] != "rejected"]
        if not raised:
            self.refused_by_driver(record, "ISSUE_NOT_RAISED")
            return
        extra = {"route_issue": lambda: {"to_principal_id": self.line.principal(step["to"])},
                 "own_issue": lambda: {},
                 "dispose_issue": lambda: {"disposition": step["disposition"], "content": {"text": step["text"]}},
                 "return_issue": lambda: {"content": {"text": step["text"]}} if "text" in step else {}}
        self.issue_action(record, step, "world_" + step["do"], raised[-1]["issue_ref"], raised[-1]["issue_primary_id"],
                          extra[step["do"]]())

    def on_object(self, record, step, kind, target):
        """Task 与 Mission 上的步骤：两条线写同一个动作。Task-only 线里 Task 只有一段时与段的同名生命周期步骤配对。"""
        do = step["do"]
        subject_key = self.log["mission"] if kind == "mission" else target
        subject_id = self.object_id(subject_key)
        record["writing"] = kind
        if do in LIFECYCLE and kind == "task" and self.name == "task_only" and self.k.get(target) == 1:
            record["merged_into"] = self.partner(record, step, target)
            if record["merged_into"] is not None:
                return
        if do in LIFECYCLE:
            self.lifecycle(record, step, "primary", subject_key, subject_id)
        elif do == "assign":
            self.assign(record, step, subject_key, subject_id, step["to"])
        elif do == "raise_issue":
            self.raise_issue(record, step, subject_key, subject_id, None, [])
        else:
            self.snapshot(record, step, subject_key, subject_id, None, [])

    def on_activity(self, record, step):
        """Task+Activity 线的段：Task 下的 Activity。"""
        do, segment = step["do"], step["segment"]
        subject_key = f"segment:{segment}"
        record["writing"] = "activity"
        if do == "plan":
            task_key, who = step["task"], step["by"]
            task_id = self.object_id(task_key)

            def create():
                payload = {"title": step["title"], "parent_ref": self.line.ref(task_id, who),
                           "blocks": {"instruction": {"text": step["text"]}}}
                return self.body("world_create_object", {
                    "domain_id": self.line.domain(self.log["objects"][task_key]["domain"]), "object_type": "Activity",
                    "payload": payload}, None, who)
            made = self.attempt(record, "primary", "world_create_object", subject_key, None, who, create)
            if made["committed"]:
                self.log["objects"][subject_key] = {"object_id": made["result_object_id"], "type": "Activity",
                                                    "domain": self.log["objects"][task_key]["domain"], "task": task_key}
                self.assign(record, step, subject_key, made["result_object_id"], step["to"])
            return
        activity_id = self.object_id(subject_key)
        if activity_id is None:
            self.refused_by_driver(record, "SEGMENT_NOT_PLANNED")
        elif do in LIFECYCLE:
            self.lifecycle(record, step, "primary", subject_key, activity_id)
        elif do == "assign":
            self.assign(record, step, subject_key, activity_id, step["to"])
        elif do == "raise_issue":
            self.raise_issue(record, step, subject_key, activity_id, None, [])
        else:
            self.snapshot(record, step, subject_key, activity_id, None, [])

    def revise_plan(self, record, step, role, subject_key, task_id, segment, text, responsible):
        """Task-only：由这一步的行动者改 Task 计划块里的一条计划条目（组件按 id 合并，整条替换）。"""
        who = step["by"]
        component = {"id": segment, "type": "plan_item", "text": text,
                     "attributes": {"responsible": self.line.principal(responsible)}}
        return self.attempt(record, role, "world_revise_object", subject_key, task_id, who, lambda: self.body(
            "world_revise_object", {"payload": {"blocks": {"plan": {"components": [component]}}},
                                    **self.declaration(step, who, task_id)}, task_id, who))

    def on_plan_item(self, record, step):
        """Task-only 线的段：Task 计划块里带责任人的计划条目。"""
        do, segment = step["do"], step["segment"]
        task_key = self.segments[segment]["task"]
        task_id = self.object_id(task_key)
        subject_key = f"{task_key}#plan/{segment}"
        single = self.k[task_key] == 1
        base = self.segments[segment]["text"]
        record["writing"], record["grain"] = "plan_item", "coarse"
        if do in ("plan", "assign"):
            self.revise_plan(record, step, "primary", subject_key, task_id, segment, base, step["to"])
            return
        if do in LIFECYCLE:
            status = (f"{base}\n\n状态：{VERB_NAMES[do]}（执行脚本第 {step['n']} 步）"
                      + (f"：{step['text']}" if "text" in step else ""))
            if not single:
                self.revise_plan(record, step, "primary", subject_key, task_id, segment, status,
                                 self.responsible(segment))
                return
            record["writing"], record["grain"] = "task_level", "native"
            record["merged_into"] = self.partner(record, step, task_key)
            if record["merged_into"] is not None:
                return
            if not self.lifecycle(record, step, "primary", task_key, task_id)["committed"]:
                self.revise_plan(record, step, "fallback", subject_key, task_id, segment, status,
                                 self.responsible(segment))
            return
        # 进展、状态刷新与问题：以 Task 为主体、引用这条计划条目；外部事件以计划条目的组件引用为主体。
        record["writing"], record["grain"] = "task_subject", "native" if single else "coarse"
        component = f"{self.line.ref(task_id, step['by'])}#plan/{segment}"
        if do == "raise_issue":
            self.raise_issue(record, step, subject_key, task_id, component, [component])
        else:
            self.snapshot(record, step, subject_key, task_id, component, [component])

    def partner(self, record: dict, step: dict, task_key: str) -> str | None:
        """Task-only 线里只有一段的 Task：这一段与 Task 一级的同名生命周期步骤按次序配对（第 i 条段的步骤配第 i 条
        Task 的步骤）。配对的另一半已经在前面写成了这个 Task 的生命周期动作，就返回它的键，这一步并入、不再发动作。"""
        do, task_id = step["do"], self.object_id(task_key)
        mine_segment = record["target"]["kind"] == "segment"

        def same(item, as_segment: bool) -> bool:
            target = item["target"]
            if item["do"] != do or item["phase"] != "script":
                return False
            if as_segment:
                return target["kind"] == "segment" and target.get("task") == task_key
            return target["kind"] == "task" and target["key"] == task_key
        index = sum(1 for item in self.log["steps"] if same(item, mine_segment))
        others = [item for item in self.log["steps"] if same(item, not mine_segment)]
        if index >= len(others):
            return None
        other = others[index]
        wrote = any(attempt["committed"] and attempt["action"] == LIFECYCLE[do] and attempt["object_id"] == task_id
                    for attempt in other["attempts"])
        return other["key"] if wrote else None

    # ---------------------------------------------------------- phases
    def run(self, steps: list[dict], phase: str, keys: list[str]) -> list[dict]:
        """逐步驱动，每记完一步落盘并清掉这一步留在 pending 里的已提交请求；记过的步骤跳过。"""
        done = {record["key"] for record in self.log["steps"]}
        written = []
        for step, key in zip(steps, keys):
            if key in done:
                continue
            record = self.run_step(step, key, phase)
            self.log["steps"].append(record)
            for attempt in record["attempts"]:
                self.log["pending"].pop(attempt.get("idempotency_key"), None)
            self.save()
            written.append(record)
            print(f"{LINE_NAMES[self.name]:<13} {key:<22} {step['by']:<11} {VERB_NAMES[step['do']]:<5} "
                  f"→ {record['expression']}" + (f" {record['error_codes']}" if record["error_codes"] else "")
                  + (f"（并入 {record['merged_into']}）" if record["merged_into"] else ""), flush=True)
        return written


def attach_script(log: dict, script: dict) -> None:
    """一条线只跑一份脚本：第一次驱动记下脚本 id 与哈希，之后换脚本拒绝（两条线按步号配对）。"""
    identity = {"id": script["id"], "sha256": sha256(script)}
    if log["script"] is None:
        log["script"] = identity
    elif log["script"] != identity:
        raise ValueError(f"这条线已经跑过脚本 {log['script']['id']}（哈希不同）；换脚本要新开 scope")


def drive(line: Line, out: Path, script: dict) -> dict:
    """按脚本驱动一条已播种的线；可重跑。"""
    log = load_log(out)
    if log is None or not log.get("seed_done"):
        raise ValueError(f"{out} 还没播种完（先跑 b_seed seed）")
    if log["scope_id"] != line.scope_id:
        raise ValueError(f"{Path(out) / RUN_FILE} 属于另一个 scope")
    check_script(script, log["segments"], log["tasks"], set(line.ids["principals"]))
    save = saver(out, log)
    attach_script(log, script)
    save()
    driver = Driver(line, log, save, segment_table(log["segments"], script))
    steps = numbered(script)
    driver.run(steps, "script", [str(step["n"]) for step in steps])
    log["script_done"] = True
    save()
    return log


# ------------------------------------------------------------------ evidence
def compact_event(view: dict) -> dict:
    """统计要用的事件字段（不带正文与显示名）。"""
    return {"event_id": view["event_id"], "kind": view["kind"], "class": view["class"],
            "subjects": [{"object_id": item["object_id"], "component": item.get("component")}
                         for item in view["subject_refs"]],
            "principal_id": view["principal"]["principal_id"], "principal_type": view["principal"]["principal_type"],
            "on_behalf_of": view["on_behalf_of"] and view["on_behalf_of"]["principal_id"],
            "detail": view["detail"], "action": view["action"], "action_id": view["action_id"],
            "outcome": view["outcome"], "disposition": view["disposition"], "category": view["category"],
            "occurred_at": view["occurred_at"], "recorded_at": view["recorded_at"], "late": view["late"]}


def collect(line: Line, out: Path) -> dict:
    """取证：这条线上播种与驱动碰过的每个对象（含快照）的事件，业务对象的生命周期、责任人与计划条目，快照的主体与
    组件，写进运行日志的 evidence。读者用 CEO（scope 内有指派的人都能读全部 world 对象）。"""
    log = load_log(out)
    who = "ceo"
    ids = {item["object_id"]: {"key": key, "type": item["type"]} for key, item in log["objects"].items()}
    for record in log["steps"]:
        for attempt in record["attempts"]:
            if attempt["committed"] and attempt["action"] == "world_refresh_state":
                ids.setdefault(attempt["result_object_id"], {"key": f"snapshot:{record['key']}", "type": "StateSnapshot"})
    events, objects, snapshots = {}, {}, {}
    for object_id, item in ids.items():
        for view in line.events(object_id, who):
            events[view["event_id"]] = compact_event(view)
        view = line.view(object_id, who)
        if item["type"] == "StateSnapshot":
            snapshots[object_id] = {"key": item["key"], "subject_id": view["subject_ref"]["object_id"],
                                    "payload_type": view["payload_type"]["id"], "as_of": view["as_of"],
                                    "components": {block["id"]: [component["id"] for component in block["components"]]
                                                   for block in view["blocks"] if block["components"]}}
            continue
        lifecycle = view["records"]["lifecycle"]
        objects[object_id] = {**item, "version": view["business"]["version"],
                              "lifecycle": lifecycle and {"status": lifecycle["status"], "event_id": lifecycle["event_id"]},
                              "responsible": [principal["principal_id"]
                                              for principal in view["identity"]["responsible"]["principals"]],
                              "plan_items": {component["id"]: component["attributes"].get("responsible")
                                             for block in view["business"]["blocks"]
                                             if block["id"] in ("plan", "execution_plan")
                                             for component in block["components"]}}
    log["evidence"] = {"events": sorted(events.values(), key=lambda item: (utc_text(item["recorded_at"]), item["event_id"])),
                       "objects": objects, "snapshots": snapshots}
    saver(out, log)()
    return log


def status_lines(log: dict) -> list[str]:
    counts: dict[str, int] = {}
    for record in log["steps"]:
        counts[record["expression"]] = counts.get(record["expression"], 0) + 1
    return [f"== {LINE_NAMES[log['line']]}：scope {log['scope_id']}，播种 {len(log['seed'])} 步"
            + ("（完成）" if log.get("seed_done") else "（未完成）")
            + f"，脚本 {log['script'] and log['script']['id']}" + ("（跑完）" if log.get("script_done") else "")
            + f"，已记 {len(log['steps'])} 步：" + "，".join(f"{key} {value}" for key, value in sorted(counts.items()))
            + f"，待重发 {len(log['pending'])}，取证{'已做' if log.get('evidence') else '未做'}"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("drive", help="按脚本驱动一条已播种的线，逐步记进运行日志，可重跑")
    run.add_argument("base_url")
    run.add_argument("out", type=Path)
    run.add_argument("--script", type=Path, default=FOLDER / "b_smoke.json")
    evidence = sub.add_parser("collect", help="读回这条线的事件、生命周期与快照，写进运行日志")
    evidence.add_argument("base_url")
    evidence.add_argument("out", type=Path)
    show = sub.add_parser("status", help="只看运行日志的状态，不连服务")
    show.add_argument("out", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "status":
            log = load_log(args.out)
            print("\n".join(status_lines(log)) if log else f"{args.out} 还没有运行日志")
            return
        line = Line(args.base_url, args.out)
        if args.command == "drive":
            log = drive(line, args.out, json.loads(args.script.read_text(encoding="utf-8")))
        else:
            log = collect(line, args.out)
        print("\n".join(status_lines(log)))
    except (TransportError, ValueError) as exc:
        print(f"FAIL {exc}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
