"""Standalone entry point for the offline A2 foundation test suite.

Runs pytest against tests/a2 only, with --confcutdir so the
database-requiring tests/conftest.py is never imported.  No DATABASE_URL is
consulted and no database connection is attempted.

Usage (from the repository root):
    .venv/bin/python tests/a2/run_offline.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    return subprocess.call(
        [sys.executable, "-m", "pytest", "tests/a2",
         "--confcutdir=tests/a2", "-p", "no:cacheprovider", "-q"],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
