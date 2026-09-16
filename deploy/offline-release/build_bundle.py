#!/usr/bin/env python3
"""Create one checksummed five-image offline bundle from a verified image set."""
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
import tempfile


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
COMPONENTS = {
    "runtime_api", "runtime_worker", "postgres_pgvector", "minio_server", "minio_client"
}
DEPLOY_FILES = {
    ".env.example", "README.md", "compose.yaml", "db_admin.py", "start-offline.sh",
    "verify_bundle.py", "images.json",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(argv: list[str], *, cwd: Path = ROOT, stdout=None) -> str:
    result = subprocess.run(argv, cwd=cwd, text=stdout is None,
                            stdout=stdout or subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f"COMMAND_FAILED:{argv[0]}:{result.returncode}")
    return result.stdout.strip() if stdout is None else ""


def validate_image_set(path: Path, release: str, source_ref: str, arch: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema_version"), data.get("release"), data.get("source_ref"),
            data.get("os"), data.get("architecture")) != (
            "tkos.offline-image-set/1", release, source_ref, "linux", arch):
        raise RuntimeError("IMAGE_SET_HEADER_MISMATCH")
    if set(data.get("images", {})) != COMPONENTS:
        raise RuntimeError("IMAGE_SET_COMPONENTS_MISMATCH")
    tags: set[str] = set()
    for component, recorded in data["images"].items():
        tag = recorded.get("tag", "")
        if "clark" in component.lower() or "clark" in tag.lower() or tag in tags:
            raise RuntimeError("IMAGE_SET_TAG_REJECTED")
        item = json.loads(run(["docker", "image", "inspect", tag]))[0]
        if item.get("Id") != recorded.get("id") or item.get("Os") != "linux" or item.get("Architecture") != arch:
            raise RuntimeError(f"IMAGE_SET_LOCAL_MISMATCH:{component}")
        tags.add(tag)
    return data


def gzip_file(source: Path, target: Path) -> None:
    with source.open("rb") as incoming, target.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)


def normalized_archive(source: Path, target: Path, top_name: str) -> None:
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in [source, *sorted(source.rglob("*"))]:
                    relative = Path(top_name) if path == source else Path(top_name) / path.relative_to(source)
                    info = archive.gettarinfo(str(path), arcname=str(relative))
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    if path.is_file():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    elif path.is_dir():
                        archive.addfile(info)
                    else:
                        raise RuntimeError("UNSUPPORTED_BUNDLE_ENTRY")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--image-set", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if len(args.source_ref) != 40 or any(c not in "0123456789abcdef" for c in args.source_ref):
        raise RuntimeError("SOURCE_REF_MUST_BE_FULL_COMMIT")
    if run(["git", "rev-parse", args.source_ref]) != args.source_ref:
        raise RuntimeError("SOURCE_REF_NOT_FOUND")
    critical = ["Dockerfile", "pyproject.toml", "uv.lock", "src", "deploy/offline-release"]
    dirty = subprocess.run(["git", "diff", "--quiet", args.source_ref, "--", *critical], cwd=ROOT)
    if dirty.returncode:
        raise RuntimeError("RELEASE_INPUTS_DIFFER_FROM_SOURCE_REF")
    image_set = validate_image_set(args.image_set, args.release, args.source_ref, args.arch)

    stem = f"tkos-ontology-runtime-{args.release}-linux-{args.arch}"
    output_dir = args.output_dir.resolve()
    expanded = output_dir / stem
    package = output_dir / f"{stem}.tar.gz"
    if expanded.exists() or package.exists():
        raise RuntimeError("BUNDLE_OUTPUT_ALREADY_EXISTS")
    output_dir.mkdir(parents=True, exist_ok=True)
    expanded.mkdir(mode=0o700)
    deploy = expanded / "deploy" / "offline-release"
    deploy.mkdir(parents=True)

    for name in sorted(DEPLOY_FILES):
        source = HERE / name
        if not source.is_file():
            raise RuntimeError(f"DEPLOY_FILE_MISSING:{name}")
        shutil.copy2(source, deploy / name)
    env_path = deploy / ".env.example"
    rendered = env_path.read_text(encoding="utf-8").replace("__RELEASE__", args.release).replace("__ARCH__", args.arch)
    if "__RELEASE__" in rendered or "__ARCH__" in rendered:
        raise RuntimeError("ENV_TEMPLATE_NOT_RENDERED")
    env_path.write_text(rendered, encoding="utf-8")

    with tempfile.TemporaryDirectory(dir=output_dir) as temporary:
        temporary_path = Path(temporary)
        source_tar = temporary_path / "source.tar"
        run(["git", "archive", "--format=tar", f"--output={source_tar}", args.source_ref])
        gzip_file(source_tar, expanded / "runtime-source.tar.gz")
        images_tar = temporary_path / "images.tar"
        tags = [image_set["images"][name]["tag"] for name in sorted(COMPONENTS)]
        with images_tar.open("wb") as stream:
            run(["docker", "save", *tags], stdout=stream)
        gzip_file(images_tar, expanded / "images.tar.gz")

    payload_files = {
        str(path.relative_to(expanded)): {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(expanded.rglob("*")) if path.is_file()
    }
    manifest = {
        "schema_version": "tkos.offline-bundle/1",
        "release": args.release,
        "source_ref": args.source_ref,
        "deployment_source_ref": run(["git", "rev-parse", "HEAD"], cwd=HERE),
        "os": "linux",
        "architecture": args.arch,
        "component_count": len(COMPONENTS),
        "components": image_set["images"],
        "files": payload_files,
        "contains_clark": False,
        "contains_secrets": False,
        "contains_database_dump": False,
        "production_deployed": False,
    }
    manifest_path = expanded / "bundle-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums_path = expanded / "SHA256SUMS"
    all_files = [path for path in sorted(expanded.rglob("*")) if path.is_file() and path != sums_path]
    sums_path.write_text("".join(
        f"{sha256(path)}  {path.relative_to(expanded)}\n" for path in all_files
    ), encoding="utf-8")
    normalized_archive(expanded, package, stem)
    checksum = output_dir / f"{stem}.tar.gz.sha256"
    checksum.write_text(f"{sha256(package)}  {package.name}\n", encoding="utf-8")
    release_manifest = output_dir / f"release-manifest-{args.arch}.json"
    shutil.copy2(manifest_path, release_manifest)
    print(json.dumps({"ok": True, "package": str(package), "sha256": sha256(package),
                      "bytes": package.stat().st_size, "architecture": args.arch,
                      "components": len(COMPONENTS)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
