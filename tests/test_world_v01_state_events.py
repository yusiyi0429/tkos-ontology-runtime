"""tkos.world/0.1 的状态快照写入与外部事件参数（票 #22），不连数据库。"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import world_v01_models as models

M = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e06"
T = "0b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e07"
E = "3b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"

MEETING = {"category": "meeting", "subject_refs": [f"{M}@1", f"{T}@2#plan"],
           "occurred_at": "2026-09-22T17:37:00+08:00",
           "content": {"text": "9/22 17:37 会议：确认数据环境先行。", "artifacts": ["https://example.test/minutes"]}}


def test_an_external_event_names_its_category_subjects_time_and_content():
    params = models.WorldRecordEventParams.model_validate(MEETING)
    assert params.model_dump(mode="json", exclude_none=True) == {
        "category": "meeting", "subject_refs": [f"{M}@1", f"{T}@2#plan"],
        "occurred_at": "2026-09-22T09:37:00Z",
        "content": {"text": "9/22 17:37 会议：确认数据环境先行。", "refs": [],
                    "artifacts": ["https://example.test/minutes"]}}


def test_a_correction_references_the_event_it_corrects():
    params = models.WorldRecordEventParams.model_validate(
        {**MEETING, "category": "correction", "supersedes_event_id": E})
    assert params.supersedes_event_id == E


@pytest.mark.parametrize("change", [
    {"subject_refs": []},                                      # 至少一条主体
    {"subject_refs": None},
    {"subject_refs": [f"{M}@1", f"{M}@1"]},                    # 同一对象只作一次主体
    {"subject_refs": [f"{M}@1", f"{M}@2#play"]},
    {"category": "chat"},                                      # category 取值
    {"category": None},
    {"category": "correction"},                                # 更正必须引用原事件
    {"supersedes_event_id": E},                                # 只有更正引用原事件
    {"occurred_at": "2026-09-22T17:37:00"},                    # 没有时区的时刻有歧义
    {"content": {"text": "  "}},                               # 空内容不能冒充有内容
    {"content": None},
    {"note": "x"},
])
def test_an_external_event_outside_its_shape_is_refused(change):
    params = {key: value for key, value in {**MEETING, **change}.items() if value is not None}
    with pytest.raises(ValueError):
        models.WorldRecordEventParams.model_validate(params)


def test_event_categories_and_the_correction_rule_are_the_registrys():
    import json
    from pathlib import Path
    from typing import get_args
    registry = json.loads((Path(__file__).resolve().parents[1] / "docs/contracts/world-registry-0.1.json").read_text())
    categories = [value["id"] for value in registry["event_attribute_values"]["category"]]
    assert list(get_args(models.WorldRecordEventParams.model_fields["category"].annotation)) == categories
    for category in categories:
        params = {**MEETING, "category": category}
        if category in registry["supersedes_required_when"]["category"]:
            params["supersedes_event_id"] = E
        assert models.WorldRecordEventParams.model_validate(params).category == category


def test_a_snapshot_write_carries_a_payload_and_an_optional_declaration():
    declaration = {"scene": f"{M}@1", "trigger": "会后整理",
                   "human_acceptance": {"required": False}}
    params = models.WorldRefreshStateParams.model_validate({"payload": {"title": "x"}, "declaration": declaration})
    assert params.model_dump(mode="json", exclude_none=True) == {"payload": {"title": "x"}, "declaration": declaration}
    with pytest.raises(ValueError):
        models.WorldRefreshStateParams.model_validate({"payload": {"title": "x"}, "domain_id": M})
