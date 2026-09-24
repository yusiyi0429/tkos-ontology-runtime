"""静态截面实验的指标（票 #29）：用运行记录、MCP 运行日志与结构化回答 fixture（按真实运行日志的格式手工构造），
以及小的标准答案 fixture 测，不接真模型。期望值都按 fixture 手算：

- A 组 why 三次（取到 2/2、2/2、0/2；断言可追溯 3/3、1/2、1/3），who 三次（事件带大写与空格的写法也认），
  now 一次有效、一次污染不计；
- B 组每问一次取上下文，包里混进了无关事件；
- 全量塞入 20000 字符；
- A0 组（纯模型遍历，#32 D）取 A 组里从没调过取上下文的那些运行（why-2 调过，不算），与 A 同一口径、各自判定。"""
import json
from pathlib import Path

import pytest

from experiments.world_v01 import metrics

FIXTURE = Path(__file__).resolve().parent / 'fixtures/world_v01_experiment'
ACT, TASK, MISSION = 'aaaaaaaa-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002', 'cccccccc-0000-4000-8000-000000000003'
SNAP, AGENTS = 'dddddddd-0000-4000-8000-000000000004', 'eeeeeeee-0000-4000-8000-000000000005'
E2, E3 = '22222222-0000-4000-8000-000000000022', '33333333-0000-4000-8000-000000000033'


def load(name: str) -> dict:
    return json.loads((FIXTURE / name).read_text())


def pure(runs: list[dict]) -> list[dict]:
    return [run for run in runs if all(line['tool'] != 'world_get_context' for line in run['log'])]


@pytest.fixture
def summary():
    gold = metrics.resolve(load('gold.json'), load('manifest.json'))
    a_runs = metrics.load_runs(FIXTURE / 'a')
    return metrics.summarize(gold, {'a': a_runs, 'a0': pure(a_runs)}, metrics.load_runs(FIXTURE / 'b'),
                             load('world.json'), attempts=3)


def test_gold_placeholders_become_the_references_of_this_seeding():
    gold = metrics.resolve(load('gold.json'), load('manifest.json'))
    assert gold['questions'] == {
        'why': {'question': '为什么做？', 'expected': [f'{MISSION}@3#definition', f'{TASK}@3#definition']},
        'who': {'question': '谁负责？', 'expected': [f'{ACT}@2', f'event:{E2}']},
        'now': {'question': '现在怎样？', 'expected': [f'{SNAP}@1']}}
    assert gold['decoys'] == {'old_version': [f'{TASK}@1#definition'], 'other_unit_constraint': [f'{AGENTS}@1#constraint'],
                              'unrelated_event': [f'event:{E3}'], 'empty_block_as_content': [f'{MISSION}@3#acceptance']}
    assert gold['latest'] == {ACT: 2, TASK: 3, MISSION: 3, SNAP: 1, AGENTS: 1}


def test_a_group_is_scored_per_question_from_what_each_run_read_and_cited(summary):
    why, who, now = (summary['a']['questions'][key] for key in ('why', 'who', 'now'))
    # why：第三次只取了 Activity，Mission 的定义只在引用里见过，引它不算可追溯；引空块的 fact 是空块幻觉，引旧版本是反例。
    assert (why['runs'], why['recall'], why['traceability']) == (3, [4, 6], [5, 8])
    # 确定性按运行日志里取到的集合（三次分别取到 9、6、3 项），另记所引集合的一致率。
    assert why['determinism'] == pytest.approx(3 / 7) and why['determinism_cited'] == pytest.approx(5 / 18)
    assert why['chars'] == pytest.approx(6500 / 3)
    assert why['counterexamples'] == {'old_version': 1, 'other_unit_constraint': 0, 'unrelated_event': 0,
                                      'empty_block_as_content': 1}
    # who：大写的对象 id、不带 event: 前缀的事件 id 也认；第二次引了读到的无关事件，可追溯但算反例。
    assert (who['runs'], who['recall'], who['traceability']) == (3, [6, 6], [4, 4])
    assert who['determinism'] == pytest.approx(8 / 9) and who['determinism_cited'] == pytest.approx(7 / 9)
    assert who['chars'] == pytest.approx(4700 / 3)
    assert who['counterexamples']['unrelated_event'] == 1 and who['decoys_taken']['unrelated_event'] == 1
    # now：污染的那次不计，只剩一次有效运行，确定性不适用。
    assert (now['runs'], now['recall'], now['traceability'], now['determinism']) == (1, [1, 1], [1, 1], None)
    assert sum(now['counterexamples'].values()) == 0


def test_a_group_totals_pool_every_valid_run_and_count_what_was_excluded(summary):
    a = summary['a']
    assert a['runs'] == {'ok': 7, 'contaminated': 1}
    assert a['recall'] == pytest.approx(11 / 13) and a['traceability'] == pytest.approx(10 / 13)
    # 确定性是 why 与 who 的平均；now 只有一次有效运行，不计，并列为「不足三次」。
    assert a['determinism'] == pytest.approx(83 / 126) and a['determinism_cited'] == pytest.approx(19 / 36)
    assert a['short'] == {'now': 1}
    assert a['chars'] == pytest.approx(12400 / 9)  # 先逐问平均再对各问平均，与 B 同一口径
    assert a['counterexamples'] == {'old_version': 1, 'other_unit_constraint': 0, 'unrelated_event': 1,
                                    'empty_block_as_content': 1}


def test_b_group_is_scored_from_the_context_pack_it_returned(summary):
    b = summary['b']
    assert b['runs'] == {'ok': 3}
    assert [b['questions'][key]['recall'] for key in ('why', 'who', 'now')] == [[2, 2], [1, 2], [1, 1]]
    assert b['recall'] == pytest.approx(4 / 5)
    assert (b['chars'], b['used_chars']) == (5000, 2000)
    # 每个包里都混进了无关事件；包里以标准句出现的空块不算反例，B 没有回答也就没有可追溯与确定性。
    assert b['decoys_taken'] == {'old_version': 0, 'other_unit_constraint': 0, 'unrelated_event': 3}


