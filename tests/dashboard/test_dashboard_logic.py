"""分组/basis/正式状态/投影回归（无数据库）。

真实验收另见 acceptance/dashboard_0_3；本文件只固定契约级行为：
精确 revision 遍历、分页 lookahead、hash 一致性、独立域隔离、确认来源与
历史 revision 不被当前状态借用。
"""
from __future__ import annotations

import pytest

from memory_service_runtime.governed import dashboard
from memory_service_runtime.governed.errors import GovernedError
from memory_service_runtime.governed.workbench import encode_cursor

from dashboard_fakes import CTX, Result, head, revision, ts, uid


SELECTED = {"strategy_id": uid(100), "revision_id": uid(101), "payload_hash": "f" * 64,
            "domain_id": uid(1), "title": "S1", "contract_version": "tkos.method/0.3"}


def _ref(oid, rid, digest="a"):
    return {"object_id": oid, "revision_id": rid, "payload_hash": digest * 64}


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_same_ref_requires_both_object_and_revision():
    left = {"object_id": uid(1), "revision_id": uid(2), "payload_hash": "a" * 64}
    right = {"object_id": uid(1), "revision_id": uid(2), "payload_hash": "b" * 64}
    assert dashboard._same_ref(left, right)
    assert not dashboard._same_ref(left, {"object_id": uid(1), "revision_id": uid(3)})
    assert not dashboard._same_ref(left, {"object_id": uid(1)})
    assert not dashboard._same_ref(left, None)


def test_basis_matches_separates_current_historical_unattached_unrelated():
    assert dashboard._basis_matches({"status": "current"}, "current")
    assert internal_status_ok("historical", "historical")
    assert dashboard._basis_matches({"status": "mixed"}, "historical")
    assert not dashboard._basis_matches({"status": "mixed"}, "current")
    assert dashboard._basis_matches({"status": "unattached"}, "unattached")
    assert not dashboard._basis_matches({"status": "unrelated"}, "historical")
    assert not dashboard._basis_matches({"status": "unrelated"}, "current")
    combined = ["current", "historical", "mixed", "unattached", "unavailable"]
    for status in combined:
        assert dashboard._basis_matches({"status": status}, "all")
    # An independent Strategy domain must never leak into a selected Strategy's
    # combined list.
    assert not dashboard._basis_matches({"status": "unrelated"}, "all")


def internal_status_ok(status, requested):
    return dashboard._basis_matches({"status": status}, requested)


def test_period_overlap_is_inclusive():
    period = {"start": "2026-09-01T00:00:00+00:00", "end": "2026-10-01T00:00:00+00:00"}
    assert dashboard._period_overlap(period, "2026-09-15T00:00:00+00:00", "2026-09-16T00:00:00+00:00")
    assert dashboard._period_overlap(period, None, "2026-10-01T00:00:00+00:00")
    assert dashboard._period_overlap(period, "2026-10-01T00:00:00+00:00", None)
    assert not dashboard._period_overlap(period, "2026-10-02T00:00:00+00:00", None)
    assert not dashboard._period_overlap(period, None, "2026-08-31T00:00:00+00:00")


def test_owner_filter_accepts_assignment_or_principal_id():
    entries = [{"assignment_id": uid(5), "principal": {"principal_id": uid(6)}}]
    assert dashboard._owner_matches(entries, uid(5))
    assert dashboard._owner_matches(entries, uid(6))
    assert not dashboard._owner_matches(entries, uid(7))
    assert dashboard._owner_matches(entries, None)


def test_scope_filter_uses_recorded_primary_scope_and_supports_outcomes():
    mission = {"primary_scope_id": "unit-pilot"}
    assert dashboard._scope_matches(mission, "unit-pilot")
    assert not dashboard._scope_matches(mission, "unit-other")
    review = {"target_refs": []}
    assert dashboard._scope_matches(review, None)
    assert not dashboard._scope_matches(review, "unit-pilot")


def test_topic_only_subject_is_unattached_without_resolving_a_target():
    basis = dashboard._basis_of_exact_ref(None, CTX, {"topic": "company cash"}, SELECTED,
                                          visited=frozenset(), impact_linked=False)
    assert basis["status"] == "unattached"
    assert basis["reason"] == "topic_only_subject"


def test_tree_refs_only_returns_exact_references():
    payload = {"strategy_ref": _ref(uid(1), uid(2)),
               "units": [{"unit_id": "u1", "owner_principal_id": uid(3)}],
               "nested": {"list": [{"object_id": uid(4), "revision_id": uid(5)}]}}
    keys = {(ref["object_id"], ref["revision_id"]) for _path, ref in dashboard._tree_refs(payload)}
    assert (uid(1), uid(2)) in keys
    assert (uid(4), uid(5)) in keys


def test_unreadable_reference_is_omitted_without_a_hidden_marker(monkeypatch):
    monkeypatch.setattr(dashboard, "_try_ref", lambda conn, ctx, ref: None)
    assert dashboard._resolve_refs(None, CTX, [("/a", _ref(uid(1), uid(2)))]) == []


def test_hash_inconsistent_reference_is_never_usable(monkeypatch):
    oid, rid = uid(1), uid(2)
    monkeypatch.setattr(dashboard, "_visible_head", lambda c, x, o: (head(o, "Mission", latest=rid, effective=rid), None))
    monkeypatch.setattr(dashboard, "_visible_revision", lambda c, x, o, r, a: revision(o, r, {"title": "t"}))
    assert dashboard._try_ref(None, CTX, _ref(oid, rid, "b")) is None
    assert dashboard._try_ref(None, CTX, _ref(oid, rid, "a")) is not None


