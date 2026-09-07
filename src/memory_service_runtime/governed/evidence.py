"""Version-addressed evidence bytes, separate from database authority."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
import uuid

from memory_service_runtime.config import env_value
from memory_service_runtime.governed import db
from memory_service_runtime.governed.errors import GovernedError


MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


@contextmanager
def object_client():
    try:
        from botocore.config import Config
        from botocore.session import get_session

        client = get_session().create_client(
            "s3",
            endpoint_url=env_value("TKOS_OBJECT_STORE_ENDPOINT", required=True),
            region_name=env_value("TKOS_OBJECT_STORE_REGION", default="us-east-1"),
            aws_access_key_id=env_value("TKOS_OBJECT_STORE_ACCESS_KEY", required=True),
            aws_secret_access_key=env_value("TKOS_OBJECT_STORE_SECRET_KEY", required=True),
            verify=os.environ.get("TKOS_OBJECT_STORE_VERIFY_TLS", "true").lower() not in {"0", "false", "no"},
            config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=5,
                          retries={"total_max_attempts": 2, "mode": "standard"},
                          s3={"addressing_style": "path"}),
        )
    except Exception as exc:
        raise GovernedError("EVIDENCE_UNAVAILABLE", "Evidence storage configuration is unavailable", status=503) from exc
    try:
        yield client
    except GovernedError:
        raise
    except Exception as exc:
        raise GovernedError("EVIDENCE_UNAVAILABLE", "Evidence storage could not complete the operation", status=503) from exc
    finally:
        client.close()


def store_bytes(ctx, domain_id: str, title: str, content: bytes, media_type: str) -> dict:
    if not 1 <= len(content) <= MAX_EVIDENCE_BYTES:
        raise GovernedError("INVALID_REQUEST", "Evidence must contain 1..2097152 bytes", status=422)
    bucket = env_value("TKOS_OBJECT_STORE_BUCKET", required=True)
    key = f"gov/{ctx.scope_id}/{domain_id}/{uuid.uuid4()}"
    digest = hashlib.sha256(content).hexdigest()
    with object_client() as client:
        from memory_service.context_graph.snapshot_storage import probe_immutability

        probe = probe_immutability(bucket, client)
        if not probe.passed:
            raise GovernedError("EVIDENCE_UNAVAILABLE", "Versioned retained evidence storage is required", status=503)
        saved = client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=media_type,
                                  Metadata={"sha256": digest})
        version_id = saved.get("VersionId")
        if not version_id or version_id == "null":
            raise GovernedError("EVIDENCE_UNAVAILABLE", "Storage did not return a durable version identity", status=503)
    return {"title": title, "bucket": bucket, "key": key, "version_id": version_id,
            "sha256": digest, "length": len(content), "media_type": media_type}


def fetch_payload(payload: dict, *, scope_id: str, domain_id: str) -> bytes:
    required = {"bucket", "key", "version_id", "sha256", "length", "media_type"}
    if not required <= payload.keys() or not payload["key"].startswith(f"gov/{scope_id}/{domain_id}/"):
        raise GovernedError("EVIDENCE_UNAVAILABLE", "Evidence provenance is invalid", status=503)
    if payload["bucket"] != env_value("TKOS_OBJECT_STORE_BUCKET", required=True):
        raise GovernedError("EVIDENCE_UNAVAILABLE", "Evidence bucket is not configured", status=503)
    with object_client() as client:
        response = client.get_object(Bucket=payload["bucket"], Key=payload["key"], VersionId=payload["version_id"])
        stream = response["Body"]
        try:
            content = stream.read(MAX_EVIDENCE_BYTES + 1)
        finally:
            stream.close()
        if response.get("VersionId") != payload["version_id"]:
            raise GovernedError("EVIDENCE_UNAVAILABLE", "Evidence version did not match", status=503)
    if len(content) != payload["length"] or hashlib.sha256(content).hexdigest() != payload["sha256"]:
        raise GovernedError("EVIDENCE_UNAVAILABLE", "Evidence bytes failed integrity verification", status=503)
    return content


def verify_revision(conn, ctx, evidence_revision_id: str) -> dict:
    row = conn.execute(
        "SELECT object_id FROM gov_object_revisions WHERE scope_id=%s AND revision_id=%s",
        (ctx.scope_id, str(evidence_revision_id)),
    ).fetchone()
    if row is None:
        raise GovernedError("NOT_FOUND", "Evidence revision was not found", status=404)
    obj = db.object_row(conn, ctx, str(row["object_id"]))
    if obj["object_type"] != "EvidenceAsset":
        raise GovernedError("INVALID_STATE", "The supplied revision is not evidence", status=409)
    revision = db.revision_row(conn, ctx, obj["object_id"], str(evidence_revision_id))
    payload = revision["payload"]
    fetch_payload(payload, scope_id=ctx.scope_id, domain_id=obj["domain_id"])
    return payload
