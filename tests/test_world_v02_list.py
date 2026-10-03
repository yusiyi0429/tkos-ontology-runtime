"""tkos.world/0.2 的列对象、外部引用与 0.1 对象的 0.2 视图（票 #63，契约第 3.4、15.2、15.4 节；不连数据库）。

HTTP 这一层用假事务打路由：列对象的筛选与分页参数、不连库就能判的拒绝（在进入事务之前）、取对象与取状态按对象绑定
的契约版本与 view 参数分派。读投影的输出（对象头、0.1 对象的三组视图与 legacy_0_1 快照）按纯函数的输出核对。
库里的行、真 API 上的筛选、分页、外部引用冲突与 0.1 视图在 acceptance/world_v02 的 list_objects 场景里跑。
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from memory_service_runtime.governed import world_v02_models as models
from memory_service_runtime.governed.errors import GovernedError

V01, V02 = "tkos.world/0.1", "tkos.world/0.2"


def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


# ------------------------------------------------------------ 外部引用：一个对象里同一 (system, id) 只出现一次
def company(refs):
    return {"title": "E&O 公司", "external_refs": refs, "blocks": {}}


def test_an_object_may_carry_several_external_references_that_differ_in_system_or_id():
    refs = [{"system": "tianshu", "id": "card:1"}, {"system": "tianshu", "id": "card:2"},
            {"system": "feishu", "id": "card:1", "url": "https://example.test/c/1"}]
    assert models.validate_input("Company", company(refs))["external_refs"] == [
        {"system": "tianshu", "id": "card:1", "url": None}, {"system": "tianshu", "id": "card:2", "url": None},
        {"system": "feishu", "id": "card:1", "url": "https://example.test/c/1"}]


@pytest.mark.parametrize("refs", [
    [{"system": "tianshu", "id": "card:1"}, {"system": "tianshu", "id": "card:1"}],
    [{"system": "tianshu", "id": "card:1", "url": "https://example.test/a"},
     {"system": "tianshu", "id": "card:1", "url": "https://example.test/b"}],
])
def test_the_same_external_reference_twice_in_one_object_is_refused(refs):
    with pytest.raises(ValueError, match="once"):
        models.validate_input("Company", company(refs))


def test_a_revision_that_repeats_an_external_reference_is_refused():
    stored = models.stored("Company", {"title": "E&O 公司", "external_refs": [], "blocks": {},
                                       "component_ledger": []})
    with pytest.raises(ValueError, match="once"):
        models.merge_revision("Company", stored, {"external_refs": [{"system": "t", "id": "1"}] * 2})


# ------------------------------------------------------------ HTTP：列对象的参数与分派
class Calls:
    def __init__(self):
        self.transactions = 0
        self.listed = []


@pytest.fixture
def api(monkeypatch):
    """路由挂在假事务上：进入事务就记一次；列对象、取对象、取状态的实现换成记下参数的假函数。"""
    from memory_service_runtime.governed import (db, routes, world_v01_readers, world_v02_legacy, world_v02_list,
                                                 world_v02_readers)
    calls = Calls()
    bound = {uid(1): V01, uid(2): V02}

    @contextmanager
    def transaction(token):
        calls.transactions += 1
        yield "conn", SimpleNamespace(scope_id=uid(0), principal_id=uid(9))

    def bound_contract(conn, ctx, object_id):
        if object_id not in bound:
            raise GovernedError("NOT_FOUND")
        return bound[object_id]

    def listed(conn, ctx, given, limit, cursor):
        calls.listed.append((given, limit, cursor))
        return {"items": [], "next_cursor": None}

    def reader(name):
        def fake(conn, ctx, object_id, moment=None):
            return {"read_by": name, "object_id": object_id, "at": moment and str(moment)}
        return fake

    monkeypatch.setattr(db, "transaction", transaction)
    monkeypatch.setattr(world_v02_readers, "bound_contract", bound_contract)
    monkeypatch.setattr(world_v02_list, "list_objects", listed)
    for module, name in ((world_v01_readers, "0.1"), (world_v02_readers, "0.2"), (world_v02_legacy, "0.2 view of 0.1")):
        monkeypatch.setattr(module, "read_object", reader(name))
        monkeypatch.setattr(module, "state", reader(name))
    for module, name in ((world_v01_readers, "0.1"), (world_v02_readers, "0.2")):
        monkeypatch.setattr(module, "children", reader(name))
    app = FastAPI()
    app.include_router(routes.router)
    routes.install_errors(app)
    client = TestClient(app, headers={"Authorization": "Bearer synthetic"})
    return client, calls


EMPTY = {"unit_id": None, "domain_id": None, "type": None, "period": None, "external_system": None,
         "external_id": None}


def test_list_objects_passes_each_filter_the_page_size_and_the_cursor(api):
    client, calls = api
    response = client.get("/v1/world/objects")
    assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}
    assert response.headers["cache-control"] == "no-store"
    client.get("/v1/world/objects", params={"unit_id": uid(3).upper(), "type": "Mission", "period": "2026-10",
                                            "limit": 7, "cursor": "abc"})
    client.get("/v1/world/objects", params={"domain_id": uid(4), "external_system": "tianshu",
                                            "external_id": "card:12"})
    client.get("/v1/world/objects", params={"external_system": "tianshu"})
    assert calls.listed == [
        (EMPTY, 50, None),
        ({**EMPTY, "unit_id": uid(3), "type": "Mission", "period": "2026-10"}, 7, "abc"),
        ({**EMPTY, "domain_id": uid(4), "external_system": "tianshu", "external_id": "card:12"}, 50, None),
        ({**EMPTY, "external_system": "tianshu"}, 50, None)]


@pytest.mark.parametrize("query", [
    {"unit_id": uid(3), "domain_id": uid(4)},          # 按单元或按域，不能两个都给
    {"external_id": "card:12"},                         # 外部 id 要连同系统一起查
    {"period": "2026-13"}, {"period": "2026-1"}, {"period": "202610"},
    {"type": "Issue"}, {"type": "mission"}, {"type": "Principal"},
    {"unit_id": "not-a-uuid"}, {"domain_id": "x"},
    {"external_system": " "}, {"external_system": "t", "external_id": ""}, {"external_system": "x" * 257},
    {"limit": 0}, {"limit": 101}, {"limit": "many"},
    {"unit": uid(3)}, {"object_type": "Mission"}, {"view": V02},  # 不认识的参数不静默忽略
    [("type", "Mission"), ("type", "Task")],            # 重复的参数
])
def test_a_malformed_list_request_is_refused_before_touching_the_database(api, query):
    client, calls = api
    response = client.get("/v1/world/objects", params=query)
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_REQUEST"
    assert calls.transactions == 0 and calls.listed == []


@pytest.mark.parametrize("path", ["", "/state"])
def test_reads_take_the_0_2_view_of_a_0_1_object_only_when_asked(api, path):
    """0.1 对象默认仍按 0.1 读（形状不变）；带 view=tkos.world/0.2 时按 0.2 的三组读；0.2 对象带不带都是 0.2。"""
    client, _ = api
    read = {}
    for object_id in (uid(1), uid(2)):
        for query in ({}, {"view": V02}):
            response = client.get(f"/v1/world/objects/{object_id}{path}", params=query)
            assert response.status_code == 200
            read[object_id, bool(query)] = response.json()["read_by"]
    assert read == {(uid(1), False): "0.1", (uid(1), True): "0.2 view of 0.1", (uid(2), False): "0.2",
                    (uid(2), True): "0.2"}
    for bad in (V01, "tkos.world/0.3", "grouped", ""):
        response = client.get(f"/v1/world/objects/{uid(1)}{path}", params={"view": bad})
        assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_REQUEST"
    missing = client.get(f"/v1/world/objects/{uid(5)}{path}", params={"view": V02})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_children_are_read_under_the_version_the_object_is_bound_to(api):
    """取子对象（0.2 契约第 15.1 节「同 0.1」，票 #93）：0.2 对象按 0.2 读，不再 409；0.1 对象仍按 0.1 读。"""
    client, _ = api
    assert {object_id: client.get(f"/v1/world/objects/{object_id}/children").json()["read_by"]
            for object_id in (uid(1), uid(2))} == {uid(1): "0.1", uid(2): "0.2"}
    missing = client.get(f"/v1/world/objects/{uid(5)}/children")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------ 对象头（契约第 15.2 节）
