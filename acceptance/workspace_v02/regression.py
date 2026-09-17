"""Python/workbench/build regression with private, role-specific env."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from psycopg.conninfo import conninfo_to_dict

from acceptance.protocol_a1_independent.support import Environment, public_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    env = Environment(args.env_file.resolve())
    database = conninfo_to_dict(env.values["APP_DATABASE_URL"])["dbname"]
    if not database.startswith("tkos_a1_method_") or args.output.exists():
        raise ValueError("Use an isolated Method database and a fresh output directory")
    root = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True, mode=0o700)
    jobs = [
        ("python", False, [sys.executable, "-m", "pytest", "tests",
                           "--ignore=tests/test_migrations.py", "-q",
                           "--junitxml=" + str(args.output.resolve() / "python.xml")]),
        ("workbench", False, ["node", "--test", *[str(p) for p in sorted((root / "tests/workbench-ui").glob("*.test.mjs"))]]),
        ("build", False, ["uv", "build", "--offline"]),
    ]
    results = {}
    for name, owner, command in jobs:
        child = env.child(owner=owner, updates={"PYTHONPATH": str(root / "src")})
        run = subprocess.run(command, cwd=root, env=child, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = args.output / (name + ".log")
        log.write_text(env.redact(run.stdout))
        log.chmod(0o600)
        results[name] = {"exit_code": run.returncode}
        if name == "python" and (args.output / "python.xml").exists():
            tests = ET.parse(args.output / "python.xml").findall(".//testcase")
            results[name].update(total=len(tests), skipped=sum(t.find("skipped") is not None for t in tests),
                                 failures=sum(t.find("failure") is not None or t.find("error") is not None
                                              for t in tests))
        print(json.dumps({"suite": name, **results[name]}), flush=True)
    public_json(args.output / "summary.json", {
        "results": results, "passed": all(item["exit_code"] == 0 for item in results.values()),
        "excluded": {"tests/test_migrations.py": "requires a separate CREATEDB administrator; "
                                                  "0026/0028 applied and verified by bootstrap"},
    })
    if any(item["exit_code"] for item in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
