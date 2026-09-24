"""tkos.world/0.1 的生命周期推导（契约第 10 节、ADR-0002）：事件列表 → 所处的段与推出它的事件 id，纯函数。

每个用例直接以事件列表为输入，期望值逐条取自契约第 10 节的状态机。
"""
from __future__ import annotations

from itertools import count

import pytest

from memory_service_runtime.governed.world_v01_lifecycle import Refused, admit, derive

_ids = count(1)


def ev(kind, action=None, *, phase=None, category=None, outcome=None, supersedes=None, parent_responsible=None):
    event = {"event_id": f"e{next(_ids)}", "kind": kind, "action": action, "phase": phase, "category": category,
             "outcome": outcome, "supersedes_event_id": supersedes}
    if parent_responsible is not None:
        event["by_spine_parent_responsible"] = parent_responsible
    return event


def created():
    return ev("object.created", "world_create_object")


def commit(action, phase=None, supersedes=None):
    return ev("commit", action, phase=phase, outcome="withdrawn" if supersedes else None, supersedes=supersedes)


def confirm(action, outcome, phase=None, supersedes=None):
    return ev("confirm", action, phase=phase, outcome=outcome, supersedes=supersedes)


def withdraw(event):
    return ev(event["kind"], event["action"], phase=event["phase"], outcome="withdrawn",
              supersedes=event["event_id"])


def refreshed():
    return ev("state.refreshed", "world_refresh_state")


def recorded(category, parent_responsible=None):
    return ev("event.recorded", "world_record_event", category=category, parent_responsible=parent_responsible)


def assigned():
    return ev("assign", "world_assign")


def marked():
    return ev("core_battle.marked", "world_mark_core_battle")


def status(object_type, events):
    result = derive(object_type, events)
    return result["status"], result["event_id"]


@pytest.mark.parametrize("object_type", ["Company", "Strategy", "ResponsibilityUnit", "StateSnapshot"])
def test_types_without_a_lifecycle_only_have_versions(object_type):
    assert derive(object_type, [created()]) is None


# ------------------------------------------------------------ LongTermGoal
LTG = "world_confirm_long_term_goal"


def test_a_long_term_goal_starts_as_a_draft_and_is_confirmed_by_the_ceo():
    c = created()
    assert status("LongTermGoal", [c]) == ("draft", c["event_id"])
    ok = confirm(LTG, "accepted")
    assert status("LongTermGoal", [c, ok]) == ("confirmed", ok["event_id"])


def test_a_returned_long_term_goal_stays_a_draft_and_the_return_is_only_a_record():
    c, back = created(), confirm(LTG, "returned")
    assert status("LongTermGoal", [c, back]) == ("draft", c["event_id"])


def test_withdrawing_the_confirmation_of_a_long_term_goal_returns_it_to_draft():
    c, ok = created(), confirm(LTG, "accepted")
    back = withdraw(ok)
    assert status("LongTermGoal", [c, ok, back]) == ("draft", back["event_id"])


def test_regating_a_confirmed_long_term_goal_does_not_change_its_stage():
    c, ok = created(), confirm(LTG, "accepted")
    assert status("LongTermGoal", [c, ok, confirm(LTG, "returned"), confirm(LTG, "accepted")]) == (
        "confirmed", ok["event_id"])


# ------------------------------------------------------------ PeriodGoal
PG_COMMIT, PG_CONFIRM = "world_commit_period_goal", "world_confirm_period_goal"


def test_a_period_goal_is_committed_by_the_dri_then_confirmed_by_the_ceo():
    c, promise, ok = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")
    assert status("PeriodGoal", [c, promise]) == ("committed", promise["event_id"])
    assert status("PeriodGoal", [c, promise, ok]) == ("confirmed", ok["event_id"])


def test_a_committed_period_goal_that_is_returned_goes_back_to_draft():
    c, promise, back = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "returned")
    assert status("PeriodGoal", [c, promise, back]) == ("draft", back["event_id"])


def test_an_unconfirmed_draft_cannot_be_confirmed():
    c = created()
    assert status("PeriodGoal", [c, confirm(PG_CONFIRM, "accepted")]) == ("draft", c["event_id"])