def row(**change):
    return {"object_id": uid(10), "object_type": "Mission", "domain_id": uid(20), "created_at": "2026-09-29T01:00:00+00:00",
            "object_version": 4, "version": 3, "revision_id": uid(30), "title": "试点一",
            "external_refs": [{"system": "tianshu", "id": "card:1", "url": None}], "contract_version": V02, **change}


def test_a_0_2_object_header_carries_what_section_15_2_lists():
    from memory_service_runtime.governed import world_v02_list as listing
    lifecycle = {"status": "in_progress", "display_name": "进行中", "event_id": uid(40), "round": None}
    assert listing.header(row(), lifecycle) == {
        "object_id": uid(10), "object_type": "Mission", "type_display_name": "Mission",
        "category": {"id": "business_object", "display_name": "业务对象"}, "title": "试点一",
        "version": 3, "revision_id": uid(30), "object_version": 4,
        "lifecycle": {"status": "in_progress", "display_name": "进行中", "event_id": uid(40)},
        "domain_id": uid(20), "external_refs": [{"system": "tianshu", "id": "card:1", "url": None}],
        "contract_version": V02}


def test_a_snapshot_and_a_0_1_object_have_headers_of_the_same_shape():
    from memory_service_runtime.governed import world_v02_list as listing
    snapshot = listing.header(row(object_type="StateSnapshot", title="周进展", external_refs=None), None)
    assert (snapshot["category"], snapshot["type_display_name"], snapshot["lifecycle"], snapshot["external_refs"]) == (
        {"id": "time_record", "display_name": "时间记录"}, "状态快照", None, [])
    legacy = listing.header(row(object_type="LongTermGoal", title="三年目标", external_refs=None, contract_version=V01),
                            {"status": "draft", "display_name": "草稿", "event_id": uid(41)})
    assert set(legacy) == set(snapshot) and legacy["contract_version"] == V01 and legacy["external_refs"] == []
    assert (legacy["type_display_name"], legacy["category"]["id"], legacy["lifecycle"]) == (
        "长期目标", "business_object", {"status": "draft", "display_name": "草稿", "event_id": uid(41)})


