"""tkos.world/0.2 的状态快照外壳、外部事件、迟记与更正（票 #52，契约第 7、8、11、15.1 节；不连数据库）。"""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / "docs/contracts/world-registry-0.2.json").read_text(encoding="utf-8"))
MIGRATION = ROOT / "src/memory_service_app/migrations/0039_world_v02.sql"
OID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
RID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e02"
EID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e03"
PID = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e04"
EID2 = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e05"
PAYLOAD_TYPES = {item["id"]: item for item in REGISTRY["state"]["payload_types"]}


def snapshot(payload_type="execution_state", **overrides):
    return {"title": "第 40 周", "subject_ref": f"{OID}@1", "as_of": "2026-09-27T23:59:59+08:00",
            "payload_type": payload_type, "source_event_refs": [f"event:{EID}"], "blocks": {}, **overrides}


ISSUE = {"id": "iss-7", "type": "issue", "text": "试点排期冲突",
         "attributes": {"core_question": "要不要把试点推迟一周？", "responsible_hint": PID}}
PROGRESS = {"id": "todo:17", "type": "progress_item", "text": "搭环境", "attributes": {
    "principal_id": PID, "principal_name": "张三", "external_status": "进行中",
    "entries": [{"at": "2026-09-26T10:00:00+08:00", "source": "codex", "text": "完成脚手架"},
                {"at": "2026-09-27T09:00:00Z", "source": "github", "text": "合并 PR", "url": "https://example.test/pr/1"}]}}


# ------------------------------------------------------------------ the shell
@pytest.mark.parametrize("payload_type, blocks", [
    ("execution_state", {"progress": {"components": [PROGRESS]}, "blockers": {"text": "等客户"},
                         "issues": {"components": [ISSUE]}, "materials": {"artifacts": ["https://example.test/a"]}}),
    ("goal_state", {"progress": {"text": "签了 1 家"}, "issues": {"components": [ISSUE]}}),
    ("unit_state", {"issues": {"components": [ISSUE]}}),
    ("strategy_state", {"issues": {"components": [ISSUE]}, "materials": {"text": "候选稿"}}),
    ("company_review", {"results": {"text": "收入达成八成"}, "gaps": {"text": "B 战场落后"}}),
])
def test_each_payload_type_takes_its_registered_blocks_and_components(payload_type, blocks):
    value = models.validate_snapshot(snapshot(payload_type, blocks=blocks))
    assert set(value["blocks"]) == {block["id"] for block in PAYLOAD_TYPES[payload_type]["blocks"]}
    assert value["payload_type"] == payload_type and value["source_event_refs"] == [f"event:{EID}"]


def test_as_of_is_stored_as_the_one_utc_text_for_that_moment():
    assert models.validate_snapshot(snapshot())["as_of"] == "2026-09-27T15:59:59Z"
    assert models.validate_snapshot(snapshot(as_of="2026-09-27T15:59:59.500+00:00"))["as_of"] \
        == "2026-09-27T15:59:59.500000Z"


def test_progress_entries_are_normalised_and_keep_their_shape():
    value = models.validate_snapshot(snapshot(blocks={"progress": {"components": [PROGRESS]}}))
    entries = value["blocks"]["progress"]["components"][0]["attributes"]["entries"]
    assert entries == [{"at": "2026-09-26T02:00:00Z", "source": "codex", "text": "完成脚手架", "url": None},
                       {"at": "2026-09-27T09:00:00Z", "source": "github", "text": "合并 PR",
                        "url": "https://example.test/pr/1"}]
    assert {item["id"] for item in REGISTRY["components"]["progress_entry_sources"]} >= {"codex", "github"}


@pytest.mark.parametrize("overrides", [
    {"generator": PID},                                           # 生成者由服务按凭证填
    {"source_event_refs": []},                                    # 来源事件至少一条
    {"source_event_refs": [f"{OID}@1"]},                          # 来源须是事件引用
    {"source_event_refs": [f"event:{EID}", f"event:{EID}"]},
    {"subject_ref": f"{OID}@1#definition"},                       # 主体是对象形式
    {"payload_type": "no_such_state"},
    {"as_of": "2026-09-27"},
    {"period": "2026-13"},
    {"blocks": {"results": {"text": "执行状态没有这个块"}}},
    {"blocks": {"issues": {"components": [{"type": "issue", "text": "缺核心判断问题"}]}}},
    {"blocks": {"issues": {"components": [{"type": "issue", "attributes": {"responsible_hint": PID}}]}}},
    {"blocks": {"blockers": {"components": [ISSUE]}}},             # 这个块不带组件
    {"blocks": {"issues": {"components": [ISSUE, ISSUE]}}},        # 组件 id 在快照内唯一
    {"blocks": {"progress": {"components": [{"type": "progress_item", "attributes": {
        "entries": [{"at": "2026-09-26T10:00:00Z", "source": "slack", "text": "x"}]}}]}}},
    {"blocks": {"progress": {"components": [{"type": "progress_item", "attributes": {
        "entries": [{"at": "2026-09-26T10:00:00Z", "source": "web"}]}}]}}},
    {"blocks": {"materials": {"text": "  "}}},
    {"note": "外壳之外的字段"},
])
def test_a_snapshot_that_breaks_the_shell_or_its_payload_is_refused(overrides):
    with pytest.raises(ValueError):
        models.validate_snapshot(snapshot(**overrides))


