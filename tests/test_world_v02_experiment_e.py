"""实验 E（票 #67）：场景文件与标准答案的自洽校验与占位解析、主干正文的出处（Company 与 Strategy 逐条是战略材料的原句）、
E&O DRI 批准（未批准或批准后内容改过，跑器都拒用；仓库里的标准答案批没批准，这些用例都成立）、审阅稿与两份文件一致，
以及反例判定（纯函数，按 fixture 逐场景核对出现与不出现）。无库。"""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import uuid

import pytest

from experiments.world_v02 import counterexamples, gold, spec
from memory_service_runtime.governed import world_v02_models as models

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'experiments/world_v02'
FIXTURES = ROOT / 'tests/fixtures/world_v02_experiment_e'
NAMESPACE = uuid.UUID('6f1c0c1e-67e0-4a67-8e67-000000000067')
# 战略材料里拆进 Company 与 Strategy 组件的原句与页码（#67）；PDF 与抽出的文字不进仓库。
MATERIAL = json.loads((FOLDER / 'strategy_material.json').read_text())
SEPTEMBER = {step['key']: step for step in json.loads((ROOT / 'experiments/world_v01/seed.json').read_text())['steps']
             if step.get('key')}
QUESTIONS = '本轮管理层需要确认的 6 个问题'  # 材料第 10 页的标题
UNAPPROVED = {'approved_by': None, 'approved_at': None, 'content_sha256': None}


def committed():
    return json.loads((FOLDER / 'scenarios.json').read_text()), json.loads((FOLDER / 'gold.json').read_text())


def material_text(item):
    """一条组件的正文：原句只在标题式短语与说明之间加「：」、几条说明之间加「；」，不改字句；token-principle 不在材料里，
    是 0.1 审过的公司约束原文。"""
    if item['page'] is None:
        assert item['kept'] == 'experiments/world_v01/seed.json#company/constraint'
        return SEPTEMBER['company']['payload']['blocks']['constraint']['text']
    head, *rest = item['pieces']
    return head + ('：' + '；'.join(rest) if rest else '')


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
def test_the_committed_scenarios_and_gold_are_consistent_and_the_approval_is_empty_or_current():
    """仓库里的标准答案要么未批准（批准段三项都空，跑器拒用），要么由 E&O DRI 批准了现在的内容（哈希相同，能取用）；
    不许有半截的批准段，也不许是已失效的批准。"""
    scenarios, answers = committed()
    spec.validate(scenarios)
    spec.validate_gold(answers, scenarios)
    assert answers['approver'] == 'E&O DRI'
    assert [item['id'] for item in scenarios['scenarios']] == list(spec.SCENARIOS)
    approval = answers['approval']
    assert set(approval) == set(UNAPPROVED)
    if approval == UNAPPROVED:
        with pytest.raises(gold.NotApproved):
            gold.load_approved(FOLDER / 'gold.json')
    else:
        assert None not in approval.values() and approval['approved_by'] == answers['approver']
        assert approval['content_sha256'] == gold.content_sha256(answers, scenarios)
        assert gold.load_approved(FOLDER / 'gold.json')['gold'] == answers


def test_the_seeded_versions_follow_the_steps():
    scenarios, _ = committed()
    versions = spec.final_versions(scenarios)
    # 十月周期目标经一轮重走写回出第 2 版；Mission 与 Task 的指派各出一版；修订、建关系各出一版；快照只有一版。
    assert versions['october_goal'] == 2 and versions['strategy'] == 1 and versions['unit_eo'] == 1
    assert versions['mission_lock'] == 2 and versions['task_freeze'] == 2
    assert versions['task_retrieval_report'] == 3 and versions['task_experiment_e'] == 3
    assert versions['agents_mission'] == 2 and versions['activity_b_run'] == 2 and versions['snap_freeze'] == 1


