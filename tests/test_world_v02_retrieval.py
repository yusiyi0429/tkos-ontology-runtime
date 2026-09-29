"""四种取法对照（票 #68）：RAG 的切分、BM25 排序与同成本装入，跑器的隔离与批准门，带门的指标（含 short 与无有效
运行）、反例统计与四组的成本口径，对照实验 B 的两种口径，四个触发检查的三种输出，以及只由 summary.json 生成的报告。
无库、不接模型。对照实验 B 用 #66 录好的冒烟运行日志（tests/fixtures/world_v02_experiment_b/）经 b_observe 算出的观测。

fixture（tests/fixtures/world_v02_retrieval/）：
- corpus.json：一次真实播种（实验 E）的读投影里取的 8 个对象与 25 条事件，id 按实验 E 测试的假播种清单换过
  （有键的对象与事件用 uuid5，其余顺序编号），正文不变；
- prepared.json：同一次播种上准备命令的输出，id 同样换过；
- runs.json：录好的运行（引用写成占位，测试里换成具体引用后写成跑器的输出目录）。
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import uuid

import pytest

from experiments.world_v01.experiment import contamination
from experiments.world_v02 import b_observe, counterexamples, experiment, gold, metrics, report, retrieval, spec, triggers

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'experiments/world_v02'
FIXTURES = ROOT / 'tests/fixtures/world_v02_retrieval'
B_FIXTURES = ROOT / 'tests/fixtures/world_v02_experiment_b'  # #66 录好的冒烟运行日志
CODEX = ROOT / 'tests/fixtures/world_v01_experiment/codex'
NAMESPACE = uuid.UUID('6f1c0c1e-67e0-4a67-8e67-000000000067')  # 与实验 E 的测试同一个假播种清单


def committed():
    return json.loads((FOLDER / 'scenarios.json').read_text()), json.loads((FOLDER / 'gold.json').read_text())


def fake_manifest(scenarios, answers):
    world = spec.World(scenarios)
    versions = world.final_versions()
    return {'content_sha256': gold.content_sha256(answers, scenarios),
            'scope': {'scope_id': str(uuid.uuid5(NAMESPACE, 'scope')), 'tenant_id': 'experiment-r-fixture'},
            'objects': {key: {'object_id': str(uuid.uuid5(NAMESPACE, key)), 'version': versions[key], 'type': item['type']}
                        for key, item in world.objects.items()},
            'events': {key: str(uuid.uuid5(NAMESPACE, 'event:' + key)) for key in world.events}}


@pytest.fixture(scope='module')
def world():
    scenarios, answers = committed()
    manifest = fake_manifest(scenarios, answers)
    return {'answers': answers, 'manifest': manifest, 'golds': metrics.resolve(answers, manifest)}


def concrete(ref, manifest):
    kind, key = next(spec.placeholders(ref))
    return f"event:{manifest['events'][key]}" if kind == 'event' else spec.resolve(ref, manifest['objects'], {})


def corpus():
    return json.loads((FIXTURES / 'corpus.json').read_text())


def prepared():
    return json.loads((FIXTURES / 'prepared.json').read_text())


def write_runs(output: Path, manifest: dict, runs: list[dict]) -> None:
    """录好的运行（占位）写成跑器的输出目录：模型遍历组写 mcp/ 运行日志，其余写 context.json。"""
    def split(refs):
        taken = [concrete(ref, manifest) for ref in refs]
        return ([ref for ref in taken if not ref.startswith('event:')],
                [ref[len('event:'):] for ref in taken if ref.startswith('event:')])

    for run in runs:
        name = f"{run['scenario']}.{run['question']}-{run['attempt']}" + (f"-retry{run['retry']}" if run.get('retry') else '')
        folder = output / run['group'] / name
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({key: run[key] for key in
                                                     ('group', 'scenario', 'question', 'attempt', 'status')}))
        if 'answer' in run:
            claims = [{**claim, 'refs': [concrete(ref, manifest) for ref in claim['refs']]}
                      for claim in run['answer']['claims']]
            (folder / 'answer.json').write_text(json.dumps({'claims': claims}, ensure_ascii=False))
        if 'context' in run:
            refs, events = split(run['context']['taken'])
            (folder / 'context.json').write_text(json.dumps({'refs': refs, 'event_ids': events,
                                                             'chars': run['context']['chars']}))
        else:
            (folder / 'mcp').mkdir()
            lines = []
            for line in run['log']:
                refs, events = split(line['taken'])
                lines.append(json.dumps({'tool': line['tool'], 'chars': line['chars'], 'read_refs': refs,
                                         'read_event_ids': events}))
            (folder / 'mcp/run.jsonl').write_text('\n'.join(lines) + '\n')


def recorded(tmp_path, world):
    output = tmp_path / 'out'
    write_runs(output, world['manifest'], json.loads((FIXTURES / 'runs.json').read_text())['runs'])
    return output, {name: metrics.load_runs(output / name) for name in metrics.GROUPS}


def perfect_runs(golds: dict, attempts: int = 3) -> list[dict]:
    """每问 attempts 次有效运行：取到的就是应引项，每条应引项一条 fact 断言；要指出冲突的问另有一条同时引两侧的
    conflict 断言。四项门都过。"""
    runs = []
    for scenario, item in golds.items():
        conflict = item['counterexamples'].get('conflict_missed', {}).get('conflict')
        for question, expected in item['questions'].items():
            claims = [{'claim': '陈述', 'kind': 'fact', 'refs': [ref]} for ref in expected]
            if conflict and question in conflict['questions']:
                claims.append({'claim': '冲突', 'kind': 'conflict', 'refs': conflict['upper'] + conflict['lower']})
            runs += [{'scenario': scenario, 'question': question, 'attempt': attempt, 'status': 'ok',
                      'taken': set(expected), 'chars': 1000, 'answer': {'claims': deepcopy(claims)}}
                     for attempt in range(1, attempts + 1)]
    return runs


def summary_of(world, runs: dict, observations=None):
    result = metrics.summarize(world['golds'], runs, 3, prepared())
    result['setup'] = None
    result['b'] = triggers.b_conclusions(observations)
    result['triggers'] = triggers.evaluate(result)
    result['thresholds'] = triggers.THRESHOLDS
    return result


# ------------------------------------------------------------------ RAG: chunking
def test_each_chunk_carries_exactly_its_own_reference_and_the_full_text_carries_every_one():
    found = retrieval.chunks(corpus())
    assert {chunk.kind for chunk in found} == {'object', 'block', 'component', 'event'}
    assert [chunk.order for chunk in found] == list(range(len(found)))
    for chunk in found:
        assert f'`{chunk.ref}`' in chunk.text.splitlines()[0]  # 分块开头带自己的业务引用
        refs, events = retrieval.taken([chunk])
        assert refs | {f'event:{event}' for event in events} == {chunk.ref}
    full = retrieval.full(found)
    assert set(full['refs']) | {f"event:{event}" for event in full['event_ids']} == {chunk.ref for chunk in found}
    assert full['chars'] == len(full['text']) and all(chunk.text in full['text'] for chunk in found)


def test_objects_are_cut_into_a_header_their_blocks_and_components_and_every_event_is_a_chunk(world):
    found = {chunk.ref: chunk for chunk in retrieval.chunks(corpus())}
    manifest = world['manifest']
    task, plan = concrete('@task_retrieval_report', manifest), concrete('@task_retrieval_report#plan', manifest)
    component = concrete('@task_retrieval_report#plan/rag-graph', manifest)
    assert found[task].kind == 'object' and '责任人' in found[task].text and '生命周期' in found[task].text
    assert found[plan].kind == 'block' and component not in found[plan].text  # 块不含组件，组件另成分块
    assert found[component].kind == 'component' and '图数据库' in found[component].text
    snapshot = concrete('@snap_retrieval', manifest)
    assert found[snapshot].kind == 'object' and '主体' in found[snapshot].text  # 快照：表头加块
    assert found[concrete('@snap_retrieval#progress', manifest)].kind == 'block'
    assert found[concrete('event:start_retrieval', manifest)].kind == 'event'
    assert found[concrete('@task_freeze#plan', manifest)].text.endswith('当前没有计划')  # 空块写标准句
    assert len([chunk for chunk in found.values() if chunk.kind == 'event']) == len(corpus()['events'])


def test_a_chunk_whose_source_carries_a_second_reference_is_refused():
    broken = corpus()
    broken['objects'][0]['business']['extra'] = {'event_id': str(uuid.uuid4()), 'occurred_at': '2026-10-01T00:00:00Z'}
    with pytest.raises(ValueError, match='not exactly its own reference'):
        retrieval.chunks(broken)


# ------------------------------------------------------------------ RAG: BM25 and filling
def test_tokens_are_character_bigrams_without_business_references_or_moments():
    ref = '12345678-1234-4234-8234-123456789abc@2#plan/rag-graph'
    assert retrieval.tokens(f'图数据库 `{ref}` RAG，a') == ['图数', '数据', '据库', 'ra', 'ag', 'a']
    assert retrieval.tokens('event:12345678-1234-4234-8234-123456789abc') == []
    # 时刻每次播种都不同，不进检索文本：同样的内容在不同的播种上排出同样的名次
    assert retrieval.tokens('- 2026-09-29T02:24:40.380849Z 开始') == retrieval.tokens('- 2026-10-01T00:00:00Z 开始') \
        == ['开始']


def test_bm25_ranks_by_score_then_document_order_and_is_deterministic():
    found = retrieval.chunks(corpus())
    index = retrieval.Index(found)
    ranked = index.rank('四种取法对照（含 RAG）与实验报告\n凭什么这样做？受什么约束？')
    assert ranked and ranked == retrieval.Index(retrieval.chunks(corpus())).rank(
        '四种取法对照（含 RAG）与实验报告\n凭什么这样做？受什么约束？')
    assert all(score > 0 for score, _ in ranked)
    keys = [(-score, chunk.order) for score, chunk in ranked]
    assert keys == sorted(keys)
    # 同分按文档顺序：两块正文相同时名次按先后
    twin = [retrieval.Chunk(f'ref-{n}', 'block', n, str(n), '图数据库', {}) for n in range(3)]
    assert [chunk.order for _, chunk in retrieval.Index(twin).rank('图数据库')] == [0, 1, 2]
    assert retrieval.Index(twin).rank('完全无关') == []  # 分数为 0 的不取


def test_filling_takes_chunks_by_rank_up_to_the_budget_and_skips_what_does_not_fit():
    chunks = [retrieval.Chunk(f'r{n}', 'block', n, '', 'x' * size, {}) for n, size in enumerate([50, 80, 30, 20])]
    ranked = [(4.0, chunks[0]), (3.0, chunks[1]), (2.0, chunks[2]), (1.0, chunks[3])]
    packed = retrieval.fill(ranked, 100)
    # 50 装下；80 装不下，记下、接着看；30（加一个空行 2 字符）装下；20 装不下（82 + 22 > 100）
    assert [chunk.ref for _, chunk in packed['kept']] == ['r0', 'r2']
    assert [chunk.ref for _, chunk in packed['skipped']] == ['r1', 'r3']
    assert packed['chars'] == 82 == len(retrieval.render([chunk for _, chunk in packed['kept']]))
    assert retrieval.fill(ranked, 10)['kept'] == []


def test_retrieval_is_deterministic_and_stays_within_the_same_cost_as_the_fixed_path(world):
    found = retrieval.chunks(corpus())
    index = retrieval.Index(found)
    # 同成本对照：上限是固定路径这一问交给模型的字符数（写进提示词的取上下文返回），不是 Markdown 的 used_chars
    fixed = prepared()['questions']['constraint_conflict.what']['contexts']['fixed']
    limit = fixed['chars']
    assert limit > fixed['used_chars'] and prepared()['questions']['constraint_conflict.what']['contexts']['rag'][
        'max_chars'] == limit
    first = retrieval.retrieve(index, '四种取法对照（含 RAG）与实验报告', '这个 Task 要做成什么、打算怎么做？', limit)
    again = retrieval.retrieve(retrieval.Index(retrieval.chunks(corpus())), '四种取法对照（含 RAG）与实验报告',
                               '这个 Task 要做成什么、打算怎么做？', limit)
    assert first == again and first['max_chars'] == limit and first['chars'] == len(first['text']) <= limit
    assert set(first['refs']) | {f'event:{event}' for event in first['event_ids']} == {item['ref'] for item in first['kept']}
    assert concrete('@task_retrieval_report#plan/rag-graph', world['manifest']) in first['refs']
    assert first['query'].startswith('四种取法对照')  # 查询是起点标题加问题


# ------------------------------------------------------------------ the runner
def test_the_four_groups_answer_with_the_same_schema_and_differ_only_in_how_they_get_context(tmp_path):
    assert list(experiment.GROUPS) == ['full', 'fixed', 'traverse', 'rag']
    assert list(experiment.select(['rag', 'full'])) == ['full', 'rag']
    assert experiment.GROUPS['traverse'] == ('world_get_object', 'world_get_events', 'world_get_state', 'world_list_objects')
    assert all(not experiment.GROUPS[name] for name in ('full', 'fixed', 'rag'))
    assert experiment.ANSWER['properties']['claims']['items']['properties']['kind']['enum'] == ['fact', 'gap', 'conflict']
    texts = {name: experiment.prompt(name, 'x', '为什么？', None if name == 'traverse' else '上下文正文')
             for name in experiment.GROUPS}
    rules = texts['traverse'].split('回答要求：')[1]
    assert '取对象、取事件、取状态、列对象' in texts['traverse'] and '取上下文' not in texts['traverse']
    for name in ('full', 'fixed', 'rag'):
        assert '上下文正文' in texts[name] and texts[name].split('回答要求：')[1] == rules.replace('工具返回', '上面的内容')
    assert '`<object_id>@<版本>#<块 id>/<组件 id>`' in rules and 'conflict' in rules


def test_traversal_runs_the_mcp_server_on_contract_02_and_the_other_groups_get_no_tools(tmp_path):
    traverse = experiment.codex_command('gpt-6-sol', 'medium', tmp_path, tmp_path / 's.json', tmp_path / 'a.json',
                                        'http://127.0.0.1:8010', tmp_path / 'mcp', '问题', experiment.GROUPS['traverse'])
    env = next(arg for arg in traverse if arg.startswith('mcp_servers.tkos_world.env='))
    assert 'TKOS_WORLD_CONTRACT_VERSION = "tkos.world/0.2"' in env and 'TOKEN' not in env
    assert ('mcp_servers.tkos_world.enabled_tools=["world_get_object", "world_get_events", "world_get_state", '
            '"world_list_objects"]') in traverse
    assert '--ignore-user-config' in traverse and ['-s', 'read-only'] == traverse[traverse.index('-s'):traverse.index('-s') + 2]
    for name in ('full', 'fixed', 'rag'):
        command = experiment.codex_command('gpt-6-sol', 'medium', tmp_path, tmp_path / 's.json', tmp_path / 'a.json',
                                           'http://127.0.0.1:8010', tmp_path / 'mcp', '问题', experiment.GROUPS[name])
        assert not any(arg.startswith('mcp_servers.') for arg in command) and command[-1] == '问题'


def test_any_tkos_world_call_is_contamination_for_the_groups_without_tools_and_get_context_is_for_traversal():
    events = [json.loads(line) for line in (CODEX / 'clean.jsonl').read_text().splitlines()]
    assert contamination(events, experiment.GROUPS['fixed'])
    assert contamination(events, experiment.GROUPS['traverse']) == ['mcp_tool_call:tkos_world/world_get_context']


def test_the_fixed_path_text_is_what_the_mcp_server_hands_over_and_its_taken_set_is_the_run_log_one():
    block = {'id': 'definition', 'empty': False, 'text': '定义', 'kind': 'definition', 'components': [],
             'ref': '11111111-1111-4111-8111-111111111111@2#definition'}
    event = {'event_id': '22222222-2222-4222-8222-222222222222', 'occurred_at': '2026-10-01T00:00:00Z',
             'ref': 'event:22222222-2222-4222-8222-222222222222'}
    body = {'context_pack_id': 'p', 'budget': {'used_chars': 8000, 'max_chars': 12000, 'over_budget': False},
            'coverage': {'why': {'answered': True}, 'now': {'answered': False}},
            'plan': {'trimmed': [{'kind': 'event', 'level': 0, 'key': event['ref'], 'reason': 'over_level_cap'},
                                 {'kind': 'block', 'level': 3, 'key': 'block:x@1#choices', 'reason': 'over_budget'}]},
            'context_pack': {'markdown': '# 上下文', 'layers': [{'level': 0, 'blocks': [block], 'events': [event], 'object': {
                'title': 'Task', 'ref': '11111111-1111-4111-8111-111111111111@2'}}]}}
    fixed = experiment.fixed_context(body)
    shown = json.loads(fixed['text'])
    assert set(shown) == {'context_pack_id', 'markdown', 'coverage', 'budget'}
    assert shown['budget']['trimmed'] == {'over_level_cap': {'event': 1}, 'over_budget': {'block': 1}}
    assert fixed['refs'] == ['11111111-1111-4111-8111-111111111111@2', block['ref']]
    assert fixed['event_ids'] == [event['event_id']] and fixed['chars'] == len(fixed['text'])
    assert fixed['used_chars'] == 8000 and fixed['max_chars'] == 12000 and fixed['coverage'] == {'why': True, 'now': False}


class FakeReader:
    """按 fixture 的读投影回答取对象（带 version）；别的版本 404。"""

    def __init__(self, found):
        self.views = {(view['business'] if 'business' in view else view)['object_id']: view for view in found['objects']}

    def json(self, method, path, body=None, expected=200):
        object_id, _, version = path.rsplit('/', 1)[1].partition('?version=')
        view = self.views.get(object_id)
        if view is None or (view['business'] if 'business' in view else view)['version'] != int(version):
            return {'error': {'code': 'NOT_FOUND'}}
        return view


def test_verification_flags_references_missing_from_the_text_or_not_readable_back(world):
    found = corpus()
    full = retrieval.full(retrieval.chunks(found))
    assert experiment.verify(FakeReader(found), found, {'full': full})['refs_read_back']
    task = concrete('@task_freeze', world['manifest'])
    bad = {**full, 'refs': full['refs'] + [f"{task.split('@')[0]}@9#plan", f"{task}#nope"],
           'text': full['text'].replace(concrete('@task_freeze#plan', world['manifest']), '')}
    checks = experiment.verify(FakeReader(found), found, {'full': bad})
    assert not checks['refs_in_text'] and concrete('@task_freeze#plan', world['manifest']) in checks['not_in_text']['full']
    assert not checks['refs_read_back'] and checks['not_read_back'] == sorted([f"{task.split('@')[0]}@9#plan", f"{task}#nope"])


def seeded(tmp_path, manifest):
    seeded_output, seeded_private = tmp_path / 'seed-out', tmp_path / 'seed-private'
    seeded_output.mkdir()
    seeded_private.mkdir()
    (seeded_output / 'manifest.json').write_text(json.dumps(manifest))
    (seeded_private / f"scope-{manifest['scope']['scope_id'][:8]}.json").write_text(json.dumps(
        {'actors': {experiment.AGENT: {'principal_id': 'p', 'token': 'fixture-not-a-credential'}}}))
    return seeded_private, seeded_output


def test_the_runner_refuses_gold_answers_the_eo_dri_has_not_approved(tmp_path, world):
    seeded_private, seeded_output = seeded(tmp_path, world['manifest'])
    with pytest.raises(gold.NotApproved):
        experiment.run(tmp_path / 'env.json', seeded_private, seeded_output, tmp_path / 'p', tmp_path / 'o', 'm', 'e')
    assert not (tmp_path / 'o').exists() and not (tmp_path / 'p').exists()
    with pytest.raises(gold.NotApproved):
        experiment.summarize(seeded_output, tmp_path / 'o')


def test_a_rehearsal_needs_no_approval_but_only_runs_on_a_world_seeded_from_the_current_content(tmp_path, world):
    loaded = gold.load_for_rehearsal(manifest=world['manifest'])
    assert loaded['gold'] == world['answers'] and loaded['gold']['approval']['approved_by'] is None
    with pytest.raises(gold.NotApproved, match='other than the current'):
        gold.load_for_rehearsal(manifest={**world['manifest'], 'content_sha256': '0' * 64})
    seeded_private, seeded_output = seeded(tmp_path, world['manifest'])
    with pytest.raises(ValueError, match='at least 1'):
        experiment.run(tmp_path / 'env.json', seeded_private, seeded_output, tmp_path / 'p', tmp_path / 'o', 'm', 'e',
                       attempts=0, rehearsal=True)


def test_summarize_takes_the_mode_and_attempts_from_the_run_and_marks_a_rehearsal_in_the_report(tmp_path, world):
    seeded_private, seeded_output = seeded(tmp_path, world['manifest'])
    output, _ = recorded(tmp_path, world)
    (output / 'prepared.json').write_text(json.dumps({**prepared(), 'content_sha256': world['manifest']['content_sha256']},
                                                     ensure_ascii=False))
    setup = {'started_at': '2026-09-29T12:00:00+00:00', 'model': 'm', 'effort': 'e', 'codex': 'codex-cli x',
             'identity': experiment.AGENT, 'contract_version': experiment.CONTRACT, 'groups': {}, 'attempts': 1,
             'rehearsal': True, 'content_sha256': world['manifest']['content_sha256'],
             'source': {'commit': '0' * 40, 'src_or_experiments_modified': False, 'modified_paths': []}}
    (output / 'setup.json').write_text(json.dumps(setup))
    result = experiment.summarize(seeded_output, output)  # 未经批准的标准答案：彩排照样汇总
    assert result['rehearsal'] is True and result['attempts'] == 1
    text = report.render(json.loads((output / 'summary.json').read_text()))
    assert report.REHEARSAL in text and '每问 1 次' in text
    # 同一批运行记为正式：标准答案未批准就拒绝汇总，彩排的产出不能拿去当正式结果
    (output / 'setup.json').write_text(json.dumps({**setup, 'rehearsal': False, 'attempts': 3}))
    with pytest.raises(gold.NotApproved):
        experiment.summarize(seeded_output, output)


def test_prepare_runs_before_approval_but_refuses_a_world_seeded_from_other_content(tmp_path, world):
    manifest = {**world['manifest'], 'content_sha256': '0' * 64}
    seeded_private, seeded_output = seeded(tmp_path, manifest)
    with pytest.raises(ValueError, match='seeded from other'):
        experiment.prepare_command(tmp_path / 'env.json', seeded_private, seeded_output, tmp_path / 'p', tmp_path / 'o')


# ------------------------------------------------------------------ what the model was shown
def test_traceability_counts_what_the_model_was_shown_while_recall_counts_what_it_took(tmp_path, world):
    golds = world['golds']
    expected = golds['cross_unit']['questions']['basis']
    event = 'event:' + str(uuid.uuid5(NAMESPACE, 'confirm-shown-only-as-a-reference'))
    text = f"周期目标 生命周期：已确认（事件 `{event}`），块 `{expected[0]}`。"
    assert experiment.shown(text) == sorted({event, expected[0]})
    claims = [{'claim': '依据', 'kind': 'fact', 'refs': [expected[0], event]}]
    run = {'taken': set(expected), 'chars': 100, 'answer': {'claims': claims}}
    strict = metrics.score(run, expected, golds['cross_unit'], 'basis')
    shown = metrics.score({**run, 'shown': set(expected) | {event}}, expected, golds['cross_unit'], 'basis')
    assert strict['traceability'] == [0, 1] and shown['traceability'] == [1, 1]
    assert strict['recall'] == shown['recall'] == [len(expected), len(expected)]
    # 跑器把文本里出现过的写进 context.json；模型遍历组取 MCP 运行日志的 refs 与 event_ids
    folder = tmp_path / 'fixed' / 'cross_unit.basis-1'
    folder.mkdir(parents=True)
    (folder / 'run.json').write_text(json.dumps({'group': 'fixed', 'scenario': 'cross_unit', 'question': 'basis',
                                                 'attempt': 1, 'status': 'ok'}))
    (folder / 'context.json').write_text(json.dumps({'refs': [], 'event_ids': [], 'chars': 1, 'shown': [event]}))
    loaded = metrics.load_runs(tmp_path / 'fixed')[0]
    assert loaded['taken'] == set() and loaded['shown'] == {event}
    folder = tmp_path / 'traverse' / 'cross_unit.basis-1'
    (folder / 'mcp').mkdir(parents=True)
    (folder / 'run.json').write_text(json.dumps({'group': 'traverse', 'scenario': 'cross_unit', 'question': 'basis',
                                                 'attempt': 1, 'status': 'ok'}))
    (folder / 'mcp/run.jsonl').write_text(json.dumps({'tool': 'world_get_context', 'chars': 1, 'read_refs': [],
                                                      'read_event_ids': [], 'refs': [expected[0]],
                                                      'event_ids': [event[len('event:'):]]}) + '\n')
    loaded = metrics.load_runs(tmp_path / 'traverse')[0]
    assert loaded['taken'] == set() and loaded['shown'] == {expected[0], event}


def test_saying_a_criterion_is_not_yet_met_is_not_calling_the_block_empty(world):
    gold_item = world['golds']['task_only']
    acceptance = next(item['ref'] for item in gold_item['counterexamples']['content_as_empty']['decoys']
                      if item['ref'].endswith('#acceptance'))
    component = acceptance + '/ac-gold'  # 彩排里全量组那条 gap 断言引的就是这条验收标准
    judged = counterexamples.judge([{'claim': '标准答案还没有批准记录', 'kind': 'gap', 'refs': [component]}],
                                   gold_item, 'now')
    assert judged['content_as_empty']['occurred'] is False
    for decoy in gold_item['counterexamples']['content_as_empty']['decoys']:
        judged = counterexamples.judge([{'claim': '没有安排', 'kind': 'gap', 'refs': [decoy['ref']]}], gold_item, 'now')
        assert judged['content_as_empty']['occurred'] is True
        judged = counterexamples.judge([{'claim': '有安排', 'kind': 'fact', 'refs': [decoy['ref']]}], gold_item, 'now')
        assert judged['content_as_empty']['occurred'] is False


# ------------------------------------------------------------------ metrics and gates
def test_a_group_with_every_run_perfect_passes_all_four_gates(world):
    result = metrics.group(world['golds'], perfect_runs(world['golds']), 3, 30000)
    assert result['verdict'] == {'recall': True, 'traceability': True, 'determinism': True, 'counterexamples': True,
                                 'passed': True}
    assert result['recall'] == 1.0 and result['determinism'] == 1.0 and result['short'] == {}
    assert result['of_full'] == 1000 / 30000
    assert all(item['verdict']['passed'] for item in result['scenarios'].values())


def test_a_question_short_of_valid_runs_fails_determinism_only(world):
    runs = [run for run in perfect_runs(world['golds'])
            if not (run['scenario'] == 'task_only' and run['question'] == 'now' and run['attempt'] == 3)]
    result = metrics.group(world['golds'], runs, 3, 30000)
    assert result['short'] == {'task_only.now': 2}
    assert result['verdict'] == {'recall': True, 'traceability': True, 'determinism': False, 'counterexamples': True,
                                 'passed': False}
    assert result['scenarios']['task_only']['short'] == {'now': 2} and result['scenarios']['cross_unit']['verdict']['passed']


def test_a_group_without_valid_runs_passes_no_gate(world):
    runs = [{**run, 'status': 'failed'} for run in perfect_runs(world['golds'])]
    result = metrics.group(world['golds'], runs, 3, 30000)
    assert not result['valid'] and result['runs'] == {'failed': 90}
    assert result['verdict'] == {'recall': False, 'traceability': False, 'determinism': False, 'counterexamples': False,
                                 'passed': False}
    assert result['recall'] is None and result['chars'] is None and result['of_full'] is None
    assert metrics.group(world['golds'], [], 3, 30000)['verdict']['passed'] is False


def test_recall_and_traceability_are_judged_against_what_each_run_took(world):
    runs = perfect_runs(world['golds'])
    target = [run for run in runs if run['scenario'] == 'version_change' and run['question'] == 'why']
    for run in target:
        run['taken'] = set(list(run['taken'])[:-1])  # 少取一项：召回降，引了它的断言也不可追溯
    result = metrics.group(world['golds'], runs, 3, 30000)
    question = result['scenarios']['version_change']['questions']['why']
    assert question['recall'] == [24, 27] and question['traceability'] == [24, 27]
    total = result['recall_counts']
    assert total[0] == total[1] - 3 and result['verdict']['recall'] and not result['verdict']['traceability']


def test_counterexamples_count_once_per_run_question_and_category_and_fail_the_gate(world):
    runs = perfect_runs(world['golds'])
    golds, manifest = world['golds'], world['manifest']
    for run in runs:
        if run['scenario'] == 'version_change' and run['question'] == 'basis' and run['attempt'] == 1:
            run['answer']['claims'].append({'claim': '旧版', 'kind': 'fact',
                                            'refs': [concrete('@october_goal@1#acceptance/ac-lock', manifest)] * 2})
        if run['scenario'] == 'constraint_conflict' and run['question'] == 'basis' and run['attempt'] == 2:
            run['answer']['claims'] = [claim for claim in run['answer']['claims'] if claim['kind'] != 'conflict']
    result = metrics.group(golds, runs, 3, 30000)
    assert result['counterexamples'] == {'other_unit': 0, 'conflict_missed': 1, 'stale_version': 1, 'stale_state': 0,
                                         'phantom_activity': 0, 'content_as_empty': 0}
    assert result['scenarios']['version_change']['questions']['basis']['counterexamples'] == {'stale_version': 1}
    assert not result['verdict']['counterexamples'] and not result['scenarios']['constraint_conflict']['verdict']['passed']


def test_recorded_runs_score_each_group_on_its_own_taken_set_cost_and_answers(tmp_path, world):
    _, runs = recorded(tmp_path, world)
    result = metrics.summarize(world['golds'], runs, 3, prepared())['groups']
    traverse = result['traverse']
    now = traverse['scenarios']['task_only']['questions']['now']
    assert traverse['runs'] == {'ok': 3, 'contaminated': 1}  # 污染的那次不计入指标
    assert now['runs'] == 3 and now['recall'] == [8, 9] and now['traceability'] == [5, 6]
    assert now['determinism'] == pytest.approx((0.75 + 1 + 0.75) / 3)
    assert now['determinism_cited'] == pytest.approx((2 / 3 + 1 / 2 + 2 / 3) / 3)
    assert now['answer_coverage'] == [7, 9]  # 引了快照的块也算覆盖快照本身
    assert now['counterexamples'] == {'phantom_activity': 1, 'content_as_empty': 0}
    fixed = result['fixed']['scenarios']['task_only']
    assert fixed['questions']['now']['determinism'] == 1.0 and fixed['questions']['what']['counterexamples'] == {
        'phantom_activity': 0, 'content_as_empty': 1}
    assert result['full']['runs'] == {'ok': 1, 'failed': 1} and result['full']['short']['task_only.now'] == 1


def test_cost_is_the_characters_handed_to_the_model_in_each_groups_own_terms(tmp_path, world):
    _, runs = recorded(tmp_path, world)
    groups = metrics.summarize(world['golds'], runs, 3, prepared())['groups']
    # 模型遍历：一次运行里工具返回的字符之和（列对象也算），逐问平均
    assert groups['traverse']['chars'] == (5100 + 4200 + 8100) / 3
    # 其余三组：写进提示词的上下文字符数，逐问平均再对各问平均
    assert groups['fixed']['chars'] == (12847 + 12858) / 2
    assert groups['full']['chars'] == 32141 and groups['rag']['chars'] == 9201
    assert groups['rag']['of_full'] == 9201 / prepared()['full']['chars']
    assert {name for name, item in groups.items() if item['verdict']['passed']} == set()


# ------------------------------------------------------------------ experiment B and the four triggers
def smoke_observations(coarse_only=False):
    """#66 录好的冒烟运行日志经 b_observe 算出的观测。coarse_only：假设 Task-only 线被拒的步骤都只是粗粒度（#66 的
    测试同样这样改日志），#66 口径的结论就成了降为组件。"""
    task_only = json.loads((B_FIXTURES / 'task_only.json').read_text(encoding='utf-8'))
    task_activity = json.loads((B_FIXTURES / 'task_activity.json').read_text(encoding='utf-8'))
    if coarse_only:
        for record in task_only['steps']:
            if record['expression'] == 'rejected':
                record['expression'] = 'coarse'
    return b_observe.observe(task_only, task_activity)