def test_each_business_type_has_exactly_one_registered_payload_type():
    business = [item["type"] for item in REGISTRY["objects"] if item["category"] == "business_object"]
    assert {t: models.payload_type_for(t) for t in business} == {
        "Company": "company_review", "Strategy": "strategy_state", "ResponsibilityUnit": "unit_state",
        "LongTermGoal": "goal_state", "PeriodGoal": "goal_state", "Mission": "execution_state",
        "Task": "execution_state", "Activity": "execution_state"}
    assert models.payload_type_for("StateSnapshot") is None


def test_storing_a_snapshot_pins_its_references_names_the_generator_and_names_missing_component_ids():
    written = models.validate_snapshot(snapshot(blocks={"issues": {"components": [
        ISSUE, {"type": "issue", "attributes": {"core_question": "谁来补人？"}, "refs": [f"event:{EID2}"]}]}}))
    pins = {f"{OID}@1": {"object_id": OID, "object_version": 1, "revision_id": RID, "block": None, "component": None},
            f"event:{EID}": {"event_id": EID}, f"event:{EID2}": {"event_id": EID2}}
    assert models.snapshot_ref_texts(written) == [f"{OID}@1", f"event:{EID}", f"event:{EID2}"]
    stored = models.stored_snapshot(written, pins, generator=PID)
    issues = stored["blocks"]["issues"]["components"]
    assert stored["generator"] == PID and stored["subject_ref"] == pins[f"{OID}@1"]
    assert stored["source_event_refs"] == [{"event_id": EID}] and "component_ledger" not in stored
    assert issues[0]["id"] == "iss-7" and models.COMPONENT_ID.fullmatch(issues[1]["id"])
    assert issues[1]["refs"] == [{"event_id": EID2}]


# ------------------------------------------------------------------ external events
def event(**overrides):
    return {"category": "other", "subject_refs": [f"{OID}@1"], "occurred_at": "2026-09-27T20:00:00+08:00",
            "content": {"text": "天枢每周同步 2026-W39"}, **overrides}


def test_an_external_event_takes_a_past_moment_in_utc_and_object_or_component_subjects():
    params = models.WorldV02RecordEventParams.model_validate(event(
        subject_refs=[f"{OID}@1", f"{OID}@1#issues/iss-7", f"{OID}@1#issues/iss-8"]))
    assert params.occurred_at == "2026-09-27T12:00:00Z" and len(params.subject_refs) == 3


def test_a_correction_references_the_event_it_corrects():
    params = models.WorldV02RecordEventParams.model_validate(event(category="correction", supersedes_event_id=EID))
    assert params.supersedes_event_id == EID


@pytest.mark.parametrize("overrides", [
    {"category": None}, {"category": "rumour"},
    {"category": "correction"},                                   # 更正必须指向原事件
    {"supersedes_event_id": EID},                                 # 只有更正带 supersedes_event_id
    {"subject_refs": []},
    {"subject_refs": [f"event:{EID}"]},                           # 主体是对象或组件
    {"subject_refs": [f"{OID}@1#definition"]},
    {"subject_refs": [f"{OID}@1", f"{OID}@1"]},
    {"occurred_at": "yesterday"},
    {"content": {"text": " "}},
    {"content": {"components": [ISSUE]}},                         # 事件内容不带组件
    {"generator": PID},
])
def test_an_external_event_that_breaks_the_contract_is_refused(overrides):
    with pytest.raises(ValueError):
        models.WorldV02RecordEventParams.model_validate({k: v for k, v in event(**overrides).items() if v is not None})


def test_the_request_models_align_with_the_registry():
    kinds = {item["kind"]: item for item in REGISTRY["event_kinds"]}
    assert list(models.EVENT_CATEGORIES) == [item["id"] for item in REGISTRY["event_attribute_values"]["category"]]
    assert set(models.BACKDATED_KINDS) == {kind for kind, item in kinds.items() if item["backdating"]} \
        == set(REGISTRY["rules"]["ordering"]["backdated_kinds"])
    assert {"world_refresh_state", "world_record_event"} <= set(models.ACTION_PARAMS)
    assert "StateSnapshot" in models.CREATABLE


