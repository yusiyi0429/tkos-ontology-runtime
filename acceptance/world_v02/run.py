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
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time

from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from acceptance.method_independent.fixture import uid
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import now, public_json, safe_traceback_frames, source_manifest
from acceptance.runtime.client import Client
from acceptance.world_v01.fixture import ROOT, _seed_actor, register_world, revoke_assignment, seed_world
from acceptance.world_v01.flow import Flow as V01Flow
from .fixture import (CONTRACT, PROFILE, REGISTRY, SUPPORT, action_roles, install_activation_policies,
                      owner, owner_rows, probe_binding_gate, probe_event_row, register_world_v02)

V01, V02 = 'tkos.world/0.1', 'tkos.world/0.2'
MIGRATION = '0039_world_v02.sql'
SCENARIOS = ['migration', 'control_plane', 'company', 'objects', 'rejections', 'coexistence', 'references',
             'revise_relate', 'state_events', 'assign_lifecycle', 'revocation']
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

    def target(self, oid):
        """当前最新修订作为目标：0.2 的读投影把修订 id 与对象行的并发版本放在 business 组里。"""
        business = self.read('outsider', oid)['business']
        return {'object_id': oid, 'revision_id': business['revision_id'], 'expected_version': business['object_version']}

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
          == [(V02, support['actions'], support['object_types'])]
          and {'world_create_object', 'world_refresh_state', 'world_record_event'} <= set(support['actions'])
          and set(support['object_types']) == {*TYPES, 'StateSnapshot'} and registered[0]['content'] == support)
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
    later = flow.children('ceo', made['object_id'], expected=409)
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