def test_infrastructure_error_is_not_degraded_to_missing(monkeypatch):
    def broken(conn, ctx, oid):
        raise GovernedError("PROTOCOL_BINDING_CONFLICT")
    monkeypatch.setattr(dashboard, "_visible_head", broken)
    with pytest.raises(GovernedError) as exc:
        dashboard._try_ref(None, CTX, _ref(uid(1), uid(2)))
    assert exc.value.code == "PROTOCOL_BINDING_CONFLICT"


def test_normalised_business_keeps_recorded_fields():
    payload = {"title": "Pilot mission", "pco_ref": _ref(uid(1), uid(2)),
               "deliverable": "Report", "boundary": "No execution authority",
               "acceptance_criteria": ["Original sources attached"],
               "hard_deadline": "2026-10-14T00:00:00+00:00",
               "supports": [{"outcome_ref": {**_ref(uid(1), uid(2)), "outcome_id": "outcome-pilot"},
                             "contribution": "Collect evidence"}]}
    business = dashboard._normalised_business("Mission", payload)
    assert business["deliverable"] == "Report"
    assert business["deadline"] == {"kind": "hard", "value": "2026-10-14T00:00:00+00:00"}
    assert business["supports"][0]["outcome_id"] == "outcome-pilot"
    assert business["raw"] == payload


# --------------------------------------------------------------------------
# exact-reference traversal
# --------------------------------------------------------------------------

class RefConn:
    """gov_method_impacts 只回答“无记录”。"""

    def execute(self, sql, params=()):
        if "gov_method_impacts" in sql:
            return Result([])
        raise AssertionError(f"unexpected SQL: {sql}")


def test_mission_keeps_the_exact_pco_revision_after_pco_head_moves(monkeypatch):
    mission_oid, mission_rid = uid(10), uid(11)
    pco_oid, pco_v1, pco_v2 = uid(20), uid(21), uid(22)
    strategy_old = _ref(uid(30), uid(31))
    payload = {"title": "M", "pco_ref": _ref(pco_oid, pco_v1)}
    pco_head_v2 = head(pco_oid, "PCO", latest=pco_v2, effective=pco_v2)
    pco_rev_v1 = revision(pco_oid, pco_v1, {"title": "PCO v1", "strategy_ref": strategy_old})

    def load(conn, ctx, ref):
        if ref is None:
            return None
        if str(ref["object_id"]) == pco_oid and str(ref["revision_id"]) == pco_v1:
            return pco_head_v2, pco_rev_v1
        if str(ref["object_id"]) == str(strategy_old["object_id"]):
            return head(strategy_old["object_id"], "Strategy", domain_id=uid(1),
                        latest=strategy_old["revision_id"], effective=strategy_old["revision_id"]), \
                revision(strategy_old["object_id"], strategy_old["revision_id"], {"title": "old"})
        raise AssertionError(ref)

    monkeypatch.setattr(dashboard, "_load_ref", load)
    basis = dashboard._basis_of_exact_ref(None, CTX, payload["pco_ref"], SELECTED, visited=frozenset(), impact_linked=False)
    assert basis["status"] == "historical"
    assert basis["strategy_ref"]["revision_id"] == strategy_old["revision_id"]
    assert basis["basis_revision"] == "exact_ref"


def test_cycle_in_exact_references_is_bounded(monkeypatch):
    a_oid, a_rid, b_oid, b_rid = uid(40), uid(41), uid(42), uid(43)
    ref_a = _ref(a_oid, a_rid)
    ref_b = _ref(b_oid, b_rid)
    heads = {a_oid: head(a_oid, "OperatingState", latest=a_rid, effective=a_rid),
             b_oid: head(b_oid, "OperatingState", latest=b_rid, effective=b_rid)}
    revs = {a_rid: revision(a_oid, a_rid, {"subject_ref": ref_b}),
            b_rid: revision(b_oid, b_rid, {"subject_ref": ref_a})}

    def load(conn, ctx, ref):
        oid, rid = str(ref["object_id"]), str(ref["revision_id"])
        return (heads[oid], revs[rid]) if oid in heads and rid in revs else None

    monkeypatch.setattr(dashboard, "_load_ref", load)
    basis = dashboard._basis_of_exact_ref(None, CTX, ref_a, SELECTED, visited=frozenset(), impact_linked=False)
    assert basis["status"] == "unavailable"
    assert basis["reason"] == "basis_cycle"


def test_another_domain_strategy_is_unrelated_not_historical(monkeypatch):
    other = _ref(uid(200), uid(201))
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: (
        head(str(ref["object_id"]), "Strategy", domain_id=uid(9),
             latest=str(ref["revision_id"]), effective=str(ref["revision_id"])),
        revision(str(ref["object_id"]), str(ref["revision_id"]), {"title": "other domain"})))
    basis = dashboard._compare_strategy_ref(None, CTX, other, SELECTED)
    assert basis["status"] == "unrelated"
    assert basis["reason"] == "different_strategy_domain"
    linked = dashboard._compare_strategy_ref(None, CTX, other, SELECTED, impact_linked=True)
    assert linked["status"] == "historical"
    assert linked["impact_linked"] is True


