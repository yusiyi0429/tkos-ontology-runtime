#!/usr/bin/env python3
"""world-02 实验实例的镜像构建：按指定提交构建 tkos/ontology-runtime 与 tkos/ontology-worker 两个镜像，
标签 world-02-<短提交>-<架构>，打成可传到主机的包并写 SHA256SUMS。

源码一律是 git archive 出来的那个提交，不用工作区。第三方 wheel 按提交里 requirements.lock 的哈希取：
--wheel-cache 给的目录里有、架构对得上、哈希在锁里的直接复用，缺的在 Docker 里用 pip 下载（与
deploy/offline-release/prepare_wheelhouse.py 同一个镜像与平台标签）；项目 wheel 从提交源码构建。
镜像按提交里的 Dockerfile 构建（与 prepare_images.py 同样的 target 与 build-arg），不打发布号标签，
也不碰三个基础镜像（主机上沿用离线包的 v0.5.0-amd64）。

用法：
  python3 deploy/world-02/build_images.py --commit origin/world/0.2 --arch amd64 \\
      --output-dir ~/tkos-world-02-build [--wheel-cache <已有 wheelhouse 目录>]
产物在 <输出目录>/world-02-<短提交>-<架构>/：
  world-02-<短提交>-src.tar.gz            提交源码，主机上解到 releases/world-02-<短提交>/
  world-02-<短提交>-<架构>-images.tar.gz   两个镜像的 docker save（gzip）
  build-manifest.json                     提交、版本、镜像 id 与标签、镜像内 0.2 安装材料的核对
  SHA256SUMS                              以上三个文件
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
REPOSITORIES = {"runtime": "tkos/ontology-runtime", "worker": "tkos/ontology-worker"}
ARCH_TOKENS = {"amd64": "x86_64", "arm64": "aarch64"}
# 镜像里必须带上、且与提交源码逐字节一致的 0.2 安装材料（install-profile、set-registry 要读）。
WORLD_02_DOCS = {
    "/opt/tkos/docs/contracts/world-profile-0.2.json": "docs/contracts/world-profile-0.2.json",
    "/opt/tkos/docs/contracts/tkos-world-0.2.md": "docs/contracts/tkos-world-0.2.md",
    "/opt/tkos/docs/contracts/world-registry-0.2.json": "docs/contracts/world-registry-0.2.json",
    "/opt/tkos/docs/runtime-world-support-0.2.json": "docs/runtime-world-support-0.2.json",
}
DEPLOY_SCRIPTS = "deploy/world-02/provision-and-install.sh"
PROBE = """
import hashlib, json, sys
from memory_service_runtime.governed.protocol import SUPPORTED_PROTOCOL_CONTRACTS
print(json.dumps({"docs": {p: hashlib.sha256(open(p, "rb").read()).hexdigest() for p in sys.argv[1:]},
                  "world_0_2_supported": ("tkos.world", "tkos.world/0.2") in SUPPORTED_PROTOCOL_CONTRACTS}))
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(argv: list[str], **kwargs) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, check=False, **kwargs)
    if result.returncode:
        sys.stderr.write(result.stderr[-4000:])
        raise RuntimeError(f"COMMAND_FAILED:{' '.join(argv[:3])}:{result.returncode}")
    return result.stdout.strip()


def gzip_into(stream, target: Path) -> None:
    with target.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6, mtime=0) as out:
        shutil.copyfileobj(stream, out, length=1024 * 1024)


def lock_specs(lock_text: str) -> list[str]:
    """requirements.lock 里 Linux 要装的 name==version（同 prepare_wheelhouse.py 的解析）。"""
    specs = []
    for line in lock_text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+==[^ ;\\]+)(?:\s*;\s*(.+?))?\s*\\?$", line)
        if not match or match.group(2) == "sys_platform == 'win32'":
            continue
        if match.group(2) not in (None, "implementation_name != 'pypy'"):
            raise RuntimeError(f"UNSUPPORTED_REQUIREMENT_MARKER:{match.group(2)}")
        specs.append(match.group(1))
    return specs


def project_name(text: str) -> str:
    return re.split(r"-\d|==", text, maxsplit=1)[0].lower().replace("_", "-")


def wheel_fits(name: str, arch: str) -> bool:
    platform = name[:-len(".whl")].split("-")[-1]
    return platform == "any" or ARCH_TOKENS[arch] in platform