def test_the_b_digest_lists_both_conclusions_and_the_strict_one_comes_from_the_coarse_lists():
    digest = triggers.b_conclusions(smoke_observations())
    assert digest['smoke'] and digest['script']['id'] == 'smoke' and digest['agent_subject'] == 'Activity'
    assert digest['expressions']['task_only'] == {'native': 20, 'coarse': 21, 'rejected': 8}
    assert {name: (item['occurred'], item['task_only']) for name, item in digest['observations'].items()} == {
        'assign': (True, 'coarse'), 'execute': (True, 'inexpressible'), 'retry': (True, 'coarse'),
        'accept': (True, 'coarse'), 'manage': (True, 'inexpressible')}
    # #66 的口径：粗粒度算能表达，只有执行与管理表达不了
    assert digest['conclusion']['activity'] == 'object' and digest['conclusion']['because'] == ['execute', 'manage']
    # 更严的口径：coarse 清单不为空的也留对象
    assert digest['strict'] == {'activity': 'object', 'because': ['assign', 'execute', 'retry', 'accept', 'manage'],
                                'rule': triggers.STRICT_RULE}
    assert [step['step'] for step in digest['observations']['assign']['coarse']] == ['plan:integration', '15']
    assert digest['observations']['assign']['coarse'][0]['writing'] == 'plan_item'
    relaxed = triggers.b_conclusions(smoke_observations(coarse_only=True))
    assert relaxed['conclusion']['activity'] == 'component' and relaxed['strict']['activity'] == 'object'
    assert triggers.b_conclusions(None) is None
    with pytest.raises(ValueError):
        triggers.b_conclusions({'format': 'other'})


