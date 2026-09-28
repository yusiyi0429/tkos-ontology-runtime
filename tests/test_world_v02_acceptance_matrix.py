"""world 0.2 独立验收矩阵（票 #65，不冻结）：格由登记推出，每条检查归到场景与格上；有格未覆盖、有检查没列进矩阵、
列了没跑、放错场景、失败，报告都不是 passed；不适用的格写明理由、不算未覆盖；world_v02_accepted 恒为 false。"""
import ast
import json
from pathlib import Path

from acceptance.world_v02 import matrix
from acceptance.world_v02.matrix import (ACTION_KINDS, CELLS, CHECKS, ITEMS, NOT_APPLICABLE, ROW_KINDS, TITLES,
                                         cell_check, cell_checks, evaluate, render_markdown, row_key, rows,
                                         topic_cells)
from acceptance.world_v02.run import SCENARIOS, Book
from acceptance.world_v02.state_cells import RIGHT, WRONG, repeats

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'docs/contracts/world-registry-0.2.json').read_text())
SUPPORT = json.loads((ROOT / 'docs/runtime-world-support-0.2.json').read_text())


def every_check():
    return {name: scenario for scenario, named in CHECKS.items() for name in named}


def all_passed():
    names = every_check()
    return {name: True for name in names}, dict(names)


def test_the_cells_are_derived_from_the_registry():
    transitions = sum(len(spec['transitions']) for spec in REGISTRY['lifecycles'].values())
    transitions += len(REGISTRY['issue']['lifecycle']['transitions'])
    topics = sum(len(named) for named in topic_cells().values())
    assert len(rows()) == transitions
    assert len(CELLS) == 2 * len(SUPPORT['actions']) + 3 * transitions + topics
    assert {row['item'] for row in CELLS.values()} == set(ITEMS)


def test_every_implemented_action_has_a_passing_and_a_refused_cell():
    assert set(SUPPORT['actions']) == {item['action'] for item in REGISTRY['actions']}
    for action in SUPPORT['actions']:
        for kind in ACTION_KINDS:
            assert f'action:{action}:{kind}' in CELLS, (action, kind)


def test_every_row_of_every_state_table_has_its_three_cells():
    keys = [row_key(object_type, row) for object_type, row in rows()]
    assert len(keys) == len(set(keys))
    for key in keys:
        for kind in ROW_KINDS:
            assert f'lifecycle:{key}:{kind}' in CELLS
    names = [cell_check(object_type, row, kind) for object_type, row in rows() for kind in ROW_KINDS]
    assert len(names) == len(set(names))
    assert set(names) <= set(CHECKS['state_cells'])


def test_topic_cells_follow_the_registry_enumerations():
    named = {cell for cells in topic_cells().values() for cell in cells}
    assert {f'duplicates:{key}' for key in REGISTRY['rules']['duplicates']} <= named
    assert {f'late:backdated:{kind}' for kind in REGISTRY['rules']['ordering']['backdated_kinds']} <= named
    assert {f'withdrawal:never:{item}' for item in REGISTRY['rules']['withdrawal']['never']} <= named
    assert {f"refs:form:{item['form']}" for item in REGISTRY['reference_forms']} <= named
    assert {f"delegation:passes:{item['id']}" for item in REGISTRY['delegation']['families']} <= named
    rounds = [t for t, spec in REGISTRY['lifecycles'].items() if spec.get('rounds')]
    assert {cell.split(':')[1] for cell in named if cell.startswith('round:')} == set(rounds)
    # 一轮进行中进入终态即作废，撤回那条事件则恢复（#69，登记 rounds.voided_in）：每个作废的状态一格，每个类型一格恢复。
    voiding = {t: REGISTRY['lifecycles'][t]['rounds']['voided_in'] for t in rounds}
    assert {cell for cell in named if ':voided_in:' in cell} == {
        f'round:{t}:voided_in:{state}' for t, states in voiding.items() for state in states} != set()
    assert {cell for cell in named if cell.endswith(':restored_by_withdrawal')} == {
        f'round:{t}:restored_by_withdrawal' for t, states in voiding.items() if states}


def test_each_check_is_listed_once_and_only_names_cells_of_the_matrix():
    names = [name for named in CHECKS.values() for name in named]
    assert len(names) == len(set(names))
    for named in CHECKS.values():
        for name, proved in named.items():
            assert set(proved) <= set(CELLS), (name, set(proved) - set(CELLS))


