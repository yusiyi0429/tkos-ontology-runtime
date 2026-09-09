"""Independent synthetic workbench acceptance on prepared local infrastructure.

This entry starts only its own API/receiver processes. It does not operate
Docker, migrate databases, install dependencies, or contact deployed services.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import traceback
from urllib.parse import urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[2]
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
DATABASE = "tkos_runtime_acceptance"
REQUIRED_DATABASE_URLS = {"DATABASE_URL", "APP_DATABASE_URL", "MIGRATION_DATABASE_URL"}
REQUIRED_PATHS = [
    "/v1/object-types", "/v1/domains", "/v1/objects",
    "/v1/objects/{object_id}/revisions", "/v1/objects/{object_id}/relations",
    "/v1/objects/{object_id}/action-receipts", "/v1/objects/{object_id}/responsibility",
]
CLAIM = (
    "Independent workbench read-API acceptance using fresh synthetic scopes and "
    "real local HTTP/database/object-store checks. This is not full Runtime "
    "acceptance, Clark integration, a real employee business pilot, release, "
    "or production deployment."
)


def validate_run_id(value: str) -> str:
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise ValueError("run-id must be 1-80 ASCII letters, digits, underscores or hyphens, starting with a letter or digit")
    return value


def _loopback(value: str) -> bool:
    # Numeric addresses avoid DNS, libpq host lists and Unix-domain sockets.
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def validate_environment(environment: dict, parse_dsn) -> None:
    """Inspect connection configuration in memory; never return or log secrets."""
    if not isinstance(environment, dict) or not REQUIRED_DATABASE_URLS <= environment.keys():
        raise ValueError("prepared isolated database configuration is incomplete")
    if any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")):
        raise ValueError("unset ambient PostgreSQL address/service overrides before acceptance")
    database_keys = {key for key in environment if key.endswith("DATABASE_URL")}
    for key in database_keys:
        try:
            value = environment[key]
            if not isinstance(value, str) or not value:
                raise ValueError
            options = parse_dsn(value)
            if options.get("service") or not _loopback(options.get("host", "")):
                raise ValueError
            if "hostaddr" in options and not _loopback(options["hostaddr"]):
                raise ValueError
            if options.get("dbname") != DATABASE:
                raise ValueError
            port = options.get("port", "")
            if not isinstance(port, str) or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
                raise ValueError
        except Exception:
            # libpq parser errors may include its input: never propagate them.
            raise ValueError("every database URL must explicitly name the isolated acceptance database and a numeric loopback host/port") from None
    try:
        endpoint = urlsplit(environment["TKOS_OBJECT_STORE_ENDPOINT"])
        if (endpoint.scheme not in {"http", "https"} or not _loopback(endpoint.hostname or "")
                or endpoint.port is None or not 1 <= endpoint.port <= 65535
                or endpoint.username is not None or endpoint.password is not None
                or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
            raise ValueError
    except Exception:
        raise ValueError("object-store endpoint must be an explicit numeric loopback HTTP(S) host/port without credentials or query") from None


def reserve_paths(run_id: str) -> tuple[Path, Path]:
    """Reserve a new run without reusing output or private bootstrap state."""
    run_id = validate_run_id(run_id)
    private = ROOT / ".runtime-acceptance" / run_id
    output = ROOT / "artifacts" / "runtime-acceptance" / run_id
    for path in (private, output):
        if path.exists() or path.is_symlink():
            raise ValueError("run-id already has private state or evidence; choose a new run-id")
        if not path.parent.resolve().is_relative_to(ROOT):
            raise ValueError("acceptance state and evidence must remain inside this checkout")
        for parent in path.parents:
            if parent == ROOT:
                break
            if parent.is_symlink():
                raise ValueError("acceptance state and evidence directories may not be symlinks")
    private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    private.mkdir(mode=0o700, exist_ok=False)
    output.mkdir(exist_ok=False)
    return private, output


def source_manifest() -> dict[str, str]:
    files = set()
    for folder in ("src", "acceptance/runtime", "acceptance/workbench"):
        files.update((ROOT / folder).rglob("*.py"))
    files.update(path for path in (ROOT / "pyproject.toml", ROOT / "uv.lock") if path.is_file())
    manifest = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(files) if path.is_file()}
    for path in (Path(__file__).resolve(), Path(__file__).with_name("independent_gates.py").resolve()):
        manifest["acceptance/workbench/" + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def failure_metadata(exc: BaseException) -> dict:
    # Do not save exception messages, locals or source lines: they may contain a
    # DSN supplied to a driver. Frame locations are enough to reproduce a gate.
    frames = []
    for frame in traceback.extract_tb(exc.__traceback__):
        path = Path(frame.filename)
        filename = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
        frames.append({"file": filename, "line": frame.lineno, "function": frame.name})
    return {"error_type": type(exc).__name__, "frames": frames}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="workbench-independent-" + uuid.uuid4().hex[:12])
    args = parser.parse_args(argv)
    harness = scenario = None
    before = after = None
    output = None
    failed = None
    passed = False
    cleanup_errors = []
    try:
        validate_run_id(args.run_id)
        # Record the checkout before importing its application/bootstrap code.
        before = source_manifest()
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "src"))
        import httpx
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from acceptance.runtime import infra
        from acceptance.runtime.client import utc_now
        from acceptance.runtime.harness import Harness
        from acceptance.runtime.run import Scenario
        from acceptance.runtime.seed import create_fixture
        from acceptance.runtime.sql_oracle import assert_application_role
        from acceptance.workbench.independent_gates import run_gates

        class IndependentHarness(Harness):
            @contextmanager
            def group(self, name):
                result = {"name": name, "started_at": utc_now(), "status": "running"}
                self.groups.append(result)
                print(f"RUN {name}", flush=True)
                try:
                    yield result
                except BaseException as exc:
                    result.update(status="failed", **failure_metadata(exc))
                    print(f"FAIL {name}: {type(exc).__name__}", flush=True)
                    raise
                else:
                    result["status"] = "passed"
                    print(f"PASS {name}", flush=True)
                finally:
                    result["finished_at"] = utc_now()
                    self.save_report(runtime_accepted=False, workbench_read_accepted=False, claim=CLAIM)

        environment = infra.load_environment()
        validate_environment(environment, conninfo_to_dict)
        _, output = reserve_paths(args.run_id)
        harness = IndependentHarness(run_id=args.run_id)
        if harness.env != environment or infra.load_environment() != environment:
            raise ValueError("isolated configuration changed before seeding; retry with a fresh run-id")
        _, fixture_file = create_fixture(args.run_id)
        if infra.load_environment() != environment:
            raise ValueError("isolated configuration changed while seeding; acceptance cannot continue")
        fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
        scenario = Scenario(harness, fixture)
        with harness.group("workbench_independent_00_source_and_application_role") as group:
            with psycopg.connect(harness.env["APP_DATABASE_URL"]) as conn:
                group["database_authority"] = assert_application_role(conn)
            response = httpx.get(scenario.api_url + "/openapi.json", trust_env=False, timeout=15)
            assert response.status_code == 200
            spec = response.json()
            assert all(path in spec["paths"] and "get" in spec["paths"][path] for path in REQUIRED_PATHS)
            write_json(output / "openapi.json", spec)
            group.update(required_paths=REQUIRED_PATHS, source_file_count=len(before),
                         infrastructure="prepared local acceptance PostgreSQL and object store")
        results = run_gates(scenario)
        assert len(harness.groups) == 11, "Expected source/application-role plus ten independent business groups"
        write_json(output / "workbench-results.json", results)
        passed = True
    except BaseException as exc:
        failed = failure_metadata(exc)
        print(f"Independent acceptance failed: {type(exc).__name__}", flush=True)
    finally:
        if scenario:
            for client in scenario.clients.values():
                try:
                    client.close()
                except BaseException as exc:
                    cleanup_errors.append(failure_metadata(exc))
        if harness:
            try:
                harness.stop_all()
            except BaseException as exc:
                cleanup_errors.append(failure_metadata(exc))
        if before is not None:
            try:
                after = source_manifest()
            except BaseException as exc:
                cleanup_errors.append(failure_metadata(exc))
        stable = before is not None and after is not None and before == after
        accepted = passed and stable and failed is None and not cleanup_errors
        if output is not None:
            changed = sorted(key for key in set(before or {}) | set(after or {})
                             if (before or {}).get(key) != (after or {}).get(key))
            write_json(output / "source-sha256.json", {"before": before, "after": after,
                                                       "unchanged": stable, "changed_files": changed})
            if harness:
                for group in harness.groups:
                    if group["name"] == "workbench_independent_00_source_and_application_role":
                        group["source_unchanged_after_run"] = stable
                        if not stable:
                            group.update(status="failed", error_type="SourceChangedDuringAcceptance")
                harness.complete = accepted
                report = harness.save_report(
                    environment="prepared local PostgreSQL + versioned object store; own HTTP API/receiver, Worker stopped",
                    runtime_accepted=False, workbench_read_accepted=accepted,
                    real_employee_pilot_accepted=False, synthetic_data_only=True,
                    implementer="Kimi Code", independent_verifier="Codex",
                    expected_groups=11, source_unchanged=stable, failure=failed,
                    cleanup_errors=cleanup_errors, claim=CLAIM,
                )
            else:
                report = output / "report.json"
                write_json(report, {"test_run_id": args.run_id, "status": "incomplete_or_failed",
                                    "runtime_accepted": False, "workbench_read_accepted": False,
                                    "clark_integration_accepted": False, "real_employee_pilot_accepted": False,
                                    "released": False, "production_deployed": False,
                                    "synthetic_data_only": True, "failure": failed, "claim": CLAIM})
            print(f"Independent evidence: {report}", flush=True)
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