def test_activity_to_component_needs_both_the_b_conclusion_and_the_task_only_scenario(world):
    component, smoke = smoke_observations(coarse_only=True), smoke_observations()
    perfect = {'fixed': perfect_runs(world['golds'])}
    check = triggers.activity_to_component
    triggered = check(summary_of(world, perfect, component))
    assert triggered['status'] == triggers.TRIGGERED
    assert triggered['evidence']['b']['strict']['activity'] == 'object'  # 更严的口径只列在依据里，不改判定
    assert check(summary_of(world, perfect, smoke))['status'] == triggers.NOT_TRIGGERED
    missing = check(summary_of(world, perfect))
    assert missing['status'] == triggers.NO_DATA and '#66' in missing['missing'][0] and len(missing['missing']) == 1
    failing = [run for run in perfect_runs(world['golds'])
               if not (run['scenario'] == 'task_only' and run['question'] == 'why' and run['attempt'] == 3)]
    assert check(summary_of(world, {'fixed': failing}, component))['status'] == triggers.NOT_TRIGGERED
    empty = check(summary_of(world, {}, component))
    assert empty['status'] == triggers.NO_DATA and 'task_only' in empty['missing'][0]
    assert check(summary_of(world, {}, smoke))['status'] == triggers.NOT_TRIGGERED  # B 留对象，不必等 E