def test_every_why_reaches_the_strategy_and_the_company_through_components_the_material_fills():
    """各场景 Why 追到 Strategy 责任结构的 eo（照旧取自战场图 V1）、总体战略的 main-line（材料第 2 页阶段打法）与公司的
    long-term-identity（材料第 10 页问题 1）；Why 引到的 Company 与 Strategy 位置都有材料原句，不是留空的位置。"""
    scenarios, answers = committed()
    empty = {item['slot'] for item in scenarios['pending_material']}
    filled = {f"@{item['object']}#{item['block']}/{item['id']}": item['page'] for item in MATERIAL['components']}
    assert filled['@strategy#strategy_core/main-line'] == 2 and filled['@company#identity/long-term-identity'] == 10
    for item in answers['scenario_answers']:
        why = item['questions'][0]['expected']
        assert {'@strategy#responsibility_structure/eo', '@strategy#strategy_core/main-line',
                '@company#identity/long-term-identity'} <= set(why)
        upper = set()  # 引到 Company 与 Strategy 的，去掉指定的版本
        for ref in why:
            key, _, block, component = spec.parts(ref) if ref.startswith('@') else (None,) * 4
            if key in ('company', 'strategy'):
                upper.add(f'@{key}' + (f'#{block}' if block else '') + (f'/{component}' if component else ''))
        assert not upper & empty
        assert upper - {'@strategy#responsibility_structure/eo'} <= {ref for ref, page in filled.items() if page}


def test_the_strategy_material_lists_each_sentence_once_with_its_page_and_keeps_the_open_questions_open():
    """原句清单（#67）：每条组件一个 id，页码在 1–10；一句原句只放一处；合成一句时第一段是标题式短语（不以标点结尾）。
    第 10 页「本轮管理层需要确认的 6 个问题」六问都在，每问带着该页标题、以问句原样放进组件，不写成已定；这个标题不出现
    在别的组件里。只有 token-principle 不在材料里，保留 0.1 原文。"""
    items = MATERIAL['components']
    assert MATERIAL['source'] == '公司知识库《总体战略定位与阶段路径》（2026-07）'
    assert len({(item['object'], item['id']) for item in items}) == len(items)
    assert [(item['object'], item['id'], set(item) - {'object', 'block', 'id', 'type', 'page'})
            for item in items if item['page'] is None] == [('company', 'token-principle', {'kept'})]
    sentences = []
    for item in items:
        if item['page'] is None:
            continue
        assert item['page'] in range(1, 11) and set(item) == {'object', 'block', 'id', 'type', 'page', 'pieces'}, item['id']
        assert item['pieces'] and all(piece and piece == piece.strip() and '\n' not in piece for piece in item['pieces'])
        if len(item['pieces']) > 1:
            assert not re.search(r'[，。；：、？！]$', item['pieces'][0]), item['id']
        sentences += item['pieces'][1:] if item['pieces'][0] == QUESTIONS else item['pieces']
    assert len(sentences) == len(set(sentences))
    asked = [item for item in items if item['page'] == 10]
    assert len(asked) == 6
    for item in asked:
        head, question = item['pieces']
        assert head == QUESTIONS and question.startswith('是否确认') and question.endswith('？'), item['id']
    assert not any(QUESTIONS in piece for item in items if item['page'] != 10 for piece in item.get('pieces', []))


def test_the_company_and_the_strategy_of_the_three_seed_files_are_the_sentences_of_the_material():
    """实验 E 的主干、十月起点原计划（b_source-2026-10.json）与实例的播种计划（deploy/world-02/seed-eo-2026-10.json）里，
    Company 与 Strategy（战略责任结构块除外）逐条照 strategy_material.json：块、顺序、id、类型相同，正文逐字等于原句按
    拼法合成的一句，块里没有别的文字、引用与链接；两步的说明逐条写出组件的页码。标题与上级沿用 0.1，责任结构块的正文
    照旧，载荷按登记严格校验。"""
    scenarios, _ = committed()
    plans = {'scenarios.json 的主干': scenarios['base']['steps'],
             'b_source-2026-10.json': json.loads((FOLDER / 'b_source-2026-10.json').read_text())['steps'],
             'seed-eo-2026-10.json': json.loads((ROOT / 'deploy/world-02/seed-eo-2026-10.json').read_text())['steps']}
    fake = {key: str(uuid.uuid5(NAMESPACE, key)) + '@1' for key in ('company', 'strategy')}
    for name, steps in plans.items():
        found = {step['key']: step for step in steps if step.get('key')}
        for key, kind in (('company', 'Company'), ('strategy', 'Strategy')):
            payload, note = found[key]['payload'], found[key]['note']
            expected = {}
            for item in MATERIAL['components']:
                if item['object'] != key:
                    continue
                expected.setdefault(item['block'], []).append(
                    {'id': item['id'], 'type': item['type'], 'text': material_text(item)})
                assert (f"{item['id']} 第 {item['page']} 页" if item['page'] else f"{item['id']} 出自 9/23 会议") in note, \
                    (name, item['id'])
            blocks = {block: value for block, value in payload['blocks'].items() if block != 'responsibility_structure'}
            assert {block: value['components'] for block, value in blocks.items()} == expected, (name, key)
            assert all(set(value) == {'components'} for value in blocks.values()), (name, key)
            assert ({k: v for k, v in payload.items() if k != 'blocks'}
                    == {k: v for k, v in SEPTEMBER[key]['payload'].items() if k != 'blocks'}), (name, key)
            models.validate_input(kind, {**payload, **({'parent_ref': fake['company']} if key == 'strategy' else {})})
        structure = found['strategy']['payload']['blocks']['responsibility_structure']
        assert structure['text'] == SEPTEMBER['strategy']['payload']['blocks']['responsibility_structure']['text'], name