@pytest.mark.parametrize("undo, expected", [("commit", "draft"), ("confirm", "committed"), ("return", "committed")])
def test_withdrawing_the_event_that_produced_the_stage_reverts_to_the_stage_before_it(undo, expected):
    c, promise = created(), commit(PG_COMMIT)
    events = [c, promise]
    if undo == "confirm":
        events.append(confirm(PG_CONFIRM, "accepted"))
    if undo == "return":
        events.append(confirm(PG_CONFIRM, "returned"))
    events.append(withdraw(events[-1]))
    assert status("PeriodGoal", events) == (expected, events[-1]["event_id"])


def test_only_the_event_that_produced_the_current_stage_can_be_withdrawn():
    c, promise, ok = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")
    assert status("PeriodGoal", [c, promise, ok, withdraw(promise)]) == ("confirmed", ok["event_id"])


def test_once_a_later_event_changed_the_lifecycle_an_earlier_one_cannot_be_withdrawn_and_nor_can_a_withdrawal():
    c, promise, ok = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")
    back = withdraw(ok)
    assert status("PeriodGoal", [c, promise, ok, back, withdraw(promise)]) == ("committed", back["event_id"])
    assert status("PeriodGoal", [c, promise, ok, back, withdraw(back)]) == ("committed", back["event_id"])


def test_regating_a_confirmed_period_goal_does_not_change_its_stage():
    c, promise, ok = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")
    again = [commit(PG_COMMIT), confirm(PG_CONFIRM, "returned"), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")]
    assert status("PeriodGoal", [c, promise, ok, *again]) == ("confirmed", ok["event_id"])


# ------------------------------------------------------------ Mission
M_COMMIT, M_CONFIRM, M_CEO = "world_commit_mission", "world_confirm_mission", "world_confirm_mission_core_battle"


def established_mission():
    c, promise, ok = created(), commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "accepted", "initiation")
    return [c, promise, ok]


def test_a_mission_is_committed_by_its_owner_and_established_by_the_dri():
    c, promise, ok = established_mission()
    assert status("Mission", [c, promise]) == ("committed", promise["event_id"])
    assert status("Mission", [c, promise, ok]) == ("established", ok["event_id"])


def test_a_committed_mission_that_is_returned_goes_back_to_draft():
    c, promise, back = created(), commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "returned", "initiation")
    assert status("Mission", [c, promise, back]) == ("draft", back["event_id"])


@pytest.mark.parametrize("marked_in", ["draft", "committed"])
def test_marking_a_core_battle_before_establishment_keeps_the_stage_and_routes_confirmation_to_the_ceo(marked_in):
    c, promise, mark = created(), commit(M_COMMIT, "initiation"), marked()
    events = [c, mark, promise] if marked_in == "draft" else [c, promise, mark]
    assert status("Mission", events[:2]) == (("draft", c["event_id"]) if marked_in == "draft"
                                            else ("committed", promise["event_id"]))
    ok = confirm(M_CONFIRM, "accepted", "initiation")
    assert status("Mission", [*events, ok]) == ("awaiting_ceo", ok["event_id"])


def test_marking_an_established_mission_sends_it_to_the_ceo():
    events = established_mission()
    mark = marked()
    assert status("Mission", [*events, mark]) == ("awaiting_ceo", mark["event_id"])


@pytest.mark.parametrize("outcome, expected", [("accepted", "established"), ("returned", "draft")])
def test_the_ceo_confirms_or_returns_a_core_battle_initiation(outcome, expected):
    c, mark, promise, ok = created(), marked(), commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "accepted", "initiation")
    ceo = confirm(M_CEO, outcome, "initiation")
    assert status("Mission", [c, mark, promise, ok, ceo]) == (expected, ceo["event_id"])


def test_a_core_battle_cannot_be_marked_once_the_mission_is_in_progress():
    events = [*established_mission(), refreshed()]
    assert status("Mission", [*events, marked()]) == ("in_progress", events[-1]["event_id"])


def test_the_first_snapshot_after_establishment_puts_the_mission_in_progress_and_earlier_ones_do_not_count():
    c, promise, ok = established_mission()
    early, first, later = refreshed(), refreshed(), refreshed()
    assert status("Mission", [c, early, promise, ok]) == ("established", ok["event_id"])
    assert status("Mission", [c, early, promise, ok, first, later]) == ("in_progress", first["event_id"])