def test_same_domain_previous_strategy_is_historical(monkeypatch):
    other = _ref(uid(200), uid(201))
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: (
        head(str(ref["object_id"]), "Strategy", domain_id=uid(1),
             latest=str(ref["revision_id"]), effective=str(ref["revision_id"])),
        revision(str(ref["object_id"]), str(ref["revision_id"]), {"title": "same domain"})))
    basis = dashboard._compare_strategy_ref(None, CTX, other, SELECTED)
    assert basis["status"] == "historical"
    assert basis["reason"] == "recorded_other_strategy"


# --------------------------------------------------------------------------
# formal state / confirmation provenance
# --------------------------------------------------------------------------

def _confirmation(record_id="rec-1", *, kind="candidate_set_confirmation", covers=True,
                  principal=uid(1001), target=uid(900), target_revision=uid(901), content=None):
    return {"record_id": record_id, "kind": kind, "principal_id": principal,
            "recorded_at": ts(5), "target_ref": {"object_id": target, "revision_id": target_revision},
            "target_revision_id": target_revision, "covered_refs": [],
            "covers_selected_revision": covers, "applies_to_selected_revision": covers,
            "human_confirmation": kind in dashboard.CONFIRMATION_REVIEW_KINDS,
            "source": "test", "content": content or {}, "receipt": None}


def test_confirmed_content_stays_formal_when_a_later_phase_changes():
    oid, rid = uid(300), uid(301)
    conn = StateConn({oid: {"phase": "draft"}})  # a later revise changed the mutable phase
    confirmations = [_confirmation()]
    formal = dashboard._formal_state(conn, CTX, head(oid, "Mission", latest=rid, effective=rid),
                                     revision(oid, rid, {"title": "M"}), confirmations)
    assert formal["formal"] is True
    assert formal["status"] == "confirmed"
    assert formal["confirmation_record_id"] == "rec-1"
    # A revision that is not covered must not borrow the confirmation.
    other = uid(302)
    formal = dashboard._formal_state(conn, CTX, head(oid, "Mission", latest=other, effective=rid),
                                     revision(oid, other, {"title": "M2"}),
                                     [_confirmation(covers=False)])
    assert formal["formal"] is False
    assert formal["status"] == "draft"


def test_operating_state_historical_confirmation_is_superseded_not_current():
    oid, rid, newer = uid(310), uid(311), uid(312)
    conn = StateConn({oid: {"phase": "confirmed",
                            "canonical_ref": {"object_id": oid, "revision_id": newer},
                            "recommendation_ref": {"object_id": oid, "revision_id": newer}}})
    formal = dashboard._formal_state(conn, CTX, head(oid, "OperatingState", latest=newer, effective=newer),
                                     revision(oid, rid, {"rag": "green"}),
                                     [_confirmation(kind="state_confirmation")])
    assert formal["formal"] is False
    assert formal["status"] == "superseded_confirmed"


def test_problem_closure_belongs_to_the_exact_revision():
    oid, rid, newer = uid(320), uid(321), uid(322)
    conn = StateConn({oid: {"phase": "open", "tracking": True}})
    closure = _confirmation(kind="problem_closure", covers=True,
                            content={"disposition": "no_further_action", "reason": "checked"})
    formal = dashboard._formal_state(conn, CTX, head(oid, "OperatingProblem", latest=newer, effective=newer),
                                     revision(oid, rid, {"core_question": "q"}), [closure])
    assert formal["status"] == "no_further_action"
    assert formal["formal"] is False
    # The newer open revision still has its own current tracking.
    formal = dashboard._formal_state(conn, CTX, head(oid, "OperatingProblem", latest=newer, effective=newer),
                                     revision(oid, newer, {"core_question": "q"}), [])
    assert formal["status"] == "open"
    assert formal["formal"] is True


def test_transferred_problem_is_not_formal_or_solved_without_closure():
    oid, rid = uid(330), uid(331)
    conn = StateConn({oid: {"phase": "transferred", "tracking": False}})
    formal = dashboard._formal_state(conn, CTX, head(oid, "OperatingProblem", latest=rid, effective=rid),
                                     revision(oid, rid, {"core_question": "q"}), [])
    assert formal["status"] == "transferred"
    assert formal["formal"] is False
    assert "not solved" in formal["note"]


def test_period_review_never_becomes_human_approved():
    oid, rid = uid(340), uid(341)
    conn = StateConn({})
    formal = dashboard._formal_state(conn, CTX, head(oid, "PeriodReview", latest=rid, effective=rid),
                                     revision(oid, rid, {"title": "Review"}), [])
    assert formal["human_approved"] is False
    assert formal["authority"] == "agent_analysis"


class StateConn:
    """只响应 gov_method_state / gov_method_strategy_heads 的最小连接。"""

    def __init__(self, states=None, strategy_head=None):
        self.states = states or {}
        self.strategy_head = strategy_head

    def execute(self, sql, params=()):
        if "SELECT state FROM gov_method_state" in sql:
            state = self.states.get(str(params[1]))
            return Result([{"state": state}] if state is not None else [])
        if "FROM gov_method_strategy_heads" in sql and "h.recorded_at" not in sql:
            if "SELECT object_id, revision_id FROM gov_method_strategy_heads" in sql:
                if not self.strategy_head:
                    return Result([])
                return Result([self.strategy_head])
            return Result([])
        raise AssertionError(f"unexpected SQL: {sql}")


# --------------------------------------------------------------------------
# candidate-set confirmation provenance (covers another object)
# --------------------------------------------------------------------------

