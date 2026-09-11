"""Guard against partial or missing independent evidence becoming a PASS."""
from acceptance.method_independent.harness import MethodBook
from acceptance.method_independent.matrix import MATRIX, REQUIRED_GATES


def test_empty_run_cannot_be_accepted(tmp_path):
    report = MethodBook(tmp_path).save()
    assert report['method_api_accepted'] is False
    assert report['checks_passed'] == 0
    assert report['not_complete'] == len(MATRIX)


def test_missing_required_check_or_gate_cannot_be_accepted(tmp_path):
    book = MethodBook(tmp_path)
    book.metadata.update(selected_scenarios=['complete'], scenarios={'complete': {'status': 'completed'}})
    final_case = list(MATRIX)[-1]
    final_check = MATRIX[final_case][-1]
    for case, names in MATRIX.items():
        for name in names:
            if (case, name) != (final_case, final_check):
                book.check(case, name, True)
    book.gates = {name: {'passed': True} for name in REQUIRED_GATES}
    assert book.save()['method_api_accepted'] is False
    book.check(final_case, final_check, True)
    assert book.save()['method_api_accepted'] is True
    book.gates.pop('raw_evidence_storage')
    assert book.save()['method_api_accepted'] is False


def test_scenario_exception_cannot_be_masked_by_completed_checks(tmp_path):
    book = MethodBook(tmp_path)
    book.metadata.update(selected_scenarios=['broken'], scenarios={'broken': {'status': 'failed'}})
    for case, names in MATRIX.items():
        for name in names:
            book.check(case, name, True)
    book.gates = {name: {'passed': True} for name in REQUIRED_GATES}
    assert book.save()['method_api_accepted'] is False


def test_preservation_comparison_catches_changed_file_or_container():
    from acceptance.method_independent.baseline import compare
    before = {'files': {'deferred': 'old'}, 'containers': [{'ID': 'one', 'State': 'running'}]}
    after = {'files': {'deferred': 'new'}, 'containers': [{'ID': 'one', 'State': 'exited'}]}
    result = compare(before, after)
    assert result['files_unchanged'] is False
    assert result['containers_unchanged'] is False
