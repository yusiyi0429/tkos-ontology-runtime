"""tkos.world/0.2 独立验收矩阵（票 #65，不冻结）：由登记推出矩阵的格，把每条检查归到格上，判覆盖。

格分票面十项：
- 每个已实现动作（支持登记）× {通过, 拒绝}；
- 每张状态表（登记 ``lifecycles`` 与 ``issue.lifecycle``）的每一行 × {进入, 拒绝, 幂等}：
  - 进入：从起始状态由「谁记」记下，读回进入状态与推出它的事件（自环不换推出事件）；
  - 拒绝：同一起始状态、同一动作，记录者不符（FORBIDDEN）或守卫不成立（INVALID_STATE），库快照不变；
  - 幂等：重复这一格不产生第二次状态变化——同键重放返回原回执、不写第二条事件，换键再记被拒（INVALID_STATE）
    或落到进入状态的自环（状态与推出事件不变）；
- 其余八项（重复与迟记、撤回与一轮重走写回、组件与引用、分组读投影、列对象与外部引用、代记、Issue 流转、
  控制面安装）的主题格，能从登记枚举的就从登记推（去重规则、可补记的事件种类、撤回的 never 清单、各类型的一轮、
  引用形式、委托的动作族）。

``CHECKS`` 是「场景 → 检查名 → 它证明的格」的对照：每条检查只属于一个场景（即 0.1 报告里的组），可以不证明任何格
（例如迁移、上下文包），但必须列在这里。报告里出现没列的检查、列了没跑、有格没被任一通过的检查覆盖，都不算通过；
不适用的格在 ``NOT_APPLICABLE`` 写明理由，不算未覆盖。本模块只读登记与支持登记，不导入被测实现。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / 'docs/contracts/world-registry-0.2.json').read_text())
SUPPORT = json.loads((ROOT / 'docs/runtime-world-support-0.2.json').read_text())

SOURCES = {
    'protocol': 'tkos.world/0.2',
    'spec': 'GitHub issue #46（测试决定：主缝）',
    'ticket': 'GitHub issue #65',
    'contract': 'docs/contracts/tkos-world-0.2.md',
    'registry': 'docs/contracts/world-registry-0.2.json',
}

# ------------------------------------------------------------------ 票面十项
ITEMS = {
    'actions': '每个动作至少一条通过、一条拒绝',
    'lifecycles': '每张状态表的每一格（进入、拒绝、幂等）',
    'duplicates_and_late': '重复与迟记',
    'withdrawal_and_rounds': '撤回与一轮重走写回',
    'components_and_refs': '组件合并、台账与跨修订引用稳定',
    'read_projection': '分组读投影',
    'listing_and_external_refs': '列对象与外部引用',
    'delegation': '代记的通过与各类拒绝',
    'issue_flow': 'Issue 完整流转',
    'control_plane': '控制面安装',
}
ACTION_KINDS = {'passes': '通过', 'refused': '拒绝'}
ROW_KINDS = {'enter': '进入', 'refused': '拒绝', 'idempotent': '幂等'}
TYPE_NAMES = {**{item['type']: item['display_name'] for item in REGISTRY['objects']}, 'Issue': '问题'}
SNAKE = {'Strategy': 'strategy', 'LongTermGoal': 'long_term_goal', 'PeriodGoal': 'period_goal', 'Mission': 'mission',
         'Task': 'task', 'Activity': 'activity', 'Issue': 'issue'}


def tables(registry=REGISTRY):
    """状态表：各类对象的（登记 lifecycles），加 Issue 的（issue.lifecycle）。"""
    return {**registry['lifecycles'], 'Issue': registry['issue']['lifecycle']}


def row_key(object_type, row):
    """状态表一行的键：<类型>:<起始>><进入>:<动作>[:<结果>][:<处置>]。"""
    key = f"{object_type}:{row['from']}>{row['to']}:{row['action']}"
    return key + ''.join(':' + value for value in (row['outcome'], row['disposition']) if value)


def cell_check(object_type, row, kind):
    """state_cells 场景为这一行这一栏记的检查名。"""
    extra = ''.join('_' + value for value in (row['outcome'], row['disposition']) if value)
    return f"cell_{SNAKE[object_type]}_{row['from']}_{row['action'][len('world_'):]}{extra}_to_{row['to']}_{kind}"


def rows(registry=REGISTRY):
    """全部状态表行：[(类型, 行)]，按登记顺序。"""
    return [(object_type, row) for object_type, spec in tables(registry).items() for row in spec['transitions']]


# ------------------------------------------------------------------ 主题格
def topic_cells(registry=REGISTRY):
    """票面其余八项的格 → 中文说明。能从登记枚举的从登记推。"""
    actions = {item['action']: item['display_name'] for item in registry['actions']}
    rounds = {t: spec['rounds'] for t, spec in registry['lifecycles'].items() if spec.get('rounds')}
    duplicates = {'same_key_same_request': '同一记录者同键重复提交同一请求：返回原回执、不写第二条',
                  'same_key_other_request': '同键不同请求：IDEMPOTENCY_CONFLICT',
                  'other_keys_record_events': '幂等键不同的记录事件照记',
                  'state_mismatch': '门事件与生命周期事件与当前状态不匹配即拒绝，重复不产生第二次状态变化'}
    cells = {'duplicates_and_late': {
        **{f'duplicates:{key}': duplicates[key] for key in registry['rules']['duplicates']},
        'duplicates:snapshot_unique': '同一主体同一时点的快照只有一条',
        **{f'late:backdated:{kind}': f'{kind} 可以补记过去的时刻'
           for kind in registry['rules']['ordering']['backdated_kinds']},
        'late:others_not_backdated': '其余事件的发生时刻就是记录时刻，不接受补记',
        'late:occurred_not_after_recorded': '发生时刻不得晚于记录时刻（未来的外部事件与快照被拒）',
        'late:read_in_occurrence_order': '读取按发生时刻',
        'late:marked_on_read': '读取时标「迟记」：记下时同一主体已有发生时刻晚于它的事件',
        'late:state_read_by_as_of': '状态刷新的发生时刻取 as_of：取状态按时点，读投影给 as_of 最新的一条',
    }}
    withdrawal = {
        'withdrawal:reverts_and_keeps_original': '撤回让状态回到原事件之前，这一段改由撤回事件推出，原事件保留',
        'withdrawal:read_as_withdrawn': '取事件给出被撤回关系',
        'withdrawal:same_action_same_role': '撤回由同一动作、同一角色记',
        'withdrawal:only_the_producer_of_the_current_state': '只撤推出当前状态的那条',
        'withdrawal:takes_back_formal_content': '撤回让内容成为正式的那条确认，正式内容一起收回',
        'withdrawal:record_events_are_not_withdrawn': '只有门事件与生命周期事件可撤回，记录事件不撤回',
        **{f'withdrawal:never:{item}': f'不能撤回：{item}' for item in registry['rules']['withdrawal']['never']},
    }
    round_cells = {
        'opens_without_changing_the_stage': '开轮不改生命周期段，读投影给出这一轮',
        'one_round_at_a_time': '一轮未结束前不能再开',
        'write_back': '确认接受时写回候选为新修订，段与推出事件不变，正式内容指针挪过去',
        'returned_round_is_void': '被退回则本轮作废，什么都不写回',
        'without_candidate': '不带候选的一轮：开轮被拒（Strategy：由再确认结束）',
        'formal_content_only_through_a_round': '有正式内容以后正式块只经一轮改，活动内容照常直接改',
    }
    for object_type in rounds:
        for key, text in round_cells.items():
            withdrawal[f'round:{object_type}:{key}'] = f'{TYPE_NAMES[object_type]}：{text}'
        # 一轮进行中进入终态即作废，撤回那条事件则连同候选恢复（#69，补 47，登记 rounds.voided_in）。
        states = {item['id']: item['display_name'] for item in registry['lifecycles'][object_type]['states']}
        for state in rounds[object_type].get('voided_in', []):
            withdrawal[f'round:{object_type}:voided_in:{state}'] = (
                f'{TYPE_NAMES[object_type]}：一轮进行中进入{states[state]}，这一轮作废，候选不写回、正式内容不变')
        if rounds[object_type].get('voided_in'):
            withdrawal[f'round:{object_type}:restored_by_withdrawal'] = (
                f'{TYPE_NAMES[object_type]}：撤回让它进入终态的那条事件，作废的一轮连同候选原样恢复（同撤回一条退回）')
    cells['withdrawal_and_rounds'] = withdrawal
    cells['components_and_refs'] = {
        'components:id_from_writer_or_service': '组件 id 由写入者给或由服务生成',
        'components:merge_by_id': '修订按 id 合并组件：改写不换 id，未提到的保留，块给 null 清空',
        'components:ledger': '组件台账记下类型、所在块、出现与删除的版本',
        'components:removed_id_not_reused': '删除留痕、id 不复用',
        'components:no_move_between_blocks': '组件不换块',
        'components:unique_and_typed': '同一对象内 id 唯一，组件类型按块的登记',
        **{f"refs:form:{item['form']}": f"{item['display_name']}引用（{item['written']}）读回业务形式与钉定结构"
           for item in registry['reference_forms']},
        'refs:unresolvable_refused': '钉不住的引用（不存在的对象、版本、块、组件、事件，scope 外）被拒',
        'refs:stable_across_revisions': '被引用对象出新修订后引用仍钉在原修订，旧版本按版本号读回原样',
    }
    cells['read_projection'] = {
        'read:three_groups': '取对象按 business、identity、records 三组读回',
        'read:business:blocks_components_ledger': 'business：类别、块类别、组件、台账、空块标准句',
        'read:business:formal': 'business：正式内容指针（生效修订）',
        'read:business:round': 'business：进行中的一轮',
        'read:business:projection': 'business：投影项（Mission 的 Task 预期结果与质量标准、责任单元的战役引用，读取时从下级'
                                    '对象投影、不存；其余类型为空）',
        'read:identity:responsible': 'identity：责任人及其来源（角色或属性）',
        'read:identity:delegations': 'identity：覆盖对象所在域的当前有效委托',
        'read:records:lifecycle': 'records：生命周期与推出它的事件',
        'read:records:latest_state': 'records：最新快照（标明未经确认）',
        'read:records:confirmed_review': 'records：已确认复盘',
        'read:records:open_issues': 'records：提出过、还没处置的问题',
        'read:legacy_0_1_view': '0.1 对象的 0.2 视图（view 参数）',
        'read:scope_rule': 'scope 级读规则：scope 内可读，scope 外 404',
    }
    cells['listing_and_external_refs'] = {
        'list:filters': '列对象按周期、单元、域、类型筛选与组合',
        'list:header': '对象头逐项同取对象',
        'list:pagination': '按 next_cursor 分页，不漏不重，游标只续同一读取',
        'list:refusals': '坏参数 INVALID_REQUEST，scope 外的单元与域 NOT_FOUND',
        'list:0_1_scope': '0.1 的 scope 里列出 0.1 对象',
        'external_refs:lookup': '按外部系统与 id 查回对象（有查找索引）',
        'external_refs:unique': '外部引用在 scope 内唯一（按各对象最新修订），释放后可被别的对象占用',
        'external_refs:shape': '外部引用缺 id 被拒',
    }
    delegation = {
        **{f"delegation:passes:{family['id']}": f"代记通过：{family['display_name']}族"
           for family in registry['delegation']['families']},
        'delegation:grant': '登记委托：委托人本人记一条以 Company 为主体的记录事件',
        'delegation:revoke': '撤销委托：委托人本人、只撤一次',
        'delegation:recorded_fields': '事件、回执与取事件记下记录者、被代记的人、外部确认记录与用到的委托',
        'delegation:replay': '代记命令同键重放返回原回执',
        'delegation:withdrawn_in_person': '被代记的人本人撤回代记的事件',
        'delegation:refused:expired': '拒绝：委托已过期',
        'delegation:refused:revoked': '拒绝：委托已撤销（重放也不成功）',
        'delegation:refused:family_out_of_scope': '拒绝：动作族不在委托范围',
        'delegation:refused:domain_out_of_scope': '拒绝：对象所在域不在委托范围',
        'delegation:refused:grantor_no_longer_valid': '拒绝：委托人不再是 scope 内有效的人',
        'delegation:refused:person_may_not_record': '拒绝：被代记的人自己无权记',
        'delegation:refused:recorder_not_the_delegate': '拒绝：记录者是人、不是受托人，或代一位没有委托给它的人',
        'delegation:refused:request_shape': '拒绝：代记带写入声明、外部确认时刻在将来、给不可代记的动作或记录事件带代记',
        **{f"delegation:refused:pending_family:{family['id']}": f"拒绝：登记待决的动作族（{family['display_name']}）"
           for family in registry['delegation']['pending_families']},
    }
    cells['delegation'] = delegation
    cells['issue_flow'] = {
        'issue:raise_route_own_dispose': '一个问题从提出、路由、承接走到处置',
        'issue:event_on_component_and_object': '每个 Issue 动作一条记录事件，主体是问题组件与主受影响对象',
        'issue:same_issue_across_snapshots': '后续快照带同一个 id 是同一个问题',
        'issue:recurrence_under_new_id': '已处置的不再提出，复发用新 id 并引用原问题',
        'issue:escalated_and_rerouted': '转交或上报后回到待路由、再路由与承接',
        'issue:owner_across_units': '承接人不限单元：另一单元的人承接、退回与处置（按 scope 判权），scope 外的人与 Agent 仍被拒',
        'issue:correction': '更正可以指向 Issue 事件，问题的状态不变',
        'issue:business_objects_unchanged': 'Issue 动作不出修订、不改任何业务对象与生命周期',
        'issue:events_listed': '取事件列出 Issue 事件（主受影响对象与快照）',
        'issue:request_refusals': '请求拒绝：缺理由、处置不在六类、承接人不是人、issue_ref 不是问题组件、Agent 缺声明',
    }
    cells['control_plane'] = {
        'control:profile_pinned': '0.2 profile 钉住契约与登记字节',
        'control:profile_mismatch_refused': '登记字节与钉定不符时 profile 整体拒绝、什么都不留',
        'control:default_policy': 'scope 默认契约为 0.2',
        'control:support_registry': '支持登记只列已实现的动作与类型',
        'control:activation_policies': '每个域装上开放 0.2 动作的激活策略',
        'control:binding_gate': '绑定经 0.2 闸门钉定 0.2 profile，伪造 profile 被拒',
    }
    return cells


def cells(registry=REGISTRY, support=SUPPORT):
    """矩阵的全部格：格 id → {'item', 'title'}，按票面十项的顺序。"""
    display = {item['action']: item['display_name'] for item in registry['actions']}
    found = {}
    for action in support['actions']:
        for kind, text in ACTION_KINDS.items():
            found[f'action:{action}:{kind}'] = {'item': 'actions', 'title': f'{display[action]}（{action}）：{text}'}
    for object_type, row in rows(registry):
        states = {s['id']: s['display_name'] for s in tables(registry)[object_type]['states']}
        label = display[row['action']] + ''.join(f'·{value}' for value in (row['outcome'], row['disposition']) if value)
        for kind, text in ROW_KINDS.items():
            found[f'lifecycle:{row_key(object_type, row)}:{kind}'] = {
                'item': 'lifecycles',
                'title': f"{TYPE_NAMES[object_type]} {states[row['from']]}→{states[row['to']]}（{label}）：{text}"}
    for item, named in topic_cells(registry).items():
        for cell, title in named.items():
            found[cell] = {'item': item, 'title': title}
    return found


CELLS = cells()

_LTG_NO_ROUND = ('长期目标不单独开轮：没有开轮动作（登记 rounds.opened_by 为 null），已确认时带候选的确认一步写回、'
                 '不带候选的确认与退回被拒（契约第 10.2、12 节），所以没有「进行中的一轮」')
NOT_APPLICABLE = {
    'round:LongTermGoal:opens_without_changing_the_stage': _LTG_NO_ROUND + '；写回见 round:LongTermGoal:write_back。',
    'round:LongTermGoal:one_round_at_a_time': _LTG_NO_ROUND + '，不存在「一轮未完再开」。',
    'round:LongTermGoal:returned_round_is_void': _LTG_NO_ROUND + '；已确认时的退回被拒见 '
                                                 'long_term_goal_a_confirmed_goal_is_confirmed_again_only_with_a_candidate。',
    'round:LongTermGoal:voided_in:terminated': _LTG_NO_ROUND + '，终止时没有可作废的一轮（#69）。',
    'round:LongTermGoal:restored_by_withdrawal': _LTG_NO_ROUND + '，撤回终止也没有可恢复的一轮（#69）。',
}


# ------------------------------------------------------------------ 检查 → 格
def _a(action, kind):
    return f'action:{action}:{kind}'


def P(*actions):
    return tuple(_a(action, 'passes') for action in actions)


def R(*actions):
    return tuple(_a(action, 'refused') for action in actions)


def L(object_type, spec, *kinds):
    """状态表的格：spec 写作 '<起始>><进入>:<动作>[:<结果或处置>]'。"""
    return tuple(f'lifecycle:{object_type}:{spec}:{kind}' for kind in kinds)


def _walk_checks(object_type):
    """assign_lifecycle 场景里 Task 与 Activity 共用的逐格检查（名字带类型前缀）。"""
    n, t = object_type.lower(), object_type
    names = {
        f'{n}_an_unassigned_{n}_cannot_start': R('world_start'),
        f'{n}_assignment_one_level_up_to_a_holder_of_the_role_only':
            R('world_assign') + L(t, 'unassigned>assigned:world_assign', 'refused'),
        f'{n}_unassigned_assign_to_assigned':
            P('world_assign') + L(t, 'unassigned>assigned:world_assign', 'enter') + ('read:records:lifecycle',),
        f'{n}_identity_names_the_assignee_by_attribute': ('read:identity:responsible',),
        f'{n}_assigned_reassign_keeps_the_stage_and_its_producer':
            L(t, 'assigned>assigned:world_assign', 'enter') + L(t, 'unassigned>assigned:world_assign', 'idempotent'),
        f'{n}_only_its_responsible_starts_it': R('world_start') + L(t, 'assigned>in_progress:world_start', 'refused'),
        f'{n}_assigned_start_to_in_progress': P('world_start') + L(t, 'assigned>in_progress:world_start', 'enter'),
        f'{n}_in_progress_reassign_keeps_the_stage_and_its_producer': L(t, 'in_progress>in_progress:world_assign', 'enter'),
        f'{n}_in_progress_deliver_to_delivered_with_a_lifecycle_event_pinned_to_the_current_revision':
            P('world_deliver') + L(t, 'in_progress>delivered:world_deliver', 'enter'),
        f'{n}_delivered_reassign_keeps_the_stage_and_its_producer': L(t, 'delivered>delivered:world_assign', 'enter'),
        f'{n}_a_withdrawal_is_recorded_by_the_role_of_the_original': ('withdrawal:same_action_same_role',),
        f'{n}_withdrawing_the_latest_delivery_returns_to_in_progress_and_keeps_the_original':
            ('withdrawal:reverts_and_keeps_original',),
        f'{n}_reading_events_shows_the_delivery_withdrawn_by_the_withdrawal': ('withdrawal:read_as_withdrawn',),
        f'{n}_an_earlier_event_cannot_be_withdrawn': ('withdrawal:only_the_producer_of_the_current_state',),
        f'{n}_a_withdrawal_cannot_be_withdrawn': ('withdrawal:never:withdrawal',),
        f'{n}_delivered_reject_to_adjusting': P('world_reject') + L(t, 'delivered>adjusting:world_reject', 'enter'),
        f'{n}_adjusting_reassign_keeps_the_stage_and_its_producer': L(t, 'adjusting>adjusting:world_assign', 'enter'),
        f'{n}_adjusting_deliver_to_delivered': P('world_deliver') + L(t, 'adjusting>delivered:world_deliver', 'enter'),
        **{f'{n}_{state}_cancel_to_cancelled': P('world_cancel') + L(t, f'{state}>cancelled:world_cancel', 'enter')
           for state in ('unassigned', 'assigned', 'in_progress', 'delivered', 'adjusting')},
        f'{n}_delivered_accept_to_closed': P('world_accept') + L(t, 'delivered>closed:world_accept', 'enter'),
        # 再取消的是从调整中取消的那一个。
        f'{n}_a_closed_or_cancelled_{n}_is_not_delivered_reassigned_or_cancelled_again':
            R('world_deliver', 'world_assign', 'world_cancel') + L(t, 'adjusting>cancelled:world_cancel', 'idempotent'),
        f'{n}_closed_reopen_to_in_progress': P('world_reopen') + L(t, 'closed>in_progress:world_reopen', 'enter'),
    }
    return names


def _mission_open_states():
    return ('draft', 'committed', 'established', 'in_progress', 'delivered', 'adjusting')


PG, LTG, M, S, I = 'PeriodGoal', 'LongTermGoal', 'Mission', 'Strategy', 'Issue'
PG_CONFIRM = 'committed>confirmed:world_confirm_period_goal:accepted'
PG_RETURN = 'committed>draft:world_confirm_period_goal:returned'
LTG_CONFIRM = 'draft>confirmed:world_confirm_long_term_goal:accepted'
M_CONFIRM = 'committed>established:world_confirm_mission:accepted'
M_RETURN = 'committed>draft:world_confirm_mission:returned'
S_AGREE_OPEN = 'draft>draft:world_agree_strategy'
S_DESIGNATE = 'draft>draft:world_assign_strategy_round'

CHECKS = {
    'migration': {
        'the_upgrade_applied_0039_to_this_database_and_repeating_it_applies_nothing': (),
        '0039_is_the_newest_applied_migration': (),
        'the_event_log_carries_every_0_2_field': (),
        'existing_0_1_events_read_as_0_1_with_none_of_the_0_2_fields': (),
    },
    'control_plane': {
        'a_0_2_profile_install_with_other_registry_bytes_is_refused_and_nothing_is_left_behind':
            ('control:profile_mismatch_refused',),
        'install_profile_records_the_0_2_profile_pinned_to_the_contract_and_registry_bytes': ('control:profile_pinned',),
        'the_new_scope_defaults_to_world_0_2': ('control:default_policy',),
        'the_0_2_support_registry_lists_only_the_implemented_actions_and_types': ('control:support_registry',),
        'every_domain_has_an_activation_policy_granting_the_implemented_0_2_actions': ('control:activation_policies',),
    },
    'company': {
        'the_ceo_creates_the_company_over_http_under_world_0_2': P('world_create_object'),
        'the_object_is_read_back_in_three_groups': ('read:three_groups',),
        # #80 改名：Company 只剩身份块，空块标准句挪到 objects 的
        # an_empty_block_reads_back_with_the_standard_sentence_named_by_the_registry。
        'business_carries_type_category_the_identity_block_with_its_components_and_no_projection':
            ('read:business:blocks_components_ledger', 'read:business:formal', 'read:business:projection'),
        'identity_names_the_ceo_by_role_and_records_are_empty_for_the_company': ('read:identity:responsible',),
        'the_company_is_read_as_world_0_2': (),
        'exactly_one_object_created_record_event_under_0_2_points_back_to_its_receipt': P('world_create_object'),
        'exactly_one_receipt_is_written_for_the_creation': (),
        'the_binding_pins_the_0_2_profile_through_the_world_gate': ('control:binding_gate',),
        'an_identity_assigned_only_in_another_domain_reads_the_company_by_the_world_rule': ('read:scope_rule',),
        'the_0_2_receipt_is_read_by_the_world_rule': ('read:scope_rule',),
        'an_identity_from_another_scope_cannot_read_the_company': ('read:scope_rule',),
        'read_endpoints_not_yet_wired_for_0_2_refuse_instead_of_reading_it_as_0_1': (),
        'replaying_the_same_command_returns_the_original_receipt': ('duplicates:same_key_same_request',),
        'reusing_a_key_for_a_different_command_is_refused': ('duplicates:same_key_other_request',),
        'the_world_gate_refuses_a_0_2_binding_whose_profile_does_not_pin_the_exact_contract': ('control:binding_gate',),
        'the_event_log_refuses_a_phase_on_a_0_2_event': (),
        'the_event_log_refuses_0_2_fields_on_a_0_1_event': (),
        'the_event_log_refuses_on_behalf_recording_of_a_record_event': ('delegation:refused:request_shape',),
    },
    'objects': {
        'components_keep_the_writers_id_or_get_one_from_the_service': ('components:id_from_writer_or_service',),
        'the_component_ledger_is_kept_from_creation': ('components:ledger',),
        'a_responsibility_unit_is_defined_by_a_unit_entry_pinned_by_component': ('refs:form:component',),
        'a_component_reference_is_read_back_in_both_forms': ('refs:form:component',),
        'object_block_and_event_references_are_pinned_and_read_back_in_both_forms':
            ('refs:form:object', 'refs:form:block', 'refs:form:event'),
        'a_whole_block_reference_in_the_0_1_form_still_works_in_a_0_2_object': ('refs:form:block',),
        'a_declared_scene_may_be_a_responsibility_unit': P('world_create_object'),
        'a_declared_scene_may_be_a_period_goal': P('world_create_object'),
        'a_plan_item_keeps_its_responsible_as_a_record_and_the_execution_plan_is_an_activity_block':
            ('read:business:blocks_components_ledger',),
        'a_component_scope_is_pinned_as_an_object_reference': ('refs:form:object',),
        'responsibility_by_attribute_is_named_as_such_and_empty_until_assigned': ('read:identity:responsible',),
        'a_plan_item_takes_the_four_optional_attributes_and_reads_them_back':
            ('read:business:blocks_components_ledger',),
        'the_activity_is_marked_as_a_candidate_type': ('read:business:blocks_components_ledger',),
        'a_mission_projects_its_tasks_expected_results_at_read_time_and_stores_none': ('read:business:projection',),
        'a_responsibility_unit_projects_the_missions_of_its_domain_as_navigation_only': ('read:business:projection',),
        'each_of_the_seven_types_reads_back_blocks_components_ledger_and_pinned_relations':
            P('world_create_object') + ('read:business:blocks_components_ledger',),
        'an_empty_block_reads_back_with_the_standard_sentence_named_by_the_registry':
            ('read:business:blocks_components_ledger',),
        'every_creation_wrote_one_0_2_object_created_event_pinned_to_its_first_revision': P('world_create_object'),
    },
    'rejections': {
        'an_ic_cannot_create_the_company': R('world_create_object'),
        'an_agent_cannot_create_the_company': R('world_create_object'),
        'authorization_is_refused_before_any_protocol_error': R('world_create_object'),
        'a_payload_field_outside_the_contract_is_refused': R('world_create_object'),
        'an_empty_block_cannot_pose_as_content': R('world_create_object'),
        # #80 改名：业务对象里已没有不收组件的块，这里拒身份块不收的组件类型；不收组件的块挪到 state_events 的
        # a_component_in_a_state_block_that_takes_none_is_refused。
        'a_component_type_the_identity_block_does_not_take_is_refused':
            R('world_create_object') + ('components:unique_and_typed',),
        'an_external_ref_without_an_id_is_refused': R('world_create_object') + ('external_refs:shape',),
        'a_state_snapshot_is_not_written_by_creating_an_object': R('world_create_object'),
        'a_scope_has_exactly_one_company': R('world_create_object'),
        'a_wrong_expected_version_is_refused': R('world_create_object'),
        # 名字是 #49 时留下的：登记委托已实现，这里拒的是缺字段的信封。
        'an_action_not_yet_implemented_under_0_2_is_refused_by_the_envelope': R('world_grant_delegation'),
        'a_0_2_request_to_a_scope_defaulting_to_0_1_is_protocol_not_supported_and_writes_nothing': (),
    },
    'coexistence': {
        'the_same_action_name_with_the_0_1_contract_is_handled_by_0_1': (),
        'a_0_1_object_reads_back_in_the_0_1_shape': (),
        'the_0_1_event_is_written_as_a_0_1_row': (),
        'a_0_1_request_to_a_scope_defaulting_to_0_2_is_refused': (),
    },
    'references': {
        **{name: R('world_create_object') + ('refs:unresolvable_refused',) for name in (
            'a_reference_to_an_object_that_does_not_exist_is_refused',
            'a_reference_to_a_version_that_does_not_exist_is_refused',
            'a_reference_to_a_block_the_type_does_not_have_is_refused',
            'a_reference_to_a_component_not_present_in_that_version_is_refused',
            'a_reference_to_an_event_that_does_not_exist_is_refused',
            'a_reference_to_an_object_of_another_scope_is_refused_as_missing',
            'a_reference_to_an_event_of_another_scope_is_refused_as_missing')},
        'an_architecture_reference_that_is_not_a_unit_entry_is_refused': R('world_create_object'),
        'a_component_id_repeated_within_the_object_is_refused':
            R('world_create_object') + ('components:unique_and_typed',),
        'a_component_type_the_block_does_not_allow_is_refused':
            R('world_create_object') + ('components:unique_and_typed',),
        'a_plan_item_responsible_that_is_no_principal_of_the_scope_is_refused': R('world_create_object'),
        'a_component_scope_is_an_object_reference': R('world_create_object'),
        'a_period_goal_review_reference_points_to_a_state_snapshot': R('world_create_object'),
        'a_declared_scene_is_an_object_reference': R('world_create_object'),
        'an_agent_does_not_create_objects': R('world_create_object'),
        'creation_rights_stay_as_in_0_1': R('world_create_object'),
        'domain_placement_stays_as_in_0_1': R('world_create_object'),
        'a_domain_has_exactly_one_responsibility_unit': R('world_create_object'),
    },
    'revise_relate': {
        'each_revision_writes_exactly_one_object_revised_event_and_one_receipt': P('world_revise_object'),
        'a_rewritten_component_keeps_its_id_and_untouched_fields_and_blocks_stay': ('components:merge_by_id',),
        'an_older_version_still_reads_back_as_it_was': ('refs:stable_across_revisions',),
        'a_gated_draft_keeps_no_effective_revision': ('read:business:formal',),
        'a_component_reference_to_the_older_version_stays_pinned_to_that_revision': ('refs:stable_across_revisions',),
        'the_same_component_id_is_found_in_the_newer_version': ('refs:stable_across_revisions',),
        'an_ungated_object_moves_its_effective_revision_with_the_revision': ('read:business:formal',),
        'a_mission_projection_follows_the_latest_task_revision_while_the_mission_stays_at_its_version':
            ('read:business:projection',),
        'a_removed_component_is_recorded_in_the_ledger_with_its_removal_version': ('components:ledger',),
        'reusing_a_removed_component_id_is_refused':
            R('world_revise_object') + ('components:removed_id_not_reused',),
        'moving_a_component_to_another_block_is_refused_even_when_removed_in_the_same_revision':
            R('world_revise_object') + ('components:no_move_between_blocks',),
        'removing_a_component_not_present_in_that_block_is_refused':
            R('world_revise_object') + ('components:merge_by_id',),
        'a_null_block_clears_it_and_records_its_components_as_removed': ('components:merge_by_id', 'components:ledger'),
        'only_a_responsible_person_up_the_spine_revises': R('world_revise_object'),
        'a_creation_relation_cannot_be_hung_on_another_object': R('world_revise_object'),
        'a_field_written_only_by_the_service_cannot_be_revised': R('world_revise_object'),
        'a_wrong_expected_version_is_refused_for_a_revision': R('world_revise_object'),
        'a_target_that_is_not_the_latest_revision_is_refused': R('world_revise_object'),
        'an_agent_revision_without_a_declaration_is_refused': R('world_revise_object'),
        'an_agent_revision_touching_formal_content_without_human_acceptance_is_refused': R('world_revise_object'),
        'an_agent_revision_touching_only_activity_content_passes_the_declaration_rule_and_still_needs_responsibility':
            R('world_revise_object'),
        'an_agent_holding_the_dri_role_is_not_the_period_goals_responsible': R('world_revise_object'),
        'a_relation_writes_one_relate_event_pinning_the_new_revision_and_each_related_object': P('world_relate'),
        'a_period_goal_depends_on_a_period_goal_and_a_mission': P('world_relate'),
        'a_revision_keeps_the_relations_written_by_relate': P('world_revise_object'),
        'a_period_goal_cannot_depend_on_itself': R('world_relate'),
        'a_relation_list_naming_an_object_twice_is_refused': R('world_relate'),
        'a_period_goal_depends_only_on_period_goals_and_missions': R('world_relate'),
        'a_period_goal_has_no_contributes_to': R('world_relate'),
        'only_a_responsible_person_up_the_spine_relates': R('world_relate'),
        'relating_is_not_on_the_agent_face': R('world_relate'),
        'replaying_a_revision_returns_the_original_receipt_and_writes_nothing_more':
            ('duplicates:same_key_same_request',),
    },
    'state_events': {
        'an_agent_records_an_external_event_under_0_2_with_its_category_and_past_occurrence':
            P('world_record_event') + ('late:backdated:event.recorded',),
        'each_of_the_five_payload_types_is_written_and_read_back_as_an_unconfirmed_time_record':
            P('world_refresh_state'),
        'the_generator_is_the_principal_of_the_writing_credential': (),
        'the_execution_payload_keeps_progress_items_issue_components_and_normalised_entries': (),
        'state_blocks_left_out_read_back_empty_with_the_standard_sentence_named_by_the_registry':
            P('world_refresh_state'),
        'the_new_state_blocks_read_back_as_written': P('world_refresh_state'),
        'an_agent_declares_a_unit_or_a_goal_as_the_scene_of_its_snapshot': P('world_refresh_state'),
        'each_snapshot_wrote_one_state_refreshed_event_that_happened_at_its_as_of_and_pins_snapshot_and_subject':
            ('late:backdated:state.refreshed', 'late:state_read_by_as_of'),
        'a_responsible_person_up_the_spine_writes_a_snapshot_without_a_declaration': P('world_refresh_state'),
        'a_payload_type_that_is_not_the_subjects_is_refused': R('world_refresh_state'),
        'a_snapshot_without_a_source_event_is_refused': R('world_refresh_state'),
        'a_source_event_outside_the_scope_is_refused': R('world_refresh_state'),
        'a_snapshot_as_of_the_future_is_refused': R('world_refresh_state') + ('late:occurred_not_after_recorded',),
        'a_request_that_names_the_generator_is_refused': R('world_refresh_state'),
        'an_issue_component_without_its_core_question_is_refused_over_http': R('world_refresh_state'),
        'an_agent_snapshot_without_a_declaration_is_refused': R('world_refresh_state'),
        'a_person_who_is_not_responsible_up_the_spine_cannot_write_the_snapshot': R('world_refresh_state'),
        'an_agent_without_the_agent_role_in_the_subjects_domain_cannot_write_the_snapshot': R('world_refresh_state'),
        'a_snapshot_is_not_the_subject_of_a_snapshot': R('world_refresh_state'),
        'a_component_in_a_state_block_that_takes_none_is_refused':
            R('world_refresh_state') + ('components:unique_and_typed',),
        'a_second_snapshot_of_the_same_subject_at_the_same_moment_is_refused_whatever_the_offset':
            R('world_refresh_state') + ('duplicates:snapshot_unique',),
        'replaying_a_snapshot_with_the_same_key_returns_the_original_receipt': ('duplicates:same_key_same_request',),
        'events_are_read_in_occurrence_order_and_backdated_ones_are_marked_late':
            ('late:read_in_occurrence_order', 'late:marked_on_read', 'late:backdated:event.recorded',
             'late:backdated:state.refreshed'),
        'a_scope_member_from_another_domain_records_an_external_event_by_the_scope_rule': P('world_record_event'),
        'each_event_carries_its_class_recorder_producing_action_and_relations': (),
        'events_since_a_moment_start_at_that_occurrence': ('late:read_in_occurrence_order',),
        'the_state_at_a_moment_is_the_newest_snapshot_as_of_that_moment': ('late:state_read_by_as_of',),
        'the_records_group_gives_the_latest_snapshot_marked_unconfirmed':
            ('read:records:latest_state', 'late:state_read_by_as_of'),
        'a_correction_of_an_external_event_is_recorded_and_read_back_as_the_corrected_relation':
            P('world_record_event'),
        'a_correction_of_a_non_external_event_is_refused': R('world_record_event'),
        'a_correction_of_an_event_in_another_scope_is_refused': R('world_record_event'),
        'an_external_event_that_has_not_happened_yet_is_refused':
            R('world_record_event') + ('late:occurred_not_after_recorded',),
        'an_external_event_without_a_category_is_refused': R('world_record_event'),
        'an_agent_external_event_without_a_declaration_is_refused': R('world_record_event'),
        'an_external_event_without_a_subject_is_refused': R('world_record_event'),
        'a_component_of_a_snapshot_can_be_the_subject_of_an_event': P('world_record_event') + ('refs:form:component',),
        'the_event_log_refuses_an_external_event_that_happens_after_it_is_recorded':
            ('late:occurred_not_after_recorded',),
        'the_event_log_takes_a_backdated_external_event': ('late:backdated:event.recorded',),
        'the_event_log_refuses_backdating_any_other_kind': ('late:others_not_backdated',),
        'every_0_2_event_written_so_far_keeps_occurrence_no_later_than_recording':
            ('late:occurred_not_after_recorded', 'late:others_not_backdated'),
    },
    'assign_lifecycle': {
        'a_unit_dri_is_assigned_only_by_the_strategy_responsible_and_only_to_a_domain_dri_of_the_unit':
            R('world_assign'),
        'assigning_the_unit_dri_records_one_assign_event_without_a_new_revision': P('world_assign'),
        'a_mission_owner_is_assigned_only_by_the_period_goal_dri_and_only_to_an_owner_of_the_unit': R('world_assign'),
        'assigning_the_mission_owner_writes_a_new_revision_and_identity_names_the_owner_by_attribute':
            P('world_assign') + ('read:identity:responsible',),
        **_walk_checks('Task'),
        'task_only_the_mission_owner_accepts_a_task':
            R('world_accept') + L('Task', 'delivered>closed:world_accept', 'refused'),
        **_walk_checks('Activity'),
        'activity_an_agent_start_or_delivery_carries_a_declaration': R('world_start'),
        'activity_an_agent_delivery_is_not_accepted_by_the_agent_or_the_mission_owner':
            R('world_accept') + L('Activity', 'delivered>closed:world_accept', 'refused'),
        'activity_the_agent_delivery_was_accepted_by_the_task_responsible': (),
        'an_agent_assigned_to_an_activity_revises_it_with_a_declaration_whose_scene_is_a_period_goal':
            P('world_revise_object'),
    },
    'gates': {
        'a_period_goal_is_born_a_draft_without_formal_content': ('read:records:lifecycle', 'read:business:formal'),
        'period_goal_a_gate_is_recorded_by_a_person_holding_the_gate_role_and_an_agent_with_the_role_is_refused':
            R('world_commit_period_goal', 'world_confirm_period_goal')
            + L(PG, 'draft>committed:world_commit_period_goal', 'refused'),
        'period_goal_nothing_committed_cannot_be_confirmed': R('world_confirm_period_goal'),
        'period_goal_a_gate_takes_no_declaration_a_confirmation_needs_its_outcome_and_a_candidate_only_formal_content':
            R('world_commit_period_goal', 'world_confirm_period_goal'),
        'period_goal_a_commitment_waits_for_its_long_term_goal_to_be_confirmed':
            R('world_commit_period_goal') + L(PG, 'draft>committed:world_commit_period_goal', 'refused'),
        'period_goal_draft_commit_to_committed_with_a_gate_event_pinned_to_the_latest_revision':
            P('world_commit_period_goal') + L(PG, 'draft>committed:world_commit_period_goal', 'enter'),
        'period_goal_withdrawing_the_commitment_returns_to_draft_and_keeps_the_original':
            ('withdrawal:reverts_and_keeps_original',),
        'period_goal_committed_confirm_returned_to_draft_with_the_reason_in_the_event':
            P('world_confirm_period_goal') + L(PG, PG_RETURN, 'enter'),
        'period_goal_a_commitment_keeps_its_candidate_in_the_event_with_ids_for_new_components':
            P('world_commit_period_goal'),
        'period_goal_committed_formal_content_is_not_revised_directly': R('world_revise_object'),
        'period_goal_committed_activity_attributes_are_revised_directly': P('world_revise_object'),
        'period_goal_confirm_accepted_writes_back_the_candidate_and_keeps_the_current_activity_attributes':
            P('world_confirm_period_goal') + L(PG, PG_CONFIRM, 'enter'),
        'period_goal_the_first_formal_confirmation_points_formal_content_at_the_written_back_revision':
            ('read:business:formal',),
        'period_goal_withdrawing_the_formal_confirmation_takes_formal_content_back':
            ('withdrawal:takes_back_formal_content', 'withdrawal:reverts_and_keeps_original'),
        'period_goal_confirming_again_writes_the_committed_candidate_back_as_it_was': P('world_confirm_period_goal'),
        'period_goal_with_formal_content_formal_blocks_change_only_through_a_re_run':
            R('world_revise_object') + ('round:PeriodGoal:formal_content_only_through_a_round',),
        'period_goal_a_re_run_commitment_carries_a_candidate': ('round:PeriodGoal:without_candidate',),
        'period_goal_a_re_run_opens_without_changing_the_stage_and_business_shows_the_round':
            ('round:PeriodGoal:opens_without_changing_the_stage', 'read:business:round'),
        'period_goal_a_round_cannot_open_while_one_is_unfinished': ('round:PeriodGoal:one_round_at_a_time',),
        'period_goal_the_commitment_of_a_round_cannot_be_withdrawn': ('withdrawal:never:round_events',),
        'period_goal_a_returned_round_is_void_and_nothing_is_written_back': ('round:PeriodGoal:returned_round_is_void',),
        'period_goal_the_confirmer_accepting_the_round_writes_it_back_and_the_stage_stays':
            ('round:PeriodGoal:write_back',),
        'period_goal_once_a_round_wrote_back_the_confirmation_that_made_it_formal_cannot_be_withdrawn':
            ('withdrawal:never:formal_confirm_after_write_back',),
        'period_goal_no_round_open_no_confirmation': R('world_confirm_period_goal') + L(PG, PG_CONFIRM, 'idempotent'),
        'long_term_goal_only_the_ceo_confirms_and_a_candidate_travels_only_with_an_acceptance_of_formal_content':
            R('world_confirm_long_term_goal') + L(LTG, LTG_CONFIRM, 'refused'),
        'long_term_goal_draft_confirm_returned_stays_a_draft_and_keeps_its_producer':
            P('world_confirm_long_term_goal') + L(LTG, 'draft>draft:world_confirm_long_term_goal:returned', 'enter'),
        'long_term_goal_draft_confirm_accepted_with_a_candidate_writes_it_back_and_makes_it_formal':
            P('world_confirm_long_term_goal') + L(LTG, LTG_CONFIRM, 'enter'),
        'long_term_goal_withdrawing_the_formal_confirmation_takes_formal_content_back':
            ('withdrawal:takes_back_formal_content',),
        'long_term_goal_a_confirmation_without_candidate_makes_the_then_latest_revision_formal':
            P('world_confirm_long_term_goal') + L(LTG, LTG_CONFIRM, 'enter'),
        'long_term_goal_confirmed_formal_attributes_are_not_revised_directly_and_activity_attributes_are':
            ('round:LongTermGoal:formal_content_only_through_a_round',),
        'long_term_goal_a_confirmed_goal_is_confirmed_again_only_with_a_candidate':
            ('round:LongTermGoal:without_candidate',) + L(LTG, LTG_CONFIRM, 'idempotent'),
        'long_term_goal_a_confirmation_with_a_candidate_rewrites_it_in_one_step_and_the_stage_stays':
            ('round:LongTermGoal:write_back',),
        'long_term_goal_once_rewritten_the_confirmation_that_made_it_formal_cannot_be_withdrawn':
            ('withdrawal:never:formal_confirm_after_write_back',),
        'mission_a_commitment_is_refused_while_its_period_goal_is_not_confirmed':
            R('world_commit_mission') + L(M, 'draft>committed:world_commit_mission', 'refused'),
        'mission_an_accepting_confirmation_is_refused_once_the_period_goal_is_no_longer_confirmed_and_a_return_is_not':
            P('world_confirm_mission') + R('world_confirm_mission') + L(M, M_CONFIRM, 'refused')
            + L(M, M_RETURN, 'enter'),
        'mission_only_its_owner_in_person_commits_it':
            R('world_commit_mission') + L(M, 'draft>committed:world_commit_mission', 'refused'),
        'mission_only_the_dri_confirms_and_an_agent_holding_the_dri_role_cannot': R('world_confirm_mission'),
        'mission_draft_commit_to_committed_by_the_owner':
            P('world_commit_mission') + L(M, 'draft>committed:world_commit_mission', 'enter'),
        'mission_withdrawing_the_commitment_returns_to_draft': ('withdrawal:reverts_and_keeps_original',),
        'mission_committed_confirm_returned_to_draft': P('world_confirm_mission') + L(M, M_RETURN, 'enter'),
        'mission_committed_confirm_accepted_to_established_writing_back_the_play_and_keeping_the_execution_plan':
            P('world_confirm_mission') + L(M, M_CONFIRM, 'enter'),
        'mission_established_formal_blocks_are_not_revised_directly':
            R('world_revise_object') + ('round:Mission:formal_content_only_through_a_round',),
        'mission_established_the_co_agent_revises_the_execution_plan_without_human_acceptance':
            P('world_revise_object'),
        'mission_a_responsible_below_it_revises_the_execution_plan_but_not_its_formal_blocks':
            P('world_revise_object') + R('world_revise_object'),
        'mission_the_owner_opens_a_round_with_a_candidate_and_the_stage_stays':
            ('round:Mission:opens_without_changing_the_stage', 'round:Mission:without_candidate', 'read:business:round'),
        'mission_a_round_cannot_open_while_one_is_unfinished': ('round:Mission:one_round_at_a_time',),
        'mission_the_dri_accepting_the_round_writes_it_back_the_stage_stays_and_the_execution_plan_is_not_overwritten':
            ('round:Mission:write_back',),
        'mission_once_a_round_wrote_back_the_confirmation_that_made_it_established_cannot_be_withdrawn':
            ('withdrawal:never:formal_confirm_after_write_back',),
        'a_gate_writes_exactly_one_event_and_replaying_it_returns_the_original_receipt':
            ('duplicates:same_key_same_request',),
    },
    'context_packs': {name: () for name in (
        'the_0_2_context_walks_the_spine_from_the_activity_up_to_the_company_reading_each_latest_version',
        'acceptance_criteria_come_back_as_component_references_pinned_to_the_version_read',
        'events_come_back_as_event_references_once_at_the_nearest_level',
        'cross_chain_relations_are_listed_as_references_and_not_followed',
        'every_reference_in_the_pack_is_pinned_and_reads_back',
        'the_latest_snapshot_comes_back_as_its_shell_view_marked_unconfirmed',
        'lifecycle_formal_content_and_responsibility_follow_the_0_2_read_projection',
        'coverage_answers_the_six_questions_from_what_the_pack_holds',
        'the_basis_takes_constraint_and_acceptance_components_along_the_spine_by_their_context_role',
        'the_why_takes_the_contribution_of_the_current_object_by_its_context_role',
        'each_call_writes_exactly_one_context_pack_row_holding_what_was_returned_with_the_0_2_defaults',
        'two_calls_on_the_same_world_state_return_the_same_pack_plan_and_coverage',
        'the_per_object_event_cap_keeps_the_newest_ten_events_and_records_the_rest',
        'a_tight_budget_trims_blocks_off_the_why_chain_then_old_events_then_the_rest_and_records_why',
        'the_current_object_keeps_its_blocks_and_latest_snapshot_under_any_budget',
        'a_tight_budget_keeps_the_why_chain_until_everything_else_is_trimmed',
        'a_cap_of_one_keeps_each_levels_newest_event_plus_the_current_objects_assignment_and_lifecycle_events',
        'recent_events_start_at_the_requested_window',
        'starting_from_a_snapshot_takes_its_subject_as_the_current_object_and_that_snapshot_as_its_state',
        'an_identity_outside_the_scope_an_unknown_object_and_bad_requests_are_refused_and_write_nothing',
        'a_0_1_object_still_gets_the_0_1_context',
        'a_0_2_context_pack_row_cannot_be_changed_even_by_the_owner')},
    'mission_lifecycle': {
        'mission_walk_draft_commit_to_committed': L(M, 'draft>committed:world_commit_mission', 'enter'),
        'mission_walk_committed_confirm_returned_to_draft': L(M, M_RETURN, 'enter'),
        'mission_walk_committed_confirm_accepted_to_established': L(M, M_CONFIRM, 'enter'),
        'mission_an_established_mission_is_not_delivered_accepted_or_reopened_before_it_starts':
            R('world_deliver', 'world_accept', 'world_reopen'),
        'mission_only_the_owner_or_an_agent_holding_agent_in_its_domain_starts_it':
            R('world_start') + L(M, 'established>in_progress:world_start', 'refused'),
        'mission_the_owners_agent_starts_it_only_with_a_declaration': R('world_start'),
        'mission_established_start_to_in_progress_by_the_owners_agent':
            P('world_start') + L(M, 'established>in_progress:world_start', 'enter'),
        'mission_a_withdrawn_start_is_recorded_by_the_owner_or_its_agent_not_the_dri':
            ('withdrawal:same_action_same_role',),
        'mission_withdrawing_the_start_returns_to_established_and_keeps_the_original':
            ('withdrawal:reverts_and_keeps_original',),
        'mission_reading_events_shows_the_start_withdrawn_by_the_withdrawal': ('withdrawal:read_as_withdrawn',),
        'mission_a_withdrawal_cannot_be_withdrawn': ('withdrawal:never:withdrawal',),
        'mission_established_start_to_in_progress_by_the_owner':
            P('world_start') + L(M, 'established>in_progress:world_start', 'enter'),
        'mission_a_repeated_start_is_refused':
            ('duplicates:state_mismatch',) + L(M, 'established>in_progress:world_start', 'idempotent'),
        'mission_in_progress_a_round_opened_by_the_owner_is_written_back_by_the_dri_and_the_stage_stays':
            ('round:Mission:write_back',),
        'mission_all_its_tasks_closed_leaves_the_mission_in_progress': (),
        'mission_only_the_owner_delivers_it_and_the_owners_agent_does_not':
            R('world_deliver') + L(M, 'in_progress>delivered:world_deliver', 'refused'),
        'mission_in_progress_deliver_to_delivered':
            P('world_deliver') + L(M, 'in_progress>delivered:world_deliver', 'enter'),
        'mission_a_repeated_delivery_is_refused':
            ('duplicates:state_mismatch',) + L(M, 'in_progress>delivered:world_deliver', 'idempotent'),
        'mission_delivered_a_round_opened_by_the_owner_is_written_back_by_the_dri_and_the_stage_stays':
            ('round:Mission:write_back',),
        'mission_only_the_dri_accepts_or_rejects_the_delivery':
            R('world_accept', 'world_reject') + L(M, 'delivered>closed:world_accept', 'refused')
            + L(M, 'delivered>adjusting:world_reject', 'refused'),
        'mission_delivered_reject_to_adjusting':
            P('world_reject') + L(M, 'delivered>adjusting:world_reject', 'enter'),
        'mission_an_adjusting_mission_is_delivered_again_before_it_is_accepted': R('world_accept'),
        'mission_adjusting_a_round_opened_by_the_owner_is_written_back_by_the_dri_and_the_stage_stays':
            ('round:Mission:write_back',),
        'mission_adjusting_deliver_to_delivered':
            P('world_deliver') + L(M, 'adjusting>delivered:world_deliver', 'enter'),
        'mission_delivered_accept_to_closed': P('world_accept') + L(M, 'delivered>closed:world_accept', 'enter'),
        'mission_withdrawing_the_acceptance_returns_to_delivered':
            ('withdrawal:reverts_and_keeps_original', 'withdrawal:same_action_same_role'),
        'mission_an_earlier_event_cannot_be_withdrawn': ('withdrawal:only_the_producer_of_the_current_state',),
        'mission_closing_the_mission_leaves_its_tasks_as_they_were': (),
        'mission_a_closed_mission_is_not_delivered_cancelled_re_run_marked_or_reassigned':
            R('world_deliver', 'world_cancel', 'world_commit_mission', 'world_mark_core_battle', 'world_assign'),
        'mission_a_closed_mission_is_not_revised_directly': R('world_revise_object'),
        'mission_closed_reopen_to_in_progress': P('world_reopen') + L(M, 'closed>in_progress:world_reopen', 'enter'),
        'mission_only_the_dri_cancels_it': R('world_cancel') + L(M, 'in_progress>cancelled:world_cancel', 'refused'),
        'mission_withdrawing_the_cancellation_restores_in_progress': ('withdrawal:reverts_and_keeps_original',),
        'mission_cancelling_the_mission_leaves_its_tasks_as_they_were':
            P('world_cancel') + L(M, 'in_progress>cancelled:world_cancel', 'enter'),
        'mission_a_cancelled_mission_is_not_cancelled_reopened_started_or_marked':
            R('world_cancel', 'world_reopen', 'world_start', 'world_mark_core_battle')
            + L(M, 'in_progress>cancelled:world_cancel', 'idempotent'),
        'mission_a_cancelled_mission_is_not_revised_directly': R('world_revise_object'),
        **{f'mission_{state}_mark_core_battle_keeps_the_stage_its_producer_and_the_owner':
           P('world_mark_core_battle') + L(M, f'{state}>{state}:world_mark_core_battle', 'enter')
           for state in _mission_open_states()},
        'a_mark_is_one_record_event_by_the_ceo_pinned_to_the_revision_that_sets_core_battle': P('world_mark_core_battle'),
        'a_mission_is_marked_a_core_battle_only_once':
            R('world_mark_core_battle') + ('duplicates:state_mismatch',)
            + L(M, 'draft>draft:world_mark_core_battle', 'idempotent'),
        'a_mark_is_a_record_event_and_is_not_withdrawn': ('withdrawal:record_events_are_not_withdrawn',),
        **{f'mission_{state}_cancel_to_cancelled': P('world_cancel') + L(M, f'{state}>cancelled:world_cancel', 'enter')
           for state in _mission_open_states()},
        'a_mark_writes_exactly_one_event_and_replaying_it_returns_the_original_receipt':
            ('duplicates:same_key_same_request',),
        'marking_an_established_mission_moves_the_effective_revision_with_the_new_revision': ('read:business:formal',),
        'mission_a_draft_mission_does_not_start': R('world_start'),
        'only_the_ceo_in_person_marks_a_core_battle_and_an_agent_holding_the_ceo_role_cannot':
            R('world_mark_core_battle') + L(M, 'established>established:world_mark_core_battle', 'refused'),
        'a_mark_leaves_the_decisions_to_the_owner_and_the_dri_and_survives_a_write_back': ('round:Mission:write_back',),
        'mission_a_round_open_when_the_mission_is_accepted_is_void_and_nothing_is_written_back':
            ('round:Mission:voided_in:closed',) + P('world_accept') + R('world_confirm_mission'),
        'mission_withdrawing_the_acceptance_restores_the_round_which_is_then_written_back':
            ('round:Mission:restored_by_withdrawal', 'round:Mission:write_back'),
        'mission_a_round_open_when_the_mission_is_cancelled_is_void_and_nothing_is_written_back':
            ('round:Mission:voided_in:cancelled',) + P('world_cancel'),
        'mission_withdrawing_the_cancellation_restores_the_round_and_cancelling_again_voids_it':
            ('round:Mission:restored_by_withdrawal', 'round:Mission:voided_in:cancelled') + R('world_confirm_mission'),
    },
    'delegation': {
        'a_delegation_is_granted_by_a_person_in_person_to_an_agent_of_the_scope_for_its_families_domains_and_a_'
        'future_expiry_and_is_not_itself_recorded_on_behalf':
            R('world_grant_delegation') + ('delegation:grant', 'delegation:refused:pending_family:create'),
        'granting_records_one_record_event_under_the_company_with_the_delegation_in_its_detail':
            P('world_grant_delegation') + ('delegation:grant',),
        'identity_gives_the_delegations_in_force_whose_domains_cover_the_objects_domain':
            ('read:identity:delegations',),
        'the_service_principal_confirms_a_period_goal_on_behalf_of_the_ceo_and_it_counts_as_the_ceos_confirmation':
            P('world_confirm_period_goal') + ('delegation:passes:gate',),
        'the_delegated_event_records_the_service_principal_the_person_and_the_external_confirmation':
            ('delegation:recorded_fields',),
        'the_delegated_receipt_records_the_service_principal_the_person_the_external_confirmation_and_the_delegation':
            ('delegation:recorded_fields',),
        'reading_events_gives_the_recorder_the_person_recorded_on_behalf_of_and_the_external_confirmation':
            ('delegation:recorded_fields',),
        'the_service_principal_marks_a_mission_as_a_core_battle_on_behalf_of_the_ceo':
            P('world_mark_core_battle') + ('delegation:passes:gate',),
        'the_service_principal_assigns_a_task_on_behalf_of_the_mission_owner_judged_by_his_responsibility':
            P('world_assign') + ('delegation:passes:assign',)
            + L('Task', 'unassigned>assigned:world_assign', 'enter'),
        'replaying_a_command_recorded_on_behalf_returns_the_original_receipt':
            ('delegation:replay', 'duplicates:same_key_same_request')
            + L('Task', 'unassigned>assigned:world_assign', 'idempotent'),
        'revoking_records_one_record_event_under_the_company_that_references_the_grant':
            P('world_revoke_delegation') + ('delegation:revoke',),
        'a_revoked_delegation_takes_effect_at_once_and_nothing_more_is_recorded_on_its_behalf':
            ('delegation:refused:revoked',),
        'a_command_recorded_on_behalf_cannot_be_replayed_into_success_once_the_delegation_is_revoked':
            ('delegation:refused:revoked',),
        'only_the_grantor_revokes_a_grant_of_this_scope_once': R('world_revoke_delegation') + ('delegation:revoke',),
        'the_service_principal_starts_and_delivers_a_task_on_behalf_of_its_responsible_without_a_declaration':
            P('world_start', 'world_deliver') + ('delegation:passes:lifecycle',),
        'a_write_on_behalf_of_a_person_who_may_not_record_it_himself_is_forbidden':
            R('world_accept') + ('delegation:refused:person_may_not_record',),
        'the_person_withdraws_in_person_the_event_recorded_on_his_behalf':
            ('delegation:withdrawn_in_person', 'withdrawal:reverts_and_keeps_original'),
        'an_expired_delegation_lets_nothing_be_recorded_on_behalf_and_is_no_longer_listed':
            ('delegation:refused:expired',),
        'the_lifecycle_goes_on_from_a_delegated_delivery_as_if_the_person_had_recorded_it':
            P('world_accept') + ('delegation:passes:lifecycle',),
        'a_family_outside_the_delegation_is_forbidden': ('delegation:refused:family_out_of_scope',),
        'a_domain_outside_the_delegation_is_forbidden': ('delegation:refused:domain_out_of_scope',),
        'a_delegation_whose_grantor_is_no_longer_a_valid_person_of_the_scope_is_not_in_force':
            ('delegation:refused:grantor_no_longer_valid',),
        'only_the_delegated_agent_records_on_behalf_and_only_of_a_person_who_delegated_to_it':
            ('delegation:refused:recorder_not_the_delegate',),
        'a_write_on_behalf_carries_no_declaration_a_confirmation_not_later_than_the_recording_and_only_on_a_'
        'delegable_action': ('delegation:refused:request_shape',),
        'the_company_reads_back_the_delegation_events_as_record_events': (),
        # #71 议题族（补 49）
        'a_delegation_without_the_issue_family_does_not_let_the_service_principal_own_an_issue':
            R('world_own_issue') + ('delegation:refused:family_out_of_scope',),
        'the_service_principal_still_does_not_own_an_issue_in_its_own_name': R('world_own_issue'),
        'the_service_principal_owns_an_issue_on_behalf_of_its_route_target':
            P('world_own_issue') + ('delegation:passes:issue', 'delegation:recorded_fields')
            + L(I, 'routed>owned:world_own_issue', 'enter'),
        'the_service_principal_returns_the_owned_issue_to_forming_on_behalf_of_its_owner':
            P('world_return_issue') + ('delegation:passes:issue',) + L(I, 'owned>forming:world_return_issue', 'enter'),
        'the_route_target_still_owns_the_issue_in_person': P('world_own_issue'),
        'the_service_principal_disposes_the_issue_on_behalf_of_its_owner':
            P('world_dispose_issue') + ('delegation:passes:issue',)
            + L(I, 'owned>disposed:world_dispose_issue:current_layer_action', 'enter'),
        'replaying_an_issue_action_recorded_on_behalf_returns_the_original_receipt': ('delegation:replay',),
        'reading_events_gives_the_issue_events_recorded_on_behalf_with_the_person_and_the_external_confirmation':
            ('delegation:recorded_fields',),
    },
    'mcp_end_to_end': {
        'the_0_2_mcp_server_lists_the_agent_face_five_reads_and_eight_writes': (),
        'the_four_reads_reach_the_real_api_as_the_agent_and_read_0_2_shapes': (),
        'the_five_writes_commit_through_prepare_and_commit_as_the_agent_under_0_2':
            P('world_start', 'world_record_event', 'world_refresh_state', 'world_revise_object', 'world_deliver'),
        'the_agent_starts_and_delivers_its_activity_and_starts_the_mission_as_the_owners_agent':
            L('Activity', 'assigned>in_progress:world_start', 'enter')
            + L('Activity', 'in_progress>delivered:world_deliver', 'enter')
            + L(M, 'established>in_progress:world_start', 'enter'),
        'a_write_missing_its_declaration_is_refused_by_http_and_returned_verbatim': R('world_record_event'),
        'tools_outside_the_0_2_face_and_on_behalf_writes_are_refused_before_any_http_call_and_change_nothing':
            ('delegation:refused:request_shape',),
        'every_mcp_call_is_in_the_run_log_with_component_and_event_references_and_no_credential': (),
    },
    'list_objects': {
        'the_external_reference_lookup_has_its_index': ('external_refs:lookup',),
        'period_lists_the_period_goal_its_mission_task_and_activity_and_snapshots_of_that_period': ('list:filters',),
        'a_mission_is_listed_under_the_period_of_its_period_goal': ('list:filters',),
        'unit_id_lists_the_objects_in_the_units_domain_including_the_unit': ('list:filters',),
        'domain_id_lists_the_objects_in_that_domain': ('list:filters',),
        'type_lists_the_objects_of_that_type_including_state_snapshots': ('list:filters',),
        'the_filters_combine': ('list:filters',),
        'external_system_and_id_look_up_exactly_one_object': ('external_refs:lookup',),
        'external_system_alone_lists_the_objects_with_any_reference_of_that_system': ('external_refs:lookup',),
        'an_agent_reads_the_list_like_any_identity_of_the_scope': ('list:filters',),
        'each_header_carries_id_type_category_title_latest_version_lifecycle_domain_and_external_refs_as_read':
            ('list:header',),
        'pages_follow_next_cursor_in_order_without_gaps_or_repeats': ('list:pagination',),
        'a_cursor_only_continues_the_same_read_by_the_same_identity': ('list:pagination', 'list:refusals'),
        'malformed_list_requests_are_invalid': ('list:refusals',),
        'a_unit_or_domain_outside_the_scope_is_not_found': ('list:refusals',),
        'creating_a_second_object_with_the_same_external_reference_is_refused_and_changes_nothing':
            R('world_create_object') + ('external_refs:unique',),
        'revising_another_object_to_take_the_same_external_reference_is_refused_and_changes_nothing':
            R('world_revise_object') + ('external_refs:unique',),
        'an_object_found_by_its_external_reference_stays_one_across_its_revisions': ('external_refs:unique',),
        'a_reference_freed_on_the_latest_revision_can_be_taken_by_another_object': ('external_refs:unique',),
        **{name: ('read:legacy_0_1_view',) for name in (
            'a_0_1_object_keeps_its_0_1_shape_by_default',
            'with_the_view_parameter_a_0_1_object_is_read_in_the_three_groups_under_the_0_1_contract',
            '0_1_references_read_as_0_2_object_and_block_forms',
            'the_lifecycle_follows_the_0_1_state_machine_from_its_0_1_event',
            'a_0_1_snapshot_is_given_as_the_read_only_legacy_0_1_payload',
            'the_snapshot_and_the_state_read_the_same_legacy_view',
            'a_0_2_object_reads_the_same_with_or_without_the_view_parameter',
            'an_unknown_view_is_invalid')},
        'the_list_also_heads_0_1_objects_under_the_0_1_contract': ('list:0_1_scope',),
    },
    'issues': {
        'the_co_agent_raises_an_issue_component_from_not_raised_into_pending_routing':
            P('world_raise_issue') + L(I, 'not_raised>pending_routing:world_raise_issue', 'enter'),
        'an_issue_event_is_one_0_2_record_event_on_the_component_and_its_primary_affected_object':
            ('issue:event_on_component_and_object',),
        'the_same_key_replays_the_raise_into_the_original_receipt_and_no_second_event':
            ('duplicates:same_key_same_request',)
            + L(I, 'not_raised>pending_routing:world_raise_issue', 'idempotent'),
        'an_issue_in_process_is_not_raised_again_while_pending_routing':
            R('world_raise_issue') + ('duplicates:state_mismatch',)
            + L(I, 'not_raised>pending_routing:world_raise_issue', 'idempotent'),
        'the_co_agent_routes_a_pending_issue_to_a_person':
            P('world_route_issue') + L(I, 'pending_routing>routed:world_route_issue', 'enter'),
        'an_issue_in_process_is_not_raised_again_while_routed': R('world_raise_issue'),
        'the_same_id_in_a_later_snapshot_is_the_same_issue_and_is_not_raised_again':
            ('issue:same_issue_across_snapshots',),
        'the_open_issue_reads_from_the_latest_snapshot_that_carries_it':
            ('issue:same_issue_across_snapshots', 'read:records:open_issues'),
        'a_responsible_person_up_the_spine_reroutes_a_routed_issue_and_the_stage_keeps_its_producer':
            P('world_route_issue') + L(I, 'routed>routed:world_route_issue', 'enter')
            + L(I, 'pending_routing>routed:world_route_issue', 'idempotent'),
        'the_earlier_route_target_does_not_own_a_rerouted_issue':
            R('world_own_issue') + L(I, 'routed>owned:world_own_issue', 'refused'),
        'an_agent_does_not_own_an_issue_even_holding_a_role':
            R('world_own_issue') + L(I, 'routed>owned:world_own_issue', 'refused'),
        'the_route_target_owns_the_issue_in_person':
            P('world_own_issue') + L(I, 'routed>owned:world_own_issue', 'enter')
            + ('issue:same_issue_across_snapshots',),
        'an_issue_in_process_is_not_raised_again_while_owned': R('world_raise_issue'),
        'an_owned_issue_is_not_rerouted_it_is_disposed_or_returned': R('world_route_issue'),
        'only_the_owner_in_person_disposes_an_issue':
            R('world_dispose_issue')
            + L(I, 'owned>disposed:world_dispose_issue:current_layer_action', 'refused'),
        'a_person_neither_router_nor_owner_does_not_return_an_owned_issue':
            R('world_return_issue') + L(I, 'owned>forming:world_return_issue', 'refused'),
        'the_owner_returns_an_owned_issue_to_forming':
            P('world_return_issue') + L(I, 'owned>forming:world_return_issue', 'enter'),
        'a_forming_issue_is_raised_again_before_it_is_routed': R('world_route_issue'),
        'a_responsible_person_up_the_spine_raises_a_forming_issue_again_without_a_declaration':
            P('world_raise_issue') + L(I, 'forming>pending_routing:world_raise_issue', 'enter'),
        'a_person_neither_router_nor_owner_does_not_return_a_routed_issue':
            R('world_return_issue') + L(I, 'routed>forming:world_return_issue', 'refused'),
        'the_owner_of_an_earlier_routing_neither_returns_nor_owns_the_issue_routed_again':
            R('world_return_issue', 'world_own_issue') + L(I, 'routed>forming:world_return_issue', 'refused')
            + L(I, 'routed>owned:world_own_issue', 'refused'),
        'an_agent_returns_an_issue_only_with_its_declaration': R('world_return_issue') + ('issue:request_refusals',),
        'the_co_agent_as_a_router_returns_a_routed_issue_to_forming':
            P('world_return_issue') + L(I, 'routed>forming:world_return_issue', 'enter'),
        'a_disposition_without_its_reason_or_outside_the_six_is_refused':
            R('world_dispose_issue') + ('issue:request_refusals',),
        **{f"the_owner_disposes_with_{item['id']}_into_{item['to']}":
           P('world_dispose_issue') + L(I, f"owned>{item['to']}:world_dispose_issue:{item['id']}", 'enter')
           + ('issue:raise_route_own_dispose',)
           for item in REGISTRY['issue']['dispositions']},
        'dispositions_leave_the_business_objects_their_lifecycles_and_rows_as_they_were':
            ('issue:business_objects_unchanged',),
        'the_events_of_the_primary_affected_object_list_the_issue_events_with_their_action_and_disposition':
            ('issue:events_listed',),
        'the_events_of_the_snapshot_list_the_issue_events_on_its_components': ('issue:events_listed',),
        'a_disposed_issue_is_not_raised_or_routed_again':
            R('world_raise_issue', 'world_route_issue') + ('issue:recurrence_under_new_id',),
        'a_recurrence_is_raised_under_a_new_id_that_references_the_original':
            P('world_raise_issue') + ('issue:recurrence_under_new_id',),
        'an_escalated_issue_is_routed_again_and_the_ceo_owns_it':
            P('world_route_issue', 'world_own_issue') + ('issue:escalated_and_rerouted',),
        'owning_returning_or_disposing_a_pending_issue_is_invalid_state':
            R('world_own_issue', 'world_return_issue', 'world_dispose_issue'),
        'an_issue_is_routed_only_to_an_active_person_of_the_scope':
            R('world_route_issue') + ('issue:request_refusals',),
        'an_issue_is_routed_to_a_person_of_another_unit_who_owns_it_in_person':
            P('world_route_issue', 'world_own_issue') + L(I, 'routed>owned:world_own_issue', 'enter')
            + ('issue:owner_across_units',),
        'a_person_outside_the_scope_or_other_than_the_owner_does_not_dispose_an_issue_owned_across_units':
            R('world_dispose_issue') + L(I, 'owned>disposed:world_dispose_issue:current_layer_action', 'refused')
            + ('issue:owner_across_units',),
        'the_owner_from_another_unit_returns_the_issue_to_forming':
            P('world_return_issue') + L(I, 'owned>forming:world_return_issue', 'enter') + ('issue:owner_across_units',),
        'a_person_outside_the_scope_or_an_agent_does_not_own_an_issue_routed_across_units':
            R('world_own_issue') + L(I, 'routed>owned:world_own_issue', 'refused') + ('issue:owner_across_units',),
        'the_owner_from_another_unit_disposes_the_issue':
            P('world_dispose_issue') + L(I, 'owned>disposed:world_dispose_issue:current_layer_action', 'enter')
            + ('issue:owner_across_units', 'issue:raise_route_own_dispose'),
        'disposing_a_routed_issue_that_is_not_owned_is_invalid_state': R('world_dispose_issue'),
        'a_person_not_responsible_up_the_spine_of_the_primary_affected_object_does_not_raise':
            R('world_raise_issue') + L(I, 'not_raised>pending_routing:world_raise_issue', 'refused'),
        'a_principal_without_a_role_in_the_domain_of_the_primary_affected_object_does_not_raise':
            R('world_raise_issue') + L(I, 'not_raised>pending_routing:world_raise_issue', 'refused'),
        'an_agent_raises_only_with_its_declaration': R('world_raise_issue') + ('issue:request_refusals',),
        'an_issue_ref_that_is_not_an_issue_component_of_a_snapshot_is_refused':
            R('world_raise_issue') + ('issue:request_refusals',),
        'an_issue_ref_outside_the_scope_is_not_found': R('world_raise_issue') + ('issue:request_refusals',),
        'raising_an_issue_takes_neither_a_target_nor_on_behalf_of':
            R('world_raise_issue') + ('issue:request_refusals', 'delegation:refused:request_shape'),
        'a_correction_points_to_an_issue_event_and_leaves_the_issue_where_it_was':
            P('world_record_event') + ('issue:correction',),
        'records_open_issues_lists_the_raised_issues_of_the_object_that_are_not_disposed':
            ('read:records:open_issues',),
        'the_open_issues_of_an_object_without_issues_are_empty': ('read:records:open_issues',),
        'issue_actions_wrote_no_revision_and_left_the_business_lifecycles_unchanged':
            ('issue:business_objects_unchanged',),
    },
    'goal_closure': {
        'formation_a_commitment_on_a_draft_long_term_goal_is_refused':
            R('world_commit_period_goal') + L(PG, 'draft>committed:world_commit_period_goal', 'refused'),
        'a_long_term_goal_confirmation_records_returns_to_strategy_in_its_detail_and_nothing_else':
            P('world_confirm_long_term_goal') + L(LTG, LTG_CONFIRM, 'enter'),
        'returns_to_is_only_strategy_and_a_withdrawal_carries_none':
            R('world_confirm_long_term_goal', 'world_reconfirm_long_term_goal'),
        'formation_the_first_period_forms_without_a_review_while_the_scope_has_no_confirmed_company_review':
            P('world_confirm_period_goal') + L(PG, PG_CONFIRM, 'enter'),
        'reconfirming_a_long_term_goal_keeps_it_and_writes_one_gate_event_without_a_revision':
            P('world_reconfirm_long_term_goal')
            + L(LTG, 'confirmed>confirmed:world_reconfirm_long_term_goal', 'enter'),
        'replaying_a_reconfirmation_returns_the_original_receipt':
            ('duplicates:same_key_same_request',)
            + L(LTG, 'confirmed>confirmed:world_reconfirm_long_term_goal', 'idempotent'),
        'reconfirming_a_period_goal_keeps_it_and_writes_one_gate_event_without_a_revision':
            P('world_reconfirm_period_goal') + L(PG, 'confirmed>confirmed:world_reconfirm_period_goal', 'enter'),
        'only_the_ceo_in_person_reconfirms':
            R('world_reconfirm_period_goal') + L(PG, 'confirmed>confirmed:world_reconfirm_period_goal', 'refused'),
        'a_reconfirmation_carries_no_candidate_is_not_withdrawn_and_returns_to_is_only_on_a_long_term_goal':
            R('world_reconfirm_period_goal', 'world_reconfirm_long_term_goal') + ('withdrawal:never:reconfirm',),
        'a_period_goal_names_the_company_review_it_is_based_on_when_created': P('world_create_object'),
        'review_ref_points_to_a_company_review_snapshot_itself': R('world_create_object'),
        'formation_a_review_ref_to_an_unconfirmed_company_review_is_refused':
            R('world_commit_period_goal') + L(PG, 'draft>committed:world_commit_period_goal', 'refused'),
        'confirming_a_company_review_is_one_gate_event_pinning_the_snapshot_and_its_company':
            P('world_confirm_review'),
        'a_confirmed_company_review_only_gives_effect_changes_no_lifecycle_and_leaves_the_snapshot_as_it_was': (),
        'records_give_the_confirmed_review_with_the_confirmation_event_and_the_snapshot':
            ('read:records:confirmed_review',),
        'a_confirmed_company_review_reads_back_its_new_blocks_and_gives_the_standard_sentence_for_those_left_out':
            ('read:records:confirmed_review',),
        'a_company_review_is_confirmed_once_and_its_confirmation_is_not_withdrawn':
            R('world_confirm_review') + ('withdrawal:never:review_confirmed_on_company', 'duplicates:state_mismatch'),
        'only_the_ceo_confirms_a_company_review': R('world_confirm_review'),
        'only_a_company_review_or_a_snapshot_of_a_period_goal_is_confirmed_as_a_review': R('world_confirm_review'),
        'with_several_confirmed_company_reviews_the_latest_as_of_wins_not_the_latest_confirmation':
            P('world_confirm_review') + ('read:records:confirmed_review',),
        'formation_a_review_ref_to_a_confirmed_company_review_anchors_the_period_goal':
            P('world_commit_period_goal', 'world_confirm_period_goal'),
        'a_re_run_candidate_re_pointing_review_ref_to_an_unconfirmed_review_is_refused_by_the_formation_guard':
            R('world_commit_period_goal'),
        'a_first_period_goal_without_review_ref_opens_no_round_once_the_scope_has_a_confirmed_review_unless_it_names_one':
            R('world_commit_period_goal'),
        'a_first_period_goal_re_runs_with_a_candidate_re_pointing_review_ref_to_a_confirmed_review_and_it_is_written_back':
            P('world_commit_period_goal', 'world_confirm_period_goal') + ('round:PeriodGoal:write_back',),
        'formation_once_the_scope_has_a_confirmed_company_review_a_goal_without_review_ref_is_refused':
            R('world_commit_period_goal') + L(PG, 'draft>committed:world_commit_period_goal', 'refused'),
        'the_latest_as_of_confirmed_company_review_is_the_scopes_confirmed_review': ('read:records:confirmed_review',),
        'review_ref_is_re_pointed_while_the_goal_is_a_draft_and_the_confirmed_one_anchors_it':
            P('world_revise_object', 'world_commit_period_goal'),
        'review_ref_is_not_re_pointed_once_the_goal_is_committed': R('world_revise_object'),
        'a_period_goal_is_closed_by_review_only_once_it_is_confirmed': R('world_confirm_review'),
        'only_the_ceo_in_person_confirms_a_period_goal_review':
            R('world_confirm_review') + L(PG, 'confirmed>closed:world_confirm_review', 'refused'),
        'confirming_a_snapshot_of_a_period_goal_closes_it':
            P('world_confirm_review') + L(PG, 'confirmed>closed:world_confirm_review', 'enter'),
        'the_review_confirmation_is_among_the_events_of_the_period_goal': (),
        'a_closed_goal_takes_no_other_review_and_only_the_ceo_withdraws_the_one_on_the_same_snapshot':
            R('world_confirm_review') + ('withdrawal:same_action_same_role',)
            + L(PG, 'confirmed>closed:world_confirm_review', 'idempotent'),
        'withdrawing_a_period_goal_review_returns_the_goal_to_confirmed_and_the_review_is_no_longer_in_force':
            ('withdrawal:reverts_and_keeps_original', 'withdrawal:read_as_withdrawn'),
        'the_goal_is_closed_again_by_the_other_snapshot_which_is_now_its_confirmed_review':
            P('world_confirm_review') + ('read:records:confirmed_review',),
        'a_closed_period_goal_is_not_reconfirmed_cancelled_committed_confirmed_or_revised':
            R('world_reconfirm_period_goal', 'world_cancel', 'world_commit_period_goal', 'world_confirm_period_goal',
              'world_revise_object'),
        **{f'a_period_goal_is_cancelled_by_the_ceo_from_{state}':
           P('world_cancel') + R('world_cancel') + L(PG, f'{state}>cancelled:world_cancel', 'enter', 'refused')
           for state in ('draft', 'committed', 'confirmed')},
        # 再取消的是从已确认取消的那一个。
        'a_cancelled_period_goal_is_not_committed_confirmed_reconfirmed_cancelled_or_revised_again':
            R('world_commit_period_goal', 'world_confirm_period_goal', 'world_reconfirm_period_goal', 'world_cancel',
              'world_revise_object') + L(PG, 'confirmed>cancelled:world_cancel', 'idempotent'),
        'withdrawing_the_cancellation_returns_the_goal_to_where_it_was': ('withdrawal:reverts_and_keeps_original',),
        'a_draft_long_term_goal_is_terminated_by_the_ceo':
            P('world_cancel') + L(LTG, 'draft>terminated:world_cancel', 'enter'),
        'a_confirmed_long_term_goal_is_terminated_by_the_ceo_with_a_cancel_event':
            P('world_cancel') + R('world_cancel') + L(LTG, 'confirmed>terminated:world_cancel', 'enter', 'refused'),
        'a_terminated_long_term_goal_is_not_reconfirmed_confirmed_cancelled_or_revised':
            R('world_reconfirm_long_term_goal', 'world_confirm_long_term_goal', 'world_cancel', 'world_revise_object')
            + L(LTG, 'confirmed>terminated:world_cancel', 'idempotent'),
        'a_period_goal_hanging_on_a_terminated_long_term_goal_is_not_committed_or_confirmed_and_a_return_is_not_guarded':
            R('world_commit_period_goal', 'world_confirm_period_goal') + L(PG, PG_CONFIRM, 'refused')
            + L(PG, PG_RETURN, 'enter'),
        'the_long_term_goal_reconfirmation_is_recorded_on_behalf_of_the_ceo':
            P('world_reconfirm_long_term_goal') + ('delegation:passes:gate', 'delegation:recorded_fields'),
        'the_period_goal_reconfirmation_is_recorded_on_behalf_of_the_ceo':
            P('world_reconfirm_period_goal') + ('delegation:passes:gate',),
        'the_company_review_confirmation_is_recorded_on_behalf_of_the_ceo':
            P('world_confirm_review') + ('delegation:passes:gate', 'delegation:recorded_fields'),
        'the_period_goal_review_confirmation_is_recorded_on_behalf_of_the_ceo_and_closes_it':
            ('delegation:passes:gate',) + L(PG, 'confirmed>closed:world_confirm_review', 'enter'),
        'cancelling_a_period_goal_and_terminating_a_long_term_goal_are_recorded_on_behalf_of_the_ceo':
            P('world_cancel') + ('delegation:passes:lifecycle',),
        'recording_on_behalf_of_someone_without_a_delegation_is_refused': ('delegation:refused:recorder_not_the_delegate',),
        'replaying_a_review_confirmation_returns_the_original_receipt': ('duplicates:same_key_same_request',),
        'period_goal_a_round_open_when_it_is_closed_by_review_is_void_and_nothing_is_written_back':
            ('round:PeriodGoal:voided_in:closed',) + P('world_confirm_review') + R('world_confirm_period_goal'),
        'period_goal_withdrawing_the_closing_review_restores_the_round_which_is_then_written_back':
            ('round:PeriodGoal:restored_by_withdrawal', 'round:PeriodGoal:write_back'),
        'period_goal_a_round_open_when_it_is_cancelled_is_void_and_nothing_is_written_back':
            ('round:PeriodGoal:voided_in:cancelled',) + P('world_cancel'),
        'period_goal_withdrawing_the_cancellation_restores_the_round_and_cancelling_again_voids_it':
            ('round:PeriodGoal:restored_by_withdrawal', 'round:PeriodGoal:voided_in:cancelled')
            + R('world_confirm_period_goal'),
    },
    'strategy_gates': {
        'strategy_the_trunk_strategy_is_a_draft_without_formal_content_or_a_round':
            ('read:records:lifecycle', 'read:business:round'),
        'strategy_without_a_round_nothing_is_agreed_confirmed_or_reconfirmed':
            R('world_agree_strategy', 'world_confirm_strategy', 'world_reconfirm_strategy'),
        'strategy_a_round_designates_at_least_one_distinct_active_person_of_the_scope':
            R('world_assign_strategy_round'),
        'strategy_only_the_strategys_ceo_in_person_designates_a_round':
            R('world_assign_strategy_round') + L(S, S_DESIGNATE, 'refused'),
        'strategy_a_draft_round_carries_no_candidate': R('world_assign_strategy_round'),
        'strategy_designating_records_one_assign_event_with_the_designated_people_and_no_new_revision':
            P('world_assign_strategy_round') + L(S, S_DESIGNATE, 'enter'),
        'strategy_a_draft_round_keeps_the_stage_and_is_read_back_with_the_designated_people':
            ('read:business:round',) + L(S, S_DESIGNATE, 'enter'),
        'strategy_only_a_person_designated_for_the_round_records_an_agreement_whatever_roles_the_policy_lists':
            R('world_agree_strategy') + L(S, S_AGREE_OPEN, 'refused'),
        'strategy_a_designated_unit_dri_without_a_role_in_the_company_domain_records_an_agreement':
            P('world_agree_strategy') + L(S, S_AGREE_OPEN, 'enter'),
        'strategy_replaying_an_agreement_returns_the_original_receipt_and_writes_nothing_more':
            ('duplicates:same_key_same_request',) + L(S, S_AGREE_OPEN, 'idempotent'),
        'strategy_the_same_person_agrees_to_the_same_content_once':
            R('world_agree_strategy') + ('duplicates:state_mismatch',) + L(S, S_AGREE_OPEN, 'idempotent'),
        'strategy_an_agreement_on_an_earlier_revision_does_not_count_once_the_draft_is_revised':
            P('world_agree_strategy'),
        'strategy_the_agreement_completing_the_round_on_the_latest_revision_reaches_agreed':
            P('world_agree_strategy') + L(S, 'draft>agreed:world_agree_strategy', 'enter'),
        'strategy_once_agreed_there_is_no_new_designation_formal_revision_further_agreement_or_reconfirmation':
            R('world_assign_strategy_round', 'world_revise_object', 'world_agree_strategy', 'world_reconfirm_strategy')
            + L(S, 'draft>agreed:world_agree_strategy', 'idempotent'),
        'strategy_the_agreement_that_completed_the_round_is_withdrawn_by_its_recorder_back_to_draft':
            ('withdrawal:reverts_and_keeps_original', 'withdrawal:same_action_same_role',
             'withdrawal:never:agreement_not_producing_state'),
        'strategy_after_withdrawing_the_person_agrees_again_and_the_round_is_agreed': P('world_agree_strategy'),
        'strategy_returning_the_agreed_draft_ends_the_round':
            P('world_confirm_strategy') + R('world_confirm_strategy')
            + L(S, 'agreed>draft:world_confirm_strategy:returned', 'enter', 'refused'),
        'strategy_after_a_return_an_agreement_needs_a_new_designation': R('world_agree_strategy'),
        'strategy_designating_again_opens_a_new_round_and_the_earlier_agreements_are_void':
            P('world_assign_strategy_round') + R('world_agree_strategy') + L(S, S_DESIGNATE, 'idempotent'),
        'strategy_the_ceo_confirms_the_agreed_draft_and_the_strategy_becomes_effective_without_a_new_revision':
            P('world_confirm_strategy') + ('read:business:formal',)
            + L(S, 'agreed>effective:world_confirm_strategy:accepted', 'enter'),
        'strategy_effective_formal_content_changes_only_through_a_round_and_external_refs_are_revised_directly':
            ('round:Strategy:formal_content_only_through_a_round',),
        'strategy_effective_without_a_round_nothing_is_confirmed_or_agreed':
            R('world_confirm_strategy', 'world_agree_strategy')
            + L(S, 'agreed>effective:world_confirm_strategy:accepted', 'idempotent'),
        'strategy_reconfirming_records_one_gate_event_without_a_new_revision_and_keeps_the_state':
            P('world_reconfirm_strategy') + L(S, 'effective>effective:world_reconfirm_strategy', 'enter'),
        'strategy_a_reconfirmation_is_recorded_by_the_ceo_and_is_not_withdrawn':
            R('world_reconfirm_strategy') + ('withdrawal:never:reconfirm',)
            + L(S, 'effective>effective:world_reconfirm_strategy', 'refused'),
        'strategy_a_rounds_candidate_carries_only_formal_content': R('world_assign_strategy_round'),
        'strategy_designating_with_a_candidate_opens_a_round_on_the_effective_strategy_and_keeps_its_state':
            P('world_assign_strategy_round') + ('round:Strategy:opens_without_changing_the_stage',)
            + L(S, 'effective>effective:world_assign_strategy_round', 'enter'),
        'strategy_an_effective_rounds_agreement_pins_the_round_and_its_candidate': P('world_agree_strategy'),
        'strategy_designating_again_before_the_round_is_agreed_opens_a_new_round_with_its_own_candidate':
            P('world_assign_strategy_round'),
        'strategy_an_effective_round_is_agreed_in_its_own_stage_while_the_strategy_stays_effective':
            P('world_agree_strategy'),
        'strategy_an_agreed_round_is_not_designated_again':
            R('world_assign_strategy_round') + ('round:Strategy:one_round_at_a_time',),
        'strategy_confirming_the_agreed_round_writes_the_candidate_back_as_a_new_effective_revision':
            ('round:Strategy:write_back',),
        'strategy_a_new_version_takes_effect_without_reopening_downstream_which_stays_pinned_to_the_old_version':
            ('refs:stable_across_revisions',),
        'strategy_once_a_round_wrote_back_the_confirmation_that_made_it_effective_cannot_be_withdrawn':
            ('withdrawal:never:formal_confirm_after_write_back',),
        'strategy_a_round_concluding_no_change_ends_with_a_reconfirmation_and_no_new_revision':
            ('round:Strategy:without_candidate',),
        'strategy_returning_an_effective_round_voids_it_and_writes_nothing_back':
            ('round:Strategy:returned_round_is_void',),
        'strategy_an_agreement_on_behalf_needs_a_delegation_covering_the_strategys_domain_and_a_designated_person':
            ('delegation:refused:domain_out_of_scope', 'delegation:refused:person_may_not_record'),
        'strategy_the_service_principal_records_an_agreement_on_behalf_of_a_designated_person':
            P('world_agree_strategy') + ('delegation:passes:gate', 'delegation:recorded_fields'),
        'strategy_replaying_an_agreement_recorded_on_behalf_returns_the_original_receipt': ('delegation:replay',),
        'strategy_the_person_recorded_on_behalf_has_agreed_and_does_not_agree_again_in_person':
            R('world_agree_strategy'),
        'strategy_the_round_agreed_partly_on_behalf_ends_with_a_reconfirmation': P('world_reconfirm_strategy'),
    },
    # 取上下文补齐（#64）：上下文包不在票面十项里，检查照列、不证明格。
    'context_fill': {name: () for name in (
        'a_unit_goal_takes_one_hop_along_goal_ref_to_the_company_goal_and_the_plan_records_it',
        'from_a_unit_period_goal_why_covers_blocks_or_components_of_the_company_goal_the_strategy_and_the_company',
        'the_markdown_opens_with_the_six_question_guide_and_takes_the_six_questions_as_its_sections',
        'a_delegated_event_line_names_both_the_recorder_and_the_person_recorded_on_behalf_of',
        'the_same_world_state_gives_the_same_markdown_pack_plan_and_coverage',
        'an_assignment_event_line_names_the_recorder_and_the_assignee',
        'from_a_mission_the_pack_projects_its_tasks_expected_results_under_what_and_never_trims_them',
        'from_a_responsibility_unit_the_pack_lists_the_missions_of_its_domain_as_a_projection',
        'a_tight_budget_trims_in_the_0_2_order_keeps_the_current_object_and_the_guide_only_points_at_what_is_left',
        'after_a_roll_forward_the_affected_gated_object_carries_the_issue_into_its_formation',
        'carried_issues_are_never_trimmed',
        'once_the_affected_object_records_a_gate_event_the_issue_is_no_longer_carried',
        'forming_a_period_goal_carries_a_pending_issue_on_its_units_long_term_goal',
        'forming_a_period_goal_carries_a_pending_issue_on_its_responsibility_unit',
        'once_a_period_goal_of_the_unit_records_a_gate_event_the_units_issue_is_no_longer_carried_and_others_stay',
        'after_a_company_review_is_confirmed_forming_a_period_goal_carries_it_pinned_to_its_snapshot',
        'with_a_later_confirmed_review_the_latest_by_as_of_is_carried',
        'forming_a_period_goal_carries_the_units_confirmed_long_term_goals_and_not_terminated_drafts_or_its_own',
        'the_formation_carry_in_is_never_trimmed')},
    'state_cells': {
        **{cell_check(object_type, row, kind): (f'lifecycle:{row_key(object_type, row)}:{kind}',)
           for object_type, row in rows() for kind in ROW_KINDS},
        'mission_a_returned_round_is_void_and_nothing_is_written_back':
            P('world_confirm_mission') + ('round:Mission:returned_round_is_void',),
        'the_same_external_event_under_two_keys_is_recorded_twice':
            P('world_record_event') + ('duplicates:other_keys_record_events',),
    },
    'revocation': {
        'a_revoked_ceo_cannot_replay_the_0_2_creation_into_success': R('world_create_object'),
    },
    # 实验 E 的五个场景（#67）：新 scope 里播种与回放，不在票面十项里，检查照列、不证明格。
    'experiment_e': {name: () for name in (
        'experiment_e_the_scenarios_and_the_gold_answers_are_consistent',
        'experiment_e_the_five_scenarios_are_seeded_over_http_into_a_fresh_scope',
        'experiment_e_the_manifest_carries_no_credentials',
        'experiment_e_cross_unit_recovers_the_six_questions_from_its_start',
        'experiment_e_cross_unit_has_its_decoys_in_place',
        'experiment_e_constraint_conflict_recovers_the_six_questions_from_its_start',
        'experiment_e_constraint_conflict_has_its_decoys_in_place',
        'experiment_e_version_change_recovers_the_six_questions_from_its_start',
        'experiment_e_version_change_has_its_decoys_in_place',
        'experiment_e_rework_restart_recovers_the_six_questions_from_its_start',
        'experiment_e_rework_restart_has_its_decoys_in_place',
        'experiment_e_task_only_recovers_the_six_questions_from_its_start',
        'experiment_e_task_only_has_its_decoys_in_place')},
    # 对照实验 B（#66）：两条线的播种、驱动与观测，用实验本身的代码打本次起的 API；不证明矩阵的格。
    'experiment_b': {name: () for name in (
        'experiment_b_both_lines_seed_the_trial_mission_and_read_back_as_b_lines_says',
        'experiment_b_the_objects_down_to_the_tasks_have_the_same_shape_on_both_lines',
        'experiment_b_both_missions_are_closed_on_the_read_projection',
        'experiment_b_the_tasks_are_closed_on_both_lines_and_the_activities_on_the_task_activity_line',
        'experiment_b_every_committed_action_in_the_logs_has_its_event_with_the_same_kind_recorder_and_receipt',
        'experiment_b_each_scope_holds_exactly_the_events_and_receipts_the_logs_committed',
        'experiment_b_the_evidence_read_over_http_covers_every_event_of_the_logs',
        'experiment_b_the_smoke_is_native_on_task_activity_and_mixes_native_coarse_and_rejected_on_task_only',
        'experiment_b_seeding_and_driving_again_resume_from_the_log_and_write_nothing',
        'experiment_b_the_five_observations_come_out_of_the_logs_with_events_of_the_task_activity_scope',
        'experiment_b_the_smoke_observations_match_the_documented_outcome')},
    # 四种取法对照（#68）：新 scope 里播种实验 E，跑实验跑器的准备、核对交给模型的文本，不调模型；不证明矩阵的格。
    'experiment_r': {name: () for name in (
        'experiment_r_the_five_scenarios_are_seeded_over_http_into_another_fresh_scope',
        'experiment_r_the_full_text_holds_every_object_and_event_of_the_scope',
        'experiment_r_every_chunk_carries_exactly_its_own_reference',
        'experiment_r_every_taken_reference_is_written_in_the_text_handed_to_the_model',
        'experiment_r_every_taken_reference_reads_back_through_the_read_projection',
        'experiment_r_each_rag_text_stays_within_the_characters_the_fixed_path_hands_to_the_model')},
}

TITLES = {
    'migration': '迁移 0039', 'control_plane': '控制面安装', 'company': 'Company 与三组读回', 'objects': '业务对象、组件与引用',
    'rejections': '建对象的拒绝', 'coexistence': '与 0.1 并存', 'references': '引用与组件的拒绝',
    'revise_relate': '修订与建关系', 'state_events': '状态快照、外部事件、迟记与更正',
    'assign_lifecycle': '指派与 Task、Activity 的生命周期', 'gates': '有门对象的承诺与确认、一轮重走',
    'context_packs': '取上下文', 'mission_lifecycle': 'Mission 的执行生命周期与关注标记', 'delegation': '代记',
    'mcp_end_to_end': 'MCP 端到端', 'list_objects': '列对象、外部引用与 0.1 视图', 'issues': 'Issue',
    'goal_closure': '长期目标与周期目标的收口', 'strategy_gates': 'Strategy 的门', 'context_fill': '取上下文补齐',
    'state_cells': '状态表逐格',
    'revocation': '撤掉指派后不能重放',
    'experiment_e': '实验 E 的五个场景：播种与回放',
    'experiment_b': '对照实验 B 的两条线',
    'experiment_r': '四种取法对照：准备与引用核对',
}

# 没有驱动、没有验证的路径与部署边界（摘自 README「没有驱动的路径」），报告里单列。
UNVERIFIED = [
    '部署：本验收只在隔离验收栈的新建库上跑；联调实例 world-02、world-lab 与生产都没有跑，合并或本地通过不等于已部署。',
    '冻结：锁版前不冻结（ADR-0009），不钉提交、不生成冻结检查点，world_v02_accepted 恒为 false。',
    '并发写、API 进程重启恢复、事务中途故障注入：没有驱动。',
    '最终复核：判权与提交之间指派、委托、被指定的人自然失效的路径没有在 HTTP 上驱动（纯函数与无库测试覆盖一部分）。',
    '一轮进行中撤回重开回到已关闭（其间开的一轮作废）、重开不恢复作废的一轮：只在纯函数测试里核对（#69）。',
    'Issue 的状态表只以 Mission 为主受影响对象逐格驱动；长期目标与责任单元的问题只驱动了形成时带入（#64、#69），'
    '周期目标与 Strategy 的问题没有在 HTTP 上驱动；责任单元上的问题不因别的单元的周期目标记门事件而失效，没有驱动。',
    'CLI 的 0.2 面、MCP 的列对象与三个问题工具只在无库测试里打假 HTTP 核对。',
    '0.1 视图只驱动了长期目标与快照；取事件、取上下文、取子对象没有 0.2 视图参数。',
    '代记 Mission 的生命周期动作、代记撤回、代记指定本轮与 Strategy 的确认与再确认：机制同其余代记，由无库测试核对。',
    '议题族（#71）：承接人在另一单元时的代记（委托须覆盖问题所在的域）、委托不含议题族时代处置与代退回形成的拒绝、'
    '被代记的人不是承接人时的拒绝，只由无库测试换掉读库的几处、跑服务里的判权路径核对。',
    '工作台与看板、证据上传、真实模型：没有驱动。',
    '对照实验 B（#66）：只在隔离库上用合成的冒烟脚本驱动；实例上的供给、播种与驱动没有跑，真实记录的转写与回放要等试用。',
    '四种取法对照（#68）：只跑准备与引用核对，不调模型；四组作答、指标、触发检查与报告只由无库测试按录好的运行核对。',
]

# 补检查时发现的服务与契约不符之处（格、期望、实际、复现），由人分派；没有时为空。
DEVIATIONS: list[dict] = [
    {'cell': 'read:legacy_0_1_view',
     'expected': '0.1 对象带 view=tkos.world/0.2 读时按第 15.1 节分组（契约第 15.4 节），business 的键同 0.2 取对象'
                 '（#63 起验收这样核）；#79 给 0.2 的 business 加了 projection（没有投影项的类型为 null）',
     'actual': 'world_v02_legacy.object_view 的 business 没有 projection 这个键，其余键相同（#80 发现，未改 src）',
     'reproduce': 'GET /v1/world/objects/<0.1 长期目标>?view=tkos.world/0.2 与 GET /v1/world/objects/<0.2 长期目标>，'
                  '比较 business 的键集合；list_objects 场景的 LEGACY_BUSINESS 按实际形状核对'},
]


# ------------------------------------------------------------------ 判定
def cell_checks(checks=CHECKS):
    """格 → 证明它的检查名（按 CHECKS 的顺序）。"""
    found = {cell: [] for cell in CELLS}
    for named in checks.values():
        for name, proved in named.items():
            for cell in proved:
                found[cell].append(name)
    return found


def evaluate(recorded, where, scenarios=None):
    """报告的矩阵字段。recorded：检查名 → 是否通过；where：检查名 → 记下它时所在的场景；scenarios：场景 → 状态。"""
    required = {name: scenario for scenario, named in CHECKS.items() for name in named}
    passed = {name for name, ok in recorded.items() if ok}
    proved = cell_checks()
    covered = {cell: [name for name in names if name in passed] for cell, names in proved.items()}
    uncovered = [cell for cell in CELLS if cell not in NOT_APPLICABLE and not covered[cell]]
    unlisted = [name for name in recorded if name not in required]
    misplaced = [name for name in recorded if name in required and where.get(name) != required[name]]
    missing = [name for name in required if name not in recorded]
    failed = [name for name, ok in recorded.items() if not ok]
    by_item = {}
    for item in ITEMS:
        ids = [cell for cell, row in CELLS.items() if row['item'] == item]
        by_item[item] = {'title': ITEMS[item], 'cells': len(ids),
                         'covered': sum(bool(covered[cell]) for cell in ids),
                         'not_applicable': sum(cell in NOT_APPLICABLE for cell in ids),
                         'uncovered': [cell for cell in ids if cell in uncovered]}
    groups = {scenario: {'title': TITLES[scenario], 'required': len(named),
                         'passed': sum(name in passed for name in named),
                         'status': (scenarios or {}).get(scenario, {}).get('status', 'not_started')}
              for scenario, named in CHECKS.items()}
    matrix_passed = not (uncovered or unlisted or misplaced or missing or failed)
    return {
        'matrix_passed': matrix_passed,
        'mandatory_groups': len(CHECKS), 'mandatory_checks': len(required),
        'required_checks': {scenario: list(named) for scenario, named in CHECKS.items()},
        'titles': TITLES, 'groups': groups,
        'matrix': {'cells': len(CELLS), 'covered': sum(bool(names) for names in covered.values()),
                   'not_applicable': len(NOT_APPLICABLE), 'uncovered': uncovered, 'by_item': by_item},
        'action_coverage': {action: {kind: covered[f'action:{action}:{kind}'] for kind in ACTION_KINDS}
                            for action in SUPPORT['actions']},
        'lifecycle_coverage': {object_type: {row_key(object_type, row): {
            kind: covered[f'lifecycle:{row_key(object_type, row)}:{kind}'] for kind in ROW_KINDS}
            for row in spec['transitions']} for object_type, spec in tables().items()},
        'topic_coverage': {item: {cell: covered[cell] for cell in named}
                           for item, named in topic_cells().items()},
        'not_applicable': NOT_APPLICABLE,
        'unlisted_checks': unlisted, 'misplaced_checks': misplaced, 'missing_checks': missing,
        'failed_checks': failed,
        'unverified': UNVERIFIED, 'deviations': DEVIATIONS, 'sources': SOURCES,
    }


def render_markdown(report):
    """中文报告：已执行、跳过、未验证分开写。"""
    matrix, groups = report['matrix'], report['groups']
    lines = [
        '# tkos.world/0.2 独立验收矩阵报告（不冻结）', '',
        f"- 开始 {report.get('started_at')}，结束 {report.get('updated_at')}（UTC）；源码 `{report.get('source_root')}`。",
        f"- 结论：passed = `{str(report['passed']).lower()}`（矩阵 `{str(report['matrix_passed']).lower()}`，"
        f"场景全部跑完 `{str(report['all_scenarios_completed']).lower()}`，源码运行中不变 "
        f"`{str(report.get('source_unchanged')).lower()}`）；world_v02_accepted = `false`：锁版前不冻结（ADR-0009）。",
        f"- 组（场景）{report['mandatory_groups']} 个，必需检查 {report['mandatory_checks']} 项，通过 "
        f"{report['checks_passed']} 项、失败 {report['checks_failed']} 项。",
        f"- 矩阵共 {matrix['cells']} 格：覆盖 {matrix['covered']}，不适用 {matrix['not_applicable']}，"
        f"未覆盖 {len(matrix['uncovered'])}。", '',
        '## 已执行', '', '### 按场景（组）', '', '| 场景 | 说明 | 状态 | 必需检查 | 通过 |', '|-|-|-|-|-|',
        *[f"| `{name}` | {row['title']} | {row['status']} | {row['required']} | {row['passed']} |"
          for name, row in groups.items()],
        '', '### 按票面十项', '', '| 项 | 格 | 覆盖 | 不适用 | 未覆盖 |', '|-|-|-|-|-|',
        *[f"| {row['title']} | {row['cells']} | {row['covered']} | {row['not_applicable']} | {len(row['uncovered'])} |"
          for row in matrix['by_item'].values()],
        '', '### 不适用的格', '',
        *([f'- `{cell}`：{reason}' for cell, reason in report['not_applicable'].items()] or ['- 无']),
        '', '### 未覆盖的格', '',
        *([f"- `{cell}`：{CELLS[cell]['title']}" for cell in matrix['uncovered']] or ['- 无']),
        '', '### 失败的检查', '', *([f'- `{name}`' for name in report['failed_checks']] or ['- 无']),
        '', '### 没列进矩阵的检查（记下了、但 CHECKS 里没有，或不在它所属的场景里记）', '',
        *([f'- `{name}`' for name in report['unlisted_checks'] + report['misplaced_checks']] or ['- 无']),
        '', '## 跳过', '',
        f"- 没跑完的场景：{'、'.join(f'`{n}`' for n, row in groups.items() if row['status'] != 'completed') or '无'}。",
        f"- 必需但没有记下的检查：{len(report['missing_checks'])} 项"
        + ('' if not report['missing_checks'] else '（' + '、'.join(f'`{n}`' for n in report['missing_checks'][:20])
           + ('……' if len(report['missing_checks']) > 20 else '') + '）') + '。',
        '', '## 未验证', '', *[f'- {text}' for text in report['unverified']],
        '', '## 服务与契约不符', '',
        *([f"- `{row['cell']}`：期望 {row['expected']}；实际 {row['actual']}；复现 {row['reproduce']}"
           for row in report['deviations']] or ['- 无']), '',
    ]
    if report.get('run_error'):
        lines[5:5] = [f"- 运行错误：`{report['run_error'].get('exception_type')}`（见 report.json 的 run_error）。"]
    return '\n'.join(lines)
