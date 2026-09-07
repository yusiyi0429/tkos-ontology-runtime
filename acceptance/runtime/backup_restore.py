#!/usr/bin/env python3
"""Quiesced PG + MinIO physical backup into new acceptance-only storage.

The caller must first stop its test API and Worker. Original volumes/databases
are retained. No pre-existing target-local resource is ever addressed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

try:
    from . import infra
except ImportError:
    import infra


MINIO_IMAGE = "quay.io/minio/minio@sha256:a1ea29fa28355559ef137d71fc570e508a214ec84ff8083e39bc5428980b015e"
TAR_IMAGE = "alpine:3.21.2"


DB_COUNTS = r'''
import json,sys
import psycopg
from psycopg import sql
p=json.load(sys.stdin)
with psycopg.connect(p["database_url"]) as c:
    tables=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
    counts={t:c.execute(sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(t))).fetchone()[0] for (t,) in tables}
print(json.dumps(counts,sort_keys=True))
'''

CREATE_DB = r'''
import json,sys
import psycopg
from psycopg import sql
p=json.load(sys.stdin)
with psycopg.connect(p["admin_url"],autocommit=True) as c:
    c.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(sql.Identifier(p["database"]),sql.Identifier(p["owner"])))
with psycopg.connect(p["restore_admin_url"],autocommit=True) as c:
    c.execute("CREATE EXTENSION IF NOT EXISTS vector")
    c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(p["database"])))
    c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(sql.Identifier(p["database"]),sql.Identifier(p["owner"]),sql.Identifier(p["app"])))
    c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(p["app"])))
print(json.dumps({"created_database":p["database"]}))
'''

RESTORE_OWNERS = r'''
import json,sys
import psycopg
from psycopg import sql
p=json.load(sys.stdin)
with psycopg.connect(p["admin_url"]) as c:
    # Restore data as the isolated admin: COPY cannot load FORCE RLS tables as
    # the ordinary owner. Transfer only this database's application objects;
    # REASSIGN OWNED would also affect shared objects in this PostgreSQL cluster.
    objects=c.execute("""SELECT n.nspname,cl.relname,cl.relkind FROM pg_class cl
      JOIN pg_namespace n ON n.oid=cl.relnamespace
      WHERE n.nspname='public' AND cl.relkind IN ('r','p','v','m','S')
        AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                        AND d.objid=cl.oid AND d.deptype='e')
      ORDER BY CASE WHEN cl.relkind='S' THEN 1 ELSE 0 END,cl.relname""").fetchall()
    for schema,name,kind in objects:
        noun={"r":"TABLE","p":"TABLE","v":"VIEW","m":"MATERIALIZED VIEW","S":"SEQUENCE"}[kind]
        c.execute(sql.SQL("ALTER "+noun+" {} OWNER TO {}").format(sql.Identifier(schema,name),sql.Identifier(p["owner"])))
    functions=c.execute("""SELECT n.nspname,pr.proname,pg_get_function_identity_arguments(pr.oid),pr.prokind
      FROM pg_proc pr JOIN pg_namespace n ON n.oid=pr.pronamespace
      WHERE n.nspname='public' AND pr.prokind IN ('f','p')
        AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
                        AND d.objid=pr.oid AND d.deptype='e')""").fetchall()
    for schema,name,args,kind in functions:
        c.execute(sql.SQL("ALTER "+("PROCEDURE" if kind=='p' else "FUNCTION")+" {}({}) OWNER TO {}").format(
            sql.Identifier(schema,name),sql.SQL(args),sql.Identifier(p["owner"])))
    wrong=c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner<>%s",(p["owner"],)).fetchone()[0]
    assert wrong==0,"restored application table ownership differs"
print(json.dumps({"tables_and_relations_transferred":len(objects),"functions_transferred":len(functions),"owner_verified":True}))
'''

S3_MANIFEST = r'''
import json,sys
from botocore.config import Config
from botocore.session import get_session
p=json.load(sys.stdin)
c=get_session().create_client("s3",endpoint_url=p["endpoint"],region_name="us-east-1",aws_access_key_id=p["access"],aws_secret_access_key=p["secret"],config=Config(signature_version="s3v4",connect_timeout=3,read_timeout=5,s3={"addressing_style":"path"}))
rows=[]
for bucket in p["buckets"]:
    for page in c.get_paginator("list_object_versions").paginate(Bucket=bucket):
        for kind in ("Versions","DeleteMarkers"):
            for v in page.get(kind,[]):
                row={"bucket":bucket,"kind":kind,"key":v["Key"],"version_id":v["VersionId"],"is_latest":v["IsLatest"]}
                if kind=="Versions":
                    # Hash exact bytes, not just ETag (multipart ETags are not content hashes).
                    import hashlib
                    obj=c.get_object(Bucket=bucket,Key=v["Key"],VersionId=v["VersionId"])
                    row["sha256"]=hashlib.sha256(obj["Body"].read()).hexdigest()
                    row["size"]=v["Size"]
                    row["lock_mode"]=obj.get("ObjectLockMode")
                    row["retain_until"]=str(obj.get("ObjectLockRetainUntilDate"))
                rows.append(row)
c.close()
print(json.dumps(sorted(rows,key=lambda r:(r["bucket"],r["key"],r["version_id"])),sort_keys=True))
'''


def database_url(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/" + database, parts.query, parts.fragment))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pg_copy_command(command: list[str], path: Path, *, restore: bool = False) -> None:
    mode = "rb" if restore else "wb"
    with path.open(mode) as stream:
        kwargs = {"stdin": stream, "stdout": subprocess.PIPE} if restore else {"stdout": stream}
        result = subprocess.run(command, stderr=subprocess.PIPE, timeout=180, **kwargs)
    path.chmod(0o600)
    if result.returncode:
        raise RuntimeError(infra.redact(result.stderr.decode(errors="replace")))


def minio_source_volume() -> str:
    container = infra.run(infra.compose("ps", "-q", "minio")).strip()
    metadata = json.loads(infra.run(["docker", "inspect", container, "--format", "{{json .Mounts}}"] ))
    volumes = [m["Name"] for m in metadata if m.get("Type") == "volume" and m.get("Destination") == "/data"]
    if len(volumes) != 1 or not volumes[0].startswith(infra.PROJECT + "_"):
        raise RuntimeError("source MinIO volume is not owned by the acceptance project")
    return volumes[0]


def wait_ready(endpoint: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint + "/minio/health/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError("restored MinIO did not become ready")


def restore_backup() -> dict:
    env = infra.load_environment()
    private = infra.credentials()
    admin_url = infra.test_admin_url()
    identity = secrets.token_hex(6)
    folder = infra.STATE / ("restore-" + identity)
    folder.mkdir(mode=0o700)
    restored_database = "tkos_runtime_restore_" + identity
    restored_volume = infra.PROJECT + "_minio_restore_" + identity
    restored_container = infra.PROJECT + "-minio-restore-" + identity
    pg_dump = folder / "postgres.dump"
    archive = folder / "minio-volume.tar"
    source_volume = minio_source_volume()
    buckets = [env["TKOS_OBJECT_STORE_BUCKET"], env["TKOS_OBJECT_STORE_ARTIFACT_BUCKET"]]
    s3_params = {"endpoint": env["TKOS_OBJECT_STORE_ENDPOINT"], "access": private["MINIO_APP_ACCESS_KEY"],
                 "secret": private["MINIO_APP_SECRET_KEY"], "buckets": buckets}
    source_s3 = infra.child_python(S3_MANIFEST, s3_params)
    source_counts = infra.child_python(DB_COUNTS, {"database_url": admin_url})
    pg_copy_command(infra.compose("exec", "-T", "postgres", "pg_dump", "-U", infra.ADMIN,
                                   "-d", infra.DATABASE, "--format=custom", "--no-owner", "--no-acl"), pg_dump)

    # No MinIO API-level copy: archive the stopped volume so object version IDs,
    # IAM metadata and retention records all remain the original values.
    infra.run(infra.compose("stop", "--timeout", "30", "minio"))
    try:
        infra.run(["docker", "run", "--rm", "--pull=never", "--network=none",
                   "--mount", f"type=volume,src={source_volume},dst=/source,readonly",
                   "--mount", f"type=bind,src={folder},dst=/backup", TAR_IMAGE,
                   "tar", "-C", "/source", "-cpf", "/backup/minio-volume.tar", "."])
        archive.chmod(0o600)
    finally:
        infra.run(infra.compose("start", "minio"))
        wait_ready(env["TKOS_OBJECT_STORE_ENDPOINT"])

    infra.run(["docker", "volume", "create", "--label", f"com.docker.compose.project={infra.PROJECT}",
               "--label", "tkos.acceptance.component=restore", restored_volume])
    infra.run(["docker", "run", "--rm", "--pull=never", "--network=none",
               "--mount", f"type=volume,src={restored_volume},dst=/target",
               "--mount", f"type=bind,src={folder},dst=/backup,readonly", TAR_IMAGE,
               "tar", "-C", "/target", "-xpf", "/backup/minio-volume.tar"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        restored_port = listener.getsockname()[1]
    minio_env = folder / "minio.env"
    infra.private_write(minio_env, f"MINIO_ROOT_USER={private['MINIO_ROOT_USER']}\nMINIO_ROOT_PASSWORD={private['MINIO_ROOT_PASSWORD']}\n")
    infra.run(["docker", "run", "-d", "--pull=never", "--name", restored_container,
               "--label", "tkos.acceptance.project=" + infra.PROJECT,
               "--label", "tkos.acceptance.component=restore", "--env-file", str(minio_env),
               "--mount", f"type=volume,src={restored_volume},dst=/data",
               "-p", f"127.0.0.1:{restored_port}:9000", MINIO_IMAGE,
               "server", "/data", "--console-address", ":9001"])
    restored_endpoint = f"http://127.0.0.1:{restored_port}"
    wait_ready(restored_endpoint)

    restore_admin = database_url(admin_url, restored_database)
    infra.child_python(CREATE_DB, {"admin_url": admin_url, "restore_admin_url": restore_admin,
                                  "database": restored_database, "owner": infra.OWNER, "app": infra.APP})
    pg_copy_command(infra.compose("exec", "-T", "postgres", "pg_restore", "-U", infra.ADMIN,
                                   "-d", restored_database, "--no-owner", "--no-acl", "--no-comments",
                                   "--exit-on-error"), pg_dump, restore=True)
    ownership = infra.child_python(RESTORE_OWNERS, {"admin_url": restore_admin, "owner": infra.OWNER})
    restored_env = dict(env)
    for key in ("DATABASE_URL", "APP_DATABASE_URL", "MIGRATION_DATABASE_URL"):
        restored_env[key] = database_url(env[key], restored_database)
    restored_env["TKOS_OBJECT_STORE_ENDPOINT"] = restored_endpoint
    restored_env["TKOS_ACCEPTANCE_RESTORE_ID"] = identity
    privileges = infra.child_python(infra.GRANTS, {
        "migration_url": restored_env["MIGRATION_DATABASE_URL"], "app_url": restored_env["APP_DATABASE_URL"],
        "app": infra.APP, "mutable_gov_tables": sorted(infra.GOV_MUTABLE_TABLES),
    })
    restored_counts = infra.child_python(DB_COUNTS, {"database_url": restore_admin})
    restored_s3 = infra.child_python(S3_MANIFEST, {**s3_params, "endpoint": restored_endpoint})
    if source_counts != restored_counts:
        raise RuntimeError("restored database table counts differ from quiesced source")
    if source_s3 != restored_s3:
        raise RuntimeError("restored S3 version IDs, bytes or retention differ from source")
    private_env = folder / "env.json"
    infra.private_json(private_env, restored_env)
    report = {
        "ok": True, "restore_id": identity, "source_project": infra.PROJECT,
        "restore_database": restored_database, "restore_minio_container": restored_container,
        "restore_minio_volume": restored_volume, "restore_endpoint": restored_endpoint,
        "private_env_file": str(private_env), "postgres_dump_sha256": sha256_file(pg_dump),
        "minio_archive_sha256": sha256_file(archive), "table_counts_equal": True,
        "table_count": len(source_counts), "s3_versions_equal": True,
        "s3_version_records": len(source_s3), "roles": privileges,
        "ownership": ownership,
        "source_storage_preserved": True,
        "recovery_boundary": "new database in the same PostgreSQL instance and a new MinIO volume; not host disaster recovery",
        "claim": "backup restored and bytes/version IDs reconciled; API checks against restore still required",
    }
    infra.private_json(folder / "restore-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--writers-quiesced", action="store_true", required=True,
                        help="caller confirms its API/Worker and tests have stopped writing")
    parser.parse_args()
    print(json.dumps(restore_backup(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(infra.redact(f"acceptance restore failed: {exc}"), file=sys.stderr)
        raise SystemExit(1) from exc
