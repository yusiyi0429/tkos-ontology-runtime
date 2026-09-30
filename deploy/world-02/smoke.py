#!/usr/bin/env python3
"""world-02 冒烟（第 7 步）：健康、world 路由、401/404，然后经 HTTP 按 tkos.world/0.2 走一条主干链。

用法：python3 smoke.py <base_url> <out目录> [--probe-only | --mcp-cli]
只用标准库（--mcp-cli 除外）；凭证只从 <out目录>/<主体键>.token 读，不打印。主体键与域键按 spec.example.json
（改了键就改这里的 KEYS）。

--probe-only：只查健康、world 路由、无凭证 401，以及 ids.json 里实际有的每个主体的凭证都能认证、读 scope 外（不存在）
的对象 404，不写库、不建对象。实验 scope（out/，名单见 spec.eo.example.json）只跑这一种。完整冒烟按 KEYS 取主体，
只对冒烟 scope（out-smoke/，tenant 以 -smoke 结尾）跑，打到别的 scope 直接 FAIL。

骨架（Company、Strategy、E&O 与 Agents 两个责任单元）一个 scope 只有一套：第一次跑时建，id 记在
<out目录>/smoke-world-02.json，重跑沿用（一个 scope 只有一个 Company、一个域只有一个责任单元）。其余对象每跑一次新建一套
（标题以「冒烟 <run>」开头），id 同样记进这个文件的 runs。链：公司级与单元长期目标（CEO 确认）→ 周期目标（DRI 承诺、
CEO 确认）→ Mission（指派 Owner、Owner 承诺、DRI 确认、Owner 开始）→ Task（Owner 指派 IC、IC 开始）→ Activity
（Task 责任人指派执行 Agent，Agent 开始、交付，Task 责任人验收）→ Task 交付与验收 → 天枢记外部事件、写引用它的
执行状态快照 → CEO 登记委托、天枢代 CEO 标核心战役、CEO 撤销委托 → 执行 Agent 从 Activity 取上下文（组件级引用）。
每步断言回执、生命周期与读回；任一步不成立即 FAIL 并以 1 退出。

--mcp-cli：链跑完后再以执行 Agent 的凭证、TKOS_WORLD_CONTRACT_VERSION=tkos.world/0.2 走命令行与 MCP：tkos-world
读 Activity、取上下文、写一条外部事件；经 stdio 起 tkos-world-mcp，工具清单是 0.2 的十三个，读 Activity、写一条外部
事件；两条事件经 HTTP 读回。tkos-world、tkos-world-mcp 与 MCP 客户端要在装了本包的环境里（仓库检出里
uv run python deploy/world-02/smoke.py …）。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

V02 = "tkos.world/0.2"
ZERO = "00000000-0000-0000-0000-000000000000"
KEYS = ("ceo", "eo-dri", "agents-dri", "eo-owner", "eo-ic", "agents-ic", "tianshu", "eo-coagent", "exec-agent")
DOMAINS = ("company", "eo", "agents")
UNITS = {"eo": ("eo-dri", "E&O"), "agents": ("agents-dri", "Agents")}
SPINE = ["Activity", "Task", "Mission", "PeriodGoal", "LongTermGoal", "ResponsibilityUnit", "Strategy", "Company"]
MCP_TOOLS = ["world_deliver", "world_get_context", "world_get_events", "world_get_object", "world_get_state",
             "world_list_objects", "world_raise_issue", "world_record_event", "world_refresh_state",
             "world_return_issue", "world_revise_object", "world_route_issue", "world_start"]


def check(name, ok, detail="", quiet=False):
    """不成立即打印 FAIL 并以 1 退出；quiet 的检查成立时不打印（读回与 prepare 这类每步都有的）。"""
    if not ok or not quiet:
        print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail and not ok else ""), flush=True)
    if not ok:
        sys.exit(1)


def moment(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec="seconds")


def blocks(view):
    return {block["id"]: block for block in view["business"]["blocks"]}


class Smoke:
    def __init__(self, base, out, probe_only=False):
        self.base, self.out = base.rstrip("/"), out
        self.ids = json.loads((out / "ids.json").read_text(encoding="utf-8"))
        if probe_only:  # 探活只看 ids.json 里实际有的主体：实验 scope 的名单与冒烟 scope 不同
            self.keys = tuple(self.ids["principals"])
            check("ids.json lists principals", bool(self.keys))
        else:
            self.keys = KEYS
            check("ids.json has the smoke spec's principals and domains",
                  set(KEYS) <= set(self.ids["principals"]) and set(DOMAINS) <= set(self.ids["domains"]))
        self.tokens = {key: (out / f"{key}.token").read_text(encoding="utf-8").strip() for key in self.keys}
        self.pid = {key: self.ids["principals"][key]["principal_id"] for key in self.keys}
        self.domain = self.ids["domains"]

    def load_state(self):
        """完整冒烟才用：只对冒烟 scope 跑，骨架与每次的对象记在 smoke-world-02.json。"""
        check("the full smoke runs only on the smoke scope (tenant ends with -smoke; use --probe-only elsewhere)",
              self.ids["tenant_id"].endswith("-smoke"), self.ids["tenant_id"])
        self.state_path = self.out / "smoke-world-02.json"
        self.state = (json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists()
                      else {"scope_id": self.ids["scope_id"], "skeleton": {}, "runs": {}})
        check("smoke record belongs to this scope", self.state["scope_id"] == self.ids["scope_id"])

    def save(self):
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------------------------------------------------------------- HTTP
    def call(self, method, path, body=None, who=None):
        request = urllib.request.Request(self.base + path, method=method,
                                         data=json.dumps(body).encode() if body is not None else None)
        request.add_header("Content-Type", "application/json")
        if who:
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

    def read(self, oid, who="ceo"):
        status, view = self.call("GET", f"/v1/world/objects/{oid}", who=who)
        check(f"read {oid}", status == 200, f"{status} {str(view)[:300]}", quiet=True)
        return view

    def ref(self, oid):
        """对象形式的引用，钉到当前最新版本。"""
        return f"{oid}@{self.read(oid)['business']['version']}"

    def life(self, oid):
        return (self.read(oid)["records"]["lifecycle"] or {}).get("status")

    def act(self, who, action, params, oid=None, key=None):
        """prepare 再 commit；oid 给出时目标取它的当前最新修订。返回回执的 result。"""
        target = None
        if oid:
            business = self.read(oid)["business"]
            target = {"object_id": oid, "revision_id": business["revision_id"],
                      "expected_version": business["object_version"]}
        body = {"action_type": action, "contract_version": V02, "target": target, "expected_versions": [],
                "idempotency_key": key or f"world-02-smoke-{uuid.uuid4()}", "reason": "world-02 冒烟", "params": params}
        status, prepared = self.call("POST", "/v1/actions/prepare", body, who)
        check(f"{who} prepares {action}", status == 200, f"{status} {str(prepared)[:400]}", quiet=True)
        body["expected_versions"] = prepared.get("expected_versions", [])
        status, receipt = self.call("POST", "/v1/actions", body, who)
        check(f"{who} commits {action}", status in (200, 201) and isinstance(receipt, dict)
              and receipt.get("status") == "committed" and receipt["result"].get("contract_version") == V02,
              f"{status} {str(receipt)[:400]}")
        return receipt["result"]

    def create(self, who, object_type, domain, payload, key=None):
        made = self.act(who, "world_create_object", {"domain_id": self.domain[domain], "object_type": object_type,
                                                     "payload": {"blocks": {}, **payload}}, key=key)
        view = self.read(made["object_id"])
        check(f"{object_type} reads back under 0.2", view["business"]["object_type"] == object_type
              and view["business"]["title"] == payload["title"] and view["protocol"]["contract_version"] == V02)
        return made, view

    def expect(self, name, oid, status):
        now = self.life(oid)
        check(f"{name} -> {status}", now == status, f"lifecycle is {now}")

    # ---------------------------------------------------------------- steps
    def basics(self):
        status, health = self.call("GET", "/v1/health")
        check("health", status == 200 and health.get("ok") is True and health.get("db") is True, f"{status} {health}")
        status, spec = self.call("GET", "/openapi.json")
        world = sorted(path for path in spec.get("paths", {}) if path.startswith("/v1/world/"))
        check("openapi has the world routes", status == 200 and len(world) >= 5, str(world))
        status, _ = self.call("GET", f"/v1/world/objects/{ZERO}")
        check("no token -> 401", status == 401, str(status))
        for key in self.keys:
            status, _ = self.call("GET", f"/v1/world/objects/{ZERO}", who=key)
            check(f"{key} token authenticates; object outside the scope -> 404", status == 404, str(status))

    def skeleton(self):
        """Company、Strategy、两个责任单元：只建一次（确定的幂等键，建成即记下），重跑读回沿用。"""
        kept = self.state["skeleton"]
        scope = self.ids["scope_id"]

        def once(name, build):
            if name not in kept:
                kept[name] = build(f"world-02-smoke:{scope}:{name}")
                self.save()
            else:
                check(f"{name} recorded by an earlier run still reads", self.read(kept[name])["object_id"] == kept[name])
            return kept[name]

        company = once("company", lambda key: self.create("ceo", "Company", "company", {
            "title": "冒烟公司",
            "blocks": {"identity": {"text": "world-02 冒烟 scope 的公司对象，由冒烟脚本以 CEO 凭证创建，只供冒烟。"}}},
            key)[0]["object_id"])
        view = self.read(company)
        check("the company's responsible is the CEO by role",
              [p["principal_id"] for p in view["identity"]["responsible"]["principals"]] == [self.pid["ceo"]])

        def strategy(key):
            made, view = self.create("ceo", "Strategy", "company", {
                "title": "冒烟战略", "parent_ref": self.ref(company),
                "blocks": {"responsibility_structure": {
                    "text": "两个责任单元；冒烟 scope 的骨架。",
                    "components": [{"id": unit, "type": "unit_entry", "text": title} for unit, (_, title) in UNITS.items()]}}},
                key)
            check("strategy keeps the writer's unit entry ids",
                  [c["id"] for c in blocks(view)["responsibility_structure"]["components"]] == list(UNITS))
            return made["object_id"]

        strategy_id = once("strategy", strategy)
        for unit, (dri, title) in UNITS.items():
            def build(key, unit=unit, dri=dri, title=title):
                entry = f"{self.ref(strategy_id)}#responsibility_structure/{unit}"
                made, view = self.create("ceo", "ResponsibilityUnit", unit, {
                    "title": title, "unit_kind": "domain", "architecture_ref": entry,
                    "blocks": {"definition": {"text": f"{title} 责任单元（冒烟 scope）。"}}}, key)
                check(f"unit {unit} is defined by its unit entry (component reference)",
                      view["business"]["relations"][0]["field"] == "architecture_ref"
                      and view["business"]["relations"][0]["value"]["component"] == unit)
                assigned = self.act("ceo", "world_assign", {"principal_id": self.pid[dri]}, made["object_id"])
                check(f"the CEO assigns the {unit} DRI", assigned["assignee"] == self.pid[dri])
                return made["object_id"]
            unit_id = once(f"unit-{unit}", build)
            responsible = self.read(unit_id)["identity"]["responsible"]["principals"]
            check(f"unit {unit}'s responsible is its DRI", self.pid[dri] in [p["principal_id"] for p in responsible])
        return company, strategy_id

    def chain(self, run, company):
        made = self.state["runs"].setdefault(run, {"started_at": moment()})

        def keep(name, value):
            made[name] = value
            self.save()
            return value

        title = f"冒烟 {run}"
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        unit = self.state["skeleton"]["unit-eo"]

        # 长期目标：公司级（CEO 建）与单元级（DRI 建），都由 CEO 确认。
        company_goal, _ = self.create("ceo", "LongTermGoal", "company", {
            "title": f"{title} 公司长期目标", "scope": "company", "horizon": "2028", "parent_ref": self.ref(company),
            "blocks": {"target": {"components": [{"id": "sc-1", "type": "success_criterion", "text": "冒烟：衡量一"}]}}})
        keep("company_goal", company_goal["object_id"])
        self.act("ceo", "world_confirm_long_term_goal", {"outcome": "accepted"}, company_goal["object_id"])
        self.expect("company long-term goal confirmed by the CEO", company_goal["object_id"], "confirmed")
        unit_goal, view = self.create("eo-dri", "LongTermGoal", "eo", {
            "title": f"{title} E&O 长期目标", "scope": "unit", "horizon": "2027", "parent_ref": self.ref(unit),
            "goal_ref": self.ref(company_goal["object_id"]),
            "blocks": {"target": {"components": [{"id": "uo-1", "type": "outcome", "text": "冒烟：结果一",
                                                  "refs": [f"{self.ref(company_goal['object_id'])}#target/sc-1"]}]}}})
        keep("unit_goal", unit_goal["object_id"])
        check("an outcome references the company goal's success criterion as a component",
              blocks(view)["target"]["components"][0]["refs"][0]["component"] == "sc-1")
        self.act("ceo", "world_confirm_long_term_goal", {"outcome": "accepted"}, unit_goal["object_id"])
        self.expect("unit long-term goal confirmed by the CEO", unit_goal["object_id"], "confirmed")

        # 周期目标：DRI 建并承诺，CEO 确认。
        goal, _ = self.create("eo-dri", "PeriodGoal", "eo", {
            "title": f"{title} 周期目标", "period": period, "goal_ref": self.ref(unit_goal["object_id"]),
            "blocks": {"target": {"components": [{"id": "pg-o1", "type": "outcome", "text": "冒烟：本期结果",
                                                  "refs": [f"{self.ref(unit_goal['object_id'])}#target/uo-1"]},
                                                 {"id": "pg-ac1", "type": "acceptance_criterion",
                                                  "text": "冒烟：本期验收"}]}}})
        goal_id = keep("period_goal", goal["object_id"])
        self.act("eo-dri", "world_commit_period_goal", {}, goal_id)
        self.expect("period goal committed by the DRI", goal_id, "committed")
        self.act("ceo", "world_confirm_period_goal", {"outcome": "accepted"}, goal_id)
        self.expect("period goal confirmed by the CEO", goal_id, "confirmed")

        # Mission：DRI 建并指派 Owner，Owner 承诺，DRI 确认，Owner 开始。
        mission, _ = self.create("eo-dri", "Mission", "eo", {
            "title": f"{title} Mission", "goal_ref": self.ref(goal_id),
            "blocks": {"definition": {"text": "冒烟：Mission 定义", "components": [
                           {"id": "m-ac1", "type": "acceptance_criterion", "text": "冒烟：交付可用",
                            "refs": [f"{self.ref(goal_id)}#target/pg-ac1"]}]},
                       "mission_plan": {"components": [{"id": "mp-path", "type": "core_path", "text": "冒烟：核心路径"}]},
                       "execution_plan": {"components": [{"id": "mp-1", "type": "plan_item", "text": "冒烟：一段计划",
                                                          "attributes": {"responsible": self.pid["eo-ic"]}}]}}})
        mission_id = keep("mission", mission["object_id"])
        self.expect("mission born a draft", mission_id, "draft")
        self.act("eo-dri", "world_assign", {"principal_id": self.pid["eo-owner"]}, mission_id)
        check("the DRI assigns the mission owner",
              self.read(mission_id)["business"]["attributes"]["responsible"] == self.pid["eo-owner"])
        self.act("eo-owner", "world_commit_mission", {}, mission_id)
        self.expect("mission committed by its owner", mission_id, "committed")
        self.act("eo-dri", "world_confirm_mission", {"outcome": "accepted"}, mission_id)
        self.expect("mission established by the DRI", mission_id, "established")
        self.act("eo-owner", "world_start", {}, mission_id)
        self.expect("mission started by its owner", mission_id, "in_progress")

        # Task：Owner 建并指派 IC，IC 开始。
        task, _ = self.create("eo-owner", "Task", "eo", {
            "title": f"{title} Task", "parent_ref": self.ref(mission_id),
            "blocks": {"definition": {"text": "冒烟：Task 定义", "components": [
                           {"id": "t-ac1", "type": "acceptance_criterion", "text": "冒烟：验收一",
                            "refs": [f"{self.ref(mission_id)}#definition/m-ac1"]},
                           {"id": "t-ac2", "type": "acceptance_criterion", "text": "冒烟：验收二"}]},
                       "plan": {"components": [{"id": "tp-1", "type": "plan_item", "text": "冒烟：先做一段"}]}}})
        task_id = keep("task", task["object_id"])
        self.expect("task born unassigned", task_id, "unassigned")
        self.act("eo-owner", "world_assign", {"principal_id": self.pid["eo-ic"]}, task_id)
        self.expect("task assigned by the mission owner", task_id, "assigned")
        self.act("eo-ic", "world_start", {}, task_id)
        self.expect("task started by its IC", task_id, "in_progress")

        # Activity：Task 的责任人建并指派执行 Agent，Agent 开始、交付（带写入声明），Task 的责任人验收。
        activity, _ = self.create("eo-ic", "Activity", "eo", {
            "title": f"{title} Activity", "parent_ref": self.ref(task_id),
            "blocks": {"instruction": {"text": "冒烟：按验收一执行", "refs": [f"{self.ref(task_id)}#definition/t-ac1"]}}})
        activity_id = keep("activity", activity["object_id"])
        self.act("eo-ic", "world_assign", {"principal_id": self.pid["exec-agent"]}, activity_id)
        self.expect("activity assigned to the execution agent", activity_id, "assigned")
        declared = {"scene": self.ref(activity_id), "trigger": "冒烟执行", "human_acceptance": {"required": False}}
        self.act("exec-agent", "world_start", {"declaration": declared}, activity_id)
        self.expect("activity started by the execution agent", activity_id, "in_progress")
        self.act("exec-agent", "world_deliver", {"declaration": declared, "content": {"text": "冒烟：已交付"}}, activity_id)
        self.expect("activity delivered by the execution agent", activity_id, "delivered")
        self.act("eo-ic", "world_accept", {}, activity_id)
        self.expect("activity accepted by the task's responsible", activity_id, "closed")
        self.act("eo-ic", "world_deliver", {"content": {"text": "冒烟：Task 交付"}}, task_id)
        self.expect("task delivered by its IC", task_id, "delivered")
        self.act("eo-owner", "world_accept", {}, task_id)
        self.expect("task accepted by the mission owner", task_id, "closed")

        # 天枢：先记一条外部事件作来源，再写引用它的执行状态快照。
        scene = {"scene": self.ref(mission_id), "trigger": "每周同步", "human_acceptance": {"required": False}}
        synced = self.act("tianshu", "world_record_event", {
            "category": "other", "subject_refs": [self.ref(mission_id)], "occurred_at": moment(seconds=-60),
            "content": {"text": f"天枢每周同步（{title}）"}, "declaration": scene})
        keep("sync_event", synced["event_id"])
        status, listed = self.call("GET", f"/v1/world/objects/{mission_id}/events", who="ceo")
        check("the external event reads back on the mission", status == 200 and any(
            e["event_id"] == synced["event_id"] and e["kind"] == "event.recorded" for e in listed["events"]))
        snapshot = self.act("tianshu", "world_refresh_state", {"declaration": scene, "payload": {
            "title": f"{title} 执行状态", "subject_ref": self.ref(mission_id), "as_of": moment(seconds=-30),
            "period": period, "payload_type": "execution_state", "source_event_refs": [f"event:{synced['event_id']}"],
            "blocks": {"progress": {"components": [{"id": "todo:smoke-1", "type": "progress_item", "text": "冒烟进展",
                                                    "attributes": {"principal_id": self.pid["eo-owner"],
                                                                   "principal_name": "E&O Mission Owner",
                                                                   "external_status": "进行中",
                                                                   "entries": [{"at": moment(seconds=-90), "source": "web",
                                                                                "text": "冒烟：本期条目"}]}}]}}}})
        keep("snapshot", snapshot["object_id"])
        status, state = self.call("GET", f"/v1/world/objects/{mission_id}/state", who="ceo")
        shot = state.get("snapshot") if status == 200 else None
        check("the execution snapshot reads back with its source event, generator and unconfirmed mark",
              shot is not None and shot["object_id"] == snapshot["object_id"]
              and shot["payload_type"]["id"] == "execution_state" and shot["unconfirmed"] is True
              and shot["source_event_refs"] == [{"event_id": synced["event_id"], "ref": f"event:{synced['event_id']}"}]
              and shot["generator"]["principal_id"] == self.pid["tianshu"], f"{status} {str(state)[:300]}")
        check("the mission's records give the latest snapshot",
              self.read(mission_id)["records"]["latest_state"]["object_id"] == snapshot["object_id"])

        # 代记：CEO 先给天枢登记门的委托（E&O 域，一小时），天枢代 CEO 标核心战役，CEO 再撤销委托。
        granted = self.act("ceo", "world_grant_delegation", {
            "delegate_principal_id": self.pid["tianshu"], "families": ["gate"], "domain_ids": [self.domain["eo"]],
            "valid_until": moment(hours=1)})
        keep("delegation_event", granted["event_id"])
        check("the mission lists the delegation in force", granted["event_id"] in [
            d["event_id"] for d in self.read(mission_id)["identity"]["delegations"]])
        marked = self.act("tianshu", "world_mark_core_battle", {"on_behalf_of": {
            "principal_id": self.pid["ceo"], "external_record_id": f"world-02-smoke:{run}",
            "external_confirmed_at": moment(minutes=-2)}}, mission_id)
        keep("core_battle_event", marked["event_id"])
        check("tianshu marks the core battle on behalf of the CEO",
              self.read(mission_id)["business"]["attributes"]["core_battle"] is True)
        status, listed = self.call("GET", f"/v1/world/objects/{mission_id}/events", who="ceo")
        row = next((e for e in listed.get("events", []) if e["event_id"] == marked["event_id"]), None)
        check("the delegated event records tianshu and the CEO it was recorded for",
              row is not None and row["principal"]["principal_id"] == self.pid["tianshu"]
              and row["on_behalf_of"]["principal_id"] == self.pid["ceo"], str(row)[:300])
        self.act("ceo", "world_revoke_delegation", {"delegation_event_id": granted["event_id"]})
        check("the revoked delegation is no longer in force", granted["event_id"] not in [
            d["event_id"] for d in self.read(mission_id)["identity"]["delegations"]])

        # 取上下文：执行 Agent 从 Activity 出发，沿主干到 Company；Task 的验收条件（任务定义块里的成功 / 验收标准
        # 组件）以组件引用返回。
        status, context = self.call("POST", f"/v1/world/objects/{activity_id}/context",
                                    {"question": "这条 Activity 为什么做、做什么、谁负责、现在怎样？"}, who="exec-agent")
        layers = context["context_pack"]["layers"] if status == 200 else []
        check("context from the activity walks the spine up to the company under 0.2",
              status == 200 and context["contract_version"] == V02
              and [layer["object"]["object_type"] for layer in layers] == SPINE, f"{status} {str(context)[:300]}")
        task_layer = layers[1]
        criteria = [c for c in {b["id"]: b for b in task_layer["blocks"]}["definition"]["components"]
                    if c["type"] == "acceptance_criterion"]
        check("the task's acceptance criteria come back as pinned component references",
              [c["ref"] for c in criteria] == [f"{task_layer['object']['ref']}#definition/{cid}" for cid in ("t-ac1", "t-ac2")]
              and all(c["pinned"]["component"] == c["id"] and f"`{c['ref']}`" in context["context_pack"]["markdown"]
                      for c in criteria))
        keep("context_pack_id", context["context_pack_id"])
        keep("finished_at", moment())
        return activity_id

    def mcp_cli(self, activity_id):
        """命令行与 MCP 都选 0.2，以执行 Agent 的凭证：CLI 读对象、取上下文、写一条外部事件；MCP 经 stdio 起服务，
        工具清单是 0.2 的十三个，读对象、写一条外部事件（做法同 acceptance/world_v02/agent_face.py）；两条事件经 HTTP 读回。"""
        ref, token = self.ref(activity_id), self.tokens["exec-agent"]
        declared = {"scene": ref, "trigger": "冒烟：命令行与 MCP", "human_acceptance": {"required": False}}

        def event(text):
            return {"category": "other", "subject_refs": [ref], "occurred_at": moment(seconds=-10),
                    "content": {"text": text}, "declaration": declared}

        cli, server = shutil.which("tkos-world"), shutil.which("tkos-world-mcp")
        check("tkos-world, tkos-world-mcp and the mcp client are installed (run from a repo checkout with uv run)",
              cli is not None and server is not None and importlib.util.find_spec("mcp") is not None)
        base = {**os.environ, "TKOS_WORLD_API_URL": self.base, "TKOS_WORLD_CONTRACT_VERSION": V02}

        def run_cli(*argv):
            result = subprocess.run([cli, *argv], env={**base, "TKOS_WORLD_TOKEN": token},
                                    capture_output=True, text=True, timeout=120)
            try:
                return result, json.loads(result.stdout)
            except ValueError:
                return result, {}

        result, body = run_cli("get", activity_id)
        check("cli get reads the activity in the 0.2 groups",
              result.returncode == 0 and body.get("business", {}).get("object_id") == activity_id,
              f"exit {result.returncode} {result.stderr[-300:]}")
        result, body = run_cli("context", activity_id, "--question", "冒烟：命令行取上下文")
        check("cli context from the activity is 0.2", result.returncode == 0 and body.get("contract_version") == V02,
              f"exit {result.returncode} {result.stderr[-300:]}")
        result, body = run_cli("act", "world_record_event", "--params",
                               json.dumps(event("冒烟：经 CLI 记的外部事件"), ensure_ascii=False),
                               "--reason", "world-02 冒烟（CLI）")
        check("cli act world_record_event commits under 0.2",
              result.returncode == 0 and body.get("status") == "committed"
              and body["result"]["contract_version"] == V02, f"exit {result.returncode} {str(body)[:300]}")
        cli_event = body["result"]["event_id"]

        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def session_run(logs):
            params = StdioServerParameters(command=server, env={**base, "TKOS_WORLD_AGENT_TOKEN": token,
                                                                "TKOS_WORLD_MCP_LOG_DIR": logs})
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                names = sorted(tool.name for tool in (await session.list_tools()).tools)

                async def call(name, arguments):
                    result = await session.call_tool(name, arguments)
                    return result.is_error, json.loads(result.content[0].text)

                return (names, await call("world_get_object", {"object_id": activity_id}),
                        await call("world_record_event", event("冒烟：经 MCP 记的外部事件")))

        with tempfile.TemporaryDirectory(prefix="world-02-mcp-") as logs:
            names, (read_failed, view), (write_failed, receipt) = anyio.run(session_run, logs)
            log = "".join(path.read_text(encoding="utf-8") for path in Path(logs).iterdir())
        check("mcp lists the thirteen 0.2 agent-face tools", names == MCP_TOOLS, str(names))
        check("mcp reads the activity in the 0.2 groups",
              not read_failed and view["business"]["object_id"] == activity_id, str(view)[:300])
        check("mcp records an external event under 0.2",
              not write_failed and receipt["status"] == "committed" and receipt["result"]["contract_version"] == V02,
              str(receipt)[:300])
        check("the mcp run log has both calls and no credential", log.count("\n") >= 2 and token not in log)
        status, listed = self.call("GET", f"/v1/world/objects/{activity_id}/events", who="ceo")
        recorders = {e["event_id"]: e["principal"]["principal_id"] for e in listed.get("events", [])}
        check("both events read back on the activity, recorded by the execution agent",
              status == 200 and all(recorders.get(e) == self.pid["exec-agent"]
                                    for e in (cli_event, receipt["result"]["event_id"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("out", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--probe-only", action="store_true", help="只查健康、路由与凭证，不写库（实验 scope 用这个）")
    mode.add_argument("--mcp-cli", action="store_true", help="链跑完后另用 tkos-world 与 tkos-world-mcp（0.2）冒烟")
    args = parser.parse_args()
    smoke = Smoke(args.base_url, args.out, probe_only=args.probe_only)
    smoke.basics()
    if args.probe_only:
        print(f"PROBE_OK scope={smoke.ids['scope_id']} tenant={smoke.ids['tenant_id']}")
        return
    smoke.load_state()
    company, _ = smoke.skeleton()
    run = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    activity_id = smoke.chain(run, company)
    if args.mcp_cli:
        smoke.mcp_cli(activity_id)
    print(f"SMOKE_OK run={run} company={company} activity={activity_id}")


if __name__ == "__main__":
    main()
