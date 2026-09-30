"""对照实验 B 试用回放的端到端预演（票 #75）：隔离库上仿一个真实 scope、在 mission_context 上仿天枢的写法造一段试用
记录，转写，再按转写的内容在两条线上回放，全程只用实验本身的代码。

    python -m experiments.world_v02.b_rehearse --env-file E --private P --output O

1. 仿真实 scope：按 deploy/world-02/spec.eo.example.json 的名单（人、天枢服务主体、Co-Agent、执行 Agent；键与显示名都是
   角色名）经 owner SQL 供给一个随机 tenant 的 scope，控制面 CLI 装 0.2（同 b_seed.provision），起真 API；用
   deploy/world-02/seed_eo.py 按人分段播 E&O 十月起点（seed-eo-2026-10.json；委托的到期时刻已过或不到一天时顺延一周）。
2. 试用记录（Trial）：请求形状照 deploy/world-02/examples.py，块按 Content Pact——天枢写 Mission 的外部引用与执行计划
   （Task 全景）的 todo 计划条目；天枢代记月度计划签发与任务卡确认（第一次退回）；E&O DRI 建三个带外部引用的 Task（任务
   定义块三种写法；一个带 Task 计划块与 Activity 全景块，计划条目带 plan_item 的可选属性）；天枢代 Mission Owner
   指派，以 Owner 的 Agent 开始 Mission，代执行人开始、交付，代 Owner 打回、验收、重开、取消；Task 下一个 Activity（执行
   事项、预期产出与验收标准组件）交给执行 Agent（开始、写进展、交付，Task 责任人验收）；天枢的周快照（三条进展条目、一个问题）与会议事件；议题由天枢提出、
   路由，代承接人承接、退回形成、处置；Co-Agent 在 Task 上以自己身份提问题，E&O DRI 本人承接、处置；Task 责任人本人改
   计划条目、写进展快照；Co-Agent 再加一条执行计划；最后天枢代记 Mission 交付与验收。
3. 转写：对照表按名单生成（人：键即角色键；天枢按写入转写；Co-Agent、执行 Agent 钉成同名角色键），以 ceo 的凭证经
   b_transcribe 只读取回并转写。
4. 回放：按 b_spec.json 另开两个 scope，b_seed 播转写的 b-lines.json、读回核对，b_drive 按转写的 b-script.json 驱动、
   取证，b_observe 算五项观测（b_seed.run_lines）。

通过：转写成功（含显示名拦截）；两条线播种核对无问题、Mission 都关闭；Task+Activity 线没有被拒的步骤（仿真实 scope
里记得下的，这条线都记得下：转写是忠实的）；仿真实 scope 里 Mission、每个 Task 与 Activity 的生命周期与两条线读回
一致。--private 下是三个 scope 的凭证、ids.json、真实 scope 的读取原样（bundle.json）与对照表；--output 下是转写产物
（transcript/）、两条线的运行日志、observations.json 与 summary.json，不含凭证与显示名。全部通过退出码为 0。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

from . import b_drive, b_seed, b_transcribe

ROOT = b_seed.ROOT
DEPLOY = ROOT / "deploy" / "world-02"
EO_SPEC = DEPLOY / "spec.eo.example.json"
EO_PLAN = DEPLOY / "seed-eo-2026-10.json"
CST = timezone(timedelta(hours=8))
smoke = b_seed._deploy_module("smoke")
seed_eo = b_seed.seed_eo


def at(**delta) -> str:
    """请求里的时刻按天枢的习惯写 +08:00，到秒。"""
    return (datetime.now(CST) + timedelta(**delta)).isoformat(timespec="seconds")


# ------------------------------------------------------------------ the imitated real scope
def plan_copy(path: Path) -> Path:
    """十月起点的计划原样拷一份；委托的到期时刻已过或不到一天时顺延到一周后（预演可以在十月以后重跑）。"""
    plan = json.loads(EO_PLAN.read_text(encoding="utf-8"))
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    for step in plan["steps"]:
        if step["do"] == "delegate" and datetime.fromisoformat(step["valid_until"]) < soon:
            step["valid_until"] = (datetime.now(CST) + timedelta(days=7)).isoformat(timespec="seconds")
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def seed_real(url: str, out: Path, plan_path: Path) -> dict:
    """按人分段跑 seed_eo.py（ceo、eo-dri、eo-owner 交替），直到计划全部做完；返回它的状态文件。"""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    state_path = out / seed_eo.STATE_FILE
    for _ in range(len(plan["steps"])):
        done = json.loads(state_path.read_text(encoding="utf-8"))["steps"] if state_path.exists() else {}
        if len(done) == len(plan["steps"]):
            return json.loads(state_path.read_text(encoding="utf-8"))
        who = seed_eo.next_up(plan, done)[0][0]
        subprocess.run([sys.executable, str(DEPLOY / "seed_eo.py"), url, str(out), str(plan_path), "--as", who],
                       check=True, stdout=subprocess.DEVNULL)
    raise ValueError("十月起点没有播完")


class Trial(smoke.Smoke):
    """仿天枢的写法在 mission_context 上造一段试用记录（请求形状同 deploy/world-02/examples.py，块按 Content Pact）。
    文字里不出现名单的显示名（它们在这个 scope 里就是角色名），否则转写的显示名拦截会拦下；唯一的例外是一条计划条目的
    执行主体整个就是显示名，转写把它换成角色键。"""

    def __init__(self, url: str, out: Path, state: dict):
        super().__init__(url, out, probe_only=True)
        self.run, self.seq = uuid.uuid4().hex[:8], 0
        self.mission = state["steps"]["mission_context"]["object_id"]
        self.goal = state["steps"]["october_goal"]["object_id"]
        self.made: dict[str, str] = {}

    def declare(self, oid, trigger):
        return {"scene": self.ref(oid), "trigger": trigger, "human_acceptance": {"required": False}}

    def behalf(self, action, params, oid, person, what=None):
        """天枢代 person 记（带 on_behalf_of，不带写入声明）。"""
        self.seq += 1
        on_behalf = {"principal_id": self.pid[person], "external_record_id": f"tianshu:{what or action}:{self.run}-{self.seq:03d}",
                     "external_confirmed_at": at(minutes=-3)}
        return self.act("tianshu", action, {**params, "on_behalf_of": on_behalf}, oid)

    def item(self, key, text, who, **attributes):
        """计划条目：责任人之外可带 plan_item 的四个可选属性（预期产出、质量标准、执行主体、分工）。"""
        return {"id": key, "type": "plan_item", "text": text, "attributes": {"responsible": self.pid[who], **attributes}}

    def snapshot(self, who, subject, trigger, text, as_of, blocks):
        """先记来源外部事件，再写引用它的执行状态快照；Agent 带写入声明。返回快照的回执。"""
        declared = {"declaration": self.declare(subject, trigger)} if self.ids["principals"][who]["type"] == "agent" else {}
        source = self.act(who, "world_record_event", {
            "category": "other", "subject_refs": [self.ref(subject)], "occurred_at": at(seconds=as_of - 20),
            "content": {"text": text}, **declared})
        return self.act(who, "world_refresh_state", {**declared, "payload": {
            "title": text, "subject_ref": self.ref(subject), "as_of": at(seconds=as_of), "payload_type": "execution_state",
            "source_event_refs": [f"event:{source['event_id']}"], "blocks": blocks}})

    def issue(self, who, action, issue_ref, params=None, person=None, declared_on=None):
        params = {"issue_ref": issue_ref, **(params or {})}
        if declared_on:
            params["declaration"] = self.declare(declared_on, "天枢同步议题")
        if person:
            return self.behalf(action, params, None, person)
        return self.act(who, action, params)

    def play(self) -> dict:
        m, g = self.mission, self.goal
        todo = {name: f"todo:{uuid.uuid4()}" for name in ("link", "data", "probe", "notes")}
        # 天枢写 Mission 的外部引用（活动属性，带写入声明）
        self.act("tianshu", "world_revise_object", {
            "payload": {"external_refs": [{"system": "tianshu", "id": f"mission:demo-{self.run}"}]},
            "declaration": self.declare(m, "天枢 Mission 关联本体")}, m)
        # 门：月度计划签发（DRI 承诺、总负责人签发），任务卡确认（Owner 承诺、DRI 确认；第一次退回）
        self.behalf("world_commit_period_goal", {}, g, "eo-dri", "plan-submit")
        self.behalf("world_confirm_period_goal", {"outcome": "accepted"}, g, "ceo", "plan-sign")
        self.behalf("world_commit_mission", {}, m, "eo-dri", "card-submit")
        self.behalf("world_confirm_mission", {"outcome": "returned", "content": {"text": "验收条件要补一条可观测的"}},
                    m, "eo-dri", "card-return")
        self.behalf("world_commit_mission", {}, m, "eo-dri", "card-submit")
        self.behalf("world_confirm_mission", {"outcome": "accepted"}, m, "eo-dri", "card-confirm")
        self.expect("mission established on behalf", m, "established")
        # 天枢写执行计划：计划条目的组件 id 用执行事项 id，responsible 填执行人
        self.act("tianshu", "world_revise_object", {"payload": {"blocks": {"execution_plan": {"components": [
            self.item(todo["link"], "接入 0.2 的读写链路", "eo-owner"),
            self.item(todo["data"], "Context 包的真实数据验收", "eo-dri"),
            self.item(todo["probe"], "临时排查：代记失败的请求", "eo-owner")]}}},
            "declaration": self.declare(m, "天枢同步执行事项到执行计划")}, m)
        # E&O DRI 建 Task：外部引用写同一个执行事项 id。第一个照 examples.py 的写法，任务定义块带文字与指回 Mission
        # 战役定义块验收标准的组件，另带 Task 计划块与 Activity 全景块（计划条目带 plan_item 的可选属性：一条的执行主体
        # 写的是名单上的显示名，一条写的是名单外的 Agent 名）；第二个的任务定义块只有组件；第三个只有文字。
        mission_ref = self.ref(m)
        link, _ = self.create("eo-dri", "Task", "eo", {
            "title": "接入 0.2 的读写链路", "parent_ref": mission_ref,
            "external_refs": [{"system": "tianshu", "id": todo["link"]}],
            "blocks": {"definition": {"text": "天枢按接口变化清单接入 0.2，读写两条链路都跑通。", "components": [
                           {"id": "t-ac1", "type": "acceptance_criterion", "text": "读写链路按接口清单逐项跑通",
                            "refs": [f"{mission_ref}#definition/ac-3"]}]},
                       "task_plan": {"components": [{"id": "t-seq", "type": "execution_sequence",
                                                     "text": "先对齐接口变化清单，再联调写入声明与代记"}]},
                       "plan": {"components": [
                           self.item("tp-1", "对齐接口变化清单", "eo-owner", executor="E&O Mission Owner"),
                           self.item("tp-2", "写入声明与代记联调", "exec-agent", expected_output="代记联调记录",
                                     quality_standard="代记被拒的请求都有原因", executor="Codex",
                                     division="人定联调用例，Agent 跑用例并记结果")]}}})
        data, _ = self.create("eo-dri", "Task", "eo", {
            "title": "Context 包的真实数据验收", "parent_ref": mission_ref,
            "external_refs": [{"system": "tianshu", "id": todo["data"]}],
            "blocks": {"definition": {"components": [
                {"id": "t-outcome", "type": "outcome", "text": "8–9 月真实数据进入 Context"},
                {"id": "t-ac1", "type": "acceptance_criterion", "text": "逐项核对来源与权限"}]}}})
        probe, _ = self.create("eo-dri", "Task", "eo", {
            "title": "临时排查：代记失败的请求", "parent_ref": mission_ref,
            "external_refs": [{"system": "tianshu", "id": todo["probe"]}],
            "blocks": {"definition": {"text": "排查联调里代记被拒的请求。"}}})
        link, data, probe = link["object_id"], data["object_id"], probe["object_id"]
        self.made.update(link=link, data=data, probe=probe)
        # 天枢代 Mission Owner（这条 Mission 是 E&O DRI）指派；以 Owner 的 Agent 开始 Mission；代执行人开始 Task
        self.behalf("world_assign", {"principal_id": self.pid["eo-owner"]}, link, "eo-dri", "assign")
        self.behalf("world_assign", {"principal_id": self.pid["eo-dri"]}, data, "eo-dri", "assign")
        self.behalf("world_assign", {"principal_id": self.pid["eo-owner"]}, probe, "eo-dri", "assign")
        self.act("tianshu", "world_start", {"declaration": self.declare(m, "天枢按任务卡开始执行")}, m)
        self.behalf("world_start", {}, link, "eo-owner", "start")
        self.behalf("world_start", {}, data, "eo-dri", "start")
        # Task 下的 Activity：Task 责任人建一段交给执行 Agent，Agent 开始、写进展
        activity, _ = self.create("eo-dri", "Activity", "eo", {
            "title": "整理真实数据清单", "parent_ref": self.ref(data),
            "blocks": {"instruction": {"components": [
                {"id": "a-work", "type": "work_definition", "text": "列出 8–9 月进入 Context 的数据与来源。"},
                {"id": "a-output", "type": "expected_output", "text": "数据清单"},
                {"id": "a-ac1", "type": "acceptance_criterion", "text": "每个来源有责任人与时间"}]}}})
        activity = self.made["activity"] = activity["object_id"]
        self.act("eo-dri", "world_assign", {"principal_id": self.pid["exec-agent"]}, activity)
        self.act("exec-agent", "world_start", {"declaration": self.declare(activity, "按指派开始整理")}, activity)
        self.snapshot("exec-agent", activity, "整理数据清单", "数据清单初稿", -40, {"progress": {"components": [{
            "id": "draft", "type": "progress_item", "text": "数据清单初稿：已列出十二个来源",
            "attributes": {"principal_id": self.pid["exec-agent"],
                           "entries": [{"at": at(seconds=-45), "source": "codex", "text": "列出十二个来源"}]}}]}})
        # 天枢每周同步：Mission 快照带三条进展条目（两条对得上 Task，一条对不上）与一个问题
        issue_id = f"issue:demo-{self.run}-1"
        weekly = self.snapshot("tianshu", m, "天枢每周同步", "天枢每周同步", -30, {
            "progress": {"text": "本周同步：读写链路在联调，数据验收已开始。", "components": [
                {"id": todo["link"], "type": "progress_item", "text": "接入 0.2 的本周进展",
                 "artifacts": ["https://example.com/tianshu/todo/1", "https://example.com/tianshu/pr/1"],
                 "attributes": {"principal_id": self.pid["eo-owner"], "external_status": "进行中", "entries": [
                     {"at": at(days=-2), "source": "web", "text": "对齐 0.2 接口变化清单"},
                     {"at": at(days=-1), "source": "github", "text": "提交接入改动", "url": "https://example.com/tianshu/pr/1"}]}},
                {"id": todo["data"], "type": "progress_item", "text": "数据验收的本周进展",
                 "attributes": {"principal_id": self.pid["eo-dri"],
                                "entries": [{"at": at(days=-1), "source": "other", "text": "开始核对来源"}]}},
                {"id": todo["notes"], "type": "progress_item", "text": "周会纪要整理",
                 "attributes": {"principal_id": self.pid["eo-owner"]}}]},
            "issues": {"components": [{"id": issue_id, "type": "issue", "text": "每周同步里发现的问题",
                                       "attributes": {"core_question": "天枢里的指派要不要同步成本体的 Task 指派？",
                                                      "responsible_hint": self.pid["eo-owner"]}}]},
            "materials": {"artifacts": ["https://example.com/tianshu/weekly/1"]}})
        # 会议事件（补记两小时前的会，不转写）
        self.act("tianshu", "world_record_event", {
            "category": "meeting", "subject_refs": [self.ref(m), self.ref(g)], "occurred_at": at(hours=-2),
            "content": {"text": "周会：确认本周计划与问题"}, "declaration": self.declare(m, "天枢同步会议纪要")})
        # 议题：天枢提出、路由；代承接人承接、退回形成；再提出、路由、承接、处置
        ref = f"{weekly['ref']}#issues/{issue_id}"
        owner = self.pid["eo-owner"]
        self.issue("tianshu", "world_raise_issue", ref, {"content": {"text": "每周同步提出"}}, declared_on=m)
        self.issue("tianshu", "world_route_issue", ref, {"to_principal_id": owner}, declared_on=m)
        self.issue("tianshu", "world_own_issue", ref, person="eo-owner")
        self.issue("tianshu", "world_return_issue", ref, {"content": {"text": "核心问题要先补齐再路由"}}, person="eo-owner")
        self.issue("tianshu", "world_raise_issue", ref, {"content": {"text": "补齐后再提出"}}, declared_on=m)
        self.issue("tianshu", "world_route_issue", ref, {"to_principal_id": owner}, declared_on=m)
        self.issue("tianshu", "world_own_issue", ref, person="eo-owner")
        self.issue("tianshu", "world_dispose_issue", ref, {
            "disposition": "current_layer_action", "content": {"text": "本层处理：执行计划里加一条同步指派的计划条目"}},
            person="eo-owner")
        # Task 责任人本人改计划：一段改责任人（整条替换，照旧带执行主体），加一段
        self.act("eo-owner", "world_revise_object", {"payload": {"blocks": {"plan": {"components": [
            self.item("tp-1", "对齐接口变化清单", "eo-dri", executor="E&O Mission Owner"),
            self.item("tp-3", "补写周快照的来源事件", "exec-agent", expected_output="带来源事件的周快照")]}}}},
            link)
        # Co-Agent 以自己身份在 Task 上提问题（只带问题的快照），路由给 E&O DRI；DRI 本人承接、处置
        question = f"issue:demo-{self.run}-2"
        raised = self.snapshot("eo-coagent", link, "联调中发现问题", "联调中发现代记的委托范围问题", -15, {
            "issues": {"components": [{"id": question, "type": "issue", "text": "代记的委托范围要不要覆盖公司域",
                                       "attributes": {"core_question": "天枢的委托要不要覆盖公司域？"}}]}})
        ref = f"{raised['ref']}#issues/{question}"
        self.issue("eo-coagent", "world_raise_issue", ref, declared_on=link)
        self.issue("eo-coagent", "world_route_issue", ref, {"to_principal_id": self.pid["eo-dri"]}, declared_on=link)
        self.issue("eo-dri", "world_own_issue", ref)
        self.issue("eo-dri", "world_dispose_issue", ref, {"disposition": "no_action_close",
                                                          "content": {"text": "按现有委托范围即可"}})
        # 执行人本人写 Task 的进展快照
        self.snapshot("eo-owner", link, "", "对齐完成，联调中", -5, {"progress": {"text": "对齐完成，联调中。"}})
        # Activity 交付（执行 Agent），Task 责任人验收
        self.act("exec-agent", "world_deliver", {"content": {"text": "数据清单"},
                                                 "declaration": self.declare(activity, "整理完成")}, activity)
        self.act("eo-dri", "world_accept", {}, activity)
        # Task 的交付、打回、验收、重开，取消（天枢代记）
        self.behalf("world_deliver", {"content": {"text": "执行事项完成"}}, link, "eo-owner", "deliver")
        self.behalf("world_reject", {"content": {"text": "打回：缺验收材料"}}, link, "eo-dri", "reject")
        self.behalf("world_deliver", {"content": {"text": "补齐验收材料后再交付"}}, link, "eo-owner", "deliver")
        self.behalf("world_accept", {}, link, "eo-dri", "accept")
        self.behalf("world_reopen", {"content": {"text": "重开：验收后发现遗漏"}}, link, "eo-dri", "reopen")
        self.behalf("world_deliver", {}, link, "eo-owner", "deliver")
        self.behalf("world_accept", {}, link, "eo-dri", "accept")
        self.behalf("world_deliver", {}, data, "eo-dri", "deliver")
        self.behalf("world_accept", {}, data, "eo-dri", "accept")
        self.behalf("world_cancel", {"content": {"text": "并入接入任务"}}, probe, "eo-dri", "cancel")
        # Co-Agent 再加一条执行计划（不转写）
        self.act("eo-coagent", "world_revise_object", {"payload": {"blocks": {"execution_plan": {"components": [
            self.item(f"plan:demo-{self.run}-1", "联调验收", "eo-dri")]}}},
            "declaration": self.declare(m, "按周会结论更新执行计划")}, m)
        # Mission 交付、验收（天枢代 Owner、代 DRI）
        self.behalf("world_deliver", {"content": {"text": "本月闭环跑通"}}, m, "eo-dri", "deliver")
        self.behalf("world_accept", {}, m, "eo-dri", "accept")
        self.expect("mission closed on behalf of the DRI", m, "closed")
        return {"mission": m, **self.made}


def principal_table(ids: dict) -> dict:
    """仿真实 scope 的对照表：人的键就是角色键；天枢按写入转写；Co-Agent 与执行 Agent 钉成同名角色键。"""
    pinned = {"eo-coagent": "eo-coagent", "exec-agent": "exec-agent"}
    return {"format": b_transcribe.TABLE_FORMAT, "principals": {
        key: {"principal_id": item["principal_id"], "display_name": item["display_name"],
              "role": key if item["type"] == "human" else pinned.get(key, "agent")}
        for key, item in ids["principals"].items()}}


# ------------------------------------------------------------------ rehearsal
def rehearse(env_file: Path, private: Path, output: Path) -> dict:
    from acceptance.method_independent.harness import MethodHarness
    from acceptance.protocol_a1_independent.support import public_json

    if private.exists() or output.exists():
        raise ValueError("--private 与 --output 用新的路径")
    private.mkdir(parents=True, mode=0o700)
    source = (ROOT / "src").resolve()
    h = MethodHarness(env_file.resolve(), output.resolve(), private.resolve())
    try:
        real = private / "real"
        ids = b_seed.provision(h, source, json.loads(EO_SPEC.read_text(encoding="utf-8")), "real", real)
        _, url, _ = h.start_api(source)
        state = seed_real(url, real, plan_copy(private / "seed-eo-plan.json"))
        made = Trial(url, real, state).play()
        # 转写：对照表与读取原样放 --private，转写产物放 --output
        table_path = private / "principals.json"
        b_transcribe.private_json(table_path, principal_table(ids))
        bundle = b_transcribe.fetch(b_transcribe.Reader(url, real / "ceo.token"), made["mission"])
        b_transcribe.private_json(private / "bundle.json", bundle)
        transcript = output / "transcript"
        written = b_transcribe.write(bundle, json.loads(table_path.read_text(encoding="utf-8")), transcript)
        # 回放
        outs = {name: private / name for name in b_drive.LINES}
        spec = json.loads(b_seed.SPEC_FILE.read_text(encoding="utf-8"))
        for name, out in outs.items():
            b_seed.provision(h, source, spec, name, out)
        script = json.loads((transcript / "b-script.json").read_text(encoding="utf-8"))
        summary = b_seed.run_lines(url, outs, script, transcript / "b-lines.json")
        logs = {name: b_drive.load_log(out) for name, out in outs.items()}
        for name, log in logs.items():
            public_json(output / f"{name}.json", b_seed.strip_pending(log))
        public_json(output / "observations.json", summary["observations"])
        checks = verdicts(bundle, written, logs, summary)
        result = {"passed": all(checks.values()), "checks": checks,
                  **{key: summary[key] for key in ("missions_closed", "seed_problems", "expressions", "conclusion")},
                  "transcript": {"steps": len(script["steps"]), "tasks": len(written["lines"]["tasks"]),
                                 "initial_segments": sum(len(item["segments"]) for item in written["lines"]["tasks"]),
                                 "planned_segments": sum(1 for step in script["steps"] if step["do"] == "plan"),
                                 "skipped": len(written["skipped"]), "warnings": len(written["warnings"])},
                  "scopes": {"real": ids["scope_id"], **{name: log["scope_id"] for name, log in logs.items()}}}
        public_json(output / "summary.json", result)
        return result
    finally:
        h.close()


def verdicts(bundle: dict, written: dict, logs: dict, summary: dict) -> dict:
    """预演的判定：播种核对、Mission 关闭、Task+Activity 线没有被拒、生命周期与仿真实 scope 一致。"""
    # 真实对象 id → 转写里的键（mission、task_n、segment:<段>）→ 仿真实 scope 里读回的生命周期
    real = {key: (bundle["objects"][object_id]["latest"]["records"]["lifecycle"] or {}).get("status")
            for object_id, key in written["objects"].items()}

    def replayed(log):
        """一条线上同一批对象读回的生命周期（Task-only 线没有 Activity）。"""
        ids = {("mission" if key == log["mission"] else key): item["object_id"] for key, item in log["objects"].items()}
        return {key: log["evidence"]["objects"][ids[key]]["lifecycle"]["status"] for key in real if key in ids}
    lifecycles = {name: replayed(log) for name, log in logs.items()}
    return {
        "seed_read_back": not any(summary["seed_problems"].values()),
        "missions_closed": all(summary["missions_closed"].values()),
        "task_activity_rejects_nothing": all(record["expression"] != "rejected"
                                             for record in logs["task_activity"]["steps"]),
        "lifecycles_match_the_real_scope": lifecycles["task_activity"] == real and all(
            value == real[key] for line in lifecycles.values() for key, value in line.items()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = rehearse(args.env_file, args.private, args.output)
    except (b_transcribe.TranscribeError, ValueError) as exc:
        print(f"FAIL {exc}", flush=True)
        sys.exit(1)
    print(json.dumps({key: result[key] for key in ("passed", "checks", "expressions", "conclusion", "transcript")},
                     ensure_ascii=False))
    if not result["passed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