def test_the_report_holds_each_metric_against_its_pass_criterion(summary):
    assert summary['full_chars'] == 20000
    # B 的长度按渲染后 Markdown 的字符数（规格的预算口径），另记经 MCP 返回的 JSON 字符数；逐问比，why 的 A 超过了 B。
    assert summary['a']['budget'] == {'of_full': pytest.approx(12400 / 9 / 20000), 'of_b': pytest.approx(12400 / 9 / 2000),
                                      'of_b_json': pytest.approx(12400 / 9 / 5000), 'over': ['why']}
    assert summary['a']['verdict'] == {'recall': False, 'traceability': False, 'budget': False, 'determinism': False,
                                       'counterexamples': False}


def test_the_pure_traversal_group_is_scored_with_the_same_metrics_and_judged_on_its_own(summary):
    a0 = summary['a0']
    assert set(a0) == set(summary['a']) and a0['runs'] == {'ok': 6, 'contaminated': 1}
    why = a0['questions']['why']
    # why 只剩 why-1、why-3：取到 2/2 与 0/2，可追溯 3/3 与 1/3；取到的集合 9 项与 3 项，交 3。
    assert (why['runs'], why['recall'], why['traceability']) == (2, [2, 4], [4, 6])
    assert why['determinism'] == pytest.approx(1 / 3) and why['chars'] == 1750
    assert a0['questions']['who'] == summary['a']['questions']['who']
    assert (a0['recall'], a0['traceability']) == (pytest.approx(9 / 11), pytest.approx(9 / 11))
    assert a0['determinism'] == pytest.approx(11 / 18) and a0['short'] == {'why': 2, 'now': 1}
    assert a0['counterexamples'] == {'old_version': 0, 'other_unit_constraint': 0, 'unrelated_event': 1,
                                     'empty_block_as_content': 1}
    # 同一口径比预算：A0 各问都不超过全量塞入的一半，也不高于 B；A 的 why 因为调了取上下文才超过 B。
    assert a0['chars'] == pytest.approx(11150 / 9)
    assert a0['budget'] == {'of_full': pytest.approx(11150 / 9 / 20000), 'of_b': pytest.approx(11150 / 9 / 2000),
                            'of_b_json': pytest.approx(11150 / 9 / 5000), 'over': []}
    assert a0['verdict'] == {'recall': False, 'traceability': False, 'budget': True, 'determinism': False,
                             'counterexamples': False}
    assert list(summary) == ['a', 'a0', 'b', 'full_chars']


def test_a_group_that_was_not_run_has_no_runs(tmp_path):
    assert metrics.load_runs(tmp_path / 'a0') == []


def test_citing_any_version_older_than_the_seeded_one_is_an_old_version_counterexample():
    gold = metrics.resolve(load('gold.json'), load('manifest.json'))
    run = {'log': [{'chars': 1, 'read_refs': [f'{TASK}@3', f'{TASK}@3#definition']}], 'answer': {'claims': [
        {'claim': '旧版任务', 'kind': 'fact', 'refs': [f'{TASK}@2']},                                  # 不在诱饵清单里的旧版本
        {'claim': '旧版验收', 'kind': 'gap', 'refs': [f'{TASK}@1#acceptance', f'{MISSION}@3#definition']},
        {'claim': '现行定义', 'kind': 'fact', 'refs': [f'{TASK}@3#definition']}]}}
    assert metrics.score(run, [], gold, set())['counterexamples']['old_version'] == 2


def test_answer_coverage_counts_the_expected_references_each_answer_cited(summary):
    """第二轮报告的口径（#32 D）：期望引用是整个对象时，引了它的某个版本或块也算；块与事件要完全相同。
    why：2/2、1/2（引的是旧版本的 Task 定义）、1/2；who 三次都 2/2（大写与不带前缀的写法也认）；
    now 只有一次有效，引了快照的块，算覆盖了整个快照。A0 不含调过取上下文的 why-2。"""
    a = summary['a']
    assert {key: item['answer_coverage'] for key, item in a['questions'].items()} == {
        'why': [4, 6], 'who': [6, 6], 'now': [1, 1]}
    assert a['answer_coverage'] == pytest.approx(11 / 13)
    assert summary['a0']['answer_coverage'] == pytest.approx(10 / 11)


def test_call_patterns_count_calls_repeats_and_runs_that_left_the_start_object():
    """有效的 7 次运行共 12 次调用；只有 why-1 读了起点以外的对象（Task、Mission）；why-2 调了一次取上下文。"""
    gold = metrics.resolve(load('gold.json'), load('manifest.json'))
    runs = metrics.load_runs(FIXTURE / 'a')
    result = metrics.summarize(gold, {'a': runs}, metrics.load_runs(FIXTURE / 'b'), load('world.json'),
                               attempts=3, start=ACT)
    assert result['a']['calls'] == {'runs': 7, 'per_run': pytest.approx(12 / 7), 'repeated': 0,
                                    'context_per_run': pytest.approx(1 / 7), 'left_start': 1,
                                    'objects_per_run': pytest.approx(9 / 7)}
    line = {'tool': 'world_get_object', 'arguments': {'object_id': ACT}, 'chars': 10, 'read_refs': [f'{ACT}@2']}
    repeated = {'question': 'who', 'status': 'ok', 'log': [line, dict(line)], 'answer': None}
    assert metrics.score(repeated, gold['questions']['who']['expected'], gold, set())['repeated_calls'] == 1
