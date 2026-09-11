"""A2 公司组合业务 handler（Contract-A tkos.contract-a/0.1）。

本模块只承载 6 个 A2 动作与 2 个受控输入写（CompanyReference/CapacityObservation）
的业务语义。所有数据库写、提交、scope 栅栏与回执由调用方拥有；本模块不出 commit、
不新建连接、不派生独立事务。协议围栏与 owner 登记在调用之前由 db.authenticate /
protocol.gate_target_action 完成；本模块在持有栅栏后用 ctx 与 helpers 复查最新当前
授权、版本与绑定。

关键不变量（与契约包 A §§4/6、独立验收契约 A2-01…A2-18 严格对齐）：
  - 来源 currentness 与最终准入前最终签认人资格在每次激活前以 DB 时钟重查；
  - 成员集合 / 正式 Submission / binding 来源 / member_set_version / input_set_version
    与 manifest 逐项相等才准入；
  - 容量总需求来自完整正式 Submission 集合（全局覆盖），而不是单 Submission 一一对应；
  - 依赖闭包 64 节点 / 深度 8 上限，超限/成环显式拒绝；
  - Mission 仅按 manifest 精确 submission_ref 选中；不在 gov_mission_index 全表激活；
  - Receipt 仅内部持久化；effect_task_ids=[]；不创建 IC/WorkItem/执行授权。

Reader 集成必须使用 a2_readers 的当前授权检查；依赖模块缺失时直接失败。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4


from . import db, protocol
from .a2_models import A2_ACTIONS, A2_COMPOSITION_TYPES, A2_OBJECT_TYPES, A2_SOURCE_TYPES, CLOSURE_MAX_DEPTH, CLOSURE_MAX_NODES
from .errors import GovernedError
from .models import ActionRequest
from .service import ActionExecution

# 受控输入类型对应于通用 create_object / propose_revision 在 Contract-A 协议下
# 唯一允许的对象类型；其余 5 类必须由 A2 专用动作派生。
_A2_GENERIC_SOURCE_TYPES = frozenset(A2_SOURCE_TYPES)

# a2_readers is mandatory for A2 dispatch: a missing/broken module must surface
# loudly, never silently weaken authorization through a raw DB fallback.
from . import a2_readers as _a2_readers


def a2_readers_visible_object(conn: Any, ctx: Any, object_id: str) -> dict[str, Any] | None:
    """Thin wrapper around ``a2_readers.visible_object`` with NOT_FOUND/FORBIDDEN → None."""
    try:
        return _a2_readers.visible_object(conn, ctx, object_id)
    except GovernedError as exc:
        if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
            return None
        raise


def a2_readers_visible_revision(conn: Any, ctx: Any, object_id: str,
                                  revision_id: str) -> dict[str, Any] | None:
    try:
        return _a2_readers.visible_revision(conn, ctx, object_id, revision_id)
    except GovernedError as exc:
        if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
            return None
        raise


# --------------------------------------------------------------------- helpers


def _fail(code: str, message: str = "", status: int | None = None) -> None:
    raise GovernedError(code, message, status=status)


def _canonical_uuid(value: Any) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        _fail("INVALID_REQUEST", f"invalid UUID: {value!r}", 422)


def _require_human(execution: ActionExecution) -> None:
    if execution.ctx.principal_type != "human":
        _fail("FORBIDDEN", "A2 actions require a human actor.")


# --------------------------------------------------------- A2 source creation


# ------------------------------------------------------ capacity & closure math


# ------------------------------------------------------------ A2Execution


class A2Execution(ActionExecution):
    """6 A2 动作 + 2 受控输入写的业务 handler。

    通过 handles_request() 决定是否接管当前请求；service 工厂在持栅栏后用此
    入口取代默认 ActionExecution.run_action。authorize / collect_dependencies
    / check_versions / run_action / finish / head / revision / assignment 按 A2
    规则覆盖，并复用 base 的事务与回执实现。
    """

    # ---------- entrypoint ----------

    @classmethod
    def handles_request(cls, conn: Any, ctx: Any, request: ActionRequest) -> bool:
        """Decide whether A2 handler should take this request.

        Routes the six A2 actions and the two Contract-A source creation paths.
        For ``create_object`` the requested object_type is authoritative: only
        the two A2 source types are A2-bound and accept writes. For
        ``propose_revision`` the live target must (a) be readable through the
        A2 shared-read exception, (b) belong to one of the two A2 source
        object_types, and (c) carry a current ``tkos.contract-a`` binding —
        any other object is legacy and falls through to the base handler.
        """
        kind = request.action_type
        if kind in A2_ACTIONS:
            return True
        if kind == "create_object":
            params = cls._dump_params(request)
            obj_type = params.get("object_type") if isinstance(params, dict) else None
            return obj_type in _A2_GENERIC_SOURCE_TYPES
        if kind == "propose_revision":
            target = getattr(request, "target", None)
            if target is None or not getattr(target, "object_id", None):
                return False
            try:
                head = a2_readers_visible_object(conn, ctx, target.object_id)
            except GovernedError:
                return False
            if head is None or head.get("object_type") not in _A2_GENERIC_SOURCE_TYPES:
                return False
            binding = protocol.current_binding(conn, ctx.scope_id, target.object_id)
            return binding is not None and binding.get("protocol_id") == "tkos.contract-a"
        return False

    @staticmethod
    def _dump_params(request: ActionRequest) -> dict[str, Any]:
        params = getattr(request, "params", None)
        if params is None:
            return {}
        if hasattr(params, "model_dump"):
            return params.model_dump(mode="json", exclude_none=True)
        if isinstance(params, dict):
            return dict(params)
        return {}

    # ---------- overrides ----------

    def authorize(self) -> None:
        _require_human(self)
        from . import a2_rounds
        if self.kind in {'open_formation_round', 'amend_formation_round', 'publish_domain_submission'}:
            a2_rounds.authorize(self)
            return
        if self.kind in {'create_object', 'propose_revision'}:
            super().authorize()
            object_type = self.params['object_type'] if self.kind == 'create_object' else self.target['object_type']
            if object_type == 'CompanyReference' and not any(a['role'] == 'CEO' for a in self.action_assignments):
                _fail('FORBIDDEN')
            return
        self.target = self.head(self.request.target.object_id)
        self.target_revision = self.revision(self.target['object_id'], self.request.target.revision_id)
        if self.kind == 'form_company_composition':
            if self.target['object_type'] != 'FormationRound': _fail('NOT_FOUND')
            definition = self.target_revision['payload']
            self.domain_id = definition['company_domain_id']
            a2_rounds.actor(self, self.domain_id, 'CEO', definition['ceo_assignment_id'])
            for m in definition['members']: a2_rounds.actor(self, m['domain_id'], 'CEO')
        else:
            if self.target['object_type'] != 'CompanyComposition': _fail('NOT_FOUND')
            manifest = self.target_revision['payload']
            round_obj = self.head(manifest['round_id'])
            definition = self.revision(round_obj['object_id'], round_obj['latest_revision_id'])['payload']
            aid = self.params.get('assignment_id')
            slot = next((s for s in manifest['required_signers'] if s['principal_id'] == self.ctx.principal_id
                         and (aid is None or s['assignment_id'] == aid)), None)
            if slot is None: _fail('FORBIDDEN')
            if self.kind == 'activate_company_composition' and slot['responsibility_role'] != 'company_decider': _fail('FORBIDDEN')
            role = 'CEO' if slot['responsibility_role'] == 'company_decider' else 'DOMAIN_DRI'
            self.domain_id = definition['company_domain_id'] if role == 'CEO' else next(
                m['domain_id'] for m in manifest['members'] if m['dri_assignment_id'] == slot['assignment_id'])
            a2_rounds.actor(self, self.domain_id, role, slot['assignment_id'])
        self.protocol_context = protocol.gate_target_action(self.conn, self.ctx.scope_id,
            self.target['object_id'], self.kind, self.request.contract_version)


    def collect_dependencies(self) -> None:
        if self.kind in {'create_object', 'propose_revision'}:
            self._a2_collect_source_upstream_closure()
        elif self.kind in {'open_formation_round', 'amend_formation_round', 'publish_domain_submission'}:
            from . import a2_rounds
            a2_rounds.collect(self)
        else:
            from . import a2_composition
            a2_composition.collect(self)

    def _a2_collect_source_upstream_closure(self) -> None:
        seen = set()
        heights = {}
        active = {self.target['object_id']} if self.target else set()
        shared = set((self.payload or {}).get('shared_with_domain_ids', []))

        def visit(ref, depth):
            oid, rid = ref['object_id'], ref['revision_id']
            obj = self.add_dependency(oid)
            rev = self.revision(oid, rid)
            if oid in active or depth > CLOSURE_MAX_DEPTH:
                _fail('COMPOSITION_NOT_READY', 'Source dependency cycle or depth limit.')
            if obj['object_type'] not in A2_SOURCE_TYPES:
                _fail('INVALID_STATE', 'Source dependencies require published source objects.')
            if obj['effective_revision_id'] != rid:
                _fail('COMPOSITION_INPUT_CHANGED')
            if ref.get('payload_hash', rev['payload_hash']) != rev['payload_hash']:
                _fail('COMPOSITION_INPUT_CHANGED')
            if not shared.issubset(set(rev['payload'].get('shared_with_domain_ids', []))):
                _fail('INVALID_STATE', 'An upstream source does not permit the requested publication.')
            current = self.conn.execute(
                """SELECT valid_from<=clock_timestamp() AND
                    (valid_to IS NULL OR clock_timestamp()<valid_to) AS valid
                    FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s""",
                (self.ctx.scope_id, oid, rid)).fetchone()
            if not current or not current['valid']:
                _fail('COMPOSITION_INPUT_CHANGED')
            key = (oid, rid)
            if key in seen:
                if depth + heights[key] - 1 > CLOSURE_MAX_DEPTH:
                    _fail('COMPOSITION_NOT_READY', 'Source dependency depth limit.')
                return heights[key]
            seen.add(key)
            if len(seen) > CLOSURE_MAX_NODES:
                _fail('COMPOSITION_NOT_READY', 'Source dependency node limit.')
            active.add(oid)
            height = 1
            for child in rev['payload'].get('upstream_refs', []):
                height = max(height, 1 + visit(child, depth+1))
            active.remove(oid)
            heights[key] = height
            return height

        for ref in (self.payload or {}).get('upstream_refs', []):
            visit(ref, 1)


    def head(self, object_id: str) -> dict[str, Any]:  # type: ignore[override]
        """A2 shared-read head. Cross-domain formal dependencies are allowed;
        inactive or non-human sources are rejected by the reader."""
        object_id = str(object_id)
        head = a2_readers_visible_object(self.conn, self.ctx, object_id)
        if head is None:
            _fail("NOT_FOUND")
        self.heads[object_id] = head
        return head

    def revision(self, object_id: str, revision_id: str) -> dict[str, Any]:  # type: ignore[override]
        object_id, revision_id = str(object_id), str(revision_id)
        rev = a2_readers_visible_revision(self.conn, self.ctx, object_id, revision_id)
        if rev is None:
            _fail("NOT_FOUND")
        if rev["object_id"] != object_id:
            _fail("NOT_FOUND")
        self.revisions[revision_id] = rev
        return rev

    def add_dependency(self, object_id: str) -> dict[str, Any]:  # type: ignore[override]
        """A2 dependency gate: load head through the reader, gate the
        dependency against the expected contract, then register the
        object_id and return the live row. No raw visibility bypass."""
        obj = a2_readers_visible_object(self.conn, self.ctx, object_id)
        if obj is None:
            _fail("NOT_FOUND")
        if self.protocol_context is not None and (
            self.request.target is None or object_id != self.request.target.object_id
        ):
            protocol.gate_dependency(self.conn, self.ctx.scope_id, object_id,
                                     self.protocol_context)
        if self.request.target is None or str(object_id) != self.request.target.object_id:
            self.dependencies.add(str(object_id))
        self.heads.setdefault(str(object_id), obj)
        return obj

    def assignment(self, assignment_id: str) -> dict[str, Any]:
        row = _a2_readers.current_assignment(self.conn, self.ctx, assignment_id)
        if row is None:
            _fail("FORBIDDEN", "A required assignment is not currently valid.")
        return row

    def add_revision_dependency(self, revision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:  # type: ignore[override]
        row = self.conn.execute(
            "SELECT object_id FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
            (self.ctx.scope_id, revision_id),
        ).fetchone()
        if row is None:
            _fail("NOT_FOUND")
        object_id = str(row["object_id"])
        obj = self.add_dependency(object_id)
        rev = a2_readers_visible_revision(self.conn, self.ctx, object_id, revision_id)
        if rev is None:
            _fail("NOT_FOUND")
        self.revisions[revision_id] = rev
        return obj, rev

    def check_versions(self) -> None:  # type: ignore[override]
        """A2 check_versions: dependency completeness + extra-dependency
        visibility + expected-version check, but lock SQL scope rows only
        after a2_readers authorization (no raw db.object_row(..., lock=True)
        bypass, which would reject cross-domain formal dependencies)."""
        supplied = {item.object_id: item.expected_version for item in self.request.expected_versions}
        missing = self.dependencies - supplied.keys()
        if missing:
            _fail("DEPENDENCY_MISSING", "The request omits required mutable dependencies.")
        for object_id in supplied:
            self.add_dependency(object_id)
        expected = dict(supplied)
        if self.request.target:
            expected[self.request.target.object_id] = self.request.target.expected_version
        for object_id in sorted(expected):
            head = a2_readers_visible_object(self.conn, self.ctx, object_id)
            if head is None:
                _fail("NOT_FOUND")
            if head["object_version"] != expected[object_id]:
                _fail("VERSION_CONFLICT", "An object changed after the request was prepared.")
            locked = self.conn.execute(
                """SELECT * FROM gov_objects WHERE scope_id=%s AND object_id=%s FOR UPDATE""",
                (self.ctx.scope_id, object_id),
            ).fetchone()
            if locked is None:
                _fail("NOT_FOUND")
            locked_row = db.jsonable(locked)
            if locked_row["object_version"] != expected[object_id]:
                _fail("VERSION_CONFLICT",
                      "An object changed after the request was prepared.")
            self.heads[object_id] = locked_row
        if self.request.target:
            self.target = self.heads[self.request.target.object_id]
            if self.target["latest_revision_id"] != self.request.target.revision_id:
                _fail("STALE_DEPENDENCY", "Target must identify the current candidate revision.")

    # ---------- run_action dispatcher ----------

    def run_action(self) -> dict[str, Any]:
        from . import a2_rounds, a2_composition, a2_activation
        if self.kind == 'create_object': return self.a2_create_source()
        if self.kind == 'propose_revision': return self.a2_propose_source()
        if self.kind == 'open_formation_round': return a2_rounds.open_round(self)
        if self.kind == 'amend_formation_round': return a2_rounds.amend_round(self)
        if self.kind == 'publish_domain_submission': return a2_rounds.publish_submission(self)
        if self.kind == 'form_company_composition': return a2_composition.form(self)
        if self.kind == 'confirm_company_composition': return a2_activation.confirm(self)
        if self.kind == 'activate_company_composition': return a2_activation.activate(self)
        _fail('ACTION_NOT_SUPPORTED_FOR_PROTOCOL')


    # ---------- 7) controlled source writes ----------

    def a2_create_source(self) -> dict[str, Any]:
        """create_object for CompanyReference / CapacityObservation.

        The base ``ActionExecution.authorize`` has already validated domain,
        role and payload; here we only materialize the row, insert the
        immutable revision via ``insert_revision`` (which handles validity
        windows) and register the binding through ``protocol.insert_binding``
        with ``registered_by`` / ``receipt_id`` kwargs. Source current
        publication is effective — there is no legacy draft-only behavior.
        """
        params = self.params
        object_type = params["object_type"]
        domain_id = str(self.domain_id)
        strict_payload = self.payload
        object_id = str(uuid4())
        row = self.conn.execute(
            """INSERT INTO gov_objects(object_id, scope_id, domain_id, object_type, lifecycle_status)
               VALUES (%s, %s, %s, %s, 'recorded') RETURNING *""",
            (object_id, self.ctx.scope_id, _canonical_uuid(domain_id), object_type),
        ).fetchone()
        obj = db.jsonable(row)
        revision = self.insert_revision(
            obj, strict_payload, version=1,
            valid_from=strict_payload.get("valid_from"),
        )
        # Source current publication is effective.
        updated = db.jsonable(self.conn.execute(
            """UPDATE gov_objects SET object_version=1,
                                      latest_revision_id=%s,
                                      effective_revision_id=%s,
                                      updated_at=clock_timestamp()
               WHERE scope_id=%s AND object_id=%s RETURNING *""",
            (revision["revision_id"], revision["revision_id"],
             self.ctx.scope_id, object_id),
        ).fetchone())
        self.heads[object_id] = self.changed[object_id] = updated
        protocol.insert_binding(
            self.conn, self.ctx.scope_id, object_id, self.creation_fields,
            registered_by=self.ctx.principal_id, receipt_id=self.action_id,
        )
        self.event(updated, None, "create_object")
        return {"object_id": object_id, "revision_id": revision["revision_id"]}

    def a2_propose_source(self) -> dict[str, Any]:
        """Publish a new controlled source revision. The base authorize has already
        validated the target binding and the payload; ``insert_revision``
        handles the validity window atomically. Source current publication
        is effective — no legacy draft-only behavior."""
        if self.target["object_type"] not in _A2_GENERIC_SOURCE_TYPES:
            _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL")
        strict_payload = self.payload
        previous = self.target
        new_revision = self.insert_revision(
            previous, strict_payload,
            version=previous["object_version"] + 1,
            valid_from=strict_payload.get("valid_from"),
        )
        updated = db.jsonable(self.conn.execute(
            """UPDATE gov_objects SET object_version=object_version+1,
                                      latest_revision_id=%s,
                                      effective_revision_id=%s,
                                      updated_at=clock_timestamp()
               WHERE scope_id=%s AND object_id=%s RETURNING *""",
            (new_revision["revision_id"], new_revision["revision_id"],
             self.ctx.scope_id, previous["object_id"]),
        ).fetchone())
        self.heads[previous["object_id"]] = self.changed[previous["object_id"]] = updated
        self._a2_increment_input_set_version_for_source(updated, new_revision)
        self.event(updated, previous, "propose_revision")
        return {
            "object_id": updated["object_id"],
            "revision_id": new_revision["revision_id"],
            "effective_revision_id": new_revision["revision_id"],
        }

    def _a2_increment_input_set_version_for_source(self, obj: dict[str, Any],
                                                    revision: dict[str, Any]) -> None:
        # Internal invalidation reads do not grant the source publisher access
        # to the consuming Rounds, nor put private Round IDs into their receipt.
        changed_id = str(obj['object_id'])
        rounds = self.conn.execute(
            """SELECT o.object_id,r.payload FROM gov_formation_round_state s
                JOIN gov_objects o ON o.scope_id=s.scope_id AND o.object_id=s.object_id
                JOIN gov_object_revisions r ON r.scope_id=o.scope_id
                  AND r.object_id=o.object_id AND r.revision_id=o.latest_revision_id
                WHERE s.scope_id=%s AND o.lifecycle_status='open'""",
            (self.ctx.scope_id,)).fetchall()

        def refs(payload):
            result = list(payload.get('upstream_refs', [])) + list(payload.get('dependency_refs', []))
            result += [b['source_ref'] for b in payload.get('bindings', [])]
            if isinstance(payload.get('submission'), dict):
                result += refs(payload['submission'])
            for mission in payload.get('missions', []):
                result += refs(mission)
            return result

        for row in rounds:
            definition = row['payload']
            pending = [definition['company_reference_ref']]
            members = {m['domain_id'] for m in definition['members']}
            formal = self.conn.execute(
                """SELECT f.domain_id,r.payload FROM gov_round_formal_submissions f
                    JOIN gov_object_revisions r ON r.scope_id=f.scope_id
                      AND r.object_id=f.submission_object_id AND r.revision_id=f.submission_revision_id
                    WHERE f.scope_id=%s AND f.round_object_id=%s""",
                (self.ctx.scope_id,row['object_id'])).fetchall()
            for sub in formal:
                if str(sub['domain_id']) in members:
                    pending += refs(sub['payload'])
            visited = set()
            while pending:
                ref = pending.pop()
                oid, rid = ref['object_id'], ref['revision_id']
                if oid == changed_id:
                    self.conn.execute(
                        """UPDATE gov_formation_round_state
                            SET input_set_version=input_set_version+1,updated_at=clock_timestamp()
                            WHERE scope_id=%s AND object_id=%s""",
                        (self.ctx.scope_id,row['object_id']))
                    break
                if (oid,rid) in visited:
                    continue
                visited.add((oid,rid))
                if len(visited) > CLOSURE_MAX_NODES:
                    _fail('COMPOSITION_NOT_READY', 'Stored dependency closure exceeds the node limit.')
                bound = self.conn.execute(
                    'SELECT payload FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s AND revision_id=%s',
                    (self.ctx.scope_id,oid,rid)).fetchone()
                if bound:
                    pending += refs(bound['payload'])

    # ---------- finish override (replay governance) ----------

    def finish(self, result: dict[str, Any], request_hash: str) -> dict[str, Any]:
        fields = self.creation_fields or protocol.current_binding(self.conn, self.ctx.scope_id, self.target['object_id'])
        result['governance'] = {
            'contract_version': fields['contract_version'],
            'method_profile_ref': {'profile_id': fields['profile_id'], 'revision': fields['profile_revision'],
                                   'canonical_hash': fields['profile_canonical_hash']},
            'actor_assignment_ids': sorted({a['assignment_id'] for a in self.action_assignments}),
            'dependency_versions': [{'object_id': oid, 'object_version': self.heads[oid]['object_version']}
                                    for oid in sorted(self.dependencies)],
        }
        self.effect_task_ids = []
        receipt = super().finish(result, request_hash)
        if self.kind in {'confirm_company_composition', 'activate_company_composition'}:
            from . import a2_composition
            a2_composition.recheck_live(self)
        return receipt


# ------------------------------------------------------- module exports

__all__ = [
    "A2Execution",
    "A2_SOURCE_TYPES",
    "A2_COMPOSITION_TYPES",
    "A2_OBJECT_TYPES",
]
