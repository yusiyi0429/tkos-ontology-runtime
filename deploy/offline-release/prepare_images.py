#!/usr/bin/env python3
"""Build Runtime images and pin the three vendor images for one architecture."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess


HERE = Path(__file__).resolve().parent
PLAN = HERE / "images.json"
ARCHES = {"amd64", "arm64"}
RUNTIME_COMPONENTS = {
    "runtime_api": ("runtime", "tkos/ontology-runtime"),
    "runtime_worker": ("worker", "tkos/ontology-worker"),
}
VENDOR_REPOSITORIES = {
    "postgres_pgvector": "tkos/offline-postgres-pgvector",
    "minio_server": "tkos/offline-minio",
    "minio_client": "tkos/offline-minio-mc",
}


def run(argv: list[str]) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"COMMAND_FAILED:{argv[0]}:{result.returncode}")
    return result.stdout.strip()


def inspect(tag: str, arch: str) -> dict:
    item = json.loads(run(["docker", "image", "inspect", tag]))[0]
    if item.get("Os") != "linux" or item.get("Architecture") != arch:
        raise RuntimeError(f"IMAGE_PLATFORM_MISMATCH:{tag}")
    return {
        "tag": tag,
        "id": item["Id"],
        "os": item["Os"],
        "architecture": item["Architecture"],
        "labels": item.get("Config", {}).get("Labels") or {},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--arch", choices=sorted(ARCHES), required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build-date")
    args = parser.parse_args()

    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if args.release != plan["release"] or not re.fullmatch(r"v\d+\.\d+\.\d+", args.release):
        raise RuntimeError("RELEASE_PLAN_MISMATCH")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_ref):
        raise RuntimeError("SOURCE_REF_MUST_BE_FULL_COMMIT")
    source = args.source_dir.resolve()
    if not (source / "Dockerfile").is_file():
        raise RuntimeError("SOURCE_DIR_INVALID")
    wheelhouse = args.wheelhouse.resolve()
    wheel_manifest = wheelhouse / "wheelhouse-manifest.json"
    if not wheel_manifest.is_file():
        raise RuntimeError("WHEELHOUSE_INVALID")
    wheel_data = json.loads(wheel_manifest.read_text(encoding="utf-8"))
    if (wheel_data.get("architecture"), wheel_data.get("release"),
            wheel_data.get("source_ref")) != (args.arch, args.release, args.source_ref):
        raise RuntimeError("WHEELHOUSE_ARCHITECTURE_MISMATCH")
    if args.output.exists():
        raise RuntimeError("OUTPUT_ALREADY_EXISTS")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    build_date = args.build_date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    version = args.release.removeprefix("v")
    images: dict[str, dict] = {}
    for component, (target, repository) in RUNTIME_COMPONENTS.items():
        tag = f"{repository}:{args.release}-{args.arch}"
        run([
            "docker", "buildx", "build", "--load", "--platform", f"linux/{args.arch}",
            "--build-context", f"wheelhouse={wheelhouse}",
            "--target", target,
            "--build-arg", f"VERSION={version}",
            "--build-arg", f"VCS_REF={args.source_ref}",
            "--build-arg", f"BUILD_DATE={build_date}",
            "--tag", tag, str(source),
        ])
        images[component] = inspect(tag, args.arch)
        labels = images[component]["labels"]
        if labels.get("org.opencontainers.image.version") != version or labels.get("org.opencontainers.image.revision") != args.source_ref:
            raise RuntimeError(f"RUNTIME_LABEL_MISMATCH:{component}")

    for component, target_repository in VENDOR_REPOSITORIES.items():
        vendor = plan["components"][component]
        source_ref = f'{vendor["repository"]}@{vendor["platforms"][args.arch]}'
        target_tag = f"{target_repository}:{args.release}-{args.arch}"
        run(["docker", "pull", "--platform", f"linux/{args.arch}", source_ref])
        run(["docker", "tag", source_ref, target_tag])
        images[component] = inspect(target_tag, args.arch)
        images[component]["upstream"] = {
            "repository": vendor["repository"],
            "index_digest": vendor["index_digest"],
            "platform_digest": vendor["platforms"][args.arch],
            **({"release": vendor["upstream_release"]} if "upstream_release" in vendor else {}),
        }

    result = {
        "schema_version": "tkos.offline-image-set/1",
        "release": args.release,
        "source_ref": args.source_ref,
        "build_date": build_date,
        "os": "linux",
        "architecture": args.arch,
        "images": images,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "architecture": args.arch, "images": len(images), "output": str(args.output)}))


if __name__ == "__main__":
    main()
