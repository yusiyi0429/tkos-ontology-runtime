"""Run the unchanged A2 business matrix against the additional A3 schema.

Only the explicit schema inventory/UPDATE allowlist is extended for A3's two
state tables. Every A2 case, expected result and authority/transaction probe
remains the existing independently frozen implementation.
"""
from acceptance.composition_a2_independent import run as a2

from .run import NEW_TABLES, MUTABLE_TABLES


def main():
    a2.NEW_TABLES = a2.NEW_TABLES | NEW_TABLES
    a2.MUTABLE_TABLES = a2.MUTABLE_TABLES | MUTABLE_TABLES
    a2.main()


if __name__ == "__main__":
    main()