def test_the_empty_slots_are_exactly_the_component_types_the_material_does_not_cover():
    """材料没讲到的组件类型留空，pending_material 按块列出它们并写明「材料里没有」；材料用到的类型都在所在块允许的
    类型里。战略责任结构块不取自这份材料，不列。"""
    scenarios, _ = committed()
    names = {item['id']: item['display_name'] for item in spec.REGISTRY['components']['types']}
    empty = {}
    for key, kind in (('company', 'Company'), ('strategy', 'Strategy')):
        for block in spec.OBJECT_SPECS[kind]['blocks']:
            if block['id'] == 'responsibility_structure':
                continue
            used = {item['type'] for item in MATERIAL['components'] if (item['object'], item['block']) == (key, block['id'])}
            assert used and used <= set(block['components']), (key, block['id'])
            missing = [type_id for type_id in block['components'] if type_id not in used]
            if missing:
                empty[f"@{key}#{block['id']}"] = '、'.join(names[type_id] for type_id in missing) + '：材料里没有，留空'
    assert {item['slot']: item['stub'] for item in scenarios['pending_material']} == empty == {
        '@company#identity': '企业使命：材料里没有，留空', '@strategy#strategy_core': '目标客户、目标市场：材料里没有，留空'}


def test_the_trunk_repeats_the_october_starting_point_and_the_reviewed_september_text():
    """主干的正文照搬换任务卡之前的十月起点（原样存在 b_source-2026-10.json；Company 与 Strategy 是战略材料的原句，其余
    公司层来自 0.1 审过的材料），只有 Strategy 多一条责任单元条目；Agents 单元的正文取自 0.1 审过的 seed.json，按
    Content Pact 放进新块的组件（#82），逐条正文与 0.1 相同。"""
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
    """两份文件的临时副本，批准段置空：仓库里的标准答案批没批准，下面的用例都从未批准测起。"""
    for name in ('scenarios.json', 'gold.json'):
        shutil.copy(FOLDER / name, tmp_path / name)
    answers = json.loads((tmp_path / 'gold.json').read_text())
    (tmp_path / 'gold.json').write_text(json.dumps({**answers, 'approval': UNAPPROVED}, ensure_ascii=False, indent=2) + '\n')
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


def test_the_review_document_marks_the_empty_slots_the_page_of_every_material_sentence_and_every_decoy():
    text = gold.render()
    scenarios, answers = committed()
    assert '待真实战略材料' not in text
    assert text.count('**材料里没有**') >= len(scenarios['pending_material'])
    assert all(f"| {item['stub']} |" in text for item in scenarios['pending_material'])
    for item in MATERIAL['components']:  # 两步的说明逐条写出页码，审阅稿照录
        assert (f"{item['id']} 第 {item['page']} 页" if item['page'] else f"{item['id']} 出自 9/23 会议") in text
    for item in answers['scenario_answers']:
        for counter in item['counterexamples']:
            assert f"（`{counter['category']}`）" in text
    approval = answers['approval']
    status = ('未批准：实验跑器会拒绝使用' if approval == UNAPPROVED
              else f"已由 {approval['approved_by']} 于 {approval['approved_at']} 批准")
    assert f'- 批准状态：{status}\n' in text and '批准已失效' not in text


def test_the_review_document_of_unapproved_gold_says_the_runner_refuses_it(copies):
    text = gold.render(copies / 'gold.json')
    assert '- 批准状态：未批准：实验跑器会拒绝使用\n' in text
    gold.approve(copies / 'gold.json', 'E&O DRI')
    assert '- 批准状态：已由 E&O DRI 于 ' in gold.render(copies / 'gold.json')


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
