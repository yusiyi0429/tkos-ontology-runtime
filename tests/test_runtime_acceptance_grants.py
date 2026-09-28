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


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _FakeServer:
    """Records the harness's GRANT/REVOKE statements and answers the app-role check from them."""

    def __init__(self, tables, extra=None):
        self.tables, self.extra, self.statements, self.granted = tables, extra or {}, [], {}

    def connect(self, url):
        server = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, query, params=None):
                text = query if isinstance(query, str) else query.as_string(None)
                if text.startswith("SELECT tablename FROM"):
                    return _Result([(t,) for t in server.tables])
                if text.startswith("SELECT tablename, has_table_privilege"):
                    verbs = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE")
                    return _Result([(t, *(v in server.granted.get(t, set()) | server.extra.get(t, set())
                                          for v in verbs)) for t in server.tables])
                server.statements.append(text)
                if text.startswith("REVOKE ALL ON TABLE public."):
                    server.granted[text.split('"')[1]] = set()
                elif text.startswith("GRANT ") and " ON TABLE public." in text:
                    table = text.split('"')[1]
                    server.granted[table] = set(text[len("GRANT "):text.index(" ON TABLE")].split(", "))
                return _Result([])

        return Conn()


def _environment():
    from types import SimpleNamespace
    return SimpleNamespace(values={"APP_DATABASE_URL": "postgresql://app_role@127.0.0.1/tkos_a1_x",
                                   "MIGRATION_DATABASE_URL": "postgresql://owner_role@127.0.0.1/tkos_a1_x"})


TABLES = ["gov_method_agent_bindings", "gov_objects", "gov_object_revisions", "runtime_tasks",
          "runtime_worker_heartbeats", "schema_migrations", "wm_issue_chains"]


def test_independent_harness_grants_follow_the_release(monkeypatch) -> None:
    from acceptance.execution_a3_independent import database

    server = _FakeServer(TABLES)
    monkeypatch.setattr(database.psycopg, "connect", server.connect)
    database.grants(_environment())
    release = release_db_admin()
    assert {t: server.granted[t] for t in TABLES} == {t: release.expected_privileges(t) for t in TABLES}
    assert "DELETE" not in server.granted["runtime_tasks"] | server.granted["runtime_worker_heartbeats"]
    for table in TABLES:
        revoke = server.statements.index(f'REVOKE ALL ON TABLE public."{table}" FROM "app_role"')
        grant = next(i for i, s in enumerate(server.statements) if s.startswith("GRANT ") and f'"{table}"' in s)
        assert revoke < grant


def test_independent_harness_grants_reject_privileges_the_release_does_not_give(monkeypatch) -> None:
    import pytest
    from acceptance.execution_a3_independent import database

    server = _FakeServer(TABLES, extra={"runtime_tasks": {"DELETE"}})
    monkeypatch.setattr(database.psycopg, "connect", server.connect)
    with pytest.raises(AssertionError, match="runtime_tasks"):
        database.grants(_environment())
