"""A3 reporting and source-pinned process control, reusing audited A1 plumbing."""
from pathlib import Path
import json
import sys
import uuid

from acceptance.protocol_a1_independent.support import (
    CaseBook, Harness, now, public_json, wait,
)
from .matrix import MATRIX, CAPABILITIES_EXCLUDED


class A3Harness(Harness):
    def start_api(self, source: Path, *, old=False, updates=None):
        if old:
            return super().start_api(source, old=True, updates=updates)
        ready = self.private / ('api-ready-' + uuid.uuid4().hex + '.json')
        args = [sys.executable, '-I', str(Path(__file__).with_name('api_process.py')),
                '--source', str(source.resolve()), '--ready-file', str(ready),
                '--control-dir', str(self.control), '--key-file', str(self.key_file)]
        process = self.spawn('a3-api', args, updates=updates)
        def started():
            if ready.exists():
                return json.loads(ready.read_text())
            if process.poll() is not None:
                raise AssertionError('A3 API exited before readiness; inspect redacted process log')
            return None
        info = wait(started)
        assert Path(info['application_source']).resolve().is_relative_to(source.resolve())
        return process, info['url'], ready


class A3Book(CaseBook):
    def __init__(self, output):
        super().__init__(output, MATRIX)
        self.metadata = {}
        self.gates = {}

    def save(self, **extra):
        self.metadata.update(extra)
        passed = sum(c['status'] == 'passed' for c in self.cases.values())
        failed = sum(c['status'] == 'failed' for c in self.cases.values())
        all_checks = sum(len(v) for v in self.matrix.values())
        done_checks = sum(sum(x['passed'] for x in c['checks'].values()) for c in self.cases.values())
        gate_names = {'source_unchanged', 'schema_privileges', 'write_capability', 'immutable_history'}
        gate_names.update({'read_authority_rechecks', 'legacy_serialization', 'no_external_effects',
                           'independent_acceptance_authority'})
        matrix_passed = passed == len(MATRIX) == 14 and done_checks == all_checks
        selected = self.metadata.get('selected_scenarios', [])
        scenarios = self.metadata.get('scenarios', {})
        scenarios_complete = bool(selected) and all(
            scenarios.get(name, {}).get('status') == 'completed' for name in selected)
        accepted = matrix_passed and scenarios_complete and not self.metadata.get('run_error') and set(self.gates) >= gate_names and all(
            self.gates[name].get('passed') is True for name in gate_names)
        result = {**self.metadata, 'scope': 'runtime-a3-execution-handover',
                  'started_at': self.started_at, 'updated_at': now(),
                  'matrix_passed': matrix_passed, 'a3_local_matrix_accepted': accepted,
                  'all_selected_scenarios_completed': scenarios_complete,
                  'runtime_accepted': False,
                  'legacy_regression': 'reported separately; never inferred from A3 matrix',
                  'released': False, 'deployed': False,
                  'excluded_capabilities': CAPABILITIES_EXCLUDED,
                  'mandatory_cases': len(MATRIX), 'mandatory_checks': all_checks,
                  'passed': passed, 'failed': failed, 'checks_passed': done_checks,
                  'not_complete': len(MATRIX) - passed - failed,
                  'gates': self.gates, 'required_checks': self.matrix,
                  'cases': list(self.cases.values())}
        public_json(self.output / 'report.json', result)
        return result