class ProvenanceConn:
    """gov_method_reviews 以候选集合为目标；action receipt 查询返回空。"""

    def __init__(self, state, candidate_rows):
        self.state = state
        self.candidate_rows = candidate_rows

    def execute(self, sql, params=()):
        if "SELECT state FROM gov_method_state" in sql:
            return Result([{"state": self.state}])
        if "FROM gov_method_reviews" in sql and "record_id=%s" in sql:
            return Result([row for row in self.candidate_rows if str(row["record_id"]) == str(params[1])])
        if "FROM gov_method_reviews" in sql:
            return Result(self.candidate_rows)
        if "SELECT * FROM gov_action_receipts" in sql:
            return Result([])
        if "FROM gov_principals" in sql:
            return Result([{"principal_id": uid(1001), "principal_type": "human",
                            "display_name": "确认人", "active": True}])
        raise AssertionError(f"unexpected SQL: {sql}")


def test_confirmation_follows_confirmed_candidate_ref_and_covers_member_revision(monkeypatch):
    oid, rid = uid(400), uid(401)
    candidate_oid, candidate_rid = uid(410), uid(411)
    row = {"record_id": uid(420), "kind": "candidate_set_confirmation", "principal_id": uid(1001),
           "recorded_at": ts(6), "target_object_id": candidate_oid, "target_revision_id": candidate_rid,
           "content": {"reason": "CEO confirmed", "target_refs": [{"object_id": oid, "revision_id": rid,
                                                                   "payload_hash": "a" * 64}]},
           "action_id": uid(430)}
    conn = ProvenanceConn({"phase": "confirmed",
                           "confirmed_candidate_ref": {"object_id": candidate_oid,
                                                       "revision_id": candidate_rid},
                           "confirmation_record_id": None}, [row])
    monkeypatch.setattr(dashboard.method_readers, "review_records", lambda c, x, o: {"items": []})
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: None)
    selected = head(oid, "Mission", latest=rid, effective=rid)
    confirmations = dashboard._covering_confirmations(conn, CTX, selected, revision(oid, rid, {}))
    # The candidate set itself is not readable, so a uniform omission happens:
    # no record is presented without an authorized exact target.
    assert confirmations == []

    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: (
        head(candidate_oid, "CandidateSet", latest=candidate_rid, effective=candidate_rid),
        revision(str(ref["object_id"]), str(ref["revision_id"]), {"title": "Candidate"})))
    confirmations = dashboard._covering_confirmations(conn, CTX, selected, revision(oid, rid, {}))
    assert len(confirmations) == 1
    record = confirmations[0]
    assert record["covers_selected_revision"] is True
    assert record["source"] == "confirmed_candidate_set"
    assert record["human_confirmation"] is True
    formal = dashboard._formal_state(conn, CTX, selected, revision(oid, rid, {}), confirmations)
    assert formal["formal"] is True


# --------------------------------------------------------------------------
# pagination: cursor after the last emitted item, hidden rows included
# --------------------------------------------------------------------------

class PaginationConn:
    def execute(self, sql, params=()):
        if "clock_timestamp()" in sql:
            return Result([{"now": ts(9)}])
        raise AssertionError(f"unexpected SQL: {sql}")


def _pagination_env(monkeypatch, objects):
    """objects: list of (object_id, hidden, basis_status) in ascending id order."""
    ordered = [{"object_id": oid, "domain_id": uid(1), "object_type": "Mission",
                "lifecycle_status": "active", "object_version": 1,
                "latest_revision_id": uid(900 + index), "effective_revision_id": None,
                "created_at": ts(1)}
               for index, (oid, _hidden, _status) in enumerate(objects)]
    hidden = {str(oid) for oid, is_hidden, _status in objects if is_hidden}
    status_by_id = {str(oid): status for oid, _hidden, status in objects}

    def iter_candidates(conn, ctx, types, after, need, domain_id):
        rows = ordered
        if after is not None:
            rows = [row for row in rows
                    if (row["created_at"], str(row["object_id"])) > (after[0], str(after[1]))]
        return rows[:need]

    monkeypatch.setattr(dashboard, "_iter_candidates", iter_candidates)

    def visible(conn, ctx, oid):
        if str(oid) in hidden:
            raise GovernedError("NOT_FOUND")
        row = next(row for row in ordered if str(row["object_id"]) == str(oid))
        return row, None

    monkeypatch.setattr(dashboard, "_visible_head", visible)
    monkeypatch.setattr(dashboard, "_visible_revision",
                        lambda c, x, o, r, a: revision(o, r, {"title": "t"}))

    def basis(conn, ctx, head_value, revision_value, selected, *, visited, impact_linked=None):
        return {"status": status_by_id[str(head_value["object_id"])], "reason": None,
                "selected_strategy_ref": None, "strategy_ref": None,
                "impact_linked": False, "basis_revision": "latest"}

    monkeypatch.setattr(dashboard, "_basis_of_revision", basis)
    monkeypatch.setattr(dashboard, "_covering_confirmations", lambda c, x, h, r: [])
    monkeypatch.setattr(dashboard, "_list_item",
                        lambda c, x, h, s, r, b, conf: {"object_id": h["object_id"],
                                                        "object_type": h["object_type"]})
    monkeypatch.setattr(dashboard, "_read_at", lambda conn: "now")
    monkeypatch.setattr(dashboard, "select_strategy", lambda conn, ctx, sid: SELECTED)


