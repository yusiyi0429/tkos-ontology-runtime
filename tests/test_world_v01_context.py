"""tkos.world/0.1 的取上下文（票 #25）：预算裁剪与六问覆盖两个纯函数，不连数据库。

裁剪：每层条数上限先生效，仍超字符预算时先裁最旧的事件，再从主干最远层起裁块与快照；
当前对象的块与最新快照不裁。覆盖：按上下文包里实际留下的内容逐问判定，给出依据或缺口。
"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import world_v01_models as models
from memory_service_runtime.governed.world_v01_context import cover, trim


def item(key, kind, level, text, occurred_at=None, object_id=None):
    return {"key": key, "kind": kind, "level": level, "text": text, "occurred_at": occurred_at,
            "object_id": object_id or f"o{level}"}


def pack():
    """文档顺序：标题、第 0 层（当前对象）到第 2 层，每层表头、块、快照、事件（新的在前）。"""
    return [item("title", "title", -1, "# 上下文"),
            item("h0", "header", 0, "## Activity"), item("b0", "block", 0, "x" * 40), item("s0", "snapshot", 0, "s" * 30),
            item("e0-new", "event", 0, "e" * 20, "2026-09-24T02:00:00.5Z"),  # 同一秒里晚半秒：UTC 文本按字符串比较会排反
            item("e0-old", "event", 0, "e" * 20, "2026-09-24T02:00:00Z"),
            item("h1", "header", 1, "## Task"), item("b1a", "block", 1, "y" * 40), item("b1b", "block", 1, "y" * 40),
            item("s1", "snapshot", 1, "s" * 30), item("e1", "event", 1, "e" * 20, "2026-09-23T02:00:00Z"),
            item("h2", "header", 2, "## Company"), item("b2", "block", 2, "z" * 40)]


def keys(result, field="kept"):
    return [entry["key"] for entry in result[field]]


def test_a_pack_within_budget_keeps_everything_and_counts_the_rendered_markdown():
    result = trim(pack(), max_chars=10_000, max_events_per_object=10)
    markdown = "\n\n".join(entry["text"] for entry in pack())
    assert keys(result) == keys({"kept": pack()}) and result["trimmed"] == []
    assert result["markdown"] == markdown and result["chars"] == len(markdown) and result["over_budget"] is False


def test_the_per_object_event_cap_keeps_the_newest_events_first():
    result = trim(pack(), max_chars=10_000, max_events_per_object=1)
    assert [(entry["key"], entry["reason"]) for entry in result["trimmed"]] == [("e0-old", "over_level_cap")]


@pytest.mark.parametrize("limit, trimmed", [
    (len("\n\n".join(e["text"] for e in pack())) - 1, ["e1"]),                      # 先裁最旧的事件
    (260, ["e1", "e0-old", "e0-new", "b2"]),                                           # 事件裁完才裁最远层的块
    (120, ["e1", "e0-old", "e0-new", "b2", "b1b", "b1a", "s1"]),                       # 由远及近，块在前、快照在后
])
def test_over_budget_trims_old_events_first_then_the_farthest_levels(limit, trimmed):
    result = trim(pack(), max_chars=limit, max_events_per_object=10)
    assert keys(result, "trimmed") == trimmed
    assert all(entry["reason"] == "over_budget" for entry in result["trimmed"])
    assert result["chars"] <= limit and result["over_budget"] is False


def test_the_current_object_and_its_snapshot_stay_even_over_budget():
    result = trim(pack(), max_chars=10, max_events_per_object=10)
    assert keys(result) == ["title", "h0", "b0", "s0", "h1", "h2"] and result["over_budget"] is True


def layer(level, *, blocks=(), state=None, events=(), responsible=(), formal=None):
    return {"level": level, "object": {"ref": f"o{level}@1", "formal": formal, "responsible": list(responsible)},
            "blocks": [{"ref": f"o{level}@1#{block_id}", "id": block_id, "kind": kind, "empty": empty}
                       for block_id, kind, empty in blocks],
            "state": state and {"ref": state}, "events": [{"event_id": event_id} for event_id in events]}


def test_each_question_is_answered_from_what_the_pack_holds():
    layers = [layer(0, blocks=[("instruction", "definition", False), ("constraint", "constraint", True)],
                    state="s0@1", events=["e1"], responsible=["p1"]),
              layer(1, blocks=[("definition", "definition", False), ("acceptance", "definition", False)]),
              layer(2, blocks=[("outcome", "definition", False)], formal=True)]
    coverage = cover(layers)
    assert {name: answer["evidence"] for name, answer in coverage.items()} == {
        "why": [{"ref": "o1@1#definition"}, {"ref": "o1@1#acceptance"}, {"ref": "o2@1#outcome"}],
        "what": [{"ref": "o0@1#instruction"}],
        "who": [{"ref": "o0@1"}],
        "now": [{"ref": "s0@1"}],
        "happened": [{"event_id": "e1"}],
        "basis": [{"ref": "o2@1#outcome"}, {"ref": "o1@1#acceptance"}]}
    assert all(answer["answered"] and answer["gap"] is None for answer in coverage.values())
    assert [answer["question"] for answer in coverage.values()] == ["为什么", "做什么", "谁负责", "现在怎样", "发生了什么",
                                                                     "凭什么"]


def test_a_question_the_pack_cannot_answer_is_a_gap_with_its_reason():
    coverage = cover([layer(0, blocks=[("instruction", "definition", True)]), layer(1, formal=False)])
    assert all(not answer["answered"] and answer["evidence"] == [] and answer["gap"] for answer in coverage.values())


@pytest.mark.parametrize("body", [
    {"question": "为什么要做这件事？", "budget": {"max_chars": 2000, "max_events_per_object": 3}, "recent_days": 7},
    {"question": "为什么要做这件事？"},
])
def test_a_context_request_names_a_question_and_optionally_its_budget(body):
    assert models.WorldContextRequest.model_validate(body).model_dump(mode="json", exclude_none=True) == body


@pytest.mark.parametrize("body", [
    {}, {"question": "  "}, {"question": "x", "budget": {"max_chars": 0}}, {"question": "x", "recent_days": 0},
    {"question": "x", "recent_days": 3_000_000}, {"question": "x" * 2001},
    {"question": "x", "budget": {"max_tokens": 10}}, {"question": "x", "scope": "all"},
])
def test_a_context_request_outside_its_shape_is_refused(body):
    with pytest.raises(ValueError):
        models.WorldContextRequest.model_validate(body)


def test_relation_lines_are_trimmed_with_their_level_and_the_current_objects_last():
    items = [item("title", "title", -1, "# 上下文"), item("h0", "header", 0, "## A"), item("r0", "relations", 0, "r" * 50),
             item("b0", "block", 0, "x" * 10), item("h1", "header", 1, "## T"), item("r1", "relations", 1, "r" * 50),
             item("b1", "block", 1, "y" * 10)]
    assert keys(trim(items, max_chars=10, max_events_per_object=10), "trimmed") == ["r1", "b1", "r0"]
    assert keys(trim(items, max_chars=100, max_events_per_object=10), "trimmed") == ["r1"]


def test_formal_upper_content_counts_as_basis_only_while_the_pack_still_holds_it():
    trimmed_away = [layer(0, blocks=[("instruction", "definition", False)]), layer(1, formal=True)]
    assert cover(trimmed_away)["basis"]["answered"] is False
    held = [layer(0), layer(1, blocks=[("outcome", "definition", False)], formal=True)]
    assert cover(held)["basis"]["evidence"] == [{"ref": "o1@1#outcome"}]
