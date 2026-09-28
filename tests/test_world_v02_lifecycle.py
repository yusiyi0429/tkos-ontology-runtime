"""tkos.world/0.2 的生命周期推导与门准入（契约第 10 至 13 节）：纯函数，按登记的状态表运行。

期望值逐格抄自契约第 10、13 节的状态表（不从登记推），登记只用来枚举输入空间：每个列出的格进入正确状态、
推出事件正确；未列出的（状态，动作）一律拒绝。撤回、自环、一轮重走各有用例。登记直接读 JSON 文件。
"""
from __future__ import annotations

from itertools import count
import json
from pathlib import Path

import pytest

from memory_service_runtime.governed.world_v02_lifecycle import Refused, admit, derive

REGISTRY = json.loads((Path(__file__).resolve().parents[1] / "docs" / "contracts" / "world-registry-0.2.json")
                      .read_text(encoding="utf-8"))
_ids = count(1)


def ev(action, outcome=None, *, disposition=None, supersedes=None, candidate=False, guards=None, by=()):
    return {"event_id": f"e{next(_ids)}", "action": action, "outcome": outcome, "disposition": disposition,
            "supersedes_event_id": supersedes, "candidate": candidate, "guards": dict(guards or {}),
            "recorders": set(by)}


def created():
    return ev("world_create_object")


def withdraw(event, by=()):
    return ev(event["action"], "withdrawn", supersedes=event["event_id"], by=by)


# ------------------------------------------------------------------ 契约的状态表（逐格）

OPEN_MISSION = ["draft", "committed", "established", "in_progress", "delivered", "adjusting"]
OPEN_EXECUTION = ["unassigned", "assigned", "in_progress", "delivered", "adjusting"]
EXECUTION = [
    ("unassigned", "world_assign", None, None, "assigned", "parent", None),
    *[(s, "world_assign", None, None, s, "parent", None) for s in ["assigned", "in_progress", "delivered", "adjusting"]],
    ("assigned", "world_start", None, None, "in_progress", "self", None),
    ("in_progress", "world_deliver", None, None, "delivered", "self", None),
    ("adjusting", "world_deliver", None, None, "delivered", "self", None),
    ("delivered", "world_accept", None, None, "closed", "parent", None),
    ("delivered", "world_reject", None, None, "adjusting", "parent", None),
    ("closed", "world_reopen", None, None, "in_progress", "parent", None),
    *[(s, "world_cancel", None, None, "cancelled", "parent", None) for s in OPEN_EXECUTION],
]
# (起始状态, 动作, 结果, 处置, 进入状态, 谁记, 守卫)
TABLES = {
    "Strategy": [
        ("draft", "world_assign_strategy_round", None, None, "draft", "self", None),
        ("effective", "world_assign_strategy_round", None, None, "effective", "self", None),
        ("draft", "world_agree_strategy", None, None, "draft", "designated", "round_incomplete"),
        ("draft", "world_agree_strategy", None, None, "agreed", "designated", "round_complete"),
        ("agreed", "world_confirm_strategy", "accepted", None, "effective", "gate_role", None),
        ("agreed", "world_confirm_strategy", "returned", None, "draft", "gate_role", None),
        ("effective", "world_reconfirm_strategy", None, None, "effective", "gate_role", None),
    ],
    "LongTermGoal": [
        ("draft", "world_confirm_long_term_goal", "accepted", None, "confirmed", "gate_role", None),
        ("draft", "world_confirm_long_term_goal", "returned", None, "draft", "gate_role", None),
        ("confirmed", "world_reconfirm_long_term_goal", None, None, "confirmed", "gate_role", None),
        ("draft", "world_cancel", None, None, "terminated", "self", None),
        ("confirmed", "world_cancel", None, None, "terminated", "self", None),
    ],
    "PeriodGoal": [
        ("draft", "world_commit_period_goal", None, None, "committed", "gate_role", "formation_anchors"),
        ("committed", "world_confirm_period_goal", "accepted", None, "confirmed", "gate_role", "formation_anchors"),
        ("committed", "world_confirm_period_goal", "returned", None, "draft", "gate_role", None),
        ("confirmed", "world_reconfirm_period_goal", None, None, "confirmed", "gate_role", None),
        ("confirmed", "world_confirm_review", None, None, "closed", "gate_role", "review_of_subject"),
        *[(s, "world_cancel", None, None, "cancelled", "parent", None) for s in ["draft", "committed", "confirmed"]],
    ],
    "Mission": [
        ("draft", "world_commit_mission", None, None, "committed", "self", "parent_goal_confirmed"),
        ("committed", "world_confirm_mission", "accepted", None, "established", "gate_role", "parent_goal_confirmed"),
        ("committed", "world_confirm_mission", "returned", None, "draft", "gate_role", None),
        ("established", "world_start", None, None, "in_progress", "self_or_agent", None),
        ("in_progress", "world_deliver", None, None, "delivered", "self", None),
        ("adjusting", "world_deliver", None, None, "delivered", "self", None),
        ("delivered", "world_accept", None, None, "closed", "parent", None),
        ("delivered", "world_reject", None, None, "adjusting", "parent", None),
        ("closed", "world_reopen", None, None, "in_progress", "parent", None),
        *[(s, "world_cancel", None, None, "cancelled", "parent", None) for s in OPEN_MISSION],
        *[(s, "world_mark_core_battle", None, None, s, "gate_role", "once") for s in OPEN_MISSION],
    ],
    "Task": EXECUTION,
    "Activity": EXECUTION,
    "Issue": [
        ("not_raised", "world_raise_issue", None, None, "pending_routing", "raiser", None),
        ("forming", "world_raise_issue", None, None, "pending_routing", "raiser", None),
        ("pending_routing", "world_route_issue", None, None, "routed", "router", None),
        ("routed", "world_route_issue", None, None, "routed", "router", None),
        ("routed", "world_own_issue", None, None, "owned", "route_target", None),
        *[("owned", "world_dispose_issue", None, d, "disposed", "owner", None)
          for d in ["no_action_close", "current_layer_action", "roll_forward", "immediate_reopen"]],
        ("owned", "world_dispose_issue", None, "route_escalate", "pending_routing", "owner", None),
        ("owned", "world_dispose_issue", None, "pushback", "forming", "owner", None),
        ("routed", "world_return_issue", None, None, "forming", "router_or_owner", None),
        ("owned", "world_return_issue", None, None, "forming", "router_or_owner", None),
    ],
}

