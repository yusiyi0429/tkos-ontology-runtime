"""world 0.1 验收判定（票 #27）：缺检查、缺门槛、场景失败、没钉提交都不能写 world_api_accepted: true；
冻结矩阵按票面清单分组，13 个动作逐格对照五类拒绝；摘要与冻结检查点只从通过的报告生成。"""
import ast
import json
from pathlib import Path

import pytest

from acceptance.world_v01.book import WorldBook
from acceptance.world_v01.database import BASE_COMMIT
from acceptance.world_v01.matrix import (ACTIONS, COVERAGE, KINDS, MATRIX, NOT_APPLICABLE, REQUIRED_GATES, SOURCES,
                                         TITLES)
from acceptance.world_v01.summarize import summarize

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'c' * 40


def every_check():
    return [name for names in MATRIX.values() for name in names]


def completed(book, *, skip=None):
    """跑完全部场景、记下全部检查（skip 那条除外）与全部门槛，源码钉在一个提交上。"""
    book.metadata.update(selected_scenarios=['all'], scenarios={'all': {'status': 'completed'}},
                         source_commit=COMMIT)
    for name in every_check():
        if name != skip:
            book.record(name)
    book.gates = {name: {'passed': True} for name in REQUIRED_GATES}
    return book


def test_an_empty_run_is_not_accepted(tmp_path):
    report = WorldBook(tmp_path).save()
    assert report['world_api_accepted'] is False
    assert report['checks_passed'] == 0 and report['not_complete'] == len(MATRIX)
    assert json.loads((tmp_path / 'report.json').read_text())['world_api_accepted'] is False


def test_every_check_and_every_gate_is_needed(tmp_path):
    last = every_check()[-1]
    book = completed(WorldBook(tmp_path), skip=last)
    assert book.save()['world_api_accepted'] is False
    book.record(last)
    assert book.save()['world_api_accepted'] is True
    book.gates.pop(sorted(REQUIRED_GATES)[0])
    assert book.save()['world_api_accepted'] is False


def test_a_failed_scenario_is_not_masked_by_passed_checks(tmp_path):
    book = completed(WorldBook(tmp_path))
    book.metadata['scenarios']['all'] = {'status': 'failed'}
    assert book.save()['world_api_accepted'] is False


def test_a_run_error_is_not_masked_by_passed_checks(tmp_path):
    book = completed(WorldBook(tmp_path))
    assert book.save(run_error={'exception_type': 'AssertionError'})['world_api_accepted'] is False


def test_a_run_from_the_working_tree_is_not_accepted(tmp_path):
    book = completed(WorldBook(tmp_path))
    book.metadata['source_commit'] = None
    report = book.save()
    assert report['matrix_passed'] is True and report['world_api_accepted'] is False


def test_a_failed_check_is_recorded_as_failed_and_stops_the_run(tmp_path):
    book = WorldBook(tmp_path)
    name = every_check()[0]
    with pytest.raises(AssertionError):
        book.record(name, False)
    report = book.save()
    assert report['failed'] == 1 and report['world_api_accepted'] is False


def test_a_check_outside_the_frozen_matrix_cannot_be_recorded(tmp_path):
    with pytest.raises(ValueError):
        WorldBook(tmp_path).record('a_check_nobody_froze')


def test_a_check_cannot_be_recorded_twice(tmp_path):
    book = WorldBook(tmp_path)
    book.record(every_check()[0])
    with pytest.raises(ValueError):
        book.record(every_check()[0])


def test_the_groups_are_the_ticket_checklist():
    assert list(MATRIX) == list(TITLES)
    assert list(TITLES.values()) == [
        '迁移升级与幂等', '控制面安装四步', '13 个动作的通过与拒绝', '门按目标类型判权', '退回与撤回',
        '确认写回与生效指针', '快照唯一性', '引用钉定', '四个读投影', '上下文包落表', '读权限', 'MCP 端到端']
    assert all(MATRIX.values())


def test_each_check_belongs_to_exactly_one_group():
    names = every_check()
    assert len(names) == len(set(names))