def test_component_ref_instability_on_the_scan_of_the_read_projection(world):
    manifest = world['manifest']
    scanned = triggers.scan(corpus())
    pinned = {item['ref']: item for item in scanned['component_refs']}
    old = concrete('@october_goal@1#acceptance/ac-lock', manifest)
    assert pinned[old]['cross_revision'] and pinned[old]['present'] and pinned[old]['latest_version'] == 2
    assert not pinned[concrete('@october_goal#acceptance/ac-lock', manifest)]['cross_revision']
    broken = corpus()
    goal = next(view for view in broken['objects']
                if 'business' in view and view['business']['object_id'] == concrete('@october_goal', manifest).split('@')[0])
    for entry in goal['business']['component_ledger']:
        if entry['id'] == 'ac-lock':
            entry['removed_in_version'] = 2
    assert not {item['ref']: item for item in triggers.scan(broken)['component_refs']}[old]['present']
    result = triggers.component_ref_instability(scanned)
    assert result['status'] == triggers.NO_DATA and '至少要 10 条' in result['missing'][0]
    many = {'component_refs': [{'ref': f'r{n}', 'cross_revision': True, 'present': n >= 2} for n in range(12)]}
    assert triggers.component_ref_instability(many)['status'] == triggers.TRIGGERED  # 2/12 > 0.1
    many['component_refs'][1]['present'] = True
    assert triggers.component_ref_instability(many)['status'] == triggers.NOT_TRIGGERED  # 1/12 ≤ 0.1


