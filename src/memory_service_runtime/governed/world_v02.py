"""tkos.world/0.2 的动作执行，复用内核的授权、乐观并发、回执与同事务写入。

执行工厂按请求声明的契约版本先认领 0.2（0.1 只认动作名）。每个 world 动作在同一事务里写
对象修订、恰好一条 world 事件与回执（契约第 8.1 节）；0.2 的事件行写明契约版本，回执结果也写明，
重放与读回执据此分派。授权先于协议错误：先按激活策略判权，Agent 不在建对象的 Agent 面上，再过协议
闸门；然后校验载荷，按主干判断调用者是不是有权的责任人（从新对象的主干上一级找起，同 0.1），之后才
钉定其余引用、核对对象放在哪个域与挂在谁下面、写入声明。已接通八类业务对象的建对象、合并修订与建关系，
写状态快照与记外部事件，指派与 Task、Activity 的六个生命周期动作（#53），周期目标、长期目标与 Mission 立项的
承诺与确认（#54），Mission 的六个生命周期动作与关注标记（#55），委托的登记与撤销与代记（#62）；其余门随各自的票
接入。

指派、生命周期动作与门的判权（契约第 9.2 节）：激活策略列角色（门按目标类型拆名，ADR-0005），之后由服务算出
调用者对目标满足的记录者类别（self、self_or_agent、parent、gate_role）与守卫事实，连同目标的事件交给生命周期引擎
admit 判状态、守卫与记录者。记录者不符是 FORBIDDEN，状态表或守卫不允许是 INVALID_STATE。关注标记是记录事件，
但和门一样只由持策略角色（CEO）的人记，走门的判权，写入时只置 core_battle。

代记（契约第 14 节）：受托的服务主体以自己的凭证记可代记的动作，另带 on_behalf_of。判权不改内核机制，只换判谁：
先按调用者核对委托（服务主体本人、当前有效、动作族与目标所在域在范围内），再把身份上下文换成被代记的人——带他当前
的角色指派——走他本人记时的同一条判权路径（内核的 authorize_domain 对照激活策略，服务算记录者类别与责任关系）；
最终复核同样先核委托、再按被代记的人复核。回执与事件的记录者、幂等与重放仍是调用者本人，事件另写被代记的人与外部
确认记录。

有门对象的修订规则（契约第 12 节）：正式块、正式属性与建对象时写的关系只在草稿直接修订；有了正式内容以后经一轮
重走改——承诺或长期目标的确认带候选（合并补丁），确认接受时写回，活动内容取写回时的当前值。活动块与活动属性在
终态之外直接修订，下级责任人与在对象所在域持 AGENT 的 Agent 也可以。
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterator
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, protocol
from . import world_v02_models as models
from . import world_v02_profile as world_profile
from . import world_v02_lifecycle as world_lifecycle
from . import world_v02_registry as world_registry
from .errors import GovernedError
from .service import ActionExecution
from .world_v01_readers import holds_role
from .world_v02_readers import (cited, delegations_in_force, head_and_binding, judged_context, lifecycle,
                                lifecycle_events)


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _invalid(message: str) -> None:
    _fail("INVALID_REQUEST", message, 422)


class WorldExecution(ActionExecution):
    @classmethod
    def handles_request(cls, conn, ctx, request) -> bool:
        return request.contract_version == models.CONTRACT_VERSION and request.action_type in models.ACTION_PARAMS

    def authorize(self) -> None:
        self.pins: dict[str, dict[str, Any]] = {}
        self.referenced: dict[str, dict[str, Any]] = {}
        # 调用者经哪个对象的 responsible 属性成为责任人；重放时复核该属性仍指向他（契约第 3.3 节）。
        self.responsible_through: str | None = None
        # 事件 subject_refs 里除新修订外还要钉的：建关系的列表、写快照时的主体。
        self.event_subjects: list[dict[str, Any]] = []
        self.declaration: dict[str, Any] | None = None
        # 代记（契约第 14 节）：被代记的人与外部确认记录，以及让这条写入成立的那条委托；不代记时都为 None。
        self.on_behalf: dict[str, Any] | None = self.params.get("on_behalf_of")
        self.delegation: dict[str, Any] | None = None
        if self.on_behalf is not None:
            self.authorize_on_behalf()
        else:
            self.authorize_recording()

    def authorize_recording(self) -> None:
        """按动作分派判权；代记时由 authorize_on_behalf 换成被代记的人再走这里。"""
        if self.kind == "world_create_object":
            self.authorize_create()
        elif self.kind == "world_refresh_state":
            self.authorize_refresh_state()
        elif self.kind == "world_record_event":
            self.authorize_record_event()
        elif self.kind in models.DELEGATION_ACTIONS:
            self.authorize_delegation()
        elif self.kind in {"world_revise_object", "world_relate"}:
            self.authorize_target()
        elif self.kind in models.GATE_ACTIONS:
            self.authorize_gate()
        else:
            self.authorize_responsibility()

    def authorize_create(self) -> None:
        """建对象（契约第 3、9 节）：权限与域放置同 0.1，Company 只由 CEO 本人建；授权之后才暴露协议错误。"""
        object_type = self.object_type = self.params["object_type"]
        self.domain_id = self.params["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        if self.ctx.principal_type != "human":
            _fail("FORBIDDEN", "An Agent does not create objects; creation is not on the Agent face.")
        ceo = [row for row in self.action_assignments if row["role"] == "CEO"]
        if object_type == "Company" and not ceo:
            _fail("FORBIDDEN", "Only the CEO in person records the Company.")
        # 0.2 只在默认契约是 0.2 的域里启用（契约第 1 节）；0.1 的域与没有 world 的域一律 PROTOCOL_NOT_SUPPORTED。
        policy = protocol.creation_policy(self.conn, self.ctx.scope_id, self.domain_id)
        if policy is None or (policy["content"].get("default_protocol"), policy["content"].get(
                "default_contract_version")) != (world_profile.PROTOCOL_ID, models.CONTRACT_VERSION):
            _fail("PROTOCOL_NOT_SUPPORTED", "tkos.world/0.2 is not enabled for this domain.", 409)
        # 状态快照只经 world_refresh_state 写入（契约第 7 节），先于支持登记判。
        if object_type == "StateSnapshot":
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL", "A state snapshot is written by world_refresh_state.")
        self.creation_fields = protocol.resolve_creation(
            self.conn, self.ctx.scope_id, self.domain_id, object_type, self.request.contract_version,
            action_type=self.kind)
        self.protocol_context = self.creation_fields["contract_version"]
        try:
            self.written = models.validate_input(object_type, self.params["payload"])
        except ValueError:
            _invalid("Payload does not satisfy this world 0.2 object type.")
        # 记下让调用者成为责任人的那条指派，重放与最终复核都按它来。
        if object_type == "Company":
            used = sorted(ceo, key=lambda row: row["assignment_id"])[0]
        else:
            field = world_registry.object_spec(object_type)["spine_parent_field"]
            used = self.responsible_up_the_spine(self.pin(self.written[field])["object_id"])
        self.required_assignments.add(used["assignment_id"])
        for text in models.ref_texts(object_type, self.written):
            self.pin(text)
        self.check_placement(object_type)
        self.check_components(object_type)
        self.payload = models.stored_payload(object_type, self.written, self.pins, version=1)
        self.declaration = self.pinned_declaration()

    # ------------------------------------------------------------ revise and relate
    def authorize_target(self) -> None:
        """修订与建关系（契约第 6、9、12 节）：目标须是本 scope 的 world 对象（否则 404），按激活策略判权，
        不在 Agent 面上的动作 Agent 不能做，再过目标动作闸门；然后校验补丁与写入声明，再判调用者能不能改（建关系
        同 0.1；修订按块类别，见 reviser），修订有门对象时最后按生命周期判修订规则（见 check_revisable）。"""
        self.target, _ = head_and_binding(self.conn, self.ctx, self.request.target.object_id)
        self.domain_id = self.target["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        if self.ctx.principal_type != "human" and self.kind not in world_registry.registry()["agent_face"]["writes"]:
            _fail("FORBIDDEN", "This action is not on the Agent face.")
        self.protocol_context = protocol.gate_target_action(
            self.conn, self.ctx.scope_id, self.target["object_id"], self.kind, self.request.contract_version)
        object_type = self.target["object_type"]
        latest = self.target_revision_row()
        self.next_version = latest["object_version"] + 1
        current = latest["payload"]
        if self.kind == "world_revise_object":
            try:
                self.written = models.merge_revision(object_type, current, self.params["payload"])
            except ValueError as exc:
                _invalid(f"The revision does not satisfy this world 0.2 object type: {exc}.")
        self.require_declaration(object_type)
        if self.kind == "world_revise_object":
            used = self.reviser(object_type)
        else:
            used = self.responsible_up_the_spine(self.target["object_id"])
        self.required_assignments.add(used["assignment_id"])
        if self.kind == "world_revise_object":
            self.check_revisable(object_type)
            self.revise(object_type, current)
        else:
            self.relate(object_type, current)
        self.declaration = self.pinned_declaration()

    def reviser(self, object_type: str) -> dict[str, Any]:
        """谁能直接修订（契约第 3.2、12 节）：该对象或其主干上某一级的责任人（同 0.1）；有门对象只改活动块与活动
        属性时，另有下级责任人与在对象所在域持 AGENT 的 Agent（Co-Agent）。返回让调用者有权的那条指派。"""
        used = self.responsible_up_the_spine(self.target["object_id"], required=False)
        if (used is None and world_registry.object_spec(object_type)["gated"]
                and not models.touches_formal(object_type, self.params["payload"])):
            current = db._assignments(self.conn, self.ctx)
            used = next((row for row in current if self.ctx.principal_type == "agent" and row["role"] == "AGENT"
                         and row["domain_id"] == self.domain_id), None) or self.responsible_below(
                self.target["object_id"], current)
        if used is None:
            _fail("FORBIDDEN", "Only a responsible person up the spine writes this object; the activity blocks and "
                               "attributes of a gated object also a responsible below it or an Agent of its domain.")
        return used

    def check_revisable(self, object_type: str) -> None:
        """有门对象的修订规则（契约第 12 节）：终态（已关闭、已取消、已终止）不再直接修订；正式块、正式属性与建对象
        时写的关系只在初始段（草稿）直接修订——已承诺时先由确认人退回，有正式内容后经一轮重走改；活动块与活动属性
        在终态之外都可以直接修订（补 31）。无门对象随时可改（同 0.1）。"""
        spec = world_registry.registry()["lifecycles"].get(object_type)
        if not world_registry.object_spec(object_type)["gated"] or spec is None:
            return
        status = lifecycle(self.conn, self.ctx, self.target)["status"]
        if status in spec["terminal"]:
            _fail("INVALID_STATE", "A closed, cancelled or terminated object is not revised.")
        if models.touches_formal(object_type, self.params["payload"]) and status != spec["initial"]:
            _fail("INVALID_STATE", "Formal blocks and attributes of a gated object are revised directly only while it "
                                   "is a draft: a commitment is returned by its confirmer first, and formal content "
                                   "changes through a re-run of the gate.")

    def require_declaration(self, object_type: str | None = None) -> None:
        """写入声明只对 Agent 强制（契约第 9.3 节）；Agent 的修订触及正式块、正式属性时必须要求人工验收，
        只触及活动块与活动属性时可以不要求（补 18）。"""
        if self.ctx.principal_type == "human":
            return
        given = self.params.get("declaration")
        if given is None:
            _invalid("An Agent write must declare its scene, trigger and human acceptance.")
        if (self.kind == "world_revise_object" and models.touches_formal(object_type, self.params["payload"])
                and not given["human_acceptance"]["required"]):
            _invalid("An Agent revision that touches formal blocks or attributes needs human acceptance and an acceptor.")

    def revise(self, object_type: str, current: dict[str, Any]) -> None:
        """合并修订（契约第 12 节）：建对象时写的关系引用只能改钉到同一对象的另一版本（同 0.1）；
        组件台账记下这一版的新增与删除，只由服务写的字段沿用当前版本。"""
        before = models.written_form(object_type, current)
        for relation in world_registry.object_spec(object_type)["relation_fields"]:
            field = relation["field"]
            if field in before and ([models.parse_ref(text)["object_id"] for text in models.listed(before[field])]
                                    != [models.parse_ref(text)["object_id"] for text in models.listed(self.written[field])]):
                _invalid(f"{field} can only be re-pinned to another version of the object it was created with; "
                         "create a new object to hang it elsewhere.")
        for text in models.ref_texts(object_type, self.written):
            self.pin(text)
        self.check_placement(object_type)
        self.check_components(object_type)
        self.payload = models.stored_payload(object_type, self.written, self.pins, version=self.next_version,
                                             ledger=current["component_ledger"],
                                             server=models.server_fields(object_type, current))

    def relate(self, object_type: str, current: dict[str, Any]) -> None:
        """整体替换一个跨链关系列表（契约第 6 节）：类型按登记，不指向自己；contributes_to 指向另一责任单元的
        周期目标或单元级长期目标（同 0.1）。周期目标的 depends_on 可以指向周期目标或 Mission（补 8）。"""
        field = self.params["field"]
        relation = next((item for item in world_registry.object_spec(object_type)["relation_fields"]
                         if item["field"] == field), None)
        if relation is None:
            _invalid(f"{object_type} has no {field}.")
        for text in self.params["refs"]:
            self.event_subjects.append(self.pin(text))
            target = self.referenced[text]
            if target["object_type"] not in relation["targets"]:
                _invalid(f"{field} must point to one of {', '.join(relation['targets'])}.")
            if target["object_id"] == self.target["object_id"]:
                _invalid("An object cannot relate to itself.")
            company_goal = target["object_type"] == "LongTermGoal" and target["payload"]["scope"] == "company"
            if field == "contributes_to" and (target["domain_id"] == self.domain_id or company_goal):
                _invalid("A contribution goes to a period goal or unit-level goal of another unit.")
        self.payload = models.stored(object_type, {**current, field: self.event_subjects})

    def target_revision_row(self) -> dict[str, Any]:
        return db.jsonable(self.conn.execute(
            "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
            (self.ctx.scope_id, self.target["latest_revision_id"])).fetchone())

    def authorize_refresh_state(self) -> None:
        """写状态快照（契约第 7、9 节）：所在域随主体；人须是主体主干上的责任人，Agent 须在主体所在域持
        AGENT。授权之后才暴露协议错误；生成者按凭证填，来源事件至少一条，payload 类型是主体类型登记的那种。"""
        self.object_type = "StateSnapshot"
        payload = self.params["payload"]
        try:
            subject_id = models.parse_ref(payload.get("subject_ref"))["object_id"]
        except (ValueError, TypeError):
            _invalid("A state snapshot names its subject as <object id>@<version>.")
        subject = head_and_binding(self.conn, self.ctx, subject_id)[0]
        self.domain_id = subject["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        self.open_world_v02(self.object_type)
        try:
            self.written = models.validate_snapshot(payload)
        except ValueError:
            _invalid("The snapshot does not satisfy the world 0.2 state shell or its payload type.")
        self.require_declaration()
        subject_type = self.referenced_type(self.written["subject_ref"])
        if world_registry.object_spec(subject_type)["category"] != "business_object":
            _invalid("The subject of a state snapshot is a business object.")
        if self.written["payload_type"] != models.payload_type_for(subject_type):
            _invalid(f"A {subject_type} snapshot carries the {models.payload_type_for(subject_type)} payload.")
        if self.ctx.principal_type == "agent":
            held = [row for row in self.action_assignments if row["role"] == "AGENT"]
            if not held:
                _fail("FORBIDDEN", "An Agent writes a snapshot only with the AGENT role in the subject's domain.")
            used = held[0]
        else:
            used = self.responsible_up_the_spine(subject_id)
        self.required_assignments.add(used["assignment_id"])
        for text in models.snapshot_ref_texts(self.written):
            self.pin(text)
        self.check_components(self.object_type)
        self.not_in_future(self.written["as_of"], "A snapshot cannot be as of a time that has not come yet.")
        self.payload = models.stored_snapshot(self.written, self.pins, generator=self.ctx.principal_id)
        self.event_subjects = [self.pins[self.written["subject_ref"]]]
        self.declaration = self.pinned_declaration()

    def authorize_record_event(self) -> None:
        """记外部事件（契约第 8、11 节）：按 scope 判权，不看各域策略；主体与内容里的引用钉定；发生时刻可以在过去，
        不能在将来；更正指向本 scope 里登记允许更正的 0.2 事件（外部事件与 Issue 事件）。"""
        db._assignments(self.conn, self.ctx)  # scope 内没有生效指派的调用者是 403
        self.protocol_context = protocol.gate_world_action(self.conn, self.ctx.scope_id, self.kind,
                                                           models.CONTRACT_VERSION)
        self.require_declaration()
        self.event_subjects = [self.pin(text) for text in self.params["subject_refs"]]
        # 回执记第一条主体所在的域；判权不看它。
        self.domain_id = self.referenced[self.params["subject_refs"][0]]["domain_id"]
        self.not_in_future(self.params["occurred_at"], "An external event that has not happened yet cannot be recorded.")
        original = self.params.get("supersedes_event_id")
        if original is not None and self.conn.execute(
                """SELECT 1 FROM gov_world_events WHERE scope_id=%s AND event_id=%s AND contract_version=%s
                     AND kind = ANY(%s)""",
                (self.ctx.scope_id, original, models.CONTRACT_VERSION,
                 world_registry.registry()["rules"]["correction"]["corrects"])).fetchone() is None:
            _invalid("A correction corrects an external event or an Issue event in this scope.")
        content = self.params["content"]
        self.content = {**content, "refs": [self.pin(text) for text in content["refs"]]}
        self.declaration = self.pinned_declaration()

    def open_world_v02(self, object_type: str) -> None:
        """0.2 只在默认契约是 0.2 的域里启用（契约第 1 节），然后按支持登记解析新对象的绑定。"""
        policy = protocol.creation_policy(self.conn, self.ctx.scope_id, self.domain_id)
        if policy is None or (policy["content"].get("default_protocol"), policy["content"].get(
                "default_contract_version")) != (world_profile.PROTOCOL_ID, models.CONTRACT_VERSION):
            _fail("PROTOCOL_NOT_SUPPORTED", "tkos.world/0.2 is not enabled for this domain.", 409)
        self.creation_fields = protocol.resolve_creation(
            self.conn, self.ctx.scope_id, self.domain_id, object_type, self.request.contract_version,
            action_type=self.kind)
        self.protocol_context = self.creation_fields["contract_version"]

    def referenced_type(self, text: str) -> str:
        self.pin(text)
        return self.referenced[text]["object_type"]

    def not_in_future(self, moment: str, message: str) -> None:
        if self.conn.execute("SELECT %s::timestamptz > clock_timestamp() AS future", (moment,)).fetchone()["future"]:
            _invalid(message)

    # ------------------------------------------------------------ delegation
    def authorize_delegation(self) -> None:
        """登记与撤销委托（契约第 14 节）：按 scope 判权（同外部事件），只由委托人本人记——人；Agent 既不登记也不撤销，
        所以受托的服务主体不能转委托。两者都是记录事件，以 Company 为主体（补 39），钉住它的当前修订。"""
        db._assignments(self.conn, self.ctx)  # scope 内没有生效指派的调用者是 403
        if self.ctx.principal_type != "human":
            _fail("FORBIDDEN", "A delegation is granted and revoked by the delegating person in person.")
        self.protocol_context = protocol.gate_world_action(self.conn, self.ctx.scope_id, self.kind,
                                                           models.CONTRACT_VERSION)
        company = self.conn.execute(
            """SELECT o.object_id, r.object_version FROM gov_objects o JOIN gov_object_revisions r
                 ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                WHERE o.scope_id=%s AND o.object_type='Company'""", (self.ctx.scope_id,)).fetchone()
        if company is None:
            _fail("INVALID_STATE", "The scope has no Company yet to record a delegation under.")
        text = models.citation(str(company["object_id"]), company["object_version"])
        self.event_subjects = [self.pin(text)]
        self.domain_id = self.referenced[text]["domain_id"]  # 回执记 Company 所在的域；判权不看它
        if self.kind == "world_grant_delegation":
            self.check_grant()
        else:
            self.check_revocation()

    def check_grant(self) -> None:
        """受托的是本 scope 启用的 Agent 主体，域都是本 scope 的域，有效期还没到；事件 detail 写登记的范围。"""
        params = self.params
        if self.conn.execute(
                """SELECT 1 FROM gov_principals WHERE scope_id=%s AND principal_id=%s AND active
                     AND principal_type='agent'""", (self.ctx.scope_id, params["delegate_principal_id"])).fetchone() is None:
            _invalid("A delegation goes to an active Agent service principal of this scope.")
        known = self.conn.execute(
            "SELECT count(*) AS n FROM gov_domains WHERE scope_id=%s AND domain_id = ANY(%s::uuid[])",
            (self.ctx.scope_id, params["domain_ids"])).fetchone()["n"]
        if known != len(params["domain_ids"]):
            _invalid("A delegation covers domains of this scope.")
        if self.conn.execute("SELECT %s::timestamptz <= clock_timestamp() AS passed",
                             (params["valid_until"],)).fetchone()["passed"]:
            _invalid("A delegation is valid until a time that has not passed yet.")
        self.detail = {field: params[field] for field in world_registry.registry()["delegation"]["grant_fields"]}

    def check_revocation(self) -> None:
        """撤销引用本 scope 的一条登记事件，只由它的委托人本人撤销；已撤销的不再撤销。"""
        original = self.params["delegation_event_id"]
        grant = self.conn.execute(
            """SELECT principal_id FROM gov_world_events WHERE scope_id=%s AND event_id=%s AND contract_version=%s
                 AND kind='delegation.granted'""", (self.ctx.scope_id, original, models.CONTRACT_VERSION)).fetchone()
        if grant is None:
            _invalid("A revocation references a delegation granted in this scope.")
        if str(grant["principal_id"]) != self.ctx.principal_id:
            _fail("FORBIDDEN", "Only the delegating person revokes the delegation.")
        if self.conn.execute(
                """SELECT 1 FROM gov_world_events WHERE scope_id=%s AND contract_version=%s
                     AND kind='delegation.revoked' AND detail->>'delegation_event_id'=%s""",
                (self.ctx.scope_id, models.CONTRACT_VERSION, original)).fetchone() is not None:
            _fail("INVALID_STATE", "The delegation has already been revoked.")
        self.detail = {"delegation_event_id": original}

    def authorize_on_behalf(self) -> None:
        """代记写入（契约第 14 节）：目标须是本 scope 的 world 对象（否则 404）；记录者须是本 scope 的 Agent 主体，并持有
        被代记的人登记给它、当前有效、覆盖这个动作所属的族与目标所在域的委托。之后按被代记的人走他本人记时的同一条
        判权路径：他当前的角色指派对照激活策略，状态表的记录者类别与责任关系，规则相同。任一不满足是 FORBIDDEN。
        代记写入不带写入声明（请求模型已拒）；外部确认时刻不晚于记录时刻，在委托之后、按被代记的人判权之前校验。"""
        target = head_and_binding(self.conn, self.ctx, self.request.target.object_id)[0]
        if self.ctx.principal_type != "agent":
            _fail("FORBIDDEN", "Only an Agent service principal of this scope records on behalf of a person.")
        self.delegation = self.delegation_in_force(target["domain_id"])
        self.not_in_future(self.on_behalf["external_confirmed_at"],
                           "An external confirmation cannot be later than the recording.")
        with self.judged_as_represented():
            self.authorize_recording()

    def delegation_in_force(self, domain_id: str) -> dict[str, Any]:
        """被代记的人登记给调用者、当前有效、覆盖这个动作的族与该域的一条委托（取最早登记的）；没有是 FORBIDDEN。"""
        found = delegations_in_force(self.conn, self.ctx, grantor=self.on_behalf["principal_id"],
                                     delegate=self.ctx.principal_id,
                                     family=world_registry.action_spec(self.kind)["delegable"], domain_id=domain_id)
        if not found:
            _fail("FORBIDDEN", "No delegation in force lets this service principal record this family of actions in "
                               "this domain on behalf of that person.")
        return found[0]

    @contextmanager
    def judged_as_represented(self) -> Iterator[None]:
        """判权期间把身份上下文换成被代记的人、带他当前的角色指派（没有生效指派是 FORBIDDEN），之后换回调用者：
        回执、事件的记录者、幂等键与重放都属于调用者本人。"""
        caller = self.ctx
        person = judged_context(caller, self.on_behalf["principal_id"])
        self.ctx = replace(person, assignments=db._assignments(self.conn, person))
        try:
            yield
        finally:
            self.ctx = caller

    # ------------------------------------------------------------ assign and lifecycle
    def authorize_responsibility(self) -> None:
        """指派与生命周期动作（契约第 9、10.4–10.6、11 节）：目标须是本 scope 的 world 对象（否则 404），按激活
        策略判权后过目标动作闸门；Agent 只做 Agent 面上的动作并带写入声明；然后校验参数，最后按状态表与责任关系
        判这条事件现在能不能记。Mission 的开始可由 Owner 的 Agent 记（记录者类别 self_or_agent）。"""
        self.target = head_and_binding(self.conn, self.ctx, self.request.target.object_id)[0]
        self.domain_id = self.target["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        if self.ctx.principal_type != "human" and self.kind not in world_registry.registry()["agent_face"]["writes"]:
            _fail("FORBIDDEN", "This action is not on the Agent face.")
        self.protocol_context = protocol.gate_target_action(
            self.conn, self.ctx.scope_id, self.target["object_id"], self.kind, self.request.contract_version)
        self.require_declaration(self.target["object_type"])
        self.declaration = self.pinned_declaration()
        content = self.params.get("content")
        self.content = content and {**content, "refs": [self.pin(text) for text in content["refs"]]}
        recorders = self.recorders()
        object_type = self.target["object_type"]
        lifecycles = world_registry.registry()["lifecycles"]
        if self.kind == "world_assign":
            # 逐级指派、不越级（契约第 9.2 节）：只由上一级责任人记；被指派者须已在该域持对应角色。
            if "parent" not in recorders:
                _fail("FORBIDDEN", "Only the responsible one level up assigns this object: the Strategy's CEO a "
                                   "unit's DRI, the period goal's DRI a Mission's Owner, the Mission's Owner a Task, "
                                   "the Task's responsible an Activity.")
            self.assignee = self.params["principal_id"]
            self.check_assignee()
        if self.kind == "world_assign" and object_type in lifecycles and object_type not in {"Task", "Activity"}:
            # Mission 的状态表里没有指派；已关闭、已取消的不能再指派（补 16）。
            if lifecycle(self.conn, self.ctx, self.target)["status"] in lifecycles[object_type]["terminal"]:
                _fail("INVALID_STATE", "A closed or cancelled object is not assigned again.")
            by = "parent"
        elif object_type in lifecycles:
            event = {"event_id": "pending", "action": self.kind, "outcome": self.params.get("outcome"),
                     "disposition": None, "supersedes_event_id": self.params.get("supersedes_event_id"),
                     "candidate": False, "guards": {}, "recorders": set(recorders)}
            try:
                by = world_lifecycle.admit(world_registry.registry(), object_type,
                                           lifecycle_events(self.conn, self.ctx, self.target["object_id"]), event)["by"]
            except world_lifecycle.Refused as exc:
                _fail("FORBIDDEN" if exc.reason == "recorder" else "INVALID_STATE", str(exc))
        else:
            by = "parent"  # 责任单元没有生命周期，只有版本
        used, through = recorders[by]
        self.required_assignments.add(used["assignment_id"])
        self.responsible_through = through

    def recorders(self) -> dict[str, tuple[dict[str, Any], str | None]]:
        """调用者对目标满足的记录者类别（登记 recorders，契约第 9.2 节）→（让他满足的那条角色指派，经哪个对象的
        responsible 属性成立）：self 是目标的责任人，parent 是主干上一级的责任人；self_or_agent 是目标的责任人，
        或在目标所在域持 AGENT 的 Agent（Mission 的「Owner 的 Agent」，补 15）。"""
        current = db._assignments(self.conn, self.ctx)
        node = self.current_object(self.target["object_id"])
        found = {}
        for category, level in (("self", node), ("parent", self.current_object(self.spine_parent(node)))):
            used = self.responsibility_assignment(level, current)
            if used is not None:
                by_attribute = world_registry.object_spec(level["object_type"])["responsible"]["source"] == "attribute"
                found[category] = (used, level["object_id"] if by_attribute else None)
        agent = next((row for row in current if self.ctx.principal_type == "agent" and row["role"] == "AGENT"
                      and row["domain_id"] == node["domain_id"]), None)
        if "self" in found or agent is not None:
            found["self_or_agent"] = found.get("self") or (agent, None)
        return found

    def check_assignee(self) -> None:
        """被指派者是本 scope 启用的身份，且当前在目标所在的域持该类型要求的角色（契约第 3.3 节）；
        责任单元的 DRI 是人、持 DOMAIN_DRI。"""
        principal = self.conn.execute(
            "SELECT principal_type FROM gov_principals WHERE scope_id=%s AND principal_id=%s AND active",
            (self.ctx.scope_id, self.assignee)).fetchone()
        rule = world_registry.object_spec(self.target["object_type"])["responsible"]
        if principal is None:
            role = None
        elif rule["source"] == "role":
            role = rule["role"] if principal["principal_type"] == "human" else None
        else:
            role = rule["roles"].get(principal["principal_type"])
        if role is None or not holds_role(self.conn, self.ctx, self.assignee, self.domain_id, role):
            _invalid("The assignee does not hold the role this assignment needs in the object's domain.")

    # ------------------------------------------------------------ gates
    def authorize_gate(self) -> None:
        """门动作（契约第 9、10.2–10.4、11、12 节）：目标须是本 scope 的 world 对象（否则 404），角色按激活策略判
        （ADR-0005），门事件只由人记——Agent 持有角色也不能记；再过目标动作闸门。然后校验事件内容与候选（合并补丁，
        只含正式内容），最后连同守卫事实与调用者满足的记录者类别交给生命周期引擎，判这条门事件现在能不能记。
        要写回的候选：这条请求带来的（长期目标带候选的确认），或此前那条承诺留存的（确认接受时）。
        关注标记（契约第 10.4 节）同样走这里：只由持 CEO 角色的人记，守卫 once，不带候选。"""
        self.target = head_and_binding(self.conn, self.ctx, self.request.target.object_id)[0]
        self.domain_id = self.target["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        if self.ctx.principal_type != "human":
            _fail("FORBIDDEN", "A gate event is recorded by a person; an Agent does not record it even with the role.")
        self.protocol_context = protocol.gate_target_action(
            self.conn, self.ctx.scope_id, self.target["object_id"], self.kind, self.request.contract_version)
        object_type = self.target["object_type"]
        content = self.params.get("content")
        self.content = content and {**content, "refs": [self.pin(text) for text in content["refs"]]}
        latest = self.target_revision_row()
        self.next_version = latest["object_version"] + 1
        self.candidate = models.with_component_ids(self.params["payload"]) if "payload" in self.params else None
        proposed = None
        if self.candidate is not None:  # 按修订的规则校验、钉定；这条请求就写回时，写回的正是这一版
            proposed = self.candidate_revision(object_type, latest["payload"], latest["payload"], self.candidate)
        recorders = self.recorders()
        recorders["gate_role"] = (sorted(self.action_assignments, key=lambda row: row["assignment_id"])[0], None)
        event = {"event_id": "pending", "action": self.kind, "outcome": self.params.get("outcome"),
                 "disposition": None, "supersedes_event_id": self.params.get("supersedes_event_id"),
                 "candidate": self.candidate is not None, "guards": self.guard_facts(object_type),
                 "recorders": set(recorders)}
        try:
            self.effect = world_lifecycle.admit(world_registry.registry(), object_type,
                                                lifecycle_events(self.conn, self.ctx, self.target["object_id"]), event)
        except world_lifecycle.Refused as exc:
            _fail("FORBIDDEN" if exc.reason == "recorder" else "INVALID_STATE", str(exc))
        used, through = recorders[self.effect["by"]]
        self.required_assignments.add(used["assignment_id"])
        self.responsible_through = through
        source = self.effect["candidate_event_id"]
        if source == "pending":
            self.payload = proposed
        elif source is not None:
            base, carried = self.carried_candidate(source)
            self.payload = self.candidate_revision(object_type, base, latest["payload"], carried, conflict=True)
        else:
            self.payload = None

    def guard_facts(self, object_type: str) -> dict[str, bool]:
        """这条门事件的守卫事实（登记 guards）：Mission 立项的承诺与确认接受查父周期目标（goal_ref）当前处于已确认；
        关注标记查这个 Mission 还没有标过（once，每个 Mission 只标一次）。
        周期目标的形成锚定（goal_ref 的长期目标已确认、review_ref 指向已确认的公司复盘）随票 #60 实现，本票先当它成立。"""
        if object_type == "Mission":
            goal = self.current_object(self.target["object_id"])["payload"]["goal_ref"]["object_id"]
            status = lifecycle(self.conn, self.ctx, {"object_id": goal, "object_type": "PeriodGoal"})["status"]
            marked = self.conn.execute(
                """SELECT 1 FROM gov_world_events WHERE scope_id=%s AND contract_version=%s
                     AND kind='core_battle.marked' AND subject_refs->0->>'object_id'=%s LIMIT 1""",
                (self.ctx.scope_id, models.CONTRACT_VERSION, self.target["object_id"])).fetchone() is not None
            return {"parent_goal_confirmed": status == "confirmed", "once": not marked}
        if object_type == "PeriodGoal":
            return {"formation_anchors": True}
        return {}

    def candidate_revision(self, object_type: str, base: dict[str, Any], latest: dict[str, Any], candidate: Any, *,
                           conflict: bool = False) -> dict[str, Any]:
        """候选写回后的那一版（契约第 12 节，补 33）：正式内容取 base 合并候选，活动内容与只由服务写的字段取 latest 的
        当前值，按修订的规则校验、钉定引用，版本号接在 latest 之后。候选本身不合契约是 INVALID_REQUEST；留存的候选
        写回时已对不上此后的组件台账（conflict）是 INVALID_STATE，由确认人退回、重新承诺。"""
        try:
            self.written = models.written_back(object_type, base, candidate, latest)
        except ValueError as exc:
            if conflict:
                _fail("INVALID_STATE", f"The committed candidate no longer fits the object ({exc}); "
                                       "return it and commit again.")
            _invalid(f"The candidate does not satisfy this world 0.2 object type: {exc}.")
        self.revise(object_type, latest)
        return self.payload

    def carried_candidate(self, event_id: str) -> tuple[dict[str, Any], Any]:
        """此前那条承诺留存的候选（事件 detail）与它所钉的修订：承诺不出修订，钉的是承诺时的最新修订。"""
        row = db.jsonable(self.conn.execute(
            """SELECT e.detail, r.payload FROM gov_world_events e
                 JOIN gov_object_revisions r
                   ON r.scope_id=e.scope_id AND r.revision_id=(e.subject_refs->0->>'revision_id')::uuid
                WHERE e.scope_id=%s AND e.event_id=%s""", (self.ctx.scope_id, event_id)).fetchone())
        return row["payload"], row["detail"]["candidate"]

    # ------------------------------------------------------------ references
    def pin(self, text: str) -> dict[str, Any]:
        """把业务形式的引用解析并钉住（契约第 5 节）：对象形式钉到本 scope 某个 0.2 world 对象的某个修订，
        块须是该类型登记的块，组件须在该修订的那个块里现存；事件须是本 scope 的 0.2 world 事件。
        不存在、不是 world 0.2、在 scope 外的一律按不存在拒绝。"""
        if text in self.pins:
            return self.pins[text]
        ref = models.parse_ref(text)
        if ref["form"] == "event":
            if self.conn.execute(
                    "SELECT 1 FROM gov_world_events WHERE scope_id=%s AND event_id=%s AND contract_version=%s",
                    (self.ctx.scope_id, ref["event_id"], models.CONTRACT_VERSION)).fetchone() is None:
                _invalid("An event reference does not resolve to a world 0.2 event in this scope.")
            self.pins[text] = {"event_id": ref["event_id"]}
            return self.pins[text]
        row = self.conn.execute(
            """SELECT o.object_id, o.object_type, o.domain_id, r.revision_id, r.payload, to_jsonb(o) AS head
                 FROM gov_objects o JOIN gov_object_revisions r ON r.scope_id=o.scope_id AND r.object_id=o.object_id
                WHERE o.scope_id=%s AND o.object_id=%s AND r.object_version=%s""",
            (self.ctx.scope_id, ref["object_id"], ref["object_version"])).fetchone()
        binding = protocol.current_binding(self.conn, self.ctx.scope_id, ref["object_id"]) if row else None
        if row is None or binding is None or binding["contract_version"] != models.CONTRACT_VERSION:
            _invalid("A reference does not resolve to a version of a world 0.2 object in this scope.")
        target = db.jsonable(row)
        if ref["block"] is not None:
            if ref["block"] not in {block["id"] for block in self.blocks_of(target)}:
                _invalid("A reference names a block its object type does not have.")
            block = target["payload"]["blocks"][ref["block"]]
            if ref["component"] is not None:
                target["component"] = next((item for item in (block["components"] if block else [])
                                            if item["id"] == ref["component"]), None)
                if target["component"] is None:
                    _invalid("A component reference names no component present in that block of that version.")
        # 回执的 referenced_object_ids 列出钉住的对象。world 读取按 scope，不走 head() 的域级读策略。
        self.heads.setdefault(target["object_id"], target.pop("head"))
        self.referenced[text] = target
        self.pins[text] = {"object_id": target["object_id"], "object_version": ref["object_version"],
                           "revision_id": target["revision_id"], "block": ref["block"], "component": ref["component"]}
        return self.pins[text]

    @staticmethod
    def blocks_of(target: dict[str, Any]) -> list[dict[str, Any]]:
        """对象的块按类型登记；状态快照的块按它的 payload 类型（契约第 7 节），所以快照里的组件同样可以引用。"""
        if target["object_type"] == "StateSnapshot":
            return models.payload_spec(target["payload"]["payload_type"])["blocks"]
        return world_registry.object_spec(target["object_type"])["blocks"]

    def check_placement(self, object_type: str) -> None:
        """关系引用指向登记允许的类型与组件类型，对象放在契约第 1 节规定的域，目标约束同 0.1。"""
        spec = world_registry.object_spec(object_type)
        for relation in spec["relation_fields"]:
            for text in models.listed(self.written.get(relation["field"])):
                target = self.referenced[text]
                if target["object_type"] not in relation["targets"]:
                    _invalid(f"{relation['field']} must point to one of {', '.join(relation['targets'])}.")
                if relation["target_component"] and target["component"]["type"] != relation["target_component"]:
                    _invalid(f"{relation['field']} must point to a {relation['target_component']} component.")
        field = spec["spine_parent_field"]
        if field is not None:
            same_domain = self.referenced[self.written[field]]["domain_id"] == self.domain_id
            # 责任单元在自己的域，不在其 Strategy 所在的公司域；其余对象与主干上一级同域。
            if same_domain == (object_type == "ResponsibilityUnit"):
                _invalid("The object is not placed in the domain its spine parent requires.")
        if object_type == "LongTermGoal":
            company_level = self.written["scope"] == "company"
            parent = self.referenced[self.written["parent_ref"]]
            if parent["object_type"] != ("Company" if company_level else "ResponsibilityUnit"):
                _invalid("A company-level goal hangs on the Company, a unit-level goal on its unit.")
            goal = self.written.get("goal_ref")
            if goal and (company_level or self.referenced[goal]["payload"]["scope"] != "company"):
                _invalid("Only a unit-level goal decomposes a company-level goal.")
        if object_type == "PeriodGoal" and self.referenced[self.written["goal_ref"]]["payload"]["scope"] != "unit":
            _invalid("A period goal advances a long-term goal of its own unit.")

    def check_components(self, object_type: str) -> None:
        """组件的适用范围指向业务对象；计划条目的责任人（只作记录）是本 scope 的主体（契约第 4 节）。"""
        for block in self.written["blocks"].values():
            for component in (block["components"] if block else []):
                scope = component["scope"]
                if scope and world_registry.object_spec(self.referenced[scope]["object_type"])["category"] \
                        != "business_object":
                    _invalid("A component's scope is a business object.")
                for attribute in models.component_spec(component["type"])["attributes"]:
                    value = component["attributes"].get(attribute["id"])
                    if attribute["value"] == "principal" and value is not None and self.conn.execute(
                            "SELECT 1 FROM gov_principals WHERE scope_id=%s AND principal_id=%s",
                            (self.ctx.scope_id, value)).fetchone() is None:
                        _invalid(f"{attribute['id']} of a component names no principal in this scope.")

    def pinned_declaration(self) -> dict[str, Any] | None:
        """写入声明三项（契约第 9.3 节）：场景钉到任一业务对象，需要人工验收时验收人是本 scope 内有效的人。"""
        given = self.params.get("declaration")
        if given is None:
            return None
        scene = self.pin(given["scene"])
        if world_registry.object_spec(self.referenced[given["scene"]]["object_type"])["category"] != "business_object":
            _invalid("A declared scene is a business object.")
        acceptor = given["human_acceptance"].get("acceptor")
        # 本 scope 内有效的人：启用的人类身份，且当前有任一生效的角色指派。
        if acceptor is not None and self.conn.execute(
                """SELECT 1 FROM gov_principals p
                    WHERE p.scope_id=%s AND p.principal_id=%s AND p.active AND p.principal_type='human'
                      AND EXISTS (SELECT 1 FROM gov_role_assignments a
                                   WHERE a.scope_id=p.scope_id AND a.principal_id=p.principal_id AND a.active
                                     AND a.valid_from<=clock_timestamp()
                                     AND (a.valid_to IS NULL OR clock_timestamp()<a.valid_to))""",
                (self.ctx.scope_id, acceptor)).fetchone() is None:
            _invalid("A declared acceptor is an active person in this scope.")
        return {**given, "scene": cited(scene)}

    # ------------------------------------------------------------ who may create
    def current_object(self, object_id: str) -> dict[str, Any]:
        """对象的 id、类型、所在的域与最新修订的载荷。"""
        return db.jsonable(self.conn.execute(
            """SELECT o.object_id, o.object_type, o.domain_id, r.payload
                 FROM gov_objects o JOIN gov_object_revisions r
                   ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                WHERE o.scope_id=%s AND o.object_id=%s""", (self.ctx.scope_id, object_id)).fetchone())

    def spine_parent(self, node: dict[str, Any]) -> str | None:
        up = world_registry.object_spec(node["object_type"])["spine_parent_field"]
        return node["payload"][up]["object_id"] if up else None

    def responsibility_assignment(self, node: dict[str, Any], current: list[dict[str, Any]]) -> dict[str, Any] | None:
        """调用者是不是这一级对象的责任人（契约第 3.3 节），是则返回让他成为责任人的那条角色指派：按角色
        解析的须是人、在该对象所在的域持有该角色；按属性解析的须是 responsible 上的身份并在该域持对应角色。"""
        rule = world_registry.object_spec(node["object_type"])["responsible"]
        if rule["source"] == "role":
            role = rule["role"] if self.ctx.principal_type == "human" else None
        elif rule["source"] == "attribute" and node["payload"].get("responsible") == self.ctx.principal_id:
            role = rule["roles"].get(self.ctx.principal_type)
        else:
            role = None
        return next((row for row in current if row["domain_id"] == node["domain_id"] and row["role"] == role), None)

    def responsible_up_the_spine(self, object_id: str, *, required: bool = True) -> dict[str, Any] | None:
        """调用者须是从 object_id 起沿主干向上某一级的责任人（同 0.1），返回让他成为责任人的那条指派；
        经 responsible 属性成立时记下该对象，重放时复核。不是时 FORBIDDEN（required 为假时返回 None）。"""
        current = db._assignments(self.conn, self.ctx)
        while object_id is not None:
            node = self.current_object(object_id)
            used = self.responsibility_assignment(node, current)
            if used is not None:
                if world_registry.object_spec(node["object_type"])["responsible"]["source"] == "attribute":
                    self.responsible_through = node["object_id"]
                return used
            object_id = self.spine_parent(node)
        if required:
            _fail("FORBIDDEN", "Only a responsible person up the spine writes this object.")
        return None

    def responsible_below(self, object_id: str, current: list[dict[str, Any]]) -> dict[str, Any] | None:
        """下级责任人（契约第 3.2 节）：调用者是主干上位于 object_id 之下某个对象的责任人，逐层往下找；是则返回让他
        成为责任人的那条指派，经 responsible 属性成立时记下该对象，重放时复核。"""
        spine = [(spec["type"], spec["spine_parent_field"]) for spec in world_registry.registry()["objects"]
                 if spec["spine_parent_field"]]
        level = [object_id]
        while level:
            rows = [db.jsonable(row) for row in self.conn.execute(
                """SELECT o.object_id, o.object_type, o.domain_id, r.payload
                     FROM gov_objects o JOIN gov_object_revisions r
                       ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                    WHERE o.scope_id=%s AND (""" + " OR ".join(
                    ["(o.object_type=%s AND r.payload->%s->>'object_id' = ANY(%s))"] * len(spine)) + """)
                    ORDER BY o.object_id""",
                (self.ctx.scope_id, *[value for pair in spine for value in (*pair, level)])).fetchall()]
            for node in rows:
                used = self.responsibility_assignment(node, current)
                if used is not None:
                    if world_registry.object_spec(node["object_type"])["responsible"]["source"] == "attribute":
                        self.responsible_through = node["object_id"]
                    return used
            level = [row["object_id"] for row in rows]
        return None

    def check_versions(self) -> None:
        """锁定并核对期望版本。world 读按 scope（契约第 15.1 节），不走内核 object_row 的域级读策略。"""
        expected = {item.object_id: item.expected_version for item in self.request.expected_versions}
        if self.request.target:
            expected[self.request.target.object_id] = self.request.target.expected_version
        for object_id in sorted(expected):
            head_and_binding(self.conn, self.ctx, object_id)
            self.heads[object_id] = db.jsonable(self.conn.execute(
                "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s FOR UPDATE",
                (self.ctx.scope_id, object_id)).fetchone())
            if self.heads[object_id]["object_version"] != expected[object_id]:
                _fail("VERSION_CONFLICT", "An object changed after the request was prepared.")
        if self.request.target:
            self.target = self.heads[self.request.target.object_id]
            if self.target["latest_revision_id"] != self.request.target.revision_id:
                _fail("STALE_DEPENDENCY", "Target must identify the current latest revision.")

    def recheck_final_barrier(self) -> None:
        # 代记：服务主体仍在 scope 内有生效指派、委托仍有效，再按被代记的人复核他用到的指派与责任关系（契约第 14 节）。
        if self.on_behalf is not None:
            db._assignments(self.conn, self.ctx)
            self.delegation_in_force(self.domain_id)
            with self.judged_as_represented():
                self.recheck_recorder()
        else:
            self.recheck_recorder()

    def recheck_recorder(self) -> None:
        # 让调用者成为责任人的指派可以在上一级对象的域（例如公司域的 CEO），不必在本动作的域。
        # 外部事件按 scope 判权，不看各域策略，只要调用者仍在 scope 内有生效指派。
        if world_registry.action_spec(self.kind)["authorization"] == "scope":
            db._assignments(self.conn, self.ctx)
        else:
            db.authorize_domain(self.conn, self.ctx, self.domain_id, action_type=self.kind)
        current = {row["assignment_id"] for row in db._assignments(self.conn, self.ctx)}
        if not self.required_assignments <= current:
            _fail("FORBIDDEN", "A required assignment is not currently valid.")
        if self.kind == "world_assign":  # 责任关系一并复核：被指派者此刻仍持对应角色
            self.check_assignee()

    # ------------------------------------------------------------ writing
    def collect_dependencies(self) -> None:
        """引用钉在不可变的修订上，不随被引用对象更新而漂移，所以不是需要期望版本的可变依赖。"""

    def run_action(self) -> dict[str, Any]:
        if self.kind == "world_create_object" or self.kind == "world_refresh_state":
            return self.create_world_object(self.object_type)
        if self.kind == "world_record_event":
            return self.record_event()
        if self.kind in models.DELEGATION_ACTIONS:
            return self.record_delegation()
        if self.kind == "world_assign":
            return self.assign()
        if self.kind in models.LIFECYCLE_ACTIONS:
            return self.record_lifecycle()
        if self.kind == "world_mark_core_battle":
            return self.mark_core_battle()
        if self.kind in models.GATE_ACTIONS:
            return self.record_gate()
        return self.new_revision()

    def record_event(self) -> dict[str, Any]:
        event_id = self.world_event(world_registry.action_spec(self.kind)["event_kind"], self.event_subjects,
                                    category=self.params["category"], occurred_at=self.params["occurred_at"],
                                    content=self.content, supersedes_event_id=self.params.get("supersedes_event_id"))
        result = {"contract_version": models.CONTRACT_VERSION, "event_id": event_id,
                  "occurred_at": self.params["occurred_at"], "subject_refs": cited(self.event_subjects)}
        if self.declaration is not None:
            result["declaration"] = self.declaration
        return result

    def record_delegation(self) -> dict[str, Any]:
        """登记或撤销委托：一条以 Company 为主体的记录事件，detail 写登记的范围或被撤销的那条登记（契约第 14 节）。"""
        event_id = self.world_event(world_registry.action_spec(self.kind)["event_kind"], self.event_subjects,
                                    detail=self.detail)
        return {"contract_version": models.CONTRACT_VERSION, "event_id": event_id,
                "subject_refs": cited(self.event_subjects), "detail": self.detail}

    def assign(self) -> dict[str, Any]:
        """指派只记业务责任、不授予权限（契约第 9.2 节）。Mission、Task、Activity 出新修订写 responsible，
        生效指针原先等于最新修订的随之移动；责任单元的 DRI 按角色解析，只记事件。事件 detail 写被指派者。"""
        obj, latest = self.target, self.target_revision_row()
        if obj["object_type"] == "ResponsibilityUnit":
            revision = latest
            obj = self.bump(obj)
        else:
            revision = self.insert_revision(obj, {**latest["payload"], "responsible": self.assignee},
                                            version=latest["object_version"] + 1)
            moves = obj["effective_revision_id"] == obj["latest_revision_id"]
            obj = self.bump(obj, latest=revision["revision_id"],
                            effective=revision["revision_id"] if moves else obj["effective_revision_id"])
        result = self.written_result(obj, revision, detail={"principal_id": self.assignee})
        result["assignee"] = self.assignee
        return result

    def record_lifecycle(self) -> dict[str, Any]:
        """生命周期事件不改内容、不出修订，钉到当前最新修订；对象行的并发版本照常前进（同门事件）。"""
        revision = self.target_revision_row()
        obj = self.bump(self.target)
        return self.written_result(obj, revision, outcome=self.params.get("outcome"), content=self.content,
                                   supersedes_event_id=self.params.get("supersedes_event_id"))

    def mark_core_battle(self) -> dict[str, Any]:
        """关注标记只置 core_battle（契约第 10.4 节，方案 A）：出新修订写这一只由服务写的属性，生效指针原先等于最新
        修订的随之移动（同指派）；不改生命周期、责任人与正式内容指针的状态。事件钉到新修订。"""
        obj, latest = self.target, self.target_revision_row()
        revision = self.insert_revision(obj, {**latest["payload"], "core_battle": True},
                                        version=latest["object_version"] + 1)
        moves = obj["effective_revision_id"] == obj["latest_revision_id"]
        obj = self.bump(obj, latest=revision["revision_id"],
                        effective=revision["revision_id"] if moves else obj["effective_revision_id"])
        return self.written_result(obj, revision, content=self.content)

    def record_gate(self) -> dict[str, Any]:
        """记门事件并按它的作用挪正式内容指针（契约第 12 节末条）：第一次进入正式段时对象行改为 confirmed、生效指针
        挪到被确认的修订，撤回这条确认时回到 draft、生效指针清空。写回候选出一个新修订，最新与生效指针一起移过去，
        门事件钉这一修订、不另记 object.revised；其余门事件钉当前最新修订。带候选的门事件把候选（合并补丁，新组件的
        id 已定下）留存在 detail，确认接受时从它写回。"""
        obj, effect = self.target, self.effect
        status, latest, effective = obj["lifecycle_status"], obj["latest_revision_id"], obj["effective_revision_id"]
        if self.payload is not None:
            revision = self.insert_revision(obj, self.payload, version=self.next_version)
            latest = effective = revision["revision_id"]
        else:
            revision = self.target_revision_row()
        if effect["makes_formal"]:
            status, effective = "confirmed", latest
        if effect["unmakes_formal"]:
            status, effective = "draft", None
        obj = self.bump(obj, status=status, latest=latest, effective=effective)
        return self.written_result(obj, revision, outcome=self.params.get("outcome"), content=self.content,
                                   detail=None if self.candidate is None else {"candidate": self.candidate},
                                   supersedes_event_id=self.params.get("supersedes_event_id"))

    def create_world_object(self, object_type: str) -> dict[str, Any]:
        if object_type == "StateSnapshot" and self.conn.execute(
                """SELECT 1 FROM gov_object_revisions WHERE scope_id=%s AND payload->'subject_ref' ? 'object_version'
                     AND payload->'subject_ref'->>'object_id'=%s AND payload->>'as_of'=%s LIMIT 1""",
                (self.ctx.scope_id, self.payload["subject_ref"]["object_id"], self.payload["as_of"])).fetchone():
            _fail("INVALID_STATE", "The subject already has a snapshot at this time.")
        if object_type == "Company" and self.conn.execute(
                "SELECT 1 FROM gov_objects WHERE scope_id=%s AND object_type='Company' LIMIT 1",
                (self.ctx.scope_id,)).fetchone() is not None:
            _fail("INVALID_STATE", "A scope has exactly one Company.")
        if object_type == "ResponsibilityUnit" and self.conn.execute(
                "SELECT 1 FROM gov_objects WHERE scope_id=%s AND domain_id=%s AND object_type='ResponsibilityUnit' LIMIT 1",
                (self.ctx.scope_id, self.domain_id)).fetchone() is not None:
            _fail("INVALID_STATE", "A domain has exactly one responsibility unit.")
        gated = world_registry.object_spec(object_type)["gated"]
        object_id = str(uuid4())
        obj = db.jsonable(self.conn.execute(
            """INSERT INTO gov_objects(object_id,scope_id,domain_id,object_type,lifecycle_status)
               VALUES (%s,%s,%s,%s,%s) RETURNING *""",
            (object_id, self.ctx.scope_id, self.domain_id, object_type, "draft" if gated else "recorded"),
        ).fetchone())
        revision = self.insert_revision(obj, self.payload, version=1)
        obj = db.jsonable(self.conn.execute(
            "UPDATE gov_objects SET latest_revision_id=%s,effective_revision_id=%s WHERE scope_id=%s AND object_id=%s RETURNING *",
            (revision["revision_id"], None if gated else revision["revision_id"], self.ctx.scope_id, object_id),
        ).fetchone())
        protocol.insert_binding(self.conn, self.ctx.scope_id, object_id, self.creation_fields,
                                registered_by=self.ctx.principal_id, receipt_id=self.action_id)
        self.heads[object_id] = self.changed[object_id] = obj
        self.event(obj, None, self.kind)
        return self.written_result(obj, revision)

    def new_revision(self) -> dict[str, Any]:
        """修订与建关系各出一个新修订；生效指针原先等于最新修订的随之移动，否则不动（同 0.1）。"""
        obj = self.target
        revision = self.insert_revision(obj, self.payload, version=self.next_version)
        moves = obj["effective_revision_id"] == obj["latest_revision_id"]
        obj = self.bump(obj, latest=revision["revision_id"],
                        effective=revision["revision_id"] if moves else obj["effective_revision_id"])
        return self.written_result(obj, revision)

    def world_event(self, kind: str, subject_refs: list[dict[str, Any]], *, category: str | None = None,
                    occurred_at: str | None = None, outcome: str | None = None, content: dict[str, Any] | None = None,
                    detail: dict[str, Any] | None = None, supersedes_event_id: str | None = None,
                    on_behalf_of: dict[str, Any] | None = None) -> str:
        """写恰好一条 0.2 的 world 事件（契约第 11 节）：只读一次时钟，记录时刻与不补记的发生时刻取同一个值；
        只有外部事件与状态刷新给 occurred_at，迁移 0039 同样这样约束。记录者是调用者；代记时另写被代记的人、
        外部记录 id 与外部确认时刻（契约第 8.1、14 节）。"""
        if occurred_at is not None and kind not in models.BACKDATED_KINDS:
            raise ValueError(f"{kind} happens at the moment it is recorded")
        on_behalf_of = on_behalf_of or {}
        return str(self.conn.execute(
            """INSERT INTO gov_world_events (scope_id, contract_version, kind, category, outcome, subject_refs,
                                             principal_id, on_behalf_of, external_record_id, external_confirmed_at,
                                             occurred_at, recorded_at, content, detail, action_id, supersedes_event_id)
               SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz, now.t),now.t,%s,%s,%s,%s
                 FROM (SELECT clock_timestamp() AS t) now RETURNING event_id""",
            (self.ctx.scope_id, models.CONTRACT_VERSION, kind, category, outcome, Jsonb(subject_refs),
             self.ctx.principal_id, on_behalf_of.get("principal_id"), on_behalf_of.get("external_record_id"),
             on_behalf_of.get("external_confirmed_at"), occurred_at, Jsonb(content) if content is not None else None,
             Jsonb(detail) if detail is not None else None, self.action_id, supersedes_event_id)).fetchone()["event_id"])

    def written_result(self, obj: dict[str, Any], revision: dict[str, Any], **event: Any) -> dict[str, Any]:
        """写恰好一条钉到该修订的事件（建关系时再加列表里的对象；event 是事件的其余字段），返回回执结果；
        结果写明契约版本。"""
        version = revision["object_version"]
        pinned = {"object_id": obj["object_id"], "object_version": version, "revision_id": revision["revision_id"],
                  "block": None, "component": None}
        kind = world_registry.action_spec(self.kind)["event_kind"]
        # 状态刷新的发生时刻取快照的 as_of（契约第 11 节）；事件同时钉住快照与它的主体。
        if kind == "state.refreshed":
            event["occurred_at"] = self.payload["as_of"]
        event_id = self.world_event(kind, [pinned, *self.event_subjects], on_behalf_of=self.on_behalf, **event)
        result = {"contract_version": models.CONTRACT_VERSION, "object_id": obj["object_id"],
                  "revision_id": revision["revision_id"], "version": version,
                  "ref": models.citation(obj["object_id"], version), "event_id": event_id}
        if kind == "state.refreshed":
            result.update(subject_refs=cited(self.event_subjects), as_of=self.payload["as_of"],
                          generator=self.payload["generator"])
        if self.responsible_through is not None:
            result["responsible_through"] = self.responsible_through
        if self.declaration is not None:
            result["declaration"] = self.declaration
        if self.on_behalf is not None:  # 回执同时记下被代记的人、外部确认记录与用到的委托（契约第 14 节）
            result["on_behalf_of"] = {**self.on_behalf, "delegation_event_id": self.delegation["event_id"]}
        return result