# 从初始段走到每个状态的一条路径：(动作, 结果, 处置, 守卫事实)。已记的事件不重判守卫与记录者，
# 只有 Agreement 要带当时的 round_complete。
COMPLETE = {"round_complete": True}
EXECUTION_PATHS = {
    "unassigned": [],
    "assigned": [("world_assign", None, None, None)],
    "in_progress": [("world_assign", None, None, None), ("world_start", None, None, None)],
    "delivered": [("world_assign", None, None, None), ("world_start", None, None, None),
                  ("world_deliver", None, None, None)],
    "adjusting": [("world_assign", None, None, None), ("world_start", None, None, None),
                  ("world_deliver", None, None, None), ("world_reject", None, None, None)],
    "closed": [("world_assign", None, None, None), ("world_start", None, None, None),
               ("world_deliver", None, None, None), ("world_accept", None, None, None)],
    "cancelled": [("world_cancel", None, None, None)],
}
MISSION_ESTABLISHED = [("world_commit_mission", None, None, None), ("world_confirm_mission", "accepted", None, None)]
PATHS = {
    "Strategy": {
        "draft": [],
        "agreed": [("world_assign_strategy_round", None, None, None), ("world_agree_strategy", None, None, COMPLETE)],
        "effective": [("world_assign_strategy_round", None, None, None), ("world_agree_strategy", None, None, COMPLETE),
                      ("world_confirm_strategy", "accepted", None, None)],
    },
    "LongTermGoal": {
        "draft": [],
        "confirmed": [("world_confirm_long_term_goal", "accepted", None, None)],
        "terminated": [("world_cancel", None, None, None)],
    },
    "PeriodGoal": {
        "draft": [],
        "committed": [("world_commit_period_goal", None, None, None)],
        "confirmed": [("world_commit_period_goal", None, None, None),
                      ("world_confirm_period_goal", "accepted", None, None)],
        "closed": [("world_commit_period_goal", None, None, None),
                   ("world_confirm_period_goal", "accepted", None, None), ("world_confirm_review", None, None, None)],
        "cancelled": [("world_cancel", None, None, None)],
    },
    "Mission": {
        "draft": [],
        "committed": MISSION_ESTABLISHED[:1],
        "established": MISSION_ESTABLISHED,
        "in_progress": MISSION_ESTABLISHED + [("world_start", None, None, None)],
        "delivered": MISSION_ESTABLISHED + [("world_start", None, None, None), ("world_deliver", None, None, None)],
        "adjusting": MISSION_ESTABLISHED + [("world_start", None, None, None), ("world_deliver", None, None, None),
                                            ("world_reject", None, None, None)],
        "closed": MISSION_ESTABLISHED + [("world_start", None, None, None), ("world_deliver", None, None, None),
                                         ("world_accept", None, None, None)],
        "cancelled": [("world_cancel", None, None, None)],
    },
    "Task": EXECUTION_PATHS,
    "Activity": EXECUTION_PATHS,
    "Issue": {
        "not_raised": [],
        "pending_routing": [("world_raise_issue", None, None, None)],
        "routed": [("world_raise_issue", None, None, None), ("world_route_issue", None, None, None)],
        "owned": [("world_raise_issue", None, None, None), ("world_route_issue", None, None, None),
                  ("world_own_issue", None, None, None)],
        "disposed": [("world_raise_issue", None, None, None), ("world_route_issue", None, None, None),
                     ("world_own_issue", None, None, None), ("world_dispose_issue", None, "no_action_close", None)],
        "forming": [("world_raise_issue", None, None, None), ("world_route_issue", None, None, None),
                    ("world_return_issue", None, None, None)],
    },
}


