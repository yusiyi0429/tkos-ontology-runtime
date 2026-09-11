"""Run unchanged A2/A3 business cases with the complete post-0021 table catalog.

Only catalog expectations grow for later migrations. No database privileges,
SQL results, business assertions, action schemas or reported checks are changed.
"""
import sys
from acceptance.composition_a2_independent import storage

METHOD_MUTABLE = {'gov_method_state', 'gov_method_strategy_heads', 'gov_method_runs'}
A3_MUTABLE = {'gov_execution_state', 'gov_a3_work_item_state'}
METHOD_CONTROL = {'gov_method_agent_bindings'}
METHOD_TABLES = METHOD_MUTABLE | METHOD_CONTROL | {
    'gov_method_reviews', 'gov_method_impacts', 'gov_method_run_attempts', 'gov_method_run_members'}
A3_TABLES = A3_MUTABLE | {'gov_execution_authorities', 'gov_acceptance_appointments', 'gov_work_receipts',
                         'gov_a3_delivery_acceptances', 'gov_a3_outcome_assessments'}


def main():
    suite = sys.argv.pop(1)
    if suite == 'a2':
        from acceptance.composition_a2_independent import run
    elif suite == 'a3':
        from acceptance.execution_a3_independent import run
    else:
        raise ValueError('only the existing A2 and A3 suites are supported')
    storage.CONTROL = storage.CONTROL | METHOD_CONTROL
    run.NEW_TABLES = run.NEW_TABLES | METHOD_TABLES | A3_TABLES
    run.MUTABLE_TABLES = run.MUTABLE_TABLES | METHOD_MUTABLE | A3_MUTABLE
    run.main()


if __name__ == '__main__':
    main()
