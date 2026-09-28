"""tkos.world/0.2 独立验收的 Issue：问题组件的提出、路由、承接、处置与退回形成（票 #61，契约第 8、9、11、13、15.1 节）。

放在列对象之后、撤销 CEO 指派之前跑，对象都新建，不动前面场景的主干：单元 a 里一条已开始的 Mission（Owner 是 owner_a，
DRI 是单元 a 的 DRI），其下一条指派给 ic_a 的 Task。单元里的 Co-Agent（agent_a，在单元 a 持 AGENT，即 MF）先记一条
每周同步的外部事件，再以它为来源事件写 Mission 的执行状态快照，问题组件都在快照的 issues 块里；问题的身份是
（Mission，组件 id），后续快照带同一个 id 是同一个问题。

- 状态表的每一格经 HTTP 各走一条，每步核对回执给出的 Issue 状态与读投影 records.open_issues（状态、推出它的事件、
  当前路由的承接人、已承接的承接人）；六类处置各一条，进入状态按登记。
- 拒绝（错误码，prepare 与 commit 两个入口上库快照都不变）：处理中与已处置的重复提出、非承接人承接或处置、Agent
  承接与处置、不在主干上的人与没有该域角色的 Agent 提出、非路由者非承接人退回、状态表没列的组合、缺理由的处置、
  承接人不是 scope 内有效的人、issue_ref 指向的不是快照里的问题组件、带目标或代记。
- 承接人不限单元（#69，补 44）：路由给另一单元的 DRI，他本人承接、退回形成与处置都放行；另一 scope 的人与 Agent 仍被拒。
- 事件：每个动作恰好一条 0.2 的 Issue 记录事件，subject_refs 是问题组件的组件引用与 Mission 的对象引用，路由的
  detail 写承接人，处置写 disposition 与理由；不出修订、不改任何对象行，Mission 与 Task 的生命周期始终不变。
- 同键重放返回原回执；更正可以指向 Issue 事件，取事件给出被更正关系，问题的状态不变；复发用新 id 并引用原问题。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from acceptance.method_independent.fixture import uid

V02 = 'tkos.world/0.2'
AGENTS = {'agent', 'agent_a', 'agent_company', 'agent_ceo_a', 'tianshu'}
DISPOSITIONS = [('no_action_close', 'disposed'), ('current_layer_action', 'disposed'), ('roll_forward', 'disposed'),
                ('immediate_reopen', 'disposed'), ('route_escalate', 'pending_routing'), ('pushback', 'forming')]
OPEN_ISSUE = {'component_id', 'issue_ref', 'text', 'core_question', 'responsible_hint', 'as_of', 'lifecycle',
              'route_target', 'owner'}


def utc_now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%SZ')


def issues(book, h, f, flow, trunk):
    check = book.check
    made = trunk['made']
    scope, actor_id = f['scope_id'], {name: actor['principal_id'] for name, actor in f['actors'].items()}

    def act(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    # ---------------------------------------------------------------- 播种：一条已开始的 Mission 与它的 Task
    goal = flow.create('a', 'PeriodGoal', 'a', {'title': 'Issue 目标（#61）', 'period': '2026-12',
                                               'goal_ref': made['LongTermGoal.unit']['ref']})['result']
    act('a', 'world_commit_period_goal', goal['object_id'])
    act('ceo', 'world_confirm_period_goal', goal['object_id'], {'outcome': 'accepted'})
    mission = flow.create('a', 'Mission', 'a', {'title': 'Issue Mission（#61）', 'goal_ref': goal['ref'], 'blocks': {
        'play': {'text': 'Play：问题经提出、路由、承接与处置流转。'}}})['result']
    mid = mission['object_id']
    flow.assign('a', mid, actor_id['owner_a'])
    act('owner_a', 'world_commit_mission', mid)
    act('a', 'world_confirm_mission', mid, {'outcome': 'accepted'})
    started = act('owner_a', 'world_start', mid)
    task = flow.create('a', 'Task', 'a', {'title': 'Issue Task（#61）', 'parent_ref': mission['ref'], 'blocks': {
        'acceptance': {'components': [{'id': 'iss-t1', 'type': 'acceptance_criterion', 'text': '试点如期开始'}]}}}
    )['result']
    act('owner_a', 'world_assign', task['object_id'], {'principal_id': actor_id['ic_a']})
    criterion = f"{task['ref']}#acceptance/iss-t1"

    declared = {'scene': mission['ref'], 'trigger': 'Co-Agent 周检（#61）', 'human_acceptance': {'required': False}}
    t0, t1, t2 = utc_now(seconds=-30), utc_now(seconds=-20), utc_now(seconds=-10)
    sync = flow.record('agent_a', {'category': 'other', 'subject_refs': [mission['ref']], 'occurred_at': t0,
                                   'content': {'text': '天枢每周同步（#61）'}, 'declaration': declared})['result']
    source = f"event:{sync['event_id']}"

    def component(cid, question, text=None, **attributes):
        return {'id': cid, 'type': 'issue', 'text': text or question,
                'attributes': {'core_question': question, **attributes}}

    def snapshot(title, as_of, components, **blocks):
        payload = {'title': title, 'subject_ref': mission['ref'], 'as_of': as_of, 'payload_type': 'execution_state',
                   'source_event_refs': [source], 'blocks': {'issues': {'components': components}, **blocks}}
        return flow.refresh('agent_a', payload, declared)['result']

    main_question = '试点要不要推迟一周？'
    first = snapshot('第 1 周（#61）', t1, [
        component('iss-main', main_question, responsible_hint=actor_id['owner_a']),
        *[component(f'iss-d{n}', f'处置 {name} 的问题') for n, (name, _) in enumerate(DISPOSITIONS, 1)],
        component('iss-x', '拒绝用例的问题'), component('iss-quiet', '只记在快照里、从没提出的问题'),
        component('iss-cross', '要不要请单元 b 接手客户对接？')],
        progress={'components': [{'id': 'todo:61', 'type': 'progress_item', 'text': '搭环境'}]})

    def ref(snap, cid):
        return f"{snap['ref']}#issues/{cid}"

    def body(actor, kind, issue_ref, params=None, key=None):
        params = {'issue_ref': issue_ref, **(params or {})}
        if actor in AGENTS and 'declaration' not in params:
            params['declaration'] = declared
        return flow.command(kind, params, key=key)

    def issue(actor, kind, issue_ref, params=None):
        """记一条 Issue 动作（不带目标，expected_versions 为空），Agent 带写入声明；返回回执。"""
        return flow.commit(actor, flow.prepare(actor, body(actor, kind, issue_ref, params)))

    def deny(actor, kind, issue_ref, codes, params=None, says=None, declare=True):
        command = body(actor if declare else None, kind, issue_ref, params)
        flow.deny(actor, command, codes=codes, says=says)

    def event_row(event_id):
        return flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s AND event_id=%s', (scope, event_id))[0]

    def view():
        return flow.read('outsider', mid)

    def opened():
        return {item['component_id']: item for item in view()['records']['open_issues']}

    def head_row(oid):
        return flow.rows('SELECT object_version, latest_revision_id, effective_revision_id, lifecycle_status '
                         'FROM gov_objects WHERE scope_id=%s AND object_id=%s', (scope, oid))[0]

    def pinned(text):
        """引用的钉定结构（对象、块或组件形式），修订 id 取独立的只读 SQL。"""
        oid, rest = text.split('@', 1)
        version = int(rest.split('#', 1)[0])
        block = rest.split('#', 1)[1].split('/', 1)[0] if '#' in rest else None
        comp = rest.split('/', 1)[1] if '/' in rest else None
        revision = flow.rows('SELECT revision_id FROM gov_object_revisions WHERE scope_id=%s AND object_id=%s '
                             'AND object_version=%s', (scope, oid, version))[0]['revision_id']
        return {'object_id': oid, 'object_version': version, 'revision_id': str(revision), 'block': block,
                'component': comp}

    def at_state(receipt, status, producer=None, *, route_target=None, owner=None, cid=None, issue_ref=None):
        """回执给出的 Issue 状态，与读投影里这一问题的状态、推出它的事件、承接人一致。"""
        result = receipt['result']
        entry = opened().get(cid or result['issue']['component_id'])
        ok = (result['contract_version'] == V02 and result['issue']['status'] == status
              and result['issue']['primary_affected_object_id'] == mid)
        if status == 'disposed':
            return ok and entry is None
        return (ok and entry is not None and entry['lifecycle']['status'] == status
                and entry['lifecycle']['event_id'] == (producer or result['event_id'])
                and (entry['route_target'] or {}).get('principal_id') == route_target
                and (entry['owner'] or {}).get('principal_id') == owner
                and (issue_ref is None or entry['issue_ref']['ref'] == issue_ref))

    before_mission, before_task = head_row(mid), head_row(task['object_id'])
    life_before = view()['records']['lifecycle']
    revisions_before = flow.rows('SELECT count(*) AS n FROM gov_object_revisions WHERE scope_id=%s', (scope,))[0]['n']

    # ---------------------------------------------------------------- 主线：iss-main 走遍提出、路由、承接与退回
    main1 = ref(first, 'iss-main')
    raise_body = flow.prepare('agent_a', body('agent_a', 'world_raise_issue', main1, {
        'content': {'text': '试点排期与客户冲突，要人判断。', 'refs': [criterion, source]}}))
    raised = flow.commit('agent_a', raise_body)
    row = event_row(raised['result']['event_id'])
    receipt = flow.rows('SELECT * FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s',
                        (scope, raised['receipt_id']))[0]
    check('the_co_agent_raises_an_issue_component_from_not_raised_into_pending_routing',
          at_state(raised, 'pending_routing', issue_ref=main1)
          and raised['result']['issue']['component_id'] == 'iss-main'
          and raised['result']['declaration']['scene']['ref'] == mission['ref'])
    check('an_issue_event_is_one_0_2_record_event_on_the_component_and_its_primary_affected_object',
          (row['kind'], row['contract_version'], row['category'], row['outcome'], row['disposition'], row['detail'])
          == ('issue.raised', V02, None, None, None, None)
          and row['subject_refs'] == [pinned(main1), pinned(f"{mid}@{view()['business']['version']}")]
          and str(row['principal_id']) == actor_id['agent_a'] and row['on_behalf_of'] is None
          and row['occurred_at'] == row['recorded_at'] and str(row['action_id']) == raised['receipt_id']
          and [r['ref'] if 'ref' in r else r for r in raised['result']['subject_refs']] == [
              main1, f"{mid}@{view()['business']['version']}"]
          and row['content']['refs'] == [pinned(criterion), {'event_id': sync['event_id']}]
          and receipt['object_versions'] == [] and receipt['target_object_id'] is None
          and receipt['action_type'] == 'world_raise_issue')
    replayed = flow.clients['agent_a'].json('POST', '/v1/actions', deepcopy(raise_body))
    check('the_same_key_replays_the_raise_into_the_original_receipt_and_no_second_event',
          replayed['receipt_id'] == raised['receipt_id']
          and len(flow.rows("SELECT 1 FROM gov_world_events WHERE scope_id=%s AND kind='issue.raised' "
                            "AND action_id=%s", (scope, raised['receipt_id']))) == 1)
    deny('agent_a', 'world_raise_issue', main1, {'INVALID_STATE'})
    check('an_issue_in_process_is_not_raised_again_while_pending_routing')

    routed = issue('agent_a', 'world_route_issue', main1, {'to_principal_id': actor_id['a']})
    row = event_row(routed['result']['event_id'])
    check('the_co_agent_routes_a_pending_issue_to_a_person',
          at_state(routed, 'routed', route_target=actor_id['a'])
          and row['kind'] == 'issue.routed' and row['detail'] == {'to_principal_id': actor_id['a']}
          and routed['result']['detail'] == {'to_principal_id': actor_id['a']})
    deny('a', 'world_raise_issue', main1, {'INVALID_STATE'})
    check('an_issue_in_process_is_not_raised_again_while_routed')

    # 后续快照带同一个 id：同一个问题的新情况；读投影改指最新的那条。
    second = snapshot('第 2 周（#61）', t2, [component('iss-main', main_question, '客户回复：下周才能开会。',
                                                    responsible_hint=actor_id['owner_a'])])
    main2 = ref(second, 'iss-main')
    deny('agent_a', 'world_raise_issue', main2, {'INVALID_STATE'})
    check('the_same_id_in_a_later_snapshot_is_the_same_issue_and_is_not_raised_again')
    entry = opened()['iss-main']
    check('the_open_issue_reads_from_the_latest_snapshot_that_carries_it',
          entry['issue_ref']['ref'] == main2 and entry['text'] == '客户回复：下周才能开会。'
          and entry['core_question'] == main_question and entry['responsible_hint'] == actor_id['owner_a']
          and entry['as_of'] == t2 and entry['lifecycle']['event_id'] == routed['result']['event_id'])

    rerouted = issue('owner_a', 'world_route_issue', main2, {'to_principal_id': actor_id['ic_a']})
    check('a_responsible_person_up_the_spine_reroutes_a_routed_issue_and_the_stage_keeps_its_producer',
          at_state(rerouted, 'routed', routed['result']['event_id'], route_target=actor_id['ic_a'])
          and event_row(rerouted['result']['event_id'])['detail'] == {'to_principal_id': actor_id['ic_a']})
    deny('a', 'world_own_issue', main2, {'FORBIDDEN'}, says='route_target')
    check('the_earlier_route_target_does_not_own_a_rerouted_issue')
    deny('agent_a', 'world_own_issue', main2, {'FORBIDDEN'}, declare=False)
    deny('agent_ceo_a', 'world_own_issue', main2, {'FORBIDDEN'}, declare=False)
    check('an_agent_does_not_own_an_issue_even_holding_a_role')
    owned = issue('ic_a', 'world_own_issue', main1)  # 较早快照里的同一组件：同一个问题
    check('the_route_target_owns_the_issue_in_person',
          at_state(owned, 'owned', route_target=actor_id['ic_a'], owner=actor_id['ic_a'])
          and str(event_row(owned['result']['event_id'])['principal_id']) == actor_id['ic_a'])
    deny('owner_a', 'world_raise_issue', main2, {'INVALID_STATE'})
    check('an_issue_in_process_is_not_raised_again_while_owned')
    deny('agent_a', 'world_route_issue', main2, {'INVALID_STATE'}, {'to_principal_id': actor_id['a']})
    check('an_owned_issue_is_not_rerouted_it_is_disposed_or_returned')
    deny('owner_a', 'world_dispose_issue', main2, {'FORBIDDEN'},
         {'disposition': 'current_layer_action', 'content': {'text': '我来处理'}})
    deny('agent_a', 'world_dispose_issue', main2, {'FORBIDDEN'},
         {'disposition': 'current_layer_action', 'content': {'text': '我来处理'}}, declare=False)
    check('only_the_owner_in_person_disposes_an_issue')
    deny('owner_a2', 'world_return_issue', main2, {'FORBIDDEN'})
    check('a_person_neither_router_nor_owner_does_not_return_an_owned_issue')
    back = issue('ic_a', 'world_return_issue', main2, {'content': {'text': '缺客户排期，退回补齐。'}})
    check('the_owner_returns_an_owned_issue_to_forming',
          at_state(back, 'forming') and event_row(back['result']['event_id'])['content']['text'] == '缺客户排期，退回补齐。')
    deny('agent_a', 'world_route_issue', main2, {'INVALID_STATE'}, {'to_principal_id': actor_id['a']})
    check('a_forming_issue_is_raised_again_before_it_is_routed')
    again = issue('owner_a', 'world_raise_issue', main2)
    check('a_responsible_person_up_the_spine_raises_a_forming_issue_again_without_a_declaration',
          at_state(again, 'pending_routing') and 'declaration' not in again['result'])
    rerouted = issue('a', 'world_route_issue', main2, {'to_principal_id': actor_id['owner_a']})
    deny('owner_a2', 'world_return_issue', main2, {'FORBIDDEN'})
    check('a_person_neither_router_nor_owner_does_not_return_a_routed_issue')
    deny('ic_a', 'world_return_issue', main2, {'FORBIDDEN'})
    deny('ic_a', 'world_own_issue', main2, {'FORBIDDEN'})
    check('the_owner_of_an_earlier_routing_neither_returns_nor_owns_the_issue_routed_again')
    deny('agent_a', 'world_return_issue', main2, {'INVALID_REQUEST'}, declare=False)
    check('an_agent_returns_an_issue_only_with_its_declaration')
    returned = issue('agent_a', 'world_return_issue', main2)
    check('the_co_agent_as_a_router_returns_a_routed_issue_to_forming',
          at_state(returned, 'forming') and str(event_row(returned['result']['event_id'])['principal_id'])
          == actor_id['agent_a'])

    # ---------------------------------------------------------------- 六类处置
    reason = {'text': '最低理由：本层判断后的处置。', 'refs': [main1]}
    denied_first = False
    results = {}
    for n, (name, to) in enumerate(DISPOSITIONS, 1):
        cref = ref(first, f'iss-d{n}')
        issue('agent_a', 'world_raise_issue', cref)
        issue('agent_a', 'world_route_issue', cref, {'to_principal_id': actor_id['owner_a']})
        issue('owner_a', 'world_own_issue', cref)
        if not denied_first:
            deny('owner_a', 'world_dispose_issue', cref, {'INVALID_REQUEST'}, {'disposition': name})
            deny('owner_a', 'world_dispose_issue', cref, {'INVALID_REQUEST'},
                 {'disposition': name, 'content': {'text': '  ', 'refs': [main1]}})
            deny('owner_a', 'world_dispose_issue', cref, {'INVALID_REQUEST'},
                 {'disposition': 'Route', 'content': reason})
            check('a_disposition_without_its_reason_or_outside_the_six_is_refused')
            denied_first = True
        results[name] = issue('owner_a', 'world_dispose_issue', cref, {'disposition': name, 'content': reason})
        row = event_row(results[name]['result']['event_id'])
        check(f'the_owner_disposes_with_{name}_into_{to}',
              at_state(results[name], to, cid=f'iss-d{n}',
                       owner=None, route_target=None)
              and (row['kind'], row['disposition'], row['content']['text']) == ('issue.disposed', name, reason['text'])
              and row['content']['refs'] == [pinned(main1)] and results[name]['result']['disposition'] == name)
    check('dispositions_leave_the_business_objects_their_lifecycles_and_rows_as_they_were',
          view()['records']['lifecycle'] == life_before == {**life_before, 'status': 'in_progress',
                                                            'event_id': started['event_id']}
          and head_row(mid) == before_mission and head_row(task['object_id']) == before_task
          and flow.read('outsider', task['object_id'])['records']['lifecycle']['status'] == 'assigned')
    events = {e['event_id']: e for e in flow.events('outsider', mid)['events']}
    check('the_events_of_the_primary_affected_object_list_the_issue_events_with_their_action_and_disposition',
          all(events[results[name]['result']['event_id']]['disposition'] == name
              and events[results[name]['result']['event_id']]['action'] == 'world_dispose_issue'
              and events[results[name]['result']['event_id']]['class'] == 'record' for name, _ in DISPOSITIONS)
          and events[routed['result']['event_id']]['detail'] == {'to_principal_id': actor_id['a']}
          and events[raised['result']['event_id']]['subject_refs'][0]['ref'] == main1)
    snapshot_events = {e['event_id'] for e in flow.events('outsider', first['object_id'])['events']}
    check('the_events_of_the_snapshot_list_the_issue_events_on_its_components',
          {raised['result']['event_id'], owned['result']['event_id']} <= snapshot_events
          and back['result']['event_id'] not in snapshot_events)

    # 已处置的不再提出，也不再流转；复发用新 id 并在内容里引用原问题。
    d1 = ref(first, 'iss-d1')
    deny('agent_a', 'world_raise_issue', d1, {'INVALID_STATE'})
    deny('a', 'world_route_issue', d1, {'INVALID_STATE'}, {'to_principal_id': actor_id['owner_a']})
    check('a_disposed_issue_is_not_raised_or_routed_again')
    third = snapshot('第 3 周（#61）', utc_now(seconds=-1), [
        {**component('iss-d1-again', '处置 no_action_close 的问题（复发）'), 'refs': [d1]}])
    recur = issue('agent_a', 'world_raise_issue', ref(third, 'iss-d1-again'), {
        'content': {'text': '同一问题复发，原问题已关闭。', 'refs': [d1, f"event:{results['no_action_close']['result']['event_id']}"]}})
    check('a_recurrence_is_raised_under_a_new_id_that_references_the_original',
          at_state(recur, 'pending_routing')
          and event_row(recur['result']['event_id'])['content']['refs']
          == [pinned(d1), {'event_id': results['no_action_close']['result']['event_id']}])

    # 转交或上报后回到待路由，由路由者改给 CEO（在单元 a 持 CEO），CEO 本人承接。
    d5 = ref(first, 'iss-d5')
    escalated = issue('a', 'world_route_issue', d5, {'to_principal_id': actor_id['ceo']})
    routed_to_ceo = at_state(escalated, 'routed', route_target=actor_id['ceo'])
    ceo_owned = issue('ceo', 'world_own_issue', d5)
    check('an_escalated_issue_is_routed_again_and_the_ceo_owns_it',
          routed_to_ceo and at_state(ceo_owned, 'owned', route_target=actor_id['ceo'], owner=actor_id['ceo']))

    # 承接人不限单元（#69，补 44）：单元 b 的 DRI 在单元 a（主受影响对象所在的域）不持任何角色。路由给他，他本人承接、
    # 退回形成、再承接后处置都放行（承接、处置与退回形成按 scope 判权）；另一 scope 的人是 NOT_FOUND，Agent 与别的人
    # 仍按记录者类别被拒。
    cross = ref(first, 'iss-cross')
    issue('agent_a', 'world_raise_issue', cross)
    to_b = issue('agent_a', 'world_route_issue', cross, {'to_principal_id': actor_id['b']})
    routed_to_b = at_state(to_b, 'routed', route_target=actor_id['b'], cid='iss-cross')
    owned_b = issue('b', 'world_own_issue', cross)
    check('an_issue_is_routed_to_a_person_of_another_unit_who_owns_it_in_person',
          routed_to_b
          and at_state(owned_b, 'owned', route_target=actor_id['b'], owner=actor_id['b'], cid='iss-cross')
          and str(event_row(owned_b['result']['event_id'])['principal_id']) == actor_id['b'])
    across = {'disposition': 'current_layer_action', 'content': {'text': '单元 b 接手对接'}}
    deny('foreign_ceo', 'world_dispose_issue', cross, {'NOT_FOUND'}, across)
    deny('owner_a2', 'world_dispose_issue', cross, {'FORBIDDEN'}, across)
    deny('a', 'world_dispose_issue', cross, {'FORBIDDEN'}, across)
    check('a_person_outside_the_scope_or_other_than_the_owner_does_not_dispose_an_issue_owned_across_units')
    back_b = issue('b', 'world_return_issue', cross, {'content': {'text': '单元 b 缺客户资料，退回补齐。'}})
    check('the_owner_from_another_unit_returns_the_issue_to_forming',
          at_state(back_b, 'forming', cid='iss-cross')
          and str(event_row(back_b['result']['event_id'])['principal_id']) == actor_id['b'])
    issue('agent_a', 'world_raise_issue', cross)
    issue('agent_a', 'world_route_issue', cross, {'to_principal_id': actor_id['b']})
    deny('foreign_ceo', 'world_own_issue', cross, {'NOT_FOUND'})
    deny('agent_a', 'world_own_issue', cross, {'FORBIDDEN'}, declare=False)
    deny('c', 'world_own_issue', cross, {'FORBIDDEN'})
    check('a_person_outside_the_scope_or_an_agent_does_not_own_an_issue_routed_across_units')
    issue('b', 'world_own_issue', cross)
    disposed_b = issue('b', 'world_dispose_issue', cross, across)
    row = event_row(disposed_b['result']['event_id'])
    check('the_owner_from_another_unit_disposes_the_issue',
          at_state(disposed_b, 'disposed', cid='iss-cross')
          and (row['kind'], row['disposition'], row['content']['text']) == ('issue.disposed', 'current_layer_action',
                                                                            '单元 b 接手对接')
          and str(row['principal_id']) == actor_id['b'])

    # ---------------------------------------------------------------- 状态表没列的组合、提出与路由的拒绝
    x = ref(first, 'iss-x')
    issue('agent_a', 'world_raise_issue', x)
    deny('owner_a', 'world_own_issue', x, {'INVALID_STATE'})
    deny('agent_a', 'world_return_issue', x, {'INVALID_STATE'})
    deny('owner_a', 'world_dispose_issue', x, {'INVALID_STATE'},
         {'disposition': 'no_action_close', 'content': {'text': '不处理'}})
    check('owning_returning_or_disposing_a_pending_issue_is_invalid_state')
    # 承接人是 scope 内有效的人（补 44）：Agent、没有任何指派的人、不存在的身份、另一 scope 的人都不行。
    for principal in (actor_id['agent_a'], f['bystander_principal_id'], uid(), actor_id['foreign_ceo']):
        deny('a', 'world_route_issue', x, {'INVALID_REQUEST'}, {'to_principal_id': principal}, says='person')
    check('an_issue_is_routed_only_to_an_active_person_of_the_scope')
    routed_x = issue('a', 'world_route_issue', x, {'to_principal_id': actor_id['owner_a']})
    deny('owner_a', 'world_dispose_issue', x, {'INVALID_STATE'},
         {'disposition': 'no_action_close', 'content': {'text': '不处理'}})
    check('disposing_a_routed_issue_that_is_not_owned_is_invalid_state')

    quiet = ref(first, 'iss-quiet')
    deny('ic_a', 'world_raise_issue', quiet, {'FORBIDDEN'})
    deny('owner_a2', 'world_raise_issue', quiet, {'FORBIDDEN'})
    check('a_person_not_responsible_up_the_spine_of_the_primary_affected_object_does_not_raise')
    deny('agent_company', 'world_raise_issue', quiet, {'FORBIDDEN'})
    deny('owner_c', 'world_raise_issue', quiet, {'FORBIDDEN'})
    check('a_principal_without_a_role_in_the_domain_of_the_primary_affected_object_does_not_raise')
    deny('agent_a', 'world_raise_issue', quiet, {'INVALID_REQUEST'}, declare=False)
    check('an_agent_raises_only_with_its_declaration')
    for bad in (f"{first['ref']}#progress/todo:61", criterion, ref(first, 'iss-none'),
                f"{first['object_id']}@2#issues/iss-quiet", f"{first['ref']}#blockers/iss-quiet"):
        deny('agent_a', 'world_raise_issue', bad, {'INVALID_REQUEST'})
    check('an_issue_ref_that_is_not_an_issue_component_of_a_snapshot_is_refused')
    deny('agent_a', 'world_raise_issue', f'{uid()}@1#issues/iss-quiet', {'NOT_FOUND'})
    check('an_issue_ref_outside_the_scope_is_not_found')
    on_behalf = {'principal_id': actor_id['owner_a'], 'external_record_id': 'tianshu-61',
                 'external_confirmed_at': utc_now(seconds=-1)}
    deny('tianshu', 'world_raise_issue', quiet, {'INVALID_REQUEST'}, {'on_behalf_of': on_behalf})
    targeted = body('agent_a', 'world_raise_issue', quiet)
    targeted['target'] = flow.target(mid)
    flow.deny('agent_a', targeted, codes={'INVALID_REQUEST'})
    check('an_issue_action_takes_neither_a_target_nor_on_behalf_of')

    # ---------------------------------------------------------------- 更正指向 Issue 事件
    correction = flow.record('agent_a', {
        'category': 'correction', 'subject_refs': [main1, mission['ref']], 'occurred_at': utc_now(seconds=-1),
        'content': {'text': '更正：第一次路由应给 Owner。'}, 'supersedes_event_id': routed['result']['event_id'],
        'declaration': declared})['result']
    events = {e['event_id']: e for e in flow.events('outsider', mid)['events']}
    check('a_correction_points_to_an_issue_event_and_leaves_the_issue_where_it_was',
          events[routed['result']['event_id']]['corrected_by'] == [correction['event_id']]
          and events[correction['event_id']]['supersedes_event_id'] == routed['result']['event_id']
          and opened()['iss-main']['lifecycle'] == {'status': 'forming', 'display_name': '形成中',
                                                    'event_id': returned['result']['event_id']})

    # ---------------------------------------------------------------- 读投影：主受影响对象是它、还没处置的问题
    final = opened()
    statuses = {cid: item['lifecycle']['status'] for cid, item in final.items()}
    check('records_open_issues_lists_the_raised_issues_of_the_object_that_are_not_disposed',
          statuses == {'iss-main': 'forming', 'iss-d5': 'owned', 'iss-d6': 'forming', 'iss-d1-again': 'pending_routing',
                       'iss-x': 'routed'}
          and list(final) == ['iss-main', 'iss-d5', 'iss-d6', 'iss-d1-again', 'iss-x']  # 按第一条 Issue 事件的顺序
          and all(set(item) == OPEN_ISSUE for item in final.values())
          and final['iss-x']['lifecycle']['event_id'] == routed_x['result']['event_id']
          and final['iss-x']['route_target']['principal_id'] == actor_id['owner_a'] and final['iss-x']['owner'] is None
          and final['iss-d5']['owner'] == {'principal_id': actor_id['ceo'], 'principal_type': 'human',
                                           'display_name': final['iss-d5']['owner']['display_name']}
          and final['iss-d1-again']['issue_ref'] == {**pinned(ref(third, 'iss-d1-again')),
                                                     'ref': ref(third, 'iss-d1-again')}
          and final['iss-d6']['issue_ref']['ref'] == ref(first, 'iss-d6'))
    check('the_open_issues_of_an_object_without_issues_are_empty',
          flow.read('outsider', task['object_id'])['records']['open_issues'] == []
          and flow.read('outsider', goal['object_id'])['records']['open_issues'] == [])
    check('issue_actions_wrote_no_revision_and_left_the_business_lifecycles_unchanged',
          flow.rows('SELECT count(*) AS n FROM gov_object_revisions WHERE scope_id=%s', (scope,))[0]['n']
          == revisions_before + 2  # 只有第 2、3 周的两条快照
          and head_row(mid) == before_mission and head_row(task['object_id']) == before_task
          and view()['records']['lifecycle'] == life_before)
