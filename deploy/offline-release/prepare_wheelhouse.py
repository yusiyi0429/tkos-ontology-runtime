#!/usr/bin/env python3
"""Download one hashed Linux wheelhouse for a Runtime target architecture."""
from __future__ import annotations

import argparse
import hashlib
import json
import ipaddress
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = {
    "amd64": ("manylinux_2_28_x86_64", "manylinux2014_x86_64"),
    "arm64": ("manylinux_2_28_aarch64", "manylinux2014_aarch64"),
}
DOWNLOADER_IMAGE = "python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pypi-cdn-ip", help="optional temporary IPv4/IPv6 for pypi.org and files.pythonhosted.org")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    lock = ROOT / "requirements.lock"
    project_text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version_match = re.search(r'^version = "([^"]+)"$', project_text, re.MULTILINE)
    if version_match is None:
        raise RuntimeError("PROJECT_VERSION_MISSING")
    version = version_match.group(1)
    if args.release != f"v{version}" or not re.fullmatch(r"[0-9a-f]{40}", args.source_ref):
        raise RuntimeError("SOURCE_VERSION_MISMATCH")
    source_dirty = subprocess.run(
        ["git", "diff", "--quiet", args.source_ref, "--", "pyproject.toml", "README.md", "src"],
        cwd=ROOT,
    )
    if source_dirty.returncode:
        raise RuntimeError("PROJECT_WHEEL_SOURCE_DIRTY")
    if args.output.exists() and not args.resume:
        raise RuntimeError("OUTPUT_ALREADY_EXISTS")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    specifications = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+==[^ ;\\]+)(?:\s*;\s*(.+?))?\s*\\?$", line)
        if not match:
            continue
        marker = match.group(2)
        if marker == "sys_platform == 'win32'":
            continue
        if marker not in (None, "implementation_name != 'pypy'"):
            raise RuntimeError(f"UNSUPPORTED_REQUIREMENT_MARKER:{marker}")
        specifications.append(match.group(1))
    existing = {
        re.split(r"-\d", wheel.name, maxsplit=1)[0].lower().replace("_", "-")
        for wheel in args.output.glob("*.whl")
    }
    missing = [
        spec for spec in specifications
        if spec.split("==", 1)[0].lower().replace("_", "-") not in existing
    ]
    specs_path = args.output / "requirements-specs.json"
    specs_path.write_text(json.dumps(missing), encoding="utf-8")
    downloader = (
        "import concurrent.futures,json,subprocess,sys;"
        "specs=json.load(open('/input/requirements-specs.json'));platforms=sys.argv[1:];"
        "base=['python','-m','pip','download','--no-deps','--only-binary=:all:',"
        "'--implementation','cp','--python-version','312','--dest','/output'];"
        "base+=sum((['--platform',p] for p in platforms),[]);"
        "run=lambda spec:subprocess.run(base+[spec],check=True,capture_output=True);"
        "pool=concurrent.futures.ThreadPoolExecutor(max_workers=6);"
        "results=list(pool.map(run,specs));pool.shutdown()"
    )
    command = [
        "docker", "run", "--rm",
        "--volume", f"{specs_path.resolve()}:/input/requirements-specs.json:ro",
        "--volume", f"{args.output.resolve()}:/output",
    ]
    if args.pypi_cdn_ip:
        address = str(ipaddress.ip_address(args.pypi_cdn_ip))
        command += ["--add-host", f"pypi.org:{address}",
                    "--add-host", f"files.pythonhosted.org:{address}"]
    command += [DOWNLOADER_IMAGE, "python", "-c", downloader, *PLATFORMS[args.arch]]
    if missing:
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError(f"PIP_DOWNLOAD_FAILED:{result.returncode}")
    specs_path.unlink()
    for old_project_wheel in args.output.glob("tkos_memory_service-*.whl"):
        old_project_wheel.unlink()
    project = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(args.output), str(ROOT)],
        text=True, capture_output=True, check=False,
    )
    if project.returncode:
        raise RuntimeError(f"PROJECT_WHEEL_FAILED:{project.returncode}")
    wheels = sorted(args.output.glob("*.whl"))
    allowed = lock.read_text(encoding="utf-8")
    if not wheels:
        raise RuntimeError("WHEELHOUSE_EMPTY")
    if len(wheels) != len(specifications) + 1:
        raise RuntimeError("WHEELHOUSE_COUNT_MISMATCH")
    records = []
    for wheel in wheels:
        digest = sha256(wheel)
        is_project = wheel.name.startswith(f"tkos_memory_service-{version}-")
        if not is_project and f"sha256:{digest}" not in allowed:
            raise RuntimeError(f"WHEEL_NOT_IN_LOCK:{wheel.name}")
        records.append({"name": wheel.name, "bytes": wheel.stat().st_size,
                        "sha256": digest, "project": is_project})
    if sum(item["project"] for item in records) != 1:
        raise RuntimeError("PROJECT_WHEEL_MISMATCH")
    manifest = {
        "schema_version": "tkos.wheelhouse/1",
        "architecture": args.arch,
        "release": args.release,
        "source_ref": args.source_ref,
        "python": "cp312",
        "platforms": list(PLATFORMS[args.arch]),
        "requirements_sha256": sha256(lock),
        "wheels": records,
    }
    (args.output / "wheelhouse-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"ok": True, "architecture": args.arch, "wheels": len(wheels),
                      "output": str(args.output)}, separators=(",", ":")))


if __name__ == "__main__":
    main()
