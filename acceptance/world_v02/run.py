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
SCENARIOS = ['migration', 'control_plane', 'company', 'rejections', 'coexistence', 'revocation']
EVENT_KINDS = {item['kind']: item for item in json.loads(REGISTRY.read_text())['event_kinds']}


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
          == [(V02, ['world_create_object'], ['Company'])] and registered[0]['content'] == support)
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
                                             'revision_id': made['revision_id'], 'block': None}])
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
    check('components_are_refused_until_they_are_implemented')
    flow.deny('ceo', flow.command('world_create_object', params(external_refs=[{'system': 'tianshu'}])),
              codes={'INVALID_REQUEST'})
    check('an_external_ref_without_an_id_is_refused')
    flow.deny('ceo', flow.command('world_create_object', params(object_type='Strategy')),
              codes={'ACTION_NOT_SUPPORTED_FOR_PROTOCOL'})
    check('an_object_type_not_yet_implemented_under_0_2_is_refused')
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
        with scenario('rejections'):
            rejections(book, h, f, flow, made['company'])
        with scenario('coexistence'):
            coexistence(book, h, f, flow)
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
