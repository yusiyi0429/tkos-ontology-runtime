"""Trusted scope/domain creation policy and evidence registration boundaries."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import uuid

from acceptance.runtime.client import Client
from .control_adapter import A_CONTRACT, A_PROTOCOL, LEGACY_CONTRACT, LEGACY_PROTOCOL, NotReady
from .http_fence_cases import run_creation_fences
from .support import Harness, private_json, public_json, digest


def run_creation_cases(h: Harness, f: dict, *, url: str, adapter, control, profile_file,
                       legacy_flow, legacy_result) -> dict:
    core = json.loads(profile_file.read_text())
    scope_rows = h.sql(f, "SELECT * FROM gov_protocol_policies WHERE scope_id=%s AND domain_id IS NULL ORDER BY policy_seq DESC LIMIT 1", (f["scope_id"],))
    domain_rows = h.sql(f, "SELECT * FROM gov_protocol_policies WHERE scope_id=%s AND domain_id=%s ORDER BY policy_seq DESC LIMIT 1", (f["scope_id"], f["domain_id"]))
    if not scope_rows:
        raise NotReady("trusted legacy default policy must already exist")
    original_scope = scope_rows[0]["content"]
    original_domain = domain_rows[0]["content"] if domain_rows else original_scope
    assert original_domain["default_protocol"] == LEGACY_PROTOCOL
    a_policy = {"default_protocol": A_PROTOCOL, "default_contract_version": A_CONTRACT,
        "allow_legacy_create": False, "record_origin": "synthetic", "experimental": True,
        "default_profile_ref": {"profile_id": core["profile_id"], "revision": core["revision"]},
        "notes": "Independent synthetic A creation policy; no A business handler is implemented"}
    registry_rows = h.sql(f, "SELECT * FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=ANY(%s) ORDER BY registry_seq DESC",
                         (f["scope_id"], [A_PROTOCOL, LEGACY_PROTOCOL]))
    originals = {}
    for row in registry_rows:
        originals.setdefault(row["protocol_id"], row)
    if set(originals) != {A_PROTOCOL, LEGACY_PROTOCOL}:
        raise NotReady("both compiled protocols must have an actual server support record")
    clients = h.clients(url, f)
    touched_policy = set()
    touched_registry = set()
    result = {"checks": {}, "evidence_checks": {}, "authority_checks": {}, "requests": [], "contract_a1_accepted": False}

    def policy(label, content, *, scoped=False):
        name = "set_scope_policy" if scoped else "set_domain_policy"
        path = h.private / ("policy-" + label + ".json")
        private_json(path, content)
        values = {"scope_id": f["scope_id"], "domain_id": f["domain_id"], "document_file": path,
                  "reason": "Independent policy precedence acceptance", "operator": "codex-independent-acceptance"}
        control.invoke(adapter, name, label, values)
        touched_policy.add("scope" if scoped else "domain")
        where = "domain_id IS NULL" if scoped else "domain_id=%s"
        args = (f["scope_id"],) if scoped else (f["scope_id"], f["domain_id"])
        rows = h.sql(f, "SELECT content FROM gov_protocol_policies WHERE scope_id=%s AND " + where + " ORDER BY policy_seq DESC LIMIT 1", args)
        assert len(rows) == 1 and rows[0]["content"] == content, "CLI policy did not persist exact server-selected content"

    def registry(label, protocol_id, content, *, contract_version=None):
        original = originals[protocol_id]
        path = h.private / ("registry-" + label + ".json")
        private_json(path, content)
        control.invoke(adapter, "set_registry", label, {"scope_id": f["scope_id"], "protocol_id": protocol_id,
            "contract_version": contract_version or original["contract_version"], "document_file": path,
            "reason": "Independent evidence freeze acceptance", "operator": "codex-independent-acceptance"})
        touched_registry.add(protocol_id)
        row = h.sql(f, "SELECT content FROM gov_protocol_support_registry WHERE scope_id=%s AND protocol_id=%s ORDER BY registry_seq DESC LIMIT 1",
                    (f["scope_id"], protocol_id))[0]
        assert row["content"] == content

    try:
        policy("scope-a", a_policy, scoped=True)
        policy("domain-explicit-legacy", original_domain)
        command = Client.command("create_object", {"object_type": "CompanyOutcome", "domain_id": f["domain_id"],
            "payload": {"title": "Explicit domain legacy policy overrides A scope policy"}})
        accepted = clients["ceo"].json("POST", "/v1/actions", command)
        binding = adapter.binding(f, accepted["result"]["object_id"])
        assert (binding["protocol_id"], binding["contract_version"]) == (LEGACY_PROTOCOL, LEGACY_CONTRACT)
        result["requests"].append({"kind": "explicit_domain_legacy_override", "receipt_id": accepted["receipt_id"]})
        policy("scope-legacy", original_scope, scoped=True)
        policy("domain-a", a_policy)
        parents = {"BusinessCommitment": legacy_flow.ref(legacy_result["outcome"]),
                   "ExecutionCommitment": legacy_flow.ref(legacy_result["bc"]),
                   "WorkItem": legacy_flow.ref(legacy_result["ec"])}
        fences = run_creation_fences(h, f, url=url, a_domain_id=f["domain_id"], existing_legacy_parents=parents)
        assert len(fences["checks"]) == 30
        for name in ("new_domain_old_bc_ec_work_denied", "removed_parent_no_fallback", "old_parent_new_domain_denied",
                     "scope_domain_policy_precedence_enforced"):
            result["checks"][name] = {"passed": True, "evidence": {"report": "creation-protocol-fences.json"}}

        # Authority revocation is an explicit targetless control action. It
        # must remain available under A policy without creating an A success.
        from .binding_cases import _owner
        assignment = str(uuid.uuid4())
        with _owner(h, f) as conn:
            conn.execute("INSERT INTO gov_role_assignments(assignment_id,scope_id,principal_id,domain_id,role) VALUES(%s,%s,%s,%s,'IC')",
                (assignment, f["scope_id"], f["actors"]["verifier"]["principal_id"], f["domain_id"]))
        before = h.snapshot(f)
        control_command = Client.command("revoke_assignment", {"assignment_id": assignment})
        prepared = clients["ceo"].json("POST", "/v1/actions/prepare", control_command)
        assert h.snapshot(f) == before
        control_command["expected_versions"] = prepared["expected_versions"]
        receipt = clients["ceo"].json("POST", "/v1/actions", control_command)
        assert receipt["action_type"] == "revoke_assignment" and receipt["result"]["after_active"] is False
        assert h.sql(f, "SELECT active FROM gov_role_assignments WHERE scope_id=%s AND assignment_id=%s", (f["scope_id"], assignment))[0]["active"] is False
        after = h.snapshot(f)
        for table in ("gov_objects", "gov_object_revisions", "gov_object_protocol_bindings", "gov_lifecycle_events"):
            assert after["tables"][table] == before["tables"][table], "targetless control created business state"
        result["authority_checks"]["targetless_authority_control_explicit"] = {"passed": True,
            "evidence": {"policy": A_PROTOCOL, "assignment_id": assignment, "receipt_id": receipt["receipt_id"], "business_state_unchanged": True}}

        # Contract A may register neutral evidence if the server explicitly
        # enables it. This is not an A2/A3 business transition.
        enabled = {**deepcopy(originals[A_PROTOCOL]["content"]), "evidence_upload": True}
        registry("a-evidence-enabled", A_PROTOCOL, enabled)
        body = {"domain_id": f["domain_id"], "title": "Independent A neutral evidence metadata",
                "content_base64": base64.b64encode(b"synthetic A evidence\n").decode(), "media_type": "text/plain"}
        asset = clients["ceo"].json("POST", "/v1/evidence-assets", body)
        evidence_binding = adapter.binding(f, asset["object_id"])
        assert (evidence_binding["protocol_id"], evidence_binding["contract_version"]) == (A_PROTOCOL, A_CONTRACT)
        assert evidence_binding["profile_canonical_hash"] == core["canonical_hash"]
        assert clients["ceo"].request("GET", f"/v1/evidence-assets/{asset['object_id']}/revisions/{asset['revision_id']}").content == b"synthetic A evidence\n"
        result["requests"].append({"kind": "neutral_a_evidence", "object_id": asset["object_id"], "binding": evidence_binding})
        for protocol_id in (A_PROTOCOL, LEGACY_PROTOCOL):
            if protocol_id == LEGACY_PROTOCOL:
                policy("domain-legacy-evidence", original_domain)
            disabled = {**deepcopy(originals[protocol_id]["content"]), "evidence_upload": False}
            registry("evidence-disabled-" + protocol_id.rsplit(".", 1)[-1], protocol_id, disabled)
            result["requests"].append(h.rejection(f, clients["ceo"], "/v1/evidence-assets", body, 409,
                                                   "PROTOCOL_WRITE_DISABLED", check_storage=True))
        result["evidence_checks"]["disabled_a_and_legacy_evidence_before_s3"] = {"passed": True}
        registry("unknown-legacy-version-evidence", LEGACY_PROTOCOL,
                 deepcopy(originals[LEGACY_PROTOCOL]["content"]), contract_version="tkos.governed/999-independent-evidence")
        unknown = h.rejection(f, clients["ceo"], "/v1/evidence-assets", body, 409,
                              "PROTOCOL_NOT_SUPPORTED", check_storage=True)
        result["evidence_checks"]["unknown_protocol_evidence_before_s3"] = {"passed": True, "evidence": unknown}
        return result
    finally:
        # Restore effective policy/registry by versioned owner operations. This
        # never deletes installed records or rewrites historical versions.
        if "scope" in touched_policy:
            policy("restore-scope", original_scope, scoped=True)
        if "domain" in touched_policy:
            policy("restore-domain", original_domain)
        for protocol_id in sorted(touched_registry):
            registry("restore-" + protocol_id.rsplit(".", 1)[-1], protocol_id, originals[protocol_id]["content"])
        public_json(h.output / "creation-policy-cases.json", result)
        for client in clients.values():
            client.close()
