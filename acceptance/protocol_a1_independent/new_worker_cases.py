"""Real legacy effects under current protocol matrix changes, with zero HTTP negatives."""
from __future__ import annotations

from copy import deepcopy
import json

from psycopg.types.json import Jsonb

from .binding_cases import _owner
from .control_adapter import LEGACY_CONTRACT, LEGACY_PROTOCOL
from .old_entrypoints import probe_old_http
from .support import public_json
from .worker_probe import run_dispatch, start_receiver, task_file


def append_registry(h, f, *, protocol_id, contract_version, content, reason):
    """Explicit owner test control, versioned; never updates an old row."""
    with _owner(h, f) as conn:
        seq = conn.execute("SELECT COALESCE(max(registry_seq),0)+1 AS n FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s",
                           (f["scope_id"], protocol_id)).fetchone()["n"]
        conn.execute("""INSERT INTO gov_protocol_support_registry
            (scope_id,protocol_id,contract_version,registry_seq,content,recorded_by)
            VALUES(%s,%s,%s,%s,%s,'independent-protocol-test-control')""",
            (f["scope_id"], protocol_id, contract_version, seq, Jsonb(content)))
        conn.execute("INSERT INTO gov_protocol_control_events(scope_id,event_type,detail,actor) VALUES(%s,%s,%s,%s)",
            (f["scope_id"], "independent_protocol_matrix_fixture", Jsonb({"registry_seq": seq,
             "protocol_id": protocol_id, "contract_version": contract_version, "reason": reason,
             "synthetic": True, "business_success": False}), "independent-protocol-test-control"))
    return seq


def run_new_worker_cases(h, f, *, source, old_source, url, legacy_flow, typed_fixture, control):
    original = h.sql(f, "SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1",
                     (f["scope_id"], LEGACY_PROTOCOL))[0]
    assert original["contract_version"] == LEGACY_CONTRACT and original["content"]["can_write"] is True
    command = next(row for row in legacy_flow.commands if row["receipt"]["action_type"] == "activate_commitment")
    receipt = command["receipt"]
    assert len(receipt["effect_task_ids"]) == 1
    task_id = receipt["effect_task_ids"][0]
    real = task_file(h, f, task_id)
    forged = task_file(h, f, task_id, payload_updates={
        "protocol_id": LEGACY_PROTOCOL, "contract_version": LEGACY_CONTRACT,
        "method_profile_ref": {"profile_id": "queue-cannot-select-a-profile", "revision": "forged"},
        "action_type": "confirm_closure", "object_versions": [typed_fixture["types"]["CompanyOutcome"]]})
    result = {"checks": {}, "probes": [], "contract_a1_accepted": False}
    receiver = start_receiver(h, "current-matrix")
    # Queue metadata is untrusted: the actual body must still be the immutable
    # legacy receipt's body. Rejecting solely the extra fields is not required.
    positive = run_dispatch(h, f, source, forged, label="queue-fields-do-not-select-authority",
                            receiver=receiver, expect_success=True)
    assert positive["passed"]
    result["checks"]["legacy_effect_positive_control"] = {"passed": True, "evidence": positive}
    result["probes"].append(positive)
    before, external = h.snapshot(f), receiver.snapshot()
    assert legacy_flow.clients[command["actor"]].json("POST", "/v1/actions", command["request"]) == receipt
    assert h.snapshot(f) == before and receiver.snapshot() == external
    result["replay_does_not_dispatch"] = {"passed": True, "receiver_calls": external["total_calls"], "receipt_id": receipt["receipt_id"]}
    expected = control.content.get("worker_contract", {})
    trials = [
        ("write-disabled", LEGACY_CONTRACT, {**deepcopy(original["content"]), "can_write": False}, "write_disabled"),
        ("version-unknown", "tkos.governed/999-independent-worker", deepcopy(original["content"]), "unknown_version"),
        ("action-removed", LEGACY_CONTRACT, {**deepcopy(original["content"]), "actions": [
            action for action in original["content"]["actions"] if action != receipt["action_type"]]}, "action_removed"),
    ]
    try:
        for label, contract_version, content, expectation in trials:
            append_registry(h, f, protocol_id=LEGACY_PROTOCOL, contract_version=contract_version,
                            content=content, reason=label)
            denied = run_dispatch(h, f, source, forged, label=label, receiver=receiver,
                expected_error_codes=(expected.get(expectation, "governance_effect_permission_revoked"),))
            assert denied["passed"], "current worker ignored the actual protocol support matrix"
            result["probes"].append(denied)
        result["checks"]["new_worker_unsupported_effect_no_http"] = {"passed": True,
            "evidence": {"negative_matrix_entries": [row[0] for row in trials], "total_calls": receiver.snapshot()["total_calls"]}}
        result["checks"]["queue_metadata_cannot_select_legacy"] = {"passed": True,
            "evidence": {"positive_and_negative_body_selection": True, "probes": len(result["probes"])}}
    finally:
        append_registry(h, f, protocol_id=LEGACY_PROTOCOL, contract_version=original["contract_version"],
                        content=original["content"], reason="restore-prior-support-matrix")
        public_json(h.output / "new-worker-protocol-cases.json", result)
    if old_source is not None:
        old = probe_old_http(h, old_source, f, domain_id=f["domain_id"], positive_current_url=url)
        assert old["passed"]
        result["checks"]["real_old_api_cannot_write"] = {"passed": True, "evidence": old}
    public_json(h.output / "new-worker-protocol-cases.json", result)
    return result