def assign_lifecycle(book, h, f, flow, trunk):
    """#53：逐级指派（责任单元、Mission、Task、Activity）与 Task、Activity 状态表的每一格各经 HTTP 走一条；
    未列出的组合、越级与错角色指派、Agent 与 Mission Owner 验收 Activity、撤回的三条规则各有拒绝，库快照不变。
    Task 的责任人是人（ic_a），Activity 的责任人是 Agent（agent_a），Agent 的开始、交付与撤回带写入声明。"""
    check = book.check
    made = trunk['made']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}

    def declared(actor, kind, oid):
        """Agent 身份记生命周期事件带写入声明（契约第 9.3 节）；指派不收写入声明。"""
        if f['actors'][actor]['role'] != 'AGENT' or kind == 'world_assign':
            return {}
        return {'declaration': {'scene': f'{oid}@1', 'trigger': '执行', 'human_acceptance': {'required': False}}}

    def act(actor, kind, oid, params=None):
        body = flow.targeted(kind, oid, {**declared(actor, kind, oid), **(params or {})})
        return flow.commit(actor, flow.prepare(actor, body))['result']

    def deny(actor, kind, oid, codes, params=None):
        flow.deny(actor, flow.targeted(kind, oid, {**declared(actor, kind, oid), **(params or {})}), codes=codes)

    def life(oid):
        return flow.read('outsider', oid)['records']['lifecycle']

    def event(event_id):
        return flow.rows('SELECT e.*, r.action_type FROM gov_world_events e JOIN gov_action_receipts r '
                         'ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id WHERE e.scope_id=%s AND e.event_id=%s',
                         (f['scope_id'], event_id))[0]

    # ---- 责任单元：DRI 由 Strategy 的责任人（CEO）指派，只记事件、不出修订。
    unit = made['ResponsibilityUnit']['object_id']
    unit_version = flow.read('outsider', unit)['business']['version']
    deny('a', 'world_assign', unit, {'FORBIDDEN'}, {'principal_id': actor_id['a']})
    deny('ceo', 'world_assign', unit, {'INVALID_REQUEST'}, {'principal_id': actor_id['ic_a']})
    check('a_unit_dri_is_assigned_only_by_the_strategy_responsible_and_only_to_a_domain_dri_of_the_unit')
    assigned = act('ceo', 'world_assign', unit, {'principal_id': actor_id['a']})
    row = event(assigned['event_id'])
    check('assigning_the_unit_dri_records_one_assign_event_without_a_new_revision',
          assigned['version'] == unit_version and assigned['assignee'] == actor_id['a'] and row['kind'] == 'assign'
          and row['contract_version'] == V02 and row['detail'] == {'principal_id': actor_id['a']}
          and EVENT_KINDS['assign']['class'] == 'record' and flow.read('outsider', unit)['records']['lifecycle'] is None)

    # ---- Mission：Owner 由周期目标的 DRI 指派，出新修订写 responsible。
    mission = made['Mission']['object_id']
    mission_version = flow.read('outsider', mission)['business']['version']
    # 夹具里 CEO 也持 a 域的 DOMAIN_DRI（A2 的负例身份），是周期目标的责任人；越级用另一单元的 DRI。
    deny('b', 'world_assign', mission, {'FORBIDDEN'}, {'principal_id': actor_id['owner_a']})
    deny('ic_a', 'world_assign', mission, {'FORBIDDEN'}, {'principal_id': actor_id['owner_a']})
    deny('a', 'world_assign', mission, {'INVALID_REQUEST'}, {'principal_id': actor_id['ic_a']})
    check('a_mission_owner_is_assigned_only_by_the_period_goal_dri_and_only_to_an_owner_of_the_unit')
    assigned = act('a', 'world_assign', mission, {'principal_id': actor_id['owner_a']})
    view = flow.read('outsider', mission)
    check('assigning_the_mission_owner_writes_a_new_revision_and_identity_names_the_owner_by_attribute',
          assigned['version'] == mission_version + 1
          and view['business']['attributes']['responsible'] == actor_id['owner_a']
          and view['identity']['responsible']['source'] == 'attribute'
          and [p['principal_id'] for p in view['identity']['responsible']['principals']] == [actor_id['owner_a']]
          and view['records']['lifecycle']['status'] == 'draft'
          and event(assigned['event_id'])['subject_refs'][0]['revision_id'] == assigned['revision_id'])

    # ---- Task 与 Activity 的状态表逐格。
    def create(object_type, parent_ref, title):
        return flow.create('a', object_type, 'a', {'title': title, 'parent_ref': parent_ref})['result']

    def walk(object_type, parent_ref, parent, responsible, wrong_assigner, wrong_assignee):
        name = object_type.lower()
        main = create(object_type, parent_ref, f'{object_type} 主线')['object_id']
        born = life(main)
        deny(responsible, 'world_start', main, {'INVALID_STATE'})
        check(f'{name}_an_unassigned_{name}_cannot_start')
        deny(wrong_assigner, 'world_assign', main, {'FORBIDDEN'}, {'principal_id': actor_id[responsible]})
        deny(parent, 'world_assign', main, {'INVALID_REQUEST'}, {'principal_id': actor_id[wrong_assignee]})
        deny(parent, 'world_assign', main, {'INVALID_REQUEST'}, {'principal_id': uid()})
        deny('agent_a', 'world_assign', main, {'FORBIDDEN'}, {'principal_id': actor_id[responsible]})
        check(f'{name}_assignment_one_level_up_to_a_holder_of_the_role_only')

        cell = {}
        cell['assign'] = act(parent, 'world_assign', main, {'principal_id': actor_id[responsible]})
        check(f'{name}_unassigned_assign_to_assigned',
              life(main) == {'status': 'assigned', 'display_name': '已指派', 'event_id': cell['assign']['event_id']}
              and born['status'] == 'unassigned')
        view = flow.read('outsider', main)
        check(f'{name}_identity_names_the_assignee_by_attribute',
              view['identity']['responsible']['source'] == 'attribute'
              and [p['principal_id'] for p in view['identity']['responsible']['principals']] == [actor_id[responsible]])

        def self_loop(state):
            before = life(main)
            act(parent, 'world_assign', main, {'principal_id': actor_id[responsible]})
            check(f'{name}_{state}_reassign_keeps_the_stage_and_its_producer',
                  before['status'] == state and life(main) == before)

        self_loop('assigned')
        deny(wrong_assigner if object_type == 'Task' else 'lapsed', 'world_start', main, {'FORBIDDEN'})
        check(f'{name}_only_its_responsible_starts_it')
        start = act(responsible, 'world_start', main)
        check(f'{name}_assigned_start_to_in_progress', life(main)['status'] == 'in_progress'
              and life(main)['event_id'] == start['event_id'])
        self_loop('in_progress')
        content = {'text': '交付说明', 'refs': [parent_ref], 'artifacts': ['https://example.test/delivery']}
        current = flow.read('outsider', main)['business']
        deliver = act(responsible, 'world_deliver', main, {'content': content})
        row = event(deliver['event_id'])
        check(f'{name}_in_progress_deliver_to_delivered_with_a_lifecycle_event_pinned_to_the_current_revision',
              life(main) == {'status': 'delivered', 'display_name': '已交付', 'event_id': deliver['event_id']}
              and row['kind'] == 'deliver' and EVENT_KINDS['deliver']['class'] == 'lifecycle'
              and row['contract_version'] == V02 and row['outcome'] is None and row['supersedes_event_id'] is None
              and str(row['principal_id']) == actor_id[responsible] and row['action_type'] == 'world_deliver'
              and row['subject_refs'] == [{'object_id': main, 'object_version': current['version'],
                                           'revision_id': current['revision_id'], 'block': None, 'component': None}]
              and row['content']['refs'][0]['object_id'] == parent_ref.split('@')[0]
              and row['content']['text'] == '交付说明' and deliver['contract_version'] == V02)
        self_loop('delivered')

        # 撤回：只撤推出当前状态的那条，由同一角色记；撤回事件与更早的事件都不能再撤。
        deny(parent, 'world_deliver', main, {'FORBIDDEN'}, {'outcome': 'withdrawn', 'supersedes_event_id': deliver['event_id']})
        check(f'{name}_a_withdrawal_is_recorded_by_the_role_of_the_original')
        back = act(responsible, 'world_deliver', main, {'outcome': 'withdrawn', 'supersedes_event_id': deliver['event_id']})
        check(f'{name}_withdrawing_the_latest_delivery_returns_to_in_progress_and_keeps_the_original',
              life(main) == {'status': 'in_progress', 'display_name': '进行中', 'event_id': back['event_id']}
              and event(back['event_id'])['outcome'] == 'withdrawn'
              and str(event(back['event_id'])['supersedes_event_id']) == deliver['event_id']
              and event(deliver['event_id'])['kind'] == 'deliver')
        read = {item['event_id']: item for item in flow.events('outsider', main)['events']}
        check(f'{name}_reading_events_shows_the_delivery_withdrawn_by_the_withdrawal',
              read[deliver['event_id']]['withdrawn_by'] == [back['event_id']]
              and read[back['event_id']]['class'] == 'lifecycle' and read[back['event_id']]['outcome'] == 'withdrawn'
              and read[back['event_id']]['action'] == 'world_deliver' and read[back['event_id']]['late'] is False)
        deny(responsible, 'world_start', main, {'INVALID_STATE'},
             {'outcome': 'withdrawn', 'supersedes_event_id': start['event_id']})
        check(f'{name}_an_earlier_event_cannot_be_withdrawn')
        deny(responsible, 'world_deliver', main, {'INVALID_STATE'},
             {'outcome': 'withdrawn', 'supersedes_event_id': back['event_id']})
        check(f'{name}_a_withdrawal_cannot_be_withdrawn')

        act(responsible, 'world_deliver', main)
        reject = act(parent, 'world_reject', main, {'content': {'text': '缺录屏'}})
        check(f'{name}_delivered_reject_to_adjusting', life(main)['status'] == 'adjusting'
              and life(main)['event_id'] == reject['event_id'])
        self_loop('adjusting')
        redeliver = act(responsible, 'world_deliver', main)
        check(f'{name}_adjusting_deliver_to_delivered', life(main)['status'] == 'delivered'
              and life(main)['event_id'] == redeliver['event_id'])
        return main, parent, responsible, cancel_cells(object_type, parent_ref, parent, responsible)

    def cancel_cells(object_type, parent_ref, parent, responsible):
        """取消从五个可取消的状态各走一条，每条用一个新对象。"""
        name = object_type.lower()
        paths = {'unassigned': [], 'assigned': ['world_assign'], 'in_progress': ['world_assign', 'world_start'],
                 'delivered': ['world_assign', 'world_start', 'world_deliver'],
                 'adjusting': ['world_assign', 'world_start', 'world_deliver', 'world_reject']}
        for state, steps in paths.items():
            oid = create(object_type, parent_ref, f'{object_type} 取消于{state}')['object_id']
            for kind in steps:
                actor = parent if kind in ('world_assign', 'world_reject') else responsible
                act(actor, kind, oid, {'principal_id': actor_id[responsible]} if kind == 'world_assign' else None)
            assert life(oid)['status'] == state
            cancelled = act(parent, 'world_cancel', oid)
            check(f'{name}_{state}_cancel_to_cancelled',
                  life(oid) == {'status': 'cancelled', 'display_name': '已取消', 'event_id': cancelled['event_id']})
        return oid

    def close_and_reopen(object_type, main, parent, responsible, cancelled):
        name = object_type.lower()
        accept = act(parent, 'world_accept', main)
        check(f'{name}_delivered_accept_to_closed', life(main) == {'status': 'closed', 'display_name': '已关闭',
                                                                    'event_id': accept['event_id']})
        deny(responsible, 'world_deliver', main, {'INVALID_STATE'})
        deny(parent, 'world_assign', main, {'INVALID_STATE'}, {'principal_id': actor_id[responsible]})
        deny(parent, 'world_assign', cancelled, {'INVALID_STATE'}, {'principal_id': actor_id[responsible]})
        deny(parent, 'world_cancel', cancelled, {'INVALID_STATE'})
        check(f'{name}_a_closed_or_cancelled_{name}_is_not_delivered_reassigned_or_cancelled_again')
        reopen = act(parent, 'world_reopen', main)
        check(f'{name}_closed_reopen_to_in_progress', life(main)['status'] == 'in_progress'
              and life(main)['event_id'] == reopen['event_id'])

    task_ref = made['Task']['ref']
    mission_ref = made['Mission']['ref']
    task, parent, responsible, cancelled = walk('Task', mission_ref, 'owner_a', 'ic_a', 'a', 'agent_a')
    deny('ic_a', 'world_accept', task, {'FORBIDDEN'})
    deny('a', 'world_accept', task, {'FORBIDDEN'})
    check('task_only_the_mission_owner_accepts_a_task')
    close_and_reopen('Task', task, parent, responsible, cancelled)

    # Activity 的上一级是 Task 的责任人：把主干上的 Task 指派给 ic_a；Activity 由 Agent 执行。
    act('owner_a', 'world_assign', made['Task']['object_id'], {'principal_id': actor_id['ic_a']})
    activity, parent, responsible, cancelled = walk('Activity', task_ref, 'ic_a', 'agent_a', 'owner_a', 'ceo')
    flow.deny('agent_a', flow.targeted('world_start', activity, {}), codes={'INVALID_REQUEST'})
    check('activity_an_agent_start_or_delivery_carries_a_declaration')
    deny('agent_a', 'world_accept', activity, {'FORBIDDEN'})
    deny('owner_a', 'world_accept', activity, {'FORBIDDEN'})
    check('activity_an_agent_delivery_is_not_accepted_by_the_agent_or_the_mission_owner')
    close_and_reopen('Activity', activity, parent, responsible, cancelled)
    check('activity_the_agent_delivery_was_accepted_by_the_task_responsible',
          str(flow.rows("SELECT principal_id FROM gov_world_events WHERE scope_id=%s AND kind='accept' "
                        "AND subject_refs->0->>'object_id'=%s", (f['scope_id'], activity))[0]['principal_id'])
          == actor_id['ic_a'])
    # 票 #51、#50 留下的补验：Agent 经指派成为 Activity 的责任人后可以修订它，触及正式块的声明要求人工验收；
    # Agent 写入声明的场景可以是周期目标。
    revised = flow.revise('agent_a', activity, {'blocks': {'instruction': {'text': '按新脚本录屏。'}}},
                          {'scene': made['PeriodGoal']['ref'], 'trigger': '执行中调整',
                           'human_acceptance': {'required': True, 'acceptor': actor_id['ic_a']}})['result']
    check('an_agent_assigned_to_an_activity_revises_it_with_a_declaration_whose_scene_is_a_period_goal',
          revised['responsible_through'] == activity
          and revised['declaration']['human_acceptance']['acceptor'] == actor_id['ic_a']
          and revised['declaration']['scene']['ref'] == made['PeriodGoal']['ref'])


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
    flow.deny('ceo', flow.command('world_grant_delegation', {'delegate_principal_id': f['actors']['a']['principal_id']}),
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


def revise_relate(book, h, f, flow, trunk):
    """#51：合并修订按 id 合并组件、台账记删除、删除后 id 不复用、组件不换块，旧版本的组件引用钉在旧修订；
    周期目标的 depends_on 指向周期目标与 Mission；Agent 修订的声明按触及的块类别；每次修订恰好一条事件一张回执。"""
    check = book.check
    made = trunk['made']
    mission, task, goal = made['Mission'], made['Task'], made['PeriodGoal']
    scope = f['scope_id']

    def one_event_one_receipt(receipt, kind, subjects):
        events = flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                           (scope, receipt['receipt_id']))
        receipts = flow.rows('SELECT 1 FROM gov_action_receipts WHERE scope_id=%s AND receipt_id=%s',
                             (scope, receipt['receipt_id']))
        result = receipt['result']
        return (len(events) == 1 and len(receipts) == 1 and events[0]['kind'] == kind
                and events[0]['contract_version'] == V02 and str(events[0]['event_id']) == result['event_id']
                and events[0]['subject_refs'] == [{'object_id': result['object_id'], 'object_version': result['version'],
                                                   'revision_id': result['revision_id'], 'block': None,
                                                   'component': None}, *subjects])

    def deny_revise(actor, oid, patch, codes, says=None, declaration=None, target=None):
        params = {'payload': patch, **({'declaration': declaration} if declaration else {})}
        # 期望版本与目标修订在提交时才核对，prepare 不碰它们。
        flow.deny(actor, flow.targeted('world_revise_object', oid, params, target), codes=codes, says=says,
                  prepare=target is None)

    def deny_relate(actor, oid, field, refs, codes, says=None):
        flow.deny(actor, flow.targeted('world_relate', oid, {'field': field, 'refs': refs}), codes=codes, says=says)

    # 版本 2：改写验收条件与计划条目（id 不换），追加一条带 id 的计划条目。
    v1 = flow.read('a', mission['object_id'])
    criterion = blocks_of(v1)['acceptance']['components'][0]['id']
    revised = flow.revise('a', mission['object_id'], {'blocks': {
        'acceptance': {'components': [{'id': criterion, 'type': 'acceptance_criterion', 'text': '客户书面签字'}]},
        'execution_plan': {'components': [{'id': 'plan-1', 'type': 'plan_item', 'text': '搭环境（已完成）'},
                                          {'id': 'plan-2', 'type': 'plan_item', 'text': '联调'}]}}})
    check('each_revision_writes_exactly_one_object_revised_event_and_one_receipt',
          revised['result']['version'] == 2 and revised['result']['contract_version'] == V02
          and one_event_one_receipt(revised, 'object.revised', []))
    v2 = flow.read('a', mission['object_id'])
    acceptance, plan = blocks_of(v2)['acceptance'], blocks_of(v2)['execution_plan']
    check('a_rewritten_component_keeps_its_id_and_untouched_fields_and_blocks_stay',
          [c['id'] for c in acceptance['components']] == [criterion] and acceptance['components'][0]['text'] == '客户书面签字'
          and acceptance['components'][0]['scope'] is None
          and [(c['id'], c['text']) for c in plan['components']] == [('plan-1', '搭环境（已完成）'), ('plan-2', '联调')]
          and v2['business']['title'] == v1['business']['title']
          and v2['business']['relations'][0]['value'] == v1['business']['relations'][0]['value'])
    check('an_older_version_still_reads_back_as_it_was',
          [c['text'] for c in blocks_of(flow.read('a', mission['object_id'], version=1))['execution_plan']['components']]
          == ['搭环境'])
    check('a_gated_draft_keeps_no_effective_revision',
          v2['business']['formal'] == {'lifecycle_status': 'draft', 'effective_revision_id': None})

    # 旧版本的组件引用钉在旧修订；新版本里同一 id 可找到。
    task_view = flow.read('a', task['object_id'])
    old_ref = blocks_of(task_view)['acceptance']['components'][0]['refs'][0]
    check('a_component_reference_to_the_older_version_stays_pinned_to_that_revision',
          old_ref['ref'] == f"{mission['ref']}#acceptance/{criterion}" and old_ref['revision_id'] == mission['revision_id'])
    new_ref = f"{mission['object_id']}@2#acceptance/{criterion}"
    task_revised = flow.revise('a', task['object_id'], {'blocks': {'acceptance': {'components': [
        {'type': 'acceptance_criterion', 'text': '演示通过（按新验收）', 'refs': [new_ref]}]}}})
    task_view = flow.read('a', task['object_id'])
    components = blocks_of(task_view)['acceptance']['components']
    check('the_same_component_id_is_found_in_the_newer_version',
          [c['refs'][0]['revision_id'] for c in components] == [mission['revision_id'], revised['result']['revision_id']]
          and components[1]['refs'][0]['ref'] == new_ref)
    check('an_ungated_object_moves_its_effective_revision_with_the_revision',
          task_view['business']['formal']['effective_revision_id'] == task_revised['result']['revision_id'])

    # 版本 3：删除一条计划条目，台账记删除版本号。
    flow.revise('a', mission['object_id'], {'blocks': {'execution_plan': {'components': [{'id': 'plan-2', 'removed': True}]}}})
    ledger = {e['id']: e for e in flow.read('a', mission['object_id'])['business']['component_ledger']}
    check('a_removed_component_is_recorded_in_the_ledger_with_its_removal_version',
          ledger['plan-2'] == {'id': 'plan-2', 'type': 'plan_item', 'block': 'execution_plan', 'added_in_version': 2,
                               'removed_in_version': 3}
          and ledger['plan-1']['removed_in_version'] is None and ledger[criterion]['removed_in_version'] is None)
    deny_revise('a', mission['object_id'], {'blocks': {'execution_plan': {'components': [
        {'id': 'plan-2', 'type': 'plan_item', 'text': '联调'}]}}}, {'INVALID_REQUEST'}, 'not reused')
    check('reusing_a_removed_component_id_is_refused')
    deny_revise('a', mission['object_id'], {'blocks': {'acceptance': {'components': [
        {'id': 'plan-1', 'type': 'acceptance_criterion', 'text': '搬过来'}]}}}, {'INVALID_REQUEST'}, 'move between blocks')
    deny_revise('a', mission['object_id'], {'blocks': {
        'execution_plan': {'components': [{'id': 'plan-1', 'removed': True}]},
        'acceptance': {'components': [{'id': 'plan-1', 'type': 'acceptance_criterion', 'text': '搬过来'}]}}},
        {'INVALID_REQUEST'}, 'move between blocks')
    check('moving_a_component_to_another_block_is_refused_even_when_removed_in_the_same_revision')
    deny_revise('a', mission['object_id'], {'blocks': {'execution_plan': {'components': [
        {'id': 'plan-9', 'removed': True}]}}}, {'INVALID_REQUEST'}, 'present in that block')
    check('removing_a_component_not_present_in_that_block_is_refused')

    # 版本 4：块给 null 清空，其中的组件在台账记删除。
    cleared = flow.revise('a', mission['object_id'], {'blocks': {'execution_plan': None}})
    v4 = flow.read('a', mission['object_id'])
    ledger = {e['id']: e for e in v4['business']['component_ledger']}
    check('a_null_block_clears_it_and_records_its_components_as_removed',
          cleared['result']['version'] == 4 and blocks_of(v4)['execution_plan']['empty']
          and ledger['plan-1']['removed_in_version'] == 4 and ledger['plan-2']['removed_in_version'] == 3)

    # 拒绝：非责任人、持 DRI 角色的 Agent、改挂关系、只由服务写的字段、错期望版本、非最新修订。
    deny_revise('ic_a', mission['object_id'], {'title': 'x'}, {'FORBIDDEN'}, 'responsible person up the spine')
    check('only_a_responsible_person_up_the_spine_revises')
    other_goal = flow.create('a', 'PeriodGoal', 'a', {'title': '11 月目标', 'period': '2026-11',
                                                     'goal_ref': made['LongTermGoal.unit']['ref']})['result']
    deny_revise('a', mission['object_id'], {'goal_ref': other_goal['ref']}, {'INVALID_REQUEST'}, 're-pinned')
    check('a_creation_relation_cannot_be_hung_on_another_object')
    deny_revise('a', mission['object_id'], {'responsible': f['actors']['owner_a']['principal_id']}, {'INVALID_REQUEST'})
    check('a_field_written_only_by_the_service_cannot_be_revised')
    current = flow.target(mission['object_id'])
    deny_revise('a', mission['object_id'], {'title': 'x'}, {'VERSION_CONFLICT'},
                target={**current, 'expected_version': current['expected_version'] + 5})
    check('a_wrong_expected_version_is_refused_for_a_revision')
    deny_revise('a', mission['object_id'], {'title': 'x'}, {'STALE_DEPENDENCY'},
                target={**current, 'revision_id': mission['revision_id']})
    check('a_target_that_is_not_the_latest_revision_is_refused')

    # Agent：声明必带；触及正式块而不要求人工验收在判责任人之前就被拒；只触及活动块的声明可以不要求人工验收，
    # 但仍须是责任人（Agent 只经指派成为 Activity 的责任人，指派随票 #53，成功路径在那之后补验）。
    unattended = {'scene': goal['ref'], 'trigger': '周会同步', 'human_acceptance': {'required': False}}
    deny_revise('agent_a', task['object_id'], {'blocks': {'plan': {'text': '改计划'}}}, {'INVALID_REQUEST'},
                'must declare')
    check('an_agent_revision_without_a_declaration_is_refused')
    deny_revise('agent_a', task['object_id'], {'title': '改标题'}, {'INVALID_REQUEST'}, 'needs human acceptance',
                unattended)
    deny_revise('agent_a', task['object_id'], {'blocks': {'definition': {'text': '改定义'}}}, {'INVALID_REQUEST'},
                'needs human acceptance', unattended)
    check('an_agent_revision_touching_formal_content_without_human_acceptance_is_refused')
    deny_revise('agent_a', task['object_id'], {'blocks': {'plan': {'text': '改计划'}},
                                               'external_refs': [{'system': 'tianshu', 'id': 'todo-1'}]},
                {'FORBIDDEN'}, 'responsible person up the spine', unattended)
    check('an_agent_revision_touching_only_activity_content_passes_the_declaration_rule_and_still_needs_responsibility')
    deny_revise('agent', goal['object_id'], {'title': 'x'}, {'FORBIDDEN'}, 'responsible person up the spine',
                {**unattended, 'human_acceptance': {'required': True, 'acceptor': f['actors']['ceo']['principal_id']}})
    check('an_agent_holding_the_dri_role_is_not_the_period_goals_responsible')

    # 建关系：周期目标的 depends_on 指向周期目标与 Mission。
    related = flow.relate('a', goal['object_id'], 'depends_on', [other_goal['ref'], mission['ref']])
    subjects = [{'object_id': other_goal['object_id'], 'object_version': 1, 'revision_id': other_goal['revision_id'],
                 'block': None, 'component': None},
                {'object_id': mission['object_id'], 'object_version': 1, 'revision_id': mission['revision_id'],
                 'block': None, 'component': None}]
    check('a_relation_writes_one_relate_event_pinning_the_new_revision_and_each_related_object',
          one_event_one_receipt(related, 'relate', subjects))
    relations = {r['field']: r['value'] for r in flow.read('a', goal['object_id'])['business']['relations']}
    check('a_period_goal_depends_on_a_period_goal_and_a_mission',
          [item['ref'] for item in relations['depends_on']] == [other_goal['ref'], mission['ref']]
          and relations['goal_ref']['ref'] == made['LongTermGoal.unit']['ref'])
    revised_goal = flow.revise('a', goal['object_id'], {'title': '10 月目标（改）'})
    relations = {r['field']: r['value'] for r in flow.read('a', goal['object_id'])['business']['relations']}
    check('a_revision_keeps_the_relations_written_by_relate',
          revised_goal['result']['version'] == 3 and [item['ref'] for item in relations['depends_on']]
          == [other_goal['ref'], mission['ref']])
    deny_relate('a', goal['object_id'], 'depends_on', [goal['ref']], {'INVALID_REQUEST'}, 'itself')
    check('a_period_goal_cannot_depend_on_itself')
    deny_relate('a', goal['object_id'], 'depends_on', [other_goal['ref'], other_goal['object_id'] + '@1'],
                {'INVALID_REQUEST'})
    check('a_relation_list_naming_an_object_twice_is_refused')
    deny_relate('a', goal['object_id'], 'depends_on', [task['ref']], {'INVALID_REQUEST'}, 'must point to one of')
    check('a_period_goal_depends_only_on_period_goals_and_missions')
    deny_relate('a', goal['object_id'], 'contributes_to', [other_goal['ref']], {'INVALID_REQUEST'}, 'has no contributes_to')
    check('a_period_goal_has_no_contributes_to')
    deny_relate('ic_a', goal['object_id'], 'depends_on', [other_goal['ref']], {'FORBIDDEN'})
    check('only_a_responsible_person_up_the_spine_relates')
    deny_relate('agent', goal['object_id'], 'depends_on', [other_goal['ref']], {'FORBIDDEN'}, 'Agent face')
    check('relating_is_not_on_the_agent_face')

    body = flow.prepare('a', flow.targeted('world_revise_object', task['object_id'], {'payload': {
        'blocks': {'plan': {'text': '按新验收排期'}}}}))
    receipt = flow.commit('a', body)
    replay = flow.commit('a', deepcopy(body))
    check('replaying_a_revision_returns_the_original_receipt_and_writes_nothing_more',
          replay['receipt_id'] == receipt['receipt_id'] and one_event_one_receipt(receipt, 'object.revised', []))