def history(object_type, state):
    events = [] if object_type == "Issue" else [created()]
    events += [ev(action, outcome, disposition=disposition, guards=guards)
               for action, outcome, disposition, guards in PATHS[object_type][state]]
    assert derive(REGISTRY, object_type, events)["status"] == state
    return events


CELLS = [(object_type, *cell) for object_type, cells in TABLES.items() for cell in cells]


@pytest.mark.parametrize("object_type,start,action,outcome,disposition,to,by,guard", CELLS)
def test_every_listed_cell_enters_its_state_with_the_right_producer(object_type, start, action, outcome,
                                                                    disposition, to, by, guard):
    events = history(object_type, start)
    before = derive(REGISTRY, object_type, events)["event_id"]
    event = ev(action, outcome, disposition=disposition, guards={guard: True} if guard else {}, by={by})
    result = admit(REGISTRY, object_type, events, event)
    assert result["status"] == to
    assert result["event_id"] == (before if to == start else event["event_id"])
    after = derive(REGISTRY, object_type, [*events, event])
    assert (after["status"], after["event_id"]) == (result["status"], result["event_id"])


def _unlisted():
    dispositions = [item["id"] for item in REGISTRY["issue"]["dispositions"]]
    for object_type, cells in TABLES.items():
        listed = {(start, action, outcome, disposition) for start, action, outcome, disposition, *_ in cells}
        for start in PATHS[object_type]:
            for spec in REGISTRY["actions"]:
                for outcome in [None, *(item for item in spec["outcomes"] if item != "withdrawn")]:
                    for disposition in [None, *(dispositions if spec["action"] == "world_dispose_issue" else [])]:
                        if (start, spec["action"], outcome, disposition) not in listed:
                            yield object_type, start, spec["action"], outcome, disposition


ALL_FACTS = {item["id"]: True for item in REGISTRY["guards"]}
ALL_RECORDERS = {item["id"] for item in REGISTRY["recorders"]}


@pytest.mark.parametrize("object_type,start,action,outcome,disposition", list(_unlisted()))
def test_every_unlisted_combination_is_refused(object_type, start, action, outcome, disposition):
    events = history(object_type, start)
    event = ev(action, outcome, disposition=disposition, guards=ALL_FACTS, by=ALL_RECORDERS)
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, object_type, events, event)
    assert caught.value.reason == "state"


def test_types_without_a_lifecycle_derive_nothing():
    assert derive(REGISTRY, "Company", [created()]) is None
    assert derive(REGISTRY, "ResponsibilityUnit", [created()]) is None


def test_the_initial_stage_is_produced_by_object_creation():
    first = created()
    assert derive(REGISTRY, "Mission", [first]) == {
        "status": "draft", "display_name": "草稿", "event_id": first["event_id"], "formal_event_id": None,
        "round": None}
    assert derive(REGISTRY, "Issue", [])["event_id"] is None


# ------------------------------------------------------------------ 准入：记录者与守卫


