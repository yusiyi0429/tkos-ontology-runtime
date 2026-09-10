"""HTTP transaction faults, concurrent creation and lost-response replay.

Runs only when explicitly called by the acceptance coordinator after migration.
Every assertion inspects PostgreSQL independently; implementation hooks select
fault locations only and are never used as the result oracle.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sys
import threading
import uuid

import httpx

from acceptance.runtime.client import Client
from .support import Harness, ROOT, digest, public_json, wait


def _command(fixture: dict, label: str) -> dict:
    return Client.command("create_object", {"object_type": "CompanyOutcome",
        "domain_id": fixture["domain_id"], "payload": {"title": "Independent " + label}},
        key="a1-independent-create-" + uuid.uuid4().hex)


def created_rows(h: Harness, f: dict, command: dict, receipt: dict) -> dict:
    oid, rid = receipt["result"]["object_id"], receipt["receipt_id"]
    checks = {}
    for table, column in (("gov_objects", "object_id"), ("gov_object_revisions", "object_id"),
                          ("gov_object_protocol_bindings", "object_id"),
                          ("gov_lifecycle_events", "object_id"), ("gov_action_receipts", "receipt_id")):
        value = rid if column == "receipt_id" else oid
        count = h.sql(f, f"SELECT count(*) AS n FROM {table} WHERE scope_id=%s AND {column}=%s",
                      (f["scope_id"], value))[0]["n"]
        assert count == 1, f"creation did not commit exactly one {table} row"
        checks[table] = count
    binding = h.sql(f, "SELECT * FROM gov_object_protocol_bindings WHERE scope_id=%s AND object_id=%s",
                    (f["scope_id"], oid))[0]
    assert binding["binding_version"] == 1
    assert binding["protocol_id"] == "tkos.legacy-governed"
    assert binding["contract_version"] == "tkos.governed/v0.2"
    assert str(binding["receipt_id"]) == rid, "binding lost its creating receipt"
    stored = h.sql(f, "SELECT receipt_id::text FROM gov_action_receipts WHERE scope_id=%s AND idempotency_key=%s",
                   (f["scope_id"], command["idempotency_key"]))
    assert stored == [{"receipt_id": rid}]
    assert receipt["effect_task_ids"] == []
    return {"object_id": oid, "receipt_id": rid, "rows": checks}


def run_transactions(h: Harness, f: dict, *, url: str) -> dict:
    clients = h.clients(url, f)
    ceo = clients["ceo"]
    proxy = None
    proxy_client = None
    proxy_clients = {}
    results = {}
    try:
        command = _command(f, "rollback after initial binding")
        before = h.snapshot(f)
        token = "a1-rollback-" + uuid.uuid4().hex
        response = ceo.request("POST", "/v1/actions", command, expected=500,
                              headers=h.headers("before_business_commit", token=token))
        reached_path = h.control / (token + ".reached.json")
        reached = wait(lambda: json.loads(reached_path.read_text()) if reached_path.exists() else None)
        assert reached["checkpoint"] == "before_business_commit"
        assert reached["context"]["action_type"] == "create_object"
        assert h.snapshot(f) == before, "injected failure left object/binding/event/receipt/task rows"
        results["rollback"] = {"checkpoint": reached, "http_status": response.status_code,
                               "snapshot_sha256": digest(before)}
        receipt = ceo.json("POST", "/v1/actions", command)
        results["retry"] = created_rows(h, f, command, receipt)
        committed = h.snapshot(f)
        assert ceo.json("POST", "/v1/actions", command) == receipt
        assert h.snapshot(f) == committed

        command = _command(f, "same-key simultaneous create")
        gate = threading.Barrier(2)
        def simultaneous():
            actor = Client(url, f["actors"]["ceo"]["token"], "same-ceo-concurrent", h.log)
            try:
                gate.wait(timeout=5)
                return actor.json("POST", "/v1/actions", deepcopy(command))
            finally:
                actor.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(simultaneous) for _ in range(2)]
            receipts = [task.result(timeout=35) for task in tasks]
        assert receipts[0] == receipts[1], "same key created divergent receipts"
        results["concurrency"] = created_rows(h, f, command, receipts[0])

        command = _command(f, "lost HTTP response after commit")
        ready_file = h.private / ("proxy-ready-" + uuid.uuid4().hex + ".json")
        proxy = h.spawn("response-loss-proxy", [sys.executable, "-B", str(ROOT / "acceptance/runtime/proxy.py"),
            "--port", "0", "--upstream", url, "--control-dir", str(h.control),
            "--key-file", str(h.key_file), "--ready-file", str(ready_file)])
        ready = wait(lambda: json.loads(ready_file.read_text()) if ready_file.exists() else None)
        proxy_clients = h.clients(ready["url"], f)
        proxy_client = proxy_clients["ceo"]
        token = "a1-drop-" + uuid.uuid4().hex
        try:
            proxy_client.request("POST", "/v1/actions", command, headers={
                "X-Acceptance-Key": h.control_key, "X-Acceptance-Drop-Response": token})
        except httpx.TransportError:
            pass
        else:
            raise AssertionError("the proxy did not actually lose the HTTP response")
        dropped_path = h.control / (token + ".dropped.json")
        dropped = wait(lambda: json.loads(dropped_path.read_text()) if dropped_path.exists() else None)
        assert dropped["upstream_status"] == 200, "lost response must have been a committed success"
        before_replay = h.snapshot(f)
        receipt = proxy_client.json("POST", "/v1/actions", command)
        assert ceo.json("POST", "/v1/actions", command) == receipt
        assert h.snapshot(f) == before_replay
        rows = created_rows(h, f, command, receipt)
        denied = h.rejection(f, ceo, "/v1/actions", {**command, "reason": "changed request body"},
                             409, "IDEMPOTENCY_CONFLICT")
        results["response_loss"] = {"proxy_event": dropped, **rows, "changed_body": denied,
                                    "snapshot_sha256": digest(before_replay)}
        public_json(h.output / "transaction-cases.json", {"checks": results, "contract_a1_accepted": False})
        return results
    finally:
        for client in proxy_clients.values():
            client.close()
        if proxy:
            h.stop(proxy)
        for client in clients.values():
            client.close()