# ------------------------------------------------------------ 0.1 对象的 0.2 视图（契约第 15.4 节）
def pin(object_id, version, block=None):
    """0.1 的钉定引用：没有组件这一项。"""
    return {"object_id": object_id, "object_version": version, "revision_id": uid(900 + version), "block": block}


def test_a_0_1_mission_is_read_in_three_groups_under_the_0_1_registry():
    from memory_service_runtime.governed import world_v02_legacy as legacy
    oid, rid, goal, owner = uid(11), uid(31), uid(12), uid(50)
    head = {"object_id": oid, "object_type": "Mission", "object_version": 5, "lifecycle_status": "confirmed",
            "latest_revision_id": rid, "effective_revision_id": rid, "domain_id": uid(20)}
    revision = {"revision_id": rid, "object_version": 2, "payload": {
        "title": "试点一", "core_battle": False, "responsible": owner, "goal_ref": pin(goal, 1),
        "depends_on": [], "contributes_to": [],
        "blocks": {"definition": {"text": "做试点。", "refs": [pin(goal, 1, "acceptance")], "artifacts": []},
                   "acceptance": None, "play": None, "constraint": None}}}
    people = [{"principal_id": owner, "principal_type": "human", "display_name": "Owner"}]
    lifecycle = {"status": "established", "display_name": "已成立", "event_id": uid(60)}
    view = legacy.object_view(head, revision, {"interpretation_status": "world_v0_1", "contract_version": V01},
                              responsible=people, lifecycle=lifecycle, latest_state={"object_id": uid(70)})
    assert set(view) == {"object_id", "business", "identity", "records", "protocol"}
    business = view["business"]
    assert set(business) == {"object_id", "object_type", "type_display_name", "category", "candidate", "version",
                             "revision_id", "object_version", "title", "attributes", "relations", "referenced_by",
                             "supersedes", "blocks", "component_ledger", "projection", "formal", "round"}
    assert business["projection"] is None and business["referenced_by"] == [] and business["supersedes"] is None
    assert (business["object_type"], business["category"], business["candidate"], business["version"],
            business["object_version"], business["title"]) == (
        "Mission", {"id": "business_object", "display_name": "业务对象"}, False, 2, 5, "试点一")
    # 属性与块按 0.1 登记：没有外部引用与执行计划块；0.1 没有块类别与组件。
    assert business["attributes"] == {"core_battle": False, "responsible": owner}
    assert [(b["id"], b["class"], b["empty"], b["text"], b["components"]) for b in business["blocks"]] == [
        ("definition", None, False, "做试点。", []), ("acceptance", None, True, "验收标准：暂无", []),
        ("play", None, True, "打法：暂无", []), ("constraint", None, True, "约束：暂无", [])]
    # 0.1 的引用读成 0.2 的对象或块形式：钉定结构补上 component，另给业务形式。
    assert business["blocks"][0]["value"] == {"text": "做试点。", "components": [], "artifacts": [], "refs": [
        {**pin(goal, 1, "acceptance"), "component": None, "ref": f"{goal}@1#acceptance"}]}
    assert business["blocks"][0]["ref"] == f"{oid}@2#definition"
    assert business["relations"] == [
        {"field": "goal_ref", "relation": "serves", "value": {**pin(goal, 1), "component": None, "ref": f"{goal}@1"}},
        {"field": "depends_on", "relation": "depends_on", "value": []},
        {"field": "contributes_to", "relation": "contributes_to", "value": []}]
    assert business["component_ledger"] == [] and business["round"] is None
    assert business["formal"] == {"lifecycle_status": "confirmed", "effective_revision_id": rid}
    assert view["identity"] == {"responsible": {"role": None, "source": "attribute", "principals": people},
                                "delegations": []}
    assert view["records"] == {"lifecycle": lifecycle, "latest_state": {"object_id": uid(70)},
                               "confirmed_review": None, "open_issues": []}
    assert view["protocol"]["contract_version"] == V01


