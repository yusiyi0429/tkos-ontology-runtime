"""Exercise every frozen Profile input through the real owner maintenance CLI."""
from __future__ import annotations

from pathlib import Path
import json
from copy import deepcopy
import uuid

from .control_adapter import ControlAdapter, FrozenControlContract
from .profile_cases import cases, P1_HASH, rehash
from .control_adapter import NotReady
from .support import Harness, private_json, public_json, digest


def run_profile_installs(h: Harness, f: dict, adapter: ControlAdapter,
                        contract: FrozenControlContract, profile_file: Path, *, contract_file: Path, create_typed) -> dict:
    inputs = cases(profile_file)
    by_id = {case["id"]: case for case in inputs}
    result = {"profile_inputs": [], "binding_preservation": None}

    def invoke(case_id: str, rejection: str | None = None):
        case = by_id[case_id]
        path = h.private / "profile-inputs" / (case_id + ".json")
        private_json(path, case["input"])
        values = {"scope_id": f["scope_id"], "domain_id": f["domain_id"], "profile_file": path,
                  "contract_file": contract_file, "reason": "Independent synthetic A1 acceptance", "operator": "codex-independent-acceptance"}
        return contract.invoke(adapter, "install_profile", case_id, values, rejection=rejection)

    invoke("p1_exact")
    p1 = by_id["p1_exact"]["input"]
    stored = adapter.installed_profile(f, p1["profile_id"], p1["revision"])
    assert stored["canonical_hash"] == P1_HASH
    assert stored["content"] == p1, "installed profile is not the exact supplied content"
    assert stored["record_origin"] == "synthetic" and stored["experimental"] is True
    assert stored["content"]["corporate_approved"] is False
    result["profile_inputs"].append({"id": "p1_exact", "passed": True})
    before = h.snapshot(f)
    invoke("p1_exact")
    after = h.snapshot(f)
    changed = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
    assert changed <= {"gov_protocol_control_events"}, "identical installation changed installed content or business state"
    assert adapter.installed_profile(f, p1["profile_id"], p1["revision"]) == stored
    if changed:
        assert after["tables"]["gov_protocol_control_events"]["row_count"] == before["tables"]["gov_protocol_control_events"]["row_count"] + 1
        events = h.sql(f, "SELECT * FROM gov_protocol_control_events WHERE scope_id=%s ORDER BY recorded_at DESC, event_id DESC LIMIT 1", (f["scope_id"],))
        assert events[0]["event_type"] == "install_profile_noop"
    result["profile_inputs"].append({"id": "p1_idempotent", "passed": True, "noop_audit_allowed": bool(changed)})

    # Establish real P1 bindings before P2/P3 exist. Later installs must not
    # retroactively alter N1 or select a new interpretation for it.
    typed = create_typed(p1["profile_id"], p1["revision"])
    bindings_before = {kind: adapter.binding(f, ref["object_id"]) for kind, ref in typed["types"].items()}
    for name in ("p2_label_revision", "p3_meaning_revision"):
        invoke(name)
        core = by_id[name]["input"]
        installed = adapter.installed_profile(f, core["profile_id"], core["revision"])
        assert installed["content"] == core and installed["canonical_hash"] == core["canonical_hash"]
        assert {kind: adapter.binding(f, ref["object_id"]) for kind, ref in typed["types"].items()} == bindings_before
        result["profile_inputs"].append({"id": name, "passed": True})
    result["binding_preservation"] = {"count": len(bindings_before), "sha256": digest(bindings_before), "passed": True}

    for case in inputs:
        if case["id"] in {"p1_exact", "p2_label_revision", "p3_meaning_revision"}:
            continue
        before = h.snapshot(f)
        invoke(case["id"], rejection=case["id"])
        assert h.snapshot(f) == before, f"rejected profile input changed SQL state: {case['id']}"
        result["profile_inputs"].append({"id": case["id"], "passed": True})
    assert len(result["profile_inputs"]) == len(inputs) + 1
    # Preserve duplicate raw JSON keys all the way to the real CLI parser.
    # A dict fixture would erase the exact defect before the implementation
    # sees it. The final value is still P1 so permissive json.loads would
    # incorrectly accept the original canonical hash/idempotent record.
    duplicate = json.dumps(p1, ensure_ascii=False, indent=2).replace(
        '"display_name":', '"display_name": "DUPLICATE-KEY-MUST-NOT-BE-IGNORED",\n  "display_name":', 1)
    assert json.loads(duplicate) == p1 and duplicate.count('"display_name":') == 2
    raw_path = h.private / "profile-inputs" / "duplicate-json-key.json"
    raw_path.write_text(duplicate + "\n")
    raw_path.chmod(0o600)
    before = h.snapshot(f)
    control_values = {"scope_id": f["scope_id"], "domain_id": f["domain_id"], "profile_file": raw_path,
                      "contract_file": contract_file, "reason": "Raw duplicate JSON key rejection", "operator": "codex-independent-acceptance"}
    contract.invoke(adapter, "install_profile", "duplicate-json-key", control_values, rejection="duplicate_json_key")
    assert h.snapshot(f) == before
    result["profile_inputs"].append({"id": "duplicate_json_key", "passed": True, "input_kind": "raw_json_bytes"})
    # Both artifacts agree with each other but disagree with the compiled
    # accepted contract. A caller-controlled reference cannot redefine A1.
    altered_contract = h.private / "profile-inputs" / "substituted-contract.md"
    altered_contract.write_bytes(contract_file.read_bytes() + b"\nIndependent incompatible action meaning.\n")
    altered_contract.chmod(0o600)
    import hashlib
    altered_core = deepcopy(p1)
    altered_core["revision"] = "invalid-independent-coordinated-contract-substitution"
    altered_core["action_contract_ref"]["content_sha256"] = hashlib.sha256(altered_contract.read_bytes()).hexdigest()
    altered_path = h.private / "profile-inputs" / "substituted-contract-profile.json"
    private_json(altered_path, rehash(altered_core))
    before = h.snapshot(f)
    contract.invoke(adapter, "install_profile", "coordinated-contract-substitution", {
        **control_values, "profile_file": altered_path, "contract_file": altered_contract},
        rejection="coordinated_contract_substitution")
    assert h.snapshot(f) == before
    result["profile_inputs"].append({"id": "coordinated_contract_substitution", "passed": True,
        "input_kind": "two_consistent_but_unapproved_artifacts"})
    result["typed_fixture"] = typed
    public_json(h.output / "profile-install-cases.json", {**result, "contract_a1_accepted": False})
    return result


