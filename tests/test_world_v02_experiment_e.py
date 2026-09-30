"""实验 E（票 #67）：场景文件与标准答案的自洽校验与占位解析、主干正文的出处、E&O DRI 批准（未批准或批准后内容改过，
跑器都拒用）、审阅稿与两份文件一致，以及反例判定（纯函数，按 fixture 逐场景核对出现与不出现）。无库。"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import uuid

import pytest

from experiments.world_v02 import counterexamples, gold, spec

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'experiments/world_v02'
FIXTURES = ROOT / 'tests/fixtures/world_v02_experiment_e'
NAMESPACE = uuid.UUID('6f1c0c1e-67e0-4a67-8e67-000000000067')


def committed():
    return json.loads((FOLDER / 'scenarios.json').read_text()), json.loads((FOLDER / 'gold.json').read_text())


def fake_manifest(scenarios):
    """一份假的播种清单：每个键一个确定的 UUID，版本取场景文件推演出的播种结束时的版本。"""
    world = spec.World(scenarios)
    versions = world.final_versions()
    return {'objects': {key: {'object_id': str(uuid.uuid5(NAMESPACE, key)), 'version': versions[key], 'type': item['type']}
                        for key, item in world.objects.items()},
            'events': {key: str(uuid.uuid5(NAMESPACE, 'event:' + key)) for key in world.events}}


def concrete(ref, manifest):
    found = list(spec.placeholders(ref))
    if not found:
        return ref
    kind, key = found[0]
    return f"event:{manifest['events'][key]}" if kind == 'event' else spec.resolve(ref, manifest['objects'], {})


# ------------------------------------------------------------------ the committed files
def test_the_committed_scenarios_and_gold_are_consistent_and_not_yet_approved():
    scenarios, answers = committed()
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    assert answers['approver'] == 'E&O DRI'
    assert [item['id'] for item in scenarios['scenarios']] == list(spec.SCENARIOS)
    with pytest.raises(gold.NotApproved):
        gold.load_approved(FOLDER / 'gold.json')


def test_the_seeded_versions_follow_the_steps():
    scenarios, _ = committed()
    versions = spec.final_versions(scenarios)
    # 十月周期目标经一轮重走写回出第 2 版；Mission 与 Task 的指派各出一版；修订、建关系各出一版；快照只有一版。
    assert versions['october_goal'] == 2 and versions['strategy'] == 1 and versions['unit_eo'] == 1
    assert versions['mission_lock'] == 2 and versions['task_freeze'] == 2
    assert versions['task_retrieval_report'] == 3 and versions['task_experiment_e'] == 3
    assert versions['agents_mission'] == 2 and versions['activity_b_run'] == 2 and versions['snap_freeze'] == 1


def test_every_why_reaches_the_strategy_and_the_company_through_slots_waiting_for_the_real_material():
    scenarios, answers = committed()
    slots = {item['slot'] for item in scenarios['pending_material']}
    for item in answers['scenario_answers']:
        why = item['questions'][0]['expected']
        assert {'@strategy#responsibility_structure/eo', '@strategy#strategy_core/main-line',
                '@company#identity/long-term-identity'} <= set(why) & slots


def test_the_trunk_repeats_the_october_starting_point_and_the_reviewed_september_text():
    """主干的正文照搬换任务卡之前的十月起点（原样存在 b_source-2026-10.json，公司层来自 0.1 审过的材料），只有 Strategy
    多一条责任单元条目；Agents 单元的正文取自 0.1 审过的 seed.json，按 Content Pact 放进新块的组件（#82），逐条正文
    与 0.1 相同。"""
    scenarios, _ = committed()
    trunk = {step['key']: step for step in scenarios['base']['steps'] if step.get('key')}
    scenario_steps = {step['key']: step for item in scenarios['scenarios'] for step in item['steps'] if step.get('key')}
    october = {step['key']: step for step in json.loads((ROOT / 'experiments/world_v02/b_source-2026-10.json').read_text())['steps']}
    for key in ('company', 'unit_eo', 'company_goal', 'eo_goal', 'october_goal', 'mission_trial', 'mission_experiments',
                'mission_lock', 'task_trial_integration', 'task_trial_week', 'task_trial_acceptance', 'task_experiment_b',
                'task_experiment_e', 'task_retrieval_report', 'task_freeze', 'task_release'):
        assert trunk[key]['payload'] == october[key]['payload'], key
    ours, theirs = trunk['strategy']['payload'], october['strategy']['payload']
    structure = ours['blocks']['responsibility_structure']
    assert {**ours, 'blocks': {**ours['blocks'], 'responsibility_structure': None}} == \
        {**theirs, 'blocks': {**theirs['blocks'], 'responsibility_structure': None}}
    assert structure['text'] == theirs['blocks']['responsibility_structure']['text']
    assert structure['components'] == theirs['blocks']['responsibility_structure']['components'] + [
        {'id': 'agents', 'type': 'unit_entry', 'text': '04 Agents'}]
    assert '04 Agents' in structure['text']
    september = {step['key']: step for step in json.loads((ROOT / 'experiments/world_v01/seed.json').read_text())['steps']
                 if step.get('key')}
    def texts(blocks):  # 各块的正文与组件正文，按块与组件的顺序
        return [text for value in blocks.values()
                for text in ([value['text']] if value.get('text') else []) + [item['text'] for item in value.get('components', [])]]

    assert (texts(scenario_steps['unit_agents']['payload']['blocks'])
            == [september['unit_agents']['payload']['blocks']['definition']['text']])
    for key in ('agents_goal', 'agents_period'):
        assert ([item['text'] for item in scenario_steps[key]['payload']['blocks']['target']['components']]
                == [september[key]['payload']['blocks']['outcome']['text']])
        assert scenario_steps[key]['payload']['title'] == september[key]['payload']['title']
    # 0.1 的定义块成了战役结果，约束块成了 Mission 计划块的关键约束与依赖（Mission 不再有约束块）
    mission = scenario_steps['agents_mission']['payload']['blocks']
    assert list(mission) == ['definition', 'mission_plan']
    assert texts(mission) == texts(september['agents_mission']['payload']['blocks'])
    assert [item['type'] for value in mission.values() for item in value['components']] == ['outcome', 'constraint_dependency']


def test_identities_are_seeded_under_role_names():
    scenarios, _ = committed()
    assert all(name.endswith(('CEO', 'DRI', 'Owner', 'Agent', '责任人')) for name in
               (item['display_name'] for item in scenarios['identities'].values()))


# ------------------------------------------------------------------ placeholders
def test_placeholders_resolve_objects_blocks_components_events_and_people():
    objects = {'goal': {'object_id': '11111111-1111-4111-8111-111111111111', 'version': 2}}
    value = {'refs': ['@goal', '@goal@1#target/ac-lock', '@goal#target', 'event:done'], 'who': '$owner',
             'text': '见 @goal'}
    resolved = spec.resolve(value, objects, {'owner': '33333333-3333-4333-8333-333333333333'},
                            {'done': '44444444-4444-4444-8444-444444444444'})
    assert resolved == {'refs': ['11111111-1111-4111-8111-111111111111@2', '11111111-1111-4111-8111-111111111111@1#target/ac-lock',
                                 '11111111-1111-4111-8111-111111111111@2#target', 'event:44444444-4444-4444-8444-444444444444'],
                        'who': '33333333-3333-4333-8333-333333333333', 'text': '见 @goal'}
    for bad in ('@nowhere', '@goal@3', '$nobody'):
        with pytest.raises(spec.SeedError):
            spec.resolve(bad, objects, {}, {})


# ------------------------------------------------------------------ inconsistency is refused
def _scenario(scenarios, name):
    return next(item for item in scenarios['scenarios'] if item['id'] == name)


@pytest.mark.parametrize('breaks', [
    # 一个场景改了另一个场景认领的对象
    lambda s: _scenario(s, 'constraint_conflict')['steps'].append(
        {'key': 'stray', 'do': 'lifecycle', 'by': 'trial_ic', 'target': 'task_trial_week', 'action': 'world_start', 'params': {}}),
    # 一个场景改了没人认领的主干对象
    lambda s: _scenario(s, 'cross_unit')['steps'].append(
        {'key': 'stray', 'do': 'assign', 'by': 'eo_owner', 'target': 'task_release', 'to': 'trial_ic'}),
    # 两个场景认领同一个主干对象
    lambda s: _scenario(s, 'task_only')['owns'].append('task_freeze'),
    # 引用后面才记下的事件
    lambda s: _scenario(s, 'cross_unit')['steps'][-1]['payload'].update(source_event_refs=['event:ev_rag_design']),
    # 指定的版本比当时的新
    lambda s: s['base']['steps'][10]['payload']['blocks']['definition']['components'][1].update(refs=['@october_goal@2#target/ac-tianshu']),
    # 引用那一版里没有的组件
    lambda s: s['base']['steps'][10]['payload']['blocks']['definition']['components'][1].update(refs=['@october_goal#target/ac-nope']),
    # 引用登记里已经没有的块（Content Pact 之前的周期目标验收块）
    lambda s: s['base']['steps'][10]['payload']['blocks']['definition']['components'][1].update(refs=['@october_goal#acceptance/ac-tianshu']),
    # 被指派者不存在
    lambda s: _scenario(s, 'rework_restart')['steps'][0].update(to='nobody'),
    # 键重复
    lambda s: _scenario(s, 'task_only')['steps'][-1].update(key='assign_experiment_e'),
    # 起点不是本场景的对象
    lambda s: _scenario(s, 'version_change').update(start='task_release'),
    # 时刻既不是 now 也不是时间戳
    lambda s: _scenario(s, 'task_only')['steps'][3]['params'].update(occurred_at='yesterday'),
])
def test_inconsistent_scenarios_are_refused(breaks):
    scenarios, _ = committed()
    breaks(scenarios)
    with pytest.raises(spec.SeedError):
        spec.validate(scenarios)


@pytest.mark.parametrize('breaks', [
    lambda g: g['scenario_answers'][0]['questions'][0]['expected'].remove('@company#identity/long-term-identity'),  # Why 不到 Company
    lambda g: g['scenario_answers'][0]['questions'][1]['expected'].append('@agents_mission#mission_plan/manual-fallback'),  # 诱饵也是应引用
    lambda g: g['scenario_answers'][1]['counterexamples'][0].pop('conflict'),                       # 冲突类没写冲突
    lambda g: g['scenario_answers'][1]['questions'][1]['expected'].remove('@unit_eo#definition/no-graph-db'),  # 要指出冲突的问没引上层
    lambda g: g['scenario_answers'][1]['questions'][5]['expected'].remove('@task_retrieval_report#plan/rag-graph'),  # 要指出冲突的问少了下层一条
    lambda g: g['scenario_answers'][4]['counterexamples'].pop(),                                     # 少了该判的类别
    lambda g: g['scenario_answers'][2]['questions'][0]['expected'].append('@october_goal#target/ac-nope'),  # 组件不存在
    lambda g: g['scenario_answers'][2]['questions'][0]['expected'].append('@october_goal#acceptance/ac-lock'),  # 登记里已经没有的块
    lambda g: g['scenario_answers'][3]['questions'][4]['expected'].append('event:nowhere'),         # 事件不存在
    lambda g: g['scenario_answers'].reverse(),                                                       # 场景顺序
])
def test_inconsistent_gold_is_refused(breaks):
    scenarios, answers = committed()
    breaks(answers)
    with pytest.raises(spec.SeedError):
        spec.validate_gold(answers, scenarios)


# ------------------------------------------------------------------ approval by the E&O DRI
@pytest.fixture
def copies(tmp_path):
    for name in ('scenarios.json', 'gold.json'):
        shutil.copy(FOLDER / name, tmp_path / name)
    return tmp_path


def test_unapproved_gold_is_refused(copies):
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')


def test_approved_gold_is_loaded_until_either_file_changes(copies):
    gold.approve(copies / 'gold.json', 'E&O DRI')
    loaded = gold.load_approved(copies / 'gold.json')
    assert loaded['gold']['approval']['approved_by'] == 'E&O DRI' and loaded['scenarios']['scenarios']
    answers = json.loads((copies / 'gold.json').read_text())
    answers['scenario_answers'][0]['questions'][0]['answer'] += '（改过）'
    (copies / 'gold.json').write_text(json.dumps(answers, ensure_ascii=False))
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')
    gold.approve(copies / 'gold.json', 'E&O DRI')
    gold.load_approved(copies / 'gold.json')
    scenarios = json.loads((copies / 'scenarios.json').read_text())
    scenarios['base']['steps'][0]['payload']['blocks']['identity']['components'][0]['text'] += '（改过）'
    (copies / 'scenarios.json').write_text(json.dumps(scenarios, ensure_ascii=False))
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')


def test_approved_gold_is_refused_for_a_world_seeded_from_other_content(copies):
    approval = gold.approve(copies / 'gold.json', 'E&O DRI')
    assert gold.load_approved(copies / 'gold.json', {'content_sha256': approval['content_sha256']})
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json', {'content_sha256': '0' * 64})


def test_the_approval_is_signed_with_the_approver_role_only(copies):
    for by in ('  ', 'CEO', 'Someone Personal'):
        with pytest.raises(ValueError):
            gold.approve(copies / 'gold.json', by)
    with pytest.raises(gold.NotApproved):
        gold.load_approved(copies / 'gold.json')


def test_the_approval_section_is_outside_the_content_hash():
    scenarios, answers = committed()
    signed = deepcopy(answers)
    signed['approval'] = {'approved_by': 'E&O DRI', 'approved_at': 'x', 'content_sha256': 'y'}
    assert gold.content_sha256(signed, scenarios) == gold.content_sha256(answers, scenarios)
    scenarios['scenarios'][0]['summary'] += '（改过）'
    assert gold.content_sha256(signed, scenarios) != gold.content_sha256(answers, committed()[0])


def test_the_review_document_is_generated_from_the_two_files():
    assert (ROOT / 'docs/world-v02-scenarios-review.md').read_text() == gold.render()


def test_the_review_document_marks_the_slots_waiting_for_the_real_material_and_every_decoy():
    text = gold.render()
    scenarios, answers = committed()
    assert text.count('**待真实战略材料**') >= len(scenarios['pending_material'])
    assert '未批准：实验跑器会拒绝使用' in text
    for item in answers['scenario_answers']:
        for counter in item['counterexamples']:
            assert f"（`{counter['category']}`）" in text


# ------------------------------------------------------------------ counterexamples
CASES = json.loads((FIXTURES / 'answers.json').read_text())['cases']


def test_every_scenario_has_a_case_where_its_counterexamples_occur_and_one_where_none_does():
    for name in spec.SCENARIOS:
        cases = [case for case in CASES if case['scenario'] == name]
        assert any(not case['occurred'] for case in cases), name
        assert set().union(*(case['occurred'] for case in cases)) == spec.REQUIRED[name], name


@pytest.mark.parametrize('case', CASES, ids=[f"{case['scenario']}:{case['name']}" for case in CASES])
def test_the_judge_finds_exactly_the_counterexamples_of_the_fixture(case):
    scenarios, answers = committed()
    manifest = fake_manifest(scenarios)
    resolved = counterexamples.resolve(next(item for item in answers['scenario_answers'] if item['id'] == case['scenario']),
                                       manifest)
    claims = [{**claim, 'refs': [concrete(ref, manifest) for ref in claim['refs']]} for claim in case['claims']]
    found = counterexamples.judge(claims, resolved, case['question'])
    assert set(found) == {counter['category'] for counter in
                          next(item for item in answers['scenario_answers'] if item['id'] == case['scenario'])['counterexamples']}
    for category, result in found.items():
        if category in case['occurred']:
            assert result['occurred'] and result['claims'] == case['occurred'][category], (category, result)
            assert result['reason'] == (case.get('reason') if category == 'conflict_missed' else None)
        else:
            assert not result['occurred'] and result['claims'] == [], (category, result)


def test_the_judge_reads_references_the_way_0_1_answers_wrote_them():
    event = '44444444-4444-4444-8444-444444444444'
    assert counterexamples.normalize(f'  {event.upper()} ') == f'event:{event}'
    assert counterexamples.normalize(f'EVENT: {event}') == f'event:{event}'
    assert counterexamples.parse('11111111-1111-4111-8111-111111111111@2#plan/rag-graph') == {
        'object_id': '11111111-1111-4111-8111-111111111111', 'version': 2, 'block': 'plan', 'component': 'rag-graph'}
    assert counterexamples.parse('not a reference') is None


def test_a_decoy_written_with_a_version_only_catches_that_version():
    scenarios, answers = committed()
    manifest = fake_manifest(scenarios)
    resolved = counterexamples.resolve(answers['scenario_answers'][2], manifest)
    assert resolved['counterexamples']['stale_version']['decoys'][0]['any_version'] is False
    other_unit = counterexamples.resolve(answers['scenario_answers'][0], manifest)['counterexamples']['other_unit']
    assert all(item['any_version'] for item in other_unit['decoys'])


@pytest.mark.parametrize('claims', [
    'not a list', [{'claim': 'x', 'kind': 'opinion', 'refs': []}], [{'claim': 'x', 'kind': 'fact'}],
    [{'claim': 'x', 'kind': 'fact', 'refs': [1]}], [{'kind': 'fact', 'refs': []}],
])
def test_a_malformed_answer_is_refused(claims):
    scenarios, answers = committed()
    resolved = counterexamples.resolve(answers['scenario_answers'][0], fake_manifest(scenarios))
    with pytest.raises(ValueError):
        counterexamples.judge(claims, resolved)


def test_an_unknown_question_is_refused():
    scenarios, answers = committed()
    resolved = counterexamples.resolve(answers['scenario_answers'][1], fake_manifest(scenarios))
    with pytest.raises(ValueError):
        counterexamples.judge([], resolved, 'when')
