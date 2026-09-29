#!/usr/bin/env python3
"""world-02：给天枢的实测请求与返回示例（#72）。在冒烟 scope 上按天枢的接入顺序逐步真打接口，记下每个请求与返回，
再把 id 换成占位、显示名换成角色名，渲染成给天枢的 Markdown。

用法：
  python3 examples.py run <base_url> <凭证目录> <输出目录> [--commit <短提交>] [--doc <Markdown 路径>]
  python3 examples.py render <原始记录> --doc <Markdown 路径> [--commit <短提交>]

只用标准库；凭证只从 <凭证目录>/<主体键>.token 读，不打印、不写进任何记录。<凭证目录> 是冒烟 scope 的那份
（ids.json 与凭证，tenant 以 -smoke 结尾，别的 scope 直接 FAIL），骨架（Company、Strategy、两个责任单元）照
smoke.py 的做法有就沿用、没有就建，记在 <凭证目录>/smoke-world-02.json。其余对象每跑一次新建一套，标题以
「示例 <run>」开头，不动骨架以外别人的对象；骨架里只由 E&O DRI 本人给 E&O 责任单元写能力域的外部引用（固定写
tianshu 的 capability:05，重跑原样再写）。

run 按十一步真打，每步断言返回码与关键字段，失败即停（以 1 退出，停之前尽力撤销已登记的委托）；人的委托由本人先
登记、末尾撤销。原始记录（含真实 id，不含凭证）写到 <输出目录>/examples-<run>.json（0600，不入库），再渲染成
--doc（默认 <输出目录>/world-v02-tianshu-examples.md）。render 只从原始记录重新渲染，不连服务。
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit
import uuid

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("world_02_smoke", HERE / "smoke.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)
check, V02 = smoke.check, smoke.V02

CST = timezone(timedelta(hours=8))
UNIT_REF = {"system": "tianshu", "id": "capability:05"}  # 骨架 E&O 单元（能力域 05）的外部引用，固定，重跑不累积
# 显示名一律换成冒烟名单里的角色名（spec.example.json），真名即使出现在库里也不进文档。
ROLE_NAMES = {key: principal["display_name"] for key, principal in json.loads(
    (HERE / "spec.example.json").read_text(encoding="utf-8"))["principals"].items()}
FORMAT = "world-02-examples/1"

# 文档的节，按天枢的接入顺序：（编号，标题，一句说明）。编号也是原始记录里 step 的取值。
STEPS = [
    ("prep", "准备（不是天枢的调用）",
     "以下由人本人经 HTTP 记，只列结果，不列请求；骨架（Company、Strategy、两个责任单元）沿用冒烟 scope 已有的一套。"
     "责任单元（能力域）的外部引用由 E&O 写好：单元的 DRI 本人写，天枢只写 Mission 的（天枢改单元的是 403，见第 11 节）。"),
    ("1", "列对象、按外部引用查回",
     "接口清单第十项：按外部引用查回 E&O 写好的能力域；按单元、类型、周期列 Mission；取对象的三组读投影。本次 Mission "
     "的外部引用还没写，按它查回空列表。"),
    ("2", "写外部引用",
     "接口清单第九项：天枢以 Agent 身份修订 Mission 的 `external_refs`，带写入声明，只改活动属性时 "
     "`human_acceptance.required` 为 false。每个写入都先以同一请求体调 `/v1/actions/prepare`，再把返回的 "
     "`expected_versions` 带回 `/v1/actions` 提交；本节列出两段，之后只列提交与出错的 prepare。"),
    ("3", "每周同步：来源事件与执行状态快照",
     "接口清单第四项：先记一条外部事件作来源，再写引用它的执行状态快照。进展条目的组件 id 用天枢执行事项 id（`todo:` 加"
     "天枢的 uuid），本期条目放 `attributes.entries`，多个链接放组件的 `artifacts`；问题写成 `issues` 块里的 `issue` "
     "组件，组件 id 用天枢 issue id。幂等键按清单的建议写。"),
    ("4", "会议事件",
     "接口清单第十一项：外部事件可以补记过去的时刻（这里补记两小时前的会），读取按发生时刻升序并标迟记。"),
    ("5", "代记门：周期目标与 Mission 的承诺、确认",
     "接口清单第八项：人本人先登记委托（门、指派、生命周期、议题，按人各取所需），天枢再带 `on_behalf_of` 代记，代记"
     "写入不带写入声明。事件同时记下记录者（天枢服务主体）与被代记的人，外部确认时刻另存。"),
    ("6", "执行计划：天枢写计划条目",
     "接口清单第二项：天枢以 Agent 身份修订 Mission 的执行计划块（活动块，已成立后也不走门），带写入声明、不要求人工"
     "验收。计划条目的组件 id 用天枢执行事项 id，`responsible` 填执行人，只作记录，不是指派。"),
    ("7", "Task：建、指派与生命周期",
     "接口清单第六、八项：E&O DRI 建 Task，`external_refs` 写同一个执行事项 id；之后天枢代 Mission Owner 指派、打回、"
     "验收、重开，代执行人开始、交付。重开要求 Task 已关闭，所以打回之后先再交付、验收一次；重复的交付与验收没有列出。"),
    ("8", "执行计划：Co-Agent 再加一条",
     "接口清单第二项：E&O 的 Co-Agent 直接修订同一个执行计划块。组件按 id 合并：补丁里只有新条目，天枢写的那条保留。"),
    ("9", "议题：提出、路由、承接、处置、退回形成",
     "接口清单第七、八项：五个动作都不带 `target`，以 `params.issue_ref`（快照 `issues` 块里问题组件的组件引用）指明"
     "问题。天枢以自己的身份提出、路由（带写入声明）；承接、退回形成与处置由天枢按承接人第 5 节登记的议题族委托代记，"
     "不带写入声明。委托的域按问题所在的域（主受影响对象的域）判。"),
    ("10", "取上下文",
     "接口清单第十项：从 Mission 出发取上下文；有门的对象另带「形成时带入」。返回很长，下面截短了。"),
    ("11", "典型错误",
     "错误都在 prepare 就返回，库里不留任何记录。返回形状是 `{\"error\": {\"code\", \"message\"}}`。"),
    ("end", "收尾：撤销委托",
     "委托人本人撤销委托，引用登记那条事件，即时生效。每个人的撤销请求形状相同，只列第一条。"),
]
STEP_IDS = [number for number, _, _ in STEPS]

UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SLUGS = {"Company": "company", "Strategy": "strategy", "ResponsibilityUnit": "unit", "LongTermGoal": "long-term-goal",
         "PeriodGoal": "period-goal", "Mission": "mission", "Task": "task", "Activity": "activity",
         "StateSnapshot": "snapshot"}
ID_KINDS = {"event_id": "event", "supersedes_event_id": "event", "delegation_event_id": "event",
            "revision_id": "revision", "latest_revision_id": "revision", "receipt_id": "receipt",
            "action_id": "action", "context_pack_id": "context-pack"}
# 文档里截短返回：（数组超过几项就截，留前几项，文本超过几字就截）；取上下文的返回太大，另收紧。JSON 行宽超过 WIDTH 才折行。
CONTEXT_STEP = "10"
SHORTEN = {"default": (6, 3, 1200), CONTEXT_STEP: (2, 1, 1500)}
WIDTH = 110


def at(**delta) -> str:
    """请求里的时刻按天枢的习惯写 +08:00，到秒。"""
    return (datetime.now(CST) + timedelta(**delta)).isoformat(timespec="seconds")


class Examples(smoke.Smoke):
    """在冒烟的 HTTP、骨架与读回上加记账：step 不为 None 时每个请求与返回都记下，带说明（caption）的进文档。"""

    def __init__(self, base, out):
        super().__init__(base, out)
        self.run = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.todo = str(uuid.uuid4())  # 天枢执行事项的 id（真 uuid）：进展条目、计划条目与 Task 的外部引用共用
        self.records, self.types, self.prep, self.steps = [], {}, [], {}
        self.step = self.caption = None
        self.reason, self.seq, self.delegations, self.revoked = "world-02 冒烟", 0, {}, set()

    # ---------------------------------------------------------------- 记账
    def call(self, method, path, body=None, who=None):
        status, response = super().call(method, path, body, who)
        if self.step is not None:
            self.records.append({"step": self.step, "caption": self.caption, "method": method, "path": path,
                                 "who": who, "body": copy.deepcopy(body), "status": status, "response": response})
        self.caption = None
        return status, response

    def read(self, oid, who="ceo"):
        view = super().read(oid, who)
        self.types[oid] = view.get("business", view)["object_type"]  # 状态快照的读回是平的，没有三组
        return view

    def get(self, path, caption, who="tianshu"):
        self.caption = caption
        status, body = self.call("GET", path, who=who)
        check(f"GET {path.split('?')[0]} as {who}", status == 200, f"{status} {str(body)[:300]}")
        return body

    def act(self, who, action, params, oid=None, key=None, caption=None, prepare_caption=None, expect=None):
        """prepare 再 commit（同 smoke.Smoke.act），幂等键默认按运行标记编号。expect=(状态码, 错误码) 时只 prepare，
        断言它按预期被拒并返回错误体。"""
        target = None
        if oid:
            business = self.read(oid)["business"]
            target = {"object_id": oid, "revision_id": business["revision_id"],
                      "expected_version": business["object_version"]}
        self.seq += 1
        body = {"action_type": action, "contract_version": V02, "target": target, "expected_versions": [],
                "idempotency_key": key or f"world-02-examples:{self.run}:{self.seq:03d}", "reason": self.reason,
                "params": params}
        self.caption = prepare_caption
        status, prepared = self.call("POST", "/v1/actions/prepare", body, who)
        if expect:
            code = prepared.get("error", {}).get("code") if isinstance(prepared, dict) else None
            check(f"{who} {action} is refused with {expect[0]} {expect[1]}", (status, code) == expect,
                  f"{status} {str(prepared)[:400]}")
            return prepared
        check(f"{who} prepares {action}", status == 200, f"{status} {str(prepared)[:400]}", quiet=True)
        body["expected_versions"] = prepared.get("expected_versions", [])
        self.caption = caption
        status, receipt = self.call("POST", "/v1/actions", body, who)
        check(f"{who} commits {action}", status in (200, 201) and isinstance(receipt, dict)
              and receipt.get("status") == "committed" and receipt["result"].get("contract_version") == V02,
              f"{status} {str(receipt)[:400]}")
        return receipt["result"]

    def declare(self, oid, trigger):
        return {"scene": self.ref(oid), "trigger": trigger, "human_acceptance": {"required": False}}

    def behalf(self, person, what):
        self.seq += 1
        return {"principal_id": self.pid[person], "external_record_id": f"tianshu:{what}:{self.run}-{self.seq:03d}",
                "external_confirmed_at": at(minutes=-3)}

    def listed(self, query, caption=None):
        return self.get("/v1/world/objects?" + urlencode(query, safe=":"), caption)

    def passed(self, step):
        self.steps[step] = {"status": "passed"}
        print(f"STEP {step} passed", flush=True)

    # ---------------------------------------------------------------- 准备
    def prepare_chain(self, company):
        """人本人建本次要用的目标与 Mission（不进文档正文，只在「准备」一节列结果）。"""
        self.step, self.reason = "prep", "world-02 天枢接入示例"
        self.title, self.period = f"示例 {self.run}", datetime.now(CST).strftime("%Y-%m")
        unit = self.unit = self.state["skeleton"]["unit-eo"]

        def made(who, text, oid):
            self.prep.append({"who": who, "text": text, "object_id": oid})
            return oid

        self.act("eo-dri", "world_revise_object", {"payload": {"external_refs": [UNIT_REF]}}, unit)
        made("eo-dri", f"给 E&O 责任单元写能力域的外部引用 `{UNIT_REF['system']}`/`{UNIT_REF['id']}`（骨架，重跑原样再写）", unit)
        goal = made("ceo", "建公司级长期目标并确认", self.create("ceo", "LongTermGoal", "company", {
            "title": f"{self.title} 公司长期目标", "scope": "company", "horizon": "2028", "parent_ref": self.ref(company),
            "blocks": {"measures": {"components": [{"id": "sc-1", "type": "success_criterion", "text": "示例：衡量一"}]}}}
        )[0]["object_id"])
        self.act("ceo", "world_confirm_long_term_goal", {"outcome": "accepted"}, goal)
        unit_goal = made("eo-dri", "建 E&O 长期目标，CEO 确认", self.create("eo-dri", "LongTermGoal", "eo", {
            "title": f"{self.title} E&O 长期目标", "scope": "unit", "horizon": "2027", "parent_ref": self.ref(unit),
            "goal_ref": self.ref(goal), "blocks": {"outcome": {"components": [
                {"id": "uo-1", "type": "outcome", "text": "示例：结果一", "refs": [f"{self.ref(goal)}#measures/sc-1"]}]}}}
        )[0]["object_id"])
        self.act("ceo", "world_confirm_long_term_goal", {"outcome": "accepted"}, unit_goal)
        self.goal = made("eo-dri", "建本期周期目标（草稿，第 5 节代记承诺与确认）", self.create("eo-dri", "PeriodGoal", "eo", {
            "title": f"{self.title} 周期目标", "period": self.period, "goal_ref": self.ref(unit_goal),
            "blocks": {"outcome": {"components": [{"id": "pg-o1", "type": "outcome", "text": "示例：本期结果",
                                                   "refs": [f"{self.ref(unit_goal)}#outcome/uo-1"]}]},
                       "acceptance": {"components": [{"id": "pg-ac1", "type": "acceptance_criterion",
                                                      "text": "示例：本期验收"}]}}})[0]["object_id"])
        self.mission = made("eo-dri", "建 Mission（草稿）并指派 E&O Mission Owner", self.create("eo-dri", "Mission", "eo", {
            "title": f"{self.title} Mission", "goal_ref": self.ref(self.goal),
            "blocks": {"definition": {"text": "示例：Mission 定义"}, "play": {"text": "示例：核心路径"},
                       "acceptance": {"components": [{"id": "m-ac1", "type": "acceptance_criterion", "text": "示例：交付可用",
                                                      "refs": [f"{self.ref(self.goal)}#acceptance/pg-ac1"]}]}}})[0]["object_id"])
        self.act("eo-dri", "world_assign", {"principal_id": self.pid["eo-owner"]}, self.mission)
        self.expect("mission born a draft with its owner", self.mission, "draft")
        self.passed("prep")

    # ---------------------------------------------------------------- 十一步
    def step1(self):
        self.step, mission = "1", self.mission
        found = self.listed({"external_system": UNIT_REF["system"], "external_id": UNIT_REF["id"]},
                            f"按外部引用查回责任单元：E&O 写好的能力域 `{UNIT_REF['id']}`")
        check("the capability finds the E&O unit", [item["object_id"] for item in found["items"]] == [self.unit])
        query = {"unit_id": self.unit, "type": "Mission", "period": self.period, "limit": 5}
        page = self.listed(query)
        while mission not in [item["object_id"] for item in page["items"]] and page["next_cursor"]:
            page = self.listed({**query, "cursor": page["next_cursor"]})
        mine = [item for item in page["items"] if item["object_id"] == mission]
        check("this run's mission is listed with its lifecycle and no external refs yet",
              len(mine) == 1 and mine[0]["lifecycle"]["status"] == "draft" and mine[0]["external_refs"] == []
              and all(item["object_type"] == "Mission" for item in page["items"]))
        self.records[-1]["caption"] = ("列 E&O 单元本期的 Mission：按建立时刻升序分页，每页 5 条，把上一页的 `next_cursor` "
                                       "原样带回取下一页；这里列出本次 Mission 所在的那一页")
        view = self.get(f"/v1/world/objects/{mission}", "取对象：`business`、`identity`、`records` 三组")
        check("the object reads in the three groups", set(view) >= {"business", "identity", "records"}
              and view["business"]["object_id"] == mission)
        self.mission_ext = {"system": "tianshu", "id": f"mission:demo-{self.run}"}
        empty = self.listed({"external_system": "tianshu", "external_id": self.mission_ext["id"]},
                            "按外部引用查找本次的 Mission：还没写过这一对时 `items` 为空")
        check("an external ref nobody carries finds nothing", empty == {"items": [], "next_cursor": None})
        self.passed("1")

    def step2(self):
        self.step, mission = "2", self.mission
        self.act("tianshu", "world_revise_object", {"payload": {"external_refs": [self.mission_ext]},
                                                    "declaration": self.declare(mission, "天枢 Mission 关联本体")},
                 mission, prepare_caption="天枢修订 Mission 的外部引用：先 prepare",
                 caption="再以同一请求体（带上 prepare 返回的 `expected_versions`）提交，返回回执")
        check("the mission carries its external ref", self.read(mission)["business"]["attributes"]["external_refs"]
              == [{**self.mission_ext, "url": None}])
        found = self.listed({"external_system": "tianshu", "external_id": self.mission_ext["id"]},
                            "按外部引用查回本次的 Mission")
        check("the external ref finds the mission", [item["object_id"] for item in found["items"]] == [mission])
        self.passed("2")

    def step3(self):
        self.step, mission = "3", self.mission
        year, week, _ = datetime.now(CST).isocalendar()
        self.week = f"{year}-W{week:02d}"
        scene = self.declare(mission, "天枢每周同步")
        synced = self.act("tianshu", "world_record_event", {
            "category": "other", "subject_refs": [self.ref(mission)], "occurred_at": at(seconds=-60),
            "content": {"text": f"天枢每周同步 {self.week}"}, "declaration": scene},
            key=f"tianshu:weekly-sync:{mission}:{self.week}", caption="先记来源外部事件（`category: other`）")
        self.source_event = synced["event_id"]
        self.issue_id = f"issue:demo-{self.run}-1"
        shot = self.act("tianshu", "world_refresh_state", {"declaration": scene, "payload": {
            "title": f"{self.title} 执行状态 {self.week}", "subject_ref": self.ref(mission), "as_of": at(seconds=-30),
            "period": self.period, "payload_type": "execution_state",
            "source_event_refs": [f"event:{self.source_event}"],
            "blocks": {
                "progress": {"components": [{
                    "id": f"todo:{self.todo}", "type": "progress_item", "text": "接入 0.2 的本周进展",
                    "artifacts": ["https://example.com/tianshu/todo/1", "https://example.com/tianshu/pr/1"],
                    "attributes": {"principal_id": self.pid["eo-owner"], "principal_name": ROLE_NAMES["eo-owner"],
                                   "external_status": "进行中", "entries": [
                                       {"at": at(days=-2), "source": "web", "text": "对齐 0.2 接口变化清单"},
                                       {"at": at(days=-1), "source": "github", "text": "提交接入改动",
                                        "url": "https://example.com/tianshu/pr/1"}]}}]},
                "issues": {"components": [{
                    "id": self.issue_id, "type": "issue", "text": "每周同步里发现的问题",
                    "attributes": {"core_question": "天枢里的指派要不要同步成本体的 Task 指派？",
                                   "responsible_hint": self.pid["eo-owner"]}}]},
                "materials": {"artifacts": ["https://example.com/tianshu/weekly/1"]}}}},
            key=f"tianshu:weekly:{mission}:{self.week}", caption="再写执行状态快照，`source_event_refs` 引用上一步的事件")
        self.snapshot, self.snapshot_ref = shot["object_id"], shot["ref"]
        state = self.get(f"/v1/world/objects/{mission}/state", "取状态：最新快照，带生成者、来源事件与未经确认标记")
        snap = state.get("snapshot") or {}
        check("the snapshot reads back with its source event, generator and unconfirmed mark",
              snap.get("object_id") == self.snapshot and snap["payload_type"]["id"] == "execution_state"
              and snap["unconfirmed"] is True and snap["generator"]["principal_id"] == self.pid["tianshu"]
              and [r["event_id"] for r in snap["source_event_refs"]] == [self.source_event], str(state)[:300])
        self.passed("3")

    def step4(self):
        self.step, mission = "4", self.mission
        met = self.act("tianshu", "world_record_event", {
            "category": "meeting", "subject_refs": [self.ref(mission), self.ref(self.goal)], "occurred_at": at(hours=-2),
            "content": {"text": "E&O 周会：确认本周计划与问题", "artifacts": ["https://example.com/tianshu/minutes/1"]},
            "declaration": self.declare(mission, "天枢同步会议纪要")},
            caption="记会议事件（`category: meeting`，补记两小时前的会，主体可以多个）")
        events = self.get(f"/v1/world/objects/{mission}/events", "取事件：按发生时刻升序，补记的会标迟记")
        row = next((e for e in events["events"] if e["event_id"] == met["event_id"]), None)
        check("the meeting reads back as a late external event on the mission",
              row is not None and row["kind"] == "event.recorded" and row["category"] == "meeting" and row["late"] is True,
              str(row)[:300])
        self.passed("4")

    def step5(self):
        self.step = "5"
        valid_until = at(hours=1)
        for person, families in (("ceo", ["gate"]), ("eo-dri", ["gate"]),
                                 ("eo-owner", ["gate", "assign", "lifecycle", "issue"]), ("eo-ic", ["lifecycle"])):
            granted = self.act(person, "world_grant_delegation", {
                "delegate_principal_id": self.pid["tianshu"], "families": families,
                "domain_ids": [self.domain["eo"]], "valid_until": valid_until},
                caption=f"{ROLE_NAMES[person]} 本人登记委托（{'、'.join(families)}，E&O 域，一小时）")
            self.delegations[person] = [granted["event_id"]]
        in_force = {d["event_id"] for d in self.read(self.goal)["identity"]["delegations"]}
        check("the period goal lists the four delegations in force",
              {e for events in self.delegations.values() for e in events} <= in_force)
        since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")  # 到微秒：同一秒里此前的事件不算
        self.act("tianshu", "world_commit_period_goal", {"on_behalf_of": self.behalf("eo-dri", "plan-submit")}, self.goal,
                 caption="代 E&O DRI 承诺周期目标（月度计划提交）")
        self.expect("period goal committed on behalf of the DRI", self.goal, "committed")
        self.act("tianshu", "world_confirm_period_goal", {"outcome": "accepted",
                                                          "on_behalf_of": self.behalf("ceo", "plan-sign")}, self.goal,
                 caption="代 CEO 确认周期目标（月度计划签发）")
        self.expect("period goal confirmed on behalf of the CEO", self.goal, "confirmed")
        self.act("tianshu", "world_commit_mission", {"on_behalf_of": self.behalf("eo-owner", "card-submit")}, self.mission,
                 caption="代 Mission Owner 承诺 Mission（任务卡提交）")
        self.act("tianshu", "world_confirm_mission", {"outcome": "accepted",
                                                      "on_behalf_of": self.behalf("eo-dri", "card-confirm")}, self.mission,
                 caption="代 E&O DRI 确认 Mission（任务卡确认）")
        self.expect("mission established on behalf of the DRI", self.mission, "established")
        events = self.get(f"/v1/world/objects/{self.mission}/events?since={since}",
                          "取 Mission 的事件（`since` 之后）：记录者是天枢，另有被代记的人与外部确认记录")
        gates = [e for e in events["events"] if e["kind"] in ("commit", "confirm")]
        check("both mission gates are recorded by tianshu on behalf of the owner and the DRI",
              [(e["principal"]["principal_id"], e["on_behalf_of"]["principal_id"]) for e in gates]
              == [(self.pid["tianshu"], self.pid["eo-owner"]), (self.pid["tianshu"], self.pid["eo-dri"])]
              and all(e["external_confirmation"] for e in gates), str(gates)[:400])
        self.passed("5")

    def step6(self):
        self.step, mission = "6", self.mission
        self.plan_item = {"id": f"todo:{self.todo}", "type": "plan_item", "text": "示例：接入改动（天枢执行事项）",
                          "attributes": {"responsible": self.pid["eo-ic"]}}
        self.act("tianshu", "world_revise_object", {
            "payload": {"blocks": {"execution_plan": {"components": [self.plan_item]}}},
            "declaration": self.declare(mission, "天枢同步执行事项到执行计划")}, mission,
            caption="天枢修订 Mission 的执行计划块：计划条目的组件 id 用执行事项 id，`responsible` 填执行人")
        plan = smoke.blocks(self.read(mission))["execution_plan"]["components"]
        check("tianshu's plan item reads back with the executor as its responsible",
              [(c["id"], c["type"], c["attributes"]["responsible"]) for c in plan]
              == [(self.plan_item["id"], "plan_item", self.pid["eo-ic"])])
        self.expect("the mission stays established (an activity block does not go through the gate)",
                    mission, "established")
        self.passed("6")

    def step7(self):
        self.step, eo = "7", self.domain["eo"]
        todo = {"system": "tianshu", "id": f"todo:{self.todo}"}
        made = self.act("eo-dri", "world_create_object", {"domain_id": eo, "object_type": "Task", "payload": {
            "title": f"{self.title} Task", "parent_ref": self.ref(self.mission), "external_refs": [todo],
            "blocks": {"definition": {"text": "示例：Task 定义"},
                       "acceptance": {"components": [{"id": "t-ac1", "type": "acceptance_criterion", "text": "示例：验收一",
                                                      "refs": [f"{self.ref(self.mission)}#acceptance/m-ac1"]}]},
                       "plan": {"components": [{"id": "tp-1", "type": "plan_item", "text": "示例：先做一段"}]}}}},
            caption="E&O DRI 建 Task，`external_refs` 写同一个执行事项 id")
        found = self.listed({"external_system": "tianshu", "external_id": todo["id"]})
        check("the executive item's id finds the task", [item["object_id"] for item in found["items"]]
              == [made["object_id"]])
        task = self.task = made["object_id"]
        self.expect("task born unassigned", task, "unassigned")
        steps = [("world_assign", "eo-owner", {"principal_id": self.pid["eo-ic"]}, "assigned", "代 Mission Owner 指派执行人"),
                 ("world_start", "eo-ic", {}, "in_progress", "代执行人开始"),
                 ("world_deliver", "eo-ic", {"content": {"text": "执行事项完成"}}, "delivered", "代执行人交付（执行事项完成）"),
                 ("world_reject", "eo-owner", {"content": {"text": "打回：缺验收材料"}}, "adjusting",
                  "代 Mission Owner 打回，Task 进入调整中"),
                 ("world_deliver", "eo-ic", {"content": {"text": "补齐验收材料后再交付"}}, "delivered", None),
                 ("world_accept", "eo-owner", {}, "closed", "代 Mission Owner 验收通过，Task 进入已关闭"),
                 ("world_reopen", "eo-owner", {"content": {"text": "重开：验收后发现遗漏"}}, "in_progress",
                  "代 Mission Owner 重开，Task 回到进行中"),
                 ("world_deliver", "eo-ic", {}, "delivered", None),
                 ("world_accept", "eo-owner", {}, "closed", None)]
        for action, person, params, status, caption in steps:
            self.act("tianshu", action, {**params, "on_behalf_of": self.behalf(person, action.removeprefix("world_"))},
                     task, caption=caption)
            self.expect(f"task {action} on behalf of {person}", task, status)
        self.passed("7")

    def step8(self):
        self.step, mission = "8", self.mission
        item = {"id": f"plan:demo-{self.run}-1", "type": "plan_item", "text": "示例：联调与验收",
                "attributes": {"responsible": self.pid["eo-owner"]}}
        self.act("eo-coagent", "world_revise_object", {
            "payload": {"blocks": {"execution_plan": {"text": "按周会结论更新", "components": [item]}}},
            "declaration": self.declare(mission, "按周会结论更新执行计划")}, mission,
            caption="Co-Agent 修订 Mission 的执行计划块：补丁里只有新的一条")
        plan = smoke.blocks(self.read(mission))["execution_plan"]["components"]
        check("the plan keeps tianshu's item and appends the co-agent's, each with its responsible",
              [(c["id"], c["attributes"]["responsible"]) for c in plan]
              == [(self.plan_item["id"], self.pid["eo-ic"]), (item["id"], self.pid["eo-owner"])])
        self.expect("the mission stays established", mission, "established")
        self.passed("8")

    def step9(self):
        self.step, mission = "9", self.mission
        since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        issue_ref = f"{self.snapshot_ref}#issues/{self.issue_id}"
        declared = self.declare(mission, "每周同步发现的问题")

        def issue(action, params, status, caption=None, person=None):
            if person:
                params = {**params, "on_behalf_of": self.behalf(person, action.removeprefix("world_"))}
            result = self.act("tianshu", action, {"issue_ref": issue_ref, **params}, caption=caption)
            check(f"{action} leaves the issue {status}", result["issue"]["status"] == status, str(result)[:300])
            return result

        issue("world_raise_issue", {"content": {"text": "每周同步提出"}, "declaration": declared}, "pending_routing",
              "天枢提出问题（写入声明必带）")
        issue("world_route_issue", {"to_principal_id": self.pid["eo-owner"], "declaration": declared}, "routed",
              "天枢把问题路由给 E&O Mission Owner")
        owned = issue("world_own_issue", {}, "owned", "代承接人承接（不带写入声明）", "eo-owner")
        check("the delegated ownership names the owner and the issue-family delegation it used",
              owned["on_behalf_of"]["principal_id"] == self.pid["eo-owner"]
              and owned["on_behalf_of"]["delegation_event_id"] == self.delegations["eo-owner"][0], str(owned)[:300])
        issue("world_return_issue", {"content": {"text": "核心问题要先补齐再路由"}}, "forming", "代承接人退回形成", "eo-owner")
        issue("world_raise_issue", {"content": {"text": "补齐后再提出"}, "declaration": declared}, "pending_routing")
        issue("world_route_issue", {"to_principal_id": self.pid["eo-owner"], "declaration": declared}, "routed")
        issue("world_own_issue", {}, "owned", None, "eo-owner")
        issue("world_dispose_issue", {"disposition": "current_layer_action",
                                      "content": {"text": "本层处理：在执行计划里加一条同步指派的计划条目"}},
              "disposed", "代承接人处置（本层处理，理由写在 `content.text`）", "eo-owner")
        open_issues = self.read(mission)["records"]["open_issues"]
        check("the disposed issue is no longer open on the mission",
              all(item["component_id"] != self.issue_id for item in open_issues))
        events = self.get(f"/v1/world/objects/{mission}/events?since={since}",
                          "取 Mission 的事件（`since` 之后）：议题事件，代记的带被代记的人与外部确认记录")
        kinds = [(e["kind"], (e["on_behalf_of"] or {}).get("principal_id")) for e in events["events"]
                 if e["kind"].startswith("issue.")]
        owner = self.pid["eo-owner"]
        check("the issue events read back in order, the delegated ones on behalf of the owner",
              kinds == [("issue.raised", None), ("issue.routed", None), ("issue.owned", owner), ("issue.returned", owner),
                        ("issue.raised", None), ("issue.routed", None), ("issue.owned", owner),
                        ("issue.disposed", owner)], str(kinds))
        self.passed("9")

    def step10(self):
        self.step, mission = "10", self.mission
        self.caption = "从 Mission 出发取上下文"
        status, context = self.call("POST", f"/v1/world/objects/{mission}/context",
                                    {"question": "这个 Mission 为什么做、做什么、谁负责、现在怎样？"}, who="tianshu")
        layers = context["context_pack"]["layers"] if status == 200 else []
        check("context from the mission is 0.2 and starts at the mission",
              status == 200 and context["contract_version"] == V02 and layers
              and layers[0]["object"]["object_id"] == mission, f"{status} {str(context)[:300]}")
        self.passed("10")

    def step11(self):
        self.step, mission = "11", self.mission
        scene = self.declare(mission, "天枢每周同步")
        self.act("tianshu", "world_refresh_state", {"declaration": scene, "payload": {
            "title": f"{self.title} 执行状态（块放错）", "subject_ref": self.ref(mission), "as_of": at(seconds=-5),
            "period": self.period, "payload_type": "execution_state", "source_event_refs": [f"event:{self.source_event}"],
            "progress": {"text": "块应当放在 payload.blocks 里"}}},
            expect=(422, "INVALID_REQUEST"), prepare_caption="块放在 payload 顶层（应在 `payload.blocks` 里）：422")
        self.act("tianshu", "world_create_object", {"domain_id": self.domain["eo"], "object_type": "Task", "payload": {
            "title": f"{self.title} 天枢建的 Task", "parent_ref": self.ref(mission), "blocks": {}},
            "declaration": scene}, expect=(403, "FORBIDDEN"), prepare_caption="Agent 建对象：403（建对象不在 Agent 面上）")
        self.act("tianshu", "world_revise_object", {"payload": {"external_refs": [UNIT_REF]},
                                                    "declaration": self.declare(self.unit, "天枢能力域关联本体责任单元")},
                 self.unit, expect=(403, "FORBIDDEN"),
                 prepare_caption="天枢改责任单元的外部引用：403（单元没有门，天枢不是它的责任人；单元的外部引用由 E&O 写）")
        self.act("tianshu", "world_assign", {"principal_id": self.pid["eo-owner"],
                                             "on_behalf_of": self.behalf("eo-dri", "assign-owner")}, mission,
                 expect=(403, "FORBIDDEN"),
                 prepare_caption="委托不含该族：E&O DRI 只登记了门，天枢代他指派 Mission Owner 是 403")
        self.act("tianshu", "world_revise_object", {"payload": {"external_refs": [self.mission_ext]},
                                                    "declaration": self.declare(self.goal, "天枢 Mission 关联本体")},
                 self.goal, expect=(409, "INVALID_STATE"),
                 prepare_caption="同一外部引用挂到第二个对象：409（错误信息写出已有这一对的对象）")
        self.passed("11")

    def finish(self):
        self.step = "end"
        first = True
        for person, events in self.delegations.items():
            for event in events:
                self.act(person, "world_revoke_delegation", {"delegation_event_id": event},
                         caption=f"{ROLE_NAMES[person]} 本人撤销委托" if first else None)
                self.revoked.add(event)
                first = False
        left = {d["event_id"] for d in self.read(self.mission)["identity"]["delegations"]}
        check("no delegation of this run is still in force",
              not left & {e for events in self.delegations.values() for e in events})
        self.passed("end")

    def revoke_left(self):
        """运行失败时尽力撤销本次登记、还没撤销的委托（不断言、不记账），免得它们在冒烟 scope 里留到过期。"""
        self.step = None
        for person, events in self.delegations.items():
            for event in set(events) - self.revoked:
                body = {"action_type": "world_revoke_delegation", "contract_version": V02, "target": None,
                        "expected_versions": [], "idempotency_key": f"world-02-examples:{self.run}:cleanup:{event}",
                        "reason": self.reason, "params": {"delegation_event_id": event}}
                status, prepared = self.call("POST", "/v1/actions/prepare", body, person)
                if status == 200:
                    body["expected_versions"] = prepared.get("expected_versions", [])
                    status, _ = self.call("POST", "/v1/actions", body, person)
                print(f"CLEANUP revoke a delegation of {person}: {status}", flush=True)

    # ---------------------------------------------------------------- 原始记录
    def raw(self, commit):
        skeleton = {name: oid for name, oid in self.state["skeleton"].items()}
        return {"format": FORMAT, "run": self.run, "commit": commit, "base_url": self.base,
                "tenant_id": self.ids["tenant_id"],
                "identities": {"scope_id": self.ids["scope_id"], "company_id": self.ids.get("company_id"),
                               "domains": self.ids["domains"], "skeleton": skeleton,
                               "principals": {key: {"principal_id": p["principal_id"], "display_name": p["display_name"],
                                                    "assignments": p.get("assignments", [])}
                                              for key, p in self.ids["principals"].items()}},
                "external_ids": {self.todo: "todo-uuid"},
                "types": self.types, "prep": self.prep, "steps": self.steps, "records": self.records}


# -------------------------------------------------------------------- 脱敏
def _walk(value, visit):
    if isinstance(value, dict):
        for key, item in value.items():
            visit(value, key, item)
            _walk(item, visit)
    elif isinstance(value, list):
        for item in value:
            _walk(item, visit)


def placeholders(raw) -> dict[str, str]:
    """每个 uuid 按它指向什么编成占位，同一次运行里前后一致：scope、域、主体、角色指派与骨架按身份清单，天枢侧的 id
    （执行事项）按 external_ids；其余按出现它的键判种类（对象另按类型），按首次出现的顺序逐类编号。说不出指向什么的
    uuid 一个也不放过：抛 ValueError。"""
    ident = raw["identities"]
    names = {ident["scope_id"]: "<scope>", **{u: f"<{name}>" for u, name in raw.get("external_ids", {}).items()}}
    if ident.get("company_id"):
        names[ident["company_id"]] = "<scope-company-id>"
    for key, domain in ident["domains"].items():
        names[domain] = f"<domain:{key}>"
    for key, principal in ident["principals"].items():
        names[principal["principal_id"]] = f"<principal:{key}>"
        for row in principal.get("assignments", []):
            names[row["assignment_id"]] = f"<assignment:{key}:{row['role']}@{row['domain']}>"
    for name, oid in ident["skeleton"].items():
        names[oid] = "<unit:" + name.removeprefix("unit-") + ">" if name.startswith("unit-") else f"<{name}>"
    names = {k.lower(): v for k, v in names.items()}
    types = {k.lower(): v for k, v in raw.get("types", {}).items()}
    kinds: dict[str, str] = {}

    def visit(parent, key, item):
        if isinstance(item, str) and UUID.fullmatch(item):
            item = item.lower()
            if key in ID_KINDS:
                kinds.setdefault(item, ID_KINDS[key])
            elif key == "object_id" or key.endswith("_object_id"):
                kinds.setdefault(item, "object")
                if key == "object_id" and isinstance(parent.get("object_type"), str):
                    types.setdefault(item, parent["object_type"])

    content = [raw.get("prep", []), raw["records"]]
    _walk(content, visit)
    counters: dict[str, int] = {}
    unknown = set()
    for match in UUID.finditer(json.dumps(content, ensure_ascii=False)):
        found = match.group(0).lower()
        if found in names:
            continue
        kind = SLUGS.get(types[found]) if found in types else kinds.get(found)
        if kind in (None, "object"):
            unknown.add(found)
            continue
        counters[kind] = counters.get(kind, 0) + 1
        names[found] = f"<{kind}-{counters[kind]}>"
    if unknown:
        raise ValueError(f"{len(unknown)} 个 uuid 说不出指向什么（不是身份清单里的，也没有出现在可判种类的键下），不渲染")
    return names


def sanitize(raw):
    """脱敏后的原始记录副本：uuid 换占位、显示名换角色名、运行标记换 <run>、分页游标换 <cursor>。"""
    names = placeholders(raw)
    renames = sorted(((p["display_name"], ROLE_NAMES.get(key, key)) for key, p in raw["identities"]["principals"].items()
                      if p["display_name"] != ROLE_NAMES.get(key, key)), key=lambda pair: -len(pair[0]))
    run = raw["run"]

    def text(value: str) -> str:
        value = UUID.sub(lambda m: names[m.group(0).lower()], value)
        for real, role in renames:
            value = value.replace(real, role)
        value = re.sub(r"cursor=[^&]+", "cursor=<cursor>", value)
        return value.replace(run, "<run>")

    def clean(value, key=None):
        if isinstance(value, dict):
            return {k: clean(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        if isinstance(value, str):
            return "<cursor>" if key == "next_cursor" else text(value)
        return value

    out = copy.deepcopy(raw)
    out["records"] = clean(raw["records"])
    out["prep"] = clean(raw.get("prep", []))
    out["steps"] = clean(raw.get("steps", {}))
    return out


def shorten(value, limits=SHORTEN["default"]):
    """文档里截短过长的返回：长数组留前几项，长文本留开头，截掉的在原处写明。"""
    max_items, keep, max_chars = limits
    if isinstance(value, dict):
        return {k: shorten(v, limits) for k, v in value.items()}
    if isinstance(value, list):
        if len(value) > max_items:
            return [shorten(v, limits) for v in value[:keep]] + [f"……（截去 {len(value) - keep} 项）"]
        return [shorten(v, limits) for v in value]
    if isinstance(value, str) and len(value) > max_chars:
        return value[:max_chars] + f"……（截去 {len(value) - max_chars} 字）"
    return value


def assert_clean(text: str, tokens=(), real_names=()) -> None:
    """渲染结果里不能有凭证、Authorization 头、任何 uuid 与真名。"""
    if any(token and token in text for token in tokens) or re.search(r"(?i)bearer\s|authorization:", text):
        raise ValueError("渲染结果里出现了凭证或 Authorization 头")
    if UUID.search(text):
        raise ValueError("渲染结果里还有 uuid")
    if any(name and name in text for name in real_names):
        raise ValueError("渲染结果里出现了没换掉的显示名")


def pretty(value, indent=0, width=WIDTH) -> str:
    """缩进两格的 JSON，放得进一行的对象与数组不折行（内容与 json.dumps 相同，只是换行少）。"""
    one = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, (dict, list)) or not value or indent + len(one) <= width:
        return one
    pad = " " * (indent + 2)
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            name = json.dumps(key, ensure_ascii=False) + ": "
            rows.append(pad + name + pretty(item, indent + 2, width - len(name)))
        return "{\n" + ",\n".join(rows) + "\n" + " " * indent + "}"
    return "[\n" + ",\n".join(pad + pretty(item, indent + 2, width) for item in value) + "\n" + " " * indent + "]"


def _json(value) -> list[str]:
    return ["```json", pretty(value), "```"]


def render(raw, commit=None) -> str:
    """把原始记录渲染成给天枢的 Markdown（纯函数，不连服务）。"""
    if raw.get("format") != FORMAT:
        raise ValueError(f"不是 {FORMAT} 的原始记录")
    clean = sanitize(raw)
    commit = commit or raw.get("commit") or "未注明"
    host = urlsplit(raw.get("base_url", "")).hostname or ""
    where = "本机隔离栈" if host in ("127.0.0.1", "localhost", "::1") else f"`{raw['base_url']}`"
    lines = [
        "# tkos.world/0.2 给天枢的实测请求与返回示例",
        "",
        "对象：天枢服务端接入本体的工程师。本文是《接口变化清单》（`docs/world-v02-tianshu-interface-changes.md`）"
        "承诺的实测示例，按天枢的接入顺序分节，每节对应清单的一项或几项。",
        "",
        f"**怎么生成的**：`deploy/world-02/examples.py` 在冒烟 scope 上按下面的顺序逐步真打 HTTP 接口，每一步断言返回码与"
        f"关键字段，记下全部请求与返回，再脱敏渲染成本文。本文以服务提交 `{commit}`、{where}上的运行 `{raw['run']}`"
        "（下文写作 `<run>`）为准；实例重建或契约改动后按 `deploy/world-02/README.md` 第 7 节后的说明重新生成。",
        "",
        "**占位规则**：",
        "",
        "- 同一次运行里的每个 id 按它指向什么换成占位，前后一致：`<scope>`；域 `<domain:eo>`；主体 `<principal:tianshu>`"
        "（冒号后是冒烟名单的主体键）；骨架对象 `<company>`、`<strategy>`、`<unit:eo>`；本次新建的对象按类型编号，如 "
        "`<mission-1>`、`<task-1>`、`<snapshot-1>`；事件 `<event-3>`、修订 `<revision-5>`、回执 `<receipt-2>`（事件的 "
        "`action_id` 就是产生它的回执 id）、上下文包 `<context-pack-1>`、角色指派 `<assignment:tianshu:AGENT@eo>`。"
        "引用里的 id 同样替换，例如 `<mission-1>@3#acceptance/m-ac1`、`event:<event-3>`。真实接口里这些位置都是 uuid。"
        "天枢执行事项的 id 在请求里是真 uuid（`todo:` 加 uuid），本文写作 `todo:<todo-uuid>`。",
        "- 时刻是实跑时刻，格式原样：请求里按天枢的习惯写 `+08:00`，返回里服务统一给 UTC。",
        "- 显示名一律是冒烟名单的角色名；分页游标写作 `<cursor>`，真实值是不透明字符串，原样带回即可。",
        "- 「身份」是这个请求带哪个主体的凭证（放在请求头里，本文不列出凭证）。天枢的请求都用天枢服务主体的凭证；"
        "人本人的请求（登记委托、建 Task 等）只是为了示例完整。",
        "- 过长的返回截短了：超过 {0} 项的数组留前 {1} 项，超过 {2} 字的文本留开头（取上下文一节更紧：超过 {3} 项留 {4} "
        "项，文本留 {5} 字），截掉的在原处写明「截去 N 项」或「截去 N 字」。请求都是完整的。".format(
            *SHORTEN["default"], *SHORTEN[CONTEXT_STEP]),
        "",
        "**各步结果**：",
        "",
        "| 节 | 内容 | 结果 |",
        "|-|-|-|",
    ]
    for number, title, _ in STEPS:
        row = clean["steps"].get(number)
        state = "未跑" if row is None else {"passed": "通过"}.get(row["status"], row["status"])
        lines.append(f"| {'' if number in ('prep', 'end') else number} | {title} | {state} |")
    for number, title, intro in STEPS:
        heading = title if number in ("prep", "end") else f"{number}. {title}"
        lines += ["", f"## {heading}", "", intro, ""]
        row = clean["steps"].get(number)
        if number == "prep":
            for item in clean["prep"]:
                lines.append(f"- `{item['who']}`（{ROLE_NAMES.get(item['who'], item['who'])}）{item['text']}："
                             f"`{item['object_id']}`")
        shown = [r for r in clean["records"] if r["step"] == number and r["caption"]]
        for record in shown:
            who = record["who"]
            identity = f"身份：`{who}`（{ROLE_NAMES.get(who, who)}）" if who else "不带凭证"
            lines += [f"**{record['caption']}**", "", f"`{record['method']} {record['path']}`，{identity}", ""]
            if record["body"] is not None:
                lines += _json(record["body"]) + [""]
            limits = SHORTEN.get(number, SHORTEN["default"])
            lines += [f"返回 `{record['status']}`：", ""] + _json(shorten(record["response"], limits)) + [""]
        if row is None:
            lines += ["本节没有跑到。", ""]
    return "\n".join(lines).rstrip() + "\n"


# -------------------------------------------------------------------- 入口
def run(args) -> None:
    examples = Examples(args.base_url, args.credentials)
    examples.load_state()  # 只认 -smoke 结尾的 tenant，别的 scope 在发出任何请求之前 FAIL
    examples.basics()
    args.out.mkdir(parents=True, exist_ok=True)
    raw_path = args.out / f"examples-{examples.run}.json"
    failed = True
    try:
        company, _ = examples.skeleton()
        examples.prepare_chain(company)
        for number in STEP_IDS[1:-1]:
            getattr(examples, f"step{number}")()
        examples.finish()
        failed = False
    finally:
        if failed:
            examples.revoke_left()
        raw = examples.raw(args.commit)
        text = json.dumps(raw, ensure_ascii=False, indent=1)
        tokens = list(examples.tokens.values())
        if any(token in text for token in tokens):
            raise SystemExit("FAIL 原始记录里出现了凭证，不写")
        raw_path.write_text(text, encoding="utf-8")
        os.chmod(raw_path, 0o600)
        print(f"RAW {raw_path}" + ("（运行失败，记到失败处为止）" if failed else ""), flush=True)
    doc = args.doc or args.out / "world-v02-tianshu-examples.md"
    rendered = render(raw, args.commit)
    real = [p["display_name"] for key, p in raw["identities"]["principals"].items()
            if p["display_name"] != ROLE_NAMES.get(key, key)]
    assert_clean(rendered, tokens, real)
    doc.write_text(rendered, encoding="utf-8")
    print(f"EXAMPLES_OK run={examples.run} doc={doc}")


def render_only(args) -> None:
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    rendered = render(raw, args.commit)
    real = [p["display_name"] for key, p in raw["identities"]["principals"].items()
            if p["display_name"] != ROLE_NAMES.get(key, key)]
    assert_clean(rendered, (), real)
    args.doc.write_text(rendered, encoding="utf-8")
    print(f"RENDERED {args.doc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    go = sub.add_parser("run", help="在冒烟 scope 上实跑并渲染")
    go.add_argument("base_url")
    go.add_argument("credentials", type=Path, help="冒烟 scope 的 ids.json 与 <键>.token 所在目录")
    go.add_argument("out", type=Path, help="原始记录的输出目录（不入库）")
    go.add_argument("--commit", help="实例构建所用的提交，写进文档开头")
    go.add_argument("--doc", type=Path, help="渲染到哪里，默认 <输出目录>/world-v02-tianshu-examples.md")
    again = sub.add_parser("render", help="只从原始记录重新渲染，不连服务")
    again.add_argument("raw", type=Path)
    again.add_argument("--doc", type=Path, required=True)
    again.add_argument("--commit")
    args = parser.parse_args(argv)
    (run if args.mode == "run" else render_only)(args)


if __name__ == "__main__":
    main()
