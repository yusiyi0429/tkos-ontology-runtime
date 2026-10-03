"""tkos.world/0.2 读面补齐与 0.1 的等价（票 #93；不连数据库）。

0.2 契约在这几处写「同 0.1」：取子对象（第 15.1 节）、取对象时被指向的一端列出跨链关系与修订链 supersedes（第 6 节
沿用 0.1 第 6 节）、按属性解析的责任人还须当前在对象所在域持对应角色（第 3.3 节同 0.1 第 4 节）。这里用按语句应答的
假连接核对读投影的纯逻辑；路由按绑定版本分派取子对象在 test_world_v02_list 里核对，真库上的行为在
acceptance/world_v02 的 objects、revise_relate、list_objects 与 revocation 场景里跑。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from memory_service_runtime.governed import world_v01_readers as v01
from memory_service_runtime.governed import world_v02_legacy as legacy
from memory_service_runtime.governed import world_v02_readers as readers
from memory_service_runtime.governed import world_v02_registry as registry
from memory_service_runtime.governed.world_v01_models import RESPONSIBLE_ROLES

CTX = SimpleNamespace(scope_id="scope", principal_id="caller")


def uid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def pin(object_id, version, block=None, component=None):
    return {"object_id": object_id, "object_version": version, "revision_id": f"rev-{object_id[-2:]}-{version}",
            "block": block, "component": component}


def cited(object_id, version, block=None, component=None):
    ref = f"{object_id}@{version}" + (f"#{block}" if block else "") + (f"/{component}" if component else "")
    return {**pin(object_id, version, block, component), "ref": ref}


class Conn:
    """按语句应答：身份（类型、是否启用）、角色指派（身份、域、角色）、修订（对象、版本）、跨链关系与子对象的行。"""

    def __init__(self, *, people=None, roles=(), revisions=(), rows=()):
        self.people, self.roles, self.revisions, self.rows = people or {}, set(roles), set(revisions), list(rows)
        self.seen = []

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        self.seen.append((sql, params))
        rows = self.answer(sql, params)
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)

    def answer(self, sql, params):
        if sql.startswith("SELECT principal_id, principal_type, display_name FROM gov_principals"):
            kind, active = self.people.get(params[1], (None, False))
            return [{"principal_id": params[1], "principal_type": kind, "display_name": f"name-{params[1][-2:]}"}] \
                if active else []
        if sql.startswith("SELECT 1 FROM gov_role_assignments"):
            return [{"?column?": 1}] if tuple(params[1:4]) in self.roles else []
        if sql.startswith("SELECT revision_id FROM gov_object_revisions"):
            return [{"revision_id": f"rev-{params[1][-2:]}-{params[2]}"}] if (params[1], params[2]) in self.revisions else []
        if sql.startswith("SELECT o.object_id"):
            return self.rows
        raise AssertionError(f"unexpected query: {sql}")


# ------------------------------------------------------------ 责任人须当前持角色（契约第 3.3 节，同 0.1 第 4 节）
OWNER, IC, AGENT = uid(31), uid(32), uid(33)
MISSION = {"object_id": uid(1), "object_type": "Mission", "domain_id": "d"}
ACTIVITY = {"object_id": uid(2), "object_type": "Activity", "domain_id": "d"}


def responsible(head, person, **conn):
    return readers.responsible_principals(Conn(**conn), CTX, head, {"responsible": person})


def test_a_responsible_by_attribute_counts_only_while_holding_the_role_in_the_objects_domain():
    people = {OWNER: ("human", True)}
    assert responsible(MISSION, OWNER, people=people, roles={(OWNER, "d", "OWNER")}) == [
        {"principal_id": OWNER, "principal_type": "human", "display_name": "name-31"}]
    # 撤了角色、角色在别的域、只持别的角色：都不算责任人，属性仍指向他。
    assert responsible(MISSION, OWNER, people=people) == []
    assert responsible(MISSION, OWNER, people=people, roles={(OWNER, "other", "OWNER")}) == []
    assert responsible(MISSION, OWNER, people=people, roles={(OWNER, "d", "IC")}) == []


def test_a_disabled_or_unregistered_type_or_unassigned_responsible_is_nobody():
    roles = {(OWNER, "d", "OWNER"), (AGENT, "d", "OWNER"), (AGENT, "d", "AGENT")}
    assert responsible(MISSION, OWNER, people={OWNER: ("human", False)}, roles=roles) == []
    # Mission 的登记只给人一个角色：Agent 即使持着 OWNER 也不是 Mission 的责任人。
    assert responsible(MISSION, AGENT, people={AGENT: ("agent", True)}, roles=roles) == []
    assert responsible(ACTIVITY, AGENT, people={AGENT: ("agent", True)}, roles=roles) == [
        {"principal_id": AGENT, "principal_type": "agent", "display_name": "name-33"}]
    conn = Conn()
    assert readers.responsible_principals(conn, CTX, MISSION, {"responsible": None}) == [] and conn.seen == []


def test_the_roles_are_the_0_2_registrys_and_0_1_supplies_the_same_ones():
    """0.2 登记的属性规则按身份类型给角色；0.1 登记不带，0.1 对象的 0.2 视图按 0.1 的规定补，两版的角色相同。"""
    for object_type in ("Mission", "Task", "Activity"):
        rule = registry.object_spec(object_type)["responsible"]
        assert rule["source"] == "attribute" and rule["roles"] == RESPONSIBLE_ROLES[object_type]


# ------------------------------------------------------------ 修订链与指向它的跨链关系（契约第 6 节）
def test_supersedes_points_at_the_revision_the_read_one_replaced_and_the_first_replaces_nothing():
    oid = uid(3)
    conn = Conn(revisions={(oid, 1), (oid, 2)})
    assert readers.supersedes(conn, CTX, oid, 1) is None
    assert readers.supersedes(conn, CTX, oid, 3) == cited(oid, 2)
    assert readers.supersedes(conn, CTX, oid, 2) == cited(oid, 1)


def test_referenced_by_lists_each_relate_written_pin_to_any_version_from_the_latest_revisions():
    target, goal, mission, other = uid(4), uid(5), uid(6), uid(7)
    rows = [{"object_id": goal, "object_version": 3, "revision_id": f"rev-{goal[-2:]}-3",
             "payload": {"depends_on": [pin(target, 1), pin(other, 1)]}},
            {"object_id": mission, "object_version": 2, "revision_id": f"rev-{mission[-2:]}-2",
             "payload": {"depends_on": [], "contributes_to": [pin(target, 2)]}}]
    conn = Conn(rows=rows)
    assert readers.referenced_by(conn, CTX, target) == [
        {"field": "depends_on", "relation": "depends_on", "source": cited(goal, 3), "target": cited(target, 1)},
        {"field": "contributes_to", "relation": "contributes_to", "source": cited(mission, 2), "target": cited(target, 2)}]
    # 字段取 0.2 登记里由建关系写的：含 0.2 新增的周期目标 depends_on；建对象时写的 goal_ref、parent_ref 不算。
    written = {(item["type"], field["field"]) for item in registry.registry()["objects"]
               for field in item["relation_fields"] if field["written_by"] == "world_relate"}
    assert ("PeriodGoal", "depends_on") in written and {field for _, field in written} == {"depends_on", "contributes_to"}
    sql, params = conn.seen[0]
    assert "o.latest_revision_id=r.revision_id" in sql and [value for value in params if isinstance(value, str)][1:] \
        == ["depends_on", "contributes_to"]


def test_object_view_puts_both_into_business_and_defaults_to_none_and_empty():
    head = {"object_id": uid(8), "object_type": "Company", "object_version": 1, "lifecycle_status": "recorded",
            "latest_revision_id": "r", "effective_revision_id": "r", "domain_id": "d"}
    revision = {"revision_id": "r", "object_version": 1, "payload": {"title": "C", "external_refs": [], "blocks": {}}}
    plain = readers.object_view(head, revision, {}, responsible=[])["business"]
    assert (plain["supersedes"], plain["referenced_by"]) == (None, [])
    items = [{"field": "depends_on"}]
    given = readers.object_view(head, revision, {}, responsible=[], supersedes=cited(uid(8), 1),
                                referenced_by=items)["business"]
    assert (given["supersedes"], given["referenced_by"]) == (cited(uid(8), 1), items)


# ------------------------------------------------------------ 取子对象（契约第 15.1 节，同 0.1）
def test_children_are_the_objects_whose_latest_parent_ref_points_at_it(monkeypatch):
    parent, task, activity = uid(9), uid(10), uid(11)
    monkeypatch.setattr(readers, "readable", lambda conn, ctx, oid: ({"object_id": parent}, {}))
    rows = [{"object_id": task, "object_type": "Task", "object_version": 2, "revision_id": f"rev-{task[-2:]}-2",
             "payload": {"title": "准备演示", "parent_ref": pin(parent, 1)}},
            {"object_id": activity, "object_type": "Activity", "object_version": 1,
             "revision_id": f"rev-{activity[-2:]}-1", "payload": {"title": "录屏", "parent_ref": pin(parent, 3)}}]
    conn = Conn(rows=rows)
    assert readers.children(conn, CTX, parent) == {"object_id": parent, "children": [
        {"object_id": task, "object_type": "Task", "title": "准备演示", "version": 2, "revision_id": f"rev-{task[-2:]}-2",
         "ref": f"{task}@2", "parent_ref": cited(parent, 1)},
        {"object_id": activity, "object_type": "Activity", "title": "录屏", "version": 1,
         "revision_id": f"rev-{activity[-2:]}-1", "ref": f"{activity}@1", "parent_ref": cited(parent, 3)}]}
    sql, params = conn.seen[0]
    # 同 0.1：只按最新修订的 parent_ref（Mission 挂周期目标的 goal_ref 不算子对象），按建立先后。
    assert "parent_ref" in sql and "goal_ref" not in sql and "o.latest_revision_id=r.revision_id" in sql
    assert sql.endswith("ORDER BY o.created_at, o.object_id") and params == ("scope", parent)


def test_children_of_a_0_1_object_are_not_read_under_0_2(monkeypatch):
    from memory_service_runtime.governed.errors import GovernedError
    monkeypatch.setattr(readers, "head_and_binding", lambda conn, ctx, oid: (
        {"object_id": oid}, {"contract_version": "tkos.world/0.1"}))
    monkeypatch.setattr(readers.protocol, "read_metadata", lambda conn, scope, oid: {"interpretation_status": "world_v0_1"})
    with pytest.raises(GovernedError) as refused:
        readers.children(Conn(), CTX, uid(12))
    assert refused.value.code == "PROTOCOL_NOT_SUPPORTED"


# ------------------------------------------------------------ 0.1 对象的 0.2 视图（契约第 15.4 节）
def test_the_0_2_view_of_a_0_1_object_gives_both_from_0_1_data_in_0_2_form(monkeypatch):
    oid, source, owner = uid(13), uid(14), uid(15)
    head = {"object_id": oid, "object_type": "Mission", "object_version": 3, "lifecycle_status": "confirmed",
            "latest_revision_id": f"rev-{oid[-2:]}-2", "effective_revision_id": None, "domain_id": "d"}
    old_pin = {key: value for key, value in pin(oid, 1).items() if key != "component"}  # 0.1 的钉定没有组件
    source_pin = {key: value for key, value in pin(source, 4).items() if key != "component"}
    monkeypatch.setattr(v01, "readable", lambda conn, ctx, object_id: (head, {"interpretation_status": "world_v0_1"}))
    monkeypatch.setattr(v01, "lifecycle", lambda conn, ctx, head: None)
    monkeypatch.setattr(v01, "referenced_by", lambda conn, ctx, object_id: [
        {"field": "depends_on", "relation": "depends_on", "source": v01.cited(source_pin), "target": v01.cited(old_pin)}])
    monkeypatch.setattr(legacy, "latest_snapshot", lambda conn, ctx, subject: None)

    class Revisions(Conn):
        def answer(self, sql, params):
            if sql.startswith("SELECT * FROM gov_object_revisions"):
                return [{"revision_id": f"rev-{oid[-2:]}-2", "object_version": 2, "payload": {
                    "title": "试点一", "core_battle": False, "responsible": owner, "goal_ref": None, "depends_on": [],
                    "contributes_to": [], "blocks": {"definition": None, "acceptance": None, "play": None,
                                                     "constraint": None}}}]
            return super().answer(sql, params)

    held = Revisions(people={owner: ("human", True)}, roles={(owner, "d", "OWNER")}, revisions={(oid, 1)})
    view = legacy.read_object(held, CTX, oid)
    business = view["business"]
    assert business["supersedes"] == cited(oid, 1)
    assert business["referenced_by"] == [
        {"field": "depends_on", "relation": "depends_on", "source": cited(source, 4), "target": cited(oid, 1)}]
    # 责任人同 0.1 的读法：按 0.1 的规定须持 OWNER；撤了角色就没有责任人。identity 原样回显 0.1 登记的规则。
    assert [person["principal_id"] for person in view["identity"]["responsible"]["principals"]] == [owner]
    assert view["identity"]["responsible"] == {"role": None, "source": "attribute",
                                               "principals": view["identity"]["responsible"]["principals"]}
    revoked = Revisions(people={owner: ("human", True)}, revisions={(oid, 1)})
    assert legacy.read_object(revoked, CTX, oid)["identity"]["responsible"]["principals"] == []


def test_the_0_2_view_of_a_0_1_object_has_the_same_business_keys_as_a_0_2_object():
    head = {"object_id": uid(16), "object_type": "Mission", "object_version": 1, "lifecycle_status": "draft",
            "latest_revision_id": "r", "effective_revision_id": None, "domain_id": "d"}
    v02_payload = {"title": "M", "core_battle": False, "responsible": None, "external_refs": [], "goal_ref": None,
                   "depends_on": [], "contributes_to": [], "component_ledger": [], "blocks": {}}
    v01_payload = {"title": "M", "core_battle": False, "responsible": None, "goal_ref": None, "depends_on": [],
                   "contributes_to": [], "blocks": {"definition": None, "acceptance": None, "play": None,
                                                    "constraint": None}}
    current = readers.object_view(head, {"revision_id": "r", "object_version": 1, "payload": v02_payload}, {},
                                  responsible=[])
    older = legacy.object_view(head, {"revision_id": "r", "object_version": 1, "payload": v01_payload}, {},
                               responsible=[])
    assert set(older["business"]) == set(current["business"]) >= {"supersedes", "referenced_by"}
