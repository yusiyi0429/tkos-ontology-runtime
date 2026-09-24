"""Real HTTP+PG acceptance skeleton of tkos.world/0.1 (ticket #19: wiring and the Company root).

Business success comes only from /v1/actions/prepare + /v1/actions; SQL is used for
identity seeding and independent assertions. Every rejection is observed at the
entrances it can reach, with an unchanged scope snapshot. No real model is run.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from acceptance.method_independent.fixture import uid
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.control_adapter import ControlAdapter
from acceptance.protocol_a1_independent.support import private_json, public_json, source_manifest
from .fixture import (GATE_ROLES, ROOT, activation_policy, install_activation_policies, owner_rows,
                      probe_binding_gate, register_world, revoke_assignment, seed_world)
from .flow import Flow

PROFILE = json.loads((ROOT / 'docs/contracts/world-profile-0.1.json').read_text())


def _checker(checks):
    """返回一个记录并打印通过项的断言函数。"""
    def check(name, value=True):
        assert value, name
        checks.append(name)
        print('PASS ' + name, flush=True)
    return check


def company_root(h, f, flow):
    checks = []
    check = _checker(checks)

    last = flow.rows('SELECT name FROM schema_migrations ORDER BY name DESC LIMIT 1')[0]['name']
    check('the_world_migration_is_the_newest_applied_migration', last == '0035_world_v01_gates_repin.sql')

    # ------------------------------------------------------------ happy path
    identity = {'text': '一家为企业做经营系统的公司。', 'artifacts': ['https://example.test/company-brief']}
    body = flow.prepare('ceo', flow.command('world_create_object', flow.company_params(blocks={'identity': identity})))
    receipt = flow.commit('ceo', body)
    company = receipt['result']
    check('the_ceo_creates_the_company_over_http',
          receipt['action_type'] == 'world_create_object' and company['version'] == 1
          and company['ref'] == company['object_id'] + '@1')

    view = flow.read('ceo', company['object_id'])
    blocks = {b['id']: b for b in view['blocks']}
    check('the_object_view_renders_blocks_citations_and_the_empty_block_sentence',
          view['object_type'] == 'Company' and view['type_display_name'] == '公司' and view['title'] == 'E&O 合成公司'
          and view['version'] == 1 and list(blocks) == ['identity', 'constraint']
          and blocks['identity']['text'] == identity['text'] and not blocks['identity']['empty']
          and blocks['identity']['value']['artifacts'] == identity['artifacts']
          and blocks['constraint']['empty'] and blocks['constraint']['text'] == '当前没有约束'
          and blocks['identity']['ref'] == company['object_id'] + '@1#identity')
    check('the_company_is_formal_on_creation_and_read_as_world_0_1',
          view['formal']['lifecycle_status'] == 'recorded'
          and view['formal']['effective_revision_id'] == company['revision_id']
          and view['protocol']['interpretation_status'] == 'world_v0_1'
          and view['protocol']['contract_version'] == 'tkos.world/0.1')

    events = flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s', (f['scope_id'],))
    check('exactly_one_object_created_event_points_back_to_its_receipt',
          len(events) == 1 and events[0]['kind'] == 'object.created'
          and str(events[0]['action_id']) == receipt['receipt_id']
          and str(events[0]['principal_id']) == f['actors']['ceo']['principal_id']
          and events[0]['subject_refs'] == [{'object_id': company['object_id'], 'object_version': 1,
                                             'revision_id': company['revision_id'], 'block': None}])
    receipts = flow.rows('SELECT receipt_id FROM gov_action_receipts WHERE scope_id=%s', (f['scope_id'],))
    check('exactly_one_receipt_is_written_for_the_creation',
          [str(row['receipt_id']) for row in receipts] == [receipt['receipt_id']])
    binding = flow.rows('SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s',
                        (f['scope_id'], company['object_id']))
    check('the_binding_pins_the_world_profile_through_the_world_gate',
          len(binding) == 1 and binding[0]['protocol_id'] == 'tkos.world'
          and binding[0]['contract_version'] == 'tkos.world/0.1'
          and binding[0]['profile_id'] == PROFILE['profile_id']
          and binding[0]['profile_canonical_hash'] == PROFILE['canonical_hash'])

    # ------------------------------------------------------------ reads
    # outsider 只在另一个域有指派，域级读策略读不到 Company；读得到说明走的是 world 的 scope 级规则。
    check('an_identity_assigned_only_in_another_domain_reads_the_company_by_the_world_rule',
          flow.read('outsider', company['object_id'])['object_id'] == company['object_id'])
    seen = flow.clients['outsider'].json('GET', f"/v1/action-receipts/{receipt['receipt_id']}")
    check('the_world_receipt_is_read_by_the_world_rule', seen['receipt']['receipt_id'] == receipt['receipt_id'])
    missing = flow.read('foreign_ceo', company['object_id'], expected=404)
    check('an_identity_from_another_scope_cannot_read_the_company', missing['error']['code'] == 'NOT_FOUND')

    # ------------------------------------------------------------ idempotency
    replay = flow.commit('ceo', deepcopy(body))
    check('replaying_the_same_command_returns_the_original_receipt',
          replay['receipt_id'] == receipt['receipt_id']
          and len(flow.rows('SELECT 1 FROM gov_world_events WHERE scope_id=%s', (f['scope_id'],))) == 1)
    changed = deepcopy(body)
    changed['params']['payload']['title'] = 'Another title under the same key'
    flow.deny('ceo', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    check('reusing_a_key_for_a_different_command_is_refused')

    # ------------------------------------------------------------ rejections
    flow.deny('unrelated', flow.command('world_create_object', flow.company_params(title='IC company')),
              codes={'FORBIDDEN'})
    check('an_ic_cannot_create_the_company')
    flow.deny('agent', flow.command('world_create_object', flow.company_params(title='Agent company', domain='a')),
              codes={'FORBIDDEN'})
    check('an_agent_cannot_create_the_company')
    extra = flow.company_params()
    extra['payload']['owner'] = 'not part of the Company contract'
    flow.deny('ceo', flow.command('world_create_object', extra), codes={'INVALID_REQUEST'})
    check('a_payload_field_outside_the_contract_is_refused')
    blank = flow.company_params(blocks={'identity': {'text': '   '}})
    flow.deny('ceo', flow.command('world_create_object', blank), codes={'INVALID_REQUEST'})
    check('an_empty_block_cannot_pose_as_content')
    flow.deny('ceo', flow.command('world_create_object', flow.company_params(title='Second company')),
              codes={'INVALID_STATE'}, prepare=False)
    check('a_scope_has_exactly_one_company')
    stale = flow.command('world_create_object', flow.company_params(title='Stale company'),
                         expected_versions=[{'object_id': company['object_id'], 'expected_version': 99}])
    flow.deny('ceo', stale, codes={'VERSION_CONFLICT'}, prepare=False)
    check('a_wrong_expected_version_is_refused')
    flow.deny('ceo', flow.command('m1b_confirm_constraint', {'statement': 'A Method action aimed at a world object.'})
              | {'contract_version': 'tkos.method/0.5',
                 'target': {'object_id': company['object_id'], 'revision_id': company['revision_id'],
                            'expected_version': 1}},
              codes={'PROTOCOL_NOT_SUPPORTED', 'PROTOCOL_BINDING_CONFLICT'})
    check('an_action_of_another_protocol_cannot_touch_a_world_object')
    mission = flow.company_params(title='Not yet creatable')
    mission['object_type'] = 'Mission'
    flow.deny('outsider', flow.command('world_create_object', mission), codes={'FORBIDDEN'})
    check('authorization_is_refused_before_any_protocol_error')

    # 另一 scope 没有安装 world：通过授权的请求返回协议错误，那个 scope 里也没有留下任何 world 行。
    foreign = flow.command('world_create_object', {'domain_id': f['foreign_domains']['company'], 'object_type': 'Company',
                                                   'payload': {'title': 'Foreign company'}})
    for path in ('/v1/actions/prepare', '/v1/actions'):
        response = flow.clients['foreign_ceo'].json('POST', path, foreign, expected=409)
        assert response['error']['code'] == 'PROTOCOL_NOT_SUPPORTED', (path, response)
    leftovers = h.sql({'scope_id': f['foreign_scope_id']},
                      "SELECT (SELECT count(*) FROM gov_objects WHERE scope_id=%s) + (SELECT count(*) FROM gov_world_events WHERE scope_id=%s) AS n",
                      (f['foreign_scope_id'], f['foreign_scope_id']))[0]['n']
    check('a_scope_without_world_answers_protocol_not_supported_and_writes_nothing', leftovers == 0)

    before = h.snapshot(f)
    refusal = probe_binding_gate(h.env, f, '0' * 64)
    check('the_world_gate_refuses_a_world_binding_whose_profile_does_not_pin_the_exact_contract',
          refusal is not None and 'world 0.1 requires its exact business-world contract and registry' in refusal
          and h.snapshot(f) == before)
    return {'checks': checks, 'company': company, 'company_command': body}


def _ref(obj, block=None):
    """引用的业务形式 `<对象 id>@<版本号>#<块路径>`。"""
    return f"{obj['object_id']}@{obj['version']}" + (f'#{block}' if block else '')


def _pinned(obj, block=None):
    """钉定引用的读回形式：结构化四项加业务形式。"""
    return {'object_id': obj['object_id'], 'object_version': obj['version'], 'revision_id': obj['revision_id'],
            'block': block, 'ref': _ref(obj, block)}


def objects_and_refs(h, f, flow, company):
    """票 #20：其余七类经 world_create_object 建出并读回，引用在写入时钉定；拒绝用例库快照不变。"""
    checks = []
    check = _checker(checks)

    def created(actor, object_type, domain, payload, declaration=None):
        receipt = flow.create(actor, object_type, domain, payload, declaration)
        return receipt, receipt['result']

    # ------------------------------------------------------------ the spine, top to bottom
    choices = {'text': '聚焦企业经营系统。', 'refs': [_ref(company, 'identity')]}
    _, strategy = created('ceo', 'Strategy', 'company', {
        'title': 'E&O 战略', 'parent_ref': _ref(company),
        'blocks': {'choices': choices, 'responsibility_structure': {'text': 'E&O 与交付两个战场。'}}})
    _, unit = created('ceo', 'ResponsibilityUnit', 'a', {
        'title': 'E&O', 'unit_kind': 'battlefield', 'architecture_ref': _ref(strategy, 'responsibility_structure')})
    _, company_goal = created('ceo', 'LongTermGoal', 'company', {
        'title': '公司三年目标', 'scope': 'company', 'horizon': '2029 年底', 'parent_ref': _ref(company)})
    _, unit_goal = created('a', 'LongTermGoal', 'a', {
        'title': 'E&O 年度目标', 'scope': 'unit', 'horizon': '2027 年底',
        'parent_ref': _ref(unit), 'goal_ref': _ref(company_goal)})
    _, period_goal = created('a', 'PeriodGoal', 'a', {
        'title': '9 月', 'period': '2026-09', 'goal_ref': _ref(unit_goal)})
    # 空块也可引用：周期目标的验收标准块此刻为 null。
    _, mission = created('a', 'Mission', 'a', {
        'title': '9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用', 'goal_ref': _ref(period_goal),
        'blocks': {'acceptance': {'text': '按周期目标验收。', 'refs': [_ref(period_goal, 'acceptance')]}}})
    declaration = {'scene': _ref(mission), 'trigger': '9/23 会议拆解 Mission',
                   'human_acceptance': {'required': True, 'acceptor': f['actors']['ceo']['principal_id']}}
    task_receipt, task = created('a', 'Task', 'a', {'title': '数据环境准备', 'parent_ref': _ref(mission)}, declaration)
    _, activity = created('ceo', 'Activity', 'a', {'title': '隔离库迁移与播种', 'parent_ref': _ref(task)})
    made = {'Strategy': strategy, 'ResponsibilityUnit': unit, 'LongTermGoal': company_goal,
            'UnitLongTermGoal': unit_goal, 'PeriodGoal': period_goal, 'Mission': mission, 'Task': task,
            'Activity': activity}
    check('the_ceo_and_the_unit_dri_create_the_whole_spine_over_http',
          all(obj['version'] == 1 and obj['ref'] == _ref(obj) for obj in made.values()))

    views = {name: flow.read('outsider', obj['object_id']) for name, obj in made.items()}
    relations = {name: {r['field']: r['value'] for r in view['relations']} for name, view in views.items()}
    check('relation_refs_read_back_pinned_to_the_referenced_revision',
          relations['Strategy'] == {'parent_ref': _pinned(company)}
          and relations['ResponsibilityUnit'] == {'architecture_ref': _pinned(strategy, 'responsibility_structure')}
          and relations['LongTermGoal'] == {'parent_ref': _pinned(company), 'goal_ref': None}
          and relations['UnitLongTermGoal'] == {'parent_ref': _pinned(unit), 'goal_ref': _pinned(company_goal)}
          and relations['PeriodGoal'] == {'goal_ref': _pinned(unit_goal)}
          and relations['Mission'] == {'goal_ref': _pinned(period_goal), 'depends_on': [], 'contributes_to': []}
          and relations['Task'] == {'parent_ref': _pinned(mission), 'depends_on': []}
          and relations['Activity'] == {'parent_ref': _pinned(task)})
    strategy_blocks = {b['id']: b for b in views['Strategy']['blocks']}
    mission_blocks = {b['id']: b for b in views['Mission']['blocks']}
    check('block_refs_read_back_pinned_and_an_empty_block_can_be_cited',
          strategy_blocks['choices']['value'] == {'text': choices['text'], 'artifacts': [],
                                                  'refs': [_pinned(company, 'identity')]}
          and strategy_blocks['responsibility_structure']['text'] == 'E&O 与交付两个战场。'
          and strategy_blocks['path']['empty'] and strategy_blocks['path']['text'] == '当前没有路径'
          and mission_blocks['acceptance']['value']['refs'] == [_pinned(period_goal, 'acceptance')])
    check('attributes_read_back_as_written_and_server_owned_ones_start_empty',
          views['ResponsibilityUnit']['attributes'] == {'unit_kind': 'battlefield'}
          and views['UnitLongTermGoal']['attributes'] == {'scope': 'unit', 'horizon': '2027 年底'}
          and views['PeriodGoal']['attributes'] == {'period': '2026-09'}
          and views['Mission']['attributes'] == {'core_battle': False, 'responsible': None}
          and views['Task']['attributes'] == {'responsible': None}
          and all(views[name]['title'] == title for name, title in (
              ('Strategy', 'E&O 战略'), ('Task', '数据环境准备'), ('Activity', '隔离库迁移与播种'))))
    gated = {'LongTermGoal', 'UnitLongTermGoal', 'PeriodGoal', 'Mission'}
    check('gated_types_start_as_draft_and_the_rest_are_formal_on_creation',
          all(view['formal'] == ({'lifecycle_status': 'draft', 'effective_revision_id': None} if name in gated else
                                 {'lifecycle_status': 'recorded', 'effective_revision_id': made[name]['revision_id']})
              for name, view in views.items()))

    ids = [obj['object_id'] for obj in made.values()]
    events = flow.rows("SELECT kind, subject_refs FROM gov_world_events WHERE scope_id=%s AND kind='object.created'"
                       " AND subject_refs->0->>'object_id' = ANY(%s)", (f['scope_id'], ids))
    bindings = flow.rows('SELECT profile_revision FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id = ANY(%s::uuid[])',
                         (f['scope_id'], ids))
    check('each_creation_writes_one_pinned_event_and_binds_through_the_repinned_world_gate',
          sorted(e['subject_refs'][0]['revision_id'] for e in events) == sorted(o['revision_id'] for o in made.values())
          and len(bindings) == len(ids) and {b['profile_revision'] for b in bindings} == {PROFILE['revision']})

    result = task_receipt['result']
    check('a_person_may_write_without_a_declaration_and_one_given_is_pinned_into_the_receipt',
          result['declaration'] == {**declaration, 'scene': _pinned(mission)}
          and set(result['referenced_object_ids']) == {task['object_id'], mission['object_id']}
          and all('declaration' not in r['result'] for r in flow.receipts if r['receipt_id'] != task_receipt['receipt_id']))

    # ------------------------------------------------------------ references that do not resolve
    missing_object = '00000000-0000-4000-8000-000000000000@1'
    unresolved = 'does not resolve to a version of a world object'
    for label, payload, says in (
            ('an_unknown_object', {'title': 'x', 'parent_ref': missing_object}, unresolved),
            ('an_unknown_version', {'title': 'x', 'parent_ref': f"{mission['object_id']}@2"}, unresolved),
            ('a_block_its_type_does_not_have', {'title': 'x', 'parent_ref': _ref(mission),
                                                'blocks': {'plan': {'text': 'x', 'refs': [_ref(mission, 'plan')]}}},
             'names a block its object type does not have'),
    ):
        flow.deny_create('a', 'Task', 'a', payload, codes={'INVALID_REQUEST'}, says=says)
        check(f'a_reference_to_{label}_is_refused')
    flow.deny_create('a', 'Task', 'a', {'title': 'x', 'parent_ref': _ref(mission), 'owner': 'not in the contract'},
                     codes={'INVALID_REQUEST'}, says='Payload does not satisfy')
    check('a_field_outside_the_contract_is_refused')
    flow.deny_create('a', 'Mission', 'a', {'title': 'x', 'goal_ref': _ref(period_goal), 'depends_on': [_ref(mission)]},
                     codes={'INVALID_REQUEST'}, says='Payload does not satisfy')
    check('a_cross_chain_relation_is_not_written_by_creation')

    # ------------------------------------------------------------ where objects live and what they hang on
    misplaced, decomposes = 'not placed in the domain its spine parent requires', 'Only a unit-level goal decomposes'
    new_unit = {'title': 'x', 'unit_kind': 'domain', 'architecture_ref': _ref(strategy, 'responsibility_structure')}
    for label, actor, object_type, domain, payload, says in (
            ('a_strategy_outside_the_company_domain', 'ceo', 'Strategy', 'a',
             {'title': 'x', 'parent_ref': _ref(company)}, misplaced),
            ('a_unit_in_the_company_domain', 'ceo', 'ResponsibilityUnit', 'company', new_unit, misplaced),
            ('a_mission_in_another_unit_than_its_period_goal', 'ceo', 'Mission', 'b',
             {'title': 'x', 'goal_ref': _ref(period_goal)}, misplaced),
            ('a_company_goal_hanging_on_a_unit', 'ceo', 'LongTermGoal', 'a',
             {'title': 'x', 'scope': 'company', 'horizon': 'x', 'parent_ref': _ref(unit)},
             'company-level goal hangs on the Company'),
            ('a_company_goal_with_a_goal_ref', 'ceo', 'LongTermGoal', 'company',
             {'title': 'x', 'scope': 'company', 'horizon': 'x', 'parent_ref': _ref(company),
              'goal_ref': _ref(company_goal)}, decomposes),
            ('a_unit_goal_decomposing_a_unit_goal', 'a', 'LongTermGoal', 'a',
             {'title': 'x', 'scope': 'unit', 'horizon': 'x', 'parent_ref': _ref(unit), 'goal_ref': _ref(unit_goal)},
             decomposes),
            ('a_period_goal_advancing_a_company_goal', 'ceo', 'PeriodGoal', 'company',
             {'title': 'x', 'period': '2026-10', 'goal_ref': _ref(company_goal)}, 'long-term goal of its own unit'),
            ('a_task_under_a_task', 'a', 'Task', 'a', {'title': 'x', 'parent_ref': _ref(task)},
             'parent_ref must point to one of Mission'),
    ):
        flow.deny_create(actor, object_type, domain, payload, codes={'INVALID_REQUEST'}, says=says)
        check(f'{label}_is_refused')
    flow.deny_create('ceo', 'ResponsibilityUnit', 'a', new_unit, codes={'INVALID_STATE'}, prepare=False,
                     says='exactly one responsibility unit')
    check('a_domain_has_exactly_one_responsibility_unit')
    flow.deny_create('ceo', 'StateSnapshot', 'a',
                     {'title': 'x', 'subject_ref': _ref(mission), 'as_of': '2026-09-24T09:37:00Z'},
                     codes={'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'}, says='written by world_refresh_state')
    check('a_state_snapshot_is_not_written_by_creation')

    # ------------------------------------------------------------ who may create
    not_responsible = 'Only a responsible person up the spine'
    flow.deny_create('a', 'ResponsibilityUnit', 'b', new_unit, codes={'FORBIDDEN'})
    check('a_dri_cannot_create_in_a_domain_where_they_hold_no_role')
    flow.deny_create('b', 'ResponsibilityUnit', 'b', new_unit, codes={'FORBIDDEN'}, says=not_responsible)
    check('only_the_ceo_creates_a_responsibility_unit')
    flow.deny_create('unrelated', 'Strategy', 'company', {'title': 'x', 'parent_ref': _ref(company)},
                     codes={'FORBIDDEN'}, says=not_responsible)
    check('an_ic_cannot_create_a_strategy')
    # 判权先于其余校验：别的单元的 DRI 挂到本单元的周期目标下，先被拒在责任人上，不暴露放置错误。
    flow.deny_create('b', 'Mission', 'b', {'title': 'x', 'goal_ref': _ref(period_goal)},
                     codes={'FORBIDDEN'}, says=not_responsible)
    check('a_dri_cannot_create_under_another_units_goal')

    # ------------------------------------------------------------ write declarations (Agent only)
    agent_task = {'title': 'Agent 分解', 'parent_ref': _ref(mission)}
    flow.deny_create('agent', 'Task', 'a', agent_task, codes={'INVALID_REQUEST'}, says='must declare its scene')
    check('an_agent_write_without_a_declaration_is_refused')
    for item in ('scene', 'trigger', 'human_acceptance'):
        partial = {k: v for k, v in declaration.items() if k != item}
        flow.deny_create('agent', 'Task', 'a', agent_task, partial, codes={'INVALID_REQUEST'})
        check(f'an_agent_write_missing_its_{item}_is_refused')
    flow.deny_create('agent', 'Task', 'a', agent_task, declaration, codes={'FORBIDDEN'}, says=not_responsible)
    check('a_declared_agent_still_needs_to_be_responsible_up_the_spine')
    no_person = 'declared acceptor is an active person'
    for label, bad, says in (
            ('whose_scene_is_not_a_mission_or_task', {**declaration, 'scene': _ref(period_goal)},
             'declared scene is a Mission or a Task'),
            ('whose_acceptor_is_an_agent', {**declaration, 'human_acceptance': {
                'required': True, 'acceptor': f['actors']['agent']['principal_id']}}, no_person),
            ('whose_acceptor_holds_no_role_in_the_scope', {**declaration, 'human_acceptance': {
                'required': True, 'acceptor': f['bystander_principal_id']}}, no_person),
            ('whose_acceptor_is_unknown', {**declaration, 'human_acceptance': {
                'required': True, 'acceptor': '00000000-0000-4000-8000-000000000000'}}, no_person),
    ):
        flow.deny_create('a', 'Task', 'a', {'title': 'x', 'parent_ref': _ref(mission)}, bad,
                         codes={'INVALID_REQUEST'}, says=says)
        check(f'a_declaration_{label}_is_refused')
    return {'checks': checks, 'made': made}


