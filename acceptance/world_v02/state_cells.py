"""tkos.world/0.2 独立验收的状态表逐格（票 #65）：按登记 ``lifecycles`` 与 ``issue.lifecycle`` 的每一行各走一遍。

每一行一个新主体（对象，或 Issue 的一个新问题组件），经 HTTP 推到这一行的起始状态，然后三条检查：
- 拒绝：同一动作由不是这一行「谁记」的人记，FORBIDDEN，prepare 与 commit 两个入口上 scope 的库快照都不变；
- 进入：由「谁记」记下，读回进入状态与推出它的事件（自环：状态与推出它的事件都不变）；
- 幂等：同键重放返回原回执、这张回执只有一条事件、状态不变；换键再记一次不再推进状态——进入状态有同一动作的自环
  就照记（状态与推出事件不变），否则 INVALID_STATE（关注标记只标一次、同一人对同一内容只记一条 Agreement）。

放在最后几个场景之后、撤销 CEO 指派之前跑，对象都新建，不动前面场景的对象：先记一条外部事件、写一条公司复盘并由
CEO 确认，建一条单元长期目标并确认；周期目标带 review_ref 指向这条复盘（形成锚定），Mission 挂在已确认的周期目标
下。另补矩阵里现有场景没覆盖的两格：Mission 的一轮被 DRI 退回即作废、什么都不写回；幂等键不同的同一条外部事件
照记两条。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .matrix import cell_check, tables

AGENTS = {'agent_a'}
REASON = {'text': '最低理由：状态表逐格（#65）。'}
# 各类型动作的「谁记」（按状态表的 by 取这次验收里的人），与记录者不符的人（拒绝那一格用）。
RIGHT = {
    'Task': {'world_assign': 'owner_a', 'world_start': 'ic_a', 'world_deliver': 'ic_a', 'world_accept': 'owner_a',
             'world_reject': 'owner_a', 'world_reopen': 'owner_a', 'world_cancel': 'owner_a'},
    'Activity': {'world_assign': 'ic_a', 'world_start': 'agent_a', 'world_deliver': 'agent_a', 'world_accept': 'ic_a',
                 'world_reject': 'ic_a', 'world_reopen': 'ic_a', 'world_cancel': 'ic_a'},
    'Mission': {'world_commit_mission': 'owner_a', 'world_confirm_mission': 'a', 'world_start': 'owner_a',
                'world_deliver': 'owner_a', 'world_accept': 'a', 'world_reject': 'a', 'world_reopen': 'a',
                'world_cancel': 'a', 'world_mark_core_battle': 'ceo'},
    'PeriodGoal': {'world_commit_period_goal': 'a', 'world_confirm_period_goal': 'ceo',
                   'world_reconfirm_period_goal': 'ceo', 'world_confirm_review': 'ceo', 'world_cancel': 'ceo'},
    'LongTermGoal': {'world_confirm_long_term_goal': 'ceo', 'world_reconfirm_long_term_goal': 'ceo',
                     'world_cancel': 'ceo'},
    'Strategy': {'world_assign_strategy_round': 'ceo', 'world_agree_strategy': 'a', 'world_confirm_strategy': 'ceo',
                 'world_reconfirm_strategy': 'ceo'},
    'Issue': {'world_raise_issue': 'agent_a', 'world_route_issue': 'agent_a', 'world_own_issue': 'owner_a',
              'world_dispose_issue': 'owner_a', 'world_return_issue': 'agent_a'},
}
WRONG = {
    # parent 的动作给对象自己的责任人记，self 的给上一级的责任人记。
    'Task': {'world_assign': 'ic_a', 'world_start': 'owner_a', 'world_deliver': 'owner_a', 'world_accept': 'ic_a',
             'world_reject': 'ic_a', 'world_reopen': 'ic_a', 'world_cancel': 'ic_a'},
    # Activity 的上一级是 Task 的责任人：parent 的动作给 Mission 的 Owner（再上一级）记，self 的给 Task 的责任人记。
    'Activity': {'world_assign': 'owner_a', 'world_start': 'ic_a', 'world_deliver': 'ic_a', 'world_accept': 'owner_a',
                 'world_reject': 'owner_a', 'world_reopen': 'owner_a', 'world_cancel': 'owner_a'},
    # 承诺只由它的 Owner 本人（单元里另一位 Owner 不行）；开始、交付不由 DRI；DRI 的动作不由 Owner；门按策略角色。
    'Mission': {'world_commit_mission': 'owner_a2', 'world_confirm_mission': 'owner_a', 'world_start': 'a',
                'world_deliver': 'a', 'world_accept': 'owner_a', 'world_reject': 'owner_a', 'world_reopen': 'owner_a',
                'world_cancel': 'owner_a', 'world_mark_core_battle': 'a'},
    'PeriodGoal': {'world_commit_period_goal': 'ic_a', 'world_confirm_period_goal': 'a',
                   'world_reconfirm_period_goal': 'a', 'world_confirm_review': 'a', 'world_cancel': 'a'},
    'LongTermGoal': {'world_confirm_long_term_goal': 'a', 'world_reconfirm_long_term_goal': 'a', 'world_cancel': 'a'},
    # 指定本轮只由 Strategy 的责任人（公司域的 IC 策略放行，不是责任人）；Agreement 只由本轮被指定的人。
    'Strategy': {'world_assign_strategy_round': 'unrelated', 'world_agree_strategy': 'c',
                 'world_confirm_strategy': 'a', 'world_reconfirm_strategy': 'a'},
    # 提出与路由不由 Mission 主干以外的人（Task 的责任人）；承接、处置、退回不由单元里另一位 Owner。
    'Issue': {'world_raise_issue': 'ic_a', 'world_route_issue': 'ic_a', 'world_own_issue': 'owner_a2',
              'world_dispose_issue': 'owner_a2', 'world_return_issue': 'owner_a2'},
}


def repeats(object_type, row):
    """换键再记一次是否照记：进入状态有同一动作、同一结果与处置的自环，且不受「只标一次」「同一人对同一内容只记
    一条 Agreement」所限。否则应被拒（INVALID_STATE）。"""
    if row['guard'] == 'once' or row['action'] == 'world_agree_strategy':
        return False
    return any(item['from'] == item['to'] == row['to'] and item['action'] == row['action']
               and item['outcome'] == row['outcome'] and item['disposition'] == row['disposition']
               for item in tables()[object_type]['transitions'])


def utc(moment):
    text = moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return text + (f'.{moment.microsecond:06d}' if moment.microsecond else '') + 'Z'


def state_cells(book, h, f, flow, trunk):
    check = book.check
    made = trunk['made']
    scope = f['scope_id']
    who = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    periods = iter(f'{year}-{month:02d}' for year in (2040, 2041) for month in range(1, 13))
    now = datetime.now(timezone.utc)

    def view(oid):
        return flow.read('outsider', oid)

    def life(oid):
        found = view(oid)['records']['lifecycle']
        return {'status': found['status'], 'event_id': found['event_id']}

    def ref(oid):
        return f"{oid}@{view(oid)['business']['version']}"

    def act(actor, kind, oid, params=None, target=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {}, target)))['result']

    def events_of(receipt):
        return flow.rows('SELECT event_id FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                         (scope, receipt['receipt_id']))

    def declared(actor, scene):
        if actor not in AGENTS:
            return {}
        return {'declaration': {'scene': scene, 'trigger': '状态表逐格（#65）', 'human_acceptance': {'required': False}}}

    def snapshot(actor, oid, payload_type, as_of, source, blocks):
        """写一条带独有周期的快照，返回回执结果另加以它为目标时用的 target（期望版本取列对象头）。"""
        period = next(periods)
        written = flow.refresh(actor, {
            'title': f'状态表逐格 {payload_type} {period}', 'subject_ref': ref(oid), 'as_of': utc(as_of),
            'period': period, 'payload_type': payload_type, 'source_event_refs': [f"event:{source['event_id']}"],
            'blocks': blocks}, declared(actor, ref(oid)).get('declaration'))['result']
        header, = flow.clients['outsider'].json(
            'GET', '/v1/world/objects?' + urlencode({'type': 'StateSnapshot', 'period': period}))['items']
        written['target'] = {'object_id': written['object_id'], 'revision_id': written['revision_id'],
                             'expected_version': header['object_version']}
        return written

    def run_row(object_type, row, body, read, entered=lambda receipt: True):
        """一行的三条检查。body(actor) 给出这条命令（每次新的幂等键），read() 读回 {status, event_id}。"""
        right, wrong = RIGHT[object_type][row['action']], WRONG[object_type][row['action']]
        if object_type == 'Issue' and row['action'] == 'world_return_issue' and row['from'] == 'owned':
            right = 'owner_a'  # 已承接时由承接人本人退回，已路由时由路由者（Co-Agent）退回
        name = lambda kind: cell_check(object_type, row, kind)  # noqa: E731
        before = read()
        assert before['status'] == row['from'], (object_type, row, before)
        flow.deny(wrong, body(wrong), codes={'FORBIDDEN'})
        check(name('refused'), read() == before)

        prepared = flow.prepare(right, body(right))
        receipt = flow.commit(right, prepared)
        after = read()
        if row['from'] == row['to']:
            producer = before['event_id']
        elif object_type == 'Issue' and row['to'] == 'disposed':
            producer = None  # 已处置的问题不在读投影的 open_issues 里
        else:
            producer = receipt['result']['event_id']
        check(name('enter'), after == {'status': row['to'], 'event_id': producer} and entered(receipt))

        replay = flow.commit(right, deepcopy(prepared))
        replayed = replay['receipt_id'] == receipt['receipt_id'] and len(events_of(receipt)) == 1 and read() == after
        if repeats(object_type, row):
            flow.commit(right, flow.prepare(right, body(right)))
        else:
            flow.deny(right, body(right), codes={'INVALID_STATE'})
        check(name('idempotent'), replayed and read() == after)

    def object_rows(object_type, new, path, params):
        """对象的各行：new(row) 建一个新对象，path(row) 是推到起始状态的步骤 [(人, 动作, 参数)]。"""
        for row in tables()[object_type]['transitions']:
            oid = new(row)
            for actor, kind, step in path(row):
                act(actor, kind, oid, {**step, **(declared(actor, f'{oid}@1') if kind != 'world_assign' else {})})
            target = None
            if row['action'] == 'world_confirm_review':  # 目标是以周期目标为主体的期末快照
                closing = snapshot('a', oid, 'goal_state', now - timedelta(seconds=30), source,
                                   {'progress': {'text': '期末（状态表逐格）'}})
                target, subject = closing['target'], closing['object_id']
            else:
                subject = oid

            def body(actor, row=row, oid=oid, subject=subject, target=target):
                extra = declared(actor, f'{oid}@1') if row['action'] != 'world_assign' else {}
                return flow.targeted(row['action'], subject, {**params(row), **extra}, target)

            run_row(object_type, row, body, lambda oid=oid: life(oid))

    # ================================================================ 公共的锚定对象
    company, unit = made['Company'], made['ResponsibilityUnit']
    source = flow.record('ceo', {'category': 'meeting', 'subject_refs': [ref(company['object_id'])],
                                 'occurred_at': utc(now - timedelta(seconds=60)),
                                 'content': {'text': '状态表逐格的经营复盘会（#65）'}})['result']
    review = snapshot('ceo', company['object_id'], 'company_review', now - timedelta(seconds=45), source,
                      {'results': {'text': '逐格验收用的公司复盘'}})
    act('ceo', 'world_confirm_review', review['object_id'], target=review['target'])
    goal_ltg = flow.create('a', 'LongTermGoal', 'a', {
        'title': '状态表逐格的单元长期目标', 'scope': 'unit', 'horizon': '2030', 'parent_ref': ref(unit['object_id']),
        'goal_ref': made['LongTermGoal']['ref']})['result']['object_id']
    act('ceo', 'world_confirm_long_term_goal', goal_ltg, {'outcome': 'accepted'})

    def period_goal(title):
        return flow.create('a', 'PeriodGoal', 'a', {'title': title, 'period': next(periods), 'goal_ref': ref(goal_ltg),
                                                    'review_ref': review['ref']})['result']['object_id']

    anchor = period_goal('状态表逐格的周期目标')
    act('a', 'world_commit_period_goal', anchor)
    act('ceo', 'world_confirm_period_goal', anchor, {'outcome': 'accepted'})

    def mission(title):
        oid = flow.create('a', 'Mission', 'a', {'title': title, 'goal_ref': ref(anchor), 'blocks': {
            'play': {'text': 'Play：状态表逐格。'}}})['result']['object_id']
        flow.assign('a', oid, who['owner_a'])
        return oid

    # ================================================================ Mission
    commit, confirm = ('owner_a', 'world_commit_mission', {}), ('a', 'world_confirm_mission', {'outcome': 'accepted'})
    start, deliver = ('owner_a', 'world_start', {}), ('owner_a', 'world_deliver', {})
    ladder = {'draft': [], 'committed': [commit], 'established': [commit, confirm],
              'in_progress': [commit, confirm, start], 'delivered': [commit, confirm, start, deliver],
              'adjusting': [commit, confirm, start, deliver, ('a', 'world_reject', {})],
              'closed': [commit, confirm, start, deliver, ('a', 'world_accept', {})]}
    object_rows('Mission', lambda row: mission(f"逐格 Mission（{row['from']}）"), lambda row: ladder[row['from']],
                lambda row: {'outcome': row['outcome']} if row['outcome'] else {})

    # ================================================================ Task 与 Activity
    # Task 挂在一条已指派 Owner 的 Mission 下；Activity 挂在一条指派给 ic_a 的 Task 下，由 Agent 执行。
    above = {'Task': mission('逐格 Task 的上级 Mission')}
    above['Activity'] = flow.create('a', 'Task', 'a', {'title': '逐格 Activity 的上级 Task',
                                                       'parent_ref': ref(above['Task'])})['result']['object_id']
    act('owner_a', 'world_assign', above['Activity'], {'principal_id': who['ic_a']})
    for object_type, responsible in (('Task', 'ic_a'), ('Activity', 'agent_a')):
        up, down = RIGHT[object_type]['world_assign'], RIGHT[object_type]['world_start']
        assign = (up, 'world_assign', {'principal_id': who[responsible]})
        steps = {'unassigned': [], 'assigned': [assign], 'in_progress': [assign, (down, 'world_start', {})],
                 'delivered': [assign, (down, 'world_start', {}), (down, 'world_deliver', {})]}
        steps['adjusting'] = steps['delivered'] + [(up, 'world_reject', {})]
        steps['closed'] = steps['delivered'] + [(up, 'world_accept', {})]
        object_rows(object_type,
                    lambda row, t=object_type: flow.create('a', t, 'a', {
                        'title': f"逐格 {t}（{row['from']}）", 'parent_ref': ref(above[t])})['result']['object_id'],
                    lambda row, steps=steps: steps[row['from']],
                    lambda row, r=responsible: {'principal_id': who[r]} if row['action'] == 'world_assign' else {})

    # ================================================================ 周期目标与长期目标
    pg_commit, pg_confirm = ('a', 'world_commit_period_goal', {}), ('ceo', 'world_confirm_period_goal',
                                                                    {'outcome': 'accepted'})
    pg_ladder = {'draft': [], 'committed': [pg_commit], 'confirmed': [pg_commit, pg_confirm]}
    object_rows('PeriodGoal', lambda row: period_goal(f"逐格周期目标（{row['from']}）"),
                lambda row: pg_ladder[row['from']], lambda row: {'outcome': row['outcome']} if row['outcome'] else {})
    ltg_ladder = {'draft': [], 'confirmed': [('ceo', 'world_confirm_long_term_goal', {'outcome': 'accepted'})]}
    object_rows('LongTermGoal', lambda row: flow.create('a', 'LongTermGoal', 'a', {
        'title': f"逐格长期目标（{row['from']}）", 'scope': 'unit', 'horizon': '2031',
        'parent_ref': ref(unit['object_id']), 'goal_ref': made['LongTermGoal']['ref']})['result']['object_id'],
        lambda row: ltg_ladder[row['from']], lambda row: {'outcome': row['outcome']} if row['outcome'] else {})

    # ================================================================ Strategy（每行一个新的草稿 Strategy）
    def designate(*people):
        return ('ceo', 'world_assign_strategy_round', {'principal_ids': [who[p] for p in people]})

    agreed = [designate('a'), ('a', 'world_agree_strategy', {})]
    effective = agreed + [('ceo', 'world_confirm_strategy', {'outcome': 'accepted'})]

    def strategy_path(row):
        if row['action'] == 'world_agree_strategy':  # 本轮未齐：两人里先记一条；本轮补齐：只指定一人
            return [designate('a', 'b')] if row['guard'] == 'round_incomplete' else [designate('a')]
        return {'draft': [], 'agreed': agreed, 'effective': effective}[row['from']]

    def strategy_params(row):
        if row['action'] == 'world_assign_strategy_round':
            return {'principal_ids': [who['a']]}
        return {'outcome': row['outcome']} if row['outcome'] else {}

    object_rows('Strategy', lambda row: flow.create('ceo', 'Strategy', 'company', {
        'title': f"逐格 Strategy（{row['from']}）", 'parent_ref': ref(company['object_id']),
        'blocks': {'choices': {'text': '逐格验收用的战略选择。'}}})['result']['object_id'],
        strategy_path, strategy_params)

    # ================================================================ Issue（每行一个新的问题组件）
    carrier = mission('逐格 Issue 的主受影响 Mission')
    for step in (commit, confirm, start):
        act(step[0], step[1], carrier, step[2])
    scene = ref(carrier)
    sync = flow.record('agent_a', {'category': 'other', 'subject_refs': [scene], 'occurred_at': utc(now - timedelta(seconds=20)),
                                   'content': {'text': '逐格 Issue 的每周同步'}, **declared('agent_a', scene)})['result']
    issue_rows = tables()['Issue']['transitions']
    carried = flow.refresh('agent_a', {
        'title': '逐格 Issue 快照', 'subject_ref': scene, 'as_of': utc(now - timedelta(seconds=10)),
        'payload_type': 'execution_state', 'source_event_refs': [f"event:{sync['event_id']}"],
        'blocks': {'issues': {'components': [
            {'id': f'cell-{n:02d}', 'type': 'issue', 'text': f'逐格问题 {n}',
             'attributes': {'core_question': f'逐格问题 {n} 要不要处理？'}} for n in range(len(issue_rows))]}}},
        declared('agent_a', scene)['declaration'])['result']

    def issue_command(actor, kind, issue_ref, params=None):
        return flow.command(kind, {'issue_ref': issue_ref, **(params or {}), **declared(actor, scene)})

    def issue_life(cid, raised):
        entry = {item['component_id']: item for item in view(carrier)['records']['open_issues']}.get(cid)
        if entry is not None:
            return {'status': entry['lifecycle']['status'], 'event_id': entry['lifecycle']['event_id']}
        return {'status': 'disposed' if raised['yes'] else 'not_raised', 'event_id': None}

    raise_, route = ('agent_a', 'world_raise_issue', {}), ('agent_a', 'world_route_issue', {'to_principal_id': who['owner_a']})
    issue_ladder = {'not_raised': [], 'pending_routing': [raise_], 'routed': [raise_, route],
                    'owned': [raise_, route, ('owner_a', 'world_own_issue', {})],
                    'forming': [raise_, route, ('agent_a', 'world_return_issue', {})]}
    for n, row in enumerate(issue_rows):
        cid = f'cell-{n:02d}'
        issue_ref = f"{carried['ref']}#issues/{cid}"
        for actor, kind, params in issue_ladder[row['from']]:
            flow.commit(actor, flow.prepare(actor, issue_command(actor, kind, issue_ref, params)))
        if row['disposition']:
            params = {'disposition': row['disposition'], 'content': REASON}
        elif row['action'] == 'world_route_issue':  # 已路由时改路由给单元 DRI
            params = {'to_principal_id': who['a' if row['from'] == 'routed' else 'owner_a']}
        else:
            params = {}
        raised = {'yes': row['from'] != 'not_raised'}
        run_row('Issue', row,
                lambda actor, kind=row['action'], issue_ref=issue_ref, params=params:
                    issue_command(actor, kind, issue_ref, params),
                lambda cid=cid, raised=raised: issue_life(cid, raised),
                lambda receipt, row=row: receipt['result']['issue']['status'] == row['to'])

    # ================================================================ 矩阵里另补的两格
    rerun = mission('逐格 Mission 的一轮退回')
    for step in (commit, confirm):
        act(step[0], step[1], rerun, step[2])
    before = view(rerun)
    opened = act('owner_a', 'world_commit_mission', rerun, {'payload': {'blocks': {'play': {'text': '候选：换打法'}}}})
    during = view(rerun)
    act('a', 'world_confirm_mission', rerun, {'outcome': 'returned', 'content': {'text': '打法变化不成立'}})
    after = view(rerun)
    play = lambda read: next(b['text'] for b in read['business']['blocks'] if b['id'] == 'play')  # noqa: E731
    check('mission_a_returned_round_is_void_and_nothing_is_written_back',
          during['business']['round']['opened_by_event_id'] == opened['event_id'] and after['business']['round'] is None
          and after['business']['version'] == before['business']['version'] == during['business']['version']
          and play(after) == play(before) == 'Play：状态表逐格。'
          and after['business']['formal'] == before['business']['formal']
          and after['records']['lifecycle'] == before['records']['lifecycle'])

    sent = {'category': 'meeting', 'subject_refs': [ref(rerun)], 'occurred_at': utc(now - timedelta(seconds=5)),
            'content': {'text': '同一场会，两个幂等键'}}
    first, second = flow.record('a', deepcopy(sent)), flow.record('a', deepcopy(sent))
    listed = [item for item in flow.events('outsider', rerun)['events'] if item['action'] == 'world_record_event']
    check('the_same_external_event_under_two_keys_is_recorded_twice',
          first['receipt_id'] != second['receipt_id']
          and first['result']['event_id'] != second['result']['event_id']
          and {item['event_id'] for item in listed} == {first['result']['event_id'], second['result']['event_id']}
          and all(item['occurred_at'] == sent['occurred_at'] for item in listed))