def test_why_coverage_low_is_triggered_from_the_context_side_alone_and_not_triggered_only_with_answers(world):
    none = summary_of(world, {})
    result = triggers.why_coverage_low(none)
    assert result['status'] == triggers.NO_DATA and len(result['missing']) == 5
    assert all(row['retrieval_recall'] == 1.0 for row in result['evidence']['scenarios'].values())
    assert triggers.why_coverage_low(summary_of(world, {'fixed': perfect_runs(world['golds'])}))['status'] \
        == triggers.NOT_TRIGGERED
    trimmed = summary_of(world, {})
    for scenario in ('cross_unit', 'constraint_conflict', 'version_change'):
        trimmed['prepared']['questions'][f'{scenario}.why']['contexts']['fixed']['recall'][0] = 3
    low = triggers.why_coverage_low(trimmed)
    assert low['status'] == triggers.TRIGGERED and low['missing'] == []
    assert low['evidence']['low'] == ['cross_unit', 'constraint_conflict', 'version_change']
    # 回答覆盖的阈值与召回门一致（0.9）：三个场景的 Why 回答少引一项（6/7、6/7、8/9）就触发
    assert triggers.WHY_COVERAGE == triggers.WHY_RECALL == metrics.GATES['recall'] == 0.9
    runs = perfect_runs(world['golds'])
    for run in runs:
        if run['question'] == 'why' and run['scenario'] in ('cross_unit', 'constraint_conflict', 'version_change'):
            run['answer']['claims'] = run['answer']['claims'][1:]
    answered = triggers.why_coverage_low(summary_of(world, {'fixed': runs}))
    assert answered['status'] == triggers.TRIGGERED
    assert [round(row['answer_coverage'], 3) for row in answered['evidence']['scenarios'].values()] == [
        round(6 / 7, 3), round(6 / 7, 3), round(8 / 9, 3), 1.0, 1.0]
    # 读法：取到召回就不够的是取法或主干关系的问题；都取到了、只是回答没引全的，调整的是怎么让模型答全
    assert low['evidence']['reading'] == 'retrieval' and low['action'] == '改主干关系或取法'
    assert answered['evidence']['reading'] == 'answering' and answered['action'] == triggers.WHY_ANSWERING
    assert '不改主干关系' in triggers.WHY_ANSWERING
    text = report.render({**summary_of(world, {'fixed': runs}), 'triggers': [answered], 'b': None})
    assert f"Why 覆盖持续偏低 → {triggers.WHY_ANSWERING}：**触发**" in text and '读法：Why 链都取到了' in text


