#!/usr/bin/env python3
"""Build a checksummed, secret-free linux/amd64 offline production bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


REPO = Path(__file__).resolve().parents[2]
CLARK = REPO.parent / "clark"


def command(argv: list[str], *, stdout=None) -> str:
    result = subprocess.run(argv, cwd=REPO, text=stdout is None, stdout=stdout or subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError("BUNDLE_COMMAND_FAILED:" + argv[0])
    return result.stdout if stdout is None else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--runtime-ref", required=True)
    parser.add_argument("--clark-ref", required=True)
    parser.add_argument("--image", action="append", required=True)
    args = parser.parse_args()
    output_root = REPO / ".runtime-acceptance" / "production-bundles"
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = output_root / args.release_id
    if output.exists():
        raise RuntimeError("BUNDLE_OUTPUT_ALREADY_EXISTS")
    output.mkdir(mode=0o700)
    deployment = output / "deploy" / "remote-production"
    shutil.copytree(Path(__file__).resolve().parent, deployment,
                    ignore=shutil.ignore_patterns("__pycache__", ".env", "private",
                                                  "restore-complete", "deployment-state.json"))

    images = []
    for tag in args.image:
        raw = command(["docker", "image", "inspect", tag])
        item = json.loads(raw)[0]
        if item["Architecture"] != "amd64" or item["Os"] != "linux":
            raise RuntimeError("IMAGE_PLATFORM_REJECTED:" + tag)
        images.append({"tag": tag, "id": item["Id"], "architecture": "amd64", "os": "linux"})
    image_archive = output / "images.tar.gz"
    with image_archive.open("wb") as raw_stream:
        with gzip.GzipFile(fileobj=raw_stream, mode="wb", compresslevel=6, mtime=0) as compressed:
            proc = subprocess.Popen(["docker", "save", *args.image], cwd=REPO,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert proc.stdout is not None
            shutil.copyfileobj(proc.stdout, compressed, length=1024 * 1024)
            stderr = proc.stderr.read() if proc.stderr else b""
            if proc.wait() != 0:
                raise RuntimeError("DOCKER_SAVE_FAILED")
            if stderr:
                raise RuntimeError("DOCKER_SAVE_UNEXPECTED_STDERR")
    os.chmod(image_archive, 0o600)

    runtime_source = output / "runtime-source.tar.gz"
    clark_source = output / "clark-source.tar.gz"
    command(["git", "archive", "--format=tar.gz", f"--output={runtime_source}", args.runtime_ref])
    command(["git", "-C", str(CLARK), "archive", "--format=tar.gz",
             f"--output={clark_source}", args.clark_ref])
    manifest = {
        "schema_version": "tkos-offline-production-bundle.v1",
        "release_id": args.release_id,
        "runtime_ref": args.runtime_ref,
        "clark_ref": args.clark_ref,
        "images": images,
        "files": {},
        "contains_secrets": False,
        "contains_database_dump": False,
    }
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "bundle-manifest.json":
            manifest["files"][str(path.relative_to(output))] = {
                "bytes": path.stat().st_size, "sha256": sha256(path)
            }
    manifest_path = output / "bundle-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    sums = output / "SHA256SUMS"
    sums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in
                             (manifest_path, image_archive, runtime_source, clark_source)),
                    encoding="utf-8")
    os.chmod(sums, 0o600)

    package = output_root / f"{args.release_id}.tar.gz"
    with tarfile.open(package, "w:gz") as archive:
        archive.add(output, arcname=args.release_id)
    os.chmod(package, 0o600)
    checksum = output_root / f"{args.release_id}.tar.gz.sha256"
    checksum.write_text(f"{sha256(package)}  {package.name}\n", encoding="utf-8")
    os.chmod(checksum, 0o600)
    print(json.dumps({"ok": True, "release_id": args.release_id,
                      "package": str(package), "bytes": package.stat().st_size,
                      "sha256": sha256(package), "images": len(images),
                      "contains_secrets": False, "contains_database_dump": False},
                     separators=(",", ":")))


if __name__ == "__main__":
    main()
