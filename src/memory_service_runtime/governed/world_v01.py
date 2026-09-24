"""tkos.world/0.1 的动作执行，复用内核的授权、乐观并发、回执与同事务写入。

每个 world 动作在同一事务里写对象修订、恰好一条 world 事件与回执（契约第 8 节）。
授权先于协议错误：先按激活策略判权，再过协议闸门，最后校验载荷。
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
from .world_v01_readers import citation

# 本票只开放 Company；其余类型随各自的票开放。
CREATABLE = frozenset({"Company"})


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


class WorldExecution(ActionExecution):
    @classmethod
    def handles_request(cls, conn, ctx, request) -> bool:
        return request.action_type in models.ACTION_PARAMS

    def authorize(self) -> None:
        object_type = self.params["object_type"]
        self.domain_id = self.params["domain_id"]
        self.action_assignments = db.authorize_domain(self.conn, self.ctx, self.domain_id, self.kind)
        used = self.action_assignments
        if object_type == "Company":
            used = [row for row in used if row["role"] == "CEO"]
            if not used or self.ctx.principal_type != "human":
                _fail("FORBIDDEN", "Only the CEO in person records the Company.")
        # 授权之后才暴露协议错误：未在该域启用 world 的请求一律 PROTOCOL_NOT_SUPPORTED（契约第 1 节）。
        policy = protocol.creation_policy(self.conn, self.ctx.scope_id, self.domain_id)
        if policy is None or policy["content"].get("default_protocol") != world_profile.PROTOCOL_ID:
            _fail("PROTOCOL_NOT_SUPPORTED", "tkos.world/0.1 is not enabled for this domain.", 409)
        self.creation_fields = protocol.resolve_creation(
            self.conn, self.ctx.scope_id, self.domain_id, object_type, self.request.contract_version,
            action_type=self.kind)
        self.protocol_context = self.creation_fields["contract_version"]
        if object_type not in CREATABLE:
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL", "This object type is not open for creation yet.")
        try:
            self.payload = models.validate_payload(object_type, self.params["payload"])
        except ValueError:
            _fail("INVALID_REQUEST", "Payload does not satisfy this world object type.", 422)
        if any(block and block["refs"] for block in self.payload["blocks"].values()):
            _fail("INVALID_REQUEST", "Block references are not open until reference pinning is available.", 422)
        # 记下这次调用实际依赖的指派（Company 为那条 CEO 指派），重放与最终复核都按它来。
        self.required_assignments.add(sorted(used, key=lambda row: row["assignment_id"])[0]["assignment_id"])

    def collect_dependencies(self) -> None:
        """Company 不引用任何对象；有引用的类型随引用钉定一起补上。"""

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
        return {"object_id": object_id, "revision_id": revision["revision_id"], "version": 1,
                "ref": citation(object_id, 1)}
