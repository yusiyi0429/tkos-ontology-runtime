"""Provision fresh synthetic identities and a starting fact, never a closed loop."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import psycopg

from acceptance.runtime.harness import private_json
from memory_service_runtime.governed.bootstrap import seed_scope


def create_fixture(run_id=None):
    run_id = run_id or f"runtime-{uuid.uuid4().hex[:12]}"
    env = json.loads((ROOT / ".runtime-acceptance/env.json").read_text())
    with psycopg.connect(env["MIGRATION_DATABASE_URL"]) as conn:
        fixture = seed_scope(conn, f"runtime-acceptance-{run_id}", f"runtime-acceptance-company-{run_id}")
    path = ROOT / ".runtime-acceptance" / run_id / "fixture.json"
    private_json(path, fixture)
    path.parent.chmod(0o700)
    return run_id, path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    run_id, path = create_fixture(args.run_id)
    print(json.dumps({"run_id": run_id, "fixture_file": str(path)}))
