"""tkos.world/0.2 独立 API 验收的骨架（票 #49 起逐票追加，票 #65 汇总成矩阵，不冻结）：真 API 进程、隔离库、
真 HTTP 与 PostgreSQL。

业务成功只来自 /v1/actions/prepare 与 /v1/actions；SQL 只用于播种身份与独立核对。每个拒绝用例都在它能到达的
入口上核对 scope 的库快照不变。骨架不写「验收通过」：报告只列每条检查与场景是否跑完，全部通过退出码为 0。
库可以是 method_v05 工具新建的，也可以是刚跑完 0.1 独立验收的同一个库（同库回归，0.1 先跑）。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from acceptance.method_independent.fixture import uid
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import now, public_json, safe_traceback_frames, source_manifest
from acceptance.runtime.client import Client
from acceptance.world_v01.fixture import ROOT, register_world, revoke_assignment, seed_world
from acceptance.world_v01.flow import Flow as V01Flow
from .fixture import (CONTRACT, PROFILE, REGISTRY, SUPPORT, action_roles, install_activation_policies, owner,
                      owner_rows, probe_binding_gate, probe_event_row, register_world_v02)

V01, V02 = 'tkos.world/0.1', 'tkos.world/0.2'
MIGRATION = '0039_world_v02.sql'
SCENARIOS = ['migration', 'control_plane', 'company', 'objects', 'rejections', 'coexistence', 'references',
             'revocation']
EVENT_KINDS = {item['kind']: item for item in json.loads(REGISTRY.read_text())['event_kinds']}
OBJECTS = {item['type']: item for item in json.loads(REGISTRY.read_text())['objects']}
TYPES = ['Company', 'Strategy', 'ResponsibilityUnit', 'LongTermGoal', 'PeriodGoal', 'Mission', 'Task', 'Activity']


class Book:
    """骨架的记账：检查按名字记一次，不成立即记为失败并中止这次运行。"""

    def __init__(self, output: Path):
        self.output, self.checks, self.metadata, self.started_at = output, {}, {}, now()

    def check(self, name, value=True):
        if name in self.checks:
            raise ValueError('a check cannot be silently replaced')
        self.checks[name] = bool(value)
        self.save()
        print(('PASS ' if value else 'FAIL ') + name, flush=True)
        if not value:
            raise AssertionError(name)

    def save(self, **extra):
        self.metadata.update(extra)
        scenarios = self.metadata.get('scenarios', {})
        complete = all(scenarios.get(name, {}).get('status') == 'completed' for name in SCENARIOS)
        result = {**self.metadata, 'scope': 'tkos.world/0.2 APIs (skeleton, not frozen)',
                  'started_at': self.started_at, 'updated_at': now(), 'checks': self.checks,
                  'checks_passed': sum(self.checks.values()), 'checks_failed': len(self.checks) - sum(self.checks.values()),
                  'all_scenarios_completed': complete,
                  'passed': complete and all(self.checks.values()) and not self.metadata.get('run_error'),
                  'world_v02_accepted': False, 'released': False, 'deployed': False}
        public_json(self.output / 'report.json', result)
        return result


class Flow(V01Flow):
    """0.1 的公开 HTTP 驱动，命令默认声明 0.2；contract 给 0.1 时发 0.1 的命令。"""

    def command(self, kind, params, *, key=None, expected_versions=None, contract=V02):
        body = Client.command(kind, deepcopy(params), expected_versions=expected_versions, key=key)
        body['contract_version'] = contract
        return body

    def scoped_snapshot(self, scope_id):
        return self.h.sql({'scope_id': scope_id}, """
            SELECT (SELECT count(*) FROM gov_objects WHERE scope_id=%s) AS objects,
                   (SELECT count(*) FROM gov_world_events WHERE scope_id=%s) AS events,
                   (SELECT count(*) FROM gov_action_receipts WHERE scope_id=%s) AS receipts""",
                          (scope_id, scope_id, scope_id))[0]


def migration(book, h, upgrade_evidence):
    """0039 作为升级的一步应用、重复为空；它是最新的迁移；已有的 0.1 事件行（同库回归时）只被校验、不被改写。"""
    check = book.check
    upgraded = json.loads(upgrade_evidence.read_text())
    database = conninfo_to_dict(h.env.values['APP_DATABASE_URL'])['dbname']
    check('the_upgrade_applied_0039_to_this_database_and_repeating_it_applies_nothing',
          upgraded['database'] == database and MIGRATION in upgraded['applied'] and upgraded['repeat_applied'] == [])
    with h.app_connection() as conn:
        applied = [row['name'] for row in conn.execute('SELECT name FROM schema_migrations ORDER BY name')]
    check('0039_is_the_newest_applied_migration', applied[-1] == MIGRATION)
    with h.app_connection() as conn:
        columns = {row['column_name'] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='gov_world_events'")}
    check('the_event_log_carries_every_0_2_field',
          {'contract_version', 'disposition', 'on_behalf_of', 'external_record_id', 'external_confirmed_at',
           'detail', 'phase'} <= columns)
    with owner(h.env) as conn:
        rows = conn.execute("""SELECT contract_version, count(*) AS n,
                                      count(*) FILTER (WHERE disposition IS NOT NULL OR on_behalf_of IS NOT NULL
                                                       OR external_record_id IS NOT NULL OR detail IS NOT NULL) AS extra
                                 FROM gov_world_events GROUP BY contract_version""").fetchall()
    book.metadata['existing_events'] = {row['contract_version']: row['n'] for row in rows}
    check('existing_0_1_events_read_as_0_1_with_none_of_the_0_2_fields',
          all(row['contract_version'] == V01 and row['extra'] == 0 for row in rows))


def control_plane(book, h, source, f):
    """控制面：0.2 scope 装 0.2 profile、默认策略（默认契约 0.2）、支持登记与激活策略；另一 scope 装 0.1 作对照。
    登记字节与钉定不符时 profile 整体拒绝、什么都不留。"""
    check = book.check
    scope = f['scope_id']
    install_activation_policies(h, source, {scope: f['domains'], f['foreign_scope_id']: f['foreign_domains']})

    def rows(statement, *params):
        return owner_rows(h.env, scope, statement, (scope, *params))

    def profiles():
        return rows('SELECT * FROM gov_method_profile_revisions WHERE scope_id=%s AND profile_id=%s', PROFILE['profile_id'])

    other = h.private / f'world-registry-0.2-other-{uid()[:8]}.json'
    other.write_bytes(REGISTRY.read_bytes() + b'\n')
    register_world_v02(h, source, scope, registry=other, expected_exit=2, expected_error_code='PROFILE_CONTENT_CONFLICT')
    check('a_0_2_profile_install_with_other_registry_bytes_is_refused_and_nothing_is_left_behind',
          profiles() == []
          and [r for r in rows('SELECT event_type FROM gov_protocol_control_events WHERE scope_id=%s')
               if r['event_type'] == 'install_profile'] == [])

    register_world_v02(h, source, scope)
    register_world(h, source, {'scope_id': f['foreign_scope_id']})
    installed = profiles()
    check('install_profile_records_the_0_2_profile_pinned_to_the_contract_and_registry_bytes',
          [(r['schema_version'], r['revision'], r['canonical_hash']) for r in installed]
          == [('tkos.world-profile/0.2', PROFILE['revision'], PROFILE['canonical_hash'])]
          and installed[0]['content']['action_contract_ref']['content_sha256'] == hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
          and installed[0]['content']['world_registry_ref']['content_sha256'] == hashlib.sha256(REGISTRY.read_bytes()).hexdigest())
    policy = rows('SELECT * FROM gov_protocol_policies WHERE scope_id=%s AND domain_id IS NULL ORDER BY policy_seq')[-1]
    check('the_new_scope_defaults_to_world_0_2',
          (policy['content']['default_protocol'], policy['content']['default_contract_version']) == ('tkos.world', V02)
          and policy['content']['default_profile_ref'] == {'profile_id': PROFILE['profile_id'], 'revision': PROFILE['revision']})
    support = json.loads(SUPPORT.read_text())
    registered = rows("SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id='tkos.world'")
    check('the_0_2_support_registry_lists_only_the_implemented_actions_and_types',
          [(r['contract_version'], r['content']['actions'], r['content']['object_types']) for r in registered]
          == [(V02, ['world_create_object'], sorted(TYPES))] and registered[0]['content'] == support)
    activation = rows('SELECT DISTINCT ON (domain_id) content FROM gov_activation_policies WHERE scope_id=%s '
                      'ORDER BY domain_id, policy_seq DESC')
    check('every_domain_has_an_activation_policy_granting_the_implemented_0_2_actions',
          len(activation) == len(f['domains'])
          and all(r['content']['action_roles'][action] == roles
                  for r in activation for action, roles in action_roles().items()))


def company(book, h, f, flow):
    """CEO 经 HTTP 以 0.2 建出 Company，取对象按三层读回；恰好一条 0.2 的 object.created 与一张回执。"""
    check = book.check
    identity = {'text': '一家为企业做经营系统的公司。', 'artifacts': ['https://example.test/company-brief']}
    refs = [{'system': 'tianshu', 'id': 'company-1', 'url': 'https://example.test/c/1'}]
    body = flow.prepare('ceo', flow.command('world_create_object', {
        'domain_id': f['domains']['company'], 'object_type': 'Company',
        'payload': {'title': 'E&O 合成公司', 'external_refs': refs, 'blocks': {'identity': identity}}}))
    receipt = flow.commit('ceo', body)
    made = receipt['result']
    check('the_ceo_creates_the_company_over_http_under_world_0_2',
          receipt['action_type'] == 'world_create_object' and made['contract_version'] == V02
          and made['version'] == 1 and made['ref'] == made['object_id'] + '@1')

    view = flow.read('ceo', made['object_id'])
    business, blocks = view['business'], {b['id']: b for b in view['business']['blocks']}
    check('the_object_is_read_back_in_three_groups', set(view) == {'object_id', 'business', 'identity', 'records', 'protocol'})
    check('business_carries_type_category_blocks_with_classes_and_the_empty_block_sentence',
          business['object_type'] == 'Company' and business['type_display_name'] == '公司'
          and business['category'] == {'id': 'business_object', 'display_name': '业务对象'}
          and business['candidate'] is False and business['title'] == 'E&O 合成公司' and business['version'] == 1
          and business['attributes'] == {'external_refs': refs}
          and list(blocks) == ['identity', 'constraint']
          and blocks['identity']['text'] == identity['text'] and blocks['identity']['class'] == 'formal'
          and blocks['identity']['value']['artifacts'] == identity['artifacts'] and blocks['identity']['components'] == []
          and blocks['constraint']['empty'] and blocks['constraint']['text'] == '当前没有约束'
          and blocks['identity']['ref'] == made['object_id'] + '@1#identity'
          and business['component_ledger'] == [] and business['round'] is None
          and business['formal'] == {'lifecycle_status': 'recorded', 'effective_revision_id': made['revision_id']})
    check('identity_names_the_ceo_by_role_and_records_are_empty_for_the_company',
          view['identity']['responsible']['source'] == 'role' and view['identity']['responsible']['role'] == 'CEO'
          and [p['principal_id'] for p in view['identity']['responsible']['principals']] == [f['actors']['ceo']['principal_id']]
          and view['identity']['delegations'] == []
          and view['records'] == {'lifecycle': None, 'latest_state': None, 'confirmed_review': None, 'open_issues': []})
    check('the_company_is_read_as_world_0_2',
          view['protocol']['interpretation_status'] == 'world_v0_2' and view['protocol']['contract_version'] == V02)

    events = flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s', (f['scope_id'],))
    check('exactly_one_object_created_record_event_under_0_2_points_back_to_its_receipt',
          len(events) == 1 and events[0]['kind'] == 'object.created' and events[0]['contract_version'] == V02
          and EVENT_KINDS[events[0]['kind']]['class'] == 'record' and events[0]['phase'] is None
          and str(events[0]['action_id']) == receipt['receipt_id'] and str(events[0]['event_id']) == made['event_id']
          and str(events[0]['principal_id']) == f['actors']['ceo']['principal_id']
          and events[0]['subject_refs'] == [{'object_id': made['object_id'], 'object_version': 1,
                                             'revision_id': made['revision_id'], 'block': None, 'component': None}])
    receipts = flow.rows('SELECT receipt_id FROM gov_action_receipts WHERE scope_id=%s', (f['scope_id'],))
    check('exactly_one_receipt_is_written_for_the_creation', [str(r['receipt_id']) for r in receipts] == [receipt['receipt_id']])
    binding = flow.rows('SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s',
                        (f['scope_id'], made['object_id']))
    check('the_binding_pins_the_0_2_profile_through_the_world_gate',
          len(binding) == 1 and (binding[0]['protocol_id'], binding[0]['contract_version']) == ('tkos.world', V02)
          and binding[0]['profile_canonical_hash'] == PROFILE['canonical_hash'])

    check('an_identity_assigned_only_in_another_domain_reads_the_company_by_the_world_rule',
          flow.read('outsider', made['object_id'])['object_id'] == made['object_id'])
    seen = flow.clients['outsider'].json('GET', f"/v1/action-receipts/{receipt['receipt_id']}")
    check('the_0_2_receipt_is_read_by_the_world_rule', seen['receipt']['receipt_id'] == receipt['receipt_id'])
    check('an_identity_from_another_scope_cannot_read_the_company',
          flow.read('foreign_ceo', made['object_id'], expected=404)['error']['code'] == 'NOT_FOUND')
    later = flow.events('ceo', made['object_id'], expected=409)
    check('read_endpoints_not_yet_wired_for_0_2_refuse_instead_of_reading_it_as_0_1',
          later['error']['code'] == 'PROTOCOL_NOT_SUPPORTED')

    replay = flow.commit('ceo', deepcopy(body))
    check('replaying_the_same_command_returns_the_original_receipt',
          replay['receipt_id'] == receipt['receipt_id']
          and len(flow.rows('SELECT 1 FROM gov_world_events WHERE scope_id=%s', (f['scope_id'],))) == 1)
    changed = deepcopy(body)
    changed['params']['payload']['title'] = 'Another title under the same key'
    flow.deny('ceo', changed, codes={'IDEMPOTENCY_CONFLICT'}, prepare=False)
    check('reusing_a_key_for_a_different_command_is_refused')

    before = h.snapshot(f)
    refusal = probe_binding_gate(h.env, f, '0' * 64)
    check('the_world_gate_refuses_a_0_2_binding_whose_profile_does_not_pin_the_exact_contract',
          refusal is not None and 'world 0.2 requires its exact business-world contract and registry' in refusal
          and h.snapshot(f) == before)
    check('the_event_log_refuses_a_phase_on_a_0_2_event',
          probe_event_row(h.env, f, V02, phase='initiation') == 'ck_gov_world_event_v02')
    check('the_event_log_refuses_0_2_fields_on_a_0_1_event',
          probe_event_row(h.env, f, V01, detail=Jsonb({})) == 'ck_gov_world_event_v01')
    check('the_event_log_refuses_on_behalf_recording_of_a_record_event',
          probe_event_row(h.env, f, V02, on_behalf_of=f['actors']['unrelated']['principal_id'],
                          external_record_id='ext-1', external_confirmed_at='2026-09-27T00:00:00Z')
          == 'ck_gov_world_event_v02')
    return {'company': made, 'command': body}


def blocks_of(view):
    return {block['id']: block for block in view['business']['blocks']}


def objects(book, h, f, flow, company):
    """#50：沿主干建出其余七类对象，块里带组件，引用四种形式各有；每类读回块、组件、台账与钉定引用。
    单元 a 的 DRI 建单元以下的对象，CEO 建公司域的对象与责任单元；人带的写入声明按 Agent 的规则校验。"""
    check = book.check
    made = {'Company': company}

    def create(actor, object_type, domain, payload, declaration=None):
        made[object_type if object_type not in made else object_type + '.unit'] = result = flow.create(
            actor, object_type, domain, payload, declaration)['result']
        return result, flow.read(actor, result['object_id'])

    strategy, strategy_view = create('ceo', 'Strategy', 'company', {
        'title': '2026 战略', 'parent_ref': company['ref'],
        'blocks': {'choices': {'text': '聚焦企业经营系统。'},
                   'assumptions': {'components': [{'type': 'assumption', 'text': '客户愿意为可追溯付费。'}]},
                   'responsibility_structure': {'text': '两个战场。', 'components': [
                       {'id': 'unit-a', 'type': 'unit_entry', 'text': '战场 A'},
                       {'type': 'unit_entry', 'text': '战场 B'}]}}})
    structure = blocks_of(strategy_view)['responsibility_structure']
    generated = structure['components'][1]['id']
    assumption = blocks_of(strategy_view)['assumptions']['components'][0]['id']
    check('components_keep_the_writers_id_or_get_one_from_the_service',
          [c['id'] for c in structure['components']] == ['unit-a', generated] and generated != assumption
          and all(len(cid) == 36 for cid in (generated, assumption))
          and structure['components'][0] == {
              'id': 'unit-a', 'type': 'unit_entry', 'scope': None, 'text': '战场 A', 'refs': [], 'artifacts': [],
              'attributes': {}, 'ref': strategy['object_id'] + '@1#responsibility_structure/unit-a'})
    check('the_component_ledger_is_kept_from_creation',
          strategy_view['business']['component_ledger'] == [
              {'id': assumption, 'type': 'assumption', 'block': 'assumptions', 'added_in_version': 1,
               'removed_in_version': None},
              {'id': 'unit-a', 'type': 'unit_entry', 'block': 'responsibility_structure', 'added_in_version': 1,
               'removed_in_version': None},
              {'id': generated, 'type': 'unit_entry', 'block': 'responsibility_structure', 'added_in_version': 1,
               'removed_in_version': None}])
    strategy_event = str(flow.rows('SELECT event_id FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                                   (f['scope_id'], flow.receipts[-1]['receipt_id']))[0]['event_id'])

    unit, unit_view = create('ceo', 'ResponsibilityUnit', 'a', {
        'title': '战场 A', 'unit_kind': 'battlefield',
        'architecture_ref': strategy['ref'] + '#responsibility_structure/unit-a',
        'blocks': {'definition': {'text': '负责 A 客户群。'}}})
    architecture = unit_view['business']['relations'][0]
    check('a_responsibility_unit_is_defined_by_a_unit_entry_pinned_by_component',
          architecture['field'] == 'architecture_ref' and architecture['value'] == {
              'object_id': strategy['object_id'], 'object_version': 1, 'revision_id': strategy['revision_id'],
              'block': 'responsibility_structure', 'component': 'unit-a',
              'ref': strategy['ref'] + '#responsibility_structure/unit-a'})

    company_goal, company_goal_view = create('ceo', 'LongTermGoal', 'company', {
        'title': '公司三年目标', 'scope': 'company', 'horizon': '2028', 'parent_ref': company['ref'],
        'blocks': {'measures': {'components': [{'id': 'sc-1', 'type': 'success_criterion', 'text': '年收入过亿'}]}}})
    unit_goal, unit_goal_view = create('a', 'LongTermGoal', 'a', {
        'title': '战场 A 长期目标', 'scope': 'unit', 'horizon': '2027', 'parent_ref': unit['ref'],
        'goal_ref': company_goal['ref'],
        'blocks': {'outcome': {'components': [{'id': 'ua-o1', 'type': 'outcome', 'text': 'A 客户群收入过半',
                                               'refs': [company_goal['ref'] + '#measures/sc-1']}]}}})
    outcome = blocks_of(unit_goal_view)['outcome']['components'][0]
    check('a_component_reference_is_read_back_in_both_forms',
          outcome['refs'] == [{'object_id': company_goal['object_id'], 'object_version': 1,
                               'revision_id': company_goal['revision_id'], 'block': 'measures', 'component': 'sc-1',
                               'ref': company_goal['ref'] + '#measures/sc-1'}])

    # 人带写入声明：场景是责任单元（0.1 只认 Mission、Task）。
    scene_unit = {'scene': unit['ref'], 'trigger': '周期形成', 'human_acceptance': {'required': False}}
    goal, goal_view = create('a', 'PeriodGoal', 'a', {
        'title': '10 月目标', 'period': '2026-10', 'goal_ref': unit_goal['ref'],
        'blocks': {'outcome': {'components': [{'id': 'pg-o1', 'type': 'outcome', 'text': '签 3 家',
                                               'refs': [unit_goal['ref'] + '#outcome/ua-o1']}]},
                   'realization_logic': {'text': '靠两场试点。',
                                         'refs': [unit_goal['ref'] + '#measures', f'event:{strategy_event}',
                                                  strategy['ref']]},
                   'acceptance': {'components': [{'id': 'pg-ac1', 'type': 'acceptance_criterion', 'text': '合同签署'}]}}},
        scene_unit)
    logic = blocks_of(goal_view)['realization_logic']['value']['refs']
    check('object_block_and_event_references_are_pinned_and_read_back_in_both_forms',
          logic == [{'object_id': unit_goal['object_id'], 'object_version': 1, 'revision_id': unit_goal['revision_id'],
                     'block': 'measures', 'component': None, 'ref': unit_goal['ref'] + '#measures'},
                    {'event_id': strategy_event, 'ref': f'event:{strategy_event}'},
                    {'object_id': strategy['object_id'], 'object_version': 1, 'revision_id': strategy['revision_id'],
                     'block': None, 'component': None, 'ref': strategy['ref']}])
    check('a_whole_block_reference_in_the_0_1_form_still_works_in_a_0_2_object',
          logic[0]['ref'] == unit_goal['ref'] + '#measures' and blocks_of(unit_goal_view)['measures']['empty'])
    check('a_declared_scene_may_be_a_responsibility_unit',
          goal['declaration']['scene']['ref'] == unit['ref'] and goal['declaration']['scene']['block'] is None)

    scene_goal = {'scene': goal['ref'], 'trigger': 'Mission 立项',
                  'human_acceptance': {'required': True, 'acceptor': f['actors']['ceo']['principal_id']}}
    mission, mission_view = create('a', 'Mission', 'a', {
        'title': '试点一', 'goal_ref': goal['ref'],
        'blocks': {'acceptance': {'components': [{'type': 'acceptance_criterion', 'text': '客户签字',
                                                  'refs': [goal['ref'] + '#acceptance/pg-ac1'],
                                                  'scope': goal['ref']}]},
                   'execution_plan': {'components': [{'id': 'plan-1', 'type': 'plan_item', 'text': '搭环境',
                                                      'attributes': {'responsible': f['actors']['owner_a']['principal_id']}}]}}},
        scene_goal)
    check('a_declared_scene_may_be_a_period_goal', mission['declaration']['scene']['ref'] == goal['ref']
          and mission['declaration']['human_acceptance']['acceptor'] == f['actors']['ceo']['principal_id'])
    mission_blocks = blocks_of(mission_view)
    check('a_plan_item_keeps_its_responsible_as_a_record_and_the_execution_plan_is_an_activity_block',
          mission_blocks['execution_plan']['class'] == 'activity'
          and mission_blocks['execution_plan']['components'][0]['attributes']
          == {'responsible': f['actors']['owner_a']['principal_id']}
          and mission_view['business']['attributes']['responsible'] is None)
    check('a_component_scope_is_pinned_as_an_object_reference',
          mission_blocks['acceptance']['components'][0]['scope']['ref'] == goal['ref'])
    check('responsibility_by_attribute_is_named_as_such_and_empty_until_assigned',
          mission_view['identity']['responsible'] == {'source': 'attribute', 'roles': {'human': 'OWNER'},
                                                     'principals': []})

    task, task_view = create('a', 'Task', 'a', {
        'title': '准备演示', 'parent_ref': mission['ref'],
        'blocks': {'acceptance': {'components': [{'type': 'acceptance_criterion', 'text': '演示通过',
                                                  'refs': [mission_blocks['acceptance']['components'][0]['ref']]}]},
                   'plan': {'components': [{'type': 'plan_item', 'text': '写脚本'}]}}})
    activity, activity_view = create('a', 'Activity', 'a', {
        'title': '录屏', 'parent_ref': task['ref'],
        'blocks': {'instruction': {'text': '按脚本录屏。', 'refs': [task['ref'] + '#plan']}}})
    check('the_activity_is_marked_as_a_candidate_type',
          activity_view['business']['candidate'] is True and task_view['business']['candidate'] is False)

    views = {'Strategy': strategy_view, 'ResponsibilityUnit': unit_view, 'LongTermGoal': unit_goal_view,
             'PeriodGoal': goal_view, 'Mission': mission_view, 'Task': task_view, 'Activity': activity_view}
    check('each_of_the_seven_types_reads_back_blocks_components_ledger_and_pinned_relations',
          all(view['business']['object_type'] == object_type and view['protocol']['interpretation_status'] == 'world_v0_2'
              and [b['id'] for b in view['business']['blocks']] == [b['id'] for b in OBJECTS[object_type]['blocks']]
              and sorted(e['id'] for e in view['business']['component_ledger'])
              == sorted(c['id'] for b in view['business']['blocks'] for c in b['components'])
              and all(r['value'] is None or all('ref' in item and 'revision_id' in item for item in
                                                (r['value'] if isinstance(r['value'], list) else [r['value']]))
                      for r in view['business']['relations'])
              for object_type, view in views.items()))
    events = flow.rows("SELECT kind, contract_version, subject_refs FROM gov_world_events WHERE scope_id=%s", (f['scope_id'],))
    check('every_creation_wrote_one_0_2_object_created_event_pinned_to_its_first_revision',
          len(events) == 9  # Company 与主干上的八个对象（长期目标公司级、单元级各一）
          and all(e['kind'] == 'object.created' and e['contract_version'] == V02
                  and e['subject_refs'][0]['component'] is None for e in events))
    return {'made': made, 'strategy_event': strategy_event, 'assumption': assumption}


def references(book, h, f, flow, trunk, foreign):
    """#50 的拒绝（在 0.1 对照 scope 建出对象之后跑）：不存在的对象、版本、块、组件、事件，scope 外的对象与事件，
    非责任单元条目的架构引用，组件的各类违约，Agent 建对象，建对象写快照；错误码正确且库快照不变。"""
    check = book.check
    made = trunk['made']
    mission, task, strategy = made['Mission'], made['Task'], made['Strategy']

    def deny_task(codes, *, actor='a', refs=None, parent=None, blocks=None, declaration=None, **payload):
        definition = {'text': '做事', 'refs': refs} if refs is not None else {'text': '做事'}
        flow.deny_create(actor, 'Task', 'a', {'title': 'T', 'parent_ref': parent or mission['ref'],
                                             'blocks': blocks or {'definition': definition}, **payload},
                         declaration, codes=codes)

    missing = uid()
    deny_task({'INVALID_REQUEST'}, parent=f'{missing}@1')
    check('a_reference_to_an_object_that_does_not_exist_is_refused')
    deny_task({'INVALID_REQUEST'}, refs=[mission['object_id'] + '@9'])
    check('a_reference_to_a_version_that_does_not_exist_is_refused')
    deny_task({'INVALID_REQUEST'}, refs=[mission['ref'] + '#no_such_block'])
    check('a_reference_to_a_block_the_type_does_not_have_is_refused')
    deny_task({'INVALID_REQUEST'}, refs=[mission['ref'] + '#execution_plan/no-such-item'])
    check('a_reference_to_a_component_not_present_in_that_version_is_refused')
    deny_task({'INVALID_REQUEST'}, refs=[f'event:{missing}'])
    check('a_reference_to_an_event_that_does_not_exist_is_refused')
    deny_task({'INVALID_REQUEST'}, refs=[foreign['object']['ref']])
    check('a_reference_to_an_object_of_another_scope_is_refused_as_missing')
    deny_task({'INVALID_REQUEST'}, refs=[f"event:{foreign['event_id']}"])
    check('a_reference_to_an_event_of_another_scope_is_refused_as_missing')

    for architecture in (strategy['ref'], strategy['ref'] + '#responsibility_structure',
                         strategy['ref'] + f"#assumptions/{trunk['assumption']}"):
        flow.deny_create('ceo', 'ResponsibilityUnit', 'b', {'title': 'B', 'unit_kind': 'domain',
                                                             'architecture_ref': architecture}, codes={'INVALID_REQUEST'})
    check('an_architecture_reference_that_is_not_a_unit_entry_is_refused')

    deny_task({'INVALID_REQUEST'}, blocks={'acceptance': {'components': [{'id': 'x', 'type': 'acceptance_criterion'}]},
                                           'plan': {'components': [{'id': 'x', 'type': 'plan_item'}]}})
    check('a_component_id_repeated_within_the_object_is_refused')
    deny_task({'INVALID_REQUEST'}, blocks={'acceptance': {'components': [{'type': 'plan_item', 'text': 'x'}]}})
    check('a_component_type_the_block_does_not_allow_is_refused')
    deny_task({'INVALID_REQUEST'}, blocks={'plan': {'components': [{'type': 'plan_item',
                                                                    'attributes': {'responsible': missing}}]}})
    check('a_plan_item_responsible_that_is_no_principal_of_the_scope_is_refused')
    deny_task({'INVALID_REQUEST'}, blocks={'plan': {'components': [{'type': 'plan_item', 'scope': f"event:{trunk['strategy_event']}"}]}})
    check('a_component_scope_is_an_object_reference')
    flow.deny_create('a', 'PeriodGoal', 'a', {'title': 'P', 'period': '2026-11', 'goal_ref': made['LongTermGoal.unit']['ref'],
                                             'review_ref': made['Company']['ref']}, codes={'INVALID_REQUEST'})
    check('a_period_goal_review_reference_is_not_open_yet')
    deny_task({'INVALID_REQUEST'}, declaration={'scene': mission['ref'] + '#acceptance', 'trigger': 'x',
                                                'human_acceptance': {'required': False}})
    check('a_declared_scene_is_an_object_reference')

    flow.deny_create('agent_a', 'Activity', 'a', {'title': 'A', 'parent_ref': task['ref']},
                     {'scene': task['ref'], 'trigger': 'x', 'human_acceptance': {'required': False}}, codes={'FORBIDDEN'})
    check('an_agent_does_not_create_objects')
    deny_task({'FORBIDDEN'}, actor='ic_a')
    check('creation_rights_stay_as_in_0_1')
    flow.deny_create('ceo', 'Task', 'b', {'title': 'T', 'parent_ref': mission['ref']}, codes={'INVALID_REQUEST'})
    check('domain_placement_stays_as_in_0_1')
    flow.deny_create('ceo', 'ResponsibilityUnit', 'a', {'title': 'A2', 'unit_kind': 'domain',
                                                        'architecture_ref': strategy['ref'] + '#responsibility_structure/unit-a'},
                     codes={'INVALID_STATE'}, prepare=False)
    check('a_domain_has_exactly_one_responsibility_unit')


def rejections(book, h, f, flow, made):
    """拒绝：无权角色、多余字段、错期望版本、0.2 请求打到 0.1 默认的 scope，错误码正确且库快照不变。"""
    check = book.check

    def params(title='E&O', blocks=None, domain='company', object_type='Company', **payload):
        return {'domain_id': f['domains'][domain], 'object_type': object_type,
                'payload': {'title': title, 'blocks': blocks or {}, **payload}}

    flow.deny('unrelated', flow.command('world_create_object', params('IC company')), codes={'FORBIDDEN'})
    check('an_ic_cannot_create_the_company')
    flow.deny('agent', flow.command('world_create_object', params('Agent company', domain='a')), codes={'FORBIDDEN'})
    check('an_agent_cannot_create_the_company')
    flow.deny('outsider', flow.command('world_create_object', params(object_type='Strategy')), codes={'FORBIDDEN'})
    check('authorization_is_refused_before_any_protocol_error')
    flow.deny('ceo', flow.command('world_create_object', params(owner='not in the contract')), codes={'INVALID_REQUEST'})
    check('a_payload_field_outside_the_contract_is_refused')
    flow.deny('ceo', flow.command('world_create_object', params(blocks={'identity': {'text': '   '}})),
              codes={'INVALID_REQUEST'})
    check('an_empty_block_cannot_pose_as_content')
    flow.deny('ceo', flow.command('world_create_object', params(
        blocks={'identity': {'text': 'x', 'components': [{'type': 'outcome', 'text': 'y'}]}})), codes={'INVALID_REQUEST'})
    check('a_component_in_a_block_that_takes_none_is_refused')
    flow.deny('ceo', flow.command('world_create_object', params(external_refs=[{'system': 'tianshu'}])),
              codes={'INVALID_REQUEST'})
    check('an_external_ref_without_an_id_is_refused')
    flow.deny('ceo', flow.command('world_create_object', params(object_type='StateSnapshot')),
              codes={'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'})
    check('a_state_snapshot_is_not_written_by_creating_an_object')
    flow.deny('ceo', flow.command('world_create_object', params('Second company')), codes={'INVALID_STATE'}, prepare=False)
    check('a_scope_has_exactly_one_company')
    stale = flow.command('world_create_object', params('Stale company'),
                         expected_versions=[{'object_id': made['object_id'], 'expected_version': 99}])
    flow.deny('ceo', stale, codes={'VERSION_CONFLICT'}, prepare=False)
    check('a_wrong_expected_version_is_refused')
    flow.deny('ceo', flow.command('world_revise_object', {'payload': {'title': 'x'}}) | {
        'target': {'object_id': made['object_id'], 'revision_id': made['revision_id'], 'expected_version': 1}},
        codes={'INVALID_REQUEST'})
    check('an_action_not_yet_implemented_under_0_2_is_refused_by_the_envelope')

    # 0.1 默认的 scope：0.2 请求在授权之后返回协议错误，那个 scope 里什么都不留。
    foreign = flow.command('world_create_object', {'domain_id': f['foreign_domains']['company'], 'object_type': 'Company',
                                                   'payload': {'title': 'Foreign company'}})
    before = flow.scoped_snapshot(f['foreign_scope_id'])
    codes = [flow.clients['foreign_ceo'].json('POST', path, foreign, expected=409)['error']['code']
             for path in ('/v1/actions/prepare', '/v1/actions')]
    check('a_0_2_request_to_a_scope_defaulting_to_0_1_is_protocol_not_supported_and_writes_nothing',
          codes == ['PROTOCOL_NOT_SUPPORTED'] * 2 and flow.scoped_snapshot(f['foreign_scope_id']) == before)


def coexistence(book, h, f, flow):
    """同名动作带 0.1 版本仍由 0.1 处理，0.1 对象读回仍是 0.1 形状；0.1 请求打到 0.2 的 scope 被拒。"""
    check = book.check
    body = flow.command('world_create_object', {'domain_id': f['foreign_domains']['company'], 'object_type': 'Company',
                                                'payload': {'title': 'World 0.1 company'}}, contract=V01)
    receipt = flow.commit('foreign_ceo', flow.prepare('foreign_ceo', body))
    made = receipt['result']
    check('the_same_action_name_with_the_0_1_contract_is_handled_by_0_1',
          'contract_version' not in made and made['ref'] == made['object_id'] + '@1')
    view = flow.read('foreign_ceo', made['object_id'])
    check('a_0_1_object_reads_back_in_the_0_1_shape',
          'business' not in view and [b['id'] for b in view['blocks']] == ['identity', 'constraint']
          and view['protocol']['interpretation_status'] == 'world_v0_1' and view['protocol']['contract_version'] == V01)
    events = h.sql({'scope_id': f['foreign_scope_id']}, 'SELECT contract_version, kind FROM gov_world_events WHERE scope_id=%s',
                   (f['foreign_scope_id'],))
    check('the_0_1_event_is_written_as_a_0_1_row', [(e['contract_version'], e['kind']) for e in events]
          == [(V01, 'object.created')])
    flow.deny('ceo', flow.command('world_create_object', {'domain_id': f['domains']['company'], 'object_type': 'Company',
                                                          'payload': {'title': 'x'}}, contract=V01),
              codes={'PROTOCOL_BINDING_CONFLICT'})
    check('a_0_1_request_to_a_scope_defaulting_to_0_2_is_refused')
    return {'object': made, 'event_id': str(h.sql({'scope_id': f['foreign_scope_id']},
                                                  'SELECT event_id FROM gov_world_events WHERE scope_id=%s',
                                                  (f['foreign_scope_id'],))[0]['event_id'])}


def revocation(book, h, f, flow, command):
    """撤掉 CEO 的公司域指派后，原 0.2 命令不能重放成成功（重放按 0.2 回执的规则复核）。"""
    ceo = f['actors']['ceo']
    revoke_assignment(h.env, f, ceo['assignment_id'])
    response = flow.clients['ceo'].json('POST', '/v1/actions', deepcopy(command), expected={403})
    book.check('a_revoked_ceo_cannot_replay_the_0_2_creation_into_success',
               response['error']['code'] == 'FORBIDDEN')


def run(book, h, source, upgrade_evidence):
    scenarios = book.metadata['scenarios']

    @contextmanager
    def scenario(name):
        scenarios[name] = {'status': 'running'}
        book.save()
        yield
        scenarios[name] = {'status': 'completed'}
        book.save()

    with scenario('migration'):
        migration(book, h, upgrade_evidence)
    f = seed_world(h.env, h.private / 'identities.json', 'runtime-acceptance-world-v02')
    with scenario('control_plane'):
        control_plane(book, h, source, f)
    process, url, _ = h.start_api(source)
    flow = Flow(h, url, f)
    try:
        with scenario('company'):
            made = company(book, h, f, flow)
        with scenario('objects'):
            trunk = objects(book, h, f, flow, made['company'])
        with scenario('rejections'):
            rejections(book, h, f, flow, made['company'])
        with scenario('coexistence'):
            foreign = coexistence(book, h, f, flow)
        with scenario('references'):
            references(book, h, f, flow, trunk, foreign)
        with scenario('revocation'):
            revocation(book, h, f, flow, made['command'])
    finally:
        flow.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upgrade-evidence', type=Path, required=True,
                        help='method_v05 或 world_v01 的 database upgrade 写的 upgrade.json')
    args = parser.parse_args()
    if not __debug__:
        raise SystemExit('the acceptance oracle is written as assertions; run without -O')
    if args.private.exists() or args.output.exists():
        raise ValueError('Use fresh private and output paths')
    args.private.mkdir(parents=True, mode=0o700)
    source = (ROOT / 'src').resolve()
    h = MethodHarness(args.env_file.resolve(), args.output.resolve(), args.private.resolve())
    book = Book(h.output)
    book.save(scenarios={}, source_root=str(source))
    initial = source_manifest(source)
    error = None
    try:
        run(book, h, source, args.upgrade_evidence.resolve())
    except BaseException as exc:  # noqa: BLE001 - recorded in the report, re-raised after the harness is closed
        error = exc
        details = {'exception_type': type(exc).__name__, 'frames': safe_traceback_frames(exc)}
        for row in book.metadata['scenarios'].values():
            if row['status'] == 'running':
                row.update(status='failed', **details)
        book.save(run_error=details)
    finally:
        try:
            h.close()
        finally:
            unchanged = source_manifest(source) == initial
            result = book.save(source_unchanged=unchanged)
            print(json.dumps({key: result[key] for key in ('passed', 'checks_passed', 'checks_failed',
                                                           'all_scenarios_completed')}))
            if not unchanged and error is None:
                error = RuntimeError('source changed during the acceptance run; rerun on a stable checkout')
    if error is not None:
        raise error
    if result['passed'] is not True:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
