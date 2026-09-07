"""P1/P2 entity projection contract tests against a real isolated PostgreSQL scope."""
from __future__ import annotations

from datetime import datetime

from adapter.codes import slug_of
from adapter.contracts import GkEntity, GkVersion
from adapter.errors import status_for_exception
from adapter.main import app
from adapter.settings import Settings, get_settings
from adapter.wm_views import build_code_index, project_entity
from memory_service import working
from tests.asgi_client import get
from tests.conftest import DATABASE_URL, Scope, connect


def _target_for(index, *, kind: str, object_id: str | None = None):
    matches = [entry for entry in index.entries if entry.kind == kind]
    if object_id is not None:
        matches = [entry for entry in matches if entry.object_id == object_id]
    assert len(matches) == 1
    return matches[0]


def _configure_for(scope: Scope) -> None:
    settings = Settings(
        memory_tenant=scope.tenant_id,
        memory_org=scope.organization_id,
        database_url=DATABASE_URL,
        adapter_auth="clark:test",
        viewer_user_id=scope.user_id or "",
    )
    app.dependency_overrides[get_settings] = lambda: settings


def test_entity_projection_matches_gk_shapes_and_relations():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            first = s.seed_chain(conn, title="第一实体链", confirmed_judgment=True)
            second = s.seed_chain(conn, title="第二实体链", confirmed_judgment=False)
            index = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            issue_target = _target_for(index, kind="ISS", object_id=first["issue"]["object_id"])
            signal_target = _target_for(index, kind="SGN", object_id=first["signal"]["object_id"])
            judgment_target = _target_for(index, kind="JDG", object_id=first["judgment"]["object_id"])
            entity = project_entity(
                conn,
                issue_target.code,
                tenant_id=s.tenant_id,
                organization_id=s.organization_id,
            )
            signal = project_entity(
                conn,
                signal_target.code,
                tenant_id=s.tenant_id,
                organization_id=s.organization_id,
            )

            assert isinstance(entity, GkEntity)
            assert set(entity.model_dump()) == {"code", "label", "properties", "relations", "versions"}
            assert entity.code == issue_target.code
            assert entity.label == "第一实体链 应该怎么走？"
            assert entity.properties == {
                "key_question": "第一实体链 应该怎么走？",
                "state": "potential",
                "why": "adapter 测试种子",
                "monitoringNote": "",
            }
            assert entity.relations == {
                "shakes": [judgment_target.code],
                "hasJudgement": [judgment_target.code],
                "resolvedBy": [],
                "spawnsOf": [signal_target.code],
            }
            assert entity.versions is None

            assert signal.code == signal_target.code
            assert signal.label == "第一实体链 的信号"
            assert signal.properties == {
                "title": "第一实体链 的信号",
                "why": "adapter 测试种子信号",
            }
            assert signal.relations == {"spawns": [issue_target.code]}

            # The second chain is also indexed, and its unconfirmed judgment is
            # still a real Judgment object for P1 relation/index purposes.
            second_judgment = _target_for(
                index, kind="JDG", object_id=second["judgment"]["object_id"],
            )
            second_issue = _target_for(index, kind="ISS", object_id=second["issue"]["object_id"])
            assert second_judgment.code != judgment_target.code
            assert second_issue.code != issue_target.code
    finally:
        s.cleanup()