def test_an_activity_delivery_is_accepted_only_by_the_task_responsible():
    events = history("Activity", "delivered")
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "Activity", events, ev("world_accept", by={"self"}))
    assert caught.value.reason == "recorder"
    assert admit(REGISTRY, "Activity", events, ev("world_accept", by={"parent"}))["status"] == "closed"


def test_a_mission_start_may_be_recorded_by_the_owners_agent():
    events = history("Mission", "established")
    assert admit(REGISTRY, "Mission", events, ev("world_start", by={"self_or_agent"}))["status"] == "in_progress"
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "Mission", events, ev("world_start", by={"parent"}))
    assert caught.value.reason == "recorder"


def test_a_guard_that_does_not_hold_refuses_the_event():
    events = history("Mission", "draft")
    for guards in ({}, {"parent_goal_confirmed": False}):
        with pytest.raises(Refused) as caught:
            admit(REGISTRY, "Mission", events, ev("world_commit_mission", guards=guards, by={"self"}))
        assert caught.value.reason == "guard"


def test_a_core_battle_is_marked_once():
    events = history("Mission", "in_progress")
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "Mission", events, ev("world_mark_core_battle", guards={"once": False}, by={"gate_role"}))
    assert caught.value.reason == "guard"


def test_derivation_does_not_rejudge_guards_of_recorded_events():
    # 立项时父周期目标已确认；此后它被取消，Mission 仍停在已成立。
    events = [created(), ev("world_commit_mission", guards={"parent_goal_confirmed": True}),
              ev("world_confirm_mission", "accepted", guards={"parent_goal_confirmed": True})]
    assert derive(REGISTRY, "Mission", events)["status"] == "established"
    bare = [created(), ev("world_commit_mission"), ev("world_confirm_mission", "accepted")]
    assert derive(REGISTRY, "Mission", bare)["status"] == "established"


def test_a_recorded_agreement_is_derived_by_its_round_fact():
    first = created()
    events = [first, ev("world_assign_strategy_round"), ev("world_agree_strategy", guards={"round_complete": False})]
    draft = derive(REGISTRY, "Strategy", events)
    assert (draft["status"], draft["event_id"]) == ("draft", first["event_id"])
    last = ev("world_agree_strategy", guards=COMPLETE)
    agreed = derive(REGISTRY, "Strategy", [*events, last])
    assert (agreed["status"], agreed["event_id"]) == ("agreed", last["event_id"])


# ------------------------------------------------------------------ 自环


def test_a_self_loop_keeps_the_stage_and_its_producer():
    assign = ev("world_assign")
    events = [created(), assign]
    result = admit(REGISTRY, "Task", events, ev("world_assign", by={"parent"}))
    assert (result["status"], result["event_id"]) == ("assigned", assign["event_id"])
    start = ev("world_start")
    marked = admit(REGISTRY, "Mission", [*history("Mission", "established"), start],
                   ev("world_mark_core_battle", guards={"once": True}, by={"gate_role"}))
    assert (marked["status"], marked["event_id"]) == ("in_progress", start["event_id"])


def test_a_reconfirmation_keeps_the_stage_and_its_producer():
    events = history("PeriodGoal", "confirmed")
    result = admit(REGISTRY, "PeriodGoal", events, ev("world_reconfirm_period_goal", by={"gate_role"}))
    assert (result["status"], result["event_id"]) == ("confirmed", events[-1]["event_id"])
    assert not any(result[key] for key in ("makes_formal", "writes_back", "opens_round"))


# ------------------------------------------------------------------ 撤回


def test_withdrawing_the_latest_delivery_returns_to_in_progress():
    events = history("Task", "delivered")
    deliver = events[-1]
    back = withdraw(deliver, by={"self"})
    result = admit(REGISTRY, "Task", events, back)
    assert (result["status"], result["event_id"]) == ("in_progress", back["event_id"])
    assert derive(REGISTRY, "Task", [*events, back])["event_id"] == back["event_id"]


def test_an_earlier_event_cannot_be_withdrawn():
    events = history("Task", "delivered")
    start = events[-2]
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "Task", events, withdraw(start, by={"self"}))
    assert caught.value.reason == "state"


