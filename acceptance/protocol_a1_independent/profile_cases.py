"""Independent Profile installation inputs; no production validators imported.

The caller must freeze the maintenance CLI's exact error code/exit contract before
execution. These inputs alone cannot report an installation or A1 PASS.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


P1_HASH = "5050d542cc521991f17932562c4397331c0482b673f9bcd8fe694749bd43fc76"
CONTRACT_HASH = "fff438ca5eb3b2c709d9911929bc0d8d393a27b42f0af62d48767a2f65388fd4"


def canonical_hash(core: dict) -> str:
    body = {key: value for key, value in core.items() if key != "canonical_hash"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def rehash(core: dict) -> dict:
    core["canonical_hash"] = canonical_hash(core)
    return core


def cases(profile_file: Path) -> list[dict]:
    p1 = json.loads(profile_file.read_text())
    assert canonical_hash(p1) == p1["canonical_hash"] == P1_HASH, "the accepted P1 artifact drifted"
    assert p1["action_contract_ref"]["content_sha256"] == CONTRACT_HASH
    result = [{"id": "p1_exact", "expected": "install_exact", "input": p1}]
    p2 = deepcopy(p1)
    p2["revision"] = "0.1.1-independent-label"
    p2["display_name"] += " — independent label revision"
    result.append({"id": "p2_label_revision", "expected": "install_without_rebinding_p1", "input": rehash(p2)})
    p3 = deepcopy(p1)
    p3["revision"] = "0.2.0-independent-semantics"
    p3["rules"]["responsibility_and_acceptance"]["acceptor_mode"] = "independent_named_assessor_v2"
    result.append({"id": "p3_meaning_revision", "expected": "record_without_rebinding_or_enabling_new_business",
                   "input": rehash(p3)})
    conflict = deepcopy(p1)
    conflict["display_name"] += " conflicting bytes under the same identity"
    result.append({"id": "same_identity_different_content", "expected": "PROFILE_CONTENT_CONFLICT_after_p1",
                   "input": rehash(conflict)})
    mutations = []
    for key, id_name in (("sources", "source_id"), ("concepts", "concept_id"),
                         ("source_aliases", "alias_id"), ("pending_decisions", "decision_id")):
        item = deepcopy(p1)
        item[key].append(deepcopy(item[key][0]))
        mutations.append(("duplicate_" + id_name, item))
    dangling_source = deepcopy(p1)
    dangling_source["source_aliases"][0]["source_id"] = "MISSING-INDEPENDENT-SOURCE"
    mutations.append(("dangling_alias_source", dangling_source))
    dangling_concept = deepcopy(p1)
    dangling_concept["source_aliases"][0]["candidate_concept_ids"] = ["urn:tkos:missing-independent-concept"]
    mutations.append(("dangling_alias_concept", dangling_concept))
    for field in ("experimental", "corporate_approved"):
        for value, label in ((1 if field == "experimental" else 0, "integer"),
                             ("true" if field == "experimental" else "false", "string")):
            item = deepcopy(p1)
            item[field] = value
            mutations.append((f"{field}_{label}", item))
    omitted_nullable = deepcopy(p1)
    omitted_nullable["source_aliases"][0].pop("unresolved_note")
    mutations.append(("omitted_required_nullable", omitted_nullable))
    arbitrary = deepcopy(p1)
    arbitrary["actor_id"] = "this-must-not-select-install-authority"
    mutations.append(("arbitrary_actor_field", arbitrary))
    wrong_contract = deepcopy(p1)
    wrong_contract["action_contract_ref"]["content_sha256"] = "0" * 64
    mutations.append(("wrong_action_contract_with_valid_core_hash", wrong_contract))
    bad_schema = deepcopy(p1)
    bad_schema["profile_core_schema_version"] = "tkos.profile-core/unknown"
    mutations.append(("unknown_profile_schema", bad_schema))
    for name, item in mutations:
        # New identity avoids an early same-version content conflict masking a
        # structural/reference defect. Recomputed digest avoids hash-only passes.
        item["revision"] = "invalid-independent-" + name
        result.append({"id": name, "expected": "reject_invalid_profile_before_database_insert", "input": rehash(item)})
    invalid_hash = deepcopy(p1)
    invalid_hash["revision"] = "invalid-independent-hash"
    invalid_hash["canonical_hash"] = "0" * 64
    result.append({"id": "wrong_declared_hash", "expected": "reject_invalid_profile_before_database_insert", "input": invalid_hash})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    index = []
    for case in cases(args.profile):
        path = args.output / (case["id"] + ".json")
        path.write_text(json.dumps(case["input"], ensure_ascii=False, indent=2) + "\n")
        index.append({"id": case["id"], "expected": case["expected"], "input_file": path.name,
                      "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (args.output / "index.json").write_text(json.dumps({"status": "prepared_not_run", "cases": index,
        "installation_accepted": False, "contract_a1_accepted": False}, indent=2) + "\n")
    print(json.dumps({"prepared_cases": len(index), "database_access": False, "contract_a1_accepted": False}))


if __name__ == "__main__":
    main()
