"""Offline unit tests for a2_service.A2Execution.

这些测试直接导入生产模块 ``memory_service_runtime.governed.a2_service``
（禁止 sys.modules 注入 / fake SQL parser），只在每个测试作用域内 mock
边界调用（a2_readers 可见性、protocol binding、DB 时钟、下游 a2_rounds /
a2_composition / a2_activation 派发）。覆盖范围：

  - 模块导入干净，6 个 A2 动作全部进入路由；
  - handles_request 的动作/对象类型/绑定分发（legacy 一律不落 A2）；
  - run_action 派发到新的 a2_rounds / a2_composition / a2_activation，
    未知动作在任何写路径之前拒绝；
  - finish 的治理附件与 recheck_live 接线；
  - payload 模型与 manifest helper 校验（a2_models 直接引用）。

跨 Submission 全集容量聚合的正反例由 test_a2_composition 针对现行
a2_composition._aggregate_capacity 覆盖；本文件不再测试已从生产代码
删除的旧草稿 helper（_compute_capacity_closure / _walk_dependency_closure /
_share_coverage / _capacity_pool_key）。

完整 HTTP/SQL 验收由 Codex 独立持有；本文件不创建数据库连接、不 commit、
不动用外部资源。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from memory_service_runtime.governed import (
    a2_activation,
    a2_composition,
    a2_models,
    a2_service,
)
from memory_service_runtime.governed.errors import GovernedError


# ----------------------------- helpers


def _fixed_uuid(seed: int) -> str:
    # 用固定 8-hex 段构造规范 UUID 字符串供测试。
    base = format(seed, "08x")
    return f"{base}-0000-4000-8000-000000000000"


def _request(action_type, params=None, target=None):
    return SimpleNamespace(action_type=action_type,
                           params=params if params is not None else {},
                           target=target)


def _ctx():
    return SimpleNamespace(scope_id=_fixed_uuid(100), principal_id=_fixed_uuid(101),
                           principal_type="human", company_id=_fixed_uuid(102),
                           tenant_id=_fixed_uuid(103), auth_epoch=1)


def _execution(kind="open_formation_round", params=None):
    """A REAL A2Execution instance with a mock conn — no sys.modules tricks."""
    request = mock.MagicMock()
    request.action_type = kind
    request.params = mock.MagicMock()
    request.params.model_dump = mock.MagicMock(return_value=params or {})
    request.target = None
    request.idempotency_key = "idem-1"
    return a2_service.A2Execution(mock.MagicMock(), _ctx(), request)


# ----------------------------- module smoke


def test_module_imports_real_readers():
    """a2_service 必须与真实 a2_readers 绑定（无降级路径）。"""
    from memory_service_runtime.governed import a2_readers
    assert a2_service._a2_readers is a2_readers
    cls = a2_service.A2Execution
    assert issubclass(cls, a2_service.ActionExecution)
    for kind in (
        "open_formation_round", "amend_formation_round",
        "publish_domain_submission", "form_company_composition",
        "confirm_company_composition", "activate_company_composition",
    ):
        assert kind in a2_models.A2_ACTIONS


# ----------------------------- handles_request routing


def test_handles_request_a2_actions():
    cls = a2_service.A2Execution
    with mock.patch.object(a2_service, "a2_readers_visible_object") as spy:
        for kind in (
            "open_formation_round", "amend_formation_round",
            "publish_domain_submission", "form_company_composition",
            "confirm_company_composition", "activate_company_composition",
        ):
            assert cls.handles_request(mock.MagicMock(), _ctx(),
                                       _request(kind)) is True
    # The six A2 actions short-circuit: reader boundary must not be consulted.
    spy.assert_not_called()


def test_handles_request_rejects_legacy():
    cls = a2_service.A2Execution
    with mock.patch.object(a2_service, "a2_readers_visible_object") as spy:
        for legacy_kind in ("accept_commitment", "revoke_assignment",
                            "submit_deliverable", "accept_work_item"):
            assert cls.handles_request(mock.MagicMock(), _ctx(),
                                       _request(legacy_kind)) is False
    spy.assert_not_called()


def test_handles_request_source_create():
    cls = a2_service.A2Execution
    for obj_type in ("CompanyReference", "CapacityObservation"):
        req = _request("create_object", params={"object_type": obj_type})
        assert cls.handles_request(mock.MagicMock(), _ctx(), req) is True


def test_handles_request_rejects_non_source_create():
    cls = a2_service.A2Execution
    for obj_type in ("FormationRound", "DomainSubmission",
                     "CompanyComposition", "Mission", "DomainCommitment"):
        req = _request("create_object", params={"object_type": obj_type})
        assert cls.handles_request(mock.MagicMock(), _ctx(), req) is False


def test_handles_request_propose_revision_source_with_contract_a():
    cls = a2_service.A2Execution
    target = SimpleNamespace(object_id=_fixed_uuid(1))
    req = _request("propose_revision", target=target)
    with mock.patch.object(a2_service, "a2_readers_visible_object",
                           return_value={"object_type": "CapacityObservation"}), \
         mock.patch.object(a2_service.protocol, "current_binding",
                           return_value={"protocol_id": "tkos.contract-a"}):
        assert cls.handles_request(mock.MagicMock(), _ctx(), req) is True


@pytest.mark.parametrize("head,binding", [
    ({"object_type": "CapacityObservation"}, {"protocol_id": "legacy/0"}),
    ({"object_type": "CapacityObservation"}, None),
    ({"object_type": "FormationRound"}, {"protocol_id": "tkos.contract-a"}),
    (None, {"protocol_id": "tkos.contract-a"}),
])
def test_handles_request_propose_revision_falls_through_to_legacy(head, binding):
    """非来源类型 / 非 Contract-A 绑定 / 不可见目标一律不接管（legacy 不变）。"""
    cls = a2_service.A2Execution
    target = SimpleNamespace(object_id=_fixed_uuid(1))
    req = _request("propose_revision", target=target)
    with mock.patch.object(a2_service, "a2_readers_visible_object",
                           return_value=head), \
         mock.patch.object(a2_service.protocol, "current_binding",
                           return_value=binding):
        assert cls.handles_request(mock.MagicMock(), _ctx(), req) is False


def test_handles_request_propose_revision_reader_error_falls_through():
    """Reader 抛 NOT_FOUND/FORBIDDEN 时不接管（a2_readers_visible_object → None）。"""
    cls = a2_service.A2Execution
    target = SimpleNamespace(object_id=_fixed_uuid(1))
    req = _request("propose_revision", target=target)
    with mock.patch.object(a2_service._a2_readers, "visible_object",
                           side_effect=GovernedError("NOT_FOUND")):
        assert cls.handles_request(mock.MagicMock(), _ctx(), req) is False


def test_dump_params_shapes():
    dump = a2_service.A2Execution._dump_params
    assert dump(_request("x", params=None)) == {}
    assert dump(_request("x", params={"a": 1})) == {"a": 1}
    model = mock.MagicMock()
    model.model_dump = mock.MagicMock(return_value={"b": 2})
    assert dump(_request("x", params=model)) == {"b": 2}
    assert dump(_request("x", params=42)) == {}  # 非 dict 非 model → 空


# ----------------------------- run_action dispatch / refusal


def test_run_action_dispatches_confirm_and_activate_to_a2_activation():
    """签认/激活走 a2_activation（现行接线）。"""
    with mock.patch.object(a2_activation, "confirm",
                           return_value={"ok": "confirm"}) as c, \
         mock.patch.object(a2_activation, "activate",
                           return_value={"ok": "activate"}) as a:
        ex_c = _execution("confirm_company_composition")
        assert ex_c.run_action() == {"ok": "confirm"}
        ex_a = _execution("activate_company_composition")
        assert ex_a.run_action() == {"ok": "activate"}
    c.assert_called_once_with(ex_c)
    a.assert_called_once_with(ex_a)
    # 拒绝/派发路径未触碰任何 SQL。
    ex_c.conn.execute.assert_not_called()
    ex_a.conn.execute.assert_not_called()


def test_run_action_dispatches_form_to_a2_composition():
    with mock.patch.object(a2_composition, "form",
                           return_value={"ok": "form"}) as f:
        ex = _execution("form_company_composition")
        assert ex.run_action() == {"ok": "form"}
    f.assert_called_once_with(ex)


def test_run_action_unknown_kind_rejected_before_any_write():
    ex = _execution("not_an_action")
    with pytest.raises(GovernedError) as ei:
        ex.run_action()
    assert ei.value.code == "ACTION_NOT_SUPPORTED_FOR_PROTOCOL"
    ex.conn.execute.assert_not_called()


# ----------------------------- payload validation


def test_a2_action_params_complete():
    for action, model in a2_models.A2_ACTION_PARAMS.items():
        assert action in a2_models.A2_ACTIONS
        assert model.__name__


def test_company_reference_payload_basic():
    payload = a2_models.CompanyReferencePayload.model_validate({
        "title": "Q4 reference",
        "statement": "本周期公司目标",
        "period_id": _fixed_uuid(1),
        "terms": {},
        "shared_with_domain_ids": [_fixed_uuid(2)],
        "upstream_refs": [],
    })
    dumped = payload.model_dump(mode="json", exclude_none=True)
    assert dumped["title"] == "Q4 reference"


def test_company_reference_payload_rejects_unknown_field():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        a2_models.CompanyReferencePayload.model_validate({
            "title": "x", "statement": "y", "period_id": _fixed_uuid(1),
            "terms": {}, "shared_with_domain_ids": [], "upstream_refs": [],
            "rogue_field": "not-in-contract",
        })


def test_capacity_observation_payload_accepts_any_iso():
    """future-time check is at admission (review 8), not at payload validation."""
    payload = a2_models.CapacityObservationPayload.model_validate({
        "title": "future",
        "resource_id": _fixed_uuid(1),
        "period_id": _fixed_uuid(2),
        "unit": "slot",
        "available": 1, "reserved": 0,
        "observed_at": "2999-01-01T00:00:00+00:00",
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to": None,
    })
    assert payload.observed_at == "2999-01-01T00:00:00+00:00"


def test_capacity_observation_payload_rejects_reserved_gt_available():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        a2_models.CapacityObservationPayload.model_validate({
            "title": "bad",
            "resource_id": _fixed_uuid(1),
            "period_id": _fixed_uuid(2),
            "unit": "slot",
            "available": 1, "reserved": 5,
            "observed_at": "2026-10-01T00:00:00+00:00",
            "valid_from": "2026-10-01T00:00:00+00:00",
            "valid_to": None,
        })


# ----------------------------- manifest helpers


def _manifest_dict(binding_dependencies):
    manifest_dict = {
        "manifest_schema_version": "tkos.composition-manifest/0.1",
        "scope_id": _fixed_uuid(1),
        "company_id": _fixed_uuid(2),
        "round_id": _fixed_uuid(3),
        "period_id": _fixed_uuid(4),
        "method_profile_ref": {
            "profile_id": "urn:tkos:experimental:method-profile:contract-a",
            "revision": "0.1.0",
            "canonical_hash": "a" * 64,
        },
        "member_set_version": 1,
        "input_set_version": 1,
        "company_reference_ref": {
            "object_id": _fixed_uuid(5),
            "revision_id": _fixed_uuid(6),
            "payload_hash": "b" * 64,
        },
        "members": [{
            "domain_id": _fixed_uuid(7),
            "dri_assignment_id": _fixed_uuid(8),
            "dri_principal_id": _fixed_uuid(9),
            "submission_ref": {
                "object_id": _fixed_uuid(10),
                "revision_id": _fixed_uuid(11),
                "payload_hash": "c" * 64,
            },
        }],
        "binding_dependencies": binding_dependencies,
        "judgments": {
            name: {"conclusion": "pass", "reason": "ok",
                   "evidence_refs": [], "judge_principal_id": _fixed_uuid(99)}
            for name in ("coverage", "coherence", "feasibility", "tradeoff")
        },
        "required_signers": [{
            "principal_id": _fixed_uuid(9),
            "assignment_id": _fixed_uuid(8),
            "responsibility_role": "area_accountable",
        }, {
            "principal_id": _fixed_uuid(99),
            "assignment_id": _fixed_uuid(98),
            "responsibility_role": "company_decider",
        }],
        "hash_scheme": "tkos-json-v1",
    }
    manifest_dict["manifest_hash"] = a2_models.compute_manifest_hash(
        {k: v for k, v in manifest_dict.items() if k != "manifest_hash"})
    return manifest_dict


def test_static_conflict_reasons_basic():
    manifest_dict = _manifest_dict([{
        "dependency_id": _fixed_uuid(12),
        "relation_type": "resource_capacity",
        "source_ref": {
            "object_id": _fixed_uuid(13),
            "revision_id": _fixed_uuid(14),
            "payload_hash": "d" * 64,
        },
        "constraint": {
            "kind": "capacity",
            "resource_id": _fixed_uuid(15),
            "period_id": _fixed_uuid(4),
            "unit": "slot",
            "required": 3, "available": 2,
        },
    }])
    manifest = a2_models.CompositionManifest.model_validate(manifest_dict)
    reasons = a2_models.static_conflict_reasons(manifest)
    assert any("deterministic capacity conflict" in r for r in reasons)


def test_validate_manifest_pass():
    manifest_dict = _manifest_dict([])
    manifest = a2_models.CompositionManifest.model_validate(manifest_dict)
    a2_models.validate_manifest(manifest)


# ----------------------------- finish / governance


def _finish_harness(kind, creation_fields):
    """Real A2Execution + patched base finish returning the result dict."""
    ex = _execution(kind)
    ex.creation_fields = creation_fields
    ex.action_assignments = [{"assignment_id": "a2"}, {"assignment_id": "a1"}]
    ex.dependencies = {_fixed_uuid(50)}
    ex.heads = {_fixed_uuid(50): {"object_version": 3}}
    return ex


def test_finish_attaches_governance_and_no_effect():
    ex = _finish_harness("open_formation_round", {
        "contract_version": "tkos.contract-a/0.1",
        "profile_id": "x", "profile_revision": "y",
        "profile_canonical_hash": "z" * 64,
    })
    result = {}
    with mock.patch.object(a2_service.ActionExecution, "finish",
                           lambda self, result, request_hash: result), \
         mock.patch.object(a2_composition, "recheck_live") as recheck, \
         mock.patch.object(a2_service.protocol, "current_binding",
                           side_effect=AssertionError("creation_fields must win")):
        out = ex.finish(result, request_hash="h")
    assert ex.effect_task_ids == []
    governance = out["governance"]
    assert governance["contract_version"] == "tkos.contract-a/0.1"
    assert governance["method_profile_ref"] == {
        "profile_id": "x", "revision": "y", "canonical_hash": "z" * 64}
    assert governance["actor_assignment_ids"] == ["a1", "a2"]
    assert governance["dependency_versions"] == [
        {"object_id": _fixed_uuid(50), "object_version": 3}]
    recheck.assert_not_called()


def test_finish_without_creation_fields_uses_current_binding():
    """target 类动作没有 creation_fields：governance 取自当前绑定。"""
    ex = _finish_harness("confirm_company_composition", None)
    ex.target = {"object_id": _fixed_uuid(60)}
    binding = {"contract_version": "tkos.contract-a/0.1",
               "profile_id": "p", "profile_revision": "1",
               "profile_canonical_hash": "c" * 64}
    with mock.patch.object(a2_service.ActionExecution, "finish",
                           lambda self, result, request_hash: result), \
         mock.patch.object(a2_service.protocol, "current_binding",
                           return_value=binding) as cb, \
         mock.patch.object(a2_composition, "recheck_live"):
        out = ex.finish({}, request_hash="h")
    cb.assert_called_once_with(ex.conn, ex.ctx.scope_id, _fixed_uuid(60))
    assert out["governance"]["contract_version"] == "tkos.contract-a/0.1"


@pytest.mark.parametrize("kind", ["confirm_company_composition",
                                  "activate_company_composition"])
def test_finish_rechecks_live_after_signing_actions(kind):
    """签认/激活的 receipt 持久化后必须 recheck_live（活力复查接线）。"""
    ex = _finish_harness(kind, {
        "contract_version": "tkos.contract-a/0.1",
        "profile_id": "x", "profile_revision": "y",
        "profile_canonical_hash": "z" * 64,
    })
    with mock.patch.object(a2_service.ActionExecution, "finish",
                           lambda self, result, request_hash: result), \
         mock.patch.object(a2_composition, "recheck_live") as recheck:
        ex.finish({}, request_hash="h")
    recheck.assert_called_once_with(ex)
