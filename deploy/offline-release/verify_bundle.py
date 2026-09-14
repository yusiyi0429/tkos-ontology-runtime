#!/usr/bin/env python3
"""Verify an extracted or compressed TKOS offline bundle; optionally load images."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile


COMPONENTS = {
    "runtime_api", "runtime_worker", "postgres_pgvector", "minio_server", "minio_client"
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
        raise RuntimeError(f"UNSAFE_ARCHIVE_MEMBER:{member.name}")
    if not (member.isfile() or member.isdir()):
        raise RuntimeError(f"UNSUPPORTED_ARCHIVE_MEMBER:{member.name}")


def extract_bundle(package: Path, destination: Path) -> Path:
    with tarfile.open(package, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise RuntimeError("EMPTY_BUNDLE")
        for member in members:
            validate_member(member)
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise RuntimeError("BUNDLE_ROOT_MISMATCH")
        # validate_member has already rejected traversal, links, and special files.
        # Avoid Python 3.12-only extraction filters on older offline hosts.
        archive.extractall(destination, members=members)
    return destination / roots.pop()


def parse_sums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if separator != "  " or len(digest) != 64 or not name or name in result:
            raise RuntimeError("SHA256SUMS_INVALID")
        result[name] = digest
    return result


def docker_archive_platforms(path: Path) -> dict[str, tuple[str, str]]:
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            validate_member(member)
        manifest_stream = archive.extractfile("manifest.json")
        if manifest_stream is None:
            raise RuntimeError("DOCKER_MANIFEST_MISSING")
        manifest = json.load(manifest_stream)
        result: dict[str, tuple[str, str]] = {}
        for item in manifest:
            config_stream = archive.extractfile(item["Config"])
            if config_stream is None:
                raise RuntimeError("DOCKER_CONFIG_MISSING")
            config = json.load(config_stream)
            for tag in item.get("RepoTags") or []:
                if tag in result:
                    raise RuntimeError("DOCKER_TAG_DUPLICATED")
                result[tag] = (config.get("os", ""), config.get("architecture", ""))
    return result


def run(argv: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"COMMAND_FAILED:{argv[0]}:{result.returncode}")
    return result.stdout.strip()


def verify(root: Path, load: bool) -> dict:
    manifest_path = root / "bundle-manifest.json"
    sums_path = root / "SHA256SUMS"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    arch = manifest.get("architecture")
    if (manifest.get("schema_version"), manifest.get("os"), arch,
            manifest.get("component_count"), manifest.get("contains_clark"),
            manifest.get("contains_secrets"), manifest.get("contains_database_dump")) != (
            "tkos.offline-bundle/1", "linux", arch, 5, False, False, False):
        raise RuntimeError("BUNDLE_HEADER_MISMATCH")
    if arch not in {"amd64", "arm64"} or set(manifest.get("components", {})) != COMPONENTS:
        raise RuntimeError("BUNDLE_COMPONENTS_MISMATCH")

    files = manifest.get("files", {})
    actual_payload = {
        str(path.relative_to(root)) for path in root.rglob("*")
        if path.is_file() and path.name not in {"bundle-manifest.json", "SHA256SUMS"}
    }
    if set(files) != actual_payload:
        raise RuntimeError("BUNDLE_FILE_SET_MISMATCH")
    for name, recorded in files.items():
        path = root / name
        if path.stat().st_size != recorded.get("bytes") or sha256(path) != recorded.get("sha256"):
            raise RuntimeError(f"BUNDLE_FILE_HASH_MISMATCH:{name}")

    sums = parse_sums(sums_path)
    summed_files = {
        str(path.relative_to(root)) for path in root.rglob("*")
        if path.is_file() and path != sums_path
    }
    if set(sums) != summed_files:
        raise RuntimeError("SHA256SUMS_FILE_SET_MISMATCH")
    for name, digest in sums.items():
        if sha256(root / name) != digest:
            raise RuntimeError(f"SHA256SUMS_MISMATCH:{name}")

    with tarfile.open(root / "runtime-source.tar.gz", "r:gz") as source:
        for member in source.getmembers():
            validate_member(member)
    platforms = docker_archive_platforms(root / "images.tar.gz")
    expected_tags = {item["tag"] for item in manifest["components"].values()}
    if set(platforms) != expected_tags or any(platform != ("linux", arch) for platform in platforms.values()):
        raise RuntimeError("DOCKER_ARCHIVE_IMAGE_SET_MISMATCH")
    if any("clark" in tag.lower() for tag in expected_tags):
        raise RuntimeError("CLARK_IMAGE_REJECTED")

    loaded = False
    compose_images: list[str] = []
    if load:
        run(["docker", "load", "--input", str(root / "images.tar.gz")])
        for component, recorded in manifest["components"].items():
            item = json.loads(run(["docker", "image", "inspect", recorded["tag"]]))[0]
            if item.get("Id") != recorded.get("id") or (item.get("Os"), item.get("Architecture")) != ("linux", arch):
                raise RuntimeError(f"LOADED_IMAGE_MISMATCH:{component}")
        deploy = root / "deploy" / "offline-release"
        compose_images = run([
            "docker", "compose", "--profile", "ops", "--env-file", str(deploy / ".env.example"),
            "-f", str(deploy / "compose.yaml"), "config", "--images",
        ], cwd=deploy).splitlines()
        if set(compose_images) != expected_tags:
            raise RuntimeError("COMPOSE_IMAGE_SET_MISMATCH")
        loaded = True
    return {
        "ok": True,
        "release": manifest["release"],
        "source_ref": manifest["source_ref"],
        "architecture": arch,
        "components": sorted(COMPONENTS),
        "files_verified": len(sums),
        "images_loaded": loaded,
        "compose_images_verified": len(compose_images),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    if bundle.is_dir():
        result = verify(bundle, args.load)
    else:
        with tempfile.TemporaryDirectory() as temporary:
            result = verify(extract_bundle(bundle, Path(temporary)), args.load)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
