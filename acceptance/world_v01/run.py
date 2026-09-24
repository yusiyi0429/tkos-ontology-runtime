"""Real HTTP+PG acceptance skeleton of tkos.world/0.1 (ticket #19: wiring and the Company root).

Business success comes only from /v1/actions/prepare + /v1/actions; SQL is used for
identity seeding and independent assertions. Every rejection is observed at the
entrances it can reach, with an unchanged scope snapshot. No real model is run.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
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
    check('the_world_migration_is_the_newest_applied_migration', last == '0031_world_v01_contract_repin.sql')

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
    ref = _ref

    def created(actor, object_type, domain, payload, declaration=None):
        receipt = flow.create(actor, object_type, domain, payload, declaration)
        return receipt, receipt['result']

    # ------------------------------------------------------------ the spine, top to bottom
    choices = {'text': '聚焦企业经营系统。', 'refs': [ref(company, 'identity')]}
    _, strategy = created('ceo', 'Strategy', 'company', {
        'title': 'E&O 战略', 'parent_ref': ref(company),
        'blocks': {'choices': choices, 'responsibility_structure': {'text': 'E&O 与交付两个战场。'}}})
    _, unit = created('ceo', 'ResponsibilityUnit', 'a', {
        'title': 'E&O', 'unit_kind': 'battlefield', 'architecture_ref': ref(strategy, 'responsibility_structure')})
    _, company_goal = created('ceo', 'LongTermGoal', 'company', {
        'title': '公司三年目标', 'scope': 'company', 'horizon': '2029 年底', 'parent_ref': ref(company)})
    _, unit_goal = created('a', 'LongTermGoal', 'a', {
        'title': 'E&O 年度目标', 'scope': 'unit', 'horizon': '2027 年底',
        'parent_ref': ref(unit), 'goal_ref': ref(company_goal)})
    _, period_goal = created('a', 'PeriodGoal', 'a', {
        'title': '9 月', 'period': '2026-09', 'goal_ref': ref(unit_goal)})
    # 空块也可引用：周期目标的验收标准块此刻为 null。
    _, mission = created('a', 'Mission', 'a', {
        'title': '9 月底 CU Agent、DRI Agent 与 M1、M1B 真实可用', 'goal_ref': ref(period_goal),
        'blocks': {'acceptance': {'text': '按周期目标验收。', 'refs': [ref(period_goal, 'acceptance')]}}})
    declaration = {'scene': ref(mission), 'trigger': '9/23 会议拆解 Mission',
                   'human_acceptance': {'required': True, 'acceptor': f['actors']['ceo']['principal_id']}}
    task_receipt, task = created('a', 'Task', 'a', {'title': '数据环境准备', 'parent_ref': ref(mission)}, declaration)
    _, activity = created('ceo', 'Activity', 'a', {'title': '隔离库迁移与播种', 'parent_ref': ref(task)})
    made = {'Strategy': strategy, 'ResponsibilityUnit': unit, 'LongTermGoal': company_goal,
            'UnitLongTermGoal': unit_goal, 'PeriodGoal': period_goal, 'Mission': mission, 'Task': task,
            'Activity': activity}
    check('the_ceo_and_the_unit_dri_create_the_whole_spine_over_http',
          all(obj['version'] == 1 and obj['ref'] == ref(obj) for obj in made.values()))

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
            ('a_block_its_type_does_not_have', {'title': 'x', 'parent_ref': ref(mission),
                                                'blocks': {'plan': {'text': 'x', 'refs': [ref(mission, 'plan')]}}},
             'names a block its object type does not have'),
    ):
        flow.deny_create('a', 'Task', 'a', payload, codes={'INVALID_REQUEST'}, says=says)
        check(f'a_reference_to_{label}_is_refused')
    flow.deny_create('a', 'Task', 'a', {'title': 'x', 'parent_ref': ref(mission), 'owner': 'not in the contract'},
                     codes={'INVALID_REQUEST'}, says='Payload does not satisfy')
    check('a_field_outside_the_contract_is_refused')
    flow.deny_create('a', 'Mission', 'a', {'title': 'x', 'goal_ref': ref(period_goal), 'depends_on': [ref(mission)]},
                     codes={'INVALID_REQUEST'}, says='Payload does not satisfy')
    check('a_cross_chain_relation_is_not_written_by_creation')

    # ------------------------------------------------------------ where objects live and what they hang on
    misplaced, decomposes = 'not placed in the domain its spine parent requires', 'Only a unit-level goal decomposes'
    new_unit = {'title': 'x', 'unit_kind': 'domain', 'architecture_ref': ref(strategy, 'responsibility_structure')}
    for label, actor, object_type, domain, payload, says in (
            ('a_strategy_outside_the_company_domain', 'ceo', 'Strategy', 'a',
             {'title': 'x', 'parent_ref': ref(company)}, misplaced),
            ('a_unit_in_the_company_domain', 'ceo', 'ResponsibilityUnit', 'company', new_unit, misplaced),
            ('a_mission_in_another_unit_than_its_period_goal', 'ceo', 'Mission', 'b',
             {'title': 'x', 'goal_ref': ref(period_goal)}, misplaced),
            ('a_company_goal_hanging_on_a_unit', 'ceo', 'LongTermGoal', 'a',
             {'title': 'x', 'scope': 'company', 'horizon': 'x', 'parent_ref': ref(unit)},
             'company-level goal hangs on the Company'),
            ('a_company_goal_with_a_goal_ref', 'ceo', 'LongTermGoal', 'company',
             {'title': 'x', 'scope': 'company', 'horizon': 'x', 'parent_ref': ref(company),
              'goal_ref': ref(company_goal)}, decomposes),
            ('a_unit_goal_decomposing_a_unit_goal', 'a', 'LongTermGoal', 'a',
             {'title': 'x', 'scope': 'unit', 'horizon': 'x', 'parent_ref': ref(unit), 'goal_ref': ref(unit_goal)},
             decomposes),
            ('a_period_goal_advancing_a_company_goal', 'ceo', 'PeriodGoal', 'company',
             {'title': 'x', 'period': '2026-10', 'goal_ref': ref(company_goal)}, 'long-term goal of its own unit'),
            ('a_task_under_a_task', 'a', 'Task', 'a', {'title': 'x', 'parent_ref': ref(task)},
             'parent_ref must point to one of Mission'),
    ):
        flow.deny_create(actor, object_type, domain, payload, codes={'INVALID_REQUEST'}, says=says)
        check(f'{label}_is_refused')
    flow.deny_create('ceo', 'ResponsibilityUnit', 'a', new_unit, codes={'INVALID_STATE'}, prepare=False,
                     says='exactly one responsibility unit')
    check('a_domain_has_exactly_one_responsibility_unit')
    flow.deny_create('ceo', 'StateSnapshot', 'a',
                     {'title': 'x', 'subject_ref': ref(mission), 'as_of': '2026-09-24T09:37:00Z'},
                     codes={'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'}, says='written by world_refresh_state')
    check('a_state_snapshot_is_not_written_by_creation')

    # ------------------------------------------------------------ who may create
    not_responsible = 'Only a responsible person up the spine'
    flow.deny_create('a', 'ResponsibilityUnit', 'b', new_unit, codes={'FORBIDDEN'})
    check('a_dri_cannot_create_in_a_domain_where_they_hold_no_role')
    flow.deny_create('b', 'ResponsibilityUnit', 'b', new_unit, codes={'FORBIDDEN'}, says=not_responsible)
    check('only_the_ceo_creates_a_responsibility_unit')
    flow.deny_create('unrelated', 'Strategy', 'company', {'title': 'x', 'parent_ref': ref(company)},
                     codes={'FORBIDDEN'}, says=not_responsible)
    check('an_ic_cannot_create_a_strategy')
    # 判权先于其余校验：别的单元的 DRI 挂到本单元的周期目标下，先被拒在责任人上，不暴露放置错误。
    flow.deny_create('b', 'Mission', 'b', {'title': 'x', 'goal_ref': ref(period_goal)},
                     codes={'FORBIDDEN'}, says=not_responsible)
    check('a_dri_cannot_create_under_another_units_goal')

    # ------------------------------------------------------------ write declarations (Agent only)
    agent_task = {'title': 'Agent 分解', 'parent_ref': ref(mission)}
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
            ('whose_scene_is_not_a_mission_or_task', {**declaration, 'scene': ref(period_goal)},
             'declared scene is a Mission or a Task'),
            ('whose_acceptor_is_an_agent', {**declaration, 'human_acceptance': {
                'required': True, 'acceptor': f['actors']['agent']['principal_id']}}, no_person),
            ('whose_acceptor_holds_no_role_in_the_scope', {**declaration, 'human_acceptance': {
                'required': True, 'acceptor': f['bystander_principal_id']}}, no_person),
            ('whose_acceptor_is_unknown', {**declaration, 'human_acceptance': {
                'required': True, 'acceptor': '00000000-0000-4000-8000-000000000000'}}, no_person),
    ):
        flow.deny_create('a', 'Task', 'a', {'title': 'x', 'parent_ref': ref(mission)}, bad,
                         codes={'INVALID_REQUEST'}, says=says)
        check(f'a_declaration_{label}_is_refused')
    return {'checks': checks, 'made': made}


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
        checks = ctx['checks'] + objects['checks'] + revocation(h, f, flow, ctx['company_command'])
        public_json(h.output / 'summary.json', {
            'world_v01_skeleton_passed': True, 'checks': checks,
            'scope': 'Tickets #19-#20: world wiring, the Company root, the other seven creatable types and '
                     'reference pinning over real HTTP/PostgreSQL; synthetic data',
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