def test_the_groups_are_the_scenarios_of_the_run():
    assert list(CHECKS) == SCENARIOS and list(TITLES) == SCENARIOS
    assert all(CHECKS.values())


def test_every_cell_is_covered_by_a_listed_check_or_says_why_not():
    proved = cell_checks()
    assert set(NOT_APPLICABLE) <= set(CELLS)
    for cell in CELLS:
        assert bool(proved[cell]) != (cell in NOT_APPLICABLE), cell
        assert cell not in NOT_APPLICABLE or NOT_APPLICABLE[cell].strip()


def _scenario_checks():
    """每个场景函数里 check(...) 的名字：字面量原样，f-string 转成正则；其余写法（state_cells 由登记生成）不收。"""
    import re
    found = {}
    for path in sorted((ROOT / 'acceptance/world_v02').glob('*.py')):
        for top in ast.parse(path.read_text()).body:
            if not isinstance(top, ast.FunctionDef) or top.name not in CHECKS:
                continue
            for node in ast.walk(top):
                if not (isinstance(node, ast.Call) and node.args
                        and getattr(node.func, 'attr', getattr(node.func, 'id', None)) == 'check'):
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(top.name, []).append(re.compile(re.escape(arg.value) + '$'))
                elif isinstance(arg, ast.JoinedStr):
                    pattern = ''.join(re.escape(v.value) if isinstance(v, ast.Constant) else '.+?' for v in arg.values)
                    found.setdefault(top.name, []).append(re.compile(pattern + '$'))
                elif isinstance(arg, ast.BinOp):  # 'a' 'b' 拼接的长名字仍是字面量
                    value = ast.literal_eval(arg)
                    found.setdefault(top.name, []).append(re.compile(re.escape(value) + '$'))
    return found


def test_every_check_a_scenario_records_is_listed_under_that_scenario():
    """新加的检查没列进矩阵、或列在别的场景下，这里就失败：加检查不会漏掉矩阵。"""
    found = _scenario_checks()
    generated = {cell_check(object_type, row, kind) for object_type, row in rows() for kind in ROW_KINDS}
    for scenario, patterns in found.items():
        for pattern in patterns:
            assert any(pattern.match(name) for name in CHECKS[scenario]), (scenario, pattern.pattern)
    for scenario, named in CHECKS.items():
        for name in named:
            if name in generated:
                continue
            assert any(pattern.match(name) for pattern in found.get(scenario, [])), (scenario, name)


def test_a_full_run_passes_the_matrix():
    recorded, where = all_passed()
    result = evaluate(recorded, where, {s: {'status': 'completed'} for s in CHECKS})
    assert result['matrix_passed'] is True
    assert result['matrix']['uncovered'] == [] and result['matrix']['not_applicable'] == len(NOT_APPLICABLE)
    assert result['matrix']['covered'] + result['matrix']['not_applicable'] == result['matrix']['cells'] == len(CELLS)
    assert result['mandatory_groups'] == len(CHECKS) and result['mandatory_checks'] == len(recorded)
    assert all(row['passed'] == row['required'] for row in result['groups'].values())
    assert set(result['action_coverage']) == set(SUPPORT['actions'])
    assert all(cells['passes'] and cells['refused'] for cells in result['action_coverage'].values())


def test_an_uncovered_cell_fails_the_matrix():
    recorded, where = all_passed()
    cell = 'duplicates:other_keys_record_events'
    only = cell_checks()[cell]
    assert only == ['the_same_external_event_under_two_keys_is_recorded_twice']
    del recorded[only[0]]
    result = evaluate(recorded, where)
    assert result['matrix_passed'] is False
    assert cell in result['matrix']['uncovered'] and result['missing_checks'] == only
    assert cell in result['matrix']['by_item']['duplicates_and_late']['uncovered']


def test_a_not_applicable_cell_is_not_counted_as_uncovered():
    recorded, where = all_passed()
    result = evaluate(recorded, where)
    assert not set(NOT_APPLICABLE) & set(result['matrix']['uncovered'])
    assert result['not_applicable'] == NOT_APPLICABLE


