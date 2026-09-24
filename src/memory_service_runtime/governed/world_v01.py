"""tkos.world/0.1 的动作执行，复用内核的授权、乐观并发、回执与同事务写入。

每个 world 动作在同一事务里写对象修订、恰好一条 world 事件与回执（契约第 8 节）。
授权先于协议错误：先按激活策略判权，再过协议闸门；载荷通过结构校验后，钉住主干
上一级并按主干判断调用者是不是有权建它的责任人，之后才校验其余引用、对象放在
哪个域与挂在谁下面、写入声明。
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
from .world_v01_readers import citation, cited


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _invalid(message: str) -> None:
    _fail("INVALID_REQUEST", message, 422)


class WorldExecution(ActionExecution):
    @classmethod
    def handles_request(cls, conn, ctx, request) -> bool:
        return request.action_type in models.ACTION_PARAMS

    def authorize(self) -> None:
        object_type = self.params["object_type"]
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
        self.creation_fields = protocol.resolve_creation(
            self.conn, self.ctx.scope_id, self.domain_id, object_type, self.request.contract_version,
            action_type=self.kind)
        self.protocol_context = self.creation_fields["contract_version"]
        if self.ctx.principal_type == "agent" and "declaration" not in self.params:
            _invalid("An Agent write must declare its scene, trigger and human acceptance.")
        try:
            self.written = models.validate_input(object_type, self.params["payload"])
        except ValueError:
            _invalid("Payload does not satisfy this world object type.")
        self.pins: dict[str, dict[str, Any]] = {}
        self.referenced: dict[str, dict[str, Any]] = {}
        # 记下这次调用实际依赖的指派（让调用者成为责任人的那条），重放与最终复核都按它来。
        if object_type == "Company":
            used = sorted(ceo, key=lambda row: row["assignment_id"])[0]
        else:
            used = self.responsible_up_the_spine(object_type)
        self.required_assignments.add(used["assignment_id"])
        for text in models.ref_texts(object_type, self.written):
            self.pin(text)
        self.payload = models.stored_payload(object_type, self.written, self.pins)
        self.check_placement(object_type)
        self.declaration = self.pinned_declaration()

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
                if self.referenced[text]["object_type"] not in relation["targets"]:
                    _invalid(f"{relation['field']} must point to one of {', '.join(relation['targets'])}.")
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
    def responsible_up_the_spine(self, object_type: str) -> dict[str, Any]:
        """建对象者须是新对象主干上某一级的责任人（契约第 9 节），返回让他成为责任人的那条指派。

        按角色解析的责任人（CEO、该单元的 DOMAIN_DRI）须是人，在那一级对象所在的域持有该角色。
        按属性解析的责任人（Mission、Task 的 responsible）只由指派写入，随指派（票 #23）一起
        接入，届时重放与最终复核也要复核它；在那之前沿主干越过这两级继续向上找。
        """
        current = db._assignments(self.conn, self.ctx)
        field = world_registry.object_spec(object_type)["spine_parent_field"]
        object_id = self.pin(self.written[field])["object_id"]
        while object_id is not None:
            node = db.jsonable(self.conn.execute(
                """SELECT o.object_type, o.domain_id, r.payload
                     FROM gov_objects o JOIN gov_object_revisions r
                       ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                    WHERE o.scope_id=%s AND o.object_id=%s""", (self.ctx.scope_id, object_id)).fetchone())
            spec = world_registry.object_spec(node["object_type"])
            rule = spec["responsible"]
            if rule["source"] == "role" and self.ctx.principal_type == "human":
                held = [row for row in current if row["domain_id"] == node["domain_id"] and row["role"] == rule["role"]]
                if held:
                    return held[0]
            up = spec["spine_parent_field"]
            object_id = node["payload"][up]["object_id"] if up else None
        _fail("FORBIDDEN", "Only a responsible person up the spine creates this object.")

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
        return self.create_world_object()

    def world_event(self, kind: str, subject_refs: list[dict[str, Any]]) -> None:
        self.conn.execute(
            """INSERT INTO gov_world_events (scope_id, kind, subject_refs, principal_id, occurred_at, action_id)
               VALUES (%s,%s,%s,%s,clock_timestamp(),%s)""",
            (self.ctx.scope_id, kind, Jsonb(subject_refs), self.ctx.principal_id, self.action_id))

    def create_world_object(self) -> dict[str, Any]:
        object_type = self.params["object_type"]
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
        pinned = {"object_id": object_id, "object_version": 1, "revision_id": revision["revision_id"], "block": None}
        self.world_event("object.created", [pinned])
        result = {"object_id": object_id, "revision_id": revision["revision_id"], "version": 1,
                  "ref": citation(object_id, 1)}
        if self.declaration is not None:
            result["declaration"] = self.declaration
        return result