def test_the_actions_are_the_supported_world_actions():
    support = json.loads((ROOT / 'docs/runtime-world-support-0.1.json').read_text())
    assert list(ACTIONS) == support['actions']


def test_the_matrix_names_the_baseline_the_database_tool_builds_from():
    assert SOURCES['base_commit'] == BASE_COMMIT


def test_each_action_and_kind_names_frozen_checks_or_says_why_not():
    frozen = set(every_check())
    assert set(COVERAGE) == set(ACTIONS) and set(NOT_APPLICABLE) <= set(ACTIONS)
    for action in ACTIONS:
        for kind in KINDS:
            named = COVERAGE[action].get(kind, [])
            why = NOT_APPLICABLE.get(action, {}).get(kind)
            assert bool(named) != bool(why), (action, kind)
            assert set(named) <= frozen, (action, kind, set(named) - frozen)
        assert COVERAGE[action]['passes'], action
        assert set(COVERAGE[action]) | set(NOT_APPLICABLE.get(action, {})) == set(KINDS), action


def test_the_matrix_does_not_import_the_implementation():
    tree = ast.parse((ROOT / 'acceptance/world_v01/matrix.py').read_text())
    imported = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert imported == []


def accepted_report(tmp_path):
    output = tmp_path / 'run'
    book = completed(WorldBook(output))
    manifest = {'memory_service_runtime/governed/world_v01.py': 'a' * 64, 'tkos_world_mcp/server.py': 'b' * 64}
    book.save(frozen_files={'src/memory_service_runtime/governed/world_v01.py': 'a' * 64,
                            'docs/contracts/tkos-world-0.1.md': 'd' * 64})
    (output / 'source-before.json').write_text(json.dumps(manifest))
    (output / 'source-after.json').write_text(json.dumps(manifest))
    return output


def test_summarize_writes_the_summary_and_the_freeze_checkpoint(tmp_path):
    output = accepted_report(tmp_path)
    result = summarize(output / 'report.json', tmp_path / 'summary.json', tmp_path / 'checkpoint.md')
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary == result
    assert summary['world_api_accepted'] is True and summary['source_commit'] == COMMIT
    assert summary['passed_checks'] == len(every_check()) and summary['groups'] == len(MATRIX)
    assert summary['gates'] == {name: True for name in sorted(REQUIRED_GATES)}
    assert summary['released'] is False and summary['deployed'] is False
    checkpoint = (tmp_path / 'checkpoint.md').read_text()
    assert COMMIT in checkpoint and summary['source_manifest_sha256'] in checkpoint
    assert '| `src/memory_service_runtime/governed/world_v01.py` | `' + 'a' * 64 + '` |' in checkpoint
    assert '| `docs/contracts/tkos-world-0.1.md` | `' + 'd' * 64 + '` |' in checkpoint


def test_summarize_refuses_a_report_that_is_not_accepted(tmp_path):
    output = accepted_report(tmp_path)
    report = json.loads((output / 'report.json').read_text())
    report['world_api_accepted'] = False
    (output / 'report.json').write_text(json.dumps(report))
    with pytest.raises(AssertionError):
        summarize(output / 'report.json', tmp_path / 'summary.json', tmp_path / 'checkpoint.md')
    assert not (tmp_path / 'summary.json').exists() and not (tmp_path / 'checkpoint.md').exists()


def test_summarize_refuses_a_report_whose_source_changed_during_the_run(tmp_path):
    output = accepted_report(tmp_path)
    (output / 'source-after.json').write_text(json.dumps({'changed.py': 'e' * 64}))
    with pytest.raises(AssertionError):
        summarize(output / 'report.json', tmp_path / 'summary.json', tmp_path / 'checkpoint.md')


def test_summarize_refuses_a_report_whose_frozen_matrix_differs(tmp_path):
    output = accepted_report(tmp_path)
    report = json.loads((output / 'report.json').read_text())
    report['required_checks'] = {**report['required_checks'], 'WORLD-99': ['an_extra_check']}
    (output / 'report.json').write_text(json.dumps(report))
    with pytest.raises(AssertionError):
        summarize(output / 'report.json', tmp_path / 'summary.json', tmp_path / 'checkpoint.md')
