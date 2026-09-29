"""对照实验 B 的两条线播种（票 #66）：同一份 mission_trial 内容（deploy/world-02/seed-eo-2026-10.json）分别播进
Task-only 线与 Task+Activity 线的 scope，读回核对；另有隔离库上的一键冒烟。

    播种  python -m experiments.world_v02.b_seed seed <base_url> <目录> --line task_only|task_activity
    核对  python -m experiments.world_v02.b_seed check <base_url> <目录>
    冒烟  python -m experiments.world_v02.b_seed smoke --env-file E --private P --output O [--script S]

<目录> 是一条线的 scope：ids.json 与每个主体的 <键>.token（实例上由 deploy/world-02 的 provision-and-install.sh 按
b_spec.json 供给；隔离库上由 smoke 用 owner SQL 供给）。凭证只从文件读，不上命令行、不打印。运行日志 b-run.json 写在
同一目录，重跑沿用：播过的步骤不再做，中途断掉的那一步原样重发。

播种分两段，两条线的第一段完全相同：
1. 共同播种：取原计划里 mission_trial 到 Company 的主干（b_lines.json 的 source.steps，正文与顺序照原计划），再由
   Mission Owner 把各 Task 指派给 tasks[].responsible、把 Mission 的执行计划写成每个 Task 一条带责任人的计划条目；
2. 划段（运行日志里 phase 为 plan）：每个 Task 的 segments 按线的写法落下——Task-only 线是 Task 计划块里带责任人的
   计划条目，Task+Activity 线是 Task 下的 Activity 并指派。这一段走驱动器（b_drive），与执行脚本的记录同一格式。

smoke 在隔离库上新开两个随机 tenant 的 scope（按 b_spec.json 的角色名单）、经控制面 CLI 装 0.2、起真 API，两条线
播种、读回核对、按脚本驱动到 Mission 关闭、取证，再算五项观测（b_observe），输出在 --output。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import secrets
import sys
import uuid

from . import b_drive, b_observe
from .b_http import Line, TransportError, submit, utc_text

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[1]
LINES_FILE = FOLDER / "b_lines.json"
SPEC_FILE = FOLDER / "b_spec.json"
SMOKE_FILE = FOLDER / "b_smoke.json"
LINES_FORMAT = "tkos-world-02-experiment-b-lines/0.1"
ACTIONS = {"create": "world_create_object", "assign": "world_assign", "revise": "world_revise_object"}


def _deploy_module(name: str):
    """deploy/world-02 的脚本不是包，按文件载入（与 tests/test_world_02_*.py 相同）；只用其中的纯函数。"""
    spec = importlib.util.spec_from_file_location(f"world_02_{name}", ROOT / "deploy" / "world-02" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_eo = _deploy_module("seed_eo")


# ------------------------------------------------------------------ lines (pure)
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def derive(lines: dict, source: dict) -> dict:
    """b_lines.json 与原计划 → 两条线共同的播种步骤与段表；不自洽就抛 ValueError。

    共同播种：原计划里 source.steps 列的步骤（按原计划的顺序与正文，after 只留列出的步骤，由 seed_eo.check_plan 核对
    前置与占位），加上每个 Task 的指派（Mission Owner 记）与 Mission 执行计划的修订（每个 Task 一条带责任人的计划条目）。
    """
    if lines.get("format") != LINES_FORMAT:
        raise ValueError(f"b_lines 的 format 是 {LINES_FORMAT}")
    wanted = lines["source"]["steps"]
    steps = {step["key"]: step for step in source["steps"]}
    missing = [key for key in wanted if key not in steps]
    if missing:
        raise ValueError(f"原计划里没有这些步骤：{missing}")
    kept = [dict(step) for step in source["steps"] if step["key"] in wanted]
    for step in kept:
        dropped = [dep for dep in step["after"] if dep not in wanted]
        if dropped:
            raise ValueError(f"{step['key']} 的前置 {dropped} 不在 source.steps 里")
    plan = {**source, "title": lines["title"], "event_text": lines["event_text"], "steps": kept}
    seed_eo.check_plan(plan)
    mission = lines["source"]["mission"]
    if steps.get(mission, {}).get("type") != "Mission" or mission not in wanted:
        raise ValueError("source.mission 是列出的一个建 Mission 的步骤")
    owners = [step["to"] for step in kept if step["do"] == "assign" and step["target"] == mission]
    if len(owners) != 1:
        raise ValueError("source.steps 里恰好有一步给这个 Mission 指派 Owner")
    owner, tasks, segments = owners[0], [], {}
    for item in lines["tasks"]:
        task = item["task"]
        step = steps.get(task, {})
        if task not in wanted or step.get("type") != "Task" or step["payload"].get("parent_ref") != f"@{mission}":
            raise ValueError(f"{task} 是列出的、挂在 {mission} 下的建 Task 步骤")
        if task in tasks or not item["segments"]:
            raise ValueError(f"{task} 只列一次，且至少划一段")
        tasks.append(task)
        for segment in item["segments"]:
            key = segment["key"]
            if not b_drive.COMPONENT_ID.match(key) or key in segments:
                raise ValueError(f"段键 {key!r} 是组件 id 的写法，全表不重复")
            if not all(isinstance(segment.get(field), str) and segment[field].strip()
                       for field in ("title", "text", "responsible")):
                raise ValueError(f"段 {key} 要有 title、text 与 responsible")
            segments[key] = {"task": task, "title": segment["title"], "text": segment["text"],
                             "responsible": segment["responsible"]}
    shared = list(kept)
    for item in lines["tasks"]:
        shared.append({"key": f"assign_{item['task']}", "by": owner, "do": "assign", "after": [item["task"]],
                       "target": item["task"], "to": item["responsible"]})
    shared.append({"key": "execution_plan", "by": owner, "do": "revise", "after": [f"assign_{task}" for task in tasks],
                   "target": mission, "payload": {"blocks": {"execution_plan": {"components": [
                       {"id": item["task"], "type": "plan_item", "text": steps[item["task"]]["payload"]["title"],
                        "refs": [f"@{item['task']}"], "attributes": {"responsible": item["responsible"]}}
                       for item in lines["tasks"]]}}}})
    return {"mission": mission, "owner": owner, "tasks": tasks, "segments": segments, "shared": shared,
            "event_text": lines["event_text"]}


def load(lines_path: Path = LINES_FILE) -> tuple[dict, str]:
    """读 b_lines.json 与它指向的原计划，返回（派生结果，两份文件合在一起的内容哈希）。"""
    lines_text = Path(lines_path).read_text(encoding="utf-8")
    lines = json.loads(lines_text)
    source_text = (ROOT / lines["source"]["plan"]).read_text(encoding="utf-8")
    return derive(lines, json.loads(source_text)), sha256_text(lines_text + "\n" + source_text)


def plan_steps(derived: dict) -> tuple[list[dict], list[str]]:
    """划段那一段的步骤（与执行脚本同一格式）：每段一步 plan，由该 Task 的责任人记；键为 plan:<段>。"""
    responsible = {step["target"]: step["to"] for step in derived["shared"]
                   if step["do"] == "assign" and step["target"] in derived["tasks"]}
    steps, keys = [], []
    for n, (key, segment) in enumerate(derived["segments"].items(), 1):
        steps.append({"n": n, "do": "plan", "by": responsible[segment["task"]], "segment": key,
                      "task": segment["task"], "title": segment["title"], "text": segment["text"],
                      "to": segment["responsible"]})
        keys.append(f"plan:{key}")
    return steps, keys


# ------------------------------------------------------------------ seeding (HTTP)
class Seeder:
    """一条线的共同播种：每步 prepare 再 commit，幂等键 world-02-b:<线>:seed:<步骤>，结果记进运行日志的 seed。"""

    def __init__(self, line: Line, log: dict, save, derived: dict):
        self.line, self.log, self.save, self.derived = line, log, save, derived
        self.steps = {step["key"]: step for step in derived["shared"]}

    def object_id(self, key: str) -> str:
        return self.log["objects"][key]["object_id"]

    def resolve(self, value, who: str):
        """占位（整串匹配）换成业务形式的引用，钉到被引用对象的当前版本；计划条目的责任人（主体键）换成主体 id。"""
        if isinstance(value, dict):
            resolved = {key: self.resolve(item, who) for key, item in value.items()}
            if resolved.get("type") == "plan_item" and "responsible" in resolved.get("attributes", {}):
                resolved["attributes"] = {**resolved["attributes"],
                                          "responsible": self.line.principal(resolved["attributes"]["responsible"])}
            return resolved
        if isinstance(value, list):
            return [self.resolve(item, who) for item in value]
        match = seed_eo.PLACEHOLDER.match(value) if isinstance(value, str) else None
        if match is None:
            return value
        key, block, component = match.groups()
        return (self.line.ref(self.object_id(key), who)
                + (f"#{block}" if block else "") + (f"/{component}" if component else ""))

    def request(self, step: dict) -> dict:
        who, do = step["by"], step["do"]
        target = None
        if do == "create":
            params = {"domain_id": self.line.domain(step["domain"]), "object_type": step["type"],
                      "payload": self.resolve(step["payload"], who)}
        else:
            target = self.line.target(self.object_id(step["target"]), who)
            if do == "assign":
                params = {"principal_id": self.line.principal(step["to"])}
            elif do == "revise":
                params = {"payload": self.resolve(step["payload"], who)}
            else:
                params = {"content": {"text": self.derived["event_text"]},
                          **({"outcome": step["outcome"]} if "outcome" in step else {})}
        return {"action_type": ACTIONS.get(do) or step["action"], "contract_version": b_drive.V02, "target": target,
                "expected_versions": [], "idempotency_key": "", "reason": b_drive.REASON, "params": params}

    def run(self) -> None:
        for step in self.derived["shared"]:
            key = step["key"]
            if key in self.log["seed"]:
                continue
            idempotency = f"{b_drive.KEY_PREFIX}:{self.log['line']}:seed:{key}"
            outcome = submit(self.line, self.log["pending"], self.save, idempotency, step["by"],
                             lambda step=step: self.request(step))
            if not outcome["committed"]:
                raise ValueError(f"播种 {key}（{step['by']}）被拒：{outcome['error_code']}（{outcome['stage']}）")
            receipt, result = outcome["receipt"], outcome["receipt"]["result"]
            about = result.get("object_id") or result["subject_refs"][0]["object_id"]
            self.log["seed"][key] = {"by": step["by"], "to": step.get("to"), "action": receipt["action_type"],
                                     "receipt_id": receipt["receipt_id"], "event_id": result["event_id"],
                                     "object_id": about, "recorded_at": utc_text(receipt["recorded_at"])}
            if step["do"] == "create":
                self.log["objects"][key] = {"object_id": result["object_id"], "type": step["type"],
                                            "domain": step["domain"]}
            self.log["pending"].pop(idempotency, None)
            self.save()
            print(f"{b_drive.LINE_NAMES[self.log['line']]:<13} seed:{key:<28} {step['by']:<11} → event:{result['event_id']}",
                  flush=True)


def seed(line: Line, out: Path, line_name: str, lines_path: Path = LINES_FILE) -> dict:
    """播种一条线（共同播种加划段），可重跑；返回运行日志。"""
    derived, digest = load(lines_path)
    log = b_drive.load_log(out)
    if log is None:
        log = b_drive.new_log(line_name, line, digest)
    if (log["line"], log["scope_id"]) != (line_name, line.scope_id):
        raise ValueError(f"{Path(out) / b_drive.RUN_FILE} 属于另一条线或另一个 scope")
    if log["lines_sha256"] != digest:
        raise ValueError("b_lines.json 或原计划在上次播种之后改过；改了要新开 scope 重播")
    log.update(mission=derived["mission"], tasks=derived["tasks"], segments=derived["segments"])
    save = b_drive.saver(out, log)
    save()
    Seeder(line, log, save, derived).run()
    steps, keys = plan_steps(derived)
    b_drive.Driver(line, log, save, dict(derived["segments"])).run(steps, "plan", keys)
    log["seed_done"] = True
    save()
    return log


def check(line: Line, log: dict) -> list[str]:
    """播种之后（驱动之前）读回核对，返回问题清单：Mission 已成立、Owner 与 Task 的责任人照计划、Mission 执行计划每个
    Task 一条带责任人的计划条目；Task-only 线每段是 Task 计划块里带责任人的计划条目、scope 里没有 Activity；
    Task+Activity 线每段是 Task 下已指派的 Activity、Task 的计划块为空。"""
    who, problems = "ceo", []
    principal = {key: item["principal_id"] for key, item in line.ids["principals"].items()}

    def responsible(view: dict) -> list[str]:
        return [item["principal_id"] for item in view["identity"]["responsible"]["principals"]]

    def blocks(view: dict) -> dict:
        return {block["id"]: block for block in view["business"]["blocks"]}

    mission = line.view(log["objects"][log["mission"]]["object_id"], who)
    if mission["records"]["lifecycle"]["status"] != "established":
        problems.append(f"Mission 在 {mission['records']['lifecycle']['status']}，不是已成立")
    if responsible(mission) != [principal[log["seed"][f"assign_{log['mission']}"]["to"]]]:
        problems.append("Mission 的 Owner 与原计划的指派对不上")
    plan = {item["id"]: item for item in blocks(mission)["execution_plan"]["components"]}
    for task in log["tasks"]:
        view = line.view(log["objects"][task]["object_id"], who)
        expected = principal[log["seed"][f"assign_{task}"]["to"]]
        if responsible(view) != [expected]:
            problems.append(f"{task} 的责任人与 b_lines 对不上")
        if task not in plan or plan[task]["attributes"].get("responsible") != expected:
            problems.append(f"Mission 执行计划里 {task} 没有带同一责任人的计划条目")
        if view["records"]["lifecycle"]["status"] != "assigned":
            problems.append(f"{task} 在 {view['records']['lifecycle']['status']}，不是已指派")
        planned = {item["id"]: item for item in blocks(view)["plan"]["components"]}
        segments = {key: value for key, value in log["segments"].items() if value["task"] == task}
        if log["line"] == "task_only":
            for key, value in segments.items():
                if key not in planned or planned[key]["attributes"].get("responsible") != principal[value["responsible"]]:
                    problems.append(f"Task-only：{task} 的计划块里没有 {key} 带责任人 {value['responsible']} 的计划条目")
        else:
            if planned:
                problems.append(f"Task+Activity：{task} 的计划块应为空")
            for key, value in segments.items():
                activity = log["objects"].get(f"segment:{key}")
                if activity is None:
                    problems.append(f"Task+Activity：段 {key} 没有 Activity")
                    continue
                view = line.view(activity["object_id"], who)
                parent = {relation["field"]: relation["value"] for relation in view["business"]["relations"]}["parent_ref"]
                if (parent["object_id"] != log["objects"][task]["object_id"]
                        or responsible(view) != [principal[value["responsible"]]]
                        or view["records"]["lifecycle"]["status"] != "assigned"):
                    problems.append(f"Task+Activity：段 {key} 的 Activity 不在 {task} 下、责任人或状态不对")
    listed = line.get("/v1/world/objects?type=Activity", who)["items"]
    if log["line"] == "task_only" and listed:
        problems.append("Task-only 线里不该有 Activity")
    if log["line"] == "task_activity" and len(listed) != len(log["segments"]):
        problems.append("Task+Activity 线的 Activity 数与段数不等")
    return problems


# ------------------------------------------------------------------ isolated database (owner SQL + control plane)
def provision(h, source: Path, spec: dict, line_name: str, out: Path) -> dict:
    """在隔离库上新开一个随机 tenant 的 scope：域、主体、角色指派与凭证由 owner SQL 播种（与验收夹具、world_v01 实验
    相同的合成控制面操作），凭证写进 out/<键>.token（仅本人可读），ids.json 同 deploy/world-02/provision.py 的格式；
    再经真实控制面 CLI 装各域激活策略、0.2 profile、scope 默认策略与支持登记。"""
    import psycopg
    from psycopg.rows import dict_row

    from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
    from acceptance.protocol_a1_independent.support import private_json
    from acceptance.world_v02.fixture import action_roles, register_world_v02

    _deploy_module("provision").check_spec(spec)
    out.mkdir(parents=True, mode=0o700)
    ids = {"scope_id": str(uuid.uuid4()), "tenant_id": f"experiment-world-02-b-{line_name}-{uuid.uuid4().hex[:8]}",
           "company_id": str(uuid.uuid4()), "contract_version": b_drive.V02,
           "domains": {key: str(uuid.uuid4()) for key in spec["domains"]}, "principals": {}}
    tokens = {}
    with psycopg.connect(h.env.values["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (ids["scope_id"],))
        conn.execute("INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES (%s,%s,%s)",
                     (ids["scope_id"], ids["tenant_id"], ids["company_id"]))
        for key, name in spec["domains"].items():
            conn.execute("INSERT INTO gov_domains(domain_id,scope_id,name) VALUES (%s,%s,%s)",
                         (ids["domains"][key], ids["scope_id"], name))
        for key, principal in spec["principals"].items():
            principal_id, token = str(uuid.uuid4()), secrets.token_urlsafe(48)
            digest = hashlib.sha256(token.encode()).hexdigest()
            conn.execute("INSERT INTO gov_principals(principal_id,scope_id,principal_type,display_name) VALUES (%s,%s,%s,%s)",
                         (principal_id, ids["scope_id"], principal["type"], principal["display_name"]))
            assignments = []
            for domain, roles in principal["roles"].items():
                for role in roles:
                    assignment_id = str(uuid.uuid4())
                    conn.execute("INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role)"
                                 " VALUES (%s,%s,%s,%s,%s)",
                                 (assignment_id, ids["scope_id"], principal_id, ids["domains"][domain], role))
                    assignments.append({"domain": domain, "role": role, "assignment_id": assignment_id})
            conn.execute("SELECT set_config('app.governed_credential_digest',%s,true)", (digest,))
            conn.execute("INSERT INTO gov_credentials(scope_id,principal_id,credential_digest,label) VALUES (%s,%s,%s,%s)",
                         (ids["scope_id"], principal_id, digest, f"experiment-b {key}"))
            ids["principals"][key] = {"principal_id": principal_id, "type": principal["type"],
                                      "display_name": principal["display_name"], "assignments": assignments}
            tokens[key] = token
    for key, token in tokens.items():
        path = out / f"{key}.token"
        path.touch(mode=0o600)
        path.write_text(token, encoding="utf-8")
    (out / "ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    adapter = ControlAdapter(h, source, "memory_service_runtime.governed.control")
    for domain_id in ids["domains"].values():
        tag = uuid.uuid4().hex[:8]
        path = h.private / f"experiment-b-activation-{tag}.json"
        private_json(path, {"action_roles": action_roles(), "notes": "world-02 对照实验 B：0.2 已实现的动作"})
        adapter.cli("experiment-b-activation-" + tag, [
            "install-activation-policy", "--scope-id", ids["scope_id"], "--domain-id", domain_id,
            "--content-json", str(path), "--reason", b_drive.REASON], expected_exit=0)
    register_world_v02(h, source, ids["scope_id"])
    return ids


def smoke(env_file: Path, private: Path, output: Path, script_path: Path = SMOKE_FILE) -> dict:
    """隔离库上的一键冒烟：两条线各开一个 scope，播种、读回核对、按脚本驱动、取证，再算五项观测。
    输出 --output 下 <线>.json（运行日志，不含凭证）与 observations.json；两条线的 Mission 都关闭、核对无问题才算过。"""
    from acceptance.method_independent.harness import MethodHarness
    from acceptance.protocol_a1_independent.support import public_json

    if private.exists() or output.exists():
        raise ValueError("--private 与 --output 用新的路径")
    private.mkdir(parents=True, mode=0o700)
    source = (ROOT / "src").resolve()
    script = json.loads(script_path.read_text(encoding="utf-8"))
    spec = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    h = MethodHarness(env_file.resolve(), output.resolve(), private.resolve())
    try:
        outs = {name: private / name for name in b_drive.LINES}
        for name, out in outs.items():
            provision(h, source, spec, name, out)
        _, url, _ = h.start_api(source)
        summary = run_lines(url, outs, script)
        logs = {name: b_drive.load_log(out) for name, out in outs.items()}
        for name, log in logs.items():
            public_json(output / f"{name}.json", strip_pending(log))
        public_json(output / "observations.json", summary["observations"])
        public_json(output / "summary.json", {key: value for key, value in summary.items() if key != "observations"})
        return summary
    finally:
        h.close()


def run_lines(url: str, outs: dict, script: dict) -> dict:
    """两条线：播种、读回核对、驱动、取证，然后算观测。返回摘要。"""
    problems, closed = {}, {}
    for name, out in outs.items():
        line = Line(url, out)
        log = seed(line, out, name)
        problems[name] = check(line, log)
        b_drive.drive(line, out, script)
        log = b_drive.collect(line, out)
        mission = log["objects"][log["mission"]]["object_id"]
        closed[name] = log["evidence"]["objects"][mission]["lifecycle"]["status"] == "closed"
    logs = {name: b_drive.load_log(out) for name, out in outs.items()}
    observations = b_observe.observe(logs["task_only"], logs["task_activity"])
    passed = all(closed.values()) and not any(problems.values())
    return {"passed": passed, "missions_closed": closed, "seed_problems": problems,
            "expressions": {name: b_observe.expression_counts(log) for name, log in logs.items()},
            "conclusion": observations["conclusion"], "observations": observations}


def strip_pending(log: dict) -> dict:
    """输出运行日志时去掉待重发的请求体（跑完应当为空；它是给重跑用的）。"""
    return {**log, "pending": {}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("seed", help="播种一条线（共同播种加划段），可重跑")
    run.add_argument("base_url")
    run.add_argument("out", type=Path)
    run.add_argument("--line", choices=b_drive.LINES, required=True)
    verify = sub.add_parser("check", help="播种之后读回核对一条线")
    verify.add_argument("base_url")
    verify.add_argument("out", type=Path)
    local = sub.add_parser("smoke", help="隔离库上的一键冒烟（两条线）")
    local.add_argument("--env-file", type=Path, required=True)
    local.add_argument("--private", type=Path, required=True)
    local.add_argument("--output", type=Path, required=True)
    local.add_argument("--script", type=Path, default=SMOKE_FILE)
    args = parser.parse_args()
    try:
        if args.command == "smoke":
            summary = smoke(args.env_file, args.private, args.output, args.script)
            print(json.dumps({key: summary[key] for key in ("passed", "missions_closed", "seed_problems",
                                                             "expressions", "conclusion")}, ensure_ascii=False))
            if not summary["passed"]:
                sys.exit(2)
            return
        line = Line(args.base_url, args.out)
        if args.command == "seed":
            log = seed(line, args.out, args.line)
        else:
            log = b_drive.load_log(args.out)
            if log is None:
                raise ValueError(f"{args.out} 还没有运行日志")
        problems = check(line, log)
        print("\n".join(b_drive.status_lines(log)))
        print("读回核对：" + ("通过" if not problems else "；".join(problems)))
        if problems:
            sys.exit(2)
    except (TransportError, ValueError) as exc:
        print(f"FAIL {exc}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
