"""The isolated acceptance stack grants the application role exactly what the release grants.

infra.GRANTS takes every table's privileges from the release grant step
(deploy/offline-release/db_admin.py, expected_privileges) rather than a private copy.
The v0.2 SQL oracle deliberately keeps its own UPDATE allowlist as an independent
check, so that list must agree with the release. No database is needed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from acceptance.runtime import infra, sql_oracle


RELEASE_DB_ADMIN = Path(__file__).resolve().parents[1] / "deploy" / "offline-release" / "db_admin.py"


def release_db_admin():
    spec = importlib.util.spec_from_file_location("release_db_admin", RELEASE_DB_ADMIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_grant_step_applies_the_release_classification(monkeypatch) -> None:
    sent = []
    monkeypatch.setattr(infra, "child_python", lambda code, payload, **_: sent.append((code, payload)) or {})
    infra.grant_application_role("owner-url", "app-url")
    [(code, payload)] = sent
    assert code == infra.GRANTS and "expected_privileges" in code
    assert Path(payload["release_db_admin"]) == RELEASE_DB_ADMIN
    assert payload["app"] == infra.APP


def test_sql_oracle_update_allowlist_matches_the_release() -> None:
    assert sql_oracle.MUTABLE_GOV_TABLES == release_db_admin().MUTABLE_GOV_TABLES
