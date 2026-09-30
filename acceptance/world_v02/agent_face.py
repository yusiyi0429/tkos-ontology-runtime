"""tkos.world/0.2 独立验收的 MCP 端到端场景（票 #57）：tkos-world-mcp 子进程选 0.2，以 Agent 凭证打真 API。

照 0.1 验收里 MCP 那几项的做法（acceptance/world_v01/run.py 的 mcp_end_to_end）：经安装后的命令入口启动，源码与
API 进程同一份。工具清单是 0.2 Agent 面的五读八写（列对象随 #63 加入，它在真 API 上的行为由 listing 场景直接
打 HTTP 驱动；提出问题、路由问题、退回形成随 #61 加入，它们在真 API 上的行为由 issues 场景直接打 HTTP 驱动）；四读到真 API，取对象分三组，取上下文只交出四项；五写
经 prepare 再 commit，记录者是 Agent，事件与回执都是 0.2；Activity 由 Agent 开始、交付，Mission 由 Owner 的 Agent
开始。缺声明的写入由 HTTP 面拒绝，错误体与直接打 HTTP 的逐字相同；面外的工具与代记在发请求之前就拒绝；拒绝都不改
库。运行日志记下组件与事件引用、不含凭证。对象都新建，不动前面场景的主干。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

from acceptance.method_independent.fixture import uid
from acceptance.world_v01.fixture import ROOT

V02 = 'tkos.world/0.2'
TOOLS = ['world_deliver', 'world_get_context', 'world_get_events', 'world_get_object', 'world_get_state',
         'world_list_objects', 'world_raise_issue', 'world_record_event', 'world_refresh_state', 'world_return_issue',
         'world_revise_object', 'world_route_issue', 'world_start']


def mcp_end_to_end(book, h, f, flow, trunk, url, source):
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    check = book.check
    made = trunk['made']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}

    def act(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    # 人经 HTTP 备好场景：确认过的周期目标下一条已成立、Owner 是 owner_a 的 Mission，其下一条指派给 ic_a、带两条
    # 验收条件的 Task，再下一条指派给 agent_a 的 Activity（执行指令引用 Task 的一条验收条件）。
    goal = flow.create('a', 'PeriodGoal', 'a', {'title': 'MCP 目标（#57）', 'period': '2026-11',
                                               'goal_ref': made['LongTermGoal.unit']['ref']})['result']
    act('a', 'world_commit_period_goal', goal['object_id'])
    act('ceo', 'world_confirm_period_goal', goal['object_id'], {'outcome': 'accepted'})
    mission = flow.create('a', 'Mission', 'a', {'title': 'MCP Mission（#57）', 'goal_ref': goal['ref'], 'blocks': {
        'mission_plan': {'text': 'Play：Agent 经 MCP 推进。'}}})['result']
    flow.assign('a', mission['object_id'], actor_id['owner_a'])
    act('owner_a', 'world_commit_mission', mission['object_id'])
    act('a', 'world_confirm_mission', mission['object_id'], {'outcome': 'accepted'})
    task = flow.create('a', 'Task', 'a', {'title': 'MCP Task（#57）', 'parent_ref': mission['ref'], 'blocks': {
        'definition': {'text': '经 MCP 走一遍 0.2 的 Agent 面。', 'components': [
            {'id': 'mcp-t1', 'type': 'acceptance_criterion', 'text': '五写都经 prepare 再 commit'},
            {'id': 'mcp-t2', 'type': 'acceptance_criterion', 'text': '日志里有组件与事件引用'}]}}}
    )['result']
    act('owner_a', 'world_assign', task['object_id'], {'principal_id': actor_id['ic_a']})
    criterion = task['ref'] + '#definition/mcp-t1'
    activity = flow.create('a', 'Activity', 'a', {'title': 'MCP Activity（#57）', 'parent_ref': task['ref'], 'blocks': {
        'instruction': {'text': '按验收条件经 MCP 执行。', 'refs': [criterion]}}})['result']
    act('ic_a', 'world_assign', activity['object_id'], {'principal_id': actor_id['agent_a']})

    log_dir = h.output / 'world-mcp-v02-runs'
    token = f['actors']['agent_a']['token']
    params = StdioServerParameters(command=str(Path(sys.executable).with_name('tkos-world-mcp')), cwd=str(ROOT),
                                   env={'PYTHONPATH': str(source), 'TKOS_WORLD_API_URL': url,
                                        'TKOS_WORLD_AGENT_TOKEN': token, 'TKOS_WORLD_MCP_LOG_DIR': str(log_dir),
                                        'TKOS_WORLD_CONTRACT_VERSION': V02})
    # 场景可以是任一业务对象（契约第 9.3 节）：这里是周期目标。修订 Activity 的执行指令触及正式块，要人工验收。
    declared = {'scene': goal['ref'], 'trigger': 'MCP 端到端（0.2）', 'human_acceptance': {'required': False}}
    accepted = {**declared, 'human_acceptance': {'required': True, 'acceptor': actor_id['ic_a']}}
    moment = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    undeclared = {'category': 'other', 'subject_refs': [activity['ref']], 'occurred_at': moment,
                  'content': {'text': '没带声明'}}
    context_rows = len(flow.rows('SELECT 1 FROM gov_world_context_packs WHERE scope_id=%s', (f['scope_id'],)))

    async def session_run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(tool.name for tool in (await session.list_tools()).tools)
            done = {}

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                return result.is_error, json.loads(result.content[0].text)

            async def target(oid):
                """每次写之前重新取对象：目标取 business 组里的 object_id、revision_id 与 object_version。"""
                failed, view = await call('world_get_object', {'object_id': oid})
                assert not failed, view
                business = view['business']
                return view, {'object_id': business['object_id'], 'revision_id': business['revision_id'],
                              'expected_version': business['object_version']}

            first, at = await target(activity['object_id'])
            done['object'] = (False, first)
            done['start'] = await call('world_start', {'target': at, 'declaration': declared})
            done['record'] = await call('world_record_event', {
                'category': 'meeting', 'subject_refs': [activity['ref'], criterion], 'occurred_at': moment,
                'content': {'text': '经 MCP 记的会议：验收条件一已满足。'}, 'declaration': declared})
            source_event = f"event:{done['record'][1]['result']['event_id']}"
            done['refresh'] = await call('world_refresh_state', {'payload': {
                'title': 'MCP 快照（0.2）', 'subject_ref': activity['ref'], 'as_of': moment,
                'payload_type': 'execution_state', 'source_event_refs': [source_event],
                'blocks': {'progress': {'text': '经 MCP 写入。'}, 'issues': {'components': [
                    {'id': 'mcp-iss-1', 'type': 'issue', 'text': '日志要认组件引用',
                     'attributes': {'core_question': '组件引用要不要截到块？'}}]}}}, 'declaration': declared})
            _, at = await target(activity['object_id'])
            done['revise'] = await call('world_revise_object', {
                'target': at, 'payload': {'blocks': {'instruction': {'text': '经 MCP 补齐执行指令。', 'refs': [criterion]}}},
                'declaration': accepted})
            _, at = await target(activity['object_id'])
            done['deliver'] = await call('world_deliver', {'target': at, 'content': {'text': '经 MCP 交付。'},
                                                           'declaration': declared})
            _, mt = await target(mission['object_id'])
            done['mission_start'] = await call('world_start', {'target': mt, 'declaration': declared})
            done['events'] = await call('world_get_events', {'object_id': activity['object_id']})
            done['state'] = await call('world_get_state', {'object_id': activity['object_id']})
            done['context'] = await call('world_get_context', {'object_id': activity['object_id'],
                                                               'question': '这条 Activity 做到哪一步了？'})
            before = h.snapshot(f)
            refused = await call('world_record_event', undeclared)
            outside = [await call(name, arguments) for name, arguments in [
                ('world_deliver', {'target': mt, 'declaration': declared,
                                   'on_behalf_of': {'principal_id': actor_id['owner_a'], 'external_record_id': 'mcp-1',
                                                    'external_confirmed_at': moment}}),
                ('world_accept', {'target': at}), ('world_assign', {'target': at, 'principal_id': actor_id['ic_a']}),
                ('world_commit_mission', {'target': mt})]]
            return names, done, refused, outside, before == h.snapshot(f)

    names, done, refused, outside, unchanged = anyio.run(session_run)
    source_event = f"event:{done['record'][1]['result']['event_id']}"
    check('the_0_2_mcp_server_lists_the_agent_face_five_reads_and_eight_writes', names == TOOLS)

    packs = flow.rows('SELECT context_pack_id, principal_id, pack FROM gov_world_context_packs WHERE scope_id=%s '
                      'ORDER BY created_at, context_pack_id', (f['scope_id'],))
    view, events, state, context = (done[key][1] for key in ('object', 'events', 'state', 'context'))
    check('the_four_reads_reach_the_real_api_as_the_agent_and_read_0_2_shapes',
          all(not done[key][0] for key in ('object', 'events', 'state', 'context'))
          and set(view) == {'object_id', 'business', 'identity', 'records', 'protocol'}
          and view['business']['object_id'] == activity['object_id'] and view['protocol']['contract_version'] == V02
          and {e['event_id'] for e in events['events']} >= {done[key][1]['result']['event_id']
                                                            for key in ('start', 'record', 'deliver')}
          and state['snapshot']['object_id'] == done['refresh'][1]['result']['object_id']
          and state['snapshot']['source_event_refs'][0]['ref'] == source_event
          # 取上下文只把包 id、Markdown、覆盖与预算交给模型，分层内容按包 id 到落表的那一行里核对。
          and sorted(context) == ['budget', 'context_pack_id', 'coverage', 'markdown']
          and len(packs) == context_rows + 1 and str(packs[-1]['context_pack_id']) == context['context_pack_id']
          and str(packs[-1]['principal_id']) == actor_id['agent_a'] and packs[-1]['pack']['contract_version'] == V02
          and packs[-1]['pack']['layers'][0]['object']['object_id'] == activity['object_id']
          and context['markdown'] == packs[-1]['pack']['markdown'] and f'`{criterion}`' in context['markdown'])

    writes = ('start', 'record', 'refresh', 'revise', 'deliver', 'mission_start')
    receipts = {str(row['receipt_id']): row for row in flow.rows(
        'SELECT receipt_id, principal_id, action_type FROM gov_action_receipts WHERE scope_id=%s '
        'AND receipt_id = ANY(%s::uuid[])', (f['scope_id'], [done[key][1]['receipt_id'] for key in writes]))}
    rows = {str(row['event_id']): row for row in flow.rows(
        'SELECT event_id, kind, contract_version, principal_id FROM gov_world_events WHERE scope_id=%s '
        'AND event_id = ANY(%s::uuid[])', (f['scope_id'], [done[key][1]['result']['event_id'] for key in writes]))}
    after, mission_after = flow.read('outsider', activity['object_id']), flow.read('outsider', mission['object_id'])
    check('the_five_writes_commit_through_prepare_and_commit_as_the_agent_under_0_2',
          all(not done[key][0] and done[key][1]['status'] == 'committed'
              and done[key][1]['result']['contract_version'] == V02 for key in writes)
          and [receipts[done[key][1]['receipt_id']]['action_type'] for key in writes]
          == ['world_start', 'world_record_event', 'world_refresh_state', 'world_revise_object', 'world_deliver',
              'world_start']
          and {str(row['principal_id']) for row in receipts.values()} == {actor_id['agent_a']}
          and [rows[done[key][1]['result']['event_id']]['kind'] for key in writes]
          == ['start', 'event.recorded', 'state.refreshed', 'object.revised', 'deliver', 'start']
          and {(row['contract_version'], str(row['principal_id'])) for row in rows.values()} == {(V02, actor_id['agent_a'])})
    check('the_agent_starts_and_delivers_its_activity_and_starts_the_mission_as_the_owners_agent',
          after['records']['lifecycle']['status'] == 'delivered'
          and after['records']['lifecycle']['event_id'] == done['deliver'][1]['result']['event_id']
          and {b['id']: b['text'] for b in after['business']['blocks']}['instruction'] == '经 MCP 补齐执行指令。'
          and mission_after['records']['lifecycle']['status'] == 'in_progress'
          and mission_after['records']['lifecycle']['event_id'] == done['mission_start'][1]['result']['event_id'])

    # 同样的命令直接打 HTTP 面，拒绝的错误体应与经 MCP 拿到的逐字相同。
    direct = flow.clients['agent_a'].json('POST', '/v1/actions/prepare', {
        'action_type': 'world_record_event', 'contract_version': V02, 'target': None, 'expected_versions': [],
        'idempotency_key': 'acceptance-direct-' + uid(), 'reason': 'Direct refusal probe', 'params': undeclared},
        expected=422)
    check('a_write_missing_its_declaration_is_refused_by_http_and_returned_verbatim',
          refused[0] and refused[1] == direct and 'must declare its scene' in refused[1]['error']['message'])
    check('tools_outside_the_0_2_face_and_on_behalf_writes_are_refused_before_any_http_call_and_change_nothing',
          unchanged and all(failed and body['error']['code'] == 'INVALID_ARGUMENTS' for failed, body in outside))

    files = sorted(log_dir.iterdir())
    text = files[0].read_text() if len(files) == 1 else ''
    lines = [json.loads(line) for line in text.splitlines()]
    by_tool = {}
    for line in lines:
        by_tool.setdefault(line['tool'], []).append(line)
    context_line, events_line, state_line = by_tool['world_get_context'][0], by_tool['world_get_events'][0], \
        by_tool['world_get_state'][0]
    task_version = packs[-1]['pack']['layers'][1]['object']['ref']
    check('every_mcp_call_is_in_the_run_log_with_component_and_event_references_and_no_credential',
          len(lines) == 18 and [line['seq'] for line in lines] == list(range(1, 19)) and token not in text
          and criterion in by_tool['world_get_object'][0]['refs']
          and criterion in events_line['refs'] and done['record'][1]['result']['event_id'] in events_line['read_event_ids']
          and source_event in state_line['refs']
          and f"{done['refresh'][1]['result']['ref']}#issues/mcp-iss-1" in state_line['read_refs']
          and context_line['context_pack_id'] == context['context_pack_id']
          and {f'{task_version}#definition/mcp-t1', f'{task_version}#definition/mcp-t2'} <= set(context_line['read_refs'])
          and any(ref.startswith('event:') for ref in context_line['refs'])
          and all(line.get('idempotency_key') for line in lines if line['tool'] in {
              'world_start', 'world_record_event', 'world_refresh_state', 'world_revise_object', 'world_deliver'}
              and line['status'] is not None)
          and [(line['status'], line['error_code']) for line in lines[-5:]]
          == [(422, 'INVALID_REQUEST')] + [(None, 'INVALID_ARGUMENTS')] * 4)
