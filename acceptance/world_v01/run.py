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

from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import public_json, source_manifest
from .fixture import ROOT, probe_binding_gate, register_world, revoke_assignment, seed_world
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
    check('the_world_migration_is_the_newest_applied_migration', last == '0034_world_v01_assign_owner.sql')

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


def revocation(h, f, flow, company_command):
    """最后撤掉 CEO 在公司域的那条 CEO 指派：CEO 在别的域还有角色，仍是 scope 成员，但原命令不能再被重放成成功。"""
    checks = []
    revoke_assignment(h.env, f, f['actors']['ceo']['assignment_id'])
    flow.deny('ceo', deepcopy(company_command), codes={'FORBIDDEN'}, prepare=False)
    _checker(checks)('a_revoked_ceo_cannot_replay_the_creation_into_a_success')
    return checks


def run(h: MethodHarness, source: Path):
    f = seed_world(h.env, h.private / 'identities.json', 'runtime-acceptance-world-v01')
    register_world(h, source, f)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        ctx = company_root(h, f, flow)
        objects = objects_and_refs(h, f, flow, ctx['company'])
        revisions = revise_and_relate(h, f, flow, ctx['company'], objects['made'])
        states = state_and_events(h, f, flow, objects['made'])
        assigned = assign_and_lifecycle(h, f, flow, {**objects['made'], 'Company': ctx['company']})
        checks = (ctx['checks'] + objects['checks'] + revisions['checks'] + states['checks'] + assigned['checks']
                  + revocation(h, f, flow, ctx['company_command']))
        public_json(h.output / 'summary.json', {
            'world_v01_skeleton_passed': True, 'checks': checks,
            'scope': 'Tickets #19-#23: world wiring, the Company root, the other seven creatable types, '
                     'reference pinning, revisions, cross-chain relations, state snapshots, external events, '
                     'assignments and derived lifecycles over real HTTP/PostgreSQL; synthetic data',
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