def test_migration_0039_keeps_every_0_2_event_from_happening_after_it_was_recorded():
    """发生时刻不晚于记录时刻；只有外部事件与状态刷新可以补记，其余种类发生即记录（契约第 11 节）。"""
    sql = MIGRATION.read_text(encoding="utf-8")
    v02 = sql[sql.index("ADD CONSTRAINT ck_gov_world_event_v02"):]
    assert "AND occurred_at <= recorded_at" in v02
    backdated = re.search(r"AND \(kind IN \((.*?)\) OR occurred_at = recorded_at\)", v02, re.S)[1]
    assert set(re.findall(r"'([^']+)'", backdated)) == set(models.BACKDATED_KINDS)


# ------------------------------------------------------------------ read views
def test_an_event_reads_back_its_class_recorder_lateness_relations_and_action():
    from memory_service_runtime.governed import world_v02_readers as readers
    row = {"event_id": EID, "scope_id": OID, "kind": "event.recorded", "category": "correction", "outcome": None,
           "disposition": None, "subject_refs": [{"object_id": OID, "object_version": 1, "revision_id": RID,
                                                  "block": "issues", "component": "iss-7"}],
           "principal_id": PID, "principal_type": "agent", "principal_name": "天枢",
           "on_behalf_of": None, "on_behalf_of_name": None, "external_record_id": None, "external_confirmed_at": None,
           "occurred_at": "2026-09-27T12:00:00+00:00", "recorded_at": "2026-09-28T01:00:00.25+00:00", "late": True,
           "content": {"text": "更正", "components": [], "refs": [{"event_id": EID2}], "artifacts": []}, "detail": None,
           "action": "world_record_event", "action_id": RID, "supersedes_event_id": EID2}
    view = readers.event_view(row, corrected_by=["c"], withdrawn_by=[])
    assert view == {
        "event_id": EID, "scope_id": OID, "kind": "event.recorded", "class": "record", "category": "correction",
        "outcome": None, "disposition": None,
        "subject_refs": [{**row["subject_refs"][0], "ref": f"{OID}@1#issues/iss-7"}],
        "principal": {"principal_id": PID, "principal_type": "agent", "display_name": "天枢"},
        "on_behalf_of": None, "external_confirmation": None,
        "occurred_at": "2026-09-27T12:00:00Z", "recorded_at": "2026-09-28T01:00:00.250000Z", "late": True,
        "content": {"text": "更正", "components": [], "refs": [{"event_id": EID2, "ref": f"event:{EID2}"}],
                    "artifacts": []},
        "detail": None, "action": "world_record_event", "action_id": RID, "supersedes_event_id": EID2,
        "corrected_by": ["c"], "withdrawn_by": []}


def test_a_snapshot_view_gives_the_shell_payload_blocks_and_marks_it_unconfirmed():
    from memory_service_runtime.governed import world_v02_readers as readers
    written = models.validate_snapshot(snapshot("unit_state", period="2026-09", blocks={"issues": {"components": [ISSUE]}}))
    pins = {f"{OID}@1": {"object_id": OID, "object_version": 1, "revision_id": RID, "block": None, "component": None},
            f"event:{EID}": {"event_id": EID}}
    stored = models.stored_snapshot(written, pins, generator=PID)
    view = readers.snapshot_view({"object_id": "s", "domain_id": "d"},
                                 {"revision_id": RID, "object_version": 1, "payload": stored},
                                 generator={"principal_id": PID, "principal_type": "agent", "display_name": "Co-Agent"})
    assert view["category"] == {"id": "time_record", "display_name": "时间记录"} and view["unconfirmed"] is True
    assert view["subject_ref"] == {**pins[f"{OID}@1"], "ref": f"{OID}@1"} and view["as_of"] == "2026-09-27T15:59:59Z"
    assert view["payload_type"] == {"id": "unit_state", "display_name": "单元状态"} and view["period"] == "2026-09"
    assert view["source_event_refs"] == [{"event_id": EID, "ref": f"event:{EID}"}]
    assert view["generator"]["principal_id"] == PID and view["ref"] == "s@1" and view["title"] == "第 40 周"
    blocks = {block["id"]: block for block in view["blocks"]}
    assert list(blocks) == ["current_state", "progress", "key_risks", "issues", "materials"]
    assert blocks["progress"]["text"] == "当前没有进展" and blocks["key_risks"]["text"] == "当前没有关键风险"
    assert blocks["issues"]["components"][0]["ref"] == "s@1#issues/iss-7"
    # 快照的块都可以缺省：登记后加的块在此前存的载荷里没有键，也读作空块，给标准句（#79，映射表第 4 节）。
    older = {**stored, "blocks": {key: value for key, value in stored["blocks"].items() if key != "key_risks"}}
    view = readers.snapshot_view({"object_id": "s", "domain_id": "d"},
                                 {"revision_id": RID, "object_version": 1, "payload": older}, generator=view["generator"])
    assert [(block["id"], block["empty"], block["text"]) for block in view["blocks"] if block["id"] == "key_risks"] == [
        ("key_risks", True, "当前没有关键风险")]