def utc(moment):
    """UTC 规范文本（同服务端的写法）。"""
    text = moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return text + (f'.{moment.microsecond:06d}' if moment.microsecond else '') + 'Z'


def at(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def state_events(book, h, f, flow, trunk, foreign):
    """#52：状态快照的统一外壳与五种 payload、生成者、来源事件、同一时点唯一；外部事件的补记、迟记与更正；
    取状态按时点、取事件按发生时刻并带迟记与被更正关系、读投影的 records 给最新快照并标明未经确认。"""
    check = book.check
    made = trunk['made']
    mission, unit, goal, strategy, company = (made['Mission'], made['ResponsibilityUnit'], made['PeriodGoal'],
                                              made['Strategy'], made['Company'])
    scope = f['scope_id']

    def event_rows(where='TRUE', params=()):
        return flow.rows('SELECT * FROM gov_world_events WHERE scope_id=%s AND ' + where + ' ORDER BY recorded_at',
                         (scope, *params))

    def declare(obj, trigger='每周同步'):
        return {'scene': obj['ref'], 'trigger': trigger, 'human_acceptance': {'required': False}}

    # 时点都落在 Mission 已有的事件（建对象与 #51 的修订）之后、此刻之前，这样只有有意补记的那几条算迟记。
    created = event_rows('subject_refs @> %s', (Jsonb([{'object_id': mission['object_id']}]),))[-1]['occurred_at']
    while datetime.now(timezone.utc) < created + timedelta(seconds=4):
        time.sleep(0.2)
    t_sync, t_early, t_late = (utc(created + timedelta(seconds=n)) for n in (1, 2, 3))

    # 来源事件：天枢每周同步，先记外部事件（Agent 带写入声明），再写快照引用它。
    sync = flow.record('agent_a', {'category': 'other', 'subject_refs': [mission['ref']], 'occurred_at': t_sync,
                                   'content': {'text': '天枢每周同步 2026-W39'}, 'declaration': declare(mission)})
    synced = sync['result']
    row = event_rows('event_id=%s', (synced['event_id'],))[0]
    check('an_agent_records_an_external_event_under_0_2_with_its_category_and_past_occurrence',
          synced['contract_version'] == V02 and synced['occurred_at'] == t_sync
          and synced['subject_refs'][0]['ref'] == mission['ref'] and synced['declaration']['scene']['ref'] == mission['ref']
          and (row['kind'], row['category'], row['contract_version']) == ('event.recorded', 'other', V02)
          and utc(row['occurred_at']) == t_sync and row['occurred_at'] < row['recorded_at']
          and str(row['action_id']) == sync['receipt_id'])
    source = f"event:{synced['event_id']}"

    def shell(subject, payload_type, as_of, blocks=None, **extra):
        return {'title': f'{payload_type} 快照', 'subject_ref': subject['ref'], 'as_of': as_of,
                'payload_type': payload_type, 'source_event_refs': [source], 'blocks': blocks or {}, **extra}

    issue = {'id': 'iss-1', 'type': 'issue', 'text': '试点排期冲突',
             'attributes': {'core_question': '要不要把试点推迟一周？',
                            'responsible_hint': f['actors']['owner_a']['principal_id']}}
    progress = {'id': 'todo:17', 'type': 'progress_item', 'text': '搭环境', 'attributes': {
        'principal_id': f['actors']['owner_a']['principal_id'], 'principal_name': '执行人甲', 'external_status': '进行中',
        'entries': [{'at': '2026-09-26T10:00:00+08:00', 'source': 'codex', 'text': '完成脚手架'}]}}
    written = {}
    written['execution_state'] = flow.refresh('agent_a', shell(mission, 'execution_state', t_late, {
        'progress': {'components': [progress]}, 'blockers': {'text': '等客户排期'},
        'issues': {'components': [issue]}, 'materials': {'artifacts': ['https://example.test/w39']}}, period='2026-09'),
        declare(mission))
    goal_body = flow.prepare('agent_a', flow.command('world_refresh_state', {
        'payload': shell(goal, 'goal_state', t_late, {'progress': {'text': '签了 1 家'}}),
        'declaration': declare(goal, '周期检查')}))
    written['goal_state'] = flow.commit('agent_a', goal_body)
    written['unit_state'] = flow.refresh('agent_a', shell(unit, 'unit_state', t_late, {
        'issues': {'components': [{**issue, 'id': 'unit-iss-1'}]}}), declare(unit, '单元周报'))
    written['strategy_state'] = flow.refresh('agent_company', shell(strategy, 'strategy_state', t_late, {
        'materials': {'text': '战略候选稿', 'artifacts': ['https://example.test/strategy-draft']}}),
        declare(strategy, '战略复盘'))
    written['company_review'] = flow.refresh('ceo', shell(company, 'company_review', t_late, {
        'results': {'text': '九月收入达成八成'}, 'gaps': {'text': 'B 战场落后'}}))
    writers = {'execution_state': 'agent_a', 'goal_state': 'agent_a', 'unit_state': 'agent_a',
               'strategy_state': 'agent_company', 'company_review': 'ceo'}
    views = {name: flow.read('ceo', receipt['result']['object_id']) for name, receipt in written.items()}
    check('each_of_the_five_payload_types_is_written_and_read_back_as_an_unconfirmed_time_record',
          all(view['payload_type']['id'] == name and view['unconfirmed'] is True
              and view['category']['id'] == 'time_record' and view['as_of'] == t_late
              and view['source_event_refs'] == [{'event_id': synced['event_id'], 'ref': source}]
              and view['protocol']['interpretation_status'] == 'world_v0_2'
              for name, view in views.items()))
    check('the_generator_is_the_principal_of_the_writing_credential',
          all(views[name]['generator']['principal_id'] == f['actors'][actor]['principal_id']
              and written[name]['result']['generator'] == f['actors'][actor]['principal_id']
              for name, actor in writers.items()))
    execution = {b['id']: b for b in views['execution_state']['blocks']}
    check('the_execution_payload_keeps_progress_items_issue_components_and_normalised_entries',
          [b for b in execution] == ['progress', 'blockers', 'issues', 'materials']
          and execution['progress']['components'][0]['attributes']['entries']
          == [{'at': '2026-09-26T02:00:00Z', 'source': 'codex', 'text': '完成脚手架', 'url': None}]
          and execution['issues']['components'][0]['ref'] == written['execution_state']['result']['ref'] + '#issues/iss-1'
          and execution['issues']['components'][0]['attributes']['core_question'] == '要不要把试点推迟一周？'
          and views['execution_state']['period'] == '2026-09')
    check('an_agent_declares_a_unit_or_a_goal_as_the_scene_of_its_snapshot',
          written['unit_state']['result']['declaration']['scene']['ref'] == unit['ref']
          and written['goal_state']['result']['declaration']['scene']['ref'] == goal['ref'])
    refreshed = event_rows("kind='state.refreshed'")
    check('each_snapshot_wrote_one_state_refreshed_event_that_happened_at_its_as_of_and_pins_snapshot_and_subject',
          len(refreshed) == 5 and all(utc(e['occurred_at']) == t_late and e['contract_version'] == V02
                                      and e['occurred_at'] < e['recorded_at'] and len(e['subject_refs']) == 2
                                      for e in refreshed)
          and {e['subject_refs'][1]['object_id'] for e in refreshed}
          == {o['object_id'] for o in (mission, goal, unit, strategy, company)})

    # 人：主体主干上的责任人（单元 DRI 为 Task 写快照），不带声明。
    task = made['Task']
    human = flow.refresh('a', shell(task, 'execution_state', t_early))['result']
    check('a_responsible_person_up_the_spine_writes_a_snapshot_without_a_declaration',
          flow.read('a', human['object_id'])['generator']['principal_id'] == f['actors']['a']['principal_id'])

    # 拒绝：错误码与库快照不变。
    def deny(actor, payload, codes, declaration=None, **deny_options):
        params = {'payload': payload, **({'declaration': declaration} if declaration else {})}
        flow.deny(actor, flow.command('world_refresh_state', params), codes=codes, **deny_options)

    deny('a', shell(mission, 'goal_state', t_early), {'INVALID_REQUEST'})
    check('a_payload_type_that_is_not_the_subjects_is_refused')
    no_source = shell(mission, 'execution_state', t_early)
    del no_source['source_event_refs']
    deny('a', no_source, {'INVALID_REQUEST'})
    deny('a', shell(mission, 'execution_state', t_early, source_event_refs=[]), {'INVALID_REQUEST'})
    check('a_snapshot_without_a_source_event_is_refused')
    deny('a', shell(mission, 'execution_state', t_early, source_event_refs=[f"event:{foreign['event_id']}"]),
         {'INVALID_REQUEST'})
    check('a_source_event_outside_the_scope_is_refused')
    deny('a', shell(mission, 'execution_state', utc(datetime.now(timezone.utc) + timedelta(days=1))), {'INVALID_REQUEST'})
    check('a_snapshot_as_of_the_future_is_refused')
    deny('a', shell(mission, 'execution_state', t_early, generator=f['actors']['a']['principal_id']), {'INVALID_REQUEST'})
    check('a_request_that_names_the_generator_is_refused')
    deny('agent_a', shell(mission, 'execution_state', t_early, {'issues': {'components': [
        {'type': 'issue', 'text': '缺核心判断问题'}]}}), {'INVALID_REQUEST'}, declare(mission))
    check('an_issue_component_without_its_core_question_is_refused_over_http')
    deny('agent_a', shell(mission, 'execution_state', t_early), {'INVALID_REQUEST'})
    check('an_agent_snapshot_without_a_declaration_is_refused')
    deny('ic_a', shell(mission, 'execution_state', t_early), {'FORBIDDEN'})
    check('a_person_who_is_not_responsible_up_the_spine_cannot_write_the_snapshot')
    deny('agent_a', shell(company, 'company_review', t_early), {'FORBIDDEN'}, declare(company))
    check('an_agent_without_the_agent_role_in_the_subjects_domain_cannot_write_the_snapshot')
    deny('a', shell({'ref': written['execution_state']['result']['ref']}, 'execution_state', t_early), {'INVALID_REQUEST'})
    check('a_snapshot_is_not_the_subject_of_a_snapshot')
    other_form = at(t_late).astimezone(timezone(timedelta(hours=8))).isoformat()
    deny('agent_a', shell(mission, 'execution_state', other_form), {'INVALID_STATE'}, declare(mission), prepare=False)
    check('a_second_snapshot_of_the_same_subject_at_the_same_moment_is_refused_whatever_the_offset')

    # 同键重放返回原回执。
    replay = flow.clients['agent_a'].json('POST', '/v1/actions', deepcopy(goal_body))
    check('replaying_a_snapshot_with_the_same_key_returns_the_original_receipt',
          replay['receipt_id'] == written['goal_state']['receipt_id'] and len(event_rows("kind='state.refreshed'")) == 6)

    # 补记：一条更早的外部事件与一条更早时点的快照，都晚于同一主体已有的事件记下。
    backdated_at = utc(created - timedelta(days=1))
    backdated = flow.record('outsider', {'category': 'meeting', 'subject_refs': [mission['ref']],
                                         'occurred_at': backdated_at, 'content': {'text': '九月立项前的沟通会'}})['result']
    early = flow.refresh('a', shell(mission, 'execution_state', t_early, {'progress': {'text': '补写的中间快照'}}))['result']
    listed = flow.events('ceo', mission['object_id'])['events']
    order = [(e['kind'], at(e['occurred_at'])) for e in listed]
    by_id = {e['event_id']: e for e in listed}
    check('events_are_read_in_occurrence_order_and_backdated_ones_are_marked_late',
          order == sorted(order, key=lambda item: item[1]) and order[0] == ('event.recorded', at(backdated_at))
          and by_id[backdated['event_id']]['late'] is True and by_id[early['event_id']]['late'] is True
          and by_id[synced['event_id']]['late'] is False
          and by_id[written['execution_state']['result']['event_id']]['late'] is False
          and [e['occurred_at'] for e in listed if e['kind'] == 'state.refreshed'] == [t_early, t_late])
    check('a_scope_member_from_another_domain_records_an_external_event_by_the_scope_rule',
          by_id[backdated['event_id']]['principal']['principal_id'] == f['actors']['outsider']['principal_id'])
    sync_view = by_id[synced['event_id']]
    check('each_event_carries_its_class_recorder_producing_action_and_relations',
          sync_view['class'] == 'record' and sync_view['action'] == 'world_record_event'
          and sync_view['action_id'] == sync['receipt_id'] and sync_view['category'] == 'other'
          and sync_view['principal'] == {'principal_id': f['actors']['agent_a']['principal_id'], 'principal_type': 'agent',
                                         'display_name': 'Synthetic world AGENT'}
          and sync_view['on_behalf_of'] is None and sync_view['external_confirmation'] is None
          and sync_view['corrected_by'] == [] and sync_view['withdrawn_by'] == [])
    since = flow.events('ceo', mission['object_id'], since=t_sync)['events']
    check('events_since_a_moment_start_at_that_occurrence',
          [e['event_id'] for e in since] == [e['event_id'] for e in listed if at(e['occurred_at']) >= at(t_sync)])

    # 取状态按时点；读投影 records 给按 as_of 最新的那条，而不是最后记下的那条。
    at_early = flow.state('ceo', mission['object_id'], as_of=utc(created + timedelta(seconds=2, milliseconds=500)))
    check('the_state_at_a_moment_is_the_newest_snapshot_as_of_that_moment',
          at_early['snapshot']['object_id'] == early['object_id']
          and flow.state('ceo', mission['object_id'])['snapshot']['object_id'] == written['execution_state']['result']['object_id']
          and flow.state('ceo', mission['object_id'], as_of=backdated_at)['snapshot'] is None)
    mission_view = flow.read('ceo', mission['object_id'])
    check('the_records_group_gives_the_latest_snapshot_marked_unconfirmed',
          mission_view['records']['latest_state']['object_id'] == written['execution_state']['result']['object_id']
          and mission_view['records']['latest_state']['unconfirmed'] is True)

    # 更正：只对外部事件（与 Issue 事件）；原事件不变，读取给出被更正关系。
    before_row = event_rows('event_id=%s', (synced['event_id'],))[0]
    correction = flow.record('agent_a', {'category': 'correction', 'subject_refs': [mission['ref']],
                                         'occurred_at': t_sync, 'supersedes_event_id': synced['event_id'],
                                         'content': {'text': '同步周次写错，应为 2026-W40', 'refs': [source]},
                                         'declaration': declare(mission, '更正')})['result']
    corrected = {e['event_id']: e for e in flow.events('ceo', mission['object_id'])['events']}
    check('a_correction_of_an_external_event_is_recorded_and_read_back_as_the_corrected_relation',
          corrected[synced['event_id']]['corrected_by'] == [correction['event_id']]
          and corrected[correction['event_id']]['supersedes_event_id'] == synced['event_id']
          and corrected[correction['event_id']]['content']['refs'] == [{'event_id': synced['event_id'], 'ref': source}]
          and event_rows('event_id=%s', (synced['event_id'],))[0] == before_row)
    creation_event = event_rows("kind='object.created' AND subject_refs @> %s",
                                (Jsonb([{'object_id': mission['object_id']}]),))[0]['event_id']
    for original in (creation_event, written['execution_state']['result']['event_id']):
        flow.deny('a', flow.command('world_record_event', {
            'category': 'correction', 'subject_refs': [mission['ref']], 'occurred_at': t_sync,
            'supersedes_event_id': str(original), 'content': {'text': '更正'}}), codes={'INVALID_REQUEST'})
    check('a_correction_of_a_non_external_event_is_refused')
    flow.deny('a', flow.command('world_record_event', {
        'category': 'correction', 'subject_refs': [mission['ref']], 'occurred_at': t_sync,
        'supersedes_event_id': foreign['event_id'], 'content': {'text': '更正'}}), codes={'INVALID_REQUEST'})
    check('a_correction_of_an_event_in_another_scope_is_refused')

    # 外部事件的其余拒绝。
    def deny_event(actor, codes, **params):
        body = {'category': 'meeting', 'subject_refs': [mission['ref']], 'occurred_at': t_sync,
                'content': {'text': '会议'}, **params}
        flow.deny(actor, flow.command('world_record_event', {k: v for k, v in body.items() if v is not None}),
                  codes=codes)

    deny_event('a', {'INVALID_REQUEST'}, occurred_at=utc(datetime.now(timezone.utc) + timedelta(days=1)))
    check('an_external_event_that_has_not_happened_yet_is_refused')
    deny_event('a', {'INVALID_REQUEST'}, category=None)
    check('an_external_event_without_a_category_is_refused')
    deny_event('agent_a', {'INVALID_REQUEST'})
    check('an_agent_external_event_without_a_declaration_is_refused')
    deny_event('a', {'INVALID_REQUEST'}, subject_refs=[])
    check('an_external_event_without_a_subject_is_refused')

    # 快照里的组件可以作事件主体：以组件形式指向问题组件，同时钉住所在的快照。
    issue_ref = written['execution_state']['result']['ref'] + '#issues/iss-1'
    about = flow.record('a', {'category': 'review', 'subject_refs': [issue_ref, mission['ref']], 'occurred_at': t_late,
                              'content': {'text': '评审了排期冲突'}})['result']
    snapshot_events = flow.events('ceo', written['execution_state']['result']['object_id'])['events']
    check('a_component_of_a_snapshot_can_be_the_subject_of_an_event',
          about['subject_refs'][0]['component'] == 'iss-1' and about['subject_refs'][0]['ref'] == issue_ref
          and about['event_id'] in [e['event_id'] for e in snapshot_events])

    # 库的最后一道：发生时刻不晚于记录时刻，除外部事件与状态刷新外发生即记录。
    check('the_event_log_refuses_an_external_event_that_happens_after_it_is_recorded',
          probe_event_row(h.env, f, V02, kind='event.recorded', category='other',
                          occurred_at='2999-01-01T00:00:00Z') == 'ck_gov_world_event_v02')
    check('the_event_log_takes_a_backdated_external_event',
          probe_event_row(h.env, f, V02, kind='event.recorded', category='other') is None)
    check('the_event_log_refuses_backdating_any_other_kind',
          probe_event_row(h.env, f, V02, kind='object.created') == 'ck_gov_world_event_v02')
    check('every_0_2_event_written_so_far_keeps_occurrence_no_later_than_recording',
          all(e['occurred_at'] <= e['recorded_at'] and (e['kind'] in ('event.recorded', 'state.refreshed')
                                                        or e['occurred_at'] == e['recorded_at'])
              for e in event_rows()))


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
    # 在公司域持 AGENT 的 Agent：为 Strategy 写快照（#52）。
    f['actors']['agent_company'] = _seed_actor(h.env, f, 'AGENT', f['domains']['company'], principal_type='agent')
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
        with scenario('revise_relate'):
            revise_relate(book, h, f, flow, trunk)
        with scenario('state_events'):
            state_events(book, h, f, flow, trunk, foreign)
        with scenario('assign_lifecycle'):
            assign_lifecycle(book, h, f, flow, trunk)
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