def test_pagination_emits_each_readable_object_exactly_once(monkeypatch):
    a, b, c, d = uid(1), uid(2), uid(3), uid(4)
    # uid(2) is hidden, uid(4) belongs to another Strategy basis and is filtered.
    _pagination_env(monkeypatch, [(a, False, "current"), (b, True, "current"),
                                   (c, False, "current"), (d, False, "unrelated")])
    conn = PaginationConn()
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = dashboard.objects(conn, CTX, group="mission", limit=1, cursor=cursor)
        seen.extend(item["object_id"] for item in page["items"])
        assert page["loaded_count"] == len(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [a, c]
    assert cursor is None


def test_pagination_cursor_binds_the_strategy_revision(monkeypatch):
    _pagination_env(monkeypatch, [(uid(1), False, "current"), (uid(2), False, "current")])
    conn = PaginationConn()
    page = dashboard.objects(conn, CTX, group="mission", limit=1)
    assert page["has_more"] is True
    changed = {**SELECTED, "revision_id": uid(999)}
    monkeypatch.setattr(dashboard, "select_strategy", lambda conn, ctx, sid: changed)
    with pytest.raises(GovernedError) as exc:
        dashboard.objects(conn, CTX, group="mission", limit=1, cursor=page["next_cursor"])
    assert exc.value.status == 422


# --------------------------------------------------------------------------
# owner appointments
# --------------------------------------------------------------------------

class AppointmentConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=()):
        if "FROM gov_role_assignments" in sql and "principal_id=%s" in sql:
            return Result(self.rows)
        raise AssertionError(f"unexpected SQL: {sql}")


def _appointment_env(monkeypatch, rows, *, readable=True):
    monkeypatch.setattr(dashboard, "_domain_readable", lambda conn, ctx, domain_id: readable)
    return AppointmentConn(rows)


def test_owner_appointment_distinguishes_current_future_expired_and_revoked(monkeypatch):
    principal = uid(500)
    current = {"assignment_id": uid(501), "principal_id": principal, "domain_id": uid(1),
               "role": "DOMAIN_DRI", "active": True, "valid_from": ts(1), "valid_to": None,
               "current": True, "future": False, "display_name": "p", "principal_type": "human"}
    conn = _appointment_env(monkeypatch, [current])
    appointment = dashboard._appointment(conn, CTX, principal, uid(1))
    assert appointment["status"] == "current"
    assert appointment["assignments"][0]["role"] == "DOMAIN_DRI"
    assert dashboard._appointment(_appointment_env(monkeypatch, []), CTX, principal,
                                  uid(1))["status"] == "not_recorded"
    expired = {**current, "valid_to": ts(1).replace(year=2020), "current": False, "active": True}
    assert dashboard._appointment(_appointment_env(monkeypatch, [expired]), CTX, principal,
                                  uid(1))["status"] == "expired"
    # A future valid_from is not a revocation.
    future = {**current, "valid_from": "2099-01-01T00:00:00+00:00", "current": False, "future": True}
    assert dashboard._appointment(_appointment_env(monkeypatch, [future]), CTX, principal,
                                  uid(1))["status"] == "future"
    revoked = {**current, "active": False, "current": False, "future": False}
    assert dashboard._appointment(_appointment_env(monkeypatch, [revoked]), CTX, principal,
                                  uid(1))["status"] == "revoked"


def test_mission_owner_keeps_visible_child_domain_appointment(monkeypatch):
    principal = uid(510)
    child_domain = uid(7)
    row = {"assignment_id": uid(511), "principal_id": principal, "domain_id": child_domain,
           "role": "DOMAIN_DRI", "active": True, "valid_from": ts(1), "valid_to": None,
           "current": True, "future": False, "display_name": "owner", "principal_type": "human"}
    conn = _appointment_env(monkeypatch, [row])
    # The Mission is stored in the company domain (uid(1)) while the owner's
    # legitimate appointment lives in a child business domain.
    appointment = dashboard._appointment(conn, CTX, principal, uid(1), all_domains=True)
    assert appointment["status"] == "current"
    assert appointment["assignments"][0]["domain_id"] == child_domain
    monkeypatch.setattr(dashboard, "_principal", lambda c, x, pid: {
        "principal_id": principal, "display_name": "owner", "principal_type": "human", "active": True})
    monkeypatch.setattr(dashboard, "_assignment", lambda c, x, aid: None)
    monkeypatch.setattr(dashboard, "_unit_domains", lambda c, x, payload: {})
    entries = dashboard._responsibility_entries(conn, CTX, head(uid(520), "Mission", domain_id=uid(1)),
                                                {"owner_principal_id": principal})
    owner = entries[0]
    assert owner["relation"] == "mission_owner"
    assert owner["appointment"]["status"] == "current"
    # Owner is still never reported as a DRI.
    assert dashboard._dri_entries(entries) == []


def test_pco_outcome_dri_uses_the_exact_unit_definition_domain(monkeypatch):
    principal = uid(530)
    unit_domain = uid(8)
    row = {"assignment_id": uid(531), "principal_id": principal, "domain_id": unit_domain,
           "role": "DOMAIN_DRI", "active": True, "valid_from": ts(1), "valid_to": None,
           "current": True, "future": False, "display_name": "dri", "principal_type": "human"}
    queried: list[str] = []

    class PcoConn(AppointmentConn):
        def execute(self, sql, params=()):
            if "FROM gov_role_assignments" in sql:
                queried.append(str(params[2]))
            return super().execute(sql, params)

    monkeypatch.setattr(dashboard, "_domain_readable", lambda c, x, domain_id: True)
    monkeypatch.setattr(dashboard, "_principal", lambda c, x, pid: {
        "principal_id": principal, "display_name": "dri", "principal_type": "human", "active": True})
    monkeypatch.setattr(dashboard, "_assignment", lambda c, x, aid: None)
    monkeypatch.setattr(dashboard, "_unit_domains", lambda c, x, payload: {"unit-pilot": unit_domain})
    conn = PcoConn([row])
    payload = {"unit_outcomes": [{"outcome_id": "o1", "unit_id": "unit-pilot",
                                  "dri_principal_id": principal}]}
    entries = dashboard._responsibility_entries(conn, CTX, head(uid(540), "PCO", domain_id=uid(1)), payload)
    assert entries[0]["appointment"]["status"] == "current"
    assert queried == [unit_domain]


