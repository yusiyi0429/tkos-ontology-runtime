"""Independent probes of real old code after A1 migration.

Run only in root-selected one-off scopes. A DB write failure alone is insufficient
for evidence uploads: the S3 version inventory must also remain unchanged.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import uuid

from acceptance.runtime.client import Client
from .support import Harness, digest, public_json


def probe_old_http(harness: Harness, old_source: Path, fixture: dict, *, domain_id: str,
                   positive_current_url: str) -> dict:
    """Use the same current-readable actor for old create and old S3 upload.

    Returns actual HTTP status/code, SQL and S3 changes. Any success or durable
    change is a failure. The caller freezes the expected old-process failure
    mode once the actual migration/route isolation design is available.
    """
    current = harness.clients(positive_current_url, fixture)
    old_clients = None
    api = None
    try:
        # Prove this is a valid current principal, not an invalid-token rejection.
        current["ceo"].object(fixture["outcome"]["object_id"])
        api, old_url, ready = harness.start_api(old_source, old=True, updates={
            "MEMORY_TENANT": fixture["tenant_id"], "MEMORY_ORG": fixture["company_id"]})
        old_clients = harness.clients(old_url, fixture)
        commands = [
            ("old_create", "/v1/actions", Client.command("create_object", {
                "object_type": "CompanyOutcome", "domain_id": domain_id,
                "payload": {"title": "Old process must not create an unbound object"}},
                key=f"old-process-probe-{uuid.uuid4()}")),
            ("old_evidence", "/v1/evidence-assets", {"domain_id": domain_id,
                "title": "Old process must not write S3 before its DB fence",
                "content_base64": base64.b64encode(b"Independent old writer fence probe\n").decode(),
                "media_type": "text/plain"}),
        ]
        rows = []
        for label, path, body in commands:
            before = harness.snapshot(fixture)
            storage_before = harness.storage_snapshot(fixture["scope_id"])
            # Frozen after review of 0018's restrictive credential/scope reads:
            # real old authenticate receives no row before it can reach S3.
            response = old_clients["ceo"].request("POST", path, body, expected=401)
            after = harness.snapshot(fixture)
            storage_after = harness.storage_snapshot(fixture["scope_id"])
            error = response.json().get("error", {}) if "application/json" in response.headers.get("content-type", "") else {}
            row = {"probe": label, "status": response.status_code, "code": error.get("code"),
                   "sql_unchanged": before == after, "s3_unchanged": storage_before == storage_after,
                   "before_sql_sha256": digest(before), "after_sql_sha256": digest(after),
                   "before_s3_versions": storage_before["count"], "after_s3_versions": storage_after["count"]}
            row["passed"] = (response.status_code == 401 and row["code"] == "UNAUTHENTICATED"
                             and row["sql_unchanged"] and row["s3_unchanged"])
            rows.append(row)
        result = {"gate": "real_old_http_entrypoints", "api_provenance": json.loads(ready.read_text()),
                  "checks": rows, "passed": all(row["passed"] for row in rows), "contract_a1_accepted": False}
        public_json(harness.output / "old-http-entrypoints.json", result)
        return result
    finally:
        for client in current.values():
            client.close()
        if old_clients:
            for client in old_clients.values():
                client.close()
        if api:
            harness.stop(api)
