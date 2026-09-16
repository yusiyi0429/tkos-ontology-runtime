#!/usr/bin/env python3
"""Fail closed when the compiled dashboard assets are stale, missing or inconsistent.

The build manifest (``asset-manifest.json`` in the compiled output) records the
SHA256 of every frontend input (source/config/lock) and every emitted asset.
Checks:

* source tree: emitted assets and the HTML references are present, no external
  runtime assets, and the manifest's input hashes still match the frontend
  source — editing ``workbench/dashboard`` without rebuilding fails;
* built wheel/sdist: the archive carries the manifest and identical emitted
  assets (checked by hash, not just by presence);
* this script is also invoked from a Hatch build hook so a stale package build
  fails before an image can ship a blank page.

    python3 scripts/verify_dashboard_assets.py                 # source tree
    python3 scripts/verify_dashboard_assets.py --write-manifest # after npm build
    python3 scripts/verify_dashboard_assets.py --archive dist/*.whl --skip-source
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "workbench" / "dashboard"
DIST = ROOT / "src" / "memory_service_app" / "dashboard_dist"
MANIFEST_NAME = "asset-manifest.json"
MANIFEST_SCHEMA = "tkos.dashboard-assets/1"
REQUIRED_ENTRY = "memory_service_app/dashboard_dist/" + MANIFEST_NAME
ASSET_REFERENCE = re.compile(r"(?:src|href)=\"(/dashboard/assets/[^\"]+)\"")
EXTERNAL_RUNTIME = re.compile(
    r"(?:src|href)=\"(?:https?:)?//[^\"]+\"|<link[^>]+href=\"(?:https?:)?//[^\"]+\"")
INPUT_EXCLUDES = {"node_modules", "dist", ".vite", "coverage", ".DS_Store", "__pycache__"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def collect_inputs() -> list[dict]:
    records = []
    for path in sorted(FRONTEND.rglob("*")):
        if not path.is_file() or path.suffix == ".tsbuildinfo":
            continue
        if any(part in INPUT_EXCLUDES for part in path.relative_to(FRONTEND).parts):
            continue
        records.append({"path": _relative(path, ROOT), "sha256": sha256(path)})
    if not records:
        raise SystemExit("DASHBOARD_INPUTS_MISSING: workbench/dashboard is empty")
    return records


def collect_outputs(dist: Path | None = None) -> list[dict]:
    dist = dist or DIST
    records = []
    for path in sorted(dist.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        records.append({"path": _relative(path, dist), "sha256": sha256(path),
                        "bytes": path.stat().st_size})
    return records


def write_manifest(dist: Path | None = None) -> dict:
    dist = dist or DIST
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "generator": "scripts/build_dashboard.sh",
        "inputs": collect_inputs(),
        "outputs": collect_outputs(dist),
    }
    (dist / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _verify_html(dist: Path) -> list[str]:
    index = dist / "index.html"
    if not index.is_file():
        raise SystemExit("DASHBOARD_ASSETS_MISSING: index.html is not built")
    html = index.read_text(encoding="utf-8")
    if '<div id="root">' not in html:
        raise SystemExit("DASHBOARD_ASSETS_STALE: index.html has no React root")
    references = ASSET_REFERENCE.findall(html)
    if not references:
        raise SystemExit("DASHBOARD_ASSETS_STALE: index.html references no compiled assets")
    for reference in references:
        if not (dist / reference.removeprefix("/dashboard/")).is_file():
            raise SystemExit(f"DASHBOARD_ASSETS_MISSING: {reference} is not present")
    if EXTERNAL_RUNTIME.search(html):
        raise SystemExit("DASHBOARD_EXTERNAL_ASSET: the page must not load external runtime assets")
    for path in [*dist.rglob("*.js"), *dist.rglob("*.css")]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"sourceMappingURL=.*https?://", text):
            raise SystemExit("DASHBOARD_EXTERNAL_ASSET: a bundle references an external source map")
    return references


def verify_source_tree() -> dict:
    manifest_path = DIST / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SystemExit("DASHBOARD_MANIFEST_MISSING: run scripts/build_dashboard.sh")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise SystemExit("DASHBOARD_MANIFEST_SCHEMA: unsupported asset manifest")
    current_inputs = collect_inputs()
    if manifest.get("inputs") != current_inputs:
        raise SystemExit("DASHBOARD_ASSETS_STALE: frontend source changed without a rebuild")
    current_outputs = collect_outputs()
    if manifest.get("outputs") != current_outputs:
        raise SystemExit("DASHBOARD_ASSETS_STALE: emitted assets do not match the build manifest")
    references = _verify_html(DIST)
    if not any(item["path"].endswith(".js") for item in current_outputs) or \
            not any(item["path"].endswith(".css") for item in current_outputs):
        raise SystemExit("DASHBOARD_ASSETS_MISSING: compiled js/css assets are absent")
    return {"manifest": str(manifest_path.relative_to(ROOT)), "inputs": len(current_inputs),
            "outputs": len(current_outputs), "references": references}


def _archive_manifest(archive: Path) -> tuple[dict, dict[str, bytes]]:
    files: dict[str, bytes] = {}
    if archive.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(archive) as handle:
            for name in handle.namelist():
                if "/dashboard_dist/" in name and not name.endswith("/"):
                    files[name.split("/dashboard_dist/", 1)[1]] = handle.read(name)
    else:
        with tarfile.open(archive) as handle:
            for member in handle.getmembers():
                if member.isfile() and "/dashboard_dist/" in member.name:
                    stream = handle.extractfile(member)
                    if stream is not None:
                        files[member.name.split("/dashboard_dist/", 1)[1]] = stream.read()
    if MANIFEST_NAME not in files:
        raise SystemExit(f"DASHBOARD_MANIFEST_MISSING: {archive.name} carries no asset manifest")
    manifest = json.loads(files[MANIFEST_NAME].decode("utf-8"))
    return manifest, files


def verify_archive(archive: Path, *, compare_source: bool) -> dict:
    manifest, files = _archive_manifest(archive)
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise SystemExit(f"DASHBOARD_MANIFEST_SCHEMA: {archive.name} has an unsupported manifest")
    recorded = {item["path"]: item for item in manifest.get("outputs", [])}
    for name, digest in recorded.items():
        if name not in files:
            raise SystemExit(f"DASHBOARD_ASSETS_MISSING: {archive.name} lacks {name}")
        if hashlib.sha256(files[name]).hexdigest() != digest["sha256"]:
            raise SystemExit(f"DASHBOARD_ASSETS_STALE: {archive.name}:{name} hash mismatch")
    extras = set(files) - set(recorded) - {MANIFEST_NAME}
    if extras:
        raise SystemExit(f"DASHBOARD_ASSETS_STALE: {archive.name} has unrecorded assets: {sorted(extras)}")
    if compare_source:
        local = json.loads((DIST / MANIFEST_NAME).read_text(encoding="utf-8"))
        if local.get("outputs") != manifest.get("outputs"):
            raise SystemExit(f"DASHBOARD_ASSETS_STALE: {archive.name} differs from the local build")
    return {"archive": archive.name, "assets": len(recorded)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="store_true",
                        help="record the current frontend inputs and emitted assets")
    parser.add_argument("--archive", type=Path, action="append", default=[],
                        help="built wheel/sdist to inspect")
    parser.add_argument("--skip-source", action="store_true",
                        help="skip the local source-tree check (archive-only use)")
    args = parser.parse_args()
    if args.write_manifest:
        manifest = write_manifest()
        print(f"dashboard asset manifest written: {len(manifest['inputs'])} inputs, "
              f"{len(manifest['outputs'])} outputs")
        return
    if not args.skip_source:
        result = verify_source_tree()
    else:
        result = {"source_tree": "skipped"}
    for archive in args.archive:
        result.update(verify_archive(archive, compare_source=not args.skip_source))
    print(f"dashboard assets ok: {result}")


if __name__ == "__main__":
    sys.exit(main())
