"""0.5 read projections: review effects, company view grouping, confirmations (no DB)."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from memory_service_runtime.governed import method_readers
from memory_service_runtime.governed.errors import GovernedError


def _all_0_5(conn, scope_id, object_ids):
    """Stub for protocol.list_metadata: every object is currently bound to 0.5.

    company_view() calls this before it ever reads a row's payload, so tests
    that are not specifically about mixed-protocol scopes must stub it, or
    the real DB-querying implementation runs against the fake `conn` above
    and breaks on rows shaped like gov_objects, not protocol bindings.
    """
    return {oid: {"contract_version": "tkos.method/0.5"} for oid in object_ids}


def test_every_0_4_and_0_5_review_kind_has_one_effect():
    kinds = {'agent_issue_initiation', 'agreement_confirmation', 'agreement_formalized', 'candidate_set_activation',
             'ceo_reopen', 'issue_association', 'issue_participants', 'issue_reframe', 'ltco_confirmation',
             'ltco_revision_response', 'personal_agent_analysis', 'problem_closure', 'problem_transfer',
             'state_confirmation', 'strategy_update_confirmation', 'strategy_update_impact_review', 'window_closed',
             'window_comment', 'window_opinion_withdrawal', 'window_resolution',
             'review_confirmation', 'constraint_confirmation'}
    effects = {kind: method_readers.review_effect(kind) for kind in kinds}
    assert set(effects.values()) == {'decision', 'opinion', 'analysis', 'record'}
    assert effects['ltco_confirmation'] == 'decision' and effects['constraint_confirmation'] == 'decision'
    assert effects['review_confirmation'] == 'decision' and effects['candidate_set_activation'] == 'decision'
    assert effects['window_comment'] == 'opinion' and effects['personal_agent_analysis'] == 'analysis'
    assert effects['window_closed'] == 'record' and method_readers.review_effect('unknown_kind') == 'record'


def test_company_view_groups_effective_objects_by_primary_scope(monkeypatch):
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    old_pco = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-08-01T00:00:00+00:00', 'end': '2026-08-31T00:00:00+00:00'}}}
    mission = {'object_id': str(uuid4()), 'object_type': 'Mission', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-15T00:00:00+00:00'}}}
    company = {'object_id': str(uuid4()), 'object_type': 'Constraint', 'payload': {'applies_to': {'kind': 'company'}, 'effective': period}}
    scoped = {'object_id': str(uuid4()), 'object_type': 'Constraint', 'payload': {'applies_to': {'kind': 'scope', 'scope_id': 'bf-1'}, 'effective': period}}
    rows = [ltco, pco, old_pco, mission, company, scoped]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    monkeypatch.setattr(method_readers.protocol, 'list_metadata', _all_0_5)
    # T10-b: object_state now must carry a 0.5 protocol tag and a readable
    # effective_revision_id for company_view to place the object at all.
    monkeypatch.setattr(method_readers, 'object_state',
                        lambda conn, ctx, oid: {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                                                'protocol': {'contract_version': 'tkos.method/0.5'},
                                                'effective_revision_id': 'rev-eff-1'})
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    assert [s['scope_id'] for s in view['scopes']] == ['bf-1']
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert [p['object_id'] for p in scope['pcos']] == [pco['object_id']]
    assert [m['object_id'] for m in scope['missions']] == [mission['object_id']]
    assert [c['object_id'] for c in scope['constraints']] == [scoped['object_id']]
    assert [c['object_id'] for c in view['company_constraints']] == [company['object_id']]
    assert view['period'] == period and view['schema_version'] == 'method-read/0.5'


def test_company_view_excludes_an_object_bound_to_a_different_contract_version(monkeypatch):
    # T10-b(i): a 0.4-bound row must not be presented under 0.5 read semantics,
    # even though it otherwise matches the primary Scope and period.
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco_04 = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    rows = [ltco, pco_04]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    # Current binding is 0.5 for both (so the fix-round pre-filter lets both
    # rows through); this test is specifically about visible()'s own second
    # line of defence, exercised via object_state's returned protocol field.
    monkeypatch.setattr(method_readers.protocol, 'list_metadata', _all_0_5)

    def fake_object_state(conn, ctx, oid):
        version = 'tkos.method/0.4' if oid == pco_04['object_id'] else 'tkos.method/0.5'
        return {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                'protocol': {'contract_version': version}, 'effective_revision_id': 'rev-eff-1'}

    monkeypatch.setattr(method_readers, 'object_state', fake_object_state)
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert scope['pcos'] == []


def test_company_view_excludes_an_object_without_a_readable_effective_revision(monkeypatch):
    # T10-b(ii): object_state blanks effective_revision_id to None when the
    # caller cannot read it; company_view must not place such an object either.
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    pco_hidden = {'object_id': str(uuid4()), 'object_type': 'PCO', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    rows = [ltco, pco_hidden]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    monkeypatch.setattr(method_readers.protocol, 'list_metadata', _all_0_5)

    def fake_object_state(conn, ctx, oid):
        rid = None if oid == pco_hidden['object_id'] else 'rev-eff-1'
        return {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                'protocol': {'contract_version': 'tkos.method/0.5'}, 'effective_revision_id': rid}

    monkeypatch.setattr(method_readers, 'object_state', fake_object_state)
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert scope['pcos'] == []


def test_company_view_drops_rows_bound_to_other_protocols_before_reading_their_payload(monkeypatch):
    # Fix round 1, CRITICAL: the SQL selects every LTCO/PCO/Mission/Constraint
    # in the scope across ALL protocols, not just 0.5. An A2 MissionPayload
    # (a2_models.py) and a 0.1 MissionPayload (method_m1b_models.py) have no
    # `period` or `primary_scope_id` at all, so reading those keys before
    # dropping non-0.5 rows would KeyError for every caller in a mixed
    # scope, including ones who cannot even see the offending object. The
    # fix must filter by CURRENT BINDING (protocol.list_metadata) before
    # company_view ever indexes into a row's payload.
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    ltco = {'object_id': str(uuid4()), 'object_type': 'LTCO', 'payload': {'primary_scope_id': 'bf-1', 'period': {'start': '2026-09-01T00:00:00+00:00', 'end': '2027-03-01T00:00:00+00:00'}}}
    a2_mission = {'object_id': str(uuid4()), 'object_type': 'Mission',
                  'payload': {'title': 'A2 mission', 'owner_principal_id': str(uuid4())}}  # no period/primary_scope_id
    v01_mission = {'object_id': str(uuid4()), 'object_type': 'Mission', 'payload': {'name': 'v0.1 mission'}}  # no period/primary_scope_id
    rows = [ltco, a2_mission, v01_mission]
    conn = SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: None))
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    monkeypatch.setattr(method_readers, 'object_state',
                        lambda conn, ctx, oid: {'object_id': oid, 'method_state': {}, 'latest_revision': None,
                                                'protocol': {'contract_version': 'tkos.method/0.5'},
                                                'effective_revision_id': 'rev-eff-1'})

    def fake_list_metadata(conn, scope_id, object_ids):
        bindings = {a2_mission['object_id']: 'tkos.contract-a/0.1', v01_mission['object_id']: 'tkos.method/0.1'}
        return {oid: {'contract_version': bindings.get(oid, 'tkos.method/0.5')} for oid in object_ids}

    monkeypatch.setattr(method_readers.protocol, 'list_metadata', fake_list_metadata)
    # Must not raise KeyError on the two payload-incompatible rows.
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    scope = view['scopes'][0]
    assert scope['ltco']['object_id'] == ltco['object_id']
    assert scope['missions'] == []


def test_company_view_owner_effective_from_respects_current_visibility(monkeypatch):
    # Fix round 1, IMPORTANT #2: owner_effective_from must not leak the
    # timestamp of a candidate_set_activation record whose target (the
    # CandidateSet revision that was activated, per method_v04.py's
    # _run_activate_candidates/_activate_candidates) the caller can no
    # longer read; /reviews and /confirmations already hide that row.
    period = {'start': '2026-10-01T00:00:00+00:00', 'end': '2026-10-31T00:00:00+00:00'}
    activated_id = str(uuid4())
    not_activated_id = str(uuid4())
    hidden_id = str(uuid4())
    activated = {'object_id': activated_id, 'object_type': 'Mission', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    not_activated = {'object_id': not_activated_id, 'object_type': 'Mission', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    hidden = {'object_id': hidden_id, 'object_type': 'Mission', 'payload': {'primary_scope_id': 'bf-1', 'period': period}}
    rows = [activated, not_activated, hidden]

    candidate_set_id = str(uuid4())
    visible_revision_id = str(uuid4())
    hidden_revision_id = str(uuid4())
    activation_record, hidden_record = 'rec-activated', 'rec-hidden'
    records = {
        activation_record: {'target_object_id': candidate_set_id, 'target_revision_id': visible_revision_id,
                            'recorded_at': '2026-09-20T10:00:00+00:00'},
        hidden_record: {'target_object_id': candidate_set_id, 'target_revision_id': hidden_revision_id,
                        'recorded_at': '2026-09-21T10:00:00+00:00'},
    }

    def execute(sql, params=None):
        if 'gov_objects' in sql and 'gov_object_revisions' in sql:
            return SimpleNamespace(fetchall=lambda: rows)
        if 'gov_method_reviews' in sql:
            return SimpleNamespace(fetchone=lambda: records.get(params[1]))
        raise AssertionError(f'unexpected query: {sql}')

    conn = SimpleNamespace(execute=execute)
    monkeypatch.setattr(method_readers.access, 'is_method_object', lambda conn, ctx, oid: True)
    monkeypatch.setattr(method_readers.protocol, 'list_metadata', _all_0_5)

    def fake_object_state(conn, ctx, oid):
        state = {}
        if oid == activated_id:
            state = {'owner_activation_record_id': activation_record}
        elif oid == hidden_id:
            state = {'owner_activation_record_id': hidden_record}
        return {'object_id': oid, 'method_state': state, 'latest_revision': None,
                'protocol': {'contract_version': 'tkos.method/0.5'}, 'effective_revision_id': 'rev-eff-1'}

    monkeypatch.setattr(method_readers, 'object_state', fake_object_state)

    def fake_revision(conn, ctx, oid, rid):
        if rid == hidden_revision_id:
            raise GovernedError('FORBIDDEN')
        return {'object_id': oid, 'revision_id': rid}

    monkeypatch.setattr(method_readers.access, 'revision', fake_revision)
    view = method_readers.company_view(conn, SimpleNamespace(scope_id=str(uuid4())), period['start'], period['end'])
    by_id = {m['object_id']: m['owner_effective_from'] for m in view['scopes'][0]['missions']}
    assert by_id[activated_id] == '2026-09-20T10:00:00+00:00'
    assert by_id[not_activated_id] is None
    assert by_id[hidden_id] is None


def _reviews_and_commitments_conn(reviews, commitments):
    def execute(sql, params=None):
        if 'gov_method_reviews' in sql:
            return SimpleNamespace(fetchall=lambda: reviews)
        if 'gov_method_commitments' in sql:
            return SimpleNamespace(fetchall=lambda: commitments)
        raise AssertionError(f'unexpected query in confirmations(): {sql}')
    return SimpleNamespace(execute=execute)


def _commitment_row(*, responsibility_object_id):
    return {'commitment_id': str(uuid4()), 'candidate_object_id': str(uuid4()),
            'candidate_revision_id': str(uuid4()), 'responsibility_object_id': responsibility_object_id,
            'responsibility_revision_id': str(uuid4()), 'principal_id': str(uuid4()), 'assignment_id': str(uuid4()),
            'statement': 'DRI commits.', 'recorded_at': '2026-10-01T00:00:00+00:00'}


def test_confirmations_excludes_a_commitment_whose_referenced_revision_is_not_visible(monkeypatch):
    # T10-c: each commitment row names another PCO/Mission and another
    # person's assignment; it must be dropped whenever EITHER side (the
    # responsibility revision or the candidate revision) is not currently
    # visible to the caller, not only when the requested object itself is.
    object_id = str(uuid4())
    visible_commitment = _commitment_row(responsibility_object_id=object_id)
    hidden_by_responsibility = _commitment_row(responsibility_object_id=object_id)
    hidden_by_candidate = _commitment_row(responsibility_object_id=object_id)
    conn = _reviews_and_commitments_conn([], [visible_commitment, hidden_by_responsibility, hidden_by_candidate])
    monkeypatch.setattr(method_readers.access, 'head', lambda conn, ctx, oid: {'object_id': oid})

    def fake_revision(conn, ctx, oid, rid):
        if rid == hidden_by_responsibility['responsibility_revision_id']:
            raise GovernedError('FORBIDDEN')
        if rid == hidden_by_candidate['candidate_revision_id']:
            raise GovernedError('NOT_FOUND')
        return {'object_id': oid, 'revision_id': rid}

    monkeypatch.setattr(method_readers.access, 'revision', fake_revision)
    result = method_readers.confirmations(conn, SimpleNamespace(scope_id=str(uuid4())), object_id)
    assert [c['commitment_id'] for c in result['commitments']] == [visible_commitment['commitment_id']]


def test_confirmations_review_row_visibility_check_propagates_unexpected_error(monkeypatch):
    # T10-c: only NOT_FOUND/FORBIDDEN are a plain visibility filter; any other
    # GovernedError (e.g. from a protocol/read-support check) must surface,
    # not be silently swallowed like the brief's bare `except GovernedError`.
    object_id = str(uuid4())
    review_row = {'record_id': str(uuid4()), 'kind': 'ltco_confirmation', 'target_object_id': object_id,
                  'target_revision_id': str(uuid4()), 'window_id': None, 'principal_id': str(uuid4()),
                  'content': {}, 'action_id': str(uuid4()), 'recorded_at': '2026-10-01T00:00:00+00:00'}
    conn = _reviews_and_commitments_conn([review_row], [])
    monkeypatch.setattr(method_readers.access, 'head', lambda conn, ctx, oid: {'object_id': oid})

    def explode(conn, ctx, oid, rid):
        raise GovernedError('PROTOCOL_NOT_SUPPORTED')

    monkeypatch.setattr(method_readers.access, 'revision', explode)
    with pytest.raises(GovernedError) as excinfo:
        method_readers.confirmations(conn, SimpleNamespace(scope_id=str(uuid4())), object_id)
    assert excinfo.value.code == 'PROTOCOL_NOT_SUPPORTED'


def test_confirmations_commitment_row_visibility_check_propagates_unexpected_error(monkeypatch):
    object_id = str(uuid4())
    commitment_row = _commitment_row(responsibility_object_id=object_id)
    conn = _reviews_and_commitments_conn([], [commitment_row])
    monkeypatch.setattr(method_readers.access, 'head', lambda conn, ctx, oid: {'object_id': oid})

    def explode(conn, ctx, oid, rid):
        raise GovernedError('PROTOCOL_NOT_SUPPORTED')

    monkeypatch.setattr(method_readers.access, 'revision', explode)
    with pytest.raises(GovernedError) as excinfo:
        method_readers.confirmations(conn, SimpleNamespace(scope_id=str(uuid4())), object_id)
    assert excinfo.value.code == 'PROTOCOL_NOT_SUPPORTED'


# ------------------------------------------------------- review_effect exhaustiveness

_METHOD_SOURCE_DIR = Path(method_readers.__file__).resolve().parent
_REVIEW_CALL_PATTERN = re.compile(r'\.review\(\s*[\'"]([a-z_]+)[\'"]')
# These four kinds are actually written by an e.review(...) call but cannot be
# found by the regex above, because the source does not spell them out as the
# literal string immediately following `.review(`:
#  - method_m1a.py ~343 builds the kind dynamically for three m1a actions
#    (`action.removeprefix("m1a_")`), so record_clarification/check_memo/
#    direct_clarification are computed, not literal, text;
#  - method_m1b.py ~536 writes "ltco_feedback" only as the else-branch of a
#    ternary (`"ltco_confirmation" if confirmed else "ltco_feedback"`); the
#    regex finds the if-branch literal that sits right after `.review(` but
#    not the second literal further along the same expression.
_UNDISCOVERABLE_KINDS = {"record_clarification", "check_memo", "direct_clarification", "ltco_feedback"}


def _kinds_written_by_review_calls():
    """Every `kind` actually passed to a Method executor's `e.review(...)`,
    scanned straight from source. A newly-added review kind shows up here on
    its own, so forgetting to classify it fails this test instead of quietly
    taking review_effect's `record` fallback."""
    kinds = set(_UNDISCOVERABLE_KINDS)
    for name in ("method_m1a.py", "method_m1b.py", "method_v02.py", "method_v03.py", "method_v04.py", "method_v05.py"):
        kinds.update(_REVIEW_CALL_PATTERN.findall((_METHOD_SOURCE_DIR / name).read_text(encoding="utf-8")))
    return kinds


