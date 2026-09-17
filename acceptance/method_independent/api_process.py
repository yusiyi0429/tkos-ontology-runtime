"""Keyed, private failure points; cannot alter authorization or grant success."""
from pathlib import Path
import sys


def main():
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    from acceptance.runtime import server
    server.CHECKPOINTS.update({
        'after_first_member_activation', 'after_a3_submission_write', 'after_a3_review_write',
        'after_method_strategy_write', 'after_method_candidate_write', 'after_method_problem_transfer',
        'after_method_pair_strategy_write', 'after_method_problem_transfer_link',
        'workspace_before_receipt',
    })
    from acceptance.protocol_a1_independent.api_process import main as start
    start()


if __name__ == '__main__':
    main()
