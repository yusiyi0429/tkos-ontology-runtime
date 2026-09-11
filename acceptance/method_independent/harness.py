"""Independent Method reporting and source-pinned real HTTP process control."""
from pathlib import Path
import json
import sys
import uuid

from acceptance.protocol_a1_independent.support import CaseBook, Harness, now, public_json, wait

from .matrix import EXCLUDED, MATRIX, REQUIRED_GATES, SOURCES


class MethodHarness(Harness):
    def start_api(self, source: Path, *, old=False, updates=None):
        if old:
            return super().start_api(source, old=True, updates=updates)
        ready = self.private / ('api-ready-' + uuid.uuid4().hex + '.json')
        args = [sys.executable, '-I', str(Path(__file__).with_name('api_process.py')),
                '--source', str(source.resolve()), '--ready-file', str(ready),
                '--control-dir', str(self.control), '--key-file', str(self.key_file)]
        process = self.spawn('method-api', args, updates=updates)

        def started():
            if ready.exists():
                return json.loads(ready.read_text())
            if process.poll() is not None:
                raise AssertionError('Method API exited before readiness; inspect redacted process log')
            return None

        info = wait(started)
        assert Path(info['application_source']).resolve().is_relative_to(source.resolve())
        return process, info['url'], ready


class MethodBook(CaseBook):
    def __init__(self, output):
        super().__init__(output, MATRIX)
        self.metadata = {}
        self.gates = {}

    def save(self, **extra):
        self.metadata.update(extra)
        passed = sum(case['status'] == 'passed' for case in self.cases.values())
        failed = sum(case['status'] == 'failed' for case in self.cases.values())
        total_checks = sum(len(checks) for checks in MATRIX.values())
        done_checks = sum(sum(check['passed'] for check in case['checks'].values())
                          for case in self.cases.values())
        selected = self.metadata.get('selected_scenarios', [])
        scenarios = self.metadata.get('scenarios', {})
        scenarios_complete = bool(selected) and all(
            scenarios.get(name, {}).get('status') == 'completed' for name in selected)
        matrix_passed = passed == len(MATRIX) and done_checks == total_checks
        gates_passed = all(self.gates.get(name, {}).get('passed') is True for name in REQUIRED_GATES)
        accepted = matrix_passed and scenarios_complete and gates_passed and not self.metadata.get('run_error')
        result = {
            **self.metadata, 'scope': 'runtime-method-m1a-m1b', 'sources': SOURCES,
            'started_at': self.started_at, 'updated_at': now(),
            'matrix_passed': matrix_passed, 'method_api_accepted': accepted,
            'all_selected_scenarios_completed': scenarios_complete,
            'runtime_acceptance_scope': 'tkos.method/0.1 APIs', 'released': False, 'deployed': False,
            'excluded_capabilities': EXCLUDED, 'mandatory_cases': len(MATRIX),
            'mandatory_checks': total_checks, 'passed': passed, 'failed': failed,
            'checks_passed': done_checks, 'not_complete': len(MATRIX) - passed - failed,
            'gates': self.gates, 'required_gates': sorted(REQUIRED_GATES),
            'required_checks': MATRIX, 'cases': list(self.cases.values()),
        }
        public_json(self.output / 'report.json', result)
        return result
