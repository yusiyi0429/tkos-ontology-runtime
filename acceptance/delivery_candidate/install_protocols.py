"""交付候选：只用镜像里 /opt/tkos/docs 的材料，经控制面四步把 Method 0.4、0.5 与 world 0.1 各装进一个合成 scope。

在候选镜像里以迁移所有者身份运行（DATABASE_URL 与 MIGRATION_DATABASE_URL 都是 owner）。四步是 install-profile
（核对契约、本体登记或 world 登记的字节）、install-policy（scope 默认协议）、set-registry（支持登记）与
install-activation-policy（在该域现有的激活策略上开放本协议的动作；门动作的角色取 world 登记的门表）。
只打印一行 JSON：每步的退出码与 ok、各 scope 的 id 与域、CEO 的凭证。凭证由调用方取走，不进交付清单。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from memory_service_runtime.governed import bootstrap

DOCS = os.environ.get("TKOS_DOCS", "/opt/tkos/docs")
ROLES = ["AGENT", "CEO", "DOMAIN_DRI", "IC", "MISSION_DRI"]
PROTOCOLS = {
    "method-0.4": {"protocol": "tkos.method", "version": "tkos.method/0.4",
                   "profile": "contracts/method-profile-0.4.json", "registry": "runtime-method-registry-0.4.json",
                   "files": ["--contract-file", "contracts/tkos-method-0.4.md"]},
    "method-0.5": {"protocol": "tkos.method", "version": "tkos.method/0.5",
                   "profile": "contracts/method-profile-0.5.json", "registry": "runtime-method-registry-0.5.json",
                   "files": ["--contract-file", "contracts/tkos-method-0.5.md",
                             "--ontology-registry-file", "contracts/ontology-registry-0.7.json"]},
    "world-0.1": {"protocol": "tkos.world", "version": "tkos.world/0.1",
                  "profile": "contracts/world-profile-0.1.json", "registry": "runtime-world-support-0.1.json",
                  "files": ["--contract-file", "contracts/tkos-world-0.1.md",
                            "--world-registry-file", "contracts/world-registry-0.1.json"]},
}


def docs(path: str) -> str:
    return os.path.join(DOCS, path)


def load(path: str):
    with open(docs(path), encoding="utf-8") as handle:
        return json.load(handle)


def control(*args: str) -> dict:
    done = subprocess.run(["tkos-governed-control", *args, "--reason", "delivery candidate protocol check"],
                          capture_output=True, text=True)
    ok = done.returncode == 0 and json.loads(done.stdout).get("ok") is True
    return {"command": args[0], "rc": done.returncode, "ok": ok}


def activation_roles(name: str, spec: dict) -> dict[str, list[str]]:
    """本协议的全部动作；world 的门动作只给登记的门角色，其余交给服务代码的责任人规则。"""
    actions = load(spec["registry"])["actions"]
    gates = {}
    if name == "world-0.1":
        gates = {item["action"]: item["gate_roles"] for item in load("contracts/world-registry-0.1.json")["actions"]
                 if item["gate_roles"] is not None}
    return {action: gates.get(action, ROLES) for action in actions}


def current_policy(scope_id: str, domain_id: str) -> dict:
    with psycopg.connect(os.environ["MIGRATION_DATABASE_URL"], row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('app.runtime_write_capability','tkos-runtime-a1',true)")
        conn.execute("SELECT set_config('app.gov_control_plane','on',true)")
        conn.execute("SELECT set_config('app.governed_scope_id',%s,true)", (scope_id,))
        return conn.execute("SELECT content FROM gov_activation_policies WHERE scope_id=%s AND domain_id=%s"
                            " ORDER BY policy_seq DESC LIMIT 1", (scope_id, domain_id)).fetchone()["content"]


def install(name: str, spec: dict, work: str) -> dict:
    label = uuid4().hex[:12]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        seeded = bootstrap.seed_scope(conn, f"runtime-acceptance-candidate-{label}",
                                      f"runtime-acceptance-candidate-company-{label}")
    scope, domain = seeded["scope_id"], seeded["domain_id"]
    profile = load(spec["profile"])
    files = [docs(item) if index % 2 else item for index, item in enumerate(spec["files"])]
    policy = {"default_protocol": spec["protocol"], "default_contract_version": spec["version"],
              "allow_legacy_create": False, "record_origin": "synthetic",
              "default_profile_ref": {"profile_id": profile["profile_id"], "revision": profile["revision"]},
              "experimental": True, "notes": "Delivery candidate check; synthetic records only"}
    current = current_policy(scope, domain)
    activation = {**current, "action_roles": {**current["action_roles"], **activation_roles(name, spec)}}
    paths = {}
    for key, value in (("policy", policy), ("activation", activation)):
        paths[key] = os.path.join(work, f"{name}-{key}.json")
        with open(paths[key], "w", encoding="utf-8") as handle:
            json.dump(value, handle)
    steps = [
        control("install-profile", "--scope-id", scope, "--profile-json", docs(spec["profile"]), *files),
        control("install-policy", "--scope-id", scope, "--content-json", paths["policy"]),
        control("set-registry", "--scope-id", scope, "--protocol-id", spec["protocol"],
                "--contract-version", spec["version"], "--content-json", docs(spec["registry"])),
        control("install-activation-policy", "--scope-id", scope, "--domain-id", domain,
                "--content-json", paths["activation"]),
    ]
    return {"scope_id": scope, "domain_id": domain, "outcome_id": seeded["outcome"]["object_id"], "steps": steps,
            "ceo_token": seeded["actors"]["ceo"]["token"]}


def main() -> None:
    with tempfile.TemporaryDirectory() as work:
        print(json.dumps({name: install(name, spec, work) for name, spec in PROTOCOLS.items()}))


if __name__ == "__main__":
    main()