def wheelhouse(source: Path, arch: str, cache: Path | None, out: Path) -> None:
    """第三方 wheel（先复用缓存，缺的下载）加提交源码构建的项目 wheel；每个第三方 wheel 的哈希都在锁里。"""
    lock = (source / "requirements.lock").read_text(encoding="utf-8")
    specs = lock_specs(lock)
    wanted = {project_name(spec): spec for spec in specs}
    for wheel in sorted(cache.glob("*.whl")) if cache else []:
        if (project_name(wheel.name) in wanted and wheel_fits(wheel.name, arch)
                and f"sha256:{sha256(wheel)}" in lock and not (out / wheel.name).exists()):
            shutil.copy2(wheel, out / wheel.name)
    have = {project_name(wheel.name) for wheel in out.glob("*.whl")}
    missing = [spec for name, spec in wanted.items() if name not in have]
    if missing:
        sys.path.insert(0, str(source / "deploy/offline-release"))
        import prepare_wheelhouse  # 提交里的发布工具：下载用的镜像与平台标签以它为准
        command = ["docker", "run", "--rm", "--volume", f"{out.resolve()}:/output", prepare_wheelhouse.DOWNLOADER_IMAGE,
                   "python", "-m", "pip", "download", "--no-deps", "--only-binary=:all:", "--implementation", "cp",
                   "--python-version", "312", "--dest", "/output"]
        for platform in prepare_wheelhouse.PLATFORMS[arch]:
            command += ["--platform", platform]
        print(f"downloading {len(missing)} wheels for {arch} ...", flush=True)
        run(command + missing)
    wheels = sorted(out.glob("*.whl"))
    if {project_name(w.name) for w in wheels} != set(wanted) or len(wheels) != len(wanted):
        raise RuntimeError("WHEELHOUSE_INCOMPLETE")
    for wheel in wheels:
        if not wheel_fits(wheel.name, arch) or f"sha256:{sha256(wheel)}" not in lock:
            raise RuntimeError(f"WHEEL_NOT_IN_LOCK:{wheel.name}")
    run(["uv", "build", "--wheel", "--out-dir", str(out), str(source)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", required=True, help="要构建的提交（分支名、标签或提交号）")
    parser.add_argument("--arch", choices=sorted(ARCH_TOKENS), default="amd64")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wheel-cache", type=Path, help="已有的 wheelhouse 目录，架构与哈希对得上的第三方 wheel 直接复用")
    args = parser.parse_args()

    commit = run(["git", "-C", str(ROOT), "rev-parse", "--verify", f"{args.commit}^{{commit}}"])
    short = commit[:7]
    tag = f"world-02-{short}-{args.arch}"
    out = args.output_dir.expanduser().resolve() / tag
    if out.exists():
        raise RuntimeError(f"OUTPUT_ALREADY_EXISTS:{out}")
    src_package = out / f"world-02-{short}-src.tar.gz"
    images_package = out / f"{tag}-images.tar.gz"

    with tempfile.TemporaryDirectory(prefix="world-02-build-") as temporary:
        work = Path(temporary)
        archive = work / "source.tar"
        run(["git", "-C", str(ROOT), "archive", "--format=tar", f"--output={archive}", commit])
        source = work / "source"
        source.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        version = re.search(r'^version = "([^"]+)"$', (source / "pyproject.toml").read_text(encoding="utf-8"),
                            re.MULTILINE).group(1)
        wheels = work / "wheels"
        wheels.mkdir()
        wheelhouse(source, args.arch, args.wheel_cache.expanduser() if args.wheel_cache else None, wheels)

        build_date = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        images = {}
        for target, repository in REPOSITORIES.items():
            image = f"{repository}:{tag}"
            print(f"building {image} ...", flush=True)
            run(["docker", "buildx", "build", "--load", "--platform", f"linux/{args.arch}",
                 "--build-context", f"wheelhouse={wheels}", "--target", target,
                 "--build-arg", f"VERSION={version}", "--build-arg", f"VCS_REF={commit}",
                 "--build-arg", f"BUILD_DATE={build_date}", "--tag", image, str(source)])
            item = json.loads(run(["docker", "image", "inspect", image]))[0]
            labels = item["Config"].get("Labels") or {}
            if (item["Os"], item["Architecture"]) != ("linux", args.arch) or labels.get(
                    "org.opencontainers.image.revision") != commit or labels.get("org.opencontainers.image.version") != version:
                raise RuntimeError(f"IMAGE_MISMATCH:{image}")
            images[target] = {"tag": image, "id": item["Id"], "architecture": item["Architecture"],
                              "version": version, "revision": commit}

        probe = json.loads(run(["docker", "run", "--rm", "--platform", f"linux/{args.arch}", "--network", "none",
                                "--entrypoint", "python", images["runtime"]["tag"], "-c", PROBE, *WORLD_02_DOCS]))
        expected = {inside: sha256(source / relative) for inside, relative in WORLD_02_DOCS.items()}
        if probe["docs"] != expected or probe["world_0_2_supported"] is not True:
            raise RuntimeError("WORLD_0_2_DOCS_MISMATCH")
        deploy_scripts = (source / DEPLOY_SCRIPTS).is_file()

        out.mkdir(parents=True)
        with archive.open("rb") as stream:
            gzip_into(stream, src_package)
        saver = subprocess.Popen(["docker", "save", images["runtime"]["tag"], images["worker"]["tag"]],
                                 stdout=subprocess.PIPE)
        gzip_into(saver.stdout, images_package)
        if saver.wait():
            raise RuntimeError("DOCKER_SAVE_FAILED")

    manifest = {"schema_version": "tkos.world-02-build/1", "commit": commit, "tag": tag, "architecture": args.arch,
                "version": version, "build_date": build_date, "images": images,
                "world_0_2_docs_sha256": expected, "world_0_2_supported": True,
                "deploy_scripts_included": deploy_scripts,
                "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256(path)}
                          for path in (src_package, images_package)}}
    (out / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "SHA256SUMS").write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in (src_package, images_package, out / "build-manifest.json")),
        encoding="utf-8")
    if not deploy_scripts:
        print(f"WARNING: {commit} 不含 {DEPLOY_SCRIPTS}：主机上的源码包里没有 world-02 的部署脚本，"
              "部署要用合入 deploy/world-02 之后的提交构建", file=sys.stderr)
    print(json.dumps({"ok": True, "output": str(out), "tag": tag, "commit": commit, **manifest["files"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