def revise_and_relate(h, f, flow, company, made):
    """票 #21：合并修订与版本链、引用不漂移、改钉同一对象的新版本、跨链关系、按 parent_ref 反查子对象。"""
    checks = []
    check = _checker(checks)
    strategy, unit, mission, task = made['Strategy'], made['ResponsibilityUnit'], made['Mission'], made['Task']
    period_goal, company_goal = made['PeriodGoal'], made['LongTermGoal']

    def world_events(kind, object_id):
        return flow.rows("SELECT subject_refs FROM gov_world_events WHERE scope_id=%s AND kind=%s"
                         " AND subject_refs->0->>'object_id' = %s", (f['scope_id'], kind, object_id))

    # ------------------------------------------------------------ merge revision and the version chain
    before = flow.read('outsider', strategy['object_id'])
    strategy2 = flow.revise('ceo', strategy['object_id'], {
        'title': 'E&O 战略（二）', 'blocks': {'path': {'text': '先 E&O 后交付。'}, 'choices': None}})['result']
    now = flow.read('outsider', strategy['object_id'])
    blocks = {b['id']: b for b in now['blocks']}
    check('a_revision_changes_only_what_it_names_and_the_rest_carries_over',
          strategy2['version'] == 2 and now['version'] == 2 and now['title'] == 'E&O 战略（二）'
          and blocks['path']['text'] == '先 E&O 后交付。'
          and blocks['choices']['empty'] and blocks['choices']['text'] == '当前没有战略选择'
          and blocks['responsibility_structure']['text'] == 'E&O 与交付两个战场。'
          and {r['field']: r['value'] for r in now['relations']} == {'parent_ref': _pinned(company)})
    old = flow.read('outsider', strategy['object_id'], version=1)
    check('the_old_version_is_still_read_by_its_number_and_the_new_one_supersedes_it',
          now['supersedes'] == _pinned(strategy) and old['supersedes'] is None
          and old['version'] == 1 and old['revision_id'] == strategy['revision_id']
          and {b['id']: b['value'] for b in old['blocks']} == {b['id']: b['value'] for b in before['blocks']}
          and now['formal'] == {'lifecycle_status': 'recorded', 'effective_revision_id': strategy2['revision_id']})
    flow.read('outsider', strategy['object_id'], version=3, expected=404)
    check('a_version_that_does_not_exist_is_not_found')
    events = world_events('object.revised', strategy['object_id'])
    check('a_revision_writes_one_object_revised_event_pinned_to_the_new_version',
          [e['subject_refs'] for e in events] == [[{k: v for k, v in _pinned(strategy2).items() if k != 'ref'}]])

    # 被引用对象出新修订，引用不漂移：Company、Strategy 都到了新版本，下级读回的仍是原来钉的版本。
    body = flow.prepare('ceo', flow.targeted('world_revise_object', company['object_id'], {
        'payload': {'blocks': {'constraint': {'text': '只做经营系统。'}}}}))
    receipt = flow.commit('ceo', body)
    replay = flow.commit('ceo', deepcopy(body))
    changed = deepcopy(body)
    changed['params']['payload'] = {'title': 'Another revision under the same key'}
    flow.deny('ceo', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    check('a_replayed_revision_returns_its_receipt_and_a_reused_key_for_another_patch_is_refused',
          replay['receipt_id'] == receipt['receipt_id'] and receipt['result']['version'] == 2
          and len(world_events('object.revised', company['object_id'])) == 1)
    relations = {r['field']: r['value'] for r in flow.read('outsider', unit['object_id'])['relations']}
    check('references_stay_pinned_after_the_referenced_objects_get_new_versions',
          flow.read('outsider', company['object_id'])['version'] == 2
          and {r['field']: r['value'] for r in flow.read('outsider', strategy['object_id'], version=1)['relations']}
          == {'parent_ref': _pinned(company)}
          and relations == {'architecture_ref': _pinned(strategy, 'responsibility_structure')})
    flow.revise('ceo', unit['object_id'], {'architecture_ref': _ref(strategy2, 'responsibility_structure')})
    relations = {r['field']: r['value'] for r in flow.read('outsider', unit['object_id'])['relations']}
    check('a_creation_reference_can_follow_the_same_object_to_a_newer_version',
          relations == {'architecture_ref': _pinned(strategy2, 'responsibility_structure')})

    # 有门类型草稿期由责任人直接修订，正式内容指针不动；无门类型由主干上级修订（Task 还没有责任人）。
    flow.revise('a', period_goal['object_id'], {'blocks': {'acceptance': {'text': '两个 Agent 都真实跑通。'}}})
    pg = flow.read('outsider', period_goal['object_id'])
    flow.revise('a', task['object_id'], {'blocks': {'plan': {'text': '人建库，Agent 播种。'}}})
    check('a_draft_gated_goal_is_revised_without_moving_its_formal_pointer_and_a_task_by_its_unit_dri',
          pg['version'] == 2 and pg['formal'] == {'lifecycle_status': 'draft', 'effective_revision_id': None}
          and flow.read('outsider', task['object_id'])['version'] == 2)

    # ------------------------------------------------------------ cross-chain relations
    second = flow.create('a', 'Mission', 'a', {'title': '数据环境可用', 'goal_ref': _ref(period_goal)})['result']
    unit_b = flow.create('ceo', 'ResponsibilityUnit', 'b', {
        'title': '交付', 'unit_kind': 'domain', 'architecture_ref': _ref(strategy2, 'responsibility_structure')})['result']
    goal_b = flow.create('b', 'LongTermGoal', 'b', {'title': '交付年度目标', 'scope': 'unit', 'horizon': '2027 年底',
                                                    'parent_ref': _ref(unit_b)})['result']
    mission_before = flow.read('outsider', mission['object_id'])['version']
    related = flow.relate('a', mission['object_id'], 'depends_on', [_ref(second)])['result']
    flow.relate('a', mission['object_id'], 'contributes_to', [_ref(goal_b)])
    view = flow.read('outsider', mission['object_id'])
    relations = {r['field']: r['value'] for r in view['relations']}
    relate_events = world_events('relate', mission['object_id'])
    check('relations_read_back_pinned_on_the_object_that_holds_them_and_the_other_end_is_untouched',
          related['version'] == mission_before + 1 and view['version'] == mission_before + 2
          and relations['depends_on'] == [_pinned(second)] and relations['contributes_to'] == [_pinned(goal_b)]
          and relations['goal_ref'] == _pinned(period_goal)
          and flow.read('outsider', second['object_id'])['version'] == 1
          and view['formal'] == {'lifecycle_status': 'draft', 'effective_revision_id': None})
    check('each_relate_writes_exactly_one_relate_event_naming_both_ends',
          len(relate_events) == 2
          and all([r['object_id'] for r in e['subject_refs']][:1] == [mission['object_id']] for e in relate_events)
          and sorted(e['subject_refs'][1]['object_id'] for e in relate_events)
          == sorted([second['object_id'], goal_b['object_id']]))
    source = _pinned({'object_id': mission['object_id'], 'version': view['version'], 'revision_id': view['revision_id']})
    check('the_other_end_lists_the_relations_that_point_at_it',
          flow.read('outsider', second['object_id'])['referenced_by']
          == [{'field': 'depends_on', 'relation': 'depends_on', 'source': source, 'target': _pinned(second)}]
          and flow.read('outsider', goal_b['object_id'])['referenced_by']
          == [{'field': 'contributes_to', 'relation': 'contributes_to', 'source': source, 'target': _pinned(goal_b)}])
    body = flow.prepare('a', flow.targeted('world_relate', mission['object_id'], {'field': 'depends_on', 'refs': []}))
    receipt = flow.commit('a', body)
    replay = flow.commit('a', deepcopy(body))
    relations = {r['field']: r['value'] for r in flow.read('outsider', mission['object_id'])['relations']}
    check('a_relation_list_is_replaced_as_a_whole_so_it_can_be_emptied_and_a_replay_changes_nothing',
          relations['depends_on'] == [] and relations['contributes_to'] == [_pinned(goal_b)]
          and replay['receipt_id'] == receipt['receipt_id'] and len(world_events('relate', mission['object_id'])) == 3
          and flow.read('outsider', second['object_id'])['referenced_by'] == [])

    # ------------------------------------------------------------ children by parent_ref
    task2 = flow.create('a', 'Task', 'a', {'title': '写内容块标准', 'parent_ref': _ref(mission)})['result']
    kids = flow.children('outsider', mission['object_id'])['children']
    company_kids = flow.children('outsider', company['object_id'])['children']
    check('children_are_all_and_only_the_objects_whose_parent_ref_points_at_the_object',
          flow.children('outsider', strategy['object_id'])['children'] == []  # 责任单元经 architecture_ref 挂在 Strategy 下
          and sorted(c['object_id'] for c in kids) == sorted([task['object_id'], task2['object_id']])
          and {c['object_type'] for c in kids} == {'Task'}
          and sorted(c['object_id'] for c in company_kids) == sorted([strategy['object_id'], company_goal['object_id']])
          and all(c['parent_ref']['object_id'] == mission['object_id'] for c in kids))
    flow.children('foreign_ceo', mission['object_id'], expected=404)
    check('an_identity_from_another_scope_cannot_list_children')

    # ------------------------------------------------------------ rejections
    not_responsible = 'Only a responsible person up the spine'
    cannot_move = 'can only be re-pinned to another version of the object it was created with'
    declaration = {'scene': _ref(mission), 'trigger': '会后整理',
                   'human_acceptance': {'required': True, 'acceptor': f['actors']['ceo']['principal_id']}}

    def deny_revise(actor, obj, patch, codes, says=None, declared=None, target=None, prepare=True):
        params = {'payload': patch, **({'declaration': declared} if declared else {})}
        flow.deny(actor, flow.targeted('world_revise_object', obj['object_id'], params, target), codes=codes,
                  says=says, prepare=prepare)

    def deny_relate(actor, obj, field, refs, codes, says=None, declared=None):
        params = {'field': field, 'refs': refs, **({'declaration': declared} if declared else {})}
        flow.deny(actor, flow.targeted('world_relate', obj['object_id'], params), codes=codes, says=says)

    deny_revise('unrelated', strategy, {'title': 'x'}, {'FORBIDDEN'}, not_responsible)
    check('someone_not_responsible_up_the_spine_cannot_revise')
    deny_revise('ic_a', task, {'title': 'x'}, {'FORBIDDEN'}, not_responsible)
    check('a_unit_member_who_is_not_responsible_cannot_revise_a_task')
    deny_revise('agent', mission, {'title': 'x'}, {'FORBIDDEN'}, 'Agent revises only ungated', declaration)
    check('an_agent_cannot_revise_a_gated_type')
    deny_revise('agent', task, {'title': 'x'}, {'INVALID_REQUEST'}, 'must declare its scene')
    check('an_agent_revision_without_a_declaration_is_refused')
    unattended = {**declaration, 'human_acceptance': {'required': False}}
    deny_revise('agent', task, {'title': 'x'}, {'INVALID_REQUEST'}, 'needs human acceptance', unattended)
    check('an_agent_revision_must_ask_for_human_acceptance')
    deny_revise('agent', task, {'title': 'x'}, {'FORBIDDEN'}, not_responsible, declaration)
    check('a_declared_agent_still_needs_to_be_responsible_to_revise')
    stale = {'object_id': strategy['object_id'], 'revision_id': strategy2['revision_id'], 'expected_version': 1}
    deny_revise('ceo', strategy, {'title': 'x'}, {'VERSION_CONFLICT'}, target=stale, prepare=False)
    check('a_revision_with_a_wrong_expected_version_is_refused')
    deny_revise('a', task, {'parent_ref': _ref(second)}, {'INVALID_REQUEST'}, cannot_move)
    check('a_revision_cannot_move_an_object_under_another_parent')
    deny_revise('a', made['UnitLongTermGoal'], {'goal_ref': None}, {'INVALID_REQUEST'}, cannot_move)
    check('a_revision_cannot_drop_a_creation_reference')
    deny_revise('a', mission, {'responsible': f['actors']['a']['principal_id']}, {'INVALID_REQUEST'},
                'Payload does not satisfy')
    check('a_revision_cannot_write_a_field_only_the_service_writes')

    deny_relate('ic_a', mission, 'depends_on', [_ref(second)], {'FORBIDDEN'}, not_responsible)
    check('someone_not_responsible_cannot_relate')
    deny_relate('a', mission, 'depends_on', [_ref(mission)], {'INVALID_REQUEST'}, 'cannot depend on itself')
    check('an_object_cannot_depend_on_itself')
    deny_relate('a', mission, 'contributes_to', [_ref(period_goal)], {'INVALID_REQUEST'}, 'of another unit')
    check('a_contribution_goes_to_another_units_goal')
    deny_relate('a', mission, 'contributes_to', [_ref(company_goal)], {'INVALID_REQUEST'}, 'of another unit')
    check('a_contribution_does_not_go_to_a_company_level_goal')
    deny_relate('a', mission, 'depends_on', [_ref(task)], {'INVALID_REQUEST'}, 'depends_on must point to one of Mission')
    check('a_relation_points_to_a_type_the_registry_allows')
    deny_relate('a', task, 'contributes_to', [_ref(goal_b)], {'INVALID_REQUEST'}, 'has no contributes_to')
    check('a_relation_field_the_type_does_not_have_is_refused')
    return {'checks': checks}


def state_and_events(h, f, flow, made):
    """票 #22：状态快照写入与按时点取状态，外部事件、更正与按起始时间取事件；拒绝用例库快照不变。"""
    checks = []
    check = _checker(checks)
    mission, task = made['Mission'], made['Task']
    mission_id = mission['object_id']
    declaration = {'scene': _ref(mission), 'trigger': '9/22 17:37 会后整理',
                   'human_acceptance': {'required': True, 'acceptor': f['actors']['ceo']['principal_id']}}

    def refresh_body(payload, declared=None):
        return flow.command('world_refresh_state', {'payload': payload, **({'declaration': declared} if declared else {})})

    # ------------------------------------------------------------ state snapshots
    first = flow.refresh('a', {'title': '9/22 状态', 'subject_ref': _ref(mission), 'as_of': '2026-09-22T17:37:00+08:00',
                               'blocks': {'progress': {'text': '数据环境搭了一半。'}, 'issue': {'text': '缺隔离库权限。'}}})['result']
    second = flow.refresh('agent_a', {
        'title': '9/23 状态', 'subject_ref': _ref(mission), 'as_of': '2026-09-23T10:00:00.5Z',
        'blocks': {'progress': {'text': '隔离库已迁移。'},
                   'artifacts': {'text': '播种草稿', 'artifacts': ['https://example.test/seed-draft']}}}, declaration)['result']
    rows = flow.rows("""SELECT o.domain_id, o.lifecycle_status, o.latest_revision_id, o.effective_revision_id, r.payload
                          FROM gov_objects o JOIN gov_object_revisions r
                            ON r.scope_id=o.scope_id AND r.revision_id=o.latest_revision_id
                         WHERE o.scope_id=%s AND o.object_id = ANY(%s::uuid[]) ORDER BY r.payload->>'title'""",
                     (f['scope_id'], [first['object_id'], second['object_id']]))
    check('a_unit_dri_and_a_unit_agent_write_snapshots_stored_in_the_subjects_domain_with_utc_text',
          [row['payload']['as_of'] for row in rows] == ['2026-09-22T09:37:00Z', '2026-09-23T10:00:00.500000Z']
          and all(str(row['domain_id']) == f['domains']['a'] and row['lifecycle_status'] == 'recorded'
                  and row['effective_revision_id'] == row['latest_revision_id'] for row in rows)
          and all(row['payload']['subject_ref'] == {k: v for k, v in _pinned(mission).items() if k != 'ref'}
                  for row in rows))
    at = {label: flow.state('outsider', mission_id, as_of=moment)['snapshot'] for label, moment in (
        ('before', '2026-09-21T00:00:00Z'), ('between', '2026-09-23T00:00:00Z'), ('exactly', '2026-09-23T10:00:00.5Z'),
        ('after', '2026-09-24T00:00:00Z'))}
    latest = flow.state('outsider', mission_id)['snapshot']
    check('get_state_returns_the_latest_snapshot_not_later_than_the_given_time',
          at['before'] is None and at['between']['object_id'] == first['object_id']
          and at['exactly']['object_id'] == second['object_id'] and at['after']['object_id'] == second['object_id']
          and latest['object_id'] == second['object_id'] and at['between']['unconfirmed'] is True
          and at['between']['attributes']['as_of'] == '2026-09-22T09:37:00Z'
          and at['between']['attributes']['subject_ref'] == _pinned(mission))
    view = flow.read('outsider', mission_id)
    blocks = {b['id']: b for b in view['state']['blocks']}
    check('get_object_carries_the_latest_snapshot_and_every_snapshot_read_is_marked_unconfirmed',
          view['state']['object_id'] == second['object_id'] and view['state']['unconfirmed'] is True
          and blocks['progress']['text'] == '隔离库已迁移。' and blocks['issue']['text'] == '当前没有问题'
          and flow.read('outsider', first['object_id'])['unconfirmed'] is True and 'state' not in view['state'])
    refreshed = flow.rows("SELECT subject_refs FROM gov_world_events WHERE scope_id=%s AND kind='state.refreshed'"
                          " AND subject_refs->0->>'object_id' = %s", (f['scope_id'], first['object_id']))
    check('a_snapshot_write_records_one_state_refreshed_event_naming_the_snapshot_then_its_subject',
          [row['subject_refs'] for row in refreshed] == [[
              {'object_id': first['object_id'], 'object_version': 1, 'revision_id': first['revision_id'], 'block': None},
              {k: v for k, v in _pinned(mission).items() if k != 'ref'}]])
    same_instant = refresh_body({'title': 'x', 'subject_ref': _ref(mission), 'as_of': '2026-09-22T09:37:00.000000Z'})
    flow.deny('a', same_instant, codes={'INVALID_STATE'}, prepare=False, says='already has a snapshot at this time')
    check('a_second_snapshot_of_the_same_subject_at_the_same_instant_is_refused')
    body = flow.prepare('a', refresh_body({'title': '9/23 晚', 'subject_ref': _ref(task), 'as_of': '2026-09-23T20:00:00Z'}))
    receipt, replay = flow.commit('a', body), flow.commit('a', deepcopy(body))
    changed = deepcopy(body)
    changed['params']['payload']['as_of'] = '2026-09-23T21:00:00Z'
    flow.deny('a', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    check('a_replayed_snapshot_write_returns_its_receipt_and_a_reused_key_for_another_snapshot_is_refused',
          replay['receipt_id'] == receipt['receipt_id']
          and flow.state('outsider', task['object_id'])['snapshot']['object_id'] == receipt['result']['object_id'])

    # ------------------------------------------------------------ external events
    meeting = flow.record('a', {'category': 'meeting', 'subject_refs': [_ref(mission), _ref(task)],
                                'occurred_at': '2026-09-22T17:37:00+08:00',
                                'content': {'text': '9/22 17:37 会议：先把数据环境准备好。',
                                            'artifacts': ['https://example.test/minutes-0922']}})['result']
    original = flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s AND event_id=%s', (f['scope_id'], meeting['event_id']))
    check('an_external_event_that_happened_earlier_is_recorded_at_its_own_occurred_at',
          len(original) == 1 and original[0]['kind'] == 'event.recorded' and original[0]['category'] == 'meeting'
          and original[0]['occurred_at'].isoformat() == '2026-09-22T09:37:00+00:00'
          and original[0]['occurred_at'] < original[0]['recorded_at']
          and [r['object_id'] for r in original[0]['subject_refs']] == [mission_id, task['object_id']])
    review = flow.record('b', {'category': 'review', 'subject_refs': [_ref(mission)], 'occurred_at': '2026-09-23T09:00:00Z',
                               'content': {'text': '交付单元评审了 E&O 的 Mission。'}})['result']
    # outsider 所在域的策略不列外部事件，他在 E&O 域也没有角色：记得成，说明判权只看 scope 内有没有生效指派。
    observed = flow.record('outsider', {'category': 'other', 'subject_refs': [_ref(mission)],
                                        'occurred_at': '2026-09-23T09:30:00Z', 'content': {'text': '旁听记录。'}})['result']
    check('events_are_recorded_by_scope_permission_regardless_of_any_domain_policy',
          review['event_id'] != meeting['event_id'] and observed['occurred_at'] == '2026-09-23T09:30:00Z')
    by_agent = flow.record('agent_a', {'category': 'other', 'subject_refs': [_ref(mission)],
                                       'occurred_at': '2026-09-23T11:00:00Z', 'content': {'text': 'Agent 整理了会议纪要。'},
                                       'declaration': declaration})['result']
    check('a_unit_agent_records_an_event_with_its_declaration', by_agent['declaration']['scene'] == _pinned(mission))
    correction = flow.record('ceo', {'category': 'correction', 'supersedes_event_id': meeting['event_id'],
                                     'subject_refs': [_ref(mission)], 'occurred_at': '2026-09-22T17:37:00+08:00',
                                     'content': {'text': '更正：会上决定的是先建隔离库。'}})['result']
    after = flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s AND event_id=%s', (f['scope_id'], meeting['event_id']))
    check('a_correction_references_the_original_which_stays_unchanged', after == original)

    listed = flow.events('outsider', mission_id)['events']
    by_id = {e['event_id']: e for e in listed}
    moments = [datetime.fromisoformat(e['occurred_at'].replace('Z', '+00:00')) for e in listed]
    check('get_events_lists_every_event_about_the_object_in_occurred_at_order_and_marks_corrections',
          {meeting['event_id'], review['event_id'], by_agent['event_id'], correction['event_id']} <= set(by_id)
          and {'object.created', 'relate', 'state.refreshed', 'event.recorded'} <= {e['kind'] for e in listed}
          and moments == sorted(moments)
          and by_id[meeting['event_id']]['corrected_by'] == [correction['event_id']]
          and by_id[correction['event_id']]['supersedes_event_id'] == meeting['event_id']
          and by_id[meeting['event_id']]['content']['artifacts'] == ['https://example.test/minutes-0922']
          and by_id[meeting['event_id']]['subject_refs'][0] == _pinned(mission)
          and by_id[meeting['event_id']]['occurred_at'] == '2026-09-22T09:37:00Z'
          and all(e['occurred_at'].endswith('Z') and e['recorded_at'].endswith('Z') for e in listed))
    since = flow.events('outsider', mission_id, since='2026-09-23T08:00:00+08:00')
    start = datetime(2026, 9, 23, tzinfo=timezone.utc)
    check('get_events_starts_at_the_given_time',
          since['since'] == '2026-09-23T00:00:00Z'
          and {review['event_id'], by_agent['event_id']} <= {e['event_id'] for e in since['events']}
          and not {meeting['event_id'], correction['event_id']} & {e['event_id'] for e in since['events']}
          and all(datetime.fromisoformat(e['occurred_at'].replace('Z', '+00:00')) >= start for e in since['events']))
    flow.events('foreign_ceo', mission_id, expected=404)
    flow.state('foreign_ceo', mission_id, expected=404)
    check('an_identity_from_another_scope_reads_neither_events_nor_state')

    # ------------------------------------------------------------ rejections
    event = {'category': 'meeting', 'subject_refs': [_ref(mission)], 'occurred_at': '2026-09-23T12:00:00Z',
             'content': {'text': 'x'}}
    flow.deny('a', flow.command('world_record_event', {**event, 'subject_refs': []}), codes={'INVALID_REQUEST'})
    check('an_event_without_subjects_is_refused')
    body = flow.prepare('lapsed', flow.command('world_record_event', event))
    receipt, replay = flow.commit('lapsed', body), flow.commit('lapsed', deepcopy(body))
    changed = deepcopy(body)
    changed['params']['content'] = {'text': 'Another event under the same key'}
    flow.deny('lapsed', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    check('a_replayed_event_returns_its_receipt_and_a_reused_key_for_another_event_is_refused',
          replay['receipt_id'] == receipt['receipt_id'])
    # 最后一条指派撤掉后，这名身份在 scope 内已没有生效指派：不能再记事件、不能把原命令重放成成功、也读不到。
    revoke_assignment(h.env, f, f['actors']['lapsed']['assignment_id'])
    flow.deny('lapsed', flow.command('world_record_event', event), codes={'FORBIDDEN', 'UNAUTHENTICATED'})
    flow.deny('lapsed', deepcopy(body), codes={'FORBIDDEN', 'UNAUTHENTICATED'}, prepare=False)
    flow.events('lapsed', mission_id, expected={401, 403})
    flow.state('lapsed', mission_id, expected={401, 403})
    check('an_identity_without_a_current_assignment_in_the_scope_can_neither_record_replay_nor_read')
    flow.deny('agent_a', flow.command('world_record_event', event), codes={'INVALID_REQUEST'}, says='must declare its scene')
    snapshot = {'title': 'x', 'subject_ref': _ref(task), 'as_of': '2026-09-23T12:00:00Z'}
    flow.deny('agent_a', refresh_body(snapshot), codes={'INVALID_REQUEST'}, says='must declare its scene')
    check('an_agent_event_or_snapshot_without_a_declaration_is_refused')
    flow.deny('ic_a', refresh_body(snapshot), codes={'FORBIDDEN'}, says='Only a responsible person up the spine')
    check('a_person_not_responsible_up_the_spine_cannot_write_a_snapshot')
    flow.deny('agent', refresh_body(snapshot, declaration), codes={'FORBIDDEN'}, says='AGENT role')
    check('an_agent_without_the_agent_role_in_the_unit_cannot_write_a_snapshot')
    flow.deny('a', refresh_body({**snapshot, 'subject_ref': _ref({**first, 'version': 1})}), codes={'INVALID_REQUEST'},
              says='A state snapshot is not the subject of a snapshot')
    check('a_snapshot_cannot_be_the_subject_of_a_snapshot')
    flow.deny('a', flow.command('world_record_event', {**event, 'occurred_at': '2099-01-01T00:00:00Z'}),
              codes={'INVALID_REQUEST'}, says='has not happened yet')
    flow.deny('a', refresh_body({**snapshot, 'as_of': '2099-01-01T00:00:00Z'}), codes={'INVALID_REQUEST'},
              says='has not come yet')
    check('an_event_or_snapshot_dated_in_the_future_is_refused')
    refreshed_id = str(flow.rows("SELECT event_id FROM gov_world_events WHERE scope_id=%s AND kind='state.refreshed' LIMIT 1",
                                 (f['scope_id'],))[0]['event_id'])
    flow.deny('ceo', flow.command('world_record_event', {**event, 'category': 'correction',
                                                         'supersedes_event_id': refreshed_id}),
              codes={'INVALID_REQUEST'}, says='corrects an external event')
    check('a_correction_can_only_correct_an_external_event')
    return {'checks': checks}


def assign_and_lifecycle(h, f, flow, made):
    """票 #23：逐级指派留痕，生命周期由事件推导（段与推出它的事件 id），按 responsible 属性的责任人获得权限。"""
    checks = []
    check = _checker(checks)
    actors = f['actors']
    person = {name: actors[name]['principal_id'] for name in ('a', 'owner_a', 'ic_a', 'agent_a')}
    unit, mission, task, activity = made['ResponsibilityUnit'], made['Mission'], made['Task'], made['Activity']

    def lifecycle(obj):
        return flow.read('outsider', obj['object_id'])['lifecycle']

    def event_of(receipt):
        return str(flow.rows('SELECT event_id FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                             (f['scope_id'], receipt['receipt_id']))[0]['event_id'])

    created = {row['object_id']: str(row['event_id']) for row in flow.rows(
        "SELECT subject_refs->0->>'object_id' AS object_id, event_id FROM gov_world_events"
        " WHERE scope_id=%s AND kind='object.created'", (f['scope_id'],))}
    check('types_without_a_lifecycle_read_none_and_gated_objects_start_as_drafts_produced_by_creation',
          lifecycle(unit) is None and lifecycle(made['Strategy']) is None and lifecycle(made['Company']) is None
          and lifecycle(made['PeriodGoal']) == {'status': 'draft', 'display_name': '草稿',
                                                'event_id': created[made['PeriodGoal']['object_id']]}
          and lifecycle(mission)['status'] == 'draft')

    # ------------------------------------------------------------ assignment, one level at a time
    unit_before = flow.read('outsider', unit['object_id'])
    dri = flow.assign('ceo', unit['object_id'], person['a'])
    unit_after = flow.read('outsider', unit['object_id'])
    check('the_ceo_assigns_the_unit_dri_as_a_record_without_a_new_version',
          dri['result']['assignee'] == person['a'] and unit_after['version'] == unit_before['version']
          and unit_after['revision_id'] == unit_before['revision_id']
          and [e['assignee'] for e in flow.events('outsider', unit['object_id'])['events'] if e['kind'] == 'assign']
          == [person['a']])
    before = flow.read('outsider', mission['object_id'])['version']
    flow.assign('a', mission['object_id'], person['owner_a'])
    view = flow.read('outsider', mission['object_id'])
    check('the_dri_assigns_the_mission_owner_as_a_new_version_without_moving_its_lifecycle',
          view['attributes']['responsible'] == person['owner_a'] and view['version'] == before + 1
          and view['lifecycle']['status'] == 'draft')
    # 这条 Task 在 #22 已有快照：指派之前的快照不算，指派后是「已指派」。
    given = flow.assign('owner_a', task['object_id'], person['ic_a'])
    check('the_owner_assigns_the_task_and_snapshots_written_before_assignment_do_not_count',
          lifecycle(task) == {'status': 'assigned', 'display_name': '已指派', 'event_id': event_of(given)}
          and flow.read('outsider', task['object_id'])['attributes']['responsible'] == person['ic_a'])

    # ------------------------------------------------------------ an Activity from assignment to closure
    declaration = {'scene': _ref(task), 'trigger': 'Activity 被指派即触发执行',
                   'human_acceptance': {'required': True, 'acceptor': person['ic_a']}}
    # 每一步之后立即取对象，核对所处的段与推出它的事件。
    steps = [('assigned', lambda: flow.assign('owner_a', activity['object_id'], person['agent_a'])),
             ('in_progress', lambda: flow.refresh('agent_a', {
                 'title': '执行中', 'subject_ref': _ref(activity), 'as_of': '2026-09-24T01:00:00Z',
                 'blocks': {'progress': {'text': '隔离库迁移完成一半。'}}}, declaration)),
             ('delivered', lambda: flow.record('agent_a', {
                 'category': 'delivery', 'subject_refs': [_ref(activity)], 'occurred_at': '2026-09-24T02:00:00Z',
                 'content': {'text': '隔离库迁移与播种完成。'}, 'declaration': declaration}))]
    seen = []
    for expected, act in steps:
        receipt = act()
        state = lifecycle(activity)
        seen.append(state['status'] == expected and state['event_id'] == event_of(receipt))
    flow.record('owner_a', {'category': 'acceptance', 'subject_refs': [_ref(activity)],
                            'occurred_at': '2026-09-24T03:00:00Z', 'content': {'text': 'Owner 看过了。'}})
    held = lifecycle(activity)
    closing = flow.record('ic_a', {'category': 'acceptance', 'subject_refs': [_ref(activity)],
                                   'occurred_at': '2026-09-24T03:30:00Z', 'content': {'text': 'Task 责任人验收通过。'}})
    check('an_activity_moves_assigned_in_progress_delivered_with_each_step_naming_its_event', all(seen))
    check('an_acceptance_by_someone_other_than_the_task_responsible_does_not_close_the_activity',
          held['status'] == 'delivered' and held['event_id'] == event_of(receipt))
    check('the_task_responsible_closes_the_activity_by_acceptance',
          lifecycle(activity) == {'status': 'closed', 'display_name': '已关闭', 'event_id': event_of(closing)})

    # ------------------------------------------------------------ authority through the responsible attribute
    owner_body = flow.prepare('owner_a', flow.command('world_create_object', flow.create_params(
        'Task', 'a', {'title': 'Owner 拆出的 Task', 'parent_ref': _ref(mission)})))
    by_owner = flow.commit('owner_a', owner_body)
    ic_body = flow.prepare('ic_a', flow.targeted('world_revise_object', task['object_id'], {
        'payload': {'blocks': {'constraint': {'text': '只用隔离库。'}}}}))
    by_ic = flow.commit('ic_a', ic_body)
    revised = flow.revise('agent_a', activity['object_id'], {'blocks': {'instruction': {'text': '补齐播种脚本。'}}},
                          declaration)
    check('the_owner_creates_under_the_mission_the_task_responsible_revises_the_task_and_the_activity_agent_revises_it',
          by_owner['result']['version'] == 1 and revised['result']['object_id'] == activity['object_id']
          and by_ic['result']['responsible_through'] == task['object_id']
          and by_owner['result']['responsible_through'] == mission['object_id'])
    owner_snapshot = flow.refresh('owner_a', {'title': 'Owner 周报', 'subject_ref': _ref(mission),
                                              'as_of': '2026-09-24T04:00:00Z',
                                              'blocks': {'progress': {'text': '两条 Task 在推进。'}}})
    flow.relate('owner_a', mission['object_id'], 'depends_on', [])
    task_run = flow.refresh('ic_a', {'title': 'Task 进展', 'subject_ref': _ref(task), 'as_of': '2026-09-24T04:30:00Z',
                                      'blocks': {'progress': {'text': '数据环境可用。'}}})
    in_progress = lifecycle(task)
    task_done = flow.record('ic_a', {'category': 'delivery', 'subject_refs': [_ref(task)],
                                     'occurred_at': '2026-09-24T05:00:00Z', 'content': {'text': '数据环境交付。'}})
    check('the_owner_writes_a_snapshot_and_relates_the_mission_and_the_task_responsible_moves_the_task_to_delivered',
          owner_snapshot['result']['responsible_through'] == mission['object_id']
          and in_progress == {'status': 'in_progress', 'display_name': '进行中', 'event_id': event_of(task_run)}
          and lifecycle(task) == {'status': 'delivered', 'display_name': '已交付', 'event_id': task_done['result']['event_id']})

    # ------------------------------------------------------------ rejections
    def deny_assign(actor, obj, assignee, codes, says=None):
        flow.deny(actor, flow.targeted('world_assign', obj['object_id'], {'principal_id': assignee}), codes=codes, says=says)

    one_level_up, wrong_role = 'one level up', 'does not hold the role'
    deny_assign('a', task, person['a'], {'FORBIDDEN'}, one_level_up)
    check('a_dri_cannot_skip_the_owner_to_assign_a_task')
    deny_assign('ceo', activity, person['ic_a'], {'FORBIDDEN'}, one_level_up)
    check('the_ceo_cannot_skip_levels_to_assign_an_activity')
    deny_assign('ic_a', activity, person['ic_a'], {'FORBIDDEN'}, one_level_up)
    check('a_task_responsible_does_not_assign_activities')
    deny_assign('a', mission, person['ic_a'], {'INVALID_REQUEST'}, wrong_role)
    check('a_mission_owner_must_hold_the_owner_role')
    deny_assign('owner_a', task, person['agent_a'], {'INVALID_REQUEST'}, wrong_role)
    check('a_task_responsible_must_be_a_person')
    deny_assign('owner_a', task, '00000000-0000-4000-8000-000000000000', {'INVALID_REQUEST'}, wrong_role)
    check('an_unknown_assignee_is_refused')
    deny_assign('ceo', made['PeriodGoal'], person['a'], {'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'})
    check('a_goal_is_not_assigned')

    # 改派 Task：原责任人不能再把他凭 responsible 做过的修订重放成成功。指派本身的重放与版本冲突。
    body = flow.prepare('owner_a', flow.targeted('world_assign', task['object_id'], {'principal_id': person['a']}))
    reassigned = flow.commit('owner_a', body)
    replay = flow.commit('owner_a', deepcopy(body))
    flow.deny('ic_a', deepcopy(ic_body), codes={'FORBIDDEN'}, prepare=False, says='has moved to someone else')
    check('a_former_task_responsible_cannot_replay_a_revision_after_the_task_is_reassigned',
          flow.read('outsider', task['object_id'])['attributes']['responsible'] == person['a']
          and lifecycle(task)['status'] == 'delivered')
    changed = deepcopy(body)
    changed['params']['principal_id'] = person['ic_a']
    flow.deny('owner_a', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    stale = flow.targeted('world_assign', task['object_id'], {'principal_id': person['ic_a']}, body['target'])
    flow.deny('owner_a', stale, codes={'VERSION_CONFLICT', 'STALE_DEPENDENCY'}, prepare=False)
    check('a_replayed_assignment_returns_its_receipt_a_reused_key_is_refused_and_a_stale_version_conflicts',
          replay['receipt_id'] == reassigned['receipt_id'])

    # 最后撤掉 Owner 的 OWNER 角色（他在该域还有 IC 角色）：Mission 上的 responsible 还在，但他不再是责任人，
    # 不能再建 Task，原来建 Task 的命令也不能重放成成功。
    revoke_assignment(h.env, f, actors['owner_a']['assignment_id'])
    flow.deny('owner_a', flow.command('world_create_object', flow.create_params(
        'Task', 'a', {'title': 'x', 'parent_ref': _ref(mission)})), codes={'FORBIDDEN'},
        says='Only a responsible person up the spine')
    flow.deny('owner_a', deepcopy(owner_body), codes={'FORBIDDEN'}, prepare=False)
    check('an_owner_who_lost_the_owner_role_can_neither_create_nor_replay_a_creation')
    late = flow.record('owner_a', {'category': 'acceptance', 'subject_refs': [_ref(task)],
                                   'occurred_at': '2026-09-24T06:00:00Z', 'content': {'text': '验收（已无 OWNER 角色）。'}})
    check('an_acceptance_by_an_owner_without_the_owner_role_does_not_close_the_task',
          late['result']['accepted_as_parent_responsible'] == [] and lifecycle(task)['status'] == 'delivered')
    return {'checks': checks}


def activation_policy_control(h, source, f, installed):
    """票 #24：激活策略经控制面 install-activation-policy 安装；失败的安装整体回滚，什么都不留。"""
    checks = []
    check = _checker(checks)
    scope = f['scope_id']

    def control_state():
        return (owner_rows(h.env, scope, 'SELECT * FROM gov_activation_policies WHERE scope_id=%s ORDER BY domain_id, policy_seq',
                           (scope,)),
                owner_rows(h.env, scope, 'SELECT * FROM gov_protocol_control_events WHERE scope_id=%s ORDER BY recorded_at, event_id',
                           (scope,)),
                owner_rows(h.env, scope, 'SELECT auth_epoch FROM gov_scopes WHERE scope_id=%s', (scope,)))

    policies, events, _ = control_state()
    latest = {}
    for row in policies:
        latest[str(row['domain_id'])] = row
    installs = [e for e in events if e['event_type'] == 'install_activation_policy']
    check('each_domain_gets_its_activation_policy_from_the_control_plane_with_one_control_event_each',
          sorted(e['detail']['domain_id'] for e in installs) == sorted(f['domains'].values())
          and all(latest[f['domains'][name]]['policy_seq'] == response['policy_seq']
                  and str(latest[f['domains'][name]]['policy_id']) == response['policy_id']
                  for name, response in installed.items()))
    check('the_installed_policies_carry_the_gate_roles_of_the_registry',
          all(latest[domain]['content']['action_roles'][action] == roles
              for domain in f['domains'].values() for action, roles in GATE_ROLES.items()))

    adapter = ControlAdapter(h, source, 'memory_service_runtime.governed.control')
    good = activation_policy(h.env, scope, f['domains']['c'], 'c')
    before = control_state()
    for label, content, domain, code in (
            ('an_unknown_role', {**good, 'action_roles': {**good['action_roles'], 'world_confirm_mission': ['CTO']}},
             f['domains']['c'], 'INVALID_REQUEST'),
            ('an_empty_role_list', {**good, 'action_roles': {**good['action_roles'], 'world_assign': []}},
             f['domains']['c'], 'INVALID_REQUEST'),
            ('a_domain_of_another_scope', good, f['foreign_domains']['c'], 'DOMAIN_NOT_FOUND'),
            # 授权纪元已经推进、写策略时才被数据库拒绝（jsonb 不收 \u0000）：纪元也要一起回滚。
            ('content_the_database_refuses_after_the_epoch_moved', {**good, 'notes': 'nul \u0000 byte'},
             f['domains']['c'], 'CONTROL_PLANE_UNAVAILABLE'),
    ):
        tag = uid()[:8]
        path = h.private / f'activation-bad-{tag}.json'
        private_json(path, content)
        adapter.cli('world-activation-bad-' + tag, [
            'install-activation-policy', '--scope-id', scope, '--domain-id', domain, '--content-json', str(path),
            '--reason', 'Synthetic refusal'], expected_exit=2, expected_error_code=code)
        check(f'an_activation_policy_with_{label}_is_refused_and_nothing_is_left_behind', control_state() == before)
    return checks


def gates(h, f, flow, made):
    """票 #24：承诺与确认的门——完整 Mission 场景、退回与撤回、目标的门、重走写回、每个门动作的拒绝用例。"""
    checks = []
    check = _checker(checks)
    strategy = made['Strategy']

    def view(obj):
        return flow.read('outsider', obj['object_id'])

    def event_of(receipt):
        return str(flow.rows('SELECT event_id FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                             (f['scope_id'], receipt['receipt_id']))[0]['event_id'])

    def at(obj, status, receipt, *, formal=None):
        """所处的段由这条回执的事件推出；formal 给出时核对正式内容指针（'latest' 表示指向最新修订）。"""
        seen = view(obj)
        ok = seen['lifecycle']['status'] == status and seen['lifecycle']['event_id'] == event_of(receipt)
        if formal is not None:
            status_, pointer = formal
            ok = ok and seen['formal'] == {'lifecycle_status': status_, 'effective_revision_id':
                                           seen['revision_id'] if pointer == 'latest' else pointer}
        return ok

    def gate(actor, kind, obj, **params):
        return flow.gate(actor, kind, obj['object_id'], params)

    def deny_gate(actor, kind, obj, codes, says=None, target=None, prepare=True, **params):
        flow.deny(actor, flow.targeted(kind, obj['object_id'], params, target), codes=codes, says=says, prepare=prepare)

    def wrong_version(obj):
        seen = view(obj)
        return {'object_id': obj['object_id'], 'revision_id': seen['revision_id'], 'expected_version': seen['object_version'] + 1}

    # ------------------------------------------------------------ a unit with its goals and a Mission
    unit = flow.create('ceo', 'ResponsibilityUnit', 'c', {
        'title': '增长', 'unit_kind': 'battlefield', 'architecture_ref': _ref(strategy, 'responsibility_structure')})['result']
    goal = flow.create('c', 'LongTermGoal', 'c', {'title': '增长年度目标', 'scope': 'unit', 'horizon': '2027 年底',
                                                  'parent_ref': _ref(unit)})['result']
    period = flow.create('c', 'PeriodGoal', 'c', {'title': '10 月', 'period': '2026-10', 'goal_ref': _ref(goal)})['result']
    mission = flow.create('c', 'Mission', 'c', {'title': '拿下首批三家客户', 'goal_ref': _ref(period),
                                                'blocks': {'play': {'text': '先打 E&O 老客户。'}}})['result']
    flow.assign('c', mission['object_id'], f['actors']['owner_c']['principal_id'])

    # ------------------------------------------------------------ the Mission, from commitment to closure
    promise = gate('owner_c', 'world_commit_mission', mission, phase='initiation')
    check('the_owner_commits_the_initiation_and_the_content_is_not_formal_yet',
          at(mission, 'committed', promise, formal=('draft', None)))
    established = gate('c', 'world_confirm_mission', mission, phase='initiation', outcome='accepted')
    check('the_dri_accepts_the_initiation_and_the_confirmed_revision_becomes_the_formal_content',
          at(mission, 'established', established, formal=('confirmed', 'latest'))
          and established['result']['revision_id'] == view(mission)['formal']['effective_revision_id'])
    mark = gate('ceo', 'world_mark_core_battle', mission)
    marked = view(mission)
    check('the_ceo_marks_the_established_mission_a_core_battle_which_now_awaits_the_ceo',
          at(mission, 'awaiting_ceo', mark, formal=('confirmed', 'latest'))
          and marked['attributes']['core_battle'] is True and mark['result']['version'] == marked['version'])
    first_ceo = gate('ceo', 'world_confirm_mission_core_battle', mission, phase='initiation', outcome='accepted')
    pointer = view(mission)['formal']
    undone = gate('ceo', 'world_confirm_mission_core_battle', mission, phase='initiation', outcome='withdrawn',
                  supersedes_event_id=event_of(first_ceo))
    check('withdrawing_a_confirmation_that_did_not_make_the_content_formal_leaves_the_formal_pointer',
          at(mission, 'awaiting_ceo', undone, formal=('confirmed', pointer['effective_revision_id'])))
    by_ceo = gate('ceo', 'world_confirm_mission_core_battle', mission, phase='initiation', outcome='accepted')
    check('the_ceo_confirms_the_core_battle_initiation', at(mission, 'established', by_ceo, formal=('confirmed', 'latest')))
    running = flow.refresh('owner_c', {'title': '首周', 'subject_ref': _ref(mission), 'as_of': '2026-09-24T06:00:00Z',
                                       'blocks': {'progress': {'text': '约到两家。'}}})
    check('the_first_snapshot_puts_it_in_progress', at(mission, 'in_progress', running))

    # 已成立后直接修订被拒；重走立项门：Owner 承诺带候选，DRI 接受，核心战役还要 CEO 接受，写回发生在 CEO 接受时。
    formal_before = view(mission)
    flow.deny('owner_c', flow.targeted('world_revise_object', mission['object_id'], {
        'payload': {'blocks': {'play': {'text': '改打新客户。'}}}}), codes={'INVALID_STATE'}, says='only while it is a draft')
    check('an_established_mission_is_not_revised_directly')
    rerun = gate('owner_c', 'world_commit_mission', mission, phase='initiation',
                 payload={'blocks': {'play': {'text': '改打新客户。'}}}, content={'text': '打法调整，见草稿。',
                                                                         'artifacts': ['https://example.test/play-v2']})
    dri_ok = gate('c', 'world_confirm_mission', mission, phase='initiation', outcome='accepted')
    pending = view(mission)
    check('a_rerun_commitment_carries_its_candidate_and_nothing_formal_changes_until_the_last_confirmation',
          rerun['result']['candidate']['blocks']['play']['text'] == '改打新客户。'
          and pending['version'] == formal_before['version'] and pending['formal'] == formal_before['formal']
          and {b['id']: b['text'] for b in pending['blocks']}['play'] == '先打 E&O 老客户。'
          and at(mission, 'in_progress', running))
    written = gate('ceo', 'world_confirm_mission_core_battle', mission, phase='initiation', outcome='accepted')
    after = view(mission)
    confirm_event = flow.rows('SELECT subject_refs FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                              (f['scope_id'], written['receipt_id']))[0]
    revised_events = [e for e in flow.events('outsider', mission['object_id'])['events'] if e['kind'] == 'object.revised']
    check('the_ceo_acceptance_writes_the_candidate_back_as_the_new_formal_version_without_moving_the_lifecycle',
          revised_events == [] and
          after['version'] == formal_before['version'] + 1 and written['result']['revision_id'] == after['revision_id']
          and {b['id']: b['text'] for b in after['blocks']}['play'] == '改打新客户。'
          and after['attributes']['core_battle'] is True and after['attributes']['responsible'] == f['actors']['owner_c']['principal_id']
          and after['formal'] == {'lifecycle_status': 'confirmed', 'effective_revision_id': after['revision_id']}
          and confirm_event['subject_refs'][0]['revision_id'] == after['revision_id']
          and at(mission, 'in_progress', running) and dri_ok['result']['revision_id'] == formal_before['revision_id'])

    delivered = gate('owner_c', 'world_commit_mission', mission, phase='delivery')
    check('the_owner_commits_the_delivery', at(mission, 'delivered', delivered, formal=('confirmed', 'latest')))
    returned = gate('c', 'world_confirm_mission', mission, phase='delivery', outcome='returned',
                    content={'text': '第三家客户还没签。'})
    check('the_dri_returns_the_delivery_for_adjustment', at(mission, 'adjusting', returned, formal=('confirmed', 'latest')))
    again = gate('owner_c', 'world_commit_mission', mission, phase='delivery')
    check('the_owner_commits_the_delivery_again_from_adjustment', at(mission, 'delivered', again, formal=('confirmed', 'latest')))
    closed = gate('c', 'world_confirm_mission', mission, phase='delivery', outcome='accepted')
    listed = [e for e in flow.events('outsider', mission['object_id'])['events'] if e['kind'] in {'commit', 'confirm'}]
    check('the_dri_closes_the_delivery_and_every_gate_event_is_listed_with_its_action_phase_and_outcome',
          at(mission, 'closed', closed, formal=('confirmed', 'latest'))
          and [(e['action'], e['phase'], e['outcome']) for e in listed] == [
              ('world_commit_mission', 'initiation', None), ('world_confirm_mission', 'initiation', 'accepted'),
              ('world_confirm_mission_core_battle', 'initiation', 'accepted'),
              ('world_confirm_mission_core_battle', 'initiation', 'withdrawn'),
              ('world_confirm_mission_core_battle', 'initiation', 'accepted'),
              ('world_commit_mission', 'initiation', None), ('world_confirm_mission', 'initiation', 'accepted'),
              ('world_confirm_mission_core_battle', 'initiation', 'accepted'),
              ('world_commit_mission', 'delivery', None), ('world_confirm_mission', 'delivery', 'returned'),
              ('world_commit_mission', 'delivery', None), ('world_confirm_mission', 'delivery', 'accepted')]
          and [e for e in listed if e['outcome'] == 'returned'][0]['content']['text'] == '第三家客户还没签。')

    # ------------------------------------------------------------ the goals' gates, return and withdrawal
    ceo_back = gate('ceo', 'world_confirm_long_term_goal', goal, outcome='returned', content={'text': '衡量口径再具体一点。'})
    check('a_returned_long_term_goal_stays_a_draft_with_the_return_on_record',
          view(goal)['lifecycle']['status'] == 'draft' and ceo_back['result']['event_id'] == event_of(ceo_back))
    flow.revise('c', goal['object_id'], {'blocks': {'measures': {'text': '签约额与续约率。'}}})
    goal_ok = gate('ceo', 'world_confirm_long_term_goal', goal, outcome='accepted')
    check('the_ceo_confirms_the_revised_long_term_goal', at(goal, 'confirmed', goal_ok, formal=('confirmed', 'latest')))
    goal_back = gate('ceo', 'world_confirm_long_term_goal', goal, outcome='withdrawn',
                     supersedes_event_id=event_of(goal_ok))
    check('withdrawing_the_confirmation_takes_the_formal_content_back_with_the_lifecycle',
          at(goal, 'draft', goal_back, formal=('draft', None)))
    goal_again = gate('ceo', 'world_confirm_long_term_goal', goal, outcome='accepted')
    rewrite = gate('ceo', 'world_confirm_long_term_goal', goal, outcome='accepted',
                   payload={'blocks': {'outcome': {'text': '年底签约额翻倍。'}}})
    check('a_confirmed_long_term_goal_is_changed_by_a_ceo_confirmation_that_carries_the_new_content',
          at(goal, 'confirmed', goal_again, formal=('confirmed', 'latest'))
          and {b['id']: b['text'] for b in view(goal)['blocks']}['outcome'] == '年底签约额翻倍。'
          and rewrite['result']['version'] == view(goal)['version'])

    pg_promise = gate('c', 'world_commit_period_goal', period)
    pg_withdrawn = gate('c', 'world_commit_period_goal', period, outcome='withdrawn',
                        supersedes_event_id=event_of(pg_promise))
    kinds = [(e['event_id'], e['outcome']) for e in flow.events('outsider', period['object_id'])['events'] if e['kind'] == 'commit']
    check('a_mistaken_commitment_is_withdrawn_by_the_same_role_and_the_original_event_stays',
          at(period, 'draft', pg_withdrawn) and kinds == [(event_of(pg_promise), None), (event_of(pg_withdrawn), 'withdrawn')])
    gate('c', 'world_commit_period_goal', period)
    pg_back = gate('ceo', 'world_confirm_period_goal', period, outcome='returned')
    flow.revise('c', period['object_id'], {'blocks': {'acceptance': {'text': '三家签约。'}}})
    gate('c', 'world_commit_period_goal', period)
    pg_ok = gate('ceo', 'world_confirm_period_goal', period, outcome='accepted')
    check('a_period_goal_is_returned_revised_as_a_draft_committed_again_and_confirmed',
          view(period)['lifecycle']['status'] == 'confirmed' and at(period, 'confirmed', pg_ok, formal=('confirmed', 'latest'))
          and pg_back['result']['event_id'] == event_of(pg_back))
    flow.deny('c', flow.targeted('world_revise_object', period['object_id'], {'payload': {'title': '10 月（改）'}}),
              codes={'INVALID_STATE'}, says='only while it is a draft')
    check('a_confirmed_period_goal_is_not_revised_directly')
    pg_rerun = gate('c', 'world_commit_period_goal', period, payload={'title': '10 月（改）'})
    deny_gate('c', 'world_commit_period_goal', period, {'INVALID_STATE'}, says='already awaits confirmation',
              payload={'title': '又一版'})
    check('a_second_candidate_waits_for_the_first_to_be_confirmed_or_returned',
          view(period)['title'] == '10 月' and pg_rerun['result']['candidate']['title'] == '10 月（改）')
    pg_written = gate('ceo', 'world_confirm_period_goal', period, outcome='accepted')
    check('the_ceo_confirmation_writes_the_period_goal_candidate_back',
          view(period)['title'] == '10 月（改）' and at(period, 'confirmed', pg_ok, formal=('confirmed', 'latest'))
          and pg_written['result']['revision_id'] == view(period)['revision_id'])

    # ------------------------------------------------------------ replay and key reuse
    body = flow.prepare('c', flow.targeted('world_commit_period_goal', period['object_id'], {'payload': {'title': '10 月（三）'}}))
    receipt, replay = flow.commit('c', body), flow.commit('c', deepcopy(body))
    changed = deepcopy(body)
    changed['params']['payload'] = {'title': 'Another candidate under the same key'}
    flow.deny('c', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    check('a_replayed_gate_returns_its_receipt_and_a_reused_key_for_another_candidate_is_refused',
          replay['receipt_id'] == receipt['receipt_id'])
    discarded = gate('ceo', 'world_confirm_period_goal', period, outcome='returned')
    deny_gate('ceo', 'world_confirm_period_goal', period, {'INVALID_STATE'}, says='not allowed at this stage',
              outcome='accepted')
    check('a_returned_candidate_is_discarded_and_nothing_is_left_to_confirm',
          view(period)['title'] == '10 月（改）' and at(period, 'confirmed', pg_ok, formal=('confirmed', 'latest'))
          and discarded['result']['revision_id'] == view(period)['revision_id'])

    # ------------------------------------------------------------ a Mission that is not a core battle
    def mission_in_c(title):
        obj = flow.create('c', 'Mission', 'c', {'title': title, 'goal_ref': _ref(period)})['result']
        flow.assign('c', obj['object_id'], f['actors']['owner_c']['principal_id'])
        return obj

    plain = flow.create('c', 'Mission', 'c', {'title': '续约两家老客户', 'goal_ref': _ref(period)})['result']
    flow.assign('c', plain['object_id'], f['actors']['owner_c']['principal_id'])
    gate('owner_c', 'world_commit_mission', plain, phase='initiation')
    gate('c', 'world_confirm_mission', plain, phase='initiation', outcome='accepted')
    started = flow.refresh('owner_c', {'title': '启动', 'subject_ref': _ref(plain), 'as_of': '2026-09-24T06:30:00Z',
                                       'blocks': {'progress': {'text': '已联系。'}}})
    before = view(plain)
    gate('owner_c', 'world_commit_mission', plain, phase='initiation',
         payload={'blocks': {'acceptance': {'text': '两家都续约。'}}})
    plain_written = gate('c', 'world_confirm_mission', plain, phase='initiation', outcome='accepted')
    plain_after = view(plain)
    check('a_mission_that_is_not_a_core_battle_writes_its_candidate_back_when_the_dri_accepts',
          plain_after['version'] == before['version'] + 1 and plain_written['result']['revision_id'] == plain_after['revision_id']
          and {b['id']: b['text'] for b in plain_after['blocks']}['acceptance'] == '两家都续约。'
          and at(plain, 'in_progress', started, formal=('confirmed', 'latest')))

    # 已成立后被标为核心战役：CEO 的一次接受既让它回到已成立，也写回进行中那一轮的候选。
    rerun_marked = mission_in_c('重走中被标记')
    gate('owner_c', 'world_commit_mission', rerun_marked, phase='initiation')
    gate('c', 'world_confirm_mission', rerun_marked, phase='initiation', outcome='accepted')
    gate('owner_c', 'world_commit_mission', rerun_marked, phase='initiation', payload={'title': '重走后的标题'})
    gate('ceo', 'world_mark_core_battle', rerun_marked)
    gate('c', 'world_confirm_mission', rerun_marked, phase='initiation', outcome='accepted')
    both = gate('ceo', 'world_confirm_mission_core_battle', rerun_marked, phase='initiation', outcome='accepted')
    check('one_ceo_acceptance_establishes_a_mission_marked_during_a_rerun_and_writes_its_candidate_back',
          at(rerun_marked, 'established', both, formal=('confirmed', 'latest')) and view(rerun_marked)['title'] == '重走后的标题')

    # 已成立后被标为核心战役、CEO 退回草稿：正式内容一并收回，这条退回不能撤回；草稿修订不碰正式内容指针。
    returned_core = mission_in_c('被 CEO 退回')
    gate('owner_c', 'world_commit_mission', returned_core, phase='initiation')
    gate('c', 'world_confirm_mission', returned_core, phase='initiation', outcome='accepted')
    gate('ceo', 'world_mark_core_battle', returned_core)
    ceo_return = gate('ceo', 'world_confirm_mission_core_battle', returned_core, phase='initiation', outcome='returned')
    taken_back = at(returned_core, 'draft', ceo_return, formal=('draft', None))
    deny_gate('ceo', 'world_confirm_mission_core_battle', returned_core, {'INVALID_STATE'},
              says='took the formal content back', phase='initiation', outcome='withdrawn',
              supersedes_event_id=event_of(ceo_return))
    flow.revise('owner_c', returned_core['object_id'], {'title': '退回后改写'})
    check('a_return_to_draft_takes_the_formal_content_back_and_cannot_be_withdrawn',
          taken_back and at(returned_core, 'draft', ceo_return, formal=('draft', None))
          and view(returned_core)['title'] == '退回后改写')

    # ------------------------------------------------------------ rejections, one set per gate action
    draft_goal = flow.create('c', 'PeriodGoal', 'c', {'title': '11 月', 'period': '2026-11', 'goal_ref': _ref(goal)})['result']
    committed_goal = flow.create('c', 'PeriodGoal', 'c', {'title': '12 月', 'period': '2026-12', 'goal_ref': _ref(goal)})['result']
    gate('c', 'world_commit_period_goal', committed_goal)
    draft_mission, committed_mission, core_mission = (mission_in_c(title) for title in ('草稿', '已承诺', '等 CEO'))
    gate('owner_c', 'world_commit_mission', committed_mission, phase='initiation')
    gate('ceo', 'world_mark_core_battle', core_mission)
    gate('owner_c', 'world_commit_mission', core_mission, phase='initiation')
    gate('c', 'world_confirm_mission', core_mission, phase='initiation', outcome='accepted')
    initiation_ok = {'phase': 'initiation', 'outcome': 'accepted'}
    for kind, actor, obj, params in (
            ('world_commit_period_goal', 'owner_c', draft_goal, {}),
            ('world_commit_mission', 'c', draft_mission, {'phase': 'initiation'}),
            ('world_confirm_long_term_goal', 'c', goal, {'outcome': 'accepted'}),
            ('world_confirm_period_goal', 'c', committed_goal, {'outcome': 'accepted'}),
            ('world_confirm_mission', 'owner_c', committed_mission, initiation_ok),
            ('world_confirm_mission_core_battle', 'c', core_mission, initiation_ok),
            ('world_mark_core_battle', 'c', draft_mission, {}),
    ):
        deny_gate(actor, kind, obj, {'FORBIDDEN'}, **params)
        check(f'{kind}_by_a_role_the_policy_does_not_list_is_refused')
    for kind, actor, obj, params in (
            ('world_commit_period_goal', 'c', committed_goal, {}),
            ('world_commit_mission', 'owner_c', draft_mission, {'phase': 'delivery'}),
            ('world_confirm_long_term_goal', 'ceo', goal, {'outcome': 'returned'}),
            ('world_confirm_period_goal', 'ceo', draft_goal, {'outcome': 'accepted'}),
            ('world_confirm_mission', 'c', draft_mission, initiation_ok),
            ('world_confirm_mission_core_battle', 'ceo', committed_mission, initiation_ok),
            ('world_mark_core_battle', 'ceo', plain, {}),
    ):
        deny_gate(actor, kind, obj, {'INVALID_STATE'}, says='not allowed at this stage', **params)
        check(f'{kind}_in_a_state_the_gate_does_not_allow_is_refused')
    # 期望版本错了的请求本身在当前状态下是合法的：拒绝只来自版本。
    for kind, actor, obj, params in (
            ('world_commit_period_goal', 'c', draft_goal, {}),
            ('world_commit_mission', 'owner_c', draft_mission, {'phase': 'initiation'}),
            ('world_confirm_long_term_goal', 'ceo', goal, {'outcome': 'accepted', 'payload': {'title': 'x'}}),
            ('world_confirm_period_goal', 'ceo', committed_goal, {'outcome': 'accepted'}),
            ('world_confirm_mission', 'c', committed_mission, initiation_ok),
            ('world_confirm_mission_core_battle', 'ceo', core_mission, initiation_ok),
            ('world_mark_core_battle', 'ceo', draft_mission, {}),
    ):
        deny_gate(actor, kind, obj, {'VERSION_CONFLICT', 'STALE_DEPENDENCY'}, target=wrong_version(obj), prepare=False, **params)
        check(f'{kind}_with_a_wrong_expected_version_is_refused')

    deny_gate('other_owner_c', 'world_commit_mission', draft_mission, {'FORBIDDEN'}, says="Only the Mission's Owner",
              phase='initiation')
    check('an_owner_role_alone_does_not_commit_someone_elses_mission')
    deny_gate('agent', 'world_confirm_mission', made['Mission'], {'FORBIDDEN'}, says='signed by a person', **initiation_ok)
    check('an_agent_holding_the_dri_role_cannot_sign_a_gate')
    deny_gate('owner_c', 'world_commit_mission', draft_mission, {'INVALID_REQUEST'}, says='revised directly',
              phase='initiation', payload={'title': 'x'})
    check('a_draft_carries_no_candidate_because_it_is_revised_directly')
    deny_gate('c', 'world_commit_period_goal', period, {'INVALID_REQUEST'}, says='carries the candidate')
    check('a_rerun_commitment_must_carry_its_candidate')
    deny_gate('owner_c', 'world_commit_mission', period, {'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'}, phase='initiation')
    check('a_gate_on_a_type_it_does_not_name_is_refused')
    gate('ceo', 'world_mark_core_battle', draft_mission)
    deny_gate('ceo', 'world_mark_core_battle', draft_mission, {'INVALID_STATE'}, says='already a core battle')
    check('a_mission_is_marked_a_core_battle_once')
    first = gate('owner_c', 'world_commit_mission', draft_mission, phase='initiation')
    deny_gate('c', 'world_confirm_mission', draft_mission, {'INVALID_STATE'}, says='Only the commitment or confirmation',
              phase='initiation', outcome='withdrawn', supersedes_event_id=event_of(first))
    check('a_withdrawal_is_recorded_by_the_same_gate_action_as_the_event_it_withdraws')
    dri_first = gate('c', 'world_confirm_mission', draft_mission, **initiation_ok)
    deny_gate('owner_c', 'world_commit_mission', draft_mission, {'INVALID_STATE'}, says='Only the commitment or confirmation',
              phase='initiation', outcome='withdrawn', supersedes_event_id=event_of(first))
    check('only_the_gate_event_that_produced_the_current_stage_is_withdrawn',
          at(draft_mission, 'awaiting_ceo', dri_first, formal=('draft', None)))
    return {'checks': checks}


def revocation(h, f, flow, company_command):
    """最后撤掉 CEO 在公司域的那条 CEO 指派：CEO 在别的域还有角色，仍是 scope 成员，但原命令不能再被重放成成功。"""
    checks = []
    revoke_assignment(h.env, f, f['actors']['ceo']['assignment_id'])
    flow.deny('ceo', deepcopy(company_command), codes={'FORBIDDEN'}, prepare=False)
    _checker(checks)('a_revoked_ceo_cannot_replay_the_creation_into_a_success')
    return checks


def run(h: MethodHarness, source: Path):
    f = seed_world(h.env, h.private / 'identities.json', 'runtime-acceptance-world-v01')
    installed = install_activation_policies(h, source, f)
    register_world(h, source, f)
    policy_checks = activation_policy_control(h, source, f, installed)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        ctx = company_root(h, f, flow)
        objects = objects_and_refs(h, f, flow, ctx['company'])
        revisions = revise_and_relate(h, f, flow, ctx['company'], objects['made'])
        states = state_and_events(h, f, flow, objects['made'])
        made = {**objects['made'], 'Company': ctx['company']}
        assigned = assign_and_lifecycle(h, f, flow, made)
        gated = gates(h, f, flow, made)
        checks = (policy_checks + ctx['checks'] + objects['checks'] + revisions['checks'] + states['checks']
                  + assigned['checks'] + gated['checks'] + revocation(h, f, flow, ctx['company_command']))
        public_json(h.output / 'summary.json', {
            'world_v01_skeleton_passed': True, 'checks': checks,
            'scope': 'Tickets #19-#24: world wiring, the Company root, the other seven creatable types, '
                     'reference pinning, revisions, cross-chain relations, state snapshots, external events, '
                     'assignments, derived lifecycles, commitment and confirmation gates and the activation-policy '
                     'control command over real HTTP/PostgreSQL; synthetic data',
            'world_api_accepted': False, 'world_api_accepted_note': 'set only by the finished matrix (ticket #27)',
            'real_model': 'not_run', 'mcp': 'not_built', 'deployment': 'not_verified'})
    finally:
        flow.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.private.exists() or args.output.exists():
        raise ValueError('Use fresh private and output paths')
    args.private.mkdir(parents=True, mode=0o700)
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    initial = source_manifest(Path('src').resolve())
    error = None
    try:
        run(h, Path('src').resolve())
    except BaseException as exc:  # noqa: BLE001 - re-raised after the harness is closed
        error = exc
    finally:
        try:
            h.close()
        finally:
            if source_manifest(Path('src').resolve()) != initial and error is None:
                error = RuntimeError('source changed during the acceptance run; rerun on a stable checkpoint')
    if error is not None:
        raise error


if __name__ == '__main__':
    main()