def test_issue_detached_follows_each_issue_event_to_the_primarys_snapshot_at_that_time(world):
    manifest = world['manifest']
    found = corpus()
    snapshot = next(view for view in found['objects']
                    if 'business' not in view and view['object_id'] == concrete('@snap_freeze', manifest).split('@')[0])
    primary = snapshot['subject_ref']['object_id']
    issues = next(block for block in snapshot['blocks'] if block['id'] == 'issues')
    issues['components'] = [{'id': 'iss-1', 'type': 'issue', 'ref': f"{snapshot['ref']}#issues/iss-1"}]
    later = {**deepcopy(snapshot), 'object_id': str(uuid.uuid4()), 'as_of': '2099-01-02T00:00:00Z'}
    for block in later['blocks']:
        block['components'] = []  # 后来的快照不再带这个问题
    later['ref'] = f"{later['object_id']}@1"
    found['objects'].append(later)

    def issue(kind, when):
        return {'event_id': str(uuid.uuid4()), 'kind': kind, 'occurred_at': when,
                'subject_refs': [{'object_id': snapshot['object_id'], 'component': 'iss-1'}, {'object_id': primary}]}
    found['events'] += [issue('issue.raised', snapshot['as_of']), issue('issue.routed', '2099-01-01T00:00:00Z'),
                        issue('issue.owned', '2099-01-03T00:00:00Z')]
    scanned = triggers.scan(found)
    assert [(item['kind'], item['in_snapshot']) for item in scanned['issue_events']] == [
        ('issue.routed', True), ('issue.owned', False)]
    assert triggers.issue_detached(scanned)['status'] == triggers.NO_DATA
    assert triggers.issue_detached(triggers.scan(corpus()))['evidence']['issue_events'] == 0
    events = [{'event': f'e{n}', 'in_snapshot': n >= 3} for n in range(6)]
    assert triggers.issue_detached({'issue_events': events})['status'] == triggers.TRIGGERED  # 3/6 ≥ 0.5
    events[0]['in_snapshot'] = True
    assert triggers.issue_detached({'issue_events': events})['status'] == triggers.NOT_TRIGGERED


