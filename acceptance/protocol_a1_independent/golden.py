"""Generate request hashes with frozen pre-A1 source; compare current models.

This is a serialization gate only. It never connects to a database or implies
that HTTP replay, migrations, authority or A1 acceptance have passed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_OLD_SRC = Path("/tmp/tkos-a1-legacy-3cd9109d/src")
GOLDEN = HERE / "legacy-request-golden.json"


def uid(value: int) -> str:
    return str(UUID(int=value))


def vectors() -> list[dict]:
    ref = {"object_id": uid(11), "revision_id": uid(12)}
    commitment = {"title": "Synthetic commitment", "terms": {"target": 3, "label": "中文"},
                  "required_assignment_ids": [uid(1), uid(2)], "upstream_refs": [ref]}
    change = {"object_id": uid(11), "from_revision_id": uid(12), "to_revision_id": uid(13)}
    work = {"title": "Synthetic work", "execution_commitment_ref": ref,
            "dri_assignment_id": uid(1), "acceptor_assignment_id": uid(2),
            "acceptance_criteria": [{"criterion_id": "c1", "description": "Exact synthetic criterion"}]}
    creations = {
        "CompanyOutcome": {"title": "最小结果"},
        "BusinessCommitment": commitment,
        "ExecutionCommitment": commitment,
        "FeedbackThread": {"title": "Synthetic feedback", "description": "Need a decision"},
        "ManagementAdjustment": {"title": "Synthetic adjustment", "feedback_revision_id": uid(15),
                                 "decision_revision_id": uid(16), "changes": [change]},
        "Decision": {"title": "Synthetic decision", "statement": "Proceed with the proposal"},
        "MetricObservation": {"title": "Synthetic observation", "metric_id": "clients", "value": 3,
                              "unit": "client", "valid_from": "2026-09-10T08:00:00+08:00"},
        "WorkItem": work,
    }
    samples: list[tuple[str, str, dict]] = []
    for kind, payload in creations.items():
        samples.append((f"create_{kind}_defaults", "create_object",
                        {"object_type": kind, "domain_id": uid(3), "payload": deepcopy(payload)}))
    samples += [
        ("propose_outcome_defaults", "propose_revision", {"payload": {"title": "New terms"}}),
        ("accept_commitment", "accept_commitment", {"party_assignment_id": uid(1),
         "understanding": "I accept these exact synthetic terms.", "accepted_terms_hash": "a" * 64}),
        ("activate_commitment", "activate_commitment", {"handshake_record_ids": [uid(4), uid(5)],
         "activation_policy_revision_id": uid(6)}),
        ("confirm_adjustment", "confirm_adjustment", {"decision_revision_id": uid(16),
         "feedback_revision_id": uid(15), "changes": [change]}),
        ("confirm_closure", "confirm_closure", {"acceptance_record_id": uid(20),
         "resolution_decision_revision_id": uid(16), "closure_evidence_revision_ids": [uid(21)],
         "disposition": "resolved", "closure_note": "Independent evidence confirms resolution."}),
        ("route_feedback", "route_feedback", {"responsible_assignment_id": uid(1)}),
        ("record_acceptance", "record_acceptance", {"decision_revision_id": uid(16),
         "evidence_revision_ids": [uid(21)], "verification_result": "changes_requested"}),
        ("request_feedback_acceptance", "request_feedback_acceptance", {"decision_revision_id": uid(16)}),
        ("revoke_assignment", "revoke_assignment", {"assignment_id": uid(1)}),
        ("submit_v1_defaults", "submit_deliverable", {"title": "Delivery v1", "summary": "First version",
         "evidence_revision_ids": [uid(21)]}),
        ("submit_v2_explicit", "submit_deliverable", {"title": "Delivery v2", "summary": "Revised version",
         "evidence_revision_ids": [uid(21)], "responds_to_acceptance_id": uid(22)}),
        ("review_deliverable", "review_deliverable", {"deliverable_revision_id": uid(23),
         "delivery_payload_hash": "b" * 64, "verification_result": "accepted",
         "criterion_results": [{"criterion_id": "c1", "result": "passed", "note": "Source verified"}],
         "review_note": "Independent exact version review"}),
        ("record_outcome_defaults", "record_outcome_assessment", {"assessment_result": "not_achieved",
         "observation_revision_ids": [uid(24)], "evidence_revision_ids": [uid(21)],
         "assessment_note": "Delivery accepted but only two customers activated"}),
    ]
    for kind in ("accept_feedback", "investigate_feedback", "confirm_decision", "confirm_outcome",
                 "reopen_feedback", "accept_work_item"):
        samples.append((kind, kind, {}))
    result = []
    for i, (name, kind, params) in enumerate(samples):
        result.append({"id": name, "request": {
            "action_type": kind,
            "target": None if kind in {"create_object", "revoke_assignment"} else
                {"object_id": uid(100), "revision_id": uid(101), "expected_version": 2},
            "expected_versions": [{"object_id": uid(11), "expected_version": 3}],
            "idempotency_key": f"a1-independent-golden-{i:03d}",
            "reason": "Independent legacy serialization golden",
            "params": params,
        }})
    by_id = {x["id"]: x for x in result}
    additions = [
        ("create_CompanyOutcome_defaults", "explicit_nullable_create", ("params", "valid_from"), None),
        ("propose_outcome_defaults", "explicit_nullable_bundle", ("params", "bundle_id"), None),
        ("submit_v1_defaults", "explicit_nullable_response", ("params", "responds_to_acceptance_id"), None),
        ("record_outcome_defaults", "explicit_empty_delivery_acceptances", ("params", "delivery_acceptance_ids"), []),
        ("create_WorkItem_defaults", "work_due_timezone", ("params", "payload", "due_at"), "2026-09-10T18:00:00+08:00"),
        ("create_CompanyOutcome_defaults", "outcome_explicit_empty_terms", ("params", "payload", "terms"), {}),
        ("create_MetricObservation_defaults", "observation_float", ("params", "payload", "value"), 3.0),
        ("create_CompanyOutcome_defaults", "meaningful_whitespace", ("params", "payload", "title"), "  合成结果  "),
    ]
    for source, name, path, value in additions:
        item = deepcopy(by_id[source])
        item["id"] = name
        destination = item["request"]
        for key in path[:-1]:
            destination = destination[key]
        destination[path[-1]] = value
        result.append(item)
    return result


PROBE = r'''
import hashlib, json, pathlib, sys
src = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(src))
from memory_service_runtime.governed import models
assert pathlib.Path(models.__file__).resolve().is_relative_to(src), "wrong model source"
rows = json.load(sys.stdin)
out = []
for row in rows:
    parsed = models.ActionRequest.model_validate(row["request"])
    payload = parsed.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    out.append({"id": row["id"], "canonical_payload": payload,
                "canonical_utf8_hex": canonical.encode().hex(),
                "request_hash": hashlib.sha256(canonical.encode()).hexdigest()})
print(json.dumps({"model_source": str(pathlib.Path(models.__file__).resolve()), "rows": out}, ensure_ascii=False))
'''


def probe(source: Path, rows: list[dict]) -> dict:
    environment = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "HOME") if k in os.environ}
    completed = subprocess.run([sys.executable, "-I", "-c", PROBE, str(source)],
                               input=json.dumps(rows, ensure_ascii=False), text=True,
                               capture_output=True, env=environment, timeout=30, check=True)
    return json.loads(completed.stdout)


def generate(old_source: Path, output: Path) -> dict:
    # Refuse the live source as an oracle even if its content happens to match.
    old_source = old_source.resolve()
    if old_source == (ROOT / "src").resolve():
        raise ValueError("golden generation requires separately exported pre-A1 source")
    rows = vectors()
    expected = probe(old_source, rows)
    for row, outcome in zip(rows, expected["rows"], strict=True):
        assert row["id"] == outcome["id"]
        row["expected"] = {k: v for k, v in outcome.items() if k != "id"}
    source_files = {}
    for rel in ("memory_service_runtime/governed/models.py", "memory_service_runtime/governed/service.py"):
        path = old_source / rel
        source_files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    document = {"format": "tkos.a1.legacy-request-golden/1", "legacy_git_head":
                "3cd9109d726a9a9069a7960a2f2665ce677785d2", "source_files_sha256": source_files,
                "generated_by": "isolated subprocess importing exported pre-A1 models only",
                "canonicalization": "existing service json.dumps(sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)",
                "cases": rows}
    output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"generated": len(rows), "output": str(output), "database_access": False}


def check(source: Path, golden: Path, *, report: Path | None = None) -> dict:
    document = json.loads(golden.read_text(encoding="utf-8"))
    expected = document["cases"]
    actual = probe(source, expected)
    checks = []
    for before, after in zip(expected, actual["rows"], strict=True):
        comparisons = {field: before["expected"][field] == after[field]
                       for field in ("canonical_payload", "canonical_utf8_hex", "request_hash")}
        checks.append({"id": before["id"], "passed": all(comparisons.values()), "checks": comparisons})
    result = {"gate": "legacy_serialization_only", "model_source": actual["model_source"],
              "total": len(checks), "passed": sum(c["passed"] for c in checks),
              "failed": sum(not c["passed"] for c in checks), "checks": checks,
              "database_access": False, "contract_a1_accepted": False}
    if report:
        report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--old-source", type=Path, default=DEFAULT_OLD_SRC)
    gen.add_argument("--out", type=Path, default=GOLDEN)
    verify = sub.add_parser("check")
    verify.add_argument("--source", type=Path, default=ROOT / "src")
    verify.add_argument("--golden", type=Path, default=GOLDEN)
    verify.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.mode == "generate":
        result = generate(args.old_source, args.out)
    else:
        result = check(args.source, args.golden, report=args.report)
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, ensure_ascii=False))
    if result.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
