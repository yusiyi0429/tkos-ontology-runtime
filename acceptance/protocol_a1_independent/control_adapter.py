"""Real maintenance-CLI adapter and independent scoped SQL observations.

CLI module/arguments are explicitly supplied after interface review. The adapter
never calls implementation validators as an oracle and never guesses success
from a return object. Missing interfaces are NOT_READY, not a passing case.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

from .support import Harness, private_json, public_json, digest


LEGACY_PROTOCOL = "tkos.legacy-governed"
LEGACY_CONTRACT = "tkos.governed/v0.2"
A_PROTOCOL = "tkos.contract-a"
A_CONTRACT = "tkos.contract-a/0.1"
CONTROL_TABLES = (
    "gov_method_profile_revisions", "gov_protocol_policies",
    "gov_protocol_support_registry", "gov_protocol_control_events",
)
BINDING_TABLE = "gov_object_protocol_bindings"


class NotReady(RuntimeError):
    pass


CLI_RUNNER = r'''
import importlib.util, json, pathlib, runpy, sys
source = pathlib.Path(sys.argv[1]).resolve()
module_name = sys.argv[2]
arguments = json.loads(pathlib.Path(sys.argv[3]).read_text())
if len(sys.argv) > 4:
    import os, time, psycopg
    probe = json.loads(pathlib.Path(sys.argv[4]).read_text())
    original_execute = psycopg.Connection.execute
    consumed = False
    def execute_with_identity_pause(self, query, *args, **kwargs):
        global consumed
        cursor = original_execute(self, query, *args, **kwargs)
        normalized = ' '.join(query.split()) if isinstance(query, str) else ''
        if not consumed and normalized.startswith('SELECT scope_id, canonical_hash FROM gov_method_profile_revisions'):
            consumed = True
            path = pathlib.Path(probe['paused'])
            temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'phase': 'after_real_global_profile_identity_select',
                           'backend_pid': self.info.backend_pid,
                           'application_name': self.info.parameter_status('application_name'),
                           'sql_result_modified': False, 'token': probe['token']}, stream)
            os.replace(temporary, path)
            deadline = time.monotonic() + 30
            release = pathlib.Path(probe['release'])
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('independent Profile identity pause expired')
                time.sleep(0.025)
            assert json.loads(release.read_text()) == {'token': probe['token']}
        return cursor
    psycopg.Connection.execute = execute_with_identity_pause
sys.path.insert(0, str(source))
spec = importlib.util.find_spec(module_name)
if spec is None or spec.origin is None or not pathlib.Path(spec.origin).resolve().is_relative_to(source):
    raise SystemExit("controlled CLI source is not available")
sys.argv = [module_name, *arguments]
runpy.run_module(module_name, run_name="__main__")
'''


class ControlAdapter:
    def __init__(self, harness: Harness, source: Path, cli_module: str | None = None):
        self.h, self.source, self.cli_module = harness, source, cli_module

    def cli(self, case_id: str, arguments: list[str], *, expected_exit: int | tuple[int, ...],
            expected_error_code: str | None = None, identity_checkpoint: dict | None = None) -> dict:
        if not self.cli_module:
            raise NotReady("maintenance CLI interface has not been frozen")
        if not re.fullmatch(r"memory_service_runtime\.governed\.[a-z][a-z0-9_]*", self.cli_module):
            raise ValueError("only the reviewed governed maintenance CLI may run")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", case_id):
            raise ValueError("case ID must be a safe artifact name")
        if not isinstance(arguments, list) or any(not isinstance(arg, str) for arg in arguments):
            raise ValueError("CLI arguments must be a string list")
        module_path = self.source.joinpath(*self.cli_module.split(".")).with_suffix(".py")
        if not module_path.is_file():
            raise NotReady("reviewed maintenance CLI module is not implemented")
        argfile = self.h.private / f"cli-{case_id}-{uuid.uuid4().hex}.json"
        private_json(argfile, arguments)
        owner_env = self.h.env.child(owner=True)
        owner_env["MIGRATION_DATABASE_URL"] = self.h.env.values["MIGRATION_DATABASE_URL"]
        argv = [sys.executable, "-I", "-c", CLI_RUNNER, str(self.source), self.cli_module, str(argfile)]
        if identity_checkpoint is not None:
            from psycopg.conninfo import make_conninfo
            if not case_id.startswith(("concurrent-profile-", "cross-scope-concurrent-profile-")):
                raise ValueError("identity pause is restricted to independent Profile concurrency cases")
            if set(identity_checkpoint) != {"paused", "release", "token", "application_name"}:
                raise ValueError("invalid Profile identity checkpoint")
            for name in ("paused", "release"):
                path = Path(identity_checkpoint[name]).resolve()
                if not path.is_relative_to(self.h.private.resolve()) or path.exists():
                    raise ValueError("Profile concurrency checkpoints require fresh private paths")
            checkpoint_file = self.h.private / (case_id + "-instrumentation.json")
            private_json(checkpoint_file, identity_checkpoint)
            argv.append(str(checkpoint_file))
            owner_env["MIGRATION_DATABASE_URL"] = make_conninfo(owner_env["MIGRATION_DATABASE_URL"],
                application_name=identity_checkpoint["application_name"])
        completed = subprocess.run(argv, env=owner_env, text=True, capture_output=True, timeout=40)
        out, err = self.h.env.redact(completed.stdout), self.h.env.redact(completed.stderr)
        response = None
        streams = [(name, value.strip()) for name, value in (("stdout", out), ("stderr", err)) if value.strip()]
        # This CLI contract emits one entire JSON document on exactly one
        # channel. Reject mixed/log-decorated output instead of guessing which
        # fragment is authoritative.
        if len(streams) == 1:
            try:
                response = json.loads(streams[0][1])
            except json.JSONDecodeError:
                pass
        record = {"case_id": case_id, "module": self.cli_module, "exit_code": completed.returncode,
                  "stdout": out, "stderr": err, "response": response,
                  "identity_pause_instrumentation": identity_checkpoint is not None}
        public_json(self.h.output / f"control-{case_id}.json", record)
        allowed_exits = (expected_exit,) if isinstance(expected_exit, int) else expected_exit
        assert completed.returncode in allowed_exits, f"maintenance CLI exit mismatch: {case_id}"
        assert isinstance(response, dict), "maintenance CLI must emit one unambiguous JSON document"
        assert response.get("ok") is (completed.returncode == 0), "maintenance CLI result/exit disagreement"
        assert streams[0][0] == ("stdout" if completed.returncode == 0 else "stderr"), "maintenance CLI response channel mismatch"
        if expected_error_code is not None:
            assert isinstance(response, dict), "maintenance rejection must be structured"
            error = response.get("error", {})
            assert isinstance(error, dict) and error.get("code") == expected_error_code, "maintenance error code mismatch"
        return record

    def binding(self, fixture: dict, object_id: str) -> dict:
        rows = self.h.sql(fixture, """SELECT * FROM gov_object_protocol_bindings
            WHERE scope_id=%s AND object_id=%s ORDER BY binding_version, binding_id""",
            (fixture["scope_id"], object_id))
        assert len(rows) == 1, "A1 initial object binding must be unique and cannot silently rebind"
        row = rows[0]
        assert row["binding_version"] == 1
        profiles = self.h.sql(fixture, """SELECT canonical_hash FROM gov_method_profile_revisions
            WHERE scope_id=%s AND profile_id=%s AND revision=%s""",
            (fixture["scope_id"], row["profile_id"], row["profile_revision"]))
        assert len(profiles) == 1 and profiles[0]["canonical_hash"] == row["profile_canonical_hash"]
        return row

    def installed_profile(self, fixture: dict, profile_id: str, revision: str) -> dict:
        rows = self.h.sql(fixture, """SELECT * FROM gov_method_profile_revisions
            WHERE scope_id=%s AND profile_id=%s AND revision=%s""",
            (fixture["scope_id"], profile_id, revision))
        assert len(rows) == 1, "profile identity did not resolve to exactly one immutable record"
        return rows[0]

    def full_registration_coverage(self, fixture: dict) -> dict:
        rows = self.h.sql(fixture, """SELECT o.object_type, count(*) AS object_count,
            count(b.binding_id) AS binding_count
            FROM gov_objects o LEFT JOIN gov_object_protocol_bindings b
              ON b.scope_id=o.scope_id AND b.object_id=o.object_id
            WHERE o.scope_id=%s GROUP BY o.object_type ORDER BY o.object_type""", (fixture["scope_id"],))
        assert rows and all(row["object_count"] == row["binding_count"] for row in rows), "objects lack bindings"
        # Separate group-per-object check prevents two bindings on one object
        # and no binding on another from cancelling in aggregate totals.
        duplicates = self.h.sql(fixture, """SELECT o.object_id, count(b.binding_id) AS n
            FROM gov_objects o LEFT JOIN gov_object_protocol_bindings b
              ON b.scope_id=o.scope_id AND b.object_id=o.object_id
            WHERE o.scope_id=%s GROUP BY o.object_id HAVING count(b.binding_id)<>1""", (fixture["scope_id"],))
        assert duplicates == [], "initial bindings are not one per object"
        return {"types": rows, "sha256": digest(rows), "unique_per_object": True}


class FrozenControlContract:
    """Reviewed command templates, not arbitrary shell command strings."""

    def __init__(self, path: Path | None):
        self.content = json.loads(path.read_text()) if path else {}

    @property
    def module(self) -> str | None:
        return self.content.get("cli_module")

    def invoke(self, adapter: ControlAdapter, operation: str, case_id: str,
               values: dict, *, rejection: str | None = None) -> dict:
        item = self.content.get("operations", {}).get(operation)
        if not isinstance(item, dict) or not isinstance(item.get("args"), list):
            raise NotReady(f"controlled operation has not been frozen: {operation}")
        if rejection is None:
            expectation = item.get("success")
        else:
            expectation = self.content.get("rejections", {}).get(rejection)
        if not isinstance(expectation, dict) or type(expectation.get("exit")) is not int:
            raise NotReady(f"controlled exit/error contract has not been frozen: {operation}/{rejection}")
        if rejection and not isinstance(expectation.get("code"), str):
            raise NotReady("a controlled rejection requires an exact error code")
        try:
            arguments = [argument.format_map({key: str(value) for key, value in values.items()})
                         for argument in item["args"]]
        except (KeyError, AttributeError, ValueError) as exc:
            raise NotReady(f"controlled argument template is incomplete: {operation}") from exc
        return adapter.cli(case_id, arguments, expected_exit=expectation["exit"],
                           expected_error_code=expectation.get("code"))
