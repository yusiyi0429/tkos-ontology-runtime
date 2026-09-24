"""Apply the packaged append-only PostgreSQL migrations.

Each file runs once, in full-file-name order, inside the single transaction of
the run.  The runner:

- holds a transaction advisory lock, so concurrent runs serialize and the later
  ones find nothing left to apply;
- records the SHA-256 of every applied file and refuses to continue when an
  applied file has changed or disappeared.  Rows written by the old runner get
  their SHA-256 backfilled from the current file: that first backfill trusts
  whatever is on disk, so check the files against the released artifact before
  upgrading (the delivery-candidate acceptance compares them with the v0.4.0
  image);
- refuses names outside ``NNNN_name.sql`` and new duplicate numbers — the two
  historical 0028 files stay allowed, their identities are never rewritten.

A refusal applies nothing.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import os
from pathlib import Path
import re

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql\Z")
_HISTORICAL_DUPLICATES = {"0028": 2}
_LOCK_KEY = 0x746B6F73  # "tkos"：只用于迁移互斥


class MigrationError(RuntimeError):
    """迁移目录或已应用记录不一致；本次运行不应用任何迁移。"""


def _files() -> list[Path]:
    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    malformed = [path.name for path in paths if not _NAME.match(path.name)]
    if malformed:
        raise MigrationError(f"迁移文件名不合规：{', '.join(malformed)}")
    counts = Counter(_NAME.match(path.name).group(1) for path in paths)
    duplicated = sorted(number for number, count in counts.items()
                        if count > _HISTORICAL_DUPLICATES.get(number, 1))
    if duplicated:
        raise MigrationError(f"迁移编号重复：{', '.join(duplicated)}")
    return paths


def migrate(database_url: str, *, connect_timeout: int = 5) -> list[str]:
    """Apply every migration exactly once and return newly applied filenames."""
    paths = _files()
    digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    applied: list[str] = []
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations(
                 name text PRIMARY KEY,
                 applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        # 只在旧表缺这一列时 ALTER：之后的运行不再拿表上的排他锁，也不要求一定是表的属主。
        if conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid='schema_migrations'::regclass"
                        " AND attname='sha256' AND NOT attisdropped").fetchone() is None:
            conn.execute("ALTER TABLE schema_migrations ADD COLUMN sha256 text")
        done = dict(conn.execute("SELECT name, sha256 FROM schema_migrations").fetchall())
        missing = sorted(set(done) - set(digests))
        if missing:
            raise MigrationError(f"已应用的迁移文件不见了：{', '.join(missing)}")
        changed = sorted(name for name, recorded in done.items()
                         if recorded is not None and recorded != digests[name])
        if changed:
            raise MigrationError(f"已应用的迁移文件被改动过：{', '.join(changed)}")
        for name, recorded in done.items():
            if recorded is None:
                conn.execute("UPDATE schema_migrations SET sha256=%s WHERE name=%s", (digests[name], name))
        for path in paths:
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(name, sha256) VALUES (%s, %s)",
                (path.name, digests[path.name]),
            )
            applied.append(path.name)
    return applied


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL 未设置")
    timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", "5"))
    try:
        applied = migrate(database_url, connect_timeout=timeout)
    except MigrationError as exc:
        raise SystemExit(str(exc)) from exc
    for name in applied:
        print(name)


if __name__ == "__main__":
    main()
