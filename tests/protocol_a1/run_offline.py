"""Standalone entry point for the offline A1 protocol test suite.

Runs pytest against tests/protocol_a1 only, with --confcutdir so the
database-requiring tests/conftest.py is never imported.  No DATABASE_URL is
consulted and no database connection is attempted.

Usage (from the repository root):
    .venv/bin/python tests/protocol_a1/run_offline.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    return subprocess.call(
        [sys.executable, "-m", "pytest", "tests/protocol_a1",
         "--confcutdir=tests/protocol_a1", "-p", "no:cacheprovider", "-q"],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