def test_delivery_is_committed_by_the_owner_and_closed_by_the_dri():
    events = [*established_mission(), refreshed()]
    done, ok = commit(M_COMMIT, "delivery"), confirm(M_CONFIRM, "accepted", "delivery")
    assert status("Mission", [*events, done]) == ("delivered", done["event_id"])
    assert status("Mission", [*events, done, ok]) == ("closed", ok["event_id"])


def test_a_returned_delivery_is_adjusted_and_resumes_by_a_snapshot_or_a_new_delivery_commit():
    events = [*established_mission(), refreshed(), commit(M_COMMIT, "delivery")]
    back = confirm(M_CONFIRM, "returned", "delivery")
    assert status("Mission", [*events, back]) == ("adjusting", back["event_id"])
    resume, again = refreshed(), commit(M_COMMIT, "delivery")
    assert status("Mission", [*events, back, resume]) == ("in_progress", resume["event_id"])
    assert status("Mission", [*events, back, again]) == ("delivered", again["event_id"])


def test_an_initiation_confirmation_cannot_be_withdrawn_once_a_snapshot_moved_the_mission_on():
    c, promise, ok = established_mission()
    first, back = refreshed(), withdraw(ok)
    assert status("Mission", [c, promise, ok, first, withdraw(ok)]) == ("in_progress", first["event_id"])
    assert status("Mission", [c, promise, ok, back]) == ("committed", back["event_id"])


def _mission_path(to):
    """推到某一段的事件列表，最后一条是推出该段的门事件。"""
    c, mark = created(), marked()
    initiation = [c, commit(M_COMMIT, "initiation")]
    core = [c, mark, initiation[1], confirm(M_CONFIRM, "accepted", "initiation")]
    running = [*established_mission(), refreshed()]
    delivered = [*running, commit(M_COMMIT, "delivery")]
    adjusting = [*delivered, confirm(M_CONFIRM, "returned", "delivery")]
    return {"committed": initiation,
            "returned_to_draft": [*initiation, confirm(M_CONFIRM, "returned", "initiation")],
            "awaiting_ceo": core,
            "ceo_established": [*core, confirm(M_CEO, "accepted", "initiation")],
            "ceo_returned": [*core, confirm(M_CEO, "returned", "initiation")],
            "delivered": delivered,
            "closed": [*delivered, confirm(M_CONFIRM, "accepted", "delivery")],
            "adjusting": adjusting,
            "redelivered": [*adjusting, commit(M_COMMIT, "delivery")]}[to]


@pytest.mark.parametrize("path, before", [
    ("committed", "draft"), ("returned_to_draft", "committed"), ("awaiting_ceo", "committed"),
    ("ceo_established", "awaiting_ceo"), ("ceo_returned", "awaiting_ceo"), ("delivered", "in_progress"),
    ("closed", "delivered"), ("adjusting", "delivered"), ("redelivered", "adjusting"),
])
def test_each_mission_gate_event_that_produced_the_stage_can_be_withdrawn(path, before):
    events = _mission_path(path)
    back = withdraw(events[-1])
    assert status("Mission", [*events, back]) == (before, back["event_id"])


def test_a_withdrawal_must_match_the_phase_of_the_event_it_withdraws():
    events = _mission_path("closed")
    wrong = confirm(M_CONFIRM, "withdrawn", "initiation", supersedes=events[-1]["event_id"])
    assert status("Mission", [*events, wrong]) == ("closed", events[-1]["event_id"])


def test_regating_an_in_progress_mission_to_change_its_play_keeps_it_in_progress():
    events = [*established_mission(), refreshed()]
    again = [commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "accepted", "initiation")]
    assert status("Mission", [*events, *again]) == ("in_progress", events[-1]["event_id"])


def test_a_withdrawal_must_be_the_same_kind_of_gate_action():
    c, promise = created(), commit(M_COMMIT, "initiation")
    wrong = confirm(M_CONFIRM, "withdrawn", "initiation", supersedes=promise["event_id"])
    assert status("Mission", [c, promise, wrong]) == ("committed", promise["event_id"])


