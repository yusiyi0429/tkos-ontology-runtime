"""tkos.world/0.2 的动作执行，复用内核的授权、乐观并发、回执与同事务写入。

执行工厂按请求声明的契约版本先认领 0.2（0.1 只认动作名）。每个 world 动作在同一事务里写
对象修订、恰好一条 world 事件与回执（契约第 8.1 节）；0.2 的事件行写明契约版本，回执结果也写明，
重放与读回执据此分派。授权先于协议错误：先按激活策略判权，Agent 不在建对象的 Agent 面上，再过协议
闸门；然后校验载荷，按主干判断调用者是不是有权的责任人（从新对象的主干上一级找起，同 0.1），之后才
钉定其余引用、核对对象放在哪个域与挂在谁下面、写入声明。已接通八类业务对象的建对象、合并修订与建关系；
快照、事件读取与生命周期随各自的票接入。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, protocol
from . import world_v02_models as models
from . import world_v02_profile as world_profile
from . import world_v02_registry as world_registry
from .errors import GovernedError
from .service import ActionExecution
from .world_v02_readers import cited, head_and_binding


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
        # 事件 subject_refs 里除新修订外还要钉的对象：建关系的列表。
        self.event_subjects: list[dict[str, Any]] = []
        if self.kind == "world_create_object":
            self.authorize_create()
        else:
            self.authorize_target()

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
        不在 Agent 面上的动作 Agent 不能做，再过目标动作闸门；然后校验补丁与写入声明，最后判调用者是不是
        该对象或其主干上某一级的责任人（同 0.1）。有门对象按状态与块类别的修订规则随票 #54。"""
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
        used = self.responsible_up_the_spine(self.target["object_id"])
        self.required_assignments.add(used["assignment_id"])
        if self.kind == "world_revise_object":
            self.revise(object_type, current)
        else:
            self.relate(object_type, current)
        self.declaration = self.pinned_declaration()

    def require_declaration(self, object_type: str) -> None:
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
            if ref["block"] not in {block["id"] for block in world_registry.object_spec(target["object_type"])["blocks"]}:
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

    def responsible_up_the_spine(self, object_id: str) -> dict[str, Any]:
        """调用者须是从 object_id 起沿主干向上某一级的责任人（同 0.1），返回让他成为责任人的那条指派；
        经 responsible 属性成立时记下该对象，重放时复核。"""
        current = db._assignments(self.conn, self.ctx)
        while object_id is not None:
            node = self.current_object(object_id)
            used = self.responsibility_assignment(node, current)
            if used is not None:
                if world_registry.object_spec(node["object_type"])["responsible"]["source"] == "attribute":
                    self.responsible_through = node["object_id"]
                return used
            object_id = self.spine_parent(node)
        _fail("FORBIDDEN", "Only a responsible person up the spine writes this object.")

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
        # 让调用者成为责任人的指派可以在上一级对象的域（例如公司域的 CEO），不必在本动作的域。
        db.authorize_domain(self.conn, self.ctx, self.domain_id, action_type=self.kind)
        current = {row["assignment_id"] for row in db._assignments(self.conn, self.ctx)}
        if not self.required_assignments <= current:
            _fail("FORBIDDEN", "A required assignment is not currently valid.")

    # ------------------------------------------------------------ writing
    def collect_dependencies(self) -> None:
        """引用钉在不可变的修订上，不随被引用对象更新而漂移，所以不是需要期望版本的可变依赖。"""

    def run_action(self) -> dict[str, Any]:
        if self.kind == "world_create_object":
            return self.create_world_object(self.object_type)
        return self.new_revision()

    def create_world_object(self, object_type: str) -> dict[str, Any]:
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

    def world_event(self, kind: str, subject_refs: list[dict[str, Any]]) -> str:
        """写恰好一条 0.2 的 world 事件；门事件与生命周期事件的发生时刻就是记录时刻（契约第 11 节）。"""
        return str(self.conn.execute(
            """INSERT INTO gov_world_events (scope_id, contract_version, kind, subject_refs, principal_id,
                                             occurred_at, action_id)
               VALUES (%s,%s,%s,%s,%s,clock_timestamp(),%s) RETURNING event_id""",
            (self.ctx.scope_id, models.CONTRACT_VERSION, kind, Jsonb(subject_refs), self.ctx.principal_id,
             self.action_id)).fetchone()["event_id"])

    def written_result(self, obj: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
        """写恰好一条钉到新修订的事件（建关系时再加列表里的对象），返回回执结果；结果写明契约版本。"""
        version = revision["object_version"]
        pinned = {"object_id": obj["object_id"], "object_version": version, "revision_id": revision["revision_id"],
                  "block": None, "component": None}
        event_id = self.world_event(world_registry.action_spec(self.kind)["event_kind"], [pinned, *self.event_subjects])
        result = {"contract_version": models.CONTRACT_VERSION, "object_id": obj["object_id"],
                  "revision_id": revision["revision_id"], "version": version,
                  "ref": models.citation(obj["object_id"], version), "event_id": event_id}
        if self.responsible_through is not None:
            result["responsible_through"] = self.responsible_through
        if self.declaration is not None:
            result["declaration"] = self.declaration
        return result
