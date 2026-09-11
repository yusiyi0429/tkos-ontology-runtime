"""Independent negative cases for exact M1A sources and decision boundaries."""
import pytest

from memory_service_runtime.governed.errors import GovernedError
from tests.test_method_m1a import MemoryExecution, _start, _memo_ready, _report, _submit_pass, _meeting, _proposal


def test_report_cannot_import_another_issues_plan():
    e = MemoryExecution()
    first, second = _start(e), _start(e)
    _, foreign_payload = _report(e, second, _memo_ready(e, second))
    _, local_payload = _report(e, first, _memo_ready(e, first))
    local_payload["plan_ref"] = foreign_payload["plan_ref"]
    before = len(e.revisions)
    with pytest.raises(GovernedError) as error:
        e.call("dri", "publish_report", {"payload": local_payload}, first)
    assert error.value.code == "STALE_DEPENDENCY"
    assert len(e.revisions) == before


def test_different_minutes_cannot_be_reconciled_with_empty_differences():
    e = MemoryExecution()
    oid = _start(e)
    report, _ = _report(e, oid, _memo_ready(e, oid))
    _submit_pass(e, oid, report)
    meeting = e.call("dri", "open_meeting", {"report_ref": report, "title": "Meeting", "objective": "Decide",
        "material_refs": [report, e.evidence]}, oid)["meeting_ref"]
    accounts = {}
    for account in ("ceo", "dri"):
        accounts[account] = e.call(f"{account}_agent", "publish_minutes", {"meeting_ref": meeting, "title": "Minutes",
            "account": account, "body": f"{account} different interpretation", "source_refs": [e.evidence]}, oid)["minutes_ref"]
    with pytest.raises(GovernedError) as error:
        e.call("ceo_agent", "reconcile_minutes", {"meeting_ref": meeting, "ceo_minutes_ref": accounts["ceo"],
            "dri_minutes_ref": accounts["dri"], "title": "Final", "body": "Silently erased differences", "differences": []}, oid)
    assert error.value.code == "INVALID_REQUEST"
    with pytest.raises(GovernedError) as agent_error:
        e.call("ceo_agent", "publish_minutes", {"meeting_ref": meeting, "title": "Impersonation",
            "account": "dri", "body": "Write the other account", "source_refs": [e.evidence]}, oid)
    assert agent_error.value.code == "FORBIDDEN"


def test_ceo_cannot_turn_a_proposal_directly_into_strategy():
    e = MemoryExecution()
    oid = _start(e)
    report, _ = _report(e, oid, _memo_ready(e, oid))
    _submit_pass(e, oid, report)
    agreement = _meeting(e, oid, report)
    e.call("ceo", "decide_update", {"agreement_ref": agreement, "needs_update": True, "reason": "Change"}, oid)
    proposal = _proposal(e, oid, agreement)
    with pytest.raises(GovernedError) as error:
        e.call("ceo", "confirm_update", {"proposal_ref": proposal, "reason": "Skip impact review"}, oid)
    assert error.value.code == "INVALID_STATE"
    assert not any(o["object_type"] == "Strategy" for o in e.objects.values())
    with pytest.raises(GovernedError) as impact:
        e.call("co_agent", "review_update", {"proposal_ref": proposal, "accepted": True, "impact_level": "domain",
            "findings": ["Incorrectly classify company change as domain-only"]}, oid)
    assert impact.value.code == "INVALID_REQUEST"


def test_report_reopening_research_requires_a_new_plan_and_both_memo_checks():
    e = MemoryExecution()
    oid = _start(e)
    memo = _memo_ready(e, oid)
    report, original = _report(e, oid, memo)
    _submit_pass(e, oid, report)
    e.call("ceo_agent", "publish_memo", {"payload": {"title": "Additional research", "issue_ref": e.current_ref(oid),
        "question": "What additional evidence is needed?", "scope": "Company", "expected_output": "Revised conclusion",
        "source_refs": [e.evidence]}}, oid)
    assert "precheck" not in e.states[oid]
    with pytest.raises(GovernedError) as error:
        e.call("dri", "publish_report", {"payload": original}, oid)
    assert error.value.code == "INVALID_STATE"
    assert e.states[oid]["phase"] == "memo_clarifying"
