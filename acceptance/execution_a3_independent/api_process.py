"""Expose one extra failure/pause point only in the private acceptance server."""
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.append(str(root))
    # This is the existing private, keyed test middleware. Adding a name can
    # only pause or raise; it cannot grant business rights or change a result.
    from acceptance.runtime import server
    server.CHECKPOINTS.update({'after_first_member_activation', 'after_a3_submission_write', 'after_a3_review_write'})
    from acceptance.protocol_a1_independent.api_process import main as start
    start()


if __name__ == '__main__':
    main()
