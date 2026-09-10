"""Offline checks of the real maintenance CLI adapter, not Runtime acceptance.

All CLI results and database entry points are mocked. The real ControlAdapter,
Harness and Environment operate only on synthetic files in a temporary directory.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from . import control_adapter, support
from .control_adapter import ControlAdapter, NotReady
from .support import Harness, private_json, public_json


MODULE = "memory_service_runtime.governed.control"


def run() -> dict:
    if not __debug__:
        raise RuntimeError("CLI adapter selfchecks require Python assertions enabled")
    checks = []
    observations = []
    test = TestCase()
    with TemporaryDirectory(prefix="tkos-a1-cli-selfcheck-") as temp, ExitStack() as stack:
        guards = [stack.enter_context(patch(name, side_effect=AssertionError(
            "offline selfcheck attempted database or process access"))) for name in (
                "psycopg.connect", "psycopg.Connection.connect",
                "psycopg.AsyncConnection.connect", "subprocess.Popen")]
        run_mock = stack.enter_context(patch("subprocess.run", side_effect=AssertionError(
            "CLI execution requires an explicitly supplied offline result")))
        root = Path(temp)
        source = root / "source"
        module_path = source.joinpath(*MODULE.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True)
        module_path.write_text("", encoding="utf-8")
        values = {
            "APP_DATABASE_URL": "postgresql://offline_app@127.0.0.1:5555/tkos_a1_cli_selfcheck",
            "MIGRATION_DATABASE_URL": "postgresql://offline_owner@127.0.0.1:5555/tkos_a1_cli_selfcheck",
            "TKOS_OBJECT_STORE_ENDPOINT": "http://127.0.0.1:5556",
            "MEMORY_TENANT": "offline-tenant",
            "RUNTIME_TEST_SETTING": "offline-only",
        }
        env_file = root / "private" / "synthetic-env.json"
        private_json(env_file, values)
        harness = Harness(env_file, root / "output", root / "private")
        adapter = ControlAdapter(harness, source, MODULE)
        success = {"ok": True, "command": "backfill-legacy", "result": {
            "backfilled": 0, "checks": ["profile", "bindings"], "note": "离线完整文档"}}
        failure = {"ok": False, "command": "freeze-writes", "error": {
            "code": "PROTOCOL_UNKNOWN", "message": "synthetic protocol is unknown"}}
        pretty_success = json.dumps(success, ensure_ascii=False, indent=2) + "\n"
        pretty_failure = json.dumps(failure, indent=2) + "\n"

        def call(label: str, *, stdout: str = "", stderr: str = "", code: int = 0,
                 expected_exit: int | tuple[int, ...] = 0,
                 expected_error_code: str | None = None, reject: str | None = None,
                 arguments: list[str] | None = None) -> tuple[dict | None, object]:
            run_mock.reset_mock()
            run_mock.side_effect = None
            run_mock.return_value = subprocess.CompletedProcess([], code, stdout, stderr)
            command_args = arguments if arguments is not None else ["backfill-legacy", "--scope-id", "offline-scope"]
            record = None
            try:
                if reject is None:
                    record = adapter.cli(label, command_args, expected_exit=expected_exit,
                                         expected_error_code=expected_error_code)
                else:
                    with test.assertRaisesRegex(AssertionError, reject):
                        adapter.cli(label, command_args, expected_exit=expected_exit,
                                    expected_error_code=expected_error_code)
                run_mock.assert_called_once()
                artifact = json.loads((harness.output / f"control-{label}.json").read_text())
                assert artifact["exit_code"] == code and artifact["case_id"] == label
                observations.append({"case": label, "rejected": reject is not None,
                                     "exit_code": code, "artifact_recorded": True})
                return record, run_mock.call_args
            finally:
                run_mock.side_effect = AssertionError("unexpected CLI execution outside a supplied result")

        result, _ = call("pretty-stdout", stdout="\n" + pretty_success + "  ")
        assert result["response"] == success and result["stderr"] == ""
        checks.append("pretty_stdout_success_preserves_entire_document")

        result, _ = call("pretty-stderr", stderr=pretty_failure, code=2,
                         expected_exit=(2,), expected_error_code="PROTOCOL_UNKNOWN")
        assert result["response"] == failure and result["stdout"] == ""
        checks.append("pretty_stderr_exit_two_preserves_exact_error")

        for label, stderr in (("mixed-log", "diagnostic\n"), ("mixed-json", pretty_failure)):
            call(label, stdout=pretty_success, stderr=stderr,
                 reject="must emit one unambiguous JSON document")
        checks.append("mixed_stdout_stderr_rejected")

        call("decorated-stdout", stdout="CLI started\n" + pretty_success,
             reject="must emit one unambiguous JSON document")
        call("decorated-stderr", stderr="CLI failed\n" + pretty_failure, code=2,
             expected_exit=2, reject="must emit one unambiguous JSON document")
        checks.append("log_prefix_before_final_json_rejected")

        for index, body in enumerate(("{broken", "[]", "null", "", pretty_success + pretty_success)):
            call(f"invalid-json-{index}", stdout=body,
                 reject="must emit one unambiguous JSON document")
        checks.append("invalid_nonobject_empty_or_multiple_json_documents_rejected")

        for index, (body, code) in enumerate((
                ({"ok": False}, 0), ({"ok": True}, 2), ({"ok": 1}, 0),
                ({"ok": 0}, 2), ({}, 0))):
            call(f"ok-exit-{index}", stdout=json.dumps(body) if code == 0 else "",
                 stderr=json.dumps(body) if code else "", code=code, expected_exit=code,
                 reject="result/exit disagreement")
        checks.append("ok_exit_conflicts_missing_and_nonboolean_ok_rejected")

        call("success-wrong-channel", stderr=pretty_success, reject="response channel mismatch")
        call("failure-wrong-channel", stdout=pretty_failure, code=2, expected_exit=2,
             reject="response channel mismatch")
        checks.append("success_and_failure_wrong_channel_rejected")

        call("exit-mismatch", stderr=pretty_failure, code=2, expected_exit=0,
             reject="maintenance CLI exit mismatch")
        checks.append("unexpected_exit_rejected_even_with_structured_error")

        for index, error in enumerate(({"code": "OTHER_CODE"}, {}, "PROTOCOL_UNKNOWN")):
            call(f"wrong-error-{index}", stderr=json.dumps({"ok": False, "error": error}),
                 code=2, expected_exit=2, expected_error_code="PROTOCOL_UNKNOWN",
                 reject="maintenance error code mismatch")
        checks.append("wrong_missing_or_unstructured_error_code_rejected")

        literal_args = ["set-registry", "--reason", "literal $() `text`\n中文", "--scope-id", "offline-scope"]
        _, captured = call("owner-env", stdout=pretty_success, arguments=literal_args)
        argv = captured.args[0]
        child = captured.kwargs["env"]
        assert argv[:4] == [sys.executable, "-I", "-c", control_adapter.CLI_RUNNER]
        assert argv[4:6] == [str(source), MODULE] and len(argv) == 7
        assert child["DATABASE_URL"] == values["MIGRATION_DATABASE_URL"]
        assert child["MIGRATION_DATABASE_URL"] == values["MIGRATION_DATABASE_URL"]
        assert values["APP_DATABASE_URL"] not in child.values() and "APP_DATABASE_URL" not in child
        assert captured.kwargs.get("shell", False) is False
        assert captured.kwargs["text"] is True and captured.kwargs["capture_output"] is True
        assert 0 < captured.kwargs["timeout"] <= 60
        argfile = Path(argv[6])
        assert argfile.resolve().is_relative_to(harness.private.resolve())
        assert json.loads(argfile.read_text()) == literal_args
        assert stat.S_IMODE(argfile.stat().st_mode) == 0o600
        assert stat.S_IMODE(harness.private.stat().st_mode) == 0o700
        checks.append("control_uses_owner_env_isolated_argv_and_private_literal_arguments")

        with patch.dict("os.environ", {
                "MIGRATION_DATABASE_URL": values["MIGRATION_DATABASE_URL"],
                "DATABASE_URL": values["MIGRATION_DATABASE_URL"],
                "PGUSER": "offline_owner", "PGOPTIONS": "-c role=offline_owner",
                "UNRELATED_SECRET": "synthetic-do-not-inherit"}):
            api_child = harness.env.child()
        assert api_child["DATABASE_URL"] == values["APP_DATABASE_URL"]
        assert "MIGRATION_DATABASE_URL" not in api_child and "APP_DATABASE_URL" not in api_child
        assert values["MIGRATION_DATABASE_URL"] not in api_child.values()
        assert not {"PGUSER", "PGOPTIONS", "UNRELATED_SECRET"} & api_child.keys()
        assert api_child["RUNTIME_TEST_SETTING"] == "offline-only"
        assert api_child["PYTHONDONTWRITEBYTECODE"] == "1"
        checks.append("ordinary_api_child_uses_app_role_and_excludes_ambient_owner_credentials")

        run_mock.reset_mock()
        private_before = set(harness.private.rglob("*"))
        for chosen, label, args, error in (
                (ControlAdapter(harness, source, None), "no-module", [], NotReady),
                (ControlAdapter(harness, source, "os"), "wrong-module", [], ValueError),
                (ControlAdapter(harness, source, "memory_service_runtime.governed.absent"), "missing-source", [], NotReady),
                (adapter, "../unsafe-case", [], ValueError),
                (adapter, "invalid-args", [42], ValueError)):
            with test.assertRaises(error):
                chosen.cli(label, args, expected_exit=0)
        run_mock.assert_not_called()
        assert set(harness.private.rglob("*")) == private_before
        checks.append("unfrozen_missing_unsafe_interface_rejected_before_subprocess_or_argument_files")

        for guard in guards:
            guard.assert_not_called()
        assert harness.processes == []
    return {
        "gate": "independent_cli_adapter_offline_selfcheck_only",
        "passed": len(checks), "failed": 0, "checks": checks,
        "mocked_cli_results": len(observations), "observations": observations,
        "source_sha256": {Path(module.__file__).name: hashlib.sha256(
            Path(module.__file__).read_bytes()).hexdigest() for module in (control_adapter, support)},
        "database_access": False, "subprocess_executed": False,
        "contract_a1_accepted": False, "runtime_accepted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    if args.output:
        public_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
