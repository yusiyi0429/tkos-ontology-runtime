"""Pure boundary checks; these tests never open a database connection."""
import copy
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location("local_reader", Path(__file__).with_name("provision_local_reader.py"))
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


@pytest.fixture
def candidate(tmp_path):
    folder = tmp_path / ".runtime-acceptance" / "convergence-unit"
    folder.mkdir(parents=True)
    primary = {"tenant_id": "test-tenant", "organization_id": "test-company"}
    dsn = "postgresql://local_user:test-only@127.0.0.1:55432/tkos_convergence_unit"
    state = {"run_id": folder.name, "private_directory": str(folder), "output_directory": str(folder),
        "primary_scope": primary, "scopes": [primary], "legacy_scopes": [primary],
        "scope_requires_selection": False, "selected_admin_url": dsn, "selected_source": "memory_plus_aw_history",
        "MIGRATION_DATABASE_URL": dsn, "APP_DATABASE_URL": dsn}
    report = {"run_id": folder.name, "status": "passed", "local_isolated_restore": True,
        "production_mutations": False, "production_services_restarted": False,
        "traffic_switched": False, "gov_identity_or_authority_imported": False, "governed_rows": 0,
        "preservation_merge_enabled": True, "preservation_merge": {
            "union_after_insert": {"all_53_union_preserved": True},
            "union_after_replay": {"all_53_union_preserved": True}},
        "union_after_migrations": {"all_53_union_preserved": True}}
    def save():
        for name, data in (("state", state), ("report", report)):
            file = folder / (name + ".json")
            file.write_text(json.dumps(data)); file.chmod(0o600)
    save()
    return tmp_path, folder / "state.json", state, report, save


def test_valid_state_never_connects(candidate):
    root, file, *_ = candidate
    with patch.object(reader.psycopg, "connect", side_effect=AssertionError("must not connect")):
        path, state, dsns = reader.load_state(file, root=root)
    assert path == file and "reader_token" not in state
    assert dsns["APP_DATABASE_URL"]["hostaddr"] == "127.0.0.1"


@pytest.mark.parametrize("field,value,reason", [
    ("reader_token", "test-only", "READER_ALREADY_PRESENT"),
    ("reader_principal_id", "id", "READER_ALREADY_PRESENT"),
    ("scope_id", "id", "READER_ALREADY_PRESENT"),
    ("local_only", False, "LOCAL_RESTORE_REQUIRED"),
    ("run_id", "convergence-other", "RUN_ID_MISMATCH"),
    ("scope_requires_selection", True, "SINGLE_PRIMARY_SCOPE_REQUIRED"),
    ("primary_scope", None, "SINGLE_PRIMARY_SCOPE_REQUIRED"),
    ("scopes", [], "SINGLE_PRIMARY_SCOPE_REQUIRED"),
    ("legacy_scopes", [{"tenant_id": "other", "organization_id": "other"}], "SINGLE_PRIMARY_SCOPE_REQUIRED"),
    ("output_directory", "/tmp", "REPORT_DIRECTORY_MISMATCH"),
    ("selected_source", "memory", "PRESERVATION_MERGE_REQUIRED"),
])
def test_state_boundaries(candidate, field, value, reason):
    root, file, state, _, save = candidate
    state[field] = value; save()
    with pytest.raises(reader.Refused, match=reason): reader.load_state(file, root=root)


@pytest.mark.parametrize("field,value", [("status", "running"), ("status", "failed"),
    ("run_id", "convergence-other"), ("governed_rows", 1), ("production_mutations", True),
    ("gov_identity_or_authority_imported", True), ("local_isolated_restore", False), ("traffic_switched", True),
    ("preservation_merge_enabled", False), ("preservation_merge_enabled", None)])
def test_report_boundaries(candidate, field, value):
    root, file, _, report, save = candidate
    report[field] = value; save()
    with pytest.raises(reader.Refused): reader.load_state(file, root=root)