# ------------------------------------------------------------------ summarize and the report
def approved_copy(tmp_path):
    folder = tmp_path / 'gold'
    folder.mkdir()
    shutil.copy(FOLDER / 'scenarios.json', folder / 'scenarios.json')
    shutil.copy(FOLDER / 'gold.json', folder / 'gold.json')
    gold.approve(folder / 'gold.json', 'E&O DRI')  # 只在临时副本上批准
    return folder / 'gold.json'


def test_summarize_uses_approved_gold_and_the_report_is_generated_from_the_summary_alone(tmp_path, world):
    gold_path = approved_copy(tmp_path)
    answers = json.loads(gold_path.read_text())
    manifest = {**world['manifest'], 'content_sha256': answers['approval']['content_sha256']}
    seeded_private, seeded_output = seeded(tmp_path, manifest)
    output, _ = recorded(tmp_path, world)
    (output / 'prepared.json').write_text(json.dumps({**prepared(), 'content_sha256': manifest['content_sha256']},
                                                     ensure_ascii=False))
    (tmp_path / 'observations.json').write_text(json.dumps(smoke_observations(), ensure_ascii=False))
    result = experiment.summarize(seeded_output, output, gold_path, tmp_path / 'observations.json')
    saved = json.loads((output / 'summary.json').read_text())
    assert saved['groups'] == json.loads(json.dumps(result['groups'])) and saved['b'] == result['b'] is not None
    assert [item['status'] for item in result['triggers']] == [triggers.NOT_TRIGGERED, triggers.NO_DATA,
                                                               triggers.NO_DATA, triggers.NO_DATA]
    assert saved['thresholds']['WHY_COVERAGE'] == 0.9 and saved['thresholds']['REFERENCE'] == 'fixed'
    text = report.render(saved)
    assert text == report.render(json.loads((output / 'summary.json').read_text()))
    traverse = result['groups']['traverse']
    assert f"{traverse['recall']:.2f}（{traverse['recall_counts'][0]}/{traverse['recall_counts'][1]}）" in text
    assert f"{round(result['groups']['fixed']['chars']):,}" in text and '**未运行**' in text
    for item in result['triggers']:
        assert f"{item['condition']} → {item['action']}：{report.STATUS[item['status']]}" in text
    assert '12,000' in text and '8,785 / 12,000' in text  # 预算读自取上下文的返回，逐问给出
    first = prepared()['questions']['cross_unit.why']['contexts']
    assert f"| {first['fixed']['chars']:,} | {first['rag']['chars']:,}（{first['rag']['kept']} 块）" in text  # RAG 上限
    # 对照实验 B：两种口径都列，粗粒度清单逐条写出，冒烟另行注明
    b = text.split('## 七、对照实验 B')[1].split('## 八')[0]
    assert '冒烟脚本（`smoke`）' in b and '| 指派 | 是否要把一段单独指派给别人或 Agent | 是 | 粗粒度 | 0 / 2 / 0 |' in b
    assert '**按 #66 的口径（粗粒度算能表达）**：Activity 留作对象（执行、管理）' in b
    assert '**按更严的口径（粗粒度也算表达不了，由 coarse 清单算出）**：Activity 留作对象（指派、执行、重试、验收、管理）' in b
    assert '- 指派：`plan:integration`（改计划条目）、`15`（改计划条目）' in b and 'Agent 写入需要以谁为主体：Activity' in b
    assert report.B_MISSING in report.render({**saved, 'b': None})
    changed = json.loads((output / 'summary.json').read_text())
    changed['groups']['traverse']['recall'] = 0.123
    assert '0.12（8/9）' in report.render(changed) and '0.12（8/9）' not in text
    (output / 'prepared.json').write_text(json.dumps({**prepared(), 'content_sha256': '0' * 64}, ensure_ascii=False))
    with pytest.raises(ValueError, match='another seeding'):
        experiment.summarize(seeded_output, output, gold_path)
