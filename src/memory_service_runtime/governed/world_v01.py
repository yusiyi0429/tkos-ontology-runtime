"""tkos.world/0.1 的动作执行，复用内核的授权、乐观并发、回执与同事务写入。

每个 world 动作在同一事务里写对象修订、恰好一条 world 事件与回执（契约第 8 节）。
授权先于协议错误：先按激活策略判权，再过协议闸门；Agent 的类型限制与声明是否带齐
随后判；然后按主干判断调用者是不是有权的责任人（建对象从新对象的主干上一级找起，
修订、建关系与写快照从目标对象本身找起，指派只看往上规定的那一级），之后才校验其余
引用、对象放在哪个域与挂在谁下面、声明的内容。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, protocol
from . import world_v01_models as models
from . import world_v01_profile as world_profile
from . import world_v01_registry as world_registry
from .errors import GovernedError
from .service import ActionExecution
from .world_v01_models import citation
from .world_v01_readers import SNAPSHOTS_OF_SUBJECT, cited, world_head


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _invalid(message: str) -> None:
    _fail("INVALID_REQUEST", message, 422)


class WorldExecution(ActionExecution):
    @classmethod
    def handles_request(cls, conn, ctx, request) -> bool:
        return request.action_type in models.ACTION_PARAMS

    def authorize(self) -> None:
        self.pins: dict[str, dict[str, Any]] = {}
        self.referenced: dict[str, dict[str, Any]] = {}
        # 事件 subject_refs 里除新修订外还要钉的对象：建关系的列表、快照的主体、外部事件的全部主体。
        self.event_subjects: list[dict[str, Any]] = []
        # 调用者经哪个对象的 responsible 属性成为责任人；重放时复核该属性仍指向他（契约第 4 节）。
        self.responsible_through: str | None = None
        if self.kind == "world_create_object":
            self.authorize_create()
        elif self.kind == "world_refresh_state":
            self.authorize_refresh_state()
        elif self.kind == "world_record_event":
            self.authorize_record_event()
        else:
            self.authorize_target()

    def authorize_create(self) -> None:
        object_type = self.object_type = self.params["object_type"]
        self.domain_id = self.params["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        if object_type == "Company":
            ceo = [row for row in self.action_assignments if row["role"] == "CEO"]
            if not ceo or self.ctx.principal_type != "human":
                _fail("FORBIDDEN", "Only the CEO in person records the Company.")
        # 授权之后才暴露协议错误：未在该域启用 world 的请求一律 PROTOCOL_NOT_SUPPORTED（契约第 1 节）。
        policy = protocol.creation_policy(self.conn, self.ctx.scope_id, self.domain_id)
        if policy is None or policy["content"].get("default_protocol") != world_profile.PROTOCOL_ID:
            _fail("PROTOCOL_NOT_SUPPORTED", "tkos.world/0.1 is not enabled for this domain.", 409)
        # 状态快照只经 world_refresh_state 写入（契约第 9 节）。先于支持登记判：登记将来为
        # world_refresh_state 列出状态快照后，建对象仍不收它。
        if object_type == "StateSnapshot":
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL", "A state snapshot is written by world_refresh_state.")
        self.open_creation(object_type)
        # 记下这次调用实际依赖的指派（让调用者成为责任人的那条），重放与最终复核都按它来。
        if object_type == "Company":
            used = sorted(ceo, key=lambda row: row["assignment_id"])[0]
        else:
            field = world_registry.object_spec(object_type)["spine_parent_field"]
            used = self.responsible_up_the_spine(self.pin(self.written[field])["object_id"])
        self.required_assignments.add(used["assignment_id"])
        self.store_written(object_type)
        self.check_placement(object_type)
        self.declaration = self.pinned_declaration()

    def authorize_refresh_state(self) -> None:
        """写状态快照（契约第 7 节）：所在域随主体；人须是主体主干上的责任人，Agent 须在该域持 AGENT 角色。"""
        self.object_type = "StateSnapshot"
        try:
            subject_id = models.parse_ref(self.params["payload"].get("subject_ref"))["object_id"]
        except (ValueError, TypeError):
            _invalid("A state snapshot names its subject as <object id>@<version>.")
        subject = world_head(self.conn, self.ctx, subject_id)
        self.domain_id = subject["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        self.open_creation(self.object_type)
        if subject["object_type"] == "StateSnapshot":
            _invalid("A state snapshot is not the subject of a snapshot.")
        self.not_in_future(self.written["as_of"], "A snapshot cannot be as of a time that has not come yet.")
        if self.ctx.principal_type == "agent":
            held = [row for row in self.action_assignments if row["role"] == "AGENT"]
            if not held:
                _fail("FORBIDDEN", "An Agent writes a snapshot only with the AGENT role in the subject's unit.")
            used = held[0]
        else:
            used = self.responsible_up_the_spine(subject_id)
        self.required_assignments.add(used["assignment_id"])
        self.store_written(self.object_type)
        self.event_subjects = [self.pins[self.written["subject_ref"]]]
        self.declaration = self.pinned_declaration()

    def authorize_record_event(self) -> None:
        """记外部事件（契约第 8 节）：按 scope 判权，不看各域策略；主体与内容里的引用钉定，更正只更正外部事件。"""
        db._assignments(self.conn, self.ctx)  # scope 内没有生效指派的调用者是 403
        self.protocol_context = protocol.gate_world_action(self.conn, self.ctx.scope_id, self.kind)
        self.require_declaration()
        self.event_subjects = [self.pin(text) for text in self.params["subject_refs"]]
        # 回执记第一条主体所在的域；判权不看它。
        self.domain_id = self.referenced[self.params["subject_refs"][0]]["domain_id"]
        self.not_in_future(self.params["occurred_at"], "An external event that has not happened yet cannot be recorded.")
        original = self.params.get("supersedes_event_id")
        if original is not None and self.conn.execute(
                "SELECT 1 FROM gov_world_events WHERE scope_id=%s AND event_id=%s AND kind='event.recorded'",
                (self.ctx.scope_id, original)).fetchone() is None:
            _invalid("A correction corrects an external event in this scope.")
        content = self.params["content"]
        self.content = {**content, "refs": [self.pin(text) for text in content["refs"]]}
        self.declaration = self.pinned_declaration()
        if self.params["category"] == "acceptance":
            self.accepted_for = self.accepted_as_parent_responsible()

    def accepted_as_parent_responsible(self) -> list[str]:
        """验收推进生命周期的条件在记录时判定并随回执留存（契约第 10 节）：主体的状态机要求上一级
        责任人记验收时，记录者当时是不是该上一级对象的责任人（含持有对应角色）。"""
        current = db._assignments(self.conn, self.ctx)
        accepted = []
        for pinned in self.event_subjects:
            node = self.current_object(pinned["object_id"])
            spec = world_registry.registry()["lifecycles"].get(node["object_type"])
            guarded = spec and any(item["guard"] == "spine_parent_responsible" for item in spec["transitions"])
            if guarded and self.responsibility_assignment(self.current_object(self.spine_parent(node)), current):
                accepted.append(node["object_id"])
        return accepted

    def authorize_target(self) -> None:
        """修订、建关系与指派：目标须是本 scope 的 world 对象（否则 404），判权后过目标动作闸门。"""
        self.target = world_head(self.conn, self.ctx, self.request.target.object_id)
        self.domain_id = self.target["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        self.protocol_context = protocol.gate_target_action(
            self.conn, self.ctx.scope_id, self.target["object_id"], self.kind, self.request.contract_version)
        object_type = self.target["object_type"]
        if (self.kind == "world_revise_object" and self.ctx.principal_type == "agent"
                and world_registry.object_spec(object_type)["gated"]):
            _fail("FORBIDDEN", "An Agent revises only ungated object types.")
        if self.kind == "world_assign":  # 指派者都是人（CEO、DRI、Owner），不带写入声明
            used = self.assigner()
        else:
            self.require_declaration()
            used = self.responsible_up_the_spine(self.target["object_id"])
        self.required_assignments.add(used["assignment_id"])
        latest = self.target_revision_row()
        # 修订序号跟着最新修订走，不用对象行的并发版本（确认之类的动作只动指针，不出修订）。
        self.next_version, current = latest["object_version"] + 1, latest["payload"]
        if self.kind == "world_revise_object":
            self.revise(object_type, current)
        elif self.kind == "world_relate":
            self.relate(object_type, current)
        else:
            self.assign(object_type, current)
        self.declaration = self.pinned_declaration()

    def revise(self, object_type: str, current: dict[str, Any]) -> None:
        """合并修订（契约第 11 节）；建对象时写的关系引用只能改钉到同一对象的另一版本（第 6 节）。"""
        try:
            self.written = models.merge_revision(object_type, current, self.params["payload"])
        except ValueError:
            _invalid("Payload does not satisfy this world object type.")
        before = models.written_form(object_type, current)

        def pointed(text: str | None) -> str | None:
            return models.parse_ref(text)["object_id"] if text else None
        for relation in world_registry.object_spec(object_type)["relation_fields"]:
            if pointed(before.get(relation["field"])) != pointed(self.written.get(relation["field"])):
                _invalid(f"{relation['field']} can only be re-pinned to another version of the object it was "
                         "created with; create a new object to hang it elsewhere.")
        self.store_written(object_type, server=models.server_fields(object_type, current))
        self.check_placement(object_type)

    def relate(self, object_type: str, current: dict[str, Any]) -> None:
        """整体替换一个跨链关系列表（契约第 6 节）：类型按登记，不指向自己，contributes_to 指向
        另一责任单元的周期目标或单元级长期目标。"""
        field = self.params["field"]
        relation = next((item for item in world_registry.object_spec(object_type)["relation_fields"]
                         if item["field"] == field), None)
        if relation is None:
            _invalid(f"{object_type} has no {field}.")
        for text in self.params["refs"]:
            self.event_subjects.append(self.pin(text))
            target = self.check_target_type(relation, text)
            if target["object_id"] == self.target["object_id"]:
                _invalid("An object cannot depend on itself.")
            company_goal = target["object_type"] == "LongTermGoal" and target["payload"]["scope"] == "company"
            if field == "contributes_to" and (target["domain_id"] == self.domain_id or company_goal):
                _invalid("A contribution goes to a period goal or unit-level goal of another unit.")
        self.payload = models.stored_model(object_type).model_validate(
            {**current, field: self.event_subjects}).model_dump(mode="json")

    def assign(self, object_type: str, current: dict[str, Any]) -> None:
        """指派只记业务责任、不授予权限（契约第 9 节）：被指派者须已在目标所在的域持有对应角色。
        Mission、Task、Activity 出新修订写 responsible；责任单元的 DRI 仍按角色解析，只记事件。"""
        self.assignee = self.params["principal_id"]
        principal = self.conn.execute(
            "SELECT principal_type FROM gov_principals WHERE scope_id=%s AND principal_id=%s AND active",
            (self.ctx.scope_id, self.assignee)).fetchone()
        role = principal and models.RESPONSIBLE_ROLES[object_type].get(principal["principal_type"])
        if role is None or self.conn.execute(
                """SELECT 1 FROM gov_role_assignments
                    WHERE scope_id=%s AND principal_id=%s AND domain_id=%s AND role=%s AND active
                      AND valid_from<=clock_timestamp() AND (valid_to IS NULL OR clock_timestamp()<valid_to)""",
                (self.ctx.scope_id, self.assignee, self.domain_id, role)).fetchone() is None:
            _invalid("The assignee does not hold the role this assignment needs in the unit.")
        if object_type != "ResponsibilityUnit":
            self.payload = models.stored_model(object_type).model_validate(
                {**current, "responsible": self.assignee}).model_dump(mode="json")

    def open_creation(self, object_type: str) -> None:
        """在已判权的域里建一类对象：过创建闸门，看 Agent 的声明是否带齐，载荷按类型校验。"""
        self.creation_fields = protocol.resolve_creation(
            self.conn, self.ctx.scope_id, self.domain_id, object_type, self.request.contract_version,
            action_type=self.kind)
        self.protocol_context = self.creation_fields["contract_version"]
        self.require_declaration()
        try:
            self.written = models.validate_input(object_type, self.params["payload"])
        except ValueError:
            _invalid("Payload does not satisfy this world object type.")

    def store_written(self, object_type: str, server: dict[str, Any] | None = None) -> None:
        """钉定写入载荷里的全部引用，得到存储载荷。"""
        for text in models.ref_texts(object_type, self.written):
            self.pin(text)
        self.payload = models.stored_payload(object_type, self.written, self.pins, server=server)

    def not_in_future(self, moment: str, message: str) -> None:
        if self.conn.execute("SELECT %s::timestamptz > clock_timestamp() AS future", (moment,)).fetchone()["future"]:
            _invalid(message)

    def require_declaration(self) -> None:
        """写入声明只对 Agent 强制（契约第 9 节）；Agent 的修订还必须需要人工验收（第 11 节）。"""
        if self.ctx.principal_type != "agent":
            return
        given = self.params.get("declaration")
        if given is None:
            _invalid("An Agent write must declare its scene, trigger and human acceptance.")
        if self.kind == "world_revise_object" and not given["human_acceptance"]["required"]:
            _invalid("An Agent revision needs human acceptance and an acceptor.")

    # ------------------------------------------------------------ references
    def pin(self, text: str) -> dict[str, Any]:
        """把业务形式的引用解析成本 scope 内某个 world 对象的某个修订并钉住（契约第 5 节）。"""
        if text in self.pins:
            return self.pins[text]
        ref = models.parse_ref(text)
        row = self.conn.execute(
            """SELECT o.object_id, o.object_type, o.domain_id, r.revision_id, r.payload, to_jsonb(o) AS head
                 FROM gov_objects o JOIN gov_object_revisions r ON r.scope_id=o.scope_id AND r.object_id=o.object_id
                WHERE o.scope_id=%s AND o.object_id=%s AND r.object_version=%s""",
            (self.ctx.scope_id, ref["object_id"], ref["object_version"])).fetchone()
        binding = protocol.current_binding(self.conn, self.ctx.scope_id, ref["object_id"]) if row else None
        if row is None or binding is None or binding["contract_version"] != models.CONTRACT_VERSION:
            _invalid("A reference does not resolve to a version of a world object in this scope.")
        target = db.jsonable(row)
        blocks = {block["id"] for block in world_registry.object_spec(target["object_type"])["blocks"]}
        if ref["block"] is not None and ref["block"] not in blocks:
            _invalid("A reference names a block its object type does not have.")
        # 回执的 referenced_object_ids 列出钉住的对象。world 读取按 scope（契约第 12 节），不走 head() 的域级读策略。
        self.heads.setdefault(target["object_id"], target.pop("head"))
        self.referenced[text] = target
        self.pins[text] = {"object_id": target["object_id"], "object_version": ref["object_version"],
                           "revision_id": target["revision_id"], "block": ref["block"]}
        return self.pins[text]

    def check_placement(self, object_type: str) -> None:
        """关系引用指向登记允许的类型，对象放在契约第 1 节规定的域，目标约束按第 6 节。"""
        spec = world_registry.object_spec(object_type)
        for relation in spec["relation_fields"]:
            for text in models.listed(self.written.get(relation["field"])):
                self.check_target_type(relation, text)
        field = spec["spine_parent_field"]
        if field is not None:
            same_domain = self.referenced[self.written[field]]["domain_id"] == self.domain_id
            if object_type == "ResponsibilityUnit":
                misplaced = same_domain  # 责任单元在自己的域，不在其 Strategy 所在的公司域
            else:
                misplaced = not same_domain  # 其余对象与主干上一级同域
            if misplaced:
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

    def check_target_type(self, relation: dict[str, Any], text: str) -> dict[str, Any]:
        target = self.referenced[text]
        if target["object_type"] not in relation["targets"]:
            _invalid(f"{relation['field']} must point to one of {', '.join(relation['targets'])}.")
        return target

    def pinned_declaration(self) -> dict[str, Any] | None:
        """写入声明三项（契约第 9 节）：场景钉到 Mission 或 Task，需要人工验收时验收人是本 scope 内有效的人。"""
        given = self.params.get("declaration")
        if given is None:
            return None
        scene = self.pin(given["scene"])
        if self.referenced[given["scene"]]["object_type"] not in {"Mission", "Task"}:
            _invalid("A declared scene is a Mission or a Task.")
        acceptor = given["human_acceptance"].get("acceptor")
        # 本 scope 内有效的人：启用的人类身份，且当前有任一生效的角色指派（契约第 12 节）。
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
        """调用者是不是这一级对象的责任人（契约第 4 节），是则返回让他成为责任人的那条角色指派。

        按角色解析的（CEO、该单元的 DOMAIN_DRI）须是人、在该对象所在的域持有该角色；按 responsible
        属性解析的（Mission、Task、Activity）须是该属性上的身份，且当前在该域持有对应角色。
        """
        rule = world_registry.object_spec(node["object_type"])["responsible"]
        if rule["source"] == "role":
            role = rule["role"] if self.ctx.principal_type == "human" else None
        elif rule["source"] == "attribute" and node["payload"].get("responsible") == self.ctx.principal_id:
            role = models.RESPONSIBLE_ROLES[node["object_type"]].get(self.ctx.principal_type)
        else:
            role = None
        return next((row for row in current if row["domain_id"] == node["domain_id"] and row["role"] == role), None)

    def responsible_at(self, node: dict[str, Any], current: list[dict[str, Any]]) -> dict[str, Any] | None:
        """同 responsibility_assignment；经 responsible 属性成立时记下该对象，重放时复核。"""
        used = self.responsibility_assignment(node, current)
        if used is not None and world_registry.object_spec(node["object_type"])["responsible"]["source"] == "attribute":
            self.responsible_through = node["object_id"]
        return used

    def responsible_up_the_spine(self, object_id: str) -> dict[str, Any]:
        """调用者须是从 object_id 起沿主干向上某一级的责任人（契约第 9、11 节），返回让他成为责任人的那条指派；
        重放与最终复核都按这条指派复核。"""
        current = db._assignments(self.conn, self.ctx)
        while object_id is not None:
            node = self.current_object(object_id)
            used = self.responsible_at(node, current)
            if used is not None:
                return used
            object_id = self.spine_parent(node)
        _fail("FORBIDDEN", "Only a responsible person up the spine writes this object.")

    def assigner(self) -> dict[str, Any]:
        """逐级指派（契约第 9 节）：调用者须是上一级的责任人——责任单元的 DRI 由 Strategy 的责任人（CEO）
        指派，Mission 的 Owner 由周期目标的责任人（该单元 DRI）指派，Task 与 Activity 的责任人都由所属
        Mission 的 Owner 指派。"""
        node = self.current_object(self.target["object_id"])
        for _ in range(models.ASSIGNED_FROM_LEVELS_UP[node["object_type"]]):
            node = self.current_object(self.spine_parent(node))
        used = self.responsible_at(node, db._assignments(self.conn, self.ctx))
        if used is None:
            _fail("FORBIDDEN", "Only the responsible one level up assigns this object: the CEO a unit's DRI, "
                               "the unit's DRI a Mission's Owner, the Mission's Owner its Tasks and Activities.")
        return used

    def check_versions(self) -> None:
        """锁定并核对期望版本。world 读按 scope（契约第 12 节），不走内核 object_row 的域级读策略；
        world 动作没有必须随请求给出的可变依赖。"""
        expected = {item.object_id: item.expected_version for item in self.request.expected_versions}
        if self.request.target:
            expected[self.request.target.object_id] = self.request.target.expected_version
        for object_id in sorted(expected):
            world_head(self.conn, self.ctx, object_id)
            self.heads[object_id] = db.jsonable(self.conn.execute(
                "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s FOR UPDATE",
                (self.ctx.scope_id, object_id)).fetchone())
            if self.heads[object_id]["object_version"] != expected[object_id]:
                _fail("VERSION_CONFLICT", "An object changed after the request was prepared.")
        if self.request.target:
            self.target = self.heads[self.request.target.object_id]
            if self.target["latest_revision_id"] != self.request.target.revision_id:
                _fail("STALE_DEPENDENCY", "Target must identify the current candidate revision.")

    def recheck_final_barrier(self) -> None:
        # 让调用者成为责任人的指派可以在上一级对象的域（例如公司域的 CEO），不必在本动作的域；
        # 按 scope 判权的动作只复核调用者仍在 scope 内有生效指派。
        if self.kind not in models.SCOPE_ACTIONS:
            db.authorize_domain(self.conn, self.ctx, self.domain_id, action_type=self.kind)
        current = {row["assignment_id"] for row in db._assignments(self.conn, self.ctx)}
        if not self.required_assignments <= current:
            _fail("FORBIDDEN", "A required assignment is not currently valid.")

    # ------------------------------------------------------------ writing
    def collect_dependencies(self) -> None:
        """引用钉在不可变的修订上，不随被引用对象更新而漂移，所以不是需要期望版本的可变依赖。"""

    def run_action(self) -> dict[str, Any]:
        if self.kind in {"world_create_object", "world_refresh_state"}:
            return self.create_world_object(self.object_type)
        if self.kind == "world_record_event":
            return self.record_event()
        if self.kind == "world_assign" and self.payload is None:  # 指派责任单元的 DRI：只记事件，不出修订
            return self.written_result(self.target, self.target_revision_row())
        return self.new_revision()

    def target_revision_row(self) -> dict[str, Any]:
        return db.jsonable(self.conn.execute(
            "SELECT * FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
            (self.ctx.scope_id, self.target["latest_revision_id"])).fetchone())

    def world_event(self, kind: str, subject_refs: list[dict[str, Any]], *, category: str | None = None,
                    occurred_at: str | None = None, content: dict[str, Any] | None = None,
                    supersedes_event_id: str | None = None) -> str:
        """写恰好一条 world 事件；occurred_at 不给即记录时刻。"""
        return str(self.conn.execute(
            """INSERT INTO gov_world_events (scope_id, kind, category, subject_refs, principal_id, occurred_at, content,
                                             action_id, supersedes_event_id)
               VALUES (%s,%s,%s,%s,%s,COALESCE(%s::timestamptz, clock_timestamp()),%s,%s,%s) RETURNING event_id""",
            (self.ctx.scope_id, kind, category, Jsonb(subject_refs), self.ctx.principal_id, occurred_at,
             Jsonb(content) if content is not None else None, self.action_id, supersedes_event_id)).fetchone()["event_id"])

    def record_event(self) -> dict[str, Any]:
        event_id = self.world_event(world_registry.action_spec(self.kind)["event_kind"], self.event_subjects,
                                    category=self.params["category"],
                                    occurred_at=self.params["occurred_at"], content=self.content,
                                    supersedes_event_id=self.params.get("supersedes_event_id"))
        result = {"event_id": event_id, "occurred_at": self.params["occurred_at"],
                  "subject_refs": cited(self.event_subjects)}
        if self.params["category"] == "acceptance":
            result["accepted_as_parent_responsible"] = self.accepted_for
        if self.declaration is not None:
            result["declaration"] = self.declaration
        return result

    def create_world_object(self, object_type: str) -> dict[str, Any]:
        if object_type == "StateSnapshot" and self.conn.execute(
                "SELECT 1 FROM gov_object_revisions WHERE " + SNAPSHOTS_OF_SUBJECT + " AND payload->>'as_of'=%s LIMIT 1",
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
        """修订与建关系各出一个新修订；生效指针原先等于最新修订的随之移动，否则不动（契约第 11 节）。"""
        obj = self.target
        revision = self.insert_revision(obj, self.payload, version=self.next_version)
        moves = obj["effective_revision_id"] == obj["latest_revision_id"]
        obj = self.bump(obj, latest=revision["revision_id"],
                        effective=revision["revision_id"] if moves else obj["effective_revision_id"])
        return self.written_result(obj, revision)

    def written_result(self, obj: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
        """写恰好一条 world 事件（钉到新修订；建关系时再加列表里的对象，写快照时再加它的主体），返回回执结果。"""
        version = revision["object_version"]
        pinned = {"object_id": obj["object_id"], "object_version": version, "revision_id": revision["revision_id"],
                  "block": None}
        self.world_event(world_registry.action_spec(self.kind)["event_kind"], [pinned, *self.event_subjects])
        result = {"object_id": obj["object_id"], "revision_id": revision["revision_id"], "version": version,
                  "ref": citation(obj["object_id"], version)}
        if self.kind == "world_assign":
            result["assignee"] = self.assignee
        if self.responsible_through is not None:
            result["responsible_through"] = self.responsible_through
        if self.declaration is not None:
            result["declaration"] = self.declaration
        return result