def test_appointment_hidden_domains_are_not_exposed(monkeypatch):
    principal = uid(550)
    row = {"assignment_id": uid(551), "principal_id": principal, "domain_id": uid(9),
           "role": "DOMAIN_DRI", "active": True, "valid_from": ts(1), "valid_to": None,
           "current": True, "future": False, "display_name": "p", "principal_type": "human"}
    conn = _appointment_env(monkeypatch, [row], readable=False)
    appointment = dashboard._appointment(conn, CTX, principal, uid(9))
    assert appointment["status"] == "not_visible"
    assert appointment["assignments"] == []
    assert "domain_id" not in str(appointment)


# --------------------------------------------------------------------------
# query validation and cursor binding
# --------------------------------------------------------------------------

def test_objects_rejects_unknown_group_before_any_database_use():
    with pytest.raises(GovernedError) as exc:
        dashboard.objects(None, CTX, group="unknown")
    assert exc.value.status == 422
    with pytest.raises(GovernedError):
        dashboard.objects(None, CTX, group="mission", object_type="PCO")
    with pytest.raises(GovernedError):
        dashboard.objects(None, CTX, group="mission", basis="strange")


def test_cursor_is_bound_to_principal_scope_and_filters():
    filters = {"group": "mission", "object_type": None, "strategy_id": uid(1),
               "strategy_revision_id": uid(2), "basis": "current",
               "domain_id": None, "period_from": None, "period_to": None, "owner_id": None,
               "scope_id": None}
    cursor = encode_cursor("dashboard-objects", CTX, filters, ["2026-09-01T00:00:00+00:00", uid(9)])
    assert dashboard._cursor_key(cursor, CTX, filters) == \
        [dashboard._time("2026-09-01T00:00:00+00:00"), uid(9)]
    with pytest.raises(GovernedError) as exc:
        dashboard._cursor_key(cursor, CTX, {**filters, "strategy_revision_id": uid(3)})
    assert exc.value.status == 422
    with pytest.raises(GovernedError):
        dashboard._cursor_key("%%%not-base64%%%", CTX, filters)


def test_downstream_cursor_binds_object_and_revision():
    filters = {"object_id": uid(1), "revision_id": uid(2)}
    cursor = encode_cursor("dashboard-downstream", CTX, filters, ["Mission", uid(3), uid(4)])
    assert dashboard._downstream_cursor_key(cursor, CTX, filters) == ["Mission", uid(3), uid(4)]
    with pytest.raises(GovernedError):
        dashboard._downstream_cursor_key(cursor, CTX, {"object_id": uid(1), "revision_id": uid(5)})
    with pytest.raises(GovernedError):
        dashboard._downstream_cursor_key("not-a-cursor", CTX, filters)


def test_downstream_hash_inconsistency_is_not_a_usable_edge(monkeypatch):
    target = {"object_id": uid(1), "revision_id": uid(2)}
    recorded = {"strategy_ref": {"object_id": uid(1), "revision_id": uid(2), "payload_hash": "b" * 64}}
    refs = dashboard._refs_in_payload(recorded, "strategy_ref", target)
    assert len(refs) == 1
    assert str(refs[0]["payload_hash"]) != "a" * 64
    matching = {"strategy_ref": {"object_id": uid(1), "revision_id": uid(2), "payload_hash": "a" * 64}}
    refs = dashboard._refs_in_payload(matching, "strategy_ref", target)
    assert str(refs[0]["payload_hash"]) == "a" * 64


# --------------------------------------------------------------------------
# downstream behavior: parent and child hashes deliberately differ
# --------------------------------------------------------------------------

def _downstream_env(monkeypatch, *, recorded_hash, target_hash, child_hash):
    target_oid, target_rid = uid(600), uid(601)
    child_oid, child_rid = uid(610), uid(611)
    target_head = head(target_oid, "LTCO", latest=target_rid, effective=target_rid)
    target_rev = revision(target_oid, target_rid, {"title": "LTCO"})
    target_rev["payload_hash"] = target_hash
    child_head = head(child_oid, "PCO", latest=child_rid, effective=child_rid)
    child_payload = {"title": "PCO", "ltco_ref": {"object_id": target_oid,
                                                  "revision_id": target_rid,
                                                  "payload_hash": recorded_hash}}
    child_rev = revision(child_oid, child_rid, child_payload)
    child_rev["payload_hash"] = child_hash

    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: (
        (target_head, target_rev)
        if str(ref.get("object_id")) == target_oid else None))
    monkeypatch.setattr(dashboard, "_downstream_unions", lambda c, x, ref, after, need: [
        {"object_type": "PCO", "object_id": child_oid, "revision_id": child_rid,
         "payload_hash": child_hash}])
    monkeypatch.setattr(dashboard, "_visible_head", lambda c, x, oid: (child_head, None))
    monkeypatch.setattr(dashboard, "_visible_revision", lambda c, x, oid, rid, allowed: child_rev)
    monkeypatch.setattr(dashboard, "_covering_confirmations", lambda c, x, h, r: [])
    monkeypatch.setattr(dashboard, "_formal_state", lambda c, x, h, r, conf: {"status": "draft"})
    return {"object_id": target_oid, "revision_id": target_rid}, target_hash, child_hash


