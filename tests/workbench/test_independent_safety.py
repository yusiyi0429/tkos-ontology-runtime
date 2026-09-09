"""Pure configuration/path guards: no database, network or private state access."""
from __future__ import annotations

from pathlib import Path

from psycopg.conninfo import conninfo_to_dict
import pytest

from acceptance.workbench import independent


SENTINEL = "synthetic-do-not-echo-this-input"
LOCAL_DSN = f"postgresql://synthetic:{SENTINEL}@127.0.0.1:54350/tkos_runtime_acceptance"


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(independent, "ROOT", root)
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture
def environment(isolated_root):
    return {
        "DATABASE_URL": LOCAL_DSN,
        "APP_DATABASE_URL": LOCAL_DSN,
        "MIGRATION_DATABASE_URL": LOCAL_DSN,
        "TKOS_OBJECT_STORE_ENDPOINT": "http://127.0.0.1:54351",
    }


def assert_rejected_without_echo(environment, input_value):
    with pytest.raises(ValueError) as error:
        independent.validate_environment(environment, conninfo_to_dict)
    assert input_value not in str(error.value)
    assert SENTINEL not in str(error.value)


def test_local_acceptance_configuration_is_allowed(environment):
    independent.validate_environment(environment, conninfo_to_dict)


@pytest.mark.parametrize("key", [
    "DATABASE_URL", "APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "TEST_ADMIN_DATABASE_URL",
])
def test_every_database_url_must_be_local(environment, key):
    value = LOCAL_DSN.replace("127.0.0.1", "203.0.113.10")
    environment[key] = value
    assert_rejected_without_echo(environment, value)


@pytest.mark.parametrize("value", [
    LOCAL_DSN + "?hostaddr=203.0.113.10",
    LOCAL_DSN + "?service=synthetic-untrusted-service",
    LOCAL_DSN.replace("/tkos_runtime_acceptance", "/synthetic_other_database"),
])
def test_database_redirection_and_wrong_database_are_rejected(environment, value):
    environment["MIGRATION_DATABASE_URL"] = value
    assert_rejected_without_echo(environment, value)


def test_remote_object_store_is_rejected_without_echo(environment):
    value = "http://203.0.113.10:54351"
    environment["TKOS_OBJECT_STORE_ENDPOINT"] = value
    assert_rejected_without_echo(environment, value)


@pytest.mark.parametrize("run_id", ["../outside", "nested/run", "/absolute-run"])
def test_run_id_cannot_escape_root(isolated_root, run_id):
    with pytest.raises(ValueError):
        independent.reserve_paths(run_id)
    assert list(isolated_root.iterdir()) == []


@pytest.mark.parametrize("existing_parent", [
    Path(".runtime-acceptance"), Path("artifacts/runtime-acceptance"),
])
def test_existing_run_state_is_never_overwritten(isolated_root, existing_parent):
    existing = isolated_root / existing_parent / "already-used"
    existing.mkdir(parents=True)
    marker = existing / "preserve-me"
    marker.write_bytes(b"original synthetic evidence")
    before = {path.relative_to(isolated_root) for path in isolated_root.rglob("*")}
    with pytest.raises(ValueError):
        independent.reserve_paths("already-used")
    assert marker.read_bytes() == b"original synthetic evidence"
    assert {path.relative_to(isolated_root) for path in isolated_root.rglob("*")} == before


def test_first_reservation_creates_only_expected_local_directories(isolated_root):
    private, output = independent.reserve_paths("fresh-run_01")
    assert private == isolated_root / ".runtime-acceptance/fresh-run_01"
    assert output == isolated_root / "artifacts/runtime-acceptance/fresh-run_01"
    assert private.is_dir() and output.is_dir()
    assert {path.relative_to(isolated_root) for path in isolated_root.rglob("*")} == {
        Path(".runtime-acceptance"), Path(".runtime-acceptance/fresh-run_01"),
        Path("artifacts"), Path("artifacts/runtime-acceptance"),
        Path("artifacts/runtime-acceptance/fresh-run_01"),
    }
