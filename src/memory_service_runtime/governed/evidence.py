"""Version-addressed evidence bytes, separate from database authority."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib

from memory_service_runtime import object_store
from memory_service_runtime.config import env_value
from memory_service_runtime.governed import db
from memory_service_runtime.governed.errors import GovernedError


MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


@contextmanager
def object_client():
    try:
        client = object_store.create_client(**object_store.settings())
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


def _stored_version(client, bucket: str, key: str, digest: str, length: int) -> str | None:
    """键按内容寻址：同一内容已经存过就沿用那个版本，重试不再产生新对象。"""
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    if head.get("Metadata", {}).get("sha256") != digest or head.get("ContentLength") != length:
        return None
    return head.get("VersionId")


def store_bytes(scope_id: str, domain_id: str, title: str, content: bytes, media_type: str) -> dict:
    """在 scope 栅栏之外调用（ADR-0006）：只写对象存储，不碰数据库。"""
    if not 1 <= len(content) <= MAX_EVIDENCE_BYTES:
        raise GovernedError("INVALID_REQUEST", "Evidence must contain 1..2097152 bytes", status=422)
    bucket = env_value("TKOS_OBJECT_STORE_BUCKET", required=True)
    digest = hashlib.sha256(content).hexdigest()
    key = f"gov/{scope_id}/{domain_id}/sha256/{digest}"
    with object_client() as client:
        from memory_service.context_graph.snapshot_storage import probe_immutability

        probe = probe_immutability(bucket, client)
        if not probe.passed:
            raise GovernedError("EVIDENCE_UNAVAILABLE", "Versioned retained evidence storage is required", status=503)
        version_id = _stored_version(client, bucket, key, digest, len(content))
        if version_id is None:
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
