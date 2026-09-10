"""Run controlled migrate/seed/worker entrypoints from explicitly selected source.

Used only after root provides a private, isolated environment. No production
business successes are seeded. Source provenance is always checked.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("migrate", "seed", "worker-once"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--namespace")
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source))
    result = {}
    if args.operation == "migrate":
        module = importlib.import_module("memory_service_app.migrate")
        assert Path(module.__file__).resolve().is_relative_to(source)
        result = {"applied": module.migrate(os.environ["DATABASE_URL"]), "source": str(source)}
    elif args.operation == "seed":
        if not args.out or not args.namespace or not args.namespace.startswith("runtime-acceptance-"):
            parser.error("seed requires private output and explicit acceptance namespace")
        module = importlib.import_module("memory_service_runtime.governed.bootstrap")
        assert Path(module.__file__).resolve().is_relative_to(source)
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            fixture = module.seed_scope(conn, args.namespace, args.namespace + "-company")
        args.out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        args.out.parent.chmod(0o700)
        fd = os.open(args.out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(fixture, stream, indent=2)
        result = {"fixture_file": str(args.out), "source": str(source),
                  "business_transitions_seeded": False, "starting_fact_seeded": True}
    else:
        module = importlib.import_module("memory_service_runtime.worker")
        assert Path(module.__file__).resolve().is_relative_to(source)
        config = importlib.import_module("memory_service_runtime.config").RuntimeConfig.from_env()
        did_work = module.RuntimeWorker(config).run_once()
        result = {"worker_once_finished": True, "did_work": did_work, "source": str(source)}
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
