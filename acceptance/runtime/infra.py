#!/usr/bin/env python3
"""Isolated acceptance infrastructure; generated credentials are never printed.

Uses Python's standard library for orchestration and an existing project Python
for psycopg/botocore. It installs no packages and never loads any existing .env.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".runtime-acceptance"
COMPOSE_FILE = Path(__file__).with_name("compose.yaml")
PROJECT = "tkos-ontology-runtime-acceptance"
DATABASE = "tkos_runtime_acceptance"
ADMIN = "tkos_acceptance_admin"
OWNER = "tkos_acceptance_owner"
APP = "tkos_acceptance_app"
GOV_MUTABLE_TABLES = frozenset({"gov_scopes", "gov_principals", "gov_credentials",
                                "gov_role_assignments", "gov_objects", "gov_feedback_state", "gov_work_item_state"})
SECRETS = STATE / "secrets.json"
ENV_FILE = STATE / "env.json"
COMPOSE_ENV = STATE / "compose.env"
SNAPSHOT_BUCKET = "runtime-acceptance-snapshots"


def private_write(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)


def private_json(path: Path, value: Any) -> None:
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def credentials() -> dict[str, str]:
    if SECRETS.exists():
        existing = json.loads(SECRETS.read_text())
        if "POSTGRES_PORT" not in existing:
            existing["POSTGRES_PORT"], existing["MINIO_PORT"] = free_ports()
            private_json(SECRETS, existing)
        return existing
    # If private state was lost, never invent different passwords for existing volumes.
    found = subprocess.run(
        ["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={PROJECT}", "-q"],
        text=True, capture_output=True, check=True,
    )
    if found.stdout.strip():
        raise RuntimeError("acceptance volumes exist but credentials are missing; restore private state")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    private_write(STATE / ".gitignore", "*\n")
    result = {
        "POSTGRES_ADMIN_PASSWORD": secrets.token_hex(24),
        "POSTGRES_OWNER_PASSWORD": secrets.token_hex(24),
        "POSTGRES_APP_PASSWORD": secrets.token_hex(24),
        "MINIO_ROOT_USER": "acceptance-root-" + secrets.token_hex(6),
        "MINIO_ROOT_PASSWORD": secrets.token_hex(24),
        "MINIO_APP_ACCESS_KEY": "acceptance-app-" + secrets.token_hex(6),
        "MINIO_APP_SECRET_KEY": secrets.token_hex(24),
        "MEMORY_TENANT": "runtime-acceptance-" + secrets.token_hex(6),
        "MEMORY_ORG": "runtime-acceptance-org-" + secrets.token_hex(6),
    }
    result["POSTGRES_PORT"], result["MINIO_PORT"] = free_ports()
    private_json(SECRETS, result)
    return result


def free_ports() -> tuple[str, str]:
    with socket.socket() as pg, socket.socket() as s3:
        pg.bind(("127.0.0.1", 0))
        s3.bind(("127.0.0.1", 0))
        return str(pg.getsockname()[1]), str(s3.getsockname()[1])


def redact(text: str) -> str:
    if SECRETS.exists():
        data = json.loads(SECRETS.read_text())
        for key, value in data.items():
            if any(part in key for part in ("PASSWORD", "SECRET", "ACCESS_KEY", "ROOT_USER")):
                text = text.replace(value, "[REDACTED]")
    if ENV_FILE.exists():
        data = json.loads(ENV_FILE.read_text())
        for key, value in data.items():
            if isinstance(value, str) and any(part in key for part in ("TOKEN", "SECRET", "PASSWORD", "KEY", "DATABASE_URL")):
                text = text.replace(value, "[REDACTED]")
    return text


def run(command: list[str], *, input_text: str | None = None,
        environment: dict[str, str] | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(command, input=input_text, env=environment, cwd=ROOT,
                          text=True, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError(redact(proc.stderr or proc.stdout or f"command exited {proc.returncode}"))
    return proc.stdout


def compose(*args: str) -> list[str]:
    if not COMPOSE_ENV.exists():
        raise RuntimeError("acceptance private configuration missing; run up first")
    return ["docker", "compose", "-p", PROJECT, "--project-directory", str(ROOT),
            "--env-file", str(COMPOSE_ENV), "-f", str(COMPOSE_FILE), *args]


def python_path() -> str:
    candidates = [os.environ.get("TKOS_ACCEPTANCE_PYTHON", ""),
                  str(ROOT / ".venv/bin/python")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("run uv sync --frozen --extra s3, or set TKOS_ACCEPTANCE_PYTHON to a Python with psycopg and botocore")


def child_python(code: str, payload: dict[str, Any], *, timeout: int = 180) -> Any:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    result = run([python_path(), "-c", code], input_text=json.dumps(payload),
                 environment=env, timeout=timeout)
    return json.loads(result)


def dsn(user: str, password: str, port: str) -> str:
    return f"postgresql://{user}:{quote(password, safe='')}@127.0.0.1:{port}/{DATABASE}"


def port(service: str, container_port: str) -> str:
    address = run(compose("port", service, container_port)).strip()
    if not address.startswith("127.0.0.1:") or "\n" in address:
        raise RuntimeError("acceptance service must expose one loopback-only port")
    return address.rsplit(":", 1)[1]


def test_admin_url() -> str:
    """Only for isolated infra or an explicitly requested migration-test child."""
    data = credentials()
    return dsn(ADMIN, data["POSTGRES_ADMIN_PASSWORD"], port("postgres", "5432"))


def write_environment(data: dict[str, str]) -> dict[str, str]:
    pg_port, s3_port = port("postgres", "5432"), port("minio", "9000")
    # Preserve additional test tokens/bootstrap values written by the acceptance suite.
    env = json.loads(ENV_FILE.read_text()) if ENV_FILE.exists() else {}
    env.update({
        "DATABASE_URL": dsn(APP, data["POSTGRES_APP_PASSWORD"], pg_port),
        "APP_DATABASE_URL": dsn(APP, data["POSTGRES_APP_PASSWORD"], pg_port),
        "MIGRATION_DATABASE_URL": dsn(OWNER, data["POSTGRES_OWNER_PASSWORD"], pg_port),
        "MEMORY_TENANT": data["MEMORY_TENANT"],
        "MEMORY_ORG": data["MEMORY_ORG"],
        "TKOS_OBJECT_STORE_ENDPOINT": f"http://127.0.0.1:{s3_port}",
        "TKOS_OBJECT_STORE_BUCKET": SNAPSHOT_BUCKET,
        "TKOS_OBJECT_STORE_ARTIFACT_BUCKET": "runtime-acceptance-artifacts",
        "TKOS_OBJECT_STORE_ACCESS_KEY": data["MINIO_APP_ACCESS_KEY"],
        "TKOS_OBJECT_STORE_SECRET_KEY": data["MINIO_APP_SECRET_KEY"],
        "TKOS_OBJECT_STORE_REGION": "us-east-1",
        "TKOS_OBJECT_STORE_VERIFY_TLS": "false",
        "RUNTIME_WORKER_ID": "runtime-acceptance-worker",
        "TKOS_ACCEPTANCE_PROJECT": PROJECT,
        "TKOS_ACCEPTANCE_PYTHON": python_path(),
    })
    private_json(ENV_FILE, env)
    app_env = {key: value for key, value in env.items() if key != "MIGRATION_DATABASE_URL"}
    private_write(STATE / "app.env", "".join(f"{k}={v}\n" for k, v in app_env.items()))
    return env


PROVISION_DB = r'''
import json, sys
import psycopg
from psycopg import sql
p = json.load(sys.stdin)
with psycopg.connect(p["admin_url"], autocommit=True) as conn:
    for role, password in ((p["owner"], p["owner_password"]), (p["app"], p["app_password"])):
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
        operation = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(operation + " ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(sql.Identifier(role), sql.Literal(password)))
    conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(p["database"]), sql.Identifier(p["owner"])))
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(p["database"])))
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(sql.Identifier(p["database"]), sql.Identifier(p["owner"]), sql.Identifier(p["app"])))
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(p["app"])))
print(json.dumps({"database": p["database"], "owner_role": p["owner"], "app_role": p["app"]}))
'''


MIGRATE = r'''
import json, sys
from memory_service_app.migrate import migrate
p=json.load(sys.stdin)
print(json.dumps({"applied": migrate(p["migration_url"])}))
'''


GRANTS = r'''
import json, sys
import psycopg
from psycopg import sql
p=json.load(sys.stdin)
with psycopg.connect(p["migration_url"]) as conn:
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
    for (table,) in tables:
        if table.startswith("gov_"):
            conn.execute(sql.SQL("REVOKE UPDATE, DELETE ON TABLE public.{} FROM {}").format(sql.Identifier(table), sql.Identifier(p["app"])))
            privileges = "SELECT, INSERT, UPDATE" if table in p["mutable_gov_tables"] else "SELECT, INSERT"
        elif table == "schema_migrations":
            privileges = "SELECT"
        else:
            privileges = "SELECT, INSERT, UPDATE, DELETE"
        conn.execute(sql.SQL("GRANT " + privileges + " ON TABLE public.{} TO {}").format(sql.Identifier(table), sql.Identifier(p["app"])))
    conn.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(p["app"])))
with psycopg.connect(p["app_url"]) as conn:
    row=conn.execute("SELECT current_user, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone()
    owned=conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user").fetchone()[0]
    assert not any(row[1:]), row
    assert owned == 0, owned
    gov=conn.execute("SELECT tablename, has_table_privilege(current_user, quote_ident(schemaname)||'.'||quote_ident(tablename), 'UPDATE'), has_table_privilege(current_user, quote_ident(schemaname)||'.'||quote_ident(tablename), 'DELETE') FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'gov_%' ORDER BY tablename").fetchall()
print(json.dumps({"app_role":row[0], "superuser":row[1], "bypassrls":row[2], "owned_tables":owned, "governed_mutation_privileges":[{"table":x[0],"update":x[1],"delete":x[2]} for x in gov]}))
'''


PROBE_S3 = r'''
import json, sys
from botocore.config import Config
from botocore.session import get_session
from memory_service.context_graph.object_store import S3SnapshotObjectStore
p=json.load(sys.stdin)
client=get_session().create_client("s3", endpoint_url=p["endpoint"], region_name="us-east-1", aws_access_key_id=p["access"], aws_secret_access_key=p["secret"], config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5, s3={"addressing_style":"path"}))
S3SnapshotObjectStore(client,bucket=p["bucket"]).preflight_immutability()
versioning=client.get_bucket_versioning(Bucket=p["bucket"])["Status"]
lock=client.get_object_lock_configuration(Bucket=p["bucket"])["ObjectLockConfiguration"]
print(json.dumps({"bucket":p["bucket"],"versioning":versioning,"lock":lock}))
client.close()
'''


def migrate_and_grant(env: dict[str, str]) -> dict[str, Any]:
    migrated = child_python(MIGRATE, {"migration_url": env["MIGRATION_DATABASE_URL"]})
    roles = child_python(GRANTS, {"migration_url": env["MIGRATION_DATABASE_URL"],
                                 "app_url": env["APP_DATABASE_URL"], "app": APP,
                                 "mutable_gov_tables": sorted(GOV_MUTABLE_TABLES)})
    return {"migrations": migrated, "roles": roles}


def up() -> dict[str, Any]:
    data = credentials()
    private_write(COMPOSE_ENV, "".join(f"{key}={value}\n" for key, value in data.items()))
    run(compose("config", "--quiet"))
    run(compose("up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "90", "postgres", "minio"))
    run(compose("run", "--rm", "--no-deps", "minio-init"))
    env = write_environment(data)
    child_python(PROVISION_DB, {
        "admin_url": dsn(ADMIN, data["POSTGRES_ADMIN_PASSWORD"], port("postgres", "5432")),
        "owner": OWNER, "app": APP, "database": DATABASE,
        "owner_password": data["POSTGRES_OWNER_PASSWORD"], "app_password": data["POSTGRES_APP_PASSWORD"],
    })
    checked = migrate_and_grant(env)
    probe = child_python(PROBE_S3, {"endpoint": env["TKOS_OBJECT_STORE_ENDPOINT"],
                                   "bucket": SNAPSHOT_BUCKET,
                                   "access": data["MINIO_APP_ACCESS_KEY"],
                                   "secret": data["MINIO_APP_SECRET_KEY"]})
    report = {"ok": True, "project": PROJECT, "postgres": "127.0.0.1:" + port("postgres", "5432"),
              "minio": env["TKOS_OBJECT_STORE_ENDPOINT"], "object_store": probe, **checked,
              "claim": "isolated infrastructure ready; application acceptance not yet performed"}
    private_json(STATE / "infra-report.json", report)
    return report


def load_environment() -> dict[str, str]:
    if not ENV_FILE.exists():
        raise RuntimeError("run up first")
    return json.loads(ENV_FILE.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "start", "stop", "status", "migrate"):
        sub.add_parser(name)
    runner = sub.add_parser("run", help="run a child with app credentials; never print them")
    runner.add_argument("--migration", action="store_true", help="use owner URL only in this child")
    runner.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command in ("up", "start"):
        report = up()
    elif args.command == "stop":
        run(compose("stop", "--timeout", "30", "postgres", "minio"))
        report = {"ok": True, "project": PROJECT, "stopped": True, "volumes_preserved": True}
    elif args.command == "status":
        raw = run(compose("ps", "--all", "--format", "json")).strip()
        rows = json.loads(raw) if raw.startswith("[") else [json.loads(line) for line in raw.splitlines() if line]
        report = {"project": PROJECT, "services": [{"service": row.get("Service"), "state": row.get("State"), "health": row.get("Health"), "ports": row.get("Publishers", [])} for row in rows]}
    elif args.command == "migrate":
        report = {"ok": True, **migrate_and_grant(load_environment())}
    else:
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not argv:
            raise RuntimeError("run requires a command after --")
        loaded = load_environment()
        env = dict(os.environ)
        env.pop("TEST_ADMIN_DATABASE_URL", None)
        env.pop("MIGRATION_DATABASE_URL", None)
        env.update({key: str(value) for key, value in loaded.items() if key != "MIGRATION_DATABASE_URL"})
        if args.migration:
            env["DATABASE_URL"] = loaded["MIGRATION_DATABASE_URL"]
            env["MIGRATION_DATABASE_URL"] = loaded["MIGRATION_DATABASE_URL"]
            env["TEST_ADMIN_DATABASE_URL"] = test_admin_url()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT)
        proc = subprocess.run(argv, env=env, cwd=ROOT, text=True, capture_output=True)
        # Child output is captured so accidental DSN/token output is redacted.
        sys.stdout.write(redact(proc.stdout))
        sys.stderr.write(redact(proc.stderr))
        raise SystemExit(proc.returncode)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(redact(f"acceptance infra failed: {exc}"), file=sys.stderr)
        raise SystemExit(1) from exc
