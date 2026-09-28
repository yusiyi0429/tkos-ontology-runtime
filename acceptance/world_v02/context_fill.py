"""tkos.world/0.2 独立验收的取上下文补齐（票 #64 第一段，契约第 15.3 节）。

放在列对象之后、撤销 CEO 指派之前跑（要用 CEO 登记委托），对象都新建，不动前面场景的主干。单元 a 的 DRI 在主干的
单元长期目标下建一条周期目标并承诺，CEO 给天枢登记门的委托，天枢代 CEO 确认；再在它下面建一条 Mission，DRI 指派
Owner。从周期目标与 Mission 出发经 HTTP 取上下文，核对：Why 沿单元长期目标多取一跳到公司级长期目标，覆盖追到
公司级长期目标、Strategy 与 Company 的块或组件；Markdown 开头是六问指引、以六问为节；代记的事件行写出记录者与
被代记的人，指派的写出被指派者；同一世界状态两次调用结果相同；收紧预算时裁剪顺序同 0.1。MCP 取上下文只交四项
由前面的 mcp_end_to_end 场景在同一个 API 进程上核对。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

V02 = 'tkos.world/0.2'
SECTIONS = ['## 六问指引', '## 为什么', '## 做什么', '## 谁负责', '## 现在怎样', '## 发生了什么', '## 凭什么']
# 同一层里先裁的在前（同 0.1）：跨链关系、多取的一跳、块、快照。
TRIM_RANK = {'relations': 0, 'hop': 1, 'block': 2, 'snapshot': 3}


def at(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def context_fill(book, h, f, flow, trunk):
    check = book.check
    made = trunk['made']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    question = {'question': '这个周期目标为什么做、做什么、谁负责、现在怎样？'}

    def latest(oid):
        business = flow.read('outsider', oid)['business']
        return f"{oid}@{business['version']}"

    def sections(markdown):
        return {part.split('\n', 1)[0]: (part.split('\n', 1) + [''])[1].strip('\n')
                for part in ('\n' + markdown).split('\n## ')[1:]}

    # ---------------------------------------------------------------- 播种：周期目标（代 CEO 确认）与它下面的 Mission
    unit_goal, company_goal = made['LongTermGoal.unit']['object_id'], made['LongTermGoal']['object_id']
    strategy, company = made['Strategy']['object_id'], made['Company']['object_id']
    # 形成锚定（#60）：scope 里已有已确认的公司复盘时，周期目标要依据其中一条（前面的 goal_closure 场景确认过）。
    review = flow.read('outsider', company)['records']['confirmed_review']
    goal = flow.create('a', 'PeriodGoal', 'a', {
        'title': '3 月目标（取上下文）', 'period': '2027-03', 'goal_ref': latest(unit_goal),
        **({'review_ref': review['snapshot']['ref']} if review else {}),
        'blocks': {'outcome': {'components': [{'id': 'fill-o1', 'type': 'outcome', 'text': '续签两家'}]},
                   'acceptance': {'components': [{'id': 'fill-ac1', 'type': 'acceptance_criterion', 'text': '合同归档'}]}}}
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
    check('from_a_unit_period_goal_why_covers_blocks_or_components_of_the_company_goal_the_strategy_and_the_company',
          coverage['why']['answered'] and all(reached.values()) and set(why) <= refs
          and any('/' in ref for ref in reached[company_goal])  # 公司级目标的衡量是组件，引用细到组件
          and guide_why.startswith('- 为什么：长期目标 `')
          and all(f"{label} `{targets[oid]}" in guide_why
                  for label, oid in (('公司级长期目标', company_goal), ('战略', strategy), ('公司', company)))
          and guide_why.index('公司级长期目标') < guide_why.index('战略') < guide_why.index('→ 公司 '))

    check('the_markdown_opens_with_the_six_question_guide_and_takes_the_six_questions_as_its_sections',
          markdown.startswith(f"# 上下文\n\n问题：{question['question']}\n\n出发对象：`{goal['ref']}`\n\n## 六问指引\n")
          and [line for line in markdown.splitlines() if line.startswith('## ')] == SECTIONS
          and [line.split('：', 1)[0] for line in parts['六问指引'].splitlines()[1:]]
          == ['- 为什么', '- 做什么', '- 谁负责', '- 现在怎样', '- 发生了什么', '- 凭什么']
          and f"`{targets[strategy]}#choices`" in parts['为什么'] and f"`{targets[company]}#identity`" in parts['为什么']
          and f"`{goal['ref']}#outcome/fill-o1`" in parts['做什么']
          and f"`{goal['ref']}#constraint`" in parts['凭什么']
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

    # ---------------------------------------------------------------- 预算（同 0.1）
    when = {f"event:{event['event_id']}": at(event['occurred_at']) for event in in_pack.values()}

    def trimmed_in_order(result):
        """裁剪顺序同 0.1：先裁最旧的事件，再由远及近逐层裁跨链关系、多取的一跳、块、快照，当前对象的块不裁；
        六问的节都在，指引不再指向裁掉的内容（推出当前生命周期的事件一直在「现在怎样」里）。"""
        over = [entry for entry in result['plan']['trimmed'] if entry['reason'] == 'over_budget']
        trimmed_events = [entry['key'] for entry in over if entry['kind'] == 'event']
        rest = [(-entry['level'], TRIM_RANK[entry['kind']]) for entry in over if entry['kind'] != 'event']
        stage = f"event:{result['context_pack']['layers'][0]['object']['lifecycle']['event_id']}"
        gone = [entry['key'] if entry['kind'] == 'event' else entry['key'].split(':', 1)[1] for entry in over]
        guide = sections(result['context_pack']['markdown'])['六问指引']
        return (over[:len(trimmed_events)] == [entry for entry in over if entry['kind'] == 'event']
                and [when[key] for key in trimmed_events] == sorted(when[key] for key in trimmed_events)
                and rest == sorted(rest) and all(level != 0 for level, _ in rest)
                and result['context_pack']['layers'][0]['blocks'] == layers[0]['blocks']
                and [line for line in result['context_pack']['markdown'].splitlines() if line.startswith('## ')]
                == SECTIONS
                and not [ref for ref in gone if f"`{ref}" in guide and ref != stage]
                and result['budget']['used_chars'] == len(result['context_pack']['markdown'])
                and result['budget']['over_budget'] is (result['budget']['used_chars'] > result['budget']['max_chars']))

    tight = flow.context('agent_a', pid, {**question, 'budget': {'max_chars': len(markdown) // 2}})
    tiny = flow.context('agent_a', pid, {**question, 'budget': {'max_chars': 10}})
    check('a_tight_budget_trims_in_the_0_1_order_keeps_the_current_object_and_the_guide_only_points_at_what_is_left',
          tight['plan']['trimmed'] and trimmed_in_order(tight) and trimmed_in_order(tiny)
          and tiny['budget']['over_budget'] is True
          and f"hop:{targets[company_goal]}" in [entry['key'] for entry in tiny['plan']['trimmed']]
          and tiny['context_pack']['layers'][1]['hop'] is None)
