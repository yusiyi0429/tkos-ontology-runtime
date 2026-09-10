"""Offline checks of the independent report/connection safety rules only."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from .matrix import REQUIRED
from .support import CaseBook, Environment, public_json


def api_identity_checks() -> list[str]:
    from psycopg.rows import dict_row
    from .api_process import observe_database_identity

    checks = []
    base = {"current_user": "synthetic_app", "session_user": "synthetic_app", "rolsuper": False,
            "rolbypassrls": False, "is_db_owner": False, "runtime_write_capability": None}

    def observe(row, *, old):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = deepcopy(row)
        # Nested mock substitutes only the DB transport. It never calls a
        # production role/permission helper or modifies the returned row.
        with patch("psycopg.connect", return_value=connection) as connect:
            try:
                return observe_database_identity("synthetic-api-dsn-not-a-connection", require_no_capability=old)
            finally:
                connect.assert_called_once_with("synthetic-api-dsn-not-a-connection", row_factory=dict_row)
                statements = [call.args[0] for call in connection.execute.call_args_list]
                assert len(statements) == 2 and statements[0] == "SET TRANSACTION READ ONLY"
                assert statements[1].lstrip().startswith("SELECT current_user")
                assert "pg_roles" in statements[1] and "pg_database" in statements[1]
                assert "set_config" not in statements[1].lower()

    for label, old, capability in (("old_no_capability", True, None), ("old_empty_capability", True, ""),
                                   ("current_observes_capability_without_setting_it", False, "tkos-runtime-a1")):
        row = {**base, "runtime_write_capability": capability}
        assert observe(row, old=old) == row
        checks.append("api_identity_" + label)
    negatives = [("superuser", {**base, "rolsuper": True}),
                 ("bypassrls", {**base, "rolbypassrls": True}),
                 ("database_owner", {**base, "is_db_owner": True}),
                 ("changed_session_role", {**base, "session_user": "synthetic_other"}),
                 ("old_capability", {**base, "runtime_write_capability": "tkos-runtime-a1"}),
                 ("missing_role_row", None)]
    for label, row in negatives:
        try:
            observe(row, old=True)
        except ValueError:
            checks.append("api_identity_reject_" + label)
        else:
            raise AssertionError("API identity guard did not reject " + label)
    return checks


def revision_page_checks() -> list[str]:
    from .read_projection_cases import _metadata, _validate_revision_page

    expected = {"protocol_id": "tkos.contract-a", "contract_version": "tkos.contract-a/0.1",
                "binding_version": 1, "record_origin": "synthetic",
                "method_profile_ref": {"profile_id": "synthetic-profile", "revision": "fixed-v1", "canonical_hash": "a" * 64}}
    metadata = {**expected, "registration_status": "registered", "interpretation_status": "contract_a_metadata_read_only"}
    page = {"items": [{"object_id": "one-object", "revision_id": "one-revision", "protocol": deepcopy(metadata)}],
            "next_cursor": None, "protocol": deepcopy(metadata)}

    def validate(document, object_id):
        assert object_id == "one-object"
        _metadata(document, expected, path="protocol", interpretation="contract_a_metadata_read_only")

    _validate_revision_page(page, "one-object", validate)
    checks = ["revision_page_explicit_envelope_valid"]
    missing = deepcopy(page)
    del missing["protocol"]  # Correct row metadata cannot rescue a missing envelope.
    wrong_envelope = deepcopy(page)
    wrong_envelope["protocol"]["protocol_id"] = "tkos.legacy-governed"
    wrong_profile = deepcopy(page)
    wrong_profile["protocol"]["method_profile_ref"]["canonical_hash"] = "b" * 64
    wrong_owner = deepcopy(page)
    wrong_owner["items"][0]["object_id"] = "another-object"
    for label, document in (("missing_envelope", missing), ("wrong_envelope", wrong_envelope),
                            ("wrong_envelope_profile", wrong_profile), ("wrong_row_object", wrong_owner)):
        try:
            _validate_revision_page(document, "one-object", validate)
        except AssertionError:
            checks.append("revision_page_reject_" + label)
        else:
            raise AssertionError("revision page oracle accepted " + label)
    return checks


def responsibility_checks() -> list[str]:
    from .read_projection_cases import _validate_unsupported_responsibility

    expected = {"error": {"code": "PROTOCOL_NOT_SUPPORTED", "message": "The legacy interpretation is not supported."}}
    _validate_unsupported_responsibility(409, expected)
    checks = ["responsibility_exact_unsupported_error_valid"]
    wrong_code = deepcopy(expected)
    wrong_code["error"]["code"] = "FORBIDDEN"
    leaked = {**deepcopy(expected), "dri": {"assignment_id": "synthetic"}}
    nested = deepcopy(expected)
    nested["error"]["dri"] = {"assignment_id": "synthetic"}
    for label, status, document in (("status_200", 200, expected), ("status_403", 403, expected),
                                    ("wrong_code", 409, wrong_code), ("business_fields", 409, leaked),
                                    ("nested_business_fields", 409, nested), ("missing_error", 409, {})):
        try:
            _validate_unsupported_responsibility(status, document)
        except AssertionError:
            checks.append("responsibility_reject_" + label)
        else:
            raise AssertionError("responsibility oracle accepted " + label)
    return checks


def run() -> dict:
    checks = []
    with TemporaryDirectory() as temp, patch("psycopg.connect", side_effect=AssertionError("offline check touched DB")):
        root = Path(temp)
        book = CaseBook(root / "book", REQUIRED)
        report = book.save(contract_a1_accepted=True, runtime_accepted=True)
        assert report["contract_a1_accepted"] is False and report["runtime_accepted"] is False
        checks.append("extra_metadata_cannot_override_acceptance")
        book.check("A1-01", REQUIRED["A1-01"][0], True)
        assert book.cases["A1-01"]["status"] == "incomplete"
        checks.append("one_subcheck_does_not_pass_group")
        try:
            book.check("A1-01", REQUIRED["A1-01"][0], True)
        except ValueError:
            pass
        else:
            raise AssertionError("a duplicate check overwrote prior evidence")
        checks.append("duplicate_subcheck_cannot_overwrite")
        book.failure("A1-01", "an earlier runner exception")
        for name in REQUIRED["A1-01"][1:]:
            book.check("A1-01", name, True)
        assert book.cases["A1-01"]["status"] == "failed"
        assert book.save()["failed"] == 1
        assert book.save()["contract_a1_accepted"] is False
        checks.append("failure_stays_failed_after_all_other_successes")
        book.failure("A1-02", "")
        for name in REQUIRED["A1-02"]:
            book.check("A1-02", name, True)
        assert book.cases["A1-02"]["status"] == "failed"
        checks.append("empty_error_failure_is_sticky")
        base = {"APP_DATABASE_URL": "postgresql://app:synthetic@127.0.0.1:5555/tkos_runtime_acceptance",
                "MIGRATION_DATABASE_URL": "postgresql://owner:synthetic@127.0.0.1:5555/tkos_runtime_acceptance",
                "TKOS_OBJECT_STORE_ENDPOINT": "http://127.0.0.1:5556"}
        path = root / "env.json"
        path.write_text(json.dumps(base))
        Environment(path)
        checks.append("synthetic_isolated_environment_valid")
        changes = [
            ("different_db", "MIGRATION_DATABASE_URL", "postgresql://owner:synthetic@127.0.0.1:5555/tkos_a1_other"),
            ("different_port", "MIGRATION_DATABASE_URL", "postgresql://owner:synthetic@127.0.0.1:9999/tkos_runtime_acceptance"),
            ("remote_db", "APP_DATABASE_URL", "postgresql://app:synthetic@example.com:5555/tkos_runtime_acceptance"),
            ("database_prefix_confusion", "APP_DATABASE_URL", "postgresql://app:synthetic@127.0.0.1:5555/tkos_runtime_acceptance_prod"),
            ("same_role", "MIGRATION_DATABASE_URL", "postgresql://app:synthetic@127.0.0.1:5555/tkos_runtime_acceptance"),
            ("remote_s3", "TKOS_OBJECT_STORE_ENDPOINT", "https://example.com"),
            ("userinfo_s3", "TKOS_OBJECT_STORE_ENDPOINT", "http://example@127.0.0.1:5556"),
        ]
        for label, key, value in changes:
            changed = deepcopy(base)
            changed[key] = value
            path.write_text(json.dumps(changed))
            try:
                Environment(path)
            except ValueError:
                checks.append("reject_" + label)
            else:
                raise AssertionError("environment guard failed: " + label)
        checks.extend(api_identity_checks())
        checks.extend(revision_page_checks())
        checks.extend(responsibility_checks())
    return {"gate": "independent_harness_safety_only", "passed": len(checks), "failed": 0,
            "checks": checks, "database_access": False, "contract_a1_accepted": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    if args.output:
        public_json(args.output, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
