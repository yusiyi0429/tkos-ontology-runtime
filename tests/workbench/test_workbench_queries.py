"""分页授权过滤、current policy 域可见性、精确 revision 关系、回执整体授权。"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import workbench
from memory_service_runtime.governed.errors import GovernedError

from fakes import CTX, FakeConn, domain_row, object_row, revision_row, ts, uid


@pytest.fixture
def readable_domains(monkeypatch):
    readable = {uid(1), uid(3)}

    def fake_authorize(conn, ctx, domain_id, action_type="read"):
        if str(domain_id) not in readable:
            raise GovernedError("FORBIDDEN")
        return [{"assignment_id": uid(1002)}]

    monkeypatch.setattr(workbench.db, "authorize_domain", fake_authorize)
    return readable


def test_domains_hide_unreadable_without_leaking_counts(monkeypatch, readable_domains):
    conn = FakeConn(domains=[domain_row(1, "域甲"), domain_row(2, "域乙"), domain_row(3, "域丙")])
    page1 = workbench.domains(conn, CTX, 1, None)
    assert [item["name"] for item in page1["items"]] == ["域甲"]
    assert page1["next_cursor"]
    assert len(page1["items"][0]) == 2  # 只有 domain_id/name，无计数
    page2 = workbench.domains(conn, CTX, 1, page1["next_cursor"])
    assert [item["name"] for item in page2["items"]] == ["域丙"]  # 域乙被整条隐藏
    assert page2["next_cursor"] is None


def test_domains_cursor_is_bound_to_principal(readable_domains):
    conn = FakeConn(domains=[domain_row(1, "域甲"), domain_row(3, "域丙")])
    page1 = workbench.domains(conn, CTX, 1, None)
    other = CTX.__class__(**{**CTX.__dict__, "principal_id": uid(2002)})
    with pytest.raises(GovernedError) as caught:
        workbench.domains(conn, other, 1, page1["next_cursor"])
    assert caught.value.code == "INVALID_REQUEST"


def _objects_conn():
    revisions = [revision_row(11, 1, payload={"title": "反馈甲"}),
                 revision_row(12, 2, payload={"title": "决策乙"})]
    objects = [object_row(1, 1, second=1, latest=11, effective=11),
               object_row(2, 1, second=2, object_type="Decision", latest=12),
               object_row(3, 2, second=3)]
    return FakeConn(domains=[domain_row(1, "域甲"), domain_row(2, "域乙")],
                    objects=objects, revisions=revisions)


def test_objects_title_comes_from_latest_revision_and_paginates(readable_domains):
    conn = _objects_conn()
    page1 = workbench.objects(conn, CTX, uid(1), None, 1, None)
    assert [item["title"] for item in page1["items"]] == ["反馈甲"]
    assert set(page1["items"][0]) == {"object_id", "domain_id", "object_type", "lifecycle_status",
                                      "object_version", "latest_revision_id", "effective_revision_id",
                                      "created_at", "title", "protocol"}
    item_protocol = page1["items"][0]["protocol"]
    assert item_protocol["registration_status"] == "registered"
    assert item_protocol["interpretation_status"] == "legacy_v0_2"
    assert item_protocol["protocol_id"] == "tkos.legacy-governed"
    page2 = workbench.objects(conn, CTX, uid(1), None, 1, page1["next_cursor"])
    assert [item["title"] for item in page2["items"]] == ["决策乙"]
    assert page2["items"][0]["effective_revision_id"] is None  # latest 与 effective 分开
    assert page2["next_cursor"] is None
    assert "payload" not in page2["items"][0] and "result" not in page2["items"][0]


def test_objects_reject_unknown_type_and_unreadable_domain(readable_domains):
    conn = _objects_conn()
    with pytest.raises(GovernedError) as caught:
        workbench.objects(conn, CTX, uid(1), "Mission", 50, None)
    assert caught.value.code == "INVALID_REQUEST"
    with pytest.raises(GovernedError) as caught:
        workbench.objects(conn, CTX, uid(2), None, 50, None)  # 不可读域统一 NOT_FOUND
    assert caught.value.code == "NOT_FOUND"
    with pytest.raises(GovernedError) as caught:
        workbench.objects(conn, CTX, uid(7777), None, 50, None)  # 不存在的域同样 NOT_FOUND
    assert caught.value.code == "NOT_FOUND"


def test_revisions_mark_latest_and_effective_separately(monkeypatch):
    head = {"object_id": uid(1), "latest_revision_id": uid(12), "effective_revision_id": uid(11)}
    monkeypatch.setattr(workbench.db, "object_row", lambda conn, ctx, oid: head)
    conn = FakeConn(revisions=[revision_row(11, 1, second=1), revision_row(12, 1, second=2)],
                    protocol_objects={uid(1)})
    result = workbench.revisions(conn, CTX, uid(1), 50, None)
    flags = {item["revision_id"]: (item["is_latest"], item["is_effective"]) for item in result["items"]}
    assert flags == {uid(11): (False, True), uid(12): (True, False)}
    assert result["items"][0]["object_version"] == 1  # revision 建立时的对象版本
    assert result["next_cursor"] is None


def _relations_mocks(monkeypatch, readable_objects):
    heads = {oid: {"object_id": oid, "object_type": object_type,
                   "latest_revision_id": rid}
             for oid, (object_type, rid) in readable_objects.items()}
    monkeypatch.setattr(workbench.db, "object_row",
                        lambda conn, ctx, oid: heads[str(oid)] if str(oid) in heads else (_ for _ in ()).throw(GovernedError("NOT_FOUND")))
    monkeypatch.setattr(workbench.db, "revision_row",
                        lambda conn, ctx, oid, rid: {"revision_id": rid, "object_id": oid, "payload": PAYLOADS[str(rid)][0]}
                        if str(rid) in PAYLOADS and PAYLOADS[str(rid)][1] == str(oid)
                        else (_ for _ in ()).throw(GovernedError("NOT_FOUND")))


PAYLOADS: dict[str, tuple[dict, str]] = {}


def test_relations_use_exact_source_revision_and_hide_unreadable_targets(monkeypatch):
    source_oid, source_rid = uid(1), uid(10)
    PAYLOADS[uid(10)] = ({"upstream_refs": [{"object_id": uid(2), "revision_id": uid(20)}],
                          "execution_commitment_ref": {"object_id": uid(2), "revision_id": uid(20)},
                          "feedback_ref": {"object_id": uid(3), "revision_id": uid(30)}}, source_oid)
    PAYLOADS[uid(20)] = ({}, uid(2))
    PAYLOADS[uid(30)] = ({}, uid(3))
    _relations_mocks(monkeypatch, {source_oid: ("WorkItem", uid(10)),
                                   uid(2): ("ExecutionCommitment", uid(20))})  # uid(3) 无权
    conn = FakeConn(protocol_objects={source_oid, uid(2)})  # 仅已知可读对象登记为 legacy
    result = workbench.relations(conn, CTX, source_oid, None, 50, None)
    assert result["source_ref"] == {"object_id": source_oid, "revision_id": source_rid}
    assert [item["target_ref"] for item in result["items"]] == [
        {"object_id": uid(2), "revision_id": uid(20)}]  # 完全相同引用去重；无权目标整条隐藏
    assert result["items"][0]["relation_type"] == "source_reference"
    assert result["items"][0]["target_type"] == "ExecutionCommitment"
    assert uid(3) not in str(result)


def test_relations_resolve_adjustment_revision_only_fields(monkeypatch):
    source_oid, source_rid = uid(4), uid(40)
    PAYLOADS[uid(40)] = ({"feedback_revision_id": uid(50), "decision_revision_id": uid(60),
                          "changes": [{"object_id": uid(7), "from_revision_id": uid(70),
                                       "to_revision_id": uid(71)}]}, source_oid)
    PAYLOADS[uid(50)] = ({}, uid(5))
    PAYLOADS[uid(60)] = ({}, uid(6))
    PAYLOADS[uid(70)] = ({}, uid(7))
    PAYLOADS[uid(71)] = ({}, uid(7))
    _relations_mocks(monkeypatch, {source_oid: ("ManagementAdjustment", uid(40)),
                                   uid(5): ("FeedbackThread", uid(50)),
                                   uid(6): ("Decision", uid(60)),
                                   uid(7): ("BusinessCommitment", uid(71))})
    conn = FakeConn(revision_owners={uid(50): uid(5), uid(60): uid(6), uid(70): uid(7), uid(71): uid(7)},
                    protocol_objects={source_oid, uid(5), uid(6), uid(7)})
    result = workbench.relations(conn, CTX, source_oid, source_rid, 50, None)
    targets = {(item["target_ref"]["object_id"], item["target_ref"]["revision_id"]) for item in result["items"]}
    assert targets == {(uid(5), uid(50)), (uid(6), uid(60)), (uid(7), uid(70)), (uid(7), uid(71))}
    # 同对象不同 revision 保留


def test_action_receipts_hide_whole_entry_when_any_reference_unreadable(monkeypatch):
    head = {"object_id": uid(1), "latest_revision_id": uid(11), "effective_revision_id": uid(11)}
    monkeypatch.setattr(workbench.db, "object_row", lambda conn, ctx, oid: head)
    good = {"receipt_id": uid(21), "action_type": "accept_work_item", "principal_id": uid(1001),
            "status": "committed", "recorded_at": ts(1), "result": {"referenced_object_ids": [uid(1)]},
            "object_versions": [{"object_id": uid(1), "object_version": 1}], "target_object_id": uid(1)}
    tainted = {"receipt_id": uid(22), "action_type": "review_deliverable", "principal_id": uid(1001),
               "status": "committed", "recorded_at": ts(2), "result": {"referenced_object_ids": [uid(1), uid(9)]},
               "object_versions": [{"object_id": uid(1), "object_version": 2}], "target_object_id": None}
    unrelated = {"receipt_id": uid(23), "action_type": "confirm_decision", "principal_id": uid(1001),
                 "status": "committed", "recorded_at": ts(3), "result": {"referenced_object_ids": [uid(8)]},
                 "object_versions": [{"object_id": uid(8), "object_version": 1}], "target_object_id": uid(8)}

    def fake_authorize(conn, ctx, receipt):
        if uid(9) in [str(i) for i in receipt["result"].get("referenced_object_ids", [])]:
            raise GovernedError("NOT_FOUND")

    monkeypatch.setattr(workbench.readers, "authorize_receipt", fake_authorize)
    conn = FakeConn(receipts=[good, tainted, unrelated], protocol_objects={uid(1)})
    result = workbench.action_receipts(conn, CTX, uid(1), 50, None)
    assert [item["receipt_id"] for item in result["items"]] == [uid(21)]
    item = result["items"][0]
    assert set(item) == {"receipt_id", "action_type", "actor_id", "status", "recorded_at"}
    assert uid(22) not in str(result)  # 不局部删 payload，不返回隐藏数量


def test_responsibility_requires_work_item_and_projects_frozen_assignments(monkeypatch):
    monkeypatch.setattr(workbench.db, "object_row", lambda conn, ctx, oid: {
        "object_id": str(oid), "object_type": "WorkItem",
        "latest_revision_id": uid(11), "effective_revision_id": uid(11)})
    state = {"work_item_revision_id": uid(11), "dri_assignment_id": uid(31),
             "acceptor_assignment_id": uid(32)}
    assignments = [
        {"assignment_id": uid(31), "principal_id": uid(41), "role": "MISSION_DRI",
         "domain_id": uid(1), "display_name": "合成任务负责", "current_assignment_active": True},
        {"assignment_id": uid(32), "principal_id": uid(42), "role": "VERIFIER",
         "domain_id": uid(1), "display_name": "合成验收", "current_assignment_active": False},
    ]
    conn = FakeConn(work_item_state=state, assignment_rows=assignments, protocol_objects={uid(1)})
    result = workbench.responsibility(conn, CTX, uid(1))
    assert result["baseline_revision_id"] == uid(11)
    assert result["dri"]["display_name"] == "合成任务负责"
    assert result["dri"]["current_assignment_active"] is True
    assert result["acceptor"]["current_assignment_active"] is False
    assert set(result["dri"]) == {"assignment_id", "principal_id", "display_name",
                                  "role", "domain_id", "current_assignment_active"}

    monkeypatch.setattr(workbench.db, "object_row", lambda conn, ctx, oid: {
        "object_id": str(oid), "object_type": "Decision",
        "latest_revision_id": uid(11), "effective_revision_id": uid(11)})
    with pytest.raises(GovernedError) as caught:
        workbench.responsibility(conn, CTX, uid(1))
    assert caught.value.code == "INVALID_REQUEST"


def _relations_mocks_mutable(monkeypatch, visible_objects, denied_revisions):
    """visible_objects/denied_revisions 为可变集合，用于模拟翻页之间的撤权。"""

    def fake_object_row(conn, ctx, oid):
        if str(oid) in visible_objects:
            object_type, rid = visible_objects[str(oid)]
            return {"object_id": str(oid), "object_type": object_type, "latest_revision_id": rid}
        raise GovernedError("NOT_FOUND")

    def fake_revision_row(conn, ctx, oid, rid):
        if (str(oid), str(rid)) in denied_revisions or str(rid) not in PAYLOADS:
            raise GovernedError("NOT_FOUND")
        if PAYLOADS[str(rid)][1] != str(oid):
            raise GovernedError("NOT_FOUND")
        return {"revision_id": str(rid), "object_id": str(oid), "payload": PAYLOADS[str(rid)][0]}

    monkeypatch.setattr(workbench.db, "object_row", fake_object_row)
    monkeypatch.setattr(workbench.db, "revision_row", fake_revision_row)


def _three_target_source():
    source_oid, source_rid = uid(1), uid(10)
    PAYLOADS[uid(10)] = ({"upstream_refs": [
        {"object_id": uid(2), "revision_id": uid(20)},
        {"object_id": uid(3), "revision_id": uid(30)},
        {"object_id": uid(4), "revision_id": uid(40)},
    ]}, source_oid)
    for rid, oid in ((uid(20), uid(2)), (uid(30), uid(3)), (uid(40), uid(4))):
        PAYLOADS[rid] = ({}, oid)
    return source_oid, source_rid


def test_relations_hide_edge_when_target_revision_denied(monkeypatch):
    """target 对象可见但指定 target revision 被拒绝时，整条边隐藏。"""
    source_oid, _ = _three_target_source()
    visible = {uid(1): ("WorkItem", uid(10)), uid(2): ("ExecutionCommitment", uid(20)),
               uid(3): ("FeedbackThread", uid(30)), uid(4): ("EvidenceAsset", uid(40))}
    denied = {(uid(2), uid(20))}  # 对象可读，但目标 revision 精确版本不可读
    _relations_mocks_mutable(monkeypatch, visible, denied)
    result = workbench.relations(FakeConn(protocol_objects=set(visible)), CTX, source_oid, None, 50, None)
    targets = [item["target_ref"]["object_id"] for item in result["items"]]
    assert targets == [uid(3), uid(4)]
    assert uid(2) not in str(result)  # 不泄露被拒绝 revision 的所属对象


def test_relations_paginate_over_visible_edges_only(monkeypatch):
    """可见/隐藏/可见目标 limit=1 翻页；next_cursor 只反映可见项。"""
    source_oid, _ = _three_target_source()
    visible = {uid(1): ("WorkItem", uid(10)), uid(2): ("ExecutionCommitment", uid(20)),
               uid(4): ("EvidenceAsset", uid(40))}  # uid(3) 对象不可读
    _relations_mocks_mutable(monkeypatch, visible, set())
    conn = FakeConn(protocol_objects=set(visible))
    page1 = workbench.relations(conn, CTX, source_oid, None, 1, None)
    assert [item["target_ref"]["object_id"] for item in page1["items"]] == [uid(2)]
    assert page1["next_cursor"]
    page2 = workbench.relations(conn, CTX, source_oid, None, 1, page1["next_cursor"])
    assert [item["target_ref"]["object_id"] for item in page2["items"]] == [uid(4)]
    assert page2["next_cursor"] is None  # uid(3) 隐藏后不产生额外页
    assert uid(3) not in str(page1) and uid(3) not in str(page2)


def test_relations_recheck_current_rights_after_cursor_issued(monkeypatch):
    """旧 cursor 发出后撤销后续 target 权限：下一页按当前权限过滤并返回末页 null。"""
    source_oid, _ = _three_target_source()
    visible = {uid(1): ("WorkItem", uid(10)), uid(2): ("ExecutionCommitment", uid(20)),
               uid(4): ("EvidenceAsset", uid(40))}
    _relations_mocks_mutable(monkeypatch, visible, set())
    conn = FakeConn(protocol_objects=set(visible))
    page1 = workbench.relations(conn, CTX, source_oid, None, 1, None)
    assert [item["target_ref"]["object_id"] for item in page1["items"]] == [uid(2)]
    del visible[uid(4)]  # 翻页之间撤权
    page2 = workbench.relations(conn, CTX, source_oid, None, 1, page1["next_cursor"])
    assert page2["items"] == []
    assert page2["next_cursor"] is None
    assert uid(4) not in str(page2)


def _reader_key_cases(endpoint, filters, invoke, monkeypatch):
    """合法 Base64/JSON/envelope/绑定，仅 k 的构造错误；确认失败发生在 key 校验。"""
    valid_ts = "2026-09-01T00:00:00+00:00"
    cases = [
        [],                          # 长度错误
        [valid_ts],                  # 长度错误（二元 key）
        [123, uid(2)],               # 成员类型错误
        [None, uid(2)],
        ["2026-09-01T00:00:00", uid(2)],   # 无时区 timestamp
        [valid_ts, "not-a-uuid"],    # UUID 错误
        ["not-a-time", uid(2)],
    ]
    if endpoint == "domains":
        cases = [[], [123], ["not-a-uuid"], [uid(2), uid(3)], None]
        cases = [case for case in cases if case is not None]
    if endpoint == "relations":
        cases = [[], [uid(2)], [123, uid(2)], ["not-a-uuid", uid(2)], [uid(2), 123],
                 [uid(2), uid(3), uid(4)]]
    for key in cases:
        cursor = workbench.encode_cursor(endpoint, CTX, filters, key)
        with pytest.raises(GovernedError) as caught:
            invoke(cursor)
        assert caught.value.code == "INVALID_REQUEST", (endpoint, key)
        assert caught.value.status == 422


def test_domains_cursor_key_validation():
    _reader_key_cases("domains", {}, lambda c: workbench.domains(FakeConn(), CTX, 50, c), None)


def test_objects_cursor_key_validation(monkeypatch, readable_domains):
    conn = FakeConn(domains=[domain_row(1, "域甲")])
    filters = {"domain_id": uid(1), "object_type": None}
    _reader_key_cases("objects", filters,
                      lambda c: workbench.objects(conn, CTX, uid(1), None, 50, c), monkeypatch)


def test_revisions_cursor_key_validation(monkeypatch):
    head = {"object_id": uid(1), "latest_revision_id": uid(11), "effective_revision_id": uid(11)}
    monkeypatch.setattr(workbench.db, "object_row", lambda conn, ctx, oid: head)
    _reader_key_cases("revisions", {"object_id": uid(1)},
                      lambda c: workbench.revisions(FakeConn(), CTX, uid(1), 50, c), monkeypatch)


def test_action_receipts_cursor_key_validation(monkeypatch):
    head = {"object_id": uid(1), "latest_revision_id": uid(11), "effective_revision_id": uid(11)}
    monkeypatch.setattr(workbench.db, "object_row", lambda conn, ctx, oid: head)
    _reader_key_cases("action-receipts", {"object_id": uid(1)},
                      lambda c: workbench.action_receipts(FakeConn(), CTX, uid(1), 50, c), monkeypatch)


def test_relations_cursor_key_validation(monkeypatch):
    source_oid, source_rid = uid(1), uid(10)
    PAYLOADS[uid(10)] = ({"upstream_refs": []}, source_oid)
    _relations_mocks_mutable(monkeypatch, {source_oid: ("WorkItem", uid(10))}, set())
    filters = {"object_id": source_oid, "revision_id": source_rid}
    _reader_key_cases("relations", filters,
                      lambda c: workbench.relations(FakeConn(protocol_objects={source_oid}),
                                                    CTX, source_oid, None, 50, c),
                      monkeypatch)