def test_a_withdrawal_cannot_be_withdrawn_and_the_earlier_event_stays_put():
    events = history("Task", "delivered")
    back = withdraw(events[-1], by={"self"})
    events.append(back)
    for target in (back, events[-2]):  # 撤回事件本身；撤回后当前段由撤回事件推出，更早的开始也不能撤
        with pytest.raises(Refused):
            admit(REGISTRY, "Task", events, withdraw(target, by={"self"}))


def test_a_withdrawal_is_recorded_by_the_same_role_as_the_original():
    events = history("Task", "delivered")
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "Task", events, withdraw(events[-1], by={"parent"}))
    assert caught.value.reason == "recorder"


def test_record_events_are_not_withdrawn():
    events = history("Task", "assigned")
    with pytest.raises(Refused):
        admit(REGISTRY, "Task", events, withdraw(events[-1], by={"parent"}))


def test_withdrawing_the_confirmation_that_made_content_formal_takes_it_back():
    events = history("Mission", "established")
    confirm = events[-1]
    result = admit(REGISTRY, "Mission", events, withdraw(confirm, by={"gate_role"}))
    assert result["status"] == "committed"
    assert result["unmakes_formal"] is True
    assert result["formal_event_id"] is None


def test_withdrawing_a_period_goal_review_reopens_the_confirmed_goal():
    events = history("PeriodGoal", "closed")
    result = admit(REGISTRY, "PeriodGoal", events, withdraw(events[-1], by={"gate_role"}))
    assert result["status"] == "confirmed"
    assert result["unmakes_formal"] is False


def test_an_agreement_that_did_not_produce_the_state_cannot_be_withdrawn():
    events = [created(), ev("world_assign_strategy_round")]
    first = ev("world_agree_strategy", guards={"round_complete": False})
    events.append(first)
    with pytest.raises(Refused):
        admit(REGISTRY, "Strategy", events, withdraw(first, by={"designated"}))
    last = ev("world_agree_strategy", guards=COMPLETE)
    events.append(last)
    assert admit(REGISTRY, "Strategy", events, withdraw(last, by={"designated"}))["status"] == "draft"


def test_a_reconfirmation_cannot_be_withdrawn():
    events = history("LongTermGoal", "confirmed")
    events.append(ev("world_reconfirm_long_term_goal"))
    with pytest.raises(Refused):
        admit(REGISTRY, "LongTermGoal", events, withdraw(events[-1], by={"gate_role"}))


def test_withdrawing_a_cancellation_restores_the_previous_state():
    events = history("Mission", "in_progress")
    cancel = ev("world_cancel")
    events.append(cancel)
    assert admit(REGISTRY, "Mission", events, withdraw(cancel, by={"parent"}))["status"] == "in_progress"


# ------------------------------------------------------------------ 正式内容与一轮重走


def test_a_draft_commitment_may_carry_the_candidate_that_confirmation_writes_back():
    commit = ev("world_commit_period_goal", candidate=True)
    events = [created(), commit]
    result = admit(REGISTRY, "PeriodGoal", events, ev("world_confirm_period_goal", "accepted",
                                                      guards={"formation_anchors": True}, by={"gate_role"}))
    assert result["makes_formal"] is True
    assert result["candidate_event_id"] == commit["event_id"]


def test_a_draft_commitment_without_candidate_makes_the_latest_revision_formal():
    events = history("PeriodGoal", "committed")
    confirm = ev("world_confirm_period_goal", "accepted", guards={"formation_anchors": True}, by={"gate_role"})
    result = admit(REGISTRY, "PeriodGoal", events, confirm)
    assert (result["makes_formal"], result["candidate_event_id"]) == (True, None)
    assert result["formal_event_id"] == confirm["event_id"]


def test_a_returned_commitment_drops_its_candidate():
    events = [created(), ev("world_commit_period_goal", candidate=True), ev("world_confirm_period_goal", "returned"),
              ev("world_commit_period_goal")]
    result = admit(REGISTRY, "PeriodGoal", events, ev("world_confirm_period_goal", "accepted",
                                                      guards={"formation_anchors": True}, by={"gate_role"}))
    assert result["candidate_event_id"] is None


def open_period_goal_round():
    events = history("PeriodGoal", "confirmed")
    commit = ev("world_commit_period_goal", candidate=True, guards={"formation_anchors": True}, by={"gate_role"})
    result = admit(REGISTRY, "PeriodGoal", events, commit)
    events.append(commit)
    return events, commit, result