# ------------------------------------------------------------ Task and Activity
@pytest.mark.parametrize("object_type", ["Task", "Activity"])
def test_a_task_or_activity_moves_from_assignment_to_closure(object_type):
    c, early, give, run, done = created(), refreshed(), assigned(), refreshed(), recorded("delivery")
    assert status(object_type, [c]) == ("unassigned", c["event_id"])
    assert status(object_type, [c, early, give]) == ("assigned", give["event_id"])
    assert status(object_type, [c, early, give, run]) == ("in_progress", run["event_id"])
    assert status(object_type, [c, early, give, run, done]) == ("delivered", done["event_id"])
    accepted = recorded("acceptance", parent_responsible=True)
    assert status(object_type, [c, early, give, run, done, accepted]) == ("closed", accepted["event_id"])


@pytest.mark.parametrize("object_type", ["Task", "Activity"])
def test_only_the_responsible_one_level_up_closes_it_by_acceptance(object_type):
    events = [created(), assigned(), refreshed(), recorded("delivery")]
    assert status(object_type, [*events, recorded("acceptance", parent_responsible=False)]) == (
        "delivered", events[-1]["event_id"])


@pytest.mark.parametrize("object_type", ["Task", "Activity"])
def test_reassigning_or_other_events_do_not_move_the_stage(object_type):
    events = [created(), assigned(), refreshed()]
    noise = [assigned(), recorded("meeting"), ev("object.revised", "world_revise_object"), recorded("acceptance", True)]
    assert status(object_type, [*events, *noise]) == ("in_progress", events[-1]["event_id"])


def test_the_result_names_the_stage_in_the_registry_language():
    assert derive("Mission", established_mission())["display_name"] == "已成立"


# ------------------------------------------------------------ assign command
def test_an_assign_command_names_the_assignee_and_targets_only_types_with_a_responsible():
    from memory_service_runtime.governed import world_v01_models as models
    assignee = "2b5a7e2c-7d4f-4c1e-9a55-3a4f1c2d9e01"
    params = models.WorldAssignParams.model_validate({"principal_id": assignee})
    assert params.model_dump(mode="json", exclude_none=True) == {"principal_id": assignee}
    assert models.ACTION_TARGETS["world_assign"] == {"ResponsibilityUnit", "Mission", "Task", "Activity"}
    for bad in ({}, {"principal_id": "someone"}, {"principal_id": assignee, "effective_at": "2026-09-24T00:00:00Z"}):
        with pytest.raises(ValueError):
            models.WorldAssignParams.model_validate(bad)


# ------------------------------------------------------------ gate admission (ticket #24)
# admit 回答一条门事件现在能不能记（契约第 10 节：未列出的组合被拒，重走除外），以及记了之后对
# 正式内容指针做什么（第 11 节）：makes_formal 让对象第一次有正式内容，unmakes_formal 随撤回或回到草稿
# 收回，opens_round 是一轮重走的承诺（带候选），writes_back 是写回候选的确认，candidate_commit 指出
# 写回的候选随哪条承诺提交（长期目标没有承诺门，候选随确认本身提交，为 None）。
NO_EFFECT = {"makes_formal": False, "unmakes_formal": False, "opens_round": False, "writes_back": False,
             "candidate_commit": None}


def effect(object_type, events, event, **changes):
    return {**NO_EFFECT, **changes, "status": status(object_type, [*events, event])[0]}


def admitted(object_type, events, event):
    result = admit(object_type, events, event)
    return {key: result[key] for key in (*NO_EFFECT, "status")}


def refused(object_type, events, event, says):
    with pytest.raises(Refused, match=says):
        admit(object_type, events, event)


def test_the_gate_that_first_confirms_an_object_makes_its_content_formal():
    c, promise = created(), commit(PG_COMMIT)
    assert admitted("PeriodGoal", [c], promise) == effect("PeriodGoal", [c], promise)
    ok = confirm(PG_CONFIRM, "accepted")
    assert admitted("PeriodGoal", [c, promise], ok) == effect("PeriodGoal", [c, promise], ok, makes_formal=True)
    ltg = confirm(LTG, "accepted")
    assert admitted("LongTermGoal", [c], ltg) == effect("LongTermGoal", [c], ltg, makes_formal=True)
    back = confirm(LTG, "returned")
    assert admitted("LongTermGoal", [c], back) == effect("LongTermGoal", [c], back)


