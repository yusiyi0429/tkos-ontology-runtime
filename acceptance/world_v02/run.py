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

from acceptance.composition_a2_independent.storage import immutable_owner_probe
from acceptance.method_independent.fixture import uid
from acceptance.method_independent.harness import MethodHarness
from acceptance.protocol_a1_independent.support import now, public_json, safe_traceback_frames, source_manifest
from acceptance.runtime.client import Client
from acceptance.world_v01.fixture import ROOT, _grant, _seed_actor, register_world, revoke_assignment, seed_world
from acceptance.world_v01.flow import Flow as V01Flow
from .agent_face import mcp_end_to_end
from .goal_closure import goal_closure
from .issues import issues
from .listing import list_objects
from .strategy import strategy_gates
from .fixture import (CONTRACT, PROFILE, REGISTRY, SUPPORT, action_roles, install_activation_policies,
                      owner, owner_rows, probe_binding_gate, probe_event_row, register_world_v02)

V01, V02 = 'tkos.world/0.1', 'tkos.world/0.2'
MIGRATION = '0039_world_v02.sql'
SCENARIOS = ['migration', 'control_plane', 'company', 'objects', 'rejections', 'coexistence', 'references',
             'revise_relate', 'state_events', 'assign_lifecycle', 'gates', 'context_packs', 'mission_lifecycle',
             'delegation', 'mcp_end_to_end', 'list_objects', 'issues', 'goal_closure', 'strategy_gates',
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


def gates(book, h, f, flow, trunk):
    """#54：周期目标、长期目标与 Mission 立项各走一遍承诺、确认接受、确认退回与撤回，读回生命周期与正式内容指针；
    带候选的确认接受写回新修订（活动内容取当前值），不带候选接受当时的最新修订；一轮重走开轮、退回作废、写回
    且状态不变，一轮未完不能再开；写回过后让对象成为正式的那条确认不能撤回；Mission 立项查父周期目标已确认；
    有门对象的块类别修订规则（正式块只在草稿直接改，活动块由责任人、下级责任人与 Co-Agent 直接改）；门只由人记。
    周期目标的形成锚定（#60）：主干的单元长期目标还是草稿时承诺被拒，CEO 确认它之后放行（scope 里还没有已确认的公司
    复盘，周期目标不带 review_ref）；此后各场景的周期目标都挂在这条已确认的长期目标上。"""
    check = book.check
    made = trunk['made']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    scope = f['scope_id']

    def gate(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    def deny(actor, kind, oid, codes, params=None, says=None):
        flow.deny(actor, flow.targeted(kind, oid, params or {}), codes=codes, says=says)

    def deny_revise(actor, oid, patch, codes, says=None, declaration=None):
        params = {'payload': patch, **({'declaration': declaration} if declaration else {})}
        flow.deny(actor, flow.targeted('world_revise_object', oid, params), codes=codes, says=says)

    def view(oid):
        return flow.read('outsider', oid)

    def life(oid):
        return view(oid)['records']['lifecycle']

    def formal(oid):
        return view(oid)['business']['formal']

    def event(event_id):
        return flow.rows('SELECT e.*, r.action_type FROM gov_world_events e JOIN gov_action_receipts r '
                         'ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id WHERE e.scope_id=%s AND e.event_id=%s',
                         (scope, event_id))[0]

    def pinned_to(result):
        """事件钉到的修订：门事件、写回与否都钉在回执给出的那一版。"""
        return event(result['event_id'])['subject_refs'][0]['revision_id'] == result['revision_id']

    def content_of(oid, *blocks):
        """几个块的文字与组件（id、类型、文字），比较正式内容用。"""
        read = blocks_of(view(oid))
        return {b: (read[b]['text'], [(c['id'], c['type'], c['text']) for c in read[b]['components']]) for b in blocks}

    def withdraw(actor, kind, oid, original):
        return gate(actor, kind, oid, {'outcome': 'withdrawn', 'supersedes_event_id': original['event_id']})

    unit_goal, company_goal, unit = made['LongTermGoal.unit'], made['LongTermGoal'], made['ResponsibilityUnit']

    # ================================================================ 周期目标
    pg = flow.create('a', 'PeriodGoal', 'a', {
        'title': '11 月目标（门）', 'period': '2026-11', 'goal_ref': unit_goal['ref'],
        'external_refs': [{'system': 'tianshu', 'id': 'pg-54'}],
        'blocks': {'outcome': {'components': [{'id': 'g-o1', 'type': 'outcome', 'text': '签 3 家'}]},
                   'acceptance': {'components': [{'id': 'g-ac1', 'type': 'acceptance_criterion', 'text': '合同签署'}]}}}
    )['result']
    pid = pg['object_id']
    born = life(pid)
    check('a_period_goal_is_born_a_draft_without_formal_content',
          born['status'] == 'draft' and born['event_id'] == pg['event_id']
          and formal(pid) == {'lifecycle_status': 'draft', 'effective_revision_id': None}
          and view(pid)['business']['round'] is None)

    # 门只由人记：持 DRI 角色的 Agent 也不能承诺；确认只由 CEO；门不带写入声明；确认必带结果；候选只含正式内容。
    deny('agent', 'world_commit_period_goal', pid, {'FORBIDDEN'}, says='recorded by a person')
    deny('a', 'world_confirm_period_goal', pid, {'FORBIDDEN'}, {'outcome': 'accepted'})
    check('period_goal_a_gate_is_recorded_by_a_person_holding_the_gate_role_and_an_agent_with_the_role_is_refused')
    deny('ceo', 'world_confirm_period_goal', pid, {'INVALID_STATE'}, {'outcome': 'accepted'})
    check('period_goal_nothing_committed_cannot_be_confirmed')
    deny('a', 'world_commit_period_goal', pid, {'INVALID_REQUEST'},
         {'declaration': {'scene': pg['ref'], 'trigger': 'x', 'human_acceptance': {'required': False}}})
    deny('ceo', 'world_confirm_period_goal', pid, {'INVALID_REQUEST'})
    deny('a', 'world_commit_period_goal', pid, {'INVALID_REQUEST'},
         {'payload': {'external_refs': [{'system': 'tianshu', 'id': 'pg-x'}]}}, says='only formal blocks')
    deny('a', 'world_commit_period_goal', pid, {'INVALID_REQUEST'},
         {'payload': {'blocks': {'acceptance': {'components': [{'id': 'g-o1', 'type': 'acceptance_criterion'}]}}}})
    check('period_goal_a_gate_takes_no_declaration_a_confirmation_needs_its_outcome_and_a_candidate_only_formal_content')

    # 形成锚定（#60）：挂在草稿长期目标上的承诺被拒；CEO 确认主干的单元长期目标之后放行。
    deny('a', 'world_commit_period_goal', pid, {'INVALID_STATE'}, says='formation_anchors')
    gate('ceo', 'world_confirm_long_term_goal', unit_goal['object_id'], {'outcome': 'accepted'})
    check('period_goal_a_commitment_waits_for_its_long_term_goal_to_be_confirmed')

    # 承诺、撤回承诺；再承诺、退回。
    committed = gate('a', 'world_commit_period_goal', pid)
    row = event(committed['event_id'])
    check('period_goal_draft_commit_to_committed_with_a_gate_event_pinned_to_the_latest_revision',
          life(pid) == {'status': 'committed', 'display_name': '已承诺', 'event_id': committed['event_id']}
          and row['kind'] == 'commit' and EVENT_KINDS['commit']['class'] == 'gate' and row['contract_version'] == V02
          and row['outcome'] is None and row['detail'] is None and str(row['principal_id']) == actor_id['a']
          and row['action_type'] == 'world_commit_period_goal' and committed['version'] == pg['version']
          and pinned_to(committed) and committed['revision_id'] == pg['revision_id']
          and formal(pid) == {'lifecycle_status': 'draft', 'effective_revision_id': None})
    withdrawn = withdraw('a', 'world_commit_period_goal', pid, committed)
    check('period_goal_withdrawing_the_commitment_returns_to_draft_and_keeps_the_original',
          life(pid) == {'status': 'draft', 'display_name': '草稿', 'event_id': withdrawn['event_id']}
          and event(withdrawn['event_id'])['outcome'] == 'withdrawn'
          and str(event(withdrawn['event_id'])['supersedes_event_id']) == committed['event_id']
          and event(committed['event_id'])['kind'] == 'commit')
    gate('a', 'world_commit_period_goal', pid)
    returned = gate('ceo', 'world_confirm_period_goal', pid, {'outcome': 'returned', 'content': {'text': '验收标准不可测'}})
    check('period_goal_committed_confirm_returned_to_draft_with_the_reason_in_the_event',
          life(pid) == {'status': 'draft', 'display_name': '草稿', 'event_id': returned['event_id']}
          and event(returned['event_id'])['outcome'] == 'returned'
          and event(returned['event_id'])['content']['text'] == '验收标准不可测'
          and formal(pid) == {'lifecycle_status': 'draft', 'effective_revision_id': None})

    # 带候选承诺；已承诺时正式内容不能直接改，活动属性照改；CEO 接受时写回候选，活动属性取当前值。
    candidate = {'title': '11 月目标（门，候选）', 'blocks': {
        'outcome': {'text': '签 5 家'},
        'acceptance': {'components': [{'type': 'acceptance_criterion', 'text': '回款到账'}]}}}
    committed = gate('a', 'world_commit_period_goal', pid, {'payload': candidate})
    kept = event(committed['event_id'])['detail']['candidate']
    new_id = kept['blocks']['acceptance']['components'][0]['id']
    check('period_goal_a_commitment_keeps_its_candidate_in_the_event_with_ids_for_new_components',
          life(pid)['status'] == 'committed' and committed['version'] == pg['version']
          and kept == {**candidate, 'blocks': {**candidate['blocks'], 'acceptance': {'components': [
              {'id': new_id, 'type': 'acceptance_criterion', 'text': '回款到账'}]}}}
          and len(new_id) == 36 and view(pid)['business']['round'] is None)
    deny_revise('a', pid, {'title': '直接改'}, {'INVALID_STATE'}, 'revised directly only while it is a draft')
    check('period_goal_committed_formal_content_is_not_revised_directly')
    refs = [{'system': 'tianshu', 'id': 'pg-54'}, {'system': 'tianshu', 'id': 'pg-54-b', 'url': None}]
    before_confirm = flow.revise('a', pid, {'external_refs': refs})['result']
    check('period_goal_committed_activity_attributes_are_revised_directly',
          before_confirm['version'] == pg['version'] + 1 and life(pid)['status'] == 'committed')
    confirmed = gate('ceo', 'world_confirm_period_goal', pid, {'outcome': 'accepted'})
    written = view(pid)
    outcome, acceptance = blocks_of(written)['outcome'], blocks_of(written)['acceptance']
    check('period_goal_confirm_accepted_writes_back_the_candidate_and_keeps_the_current_activity_attributes',
          life(pid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': confirmed['event_id']}
          and confirmed['version'] == before_confirm['version'] + 1 == written['business']['version']
          and written['business']['title'] == candidate['title'] and outcome['text'] == '签 5 家'
          and [c['id'] for c in outcome['components']] == ['g-o1']
          and [(c['id'], c['text']) for c in acceptance['components']] == [('g-ac1', '合同签署'), (new_id, '回款到账')]
          and written['business']['attributes']['external_refs'] == [{**refs[0], 'url': None}, refs[1]]
          and pinned_to(confirmed))
    check('period_goal_the_first_formal_confirmation_points_formal_content_at_the_written_back_revision',
          formal(pid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': confirmed['revision_id']})
    formal_content = content_of(pid, 'outcome', 'acceptance', 'realization_logic')

    # 撤回让内容成为正式的那条确认：正式内容收回；再确认时承诺的候选原样写回（组件同一批 id）。
    withdrawn = withdraw('ceo', 'world_confirm_period_goal', pid, confirmed)
    check('period_goal_withdrawing_the_formal_confirmation_takes_formal_content_back',
          life(pid) == {'status': 'committed', 'display_name': '已承诺', 'event_id': withdrawn['event_id']}
          and formal(pid) == {'lifecycle_status': 'draft', 'effective_revision_id': None}
          and view(pid)['business']['revision_id'] == confirmed['revision_id'])
    confirmed = gate('ceo', 'world_confirm_period_goal', pid, {'outcome': 'accepted'})
    check('period_goal_confirming_again_writes_the_committed_candidate_back_as_it_was',
          life(pid)['event_id'] == confirmed['event_id']
          and confirmed['version'] == written['business']['version'] + 1
          and content_of(pid, 'outcome', 'acceptance', 'realization_logic') == formal_content
          and formal(pid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': confirmed['revision_id']})
    formal_confirmation = confirmed

    # 一轮重走：有正式内容后正式块不能直接改；开轮要带候选、状态不变；一轮未完不能再开；开轮的承诺不能撤回；
    # 退回则候选作废；再开一轮由 CEO 接受写回，状态与推出它的事件不变。
    deny_revise('a', pid, {'blocks': {'realization_logic': {'text': '直接改'}}}, {'INVALID_STATE'}, 're-run of the gate')
    check('period_goal_with_formal_content_formal_blocks_change_only_through_a_re_run')
    deny('a', 'world_commit_period_goal', pid, {'INVALID_STATE'}, says='carries the candidate')
    check('period_goal_a_re_run_commitment_carries_a_candidate')
    rerun = {'blocks': {'realization_logic': {'text': '先两场试点，再复制到三家。'}}}
    opened = gate('a', 'world_commit_period_goal', pid, {'payload': rerun})
    business = view(pid)['business']
    check('period_goal_a_re_run_opens_without_changing_the_stage_and_business_shows_the_round',
          life(pid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': formal_confirmation['event_id']}
          and business['round'] == {'opened_by_event_id': opened['event_id'], 'stage': 'committed',
                                    'display_name': '已承诺', 'candidate_event_id': opened['event_id'],
                                    'candidate': rerun}
          and business['formal'] == {'lifecycle_status': 'confirmed',
                                     'effective_revision_id': formal_confirmation['revision_id']}
          and opened['version'] == formal_confirmation['version'])
    deny('a', 'world_commit_period_goal', pid, {'INVALID_STATE'}, {'payload': rerun}, says='still open')
    check('period_goal_a_round_cannot_open_while_one_is_unfinished')
    deny('a', 'world_commit_period_goal', pid, {'INVALID_STATE'},
         {'outcome': 'withdrawn', 'supersedes_event_id': opened['event_id']})
    check('period_goal_the_commitment_of_a_round_cannot_be_withdrawn')
    back = gate('ceo', 'world_confirm_period_goal', pid, {'outcome': 'returned', 'content': {'text': '实现逻辑还不清楚'}})
    check('period_goal_a_returned_round_is_void_and_nothing_is_written_back',
          view(pid)['business']['round'] is None and back['version'] == formal_confirmation['version']
          and life(pid)['event_id'] == formal_confirmation['event_id']
          and content_of(pid, 'outcome', 'acceptance', 'realization_logic') == formal_content)
    rerun = {'blocks': {'realization_logic': {'text': '两场试点后复制到三家。'}, 'constraint': {'text': '预算 20 万'}}}
    opened = gate('a', 'world_commit_period_goal', pid, {'payload': rerun})
    rewritten = gate('ceo', 'world_confirm_period_goal', pid, {'outcome': 'accepted'})
    written = view(pid)
    check('period_goal_the_confirmer_accepting_the_round_writes_it_back_and_the_stage_stays',
          life(pid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': formal_confirmation['event_id']}
          and rewritten['version'] == formal_confirmation['version'] + 1 and written['business']['round'] is None
          and blocks_of(written)['realization_logic']['text'] == '两场试点后复制到三家。'
          and blocks_of(written)['constraint']['text'] == '预算 20 万'
          and content_of(pid, 'outcome', 'acceptance') == {k: formal_content[k] for k in ('outcome', 'acceptance')}
          and written['business']['formal'] == {'lifecycle_status': 'confirmed',
                                                'effective_revision_id': rewritten['revision_id']}
          and pinned_to(rewritten) and str(event(opened['event_id'])['subject_refs'][0]['revision_id'])
          == formal_confirmation['revision_id'])
    deny('ceo', 'world_confirm_period_goal', pid, {'INVALID_STATE'},
         {'outcome': 'withdrawn', 'supersedes_event_id': formal_confirmation['event_id']}, says='rewritten')
    check('period_goal_once_a_round_wrote_back_the_confirmation_that_made_it_formal_cannot_be_withdrawn')
    flow.deny('ceo', flow.targeted('world_confirm_period_goal', pid, {'outcome': 'accepted'}), codes={'INVALID_STATE'})
    check('period_goal_no_round_open_no_confirmation')

    # ================================================================ 长期目标
    ltg = flow.create('a', 'LongTermGoal', 'a', {
        'title': '战场 A 三年目标（门）', 'scope': 'unit', 'horizon': '2028', 'parent_ref': unit['ref'],
        'goal_ref': company_goal['ref'],
        'blocks': {'measures': {'components': [{'id': 'l-sc1', 'type': 'success_criterion', 'text': '三年签 20 家'}]}}}
    )['result']
    lid = ltg['object_id']
    deny('a', 'world_confirm_long_term_goal', lid, {'FORBIDDEN'}, {'outcome': 'accepted'})
    deny('ceo', 'world_confirm_long_term_goal', lid, {'INVALID_REQUEST'},
         {'outcome': 'returned', 'payload': {'horizon': '2029'}})
    deny('ceo', 'world_confirm_long_term_goal', lid, {'INVALID_REQUEST'},
         {'outcome': 'accepted', 'payload': {'external_refs': []}}, says='only formal blocks')
    check('long_term_goal_only_the_ceo_confirms_and_a_candidate_travels_only_with_an_acceptance_of_formal_content')
    back = gate('ceo', 'world_confirm_long_term_goal', lid, {'outcome': 'returned', 'content': {'text': '衡量太粗'}})
    check('long_term_goal_draft_confirm_returned_stays_a_draft_and_keeps_its_producer',
          life(lid) == {'status': 'draft', 'display_name': '草稿', 'event_id': ltg['event_id']}
          and event(back['event_id'])['outcome'] == 'returned' and back['version'] == ltg['version'])
    candidate = {'horizon': '2029', 'blocks': {'measures': {'components': [
        {'type': 'success_criterion', 'text': '三年签 30 家'}, {'id': 'l-sc1', 'removed': True}]}}}
    confirmed = gate('ceo', 'world_confirm_long_term_goal', lid, {'outcome': 'accepted', 'payload': candidate})
    written = view(lid)
    measures = blocks_of(written)['measures']['components']
    check('long_term_goal_draft_confirm_accepted_with_a_candidate_writes_it_back_and_makes_it_formal',
          life(lid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': confirmed['event_id']}
          and confirmed['version'] == ltg['version'] + 1 and written['business']['attributes']['horizon'] == '2029'
          and [c['text'] for c in measures] == ['三年签 30 家']
          and event(confirmed['event_id'])['detail']['candidate']['blocks']['measures']['components'][0]['id']
          == measures[0]['id']
          and formal(lid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': confirmed['revision_id']}
          and pinned_to(confirmed))
    withdrawn = withdraw('ceo', 'world_confirm_long_term_goal', lid, confirmed)
    check('long_term_goal_withdrawing_the_formal_confirmation_takes_formal_content_back',
          life(lid) == {'status': 'draft', 'display_name': '草稿', 'event_id': withdrawn['event_id']}
          and formal(lid) == {'lifecycle_status': 'draft', 'effective_revision_id': None})
    latest = flow.revise('ceo', lid, {'title': '战场 A 三年目标（门，改）'})['result']
    confirmed = gate('ceo', 'world_confirm_long_term_goal', lid, {'outcome': 'accepted'})
    check('long_term_goal_a_confirmation_without_candidate_makes_the_then_latest_revision_formal',
          life(lid)['event_id'] == confirmed['event_id'] and confirmed['version'] == latest['version']
          and confirmed['revision_id'] == latest['revision_id']
          and formal(lid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': latest['revision_id']})
    formal_confirmation = confirmed
    deny_revise('ceo', lid, {'horizon': '2030'}, {'INVALID_STATE'}, 're-run of the gate')
    moved = flow.revise('ceo', lid, {'external_refs': [{'system': 'tianshu', 'id': 'ltg-54'}]})['result']
    check('long_term_goal_confirmed_formal_attributes_are_not_revised_directly_and_activity_attributes_are',
          formal(lid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': moved['revision_id']}
          and life(lid)['event_id'] == formal_confirmation['event_id'])
    deny('ceo', 'world_confirm_long_term_goal', lid, {'INVALID_STATE'}, {'outcome': 'accepted'})
    deny('ceo', 'world_confirm_long_term_goal', lid, {'INVALID_STATE'}, {'outcome': 'returned'})
    check('long_term_goal_a_confirmed_goal_is_confirmed_again_only_with_a_candidate')
    rewritten = gate('ceo', 'world_confirm_long_term_goal', lid, {'outcome': 'accepted', 'payload': {'horizon': '2030'}})
    written = view(lid)
    check('long_term_goal_a_confirmation_with_a_candidate_rewrites_it_in_one_step_and_the_stage_stays',
          life(lid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': formal_confirmation['event_id']}
          and rewritten['version'] == moved['version'] + 1 and written['business']['attributes']['horizon'] == '2030'
          and written['business']['attributes']['external_refs'] == [{'system': 'tianshu', 'id': 'ltg-54', 'url': None}]
          and written['business']['round'] is None
          and formal(lid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': rewritten['revision_id']})
    deny('ceo', 'world_confirm_long_term_goal', lid, {'INVALID_STATE'},
         {'outcome': 'withdrawn', 'supersedes_event_id': formal_confirmation['event_id']}, says='rewritten')
    check('long_term_goal_once_rewritten_the_confirmation_that_made_it_formal_cannot_be_withdrawn')

    # ================================================================ Mission 立项
    # 守卫：父周期目标未确认时承诺被拒；承诺之后父目标的确认被撤回，确认接受同样被拒，退回不查守卫。
    guard_goal = flow.create('a', 'PeriodGoal', 'a', {'title': '12 月目标（门）', 'period': '2026-12',
                                                     'goal_ref': unit_goal['ref']})['result']
    guarded = flow.create('a', 'Mission', 'a', {'title': '守卫试点', 'goal_ref': guard_goal['ref']})['result']
    flow.assign('a', guarded['object_id'], actor_id['owner_a'])
    deny('owner_a', 'world_commit_mission', guarded['object_id'], {'INVALID_STATE'}, says='parent_goal_confirmed')
    check('mission_a_commitment_is_refused_while_its_period_goal_is_not_confirmed')
    gate('a', 'world_commit_period_goal', guard_goal['object_id'])
    goal_confirmed = gate('ceo', 'world_confirm_period_goal', guard_goal['object_id'], {'outcome': 'accepted'})
    gate('owner_a', 'world_commit_mission', guarded['object_id'])
    withdraw('ceo', 'world_confirm_period_goal', guard_goal['object_id'], goal_confirmed)
    deny('a', 'world_confirm_mission', guarded['object_id'], {'INVALID_STATE'}, {'outcome': 'accepted'},
         says='parent_goal_confirmed')
    returned = gate('a', 'world_confirm_mission', guarded['object_id'], {'outcome': 'returned'})
    check('mission_an_accepting_confirmation_is_refused_once_the_period_goal_is_no_longer_confirmed_and_a_return_is_not',
          life(guarded['object_id']) == {'status': 'draft', 'display_name': '草稿', 'event_id': returned['event_id']})

    goal_now = view(pid)['business']
    mission = flow.create('a', 'Mission', 'a', {
        'title': '试点（门）', 'goal_ref': f"{pid}@{goal_now['version']}",
        'blocks': {'definition': {'text': '在两家客户做试点。'}, 'play': {'text': 'Play 核心路径：两场试点。'},
                   'acceptance': {'components': [{'id': 'm-ac1', 'type': 'acceptance_criterion', 'text': '客户签字',
                                                  'refs': [f"{pid}@{goal_now['version']}#acceptance/g-ac1"]}]},
                   'execution_plan': {'components': [{'id': 'm-p1', 'type': 'plan_item', 'text': '搭环境'}]}}}
    )['result']
    mid = mission['object_id']
    flow.assign('a', mid, actor_id['owner_a'])
    deny('owner_a2', 'world_commit_mission', mid, {'FORBIDDEN'}, says='recorded by self')
    deny('a', 'world_commit_mission', mid, {'FORBIDDEN'})
    check('mission_only_its_owner_in_person_commits_it')
    deny('agent', 'world_confirm_mission', mid, {'FORBIDDEN'}, {'outcome': 'accepted'}, says='recorded by a person')
    deny('owner_a', 'world_confirm_mission', mid, {'FORBIDDEN'}, {'outcome': 'accepted'})
    check('mission_only_the_dri_confirms_and_an_agent_holding_the_dri_role_cannot')

    committed = gate('owner_a', 'world_commit_mission', mid)
    check('mission_draft_commit_to_committed_by_the_owner',
          life(mid) == {'status': 'committed', 'display_name': '已承诺', 'event_id': committed['event_id']}
          and committed['responsible_through'] == mid and event(committed['event_id'])['kind'] == 'commit')
    withdrawn = withdraw('owner_a', 'world_commit_mission', mid, committed)
    check('mission_withdrawing_the_commitment_returns_to_draft',
          life(mid) == {'status': 'draft', 'display_name': '草稿', 'event_id': withdrawn['event_id']})
    gate('owner_a', 'world_commit_mission', mid)
    returned = gate('a', 'world_confirm_mission', mid, {'outcome': 'returned', 'content': {'text': '打法太散'}})
    check('mission_committed_confirm_returned_to_draft',
          life(mid) == {'status': 'draft', 'display_name': '草稿', 'event_id': returned['event_id']}
          and formal(mid) == {'lifecycle_status': 'draft', 'effective_revision_id': None})
    candidate = {'blocks': {'play': {'text': 'Play 核心路径：一场试点加一次复盘。'}}}
    gate('owner_a', 'world_commit_mission', mid, {'payload': candidate})
    deny_revise('owner_a', mid, {'blocks': {'play': {'text': '直接改'}}}, {'INVALID_STATE'}, 'while it is a draft')
    planned = flow.revise('owner_a', mid, {'blocks': {'execution_plan': {'components': [
        {'id': 'm-p2', 'type': 'plan_item', 'text': '约客户'}]}}})['result']
    confirmed = gate('a', 'world_confirm_mission', mid, {'outcome': 'accepted'})
    written = view(mid)
    check('mission_committed_confirm_accepted_to_established_writing_back_the_play_and_keeping_the_execution_plan',
          life(mid) == {'status': 'established', 'display_name': '已成立', 'event_id': confirmed['event_id']}
          and confirmed['version'] == planned['version'] + 1
          and blocks_of(written)['play']['text'] == candidate['blocks']['play']['text']
          and [c['id'] for c in blocks_of(written)['execution_plan']['components']] == ['m-p1', 'm-p2']
          and written['business']['attributes']['responsible'] == actor_id['owner_a']
          and formal(mid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': confirmed['revision_id']})
    formal_confirmation = confirmed

    # 已成立：正式块不能直接改（Owner、持声明的 Agent）；Co-Agent 直接改执行计划、声明可以不要求人工验收；
    # 下级责任人（Task 的责任人）也可以直接改执行计划，但改不了正式块。
    deny_revise('owner_a', mid, {'blocks': {'play': {'text': '直接改打法'}}}, {'INVALID_STATE'}, 're-run of the gate')
    unattended = {'scene': f'{mid}@1', 'trigger': '周会同步执行计划', 'human_acceptance': {'required': False}}
    deny_revise('agent_a', mid, {'blocks': {'play': {'text': 'Agent 改打法'}}}, {'FORBIDDEN'}, None,
                {**unattended, 'human_acceptance': {'required': True, 'acceptor': actor_id['owner_a']}})
    check('mission_established_formal_blocks_are_not_revised_directly')
    co_agent = flow.revise('agent_a', mid, {'blocks': {'execution_plan': {'components': [
        {'id': 'm-p1', 'type': 'plan_item', 'text': '搭环境（已完成）'}]}}}, unattended)['result']
    check('mission_established_the_co_agent_revises_the_execution_plan_without_human_acceptance',
          co_agent['declaration']['human_acceptance'] == {'required': False}
          and 'responsible_through' not in co_agent
          and formal(mid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': co_agent['revision_id']}
          and life(mid)['event_id'] == formal_confirmation['event_id'])
    task = flow.create('owner_a', 'Task', 'a', {'title': '准备试点环境', 'parent_ref': f"{mid}@{co_agent['version']}"})
    task = task['result']
    flow.assign('owner_a', task['object_id'], actor_id['ic_a'])
    deny_revise('ic_a', mid, {'blocks': {'play': {'text': 'Task 责任人改打法'}}}, {'FORBIDDEN'})
    below = flow.revise('ic_a', mid, {'blocks': {'execution_plan': {'components': [
        {'id': 'm-p3', 'type': 'plan_item', 'text': '写部署脚本',
         'attributes': {'responsible': actor_id['ic_a']}}]}}})['result']
    check('mission_a_responsible_below_it_revises_the_execution_plan_but_not_its_formal_blocks',
          below['responsible_through'] == task['object_id'] and below['version'] == co_agent['version'] + 1)

    # 一轮重走：Owner 带候选承诺开轮、状态不变；一轮未完不能再开；重走期间改执行计划不被写回覆盖；DRI 接受写回。
    deny('owner_a', 'world_commit_mission', mid, {'INVALID_STATE'}, says='carries the candidate')
    rerun = {'blocks': {'play': {'text': 'Play 核心路径变化：先复盘再扩到第二家。'},
                        'acceptance': {'components': [{'id': 'm-ac1', 'type': 'acceptance_criterion',
                                                       'text': '客户书面签字'}]}}}
    opened = gate('owner_a', 'world_commit_mission', mid, {'payload': rerun})
    check('mission_the_owner_opens_a_round_with_a_candidate_and_the_stage_stays',
          life(mid) == {'status': 'established', 'display_name': '已成立', 'event_id': formal_confirmation['event_id']}
          and view(mid)['business']['round'] == {'opened_by_event_id': opened['event_id'], 'stage': 'committed',
                                                 'display_name': '已承诺', 'candidate_event_id': opened['event_id'],
                                                 'candidate': rerun}
          and opened['responsible_through'] == mid)
    deny('owner_a', 'world_commit_mission', mid, {'INVALID_STATE'}, {'payload': rerun}, says='still open')
    check('mission_a_round_cannot_open_while_one_is_unfinished')
    during = flow.revise('owner_a', mid, {'blocks': {'execution_plan': {'components': [
        {'id': 'm-p4', 'type': 'plan_item', 'text': '复盘会'}]}}})['result']
    plan = [(c['id'], c['text']) for c in blocks_of(view(mid))['execution_plan']['components']]
    rewritten = gate('a', 'world_confirm_mission', mid, {'outcome': 'accepted'})
    written = view(mid)
    check('mission_the_dri_accepting_the_round_writes_it_back_the_stage_stays_and_the_execution_plan_is_not_overwritten',
          life(mid) == {'status': 'established', 'display_name': '已成立', 'event_id': formal_confirmation['event_id']}
          and rewritten['version'] == during['version'] + 1 and written['business']['round'] is None
          and blocks_of(written)['play']['text'] == rerun['blocks']['play']['text']
          and [(c['id'], c['text']) for c in blocks_of(written)['acceptance']['components']] == [('m-ac1', '客户书面签字')]
          and [(c['id'], c['text']) for c in blocks_of(written)['execution_plan']['components']] == plan
          and [c for c, _ in plan] == ['m-p1', 'm-p2', 'm-p3', 'm-p4'] and plan[0][1] == '搭环境（已完成）'
          and written['business']['attributes']['responsible'] == actor_id['owner_a']
          and formal(mid) == {'lifecycle_status': 'confirmed', 'effective_revision_id': rewritten['revision_id']}
          and pinned_to(rewritten))
    deny('a', 'world_confirm_mission', mid, {'INVALID_STATE'},
         {'outcome': 'withdrawn', 'supersedes_event_id': formal_confirmation['event_id']}, says='rewritten')
    check('mission_once_a_round_wrote_back_the_confirmation_that_made_it_established_cannot_be_withdrawn')

    # 门事件恰好一条、一张回执；同键重放返回原回执。
    body = flow.prepare('owner_a', flow.targeted('world_commit_mission', mid, {'payload': {'title': '试点（门，二轮）'}}))
    receipt = flow.commit('owner_a', body)
    replay = flow.commit('owner_a', deepcopy(body))
    rows = flow.rows('SELECT kind FROM gov_world_events WHERE scope_id=%s AND action_id=%s', (scope, receipt['receipt_id']))
    check('a_gate_writes_exactly_one_event_and_replaying_it_returns_the_original_receipt',
          replay['receipt_id'] == receipt['receipt_id'] and [r['kind'] for r in rows] == ['commit']
          and receipt['result']['contract_version'] == V02)


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
    check('a_period_goal_review_reference_points_to_a_state_snapshot')
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


def context_packs(book, h, f, flow, trunk, foreign):
    """#56：从 Activity 出发取上下文（0.2）。沿主干取块与组件、执行链上的快照外壳与近期事件；验收条件以组件引用、
    事件以事件引用返回，包里每条钉定的引用都能按版本读回；生命周期、正式内容与责任人同 0.2 读投影；预算、裁剪、
    覆盖、检索计划、落表与默认值同 0.1；同一世界状态两次调用结果相同；scope 外 404；0.1 对象仍按 0.1 取。"""
    check = book.check
    made = trunk['made']
    scope, foreign_scope = f['scope_id'], f['foreign_scope_id']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    question = {'question': '这条 Activity 为什么做、做什么、谁负责、现在怎样？'}

    def declared(ref, trigger):
        return {'scene': ref, 'trigger': trigger, 'human_acceptance': {'required': False}}

    def act(actor, kind, oid, params):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params)))['result']

    def packs(scope_id=scope):
        return h.sql({'scope_id': scope_id}, 'SELECT * FROM gov_world_context_packs WHERE scope_id=%s '
                                             'ORDER BY created_at, context_pack_id', (scope_id,))

    # 在主干 Mission 下建一条带验收条件的 Task 与它的 Activity：Mission 的 Owner 指派 Task，Task 的责任人把 Activity
    # 指派给 Agent；Agent 开始执行，记一次以 Task 的验收条件与 Activity 为主体的评审，再写两条引用它的快照。
    mission = flow.read('outsider', made['Mission']['object_id'])
    criterion = blocks_of(mission)['acceptance']['components'][0]['ref']
    task = flow.create('a', 'Task', 'a', {
        'title': '上下文 Task', 'parent_ref': f"{mission['object_id']}@{mission['business']['version']}", 'blocks': {
            'definition': {'text': '把 0.2 的取上下文接给执行 Agent。'},
            'acceptance': {'text': '两条验收。', 'components': [
                {'id': 'ctx-ac1', 'type': 'acceptance_criterion', 'text': '引用细到组件', 'refs': [criterion]},
                {'id': 'ctx-ac2', 'type': 'acceptance_criterion', 'text': '事件以事件引用给出'}]},
            'plan': {'components': [{'id': 'ctx-p1', 'type': 'plan_item', 'text': '先写测试',
                                     'attributes': {'responsible': actor_id['ic_a']}}]}}})['result']
    act('owner_a', 'world_assign', task['object_id'], {'principal_id': actor_id['ic_a']})
    activity = flow.create('a', 'Activity', 'a', {'title': '上下文 Activity', 'parent_ref': task['ref'], 'blocks': {
        'instruction': {'text': '按验收条件实现取上下文。', 'refs': [task['ref'] + '#acceptance/ctx-ac1']}}})['result']
    act('ic_a', 'world_assign', activity['object_id'], {'principal_id': actor_id['agent_a']})
    started = act('agent_a', 'world_start', activity['object_id'], {'declaration': declared(activity['ref'], '开工')})
    # 评审与快照的时点都落在开始之后、此刻之前，这样都不算迟记。
    base = at(flow.events('outsider', activity['object_id'])['events'][-1]['occurred_at'])
    while datetime.now(timezone.utc) < base + timedelta(seconds=4):
        time.sleep(0.2)
    t_review, t_first, t_second = (utc(base + timedelta(seconds=n)) for n in (1, 2, 3))
    review = flow.record('agent_a', {'category': 'review', 'occurred_at': t_review,
                                     'subject_refs': [task['ref'] + '#acceptance/ctx-ac1', activity['ref']],
                                     'content': {'text': '评审组件级引用的做法'},
                                     'declaration': declared(activity['ref'], '评审')})['result']
    source = f"event:{review['event_id']}"

    def snapshot(as_of, text):
        return flow.refresh('agent_a', {
            'title': '上下文 Activity 进展', 'subject_ref': activity['ref'], 'as_of': as_of,
            'payload_type': 'execution_state', 'source_event_refs': [source],
            'blocks': {'progress': {'text': text}, 'issues': {'components': [
                {'id': 'ctx-iss-1', 'type': 'issue', 'text': '裁剪以块为单位',
                 'attributes': {'core_question': '裁块时组件要不要单独留？'}}]}}},
            declared(activity['ref'], '每日同步'))['result']

    first_snapshot, latest_snapshot = snapshot(t_first, '写完了测试'), snapshot(t_second, '接好了路由')

    before = len(packs())
    first = flow.context('agent_a', activity['object_id'], question)
    pack, plan, coverage = first['context_pack'], first['plan'], first['coverage']
    layers, markdown = pack['layers'], pack['markdown']
    spine = ['Activity', 'Task', 'Mission', 'PeriodGoal', 'LongTermGoal', 'ResponsibilityUnit', 'Strategy', 'Company']
    projected = {layer['object']['object_id']: flow.read('outsider', layer['object']['object_id']) for layer in layers}
    check('the_0_2_context_walks_the_spine_from_the_activity_up_to_the_company_reading_each_latest_version',
          first['contract_version'] == V02 and pack['contract_version'] == V02
          and [layer['object']['object_type'] for layer in layers] == spine
          and all(layer['object']['ref'] == f"{oid}@{projected[oid]['business']['version']}"
                  and layer['object']['pinned']['revision_id'] == projected[oid]['business']['revision_id']
                  for layer in layers for oid in [layer['object']['object_id']])
          and [step['field'] for step in plan['walked']] == ['parent_ref', 'parent_ref', 'goal_ref', 'goal_ref',
                                                             'parent_ref', 'architecture_ref', 'parent_ref']
          and all(step['read'] == layers[index + 1]['object']['ref'] for index, step in enumerate(plan['walked']))
          and plan['walked'][0]['pinned'] == task['ref']
          and plan['walked'][5]['pinned'] == made['Strategy']['ref'] + '#responsibility_structure/unit-a')

    task_layer = layers[1]
    acceptance = {block['id']: block for block in task_layer['blocks']}['acceptance']
    read_back = {c['id']: c for c in blocks_of(projected[task['object_id']])['acceptance']['components']}
    task_business = projected[task['object_id']]['business']
    check('acceptance_criteria_come_back_as_component_references_pinned_to_the_version_read',
          [c['ref'] for c in acceptance['components']]
          == [f"{task_layer['object']['ref']}#acceptance/{cid}" for cid in ('ctx-ac1', 'ctx-ac2')]
          and all(c['pinned'] == {'object_id': task['object_id'], 'object_version': task_business['version'],
                                  'revision_id': task_business['revision_id'], 'block': 'acceptance',
                                  'component': c['id'], 'ref': c['ref']}
                  and c['text'] == read_back[c['id']]['text'] and f"`{c['ref']}`" in markdown
                  for c in acceptance['components'])
          and acceptance['components'][0]['refs'][0]['ref'] == criterion
          and all({'ref': c['ref']} in coverage['basis']['evidence'] for c in acceptance['components']))

    events = [event for layer in layers for event in layer['events']]
    placed = [layer['level'] for layer in layers for event in layer['events'] if event['event_id'] == review['event_id']]
    check('events_come_back_as_event_references_once_at_the_nearest_level',
          events and all(event['ref'] == f"event:{event['event_id']}" and f"（事件 `{event['ref']}`" in markdown
                         for event in events)
          and coverage['happened']['evidence'] == [{'ref': event['ref']} for event in events]
          and placed == [0] and started['event_id'] in [event['event_id'] for event in layers[0]['events']]
          and layers[0]['object']['lifecycle']['event_id'] == started['event_id']
          and f"生命周期：进行中（事件 `event:{started['event_id']}`）" in markdown)

    # 跨链关系只列引用、不展开：周期目标依赖 11 月目标与主干 Mission（#51 建的），Mission 一层列出被周期目标引用。
    goal_layer, mission_layer = layers[3], layers[2]
    depends = [ref['ref'] for relation in goal_layer['relations'] if relation['field'] == 'depends_on'
               for ref in relation['refs']]
    shown = {(item['from'], item['field'], item['to']) for item in plan['shown_not_followed']}
    check('cross_chain_relations_are_listed_as_references_and_not_followed',
          len(depends) == 2 and {(goal_layer['object']['ref'], 'depends_on', ref) for ref in depends} <= shown
          and (goal_layer['object']['ref'], 'depends_on', mission_layer['object']['ref']) in shown
          and [(item['field'], item['source']['ref']) for item in mission_layer['referenced_by']]
          == [('depends_on', goal_layer['object']['ref'])]
          and depends[0].split('@')[0] not in {layer['object']['object_id'] for layer in layers}
          and f"depends_on：`{depends[0]}`、`{depends[1]}`" in markdown
          and f"被 `{goal_layer['object']['ref']}` 以 depends_on 引用" in markdown)

    def pins(value, found):
        """包里钉定的引用：带修订 id 的对象、块与组件引用，与事件引用。"""
        if isinstance(value, dict):
            if {'object_id', 'object_version', 'revision_id', 'block', 'component', 'ref'} <= set(value):
                found['objects'].append(value)
            elif 'event_id' in value and value.get('ref') == f"event:{value['event_id']}":
                found['events'].add(value['event_id'])
            for item in value.values():
                pins(item, found)
        elif isinstance(value, list):
            for item in value:
                pins(item, found)
        return found

    versions = {}

    def reads_back(pinned):
        key = (pinned['object_id'], pinned['object_version'])
        if key not in versions:
            versions[key] = flow.read('outsider', pinned['object_id'], version=pinned['object_version'])
        view = versions[key].get('business', versions[key])
        form = (f"{pinned['object_id']}@{pinned['object_version']}" + (f"#{pinned['block']}" if pinned['block'] else '')
                + (f"/{pinned['component']}" if pinned['component'] else ''))
        block = next((b for b in view['blocks'] if b['id'] == pinned['block']), None)
        return (view['revision_id'] == pinned['revision_id'] and pinned['ref'] == form
                and (pinned['block'] is None or block is not None)
                and (pinned['component'] is None or any(c['id'] == pinned['component'] for c in block['components'])))

    found = pins(pack, {'objects': [], 'events': set()})
    stored = {str(row['event_id']) for row in flow.rows(
        'SELECT event_id FROM gov_world_events WHERE scope_id=%s AND contract_version=%s AND event_id = ANY(%s::uuid[])',
        (scope, V02, sorted(found['events'])))}
    listed = {layer['object']['object_id']: {e['event_id'] for e in flow.events('outsider', layer['object']['object_id'])['events']}
              for layer in layers if layer['events']}
    check('every_reference_in_the_pack_is_pinned_and_reads_back',
          any(p['component'] for p in found['objects']) and review['event_id'] in found['events']
          and all(reads_back(pinned) for pinned in found['objects']) and stored == found['events']
          and all(event['event_id'] in listed[layer['object']['object_id']] for layer in layers for event in layer['events']))

    state = layers[0]['state']
    issue = {block['id']: block for block in state['blocks']}['issues']['components'][0]
    check('the_latest_snapshot_comes_back_as_its_shell_view_marked_unconfirmed',
          state['ref'] == latest_snapshot['ref'] and state['unconfirmed'] is True and state['as_of'] == t_second
          and state['payload_type']['id'] == 'execution_state'
          and state['generator']['principal_id'] == actor_id['agent_a']
          and state['source_event_refs'] == [{'event_id': review['event_id'], 'ref': source}]
          and state['subject_ref']['object_id'] == activity['object_id']
          and issue['ref'] == f"{latest_snapshot['ref']}#issues/ctx-iss-1"
          and issue['pinned']['revision_id'] == latest_snapshot['revision_id']
          and f"最新状态快照（未经确认，截至 {t_second}） `{latest_snapshot['ref']}`" in markdown
          and layers[2]['state']['ref'] == projected[mission['object_id']]['records']['latest_state']['ref']
          and [layer['state'] is not None for layer in layers] == [True, False, True] + [False] * 5)

    check('lifecycle_formal_content_and_responsibility_follow_the_0_2_read_projection',
          all(layer['object']['lifecycle'] == view['records']['lifecycle']
              and layer['object']['formal'] == (view['business']['formal']['lifecycle_status'] == 'confirmed'
                                                if OBJECTS[layer['object']['object_type']]['gated'] else None)
              and layer['object']['responsible'] == view['identity']['responsible']
              for layer in layers for view in [projected[layer['object']['object_id']]])
          and layers[0]['object']['lifecycle']['status'] == 'in_progress'
          and layers[1]['object']['lifecycle']['status'] == 'assigned'
          and [p['principal_id'] for p in layers[0]['object']['responsible']['principals']] == [actor_id['agent_a']]
          and layers[0]['object']['responsible']['source'] == 'attribute'
          and layers[3]['object']['responsible']['source'] == 'role'
          and '责任人（来自属性 responsible）：' in markdown and '责任人（来自角色 DOMAIN_DRI）：' in markdown)

    refs = set()
    pins_and_refs = [pack]
    while pins_and_refs:  # 包里出现过的全部业务形式引用
        value = pins_and_refs.pop()
        if isinstance(value, dict):
            refs.update([value['ref']] if isinstance(value.get('ref'), str) else [])
            pins_and_refs.extend(value.values())
        elif isinstance(value, list):
            pins_and_refs.extend(value)
    check('coverage_answers_the_six_questions_from_what_the_pack_holds',
          list(coverage) == ['why', 'what', 'who', 'now', 'happened', 'basis']
          and all(answer['answered'] and answer['gap'] is None for answer in coverage.values())
          and all(item['ref'] in refs for answer in coverage.values() for item in answer['evidence'])
          and coverage['what']['evidence'] == [{'ref': f"{layers[0]['object']['ref']}#instruction"}]
          and coverage['who']['evidence'] == [{'ref': layers[0]['object']['ref']}]
          and coverage['now']['evidence'] == [{'ref': latest_snapshot['ref']}, {'ref': layers[2]['state']['ref']}])

    rows = packs()
    row = rows[-1]
    check('each_call_writes_exactly_one_context_pack_row_holding_what_was_returned_with_the_0_1_defaults',
          len(rows) == before + 1 and str(row['context_pack_id']) == first['context_pack_id']
          and str(row['principal_id']) == actor_id['agent_a'] and str(row['object_id']) == activity['object_id']
          and row['question'] == question['question'] and row['pack'] == pack and row['plan'] == plan
          and row['coverage'] == coverage and row['budget'] == first['budget']
          and {k: first['budget'][k] for k in ('max_chars', 'max_events_per_object', 'recent_days')}
          == {'max_chars': 12000, 'max_events_per_object': 10, 'recent_days': 30}
          and first['budget']['used_chars'] == len(markdown)
          and first['budget']['estimated_tokens'] == -(-len(markdown) // 2)
          and set(plan) == {'walked', 'shown_not_followed', 'taken', 'trimmed', 'state_and_events_from_levels',
                            'over_budget'}
          and plan['state_and_events_from_levels'] == [0, 1, 2])

    second = flow.context('outsider', activity['object_id'], question)
    check('two_calls_on_the_same_world_state_return_the_same_pack_plan_and_coverage',
          second['context_pack'] == pack and second['plan'] == plan and second['coverage'] == coverage
          and second['context_pack_id'] != first['context_pack_id'] and len(packs()) == before + 2)

    # ------------------------------------------------------------ budget（同 0.1）
    mission_level = layers[2]
    check('the_per_object_event_cap_keeps_the_newest_ten_events_and_records_the_rest',
          len(mission_level['events']) == 10
          and [at(e['occurred_at']) for e in mission_level['events']]
          == sorted((at(e['occurred_at']) for e in mission_level['events']), reverse=True)
          and any(entry['reason'] == 'over_level_cap' and entry['level'] == 2 for entry in plan['trimmed']))
    tight = flow.context('agent_a', activity['object_id'], {**question, 'budget': {'max_chars': len(markdown) // 2}})
    over = [entry for entry in tight['plan']['trimmed'] if entry['reason'] == 'over_budget']
    when = {event['ref']: at(event['occurred_at']) for event in events}
    over_events = [entry['key'] for entry in over if entry['kind'] == 'event']
    rest = [entry['level'] for entry in over if entry['kind'] != 'event']
    check('a_tight_budget_trims_old_events_first_then_the_farthest_levels_and_records_why',
          over[:len(over_events)] == [entry for entry in over if entry['kind'] == 'event']
          and [when[key] for key in over_events] == sorted(when[key] for key in over_events)
          and rest and rest == sorted(rest, reverse=True) and 0 not in rest
          and tight['budget']['used_chars'] == len(tight['context_pack']['markdown'])
          and tight['budget']['over_budget'] is (tight['budget']['used_chars'] > len(markdown) // 2))
    tiny = flow.context('agent_a', activity['object_id'], {**question, 'budget': {'max_chars': 10}})
    check('the_current_object_keeps_its_blocks_and_latest_snapshot_under_any_budget',
          all(result['context_pack']['layers'][0]['blocks'] == layers[0]['blocks']
              and result['context_pack']['layers'][0]['state'] == layers[0]['state'] for result in (tight, tiny))
          and tiny['budget']['over_budget'] is True and tiny['plan']['over_budget'] is True
          and all(layer['blocks'] == [] and layer['events'] == [] for layer in tiny['context_pack']['layers'][1:])
          and not tiny['coverage']['why']['answered'])
    capped = flow.context('agent_a', activity['object_id'], {**question, 'budget': {'max_events_per_object': 1}})
    check('a_per_object_cap_of_one_keeps_the_newest_event_of_each_level',
          [layer['events'] for layer in capped['context_pack']['layers']]
          == [layer['events'][:1] for layer in layers])
    recent = flow.context('agent_a', activity['object_id'], {**question, 'recent_days': 1})
    start = at(recent['budget']['window_start'])
    recent_events = [e for layer in recent['context_pack']['layers'] for e in layer['events']]
    check('recent_events_start_at_the_requested_window',
          recent_events and all(at(e['occurred_at']) >= start for e in recent_events)
          and {e['event_id'] for e in recent_events} <= {e['event_id'] for e in events} | {
              e['key'].split(':', 1)[1] for e in plan['trimmed'] if e['kind'] == 'event'})

    # ------------------------------------------------------------ other starting point, refusals, 0.1, append-only
    from_snapshot = flow.context('agent_a', first_snapshot['object_id'], question)
    snap_layers = from_snapshot['context_pack']['layers']
    check('starting_from_a_snapshot_takes_its_subject_as_the_current_object_and_that_snapshot_as_its_state',
          from_snapshot['context_pack']['start'] == first_snapshot['ref']
          and snap_layers[0]['object']['object_id'] == activity['object_id']
          and snap_layers[0]['state']['ref'] == first_snapshot['ref'] != latest_snapshot['ref']
          and [layer['object']['object_type'] for layer in snap_layers] == spine
          and from_snapshot['coverage']['now']['evidence'][0] == {'ref': first_snapshot['ref']})

    count, foreign_count = len(packs()), len(packs(foreign_scope))
    refused = [flow.context('foreign_ceo', activity['object_id'], question, expected=404)['error']['code'],
               flow.context('agent_a', uid(), question, expected=404)['error']['code'],
               flow.context('agent_a', activity['object_id'], {'question': '  '}, expected=422)['error']['code'],
               flow.context('agent_a', activity['object_id'], {**question, 'recent_days': 3_000_000},
                            expected=422)['error']['code']]
    check('an_identity_outside_the_scope_an_unknown_object_and_bad_requests_are_refused_and_write_nothing',
          refused == ['NOT_FOUND', 'NOT_FOUND', 'INVALID_REQUEST', 'INVALID_REQUEST']
          and len(packs()) == count and len(packs(foreign_scope)) == foreign_count)

    old = flow.context('foreign_ceo', foreign['object']['object_id'], question)
    check('a_0_1_object_still_gets_the_0_1_context',
          'contract_version' not in old and 'contract_version' not in old['context_pack']
          and old['context_pack']['start'] == foreign['object']['ref']
          and isinstance(old['context_pack']['layers'][0]['object']['responsible'], list)
          and 'hops' in old['plan'] and '## 六问指引' in old['context_pack']['markdown']
          and len(packs(foreign_scope)) == foreign_count + 1)
    check('a_0_2_context_pack_row_cannot_be_changed_even_by_the_owner',
          immutable_owner_probe(h, f, table='gov_world_context_packs', column='question', key_column='context_pack_id',
                                key=first['context_pack_id'])['owner_write_rejected'])


def mission_lifecycle(book, h, f, flow, trunk):
    """#55：Mission 成立之后按 0.2 状态表执行——开始由 Owner 或 Owner 的 Agent（在 Mission 所在域持 AGENT、带写入声明）
    记，交付由 Owner 记，验收通过、退回、重开、取消由 DRI 记；状态表的每一格经 HTTP 走一条，未列出的组合、错记录者、
    撤回与重复各有拒绝，库快照不变。关注标记只由 CEO 本人记、每个 Mission 一次、只置 core_battle，不改生命周期与
    决定权。上层关闭、取消不改变下层，Task 全部关闭不关 Mission。#54 转来的补验：进行中、已交付、调整中由 Owner 带
    候选开轮、DRI 写回，状态不变；已关闭、已取消拒绝直接修订。Mission 都挂在本场景新建并确认的周期目标下。"""
    check = book.check
    made = trunk['made']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    names = {state['id']: state['display_name']
             for state in json.loads(REGISTRY.read_text())['lifecycles']['Mission']['states']}
    ladder = [('owner_a', 'world_commit_mission', {}), ('a', 'world_confirm_mission', {'outcome': 'accepted'}),
              ('owner_a', 'world_start', {}), ('owner_a', 'world_deliver', {}), ('a', 'world_reject', {})]
    open_states = ['draft', 'committed', 'established', 'in_progress', 'delivered', 'adjusting']

    def act(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    def deny(actor, kind, oid, codes, params=None, says=None):
        flow.deny(actor, flow.targeted(kind, oid, params or {}), codes=codes, says=says)

    def deny_revise(actor, oid, patch, codes, says=None, declaration=None):
        params = {'payload': patch, **({'declaration': declaration} if declaration else {})}
        flow.deny(actor, flow.targeted('world_revise_object', oid, params), codes=codes, says=says)

    def as_agent(oid):
        """Owner 的 Agent 记开始带写入声明（契约第 9.3 节、补 15）。"""
        return {'declaration': {'scene': f'{oid}@1', 'trigger': '按执行计划推进', 'human_acceptance': {'required': False}}}

    def withdrawal(result):
        return {'outcome': 'withdrawn', 'supersedes_event_id': result['event_id']}

    def view(oid):
        return flow.read('outsider', oid)

    def life(oid):
        return view(oid)['records']['lifecycle']

    def stage(state, result):
        return {'status': state, 'display_name': names[state], 'event_id': result['event_id']}

    def event(result):
        return flow.rows('SELECT e.*, r.action_type FROM gov_world_events e JOIN gov_action_receipts r '
                         'ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id WHERE e.scope_id=%s AND e.event_id=%s',
                         (f['scope_id'], result['event_id']))[0]

    def pinned_to_latest(result, oid):
        business = view(oid)['business']
        return event(result)['subject_refs'] == [{'object_id': oid, 'object_version': business['version'],
                                                  'revision_id': business['revision_id'], 'block': None,
                                                  'component': None}]

    def plan(oid):
        return [(c['id'], c['text']) for c in blocks_of(view(oid))['execution_plan']['components']]

    goal = flow.create('a', 'PeriodGoal', 'a', {'title': '执行目标（#55）', 'period': '2026-10',
                                               'goal_ref': made['LongTermGoal.unit']['ref']})['result']
    act('a', 'world_commit_period_goal', goal['object_id'])
    act('ceo', 'world_confirm_period_goal', goal['object_id'], {'outcome': 'accepted'})

    def mission(title):
        """挂在已确认周期目标下、已指派 Owner 的草稿 Mission。"""
        oid = flow.create('a', 'Mission', 'a', {
            'title': title, 'goal_ref': goal['ref'],
            'blocks': {'play': {'text': 'Play 核心路径：两场试点。'},
                       'execution_plan': {'components': [{'id': 'p1', 'type': 'plan_item', 'text': '搭环境'}]}}}
        )['result']['object_id']
        flow.assign('a', oid, actor_id['owner_a'])
        return oid

    def walk_to(oid, state):
        for actor, kind, params in ladder[:open_states.index(state)]:
            act(actor, kind, oid, params)
        assert life(oid)['status'] == state

    # ================================================================ 主线：立项、开始、交付、退回、验收、重开、取消
    # 立项三格 #54 已在 gates 场景逐项验过，这里在主线上各走一条，整张表在本场景里齐全。
    main = mission('执行主线')
    committed = act('owner_a', 'world_commit_mission', main)
    check('mission_walk_draft_commit_to_committed', life(main) == stage('committed', committed))
    returned = act('a', 'world_confirm_mission', main, {'outcome': 'returned'})
    check('mission_walk_committed_confirm_returned_to_draft', life(main) == stage('draft', returned))
    act('owner_a', 'world_commit_mission', main)
    established = act('a', 'world_confirm_mission', main, {'outcome': 'accepted'})
    check('mission_walk_committed_confirm_accepted_to_established', life(main) == stage('established', established))
    deny('owner_a', 'world_deliver', main, {'INVALID_STATE'})
    deny('a', 'world_accept', main, {'INVALID_STATE'})
    deny('a', 'world_reopen', main, {'INVALID_STATE'})
    check('mission_an_established_mission_is_not_delivered_accepted_or_reopened_before_it_starts')

    # 开始：Owner，或在 Mission 所在域持 AGENT 的 Agent（带写入声明）；DRI、单元里另一位 Owner、IC、持 DRI 角色的
    # Agent 都不能记。
    deny('a', 'world_start', main, {'FORBIDDEN'})
    deny('owner_a2', 'world_start', main, {'FORBIDDEN'})
    deny('ic_a', 'world_start', main, {'FORBIDDEN'})
    deny('agent', 'world_start', main, {'FORBIDDEN'}, as_agent(main))
    check('mission_only_the_owner_or_an_agent_holding_agent_in_its_domain_starts_it')
    deny('agent_a', 'world_start', main, {'INVALID_REQUEST'})
    check('mission_the_owners_agent_starts_it_only_with_a_declaration')
    started = act('agent_a', 'world_start', main, as_agent(main))
    row = event(started)
    check('mission_established_start_to_in_progress_by_the_owners_agent',
          life(main) == stage('in_progress', started) and row['kind'] == 'start'
          and EVENT_KINDS['start']['class'] == 'lifecycle' and row['contract_version'] == V02
          and row['outcome'] is None and str(row['principal_id']) == actor_id['agent_a']
          and row['action_type'] == 'world_start' and pinned_to_latest(started, main)
          and started['declaration']['scene']['ref'] == f'{main}@1' and 'responsible_through' not in started)

    # 撤回同 #53：只撤推出当前状态的那条，由原转移的记录者类别记；撤回事件不能再撤。
    deny('a', 'world_start', main, {'FORBIDDEN'}, withdrawal(started))
    check('mission_a_withdrawn_start_is_recorded_by_the_owner_or_its_agent_not_the_dri')
    back = act('agent_a', 'world_start', main, {**as_agent(main), **withdrawal(started)})
    check('mission_withdrawing_the_start_returns_to_established_and_keeps_the_original',
          life(main) == stage('established', back) and event(back)['outcome'] == 'withdrawn'
          and str(event(back)['supersedes_event_id']) == started['event_id'] and event(started)['kind'] == 'start')
    read = {item['event_id']: item for item in flow.events('outsider', main)['events']}
    check('mission_reading_events_shows_the_start_withdrawn_by_the_withdrawal',
          read[started['event_id']]['withdrawn_by'] == [back['event_id']]
          and read[back['event_id']]['action'] == 'world_start' and read[back['event_id']]['class'] == 'lifecycle')
    deny('owner_a', 'world_start', main, {'INVALID_STATE'}, withdrawal(back))
    check('mission_a_withdrawal_cannot_be_withdrawn')
    started = act('owner_a', 'world_start', main)
    check('mission_established_start_to_in_progress_by_the_owner',
          life(main) == stage('in_progress', started) and started['responsible_through'] == main
          and str(event(started)['principal_id']) == actor_id['owner_a'])
    deny('owner_a', 'world_start', main, {'INVALID_STATE'})
    check('mission_a_repeated_start_is_refused')

    def rerun(state, producer, text):
        """#54 转来的补验：在 state 段由 Owner 带候选开轮、DRI 接受写回；生命周期与推出它的事件不变，执行计划取当前值。"""
        kept = plan(main)
        opened = act('owner_a', 'world_commit_mission', main, {'payload': {'blocks': {'play': {'text': text}}}})
        during = view(main)
        rewritten = act('a', 'world_confirm_mission', main, {'outcome': 'accepted'})
        after = view(main)
        check(f'mission_{state}_a_round_opened_by_the_owner_is_written_back_by_the_dri_and_the_stage_stays',
              during['records']['lifecycle'] == stage(state, producer)
              and during['business']['round']['opened_by_event_id'] == opened['event_id']
              and during['business']['round']['stage'] == 'committed'
              and after['records']['lifecycle'] == stage(state, producer) and after['business']['round'] is None
              and blocks_of(after)['play']['text'] == text and plan(main) == kept
              and rewritten['version'] == during['business']['version'] + 1
              and after['business']['formal'] == {'lifecycle_status': 'confirmed',
                                                  'effective_revision_id': rewritten['revision_id']})

    rerun('in_progress', started, 'Play 核心路径变化：先打样再复制。')

    # Task 全部关闭不自动关闭 Mission（补 19）。
    def task(title):
        oid = flow.create('owner_a', 'Task', 'a', {'title': title,
                                                   'parent_ref': f"{main}@{view(main)['business']['version']}"})
        oid = oid['result']['object_id']
        flow.assign('owner_a', oid, actor_id['ic_a'])
        act('ic_a', 'world_start', oid)
        return oid

    tasks = [task('准备环境'), task('约客户')]
    for oid in tasks:
        act('ic_a', 'world_deliver', oid)
        act('owner_a', 'world_accept', oid)
    check('mission_all_its_tasks_closed_leaves_the_mission_in_progress',
          [life(oid)['status'] for oid in tasks] == ['closed', 'closed'] and life(main) == stage('in_progress', started))
    running = task('写部署脚本')  # 进行中：看上层关闭、取消之后它不变
    running_life = life(running)

    # 交付只由 Owner：Owner 的 Agent 与 DRI 都不能记；重复交付被拒。
    deny('agent_a', 'world_deliver', main, {'FORBIDDEN'}, as_agent(main))
    deny('a', 'world_deliver', main, {'FORBIDDEN'})
    check('mission_only_the_owner_delivers_it_and_the_owners_agent_does_not')
    content = {'text': '交付说明', 'refs': [goal['ref']], 'artifacts': ['https://example.test/mission-delivery']}
    delivered = act('owner_a', 'world_deliver', main, {'content': content})
    row = event(delivered)
    check('mission_in_progress_deliver_to_delivered',
          life(main) == stage('delivered', delivered) and row['kind'] == 'deliver'
          and str(row['principal_id']) == actor_id['owner_a'] and row['content']['text'] == '交付说明'
          and row['content']['refs'][0]['object_id'] == goal['object_id'] and pinned_to_latest(delivered, main))
    deny('owner_a', 'world_deliver', main, {'INVALID_STATE'})
    check('mission_a_repeated_delivery_is_refused')
    rerun('delivered', delivered, 'Play 核心路径变化：交付后补一场复盘。')

    # 验收与退回只由 DRI：Owner 的 Agent（不在 Agent 面上）、Owner、单元里另一位 Owner、另一单元的 DRI 都不能。
    deny('agent_a', 'world_accept', main, {'FORBIDDEN'}, as_agent(main))
    deny('owner_a', 'world_accept', main, {'FORBIDDEN'})
    deny('owner_a2', 'world_accept', main, {'FORBIDDEN'})
    deny('b', 'world_accept', main, {'FORBIDDEN'})
    deny('owner_a', 'world_reject', main, {'FORBIDDEN'})
    check('mission_only_the_dri_accepts_or_rejects_the_delivery')
    rejected = act('a', 'world_reject', main, {'content': {'text': '缺客户签字'}})
    check('mission_delivered_reject_to_adjusting',
          life(main) == stage('adjusting', rejected) and event(rejected)['content']['text'] == '缺客户签字')
    deny('a', 'world_accept', main, {'INVALID_STATE'})
    check('mission_an_adjusting_mission_is_delivered_again_before_it_is_accepted')
    rerun('adjusting', rejected, 'Play 核心路径变化：按退回意见补签字环节。')
    redelivered = act('owner_a', 'world_deliver', main)
    check('mission_adjusting_deliver_to_delivered', life(main) == stage('delivered', redelivered))
    accepted = act('a', 'world_accept', main)
    check('mission_delivered_accept_to_closed', life(main) == stage('closed', accepted)
          and str(event(accepted)['principal_id']) == actor_id['a'])
    deny('owner_a', 'world_accept', main, {'FORBIDDEN'}, withdrawal(accepted))
    back = act('a', 'world_accept', main, withdrawal(accepted))
    check('mission_withdrawing_the_acceptance_returns_to_delivered', life(main) == stage('delivered', back))
    deny('owner_a', 'world_deliver', main, {'INVALID_STATE'}, withdrawal(redelivered))
    check('mission_an_earlier_event_cannot_be_withdrawn')
    accepted = act('a', 'world_accept', main)
    check('mission_closing_the_mission_leaves_its_tasks_as_they_were',
          life(main) == stage('closed', accepted) and life(running) == running_life
          and [life(oid)['status'] for oid in tasks] == ['closed', 'closed'])

    # 已关闭：不再交付、取消、开轮、标记与再指派；拒绝直接修订（#54 转来的补验）。
    unattended = {'scene': f'{main}@1', 'trigger': '周会同步执行计划', 'human_acceptance': {'required': False}}
    step = {'blocks': {'execution_plan': {'components': [{'id': 'p9', 'type': 'plan_item', 'text': '收尾'}]}}}
    deny('owner_a', 'world_deliver', main, {'INVALID_STATE'})
    deny('a', 'world_cancel', main, {'INVALID_STATE'})
    deny('owner_a', 'world_commit_mission', main, {'INVALID_STATE'}, {'payload': {'blocks': {'play': {'text': 'x'}}}})
    deny('ceo', 'world_mark_core_battle', main, {'INVALID_STATE'})
    deny('a', 'world_assign', main, {'INVALID_STATE'}, {'principal_id': actor_id['owner_a2']})
    check('mission_a_closed_mission_is_not_delivered_cancelled_re_run_marked_or_reassigned')
    deny_revise('owner_a', main, step, {'INVALID_STATE'}, says='closed, cancelled')
    deny_revise('owner_a', main, {'external_refs': [{'system': 'tianshu', 'id': 'm-55'}]}, {'INVALID_STATE'})
    deny_revise('agent_a', main, step, {'INVALID_STATE'}, declaration=unattended)
    check('mission_a_closed_mission_is_not_revised_directly')
    reopened = act('a', 'world_reopen', main, {'content': {'text': '客户追加一场'}})
    check('mission_closed_reopen_to_in_progress', life(main) == stage('in_progress', reopened))
    deny('owner_a', 'world_cancel', main, {'FORBIDDEN'})
    deny('agent_a', 'world_cancel', main, {'FORBIDDEN'}, as_agent(main))
    check('mission_only_the_dri_cancels_it')
    cancelled = act('a', 'world_cancel', main, {'content': {'text': '客户预算取消'}})
    back = act('a', 'world_cancel', main, withdrawal(cancelled))
    check('mission_withdrawing_the_cancellation_restores_in_progress', life(main) == stage('in_progress', back))
    cancelled = act('a', 'world_cancel', main)
    check('mission_cancelling_the_mission_leaves_its_tasks_as_they_were',
          life(main) == stage('cancelled', cancelled) and life(running) == running_life
          and [life(oid)['status'] for oid in tasks] == ['closed', 'closed'])
    deny('a', 'world_cancel', main, {'INVALID_STATE'})
    deny('a', 'world_reopen', main, {'INVALID_STATE'})
    deny('owner_a', 'world_start', main, {'INVALID_STATE'})
    deny('ceo', 'world_mark_core_battle', main, {'INVALID_STATE'})
    check('mission_a_cancelled_mission_is_not_cancelled_reopened_started_or_marked')
    deny_revise('owner_a', main, step, {'INVALID_STATE'}, says='closed, cancelled')
    deny_revise('agent_a', main, step, {'INVALID_STATE'}, declaration=unattended)
    check('mission_a_cancelled_mission_is_not_revised_directly')

    # ================================================================ 关注标记与取消：六个非终态各一个 Mission
    for state in open_states:
        oid = mission(f'关注与取消于{state}')
        walk_to(oid, state)
        before = view(oid)
        if state == 'committed':  # 同键重放返回原回执，只有一条事件
            body = flow.prepare('ceo', flow.targeted('world_mark_core_battle', oid, {}))
            receipt = flow.commit('ceo', body)
            replay = flow.commit('ceo', deepcopy(body))
            rows = flow.rows('SELECT kind FROM gov_world_events WHERE scope_id=%s AND action_id=%s',
                             (f['scope_id'], receipt['receipt_id']))
            check('a_mark_writes_exactly_one_event_and_replaying_it_returns_the_original_receipt',
                  replay['receipt_id'] == receipt['receipt_id'] and [r['kind'] for r in rows] == ['core_battle.marked'])
            mark = receipt['result']
        else:
            mark = act('ceo', 'world_mark_core_battle', oid,
                       {'content': {'text': '关注：影响 Q4 回款'}} if state == 'draft' else None)
        after = view(oid)
        check(f'mission_{state}_mark_core_battle_keeps_the_stage_its_producer_and_the_owner',
              after['records']['lifecycle'] == before['records']['lifecycle']
              and not before['business']['attributes']['core_battle']
              and after['business']['attributes']['core_battle'] is True
              and after['business']['attributes']['responsible'] == actor_id['owner_a']
              and after['identity']['responsible'] == before['identity']['responsible']
              and mark['version'] == before['business']['version'] + 1)
        if state == 'draft':
            row = event(mark)
            check('a_mark_is_one_record_event_by_the_ceo_pinned_to_the_revision_that_sets_core_battle',
                  row['kind'] == 'core_battle.marked' and EVENT_KINDS['core_battle.marked']['class'] == 'record'
                  and row['contract_version'] == V02 and row['outcome'] is None and row['detail'] is None
                  and str(row['principal_id']) == actor_id['ceo'] and row['action_type'] == 'world_mark_core_battle'
                  and row['subject_refs'][0]['revision_id'] == mark['revision_id']
                  and row['content']['text'] == '关注：影响 Q4 回款'
                  and after['business']['formal'] == {'lifecycle_status': 'draft', 'effective_revision_id': None})
            deny('ceo', 'world_mark_core_battle', oid, {'INVALID_STATE'}, says='once')
            check('a_mission_is_marked_a_core_battle_only_once')
            deny('ceo', 'world_mark_core_battle', oid, {'INVALID_REQUEST'}, withdrawal(mark))
            check('a_mark_is_a_record_event_and_is_not_withdrawn')
        if state == 'established':
            check('marking_an_established_mission_moves_the_effective_revision_with_the_new_revision',
                  after['business']['formal'] == {'lifecycle_status': 'confirmed',
                                                  'effective_revision_id': mark['revision_id']})
        cancelled = act('a', 'world_cancel', oid)
        check(f'mission_{state}_cancel_to_cancelled', life(oid) == stage('cancelled', cancelled))
    deny('owner_a', 'world_start', mission('未立项'), {'INVALID_STATE'})
    check('mission_a_draft_mission_does_not_start')

    # 只由 CEO 本人记：DRI、Owner、持 CEO 角色的 Agent 都不能；标记不改决定权，一轮重走写回后标记仍在。
    watched = mission('核心战役（关注）')
    walk_to(watched, 'established')
    formed = life(watched)
    deny('a', 'world_mark_core_battle', watched, {'FORBIDDEN'})
    deny('owner_a', 'world_mark_core_battle', watched, {'FORBIDDEN'})
    deny('agent_ceo_a', 'world_mark_core_battle', watched, {'FORBIDDEN'}, says='recorded by a person')
    check('only_the_ceo_in_person_marks_a_core_battle_and_an_agent_holding_the_ceo_role_cannot')
    opened = act('owner_a', 'world_commit_mission', watched, {'payload': {'blocks': {'play': {'text': '关注期间改打法'}}}})
    act('ceo', 'world_mark_core_battle', watched)
    marked = view(watched)
    act('a', 'world_confirm_mission', watched, {'outcome': 'accepted'})
    restarted = act('owner_a', 'world_start', watched)
    after = view(watched)
    check('a_mark_leaves_the_decisions_to_the_owner_and_the_dri_and_survives_a_write_back',
          marked['records']['lifecycle'] == formed and marked['business']['round']['opened_by_event_id'] == opened['event_id']
          and after['business']['attributes']['core_battle'] is True
          and blocks_of(after)['play']['text'] == '关注期间改打法'
          and after['records']['lifecycle'] == stage('in_progress', restarted))


def delegation(book, h, f, flow, trunk):
    """#62：人向服务主体登记委托，服务主体以自己的凭证代记门、指派与生命周期事件，按被代记的人判权。天枢（在公司域
    与单元 a 持 AGENT 的 Agent）代 CEO 确认周期目标、代 Mission 的 Owner 指派 Task、代 Task 的责任人开始与交付；
    委托过期、已撤销、动作族或域超出范围、委托人不再是 scope 内有效的人、被代记的人自己无权，各一条 FORBIDDEN 且
    库快照不变。事件与回执同时记下记录者、被代记的人与外部确认记录；被代记的人本人撤回代记的事件；读投影 identity
    给委托范围覆盖对象所在域的当前有效委托；重放按委托与被代记的人复核。#55 合入后顺带代 CEO 记一条关注标记。
    对象都新建，不动前面场景的主干。"""
    check = book.check
    made = trunk['made']
    scope = f['scope_id']
    actor_id = {name: actor['principal_id'] for name, actor in f['actors'].items()}
    east8 = timezone(timedelta(hours=8))
    records = iter(range(1, 10_000))

    def later(**delta):
        """东八区写法的时刻（服务按 UTC 规范化）。"""
        return (datetime.now(east8) + timedelta(**delta)).isoformat(timespec='seconds')

    def grant_params(delegate, families, domains, valid_until=None):
        return {'delegate_principal_id': actor_id.get(delegate, delegate), 'families': list(families),
                'domain_ids': [f['domains'].get(d, d) for d in domains], 'valid_until': valid_until or later(days=1)}

    def grant(actor, delegate, families, domains, valid_until=None):
        return flow.act(actor, 'world_grant_delegation', grant_params(delegate, families, domains, valid_until))['result']

    def deny_grant(actor, codes, delegate='tianshu', families=('gate',), domains=('a',), valid_until=None, **extra):
        params = {**grant_params(delegate, families, domains, valid_until), **extra}
        flow.deny(actor, flow.command('world_grant_delegation', params), codes=codes)

    def revoke(actor, grant_event_id):
        return flow.act(actor, 'world_revoke_delegation', {'delegation_event_id': grant_event_id})['result']

    def deny_revoke(actor, grant_event_id, codes):
        flow.deny(actor, flow.command('world_revoke_delegation', {'delegation_event_id': grant_event_id}), codes=codes)

    def behalf(person, **change):
        """on_behalf_of：外部记录 id 各不相同，外部确认时刻取几分钟前。"""
        return {'principal_id': actor_id.get(person, person), 'external_record_id': f'tianshu:confirm:{next(records)}',
                'external_confirmed_at': later(minutes=-5), **change}

    def on_behalf(kind, oid, person, params=None, actor='tianshu'):
        """代记一条，返回（回执，带去的 on_behalf_of）。"""
        sent = behalf(person)
        body = flow.targeted(kind, oid, {**(params or {}), 'on_behalf_of': sent})
        return flow.commit(actor, flow.prepare(actor, body)), sent

    def deny_on_behalf(kind, oid, person, codes, params=None, actor='tianshu', says=None, **change):
        flow.deny(actor, flow.targeted(kind, oid, {**(params or {}), 'on_behalf_of': behalf(person, **change)}),
                  codes=codes, says=says)

    unscoped = 'No delegation in force'  # 委托不在（过期、撤销、族或域不在范围、委托人不再有效）时的拒绝说明

    def act(actor, kind, oid, params=None):
        return flow.commit(actor, flow.prepare(actor, flow.targeted(kind, oid, params or {})))['result']

    def event(event_id):
        return flow.rows('SELECT e.*, r.action_type FROM gov_world_events e JOIN gov_action_receipts r '
                         'ON r.scope_id=e.scope_id AND r.receipt_id=e.action_id WHERE e.scope_id=%s AND e.event_id=%s',
                         (scope, event_id))[0]

    def events_of(receipt_id):
        return flow.rows('SELECT event_id FROM gov_world_events WHERE scope_id=%s AND action_id=%s', (scope, receipt_id))

    def life(oid):
        return flow.read('outsider', oid)['records']['lifecycle']

    def delegations(oid):
        return {item['event_id']: item for item in flow.read('outsider', oid)['identity']['delegations']}

    def confirmation(sent):
        """事件行与回执里的外部确认记录应有的样子：确认时刻按 UTC 规范化。"""
        return {'external_record_id': sent['external_record_id'],
                'external_confirmed_at': utc(datetime.fromisoformat(sent['external_confirmed_at']))}

    company = made['Company']
    company_view = flow.read('outsider', company['object_id'])['business']
    pg = flow.create('a', 'PeriodGoal', 'a', {'title': '1 月目标（代记）', 'period': '2027-01',
                                             'goal_ref': made['LongTermGoal.unit']['ref']})['result']
    pid = pg['object_id']
    company_goal = flow.create('ceo', 'LongTermGoal', 'company', {'title': '公司五年目标（代记）', 'scope': 'company',
                                                                 'horizon': '2031', 'parent_ref': company['ref']})['result']

    # ---------------------------------------------------------------- 登记委托
    deny_grant('tianshu', {'FORBIDDEN'})
    deny_grant('ceo', {'INVALID_REQUEST'}, delegate='a')
    deny_grant('ceo', {'INVALID_REQUEST'}, delegate=uid())
    deny_grant('ceo', {'INVALID_REQUEST'}, families=('create',))
    deny_grant('ceo', {'INVALID_REQUEST'}, domains=(f['foreign_domains']['a'],))
    deny_grant('ceo', {'INVALID_REQUEST'}, valid_until=later(minutes=-1))
    deny_grant('ceo', {'INVALID_REQUEST'}, on_behalf_of=behalf('ceo'))
    check('a_delegation_is_granted_by_a_person_in_person_to_an_agent_of_the_scope_for_its_families_domains_and_a_'
          'future_expiry_and_is_not_itself_recorded_on_behalf')
    until = later(days=1)
    ceo_grant = grant('ceo', 'tianshu', ['gate'], ['a'], until)
    row = event(ceo_grant['event_id'])
    check('granting_records_one_record_event_under_the_company_with_the_delegation_in_its_detail',
          row['kind'] == 'delegation.granted' and EVENT_KINDS['delegation.granted']['class'] == 'record'
          and row['contract_version'] == V02 and str(row['principal_id']) == actor_id['ceo']
          and row['on_behalf_of'] is None and row['outcome'] is None and row['supersedes_event_id'] is None
          and row['action_type'] == 'world_grant_delegation' and row['occurred_at'] == row['recorded_at']
          and row['subject_refs'] == [{'object_id': company['object_id'], 'object_version': company_view['version'],
                                       'revision_id': company_view['revision_id'], 'block': None, 'component': None}]
          and row['detail'] == {'delegate_principal_id': actor_id['tianshu'], 'families': ['gate'],
                                'domain_ids': [f['domains']['a']], 'valid_until': utc(datetime.fromisoformat(until))}
          and ceo_grant['detail'] == row['detail'] and ceo_grant['contract_version'] == V02
          and len(events_of(flow.receipts[-1]['receipt_id'])) == 1)
    owner_grant = grant('owner_a', 'tianshu', ['assign'], ['a'])
    ic_grant = grant('ic_a', 'tianshu', ['lifecycle'], ['a'])
    leaver_grant = grant('leaver', 'tianshu', ['lifecycle'], ['a'])
    brief = grant('ic_a', 'agent_a', ['lifecycle'], ['a'], later(seconds=8))
    listed = delegations(pid)
    check('identity_gives_the_delegations_in_force_whose_domains_cover_the_objects_domain',
          set(listed) == {ceo_grant['event_id'], owner_grant['event_id'], ic_grant['event_id'],
                          leaver_grant['event_id'], brief['event_id']}
          and listed[ceo_grant['event_id']] == {
              'event_id': ceo_grant['event_id'], 'ref': f"event:{ceo_grant['event_id']}",
              'grantor': {'principal_id': actor_id['ceo'], 'principal_type': 'human',
                          'display_name': listed[ceo_grant['event_id']]['grantor']['display_name']},
              'delegate': {'principal_id': actor_id['tianshu'], 'principal_type': 'agent',
                           'display_name': listed[ceo_grant['event_id']]['delegate']['display_name']},
              'families': ['gate'], 'domain_ids': [f['domains']['a']], 'valid_until': ceo_grant['detail']['valid_until'],
              'granted_at': utc(row['recorded_at'])}
          and delegations(company_goal['object_id']) == {})

    # ---------------------------------------------------------------- 代 CEO 确认周期目标（门）
    act('a', 'world_commit_period_goal', pid)
    confirmed, sent = on_behalf('world_confirm_period_goal', pid, 'ceo', {'outcome': 'accepted'})
    result = confirmed['result']
    row = event(result['event_id'])
    check('the_service_principal_confirms_a_period_goal_on_behalf_of_the_ceo_and_it_counts_as_the_ceos_confirmation',
          life(pid) == {'status': 'confirmed', 'display_name': '已确认', 'event_id': result['event_id']}
          and flow.read('outsider', pid)['business']['formal'] == {'lifecycle_status': 'confirmed',
                                                                   'effective_revision_id': result['revision_id']}
          and result['required_assignment_ids'] == [f['ceo_domain_assignments']['a']])
    check('the_delegated_event_records_the_service_principal_the_person_and_the_external_confirmation',
          row['kind'] == 'confirm' and row['outcome'] == 'accepted' and row['action_type'] == 'world_confirm_period_goal'
          and str(row['principal_id']) == actor_id['tianshu'] and str(row['on_behalf_of']) == actor_id['ceo']
          and row['external_record_id'] == sent['external_record_id']
          and row['external_confirmed_at'] == datetime.fromisoformat(sent['external_confirmed_at'])
          and row['occurred_at'] == row['recorded_at'] and row['external_confirmed_at'] <= row['recorded_at'])
    check('the_delegated_receipt_records_the_service_principal_the_person_the_external_confirmation_and_the_delegation',
          confirmed['actor_id'] == actor_id['tianshu'] and 'declaration' not in result
          and result['on_behalf_of'] == {'principal_id': actor_id['ceo'], **confirmation(sent),
                                         'delegation_event_id': ceo_grant['event_id']})
    read = {item['event_id']: item for item in flow.events('outsider', pid)['events']}[result['event_id']]
    check('reading_events_gives_the_recorder_the_person_recorded_on_behalf_of_and_the_external_confirmation',
          read['principal']['principal_id'] == actor_id['tianshu'] and read['principal']['principal_type'] == 'agent'
          and read['on_behalf_of']['principal_id'] == actor_id['ceo']
          and read['external_confirmation'] == confirmation(sent)
          and read['class'] == 'gate' and read['action'] == 'world_confirm_period_goal'
          and read['action_id'] == confirmed['receipt_id'])

    # ---------------------------------------------------------------- 代 Mission 的 Owner 指派 Task（指派）
    mission = flow.create('a', 'Mission', 'a', {
        'title': '代记试点', 'goal_ref': f"{pid}@{flow.read('outsider', pid)['business']['version']}"})['result']
    flow.assign('a', mission['object_id'], actor_id['owner_a'])
    marked, _ = on_behalf('world_mark_core_battle', mission['object_id'], 'ceo')
    row = event(marked['result']['event_id'])
    check('the_service_principal_marks_a_mission_as_a_core_battle_on_behalf_of_the_ceo',
          flow.read('outsider', mission['object_id'])['business']['attributes']['core_battle'] is True
          and row['kind'] == 'core_battle.marked' and str(row['principal_id']) == actor_id['tianshu']
          and str(row['on_behalf_of']) == actor_id['ceo']
          and marked['result']['on_behalf_of']['delegation_event_id'] == ceo_grant['event_id'])
    task = flow.create('owner_a', 'Task', 'a', {
        'title': '代记 Task',
        'parent_ref': f"{mission['object_id']}@{flow.read('outsider', mission['object_id'])['business']['version']}"}
    )['result']
    tid = task['object_id']
    body = flow.prepare('tianshu', flow.targeted('world_assign', tid, {'principal_id': actor_id['ic_a'],
                                                                         'on_behalf_of': behalf('owner_a')}))
    assigned = flow.commit('tianshu', body)
    row = event(assigned['result']['event_id'])
    view = flow.read('outsider', tid)
    check('the_service_principal_assigns_a_task_on_behalf_of_the_mission_owner_judged_by_his_responsibility',
          life(tid) == {'status': 'assigned', 'display_name': '已指派', 'event_id': assigned['result']['event_id']}
          and view['business']['attributes']['responsible'] == actor_id['ic_a']
          and [p['principal_id'] for p in view['identity']['responsible']['principals']] == [actor_id['ic_a']]
          and row['kind'] == 'assign' and row['detail'] == {'principal_id': actor_id['ic_a']}
          and str(row['principal_id']) == actor_id['tianshu'] and str(row['on_behalf_of']) == actor_id['owner_a']
          and assigned['result']['responsible_through'] == mission['object_id']
          and assigned['result']['required_assignment_ids'] == [f['actors']['owner_a']['assignment_id']]
          and assigned['result']['on_behalf_of']['delegation_event_id'] == owner_grant['event_id'])
    replay = flow.commit('tianshu', deepcopy(body))
    check('replaying_a_command_recorded_on_behalf_returns_the_original_receipt',
          replay['receipt_id'] == assigned['receipt_id'] and len(events_of(assigned['receipt_id'])) == 1)

    # ---------------------------------------------------------------- 撤销委托
    revoked = revoke('owner_a', owner_grant['event_id'])
    row = event(revoked['event_id'])
    check('revoking_records_one_record_event_under_the_company_that_references_the_grant',
          row['kind'] == 'delegation.revoked' and EVENT_KINDS['delegation.revoked']['class'] == 'record'
          and row['detail'] == {'delegation_event_id': owner_grant['event_id']} and revoked['detail'] == row['detail']
          and str(row['principal_id']) == actor_id['owner_a'] and row['supersedes_event_id'] is None
          and row['subject_refs'][0]['object_id'] == company['object_id']
          and owner_grant['event_id'] not in delegations(tid))
    deny_on_behalf('world_assign', tid, 'owner_a', {'FORBIDDEN'}, {'principal_id': actor_id['ic_a']}, says=unscoped)
    flow.assign('owner_a', tid, actor_id['ic_a'])  # 他本人照样能再指派：拒绝只因委托已撤销
    check('a_revoked_delegation_takes_effect_at_once_and_nothing_more_is_recorded_on_its_behalf')
    response = flow.clients['tianshu'].json('POST', '/v1/actions', deepcopy(body), expected={403})
    check('a_command_recorded_on_behalf_cannot_be_replayed_into_success_once_the_delegation_is_revoked',
          response['error']['code'] == 'FORBIDDEN')
    deny_revoke('owner_a', owner_grant['event_id'], {'INVALID_STATE'})
    deny_revoke('ceo', ic_grant['event_id'], {'FORBIDDEN'})
    deny_revoke('tianshu', ic_grant['event_id'], {'FORBIDDEN'})
    deny_revoke('ceo', uid(), {'INVALID_REQUEST'})
    deny_revoke('ceo', result['event_id'], {'INVALID_REQUEST'})
    check('only_the_grantor_revokes_a_grant_of_this_scope_once')

    # ---------------------------------------------------------------- 代 Task 的责任人开始与交付（生命周期）
    started, _ = on_behalf('world_start', tid, 'ic_a')
    delivered, sent = on_behalf('world_deliver', tid, 'ic_a', {'content': {'text': '天枢：执行事项完成'}})
    row = event(delivered['result']['event_id'])
    check('the_service_principal_starts_and_delivers_a_task_on_behalf_of_its_responsible_without_a_declaration',
          life(tid) == {'status': 'delivered', 'display_name': '已交付', 'event_id': delivered['result']['event_id']}
          and event(started['result']['event_id'])['kind'] == 'start'
          and str(event(started['result']['event_id'])['on_behalf_of']) == actor_id['ic_a']
          and row['kind'] == 'deliver' and row['content']['text'] == '天枢：执行事项完成'
          and str(row['principal_id']) == actor_id['tianshu'] and str(row['on_behalf_of']) == actor_id['ic_a']
          and row['external_record_id'] == sent['external_record_id']
          and delivered['result']['responsible_through'] == tid and 'declaration' not in delivered['result']
          and delivered['result']['required_assignment_ids'] == [f['actors']['ic_a']['assignment_id']])
    deny_on_behalf('world_accept', tid, 'ic_a', {'FORBIDDEN'}, says='is recorded by parent')
    check('a_write_on_behalf_of_a_person_who_may_not_record_it_himself_is_forbidden')
    back = act('ic_a', 'world_deliver', tid, {'outcome': 'withdrawn',
                                              'supersedes_event_id': delivered['result']['event_id']})
    row = event(back['event_id'])
    check('the_person_withdraws_in_person_the_event_recorded_on_his_behalf',
          life(tid) == {'status': 'in_progress', 'display_name': '进行中', 'event_id': back['event_id']}
          and str(row['principal_id']) == actor_id['ic_a'] and row['on_behalf_of'] is None
          and str(row['supersedes_event_id']) == delivered['result']['event_id'] and row['outcome'] == 'withdrawn')

    # 委托过期：ic_a 给 agent_a 的那条已过期，agent_a 不能再代他交付；天枢的那条仍有效。
    while datetime.now(timezone.utc) <= datetime.fromisoformat(brief['detail']['valid_until'].replace('Z', '+00:00')):
        time.sleep(0.2)
    deny_on_behalf('world_deliver', tid, 'ic_a', {'FORBIDDEN'}, actor='agent_a', says=unscoped)
    check('an_expired_delegation_lets_nothing_be_recorded_on_behalf_and_is_no_longer_listed',
          brief['event_id'] not in delegations(tid) and ic_grant['event_id'] in delegations(tid))
    again, _ = on_behalf('world_deliver', tid, 'ic_a')
    accepted = act('owner_a', 'world_accept', tid)
    check('the_lifecycle_goes_on_from_a_delegated_delivery_as_if_the_person_had_recorded_it',
          life(tid) == {'status': 'closed', 'display_name': '已关闭', 'event_id': accepted['event_id']}
          and str(event(again['result']['event_id'])['on_behalf_of']) == actor_id['ic_a'])

    # ---------------------------------------------------------------- 范围与记录者
    deny_on_behalf('world_assign', made['ResponsibilityUnit']['object_id'], 'ceo', {'FORBIDDEN'},
                   {'principal_id': actor_id['a']}, says=unscoped)
    check('a_family_outside_the_delegation_is_forbidden')
    deny_on_behalf('world_confirm_long_term_goal', company_goal['object_id'], 'ceo', {'FORBIDDEN'},
                   {'outcome': 'accepted'}, says=unscoped)
    check('a_domain_outside_the_delegation_is_forbidden')
    revoke_assignment(h.env, f, f['actors']['leaver']['assignment_id'])
    deny_on_behalf('world_start', tid, 'leaver', {'FORBIDDEN'}, says=unscoped)
    check('a_delegation_whose_grantor_is_no_longer_a_valid_person_of_the_scope_is_not_in_force',
          leaver_grant['event_id'] not in delegations(tid))
    deny_on_behalf('world_confirm_long_term_goal', company_goal['object_id'], 'ceo', {'FORBIDDEN'},
                   {'outcome': 'accepted'}, actor='a', says='Only an Agent service principal')
    deny_on_behalf('world_confirm_period_goal', pid, 'ceo', {'FORBIDDEN'}, {'outcome': 'returned'}, actor='agent_a',
                   says=unscoped)
    deny_on_behalf('world_start', tid, 'a', {'FORBIDDEN'}, says=unscoped)
    check('only_the_delegated_agent_records_on_behalf_and_only_of_a_person_who_delegated_to_it')
    deny_on_behalf('world_reopen', tid, 'ic_a', {'INVALID_REQUEST'},
                   {'declaration': {'scene': task['ref'], 'trigger': '天枢', 'human_acceptance': {'required': False}}})
    deny_on_behalf('world_reopen', tid, 'ic_a', {'INVALID_REQUEST'}, external_confirmed_at=later(minutes=5))
    deny_on_behalf('world_revise_object', tid, 'owner_a', {'INVALID_REQUEST'}, {'payload': {'title': '代记改名'}})
    check('a_write_on_behalf_carries_no_declaration_a_confirmation_not_later_than_the_recording_and_only_on_a_'
          'delegable_action')

    read = {item['event_id']: item for item in flow.events('outsider', company['object_id'])['events']}
    check('the_company_reads_back_the_delegation_events_as_record_events',
          all(read[e]['class'] == 'record' and read[e]['on_behalf_of'] is None for e in
              (ceo_grant['event_id'], owner_grant['event_id'], revoked['event_id']))
          and read[revoked['event_id']]['detail'] == {'delegation_event_id': owner_grant['event_id']})



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
    # 单元 a 里另一位持 OWNER 的人：Mission 的承诺只由它的 Owner 本人记（#54）。
    f['actors']['owner_a2'] = _seed_actor(h.env, f, 'OWNER', f['domains']['a'])
    # 单元 a 里持 CEO 角色的 Agent：关注标记只由人记，Agent 持角色也不能记（#55）。
    f['actors']['agent_ceo_a'] = _seed_actor(h.env, f, 'CEO', f['domains']['a'], principal_type='agent')
    # 天枢的服务主体（在公司域与单元 a 持 AGENT，代记的受托人）与一位之后会被撤掉指派的委托人（#62）。
    f['actors']['tianshu'] = _seed_actor(h.env, f, 'AGENT', f['domains']['company'], principal_type='agent')
    _grant(h.env, f, f['actors']['tianshu']['principal_id'], 'AGENT', f['domains']['a'])
    f['actors']['leaver'] = _seed_actor(h.env, f, 'IC', f['domains']['a'])
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
        with scenario('gates'):
            gates(book, h, f, flow, trunk)
        with scenario('context_packs'):
            context_packs(book, h, f, flow, trunk, foreign)
        with scenario('mission_lifecycle'):
            mission_lifecycle(book, h, f, flow, trunk)
        with scenario('delegation'):
            delegation(book, h, f, flow, trunk)
        with scenario('mcp_end_to_end'):
            mcp_end_to_end(book, h, f, flow, trunk, url, source)
        with scenario('list_objects'):
            list_objects(book, h, f, flow, trunk, foreign)
        with scenario('issues'):
            issues(book, h, f, flow, trunk)
        with scenario('goal_closure'):
            goal_closure(book, h, f, flow, trunk, EVENT_KINDS)
        with scenario('strategy_gates'):
            strategy_gates(book, h, f, flow, trunk)
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