def test_merge_requires_all_three_union_gates(candidate):
    root, file, _, report, save = candidate
    report.update(preservation_merge_enabled=True, preservation_merge={
        "union_after_insert": {"all_53_union_preserved": True},
        "union_after_replay": {"all_53_union_preserved": True}})
    del report["union_after_migrations"]
    save()
    with pytest.raises(reader.Refused, match="PRESERVATION_MERGE_NOT_PASSED"): reader.load_state(file, root=root)
    report["union_after_migrations"] = {"all_53_union_preserved": True}; save()
    reader.load_state(file, root=root)


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@remote.invalid:5432/tkos_convergence_unit",
    "postgresql://u:p@127.0.0.1:5432/production",
    "host=127.0.0.1,example.org port=5432 user=u password=p dbname=tkos_convergence_unit",
    "host=127.0.0.1 hostaddr=203.0.113.1 port=5432 user=u password=p dbname=tkos_convergence_unit",
    "host=/tmp port=5432 user=u password=p dbname=tkos_convergence_unit",
    "host=127.0.0.1 port=5432 user=u password=p dbname=tkos_convergence_unit service=production",
    "host=127.0.0.1 port=5432 user=u password=p dbname=tkos_convergence_unit options='-c role=other'",
    "host=localhost user=u password=p dbname=tkos_convergence_unit",
    "host=localhost port=5432 user=u dbname=tkos_convergence_unit",
])
def test_dsn_boundaries(dsn):
    with pytest.raises(reader.Refused): reader.local_dsn(dsn)


def test_dsns_must_point_to_same_restore(candidate):
    root, file, state, _, save = candidate
    state["APP_DATABASE_URL"] = state["APP_DATABASE_URL"].replace("tkos_convergence_unit", "tkos_convergence_other")
    save()
    with pytest.raises(reader.Refused, match="DATABASE_TARGET_MISMATCH"): reader.load_state(file, root=root)


def test_private_path_mode_and_symlinks(candidate):
    root, file, *_ = candidate
    file.chmod(0o640)
    with pytest.raises(reader.Refused, match="PRIVATE_FILE_REQUIRED"): reader.load_state(file, root=root)
    file.chmod(0o600)
    original = file.with_name("saved.json"); file.rename(original); file.symlink_to(original)
    with pytest.raises(reader.Refused, match="LOCAL_STATE_PATH_REQUIRED"): reader.load_state(file, root=root)
    with pytest.raises(reader.Refused, match="LOCAL_STATE_PATH_REQUIRED"): reader.load_state(original, root=root)


def test_directory_lock_and_atomic_private_save(candidate):
    _, file, state, *_ = candidate
    with reader.state_lock(file):
        updated = copy.deepcopy(state); updated["reader_token"] = "test-only"
        reader.atomic_save(file, updated)
        assert reader.private_json(file) == updated
    assert list(file.parent.glob(".reader-state-*")) == []


def test_only_six_identity_rows_and_two_read_actions():
    empty = {name: 0 for name in reader.IDENTITY_TABLES | {"gov_objects", "gov_action_receipts", "gov_context_snapshots"}}
    reader.validate_counts(empty)
    for name in empty:
        with pytest.raises(reader.Refused, match="GOVERNED_ROWS_ALREADY_PRESENT"):
            reader.validate_counts({**empty, name: 1})
    expected = {name: int(name in reader.IDENTITY_TABLES) for name in empty}
    reader.validate_counts(expected, provisioned=True)
    with pytest.raises(reader.Refused, match="UNEXPECTED_PROVISIONING_ROWS"):
        reader.validate_counts({**expected, "gov_objects": 1}, provisioned=True)
    assert len(reader.IDENTITY_TABLES) == 6
    assert reader.POLICY["action_roles"] == {"read": ["AGENT"], "read_legacy_context": ["AGENT"]}