def test_a_period_goal_round_opens_without_changing_the_stage():
    events, commit, result = open_period_goal_round()
    assert result["opens_round"] is True
    assert (result["status"], result["event_id"]) == ("confirmed", events[-2]["event_id"])
    assert result["round"] == {"opened_by_event_id": commit["event_id"], "stage": "committed",
                               "candidate_event_id": commit["event_id"]}


def test_a_round_is_written_back_when_the_confirmer_accepts():
    events, commit, _ = open_period_goal_round()
    result = admit(REGISTRY, "PeriodGoal", events, ev("world_confirm_period_goal", "accepted",
                                                      guards={"formation_anchors": True}, by={"gate_role"}))
    assert (result["writes_back"], result["candidate_event_id"]) == (True, commit["event_id"])
    assert (result["status"], result["round"], result["makes_formal"]) == ("confirmed", None, False)
    assert result["formal_event_id"] == events[-2]["event_id"]


def test_a_returned_round_is_void_and_a_new_one_may_open():
    events, _, _ = open_period_goal_round()
    back = ev("world_confirm_period_goal", "returned", by={"gate_role"})
    result = admit(REGISTRY, "PeriodGoal", events, back)
    assert (result["ends_round"], result["writes_back"], result["round"]) == (True, False, None)
    events.append(back)
    again = ev("world_commit_period_goal", candidate=True, guards={"formation_anchors": True}, by={"gate_role"})
    assert admit(REGISTRY, "PeriodGoal", events, again)["opens_round"] is True


def test_a_round_cannot_open_while_one_is_unfinished():
    events, _, _ = open_period_goal_round()
    again = ev("world_commit_period_goal", candidate=True, guards={"formation_anchors": True}, by={"gate_role"})
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "PeriodGoal", events, again)
    assert caught.value.reason == "state"


def test_the_formal_confirmation_is_kept_once_a_round_has_written_back():
    events, _, _ = open_period_goal_round()
    formal = events[-2]
    confirm = ev("world_confirm_period_goal", "accepted", guards={"formation_anchors": True}, by={"gate_role"})
    assert admit(REGISTRY, "PeriodGoal", events, confirm)["writes_back"] is True
    events.append(confirm)
    assert derive(REGISTRY, "PeriodGoal", events)["event_id"] == formal["event_id"]
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "PeriodGoal", events, withdraw(formal, by={"gate_role"}))
    assert caught.value.reason == "state"


def test_a_long_term_goal_keeps_its_formal_confirmation_after_a_one_step_rewrite():
    formal = ev("world_confirm_long_term_goal", "accepted", by={"gate_role"})
    rewrite = ev("world_confirm_long_term_goal", "accepted", candidate=True, by={"gate_role"})
    events = [created(), formal]
    assert admit(REGISTRY, "LongTermGoal", events, withdraw(formal, by={"gate_role"}))["unmakes_formal"] is True
    events.append(rewrite)
    with pytest.raises(Refused):
        admit(REGISTRY, "LongTermGoal", events, withdraw(formal, by={"gate_role"}))


def test_round_events_cannot_be_withdrawn():
    events, commit, _ = open_period_goal_round()
    with pytest.raises(Refused):
        admit(REGISTRY, "PeriodGoal", events, withdraw(commit, by={"gate_role"}))


def test_a_round_needs_a_candidate():
    events = history("Mission", "in_progress")
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, "Mission", events, ev("world_commit_mission", guards={"parent_goal_confirmed": True},
                                              by={"self"}))
    assert caught.value.reason == "state"


def test_a_mission_round_survives_stage_changes_and_writes_back():
    events = history("Mission", "in_progress")
    commit = ev("world_commit_mission", candidate=True, guards={"parent_goal_confirmed": True}, by={"self"})
    assert admit(REGISTRY, "Mission", events, commit)["opens_round"] is True
    events += [commit, ev("world_deliver")]
    result = admit(REGISTRY, "Mission", events, ev("world_confirm_mission", "accepted",
                                                   guards={"parent_goal_confirmed": True}, by={"gate_role"}))
    assert (result["status"], result["writes_back"], result["candidate_event_id"]) == (
        "delivered", True, commit["event_id"])


def test_a_mission_round_does_not_open_once_closed():
    events = history("Mission", "closed")
    with pytest.raises(Refused):
        admit(REGISTRY, "Mission", events, ev("world_commit_mission", candidate=True,
                                              guards={"parent_goal_confirmed": True}, by={"self"}))


