"""Object-store and database connection settings read from one place, with one meaning."""
from __future__ import annotations

import pytest

from memory_service_app import migrate
from memory_service_runtime import object_store
from memory_service_runtime.config import RuntimeConfigError
from memory_service_runtime.governed import db, evidence
from memory_service_runtime.governed.errors import GovernedError

STORE = {
    "TKOS_OBJECT_STORE_ENDPOINT": "https://objects.test",
    "TKOS_OBJECT_STORE_ACCESS_KEY": "synthetic-access",
    "TKOS_OBJECT_STORE_SECRET_KEY": "synthetic-secret",
}


@pytest.mark.parametrize("raw, expected", [(" off", False), ("FALSE", False), ("0", False), ("on", True), ("true", True)])
def test_verify_tls_has_one_meaning(raw, expected):
    assert object_store.settings({**STORE, "TKOS_OBJECT_STORE_VERIFY_TLS": raw})["verify_tls"] is expected


def test_unknown_verify_tls_is_a_configuration_error_not_a_silent_default():
    with pytest.raises(RuntimeConfigError) as error:
        object_store.settings({**STORE, "TKOS_OBJECT_STORE_VERIFY_TLS": "maybe"})
    assert "synthetic" not in str(error.value)


def test_evidence_storage_rejects_unknown_verify_tls(monkeypatch):
    for name, value in {**STORE, "TKOS_OBJECT_STORE_VERIFY_TLS": "maybe"}.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(GovernedError) as error:
        with evidence.object_client():
            pass
    assert error.value.code == "EVIDENCE_UNAVAILABLE"


def test_client_makes_two_attempts_in_total():
    pytest.importorskip("botocore")
    client = object_store.create_client(**object_store.settings(STORE))
    try:
        assert client.meta.config.retries == {"total_max_attempts": 2, "mode": "standard"}
        assert client.meta.config.s3 == {"addressing_style": "path"}
    finally:
        client.close()


def test_governed_pool_uses_db_connect_timeout(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.test/db")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "7")
    assert db._pool_config()[3] == 7
    db._close_pool()
    try:
        # min_size=0: building the pool opens no connection.
        assert db._pool("postgresql://unused.test/db", 1, 7).kwargs["connect_timeout"] == 7
    finally:
        db._close_pool()


@pytest.mark.parametrize("raw", ["0", "61", "soon"])
def test_invalid_db_connect_timeout_makes_the_governed_database_unavailable(monkeypatch, raw):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.test/db")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", raw)
    monkeypatch.setattr(db.psycopg, "connect", lambda *args, **kwargs: pytest.fail("must not connect"))
    with pytest.raises(GovernedError) as error:
        with db.transaction("token"):
            pass
    assert error.value.code == "EVIDENCE_UNAVAILABLE"


def test_governed_direct_connection_uses_db_connect_timeout(monkeypatch):
    captured = {}

    def refuse(url, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("no database in this test")

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.test/db")
    monkeypatch.setenv("GOVERNED_POOL_MAX_SIZE", "0")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "7")
    monkeypatch.setattr(db.psycopg, "connect", refuse)
    with pytest.raises(RuntimeError):
        with db.transaction("token"):
            pass
    assert captured["connect_timeout"] == 7


@pytest.mark.parametrize("raw", ["0", "61", "soon"])
def test_migrate_rejects_an_invalid_connect_timeout(monkeypatch, raw):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused.test/db")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", raw)
    monkeypatch.setattr(migrate, "migrate", lambda *args, **kwargs: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as error:
        migrate.main()
    assert "DB_CONNECT_TIMEOUT" in str(error.value)
