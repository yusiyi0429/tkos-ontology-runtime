"""0.5 工作台投影：人工门进入 Method 任务、争议路径、浏览器动作白名单（D7）与版本标签。

投影只收窄、不放宽：核心 prepare / commit 仍重新校验权限；0.4 scope 的读取语义逐项不变。
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from memory_service_app import governance_commands as journal
from memory_service_runtime.governed import governance, method_v04, method_v05
from memory_service_runtime.governed.errors import GovernedError

V04, V05 = 'tkos.method/0.4', 'tkos.method/0.5'


def _human(*, roles=(), domain='d', principal='p1'):
    return SimpleNamespace(scope_id=str(uuid4()), principal_type='human', principal_id=principal,
                           assignments=[{'role': role, 'domain_id': domain, 'assignment_id': 'a'}
                                        for role in roles])


def _object(object_id, object_type, version, phase, payload=None):
    return {'object_id': object_id, 'object_type': object_type, 'object_version': 1, 'domain_id': 'd',
            'latest_revision': {'revision_id': str(uuid4()), 'payload_hash': 'a' * 64,
                                'payload': payload or {'title': f'{object_type} title'}},
            'protocol': {'contract_version': version}, 'method_state': {'phase': phase}}


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _ScanConn:
    """在内存里回放 method_tasks 扫描窗口的语义：0.4 任务类型照旧入选；0.5 人工门类型只在当前
    绑定为 0.5 时入选；按 object_id 排序并截到 LIMIT。参数按扫描 SQL 的占位符顺序读取（scope、
    after、after、任务类型、0.5 任务类型、0.5 契约版本、LIMIT）。SQL 在真实 PostgreSQL 上的执行
    见 test_the_method_task_scan_runs_on_real_postgres。"""

    def __init__(self, objects):
        self.objects = sorted(objects)          # (object_id, object_type, current binding version)
        self.windows = []

    def execute(self, sql, params=()):
        assert 'FROM gov_objects' in sql and 'gov_object_protocol_bindings' in sql, sql
        _scope, after, _after, task_types, v05_types, v05_version, limit = params
        rows = [{'object_id': oid} for oid, kind, bound in self.objects
                if (after is None or oid > after)
                and (kind in task_types or (kind in v05_types and bound == v05_version))][:limit]
        self.windows.append([row['object_id'] for row in rows])
        return _Rows(rows)


def _oid(value):
    return str(UUID(int=value))


def _serve(monkeypatch, objects):
    reads = []

    def object_state(conn, ctx, object_id):
        reads.append(object_id)
        return objects[object_id]
    monkeypatch.setattr(governance.method_readers, 'object_state', object_state)
    return reads


# ------------------------------------------------------------- I1: task scan


def test_a_generated_0_5_period_review_is_a_ceo_task_offering_its_confirmation(monkeypatch):
    review = _object(_oid(10), 'PeriodReview', V05, 'generated')
    reads = _serve(monkeypatch, {review['object_id']: review})
    conn = _ScanConn([(review['object_id'], 'PeriodReview', V05)])
    tasks = governance.method_tasks(conn, _human(roles=('CEO',)))['items']
    assert [(task['object_type'], [a['action_type'] for a in task['actions']]) for task in tasks] == \
        [('PeriodReview', ['m1b_confirm_review'])]
    assert tasks[0]['actions'][0]['allowed'] is True and tasks[0]['contract_version'] == V05
    # 不是该域 CEO 的人看不到这项待办；已确认的复盘也不再是待办。
    assert governance.method_tasks(conn, _human(roles=('DOMAIN_DRI',)))['items'] == []
    review['method_state']['phase'] = 'confirmed'
    assert governance.method_tasks(conn, _human(roles=('CEO',)))['items'] == []
    assert reads == [review['object_id']] * 3


def test_a_draft_0_5_scope_constraint_is_a_task_for_its_scope_dri_only(monkeypatch):
    constraint = _object(_oid(20), 'Constraint', V05, 'draft',
                         {'title': 'Two engineers only', 'applies_to': {'kind': 'scope', 'scope_id': 'scope-a'}})
    _serve(monkeypatch, {constraint['object_id']: constraint})

    def resolved(conn, ctx, payload):      # 真实解析见 test_method_v05_dispatch 的 Constraint 用例
        if ctx.principal_id != 'dri-a':
            raise GovernedError('FORBIDDEN')
        return {'assignment_id': 'dri-a-assignment', 'domain_id': 'auth-a'}
    monkeypatch.setattr(method_v05, 'constraint_assignment_static', resolved)
    conn = _ScanConn([(constraint['object_id'], 'Constraint', V05)])
    tasks = governance.method_tasks(conn, _human(roles=('DOMAIN_DRI',), domain='auth-a', principal='dri-a'))['items']
    assert [(task['object_type'], [a['action_type'] for a in task['actions']]) for task in tasks] == \
        [('Constraint', ['m1b_confirm_constraint'])]
    assert governance.method_tasks(conn, _human(roles=('IC',), domain='auth-a', principal='ic-a'))['items'] == []


def test_a_0_4_scope_task_scan_is_unchanged(monkeypatch):
    """0.4 也有 PeriodReview（Agent 分析、无人工门）。0.5 的两个人工门类型只按当前 0.5 绑定入选，
    所以它们不会占用 0.4 scope 的 limit*3 扫描窗口；0.4 的任务类型清单本身不变。"""
    assert governance.METHOD_TASK_TYPES == (
        'StrategicIssue', 'StrategicAgreement', 'StrategyUpdateProposal', 'LTCO', 'PCO', 'Mission',
        'ReviewWindow', 'CandidateSet', 'OperatingState', 'OperatingProblem')
    ltco = _object(_oid(4), 'LTCO', V04, 'draft')
    reads = _serve(monkeypatch, {ltco['object_id']: ltco})
    conn = _ScanConn([(_oid(1), 'PeriodReview', V04), (_oid(2), 'PeriodReview', V04),
                      (_oid(3), 'PeriodReview', V04), (ltco['object_id'], 'LTCO', V04)])
    page = governance.method_tasks(conn, _human(roles=('CEO',)), limit=1)
    assert conn.windows == [[ltco['object_id']]]          # 窗口里只有 0.4 任务类型
    assert reads == [ltco['object_id']]
    assert [(task['object_id'], [a['action_type'] for a in task['actions']]) for task in page['items']] == \
        [(ltco['object_id'], ['m1b_confirm_ltco'])]
    assert page['next_after'] is None


@pytest.mark.db
def test_the_method_task_scan_runs_on_real_postgres():
    """扫描 SQL（含按当前绑定选 0.5 类型的子查询）以应用角色在真实 PostgreSQL 上执行：随机 scope，
    不写任何行，只证明语句、列名与权限成立。"""
    import psycopg
    from psycopg.rows import dict_row
    from memory_service_runtime.governed import db
    scope_id = str(uuid4())
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as conn:
        try:
            db._set_scope(conn, scope_id)
            db.set_write_capability(conn)
            ctx = SimpleNamespace(scope_id=scope_id, principal_type='human', principal_id=str(uuid4()),
                                  assignments=[])
            assert governance.method_tasks(conn, ctx) == {'items': [], 'next_after': None}
            assert governance.method_tasks(conn, ctx, after=str(uuid4()), limit=2) == {'items': [], 'next_after': None}
        finally:
            conn.rollback()


# ------------------------------------------------- I2: §6 dispute on a 0.5 State


def test_a_recorded_0_5_state_offers_the_dispute_path_to_its_responsible_person(monkeypatch):
    monkeypatch.setattr(method_v05, 'state_subject_owner_static', lambda conn, ctx, subject: 'owner')
    monkeypatch.setattr(method_v04, '_state_subject_owner_static', lambda conn, ctx, subject: 'owner')
    monkeypatch.setattr(governance.db, '_assignments', lambda *a: [{'assignment_id': 'a'}])
    subject = {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'payload_hash': 'b' * 64}

    def state(version, phase):
        return _object(str(uuid4()), 'OperatingState', version, phase, {'subject_ref': subject})

    owner, other = _human(principal='owner'), _human(principal='other')
    assert governance._availability(object(), owner, state(V05, 'recorded'), 'method_open_problem',
                                    ceo=False) == (True, None)
    assert governance._availability(object(), other, state(V05, 'recorded'), 'method_open_problem',
                                    ceo=False) == (False, 'not_state_owner')
    offered = {item['action_type']: item['allowed']
               for item in governance.method_object_actions(None, owner, state(V05, 'recorded'))['items']}
    assert offered == {'method_open_problem': True}         # 0.5 没有 method_confirm_state
    # 0.4 不变：只有已确认的状态开放争议路径。
    assert governance.phase_rule('method_open_problem', V04) == {'confirmed'}
    assert governance.phase_rule('method_open_problem', V05) == {'recorded'}
    assert governance._availability(object(), owner, state(V04, 'confirmed'), 'method_open_problem',
                                    ceo=False) == (True, None)
    for phase in ('proposed', 'recorded'):
        assert governance._availability(object(), owner, state(V04, phase), 'method_open_problem',
                                        ceo=False) == (False, 'phase_not_permitted')


# ------------------------------------ m7: D7 browser allowlist and version label


def test_constraint_record_and_revise_are_not_browser_actions_in_0_5(monkeypatch):
    """计划 D7：0.5 的 Constraint 登记 / 修订走 API 或 Agent，不做浏览器表单。工作台不提供，
    会话门面也不接受（与 Agent 专属动作一样是 FORBIDDEN，而不是到 prepare 才报错）。"""
    constraint = _object(str(uuid4()), 'Constraint', V05, 'draft',
                         {'title': 'Cash', 'applies_to': {'kind': 'company'}})
    monkeypatch.setattr(method_v05, 'constraint_assignment_static',
                        lambda conn, ctx, payload: {'assignment_id': 'a', 'domain_id': 'd'})
    offered = [item['action_type'] for item in
               governance.method_object_actions(None, _human(roles=('CEO',)), constraint)['items']]
    assert offered == ['m1b_confirm_constraint']
    constraint['method_state']['phase'] = 'confirmed'
    assert [item['action_type'] for item in
            governance.method_object_actions(None, _human(roles=('CEO',)), constraint)['items']] == \
        ['m1b_confirm_constraint']                            # 仍只列确认（此时按阶段不可用），不列修订

    def body(action, params, target=True):
        envelope = {'action_type': action, 'contract_version': V05, 'expected_versions': [],
                    'idempotency_key': 'x' * 32, 'reason': 'Human decision recorded', 'params': params}
        if target:
            envelope['target'] = {'object_id': str(uuid4()), 'revision_id': str(uuid4()), 'expected_version': 1}
        return envelope

    for action, target in (('m1b_record_constraint', False), ('m1b_revise_constraint', True)):
        with pytest.raises(GovernedError) as error:
            journal.parse(body(action, {}, target))
        assert error.value.code == 'FORBIDDEN', action
    for action in ('m1b_confirm_constraint', 'm1b_confirm_review'):
        model, kind = journal.parse(body(action, {'statement': 'Personally confirmed.'}))
        assert kind == 'method' and model.action_type == action and model.contract_version == V05
    # 两处允许清单一致：浏览器可用的 0.5 人工动作就是模型的人工动作去掉 D7 的两个。
    from memory_service_runtime.governed.method_v05_models import HUMAN_ACTIONS as V05_HUMAN
    expected = V05_HUMAN - {'m1b_record_constraint', 'm1b_revise_constraint'}
    assert governance.human_actions_for(V05) == journal.human_actions_for(V05) == expected
    from memory_service_runtime.governed.method_v04_models import HUMAN_ACTIONS as V04_HUMAN
    assert governance.human_actions_for(V04) == journal.human_actions_for(V04) == V04_HUMAN
    for action in expected:
        assert governance.HUMAN_ACTION_LABELS.get(action) and governance.FORMAL_EFFECT.get(action), action


class _WindowConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return _Rows(self._rows)


def test_window_tasks_name_the_objects_own_rule_version(monkeypatch):
    labels = {}
    for version in (V04, V05):
        window_id = str(uuid4())
        value = {'object': {'object_id': window_id, 'object_type': 'ReviewWindow',
                            'method_state': {'phase': 'resolved'},
                            'latest_revision': {'payload': {'title': 'Window'}},
                            'protocol': {'contract_version': version}},
                 'monthly': None, 'actions': [{'action_type': 'm1b_commit_candidate', 'allowed': True}],
                 'scenes': []}
        monkeypatch.setattr(governance, 'window', lambda *_a, value=value: value)
        labels[version] = governance.tasks(_WindowConn([{'object_id': window_id}]), _human())['items'][0]['label']
    assert labels == {V04: '0.4 人工确认事项', V05: '0.5 人工确认事项'}


def test_each_todo_carries_the_windows_bound_contract_version(monkeypatch):
    """工作台按对象当前绑定的规则版本分派办理页，所以每条待办都要带出这个版本。"""
    views = {}
    for version, phase in (('tkos.method/0.3', 'open'), (V04, 'resolved'), (V05, 'resolved')):
        window_id = str(uuid4())
        views[window_id] = {
            'object': {'object_id': window_id, 'object_type': 'ReviewWindow',
                       'method_state': {'phase': phase},
                       'latest_revision': {'payload': {'title': f'{version} window'}},
                       'protocol': {'contract_version': version}},
            'monthly': ({'my_reviews': [], 'candidate': {'status': 'unavailable'}}
                        if version == 'tkos.method/0.3' else None),
            'actions': [{'action_type': 'm1b_comment', 'allowed': True}], 'scenes': []}
    monkeypatch.setattr(governance, 'window', lambda _conn, _ctx, window_id: views[window_id])
    items = governance.tasks(_WindowConn([{'object_id': oid} for oid in sorted(views)]), _human())['items']
    assert {item['object_id']: item['contract_version'] for item in items} == {
        oid: view['object']['protocol']['contract_version'] for oid, view in views.items()}


def _window_view(version, phase, offered, scenes=()):
    """window() 的返回形状；offered 是 {动作: 本人此刻是否可办}。"""
    return {'object': {'object_id': str(uuid4()), 'object_type': 'ReviewWindow',
                       'method_state': {'phase': phase},
                       'latest_revision': {'payload': {'title': f'{phase} window'}},
                       'protocol': {'contract_version': version}},
            'monthly': None if version in (V04, V05) else {'my_reviews': [], 'candidate': {'status': 'unavailable'}},
            'actions': [{'action_type': action, 'allowed': allowed} for action, allowed in offered.items()],
            'scenes': list(scenes)}


def _todos(monkeypatch, view, ctx):
    monkeypatch.setattr(governance, 'window', lambda *_: view)
    return governance.tasks(_WindowConn([{'object_id': view['object']['object_id']}]), ctx)['items']


@pytest.mark.parametrize('version', [V04, V05])
@pytest.mark.parametrize('phase', ['confirmed', 'reopened'])
def test_a_finished_method_window_leaves_my_todos(monkeypatch, version, phase):
    """CEO 整组激活后窗口为 confirmed，重开后旧窗口为 reopened：都已办结，不再是待办。#32 B 试点链验收：
    激活后 0.5 窗口仍以「0.5 人工确认事项」、零个可办动作留在 CEO 的「我的待办」。"""
    view = _window_view(version, phase, {'m1b_reopen_window': False})
    assert _todos(monkeypatch, view, _human(roles=('CEO',))) == []


@pytest.mark.parametrize('version', [V04, V05])
@pytest.mark.parametrize('phase', ['open', 'closed', 'resolved'])
def test_a_method_window_in_progress_stays_a_todo_while_waiting_on_others(monkeypatch, version, phase):
    """与 0.3 的「等待 Co-agent 收拢」一致：进行中的窗口即使本人此刻没有可办动作（等 Co-agent 关窗收拢、
    等 DRI 承诺与 CEO 激活）也留在待办里。"""
    view = _window_view(version, phase, {'m1b_comment': False, 'm1b_reopen_window': False})
    assert [(item['phase'], item['label'], item['actions']) for item in _todos(monkeypatch, view, _human())] == \
        [(phase, f"{version.rsplit('/', 1)[-1]} 人工确认事项", [])]


@pytest.mark.parametrize('phase, reviewed, label', [
    ('open', False, '等待 Co-agent 收拢'), ('closed', False, '等待 Co-agent 收拢'),
    ('resolved', False, '核对候选差异'), ('resolved', True, None),
    ('confirmed', False, None), ('reopened', False, None)])
def test_03_window_todos_keep_their_phase_rule(monkeypatch, phase, reviewed, label):
    """0.3 不变：只列进行中的阶段；本人无动作时标「等待 Co-agent 收拢」；已核对当前候选的 resolved 窗口不再列出。"""
    view = _window_view('tkos.method/0.3', phase, {'m1b_comment': False},
                        scenes=[{'monthly': {'reviewed_current_candidate': reviewed}}])
    assert [item['label'] for item in _todos(monkeypatch, view, _human())] == ([label] if label else [])


@pytest.mark.parametrize('version', [V04, V05])
def test_a_method_window_read_has_no_03_monthly_view_but_names_its_binding(monkeypatch, version):
    obj = _object(str(uuid4()), 'ReviewWindow', version, 'open')
    _serve(monkeypatch, {obj['object_id']: obj})
    monkeypatch.setattr(governance.workspace_readers, 'identity', lambda *_: {'principal_id': 'p1'})
    monkeypatch.setattr(governance, 'method_object_actions', lambda *_: {'items': []})
    monkeypatch.setattr(governance.method_readers, 'recovery', lambda *_: {})
    value = governance.window(None, _human(), obj['object_id'])
    assert value['monthly'] is None
    assert value['object']['protocol']['contract_version'] == version
