#!/usr/bin/env python3
"""交付候选的干净环境安装验收（#32 批次 E）：从一个提交出发，按文档构建、安装、迁移并留下可追溯的交付清单。

步骤：
1. 在临时 git worktree 里检出该提交：不带工作区里任何未提交或未跟踪的文件。版本号取该提交的 pyproject.toml。
2. 用 deploy/offline-release/prepare_wheelhouse.py 准备 wheelhouse；本项目的 wheel 在这一步从该检出构建。
3. 按 docs/deployment.md 的手工构建命令构建 runtime 与 worker 镜像，核对镜像标签。
4. 用 deploy/offline-release/start-offline.sh 在全新卷上起一套栈（空库迁移、授权、API、Worker）；
   PostgreSQL 与 MinIO 用本机已有的离线包镜像。
5. 在这套栈上核对：
   - 迁移清单与每个文件的 SHA256；
   - 只用镜像里 /opt/tkos/docs 的材料，经控制面四步（profile、scope 默认策略、支持登记、激活策略）
     把 Method 0.4、0.5 与 world 0.1 各装进一个合成 scope（install_protocols.py，在候选镜像里运行）；
   - 经 HTTP 办真实动作：0.5 由 CEO 登记并确认公司级约束、读回自己的回执；world 由 CEO 建 Company；
     0.4 只核对分派（装了 0.4 的 scope 由 0.4 规则答复，没装的在协议层拒绝），成功路径由 Method 0.4 验收覆盖。
6. 升级路径：已发布的 v0.4.0 镜像里的迁移文件与候选的同名文件逐字节一致；用 v0.4.0 镜像把一个新库迁到它的
   迁移头，再用候选镜像升级，核对只补上之后的迁移、旧行回填的摘要就是 v0.4.0 文件的摘要。
7. 在同一检出上跑无库测试，结果写进清单。库测试与各验收矩阵不在这里跑，清单的 not_covered 写明。

口令与凭证只在 --private 目录、临时 worktree 与 0600 的 --env-file 里，不上 docker 命令行；清单与打印的错误都先抹掉。
运行结束删除临时栈、worktree 与候选镜像（--keep-images 保留镜像）；清单只有提交、摘要、镜像 id、迁移与计数。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
ARCH = "arm64" if os.uname().machine in {"arm64", "aarch64"} else "amd64"
VENDOR_TAG = "v0.4.0"  # 离线包里固定摘要的 PostgreSQL/pgvector、MinIO、mc 镜像，本机已有
RELEASED = f"tkos/ontology-runtime:v0.4.0-{ARCH}"


def run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None, timeout: int = 1800,
        capture: bool = True) -> str:
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=capture, timeout=timeout)
    except subprocess.TimeoutExpired:  # 异常原文带整条命令，这里只留前几个参数
        raise RuntimeError(f"command timed out after {timeout}s: {' '.join(argv[:4])} …") from None
    if result.returncode:
        tail = (result.stderr or result.stdout or "")[-1500:] if capture else ""
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(argv[:4])} … {tail}")
    return result.stdout if capture else ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def http(base: str, method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(base + path, method=method,
                                     data=None if body is None else json.dumps(body).encode("utf-8"),
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:  # 例如 500 的纯文本
        return status, {"body": raw[:300].decode("utf-8", "replace")}


def _command(contract: str, kind: str, params: dict, target: dict | None) -> dict:
    return {"action_type": kind, "contract_version": contract, "target": target, "expected_versions": [],
            "idempotency_key": f"candidate-{secrets.token_hex(8)}", "reason": "Delivery candidate check",
            "params": params}


def prepare(base: str, token: str, contract: str, kind: str, params: dict) -> tuple[int, dict]:
    return http(base, "POST", "/v1/actions/prepare", token, _command(contract, kind, params, None))


def act(base: str, token: str, contract: str, kind: str, params: dict, target: dict | None = None) -> tuple[int, dict]:
    """prepare + commit 一个动作；返回提交的状态码与回执（或 prepare 的错误）。"""
    body = _command(contract, kind, params, target)
    status, prepared = http(base, "POST", "/v1/actions/prepare", token, body)
    if status != 200:
        return status, prepared
    body["expected_versions"] = prepared["expected_versions"]
    if prepared.get("target"):
        body["target"]["expected_version"] = prepared["target"]["expected_version"]
    return http(base, "POST", "/v1/actions", token, body)


def _outcome(status: int, body: dict) -> dict:
    """动作的结果：状态码，失败时另带错误码与信息。"""
    return {"status": status, **({"error": body.get("error") or body} if status != 200 else {})}


def _target(base: str, token: str, object_id: str) -> dict:
    view = http(base, "GET", f"/v1/objects/{object_id}", token)[1]
    return {"object_id": object_id, "revision_id": view.get("latest_revision_id"),
            "expected_version": view.get("object_version")}


def exercise_protocols(base: str, installed: dict) -> dict:
    """装好之后经 HTTP 核对：0.4 的请求进了 0.4 的业务规则；0.5 由 CEO 登记并确认公司级约束（0.5 独有动作），
    再读回自己的回执；world 由 CEO 建 Company。只返回状态码、错误与对象上的协议与阶段，不带凭证。"""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    period = {"start": (now - timedelta(days=30)).isoformat(), "end": (now + timedelta(days=335)).isoformat()}
    report = {}
    m04, m05, world = installed["method-0.4"], installed["method-0.5"], installed["world-0.1"]
    outcome = http(base, "GET", f"/v1/objects/{m04['outcome_id']}", m04["ceo_token"])[1]
    revision = outcome.get("latest_revision") or {}
    source = {"object_id": m04["outcome_id"], "revision_id": revision.get("revision_id"),
              "payload_hash": revision.get("payload_hash")}
    issue = {"title": "Candidate issue", "summary": "Delivery candidate check.",
             "core_question": "Is Method 0.4 installed and answering?", "business_scope": "strategic",
             "urgency": "green", "source_refs": [source], "strategy_ref": None, "architecture_ref": None}
    # 0.4 的第一步要 CEO Agent 与运行记录；这里只核对分派：装了 0.4 的 scope 由 0.4 规则拒绝人类发起；
    # 0.5 的 scope 同样授权了这个动作（0.5 沿用 0.4 的 M1A），但没装 0.4，在协议层拒绝。0.4 的成功路径由
    # Method 0.4 独立验收覆盖。
    report["method-0.4"] = {
        "installed_scope": _outcome(*prepare(base, m04["ceo_token"], "tkos.method/0.4", "m1a_create_issue",
                                             {"domain_id": m04["domain_id"], "payload": issue})),
        "scope_without_0_4": _outcome(*prepare(base, m05["ceo_token"], "tkos.method/0.4", "m1a_create_issue",
                                               {"domain_id": m05["domain_id"], "payload": issue}))}
    payload = {"title": "Candidate company constraint", "applies_to": {"kind": "company"}, "architecture_ref": None,
               "statement": "Only two engineers are available in this period.", "constraint_type": "people",
               "effective": period, "source": "Synthetic headcount plan", "authority": "CEO", "severity": "hard",
               "evidence_refs": []}
    status, receipt = act(base, m05["ceo_token"], "tkos.method/0.5", "m1b_record_constraint",
                          {"domain_id": m05["domain_id"], "payload": payload})
    result = {"record_constraint": _outcome(status, receipt)}
    if status == 200:
        object_id = receipt["result"]["object_id"]
        status, receipt = act(base, m05["ceo_token"], "tkos.method/0.5", "m1b_confirm_constraint",
                              {"statement": "The CEO confirms this exact company constraint."},
                              _target(base, m05["ceo_token"], object_id))
        result["confirm_constraint"] = _outcome(status, receipt)
        if status == 200:
            result["read_own_receipt"] = http(base, "GET", f"/v1/action-receipts/{receipt['receipt_id']}",
                                              m05["ceo_token"])[0]
        view = http(base, "GET", f"/v1/objects/{object_id}", m05["ceo_token"])[1]
        result.update(contract_version=(view.get("protocol") or {}).get("contract_version"),
                      phase=(view.get("method_state") or {}).get("phase"))
    report["method-0.5"] = result
    status, receipt = act(base, world["ceo_token"], "tkos.world/0.1", "world_create_object",
                          {"domain_id": world["domain_id"], "object_type": "Company",
                           "payload": {"title": "Candidate company"}})
    view = (http(base, "GET", f"/v1/world/objects/{receipt['result']['object_id']}", world["ceo_token"])[1]
            if status == 200 else {})
    report["world-0.1"] = {"create_company": _outcome(status, receipt), "object_type": view.get("object_type")}
    return report


def protocols_work(report: dict) -> bool:
    m04, m05, world = report["method-0.4"], report["method-0.5"], report["world-0.1"]
    return (m04["installed_scope"]["status"] == 403 and m04["installed_scope"]["error"].get("code") == "FORBIDDEN"
            and m04["scope_without_0_4"]["status"] == 409
            and str(m04["scope_without_0_4"]["error"].get("code")).startswith("PROTOCOL_")
            and m05["record_constraint"]["status"] == 200 and m05.get("confirm_constraint", {}).get("status") == 200
            and m05.get("read_own_receipt") == 200
            and m05.get("contract_version") == "tkos.method/0.5" and m05.get("phase") == "confirmed"
            and world["create_company"]["status"] == 200 and world["object_type"] == "Company")


def free_port() -> int:
    with socket.socket() as handle:
        handle.bind(("127.0.0.1", 0))
        return handle.getsockname()[1]


def alnum(n: int = 32) -> str:
    return secrets.token_hex(n // 2)


class Candidate:
    def __init__(self, commit: str, private: Path, output: Path, *, keep_images: bool = False) -> None:
        self.commit = run(["git", "rev-parse", "--verify", commit + "^{commit}"], cwd=ROOT).strip()
        self.version = tomllib.loads(run(["git", "show", f"{self.commit}:pyproject.toml"], cwd=ROOT))["project"]["version"]
        self.private, self.output, self.keep_images = private, output, keep_images
        self.private.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.output.mkdir(parents=True, exist_ok=False)
        self.src = self.private / "src"
        self.stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        self.project = f"tkos-candidate-{self.stamp}"
        self.tag = f"v{self.version}-candidate-{ARCH}"
        self.secrets: set[str] = set()
        self.manifest: dict = {"schema_version": "tkos.delivery-candidate/1", "commit": self.commit,
                               "version": self.version, "architecture": ARCH,
                               "started_at": datetime.now(timezone.utc).isoformat(), "checks": {},
                               "not_covered": ["数据库测试（CI 的 python-db 与本机隔离栈另跑）",
                                               "各独立验收矩阵（按提交另跑，摘要另入库）",
                                               "Method 0.4 的成功路径（本检查只核对分派）",
                                               "目标环境、真实身份与真实数据"]}

    def check(self, name: str, ok: bool, **evidence) -> None:
        self.manifest["checks"][name] = {"pass": bool(ok), **evidence}
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        if not ok:
            raise AssertionError(f"{name}: {evidence}")

    def redact(self, text: str) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text

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
            self.secrets.add(path.read_text(encoding="utf-8"))
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
        self.secrets.update(value for key, value in values.items()
                            if any(part in key for part in ("PASSWORD", "SECRET", "ROOT_USER")))
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

    def container_python(self, code: str, env: dict[str, str], *, image: str | None = None) -> str:
        """在栈的网络里用候选（或指定）镜像跑一段 Python，只取它打印的 JSON。口令经 0600 的 --env-file 传入。"""
        env_file = self.private / f"container-{secrets.token_hex(6)}.env"
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(f"{key}={value}\n" for key, value in env.items()))
        try:
            return run(["docker", "run", "--rm", "--network", f"{self.project}_data-internal", "--env-file", str(env_file),
                        "--entrypoint", "python", image or self.manifest["images"]["runtime"]["tag"], "-c", code],
                       timeout=600)
        finally:
            env_file.unlink()

    def owner_env(self, database: str = "tkos_runtime") -> dict[str, str]:
        owner = (f"host=postgres port=5432 dbname={database} user=tkos_runtime_owner "
                 f"password={self.values['POSTGRES_OWNER_PASSWORD']}")
        return {"DATABASE_URL": owner, "MIGRATION_DATABASE_URL": owner}

    # 5 ------------------------------------------------------------------------
    def fresh_checks(self) -> None:
        listing = json.loads(self.container_python(
            "import json,os,psycopg\n"
            "with psycopg.connect(os.environ['DATABASE_URL']) as c:\n"
            "    print(json.dumps(c.execute('SELECT name, sha256 FROM schema_migrations ORDER BY name').fetchall()))",
            self.owner_env()))
        migrations = self.src / "src/memory_service_app/migrations"
        expected = sorted(path.name for path in migrations.glob("*.sql"))
        digests = {path.name: sha256(path) for path in migrations.glob("*.sql")}
        self.manifest["migrations"] = [{"name": name, "sha256": digest} for name, digest in listing]
        self.check("empty_database_is_migrated_to_the_head_with_file_digests",
                   [name for name, _ in listing] == expected and all(digest == digests[name] for name, digest in listing),
                   applied=len(listing), head=expected[-1])
        code = (self.src / "acceptance/delivery_candidate/install_protocols.py").read_text(encoding="utf-8")
        installed = json.loads(self.container_python(code, self.owner_env()))
        self.secrets.update(item["ceo_token"] for item in installed.values())
        steps = {name: [{key: step[key] for key in ("command", "rc", "ok")} for step in item["steps"]]
                 for name, item in installed.items()}
        self.manifest["protocol_installs"] = steps
        self.check("method_0_4_0_5_and_world_0_1_install_from_the_image_materials",
                   all(step["rc"] == 0 and step["ok"] for items in steps.values() for step in items), steps=steps)
        report = exercise_protocols(f"http://127.0.0.1:{self.port}", installed)
        self.manifest["protocol_actions"] = report
        self.check("the_installed_protocols_answer_over_http_on_the_fresh_stack", protocols_work(report), **report)

    # 6 ------------------------------------------------------------------------
    def upgrade(self) -> None:
        released_files = json.loads(run(["docker", "run", "--rm", "--entrypoint", "python", RELEASED, "-c",
            "import hashlib,json,pathlib,memory_service_app\n"
            "d=pathlib.Path(memory_service_app.__file__).parent/'migrations'\n"
            "print(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.glob('*.sql'))}))"]))
        candidate_files = {path.name: sha256(path) for path in (self.src / "src/memory_service_app/migrations").glob("*.sql")}
        differing = sorted(name for name, digest in released_files.items() if candidate_files.get(name) != digest)
        self.check("the_released_v0_4_0_migration_files_are_unchanged_in_the_candidate", not differing,
                   released_files=len(released_files), differing=differing)
        admin = (f"host=postgres port=5432 dbname=tkos_runtime user=tkos_runtime_admin "
                 f"password={self.values['POSTGRES_ADMIN_PASSWORD']}")
        self.container_python("import os,psycopg\n"
                              "with psycopg.connect(os.environ['ADMIN'],autocommit=True) as c:\n"
                              "    c.execute('CREATE DATABASE tkos_upgrade OWNER tkos_runtime_owner')\n"
                              "with psycopg.connect(os.environ['ADMIN'].replace('dbname=tkos_runtime','dbname=tkos_upgrade'),"
                              "autocommit=True) as c:\n"
                              "    c.execute('CREATE EXTENSION IF NOT EXISTS vector')\n", {"ADMIN": admin})
        migrate = ("import json,os\nfrom memory_service_app.migrate import migrate\n"
                   "print(json.dumps(migrate(os.environ['DATABASE_URL'])))")
        released = json.loads(self.container_python(migrate, self.owner_env("tkos_upgrade"), image=RELEASED))
        upgraded = json.loads(self.container_python(migrate, self.owner_env("tkos_upgrade")))
        recorded = dict(json.loads(self.container_python(
            "import json,os,psycopg\nwith psycopg.connect(os.environ['DATABASE_URL']) as c:\n"
            "    print(json.dumps(c.execute('SELECT name, sha256 FROM schema_migrations').fetchall()))",
            self.owner_env("tkos_upgrade"))))
        released_id = json.loads(run(["docker", "image", "inspect", RELEASED]))[0]["Id"]
        self.manifest["upgrade"] = {"released_image": {"tag": RELEASED, "id": released_id},
                                    "applied_by_released": released, "applied_by_candidate": upgraded,
                                    "rows": len(recorded), "rows_with_digest": sum(bool(v) for v in recorded.values())}
        expected_new = [name for name in sorted(candidate_files) if name not in released]
        self.check("the_released_v0_4_0_database_upgrades_to_the_head",
                   released and released[-1].startswith("0028_") and upgraded == expected_new
                   and set(recorded) == set(candidate_files)
                   and all(recorded[name] == released_files[name] for name in released)
                   and all(recorded[name] == candidate_files[name] for name in upgraded),
                   released_head=released[-1] if released else None, applied=upgraded, rows=len(recorded))

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
        """删除临时栈（含卷）、worktree 与候选镜像；每一步的退出码写进清单。"""
        steps = {}
        if getattr(self, "compose", None):
            steps["stack_down"] = subprocess.run([*self.compose, "down", "-v", "--remove-orphans"],
                                                 capture_output=True, text=True, timeout=600).returncode
        steps["worktree_removed"] = subprocess.run(["git", "worktree", "remove", "--force", str(self.src)],
                                                   cwd=ROOT, capture_output=True, text=True).returncode
        if not self.keep_images and self.manifest.get("images"):
            steps["candidate_images_removed"] = subprocess.run(
                ["docker", "image", "rm", *(item["tag"] for item in self.manifest["images"].values())],
                capture_output=True, text=True).returncode
        self.manifest["teardown"] = {name: code == 0 for name, code in steps.items()}

    def execute(self) -> dict:
        try:
            for step in (self.checkout, self.wheelhouse, self.images, self.stack, self.fresh_checks, self.upgrade,
                         self.regression):
                step()
            self.manifest["accepted"] = all(item["pass"] for item in self.manifest["checks"].values())
        except Exception as exc:  # 记下失败步骤，清单照样写出
            self.manifest["accepted"] = False
            self.manifest["error"] = self.redact(f"{type(exc).__name__}: {str(exc)[:1500]}")
        finally:
            self.teardown()
            self.manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            text = self.redact(json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n")
            (self.output / "delivery-manifest.json").write_text(text, encoding="utf-8")
        return self.manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-images", action="store_true", help="运行结束后保留候选镜像")
    args = parser.parse_args()
    manifest = Candidate(args.commit, args.private, args.output, keep_images=args.keep_images).execute()
    print(json.dumps({"accepted": manifest["accepted"], "commit": manifest["commit"],
                      "manifest": str(args.output / "delivery-manifest.json"), "error": manifest.get("error")},
                     ensure_ascii=False))
    raise SystemExit(0 if manifest["accepted"] else 1)


if __name__ == "__main__":
    main()
