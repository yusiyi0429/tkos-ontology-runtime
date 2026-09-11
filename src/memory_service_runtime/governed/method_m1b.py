"""M1B L5 revision 837 governed actions.

Every public transition shares the MethodExecution transaction, permission
barrier, exact-reference checks, CAS, immutable receipt, and audit trail. The
application chooses the next action; this module never silently orchestrates
human decisions or grants M2 execution rights.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import GovernedError
from .method_m1b_models import (
    BusinessFactPayload,
    CandidateSetPayload,
    LTCOPayload,
    M1B_ACTION_PARAMS,
    MissionPayload,
    PCOPayload,
    ReviewWindowPayload,
)


AGENT_ACTIONS = frozenset({
    "m1b_generate_review", "m1b_regenerate_review", "m1b_advise_ltco",
    "m1b_draft_pco", "m1b_revise_pco", "m1b_draft_mission",
    "m1b_revise_mission", "m1b_open_window", "m1b_close_window",
    "m1b_resolve_window",
})
CEO_ACTIONS = frozenset({
    "m1b_return_ltco", "m1b_confirm_ltco", "m1b_confirm_candidates",
    "m1b_reopen_candidates", "m1b_reopen_window",
})


def _fail(code: str, message: str) -> None:
    raise GovernedError(code, message)


def _payload(revision: dict[str, Any]) -> dict[str, Any]:
    return revision["payload"]


def _oid(head: dict[str, Any]) -> str:
    return str(head["object_id"])


def _same_ref(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return all(str(a.get(key)) == str(b.get(key)) for key in ("object_id", "revision_id", "payload_hash"))


def _phase(e: Any, head: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    state = deepcopy(e.state(head))
    if state.get("phase") not in allowed:
        _fail("INVALID_STATE", "This M1B phase does not permit the action.")
    return state


def _actor_roles(e: Any) -> None:
    kind = e.kind
    if kind in AGENT_ACTIONS:
        e.require_role("CO_AGENT", principal_type="agent")
    elif kind in CEO_ACTIONS:
        e.require_role("CEO", principal_type="human")
    elif kind in {"m1b_propose_ltco", "m1b_revise_ltco"}:
        e.require_role("CEO_AGENT", principal_type="agent")
    elif kind in {"m1b_record_fact", "m1b_correct_fact"}:
        if e.ctx.principal_type == "agent":
            e.require_role("CO_AGENT", principal_type="agent")
        else:
            # The operation domain selects the responsibility. A CEO role in
            # another domain must not mask this domain's legitimate DRI role.
            roles = {row["role"] for row in e.ctx.assignments if row["domain_id"] == e.domain_id}
            role = "CEO" if "CEO" in roles else "DOMAIN_DRI"
            e.require_role(role, principal_type="human")


def _principal(e: Any, principal_id: str, *, role: str | None = None) -> None:
    e.validate_principal(principal_id, "human", role=role)


def _units(e: Any, strategy_ref: dict[str, Any], *, current: bool = True) -> set[str]:
    if current:
        result = e.current_strategy(strategy_ref)
        # The helper may return the dependency tuple or only validate it.
        if isinstance(result, tuple):
            _, revision = result
        else:
            _, revision = e.ref(strategy_ref, types={"Strategy"}, effective=True, current=False)
    else:
        _, revision = e.ref(strategy_ref, types={"Strategy"}, current=False)
    return {unit["unit_id"] for unit in _payload(revision)["map"]["units"]}


def _fact(e: Any, payload: dict[str, Any], *, correcting: bool = False) -> None:
    BusinessFactPayload.model_validate(payload)
    e.ref(payload["source_ref"], types={"EvidenceAsset"}, current=False)
    subject = payload["subject_ref"]
    if subject.get("object_id"):
        head, revision = e.ref({key: subject[key] for key in ("object_id", "revision_id", "payload_hash")}, current=False)
        if subject.get("outcome_id"):
            content = _payload(revision)
            outcomes = content.get("unit_outcomes", content.get("outcomes", []))
            if subject["outcome_id"] not in {item["outcome_id"] for item in outcomes}:
                _fail("INVALID_REQUEST", "Fact subject outcome is absent from the cited revision.")
        if head["object_type"] in {"ReviewWindow", "CandidateSet"}:
            _fail("INVALID_REQUEST", "Window state is not an atomic business fact subject.")
    if correcting:
        if not payload.get("corrects_ref") or not _same_ref(payload["corrects_ref"], e.exact_ref(e.target, e.target_revision)):
            _fail("INVALID_REQUEST", "A fact correction must cite its exact original record.")
        old = _payload(e.target_revision)
        if payload["fact_id"] == old["fact_id"]:
            _fail("INVALID_REQUEST", "Correcting facts have a new fact_id and retain the original record.")
        if payload["subject_ref"] != old["subject_ref"] or payload["metric"] != old["metric"]:
            _fail("INVALID_REQUEST", "A correction preserves the original subject and metric.")
        if e.state(e.target).get("corrected_by_ref"):
            _fail("INVALID_STATE", "Correct the newest correction record, preserving the full chain.")
    elif payload.get("corrects_ref"):
        _fail("INVALID_REQUEST", "Corrections must use m1b_correct_fact.")


def _period_review(e: Any, payload: dict[str, Any]) -> None:
    for ref in payload["target_refs"]:
        e.ref(ref, types={"PCO", "Mission"}, current=False)
    for ref in payload.get("fact_refs", []):
        e.ref(ref, types={"BusinessFact"}, current=False)
    if e.kind == "m1b_regenerate_review":
        previous = _payload(e.target_revision)
        if payload["review_id"] != previous["review_id"] or payload["period"] != previous["period"]:
            _fail("INVALID_REQUEST", "Regeneration preserves review identity and period.")
        if payload["generation_version"] == previous["generation_version"]:
            _fail("INVALID_REQUEST", "Regeneration must identify a new analysis generation.")


def _ltco(e: Any, payload: dict[str, Any]) -> None:
    LTCOPayload.model_validate(payload)
    unit_ids = _units(e, payload["strategy_ref"])
    if not {item["unit_id"] for item in payload["outcomes"]}.issubset(unit_ids):
        _fail("INVALID_REQUEST", "LTCO outcomes cite units absent from the exact Strategy map.")
    _principal(e, payload["owner_principal_id"], role="CEO")
    if payload.get("advice_ref"):
        _, advice = e.ref(payload["advice_ref"], types={"LTCOReviewAdvice"}, current=False)
        if not _same_ref(_payload(advice)["strategy_ref"], payload["strategy_ref"]):
            _fail("STALE_DEPENDENCY", "LTCO advice must use the proposed Strategy baseline.")


def _pco(e: Any, payload: dict[str, Any]) -> None:
    PCOPayload.model_validate(payload)
    unit_ids = _units(e, payload["strategy_ref"])
    _, ltco = e.ref(payload["ltco_ref"], types={"LTCO"}, effective=True, current=False)
    if not _same_ref(_payload(ltco)["strategy_ref"], payload["strategy_ref"]):
        _fail("STALE_DEPENDENCY", "PCO and its effective LTCO must share the exact Strategy baseline.")
    if not {item["unit_id"] for item in payload["unit_outcomes"]}.issubset(unit_ids):
        _fail("INVALID_REQUEST", "PCO outcomes cite units absent from the Strategy map.")
    from datetime import datetime
    period = payload["period"]
    ltco_period = _payload(ltco)["period"]
    if not (datetime.fromisoformat(ltco_period["start"]) <= datetime.fromisoformat(period["start"])
            < datetime.fromisoformat(period["end"]) <= datetime.fromisoformat(ltco_period["end"])):
        _fail("INVALID_REQUEST", "PCO period must be contained in its LTCO period.")
    for outcome in payload["unit_outcomes"]:
        _principal(e, outcome["dri_principal_id"], role="DOMAIN_DRI")


def _mission(e: Any, payload: dict[str, Any], *, pco_payload: dict[str, Any] | None = None,
             pco_ref: dict[str, Any] | None = None, historical: bool = False, validate_people: bool = True) -> None:
    MissionPayload.model_validate(payload)
    if pco_payload is None:
        pco_head, pco_revision = e.ref(payload["pco_ref"], types={"PCO"}, current=not historical)
        pco_payload = _payload(pco_revision)
        pco_ref = e.exact_ref(pco_head, pco_revision)
    if not _same_ref(payload["pco_ref"], pco_ref):
        _fail("INVALID_REQUEST", "Mission does not cite the required exact PCO version.")
    outcome_ids = {item["outcome_id"] for item in pco_payload["unit_outcomes"]}
    if not {support["outcome_ref"]["outcome_id"] for support in payload["supports"]}.issubset(outcome_ids):
        _fail("INVALID_REQUEST", "Mission support refers to an absent PCO outcome.")
    if validate_people:
        _principal(e, payload["owner_principal_id"])
        for principal_id in payload["participants"]:
            _principal(e, principal_id)
    from datetime import datetime
    deadline = datetime.fromisoformat(payload["hard_deadline"])
    if not (datetime.fromisoformat(pco_payload["period"]["start"]) <= deadline
            <= datetime.fromisoformat(pco_payload["period"]["end"])):
        _fail("INVALID_REQUEST", "Mission deadline must fall within its PCO period.")


def _participants(e: Any, participants: list[dict[str, Any]]) -> None:
    for participant in participants:
        e.validate_assignment(participant["assignment_id"], participant["principal_id"], "human")
        if participant.get("personal_agent_id"):
            e.validate_principal(participant["personal_agent_id"], "agent")
            e.check_personal_agent(participant["personal_agent_id"], participant["principal_id"])


def _window_actor(e: Any, payload: dict[str, Any], *, agent: bool = False) -> dict[str, Any]:
    actor = e.ctx.principal_id
    if agent:
        owner = e.params["owner_principal_id"]
        participant = next((p for p in payload["participants"] if p["principal_id"] == owner
                            and p.get("personal_agent_id") == actor), None)
        if participant is None:
            _fail("FORBIDDEN", "Only the participant's provisioned personal Agent can assist.")
        e.require_actor(actor, "agent")
        e.check_personal_agent(actor, owner)
        e.validate_principal(actor, "agent")
    else:
        participant = next((p for p in payload["participants"] if p["principal_id"] == actor), None)
        if participant is None:
            _fail("FORBIDDEN", "Only named window participants may publish their own opinion.")
        e.require_actor(actor, "human")
    e.validate_assignment(participant["assignment_id"], participant["principal_id"], "human")
    return participant


def _target_set(e: Any, refs: list[dict[str, Any]], *, locked_window: str | None = None,
                current: bool = True, validate_people: bool = True) -> tuple[tuple[dict, dict], list[tuple[dict, dict]]]:
    pcos, missions = [], []
    for ref in refs:
        head, revision = e.ref(ref, types={"PCO", "Mission"}, current=current)
        if e.state(head).get("window_id") not in {None, locked_window}:
            _fail("INVALID_STATE", "The target already belongs to another active review window.")
        (pcos if head["object_type"] == "PCO" else missions).append((head, revision))
    if len(pcos) != 1 or not missions:
        _fail("INVALID_REQUEST", "A review set contains exactly one PCO and at least one Mission.")
    pco_head, pco_revision = pcos[0]
    pco_ref = e.exact_ref(pco_head, pco_revision)
    for _, revision in missions:
        _mission(e, _payload(revision), pco_payload=_payload(pco_revision), pco_ref=pco_ref, validate_people=validate_people)
    return pcos[0], missions


def _open_window(e: Any, payload: dict[str, Any]) -> None:
    ReviewWindowPayload.model_validate(payload)
    _units(e, payload["strategy_ref"])
    _participants(e, payload["participants"])
    pco, missions = _target_set(e, payload["target_refs"])
    for head, _ in [pco, *missions]:
        _phase(e, head, {"draft"})
    content = _payload(pco[1])
    if (not _same_ref(content["strategy_ref"], payload["strategy_ref"])
            or not _same_ref(content["ltco_ref"], payload["ltco_ref"])
            or content["period"] != payload["period"]):
        _fail("INVALID_REQUEST", "Window period and sources must match its fixed PCO baseline.")
    _pco(e, content)
    if payload.get("previous_window_ref"):
        _fail("INVALID_REQUEST", "Only CEO reopening can attach a previous window.")
    e._m1b["targets"] = [pco, *missions]


def _opinion_rows(e: Any, window: dict[str, Any], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = e.list_reviews(_oid(window))
    result = {str(row["record_id"]): row for row in rows if row["kind"] == "window_comment"}
    active = {record_id for ids in state.get("active_opinions", {}).values() for record_id in ids}
    if not active.issubset(result):
        _fail("INVALID_STATE", "Window opinion state does not match its immutable records.")
    return result


def _resolve(e: Any) -> None:
    state = _phase(e, e.target, {"closed"})
    if state.get("candidate_ref"):
        _fail("INVALID_STATE", "A window admits only one formal resolution.")
    window = _payload(e.target_revision)
    params = e.params
    _pco(e, params["pco_payload"])
    if not (_same_ref(params["pco_payload"]["strategy_ref"], window["strategy_ref"])
            and _same_ref(params["pco_payload"]["ltco_ref"], window["ltco_ref"])
            and params["pco_payload"]["period"] == window["period"]):
        _fail("STALE_DEPENDENCY", "Resolution must retain the exact source baseline reviewed in this window.")
    pco, missions = _target_set(e, window["target_refs"], locked_window=_oid(e.target), validate_people=False)
    if {m["object_id"] for m in params["missions"]} != {_oid(head) for head, _ in missions}:
        _fail("INVALID_REQUEST", "Resolution must include every frozen Mission exactly once.")
    expected = set(state.get("frozen_opinion_ids", []))
    if {item["review_record_id"] for item in params["dispositions"]} != expected:
        _fail("INVALID_REQUEST", "Resolution must account for every frozen effective opinion exactly once.")
    outcomes = {o["outcome_id"] for o in params["pco_payload"]["unit_outcomes"]}
    for mission in params["missions"]:
        if not {support["outcome_id"] for support in mission["supports"]}.issubset(outcomes):
            _fail("INVALID_REQUEST", "Candidate Mission supports must cite existing candidate PCO outcomes.")
        # Static binding is generated after the new PCO revision exists. The
        # provisional ref is validation-only and is never stored or returned.
        provisional = _candidate_mission_payload(mission, e.exact_ref(*pco))
        _mission(e, provisional, pco_payload=params["pco_payload"], pco_ref=e.exact_ref(*pco))
    e._m1b.update(state=state, window=window, pco=pco, missions=missions)


def _candidate(e: Any, *, confirmation: bool) -> None:
    state = _phase(e, e.target, {"pending"})
    payload = _payload(e.target_revision)
    window, window_rev = e.ref(payload["window_ref"], types={"ReviewWindow"}, current=False)
    window_state = _phase(e, window, {"resolved"})
    if not _same_ref(window_state.get("candidate_ref", {}), e.exact_ref(e.target, e.target_revision)):
        _fail("INVALID_STATE", "The candidate is not the authoritative resolution of its window.")
    pco, missions = _target_set(e, payload["target_refs"], locked_window=_oid(window), validate_people=confirmation)
    if confirmation:
        _units(e, payload["strategy_ref"])
        _pco(e, _payload(pco[1]))
        if not (_same_ref(_payload(pco[1])["strategy_ref"], payload["strategy_ref"])
                and _same_ref(_payload(pco[1])["ltco_ref"], payload["ltco_ref"])):
            _fail("STALE_DEPENDENCY", "The exact candidate set and its source versions differ.")
    e._m1b.update(state=state, window_head=window, window_revision=window_rev,
                  window_state=window_state, pco=pco, missions=missions)


def _reopen(e: Any, *, candidate: bool) -> None:
    if candidate:
        _candidate(e, confirmation=False)
        old_head = e._m1b["window_head"]
        old_rev = e._m1b["window_revision"]
        refs = _payload(e.target_revision)["target_refs"]
    else:
        _phase(e, e.target, {"open", "closed"})
        old_head, old_rev = e.target, e.target_revision
        refs = _payload(old_rev)["target_refs"]
        pco, missions = _target_set(e, refs, locked_window=_oid(old_head), validate_people=False)
        e._m1b.update(window_head=old_head, window_revision=old_rev,
                      window_state=deepcopy(e.state(old_head)), pco=pco, missions=missions)
    old_payload = _payload(old_rev)
    payload = {
        "title": e.params["title"], "period": old_payload["period"],
        "strategy_ref": e.params.get("rebase_strategy_ref") or old_payload["strategy_ref"],
        "ltco_ref": e.params.get("rebase_ltco_ref") or old_payload["ltco_ref"],
        "target_refs": refs,
        "participants": e.params.get("participants") or old_payload["participants"],
        "previous_window_ref": e.exact_ref(old_head, old_rev),
    }
    ReviewWindowPayload.model_validate(payload)
    _units(e, payload["strategy_ref"])
    _, ltco = e.ref(payload["ltco_ref"], types={"LTCO"}, effective=True, current=False)
    if not _same_ref(_payload(ltco)["strategy_ref"], payload["strategy_ref"]):
        _fail("STALE_DEPENDENCY", "Reopened review must use LTCO aligned to its Strategy baseline.")
    _participants(e, payload["participants"])
    e._m1b["new_window_payload"] = payload


def collect(e: Any) -> None:
    """Pure admission and dependency collection, shared by prepare and execute."""
    M1B_ACTION_PARAMS[e.kind].model_validate(e.params)
    e._m1b = {}
    _actor_roles(e)
    kind, params = e.kind, e.params
    if kind in {"m1b_record_fact", "m1b_correct_fact"}:
        _fact(e, params["payload"], correcting=kind == "m1b_correct_fact")
    elif kind in {"m1b_generate_review", "m1b_regenerate_review"}:
        _period_review(e, params["payload"])
    elif kind == "m1b_advise_ltco":
        content = params["payload"]
        e.ref(content["period_review_ref"], types={"PeriodReview"}, current=False)
        _units(e, content["strategy_ref"])
        if content.get("ltco_ref"):
            e.ref(content["ltco_ref"], types={"LTCO"}, effective=True, current=False)
    elif kind in {"m1b_propose_ltco", "m1b_revise_ltco"}:
        _ltco(e, params["payload"])
        e.check_personal_agent(e.ctx.principal_id, params["payload"]["owner_principal_id"])
        if kind == "m1b_revise_ltco":
            _phase(e, e.target, {"draft", "returned"})
            if params["payload"]["owner_principal_id"] != _payload(e.target_revision)["owner_principal_id"]:
                _fail("INVALID_REQUEST", "Changing the LTCO owner requires a responsibility contract.")
    elif kind == "m1b_return_ltco":
        _phase(e, e.target, {"draft", "confirmed"})
        e.require_actor(_payload(e.target_revision)["owner_principal_id"], "human")
    elif kind == "m1b_confirm_ltco":
        _phase(e, e.target, {"draft"})
        _ltco(e, _payload(e.target_revision))
        e.require_actor(_payload(e.target_revision)["owner_principal_id"], "human")
    elif kind in {"m1b_draft_pco", "m1b_revise_pco"}:
        _pco(e, params["payload"])
        if kind == "m1b_revise_pco":
            state = _phase(e, e.target, {"draft"})
            if state.get("window_id"):
                _fail("INVALID_STATE", "Window-bound PCO content changes only through formal resolution.")
    elif kind in {"m1b_draft_mission", "m1b_revise_mission"}:
        _mission(e, params["payload"])
        pco, _ = e.ref(params["payload"]["pco_ref"], types={"PCO"})
        if e.state(pco).get("phase") != "draft" or e.state(pco).get("window_id"):
            _fail("INVALID_STATE", "A Mission draft requires an unlocked draft PCO.")
        if kind == "m1b_revise_mission":
            state = _phase(e, e.target, {"draft"})
            if state.get("window_id"):
                _fail("INVALID_STATE", "Window-bound Mission content changes only through formal resolution.")
    elif kind == "m1b_open_window":
        _open_window(e, params["payload"])
    elif kind in {"m1b_comment", "m1b_withdraw_comment", "m1b_assist_review"}:
        state = _phase(e, e.target, {"open"})
        content = _payload(e.target_revision)
        _window_actor(e, content, agent=kind == "m1b_assist_review")
        records = _opinion_rows(e, e.target, state)
        if kind != "m1b_withdraw_comment":
            if not any(_same_ref(params["target_ref"], ref) for ref in content["target_refs"]):
                _fail("FORBIDDEN", "Window access is limited to its fixed exact targets.")
            # The special window grant permits only the pinned target, never
            # other versions or the target's underlying source material.
            e.ref(params["target_ref"], types={"PCO", "Mission"}, current=False)
        prior = params.get("replaces_record_id") or params.get("review_record_id")
        if prior:
            record = records.get(prior)
            active = state.get("active_opinions", {}).get(e.ctx.principal_id, [])
            if not record or prior not in active or str(record["principal_id"]) != e.ctx.principal_id:
                _fail("FORBIDDEN", "Only the author may withdraw or replace an active opinion.")
            if kind == "m1b_comment" and (
                str(record["target_object_id"]) != params["target_ref"]["object_id"]
                or str(record["target_revision_id"]) != params["target_ref"]["revision_id"]
            ):
                _fail("INVALID_REQUEST", "A replacement opinion must concern the same exact target.")
        if kind == "m1b_assist_review":
            for ref in params.get("source_refs", []):
                # Root applies ordinary source permission, not the human's
                # or another domain's ambient identity.
                e.ref(ref, current=False)
        e._m1b.update(state=state, records=records)
    elif kind == "m1b_close_window":
        state = _phase(e, e.target, {"open"})
        _opinion_rows(e, e.target, state)
        e._m1b["state"] = state
    elif kind == "m1b_resolve_window":
        _resolve(e)
    elif kind == "m1b_confirm_candidates":
        _candidate(e, confirmation=True)
    elif kind in {"m1b_reopen_candidates", "m1b_reopen_window"}:
        _reopen(e, candidate=kind == "m1b_reopen_candidates")
    else:
        _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL", "Unsupported M1B action.")


def _created(e: Any, kind: str, payload: dict[str, Any], *, phase: str = "draft",
             status: str = "draft") -> tuple[dict, dict]:
    head, revision = e.create(kind, payload, status=status)
    e.set_state(head, {"phase": phase})
    return head, revision


def _result(e: Any, head: dict[str, Any], revision: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {**e.exact_ref(head, revision), **extra}


def _candidate_mission_payload(content: dict[str, Any], pco_ref: dict[str, Any]) -> dict[str, Any]:
    payload = {key: deepcopy(value) for key, value in content.items() if key not in {"object_id", "supports"}}
    payload["pco_ref"] = pco_ref
    payload["supports"] = [{"outcome_ref": {**pco_ref, "outcome_id": s["outcome_id"]},
                            "contribution": s["contribution"]} for s in content["supports"]]
    return payload


def _create_window(e: Any, payload: dict[str, Any], targets: list[tuple[dict, dict]]) -> tuple[dict, dict]:
    head, revision = e.create("ReviewWindow", payload, status="open")
    e.set_state(head, {"phase": "open", "active_opinions": {}, "frozen_opinion_ids": []})
    for target, _ in targets:
        state = deepcopy(e.state(target))
        state.update(window_id=_oid(head), phase="under_review")
        e.set_state(target, state)
        e.transition(target)
    return head, revision


def _reopen_run(e: Any) -> dict[str, Any]:
    cached = e._m1b
    old_window = cached["window_head"]
    targets = [cached["pco"], *cached["missions"]]
    head, revision = _create_window(e, cached["new_window_payload"], targets)
    old_state = cached["window_state"]
    if old_state["phase"] == "open":
        old_state["frozen_opinion_ids"] = sorted({record for records in old_state.get("active_opinions", {}).values() for record in records})
    old_state.update(phase="reopened", reopened_window_ref=e.exact_ref(head, revision))
    e.set_state(old_window, old_state)
    e.transition(old_window)
    if e.kind == "m1b_reopen_candidates":
        state = cached["state"]
        state.update(phase="reopened", reopened_window_ref=e.exact_ref(head, revision))
        e.set_state(e.target, state)
        e.transition(e.target)
    review_id = e.review("ceo_reopen", e.exact_ref(e.target, e.target_revision),
                         {"reason": e.params["reason"], "new_window_ref": e.exact_ref(head, revision),
                          "rebase_strategy_ref": e.params.get("rebase_strategy_ref"),
                          "rebase_ltco_ref": e.params.get("rebase_ltco_ref")}, window_id=_oid(old_window))
    return _result(e, head, revision, review_record_id=review_id, phase="open")


def run(e: Any) -> dict[str, Any]:
    """Execute one typed action; the caller commits the shared governance receipt."""
    collect(e)
    kind, params = e.kind, e.params
    if kind in {"m1b_record_fact", "m1b_correct_fact"}:
        head, revision = _created(e, "BusinessFact", params["payload"], phase="recorded", status="recorded")
        if kind == "m1b_correct_fact":
            state = deepcopy(e.state(e.target))
            state["corrected_by_ref"] = e.exact_ref(head, revision)
            e.set_state(e.target, state)
            e.transition(e.target)
        return _result(e, head, revision, phase="recorded")
    if kind == "m1b_generate_review":
        head, revision = _created(e, "PeriodReview", params["payload"], phase="generated", status="recorded")
        return _result(e, head, revision, nature="agent_analysis", phase="generated")
    if kind == "m1b_regenerate_review":
        head, revision = e.revise(e.target, params["payload"], status="recorded", effective=True)
        return _result(e, head, revision, nature="agent_analysis", phase="generated")
    if kind == "m1b_advise_ltco":
        head, revision = _created(e, "LTCOReviewAdvice", params["payload"], phase="generated", status="recorded")
        return _result(e, head, revision, nature="agent_analysis", phase="generated")
    creation = {"m1b_propose_ltco": "LTCO", "m1b_draft_pco": "PCO", "m1b_draft_mission": "Mission"}
    if kind in creation:
        head, revision = _created(e, creation[kind], params["payload"])
        return _result(e, head, revision, phase="draft")
    if kind in {"m1b_revise_ltco", "m1b_revise_pco", "m1b_revise_mission"}:
        head, revision = e.revise(e.target, params["payload"])
        state = deepcopy(e.state(head))
        state["phase"] = "draft"
        e.set_state(head, state)
        result = _result(e, head, revision, phase="draft")
        if kind == "m1b_revise_ltco":
            result["review_record_id"] = e.review("ltco_revision_response", e.exact_ref(head, revision),
                                                  {"response": params["response"]})
        return result
    if kind in {"m1b_return_ltco", "m1b_confirm_ltco"}:
        confirmed = kind == "m1b_confirm_ltco"
        phase = "confirmed" if confirmed else "returned"
        state = deepcopy(e.state(e.target))
        state["phase"] = phase
        review_id = e.review("ltco_confirmation" if confirmed else "ltco_feedback",
                             e.exact_ref(e.target, e.target_revision), {"reason": params["reason"]})
        state["last_review_record_id"] = review_id
        e.set_state(e.target, state)
        e.transition(e.target, status="confirmed" if confirmed else None, effective=confirmed)
        return _result(e, e.target, e.target_revision, phase=phase, review_record_id=review_id)
    if kind == "m1b_open_window":
        head, revision = _create_window(e, params["payload"], e._m1b["targets"])
        return _result(e, head, revision, phase="open")
    if kind == "m1b_comment":
        state = e._m1b["state"]
        review_id = e.review("window_comment", params["target_ref"],
                             {"content": params["content"], "replaces_record_id": params.get("replaces_record_id")},
                             window_id=_oid(e.target))
        active = state.setdefault("active_opinions", {}).setdefault(e.ctx.principal_id, [])
        if params.get("replaces_record_id"):
            active.remove(params["replaces_record_id"])
        active.append(review_id)
        e.set_state(e.target, state)
        e.transition(e.target)
        return {"window_id": _oid(e.target), "review_record_id": review_id, "phase": "open"}
    if kind == "m1b_withdraw_comment":
        state = e._m1b["state"]
        old = e._m1b["records"][params["review_record_id"]]
        review_id = e.review("window_opinion_withdrawal", e.exact_ref(e.target, e.target_revision),
                             {"reason": params["reason"], "withdrawn_record_id": params["review_record_id"],
                              "target_object_id": str(old["target_object_id"]),
                              "target_revision_id": str(old["target_revision_id"])}, window_id=_oid(e.target))
        state["active_opinions"][e.ctx.principal_id].remove(params["review_record_id"])
        e.set_state(e.target, state)
        e.transition(e.target)
        return {"window_id": _oid(e.target), "review_record_id": review_id, "withdrawn_record_id": params["review_record_id"]}
    if kind == "m1b_assist_review":
        review_id = e.review("personal_agent_analysis", params["target_ref"],
                             {key: value for key, value in params.items() if key != "target_ref"}, window_id=_oid(e.target))
        # Collaboration chronology advances CAS, not the business content.
        e.transition(e.target)
        return {"window_id": _oid(e.target), "review_record_id": review_id, "nature": "agent_analysis"}
    if kind == "m1b_close_window":
        state = e._m1b["state"]
        state["frozen_opinion_ids"] = sorted({record for records in state.get("active_opinions", {}).values() for record in records})
        state["phase"] = "closed"
        review_id = e.review("window_closed", e.exact_ref(e.target, e.target_revision),
                             {"reason": params["reason"], "frozen_opinion_ids": state["frozen_opinion_ids"]}, window_id=_oid(e.target))
        state["close_record_id"] = review_id
        e.set_state(e.target, state)
        e.transition(e.target)
        return {"window_id": _oid(e.target), "phase": "closed", "frozen_opinion_ids": state["frozen_opinion_ids"], "review_record_id": review_id}
    if kind == "m1b_resolve_window":
        old_pco, _ = e._m1b["pco"]
        pco, pco_revision = e.revise(old_pco, params["pco_payload"])
        e.checkpoint("after_method_candidate_write")
        pco_ref = e.exact_ref(pco, pco_revision)
        targets = [pco_ref]
        by_id = {_oid(head): head for head, _ in e._m1b["missions"]}
        for content in params["missions"]:
            payload = _candidate_mission_payload(content, pco_ref)
            head, revision = e.revise(by_id[content["object_id"]], payload)
            targets.append(e.exact_ref(head, revision))
        resolution_id = e.review("window_resolution", e.exact_ref(e.target, e.target_revision),
                                 {"summary": params["summary"], "dispositions": params["dispositions"],
                                  "remaining_differences": params["remaining_differences"], "candidate_target_refs": targets},
                                 window_id=_oid(e.target))
        window = e._m1b["window"]
        payload = {"title": params["title"], "window_ref": e.exact_ref(e.target, e.target_revision),
                   "strategy_ref": window["strategy_ref"], "ltco_ref": window["ltco_ref"], "target_refs": targets,
                   "resolution_record_id": resolution_id, "dispositions": params["dispositions"],
                   "remaining_differences": params["remaining_differences"]}
        CandidateSetPayload.model_validate(payload)
        head, revision = _created(e, "CandidateSet", payload, phase="pending")
        candidate_ref = e.exact_ref(head, revision)
        state = e._m1b["state"]
        state.update(phase="resolved", candidate_ref=candidate_ref)
        e.set_state(e.target, state)
        e.transition(e.target)
        for ref in targets:
            obj, _ = e.ref(ref, types={"PCO", "Mission"})
            member_state = deepcopy(e.state(obj))
            member_state.update(phase="candidate", candidate_ref=candidate_ref)
            e.set_state(obj, member_state)
        return _result(e, head, revision, phase="pending", target_refs=targets, resolution_record_id=resolution_id)
    if kind == "m1b_confirm_candidates":
        candidate_ref = e.exact_ref(e.target, e.target_revision)
        review_id = e.review("candidate_set_confirmation", candidate_ref,
                             {"reason": params["reason"], "target_refs": _payload(e.target_revision)["target_refs"]},
                             window_id=_oid(e._m1b["window_head"]))
        for head, _ in [e._m1b["pco"], *e._m1b["missions"]]:
            state = deepcopy(e.state(head))
            state.update(phase="confirmed", confirmation_record_id=review_id,
                         confirmed_candidate_ref=candidate_ref)
            state.pop("window_id", None)
            e.set_state(head, state)
            e.transition(head, status="confirmed", effective=True)
        state = e._m1b["state"]
        state.update(phase="confirmed", confirmation_record_id=review_id)
        e.set_state(e.target, state)
        e.transition(e.target, status="confirmed", effective=True)
        window = e._m1b["window_head"]
        window_state = e._m1b["window_state"]
        window_state.update(phase="confirmed", confirmation_record_id=review_id)
        e.set_state(window, window_state)
        e.transition(window)
        return _result(e, e.target, e.target_revision, phase="confirmed", review_record_id=review_id,
                       target_refs=_payload(e.target_revision)["target_refs"],
                       execution_authority_created=False)
    if kind in {"m1b_reopen_candidates", "m1b_reopen_window"}:
        return _reopen_run(e)
    _fail("ACTION_NOT_SUPPORTED_FOR_PROTOCOL", "Unsupported M1B action.")