def test_downstream_matches_target_hash_not_child_hash(monkeypatch):
    selected, target_hash, child_hash = _downstream_env(
        monkeypatch, recorded_hash="t" * 64, target_hash="t" * 64, child_hash="c" * 64)
    assert target_hash != child_hash  # the two hashes are deliberately different
    page = dashboard.downstream(None, CTX, selected)
    assert page["has_more"] is False
    assert len(page["items"]) == 1
    assert page["items"][0]["matched_fields"] == ["ltco_ref"]
    assert page["items"][0]["object_id"] == uid(610)


def test_downstream_rejects_recorded_ref_pointing_at_another_revision(monkeypatch):
    selected, target_hash, child_hash = _downstream_env(
        monkeypatch, recorded_hash="s" * 64, target_hash="t" * 64, child_hash="c" * 64)
    page = dashboard.downstream(None, CTX, selected)
    assert page["items"] == []
    assert page["next_cursor"] is None


def test_downstream_requires_a_readable_exact_target(monkeypatch):
    selected, _target_hash, _child_hash = _downstream_env(
        monkeypatch, recorded_hash="t" * 64, target_hash="t" * 64, child_hash="c" * 64)
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: None)
    with pytest.raises(GovernedError) as exc:
        dashboard.downstream(None, CTX, selected)
    assert exc.value.code == "NOT_FOUND"


# --------------------------------------------------------------------------
# Mission business period comes from the exact recorded PCO revision
# --------------------------------------------------------------------------

def test_mission_period_comes_from_the_exact_pco_revision(monkeypatch):
    pco_oid, pco_v1, pco_v2 = uid(700), uid(701), uid(702)
    pco_period = {"start": "2026-09-01T00:00:00+00:00", "end": "2026-10-01T00:00:00+00:00"}
    mission = revision(uid(710), uid(711), {"title": "M", "pco_ref": _ref(pco_oid, pco_v1),
                                            "hard_deadline": "2026-12-31T00:00:00+00:00"})
    pco_v1_rev = revision(pco_oid, pco_v1, {"title": "PCO v1", "period": pco_period})
    pco_v2_rev = revision(pco_oid, pco_v2, {"title": "PCO v2",
                                            "period": {"start": "2026-11-01T00:00:00+00:00",
                                                       "end": "2026-12-01T00:00:00+00:00"}})
    loads = {}

    def load(conn, ctx, ref):
        oid, rid = str(ref["object_id"]), str(ref["revision_id"])
        if oid == pco_oid and rid == pco_v1:
            return head(pco_oid, "PCO", latest=pco_v2, effective=pco_v2), pco_v1_rev
        if oid == pco_oid and rid == pco_v2:
            return head(pco_oid, "PCO", latest=pco_v2, effective=pco_v2), pco_v2_rev
        return None

    monkeypatch.setattr(dashboard, "_load_ref", load)
    period, source = dashboard._derived_period(None, CTX, head(uid(710), "Mission", latest=uid(711)), mission)
    assert period == pco_period  # the exact PCO v1 period, not the PCO head period
    assert source == "pco_ref"
    # hard_deadline is not a business period.
    assert period["end"] != mission["payload"]["hard_deadline"]
    # An unavailable PCO makes the period explicitly unavailable, not guessed.
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: None)
    period, source = dashboard._derived_period(None, CTX, head(uid(710), "Mission", latest=uid(711)), mission)
    assert period is None and source == "pco_ref_unavailable"


# --------------------------------------------------------------------------
# readable Outcome names/criteria behind supports[].outcome_ref
# --------------------------------------------------------------------------

def test_support_outcome_resolves_name_and_criteria(monkeypatch):
    pco_oid, pco_rid = uid(720), uid(721)
    outcome = {"outcome_id": "outcome-pilot", "unit_id": "unit-pilot", "title": "Pilot evidence",
               "result_statement": "Three pilots", "criteria": ["Three verified pilots"],
               "dri_principal_id": uid(1001)}
    ref = {**_ref(pco_oid, pco_rid), "outcome_id": "outcome-pilot"}
    payload = {"title": "M", "supports": [{"outcome_ref": ref, "contribution": "Collect"}]}
    revision_value = revision(pco_oid, pco_rid, {"unit_outcomes": [outcome]})
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, r: (
        (head(pco_oid, "PCO", latest=pco_rid, effective=pco_rid), revision_value)
        if str(r.get("object_id")) == pco_oid else None))
    business = dashboard._normalised_business("Mission", payload)
    enriched = dashboard._enrich_supports(None, CTX, business["supports"])
    assert enriched[0]["outcome"] == outcome
    assert enriched[0]["outcome_status"]["status"] == "available"
    # Unauthorized outcome revision: explicit missing, no fabricated name.
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, r: None)
    enriched = dashboard._enrich_supports(None, CTX, business["supports"])
    assert enriched[0]["outcome"] is None
    assert enriched[0]["outcome_status"]["reason"] == "outcome_revision_unavailable"


