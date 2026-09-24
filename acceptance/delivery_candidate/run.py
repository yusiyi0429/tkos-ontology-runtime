#!/usr/bin/env python3
"""交付候选的干净环境安装验收（#32 批次 E）：从一个提交出发，按文档构建、安装、迁移并留下可追溯的交付清单。

步骤：
1. 在临时 git worktree 里检出该提交：不带工作区里任何未提交或未跟踪的文件。
2. 用 deploy/offline-release/prepare_wheelhouse.py 准备 wheelhouse；本项目的 wheel 在这一步从该检出构建。
3. 按 docs/deployment.md 的手工构建命令构建 runtime 与 worker 镜像，核对镜像标签。
4. 用 deploy/offline-release/start-offline.sh 在全新卷上起一套栈（空库迁移、授权、API、Worker）；
   PostgreSQL 与 MinIO 用本机已有的离线包镜像。
5. 在这套栈上核对：
   - 迁移清单与每个文件的 SHA256；
   - 用镜像里 /opt/tkos/docs 的材料安装 Method 0.4、0.5 与 world 0.1；
   - 带凭证的读取成功。
6. 升级路径：用已发布的 v0.4.0 镜像把一个新库迁到它的迁移头，再用候选镜像升级，核对只补上之后的迁移、旧行回填摘要。
7. 在同一检出上跑无库测试，结果写进清单。

凭证只在 --private 目录与临时 worktree 里，运行结束删除临时栈与 worktree；清单只有提交、摘要、镜像 id、迁移与计数。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ARCH = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "amd64"
VENDOR_TAG = "v0.4.0"  # 离线包里固定摘要的 PostgreSQL/pgvector、MinIO、mc 镜像，本机已有
RELEASED = f"tkos/ontology-runtime:v0.4.0-{ARCH}"


def run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None, timeout: int = 1800,
        capture: bool = True) -> str:
    result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=capture, timeout=timeout)
    if result.returncode:
        tail = (result.stderr or result.stdout or "")[-1500:] if capture else ""
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv[:4])} … {tail}")
    return result.stdout if capture else ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def free_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def alnum(n: int = 32) -> str:
    return secrets.token_hex(n // 2)


class Candidate:
    def __init__(self, commit: str, private: Path, output: Path) -> None:
        self.commit = run(["git", "rev-parse", "--verify", commit + "^{commit}"], cwd=ROOT).strip()
        self.version = run([sys.executable, "-c", "import tomllib,sys;print(tomllib.load(open(sys.argv[1],'rb'))['project']['version'])",
                            str(ROOT / "pyproject.toml")]).strip()
        self.private, self.output = private, output
        self.private.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.output.mkdir(parents=True, exist_ok=False)
        self.src = self.private / "src"
        self.stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        self.project = f"tkos-candidate-{self.stamp}"
        self.tag = f"v{self.version}-candidate-{ARCH}"
        self.manifest: dict = {"schema_version": "tkos.delivery-candidate/1", "commit": self.commit,
                               "version": self.version, "architecture": ARCH,
                               "started_at": datetime.now(timezone.utc).isoformat(), "checks": {}}

    def check(self, name: str, ok: bool, **evidence) -> None:
        self.manifest["checks"][name] = {"pass": bool(ok), **evidence}
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        if not ok:
            raise AssertionError(f"{name}: {evidence}")

    # 1 ------------------------------------------------------------------------
    def checkout(self) -> None:
        run(["git", "worktree", "add", "--detach", str(self.src), self.commit], cwd=ROOT)
        status = run(["git", "status", "--porcelain"], cwd=self.src).strip()
        self.check("clean_checkout", status == "", commit=self.commit)

    # 2 ------------------------------------------------------------------------
    def wheelhouse(self) -> None:
        self.wheels = self.private / "wheelhouse"
        command = [sys.executable, str(self.src / "deploy/offline-release/prepare_wheelhouse.py"), "--arch", ARCH,
                   "--release", f"v{self.version}", "--source-ref", self.commit, "--output", str(self.wheels)]
        # 下载偶发失败时用 --resume 补齐缺的依赖（已下载的保留），最多再试两次。
        for attempt in range(3):
            try:
                run(command + (["--resume"] if attempt else []), cwd=self.src, timeout=3600)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
        [wheel] = sorted(self.wheels.glob("tkos_memory_service-*.whl"))
        self.manifest["wheel"] = {"name": wheel.name, "sha256": sha256(wheel)}
        self.check("project_wheel_built_from_the_checkout", wheel.name.startswith(f"tkos_memory_service-{self.version}-"),
                   wheel=wheel.name, dependency_wheels=len(list(self.wheels.glob("*.whl"))) - 1)

    # 3 ------------------------------------------------------------------------
    def images(self) -> None:
        built = {}
        for target, repository in (("runtime", "tkos/ontology-runtime"), ("worker", "tkos/ontology-worker")):
            tag = f"{repository}:{self.tag}"
            run(["docker", "buildx", "build", "--load", "--platform", f"linux/{ARCH}",
                 "--build-context", f"wheelhouse={self.wheels}", "--target", target,
                 "--build-arg", f"VERSION={self.version}", "--build-arg", f"VCS_REF={self.commit}",
                 "--build-arg", f"BUILD_DATE={self.manifest['started_at']}", "--tag", tag, str(self.src)],
                timeout=3600)
            item = json.loads(run(["docker", "image", "inspect", tag]))[0]
            labels = item["Config"]["Labels"] or {}
            built[target] = {"tag": tag, "id": item["Id"], "architecture": item["Architecture"],
                             "version_label": labels.get("org.opencontainers.image.version"),
                             "revision_label": labels.get("org.opencontainers.image.revision")}
        self.manifest["images"] = built
        self.check("images_carry_the_version_and_commit",
                   all(i["version_label"] == self.version and i["revision_label"] == self.commit
                       and i["architecture"] == ARCH for i in built.values()),
                   images={k: {"tag": v["tag"], "id": v["id"]} for k, v in built.items()})
        vendor = {}
        for name in ("offline-postgres-pgvector", "offline-minio", "offline-minio-mc"):
            tag = f"tkos/{name}:{VENDOR_TAG}-{ARCH}"
            vendor[name] = {"tag": tag, "id": json.loads(run(["docker", "image", "inspect", tag]))[0]["Id"]}
        self.manifest["vendor_images"] = vendor

    # 4 ------------------------------------------------------------------------
    def stack(self) -> None:
        here = self.src / "deploy/offline-release"
        (here / "private").mkdir(mode=0o700)
        for name in ("minio-app-access-key", "minio-app-secret-key"):
            path = here / "private" / name
            path.write_text(alnum(32), encoding="utf-8")
            path.chmod(0o600)
        self.port = free_port()
        values = {
            "COMPOSE_PROJECT_NAME": self.project, "TARGET_ARCH": ARCH, "RUNTIME_HTTP_PORT": str(self.port),
            "RUNTIME_API_IMAGE": self.manifest["images"]["runtime"]["tag"],
            "RUNTIME_WORKER_IMAGE": self.manifest["images"]["worker"]["tag"],
            "POSTGRES_IMAGE": f"tkos/offline-postgres-pgvector:{VENDOR_TAG}-{ARCH}",
            "MINIO_IMAGE": f"tkos/offline-minio:{VENDOR_TAG}-{ARCH}",
            "MINIO_MC_IMAGE": f"tkos/offline-minio-mc:{VENDOR_TAG}-{ARCH}",
            "POSTGRES_ADMIN_PASSWORD": alnum(), "POSTGRES_OWNER_PASSWORD": alnum(), "POSTGRES_APP_PASSWORD": alnum(),
            "MEMORY_TENANT": f"runtime-acceptance-candidate-{self.stamp}",
            "MEMORY_ORG": f"runtime-acceptance-candidate-org-{self.stamp}",
            "MINIO_ROOT_USER": "candidate" + alnum(12), "MINIO_ROOT_PASSWORD": alnum(),
        }
        lines = []
        for line in (here / ".env.example").read_text(encoding="utf-8").splitlines():
            key = line.split("=", 1)[0]
            lines.append(f"{key}={values[key]}" if "=" in line and key in values else line)
        self.env_file = here / ".env"
        self.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.env_file.chmod(0o600)
        self.values = values
        self.compose = ["docker", "compose", "--env-file", str(self.env_file), "-f", str(here / "compose.yaml")]
        run([str(here / "start-offline.sh"), str(self.env_file)], cwd=here, timeout=900)
        self.check("fresh_stack_starts_healthy_from_the_documented_script", True, port="loopback")

    def owner_python(self, code: str, *, database: str | None = None, image: str | None = None) -> str:
        """在栈的网络里用候选（或指定）镜像、以迁移所有者身份跑一段 Python，只取它打印的 JSON。"""
        network = f"{self.project}_data-internal"
        db = database or "tkos_runtime"
        owner = f"host=postgres port=5432 dbname={db} user=tkos_runtime_owner password={self.values['POSTGRES_OWNER_PASSWORD']}"
        return run(["docker", "run", "--rm", "--network", network, "-e", f"MIGRATION_DATABASE_URL={owner}",
                    "-e", f"DATABASE_URL={owner}", "--entrypoint", "python",
                    image or self.manifest["images"]["runtime"]["tag"], "-c", code], timeout=600)

    # 5 ------------------------------------------------------------------------
    def fresh_checks(self) -> None:
        listing = json.loads(self.owner_python(
            "import json,os,psycopg\n"
            "with psycopg.connect(os.environ['DATABASE_URL']) as c:\n"
            "    print(json.dumps(c.execute('SELECT name, sha256 FROM schema_migrations ORDER BY name').fetchall()))"))
        expected = sorted(p.name for p in (self.src / "src/memory_service_app/migrations").glob("*.sql"))
        digests = {p.name: sha256(p) for p in (self.src / "src/memory_service_app/migrations").glob("*.sql")}
        self.manifest["migrations"] = [{"name": n, "sha256": s} for n, s in listing]
        self.check("empty_database_is_migrated_to_the_head_with_file_digests",
                   [n for n, _ in listing] == expected and all(s == digests[n] for n, s in listing),
                   applied=len(listing), head=expected[-1])
        install = (
            "import json,os,subprocess,psycopg\n"
            "from psycopg.rows import dict_row\n"
            "from memory_service_runtime.governed import bootstrap\n"
            "tag=os.environ.get('TAG','')\n"
            "with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as c:\n"
            f"    f=bootstrap.seed_scope(c,'runtime-acceptance-candidate-scope-{self.stamp}','runtime-acceptance-candidate-company-{self.stamp}')\n"
            "d='/opt/tkos/docs'\n"
            "steps=[\n"
            " ['install-profile','--profile-json',d+'/contracts/method-profile-0.4.json','--contract-file',d+'/contracts/tkos-method-0.4.md'],\n"
            " ['install-profile','--profile-json',d+'/contracts/method-profile-0.5.json','--contract-file',d+'/contracts/tkos-method-0.5.md','--ontology-registry-file',d+'/contracts/ontology-registry-0.7.json'],\n"
            " ['install-profile','--profile-json',d+'/contracts/world-profile-0.1.json','--contract-file',d+'/contracts/tkos-world-0.1.md','--world-registry-file',d+'/contracts/world-registry-0.1.json'],\n"
            "]\n"
            "out=[]\n"
            "for s in steps:\n"
            "    r=subprocess.run(['tkos-governed-control',s[0],'--scope-id',f['scope_id'],*s[1:],'--reason','candidate install check'],capture_output=True,text=True)\n"
            "    out.append({'step':s[2].rsplit('/',1)[1],'rc':r.returncode,'ok':(json.loads(r.stdout).get('ok') if r.returncode==0 else False)})\n"
            "st=subprocess.run(['tkos-governed-control','status','--scope-id',f['scope_id']],capture_output=True,text=True)\n"
            "status=json.loads(st.stdout) if st.returncode==0 else {}\n"
            "print(json.dumps({'installs':out,'profiles':[p['profile_id']+'@'+p['revision'] for p in status.get('profiles',[])],"
            "'outcome':f['outcome']['object_id'],'token':f['actors']['ceo']['token']}))\n")
        result = json.loads(self.owner_python(install))
        token, outcome = result.pop("token"), result.pop("outcome")
        self.manifest["protocol_installs"] = result
        self.check("method_0_4_0_5_and_world_0_1_install_from_the_image_materials",
                   all(item["rc"] == 0 and item["ok"] for item in result["installs"]), **result)
        import urllib.request
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/objects/{outcome}",
                                         headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
        self.check("an_authenticated_governed_read_succeeds_on_the_fresh_stack", status == 200, status=status)

    # 6 ------------------------------------------------------------------------
    def upgrade(self) -> None:
        admin = f"host=postgres port=5432 dbname=tkos_runtime user=tkos_runtime_admin password={self.values['POSTGRES_ADMIN_PASSWORD']}"
        create = ("import os,psycopg\n"
                  "with psycopg.connect(os.environ['ADMIN'],autocommit=True) as c:\n"
                  "    c.execute('CREATE DATABASE tkos_upgrade OWNER tkos_runtime_owner')\n"
                  "with psycopg.connect(os.environ['ADMIN'].replace('dbname=tkos_runtime','dbname=tkos_upgrade'),autocommit=True) as c:\n"
                  "    c.execute('CREATE EXTENSION IF NOT EXISTS vector')\n")
        run(["docker", "run", "--rm", "--network", f"{self.project}_data-internal", "-e", f"ADMIN={admin}",
             "--entrypoint", "python", self.manifest["images"]["runtime"]["tag"], "-c", create])
        migrate = ("import json,os\nfrom memory_service_app.migrate import migrate\n"
                   "print(json.dumps(migrate(os.environ['DATABASE_URL'])))")
        released = json.loads(self.owner_python(migrate, database="tkos_upgrade", image=RELEASED))
        upgraded = json.loads(self.owner_python(migrate, database="tkos_upgrade"))
        backfilled = json.loads(self.owner_python(
            "import json,os,psycopg\nwith psycopg.connect(os.environ['DATABASE_URL']) as c:\n"
            "    print(json.dumps(c.execute('SELECT count(*), count(sha256) FROM schema_migrations').fetchone()))",
            database="tkos_upgrade"))
        released_id = json.loads(run(["docker", "image", "inspect", RELEASED]))[0]["Id"]
        self.manifest["upgrade"] = {"released_image": {"tag": RELEASED, "id": released_id},
                                    "applied_by_released": released, "applied_by_candidate": upgraded,
                                    "rows_and_digests": backfilled}
        expected_new = [n for n in sorted(p.name for p in (self.src / "src/memory_service_app/migrations").glob("*.sql"))
                        if n not in released]
        self.check("the_released_v0_4_0_database_upgrades_to_the_head",
                   released and released[-1].startswith("0028_") and upgraded == expected_new
                   and backfilled[0] == backfilled[1] == len(released) + len(upgraded),
                   released_head=released[-1] if released else None, applied=upgraded, rows_and_digests=backfilled)

    # 7 ------------------------------------------------------------------------
    def regression(self) -> None:
        run(["uv", "sync", "--frozen", "--extra", "s3", "--offline"], cwd=self.src, timeout=900)
        env = {k: v for k, v in os.environ.items() if not k.endswith("DATABASE_URL")}
        result = subprocess.run([str(self.src / ".venv/bin/python"), "-m", "pytest", "tests", "-q", "-m", "not db",
                                 "-p", "no:cacheprovider"], cwd=self.src, env=env, text=True, capture_output=True, timeout=1800)
        summary = (result.stdout.strip().splitlines() or [""])[-1]
        self.manifest["regression"] = {"no_database_selection": summary, "exit_code": result.returncode}
        self.check("the_no_database_tests_pass_on_the_same_checkout", result.returncode == 0, summary=summary)

    def teardown(self) -> None:
        if getattr(self, "compose", None):
            subprocess.run([*self.compose, "down", "-v", "--remove-orphans"], capture_output=True, text=True, timeout=600)
        subprocess.run(["git", "worktree", "remove", "--force", str(self.src)], cwd=ROOT, capture_output=True, text=True)

    def execute(self) -> dict:
        try:
            for step in (self.checkout, self.wheelhouse, self.images, self.stack, self.fresh_checks, self.upgrade,
                         self.regression):
                step()
            self.manifest["accepted"] = all(item["pass"] for item in self.manifest["checks"].values())
        except Exception as exc:  # 记下失败步骤，清单照样写出
            self.manifest["accepted"] = False
            self.manifest["error"] = f"{type(exc).__name__}: {str(exc)[:1500]}"
        finally:
            self.teardown()
            self.manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            text = json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n"
            for key, secret in getattr(self, "values", {}).items():
                if any(part in key for part in ("PASSWORD", "SECRET", "ROOT_USER")):
                    text = text.replace(secret, "[REDACTED]")
            (self.output / "delivery-manifest.json").write_text(text, encoding="utf-8")
        return self.manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = Candidate(args.commit, args.private, args.output).execute()
    print(json.dumps({"accepted": manifest["accepted"], "commit": manifest["commit"],
                      "manifest": str(args.output / "delivery-manifest.json"), "error": manifest.get("error")}))
    raise SystemExit(0 if manifest["accepted"] else 1)


if __name__ == "__main__":
    main()