# 合同 §7：决定类 = 有权人对一个业务对象的正式确认 / 正式化 / 激活 / 重开 / 关闭 /
# 移交（该对象随之进入 confirmed/active/formal 等正式状态，或是同一个决定点的
# 另一分支）；意见类 = 参与者对他人产出发表的看法；分析类 = Agent 产出的分析 /
# 核验结果；record = 系统记录的过程事实，既非正式决定也非意见或分析。逐个 kind
# 的依据见行内注释；本表驱动 review_effect() 的穷尽性校验，也是 fix round 1
# report 里分类表的唯一数据源。
EXPECTED_REVIEW_EFFECTS = {
    # --- 既有（本任务此前已实现，未改动）---
    "ltco_confirmation": "decision", "review_confirmation": "decision", "constraint_confirmation": "decision",
    "candidate_set_activation": "decision", "agreement_confirmation": "decision", "agreement_formalized": "decision",
    "strategy_update_confirmation": "decision", "state_confirmation": "decision", "ceo_reopen": "decision",
    "problem_closure": "decision", "problem_transfer": "decision",
    "window_comment": "opinion", "window_opinion_withdrawal": "opinion", "ltco_revision_response": "opinion",
    "personal_agent_analysis": "analysis", "strategy_update_impact_review": "analysis", "window_resolution": "analysis",
    "window_closed": "record",  # 冻结意见窗口的程序性步骤，不是对业务对象内容的裁决；本任务此前已锁定。
    # --- 本轮新分类：decision（均由有权人做出，对象随之进入正式状态）---
    "strategic_issue_confirmation": "decision",  # CEO 将 PotentialIssue 正式确认为 StrategicIssue（m1a_confirm_strategic_issue / v02 同名动作）。
    "meeting_minutes_confirmation": "decision",  # DRI 确认会议纪要，纪要随之 active/effective。
    "strategic_agreement_confirmation": "decision",  # CEO 确认 StrategicAgreement，随之 active/effective。
    "strategy_adjustment_decision": "decision",  # CEO 对"是否需要调整战略"的正式裁决；字面即"决定"。
    "architecture_confirmation": "decision",  # CEO 确认 Architecture（含随 Update 一并激活的路径），随之 active/effective。
    "candidate_set_confirmation": "decision",  # 确认候选集，PCO / Mission 随之 confirmed、effective。
    "signal_disposition": "decision",  # 仅 CEO 可 activate / archive 一个 Signal，对其去向的正式裁决。
    "brief_sufficiency": "decision",  # CEO 确认 Brief 是否足够，字面即确认（m1a_confirm_brief，仅 CEO）。
    "ltco_feedback": "decision",  # 与 ltco_confirmation 同一个 CEO 决定点的另一分支：不确认、退回并说明理由。
    "agent_issue_initiation": "decision",  # 经 ceo_agent_owner 校验的 CEO 代理人把 Issue 正式建为 active（v03/v04 共用）。
    "issue_reframe": "decision",  # 同样经校验的 CEO 代理人权限，产生新的 active/effective Issue 版本。
    # --- 本轮新分类：opinion（参与者对他人产出的看法，不改变任何对象的正式状态）---
    "record_clarification": "opinion",  # DRI（本人或其人身 Agent 代为）对备忘录提出的澄清意见。
    "direct_clarification": "opinion",  # 经 Agent 核验后，CEO / DRI 本人直接给出的澄清意见。
    # --- 本轮新分类：analysis（Agent 产出，非人的正式决定）---
    "check_memo": "analysis",  # CEO_AGENT / DRI 人身 Agent 对备忘录的自动核验（clear / needs_clarification）。
    "research_quality_precheck": "analysis",  # CEO_AGENT 对研究报告的质量预检（pass / 否）。
    # --- 本轮新分类：record（系统记录的过程事实）---
    "research_assignment": "record",  # CEO 指派研究参与者；是过程记录，不属于确认 / 正式化 / 激活 / 重开 / 关闭 / 移交。
    "research_report_submission": "record",  # DRI 提交自己的工作成果，是流程推进记录，不是对他物的意见或裁决。
    "issue_association": "record",  # 仅向 Issue 状态追加来源引用，不改变对象的正式状态（无 status/effective 变更）。
    "issue_participants": "record",  # 仅设置 Issue 的参与者列表，不改变对象的正式状态（无 status/effective 变更）。
}


def test_review_effect_classifies_every_kind_actually_written_by_the_executors():
    # Fix round 1, IMPORTANT #3: the kind list is derived from the actual
    # e.review(...) call sites (not hand-copied), so a kind newly written by
    # some future executor and never triaged here fails this test loudly
    # instead of silently taking review_effect's `record` fallback. The
    # equality (not just subset) also catches a stale entry for a kind that
    # is no longer written anywhere.
    discovered = _kinds_written_by_review_calls()
    assert discovered == set(EXPECTED_REVIEW_EFFECTS), (
        f"undiscovered/unclassified: {discovered - set(EXPECTED_REVIEW_EFFECTS)}; "
        f"stale (no longer written): {set(EXPECTED_REVIEW_EFFECTS) - discovered}")
    for kind, effect in EXPECTED_REVIEW_EFFECTS.items():
        assert method_readers.review_effect(kind) == effect, kind
    assert method_readers.review_effect('unknown_kind') == 'record'