def test_a_core_battle_becomes_formal_only_when_the_ceo_accepts_its_initiation():
    c, mark, promise = created(), marked(), commit(M_COMMIT, "initiation")
    dri = confirm(M_CONFIRM, "accepted", "initiation")
    assert admitted("Mission", [c, mark, promise], dri) == effect("Mission", [c, mark, promise], dri)
    ceo = confirm(M_CEO, "accepted", "initiation")
    assert admitted("Mission", [c, mark, promise, dri], ceo) == effect(
        "Mission", [c, mark, promise, dri], ceo, makes_formal=True)
    events = established_mission()
    late_mark = marked()
    assert admitted("Mission", events, late_mark)["status"] == "awaiting_ceo"
    assert admitted("Mission", [*events, late_mark], ceo) == effect("Mission", [*events, late_mark], ceo)


@pytest.mark.parametrize("object_type, events, event, says", [
    ("PeriodGoal", [created()], confirm(PG_CONFIRM, "accepted"), "not allowed at this stage"),       # 未承诺不能确认
    ("Mission", [created()], commit(M_COMMIT, "delivery"), "not allowed at this stage"),
    ("Mission", [created(), commit(M_COMMIT, "initiation")], confirm(M_CEO, "accepted", "initiation"),
     "not allowed at this stage"),                                                                  # 未标核心战役
    ("Mission", [*established_mission(), refreshed()], marked(), "not allowed at this stage"),
    ("Mission", [created(), marked()], marked(), "already a core battle"),
    ("Mission", _mission_path("awaiting_ceo"), commit(M_COMMIT, "initiation"), "not allowed at this stage"),
])
def test_a_gate_event_the_state_machine_does_not_list_is_refused(object_type, events, event, says):
    refused(object_type, events, event, says)


def test_withdrawing_the_confirmation_that_made_the_content_formal_takes_it_back():
    events = established_mission()
    back = withdraw(events[-1])
    assert admitted("Mission", events, back) == effect("Mission", events, back, unmakes_formal=True)
    marked_later = [*events, marked(), confirm(M_CEO, "accepted", "initiation")]
    back = withdraw(marked_later[-1])
    assert admitted("Mission", marked_later, back) == effect("Mission", marked_later, back)
    promise = commit(M_COMMIT, "initiation")
    assert admitted("Mission", [created(), promise], withdraw(promise))["status"] == "draft"


@pytest.mark.parametrize("pick", ["an_earlier_gate", "a_withdrawal", "another_action"])
def test_only_the_gate_event_that_produced_the_stage_is_withdrawn(pick):
    c, promise, ok = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")
    back = withdraw(ok)
    events, event = {
        "an_earlier_gate": ([c, promise, ok], withdraw(promise)),
        "a_withdrawal": ([c, promise, ok, back], withdraw(back)),
        "another_action": ([c, promise], confirm(PG_CONFIRM, "withdrawn", supersedes=promise["event_id"])),
    }[pick]
    refused("PeriodGoal", events, event, "Only the commitment or confirmation that produced the current stage")


