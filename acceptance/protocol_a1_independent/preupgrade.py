"""One root-invoked command: new one-off old DB plus real old HTTP history.

Requires a private acceptance environment and child-only TEST_ADMIN_DATABASE_URL.
Leaves the database intact and stops only the API child it started.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from . import database, history


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--old-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--private", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.private.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.private.chmod(0o700)
    if any(args.private.iterdir()):
        parser.error("choose an unused private run directory; no existing state will be overwritten")
    environment = args.private / "env.json"
    created = database.create(SimpleNamespace(env_file=args.env_file, source=args.old_source,
        output=args.output / "database", out_env=environment))
    captured = history.capture(SimpleNamespace(env_file=environment, source=args.old_source,
        output=args.output / "pre-upgrade", private=args.private / "pre-upgrade"))
    print(json.dumps({"preupgrade_capture_complete": True, "database": created["created_database"],
        "private_environment": str(environment), **captured, "contract_a1_accepted": False}))


if __name__ == "__main__":
    main()
