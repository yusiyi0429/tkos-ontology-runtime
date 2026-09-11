"""A3 DRI–IC 执行交接的服务端执行类（Contract-A tkos.contract-a/0.1）。

A3Execution 在既有 ActionExecution 事务/授权/CAS/回执骨架之上，按
docs/runtime-a3-engineering.md（§1–§6 冻结）接管 A3 动作的准入与写路径：

  - 协议归属永远由服务端裁定：create_object 按域创建策略行
    （creation_policy row 的 content.default_protocol）判定，propose 与目标
    动作按目标对象当前 ProtocolBinding 判定；客户端 contract_version 声明
    只决定参数形状校验，绝不降级对象协议。
  - authorize() 直接调用 ActionExecution.authorize（不是 A2Execution 的
    组合分支），随后用 a3_models.A3_ACTION_PARAMS 对请求参数做严格复核
    （extra=forbid：A3 create 不接受 valid_from，旧版 EC 载荷/宽松标量一律
    422）。
  - collect_dependencies() 承载全部准入验证，prepare 与 execute 走同一代码
    路径；run_action() 在同一事务内重跑同一准入函数后落库。
  - finish() 复用 A2 的治理元数据块；effect_task_ids 恒为 [] —— A3 的
    activate_commitment 绝不发出 governance.dispatch。这依赖
    service.ActionExecution 的窄钩子（_enqueue_effects /
    recheck_final_barrier）；钩子缺失时 finish 明确失败而非静默误发。

a3_delivery / a3_outcome / a3_readers 为后续阶段模块；本文件对它们只做
函数级惰性导入——模块缺失时 ImportError 直接暴露（半成品构建必须响亮
失败），本文件不包含任何占位性放行。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from . import a3_governance, profile, protocol
from .a2_service import A2Execution
from .a3_models import (
    A3_ACTION_PARAMS,
    A3_GENERIC_CREATE_TYPES,
    A3_GENERIC_REVISE_TYPES,
    A3_PAYLOAD_MODELS,
)
from .errors import GovernedError
from .models import ActionRequest
from .protocol import A3_EXECUTION_OBJECT_TYPES, A3_TARGET_OBJECT_TYPES
from .service import ActionExecution

# A3 复用既有动作名（契约包 A §5.2）；目标类型集合在 protocol.py 中编译固定。
A3_TARGETED_ACTIONS = frozenset({
    "accept_commitment",
    "activate_commitment",
    "accept_work_item",
    "submit_deliverable",
    "review_deliverable",
    "record_outcome_assessment",
})


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _require_human(execution: ActionExecution) -> None:
    if execution.ctx.principal_type != "human":
        _fail("FORBIDDEN", "A3 actions require a human actor.")


def _raw_head_type(conn: Any, scope_id: str, object_id: Any) -> str | None:
    """Routing-only scoped type lookup; never leaks into any response."""
    try:
        oid = str(UUID(str(object_id)))
    except (ValueError, TypeError, AttributeError):
        return None
    row = conn.execute(
        "SELECT object_type FROM gov_objects WHERE scope_id=%s AND object_id=%s",
        (scope_id, oid),
    ).fetchone()
    return str(row["object_type"]) if row is not None else None


def _contract_a_bound(conn: Any, scope_id: str, object_id: str) -> bool:
    binding = protocol.current_binding(conn, scope_id, object_id)
    return binding is not None and binding.get("protocol_id") == profile.CONTRACT_A_PROTOCOL_ID


class A3Execution(A2Execution):
    """A3 执行交接 handler。

    继承 A2Execution 的 reader 安全原语（visible_object/visible_revision 同域
    优先、current_assignment、reader 安全 check_versions），但 authorize 走
    ActionExecution 基类（A2 的组合分支不识别 A3 目标动作）。
    """

    # ---------- entrypoint ----------

    @classmethod
    def handles_request(cls, conn: Any, ctx: Any, request: ActionRequest) -> bool:
        """Server-side routing only; a False result falls through to A2/legacy,
        which enforce their own 404/403 boundaries — nothing here reveals
        object existence."""
        kind = request.action_type
        if kind == "create_object":
            params = cls._dump_params(request)
            object_type = params.get("object_type")
            domain_id = params.get("domain_id")
            if object_type not in A3_GENERIC_CREATE_TYPES or not domain_id:
                return False
            # The domain creation policy ROW decides the creation protocol;
            # content.default_protocol is authoritative (never the client's
            # declaration, never a top-level row field).
            policy_row = protocol.creation_policy(conn, ctx.scope_id, str(domain_id))
            content = (policy_row or {}).get("content") or {}
            return content.get("default_protocol") == profile.CONTRACT_A_PROTOCOL_ID
        target = getattr(request, "target", None)
        if target is None or not getattr(target, "object_id", None):
            return False
        object_id = str(target.object_id)
        if kind == "propose_revision":
            if _raw_head_type(conn, ctx.scope_id, object_id) not in A3_GENERIC_REVISE_TYPES:
                return False
            return _contract_a_bound(conn, ctx.scope_id, object_id)
        if kind in A3_TARGETED_ACTIONS:
            allowed = A3_TARGET_OBJECT_TYPES.get(kind)
            if not allowed:
                return False
            if _raw_head_type(conn, ctx.scope_id, object_id) not in allowed:
                return False
            return _contract_a_bound(conn, ctx.scope_id, object_id)
        return False

    # ---------- overrides ----------

    def checked_payload(self, object_type: str, payload: Any) -> dict[str, Any]:
        """A3 payloads validate against the strict a3_models only; the legacy
        Commitment/WorkItem payload shapes are NOT accepted on A3-routed
        creates/revises."""
        model = A3_PAYLOAD_MODELS.get(object_type)
        if model is None:
            return super().checked_payload(object_type, payload)
        try:
            return model.model_validate(payload).model_dump(mode="json", exclude_none=True)
        except (ValidationError, ValueError, TypeError):
            _fail("INVALID_REQUEST", "Payload does not match the actual object type.", 422)

    def _revalidate_a3_params(self) -> None:
        """Strict server-side revalidation of the request params against
        a3_models.A3_ACTION_PARAMS.  The envelope's legacy-first union parse is
        only a shape guess; this step is authoritative (Codex review item 7):
        valid_from on A3 create, legacy-shaped EC payloads and lax scalars on
        accept/activate are all rejected with a controlled 422.
        record_outcome_assessment keeps the legacy params model by design."""
        model = A3_ACTION_PARAMS.get(self.kind)
        if model is None:
            return
        params = self.request.params
        if hasattr(params, "model_dump"):
            raw = params.model_dump(mode="json", exclude_none=True)
        elif isinstance(params, dict):
            raw = dict(params)
        else:
            raw = {}
        try:
            strict = model.model_validate(raw)
        except (ValidationError, ValueError, TypeError):
            _fail("INVALID_REQUEST", "The request does not satisfy the A3 command schema.", 422)
        self.a3_params = strict

    def authorize(self) -> None:
        _require_human(self)
        # Base authorize: target/domain resolution, authorize_domain for the
        # action, human/agent policy, protocol gate (gate_target_action /
        # resolve_creation accept the A3 types under an explicitly supportive
        # registry), payload validation via the overridden checked_payload.
        ActionExecution.authorize(self)
        self._revalidate_a3_params()
        if self.target and self.target_revision["revision_id"] != self.target["latest_revision_id"]:
            _fail("STALE_DEPENDENCY", "The target must identify the current content revision.")

    def collect_dependencies(self) -> None:
        """Full admission validation; prepare_action enforces the identical
        gates because it calls this same method.  Only S3 byte fetches are
        deferred to the write path."""
        if self.kind == "create_object":
            object_type = self.params["object_type"]
            if object_type == "ExecutionCommitment":
                a3_governance.collect_create_commitment(self)
                return
            from . import a3_delivery
            if object_type == "WorkItem":
                a3_delivery.collect_create_work_item(self)
                return
            a3_delivery.collect_create_plan(self)
            return
        if self.kind == "propose_revision":
            if self.target["object_type"] == "ExecutionCommitment":
                a3_governance.collect_propose_commitment(self)
                return
            from . import a3_delivery
            a3_delivery.collect_propose_plan(self)
            return
        if self.kind == "accept_commitment":
            a3_governance.collect_accept_commitment(self)
            return
        if self.kind == "activate_commitment":
            a3_governance.collect_activate_commitment(self)
            return
        if self.kind == "accept_work_item":
            from . import a3_delivery
            a3_delivery.collect_accept_work_item(self)
            return
        if self.kind == "submit_deliverable":
            from . import a3_delivery
            a3_delivery.collect_submit_deliverable(self)
            return
        if self.kind == "review_deliverable":
            from . import a3_delivery
            a3_delivery.collect_review_deliverable(self)
            return
        if self.kind == "record_outcome_assessment":
            from . import a3_outcome
            a3_outcome.collect_outcome(self)
            return
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")

    def run_action(self) -> dict[str, Any]:
        """Write-path dispatch.  Every writer re-runs its collect_* admission
        function inside the same transaction before mutating."""
        if self.kind == "create_object":
            object_type = self.params["object_type"]
            if object_type == "ExecutionCommitment":
                return a3_governance.create_commitment(self)
            from . import a3_delivery
            if object_type == "WorkItem":
                return a3_delivery.create_work_item(self)
            return a3_delivery.create_plan(self)
        if self.kind == "propose_revision":
            if self.target["object_type"] == "ExecutionCommitment":
                return a3_governance.propose_commitment_revision(self)
            from . import a3_delivery
            return a3_delivery.propose_plan(self)
        if self.kind == "accept_commitment":
            return a3_governance.accept_commitment(self)
        if self.kind == "activate_commitment":
            return a3_governance.activate_commitment(self)
        if self.kind == "accept_work_item":
            from . import a3_delivery
            return a3_delivery.accept_work_item(self)
        if self.kind == "submit_deliverable":
            from . import a3_delivery
            return a3_delivery.submit_deliverable(self)
        if self.kind == "review_deliverable":
            from . import a3_delivery
            return a3_delivery.review_deliverable(self)
        if self.kind == "record_outcome_assessment":
            from . import a3_outcome
            return a3_outcome.record_outcome_assessment(self)
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")

    # ---------- finish hooks (narrow base hooks; legacy default unchanged) ----------

    def _enqueue_effects(self, result: dict[str, Any], versions: list[dict[str, Any]]) -> None:
        """A3 emits NO external effect: activate_commitment under A3 never
        enqueues governance.dispatch; effect_task_ids stays [].  The base
        implementation (legacy) is untouched and keeps its current behavior."""
        return None

    def recheck_final_barrier(self) -> None:
        """After before_business_commit: base domain+assignment recheck, then
        the A3 rechecks — admitted authority/appointment generation pointers
        and windows by DB clock, and the A2 baseline/upstream currency for
        baseline-admitted actions (natural expiry is not stopped by the
        scope fence)."""
        super().recheck_final_barrier()
        a3_governance.recheck_final(self)

    def finish(self, result: dict[str, Any], request_hash: str) -> dict[str, Any]:
        # A2Execution.finish attaches the replay governance metadata block
        # (contract_version, method_profile_ref, actor assignments, dependency
        # versions) and zeroes effect_task_ids before the base receipt write.
        self.effect_task_ids = []
        if not hasattr(ActionExecution, "_enqueue_effects"):
            # The narrow base hooks are installed by the service.py integration
            # change; without them the base finish would enqueue a dispatch for
            # activate_commitment.  Fail the whole action instead of ever
            # emitting a wrong effect (the transaction rolls back).
            _fail("INVALID_STATE",
                  "A3 execution requires the runtime service finish hooks of this build.")
        return super().finish(result, request_hash)


# ---- A3 receipt replay dispatch (server-side of a3_readers.authorize_receipt) ----


def is_a3_receipt(conn: Any, ctx: Any, receipt: dict[str, Any]) -> bool:
    from . import a3_readers
    return a3_readers.is_a3_receipt(conn, ctx, receipt)


def reauthorize_replay(conn, ctx, request, receipt):
    """Return history only while its concrete responsibility still authorizes it.

    Do not rerun a terminal transition or current-candidate CAS: an original
    submission remains replayable after review. A different surviving role
    must not revive a revoked IC's historical WorkReceipt, however.
    """
    from . import db, a3_delivery
    ex = A3Execution(conn, ctx, request)
    ex.domain_id = receipt["result"]["domain_id"]
    ex.protocol_context = receipt["result"]["governance"]["contract_version"]
    db.authorize_domain(conn, ctx, ex.domain_id, action_type=request.action_type)
    oid = receipt["target_object_id"]
    head = ex.head(oid)
    ex.target = head
    rid = request.target.revision_id if request.target else receipt["result"]["revision_id"]
    ex.target_revision = ex.revision(oid, rid)
    if request.action_type == "record_outcome_assessment":
        from . import a3_outcome
        a3_outcome.target_baseline(ex)
        return
    if head["object_type"] == "ExecutionCommitment":
        payload = ex.target_revision["payload"]
        baseline = a3_governance.check_execution_baseline(ex, payload)
        a3_governance.check_upstream_currency(ex, baseline)
        aid = (ex.params["party_assignment_id"] if ex.kind == "accept_commitment"
               else payload["dri_assignment_id"])
        if aid not in {payload["dri_assignment_id"], payload["ic_assignment_id"]}:
            _fail("FORBIDDEN")
        party = a3_governance.raw_assignment(ex, aid)
        role = "DOMAIN_DRI" if aid == payload["dri_assignment_id"] else "IC"
        a3_delivery.actor(ex, aid, party["principal_id"], role)
        if ex.kind == "activate_commitment":
            a3_governance.require_current_authority(ex, oid, receipt["result"]["execution_authority_id"],
                                                    receipt["result"]["execution_epoch"])
        return
    if head["object_type"] == "ExecutionPlan":
        work_id = ex.target_revision["payload"]["work_item_ref"]["object_id"]
        context = a3_delivery.work(ex, work_id)
    else:
        context = a3_delivery.work(ex)
    state = context["state"]
    if ex.kind == "review_deliverable":
        appointment = a3_governance.require_current_appointment(
            ex, context["commitment_object_id"], ex.params["appointment_id"], ex.params["appointment_version"],
            expected_mission_object_id=context["payload"]["mission_ref"]["object_id"])
        a3_delivery.actor(ex, appointment["acceptor_assignment_id"], appointment["acceptor_principal_id"], "VERIFIER")
    elif ex.kind == "create_object" and head["object_type"] == "WorkItem":
        a3_delivery.execution(ex, context, state["authority_id"], state["execution_epoch"], require_ic=False)
        dri = a3_governance.raw_assignment(ex, context["payload"]["dri_assignment_id"])
        a3_delivery.actor(ex, dri["assignment_id"], dri["principal_id"], "DOMAIN_DRI")
    else:
        a3_delivery.execution(ex, context, state["authority_id"], state["execution_epoch"])


__all__ = ["A3Execution", "A3_TARGETED_ACTIONS", "is_a3_receipt"]
