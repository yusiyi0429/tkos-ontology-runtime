"""world 0.1 验收的记账：只收冻结矩阵里的检查；缺检查、缺门槛、场景没跑完或出错、源码没钉在提交上，
都不写 world_api_accepted: true。"""
from __future__ import annotations

from acceptance.protocol_a1_independent.support import CaseBook, now, public_json

from .matrix import COVERAGE, EXCLUDED, MATRIX, NOT_APPLICABLE, REQUIRED_GATES, SOURCES, TITLES

GROUP = {name: group for group, names in MATRIX.items() for name in names}


class WorldBook(CaseBook):
    def __init__(self, output):
        super().__init__(output, MATRIX)
        self.metadata = {}
        self.gates = {}

    def record(self, name: str, passed: bool = True) -> None:
        """记一条检查；不在矩阵里的名字直接拒收，不成立的记为失败并抛出。"""
        if name not in GROUP:
            raise ValueError('independent check is not in the frozen required matrix')
        self.check(GROUP[name], name, passed)

    def save(self, **extra) -> dict:
        self.metadata.update(extra)
        passed = sum(case['status'] == 'passed' for case in self.cases.values())
        failed = sum(case['status'] == 'failed' for case in self.cases.values())
        total_checks = sum(map(len, MATRIX.values()))
        done_checks = sum(sum(check['passed'] for check in case['checks'].values()) for case in self.cases.values())
        selected = self.metadata.get('selected_scenarios', [])
        scenarios = self.metadata.get('scenarios', {})
        scenarios_complete = bool(selected) and all(
            scenarios.get(name, {}).get('status') == 'completed' for name in selected)
        matrix_passed = passed == len(MATRIX) and done_checks == total_checks
        gates_passed = all(self.gates.get(name, {}).get('passed') is True for name in REQUIRED_GATES)
        accepted = (matrix_passed and scenarios_complete and gates_passed and not self.metadata.get('run_error')
                    and bool(self.metadata.get('source_commit')))
        result = {
            **self.metadata, 'scope': 'tkos.world/0.1 APIs', 'sources': SOURCES,
            'started_at': self.started_at, 'updated_at': now(),
            'matrix_passed': matrix_passed, 'all_selected_scenarios_completed': scenarios_complete,
            'world_api_accepted': accepted, 'released': False, 'deployed': False,
            'mandatory_groups': len(MATRIX), 'mandatory_checks': total_checks,
            'passed': passed, 'failed': failed, 'not_complete': len(MATRIX) - passed - failed,
            'checks_passed': done_checks, 'gates': self.gates, 'required_gates': sorted(REQUIRED_GATES),
            'excluded_capabilities': EXCLUDED, 'titles': TITLES, 'required_checks': MATRIX,
            'action_coverage': COVERAGE, 'not_applicable': NOT_APPLICABLE, 'cases': list(self.cases.values()),
        }
        public_json(self.output / 'report.json', result)
        return result
