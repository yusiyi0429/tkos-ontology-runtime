"""tkos.world/0.2 的动作执行，复用内核的授权、乐观并发、回执与同事务写入。

执行工厂按请求声明的契约版本先认领 0.2（0.1 只认动作名）。每个 world 动作在同一事务里写
对象修订、恰好一条 world 事件与回执（契约第 8.1 节）；0.2 的事件行写明契约版本，回执结果也写明，
重放与读回执据此分派。授权先于协议错误：先按激活策略判权，再过协议闸门，然后才校验载荷。
本票只接通建 Company：其余类型、组件与引用随各自的票接入。
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
from .world_v02_readers import head_and_binding


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


class WorldExecution(ActionExecution):
    @classmethod
    def handles_request(cls, conn, ctx, request) -> bool:
        return request.contract_version == models.CONTRACT_VERSION and request.action_type in models.ACTION_PARAMS

    def authorize(self) -> None:
        self.authorize_create()

    def authorize_create(self) -> None:
        """建对象：Company 只由 CEO 本人建（契约第 3.3 节，同 0.1）；授权之后才暴露协议错误。"""
        object_type = self.object_type = self.params["object_type"]
        self.domain_id = self.params["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        ceo = [row for row in self.action_assignments if row["role"] == "CEO"]
        if object_type == "Company" and (not ceo or self.ctx.principal_type != "human"):
            _fail("FORBIDDEN", "Only the CEO in person records the Company.")
        # 0.2 只在默认契约是 0.2 的域里启用（契约第 1 节）；0.1 的域与没有 world 的域一律 PROTOCOL_NOT_SUPPORTED。
        policy = protocol.creation_policy(self.conn, self.ctx.scope_id, self.domain_id)
        if policy is None or (policy["content"].get("default_protocol"), policy["content"].get(
                "default_contract_version")) != (world_profile.PROTOCOL_ID, models.CONTRACT_VERSION):
            _fail("PROTOCOL_NOT_SUPPORTED", "tkos.world/0.2 is not enabled for this domain.", 409)
        self.creation_fields = protocol.resolve_creation(
            self.conn, self.ctx.scope_id, self.domain_id, object_type, self.request.contract_version,
            action_type=self.kind)
        self.protocol_context = self.creation_fields["contract_version"]
        try:
            self.payload = models.validate_input(object_type, self.params["payload"])
        except ValueError:
            _fail("INVALID_REQUEST", "Payload does not satisfy this world 0.2 object type.", 422)
        # 记下让调用者成为责任人的那条指派，重放与最终复核都按它来。
        self.required_assignments.add(sorted(ceo, key=lambda row: row["assignment_id"])[0]["assignment_id"])

    def collect_dependencies(self) -> None:
        """建 Company 没有可变依赖。"""

    def check_versions(self) -> None:
        """锁定并核对期望版本。world 读按 scope（契约第 15.1 节），不走内核 object_row 的域级读策略。"""
        expected = {item.object_id: item.expected_version for item in self.request.expected_versions}
        for object_id in sorted(expected):
            head_and_binding(self.conn, self.ctx, object_id)
            self.heads[object_id] = db.jsonable(self.conn.execute(
                "SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s FOR UPDATE",
                (self.ctx.scope_id, object_id)).fetchone())
            if self.heads[object_id]["object_version"] != expected[object_id]:
                _fail("VERSION_CONFLICT", "An object changed after the request was prepared.")

    def run_action(self) -> dict[str, Any]:
        return self.create_world_object(self.object_type)

    def create_world_object(self, object_type: str) -> dict[str, Any]:
        if object_type == "Company" and self.conn.execute(
                "SELECT 1 FROM gov_objects WHERE scope_id=%s AND object_type='Company' LIMIT 1",
                (self.ctx.scope_id,)).fetchone() is not None:
            _fail("INVALID_STATE", "A scope has exactly one Company.")
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

    def world_event(self, kind: str, subject_refs: list[dict[str, Any]]) -> str:
        """写恰好一条 0.2 的 world 事件；门事件与生命周期事件的发生时刻就是记录时刻（契约第 11 节）。"""
        return str(self.conn.execute(
            """INSERT INTO gov_world_events (scope_id, contract_version, kind, subject_refs, principal_id,
                                             occurred_at, action_id)
               VALUES (%s,%s,%s,%s,%s,clock_timestamp(),%s) RETURNING event_id""",
            (self.ctx.scope_id, models.CONTRACT_VERSION, kind, Jsonb(subject_refs), self.ctx.principal_id,
             self.action_id)).fetchone()["event_id"])

    def written_result(self, obj: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
        """写恰好一条钉到新修订的事件，返回回执结果；结果写明契约版本。"""
        version = revision["object_version"]
        pinned = {"object_id": obj["object_id"], "object_version": version, "revision_id": revision["revision_id"],
                  "block": None}
        event_id = self.world_event(world_registry.action_spec(self.kind)["event_kind"], [pinned])
        return {"contract_version": models.CONTRACT_VERSION, "object_id": obj["object_id"],
                "revision_id": revision["revision_id"], "version": version,
                "ref": models.citation(obj["object_id"], version), "event_id": event_id}
