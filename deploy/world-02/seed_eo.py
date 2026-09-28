#!/usr/bin/env python3
"""world-02 实验 scope 的「E&O 十月起点」播种：按人分段，每一段只用那个人的凭证，只做他名下前置已满足的步骤。

用法：
  python3 seed_eo.py <base_url> <out目录> <计划.json> --as <主体键> [--dry-run]
                     [--proxy-operator <主体键> [--proxy-note <文字>]]
  python3 seed_eo.py <base_url> <out目录> <计划.json> --status

只用标准库；凭证只从 <out目录>/<主体键>.token 读，不打印。计划（seed-eo-2026-10.json）只写主体与域的键，id 与
显示名取 <out目录>/ids.json。每步 prepare 再 commit，幂等键固定为 world-02-eo-seed:<步骤>；回执、事件与对象 id 记进
<out目录>/seed-eo-state.json，重跑沿用。提交前先把请求记进这个文件：中断后重跑原样重发，已提交的拿回原回执。

--as X：按计划顺序做 X 名下前置已满足、还没做过的步骤；剩下的要等别人时停下，最后打印状态视图（每步谁做、做没做、
在等谁）与「下一步：谁 做 什么」。--dry-run 只列出这一段会做的步骤，不连服务、不写。--status 只打印状态视图。

代录（--proxy-operator Y）：这一段仍用 X 的凭证写，但由 Y 代为录入。收内容的门动作把 content.text 写成「<计划的事件
文字>。由<Y>代<X>录入，待本人复核」（显示名取 ids.json；--proxy-note 换这句的模板，可用 {operator}、{person}、
{date}）。这一段写完后再以 Y 自己的凭证补记一条外部事件（category other）作代录说明：主体是这一段涉及的对象（对象
形式，钉到当前版本），正文逐条列出这一段写下的事件与动作名，content.refs 是这些事件的引用；幂等键
world-02-eo-seed:proxy-note:<X>:<步骤集合的摘要>，重跑不重复记。状态视图标出每步是本人执行还是代录、由谁代录。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

V02 = "tkos.world/0.2"
FORMAT = "tkos-world-02-seed/0.1"
STATE_FILE = "seed-eo-state.json"
KEY_PREFIX = "world-02-eo-seed:"
REASON = "world-02 E&O 十月起点播种"
CHINA = timezone(timedelta(hours=8))
PROXY_SENTENCE = "由{operator}代{person}录入，待本人复核"
NOTE_HEADER = "以下{person}名下的记录由{operator}于 {date} 代为录入，待本人复核："
STEP_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
# 占位整串匹配才替换：@键 是该步建出的对象的当前版本，后面可带 #块 与 /组件。
PLACEHOLDER = re.compile(r"^@([a-z][a-z0-9_]*)(?:#([a-z][a-z0-9_]*)(?:/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127}))?)?$")
FIELDS = {"create": {"type", "domain", "payload"}, "gate": {"action", "target"}, "assign": {"target", "to"},
          "delegate": {"to", "families", "domains", "valid_until"}}
OPTIONAL = {"gate": {"outcome"}}
ACTIONS = {"create": "world_create_object", "assign": "world_assign", "delegate": "world_grant_delegation"}
GATES = {"world_confirm_long_term_goal": "确认长期目标", "world_commit_period_goal": "承诺周期目标",
         "world_confirm_period_goal": "确认周期目标", "world_commit_mission": "承诺 Mission",
         "world_confirm_mission": "确认 Mission"}
TYPES = {"Company": "公司", "Strategy": "战略", "ResponsibilityUnit": "责任单元", "LongTermGoal": "长期目标",
         "PeriodGoal": "周期目标", "Mission": "Mission", "Task": "Task", "Activity": "Activity"}
FAMILIES = {"gate": "门", "assign": "指派", "lifecycle": "生命周期"}


def fail(message: str) -> None:
    print("FAIL " + message, flush=True)
    sys.exit(1)


# ------------------------------------------------------------------ plan (pure)
def placeholders(value):
    """值里出现的全部对象占位：(步骤键, 块, 组件)。"""
    if isinstance(value, dict):
        for item in value.values():
            yield from placeholders(item)
    elif isinstance(value, list):
        for item in value:
            yield from placeholders(item)
    elif isinstance(value, str) and PLACEHOLDER.match(value):
        yield PLACEHOLDER.match(value).groups()


def check_plan(plan: dict) -> None:
    """计划不自洽就抛 ValueError，列出全部问题：步骤键唯一；after 只列前面的步骤（计划顺序就是依赖顺序）；占位与
    目标指向前置里建对象的步骤，组件占位指向那一步里有的组件。主体与域的键另由 check_people 对着 ids.json 核对。"""
    if not isinstance(plan, dict) or (plan.get("format"), plan.get("contract_version")) != (FORMAT, V02):
        raise ValueError(f"计划的 format 是 {FORMAT}、contract_version 是 {V02}")
    problems = []
    if not isinstance(plan.get("event_text"), str) or not plan["event_text"].strip():
        problems.append("event_text 非空")
    seen: dict[str, dict] = {}
    ancestors: dict[str, set] = {}
    for number, step in enumerate(plan.get("steps") or [], 1):
        where = f"第 {number} 步 {step.get('key')!r}" if isinstance(step, dict) else f"第 {number} 步"
        if not isinstance(step, dict) or step.get("do") not in FIELDS:
            problems.append(f"{where}：do 是 {'、'.join(FIELDS)} 之一")
            continue
        required = {"key", "by", "do", "after"} | FIELDS[step["do"]]
        if not required <= set(step) or set(step) - required - OPTIONAL.get(step["do"], set()) - {"note"}:
            problems.append(f"{where}：必须有 {sorted(required)}，另外只能有 {sorted(OPTIONAL.get(step['do'], set()) | {'note'})}")
            continue
        key, after = step["key"], step["after"]
        if not isinstance(key, str) or not STEP_KEY.match(key) or key in seen:
            problems.append(f"{where}：键是小写字母、数字与下划线，且不重复")
            continue
        if not isinstance(step["by"], str) or not step["by"]:
            problems.append(f"{where}：by 是主体键")
        if not isinstance(after, list) or len(set(after)) != len(after) or any(dep not in seen for dep in after):
            problems.append(f"{where}：after 只列前面的步骤，不重复")
            after = [dep for dep in after if dep in seen] if isinstance(after, list) else []
        ancestors[key] = set(after).union(*(ancestors[dep] for dep in after))
        refs = list(placeholders(step.get("payload")))
        if "target" in step:
            refs.append((step["target"], None, None))
        for ref, block, component in refs:
            origin = seen.get(ref)
            if ref not in ancestors[key] or origin is None or origin["do"] != "create":
                problems.append(f"{where}：{ref} 须是它前置里建对象的步骤")
            elif component is not None:
                value = (origin["payload"].get("blocks") or {}).get(block) or {}
                if component not in [item.get("id") for item in value.get("components") or []]:
                    problems.append(f"{where}：{ref} 的 {block} 块里没有组件 {component}")
        if step["do"] == "gate" and step["action"] not in GATES:
            problems.append(f"{where}：门动作是 {'、'.join(GATES)} 之一")
        if step["do"] == "create" and step["type"] not in TYPES:
            problems.append(f"{where}：type 是 world 业务对象类型")
        if step["do"] == "delegate" and (not step["families"] or set(step["families"]) - set(FAMILIES)):
            problems.append(f"{where}：families 是 {'、'.join(FAMILIES)} 里的")
        seen[key] = step
    if not seen:
        problems.append("steps 不能为空")
    if problems:
        raise ValueError("；".join(problems))


def check_people(plan: dict, ids: dict) -> list[str]:
    """计划用到的主体与域的键都在 ids.json 里，步骤由人记；返回问题清单。"""
    principals, domains = ids.get("principals", {}), ids.get("domains", {})
    problems = []
    for step in plan["steps"]:
        for key in [step["by"]] + ([step["to"]] if "to" in step else []):
            if key not in principals:
                problems.append(f"{step['key']}：ids.json 里没有主体 {key}")
        if principals.get(step["by"], {}).get("type") == "agent":
            problems.append(f"{step['key']}：播种的步骤由人记，{step['by']} 是 Agent")
        for domain in ([step["domain"]] if "domain" in step else []) + list(step.get("domains", [])):
            if domain not in domains:
                problems.append(f"{step['key']}：ids.json 里没有域 {domain}")
    return problems


def runnable(plan: dict, done, who: str) -> list[str]:
    """这一段会做的步骤：按计划顺序，who 名下还没做、前置都已做（或在这一段里先做到）的。"""
    ready, todo = set(done), []
    for step in plan["steps"]:
        if step["by"] == who and step["key"] not in ready and all(dep in ready for dep in step["after"]):
            todo.append(step["key"])
            ready.add(step["key"])
    return todo


def next_up(plan: dict, done) -> list[tuple[str, list[str]]]:
    """接下来谁做什么：现在有步骤前置已满足的人（按计划顺序），和他那一段会做的步骤。"""
    people = []
    for step in plan["steps"]:
        if step["key"] not in done and all(dep in done for dep in step["after"]) and step["by"] not in people:
            people.append(step["by"])
    return [(who, runnable(plan, done, who)) for who in people]


def _joined(verb: str, noun: str) -> str:
    return verb + (" " if noun[:1].isascii() else "") + noun


def describe(step: dict, steps: dict) -> str:
    """一步的中文说明，用在输出、状态视图与代录说明里。"""
    if step["do"] == "create":
        return _joined("建", f"{TYPES[step['type']]}「{step['payload']['title']}」")
    if step["do"] == "delegate":
        return (f"登记给 {step['to']} 的委托（{'、'.join(FAMILIES[f] for f in step['families'])}；"
                f"域 {'、'.join(step['domains'])}）")
    target = steps[step["target"]]
    title = f"「{target['payload']['title']}」"
    if step["do"] == "gate":
        return GATES[step["action"]] + title
    return _joined("指派", f"{TYPES[target['type']]}{title}给 {step['to']}")


def status_lines(plan: dict, state: dict, names: dict) -> list[str]:
    """状态视图：每一步谁做、做没做（本人执行或代录、由谁代录）、在等谁，最后是「下一步」。"""
    steps = {step["key"]: step for step in plan["steps"]}
    done = state["steps"]
    proxied = sum(1 for record in done.values() if record.get("proxy"))
    lines = [f"== 状态：{plan['title']}，共 {len(steps)} 步，已做 {len(done)}（其中代录 {proxied}），"
             f"代录说明 {len(state['notes'])} 条",
             "   主体：" + "，".join(f"{key}={names.get(key, key)}" for key in dict.fromkeys(s["by"] for s in plan["steps"]))]
    for number, step in enumerate(plan["steps"], 1):
        record = done.get(step["key"])
        if record:
            how = f"代录（{record['proxy']} 代）" if record.get("proxy") else "本人"
            status = f"已做·{how} event:{record['event_id']}"
        else:
            waiting: dict[str, list[str]] = {}
            for dep in step["after"]:
                if dep not in done:
                    waiting.setdefault(steps[dep]["by"], []).append(dep)
            status = "可以做" if not waiting else "在等 " + "；".join(
                f"{who}：{describe(steps[keys[0]], steps)}" + (f" 等 {len(keys)} 步" if len(keys) > 1 else "")
                for who, keys in waiting.items())
        lines.append(f"{number:>2}. {step['by']:<9}{describe(step, steps)} —— {status}")
    up = next_up(plan, done)
    if not up:
        lines.append("全部完成" if len(done) == len(steps) else "没有可做的步骤：计划的前置对不上")
    for who, keys in up:
        lines.append(f"下一步：{names.get(who, who)}（{who}）做 {describe(steps[keys[0]], steps)}"
                     + (f" 等 {len(keys)} 步" if len(keys) > 1 else ""))
    return lines


def note_key(person: str, keys: list[str]) -> str:
    """代录说明的幂等键：本人的键加这一批步骤键集合的摘要，同一批重跑得到同一个键。"""
    return f"{KEY_PREFIX}proxy-note:{person}:{hashlib.sha256(','.join(sorted(keys)).encode()).hexdigest()[:12]}"


def note_params(person: str, operator: str, date: str, entries: list[tuple[str, str, str]], subject_refs: list[str],
                occurred_at: str) -> dict:
    """代录说明（一条外部事件）的参数：entries 是按记下顺序的 (动作名, 事件 id, 步骤说明)。"""
    lines = [NOTE_HEADER.format(person=person, operator=operator, date=date)]
    lines += [f"{n}. {action} event:{event_id}（{what}）" for n, (action, event_id, what) in enumerate(entries, 1)]
    return {"category": "other", "subject_refs": subject_refs, "occurred_at": occurred_at,
            "content": {"text": "\n".join(lines), "refs": [f"event:{event_id}" for _, event_id, _ in entries]}}


def moment(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def committed(status: int, receipt) -> bool:
    return (status in (200, 201) and isinstance(receipt, dict) and receipt.get("status") == "committed"
            and (receipt.get("result") or {}).get("contract_version") == V02)


# ------------------------------------------------------------------ seeding (HTTP)
class Seeder:
    def __init__(self, base: str, out: Path, plan: dict, proxy_note: str | None = None):
        self.base, self.out, self.plan, self.proxy_note = base.rstrip("/"), out, plan, proxy_note
        self.steps = {step["key"]: step for step in plan["steps"]}
        self.ids = json.loads((out / "ids.json").read_text(encoding="utf-8"))
        problems = check_people(plan, self.ids)
        if problems:
            fail("计划与 ids.json 对不上：" + "；".join(problems))
        self.names = {key: item["display_name"] for key, item in self.ids["principals"].items()}
        self.state_path = out / STATE_FILE
        self.state = (json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else
                      {"scope_id": self.ids["scope_id"], "plan": plan["title"], "steps": {}, "pending": {}, "notes": []})
        if self.state["scope_id"] != self.ids["scope_id"]:
            fail(f"{self.state_path} 属于另一个 scope")
        self.tokens: dict[str, str] = {}

    def save(self) -> None:
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def call(self, method: str, path: str, body=None, who: str | None = None):
        request = urllib.request.Request(self.base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Content-Type", "application/json")
        if who:
            if who not in self.tokens:
                self.tokens[who] = (self.out / f"{who}.token").read_text(encoding="utf-8").strip()
            request.add_header("Authorization", "Bearer " + self.tokens[who])
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw.decode(errors="replace")
        except (urllib.error.URLError, OSError) as exc:
            fail(f"连不上 {self.base}（{exc}）：重跑即可，已记下的请求会原样重发")

    def business(self, object_id: str, who: str) -> dict:
        status, view = self.call("GET", f"/v1/world/objects/{object_id}", who=who)
        if status != 200:
            fail(f"读 {object_id}：{status} {str(view)[:300]}")
        return view["business"]

    def resolve(self, value, who: str):
        """把占位换成业务形式的引用，钉到被引用对象的当前版本。"""
        if isinstance(value, dict):
            return {key: self.resolve(item, who) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, who) for item in value]
        match = PLACEHOLDER.match(value) if isinstance(value, str) else None
        if match is None:
            return value
        key, block, component = match.groups()
        object_id = self.state["steps"][key]["object_id"]
        return (f"{object_id}@{self.business(object_id, who)['version']}"
                + (f"#{block}" if block else "") + (f"/{component}" if component else ""))

    def content_text(self, who: str, operator: str | None) -> str:
        if operator is None:
            return self.plan["event_text"]
        sentence = (self.proxy_note or PROXY_SENTENCE).format(
            operator=self.names[operator], person=self.names[who], date=datetime.now(CHINA).date().isoformat())
        return f"{self.plan['event_text']}。{sentence}"

    def request(self, step: dict, who: str, operator: str | None) -> dict:
        """这一步的请求体（expected_versions 由 prepare 给）。门与指派的目标取当前最新修订。"""
        target, principal = None, lambda key: self.ids["principals"][key]["principal_id"]
        if step["do"] == "create":
            params = {"domain_id": self.ids["domains"][step["domain"]], "object_type": step["type"],
                      "payload": self.resolve(step["payload"], who)}
        elif step["do"] == "delegate":
            params = {"delegate_principal_id": principal(step["to"]), "families": step["families"],
                      "domain_ids": [self.ids["domains"][key] for key in step["domains"]],
                      "valid_until": step["valid_until"]}
        else:
            object_id = self.state["steps"][step["target"]]["object_id"]
            business = self.business(object_id, who)
            target = {"object_id": object_id, "revision_id": business["revision_id"],
                      "expected_version": business["object_version"]}
            if step["do"] == "assign":
                params = {"principal_id": principal(step["to"])}
            else:
                params = {"content": {"text": self.content_text(who, operator)},
                          **({"outcome": step["outcome"]} if "outcome" in step else {})}
        return {"action_type": ACTIONS.get(step["do"]) or step["action"], "contract_version": V02, "target": target,
                "expected_versions": [], "idempotency_key": KEY_PREFIX + step["key"], "reason": REASON,
                "params": params}

    def submit(self, key: str, who: str, operator: str | None, build) -> dict:
        """prepare 再 commit，返回回执。提交前把请求体记进 state 的 pending：中断后重跑原样重发，已提交的拿回原回执；
        重发被拒（4xx：原请求没提交、之后对象又变了）就丢掉它，重新 prepare；服务端错误（5xx）留着它，稍后重跑。
        成功后由调用者记下结果并清掉 pending。"""
        pending = self.state["pending"].get(key)
        if pending is not None:
            if (pending["who"], pending["operator"]) != (who, operator):
                fail(f"{key} 上次由 {pending['who']}（代录人 {pending['operator']}）提交时中断，先按同样的方式重跑")
            status, receipt = self.call("POST", "/v1/actions", pending["body"], who)
            if committed(status, receipt):
                return receipt
            if status >= 500:
                fail(f"重发 {key}：服务端 {status}，稍后重跑（记下的请求留着）")
            print(f"     上次中断的 {key} 没有提交（{status}），重新 prepare", flush=True)
            del self.state["pending"][key]
            self.save()
        body = build()
        status, prepared = self.call("POST", "/v1/actions/prepare", body, who)
        if status != 200:
            fail(f"{who} prepare {body['action_type']}（{key}）：{status} {str(prepared)[:400]}")
        body["expected_versions"] = prepared.get("expected_versions", [])
        self.state["pending"][key] = {"who": who, "operator": operator, "body": body}
        self.save()
        status, receipt = self.call("POST", "/v1/actions", body, who)
        if not committed(status, receipt):
            fail(f"{who} commit {body['action_type']}（{key}）：{status} {str(receipt)[:400]}")
        return receipt

    def run_step(self, step: dict, who: str, operator: str | None) -> None:
        key = KEY_PREFIX + step["key"]
        receipt = self.submit(key, who, operator, lambda: self.request(step, who, operator))
        result = receipt["result"]
        # 事件所关于的对象：建出的对象、门与指派的目标；委托以 Company 为主体。
        about = result.get("object_id") or result["subject_refs"][0]["object_id"]
        self.state["steps"][step["key"]] = {
            "by": who, "action": receipt["action_type"], "receipt_id": receipt["receipt_id"],
            "event_id": result["event_id"], "object_id": about, "version": result.get("version"),
            "recorded_at": receipt["recorded_at"], "proxy": operator, "note": None}
        self.state["pending"].pop(key, None)
        self.save()
        how = f"，{operator} 代录" if operator else ""
        print(f"OK   {describe(step, self.steps)} → event:{result['event_id']}{how}", flush=True)

    def flush_notes(self, person: str) -> None:
        """补记代录说明：person 名下代录过、还没有代录说明的步骤，按代录人各记一条。"""
        waiting: dict[str, list[str]] = {}
        for step in self.plan["steps"]:
            record = self.state["steps"].get(step["key"])
            if record and record["by"] == person and record.get("proxy") and not record.get("note"):
                waiting.setdefault(record["proxy"], []).append(step["key"])
        for operator, keys in waiting.items():
            self.write_note(person, operator, keys)

    def write_note(self, person: str, operator: str, keys: list[str]) -> None:
        """以代录人自己的凭证记一条外部事件：主体是这批步骤涉及的对象（钉到当前版本），发生时刻取这批回执里最晚的
        记录时刻（不早于其中任一事件，也就不会被标成迟记），正文逐条列出事件与动作名，refs 是这些事件的引用。"""
        key = note_key(person, keys)
        records = [self.state["steps"][step_key] for step_key in keys]

        def build() -> dict:
            occurred = max((record["recorded_at"] for record in records), key=moment)
            subjects = list(dict.fromkeys(record["object_id"] for record in records))
            refs = [f"{object_id}@{self.business(object_id, operator)['version']}" for object_id in subjects]
            entries = [(record["action"], record["event_id"], describe(self.steps[step_key], self.steps))
                       for step_key, record in zip(keys, records)]
            params = note_params(self.names[person], self.names[operator],
                                 moment(occurred).astimezone(CHINA).date().isoformat(), entries, refs, occurred)
            if self.ids["principals"][operator]["type"] == "agent":  # 写入声明只对 Agent 强制
                params["declaration"] = {"scene": refs[0], "trigger": "E&O 十月起点播种的代录说明",
                                         "human_acceptance": {"required": False}}
            return {"action_type": "world_record_event", "contract_version": V02, "target": None,
                    "expected_versions": [], "idempotency_key": key, "reason": REASON, "params": params}

        receipt = self.submit(key, operator, None, build)
        event_id = receipt["result"]["event_id"]
        for step_key in keys:
            self.state["steps"][step_key]["note"] = event_id
        self.state["notes"].append({"person": person, "operator": operator, "steps": keys, "event_id": event_id,
                                    "receipt_id": receipt["receipt_id"], "idempotency_key": key})
        self.state["pending"].pop(key, None)
        self.save()
        print(f"OK   代录说明（{operator} 记，覆盖 {len(keys)} 条）→ event:{event_id}", flush=True)

    def segment(self, who: str, operator: str | None, dry_run: bool) -> None:
        principals = self.ids["principals"]
        if who not in principals:
            fail(f"ids.json 里没有主体 {who}")
        if operator is not None and (operator not in principals or operator == who):
            fail("--proxy-operator 是 ids.json 里另一个主体的键")
        if operator is not None:
            try:
                self.content_text(who, operator)
            except (KeyError, IndexError, ValueError):
                fail("--proxy-note 的模板只能用 {operator}、{person}、{date}")
        todo = runnable(self.plan, self.state["steps"], who)
        how = f"，由 {self.names[operator]}（{operator}）代录" if operator else ""
        print(f"== {self.names[who]}（{who}）这一段 {len(todo)} 步{how}" + ("（dry-run，不写）" if dry_run else ""),
              flush=True)
        if dry_run:
            for key in todo:
                print(f"   {describe(self.steps[key], self.steps)}")
            if operator is not None and todo:
                print(f"   之后以 {operator} 的凭证补记一条代录说明")
        else:
            try:
                for key in todo:
                    self.run_step(self.steps[key], who, operator)
            finally:
                self.flush_notes(who)
        print("\n".join(status_lines(self.plan, self.state, self.names)), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("out", type=Path)
    parser.add_argument("plan", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--as", dest="who", metavar="主体键", help="这一段用谁的凭证、做谁名下的步骤")
    mode.add_argument("--status", action="store_true", help="只打印状态视图，不连服务")
    parser.add_argument("--dry-run", action="store_true", help="只列出这一段会做的步骤，不写")
    parser.add_argument("--proxy-operator", metavar="主体键", help="代录：实际由这个主体代为录入")
    parser.add_argument("--proxy-note", metavar="文字", help="代录时门事件里那句说明的模板（{operator}、{person}、{date}）")
    args = parser.parse_args()
    if args.status and (args.dry_run or args.proxy_operator):
        parser.error("--dry-run 与 --proxy-operator 要和 --as 一起用")
    if args.proxy_note is not None and args.proxy_operator is None:
        parser.error("--proxy-note 要和 --proxy-operator 一起用")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    try:
        check_plan(plan)
    except ValueError as exc:
        fail(f"计划不成立：{exc}")
    seeder = Seeder(args.base_url, args.out, plan, args.proxy_note)
    if args.status:
        print("\n".join(status_lines(plan, seeder.state, seeder.names)))
        return
    seeder.segment(args.who, args.proxy_operator, args.dry_run)


if __name__ == "__main__":
    main()