def run_concurrent_installs(h, f, adapter, contract, profile_file, *, contract_file):
    """Exactly one of two different contents under one new identity may win."""
    operation = contract.content.get("operations", {}).get("install_profile")
    conflict = contract.content.get("rejections", {}).get("same_identity_different_content")
    if not operation or not conflict or "success" not in operation:
        raise NotReady("concurrent profile install exit/error contract is not frozen")
    success_exit, conflict_exit = operation["success"]["exit"], conflict["exit"]
    assert success_exit != conflict_exit
    core = cases(profile_file)[0]["input"]
    revision = "independent-concurrent-" + uuid.uuid4().hex
    variants = []
    for n in (1, 2):
        item = deepcopy(core)
        item["revision"] = revision
        item["display_name"] += " concurrent candidate " + str(n)
        variants.append(rehash(item))
    def job(n):
        path = h.private / "profile-inputs" / ("concurrent-" + str(n) + ".json")
        private_json(path, variants[n])
        values = {"scope_id": f["scope_id"], "domain_id": f["domain_id"], "profile_file": path,
                  "contract_file": contract_file, "operator": "codex-independent-acceptance", "reason": "Concurrent synthetic profile registration"}
        argv = [arg.format_map({key: str(value) for key, value in values.items()}) for arg in operation["args"]]
        return {"case_id": "concurrent-profile-" + str(n), "arguments": argv, "expected_exit": (success_exit, conflict_exit)}
    before = h.snapshot(f)
    from .cli_concurrency import run_pair
    outcomes, window = run_pair(h, adapter, [job(n) for n in (0, 1)], label="same-scope-profile")
    assert sorted(row["exit_code"] for row in outcomes) == sorted([success_exit, conflict_exit])
    loser = next(row for row in outcomes if row["exit_code"] == conflict_exit)
    assert loser["response"]["error"]["code"] == conflict["code"]
    stored = adapter.installed_profile(f, core["profile_id"], revision)
    assert stored["content"] in variants and stored["canonical_hash"] == stored["content"]["canonical_hash"]
    after = h.snapshot(f)
    assert set(before["tables"]) == set(after["tables"])
    changed = {name for name in before["tables"] if before["tables"][name] != after["tables"][name]}
    assert changed == {"gov_method_profile_revisions", "gov_protocol_control_events"}
    for name in changed:
        assert after["tables"][name]["row_count"] == before["tables"][name]["row_count"] + 1
    result = {"passed": True, "profile_id": core["profile_id"], "revision": revision,
              "winning_hash": stored["canonical_hash"], "loser_code": conflict["code"],
              "before_sha256": digest(before), "after_sha256": digest(after), "concurrency_window": window}
    public_json(h.output / "concurrent-profile-installs.json", result)
    return result