def test_a_long_term_goal_draft_confirmation_may_carry_the_candidate():
    confirm = ev("world_confirm_long_term_goal", "accepted", candidate=True, by={"gate_role"})
    result = admit(REGISTRY, "LongTermGoal", [created()], confirm)
    assert (result["status"], result["makes_formal"], result["candidate_event_id"]) == (
        "confirmed", True, confirm["event_id"])


def test_a_confirmed_long_term_goal_is_rewritten_in_one_step():
    events = history("LongTermGoal", "confirmed")
    confirm = ev("world_confirm_long_term_goal", "accepted", candidate=True, by={"gate_role"})
    result = admit(REGISTRY, "LongTermGoal", events, confirm)
    assert (result["status"], result["event_id"]) == ("confirmed", events[-1]["event_id"])
    assert (result["writes_back"], result["candidate_event_id"], result["round"]) == (True, confirm["event_id"], None)
    for bare in (ev("world_confirm_long_term_goal", "accepted", by={"gate_role"}),
                 ev("world_confirm_long_term_goal", "returned", candidate=True, by={"gate_role"})):
        with pytest.raises(Refused):
            admit(REGISTRY, "LongTermGoal", events, bare)


def agree(complete):
    return ev("world_agree_strategy", guards={"round_complete": complete, "round_incomplete": not complete},
              by={"designated"})


def test_a_strategy_first_confirmation_makes_it_effective():
    events = [created(), ev("world_assign_strategy_round"), ev("world_agree_strategy", guards={"round_complete": False}),
              ev("world_agree_strategy", guards=COMPLETE)]
    result = admit(REGISTRY, "Strategy", events, ev("world_confirm_strategy", "accepted", by={"gate_role"}))
    assert (result["status"], result["makes_formal"], result["candidate_event_id"]) == ("effective", True, None)


def test_a_strategy_round_with_a_candidate_is_written_back_after_agreement():
    events = history("Strategy", "effective")
    assign = ev("world_assign_strategy_round", candidate=True, by={"self"})
    assert admit(REGISTRY, "Strategy", events, assign)["opens_round"] is True
    events.append(assign)
    for complete, stage in ((False, "draft"), (True, "agreed")):
        agreement = agree(complete)
        result = admit(REGISTRY, "Strategy", events, agreement)
        assert (result["status"], result["round"]["stage"]) == ("effective", stage)
        events.append(agreement)
    result = admit(REGISTRY, "Strategy", events, ev("world_confirm_strategy", "accepted", by={"gate_role"}))
    assert (result["writes_back"], result["candidate_event_id"], result["round"]) == (True, assign["event_id"], None)


def test_a_strategy_round_without_candidate_ends_with_a_reconfirmation():
    events = history("Strategy", "effective")
    assign = ev("world_assign_strategy_round", by={"self"})
    assert admit(REGISTRY, "Strategy", events, assign)["opens_round"] is True
    events += [assign, agree(True)]
    with pytest.raises(Refused):
        admit(REGISTRY, "Strategy", events, ev("world_confirm_strategy", "accepted", by={"gate_role"}))
    result = admit(REGISTRY, "Strategy", events, ev("world_reconfirm_strategy", by={"gate_role"}))
    assert (result["status"], result["ends_round"], result["writes_back"], result["round"]) == (
        "effective", True, False, None)


def test_redesignating_an_unfinished_strategy_round_starts_a_new_one():
    events = history("Strategy", "effective")
    events += [ev("world_assign_strategy_round", candidate=True), agree(False)]
    again = ev("world_assign_strategy_round", by={"self"})
    result = admit(REGISTRY, "Strategy", events, again)
    assert result["opens_round"] is True
    assert result["round"] == {"opened_by_event_id": again["event_id"], "stage": "draft", "candidate_event_id": None}
    events += [again, agree(True)]
    with pytest.raises(Refused):  # 已补齐：先由 CEO 确认、退回或再确认
        admit(REGISTRY, "Strategy", events, ev("world_assign_strategy_round", by={"self"}))


def test_withdrawing_the_strategy_confirmation_takes_back_formal_content_and_the_round():
    events = history("Strategy", "effective")
    confirm = events[-1]
    events.append(ev("world_assign_strategy_round", candidate=True))
    result = admit(REGISTRY, "Strategy", events, withdraw(confirm, by={"gate_role"}))
    assert (result["status"], result["unmakes_formal"], result["round"]) == ("agreed", True, None)


