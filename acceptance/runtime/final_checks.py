"""Repository regression and reproducible evidence for the final local gate."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import xml.etree.ElementTree as ET

from acceptance.runtime import infra


def run_regression(harness) -> dict:
    output = harness.output
    junit = output / "regression.xml"
    environment = harness.process_environment({
        "DATABASE_URL": harness.env["MIGRATION_DATABASE_URL"],
        "TEST_ADMIN_DATABASE_URL": infra.test_admin_url(),
    })
    command = [harness.python, "-m", "pytest", "tests", "-q", "-rs", f"--junitxml={junit}"]
    result = subprocess.run(command, cwd=infra.ROOT, env=environment, capture_output=True,
                            text=True, timeout=180)
    (output / "regression.log").write_text(infra.redact(result.stdout + result.stderr))
    assert result.returncode == 0, "original regression suite failed; see regression.log"
    cases = ET.parse(junit).getroot().findall(".//testcase")
    assert cases and not any(case.find("failure") is not None or case.find("error") is not None for case in cases)
    skipped = [{"test": case.attrib["name"], "reason": case.find("skipped").attrib.get("message", "")}
               for case in cases if case.find("skipped") is not None]
    executable = shutil.which("uv")
    assert executable, "the existing uv build tool is required for package verification"
    build = subprocess.run([executable, "build", "--no-sources"], cwd=infra.ROOT,
                           capture_output=True, text=True, timeout=180)
    (output / "build.log").write_text(infra.redact(build.stdout + build.stderr))
    assert build.returncode == 0, "source and wheel package build failed; see build.log"
    return {"passed": len(cases) - len(skipped), "skipped": skipped,
            "failed": 0, "source_distribution_and_wheel_built": True}


def save_source_and_receiver(harness) -> dict:
    root = Path(infra.ROOT)
    files = set()
    for directory in ("src", "acceptance/runtime", "tests"):
        files.update(p for p in (root / directory).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    files.update(root / name for name in ("pyproject.toml", "uv.lock", "Dockerfile", ".env.example"))
    records = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(files) if p.is_file()}
    head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, text=True, capture_output=True)
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
    manifest = {"git_head": head.stdout.strip() if head.returncode == 0 else None,
                "git_branch": subprocess.check_output(["git", "branch", "--show-current"], cwd=root, text=True).strip(),
                "working_tree_contains_uncommitted_changes": bool(dirty), "sha256": records}
    (harness.output / "source-manifest.json").write_text(json.dumps(manifest, indent=2))
    ledger = harness.private / "receiver.sqlite"
    ledger_result = None
    if ledger.exists():
        with sqlite3.connect(f"file:{ledger}?mode=ro", uri=True) as source:
            with sqlite3.connect(harness.output / "receiver-ledger.sqlite") as target:
                source.backup(target)
            ledger_result = {"unique_effects": source.execute("SELECT count(*) FROM effects").fetchone()[0],
                             "calls": source.execute("SELECT count(*) FROM calls").fetchone()[0]}
    return {"source_files_hashed": len(records), "receiver_ledger": ledger_result}


def checksum_artifacts(harness) -> None:
    files = sorted(p for p in harness.output.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    (harness.output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files))
