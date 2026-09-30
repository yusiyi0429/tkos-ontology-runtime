"""对照实验 B 的五项观测（票 #66）：从两条线的运行日志（驱动器的逐步记录，加取证得到的事件与快照）机械算出。
纯函数，不连库、不连服务；算法与判断口径见 docs/world-v02-experiment-b.md 第 5、6 节。

    python -m experiments.world_v02.b_observe <Task-only 的 b-run.json> <Task+Activity 的 b-run.json> [--output 文件]

每项观测先在 Task+Activity 线的事件上找「发生」（occurrence），再判它是不是独立发生（independent）：

- 指派：Activity 的指派事件。被指派者不是当时这个 Task 的责任人（别人或 Agent）才算独立。
- 执行：Agent 以自己负责的 Activity 为主体的写入——开始、交付、外部事件、状态刷新（快照的主体）。都算独立：Agent
  不能是 Task 的责任人，Task 一级承载不了这些写入。
- 重试：Activity 的退回与重开。与 Task 一级的同名事件配对（Task 只有这一个 Activity，且 Task 有第 i 条同名事件配
  这个 Activity 的第 i 条）的不算独立。
- 验收：Activity 的验收通过。配对的不算独立；验收人就是这个 Activity 的责任人（自己验自己）的也不算。
- 管理：主受影响对象是 Activity 的提出问题，与主体是 Activity 的状态刷新。Task 只有这一个 Activity 时不算独立（以
  Task 为主体是同一粒度）。

每条发生按运行日志找到产生它的那一步，取 Task-only 线同一步的表达结果：原生、粗粒度，被拒记为「表达不了」。一项观测
在 Task-only 线上的表达取它独立发生的那些步里最差的（表达不了 > 粗粒度 > 原生）。

结论：五项中有独立发生、且 Task-only 线表达不了的，Activity 留作对象；否则降为组件。粗粒度算能表达：它把这一步写成
计划条目的修改或以 Task 为主体的记录，信息在，只是没有 Activity 一级的生命周期与权限——这部分损失逐条列在
coarse 里，给报告复核，不改结论。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .b_drive import RUN_FORMAT

OBSERVATIONS = {
    "assign": ("指派", "是否要把一段单独指派给别人或 Agent"),
    "execute": ("执行", "Agent 是否需要以 Activity 为主体写入"),
    "retry": ("重试", "失败后是否需要 Activity 自己的退回与重开"),
    "accept": ("验收", "是否需要在 Activity 一级验收"),
    "manage": ("管理", "Issue 与 State 是否需要指向 Activity"),
}
RANK = {"native": 0, "coarse": 1, "inexpressible": 2}
VERDICT_NAMES = {"native": "原生", "coarse": "粗粒度", "inexpressible": "表达不了"}
STATUS_AFTER = {"start": "in_progress", "deliver": "delivered", "accept": "closed", "reject": "adjusting",
                "reopen": "in_progress", "cancel": "cancelled"}
AGENT_WRITES = {"start", "deliver", "event.recorded", "state.refreshed"}
RULE = ("五项中有独立发生、且 Task-only 线表达不了（被拒）的，Activity 留作对象；否则降为组件。粗粒度（只能改计划条目，"
        "或只能以 Task 为主体记下）算能表达，逐条列出，不改结论。")


def verdict(expression: str) -> str:
    """Task-only 线一步的表达结果 → 观测口径：被拒即表达不了。"""
    return "inexpressible" if expression == "rejected" else expression


def check_pair(task_only: dict, task_activity: dict) -> None:
    """两份运行日志是同一次对照：格式、线、播种内容与脚本相同，步骤一一对应，都已取证。"""
    for name, log in (("task_only", task_only), ("task_activity", task_activity)):
        if log.get("format") != RUN_FORMAT or log.get("line") != name:
            raise ValueError(f"{name} 的运行日志格式或线不对")
        if not log.get("evidence"):
            raise ValueError(f"{name} 还没取证（b_drive collect）")
    if task_only["lines_sha256"] != task_activity["lines_sha256"] or task_only["script"] != task_activity["script"]:
        raise ValueError("两条线的播种内容或执行脚本不同")
    if [record["key"] for record in task_only["steps"]] != [record["key"] for record in task_activity["steps"]]:
        raise ValueError("两条线的步骤对不上")


class Timeline:
    """Task+Activity 线按运行日志顺序的已提交事件，以及随之推进的责任人与生命周期（只为判独立用）。"""

    def __init__(self, log: dict):
        self.log = log
        self.events = {event["event_id"]: event for event in log["evidence"]["events"]}
        self.types = {item["object_id"]: item["type"] for item in log["objects"].values()}
        self.activity_task = {item["object_id"]: log["objects"][item["task"]]["object_id"]
                              for key, item in log["objects"].items() if key.startswith("segment:")}
        self.segment_of = {item["object_id"]: key.split(":", 1)[1]
                           for key, item in log["objects"].items() if key.startswith("segment:")}
        planned: dict[str, set] = {}
        for record in log["steps"]:
            if record["do"] == "plan":
                planned.setdefault(record["target"]["task"], set()).add(record["target"]["key"])
        self.k = {log["objects"][task]["object_id"]: len(keys) for task, keys in planned.items()}
        self.sequence = []  # (步键, 记录或 None, 事件)
        for key, item in log["seed"].items():
            self.sequence.append((f"seed:{key}", None, self.events[item["event_id"]]))
        for record in log["steps"]:
            for attempt in record["attempts"]:
                if attempt["committed"]:
                    self.sequence.append((record["key"], record, self.events[attempt["event_id"]]))
        self.same_kind: dict[tuple[str, str], list[str]] = {}
        for _, _, event in self.sequence:
            if event["kind"] in STATUS_AFTER:
                self.same_kind.setdefault((event["subjects"][0]["object_id"], event["kind"]), []).append(event["event_id"])

    def mirrored(self, event: dict, activity: str) -> bool:
        """配对：Task 只有这一个 Activity，且 Task 有第 i 条同名事件配这个 Activity 的第 i 条。"""
        task = self.activity_task[activity]
        if self.k.get(task) != 1:
            return False
        index = self.same_kind[(activity, event["kind"])].index(event["event_id"])
        return index < len(self.same_kind.get((task, event["kind"]), []))


def subject_of(event: dict) -> str:
    """事件所关于的业务对象：状态刷新与 Issue 事件是第二项（第一项是快照或问题组件），其余是第一项。"""
    return event["subjects"][1 if event["kind"] == "state.refreshed" or event["kind"].startswith("issue.") else 0]["object_id"]


def occurrences(log: dict) -> dict[str, list[dict]]:
    """Task+Activity 线上五项观测的每一条发生，按日志顺序；带是否独立与理由。"""
    line = Timeline(log)
    role = {principal_id: item["key"] for principal_id, item in log["principals"].items()}
    kinds = {principal_id: item["type"] for principal_id, item in log["principals"].items()}
    responsible: dict[str, str] = {}
    status: dict[str, str] = {}
    found: dict[str, list[dict]] = {name: [] for name in OBSERVATIONS}

    def add(name: str, key: str, event: dict, activity: str, independent: bool, why: str) -> None:
        found[name].append({"step": key, "event_id": event["event_id"], "kind": event["kind"],
                            "segment": line.segment_of[activity], "independent": independent, "why": why})

    for key, record, event in line.sequence:
        subject = subject_of(event)
        activity = subject if line.types.get(subject) == "Activity" else None
        kind, recorder = event["kind"], event["principal_id"]
        if activity is not None:
            task = line.activity_task[activity]
            single = line.k.get(task) == 1
            if kind == "assign":
                assignee, holder = event["detail"]["principal_id"], responsible.get(task)
                add("assign", key, event, activity, assignee != holder,
                    f"指派给 {role[assignee]}（{'Agent' if kinds[assignee] == 'agent' else '人'}），"
                    f"当时 Task 的责任人是 {role.get(holder, '无')}")
            if kinds[recorder] == "agent" and kind in AGENT_WRITES and responsible.get(activity) == recorder:
                add("execute", key, event, activity, True, f"{role[recorder]}（Agent）以自己负责的 Activity 为主体记 {kind}")
            if kind in ("reject", "reopen"):
                paired = line.mirrored(event, activity)
                add("retry", key, event, activity, not paired,
                    "Task 只有这一个 Activity，与 Task 的同名事件配对" if paired
                    else f"Activity 自己的{'退回' if kind == 'reject' else '重开'}，当时 Task 在 {status.get(task)}")
            if kind == "accept":
                paired, own = line.mirrored(event, activity), recorder == responsible.get(activity)
                add("accept", key, event, activity, not paired and not own,
                    "Task 只有这一个 Activity，与 Task 的验收配对" if paired
                    else "验收人就是这个 Activity 的责任人" if own
                    else f"{role[recorder]} 验收 {role.get(responsible.get(activity), '无')} 负责的这一段，当时 Task 在 "
                         f"{status.get(task)}")
            if kind in ("issue.raised", "state.refreshed"):
                add("manage", key, event, activity, not single,
                    ("问题的主受影响对象" if kind == "issue.raised" else "快照的主体") + "是 Activity"
                    + ("；Task 只有这一个 Activity，以 Task 为主体是同一粒度" if single else ""))
        target = event["subjects"][0]["object_id"]
        if kind == "assign":
            responsible[target] = event["detail"]["principal_id"]
            if status.get(target, "unassigned") == "unassigned":
                status[target] = "assigned"
        elif kind in STATUS_AFTER and line.types.get(target) in ("Task", "Activity", "Mission"):
            status[target] = STATUS_AFTER[kind]
    return found


def step_view(record: dict) -> dict:
    return {"step": record["key"], "expression": record["expression"], "verdict": verdict(record["expression"]),
            "writing": record["writing"], "error_codes": record["error_codes"], "merged_into": record["merged_into"],
            "event_ids": [attempt["event_id"] for attempt in record["attempts"] if attempt["committed"]]}


def summarize(name: str, found: list[dict], task_only: dict) -> dict:
    """一项观测：独立发生了没有、Task-only 线上怎么表达（独立发生的那些步里最差的）、依据。"""
    for item in found:
        item["task_only"] = step_view(task_only[item["step"]])
    independent = [item for item in found if item["independent"]]
    steps = list(dict.fromkeys(item["step"] for item in independent))
    verdicts = {step: verdict(task_only[step]["expression"]) for step in steps}
    counts = {key: sum(1 for value in verdicts.values() if value == key) for key in RANK}
    worst = max(verdicts.values(), key=RANK.get) if verdicts else None
    title, question = OBSERVATIONS[name]
    return {"name": title, "question": question, "occurred": bool(independent), "task_only": worst,
            "task_only_counts": counts, "keeps_activity": worst == "inexpressible",
            "evidence": {"task_activity_event_ids": [item["event_id"] for item in independent], "steps": steps},
            "coarse": [step_view(task_only[step]) for step in steps if verdicts[step] == "coarse"],
            "occurrences": found}


def subject_type(log: dict, record: dict) -> str | None:
    """一步写到的主体类型（主写法或退路里第一个有对象的动作）。"""
    types = {item["object_id"]: item["type"] for item in log["objects"].values()}
    for attempt in record["attempts"]:
        if attempt["role"] != "support" and attempt["object_id"]:
            return types.get(attempt["object_id"])
    return None


def agent_subject(task_only: dict, task_activity: dict) -> dict:
    """另记：Agent 写入需要以谁为主体。逐条列出行动者是 Agent 的步骤在两条线上写到哪类主体、表达结果；
    needs：有 Agent 的写入在 Task+Activity 线以 Activity 为主体写成、在 Task-only 线表达不了，为 Activity；否则有写成的
    取 Task-only 线上写到的主体类型（Task 或 Mission）；没有 Agent 的步骤为 None。"""
    agents = {principal_id for principal_id, item in task_activity["principals"].items() if item["type"] == "agent"}
    keys = {task_activity["principals"][principal_id]["key"] for principal_id in agents}
    rows = []
    for mine, other in zip(task_activity["steps"], task_only["steps"]):
        if mine["by"] not in keys:
            continue
        rows.append({"step": mine["key"], "do": mine["do"], "by": mine["by"],
                     "task_activity": {"subject": subject_type(task_activity, mine), "expression": mine["expression"]},
                     "task_only": {"subject": subject_type(task_only, other), "expression": other["expression"],
                                   "verdict": verdict(other["expression"]), "error_codes": other["error_codes"]}})
    if any(row["task_activity"]["subject"] == "Activity" and row["task_activity"]["expression"] == "native"
           and row["task_only"]["verdict"] == "inexpressible" for row in rows):
        needs = "Activity"
    else:
        written = [row["task_only"]["subject"] for row in rows if row["task_only"]["verdict"] != "inexpressible"]
        needs = "Task" if "Task" in written else (written[0] if written else None)
    return {"needs": needs, "steps": rows}


def expression_counts(log: dict) -> dict:
    counts = {"native": 0, "coarse": 0, "rejected": 0}
    for record in log["steps"]:
        counts[record["expression"]] += 1
    return counts


def observe(task_only: dict, task_activity: dict) -> dict:
    """五项观测、Agent 写入的主体、逐步对照与结论。"""
    check_pair(task_only, task_activity)
    by_key = {record["key"]: record for record in task_only["steps"]}
    found = occurrences(task_activity)
    observations = {name: summarize(name, found[name], by_key) for name in OBSERVATIONS}
    keeps = [name for name, item in observations.items() if item["keeps_activity"]]
    return {
        "format": "tkos-world-02-experiment-b-observations/0.1",
        "scopes": {"task_only": task_only["scope_id"], "task_activity": task_activity["scope_id"]},
        "script": task_activity["script"], "lines_sha256": task_activity["lines_sha256"],
        "expressions": {"task_only": expression_counts(task_only), "task_activity": expression_counts(task_activity)},
        "observations": observations,
        "agent_subject": agent_subject(task_only, task_activity),
        "rejected_in_task_activity": [step_view(record) for record in task_activity["steps"]
                                      if record["expression"] == "rejected"],
        "steps": [{"step": mine["key"], "do": mine["do"], "by": mine["by"], "target": mine["target"],
                   "task_activity": mine["expression"], "task_only": other["expression"],
                   "task_only_writing": other["writing"], "task_only_error_codes": other["error_codes"]}
                  for mine, other in zip(task_activity["steps"], task_only["steps"])],
        "conclusion": {"activity": "object" if keeps else "component", "because": keeps, "rule": RULE},
    }


def render(result: dict) -> str:
    """观测结果的中文摘要（给终端与报告草稿）。"""
    lines = [f"对照实验 B：脚本 {result['script']['id']}，结论 Activity "
             + ("留作对象" if result["conclusion"]["activity"] == "object" else "降为组件")
             + (f"（{'、'.join(OBSERVATIONS[name][0] for name in result['conclusion']['because'])}）"
                if result["conclusion"]["because"] else "")]
    for name, item in result["observations"].items():
        where = f"，Task-only {VERDICT_NAMES[item['task_only']]}" if item["task_only"] else ""
        lines.append(f"- {item['name']}：{'独立发生' if item['occurred'] else '没有独立发生'}{where}"
                     f"（{'、'.join(item['evidence']['steps']) or '—'}）")
    lines.append(f"- Agent 写入需要以谁为主体：{result['agent_subject']['needs'] or '没有 Agent 的写入'}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task_only", type=Path)
    parser.add_argument("task_activity", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = observe(json.loads(args.task_only.read_text(encoding="utf-8")),
                     json.loads(args.task_activity.read_text(encoding="utf-8")))
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(render(result))


if __name__ == "__main__":
    main()