def test_latest_vs_chain_and_unconfirmed_history_status():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            confirmed = s.seed_chain(conn, title="已确认历史链", confirmed_judgment=True)
            pending = s.seed_chain(conn, title="未确认历史链", confirmed_judgment=False)
            index = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            confirmed_code = _target_for(
                index, kind="JDG", object_id=confirmed["judgment"]["object_id"],
            ).code
            pending_code = _target_for(
                index, kind="JDG", object_id=pending["judgment"]["object_id"],
            ).code

            latest = project_entity(
                conn, confirmed_code, view="latest",
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            history = project_entity(
                conn, confirmed_code, view="chain",
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            pending_history = project_entity(
                conn, pending_code, view="chain",
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )

            assert latest.versions is None
            assert history.versions is not None and len(history.versions) == 1
            version = history.versions[0]
            assert isinstance(version, GkVersion)
            assert set(version.model_dump()) == {"index", "status", "at", "statement", "meetingLabel"}
            assert version.index == 1
            assert version.status == "confirmed"
            assert version.statement == "关于 已确认历史链 的测试判断"
            assert version.meetingLabel is None
            assert version.at is not None
            datetime.fromisoformat(version.at)

            assert pending_history.versions is not None
            assert pending_history.versions[0].status == "unconfirmed"
            assert pending_history.versions[0].statement == "关于 未确认历史链 的测试判断"
    finally:
        s.cleanup()


def test_codes_are_global_and_collision_suffix_is_stable():
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            first = s.seed_chain(conn, title="A!", confirmed_judgment=True)
            second = s.seed_chain(conn, title="A?", confirmed_judgment=True)
            index1 = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            index2 = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            assert index1 == index2

            issue_codes = [entry.code for entry in index1.entries if entry.kind == "ISS"]
            assert issue_codes == [f"ISS-{slug_of('A!')}", f"ISS-{slug_of('A!')}-2"]
            judgment_codes = [entry.code for entry in index1.entries if entry.kind == "JDG"]
            signal_codes = [entry.code for entry in index1.entries if entry.kind == "SGN"]
            assert judgment_codes == ["JDG-01", "JDG-02"]
            assert signal_codes == ["SGN-01", "SGN-02"]
            assert all(code != "JDG-01" for code in judgment_codes[1:])
            assert first["judgment"]["object_id"] != second["judgment"]["object_id"]
    finally:
        s.cleanup()


def test_scope_index_does_not_leak_other_scope_and_route_contracts():
    first_scope = Scope()
    other_scope = Scope()
    try:
        with connect() as conn, conn.transaction():
            first = first_scope.seed_chain(conn, title="本 scope", confirmed_judgment=True)
            other_scope.seed_chain(conn, title="外部 scope", confirmed_judgment=True)
        with connect() as conn:
            index = build_code_index(
                conn,
                tenant_id=first_scope.tenant_id,
                organization_id=first_scope.organization_id,
            )
            assert all("外部" not in entry.label for entry in index.entries)
            issue_code = _target_for(index, kind="ISS", object_id=first["issue"]["object_id"]).code

        _configure_for(first_scope)
        try:
            response = get(app, f"/api/v1/entities/{issue_code}")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["code"] == issue_code
            assert payload["versions"] is None

            chain_response = get(app, f"/api/v1/entities/{issue_code}?view=chain")
            assert chain_response.status_code == 200, chain_response.text
            assert isinstance(chain_response.json()["versions"], list)

            assert get(app, "/api/v1/entities/ISS-does-not-exist").status_code == 404
            assert get(app, "/api/v1/entities/DOC-01").status_code == 404
            assert get(app, f"/api/v1/entities/{issue_code}?view=bogus").status_code == 422
        finally:
            app.dependency_overrides.clear()
    finally:
        first_scope.cleanup()
        other_scope.cleanup()


def test_issue_directory_is_scoped_deterministic_and_prefix_gated():
    scope = Scope()
    other_scope = Scope()
    try:
        with connect() as conn, conn.transaction():
            first = scope.seed_chain(conn, title="目录议题甲", confirmed_judgment=True)
            second = scope.seed_chain(conn, title="目录议题乙", confirmed_judgment=False)
            other_scope.seed_chain(conn, title="绝不泄漏", confirmed_judgment=True)
        with connect() as conn:
            index = build_code_index(
                conn, tenant_id=scope.tenant_id, organization_id=scope.organization_id,
            )
            label_by_object = {
                seeded["issue"]["object_id"]: f"{seeded['chain']['title']} 应该怎么走？"
                for seeded in (first, second)
            }
            expected = [
                {"code": target.code, "label": label_by_object[target.object_id]}
                for target in index.entries
                if target.kind == "ISS"
            ]

        _configure_for(scope)
        try:
            response = get(app, "/api/v1/entities?prefix=ISS-")
            assert response.status_code == 200, response.text
            assert response.json() == expected
            assert all(item["label"] != "绝不泄漏" for item in response.json())
            assert get(app, "/api/v1/entities").status_code == 422
            assert get(app, "/api/v1/entities?prefix=SGN-").status_code == 422
        finally:
            app.dependency_overrides.clear()
    finally:
        scope.cleanup()
        other_scope.cleanup()


def test_pre_issue_chain_uses_chain_label_without_fabricating_issue_data():
    s = Scope()
    try:
        title = "尚未立案的链"
        with connect() as conn, conn.transaction():
            human = s.ensure_human(conn)
            working.create_chain_with_first_signal(
                conn,
                title=title,
                signal_content={"title": "未立案信号", "description": "等待立案"},
                created_by=human,
                tenant_id=s.tenant_id,
                organization_id=s.organization_id,
            )
            index = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            target = _target_for(index, kind="ISS")
            entity = project_entity(
                conn,
                target.code,
                tenant_id=s.tenant_id,
                organization_id=s.organization_id,
            )
            assert entity.code == target.code
            assert entity.label == title
            assert entity.properties is None
            assert entity.relations is None

            history = project_entity(
                conn,
                target.code,
                view="chain",
                tenant_id=s.tenant_id,
                organization_id=s.organization_id,
            )
            assert history.versions == []
    finally:
        s.cleanup()


def test_route_db_errors_are_not_mapped_to_404():
    from psycopg import OperationalError

    assert status_for_exception(OperationalError("down")) == 503


def test_confirmed_agreement_projects_as_resolved_by_and_agr():
    """resolvedBy 只收 confirmed Agreement；未确认的提案仍可 P1 读但不进共识段。"""
    s = Scope()
    try:
        with connect() as conn, conn.transaction():
            human = s.ensure_human(conn)
            assert human is not None

            settled = s.seed_chain(conn, title="已共识链", confirmed_judgment=True)
            unsettled = s.seed_chain(conn, title="未共识链", confirmed_judgment=True)

            confirmed = working.create_agreement(
                conn,
                chain_id=settled["chain"]["chain_id"],
                issue_id=settled["issue"]["object_id"],
                statement="共同决定：先做 A 再做 B",
                disposition="close",
                confirmed_judgment_record_id=settled["judgment"]["record_id"],
                party_ids=[human],
                created_by=human,
            )
            working.confirm_agreement(conn, confirmed["record_id"], confirmer=human)

            pending = working.create_agreement(
                conn,
                chain_id=unsettled["chain"]["chain_id"],
                issue_id=unsettled["issue"]["object_id"],
                statement="还没签完的共识提案",
                disposition="strategic_mission",
                confirmed_judgment_record_id=unsettled["judgment"]["record_id"],
                party_ids=[human],
                created_by=human,
            )

            index = build_code_index(
                conn, tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            settled_issue = _target_for(
                index, kind="ISS", object_id=settled["issue"]["object_id"],
            ).code
            unsettled_issue = _target_for(
                index, kind="ISS", object_id=unsettled["issue"]["object_id"],
            ).code
            confirmed_code = _target_for(
                index, kind="AGR", object_id=confirmed["object_id"],
            ).code
            pending_code = _target_for(
                index, kind="AGR", object_id=pending["object_id"],
            ).code

            settled_entity = project_entity(
                conn, settled_issue,
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            assert settled_entity.relations["resolvedBy"] == [confirmed_code]

            unsettled_entity = project_entity(
                conn, unsettled_issue,
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            assert unsettled_entity.relations["resolvedBy"] == []

            agr = project_entity(
                conn, confirmed_code,
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            assert agr.label == "共同决定：先做 A 再做 B"
            assert agr.properties == {
                "agreementStatus": "confirmed",
                "text": "共同决定：先做 A 再做 B",
                "boundary": "",
            }

            pending_agr = project_entity(
                conn, pending_code,
                tenant_id=s.tenant_id, organization_id=s.organization_id,
            )
            assert pending_agr.properties["agreementStatus"] == "unconfirmed"
    finally:
        s.cleanup()
