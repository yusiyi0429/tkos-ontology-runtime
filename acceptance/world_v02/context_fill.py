"""tkos.world/0.2 独立验收的取上下文补齐（票 #64，契约第 15.3 节与补 43）。

放在 Issue 之后、撤销 CEO 指派之前跑（要用 CEO 登记委托），对象都新建，不动前面场景的主干。单元 a 的 DRI 在主干的
单元长期目标下建一条周期目标并承诺，CEO 给天枢登记门的委托，天枢代 CEO 确认；再在它下面建一条 Mission，DRI 指派
Owner。从周期目标与 Mission 出发经 HTTP 取上下文，核对：Why 沿单元长期目标多取一跳到公司级长期目标，覆盖追到
公司级长期目标、Strategy 与 Company 的块或组件；Markdown 开头是六问指引、以六问为节；代记的事件行写出记录者与
被代记的人，指派的写出被指派者；同一世界状态两次调用结果相同；收紧预算时按补 48 的顺序裁（#70）。MCP 取上下文只交四项
由前面的 mcp_end_to_end 场景在同一个 API 进程上核对。

第二段（形成时带入）：出发对象是有门类型就带入，不看它当前在哪个生命周期段——「此后主受影响对象还没记过
门事件」已经让问题自然失效，只在草稿或一轮里才带的话，立即重开的问题在对象重开之前就看不到了。在周期目标下另建一条
Mission，Co-Agent 写带问题组件的快照、提出并路由给 Owner，Owner 承接后处置为带入下次形成：从这条 Mission 取上下文
看到「形成时带入」（计入覆盖、预算再紧也不裁）；Owner 记承诺（门事件）之后不再带入。再给单元长期目标写带问题的
快照，DRI 处置为立即重开：从本单元的周期目标取上下文带入它。#69（补 46）：给责任单元写带问题的快照，DRI 处置为带入
下次形成，从本单元的周期目标取上下文带入它；CEO 再确认这条周期目标（本单元的周期目标记了门事件）之后不再带入，长期目标
上的问题照旧带入。CEO 写并确认一条公司复盘，从周期目标取上下文带入它
（钉到快照修订，材料不带）；再确认一条时点更晚的与一条时点更早、确认更晚的，带入的是时点最新的那条。本单元新建一条
确认的、一条确认后终止的与一条草稿的长期目标：只带入已确认的（与列对象按域核对），不带周期目标自己 goal_ref 指的那条。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

V02 = 'tkos.world/0.2'
SECTIONS = ['## 六问指引', '## 为什么', '## 做什么', '## 谁负责', '## 现在怎样', '## 发生了什么', '## 凭什么']
# 从周期目标出发一定有「形成时带入」一节（公司复盘或它的缺口），在六问指引之后。
FORMING = SECTIONS[:1] + ['## 形成时带入'] + SECTIONS[1:]


def at(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def sections(markdown):
    """Markdown 按二级标题切成节：节名 → 正文。"""
    return {part.split('\n', 1)[0]: (part.split('\n', 1) + [''])[1].strip('\n')
            for part in ('\n' + markdown).split('\n## ')[1:]}


def why_chain(whole):
    """契约补 48：从一次没裁过的取上下文，按读回的字段算 Why 链上的项（条目 key）。上溯各层定义类的块、多取的一跳，
    以及被层号更小的内容钉到的上溯各层的块：主干那一步的引用字段（检索计划 walked 的 pinned，第 i 步算第 i 层），
    或包里任一块（多取一跳里的块算它那一层）的块值引用与组件引用；按对象 id 与块 id 认，不看版本。"""
    layers = whole['context_pack']['layers']
    where = {(layer['object']['object_id'], block['id']): (layer['level'], f"block:{block['ref']}")
             for layer in layers if layer['level'] > 0 for block in layer['blocks']}
    found = {f"hop:{layer['hop']['object']['ref']}" for layer in layers if layer['hop']}
    found |= {f"block:{block['ref']}" for layer in layers if layer['level'] > 0 for block in layer['blocks']
              if block['kind'] == 'definition'}
    pins = []
    for level, step in enumerate(whole['plan']['walked']):
        head, _, rest = step['pinned'].partition('#')
        pins.append((level, head.split('@')[0], rest.split('/')[0] or None))
    for layer in layers:
        for part in (layer, layer['hop']):
            for block in part['blocks'] if part else []:
                refs = (block['value']['refs'] if block['value'] else []) + [
                    ref for item in block['components'] for ref in item['refs']]
                pins += [(layer['level'], ref.get('object_id'), ref.get('block')) for ref in refs]
    for level, object_id, block in pins:
        target = where.get((object_id, block))
        if target and target[0] > level:
            found.add(target[1])
    return found


def trim_step(entry, why):
    """一条裁剪记录落在补 48 的哪一步：1 上溯各层不在 Why 链上的块，2 事件，3 其余内容（跨链关系、快照），
    4 Why 链上的项。"""
    if entry['kind'] == 'event':
        return 2
    if entry['key'] in why:
        return 4
    return 1 if entry['kind'] == 'block' else 3


def trimmed_in_the_0_2_order(whole, result):
    """收紧预算的裁剪按补 48 的四步排：步号不减；事件由旧到新；其余三步各自由远及近（当前对象的跨链关系在第 3 步
    最后）；当前对象的块与最新快照不裁，也都还在。whole 是同一世界状态下没裁过的那次。"""
    why = why_chain(whole)
    when = {event['ref']: at(event['occurred_at']) for layer in whole['context_pack']['layers'] for event in layer['events']}
    over = [entry for entry in result['plan']['trimmed'] if entry['reason'] == 'over_budget']
    steps = [trim_step(entry, why) for entry in over]
    by_step = {n: [entry for entry in over if trim_step(entry, why) == n] for n in (1, 2, 3, 4)}
    current, before = result['context_pack']['layers'][0], whole['context_pack']['layers'][0]
    return (steps == sorted(steps)
            and [when[entry['key']] for entry in by_step[2]] == sorted(when[entry['key']] for entry in by_step[2])
            and all([entry['level'] for entry in by_step[n]] == sorted((entry['level'] for entry in by_step[n]), reverse=True)
                    for n in (1, 3, 4))
            and not [entry for entry in over if entry['level'] == 0 and entry['kind'] in {'block', 'snapshot'}]
            and current['blocks'] == before['blocks'] and current['state'] == before['state'])


def context_fill(book, h, f, flow, trunk):
    check = book.check
    made = trunk['made']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    question = {'question': '这个周期目标为什么做、做什么、谁负责、现在怎样？'}

    def latest(oid):
        business = flow.read('outsider', oid)['business']
        return f"{oid}@{business['version']}"

    # ---------------------------------------------------------------- 播种：周期目标（代 CEO 确认）与它下面的 Mission
    unit_goal, company_goal = made['LongTermGoal.unit']['object_id'], made['LongTermGoal']['object_id']
    strategy, company = made['Strategy']['object_id'], made['Company']['object_id']
    # 形成锚定（#60）：scope 里已有已确认的公司复盘时，周期目标要依据其中一条（前面的 goal_closure 场景确认过）。
    review = flow.read('outsider', company)['records']['confirmed_review']
    goal = flow.create('a', 'PeriodGoal', 'a', {
        'title': '3 月目标（取上下文）', 'period': '2027-03', 'goal_ref': latest(unit_goal),
        **({'review_ref': review['snapshot']['ref']} if review else {}),
        'blocks': {'alignment': {'components': [{'id': 'fill-why', 'type': 'why_this_period', 'text': '三月是续约季'}]},
                   'target': {'components': [{'id': 'fill-o1', 'type': 'outcome', 'text': '续签两家'},
                                             {'id': 'fill-ac1', 'type': 'acceptance_criterion', 'text': '合同归档'}]}}}
    )['result']
    pid = goal['object_id']
    committed = flow.gate('a', 'world_commit_period_goal', pid, {})['result']
    now = datetime.now(timezone.utc)
    flow.act('ceo', 'world_grant_delegation', {
        'delegate_principal_id': actor_id['tianshu'], 'families': ['gate'], 'domain_ids': [f['domains']['a']],
        'valid_until': (now + timedelta(days=1)).isoformat(timespec='seconds')})
    on_behalf = {'principal_id': actor_id['ceo'], 'external_record_id': 'tianshu:confirm:context-64',
                 'external_confirmed_at': (now - timedelta(minutes=5)).isoformat(timespec='seconds')}
    confirmed = flow.commit('tianshu', flow.prepare('tianshu', flow.targeted(
        'world_confirm_period_goal', pid, {'outcome': 'accepted', 'on_behalf_of': on_behalf})))['result']
    events = {event['event_id']: event for event in flow.events('outsider', pid)['events']}
    delegated, own = events[confirmed['event_id']], events[committed['event_id']]

    # ---------------------------------------------------------------- 从单元周期目标出发
    first = flow.context('agent_a', pid, question)
    pack, plan, coverage = first['context_pack'], first['plan'], first['coverage']
    layers, markdown = pack['layers'], pack['markdown']
    parts = sections(markdown)
    targets = {oid: latest(oid) for oid in (unit_goal, company_goal, strategy, company)}
    hop = layers[1]['hop']
    check('a_unit_goal_takes_one_hop_along_goal_ref_to_the_company_goal_and_the_plan_records_it',
          [layer['object']['object_type'] for layer in layers]
          == ['PeriodGoal', 'LongTermGoal', 'ResponsibilityUnit', 'Strategy', 'Company']
          and layers[1]['object']['ref'] == targets[unit_goal]
          and hop is not None and hop['field'] == 'goal_ref' and hop['object']['ref'] == targets[company_goal]
          and [block['kind'] for block in hop['blocks']] == ['definition', 'definition']
          and all(layer['hop'] is None for index, layer in enumerate(layers) if index != 1)
          and plan['hops'] == [{'from': targets[unit_goal], 'field': 'goal_ref',
                                'pinned': made['LongTermGoal']['ref'], 'read': targets[company_goal]}]
          and {'kind': 'hop', 'level': 1, 'key': f"hop:{targets[company_goal]}"} in plan['taken']
          and f"### 沿 goal_ref 多取一跳：公司级长期目标《{hop['object']['title']}》 `{targets[company_goal]}`"
          in parts['为什么'].splitlines())

    why = [item['ref'] for item in coverage['why']['evidence']]
    reached = {oid: [ref for ref in why if ref.startswith(targets[oid] + '#')]
               for oid in (company_goal, strategy, company)}
    refs = set()
    pending = [pack]
    while pending:  # 包里出现过的全部业务形式引用
        value = pending.pop()
        if isinstance(value, dict):
            refs.update([value['ref']] if isinstance(value.get('ref'), str) else [])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    guide_why = parts['六问指引'].splitlines()[1]
    # #79 起「为什么」先给当前对象带贡献角色的组件（本周期必要性），再由近及远到上层。
    contribution = f"{goal['ref']}#alignment/fill-why"
    check('from_a_unit_period_goal_why_covers_blocks_or_components_of_the_company_goal_the_strategy_and_the_company',
          coverage['why']['answered'] and all(reached.values()) and set(why) <= refs
          and any('/' in ref for ref in reached[company_goal])  # 公司级目标的成功标准是组件，引用细到组件
          and why[0] == contribution
          and guide_why.startswith(f'- 为什么：当前对象的贡献 `{contribution}`；长期目标 `')
          and all(f"{label} `{targets[oid]}" in guide_why
                  for label, oid in (('公司级长期目标', company_goal), ('战略', strategy), ('公司', company)))
          and guide_why.index('公司级长期目标') < guide_why.index('战略') < guide_why.index('→ 公司 '))

    check('the_markdown_opens_with_the_six_question_guide_and_takes_the_six_questions_as_its_sections',
          markdown.startswith(f"# 上下文\n\n问题：{question['question']}\n\n出发对象：`{goal['ref']}`\n\n## 六问指引\n")
          and [line for line in markdown.splitlines() if line.startswith('## ')] == FORMING
          and [line.split('：', 1)[0] for line in parts['六问指引'].splitlines()[1:]]
          == ['- 为什么', '- 做什么', '- 谁负责', '- 现在怎样', '- 发生了什么', '- 凭什么']
          and f"`{targets[strategy]}#strategy_core`" in parts['为什么']
          and f"`{targets[company]}#identity`" in parts['为什么']
          and f"`{goal['ref']}#target/fill-o1`" in parts['做什么']
          # #79 起约束不再单独成块：「凭什么」是逐层带约束与验收角色的组件引用清单。
          and parts['凭什么'].splitlines()[0] == '按组件的取上下文角色逐层列出约束与验收（内容在它们所在的块里）：'
          and f"`{goal['ref']}#target/fill-ac1`（成功 / 验收标准）" in parts['凭什么'].splitlines()[1]
          and all(f"`{layer['object']['ref']}`" in parts['谁负责'] for layer in layers)
          and f"生命周期：已确认（事件 `event:{confirmed['event_id']}`）" in parts['现在怎样']
          and all(f"（事件 `event:{event_id}`" in parts['发生了什么'] for event_id in events))

    lines = markdown.splitlines()
    names = (delegated['principal']['display_name'], delegated['on_behalf_of']['display_name'])
    in_pack = {event['event_id']: event for layer in layers for event in layer['events']}
    check('a_delegated_event_line_names_both_the_recorder_and_the_person_recorded_on_behalf_of',
          delegated['principal']['principal_id'] == actor_id['tianshu']
          and delegated['on_behalf_of']['principal_id'] == actor_id['ceo']
          and f"- {delegated['occurred_at']} 周期目标 确认·接受（事件 `event:{confirmed['event_id']}`，"
              f"{names[0]} 代 {names[1]} 记）" in lines
          and f"- {own['occurred_at']} 周期目标 承诺（事件 `event:{committed['event_id']}`，"
              f"{own['principal']['display_name']} 记）" in lines
          and in_pack[confirmed['event_id']]['on_behalf_of'] == delegated['on_behalf_of']
          and in_pack[confirmed['event_id']]['principal'] == delegated['principal'])

    # 同一世界状态（换一个读者）再取一次：Markdown、包、计划与覆盖逐字节相同。
    second = flow.context('outsider', pid, question)
    check('the_same_world_state_gives_the_same_markdown_pack_plan_and_coverage',
          second['context_pack'] == pack and second['plan'] == plan and second['coverage'] == coverage
          and second['context_pack']['markdown'] == markdown and second['context_pack_id'] != first['context_pack_id'])

    mission = flow.create('a', 'Mission', 'a', {'title': '取上下文 Mission', 'goal_ref': latest(pid)})['result']
    assigned = flow.assign('a', mission['object_id'], actor_id['owner_a'])['result']
    # 投影项（#79）：Mission 下一条 Task 的任务定义带贡献、工作结果与成功 / 验收标准，投影只取后两个。
    projected_task = flow.create('a', 'Task', 'a', {
        'title': '投影 Task', 'parent_ref': latest(mission['object_id']), 'blocks': {'definition': {'components': [
            {'id': 'fill-t-c1', 'type': 'contribution', 'text': '为续签铺路'},
            {'id': 'fill-t-o1', 'type': 'outcome', 'text': '续签方案成稿'},
            {'id': 'fill-t-ac1', 'type': 'acceptance_criterion', 'text': '客户确认方案'}]}}})['result']
    assignment = {event['event_id']: event for event in flow.events('outsider', mission['object_id'])['events']}[
        assigned['event_id']]
    from_mission = flow.context('agent_a', mission['object_id'], question)
    owner = flow.read('outsider', mission['object_id'])['identity']['responsible']['principals'][0]
    mission_events = {event['event_id']: event for event in from_mission['context_pack']['layers'][0]['events']}
    check('an_assignment_event_line_names_the_recorder_and_the_assignee',
          owner['principal_id'] == actor_id['owner_a']
          and f"- {assignment['occurred_at']} Mission 指派（事件 `event:{assigned['event_id']}`，"
              f"{assignment['principal']['display_name']} 记，指派给 {owner['display_name']}）"
          in from_mission['context_pack']['markdown'].splitlines()
          and mission_events[assigned['event_id']]['assignee'] == owner
          and all(event['assignee'] is None for key, event in mission_events.items() if key != assigned['event_id'])
          and from_mission['plan']['hops'][0]['read'] == targets[company_goal])

    projection = from_mission['context_pack']['layers'][0]['projection']
    doing = sections(from_mission['context_pack']['markdown'])['做什么'].split('\n\n')[-1]
    mission_guide = sections(from_mission['context_pack']['markdown'])['六问指引']
    tiny_mission = flow.context('agent_a', mission['object_id'], {**question, 'budget': {'max_chars': 10}})
    check('from_a_mission_the_pack_projects_its_tasks_expected_results_under_what_and_never_trims_them',
          (projection['id'], projection['display_name']) == ('task_expectations', 'Task 预期结果与质量标准')
          and [(item['ref'], [(c['id'], c['type'], c['ref']) for c in item['components']])
               for item in projection['items']]
          == [(projected_task['ref'], [(cid, kind, f"{projected_task['ref']}#definition/{cid}")
                                       for cid, kind in (('fill-t-o1', 'outcome'), ('fill-t-ac1', 'acceptance_criterion'))])]
          and projection == flow.read('outsider', mission['object_id'])['business']['projection']
          and all(layer['projection'] is None for layer in from_mission['context_pack']['layers'][1:])
          and doing.splitlines()[:2] == ['### Mission·Task 预期结果与质量标准（读取时从下级对象投影，不存）',
                                         f"- Task《投影 Task》 `{projected_task['ref']}`"]
          and all(f"`{c['ref']}`" in doing for c in projection['items'][0]['components']) and 'fill-t-c1' not in doing
          and '当前对象的投影项「Task 预期结果与质量标准」（1 项，读取时从下级对象投影）' in mission_guide
          and tiny_mission['budget']['over_budget'] is True
          and tiny_mission['context_pack']['layers'][0]['projection'] == projection
          and doing in tiny_mission['context_pack']['markdown']
          and not [entry for entry in tiny_mission['plan']['trimmed'] if entry['kind'] == 'projection'])

    def every(**query):
        """列对象按页取完（每页 100 条）。"""
        found, cursor = [], None
        while True:
            page = flow.clients['outsider'].json('GET', '/v1/world/objects?' + urlencode(
                {**query, 'limit': 100, **({'cursor': cursor} if cursor else {})}))
            found += page['items']
            cursor = page['next_cursor']
            if cursor is None:
                return found

    unit_id = made['ResponsibilityUnit']['object_id']
    from_unit = flow.context('agent_a', unit_id, question)
    unit_projection = from_unit['context_pack']['layers'][0]['projection']
    missions = every(domain_id=f['domains']['a'], type='Mission')
    check('from_a_responsibility_unit_the_pack_lists_the_missions_of_its_domain_as_a_projection',
          (unit_projection['id'], unit_projection['display_name']) == ('mission_refs', '战役引用')
          and [(item['object_id'], item['ref'], item['title']) for item in unit_projection['items']]
          == [(item['object_id'], f"{item['object_id']}@{item['version']}", item['title']) for item in missions]
          and mission['object_id'] in [item['object_id'] for item in missions]
          and not any('components' in item for item in unit_projection['items'])
          and '### 责任单元·战役引用（读取时从下级对象投影，不存）'
          in sections(from_unit['context_pack']['markdown'])['做什么']
          and f"当前对象的投影项「战役引用」（{len(missions)} 项，读取时从下级对象投影）"
          in sections(from_unit['context_pack']['markdown'])['六问指引'])

    # ---------------------------------------------------------------- 预算（#70，契约第 15.3 节与补 48）
    def trimmed_in_order(result):
        """裁剪按补 48 的四步排（见 trimmed_in_the_0_2_order），当前对象的块不裁；六问的节都在，指引不再指向裁掉的
        内容（推出当前生命周期的事件一直在「现在怎样」里）。"""
        over = [entry for entry in result['plan']['trimmed'] if entry['reason'] == 'over_budget']
        stage = f"event:{result['context_pack']['layers'][0]['object']['lifecycle']['event_id']}"
        gone = [entry['key'] if entry['kind'] == 'event' else entry['key'].split(':', 1)[1] for entry in over]
        guide = sections(result['context_pack']['markdown'])['六问指引']
        return (trimmed_in_the_0_2_order(first, result)
                and [line for line in result['context_pack']['markdown'].splitlines() if line.startswith('## ')]
                == FORMING
                and not [ref for ref in gone if f"`{ref}" in guide and ref != stage]
                and result['budget']['used_chars'] == len(result['context_pack']['markdown'])
                and result['budget']['over_budget'] is (result['budget']['used_chars'] > result['budget']['max_chars']))

    tight = flow.context('agent_a', pid, {**question, 'budget': {'max_chars': len(markdown) // 2}})
    tiny = flow.context('agent_a', pid, {**question, 'budget': {'max_chars': 10}})
    check('a_tight_budget_trims_in_the_0_2_order_keeps_the_current_object_and_the_guide_only_points_at_what_is_left',
          tight['plan']['trimmed'] and trimmed_in_order(tight) and trimmed_in_order(tiny)
          and tiny['budget']['over_budget'] is True
          and f"hop:{targets[company_goal]}" in [entry['key'] for entry in tiny['plan']['trimmed']]
          and tiny['context_pack']['layers'][1]['hop'] is None)

    # ---------------------------------------------------------------- 第二段：形成时带入待带入的问题（补 43）
    # 出发对象是有门类型就带入，不看它在哪个生命周期段：处置之后主受影响对象记了门事件，问题自然不再带入。
    def issue_action(actor, kind, issue_ref, params=None):
        params = {'issue_ref': issue_ref, **(params or {})}
        if actor == 'agent_a':
            params['declaration'] = scene
        return flow.commit(actor, flow.prepare(actor, flow.command(kind, params)))['result']

    def dispose(issue_ref, owner, disposition, reason):
        """MF（agent_a）提出并路由给 owner，owner 本人承接后处置；返回处置的回执结果。"""
        issue_action('agent_a', 'world_raise_issue', issue_ref)
        issue_action('agent_a', 'world_route_issue', issue_ref, {'to_principal_id': actor_id[owner]})
        issue_action(owner, 'world_own_issue', issue_ref)
        return issue_action(owner, 'world_dispose_issue', issue_ref,
                            {'disposition': disposition, 'content': {'text': reason}})

    def snapshot_with_issue(subject_ref, payload_type, cid, question_text):
        as_of = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        return flow.refresh('agent_a', {
            'title': f'{payload_type}（#64 带入）', 'subject_ref': subject_ref, 'as_of': as_of,
            'payload_type': payload_type, 'source_event_refs': [f"event:{confirmed['event_id']}"],
            'blocks': {'issues': {'components': [{'id': cid, 'type': 'issue', 'text': question_text,
                                                  'attributes': {'core_question': question_text}}]}}}, scene)['result']

    carried_mission = flow.create('a', 'Mission', 'a', {'title': '带入 Mission', 'goal_ref': latest(pid)})['result']
    cmid = carried_mission['object_id']
    flow.assign('a', cmid, actor_id['owner_a'])
    scene = {'scene': latest(cmid), 'trigger': 'Co-Agent 周检（#64）', 'human_acceptance': {'required': False}}
    mission_snapshot = snapshot_with_issue(latest(cmid), 'execution_state', 'fill-m-iss', '试点要不要换打法？')
    mission_issue = f"{mission_snapshot['ref']}#issues/fill-m-iss"
    rolled = dispose(mission_issue, 'owner_a', 'roll_forward', '换打法的判断放到下次形成')
    rolled_event = {event['event_id']: event for event in flow.events('outsider', cmid)['events']}[rolled['event_id']]
    before_gate = flow.context('agent_a', cmid, question)
    carried = before_gate['context_pack']['carried']['issues']
    carried_md = sections(before_gate['context_pack']['markdown']).get('形成时带入', '')
    check('after_a_roll_forward_the_affected_gated_object_carries_the_issue_into_its_formation',
          [item['issue_ref']['ref'] for item in carried] == [mission_issue]
          and carried[0]['issue_ref']['revision_id'] == mission_snapshot['revision_id']
          and carried[0]['primary']['object_id'] == cmid and carried[0]['core_question'] == '试点要不要换打法？'
          and carried[0]['disposition'] == {'id': 'roll_forward', 'display_name': '带入下次形成'}
          and carried[0]['disposed_by']['ref'] == f"event:{rolled['event_id']}"
          and carried[0]['disposed_by']['occurred_at'] == rolled_event['occurred_at']
          and carried[0]['reason'] == '换打法的判断放到下次形成'
          and [line for line in before_gate['context_pack']['markdown'].splitlines() if line.startswith('## ')][:3]
          == ['## 六问指引', '## 形成时带入', '## 为什么']
          and f"### 待带入的问题 `{mission_issue}`：试点要不要换打法？" in carried_md
          and f"处置：带入下次形成（事件 `event:{rolled['event_id']}`" in carried_md
          and {'ref': mission_issue} in before_gate['coverage']['basis']['evidence']
          and {'ref': f"event:{rolled['event_id']}"} in before_gate['coverage']['basis']['evidence'])
    tiny_carry = flow.context('agent_a', cmid, {**question, 'budget': {'max_chars': 10}})
    check('carried_issues_are_never_trimmed',
          tiny_carry['budget']['over_budget'] is True and tiny_carry['context_pack']['carried']['issues'] == carried
          and sections(tiny_carry['context_pack']['markdown'])['形成时带入'] == carried_md
          and not [entry for entry in tiny_carry['plan']['trimmed'] if entry['kind'] == 'carried'])

    gate = flow.gate('owner_a', 'world_commit_mission', cmid, {})['result']
    after_gate = flow.context('agent_a', cmid, question)
    check('once_the_affected_object_records_a_gate_event_the_issue_is_no_longer_carried',
          flow.read('outsider', cmid)['records']['lifecycle']['event_id'] == gate['event_id']
          and after_gate['context_pack']['carried'] == {'issues': []}
          and '## 形成时带入' not in after_gate['context_pack']['markdown']
          and {'ref': mission_issue} not in after_gate['coverage']['basis']['evidence'])

    scene = {'scene': latest(unit_goal), 'trigger': 'Co-Agent 周检（#64）', 'human_acceptance': {'required': False}}
    goal_snapshot = snapshot_with_issue(latest(unit_goal), 'goal_state', 'fill-g-iss', '长期目标的衡量要不要改？')
    goal_issue = f"{goal_snapshot['ref']}#issues/fill-g-iss"
    reopened = dispose(goal_issue, 'a', 'immediate_reopen', '衡量口径不对，立即重开')
    from_goal = flow.context('agent_a', pid, question)
    goal_carried = from_goal['context_pack']['carried']['issues']
    check('forming_a_period_goal_carries_a_pending_issue_on_its_units_long_term_goal',
          [item['issue_ref']['ref'] for item in goal_carried] == [goal_issue]
          and goal_carried[0]['primary']['object_id'] == unit_goal
          and goal_carried[0]['primary']['object_type'] == 'LongTermGoal'
          and goal_carried[0]['disposition']['id'] == 'immediate_reopen'
          and goal_carried[0]['disposed_by']['event_id'] == reopened['event_id']
          and f"### 待带入的问题 `{goal_issue}`：长期目标的衡量要不要改？"
          in sections(from_goal['context_pack']['markdown'])['形成时带入']
          and flow.context('outsider', pid, question)['context_pack'] == from_goal['context_pack'])

    # 责任单元上的问题（#69，补 46）：责任单元没有门，改看本单元任一周期目标在处置之后有没有记门事件。CEO 再确认本单元
    # 的周期目标之后不再带入；长期目标上的问题照旧带入（它的长期目标没记门事件）。
    unit = made['ResponsibilityUnit']['object_id']
    scene = {'scene': latest(unit), 'trigger': 'Co-Agent 周检（#69）', 'human_acceptance': {'required': False}}
    unit_snapshot = snapshot_with_issue(latest(unit), 'unit_state', 'fill-u-iss', '单元边界要不要调整？')
    unit_issue = f"{unit_snapshot['ref']}#issues/fill-u-iss"
    rolled_unit = dispose(unit_issue, 'a', 'roll_forward', '边界调整放到下个周期目标里定')
    with_unit = flow.context('agent_a', pid, question)['context_pack']
    unit_carried = with_unit['carried']['issues']
    check('forming_a_period_goal_carries_a_pending_issue_on_its_responsibility_unit',
          [item['issue_ref']['ref'] for item in unit_carried] == [goal_issue, unit_issue]
          and unit_carried[1]['primary']['object_id'] == unit
          and unit_carried[1]['primary']['object_type'] == 'ResponsibilityUnit'
          and unit_carried[1]['disposition']['id'] == 'roll_forward'
          and unit_carried[1]['disposed_by']['event_id'] == rolled_unit['event_id']
          and f"### 待带入的问题 `{unit_issue}`：单元边界要不要调整？" in sections(with_unit['markdown'])['形成时带入'])
    regate = flow.gate('ceo', 'world_reconfirm_period_goal', pid, {})['result']
    after_regate = flow.context('agent_a', pid, question)['context_pack']
    check('once_a_period_goal_of_the_unit_records_a_gate_event_the_units_issue_is_no_longer_carried_and_others_stay',
          regate['event_id'] in {event['event_id'] for event in flow.events('outsider', pid)['events']}
          and [item['issue_ref']['ref'] for item in after_regate['carried']['issues']] == [goal_issue]
          and f"`{unit_issue}`" not in sections(after_regate['markdown'])['形成时带入'])

    # ---------------------------------------------------------------- 第二段：形成周期目标时带入公司复盘与有效的长期目标
    # 进来时 scope 里已有前面 goal_closure 场景确认的公司复盘；这里写的快照时点都更晚。
    def listed(**query):
        return flow.clients['outsider'].json('GET', '/v1/world/objects?' + urlencode(query))['items']

    def company_review(period, as_of):
        """CEO 写一条公司复盘快照并确认它（复盘确认的目标是快照，期望版本取列对象头）；返回快照的回执结果。"""
        written = flow.refresh('ceo', {
            'title': f'公司复盘 {period}', 'subject_ref': latest(company), 'as_of': as_of, 'period': period,
            'payload_type': 'company_review', 'source_event_refs': [f"event:{confirmed['event_id']}"],
            'blocks': {'results': {'text': f'{period} 营收达成八成'}, 'gaps': {'text': '交付慢两周'},
                       'implications': {'text': '下月先补交付'},
                       'materials': {'text': '复盘草稿', 'artifacts': ['https://example.test/review-draft']}}})['result']
        header, = listed(type='StateSnapshot', period=period)
        target = {'object_id': written['object_id'], 'revision_id': written['revision_id'],
                  'expected_version': header['object_version']}
        flow.commit('ceo', flow.prepare('ceo', flow.targeted('world_confirm_review', written['object_id'],
                                                             {'content': {'text': '复盘确认（#64）'}}, target)))
        return written

    def moment(**delta):
        return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    first_review = company_review('2036-01', moment(seconds=-2))
    one = flow.context('agent_a', pid, question)
    review = one['context_pack']['carried']['company_review']
    review_md = sections(one['context_pack']['markdown'])['形成时带入']
    check('after_a_company_review_is_confirmed_forming_a_period_goal_carries_it_pinned_to_its_snapshot',
          review['snapshot']['ref'] == first_review['ref']
          and review['snapshot']['pinned']['revision_id'] == first_review['revision_id']
          # 材料不带；#79 新加的整体经营状态、关键风险与问题照带，没写的给标准句。
          and [block['id'] for block in review['snapshot']['blocks']]
          == ['overall_state', 'results', 'gaps', 'causes', 'key_changes', 'implications', 'key_risks', 'issues']
          and '整体经营状态：暂无' in review_md and '关键风险：暂无' in review_md
          and all(block['pinned']['revision_id'] == first_review['revision_id'] for block in review['snapshot']['blocks'])
          and review['principal']['principal_id'] == actor_id['ceo']
          and f"### 已确认的公司复盘《公司复盘 2036-01》 `{first_review['ref']}`" in review_md
          and '2036-01 营收达成八成' in review_md and '复盘草稿' not in review_md
          and {'ref': first_review['ref']} in one['coverage']['basis']['evidence']
          and {'ref': f"{first_review['ref']}#results"} in one['coverage']['basis']['evidence'])

    now = datetime.now(timezone.utc)
    later_review = company_review('2036-02', now.strftime('%Y-%m-%dT%H:%M:%S.%fZ'))
    # 时点比它早一秒、确认在它之后：取的仍是时点最新的那条。
    earlier_review = company_review('2036-03', (now - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S.%fZ'))
    two = flow.context('agent_a', pid, question)
    check('with_a_later_confirmed_review_the_latest_by_as_of_is_carried',
          two['context_pack']['carried']['company_review']['snapshot']['ref'] == later_review['ref']
          and earlier_review['ref'] not in two['context_pack']['markdown']
          and first_review['ref'] not in sections(two['context_pack']['markdown'])['形成时带入'])

    unit = made['ResponsibilityUnit']['object_id']

    def unit_goal_of(title):
        return flow.create('a', 'LongTermGoal', 'a', {
            'title': title, 'scope': 'unit', 'horizon': '2030', 'parent_ref': latest(unit),
            'goal_ref': latest(company_goal), 'blocks': {'target': {'text': f'{title}：结果'}}})['result']

    effective = unit_goal_of('有效的单元目标（#64）')
    flow.gate('ceo', 'world_confirm_long_term_goal', effective['object_id'], {'outcome': 'accepted'})
    ended = unit_goal_of('终止的单元目标（#64）')
    flow.gate('ceo', 'world_confirm_long_term_goal', ended['object_id'], {'outcome': 'accepted'})
    flow.gate('ceo', 'world_cancel', ended['object_id'], {'content': {'text': '不再有效'}})
    draft = unit_goal_of('草稿的单元目标（#64）')
    three = flow.context('agent_a', pid, question)
    goals = three['context_pack']['carried']['long_term_goals']
    expected = [f"{item['object_id']}@{item['version']}" for item in listed(domain_id=f['domains']['a'], type='LongTermGoal')
                if (item['lifecycle'] or {}).get('status') == 'confirmed' and item['object_id'] != unit_goal]
    goals_md = sections(three['context_pack']['markdown'])['形成时带入']
    check('forming_a_period_goal_carries_the_units_confirmed_long_term_goals_and_not_terminated_drafts_or_its_own',
          [goal['ref'] for goal in goals] == expected and effective['ref'] in expected
          and not {ended['object_id'], draft['object_id'], unit_goal} & {goal['object_id'] for goal in goals}
          and all(goal['lifecycle']['status'] == 'confirmed' and goal['object_type'] == 'LongTermGoal' for goal in goals)
          and [block['ref'] for block in next(goal for goal in goals if goal['ref'] == effective['ref'])['definition_refs']]
          == [f"{effective['ref']}#alignment", f"{effective['ref']}#target"]
          and f"### 有效的长期目标《有效的单元目标（#64）》 `{effective['ref']}`" in goals_md
          and f"### 有效的长期目标《终止的单元目标（#64）》" not in goals_md
          and {'ref': f"{effective['ref']}#target"} in three['coverage']['why']['evidence']
          and {'ref': f"{effective['ref']}#alignment"} not in three['coverage']['why']['evidence']
          and flow.context('outsider', pid, question)['context_pack'] == three['context_pack'])
    tiny_form = flow.context('agent_a', pid, {**question, 'budget': {'max_chars': 10}})
    check('the_formation_carry_in_is_never_trimmed',
          tiny_form['budget']['over_budget'] is True
          and tiny_form['context_pack']['carried'] == three['context_pack']['carried']
          and sections(tiny_form['context_pack']['markdown'])['形成时带入'] == goals_md
          and not [entry for entry in tiny_form['plan']['trimmed'] if entry['kind'] == 'carried'])
