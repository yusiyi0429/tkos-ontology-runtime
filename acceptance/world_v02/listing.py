"""tkos.world/0.2 独立验收的列对象、外部引用与 0.1 对象的 0.2 视图（票 #63，契约第 3.4、15.2、15.4 节）。

放在 MCP 端到端之后、撤销 CEO 指派之前跑，对象都新建，不动前面场景的主干。列对象的期望按独立的只读 SQL（本 scope 的
对象、所在的域与类型，按建立时刻与 id 排序）或本场景播种的对象给出；对象头逐项对着取对象核对。外部引用冲突的拒绝在
prepare 与 commit 两个入口上核对库快照不变。0.1 视图在另一 scope（装 0.1）里经 HTTP 建出的 0.1 长期目标与快照上读。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from acceptance.method_independent.fixture import uid

V01, V02 = 'tkos.world/0.1', 'tkos.world/0.2'
VIEW = {'view': V02}
HEADER = {'object_id', 'object_type', 'type_display_name', 'category', 'title', 'version', 'revision_id',
          'object_version', 'lifecycle', 'domain_id', 'external_refs', 'contract_version'}
# 0.1 取对象的默认形状（world_v01_readers.read_object），带参数之前与之后都不变。
V01_OBJECT = {'object_id', 'object_type', 'type_display_name', 'version', 'revision_id', 'title', 'attributes',
              'blocks', 'relations', 'referenced_by', 'supersedes', 'object_version', 'formal', 'protocol',
              'lifecycle', 'state'}
BUSINESS = {'object_id', 'object_type', 'type_display_name', 'category', 'candidate', 'version', 'revision_id',
            'object_version', 'title', 'attributes', 'relations', 'blocks', 'component_ledger', 'formal', 'round'}
SYSTEM = 'tianshu-63'  # 本场景专用的外部系统名：按系统筛时期望集合就是这里播种的对象


def utc_now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%SZ')


def list_objects(book, h, f, flow, trunk, foreign):
    check = book.check
    made = trunk['made']
    scope, actor_id = f['scope_id'], {name: actor['principal_id'] for name, actor in f['actors'].items()}

    def listed(actor='outsider', expected=200, **query):
        path = '/v1/world/objects' + ('?' + urlencode(query) if query else '')
        return flow.clients[actor].json('GET', path, expected=expected)

    def walk(actor='outsider', **query):
        """按 next_cursor 取完所有页：返回全部对象头与每页的条数。"""
        items, sizes, cursor = [], [], None
        while True:
            page = listed(actor, **query, **({'cursor': cursor} if cursor else {}))
            items += page['items']
            sizes.append(len(page['items']))
            cursor = page['next_cursor']
            if cursor is None:
                return items, sizes

    def ids(items):
        return [item['object_id'] for item in items]

    def expected(where='TRUE', params=(), scope_id=scope):
        """独立的只读 SQL：本 scope 的对象，按建立时刻与 id 排序。"""
        return [str(row['object_id']) for row in h.sql({'scope_id': scope_id},
            'SELECT object_id FROM gov_objects WHERE scope_id=%s AND ' + where + ' ORDER BY created_at, object_id',
            (scope_id, *params))]

    def refused(code, expected_status, actor='outsider', **query):
        body = listed(actor, expected=expected_status, **query)
        return body['error']['code'] == code

    def act(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    # ---------------------------------------------------------------- 播种：两个周期、外部引用、快照
    index = h.sql(f, 'SELECT tablename, indexdef FROM pg_indexes WHERE indexname=%s', ('ix_gov_world_external_refs',))
    check('the_external_reference_lookup_has_its_index',
          len(index) == 1 and index[0]['tablename'] == 'gov_object_revisions'
          and all(part in index[0]['indexdef'] for part in ('USING gin', "'external_refs'", 'jsonb_path_ops', 'WHERE')))
    unit_goal = made['LongTermGoal.unit']['ref']
    goal = flow.create('a', 'PeriodGoal', 'a', {'title': '7 月目标（列对象）', 'period': '2031-07', 'goal_ref': unit_goal,
                                               'external_refs': [{'system': SYSTEM, 'id': 'month:2031-07'}]})['result']
    other_goal = flow.create('a', 'PeriodGoal', 'a', {'title': '8 月目标（列对象）', 'period': '2031-08',
                                                     'goal_ref': unit_goal})['result']
    card = {'system': SYSTEM, 'id': 'card:1', 'url': 'https://example.test/cards/1'}
    mission = flow.create('a', 'Mission', 'a', {'title': '7 月 Mission（列对象）', 'goal_ref': goal['ref'],
                                               'external_refs': [card]})['result']
    other_mission = flow.create('a', 'Mission', 'a', {'title': '8 月 Mission（列对象）', 'goal_ref': other_goal['ref']})['result']
    task = flow.create('a', 'Task', 'a', {'title': '7 月 Task（列对象）', 'parent_ref': mission['ref']})['result']
    activity = flow.create('a', 'Activity', 'a', {'title': '7 月 Activity（列对象）', 'parent_ref': task['ref']})['result']
    other_task = flow.create('a', 'Task', 'a', {'title': '8 月 Task（列对象）', 'parent_ref': other_mission['ref']})['result']
    source = flow.act('a', 'world_record_event', {'category': 'other', 'subject_refs': [mission['ref']],
                                                  'occurred_at': utc_now(minutes=-3), 'content': {'text': '每周同步'}})
    snapshots = [flow.act('a', 'world_refresh_state', {'payload': {
        'title': title, 'subject_ref': subject['ref'], 'as_of': utc_now(minutes=-2), 'period': period,
        'payload_type': 'execution_state', 'source_event_refs': [f"event:{source['result']['event_id']}"],
        'blocks': {'progress': {'text': title}}}})['result'] for title, subject, period in (
            ('7 月快照', mission, '2031-07'), ('8 月快照', other_mission, '2031-08'))]

    # ---------------------------------------------------------------- 每种筛选各一条
    july = [goal, mission, task, activity, snapshots[0]]
    by_period = listed(period='2031-07')
    check('period_lists_the_period_goal_its_mission_task_and_activity_and_snapshots_of_that_period',
          ids(by_period['items']) == [item['object_id'] for item in july] and by_period['next_cursor'] is None)
    check('a_mission_is_listed_under_the_period_of_its_period_goal',
          ids(listed(type='Mission', period='2031-07')['items']) == [mission['object_id']]
          and ids(listed(type='Mission', period='2031-08')['items']) == [other_mission['object_id']]
          and ids(listed(type='Task', period='2031-08')['items']) == [other_task['object_id']])
    unit = made['ResponsibilityUnit']['object_id']
    in_unit, _ = walk(unit_id=unit, limit=100)
    check('unit_id_lists_the_objects_in_the_units_domain_including_the_unit',
          ids(in_unit) == expected('domain_id=%s', (f['domains']['a'],)) and unit in ids(in_unit)
          and {item['domain_id'] for item in in_unit} == {f['domains']['a']})
    in_company, _ = walk(domain_id=f['domains']['company'], limit=100)
    check('domain_id_lists_the_objects_in_that_domain',
          ids(in_company) == expected('domain_id=%s', (f['domains']['company'],))
          and made['Company']['object_id'] in ids(in_company) and unit not in ids(in_company))
    missions, _ = walk(type='Mission', limit=100)
    snapshots_listed, _ = walk(type='StateSnapshot', limit=100)
    check('type_lists_the_objects_of_that_type_including_state_snapshots',
          ids(missions) == expected("object_type='Mission'") and len(missions) > 2
          and ids(snapshots_listed) == expected("object_type='StateSnapshot'")
          and {item['category']['id'] for item in snapshots_listed} == {'time_record'})
    check('the_filters_combine',
          ids(listed(unit_id=unit, type='Task', period='2031-07')['items']) == [task['object_id']]
          and ids(listed(domain_id=f['domains']['company'], type='Mission')['items']) == []
          and ids(listed(type='Company', external_system='tianshu', external_id='company-1')['items'])
          == [made['Company']['object_id']])
    check('external_system_and_id_look_up_exactly_one_object',
          [(item['object_id'], item['external_refs']) for item in
           listed(external_system=SYSTEM, external_id='card:1')['items']] == [(mission['object_id'], [card])]
          and ids(listed(external_system=SYSTEM, external_id='card:404')['items']) == [])
    check('external_system_alone_lists_the_objects_with_any_reference_of_that_system',
          ids(listed(external_system=SYSTEM)['items']) == [goal['object_id'], mission['object_id']])
    check('an_agent_reads_the_list_like_any_identity_of_the_scope',
          ids(listed('agent_a', period='2031-07')['items']) == ids(by_period['items']))

    # ---------------------------------------------------------------- 对象头对着取对象核对
    def matches(item):
        view = flow.read('outsider', item['object_id'])
        domain = h.sql(f, 'SELECT domain_id FROM gov_objects WHERE scope_id=%s AND object_id=%s',
                       (scope, item['object_id']))[0]['domain_id']
        common = (set(item) == HEADER and item['contract_version'] == V02 and item['domain_id'] == str(domain))
        if item['object_type'] == 'StateSnapshot':
            return common and (item['category'], item['title'], item['version'], item['revision_id'],
                               item['lifecycle'], item['external_refs'], item['type_display_name']) == (
                view['category'], view['title'], view['version'], view['revision_id'], None, [], '状态快照')
        business = view['business']
        return common and all(item[key] == business[key] for key in (
            'object_type', 'type_display_name', 'category', 'title', 'version', 'revision_id', 'object_version')) \
            and item['lifecycle'] == view['records']['lifecycle'] \
            and item['external_refs'] == business['attributes']['external_refs']
    headed = by_period['items'] + listed(type='Mission', limit=5)['items'] + listed(domain_id=f['domains']['company'])['items']
    check('each_header_carries_id_type_category_title_latest_version_lifecycle_domain_and_external_refs_as_read',
          all(matches(item) for item in headed)
          and {item['lifecycle']['status'] for item in headed if item['object_type'] == 'Mission'} >= {'draft'})

    # ---------------------------------------------------------------- 分页
    everything = expected('domain_id=%s', (f['domains']['a'],))
    paged, sizes = walk(unit_id=unit, limit=7)
    check('pages_follow_next_cursor_in_order_without_gaps_or_repeats',
          ids(paged) == everything and len(everything) > 14 and sizes == [7] * (len(everything) // 7)
          + ([len(everything) % 7] if len(everything) % 7 else []))
    first = listed(unit_id=unit, limit=7)
    cursor = first['next_cursor']
    check('a_cursor_only_continues_the_same_read_by_the_same_identity',
          refused('INVALID_REQUEST', 422, unit_id=unit, type='Task', limit=7, cursor=cursor)
          and refused('INVALID_REQUEST', 422, 'ceo', unit_id=unit, limit=7, cursor=cursor)
          and refused('INVALID_REQUEST', 422, unit_id=unit, limit=7, cursor='not-a-cursor')
          and ids(listed(unit_id=unit, limit=7, cursor=cursor)['items']) == everything[7:14])

    # ---------------------------------------------------------------- 拒绝
    other_scope_domain = f['foreign_domains']['company']
    check('malformed_list_requests_are_invalid',
          refused('INVALID_REQUEST', 422, unit_id=unit, domain_id=f['domains']['a'])
          and refused('INVALID_REQUEST', 422, unit_id=mission['object_id'])
          and refused('INVALID_REQUEST', 422, period='2031-13')
          and refused('INVALID_REQUEST', 422, type='Issue')
          and refused('INVALID_REQUEST', 422, external_id='card:1')
          and refused('INVALID_REQUEST', 422, unit=unit)
          and refused('INVALID_REQUEST', 422, limit=101))
    check('a_unit_or_domain_outside_the_scope_is_not_found',
          refused('NOT_FOUND', 404, unit_id=uid())
          and refused('NOT_FOUND', 404, unit_id=foreign['object']['object_id'])
          and refused('NOT_FOUND', 404, domain_id=other_scope_domain)
          and ids(listed('foreign_ceo', external_system=SYSTEM)['items']) == [])

    # ---------------------------------------------------------------- 外部引用在 scope 内唯一
    def deny_revise(oid, patch, codes, says=None):
        flow.deny('a', flow.targeted('world_revise_object', oid, {'payload': patch}), codes=codes, says=says)

    points = f"already points to object {mission['object_id']}"
    flow.deny_create('a', 'Task', 'a', {'title': '撞外部引用', 'parent_ref': other_mission['ref'],
                                        'external_refs': [{'system': SYSTEM, 'id': 'card:1'}]},
                     codes={'INVALID_STATE'}, says=points)
    check('creating_a_second_object_with_the_same_external_reference_is_refused_and_changes_nothing')
    loose = flow.create('a', 'Task', 'a', {'title': '后来的 Task（列对象）', 'parent_ref': other_mission['ref']})['result']
    deny_revise(loose['object_id'], {'external_refs': [{'system': SYSTEM, 'id': 'card:1'}]}, {'INVALID_STATE'}, points)
    deny_revise(loose['object_id'], {'external_refs': [{'system': SYSTEM, 'id': 'card:9'}] * 2}, {'INVALID_REQUEST'})
    check('revising_another_object_to_take_the_same_external_reference_is_refused_and_changes_nothing')
    flow.assign('a', mission['object_id'], actor_id['owner_a'])  # 指派出新修订，外部引用随之沿用
    check('an_object_found_by_its_external_reference_stays_one_across_its_revisions',
          ids(listed(external_system=SYSTEM, external_id='card:1')['items']) == [mission['object_id']]
          and flow.read('outsider', mission['object_id'])['business']['version'] == 2)
    flow.revise('a', mission['object_id'], {'external_refs': [{'system': SYSTEM, 'id': 'card:2'}]})
    flow.revise('a', loose['object_id'], {'external_refs': [{'system': SYSTEM, 'id': 'card:1'}]})
    check('a_reference_freed_on_the_latest_revision_can_be_taken_by_another_object',
          ids(listed(external_system=SYSTEM, external_id='card:1')['items']) == [loose['object_id']]
          and ids(listed(external_system=SYSTEM, external_id='card:2')['items']) == [mission['object_id']]
          and ids(listed(external_system=SYSTEM)['items']) == [goal['object_id'], mission['object_id'],
                                                               loose['object_id']])

    # ---------------------------------------------------------------- 0.1 对象的 0.2 视图（另一 scope）
    foreign_scope = {'scope_id': f['foreign_scope_id']}
    company_ref = foreign['object']['ref']
    ltg_receipt = flow.commit('foreign_ceo', flow.prepare('foreign_ceo', flow.command('world_create_object', {
        'domain_id': f['foreign_domains']['company'], 'object_type': 'LongTermGoal',
        'payload': {'title': '0.1 三年目标', 'scope': 'company', 'horizon': '2028', 'parent_ref': company_ref,
                    'blocks': {'outcome': {'text': '三年后成为行业第一。', 'refs': [company_ref]}}}},
        contract=V01)))
    ltg = ltg_receipt['result']
    as_of = utc_now(minutes=-1)
    snap = flow.commit('foreign_ceo', flow.prepare('foreign_ceo', flow.command('world_refresh_state', {'payload': {
        'title': '0.1 周进展', 'subject_ref': ltg['ref'], 'as_of': as_of, 'period': '2026-09',
        'blocks': {'progress': {'text': '完成一半。', 'refs': [ltg['ref'] + '#outcome']},
                   'artifacts': {'artifacts': ['https://example.test/r/1']}}}}, contract=V01)))['result']
    ceo = actor_id['foreign_ceo']

    def read(oid, **query):
        suffix = '?' + urlencode(query) if query else ''
        return flow.clients['foreign_ceo'].json('GET', f'/v1/world/objects/{oid}{suffix}')

    def state(oid, **query):
        suffix = '?' + urlencode(query) if query else ''
        return flow.clients['foreign_ceo'].json('GET', f'/v1/world/objects/{oid}/state{suffix}')

    plain, grouped = read(ltg['object_id']), read(ltg['object_id'], **VIEW)
    check('a_0_1_object_keeps_its_0_1_shape_by_default',
          set(plain) == V01_OBJECT and 'business' not in plain and plain['protocol']['contract_version'] == V01
          and plain['state']['object_id'] == snap['object_id'] and 'payload_type' not in plain['state']
          and set(read(snap['object_id'])) == V01_OBJECT - {'state'} | {'unconfirmed'})
    business, records = grouped.get('business', {}), grouped.get('records', {})
    blocks = {block['id']: block for block in business.get('blocks', [])}
    created = h.sql(foreign_scope, "SELECT event_id, kind, contract_version FROM gov_world_events WHERE scope_id=%s "
                                   "AND action_id=%s", (f['foreign_scope_id'], ltg_receipt['receipt_id']))
    check('with_the_view_parameter_a_0_1_object_is_read_in_the_three_groups_under_the_0_1_contract',
          set(grouped) == {'object_id', 'business', 'identity', 'records', 'protocol'} and set(business) == BUSINESS
          and grouped['protocol'] == plain['protocol']
          and (business['object_type'], business['type_display_name'], business['category'], business['candidate'],
               business['version'], business['revision_id'], business['object_version'], business['title'])
          == ('LongTermGoal', '长期目标', {'id': 'business_object', 'display_name': '业务对象'}, False, 1,
              plain['revision_id'], plain['object_version'], '0.1 三年目标')
          and business['attributes'] == {'scope': 'company', 'horizon': '2028'}
          and list(blocks) == ['outcome', 'measures', 'constraint']
          and all(block['class'] is None and block['components'] == [] for block in blocks.values())
          and blocks['measures']['empty'] and blocks['measures']['text'] == '当前没有衡量'
          and business['component_ledger'] == [] and business['round'] is None and business['formal'] == plain['formal']
          and grouped['identity'] == {'responsible': {'role': 'CEO', 'source': 'role', 'principals': [
              {'principal_id': ceo, 'principal_type': 'human', 'display_name': 'Synthetic A2 ceo'}]}, 'delegations': []})
    check('0_1_references_read_as_0_2_object_and_block_forms',
          blocks['outcome']['value']['refs'] == [{**plain['blocks'][0]['value']['refs'][0], 'component': None}]
          and blocks['outcome']['value']['refs'][0]['ref'] == company_ref
          and business['relations'][0] == {'field': 'parent_ref', 'relation': 'has',
                                           'value': {**plain['relations'][0]['value'], 'component': None}})
    check('the_lifecycle_follows_the_0_1_state_machine_from_its_0_1_event',
          records['lifecycle'] == plain['lifecycle'] and records['lifecycle']['status'] == 'draft'
          and [(str(row['event_id']), row['kind'], row['contract_version']) for row in created]
          == [(records['lifecycle']['event_id'], 'object.created', V01)]
          and records['confirmed_review'] is None and records['open_issues'] == [])
    legacy = records['latest_state'] or {}
    legacy_blocks = {block['id']: block for block in legacy.get('blocks', [])}
    check('a_0_1_snapshot_is_given_as_the_read_only_legacy_0_1_payload',
          legacy.get('object_id') == snap['object_id']
          and legacy['payload_type'] == {'id': 'legacy_0_1', 'display_name': '0.1 状态快照（只读）'}
          and list(legacy_blocks) == ['progress', 'issue', 'artifacts']
          and legacy_blocks['progress']['text'] == '完成一半。'
          and legacy_blocks['progress']['value']['refs'][0]['ref'] == ltg['ref'] + '#outcome'
          and legacy_blocks['issue']['empty'] and legacy_blocks['artifacts']['value']['artifacts'] == ['https://example.test/r/1']
          and legacy['generator']['principal_id'] == ceo and legacy['source_event_refs'] == []
          and legacy['subject_ref']['ref'] == ltg['ref'] and legacy['as_of'] == as_of and legacy['period'] == '2026-09'
          and legacy['unconfirmed'] is True and legacy['category']['id'] == 'time_record')
    by_id = read(snap['object_id'], **VIEW)
    later = state(ltg['object_id'], **VIEW)
    earlier = state(ltg['object_id'], as_of=utc_now(minutes=-30), **VIEW)
    check('the_snapshot_and_the_state_read_the_same_legacy_view',
          {key: value for key, value in by_id.items() if key != 'protocol'} == legacy
          and by_id['protocol'] == read(snap['object_id'])['protocol']
          and later['snapshot'] == legacy and earlier['snapshot'] is None
          and read(ltg['object_id'], version=1, **VIEW)['business']['revision_id'] == plain['revision_id']
          and 'payload_type' not in state(ltg['object_id'])['snapshot'])
    viewed = flow.clients['outsider'].json('GET', f"/v1/world/objects/{mission['object_id']}?{urlencode(VIEW)}")
    check('a_0_2_object_reads_the_same_with_or_without_the_view_parameter',
          viewed == flow.read('outsider', mission['object_id']) and 'business' in viewed)
    check('an_unknown_view_is_invalid',
          all(flow.clients['foreign_ceo'].json('GET', path, expected=422)['error']['code'] == 'INVALID_REQUEST'
              for path in (f"/v1/world/objects/{ltg['object_id']}?{urlencode({'view': V01})}",
                           f"/v1/world/objects/{ltg['object_id']}/state?view=grouped")))
    foreign_items = listed('foreign_ceo')['items']
    check('the_list_also_heads_0_1_objects_under_the_0_1_contract',
          ids(foreign_items) == expected(scope_id=f['foreign_scope_id'])
          and ids(foreign_items) == [foreign['object']['object_id'], ltg['object_id'], snap['object_id']]
          and {item['contract_version'] for item in foreign_items} == {V01}
          and [item['lifecycle'] for item in foreign_items] == [None, plain['lifecycle'], None]
          and [item['category']['id'] for item in foreign_items] == ['business_object', 'business_object', 'time_record']
          and all(set(item) == HEADER and item['external_refs'] == [] for item in foreign_items))
