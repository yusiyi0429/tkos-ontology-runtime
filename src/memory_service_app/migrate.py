"""Apply the packaged append-only PostgreSQL migrations."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrate(database_url: str, *, connect_timeout: int = 5) -> list[str]:
    """Apply every migration exactly once and return newly applied filenames."""
    applied: list[str] = []
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations(
                 name text PRIMARY KEY,
                 applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        done = {
            row[0]
            for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(name) VALUES (%s)",
                (path.name,),
            )
            applied.append(path.name)
    return applied


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL 未设置")
    timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))
    for name in migrate(database_url, connect_timeout=timeout):
        print(name)


if __name__ == "__main__":
    main()