# ------------------------------------------------------------------ Issue


def test_an_issue_walks_routing_ownership_and_disposal():
    events = history("Issue", "owned")
    escalate = ev("world_dispose_issue", disposition="route_escalate", by={"owner"})
    assert admit(REGISTRY, "Issue", events, escalate)["status"] == "pending_routing"
    events += [escalate, ev("world_route_issue"), ev("world_own_issue")]
    pushback = ev("world_dispose_issue", disposition="pushback", by={"owner"})
    assert admit(REGISTRY, "Issue", events, pushback)["status"] == "forming"
    events.append(pushback)
    raised = ev("world_raise_issue", by={"raiser"})
    result = admit(REGISTRY, "Issue", events, raised)
    assert (result["status"], result["event_id"]) == ("pending_routing", raised["event_id"])


def test_a_disposed_issue_is_not_raised_again():
    with pytest.raises(Refused):
        admit(REGISTRY, "Issue", history("Issue", "disposed"), ev("world_raise_issue", by={"raiser"}))


# ------------------------------------------------------------------ 纯函数


@pytest.mark.parametrize("module", ["world_v02_lifecycle", "world_v02_registry_check"])
def test_the_engine_modules_import_nothing_but_typing(module):
    import ast

    path = Path(__file__).resolve().parents[1] / "src" / "memory_service_runtime" / "governed" / f"{module}.py"
    imported = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or ".")
    assert imported <= {"__future__", "typing"}


# ------------------------------------------------------------------ 评审补充


def test_withdrawing_a_return_restores_the_committed_candidate():
    commit = ev("world_commit_mission", candidate=True)
    back = ev("world_confirm_mission", "returned")
    events = [created(), commit, back, withdraw(back)]
    assert derive(REGISTRY, "Mission", events)["status"] == "committed"
    result = admit(REGISTRY, "Mission", events, ev("world_confirm_mission", "accepted",
                                                   guards={"parent_goal_confirmed": True}, by={"gate_role"}))
    assert result["candidate_event_id"] == commit["event_id"]


def test_withdrawing_the_formal_confirmation_restores_the_candidate_for_the_next_confirmation():
    commit = ev("world_commit_period_goal", candidate=True)
    confirm = ev("world_confirm_period_goal", "accepted")
    events = [created(), commit, confirm, withdraw(confirm)]
    result = admit(REGISTRY, "PeriodGoal", events, ev("world_confirm_period_goal", "accepted",
                                                      guards={"formation_anchors": True}, by={"gate_role"}))
    assert result["candidate_event_id"] == commit["event_id"]


def test_taking_back_formal_content_also_ends_the_open_round():
    events, _, _ = open_period_goal_round()
    confirm = events[-2]
    result = admit(REGISTRY, "PeriodGoal", events, withdraw(confirm, by={"gate_role"}))
    assert (result["unmakes_formal"], result["ends_round"], result["round"]) == (True, True, None)


@pytest.mark.parametrize("object_type,state,action,outcome,guard,by", [
    ("LongTermGoal", "confirmed", "world_reconfirm_long_term_goal", None, None, "gate_role"),
    ("PeriodGoal", "committed", "world_confirm_period_goal", "accepted", "formation_anchors", "gate_role"),
    ("Strategy", "draft", "world_assign_strategy_round", None, None, "self"),
    ("Task", "delivered", "world_accept", None, None, "parent"),
])
def test_a_candidate_on_an_event_that_does_not_carry_one_is_refused(object_type, state, action, outcome, guard, by):
    event = ev(action, outcome, candidate=True, guards={guard: True} if guard else {}, by={by})
    with pytest.raises(Refused) as caught:
        admit(REGISTRY, object_type, history(object_type, state), event)
    assert caught.value.reason == "state"


def test_a_candidate_on_a_round_confirmation_is_refused():
    events, _, _ = open_period_goal_round()
    with pytest.raises(Refused):
        admit(REGISTRY, "PeriodGoal", events, ev("world_confirm_period_goal", "accepted", candidate=True,
                                                 guards={"formation_anchors": True}, by={"gate_role"}))


def test_admitting_on_a_type_without_a_lifecycle_is_a_key_error():
    with pytest.raises(KeyError):
        admit(REGISTRY, "Company", [created()], ev("world_start", by={"self"}))