def test_a_confirmed_period_goal_is_changed_by_a_round_of_commitment_and_confirmation():
    events = [created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")]
    again = commit(PG_COMMIT)
    assert admitted("PeriodGoal", events, again) == effect("PeriodGoal", events, again, opens_round=True)
    ok = confirm(PG_CONFIRM, "accepted")
    assert admitted("PeriodGoal", [*events, again], ok) == effect(
        "PeriodGoal", [*events, again], ok, writes_back=True, candidate_commit=again["event_id"])
    refused("PeriodGoal", [*events, again], commit(PG_COMMIT), "already awaits confirmation")
    back = confirm(PG_CONFIRM, "returned")
    assert admitted("PeriodGoal", [*events, again], back) == effect("PeriodGoal", [*events, again], back)
    refused("PeriodGoal", [*events, again, back], confirm(PG_CONFIRM, "accepted"), "not allowed at this stage")
    refused("PeriodGoal", [*events, again, ok], confirm(PG_CONFIRM, "accepted"), "not allowed at this stage")


def test_a_confirmed_long_term_goal_is_changed_by_a_confirmation_that_carries_the_candidate():
    events = [created(), confirm(LTG, "accepted")]
    ok = confirm(LTG, "accepted")
    assert admitted("LongTermGoal", events, ok) == effect("LongTermGoal", events, ok, writes_back=True)
    refused("LongTermGoal", events, confirm(LTG, "returned"), "not allowed at this stage")


def test_an_in_progress_mission_reruns_its_initiation_gate_to_change_its_content():
    events = [*established_mission(), refreshed()]
    again, ok = commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "accepted", "initiation")
    assert admitted("Mission", events, again) == effect("Mission", events, again, opens_round=True)
    assert admitted("Mission", [*events, again], ok) == effect(
        "Mission", [*events, again], ok, writes_back=True, candidate_commit=again["event_id"])
    refused("Mission", events, confirm(M_CONFIRM, "accepted", "initiation"), "not allowed at this stage")


def test_a_core_battle_reruns_its_initiation_gate_through_the_ceo():
    events = [created(), marked(), commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "accepted", "initiation"),
              confirm(M_CEO, "accepted", "initiation"), refreshed()]
    again, dri = commit(M_COMMIT, "initiation"), confirm(M_CONFIRM, "accepted", "initiation")
    assert admitted("Mission", [*events, again], dri) == effect("Mission", [*events, again], dri)
    ceo = confirm(M_CEO, "accepted", "initiation")
    assert admitted("Mission", [*events, again, dri], ceo) == effect(
        "Mission", [*events, again, dri], ceo, writes_back=True, candidate_commit=again["event_id"])
    back = confirm(M_CEO, "returned", "initiation")
    assert admitted("Mission", [*events, again, dri], back) == effect("Mission", [*events, again, dri], back)
    refused("Mission", [*events, again, dri, back], ceo, "not allowed at this stage")


def test_taking_back_the_formal_content_discards_an_open_round():
    c, promise, ok = created(), commit(PG_COMMIT), confirm(PG_CONFIRM, "accepted")
    again = commit(PG_COMMIT)
    back = withdraw(ok)
    assert admitted("PeriodGoal", [c, promise, ok, again], back) == effect(
        "PeriodGoal", [c, promise, ok, again], back, unmakes_formal=True)
    confirmed = confirm(PG_CONFIRM, "accepted")
    assert admitted("PeriodGoal", [c, promise, ok, again, back], confirmed) == effect(
        "PeriodGoal", [c, promise, ok, again, back], confirmed, makes_formal=True)


def test_returning_a_mission_marked_after_establishment_to_draft_takes_its_formal_content_back():
    events = [*established_mission(), marked()]
    back = confirm(M_CEO, "returned", "initiation")
    assert admitted("Mission", events, back) == effect("Mission", events, back, unmakes_formal=True)
    again = commit(M_COMMIT, "initiation")
    assert admitted("Mission", [*events, back], again) == effect("Mission", [*events, back], again)


def test_one_ceo_acceptance_establishes_a_marked_mission_and_writes_back_the_open_candidate():
    events = [*established_mission(), commit(M_COMMIT, "initiation")]
    rerun = events[-1]
    events += [marked(), confirm(M_CONFIRM, "accepted", "initiation")]
    ceo = confirm(M_CEO, "accepted", "initiation")
    assert admitted("Mission", events, ceo) == effect(
        "Mission", events, ceo, writes_back=True, candidate_commit=rerun["event_id"])
    back = confirm(M_CEO, "returned", "initiation")
    assert admitted("Mission", events, back) == effect("Mission", events, back, unmakes_formal=True)
    refused("Mission", [*events, back], ceo, "not allowed at this stage")


def test_a_return_that_took_the_formal_content_back_is_not_withdrawn():
    events = [*established_mission(), marked(), confirm(M_CEO, "returned", "initiation")]
    refused("Mission", events, withdraw(events[-1]), "took the formal content back")
    before_formal = _mission_path("ceo_returned")
    assert admitted("Mission", before_formal, withdraw(before_formal[-1]))["status"] == "awaiting_ceo"