def test_a_0_1_snapshot_is_read_as_the_read_only_legacy_payload():
    from memory_service_runtime.governed import world_v02_legacy as legacy
    sid, rid, subject, writer = uid(13), uid(33), uid(11), {"principal_id": uid(51), "display_name": "DRI"}
    revision = {"object_id": sid, "revision_id": rid, "object_version": 1, "payload": {
        "title": "周进展", "subject_ref": pin(subject, 2), "as_of": "2026-09-27T15:59:59Z", "period": "2026-09",
        "blocks": {"progress": {"text": "完成一半。", "refs": [pin(subject, 2, "acceptance")], "artifacts": []},
                   "issue": None, "artifacts": {"text": "", "refs": [], "artifacts": ["https://example.test/r"]}}}}
    view = legacy.snapshot_view({"object_id": sid}, revision, generator=writer)
    assert view == {
        "object_id": sid, "object_type": "StateSnapshot", "category": {"id": "time_record", "display_name": "时间记录"},
        "version": 1, "revision_id": rid, "ref": f"{sid}@1", "title": "周进展",
        "subject_ref": {**pin(subject, 2), "component": None, "ref": f"{subject}@2"},
        "as_of": "2026-09-27T15:59:59Z", "period": "2026-09", "generator": writer, "source_event_refs": [],
        "payload_type": {"id": "legacy_0_1", "display_name": "0.1 状态快照（只读）"},
        "blocks": view["blocks"], "unconfirmed": True}
    assert [(b["id"], b["display_name"], b["class"], b["empty"], b["text"]) for b in view["blocks"]] == [
        ("progress", "进展", None, False, "完成一半。"), ("issue", "问题", None, True, "问题：暂无"),
        ("artifacts", "产物", None, False, "")]
    assert view["blocks"][0]["value"]["refs"][0]["ref"] == f"{subject}@2#acceptance"
    assert view["blocks"][2]["ref"] == f"{sid}@1#artifacts"