def test_subject_outcome_view_is_missing_for_topic_only_and_full_for_outcomes(monkeypatch):
    assert dashboard._subject_outcome_view(None, CTX, {"subject_ref": {"topic": "cash"}})["status"] == "missing"
    outcome = {"outcome_id": "o1", "title": "Outcome one", "criteria": ["c"]}
    ref = {**_ref(uid(730), uid(731)), "outcome_id": "o1"}
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, r: (
        head(uid(730), "PCO", latest=uid(731), effective=uid(731)),
        revision(uid(730), uid(731), {"unit_outcomes": [outcome]})))
    view = dashboard._subject_outcome_view(None, CTX, {"subject_ref": ref})
    assert view["value"]["outcome"] == outcome


# --------------------------------------------------------------------------
# downstream OperatingState panel separates state formal data from Mission confirmation
# --------------------------------------------------------------------------

def test_downstream_state_summary_exposes_exact_rag_and_subject(monkeypatch):
    mission_oid, mission_rid = uid(740), uid(741)
    state_oid, state_rid = uid(750), uid(751)
    subject = _ref(mission_oid, mission_rid)
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: (
        head(mission_oid, "Mission", latest=mission_rid, effective=mission_rid),
        revision(mission_oid, mission_rid, {"title": "M"})))
    state_rev = revision(state_oid, state_rid, {
        "subject_ref": subject, "as_of": ts(3).isoformat(), "summary": "S", "rag": "yellow",
        "baseline_refs": [subject], "evidence_refs": [], "data_gaps": ["gap"]})
    monkeypatch.setattr(dashboard, "_downstream_unions", lambda c, x, ref, after, need: [
        {"object_type": "OperatingState", "object_id": state_oid, "revision_id": state_rid,
         "payload_hash": "s" * 64}])
    monkeypatch.setattr(dashboard, "_visible_head", lambda c, x, oid: (
        head(oid, "OperatingState", latest=state_rid, effective=state_rid), None))
    monkeypatch.setattr(dashboard, "_visible_revision", lambda c, x, oid, rid, allowed: state_rev)
    monkeypatch.setattr(dashboard, "_covering_confirmations", lambda c, x, h, r: [])
    monkeypatch.setattr(dashboard, "_formal_state", lambda c, x, h, r, conf: {
        "status": "confirmed", "formal": True, "canonical_ref": _ref(state_oid, state_rid),
        "applies_to_ref": _ref(state_oid, state_rid), "confirmed_by": uid(1001)})
    page = dashboard.downstream(None, CTX, {"object_id": mission_oid, "revision_id": mission_rid})
    summary = page["items"][0]["state_summary"]
    assert summary["rag"] == "yellow"
    assert summary["as_of"] == ts(3).isoformat()
    assert summary["baseline_refs"] == [subject]
    assert summary["data_gaps"] == ["gap"]
    assert summary["subject_matches_selected_anchor"] is True
    assert summary["formal"] is True
    assert summary["applies_to_ref"]["revision_id"] == state_rid


def test_current_basis_requires_a_matching_recorded_payload_hash(monkeypatch):
    selected = {"strategy_id": uid(100), "revision_id": uid(101), "payload_hash": "a" * 64,
                "domain_id": uid(1)}
    matching = {"object_id": uid(100), "revision_id": uid(101), "payload_hash": "a" * 64}
    assert dashboard._compare_strategy_ref(None, CTX, matching, selected)["status"] == "current"
    no_hash = {"object_id": uid(100), "revision_id": uid(101)}
    assert dashboard._compare_strategy_ref(None, CTX, no_hash, selected)["status"] == "current"
    # Same coordinates but a recorded hash that does not name the immutable
    # revision must never be classified as the current basis.
    wrong = {"object_id": uid(100), "revision_id": uid(101), "payload_hash": "b" * 64}
    monkeypatch.setattr(dashboard, "_load_ref", lambda c, x, ref: None)
    basis = dashboard._compare_strategy_ref(None, CTX, wrong, selected)
    assert basis["status"] == "unavailable"
    assert basis["reason"] == "strategy_basis_hash_mismatch"


def test_list_responsibility_keeps_recorded_relations_and_drops_participants():
    entries = [
        {"relation": "mission_owner", "principal": {"principal_id": uid(1)}, "assignment_id": None},
        {"relation": "outcome_dri", "principal": {"principal_id": uid(2)}, "assignment_id": uid(20)},
        {"relation": "outcome_owner", "principal": {"principal_id": uid(3)}, "assignment_id": None},
        {"relation": "responsible", "principal": {"principal_id": uid(4)}, "assignment_id": uid(40)},
        {"relation": "participant", "principal": {"principal_id": uid(5)}, "assignment_id": None},
        {"relation": "strategy_unit_owner", "principal": {"principal_id": uid(6)}, "assignment_id": None},
    ]
    projected = dashboard._list_responsibility(entries)
    assert [entry["relation"] for entry in projected] == [
        "mission_owner", "outcome_dri", "outcome_owner", "responsible"]
    assert all(entry["relation"] != "participant" for entry in projected)


def test_owner_filter_matches_responsibility_but_not_participants():
    responsible = [{"relation": "outcome_dri", "assignment_id": uid(20),
                    "principal": {"principal_id": uid(2)}}]
    assert dashboard._owner_matches(responsible, uid(2))
    assert dashboard._owner_matches(responsible, uid(20))
    assert not dashboard._owner_matches(responsible, uid(5))
    participants = dashboard._list_responsibility([
        {"relation": "participant", "assignment_id": None, "principal": {"principal_id": uid(5)}}])
    assert participants == []