def test_a_check_outside_the_matrix_fails_it():
    recorded, where = all_passed()
    recorded['a_check_nobody_listed'], where['a_check_nobody_listed'] = True, 'company'
    result = evaluate(recorded, where)
    assert result['matrix_passed'] is False and result['unlisted_checks'] == ['a_check_nobody_listed']


def test_a_check_recorded_in_another_scenario_fails_it():
    recorded, where = all_passed()
    name = next(iter(CHECKS['company']))
    where[name] = 'objects'
    result = evaluate(recorded, where)
    assert result['matrix_passed'] is False and result['misplaced_checks'] == [name]


def test_a_failed_check_fails_it_and_does_not_cover_its_cells():
    recorded, where = all_passed()
    recorded['the_same_external_event_under_two_keys_is_recorded_twice'] = False
    result = evaluate(recorded, where)
    assert result['matrix_passed'] is False
    assert result['failed_checks'] == ['the_same_external_event_under_two_keys_is_recorded_twice']
    assert 'duplicates:other_keys_record_events' in result['matrix']['uncovered']


def run_through(book, skip=None):
    """按场景记下全部检查（skip 那条除外），场景都跑完。"""
    scenarios = book.metadata.setdefault('scenarios', {})
    for scenario, named in CHECKS.items():
        scenarios[scenario] = {'status': 'running'}
        for name in named:
            if name != skip:
                book.check(name)
        scenarios[scenario] = {'status': 'completed'}
    return book


def test_the_report_passes_only_with_the_matrix_and_is_never_accepted(tmp_path):
    report = run_through(Book(tmp_path)).save(source_unchanged=True)
    assert report['passed'] is True and report['matrix_passed'] is True
    assert report['world_v02_accepted'] is False and report['released'] is False and report['deployed'] is False
    assert report['check_scenarios']['the_ceo_creates_the_company_over_http_under_world_0_2'] == 'company'
    saved = json.loads((tmp_path / 'report.json').read_text())
    assert saved['matrix']['cells'] == len(CELLS) and saved['required_checks'] == {s: list(n) for s, n in CHECKS.items()}
    text = render_markdown(report)
    assert all(title in text for title in ('## 已执行', '## 跳过', '## 未验证', '## 服务与契约不符'))
    assert f"矩阵共 {len(CELLS)} 格" in text and '未覆盖 0' in text


def test_the_report_fails_with_a_missing_check_a_changed_source_or_an_empty_run(tmp_path):
    report = run_through(Book(tmp_path / 'a'), skip='mission_a_returned_round_is_void_and_nothing_is_written_back').save(
        source_unchanged=True)
    assert report['passed'] is False and report['all_scenarios_completed'] is True
    assert report['matrix']['uncovered'] == ['round:Mission:returned_round_is_void']
    assert 'mission_a_returned_round_is_void_and_nothing_is_written_back' in render_markdown(report)
    assert run_through(Book(tmp_path / 'b')).save(source_unchanged=False)['passed'] is False
    empty = Book(tmp_path / 'c').save()
    assert empty['passed'] is False and empty['matrix']['covered'] == 0


def test_the_walker_has_a_recorder_and_a_wrong_recorder_for_every_row():
    for object_type, row in rows():
        right, wrong = RIGHT[object_type][row['action']], WRONG[object_type][row['action']]
        assert right != wrong, (object_type, row)


def test_the_walker_expects_a_repeat_only_where_the_entered_state_loops_on_the_same_action():
    by_key = {row_key(t, row): (t, row) for t, row in rows()}
    assert repeats(*by_key['Task:unassigned>assigned:world_assign'])
    assert repeats(*by_key['LongTermGoal:draft>draft:world_confirm_long_term_goal:returned'])
    assert repeats(*by_key['Issue:pending_routing>routed:world_route_issue'])
    assert repeats(*by_key['Strategy:effective>effective:world_reconfirm_strategy'])
    assert not repeats(*by_key['Mission:draft>draft:world_mark_core_battle'])  # 只标一次
    assert not repeats(*by_key['Strategy:draft>draft:world_agree_strategy'])  # 同一人对同一内容只记一条
    assert not repeats(*by_key['Task:assigned>in_progress:world_start'])
    assert not repeats(*by_key['Issue:owned>disposed:world_dispose_issue:no_action_close'])


def test_the_matrix_does_not_import_the_implementation():
    tree = ast.parse(Path(matrix.__file__).read_text())
    modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    modules |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= {'__future__', 'json', 'pathlib'}