def run_cross_scope_concurrent_installs(h, f, adapter, contract, profile_file, *, contract_file):
    """Two real owner CLI processes must serialize one global profile identity."""
    from .binding_cases import _owner
    scopes = [str(uuid.uuid4()), str(uuid.uuid4())]
    with _owner(h, f) as conn:
        for scope in scopes:
            conn.execute("SELECT set_config('app.governed_scope_id', %s, true)", (scope,))
            conn.execute("INSERT INTO gov_scopes(scope_id,tenant_id,company_id) VALUES(%s,%s,%s)",
                (scope, "a1-independent-cross-scope-" + scope, "synthetic-profile-install-only"))
    operation = contract.content["operations"]["install_profile"]
    conflict = contract.content["rejections"]["same_identity_different_content"]
    core = cases(profile_file)[0]["input"]
    revision = "independent-cross-scope-concurrent-" + uuid.uuid4().hex
    variants = []
    for n in (0, 1):
        item = deepcopy(core)
        item["revision"] = revision
        item["display_name"] += " independent scope candidate " + str(n)
        variants.append(rehash(item))
    def job(n):
        path = h.private / "profile-inputs" / ("cross-scope-concurrent-" + str(n) + ".json")
        private_json(path, variants[n])
        values = {"scope_id": scopes[n], "profile_file": path, "contract_file": contract_file,
                  "operator": "codex-independent-acceptance", "reason": "Concurrent global profile identity acceptance"}
        argv = [arg.format_map({key: str(value) for key, value in values.items()}) for arg in operation["args"]]
        return {"case_id": "cross-scope-concurrent-profile-" + str(n), "arguments": argv,
                "expected_exit": (operation["success"]["exit"], conflict["exit"])}
    from .cli_concurrency import run_pair
    outcomes, window = run_pair(h, adapter, [job(n) for n in (0, 1)], label="cross-scope-profile")
    assert sorted(row["exit_code"] for row in outcomes) == [0, 2], "different scopes committed contradictory global Profile meanings"
    loser = next(row for row in outcomes if row["exit_code"] == conflict["exit"])
    assert loser["response"]["error"]["code"] == conflict["code"]
    stored = []
    audits = []
    for scope in scopes:
        fixture = {**f, "scope_id": scope}
        stored.extend(h.sql(fixture, "SELECT canonical_hash,content FROM gov_method_profile_revisions WHERE scope_id=%s AND profile_id=%s AND revision=%s", (scope, core["profile_id"], revision)))
        audits.extend(h.sql(fixture, "SELECT event_type FROM gov_protocol_control_events WHERE scope_id=%s", (scope,)))
        assert h.sql(fixture, "SELECT count(*) AS n FROM gov_objects WHERE scope_id=%s", (scope,))[0]["n"] == 0
        assert h.sql(fixture, "SELECT count(*) AS n FROM gov_object_protocol_bindings WHERE scope_id=%s", (scope,))[0]["n"] == 0
    assert len(stored) == 1 and stored[0]["content"] in variants
    assert len(audits) == 1 and audits[0]["event_type"] == "install_profile"
    result = {"passed": True, "scopes": scopes, "revision": revision, "stored_hashes": [row["canonical_hash"] for row in stored],
              "real_cli_processes": 2, "business_objects_created": 0, "loser_code": conflict["code"], "concurrency_window": window}
    public_json(h.output / "cross-scope-concurrent-profile-installs.json", result)
    return result
